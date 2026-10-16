"""The whole game through the HTTP API, with a fake Qloo and no network."""
import random
import time

import pytest
from fastapi.testclient import TestClient


def _answer_all(client, code, who):
    for _ in range(4):
        st = client.get(f"/api/game/{code}", params={"who": who}).json()
        q = st["question"]
        client.post(f"/api/game/{code}/answer", json={"who": who, "a": q["a"]["id"], "b": q["b"]["id"], "chose": q["a"]["id"]})


def _wait_pick(client, code):
    import time
    for _ in range(100):
        st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
        if st["phase"] != "thinking":
            return st
        time.sleep(0.05)
    raise AssertionError("Q-Lou never finished")


def test_two_phones_full_game(client):
    code = client.post("/api/game/new", json={"name": "Brent"}).json()["code"]
    assert client.get(f"/api/game/{code}", params={"who": "host"}).json()["sides"]["guest"]["joined"] is False
    assert client.post(f"/api/game/{code}/join", json={"name": "Josie"}).json()["who"] == "guest"
    assert client.post(f"/api/game/{code}/join", json={"name": "Third"}).status_code == 404
    _answer_all(client, code, "host")
    assert client.get(f"/api/game/{code}", params={"who": "host"}).json()["phase"] == "quiz"
    _answer_all(client, code, "guest")
    for rnd in range(2):
        st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
        assert st["phase"] == "cards" and st["round"] == rnd
        hand = [c["id"] for c in st["sides"]["host"]["hand"]]
        assert len(hand) == 6 and "hand" not in st["sides"]["guest"]
        assert client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:1]}).status_code == 400
        assert client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:2]}).json()["ok"]
        st = client.get(f"/api/game/{code}", params={"who": "guest"}).json()
        assert st["phase"] == "cards" and st["sides"]["host"]["locked"] and not st["sides"]["guest"]["locked"]
        ghand = [c["id"] for c in st["sides"]["guest"]["hand"]]
        client.post(f"/api/game/{code}/play", json={"who": "guest", "cards": [ghand[1], ghand[0]]})
        st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
        assert st["phase"] == "reveal" and len(st["reveal"]["pairs"]) == 2 and st["meter"] > 0
        for p in st["reveal"]["pairs"]:
            assert p["match"] in {"exact", "kin", "none"}
            assert "Brent" not in p["lines"]["host"] and "Josie" not in p["lines"]["guest"]  # your own phone says "You"
        r = client.post(f"/api/game/{code}/next", json={"more": rnd == 0}).json()
        if rnd == 0:
            assert r["phase"] == "cards"
    assert r["phase"] in ("thinking", "jukebox")  # the fake Qloo is instant; the thread may already be done
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox"
    pick = st["pick"]
    assert pick["artist"]["name"] and pick["tracks"] and pick["why"] and "votes" not in pick
    steps = [s["step"] for s in st["thinking"]]
    assert "tracks" in steps and steps[-1] in ("tracks", "baseline")   # the control may land after the song, by design
    # one no: Q-Lou tries again with that artist excluded
    first = pick["artist"]["id"]
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": False})
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox" and st["pick"]["attempt"] == 2 and st["pick"]["artist"]["id"] != first
    client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True})
    client.post(f"/api/game/{code}/vote", json={"who": "guest", "up": True})
    st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
    assert st["phase"] == "done" and st["result"]["pick"]["played"] is True
    assert st["result"]["meter"] >= 20 and st["result"]["title"]
    # "what you both played" spans the whole game, whichever round each card came out in
    plays = st["result"]["plays"]
    assert set(st["result"]["both_played"]) == set(plays["Brent"]) & set(plays["Josie"])
    assert client.get("/api/stats").json()["games"] == 1


def test_solo_against_the_house(client, monkeypatch):
    from common_ground import rooms
    monkeypatch.setattr(rooms, "HOUSE_LOCK_PAUSE", (0, 0))
    monkeypatch.setattr(rooms, "HOUSE_VOTE_PAUSE", (0, 0))
    r = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()
    code = r["code"]
    assert r["house"]
    _answer_all(client, code, "host")
    st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
    assert st["phase"] == "cards" and st["sides"]["guest"]["locked"]
    for _ in range(2):
        st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
        hand = [c["id"] for c in st["sides"]["host"]["hand"]]
        client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:2]})
        client.post(f"/api/game/{code}/next", json={"more": _ == 0})
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox"
    out = client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}).json()
    assert out["outcome"] == "play"
    assert client.get("/api/stats").json()["solo"] == 1


def test_pages_and_health(client):
    for path in ("/", "/play", "/deck", "/stats", "/organize"):
        assert client.get(path).status_code == 200
    assert client.get("/").text == client.get("/play").text      # the game is the front door
    assert "Our Song" in client.get("/").text
    assert "Common Ground" in client.get("/organize").text         # the older organizer, moved aside
    h = client.get("/api/health").json()
    assert h["ok"] and "llm" in h
    assert client.get("/api/game/ZZZZ").status_code == 404


def test_one_round_is_enough(client, monkeypatch):
    """A pair in a hurry gets a song after a single round."""
    from common_ground import rooms
    monkeypatch.setattr(rooms, "HOUSE_LOCK_PAUSE", (0, 0))
    code = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()["code"]
    _answer_all(client, code, "host")
    st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
    hand = [c["id"] for c in st["sides"]["host"]["hand"]]
    client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:2]})
    st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
    assert st["phase"] == "reveal" and st["more_allowed"] is True
    client.post(f"/api/game/{code}/next", json={"more": False})
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox" and st["pick"]["artist"]["name"]


def test_the_house_moves_on_a_human_clock(client, monkeypatch):
    """The demo partner picks cards and votes a few seconds after the human, not instantly."""
    from common_ground import rooms
    monkeypatch.setattr(rooms, "HOUSE_LOCK_PAUSE", (0.3, 0.3))
    monkeypatch.setattr(rooms, "HOUSE_VOTE_PAUSE", (0.3, 0.3))
    code = client.post("/api/game/new", json={"name": "Brent", "house": True}).json()["code"]
    _answer_all(client, code, "host")
    st = client.get(f"/api/game/{code}", params={"who": "host"}).json()
    assert st["phase"] == "cards" and not st["sides"]["guest"]["locked"]      # the stranger is still choosing
    hand = [c["id"] for c in st["sides"]["host"]["hand"]]
    client.post(f"/api/game/{code}/play", json={"who": "host", "cards": hand[:2]})
    assert client.get(f"/api/game/{code}", params={"who": "host"}).json()["phase"] == "cards"
    time.sleep(0.4)
    assert client.get(f"/api/game/{code}", params={"who": "host"}).json()["phase"] == "reveal"   # the poll let them lock in
    client.post(f"/api/game/{code}/next", json={"more": False})
    st = _wait_pick(client, code)
    out = client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}).json()
    assert out["outcome"] is None and client.get(f"/api/game/{code}", params={"who": "host"}).json()["phase"] == "jukebox"
    time.sleep(0.4)
    assert client.get(f"/api/game/{code}", params={"who": "host"}).json()["phase"] == "done"


def test_old_pickled_rooms_get_new_fields(tmp_path):
    """A room saved before a Side field existed still loads and plays."""
    import pickle
    from common_ground import rooms
    rs = rooms.Rooms(tmp_path / "rooms.pkl", random.Random(1))
    r = rs.create("Brent", house=True)
    for side in r.sides.values():
        side.__dict__.pop("pending"); side.__dict__.pop("due")
    (tmp_path / "rooms.pkl").write_bytes(pickle.dumps(rs.rooms))
    rs2 = rooms.Rooms(tmp_path / "rooms.pkl", random.Random(1))
    g = rs2.rooms[r.code].sides["guest"]
    assert g.pending is None and g.due == 0.0 and rs2.rooms[r.code].tick() is None
