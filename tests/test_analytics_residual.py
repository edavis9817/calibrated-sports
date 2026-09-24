"""The opportunity residual (`analytics.residual`).

Every test runs on an in-memory database or on plain Python values - nothing
here opens the store. Each discriminating test shows the metric returning BOTH
answers, because a persistence measure that can only report "does not persist"
is decoration (the falsifiability rule).
"""
import random
import sqlite3

import numpy as np
import pytest

from analytics import metrics, residual


# ---------------------------------------------------------------------------
# the fit
# ---------------------------------------------------------------------------

def test_the_moment_fit_matches_least_squares():
    rng = np.random.default_rng(1)
    x1 = rng.integers(1, 12, 400).astype(float)
    x2 = rng.normal(40, 20, 400)
    y = 1.5 + 6.0 * x1 + 0.2 * x2 + rng.normal(0, 15, 400)
    v = sum(residual._moments(a, (b, c)) for a, b, c in zip(y, x1, x2))
    beta, r2 = residual._solve(v, 2)
    X = np.column_stack([np.ones(400), x1, x2])
    ref, *_ = np.linalg.lstsq(X, y, rcond=None)
    assert np.allclose(beta, ref)
    assert r2 == pytest.approx(1 - np.var(y - X @ ref) / np.var(y))


def test_r2_reaches_both_ends():
    xs = list(range(1, 50))
    exact = sum(residual._moments(3.0 * x, (x,)) for x in xs)
    assert residual._solve(exact, 1)[1] == pytest.approx(1.0)
    rng = random.Random(3)
    noise = sum(residual._moments(rng.gauss(0, 1), (x,)) for x in xs * 20)
    assert residual._solve(noise, 1)[1] < 0.02


# ---------------------------------------------------------------------------
# persistence discriminates
# ---------------------------------------------------------------------------

def _league(persistent_efficiency, seasons=(2020, 2021), players=120, weeks=16,
            seed=7):
    """Rows where volume is a stable player trait and efficiency either is one
    too or is pure noise."""
    rng = random.Random(seed)
    rows, groups = [], {}
    for p in range(players):
        pid = "p%03d" % p
        groups[pid] = "WR"
        vol = rng.uniform(2, 10)
        eff = rng.gauss(0, 2.0)
        for s in seasons:
            for w in range(1, weeks + 1):
                x = max(1, round(vol + rng.gauss(0, 1.5)))
                bonus = eff if persistent_efficiency else rng.gauss(0, 2.0)
                y = 7.0 * x + bonus * x + rng.gauss(0, 10)
                rows.append((pid, s, w, y, (float(x),)))
    return rows, groups


def _persistence(rows, groups):
    res = residual.residuals(residual.fit(rows, groups))
    got = {sl: est for _s, sl, est in residual.persistence_values("t", res)}
    return got


def test_noise_efficiency_reads_as_not_persisting():
    # OVER SEEDS, NOT ONE. A single seed is one draw of a 95% interval: seed 7
    # excluded zero (0.19 [0.02, 0.35]) while 20 seeds averaged -0.008 with
    # 18/20 covering zero - the nominal rate. Asserting on one seed tests luck.
    ests, covers = [], 0
    for seed in range(1, 11):
        got = _persistence(*_league(persistent_efficiency=False, seed=seed))
        r = got["WR|lag1_season|residual"]
        ests.append(r.est)
        covers += r.lo < 0 < r.hi
        assert got["WR|lag1_season|opportunity"].est > 0.8
        assert got["WR|lag1_season|gap"].lo > 0
    assert abs(sum(ests) / len(ests)) < 0.07
    assert covers >= 8


def test_a_persistent_efficiency_reads_as_persisting():
    got = _persistence(*_league(persistent_efficiency=True))
    r = got["WR|lag1_season|residual"]
    assert r.lo > 0.5
    assert got["WR|split_half|residual"].lo > 0.5


def test_the_gap_is_one_quantity_not_two_differenced():
    got = _persistence(*_league(persistent_efficiency=False))
    for design in ("lag1_season", "split_half"):
        o, r, g = (got["WR|%s|%s" % (design, q)]
                   for q in ("opportunity", "residual", "gap"))
        assert g.est == pytest.approx(o.est - r.est)
        # the n is players, and all three come from the same blocks
        assert o.n == r.n == g.n
        assert g.rows == o.rows


# ---------------------------------------------------------------------------
# null is never zero
# ---------------------------------------------------------------------------

def _spine(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE f_play_usage (season, week, season_type, game_id, "
                "play_id, player_id, role, is_target, is_reception, is_carry, "
                "air_yards, yards)")
    con.executemany("INSERT INTO f_play_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
    return con


def test_a_completion_with_no_yards_drops_the_whole_week():
    con = _spine([
        (2020, 1, "REG", "g1", 1, "a", "receiver", 1, 1, 0, 5.0, 12.0),
        (2020, 1, "REG", "g1", 2, "a", "receiver", 1, 1, 0, 8.0, None),
        (2020, 2, "REG", "g2", 1, "a", "receiver", 1, 1, 0, 5.0, 9.0),
    ])
    rows, dropped = residual._spine_rows(con, "receiving_yards", 2020, 2020)
    assert dropped == 1
    assert [(r[2], r[3], r[4]) for r in rows] == [(2, 9.0, (1.0, 5.0))]


def test_a_target_with_no_air_yards_drops_the_week_from_the_yards_fit_only():
    con = _spine([
        (2020, 1, "REG", "g1", 1, "a", "receiver", 1, 1, 0, None, 12.0),
    ])
    assert residual._spine_rows(con, "receiving_yards", 2020, 2020) == ([], 1)
    # receptions do not use air yards, so the week stays there
    rows, dropped = residual._spine_rows(con, "receptions", 2020, 2020)
    assert dropped == 0 and rows[0][3] == 1.0


def test_an_all_incomplete_week_is_zero_yards_by_definition():
    con = _spine([
        (2020, 1, "REG", "g1", 1, "a", "receiver", 1, 0, 0, 14.0, None),
        (2020, 1, "REG", "g1", 2, "a", "receiver", 1, 0, 0, 3.0, None),
    ])
    rows, dropped = residual._spine_rows(con, "receiving_yards", 2020, 2020)
    assert dropped == 0
    assert rows == [("a", 2020, 1, 0.0, (2.0, 17.0))]


def test_a_carry_with_no_yards_drops_the_week():
    con = _spine([
        (2020, 1, "REG", "g1", 1, "r", "rusher", 0, 0, 1, None, None),
        (2020, 1, "REG", "g1", 2, "r", "rusher", 0, 0, 1, None, 4.0),
    ])
    assert residual._spine_rows(con, "rushing_yards", 2020, 2020) == ([], 1)


def _weekly(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE nfl_player_week (gsis_id, season, week, "
                "season_type, data_version, position, targets, receiving_tds, "
                "carries, rushing_tds)")
    con.executemany("INSERT INTO nfl_player_week VALUES (?,?,?,?,?,?,?,?,?,?)",
                    rows)
    return con


def test_weekly_rows_read_the_newest_data_version_and_drop_nulls():
    con = _weekly([
        ("a", 2020, 1, "REG", "2026-01-01", "WR", 5, 0, None, None),
        ("a", 2020, 1, "REG", "2026-02-01", "WR", 6, 1, None, None),
        ("b", 2020, 1, "REG", "2026-02-01", "WR", 4, None, None, None),
        ("d", 2020, 1, "REG", "2026-02-01", "LB", None, None, None, None),
    ])
    rows, dropped = residual._weekly_rows(con, "receiving_tds", 2020, 2020)
    assert rows == [("a", 2020, 1, 1.0, (6.0,))]
    assert dropped == 1            # b had volume and no TD figure; d had nothing


def test_positions_refuse_a_player_with_two_groups():
    con = _weekly([
        ("a", 2020, 1, "REG", "v", "WR", 1, 0, 0, 0),
        ("a", 2021, 1, "REG", "v", "RB", 1, 0, 0, 0),
    ])
    with pytest.raises(AssertionError, match="as-of"):
        residual.positions(con)
    ok = _weekly([("a", 2020, 1, "REG", "v", "FB", 1, 0, 0, 0)])
    assert residual.positions(ok) == {"a": "RB"}


# ---------------------------------------------------------------------------
# what gets published
# ---------------------------------------------------------------------------

def test_player_values_need_min_games_and_resample_games():
    res = {(2020, "WR", "a"): [(w, 5.0, 1.0) for w in range(1, residual.MIN_GAMES)],
           (2020, "WR", "b"): [(w, 5.0, 1.0 if w % 2 else -1.0)
                               for w in range(1, 11)]}
    got = residual.player_values("t", res)
    assert [(p, sl) for p, sl, _e in got] == [("b", "2020|WR")]
    est = got[0][2]
    assert est.n == 10 and est.est == pytest.approx(0.0)
    assert est.lo < 0 < est.hi


def test_band_slices_name_quantiles_and_never_an_order():
    vals = [("p%d" % i, "2020|WR", metrics_estimate(i - 20.0))
            for i in range(40)]
    got = residual.band_values("t", vals)
    assert [sl for _s, sl, _e in got] == ["2020|WR|q%02d" % q
                                          for q in residual.QUANTILES]
    assert all(s == "_league" for s, _sl, _e in got)
    ests = [e.est for _s, _sl, e in got]
    assert ests == sorted(ests)
    assert all(e.lo <= e.est <= e.hi and e.n == 40 for _s, _sl, e in got)


def metrics_estimate(x):
    from analytics.intervals import Estimate
    return Estimate(x, x - 1, x + 1, 5, "t")


def test_a_band_cell_below_min_players_is_not_published():
    vals = [("p%d" % i, "2020|TE", metrics_estimate(float(i)))
            for i in range(residual.MIN_PLAYERS - 1)]
    assert residual.band_values("t", vals) == []


def test_every_family_builds_a_metric_the_registry_accepts():
    for stat in residual.PAIRINGS:
        for family in residual.FAMILIES:
            m = residual.metric_for(stat, family, note="x")
            assert m.key.startswith("opportunity_residual.")
            assert m.requires == residual.REQUIRES[stat]


def test_the_note_is_appended_to_the_derived_range_not_substituted(monkeypatch):
    monkeypatch.setattr(metrics, "derive_range",
                        lambda con, m: (2009, 2026, "starts 2009: derived"))
    monkeypatch.setattr(metrics, "live_season", lambda con: 2026)
    con = sqlite3.connect(":memory:")
    row = metrics.register(con, residual.metric_for("receptions", "fit",
                                                    note="R^2 WR median 0.777"))
    assert row["range_note"] == "starts 2009: derived; R^2 WR median 0.777"
    bare = metrics.register(con, residual.metric_for("receptions", "fit"))
    assert bare["range_note"] == "starts 2009: derived"


def test_the_fit_note_is_computed_from_the_fits():
    fits = {(2020, "WR"): {"r2": 0.5}, (2021, "WR"): {"r2": 0.7},
            (2020, "TE"): {"r2": 0.6}}
    note = residual.fit_note("receptions", fits)
    assert "WR median 0.600 (0.500-0.700, 2 seasons)" in note
    assert "TE median 0.600" in note and "RB" not in note
    assert "not as-of" in note


# ---------------------------------------------------------------------------
# a-21: kneels, per game, as-of, stale notes, survivorship
# ---------------------------------------------------------------------------

def test_a_kneel_leaves_both_sides_of_the_rushing_fit():
    con = _spine([
        (2020, 1, "REG", "g1", 1, "q", "rusher", 0, 0, 1, None, 7.0),
        (2020, 1, "REG", "g1", 2, "q", "rusher", 0, 0, 1, None, -1.0),
        (2020, 2, "REG", "g2", 9, "q", "rusher", 0, 0, 1, None, -2.0),
    ])
    kneels = {("g1", 2): ("q", 2020, 1), ("g2", 9): ("q", 2020, 2)}
    # without the exclusion a kneel is a carry and its -1 counts
    assert residual._spine_rows(con, "rushing_yards", 2020, 2020) == (
        [("q", 2020, 1, 6.0, (2.0,)), ("q", 2020, 2, -2.0, (1.0,))], 0)
    rows, dropped = residual._spine_rows(con, "rushing_yards", 2020, 2020, kneels)
    # week 1 keeps one carry and its 7 yards; week 2 was only a kneel, so the
    # player had no volume that week and is not in the population
    assert rows == [("q", 2020, 1, 7.0, (1.0,))] and dropped == 0


def test_kneels_come_off_weekly_carries_and_never_go_negative():
    con = _weekly([
        ("q", 2020, 1, "REG", "v", "QB", None, None, 5, 1),
        ("q", 2020, 2, "REG", "v", "QB", None, None, 2, 0),
        ("q", 2020, 3, "REG", "v", "QB", None, None, 1, 0),
    ])
    kneels = {("g1", 1): ("q", 2020, 1), ("g1", 2): ("q", 2020, 1),
              ("g2", 1): ("q", 2020, 2), ("g2", 2): ("q", 2020, 2),
              ("g3", 1): ("q", 2020, 3), ("g3", 2): ("q", 2020, 3)}
    rows, dropped = residual._weekly_rows(con, "rushing_tds", 2020, 2020, kneels)
    # 5 carries - 2 kneels = 3; week 2 was all kneels (out of the population);
    # week 3 has more kneels than carries - the feeds disagree - and drops
    assert rows == [("q", 2020, 1, 1.0, (3.0,))]
    assert dropped == 1
    # a caller passing no kneels (a test, or a non-rushing path) subtracts nothing
    assert residual._weekly_rows(con, "rushing_tds", 2020, 2020)[0][0][4] == (5.0,)


def test_a_rushing_stat_refuses_to_read_without_the_kneels():
    for stat in residual.RUSHING:
        with pytest.raises(ValueError, match="kneel"):
            residual.rows_for(None, None, stat, 2020, 2020)


def test_the_per_game_value_is_the_sum_over_games_from_the_same_draws():
    res = {(2020, "WR", "b"): [(w, 5.0, float(w % 3)) for w in range(1, 11)]}
    (_p, _sl, s), = residual.player_values("t", res)
    (_p, _sl, g), = residual.player_values("t", res, per_game=True)
    assert g.est == pytest.approx(s.est / 10)
    assert g.n == s.n == 10
    # not a rescaled interval: games are resampled, so the per-game bounds
    # are the per-draw mean, which for a fixed n is the sum's bounds / n
    assert g.lo == pytest.approx(s.lo / 10) and g.hi == pytest.approx(s.hi / 10)


def test_the_as_of_value_cannot_see_later_weeks_and_the_full_season_can():
    rows, groups = _league(persistent_efficiency=False, seasons=(2020,),
                           players=60, weeks=8)
    later = [(p, s, w, (y + 500.0 if w > 4 and p == "p000" else y), xs)
             for p, s, w, y, xs in rows]
    def at4(rs):
        return {(p, sl): e.est for p, sl, e in residual.asof_values("t", rs, groups)
                if sl.endswith("|w04")}
    assert at4(rows) == at4(later)
    # the full-season value of another player DOES move: the line moved
    full = lambda rs: {p: e.est for p, sl, e in residual.player_values(
        "t", residual.residuals(residual.fit(rs, groups)), per_game=True)}
    assert full(rows)["p001"] != pytest.approx(full(later)["p001"])
    # and every as-of slice names its week and needs ASOF_MIN_GAMES
    got = residual.asof_values("t", rows, groups)
    weeks = {sl.split("|")[2] for _p, sl, _e in got}
    assert weeks == {"w%02d" % t for t in range(residual.ASOF_MIN_GAMES, 9)}
    assert all(e.n >= residual.ASOF_MIN_GAMES for _p, _sl, e in got)


def test_fit_through_week_is_the_fit_on_those_weeks_alone():
    rows, groups = _league(persistent_efficiency=False, seasons=(2020,),
                           players=40, weeks=6)
    a = residual.fit(rows, groups, through_week=3)[(2020, "WR")]
    b = residual.fit([r for r in rows if r[2] <= 3], groups)[(2020, "WR")]
    assert np.allclose(a["beta"], b["beta"]) and a["r2"] == b["r2"]


def _store_with_fit(r2s, note):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE f_metrics (metric, range_note)")
    con.execute("CREATE TABLE f_metric_values (metric, slice, est)")
    con.execute("INSERT INTO f_metrics VALUES (?,?)",
                ("opportunity_residual.fit.receptions", note))
    con.executemany("INSERT INTO f_metric_values VALUES (?,?,?)",
                    [("opportunity_residual.fit.receptions", "%d|WR" % (2000 + i), v)
                     for i, v in enumerate(r2s)])
    return con


def test_check_notes_catches_the_upper_middle_median_and_passes_the_true_one():
    r2s = [0.50, 0.58, 0.584, 0.70]      # median 0.582; upper-middle 0.584
    stale = _store_with_fit(r2s, "R^2 WR median 0.584 (0.500-0.700, 4 seasons)")
    assert residual.check_notes(stale) == [
        ("opportunity_residual.fit.receptions", "WR", "0.584", "0.582")]
    ok = _store_with_fit(r2s, "R^2 WR median 0.582 (0.500-0.700, 4 seasons)")
    assert residual.check_notes(ok) == []


def test_the_fit_note_takes_its_medians_from_the_published_values():
    fits = {(2020, "WR"): {"r2": 0.9}, (2021, "WR"): {"r2": 0.9}}
    note = residual.fit_note("receptions", fits,
                             {(2020, "WR"): 0.5, (2021, "WR"): 0.7})
    assert "WR median 0.600" in note


def test_survivorship_counts_leavers_and_ignores_an_unfinished_season():
    res = {}
    for p in range(40):
        pid = "p%02d" % p
        res[(2020, "WR", pid)] = [(w, 5.0, 1.0 if p < 20 else -1.0) for w in range(1, 9)]
        if p < 20:                        # the good half returns
            res[(2021, "WR", pid)] = [(w, 5.0, 1.0) for w in range(1, 9)]
        # 2022 is in progress: two games each, nobody qualifies
        res[(2022, "WR", pid)] = [(w, 5.0, 0.0) for w in range(1, 3)]
    d = residual.survivorship(res)["WR"]
    # 2020 -> 2021 only: 20 of 40 leave, and they are the negative half. 2021
    # -> 2022 is not counted, or all 20 returners would read as leaving
    assert d["leave"] == (20, 40)
    assert d["mean"] == (pytest.approx(-1.0), pytest.approx(1.0))
    note = residual.survivorship_note("receptions", {"WR": d})
    assert "20 of 40" in note and ">= %d games" % residual.MIN_GAMES in note


def test_the_games_note_measures_how_far_a_sum_sorts_by_games():
    from analytics.intervals import Estimate
    grows = [("p%d" % g, "s", Estimate(float(g), 0, 2 * g, g, "t"))
             for g in range(5, 17)]
    flat = [("p%d" % g, "s", Estimate(1.0 + (g % 2) * 0.1, 0, 2, g, "t"))
            for g in range(5, 17)]
    assert "r 1.00 over 12" in residual.games_note(grows)
    assert "r 1.00" not in residual.games_note(flat)


def test_the_kneel_note_counts_by_group_and_is_empty_off_the_rushing_stats():
    assert residual.kneel_note("receptions", {"WR": 3}) == ""
    n = residual.kneel_note("rushing_yards", {"QB": 90, "RB": 2})
    assert "92 kneel plays removed (RB 2, QB 90" in n
