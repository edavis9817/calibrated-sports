"""Row-level versioning by ingestion time, and the content hash that decides
whether a download is new.

WHY CONTENT AND NOT BYTES. Upstream re-uploads every historical season many
times a day. Whether the bytes move on a re-upload that changes no value is not
something to bet a disk on, so a new raw copy is kept only when the CONTENT
hash moves: every column, sorted by name, rendered to CSV, lines sorted. Row
order and column order upstream do not create versions; a changed value does.

WHY ROWS AND NOT FILES. The 2026 files are rewritten as each week's games land.
Carrying every version of a whole season side by side would store week 1 once
per day for the season. Instead a row is closed only when a later file stops
carrying it unchanged, so a season re-published 100 times with one correction
costs one extra row.
"""
import hashlib
import json

import polars as pl

from cfb import schema


def content_sha256(df: pl.DataFrame) -> str:
    cols = sorted(df.columns)
    text = df.select(cols).write_csv(include_header=False)
    lines = sorted(text.splitlines())
    h = hashlib.sha256()
    h.update(("\x1f".join(cols) + "\n").encode())
    for line in lines:
        h.update(line.encode())
        h.update(b"\n")
    return h.hexdigest()


def row_sha(values) -> str:
    """16 hex characters: a collision only matters within one key's history,
    where 64 bits is ample."""
    return hashlib.sha1(json.dumps(list(values), default=str,
                                   separators=(",", ":")).encode()).hexdigest()[:16]


class OutOfOrder(Exception):
    """A file older than one already applied to the same scope. Applying it
    would close rows with a timestamp before they were opened."""


def latest_version(conn, table, dataset, season):
    r = conn.execute(
        f"SELECT MAX(valid_from_ts) FROM {table} WHERE src_dataset=? AND src_season IS ?",
        (dataset, season)).fetchone()[0]
    c = conn.execute(
        f"SELECT MAX(valid_to_ts) FROM {table} WHERE src_dataset=? AND src_season IS ?",
        (dataset, season)).fetchone()[0]
    return max(x for x in (r, c, 0.0) if x is not None)


def apply(conn, dataset, season, file_id, version_ts, table, rows, label=None):
    """Diff `rows` against the current rows for (dataset, season) and write the
    difference as of `version_ts`. Idempotent: applying the same file twice
    changes nothing. Returns (inserted, closed, unchanged).

    Runs inside the caller's transaction; the caller commits.
    """
    label = label or f"file {file_id}"
    last = latest_version(conn, table, dataset, season)
    if version_ts < last:
        raise OutOfOrder(f"{label} is as of {version_ts:.0f}, but {table} "
                         f"{dataset}/{season} already holds {last:.0f}; "
                         f"use --rebuild to replay the archive in order")

    cols = schema.columns(table)
    key = schema.keys(table)
    kidx = [cols.index(k) for k in key]

    current = {}
    for rowid, *vals in conn.execute(
            f"SELECT rowid, {', '.join(key)}, row_sha FROM {table} "
            f"WHERE src_dataset=? AND src_season IS ? AND valid_to_ts IS NULL",
            (dataset, season)):
        current[tuple(vals[:-1])] = (rowid, vals[-1])

    to_insert, seen, unchanged = [], set(), 0
    to_close = []
    for r in rows:
        k = tuple(r[i] for i in kidx)
        seen.add(k)
        sha = row_sha(r)
        held = current.get(k)
        if held and held[1] == sha:
            unchanged += 1
            continue
        if held and version_ts == conn.execute(
                f"SELECT valid_from_ts FROM {table} WHERE rowid=?", (held[0],)).fetchone()[0]:
            # The same instant cannot hold two values for one key.
            raise OutOfOrder(f"{label}: key {k} changed within one version")
        if held:
            to_close.append(held[0])
        to_insert.append(tuple(r) + (dataset, season, file_id, sha, version_ts))

    for k, (rowid, _sha) in current.items():
        if k not in seen:
            to_close.append(rowid)

    conn.executemany(f"UPDATE {table} SET valid_to_ts=? WHERE rowid=?",
                     [(version_ts, rid) for rid in to_close])
    meta = ["src_dataset", "src_season", "src_file_id", "row_sha", "valid_from_ts"]
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(cols + meta)}) "
        f"VALUES ({', '.join('?' * (len(cols) + len(meta)))})", to_insert)
    return len(to_insert), len(to_close), unchanged


def rebuild_scope(conn, table, dataset, season):
    """Delete one scope's derivation so the archive can be replayed in order.
    Only the rows this (dataset, season) produced - never the whole table."""
    conn.execute(f"DELETE FROM {table} WHERE src_dataset=? AND src_season IS ?",
                 (dataset, season))


def as_of_clause(ts: float) -> tuple[str, tuple]:
    return ("valid_from_ts <= ? AND (valid_to_ts IS NULL OR valid_to_ts > ?)", (ts, ts))

