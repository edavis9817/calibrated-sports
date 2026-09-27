"""Unit a-39: run the analytics publishers against a SCRATCH copy of the real store,
then judge what each module would publish before anything is wired to the site.

    python -m research.a39_module_judgement --scratch D:/temp/a39/run1
    python -m research.a39_module_judgement --scratch D:/temp/a39/run2 --asof

WHAT IT TOUCHES. Nothing live. It reuses `jobs.publish_preflight`'s environment:
`config.STORAGE_DIR` is repointed at <scratch>/store BEFORE any publisher runs, and
`sqlite3.connect` refuses any open of the live logger store that is not `mode=ro`
(the guard is shown firing before anything runs). `analytics.db` is copied into
scratch with the sqlite backup API over a `mode=ro` source.

WHAT IT REPORTS, per module family (metric-key prefix):
  keys, values, the estimate range, the share of values resting on fewer than five
  independent blocks (brief 020's floor - a-25 measured a two-block percentile
  bootstrap covering ~0.51-0.53 while built at 0.95), the method tags used and
  whether `analytics.export.describe_method` can state each one, and unbounded
  intervals the export would drop.

Then it runs `analytics.export.build` over the whole scratch store - the check the
unit is judged by - and prints the key count. A refusal there is reported, not
swallowed: an export that cannot build is the finding.
"""
import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAMILIES = ("team_units.", "opportunity_residual.", "vacancy.", "deltas.")
MIN_READABLE = 5


def log(msg):
    print(msg, flush=True)


def judge_family(con, prefix, describe):
    rows = con.execute(
        "SELECT metric FROM f_metrics WHERE metric LIKE ? ORDER BY metric",
        (prefix + "%",)).fetchall()
    metrics = [r[0] for r in rows]
    out = {"keys": len(metrics), "values": 0, "unbounded": 0, "est_null": 0,
           "below_5_blocks": 0, "methods": {}, "unstateable_methods": [],
           "by_metric": {}}
    for m in metrics:
        block = con.execute("SELECT block FROM f_metrics WHERE metric=?", (m,)).fetchone()[0]
        vals = con.execute(
            "SELECT est, lo, hi, n, method FROM f_metric_values WHERE metric=?", (m,)).fetchall()
        ests = [v[0] for v in vals if v[0] is not None]
        finite = [v for v in vals if v[1] not in (None,) and v[2] not in (None,)
                  and abs(v[1]) != float("inf") and abs(v[2]) != float("inf")]
        low_n = sum(1 for v in vals if v[3] is not None and v[3] < MIN_READABLE)
        widths = sorted(v[2] - v[1] for v in finite)
        tags = sorted({v[4] for v in vals})
        out["values"] += len(vals)
        out["unbounded"] += len(vals) - len(finite)
        out["est_null"] += sum(1 for v in vals if v[0] is None)
        out["below_5_blocks"] += low_n
        for t in tags:
            out["methods"][t] = out["methods"].get(t, 0) + sum(1 for v in vals if v[4] == t)
            try:
                describe(t, block)
            except Exception as e:  # noqa: BLE001 - reported
                if t not in out["unstateable_methods"]:
                    out["unstateable_methods"].append(t)
        out["by_metric"][m] = {
            "values": len(vals), "block": block,
            "est_min": min(ests) if ests else None, "est_max": max(ests) if ests else None,
            "median_width": widths[len(widths) // 2] if widths else None,
            "below_5_blocks": low_n, "methods": tags}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--env", default=os.path.join(ROOT, ".env"))
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--asof", action="store_true",
                    help="also build the five opportunity_residual.asof.* metrics (~20 min)")
    ap.add_argument("--no-publish", action="store_true",
                    help="judge the scratch store as it is (it must already exist)")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    scratch = os.path.abspath(a.scratch)

    from jobs import publish_preflight as PF
    if not a.no_publish:
        if os.path.exists(scratch) and os.listdir(scratch):
            raise SystemExit(f"{scratch} is not empty - use a fresh directory")
        os.makedirs(scratch, exist_ok=True)
    live = PF.setup_env(a.env, scratch)
    store_dir = PF.patch(live["db"], scratch)
    import config
    assert os.path.normcase(config.STORAGE_DIR) == os.path.normcase(store_dir)

    from analytics import export as AX
    from analytics import paths
    assert os.path.normcase(os.path.dirname(paths.db_path())) == os.path.normcase(store_dir), \
        "analytics.db does not resolve into scratch"

    report = {"scratch": scratch, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not a.no_publish:
        os.makedirs(store_dir, exist_ok=True)
        src_path = os.path.join(os.path.dirname(live["db"]), "analytics.db")
        src = sqlite3.connect("file:%s?mode=ro" % src_path.replace("\\", "/"), uri=True, timeout=30)
        dst = sqlite3.connect(paths.db_path())
        with dst:
            src.backup(dst)
        before = dst.execute("SELECT COUNT(*) FROM f_metrics").fetchone()[0]
        src.close()
        dst.close()
        report["metrics_before"] = before
        log(f"snapshot: {src_path} -> {paths.db_path()} ({before} metrics)")
        report["publish"] = PF.run_analytics_publishers(a.season, None, no_asof=not a.asof)

    con = paths.connect(read_only=True)
    report["metrics_after"] = con.execute("SELECT COUNT(*) FROM f_metrics").fetchone()[0]
    report["families"] = {p.rstrip("."): judge_family(con, p, AX.describe_method)
                          for p in FAMILIES}
    for fam, j in report["families"].items():
        log(f"{fam:24s} keys {j['keys']:4d}  values {j['values']:7d}  unbounded {j['unbounded']:5d}"
            f"  n<5 {j['below_5_blocks']:6d}  unstateable methods {j['unstateable_methods'][:4]}"
            f"{' ...' if len(j['unstateable_methods']) > 4 else ''}")
    try:
        out, dropped = AX.build(con)
        report["export"] = {"keys": len(out), "dropped": sum(dropped.values()),
                            "values": sum(len(p["values"]) for p in out.values()
                                          if p["kind"] == "analytics.metric")}
        log(f"analytics.export.build: {len(out)} keys, {report['export']['values']} values, "
            f"{report['export']['dropped']} unbounded dropped")
    except Exception as e:  # noqa: BLE001 - the refusal IS the finding
        report["export"] = {"error": f"{type(e).__name__}: {e}"}
        log(f"analytics.export.build REFUSED: {type(e).__name__}: {e}")
    path = a.json or os.path.join(scratch, "a39_judgement.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    log(f"report: {path}")
    return 0 if "keys" in report["export"] else 1


if __name__ == "__main__":
    sys.exit(main())
