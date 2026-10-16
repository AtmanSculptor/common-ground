import json
import random

from common_ground import deck


def test_every_card_has_a_verified_signal_and_art():
    cards = deck.cards()
    assert len(cards) == 45
    ids = [c["id"] for c in cards]
    assert len(ids) == len(set(ids))
    for c in cards:
        sig = c["signal"]
        assert sig.get("tags") or sig.get("audiences"), c["name"]
        for t in sig.get("tags", []):
            assert t.startswith("urn:tag:"), t
        for a in sig.get("audiences", []):
            assert a.startswith("urn:audience:"), a
        assert c["img"].startswith("static/deck/") and c["img"].endswith(".jpg")
        assert c["cat"] in {"music", "life", "table", "place", "heart", "book", "game"}


def test_dead_tags_are_not_in_the_deck():
    """These spellings return Qloo's default list (no signal). Found by probe, see probes/."""
    dead = {"urn:tag:genre:music:latin", "urn:tag:genre:music:latin_pop", "urn:tag:genre:music:r_b",
            "urn:tag:genre:music:rnb", "urn:tag:genre:music:latino", "urn:tag:genre:music:synthpop"}
    for c in deck.cards():
        assert not dead & set(c["signal"].get("tags", [])), c["name"]


def test_deal_is_three_music_three_other_and_never_repeats():
    rng = random.Random(1)
    seen: set[int] = set()
    for _ in range(3):
        hand = deck.deal(seen, rng)
        assert len(hand) == 6 and len(set(hand)) == 6
        assert sum(1 for c in hand if deck.card(c)["cat"] == "music") == 3
        assert not seen & set(hand)
        seen |= set(hand)


def test_signal_merges_cards_and_profile_without_duplicates():
    country = next(c["id"] for c in deck.cards() if c["name"] == "Country roads")
    small_town = next(c["id"] for c in deck.cards() if c["name"] == "Small town")
    s = deck.signal([country, country, small_town], ["urn:audience:leisure:coffee", "urn:audience:leisure:coffee"])
    assert s["tags"].count("urn:tag:genre:music:country") == 1
    assert "urn:tag:genre:music:americana" in s["tags"]
    assert s["audiences"] == ["urn:audience:leisure:coffee"]


def test_lines_and_matches():
    a = next(c for c in deck.cards() if c["name"] == "Country roads")
    b = next(c for c in deck.cards() if c["name"] == "Jazz hands")
    c = next(c for c in deck.cards() if c["name"] == "Whiskey")
    assert deck.match_kind(a, a) == "exact" and deck.match_kind(a, b) == "kin" and deck.match_kind(a, c) == "none"
    line = deck.line_for("Brent", a, "Hank", b, random.Random(0))
    assert "Country roads" in line and "Jazz hands" in line and "—" not in line
    assert "Country roads" in deck.line_for("x", a, "y", a, random.Random(0))
