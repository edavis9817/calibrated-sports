"""a-76: what the change-point depth-chart table can and cannot reconstruct, PER COLUMN.

    python -m research.a76_depth_asof_columns build   --store <new dir> --raw <raw dir> [...] --pulls last|all
    python -m research.a76_depth_asof_columns compare --store <dir>     --raw <raw dir> [...]

a-71 compared the as-of rebuild with the published file on team, dt, the
position ids, rank, slot and espn_id and reported `identical: True`. gsis_id
and player_name were not in that comparison, and gsis_id is the one column
upstream rewrites in past snapshots (research/a76_depth_gsis_drift.py).

PRE-REGISTERED, before the first run: the rebuild will NOT match on gsis_id,
because upstream rewrites it in place including to null. The deliverable is a
statement per column, not a pass mark.

Two stores are built, because "the stored table" means two different things:

  --pulls last   ONE pull ingested into an empty store. What a-71 measured, and
                 what `--from-archive` over a single file gives.
  --pulls all    every archived pull ingested oldest to newest. What the logger
                 does: it parses the file each time upstream changes it, and
                 nfl_depth_chart has no data_version in its key, so each pull
                 is written over the last (gsis_id / player_name through
                 upsert_preserving: a null never erases a held value).

`build` WRITES, so it refuses any --store whose market_log.db already exists:
it can only ever create a new store, never touch an existing one. It pins
LOGGER_DB / LOGGER_RAW_DIR to that directory before config is imported.
`compare` opens the store mode=ro and reads the raw files.
"""
import argparse
import os
import sqlite3
import sys

COLS = ("pos_slot", "espn_id", "gsis_id", "player_name", "pos_grp", "pos_name", "pos_abb")
KEY = ["team", "dt", "pos_grp_id", "pos_id", "pos_rank"]
SEASONS = (2025, 2026)


def distinct_pulls(raws, season):
    from research.a76_depth_gsis_drift import pulls
    out = []
    for day, path, h in pulls(raws, season):
        if not out or out[-1][2] != h:
            out.append((day, path, h))
    return out


def build(a):
    db = os.path.join(os.path.abspath(a.store), "market_log.db")
    if os.path.exists(db):
        raise SystemExit(f"refusing: {db} exists. build only ever creates a new store.")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    os.environ["LOGGER_DB"] = db
    os.environ["LOGGER_RAW_DIR"] = os.path.join(os.path.dirname(db), "raw")
    import config
    if os.path.abspath(config.DB_PATH) != db:
        raise SystemExit(f"refusing: config resolved {config.DB_PATH}, not {db}")
    import store
    from jobs import ingest_nflverse
    store.init_db()
    n = 0
    for s in SEASONS:
        ps = distinct_pulls(a.raw, s)
        if a.pulls == "last":
            ps = ps[-1:]
        for day, path, _ in ps:
            ingest_nflverse.normalize_depth_charts(open(path, "rb").read(), day, season=s)
            n += 1
    if not n:
        raise SystemExit("no depth-chart pull was found under --raw; nothing was built")
    print(f"built {db}: {n} pull(s) ingested, --pulls {a.pulls}")


def rebuild(db, season):
    """Every recorded snapshot rebuilt from the change points: the chart in
    force for (team, dt) is the newest stored dt <= dt for that team."""
    import polars as pl
    c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    sel = ["team", "dt", "pos_grp_id", "pos_id", "pos_rank", *COLS]
    st = pl.DataFrame(c.execute(f"SELECT {','.join(sel)} FROM nfl_depth_chart WHERE season=?",
                                (season,)).fetchall(), schema=sel, orient="row",
                      infer_schema_length=None)
    snaps = [r[0] for r in c.execute(
        "SELECT dt FROM nfl_depth_chart_snapshots WHERE season=? ORDER BY dt", (season,))]
    c.close()
    if not st.height or not snaps:
        return None, 0, 0
    grid = pl.DataFrame({"dt": snaps}).join(pl.DataFrame({"team": st["team"].unique()}), how="cross").sort("dt")
    cp = st.select("team", pl.col("dt").alias("cp")).unique().sort("cp")
    asof = grid.join_asof(cp, left_on="dt", right_on="cp", by="team", strategy="backward",
                          check_sortedness=False)
    out = asof.join(st.rename({"dt": "cp"}), on=["team", "cp"], how="inner").drop("cp")
    return out, st.height, len(snaps)


def against(rb, path, label):
    import polars as pl
    f = pl.read_parquet(path)
    dts = f["dt"].unique()
    r = rb.filter(pl.col("dt").is_in(dts.implode()))
    norm = [pl.col("pos_rank").cast(pl.Int64), pl.col("pos_slot").cast(pl.Int64)]
    r, f = r.with_columns(norm), f.select(KEY + list(COLS)).with_columns(norm)
    j = r.join(f, on=KEY, how="inner", suffix="_f", nulls_equal=True)
    only_f = f.join(r, on=KEY, how="anti", nulls_equal=True).height
    only_r = r.join(f, on=KEY, how="anti", nulls_equal=True).height
    print(f"  against {label}: {f.height:,} published rows in {dts.len()} snapshots; rebuilt {r.height:,}; "
          f"slot keys only in the file {only_f:,}, only in the rebuild {only_r:,}")
    if not j.height:
        raise SystemExit("the rebuild and the file share no row - nothing was compared")
    print(f"    {'column':12s} {'rows':>9} {'differ':>8} {'stored null, file value':>24} "
          f"{'stored value, file null':>24} {'two different values':>21}  verdict")
    for c in COLS:
        d = j.filter(pl.col(c).ne_missing(pl.col(c + "_f")))
        sn = d.filter(pl.col(c).is_null()).height
        fn = d.filter(pl.col(c + "_f").is_null()).height
        verdict = "reconstructs" if not d.height and not only_f and not only_r else "DOES NOT reconstruct"
        print(f"    {c:12s} {j.height:>9,} {d.height:>8,} {sn:>24,} {fn:>24,} {d.height - sn - fn:>21,}  {verdict}")


def compare(a):
    db = os.path.join(os.path.abspath(a.store), "market_log.db")
    if not os.path.exists(db):
        raise SystemExit(f"no store at {db}")
    c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    versions = c.execute("SELECT season, COUNT(DISTINCT data_version), MIN(data_version), MAX(data_version) "
                         "FROM nfl_depth_chart GROUP BY 1 ORDER BY 1").fetchall()
    c.close()
    print(f"store {db}")
    done = 0
    for s in SEASONS:
        ps = distinct_pulls(a.raw, s)
        rb, stored, snaps = rebuild(db, s)
        print(f"\n== depth chart {s}")
        if rb is None or not ps:
            print("  nothing stored, or no pull on disk - not compared")
            continue
        v = [x for x in versions if x[0] == s]
        print(f"  stored {stored:,} change-point rows over {snaps} recorded snapshots; data_version on the "
              f"stored rows: {v[0][1]} distinct, {v[0][2]} .. {v[0][3]}; archived pulls {len(ps)} distinct "
              f"({ps[0][0]} .. {ps[-1][0]})")
        against(rb, ps[-1][1], f"the NEWEST pull, {ps[-1][0]}")
        if len(ps) > 1:
            against(rb, ps[0][1], f"the OLDEST pull, {ps[0][0]} (what this store could have known then)")
        done += 1
    if not done:
        raise SystemExit("no season was compared")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--pulls", choices=("last", "all"), required=True)
    for p in (b, sub.add_parser("compare")):
        p.add_argument("--store", required=True)
        p.add_argument("--raw", action="append", required=True)
    a = ap.parse_args()
    (build if a.cmd == "build" else compare)(a)


if __name__ == "__main__":
    main()
