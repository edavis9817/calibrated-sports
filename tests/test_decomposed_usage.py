"""c-27: the decomposed-usage model's pure pieces, its panel, and the rule that
the scoring is c-24's code imported rather than a copy."""
import ast
import os
import sqlite3

import numpy as np
import pytest

from research import decomposed_usage as du
from research import ranking_calibration as rc

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_scoring_is_imported_not_copied():
    assert du.rc is rc
    tree = ast.parse(open(os.path.join(HERE, "research", "decomposed_usage.py"), encoding="utf-8").read())
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    # the c-24 statistics must not be re-defined here
    assert not defined & {"corp", "auc", "wauc", "pav", "brier", "murphy", "platt_fit"}


def test_mom_recovers_k_and_discriminates():
    rng = np.random.default_rng(0)
    tau, sigma = 1.0, 2.0                         # true k = 4
    groups = [rng.normal(rng.normal(10, tau), sigma, 16) for _ in range(3000)]
    m = du.mom(groups)
    assert abs(m["k"] - 4.0) < 0.4
    assert abs(m["w8"] - 8 / 12) < 0.03
    assert abs(m["r1"] - 1 / 5) < 0.03            # r1 = tau^2 / (tau^2 + sigma^2)
    # the other answer: no between-entity signal -> w8 0, r1 ~ 0
    flat = du.mom([rng.normal(10, sigma, 16) for _ in range(3000)])
    assert flat["w8"] < 0.05 and abs(flat["r1"]) < 0.03


def test_betabin_rho_recovers_icc():
    rng = np.random.default_rng(1)
    for rho in (0.01, 0.1):
        phi = 1 / rho - 1
        V = rng.integers(20, 45, 200_000)
        s = np.full(V.shape, 0.2)
        t = rng.binomial(V, rng.beta(s * phi, (1 - s) * phi))
        assert abs(du.betabin_rho(t, V, s) - rho) < 0.2 * rho + 0.002


def test_shrink():
    assert du.shrink(0, 0.5, 2.0, 0.1, 0.04) == (0.1, 0.02)
    mean, var = du.shrink(6, 0.3, 2.0, 0.1, 0.04)
    assert abs(mean - 0.25) < 1e-12 and abs(var - 0.005) < 1e-12
    assert du.shrink(10, 0.3, float("inf"), 0.1, 0.04) == (0.1, 0.0)


def _fc():
    fc = du.Forecaster.__new__(du.Forecaster)
    return fc


def test_simulation_composes_the_factors():
    fc = _fc()
    comp = {"mu_V": 34.0, "var_V": 34.0 * 1.7, "s": 0.2, "pv_s": 1e-6, "phi": 100.0,
            "ca": 650.0, "cb": 350.0}
    x = fc.simulate(comp, "receptions", seed=3, draws=200_000)
    assert abs(x.mean() - 34 * 0.2 * 0.65) < 0.05
    r = fc.simulate(comp, "rush_attempts", seed=3, draws=200_000)
    assert abs(r.mean() - 34 * 0.2) < 0.05
    # a wider share uncertainty must widen the outcome
    wide = fc.simulate(dict(comp, pv_s=0.004), "rush_attempts", seed=3, draws=200_000)
    assert wide.var() > r.var() * 1.2


def test_window_is_as_of_and_two_seasons():
    fc = _fc()
    rows = [(100, 2022, "a"), (200, 2023, "b"), (300, 2024, "c"), (400, 2024, "d"), (500, 2024, "e")]
    got = [(w, r[2]) for w, r in fc._window(rows, 2024, 400)]
    assert got == [(du.PREV_W, "b"), (1.0, "c")]   # 2022 excluded, kick >= 400 excluded


def _mini_store(path):
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE nfl_games (game_id, data_version, season, week, game_type, kickoff_ts, home_team, away_team);
      CREATE TABLE nfl_player_week (gsis_id, season, week, season_type, data_version, position, team,
        targets, receptions, carries, receiving_yards, rushing_yards, attempts);
      CREATE TABLE nfl_snap_counts (pfr_player_id, game_id, data_version, team, position, offense_snaps);
      CREATE TABLE player_xwalk (gsis_id, pfr_id);
    """)
    con.executemany("INSERT INTO nfl_games VALUES (?,?,?,?,?,?,?,?)", [
        ("2015_01_OAK_CIN", 1, 2015, 1, "REG", 1000, "OAK", "CIN"),
        ("2015_20_OAK_CIN", 1, 2015, 20, "CON", 5000, "OAK", "CIN")])
    con.executemany("INSERT INTO nfl_player_week VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        # today's code for a relocated franchise, as nflverse writes it
        ("p1", 2015, 1, "REG", 1, "WR", "LV", 8, 5, 0, 60, 0, 0),
        ("q1", 2015, 1, "REG", 1, "QB", "LV", 0, 0, 2, 0, 5, 30),
        ("p2", 2015, 1, "REG", 1, "WR", "CIN", 6, 4, 0, 40, 0, 0)])
    con.executemany("INSERT INTO nfl_snap_counts VALUES (?,?,?,?,?,?)", [
        ("P1", "2015_01_OAK_CIN", 1, "LV", "WR", 50),
        ("Z1", "2015_01_OAK_CIN", 1, "OAK", "TE", 20)])   # played, no stat row
    con.executemany("INSERT INTO player_xwalk VALUES (?,?)", [("p1", "P1"), ("z1", "Z1")])
    con.commit()
    return con


def test_panel_folds_relocations_and_adds_played_zeros(tmp_path):
    con = _mini_store(str(tmp_path / "mini.db"))
    P = du.Panel(con)
    assert P.census["player-week row with no REG game"] == 0
    assert P.pg[("p1", "2015_01_OAK_CIN")]["team"] == "OAK"
    assert P.pg[("p1", "2015_01_OAK_CIN")]["snaps"] == 50
    z = P.pg[("z1", "2015_01_OAK_CIN")]
    assert (z["tgt"], z["rec"], z["team"]) == (0, 0, "OAK")
    assert P.tg[("2015_01_OAK_CIN", "OAK")]["tgt"] == 8
    assert P.tg[("2015_01_OAK_CIN", "OAK")]["att"] == 30
    # the postseason game is metadata only, never history
    assert "2015_20_OAK_CIN" in P.meta and "2015_20_OAK_CIN" not in P.games
    # the other answer: without the fold the row would have been dropped
    saved = dict(du.RELOCATED)
    try:
        du.RELOCATED.clear()
        assert du.Panel(con).census["player-week row with no REG game"] == 2
    finally:
        du.RELOCATED.update(saved)


def test_seed_is_stable():
    assert du.seed_of("00-1", "receptions", "g") == du.seed_of("00-1", "receptions", "g")
    assert du.seed_of("00-1", "receptions", "g") != du.seed_of("00-1", "rush_attempts", "g")


@pytest.mark.parametrize("n", [0, 3])
def test_mom_refuses_nothing_quietly(n):
    assert du.mom([[1.0]] * n) is None
