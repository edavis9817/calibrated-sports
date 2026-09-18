"""Any test that reads the live store must declare a skip guard, mechanically.

WHY THIS IS A TEST AND NOT A HABIT. The store-less reproduction is documented in
CLAUDE.md and was used earlier in the very session that shipped a test without
it: `test_every_key_this_builder_produces_is_under_the_prefix_it_owns` called
`paths.connect(read_only=True)` with no guard, went red on CI and on every
store-less machine, and blocked other tracks. The tool existed, was documented,
and did not fire - the same shape as a case-sensitivity habit that exists and is
not applied.

So it is a checklist item enforced by the checklist, not by memory. A module
that reaches the store must carry a module-level `skipif`, and a test that
reaches it must be marked with one.

SCOPED TO TRACK F'S OWN TEST FILES. Widening it repo-wide is a change to other
tracks' tests and is theirs to make; `WIDEN_TO` is the one line that does it.
"""
import ast
import os
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
WIDEN_TO = "test_analytics_"

# Calls that open the LIVE store: `paths.connect()` and `paths.market_log_ro()`
# both raise on a machine without one. `paths.db_path()` and
# `paths.market_log_path()` are pure string work and are not listed.
#
# MATCHED ON THE MODULE, NOT THE METHOD NAME. The first version matched any
# `.connect(...)` and duly flagged two tests that call
# `sqlite3.connect(tmp_path / "other.db")` - a temp file, no store in sight.
# A guard whose first run is all false positives gets switched off, so it
# checks `paths.<call>` specifically.
STORE_MODULE = "paths"
REACHES_STORE = {"connect", "market_log_ro"}


def _modules():
    return sorted(p for p in HERE.glob("%s*.py" % WIDEN_TO))


def _calls_store(node):
    """`paths.connect(...)` or `paths.market_log_ro(...)`, and nothing else."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        if (isinstance(fn, ast.Attribute) and fn.attr in REACHES_STORE
                and getattr(fn.value, "id", None) == STORE_MODULE):
            return "%s.%s" % (STORE_MODULE, fn.attr)
    return None


def _has_skip_decorator(fn, guards):
    for dec in fn.decorator_list:
        for sub in ast.walk(dec):
            name = getattr(sub, "attr", None) or getattr(sub, "id", None)
            if name in guards or name == "skipif":
                return True
    return False


def _module_guards(tree):
    """Names bound at module level from a `pytest.mark.skipif(...)` call."""
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            joined = ast.dump(node.value)
            if "skipif" in joined:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out.add(t.id)
    return out


def test_this_guard_actually_sees_some_modules():
    """A guard that inspects nothing passes silently. Assert the shape of what
    it read before trusting what it concludes."""
    mods = _modules()
    assert len(mods) >= 3, [p.name for p in mods]


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_every_store_touching_test_declares_a_skip_guard(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guards = _module_guards(tree)
    offenders = []
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef)
                and node.name.startswith("test_")):
            continue
        call = _calls_store(node)
        if call and not _has_skip_decorator(node, guards):
            offenders.append("%s (calls %s)" % (node.name, call))
    assert not offenders, (
        "%s: these read the live store with no skip guard, so they are red on "
        "CI and on any store-less machine: %s. Declare a module-level "
        "`pytest.mark.skipif` and decorate them with it - see HAS_METRICS / "
        "needs_metrics in test_analytics_contract.py."
        % (path.name, "; ".join(offenders)))


def test_the_guard_catches_an_unguarded_store_call(tmp_path):
    """A guard that has never been seen to fail is not a guard."""
    bad = tmp_path / "t.py"
    bad.write_text("from analytics import paths\n"
                   "def test_x():\n"
                   "    con = paths.connect(read_only=True)\n", encoding="utf-8")
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef)][0]
    assert _calls_store(fn) == "paths.connect"
    assert not _has_skip_decorator(fn, _module_guards(tree))


def test_the_guard_does_not_flag_sqlite3_connect_on_a_temp_file(tmp_path):
    """The false positive that the first run produced. `sqlite3.connect` on a
    tmp_path needs no guard and flagging it would get the guard switched off."""
    f = tmp_path / "t.py"
    f.write_text("\n".join([
        "import sqlite3",
        "def test_x(tmp_path):",
        "    con = sqlite3.connect(str(tmp_path / 'o.db'))",
    ]), encoding="utf-8")
    tree = ast.parse(f.read_text(encoding="utf-8"))
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef)][0]
    assert _calls_store(fn) is None


def test_the_guard_accepts_a_decorated_store_call(tmp_path):
    good = tmp_path / "t.py"
    good.write_text("import pytest\n"
                    "from analytics import paths\n"
                    "needs = pytest.mark.skipif(False, reason='x')\n"
                    "@needs\n"
                    "def test_x():\n"
                    "    con = paths.connect(read_only=True)\n", encoding="utf-8")
    tree = ast.parse(good.read_text(encoding="utf-8"))
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef)][0]
    assert _calls_store(fn) == "paths.connect"
    assert _has_skip_decorator(fn, _module_guards(tree))
