"""a-76: how much does nflverse rewrite PAST depth-chart snapshots between pulls?

    python -m research.a76_depth_gsis_drift --raw <raw dir> [--raw <raw dir> ...] \
        [--season 2025 2026]

Reads parquet files only - no store is opened. A raw dir is one that holds
`nflverse/<YYYY-MM-DD>/depth_charts_<season>.parquet`; several may be given
(the live archive holds the daily 2026 pulls and ONE 2025 pull, a-71's scratch
archive holds a second 2025 pull).

a-71 quoted "4,311 rows differ across 173 snapshots between the 09-09 and 10-08
pulls, only in gsis_id, 929 of them value -> null" from a script it did not
commit. This is that measurement, committed, and extended to 2025 and to every
consecutive pair of pulls.

For a season, per pair of pulls (A older, B newer):
  - the snapshots (`dt`) BOTH carry - a snapshot only B has is new, not a rewrite
  - rows joined on the slot key (dt, team, pos_grp_id, pos_id, pos_rank)
  - per non-key column: rows that differ, split null->value / value->null /
    value->other value

A season whose pulls are byte-identical is reported as exactly that (a measured
zero for the window, nothing about before it); one with fewer than two pulls as
NOT MEASURABLE. The script exits 1 if no season at all could be compared,
because a drift report that compared nothing is not a result.
"""
import argparse
import glob
import hashlib
import os
import sys

import polars as pl

KEY = ["dt", "team", "pos_grp_id", "pos_id", "pos_rank"]


def pulls(raws, season):
    """{pull date: path}, oldest first. The same date in two raw dirs must be
    the same bytes, or the two archives disagree about what was pulled."""
    out, sha = {}, {}
    for raw in raws:
        for p in sorted(glob.glob(os.path.join(raw, "nflverse", "*", f"depth_charts_{season}.parquet"))):
            day = os.path.basename(os.path.dirname(p))
            h = hashlib.sha256(open(p, "rb").read()).hexdigest()
            if day in sha and sha[day] != h:
                print(f"  NOTE {season} {day}: two archives hold different bytes for this date "
                      f"(pulled at different times of day); keeping both as {day} and {day}+")
                day = day + "+"
            out[day], sha[day] = p, h
    days = sorted(out)
    return [(d, out[d], sha[d]) for d in days]


def compare(a, b):
    """Per-column drift on the snapshots both frames carry."""
    da, db_ = set(a["dt"].unique()), set(b["dt"].unique())
    common = sorted(da & db_)
    a = a.filter(pl.col("dt").is_in(common))
    b = b.filter(pl.col("dt").is_in(common))
    dup_a = a.select(pl.struct(KEY).is_duplicated().sum()).item()
    dup_b = b.select(pl.struct(KEY).is_duplicated().sum()).item()
    j = a.join(b, on=KEY, how="inner", suffix="_b", nulls_equal=True)
    res = {
        "snaps_a": len(da), "snaps_b": len(db_), "common": len(common),
        "dropped": len(da - db_), "rows_a": a.height, "rows_b": b.height,
        "joined": j.height, "dup_a": dup_a, "dup_b": dup_b,
        "only_a": a.join(b, on=KEY, how="anti", nulls_equal=True).height,
        "only_b": b.join(a, on=KEY, how="anti", nulls_equal=True).height,
        "cols": {}, "j": j,
    }
    for c in a.columns:
        if c in KEY or c + "_b" not in j.columns:
            continue
        d = j.filter(pl.col(c).ne_missing(pl.col(c + "_b")))
        n2v = d.filter(pl.col(c).is_null()).height
        v2n = d.filter(pl.col(c + "_b").is_null()).height
        res["cols"][c] = (d.height, n2v, v2n, d.height - n2v - v2n)
    return res


def show(tag, r):
    print(f"  {tag}")
    print(f"    snapshots: older pull {r['snaps_a']}, newer pull {r['snaps_b']}, in both {r['common']}, "
          f"in the older pull and gone from the newer {r['dropped']}")
    print(f"    rows in those shared snapshots: older {r['rows_a']:,}, newer {r['rows_b']:,}, "
          f"joined on the slot key {r['joined']:,}; only older {r['only_a']:,}, only newer {r['only_b']:,}; "
          f"duplicate slot keys older {r['dup_a']}, newer {r['dup_b']}")
    print(f"    {'column':14s} {'differ':>8} {'null->value':>12} {'value->null':>12} {'value->other':>13}")
    for c, (n, n2v, v2n, v2v) in r["cols"].items():
        print(f"    {c:14s} {n:>8,} {n2v:>12,} {v2n:>12,} {v2v:>13,}")


def detail(r, b):
    """Who and when, for the gsis_id rewrites of one pair."""
    j = r["j"]
    d = j.filter(pl.col("gsis_id").ne_missing(pl.col("gsis_id_b")))
    if not d.height:
        print("    gsis_id: no row differs")
        return
    print(f"    gsis_id rewrites touch {d['dt'].n_unique()} of {r['common']} shared snapshots, "
          f"dt {d['dt'].min()} .. {d['dt'].max()}; {d['espn_id'].n_unique()} distinct espn_id, "
          f"{d['team'].n_unique()} teams")
    for name, f in (("null->value", d.filter(pl.col("gsis_id").is_null())),
                    ("value->null", d.filter(pl.col("gsis_id_b").is_null())),
                    ("value->other", d.filter(pl.col("gsis_id").is_not_null() & pl.col("gsis_id_b").is_not_null()))):
        print(f"      {name:13s} {f.height:>6,} rows, {f['espn_id'].n_unique():>4} distinct espn_id, "
              f"{f['dt'].n_unique():>4} snapshots")
    other = [c for c, v in r["cols"].items() if v[0] and c != "gsis_id"]
    print(f"    columns other than gsis_id with any differing row: {other or 'none'}")
    # is gsis_id a function of espn_id inside ONE file? If so the rewrite is a
    # join re-run upstream, and one pull can never disagree with itself.
    per = (b.filter(pl.col("espn_id").is_not_null()).group_by("espn_id")
           .agg(pl.col("gsis_id").n_unique().alias("u")))
    print(f"    inside the newer file alone: espn_id carrying more than one gsis_id (null counted) "
          f"{per.filter(pl.col('u') > 1).height} of {per.height}; rows with espn_id null "
          f"{b['espn_id'].null_count():,}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", action="append", required=True)
    ap.add_argument("--season", type=int, nargs="+", default=[2025, 2026])
    a = ap.parse_args()
    measured = identical = 0
    for s in a.season:
        print(f"\n== depth_charts_{s}")
        ps = pulls(a.raw, s)
        distinct = []
        for day, path, h in ps:
            if not distinct or distinct[-1][2] != h:
                distinct.append((day, path, h))
        print(f"  pulls on disk {len(ps)}, distinct contents {len(distinct)}: "
              f"{ps[0][0] if ps else '-'} .. {ps[-1][0] if ps else '-'}")
        if len(ps) >= 2 and len(distinct) == 1:
            # a measured zero, and a narrow one: upstream did not rebuild this
            # file inside the window. It says nothing about rewrites before the
            # first archived pull.
            f = pl.read_parquet(ps[0][1])
            print(f"  BYTE-IDENTICAL across the {len(ps)} pulls (sha256 {ps[0][2][:12]}): {f.height:,} rows, "
                  f"{f['dt'].n_unique() if 'dt' in f.columns else 0} snapshots, 0 rows rewritten between "
                  f"{ps[0][0]} and {ps[-1][0]}. Upstream did not rebuild the file in that window; "
                  f"whether it was rewritten before {ps[0][0]} is not on disk.")
            identical += 1
            continue
        if len(distinct) < 2:
            print("  NOT MEASURABLE: fewer than two pulls of this season are archived")
            continue
        frames = {}

        def load(i):
            if i not in frames:
                frames[i] = pl.read_parquet(distinct[i][1])
            return frames[i]

        first, last = load(0), load(len(distinct) - 1)
        if "dt" not in first.columns:
            print("  NOT MEASURABLE: weekly layout, no dated snapshots")
            continue
        r = compare(first, last)
        if not r["joined"]:
            raise SystemExit(f"{s}: the first and last pull share no row - nothing was compared")
        measured += 1
        show(f"first pull {distinct[0][0]} against last pull {distinct[-1][0]}", r)
        detail(r, last)
        if len(distinct) > 2:
            print("  each pull against the next (gsis_id only, shared snapshots only):")
            print(f"    {'older':11s} {'newer':11s} {'shared':>7} {'differ':>7} {'null->value':>12} "
                  f"{'value->null':>12} {'value->other':>13} {'other cols':>11}")
            tot = [0, 0, 0, 0]
            for i in range(len(distinct) - 1):
                x = compare(load(i), load(i + 1))
                n, n2v, v2n, v2v = x["cols"]["gsis_id"]
                oth = sum(v[0] for c, v in x["cols"].items() if c != "gsis_id")
                tot = [tot[0] + n, tot[1] + n2v, tot[2] + v2n, tot[3] + v2v]
                if n or oth or x["only_a"] or x["only_b"] or x["dropped"]:
                    print(f"    {distinct[i][0]:11s} {distinct[i + 1][0]:11s} {x['common']:>7} {n:>7,} "
                          f"{n2v:>12,} {v2n:>12,} {v2v:>13,} {oth:>11,}"
                          + (f"  only-older {x['only_a']} only-newer {x['only_b']} dropped-snaps {x['dropped']}"
                             if x["only_a"] or x["only_b"] or x["dropped"] else ""))
                frames.pop(i, None)
            print(f"    sum over {len(distinct) - 1} steps: differ {tot[0]:,}, null->value {tot[1]:,}, "
                  f"value->null {tot[2]:,}, value->other {tot[3]:,} (a row rewritten twice counts twice; "
                  f"steps not listed had no difference)")
    if not measured and not identical:
        raise SystemExit("no season had two pulls - nothing was compared")


if __name__ == "__main__":
    main()
