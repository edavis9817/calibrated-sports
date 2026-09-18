"""Contract v2: the weekly refresh runs ingest -> map -> export -> upload ->
validate, never runs npm, uses git ONLY to commit the slug registry
(web/slugs pathspec), and treats late nflverse and an unreachable site as
warnings."""
import json
import os
from types import SimpleNamespace

import pytest

import config
from jobs import export_web as E
from jobs import weekly_refresh as W


class Runner:
    """A fake that answers PER STEP, because the steps now talk to each other.

    It returned stdout="ok" for everything, which was honest while nothing read
    a step's output. The export step now prints the REFRESHED sentinel and the
    upload step prints its JSON summary, and `_run` reads both - so a fake that
    still said "ok" to both would drive only the no-declaration and
    unparseable-summary paths while looking like an ordinary passing run. That
    is a stand-in producing the same green as the thing.

    `refreshed=None` means the export printed NO sentinel; `refreshed=()` means
    it declared it rebuilt nothing. Those are different facts downstream.
    """

    def __init__(self, fail=(), refreshed=("nfl/players/", "nfl/teams/"), upload_stats=None):
        self.calls, self.fail = [], set(fail)
        self.refreshed = refreshed
        self.upload_stats = upload_stats

    def stdout_for(self, step):
        if step == "export" and self.refreshed is not None:
            # The real main() prints its JSON summary first and the sentinel
            # last, so the parser has to find one line among many.
            return ('{"counts": {}}\nunresolved ids: 0\n'
                    + E.REFRESHED_SENTINEL + " " + " ".join(self.refreshed))
        if step == "upload":
            return json.dumps(self.upload_stats if self.upload_stats is not None
                              else {"configured": True, "deleted": 0, "removed_withheld": 0})
        return "ok"

    def __call__(self, cmd, cwd=None, capture_output=True, text=True):
        self.calls.append(cmd)
        step = self.name(cmd)
        return SimpleNamespace(returncode=1 if step in self.fail else 0,
                               stdout=self.stdout_for(step), stderr="")

    def cmd_for(self, step):
        for c in self.calls:
            if self.name(c) == step:
                return c
        raise AssertionError(f"no {step} step ran: {self.names()}")

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


# ------------------------------------------- the declaration reaches the uploader

def test_the_export_declaration_reaches_the_upload_command_line(env):
    """THE SECOND HALF OF THE MECHANISM. `upload()` can scope its deletions, but
    only if somebody tells it what was rebuilt - and this job is the only caller
    that runs an export and an upload in the same breath. A safe default nobody
    teaches is a permanent leak: unthreaded, every weekly run withholds every
    deletion forever and R2 grows keys the export stopped producing.
    """
    tmp, _ = env
    r = Runner(refreshed=("nfl/players/", "nfl/teams/"))
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0

    cmd = r.cmd_for("upload")
    assert "--refreshed" in cmd, f"the upload step got no declaration: {cmd}"
    assert cmd[cmd.index("--refreshed") + 1] == "nfl/players/ nfl/teams/"


def test_the_declaration_comes_from_the_export_THAT_JUST_RAN(env):
    """Passed, never persisted. The value on the upload command line is whatever
    this run's export printed - so a different export prints a different
    declaration, and no stale copy can authorise a deletion for a run that did
    not happen."""
    tmp, _ = env
    r = Runner(refreshed=("research/",))
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    cmd = r.cmd_for("upload")
    assert cmd[cmd.index("--refreshed") + 1] == "research/"


def test_an_export_that_declares_nothing_passes_an_EMPTY_flag_not_no_flag(env):
    """A manifest-only run owns no prefix and says so. That is DECLARED-and-empty,
    which reaches the uploader as `--refreshed ""` and is a different fact from
    the flag being absent."""
    tmp, _ = env
    r = Runner(refreshed=())
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    cmd = r.cmd_for("upload")
    assert "--refreshed" in cmd
    assert cmd[cmd.index("--refreshed") + 1] == ""


def test_an_export_with_no_sentinel_warns_and_passes_no_flag_at_all(env):
    """The other answer on the other input. If the sentinel ever stops being
    printed, the job must not invent a declaration - it withholds every deletion
    and says so in the log."""
    tmp, _ = env
    r = Runner(refreshed=None)
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    assert "--refreshed" not in r.cmd_for("upload")
    assert "printed no REFRESHED line" in read_log(tmp)


def test_withheld_deletions_are_surfaced_where_a_human_will_see_them(env):
    """`removed_withheld` non-zero WITH a declaration is the signal that the
    threading broke. It is only worth computing if it is printed."""
    tmp, _ = env
    r = Runner(upload_stats={"configured": True, "deleted": 0, "removed_withheld": 7})
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    log = read_log(tmp)
    assert "WARN" in log and "7 deletion(s) OUTSIDE" in log


def test_withheld_with_NO_declaration_is_reported_as_benign_not_as_a_warning(env):
    """Same number, different reading. Nothing was declared, so nothing was
    deleted and nothing is wrong - crying WARN here would train a reader to
    ignore the line that matters."""
    tmp, _ = env
    r = Runner(refreshed=None,
               upload_stats={"configured": True, "deleted": 0, "removed_withheld": 7})
    assert W.run(runner=r, log=log_to(tmp), fetch=matching_fetch) == 0
    log = read_log(tmp)
    assert "7 deletion(s): no prefixes were declared" in log
    assert "7 deletion(s) OUTSIDE" not in log


def test_an_unparseable_upload_summary_does_not_fail_the_job(env):
    """Bookkeeping must not break the job. An upload that exits 0 but prints
    something unexpected is still a successful upload."""
    tmp, _ = env

    class Garbled(Runner):
        def stdout_for(self, step):
            return "not json" if step == "upload" else super().stdout_for(step)

    assert W.run(runner=Garbled(), log=log_to(tmp), fetch=matching_fetch) == 0


def test_preflight_names_the_real_imports_the_subprocesses_need():
    # Not a mock: these are the modules jobs/export_web.py imports at module
    # scope. If one is dropped from requirements this list is what notices.
    assert "jsonschema" in W.REQUIRED_IMPORTS
    assert W.preflight() == [], "this interpreter is missing a declared dependency"
