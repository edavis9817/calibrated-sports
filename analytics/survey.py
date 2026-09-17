"""Column coverage across the archived nflverse seasonal feeds, season by season.

    python -m analytics.survey --scan            # read the parquet, fill the table
    python -m analytics.survey --report          # the coverage cliffs
    python -m analytics.survey --silent-zeros    # the class no null check sees
    python -m analytics.survey --column air_yards
    python -m analytics.survey --dataset weekly_stats --column targets

FOUR DATASETS, NOT ONE. The play-by-play is the subject, but the defect this
survey exists to prevent - 2003-2008 targets shipping as zeros - is a
`stats_player_week` column, and a sweep reading only the PBP walks past its own
worked example. `snap_counts` and `pbp_participation` are here because every
on-field and per-snap analytic depends on them and both have hard era limits.

WHY THIS RUNS BEFORE ANY ANALYTIC. nflverse's play-by-play is one schema across
1999-2026, and a column that did not exist in 2003 is not absent from the 2003
file - it is PRESENT AND EMPTY, or present and filled with zeros. Air yards
begin 2006 for passing and 2009 for receiving; snap counts begin 2013. Those
three are known. Building a league history on a fourth one nobody checked is
exactly the defect that shipped 2003-08 targets as zeros: the column was there,
the query succeeded, and six seasons of a leaderboard were silently wrong.

THREE KINDS OF EMPTY, AND ONLY ONE OF THEM IS VISIBLE TO A NULL CHECK.

    null      the column is NULL on the row
    zero      the column is 0.0 or "" on the row
    nan       the column is a float NaN

A null cliff is loud - a mean skips it, a join drops it. A zero cliff is silent
and sums to a wrong answer. A NaN cliff is the worst of the three, because NaN
IS NOT NULL: `count()` counts it, `fill_null(0) != 0` is TRUE for it, and the
first version of this survey therefore scored a column of pure NaN as fully
informative.

That was not hypothetical. `stats_player_week.target_share` is NaN on every row
of 2003-2008 - targets divided by zero targets - and the survey read those six
seasons as 99.7% informative and the OTHER twenty-two as the anomaly. The
verdict came out exactly inverted, and it was found by the dead-ends detector
track C asked for on 2026-09-17. NaN is now excluded from both `nonnull` and
`informative`, and counted separately in `nan_n` so it stays visible instead of
being folded into a category that hides it.

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
DEFAULT_DATASET = "pbp"

# The seasonal nflverse assets this survey covers. The filename patterns are
# READ FROM `nflverse.DATASETS` rather than repeated here: one copy, so a
# release rename cannot leave the survey scanning a path that no longer exists
# while every other job has moved on.
DATASETS = ("pbp", "weekly_stats", "snap_counts", "participation")

SCHEMA = """
CREATE TABLE IF NOT EXISTS f_pbp_columns (
    dataset     TEXT    NOT NULL,   -- pbp | weekly_stats | snap_counts | participation
    season      INTEGER NOT NULL,
    column_name TEXT    NOT NULL,
    condition   TEXT    NOT NULL,   -- '' = every row; else a declared subset
    dtype       TEXT    NOT NULL,
    rows        INTEGER NOT NULL,   -- rows in the season file
    nonnull     INTEGER NOT NULL,   -- not NULL and not NaN
    informative INTEGER NOT NULL,   -- nonnull and not 0 / not ""
    nan_n       INTEGER NOT NULL,   -- float NaN: not null, and not information
    distinct_n  INTEGER,            -- distinct non-null values
    pull_date   TEXT    NOT NULL,   -- which nflverse pull this was measured on
    scanned_ts  INTEGER NOT NULL,
    PRIMARY KEY (dataset, season, column_name, condition)
);
CREATE INDEX IF NOT EXISTS ix_f_pbp_columns_col
    ON f_pbp_columns(dataset, column_name, condition, season);

-- A stale database read by newer code, or the reverse, is the failure track C
-- hit on 2026-09-17: a key column the reader did not filter on, silently
-- doubling every count. `profiles` asserts on the row shape, and this says out
-- loud which shape it was written with.
CREATE TABLE IF NOT EXISTS f_survey_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS f_pbp_files (
    dataset    TEXT    NOT NULL,
    season     INTEGER NOT NULL,
    path       TEXT NOT NULL,
    pull_date  TEXT NOT NULL,
    rows       INTEGER NOT NULL,
    columns_n  INTEGER NOT NULL,
    bytes      INTEGER NOT NULL,
    games      INTEGER,
    scanned_ts INTEGER NOT NULL,
    PRIMARY KEY (dataset, season)
);
"""


# CONDITIONAL COVERAGE, AND WHY IT HAD TO EXIST.
#
# A column's coverage over ALL rows cannot see a ramp. `receiver_player_id` is
# non-null on 21.8% of 2005 plays against 38.7% at reference - 56%, which clears
# every absent/thin threshold - and what it actually means is that A TARGET
# CANNOT BE IDENTIFIED ON AN INCOMPLETION for six seasons. Measured on the rows
# that can carry it, pass attempts, the same column reads under 1% and the cliff
# is unmissable.
#
# `analytics.metrics.derive_range` reads these, so a metric needing targets is
# bounded at 2009 BY MEASUREMENT. Without them the range came back "the whole
# archive, no input binds it" - a hand-written 2009 by another name, and the
# exact defect this package exists to prevent.
#
# Declared, not inferred. Every entry is a scan, so keep the list short.
CONDITIONS = {
    "pbp": (
        ("receiver_player_id", "pass_attempt"),
        ("receiver_player_id", "incomplete_pass"),
        ("receiver_player_id", "complete_pass"),
        ("air_yards", "pass_attempt"),
        ("yards_after_catch", "complete_pass"),
        ("rusher_player_id", "rush_attempt"),
        ("passer_player_id", "pass_attempt"),
    ),
}

# What each condition needs on the row, so a renamed upstream column fails here
# rather than filtering silently to nothing.
CONDITION_COLUMNS = {
    "pass_attempt": ("pass_attempt",),
    "complete_pass": ("complete_pass",),
    "rush_attempt": ("rush_attempt",),
    "incomplete_pass": ("pass_attempt", "complete_pass"),
}


def _condition_expr(name):
    import polars as pl
    if name == "incomplete_pass":
        return ((pl.col("pass_attempt").fill_null(0) == 1)
                & (pl.col("complete_pass").fill_null(0) == 0))
    return pl.col(name).fill_null(0) == 1


def scan_conditions(path, dataset):
    """[(column, condition, rows, nonnull, informative, distinct, dtype)]."""
    import polars as pl
    out = []
    lf = pl.scan_parquet(path)
    schema = lf.collect_schema()
    names = set(schema.names())
    for column, cond in CONDITIONS.get(dataset, ()):
        need = {column} | set(CONDITION_COLUMNS[cond])
        missing = need - names
        if missing:
            raise KeyError("condition %s|%s needs %s, absent from %s"
                           % (column, cond, sorted(missing), path))
        dt = schema[column]
        nan, nn, inf = _counts(pl, column, dt)
        r = (lf.filter(_condition_expr(cond))
             .select(pl.len().alias("rows"),
                     nn.cast(pl.Int64).alias("nn"),
                     inf.cast(pl.Int64).alias("inf"),
                     nan.cast(pl.Int64).alias("na"),
                     pl.col(column).n_unique().alias("dn"))
             .collect().to_dicts()[0])
        out.append((column, cond, int(r["rows"]), int(r["nn"]),
                    int(r["inf"]), int(r["dn"]), str(dt), int(r["na"])))
    return out


# Bumped whenever a column or key changes in `f_pbp_columns`. 1 had no
# `condition`; 2 added it; 3 excludes NaN and stores `nan_n`.
SCHEMA_VERSION = "3"


def check_schema(con):
    """Raise unless the database was written by this version of the survey."""
    row = con.execute("SELECT value FROM f_survey_meta WHERE key='schema_version'"
                      ).fetchone() if _has_meta(con) else None
    got = row[0] if row else "1"
    if got != SCHEMA_VERSION:
        raise RuntimeError(
            "f_pbp_columns was written at schema_version %s and this code is "
            "%s. Re-run `python -m analytics.survey --scan`; reading across "
            "the change gives numbers rather than an error." % (got, SCHEMA_VERSION))


def _has_meta(con):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='f_survey_meta'").fetchone())


def asset_pattern(dataset):
    """The filename pattern for a seasonal nflverse dataset, from the registry."""
    import nflverse
    ds = nflverse.DATASETS[dataset]
    if not ds.seasonal:
        raise ValueError("%s is not a seasonal dataset" % dataset)
    return ds.filename


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
        nan, nn, inf = _counts(pl, name, dt)
        exprs.append(nn.cast(pl.Int64).alias("nn::" + name))
        exprs.append(inf.cast(pl.Int64).alias("in::" + name))
        exprs.append(nan.cast(pl.Int64).alias("na::" + name))
        exprs.append(pl.col(name).n_unique().alias("dn::" + name))
    row = lf.select(exprs).collect().to_dicts()[0]
    cols = [(name, str(dt), int(row["nn::" + name]), int(row["in::" + name]),
             int(row["dn::" + name]), int(row["na::" + name]))
            for name, dt in schema.items()]
    return int(row["__rows"]), row.get("__games"), cols


def _counts(pl, name, dt):
    """(nan, nonnull, informative) expressions for one column.

    NaN IS NOT NULL AND IS NOT INFORMATION. `count()` counts it and
    `fill_null(0) != 0` is true for it, so both have to say so explicitly or a
    column of pure NaN reads as fully populated - see the module docstring for
    the six seasons that did.
    """
    col = pl.col(name)
    if dt == pl.Boolean:
        return pl.lit(0), col.count(), col.fill_null(False).sum()
    if dt.is_float():
        nan = col.is_not_null().and_(col.is_nan()).sum()
        real = col.is_not_null().and_(col.is_nan().not_())
        return nan, real.sum(), real.and_(col.fill_null(0) != 0).sum()
    if dt.is_numeric():
        return pl.lit(0), col.count(), (col.fill_null(0) != 0).sum()
    if dt == pl.String:
        return (pl.lit(0), col.count(),
                (col.fill_null("").str.len_chars() > 0).sum())
    return pl.lit(0), col.count(), col.count()


def scan_files(con, items, verbose=True):
    """Scan an explicit list of (dataset, season, path, pull_date) into `con`.

    THE SCAN DOES NOT ENUMERATE. Track C asked for this seam on 2026-09-17:
    `run_scan` found its own files through the nflverse registry and the NFL
    mirror layout, so a feed stored anywhere else could not use any of it and
    they were re-implementing the loop. The measurement is the valuable part
    and it has nothing to do with where the file came from.

    Taking the connection too, rather than opening `analytics.db`, is the other
    half: a caller with its own store writes into its own store. `run_scan`
    keeps the NFL enumeration and is now a thin wrapper.
    """
    con.executescript(SCHEMA)
    con.execute("INSERT OR REPLACE INTO f_survey_meta VALUES ('schema_version',?)",
                (SCHEMA_VERSION,))
    stats = {"seasons": 0, "rows": 0, "columns": 0, "datasets": 0}
    now = int(time.time())
    seen_datasets = set()
    for dataset, season, path, pull in items:
        t0 = time.time()
        rows, games, cols = scan_season(path)
        con.execute(
            "INSERT OR REPLACE INTO f_pbp_files VALUES (?,?,?,?,?,?,?,?,?)",
            (dataset, season, path, pull, rows, len(cols),
             os.path.getsize(path), games, now))
        con.execute("DELETE FROM f_pbp_columns WHERE dataset=? AND season=?",
                    (dataset, season))
        con.executemany(
            "INSERT INTO f_pbp_columns VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(dataset, season, c, "", d, rows, nn, inf, na, dn, pull, now)
             for c, d, nn, inf, dn, na in cols])
        con.executemany(
            "INSERT INTO f_pbp_columns VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(dataset, season, c, cond, dt, n_rows, nn, inf, na, dn, pull, now)
             for c, cond, n_rows, nn, inf, dn, dt, na
             in scan_conditions(path, dataset)])
        con.commit()
        seen_datasets.add(dataset)
        stats["seasons"] += 1
        stats["rows"] += rows
        stats["columns"] = max(stats["columns"], len(cols))
        if verbose:
            print("   %s %d  rows %7d  games %4d  cols %3d  pull %s  %.1fs"
                  % (dataset, season, rows, games or 0, len(cols), pull,
                     time.time() - t0), flush=True)
    stats["datasets"] = len(seen_datasets)
    if stats["seasons"] == 0:
        raise SystemExit("scan_files was given nothing to scan. A scan that "
                         "reads nothing and exits 0 is not a scan.")
    return stats


def nflverse_files(seasons=None, datasets=None):
    """[(dataset, season, path, pull_date)] for the NFL mirror. Enumeration
    only - the measurement is `scan_files`, which takes any list."""
    out = []
    for dataset in (datasets or DATASETS):
        files = [f for f in paths.seasonal_files(asset_pattern(dataset))
                 if not seasons or f[0] in seasons]
        if not files:
            raise SystemExit(
                "no %s files in the archive - nothing to scan." % dataset)
        out.extend((dataset, season, path, pull) for season, path, pull in files)
    return out


def run_scan(seasons=None, datasets=None, verbose=True, con=None):
    """The NFL convenience wrapper: enumerate the mirror, then scan it."""
    return scan_files(con or paths.connect(),
                      nflverse_files(seasons, datasets), verbose)


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

def coverage(con, column, dataset=DEFAULT_DATASET, condition=""):
    check_schema(con)
    return con.execute(
        "SELECT season, rows, nonnull, informative, distinct_n, dtype, nan_n "
        "FROM f_pbp_columns WHERE dataset=? AND column_name=? AND condition=? "
        "ORDER BY season", (dataset, column, condition)).fetchall()


# A season is scored against the column's own REFERENCE level, not against an
# absolute share, because most of these columns are event flags that are 0 on
# 99% of plays by construction. `interception` at 1% of rows is full.
ABSENT = 0.02        # <= 2% of reference: the column is not there
THIN = 0.35          # <= 35% of reference: present but materially under-filled


def profiles(con, seasons=None, dataset=DEFAULT_DATASET, condition=""):
    """{column: (dtype, [(season, rows, nonnull_share, informative_share)])}.

    ASSERTS ON THE SHAPE OF WHAT IT READ. Every (column, season) must appear
    exactly once. Reported by track C on 2026-09-17, and it is the sharpest
    failure this package has had:

    `condition` was added to the primary key AFTER the previous push, so a
    checkout of the older code reading a newer database selected both the
    unconditional rows and the conditional ones, got each column-season twice,
    and printed `air_yards` absent in "1999, 1999-2000, 2000-2001, ..." instead
    of 1999-2005. NOTHING FAILED. The query succeeded, the report rendered, the
    numbers were wrong - which is the same shape as the defect the whole survey
    exists to prevent, one layer up.

    A version marker alone would not have caught it: the older code would not
    have known to look. Asserting that the rows are the shape the caller
    believes they are does catch it, and catches the next key column too.
    """
    check_schema(con)
    q = ("SELECT column_name, dtype, season, rows, nonnull, informative "
         "FROM f_pbp_columns WHERE dataset=? AND condition=? "
         "ORDER BY column_name, season")
    out, seen = {}, set()
    for col, dt, season, n, nn, inf in con.execute(q, (dataset, condition)):
        if seasons and season not in seasons:
            continue
        if (col, season) in seen:
            raise RuntimeError(
                "f_pbp_columns returned %s/%s twice for dataset=%r "
                "condition=%r. The table has a key column this code does not "
                "filter on - re-read the schema rather than trusting the "
                "counts, which would be silently doubled."
                % (col, season, dataset, condition))
        seen.add((col, season))
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


def anomalies(con, min_reference=0.0005, seasons=None,
              dataset=DEFAULT_DATASET, condition=""):
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
    for col, (dt, rows) in profiles(con, seasons, dataset, condition).items():
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


def silent_zeros(con, min_run=2, min_reference=0.0005, seasons=None,
                 dataset=DEFAULT_DATASET, condition=""):
    """THE SILENT-ZERO CLASS: non-null, and exactly zero, for a run of seasons.

    This is the sharpest shape in the archive and the only one no null check
    can see. `qb_hit` is 0.021 of plays in 2002, EXACTLY 0.000 in 2003, 2004
    and 2005, and 0.047 in 2006 - and it is non-null on 96% of rows throughout.
    A mean skips a null; a mean over a column of real zeros returns a number,
    and the number is wrong. It has already shipped once on this site, as
    2003-08 targets.

    A column qualifies when, for at least `min_run` CONSECUTIVE seasons:
      - `informative` is EFFECTIVELY zero - at or below `ABSENT` of the
        column's own reference level - and
      - `nonnull` is not: the column is present, populated, and zero, and
      - it is materially non-zero in other seasons.

    "EFFECTIVELY", NOT "EXACTLY", AND THE FIRST VERSION HAD IT WRONG. Required
    to be exactly 0, this sweep returned two columns and missed the defect it
    is named after: league `targets` for 2003-2008 is 3, 5, 0, 67, 14, 17, so
    five of the six seasons are not exactly zero and a strict test walks past
    them. A season carrying 3 rows out of 46,811 is zero for every purpose
    except the comparison that decides whether to look at it.

    `rows_in_run` is reported for exactly that reason - a reader should see
    "3 rows" rather than take the word "zero" on trust.

    Returns [(column, dtype, runs, reference, nonnull_share, rows_in_run)]
    sorted by the length of the longest run, because that is how much history
    a sum over the column silently loses.
    """
    out = []
    for col, (dt, rows) in profiles(con, seasons, dataset, condition).items():
        by_season = {s: (n, nn, sh) for s, n, nn, sh in rows}
        ref = reference([sh for _s, _n, _nn, sh in rows])
        if ref < min_reference:
            continue
        zero = [s for s, _n, nn, sh in rows if sh <= ABSENT * ref and nn > 0.02]
        runs = [r for r in _runs(zero) if r[1] - r[0] + 1 >= min_run]
        if not runs:
            continue
        in_run = [s for a, b in runs for s in range(a, b + 1)]
        share = max(by_season[s][1] for s in in_run)
        n_rows = sum(int(round(by_season[s][0] * by_season[s][2]))
                     for s in in_run)
        out.append((col, dt, runs, ref, share, n_rows))
    return sorted(out, key=lambda r: -max(b - a + 1 for a, b in r[2]))


def format_silent_zeros(rows):
    if not rows:
        return "(none)"
    w = max([len(r[0]) for r in rows] + [12])
    head = ("%s  %-18s  %5s  %8s  %s"
            % ("column".ljust(w), "zero seasons", "ref", "non-null",
               "informative rows in the run"))
    lines = [head, "-" * len(head)]
    for col, _dt, runs, ref, share, n_rows in rows:
        span = ",".join("%d-%d" % (a, b) if a != b else str(a) for a, b in runs)
        lines.append("%s  %-18s  %5.3f  %8.3f  %d"
                     % (col.ljust(w), span, ref, share, n_rows))
    return "\n".join(lines)


def dead_ends(con, dataset=DEFAULT_DATASET, condition=""):
    """Columns absent at BOTH the first and the last season, populated between.

    Reported by track C on 2026-09-17: their `qb_hurry` is zero for 2004-2006
    AND 2014-2020 around a populated middle, and every cliff detector written
    so far scans from one edge, so an interior-populated column is invisible to
    all of them.

    `anomalies` scores each season against the column's own median-of-top-five
    reference and never asks where the season sits, so it should already flag
    both ends - but "should" is reasoning about a detector rather than checking
    it. This names the shape explicitly so the claim is a query, and
    `tests/test_analytics_survey.py` drives the exact profile through it.

    Returns [(column, first_run, last_run, reference, n_covered_seasons)].
    """
    out = []
    for col, d in anomalies(con, dataset=dataset, condition=condition).items():
        gone = set(d["absent"]) | set(d["thin"])
        if not gone:
            continue
        seasons = [s for s, _sh in d["seasons"]]
        if not seasons or seasons[0] not in gone or seasons[-1] not in gone:
            continue
        covered = [s for s in seasons if s not in gone]
        if not covered:
            continue                    # never populated; not this shape
        runs = _runs(sorted(gone))
        out.append((col, runs[0], runs[-1], d["reference"], len(covered)))
    return sorted(out, key=lambda r: -r[4])


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
    ap.add_argument("--dead-ends", action="store_true",
                    help="columns absent at BOTH ends with a populated middle "
                         "- the shape an edge-scanning detector cannot see")
    ap.add_argument("--silent-zeros", action="store_true",
                    help="columns non-null and effectively 0 for a run of "
                         "seasons - the class no null check can see")
    ap.add_argument("--steps", action="store_true",
                    help="also print every season-over-season step past 40%%")
    ap.add_argument("--condition", default="",
                    help="a declared subset, e.g. pass_attempt; "
                         "default: every row")
    ap.add_argument("--dataset", default=None,
                    help="one of %s; default all for --scan, %s otherwise"
                         % (", ".join(DATASETS), DEFAULT_DATASET))
    a = ap.parse_args(argv)
    if a.dataset and a.dataset not in DATASETS:
        raise SystemExit("unknown dataset %r; known: %s"
                         % (a.dataset, ", ".join(DATASETS)))

    if a.scan:
        print("scanning the nflverse mirror at " + paths.archive_root())
        s = run_scan(seasons=set(a.season) if a.season else None,
                     datasets=[a.dataset] if a.dataset else None)
        print("scanned %d datasets, %d season files, %d rows, %d columns -> %s"
              % (s["datasets"], s["seasons"], s["rows"], s["columns"],
                 paths.db_path()))

    ds = a.dataset or DEFAULT_DATASET
    con = paths.connect(read_only=not a.scan)
    if a.column:
        rows = coverage(con, a.column, ds, a.condition)
        if not rows:
            raise SystemExit("no coverage rows for %r in %s - scan first?"
                             % (a.column, ds))
        print("%s  [%s%s]"
              % (a.column, ds, " | " + a.condition if a.condition else ""))
        print("%6s %8s %9s %12s %9s %8s  dtype"
              % ("season", "rows", "nonnull", "informative", "distinct", "nan"))
        for season, n, nn, inf, dn, dt, na in rows:
            print("%6d %8d %9d %12d %9d %8d  %s"
                  % (season, n, nn, inf, dn or 0, na or 0, dt))
    if a.report:
        found = anomalies(con, dataset=ds, condition=a.condition)
        if not found:
            raise SystemExit("coverage report found NOTHING - scan first; an "
                             "empty report is not a result")
        total = con.execute("SELECT COUNT(DISTINCT column_name), "
                            "COUNT(DISTINCT season) FROM f_pbp_columns "
                            "WHERE dataset=? AND condition=?",
                            (ds, a.condition)).fetchone()
        absent = {c for c, d in found.items() if d["absent"]}
        thin = {c for c, d in found.items() if d["thin"]} - absent
        print("\n[%s] %d of %d columns over %d seasons carry a coverage "
              "anomaly: %d with absent seasons, %d thin only, %d step-only\n"
              % (ds, len(found), total[0], total[1], len(absent), len(thin),
                 len(found) - len(absent) - len(thin)))
        print(format_anomalies(found, steps=a.steps))
    if a.silent_zeros:
        for name in ([a.dataset] if a.dataset else DATASETS):
            rows = silent_zeros(con, dataset=name, condition=a.condition)
            print("\n[%s] %d columns are non-null and effectively zero for two "
                  "or more consecutive seasons\n" % (name, len(rows)))
            print(format_silent_zeros(rows))
    if a.dead_ends:
        for name in ([a.dataset] if a.dataset else DATASETS):
            rows = dead_ends(con, dataset=name, condition=a.condition)
            print("\n[%s] %d columns are absent at BOTH ends with a populated "
                  "middle" % (name, len(rows)))
            for col, first, last, ref, n_cov in rows:
                print("   %-34s dead %s and %s, %d seasons covered, ref %.3f"
                      % (col, fmt_runs(range(first[0], first[1] + 1)),
                         fmt_runs(range(last[0], last[1] + 1)), n_cov, ref))
    if not (a.scan or a.report or a.column or a.silent_zeros or a.dead_ends):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
