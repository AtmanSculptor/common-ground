"""The whole game through the HTTP API, with a fake Qloo and no network."""
import random

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(fake_qloo, monkeypatch, tmp_path):
    import app as appmod
    from common_ground import rooms as rooms_mod
    monkeypatch.setattr(appmod, "Qloo", lambda **kw: fake_qloo)
    appmod.ROOMS = rooms_mod.Rooms(tmp_path / "rooms.pkl", random.Random(7))
    from common_ground import stats
    monkeypatch.setattr(stats, "FILE", tmp_path / "games.jsonl")
    return TestClient(appmod.app)


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
            assert p["match"] in {"exact", "kin", "none"} and p["line"]
        r = client.post(f"/api/game/{code}/next").json()
    assert r["phase"] in ("thinking", "jukebox")  # the fake Qloo is instant; the thread may already be done
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox"
    pick = st["pick"]
    assert pick["artist"]["name"] and pick["tracks"] and pick["why"] and "votes" not in pick
    assert [s["step"] for s in st["thinking"]][-1] == "tracks"
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
    assert client.get("/api/stats").json()["games"] == 1


def test_solo_against_the_house(client):
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
        client.post(f"/api/game/{code}/next")
    st = _wait_pick(client, code)
    assert st["phase"] == "jukebox"
    out = client.post(f"/api/game/{code}/vote", json={"who": "host", "up": True}).json()
    assert out["outcome"] == "play"
    assert client.get("/api/stats").json()["solo"] == 1


def test_pages_and_health(client):
    for path in ("/", "/play", "/deck", "/stats"):
        assert client.get(path).status_code == 200
    h = client.get("/api/health").json()
    assert h["ok"] and "llm" in h
    assert client.get("/api/game/ZZZZ").status_code == 404
