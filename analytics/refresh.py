"""Track F's weekly rebuild, and the staleness gate behind it. One entry point.

    python -m analytics.refresh --rebuild                  # the current season
    python -m analytics.refresh --rebuild --season 2026
    python -m analytics.refresh --rebuild --all-seasons    # the whole spine
    python -m analytics.refresh --gate --record-health     # non-fatal by default
    python -m analytics.refresh --status

WHY THIS EXISTS (f-25). `f_team_game_pace`, `f_play_usage` and `f_ngs_week`
stopped at 2026 week 1 on 2026-09-17 and stayed there for three weeks, because
every build in this package was a command somebody ran by hand. Nothing
scheduled any of them, and `f_ngs_build` / `f_onfield_build` - the rows that
record what a build was made from - were read by no committed code, so nothing
could see it either. The consequence was live: the matchup export drops a
team-game with no pace row, and published a total on 0 of 15 games.

`--rebuild` is that hand-run sequence as ONE step a schedule can call:

    1. spine     analytics.spine --build --season <S>   usage, pace, units
    2. survey    analytics.survey --scan --season <S>   f_pbp_files, f_pbp_columns
    3. ngs       analytics.ngs --build                  f_ngs_week (whole release)
    4. publish   every metric FAMILY ALREADY IN `f_metrics`, through its own
                 publisher, restricted to the keys already there, and never a
                 family named in HELD

THE PUBLISH RULE IS "REFRESH WHAT IS PUBLISHED, ADD NOTHING". `analytics.export`
ships every row of `f_metrics`, so a publisher run here that registers a metric
the store did not hold is a new public key shipped by a scheduled job with no
one deciding it. Measured the day this was written: the committed publishers
produce more keys than the live store carries (`role.*.by_season`, the
`team_units`, `vacancy` and `deltas` families). So a family runs only if the
store already holds one of its metrics, and any key a publisher adds beyond the
set held before the run is REMOVED again and reported by name. Adding a family
stays what it was: a deliberate publish (`jobs.publish_preflight`).

A FAILED BUILD STOPS THE RUN BEFORE ANY PUBLISHER. A publisher stamps
`computed_ts`; run over facts that were not rebuilt it would stamp today's date
on last month's data, and the staleness gate's metric check would go green on
exactly the failure it exists to see.

`--gate` runs `analytics.staleness`, writes its report (markdown and JSON) under
`<STORAGE_DIR>/logs/`, and with `--record-health` writes one `source_health`
row. It exits 0 whatever the verdict, because 30-odd of the red items are
structural and wait on rulings; `--fatal` makes a failing gate exit 1. It exits
2 only when the gate itself could not run.
"""
import argparse
import datetime as dt
import importlib
import json
import os
import sys
import time

from analytics import paths

REBUILD_SOURCE = "analytics_rebuild"
GATE_SOURCE = "staleness_gate"

# family prefix -> (module, argv). `{season}` is the season being rebuilt.
# A family held in `f_metrics` and absent here FAILS the run: a guard asserts
# only over the shapes it walks, and a silently skipped family is a family that
# goes stale again with this job reporting success.
PUBLISHERS = (
    ("air_yards", "analytics.airyards", ["--publish"]),
    ("usage_stability", "analytics.stability", ["--publish"]),
    ("script_elasticity", "analytics.script", ["--publish"]),
    ("role", "analytics.role", ["--publish"]),
    ("team_units", "analytics.team_units", ["--publish"]),
    ("pace", "analytics.pace", ["--publish"]),
    ("schedule", "analytics.schedule", ["--publish", "--season", "{season}"]),
    ("opportunity_residual", "analytics.residual", ["--publish"]),
    ("vacancy", "analytics.vacancy", ["--publish"]),
    ("deltas", "analytics.deltas", ["--publish"]),
    ("ngs_stability", "analytics.ngs", ["--publish"]),
)
ASOF_PREFIX = "opportunity_residual.asof."

# FAMILIES THIS JOB DOES NOT REPUBLISH, each with the decision it waits on.
# `analytics.export` ships `f_metrics` on every weekly refresh, so recomputing a
# family here IS publishing it. For these two the committed publisher no longer
# computes what the store holds - the definition moved on `main` and the new
# figures were never published - so a scheduled recompute would take a pending
# publish decision by default. Measured on a copy of the store, 2026-10-08:
#   role  `role.touch_share` goes from 1999-2026 to 2009-2026 and most of the
#         values it keeps move (a-40: games with no snap in the bucket are no
#         longer dropped). a-40 filed it as Ethan's publish run, and said the
#         pooled role figures on the site will drop when it happens.
#   pace  the interval method goes from block2000 to cluster_t95 and the unit
#         becomes regular-season only (a-25), with a-25's open question of
#         whether a value built on fewer than 5 games is published at all.
# A held family is skipped out loud on every run and stays stale - which the
# staleness gate reports, correctly. Release one by deleting its row here, or
# for one deliberate run pass `--include-held`.
HELD = {
    "role": "a-40: the corrected down-and-distance role figures await a deliberate publish",
    "pace": "a-25: cluster-robust interval and regular-season scope await a deliberate publish",
}


def current_season(now=None):
    """March turns the league year - the same boundary as `nflverse.current_season`."""
    d = dt.datetime.fromtimestamp(time.time() if now is None else now, dt.timezone.utc)
    return d.year if d.month >= 3 else d.year - 1


def family_of(key):
    """The publisher family a metric key belongs to, or None when nothing claims it."""
    head = key.split(".", 1)[0]
    return head if any(head == f for f, _m, _a in PUBLISHERS) else None


def metric_keys(con):
    return {r[0] for r in con.execute("SELECT metric FROM f_metrics")}


def coverage(con, season):
    """{table: (rows, newest week)} for the season, for the three weekly tables."""
    out = {}
    for table in ("f_play_usage", "f_team_game_pace", "f_team_game_units", "f_ngs_week"):
        try:
            out[table] = con.execute(
                "SELECT COUNT(*), MAX(week) FROM %s WHERE season=?" % table, (season,)).fetchone()
        except Exception:                      # the table does not exist yet
            out[table] = None
    return out


def say(msg):
    """print, flushed: under a scheduler stdout is a pipe, and a step that has
    been running for four minutes should already have said which one it is."""
    print(msg, flush=True)


class StepFailed(RuntimeError):
    pass


def call(module, argv, log):
    """Run one module's `main(argv)` in-process. Anything but a clean return raises."""
    t0 = time.time()
    log("  run  python -m %s %s" % (module, " ".join(argv)))
    try:
        rc = importlib.import_module(module).main(argv)
    except SystemExit as e:                    # argparse, and every `raise SystemExit(msg)`
        rc = e.code
    except Exception as e:                     # noqa: BLE001 - reported, then the run stops
        raise StepFailed("%s raised %s: %s" % (module, type(e).__name__, e)) from e
    if rc not in (0, None):
        raise StepFailed("%s exited %r" % (module, rc))
    return round(time.time() - t0, 1)


def plan(con, season, include_held=False):
    """(steps, orphans, withheld) for what the store holds: the publishers to
    run as [(family, module, argv)], the keys no publisher claims, and the
    families present in the store that HELD keeps this run from republishing."""
    held = metric_keys(con)
    families = {family_of(k) for k in held}
    orphans = sorted(k for k in held if family_of(k) is None)
    steps, withheld = [], []
    for family, module, argv in PUBLISHERS:
        if family not in families:
            continue
        if family in HELD and not include_held:
            withheld.append(family)
            continue
        argv = [a.replace("{season}", str(season)) for a in argv]
        if family == "opportunity_residual" and not any(k.startswith(ASOF_PREFIX) for k in held):
            argv = argv + ["--no-asof"]
        steps.append((family, module, argv))
    return steps, orphans, withheld


def drop_added(con, before):
    """Remove every metric a publisher registered that the store did not hold.

    Returns the removed keys. See the module docstring: this job refreshes what
    is published and adds nothing."""
    added = sorted(metric_keys(con) - before)
    for key in added:
        con.execute("DELETE FROM f_metric_values WHERE metric=?", (key,))
        con.execute("DELETE FROM f_metrics WHERE metric=?", (key,))
    con.commit()
    return added


def rebuild(season=None, all_seasons=False, publish=True, include_held=False, log=say):
    """The whole sequence. Returns a summary dict; raises StepFailed on the first failure."""
    season = current_season() if season is None else season
    out = {"season": season, "steps": [], "skipped_families": [], "dropped": [], "held": []}

    def step(name, module, argv):
        out["steps"].append({"step": name, "seconds": call(module, argv, log)})

    con = paths.connect(read_only=True)
    out["before"] = coverage(con, season)
    con.close()
    scope = [] if all_seasons else ["--season", str(season)]
    step("spine", "analytics.spine", ["--build"] + scope)
    step("survey", "analytics.survey", ["--scan"] + scope)
    step("ngs", "analytics.ngs", ["--build"])

    con = paths.connect()
    try:
        out["after"] = coverage(con, season)
        if not publish:
            return out
        steps, orphans, out["held"] = plan(con, season, include_held)
        if orphans:
            raise StepFailed("f_metrics holds %d key(s) no publisher claims, so this run cannot "
                             "refresh them: %s" % (len(orphans), ", ".join(orphans[:8])))
        ran = {f for f, _m, _a in steps}
        out["skipped_families"] = [f for f, _m, _a in PUBLISHERS
                                   if f not in ran and f not in out["held"]]
        before = metric_keys(con)
        started = int(time.time())
        for family, module, argv in steps:
            step("publish:" + family, module, argv)
            out["dropped"] += drop_added(con, before)
        missing = sorted(before - metric_keys(con))
        if missing:
            raise StepFailed("%d metric(s) held before the run are gone after it: %s"
                             % (len(missing), ", ".join(missing[:8])))
        current = con.execute(
            "SELECT metric, computed_ts FROM f_metrics WHERE availability='current'").fetchall()
        out["held_metrics"] = sorted(m for m, _c in current if family_of(m) in out["held"])
        stale = [m for m, c in current
                 if (c is None or c < started) and m not in out["held_metrics"]]
        out["current_metrics"] = len(current)
        out["not_recomputed"] = sorted(stale)
        if stale:
            raise StepFailed("%d 'current' metric(s) were not recomputed by this run: %s"
                             % (len(stale), ", ".join(sorted(stale)[:8])))
    finally:
        con.close()
    return out


def statement(out):
    """One line: what was rebuilt and how far it reaches."""
    def reach(t):
        got = out.get("after", {}).get(t)
        return "none" if not got or got[1] is None else "wk %d (%d rows)" % (got[1], got[0])
    words = ["%d rebuilt" % out["season"],
             "usage " + reach("f_play_usage"), "pace " + reach("f_team_game_pace"),
             "ngs " + reach("f_ngs_week")]
    if "current_metrics" in out:
        words.append("%d of %d current metrics recomputed"
                     % (out["current_metrics"] - len(out["held_metrics"]), out["current_metrics"]))
    if out.get("held"):
        words.append("HELD %s" % ", ".join(out["held"]))
    if out.get("dropped"):
        words.append("%d unpublished key(s) not added" % len(set(out["dropped"])))
    return "; ".join(words)


def record(source, ok, detail, enabled):
    """One `source_health` row, through the same writer the weekly refresh uses.

    Opt-in. `store` resolves `config.DB_PATH`: the logger's database under the
    production clone, and an inert file in track F's working clone - so a run by
    hand from a unit's tree cannot write the live store."""
    if not enabled:
        return
    import store
    store.record_health(source, ok, (detail or "")[:200], watermark=time.time() if ok else None)


def gate_paths():
    import config
    d = config.storage_path("logs")
    return os.path.join(d, "staleness_gate.md"), os.path.join(d, "staleness_gate.json")


def run_gate(offline=False, quick=False, strict=False, report_path=None, json_path=None,
             store_dir=None, root=None, log=say):
    """Run `analytics.staleness`, write both reports, return the GateReport."""
    from analytics import staleness
    if store_dir is None:
        import config
        store_dir = config.STORAGE_DIR
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    md, js = gate_paths() if report_path is None or json_path is None else (None, None)
    report_path, json_path = report_path or md, json_path or js
    now = time.time()
    report = staleness.run(store_dir, root, now=now, offline=offline, quick=quick, strict=strict)
    for p in (report_path, json_path):
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(staleness.render(report, now) + "\n")
    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"statement": report.statement, "clean": report.clean,
                   "clock": report.clock.statement if report.clock else None,
                   "items": [vars(i) for i in report.items]}, f, indent=1, default=str)
    log("staleness report: %s" % report_path)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--season", type=int)
    ap.add_argument("--all-seasons", action="store_true",
                    help="rebuild every season of the spine, not only the current one")
    ap.add_argument("--no-publish", action="store_true",
                    help="rebuild the fact tables and leave every metric as it is")
    ap.add_argument("--include-held", action="store_true",
                    help="also republish the families in HELD - a deliberate publish")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--offline", action="store_true", help="gate: skip the upstream listing")
    ap.add_argument("--quick", action="store_true", help="gate: skip the market_depth day scan")
    ap.add_argument("--strict", action="store_true", help="gate: UNRULED items fail too")
    ap.add_argument("--fatal", action="store_true", help="gate: exit 1 when it is failing")
    ap.add_argument("--store-dir", help="gate: the stores (default: config.STORAGE_DIR)")
    ap.add_argument("--root", help="gate: the code tree to scan (default: this checkout)")
    ap.add_argument("--report-dir", help="gate: where both reports go "
                                         "(default: <STORAGE_DIR>/logs)")
    ap.add_argument("--record-health", action="store_true",
                    help="write a source_health row for each of --rebuild and --gate")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    if not (a.rebuild or a.gate or a.status):
        ap.print_help()
        return 0
    code = 0
    if a.rebuild:
        try:
            out = rebuild(season=a.season, all_seasons=a.all_seasons, publish=not a.no_publish,
                          include_held=a.include_held)
        except (StepFailed, FileNotFoundError) as e:
            print("REBUILD FAILED: %s" % e, flush=True)
            record(REBUILD_SOURCE, False, str(e), a.record_health)
            return 1
        for s in out["steps"]:
            print("  %-28s %7.1fs" % (s["step"], s["seconds"]))
        if out["skipped_families"]:
            print("  not published here (no metric of the family is in f_metrics): %s"
                  % ", ".join(out["skipped_families"]))
        for family in out["held"]:
            print("  HELD, not republished: %s (%s)" % (family, HELD[family]))
        if out["dropped"]:
            print("  registered by a publisher and NOT added (%d): %s"
                  % (len(set(out["dropped"])), ", ".join(sorted(set(out["dropped"])))))
        print("REBUILT %s" % statement(out), flush=True)
        record(REBUILD_SOURCE, True, statement(out), a.record_health)
    if a.status:
        con = paths.connect(read_only=True)
        season = a.season or current_season()
        for table, got in coverage(con, season).items():
            print("  %-20s %s" % (table, "absent" if got is None else
                                  "%d rows, newest week %s" % (got[0], got[1])))
        con.close()
    if a.gate:
        try:
            where = {} if not a.report_dir else {
                "report_path": os.path.join(a.report_dir, "staleness_gate.md"),
                "json_path": os.path.join(a.report_dir, "staleness_gate.json")}
            report = run_gate(offline=a.offline, quick=a.quick, strict=a.strict,
                              store_dir=a.store_dir, root=a.root, **where)
        except Exception as e:                 # noqa: BLE001 - a gate that cannot run says so
            print("STALENESS GATE DID NOT RUN: %s: %s" % (type(e).__name__, e), flush=True)
            record(GATE_SOURCE, False, "did not run: %s: %s" % (type(e).__name__, e),
                   a.record_health)
            return 2
        print(report.statement, flush=True)
        record(GATE_SOURCE, report.clean, report.statement, a.record_health)
        if a.fatal and not report.clean:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
