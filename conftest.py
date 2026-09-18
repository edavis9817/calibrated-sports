"""Session-wide pytest configuration.

WHY THIS EXISTS: THE SUITE WAS NOT HERMETIC WITH RESPECT TO FREE DISK SPACE.

`store.disk_headroom_ok()` refuses to archive below `DISK_MIN_FREE_GB` (5 GB),
and it measures the drive holding `config.RAW_DIR`. The `env` fixtures point
`RAW_DIR` at pytest's `tmp_path`, which lives under the OS temp directory. So
the suite silently inherits the free space of whichever drive hosts temp.

On 2026-09-17 that drive fell to 3.6 GB and 15 tests in `test_nflverse.py` and
`test_retention.py` failed with `OSError: disk below 5GB floor` - tests that
assert on archiving, failing because the guard they exercise was correctly
refusing. Nothing was wrong with the code. It cost a full diagnostic cycle: a
bisect against a pristine clone of HEAD to prove the failures predated the
working-tree changes, then a re-run with `--basetemp` to prove the drive was the
cause. CI never sees this - ubuntu runners have room - so only a dev box pays.

THE FIX: when a store is configured, put pytest's temp inside it. The store is
on the data disk by construction (`test_storage_paths.py` asserts the database
and the raw archive share one root), which is the disk with room on it.

DERIVED FROM `config`, NOT FROM `os.getenv`. The first version of this file read
`os.getenv("LOGGER_DB")` and would have done NOTHING on the only machine it
exists to protect: on the dev box `LOGGER_DB` lives in `.env` and reaches the
process through python-dotenv INSIDE `config`, not as an exported environment
variable. A fix that silently no-ops on its target is the "exit 0 is not a
result" failure in another costume.

"Configured" is therefore tested as: `config.DB_PATH` is ABSOLUTE. Unset, it
falls back to the relative `data/market_log.db`, which resolves inside the
checkout - that is CI's case and pytest's own default is right there.
"""
import itertools
import os

import config as cs_config

# Monotonic within the process; `os.getpid()` carries uniqueness across them.
# See `pytest_configure` for why this is not a timestamp.
_RUN_SEQ = itertools.count(1)

# pytest DELETES AND RECREATES basetemp at session start, so this must be a
# dedicated subdirectory and must never be the store root. The name is verified
# below rather than trusted: pointing basetemp at the store itself would wipe
# the logger's database.
BASETEMP_NAME = "pytest-tmp"

# EACH RUN GETS ITS OWN SUBDIRECTORY, AND OLD ONES ARE SWEPT BEST-EFFORT.
#
# One shared basetemp does not survive this store. pytest deletes and recreates
# it at session start, but on Windows a directory cannot be unlinked while any
# process holds a handle inside it - and on this box background exports run
# against the same drive for minutes at a time. The delete then fails with
# `WinError 145: The directory is not empty`, the recreate fails with
# `WinError 183: Cannot create a file when that file already exists`, and every
# `tmp_path` test ERRORS AT SETUP.
#
# Measured 2026-09-18: 8 errors in `test_web_contract.py` in the working tree
# while a clean clone of the same commit passed 23/23 and the full suite passed
# 1186. That combination reads exactly like a real regression in whatever
# landed most recently, and the next person to see it may be another track.
# Diagnosing it once and leaving the cause in place guarantees the second
# diagnosis.
KEEP_RUNS = 3


def _sweep(parent, keep=KEEP_RUNS):
    """Drop all but the newest `keep` run directories. Never raises.

    Best-effort ON PURPOSE: a locked leftover is the normal case here, and a
    housekeeping failure must not fail the suite it is housekeeping for. The
    cost of a survivor is one stale directory; the cost of raising is the run.
    """
    import shutil
    try:
        runs = sorted(
            (e.path for e in os.scandir(parent) if e.is_dir()),
            key=os.path.getmtime, reverse=True)
    except OSError:
        return
    for path in runs[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def pytest_configure(config):
    if config.getoption("basetemp", None):
        return                          # an explicit --basetemp always wins

    db = getattr(cs_config, "DB_PATH", None)
    # Relative means unconfigured (CI, or a fresh clone): the fallback resolves
    # inside the checkout and pytest's default temp is correct there.
    if not db or not os.path.isabs(db):
        return

    root = os.path.dirname(os.path.abspath(db))
    if not os.path.isdir(root):
        return
    # Never write into the checkout, even if someone points the store at it.
    #
    # `commonpath` RAISES on Windows when the two paths are on different drives,
    # and that is the NORMAL case here: the checkout is on C: and the store on
    # D:. The first version let that propagate out of `pytest_configure`, which
    # is an INTERNALERROR that kills the whole run - a "fix" that broke every
    # local invocation of the suite it was written to protect. Different drives
    # cannot nest, so the answer there is simply False.
    repo = os.path.dirname(os.path.abspath(__file__))
    try:
        inside_checkout = os.path.commonpath([root, repo]) == repo
    except ValueError:
        inside_checkout = False
    if inside_checkout:
        return

    parent = os.path.join(root, BASETEMP_NAME)
    # Belt and braces, and the check is on the PARENT now that each run gets its
    # own child. pytest removes the tree it is handed, so this must never be the
    # store root: pointing basetemp there would wipe the logger's database.
    if os.path.basename(parent) != BASETEMP_NAME or os.path.abspath(parent) == root:
        return
    os.makedirs(parent, exist_ok=True)

    # Sweep BEFORE handing pytest a path: it deletes and recreates whatever it is
    # given, and the whole point is that it is never given a directory another
    # process is holding open.
    _sweep(parent)

    # pid + a COUNTER, not a clock.
    #
    # The first version used `int(time.time() * 1000) % 1_000_000`, and its own
    # test caught it: two calls inside one test body land in the same
    # millisecond and produce the SAME directory. It passed in isolation and
    # failed in the full suite - a flaky test over a genuinely unsafe scheme,
    # which is worse than either alone, because the flake invites re-running
    # until it goes green.
    #
    # A clock cannot promise uniqueness at any resolution; a counter can, within
    # a process. `pid` supplies the across-process half, so the pair cannot
    # repeat on this machine while a run is alive.
    run = "%d-%d" % (os.getpid(), next(_RUN_SEQ))
    basetemp = os.path.join(parent, run)
    os.makedirs(basetemp, exist_ok=True)
    config.option.basetemp = basetemp
