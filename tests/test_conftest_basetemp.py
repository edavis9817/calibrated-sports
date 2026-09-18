"""The suite's own temp location. Run: pytest -q tests/test_conftest_basetemp.py

WHY THIS FILE EXISTS. `conftest.py` redirects pytest's basetemp onto the disk
holding the configured store, because `disk_headroom_ok()` measures the drive
under `config.RAW_DIR` and the fixtures point that at `tmp_path`. Without it the
suite inherits the free space of whichever drive hosts the OS temp directory:
on 2026-09-17 that drive fell below the 5 GB floor and 15 tests failed with
`OSError: disk below 5GB floor` while nothing was wrong with the code.

THE GUARD BROKE ONCE, IMMEDIATELY, AND IN THE WORST WAY. Its first version
called `os.path.commonpath([store_root, checkout])`, which RAISES on Windows
when the two are on different drives - the normal configuration here, checkout
on C: and store on D:. The exception escaped `pytest_configure` as an
INTERNALERROR that killed the entire run. A fix that breaks every local
invocation of the suite it protects is worse than the problem, and nothing but
running it would have caught that.

`test_a_store_on_another_drive_does_not_kill_the_run` is that regression. It
forces the ValueError rather than relying on drive letters, because the failure
is Windows-only and CI is ubuntu - a test that can only fail on the machine it
was written on is not a guard.
"""
import os

import pytest

import conftest


class FakeOption:
    def __init__(self, basetemp=None):
        self.basetemp = basetemp


class FakeConfig:
    """The slice of pytest's Config that `pytest_configure` touches."""

    def __init__(self, basetemp=None):
        self.option = FakeOption(basetemp)

    def getoption(self, name, default=None):
        return getattr(self.option, name, default)


def configure(monkeypatch, db_path, basetemp=None):
    monkeypatch.setattr(conftest.cs_config, "DB_PATH", db_path)
    cfg = FakeConfig(basetemp)
    conftest.pytest_configure(cfg)
    return cfg.option.basetemp


def test_a_configured_store_moves_basetemp_onto_its_disk(tmp_path, monkeypatch):
    store = tmp_path / "data"
    store.mkdir()
    chosen = configure(monkeypatch, str(store / "market_log.db"))
    assert os.path.dirname(chosen) == str(store / conftest.BASETEMP_NAME)
    # Restored to the test it belongs to. Replacing this body left the assertion
    # stranded at the end of a LATER test, where `chosen` is not defined - a
    # NameError that only fired because the suite ran it. Selecting a path is
    # not the same as creating it, and pytest is handed a directory it expects
    # to exist.
    assert os.path.isdir(chosen), "the hook must create the directory it selects"


# ---------------------------------------------------------------- per-run dirs
#
# ONE SHARED BASETEMP DOES NOT SURVIVE THIS STORE. pytest deletes and recreates
# the directory it is handed, but on Windows a directory cannot be unlinked
# while any process holds a handle inside it - and background exports run
# against this drive for minutes. The delete fails (WinError 145), the recreate
# fails (WinError 183), and every `tmp_path` test ERRORS AT SETUP.
#
# Measured 2026-09-18: 8 errors in `test_web_contract.py` in the working tree
# while a clean clone of the same commit passed 23/23 and the full suite passed
# 1186 - a combination that reads exactly like a regression in whatever landed
# most recently. The next person to hit it may be another track.

def test_each_run_gets_its_own_directory(tmp_path, monkeypatch):
    """Two runs must not be handed the same path. The old assertion - basetemp
    equals `<store>/pytest-tmp` - would have passed a hook that collided every
    single time, which is the whole defect."""
    store = tmp_path / "data"
    store.mkdir()
    first = configure(monkeypatch, str(store / "market_log.db"))
    second = configure(monkeypatch, str(store / "market_log.db"))
    assert first != second
    assert os.path.dirname(first) == os.path.dirname(second)


def test_the_sweep_survives_a_directory_it_cannot_remove(tmp_path):
    """THE PROPERTY THAT MATTERS MOST. A locked leftover is the normal case on
    this box; a sweep that raised would fail the suite it is housekeeping for,
    which is strictly worse than the residue it is clearing."""
    parent = tmp_path / "pytest-tmp"
    parent.mkdir()
    dirs = []
    for i in range(6):
        d = parent / f"run-{i}"
        d.mkdir()
        (d / "f.txt").write_text("x", encoding="utf-8")
        dirs.append(d)

    held = open(dirs[0] / "f.txt", encoding="utf-8")       # oldest, and locked
    try:
        conftest._sweep(str(parent), keep=3)               # must not raise
    finally:
        held.close()

    left = {p.name for p in parent.iterdir()}
    assert "run-5" in left and "run-4" in left, "the newest runs were swept"
    assert len(left) <= 4, f"nothing was swept: {sorted(left)}"


def test_the_sweep_keeps_the_newest_and_never_raises_on_an_empty_parent(tmp_path):
    """Discriminating: a `_sweep` that always returned early would satisfy the
    test above. It must actually remove something, and tolerate a missing dir."""
    parent = tmp_path / "pytest-tmp"
    parent.mkdir()
    for i in range(5):
        (parent / f"run-{i}").mkdir()
    conftest._sweep(str(parent), keep=2)
    assert len(list(parent.iterdir())) == 2, "the sweep removed nothing"
    conftest._sweep(str(tmp_path / "does-not-exist"), keep=2)   # must not raise


def test_an_unconfigured_store_leaves_pytest_alone(monkeypatch):
    """CI's case: DB_PATH falls back to the relative default, which resolves
    inside the checkout, and pytest's own default temp is correct there."""
    assert configure(monkeypatch, "data/market_log.db") is None


def test_an_explicit_basetemp_always_wins(tmp_path, monkeypatch):
    store = tmp_path / "data"
    store.mkdir()
    chosen = configure(monkeypatch, str(store / "market_log.db"),
                       basetemp="chosen/by/the/caller")
    assert chosen == "chosen/by/the/caller"


def test_a_store_on_another_drive_does_not_kill_the_run(tmp_path, monkeypatch):
    """THE REGRESSION, forced rather than hoped for.

    `commonpath` raises ValueError across drives on Windows. The hook must treat
    that as "not nested" - which is what it means - and carry on, never let it
    escape into an INTERNALERROR.
    """
    store = tmp_path / "data"
    store.mkdir()

    def boom(_paths):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(conftest.os.path, "commonpath", boom)
    chosen = configure(monkeypatch, str(store / "market_log.db"))
    assert os.path.dirname(chosen) == str(store / conftest.BASETEMP_NAME)


def test_it_never_selects_the_store_root(tmp_path, monkeypatch):
    """pytest DELETES and recreates basetemp. Selecting the store root would
    wipe the logger's database.

    ASSERTS THE PROPERTY, NOT THE PATH. This used to read
    `basename(chosen) == BASETEMP_NAME`, which pinned the old single-directory
    shape rather than the safety rule - and would have passed a hook that handed
    every run the SAME directory, which is the collision this file now exists to
    prevent. What matters is that the chosen path is neither the store root nor
    anything outside the dedicated parent.
    """
    store = tmp_path / "data"
    store.mkdir()
    chosen = configure(monkeypatch, str(store / "market_log.db"))
    assert os.path.abspath(chosen) != os.path.abspath(str(store))
    assert os.path.dirname(chosen) == str(store / conftest.BASETEMP_NAME)
    assert os.path.abspath(chosen).startswith(os.path.abspath(str(store)) + os.sep)


def test_it_refuses_to_write_into_the_checkout(monkeypatch):
    """Even if someone points the store at the repo, the hook declines rather
    than handing pytest a directory inside the working tree to delete.

    THE DIRECTORY MUST EXIST FOR THIS TO TEST ANYTHING. The first version aimed
    at `<repo>/data`, which is not present in a checkout, so the hook returned
    at the `isdir` check and this test passed green without ever reaching the
    guard it is named after. `tests/` is used instead because it is guaranteed
    present wherever this file runs, CI included, and the assertion below fails
    loudly rather than letting the test quietly degrade again.
    """
    repo = os.path.dirname(os.path.abspath(conftest.__file__))
    root = os.path.join(repo, "tests")
    assert os.path.isdir(root), (
        "the checkout guard can only be exercised against a directory that "
        "exists; otherwise the isdir check returns first and proves nothing")
    assert configure(monkeypatch, os.path.join(root, "market_log.db")) is None


def test_a_missing_store_directory_is_declined(tmp_path, monkeypatch):
    assert configure(monkeypatch, str(tmp_path / "nope" / "market_log.db")) is None


@pytest.mark.parametrize("bad", [None, "", "relative/path.db"])
def test_nothing_usable_means_no_opinion(bad, monkeypatch):
    assert configure(monkeypatch, bad) is None
