"""The four-question warm-up: "which is more you?"

Every answer is a Qloo audience segment. The questions adapt: option A follows the data
(the segment most related to what this person has already chosen, from a one-time
similarity map of Qloo audiences), option B is a wildcard from a family they have not
touched. Four taps give the agent a starting audience profile before any card is dealt.
"""
from __future__ import annotations

import json
import pathlib
import random
from dataclasses import dataclass, field

from .qloo import Qloo

A = "urn:audience:"
ROUNDS = 3
PROFILE_SIZE = 3

# Friendlier wording for some segments.
NICE = {
    "american_football": "Football Sundays", "avid_reader": "A quiet novel", "cinephile": "Movies, lots of them",
    "news_junkie": "The news", "political_junkie": "Politics", "foodie": "A great meal out", "coffee": "Good coffee",
    "musician": "Playing music", "arts_crafts": "Making things by hand", "home_decor": "Home projects",
    "wine_enthusiast": "Wine", "spirituality": "Spirituality", "healthy_eating": "Eating healthy",
    "parents_with_young_children": "Kids at home", "retirement": "Retired life", "single": "Single life",
    "engaged": "Getting married", "politically_progressive": "Lean progressive",
    "politically_conservative": "Lean conservative", "politically_center": "Right in the middle",
    "discount_shoppers": "Discount hunting", "gourmand_fine_dining": "Fine dining", "technology_enthusiast": "New gadgets",
    "vintage_apparel": "Vintage finds", "video_gamer": "Video games", "secret_unravelers": "A good mystery",
    "casual_escapists": "Switching off with a show", "adrenaline_rushers": "An adrenaline rush", "spy_enthusiast": "Spy stories",
}

# Identity is not an interest, and a bar is not a debate. These never appear as "which is more you" choices.
EXCLUDE_FAMILIES = {"communities", "professional_area", "investing_interests", "global_issues", "political_preferences"}
EXCLUDE_IDS = {A + "lifestyle_preferences_beliefs:christianity", A + "lifestyle_preferences_beliefs:judaism",
               A + "lifestyle_preferences_beliefs:islam", A + "leisure:political_junkie", A + "leisure:news_junkie"}

# The house: strangers with real Qloo profiles for the demo. Q-Lou seats you with one; they pick
# their cards and say yes on a human clock (see rooms.HOUSE_LOCK_PAUSE).
HOUSE = [
    {"name": "Rosa", "profile": [A + "leisure:coffee", A + "leisure:music_festivals", A + "global_issues:climate_activism"]},
    {"name": "Hank", "profile": [A + "hobbies_and_interests:fishing", A + "hobbies_and_interests:automotive", A + "spending_habits:discount_shoppers"]},
    {"name": "Dee", "profile": [A + "hobbies_and_interests:running", A + "leisure:avid_reader", A + "life_stage:parents_with_young_children"]},
    {"name": "Marcus", "profile": [A + "hobbies_and_interests:video_gamer", A + "leisure:foodie", A + "hobbies_and_interests:basketball"]},
    {"name": "June", "profile": [A + "life_stage:retirement", A + "hobbies_and_interests:travel", A + "leisure:museums"]},
    {"name": "Tasha", "profile": [A + "leisure:foodie", A + "hobbies_and_interests:yoga", A + "leisure:cinephile"]},
    {"name": "Luis", "profile": [A + "hobbies_and_interests:soccer", A + "leisure:music_festivals", A + "life_stage:parents_with_young_children"]},
]

_SIM: dict[str, dict[str, float]] = {}
_AUDIENCES: list[dict] = []


def _load() -> None:
    """Audience list and similarity map, from the checked-in file or, failing that, from Qloo."""
    global _SIM, _AUDIENCES
    if _AUDIENCES:
        return
    f = pathlib.Path(__file__).resolve().parent / "audience_sim.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        _SIM, _AUDIENCES = d.get("sim", {}), d.get("audiences", [])
    else:
        _AUDIENCES = Qloo().all_audiences()


def audiences() -> list[dict]:
    _load()
    return _AUDIENCES


def label(aud_id: str) -> str:
    key = aud_id.split(":")[-1]
    if key in NICE:
        return NICE[key]
    for a in audiences():
        if a["id"] == aud_id:
            return a["name"]
    return key.replace("_", " ").title()


def family(aud_id: str) -> str:
    return aud_id.split(":")[-2]


def quizable(aud_id: str) -> bool:
    return family(aud_id) not in EXCLUDE_FAMILIES and aud_id not in EXCLUDE_IDS


@dataclass
class Quiz:
    """One person's answers so far."""
    answers: list[dict] = field(default_factory=list)   # {"a": id, "b": id, "chose": id | None}
    tally: dict[str, int] = field(default_factory=dict)
    pending: dict | None = None

    @property
    def done(self) -> bool:
        return len(self.answers) >= ROUNDS

    @property
    def profile(self) -> list[str]:
        return [k for k, _ in sorted(self.tally.items(), key=lambda kv: -kv[1])[:PROFILE_SIZE]]

    def next_question(self, rng: random.Random | None = None) -> dict | None:
        _load()
        rng = rng or random
        if self.done:
            return None
        if self.pending:
            return self.pending
        seen = {x for a in self.answers for x in (a["a"], a["b"])}
        all_ids = [a["id"] for a in _AUDIENCES if quizable(a["id"])]
        leaders = [k for k, _ in sorted(self.tally.items(), key=lambda kv: -kv[1])]
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
            a_id = rng.choice(pool)
        fams = {family(x) for x in seen} | {family(a_id)}
        wild = [x for x in all_ids if x not in seen and family(x) not in fams] or [x for x in all_ids if x != a_id]
        pair = [a_id, rng.choice(wild)]
        rng.shuffle(pair)
        self.pending = {"n": len(self.answers) + 1, "of": ROUNDS,
                        "a": {"text": label(pair[0]), "id": pair[0]}, "b": {"text": label(pair[1]), "id": pair[1]}}
        return self.pending

    def answer(self, a: str, b: str, chose: str | None) -> bool:
        """Record an answer. A stale answer (from a screen that already moved on) is ignored."""
        if self.pending and {self.pending["a"]["id"], self.pending["b"]["id"]} != {a, b}:
            return False
        self.pending = None
        self.answers.append({"a": a, "b": b, "chose": chose})
        if chose:
            self.tally[chose] = self.tally.get(chose, 0) + 1
        return True

    @classmethod
    def preset(cls, profile: list[str]) -> "Quiz":
        """A finished quiz with a fixed profile (the house)."""
        q = cls()
        for x in profile[:ROUNDS]:
            q.answers.append({"a": x, "b": x, "chose": x})
            q.tally[x] = q.tally.get(x, 0) + 1
        while len(q.answers) < ROUNDS:
            q.answers.append({"a": "", "b": "", "chose": None})
        return q
