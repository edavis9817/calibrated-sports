"""The game forecast: one NFL game as a distribution (c-28).

Lifted out of `models.season`, where it existed only inside the simulation:
the season model's P(home wins) = 1 / (1 + 10^(-(d + hfa)/400)), with d the
margin-of-victory Elo difference. The rating walk itself stays in
`models.season.run_elo`; what lives here is the part that describes ONE game -
the win probability, the rating update a game causes, and the forecast object
that prices every question about the game from a single distribution.

`models.season` imports its constants and both mechanics from here, so there
is one copy. The lift is required to leave the season model byte-identical:
`research/c28_season_identity.py` hashes the full walk-forward before and
after, and `tests/test_game_forecast.py` pins `run_elo` on a synthetic league
to values captured from the pre-lift code.

THE OBJECT (invariant 3: a distribution, never a scalar). Per game:
  p_home_win  exactly the season model's number;
  margin      home minus away ~ Normal(mu, sigma_m) with
              mu = sigma_m * PhiInv(p_home_win), so P(margin > 0) is
              p_home_win and the moneyline and every spread rung are one
              object, not two models;
  total       ~ Normal(mu_t, sigma_t), a LEAGUE-LEVEL as-of distribution.
              Elo reads results, not scoring, so there is no game-specific
              term in the total, and it is not expected to be informative.
Declared, not fixed: a Normal carries no key numbers (3, 7); margin and total
are independent; ties are not modelled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

MEAN = 1500.0
MOV_A = 2.2

# A franchise keeps one rating across a move. The division map is keyed on the
# published abbreviation and holds both halves (STL and LA are both NFC West).
FRANCHISE = {"STL": "LA", "SD": "LAC", "OAK": "LV"}


def franchise(team: str) -> str:
    return FRANCHISE.get(team, team)


@dataclass(frozen=True)
class EloParams:
    k: float
    hfa: float
    regress: float

    def as_dict(self):
        return {"k": self.k, "hfa": self.hfa, "regress": self.regress}


def win_prob(diff):
    """P(home wins) given home rating minus away rating PLUS home advantage."""
    return 1.0 / (1.0 + 10.0 ** (-np.asarray(diff, dtype=float) / 400.0))


def pregame(rh: float, ra: float, params: EloParams):
    """(diff, p_home) for one game from the two ratings entering it."""
    diff = rh - ra + params.hfa
    return diff, float(win_prob(diff))


def elo_delta(diff: float, p: float, margin, params: EloParams, mov: bool = True) -> float:
    """The rating points the home side gains (the away side loses) from a result.

    With `mov` the multiplier is FiveThirtyEight's
    ln(|margin| + 1) x 2.2 / (0.001 x elo_diff_winner + 2.2); without it the
    multiplier is 1 - plain Elo, which is c-28's `elo_nomov` baseline.
    """
    result = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
    if margin == 0 or not mov:
        mult = 1.0
    else:
        wdiff = diff if margin > 0 else -diff
        mult = math.log(abs(margin) + 1.0) * MOV_A / (0.001 * wdiff + MOV_A)
    return params.k * mult * (result - p)


# ---------------------------------------------------------------- the object

@dataclass(frozen=True)
class GameForecast:
    """One game, as of a named instant. Every price is a query on this."""
    game_id: str
    home: str
    away: str
    as_of: str
    p_home: float
    sigma_m: float
    mu_t: float
    sigma_t: float

    @property
    def mu_m(self) -> float:
        p = min(max(self.p_home, 1e-12), 1 - 1e-12)
        return float(self.sigma_m * norm.ppf(p))

    def prob_home_win(self) -> float:
        return self.p_home

    def margin_mean(self) -> float:
        return self.mu_m

    def total_mean(self) -> float:
        return self.mu_t

    def prob_margin_over(self, line: float) -> float:
        """P(home margin > line)."""
        return float(norm.sf(line, loc=self.mu_m, scale=self.sigma_m))

    def prob_team_by_over(self, team: str, line: float) -> float:
        """P(`team` wins by more than `line`) - Kalshi's spread rung."""
        if team == self.home:
            return self.prob_margin_over(line)
        if team == self.away:
            return float(norm.cdf(-line, loc=self.mu_m, scale=self.sigma_m))
        raise ValueError("%s is not in %s" % (team, self.game_id))

    def prob_total_over(self, line: float) -> float:
        return float(norm.sf(line, loc=self.mu_t, scale=self.sigma_t))

    def sample(self, n: int, rng: np.random.Generator):
        """(n x 2) joint samples of (home margin, total); independent by declaration."""
        return np.column_stack([rng.normal(self.mu_m, self.sigma_m, n),
                                rng.normal(self.mu_t, self.sigma_t, n)])


def margin_sigma_mle(p, margin, grid=None):
    """sigma maximising the Normal likelihood of `margin` under
    mu = sigma * PhiInv(p). Grid 10.0..18.0 step 0.1 (the pre-registration)."""
    grid = np.round(np.arange(10.0, 18.0001, 0.1), 1) if grid is None else grid
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    z = norm.ppf(p)
    m = np.asarray(margin, dtype=float)
    best, best_ll = None, -math.inf
    for s in grid:
        ll = float(norm.logpdf(m, loc=s * z, scale=s).sum())
        if ll > best_ll + 1e-12:
            best, best_ll = float(s), ll
    return best
