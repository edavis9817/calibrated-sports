"""Where the MLB store and its archive live. Resolved at CALL time, so a test that
repoints `config.STORAGE_DIR` gets its own tree.

    <STORAGE_DIR>/mlb.db                  the store
    <STORAGE_DIR>/mlb/raw/<source>/...    verbatim downloads, manifested
    <STORAGE_DIR>/mlb/checkpoints/        the single-instance lock
    <STORAGE_DIR>/mlb/web_export/         the local-only contract probe (never uploaded)

NOT under the logger's RAW_DIR, for the reason the CFB and feeds trees are not: the
logger's startup audit adopts everything under its own raw tree and rotation then ships
it to R2.
"""
import os

import config


def db_path() -> str:
    return config.storage_path("mlb.db")


def root(*parts) -> str:
    return config.storage_path("mlb", *parts)


def raw_root() -> str:
    return root("raw")


def checkpoints_root() -> str:
    return root("checkpoints")


def lock_path() -> str:
    return os.path.join(checkpoints_root(), "ingest_mlb.lock")


def ensure_dirs():
    for d in (raw_root(), checkpoints_root()):
        os.makedirs(d, exist_ok=True)
