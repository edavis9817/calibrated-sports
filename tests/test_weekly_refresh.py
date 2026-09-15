"""Brief W02 A4: the weekly refresh runs its steps in order, gates on the site
build, commits only on a diff, and treats late nflverse as a warning."""
import json
import os
from types import SimpleNamespace

import pytest

import config
from jobs import weekly_refresh as W


class Runner:
    def __init__(self, fail=(), diff=True):
        self.calls, self.fail, self.diff = [], set(fail), diff

    def __call__(self, cmd, cwd=None, capture_output=True, text=True):
        self.calls.append(cmd)
        name = self.name(cmd)
        if name == "diff":
            return SimpleNamespace(returncode=1 if self.diff else 0, stdout="", stderr="")
        return SimpleNamespace(returncode=1 if name in self.fail else 0, stdout="ok", stderr="")

    @staticmethod
    def name(cmd):
        s = " ".join(cmd)
        for key, n in (("ingest_nflverse", "ingest"), ("map_markets", "map"),
                       ("export_web", "export"), ("run check", "gate"), ("git add", "stage"),
                       ("diff --cached", "diff"), ("git commit", "commit"), ("git push", "push")):
            if key in s:
                return n
        return s

    def names(self):
        return [self.name(c) for c in self.calls]


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "web" / "public" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "WEB_REPO_DIR", str(tmp_path / "web"))
    monkeypatch.setattr(config, "WEB_DATA_DIR", str(data))
    (data / "manifest.json").write_text(json.dumps(
        {"current": {"season": 2026, "week": 2, "stale": False, "stale_reason": None}}))
    return tmp_path, data


def log_to(tmp_path):
    return W.Log(str(tmp_path / "refresh.log"))


def test_steps_run_in_order_and_push(env):
    tmp, _ = env
    r = Runner()
    assert W.run(runner=r, log=log_to(tmp)) == 0
    assert r.names() == ["ingest", "map", "export", "gate", "stage", "diff", "commit", "push"]
    commit = [c for c in r.calls if Runner.name(c) == "commit"][0]
    assert commit[-1] == "data: 2026 week 2"


def test_gate_failure_commits_and_pushes_nothing(env):
    tmp, _ = env
    r = Runner(fail={"gate"})
    assert W.run(runner=r, log=log_to(tmp)) == 2
    assert "commit" not in r.names() and "push" not in r.names()
    assert "ERROR" in open(tmp / "refresh.log", encoding="utf-8").read()


def test_export_failure_stops_before_the_gate(env):
    tmp, _ = env
    r = Runner(fail={"export"})
    assert W.run(runner=r, log=log_to(tmp)) == 1
    assert r.names() == ["ingest", "map", "export"]


def test_ingest_failure_degrades_rather_than_dies(env):
    tmp, _ = env
    r = Runner(fail={"ingest"})
    assert W.run(runner=r, log=log_to(tmp)) == 0
    assert "push" in r.names()


def test_no_diff_means_no_commit(env):
    tmp, _ = env
    r = Runner(diff=False)
    assert W.run(runner=r, log=log_to(tmp)) == 0
    assert "commit" not in r.names()


def test_stale_nflverse_is_a_warning_not_an_error(env):
    tmp, data = env
    (data / "manifest.json").write_text(json.dumps(
        {"current": {"season": 2026, "week": 3, "stale": True, "stale_reason": "week 2 missing"}}))
    r = Runner()
    assert W.run(runner=r, log=log_to(tmp)) == 0
    log = open(tmp / "refresh.log", encoding="utf-8").read()
    assert "WARN" in log and "week 2 missing" in log
    assert [c for c in r.calls if Runner.name(c) == "commit"][0][-1].endswith("(nflverse stale)")


def test_flags_skip_ingest_and_push(env):
    tmp, _ = env
    r = Runner()
    assert W.run(skip_ingest=True, no_push=True, runner=r, log=log_to(tmp)) == 0
    assert "ingest" not in r.names() and "push" not in r.names()


def test_refuses_without_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WEB_REPO_DIR", None)
    r = Runner()
    assert W.run(runner=r, log=log_to(tmp_path)) == 4
    assert r.calls == []
