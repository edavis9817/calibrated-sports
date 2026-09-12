"""Longshot analysis arithmetic. Run: pytest -q

The failure mode this file guards is the one the whole brief exists to avoid:
a small-sample effect reported as though it were established. Every test here
is about not overstating what n supports.
"""
import math

import pytest

from research.longshot import FINE_EDGES, fine_bucket, wilson


def test_wilson_never_leaves_the_unit_interval():
    """A normal interval at n=11, p=0.09 runs below zero, and a negative
    probability in a report is how a reader learns to distrust the whole
    table."""
    for n in (1, 5, 11, 30, 500):
        for k in range(0, n + 1):
            lo, hi = wilson(k, n)
            assert 0.0 <= lo <= hi <= 1.0, f"k={k} n={n} -> [{lo}, {hi}]"


def test_wilson_is_wide_at_the_sample_the_brief_is_worried_about():
    """n=11 at one hit. The brief's whole point: this cannot resolve 5.8pp."""
    lo, hi = wilson(1, 11)
    assert hi - lo > 0.30, "an interval this narrow would be a bug"
    assert lo < 0.125 < hi, "the priced value sits inside - nothing is shown"


def test_wilson_narrows_as_n_grows():
    widths = [wilson(round(0.125 * n), n)[1] - wilson(round(0.125 * n), n)[0]
              for n in (25, 100, 400, 1600)]
    assert widths == sorted(widths, reverse=True)
    # halving the width takes 4x the sample: 25 -> 100 is exactly that
    assert widths[0] / widths[1] == pytest.approx(2.0, rel=0.15)


def test_a_real_effect_is_detected_at_the_sample_the_power_calc_demands():
    """212 observations, priced 0.125, realized 0.067. The interval must
    exclude the priced value or the power calculation was wrong."""
    lo, hi = wilson(round(0.067 * 212), 212)
    assert hi < 0.125, "at n=212 a 5.8pp effect should be resolvable"


def test_the_original_bucket_does_not_resolve_it():
    """n=89, realized 0.0674, priced 0.1375 - the brief says ~1.9 SE."""
    lo, hi = wilson(round(0.0674 * 89), 89)
    assert lo < 0.1375, "89 observations should NOT be conclusive on their own"


def test_buckets_tile_the_longshot_zone_without_gaps_or_overlap():
    for i in range(len(FINE_EDGES) - 1):
        mid = (FINE_EDGES[i] + FINE_EDGES[i + 1]) / 2
        assert fine_bucket(mid) == f"{FINE_EDGES[i]:.3f}-{FINE_EDGES[i+1]:.3f}"
        # the lower edge belongs to this bucket, the upper to the next
        assert fine_bucket(FINE_EDGES[i]) == f"{FINE_EDGES[i]:.3f}-{FINE_EDGES[i+1]:.3f}"
    assert fine_bucket(0.30) is None      # above the zone
    assert fine_bucket(0.01) is None      # below it


def test_the_zone_is_finer_than_the_study_it_refines():
    """Brief 012 used 0.05 bins. A single 0.05 bin at the longshot end pools a
    2-to-1 range of prices, which is why the effect needed re-bucketing."""
    inside = [e for e in FINE_EDGES if 0.05 <= e <= 0.20]
    assert len(inside) >= 6
    assert max(inside[i + 1] - inside[i]
               for i in range(len(inside) - 1)) <= 0.025 + 1e-9


def test_the_kalshi_fee_is_small_relative_to_the_effect_at_these_prices():
    """The reason the finding is worth chasing - and the reason it must be
    computed at SIZE, since the per-contract rounding is 14x at P=0.12."""
    from core.distributions import kalshi_fee
    for p in (0.075, 0.10, 0.125, 0.15):
        taker = kalshi_fee(p, 1000, maker=False) / 1000
        maker = kalshi_fee(p, 1000, maker=True) / 1000
        assert taker < 0.058, f"taker fee at {p} exceeds the effect"
        assert maker < taker
        # one contract rounds up to a whole cent and overstates badly
        assert kalshi_fee(p, 1) >= taker
