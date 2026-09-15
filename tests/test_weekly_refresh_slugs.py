"""The weekly refresh commits the slug registry - and only the slug registry."""
from types import SimpleNamespace

from jobs import weekly_refresh as W


class Runner:
    def __init__(self, status_out="", fail=None):
        self.calls, self.status_out, self.fail = [], status_out, fail or set()

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        name = cmd[1] if cmd[0] == "git" else "other"
        out = self.status_out if name == "status" else ""
        return SimpleNamespace(returncode=1 if name in self.fail else 0, stdout=out, stderr="")


def _log():
    lines = []
    return lines, (lambda level, msg: lines.append((level, msg)))


def test_unchanged_registry_makes_no_commit():
    r, (lines, log) = Runner(status_out=""), _log()
    assert W.commit_slug_registry(r, log) is False
    assert [c[1] for c in r.calls] == ["status"]


def test_changed_registry_commits_only_the_registry_path():
    r, (lines, log) = Runner(status_out=" M web/slugs/nfl.json\n"), _log()
    assert W.commit_slug_registry(r, log) is True
    add, commit = r.calls[1], r.calls[2]
    assert add == ["git", "add", "--", "web/slugs"]
    assert commit[:2] == ["git", "commit"] and commit[-2:] == ["--", "web/slugs"]


def test_a_failed_commit_is_a_warning_not_a_failure():
    r, (lines, log) = Runner(status_out=" M web/slugs/nfl.json\n", fail={"commit"}), _log()
    assert W.commit_slug_registry(r, log) is False
    assert any(level == "WARN" for level, _ in lines)
