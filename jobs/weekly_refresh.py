"""The weekly site refresh - one entry point, one log (contract v2).

    python -m jobs.weekly_refresh
    python -m jobs.weekly_refresh --skip-ingest

Steps, in order, each logged with a timestamp to
config.storage_path("logs", "weekly_refresh.log"):

  1. nflverse ingest   jobs.ingest_nflverse --tier live --season <season>
                       (a failure DEGRADES: WARN, the export still runs and
                       marks the data stale)
  1b. headshots        jobs.ingest_headshots --season <season> (archive only;
                       failure: WARN)
  2. market mapping    jobs.map_markets --venue kalshi       (failure: WARN)
  3. export            jobs.export_web                       (failure: ERROR, stop)
  3b. slug registry    if the export appended to web/slugs/, commit ONLY that path
                       in THIS repo (failure: WARN). URLs are only stable once
                       the registry is in git; nothing else is ever committed.
  4. upload            jobs.export_web --upload-only         (failure: ERROR, stop;
                       unconfigured R2 credentials log a line and exit 0)
  5. validate          GET {WEB_SITE_URL}/data/nfl/manifest.json and compare its
                       generated_at with the local export (mismatch or an
                       unreachable site: WARN - the site may not be on v2 yet)

A data refresh builds nothing and commits no DATA: the site reads R2 at runtime.
The one commit it may make is the slug registry in this repo (step 3b).
Late nflverse is not an error: the export sets current.stale with the reason, the
log records a WARN, and the next scheduled run picks the data up.

ACCEPTED DEBT (brief W02): this runs on the home PC, so the PC must be on.
Every path comes from config, so moving it to a cloud job changes where it runs,
not what it does.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from jobs.export_web import SPORT, ConfigError, local_path, require_setting  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Log:
    def __init__(self, path=None):
        self.path = path or config.storage_path("logs", "weekly_refresh.log")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def __call__(self, level, msg):
        line = f"{datetime.now().isoformat(timespec='seconds')} {level:<5} {msg}"
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def season_now(now=None):
    now = now or datetime.now()
    return now.year if now.month >= 3 else now.year - 1


def fetch_json(url, timeout=20):
    import httpx
    r = httpx.get(url, timeout=timeout, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.json()


SLUG_PATH = "web/slugs"


def commit_slug_registry(runner, log):
    """Commit web/slugs/ if the export appended slugs, and nothing else.

    `git commit -- web/slugs` commits only that pathspec, so anything else
    someone left staged in the repo is never swept into an automated commit.
    Never fatal: an uncommitted registry is still on disk and the next run
    retries; a refresh must not fail over it."""
    st = runner(["git", "status", "--porcelain", "--", SLUG_PATH],
                cwd=ROOT, capture_output=True, text=True)
    if st.returncode != 0:
        log("WARN", f"slug registry: git status failed ({st.returncode}) - not committed")
        return False
    lines = [ln for ln in (st.stdout or "").splitlines() if ln.strip()]
    if not lines:
        log("INFO", "slug registry: unchanged")
        return False
    add = runner(["git", "add", "--", SLUG_PATH], cwd=ROOT, capture_output=True, text=True)
    msg = f"slugs: registry append ({datetime.now().date().isoformat()}, weekly refresh)"
    com = runner(["git", "commit", "-m", msg, "--", SLUG_PATH],
                 cwd=ROOT, capture_output=True, text=True) if add.returncode == 0 else add
    if com.returncode != 0:
        log("WARN", f"slug registry: commit failed ({com.returncode}) - still on disk, next run retries")
        return False
    log("INFO", f"slug registry: committed {len(lines)} changed file(s)")
    return True


def run(skip_ingest=False, runner=subprocess.run, log=None, now=None, fetch=fetch_json):
    """Exit code: 0 ok (including stale and validation warnings), 1 export failed,
    2 upload failed, 4 configuration missing."""
    log = log or Log()
    try:
        dest = require_setting("WEB_EXPORT_DIR")
    except ConfigError as e:
        log("ERROR", str(e))
        return 4
    py = sys.executable
    season = season_now(now)
    t0 = time.time()
    log("INFO", f"refresh start: season {season}, export {dest}")

    def step(name, cmd, fatal):
        log("INFO", f"step {name}: {' '.join(cmd)}")
        r = runner(cmd, cwd=ROOT, capture_output=True, text=True)
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-5:]
        for t in tail:
            log("INFO", f"  {name} | {t}")
        if r.returncode != 0:
            log("ERROR" if fatal else "WARN", f"step {name} exited {r.returncode}")
        return r

    if skip_ingest:
        log("INFO", "step ingest: skipped (--skip-ingest)")
    else:
        step("ingest", [py, "-m", "jobs.ingest_nflverse", "--tier", "live", "--season", str(season)],
             fatal=False)
    # Archive-only re-derivation, so it runs even with --skip-ingest.
    step("headshots", [py, "-m", "jobs.ingest_headshots", "--season", str(season)], fatal=False)
    step("map", [py, "-m", "jobs.map_markets", "--venue", "kalshi"], fatal=False)
    if step("export", [py, "-m", "jobs.export_web"], fatal=True).returncode != 0:
        return 1
    commit_slug_registry(runner, log)

    local = None
    try:
        with open(local_path(dest, f"{SPORT}/manifest.json"), encoding="utf-8") as f:
            local = json.load(f)
        cur = local["current"]
        if cur.get("stale"):
            log("WARN", f"nflverse is late: {cur.get('stale_reason')} - exported anyway, "
                        "marked stale; the next run picks it up")
    except (OSError, ValueError, KeyError) as e:
        log("WARN", f"could not read the local manifest after export: {e}")

    if step("upload", [py, "-m", "jobs.export_web", "--upload-only"], fatal=True).returncode != 0:
        return 2

    site = getattr(config, "WEB_SITE_URL", None)
    if not site:
        log("WARN", "validate: WEB_SITE_URL is not set - skipped")
    elif local is not None:
        url = f"{site.rstrip('/')}/data/{SPORT}/manifest.json"
        try:
            remote = fetch(url)
            if remote.get("generated_at") == local.get("generated_at"):
                log("INFO", f"validate: live manifest matches the export ({local['generated_at']})")
            else:
                log("WARN", f"validate: live manifest generated_at {remote.get('generated_at')} "
                            f"!= local {local.get('generated_at')} - upload not configured, or the "
                            "site is not reading v2 yet")
        except Exception as e:  # noqa: BLE001 - any failure here is a warning, never fatal
            log("WARN", f"validate: {url} unreachable or not v2 ({type(e).__name__}: {e})")
    log("INFO", f"refresh done in {time.time() - t0:.0f}s")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-ingest", action="store_true")
    a = ap.parse_args()
    sys.exit(run(skip_ingest=a.skip_ingest))


if __name__ == "__main__":
    main()
