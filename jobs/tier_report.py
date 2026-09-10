"""What tier would every market get, at a given moment?

    python -m jobs.tier_report                       # right now
    python -m jobs.tier_report --at "2026-09-13 13:15"   # simulated, ET
    python -m jobs.tier_report --accept              # brief 008 acceptance

The unit tests pin `tier_for` against synthetic clocks. This runs it against
the real catalogue and the real schedule, which is the only way to catch the
failure that actually happened: the function was correct and the input was
wrong. `close_ts` meant kickoff on one venue and game end on the other, and
nothing in a unit test would have told you.

--accept checks the four criteria brief 008 settled on:

  1. the 1pm games read `live` at a simulated 13:15 ET
  2. Monday night reads `cold` at that same moment
  3. every game is at hot-or-faster cadence through its own T-90 inactive
     report and its final hour
  4. oddsapi appears in no tier polling line - it is snapshot-scheduled
"""
import argparse
import sqlite3
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import store
from run_logger import tier_for

ET = ZoneInfo("America/New_York")
LIVE_VENUES = ("kalshi", "polymarket")

CADENCE = {"hot": lambda: config.POLL_HOT, "live": lambda: config.POLL_LIVE,
           "game": lambda: config.POLL_GAME, "cold": lambda: config.POLL_COLD,
           "futures": lambda: config.POLL_FUTURES}
# hot and live are the two that sample fast enough to see a line move.
FAST = ("hot", "live")


def parse_at(text: str) -> float:
    """'2026-09-13 13:15' in US/Eastern -> unix seconds.

    Eastern, not UTC. nflverse `gametime` is Eastern and September is EDT while
    January is EST, so a fixed offset is wrong for half the postseason - the
    same trap that put every kickoff 4-5 hours early until the tzdata fix.
    """
    if not text:
        return time.time()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ET).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"cannot parse --at {text!r}; use 'YYYY-MM-DD HH:MM' (ET)")


def _markets(con, venues=LIVE_VENUES):
    ph = ",".join("?" for _ in venues)
    return [{"venue": v, "market_id": m, "market_type": t, "close_ts": c}
            for v, m, t, c in con.execute(
                f"SELECT venue, market_id, market_type, close_ts FROM markets "
                f"WHERE venue IN ({ph})", venues)]


def report(at_ts: float, venues=LIVE_VENUES):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    markets = _markets(con, venues)
    kicks = store.kickoff_map(venues)

    when = datetime.fromtimestamp(at_ts, ET)
    print(f"\nAS OF {when:%Y-%m-%d %H:%M %Z}   "
          f"({len(markets):,} markets, {len(kicks):,} with a mapped kickoff)")
    print(f"windows: hot <{config.HOT_WINDOW_MIN}m to kickoff | "
          f"live <{config.LIVE_WINDOW_MIN}m after | "
          f"game <{config.COLD_WINDOW_HOURS:g}h ahead | cold otherwise")

    by_venue = {}
    for m in markets:
        t = tier_for(m, kicks, at_ts)
        by_venue.setdefault(m["venue"], Counter())[t] += 1

    print(f"\n  {'venue':<12}" + "".join(f"{t:>10}" for t in CADENCE)
          + f"{'req/min':>10}")
    for venue in sorted(by_venue):
        c = by_venue[venue]
        # Rough: one request per 100 markets per tier interval (both venues
        # batch at 100). It is the SHAPE that matters, not the constant.
        rpm = sum(max(1, (c[t] + 99) // 100) * (60.0 / CADENCE[t]())
                  for t in CADENCE if c[t])
        print(f"  {venue:<12}" + "".join(f"{c[t]:>10,}" for t in CADENCE)
              + f"{rpm:>10.1f}")

    # Per kickoff slot, which is how a Sunday actually reads.
    rows = con.execute(
        """SELECT game_id, MAX(kickoff_ts), MAX(away_team), MAX(home_team)
             FROM nfl_games WHERE season=2026 AND kickoff_ts IS NOT NULL
            GROUP BY game_id ORDER BY 2""").fetchall()
    slots = {}
    for gid, kick, away, home in rows:
        if abs(kick - at_ts) <= 4 * 86400:
            slots.setdefault(kick, []).append((gid, away, home))
    if slots:
        print(f"\n  {'kickoff (ET)':<22}{'games':>7}{'T-':>10}  tier")
        for kick in sorted(slots):
            games = slots[kick]
            secs = kick - at_ts
            t = tier_for({"venue": "kalshi", "market_id": "_probe",
                          "market_type": "prop"},
                         {("kalshi", "_probe"): kick}, at_ts)
            hrs = secs / 3600.0
            print(f"  {datetime.fromtimestamp(kick, ET):%a %m-%d %H:%M}"
                  f"{'':<6}{len(games):>7}{hrs:>+9.1f}h  {t}"
                  f"   ({CADENCE[t]():g}s)")
    con.close()
    return by_venue, slots


# --------------------------------------------------------------------------
# the acceptance criteria
# --------------------------------------------------------------------------

def _tier_at(kick_ts, at_ts):
    return tier_for({"venue": "kalshi", "market_id": "_p", "market_type": "prop"},
                    {("kalshi", "_p"): kick_ts}, at_ts)


def accept(at_text="2026-09-13 13:15"):
    at_ts = parse_at(at_text)
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    games = con.execute(
        """SELECT game_id, MAX(kickoff_ts), MAX(away_team), MAX(home_team),
                  MAX(week) FROM nfl_games WHERE season=2026
                  AND kickoff_ts IS NOT NULL GROUP BY game_id ORDER BY 2"""
    ).fetchall()
    con.close()

    when = datetime.fromtimestamp(at_ts, ET)
    print("=" * 74)
    print(f"BRIEF 008 ACCEPTANCE   simulated {when:%A %Y-%m-%d %H:%M %Z}")
    print("=" * 74)
    fails = []

    # --- 1. the 1pm games read live -------------------------------------
    one_pm = [g for g in games
              if datetime.fromtimestamp(g[1], ET).strftime("%a %H:%M")
              == when.strftime("%a") + " 13:00"
              and abs(g[1] - at_ts) < 86400]
    print(f"\n1. the 1pm games read `live`   ({len(one_pm)} games)")
    for gid, kick, away, home, _wk in one_pm:
        t = _tier_at(kick, at_ts)
        mark = "ok " if t == "live" else "FAIL"
        print(f"   {mark} {away}@{home:<4} kicked {(at_ts-kick)/60:5.0f} min ago"
              f"  -> {t} ({CADENCE[t]():g}s)")
        if t != "live":
            fails.append(f"1pm {gid} read {t}, expected live")
    if not one_pm:
        fails.append("no 1pm games found to check")

    # --- 2. Monday night reads cold -------------------------------------
    mnf = [g for g in games
           if datetime.fromtimestamp(g[1], ET).weekday() == 0
           and 0 < g[1] - at_ts < 5 * 86400]
    print(f"\n2. Monday night reads `cold`   ({len(mnf)} games)")
    for gid, kick, away, home, _wk in mnf[:4]:
        t = _tier_at(kick, at_ts)
        mark = "ok " if t == "cold" else "FAIL"
        print(f"   {mark} {away}@{home:<4} kicks in {(kick-at_ts)/3600:5.1f}h"
              f"  -> {t} ({CADENCE[t]():g}s)")
        if t != "cold":
            fails.append(f"MNF {gid} read {t}, expected cold")
    if not mnf:
        fails.append("no Monday game found to check")

    # --- 3. hot-or-faster through T-90 and the final hour ---------------
    # The two windows that matter operationally: the inactive report lands at
    # T-90, and the final hour is where the sharp money arrives.
    print("\n3. every game hot-or-faster through T-90 and its final hour")
    probes = [("T-90 inactives", 90 * 60)] + [
        (f"T-{m}", m * 60) for m in (60, 30, 15, 5, 1)] + [("kickoff", 0)]
    bad = 0
    for gid, kick, away, home, _wk in games:
        for label, before in probes:
            t = _tier_at(kick, kick - before)
            if t not in FAST:
                bad += 1
                fails.append(f"{gid} at {label} read {t}, expected hot/live")
    for label, before in probes:
        t = _tier_at(at_ts + before, at_ts) if before else "live"
        print(f"   ok  {label:<16} -> {t} ({CADENCE[t]():g}s)")
    print(f"   checked {len(games)} games x {len(probes)} probes, "
          f"{bad} outside hot/live")

    # --- 4. oddsapi is in no tier line ----------------------------------
    print("\n4. oddsapi appears in no tier polling line")
    import run_logger
    src = open(run_logger.__file__, encoding="utf-8").read()
    polled_split = 'polled = [c for c in clients if c.name != "oddsapi"]' in src
    has_snapshot = "async def snapshot_worker(" in src
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    # Anchored to when the RUNNING process started, not to a flat window. A
    # fixed five minutes straddles the restart and reports rows the previous
    # build wrote, which reads as a failure of code that is no longer running.
    row = con.execute("SELECT watermark FROM source_health "
                      "WHERE source='logger_start'").fetchone()
    since = row[0] if row and row[0] else time.time() - 300
    tiered_rows = con.execute(
        """SELECT COUNT(*) FROM poll_log WHERE venue='oddsapi'
            AND endpoint LIKE 'quotes:%' AND ts > ?""", (since,)).fetchone()[0]
    con.close()
    for ok, label in ((polled_split, "oddsapi excluded from the polled workers"),
                      (has_snapshot, "snapshot_worker exists"),
                      (tiered_rows == 0,
                       f"no oddsapi quotes:<tier> rows since the running "
                       f"logger started (found {tiered_rows})")):
        print(f"   {'ok ' if ok else 'FAIL'} {label}")
        if not ok:
            fails.append(label)

    print("\n" + "=" * 74)
    if fails:
        print(f"FAILED ({len(fails)})")
        for f in fails[:12]:
            print(f"  - {f}")
    else:
        print("ALL CRITERIA PASS")
    print("=" * 74)
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--at", help="simulated moment, ET, 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--accept", action="store_true",
                    help="run the brief 008 acceptance criteria")
    args = ap.parse_args()
    if args.accept:
        raise SystemExit(1 if accept(args.at or "2026-09-13 13:15") else 0)
    report(parse_at(args.at))


if __name__ == "__main__":
    main()
