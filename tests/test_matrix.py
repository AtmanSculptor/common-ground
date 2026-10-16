"""The card matrix: build with the fake client, draft without any calls, and the loopback-only route."""
import json
import pathlib

from common_ground import deck, matrix
from common_ground.qlou import Partner, QLou


def _ids(*names):
    return [next(c["id"] for c in deck.cards() if c["name"] == n) for n in names]


def test_build_then_draft_needs_no_calls(fake_qloo, tmp_path, monkeypatch):
    out = tmp_path / "matrix.json"
    cards = list(deck.cards())[:6]
    progress = []
    m = matrix.build(fake_qloo, out=out, on_progress=progress.append, cards=cards)
    assert out.exists() and not out.with_suffix(".partial.json").exists()
    assert len(m["scores"]) == 6 and m["entities"] and progress[-1]["stage"] == "done"
    calls_before = fake_qloo.calls
    ranked = matrix.draft(m, [cards[0]["id"], cards[1]["id"]], [cards[2]["id"], cards[3]["id"]])
    assert fake_qloo.calls == calls_before
    assert ranked and ranked[0]["name"] and 0 <= ranked[0]["a"] <= 1 and 0 <= ranked[0]["b"] <= 1
    assert min(ranked[0]["a"], ranked[0]["b"]) >= min(ranked[-1]["a"], ranked[-1]["b"])


def test_build_resumes_from_a_checkpoint(fake_qloo, tmp_path):
    out = tmp_path / "matrix.json"
    cards = list(deck.cards())[:4]
    ck = out.with_suffix(".partial.json")
    matrix.build(fake_qloo, out=out, cards=cards[:2])    # a full small build
    first = json.loads(out.read_text())
    ck.write_text(json.dumps({"pool": {}, "scores": {}, "started": 0}))
    n = fake_qloo.calls
    matrix.build(fake_qloo, out=out, cards=cards)
    assert fake_qloo.calls > n and json.loads(out.read_text())["cards"] == 4 and first["cards"] == 2


def test_qlou_drafts_from_the_matrix_and_verifies_live(fake_qloo, tmp_path, monkeypatch):
    out = tmp_path / "matrix.json"
    m = matrix.build(fake_qloo, out=out, cards=list(deck.cards()))
    monkeypatch.setattr(matrix, "FILE", out)
    matrix._cache = None
    a = Partner("host", "Brent", _ids("Country roads", "Dog person"))
    b = Partner("guest", "Hank", _ids("Rock and roll", "Night owl"))
    before = fake_qloo.calls
    p = QLou(fake_qloo, use_llm=False).pick(a, b)
    assert p is not None and p.source == "matrix"
    assert fake_qloo.calls - before <= 3          # two verification calls, no pooling or full scoring
    assert set(p.live) == {"host", "guest"} and p.artist["id"] in m["entities"]
    names = [s["step"] for s in p.steps]
    assert "draft" in names and "verify" in names and "pool" not in names
    bet = next(s for s in p.steps if s["step"] == "draft")["bet"]
    assert p.artist["name"] in bet


def test_matrix_route_is_loopback_only(client):
    assert client.get("/api/admin/matrix").status_code in (200, 404)
    r = client.get("/api/admin/matrix", headers={"x-forwarded-for": "8.8.8.8"})
    assert r.status_code == 404
