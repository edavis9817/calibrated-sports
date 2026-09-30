"""c-32: the pure pieces of research/open_line_move.py."""
import math

import numpy as np
import pytest
from scipy.stats import norm

from research import open_line_move as O


def _g(gid, season, kick, home, away):
    return {"game_id": gid, "season": season, "kickoff_ts": kick, "home": home, "away": away}


def test_info_cut_is_the_later_previous_game_plus_finish():
    games = [_g("a", 2026, 100.0, "KC", "BUF"), _g("b", 2026, 500.0, "DAL", "KC"),
             _g("c", 2026, 900.0, "KC", "NYG"), _g("d", 2025, 800.0, "NYG", "PHI")]
    assert O.info_cut(games, 2) == 500.0 + O.FINISH           # KC's week-2 game, not NYG's 2025 game
    assert O.info_cut(games, 0) is None                       # nothing earlier this season


def test_info_cut_folds_franchise_moves():
    games = [_g("a", 2026, 100.0, "OAK", "KC"), _g("b", 2026, 900.0, "LV", "DEN")]
    assert O.info_cut(games, 1) == 100.0 + O.FINISH


def test_eligible_refuses_whole_lines_wide_books_and_tails():
    ok = {"bid": 0.48, "ask": 0.50}
    assert O.eligible(ok, 3.5)
    assert not O.eligible(ok, 3.0)                            # whole number: can push
    assert not O.eligible({"bid": 0.40, "ask": 0.55}, 3.5)    # 15c wide
    assert not O.eligible({"bid": 0.03, "ask": 0.05}, 3.5)    # tail
    assert O.eligible({"bid": 0.40, "ask": 0.50}, 3.5)        # exactly 10c is allowed


def test_implied_mean_recovers_the_mean_of_a_normal_ladder():
    mu, sd = 4.3, 13.5
    pts = [(x, float(norm.sf(x, mu, sd))) for x in (-2.5, 1.5, 3.5, 6.5, 9.5)]
    assert O.implied_mean(pts, sd) == pytest.approx(mu, abs=1e-9)
    assert O.implied_mean(pts[:1], sd) is None


def test_crossing_median_interpolates_and_refuses_without_a_bracket():
    assert O.crossing_median([(1.5, 0.6), (3.5, 0.4)]) == pytest.approx(2.5)
    assert O.crossing_median([(1.5, 0.7), (3.5, 0.6)]) is None


def test_spread_point_complements_the_away_ladder():
    # away team 'by over 3.5' at 0.30 means P(home margin > -3.5) = 0.70
    assert O.spread_point("BUF", "KC", "BUF", 3.5, 0.30) == (-3.5, pytest.approx(0.70))
    assert O.spread_point("KC", "KC", "BUF", 3.5, 0.30) == (3.5, 0.30)
    with pytest.raises(ValueError):
        O.spread_point("NYG", "KC", "BUF", 3.5, 0.3)


def test_ml_home_normalises_two_sides_and_complements_one():
    assert O.ml_home({"KC": 0.6, "BUF": 0.44}, "KC", "BUF") == pytest.approx(0.6 / 1.04)
    assert O.ml_home({"BUF": 0.3}, "KC", "BUF") == pytest.approx(0.7)
    assert O.ml_home({}, "KC", "BUF") is None


def test_slope_recovers_a_known_slope_and_refuses_constant_input():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 3, 400)
    y = 0.25 * x + rng.normal(0, 0.1, 400)
    b, r = O.slope_r(x, y)
    assert b == pytest.approx(0.25, abs=0.01) and r > 0.95
    assert O.slope_r(np.ones(10), y[:10]) == (None, None)


def test_shared_open_noise_produces_a_slope_from_nothing():
    """The trap addendum 1 checks for, shown firing: a model with NO information
    and a close that never moves still draws a positive slope once the open is
    measured with noise, at var(e)/(var(m)+var(e))."""
    rng = np.random.default_rng(1)
    n = 20000
    truth = rng.normal(0, 3, n)
    model = rng.normal(0, 3, n)                 # independent of everything
    e = rng.normal(0, 1, n)
    o, c = truth + e, truth                     # the close sits at the truth; the open is noisy
    b, _ = O.slope_r(model - o, c - o)
    assert b == pytest.approx(1.0 / (9 + 9 + 1), abs=0.01)
    b0, _ = O.slope_r(model - truth, c - truth)  # no noise -> no slope (y is identically zero)
    assert b0 is None


def test_pivot_trade_sides_moves_and_costs():
    fee = lambda p: 0.01                        # noqa: E731
    oq, cq = {"bid": 0.48, "ask": 0.52}, {"bid": 0.55, "ask": 0.57}
    side, mv, cost = O.pivot_trade(oq, cq, 0.60, fee)
    assert side == "yes" and mv == pytest.approx(0.06) and cost == pytest.approx(0.03)
    side, mv, cost = O.pivot_trade(oq, cq, 0.40, fee)
    assert side == "no" and mv == pytest.approx(-0.06) and cost == pytest.approx(0.03)


def test_bands_report_unsigned_and_signed_moves():
    mmo = [0.1, -0.2, 1.0, -1.5, 3.0, -4.0]
    com = [0.1, 0.2, 0.5, -0.5, 1.0, 1.0]
    b = O.bands(mmo, com)
    assert [x["n"] for x in b] == [2, 2, 2]
    assert b[0]["mean_abs_move"] == pytest.approx(0.15)
    assert b[0]["mean_signed_toward_model"] == pytest.approx((0.1 - 0.2) / 2)
    assert b[1]["ratio"] == pytest.approx(1.0)                  # both moved the model's way
    assert b[2]["mean_signed_toward_model"] == pytest.approx(0.0)


def test_wilson():
    lo, hi = O.wilson(9, 32)
    assert 0.14 < lo < 0.17 and 0.43 < hi < 0.47
    assert O.wilson(0, 0) == (None, None)
    assert math.isclose(sum(O.wilson(16, 32)), 1.0)
