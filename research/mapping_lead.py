"""When was each Kalshi prop market mapped, against its own kickoff? (a-73)

    python -m research.mapping_lead                      # every 2026 week
    python -m research.mapping_lead --weeks 5 6
    python -m research.mapping_lead --json out.json

READ-ONLY. Opens the store `mode=ro`, in short sessions, and writes nothing.

The question a-68 raised and a-73 was asked to re-measure: a prop market that
is mapped AFTER its own game kicked off is worth nothing to the things that
lean on the mapping - depth capture, the Board, the kickoff tiers - and a count
of "mapped today" cannot see that, because by Tuesday every market is mapped.
So this counts, per week and per series:

    listed        Kalshi prop markets whose ticker names a game of that week
    unexamined    no `market_outcome` row at all: the mapper has not looked
    refused       a row and no outcome: the mapper looked and said why not
    mapped        a row with an outcome, at ANY time
    in_time       the outcome existed BEFORE the game's own kickoff
    depth         at least one `market_depth` row for the market
    depth_pre     at least one `market_depth` row timestamped before kickoff

TWO THINGS THIS RELIES ON, both stated because both can be wrong:

1. THE WEEK COMES FROM THE TICKER, not from the mapper. `KXNFLREC-26OCT08TBDAL-
   ...` carries the game's local date and the two team codes; that is matched
   to `nfl_games` by this module's own pattern. A market the mapper never
   looked at has no outcome and so no week by any route that goes through the
   mapping - which is the population this exists to count. Tickers that place
   in no game are reported (`unplaced`), never dropped.

2. "FIRST MAPPED" IS `outcomes.created_ts`, NOT `market_outcome.mapped_ts`.
   `mapped_ts` is rewritten by every full pass (`store.record_mappings`,
   ON CONFLICT ... mapped_ts=excluded.mapped_ts), so after any Tuesday it
   holds that Tuesday. `outcomes.created_ts` is written once and survives a
   re-upsert (the conflict clause touches `event_id` only; pinned by
   `tests/test_first_mapped.py`). It is the time the OUTCOME was first
   created, which equals the time the market was first mapped ONLY where the
   market's venue is the one that creates the outcome. That holds for Kalshi
   player props - Polymarket and the books link to player outcomes and never
   create them (CLAUDE.md, a-60) - and it does NOT hold for a Polymarket or
   book market, nor for a Kalshi game line, whose outcome another venue may
   have created first. So `in_time` is printed for Kalshi props and for
   nothing else. Where two Kalshi markets ever shared one outcome the later
   one would read as early; the script counts those (`shared_outcome`) rather
   than assume there are none.

`--lines` adds Kalshi's game lines (moneyline, spread, total) to the table for
their DEPTH columns only - a depth row is a direct observation and needs no
first-mapped time - so the hole in the depth series can be scoped: is it the
props, or everything. Their `in_time` column is printed as `-`.
"""
import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict

import config

TICKER = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d\d)(?P<mon>[A-Z]{3})(?P<dd>\d\d)"
                    r"(?P<blob>[A-Z]+)-")
MONTHS = {m: i + 1 for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}
# nflverse code -> the spellings Kalshi concatenates into a ticker. This
# module's own table: the placement must not go through the mapper on trial.
FORMS = {"LA": ("LA", "LAR"), "JAX": ("JAX", "JAC"), "WAS": ("WAS", "WSH"),
         "LV": ("LV", "LVR")}
SERIES = ("KXNFLREC", "KXNFLRSHATT")


def utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%m-%d %H:%M") if ts else "-"


def connect(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)


def games(con, season):
    """{(gameday, blob): (game_id, week, kickoff_ts)} for every spelling of the
    two codes, away-then-home, which is the order Kalshi writes."""
    out = {}
    for gid, week, day, home, away, kick in con.execute(
            "SELECT game_id, week, gameday, home_team, away_team, MAX(kickoff_ts) "
            "FROM nfl_games WHERE sport='nfl' AND season=? AND kickoff_ts IS NOT NULL "
            "GROUP BY game_id", (season,)):
        for a in FORMS.get(away, (away,)):
            for h in FORMS.get(home, (home,)):
                out[(day, a + h)] = (gid, week, kick)
    return out


def place(ticker, sched):
    """(series, game_id, week, kickoff_ts) or None. The ticker's date is the
    game's local day, which is `gameday`."""
    m = TICKER.match(ticker or "")
    if not m or m.group("mon") not in MONTHS:
        return None
    day = "20%s-%02d-%s" % (m.group("yy"), MONTHS[m.group("mon")], m.group("dd"))
    hit = sched.get((day, m.group("blob")))
    return (m.group("series"), *hit) if hit else None


LINE_TYPES = ("moneyline", "spread", "total")


def measure(path, season, depth=True, lines=False):
    con = connect(path)
    try:
        sched = games(con, season)
        rows = con.execute(
            """SELECT m.market_id, m.first_seen, mo.market_id IS NOT NULL,
                      mo.outcome_id, mo.unmapped_reason, o.created_ts
                 FROM markets m
                 LEFT JOIN market_outcome mo
                   ON mo.venue = m.venue AND mo.market_id = m.market_id
                 LEFT JOIN outcomes o ON o.outcome_id = mo.outcome_id
                WHERE m.venue = 'kalshi' AND m.market_type = 'prop'""").fetchall()
        rows = [(*r, "prop") for r in rows]
        if lines:
            rows += [(*r, t) for *r, t in con.execute(
                """SELECT m.market_id, m.first_seen, mo.market_id IS NOT NULL,
                          mo.outcome_id, mo.unmapped_reason, NULL, m.market_type
                     FROM markets m
                     LEFT JOIN market_outcome mo
                       ON mo.venue = m.venue AND mo.market_id = m.market_id
                    WHERE m.venue = 'kalshi' AND m.market_type IN (?,?,?)""", LINE_TYPES)]
    finally:
        con.close()
    shared = Counter(r[3] for r in rows if r[3] and r[6] == "prop")
    per = defaultdict(Counter)
    listed_hours = defaultdict(Counter)
    unplaced = []
    placed = []
    for mid, seen, has_row, oid, reason, created, mtype in rows:
        p = place(mid, sched)
        if p is None:
            unplaced.append(mid)
            continue
        series, gid, week, kick = p
        key = (week, mtype if mtype != "prop" else series if series in SERIES else "other")
        c = per[key]
        c["listed"] += 1
        if seen is not None and seen < kick:
            c["listed_pre"] += 1
        listed_hours[week][utc(seen)[:8]] += 1
        if not has_row:
            c["unexamined"] += 1
        elif oid is None:
            c["refused"] += 1
        else:
            c["mapped"] += 1
            if created is not None and created < kick:
                c["in_time"] += 1
            if mtype == "prop" and shared[oid] > 1:
                c["shared_outcome"] += 1
        placed.append((mid, key, kick))
    if depth:
        for i in range(0, len(placed), 500):          # short read sessions
            con = connect(path)
            try:
                for mid, key, kick in placed[i:i + 500]:
                    r = con.execute("SELECT MIN(ts) FROM market_depth "
                                    "WHERE venue='kalshi' AND market_id=?", (mid,)).fetchone()
                    if r[0] is not None:
                        per[key]["depth"] += 1
                        if r[0] < kick:
                            per[key]["depth_pre"] += 1
            finally:
                con.close()
    return {"rows": len(rows), "unplaced": unplaced, "per": per,
            "listed_hours": listed_hours, "lines": lines}


COLS = ("listed", "listed_pre", "unexamined", "refused", "mapped", "in_time",
        "depth", "depth_pre")


def report(res, weeks=None, out=sys.stdout):
    per = res["per"]
    if res["rows"] == 0:
        raise SystemExit("no Kalshi prop market read: wrong store, or an empty one")
    print("Kalshi %s markets read: %d; placed in a game: %d; unplaced: %d"
          % ("prop and game-line" if res["lines"] else "prop", res["rows"],
             sum(c["listed"] for c in per.values()), len(res["unplaced"])), file=out)
    if res["unplaced"]:
        heads = Counter(t.split("-")[0] for t in res["unplaced"])
        print("  unplaced by series: %s" % dict(heads.most_common(8)), file=out)
        print("  first: %s" % res["unplaced"][:3], file=out)
    print("\n%-4s %-12s" % ("week", "series") + "".join("%11s" % c for c in COLS)
          + "%9s%9s" % ("in_time%", "depth%"), file=out)
    for week in sorted(weeks or {w for w, _ in per}):
        if not any(w == week for w, _ in per):
            # said out loud: a week with nothing listed prints a line, not nothing
            print("%-4d NO Kalshi prop market is listed for this week yet" % week, file=out)
            continue
        tot = Counter()
        for series in (*SERIES, "other"):
            c = per.get((week, series))
            if not c:
                continue
            tot.update(c)
            _line(week, series, c, out)
        _line(week, "ALL PROPS", tot, out)
        for t in LINE_TYPES:
            if per.get((week, t)):
                _line(week, t, per[(week, t)], out, in_time=False)
        hrs = res["listed_hours"][week]
        top = ", ".join("%s:00Z %d" % kv for kv in sorted(hrs.items(), key=lambda kv: -kv[1])[:4])
        print("     listed by UTC hour, largest first: %s" % top, file=out)
    sh = sum(c["shared_outcome"] for c in per.values())
    print("\nKalshi prop markets sharing an outcome with another Kalshi prop market: %d "
          "(0 is what makes created_ts a per-market first-mapped time)" % sh, file=out)


def _line(week, series, c, out, in_time=True):
    n = c["listed"]
    print("%-4d %-12s" % (week, series)
          + "".join("%11s" % (c[k] if in_time or k != "in_time" else "-") for k in COLS)
          + "%9s%9s" % ("%.3f" % (c["in_time"] / n) if n and in_time else "-",
                        "%.3f" % (c["depth"] / n) if n else "-"), file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--weeks", type=int, nargs="*")
    ap.add_argument("--no-depth", action="store_true", help="skip the market_depth lookups")
    ap.add_argument("--lines", action="store_true",
                    help="add Kalshi game lines, for their depth columns only")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    res = measure(args.db, args.season, depth=not args.no_depth, lines=args.lines)
    report(res, set(args.weeks) if args.weeks else None)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"rows": res["rows"], "unplaced": len(res["unplaced"]),
                       "per": {"%d:%s" % k: dict(v) for k, v in sorted(res["per"].items())}},
                      f, indent=1)


if __name__ == "__main__":
    main()
