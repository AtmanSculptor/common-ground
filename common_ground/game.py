"""Common Ground, the game. Two phones, one room, one field.

Flow per room:
  lobby    - host creates a room, gets a 4-letter code; guest joins
  quiz     - each side answers forced-choice questions ("which is more you?")
             until their profile settles into a few Qloo audience segments
  dealing  - the agent builds the deck: for each round's domain, score candidates
             against both profiles with Qloo, deal the best shared card
  rating   - both rate the card 1 to 5 stars (halves ok); meter moves
  ...      - next round, deck adapts (rejected cards excluded)
  done     - score, title, brief, playlist

State is in memory; one process. Good enough for a pub and a judge.
"""
from __future__ import annotations

import random
import string
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from .engine import Group, score_groups, summarize, NO_SIGNAL
from .qloo import Qloo

A = "urn:audience:"

# Quiz: every audience segment is a possible answer. Nicer wording for some.
NICE = {
    "american_football": "Football Sundays", "avid_reader": "A quiet novel", "cinephile": "Movies, lots of them",
    "news_junkie": "The news", "political_junkie": "Politics", "foodie": "A great meal out", "coffee": "Good coffee",
    "musician": "Playing music", "arts_crafts": "Making things by hand", "home_decor": "Home projects",
    "wine_enthusiast": "Wine", "christianity": "Church", "spirituality": "Spirituality", "healthy_eating": "Eating healthy",
    "parents_with_young_children": "Kids at home", "retirement": "Retired life", "single": "Single life", "engaged": "Getting married",
    "politically_progressive": "Lean progressive", "politically_conservative": "Lean conservative", "politically_center": "Right in the middle",
    "discount_shoppers": "Discount hunting", "gourmand_fine_dining": "Fine dining", "technology_enthusiast": "New gadgets",
    "vintage_apparel": "Vintage finds", "video_gamer": "Video games", "secret_unravelers": "A good mystery",
    "casual_escapists": "Switching off with a show", "adrenaline_rushers": "An adrenaline rush", "spy_enthusiast": "Spy stories",
}
_SIM: dict = {}
AUDIENCES: list[dict] = []


def _load_sim() -> None:
    global _SIM, AUDIENCES
    if _SIM:
        return
    import json, pathlib
    f = pathlib.Path(__file__).resolve().parent / "audience_sim.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        _SIM, AUDIENCES = d.get("sim", {}), d.get("audiences", [])
    else:
        AUDIENCES = Qloo().all_audiences()


def label(aud_id: str) -> str:
    key = aud_id.split(":")[-1]
    if key in NICE:
        return NICE[key]
    for a in AUDIENCES:
        if a["id"] == aud_id:
            return a["name"]
    return key.replace("_", " ").title()


def family(aud_id: str) -> str:
    return aud_id.split(":")[-2]


# Identity is not an interest. These never appear as "which is more you" choices.
QUIZ_EXCLUDE_FAMILIES = {"communities", "professional_area", "investing_interests"}
QUIZ_EXCLUDE_IDS = {A + "lifestyle_preferences_beliefs:christianity", A + "lifestyle_preferences_beliefs:judaism",
                    A + "lifestyle_preferences_beliefs:islam"}


def quizable(aud_id: str) -> bool:
    return family(aud_id) not in QUIZ_EXCLUDE_FAMILIES and aud_id not in QUIZ_EXCLUDE_IDS


QUIZ_ROUNDS = 10
PROFILE_SIZE = 4
ROUND_DOMAINS = ["urn:entity:movie", "urn:entity:artist", "urn:entity:tv_show", "urn:entity:podcast", "urn:entity:movie", "urn:entity:artist"]

TITLES = [  # (min score 0..100, title)
    (90, "Common Ground, Fully Claimed"),
    (75, "Same Page, Different Chapters"),
    (60, "Neighbors on the Same Street"),
    (45, "Different Roads, Same Town"),
    (0, "Opposites, Attracting"),
]


def _code() -> str:
    return "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4))


@dataclass
class Side:
    name: str = ""
    joined: bool = False
    answers: list[dict] = field(default_factory=list)   # {"a": id, "b": id, "chose": id|None}
    tally: dict[str, int] = field(default_factory=dict)
    profile: list[str] = field(default_factory=list)     # audience ids
    favorite: dict | None = None                        # {"id","name"} optional
    ratings: dict[int, float] = field(default_factory=dict)  # round index -> stars


@dataclass
class Card:
    round: int
    domain: str
    entity: dict
    raw: dict[str, float]
    curved: dict[str, float]
    common: float
    divide: float


@dataclass
class Room:
    code: str
    created: float = field(default_factory=time.time)
    phase: str = "lobby"           # lobby | quiz | dealing | rating | done
    sides: dict[str, Side] = field(default_factory=lambda: {"host": Side(), "guest": Side()})
    cards: list[Card] = field(default_factory=list)
    current: int = -1
    excluded: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---- quiz -----------------------------------------------------------
    def next_question(self, who: str) -> dict | None:
        """Adaptive forced choice. Option A follows the data (the segment most related to what
        this person has been choosing); option B is a wildcard from a family not yet explored."""
        _load_sim()
        s = self.sides[who]
        if len(s.answers) >= QUIZ_ROUNDS:
            return None
        seen = {x for a in s.answers for x in (a["a"], a["b"])}
        all_ids = [a["id"] for a in AUDIENCES if quizable(a["id"])]
        leaders = [k for k, _ in sorted(s.tally.items(), key=lambda kv: -kv[1])]
        # A: related to the leaders, not yet shown
        a_id = None
        for lead in leaders:
            for cand in _SIM.get(lead, {}):
                if cand not in seen and cand != lead and quizable(cand):
                    a_id = cand
                    break
            if a_id:
                break
        if not a_id:
            pool = [x for x in all_ids if x not in seen] or all_ids
            a_id = random.choice(pool)
        # B: wildcard from an unexplored family, never the same family as A
        fams_seen = {family(x) for x in seen} | {family(a_id)}
        wild = [x for x in all_ids if x not in seen and family(x) not in fams_seen]
        if not wild:
            wild = [x for x in all_ids if x not in seen and x != a_id] or [x for x in all_ids if x != a_id]
        b_id = random.choice(wild)
        pair = [a_id, b_id]
        random.shuffle(pair)  # don't always put the data pick on the same side
        return {"n": len(s.answers) + 1, "of": QUIZ_ROUNDS,
                "a": {"text": label(pair[0]), "id": pair[0]}, "b": {"text": label(pair[1]), "id": pair[1]}}

    def answer(self, who: str, a: str, b: str, chose: str | None) -> None:
        s = self.sides[who]
        s.answers.append({"a": a, "b": b, "chose": chose})
        if chose:
            s.tally[chose] = s.tally.get(chose, 0) + 1
        if len(s.answers) >= QUIZ_ROUNDS:
            _load_sim()
            s.profile = [k for k, _ in sorted(s.tally.items(), key=lambda kv: -kv[1])[:PROFILE_SIZE]]
            if not s.profile:  # all "none": a neutral profile
                s.profile = [A + "leisure:cinephile", A + "leisure:foodie"]

    def both_profiled(self) -> bool:
        return all(len(s.answers) >= QUIZ_ROUNDS and s.joined for s in self.sides.values())

    # ---- dealing ----------------------------------------------------------
    def groups(self) -> list[Group]:
        out = []
        for who, s in self.sides.items():
            g = Group(s.name or who, audiences=list(s.profile))
            if s.favorite and s.favorite.get("id"):
                g.entities = [s.favorite["id"]]
            out.append(g)
        return out

    def deal(self, q: Qloo) -> Card | None:
        """Deal the next round's card: best shared candidate not yet dealt."""
        rnd = self.current + 1
        if rnd >= len(ROUND_DOMAINS):
            return None
        domain = ROUND_DOMAINS[rnd]
        dealt = {c.entity["id"] for c in self.cards}
        scored = score_groups(q, self.groups(), domain, exclude=set(self.excluded) | dealt, pool_per_group=20, pool_combined=30)
        # the card should be something both would recognise: well known first, then the best shared score
        strong = [s for s in scored if s.entity.get("popularity", 0) >= 0.97 and s.common >= 0.8]
        ok = [s for s in scored if s.entity.get("popularity", 0) >= 0.93]
        pick = (strong or ok or scored or [None])[0]
        if not pick:
            self.log.append(f"round {rnd}: nothing to deal for {domain}")
            return None
        card = Card(rnd, domain, pick.entity, pick.raw, pick.curved, pick.common, pick.divide)
        self.cards.append(card)
        self.current = rnd
        self.phase = "rating"
        self.log.append(f"round {rnd}: dealt {pick.entity['name']} ({domain.split(':')[-1]}) common={pick.common}")
        return card

    def rate(self, who: str, stars: float) -> None:
        self.sides[who].ratings[self.current] = max(0.5, min(5.0, round(stars * 2) / 2))

    def both_rated(self) -> bool:
        return all(self.current in s.ratings for s in self.sides.values())

    def after_rating(self) -> None:
        """Rejected by either side: exclude; both low: exclude. Then next round or finish."""
        h, g = (self.sides["host"].ratings[self.current], self.sides["guest"].ratings[self.current])
        card = self.cards[self.current]
        if min(h, g) <= 2.0:
            self.excluded.append(card.entity["id"])
        self.phase = "dealing" if self.current + 1 < len(ROUND_DOMAINS) else "done"

    # ---- score --------------------------------------------------------------
    def score(self) -> dict:
        pairs = [(self.sides["host"].ratings.get(i), self.sides["guest"].ratings.get(i)) for i in range(len(self.cards))]
        pairs = [(h, g) for h, g in pairs if h is not None and g is not None]
        if not pairs:
            return {"score": 0, "title": TITLES[-1][1], "rounds": 0}
        # agreement (close ratings) and warmth (both high) both count
        agree = sum(1 - abs(h - g) / 4.5 for h, g in pairs) / len(pairs)
        warm = sum(min(h, g) / 5 for h, g in pairs) / len(pairs)
        score = round(100 * (0.5 * agree + 0.5 * warm))
        title = next(t for m, t in TITLES if score >= m)
        liked = [self.cards[i].entity["name"] for i, (h, g) in enumerate(pairs) if min(h, g) >= 4]
        return {"score": score, "title": title, "rounds": len(pairs), "both_liked": liked,
                "agree": round(agree, 2), "warm": round(warm, 2)}

    def public(self, who: str | None = None) -> dict:
        cur = asdict(self.cards[self.current]) if 0 <= self.current < len(self.cards) else None
        sides = {k: {"name": s.name, "joined": s.joined, "answered": len(s.answers), "profile": s.profile,
                     "profile_labels": [label(x) for x in s.profile],
                     "favorite": s.favorite, "rated_current": self.current in s.ratings}
                 for k, s in self.sides.items()}
        return {"code": self.code, "phase": self.phase, "sides": sides, "current": self.current, "card": cur,
                "rounds": len(ROUND_DOMAINS), "cards": [{"name": c.entity["name"], "domain": c.domain, "round": c.round,
                                                        "ratings": {k: s.ratings.get(c.round) for k, s in self.sides.items()}} for c in self.cards],
                "score": self.score() if self.cards else None, "log": self.log[-6:], "result": self.result}


class Games:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self.lock = threading.Lock()

    def create(self, host_name: str) -> Room:
        with self.lock:
            code = _code()
            while code in self.rooms:
                code = _code()
            r = Room(code)
            r.sides["host"].name = host_name
            r.sides["host"].joined = True
            r.phase = "quiz"
            self.rooms[code] = r
            return r

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code.upper())

    def join(self, code: str, guest_name: str) -> Room | None:
        r = self.get(code)
        if not r or r.sides["guest"].joined:
            return None
        r.sides["guest"].name = guest_name
        r.sides["guest"].joined = True
        return r

    def sweep(self, max_age: float = 6 * 3600) -> None:
        now = time.time()
        with self.lock:
            for k in [k for k, r in self.rooms.items() if now - r.created > max_age]:
                del self.rooms[k]
