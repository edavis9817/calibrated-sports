"""The staleness gate (f-24), driven to BOTH answers on every check.

A gate that has only ever been seen red proves it can print; one only ever
seen green proves nothing. So `world()` builds a store and a code tree the gate
passes CLEAN, and each test breaks exactly one thing and asserts that one item
turns - and, where it matters, that the statement carries the last non-zero
date and not just the zero.

Everything lives under tmp_path. No test opens a configured store.
"""
import hashlib
import json
import os
import sqlite3
import time
import textwrap

import pytest

from analytics import staleness as st

DAY = 86400
NOW = 1791255600.0            # 2026-10-06 03:00 UTC, a Monday night in week 4
SEASON = 2026


def kick(week):
    """Final kickoff of `week`: week 4's is three hours before NOW."""
    return NOW - 3 * 3600 - (4 - week) * 7 * DAY


def db(path, script, rows=()):
    c = sqlite3.connect(path)
    c.executescript(script)
    for sql, data in rows:
        c.executemany(sql, data)
    c.commit()
    c.close()


def world(tmp_path, pace_weeks=(1, 2, 3), spine_pull="2026-10-05", metric_ts=NOW - DAY,
          nfl_weather_kick=None, targets_2026=5, depth_shard_day="2026-10-06",
          poly_rows=4, extra_code=None, reader=True, parser=True, props_mapped=4):
    store = tmp_path / "store"
    root = tmp_path / "repo"
    store.mkdir(parents=True)
    (root / "jobs").mkdir(parents=True)
    (root / "analytics").mkdir()
    games = [("nfl", "g%d_%d" % (s, w), "v1", s, w, kick(w) - (SEASON - s) * 364 * DAY, 20 if (s, w) <= (SEASON, 4) else None,
              # a-73: the game's local day and teams, which is how a ticker finds its kickoff
              time.strftime("%Y-%m-%d", time.gmtime(kick(w) - (SEASON - s) * 364 * DAY - 4 * 3600)), "JAX", "KC")
             for s in (2025, 2026) for w in range(1, 7)]
    # the week being priced (4, kicked off three hours ago) and the week just played (3)
    props = [("%s-%sKCJAX-P%d" % (s, d, i), w, i)
             for s in ("KXNFLREC", "KXNFLRSHATT") for d, w in (("26OCT05", 4), ("26SEP28", 3))
             for i in range(4)]
    pw = [("nfl", "p1", s, w, 5 if s == 2025 else targets_2026) for s in (2025, 2026) for w in range(1, 5)]
    db(str(store / "market_log.db"), """
        CREATE TABLE nfl_games (sport, game_id, data_version, season, week, kickoff_ts, home_score,
                                gameday, home_team, away_team);
        CREATE TABLE nfl_player_week (sport, gsis_id, season, week, targets);
        CREATE TABLE nflverse_versions (dataset, season, data_version);
        CREATE TABLE source_health (source, ok, detail, updated_ts);
        CREATE TABLE raw_shards (venue, day);
        CREATE TABLE poll_log (ts, venue, endpoint, n_markets, n_quotes);
        CREATE TABLE market_depth (ts, venue, market_id);
        CREATE TABLE markets (venue, market_id, market_type, first_seen);
        CREATE TABLE market_outcome (venue, market_id, outcome_id, unmapped_reason);
        CREATE TABLE outcomes (outcome_id, created_ts);
        """, [
        # a-68: the week's priority props, four rungs per series, listed two days
        # before their kickoff; a-73: each outcome created a day before it
        ("INSERT INTO markets VALUES (?,?,?,?)",
         [("kalshi", m, "prop", kick(w) - 2 * DAY) for m, w, _ in props]),
        ("INSERT INTO market_outcome VALUES (?,?,?,?)",
         [("kalshi", m, "o-" + m, None) for m, w, i in props if w == 3 or i < props_mapped]),
        ("INSERT INTO outcomes VALUES (?,?)",
         [("o-" + m, kick(w) - DAY) for m, w, i in props if w == 3 or i < props_mapped]),
        ("INSERT INTO nfl_games VALUES (?,?,?,?,?,?,?,?,?,?)", games),
        ("INSERT INTO nfl_player_week VALUES (?,?,?,?,?)", pw),
        ("INSERT INTO nflverse_versions VALUES (?,?,?)",
         [("pbp", 2026, "2026-10-05"), ("pbp", 2025, "2026-09-09"), ("depth_charts", 2026, "2026-10-05")]),
        ("INSERT INTO source_health VALUES (?,?,?,?)",
         [("depth_capture", 1, "kalshi_rows=9, poly_rows=%d, raw_books_kept=3" % poly_rows, NOW)]),
        ("INSERT INTO raw_shards VALUES (?,?)",
         [("kalshi", "2026-10-06"), ("kalshi_depth", depth_shard_day), ("polymarket_depth", depth_shard_day)]),
        ("INSERT INTO poll_log VALUES (?,?,?,?,?)", [(NOW - 60, "kalshi", "quotes:live", 5, 50)]),
        ("INSERT INTO market_depth VALUES (?,?,?)",
         [(NOW - 100, "kalshi", "m"), (NOW - 100 if poly_rows else NOW - 20 * DAY, "polymarket", "m")]),
    ])
    wx = [("cfb", "c1", NOW)] + ([("nfl", "n1", nfl_weather_kick)] if nfl_weather_kick else [])
    db(str(store / "feeds.db"), """
        CREATE TABLE weather_at_kickoff (sport, game_id, kickoff_ts);
        CREATE TABLE feeds_raw_files (feed, fetched_ts);
        CREATE TABLE injury_reports (sport, season, week, player_id);
        """, [("INSERT INTO injury_reports VALUES (?,?,?,?)",
               [(sp, s, w, "p") for sp in ("nfl", "cfb") for s in (2025, 2026) for w in range(1, 5)]),
              ("INSERT INTO weather_at_kickoff VALUES (?,?,?)", wx),
              ("INSERT INTO feeds_raw_files VALUES (?,?)", [("weather", NOW - 3600)])])
    db(str(store / "analytics.db"), """
        CREATE TABLE f_team_game_pace (season, week, team, plays);
        CREATE TABLE f_spine_build (season, pull_date);
        CREATE TABLE f_ngs_build (family, pull_date);
        CREATE TABLE f_onfield_build (season, pull_date);
        CREATE TABLE f_pbp_files (dataset, season, pull_date);
        CREATE TABLE f_metrics (metric, availability, computed_ts);
        """, [("INSERT INTO f_team_game_pace VALUES (?,?,?,?)",
               [(s, w, "KC", 60) for s, ws in ((2025, (1, 2, 3, 4)), (2026, pace_weeks)) for w in ws]),
              ("INSERT INTO f_spine_build VALUES (?,?)", [(2026, spine_pull), (2025, "2026-09-09")]),
              ("INSERT INTO f_ngs_build VALUES (?,?)", [("receiving", "2026-10-05")]),
              ("INSERT INTO f_onfield_build VALUES (?,?)", [(2025, "2026-09-09")]),
              ("INSERT INTO f_pbp_files VALUES (?,?,?)", [("pbp", 2026, "2026-10-05"), ("pbp", 2025, "2026-09-09")]),
              ("INSERT INTO f_metrics VALUES (?,?,?)", [("pace", "current", metric_ts)])])
    code = {
        "nflverse.py": '''
            DATASETS = {
                "pbp": Dataset("pbp", "pbp", "play_by_play_{season}.parquet", LIVE, normalize=True),
                "depth_charts": Dataset("depth_charts", "depth_charts", "depth_charts_{season}.parquet", LIVE),
            }''',
        "jobs/__init__.py": "",
        "analytics/__init__.py": "",
        "jobs/weekly_refresh.py": '''
            """Docstring that says: rebuilt from f_orphan every week."""
            import jobs.export_web
            STEPS = ["analytics.pace"]''',
        "jobs/export_web.py": ("""
            Q = ["SELECT * FROM nfl_games JOIN nfl_player_week", "SELECT 1 FROM nflverse_versions",
                 "SELECT 1 FROM source_health", "SELECT 1 FROM raw_shards", "SELECT 1 FROM poll_log",
                 "SELECT 1 FROM market_depth", "SELECT 1 FROM weather_at_kickoff",
                 "SELECT 1 FROM markets JOIN market_outcome JOIN outcomes",
                 "SELECT 1 FROM feeds_raw_files", "SELECT 1 FROM injury_reports", "SELECT 1 FROM f_spine_build", "SELECT 1 FROM f_ngs_build",
                 "SELECT 1 FROM f_onfield_build", "SELECT 1 FROM f_pbp_files", "SELECT 1 FROM f_metrics"]
            """ + ('PARSED = "depth_charts_{season}.parquet"' if parser else "")),
        "analytics/pace.py": 'Q = "SELECT plays FROM f_team_game_pace"' if reader else "X = 1",
        "analytics/spine.py": 'W = "INSERT INTO f_team_game_pace VALUES (1)"',
    }
    code.update(extra_code or {})
    for rel, text in code.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(store), str(root)


def releases(*tags):
    return lambda: [{"tag_name": t, "assets": [{"updated_at": "2026-09-30T00:00:00Z"}]} for t in tags]


def gate(tmp_path, rulings=(), fetch=None, **kw):
    run_kw = {k: kw.pop(k) for k in ("offline", "strict", "quick") if k in kw}
    store, root = world(tmp_path, **kw)
    return st.run(store, root, now=NOW, rulings=list(rulings),
                  fetch=fetch or releases("pbp", "depth_charts"), **run_kw)


def item(report, key, check=None):
    hit = [i for i in report.items if i.key == key and check in (None, i.check)]
    assert len(hit) == 1, "expected one item for %s, got %s" % (key, [i.statement for i in hit])
    return hit[0]


# ---- the gate can be green, and says what it looked at -------------------

def test_a_healthy_world_is_clean_and_not_vacuously(tmp_path):
    r = gate(tmp_path, nfl_weather_kick=NOW)
    assert r.clean, [i.statement for i in r.failing]
    assert r.count(st.RED) == r.count(st.ERROR) == r.count(st.UNRULED) == r.count(st.SKIPPED) == 0
    assert r.count(st.OK) >= 25                       # it checked things; an empty run is not a pass
    assert item(r, "analytics.db:f_team_game_pace", "freshness").status == st.OK
    assert "2026 week 3" in r.clock.statement         # week 4 kicked 3h ago: inside the grace


def test_report_refuses_truth_testing(tmp_path):
    r = gate(tmp_path, nfl_weather_kick=NOW)
    with pytest.raises(TypeError, match="clean"):
        bool(r)


def test_clock_counts_a_week_only_after_the_grace(tmp_path):
    store, _ = world(tmp_path)
    ml = os.path.join(store, "market_log.db")
    assert st.week_clock(ml, NOW).pos == (2026, 3)
    assert st.week_clock(ml, NOW).in_progress == 4
    assert st.week_clock(ml, NOW + 10 * 3600).pos == (2026, 4)      # 13h after kickoff


def test_no_schedule_is_an_error_not_a_pass(tmp_path):
    store, root = world(tmp_path)
    c = sqlite3.connect(os.path.join(store, "market_log.db"))
    c.execute("DELETE FROM nfl_games WHERE season=2026")
    c.commit()
    c.close()
    r = st.run(store, root, now=NOW, rulings=[], fetch=releases("pbp", "depth_charts"))
    assert item(r, "clock").status == st.ERROR
    assert not r.clean
    assert not [i for i in r.items if i.key == "analytics.db:f_team_game_pace"]   # and it did not guess


# ---- 1. freshness ---------------------------------------------------------

def test_a_table_stuck_at_week_one_is_red_with_the_distance(tmp_path):
    r = gate(tmp_path, pace_weeks=(1,), nfl_weather_kick=NOW)
    it = item(r, "analytics.db:f_team_game_pace", "freshness")
    assert it.status == st.RED and "2 weeks behind" in it.statement
    assert it.detail["weeks_behind"] == 2
    assert not r.clean


def test_a_hole_behind_the_newest_week_is_red(tmp_path):
    r = gate(tmp_path, pace_weeks=(1, 3), nfl_weather_kick=NOW)
    it = item(r, "analytics.db:f_team_game_pace", "freshness")
    assert it.status == st.RED and "[2]" in it.statement


def test_a_schedule_row_is_not_a_result(tmp_path):
    """nfl_games holds weeks 5 and 6 unplayed; its freshness is the newest SCORE."""
    r = gate(tmp_path, nfl_weather_kick=NOW)
    assert item(r, "market_log.db:nfl_games").detail["newest"] == (2026, 4)


def test_a_build_older_than_its_source_is_red(tmp_path):
    r = gate(tmp_path, spine_pull="2026-09-17", nfl_weather_kick=NOW)
    it = item(r, "analytics.db:f_spine_build")
    assert it.status == st.RED and "2026-09-17" in it.statement and "2026-10-05" in it.statement
    assert item(r, "analytics.db:f_pbp_files").status == st.OK      # only the stale one turned


def test_a_current_metric_computed_before_the_week_ended_is_red(tmp_path):
    r = gate(tmp_path, metric_ts=kick(3) - DAY, nfl_weather_kick=NOW)
    assert item(r, "analytics.db:f_metrics").status == st.RED
    assert item(gate(tmp_path / "b", nfl_weather_kick=NOW), "analytics.db:f_metrics").status == st.OK


def test_history_alone_does_not_make_a_game_table_current(tmp_path):
    """2,171 archive rows ending in February satisfy 'has NFL rows' and nothing else."""
    r = gate(tmp_path, nfl_weather_kick=NOW - 240 * DAY)
    it = item(r, "feeds.db:weather_at_kickoff", "freshness")
    assert it.status == st.RED and "2026 week 3" in it.statement


def test_a_failing_health_row_is_red(tmp_path):
    store, root = world(tmp_path, nfl_weather_kick=NOW)
    c = sqlite3.connect(os.path.join(store, "market_log.db"))
    c.execute("INSERT INTO source_health VALUES ('healthcheck', 0, 'client closed', ?)", (NOW,))
    c.commit()
    c.close()
    r = st.run(store, root, now=NOW, rulings=[], fetch=releases("pbp", "depth_charts"))
    assert item(r, "source_health:healthcheck").status == st.RED


# ---- 2. emptiness ---------------------------------------------------------

def test_a_sport_with_no_rows_in_a_table_another_sport_fills(tmp_path):
    r = gate(tmp_path)                                # no NFL weather row at all
    assert item(r, "feeds.db:weather_at_kickoff:sport=nfl").status == st.RED
    assert item(r, "feeds.db:weather_at_kickoff", "freshness").status == st.RED
    assert not r.clean


def test_an_unwatched_missing_sport_is_a_question(tmp_path):
    store, root = world(tmp_path, nfl_weather_kick=NOW)
    c = sqlite3.connect(os.path.join(store, "feeds.db"))
    c.execute("DELETE FROM injury_reports WHERE sport='cfb'")
    c.commit()
    c.close()
    r = st.run(store, root, now=NOW, rulings=[], fetch=releases("pbp", "depth_charts"))
    assert item(r, "feeds.db:injury_reports:sport=cfb").status == st.UNRULED and r.clean


def test_a_column_that_stopped_filling_names_both_counts(tmp_path):
    r = gate(tmp_path, targets_2026=0, nfl_weather_kick=NOW)
    it = item(r, "market_log.db:nfl_player_week.targets")
    assert it.status == st.RED
    assert it.detail == {"prior": 3, "now": 0, "last_informative": "2025 week 4"}


def test_an_empty_table_is_red(tmp_path):
    store, root = world(tmp_path, nfl_weather_kick=NOW)
    c = sqlite3.connect(os.path.join(store, "feeds.db"))
    c.execute("CREATE TABLE feeds_runs (run_id)")
    c.commit()
    c.close()
    r = st.run(store, root, now=NOW, rulings=[], fetch=releases("pbp", "depth_charts"))
    assert item(r, "feeds.db:feeds_runs").status == st.RED


def test_a_stopped_watched_series_carries_its_last_date(tmp_path):
    r = gate(tmp_path, depth_shard_day="2026-09-15", nfl_weather_kick=NOW)
    it = item(r, "raw_shards:kalshi_depth")
    assert it.status == st.RED
    assert it.detail["last_nonzero"] == "2026-09-15" and it.detail["days"] == 21


def test_a_stopped_unwatched_series_is_a_question_not_a_failure(tmp_path):
    store, root = world(tmp_path, nfl_weather_kick=NOW)
    c = sqlite3.connect(os.path.join(store, "market_log.db"))
    c.execute("INSERT INTO raw_shards VALUES ('kalshi_history', '2026-09-10')")
    c.commit()
    c.close()
    kw = dict(now=NOW, rulings=[], fetch=releases("pbp", "depth_charts"))
    r = st.run(store, root, **kw)
    assert item(r, "raw_shards:kalshi_history").status == st.UNRULED
    assert r.clean                                    # unruled does not fail...
    assert not st.run(store, root, strict=True, **kw).clean       # ...except under --strict


def test_a_zero_counter_reports_when_it_was_last_non_zero(tmp_path):
    r = gate(tmp_path, poly_rows=0, nfl_weather_kick=NOW)
    it = item(r, "source_health:depth_capture.poly_rows")
    assert it.status == st.RED and it.detail["last_nonzero"] == st.day(NOW - 20 * DAY)
    assert item(r, "market_depth:polymarket").status == st.RED
    assert item(r, "market_depth:kalshi").status == st.OK


def test_quick_skips_the_depth_scan_out_loud(tmp_path):
    r = gate(tmp_path, quick=True, nfl_weather_kick=NOW)
    assert item(r, "market_depth:by_venue").status == st.SKIPPED
    assert r.clean                                    # skipped neither passes nor fails


# ---- 3. collected and unread ----------------------------------------------

def test_a_table_written_and_never_read_is_red(tmp_path):
    r = gate(tmp_path, reader=False, nfl_weather_kick=NOW)
    it = item(r, "analytics.db:f_team_game_pace", "freshness")     # freshness is fine
    assert it.status == st.OK
    un = [i for i in r.items if i.check == "unread" and "f_team_game_pace" in i.key]
    assert [i.status for i in un] == [st.RED] and "analytics.spine" in un[0].statement


def test_a_reader_off_every_publish_path_is_unruled(tmp_path):
    r = gate(tmp_path, reader=False, nfl_weather_kick=NOW,
             extra_code={"research/probe.py": 'Q = "select * from f_team_game_pace"'})
    un = [i for i in r.items if i.check == "unread" and "f_team_game_pace" in i.key]
    assert [i.status for i in un] == [st.UNRULED] and "research.probe" in un[0].statement


def test_a_docstring_is_not_a_read(tmp_path):
    """weekly_refresh's docstring says 'rebuilt from f_orphan'; that reads nothing."""
    store, root = world(tmp_path, nfl_weather_kick=NOW)
    mods, unparsed = st.scan_code(root)
    assert not unparsed
    assert st.references(mods, "f_orphan") == {}
    assert "read" in st.references(mods, "nfl_games")["jobs.export_web"]


def test_delete_from_is_a_write_not_a_read(tmp_path):
    mods = {"m": {"strings": ["DELETE FROM quotes WHERE ts < ?"], "edges": set()}}
    assert st.references(mods, "quotes") == {"m": {"write", "mention"}}


def test_an_archived_dataset_nobody_parses_is_red(tmp_path):
    assert item(gate(tmp_path, parser=False, nfl_weather_kick=NOW), "nflverse:depth_charts").status == st.RED
    assert item(gate(tmp_path / "b", nfl_weather_kick=NOW), "nflverse:depth_charts").status == st.OK


def test_an_upstream_release_the_registry_never_fetches_is_red(tmp_path):
    r = gate(tmp_path, nfl_weather_kick=NOW, fetch=releases("pbp", "depth_charts", "stats_team"))
    assert item(r, "upstream:stats_team").status == st.RED
    assert not r.clean


def test_a_release_fetched_by_a_second_registry_is_not_reported(tmp_path):
    """feeds.sources fetches `injuries` without nflverse.DATASETS ever naming it."""
    code = {"feeds/sources.py": 'R = Release("injuries", REPO, "injuries", "injuries_{season}.parquet")'}
    r = gate(tmp_path, nfl_weather_kick=NOW, extra_code=code,
             fetch=releases("pbp", "depth_charts", "injuries", "stats_team"))
    assert not [i for i in r.items if i.key == "upstream:injuries"]
    assert item(r, "upstream:stats_team").status == st.RED      # and the real gap still fires
    assert "injuries by feeds.sources" in item(r, "upstream:nflverse-data").statement


def test_a_failed_listing_is_an_error_and_offline_is_a_skip(tmp_path):
    def boom():
        raise OSError("rate limited")
    r = gate(tmp_path, nfl_weather_kick=NOW, fetch=boom)
    assert item(r, "upstream:nflverse-data").status == st.ERROR and not r.clean
    r = gate(tmp_path / "b", nfl_weather_kick=NOW, fetch=boom, offline=True)
    assert item(r, "upstream:nflverse-data").status == st.SKIPPED and r.clean


def test_the_real_registry_parses(tmp_path):
    """The AST reader against the committed nflverse.py, not only the fixture's."""
    reg = st.registry(os.path.dirname(os.path.dirname(os.path.abspath(st.__file__))))
    assert reg["weekly_stats"] == ("stats_player", "stats_player_week", True)
    assert reg["depth_charts"] == ("depth_charts", "depth_charts", False)
    assert len(reg) >= 15


# ---- rulings ---------------------------------------------------------------

RULING = {"check": "freshness", "key": "analytics.db:f_team_game_pace", "ruled_by": "Ethan",
          "ruled_on": "2026-10-06", "reason": "test"}


def test_a_ruling_is_the_only_way_red_stops_failing(tmp_path):
    r = gate(tmp_path, pace_weeks=(1,), nfl_weather_kick=NOW, rulings=[RULING])
    it = item(r, "analytics.db:f_team_game_pace", "freshness")
    assert it.status == st.RULED and "RULED by Ethan" in it.statement
    assert r.clean


def test_a_tolerance_covers_only_what_was_ruled(tmp_path):
    one = dict(RULING, tolerance_weeks=1)
    assert item(gate(tmp_path, pace_weeks=(1,), nfl_weather_kick=NOW, rulings=[one]),
                "analytics.db:f_team_game_pace", "freshness").status == st.RED            # 2 behind > 1
    assert item(gate(tmp_path / "b", pace_weeks=(1, 2), nfl_weather_kick=NOW, rulings=[one]),
                "analytics.db:f_team_game_pace", "freshness").status == st.RULED          # 1 behind


def test_a_ruling_must_name_who_when_and_why(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"rulings": [dict(RULING, ruled_by="")]}))
    with pytest.raises(ValueError, match="ruled_by"):
        st.load_rulings(str(p))


def test_the_committed_rulings_file_loads():
    for r in st.load_rulings():
        assert all(r[f] for f in st.RULING_FIELDS)


# ---- mechanics --------------------------------------------------------------

def test_an_aborted_query_raises_instead_of_returning_nothing(tmp_path):
    store, _ = world(tmp_path)
    slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i < 50000000) SELECT COUNT(*) FROM n"
    with pytest.raises(sqlite3.OperationalError):
        st.query(os.path.join(store, "market_log.db"), slow, budget=0)


def test_the_gate_writes_nothing_to_any_store(tmp_path):
    store, root = world(tmp_path, pace_weeks=(1,))
    before = {f: hashlib.sha256(open(os.path.join(store, f), "rb").read()).hexdigest() for f in os.listdir(store)}
    st.run(store, root, now=NOW, rulings=[], fetch=releases("pbp"))
    after = {f: hashlib.sha256(open(os.path.join(store, f), "rb").read()).hexdigest() for f in os.listdir(store)}
    assert before == after and len(before) == 3
    with pytest.raises(sqlite3.OperationalError):
        c = sqlite3.connect(st._uri(os.path.join(store, "feeds.db")), uri=True)
        c.execute("DELETE FROM weather_at_kickoff")


def test_exit_code_is_the_verdict(tmp_path, monkeypatch):
    store, root = world(tmp_path, nfl_weather_kick=NOW)
    monkeypatch.setattr(st.time, "time", lambda: NOW)
    monkeypatch.setattr(st, "fetch_releases", releases("pbp", "depth_charts"))
    out = tmp_path / "r.md"
    args = ["--store-dir", store, "--root", root, "--offline", "--report", str(out)]
    assert st.main(args) == 0
    assert "CLEAN" in out.read_text(encoding="utf-8")
    c = sqlite3.connect(os.path.join(store, "analytics.db"))
    c.execute("DELETE FROM f_team_game_pace WHERE season=2026 AND week>1")
    c.commit()
    c.close()
    assert st.main(args) == 1
    assert "2 weeks behind" in out.read_text(encoding="utf-8")
