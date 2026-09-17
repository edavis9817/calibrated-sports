"""The export refuses on a broken numeric stack. Run: pytest -q tests/test_export_preflight.py

WHY A PREFLIGHT AND NOT A try/except. On 2026-09-17 `jobs/export_web` segfaulted
inside `build_research()` on the dev box's default interpreter - exit 139, no
exception, no traceback - after writing market, players and teams and before
research and manifest. There was nothing to catch: the interpreter was gone.

The damage is the point. `export()` writes the tree part by part, so a crash in
the middle leaves every written file VALID and contract-clean beside a stale
manifest. The uploader then pushed 4,910 correct keys and reported success.
A truncated export is indistinguishable from a good one downstream, so the only
place it can be caught is before the first write.

The probe runs in a CHILD process because the probe itself segfaults; an
in-process check would kill the job it protects.
"""
import sys

import pytest

from jobs import export_web as E


@pytest.fixture(autouse=True)
def _clear_cache():
    """The result is cached per process so the suite pays one spawn, not one
    per export test. Every test here must start from an unknown stack."""
    E._numeric_ok = None
    yield
    E._numeric_ok = None


# ------------------------------------------------------------- it discriminates

def test_this_interpreter_passes():
    """The suite runs on a 3.12 venv, where numpy computes."""
    assert E.assert_numeric_stack() is True


def test_an_interpreter_whose_probe_FAILS_is_refused(tmp_path):
    """The broken build exits non-zero (139, a segfault). Simulated with a stub
    that exits 3, because a real segfault cannot be summoned portably - what is
    under test is that a non-zero probe REFUSES, not how it died."""
    stub = tmp_path / "stub.py"
    stub.write_text("import sys; sys.exit(3)", encoding="utf-8")
    with pytest.raises(E.NumericStackBroken) as e:
        E.assert_numeric_stack(executable=str(stub))
    assert "cannot compute" in str(e.value) or "could not probe" in str(e.value)


def test_a_SEGFAULT_exit_code_is_named_as_one(tmp_path):
    """139 is the shell's encoding of SIGSEGV. The message says so, because the
    first hour of this incident was spent not knowing the process had died."""
    import subprocess

    class FakeRun:
        returncode = 139
        stderr = b""

    E._numeric_ok = None
    orig = subprocess.run
    subprocess.run = lambda *a, **k: FakeRun()
    try:
        with pytest.raises(E.NumericStackBroken) as e:
            E.assert_numeric_stack(executable="anything")
    finally:
        subprocess.run = orig
    assert "SEGFAULT" in str(e.value)


def test_a_missing_interpreter_raises_rather_than_passing(tmp_path):
    """An unprobeable stack is not a working one. The failure mode this avoids
    is a guard that silently decides everything is fine when it cannot look."""
    with pytest.raises(E.NumericStackBroken):
        E.assert_numeric_stack(executable=str(tmp_path / "no_such_python.exe"))


def test_the_probe_makes_numpy_COMPUTE_not_merely_import():
    """`import core.distributions` SUCCEEDS on the broken build, so an import
    check would have passed while the export segfaulted minutes later."""
    assert "import numpy" in E._NUMPY_PROBE
    assert "finfo" in E._NUMPY_PROBE, "the probe must exercise numpy, not just load it"


def test_the_result_is_cached_so_the_suite_pays_one_spawn():
    import subprocess
    calls = []
    orig = subprocess.run

    class OK:
        returncode = 0
        stderr = b""

    subprocess.run = lambda *a, **k: (calls.append(1), OK())[1]
    try:
        E._numeric_ok = None
        E.assert_numeric_stack(executable=sys.executable)
        E.assert_numeric_stack(executable=sys.executable)
        E.assert_numeric_stack(executable=sys.executable)
    finally:
        subprocess.run = orig
    assert len(calls) == 1, f"probed {len(calls)} times; the cache is not working"


# ------------------------------------------------------------------ it is WIRED

def test_export_calls_the_preflight_before_anything_else():
    """A guard nothing calls is not a guard, and one called after the first
    write is not a preflight. Matched on the AST, and on ORDER: the call must
    precede the body that resolves `dest` and writes."""
    import ast
    import inspect

    src = inspect.getsource(E.export)
    tree = ast.parse(src.strip())
    fn = tree.body[0]
    calls = [i for i, node in enumerate(fn.body)
             for sub in ast.walk(node)
             if isinstance(sub, ast.Call) and getattr(sub.func, "id", None) == "assert_numeric_stack"]
    assert calls, "export() never calls assert_numeric_stack"
    assert calls[0] == 0, f"the preflight is statement {calls[0]}, not the first"
