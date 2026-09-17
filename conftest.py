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
import os

import config as cs_config

# pytest DELETES AND RECREATES basetemp at session start, so this must be a
# dedicated subdirectory and must never be the store root. The name is verified
# below rather than trusted: pointing basetemp at the store itself would wipe
# the logger's database.
BASETEMP_NAME = "pytest-tmp"


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

    basetemp = os.path.join(root, BASETEMP_NAME)
    # Belt and braces. pytest removes this tree; refuse anything that is not the
    # dedicated subdirectory built just above.
    if os.path.basename(basetemp) != BASETEMP_NAME or os.path.abspath(basetemp) == root:
        return
    os.makedirs(basetemp, exist_ok=True)
    config.option.basetemp = basetemp
