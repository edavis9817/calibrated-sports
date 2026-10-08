"""What the logger did (unit a-74). One read of the logger's own bookkeeping -> one file.

    python -m jobs.logger_activity --dry-run             # read, validate, print a summary
    python -m jobs.logger_activity --dest D:/scratch/x   # write <dest>/board/nfl/logger.json
    python -m jobs.logger_activity --dest ... --print    # also print the whole file

Writes ONE key, kind `logger_activity`, through `export_web.sync_keys` with no
owned prefix (validated against the contract, deletes nothing):

    board/{sport}/logger.json

It lives in the Board's tree because the Board's tick is the only thing that
runs every few minutes from the production clone; `jobs.board_read._tick` calls
`report_step`, which rebuilds at most once per `EVERY_MIN` and CANNOT fail the
tick - every refusal is one log line.

READ-ONLY ON THE STORE. The logger owns `market_log.db`. Every query here opens
it `mode=ro`; nothing is written, no PRAGMA is issued. A `mode=ro` reader still
pins the WAL while its transaction is open (a-07), so the read is one short
session, and the one wide read (`market_depth`, ~17M rows, by day on its
primary key) is the reason this runs hourly rather than every tick.

THE FOUR QUESTIONS, AND WHAT EACH ANSWER IS MEASURED FROM.

 1. UPTIME is computed from SUCCESSFUL-POLL GAPS, never from process liveness
    (pre-registered in the a-74 brief, before any number was read). `_bookkeep`
    in run_logger.py never raises, so a live process that records nothing leaves
    no trace but a gap - and a logger that records nothing is down for every
    purpose this file serves. A GAP is an interval longer than GAP_S (300 s)
    between two consecutive `poll_log` rows with ok = 1, any venue, any endpoint.
    Minutes up = the window minus the gaps inside it. The window opens at the
    first poll on record and closes at the read time, so a logger that is down
    NOW shows an open gap rather than a clean record.
    POST HOC, labelled as such: each gap carries `cadence_floor`, true when it is
    no longer than CADENCE_FLOOR_S (330 s). The slowest quote tier is 300 s, so
    on a night with nothing but futures and cold markets consecutive polls sit
    298-310 s apart and the 300 s rule fires on jitter. Those gaps STILL COUNT as
    down in every figure here - the flag only tells a reader which is which.
 2. CADENCE is seconds between consecutive successful polls of ONE venue and
    ONE endpoint (`quotes:<tier>`, `discovery`, `snapshot`) inside one week. A
    tier is polled only while it holds markets, so the gap between two game
    days is in the distribution: read the median as the cadence and the p95 as
    how often it was not. Every (venue, endpoint) ever seen gets a row in every
    week; one with no successful poll that week carries nulls and is named in
    `silent`, never dropped.
 3. COVERAGE counts, per week and venue, the markets the catalogue held
    (`markets_polled`: [first_seen, last_seen] overlaps the week - discovery
    refreshes last_seen, and the logger polls what the catalogue holds), those
    with any quote row inside the week (`markets_quoted`), and those carrying an
    outcome (`markets_mapped`, AS OF THE READ - not as of the week). Props are
    counted for the week their GAME is in, and `props_mapped_before_kickoff` is
    the one that matters (a-68): the outcome existed before its own kickoff,
    from `outcomes.created_ts`, which a re-map does not rewrite. It dates the
    OUTCOME, not this market's link to it - Polymarket only links to outcomes
    another venue created - so it is an upper bound for a link-only venue.
 4. THE WATCHDOG. Two different things carry that name and both are reported:
    the RESTART watchdog (scheduled `start_logger.ps1 -Ensure`, which writes
    `<STORAGE_DIR>/logs/logger_watchdog.log` only when it acts) and the
    in-process DEAD-MAN switch (`run_logger.watchdog`, which writes the
    `liveness` row). `source_health` is one row per source - the latest state -
    so it can never hold a COUNT of anything; `fires_recorded_in_source_health`
    is what the table actually says, not what it could not say.

A WEEK is Tuesday 12:00 UTC to the next, labelled with the nflverse week whose
first kickoff falls inside it - one definition for every section.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import config
from analytics.staleness import PRIORITY_PROP_SERIES, current_season, ticker_day
from jobs import export_web as E

KIND = "logger_activity"
SPORT = "nfl"
GAP_S = 300                 # the pre-registered gap threshold
CADENCE_FLOOR_S = 330       # post hoc label only; changes no figure
EVERY_MIN = 60              # the tick rebuilds at most this often
RETENTION_DAYS = 14         # live quotes are pruned on ingest age (invariant 8)
COVERAGE_VENUES = ("kalshi", "polymarket")
DEPTH_VENUES = ("kalshi", "polymarket")
WEEK_S = 7 * 86400

DEFINITIONS = {
    "uptime": (f"Computed from successful-poll gaps, not process liveness: a gap is more than "
               f"{GAP_S} s between consecutive poll_log rows with ok = 1 (any venue, any "
               "endpoint); minutes up is the window minus the gaps inside it. The window runs "
               "from the first poll on record to the read time."),
    "cadence_floor": (f"Post hoc label: a gap of {CADENCE_FLOOR_S} s or less. The slowest quote "
                      "tier is 300 s, so a quiet night trips the 300 s rule on jitter. Flagged "
                      "gaps still count as down."),
    "cadence": ("Seconds between consecutive successful polls of one venue and one endpoint "
                "inside one week. A tier is polled only while it holds markets, so idle spans "
                "between game days are in the distribution."),
    "week": ("Tuesday 12:00 UTC to the next Tuesday 12:00 UTC, labelled with the nflverse week "
             "whose first kickoff falls inside it."),
    "markets_polled": ("Markets in the catalogue whose first_seen..last_seen span overlaps the "
                       "week. poll_log records how many markets a poll covered, never which."),
    "markets_quoted": (f"Of those, markets with at least one quote row inside the week. Live "
                       f"quotes are pruned {RETENTION_DAYS} days after ingestion unless held, so "
                       "an older week's figure is a floor (quoted_is_floor)."),
    "markets_mapped": "Of those, markets carrying an outcome AS OF THE READ, not as of the week.",
    "props": ("Player-prop markets counted in the week their game is in: the mapped game's "
              "kickoff, else the date in a Kalshi ticker, else a Polymarket close time."),
    "props_mapped_before_kickoff": ("Props whose outcome was created before that game's kickoff "
                                    "(outcomes.created_ts). It dates the outcome, not this "
                                    "market's link to it: an upper bound for a venue that only "
                                    "links to outcomes another venue created."),
    "watchdog": ("restart = the scheduled start_logger.ps1 -Ensure task, read from its own log, "
                 "which it writes only when it acts. deadman = the in-process switch that "
                 "writes the liveness row. source_health keeps one row per source, so it holds "
                 "a latest state and never a count."),
}


# ------------------------------------------------------------------ small pieces

def ro(path=None):
    return sqlite3.connect(f"file:{path or config.DB_PATH}?mode=ro", uri=True, timeout=30)


def key_for(sport=SPORT):
    return f"board/{sport}/logger.json"


def iso(ts):
    """ISO second, or None for anything that is not a plausible unix time."""
    if not isinstance(ts, (int, float)) or not 0 < ts < 4e9:
        return None
    return E.iso(ts)


def mins(seconds):
    return round(seconds / 60.0, 1)


def pctile(sorted_vals, q):
    """Nearest-rank percentile of an ascending list; None when empty."""
    if not sorted_vals:
        return None
    i = max(0, min(len(sorted_vals) - 1, int(-(-q * len(sorted_vals) // 1)) - 1))
    return sorted_vals[i]


_PATHS = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|c|d)/)[^\s'\"]*")


def scrub(text, limit=200):
    """A bookkeeping detail with local paths removed - this file is published."""
    if text is None:
        return None
    return _PATHS.sub("<path>", str(text))[:limit]


# ------------------------------------------------------------------ weeks

def week_windows(con, first_ts, as_of):
    """[{label, season, week, start, end}] covering first_ts..as_of, ascending."""
    out = []
    seasons = sorted({current_season(first_ts), current_season(as_of)})
    for season in seasons:
        for week, first in con.execute(
                "SELECT week, MIN(kickoff_ts) FROM nfl_games WHERE sport='nfl' AND season=? "
                "AND week IS NOT NULL AND kickoff_ts IS NOT NULL GROUP BY week ORDER BY week",
                (season,)):
            d = datetime.fromtimestamp(first, timezone.utc)
            tue = (d - timedelta(days=(d.weekday() - 1) % 7)).replace(
                hour=12, minute=0, second=0, microsecond=0)
            if tue.timestamp() > first:
                tue -= timedelta(days=7)
            start = tue.timestamp()
            out.append({"label": f"{season}-wk{int(week):02d}", "season": season,
                        "week": int(week), "start": start, "end": start + WEEK_S})
    out = [w for w in out if w["end"] > first_ts and w["start"] <= as_of]
    # Anything the schedule does not cover gets a dated week of its own rather
    # than vanishing: the logger ran, so the time is reported.
    covered = sorted((w["start"], w["end"]) for w in out)
    t = first_ts
    extra = []
    while t <= as_of:
        if not any(s <= t < e for s, e in covered):
            d = datetime.fromtimestamp(t, timezone.utc)
            tue = (d - timedelta(days=(d.weekday() - 1) % 7)).replace(
                hour=12, minute=0, second=0, microsecond=0)
            if tue.timestamp() > t:
                tue -= timedelta(days=7)
            s = tue.timestamp()
            nxt = min([cs for cs, _ in covered if cs > t] + [s + WEEK_S])
            extra.append({"label": "unscheduled-" + tue.strftime("%Y-%m-%d"), "season": None,
                          "week": None, "start": s, "end": nxt})
            covered.append((s, nxt))
            t = nxt
        else:
            t = next(e for s, e in covered if s <= t < e)
    return sorted(out + extra, key=lambda w: w["start"])


# ------------------------------------------------------------------ 1. uptime

def find_gaps(ok_ts, as_of):
    """Every gap over GAP_S between consecutive successful polls, plus the open
    one from the last poll to the read. `ok_ts` ascending."""
    gaps = []
    for a, b in zip(ok_ts, ok_ts[1:]):
        if b - a > GAP_S:
            gaps.append({"start": a, "end": b, "open": False})
    if ok_ts and as_of - ok_ts[-1] > GAP_S:
        gaps.append({"start": ok_ts[-1], "end": as_of, "open": True})
    return gaps


def window_uptime(gaps, lo, hi):
    """Uptime of [lo, hi) given the gaps: minutes, down minutes, the longest gap
    touching it (its FULL length - a gap that crosses midnight is one gap)."""
    span = max(0.0, hi - lo)
    down, touching = 0.0, []
    for g in gaps:
        ov = min(g["end"], hi) - max(g["start"], lo)
        if ov > 0:
            down += ov
            touching.append(g["end"] - g["start"])
    return {"window_min": mins(span), "up_min": mins(span - down), "down_min": mins(down),
            "up_share": round((span - down) / span, 6) if span else None,
            "gaps": len(touching),
            "longest_gap_min": mins(max(touching)) if touching else 0.0}


def uptime(polls, windows, as_of):
    ok_ts = [ts for ts, _v, _e, ok in polls if ok]
    if not ok_ts:
        return {"first_poll_at": None, "last_poll_at": None, "total": None, "gaps": [],
                "days": [], "weeks": []}
    first = ok_ts[0]
    gaps = find_gaps(ok_ts, as_of)
    counts = defaultdict(lambda: [0, 0])
    for ts, _v, _e, ok in polls:
        counts[int(ts // 86400)][0 if ok else 1] += 1
    days = []
    for d in range(int(first // 86400), int(as_of // 86400) + 1):
        lo, hi = max(d * 86400, first), min((d + 1) * 86400, as_of)
        if hi <= lo:
            continue
        row = {"day": datetime.fromtimestamp(d * 86400, timezone.utc).strftime("%Y-%m-%d")}
        row.update(window_uptime(gaps, lo, hi))
        row["polls_ok"], row["polls_failed"] = counts[d]
        days.append(row)
    weeks = []
    for w in windows:
        lo, hi = max(w["start"], first), min(w["end"], as_of)
        if hi <= lo:
            continue
        row = {"week": w["label"], "start": iso(w["start"]), "end": iso(w["end"]),
               "complete": w["start"] >= first and w["end"] <= as_of}
        row.update(window_uptime(gaps, lo, hi))
        weeks.append(row)
    return {
        "first_poll_at": iso(first), "last_poll_at": iso(ok_ts[-1]),
        "total": window_uptime(gaps, first, as_of),
        "gaps": [{"start": iso(g["start"]), "end": iso(g["end"]),
                  "minutes": mins(g["end"] - g["start"]), "open": g["open"],
                  "cadence_floor": (g["end"] - g["start"]) <= CADENCE_FLOOR_S} for g in gaps],
        "days": days, "weeks": weeks,
    }


# ------------------------------------------------------------------ 2. cadence

def configured_s(endpoint):
    """The cadence config asks for, or None where there is no single number."""
    tier = endpoint.split(":", 1)[1] if endpoint.startswith("quotes:") else endpoint
    return {"hot": config.POLL_HOT, "live": config.POLL_LIVE, "game": config.POLL_GAME,
            "cold": config.POLL_COLD, "futures": config.POLL_FUTURES,
            "discovery": 600}.get(tier)


def cadence(polls, windows):
    pairs = sorted({(v, e) for _ts, v, e, _ok in polls})
    venues = sorted({v for v, _e in pairs})
    last_ok = {}
    for ts, v, e, ok in polls:
        if ok:
            last_ok[(v, e)] = ts
    tiers = [{"venue": v, "endpoint": e, "configured_s": configured_s(e),
              "last_ok_at": iso(last_ok.get((v, e)))} for v, e in pairs]
    weeks = []
    for w in windows:
        ok, bad = defaultdict(list), defaultdict(int)
        for ts, v, e, good in polls:
            if w["start"] <= ts < w["end"]:
                if good:
                    ok[(v, e)].append(ts)
                else:
                    bad[(v, e)] += 1
        if not ok and not bad:
            continue
        rows = []
        for v, e in pairs:
            ts = ok.get((v, e), [])
            d = sorted(b - a for a, b in zip(ts, ts[1:]))
            rows.append({"venue": v, "endpoint": e, "polls_ok": len(ts),
                         "polls_failed": bad.get((v, e), 0),
                         "median_s": round(statistics.median(d), 1) if d else None,
                         "p95_s": round(pctile(d, 0.95), 1) if d else None,
                         "max_s": round(d[-1], 1) if d else None})
        weeks.append({
            "week": w["label"], "rows": rows,
            "venues_without_success": [v for v in venues
                                       if not any(ok.get((v, e)) for vv, e in pairs if vv == v)],
            "silent": [f"{v} {e}" for v, e in pairs if not ok.get((v, e))],
        })
    return {"tiers": tiers, "weeks": weeks}


# ------------------------------------------------------------------ 3. coverage

def game_kickoffs(con):
    best = {}
    for gid, ver, kick in con.execute(
            "SELECT game_id, data_version, kickoff_ts FROM nfl_games WHERE kickoff_ts IS NOT NULL"):
        cur = best.get(gid)
        if cur is None or (ver or "") >= cur[0]:
            best[gid] = ((ver or ""), kick)
    return {g: k for g, (_v, k) in best.items()}


def prop_game_ts(venue, market_id, close_ts, kickoff):
    """When this prop's game is, by the best evidence that does not come from the
    mapper where the mapper has said nothing."""
    if kickoff is not None:
        return kickoff
    if venue == "kalshi":
        td = ticker_day(market_id)
        return None if td is None else td[1] + 18 * 3600     # the ticker's date, mid-slate
    return close_ts


def coverage(con, windows, as_of, venues=COVERAGE_VENUES):
    kicks = game_kickoffs(con)
    floor_before = as_of - RETENTION_DAYS * 86400
    out = []
    per_venue = {}
    for venue in venues:
        per_venue[venue] = con.execute(
            "SELECT m.market_id, m.market_type, m.first_seen, m.last_seen, m.close_ts, "
            "mo.market_id IS NOT NULL, mo.outcome_id, o.created_ts, o.event_id "
            "FROM markets m LEFT JOIN market_outcome mo ON mo.venue=m.venue AND "
            "mo.market_id=m.market_id LEFT JOIN outcomes o ON o.outcome_id=mo.outcome_id "
            "WHERE m.venue=?", (venue,)).fetchall()
    for w in windows:
        lo, hi = w["start"], min(w["end"], as_of)
        if hi <= lo:
            continue
        rows = []
        for venue in venues:
            c = dict.fromkeys(("markets_polled", "markets_quoted", "markets_mapped",
                               "props_listed", "props_with_row", "props_mapped",
                               "props_mapped_before_kickoff", "props_undated"), 0)
            pri = dict.fromkeys(("listed", "mapped", "mapped_before_kickoff"), 0)
            for mid, mtype, fs, ls, close_ts, has_row, oid, created, event in per_venue[venue]:
                if fs is not None and ls is not None and fs < hi and ls >= lo:
                    c["markets_polled"] += 1
                    c["markets_mapped"] += oid is not None
                    c["markets_quoted"] += con.execute(
                        "SELECT EXISTS(SELECT 1 FROM quotes WHERE venue=? AND market_id=? "
                        "AND ts>=? AND ts<?)", (venue, mid, lo, hi)).fetchone()[0]
                if mtype != "prop":
                    continue
                kickoff = kicks.get(event) if event else None
                game_ts = prop_game_ts(venue, mid, close_ts, kickoff)
                if game_ts is None:
                    # counted once, in the week the catalogue first saw it
                    if fs is not None and lo <= fs < hi:
                        c["props_undated"] += 1
                    continue
                if not w["start"] <= game_ts < w["end"]:
                    continue
                before = (oid is not None and kickoff is not None and created is not None
                          and created < kickoff)
                c["props_listed"] += 1
                c["props_with_row"] += bool(has_row)
                c["props_mapped"] += oid is not None
                c["props_mapped_before_kickoff"] += before
                if venue == "kalshi" and mid.split("-", 1)[0] in PRIORITY_PROP_SERIES:
                    pri["listed"] += 1
                    pri["mapped"] += oid is not None
                    pri["mapped_before_kickoff"] += before
            row = {"venue": venue, **c,
                   "quoted_is_floor": lo < floor_before,
                   "props_before_kickoff_share": (
                       round(c["props_mapped_before_kickoff"] / c["props_listed"], 4)
                       if c["props_listed"] else None),
                   "priority_props": pri if venue == "kalshi" else None}
            rows.append(row)
        out.append({"week": w["label"], "complete": w["end"] <= as_of, "venues": rows})
    return {"venues": list(venues),
            "not_measured": [{"venue": "oddsapi",
                              "reason": ("snapshot-scheduled, not tier-polled; its book markets "
                                         "are keyed per book and per backfill, so a catalogue "
                                         "span is not a polling record. Its polls are in "
                                         "cadence.")}],
            "weeks": out}


# ------------------------------------------------------------------ depth

def parse_counters(detail):
    """Whole-number counters only: `allowlist_week=2026-wk5` and `elapsed=10.4`
    are not counts and are left out rather than truncated into one."""
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)(?=,|\s|$)", detail or "")}


def depth(con, windows, as_of, venues=DEPTH_VENUES):
    by_day = defaultdict(int)
    if windows:
        # One range read per UTC day on the primary key (ts leads it). Grouping the
        # whole table by a computed day sorts 17M rows: 92 s against ~8 s, measured.
        d0, d1 = int(windows[0]["start"] // 86400), int(as_of // 86400)
        for day in range(d0, d1 + 1):
            for venue, n in con.execute(
                    "SELECT venue, COUNT(*) FROM market_depth WHERE ts >= ? AND ts < ? "
                    "GROUP BY venue", (day * 86400, (day + 1) * 86400)):
                by_day[(venue, day)] = n
    shards = defaultdict(int)
    for venue, day, n in con.execute(
            "SELECT venue, day, COUNT(*) FROM raw_shards WHERE venue LIKE '%\\_depth' ESCAPE '\\' "
            "GROUP BY 1, 2"):
        shards[(venue[: -len("_depth")], day)] = n
    weeks = []
    for w in windows:
        lo, hi = w["start"], min(w["end"], as_of)
        if hi <= lo:
            continue
        days = range(int(lo // 86400), int((hi - 1) // 86400) + 1)
        names = [datetime.fromtimestamp(d * 86400, timezone.utc).strftime("%Y-%m-%d") for d in days]
        rows = []
        for v in venues:
            rows.append({
                "venue": v,
                # whole UTC days touching the week: a day on the boundary is in both
                "depth_rows": sum(by_day.get((v, d), 0) for d in days),
                "days_without_depth_rows": [n for d, n in zip(days, names)
                                            if not by_day.get((v, d))],
                "raw_book_shards": sum(shards.get((v, n), 0) for n in names),
                "days_without_raw_books": [n for n in names if not shards.get((v, n))],
            })
        weeks.append({"week": w["label"], "venues": rows})
    last = {}
    for v in venues:
        ds = [d for (vv, d), n in by_day.items() if vv == v and n]
        ss = [d for (vv, d), n in shards.items() if vv == v and n]
        last[v] = {"last_depth_row_day": (datetime.fromtimestamp(max(ds) * 86400, timezone.utc)
                                          .strftime("%Y-%m-%d") if ds else None),
                   "last_raw_book_day": max(ss) if ss else None}
    row = con.execute("SELECT ok, detail, updated_ts, last_fail_ts FROM source_health "
                      "WHERE source='depth_capture'").fetchone()
    latest = None
    if row:
        latest = {"ok": bool(row[0]), "read_at": iso(row[2]), "last_fail_at": iso(row[3]),
                  "counters": parse_counters(row[1])}
    return {"latest_cycle": latest,
            "last_seen": [{"venue": v, **last[v]} for v in venues], "weeks": weeks}


# ------------------------------------------------------------------ health

def health(con):
    rows = []
    for source, ok, detail, _wm, last_ok, last_fail, updated in con.execute(
            "SELECT source, ok, detail, watermark, last_ok_ts, last_fail_ts, updated_ts "
            "FROM source_health ORDER BY source"):
        rows.append({"source": source, "ok": bool(ok), "detail": scrub(detail),
                     "last_ok_at": iso(last_ok), "last_fail_at": iso(last_fail),
                     "updated_at": iso(updated)})
    return {"rows": len(rows), "failing": [r["source"] for r in rows if not r["ok"]],
            "sources": rows}


# ------------------------------------------------------------------ 4. watchdog

_WD_LINE = re.compile(r"^\ufeff?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (DOWN|STARTED|FAILED|UNCERTAIN)\b(.*)$")
_WD_AGE = re.compile(r"log last written (\d+)s ago")


def parse_watchdog_log(text):
    """[(ts, word, rest)] from logger_watchdog.log; unreadable lines are counted,
    not skipped silently."""
    events, unread = [], 0
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        m = _WD_LINE.match(line.strip())
        if not m:
            unread += 1
            continue
        events.append((E_parse(m.group(1)), m.group(2), m.group(3)))
    return events, unread


def E_parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def watchdog_log_path():
    return config.storage_path("logs", "logger_watchdog.log")


def watchdog(con, gaps, ok_ts, log_path=None):
    """The restart watchdog's fires from its own log, each tied to the poll gap it
    ended; and what source_health says about either watchdog."""
    path = log_path or watchdog_log_path()
    try:
        with open(path, encoding="utf-8") as f:
            text, readable = f.read(), True
    except OSError:
        text, readable = "", False
    events, unread = parse_watchdog_log(text)
    fires = []
    for i, (ts, word, rest) in enumerate(events):
        if word != "DOWN":
            continue
        nxt = events[i + 1] if i + 1 < len(events) else None
        outcome = nxt[1].lower() if nxt and nxt[1] in ("STARTED", "FAILED") else "unknown"
        age = _WD_AGE.search(rest)
        gap = next((g for g in gaps if g["start"] <= ts <= g["end"]), None)
        after = next((t for t in ok_ts if t >= ts), None)
        fires.append({
            "fired_at": iso(ts), "outcome": outcome,
            "restart_s": round(nxt[0] - ts, 1) if outcome != "unknown" else None,
            "log_idle_s_at_fire": int(age.group(1)) if age else None,
            "first_poll_after_s": round(after - ts, 1) if after is not None else None,
            "outage_min": mins(gap["end"] - gap["start"]) if gap else None,
            "outage_start": iso(gap["start"]) if gap else None,
        })
    names = [r[0] for r in con.execute("SELECT source FROM source_health")]
    wd_rows = [n for n in names if "watchdog" in n.lower()]
    live = con.execute("SELECT ok, detail, last_ok_ts, last_fail_ts, updated_ts "
                       "FROM source_health WHERE source='liveness'").fetchone()
    start = con.execute("SELECT detail, updated_ts FROM source_health "
                        "WHERE source='logger_start'").fetchone()
    long_gaps = [g for g in gaps if g["end"] - g["start"] > CADENCE_FLOOR_S]
    covered = sum(1 for g in long_gaps
                  if any(g["start"] <= E_parse(f["fired_at"]) <= g["end"] for f in fires))
    return {
        "restart": {
            "log_readable": readable, "log_lines_unread": unread,
            "fires": len(fires),
            "uncertain": sum(1 for _t, w, _r in events if w == "UNCERTAIN"),
            "events": fires,
            "fires_recorded_in_source_health": len(wd_rows),
            "source_health_rows": wd_rows,
            "gaps_over_floor": len(long_gaps),
            "gaps_over_floor_with_a_fire": covered,
        },
        "deadman": {
            "ok": bool(live[0]) if live else None,
            "detail": scrub(live[1]) if live else None,
            "last_ok_at": iso(live[2]) if live else None,
            "last_fail_at": iso(live[3]) if live else None,
            "updated_at": iso(live[4]) if live else None,
            "fires_countable": False,
        },
        "logger_start": ({"detail": scrub(start[0]), "at": iso(start[1])} if start else None),
    }


# ------------------------------------------------------------------ build / write

def build(db=None, now=None, log_path=None):
    as_of = time.time() if now is None else now
    con = ro(db)
    try:
        polls = con.execute("SELECT ts, venue, endpoint, ok FROM poll_log WHERE ts <= ? "
                            "ORDER BY ts", (as_of,)).fetchall()
        polls = [(ts, v, e or "(none)", bool(ok)) for ts, v, e, ok in polls]
        if not polls:
            raise SystemExit("poll_log holds no rows - there is no logger activity to report")
        first = polls[0][0]
        windows = week_windows(con, first, as_of)
        up = uptime(polls, windows, as_of)
        ok_ts = [ts for ts, _v, _e, ok in polls if ok]
        gaps = find_gaps(ok_ts, as_of)
        obj = E.envelope(KIND, E.iso(as_of), SPORT)
        obj.update({
            "read_at": E.iso(as_of),
            "definitions": DEFINITIONS,
            "thresholds": {"gap_s": GAP_S, "cadence_floor_s": CADENCE_FLOOR_S,
                           "retention_days": RETENTION_DAYS},
            "polls": {"rows": len(polls), "ok": len(ok_ts), "failed": len(polls) - len(ok_ts)},
            "uptime": up,
            "cadence": cadence(polls, windows),
            "coverage": coverage(con, windows, as_of),
            "depth": depth(con, windows, as_of),
            "health": health(con),
            "watchdog": watchdog(con, gaps, ok_ts, log_path),
        })
        return obj
    finally:
        con.close()


def write(dest, obj, dry_run=False):
    return E.sync_keys(dest, {key_for(obj["sport"]): obj}, [], dry_run=dry_run)


def is_due(dest, now, every_min=EVERY_MIN):
    try:
        with open(E.local_path(dest, key_for()), encoding="utf-8") as f:
            last = E_parse(json.load(f)["generated_at"])
    except (OSError, ValueError, KeyError):
        return True
    return now - last >= every_min * 60


def report_step(dest, db=None, log=print, now=None, every_min=EVERY_MIN, log_path=None):
    """The Board tick's reporting step. NEVER raises: a refusal here is one log
    line, because a report on the logger must not be able to stop the Board.
    -> a one-line statement of what it did."""
    try:
        now = time.time() if now is None else now
        if not is_due(dest, now, every_min):
            return "logger activity: not due"
        t = time.time()
        obj = build(db=db, now=now, log_path=log_path)
        written, _ = write(dest, obj)
        msg = (f"logger activity: {key_for()} written={written} in {time.time() - t:.1f}s - "
               + summary(obj))
        log(msg)
        return msg
    except BaseException as e:  # noqa: BLE001 - includes SystemExit; a report never fails the run
        if isinstance(e, KeyboardInterrupt):
            raise
        msg = f"logger activity NOT WRITTEN ({type(e).__name__}: {e}) - the tick continues"
        try:
            log(msg)
        except Exception:  # noqa: BLE001
            pass
        return msg


def summary(obj):
    up, wd = obj["uptime"], obj["watchdog"]["restart"]
    real = [g for g in up["gaps"] if not g["cadence_floor"]]
    return (f"up {up['total']['up_share']:.4%} of {up['total']['window_min']:.0f} min; "
            f"{len(up['gaps'])} gap(s) over {GAP_S}s, {len(real)} over {CADENCE_FLOOR_S}s, "
            f"longest {up['total']['longest_gap_min']} min; watchdog fired {wd['fires']}x, "
            f"{wd['fires_recorded_in_source_health']} recorded in source_health; "
            f"failing sources: {obj['health']['failing'] or 'none'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dest", help="tree to write board/nfl/logger.json into")
    ap.add_argument("--dry-run", action="store_true", help="build and validate; write nothing")
    ap.add_argument("--print", dest="show", action="store_true", help="print the whole file")
    ap.add_argument("--db", help="store to read (mode=ro); default config.DB_PATH")
    a = ap.parse_args(argv)
    if not a.dest and not a.dry_run:
        ap.error("--dest or --dry-run (there is no default tree: a job that can publish is told where)")
    obj = build(db=a.db)
    if a.dry_run:
        E.validate_contract({key_for(): obj})
        print("valid against the contract; nothing written")
    else:
        written, _ = write(a.dest, obj)
        print(f"{key_for()} written={written} under {a.dest}")
    print(summary(obj))
    for g in obj["uptime"]["gaps"]:
        if not g["cadence_floor"]:
            print(f"  gap {g['start']} -> {g['end']}  {g['minutes']} min"
                  + ("  OPEN" if g["open"] else ""))
    if a.show:
        print(json.dumps(obj, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
