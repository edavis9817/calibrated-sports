"""Unit a-40: publish the down-and-distance family into a SCRATCH copy of the real
store and judge what it would export - without touching the live analytics.db.

    python -m research.a40_role_scratch --scratch D:/temp/a40/run1

WHY SCRATCH. `analytics.db` is shared with production: `jobs.weekly_refresh`
runs `analytics.export --write --dest web` and uploads whatever it holds. A
publish into the live database is therefore a publish to the site on the next
scheduled refresh, and publishing is Ethan's run, not a unit's. Same environment
as `jobs.publish_preflight` and a-39's judgement: `config.STORAGE_DIR` is
repointed at <scratch>/store before anything runs, the live logger store may only
be opened `mode=ro` (the guard is shown firing first), and analytics.db is copied
with the sqlite backup API over a `mode=ro` source.

WHAT IT REPORTS
  - the four role metrics before (live) and after (scratch): range, availability,
    players, values, and the range note;
  - per by-season metric: value count by season, n (games) distribution, and the
    share of values resting on fewer than five games (brief 020's reading floor);
  - the median shift of the pooled shares, live -> corrected, per bucket, on the
    subjects present in both;
  - `analytics.export.build` over the whole scratch store: keys, values, the
    coverage level stated on each role key, and unbounded intervals dropped.
"""
import argparse
import json
import os
import sqlite3
import statistics as st
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MIN_READABLE = 5


def log(msg):
    print(msg, flush=True)


def metric_rows(con):
    return {r[0]: dict(zip(("metric", "season_from", "season_to", "availability",
                            "range_note", "slice_kind"), r))
            for r in con.execute(
                "SELECT metric, season_from, season_to, availability, range_note, "
                "slice_kind FROM f_metrics WHERE metric LIKE 'role.%'")}


def values(con, metric):
    return con.execute(
        "SELECT subject_id, slice, est, lo, hi, n FROM f_metric_values WHERE metric=?",
        (metric,)).fetchall()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--env", default=os.path.join(ROOT, ".env"))
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    scratch = os.path.abspath(a.scratch)
    if os.path.exists(scratch) and os.listdir(scratch):
        raise SystemExit(f"{scratch} is not empty - use a fresh directory")
    os.makedirs(scratch, exist_ok=True)

    from jobs import publish_preflight as PF
    live = PF.setup_env(a.env, scratch)
    store_dir = PF.patch(live["db"], scratch)
    import config
    assert os.path.normcase(config.STORAGE_DIR) == os.path.normcase(store_dir)
    from analytics import export as AX
    from analytics import paths, role
    assert os.path.normcase(os.path.dirname(paths.db_path())) == os.path.normcase(store_dir), \
        "analytics.db does not resolve into scratch"

    report = {"scratch": scratch,
              "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    os.makedirs(store_dir, exist_ok=True)
    src_path = os.path.join(os.path.dirname(live["db"]), "analytics.db")
    src = sqlite3.connect("file:%s?mode=ro" % src_path.replace("\\", "/"), uri=True, timeout=30)
    before_meta = metric_rows(src)
    before_vals = {m: values(src, m) for m in before_meta}
    dst = sqlite3.connect(paths.db_path())
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    log(f"snapshot: {src_path} -> {paths.db_path()}")
    report["before"] = {m: dict(meta, values=len(before_vals[m]),
                                players=len({v[0] for v in before_vals[m]}))
                        for m, meta in before_meta.items()}

    t0 = time.time()
    con = paths.connect()
    report["written"] = role.publish(con)
    report["publish_seconds"] = round(time.time() - t0, 1)
    con.close()

    con = paths.connect(read_only=True)
    after_meta = metric_rows(con)
    report["after"] = {}
    for m, meta in sorted(after_meta.items()):
        vals = values(con, m)
        entry = dict(meta, values=len(vals), players=len({v[0] for v in vals}),
                     below_5_games=sum(1 for v in vals if v[5] < MIN_READABLE))
        if meta["slice_kind"] == role.SEASON_SLICE_KIND:
            by_season = {}
            for v in vals:
                s = int(v[1].split("|")[0])
                by_season[s] = by_season.get(s, 0) + 1
            entry["values_by_season"] = dict(sorted(by_season.items()))
            ns = sorted(v[5] for v in vals)
            entry["n_games"] = {"min": ns[0], "median": ns[len(ns) // 2], "max": ns[-1]}
        report["after"][m] = entry
        log(f"{m:34s} {meta['season_from']}-{meta['season_to']} {meta['availability']:10s} "
            f"players {entry['players']:5d} values {entry['values']:6d} n<5 {entry['below_5_games']:6d}")
        log(f"{'':34s} {meta['range_note']}")

    shift = {}
    for m in ("role.onfield_share", "role.touch_share"):
        old = {(s, sl): e for s, sl, e, *_ in before_vals.get(m, [])}
        new = {(s, sl): e for s, sl, e, *_ in values(con, m)}
        for b in role.BUCKETS:
            d = [old[k] - new[k] for k in old.keys() & new.keys()
                 if k[1] == b and old[k] is not None and new[k] is not None]
            if d:
                shift.setdefault(m, {})[b] = {"subjects": len(d), "median_old_minus_new":
                                              round(st.median(d), 4)}
        log(f"{m} live - corrected, median by bucket: " + ", ".join(
            f"{b} {v['median_old_minus_new']:+.3f} (n={v['subjects']})"
            for b, v in shift.get(m, {}).items()))
    report["pooled_shift"] = shift

    try:
        out, dropped = AX.build(con)
        role_keys = {k: {"values": len(p["values"]), "coverage": AX.coverage_of(p),
                         "methods": [x["code"] for x in p["methods"]]}
                     for k, p in out.items() if "/role." in k}
        report["export"] = {
            "keys": len(out), "dropped": dropped,
            "values": sum(len(p["values"]) for p in out.values()
                          if p["kind"] == "analytics.metric"),
            "role_keys": role_keys}
        log(f"analytics.export.build: {len(out)} keys, {report['export']['values']} values, "
            f"dropped {dropped}")
        for k, v in sorted(role_keys.items()):
            log(f"  {k:48s} values {v['values']:6d} coverage {v['coverage']} {v['methods']}")
    except Exception as e:  # noqa: BLE001 - the refusal IS the finding
        report["export"] = {"error": f"{type(e).__name__}: {e}"}
        log(f"analytics.export.build REFUSED: {type(e).__name__}: {e}")
    path = a.json or os.path.join(scratch, "a40_role.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    log(f"report: {path}")
    return 0 if "keys" in report["export"] else 1


if __name__ == "__main__":
    sys.exit(main())
