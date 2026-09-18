"""What the forward capture's weekly cap does at exactly-at-cap, simulated not argued.

    python -m research.cfb_forward_cap                  # this week, from the newest listing
    python -m research.cfb_forward_cap --drift 3        # move one kickoff 3 min into a new hour
    python -m research.cfb_forward_cap --cap 60         # try another cap
    python -m research.cfb_forward_cap --season-hours   # kickoff hours per CFB week, rest of season

0 requests. Reads the archived Odds API events listing and `cfb_games`, runs
`cfb.oddsapi_capture.plan_forward` against a THROWAWAY snapshots table on a 5-minute
tick across the week, and prints which kickoff hours would be bought and which
skipped. The allocation is evaluated when each hour's window opens, so the order in
which hours arrive matters and cannot be read off a sorted list.
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import oddsapi, oddsapi_capture, paths, schema
from cfb.oddsapi import iso_ts
from research.cfb_odds_coverage import load_events

TICK_S = 300


def utc(ts, fmt="%m-%d %H:%MZ"):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(fmt)


def scratch_db(spent_hours):
    """A snapshots table with only the hours already bought this week."""
    path = os.path.join(tempfile.mkdtemp(prefix="cfb_cap_"), "sim.db")
    con = sqlite3.connect(path)
    con.executescript(schema.ddl())
    for hour, week, earliest, n, cost in spent_hours:
        con.execute("INSERT INTO cfb_odds_snapshots (hour_ts, week_start_ts, "
                    "earliest_commence_ts, n_events, cost, outcome) VALUES (?,?,?,?,?,'captured')",
                    (hour, week, earliest, n, cost))
    con.commit()
    return con


def simulate(events, week, spent, cap, verbose=True):
    """Tick every 5 minutes across the week. Returns (captured, skipped, missed)."""
    con = scratch_db(spent)
    groups = {h: g for h, g in oddsapi_capture.hour_groups(events).items() if g["week"] == week}
    if not groups:
        raise SystemExit("no kickoff hours in this CFB week in the listing")
    start = min(g["earliest"] for g in groups.values()) - 3600
    end = max(g["earliest"] for g in groups.values()) + 3600
    captured, skipped, missed = [], [], []
    now = start
    while now <= end:
        due, miss, skip = oddsapi_capture.plan_forward(con, events, now, cap=cap)
        for hour, g in miss:
            con.execute("INSERT OR IGNORE INTO cfb_odds_snapshots (hour_ts, week_start_ts, "
                        "earliest_commence_ts, n_events, outcome) VALUES (?,?,?,?,'missed')",
                        (hour, g["week"], g["earliest"], g["n"]))
            missed.append((hour, g))
        for hour, g in skip:
            con.execute("INSERT OR IGNORE INTO cfb_odds_snapshots (hour_ts, week_start_ts, "
                        "earliest_commence_ts, n_events, outcome) VALUES (?,?,?,?,"
                        "'skipped_weekly_cap')", (hour, g["week"], g["earliest"], g["n"]))
            skipped.append((hour, g))
        for hour, g, left in due:
            con.execute("INSERT OR REPLACE INTO cfb_odds_snapshots (hour_ts, week_start_ts, "
                        "earliest_commence_ts, n_events, fired_ts, cost, outcome) "
                        "VALUES (?,?,?,?,?,3,'captured')", (hour, g["week"], g["earliest"], g["n"], now))
            captured.append((hour, g, now))
        con.commit()
        now += TICK_S
    spend = con.execute("SELECT COALESCE(SUM(cost), 0) FROM cfb_odds_snapshots WHERE "
                        "week_start_ts=?", (week,)).fetchone()[0]
    if verbose:
        print(f"  captured {len(captured)}  skipped {len(skipped)}  missed {len(missed)}  "
              f"week spend {spend} of {cap}")
    con.close()
    return captured, skipped, missed


def drift(events, minutes, verbose=True):
    """Move the LAST kickoff of the busiest hour later, as tonight's game moved 23:30 -> 23:33."""
    groups = oddsapi_capture.hour_groups(events)
    busiest = max(groups, key=lambda h: groups[h]["n"])
    latest = max((e for e in events if iso_ts(e["commence_time"]) // 3600 * 3600 == busiest),
                 key=lambda e: iso_ts(e["commence_time"]))
    out = []
    for e in events:
        if e is latest:
            t = iso_ts(e["commence_time"]) + minutes * 60
            e = dict(e, commence_time=datetime.fromtimestamp(t, timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"))
            if verbose:
                print(f"  drifted {e['away_team']} @ {e['home_team']} "
                      f"{utc(iso_ts(latest['commence_time']))} -> {utc(t)}")
        out.append(e)
    return out


def season_hours(conn, season=2026):
    """Kickoff hours per CFB week for the rest of the season, FBS-involving games only -
    the Odds API lists FBS and FBS/FCS, so this is the shape the cap has to cover."""
    rows = conn.execute(
        "SELECT start_ts FROM cfb_games WHERE valid_to_ts IS NULL AND season=? AND "
        "season_type='regular' AND (home_division='fbs' OR away_division='fbs') AND "
        "start_time_tbd = 0", (season,)).fetchall()
    weeks = {}
    for (ts,) in rows:
        weeks.setdefault(oddsapi.week_start_ts(ts), set()).add(int(ts // 3600 * 3600))
    return sorted((w, len(h)) for w, h in weeks.items())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=oddsapi.FORWARD_WEEKLY_CAP)
    ap.add_argument("--drift", type=int, metavar="MINUTES", default=0)
    ap.add_argument("--season-hours", action="store_true")
    a = ap.parse_args(argv)
    con = sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)

    if a.season_hours:
        print("kickoff hours per CFB week, FBS-involving, kickoff time known "
              "(cfb_games; the cap must cover the biggest, not the median):")
        for week, n in season_hours(con):
            print(f"  week from {utc(week)}  hours {n:>3}  credits at 3/hour {n * 3:>3}")
        return 0

    (_fid, _rel, fetched), events = load_events(con)
    week = oddsapi.week_start_ts(time.time())
    spent = [(r[0], r[1], r[2], r[3], r[4]) for r in con.execute(
        "SELECT hour_ts, week_start_ts, earliest_commence_ts, n_events, cost FROM "
        "cfb_odds_snapshots WHERE week_start_ts=? AND outcome='captured'", (week,))]
    print(f"listing fetched {utc(fetched)}; CFB week from {utc(week)}; "
          f"already captured {len(spent)} hour(s), {sum(s[4] for s in spent)} credits; cap {a.cap}")
    groups = {h: g for h, g in oddsapi_capture.hour_groups(events).items() if g["week"] == week}
    print(f"kickoff hours listed this week: {len(groups)}  "
          f"({len(groups) * 3} credits at 3 each)")
    for h in sorted(groups):
        mark = " (bought)" if any(s[0] == h for s in spent) else ""
        print(f"  {utc(h)}  events {groups[h]['n']:>3}  first kickoff "
              f"{utc(groups[h]['earliest'])}{mark}")

    if a.drift:
        events = drift(events, a.drift)
    print("\nsimulating 5-minute ticks across the week:")
    captured, skipped, missed = simulate(events, week, spent, a.cap)
    for hour, g, when in captured:
        print(f"  CAPTURE {utc(hour)}  events {g['n']:>3}  fired {utc(when)}  "
              f"({(g['earliest'] - when) / 60:.0f} min before first kickoff)")
    for hour, g in skipped:
        print(f"  SKIP    {utc(hour)}  events {g['n']:>3}  first kickoff {utc(g['earliest'])}")
    for hour, g in missed:
        print(f"  MISSED  {utc(hour)}  events {g['n']:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
