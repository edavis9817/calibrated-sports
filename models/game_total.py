"""The game total, built from two teams (c-31).

c-28's `GameForecast` carries a league-level total: every game gets the same
number. This object replaces the TOTAL and nothing else. It wraps a
`GameForecast` and reads only its margin (`p_home`, `sigma_m`), so the
moneyline and every spread rung stay exactly c-28's; the wrapped object's
`mu_t` / `sigma_t` are ignored, not overwritten.

THE DECOMPOSITION (docs/C31-game-total-preregistration.md). For each team,
expected points = plays x points per play, each factor the team's own
as-of offence plus the opponent's as-of defence minus the league, and
raw = home + away. A walk-forward OLS stack maps raw, the expected absolute
margin E|M| (c-28's margin, so a projected blowout moves the total) and wind
to a mean mu. The joint:

    Total | M ~ Normal(mu + gamma * (|M| - E|M|), s_e),   M ~ c-28's Normal

so the total and the margin are one distribution, not two independent ones,
and `prob_total_over` integrates M out with Gauss-Hermite on exactly the
distribution `sample` draws from.

Declared, not fixed: the conditional total is Normal (no total key numbers);
team points are not constrained non-negative; no home-field term (it cancels
in a sum).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from models.game import GameForecast

GH_NODES = 40
_X, _W = np.polynomial.hermite_e.hermegauss(GH_NODES)   # weight exp(-x^2/2)
_W = _W / _W.sum()


def expected_abs_normal(mu: float, sigma: float) -> float:
    """E|M| for M ~ Normal(mu, sigma)."""
    if sigma <= 0:
        return abs(mu)
    return float(sigma * math.sqrt(2.0 / math.pi) * math.exp(-mu * mu / (2.0 * sigma * sigma))
                 + mu * (1.0 - 2.0 * norm.cdf(-mu / sigma)))


@dataclass(frozen=True)
class GameTotalForecast:
    """One game's total, joint with c-28's margin. `base` answers every margin question."""
    base: GameForecast
    mu: float          # E[total] from the stack
    gamma: float       # total shift per point of |M| above its expectation
    s_e: float         # conditional sd of the total given M

    @property
    def e_abs_m(self) -> float:
        return expected_abs_normal(self.base.mu_m, self.base.sigma_m)

    def _m_nodes(self):
        return self.base.mu_m + self.base.sigma_m * _X

    def _cond_means(self):
        return self.mu + self.gamma * (np.abs(self._m_nodes()) - self.e_abs_m)

    def mean(self) -> float:
        return self.mu            # E[|M| - E|M|] = 0

    total_mean = mean

    def cdf(self, x: float) -> float:
        """P(total <= x)."""
        return float(np.dot(_W, norm.cdf(x, loc=self._cond_means(), scale=self.s_e)))

    def prob_total_over(self, line: float) -> float:
        return 1.0 - self.cdf(line)

    prob_over = prob_total_over

    def prob_over_push_void(self, line: float) -> float:
        """P(over | not a push). A whole-number line voids at the line itself, priced
        with continuity correction; a half-point line is plain P(T > L)."""
        if abs(line - round(line)) > 1e-9:
            return self.prob_total_over(line)
        up = self.prob_total_over(line + 0.5)
        down = self.cdf(line - 0.5)
        return float(up / (up + down))

    def sample(self, n: int, rng: np.random.Generator):
        """(n x 2) joint samples of (home margin, total)."""
        m = rng.normal(self.base.mu_m, self.base.sigma_m, n)
        t = rng.normal(self.mu + self.gamma * (np.abs(m) - self.e_abs_m), self.s_e)
        return np.column_stack([m, t])

    # the margin is c-28's, delegated rather than re-derived
    def prob_home_win(self) -> float:
        return self.base.prob_home_win()

    def prob_team_by_over(self, team: str, line: float) -> float:
        return self.base.prob_team_by_over(team, line)


# ------------------------------------------------------------------ the factors

@dataclass
class TeamGame:
    """One team's offence in one game: the unit the factors are built from."""
    game_id: str
    season: int
    kickoff: float
    team: str
    opp: str
    plays: float
    points: float


class FactorBook:
    """As-of team factors. Feed team-games in kickoff order with `add`; `estimate`
    reads only what has been added, so a game can never see itself or anything later.

    Estimator (the pre-registration): prior = L + r (last season's mean - L), or L
    without a last season; estimate = (sum v + k prior) / (n + k) for plays, and
    (sum pts + k L_plays prior) / (sum plays + k L_plays) for points per play.
    """

    WINDOW = 512           # team-games = 256 games, c-28's total window

    def __init__(self):
        self.hist = []                          # (plays, points) league window source
        self.off = {}                           # (team, season) -> [n, plays, points]
        self.dfn = {}                           # (team, season) -> [n, plays, points] allowed

    def add(self, tg: TeamGame):
        self.hist.append((tg.plays, tg.points))
        for book, key in ((self.off, (tg.team, tg.season)), (self.dfn, (tg.opp, tg.season))):
            s = book.setdefault(key, [0, 0.0, 0.0])
            s[0] += 1
            s[1] += tg.plays
            s[2] += tg.points

    def league(self):
        w = self.hist[-self.WINDOW:]
        if len(w) < 32:
            return None
        pl = sum(x[0] for x in w)
        pt = sum(x[1] for x in w)
        return pl / len(w), pt / pl

    def raw_state(self, team, opp, season):
        """Everything `estimate` needs, frozen now: league means and the four sums."""
        L = self.league()
        if L is None:
            return None
        return {"L": L,
                "off_cur": tuple(self.off.get((team, season), (0, 0.0, 0.0))),
                "off_pri": tuple(self.off.get((team, season - 1), (0, 0.0, 0.0))),
                "def_cur": tuple(self.dfn.get((opp, season), (0, 0.0, 0.0))),
                "def_pri": tuple(self.dfn.get((opp, season - 1), (0, 0.0, 0.0)))}


def _plays_est(cur, pri, L_p, k, r):
    prior = L_p + r * (pri[1] / pri[0] - L_p) if pri[0] else L_p
    return (cur[1] + k * prior) / (cur[0] + k)


def _ppp_est(cur, pri, L_p, L_q, k, r):
    prior = L_q + r * (pri[2] / pri[1] - L_q) if pri[0] and pri[1] > 0 else L_q
    return (cur[2] + k * L_p * prior) / (cur[1] + k * L_p)


def team_expectation(state, kp, rp, kq, rq, use_plays=True, use_ppp=True):
    """(plays_X, ppp_X) for the offence in `state` against its opponent."""
    L_p, L_q = state["L"]
    if use_plays:
        plays = (_plays_est(state["off_cur"], state["off_pri"], L_p, kp, rp)
                 + _plays_est(state["def_cur"], state["def_pri"], L_p, kp, rp) - L_p)
    else:
        plays = L_p
    if use_ppp:
        ppp = (_ppp_est(state["off_cur"], state["off_pri"], L_p, L_q, kq, rq)
               + _ppp_est(state["def_cur"], state["def_pri"], L_p, L_q, kq, rq) - L_q)
    else:
        ppp = L_q
    return plays, ppp


def ols(X, y):
    """(beta, residual) by least squares."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta, y - X @ beta
