"""The deck: 45 taste cards, each tied to a verified Qloo signal.

A card is a thing you can be ("Night owl", "Country roads", "Whiskey"). Playing it is a
statement. Behind each card is a Qloo tag or audience that returns real artists, checked
one by one against the live API (see probes/). Cards whose signals returned noise were
parked, not faked.

Dealing: six cards, three music and three life, nothing this person has seen in this room.
"""
from __future__ import annotations

import json
import pathlib
import random
from functools import lru_cache

STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"
HAND = 6
MUSIC_IN_HAND = 3
PLAYS = 2
MUSIC_CATS = {"music"}


@lru_cache(maxsize=1)
def cards() -> tuple[dict, ...]:
    return tuple(json.loads((STATIC / "cards.json").read_text(encoding="utf-8")))


@lru_cache(maxsize=None)
def card(cid: int) -> dict:
    for c in cards():
        if c["id"] == cid:
            return c
    raise KeyError(f"no card {cid}")


def public(c: dict) -> dict:
    """What the phone needs: everything but the Qloo signal."""
    return {k: v for k, v in c.items() if k != "signal"}


def deal(seen: set[int], rng: random.Random | None = None) -> list[int]:
    rng = rng or random
    music = [c["id"] for c in cards() if c["cat"] in MUSIC_CATS and c["id"] not in seen]
    other = [c["id"] for c in cards() if c["cat"] not in MUSIC_CATS and c["id"] not in seen]
    hand = rng.sample(music, min(MUSIC_IN_HAND, len(music))) + rng.sample(other, min(HAND - MUSIC_IN_HAND, len(other)))
    rng.shuffle(hand)
    return hand


def signal(card_ids: list[int], audiences: list[str] = ()) -> dict[str, list[str]]:
    """Merge the Qloo signals behind a set of cards (plus a quiz profile) into one query signal."""
    tags: list[str] = []
    auds: list[str] = list(audiences)
    for cid in card_ids:
        s = card(cid)["signal"]
        tags += s.get("tags", [])
        auds += s.get("audiences", [])
    out: dict[str, list[str]] = {}
    if tags:
        out["tags"] = list(dict.fromkeys(tags))
    if auds:
        out["audiences"] = list(dict.fromkeys(auds))
    return out


# ---- the line that lands with a pair of cards -------------------------------------------
LINES_SAME = ["You both played {card}. That is not a coincidence.",
              "Same card, both hands: {card}.",
              "{card}, from both of you. Q-Lou noticed."]
LINES_BY_CAT = {
    ("music", "music"): ["{a} leads with {ca}. {b} answers with {cb}. Q-Lou hears a bridge.",
                         "{ca} meets {cb}. Somewhere in the middle there is a song."],
    ("life", "life"): ["{a} is {ca}. {b} is {cb}. Different mornings, same bar.",
                       "{ca} and {cb}. Two ways of spending a day."],
    ("table", "table"): ["{ca} for {a}, {cb} for {b}. This round is on the house."],
    ("heart", "heart"): ["{ca} and {cb}. Two homes, one jukebox."],
    ("place", "place"): ["{ca} meets {cb}. Every town has a bar like this one."],
}
LINES_MIX = ["{a} played {ca}. {b} played {cb}. Q-Lou is taking notes.",
             "{ca} on one side, {cb} on the other. The meter moved.",
             "{a}: {ca}. {b}: {cb}. Keep going."]


def line_for(a_name: str, ca: dict, b_name: str, cb: dict, rng: random.Random | None = None) -> str:
    rng = rng or random
    if ca["id"] == cb["id"]:
        return rng.choice(LINES_SAME).format(card=ca["name"])
    key = (ca["cat"], cb["cat"])
    opts = LINES_BY_CAT.get(key) or LINES_BY_CAT.get((key[1], key[0])) or LINES_MIX
    return rng.choice(opts).format(a=a_name, b=b_name, ca=ca["name"], cb=cb["name"])


def match_kind(ca: dict, cb: dict) -> str:
    if ca["id"] == cb["id"]:
        return "exact"
    if ca["cat"] == cb["cat"]:
        return "kin"
    return "none"


METER_GAIN = {"exact": 25, "kin": 10, "none": 3}
