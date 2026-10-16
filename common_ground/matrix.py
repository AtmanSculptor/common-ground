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

Draft (per game, no calls): a partner's affinity to an artist is the mean of the artist's
scores for the cards that partner played; min-max curve per partner; rank by the weaker
curved score, exactly as the live loop does.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
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


def partner_scores(m: dict, cards: list[int]) -> dict[str, float]:
    """Mean affinity per artist over the cards played, for artists covered by enough of them.

    A card Qloo had no signal on for this artist counts as that card's weakest real score: silence
    is a penalty, not a free pass. An artist still needs real scores on half the cards to be ranked."""
    cols = [col for col in (m["scores"].get(str(c), {}) for c in cards) if col]
    if not cols:
        return {}
    floors = [min(col.values()) for col in cols]
    need = max(1, int(len(cols) * MIN_COVERAGE + 0.999))
    out: dict[str, float] = {}
    for eid in m["entities"]:
        hits = sum(1 for col in cols if eid in col)
        if hits >= need:
            out[eid] = sum(col.get(eid, floor) for col, floor in zip(cols, floors)) / len(cols)
    return out


def draft(m: dict, a_cards: list[int], b_cards: list[int], exclude: set[str] | None = None, top: int = 8) -> list[dict]:
    """Ranked candidates [{id, name, a, b}] from the matrix alone. Empty if either side has no cards."""
    exclude = exclude or set()
    ra, rb = partner_scores(m, a_cards), partner_scores(m, b_cards)
    keep = [e for e in ra if e in rb and e not in exclude]
    if not keep:
        return []
    ca, cb = _curve({e: ra[e] for e in keep}), _curve({e: rb[e] for e in keep})
    ranked = sorted(keep, key=lambda e: (-min(ca[e], cb[e]), abs(ca[e] - cb[e])))
    return [{"id": e, "name": m["entities"][e]["name"], "a": ca[e], "b": cb[e], "entity": m["entities"][e]} for e in ranked[:top]]


# ---- building ------------------------------------------------------------------------------
def seed_names(path: pathlib.Path | None = None) -> list[str]:
    """The outside list: artists bars actually play. Empty when the file is missing."""
    path = path or SEEDS
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    seen: dict[str, str] = {}
    for a in d.get("artists", []):
        name = (a.get("name") if isinstance(a, dict) else a) or ""
        name = " ".join(name.split())
        if name and name.lower() not in seen:
            seen[name.lower()] = name
    return list(seen.values())


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


def _resolve_seed(q: Qloo, name: str) -> dict | None:
    """One outside name -> the Qloo artist entity, or None when Qloo has never met them."""
    for r in q.search(name, types=ARTIST, take=3):
        if r.get("entity_id") and r.get("name", "").strip().lower() == name.lower():
            return slim(r) | {"id": r["entity_id"]}
    return None


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

    def resolve(name: str) -> None:
        try:
            found = _resolve_seed(q, name)
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
