"""BRIEF C01 PART 2 - college results from collegefootballdata.com.

    python jobs/ingest_cfbd.py --status            # ledger only, 0 requests
    python jobs/ingest_cfbd.py --week 2            # 1 request
    python jobs/ingest_cfbd.py --from-archive      # re-parse, 0 requests

MINIMUM VIABLE. Game id, teams, final score and the QUARTER-BY-QUARTER line
score. No players, no rosters, no crosswalk - college is team-level and that is
the entire reason this is tractable at all.

THE BUDGET IS THE DESIGN
1,000 requests per CALENDAR MONTH, free tier, and no per-call weighting: one
HTTP request is one unit whatever it returns. So `/games?year=&week=` - which
returns a whole week WITH line scores - is the only endpoint used here. A
season is ~15 requests that way and ~800 as a per-game loop, which is how a
month disappears in one run. **If a per-game call ever looks necessary, stop
and say so rather than running it.** A full historical backfill is a Starter
Pack CSV download, not an API job.

Every request is logged to `cfbd_requests` with the quota the server reported,
and the job refuses to start when the last known remaining is at or below
`config.CFBD_RESERVE`. That is the same shape as `ODDS_RESERVE`, for the same
reason: a budget nobody checks is a budget already spent.

RAW FIRST, AND HERE IT IS A COST CONTROL
Invariant 2 says archive verbatim before parsing. On a metered API that stops
being hygiene and becomes the difference between a parser fix costing nothing
and costing the month. `--from-archive` re-derives every table from the
gzipped shards at zero requests, and it is the path every re-analysis should
take. The Odds API backfill re-parsed four times for free because of this.

WRITES TO THE CFB PROBE DATABASE, NOT THE LOGGER'S. `config.DB_PATH` is
repointed in `main()` before `store` is touched, exactly as `cfb_probe.py` does
it, so `archive_raw`'s shard registration lands in the probe's manifest and the
live NFL database is never opened. STORAGE LOCATION COMES FROM CONFIG, NEVER
FROM A PATH LITERAL.
"""
import argparse
import calendar
import gzip
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import config

ET = ZoneInfo("America/New_York")
CFB_DB = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)),
                      "cfb_probe.db")
VENUE = "cfb_cfbd"

store = None            # bound in main(), after config.DB_PATH is repointed


SCHEMA = """
CREATE TABLE IF NOT EXISTS cfbd_games (
    game_id       INTEGER PRIMARY KEY,
    season        INTEGER,
    week          INTEGER,
    season_type   TEXT,
    start_ts      REAL,
    completed     INTEGER,
    neutral_site  INTEGER,
    conference_game INTEGER,
    home_team     TEXT, home_id INTEGER, home_conf TEXT, home_class TEXT,
    away_team     TEXT, away_id INTEGER, away_conf TEXT, away_class TEXT,
    home_points   INTEGER, away_points INTEGER,
    home_q1 INTEGER, home_q2 INTEGER, home_q3 INTEGER, home_q4 INTEGER, home_ot INTEGER,
    away_q1 INTEGER, away_q2 INTEGER, away_q3 INTEGER, away_q4 INTEGER, away_ot INTEGER,
    n_periods     INTEGER,
    ingest_ts     REAL
);
CREATE INDEX IF NOT EXISTS ix_cfbd_week ON cfbd_games(season, week);
CREATE INDEX IF NOT EXISTS ix_cfbd_start ON cfbd_games(start_ts);

CREATE TABLE IF NOT EXISTS cfbd_requests (
    ts        REAL,
    month     TEXT,
    endpoint  TEXT,
    params    TEXT,
    status    INTEGER,
    remaining INTEGER,
    bytes     INTEGER,
    shard     TEXT
);
CREATE INDEX IF NOT EXISTS ix_cfbd_req_month ON cfbd_requests(month);
"""


def conn():
    c = sqlite3.connect(CFB_DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def month_key(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m")


# =============================================================================
# the ledger
# =============================================================================

def ledger(c):
    """(this month's requests, last reported remaining, reserve floor)."""
    m = month_key()
    n = c.execute("SELECT COUNT(*) FROM cfbd_requests WHERE month=?", (m,)).fetchone()[0]
    r = c.execute("SELECT remaining FROM cfbd_requests WHERE remaining IS NOT NULL "
                  "ORDER BY ts DESC LIMIT 1").fetchone()
    return n, (r[0] if r else None), config.CFBD_RESERVE


def budget_ok(c):
    """Refuse BEFORE spending, using the last quota the server reported. When
    nothing has ever been reported, fall back to this month's own count against
    the configured budget - an unknown quota must not read as an infinite one."""
    used, remaining, reserve = ledger(c)
    if remaining is not None:
        return remaining > reserve, (
            f"server reported {remaining} remaining, reserve {reserve}")
    left = config.CFBD_MONTHLY_BUDGET - used
    return left > reserve, (
        f"no quota header seen yet; {used} requests logged this month, "
        f"{left} of {config.CFBD_MONTHLY_BUDGET} nominally left")


def status(c):
    used, remaining, reserve = ledger(c)
    print(f"CFBD ledger  db={CFB_DB}")
    print(f"  month              {month_key()}")
    print(f"  requests logged    {used}")
    print(f"  server remaining   {remaining if remaining is not None else 'unknown'}")
    print(f"  reserve floor      {reserve}")
    ok, why = budget_ok(c)
    print(f"  spend allowed      {ok}  ({why})")
    g = c.execute("SELECT COUNT(*), SUM(completed), MIN(week), MAX(week) "
                  "FROM cfbd_games").fetchone()
    print(f"  games stored       {g[0] or 0} ({g[1] or 0} completed), "
          f"weeks {g[2]}-{g[3]}")
    print("\n  recent requests")
    for ts, ep, pr, st, rem, by in c.execute(
            "SELECT ts, endpoint, params, status, remaining, bytes "
            "FROM cfbd_requests ORDER BY ts DESC LIMIT 10"):
        print(f"    {datetime.fromtimestamp(ts, ET):%m-%d %H:%M}  {ep:<8}"
              f"{str(pr):<28}{st:>5}  remaining {rem}  {by or 0:>8,}B")


# =============================================================================
# the one request shape this job is allowed to make
# =============================================================================

def fetch_week(c, year, week, season_type="regular"):
    """ONE request: every game of one week, with line scores. Archived verbatim
    before a single field is read."""
    ok, why = budget_ok(c)
    if not ok:
        raise SystemExit(f"REFUSING to spend: {why}")
    if not config.CFBD_API_KEY:
        raise SystemExit("CFBD_API_KEY is not set")

    params = {"year": year, "week": week, "seasonType": season_type}
    url = f"{config.CFBD_BASE}/games"
    r = httpx.get(url, params=params, timeout=90, headers={
        "Authorization": f"Bearer {config.CFBD_API_KEY}",
        "Accept": "application/json",
        "User-Agent": config.USER_AGENT})
    remaining = r.headers.get("x-calllimit-remaining")
    remaining = int(remaining) if remaining and remaining.isdigit() else None

    shard = None
    payload = None
    if r.status_code == 200:
        payload = r.json()
        # ARCHIVE BEFORE PARSING. Everything downstream can be rebuilt from
        # this file for free; nothing downstream can be rebuilt from a request
        # we did not keep.
        shard = store.archive_raw(VENUE, f"games:{year}:w{week}:{season_type}",
                                  payload)
    c.execute("INSERT INTO cfbd_requests VALUES (?,?,?,?,?,?,?,?)",
              (time.time(), month_key(), "games", json.dumps(params),
               r.status_code, remaining, len(r.content), shard))
    c.commit()

    if r.status_code != 200:
        raise SystemExit(
            f"CFBD returned {r.status_code}: {r.text[:200]}\n"
            f"  the request WAS counted against the budget and is logged.")
    if not isinstance(payload, list):
        raise SystemExit(f"expected a list of games, got {type(payload).__name__}")
    print(f"  fetched {len(payload)} games, {len(r.content):,}B, "
          f"remaining {remaining}, shard {shard}")
    return payload


# =============================================================================
# parsing - runs from a payload, and a payload comes from the archive for free
# =============================================================================

def _periods(ls):
    """Line score array -> (q1, q2, q3, q4, overtime total, count).

    A game that went to overtime has more than four entries; the surplus is
    summed into one OT column rather than dropped, because a total settles on
    the FINAL score and quarter analysis must be able to exclude those games.
    """
    ls = list(ls or [])
    q = [ls[i] if i < len(ls) else None for i in range(4)]
    ot = sum(x for x in ls[4:] if isinstance(x, int)) if len(ls) > 4 else None
    return q[0], q[1], q[2], q[3], ot, len(ls)


def parse(c, games, quiet=False):
    rows = []
    now = time.time()
    for g in games:
        if not isinstance(g, dict) or g.get("id") is None:
            continue
        hs = _periods(g.get("homeLineScores"))
        as_ = _periods(g.get("awayLineScores"))
        try:
            start = datetime.fromisoformat(
                str(g.get("startDate", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            start = None
        rows.append((
            g["id"], g.get("season"), g.get("week"), g.get("seasonType"),
            start, int(bool(g.get("completed"))), int(bool(g.get("neutralSite"))),
            int(bool(g.get("conferenceGame"))),
            g.get("homeTeam"), g.get("homeId"), g.get("homeConference"),
            g.get("homeClassification"),
            g.get("awayTeam"), g.get("awayId"), g.get("awayConference"),
            g.get("awayClassification"),
            g.get("homePoints"), g.get("awayPoints"),
            hs[0], hs[1], hs[2], hs[3], hs[4],
            as_[0], as_[1], as_[2], as_[3], as_[4],
            max(hs[5], as_[5]), now))
    c.executemany(
        "INSERT INTO cfbd_games VALUES (" + ",".join("?" * 30) + ") "
        "ON CONFLICT(game_id) DO UPDATE SET "
        + ",".join(f"{col}=excluded.{col}" for col in (
            "season", "week", "season_type", "start_ts", "completed",
            "neutral_site", "conference_game", "home_team", "home_id",
            "home_conf", "home_class", "away_team", "away_id", "away_conf",
            "away_class", "home_points", "away_points",
            "home_q1", "home_q2", "home_q3", "home_q4", "home_ot",
            "away_q1", "away_q2", "away_q3", "away_q4", "away_ot",
            "n_periods", "ingest_ts")),
        rows)
    c.commit()
    if not quiet:
        done = sum(1 for g in games if g.get("completed"))
        ls = sum(1 for g in games if g.get("homeLineScores"))
        print(f"  parsed {len(rows)} games ({done} completed, {ls} with line scores)")
    return len(rows)


def from_archive(c):
    """Re-derive every row from the gzipped shards. ZERO requests."""
    root = os.path.join(config.RAW_DIR, VENUE)
    if not os.path.isdir(root):
        raise SystemExit(f"no archive at {root} - nothing to re-parse")
    total = files = bad = 0
    for day in sorted(os.listdir(root)):
        d = os.path.join(root, day)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".jsonl.gz"):
                continue
            path = os.path.join(d, fn)
            files += 1
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    for line in f:
                        rec = json.loads(line)
                        total += parse(c, rec.get("payload") or [], quiet=True)
            except (OSError, EOFError, json.JSONDecodeError) as e:
                # 43 of 114 CFB shards were destroyed by the two-writer
                # incident. Report and continue: a readable shard must not be
                # held hostage to an unreadable one.
                bad += 1
                print(f"  UNREADABLE {day}/{fn}: {type(e).__name__}")
    print(f"  re-parsed {total} game-rows from {files} shards, {bad} unreadable, "
          f"0 requests")
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", type=int)
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--season-type", default="regular")
    ap.add_argument("--from-archive", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    # REPOINT BEFORE store IS TOUCHED, so shard registration and every other
    # store write land in the probe's database. The NFL logger is live.
    config.DB_PATH = CFB_DB
    global store
    import store as _store
    store = _store

    c = conn()
    if a.status or (a.week is None and not a.from_archive):
        status(c)
        return
    if a.from_archive:
        from_archive(c)
        status(c)
        return
    print(f"CFBD /games year={a.year} week={a.week} type={a.season_type}  "
          f"(1 request)")
    parse(c, fetch_week(c, a.year, a.week, a.season_type))
    status(c)


if __name__ == "__main__":
    main()
