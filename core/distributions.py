"""Distributions. The model's output type — never a scalar.

Invariant #3 from CLAUDE.md. A prop asks `P(X > 5.5)`, not "what's the mean",
and two models with identical means can price a line very differently. Anything
in this system that predicts emits one of these objects.

Families are chosen from the empirical moments measured in research/
(2016-2024 nflverse):

    receptions (WR/TE)   mean 3.75   var/mean 1.69   skew 0.83   P(0) 6.1%
    rush attempts (RB)   mean 12.1   var/mean 3.72   skew 0.45   P(0) 1.2%
    receiving yards      mean 47.7   var/mean 28.6   skew 1.15   P(0) 5.8%

Variance-to-mean above 1 means overdispersed: a Poisson is wrong for
receptions and badly wrong for carries. Add right skew and a ~6% mass at zero
and a normal approximation misprices exactly the region a line sits in.

Empirical is the important one in practice: the game simulator emits joint
samples per game, so every prop is a query against a sample array and
correlation is structural rather than bolted on (invariant #4).
"""
from __future__ import annotations

import math
from typing import Protocol, Sequence, runtime_checkable

import numpy as np
from scipy import stats


@runtime_checkable
class Distribution(Protocol):
    """Minimum interface. A model that cannot answer prob_over is not finished."""

    def sample(self, n: int, rng: np.random.Generator | None = ...) -> np.ndarray: ...
    def mean(self) -> float: ...
    def cdf(self, x: float) -> float: ...
    def prob_at_most(self, x: float) -> float: ...

    def prob_over(self, line: float, push: bool = ...) -> float: ...


def _rng(rng):
    return np.random.default_rng() if rng is None else rng


class _Base:
    """Shared prob_over logic, including push handling.

    Push matters and is routinely got wrong. On an integer line for a discrete
    stat the outcome can land exactly on it, and the bet is void. Then the
    quantity you price is the CONDITIONAL probability of the over given no
    push - P(X > L) / (1 - P(X = L)) - not the raw P(X > L). Ignoring this
    biases every integer-line prop.
    """

    def prob_over(self, line: float, push: bool = False) -> float:
        p_at_most = self.prob_at_most(line)
        p_over = 1.0 - p_at_most
        if not push:
            return float(min(max(p_over, 0.0), 1.0))
        p_push = self.prob_mass_at(line)
        denom = 1.0 - p_push
        if denom <= 1e-12:
            return 0.5
        return float(min(max(p_over / denom, 0.0), 1.0))

    def prob_under(self, line: float, push: bool = False) -> float:
        return 1.0 - self.prob_over(line, push)

    def prob_mass_at(self, x: float) -> float:
        return 0.0                       # continuous families: zero mass

    def cdf(self, x: float) -> float:
        return self.prob_at_most(x)

    def quantile(self, q: float, n: int = 200_000) -> float:
        return float(np.quantile(self.sample(n), q))


class NegativeBinomial(_Base):
    """Overdispersed counts: receptions, targets, carries, attempts.

    Parameterized by mean and variance-to-mean ratio, because that is what the
    research measured. r = mu / (vmr - 1), p = 1 / vmr.
    """

    def __init__(self, mean: float, var_mean_ratio: float):
        if mean <= 0:
            raise ValueError("mean must be > 0")
        if var_mean_ratio <= 1.0:
            # vmr == 1 is Poisson; below 1 is underdispersed and NB cannot
            # represent it. Nudge rather than silently produce garbage.
            var_mean_ratio = 1.0 + 1e-6
        self._mu = float(mean)
        self._vmr = float(var_mean_ratio)
        self._p = 1.0 / self._vmr
        self._r = self._mu * self._p / (1.0 - self._p)

    @classmethod
    def from_moments(cls, mean: float, variance: float) -> "NegativeBinomial":
        return cls(mean, variance / mean)

    def mean(self) -> float:
        return self._mu

    def variance(self) -> float:
        return self._mu * self._vmr

    def sample(self, n: int, rng=None) -> np.ndarray:
        return _rng(rng).negative_binomial(self._r, self._p, size=n)

    def prob_at_most(self, x: float) -> float:
        return float(stats.nbinom.cdf(math.floor(x), self._r, self._p))

    def prob_mass_at(self, x: float) -> float:
        if not float(x).is_integer() or x < 0:
            return 0.0
        return float(stats.nbinom.pmf(int(x), self._r, self._p))

    def __repr__(self):
        return f"NegativeBinomial(mean={self._mu:.2f}, vmr={self._vmr:.2f})"


class ZeroInflatedGamma(_Base):
    """Right-skewed non-negative continuous with a point mass at zero:
    receiving yards, rushing yards.

    `mean` and `variance` describe the NON-ZERO part; p_zero is the mass at 0.
    """

    def __init__(self, mean_nonzero: float, var_nonzero: float, p_zero: float):
        if mean_nonzero <= 0:
            raise ValueError("mean_nonzero must be > 0")
        if not 0.0 <= p_zero < 1.0:
            raise ValueError("p_zero must be in [0, 1)")
        self._p0 = float(p_zero)
        self._shape = mean_nonzero ** 2 / var_nonzero
        self._scale = var_nonzero / mean_nonzero

    @classmethod
    def from_overall_moments(cls, mean: float, variance: float,
                             p_zero: float) -> "ZeroInflatedGamma":
        """Build from moments of the FULL distribution, zeros included -
        which is how the research reports them."""
        q = 1.0 - p_zero
        m_nz = mean / q
        # Var(X) = q*(v_nz + m_nz^2) - (q*m_nz)^2
        v_nz = (variance + mean ** 2) / q - m_nz ** 2
        return cls(m_nz, max(v_nz, 1e-9), p_zero)

    def mean(self) -> float:
        return (1.0 - self._p0) * self._shape * self._scale

    def sample(self, n: int, rng=None) -> np.ndarray:
        r = _rng(rng)
        out = r.gamma(self._shape, self._scale, size=n)
        out[r.random(n) < self._p0] = 0.0
        return out

    def prob_at_most(self, x: float) -> float:
        if x < 0:
            return 0.0
        return float(self._p0 + (1.0 - self._p0)
                     * stats.gamma.cdf(x, self._shape, scale=self._scale))

    def __repr__(self):
        return (f"ZeroInflatedGamma(mean={self.mean():.1f}, "
                f"p_zero={self._p0:.3f})")


class Empirical(_Base):
    """A distribution defined by samples.

    This is what the game simulator produces, and it is the reason correlation
    comes for free: every player's samples come from the same simulated games,
    so a joint query over several Empiricals built from one sim run preserves
    the dependence between them (QB<->WR1 was 0.42 in the data).
    """

    def __init__(self, samples: Sequence[float]):
        self._s = np.asarray(samples, dtype=float)
        if self._s.size == 0:
            raise ValueError("Empirical needs at least one sample")
        self._sorted = np.sort(self._s)

    def mean(self) -> float:
        return float(self._s.mean())

    def n(self) -> int:
        return int(self._s.size)

    def sample(self, n: int, rng=None) -> np.ndarray:
        return _rng(rng).choice(self._s, size=n, replace=True)

    def prob_at_most(self, x: float) -> float:
        return float(np.searchsorted(self._sorted, x, side="right") / self._s.size)

    def prob_mass_at(self, x: float) -> float:
        lo = np.searchsorted(self._sorted, x, side="left")
        hi = np.searchsorted(self._sorted, x, side="right")
        return float((hi - lo) / self._s.size)

    def mc_stderr(self, line: float) -> float:
        """Monte Carlo standard error on prob_over - report it or you will
        mistake simulation noise for edge. With 10k draws the SE near p=0.5
        is ~0.5 probability points, which is the same size as a real edge."""
        p = self.prob_over(line)
        return float(math.sqrt(max(p * (1 - p), 0.0) / self._s.size))

    def __repr__(self):
        return f"Empirical(n={self.n()}, mean={self.mean():.2f})"


# --- market-side helpers -----------------------------------------------------

def american_to_prob(odds: float) -> float:
    """Vig-inclusive implied probability. De-vig separately, never at ingest."""
    o = float(odds)
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def devig_two_way(p_over: float, p_under: float,
                  method: str = "multiplicative") -> tuple[float, float]:
    """Remove the overround from a two-sided market.

    'multiplicative' (proportional) is the standard default. 'shin' and power
    methods handle favourite-longshot bias better and are worth testing once
    there is logged data to test against - which is exactly the kind of choice
    that should be settled by the CLV harness, not by preference.
    """
    if method != "multiplicative":
        raise NotImplementedError("only multiplicative de-vig implemented")
    total = p_over + p_under
    if total <= 0:
        raise ValueError("degenerate market")
    return p_over / total, p_under / total


def kalshi_fee(price: float, contracts: int = 1, maker: bool = False) -> float:
    """Kalshi fee in DOLLARS. taker ceil(0.07*C*P*(1-P)), maker 0.0175.

    Peaks at P=0.50 (~1.75c/contract) and collapses at the tails (~0.46c at
    P=0.07). That shape is why tail mispricing is the cheapest to trade and
    why exchange coin-flip lines carry no structural edge over a -110 book.
    """
    rate = 0.0175 if maker else 0.07
    raw_cents = rate * contracts * price * (1.0 - price) * 100.0
    # Guard the float before ceil. 0.07*100*0.5*0.5*100 evaluates to
    # 175.00000000000003, and a naive ceil turns an exact $1.75 into $1.76 -
    # a systematic one-cent overstatement on precisely the round numbers that
    # show up most, which quietly biases every EV calculation downstream.
    cents = math.ceil(round(raw_cents, 6))
    return cents / 100.0


def edge_after_fees(fair_prob: float, price: float, contracts: int = 1,
                    maker: bool = False) -> float:
    """Expected profit per contract in dollars, fees included.

    Positive is not sufficient - it must clear the Monte Carlo standard error
    on fair_prob as well, or you are trading simulation noise.
    """
    fee = kalshi_fee(price, contracts, maker) / max(contracts, 1)
    return fair_prob * (1.0 - price) - (1.0 - fair_prob) * price - fee
