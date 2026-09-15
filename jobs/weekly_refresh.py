"""The weekly site refresh - one entry point, one log (brief W02 A4).

    python -m jobs.weekly_refresh
    python -m jobs.weekly_refresh --skip-ingest --no-push

Steps, in order, each logged with a timestamp to
config.storage_path("logs", "weekly_refresh.log"):

  1. nflverse ingest     jobs.ingest_nflverse --tier live --season <season>
                         (a failure DEGRADES: logged WARN, the export still runs
                         and marks the data stale)
  2. market mapping      jobs.map_markets --venue kalshi   (failure: WARN)
  3. export              jobs.export_web                   (failure: ERROR, stop)
  4. gate                npm run check in WEB_REPO_DIR     (failure: ERROR, stop,
                         nothing committed - a broken site is never pushed)
  5. commit              git add public/data; commit ONLY if there is a diff
  6. push                git push origin main - Cloudflare Pages rebuilds on push

Late nflverse is not an error: the export sets manifest.current.stale with the
reason, the log records a WARN, and the next scheduled run picks the data up.

ACCEPTED DEBT (brief W02): this runs on the home PC, so the PC must be on.
Nothing here assumes the machine - every path comes from config - so moving it
to a cloud job against a hosted DB copy changes where it runs, not what it does.
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
from jobs.export_web import ConfigError, require_setting  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NPM = "npm.cmd" if os.name == "nt" else "npm"


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


def run(skip_ingest=False, no_push=False, runner=subprocess.run, log=None, now=None):
    """Returns the process exit code: 0 ok (including stale), 1 export failed,
    2 gate failed, 3 git failed, 4 configuration missing."""
    log = log or Log()
    try:
        repo = require_setting("WEB_REPO_DIR")
        data = require_setting("WEB_DATA_DIR")
    except ConfigError as e:
        log("ERROR", str(e))
        return 4
    py = sys.executable
    season = season_now(now)
    t0 = time.time()
    log("INFO", f"refresh start: season {season}, repo {repo}")

    def step(name, cmd, cwd, fatal):
        log("INFO", f"step {name}: {' '.join(cmd)}")
        r = runner(cmd, cwd=cwd, capture_output=True, text=True)
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
             ROOT, fatal=False)
    step("map", [py, "-m", "jobs.map_markets", "--venue", "kalshi"], ROOT, fatal=False)
    if step("export", [py, "-m", "jobs.export_web"], ROOT, fatal=True).returncode != 0:
        return 1

    stale, label = False, f"season {season}"
    try:
        with open(os.path.join(data, "manifest.json"), encoding="utf-8") as f:
            cur = json.load(f)["current"]
        label = f"{cur['season']} week {cur['week']}"
        stale = bool(cur.get("stale"))
        if stale:
            log("WARN", f"nflverse is late: {cur.get('stale_reason')} - exported anyway, "
                        "marked stale; the next run picks it up")
    except (OSError, ValueError, KeyError) as e:
        log("WARN", f"could not read manifest after export: {e}")

    if step("gate", [NPM, "run", "check"], repo, fatal=True).returncode != 0:
        log("ERROR", "site check failed - nothing committed, nothing pushed")
        return 2

    if step("stage", ["git", "add", "public/data"], repo, fatal=True).returncode != 0:
        return 3
    diff = runner(["git", "diff", "--cached", "--quiet"], cwd=repo, capture_output=True, text=True)
    if diff.returncode == 0:
        log("INFO", f"no data change - no commit ({time.time() - t0:.0f}s)")
        return 0
    msg = f"data: {label}" + (" (nflverse stale)" if stale else "")
    if step("commit", ["git", "commit", "-m", msg], repo, fatal=True).returncode != 0:
        return 3
    if no_push:
        log("INFO", "push skipped (--no-push)")
    elif step("push", ["git", "push", "origin", "main"], repo, fatal=True).returncode != 0:
        return 3
    log("INFO", f"refresh done in {time.time() - t0:.0f}s ({msg})")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    sys.exit(run(skip_ingest=a.skip_ingest, no_push=a.no_push))


if __name__ == "__main__":
    main()
