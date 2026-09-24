"""The publish preflight (unit a-33): the WHOLE publish path against a scratch
copy, then the uploader in dry-run against the live bucket. One command, known
blast radius.

    python -m jobs.publish_preflight --scratch D:/temp/pf-0924
    python -m jobs.publish_preflight --scratch D:/temp/pf-0924 --no-analytics-publish
    python -m jobs.publish_preflight --scratch D:/temp/pf-0924 --stage air_rz --stage fixtures

WHAT IT RUNS, in the order a real publish would:

  1. snapshot   WEB_EXPORT_DIR (with its .upload_state.json) -> <scratch>/web
                analytics.db (sqlite backup API over mode=ro) -> <scratch>/store
                web/slugs/nfl.json -> <scratch>/slugs
  2. analytics  the metric publishers (spine, team_units, pace, schedule,
                residual, vacancy, deltas) into the SCRATCH analytics.db
  3. site       jobs.export_web.export() - every PART - into <scratch>/web
  4. analytics  analytics.export build + sync into <scratch>/web/analytics/
  5. lab        lab.universe build + jobs.lab_publish into <scratch>/web/lab/
  6. board      jobs.board_read tick into <scratch>/board (its own tree)
  7. upload     jobs.export_web.upload(dry_run=True) for the web tree and the
                Board's tree, with the declaration the steps above produced
  8. bucket     an independent listing of the live bucket: every local key's
                MD5 against the object's ETag, so "changed" is measured against
                what R2 actually holds and not only against the upload record

and prints ONE table: keys added / changed / REMOVED (every one by name) /
upload size raw and gzipped / prefixes declared and by whom / removed_withheld.

WHY EVERY WRITE LANDS IN SCRATCH, BY CONSTRUCTION RATHER THAN BY CARE.
Two mechanisms, and the second is the one that matters.
  * `config.STORAGE_DIR` is repointed at <scratch>/store before any other
    project module is imported, so `analytics.paths.db_path()` (analytics.db),
    `lab.universe.out_dir()` and every `storage_path()` resolve to scratch.
  * `sqlite3.connect` is WRAPPED: any open of the live logger database whose
    URI does not carry `mode=ro` RAISES. The export path has at least five
    independent openers of `config.DB_PATH` (export_web.ro, research.implied,
    research.score, core.version_resolve, board_read.ro) and patching them one
    by one is a list that goes stale; refusing at the one function they all
    call does not. The one write the export makes to the live store on purpose
    - the retention hold on published markets - is forced to its dry-run form
    and reported as `would_write`, so the guard never has to fire on it.
The first version of this job pointed LOGGER_DB at scratch instead; it was
refused on its first run by `research.implied._ro` reading the empty scratch
path. The refusal was correct and the design was the wrong way round: it
guarded the readers, which are many, instead of the writes, which are one call.

WHY NOT `--dry-run` ON THE EXPORT. `export(dry_run=True)` writes no files, and
the uploader diffs FILES. A dry-run export therefore cannot say what an upload
would do; a real export into a scratch copy of the tree can.
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# The only .env names this job reads by name. The file itself is never
# enumerated or printed: it carries an inline private key.
LIVE_NAMES = ("LOGGER_DB", "WEB_EXPORT_DIR", "LOGGER_RAW_DIR")

ANALYTICS_STEPS = (
    # (module, argv) - the order matters: the spine's f_team_game_units must
    # exist before team_units and pace publish (a-25), and deltas reads the
    # 2026 spine (a-19).
    ("analytics.spine", ["--build"]),
    ("analytics.team_units", ["--publish"]),
    ("analytics.pace", ["--publish"]),
    ("analytics.schedule", ["--publish", "--season", "{season}"]),
    ("analytics.residual", ["--publish"]),
    ("analytics.vacancy", ["--publish"]),
    ("analytics.deltas", ["--publish"]),
)

# The five a-21 as-of keys, flagged by name in every report.
ASOF_PATTERN = "analytics/nfl/opportunity_residual.asof."

# Transfer arithmetic for "what a page loading one costs on a phone". These are
# ASSUMED link speeds, not measurements: Lighthouse's mobile throttling preset
# (1.6 Mbit/s, "Slow 4G") and a typical mid-range 4G downlink (10 Mbit/s).
PHONE_LINKS_MBIT = {"slow_4g_1.6": 1.6, "typical_4g_10": 10.0}


def log(msg):
    print(msg, flush=True)


# =============================================================================
# environment - BEFORE any project import
# =============================================================================

def setup_env(env_path, scratch):
    from dotenv import dotenv_values, load_dotenv
    vals = dotenv_values(env_path) if env_path and os.path.exists(env_path) else {}
    live = {n: os.environ.get(n) or vals.get(n) for n in LIVE_NAMES}
    if not live["LOGGER_DB"] or not live["WEB_EXPORT_DIR"]:
        raise SystemExit(f"LOGGER_DB and WEB_EXPORT_DIR must be set (read from {env_path})")
    live_db = os.path.abspath(live["LOGGER_DB"])
    if not os.path.exists(live_db):
        raise SystemExit(f"no live store at {live_db}")
    raw = live["LOGGER_RAW_DIR"] or os.path.join(os.path.dirname(live_db), "raw")
    # LOGGER_DB stays LIVE: every reader opens it, and the guard in `patch()`
    # refuses any open of it that is not mode=ro.
    os.environ["LOGGER_DB"] = live_db
    os.environ["WEB_EXPORT_DIR"] = os.path.join(scratch, "web")
    os.environ["BOARD_EXPORT_DIR"] = os.path.join(scratch, "board")
    # Reads only - the nflverse mirror the analytics publishers read. Absolute,
    # because a relative RAW_DIR resolves against the working directory.
    os.environ["LOGGER_RAW_DIR"] = os.path.abspath(raw)
    # Credentials and everything else; override=False keeps the four above.
    if env_path and os.path.exists(env_path):
        load_dotenv(env_path, override=False)
    return {"db": live_db, "web": os.path.abspath(live["WEB_EXPORT_DIR"]),
            "raw": os.path.abspath(raw)}


class LiveWriteRefused(RuntimeError):
    """A write-capable open of the live logger store, refused by the preflight."""


def _target(database):
    s = str(database)
    if s.startswith("file:"):
        s = s[5:].split("?", 1)[0]
    return os.path.normcase(os.path.abspath(s.replace("/", os.sep)))


def write_guard(live_db, connect):
    """`connect`, refusing any open of `live_db` whose URI lacks mode=ro."""
    live = os.path.normcase(os.path.abspath(live_db))

    def guarded(database, *args, **kwargs):
        if _target(database) == live and "mode=ro" not in str(database):
            raise LiveWriteRefused(f"preflight refused a write-capable open of {live_db}")
        return connect(database, *args, **kwargs)
    return guarded


def patch(live_db, scratch):
    """Repoint the storage root at scratch, refuse any non-read-only open of the
    live store, and force the export's one intended store write to dry-run."""
    import config
    store_dir = os.path.join(scratch, "store")
    config.STORAGE_DIR = store_dir
    sqlite3.connect = write_guard(live_db, sqlite3.connect)
    from jobs import export_web as E
    original_hold = E.hold_published_markets

    def hold(published, now_ts, dry_run=False):
        return original_hold(published, now_ts, dry_run=True)
    E.hold_published_markets = hold
    # Show the guard firing before trusting it: a guard never seen to refuse is
    # not a guard.
    try:
        sqlite3.connect(live_db)
    except LiveWriteRefused:
        pass
    else:
        raise SystemExit("the live-store write guard did not fire - refusing to run")
    return store_dir


# =============================================================================
# snapshot
# =============================================================================

def snapshot(live, scratch, log=log):
    web = os.path.join(scratch, "web")
    t0 = time.time()
    shutil.copytree(live["web"], web)
    n = sum(len(f) for _r, _d, f in os.walk(web))
    log(f"snapshot: web tree {live['web']} -> {web}: {n:,} files ({time.time() - t0:.0f}s)")
    store_dir = os.path.join(scratch, "store")
    os.makedirs(store_dir, exist_ok=True)
    src_path = os.path.join(os.path.dirname(live["db"]), "analytics.db")
    src = sqlite3.connect("file:%s?mode=ro" % src_path.replace("\\", "/"), uri=True, timeout=30)
    dst = sqlite3.connect(os.path.join(store_dir, "analytics.db"))
    with dst:
        src.backup(dst)
    before = dst.execute("SELECT COUNT(*) FROM f_metrics").fetchone()[0]
    src.close()
    dst.close()
    log(f"snapshot: analytics.db -> {store_dir} ({before} metrics before any publish)")
    slugs = os.path.join(scratch, "slugs", "nfl.json")
    os.makedirs(os.path.dirname(slugs), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, "web", "slugs", "nfl.json"), slugs)
    return {"web": web, "metrics_before": before, "slugs": slugs}


# =============================================================================
# the publish path
# =============================================================================

def run_analytics_publishers(season, only=None, no_asof=False, log=log):
    import importlib
    out = []
    for mod, argv in ANALYTICS_STEPS:
        name = mod.split(".")[-1]
        if only is not None and name not in only:
            continue
        argv = [a.replace("{season}", str(season)) for a in argv]
        if name == "residual" and no_asof:
            argv = argv + ["--no-asof"]
        t0 = time.time()
        log(f"analytics: python -m {mod} {' '.join(argv)}")
        rc = importlib.import_module(mod).main(argv)
        out.append({"step": f"{mod} {' '.join(argv)}", "rc": rc or 0,
                    "seconds": round(time.time() - t0, 1)})
        if rc:
            raise SystemExit(f"{mod} exited {rc}")
    return out


def run_site_export(dest, slugs, stages, log=log):
    from jobs import export_web as E
    s = E.export(dest=dest, registry_path=slugs, stages=stages, log=log)
    return s


def run_analytics_export(dest):
    from analytics import export as AX
    from analytics import paths
    con = paths.connect(read_only=True)
    out, dropped = AX.build(con)
    con.close()
    n, deleted, _root = AX.sync(out, root=dest)
    return {"keys": len(out), "written": n, "deleted_stale": deleted,
            "unbounded_dropped": sum(dropped.values()), "declared": [AX.OWNED_PREFIX]}


def run_lab(live_db, scratch, dest, log=log):
    from lab import universe as U
    from jobs import lab_publish as L
    udir = os.path.join(scratch, "lab_universe")
    U.build(db=live_db, dest=udir, verbose=False)
    out = L.publish(U.load(udir), dest, log=log)
    out["declared"] = [L.PREFIX]
    return out


def run_board(live_db, season, dest, log=log):
    from jobs import board_read as BR
    return BR.tick(season, dest, upload=False, db=live_db, log=log)


# =============================================================================
# the diff - what an upload would do, key by key
# =============================================================================

def _gz(data):
    return len(gzip.compress(data, compresslevel=6))


def plan(dest, state, refreshed, tree="web"):
    """Key-level replica of `upload()`'s decisions. Its counts are asserted equal
    to `upload(dry_run=True)`'s own, so the detail cannot drift from the thing
    it details."""
    from jobs import export_web as E
    local = E.local_keys(dest, tables=(tree == "board"))
    if tree == "web":
        local = {k: p for k, p in local.items()
                 if not k.startswith(E.BOARD_PREFIX) and not k.startswith(E.LIVE_PREFIX)}
    added, changed = [], []
    for key, path in sorted(local.items()):
        with open(path, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        if state.get(key) == sha:
            continue
        row = {"key": key, "bytes": len(data), "gz": _gz(data),
               "md5": hashlib.md5(data).hexdigest()}
        (changed if key in state else added).append(row)
    absent = sorted(set(state) - set(local))
    if refreshed is None or tree == "board":
        removed, withheld = [], absent
    else:
        removed = [k for k in absent if any(k.startswith(p) for p in refreshed)
                   and not k.startswith(E.LIVE_PREFIX) and not k.startswith(E.BOARD_PREFIX)
                   and E.table_for_key(k)[0] is None]
        withheld = [k for k in absent if k not in set(removed)]
    return {"local": local, "added": added, "changed": changed, "removed": removed,
            "withheld": withheld}


def bucket_listing(client, bucket):
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", ()):
            out[o["Key"]] = {"etag": o["ETag"].strip('"'), "size": o["Size"]}
    return out


def against_bucket(local, listing):
    """Every local key against the object R2 holds. A multipart ETag (it
    carries a '-') is not an MD5 and is counted as unknown, never as equal."""
    new, differs, same, unknown = [], [], [], []
    for key, path in sorted(local.items()):
        obj = listing.get(key)
        if obj is None:
            new.append(key)
            continue
        if "-" in obj["etag"]:
            unknown.append(key)
            continue
        with open(path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        (same if md5 == obj["etag"] else differs).append(key)
    return {"new": new, "differs": differs, "same": same, "unknown": unknown}


def prefix_of(key, depth=2):
    parts = key.split("/")
    return "/".join(parts[:depth]) + ("/" if len(parts) > depth else "")


def summarise(p, declared_by):
    added = sorted(p["added"], key=lambda r: -r["bytes"])
    ch = p["changed"]
    up = p["added"] + p["changed"]
    by_prefix = {}
    for r in up:
        d = by_prefix.setdefault(prefix_of(r["key"]), {"added": 0, "changed": 0, "bytes": 0, "gz": 0})
        d["added" if r in p["added"] else "changed"] += 1
        d["bytes"] += r["bytes"]
        d["gz"] += r["gz"]
    return {
        "added": {"count": len(added), "bytes": sum(r["bytes"] for r in added),
                  "gz": sum(r["gz"] for r in added),
                  "largest": [{k: r[k] for k in ("key", "bytes", "gz")} for r in added[:10]]},
        "changed": {"count": len(ch), "bytes": sum(r["bytes"] for r in ch),
                    "gz": sum(r["gz"] for r in ch)},
        "removed": {"count": len(p["removed"]), "keys": p["removed"]},
        "upload": {"count": len(up), "bytes": sum(r["bytes"] for r in up),
                   "gz": sum(r["gz"] for r in up)},
        "by_prefix": dict(sorted(by_prefix.items())),
        "declared": declared_by,
        "removed_withheld": {"count": len(p["withheld"]),
                             "prefixes": sorted({k.split("/")[0] + "/" for k in p["withheld"]}),
                             "keys": p["withheld"]},
    }


def asof_report(local):
    rows = []
    for key, path in sorted(local.items()):
        if not key.startswith(ASOF_PATTERN):
            continue
        with open(path, "rb") as f:
            data = f.read()
        gz = _gz(data)
        t0 = time.perf_counter()
        doc = json.loads(data)
        parse_ms = (time.perf_counter() - t0) * 1000
        rows.append({"key": key, "bytes": len(data), "gz": gz, "values": len(doc.get("values", ())),
                     "python_json_parse_ms_this_pc": round(parse_ms),
                     "seconds_at": {name: round(gz * 8 / (mbit * 1e6), 1)
                                    for name, mbit in PHONE_LINKS_MBIT.items()},
                     "seconds_at_uncompressed": {name: round(len(data) * 8 / (mbit * 1e6), 1)
                                                 for name, mbit in PHONE_LINKS_MBIT.items()}})
    return rows


def mb(n):
    return f"{n / 1e6:,.2f} MB"


def table(s, bucket_cmp, upload_result):
    lines = ["", "| | |", "|---|---|"]
    a, c, r, u = s["added"], s["changed"], s["removed"], s["upload"]
    lines.append(f"| keys added | {a['count']:,} ({mb(a['bytes'])}, {mb(a['gz'])} gz) |")
    lines.append(f"| keys changed | {c['count']:,} ({mb(c['bytes'])}, {mb(c['gz'])} gz) |")
    lines.append(f"| **keys removed** | **{r['count']}**"
                 + (": " + ", ".join(r["keys"]) if r["keys"] else "") + " |")
    lines.append(f"| total upload | {u['count']:,} keys, {mb(u['bytes'])} raw, {mb(u['gz'])} gz |")
    lines.append("| prefixes declared | " + "; ".join(f"`{p}` ({b})" for p, b in s["declared"])
                 + " |")
    w = s["removed_withheld"]
    lines.append(f"| removed_withheld | {w['count']}"
                 + (" under " + ", ".join(w["prefixes"]) if w["prefixes"] else "") + " |")
    lines.append(f"| upload(dry_run) says | changed {upload_result.get('changed')}, removed "
                 f"{upload_result.get('removed')}, withheld {upload_result.get('removed_withheld')}"
                 f", state from {upload_result.get('state_source')} |")
    if bucket_cmp is not None:
        lines.append(f"| against the bucket listing | new {len(bucket_cmp['new']):,}, differs "
                     f"{len(bucket_cmp['differs']):,}, same {len(bucket_cmp['same']):,}, "
                     f"unknown etag {len(bucket_cmp['unknown']):,} |")
    lines.append("")
    lines.append("largest added:")
    for x in a["largest"]:
        lines.append(f"  {x['key']:<72} {mb(x['bytes']):>10}  {mb(x['gz']):>10} gz")
    lines.append("by prefix (added+changed):")
    for pfx, d in s["by_prefix"].items():
        lines.append(f"  {pfx:<40} +{d['added']:<6} ~{d['changed']:<6} {mb(d['bytes']):>10} "
                     f"{mb(d['gz']):>10} gz")
    return "\n".join(lines)


# =============================================================================
# main
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", required=True,
                    help="an EMPTY or absent directory; every write lands here")
    ap.add_argument("--env", default=os.path.join(ROOT, ".env"),
                    help="the .env whose store, tree and credentials to preflight "
                         "(default: this clone's)")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--stage", action="append", choices=("fixtures", "air_rz"), default=[],
                    help="also build a staged export feature (a code change to publish)")
    ap.add_argument("--no-analytics-publish", action="store_true",
                    help="export the analytics store as it is, without re-running the publishers")
    ap.add_argument("--analytics-only", action="append",
                    help="run only these publishers (spine, team_units, pace, schedule, "
                         "residual, vacancy, deltas)")
    ap.add_argument("--no-asof", action="store_true",
                    help="run analytics.residual with --no-asof (the five as-of keys stay out)")
    ap.add_argument("--no-lab", action="store_true")
    ap.add_argument("--no-board", action="store_true")
    ap.add_argument("--no-bucket", action="store_true",
                    help="skip the R2 reads (listing and dry-run upload)")
    ap.add_argument("--json", help="write the full report here (default <scratch>/preflight.json)")
    a = ap.parse_args(argv)

    scratch = os.path.abspath(a.scratch)
    if os.path.exists(scratch) and os.listdir(scratch):
        raise SystemExit(f"{scratch} is not empty - use a fresh directory")
    os.makedirs(scratch, exist_ok=True)
    live = setup_env(a.env, scratch)
    store_dir = patch(live["db"], scratch)
    import config
    from jobs import export_web as E
    assert os.path.normcase(os.path.abspath(config.WEB_EXPORT_DIR)) == \
        os.path.normcase(os.path.join(scratch, "web")), "WEB_EXPORT_DIR is not the scratch tree"
    log(f"preflight: live store {live['db']} (read-only), live tree {live['web']}")
    log(f"preflight: every write -> {scratch} (storage root {store_dir}); "
        "write-capable opens of the live store raise")

    report = {"scratch": scratch, "started": E.iso(), "steps": {}}
    snap = snapshot(live, scratch)
    report["snapshot"] = {"metrics_before": snap["metrics_before"]}
    web = snap["web"]

    if not a.no_analytics_publish:
        report["steps"]["analytics_publish"] = run_analytics_publishers(
            a.season, set(a.analytics_only) if a.analytics_only else None, no_asof=a.no_asof)

    t0 = time.time()
    site = run_site_export(web, snap["slugs"], tuple(a.stage) or None)
    report["steps"]["site_export"] = {
        k: site.get(k) for k in ("refreshed", "counts", "stages", "slugs_added", "runtime_s",
                                 "retention_holds", "sources", "market", "players", "teams",
                                 "components", "research", "manifest", "main_line")}
    log(f"site export: {time.time() - t0:.0f}s, refreshed {site['refreshed']}")

    ax = run_analytics_export(web)
    report["steps"]["analytics_export"] = ax
    log(f"analytics export: {ax}")

    declared_by = [(p, "jobs.export_web") for p in site["refreshed"]]
    declared_by += [(p, "analytics.export") for p in ax["declared"]]
    if not a.no_lab:
        try:
            lab = run_lab(live["db"], scratch, web)
            report["steps"]["lab"] = lab
            declared_by += [(p, "jobs.lab_publish (not declared by weekly_refresh)")
                            for p in lab["declared"]]
        except Exception as e:  # noqa: BLE001 - reported, not hidden
            report["steps"]["lab"] = {"error": f"{type(e).__name__}: {e}"}
            log(f"lab: FAILED {type(e).__name__}: {e}")
    board_dest = os.path.join(scratch, "board")
    if not a.no_board:
        try:
            report["steps"]["board"] = run_board(live["db"], a.season, board_dest)
        except Exception as e:  # noqa: BLE001
            report["steps"]["board"] = {"error": f"{type(e).__name__}: {e}"}
            log(f"board: FAILED {type(e).__name__}: {e}")

    # What weekly_refresh would pass: the site's and analytics' declarations.
    # lab/ is added here because this preflight also builds it; the runbook
    # passes it explicitly for the same reason.
    refreshed = [p for p, _b in declared_by]
    report["refreshed"] = refreshed

    state = json.load(open(os.path.join(web, E.STATE_FILE), encoding="utf-8"))
    p = plan(web, state, refreshed)
    s = summarise(p, declared_by)
    report["web"] = s
    report["asof"] = asof_report(p["local"])

    upload_result, bucket_cmp = {}, None
    if not a.no_bucket:
        client = E.r2_client()
        bucket = E.require_setting("WEB_R2_BUCKET")
        upload_result = E.upload(dest=web, client=client, dry_run=True, refreshed=refreshed)
        report["upload_dry_run"] = upload_result
        mismatch = {k: (upload_result.get(k), v) for k, v in (
            ("changed", s["upload"]["count"]), ("removed", s["removed"]["count"]),
            ("removed_withheld", s["removed_withheld"]["count"])) if upload_result.get(k) != v}
        if mismatch:
            raise SystemExit(f"the key-level plan disagrees with upload(dry_run): {mismatch}")
        listing = bucket_listing(client, bucket)
        bucket_cmp = against_bucket(p["local"], listing)
        others = sorted(k for k in listing if k not in p["local"]
                        and not k.startswith(("_state/", E.LIVE_PREFIX, E.BOARD_PREFIX)))
        report["bucket"] = {"objects": len(listing),
                            **{k: len(v) for k, v in bucket_cmp.items()},
                            "differs_keys": bucket_cmp["differs"][:200],
                            "bucket_only_not_in_tree": len(others),
                            "bucket_only_prefixes": sorted({prefix_of(k) for k in others}),
                            "bucket_only_keys": others[:200]}
        if os.path.isdir(board_dest):
            bstate = E.load_upload_state(board_dest, client, bucket,
                                         state_key=E.BOARD_STATE_KEY)[0]
            bp = plan(board_dest, bstate, None, tree="board")
            report["board_upload_dry_run"] = E.upload(dest=board_dest, client=client,
                                                      dry_run=True, tree="board")
            report["board"] = summarise(bp, [])
    report["finished"] = E.iso()
    out = a.json or os.path.join(scratch, "preflight.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    log(table(s, bucket_cmp, upload_result))
    if report.get("board"):
        b = report["board"]
        log(f"\nboard tree (its own upload, deletes nothing): +{b['added']['count']} "
            f"~{b['changed']['count']} ({mb(b['upload']['bytes'])}, {mb(b['upload']['gz'])} gz)")
    for r in report["asof"]:
        log(f"as-of: {r['key']}  {mb(r['bytes'])} raw, {mb(r['gz'])} gz, {r['values']:,} values, "
            f"{r['seconds_at']} s gz transfer")
    log(f"\nfull report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
