"""W07 TRACK C PHASE 1 - college football facts from sportsdataverse releases.

    python -m jobs.ingest_cfb                                   # status, 0 requests
    python -m jobs.ingest_cfb --fetch --season 2026             # weekly refresh
    python -m jobs.ingest_cfb --fetch --season 2004-2026        # backfill
    python -m jobs.ingest_cfb --fetch --dataset player_box --season 2025
    python -m jobs.ingest_cfb --parse                           # re-parse archive, 0 requests
    python -m jobs.ingest_cfb --rebuild --dataset player_box --season 2025
    python -m jobs.ingest_cfb --audit                           # manifest vs disk

CFB SHIPS STATS AND USAGE, NOT HIT RATES. No public source says whether a
college player appeared in a game, so nothing here settles, voids or computes a
rate. `cfb.limitations` records why, as data.

THE SHAPE OF A RUN
    plan (dataset x season, finite, from cfb.sources)
      -> one release listing per tag            (GitHub REST, 60/hour)
      -> per asset: same size+updated_at as the last check? skip, no download
      -> download to cache/*.part, size-checked, hashed
      -> same bytes or same CONTENT as the newest raw copy? record the check, discard
      -> otherwise move into raw/, manifest it                (raw first)
      -> parse every unparsed raw file in fetch order, diffed row by row

Every step commits per file, so a run killed at any point resumes where it
stopped. One instance at a time, by an OS lock. A 403 or 429 stops the run.

Writes ONLY `cfb.db` and `<STORAGE_DIR>/cfb/`. Never opens the logger's
database, never writes under the logger's RAW_DIR, imports no NFL job.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl

from cfb import fetch, limitations, normalize, paths, schema, sources, versioning
from cfb.lock import AlreadyRunning, InstanceLock

DEFAULT_MAX_FILES = 200


def connect(db_path=None):
    db = db_path or paths.db_path()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    c = sqlite3.connect(db, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(schema.ddl())
    limitations.record(c)
    c.commit()
    return c


def parse_seasons(spec):
    if spec in (None, "", "all"):
        return None
    out = set()
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _source(repo):
    return repo.split("/")[0]


def raw_rel_path(d, season, fetched_ts, content_sha):
    stem = d.asset_name(season).rsplit(".", 1)[0]
    return "/".join([_source(d.repo), d.tag, stem,
                     f"{_utc(fetched_ts)}-{content_sha[:12]}.parquet"])


def _check(conn, d, season, asset, remote, bsha, csha, outcome, detail=None):
    conn.execute("INSERT INTO cfb_fetch_checks VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (time.time(), d.name, season, asset,
                  remote and remote["size"], remote and remote["updated_at"],
                  bsha, csha, outcome, detail))
    conn.commit()


def newest_raw(conn, dataset, season):
    return conn.execute(
        "SELECT rel_path, bytes_sha256, content_sha256, fetched_ts FROM cfb_raw_files "
        "WHERE dataset=? AND season IS ? ORDER BY fetched_ts DESC LIMIT 1",
        (dataset, season)).fetchone()


# =============================================================================
# fetch
# =============================================================================

def fetch_one(conn, client, d, season):
    """Returns (outcome, downloaded?)."""
    name = d.asset_name(season)
    remote = client.release_assets(d.repo, d.tag).get(name)
    if remote is None:
        _check(conn, d, season, name, None, None, None, "absent")
        return "absent", False

    last_check = conn.execute(
        "SELECT remote_size, remote_updated_at FROM cfb_fetch_checks "
        "WHERE dataset=? AND season IS ? AND outcome IN "
        "('new','unchanged_bytes','unchanged_content') ORDER BY ts DESC LIMIT 1",
        (d.name, season)).fetchone()
    newest = newest_raw(conn, d.name, season)
    if newest and last_check and last_check == (remote["size"], remote["updated_at"]):
        _check(conn, d, season, name, remote, None, None, "skipped_same_remote")
        return "skipped_same_remote", False

    part = os.path.join(paths.cache_root(), f"{d.tag}__{name}.part")
    fetched_ts = time.time()
    try:
        nbytes, bsha = client.download(remote["url"], part, remote["size"])
        if newest and newest[1] == bsha:
            _check(conn, d, season, name, remote, bsha, newest[2], "unchanged_bytes")
            return "unchanged_bytes", True
        try:
            csha = versioning.content_sha256(pl.read_parquet(part))
        except Exception as e:
            _check(conn, d, season, name, remote, bsha, None, "unreadable",
                   f"{type(e).__name__}: {e}")
            return "unreadable", True
        if newest and newest[2] == csha:
            _check(conn, d, season, name, remote, bsha, csha, "unchanged_content")
            return "unchanged_content", True

        rel = raw_rel_path(d, season, fetched_ts, csha)
        dest = os.path.join(paths.raw_root(), *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(part, dest)
        conn.execute(
            "INSERT INTO cfb_raw_files (rel_path, dataset, season, repo, tag, asset, bytes, "
            "bytes_sha256, content_sha256, remote_updated_at, fetched_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rel, d.name, season, d.repo, d.tag, name, nbytes, bsha, csha,
             remote["updated_at"], fetched_ts))
        conn.commit()
        _check(conn, d, season, name, remote, bsha, csha, "new", rel)
        return "new", True
    finally:
        if os.path.exists(part):
            os.remove(part)


def run_fetch(conn, plan, max_files=DEFAULT_MAX_FILES, client=None):
    client = client or fetch.Client(conn)
    counts = {}
    downloads = 0
    for d, season in plan:
        if downloads >= max_files:
            counts["deferred_by_max_files"] = counts.get("deferred_by_max_files", 0) + 1
            continue
        try:
            outcome, downloaded = fetch_one(conn, client, d, season)
        except fetch.RateLimited as e:
            print(f"  STOPPED: {e}")
            counts["stopped_rate_limited"] = 1
            break
        downloads += int(downloaded)
        counts[outcome] = counts.get(outcome, 0) + 1
        print(f"  {d.name:<13} {season if season is not None else '-':<5} {outcome}")
    return counts


# =============================================================================
# parse
# =============================================================================

def _parsed(conn, rel_path):
    return conn.execute("SELECT 1 FROM cfb_parse_log WHERE rel_path=? AND status='ok'",
                        (rel_path,)).fetchone() is not None


def parse_file(conn, d, season, file_id, rel_path, fetched_ts):
    path = os.path.join(paths.raw_root(), *rel_path.split("/"))
    df = pl.read_parquet(path)
    norm = normalize.NORMALIZERS[d.name](df, season)
    assert norm.table == d.table, (norm.table, d.table)
    try:
        conn.execute("BEGIN")
        ins, closed, same = versioning.apply(conn, d.name, season, file_id, fetched_ts,
                                             d.table, norm.rows, label=rel_path)
        mseason = season if season is not None else 0
        conn.executemany(
            "INSERT INTO cfb_measurements (key, season, value, detail, src_file, measured_ts) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(key, season) DO UPDATE SET value=excluded.value, "
            "detail=excluded.detail, src_file=excluded.src_file, measured_ts=excluded.measured_ts",
            [(k, mseason, v, det, rel_path, time.time()) for k, v, det in norm.measurements])
        conn.execute("INSERT INTO cfb_parse_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), rel_path, d.name, season, fetched_ts, len(norm.rows),
                      ins, closed, same, json.dumps(norm.dropped), "ok", None))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(norm.rows), ins, closed, same, norm.dropped


def run_parse(conn, plan, rebuild=False):
    totals = {"files": 0, "rows": 0, "inserted": 0, "closed": 0, "unchanged": 0,
              "refused": 0, "dropped": {}}
    for d, season in plan:
        if rebuild:
            conn.execute("BEGIN")
            versioning.rebuild_scope(conn, d.table, d.name, season)
            conn.execute("UPDATE cfb_parse_log SET status='rebuilt_over' "
                         "WHERE dataset=? AND season IS ? AND status='ok'", (d.name, season))
            conn.commit()
        files = conn.execute(
            "SELECT file_id, rel_path, fetched_ts FROM cfb_raw_files WHERE dataset=? "
            "AND season IS ? ORDER BY fetched_ts", (d.name, season)).fetchall()
        for fid, rel, fts in files:
            if _parsed(conn, rel):
                continue
            try:
                n, ins, closed, same, dropped = parse_file(conn, d, season, fid, rel, fts)
            except (normalize.RefusedFile, versioning.OutOfOrder) as e:
                conn.execute("INSERT INTO cfb_parse_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (time.time(), rel, d.name, season, fts, None, None, None, None,
                              None, "refused", f"{type(e).__name__}: {e}"))
                conn.commit()
                totals["refused"] += 1
                print(f"  REFUSED {rel}: {e}")
                continue
            totals["files"] += 1
            totals["rows"] += n
            totals["inserted"] += ins
            totals["closed"] += closed
            totals["unchanged"] += same
            for k, v in dropped.items():
                totals["dropped"][f"{d.name}.{k}"] = totals["dropped"].get(f"{d.name}.{k}", 0) + v
            print(f"  parsed {d.name:<13} {season if season is not None else '-':<5} "
                  f"rows {n:>7,}  +{ins:,} -{closed:,} ={same:,}  dropped {dropped or 0}")
    return totals


def measure_joins(conn):
    """Measurements that span two datasets, recomputed from the current rows.

    A game names two team ids; the teams file for that season should carry
    both. In 2005 it is missing 64 of the 298 the games reference.
    """
    rows = conn.execute("""
        WITH ids AS (
            SELECT season, home_id AS id FROM cfb_games WHERE valid_to_ts IS NULL
            UNION SELECT season, away_id FROM cfb_games WHERE valid_to_ts IS NULL)
        SELECT i.season, SUM(t.team_id IS NULL), COUNT(*)
        FROM ids i LEFT JOIN cfb_teams t
          ON t.season = i.season AND t.team_id = i.id AND t.valid_to_ts IS NULL
        WHERE i.id IS NOT NULL GROUP BY i.season""").fetchall()
    conn.executemany(
        "INSERT INTO cfb_measurements (key, season, value, detail, src_file, measured_ts) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(key, season) DO UPDATE SET value=excluded.value, "
        "detail=excluded.detail, src_file=excluded.src_file, measured_ts=excluded.measured_ts",
        [("games.team_ids_without_team_row", s, missing, f"of {total} team ids in games",
          "join:cfb_games x cfb_teams", time.time()) for s, missing, total in rows])
    conn.commit()
    return rows


# =============================================================================
# audit and status
# =============================================================================

def audit(conn):
    """Every raw file manifested, every manifested file present and readable."""
    root = paths.raw_root()
    on_disk = set()
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                on_disk.add(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    manifest = {r[0] for r in conn.execute("SELECT rel_path FROM cfb_raw_files")}
    unregistered = sorted(on_disk - manifest)
    missing = sorted(manifest - on_disk)
    unreadable = []
    for rel in sorted(manifest & on_disk):
        try:
            pl.read_parquet_schema(os.path.join(root, *rel.split("/")))
        except Exception as e:
            unreadable.append((rel, f"{type(e).__name__}: {e}"))
    print(f"CFB raw audit  root={root}")
    print(f"  on disk {len(on_disk)}  manifested {len(manifest)}")
    print(f"  unregistered {len(unregistered)}  missing {len(missing)}  unreadable {len(unreadable)}")
    for x in unregistered[:20]:
        print(f"    UNREGISTERED {x}")
    for x in missing[:20]:
        print(f"    MISSING {x}")
    for x, why in unreadable[:20]:
        print(f"    UNREADABLE {x}: {why}")
    return not (unregistered or missing or unreadable)


def status(conn):
    print(f"CFB store  db={paths.db_path()}")
    print(f"           raw={paths.raw_root()}")
    print(f"\n  {'dataset':<13} {'raw files':>9} {'seasons':>8} {'current rows':>13}  newest fetch")
    for name, d in sources.DATASETS.items():
        n, seasons, newest = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT season), MAX(fetched_ts) FROM cfb_raw_files "
            "WHERE dataset=?", (name,)).fetchone()
        rows = conn.execute(f"SELECT COUNT(*) FROM {d.table} WHERE valid_to_ts IS NULL").fetchone()[0]
        when = datetime.fromtimestamp(newest, timezone.utc).strftime("%Y-%m-%d %H:%MZ") if newest else "-"
        print(f"  {name:<13} {n:>9} {seasons:>8} {rows:>13,}  {when}")
    r = conn.execute("SELECT ratelimit_remaining, ts FROM cfb_http_log "
                     "WHERE ratelimit_remaining IS NOT NULL ORDER BY ts DESC LIMIT 1").fetchone()
    print(f"\n  GitHub API remaining at last listing: {r[0] if r else 'never listed'}")
    outcomes = conn.execute("SELECT outcome, COUNT(*) FROM cfb_fetch_checks GROUP BY 1").fetchall()
    print(f"  fetch checks: {dict(outcomes) or 0}")
    refused = conn.execute("SELECT COUNT(*) FROM cfb_parse_log WHERE status='refused'").fetchone()[0]
    print(f"  refused parses: {refused}")
    print("\n  limitations recorded:")
    for lid, title in conn.execute("SELECT id, title FROM cfb_limitations ORDER BY id"):
        print(f"    {lid:<36} {title}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="download changed assets, then parse")
    ap.add_argument("--parse", action="store_true", help="parse the archive only, 0 requests")
    ap.add_argument("--rebuild", action="store_true",
                    help="delete the scope's rows and replay the archive in order, 0 requests")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--dataset", action="append", choices=sorted(sources.DATASETS))
    ap.add_argument("--season", help="2026 | 2004-2026 | 2019,2021 | all (default all)")
    ap.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    a = ap.parse_args(argv)

    paths.ensure_dirs()
    plan = sources.plan(a.dataset, parse_seasons(a.season))

    if not (a.fetch or a.parse or a.rebuild or a.audit):
        conn = connect()
        status(conn)
        return 0

    try:
        with InstanceLock(os.path.join(paths.checkpoints_root(), "ingest_cfb.lock")):
            conn = connect()
            if a.audit:
                return 0 if audit(conn) else 1
            print(f"plan: {len(plan)} (dataset, season) pairs")
            if a.fetch:
                counts = run_fetch(conn, plan, a.max_files)
                print(f"fetch: {counts}")
            totals = run_parse(conn, plan, rebuild=a.rebuild)
            measure_joins(conn)
            print(f"parse: files {totals['files']}  rows {totals['rows']:,}  "
                  f"+{totals['inserted']:,} -{totals['closed']:,} ={totals['unchanged']:,}  "
                  f"refused {totals['refused']}")
            print(f"dropped: {totals['dropped'] or 0}")
            return 0
    except AlreadyRunning as e:
        print(f"REFUSING: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
