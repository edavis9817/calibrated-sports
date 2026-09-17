"""Estimates that cannot exist without their interval and their sample count.

`analytics.gate` refuses a published rate that arrives without its apparatus.
This module is the other half: a type that cannot be CONSTRUCTED without it, so
the gate is catching mistakes rather than being the only thing between a point
estimate and the export.

Three constructions, chosen per the project's own record:

- `wilson` for a proportion. CLAUDE.md, brief 022: quote Wilson, not a
  normal-approximation SE, on any bucket with n in the hundreds at a low rate -
  the normal interval put a priced value outside a bucket it was inside.
- `block_bootstrap` for anything whose observations share a game. A slate
  shares a scoring environment and a ladder's rungs are one claim; brief 018's
  guard duplicates every row 20x inside its own game and asserts the interval
  does NOT narrow. The same guard is in `tests/test_analytics_gate.py`.
- `student_t` for a mean over independent units, which in this package means
  units that are already one-per-game.

`n` IS THE NUMBER OF INDEPENDENT UNITS, not the number of rows. A block
bootstrap records `n` as the block count and `rows` as the raw count, because
those differ by an order of magnitude and only one of them is the sample.
"""
import math
from dataclasses import dataclass, asdict

_Z = {0.90: 1.6448536269514722, 0.95: 1.959963984540054, 0.99: 2.5758293035489004}


@dataclass(frozen=True)
class Estimate:
    """A number, its interval, and the sample it was computed on.

    `flat(prefix)` renders it as the column names `analytics.gate` requires.
    """
    est: float
    lo: float
    hi: float
    n: int                      # independent units
    method: str
    rows: int = None            # raw observations, when they exceed the units

    def __post_init__(self):
        if self.n is None or self.n < 0:
            raise ValueError("an estimate without a sample count is not publishable")
        if self.est is not None and not (self.lo <= self.est <= self.hi):
            raise ValueError(f"interval [{self.lo}, {self.hi}] excludes {self.est}")

    @property
    def excludes_zero(self) -> bool:
        return self.lo > 0 or self.hi < 0

    @property
    def readable(self) -> bool:
        """Brief 020: an interval over fewer than 5 blocks is not read,
        whatever it excludes. Three identical wins bootstrap tight."""
        return self.n >= 5

    def flat(self, prefix: str) -> dict:
        return {prefix: self.est, f"{prefix}_lo": self.lo, f"{prefix}_hi": self.hi,
                f"{prefix}_n": self.n, f"{prefix}_method": self.method}

    def as_dict(self) -> dict:
        d = asdict(self)
        if d["rows"] is None:
            d.pop("rows")
        return d


def wilson(hits: int, n: int, conf: float = 0.95) -> Estimate:
    """Wilson score interval for a proportion."""
    if n == 0:
        return Estimate(None, 0.0, 1.0, 0, f"wilson{int(conf*100)}")
    z = _Z[conf]
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return Estimate(p, max(0.0, (c - half) / d), min(1.0, (c + half) / d), n,
                    f"wilson{int(conf*100)}")


# Student-t quantiles at 0.975, df 1..30, then the normal limit. A table, not
# scipy: this module is imported by the gate's own test and must not need it.
_T975 = [None, 12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262,
         2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093,
         2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045,
         2.042]


def student_t(values, conf: float = 0.95) -> Estimate:
    """Mean of independent units. `values` is one number per unit."""
    xs = [v for v in values if v is not None and not _isnan(v)]
    n = len(xs)
    if n == 0:
        return Estimate(None, float("-inf"), float("inf"), 0, "t95")
    m = sum(xs) / n
    if n == 1:
        return Estimate(m, float("-inf"), float("inf"), 1, "t95")
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(var / n)
    df = n - 1
    t = _T975[df] if df < len(_T975) else _Z[conf]
    if conf != 0.95:
        t = _Z[conf]
    return Estimate(m, m - t * se, m + t * se, n, f"t{int(conf*100)}")


def block_bootstrap(blocks, statistic, draws: int = 2000, conf: float = 0.95,
                    seed: int = 20260917) -> Estimate:
    """Resample BLOCKS with replacement; `statistic(rows)` over the pooled rows.

    `blocks` is a mapping or an iterable of (key, rows). The block is the unit
    of independence - a game, here - and `n` is the number of blocks, because
    that is the sample. Rows within a block are not independent and counting
    them as the sample is roughly a sqrt(rows-per-block) lie about the width.
    """
    import random
    items = list(blocks.items() if hasattr(blocks, "items") else blocks)
    items = [(k, list(v)) for k, v in items if len(list(v)) > 0]
    n = len(items)
    rows = sum(len(v) for _k, v in items)
    if n == 0:
        return Estimate(None, float("-inf"), float("inf"), 0, f"block{draws}", 0)
    point = statistic([r for _k, v in items for r in v])
    if n == 1:
        return Estimate(point, float("-inf"), float("inf"), 1, f"block{draws}", rows)
    rng = random.Random(seed)
    out = []
    for _ in range(draws):
        pooled = []
        for _ in range(n):
            pooled.extend(items[rng.randrange(n)][1])
        s = statistic(pooled)
        if s is not None and not _isnan(s):
            out.append(s)
    if not out:
        return Estimate(point, float("-inf"), float("inf"), n, f"block{draws}", rows)
    out.sort()
    lo = out[int((1 - conf) / 2 * len(out))]
    hi = out[min(len(out) - 1, int((1 + conf) / 2 * len(out)))]
    # The percentile interval need not contain the point estimate when the
    # statistic is skewed. Widen rather than raise in __post_init__: a refused
    # construction here would lose a real, if awkward, result.
    return Estimate(point, min(lo, point), max(hi, point), n, f"block{draws}", rows)


def _isnan(v) -> bool:
    return isinstance(v, float) and v != v


# ---------------------------------------------------------------------------
# the same block bootstrap, over histograms
# ---------------------------------------------------------------------------

def _counts_matrix(n, draws, seed, subject):
    """draws x n multinomial counts. NOT CACHED ACROSS SUBJECTS - see below.

    An earlier version memoised this on (n, draws, seed), so every subject with
    the same number of blocks was resampled with the SAME draws: common random
    numbers. THE COMMENT JUSTIFYING IT WAS WRONG TWICE.

    On the product: it said nothing published is a contrast between two
    subjects. The site's idiom is two players' intervals side by side, and a
    reader comparing them is performing an informal contrast.

    On the cost: it said generating fresh draws per subject was ~99% of the
    runtime. Measured, it is 0.0008-0.0077s per subject by block count - 22 to
    208 seconds across all 27,000 player-slices. The saving was about two
    minutes.

    And the statistics go the wrong way. `analytics/crn_check.py` measures the
    variance of the gap a reader eyeballs, against the per-game correlation
    between the two subjects, relative to independent draws:

        rho     -0.9   -0.6   -0.3    0.0   +0.3   +0.6   +0.9
        cached  1.571  1.226  1.078  1.026  0.981  0.952  0.980

    Above 1 is ANTI-conservative: more noise in the comparison than the
    intervals advertise, so two of them separate when they should not. Two
    receivers on one team share a denominator and measure rho = -0.215 on the
    2024 slate, which is the anti-conservative half - and teammates side by
    side is precisely the comparison this site invites. At rho = -0.9 the gap
    carries 57% more variance than it appears to.

    A per-subject column permutation was measured too and is flat across the
    whole range (0.985-1.020), so it would have worked. It is not used: two
    minutes does not buy an extra mechanism to reason about.
    """
    import numpy as np
    import zlib
    # zlib.crc32, NOT the builtin hash(): str hashing is salted per process, so
    # a seed built from it would give a different interval on every run and the
    # figures would not reproduce. Deterministic, and it is only a seed.
    key = zlib.crc32(str(subject).encode("utf-8"))
    rng = np.random.default_rng([seed, n, key])
    return rng.multinomial(n, np.full(n, 1.0 / n), size=draws).astype(float)


def histogram_bootstrap(hist_by_block, statistics, draws: int = 2000,
                        conf: float = 0.95, seed: int = 20260917,
                        rows_by_block=None, subject: str = "") -> dict:
    """Block bootstrap where each block is an integer HISTOGRAM, not a list.

    WHY THIS EXISTS. The air-yard metrics need twelve statistics per player -
    seven bin shares, four quantiles and a polarity - over 1,571 receivers.
    Done as twelve independent `block_bootstrap` calls over raw values that is
    roughly 5e9 python-level operations and does not finish. Air yards are
    INTEGERS in [-93, 78] (measured, not assumed: 0 of 712,224 rows are
    fractional), so a game's targets compress losslessly into a fixed-width
    count vector, resampling games becomes summing rows of a matrix, and every
    statistic is a function of the summed histogram.

    It is the same estimator. Blocks are resampled with replacement, `n` is the
    number of blocks, and all statistics share one set of resamples - which
    also keeps the bin shares coherent with the quantiles drawn beside them.

    IT IS NOT ONLY FOR HISTOGRAMS. Any statistic that can be written as a
    function of SUMS over the sample works the same way - `analytics.stability`
    passes a six-wide vector of Pearson sufficient statistics per player and
    gets the identical estimator for a correlation. What the vector holds is
    the caller's business; all this needs is that summing vectors is the same
    as pooling the rows behind them.

    `hist_by_block` is {block_key: 1-D numeric array}. `statistics` is
    {name: fn(vector) -> float or None}. Returns {name: Estimate}.

    `rows_by_block` says how many raw observations a block's vector stands for.
    A histogram knows - it is the sum of its bins - but a vector of sufficient
    statistics does not, and a rule inferring it from the vector would be right
    for one caller and quietly wrong for the other. Default is the histogram
    rule; the other caller passes it.
    """
    import numpy as np
    def _rows(k):
        if rows_by_block is not None:
            return float(rows_by_block[k])
        return float(np.asarray(hist_by_block[k]).sum())

    keys = [k for k in hist_by_block if _rows(k) > 0]
    n = len(keys)
    rows = int(sum(_rows(k) for k in keys))
    if n == 0:
        return {name: Estimate(None, float("-inf"), float("inf"), 0,
                               "hist%d" % draws, 0) for name in statistics}
    # dtype is the caller's: integer counts for a histogram, float sums for
    # sufficient statistics. Casting to int64 here truncated the second use.
    mat = np.vstack([np.asarray(hist_by_block[k], dtype=float) for k in keys])
    total = mat.sum(axis=0)
    point = {name: fn(total) for name, fn in statistics.items()}
    if n == 1:
        return {name: Estimate(point[name], float("-inf"), float("inf"), 1,
                               "hist%d" % draws, rows) for name in statistics}
    # counts[d, b] = how many times block b was drawn in replicate d. One
    # multinomial per replicate is exactly sampling n blocks with replacement,
    # and it turns the whole bootstrap into a single matrix product.
    #
    # `subject` makes the draws INDEPENDENT BETWEEN SUBJECTS. Callers pass
    # something stable and distinct - a player id - so a rerun reproduces, and
    # two subjects a reader will put side by side never share a resample. See
    # `_counts_matrix` for the measurement that settled it.
    counts = _counts_matrix(n, draws, seed, subject)
    replicates = counts @ mat
    out = {}
    for name, fn in statistics.items():
        vals = [fn(r) for r in replicates]
        vals = [v for v in vals if v is not None and not _isnan(v)]
        if not vals or point[name] is None:
            out[name] = Estimate(point[name], float("-inf"), float("inf"), n,
                                 "hist%d" % draws, rows)
            continue
        vals.sort()
        lo = vals[int((1 - conf) / 2 * len(vals))]
        hi = vals[min(len(vals) - 1, int((1 + conf) / 2 * len(vals)))]
        out[name] = Estimate(point[name], min(lo, point[name]),
                             max(hi, point[name]), n, "hist%d" % draws, rows)
    return out


def share_bootstrap(blocks, draws: int = 2000, conf: float = 0.95,
                    seed: int = 20260917, extra=None, subject: str = "") -> dict:
    """A ratio of sums, block-bootstrapped. `blocks` is {key: (num, denom)}.

    Every share in this package has this shape - a player's targets over his
    team's, his snaps in a bucket over his team's plays in it - and a ratio of
    sums is additive, so it is a `histogram_bootstrap` over two-wide vectors
    rather than a python loop that re-pools thousands of tuples per draw. Same
    estimator, same `n` (blocks), about two orders of magnitude faster.

    `extra` may name further statistics over the summed vector, which is how
    the game-script elasticity gets its two bucket shares AND their difference
    out of ONE set of resamples: a difference bootstrapped alongside its parts
    is one quantity, which is what brief 018 required and what quoting two
    separate means does not give.
    """
    import numpy as np
    vecs = {k: np.array([float(a), float(b)]) for k, (a, b) in blocks.items()}
    stats = {"share": lambda v: (v[0] / v[1]) if v[1] else None}
    stats.update(extra or {})
    return histogram_bootstrap(vecs, stats, draws=draws, conf=conf, seed=seed,
                               rows_by_block={k: 1 for k in vecs},
                               subject=subject)
