"""Rooms: the state of one game between two phones.

Phases:
  quiz      both partners tap through four "which is more you" questions
  cards     each is dealt six, plays two, locks in           (CARD_ROUNDS times)
  reveal    both hands flip; a line lands under each pair; the meter moves
  thinking  Q-Lou is working (steps stream to both phones)
  jukebox   the pick, the why, the tracks; "Play it for a credit?"
  done      meter, title, what you share, Q-Lou's shortlist, the LLM-vs-Qloo control

State lives in memory and is mirrored to a pickle so a restart does not end a game.
No accounts, no cookies: a room is a code and two first names, swept after six hours.
"""
from __future__ import annotations

import pathlib
import pickle
import random
import threading
import time
from dataclasses import dataclass, field

from . import deck, quiz
from .qlou import Partner, Pick, QLou
from .qloo import Qloo

CARD_ROUNDS = 2
MAX_PICK_ATTEMPTS = 3
PLAYED_BONUS = 5
ROOM_TTL = 6 * 3600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"

TITLES = [(90, "Common Ground, Fully Claimed"), (70, "Same Page, Different Chapters"),
          (50, "Neighbors on the Same Street"), (30, "Different Roads, Same Town"), (0, "Opposites, Attracting")]


def make_code(rng: random.Random | None = None) -> str:
    rng = rng or random
    return "".join(rng.choice(CODE_ALPHABET) for _ in range(4))


@dataclass
class Side:
    name: str = ""
    joined: bool = False
    house: bool = False
    quiz: quiz.Quiz = field(default_factory=quiz.Quiz)
    hands: list[list[int]] = field(default_factory=list)   # per card round
    plays: list[list[int]] = field(default_factory=list)   # per card round
    seen: list[int] = field(default_factory=list)

    @property
    def profile(self) -> list[str]:
        return self.quiz.profile

    def locked(self, rnd: int) -> bool:
        return 0 <= rnd < len(self.plays) and len(self.plays[rnd]) == deck.PLAYS

    def all_plays(self) -> list[int]:
        return [c for r in self.plays for c in r]

    def partner(self, key: str) -> Partner:
        return Partner(key=key, name=self.name or key, cards=self.all_plays(), profile=self.profile)


@dataclass
class Room:
    code: str
    created: float = field(default_factory=time.time)
    phase: str = "quiz"
    round: int = -1
    sides: dict[str, Side] = field(default_factory=lambda: {"host": Side(), "guest": Side()})
    reveals: list[dict] = field(default_factory=list)
    meter: int = 0
    thinking: list[dict] = field(default_factory=list)   # Q-Lou's live steps
    pick: dict | None = None                             # Pick.to_dict() + votes + attempt
    excluded: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __getstate__(self):
        d = self.__dict__.copy(); d.pop("lock", None); return d

    def __setstate__(self, d):
        self.__dict__.update(d); self.lock = threading.Lock()

    # ---- quiz ----------------------------------------------------------------------------
    def question(self, who: str) -> dict | None:
        return self.sides[who].quiz.next_question()

    def answer(self, who: str, a: str, b: str, chose: str | None) -> None:
        self.sides[who].quiz.answer(a, b, chose)

    def both_quizzed(self) -> bool:
        return all(s.joined and s.quiz.done for s in self.sides.values())

    # ---- cards ---------------------------------------------------------------------------
    def deal(self, rng: random.Random | None = None) -> None:
        self.round += 1
        self.phase = "cards"
        for s in self.sides.values():
            hand = deck.deal(set(s.seen), rng)
            s.hands.append(hand)
            s.plays.append([])
            s.seen += hand
            if s.house:
                s.plays[-1] = self._house_plays(s, hand, rng)
        self.log.append(f"round {self.round + 1}: dealt")

    @staticmethod
    def _house_plays(s: Side, hand: list[int], rng: random.Random | None = None) -> list[int]:
        """The house plays the two cards closest to its own profile; ties break at random."""
        rng = rng or random
        fams = {quiz.family(a) for a in s.profile}
        scored = []
        for cid in hand:
            sig = deck.card(cid)["signal"]
            hit = sum(1 for a in sig.get("audiences", []) if quiz.family(a) in fams)
            scored.append((hit + rng.random() * 0.5, cid))
        scored.sort(reverse=True)
        return [cid for _, cid in scored[:deck.PLAYS]]

    def play(self, who: str, cards: list[int]) -> bool:
        s = self.sides[who]
        if self.phase != "cards" or self.round >= len(s.hands):
            return False
        picks = [c for c in dict.fromkeys(cards) if c in s.hands[self.round]][:deck.PLAYS]
        if len(picks) != deck.PLAYS:
            return False
        s.plays[self.round] = picks
        return True

    def both_locked(self) -> bool:
        return all(s.locked(self.round) for s in self.sides.values())

    def reveal(self, rng: random.Random | None = None) -> None:
        h, g = self.sides["host"], self.sides["guest"]
        ha, ga = h.plays[self.round], g.plays[self.round]
        pairs, gained = [], 0
        for hc, gc in deck.pair_plays(ha, ga):
            ca, cb = deck.card(hc), deck.card(gc)
            kind = deck.match_kind(ca, cb)
            gained += deck.METER_GAIN[kind]
            pairs.append({"a": deck.public(ca), "b": deck.public(cb), "match": kind,
                          "lines": deck.lines_for(h.name or "Your partner", ca, g.name or "Your partner", cb, rng)})
        both = [deck.card(c)["name"] for c in ha if c in ga]
        self.meter = min(100, self.meter + gained)
        self.reveals.append({"round": self.round, "pairs": pairs, "both": both, "meter": self.meter})
        self.phase = "reveal"
        self.log.append(f"round {self.round + 1}: revealed, meter {self.meter}")

    def rounds_left(self) -> bool:
        return self.round + 1 < CARD_ROUNDS

    # ---- Q-Lou ---------------------------------------------------------------------------
    def think(self, q: Qloo, *, use_llm: bool = True) -> Pick | None:
        """Run Q-Lou. Steps stream into self.thinking as they happen. The caller moves the phase:
        the pick may be ready while the phones are still watching the reveal."""
        attempt = (self.pick or {}).get("attempt", 0) + 1
        rnd = self.round
        self.thinking = []

        def refresh(late: Pick) -> None:
            """A slow extra (the line, the tracks, the control) landed after the pick was returned."""
            if self.round == rnd and self.pick and self.pick.get("attempt") == attempt:
                d = late.to_dict()
                self.pick.update({k: d[k] for k in ("tracks", "why", "why_source", "baseline", "live", "calls")})

        agent = QLou(q, use_llm=use_llm, on_step=self.thinking.append, on_update=refresh)
        p = agent.pick(self.sides["host"].partner("host"), self.sides["guest"].partner("guest"), set(self.excluded))
        if self.round != rnd:
            return None   # a new round was dealt meanwhile; this run is stale
        if p is None:
            self.log.append(f"Q-Lou came up empty on attempt {attempt}")
            return None
        self.pick = {**p.to_dict(), "votes": {}, "attempt": attempt, "played": None}
        self.log.append(f"pick {attempt}: {p.artist['name']} in {p.seconds}s, {p.calls} Qloo calls")
        return p

    def vote(self, who: str, up: bool) -> None:
        if self.pick:
            self.pick["votes"][who] = bool(up)

    def settled(self) -> str | None:
        """'play' when both say yes; 'redeal' when one says no and tries remain; 'skip'; None while waiting."""
        if not self.pick or len(self.pick["votes"]) < 2:
            return None
        if all(self.pick["votes"].values()):
            self.pick["played"] = True
            return "play"
        self.excluded.append(self.pick["artist"]["id"])
        if self.pick["attempt"] < MAX_PICK_ATTEMPTS:
            return "redeal"
        self.pick["played"] = False
        return "skip"

    # ---- result --------------------------------------------------------------------------
    def finish(self) -> dict:
        """Common Ground is Q-Lou's find: the pick's weaker score, if that beats the card meter."""
        if self.pick:
            self.meter = max(self.meter, int(min(self.pick["scores"].values()) * 100))
        if self.pick and self.pick.get("played"):
            self.meter = min(100, self.meter + PLAYED_BONUS)
        title = next(t for m, t in TITLES if self.meter >= m)
        names = {k: (s.name or k) for k, s in self.sides.items()}
        p = self.pick or {}
        self.result = {
            "meter": self.meter, "title": title,
            "plays": {names[k]: [deck.card(c)["name"] for c in s.all_plays()] for k, s in self.sides.items()},
            # across the whole game, not round by round: a card you both played in different rounds still counts
            "both_played": sorted({deck.card(c)["name"] for c in set(self.sides["host"].all_plays()) & set(self.sides["guest"].all_plays())}),
            "profiles": {names[k]: [quiz.label(x) for x in s.profile] for k, s in self.sides.items()},
            "pick": {k: p.get(k) for k in ("artist", "tracks", "played", "why", "why_source", "ranked", "baseline", "explain", "scores", "attempt", "calls", "seconds")} if p else None,
        }
        self.phase = "done"
        return self.result

    # ---- what a phone sees ---------------------------------------------------------------
    def public(self, who: str | None = None) -> dict:
        sides = {}
        for k, s in self.sides.items():
            d = {"name": s.name, "joined": s.joined, "house": s.house, "answered": len(s.quiz.answers),
                 "profile_labels": [quiz.label(x) for x in s.profile], "locked": s.locked(self.round)}
            if k == who and 0 <= self.round < len(s.hands):
                d["hand"] = [deck.public(deck.card(c)) for c in s.hands[self.round]]
                d["plays"] = s.plays[self.round]
            sides[k] = d
        pick = None
        if self.pick:
            pick = {k: v for k, v in self.pick.items() if k not in ("votes", "steps")}
            pick["voted"] = {k: (k in self.pick["votes"]) for k in self.sides}
        return {"code": self.code, "phase": self.phase, "round": self.round, "rounds": CARD_ROUNDS, "sides": sides,
                "more_allowed": self.rounds_left(), "pick_ready": bool(self.pick) and self.phase == "reveal",
                "reveal": self.reveals[-1] if self.reveals and self.phase in ("reveal", "thinking", "jukebox", "done") else None,
                "meter": self.meter, "thinking": self.thinking[-8:], "pick": pick, "log": self.log[-5:],
                "result": self.result, "t": time.time()}


class Rooms:
    def __init__(self, path: str | pathlib.Path | None = None, rng: random.Random | None = None) -> None:
        self.path = pathlib.Path(path) if path else pathlib.Path(__file__).resolve().parents[1] / "data" / "rooms.pkl"
        self.rooms: dict[str, Room] = {}
        self.lock = threading.Lock()
        self.rng = rng or random.Random()
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                self.rooms = pickle.loads(self.path.read_bytes())
        except Exception:
            self.rooms = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(self.rooms))
            tmp.replace(self.path)
        except Exception:
            pass

    def create(self, host_name: str, house: bool = False) -> Room:
        with self.lock:
            code = make_code(self.rng)
            while code in self.rooms:
                code = make_code(self.rng)
            r = Room(code)
            r.sides["host"].name, r.sides["host"].joined = host_name, True
            if house:
                h = self.rng.choice(quiz.HOUSE)
                g = r.sides["guest"]
                g.name, g.joined, g.house = h["name"], True, True
                g.quiz = quiz.Quiz.preset(h["profile"])
                r.log.append(f"Q-Lou seats you with {h['name']}")
            self.rooms[code] = r
            self.save()
            return r

    def get(self, code: str) -> Room | None:
        return self.rooms.get((code or "").upper())

    def join(self, code: str, name: str) -> Room | None:
        r = self.get(code)
        if not r or r.sides["guest"].joined:
            return None
        r.sides["guest"].name, r.sides["guest"].joined = name, True
        self.save()
        return r

    def sweep(self, max_age: float = ROOM_TTL) -> int:
        now = time.time()
        with self.lock:
            old = [k for k, r in self.rooms.items() if now - r.created > max_age]
            for k in old:
                del self.rooms[k]
        return len(old)
