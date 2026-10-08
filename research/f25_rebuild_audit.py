"""f-25: what a track F rebuild changed. Read-only; every store is opened mode=ro.

    python -m research.f25_rebuild_audit --coverage <analytics.db>
    python -m research.f25_rebuild_audit --diff <before.db> <after.db>

`--coverage` prints row counts and the season's week coverage for every `f_`
table, and the build rows that say what each was built from. `--diff` compares
`f_metrics` and `f_metric_values` between a snapshot taken before a rebuild and
the store after it: keys added and removed, which envelope fields moved, and per
family how many values were added, removed, moved and left exactly as they were.

The figures in the f-25 report came from this script, run on the snapshot
`analytics.before.db` (sqlite backup API, 2026-10-08 05:32 UTC) and the live
store after `python -m analytics.refresh --rebuild --all-seasons`.

A family whose envelope `unit`, `season_from` or `requires` moves, or whose
interval `method` changes, changed DEFINITION and not only data: that is what
put `role` and `pace` in `analytics.refresh.HELD`. The `moved, same n` column
did NOT separate the two on this store (0 for `pace`, 36 for `role`), so it is
printed and not relied on.
"""
import argparse
import collections
import os
import sqlite3
import sys

ENVELOPE = ("metric", "label", "unit", "subject_type", "block", "basis", "slice_kind",
            "shares_denominator", "season_from", "season_to", "range_note", "availability",
            "requires")


def ro(path):
    if not os.path.exists(path):
        raise SystemExit("no store at %s" % path)
    return sqlite3.connect("file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro",
                           uri=True, timeout=30)


def coverage(path, season):
    con = ro(path)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    if not tables:
        raise SystemExit("%s holds no tables - nothing was measured" % path)
    print("%-22s %10s %10s  %s" % ("table", "rows", str(season), "weeks of %d" % season))
    for t in tables:
        cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
        rows = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        in_season, weeks = "", ""
        if "season" in cols:
            in_season = con.execute('SELECT COUNT(*) FROM "%s" WHERE season=?' % t,
                                    (season,)).fetchone()[0]
        if "season" in cols and "week" in cols:
            weeks = con.execute('SELECT week, COUNT(*) FROM "%s" WHERE season=? GROUP BY week '
                                "ORDER BY week" % t, (season,)).fetchall()
        print("%-22s %10d %10s  %s" % (t, rows, in_season, weeks))
    for t, sql in (("f_spine_build", "SELECT season, pull_date, usage_rows, pace_rows, games "
                                     "FROM f_spine_build WHERE season >= %d" % (season - 1)),
                   ("f_ngs_build", "SELECT family, pull_date, seasons, weekly_rows FROM f_ngs_build"),
                   ("f_pbp_files", "SELECT dataset, season, pull_date FROM f_pbp_files "
                                   "WHERE season = %d" % season)):
        if t in tables:
            print(t, con.execute(sql).fetchall())
    if "f_metrics" in tables:
        print("f_metrics by family:", con.execute(
            "SELECT substr(metric, 1, instr(metric, '.') - 1), availability, COUNT(*), "
            "date(MIN(computed_ts), 'unixepoch'), date(MAX(computed_ts), 'unixepoch') "
            "FROM f_metrics GROUP BY 1, 2").fetchall())
    con.close()


def _values(con):
    out = collections.defaultdict(dict)
    for m, s, sl, est, n, method in con.execute(
            "SELECT metric, subject_id, slice, est, n, method FROM f_metric_values"):
        out[m][(s, sl)] = (est, n, method)
    return out


def diff(before, after):
    a, b = ro(before), ro(after)
    sel = "SELECT %s FROM f_metrics" % ", ".join(ENVELOPE)
    A = {r[0]: r for r in a.execute(sel)}
    B = {r[0]: r for r in b.execute(sel)}
    if not A or not B:
        raise SystemExit("one side holds no metrics (%d before, %d after) - nothing compared"
                         % (len(A), len(B)))
    print("metrics: %d before, %d after; added %s; removed %s"
          % (len(A), len(B), sorted(set(B) - set(A)), sorted(set(A) - set(B))))
    moved = collections.defaultdict(list)
    for k in sorted(set(A) & set(B)):
        for i, name in enumerate(ENVELOPE):
            if A[k][i] != B[k][i]:
                moved[name].append((k, A[k][i], B[k][i]))
    print("envelope fields that moved: %s" % {n: len(v) for n, v in moved.items()})
    for name, rows in moved.items():
        if name == "range_note":
            continue                      # carries fitted medians; moves with the data
        for k, x, y in rows:
            print("  %s %s: %s -> %s" % (name, k, str(x)[:70], str(y)[:70]))
    VA, VB = _values(a), _values(b)
    print("%-22s %7s %8s %8s %7s %8s %7s %9s %12s  %s"
          % ("family", "metrics", "before", "after", "added", "removed", "moved", "unchanged",
             "moved,same n", "interval methods after"))
    fam = collections.defaultdict(collections.Counter)
    methods = collections.defaultdict(collections.Counter)
    for m in sorted(set(VA) | set(VB)):
        f, x, y = m.split(".")[0], VA.get(m, {}), VB.get(m, {})
        c, both = fam[f], set(VA.get(m, {})) & set(VB.get(m, {}))
        c["metrics"] += 1
        c["before"] += len(x)
        c["after"] += len(y)
        c["added"] += len(set(y) - set(x))
        c["removed"] += len(set(x) - set(y))
        c["moved"] += sum(1 for k in both if x[k][0] != y[k][0])
        c["unchanged"] += sum(1 for k in both if x[k][0] == y[k][0])
        c["moved_same_n"] += sum(1 for k in both if x[k][0] != y[k][0] and x[k][1] == y[k][1])
        methods[f].update(v[2] for v in y.values())
    for f in sorted(fam):
        c = fam[f]
        print("%-22s %7d %8d %8d %7d %8d %7d %9d %12d  %s"
              % (f, c["metrics"], c["before"], c["after"], c["added"], c["removed"], c["moved"],
                 c["unchanged"], c["moved_same_n"], dict(methods[f])))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coverage", metavar="DB")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--season", type=int, default=2026)
    a = ap.parse_args(argv)
    if not (a.coverage or a.diff):
        ap.print_help()
        return 2
    if a.coverage:
        coverage(a.coverage, a.season)
    if a.diff:
        diff(*a.diff)
    return 0


if __name__ == "__main__":
    sys.exit(main())
