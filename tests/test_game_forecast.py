"""c-28: the game forecast lifted out of the season model, and its scoring.

Run: pytest -q tests/test_game_forecast.py

The lift must not move the season model. The full-data check is
`research/c28_season_identity.py` (a hash of the whole walk-forward, run before
and after); this file pins `run_elo` on a synthetic league to a digest captured
from the PRE-LIFT code, so the check is committed and runs without a store.
No test here opens a database.
"""
import ast
import hashlib
import json
import os

import numpy as np
import pytest

from models import game as G
from models import season as M
from research import game_forecast as F

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# sha256 of `_digest(M)` computed on models/season.py at 7b44990, before c-28
# touched it (the lift is c-28's; the merge commit carries the pre-lift file).
PRE_LIFT_DIGEST = "8d91e3053ce66ef593572d5ec79d95a34244306e8b40089650c2a514ef8e8149"


def _league():
    rng = np.random.default_rng(2828)
    teams = ["T%02d" % i for i in range(12)] + ["STL", "OAK"]
    games, gid = [], 0
    for season in (2001, 2002, 2003):
        for week in range(1, 11):
            perm = rng.permutation(teams)
            for j in range(0, len(perm), 2):
                h, a = str(perm[j]), str(perm[j + 1])
                played = not (season == 2003 and week > 6)
                hs = int(rng.integers(0, 40)) if played else None
                as_ = int(rng.integers(0, 40)) if played else None
                if played and week == 3 and j == 0:
                    as_ = hs          # a tie
                games.append(dict(game_id="g%04d" % gid, season=season, week=week,
                                  kickoff_ts=float(1e9 + season * 1e6 + week * 1e4 + j),
                                  home=h, away=a, home_score=hs, away_score=as_))
                gid += 1
    return games


def _digest(mod, **kw):
    games = _league()
    out = []
    for p in (mod.EloParams(20.0, 50.0, 1 / 3), mod.EloParams(40.0, 0.0, 0.0),
              mod.EloParams(10.0, 75.0, 1.0)):
        pre, snaps = mod.run_elo(games, p, snapshot_at=[(2001, 0), (2002, 5), (2003, 6),
                                                         (2003, 99), (2004, 0)], **kw)
        out.append([[i, repr(q)] for i, q in pre])
        out.append({repr(k): {t: repr(v) for t, v in sorted(s.items())}
                    for k, s in sorted(snaps.items())})
    out.append([repr(float(x)) for x in mod.win_prob(np.array([-400.0, -37.5, 0.0, 12.25, 800.0]))])
    return hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------ the lift

def test_run_elo_is_byte_identical_to_the_pre_lift_code():
    assert _digest(M) == PRE_LIFT_DIGEST


def test_the_pin_discriminates():
    # plain Elo must move the digest, or the pin could not see a change
    assert _digest(M, mov=False) != PRE_LIFT_DIGEST


def test_one_copy_of_the_game_mechanics():
    for name in ("EloParams", "win_prob", "franchise", "FRANCHISE", "MEAN", "MOV_A"):
        assert getattr(M, name) is getattr(G, name), name
    tree = ast.parse(open(os.path.join(ROOT, "models", "season.py"), encoding="utf-8").read())
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert not defined & {"EloParams", "win_prob", "franchise", "pregame", "elo_delta"}


def test_plain_elo_uses_multiplier_one():
    p = G.EloParams(20.0, 0.0, 0.0)
    diff, q = G.pregame(1500.0, 1500.0, p)
    assert q == 0.5
    assert G.elo_delta(diff, q, 30, p, mov=False) == pytest.approx(10.0)
    assert G.elo_delta(diff, q, 30, p, mov=True) > 10.0     # a blowout is multiplied
    assert G.elo_delta(diff, q, 0, p, mov=True) == 0.0


# ------------------------------------------------------------------ the object

def fc(p=0.62, sm=13.5, mt=44.0, st=13.0):
    return G.GameForecast("g", "H", "A", "kickoff", p, sm, mt, st)


@pytest.mark.parametrize("p", [0.05, 0.3, 0.5, 0.62, 0.97])
def test_moneyline_and_spread_are_one_object(p):
    f = fc(p)
    assert f.prob_margin_over(0.0) == pytest.approx(p, abs=1e-12)
    for L in (-10.5, -3.5, 2.5, 7.5):
        assert f.prob_team_by_over("H", L) + f.prob_team_by_over("A", -L) == pytest.approx(1.0)
    assert np.sign(f.margin_mean()) == np.sign(p - 0.5)


def test_rungs_are_monotone_and_total_is_game_blind():
    f = fc()
    r = [f.prob_team_by_over("A", L) for L in (0.5, 3.5, 7.5, 13.5)]
    assert all(a > b for a, b in zip(r, r[1:]))
    assert fc(0.9).prob_total_over(44.5) == fc(0.1).prob_total_over(44.5)
    with pytest.raises(ValueError):
        f.prob_team_by_over("X", 3.5)


def test_sample_moments():
    s = fc().sample(200_000, np.random.default_rng(1))
    assert s[:, 0].mean() == pytest.approx(fc().margin_mean(), abs=0.1)
    assert s[:, 1].std() == pytest.approx(13.0, abs=0.1)


def test_sigma_mle_recovers_and_discriminates():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.2, 0.8, 20_000)
    from scipy.stats import norm
    for true in (12.0, 15.0):
        m = rng.normal(true * norm.ppf(p), true)
        assert G.margin_sigma_mle(p, m) == pytest.approx(true, abs=0.2)


# ------------------------------------------------------------------ scoring helpers

def g(gid, season, week, ts, h, a, hs, as_):
    return dict(game_id=gid, season=season, week=week, kickoff_ts=ts, home=h, away=a,
                home_score=hs, away_score=as_, game_type="REG")


def test_record_is_as_of_kickoff():
    games = [g("1", 2010, 1, 100.0, "A", "B", 20, 10),
             g("2", 2010, 1, 100.0, "C", "D", 10, 20),   # same kickoff: cannot see game 1
             g("3", 2010, 2, 200.0, "A", "D", 3, 0),     # A 1-0 vs D 1-0: equal
             g("4", 2010, 3, 300.0, "B", "A", 0, 3)]     # B 0-1 vs A 2-0: home worse
    s = F.record_signs(games)
    assert s == {0: 0, 1: 0, 2: 0, 3: -1}


def test_baseline_constants_exclude_the_scored_season():
    games = [g("1", 2000, 1, 1.0, "A", "B", 1, 0), g("2", 2000, 2, 2.0, "A", "B", 0, 1),
             g("3", 2001, 1, 3.0, "A", "B", 1, 0), g("4", 2001, 2, 4.0, "A", "B", 1, 0)]
    signs = F.record_signs(games)
    h, _q = F.baseline_constants(games, signs, 2001)
    assert h == 0.5                  # 2001's two home wins are not visible to 2001
    h2, _ = F.baseline_constants(games, signs, 2002)
    assert h2 == 0.75


def test_total_window_uses_strictly_earlier_kickoffs(monkeypatch):
    monkeypatch.setattr(F, "TOTAL_WINDOW", 2)
    games = [g("1", 2000, 1, 1.0, "A", "B", 10, 0), g("2", 2000, 1, 2.0, "C", "D", 20, 0),
             g("3", 2000, 2, 3.0, "A", "C", 30, 0), g("4", 2000, 2, 3.0, "B", "D", 99, 0),
             g("5", 2000, 3, 4.0, "A", "D", 0, 0)]
    t = F.total_params(games)
    assert 0 not in t and 1 not in t
    assert t[2][0] == 15.0 and t[3][0] == 15.0      # 3 and 4 share a kickoff: neither sees the other
    assert t[4][0] == pytest.approx((30 + 99) / 2)


def test_american_odds():
    assert F.american(-150) == pytest.approx(0.6)
    assert F.american(150) == pytest.approx(0.4)


def test_scoring_is_imported_not_copied():
    tree = ast.parse(open(os.path.join(ROOT, "research", "game_forecast.py"), encoding="utf-8").read())
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert not defined & {"brier", "auc", "wauc", "pav", "corp", "Pop", "run_elo",
                          "best_params", "game_losses", "win_prob"}
