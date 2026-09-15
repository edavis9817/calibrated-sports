"""Headshot URLs from the mirrored nflverse roster_weekly files - archive only.

    python -m jobs.ingest_headshots --season 2026
    python -m jobs.ingest_headshots --all

Reads roster_weekly_{season}.parquet from the raw archive (config.RAW_DIR) and
upserts every non-null (gsis_id, season, week, headshot_url) into
player_headshot. It makes NO network call: the logger's in-process nflverse
mirror is what downloads, and this job only re-derives from what it archived
(invariant 2). Only the URL is stored - never the image.

The file for a season is the newest data_version in nflverse_versions whose
rel_path exists under RAW_DIR; failing that, the newest date directory holding
roster_weekly_{season}.parquet.
"""
import argparse
import glob
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import store  # noqa: E402

SPORT = "nfl"
DATASET = "weekly_rosters"
_SEASON = re.compile(r"roster_weekly_(\d{4})\.parquet$")


def _ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def archived_seasons(raw_dir=None):
    raw_dir = raw_dir or config.RAW_DIR
    out = set()
    for p in glob.glob(os.path.join(raw_dir, "nflverse", "*", "roster_weekly_*.parquet")):
        m = _SEASON.search(p.replace("\\", "/"))
        if m:
            out.add(int(m.group(1)))
    return sorted(out)


def resolve_file(season, raw_dir=None, con=None):
    """-> (path, source_version) or (None, None). Archive only."""
    raw_dir = raw_dir or config.RAW_DIR
    try:
        con = con or _ro()
        rows = con.execute(
            "SELECT data_version, rel_path FROM nflverse_versions WHERE dataset = ? AND season = ? "
            "ORDER BY data_version DESC", (DATASET, season)).fetchall()
    except sqlite3.Error:
        rows = []
    for version, rel in rows:
        path = os.path.join(raw_dir, *rel.split("/"))
        if os.path.exists(path):
            return path, version
    found = sorted(glob.glob(os.path.join(raw_dir, "nflverse", "*", f"roster_weekly_{season}.parquet")))
    if not found:
        return None, None
    path = found[-1]
    return path, os.path.basename(os.path.dirname(path))


def read_rows(path):
    import polars as pl
    df = pl.read_parquet(path, columns=["gsis_id", "season", "week", "headshot_url"])
    df = df.filter(pl.col("gsis_id").is_not_null() & pl.col("headshot_url").is_not_null()
                   & (pl.col("headshot_url").str.strip_chars() != "")
                   & pl.col("season").is_not_null() & pl.col("week").is_not_null())
    return [(g, int(s), int(w), u.strip()) for g, s, w, u in df.iter_rows()]


def ingest(seasons, raw_dir=None, log=print):
    store.init_db()
    now = time.time()
    total, players, hosts, missing = 0, set(), Counter(), []
    for season in seasons:
        path, version = resolve_file(season, raw_dir)
        if path is None:
            missing.append(season)
            log(f"  {season}: no roster_weekly file in the archive - skipped")
            continue
        rows = read_rows(path)
        with store.db() as c:
            c.executemany(
                "INSERT INTO player_headshot (sport, gsis_id, season, week, headshot_url, "
                "source_version, ingested_ts) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(sport, gsis_id, season, week) DO UPDATE SET "
                "headshot_url = excluded.headshot_url, source_version = excluded.source_version, "
                "ingested_ts = excluded.ingested_ts",
                [(SPORT, g, s, w, u, version, now) for g, s, w, u in rows])
        total += len(rows)
        players.update(g for g, *_ in rows)
        hosts.update(urlparse(u).netloc for *_, u in rows)
        log(f"  {season}: {len(rows):,} rows from {os.path.relpath(path, raw_dir or config.RAW_DIR)}")
    return {"seasons": len(seasons) - len(missing), "missing_seasons": missing, "rows": total,
            "players": len(players), "hosts": dict(hosts.most_common())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--season", type=int)
    g.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    seasons = archived_seasons() if a.all else [a.season]
    s = ingest(seasons)
    print(f"seasons {s['seasons']} (missing {s['missing_seasons']}), rows {s['rows']:,}, "
          f"distinct players {s['players']:,}, hosts {s['hosts']}")
    return 0 if s["seasons"] else 1


if __name__ == "__main__":
    sys.exit(main())
