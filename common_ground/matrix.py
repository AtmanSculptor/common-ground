"""The card matrix: every card's Qloo affinity to a shared pool of artists, computed once.

Why: Qloo's live scoring calls can take 20 to 40 seconds each on a busy afternoon, and a bar
cannot wait a minute for a song. With the matrix, Q-Lou's draft pick is local arithmetic
over numbers Qloo already gave us, and the live calls only verify the top candidates.

Build (once): the pool is the union of two lists, every card's own top artists from Qloo and
a seed list of what bars actually play (static/jukebox_seeds.json, resolved to Qloo entities
by name; an artist Qloo has never met is dropped and counted, never guessed at). The pool is
capped by popularity, then every pool artist is scored against every card with
filter.results.entities, a few cards at a time. 0.765 is Qloo's no-signal filler and is
stored as missing. Checkpointed per card; resumable.

Draft (per game, no calls): a partner's affinity to an artist is the weighted mean of the
artist's scores for the cards that partner played (3, 2, 1 for "Most me", "Also me", "A bit me")
minus a share of the cards they tossed ("not me"); min-max curve per partner; rank by the weaker
curved score, exactly as the live loop does.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import deck
from .qloo import Qloo, affinity, slim

STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"
FILE = STATIC / "matrix.json"
SEEDS = STATIC / "jukebox_seeds.json"
ARTIST = "urn:entity:artist"
NO_SIGNAL = 0.765
POOL_PER_CARD = 100     # each card's own top artists, pulled in pages
POOL_CAP = 2000         # the pool, most popular first
POPULARITY_MIN = 0.9    # Qloo popularity is a percentile: the top tenth of artists
PAGE = 50               # Qloo's page size for the pool pull
BATCH = 50              # artists per scoring call
SEARCH_TAKE = 20        # search hits to look through for an outside name (collaborations crowd the top)
WORKERS = 4             # Qloo calls in flight at once while building
RETRIES = 4             # tries per scoring batch before it is written off as a hole
RETRY_PAUSE = 5.0       # seconds before the second try; doubles each time
MIN_COVERAGE = 0.5      # an artist needs scores for at least half a partner's cards to be ranked

_lock = threading.Lock()
_cache: dict | None = None
_cache_mtime: float | None = None


def load(path: pathlib.Path | None = None) -> dict | None:
    """The matrix, or None when it has not been built. Reloads when the file changes."""
    global _cache, _cache_mtime
    path = path or FILE
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    with _lock:
        if _cache is None or mtime != _cache_mtime:
            _cache = json.loads(path.read_text(encoding="utf-8"))
            _cache_mtime = mtime
        return _cache


def available(path: pathlib.Path | None = None) -> bool:
    m = load(path)
    return bool(m and m.get("scores") and m.get("entities"))


def _curve(d: dict[str, float]) -> dict[str, float]:
    lo, hi = min(d.values()), max(d.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in d}
    return {k: round((v - lo) / (hi - lo), 3) for k, v in d.items()}


def _standard(col: dict[str, float]) -> dict[str, float]:
    """One card's column on a common scale: how far each artist sits from that card's own mean, in
    that card's own standard deviations. Raw affinities are not comparable across cards: a life card
    (Dog person) spreads its artists over 0.07 to 0.09 while a genre card (Jazz hands) spreads over
    0.01 to 0.02, so without this a rank-3 life card outweighs a rank-1 genre card. A column that
    does not vary says nothing and scores everyone zero."""
    n = len(col)
    if n < 2:
        return {k: 0.0 for k in col}
    mean = sum(col.values()) / n
    var = sum((v - mean) ** 2 for v in col.values()) / n
    if var < 1e-12:
        return {k: 0.0 for k in col}
    sd = var ** 0.5
    return {k: (v - mean) / sd for k, v in col.items()}


RANK_WEIGHTS = (3.0, 2.0, 1.0)   # "Most me", "Also me", "A bit me"
TOSS_WEIGHT = 0.5                # a tossed card is "not me": it pulls against, at half the pull of a played card


def partner_scores(m: dict, cards: list[int], weights: list[float] | None = None,
                   tossed: list[int] | tuple[int, ...] = ()) -> dict[str, float]:
    """Weighted mean affinity per artist over the cards played, minus a share of the cards tossed,
    for artists covered by enough of the played cards.

    Every card's column is standardized first (see _standard), so each card pulls by its rank, not
    by how wide Qloo's numbers happen to run for it. A card Qloo had no signal on for this artist
    counts as that card's weakest real score: silence is a penalty, not a free pass. An artist still needs real scores on half the played cards to be
    ranked. Tossed cards never help coverage; they only pull an artist down when a tossed card
    liked them."""
    weights = list(weights) if weights else [1.0] * len(cards)
    cols = [(_standard(col), w) for col, w in ((m["scores"].get(str(c), {}), w) for c, w in zip(cards, weights)) if col and w > 0]
    if not cols:
        return {}
    neg = [_standard(col) for col in (m["scores"].get(str(c), {}) for c in tossed) if col]
    floors = [min(col.values()) for col, _ in cols]
    nfloors = [min(col.values()) for col in neg]
    need = max(1, int(len(cols) * MIN_COVERAGE + 0.999))
    den = sum(w for _, w in cols) + TOSS_WEIGHT * len(neg)
    out: dict[str, float] = {}
    for eid in m["entities"]:
        hits = sum(1 for col, _ in cols if eid in col)
        if hits >= need:
            num = sum(w * col.get(eid, floor) for (col, w), floor in zip(cols, floors))
            num -= TOSS_WEIGHT * sum(col.get(eid, floor) for col, floor in zip(neg, nfloors))
            out[eid] = num / den
    return out


def top_for(m: dict, cards: list[int], n: int = 3, weights: list[float] | None = None,
            tossed: list[int] | tuple[int, ...] = ()) -> list[str]:
    """One partner's own top artists from the matrix, by weighted affinity over their cards."""
    scores = partner_scores(m, cards, weights, tossed)
    return sorted(scores, key=lambda e: -scores[e])[:n]


def draft(m: dict, a_cards: list[int], b_cards: list[int], exclude: set[str] | None = None, top: int = 8, *,
          a_weights: list[float] | None = None, b_weights: list[float] | None = None,
          a_tossed: list[int] | tuple[int, ...] = (), b_tossed: list[int] | tuple[int, ...] = ()) -> list[dict]:
    """Ranked candidates [{id, name, a, b}] from the matrix alone. Empty if either side has no cards."""
    exclude = exclude or set()
    ra, rb = partner_scores(m, a_cards, a_weights, a_tossed), partner_scores(m, b_cards, b_weights, b_tossed)
    keep = [e for e in ra if e in rb and e not in exclude]
    if not keep:
        return []
    ca, cb = _curve({e: ra[e] for e in keep}), _curve({e: rb[e] for e in keep})
    ranked = sorted(keep, key=lambda e: rank_key(min(ca[e], cb[e]), abs(ca[e] - cb[e]), m["entities"][e].get("popularity")))
    return [{"id": e, "name": m["entities"][e]["name"], "a": ca[e], "b": cb[e], "entity": m["entities"][e]} for e in ranked[:top]]


NEAR_TIE = 0.03          # curved scores this close are the same answer for this pair


def rank_key(weaker: float, gap: float, popularity: float | None) -> tuple:
    """The weaker partner's curved score decides. Scores within a few points of each other are
    one band: Qloo cannot really tell those artists apart for this pair, so the one more people
    know wins (a jukebox is for a room; a 98 for an artist nobody at the bar has heard of is not
    a better night than a 97 for one they all know), then the smaller gap between the partners."""
    return (-round(weaker / NEAR_TIE), -(popularity or 0), gap)


# ---- building ------------------------------------------------------------------------------
def _seed_file(path: pathlib.Path | None = None) -> dict:
    try:
        return json.loads((path or SEEDS).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def seed_names(path: pathlib.Path | None = None) -> list[str]:
    """The outside list: artists bars actually play. Empty when the file is missing."""
    seen: dict[str, str] = {}
    for a in _seed_file(path).get("artists", []):
        name = (a.get("name") if isinstance(a, dict) else a) or ""
        name = " ".join(name.split())
        if name and name.lower() not in seen:
            seen[name.lower()] = name
    return list(seen.values())


def seed_aliases(path: pathlib.Path | None = None) -> dict[str, str]:
    """Outside name -> the spelling Qloo uses, for the few the resolver cannot be trusted to guess
    (an entry's "qloo" field: {"name": "Giveon", "qloo": "GIVĒON"})."""
    return {a["name"]: a["qloo"] for a in _seed_file(path).get("artists", []) if isinstance(a, dict) and a.get("qloo") and a.get("name")}


def _pull_card_pool(q: Qloo, card: dict, per_card: int, popularity_min: float) -> list[dict]:
    """A card's own top artists, page by page. Stops when a page repeats or comes back short."""
    out: dict[str, dict] = {}
    for page in range(1, (per_card + PAGE - 1) // PAGE + 1):
        ents = q.insights(ARTIST, take=PAGE, page=page, popularity_min=popularity_min, **card["signal"])
        new = [e for e in ents if e.get("entity_id") and e["entity_id"] not in out]
        for e in new:
            out[e["entity_id"]] = slim(e) | {"id": e["entity_id"]}
        if not new or len(ents) < PAGE:
            break
    return list(out.values())


def _norm(name: str) -> str:
    """'Lil\' Wayne', 'Dan + Shay', 'Carin León' -> 'lil wayne', 'dan shay', 'carin leon'."""
    flat = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", flat.lower()).split())


def _resolve_seed(q: Qloo, name: str, aliases: dict[str, str] | None = None) -> dict | None:
    """One outside name -> the Qloo artist entity with that exact name (spelling aside), the best
    known one if several, or None when Qloo has never met them. Collaborations never stand in."""
    aliases = seed_aliases() if aliases is None else aliases
    name = aliases.get(name, name)
    want = _norm(name)
    hits = q.search(name, types=ARTIST, take=SEARCH_TAKE)
    if len(want.split()) == 1:
        # a one-word name keeps its accents: ROSÉ is not Rose, Beyoncé is not Beyonce's cover band
        hits = [r for r in hits if r.get("entity_id") and r.get("name", "").strip().lower() == name.strip().lower()]
    else:
        hits = [r for r in hits if r.get("entity_id") and _norm(r.get("name", "")) == want]
    if not hits:
        return None
    best = max(hits, key=lambda r: float(r.get("popularity") or 0))
    return slim(best) | {"id": best["entity_id"]}


def build(q: Qloo, *, out: pathlib.Path | None = None, checkpoint: pathlib.Path | None = None,
          on_progress: Callable[[dict], None] | None = None, cards: list[dict] | None = None,
          seeds: list[str] | None = None, per_card: int = POOL_PER_CARD, cap: int = POOL_CAP,
          popularity_min: float = POPULARITY_MIN, workers: int = WORKERS) -> dict:
    """Compute the matrix. Resumable: a checkpoint file holds finished stages and cards."""
    out = out or FILE
    cards = cards or list(deck.cards())
    seeds = seed_names() if seeds is None else list(dict.fromkeys(" ".join(n.split()) for n in seeds if n.strip()))
    checkpoint = checkpoint or out.with_suffix(".partial.json")
    state: dict[str, Any] = {"pool": {}, "seeds": {}, "scores": {}, "started": time.time()}
    try:
        if checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            state.update({k: saved.get(k, v) for k, v in state.items()})
    except Exception:
        pass
    save_lock = threading.Lock()

    def progress(stage: str, **data: Any) -> None:
        if on_progress:
            on_progress({"stage": stage, "t": round(time.time() - state["started"], 1), "calls": q.calls, **data})

    def commit(section: str, key: str, value: Any) -> None:
        """One result into the state and onto disk, under one lock: threads never race the dump."""
        with save_lock:
            state[section][key] = value
            try:
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                checkpoint.write_text(json.dumps(state), encoding="utf-8")
            except Exception:
                pass

    # 1a. the pool: each card's own top artists, several cards at once
    todo = [c for c in cards if str(c["id"]) not in state["pool"]]

    def pull(c: dict) -> None:
        try:
            ents = _pull_card_pool(q, c, per_card, popularity_min)
        except Exception as e:
            progress("pool-error", card=c["name"], error=str(e))
            ents = []
        commit("pool", str(c["id"]), ents)
        progress("pool", card=c["name"], found=len(ents))

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(pull, todo))

    # 1b. the outside list, resolved by name; what Qloo has never met is dropped, never guessed at
    wanted = [n for n in seeds if n not in state["seeds"]]
    aliases = seed_aliases()

    def resolve(name: str) -> None:
        try:
            found = _resolve_seed(q, name, aliases)
        except Exception as e:
            progress("seed-error", name=name, error=str(e))
            found = None
        commit("seeds", name, found)

    if wanted:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(resolve, wanted))
    dropped = sorted(n for n, e in state["seeds"].items() if e is None)
    progress("seeds", wanted=len(seeds), found=len(seeds) - len(dropped), dropped=len(dropped))

    entities: dict[str, dict] = {}
    for ents in state["pool"].values():
        for e in ents:
            entities.setdefault(e["id"], e)
    for e in state["seeds"].values():
        if e and (e.get("popularity") or 0) >= popularity_min:
            entities.setdefault(e["id"], e)
    ids = sorted(entities, key=lambda i: -(entities[i].get("popularity") or 0))[:cap]
    progress("pool-done", artists=len(ids), union=len(entities))

    # 2. every artist against every card, several calls in flight. A batch that fails (a rate
    # limit, a slow minute) is retried with a growing pause rather than left as a hole.
    holes: list[str] = []

    def score_batch(c: dict, batch: list[str]) -> list[dict]:
        for attempt in range(RETRIES):
            try:
                return q.insights(ARTIST, result_entities=batch, take=len(batch), **c["signal"])
            except Exception as e:
                progress("score-error", card=c["name"], attempt=attempt + 1, error=str(e)[:160])
                time.sleep(RETRY_PAUSE * (2 ** attempt))
        holes.append(c["name"])
        return []

    def score_card(c: dict) -> None:
        key = str(c["id"])
        col: dict[str, float] = {}
        for i in range(0, len(ids), BATCH):
            for e in score_batch(c, ids[i:i + BATCH]):
                v = affinity(e)
                if abs(v - NO_SIGNAL) > 1e-6:
                    col[e["entity_id"]] = round(v, 4)
        commit("scores", key, col)
        progress("score", card=c["name"], scored=len(col), done=len(state["scores"]), of=len(cards))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(score_card, [c for c in cards if str(c["id"]) not in state["scores"]]))

    m = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cards": len(cards), "calls": q.calls,
         "seeds": {"wanted": len(seeds), "found": len(seeds) - len(dropped), "dropped": dropped},
         "holes": sorted(set(holes)),      # cards with a batch that never scored; empty on a clean build
         "entities": {i: {k: entities[i].get(k) for k in ("name", "popularity", "tags")} for i in ids},
         "scores": {str(c["id"]): state["scores"].get(str(c["id"]), {}) for c in cards}}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, out)          # the old matrix serves games until this instant
    try:
        checkpoint.unlink()
    except Exception:
        pass
    progress("done", artists=len(ids), seconds=round(time.time() - state["started"], 1))
    return m


def missing_cards(m: dict | None, cards: list[dict] | None = None) -> list[dict]:
    """Cards in the deck that the matrix has no column for yet (new art landed)."""
    cards = cards or list(deck.cards())
    have = set((m or {}).get("scores", {}))
    return [c for c in cards if str(c["id"]) not in have and c.get("signal")]     # the joker has nothing to score


def add_cards(q: Qloo, cards: list[dict], *, path: pathlib.Path | None = None,
              on_progress: Callable[[dict], None] | None = None, workers: int = WORKERS) -> dict:
    """Score new cards against the pool as it stands and write them into the matrix. The pool
    itself does not change; the monthly rebuild folds in each new card's own top artists."""
    path = path or FILE
    m = load(path)
    if not m:
        raise RuntimeError("no matrix to add cards to")
    ids = list(m["entities"])
    t0 = time.time()

    def progress(stage: str, **data: Any) -> None:
        if on_progress:
            on_progress({"stage": stage, "t": round(time.time() - t0, 1), "calls": q.calls, **data})

    def score_card(c: dict) -> tuple[str, dict[str, float]]:
        col: dict[str, float] = {}
        for i in range(0, len(ids), BATCH):
            batch = ids[i:i + BATCH]
            for attempt in range(RETRIES):
                try:
                    ents = q.insights(ARTIST, result_entities=batch, take=len(batch), **c["signal"])
                    break
                except Exception as e:
                    progress("score-error", card=c["name"], attempt=attempt + 1, error=str(e)[:160])
                    time.sleep(RETRY_PAUSE * (2 ** attempt))
            else:
                ents = []
            for e in ents:
                v = affinity(e)
                if abs(v - NO_SIGNAL) > 1e-6:
                    col[e["entity_id"]] = round(v, 4)
        progress("score", card=c["name"], scored=len(col))
        return str(c["id"]), col

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(score_card, cards))
    m = json.loads(path.read_text(encoding="utf-8"))     # fresh from disk: never mutate the cached copy games are reading
    for key, col in results:
        m["scores"][key] = col
    m["cards"] = len(m["scores"])
    m["added_cards"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)          # the old matrix serves games until this instant; load() sees the new mtime
    progress("done", cards=[c["name"] for c in cards], seconds=round(time.time() - t0, 1))
    return {"cards": [c["name"] for c in cards], "calls": q.calls, "scored": {k: len(v) for k, v in results}}


def extend(q: Qloo, names: list[str], *, path: pathlib.Path | None = None, popularity_min: float = POPULARITY_MIN,
           on_progress: Callable[[dict], None] | None = None) -> dict:
    """Add outside names to the built matrix: resolve them, score the new ones against every card
    (one call per card), merge, write. The way new artists join after launch."""
    path = path or FILE
    m = load(path)
    if not m:
        raise RuntimeError("no matrix to extend")
    names = list(dict.fromkeys(" ".join(n.split()) for n in names if n.strip()))
    found: dict[str, dict] = {}
    dropped: list[str] = []
    aliases = seed_aliases()
    for n in names:
        e = _resolve_seed(q, n, aliases)
        if e and (e.get("popularity") or 0) >= popularity_min:
            found[n] = e
        else:
            dropped.append(n)
    new = {e["id"]: e for e in found.values() if e["id"] not in m["entities"]}
    if on_progress:
        on_progress({"stage": "resolve", "wanted": len(names), "found": len(found), "new": len(new), "dropped": dropped})
    ids = list(new)
    cards = list(deck.cards())
    for c in cards:
        col = dict(m["scores"].get(str(c["id"]), {}))
        for i in range(0, len(ids), BATCH):
            try:
                for e in q.insights(ARTIST, result_entities=ids[i:i + BATCH], take=len(ids[i:i + BATCH]), **c["signal"]):
                    v = affinity(e)
                    if abs(v - NO_SIGNAL) > 1e-6:
                        col[e["entity_id"]] = round(v, 4)
            except Exception as e:
                if on_progress:
                    on_progress({"stage": "score-error", "card": c["name"], "error": str(e)[:160]})
        m["scores"][str(c["id"])] = col
    for i, e in new.items():
        m["entities"][i] = {k: e.get(k) for k in ("name", "popularity", "tags")}
    seeds = m.setdefault("seeds", {"wanted": 0, "found": 0, "dropped": []})
    still = [n for n in seeds.get("dropped", []) if n not in found]
    seeds.update({"found": seeds.get("found", 0) + sum(1 for n in found if n in seeds.get("dropped", [])),
                  "dropped": sorted(set(still) | {n for n in dropped if n not in found})})
    m["extended"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    if on_progress:
        on_progress({"stage": "done", "added": len(new), "artists": len(m["entities"])})
    return {"added": [e["name"] for e in new.values()], "dropped": dropped, "artists": len(m["entities"])}


# ---- keeping it current ----------------------------------------------------------------------
REFRESH_DAYS = 7        # re-resolve the outside list and extend this often
REBUILD_DAYS = 30       # full rebuild this often, in the quiet hours
QUIET_HOURS_UTC = range(8, 12)     # 1 to 5 AM Arizona: the bar is closed


def _stamp(s: str | None) -> float:
    try:
        return time.mktime(time.strptime(s or "", "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (ValueError, TypeError):
        return 0.0


def due(m: dict | None, now: float | None = None, *, refresh_days: int = REFRESH_DAYS, rebuild_days: int = REBUILD_DAYS) -> str | None:
    """What the matrix needs right now: 'build' (none, or older than rebuild_days and it is quiet),
    'extend' (the outside list has not been checked in refresh_days), or None."""
    now = time.time() if now is None else now
    if not m:
        return "build"
    built = _stamp(m.get("built"))
    checked = max(built, _stamp(m.get("extended")))
    quiet = time.gmtime(now).tm_hour in QUIET_HOURS_UTC
    if rebuild_days and now - built > rebuild_days * 86400 and quiet:
        return "build"
    if refresh_days and now - checked > refresh_days * 86400:
        return "extend"
    return None
