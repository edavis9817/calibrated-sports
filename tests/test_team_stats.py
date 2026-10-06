"""a-66: nflverse stats_team -> nfl_team_week, the pre-game weather reader, stadium
zones, and what each puts on a matchup.

Run: pytest -q tests/test_team_stats.py

No network and no live store. The parquet a normalizer reads is built in the test; the
feeds store is a tmp one; the matchup is a-64's synthetic league with a synthetic
context.
"""
import io
import json
import os
import sqlite3

import polars as pl
import pytest

import config
import nflverse
import store
from feeds import nfl_venues, paths, weather_read
from jobs import game_matchup as GM
from jobs import ingest_feeds as J
from jobs import ingest_nflverse as N
from tests.test_game_export import NOW
from tests.test_game_matchup import (_build, _matchups, measured,  # noqa: F401
                                     results_on_this_walk)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(HERE, "research", "results", "a66_stats_team_audit.json")


def _parquet(rows):
    buf = io.BytesIO()
    pl.DataFrame(rows).write_parquet(buf)
    return buf.getvalue()


def _row(team="PIT", week=1, **kw):
    base = {"season": 2026, "week": week, "team": team, "season_type": "REG",
            "game_id": f"2026_{week:02d}_X_{team}", "opponent_team": "CLE"}
    base.update({c: 1.0 for c in store.TEAM_WEEK_COLS})
    base.update(kw)
    return base


# ---------------------------------------------------------------- the normalizer

def test_a_team_game_keeps_nflverses_names_and_values():
    table, cols, rows = N.normalize_team_stats(
        _parquet([_row(passing_epa=-3.25, rushing_yards=141.0)]), "2026-10-05")
    assert table == "nfl_team_week" and len(rows) == 1
    r = dict(zip(cols, rows[0]))
    assert (r["team"], r["opponent"], r["season"], r["week"]) == ("PIT", "CLE", 2026, 1)
    assert r["passing_epa"] == -3.25 and r["rushing_yards"] == 141.0
    assert r["data_version"] == "2026-10-05" and r["sport"] == "nfl"


def test_a_column_the_file_lacks_is_null_never_zero():
    row = _row()
    del row["passing_cpoe"]
    _t, cols, rows = N.normalize_team_stats(_parquet([row]), "v")
    r = dict(zip(cols, rows[0]))
    assert r["passing_cpoe"] is None
    assert r["passing_epa"] == 1.0          # the other answer, on the same row


def test_one_keyless_row_is_dropped_and_many_refuse_the_file(capsys):
    keyless = _row(team=None)
    _t, _c, rows = N.normalize_team_stats(_parquet([_row(), keyless]), "v")
    assert len(rows) == 1
    assert "1 keyless row(s) dropped of 2" in capsys.readouterr().out
    many = [_row()] + [_row(team=None, week=w) for w in range(1, N.TEAM_STATS_MAX_KEYLESS + 2)]
    with pytest.raises(ValueError, match="without team"):
        N.normalize_team_stats(_parquet(many), "v")


def test_two_rows_for_one_team_week_refuse_rather_than_overwrite():
    with pytest.raises(ValueError, match="duplicate team-week"):
        N.normalize_team_stats(_parquet([_row(), _row(passing_yards=999.0)]), "v")
    # ... and two different weeks do not
    assert len(N.normalize_team_stats(_parquet([_row(), _row(week=2)]), "v")[2]) == 2


def test_the_week_filter_moves_only_that_week():
    data = _parquet([_row(week=1), _row(week=2), _row(week=3)])
    assert [r[3] for r in N.normalize_team_stats(data, "v", week=2)[2]] == [2]


def test_the_table_takes_what_the_normalizer_writes(tmp_path, monkeypatch):
    db = str(tmp_path / "m.db")
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    store.init_db()
    table, cols, rows = N.normalize_team_stats(_parquet([_row(), _row(team="CLE")]), "v1")
    assert store.replace_rows(table, cols, rows) == 2
    # a later version sits beside the earlier one; it does not replace it
    _t, cols2, rows2 = N.normalize_team_stats(_parquet([_row(passing_yards=300.0)]), "v2")
    store.replace_rows(table, cols2, rows2)
    con = sqlite3.connect(db)
    got = con.execute("SELECT data_version, passing_yards FROM nfl_team_week WHERE team='PIT' "
                      "ORDER BY data_version").fetchall()
    con.close()
    assert got == [("v1", 1.0), ("v2", 300.0)]


def test_the_dataset_is_live_tier_and_normalized_so_the_weekly_refresh_carries_it():
    ds = nflverse.DATASETS["team_stats"]
    assert ds.tier == nflverse.LIVE and ds.normalize and ds.seasonal
    assert ds.asset(2026) == "stats_team_week_2026.parquet"
    assert "team_stats" in N.NORMALIZERS


# ------------------------------------------------- the silent-zero class, pinned

def test_the_not_collected_ranges_are_the_ones_the_audit_measured():
    with open(AUDIT, encoding="utf-8") as f:
        audit = json.load(f)
    measured_holes = {c: (v["seasons"][0], v["seasons"][-1]) for c, v in audit["silent"].items()}
    assert measured_holes == nflverse.TEAM_STATS_NOT_COLLECTED
    for c, v in audit["silent"].items():       # each hole is one unbroken run
        assert v["seasons"] == list(range(v["seasons"][0], v["seasons"][-1] + 1)), c


def test_a_reader_is_told_a_hole_season_was_not_collected():
    assert not nflverse.team_stat_collected("def_tackles_for_loss", 2007)
    assert nflverse.team_stat_collected("def_tackles_for_loss", 2012)
    assert nflverse.team_stat_collected("def_tackles_for_loss", 2002)   # before the hole
    assert not nflverse.team_stat_collected("passing_cpoe", 1999)
    assert nflverse.team_stat_collected("passing_yards", 2003)


def test_the_audit_supports_what_the_matchup_publishes_and_withholds():
    with open(AUDIT, encoding="utf-8") as f:
        audit = json.load(f)
    rec = audit["reconcile"]
    # published from the team rows: interceptions exact, yards off on a handful
    assert rec["passing_interceptions"]["unequal"] == 0
    assert rec["passing_yards"]["unequal"] <= 5 and rec["rushing_yards"]["unequal"] <= 5
    # NOT published: fumbles lost disagree with the player rows
    assert rec["fumbles_lost_total"]["unequal"] > 0
    assert "fumbles" not in " ".join(GM.TEAM_WEEK_READ)
    for season, res in audit["epa_definition"].items():
        rush = res["rushing_epa"]["epa on play_type run or qb_kneel with a named rusher"]
        assert rush["within_0.01"] == rush["team_games"], season      # per carry is exact
        # no candidate reproduces passing_epa, so it gets no per-play figure
        assert all(v["within_0.01"] < v["team_games"] for v in res["passing_epa"].values())


# ------------------------------------------------------- forecast, never a record

@pytest.fixture
def feeds(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    paths.ensure_dirs()
    con = J.connect()
    yield con
    con.close()


KICK = NOW + 3 * 86400


def _wx(con, game_id, kind, fetched, wind, valid_from, valid_to=None, kickoff=KICK):
    from feeds import schema
    cols = schema.columns("weather_at_kickoff") + [c for c, _t in schema.META]
    row = dict.fromkeys(cols)
    row.update(sport="nfl", game_id=game_id, provider="open-meteo", kind=kind, venue_id=1,
               kickoff_ts=kickoff, observed_hour_ts=kickoff, temperature_f=55.0,
               wind_speed_mph=wind, fetched_ts=fetched, playing_conditions=1,
               src_dataset="weather_nfl", src_file_id=1, row_sha=f"{kind}{fetched}{wind}",
               valid_from_ts=valid_from, valid_to_ts=valid_to)
    con.execute(f"INSERT INTO weather_at_kickoff ({', '.join(cols)}) VALUES "
                f"({', '.join('?' * len(cols))})", [row[c] for c in cols])
    con.commit()


def test_a_pregame_read_returns_the_forecast_with_its_lead_time(feeds):
    _wx(feeds, "G1", "forecast", NOW - 3600, 12.0, NOW - 3600)
    got = weather_read.pregame_forecasts(feeds, NOW)
    assert set(got) == {"G1"}
    assert got["G1"]["wind_speed_mph"] == 12.0 and got["G1"]["kind"] == "forecast"
    assert got["G1"]["lead_hours"] == pytest.approx((KICK - (NOW - 3600)) / 3600)


def test_recorded_weather_never_reaches_a_pregame_read(feeds):
    _wx(feeds, "G1", "archive", None, 31.0, NOW - 100)
    assert weather_read.pregame_forecasts(feeds, NOW) == {}
    # the record IS in the store, and its own reader finds it
    assert weather_read.recorded(feeds)["G1"]["wind_speed_mph"] == 31.0


def test_a_forecast_row_fetched_after_kickoff_is_not_a_forecast(feeds):
    past = NOW - 2 * 86400                     # a game already played
    _wx(feeds, "OLD", "forecast", NOW - 3600, 25.0, NOW - 3600, kickoff=past)
    assert weather_read.pregame_forecasts(feeds, NOW) == {}
    # the same row taken BEFORE that kickoff is one
    _wx(feeds, "OLD2", "forecast", past - 7200, 9.0, past - 7200, kickoff=past)
    assert set(weather_read.pregame_forecasts(feeds, NOW)) == {"OLD2"}


def test_an_as_of_read_does_not_see_a_later_forecast(feeds):
    _wx(feeds, "G1", "forecast", NOW - 86400, 8.0, NOW - 86400, valid_to=NOW + 600)
    _wx(feeds, "G1", "forecast", NOW + 600, 20.0, NOW + 600)
    assert weather_read.pregame_forecasts(feeds, NOW)["G1"]["wind_speed_mph"] == 8.0
    assert weather_read.pregame_forecasts(feeds, NOW + 3600)["G1"]["wind_speed_mph"] == 20.0
    with pytest.raises(ValueError):
        weather_read.pregame_forecasts(feeds, None)


def test_the_pregame_reader_has_no_argument_that_selects_a_record():
    import inspect
    assert list(inspect.signature(weather_read.pregame_forecasts).parameters) == [
        "con", "as_of_ts", "game_ids"]


# ------------------------------------------------------------- zones and country

def test_every_usable_stadium_point_has_one_country_and_a_real_zone():
    from zoneinfo import ZoneInfo
    points = nfl_venues.load_points()
    usable = {q: p for q, p in points.items() if p["offset_km"] is not None}
    assert len(usable) >= 40
    for q, p in usable.items():
        assert len(p["country_code"]) == 2 and p["country_source"] == "wikidata:P17/P297", q
        ZoneInfo(p["timezone"])                 # raises on a key the tz database lacks
        assert p["timezone_source"] == "open-meteo:timezone=auto", q
    assert {p["country_code"] for p in usable.values()} > {"US"}     # the other answer exists


def test_zones_crossed_is_measured_at_the_kickoff_and_the_short_way_round():
    sep, nov = 1789000000.0, 1794300000.0       # 2026-09-10 and 2026-11-10
    z = nfl_venues.zones_crossed
    assert z("America/New_York", "America/New_York", sep) == 0
    assert z("America/New_York", "America/Los_Angeles", sep) == 3
    # Arizona keeps no daylight time: two hours from Chicago in September, one in November
    assert z("America/Chicago", "America/Phoenix", sep) == 2
    assert z("America/Chicago", "America/Phoenix", nov) == 1
    # Melbourne is 17-18 hours ahead of Los Angeles, which is 6-7 the short way
    assert z("America/Los_Angeles", "Australia/Melbourne", sep) == 7
    assert z("America/New_York", None, sep) is None


# ------------------------------------------------------------------ the matchup

def _with(monkeypatch, measured, team_week=None, forecast=None, reason=None):  # noqa: F811
    real = GM.build

    def build(m, fc, rec, now, **kw):
        ctx = kw["ctx"]
        if team_week is not None:
            ctx["team_week"] = team_week(m)
            ctx["team_week_reason"] = reason
        ctx["forecast"] = forecast or {}
        ctx["forecast_reason"] = None
        return real(m, fc, rec, now, **kw)
    files, failed = _build_through(measured, monkeypatch, build)
    assert failed == []
    return _matchups(files)


def _build_through(measured, monkeypatch, build):  # noqa: F811
    import functools

    from jobs import game_export as X
    from tests.test_game_matchup import _base, _ctx, _pace
    files = _base(measured)
    monkeypatch.setattr(GM, "build", functools.partial(
        build, ctx=_ctx(measured), pace=_pace(measured), pace_err=None))
    failed = []
    X.add_matchups(measured, files, failed, NOW, log=lambda *_: None)
    return files, failed


def _team_rows(m, skip=None):
    from models import game as G
    out = {}
    for g in m["games"]:
        if g["season"] != m["year"] or g["home_score"] is None or g["game_id"] == skip:
            continue
        for t in (g["home"], g["away"]):
            out[(g["game_id"], G.franchise(t))] = {
                "passing_yards": 250.0, "rushing_yards": 100.0, "passing_interceptions": 2.0,
                "passing_epa": 4.0, "rushing_epa": -2.0, "carries": 25.0}
    return out


def test_units_read_the_team_rows_where_they_cover_the_season(
        measured, monkeypatch, results_on_this_walk):  # noqa: F811
    mus = _with(monkeypatch, measured, team_week=_team_rows)
    seen = 0
    for mu in mus.values():
        for side in ("home", "away"):
            t = mu["teams"][side]
            if not t["games"]:
                continue
            u = t["units"]
            seen += 1
            assert u["source"] == "team_rows"
            assert u["passing_yards_per_game_for"] == 250.0      # not the player rows' 220
            assert u["interceptions_thrown"] == 2 * t["games"]
            assert u["passing_epa_per_game_for"] == 4.0
            assert u["rushing_epa_per_game_against"] == -2.0
            assert u["rushing_epa_per_carry_for"] == pytest.approx(-2.0 / 25.0)
            assert u["epa_reason"] is None
            assert "passing_epa_per_dropback_for" not in u
    assert seen


def test_without_team_rows_the_units_fall_back_and_the_epa_says_why(
        measured, monkeypatch, results_on_this_walk):  # noqa: F811
    mus = _with(monkeypatch, measured, team_week=lambda m: {},
                reason="the store holds no team-game totals (no such table: nfl_team_week)")
    seen = 0
    for mu in mus.values():
        for side in ("home", "away"):
            t = mu["teams"][side]
            if not t["games"]:
                continue
            u = t["units"]
            seen += 1
            assert u["source"] == "player_rows"
            assert u["passing_yards_per_game_for"] == 220.0
            assert u["passing_epa_per_game_for"] is None
            assert "no such table" in u["epa_reason"]
    assert seen


def test_a_season_the_team_rows_cover_in_part_is_not_published_from_them(
        measured, monkeypatch, results_on_this_walk):  # noqa: F811
    played = [g["game_id"] for g in measured["games"]
              if g["season"] == measured["year"] and g["home_score"] is not None]
    mus = _with(monkeypatch, measured, team_week=lambda m: _team_rows(m, skip=played[0]))
    sources = {mu["teams"][s]["units"]["source"] for mu in mus.values() for s in ("home", "away")
               if mu["teams"][s]["games"]}
    assert sources == {"team_rows", "player_rows"}        # only the two teams of that game
    partial = [mu["teams"][s]["units"] for mu in mus.values() for s in ("home", "away")
               if mu["teams"][s]["units"]["source"] == "player_rows"]
    assert all("a partial season is not published" in u["epa_reason"] for u in partial)


def test_the_forecast_and_its_lead_time_reach_the_weather_block(
        measured, monkeypatch, results_on_this_walk):  # noqa: F811
    gid = next(g["game_id"] for g in measured["games"]
               if g["season"] == measured["year"] and g["home_score"] is None)
    f = {gid: {"wind_speed_mph": 14.26, "temperature_f": 48.04, "lead_hours": 61.5,
               "fetched_ts": NOW - 3600, "kind": "forecast"}}
    mus = _with(monkeypatch, measured, forecast=f)
    hit = next(m for m in mus.values() if m["game_id"] == gid)["situation"]["weather"]
    assert (hit["forecast_wind_mph"], hit["forecast_temp_f"]) == (14.3, 48.0)
    assert hit["forecast_lead_hours"] == 61.5 and hit["forecast_reason"] is None
    assert hit["forecast_taken"].endswith("Z")
    assert hit["recorded_wind_mph"] is None              # the game has not been played
    other = next(m for m in mus.values() if m["game_id"] != gid)["situation"]["weather"]
    assert other["forecast_wind_mph"] is None and other["forecast_lead_hours"] is None
    assert "no forecast taken before this kickoff" in other["forecast_reason"]
    # the total is NOT moved by the forecast: it still states the wind it assumed
    assert hit["total_assumes_wind_mph"] == other["total_assumes_wind_mph"]


# ------------------------------------------------- the forecast horizon, the refresh

def test_a_kickoff_past_the_forecast_horizon_is_counted_not_asked_for(feeds, monkeypatch):
    from datetime import datetime, timezone

    from feeds import fetch
    from tests.test_nfl_weather import FakeMeteo, games_frame
    monkeypatch.setattr(fetch, "GAP_S", 0)
    now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc).timestamp()
    games = games_frame([
        ("2026_03_A_GB", 2026, "2026-09-24", "20:15", "GNB00", "Lambeau Field", "outdoors"),
        ("2026_09_B_GB", 2026, "2026-11-05", "20:15", "GNB00", "Lambeau Field", "outdoors"),
    ])
    fake = FakeMeteo()
    out = J.run_nfl_weather(feeds, games, season=2026, client=fake.client(feeds), now=now,
                            verbose=False)
    assert out["beyond_forecast_horizon"] == 1 and out["rows_written"] == 1
    # 20:15 ET on the 24th is the 25th in UTC; November was never asked for
    assert len(fake.calls) == 1 and "start_date=2026-09-25" in fake.calls[0]


def test_the_weekly_refresh_takes_the_forecast_before_it_builds_the_matchups():
    import ast
    src = open(os.path.join(HERE, "jobs", "weekly_refresh.py"), encoding="utf-8").read()
    steps = [n.args[0].value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "step"
             and n.args and isinstance(n.args[0], ast.Constant)]
    assert steps.index("weather") < steps.index("game")
    call = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "step" and n.args[0].value == "weather")
    assert [k.value.value for k in call.keywords if k.arg == "fatal"] == [False]
