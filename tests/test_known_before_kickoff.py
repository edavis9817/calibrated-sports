"""c-34 - the as-of rules, the stat-level fit and the multiplier, on synthetic data.

No store is opened: every function under test is pure. The scoring functions are
c-24's and are asserted to be IMPORTED, never redefined.
"""
import ast
import os

import numpy as np
import pytest

from research import known_before_kickoff as kb
from research import ranking_calibration as rc

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "research", "known_before_kickoff.py")
K = 1_700_000_000.0          # a kickoff
H = 3600.0


# ---- the as-of rules ---------------------------------------------------------

def test_injury_row_is_provable_only_strictly_before_kickoff_and_inside_the_window():
    assert kb.injury_row_provable(K - 47 * H, K) == (True, "ok")
    assert kb.injury_row_provable(K, K)[0] is False                      # AT kickoff is not before it
    assert kb.injury_row_provable(K + 1, K)[1] == "stamped at or after kickoff"
    assert kb.injury_row_provable(None, K)[1] == "no stamp"
    assert kb.injury_row_provable(K - 9 * 86400, K)[1] == "stamped more than 8 days before kickoff"


def test_one_unprovable_row_excludes_the_whole_team_game():
    good = [("a", "Out", "DNP", K - 50 * H), ("b", None, "Full", K - 40 * H)]
    assert kb.team_report_usable(good, K) == (True, "ok")
    late = good + [("c", "Questionable", "Limited", K + 5 * H)]
    assert kb.team_report_usable(late, K)[0] is False
    assert kb.team_report_usable([], K) == (False, "no rows for the team-week")


def test_version_in_force_never_returns_a_later_capture():
    vers = [(K - 100 * H, K - 10 * H, "wednesday"), (K - 10 * H, None, "friday")]
    assert kb.version_in_force(vers, K - 3 * H) == "friday"
    assert kb.version_in_force(vers, K - 50 * H) == "wednesday"
    assert kb.version_in_force(vers, K - 200 * H) is None                # nothing captured yet
    assert kb.version_in_force([(K + H, None, "postgame")], K) is None   # captured after the cut


def test_last_snapshot_is_strictly_before_the_cut_and_not_stale():
    dts = [K - 30 * 86400, K - 2 * 86400, K - 3 * H, K + H]
    assert kb.last_snapshot_before(dts, K, K) == 2
    assert kb.last_snapshot_before(dts, K - 3 * H, K) == 1               # a dt AT the cut is not before it
    assert kb.last_snapshot_before(dts[:1], K, K) is None                # 30 days old: stale
    assert kb.last_snapshot_before([K + H], K, K) is None                # only a later snapshot exists


# ---- the fit -----------------------------------------------------------------

def _planted(n=6000, effect=0.4, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        e0 = float(rng.uniform(2, 8))
        flag = float(i % 5 == 0)
        y = float(rng.poisson(e0 * np.exp(0.05 + effect * flag)))
        rows.append((2020 + i % 3, y, e0, {"own_Q": flag} if flag else {}))
    return rows


def test_poisson_fit_recovers_a_planted_effect_and_a_planted_null():
    fit = kb.fit_arm(_planted(effect=0.4), ("own_Q",), 2023)
    assert fit["beta"]["own_Q"] == pytest.approx(0.4, abs=0.04)
    null = kb.fit_arm(_planted(effect=0.0), ("own_Q",), 2023)
    assert abs(null["beta"]["own_Q"]) < 0.04                             # the other answer


def test_fit_refuses_a_training_season_at_or_after_the_test_season():
    with pytest.raises(kb.LeakError):
        kb.fit_arm(_planted(), ("own_Q",), 2022)                         # rows include 2022
    kb.fit_arm(_planted(), ("own_Q",), 2023)


def test_a_rare_feature_is_dropped_and_listed_not_fitted():
    rows = _planted()
    rows[0] = (rows[0][0], rows[0][1], rows[0][2], {"own_DO": 1.0})
    fit = kb.fit_arm(rows, ("own_Q", "own_DO"), 2023)
    assert "own_DO" not in fit["beta"]
    assert ("own_DO", 1) in fit["dropped"]


# ---- the multiplier ----------------------------------------------------------

def test_multiplier_excludes_the_intercept_and_is_exactly_one_without_information():
    beta = {"own_Q": -0.1, "vac_same_new": 0.6}
    assert kb.multiplier(beta, {}) == 1.0
    assert kb.multiplier(beta, {"own_LP": 1.0}) == 1.0                   # a feature with no coefficient
    assert kb.multiplier(beta, {"vac_same_new": 0.25}) == pytest.approx(np.exp(0.15))
    assert kb.multiplier({"x": 5.0}, {"x": 1.0}) == kb.M_CLIP[1]
    assert kb.multiplier({"x": -5.0}, {"x": 1.0}) == kb.M_CLIP[0]


def test_rows_without_information_keep_the_baseline_probability_bit_for_bit():
    p = np.array([0.5143248182706371, 0.04628643334736304, 0.9])
    out = kb.apply_multiplier(p, np.ones(3), 1.5, np.array([5.5, 5.5, 1.5]))
    assert (out == p).all()


def test_implied_mean_round_trips_and_a_multiplier_moves_the_right_way():
    line = np.array([4.5, 12.5, 0.5])
    mu = np.array([4.2, 14.0, 0.8])
    p = kb.nb_over(mu, 1.6, line)
    assert kb.implied_mu(p, 1.6, line) == pytest.approx(mu, rel=1e-6)
    up = kb.apply_multiplier(p, np.full(3, 1.2), 1.6, line)
    down = kb.apply_multiplier(p, np.full(3, 0.8), 1.6, line)
    assert (up > p).all() and (down < p).all()
    assert up == pytest.approx(kb.nb_over(mu * 1.2, 1.6, line), abs=1e-9)


# ---- the scoring is c-24's ----------------------------------------------------

def test_scoring_functions_are_imported_not_redefined():
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not defined & {"corp", "auc", "wauc", "brier", "pav", "dsc", "mcb"}
    assert kb.rc is rc
    # and the calls resolve: every rc.<name> / du.<name> the script uses exists
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "rc":
                assert hasattr(rc, node.attr), node.attr
            if node.value.id == "du":
                assert hasattr(kb.du, node.attr), node.attr


def test_verdict_words_reach_all_three_answers():
    assert kb.verdict({"lo": 0.0001, "hi": 0.001}) == "resolution rises"
    assert kb.verdict({"lo": -0.001, "hi": -0.0001}) == "resolution falls"
    assert kb.verdict({"lo": -0.001, "hi": 0.001}) == "no rise in resolution detected"
    assert kb.verdict({"lo": None, "hi": None}) == "not read"


def test_budget_shaped_and_tuning_constants_are_not_default_arguments_of_the_as_of_rules():
    # the window and the lead are read at call time, so a test (or a correction) can move them
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for d in fn.args.defaults:
            if isinstance(d, ast.Name):
                assert d.id not in ("WINDOW_DAYS", "P2_LEAD", "MIN_ROWS", "MIN_WEEKS"), (fn.name, d.id)
