"""Small statistics the whole project shares, so a published number means one
thing wherever it appears.

THE WILSON INTERVAL IS HERE BECAUSE TWO COPIES ALREADY DIVERGED. As of
2026-09-16 `research/longshot.py` clamps its result to [0, 1] and returns
(0.0, 0.0) at n = 0, while `research/sweep/common.py` returns NaN and does not
clamp. Both are defensible inside their own scripts; neither is safe to pick at
random for a number the SITE publishes. This module is the one the export uses.

The research copies are deliberately left alone: their outputs are pinned in
CLAUDE.md, and quietly changing a NaN to a 0.0 underneath a recorded finding is
how a reproducible number stops being reproducible. Unifying them is a separate,
deliberate change with a re-run attached.

Why Wilson at all, rather than a normal approximation: at n = 11 and p = 0.09 a
normal interval runs below zero. Every naive standard error this project has
quoted at small n has been wrong in that direction, and the longshot-bias
finding was corrected precisely because a Wilson interval contained the priced
value where a normal one did not.
"""
import math


def wilson(k, n, z=1.96):
    """Wilson score interval for k successes in n trials. -> (lo, hi).

    n = 0 returns (0.0, 0.0): there is no interval, and NaN in a published JSON
    field is not representable anyway - it serialises to something no JSON
    parser accepts. Callers that need "unknown" should omit the field or check
    n themselves rather than read a degenerate interval as a measurement.

    Clamped to [0, 1]. The closed form can overshoot by a float epsilon at
    k = n, and a probability of 1.0000000000000002 on a page is the kind of
    detail that makes a reader doubt every other number on it.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (min(max((centre - half) / d, 0.0), 1.0),
            min(max((centre + half) / d, 0.0), 1.0))
