"""Every store lives where config says. Run: pytest -q

THE BUG. `cfb_probe.py` set its database to `os.path.join("data", "cfb_probe.db")`
- a path literal, resolved against the process working directory. The repo lives
on C:, the data lives on D:, and the probe's raw archive correctly landed on D:
because it read `config.RAW_DIR`. So the probe half-inherited config and quietly
put a 4.3 GB database on the disk with 22 GB free.

Nothing failed. Nothing warned. The only symptom was a disk filling up, which is
the same shape as the logger's hardcoded log path before it was fixed.

STORAGE LOCATION COMES FROM CONFIG, NEVER FROM A PATH LITERAL. These tests read
the source rather than the runtime, because the failure is at import time and a
module that computes the wrong path never has to run to do damage.
"""
import ast
import os
import pathlib
import re

import pytest

import config

ROOT = pathlib.Path(__file__).resolve().parents[1]

# A few tests need the REAL store - the logger's database on its own disk - and
# CI has neither. Without LOGGER_DB, config.DB_PATH falls back to the relative
# "data/market_log.db", which resolves inside the checkout. Skipping is stated
# rather than worked around: these are the assertions CI does not cover.
NO_STORE = os.getenv("LOGGER_DB") is None
needs_store = pytest.mark.skipif(
    NO_STORE, reason="LOGGER_DB unset: no configured store, so config.DB_PATH is repo-relative"
)

# Files that legitimately name a directory: config itself defines the storage
# root, and the tests construct temporary ones.
EXEMPT = {"config.py"}
EXEMPT_DIRS = {"tests", ".venv", "node_modules", "out", ".next", "__pycache__"}

DB_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm")


def python_sources():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if set(rel.parts) & EXEMPT_DIRS or rel.name in EXEMPT:
            continue
        yield rel, p.read_text(encoding="utf-8")


def test_config_puts_storage_on_one_root():
    """The premise the rest of this file rests on: config names a single
    directory and everything derives from it."""
    db_dir = os.path.dirname(os.path.abspath(config.DB_PATH))
    raw_dir = os.path.dirname(os.path.abspath(config.RAW_DIR))
    assert db_dir == raw_dir, (
        f"the database ({db_dir}) and the raw archive ({raw_dir}) are on "
        f"different roots; a probe deriving from either would land somewhere "
        f"the other does not expect"
    )


def test_no_module_names_a_database_location_as_a_literal():
    """A .db literal that encodes a LOCATION - anything with a separator in it -
    is a store whose disk was chosen by whoever typed the string.

    A bare filename is fine and is the intended pattern: join it to a directory
    that came from config. It is the "data/" in front that decides the disk.
    """
    offenders = []
    for rel, src in python_sources():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if (v.lower().endswith(DB_SUFFIXES)
                        and len(v) > len(".db")
                        and ("/" in v or "\\" in v)):
                    offenders.append(f"{rel}:{node.lineno} {v!r}")
    assert not offenders, (
        "database paths must be derived from config, not written as literals:\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_joins_a_bare_data_directory():
    """`os.path.join("data", ...)` is the exact shape of the cfb_probe bug: it
    resolves against the working directory, so it silently means the repo.

    Matched on the AST, not on the text. A comment explaining the bug quotes the
    broken line by design, and a text search cannot tell that from the bug.
    """
    offenders = []
    for rel, src in python_sources():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = (
                f"{getattr(getattr(f, 'value', None), 'attr', '')}."
                f"{getattr(f, 'attr', '')}"
            )
            if name != "path.join":
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value == "data":
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        'os.path.join("data", ...) resolves against the working directory, not '
        "the configured store:\n  " + "\n  ".join(offenders)
    )


def test_the_cfb_probe_derives_its_database_from_config():
    """The specific regression, named. The probe is disposable; the rule is not,
    and the next probe will be written by copying this one."""
    import cfb_probe

    storage = os.path.dirname(os.path.abspath(config.DB_PATH))
    assert os.path.dirname(os.path.abspath(cfb_probe.CFB_DB)) == storage
    assert os.path.isabs(cfb_probe.CFB_DB), "an absolute path cannot follow the cwd"
    assert cfb_probe.CFB_DB != config.DB_PATH, "the probe must not share the logger's file"


@needs_store
def test_the_probe_does_not_write_its_database_into_the_repo():
    """Stated as the consequence rather than the mechanism, because the
    consequence is what anyone will actually notice.

    Needs a configured store: with LOGGER_DB unset the fallback IS repo-relative,
    so this would fail on its own default rather than on a regression. The
    derivation test above still runs everywhere, and that is the part that
    catches a path literal creeping back in.
    """
    import cfb_probe

    repo = str(ROOT).lower()
    assert not str(os.path.abspath(cfb_probe.CFB_DB)).lower().startswith(repo), (
        f"the probe database sits inside the repo at {cfb_probe.CFB_DB}; "
        f"project data belongs under the configured store"
    )


@pytest.mark.parametrize("attr", ["DB_PATH", "RAW_DIR"])
def test_config_storage_settings_are_environment_overridable(attr):
    """So moving the store is an env change, not a code change. If this ever
    stops holding, the next move will be another round of literals."""
    src = (ROOT / "config.py").read_text(encoding="utf-8")
    m = re.search(rf"^{attr}\s*=\s*(.+)$", src, re.M)
    assert m, f"{attr} not found in config.py"
    assert "getenv" in m.group(1) or "environ" in m.group(1), (
        f"{attr} is not environment-overridable: {m.group(1).strip()}"
    )
