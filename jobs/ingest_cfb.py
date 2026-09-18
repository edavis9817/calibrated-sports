"""W07 TRACK C PHASE 1 - college football facts from sportsdataverse releases.

    python -m jobs.ingest_cfb                                   # status, 0 requests
    python -m jobs.ingest_cfb --fetch --season 2026             # weekly refresh
    python -m jobs.ingest_cfb --fetch --season 2004-2026        # backfill
    python -m jobs.ingest_cfb --fetch --dataset player_box --season 2025
    python -m jobs.ingest_cfb --parse                           # re-parse archive, 0 requests
    python -m jobs.ingest_cfb --rebuild --dataset player_box --season 2025
    python -m jobs.ingest_cfb --audit                           # manifest vs disk

    python -m jobs.ingest_cfb --cfbd-status                     # /info (unmetered) + ledger
    python -m jobs.ingest_cfb --cfbd-lines 2013-2025            # 13 metered requests
    python -m jobs.ingest_cfb --cfbd-week 2026:3                # 2 metered requests
    python -m jobs.ingest_cfb --cfbd-rankings 2026:3 | latest   # 1 metered request (polls)
    python -m jobs.ingest_cfb --fetch --season 2026 --cfbd-week latest   # the weekly refresh
    python -m jobs.ingest_cfb --promote-probe                   # exchange probe -> cfb.db, 0 requests
    python -m jobs.ingest_cfb --odds-free                       # Odds API /sports + /events, 0 credits
    python -m jobs.ingest_cfb --odds-p1 --lock-wait 900         # P1: 1 credit/event, 74 lifetime, Sat 09-19
    python -m jobs.ingest_cfb --odds-forward                    # one tick; 3 credits per kickoff hour, 75/week
    python -m jobs.ingest_cfb --odds-week                       # this week's kickoff hours and captures, 0 requests
    python -m jobs.ingest_cfb --odds-reparse                    # archive -> observation tables, 0 requests

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
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl

import config
from cfb import (cfbd, cfbd_normalize, fetch, limitations, normalize, oddsapi, oddsapi_capture,
                 paths, probe_promote, schema, sources, versioning)
from core.single_instance import AlreadyRunning, InstanceLock   # C1: one lock, track A's

DEFAULT_MAX_FILES = 200


def connect(db_path=None):
    db = db_path or paths.db_path()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    c = sqlite3.connect(db, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(schema.ddl())
    schema.migrate(c)
    c.executescript(schema.index_ddl())
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

    # A poll row names a team id; the teams file for that season should carry it.
    # CFBD's `teamId` is the ESPN id, so this is a join on ids, not on names - and
    # this measurement is what would catch that ceasing to be true.
    ranks = conn.execute("""
        SELECT r.season, SUM(t.team_id IS NULL), COUNT(*)
        FROM cfb_rankings r LEFT JOIN cfb_teams t
          ON t.season = r.season AND t.team_id = r.team_id AND t.valid_to_ts IS NULL
        WHERE r.valid_to_ts IS NULL GROUP BY r.season""").fetchall()
    conn.executemany(
        "INSERT INTO cfb_measurements (key, season, value, detail, src_file, measured_ts) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(key, season) DO UPDATE SET value=excluded.value, "
        "detail=excluded.detail, src_file=excluded.src_file, measured_ts=excluded.measured_ts",
        [("rankings.team_ids_without_team_row", s, missing, f"of {total} poll rows",
          "join:cfb_rankings x cfb_teams", time.time()) for s, missing, total in ranks])

    # A provider spelled two ways ACROSS files is invisible to any one parse.
    names = [p for (p,) in conn.execute(
        "SELECT DISTINCT provider FROM cfb_game_lines WHERE valid_to_ts IS NULL")]
    folds = {}
    for p in names:
        folds.setdefault(cfbd_normalize.provider_fold(p), []).append(p)
    splits = {k: sorted(v) for k, v in folds.items() if len(v) > 1}
    conn.execute(
        "INSERT INTO cfb_measurements (key, season, value, detail, src_file, measured_ts) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(key, season) DO UPDATE SET value=excluded.value, "
        "detail=excluded.detail, src_file=excluded.src_file, measured_ts=excluded.measured_ts",
        ("cfbd_lines.provider_name_splits", 0, len(splits), json.dumps(splits) if splits else None,
         "store:cfb_game_lines", time.time()))
    if splits:
        print(f"  WARNING: provider names split across files: {splits} - "
              f"add them to PROVIDER_CANONICAL and --rebuild")
    conn.commit()
    return rows


# =============================================================================
# CFBD (phase 2) - metered; see cfb/cfbd.py for the guards
# =============================================================================

def _part_dir(part):
    return part.replace(":", "_")


def archive_cfbd(conn, req, body: bytes, fetched_ts, source="cfbd",
                 repo="collegefootballdata.com", dedupe=True):
    """Raw first: the response bytes, gzipped verbatim, manifested. A copy is kept
    only when the CONTENT differs from the newest copy for this exact scope, unless
    `dedupe=False` - every PAID Odds API response is kept, because the instant it
    was fetched is part of what was bought.
    `source`/`repo` let the Odds API archive through the same path.
    Returns (file_id or None, outcome)."""
    bsha = hashlib.sha256(body).hexdigest()
    try:
        csha = hashlib.sha256(json.dumps(json.loads(body), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
    except ValueError:
        csha = bsha                      # kept verbatim; the parse will refuse it
    newest = conn.execute(
        "SELECT file_id, bytes_sha256, content_sha256 FROM cfb_raw_files WHERE dataset=? "
        "AND season=? AND asset=? ORDER BY fetched_ts DESC LIMIT 1",
        (req.dataset, req.season, req.part)).fetchone()
    if dedupe and newest and newest[2] == csha:
        return newest[0], "unchanged_content"

    stem = "/".join([source, req.endpoint, str(req.season), _part_dir(req.part),
                     f"{_utc(fetched_ts)}-{csha[:12]}"])
    rel, n = f"{stem}.json.gz", 1
    # Never overwrite: identical content fetched in the same second (possible once
    # paid responses skip dedupe) gets a numbered sibling, checked BEFORE any write.
    while (os.path.exists(os.path.join(paths.raw_root(), *rel.split("/")))
           or conn.execute("SELECT 1 FROM cfb_raw_files WHERE rel_path=?", (rel,)).fetchone()):
        n += 1
        rel = f"{stem}-{n}.json.gz"
    dest = os.path.join(paths.raw_root(), *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest + ".part", "wb") as f:
        f.write(gzip.compress(body))
    os.replace(dest + ".part", dest)
    cur = conn.execute(
        "INSERT INTO cfb_raw_files (rel_path, dataset, season, repo, tag, asset, bytes, "
        "bytes_sha256, content_sha256, remote_updated_at, fetched_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (rel, req.dataset, req.season, repo, req.endpoint, req.part,
         len(body), bsha, csha, None, fetched_ts))
    conn.commit()
    return cur.lastrowid, "new"


def run_cfbd(conn, requests, client=None, max_requests=cfbd.MAX_REQUESTS_PER_RUN):
    """Refuse before spending, spend at most the plan, stop at the reserve.
    Returns counts; raises BudgetRefused when the run must not start."""
    cap = min(max_requests, cfbd.MAX_REQUESTS_PER_RUN)
    if len(requests) > cap:
        raise cfbd.BudgetRefused(f"plan is {len(requests)} metered requests, cap is {cap}; "
                                 f"split the run")
    client = client or cfbd.Client(conn)
    info = client.info()
    remaining = int(info["remainingCalls"])
    if remaining - len(requests) < config.CFBD_RESERVE:
        raise cfbd.BudgetRefused(
            f"server reports {remaining} calls remaining; this plan spends {len(requests)} and "
            f"would leave {remaining - len(requests)}, below the {config.CFBD_RESERVE} floor")
    print(f"  CFBD /info: {remaining} remaining of {info.get('monthlyLimit')}, "
          f"used {info.get('usedCalls')}; plan {len(requests)}")

    counts = {}
    for req in requests:
        fetched_ts = time.time()
        status, body, rem, row = client.get(req)
        if status != 200:
            client.settle(row, None, f"http_{status}")
            counts[f"http_{status}"] = counts.get(f"http_{status}", 0) + 1
            print(f"  STOPPED: /{req.endpoint} {req.param_dict()} returned {status}")
            break
        file_id, outcome = archive_cfbd(conn, req, body, fetched_ts)
        client.settle(row, file_id, outcome)
        counts[outcome] = counts.get(outcome, 0) + 1
        print(f"  {req.dataset:<11} {req.season} {req.part:<14} {outcome}  remaining {rem}")
        if rem is not None and rem <= config.CFBD_RESERVE:
            counts["stopped_at_reserve"] = 1
            print(f"  STOPPED: remaining {rem} reached the {config.CFBD_RESERVE} floor")
            break
    return counts


def run_parse_cfbd(conn, seasons=None, rebuild=False):
    totals = {"files": 0, "rows": 0, "inserted": 0, "closed": 0, "refused": 0, "dropped": {}}
    for dataset, table in cfbd_normalize.TABLES.items():
        scopes = conn.execute(
            "SELECT DISTINCT season, asset FROM cfb_raw_files WHERE dataset=? ORDER BY season, asset",
            (dataset,)).fetchall()
        for season, part in scopes:
            if seasons is not None and season not in seasons:
                continue
            if rebuild:
                conn.execute("BEGIN")
                conn.execute(f"DELETE FROM {table} WHERE {versioning.SCOPE}", (dataset, season, part))
                conn.execute("UPDATE cfb_parse_log SET status='rebuilt_over' WHERE dataset=? AND "
                             "season IS ? AND status='ok' AND rel_path IN (SELECT rel_path FROM "
                             "cfb_raw_files WHERE dataset=? AND season=? AND asset=?)",
                             (dataset, season, dataset, season, part))
                conn.commit()
            others = [p for (p,) in conn.execute(
                f"SELECT DISTINCT src_part FROM {table} WHERE src_dataset=? AND src_season=? "
                f"AND valid_to_ts IS NULL", (dataset, season)) if p and cfbd.parts_overlap(p, part)]
            for fid, rel, fts in conn.execute(
                    "SELECT file_id, rel_path, fetched_ts FROM cfb_raw_files WHERE dataset=? AND "
                    "season=? AND asset=? ORDER BY fetched_ts", (dataset, season, part)).fetchall():
                if _parsed(conn, rel):
                    continue
                try:
                    if others:
                        raise versioning.OutOfOrder(
                            f"scope {part} overlaps {others} already held for {season}; one game "
                            f"would be current twice. --rebuild the other scope first")
                    with gzip.open(os.path.join(paths.raw_root(), *rel.split("/")), "rb") as f:
                        payload = json.loads(f.read())
                    norm = cfbd_normalize.NORMALIZERS[dataset](payload, season)
                    conn.execute("BEGIN")
                    ins, closed, same = versioning.apply(conn, dataset, season, fid, fts, table,
                                                         norm.rows, label=rel, part=part)
                    suffix = "" if part == "both" else f"@{part}"
                    conn.executemany(
                        "INSERT INTO cfb_measurements (key, season, value, detail, src_file, "
                        "measured_ts) VALUES (?,?,?,?,?,?) ON CONFLICT(key, season) DO UPDATE SET "
                        "value=excluded.value, detail=excluded.detail, src_file=excluded.src_file, "
                        "measured_ts=excluded.measured_ts",
                        [(k + suffix, season, v, d, rel, time.time()) for k, v, d in norm.measurements])
                    conn.execute("INSERT INTO cfb_parse_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (time.time(), rel, dataset, season, fts, len(norm.rows), ins,
                                  closed, same, json.dumps(norm.dropped), "ok", part))
                    conn.commit()
                except (versioning.OutOfOrder, ValueError, OSError) as e:   # incl. ProviderSplit
                    conn.rollback()
                    conn.execute("INSERT INTO cfb_parse_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (time.time(), rel, dataset, season, fts, None, None, None, None,
                                  None, "refused", f"{type(e).__name__}: {e}"))
                    conn.commit()
                    totals["refused"] += 1
                    print(f"  REFUSED {rel}: {e}")
                    continue
                totals["files"] += 1
                totals["rows"] += len(norm.rows)
                totals["inserted"] += ins
                totals["closed"] += closed
                for k, v in norm.dropped.items():
                    totals["dropped"][f"{dataset}.{k}"] = totals["dropped"].get(f"{dataset}.{k}", 0) + v
                print(f"  parsed {dataset:<11} {season} {part:<14} rows {len(norm.rows):>6,}  "
                      f"+{ins:,} -{closed:,} ={same:,}  dropped {norm.dropped or 0}")
    return totals


def promote_probe(conn, probe=None):
    """The exchange probe capture -> cfb_exchange_markets / cfb_exchange_closes.

    The source is a local database, not a download, so its manifest row points
    OUTSIDE cfb/raw (`rel_path` starts with 'external:') and its content hash is
    over the extracted rows: hashing 23.9 GB to identify an archive that is never
    written again would say nothing the extraction hash does not. Re-running is
    idempotent - an unchanged extraction reuses the manifest row and changes no
    fact row."""
    probe = probe or probe_promote.open_probe()
    src = probe_promote.probe_path()
    market_rows, close_rows, measurements = probe_promote.extract(probe, conn)
    counts = {}
    for dataset, table, rows in (("probe_markets", "cfb_exchange_markets", market_rows),
                                 ("probe_closes", "cfb_exchange_closes", close_rows)):
        csha = hashlib.sha256(json.dumps(sorted(rows), default=str).encode()).hexdigest()
        rel = f"external:{src}#{dataset}"
        row = conn.execute("SELECT file_id, content_sha256 FROM cfb_raw_files WHERE rel_path=?",
                           (rel,)).fetchone()
        now = time.time()
        if row and row[1] == csha:
            fid, fts = row[0], conn.execute("SELECT fetched_ts FROM cfb_raw_files WHERE file_id=?",
                                            (row[0],)).fetchone()[0]
        elif row:
            conn.execute("UPDATE cfb_raw_files SET content_sha256=?, bytes_sha256=?, fetched_ts=? "
                         "WHERE file_id=?", (csha, csha, now, row[0]))
            fid, fts = row[0], now
        else:
            cur = conn.execute(
                "INSERT INTO cfb_raw_files (rel_path, dataset, season, repo, tag, asset, bytes, "
                "bytes_sha256, content_sha256, remote_updated_at, fetched_ts) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?)",
                (rel, dataset, 2026, "local:cfb_probe.db", "probe_capture", probe_promote.PART,
                 os.path.getsize(src), csha, csha,
                 datetime.fromtimestamp(os.path.getmtime(src), timezone.utc).isoformat(), now))
            fid, fts = cur.lastrowid, now
        conn.commit()
        conn.execute("BEGIN")
        ins, closed, same = versioning.apply(conn, dataset, 2026, fid, fts, table, rows,
                                             label=rel, part=probe_promote.PART)
        conn.commit()
        counts[dataset] = {"rows": len(rows), "inserted": ins, "closed": closed, "unchanged": same}
    conn.executemany(
        "INSERT INTO cfb_measurements (key, season, value, detail, src_file, measured_ts) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(key, season) DO UPDATE SET value=excluded.value, "
        "detail=excluded.detail, src_file=excluded.src_file, measured_ts=excluded.measured_ts",
        [(k, 2026, v, d, f"external:{src}", time.time()) for k, v, d in measurements])
    conn.commit()
    return counts, measurements


def latest_completed_week(conn, season, now=None, settle_hours=12):
    """The most recent (season_type, week) of `season` whose last game started
    at least `settle_hours` ago, from the schedule already in the store - so
    resolving it costs no request. None when no week has finished."""
    now = now or time.time()
    r = conn.execute(
        "SELECT season_type, week, MAX(start_ts) AS last FROM cfb_games "
        "WHERE valid_to_ts IS NULL AND season=? AND week IS NOT NULL AND start_ts IS NOT NULL "
        "AND season_type IN ('regular', 'postseason') GROUP BY season_type, week "
        "HAVING last <= ? ORDER BY last DESC LIMIT 1",
        (season, now - settle_hours * 3600)).fetchone()
    return (r[0], r[1]) if r else None


def temp_usage():
    """(temp dir, bytes, files) for this process's temp directory. Best effort:
    a file that vanishes mid-walk is skipped, never raised."""
    import tempfile
    root = tempfile.gettempdir()
    total = files = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return root, total, files


# =============================================================================
# The Odds API (phase 3) - FREE endpoints only; see cfb/oddsapi.py
# =============================================================================

def run_odds_free(conn, client=None):
    """GET /sports then /sports/americanfootball_ncaaf/events: 0 credits, each
    verified against x-requests-last. Raw first. Returns a summary dict."""
    floor = oddsapi.reserve()                    # refuse before any request if unset
    client = client or oddsapi.Client(conn)
    season = sources.CURRENT_SEASON
    out = {}
    for endpoint, dataset, part in (("sports", "oddsapi_sports", "all"),
                                    ("events", "oddsapi_events", oddsapi.SPORT)):
        req = cfbd.Request(dataset, endpoint, season, part)
        fetched_ts = time.time()
        body, h, row = client.get_free(endpoint)
        file_id, outcome = archive_cfbd(conn, req, body, fetched_ts, source="oddsapi",
                                        repo="the-odds-api.com")
        client.settle(row, file_id, outcome)
        out[endpoint] = {"file_id": file_id, "outcome": outcome, **h}
        print(f"  /{endpoint:<7} {outcome:<18} billed {h['last']}  remaining {h['remaining']}  "
              f"used {h['used']}  file {file_id}")
        if endpoint == "sports":
            ncaaf = [s for s in json.loads(body) if s.get("key") == oddsapi.SPORT]
            out["ncaaf_active"] = bool(ncaaf and ncaaf[0].get("active"))
            print(f"  {oddsapi.SPORT} listed {bool(ncaaf)}  active {out['ncaaf_active']}")
        else:
            out["n_events"] = len(oddsapi.events_from(body))
            print(f"  events listed: {out['n_events']}")
    rem = out["events"]["remaining"]
    print(f"  pool: remaining {rem}, reserve {floor}, spendable above reserve "
          f"{None if rem is None else rem - floor}  (shared with the NFL logger)")
    return out


def archive_odds(conn, client, row, dataset, endpoint, part, body, fetched_ts, dedupe):
    """Archive a response, settle its ledger row, parse it into the observation tables."""
    req = cfbd.Request(dataset, endpoint, sources.CURRENT_SEASON, part)
    file_id, outcome = archive_cfbd(conn, req, body, fetched_ts, source="oddsapi",
                                    repo="the-odds-api.com", dedupe=dedupe)
    client.settle(row, file_id, outcome)
    if outcome == "unchanged_content":
        # The bytes are the EARLIER file's, so the observation rows must carry that
        # file's fetch time, not this tick's. Otherwise a content-deduplicated listing
        # re-parsed five minutes later claims to have been fetched five minutes later,
        # and "when did the API first say this kickoff" reads as the last time nothing
        # changed. Only the free listing dedupes; a paid response never does.
        fetched_ts = conn.execute("SELECT fetched_ts FROM cfb_raw_files WHERE file_id=?",
                                  (file_id,)).fetchone()[0]
    counts = oddsapi_capture.store_rows(conn, file_id, fetched_ts,
                                        oddsapi_capture.PARSERS[dataset](body))
    return file_id, outcome, counts


def refresh_events(conn, client):
    """The free events listing, archived (content-deduplicated) and parsed. It also
    gives the client this run's pool balance, which every paid call requires."""
    fetched_ts = time.time()
    body, h, row = client.get_free("events")
    file_id, outcome, _ = archive_odds(conn, client, row, "oddsapi_events", "events",
                                       oddsapi.SPORT, body, fetched_ts, dedupe=True)
    return file_id, oddsapi.events_from(body), h


def newest_listing(conn):
    """(fetched_ts, file_id, events) of the newest archived events listing, or (None, None, [])."""
    row = conn.execute("SELECT fetched_ts, file_id, rel_path FROM cfb_raw_files WHERE "
                       "dataset='oddsapi_events' ORDER BY fetched_ts DESC LIMIT 1").fetchone()
    if row is None:
        return None, None, []
    with gzip.open(os.path.join(paths.raw_root(), *row[2].split("/"))) as f:
        return row[0], row[1], oddsapi.events_from(f.read())


def p1_spent(conn):
    """Credits P1 has cost, lifetime. A row whose cost the server did not report but that
    reached the server counts at the expected 1: an unknown spend is not a free one."""
    return conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN cost_last IS NOT NULL THEN cost_last "
        "WHEN status IS NOT NULL THEN 1 ELSE 0 END), 0) FROM oddsapi_requests "
        "WHERE purpose='p1'").fetchone()[0]


def run_odds_p1(conn, client=None, now=None, max_credits=oddsapi.P1_APPROVED_CREDITS,
                gap_s=1.0):
    """P1: /events/{id}/markets for every pre-match listed event, once, FBS first.
    Lifetime budget P1_APPROVED_CREDITS; not before P1_NOT_BEFORE. Every response is
    kept verbatim. Stops at the first non-200 or unexpected charge."""
    now = time.time() if now is None else now
    not_before = oddsapi.iso_ts(oddsapi.P1_NOT_BEFORE)
    if now < not_before:
        raise oddsapi.BudgetRefused(f"P1 is approved for Saturday morning; not before "
                                    f"{oddsapi.P1_NOT_BEFORE}")
    budget = min(max_credits, oddsapi.P1_APPROVED_CREDITS) - p1_spent(conn)
    if budget <= 0:
        raise oddsapi.BudgetRefused(f"P1 has spent {p1_spent(conn)} of "
                                    f"{oddsapi.P1_APPROVED_CREDITS}; nothing left")
    oddsapi.reserve()
    client = client or oddsapi.Client(conn)
    listing_id, events, h = refresh_events(conn, client)
    plan = oddsapi_capture.plan_p1(conn, events, now, sources.CURRENT_SEASON)
    print(f"  P1: {len(events)} listed, {len(plan)} pre-match, budget {budget}, pool "
          f"{h['remaining']}, listing file {listing_id}")
    done = {r[0] for r in conn.execute("SELECT event_id FROM oddsapi_requests WHERE purpose='p1' "
                                       "AND status=200")}
    counts = {"called": 0, "credits": 0, "already_done": 0, "not_reached": 0}
    for i, (e, label) in enumerate(plan):
        if e["id"] in done:
            counts["already_done"] += 1
            continue
        if budget < oddsapi.PAID["event_markets"][2]:
            counts["not_reached"] = len(plan) - i
            print(f"  P1 budget exhausted; {len(plan) - i} events not reached")
            break
        if counts["called"]:
            time.sleep(gap_s)
        fetched_ts = time.time()
        status, body, cost, row = client.get_paid("event_markets", "p1", budget, event_id=e["id"])
        budget -= cost
        counts["called"] += 1
        counts["credits"] += cost
        if status != 200:
            # Kept verbatim too: a refusal is evidence. Not parsed.
            req = cfbd.Request("oddsapi_event_markets_error", "event_markets",
                               sources.CURRENT_SEASON, e["id"])
            fid, _ = archive_cfbd(conn, req, body, fetched_ts, source="oddsapi",
                                  repo="the-odds-api.com", dedupe=False)
            client.settle(row, fid, f"http_{status}")
            print(f"  STOPPED P1: {e['id']} returned {status}")
            counts["stopped"] = status
            break
        fid, _o, c = archive_odds(conn, client, row, "oddsapi_event_markets", "event_markets",
                                  e["id"], body, fetched_ts, dedupe=False)
        n_keys = len({r[3] for r in oddsapi_capture.parse_event_markets(body)
                      ["cfb_odds_event_markets"]})
        print(f"  {label:<9} {e['commence_time']}  {e['away_team']} @ {e['home_team']}  "
              f"cost {cost}  books/markets rows {c.get('cfb_odds_event_markets', 0)}  "
              f"distinct keys {n_keys}  file {fid}")
    print(f"  P1: {counts}  pool now {client.remaining}")
    return counts


def run_odds_forward(conn, client=None, now=None):
    """One tick of the forward game-line capture. Refreshes the free listing only when
    a kickoff is near or the listing is stale; buys at most one bulk snapshot."""
    now = time.time() if now is None else now
    fetched, listing_id, events = newest_listing(conn)
    if not oddsapi_capture.needs_refresh(fetched, events, now):
        print(f"  forward: no kickoff within 40 min; listing {int((now - fetched) / 60)} min old")
        return {"refreshed": False}
    oddsapi.reserve()
    client = client or oddsapi.Client(conn)
    listing_id, events, h = refresh_events(conn, client)
    due, missed, skipped = oddsapi_capture.plan_forward(conn, events, now)
    for hour, g in missed:
        conn.execute("INSERT OR IGNORE INTO cfb_odds_snapshots (hour_ts, week_start_ts, "
                     "earliest_commence_ts, n_events, listing_file_id, outcome, detail) "
                     "VALUES (?,?,?,?,?,'missed',?)",
                     (hour, g["week"], g["earliest"], g["n"], listing_id,
                      f"first kickoff passed before a tick fell in the window ({now:.0f})"))
        print(f"  MISSED kickoff hour {_utc(hour)} ({g['n']} events)")
    for hour, g in skipped:
        conn.execute("INSERT OR IGNORE INTO cfb_odds_snapshots (hour_ts, week_start_ts, "
                     "earliest_commence_ts, n_events, listing_file_id, outcome, detail) "
                     "VALUES (?,?,?,?,?,'skipped_weekly_cap',?)",
                     (hour, g["week"], g["earliest"], g["n"], listing_id,
                      f"cap {oddsapi.FORWARD_WEEKLY_CAP}/week kept hours with more games"))
        print(f"  SKIPPED kickoff hour {_utc(hour)} ({g['n']} events): weekly cap")
    conn.commit()
    out = {"refreshed": True, "missed": len(missed), "skipped": len(skipped), "captured": 0}
    for hour, g, left in due:
        conn.execute("INSERT OR REPLACE INTO cfb_odds_snapshots (hour_ts, week_start_ts, "
                     "earliest_commence_ts, n_events, listing_file_id, fired_ts, outcome) "
                     "VALUES (?,?,?,?,?,?,'firing')",
                     (hour, g["week"], g["earliest"], g["n"], listing_id, time.time()))
        conn.commit()
        fetched_ts = time.time()
        try:
            status, body, cost, row = client.get_paid("odds", "forward", left)
        except oddsapi.BudgetRefused as e:
            conn.execute("UPDATE cfb_odds_snapshots SET outcome='refused', detail=? WHERE hour_ts=?",
                         (str(e), hour))
            conn.commit()
            raise
        except oddsapi.UnexpectedCharge as e:
            # Count the larger of what was expected and what the server said it billed,
            # so the weekly cap is charged for an overcharge rather than hiding it.
            billed = conn.execute("SELECT MAX(cost_last) FROM oddsapi_requests WHERE run_id=? "
                                  "AND purpose='forward'", (client.run_id,)).fetchone()[0]
            conn.execute("UPDATE cfb_odds_snapshots SET outcome='charge_unknown', cost=?, detail=? "
                         "WHERE hour_ts=?", (max(oddsapi.PAID["odds"][2], billed or 0), str(e), hour))
            conn.commit()
            raise
        except oddsapi.OddsApiError as e:
            conn.execute("UPDATE cfb_odds_snapshots SET outcome='error', cost=0, detail=? "
                         "WHERE hour_ts=?", (str(e), hour))
            conn.commit()
            raise
        if status != 200:
            conn.execute("UPDATE cfb_odds_snapshots SET outcome='error', cost=?, detail=? "
                         "WHERE hour_ts=?", (cost, f"http {status}", hour))
            client.settle(row, None, f"http_{status}")
            conn.commit()
            raise oddsapi.OddsApiError(f"forward odds returned {status}")
        fid, _o, c = archive_odds(conn, client, row, "oddsapi_odds", "odds", oddsapi.SPORT,
                                  body, fetched_ts, dedupe=False)
        conn.execute("UPDATE cfb_odds_snapshots SET outcome='captured', cost=?, file_id=? "
                     "WHERE hour_ts=?", (cost, fid, hour))
        conn.commit()
        out["captured"] = 1
        print(f"  CAPTURED kickoff hour {_utc(hour)}: first kickoff {_utc(g['earliest'])}, "
              f"{g['n']} events in hour, cost {cost}, {left - cost} left this week, "
              f"{c.get('cfb_odds_quotes', 0)} quotes on {c.get('cfb_odds_events', 0)} events, "
              f"file {fid}, pool {client.remaining}")
    if not due:
        nxt = sorted((g["earliest"], hr) for hr, g in oddsapi_capture.hour_groups(events).items()
                     if g["earliest"] > now)
        print(f"  forward: nothing due; pool {h['remaining']}; next kickoff hour "
              f"{_utc(nxt[0][1]) if nxt else '-'}")
    return out


def odds_week_report(conn, now=None):
    """The current CFB week's kickoff hours as listed, and what the capture did with each."""
    now = time.time() if now is None else now
    fetched, _fid, events = newest_listing(conn)
    week = oddsapi.week_start_ts(now)
    groups = {h: g for h, g in oddsapi_capture.hour_groups(events).items() if g["week"] == week}
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT hour_ts, outcome, cost, file_id FROM cfb_odds_snapshots WHERE week_start_ts=?",
        (week,))}
    spent = sum((r[1] or 0) for r in rows.values())
    print(f"forward capture, CFB week from {_utc(week)}: {len(groups)} kickoff hours listed "
          f"(listing {_utc(fetched) if fetched else '-'}), cap {oddsapi.FORWARD_WEEKLY_CAP}, "
          f"spent {spent}")
    for h in sorted(set(groups) | set(rows)):
        g = groups.get(h, {})
        r = rows.get(h, ("pending", 0, None))
        print(f"  {_utc(h)}  events {g.get('n', '-'):>3}  {r[0]:<20} cost {r[1]}  file {r[2]}")


def run_odds_reparse(conn):
    """Replay every archived Odds API response into the observation tables, 0 requests."""
    n = 0
    for fid, dataset, rel, fetched_ts in conn.execute(
            "SELECT file_id, dataset, rel_path, fetched_ts FROM cfb_raw_files WHERE dataset IN "
            f"({','.join('?' * len(oddsapi_capture.PARSERS))}) ORDER BY fetched_ts",
            tuple(oddsapi_capture.PARSERS)).fetchall():
        with gzip.open(os.path.join(paths.raw_root(), *rel.split("/"))) as f:
            oddsapi_capture.store_rows(conn, fid, fetched_ts,
                                       oddsapi_capture.PARSERS[dataset](f.read()))
        n += 1
    return n


def cfbd_status(conn, client=None):
    month = cfbd.month_key()
    mine = conn.execute("SELECT COUNT(*) FROM cfbd_requests WHERE month=? AND metered=1 AND "
                        "(status IS NULL OR status BETWEEN 200 AND 299)", (month,)).fetchone()[0]
    print(f"CFBD ledger  month {month}")
    print(f"  metered 2xx requests logged by this job: {mine}")
    for origin, n in conn.execute("SELECT origin, COUNT(*) FROM cfbd_requests WHERE month=? AND "
                                  "metered=1 GROUP BY origin", (month,)):
        print(f"    {n:>4}  {origin}")
    try:
        info = (client or cfbd.Client(conn)).info()
    except cfbd.BudgetRefused as e:
        print(f"  server: unavailable ({e})")
        return
    used = info.get("usedCalls")
    print(f"  server: remaining {info.get('remainingCalls')} of {info.get('monthlyLimit')}, "
          f"used {used}, resets {info.get('resetAt')}")
    if used is not None:
        print(f"  used by something other than this job this month: {int(used) - mine} "
              f"(the main clone, CBBD, the docs playground)")
    print(f"  reserve floor {config.CFBD_RESERVE}; per-run cap {cfbd.MAX_REQUESTS_PER_RUN}")


# =============================================================================
# audit and status
# =============================================================================

@dataclass(frozen=True)
class AuditReport:
    """What the audit found, as a value the caller has to carry.

    A GUARD RETURNS THE STATEMENT IT APPROVED, NEVER A BARE BOOLEAN. A boolean can be
    dropped on the floor and usually is; a statement has to be printed, stored or
    asserted on. Ethan, 2026-09-17, generalising `cfb.pbp_scope.check`, which returns
    the scope it approved rather than True.

    SO THIS OBJECT REFUSES TRUTH-TESTING, rather than merely not supporting it.
    `__bool__` would reinstate exactly what the rule prevents - `if audit(conn):`
    discards the statement - but DELETING it is worse than refusing it: Python then
    makes every instance truthy, so the seven existing `assert audit(store)` call sites
    would keep passing while asserting nothing at all, including on a failed audit. That
    is the vacuous-assertion trap this project has hit before. Raising turns each of
    them into a loud failure that has to be rewritten as `.clean`.
    """
    on_disk: int
    manifested: int
    unregistered: list
    missing: list
    unreadable: list
    external: int
    bad_external: list

    @property
    def clean(self) -> bool:
        return not (self.unregistered or self.missing or self.unreadable or self.bad_external)

    @property
    def statement(self) -> str:
        return (f"CFB raw audit: {self.on_disk} files on disk, {self.manifested} manifested, "
                f"{self.external} external; unregistered {len(self.unregistered)}, missing "
                f"{len(self.missing)}, unreadable {len(self.unreadable)}, unreadable external "
                f"{len(self.bad_external)} - {'CLEAN' if self.clean else 'FAILED'}")

    def __bool__(self):
        raise TypeError(
            "AuditReport has no truth value: use .clean for the verdict and .statement "
            "for the line to print or store. A guard's result must be carried, not "
            "discarded by an `if`.")

    def __str__(self):
        return self.statement


def audit(conn):
    """Every raw file manifested, every manifested file present and readable.
    Returns an `AuditReport`; see that class for why it is not a bool."""
    root = paths.raw_root()
    on_disk = set()
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                on_disk.add(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    manifest = {r[0] for r in conn.execute("SELECT rel_path FROM cfb_raw_files")
                if not r[0].startswith("external:")}
    external = [r[0] for r in conn.execute(
        "SELECT rel_path FROM cfb_raw_files WHERE rel_path LIKE 'external:%'")]
    unregistered = sorted(on_disk - manifest)
    missing = sorted(manifest - on_disk)
    unreadable = []
    for rel in sorted(manifest & on_disk):
        path = os.path.join(root, *rel.split("/"))
        try:
            if rel.endswith(".json.gz"):
                with gzip.open(path, "rb") as f:
                    json.loads(f.read())
            else:
                pl.read_parquet_schema(path)
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
    bad_external = []
    for rel in external:
        path = rel[len("external:"):].split("#", 1)[0]
        try:
            import sqlite3 as _sq
            c = _sq.connect(f"file:{path}?mode=ro", uri=True)
            c.execute("SELECT 1 FROM markets LIMIT 1").fetchone()
            c.close()
        except Exception as e:
            bad_external.append((rel, f"{type(e).__name__}: {e}"))
    print(f"  external sources {len(external)}  unreadable {len(bad_external)}")
    for x, why in bad_external:
        print(f"    UNREADABLE EXTERNAL {x}: {why}")
    report = AuditReport(len(on_disk), len(manifest), unregistered, missing, unreadable,
                         len(external), bad_external)
    print(f"  {report.statement}")
    return report


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


def _locked_run(conn, a, plan, cfbd_plan):
    """Facts first, then CFBD. A CFBD refusal must not cost the week its stats,
    so it is reported in the exit code (3) AFTER the free work is done."""
    code = 0
    if a.odds_free:
        print("Odds API, free endpoints:")
        try:
            run_odds_free(conn)
        except (oddsapi.OddsApiError, oddsapi.UnexpectedCharge) as e:
            print(f"STOPPED Odds API: {type(e).__name__}: {e}")
            code = 5
    for flag, label, fn in (("odds_p1", "P1 event markets", run_odds_p1),
                            ("odds_forward", "forward game lines", run_odds_forward)):
        if getattr(a, flag):
            print(f"Odds API, {label}:")
            try:
                fn(conn)
            except (oddsapi.OddsApiError, oddsapi.UnexpectedCharge, oddsapi.BudgetRefused) as e:
                print(f"STOPPED Odds API {label}: {type(e).__name__}: {e}")
                code = 5
    if a.odds_week:
        odds_week_report(conn)
    if a.odds_reparse:
        print(f"Odds API reparse: {run_odds_reparse(conn)} files")
    if a.promote_probe:
        counts, m = promote_probe(conn)
        print(f"probe promotion: {counts}")
        for k, v, d in m:
            print(f"  {k:<70} {v}{'  ' + d if d else ''}")
    if a.fetch or a.parse or a.rebuild:
        print(f"plan: {len(plan)} (dataset, season) pairs")
        if a.fetch:
            print(f"fetch: {run_fetch(conn, plan, a.max_files)}")
        totals = run_parse(conn, plan, rebuild=a.rebuild)
        print(f"parse: files {totals['files']}  rows {totals['rows']:,}  "
              f"+{totals['inserted']:,} -{totals['closed']:,} ={totals['unchanged']:,}  "
              f"refused {totals['refused']}")
        print(f"dropped: {totals['dropped'] or 0}")
        if a.parse or a.rebuild:
            t = run_parse_cfbd(conn, parse_seasons(a.season), rebuild=a.rebuild)
            print(f"CFBD parse: files {t['files']}  rows {t['rows']:,}  refused {t['refused']}")

    if a.cfbd_week == "latest" or a.cfbd_rankings == "latest":
        wk = latest_completed_week(conn, sources.CURRENT_SEASON)
        if wk is None:
            print(f"CFBD: no finished week of {sources.CURRENT_SEASON} in the stored schedule")
        else:
            print(f"CFBD latest finished week: {sources.CURRENT_SEASON} {wk[0]} week {wk[1]}")
            if a.cfbd_week == "latest":
                cfbd_plan = cfbd_plan + cfbd.week(sources.CURRENT_SEASON, wk[1], wk[0])
            if a.cfbd_rankings == "latest":
                cfbd_plan = cfbd_plan + cfbd.rankings(sources.CURRENT_SEASON, wk[1], wk[0])

    if cfbd_plan:
        print(f"CFBD plan: {len(cfbd_plan)} metered requests")
        try:
            print(f"CFBD: {run_cfbd(conn, cfbd_plan, max_requests=a.max_requests)}")
        except cfbd.BudgetRefused as e:
            print(f"REFUSING CFBD: {e}")
            code = 3
        t = run_parse_cfbd(conn, {r.season for r in cfbd_plan})
        print(f"CFBD parse: files {t['files']}  rows {t['rows']:,}  +{t['inserted']:,} "
              f"-{t['closed']:,}  refused {t['refused']}  dropped {t['dropped'] or 0}")
        if t["refused"]:
            code = code or 4
    if a.fetch or a.parse or a.rebuild or cfbd_plan or a.promote_probe:
        measure_joins(conn)
    return code


def main(argv=None):
    """Entry point. With --log, every line and any traceback go to the CFB log
    and the exit code is written last, so a scheduled run leaves evidence even
    when it fails before printing anything."""
    args = sys.argv[1:] if argv is None else argv
    if "--log" not in args:
        return _main(argv)
    import contextlib
    import traceback
    path = paths.root("logs", "ingest_cfb.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f, contextlib.redirect_stdout(f),             contextlib.redirect_stderr(f):
        print(f"===== {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} start  argv={args}")
        code = 1
        try:
            code = _main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        except BaseException:
            traceback.print_exc()
            code = 1
        finally:
            print(f"===== {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} exit {code}", flush=True)
    return code


def _main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fetch", action="store_true", help="download changed assets, then parse")
    ap.add_argument("--parse", action="store_true", help="parse the archive only, 0 requests")
    ap.add_argument("--rebuild", action="store_true",
                    help="delete the scope's rows and replay the archive in order, 0 requests")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--dataset", action="append", choices=sorted(sources.DATASETS))
    ap.add_argument("--season", help="2026 | 2004-2026 | 2019,2021 | all (default all)")
    ap.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    ap.add_argument("--cfbd-status", action="store_true", help="ledger + /info, 0 metered requests")
    ap.add_argument("--log", action="store_true",
                    help="append all output to <STORAGE_DIR>/cfb/logs/ingest_cfb.log (for the scheduler)")
    ap.add_argument("--promote-probe", action="store_true",
                    help="exchange probe capture -> cfb.db (read-only on the probe, 0 requests)")
    ap.add_argument("--odds-free", action="store_true",
                    help="Odds API /sports and NCAAF /events: documented free, verified 0 per call")
    ap.add_argument("--odds-p1", action="store_true",
                    help=f"P1: event markets for every pre-match listed event, "
                         f"{oddsapi.P1_APPROVED_CREDITS} credits lifetime, not before {oddsapi.P1_NOT_BEFORE}")
    ap.add_argument("--odds-forward", action="store_true",
                    help="one forward-capture tick: bulk h2h/spreads/totals per kickoff hour "
                         f"(3 credits, {oddsapi.FORWARD_WEEKLY_CAP}/week); schedule every 5 minutes")
    ap.add_argument("--odds-week", action="store_true", help="this CFB week's capture state, 0 requests")
    ap.add_argument("--odds-reparse", action="store_true",
                    help="re-derive the Odds API observation tables from the archive, 0 requests")
    ap.add_argument("--lock-wait", type=int, default=0, metavar="SECONDS",
                    help="retry the single-instance lock for this long instead of exiting 2")
    ap.add_argument("--cfbd-lines", metavar="SEASONS",
                    help="season-level lines, regular+postseason: one metered request per season")
    ap.add_argument("--cfbd-week", metavar="YEAR:WEEK|latest",
                    help="one week's results and lines: two metered requests. 'latest' resolves "
                         "the most recent finished week of CURRENT_SEASON from the stored schedule")
    ap.add_argument("--cfbd-rankings", metavar="YEAR:WEEK|latest",
                    help="one week's polls: ONE metered request. 'latest' resolves the most "
                         "recent finished week of CURRENT_SEASON from the stored schedule")
    ap.add_argument("--season-type", default="regular", choices=["regular", "postseason"])
    ap.add_argument("--max-requests", type=int, default=cfbd.MAX_REQUESTS_PER_RUN,
                    help=f"lower the per-run cap (never above {cfbd.MAX_REQUESTS_PER_RUN})")
    a = ap.parse_args(argv)

    paths.ensure_dirs()
    plan = sources.plan(a.dataset, parse_seasons(a.season))

    cfbd_plan = []
    if a.cfbd_lines:
        cfbd_plan += cfbd.lines_backfill(parse_seasons(a.cfbd_lines))
    if a.cfbd_week and a.cfbd_week != "latest":
        y, w = (int(x) for x in a.cfbd_week.split(":"))
        cfbd_plan += cfbd.week(y, w, a.season_type)
    if a.cfbd_rankings and a.cfbd_rankings != "latest":
        y, w = (int(x) for x in a.cfbd_rankings.split(":"))
        cfbd_plan += cfbd.rankings(y, w, a.season_type)

    if a.cfbd_status:
        cfbd_status(connect())
        return 0
    odds_flags = a.odds_free or a.odds_p1 or a.odds_forward or a.odds_week or a.odds_reparse
    if not (a.fetch or a.parse or a.rebuild or a.audit or cfbd_plan or a.cfbd_week
            or a.cfbd_rankings or a.promote_probe or odds_flags):
        conn = connect()
        status(conn)
        return 0

    if a.odds_forward and not (a.fetch or a.parse or a.rebuild or a.audit or cfbd_plan
                               or a.cfbd_week or a.promote_probe or a.odds_free or a.odds_p1
                               or a.odds_reparse or a.odds_week) and os.path.exists(paths.db_path()):
        # A tick every 5 minutes: with nothing near kickoff it takes no lock, writes
        # nothing and makes no request, so it cannot collide with P1 or the weekly refresh.
        ro = sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)
        try:
            fetched, _fid, events = newest_listing(ro)
        except sqlite3.OperationalError:
            fetched, events = None, []
        finally:
            ro.close()
        if not oddsapi_capture.needs_refresh(fetched, events, time.time()):
            print(f"forward: no kickoff within 40 min; listing "
                  f"{int((time.time() - fetched) / 60)} min old; no request")
            return 0

    lock = InstanceLock(os.path.join(paths.checkpoints_root(), "ingest_cfb.lock"))
    deadline = time.time() + max(a.lock_wait, 0)
    while True:
        try:
            lock.__enter__()
            break
        except AlreadyRunning as e:
            if time.time() >= deadline:
                print(f"REFUSING: {e}")
                return 2
            time.sleep(5)

    try:                                  # the lock taken above is the one held throughout
        conn = connect()
        if a.audit:
            return 0 if audit(conn).clean else 1
        run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
        tdir, tbytes, tfiles = temp_usage()
        conn.execute("INSERT INTO cfb_runs (run_id, argv, started_ts, temp_dir, "
                     "temp_bytes_start, temp_files_start) VALUES (?,?,?,?,?,?)",
                     (run_id, json.dumps(sys.argv[1:] if argv is None else argv), time.time(),
                      tdir, tbytes, tfiles))
        conn.commit()
        print(f"temp at start: {tdir}  {tbytes / 1e9:.3f} GB in {tfiles:,} files")
        code = 0
        try:
            code = _locked_run(conn, a, plan, cfbd_plan)
            return code
        finally:
            tdir, tbytes, tfiles = temp_usage()
            conn.execute("UPDATE cfb_runs SET ended_ts=?, exit_code=?, temp_bytes_end=?, "
                         "temp_files_end=? WHERE run_id=?",
                         (time.time(), code, tbytes, tfiles, run_id))
            conn.commit()
            print(f"temp at end:   {tdir}  {tbytes / 1e9:.3f} GB in {tfiles:,} files")
    finally:
        lock.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
