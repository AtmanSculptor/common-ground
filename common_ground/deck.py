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
HAND = 5
MUSIC_IN_HAND = 3    # of five: the song is the point; the rest is flavor
PLAYS = 3                # on the table, ranked: Most me, Also me, A bit me
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


def meter_words(pairs: list[dict], both: list[str] | None = None) -> str:
    """What the meter means, in words a stranger gets at a glance: which cards you both played,
    which pairs were close (same kind of card), which were different. 'both' lists the cards
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


def match_kind(ca: dict, cb: dict) -> str:
    if ca["id"] == cb["id"]:
        return "exact"
    if ca["cat"] == cb["cat"]:
        return "kin"
    return "none"

