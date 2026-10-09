"""c-39: the college walk (`models.cfb_game`) and its scoring script.

Run: pytest -q tests/test_cfb_game.py

No test here opens a database. The NFL side is guarded twice: with the NFL's
settings the college walk must reproduce `models.season.run_elo`, and c-39 may
not have touched the NFL modules at all (`research/c28_season_identity.py` is
the full-data check, run before and after).
"""
import itertools
import math

import numpy as np
import pytest

from models import cfb_game as C
from models import game as G
from models import season as M
from research import cfb_game_forecast as R


def _league(seasons=(2001, 2002, 2003), n_teams=12, neutral_every=0, confs=3, seed=39):
    rng = np.random.default_rng(seed)
    teams = ["T%02d" % i for i in range(n_teams)]
    conf = {t: "C%d" % (i % confs) for i, t in enumerate(teams)}
    games, gid = [], 0
    for season in seasons:
        for week in range(1, 11):
            perm = rng.permutation(teams)
            for j in range(0, len(perm), 2):
                h, a = str(perm[j]), str(perm[j + 1])
                hs, as_ = int(rng.integers(0, 60)), int(rng.integers(0, 60))
                if hs == as_:
                    hs += 1
                gid += 1
                ts = season * 1e6 + week * 1e3 + j
                games.append({"game_id": "g%04d" % gid, "season": season, "week": week,
                              "start_ts": ts, "kickoff_ts": ts,
                              "home": h, "away": a, "home_conf": conf[h], "away_conf": conf[a],
                              "neutral": int(bool(neutral_every) and gid % neutral_every == 0),
                              "home_score": hs, "away_score": as_, "fit": True})
    return games


def test_nfl_settings_reproduce_the_nfl_walk():
    games = _league()
    nfl = G.EloParams(k=20.0, hfa=50.0, regress=0.5)
    for mov in (True, False):
        want = dict(M.run_elo(games, nfl, mov=mov)[0])
        got = dict(C.run(games, C.CfbParams(k=20.0, hfa=50.0, regress=0.5, a=G.MOV_A, cap=None,
                                            conf_w=0.0, entry=0.0), mov=mov)[0])
        assert set(got) == set(want) and len(got) == len(games)
        assert max(abs(got[i] - want[i]) for i in want) < 1e-12


def test_a_changed_constant_moves_the_walk():
    # the parity test above must be able to fail
    games = _league()
    base = dict(C.run(games, C.CfbParams(20.0, 50.0, 0.5))[0])
    for other in (C.CfbParams(20.0, 50.0, 0.5, cap=14.0), C.CfbParams(20.0, 50.0, 0.5, a=None),
                  C.CfbParams(20.0, 50.0, 0.5, conf_w=1.0), C.CfbParams(30.0, 50.0, 0.5)):
        got = dict(C.run(games, other)[0])
        assert max(abs(got[i] - base[i]) for i in base) > 1e-4, other


def test_grid_walk_is_the_scalar_walk():
    games = _league(neutral_every=7)
    games[5]["fit"] = False
    plist = [C.CfbParams(*v) for v in itertools.product(
        [20.0, 60.0], [0.0, 70.0], [0.0, 0.4], [2.2, None], [21.0, None], [0.0, 0.5, 1.0], [0.0, -200.0])]
    for mov in (True, False):
        seasons, sums, counts, first_bad = C.grid_fit(games, plist, mov=mov)
        assert (first_bad == C.NEVER).all()
        assert seasons == [2001, 2002, 2003] and counts.sum() == len(games) - 1
        for j in range(0, len(plist), 7):
            pre = C.run(games, plist[j], mov=mov)[0]
            for s_i, s in enumerate(seasons):
                want = sum(C.log_loss(p, 1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0)
                           for i, p in pre if games[i]["season"] == s and games[i]["fit"])
                assert abs(sums[s_i, j] - want) < 1e-9
        p, ll, n = C.best_params(plist, seasons, sums, counts, first_bad, 2001, 2003)
        mean = sums[:2].sum(axis=0) / counts[:2].sum()
        assert ll == pytest.approx(mean.min()) and n == counts[:2].sum()
        assert p == plist[int(np.argmin(mean))]


def test_neutral_site_carries_no_home_advantage():
    g = {"game_id": "a", "season": 2001, "start_ts": 1.0, "home": "H", "away": "A", "home_conf": "X",
         "away_conf": "X", "neutral": 1, "home_score": 30, "away_score": 10}
    p = C.CfbParams(40.0, 70.0, 0.3)
    assert C.run([g], p)[0][0][1] == pytest.approx(0.5)
    assert C.run([dict(g, neutral=0)], p)[0][0][1] == pytest.approx(float(G.win_prob(70.0)))
    assert C.run([g], p, hfa_on_neutral=True)[0][0][1] == pytest.approx(float(G.win_prob(70.0)))


def test_blowout_cap_and_damping():
    p = C.CfbParams(40.0, 0.0, 0.0, a=2.2, cap=21.0)
    assert C.multiplier(50, 0.0, p) == C.multiplier(21, 0.0, p) == pytest.approx(math.log(22.0))
    assert C.multiplier(-50, 0.0, p) == C.multiplier(50, 0.0, p)
    assert C.multiplier(10, 0.0, p) < C.multiplier(21, 0.0, p)
    uncapped = C.CfbParams(40.0, 0.0, 0.0, a=2.2, cap=None)
    assert C.multiplier(50, 0.0, uncapped) > C.multiplier(50, 0.0, p)
    # the NFL form, exactly
    for margin, diff in ((7, 120.0), (-31, 80.0), (3, -200.0)):
        nfl = G.EloParams(40.0, 0.0, 0.0)
        wdiff = diff if margin > 0 else -diff
        want = G.elo_delta(diff, 0.5, margin, nfl) / (40.0 * ((1.0 if margin > 0 else 0.0) - 0.5))
        assert C.multiplier(margin, wdiff, uncapped) == pytest.approx(want)
    assert C.multiplier(50, 500.0, C.CfbParams(40.0, 0.0, 0.0, a=None)) == pytest.approx(math.log(51.0))
    with pytest.raises(ValueError):
        C.multiplier(7, -1200.0, C.CfbParams(40.0, 0.0, 0.0, a=1.0))


def test_entry_offset_and_conference_target():
    def g(gid, season, ts, h, a, hs, as_, hc="X", ac="X"):
        return {"game_id": gid, "season": season, "start_ts": ts, "home": h, "away": a, "home_conf": hc,
                "away_conf": ac, "neutral": 1, "home_score": hs, "away_score": as_}
    games = [g("1", 2001, 1.0, "A", "B", 40, 0), g("2", 2001, 2.0, "C", "D", 40, 0, "Y", "Y"),
             g("3", 2002, 3.0, "A", "NEW", 10, 0), g("4", 2002, 4.0, "C", "D", 10, 0, "Y", "Y")]
    # entry: the first season starts at MEAN, a later arrival at MEAN + entry
    p = C.CfbParams(40.0, 0.0, 0.0, entry=-200.0)
    pre, _r, snaps = C.run(games, p, snapshot_seasons=(2002,))
    pre = dict(pre)
    assert pre[0] == pytest.approx(0.5)
    assert pre[2] == pytest.approx(float(G.win_prob(snaps[2002]["A"] - (G.MEAN - 200.0))))
    # full regression to the conference mean: both members land ON it; conference Y
    # is zero-sum inside itself, so its mean is MEAN whatever happened
    _pre, _r, snaps = C.run(games, C.CfbParams(40.0, 0.0, 1.0, conf_w=1.0), snapshot_seasons=(2002,))
    assert snaps[2002]["C"] == pytest.approx(G.MEAN) and snaps[2002]["D"] == pytest.approx(G.MEAN)
    # the 2002 conference of A holds A and the unrated NEW; B has no 2002 row.
    # So A regresses to its own rating and B to MEAN
    assert snaps[2002]["A"] > G.MEAN and snaps[2002]["B"] == pytest.approx(G.MEAN)
    # conf_w = 0 is the NFL target
    _pre, _r, snaps = C.run(games, C.CfbParams(40.0, 0.0, 1.0, conf_w=0.0), snapshot_seasons=(2002,))
    assert all(v == pytest.approx(G.MEAN) for v in snaps[2002].values())


def test_order_is_start_time_not_week_and_as_of():
    games = _league()
    # a bowl row: week 1, played last. It must be walked last in its season.
    bowl = dict(games[5], game_id="bowl", week=1, start_ts=2001 * 1e6 + 99e3, home_score=3, away_score=40)
    with_bowl = games + [bowl]
    order = C.order(with_bowl)
    last_2001 = [i for i in order if with_bowl[i]["season"] == 2001][-1]
    assert with_bowl[last_2001]["game_id"] == "bowl"
    # as-of: rewriting a LATER score cannot move an earlier forecast
    p = C.CfbParams(40.0, 55.0, 0.3, cap=28.0, conf_w=0.5)
    before = dict(C.run(games, p)[0])
    cut = len(games) // 2
    later = [dict(g) for g in games]
    later[cut]["home_score"], later[cut]["away_score"] = 0, 59
    after = dict(C.run(later, p)[0])
    ts = games[cut]["start_ts"]
    early = [i for i, g in enumerate(games) if g["start_ts"] <= ts]
    assert all(before[i] == after[i] for i in early)
    assert any(before[i] != after[i] for i in before if games[i]["start_ts"] > ts)


def test_equal_score_is_not_a_game():
    games = _league()
    games[3] = dict(games[3], home_score=0, away_score=0)
    assert 3 not in C.order(games) and len(C.order(games)) == len(games) - 1


def test_grids_and_registered_constants():
    assert len(R.grid(R.GRID_MOV)) == 207360 and len(R.grid(R.GRID_PLAIN)) == 9600
    nfl_form = [p for p in R.grid(R.GRID_MOV) if p.a == G.MOV_A and p.cap is None
                and p.conf_w == 0.0 and p.entry == 0.0]
    assert len(nfl_form) == 9 * 8 * 8                    # "the NFL multiplier is right" is reachable
    assert (R.FIT_FROM, R.SCORE_FROM, R.SCORE_TO) == (2002, 2005, 2025)
    assert R.BREAK_EVEN == pytest.approx(0.5238, abs=1e-4)
    assert R.devig(-110, -110) == pytest.approx(0.5)
    assert R.devig(-200, 170) == pytest.approx((200 / 300) / (200 / 300 + 100 / 270))
    assert R.devig(-110, -300) is None and R.devig(150, 150) is None   # outside 1.00-1.15
    g = {"season": 2024, "start_ts": 0.0}
    tue = 5.5 * 86400.0
    assert R.week_block(dict(g, start_ts=tue - 1)) != R.week_block(dict(g, start_ts=tue + 1))
    assert R.week_block(dict(g, start_ts=tue + 1)) == R.week_block(dict(g, start_ts=tue + 6.9 * 86400))


def test_c39_uses_the_nfl_mechanics_and_scoring():
    # the college module imports the NFL link; the script imports c-28's loop and c-24's statistics
    assert C.win_prob is G.win_prob and C.MEAN is G.MEAN
    assert R.F.compare.__module__ == "research.game_forecast"
    assert R.rc.corp.__module__ == "research.ranking_calibration"
