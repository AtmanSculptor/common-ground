"""The deck: 101 taste cards in four suits, each tied to a verified Qloo signal.

A card is a thing you can be ("Night owl", "Country roads", "Whiskey"). Playing it is a
statement. Behind each card is a Qloo tag or audience that returns real artists, checked
one by one against the live API (see probes/ and docs/art/). Cards whose signals returned
noise were parked, not faked.

Suits: Stage (what you turn up), Bar (what you order and where you sit), Road (what you do
with a Saturday), Porch (who you are at home). Dealing: six cards, one of each suit and two
more from anywhere, nothing this person has seen in this room.

The joker (On the house, id 111) is retired: it stays in cards.json for the art and the old
rooms, but cards() leaves it out, so it is never dealt, scored or shown.
"""
from __future__ import annotations

import json
import pathlib
import random
from functools import lru_cache

STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"
SUITS = ("stage", "bar", "road", "porch")
EXTRA = 2                # beyond one of each suit
HAND = len(SUITS) + EXTRA
PLAYS = 3                # on the table, ranked: Most me, Also me, A bit me
JOKER = "joker"          # On the house: no taste signal; played, the song is free if both say yes
JOKER_CHANCE = 0.0       # retired: the joker is never dealt (see the module note)
MUSIC_CATS = {"music"}   # kept for older callers; suits do the dealing now


@lru_cache(maxsize=1)
def cards() -> tuple[dict, ...]:
    """The playable deck: everything in cards.json but the joker."""
    return tuple(c for c in json.loads((STATIC / "cards.json").read_text(encoding="utf-8")) if c.get("suit") != JOKER)


@lru_cache(maxsize=None)
def card(cid: int) -> dict:
    for c in cards():
        if c["id"] == cid:
            return c
    for c in json.loads((STATIC / "cards.json").read_text(encoding="utf-8")):   # a retired card in an old room
        if c["id"] == cid:
            return c
    raise KeyError(f"no card {cid}")


def public(c: dict) -> dict:
    """What the phone needs: everything but the raw signal, plus the signal's ids as a flat list
    so a screen that wants to show its work (the laptop frame) can name the Qloo tag or audience
    behind a card."""
    out = {k: v for k, v in c.items() if k != "signal"}
    sig = c.get("signal") or {}
    out["sig"] = list(sig.get("tags", [])) + list(sig.get("audiences", []))
    return out


def deal(seen: set[int], rng: random.Random | None = None, joker_chance: float | None = None) -> list[int]:
    """One card from each suit, then two more from any suit, never one this side has seen, shuffled;
    now and then the joker takes one of the two extra seats."""
    rng = rng or random
    if joker_chance is None:
        joker_chance = JOKER_CHANCE
    hand = []
    for suit in SUITS:
        pool = [c["id"] for c in cards() if c["suit"] == suit and c["id"] not in seen]
        if pool:
            hand.append(rng.choice(pool))
    extra = EXTRA
    joker = next((c["id"] for c in cards() if c["suit"] == JOKER), None)
    if joker is not None and joker not in seen and rng.random() < joker_chance:
        hand.append(joker)
        extra -= 1
    pool = [c["id"] for c in cards() if c["suit"] != JOKER and c["id"] not in seen and c["id"] not in hand]
    hand += rng.sample(pool, min(extra, len(pool)))
    rng.shuffle(hand)
    return hand


def draw_one(seen: set[int], like: int, rng: random.Random | None = None) -> int | None:
    """One fresh card to swap in for 'like', from the same suit, so the hand keeps one of each;
    never one this side has already seen. The joker is not replaced."""
    rng = rng or random
    suit = card(like)["suit"]
    if suit == JOKER:
        return None
    pool = [c["id"] for c in cards() if c["suit"] == suit and c["id"] not in seen]
    return rng.choice(pool) if pool else None


def meter_words(pairs: list[dict], both: list[str] | None = None) -> str:
    """What the meter means, in words a stranger gets at a glance: which cards you both played,
    which pairs were close (same suit), which were different. 'both' lists the cards
    played by both sides whatever their rank; a rank pair that is the same card is skipped here."""
    same = list(both) if both is not None else [p["a"]["name"] for p in pairs if p["match"] == "exact"]
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
    """Merge the Qloo signals behind a set of cards (plus a house profile) into one query signal.
    The joker carries none."""
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


def match_kind(ca: dict, cb: dict) -> str:
    """The same card; the same suit ('close'); or not. The joker is close to nothing."""
    if ca["id"] == cb["id"]:
        return "exact"
    if ca["suit"] == cb["suit"] and ca["suit"] != JOKER:
        return "kin"
    return "none"
