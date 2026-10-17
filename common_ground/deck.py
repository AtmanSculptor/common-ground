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


def draw_one(seen: set[int], like: int, rng: random.Random | None = None) -> int | None:
    """One fresh card to swap in for 'like': from the same half of the deck (music for music,
    life for life) so the hand keeps its mix, never one this side has already seen."""
    rng = rng or random
    music = card(like)["cat"] in MUSIC_CATS
    pool = [c["id"] for c in cards() if (c["cat"] in MUSIC_CATS) == music and c["id"] not in seen]
    return rng.choice(pool) if pool else None


def meter_words(pairs: list[dict]) -> str:
    """What the meter means, in words a stranger gets at a glance: which pairs were the same
    card, which were close (same kind of card), which were different."""
    same = [p["a"]["name"] for p in pairs if p["match"] == "exact"]
    close = [f'{p["a"]["name"]} and {p["b"]["name"]}' for p in pairs if p["match"] == "kin"]
    apart = [f'{p["a"]["name"]} and {p["b"]["name"]}' for p in pairs if p["match"] == "none"]
    out = []
    if same:
        out.append(("Same card: " if len(same) == 1 else "Same cards: ") + ", ".join(same) + ".")
    if close:
        out.append("Close: " + ", ".join(close) + ".")
    if apart:
        out.append("Different: " + ", ".join(apart) + ".")
    return " ".join(out)


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
# Templates are written so {a} and {b} can be a name or "You" (past tense, no third-person verbs).
LINES_SAME = ["Both of you played {card}. That is not a coincidence.",
              "Same card, both hands: {card}.",
              "{card}, from both of you. Q-Lou noticed."]
LINES_BY_CAT = {
    ("music", "music"): ["{a} led with {ca}. {b} answered with {cb}. Q-Lou hears a bridge.",
                         "{ca} meets {cb}. Somewhere in the middle there is a song.",
                         "{ca} and {cb} in the same room. That is a setlist."],
    ("life", "life"): ["{a}: {ca}. {b}: {cb}. Different mornings, same bar.",
                       "{ca} and {cb}. Two ways of spending a day.",
                       "{ca} here, {cb} there. The week has room for both."],
    ("table", "table"): ["{ca} for {a}, {cb} for {b}. This round is on the house.",
                         "{ca} and {cb}. Same table, two orders."],
    ("heart", "heart"): ["{ca} and {cb}. Two homes, one jukebox.",
                         "{ca} on one side, {cb} on the other. Q-Lou hears a chorus."],
    ("place", "place"): ["{ca} meets {cb}. Every town has a bar like this one.",
                         "{ca} and {cb}. Two maps, one corner."],
}
LINES_MIX = ["{a} played {ca}. {b} played {cb}. Q-Lou is taking notes.",
             "{ca} on one side, {cb} on the other. The meter moved.",
             "{a}: {ca}. {b}: {cb}. Keep going.",
             "{ca} and {cb}. Q-Lou has a song for that.",
             "{ca} meets {cb}. Unlikely pair, likely song."]


def line_for(a_name: str, ca: dict, b_name: str, cb: dict, rng: random.Random | None = None,
             avoid: set[str] | None = None) -> str:
    """One line for a pair. 'avoid' holds templates already used in this reveal, so two pairs on
    one table never say the same thing; the chosen template is added to it."""
    rng = rng or random
    if ca["id"] == cb["id"]:
        opts = LINES_SAME
    else:
        key = (ca["cat"], cb["cat"])
        opts = LINES_BY_CAT.get(key) or LINES_BY_CAT.get((key[1], key[0])) or LINES_MIX
    fresh = [t for t in opts if avoid is None or t not in avoid] or list(opts)
    t = rng.choice(fresh)
    if avoid is not None:
        avoid.add(t)
    return t.format(card=ca["name"], a=a_name, b=b_name, ca=ca["name"], cb=cb["name"])


def lines_for(host_name: str, ca: dict, guest_name: str, cb: dict, rng: random.Random | None = None,
              avoid: set[str] | None = None) -> dict[str, str]:
    """The same line twice: once for the host's phone (host is 'You'), once for the guest's."""
    rng = rng or random
    seed = rng.random()
    used = set(avoid) if avoid is not None else None
    host = line_for("You", ca, guest_name, cb, random.Random(seed), used)
    guest = line_for(host_name, ca, "You", cb, random.Random(seed), set(avoid) if avoid is not None else None)
    if avoid is not None and used is not None:
        avoid |= used
    return {"host": host, "guest": guest}


def pair_plays(host_cards: list[int], guest_cards: list[int]) -> list[tuple[int, int]]:
    """Face the most alike cards: exact matches first, then same category, then what is left."""
    h, g = list(host_cards), list(guest_cards)
    pairs: list[tuple[int, int]] = []
    for want in ("exact", "kin"):
        for hc in list(h):
            for gc in list(g):
                if match_kind(card(hc), card(gc)) == want:
                    pairs.append((hc, gc)); h.remove(hc); g.remove(gc)
                    break
    pairs += list(zip(h, g))
    return pairs


def match_kind(ca: dict, cb: dict) -> str:
    if ca["id"] == cb["id"]:
        return "exact"
    if ca["cat"] == cb["cat"]:
        return "kin"
    return "none"


METER_GAIN = {"exact": 25, "kin": 10, "none": 3}
