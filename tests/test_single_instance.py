"""One capture process per store. Run: pytest -q tests/test_single_instance.py

THE INCIDENT, AS A TEST. Two `cfb_probe.py` instances appended to the same
hourly shard on 2026-09-11 and 43 of 114 CFB shards failed `gunzip -t`. The
launcher's duplicate check did not fire because the second process was started
by hand, which is the path the launcher does not cover.

The load-bearing assertion here is NOT that a second acquisition raises - a PID
file would pass that. It is `test_the_lock_frees_when_the_holder_is_killed`:
a real process is killed with no chance to clean up, and the next process is
let in anyway. That is the property a PID file cannot have, and it is the
reason this is an OS lock.
"""
import ast
import os
import pathlib
import signal
import subprocess
import sys
import textwrap

import pytest

import config
from core import single_instance as si

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    """Pin the storage ROOT, not DB_PATH.

    `storage_path()` reads `config.STORAGE_DIR`, which config computes once at
    import time from DB_PATH - so pinning DB_PATH alone leaves the lock file in
    the real store, and these tests would fight the running logger for it.
    """
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    si._held.clear()
    yield
    for lock in list(si._held.values()):
        lock.release()


def child(tmp_path, name, hold_seconds):
    """A genuinely separate process that takes the lock and holds it.

    The store is passed by env, not by monkeypatch: a subprocess inherits the
    environment and nothing else, and LOGGER_DB is what config derives
    STORAGE_DIR from.

    IT REPORTS ITS OWN PID, AND `Popen.pid` IS NOT IT. On this dev box the venv
    `python.exe` is a redirector stub that re-execs the base interpreter, so
    Popen hands back the stub while the process that runs this code - and takes
    the lock, and must be the one killed - is its child. The first version of
    these tests asserted against `Popen.pid`; it failed here (37104 vs 22524)
    and would have passed on CI's Linux, which is the worst combination. Ask the
    holder who it is instead of inferring it.
    """
    src = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from core import single_instance as si
        try:
            lock = si.acquire({name!r})
        except si.AlreadyRunning:
            print("REFUSED", flush=True)
            raise SystemExit(9)
        print("HELD", os.getpid(), flush=True)
        time.sleep({hold_seconds})
    """)
    env = dict(os.environ, LOGGER_DB=str(tmp_path / "market_log.db"))
    return subprocess.Popen([sys.executable, "-c", src], env=env,
                            stdout=subprocess.PIPE, text=True)


def held_pid(proc) -> int:
    """Block until the child says it holds the lock, and return ITS pid."""
    parts = proc.stdout.readline().split()
    assert parts and parts[0] == "HELD", f"child did not take the lock: {parts}"
    return int(parts[1])


def kill(pid) -> None:
    """Kill by pid - the holder, not whatever Popen happens to be holding."""
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except (OSError, ProcessLookupError):
        pass


def test_the_lock_lands_in_the_configured_store(tmp_path):
    """Storage location comes from config, never from a path literal."""
    with si.acquire(si.LOGGER):
        path = pathlib.Path(si.lock_path(si.LOGGER))
        assert path.parent.parent == tmp_path
        assert path.exists()


def test_a_second_acquisition_is_refused():
    si.acquire(si.LOGGER)
    with pytest.raises(si.AlreadyRunning):
        si.acquire(si.LOGGER)


def test_the_refusal_names_the_holder():
    """A refusal that does not say who is holding it sends someone to Task
    Manager to guess, which is where the 09-11 duplicate survived for a day."""
    si.acquire(si.LOGGER)
    with pytest.raises(si.AlreadyRunning) as e:
        si.acquire(si.LOGGER)
    assert e.value.holder is not None, "the holder's record must be readable while held"
    assert e.value.holder["pid"] == os.getpid()
    assert str(os.getpid()) in str(e.value)


def test_releasing_lets_the_next_one_in():
    si.acquire(si.LOGGER).release()
    si.acquire(si.LOGGER).release()


def test_the_logger_and_the_probe_do_not_block_each_other():
    """They write disjoint shard trees. One lock for both would mean a running
    logger silently prevents a probe, which is not the rule being enforced."""
    a = si.acquire(si.LOGGER)
    b = si.acquire(si.CFB_PROBE)
    assert a.path != b.path


def test_a_separate_process_is_refused_while_the_holder_lives(tmp_path):
    """The actual 09-11 shape: two processes, not two calls in one."""
    proc = child(tmp_path, si.CFB_PROBE, hold_seconds=30)
    pid = None
    try:
        pid = held_pid(proc)
        with pytest.raises(si.AlreadyRunning) as e:
            si.acquire(si.CFB_PROBE)
        assert e.value.holder["pid"] == pid
        assert str(pid) in str(e.value)
    finally:
        # Both: the holder is what must die, and the stub is what Popen waits on.
        if pid:
            kill(pid)
        kill(proc.pid)
        proc.wait(timeout=10)


def test_the_lock_frees_when_the_holder_is_killed(tmp_path):
    """THE REASON THIS IS NOT A PID FILE.

    The holder is killed outright - no signal handler, no cleanup, no chance to
    remove anything. A PID file would still be sitting there and the next
    process would either refuse forever or ignore it. The kernel drops an
    advisory lock when the process dies, so the next one is simply let in.
    """
    proc = child(tmp_path, si.CFB_PROBE, hold_seconds=30)
    pid = held_pid(proc)
    kill(pid)                # the HOLDER, not Popen's stub - see child()
    proc.wait(timeout=10)
    si.acquire(si.CFB_PROBE).release()      # no exception is the assertion


def test_a_stale_lock_FILE_alone_does_not_block(tmp_path):
    """A leftover file from a dead process is not a lock. Only the OS lock is."""
    path = pathlib.Path(si.lock_path(si.LOGGER))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" + b'{"pid":999999,"started_iso":"whenever"}')
    si.acquire(si.LOGGER).release()


# ------------------------------------------------- InstanceLock (path-addressed)
#
# The context-manager API, kept compatible with Track C's cfb/lock.py so that
# module can retire onto this one by changing an import. MEASURED: the two are
# identical on every contention behaviour (refused while held, released on exit,
# freed when the holder is killed) because both take a byte-range lock on an
# open handle and the kernel owns liveness. What this one adds is holder identity
# and the `_held` registry `acquire()` needs for a daemon with no enclosing
# block. See docs/track-c-requests.md C1.


def path_child(tmp_path, lock_path, hold_seconds):
    """A separate process taking the SAME path via InstanceLock."""
    src = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from core import single_instance as si
        try:
            lk = si.InstanceLock({str(lock_path)!r}).__enter__()
        except si.AlreadyRunning:
            print("REFUSED", flush=True)
            raise SystemExit(9)
        print("HELD", os.getpid(), flush=True)
        time.sleep({hold_seconds})
    """)
    env = dict(os.environ, LOGGER_DB=str(tmp_path / "market_log.db"))
    return subprocess.Popen([sys.executable, "-c", src], env=env,
                            stdout=subprocess.PIPE, text=True)


def test_instancelock_acquires_and_releases(tmp_path):
    p = tmp_path / "locks" / "probe.lock"
    with si.InstanceLock(str(p)):
        assert p.exists()
    with si.InstanceLock(str(p)):
        pass                                # released on exit, so re-enterable


def test_instancelock_stamps_the_STEM_not_the_whole_path(tmp_path):
    """THE REGRESSION. The first version passed the path as both name and path,
    so the holder record carried the full path in `name` - redundant with
    `path`, unreadable in a refusal, and the field another track reads."""
    p = tmp_path / "locks" / "ingest_cfb.lock"
    with si.InstanceLock(str(p)):
        h = si._holder_at(str(p))
    assert h is not None
    assert h["name"] == "ingest_cfb", h["name"]
    assert os.sep not in h["name"] and not h["name"].endswith(".lock")


def test_instancelock_refuses_a_separate_process(tmp_path):
    p = tmp_path / "locks" / "probe.lock"
    with si.InstanceLock(str(p)):
        proc = path_child(tmp_path, p, hold_seconds=5)
        try:
            out = proc.stdout.readline().strip()
            assert out == "REFUSED", f"a second process got in: {out!r}"
        finally:
            kill(proc.pid)
            proc.wait(timeout=10)


def test_instancelock_frees_when_the_holder_is_killed(tmp_path):
    """The kernel owns liveness; no pid probe anywhere. On Windows a pid probe
    is itself destructive - `os.kill(pid, 0)` TERMINATES rather than tests,
    recorded in Track C's cfb/lock.py and true of this module too."""
    p = tmp_path / "locks" / "probe.lock"
    proc = path_child(tmp_path, p, hold_seconds=30)
    pid = held_pid(proc)
    kill(pid)
    proc.wait(timeout=10)
    with si.InstanceLock(str(p)):
        pass                                # no exception is the assertion


def test_instancelock_releases_on_an_exception(tmp_path):
    p = tmp_path / "locks" / "probe.lock"
    with pytest.raises(ValueError):
        with si.InstanceLock(str(p)):
            raise ValueError("boom")
    with si.InstanceLock(str(p)):
        pass                                # __exit__ ran despite the raise


def test_the_two_apis_lock_different_paths_so_they_cannot_disagree(tmp_path):
    """THE MIGRATION WINDOW. While both APIs exist they must not contend for one
    resource unnoticed. `acquire(name)` resolves through
    config.storage_path("locks", name); InstanceLock takes the path it is given.
    The second half - that the SAME path DOES contend - is what stops the first
    half passing for free.
    """
    named = si.acquire("run_logger")
    try:
        other = tmp_path / "other.lock"
        assert os.path.abspath(named.path) != os.path.abspath(str(other))
        with si.InstanceLock(str(other)):
            pass                            # different path: no contention
        with pytest.raises(si.AlreadyRunning):
            with si.InstanceLock(named.path):
                pass                        # same path: contends
    finally:
        named.release()


# --------------------------------------------------------------- it is WIRED

def calls_acquire(path: pathlib.Path) -> bool:
    """Does this module's `main()` call `.acquire(...)`?

    Matched on the AST rather than by grepping, for the same reason
    test_storage_paths.py does: a docstring explaining the guard quotes it by
    design, and a text search cannot tell that from the guard itself.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "acquire"):
                    return True
    return False


@pytest.mark.parametrize("module", ["run_logger.py", "cfb_probe.py"])
def test_every_capture_entrypoint_takes_the_lock(module):
    """A module nothing calls is not a guard. Both capture processes write raw
    shards, so both must hold a lock before the first write."""
    assert calls_acquire(ROOT / module), f"{module} main() never acquires a lock"
