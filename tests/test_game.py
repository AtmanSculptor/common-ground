"""The whole game through the HTTP API, with a fake Qloo and no network."""
import random
import time

import pytest

from common_ground import deck
from fastapi.testclient import TestClient


def _st(client, code, who="host"):
    return client.get(f"/api/game/{code}", params={"who": who}).json()


def _hand(client, code, who):
    return [c["id"] for c in _st(client, code, who)["sides"][who]["hand"]]


def _wait_pick(client, code):
    for _ in range(100):
        st = _st(client, code)
        if st["phase"] != "thinking":
            return st
        time.sleep(0.05)
    raise AssertionError("Q-Lou never finished")


def _play(client, code, who, card):
    return client.post(f"/api/game/{code}/play", json={"who": who, "card": card})


def _to_jukebox(client, code, toss_host=1, toss_guest=0):
    """Both toss, both play three rounds. Returns the jukebox state."""
    h = _hand(client, code, "host")
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": h[:toss_host]}).json()["ok"]
    g = _hand(client, code, "guest")
    assert client.post(f"/api/game/{code}/toss", json={"who": "guest", "cards": g[:toss_guest]}).json()["ok"]
    assert _st(client, code)["phase"] == "play"
    h, g = _hand(client, code, "host"), _hand(client, code, "guest")
    for hc, gc in zip(h[:3], [g[1], g[0], g[2]]):
        assert _play(client, code, "host", hc).json()["ok"]
        assert _play(client, code, "guest", gc).json()["ok"]
    assert _st(client, code)["phase"] in ("thinking", "jukebox")
    return _wait_pick(client, code)


def test_two_phones_full_game(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    st = _st(client, code)
    assert st["phase"] == "lobby" and st["sides"]["guest"]["joined"] is False and st["rounds"] == ["deal", "r1", "r2", "r3"] and st["tries"] == 3
    assert st["rank_names"] == ["Most me", "Also me", "A bit me"] and st["rank"] == 0 and st["pairs"] == []
    assert client.post(f"/api/game/{code}/join", json={"name": "Josie"}).json()["who"] == "guest"
    assert client.post(f"/api/game/{code}/join", json={"name": "Third"}).status_code == 404
    # the deal: toss
    st = _st(client, code)
    assert st["phase"] == "toss" and st["sides"]["host"]["tokens"] == 10
    hand = [c["id"] for c in st["sides"]["host"]["hand"]]
    assert len(hand) == 4 and "hand" not in st["sides"]["guest"]
    assert st["toss_max"] == 4 and st["suits"] == ["stage", "bar", "road", "porch"]
    hand = [c for c in hand if deck.card(c)["suit"] != deck.JOKER]
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand[:2] + [999]}).status_code == 400   # not in the hand
    assert _play(client, code, "host", hand[0]).status_code == 400                                              # not yet
    out = client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand[:2]}).json()
    new = [c["id"] for c in out["hand"]]
    assert out["phase"] == "toss" and not set(new) & set(hand[:2]) and set(hand[2:]) <= set(new)
    assert sorted(deck.card(c)["suit"] for c in new if deck.card(c)["suit"] != deck.JOKER) == sorted(deck.card(c)["suit"] for c in hand)
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": []}).status_code == 400     # once
    st = _st(client, code, "guest")
    assert st["sides"]["host"]["locked"] and st["sides"]["host"]["tossed_n"] == 2 and not st["sides"]["guest"]["locked"]
    ghand = [c["id"] for c in st["sides"]["guest"]["hand"]]
    assert client.post(f"/api/game/{code}/toss", json={"who": "guest", "cards": []}).json()["phase"] == "play"
    st = _st(client, code)
    assert st["phase"] == "play" and st["toss_summary"]["host"] and st["toss_summary"]["guest"] == []
    assert st["sides"]["host"]["tossed"] == hand[:2] and not st["sides"]["host"]["locked"]
    # three rounds: one card each, the pair flips when both are down
    hand = [c["id"] for c in st["sides"]["host"]["hand"]]
    assert _play(client, code, "host", 999).status_code == 400                      # not in the hand
    out = _play(client, code, "host", hand[0]).json()
    assert out["ok"] and out["rank"] == 0 and out["pairs"] == []                     # down, waiting on Josie
    assert _play(client, code, "host", hand[1]).status_code == 400                  # one card per round
    st = _st(client, code, "guest")
    assert st["phase"] == "play" and st["sides"]["host"]["down"] and st["sides"]["host"]["placed"] == 1 and not st["sides"]["guest"]["down"]
    assert "plays" not in st["sides"]["host"]                                       # face-down to the partner until the flip
    out = _play(client, code, "guest", ghand[1]).json()
    assert out["rank"] == 1 and len(out["pairs"]) == 1 and out["phase"] == "play"   # the first pair flipped
    st = _st(client, code)
    p1 = st["pairs"][0]
    assert p1["rank"] == 1 and p1["a"]["id"] == hand[0] and p1["b"]["id"] == ghand[1] and p1["match"] in {"exact", "kin", "none"}
    assert not st["sides"]["host"]["down"] and not st["sides"]["guest"]["down"]     # round two is open
    assert _play(client, code, "host", hand[0]).status_code == 400                  # already played
    assert _play(client, code, "guest", ghand[0]).json()["ok"]
    assert _play(client, code, "host", hand[1]).json()["rank"] == 2
    assert _play(client, code, "guest", ghand[2]).json()["ok"]
    out = _play(client, code, "host", hand[2]).json()
    assert out["rank"] == 3 and out["phase"] == "thinking"                          # the third flip: nobody presses anything
    st = _st(client, code)
    rv = st["reveal"]
    assert [p["rank"] for p in rv["pairs"]] == [1, 2, 3] and st["meter"] > 0 and len(st["pairs"]) == 3
    assert rv["words"] and all(p["match"] in {"exact", "kin", "none"} for p in rv["pairs"])
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox"
    pick = st["pick"]
    assert pick["artist"]["name"] and pick["tracks"] and pick["status"] == "open" and "votes" not in pick
    steps = [s["step"] for s in st["thinking"]]
    assert "tracks" in steps and steps[-1] in ("tracks", "baseline")
    # both yes: two tokens each
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    out = client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": True}).json()
    assert out["outcome"] == "play"
    st = _st(client, code)
    assert st["phase"] == "done" and st["result"]["pick"]["played"] is True and st["result"]["paid_by"] == "both"
    assert st["result"]["tokens"] == {"host": 8, "guest": 8} and st["sides"]["host"]["tokens"] == 8
    assert st["result"]["meter"] > 0 and st["result"]["title"] and st["result"]["words"]
    plays = st["result"]["plays"]
    assert set(st["result"]["both_played"]) == set(plays["Brent"]) & set(plays["Josie"])
    assert st["result"]["tossed"]["Brent"] and st["result"]["tossed"]["Josie"] == []
    assert client.get("/api/stats").json()["games"] == 1


def test_a_no_brings_the_next_pick_and_three_nos_mean_no_match(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    st = _to_jukebox(client, code)
    seen = [st["pick"]["artist"]["id"]]
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    out = client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": False}).json()
    assert out["outcome"] == "again"
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox" and st["pick"]["attempt"] == 2 and st["pick"]["artist"]["id"] not in seen
    assert st["offers"][0]["status"] == "declined" and st["offers"][0]["said_no"] == ["guest"]
    assert st["sides"]["host"]["tokens"] == 10                      # nobody paid
    seen.append(st["pick"]["artist"]["id"])
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": False})
    assert client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": True}).json()["outcome"] == "again"
    st = _wait_pick(client, code)
    assert st["pick"]["attempt"] == 3 and st["pick"]["artist"]["id"] not in seen
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    out = client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": False}).json()
    assert out["outcome"] == "nothing"
    st = _st(client, code)
    r = st["result"]
    assert st["phase"] == "done" and r["paid_by"] is None and r["pick"]["played"] is False and r["tokens"] == {"host": 10, "guest": 10}
    assert [o["status"] for o in r["offers"]] == ["declined", "declined", "declined"]
    assert client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}).status_code == 400


def test_no_tokens_counts_as_a_no(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    _to_jukebox(client, code)
    rooms_of(client)[code].sides["guest"].tokens = 1
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    assert client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": True}).json()["outcome"] == "again"


def test_the_venue_rotation_keeps_recent_picks_out(client, monkeypatch):
    from common_ground import stats
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    first = _to_jukebox(client, code)["pick"]["artist"]["id"]
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}); client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": True})
    assert stats.recent_ids(25) == [first]
    code2 = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code2}/join", json={"name": "Josie"})
    second = _to_jukebox(client, code2)["pick"]["artist"]["id"]
    assert second != first                                          # same fake Qloo, same hands would repeat; the rotation stops it


def rooms_of(client):
    import app as appmod
    return appmod.ROOMS.rooms


def test_solo_against_the_house(client, monkeypatch):
    from common_ground import rooms
    for name in ("HOUSE_TOSS_PAUSE", "HOUSE_LOCK_PAUSE", "HOUSE_VOTE_PAUSE"):
        monkeypatch.setattr(rooms, name, (0, 0))
    r = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()
    code = r["code"]
    assert r["house"]
    st = _st(client, code)
    assert st["phase"] == "toss" and st["sides"]["guest"]["locked"]          # the house tossed at once (no pause)
    hand = _hand(client, code, "host")
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand[:1]}).json()["phase"] == "play"
    st = _st(client, code)
    assert st["sides"]["guest"]["down"]                                       # and its first card is down at once
    hand = _hand(client, code, "host")
    for i in range(3):
        _play(client, code, "host", hand[i])
        st = _st(client, code)
        assert len(st["pairs"]) == i + 1                                      # the house's card was down; the pair flipped
    assert st["phase"] in ("thinking", "jukebox")
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox"
    out = client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}).json()
    assert out["outcome"] == "play"
    assert client.get("/api/stats").json()["solo"] == 1


def test_pages_and_health(client):
    for path in ("/", "/play", "/deck", "/stats", "/organize", "/screen/ABCD"):
        assert client.get(path).status_code == 200
    assert client.get("/").text == client.get("/play").text      # the game is the front door
    assert "Our Song" in client.get("/").text
    assert "Common Ground" in client.get("/organize").text         # the older organizer, moved aside
    h = client.get("/api/health").json()
    assert h["ok"] and "llm" in h
    assert client.get("/api/game/ZZZZ").status_code == 404


def test_the_house_moves_on_a_human_clock(client, monkeypatch):
    """The demo partner tosses, plays and votes a few seconds after the human, not instantly."""
    from common_ground import rooms
    for name in ("HOUSE_TOSS_PAUSE", "HOUSE_LOCK_PAUSE", "HOUSE_VOTE_PAUSE"):
        monkeypatch.setattr(rooms, name, (0.3, 0.3))
    code = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()["code"]
    st = _st(client, code)
    assert st["phase"] == "toss" and not st["sides"]["guest"]["locked"]      # the stranger is still looking at the hand
    client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": []})
    assert _st(client, code)["phase"] == "toss"
    time.sleep(0.4)
    assert _st(client, code)["phase"] == "play"                             # the poll let the house toss
    hand = _hand(client, code, "host")
    for i in range(3):
        _play(client, code, "host", hand[i])
        st = _st(client, code)
        assert len(st["pairs"]) == i                                          # the house is still thinking
        time.sleep(0.4)
        assert len(_st(client, code)["pairs"]) == i + 1                       # the poll let it put its card down
    assert _st(client, code)["phase"] in ("thinking", "jukebox")
    st = _wait_pick(client, code)
    out = client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}).json()
    assert out["outcome"] is None and _st(client, code)["phase"] == "jukebox"
    time.sleep(0.4)
    assert _st(client, code)["phase"] == "done"


def test_old_pickled_rooms_get_new_fields_and_v1_rooms_are_dropped(tmp_path):
    """A room saved before a Side field existed still loads; a room from version 1 is let go."""
    import pickle
    from common_ground import rooms
    rs = rooms.Rooms(tmp_path / "rooms.pkl", random.Random(1))
    r = rs.create("Brent", house=True)
    for side in r.sides.values():
        side.__dict__.pop("pending"); side.__dict__.pop("due")
    old = rooms.Room("OLDV"); old.__dict__.pop("offers")
    rs.rooms["OLDV"] = old
    (tmp_path / "rooms.pkl").write_bytes(pickle.dumps(rs.rooms))
    rs2 = rooms.Rooms(tmp_path / "rooms.pkl", random.Random(1))
    assert "OLDV" not in rs2.rooms
    g = rs2.rooms[r.code].sides["guest"]
    assert g.pending is None and g.due == 0.0


def test_tossing_three_keeps_the_suits(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    hand = [c for c in _hand(client, code, "host") if deck.card(c)["suit"] != deck.JOKER][:3]
    out = client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand}).json()
    new = [c["id"] for c in out["hand"]]
    assert len(new) == 4 and not set(new) & set(hand)
    assert set(deck.card(c)["suit"] for c in new if deck.card(c)["suit"] != deck.JOKER) == set(deck.SUITS)
    assert _st(client, code, "guest")["sides"]["host"]["tossed_n"] == 3


def test_swapping_all_four_keeps_one_of_each_suit_and_the_mark_rides_with_the_room(client):
    code = client.post("/api/game/new", json={"name": "Brent", "icon": 2}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie", "icon": 7})
    hand = _hand(client, code, "host")
    out = client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand}).json()
    new = [c["id"] for c in out["hand"]]
    assert len(new) == 4 and not set(new) & set(hand)
    assert sorted(deck.card(c)["suit"] for c in new) == sorted(deck.SUITS)
    st = _st(client, code, "guest")
    assert st["sides"]["host"]["icon"] == 2 and st["sides"]["guest"]["icon"] == 3     # 7 wraps to 3
    assert client.get("/api/profile?name=Nobody+Yet").json()["games"] == 0


def test_a_nudge_crosses_the_table_for_a_few_seconds(client, monkeypatch):
    from common_ground import rooms
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    assert client.post(f"/api/game/{code}/emote", json={"who": "host", "emote": "cheers"}).json()["ok"]
    assert not client.post(f"/api/game/{code}/emote", json={"who": "host", "emote": "fire"}).json()["ok"]   # too soon
    assert not client.post(f"/api/game/{code}/emote", json={"who": "guest", "emote": "rude"}).json()["ok"]   # not a nudge
    st = _st(client, code, "guest")
    assert st["sides"]["host"]["emote"]["name"] == "cheers" and st["sides"]["guest"]["emote"] is None
    monkeypatch.setattr(rooms, "EMOTE_TTL", 0.0)
    assert _st(client, code, "guest")["sides"]["host"]["emote"] is None


def test_the_reveal_comes_with_words_and_the_pull(client, monkeypatch):
    from common_ground import rooms
    for name in ("HOUSE_TOSS_PAUSE", "HOUSE_LOCK_PAUSE"):
        monkeypatch.setattr(rooms, name, (0, 0))
    code = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()["code"]
    client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": []})
    hand = _hand(client, code, "host")
    for i in range(3):
        _play(client, code, "host", hand[i]); _st(client, code)
    rv = _st(client, code)["reveal"]
    words = rv["words"]
    assert words and words.endswith(".")
    for p in rv["pairs"]:
        assert p["a"]["name"] in words or p["b"]["name"] in words
    c = rv["counts"]                                   # the words follow the scored pairing (same card first, then closest), not the rank pairs
    assert ("Same card" in words) == bool(rv["both"]) == (c["same"] > 0)
    assert ("Close:" in words) == (c["close"] > 0) and ("Different:" in words) == (c["apart"] > 0)
    assert c["same"] + c["close"] + c["apart"] == 3
    assert rv["pull"] is None or ({"host", "guest", "agreement"} <= set(rv["pull"]))
