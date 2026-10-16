"""Common Ground, card edition. Two phones, one room, Q-Lou in the jukebox.

Flow per room:
  lobby   - host makes a room (4-letter code), partner joins on their own phone
  quiz    - 4 quick "which is more you" taps each, seeds the audience profile
  cards   - each side is dealt 6 cards and plays the 2 that are most them; locks in
  reveal  - both hands flip at once; each played card lands with a line about the two of you
  (cards round 2, same shape, fresh cards)
  jukebox - Q-Lou scores artists against BOTH partners' plays (tags + audiences) with Qloo,
            finds the one you share, then looks up that artist's top track (iTunes Search,
            30s preview) so the phones can actually play it. "Play it for a credit?"
  done    - meter, title, what you share, playlist

Every pick is Qloo. The song catalog is Apple's for the demo and TouchTunes' in production.
"""
from __future__ import annotations

import json
import pathlib
import random
import threading
import time
from dataclasses import dataclass, field, asdict

import requests

from .qloo import Qloo, affinity, slim
from .game import A, AUDIENCES, _load_sim, label, quizable, family, _SIM, _code, HOUSE, NICE

ROOT = pathlib.Path(__file__).resolve().parents[1]
NO_SIGNAL = 0.765
QUIZ_ROUNDS = 4
HAND = 6
PLAYS = 2
CARD_ROUNDS = 2
MUSIC_CATS = {"music"}

_DECK: list[dict] = []


def deck() -> list[dict]:
    global _DECK
    if not _DECK:
        _DECK = json.loads((ROOT / "static" / "cards.json").read_text(encoding="utf-8"))
    return _DECK


def card(cid: int) -> dict:
    for c in deck():
        if c["id"] == cid:
            return c
    raise KeyError(cid)


# ---- lines about the two of you ----------------------------------------------------
LINES_SAME = ["You both played {card}. That is not a coincidence.", "Same card, both hands: {card}.",
              "{card}, from both of you. Q-Lou noticed."]
LINES_CAT = {
    ("music", "music"): ["{a} leads with {ca}. {b} answers with {cb}. Q-Lou hears a bridge.",
                         "{ca} meets {cb}. Somewhere in the middle there is a song."],
    ("life", "life"): ["{a} is {ca}. {b} is {cb}. Different mornings, same bar.",
                       "{ca} and {cb}. That is two ways of spending a day."],
    ("table", "table"): ["{ca} for {a}, {cb} for {b}. The round is on the house."],
    ("heart", "heart"): ["{ca} and {cb}. Two homes, one jukebox."],
    ("place", "place"): ["{ca} meets {cb}. Every town has a bar like this one."],
}
LINES_MIX = ["{a} played {ca}. {b} played {cb}. Q-Lou is taking notes.",
             "{ca} on one side, {cb} on the other. The meter moved.",
             "{a}: {ca}. {b}: {cb}. Keep going."]


def line_for(a_name: str, ca: dict, b_name: str, cb: dict) -> str:
    if ca["id"] == cb["id"]:
        return random.choice(LINES_SAME).format(card=ca["name"])
    key = (ca["cat"], cb["cat"])
    opts = LINES_CAT.get(key) or LINES_CAT.get((key[1], key[0])) or LINES_MIX
    return random.choice(opts).format(a=a_name, b=b_name, ca=ca["name"], cb=cb["name"])


# ---- state ----------------------------------------------------------------------------
@dataclass
class Side:
    name: str = ""
    joined: bool = False
    house: bool = False
    answers: list[dict] = field(default_factory=list)
    tally: dict[str, int] = field(default_factory=dict)
    profile: list[str] = field(default_factory=list)
    pending: dict | None = None
    hands: list[list[int]] = field(default_factory=list)   # per card round
    plays: list[list[int]] = field(default_factory=list)   # per card round
    seen: list[int] = field(default_factory=list)

    def locked(self, rnd: int) -> bool:
        return len(self.plays) > rnd and len(self.plays[rnd]) == PLAYS

    def signal(self) -> dict:
        tags: list[str] = []
        auds: list[str] = list(self.profile)
        for rnd in self.plays:
            for cid in rnd:
                s = card(cid)["signal"]
                tags += s.get("tags", [])
                auds += s.get("audiences", [])
        out = {}
        if tags:
            out["tags"] = list(dict.fromkeys(tags))
        if auds:
            out["audiences"] = list(dict.fromkeys(auds))
        return out


@dataclass
class Room:
    code: str
    created: float = field(default_factory=time.time)
    phase: str = "lobby"   # lobby | quiz | cards | reveal | thinking | jukebox | done
    round: int = -1
    sides: dict[str, Side] = field(default_factory=lambda: {"host": Side(), "guest": Side()})
    reveals: list[dict] = field(default_factory=list)      # per round: {"pairs":[{a,b,line,match}], "meter": n}
    meter: int = 0
    jukebox: dict | None = None    # {"artist", "song", "why", "votes", "attempt", "played", "ranked"}
    excluded: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __getstate__(self):
        d = self.__dict__.copy(); d.pop("lock", None); return d

    def __setstate__(self, d):
        self.__dict__.update(d); self.lock = threading.Lock()

    # ---- quiz (short) -----------------------------------------------------------------
    def next_question(self, who: str) -> dict | None:
        _load_sim()
        s = self.sides[who]
        if len(s.answers) >= QUIZ_ROUNDS:
            return None
        if s.pending:
            return s.pending
        seen = {x for a in s.answers for x in (a["a"], a["b"])}
        all_ids = [a["id"] for a in AUDIENCES if quizable(a["id"])]
        leaders = [k for k, _ in sorted(s.tally.items(), key=lambda kv: -kv[1])]
        a_id = None
        for lead in leaders:
            for cand in _SIM.get(lead, {}):
                if cand not in seen and cand != lead and quizable(cand):
                    a_id = cand; break
            if a_id:
                break
        if not a_id:
            pool = [x for x in all_ids if x not in seen] or all_ids
            a_id = random.choice(pool)
        fams = {family(x) for x in seen} | {family(a_id)}
        wild = [x for x in all_ids if x not in seen and family(x) not in fams] or [x for x in all_ids if x != a_id]
        pair = [a_id, random.choice(wild)]
        random.shuffle(pair)
        s.pending = {"n": len(s.answers) + 1, "of": QUIZ_ROUNDS,
                     "a": {"text": label(pair[0]), "id": pair[0]}, "b": {"text": label(pair[1]), "id": pair[1]}}
        return s.pending

    def answer(self, who: str, a: str, b: str, chose: str | None) -> None:
        s = self.sides[who]
        if s.pending and {s.pending["a"]["id"], s.pending["b"]["id"]} != {a, b}:
            return
        s.pending = None
        s.answers.append({"a": a, "b": b, "chose": chose})
        if chose:
            s.tally[chose] = s.tally.get(chose, 0) + 1
        if len(s.answers) >= QUIZ_ROUNDS:
            s.profile = [k for k, _ in sorted(s.tally.items(), key=lambda kv: -kv[1])[:3]]

    def both_quizzed(self) -> bool:
        return all(s.joined and len(s.answers) >= QUIZ_ROUNDS for s in self.sides.values())

    # ---- cards ------------------------------------------------------------------------
    def deal(self) -> None:
        """New round: 6 cards each, 3 music + 3 other, nothing they have seen."""
        self.round += 1
        self.phase = "cards"
        for s in self.sides.values():
            music = [c for c in deck() if c["cat"] in MUSIC_CATS and c["id"] not in s.seen]
            other = [c for c in deck() if c["cat"] not in MUSIC_CATS and c["id"] not in s.seen]
            hand = random.sample(music, min(3, len(music))) + random.sample(other, min(HAND - 3, len(other)))
            random.shuffle(hand)
            ids = [c["id"] for c in hand]
            s.hands.append(ids)
            s.plays.append([])
            s.seen += ids
            if s.house:
                s.plays[-1] = self._house_plays(s, ids)
        self.log.append(f"round {self.round + 1}: dealt")

    def _house_plays(self, s: Side, hand: list[int]) -> list[int]:
        """The house plays the two cards closest to its own profile family, else random."""
        fams = {family(a) for a in s.profile}
        scored = []
        for cid in hand:
            sig = card(cid)["signal"]
            hit = sum(1 for a in sig.get("audiences", []) if family(a) in fams)
            scored.append((hit + random.random() * 0.5, cid))
        scored.sort(reverse=True)
        return [cid for _, cid in scored[:PLAYS]]

    def play(self, who: str, cards: list[int]) -> bool:
        s = self.sides[who]
        if self.phase != "cards" or len(s.plays) <= self.round:
            return False
        hand = s.hands[self.round]
        picks = [c for c in cards if c in hand][:PLAYS]
        if len(picks) != PLAYS:
            return False
        s.plays[self.round] = picks
        return True

    def both_locked(self) -> bool:
        return all(s.locked(self.round) for s in self.sides.values())

    def reveal(self) -> None:
        h, g = self.sides["host"], self.sides["guest"]
        ha, ga = h.plays[self.round], g.plays[self.round]
        pairs = []
        gained = 0
        for i in range(PLAYS):
            ca, cb = card(ha[i]), card(ga[i])
            match = ca["id"] == cb["id"] or ca["cat"] == cb["cat"]
            exact = ca["id"] == cb["id"]
            gained += 25 if exact else (10 if match else 3)
            pairs.append({"a": {"id": ca["id"], "name": ca["name"], "img": ca["img"], "cat": ca["cat"]},
                          "b": {"id": cb["id"], "name": cb["name"], "img": cb["img"], "cat": cb["cat"]},
                          "line": line_for(h.name or "You", ca, g.name or "your partner", cb),
                          "match": "exact" if exact else ("kin" if match else "none")})
        # cross matches: a card both played in any slot
        both = set(ha) & set(ga)
        self.meter = min(100, self.meter + gained)
        self.reveals.append({"round": self.round, "pairs": pairs, "both": [card(c)["name"] for c in both], "meter": self.meter})
        self.phase = "reveal"
        self.log.append(f"round {self.round + 1}: revealed, meter {self.meter}")

    def rounds_left(self) -> bool:
        return self.round + 1 < CARD_ROUNDS

    # ---- jukebox ------------------------------------------------------------------------
    def pick(self, q: Qloo) -> dict | None:
        """Score artists against both partners' signals; the pick is the one both rank highest."""
        attempt = (self.jukebox or {}).get("attempt", 0) + 1
        skip = set(self.excluded)
        h, g = self.sides["host"], self.sides["guest"]
        sa, sb = h.signal(), g.signal()
        both = {"tags": sa.get("tags", []) + sb.get("tags", []), "audiences": sa.get("audiences", []) + sb.get("audiences", [])}
        both = {k: v for k, v in both.items() if v}

        def pool(sig, take):
            try:
                return {e["entity_id"]: e for e in q.insights("urn:entity:artist", take=take, popularity_min=0.9, **sig)}
            except Exception as e:
                self.log.append(f"pool error: {e}"); return {}

        p = {**pool(sa, 25), **pool(sb, 25), **pool(both, 40)}
        ids = [i for i in p if i not in skip]
        if not ids:
            return None

        def score(sig):
            out = {}
            for i in range(0, len(ids), 50):
                b = ids[i:i + 50]
                for e in q.insights("urn:entity:artist", result_entities=b, take=len(b), **sig):
                    out[e["entity_id"]] = affinity(e)
            return out

        ra, rb = score(sa), score(sb)
        keep = [i for i in ids if i in ra and i in rb and abs(ra[i] - NO_SIGNAL) > 1e-6 and abs(rb[i] - NO_SIGNAL) > 1e-6]
        if not keep:
            return None

        def curve(d):
            lo, hi = min(d.values()), max(d.values())
            return {k: (v - lo) / (hi - lo) if hi - lo > 1e-9 else 1.0 for k, v in d.items()}

        ca, cb = curve({i: ra[i] for i in keep}), curve({i: rb[i] for i in keep})
        ranked = sorted(keep, key=lambda i: (-min(ca[i], cb[i]), abs(ca[i] - cb[i])))
        top = ranked[0]
        ent = slim(p[top])
        why = self._why(h, g, ent)
        song = find_song(ent["name"])
        self.jukebox = {"artist": ent, "song": song, "why": why, "votes": {}, "attempt": attempt, "played": None,
                        "scores": {"host": round(ca[top], 2), "guest": round(cb[top], 2)},
                        "ranked": [{"name": slim(p[i])["name"], "host": round(ca[i], 2), "guest": round(cb[i], 2)} for i in ranked[:8]],
                        "pool": len(ids), "calls": q.calls}
        self.phase = "jukebox"
        self.log.append(f"jukebox pick {attempt}: {ent['name']} ({song.get('track') if song else 'no track'})")
        return self.jukebox

    def _why(self, h: Side, g: Side, ent: dict) -> str:
        hp = [card(c)["name"] for r in h.plays for c in r]
        gp = [card(c)["name"] for r in g.plays for c in r]
        tags = [t for t in ent.get("tags", []) if t][:3]
        bits = []
        if hp and gp:
            bits.append(f"{h.name or 'You'} played {', '.join(hp[:2])}. {g.name or 'Your partner'} played {', '.join(gp[:2])}.")
        if tags:
            bits.append(f"Qloo files {ent['name']} under {', '.join(tags)}.")
        bits.append("Both of you rank them near the top. That is the common ground.")
        return " ".join(bits)

    def vote(self, who: str, up: bool) -> None:
        if self.jukebox:
            self.jukebox["votes"][who] = bool(up)

    def settled(self) -> str | None:
        if not self.jukebox or len(self.jukebox["votes"]) < 2:
            return None
        if all(self.jukebox["votes"].values()):
            self.jukebox["played"] = True
            return "play"
        self.excluded.append(self.jukebox["artist"]["id"])
        if self.jukebox["attempt"] < 3:
            return "redeal"
        self.jukebox["played"] = False
        return "skip"

    # ---- result -------------------------------------------------------------------------
    TITLES = [(90, "Common Ground, Fully Claimed"), (70, "Same Page, Different Chapters"),
              (50, "Neighbors on the Same Street"), (30, "Different Roads, Same Town"), (0, "Opposites, Attracting")]

    def finish(self) -> dict:
        if self.jukebox and self.jukebox.get("played"):
            self.meter = min(100, self.meter + 20)
        title = next(t for m, t in self.TITLES if self.meter >= m)
        names = {k: (s.name or k) for k, s in self.sides.items()}
        plays = {names[k]: [card(c)["name"] for r in s.plays for c in r] for k, s in self.sides.items()}
        both = sorted({n for r in self.reveals for n in r["both"]})
        jb = self.jukebox or {}
        self.result = {"meter": self.meter, "title": title, "plays": plays, "both_played": both,
                       "profiles": {names[k]: [label(x) for x in s.profile] for k, s in self.sides.items()},
                       "jukebox": {"artist": jb.get("artist", {}).get("name"), "song": jb.get("song"), "played": jb.get("played"),
                                   "why": jb.get("why"), "ranked": jb.get("ranked", [])} if jb else None}
        self.phase = "done"
        return self.result

    def public(self, who: str | None = None) -> dict:
        sides = {}
        for k, s in self.sides.items():
            d = {"name": s.name, "joined": s.joined, "house": s.house, "answered": len(s.answers),
                 "profile_labels": [label(x) for x in s.profile], "locked": s.locked(self.round) if self.round >= 0 else False}
            if k == who and self.round >= 0 and len(s.hands) > self.round:
                d["hand"] = [{k2: v for k2, v in card(c).items() if k2 != "signal"} for c in s.hands[self.round]]
                d["plays"] = s.plays[self.round]
            sides[k] = d
        jb = None
        if self.jukebox:
            jb = {k: v for k, v in self.jukebox.items() if k != "votes"}
            jb["voted"] = {k: (k in self.jukebox["votes"]) for k in self.sides}
        return {"code": self.code, "phase": self.phase, "round": self.round, "rounds": CARD_ROUNDS, "sides": sides,
                "reveal": self.reveals[-1] if self.reveals and self.phase in ("reveal", "thinking", "jukebox", "done") else None,
                "meter": self.meter, "jukebox": jb, "log": self.log[-5:], "result": self.result, "t": time.time()}


# ---- song lookup (demo catalog: Apple's iTunes Search, no key) ---------------------------
_SONG_CACHE: dict[str, dict | None] = {}


def find_song(artist: str) -> dict | None:
    if artist in _SONG_CACHE:
        return _SONG_CACHE[artist]
    out = None
    try:
        r = requests.get("https://itunes.apple.com/search", params={"term": artist, "entity": "song", "limit": 8, "country": "US"},
                         timeout=10, headers={"User-Agent": "CommonGround/1.0"})
        for t in r.json().get("results", []):
            if (t.get("artistName", "").lower() == artist.lower() or artist.lower() in t.get("artistName", "").lower()) and t.get("previewUrl"):
                out = {"track": t.get("trackName"), "artist": t.get("artistName"), "preview": t.get("previewUrl"),
                       "art": (t.get("artworkUrl100") or "").replace("100x100", "300x300"), "album": t.get("collectionName")}
                break
    except Exception:
        out = None
    _SONG_CACHE[artist] = out
    return out


class Rooms:
    def __init__(self, path: str | None = None) -> None:
        self.path = pathlib.Path(path) if path else ROOT / "rooms_cards.pkl"
        self.rooms: dict[str, Room] = {}
        self.lock = threading.Lock()
        self.load()

    def load(self) -> None:
        import pickle
        try:
            if self.path.exists():
                self.rooms = pickle.loads(self.path.read_bytes())
        except Exception:
            self.rooms = {}

    def save(self) -> None:
        import pickle
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(self.rooms))
            tmp.replace(self.path)
        except Exception:
            pass

    def create(self, host_name: str, house: bool = False) -> Room:
        with self.lock:
            code = _code()
            while code in self.rooms:
                code = _code()
            r = Room(code)
            r.sides["host"].name, r.sides["host"].joined = host_name, True
            r.phase = "quiz"
            if house:
                h = random.choice(HOUSE)
                g = r.sides["guest"]
                g.name, g.joined, g.house = h["name"], True, True
                g.profile = list(h["profile"])[:3]
                g.answers = [{"a": x, "b": x, "chose": x} for x in h["profile"]]
                r.log.append(f"Q-Lou seats you with {h['name']}")
            self.rooms[code] = r
            self.save()
            return r

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code.upper())

    def join(self, code: str, name: str) -> Room | None:
        r = self.get(code)
        if not r or r.sides["guest"].joined:
            return None
        r.sides["guest"].name, r.sides["guest"].joined = name, True
        self.save()
        return r

    def sweep(self, max_age: float = 6 * 3600) -> None:
        now = time.time()
        with self.lock:
            for k in [k for k, r in self.rooms.items() if now - r.created > max_age]:
                del self.rooms[k]
