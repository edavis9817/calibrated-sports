"""Where the feeds store and its archive live. Resolved at CALL time, so a test that
repoints `config.STORAGE_DIR` gets its own tree.

    <STORAGE_DIR>/feeds.db                 the store
    <STORAGE_DIR>/feeds/raw/<source>/...   verbatim downloads, manifested
    <STORAGE_DIR>/feeds/cache/             in-flight (.part)
    <STORAGE_DIR>/feeds/checkpoints/       the single-instance lock

NOT under the logger's RAW_DIR, for the reason the CFB tree is not: the logger's startup
audit adopts everything under its own raw tree and rotation then ships it to R2.
"""
import os

import config


def db_path() -> str:
    return config.storage_path("feeds.db")


def root(*parts) -> str:
    return config.storage_path("feeds", *parts)


def raw_root() -> str:
    return root("raw")


def cache_root() -> str:
    return root("cache")


def checkpoints_root() -> str:
    return root("checkpoints")


def lock_path() -> str:
    return os.path.join(checkpoints_root(), "ingest_feeds.lock")


def ensure_dirs():
    for d in (raw_root(), cache_root(), checkpoints_root()):
        os.makedirs(d, exist_ok=True)
