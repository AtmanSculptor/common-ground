"""The results log and its roll-up, the guess-vs-data numbers above all."""
from common_ground import stats


def test_guess_vs_data_rolls_up_the_control():
    rows = [
        {"scores": {"host": 0.95, "guest": 0.94}, "baseline": {"verdict": "splits", "scores": {"host": 0.34, "guest": 0.79}}},
        {"scores": {"host": 0.97, "guest": 0.97}, "baseline": {"verdict": "holds", "scores": {"host": 0.86, "guest": 0.93}}},
        {"scores": {"host": 0.90, "guest": 0.99}, "baseline": {"verdict": "holds", "scores": {"host": 0.95, "guest": 0.80}}},   # guess wins one partner, loses the weaker
        {"scores": {"host": 0.98, "guest": 1.0}, "baseline": {"verdict": "misses", "scores": None}},                         # unscored guess
    ]
    g = stats.guess_vs_data(rows)
    assert g == {"n": 4, "holds": 2, "splits": 1, "misses": 1, "scored": 3, "qlou_higher_both": 2, "qlou_higher_weaker": 3}


def test_record_keeps_scores_and_the_control(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "FILE", tmp_path / "games.jsonl")
    result = {"meter": 99, "title": "t", "plays": {}, "both_played": [],
              "pick": {"artist": {"name": "X"}, "played": True, "attempt": 1, "calls": 2, "seconds": 9.8, "why_source": "llm",
                       "scores": {"host": 0.95, "guest": 0.94}, "baseline": {"guess": "G", "verdict": "holds", "scores": {"host": 0.6, "guest": 0.8}}}}
    stats.record("ABCD", result, False)
    r = stats.rows()[0]
    assert r["scores"] == {"host": 0.95, "guest": 0.94} and r["baseline"] == {"guess": "G", "verdict": "holds", "scores": {"host": 0.6, "guest": 0.8}}
    assert stats.summary()["baseline"]["qlou_higher_both"] == 1
