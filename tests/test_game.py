"""The whole game through the HTTP API, with a fake Qloo and no network."""
import random
import time

import pytest
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


def _to_jukebox(client, code, toss_host=1, toss_guest=0):
    """Both toss, both play three, press on. Returns the jukebox state."""
    h = _hand(client, code, "host")
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": h[:toss_host]}).json()["ok"]
    g = _hand(client, code, "guest")
    assert client.post(f"/api/game/{code}/toss", json={"who": "guest", "cards": g[:toss_guest]}).json()["ok"]
    assert _st(client, code)["phase"] == "play"
    h, g = _hand(client, code, "host"), _hand(client, code, "guest")
    client.post(f"/api/game/{code}/play", json={"who": "host", "cards": h[:3]})
    client.post(f"/api/game/{code}/play", json={"who": "guest", "cards": [g[1], g[0], g[2]]})
    assert _st(client, code)["phase"] == "reveal"
    client.post(f"/api/game/{code}/next")
    return _wait_pick(client, code)


def test_two_phones_full_game(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    st = _st(client, code)
    assert st["phase"] == "lobby" and st["sides"]["guest"]["joined"] is False and st["rounds"] == ["toss", "play", "reveal"]
    assert client.post(f"/api/game/{code}/join", json={"name": "Josie"}).json()["who"] == "guest"
    assert client.post(f"/api/game/{code}/join", json={"name": "Third"}).status_code == 404
    # round one: toss
    st = _st(client, code)
    assert st["phase"] == "toss" and st["sides"]["host"]["tokens"] == 10
    hand = [c["id"] for c in st["sides"]["host"]["hand"]]
    assert len(hand) == 6 and "hand" not in st["sides"]["guest"]
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand[:4]}).status_code == 400   # three at most
    assert client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:3]}).status_code == 400   # not yet
    out = client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand[:2]}).json()
    new = [c["id"] for c in out["hand"]]
    assert out["phase"] == "toss" and new[2:] == hand[2:] and not set(new[:2]) & set(hand)
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": []}).status_code == 400     # once
    st = _st(client, code, "guest")
    assert st["sides"]["host"]["locked"] and st["sides"]["host"]["tossed_n"] == 2 and not st["sides"]["guest"]["locked"]
    ghand = [c["id"] for c in st["sides"]["guest"]["hand"]]
    assert client.post(f"/api/game/{code}/toss", json={"who": "guest", "cards": []}).json()["phase"] == "play"
    st = _st(client, code)
    assert st["phase"] == "play" and st["toss_summary"]["host"] and st["toss_summary"]["guest"] == []
    assert st["sides"]["host"]["tossed"] == hand[:2] and not st["sides"]["host"]["locked"]
    # round two: play three, ranked
    hand = [c["id"] for c in st["sides"]["host"]["hand"]]
    assert client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:2]}).status_code == 400
    client.post(f"/api/game/{code}/placed", json={"who": "host", "n": 2})
    assert _st(client, code, "guest")["sides"]["host"]["placed"] == 2
    assert client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:3]}).json()["ok"]
    st = _st(client, code, "guest")
    assert st["phase"] == "play" and st["sides"]["host"]["locked"] and st["sides"]["host"]["placed"] == 3
    client.post(f"/api/game/{code}/play", json={"who": "guest", "cards": [ghand[1], ghand[0], ghand[2]]})
    # round three: reveal, rank one facing rank one
    st = _st(client, code)
    rv = st["reveal"]
    assert st["phase"] == "reveal" and [p["rank"] for p in rv["pairs"]] == [1, 2, 3] and st["meter"] > 0
    assert rv["pairs"][0]["a"]["id"] == hand[0] and rv["pairs"][0]["b"]["id"] == ghand[1]
    assert rv["words"] and all(p["match"] in {"exact", "kin", "none"} for p in rv["pairs"])
    assert client.post(f"/api/game/{code}/react", json={"who": "guest", "reaction": "guilty"}).json()["ok"]
    assert not client.post(f"/api/game/{code}/react", json={"who": "guest", "reaction": "meh"}).json()["ok"]
    assert _st(client, code)["sides"]["guest"]["reaction"]["name"] == "guilty"
    r = client.post(f"/api/game/{code}/next").json()
    assert r["phase"] in ("thinking", "jukebox")  # the fake Qloo is instant; the thread may already be done
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


def test_one_no_brings_the_backup_and_the_first_stays_buyable(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    st = _to_jukebox(client, code)
    first = st["pick"]["artist"]["id"]
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    out = client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": False}).json()
    assert out["outcome"] == "backup"
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox" and st["pick"]["attempt"] == 2 and st["pick"]["artist"]["id"] != first
    assert st["offers"][0]["status"] == "declined" and st["offers"][0]["said_no"] == ["guest"]
    assert st["sides"]["host"]["tokens"] == 10                      # nobody paid yet
    # the one who said yes buys the first song alone for the full price
    assert client.post(f"/api/game/{code}/buy", json={"who": "host", "attempt": 2}).status_code == 400   # not declined
    assert client.post(f"/api/game/{code}/buy", json={"who": "host", "attempt": 1}).json()["phase"] == "done"
    st = _st(client, code)
    r = st["result"]
    assert r["paid_by"] == "host" and r["pick"]["artist"]["id"] == first and r["tokens"] == {"host": 6, "guest": 10}
    assert [o["status"] for o in r["offers"]] == ["played", "declined"]


def test_two_nos_mean_last_call_then_the_night_moves_on(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    _to_jukebox(client, code)
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": False})
    client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": True})
    _wait_pick(client, code)
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    out = client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": False}).json()
    assert out["outcome"] == "lastcall" and _st(client, code)["phase"] == "lastcall"
    assert client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}).status_code == 400
    assert client.post(f"/api/game/{code}/next").json()["phase"] == "done"
    r = _st(client, code)["result"]
    assert r["paid_by"] is None and r["pick"]["played"] is False and r["tokens"] == {"host": 10, "guest": 10}


def test_no_tokens_no_song(client):
    from common_ground import rooms
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    _to_jukebox(client, code)
    room = rooms_of(client)[code]
    room.sides["guest"].tokens = 1
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    out = client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": True}).json()
    assert out["outcome"] == "backup"                               # a yes that cannot pay its half is a no
    _wait_pick(client, code)
    assert client.post(f"/api/game/{code}/buy", json={"who": "guest", "attempt": 1}).status_code == 400   # one token
    assert client.post(f"/api/game/{code}/buy", json={"who": "host", "attempt": 1}).json()["ok"]        # the partner covers


def rooms_of(client):
    import app as appmod
    return appmod.ROOMS.rooms


def test_solo_against_the_house(client, monkeypatch):
    from common_ground import rooms
    for name in ("HOUSE_TOSS_PAUSE", "HOUSE_LOCK_PAUSE", "HOUSE_VOTE_PAUSE", "HOUSE_REACT_PAUSE"):
        monkeypatch.setattr(rooms, name, (0, 0))
    r = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()
    code = r["code"]
    assert r["house"]
    st = _st(client, code)
    assert st["phase"] == "toss" and st["sides"]["guest"]["locked"]          # the house tossed at once (no pause)
    hand = _hand(client, code, "host")
    assert client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": hand[:1]}).json()["phase"] == "play"
    st = _st(client, code)
    assert st["sides"]["guest"]["locked"]                                     # and played at once
    hand = _hand(client, code, "host")
    client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:3]})
    assert _st(client, code)["phase"] == "reveal"
    client.post(f"/api/game/{code}/next")
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
    for name in ("HOUSE_TOSS_PAUSE", "HOUSE_LOCK_PAUSE", "HOUSE_VOTE_PAUSE", "HOUSE_REACT_PAUSE"):
        monkeypatch.setattr(rooms, name, (0.3, 0.3))
    code = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()["code"]
    st = _st(client, code)
    assert st["phase"] == "toss" and not st["sides"]["guest"]["locked"]      # the stranger is still looking at the hand
    client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": []})
    assert _st(client, code)["phase"] == "toss"
    time.sleep(0.4)
    assert _st(client, code)["phase"] == "play"                             # the poll let the house toss
    hand = _hand(client, code, "host")
    client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:3]})
    assert _st(client, code)["phase"] == "play"
    time.sleep(0.4)
    assert _st(client, code)["phase"] == "reveal"
    client.post(f"/api/game/{code}/next")
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


def test_placed_cards_show_face_down_to_the_partner(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    client.post(f"/api/game/{code}/join", json={"name": "Josie"})
    client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": []})
    client.post(f"/api/game/{code}/toss", json={"who": "guest", "cards": []})
    client.post(f"/api/game/{code}/placed", json={"who": "host", "n": 1})
    assert _st(client, code, "guest")["sides"]["host"]["placed"] == 1
    hand = _hand(client, code, "host")
    client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:3]})
    assert _st(client, code, "guest")["sides"]["host"]["placed"] == 3
    assert client.post(f"/api/game/{code}/placed", json={"who": "host", "n": 4}).status_code == 422


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
    for name in ("HOUSE_TOSS_PAUSE", "HOUSE_LOCK_PAUSE", "HOUSE_REACT_PAUSE"):
        monkeypatch.setattr(rooms, name, (0, 0))
    code = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()["code"]
    client.post(f"/api/game/{code}/toss", json={"who": "host", "cards": []})
    hand = _hand(client, code, "host")
    client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:3]})
    rv = _st(client, code)["reveal"]
    words = rv["words"]
    assert words and words.endswith(".")
    for p in rv["pairs"]:
        assert p["a"]["name"] in words or p["b"]["name"] in words
    assert ("Same card" in words) == bool(rv["both"])
    assert ("Close:" in words) == any(p["match"] == "kin" for p in rv["pairs"])
    assert ("Different:" in words) == any(p["match"] == "none" for p in rv["pairs"])
    assert rv["pull"] is None or ({"host", "guest", "agreement"} <= set(rv["pull"]))
