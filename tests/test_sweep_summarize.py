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


def test_other_side_none_fails_bar_1():
    idx = {("f", "s"): _rec(0.04, 0.02, 0.06, bh=True), ("f", "r"): _rec(0.03, 0.01, 0.05, ts=500)}
    assert Z.grade(_cand(other_side="none - arithmetic"), idx, doc_ts=200)[1] == 1


def test_unreadable_and_zero_variance_tests_enter_bh_at_p_1():
    from research.sweep import common as S
    ok = {"se": 0.01, "games": 16, "p": 1e-9}
    few = {"se": 0.01, "games": 3, "p": 1e-9}
    flat = {"se": 0.0, "games": 16, "p": 0.0}
    assert S.bh_p(ok) == 1e-9 and S.bh_p(few) == 1.0 and S.bh_p(flat) == 1.0


def test_money_direction_separates_losing_side_significance():
    from research.sweep import common as S
    assert S.money_direction({"family": "H2 economic", "name": "x", "est": -2.0}) == "money-"
    assert S.money_direction({"family": "H1_net_mean", "name": "x", "est": 1.8}) == "money+"
    assert S.money_direction({"family": "H2 slope", "name": "x", "est": 0.3}) == "stat"


def test_boot_zero_variance_is_not_significant():
    from research.sweep import common as S
    rows = [{"game": g, "v": 0.9} for g in range(6)]
    assert S.boot(rows, S.mean_of("v"))["p"] == 1.0


def test_cost_bar():
    idx = {("f", "s"): _rec(0.04, 0.02, 0.06, bh=True), ("f", "r"): _rec(0.03, 0.01, 0.05, ts=500)}
    assert Z.grade(_cand(cost={"value": 1.0, "threshold": 3.6}), idx, doc_ts=200)[1] == 4
