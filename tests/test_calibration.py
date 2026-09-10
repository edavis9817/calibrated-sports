"""Calibration-curve arithmetic. Run: pytest -q

The curve is the scoreboard, so a bug here does not produce an error - it
produces a number that looks like a finding. Every test below is a way the
arithmetic could be quietly wrong.
"""
import math

import pytest

from research import calibration as cal


def _row(p, hit, side="over", stat="receptions", season=2024, n_all=3,
         dispersion=0.01, bucket="unknown", line=5.5, actual=None):
    """result is the FACT; side is the claim. hit is derived, never set."""
    result = side if hit else ("under" if side == "over" else "over")
    if actual is None:
        actual = line + (1 if result == "over" else -1)
    return cal.Row(("oid", season, 1, stat, line, side, result, actual,
                    p, 3, p, n_all, dispersion, 14.4, bucket))


# --- the claim, the fact, and which of them is which -------------------------

def test_hit_compares_the_side_to_the_result_not_to_the_line():
    """The polarity test. An UNDER outcome that settled under is a WIN, and the
    one place this repo has already shipped a sign error is exactly here."""
    assert _row(0.5, True, side="over").hit == 1
    assert _row(0.5, False, side="over").hit == 0
    assert _row(0.5, True, side="under").hit == 1
    assert _row(0.5, False, side="under").hit == 0

    # spelled out without the helper, in case the helper is the thing that lies
    r = cal.Row(("o", 2024, 1, "receptions", 5.5, "under", "under", 3.0,
                 0.6, 3, 0.6, 3, 0.01, 14.0, "unknown"))
    assert r.hit == 1
    r = cal.Row(("o", 2024, 1, "receptions", 5.5, "under", "over", 8.0,
                 0.6, 3, 0.6, 3, 0.01, 14.0, "unknown"))
    assert r.hit == 0


# --- bucketing ---------------------------------------------------------------

def test_bins_are_half_open_and_1_0_does_not_fall_off_the_end():
    assert cal._bin(0.00) == "0.00-0.05"
    assert cal._bin(0.049) == "0.00-0.05"
    assert cal._bin(0.05) == "0.05-0.10"
    assert cal._bin(0.999) == "0.95-1.00"
    assert cal._bin(1.0) == "0.95-1.00"      # not an IndexError, not a 21st bin
    assert cal._bin(None) is None


def test_bins_sort_in_price_order_as_strings():
    """The report prints sorted(bins). Zero-padded decimals are what make that
    a price ordering rather than an alphabetical one."""
    got = [cal._bin(p / 100) for p in (7, 12, 51, 96)]
    assert got == sorted(got)
    assert got == ["0.05-0.10", "0.10-0.15", "0.50-0.55", "0.95-1.00"]


# --- the metrics -------------------------------------------------------------

def test_a_perfectly_calibrated_slice_scores_zero_error():
    """100 outcomes priced 0.70, 70 of which land. Realized equals predicted,
    so the calibration error is zero even though the Brier score is not - a
    Brier of 0.21 is what an honest 0.70 coin costs."""
    rows = [_row(0.70, i < 70) for i in range(100)]
    s = cal.score(rows)
    assert s["hit_rate"] == pytest.approx(0.70)
    assert s["mean_p"] == pytest.approx(0.70)
    assert s["ece"] == pytest.approx(0.0, abs=1e-12)
    assert s["brier"] == pytest.approx(0.21)
    assert s["log_loss"] == pytest.approx(
        -(0.7 * math.log(0.7) + 0.3 * math.log(0.3)), abs=1e-9)


def test_a_biased_slice_shows_the_bias_with_the_right_sign():
    """Priced 0.60, lands 0.50. The market was too high, so diff is negative."""
    rows = [_row(0.60, i < 50) for i in range(100)]
    c = cal.curve(rows)
    assert len(c) == 1
    assert c[0]["predicted"] == pytest.approx(0.60)
    assert c[0]["realized"] == pytest.approx(0.50)
    assert c[0]["diff"] == pytest.approx(-0.10)
    assert cal.score(rows)["ece"] == pytest.approx(0.10)


def test_log_loss_does_not_blow_up_on_a_certainty_that_missed():
    """A price of exactly 1.0 on a loser is an infinite loss. Clamping is not
    cosmetic - one such row would make every aggregate NaN and the report would
    still print."""
    s = cal.score([_row(1.0, False)])
    assert math.isfinite(s["log_loss"]) and s["log_loss"] > 15
    assert s["brier"] == pytest.approx(1.0)


def test_the_standard_error_scales_with_n():
    """Without it a 2-point gap on n=180 reads like a finding."""
    small = cal.curve([_row(0.50, i % 2 == 0) for i in range(100)])[0]
    big = cal.curve([_row(0.50, i % 2 == 0) for i in range(10000)])[0]
    assert small["se"] == pytest.approx(0.05, abs=1e-9)
    assert big["se"] == pytest.approx(0.005, abs=1e-9)
    assert abs(big["z"]) < 1e-6              # perfectly calibrated, huge n


def test_ece_weights_buckets_by_size():
    """A 20-point miss on 10 rows must not outweigh a clean 990."""
    rows = ([_row(0.50, i % 2 == 0) for i in range(990)]
            + [_row(0.90, False) for _ in range(10)])
    s = cal.score(rows)
    assert s["ece"] == pytest.approx(10 * 0.90 / 1000, abs=1e-9)


def test_an_empty_slice_returns_none_rather_than_dividing_by_zero():
    assert cal.score([]) is None
    assert cal.curve([]) == []


# --- the under skew ----------------------------------------------------------

def test_skew_measures_realized_against_PRICED_not_against_one_half():
    """The distinction the whole section exists for. Lines hung near the mean
    of a right-skewed stat make the under win 54% of the time and the price
    already says so. That is line placement, not an edge."""
    rows = [_row(0.46, i < 46, side="over") for i in range(100)]
    s = cal.skew(rows)
    assert s["all"]["priced_over"] == pytest.approx(0.46)
    assert s["all"]["realized_over"] == pytest.approx(0.46)
    assert s["all"]["diff"] == pytest.approx(0.0)     # priced correctly
    assert s["all"]["realized_under"] == pytest.approx(0.54)


def test_skew_finds_a_real_mispricing_inside_the_band():
    """Priced a coinflip, the under lands 60% of the time. That IS an edge."""
    rows = [_row(0.50, i < 400, side="over") for i in range(1000)]
    s = cal.skew(rows)
    assert s["band"]["n"] == 1000
    assert s["band"]["realized_over"] == pytest.approx(0.40)
    assert s["band"]["diff"] == pytest.approx(-0.10)
    assert s["band"]["z"] < -6


def test_the_band_excludes_prices_outside_it():
    rows = ([_row(0.50, True, side="over")] * 10
            + [_row(0.20, True, side="over")] * 10
            + [_row(0.80, True, side="over")] * 10)
    s = cal.skew(rows, lo=0.45, hi=0.55)
    assert s["all"]["n"] == 30 and s["band"]["n"] == 10


def test_skew_ignores_the_under_side_so_it_cannot_double_count():
    """Every line produces two outcomes that are exact complements. Counting
    both makes the aggregate symmetric by construction and hides the very
    asymmetry being measured."""
    rows = ([_row(0.50, False, side="over")] * 100
            + [_row(0.50, True, side="under")] * 100)
    assert cal.skew(rows)["all"]["n"] == 100


def test_margin_is_the_line_placement_axis_and_carries_no_price():
    """actual - line, in stat units. Independent of what anything was priced
    at, which is what makes it able to separate the two explanations."""
    rows = [_row(0.5, True, side="over", line=5.5, actual=a)
            for a in (2, 3, 4, 9, 40)]
    s = cal.skew(rows)
    assert s["all"]["median_margin"] == pytest.approx(-1.5)
    assert s["all"]["mean_margin"] > s["all"]["median_margin"]   # right skew
