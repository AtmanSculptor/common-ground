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
    assert min(ranked[0]["a"], ranked[0]["b"]) >= min(ranked[-1]["a"], ranked[-1]["b"]) - 0.01   # ties within a point go to popularity


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


def test_extend_route_is_loopback_only(client):
    assert client.post("/api/admin/matrix/extend", json={"names": ["Adele"]}, headers={"x-forwarded-for": "8.8.8.8"}).status_code == 404   # the guard answers 404, not 403: the route does not exist for the outside


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


def test_a_failing_batch_is_retried_then_counted_as_a_hole(fake_qloo, tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "RETRY_PAUSE", 0.0)
    cards = list(deck.cards())[:2]
    real = fake_qloo.insights
    tries = {"n": 0}

    def flaky(*a, **kw):
        if kw.get("result_entities") is not None and tries["n"] < 2:
            tries["n"] += 1
            raise RuntimeError("429")
        return real(*a, **kw)

    monkeypatch.setattr(fake_qloo, "insights", flaky)
    m = matrix.build(fake_qloo, out=tmp_path / "m.json", cards=cards, workers=1)
    assert tries["n"] == 2 and m["holes"] == [] and all(m["scores"].values())

    monkeypatch.setattr(fake_qloo, "insights", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")) if kw.get("result_entities") is not None else real(*a, **kw))
    m2 = matrix.build(fake_qloo, out=tmp_path / "m2.json", cards=cards[:1], workers=1)
    assert m2["holes"] == [cards[0]["name"]]


def test_partial_coverage_does_not_outrank_full_coverage():
    m = {"entities": {"full": {"name": "Full"}, "half": {"name": "Half"}},
         "scores": {"1": {"full": 0.9, "half": 0.99}, "2": {"full": 0.9, "half": 0.99}, "3": {"full": 0.9}, "4": {"full": 0.9}}}
    ps = matrix.partner_scores(m, [1, 2, 3, 4])
    assert abs(ps["half"] - (0.99 + 0.99 + 0.9 + 0.9) / 4) < 1e-9   # the two silent cards count as their floor, not as nothing
    assert ps["full"] == 0.9 and ps["half"] < 0.99
    assert matrix.partner_scores(m, [3, 4]) == {"full": 0.9}            # below half coverage: not ranked


def test_within_a_point_the_better_known_artist_wins():
    k = matrix.rank_key
    assert sorted(["obscure", "famous"], key=lambda n: k(0.99, 0.0, 0.97) if n == "obscure" else k(0.987, 0.0, 0.9999))[0] == "famous"
    assert sorted(["obscure", "famous"], key=lambda n: k(0.99, 0.0, 0.97) if n == "obscure" else k(0.90, 0.0, 0.9999))[0] == "obscure"


def test_resolver_forgives_spelling_and_refuses_collaborations(fake_qloo, monkeypatch):
    assert matrix._norm("Lil' Wayne") == "lil wayne" and matrix._norm("Dan + Shay") == "dan shay" and matrix._norm("Carin León") == "carin leon"
    hits = {"Lil Wayne": [{"entity_id": "x1", "name": "Lil Wayne & Drake", "popularity": 0.93},
                          {"entity_id": "x2", "name": "Lil' Wayne", "popularity": 0.999}],
            "Selena Gomez": [{"entity_id": "x3", "name": "Selena Gomez & the Scene", "popularity": 0.99}]}
    monkeypatch.setattr(fake_qloo, "search", lambda q, types=None, take=5: hits.get(q, []))
    assert matrix._resolve_seed(fake_qloo, "Lil Wayne")["id"] == "x2"
    assert matrix._resolve_seed(fake_qloo, "Selena Gomez") is None
    hits["ROSÉ"] = [{"entity_id": "x4", "name": "Rose", "popularity": 0.95}]
    hits["Carin León"] = [{"entity_id": "x5", "name": "Carin Leon", "popularity": 0.95}]
    assert matrix._resolve_seed(fake_qloo, "ROSÉ") is None                 # one word: the accent is the name
    assert matrix._resolve_seed(fake_qloo, "Carin León")["id"] == "x5"     # two words: spelling is forgiven


def test_extend_adds_new_names_and_scores_them_on_every_card(fake_qloo, tmp_path):
    out = tmp_path / "matrix.json"
    cards = list(deck.cards())[:3]
    matrix.build(fake_qloo, out=out, cards=cards, seeds=["Adele"])
    matrix._cache = None
    r = matrix.extend(fake_qloo, ["Adele", "Brand New Band", "Nobody Here"], path=out)   # the fake's card pools already hold Shakira
    assert r["added"] == ["Brand New Band"] and r["dropped"] == ["Nobody Here"]
    m = json.loads(out.read_text())
    assert "id:Brand New Band" in m["entities"] and all("id:Brand New Band" in col for col in m["scores"].values())
    assert m["seeds"]["dropped"] == ["Nobody Here"] and m.get("extended")


def test_seed_aliases_carry_qloo_spellings(fake_qloo, tmp_path, monkeypatch):
    f = tmp_path / "seeds.json"
    f.write_text(json.dumps({"artists": [{"name": "Giveon", "qloo": "GIVĒON"}, {"name": "Adele"}]}), encoding="utf-8")
    assert matrix.seed_aliases(f) == {"Giveon": "GIVĒON"} and matrix.seed_names(f) == ["Giveon", "Adele"]
    monkeypatch.setattr(fake_qloo, "search", lambda q, types=None, take=5: [{"entity_id": "x9", "name": "GIVĒON", "popularity": 0.95}] if q == "GIVĒON" else [])
    assert matrix._resolve_seed(fake_qloo, "Giveon", aliases={}) is None
    assert matrix._resolve_seed(fake_qloo, "Giveon", aliases=matrix.seed_aliases(f))["id"] == "x9"


def test_due_knows_when_to_extend_and_when_to_rebuild():
    import time
    day = 86400
    quiet = time.mktime(time.strptime("2026-10-20T09:00:00Z", "%Y-%m-%dT%H:%M:%SZ")) - time.timezone   # 09:00 UTC, 2 AM Arizona
    busy = quiet + 12 * 3600                                                                              # 21:00 UTC, 2 PM Arizona
    assert matrix.due(None) == "build"
    fresh = {"built": "2026-10-19T00:00:00Z"}
    assert matrix.due(fresh, now=quiet) is None
    week_old = {"built": "2026-10-10T00:00:00Z"}
    assert matrix.due(week_old, now=quiet) == "extend"
    assert matrix.due({"built": "2026-10-10T00:00:00Z", "extended": "2026-10-19T00:00:00Z"}, now=quiet) is None
    month_old = {"built": "2026-09-01T00:00:00Z", "extended": "2026-10-19T00:00:00Z"}
    assert matrix.due(month_old, now=busy) is None            # a rebuild waits for the quiet hours
    assert matrix.due(month_old, now=quiet) == "build"
