"""Where CFB data lives. Every path derives from `config.storage_path`.

Resolved at CALL time, not import time, so a test that repoints
`config.STORAGE_DIR` gets its own tree and never the production one.

    <STORAGE_DIR>/cfb.db                    the normalised store
    <STORAGE_DIR>/cfb/raw/<source>/...      verbatim downloads, manifested
    <STORAGE_DIR>/cfb/cache/                in-flight downloads (.part)
    <STORAGE_DIR>/cfb/checkpoints/          the single-instance lock

`cfb/raw` is deliberately NOT under the NFL logger's RAW_DIR. The logger's
startup audit adopts every file under its own raw tree and its rotation then
ships them to R2 - which is exactly what happened to the CFB probe's shards.
"""
import os

import config


def db_path() -> str:
    return config.storage_path("cfb.db")


def root(*parts) -> str:
    return config.storage_path("cfb", *parts)


def raw_root() -> str:
    return root("raw")


def cache_root() -> str:
    return root("cache")


def checkpoints_root() -> str:
    return root("checkpoints")


def ensure_dirs():
    for d in (raw_root(), cache_root(), checkpoints_root()):
        os.makedirs(d, exist_ok=True)
