"""Results log. One JSON line per finished game, and a summary for the /stats page.

Nothing personal is kept: first names as typed, the cards played, the pick, whether it
played, the meter, the Qloo call count and time. That is enough to say, after a night at
the pub, how often two strangers said yes to the same song.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from collections import Counter

DATA_DIR = pathlib.Path(os.environ.get("CG_DATA_DIR", str(pathlib.Path(__file__).resolve().parents[1] / "data")))
FILE = DATA_DIR / "games.jsonl"
_lock = threading.Lock()


def record(room_code: str, result: dict, house: bool) -> None:
    p = result.get("pick") or {}
    row = {
        "t": round(time.time()), "code": room_code, "house": house,
        "meter": result.get("meter"), "title": result.get("title"),
        "plays": result.get("plays"), "both_played": result.get("both_played"),
        "artist": (p.get("artist") or {}).get("name"), "played": p.get("played"),
        "attempt": p.get("attempt"), "calls": p.get("calls"), "seconds": p.get("seconds"),
        "why_source": p.get("why_source"),
        "baseline": {k: (p.get("baseline") or {}).get(k) for k in ("guess", "verdict")} if p.get("baseline") else None,
    }
    with _lock:
        try:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            with FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass


def rows(limit: int = 200) -> list[dict]:
    try:
        lines = FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def summary() -> dict:
    rs = rows(1000)
    real = [r for r in rs if not r.get("house")]
    picks = [r for r in rs if r.get("artist")]
    played = [r for r in picks if r.get("played")]
    base = [r for r in rs if r.get("baseline")]
    return {
        "games": len(rs), "with_partner": len(real), "solo": len(rs) - len(real),
        "songs_played": len(played), "songs_skipped": len(picks) - len(played),
        "play_rate": round(len(played) / len(picks), 2) if picks else None,
        "avg_meter": round(sum(r["meter"] for r in rs if r.get("meter") is not None) / len(rs), 1) if rs else None,
        "first_try": sum(1 for r in played if r.get("attempt") == 1),
        "avg_seconds": round(sum(r["seconds"] for r in picks if r.get("seconds")) / len(picks), 1) if picks else None,
        "avg_calls": round(sum(r["calls"] for r in picks if r.get("calls")) / len(picks), 1) if picks else None,
        "top_artists": Counter(r["artist"] for r in picks).most_common(10),
        "top_cards": Counter(c for r in rs for v in (r.get("plays") or {}).values() for c in v).most_common(12),
        "baseline": {"n": len(base), "holds": sum(1 for r in base if r["baseline"].get("verdict") == "holds"),
                     "splits": sum(1 for r in base if r["baseline"].get("verdict") == "splits")},
        "recent": [{k: r.get(k) for k in ("t", "house", "meter", "title", "artist", "played", "attempt")} for r in rs[-15:]][::-1],
    }
