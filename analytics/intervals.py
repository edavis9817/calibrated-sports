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
