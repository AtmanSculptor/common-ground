import random

from common_ground import quiz


def test_the_questions_then_a_profile():
    q = quiz.Quiz()
    rng = random.Random(3)
    for n in range(1, quiz.ROUNDS + 1):
        qu = q.next_question(rng)
        assert qu["n"] == n and qu["of"] == quiz.ROUNDS
        assert qu["a"]["id"] != qu["b"]["id"]
        assert quiz.quizable(qu["a"]["id"]) and quiz.quizable(qu["b"]["id"])
        assert q.answer(qu["a"]["id"], qu["b"]["id"], qu["a"]["id"])
    assert q.done and q.next_question(rng) is None
    assert 1 <= len(q.profile) <= 3


def test_stale_answer_is_ignored_and_none_counts_as_no_vote():
    q = quiz.Quiz()
    qu = q.next_question(random.Random(1))
    assert not q.answer("urn:audience:leisure:coffee", "urn:audience:leisure:foodie", "urn:audience:leisure:coffee")
    assert q.answer(qu["a"]["id"], qu["b"]["id"], None)
    assert q.tally == {}


def test_identity_segments_are_never_asked():
    assert not quiz.quizable("urn:audience:communities:lgbtq")
    assert not quiz.quizable("urn:audience:lifestyle_preferences_beliefs:christianity")
    assert quiz.quizable("urn:audience:leisure:coffee")


def test_house_preset_is_finished():
    h = quiz.Quiz.preset(quiz.HOUSE[0]["profile"])
    assert h.done and h.profile == quiz.HOUSE[0]["profile"][:3]
