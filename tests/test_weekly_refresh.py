"""Contract v2: the weekly refresh runs ingest -> map -> export -> upload ->
validate, never runs npm, uses git ONLY to commit the slug registry
(web/slugs pathspec), and treats late nflverse and an unreachable site as
warnings."""
import json
import os
from types import SimpleNamespace

import pytest

import config
from jobs import weekly_refresh as W


class Runner:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, cmd, cwd=None, capture_output=True, text=True):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=1 if self.name(cmd) in self.fail else 0,
                               stdout="ok", stderr="")

    @staticmethod
    def name(cmd):
        s = " ".join(cmd)
        if "ingest_nflverse" in s:
            return "ingest"
        if "ingest_headshots" in s:
            return "headshots"
        if "map_markets" in s:
            return "map"
        if "export_web" in s and "--upload-only" in s:
            return "upload"
        if "export_web" in s:
            return "export"
        return s

    def names(self):
        return [self.name(c) for c in self.calls]


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Every test in this module gets its own database.

    run() writes a source_health row on every exit now. Before that it wrote
    nothing, so these tests were harmless unpinned - afterwards SEVEN of them
    wrote straight into the live store, which is how the production
    weekly_refresh row came to read ok=0 while the job was healthy. Autouse
    rather than per-test, because the failure mode is a test that forgets.
    """
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "health.db"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    dest = tmp_path / "export"
    (dest / "nfl").mkdir(parents=True)
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(dest))
    monkeypatch.setattr(config, "WEB_SITE_URL", "https://site.example")
    (dest / "nfl" / "manifest.json").write_text(json.dumps(
        {"generated_at": "2026-09-15T18:00:00Z",
         "current": {"season": 2026, "stale": False, "stale_reason": None}}))
    return tmp_path, dest


def log_to(tmp_path):
    return W.Log(str(tmp_path / "refresh.log"))


def read_log(tmp_path):
    return open(tmp_path / "refresh.log", encoding="utf-8").read()


def matching_fetch(url):
    assert url == "https://site.example/data/nfl/manifest.json"
    return {"generated_at": "2026-09-15T18:00:00Z"}


def test_steps_run_in_order_and_touch_git_only_for_the_slug_registry(env):
    """A data refresh builds nothing and commits no data. Its ONLY git use is the
    slug registry in this repo - every git call carries the web/slugs pathspec -
    and it never runs npm."""
    tmp, _ = env
    r = Runner()
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    # python steps only: `git commit -m <message>` also contains "-m"
    py_steps = [c[c.index("-m") + 1] for c in r.calls if c and c[0] != "git" and "-m" in c]
    assert py_steps == ["jobs.ingest_nflverse", "jobs.ingest_headshots", "jobs.map_markets",
                        "jobs.export_web", "jobs.export_web"]
    git_calls = [c for c in r.calls if c and c[0] == "git"]
    assert git_calls, "the refresh should check the slug registry"
    assert all(c[-1] == W.SLUG_PATH and c[-2] == "--" for c in git_calls)
    flat = [" ".join(c).lower() for c in r.calls]
    assert not any("npm" in c for c in flat)
    assert "live manifest matches" in read_log(tmp)


def test_export_failure_stops_before_upload(env):
    tmp, _ = env
    r = Runner(fail={"export"})
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 1
    assert r.names() == ["ingest", "headshots", "map", "export"]


def test_upload_failure_is_an_error(env):
    tmp, _ = env
    r = Runner(fail={"upload"})
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 2
    assert "ERROR" in read_log(tmp)


def test_ingest_headshot_and_map_failures_degrade_rather_than_die(env):
    tmp, _ = env
    r = Runner(fail={"ingest", "headshots", "map"})
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    assert "upload" in r.names()


def test_stale_nflverse_is_a_warning_not_an_error(env):
    tmp, dest = env
    (dest / "nfl" / "manifest.json").write_text(json.dumps(
        {"generated_at": "2026-09-15T18:00:00Z",
         "current": {"season": 2026, "stale": True, "stale_reason": "week 2 missing"}}))
    assert W.run(runner=Runner(), log=log_to(tmp), fetch=matching_fetch) == 0
    log = read_log(tmp)
    assert "WARN" in log and "week 2 missing" in log


def test_validation_mismatch_and_unreachable_site_are_warnings(env):
    tmp, _ = env
    assert W.run(runner=Runner(), log=log_to(tmp),
                 fetch=lambda url: {"generated_at": "2026-01-01T00:00:00Z"}) == 0
    assert "!= local" in read_log(tmp)

    def boom(url):
        raise ConnectionError("no route")
    assert W.run(runner=Runner(), log=log_to(tmp), fetch=boom) == 0
    assert "unreachable" in read_log(tmp)


def test_skip_ingest(env):
    tmp, _ = env
    r = Runner()
    assert W.run(skip_ingest=True, runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    assert "ingest" not in r.names() and "headshots" in r.names()   # archive-only, still runs


def test_refuses_without_config(monkeypatch, tmp_path):
    # DB_PATH is pinned because run() now writes a source_health row on every
    # exit. Before that it wrote nothing, so this test was harmless without the
    # pin; afterwards it wrote "configuration missing" straight into the LIVE
    # store, which is how the production weekly_refresh row came to read ok=0
    # while the job was healthy. Every other test file that imports store
    # already pins this - 16 of 16 - and this one was the exception.
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", None)
    r = Runner()
    assert W.run(runner=r, log=log_to(tmp_path), fetch=matching_fetch) == 4
    assert r.calls == []


# ---------------------------------------------------------------- health rows

def test_a_failed_run_records_health_so_it_is_not_silent(env, monkeypatch):
    """The 09-16 failure: exit 1, no log, no health row, nothing noticed.

    The job renews quote_retention_hold, so a silent failure is a countdown to
    losing week-2 opening prices - which is exactly what it was until someone
    looked at the task's result code by hand.
    """
    tmp_path, _ = env
    seen = []
    monkeypatch.setattr(W.store, "record_health",
                        lambda source, ok, detail=None, watermark=None:
                            seen.append((source, ok, detail)))
    code = W.run(runner=Runner(fail=("export",)), log=log_to(tmp_path))
    assert code == 1
    assert seen and seen[-1][0] == "weekly_refresh"
    assert seen[-1][1] is False
    assert "export failed" in seen[-1][2]


def test_a_good_run_records_health_with_a_watermark(env, monkeypatch):
    tmp_path, _ = env
    seen = []
    monkeypatch.setattr(W.store, "record_health",
                        lambda source, ok, detail=None, watermark=None:
                            seen.append((source, ok, detail, watermark)))
    assert W.run(runner=Runner(), log=log_to(tmp_path), fetch=lambda url: {
        "generated_at": "2026-09-15T18:00:00Z"}) == 0
    assert seen[-1][0] == "weekly_refresh" and seen[-1][1] is True
    assert seen[-1][3] is not None, "a healthy run must advance the watermark"


def test_a_missing_dependency_fails_loudly_before_any_step_runs(env, monkeypatch):
    """The root cause, as a test.

    jsonschema was declared in requirements.txt and absent from the .venv the
    scheduled task uses, so export_web died at import - before the job could
    open its own log. Preflight turns that into a named failure.
    """
    tmp_path, _ = env
    seen = []
    monkeypatch.setattr(W, "preflight", lambda: ["jsonschema"])
    monkeypatch.setattr(W.store, "record_health",
                        lambda source, ok, detail=None, watermark=None:
                            seen.append((source, ok, detail)))
    r = Runner()
    assert W.run(runner=r, log=log_to(tmp_path)) == 4
    assert r.names() == [], "no step may run when a dependency is missing"
    assert "jsonschema" in seen[-1][2]


def test_preflight_names_the_real_imports_the_subprocesses_need():
    # Not a mock: these are the modules jobs/export_web.py imports at module
    # scope. If one is dropped from requirements this list is what notices.
    assert "jsonschema" in W.REQUIRED_IMPORTS
    assert W.preflight() == [], "this interpreter is missing a declared dependency"
