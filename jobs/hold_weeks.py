"""Hold one NFL week's quote history out of the prune.

    python -m jobs.hold_weeks --week 2 --week 3 --dry-run
    python -m jobs.hold_weeks --week 2 --week 3 --until 2027-03-01 \\
        --reason "a-60: ..."

Retention prunes live quotes 14 days after INGESTION (invariant 8). The
export's own holds follow what the site shows and lapse when a week leaves the
page, so a week that research still needs - the only live-tier in-game week on
disk, a cross-venue timing study - is deleted on schedule unless something
holds it. This is that something: it selects the week's markets READ-ONLY and
writes the holds through `store.hold_quotes`, which extends and never shortens.

Selection, per venue:
  kalshi      the series named in KALSHI_SERIES whose ticker carries one of the
              week's game dates (`KXNFLREC-26SEP24...`). The date is read from
              nfl_games, never typed.
  polymarket  NFL markets on a GAME slug (`nfl-sf-la-2026-09-11[-...]`) whose
              slug date is one of the week's game dates or the day after one.
              The slug carries the kickoff's UTC date, so a Thursday 20:15 ET
              game is slugged Friday; NFL game dates within a week (Thu/Sun/Mon)
              and across weeks are >= 2 days apart, so date-or-day-after is
              unambiguous. Season-long and weekly-leader slugs are not game
              markets and are not held.
"""
import argparse
import datetime as dt
import re
import sqlite3
import time
from collections import Counter

import config
import store

KALSHI_SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


GAME_SLUG = re.compile(r"^nfl-[a-z0-9]+-[a-z0-9]+-(\d{4}-\d{2}-\d{2})(?:-.*)?$")


def day_after(gameday: str) -> str:
    return (dt.date.fromisoformat(gameday) + dt.timedelta(days=1)).isoformat()


def _ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=30)


def ticker_date(gameday: str) -> str:
    """'2026-09-24' -> '26SEP24', the date segment of a Kalshi NFL ticker."""
    y, m, d = gameday.split("-")
    return f"{y[2:]}{MONTHS[int(m) - 1]}{d}"


def week_games(c, season: int, week: int):
    rows = c.execute(
        "SELECT DISTINCT gameday, kickoff_ts FROM nfl_games "
        "WHERE season = ? AND week = ? AND game_type = 'REG'", (season, week)).fetchall()
    if not rows:
        raise SystemExit(f"no nfl_games rows for {season} week {week} - refusing to "
                         "hold an empty selection as if it were a week")
    return sorted({r[0] for r in rows}), [r[1] for r in rows if r[1]]


def select(c, season: int, week: int, series=KALSHI_SERIES, polymarket=True):
    """{(venue, series_or_type): [market_id, ...]} for one week."""
    days, kicks = week_games(c, season, week)
    out = {}
    for s in series:
        ids = []
        for d in days:
            ids += [r[0] for r in c.execute(
                "SELECT market_id FROM markets WHERE venue = 'kalshi' AND market_id LIKE ?",
                (f"{s}-{ticker_date(d)}%",))]
        out[("kalshi", s)] = sorted(set(ids))
    if polymarket:
        slug_days = set(days) | {day_after(d) for d in days}
        for mid, mtype, eid in c.execute(
                "SELECT market_id, market_type, event_id FROM markets "
                "WHERE venue = 'polymarket' AND sport = 'nfl'"):
            m = GAME_SLUG.match((eid or "").lower())
            if m and m.group(1) in slug_days:
                out.setdefault(("polymarket", mtype or "unknown"), []).append(mid)
    return out


def census(c, venue, ids, now):
    """Held-now count and quote rows for a list of market ids."""
    held = rows = 0
    for m in ids:
        h = c.execute("SELECT until_ts FROM quote_retention_hold WHERE venue = ? "
                      "AND market_id = ?", (venue, m)).fetchone()
        held += bool(h and h[0] > now)
        rows += c.execute("SELECT COUNT(*) FROM quotes WHERE venue = ? AND market_id = ?",
                          (venue, m)).fetchone()[0]
    return held, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, action="append", required=True)
    ap.add_argument("--until", default="2027-03-01",
                    help="UTC date the hold ends (a hold always ends)")
    ap.add_argument("--reason", default=None)
    ap.add_argument("--no-polymarket", action="store_true")
    ap.add_argument("--census", action="store_true",
                    help="also count held markets and quote rows (slow: one query per market)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    until = dt.datetime.strptime(a.until, "%Y-%m-%d").replace(
        tzinfo=dt.timezone.utc).timestamp()
    now = time.time()
    c = _ro()
    plan = {}
    try:
        for w in a.week:
            for key, ids in select(c, a.season, w, polymarket=not a.no_polymarket).items():
                plan[(w,) + key] = ids
        total = Counter()
        for (w, venue, kind), ids in sorted(plan.items()):
            line = f"week {w}  {venue:10s} {kind:14s} markets {len(ids):6d}"
            if a.census:
                held, rows = census(c, venue, ids, now)
                line += f"  held-now {held:6d}  quote rows {rows:9d}"
            print(line)
            total[venue] += len(ids)
    finally:
        c.close()
    if not sum(total.values()):
        raise SystemExit("selection is empty - nothing held")
    print("total", dict(total))
    if a.dry_run:
        print(f"dry run: would hold {sum(total.values())} markets until {a.until}")
        return
    reason = a.reason or (f"a-60: {a.season} weeks {','.join(map(str, a.week))} kept for "
                          "research past the site's window")
    pairs = [(venue, m) for (w, venue, kind), ids in plan.items() for m in ids]
    n = store.hold_quotes(pairs, until, reason)
    print(f"held {n} markets until {a.until}Z: {reason}")


if __name__ == "__main__":
    main()
