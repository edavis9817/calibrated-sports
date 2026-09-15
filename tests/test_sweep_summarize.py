"""Brief 022 Part 4: the five-bar grader computes bars 2-3 from registries."""
from research.sweep import summarize as Z


def _rec(est, lo, hi, bh=False, ts=100.0, role="search"):
    return {"est": est, "lo": lo, "hi": hi, "estimable": True, "excludes_zero": lo > 0 or hi < 0,
            "bh_survives": bh, "ts": ts, "role": role}


def _cand(**kw):
    c = {"id": "x", "search_test": ["f", "s"], "replication_test": ["f", "r"],
         "mechanism": "why", "other_side": "who",
         "cost": {"value": 3.0, "threshold": 2.5}, "persistence_s": 120}
    c.update(kw)
    return c


def test_a_candidate_clearing_everything_is_a_finding():
    idx = {("f", "s"): _rec(0.04, 0.02, 0.06, bh=True), ("f", "r"): _rec(0.03, 0.01, 0.05, ts=500)}
    bars, first = Z.grade(_cand(), idx, doc_ts=200)
    assert bars == [True] * 5 and first is None


def test_replication_computed_before_the_mechanism_was_committed_fails_bar_1():
    idx = {("f", "s"): _rec(0.04, 0.02, 0.06, bh=True), ("f", "r"): _rec(0.03, 0.01, 0.05, ts=150)}
    assert Z.grade(_cand(), idx, doc_ts=200)[1] == 1


def test_opposite_sign_replication_fails_bar_2():
    idx = {("f", "s"): _rec(0.04, 0.02, 0.06, bh=True), ("f", "r"): _rec(-0.03, -0.05, -0.01, ts=500)}
    assert Z.grade(_cand(), idx, doc_ts=200)[1] == 2


def test_no_bh_survival_fails_bar_3_and_latency_race_fails_bar_5():
    idx = {("f", "s"): _rec(0.04, 0.02, 0.06, bh=False), ("f", "r"): _rec(0.03, 0.01, 0.05, ts=500)}
    bars, first = Z.grade(_cand(persistence_s=8), idx, doc_ts=200)
    assert first == 3 and bars[4] is False


def test_cost_bar():
    idx = {("f", "s"): _rec(0.04, 0.02, 0.06, bh=True), ("f", "r"): _rec(0.03, 0.01, 0.05, ts=500)}
    assert Z.grade(_cand(cost={"value": 1.0, "threshold": 3.6}), idx, doc_ts=200)[1] == 4
