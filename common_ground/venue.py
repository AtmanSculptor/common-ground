"""The room tonight: who is at the bar, the bar's pulse, the queue for "Play the next person", invites.

No accounts. A nickname at this venue is the identity. A phone that opens Our Song and says a name is
"here" until it has been quiet for a couple of minutes. The house regulars (quiz.HOUSE) are always here, so
a judge alone at midnight still walks into a bar with people in it; they are marked as the house.

The queue: press Play the next person and you wait for the next person who presses it. If nobody comes in
QUEUE_WAIT seconds, Q-Lou seats you with a house regular you have not played yet. An invite rings one
person; if they do not answer in INVITE_TTL seconds it fades and nothing is said.
"""
from __future__ import annotations

import json
import threading
import time

from . import quiz, stats

PICS_FILE = stats.DATA_DIR / "pics.json"     # nickname -> the picture they picked, so Brent is Brent next week too

HERE_TTL = 150.0        # seconds since the last heartbeat before a name leaves the room
QUEUE_WAIT = 12.0       # seconds in the queue before the house sits in
INVITE_TTL = 20.0       # seconds an invite rings before it fades

# nights at this venue -> title; the first picture ladder uses the same numbers
TITLES = ((500, "Patron Saint"), (200, "Landmark"), (100, "Icon"), (50, "Legend"), (25, "Veteran"), (10, "Fixture"), (5, "Local"), (1, "Regular"))


def title_for(nights: int) -> str:
    return next((t for n, t in TITLES if nights >= n), "")


def _key(name: str) -> str:
    return (name or "").strip().lower()


class Venue:
    def __init__(self, rooms) -> None:
        self._rooms = rooms                      # a Rooms, or a callable returning the current one
        self.lock = threading.Lock()
        self.here: dict[str, dict] = {}          # key -> {name, icon, seen}
        self.waiting: dict[str, dict] = {}       # key -> {name, icon, since}
        self.matched: dict[str, dict] = {}       # key -> {code, who, at}
        self.invites: dict[str, dict] = {}       # to-key -> {from, from_icon, at}
        try:
            self.pics: dict[str, int] = {k: int(v) for k, v in json.loads(PICS_FILE.read_text()).items()}
        except Exception:
            self.pics = {}

    def pic_for(self, name: str) -> int | None:
        return self.pics.get(_key(name))

    def remember_pic(self, name: str, icon: int) -> None:
        k = _key(name)
        if not k:
            return
        with self.lock:
            if self.pics.get(k) == int(icon) % 64:
                return
            self.pics[k] = int(icon) % 64
            try:
                PICS_FILE.parent.mkdir(parents=True, exist_ok=True)
                PICS_FILE.write_text(json.dumps(self.pics))
            except Exception:
                pass

    @property
    def rooms(self):
        return self._rooms() if callable(self._rooms) else self._rooms

    # ---- presence ----
    def heartbeat(self, name: str, icon: int) -> None:
        k = _key(name)
        if not k:
            return
        with self.lock:
            self.here[k] = {"name": name.strip()[:24], "icon": int(icon) % 64, "seen": time.time()}
            self._sweep()
        self.remember_pic(name, icon)

    def _sweep(self) -> None:
        now = time.time()
        for k in [k for k, v in self.here.items() if now - v["seen"] > HERE_TTL]:
            del self.here[k]
        for k in [k for k, v in self.invites.items() if now - v["at"] > INVITE_TTL]:
            del self.invites[k]
        for k in [k for k, v in self.matched.items() if now - v["at"] > 600]:
            del self.matched[k]

    def _playing(self) -> dict[str, str]:
        """key -> room code for everyone in a live room right now."""
        out: dict[str, str] = {}
        for r in list(self.rooms.rooms.values()):
            if r.phase in ("done",) or time.time() - r.created > 3600:
                continue
            for s in r.sides.values():
                if s.joined and s.name:
                    out[_key(s.name)] = r.code
        return out

    def snapshot(self, me: str = "") -> dict:
        """What the entry screen shows: the bar's name, the pulse, who is here."""
        with self.lock:
            self._sweep()
            playing = self._playing()
            people = []
            mk = _key(me)
            seen = set()
            for k, v in self.here.items():
                if k == mk:
                    continue
                seen.add(k)
                people.append(self._person(v["name"], v["icon"], False, k in playing))
            for h in quiz.HOUSE:
                k = _key(h["name"])
                if k in seen or k == mk:
                    continue
                people.append(self._person(h["name"], h.get("icon", (sum(map(ord, h["name"])) % 6) + 4), True, k in playing))
            people.sort(key=lambda p: (p["playing"], p["house"], -p["nights"]))
            pulse = stats.tonight()
            return {"name": stats.venue_name(), "here": len(self.here) + len(quiz.HOUSE), "playing": sum(1 for p in people if p["playing"]),
                    "songs_tonight": pulse["songs"], "last_played": pulse["last"], "people": people}

    def _person(self, name: str, icon: int, house: bool, playing: bool) -> dict:
        pr = stats.profile(name)
        base = next((h.get("nights", 0) for h in quiz.HOUSE if h["name"] == name), 0) if house else 0
        nights = pr["games"] + base                      # a house regular has been here a while
        return {"name": name, "icon": icon, "house": house, "playing": playing, "nights": nights,
                "title": title_for(nights), "tonight": pr.get("tonight", 0), "cards": pr.get("cards", [])}

    # ---- the queue ----
    def next_person(self, name: str, icon: int) -> dict:
        """Press Play the next person. Returns {status: waiting} or {status: matched, code, who}."""
        k = _key(name)
        with self.lock:
            self._sweep()
            self.here[k] = {"name": name.strip()[:24], "icon": int(icon) % 64, "seen": time.time()}
            if k in self.matched:
                return {"status": "matched", **{x: self.matched[k][x] for x in ("code", "who")}}
            other = next(((ok, ov) for ok, ov in self.waiting.items() if ok != k), None)
            if other:
                ok, ov = other
                del self.waiting[ok]
                r = self.rooms.create(ov["name"], icon=ov["icon"])
                self.rooms.join(r.code, name.strip()[:24], icon=icon)
                self.matched[ok] = {"code": r.code, "who": "host", "at": time.time()}
                self.matched[k] = {"code": r.code, "who": "guest", "at": time.time()}
                return {"status": "matched", "code": r.code, "who": "guest"}
            self.waiting[k] = {"name": name.strip()[:24], "icon": int(icon) % 64, "since": time.time()}
            return {"status": "waiting"}

    def poll(self, name: str) -> dict:
        """The waiting phone asks: matched yet? After QUEUE_WAIT the house sits in. Also carries an invite."""
        k = _key(name)
        with self.lock:
            self._sweep()
            out: dict = {"status": "idle"}
            if k in self.matched:
                m = self.matched.pop(k)
                return {"status": "matched", "code": m["code"], "who": m["who"]}
            w = self.waiting.get(k)
            if w:
                if time.time() - w["since"] >= QUEUE_WAIT:
                    del self.waiting[k]
                    r = self.rooms.create(w["name"], house=True, icon=w["icon"], avoid=self._played_with(k))
                    return {"status": "matched", "code": r.code, "who": "host", "house": r.sides["guest"].name}
                out = {"status": "waiting", "for": round(time.time() - w["since"])}
            inv = self.invites.get(k)
            if inv:
                out["invite"] = {"from": inv["from"], "icon": inv["from_icon"], "left": max(0, round(INVITE_TTL - (time.time() - inv["at"])))}
            return out

    def leave_queue(self, name: str) -> None:
        with self.lock:
            self.waiting.pop(_key(name), None)

    def _played_with(self, k: str) -> set[str]:
        out: set[str] = set()
        for r in stats.rows(2000):
            names = [_key(n) for n in (r.get("plays") or {})]
            if k in names:
                out.update(n for n in names if n != k)
        return out

    # ---- invites ----
    def invite(self, frm: str, frm_icon: int, to: str) -> dict:
        """Ring one person. A house regular answers at once: the room is made and you are the host."""
        tk = _key(to)
        with self.lock:
            self._sweep()
            house = next((h for h in quiz.HOUSE if _key(h["name"]) == tk), None)
            if house:
                r = self.rooms.create(frm.strip()[:24], house=True, icon=frm_icon, house_name=house["name"])
                return {"status": "matched", "code": r.code, "who": "host", "house": house["name"]}
            if tk not in self.here:
                return {"status": "gone"}
            self.invites[tk] = {"from": frm.strip()[:24], "from_icon": int(frm_icon) % 64, "at": time.time()}
            return {"status": "ringing"}

    def answer(self, name: str, icon: int, yes: bool) -> dict:
        """The invited one answers. Yes makes the room: the inviter is the host and finds it on the next poll."""
        k = _key(name)
        with self.lock:
            inv = self.invites.pop(k, None)
            if not inv or not yes:
                return {"status": "idle"}
            fk = _key(inv["from"])
            r = self.rooms.create(inv["from"], icon=inv["from_icon"])
            self.rooms.join(r.code, name.strip()[:24], icon=icon)
            self.matched[fk] = {"code": r.code, "who": "host", "at": time.time()}
            return {"status": "matched", "code": r.code, "who": "guest"}

    def inviter_poll(self, frm: str) -> dict:
        """The one who rang asks whether the answer came."""
        return self.poll(frm)
