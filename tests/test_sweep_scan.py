"""Brief 022 Part 3 scan: the pure pieces, and the fast bootstrap's identity
with common.boot."""
import random
from datetime import datetime

import pytest

from research.sweep import common as S
from research.sweep import scan as X


def _rows(seed=3, games=9, per=7):
    rng = random.Random(seed)
    out = []
    for g in range(games):
        shift = rng.gauss(0, 1)
        for _ in range(per):
            x = rng.gauss(0, 1)
            out.append({"game": f"g{g}", "x": x, "y": 0.4 * x + shift + rng.gauss(0, 1), "v": shift + rng.gauss(0, 1)})
    return out


def test_fast_mean_boot_is_identical_to_common_boot():
    rows = _rows()
    a, b = X.mean_boot(rows, "v"), S.boot(rows, S.mean_of("v"))
    for k in ("est", "lo", "hi", "se", "p", "games", "n"):
        assert a[k] == pytest.approx(b[k], rel=1e-9, abs=1e-12)


def test_fast_slope_boot_is_identical_to_common_boot():
    rows = _rows(seed=5)
    a, b = X.slope_boot(rows, "x", "y"), S.boot(rows, S.slope_of("x", "y"))
    for k in ("est", "lo", "hi", "se", "p", "games"):
        assert a[k] == pytest.approx(b[k], rel=1e-9, abs=1e-12)


def test_constraint_arbitrage_conditions():
    assert X.c1_violation(game_ask=0.55, spread_bid=0.56)
    assert not X.c1_violation(game_ask=0.60, spread_bid=0.52)
    assert X.c2_violation(0.52, 0.49) and not X.c2_violation(0.45, 0.49)


def test_favourite_margin_uses_home_positive_convention():
    hist = [(3.0, 7), (-3.0, 7), (3.5, -4), (10.0, 3), (0.0, 5)]
    # home fav by 3 won by 7 -> +7; away fav by 3, home won by 7 -> -7; pick'em excluded
    assert X.fav_margins(hist, 3.0) == sorted([7, -7, -4])


def test_shift_fit_and_rung_prices():
    sample = sorted([-3, 0, 3, 3, 7, 7, 10, 14, 3, 1])
    d = X.fit_shift(sample, 3.5, 0.5)
    assert abs(X.p_greater(sample, 3.5, d) - 0.5) <= 0.1
    assert X.p_greater(sample, 100, 0) == 0.0 and X.p_less(sample, -100, 0) == 0.0
    # underdog "wins by over 2.5" = favourite margin < -2.5
    assert X.p_less(sample, -2.5, 0.0) == pytest.approx(1 / 10)


def test_fit_shift_prefers_the_smallest_shift_on_ties():
    sample = sorted([0, 10])
    assert X.fit_shift(sample, 5.5, 0.5) == 0.0


def test_slot_classification():
    et = S.ET
    ts = lambda *a: datetime(*a, tzinfo=et).timestamp()
    assert X.slot_of(ts(2026, 9, 13, 13, 0)) == "sun 1pm"
    assert X.slot_of(ts(2026, 9, 13, 16, 25)) == "sun late"
    assert X.slot_of(ts(2026, 9, 13, 20, 20)) == "snf"
    assert X.slot_of(ts(2026, 9, 14, 20, 15)) == "wed/thu/mon prime"


def test_grid_respects_both_window_ends_and_the_cap():
    g = X.grid_times(0, 1000, 100, cap=1000)
    assert g[0] == 100 and g[-1] == 900
    assert len(X.grid_times(0, 10_000_000, 60, cap=150)) == 150
    assert X.grid_times(0, 150, 100) == []


def test_asof_rejects_stale_and_one_sided():
    q = X.Quotes([(0, 0.4, 0.5), (100, None, 0.5), (1000, 0.41, 0.52)])
    assert X.Quotes.asof(q, 50) == (0, 0.4, 0.5)
    assert q.asof(150) is None                       # latest state one-sided
    assert q.asof(1700) is None                      # older than 660s
    assert q.last_two_sided_before(1000) == (0, 0.4, 0.5)


def test_top_decile_and_sign():
    rows = [{"x": v} for v in (0, 0, 1, -5, 2, 0, 0, 0, 0, 0, 0)]
    assert [r["x"] for r in X.top_decile(rows, "x")] == [-5, 2]
    assert X.sign(-3) == -1 and X.sign(0) == 0


def test_team_outcome_orientation():
    g = {"home": "HOU", "away": "BUF", "hs": 31, "as": 36}
    assert X.team_outcome({"series": "KXNFLGAME", "team": "BUF", "line": None}, g) == 1.0
    assert X.team_outcome({"series": "KXNFLSPREAD", "team": "HOU", "line": 1.5}, g) == 0.0
    assert X.team_outcome({"series": "KXNFLSPREAD", "team": "BUF", "line": 4.5}, g) == 1.0
    assert X.team_outcome({"series": "KXNFLTOTAL", "team": None, "line": 66.5}, g) == 1.0


def test_week2_market_raises_through_the_quote_loader():
    with pytest.raises(S.HoldoutViolation):
        X.quotes(None, "KXNFLSPREAD-26SEP17DETBUF-BUF4")
