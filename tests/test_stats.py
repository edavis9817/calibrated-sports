"""The canonical Wilson interval.

This module exists because two copies had already diverged: research/longshot.py
clamps to [0, 1] and returns (0, 0) at n = 0, while research/sweep/common.py
returns NaN and does not clamp. The site publishes intervals, so it needs one
that is safe to serialise and safe to read - and it must not become a third
variant, which is what these tests pin.
"""
import json
import math

import pytest

from core.stats import wilson


def test_it_matches_the_clamped_research_copy_exactly():
    """Canonical, not a third implementation. If this ever drifts, every
    published interval quietly stops matching the research it came from."""
    from research.longshot import wilson as clamped
    for k, n in [(0, 10), (1, 10), (5, 10), (10, 10), (1, 11), (11, 17),
                 (1, 194), (3, 3), (0, 1), (167, 1200), (23, 89)]:
        assert wilson(k, n) == pytest.approx(clamped(k, n), abs=0.0)


def test_no_interval_escapes_zero_to_one():
    """The closed form overshoots by a float epsilon at k = n. A probability of
    1.0000000000000002 on a page makes a reader doubt every other number."""
    for n in range(1, 60):
        for k in (0, 1, n // 2, n - 1, n):
            lo, hi = wilson(k, n)
            assert 0.0 <= lo <= hi <= 1.0, (k, n, lo, hi)


def test_n_zero_is_not_nan_because_nan_is_not_json():
    """json.dumps emits bare NaN, which no compliant parser accepts - the site
    would fail to load the file rather than show an empty interval."""
    assert wilson(0, 0) == (0.0, 0.0)
    assert not any(math.isnan(x) for x in wilson(0, 0))
    json.loads(json.dumps({"wilson": wilson(0, 0)}))


def test_it_contains_the_point_estimate_and_narrows_with_n():
    """The property that makes it worth publishing: at small n it is wide, and
    it is wide in a direction a normal approximation gets wrong."""
    wide = wilson(1, 11)
    narrow = wilson(100, 1100)
    assert wide[0] <= 1 / 11 <= wide[1]
    assert narrow[0] <= 100 / 1100 <= narrow[1]
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])
    # n = 11, p = 0.09: a normal interval runs below zero. This one cannot.
    assert wilson(1, 11)[0] > 0.0


def test_a_wider_z_gives_a_wider_interval():
    lo95, hi95 = wilson(11, 17)
    lo99, hi99 = wilson(11, 17, z=2.576)
    assert lo99 < lo95 and hi99 > hi95
