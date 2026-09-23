"""prod_sync.ps1 - the production clone's drift check (unit a-10).

Scheduled tasks run from a production clone that must sit on origin/main with a
clean tree and no other branch. The script's whole value is that it FAILS when
that is untrue, so every verdict below is shown firing on its own input and, for
the -Update path, the tree is checked to be exactly where it was when the script
refused. A check that only ever printed OK would pass a test that only looked
for OK.

Everything runs against a bare origin and a clone under tmp_path: no network,
and no real clone is touched. It needs Windows PowerShell and git, so it skips
where either is absent - which is CI (ubuntu), stated in the reason so the skip
never reads as a pass.
"""
import http.server
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "prod_sync.ps1")
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(
    not (POWERSHELL and shutil.which("git") and sys.platform == "win32"),
    reason="prod_sync.ps1 needs Windows PowerShell and git; not available here")


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


def commit(repo, path, text, msg):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    git(repo, "add", "--", path)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    """origin (bare) <- author (pushes) ; prod (the production clone)."""
    origin = tmp_path / "origin.git"
    author = tmp_path / "author"
    prod = tmp_path / "prod"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(author)], check=True,
                   capture_output=True)
    for repo in (author,):
        git(repo, "config", "user.email", "t@example.invalid")
        git(repo, "config", "user.name", "t")
        git(repo, "config", "core.autocrlf", "false")
    commit(author, ".gitignore", ".env\n.venv/\n", "ignore")
    commit(author, "README.md", "one\n", "c1")
    commit(author, "web/slugs/nfl.json", "{}\n", "c2")
    commit(author, "requirements.txt", "# nothing\n", "c3")
    git(author, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(prod)], check=True,
                   capture_output=True)
    git(prod, "config", "user.email", "t@example.invalid")
    git(prod, "config", "user.name", "t")
    git(prod, "config", "core.autocrlf", "false")
    (prod / ".env").write_text("LOGGER_DB=" + str(tmp_path / "store" / "x.db").replace("\\", "/") + "\n")
    return {"origin": origin, "author": author, "prod": prod, "log": tmp_path / "sync.log"}


def run(w, *flags):
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", SCRIPT, "-Root", str(w["prod"]), "-LogPath", str(w["log"]), "-NoLogger", *flags],
        capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def head(w):
    return git(w["prod"], "rev-parse", "HEAD")


def advance_origin(w, n=2):
    for i in range(n):
        tip = commit(w["author"], "README.md", f"v{i}\n", f"later {i}")
    git(w["author"], "push", "-q", "origin", "main")
    return tip


def test_clean_clone_is_ok_and_logs_one_line(world):
    rc, out = run(world)
    assert rc == 0, out
    assert " OK " in out and "DRIFT" not in out
    lines = world["log"].read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 1 and " OK " in lines[0]


def test_a_unit_branch_checked_out_is_drift(world):
    git(world["prod"], "checkout", "-q", "-b", "a-99-some-unit")
    rc, out = run(world)
    assert rc == 1, out
    assert "on branch 'a-99-some-unit', not main" in out
    assert "local branches besides main: a-99-some-unit" in out


def test_a_second_local_branch_is_drift_even_on_main(world):
    git(world["prod"], "branch", "leftover")
    rc, out = run(world)
    assert rc == 1 and "local branches besides main: leftover" in out, out


def test_untracked_file_is_drift_but_ignored_env_is_not(world):
    assert run(world)[0] == 0          # .env exists and is ignored
    (world["prod"] / "stray.py").write_text("x = 1\n")
    rc, out = run(world)
    assert rc == 1 and "working tree not clean (1): ?? stray.py" in out, out


def test_slug_commit_is_named_and_survives_update(world):
    """The one local commit production itself makes. It must be reported as
    what it is, and an -Update must neither fast-forward over it nor lose it."""
    tip = commit(world["prod"], "web/slugs/nfl.json", '{"a": 1}\n', "slugs: registry append")
    advance_origin(world)
    rc, out = run(world, "-Update")
    assert rc == 1, out
    assert "touching only web/slugs" in out
    assert "NOT fast-forwarded" in out
    assert head(world) == tip


def test_any_other_local_commit_is_drift_with_its_files(world):
    commit(world["prod"], "README.md", "hand edit\n", "hotfix in prod")
    rc, out = run(world)
    assert rc == 1 and "not on origin/main, touching: README.md" in out, out


def test_behind_is_drift_in_check_mode_and_changes_nothing(world):
    before = head(world)
    advance_origin(world, 3)
    rc, out = run(world)
    assert rc == 1 and "behind origin/main by 3 commit(s)" in out, out
    assert head(world) == before


def test_update_fast_forwards_a_clean_clone(world):
    tip = advance_origin(world, 2)
    rc, out = run(world, "-Update")
    assert rc == 0, out
    assert "fast-forwarded 2 commit(s)" in out
    assert head(world) == tip
    assert run(world)[0] == 0          # and the next check agrees


def test_update_refuses_on_a_dirty_tree(world):
    before = head(world)
    advance_origin(world)
    (world["prod"] / "stray.py").write_text("x = 1\n")
    rc, out = run(world, "-Update")
    assert rc == 1 and "NOT fast-forwarded" in out, out
    assert head(world) == before


@pytest.fixture
def venv(world):
    subprocess.run([sys.executable, "-m", "venv", str(world["prod"] / ".venv")], check=True)
    return world["prod"] / ".venv" / "Scripts" / "python.exe"


def test_update_defers_while_a_job_runs_from_the_clone(world, venv):
    before = head(world)
    advance_origin(world)
    job = subprocess.Popen([str(venv), "-c", "import time; time.sleep(90)"])
    try:
        time.sleep(1.5)
        rc, out = run(world, "-Update")
    finally:
        job.kill()
    assert rc == 0 and "DEFERRED" in out and str(job.pid) in out, out
    assert head(world) == before


def test_requirements_change_reinstalls_and_a_failed_install_is_drift(world, venv):
    commit(world["author"], "requirements.txt", "# still nothing\n", "req ok")
    git(world["author"], "push", "-q", "origin", "main")
    rc, out = run(world, "-Update")
    assert rc == 0 and "requirements.txt changed; pip install ok" in out, out

    commit(world["author"], "requirements.txt", "-e ./no-such-local-package\n", "req broken")
    git(world["author"], "push", "-q", "origin", "main")
    rc, out = run(world, "-Update")
    assert rc == 1 and "pip install failed" in out, out


class _Hook(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        _Hook.seen.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_healthcheck_pinged_on_ok_and_on_drift_at_fail(world):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Hook.seen = []
    with open(world["prod"] / ".env", "a") as f:
        f.write(f"PROD_SYNC_HEALTHCHECK_URL=http://127.0.0.1:{srv.server_port}/uuid-1\n")
    try:
        assert run(world)[0] == 0
        git(world["prod"], "branch", "leftover")
        assert run(world)[0] == 1
    finally:
        srv.shutdown()
    assert _Hook.seen == ["/uuid-1", "/uuid-1/fail"]
