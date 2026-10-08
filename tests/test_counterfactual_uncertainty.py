"""c-42 - the counterfactual ledger on an uncertainty scale.
Run: pytest -q tests/test_counterfactual_uncertainty.py

Synthetic fixtures only - no store is opened. What these guard:

- the standard errors are the registered ones, and refuse where none exists;
- the nearest flip is chosen IN STANDARD ERRORS, which need not be the nearest in
  log ratio (the reason the scan runs one direction at a time);
- a reported flip value reaches the flip and a slightly smaller move does not;
- the frozen gate can fail;
- the registered verdict comes out all three ways (CLAUDE.md, falsifiability);
- a game-block interval does not narrow when rows are duplicated inside a game.
"""
import math

import numpy as np
import pytest

from research import counterfactual_ledger as CL
from research import counterfactual_uncertainty as CU


def comp(**kw):
    c = {"stat": "receptions", "n": 10, "own_mean": 4.0, "own_var": 5.0, "group_mean": 3.0,
         "group_var": 4.0, "no_group": False, "weight": 0.625, "role": 1,
         "team_changed": False, "coach_changed": False}
    c.update(kw)
    return c


def test_own_mean_se_is_the_standard_error_of_the_sample_mean():
    g = [2.0, 4.0, 6.0, 3.0, 5.0]
    assert CU.se_own_mean(g) == pytest.approx(math.sqrt(np.var(g, ddof=1) / 5))
    assert CU.se_own_mean([4.0]) is None                 # one game: no as-of uncertainty
    assert CU.se_own_mean([3.0, 3.0, 3.0]) is None       # zero variance: not a ruler


def test_group_mean_se_and_its_refusals():
    grp = [(2.0, 1.0), (4.0, 2.0), (6.0, 3.0)]
    assert CU.se_group_mean(comp(), grp) == pytest.approx(math.sqrt(4.0 / 3))
    assert CU.se_group_mean(comp(no_group=True), grp) is None
    assert CU.se_group_mean(comp(), grp[:1]) is None


def test_dispersion_se_is_seeded_by_the_lean_and_moves_with_the_sample():
    games = [0.0, 2.0, 9.0, 4.0, 1.0, 7.0, 3.0, 5.0, 2.0, 6.0]
    grp = [(2.0, 3.0), (3.0, 4.0), (4.0, 5.0), (3.5, 2.0)]
    c = comp(own_mean=float(np.mean(games)), own_var=float(np.var(games)))
    a = CU.se_dispersion(c, games, grp, "lean-a", draws=200)
    assert a == CU.se_dispersion(c, games, grp, "lean-a", draws=200)      # reproducible
    assert a != CU.se_dispersion(c, games, grp, "lean-b", draws=200)      # independent per lean
    assert a > 0
    assert CU.se_dispersion(c, games[:1], grp, "lean-a") is None
    flat = [3.0] * 10                                    # nothing to resample: only the group moves
    cf = comp(own_mean=3.0, own_var=0.0)
    assert CU.se_dispersion(cf, flat, grp, "lean-a", draws=200) < a


def test_nearest_in_standard_errors_is_not_nearest_in_log_ratio():
    """Up by x2 (log 0.693) and down by x1/3 (log 1.099): the log scale takes up,
    the absolute scale takes down (|4| against |2.67|)."""
    cr = [(math.log(2.0), 1), (math.log(3.0), -1)]
    best = CU.nearest_in_se(4.0, 0.5, cr)
    assert best["direction"] == "down"
    assert best["distance"] == pytest.approx((4.0 - 4.0 / 3) / 0.5)
    assert min(u for u, _ in cr) == math.log(2.0)        # the log-nearest was the other one
    assert CU.nearest_in_se(4.0, 0.5, []) is None


def test_a_flip_value_reaches_the_flip_and_a_smaller_move_does_not():
    c = comp()
    line, side, T = 3.5, "over", 4.0
    mkt = CL.p_over(c, line) - 0.08                      # an over lean, 8 points above the market
    ses = {"own_mean": 0.7, "group_mean": 0.3, "dispersion": 1.5}
    pert, log_min = CU.se_perturbations(c, line, side, mkt, T, ses)
    own = pert["own_mean"]
    assert own is not None and own["direction"] == "down"
    assert CL.reached(CL.p_over(c, line, own_mean=own["value"]), side, mkt, T, "flip")
    back = c["own_mean"] - 0.98 * (c["own_mean"] - own["value"])
    assert not CL.reached(CL.p_over(c, line, own_mean=back), side, mkt, T, "flip")
    assert own["distance"] == pytest.approx((c["own_mean"] - own["value"]) / 0.7)
    # the per-direction scan finds c-40's own nearest log distance
    ref = CL.min_perturbations(c, line, side, mkt, T, "flip")
    for i in CU.INPUTS:
        assert (ref[i] is None) == (log_min[i] is None)
        if ref[i] is not None:
            assert ref[i]["distance"] == pytest.approx(log_min[i], abs=1e-9)
    # an input with no standard error has no distance, whatever it could reach
    none, _ = CU.se_perturbations(c, line, side, mkt, T, dict(ses, own_mean=None))
    assert none["own_mean"] is None


def test_the_ruler_can_change_the_flipping_input():
    """Same lean, same flips: a tight own-mean SE hands the row to the group mean."""
    c = comp()
    mkt = CL.p_over(c, 3.5) - 0.08
    wide, _ = CU.se_perturbations(c, 3.5, "over", mkt, 4.0,
                                  {"own_mean": 1.0, "group_mean": 0.05, "dispersion": 1.0})
    tight, _ = CU.se_perturbations(c, 3.5, "over", mkt, 4.0,
                                   {"own_mean": 0.01, "group_mean": 5.0, "dispersion": 1.0})
    pick = lambda p: CU.flipping({i: p[i] and p[i]["distance"] for i in CU.INPUTS})  # noqa: E731
    assert pick(wide) == "own_mean"
    assert pick(tight) == "group_mean"


def test_the_frozen_gate_refuses_a_fit_that_is_not_c40s():
    c = comp()
    games = [4.0 + d for d in (-3, -2, -1, 0, 1, 2, 3, 0, 1, -1)]
    c["own_mean"], c["own_var"] = float(np.mean(games)), float(np.var(games))
    grp = [(2.0, 3.0), (4.0, 5.0)]
    c["group_mean"], c["group_var"] = 3.0, 4.0
    row = {"lean_id": "x", "as_of": {"own_mean": c["own_mean"], "group_mean": 3.0,
                                     "weight": c["weight"], "dispersion": CL.replica(c)[2],
                                     "prior_games": 10}}
    entry = {"c": c, "games": games, "group": grp}
    CU.assert_frozen(row, entry)                                    # passes as built
    with pytest.raises(CU.FrozenError):
        CU.assert_frozen(dict(row, as_of=dict(row["as_of"], own_mean=c["own_mean"] + 1e-6)), entry)
    with pytest.raises(CU.FrozenError):
        CU.assert_frozen(row, dict(entry, games=games[:-1]))        # a game went missing
    with pytest.raises(CU.FrozenError):
        CU.assert_frozen(row, dict(entry, group=[(2.0, 3.0), (5.0, 5.0)]))


def rows_for(counts_missed, counts_cleared, games=12):
    # games are assigned at random: a cyclic assignment gives every game the same
    # mix, and a block bootstrap over identical blocks has almost no variance
    out, rng = [], np.random.default_rng(11)
    for result, counts in (("missed", counts_missed), ("cleared", counts_cleared)):
        for inp, k in counts.items():
            for _ in range(k):
                out.append({"result": result, "flip_se": inp,
                            "game_id": f"g{int(rng.integers(0, games))}"})
    return out


def run_verdict(rows):
    s, t = CU.slice_tests("all", rows, np.random.default_rng(1))
    CL.holm(t)
    return CU.verdict(s, t)


def test_the_verdict_reaches_all_three_answers():
    same = run_verdict(rows_for({"own_mean": 150, "group_mean": 40, "dispersion": 10},
                                {"own_mean": 150, "group_mean": 40, "dispersion": 10}))
    assert same["verdict"] == "same_ranking"
    assert same["t2_interval_contains_zero"]
    assert "not a diagnostic of a miss" in same["cleared_statement"]

    moved = run_verdict(rows_for({"own_mean": 40, "group_mean": 150, "dispersion": 10},
                                 {"own_mean": 150, "group_mean": 40, "dispersion": 10}))
    assert moved["verdict"] == "scale_dependent"
    assert moved["words"] == "c-40's 66.1% was scale-dependent"
    assert moved["first_input"] == "group_mean"

    weak = run_verdict(rows_for({"own_mean": 70, "group_mean": 66, "dispersion": 64},
                                {"own_mean": 70, "group_mean": 66, "dispersion": 64}))
    assert weak["verdict"] == "same_first_input_share_not_above_null"

    about = run_verdict(rows_for({"own_mean": 190, "group_mean": 5, "dispersion": 5},
                                 {"own_mean": 80, "group_mean": 60, "dispersion": 60}))
    assert about["inputs_over_represented_on_misses"] == ["own_mean"]
    assert not about["t2_interval_contains_zero"]


def test_the_null_is_one_third_and_a_flat_bootstrap_is_p_one():
    rows = rows_for({"own_mean": 100}, {"own_mean": 100})
    s, t = CU.slice_tests("all", rows, np.random.default_rng(1))
    t1 = {x["input"]: x for x in t if x["test"] == "T1"}
    assert t1["own_mean"]["estimate"] == pytest.approx(1 - 1 / 3)
    assert all(x["p"] == 1.0 for x in t)                 # every share is constant across draws


def test_duplicating_rows_inside_a_game_does_not_narrow_the_interval():
    rng = np.random.default_rng(7)
    base = [{"result": "missed" if rng.random() < 0.5 else "cleared",
             "flip_se": CU.INPUTS[int(rng.integers(0, 3))], "game_id": f"g{n % 15}"}
            for n in range(300)]
    _, t1 = CU.slice_tests("all", base, np.random.default_rng(3))
    _, t20 = CU.slice_tests("all", base * 20, np.random.default_rng(3))
    for a, b in zip(t1, t20):
        assert b["se"] == pytest.approx(a["se"], rel=1e-9)


def test_reading_follows_the_registered_wording():
    assert CU.reading({"ratio_to_mde": 3.0, "p_holm": 0.001}) == "detected"
    assert CU.reading({"ratio_to_mde": 1.1, "p_holm": 0.01}) == "at its MDE"
    assert CU.reading({"ratio_to_mde": 0.4, "p_holm": 1.0}) == "not detected"
    assert CU.reading({"ratio_to_mde": None, "p_holm": 1.0}) == "no variance"
