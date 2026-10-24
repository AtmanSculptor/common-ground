"""The door: a public demo that judges share with whoever finds the link.

Two guards, both soft. A nickname that would embarrass the room list is replaced, not refused, so the
game goes on. New tables are limited per address per hour and per day for everyone, so one busy night
cannot spend the Qloo quota before the judges get to it. Nothing here stores anything about a person
beyond an address and a count that expire within the day.
"""
from __future__ import annotations

import re
import threading
import time

PER_IP_PER_HOUR = int(__import__("os").environ.get("CG_TABLES_PER_IP_HOUR", "12"))
PER_DAY = int(__import__("os").environ.get("CG_TABLES_PER_DAY", "400"))
FULL = "The bar is full tonight. Come back tomorrow."

_lock = threading.Lock()
_by_ip: dict[str, list[float]] = {}
_day: list[float] = []

# words that should not sit on a bar's screen next to a judge's name; matched inside the name, case folded,
# with the usual letter swaps (f*ck, sh1t). Kept short on purpose: this is a jukebox, not a moderation product.
_BAD = ("fuck", "shit", "cunt", "nigg", "retard", "bitch", "whore", "slut", "nazi", "hitler", "kike", "chink", "pussy", "penis", "porn")   # no "dick", "cock", "anal", "rape", "spic", "fag": Dick, Cockburn, Analyst, Grape, Spicy and Fagioli are real
_SWAPS = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", ".": "", "_": "", "-": "", " ": ""})
_RX = [re.compile("".join(f"[{c}*#]" for c in w)) for w in _BAD]    # a star or a hash may stand in for any letter


def clean_name(name: str, fallback: str = "Someone") -> str:
    """Trim, drop control characters, cap at 24, and swap a name the room should not show."""
    n = re.sub(r"[\x00-\x1f\x7f]", "", name or "").strip()
    n = re.sub(r"\s+", " ", n)[:24]
    if not n:
        return ""
    folded = n.lower().translate(_SWAPS)
    if any(rx.search(folded) for rx in _RX):
        return fallback
    return n


def client_ip(request) -> str:
    h = request.headers
    return (h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "") or "?")


def allow_table(ip: str, now: float | None = None) -> bool:
    """One more table from this address, if the hour and the day have room. Counts it when allowed."""
    now = now or time.time()
    with _lock:
        hour = [t for t in _by_ip.get(ip, []) if now - t < 3600]
        day = [t for t in _day if now - t < 86400]
        _day[:] = day
        if len(hour) >= PER_IP_PER_HOUR or len(day) >= PER_DAY:
            _by_ip[ip] = hour
            return False
        hour.append(now)
        _by_ip[ip] = hour
        _day.append(now)
        # keep the per-address table from growing with every passer-by
        if len(_by_ip) > 5000:
            for k in [k for k, v in _by_ip.items() if not v or now - v[-1] > 3600]:
                _by_ip.pop(k, None)
        return True


def tables_today() -> int:
    now = time.time()
    with _lock:
        return sum(1 for t in _day if now - t < 86400)
