"""Song catalog. Qloo knows artists, not tracks, so the last step is a catalog lookup.

In the demo that catalog is Apple's iTunes Search API (public, no key, 30-second previews).
In a jukebox product it is the jukebox's own catalog. The seam is this one function.
"""
from __future__ import annotations

import threading

import requests

SEARCH = "https://itunes.apple.com/search"
TOP = 5
_cache: dict[str, list[dict]] = {}
_lock = threading.Lock()


def _fetch(artist: str) -> list[dict]:
    r = requests.get(SEARCH, params={"term": artist, "entity": "song", "limit": 25, "country": "US"},
                     timeout=10, headers={"User-Agent": "OurSong/1.0 (+https://github.com/AtmanSculptor/common-ground)"})
    r.raise_for_status()
    want = artist.lower()
    out: list[dict] = []
    seen: set[str] = set()
    for t in r.json().get("results", []):
        who = (t.get("artistName") or "").lower()
        if not (who == want or want in who) or not t.get("previewUrl"):
            continue
        name = t.get("trackName") or ""
        key = name.lower().split(" (")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append({"track": name, "artist": t.get("artistName"), "preview": t["previewUrl"],
                    "art": (t.get("artworkUrl100") or "").replace("100x100", "300x300"),
                    "album": t.get("collectionName"), "year": (t.get("releaseDate") or "")[:4]})
        if len(out) >= TOP:
            break
    return out


def top_tracks(artist: str) -> list[dict]:
    """Up to five tracks by this artist, most popular first per the catalog. Empty if none."""
    with _lock:
        if artist in _cache:
            return _cache[artist]
    try:
        tracks = _fetch(artist)
    except Exception:
        tracks = []
    with _lock:
        _cache[artist] = tracks
    return tracks
