"""Q-Lou, the genie in the jukebox. The agent.

Given two partners (the cards each played plus a short quiz profile), Q-Lou finds the one
artist both of them rank near the top, says why in plain words, and brings back the tracks.

The loop, every step logged so the phones can watch it think:
  1. read     - turn each partner's plays and profile into a Qloo signal (tags + audiences)
  2. pool     - candidate artists: each partner's own top list plus the combined signal
  3. score    - every candidate scored for partner A, then for partner B
                (filter.results.entities; 0.765 is Qloo's no-signal filler, never a score)
  4. curve    - min-max within each partner; raw affinities sit near 1.0 and some signals skew
  5. pick     - the artist whose WEAKER score is highest: common ground, not a compromise
  6. explain  - ask Qloo for the signal breakdown behind the pick (feature.explainability)
  7. baseline - ask a plain LLM the same question cold, then score its guess with Qloo
  8. why      - one short line for the two of them, written by the LLM from Qloo facts only,
                checked: it must name the pick and nothing Qloo did not return
  9. tracks   - the artist's top tracks from the catalog
Steps 2 and 3 run their calls in parallel. Steps 6 to 9 run together under one deadline:
the song is never kept waiting on an extra.

Everything Q-Lou says is backed by a Qloo entity id or it is not said.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from . import catalog, deck
from .llm import BudgetExceeded, chat_json
from .qloo import Qloo, affinity, slim

NO_SIGNAL = 0.765
ARTIST = "urn:entity:artist"
POOL_EACH = 25
POOL_BOTH = 40
POPULARITY_MIN = 0.9
SHORTLIST = 8
EXTRAS_DEADLINE = 25.0   # seconds to wait for explainability, the control and the line, together

WHY_SYSTEM = """You are Q-Lou, the genie in a bar jukebox. Two strangers just played taste cards on their phones and you picked the one artist both of them rank highest.
Write what you say to them: two short sentences, under 45 words total, warm, a little playful, plain words, no em-dashes, no bullet points, no hashtags.
Use ONLY the facts given. Name the artist exactly once, by the exact name given. Do not name any other artist, song, or album. Do not invent facts.
Reply as JSON: {"line": "..."}"""

GUESS_SYSTEM = """Answer from general knowledge only, in one word or a short name. Reply as JSON: {"artist": "..."}"""


@dataclass
class Partner:
    key: str                      # "host" | "guest"
    name: str
    cards: list[int]              # card ids played, all rounds
    profile: list[str] = field(default_factory=list)   # quiz audiences

    def signal(self) -> dict[str, list[str]]:
        return deck.signal(self.cards, self.profile)

    def card_names(self) -> list[str]:
        return [deck.card(c)["name"] for c in self.cards]


@dataclass
class Pick:
    artist: dict                                  # slim Qloo entity
    scores: dict[str, float]                      # partner key -> curved score 0..1
    ranked: list[dict]                            # shortlist: [{name, id, <key>: score...}]
    tracks: list[dict]                            # catalog top tracks
    why: str
    why_source: str                               # "llm" | "facts"
    explain: dict[str, list[dict]]                # partner key -> [{label, score}]
    baseline: dict | None                         # {"guess", "id", "scores", "verdict"}
    steps: list[dict]
    pool: int
    calls: int
    seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


def _curve(d: dict[str, float]) -> dict[str, float]:
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in d}
    return {k: round((v - lo) / (hi - lo), 3) for k, v in d.items()}


def _flatten_explain(x: Any, out: list[dict], depth: int = 0) -> None:
    """Qloo's explainability payload is nested; collect anything that looks like (label, score)."""
    if depth > 6 or len(out) > 40:
        return
    if isinstance(x, dict):
        label = x.get("name") or x.get("tag_id") or x.get("entity_id") or x.get("id")
        score = next((x[k] for k in ("score", "affinity", "weight", "value") if isinstance(x.get(k), (int, float))), None)
        if label and score is not None:
            out.append({"label": str(label).split(":")[-1].replace("_", " "), "score": round(float(score), 3)})
        for k, v in x.items():
            if isinstance(v, (dict, list)):
                _flatten_explain(v, out, depth + 1)
            elif isinstance(v, (int, float)) and isinstance(k, str) and ":" in k:
                out.append({"label": k.split(":")[-1].replace("_", " "), "score": round(float(v), 3)})
    elif isinstance(x, list):
        for v in x:
            _flatten_explain(v, out, depth + 1)


class QLou:
    def __init__(self, q: Qloo, *, use_llm: bool = True, on_step: Callable[[dict], None] | None = None) -> None:
        self.q = q
        self.use_llm = use_llm
        self.on_step = on_step
        self.steps: list[dict] = []
        self._t0 = time.time()

    def step(self, name: str, text: str, **data: Any) -> None:
        s = {"step": name, "text": text, "t": round(time.time() - self._t0, 1), **data}
        self.steps.append(s)
        if self.on_step:
            self.on_step(s)

    # ---- the loop -------------------------------------------------------------------
    def pick(self, a: Partner, b: Partner, exclude: set[str] | None = None) -> Pick | None:
        exclude = exclude or set()
        self._t0 = time.time()
        sa, sb = a.signal(), b.signal()
        self.step("read", f"{a.name} played {', '.join(a.card_names()) or 'nothing yet'}. {b.name} played {', '.join(b.card_names()) or 'nothing yet'}.",
                  signals={a.key: sa, b.key: sb})

        both = {"tags": sa.get("tags", []) + sb.get("tags", []), "audiences": sa.get("audiences", []) + sb.get("audiences", [])}
        both = {k: list(dict.fromkeys(v)) for k, v in both.items() if v}
        with ThreadPoolExecutor(max_workers=6) as ex:
            # 2. pool: three calls at once
            pools = list(ex.map(lambda arg: self._insights(arg[0], take=arg[1]), [(sa, POOL_EACH), (sb, POOL_EACH), (both, POOL_BOTH)]))
            pool: dict[str, dict] = {}
            for ents in pools:
                for e in ents:
                    if e.get("entity_id") and e["entity_id"] not in exclude:
                        pool.setdefault(e["entity_id"], e)
            ids = list(pool)
            self.step("pool", f"Pulled {len(ids)} artists from Qloo: yours, theirs, and the two of you together.", pool=len(ids))
            if not ids:
                return None

            # 3. score: every batch for both partners, all at once
            fa, fb = ex.submit(self._score, ids, sa), ex.submit(self._score, ids, sb)
            ra = fa.result()
            self.step("score", f"Scored every one of them for {a.name}.", calls=self.q.calls)
            rb = fb.result()
            self.step("score", f"Scored every one of them for {b.name}.", calls=self.q.calls)
            keep = [i for i in ids if i in ra and i in rb and abs(ra[i] - NO_SIGNAL) > 1e-6 and abs(rb[i] - NO_SIGNAL) > 1e-6]
            if not keep:
                self.step("curve", "Qloo had no signal for either of you on anything in the pool.")
                return None
            ca, cb = _curve({i: ra[i] for i in keep}), _curve({i: rb[i] for i in keep})
            ranked = sorted(keep, key=lambda i: (-min(ca[i], cb[i]), abs(ca[i] - cb[i])))
            top = ranked[0]
            artist = slim(pool[top])
            self.step("curve", f"Graded on a curve for each of you. {len(keep)} artists had a real signal for both.")
            self.step("pick", f"{artist['name']}. {a.name} {int(ca[top] * 100)}, {b.name} {int(cb[top] * 100)}. The weaker score is the one that counts.",
                      artist=artist["name"], id=top)
            shortlist = [{"id": i, "name": slim(pool[i])["name"], a.key: ca[i], b.key: cb[i]} for i in ranked[:SHORTLIST]]

            # 6 to 9: the extras, together, with a deadline. The pick never waits on a slow extra.
            f_tracks = ex.submit(catalog.top_tracks, artist["name"])
            f_why = ex.submit(self._why, a, b, artist, ca[top], cb[top], shortlist)
            f_base = ex.submit(self._baseline, a, b, ra, rb, keep) if self.use_llm else None
            f_expl = ex.submit(self._explain, top, {a.key: sa, b.key: sb})
            futures = [f for f in (f_tracks, f_why, f_base, f_expl) if f]
            done, pending = wait(futures, timeout=EXTRAS_DEADLINE)
            for f in pending:
                f.cancel()
            tracks = f_tracks.result() if f_tracks in done else []
            why, src = f_why.result() if f_why in done else self._facts_line(a, b, artist)
            baseline = f_base.result() if (f_base and f_base in done) else None
            explain = f_expl.result() if f_expl in done else {}
            if pending:
                self.step("deadline", f"{len(pending)} slow extra{'s' if len(pending) > 1 else ''} left behind so the song is not kept waiting.")
            self.step("tracks", f"{len(tracks)} tracks by {artist['name']} in the catalog." if tracks else f"No preview tracks for {artist['name']} in the demo catalog.")
        return Pick(artist=artist, scores={a.key: ca[top], b.key: cb[top]}, ranked=shortlist, tracks=tracks, why=why, why_source=src,
                    explain=explain, baseline=baseline, steps=self.steps, pool=len(ids), calls=self.q.calls,
                    seconds=round(time.time() - self._t0, 1))

    # ---- helpers -------------------------------------------------------------------------
    def _insights(self, sig: dict, **kw: Any) -> list[dict]:
        try:
            return self.q.insights(ARTIST, popularity_min=POPULARITY_MIN, **sig, **kw)
        except Exception as e:
            self.step("error", f"Qloo call failed: {e}")
            return []

    def _score(self, ids: list[str], sig: dict) -> dict[str, float]:
        """Affinity of every id for one signal, in batches of 50, batches in parallel."""
        batches = [ids[i:i + 50] for i in range(0, len(ids), 50)]

        def one(batch: list[str]) -> dict[str, float]:
            try:
                return {e["entity_id"]: affinity(e) for e in self.q.insights(ARTIST, result_entities=batch, take=len(batch), **sig)}
            except Exception as e:
                self.step("error", f"scoring batch failed: {e}")
                return {}
        out: dict[str, float] = {}
        if len(batches) == 1:
            out.update(one(batches[0]))
        else:
            with ThreadPoolExecutor(max_workers=len(batches)) as ex:
                for part in ex.map(one, batches):
                    out.update(part)
        return out

    def _explain(self, entity_id: str, sigs: dict[str, dict]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for key, sig in sigs.items():
            try:
                ents = self.q.insights(ARTIST, result_entities=[entity_id], take=1, explain=True, **sig)
                raw = (ents[0].get("query") or {}).get("explainability") if ents else None
                found: list[dict] = []
                _flatten_explain(raw, found)
                found.sort(key=lambda d: -d["score"])
                out[key] = found[:5]
            except Exception as e:
                self.step("error", f"explainability failed: {e}")
                out[key] = []
        n = sum(len(v) for v in out.values())
        self.step("explain", f"Asked Qloo which signals drove the pick: {n} drivers." if n else "Asked Qloo for the signal breakdown; nothing extra came back.")
        return out

    def _baseline(self, a: Partner, b: Partner, ra: dict, rb: dict, keep: list[str]) -> dict | None:
        """The control: a plain LLM's cold guess, scored by Qloo against both partners."""
        try:
            j = chat_json(GUESS_SYSTEM, f"{a.name} likes: {', '.join(a.card_names())}. {b.name} likes: {', '.join(b.card_names())}. "
                          f"Name ONE musical artist both would enjoy.", temperature=0.3, max_tokens=200)
            guess = str(j.get("artist", "")).strip()
        except BudgetExceeded:
            self.step("baseline", "Skipped the plain-LLM control: daily LLM cap reached.")
            return None
        except Exception as e:
            self.step("baseline", f"Plain-LLM control failed: {e}")
            return None
        if not guess:
            return None
        res = self.q.search(guess, types=ARTIST, take=1)
        if not res:
            self.step("baseline", f"A plain LLM guessed {guess}. Qloo could not find them.")
            return {"guess": guess, "id": None, "scores": {}, "verdict": "unknown"}
        gid, gname = res[0].get("entity_id"), res[0].get("name", guess)
        sa, sb = a.signal(), b.signal()
        ga = self._score([gid], sa).get(gid)
        gb = self._score([gid], sb).get(gid)
        lo_a, hi_a = min(ra[i] for i in keep), max(ra[i] for i in keep)
        lo_b, hi_b = min(rb[i] for i in keep), max(rb[i] for i in keep)

        def place(v: float | None, lo: float, hi: float) -> float | None:
            if v is None or abs(v - NO_SIGNAL) < 1e-6:
                return None
            return round(max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi - lo > 1e-9 else 1.0, 3)

        pa, pb = place(ga, lo_a, hi_a), place(gb, lo_b, hi_b)
        if pa is None or pb is None:
            verdict = "no data"
        elif min(pa, pb) >= 0.6:
            verdict = "holds"
        else:
            verdict = "splits"
        self.step("baseline", f"A plain LLM guessed {gname}. Qloo scores that {a.name} {int((pa or 0) * 100)}, {b.name} {int((pb or 0) * 100)}: it {verdict}.")
        return {"guess": gname, "id": gid, "scores": {a.key: pa, b.key: pb}, "verdict": verdict}

    @staticmethod
    def _facts_line(a: Partner, b: Partner, artist: dict) -> tuple[str, str]:
        tags = [t for t in artist.get("tags", []) if t][:3]
        line = (f"{a.name} played {', '.join(a.card_names()[:2])}. {b.name} played {', '.join(b.card_names()[:2])}. "
                + (f"Qloo files {artist['name']} under {', '.join(tags)}. " if tags else "")
                + "Both of you rank them near the top. That is the common ground.")
        return line, "facts"

    def _why(self, a: Partner, b: Partner, artist: dict, sa: float, sb: float, shortlist: list[dict]) -> tuple[str, str]:
        tags = [t for t in artist.get("tags", []) if t][:3]
        facts = (f"{a.name} played: {', '.join(a.card_names())}. {b.name} played: {', '.join(b.card_names())}. "
                 f"Pick: {artist['name']}. Qloo tags for the pick: {', '.join(tags) or 'none'}. "
                 f"Scores out of 100: {a.name} {int(sa * 100)}, {b.name} {int(sb * 100)}.")
        fallback, _ = self._facts_line(a, b, artist)
        if not self.use_llm:
            return fallback, "facts"
        try:
            j = chat_json(WHY_SYSTEM, facts, temperature=0.7, max_tokens=300)
            line = " ".join(str(j.get("line", "")).split())
        except BudgetExceeded:
            self.step("why", "Wrote the line from the facts: daily LLM cap reached.")
            return fallback, "facts"
        except Exception as e:
            self.step("why", f"LLM line failed ({e}); using the facts.")
            return fallback, "facts"
        others = [s["name"] for s in shortlist if s["name"] != artist["name"]]
        ok = bool(line) and artist["name"].lower() in line.lower() and len(line) < 400 \
            and not any(o.lower() in line.lower() for o in others) and "—" not in line
        if not ok:
            self.step("why", "LLM line failed the citation check; using the facts.")
            return fallback, "facts"
        self.step("why", "Q-Lou has something to say.")
        return line, "llm"
