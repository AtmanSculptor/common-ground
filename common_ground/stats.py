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
        "artist": (p.get("artist") or {}).get("name"), "artist_id": (p.get("artist") or {}).get("id"), "played": p.get("played"),
        "attempt": p.get("attempt"), "calls": p.get("calls"), "seconds": p.get("seconds"),
        "why_source": p.get("why_source"),
        "scores": p.get("scores"),
        "baseline": {k: (p.get("baseline") or {}).get(k) for k in ("guess", "verdict", "scores")} if p.get("baseline") else None,
    }
    with _lock:
        try:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            with FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass


def recent_ids(n: int) -> list[str]:
    """The last n artists Q-Lou picked at this venue (played or not): the rotation."""
    out: list[str] = []
    for r in reversed(rows(500)):
        a = r.get("artist_id")
        if a and a not in out:
            out.append(a)
        if len(out) >= n:
            break
    return out


LEVELS = ((10, 3), (3, 2), (1, 1))   # games played at this venue -> the ring on your picture
_profiles: dict[str, tuple[float, dict]] = {}


def profile(name: str) -> dict:
    """What this venue knows about a nickname: games, songs that played, the level. No account, no
    login: the nickname at this bar is the identity (in a jukebox app it would be the app's account).
    Cached a minute; the log is small but polled often."""
    key = (name or "").strip().lower()
    now = time.time()
    hit = _profiles.get(key)
    if hit and now - hit[0] < 60:
        return hit[1]
    games = songs = 0
    cards: Counter = Counter()
    if key:
        for r in rows(2000):
            plays = r.get("plays") or {}
            mine = next((v for k, v in plays.items() if k.strip().lower() == key), None)
            if mine is not None:
                games += 1
                cards.update(c for c in mine if c)
                if r.get("played"):
                    songs += 1
    level = next((lv for n, lv in LEVELS if games >= n), 0)
    out = {"games": games, "songs": songs, "level": level, "cards": [c for c, _ in cards.most_common(3)]}
    _profiles[key] = (now, out)
    return out


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
        "baseline": guess_vs_data(base),
        "recent": [{k: r.get(k) for k in ("t", "house", "meter", "title", "artist", "played", "attempt")} for r in rs[-15:]][::-1],
    }


def guess_vs_data(base: list[dict]) -> dict:
    """The control, rolled up: how often Qloo's pick out-scored a plain LLM's cold guess.
    'both': higher for each partner. 'weaker': higher on the weaker partner's score, which is
    the game's own criterion. Only games where both were scored count toward those two."""
    scored = [r for r in base if r.get("scores") and (r["baseline"].get("scores") or {})]
    both = weaker = 0
    for r in scored:
        ps, bs = r["scores"], r["baseline"]["scores"]
        keys = [k for k in ps if k in bs and bs[k] is not None]
        if len(keys) < 2:
            continue
        if all(ps[k] > bs[k] for k in keys):
            both += 1
        if min(ps[k] for k in keys) > min(bs[k] for k in keys):
            weaker += 1
    verdicts = Counter(r["baseline"].get("verdict") for r in base)
    return {"n": len(base), "holds": verdicts.get("holds", 0), "splits": verdicts.get("splits", 0), "misses": verdicts.get("misses", 0),
            "scored": len(scored), "qlou_higher_both": both, "qlou_higher_weaker": weaker}


def archive(label: str = "") -> str | None:
    """Move the log aside (games-<stamp>[-label].jsonl) so the next night starts on a clean slate.
    Returns the archive's name, or None when there was nothing to move."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch for ch in label if ch.isalnum() or ch in "-_")[:40]
    target = FILE.with_name(f"games-{stamp}{'-' + safe if safe else ''}.jsonl")
    with _lock:
        if not FILE.exists():
            return None
        os.replace(FILE, target)
    return target.name
