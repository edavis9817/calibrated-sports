"""`analytics.refresh` (f-25): the weekly rebuild and the wired staleness gate.

Each rule is driven to BOTH answers. The rebuild's publishers are replaced by
fakes that write the same two tables the real ones do, so what is under test is
the orchestration - what runs, what stops, what is refused - and never a
metric's arithmetic. Everything lives under tmp_path; no configured store is
opened and `store.record_health` is captured, never called.
"""
import json
import os
import sqlite3
import subprocess
import types

import pytest

from analytics import paths, refresh
from analytics import staleness as st
from tests.test_staleness import NOW, releases, world

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = 1000


def make_db(tmp_path, metrics):
    """A store holding `metrics` as {key: availability}, all computed at OLD."""
    p = str(tmp_path / "analytics.db")
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE f_metrics (metric PRIMARY KEY, availability, computed_ts);
        CREATE TABLE f_metric_values (metric, subject_id, est);
        CREATE TABLE f_play_usage (season, week);
        CREATE TABLE f_team_game_pace (season, week);
        CREATE TABLE f_ngs_week (season, week);
        INSERT INTO f_play_usage VALUES (2026, 1);
        INSERT INTO f_team_game_pace VALUES (2026, 1);
        INSERT INTO f_ngs_week VALUES (2026, 1);
    """)
    c.executemany("INSERT INTO f_metrics VALUES (?,?,?)", [(k, a, OLD) for k, a in metrics.items()])
    c.executemany("INSERT INTO f_metric_values VALUES (?,?,?)", [(k, "s", 1.0) for k in metrics])
    c.commit()
    c.close()
    return p


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Stand-ins for every module `refresh` calls. `fakes.publishes[module]` is
    the keys that module's `--publish` writes; `fakes.fail` names modules that
    exit non-zero; `fakes.calls` records (module, argv) in order."""
    ns = types.SimpleNamespace(calls=[], fail=set(), publishes={}, db=None)

    def module(name):
        def main(argv):
            ns.calls.append((name, list(argv)))
            if name in ns.fail:
                raise SystemExit("%s broke" % name)
            c = sqlite3.connect(ns.db)
            if name == "analytics.spine":
                c.execute("INSERT INTO f_play_usage VALUES (2026, 4)")
                c.execute("INSERT INTO f_team_game_pace VALUES (2026, 4)")
            if name == "analytics.ngs" and "--build" in argv:
                c.execute("INSERT INTO f_ngs_week VALUES (2026, 4)")
            if "--publish" in argv:
                for key in ns.publishes.get(name, ()):
                    c.execute("INSERT OR REPLACE INTO f_metrics VALUES (?,?,?)",
                              (key, "current", 9_999_999_999))
                    c.execute("INSERT INTO f_metric_values VALUES (?,?,?)", (key, "s", 2.0))
            c.commit()
            c.close()
            return 0
        return types.SimpleNamespace(main=main)

    monkeypatch.setattr(refresh.importlib, "import_module", module)
    monkeypatch.setattr(refresh, "HELD", {})                 # the hold has its own tests
    monkeypatch.setattr(paths, "db_path", lambda: ns.db)
    return ns


def keys(db):
    c = sqlite3.connect(db)
    out = dict(c.execute("SELECT metric, computed_ts FROM f_metrics"))
    c.close()
    return out


# ---- what runs ------------------------------------------------------------

def test_the_facts_are_rebuilt_before_any_publisher_and_scoped_to_the_season(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current"})
    fakes.publishes = {"analytics.pace": ["pace.plays_per_game"]}
    out = refresh.rebuild(season=2026, log=lambda m: None)
    assert fakes.calls == [
        ("analytics.spine", ["--build", "--season", "2026"]),
        ("analytics.survey", ["--scan", "--season", "2026"]),
        ("analytics.ngs", ["--build"]),
        ("analytics.pace", ["--publish"]),
    ]
    assert out["before"]["f_team_game_pace"] == (1, 1)
    assert out["after"]["f_team_game_pace"] == (2, 4)       # the rebuild reached week 4
    assert keys(fakes.db)["pace.plays_per_game"] > OLD


def test_all_seasons_drops_the_season_scope(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {})
    refresh.rebuild(season=2026, all_seasons=True, log=lambda m: None)
    assert fakes.calls[:2] == [("analytics.spine", ["--build"]), ("analytics.survey", ["--scan"])]


def test_only_families_already_in_the_store_are_published(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current", "role.touch_share": "current"})
    fakes.publishes = {"analytics.pace": ["pace.plays_per_game"],
                       "analytics.role": ["role.touch_share"]}
    out = refresh.rebuild(season=2026, log=lambda m: None)
    ran = [m for m, a in fakes.calls if "--publish" in a]
    assert ran == ["analytics.role", "analytics.pace"]      # PUBLISHERS order, nothing else
    assert "vacancy" in out["skipped_families"] and "deltas" in out["skipped_families"]
    assert "pace" not in out["skipped_families"]


def test_the_asof_keys_are_published_only_where_the_store_already_holds_them(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"opportunity_residual.rushing_yards": "current"})
    con = sqlite3.connect(fakes.db)
    assert refresh.plan(con, 2026)[0] == [
        ("opportunity_residual", "analytics.residual", ["--publish", "--no-asof"])]
    con.execute("INSERT INTO f_metrics VALUES ('opportunity_residual.asof.rushing_yards','current',1)")
    assert refresh.plan(con, 2026)[0] == [("opportunity_residual", "analytics.residual", ["--publish"])]
    con.close()


def test_the_schedule_publisher_is_handed_the_season(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"schedule.efficiency.rb": "current"})
    con = sqlite3.connect(fakes.db)
    assert refresh.plan(con, 2031)[0] == [
        ("schedule", "analytics.schedule", ["--publish", "--season", "2031"])]
    con.close()


# ---- what is held ---------------------------------------------------------

def test_a_held_family_is_skipped_out_loud_and_is_not_a_failure(tmp_path, fakes, monkeypatch, capsys):
    monkeypatch.setattr(refresh, "HELD", {"role": "awaits a publish run"})
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current", "role.touch_share": "current"})
    fakes.publishes = {"analytics.pace": ["pace.plays_per_game"],
                       "analytics.role": ["role.touch_share"]}
    assert refresh.main(["--rebuild", "--season", "2026"]) == 0
    assert "analytics.role" not in [m for m, _ in fakes.calls]
    got = keys(fakes.db)
    assert got["role.touch_share"] == OLD and got["pace.plays_per_game"] > OLD
    said = capsys.readouterr().out
    assert "HELD, not republished: role (awaits a publish run)" in said
    assert "1 of 2 current metrics recomputed; HELD role" in said


def test_include_held_releases_it_for_one_run(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(refresh, "HELD", {"role": "awaits a publish run"})
    fakes.db = make_db(tmp_path, {"role.touch_share": "current"})
    fakes.publishes = {"analytics.role": ["role.touch_share"]}
    out = refresh.rebuild(season=2026, include_held=True, log=lambda m: None)
    assert out["held"] == [] and keys(fakes.db)["role.touch_share"] > OLD


def test_a_hold_names_a_real_family_and_says_why():
    families = {f for f, _m, _a in refresh.PUBLISHERS}
    for family, why in refresh.HELD.items():
        assert family in families and len(why) > 20, family


# ---- what is refused ------------------------------------------------------

def test_a_publisher_may_not_add_a_key_the_store_did_not_hold(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"role.touch_share": "current"})
    fakes.publishes = {"analytics.role": ["role.touch_share", "role.touch_share.by_season"]}
    out = refresh.rebuild(season=2026, log=lambda m: None)
    assert out["dropped"] == ["role.touch_share.by_season"]
    assert set(keys(fakes.db)) == {"role.touch_share"}
    c = sqlite3.connect(fakes.db)
    assert c.execute("SELECT COUNT(*) FROM f_metric_values WHERE metric LIKE '%by_season'").fetchone()[0] == 0
    c.close()


def test_a_failed_build_stops_before_any_publisher_and_stamps_nothing(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current"})
    fakes.publishes = {"analytics.pace": ["pace.plays_per_game"]}
    fakes.fail = {"analytics.spine"}
    with pytest.raises(refresh.StepFailed, match="analytics.spine"):
        refresh.rebuild(season=2026, log=lambda m: None)
    assert [m for m, _ in fakes.calls] == ["analytics.spine"]
    assert keys(fakes.db)["pace.plays_per_game"] == OLD      # NOT stamped fresh on old facts


def test_a_key_no_publisher_claims_fails_the_run(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current", "mystery.metric": "current"})
    fakes.publishes = {"analytics.pace": ["pace.plays_per_game"]}
    with pytest.raises(refresh.StepFailed, match="mystery.metric"):
        refresh.rebuild(season=2026, log=lambda m: None)
    assert not [m for m, a in fakes.calls if "--publish" in a]


def test_a_current_metric_its_publisher_did_not_recompute_fails_the_run(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current", "pace.retired": "current",
                                  "role.onfield_share": "historical"})
    fakes.publishes = {"analytics.pace": ["pace.plays_per_game"]}
    with pytest.raises(refresh.StepFailed, match="pace.retired"):
        refresh.rebuild(season=2026, log=lambda m: None)


def test_a_historical_metric_left_alone_is_not_a_failure(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"role.touch_share": "current", "role.onfield_share": "historical"})
    fakes.publishes = {"analytics.role": ["role.touch_share"]}
    out = refresh.rebuild(season=2026, log=lambda m: None)
    assert out["not_recomputed"] == [] and out["current_metrics"] == 1


def test_no_publish_rebuilds_facts_and_touches_no_metric(tmp_path, fakes):
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current"})
    refresh.rebuild(season=2026, publish=False, log=lambda m: None)
    assert not [m for m, a in fakes.calls if "--publish" in a]
    assert keys(fakes.db)["pace.plays_per_game"] == OLD


def test_every_publisher_module_exists_and_takes_its_arguments():
    """The table names real modules: a renamed publisher would otherwise fail
    only on the night its family is due."""
    import importlib
    for family, module, argv in refresh.PUBLISHERS:
        mod = importlib.import_module(module)
        assert callable(mod.main), module
        src = open(mod.__file__, encoding="utf-8").read()
        for flag in (a for a in argv if a.startswith("--")):
            assert '"%s"' % flag in src, (module, flag)
    assert len({f for f, _m, _a in refresh.PUBLISHERS}) == len(refresh.PUBLISHERS)


# ---- the command ----------------------------------------------------------

@pytest.fixture
def health(monkeypatch):
    import store
    rows = []
    monkeypatch.setattr(store, "record_health",
                        lambda source, ok, detail=None, watermark=None:
                        rows.append((source, bool(ok), detail, watermark)))
    return rows


def test_a_failed_rebuild_exits_1_and_records_it(tmp_path, fakes, health, capsys):
    fakes.db = make_db(tmp_path, {})
    fakes.fail = {"analytics.ngs"}
    assert refresh.main(["--rebuild", "--season", "2026", "--record-health"]) == 1
    assert "REBUILD FAILED" in capsys.readouterr().out
    assert [(s, ok) for s, ok, _d, _w in health] == [(refresh.REBUILD_SOURCE, False)]
    assert health[0][3] is None                              # no watermark on a failure


def test_a_good_rebuild_exits_0_and_says_how_far_it_reaches(tmp_path, fakes, health, capsys):
    fakes.db = make_db(tmp_path, {"pace.plays_per_game": "current"})
    fakes.publishes = {"analytics.pace": ["pace.plays_per_game"]}
    assert refresh.main(["--rebuild", "--season", "2026", "--record-health"]) == 0
    assert "REBUILT 2026 rebuilt; usage wk 4" in capsys.readouterr().out
    assert health[0][:2] == (refresh.REBUILD_SOURCE, True) and "pace wk 4" in health[0][2]


def test_health_is_not_written_unless_asked(tmp_path, fakes, health):
    fakes.db = make_db(tmp_path, {})
    refresh.main(["--rebuild", "--season", "2026"])
    assert health == []


def gate_args(tmp_path, **kw):
    store_dir, root = world(tmp_path, nfl_weather_kick=NOW, **kw)
    return ["--gate", "--offline", "--store-dir", store_dir, "--root", root,
            "--report-dir", str(tmp_path / "out")]


def test_the_gate_step_is_non_fatal_until_asked_and_fatal_when_asked(tmp_path, monkeypatch, health):
    monkeypatch.setattr(st.time, "time", lambda: NOW)
    monkeypatch.setattr(refresh.time, "time", lambda: NOW)
    monkeypatch.setattr(st, "fetch_releases", releases("pbp", "depth_charts"))
    args = gate_args(tmp_path, pace_weeks=(1,))              # a stale store: the gate FAILS
    assert refresh.main(args + ["--record-health"]) == 0
    assert refresh.main(args + ["--fatal"]) == 1
    report = (tmp_path / "out" / "staleness_gate.md").read_text(encoding="utf-8")
    assert "2 weeks behind" in report
    items = json.loads((tmp_path / "out" / "staleness_gate.json").read_text(encoding="utf-8"))
    assert items["clean"] is False and len(items["items"]) > 20
    assert health == [(refresh.GATE_SOURCE, False, items["statement"][:200], None)]


def test_a_clean_gate_records_ok_and_fatal_still_exits_0(tmp_path, monkeypatch, health):
    monkeypatch.setattr(st.time, "time", lambda: NOW)
    monkeypatch.setattr(refresh.time, "time", lambda: NOW)
    args = gate_args(tmp_path)
    assert refresh.main(args + ["--fatal", "--record-health"]) == 0
    assert health[0][:2] == (refresh.GATE_SOURCE, True) and health[0][3] == NOW
    assert "CLEAN" in health[0][2]


def test_a_gate_that_cannot_run_exits_2_and_never_reads_as_a_verdict(tmp_path, monkeypatch, health):
    def boom(*a, **k):
        raise RuntimeError("no stores")
    monkeypatch.setattr(st, "run", boom)
    assert refresh.main(["--gate", "--report-dir", str(tmp_path), "--store-dir", str(tmp_path),
                         "--record-health"]) == 2
    assert health[0][:2] == (refresh.GATE_SOURCE, False) and "did not run" in health[0][2]


# ---- the gate does not count itself ---------------------------------------

def _health_row(store_dir, source, ok):
    c = sqlite3.connect(os.path.join(store_dir, "market_log.db"))
    c.execute("INSERT INTO source_health VALUES (?,?,?,?)", (source, ok, "detail", NOW))
    c.commit()
    c.close()


def test_the_gates_own_failing_row_is_not_a_finding_and_the_rebuilds_is(tmp_path):
    store_dir, root = world(tmp_path, nfl_weather_kick=NOW)
    _health_row(store_dir, refresh.GATE_SOURCE, 0)
    r = st.run(store_dir, root, now=NOW, rulings=[], fetch=releases("pbp", "depth_charts"))
    assert r.clean, [i.statement for i in r.failing]
    _health_row(store_dir, refresh.REBUILD_SOURCE, 0)
    r = st.run(store_dir, root, now=NOW, rulings=[], fetch=releases("pbp", "depth_charts"))
    assert [i.key for i in r.failing] == ["source_health:%s" % refresh.REBUILD_SOURCE]
    assert refresh.GATE_SOURCE == st.OWN_HEALTH_SOURCE


# ---- the weekly cadence ---------------------------------------------------

PATCH = os.path.join(ROOT, "docs", "findings", "f25-weekly-refresh.patch")
REBUILD_CMD = '"-m", "analytics.refresh", "--rebuild"'
GATE_CMD = '"-m", "analytics.refresh", "--gate"'


def test_the_weekly_refresh_steps_are_applied_or_the_filed_patch_still_applies():
    """`jobs/weekly_refresh.py` is track A's. Until track A takes the two steps,
    the exact diff is filed at PATCH and must keep applying to the file as it
    is; once taken, the steps must be there in the right order. Either answer
    passes; a patch that has rotted against the file it targets does not."""
    src = open(os.path.join(ROOT, "jobs", "weekly_refresh.py"), encoding="utf-8").read()
    if REBUILD_CMD in src or GATE_CMD in src:
        assert src.index(REBUILD_CMD) < src.index('"-m", "jobs.export_web"]'), \
            "the rebuild must run before the first export that reads it"
        assert src.index(GATE_CMD) > src.index('"--upload-only"'), "the gate runs last"
        return
    # Fed on stdin with LF endings: under `core.autocrlf` the checked-out patch
    # is CRLF, and a patch is the one text file whose line endings are payload.
    body = open(PATCH, "rb").read().replace(b"\r\n", b"\n")
    assert body.count(b"analytics.refresh") >= 4, "the patch is not the patch"
    r = subprocess.run(["git", "apply", "--check", "-"], cwd=ROOT, input=body, capture_output=True)
    assert r.returncode == 0, "the filed patch no longer applies: %s" % r.stderr.decode()
