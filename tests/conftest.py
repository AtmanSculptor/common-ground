"""Test doubles. A FakeQloo answers like the hackathon API without a key or a network."""
from __future__ import annotations

import hashlib
import os
import random

import pytest

os.environ.setdefault("QLOO_API_KEY", "test-key")
os.environ.setdefault("CG_DATA_DIR", "/tmp/oursong-test-data")

from common_ground import catalog  # noqa: E402


ARTISTS = ["Dolly Parton", "Kacey Musgraves", "Elton John", "John Mayer", "Shakira", "Ben Harper", "Miles Davis",
           "Jay-Z", "Nirvana", "Daft Punk", "Norah Jones", "Guy Clark", "Linkin Park", "Van Morrison", "Lizzo"]


def _aff(entity_id: str, sig: dict) -> float:
    """Deterministic affinity in 0.80..0.99 from the entity and the signal. A fixed seed per pair keeps tests stable."""
    key = entity_id + "|" + ",".join(sorted(sig.get("tags", []) + sig.get("audiences", [])))
    h = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return 0.80 + (h % 1900) / 10000


class FakeQloo:
    """Enough of the Qloo client for the game: insights (pool + scoring + explainability), search, calls."""
    def __init__(self) -> None:
        self.calls = 0
        self.unknown_tags: set[str] = set()

    def _sig(self, audiences, entities, tags) -> dict:
        return {"audiences": list(audiences or []), "tags": list(tags or []), "entities": list(entities or [])}

    def insights(self, entity_type, *, audiences=None, entities=None, tags=None, location_query=None,
                 result_entities=None, popularity_min=None, take=50, page=1, explain=False):
        self.calls += 1
        sig = self._sig(audiences, entities, tags)
        if page > 1:
            return []
        if result_entities is not None:
            out = []
            for eid in result_entities:
                e = {"entity_id": eid, "name": eid.replace("id:", ""), "type": entity_type, "popularity": 0.95,
                     "query": {"affinity": _aff(eid, sig)}, "tags": [{"name": "Vocals", "type": "urn:tag:genre"}]}
                if explain:
                    e["query"]["explainability"] = {"signal.interests.tags": [{"tag_id": t, "score": 0.5} for t in sig["tags"]][:3]}
                out.append(e)
            return out
        rng = random.Random(",".join(sorted(sig["tags"] + sig["audiences"])))
        names = rng.sample(ARTISTS, min(take, len(ARTISTS)))
        out = []
        for n in names:
            e = {"entity_id": "id:" + n, "name": n, "type": entity_type, "popularity": 0.95,
                 "query": {"affinity": _aff("id:" + n, sig)}, "tags": [{"name": "Vocals", "type": "urn:tag:genre"}]}
            if explain:
                e["query"]["explainability"] = {"signal.interests.tags": [{"tag_id": t, "score": 0.5} for t in sig["tags"]][:3]}
            out.append(e)
        return out

    def compare(self, a_entities, b_entities, entity_type=None, take=30):
        self.calls += 1
        return {"results": {"tags": [{"name": "Soul", "subtype": "urn:tag:genre:music", "query": {"score": 0.88}},
                                     {"name": "Live Music", "subtype": "urn:tag:keyword:media", "query": {"score": 0.91}},
                                     {"name": "Indie Rock", "subtype": "urn:tag:genre:music", "query": {"score": 0.61}},
                                     {"name": "Soul", "subtype": "urn:tag:keyword:media", "query": {"score": 0.60}}]}}

    def search(self, query, types=None, take=5):
        self.calls += 1
        if query.lower().startswith("nobody"):
            return []                      # an artist Qloo has never met
        return [{"entity_id": "id:" + query, "name": query, "popularity": 0.97}]

    def all_audiences(self):
        return []


@pytest.fixture
def fake_qloo():
    return FakeQloo()


@pytest.fixture
def client(fake_qloo, monkeypatch, tmp_path):
    """The app over HTTP with the fake Qloo, fresh rooms and a fresh results log."""
    from fastapi.testclient import TestClient
    import app as appmod
    from common_ground import rooms as rooms_mod, stats
    monkeypatch.setattr(appmod, "Qloo", lambda **kw: fake_qloo)
    appmod.ROOMS = rooms_mod.Rooms(tmp_path / "rooms.pkl", random.Random(7))
    monkeypatch.setattr(stats, "FILE", tmp_path / "games.jsonl")
    return TestClient(appmod.app)


@pytest.fixture(autouse=True)
def no_matrix(monkeypatch, tmp_path):
    """Game tests run the full live loop unless a test builds a matrix itself."""
    from common_ground import matrix
    monkeypatch.setattr(matrix, "FILE", tmp_path / "no-matrix.json")
    matrix._cache = None


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in the tests touches the internet: catalog and LLM are stubbed."""
    monkeypatch.setattr(catalog, "_fetch", lambda artist: [{"track": f"{artist} song {i}", "artist": artist, "preview": "p", "art": "", "album": "", "year": "2020"} for i in range(1, 4)])
    catalog._cache.clear()
    from common_ground import qlou
    monkeypatch.setattr(qlou, "chat_json", lambda system, user, **kw: {"artist": "Dolly Parton", "line": "LLM should not be called in this test"})
