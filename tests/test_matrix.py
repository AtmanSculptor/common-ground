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


def test_within_a_few_points_the_better_known_artist_wins():
    k = matrix.rank_key
    def first(obscure, famous):
        return sorted(["obscure", "famous"], key=lambda n: k(obscure, 0.0, 0.97) if n == "obscure" else k(famous, 0.0, 0.9999))[0]
    assert first(0.99, 0.987) == "famous"
    assert first(0.99, 0.976) == "famous"      # the Lissie case: 98 for a name nobody knows loses to 97 for one they all do
    assert first(0.99, 0.90) == "obscure"      # nine points is a real difference


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


def test_the_pick_carries_what_the_two_share(fake_qloo, tmp_path, monkeypatch):
    import time
    out = tmp_path / "matrix.json"
    matrix.build(fake_qloo, out=out, cards=list(deck.cards()))
    monkeypatch.setattr(matrix, "FILE", out)
    matrix._cache = None
    a = Partner("host", "Brent", _ids("Country roads", "Dog person"))
    b = Partner("guest", "Hank", _ids("Rock and roll", "Night owl"))
    p = QLou(fake_qloo, use_llm=False).pick(a, b)
    time.sleep(0.3)                                     # compare is a done-screen extra; it may land after the pick
    assert [x["name"] for x in p.shared] == ["Soul", "Indie Rock", "Live Music"]   # music genres first, then by score
    assert any(s["step"] == "compare" for s in p.steps)
    assert matrix.top_for(matrix.load(out), a.cards) and len(matrix.top_for(matrix.load(out), a.cards)) == 3


def test_rank_weights_and_tosses_move_the_draft():
    m = {"entities": {"x": {"name": "X", "popularity": .9}, "y": {"name": "Y", "popularity": .9}},
         "scores": {"1": {"x": 0.9, "y": 0.6}, "2": {"x": 0.6, "y": 0.9}, "3": {"x": 0.7, "y": 0.7}}}
    flat = matrix.partner_scores(m, [1, 2])
    assert abs(flat["x"] - flat["y"]) < 1e-9                           # two equal cards: a tie
    ranked = matrix.partner_scores(m, [1, 2], weights=[3, 2])
    assert ranked["x"] > ranked["y"]                                   # "Most me" on card 1 breaks it toward X
    tossed = matrix.partner_scores(m, [3], tossed=[1])
    assert tossed["y"] > tossed["x"]                                   # card 1 liked X; tossing it pulls X down
    assert set(matrix.partner_scores(m, [3], tossed=[1])) == {"x", "y"}   # tossed cards never decide coverage
    assert matrix.partner_scores(m, [], tossed=[1]) == {}              # nothing played, nothing ranked
    d = matrix.draft(m, [1], [2], a_weights=[3], b_weights=[3], a_tossed=[2], b_tossed=[1])
    assert [r["id"] for r in d] and all(0 <= r["a"] <= 1 for r in d)


def test_new_cards_get_columns_without_a_rebuild(tmp_path, fake_qloo, monkeypatch):
    path = tmp_path / "m.json"
    m = {"built": "2026-10-01T00:00:00Z", "entities": {"a1": {"name": "A", "popularity": .95}, "a2": {"name": "B", "popularity": .93}},
         "scores": {"1": {"a1": 0.9, "a2": 0.6}}}
    path.write_text(json.dumps(m), encoding="utf-8")
    cards = [{"id": 1, "name": "Old", "signal": {"tags": ["t"]}}, {"id": 99, "name": "New card", "signal": {"audiences": ["urn:audience:x:y"]}}]
    assert [c["name"] for c in matrix.missing_cards(matrix.load(path), cards)] == ["New card"]
    out = matrix.add_cards(fake_qloo, [cards[1]], path=path, workers=1)
    assert out["cards"] == ["New card"] and out["scored"]["99"] >= 1
    m2 = matrix.load(path)
    assert "99" in m2["scores"] and m2["cards"] == 2 and set(m2["entities"]) == {"a1", "a2"}   # the pool is untouched
    assert matrix.missing_cards(m2, cards) == []
