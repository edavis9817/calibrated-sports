"""The team unit table and the interval it is published with (unit a-25).

Every guard here is shown returning BOTH answers: a check that can only pass
tells you nothing. No test touches a store - the facts are built from a parquet
in `tmp_path` and the metrics are computed on an in-memory database.
"""
import math
import sqlite3

import pytest

from analytics import pace, spine, team_units
from analytics.intervals import ratio_t


# =============================================================================
# ratio_t
# =============================================================================

def test_ratio_t_is_the_ratio_of_sums_with_n_as_blocks():
    e = ratio_t({"g1": (3, 10), "g2": (7, 10), "g3": (5, 20)})
    assert e.est == pytest.approx(15 / 40)
    assert e.n == 3 and e.rows == 40
    assert e.lo < e.est < e.hi
    assert e.method == "cluster_t95"


def test_ratio_t_matches_the_formula():
    blocks = {"a": (6.0, 10.0), "b": (2.0, 10.0)}
    est = 8 / 20
    ss = (6 - est * 10) ** 2 + (2 - est * 10) ** 2
    se = math.sqrt(2 / 1 * ss) / 20
    e = ratio_t(blocks)
    assert e.lo == pytest.approx(est - 12.706 * se)
    assert e.hi == pytest.approx(est + 12.706 * se)


def test_two_games_that_agree_do_not_make_a_narrow_interval_by_luck():
    """The bootstrap's failure: two similar games -> a tiny interval. The t
    interval at df=1 is 12.7 standard errors wide either side, so two games
    that differ by 0.02 still give an interval of about +-0.1."""
    e = ratio_t({"a": (0.21 * 50, 50), "b": (0.23 * 50, 50)})
    assert e.hi - e.lo > 0.2
    # and the same spread over ten games is much narrower - it can answer both
    ten = {i: ((0.21 if i % 2 else 0.23) * 50, 50) for i in range(10)}
    assert ratio_t(ten).hi - ratio_t(ten).lo < 0.05


def test_bounds_clip_a_rate_and_a_zero_se_is_not_certainty():
    wide = ratio_t({"a": (1, 10), "b": (9, 10)}, bounds=(0.0, 1.0))
    assert wide.lo == 0.0 and wide.hi == 1.0
    same = ratio_t({"a": (0, 10), "b": (0, 12)}, bounds=(0.0, 1.0))
    assert (same.lo, same.hi) == (0.0, 1.0), "identical blocks read as certain"
    unb = ratio_t({"a": (5, 10), "b": (5, 10)})
    assert math.isinf(unb.lo) and math.isinf(unb.hi)


def test_one_block_is_unbounded_and_zero_blocks_have_no_estimate():
    one = ratio_t({"a": (3, 10)})
    assert one.n == 1 and math.isinf(one.hi)
    assert ratio_t({}).est is None and ratio_t({"a": (3, 0)}).n == 0


# =============================================================================
# the facts: spine.build_units
# =============================================================================

def _play(game, pid, off, deff, **kw):
    row = {c: None for c in spine.PBP_COLUMNS}
    row.update(season=2024, week=1, season_type="REG", game_id=game,
               play_id=float(pid), posteam=off, defteam=deff,
               score_differential=0.0, down=1.0, ydstogo=10.0, wp=0.5,
               half_seconds_remaining=900.0, game_seconds_remaining=3000.0 - pid,
               qtr=1.0, play_type="run", qb_kneel=0.0, qb_spike=0.0,
               qb_dropback=0.0, pass_attempt=0.0, rush_attempt=1.0,
               complete_pass=0.0, shotgun=0.0, fixed_drive=1.0,
               yards_gained=3.0, epa=0.1, sack=0.0, qb_hit=0.0)
    row["pass"] = 0.0
    row.update(kw)
    return row


def _write(tmp_path, rows, name="pbp.parquet"):
    import polars as pl
    p = tmp_path / name
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(p)
    return str(p)


def _pass(game, pid, off, deff, **kw):
    base = {"play_type": "pass", "pass": 1.0, "qb_dropback": 1.0,
            "pass_attempt": 1.0, "rush_attempt": 0.0}
    base.update(kw)
    return _play(game, pid, off, deff, **base)


def _pbp(tmp_path):
    rows = [
        _play("G1", 1, "AAA", "BBB", epa=0.5),                      # early run
        _pass("G1", 2, "AAA", "BBB", epa=-1.0, sack=1.0),           # early sack
        _pass("G1", 3, "AAA", "BBB", down=3.0, epa=2.0, qb_hit=1.0),  # 3rd down
        _pass("G1", 4, "AAA", "BBB", wp=0.95, epa=0.3),             # garbage time
        _play("G1", 5, "AAA", "BBB", play_type="qb_kneel", epa=-0.2),  # kneel
        _pass("G1", 6, "BBB", "AAA", epa=0.4),
    ]
    return _write(tmp_path, rows)


def _units(tmp_path, participation=None):
    df = spine.build_units(_pbp(tmp_path), participation)
    return {(r["team"], r["situation"]): r for r in
            df.rename({"posteam": "team", "defteam": "opponent"}).to_dicts()}


def test_units_count_the_components_and_exclude_kneels(tmp_path):
    u = _units(tmp_path)
    a = u[("AAA", "all")]
    assert a["plays"] == 4, "the kneel is not a play"
    assert a["dropbacks"] == 3 and a["early_plays"] == 3
    assert a["early_dropbacks"] == 2
    assert a["epa_plays"] == 4 and a["epa_sum"] == pytest.approx(1.8)
    assert a["sack_or_hit"] == 2


def test_neutral_drops_garbage_time_and_is_the_spine_rule(tmp_path):
    n = _units(tmp_path)[("AAA", "neutral")]
    assert n["plays"] == 3, "wp 0.95 is garbage time and must be excluded"
    assert n["early_plays"] == 2 and n["early_dropbacks"] == 1


def test_no_participation_is_null_not_zero(tmp_path):
    a = _units(tmp_path)[("AAA", "all")]
    assert a["charted_dropbacks"] is None and a["pressures"] is None


def test_participation_is_joined_and_counted_over_dropbacks(tmp_path):
    import polars as pl
    part = tmp_path / "part.parquet"
    pl.DataFrame({"nflverse_game_id": ["G1", "G1", "G1", "G1"],
                  "play_id": [1, 2, 3, 4],
                  "was_pressure": [True, True, None, False]}).write_parquet(part)
    a = _units(tmp_path, str(part))[("AAA", "all")]
    # play 1 is a run: its pressure flag must not count
    assert a["charted_dropbacks"] == 2 and a["pressures"] == 1


def test_neutral_definition_follows_the_constants(monkeypatch):
    assert "20% and 80%" in spine.neutral_definition()
    monkeypatch.setattr(spine, "NEUTRAL_WP", (0.10, 0.90))
    assert "10% and 90%" in spine.neutral_definition()


# =============================================================================
# the metrics: team_units.compute and the proxy sentence
# =============================================================================

def _db(rows):
    con = sqlite3.connect(":memory:")
    con.executescript(spine.SCHEMA)
    con.executemany("INSERT INTO f_team_game_units VALUES (%s)"
                    % ",".join("?" * len(spine.UNITS_COLS)), rows)
    return con


def _row(game, team, opp, sack_or_hit, dropbacks, pressures=None, charted=None,
         season=2024, situation="all"):
    return (season, 1, "REG", game, team, opp, situation, 60, dropbacks, 40,
            20, 60, 3.0, sack_or_hit, charted, pressures)


SOH_DEF = next(s for s in team_units.SPECS
               if s.key == "team_units.sack_or_hit_rate.defense.by_season")
SOH_OFF = next(s for s in team_units.SPECS
               if s.key == "team_units.sack_or_hit_rate.offense.by_season")


def test_defence_is_read_from_the_other_side():
    con = _db([_row("G1", "AAA", "BBB", 5, 40), _row("G1", "BBB", "AAA", 2, 30),
               _row("G2", "AAA", "CCC", 3, 30), _row("G2", "CCC", "AAA", 4, 20)])
    off = {t: e for t, _s, e in team_units.compute(con, SOH_OFF, 2024, 2024)}
    dfn = {t: e for t, _s, e in team_units.compute(con, SOH_DEF, 2024, 2024)}
    assert off["AAA"].est == pytest.approx(8 / 70)
    assert dfn["AAA"].est == pytest.approx(6 / 50), "AAA's defence faced BBB and CCC"
    assert "BBB" not in off, "one game is below MIN_GAMES"


def test_min_games_refuses_one_game_and_admits_two():
    one = _db([_row("G1", "AAA", "BBB", 5, 40)])
    assert team_units.compute(one, SOH_OFF, 2024, 2024) == []
    two = _db([_row("G1", "AAA", "BBB", 5, 40), _row("G2", "AAA", "CCC", 3, 30)])
    (_t, season, e), = team_units.compute(two, SOH_OFF, 2024, 2024)
    assert season == "2024" and e.n == 2 and e.rows == 70


def test_proxy_sentence_reports_the_measured_gap_and_can_say_they_agree():
    far = _db([_row("G1", "AAA", "BBB", 10, 100, pressures=30, charted=100)])
    assert "0.33 times the charted pressure rate" in team_units.proxy_sentence(far)
    near = _db([_row("G1", "AAA", "BBB", 30, 100, pressures=30, charted=100)])
    assert "about the same size" in team_units.proxy_sentence(near)
    none = _db([_row("G1", "AAA", "BBB", 10, 100)])
    assert "cannot be measured" in team_units.proxy_sentence(none)


def test_every_spec_names_a_real_column_and_rates_are_bounded():
    for s in team_units.SPECS:
        assert s.num in spine.UNITS_COLS and s.den in spine.UNITS_COLS, s.key
        if "rate" in s.key:
            assert s.bounds == (0.0, 1.0), s.key
        assert s.key.endswith(".by_season"), s.key
    # a pbp metric may not be called a pressure rate - that is the proxy error
    for s in team_units.SPECS:
        if "pressure_rate" in s.key:
            assert s.basis == "participation" and s.availability == "historical"


def test_neutral_metrics_state_the_definition_in_their_unit():
    con = _db([_row("G1", "AAA", "BBB", 10, 100, pressures=30, charted=100)])
    for s in team_units.SPECS:
        m = team_units.metric_for(s, con)
        assert "{" not in m.unit, "an unfilled placeholder reached the unit"
        if s.situation == "neutral":
            assert spine.neutral_definition() in m.unit, s.key


# =============================================================================
# pace: a game with no timed snap is not a block
# =============================================================================

def test_pace_does_not_count_an_untimed_game_as_a_sample():
    """1999 has 374 neutral team-games with no timed snaps. The bootstrap
    version counted each as a block, publishing TEN 1999 at n=16 with a
    zero-width interval from the one game that was timed."""
    con = sqlite3.connect(":memory:")
    con.executescript(spine.SCHEMA)
    rows = [(1999, w, "REG", "G%d" % w, "TEN", "X", "neutral", 30,
             300.0 if w <= 3 else None, 10 if w <= 3 else 0, 8)
            for w in range(1, 7)]
    con.executemany("INSERT INTO f_team_game_pace VALUES (%s)"
                    % ",".join("?" * len(spine.PACE_COLS)), rows)
    (_t, _s, secs), = pace.compute(con, "seconds_per_play", 1999, 1999, True)
    assert secs.n == 3
    (_t, _s, ppg), = pace.compute(con, "plays_per_game", 1999, 1999, True)
    assert ppg.n == 6 and ppg.est == pytest.approx(30)
    # the pooled headline still needs five games
    assert pace.compute(con, "seconds_per_play", 1999, 1999, False) == []
