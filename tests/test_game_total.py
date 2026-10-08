"""c-31: the total built from two teams, joint with c-28's margin.

Run: pytest -q tests/test_game_total.py

No test here opens a database. The full-data invariant (c-28's moneyline Brier
reproduced to the float) is asserted inside `research/game_total.py`, which
refuses to score if it moves.
"""
import copy

import numpy as np
import pytest
from scipy.stats import norm

from models import game as G
from models import game_total as GT
from research import game_total as R


def _base(p=0.62, sigma=13.5):
    return G.GameForecast(game_id="g", home="H", away="A", as_of="kickoff", p_home=p,
                          sigma_m=sigma, mu_t=44.0, sigma_t=13.0)


# ---------------------------------------------------------------- the wrapper leaves c-28 alone

def test_wrapper_does_not_move_the_margin():
    base = _base()
    before = (base.p_home, base.mu_m, base.prob_team_by_over("H", 3.5),
              base.prob_team_by_over("A", -6.5), base.mu_t, base.sigma_t)
    snap = copy.deepcopy(base)
    t = GT.GameTotalForecast(base=base, mu=47.0, gamma=0.15, s_e=13.0)
    t.prob_total_over(44.5), t.cdf(40.0), t.sample(100, np.random.default_rng(1))
    after = (base.p_home, base.mu_m, base.prob_team_by_over("H", 3.5),
             base.prob_team_by_over("A", -6.5), base.mu_t, base.sigma_t)
    assert before == after and base == snap
    assert t.prob_home_win() == base.p_home
    assert t.prob_team_by_over("H", 3.5) == base.prob_team_by_over("H", 3.5)
    # and the total really is the new one, not c-28's league number
    assert t.mean() == 47.0 and t.mean() != base.total_mean()


# ---------------------------------------------------------------- the distribution

def test_expected_abs_normal():
    assert GT.expected_abs_normal(0.0, 10.0) == pytest.approx(10.0 * np.sqrt(2 / np.pi))
    x = np.random.default_rng(3).normal(4.0, 13.0, 2_000_000)
    assert GT.expected_abs_normal(4.0, 13.0) == pytest.approx(np.abs(x).mean(), abs=0.02)


def test_gamma_zero_is_a_plain_normal():
    t = GT.GameTotalForecast(base=_base(), mu=45.0, gamma=0.0, s_e=13.0)
    for L in (33.5, 44.5, 51.5):
        assert t.prob_total_over(L) == pytest.approx(float(norm.sf(L, 45.0, 13.0)), abs=1e-12)


def test_prob_over_is_the_distribution_sample_draws_from():
    t = GT.GameTotalForecast(base=_base(0.85), mu=46.0, gamma=0.35, s_e=12.0)
    s = t.sample(1_000_000, np.random.default_rng(7))
    assert s[:, 1].mean() == pytest.approx(t.mean(), abs=0.05)
    for L in (36.5, 46.5, 55.5):
        assert t.prob_total_over(L) == pytest.approx((s[:, 1] > L).mean(), abs=0.002)
    # gamma > 0 couples the two: blowouts carry higher totals
    assert np.corrcoef(np.abs(s[:, 0]), s[:, 1])[0, 1] > 0.1
    # ... and the margin draws are c-28's
    assert s[:, 0].mean() == pytest.approx(t.base.mu_m, abs=0.05)


def test_push_void_pricing():
    t = GT.GameTotalForecast(base=_base(), mu=44.0, gamma=0.1, s_e=13.0)
    assert t.prob_over_push_void(44.5) == t.prob_total_over(44.5)
    w = t.prob_over_push_void(44.0)
    assert t.prob_total_over(44.5) < w < t.prob_total_over(43.5)


# ---------------------------------------------------------------- every feature is as-of

def _league():
    rng = np.random.default_rng(31)
    teams = ["T%02d" % i for i in range(10)]
    games, pace = [], {}
    gid = 0
    for season in (2001, 2002):
        for week in range(1, 9):
            perm = rng.permutation(teams)
            for j in range(0, len(perm), 2):
                h, a = str(perm[j]), str(perm[j + 1])
                g = dict(game_id="g%04d" % gid, season=season, week=week, game_type="REG",
                         kickoff_ts=float(1e9 + season * 1e6 + week * 1e4 + (j // 4)),
                         home=h, away=a, home_score=int(rng.integers(3, 40)),
                         away_score=int(rng.integers(3, 40)))
                games.append(g)
                pace[(g["game_id"], h)] = float(rng.integers(52, 75))
                pace[(g["game_id"], a)] = float(rng.integers(52, 75))
                gid += 1
    return games, pace


def _pred(states, i):
    return (GT.team_expectation(states[(i, "home")], 6, 0.5, 8, 0.35),
            GT.team_expectation(states[(i, "away")], 6, 0.5, 8, 0.35))


def test_a_game_cannot_see_itself_or_anything_later():
    games, pace = _league()
    i = 60                                          # season 2002, mid-season
    base = _pred(R.factor_states(games, pace), i)
    for j in (i, i + 1, len(games) - 1):            # itself, a later game, the last game
        g2 = copy.deepcopy(games)
        p2 = dict(pace)
        g2[j]["home_score"] += 30
        p2[(g2[j]["game_id"], g2[j]["home"])] += 20
        assert _pred(R.factor_states(g2, p2), i) == base, j


def test_an_earlier_game_does_move_it():
    """The as-of test discriminates: the same edit to an EARLIER game of the same team moves it."""
    games, pace = _league()
    i = 60
    team = games[i]["home"]
    j = max(k for k in range(i) if team in (games[k]["home"], games[k]["away"])
            and games[k]["kickoff_ts"] < games[i]["kickoff_ts"])
    base = _pred(R.factor_states(games, pace), i)
    g2 = copy.deepcopy(games)
    p2 = dict(pace)
    side = "home_score" if g2[j]["home"] == team else "away_score"
    g2[j][side] += 30
    p2[(g2[j]["game_id"], team)] += 20
    assert _pred(R.factor_states(g2, p2), i) != base


def test_games_sharing_a_kickoff_do_not_see_each_other():
    games, pace = _league()
    i = 60
    same = [k for k, g in enumerate(games) if g["kickoff_ts"] == games[i]["kickoff_ts"] and k != i]
    assert same, "the synthetic league must put two games on one kickoff"
    base = _pred(R.factor_states(games, pace), i)
    g2 = copy.deepcopy(games)
    g2[same[0]]["home_score"] += 30
    assert _pred(R.factor_states(g2, pace), i) == base


def test_weather_encoding():
    assert R.weather_x(("outdoors", 12.0)) == (12.0, 0.0, 0.0)
    assert R.weather_x(("dome", None)) == (0.0, 1.0, 0.0)
    assert R.weather_x(("closed", float("nan"))) == (0.0, 1.0, 0.0)
    assert R.weather_x(("outdoors", None)) == (0.0, 0.0, 1.0)
    assert R.weather_x(None) == (0.0, 0.0, 1.0)
