"""Where track F reads from and writes to. No path literal leaves this module.

READS: the nflverse mirror under `<RAW_DIR>/nflverse/<pull-date>/`, written by
`jobs.ingest_nflverse` via `store.archive_file`. Partitioned by PULL DATE, not
by season, because nflverse applies stat corrections for weeks and the pull date
IS the data version that makes a correction legible. So "the 2015 play-by-play"
is not one path - it is the newest pull that carries a 2015 file, and
`latest_asset` is the only thing allowed to decide which.

WRITES: `<STORAGE_DIR>/analytics.db`, this track's own database, resolved
through `config.storage_path`.

`market_log.db` IS THE LIVE LOGGER'S AND IS OPENED READ-ONLY. SQLite serialises
writers, so a bulk play-by-play write holding the WAL writer lock makes the
logger block for up to its 30s busy timeout and drop quotes. `market_log_ro()`
returns a connection over a `file:...?mode=ro` URI, which fails at CONNECT time
on any attempted write rather than at the point of damage.
"""
import os
import re
import sqlite3

import config

ANALYTICS_DB = "analytics.db"
NFLVERSE_SOURCE = "nflverse"
PBP = "play_by_play_{season}.parquet"

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def db_path() -> str:
    """This track's database. Never `data/analytics.db`, never a literal."""
    return config.storage_path(ANALYTICS_DB)


def connect(read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(_uri(db_path()) + "?mode=ro", uri=True, timeout=30)
    c = sqlite3.connect(db_path(), timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def _uri(path: str) -> str:
    """A SQLite file: URI. Backslashes are not URI separators and a Windows
    path pasted in raw opens a database named after the whole string."""
    return "file:" + os.path.abspath(path).replace("\\", "/")


def market_log_path() -> str:
    """The LIVE logger's database. Same two-candidate rule as `mirror_root`:
    this clone's `config.DB_PATH` is deliberately an inert file so that nothing
    here can be configured into the logger's store by accident."""
    for p in (os.path.abspath(config.DB_PATH), config.storage_path("market_log.db")):
        if os.path.exists(p):
            return p
    raise FileNotFoundError("no logger database at " + config.storage_path("market_log.db"))


def market_log_ro() -> sqlite3.Connection:
    """The logger's database, READ ONLY. The only way this package may open it.

    `mode=ro` refuses at CONNECT, not at the first write, so a mistake is a
    stack trace in a track F script rather than the live logger dropping quotes
    while it waits out a 30-second busy timeout. The timeout is short for the
    same reason: if the logger is mid-write, back off rather than queue.
    """
    return sqlite3.connect(_uri(market_log_path()) + "?mode=ro", uri=True, timeout=5)


def mirror_root() -> str:
    """The raw archive root that actually holds the nflverse mirror.

    Two candidates, checked in order, and a loud failure rather than a guess.

    `config.RAW_DIR` is the first because that is where `store.archive_file`
    writes. It is not the only one because THIS CLONE POINTS RAW_DIR AT AN
    INERT DIRECTORY ON PURPOSE - track F must never be able to write into the
    live logger's archive, and the cheapest guarantee of that is that its
    configured archive is somewhere else. The mirror it READS still lives at
    the store root, which `test_storage_paths` pins to the same root as the
    database, so `storage_path("raw")` finds it without a path literal.

    Raising names both candidates. A silent fall-through to an empty directory
    is the "exit 0 is not a result" failure: a survey over zero seasons would
    report nothing and succeed.
    """
    candidates = [os.path.abspath(config.RAW_DIR), config.storage_path("raw")]
    seen = []
    for root in candidates:
        if root in seen:
            continue
        seen.append(root)
        if os.path.isdir(os.path.join(root, NFLVERSE_SOURCE)):
            return root
    raise FileNotFoundError(
        "no nflverse mirror under any configured archive root: "
        + ", ".join(seen))


def archive_root(source: str = NFLVERSE_SOURCE) -> str:
    return os.path.join(mirror_root(), source)


def pull_days(source: str = NFLVERSE_SOURCE) -> list:
    """Every pull date in the mirror, oldest first."""
    root = archive_root(source)
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if _DAY.match(d) and os.path.isdir(os.path.join(root, d)))


def latest_asset(asset: str, source: str = NFLVERSE_SOURCE):
    """(path, pull_date) for the newest pull carrying `asset`, or None.

    Newest wins because a later pull is a stat correction applied, not a
    different dataset. The pull date is returned with it so every derived row
    can record which version of the facts it was built from - the corrections
    are real and a figure that cannot name its data version cannot be
    reproduced once upstream moves again.
    """
    for day in reversed(pull_days(source)):
        p = os.path.join(archive_root(source), day, asset)
        if os.path.exists(p):
            return p, day
    return None


def pbp_files() -> list:
    """[(season, path, pull_date)] for every season in the mirror, ascending."""
    seen = {}
    for day in pull_days():
        d = os.path.join(archive_root(), day)
        for name in os.listdir(d):
            m = re.match(r"^play_by_play_(\d{4})\.parquet$", name)
            if m:
                seen[int(m.group(1))] = (os.path.join(d, name), day)
    return [(s, *seen[s]) for s in sorted(seen)]
