"""The card matrix: every card's Qloo affinity to a shared pool of artists, computed once.

Why: Qloo's live scoring calls can take 20 to 40 seconds each on a busy afternoon, and a bar
cannot wait a minute for a song. With the matrix, Q-Lou's draft pick is local arithmetic
over numbers Qloo already gave us, and the live calls only verify the top candidates.

Build (once, ~300 calls): for each card, pull its top artists (the signal's own list), union
them into one pool capped by popularity, then score every pool artist against every card
with filter.results.entities. 0.765 is Qloo's no-signal filler and is stored as missing.

Draft (per game, no calls): a partner's affinity to an artist is the mean of the artist's
scores for the cards that partner played; min-max curve per partner; rank by the weaker
curved score, exactly as the live loop does.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from typing import Any, Callable

from . import deck
from .qloo import Qloo, affinity, slim

STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"
FILE = STATIC / "matrix.json"
ARTIST = "urn:entity:artist"
NO_SIGNAL = 0.765
POOL_PER_CARD = 20
POOL_CAP = 320
POPULARITY_MIN = 0.9
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
    """Mean affinity per artist over the cards played, for artists covered by enough of them."""
    cols = [m["scores"].get(str(c), {}) for c in cards]
    if not cols:
        return {}
    need = max(1, int(len(cols) * MIN_COVERAGE + 0.999))
    out: dict[str, float] = {}
    for eid in m["entities"]:
        vals = [col[eid] for col in cols if eid in col]
        if len(vals) >= need:
            out[eid] = sum(vals) / len(vals)
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
def build(q: Qloo, *, out: pathlib.Path | None = None, checkpoint: pathlib.Path | None = None,
          on_progress: Callable[[dict], None] | None = None, cards: list[dict] | None = None) -> dict:
    """Compute the matrix. Resumable: a checkpoint file holds finished cards."""
    out = out or FILE
    cards = cards or list(deck.cards())
    checkpoint = checkpoint or out.with_suffix(".partial.json")
    state: dict[str, Any] = {"pool": {}, "scores": {}, "started": time.time()}
    try:
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
    except Exception:
        pass

    def progress(stage: str, **data: Any) -> None:
        if on_progress:
            on_progress({"stage": stage, "t": round(time.time() - state["started"], 1), "calls": q.calls, **data})

    def save_partial() -> None:
        try:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(json.dumps(state), encoding="utf-8")
        except Exception:
            pass

    # 1. the pool: each card's own top artists
    if len(state["pool"]) < len(cards):
        for c in cards:
            key = str(c["id"])
            if key in state["pool"]:
                continue
            try:
                ents = q.insights(ARTIST, take=POOL_PER_CARD, popularity_min=POPULARITY_MIN, **c["signal"])
            except Exception as e:
                progress("pool-error", card=c["name"], error=str(e))
                ents = []
            state["pool"][key] = [slim(e) | {"id": e.get("entity_id")} for e in ents if e.get("entity_id")]
            progress("pool", card=c["name"], found=len(ents))
            save_partial()
    entities: dict[str, dict] = {}
    for ents in state["pool"].values():
        for e in ents:
            entities.setdefault(e["id"], e)
    ids = sorted(entities, key=lambda i: -entities[i].get("popularity", 0))[:POOL_CAP]
    progress("pool-done", artists=len(ids))

    # 2. every artist against every card
    for c in cards:
        key = str(c["id"])
        if key in state["scores"]:
            continue
        col: dict[str, float] = {}
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            try:
                for e in q.insights(ARTIST, result_entities=batch, take=len(batch), **c["signal"]):
                    v = affinity(e)
                    if abs(v - NO_SIGNAL) > 1e-6:
                        col[e["entity_id"]] = round(v, 4)
            except Exception as e:
                progress("score-error", card=c["name"], error=str(e))
        state["scores"][key] = col
        progress("score", card=c["name"], scored=len(col), done=len(state["scores"]), of=len(cards))
        save_partial()

    m = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cards": len(cards), "calls": q.calls,
         "entities": {i: {k: entities[i].get(k) for k in ("name", "popularity", "tags")} for i in ids},
         "scores": state["scores"]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
    try:
        checkpoint.unlink()
    except Exception:
        pass
    progress("done", artists=len(ids), seconds=round(time.time() - state["started"], 1))
    return m
