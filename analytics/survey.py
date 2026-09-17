"""Column coverage across the archived play-by-play, season by season.

    python -m analytics.survey --scan          # read the parquet, fill the table
    python -m analytics.survey --report        # the coverage cliffs
    python -m analytics.survey --column air_yards

WHY THIS RUNS BEFORE ANY ANALYTIC. nflverse's play-by-play is one schema across
1999-2026, and a column that did not exist in 2003 is not absent from the 2003
file - it is PRESENT AND EMPTY, or present and filled with zeros. Air yards
begin 2006 for passing and 2009 for receiving; snap counts begin 2013. Those
three are known. Building a league history on a fourth one nobody checked is
exactly the defect that shipped 2003-08 targets as zeros: the column was there,
the query succeeded, and six seasons of a leaderboard were silently wrong.

TWO KINDS OF EMPTY, AND ONLY ONE OF THEM IS VISIBLE TO A NULL CHECK.

    null      the column is NULL on the row
    zero      the column is 0.0 or "" on the row

A null cliff is loud - a mean skips it, a join drops it. A zero cliff is silent
and sums to a wrong answer, so both are counted separately here and the report
says which kind each cliff is.

WHAT IS STORED IS COUNTS, NOT RATES. `analytics.gate` would demand an interval
on a stored `null_rate`, and it would be right to: the name does not say whether
the denominator is on the row. Counts compose, and the rate is one division at
read time. Same rule as "components, not derived totals".
"""
import argparse
import os
import sys
import time

from analytics import paths

TABLE = "f_pbp_columns"

SCHEMA = """
CREATE TABLE IF NOT EXISTS f_pbp_columns (
    season      INTEGER NOT NULL,
    column_name TEXT    NOT NULL,
    dtype       TEXT    NOT NULL,
    rows        INTEGER NOT NULL,   -- rows in the season file
    nonnull     INTEGER NOT NULL,   -- rows where the column is not NULL
    informative INTEGER NOT NULL,   -- not NULL and not 0 / not ""
    distinct_n  INTEGER,            -- distinct non-null values
    pull_date   TEXT    NOT NULL,   -- which nflverse pull this was measured on
    scanned_ts  INTEGER NOT NULL,
    PRIMARY KEY (season, column_name)
);
CREATE INDEX IF NOT EXISTS ix_f_pbp_columns_col ON f_pbp_columns(column_name, season);

CREATE TABLE IF NOT EXISTS f_pbp_files (
    season     INTEGER PRIMARY KEY,
    path       TEXT NOT NULL,
    pull_date  TEXT NOT NULL,
    rows       INTEGER NOT NULL,
    columns_n  INTEGER NOT NULL,
    bytes      INTEGER NOT NULL,
    games      INTEGER,
    scanned_ts INTEGER NOT NULL
);
"""


def _pl():
    import polars as pl
    return pl


def scan_season(path):
    """(rows, games, [(col, dtype, nonnull, informative, distinct), ...])."""
    pl = _pl()
    lf = pl.scan_parquet(path)
    schema = lf.collect_schema()
    exprs = [pl.len().alias("__rows")]
    names = list(schema.names())
    if "game_id" in names:
        exprs.append(pl.col("game_id").n_unique().alias("__games"))
    for name, dt in schema.items():
        exprs.append(pl.col(name).count().alias("nn::" + name))
        exprs.append(pl.col(name).n_unique().alias("dn::" + name))
        if dt == pl.Boolean:
            inf = pl.col(name).fill_null(False).sum()
        elif dt.is_numeric():
            inf = (pl.col(name).fill_null(0) != 0).sum()
        elif dt == pl.String:
            inf = (pl.col(name).fill_null("").str.len_chars() > 0).sum()
        else:
            inf = pl.col(name).count()
        exprs.append(inf.cast(pl.Int64).alias("in::" + name))
    row = lf.select(exprs).collect().to_dicts()[0]
    cols = [(name, str(dt), int(row["nn::" + name]), int(row["in::" + name]),
             int(row["dn::" + name]))
            for name, dt in schema.items()]
    return int(row["__rows"]), row.get("__games"), cols


def run_scan(seasons=None, verbose=True):
    con = paths.connect()
    con.executescript(SCHEMA)
    files = [f for f in paths.pbp_files() if not seasons or f[0] in seasons]
    if not files:
        raise SystemExit("no play-by-play in the archive - nothing scanned")
    stats = {"seasons": 0, "rows": 0, "columns": 0}
    now = int(time.time())
    for season, path, pull in files:
        t0 = time.time()
        rows, games, cols = scan_season(path)
        con.execute("INSERT OR REPLACE INTO f_pbp_files VALUES (?,?,?,?,?,?,?,?)",
                    (season, path, pull, rows, len(cols),
                     os.path.getsize(path), games, now))
        con.execute("DELETE FROM f_pbp_columns WHERE season=?", (season,))
        con.executemany(
            "INSERT INTO f_pbp_columns VALUES (?,?,?,?,?,?,?,?,?)",
            [(season, c, d, rows, nn, inf, dn, pull, now)
             for c, d, nn, inf, dn in cols])
        con.commit()
        stats["seasons"] += 1
        stats["rows"] += rows
        stats["columns"] = max(stats["columns"], len(cols))
        if verbose:
            print("  %d  rows %7d  games %4d  cols %3d  pull %s  %.1fs"
                  % (season, rows, games or 0, len(cols), pull, time.time() - t0),
                  flush=True)
    return stats


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def coverage(con, column):
    return con.execute(
        "SELECT season, rows, nonnull, informative, distinct_n, dtype "
        "FROM f_pbp_columns WHERE column_name=? ORDER BY season",
        (column,)).fetchall()


# A season is scored against the column's own REFERENCE level, not against an
# absolute share, because most of these columns are event flags that are 0 on
# 99% of plays by construction. `interception` at 1% of rows is full.
ABSENT = 0.02        # <= 2% of reference: the column is not there
THIN = 0.35          # <= 35% of reference: present but materially under-filled


def profiles(con, seasons=None):
    """{column: (dtype, [(season, rows, nonnull_share, informative_share)])}."""
    q = ("SELECT column_name, dtype, season, rows, nonnull, informative "
         "FROM f_pbp_columns ORDER BY column_name, season")
    out = {}
    for col, dt, season, n, nn, inf in con.execute(q):
        if seasons and season not in seasons:
            continue
        out.setdefault(col, (dt, []))[1].append(
            (season, n, nn / n if n else 0.0, inf / n if n else 0.0))
    return out


def reference(shares):
    """The level a full season of this column looks like.

    The MEDIAN OF THE TOP FIVE seasons, not the max. A single anomalous season
    - 2001-02's `end_yard_line`, populated in two seasons out of twenty-eight -
    sets a max that makes every other season look like a cliff, which inverts
    the finding. Five is enough that a two-season artifact cannot be the
    reference and few enough that a long ramp still measures against its top.
    """
    top = sorted(shares, reverse=True)[:5]
    if not top:
        return 0.0
    return top[len(top) // 2] if len(top) % 2 else (top[len(top) // 2 - 1] + top[len(top) // 2]) / 2


def anomalies(con, min_reference=0.0005, seasons=None):
    """Every column-season that is absent or thin against its own reference.

    Returns {column: {"dtype", "reference", "absent": [...], "thin": [...],
    "kind", "steps": [(season, before, after)]}}.

    THREE SHAPES, AND THE FIRST VERSION OF THIS ONLY CAUGHT ONE.

    - An ONSET cliff: empty early, full later. `air_yards` from 2006.
    - A MID-SERIES hole: full, empty, full. `targets` 2003-2008, which is the
      defect this survey exists to prevent and which a leading-edge-only
      detector does not see at all.
    - A COLLAPSE: full early, thinning at the live end.
      `tackle_with_assist` 0.075 in 2023 to 0.007 in 2026, while
      `assist_tackle` rises - a reclassification, not a missing feed.

    `steps` catches the fourth shape, a RAMP that never reaches the floor:
    `receiver_player_id` holds 0.22 through 2003-2008 against 0.38 either side,
    which is 57% of reference and passes every threshold while meaning that a
    target cannot be attributed on an incompletion.
    """
    out = {}
    for col, (dt, rows) in profiles(con, seasons).items():
        shares = [sh for _s, _n, _nn, sh in rows]
        ref = reference(shares)
        if ref < min_reference:
            # Either never populated, or populated in FEWER THAN FIVE SEASONS -
            # which the median-of-top-five reference cannot represent and which
            # the first version therefore dropped silently. `end_yard_line` is
            # filled in 2001-2002 and empty in the other twenty-six; a report
            # that says nothing about it is the "exit 0 is not a result"
            # failure. Report it as its own class, naming the seasons it has.
            peak = max(shares) if shares else 0.0
            if peak < min_reference:
                continue                # never populated anywhere
            covered = [s for s, _n, _nn, sh in rows if sh > THIN * peak]
            out[col] = {"dtype": dt, "reference": peak, "absent": [],
                        "thin": [], "kind": "sparse", "steps": [],
                        "covered": covered,
                        "seasons": [(s, sh) for s, _n, _nn, sh in rows]}
            continue
        absent = [s for s, _n, _nn, sh in rows if sh <= ABSENT * ref]
        thin = [s for s, _n, _nn, sh in rows
                if ABSENT * ref < sh <= THIN * ref]
        steps = []
        for (s0, _n0, _q0, a), (s1, _n1, _q1, b) in zip(rows, rows[1:]):
            if max(a, b) > 0.02 * ref and min(a, b) < 0.6 * max(a, b):
                steps.append((s1, a, b))
        if not (absent or thin or steps):
            continue
        nn_where_absent = [nn for s, _n, nn, _sh in rows if s in absent]
        kind = ("null" if nn_where_absent and max(nn_where_absent) <= 0.02
                else "zero" if nn_where_absent else "step")
        out[col] = {"dtype": dt, "reference": ref, "absent": absent,
                    "thin": thin, "kind": kind, "steps": steps,
                    "covered": [s for s, _n, _nn, sh in rows
                                if sh > THIN * ref],
                    "seasons": [(s, sh) for s, _n, _nn, sh in rows]}
    return out


def _runs(seasons):
    """[(first, last)] for consecutive runs, so 28 seasons print as a range."""
    out = []
    for s in sorted(seasons):
        if out and s == out[-1][1] + 1:
            out[-1][1] = s
        else:
            out.append([s, s])
    return [(a, b) for a, b in out]


def fmt_runs(seasons):
    return ",".join(str(a) if a == b else "%d-%d" % (a, b)
                    for a, b in _runs(seasons)) or "-"


def format_anomalies(found, steps=False):
    if not found:
        return "(none)"
    w = max(len(c) for c in found)
    head = ("%s  %5s  %-13s  %-20s  %s"
            % ("column".ljust(w), "ref", "absent", "thin / covers", "kind"))
    lines = [head, "-" * len(head)]
    for col in sorted(found, key=lambda c: (-len(found[c]["absent"]), c)):
        d = found[col]
        detail = ("covers " + fmt_runs(d["covered"]) if d["kind"] == "sparse"
                  else fmt_runs(d["thin"]))
        lines.append("%s  %5.3f  %-13s  %-20s  %s"
                     % (col.ljust(w), d["reference"], fmt_runs(d["absent"]),
                        detail, d["kind"]))
        if steps and d["steps"]:
            for s, a, b in d["steps"]:
                lines.append("%s     step %d: %.3f -> %.3f"
                             % (" " * w, s, a, b))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--column")
    ap.add_argument("--season", type=int, action="append")
    ap.add_argument("--steps", action="store_true",
                    help="also print every season-over-season step past 40%%")
    a = ap.parse_args(argv)

    if a.scan:
        print("scanning the play-by-play mirror at " + paths.archive_root())
        s = run_scan(seasons=set(a.season) if a.season else None)
        print("scanned %d seasons, %d plays, %d columns -> %s"
              % (s["seasons"], s["rows"], s["columns"], paths.db_path()))

    con = paths.connect(read_only=not a.scan)
    if a.column:
        rows = coverage(con, a.column)
        if not rows:
            raise SystemExit("no coverage rows for %r - scan first?" % a.column)
        print(a.column)
        print("%6s %8s %9s %12s %9s  dtype"
              % ("season", "rows", "nonnull", "informative", "distinct"))
        for season, n, nn, inf, dn, dt in rows:
            print("%6d %8d %9d %12d %9d  %s" % (season, n, nn, inf, dn or 0, dt))
    if a.report:
        found = anomalies(con)
        if not found:
            raise SystemExit("coverage report found NOTHING - scan first; an "
                             "empty report is not a result")
        total = con.execute("SELECT COUNT(DISTINCT column_name), "
                            "COUNT(DISTINCT season) FROM f_pbp_columns").fetchone()
        absent = {c for c, d in found.items() if d["absent"]}
        thin = {c for c, d in found.items() if d["thin"]} - absent
        print("\n%d of %d columns over %d seasons carry a coverage anomaly: "
              "%d with absent seasons, %d thin only, %d step-only\n"
              % (len(found), total[0], total[1], len(absent), len(thin),
                 len(found) - len(absent) - len(thin)))
        print(format_anomalies(found, steps=a.steps))
    if not (a.scan or a.report or a.column):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
