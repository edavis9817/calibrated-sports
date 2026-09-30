"""The key-number margin: c-28's game forecast with an NFL-shaped margin (c-30).

c-28's `GameForecast` carries the margin as a Normal, so P(margin > L) is smooth
in L. NFL margins are not: scoring in 3s and 7s puts spikes at 3, 7, 10, 14, 6
and 4 and troughs between them, exactly where spreads sit. This object keeps
the base forecast's moneyline and replaces only the SHAPE of the margin inside
each side of zero:

    P(M = 0)          = t                         (training tie rate)
    P(M = k), k >= 1  = p (1 - t) b_k w_k / sum_{j>=1}  b_j w_j
    P(M = k), k <= -1 = (1 - p)(1 - t) b_k w_|k| / sum_{j<=-1} b_j w_|j|

with b_k the base Normal discretised on integers and w a per-|k| multiplier
(w = 1 beyond `J`). p is the base object's `p_home`, so
P(home wins | not a tie) is c-28's number exactly - the moneyline does not
move; only the spread tails do.

`fit_weights` fits w by penalised maximum likelihood of the WITHIN-SIDE shape
(the side itself is the moneyline's business). Pre-registration:
docs/C30-against-the-spread-preregistration.md.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from models.game import GameForecast

KMAX = 60          # support is [-KMAX, KMAX]
J = 24             # free weights for |k| = 1..J; w = 1 beyond
PENALTY = 2.0      # ridge on log w: a prior sd of 0.5, pulling toward the Normal
_K = np.arange(-KMAX, KMAX + 1)


def base_mass(mu, sigma):
    """(n, 2*KMAX+1) discretised Normal masses for arrays mu, sigma."""
    mu = np.atleast_1d(np.asarray(mu, float))[:, None]
    sigma = np.atleast_1d(np.asarray(sigma, float))[:, None]
    return norm.cdf((_K + 0.5 - mu) / sigma) - norm.cdf((_K - 0.5 - mu) / sigma)


def full_weights(w):
    """Per-support-point multipliers from w_1..w_J (symmetric; w_0 unused)."""
    w = np.asarray(w, float)
    if w.shape != (J,):
        raise ValueError("need %d weights, got %s" % (J, w.shape))
    a = np.abs(_K)
    out = np.ones(_K.shape)
    inside = (a >= 1) & (a <= J)
    out[inside] = w[a[inside] - 1]
    return out


def pmf(p_home, mu, sigma, w, t):
    """(n, 2*KMAX+1) margin pmf, sides pinned to p_home, tie mass t."""
    p = np.atleast_1d(np.asarray(p_home, float))[:, None]
    shaped = base_mass(mu, sigma) * full_weights(w)
    pos, neg = _K >= 1, _K <= -1
    out = np.zeros(shaped.shape)
    out[:, pos] = p * (1 - t) * shaped[:, pos] / shaped[:, pos].sum(axis=1, keepdims=True)
    out[:, neg] = (1 - p) * (1 - t) * shaped[:, neg] / shaped[:, neg].sum(axis=1, keepdims=True)
    out[:, _K == 0] = t
    return out


def fit_weights(mu, sigma, margin, penalty=None):
    """w_1..w_J maximising the within-side log likelihood of `margin` (decisive
    games only - a tie is the tie rate's business) minus penalty * sum (log w)^2.
    Returns (w, info)."""
    penalty = PENALTY if penalty is None else penalty
    m = np.asarray(margin, int)
    keep = m != 0
    b = base_mass(np.asarray(mu, float)[keep], np.asarray(sigma, float)[keep])
    m = m[keep]
    if np.abs(m).max(initial=0) > KMAX:
        raise ValueError("a margin beyond +/-%d" % KMAX)
    col = m + KMAX
    side_pos = m > 0
    a = np.abs(_K)
    free = (a >= 1) & (a <= J)
    # one-hot of |k| - 1 for every free support point, to map log w -> columns
    onehot = np.zeros((_K.size, J))
    onehot[np.where(free)[0], a[free] - 1] = 1.0
    pos_cols, neg_cols = _K >= 1, _K <= -1
    obs_j = np.abs(m) - 1                      # index into w, or >= J (fixed at 1)
    obs_free = obs_j < J

    def nll(lw):
        wf = np.exp(onehot @ lw)               # w_0 and |k| > J come out as exp(0) = 1
        bw = b * wf
        zp = bw[:, pos_cols].sum(axis=1)
        zn = bw[:, neg_cols].sum(axis=1)
        z = np.where(side_pos, zp, zn)
        ll = np.log(b[np.arange(len(m)), col]).sum() + lw[obs_j[obs_free]].sum() - np.log(z).sum()
        # gradient of -sum log z w.r.t. lw_j: expected share of mass at |k| = j+1 within side
        sp = (bw[:, pos_cols] / zp[:, None]) @ onehot[pos_cols]
        sn = (bw[:, neg_cols] / zn[:, None]) @ onehot[neg_cols]
        expct = np.where(side_pos[:, None], sp, sn).sum(axis=0)
        cnt = np.bincount(obs_j[obs_free], minlength=J).astype(float)
        f = -(ll - penalty * (lw ** 2).sum())
        g = -(cnt - expct - 2.0 * penalty * lw)
        return f, g

    r = minimize(nll, np.zeros(J), jac=True, method="L-BFGS-B")
    if not r.success:
        raise RuntimeError("weight fit did not converge: %s" % r.message)
    return np.exp(r.x), {"n": int(len(m)), "nll": float(r.fun), "iters": int(r.nit)}


@dataclass(frozen=True)
class KeyMarginForecast:
    """c-28's forecast with a key-number margin. The moneyline is the base's."""
    base: GameForecast
    w: tuple
    t: float

    @property
    def _pmf(self):
        return pmf(self.base.p_home, self.base.mu_m, self.base.sigma_m, np.array(self.w), self.t)[0]

    def prob_home_win(self) -> float:
        return self.base.prob_home_win()

    def prob_margin_eq(self, k: int) -> float:
        if k != int(k) or abs(k) > KMAX:
            return 0.0
        return float(self._pmf[int(k) + KMAX])

    def prob_margin_over(self, line: float) -> float:
        """P(home margin > line)."""
        return float(self._pmf[_K > line].sum())

    def prob_cover(self, line: float) -> float:
        """P(home margin > line | margin != line) - a push is a void."""
        return self.prob_margin_over(line) / (1.0 - self.prob_margin_eq(line))

    def prob_team_by_over(self, team: str, line: float) -> float:
        if team == self.base.home:
            return self.prob_margin_over(line)
        if team == self.base.away:
            return float(self._pmf[_K < -line].sum())
        raise ValueError("%s is not in %s" % (team, self.base.game_id))

    def margin_mean(self) -> float:
        return float((self._pmf * _K).sum())

    def sample(self, n: int, rng: np.random.Generator):
        return rng.choice(_K, size=n, p=self._pmf)


def cover_probs(p_home, mu, sigma, w, t, line):
    """Vectorised (P(M > L), P(M = L)) for arrays of games and their lines."""
    P = pmf(p_home, mu, sigma, w, t)
    L = np.asarray(line, float)[:, None]
    over = (P * (_K > L)).sum(axis=1)
    eq = (P * (_K == L)).sum(axis=1)
    return over, eq

