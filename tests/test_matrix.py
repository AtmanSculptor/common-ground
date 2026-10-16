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


def test_a_slow_live_check_does_not_hold_the_matrix_pick(fake_qloo, tmp_path, monkeypatch):
    """Past the verify deadline Q-Lou goes with the matrix; the live scores attach when they land."""
    import time
    from common_ground import qlou
    out = tmp_path / "matrix.json"
    matrix.build(fake_qloo, out=out, cards=list(deck.cards()))
    monkeypatch.setattr(matrix, "FILE", out)
    matrix._cache = None
    real = fake_qloo.insights

    def slow_when_verifying(*args, **kw):
        if kw.get("result_entities") is not None:
            time.sleep(0.3)
        return real(*args, **kw)

    monkeypatch.setattr(fake_qloo, "insights", slow_when_verifying)
    monkeypatch.setattr(qlou, "VERIFY_DEADLINE", 0.05)
    updates = []
    a = Partner("host", "Brent", _ids("Country roads", "Dog person"))
    b = Partner("guest", "Hank", _ids("Rock and roll", "Night owl"))
    t0 = time.time()
    p = QLou(fake_qloo, use_llm=False, on_update=updates.append).pick(a, b)
    assert time.time() - t0 < 0.25 and p.source == "matrix" and p.live is None
    assert "taking its time" in next(s["text"] for s in p.steps if s["step"] == "verify")
    time.sleep(0.5)
    assert p.live and set(p.live) == {"host", "guest"} and updates and updates[-1] is p


def test_seeds_join_the_pool_and_unknowns_are_dropped(fake_qloo, tmp_path):
    """The outside list: names Qloo knows become pool artists; names it has never met are counted, not guessed."""
    out = tmp_path / "matrix.json"
    cards = list(deck.cards())[:3]
    m = matrix.build(fake_qloo, out=out, cards=cards, seeds=["Morgan Wallen", "Nobody From Nowhere", "Morgan Wallen"])
    assert m["seeds"] == {"wanted": 2, "found": 1, "dropped": ["Nobody From Nowhere"]}
    assert "id:Morgan Wallen" in m["entities"] and "id:Nobody From Nowhere" not in m["entities"]
    assert all("id:Morgan Wallen" in col for col in m["scores"].values())   # scored against every card like the rest


def test_seed_names_dedupe_and_survive_a_missing_file(tmp_path):
    assert matrix.seed_names(tmp_path / "none.json") == []
    f = tmp_path / "seeds.json"
    f.write_text(json.dumps({"artists": [{"name": "Adele"}, {"name": "adele "}, "Zach  Bryan", {"name": ""}]}))
    assert matrix.seed_names(f) == ["Adele", "Zach Bryan"]


def test_pool_pull_stops_on_a_short_or_repeated_page(fake_qloo, monkeypatch):
    card = list(deck.cards())[0]
    pages = []
    real = fake_qloo.insights

    def counting(*a, **kw):
        pages.append(kw.get("page", 1))
        return real(*a, **kw)

    monkeypatch.setattr(fake_qloo, "insights", counting)
    ents = matrix._pull_card_pool(fake_qloo, card, per_card=150, popularity_min=0.9)
    assert ents and pages == [1]          # the fake's first page is short, so no second call
    monkeypatch.setattr(fake_qloo, "insights", lambda *a, **kw: real(*a, **{**kw, "page": 1}) + [])   # every page repeats page one
    monkeypatch.setattr(matrix, "PAGE", 2)
    ents2 = matrix._pull_card_pool(fake_qloo, card, per_card=6, popularity_min=0.9)
    assert len(ents2) == len({e["id"] for e in ents2})   # no duplicates when pages repeat


def test_the_old_matrix_serves_until_the_new_one_is_written(fake_qloo, tmp_path):
    out = tmp_path / "matrix.json"
    out.write_text('{"old": true}')
    cards = list(deck.cards())[:2]
    seen = []
    matrix.build(fake_qloo, out=out, cards=cards, on_progress=lambda d: seen.append(json.loads(out.read_text()).get("old")))
    assert all(seen[:-1]) and "old" not in json.loads(out.read_text())   # old file stays whole until the final replace
