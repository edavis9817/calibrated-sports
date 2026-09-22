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

from cfb import schema as cfb_schema


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


SCOPE = "src_dataset=? AND src_season IS ? AND src_part IS ?"


def _schema(mod):
    """Which schema module describes `table`. Defaults to the CFB one; `feeds` passes
    its own. The alternative was a second copy of this file, which is the duplication
    that has cost this project a settlement rule and a lock."""
    return mod or cfb_schema


def latest_version(conn, table, dataset, season, part=None):
    r = conn.execute(f"SELECT MAX(valid_from_ts) FROM {table} WHERE {SCOPE}",
                     (dataset, season, part)).fetchone()[0]
    c = conn.execute(f"SELECT MAX(valid_to_ts) FROM {table} WHERE {SCOPE}",
                     (dataset, season, part)).fetchone()[0]
    return max(x for x in (r, c, 0.0) if x is not None)


def silent(v) -> bool:
    """NULL, or a string with nothing in it. Upstream has written both for "no value"
    (nflverse injuries carry 69 practice_status cells that are only whitespace), and
    a preservation rule that tested `is None` alone would let the second one through."""
    return v is None or (isinstance(v, str) and not v.strip())


def apply(conn, dataset, season, file_id, version_ts, table, rows, label=None, part=None,
          schema_mod=None, *, preserve=(), dated_by=(), preserved=None):
    """Diff `rows` against the current rows for (dataset, season) and write the
    difference as of `version_ts`. Idempotent: applying the same file twice
    changes nothing. Returns (inserted, closed, unchanged).

    PRESERVE. A column in `preserve` that arrives SILENT (see `silent`) while the
    current version holds a value keeps the held value - the versioned form of
    `store.upsert_preserving`'s `COALESCE(excluded.c, table.c)`. A changed value is
    a restatement and wins; silence is not a restatement. Without this, a release
    that omits a field opens a new version with the field null and the CURRENT row
    set - what "now" reads - has unsaid it, although the history still holds it.

    DATED_BY. A column that DATES the rest of the row (an upstream capture time) is
    carried forward only when every other column matches the held version after
    preservation. Carried across a restatement it would stamp the new content with
    the old content's date, which is a wrong as-of and worse than none.

    Every substitution, and every date that was NOT carried, is appended to
    `preserved` as (key, column, held_value, outcome), so silence stays visible.
    Default behaviour, with neither argument, is unchanged.

    Runs inside the caller's transaction; the caller commits.
    """
    label = label or f"file {file_id}"
    last = latest_version(conn, table, dataset, season, part)
    if version_ts < last:
        raise OutOfOrder(f"{label} is as of {version_ts:.0f}, but {table} "
                         f"{dataset}/{season} already holds {last:.0f}; "
                         f"use --rebuild to replay the archive in order")

    schema = _schema(schema_mod)
    cols = schema.columns(table)
    key = schema.keys(table)
    kidx = [cols.index(k) for k in key]

    preserve, dated_by = tuple(preserve), tuple(dated_by)
    unknown = [c for c in preserve + dated_by if c not in cols]
    if unknown:
        raise ValueError(f"{table}: not columns: {unknown}")
    if set(preserve + dated_by) & set(key):
        raise ValueError(f"{table}: a key column cannot be preserved")
    pidx = [cols.index(c) for c in preserve]
    didx = [cols.index(c) for c in dated_by]
    rest = [i for i in range(len(cols)) if i not in didx]
    held_vals = {}

    current = {}
    extra = f", {', '.join(cols)}" if (pidx or didx) else ""
    for rowid, *vals in conn.execute(
            f"SELECT rowid, {', '.join(key)}, row_sha{extra} FROM {table} "
            f"WHERE {SCOPE} AND valid_to_ts IS NULL",
            (dataset, season, part)):
        k = tuple(vals[:len(key)])
        current[k] = (rowid, vals[len(key)])
        if extra:
            held_vals[k] = vals[len(key) + 1:]

    to_insert, seen, unchanged = [], set(), 0
    to_close = []
    for r in rows:
        k = tuple(r[i] for i in kidx)
        seen.add(k)
        hv = held_vals.get(k)
        if hv is not None:
            r = list(r)
            for i in pidx:
                if silent(r[i]) and not silent(hv[i]):
                    r[i] = hv[i]
                    if preserved is not None:
                        preserved.append((k, cols[i], hv[i], "kept"))
            for i in didx:
                if silent(r[i]) and not silent(hv[i]):
                    if all(r[j] == hv[j] for j in rest):
                        r[i] = hv[i]
                        outcome = "kept"
                    else:
                        outcome = "not_carried_row_restated"
                    if preserved is not None:
                        preserved.append((k, cols[i], hv[i], outcome))
            r = tuple(r)
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
        to_insert.append(tuple(r) + (dataset, season, part, file_id, sha, version_ts))

    for k, (rowid, _sha) in current.items():
        if k not in seen:
            to_close.append(rowid)

    conn.executemany(f"UPDATE {table} SET valid_to_ts=? WHERE rowid=?",
                     [(version_ts, rid) for rid in to_close])
    meta = ["src_dataset", "src_season", "src_part", "src_file_id", "row_sha", "valid_from_ts"]
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

