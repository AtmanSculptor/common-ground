from common_ground import deck
from common_ground.qlou import Partner, QLou, _curve, _flatten_explain


def _ids(*names):
    return [next(c["id"] for c in deck.cards() if c["name"] == n) for n in names]


def test_pick_is_backed_by_qloo_and_steps_are_logged(fake_qloo):
    a = Partner("host", "Brent", _ids("Country roads", "Dog person"), ["urn:audience:leisure:coffee"])
    b = Partner("guest", "Hank", _ids("Rock and roll", "Night owl"))
    agent = QLou(fake_qloo, use_llm=False)
    p = agent.pick(a, b)
    assert p is not None
    assert p.artist["id"].startswith("id:") and p.artist["name"]
    assert set(p.scores) == {"host", "guest"} and all(0 <= v <= 1 for v in p.scores.values())
    # the pick maximises the weaker score across the shortlist
    assert min(p.scores.values()) >= max(min(r["host"], r["guest"]) for r in p.ranked[1:])
    names = [s["step"] for s in p.steps]
    for must in ("read", "pool", "score", "curve", "pick", "explain", "tracks"):
        assert must in names
    assert p.why_source == "facts" and p.artist["name"] in p.why
    assert p.tracks and p.tracks[0]["artist"] == p.artist["name"]
    assert p.calls == fake_qloo.calls > 0 and p.baseline is None


def test_exclusions_change_the_pick(fake_qloo):
    a = Partner("host", "A", _ids("Jazz hands", "Whiskey"))
    b = Partner("guest", "B", _ids("Gospel", "Trail head"))
    first = QLou(fake_qloo, use_llm=False).pick(a, b)
    second = QLou(fake_qloo, use_llm=False).pick(a, b, exclude={first.artist["id"]})
    assert second.artist["id"] != first.artist["id"]


def test_llm_line_is_citation_checked(fake_qloo, monkeypatch):
    from common_ground import qlou
    a = Partner("host", "A", _ids("Pop star", "Big city"))
    b = Partner("guest", "B", _ids("Latin heat", "Taco Tuesday"))
    # a line that names the wrong artist is rejected
    monkeypatch.setattr(qlou, "chat_json", lambda s, u, **kw: {"artist": "Shakira", "line": "You two should hear Some Other Band tonight."})
    p = QLou(fake_qloo, use_llm=True).pick(a, b)
    assert p.why_source == "facts"
    # a line that names the pick once and nothing else passes
    monkeypatch.setattr(qlou, "chat_json", lambda s, u, **kw: {"artist": "Shakira", "line": f"Tonight belongs to {p.artist['name']}. Both of you lean in."})
    p2 = QLou(fake_qloo, use_llm=True).pick(a, b)
    assert p2.why_source == "llm" and p2.artist["name"] in p2.why
    assert p2.baseline and p2.baseline["guess"] == "Shakira" and p2.baseline["verdict"] in {"holds", "splits", "no data"}


def test_llm_budget_exhaustion_is_graceful(fake_qloo, monkeypatch):
    from common_ground import qlou
    from common_ground.llm import BudgetExceeded

    def boom(*a, **k):
        raise BudgetExceeded("cap")
    monkeypatch.setattr(qlou, "chat_json", boom)
    p = QLou(fake_qloo, use_llm=True).pick(Partner("host", "A", _ids("Metal")), Partner("guest", "B", _ids("Pop star")))
    assert p.why_source == "facts" and p.baseline is None


def test_curve_and_explain_flattening():
    assert _curve({"a": 0.9, "b": 0.95, "c": 1.0}) == {"a": 0.0, "b": 0.5, "c": 1.0}
    assert _curve({"a": 0.9, "b": 0.9}) == {"a": 1.0, "b": 1.0}
    out: list[dict] = []
    _flatten_explain({"signal.interests.tags": [{"tag_id": "urn:tag:genre:music:country", "score": 0.7}],
                      "signal.demographics.audiences": {"urn:audience:leisure:coffee": 0.2}}, out)
    labels = {d["label"] for d in out}
    assert "country" in labels and "coffee" in labels
