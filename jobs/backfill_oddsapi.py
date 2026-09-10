"""Odds API historical props - PILOT ONLY.

    python -m jobs.backfill_oddsapi --season 2024 --week 8 --dry-run
    python -m jobs.backfill_oddsapi --season 2024 --week 8
    python -m jobs.backfill_oddsapi --verify

THIS SPENDS REAL CREDITS. Measured 2026-09-10, not estimated:

    historical events listing   1 credit per snapshot timestamp
    historical event odds      50 credits per event
                               (= 5 markets x 1 region x 10 for historical)

The events listing is NOT free and was not counted in the original spec. One
week of one season is 16 events = 800 credits, not the ~500 the spec assumed.
That difference matters at three-season scale: see --verify for the
extrapolation.

Every response is archived verbatim before parsing (invariant #2), so a
normalizer fix is a re-parse and never a second paid pull. That is worth more
here than anywhere else in the system - these bytes cost money.
"""
import argparse
import datetime as dt
import sqlite3
import time

import httpx

import config
import store
from core.outcomes import MarketType, Outcome, Side, Sport, Stat, player_prop
from venues.mapping import Unresolved, resolve_player

SOURCE = "oddsapi_historical"

# Odds API market key -> canonical stat. Keyed structurally: a renamed display
# label must never be able to move a market onto a different stat.
MARKET_STAT = {
    "player_receptions": Stat.RECEPTIONS,
    "player_receptions_alternate": Stat.RECEPTIONS,
    "player_rush_attempts": Stat.RUSH_ATTEMPTS,
    "player_reception_yds": Stat.RECEIVING_YARDS,
    "player_tackles_assists": Stat.TACKLES_ASSISTS,
    "player_sacks": Stat.SACKS,
}
PILOT_MARKETS = ("player_receptions", "player_rush_attempts",
                 "player_reception_yds", "player_receptions_alternate",
                 "player_tackles_assists")

SIDE = {"over": Side.OVER, "under": Side.UNDER,
        "yes": Side.YES, "no": Side.NO}


def american_to_prob(odds):
    """Implied probability, vig included. De-vig in analysis, never at ingest."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


class Spend:
    """Tracks real credit usage from the response headers."""

    def __init__(self):
        self.calls = 0
        self.credits = 0
        self.start = None
        self.remaining = None

    def note(self, r):
        self.calls += 1
        last = r.headers.get("x-requests-last")
        rem = r.headers.get("x-requests-remaining")
        if last is not None:
            self.credits += int(last)
        if rem is not None:
            self.remaining = int(rem)
            if self.start is None:
                self.start = int(rem) + int(last or 0)


def snapshots_for(con, season, week, lead_seconds=600):
    """(iso_timestamp, [event kickoffs]) per distinct kickoff slot.

    Featured and prop markets cluster into a handful of kickoff slots per week,
    so one snapshot per slot covers every game in it and the listing is billed
    once rather than once per game.
    """
    rows = con.execute(
        """SELECT game_id, MAX(kickoff_ts), MAX(away_team), MAX(home_team)
             FROM nfl_games WHERE season=? AND week=? AND game_type='REG'
            GROUP BY game_id ORDER BY 2""", (season, week)).fetchall()
    slots = {}
    for gid, kick, away, home in rows:
        slots.setdefault(kick, []).append((gid, away, home))
    return [(dt.datetime.fromtimestamp(k - lead_seconds, dt.timezone.utc)
             .strftime("%Y-%m-%dT%H:%M:%SZ"), k, games)
            for k, games in sorted(slots.items())]


def list_events(client, spend, iso_ts):
    r = client.get(f"{config.ODDS_BASE}/historical/sports/{config.ODDS_SPORT}/events",
                   params={"apiKey": config.ODDS_API_KEY, "date": iso_ts})
    spend.note(r)
    if r.status_code != 200:
        return [], r.status_code
    body = r.json()
    store.archive_raw("oddsapi_historical", "events", body)
    return body.get("data") or [], 200


def event_odds(client, spend, event_id, iso_ts, markets):
    r = client.get(
        f"{config.ODDS_BASE}/historical/sports/{config.ODDS_SPORT}"
        f"/events/{event_id}/odds",
        params={"apiKey": config.ODDS_API_KEY, "regions": config.ODDS_REGIONS,
                "markets": ",".join(markets), "oddsFormat": "american",
                "date": iso_ts})
    spend.note(r)
    if r.status_code != 200:
        return None, r.status_code
    body = r.json()
    store.archive_raw("oddsapi_historical", f"event_odds/{event_id}", body)
    return body, 200


def normalize(body, season, week, game_id, stats, teams=None):
    """Snapshot -> (quote rows, resolution failures).

    Player identity resolves HERE, at ingest, exactly as brief 003 requires.
    A name that does not resolve is recorded as a failure and its row is
    dropped - never guessed onto the nearest match.
    """
    data = body.get("data") or {}
    snap_ts = body.get("timestamp")
    ts = dt.datetime.strptime(snap_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc).timestamp() if snap_ts else time.time()

    rows, failures, seen_names = [], [], {}
    for bk in data.get("bookmakers") or []:
        book = bk.get("key")
        for mkt in bk.get("markets") or []:
            stat = MARKET_STAT.get(mkt.get("key"))
            if stat is None:
                continue
            stats[mkt["key"]] = stats.get(mkt["key"], 0) + 1
            for oc in mkt.get("outcomes") or []:
                name = oc.get("description")
                side = SIDE.get(str(oc.get("name", "")).strip().lower())
                line = oc.get("point")
                if not name or side is None or line is None:
                    continue
                if name not in seen_names:
                    try:
                        gsis, method, conf = resolve_player(name, season,
                                                            teams=teams)
                        seen_names[name] = (gsis, method, conf)
                    except Unresolved as e:
                        seen_names[name] = None
                        failures.append((name, str(e)[:90]))
                got = seen_names[name]
                if got is None:
                    continue
                gsis, method, conf = got
                o = player_prop(season, week, gsis, stat, float(line), side,
                                event_id=game_id)
                outcome_id = store.upsert_outcome(o, game_id)
                prob = american_to_prob(oc.get("price"))
                rows.append({
                    "ts": ts, "sport": "nfl", "venue": f"oddsapi:{book}",
                    "event_id": game_id, "market_id":
                        f"{game_id}|{mkt['key']}|{name}|{oc.get('name')}|{line}",
                    "market_type": "prop", "subject": gsis, "line": float(line),
                    "side": side.value,
                    # A sportsbook quotes one price per side, not a book. The
                    # implied probability is the price; there is no bid/ask to
                    # record and inventing one would be worse than a null.
                    "best_bid": None, "best_ask": None, "mid": prob,
                    "last": float(oc["price"]) if oc.get("price") is not None else None,
                    "volume": None, "open_interest": None,
                    "raw_ref": "oddsapi_historical/event_odds",
                    "source": SOURCE,
                    "_outcome_id": outcome_id, "_book": book,
                })
    return rows, failures


def run(season=2024, week=8, markets=PILOT_MARKETS, dry_run=False, limit=None):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    slots = snapshots_for(con, season, week)
    con.close()

    spend = Spend()
    stats = {}
    all_rows, all_failures, per_event = [], [], []
    est = sum(len(g) for _t, _k, g in slots) * (10 * len(markets)) + len(slots)
    print(f"{sum(len(g) for _t,_k,g in slots)} events in {len(slots)} kickoff "
          f"slots; estimated {est} credits "
          f"({len(slots)} listings + {len(markets)}x10 per event)")
    if dry_run:
        for iso, _k, games in slots:
            print(f"  {iso}  {len(games)} games: "
                  f"{', '.join(a + '@' + h for _g, a, h in games)}")
        return {"dry_run": True, "estimated_credits": est}

    with httpx.Client(timeout=90, follow_redirects=True) as client:
        for iso, _kick, games in slots:
            events, code = list_events(client, spend, iso)
            if code != 200:
                print(f"  {iso}: events listing HTTP {code}")
                continue
            by_pair = {(e["away_team"], e["home_team"]): e["id"] for e in events}
            for game_id, away, home in games:
                if limit and len(per_event) >= limit:
                    break
                eid = _match_event(by_pair, away, home)
                if not eid:
                    print(f"    {game_id}: no historical event id matched")
                    continue
                body, code = event_odds(client, spend, eid, iso, markets)
                if code != 200 or body is None:
                    print(f"    {game_id}: odds HTTP {code}")
                    continue
                rows, failures = normalize(body, season, week, game_id, stats,
                                           teams=(away, home))
                all_rows.extend(rows)
                all_failures.extend(failures)
                per_event.append((game_id, len(rows), len(failures)))
                print(f"    {game_id:<20} {len(rows):>5} rows  "
                      f"{len(failures):>2} unresolved  "
                      f"(spent {spend.credits}, {spend.remaining} left)")

    written = store.write_quotes(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in all_rows],
        dedupe=False)
    for r in all_rows:
        store.record_mapping(r["venue"], r["market_id"], r["_outcome_id"],
                             "oddsapi_historical", 1.0)
    store.record_health("backfill_oddsapi", True,
                        f"{written} rows, {spend.credits} credits, "
                        f"{len(all_failures)} unresolved names",
                        watermark=time.time())
    return {"events": len(per_event), "rows": written,
            "credits": spend.credits, "calls": spend.calls,
            "remaining": spend.remaining, "failures": all_failures,
            "markets": stats, "per_event": per_event}


def rederive(season=2024, week=8):
    """Re-normalize the pilot from the ARCHIVE. Costs nothing.

    This is invariant #2 collecting on its promise where it matters most: the
    bytes cost 806 credits, and a name-normalization fix is a re-parse rather
    than a second paid pull.
    """
    import glob
    import gzip
    import json
    import os

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    teams_by_game = {g: (a, h) for g, a, h in con.execute(
        """SELECT game_id, MAX(away_team), MAX(home_team) FROM nfl_games
            WHERE season=? AND week=? GROUP BY game_id""", (season, week))}
    con.close()

    stats, rows, failures, seen = {}, [], [], 0
    for f in sorted(glob.glob(os.path.join(
            config.RAW_DIR, "oddsapi_historical", "*", "*.jsonl.gz"))):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            rec = json.loads(line)
            if not rec["endpoint"].startswith("event_odds"):
                continue
            data = (rec["payload"].get("data") or {})
            away = None
            for gid, (a, h) in teams_by_game.items():
                from venues.mapping import team_abbr
                if (team_abbr(data.get("away_team")) == a
                        and team_abbr(data.get("home_team")) == h):
                    away, game_id, teams = a, gid, (a, h)
                    break
            if away is None:
                continue
            seen += 1
            r, fails = normalize(rec["payload"], season, week, game_id, stats,
                                 teams=teams)
            rows.extend(r)
            failures.extend(fails)

    # A re-parse REPLACES its own prior derivation rather than appending to it.
    # The archive is the source of truth and the parse is deterministic, so two
    # runs must not leave two copies - that is not append-only history, it is
    # double-counting, and it would inflate every backtest that reads this
    # source. Only rows from this source and these games are removed.
    games = tuple(teams_by_game)
    if games:
        with store.db() as c:
            c.execute(
                f"DELETE FROM quotes WHERE source = ? AND event_id IN "
                f"({','.join('?' * len(games))})", (SOURCE, *games))
    written = store.write_quotes(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        dedupe=False)
    for r in rows:
        store.record_mapping(r["venue"], r["market_id"], r["_outcome_id"],
                             "oddsapi_historical", 1.0)
    uniq = {n: w for n, w in failures}
    return {"snapshots": seen, "rows": written, "credits": 0,
            "failures": uniq, "markets": stats}


def _match_event(by_pair, away, home):
    """Odds API uses full team names; nfl_games uses abbreviations."""
    from venues.mapping import team_abbr
    for (a, h), eid in by_pair.items():
        if team_abbr(a) == away and team_abbr(h) == home:
            return eid
    return None


def verify(season=2024, week=8):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print("=== what landed ===")
    for venue, n, mk, lo, hi in con.execute(
            """SELECT venue, COUNT(*), COUNT(DISTINCT market_id), MIN(ts), MAX(ts)
                 FROM quotes WHERE source=? GROUP BY venue ORDER BY 2 DESC""",
            (SOURCE,)):
        print(f"  {venue:<24} {n:>6,} rows  {mk:>6,} markets")
    tot = con.execute("SELECT COUNT(*), COUNT(DISTINCT market_id) FROM quotes "
                      "WHERE source=?", (SOURCE,)).fetchone()
    print(f"  {'TOTAL':<24} {tot[0]:>6,} rows  {tot[1]:>6,} markets")

    print("\n=== outcomes created ===")
    for stat, n, lines in con.execute(
            """SELECT o.stat, COUNT(DISTINCT o.outcome_id),
                      COUNT(DISTINCT o.line) FROM outcomes o
                WHERE o.season=? AND o.week=? GROUP BY o.stat ORDER BY 2 DESC""",
            (season, week)):
        print(f"  {stat:<20} {n:>5} outcomes over {lines:>3} distinct lines")

    print("\n=== 3-season extrapolation, from MEASURED cost ===")
    games = con.execute(
        """SELECT COUNT(*) FROM (SELECT game_id FROM nfl_games
            WHERE season IN (2023,2024,2025) GROUP BY game_id)""").fetchone()[0]
    slots = con.execute(
        """SELECT COUNT(*) FROM (SELECT season, week, kickoff_ts FROM nfl_games
            WHERE season IN (2023,2024,2025)
            GROUP BY season, week, kickoff_ts)""").fetchone()[0]
    per_event = 50
    print(f"  {games:,} games, {slots:,} distinct kickoff slots (2023-2025)")
    print(f"  events : {games:,} x {per_event} = {games*per_event:,} credits")
    print(f"  listings: {slots:,} x 1      = {slots:,} credits")
    print(f"  TOTAL   : {games*per_event + slots:,} credits for 5 prop markets")
    print(f"  (spec estimated ~51k including featured; the listing line was "
          f"uncounted)")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--week", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--from-archive", action="store_true",
                    help="re-normalize the archived payloads; costs 0 credits")
    args = ap.parse_args()

    store.init_db()
    if args.verify:
        verify(args.season, args.week)
        return
    if args.from_archive:
        s = rederive(args.season, args.week)
        print(f"re-derived {s['snapshots']} archived snapshots -> "
              f"{s['rows']:,} rows, CREDITS SPENT=0")
        print(f"markets: {s['markets']}")
        print(f"unresolved names: {len(s['failures'])}")
        for n, w in sorted(s["failures"].items()):
            print(f"  {n:<32} {w}")
        return
    s = run(args.season, args.week, dry_run=args.dry_run, limit=args.limit)
    if s.get("dry_run"):
        return
    print(f"\nevents={s['events']} rows={s['rows']:,} calls={s['calls']} "
          f"CREDITS SPENT={s['credits']} remaining={s['remaining']}")
    print(f"markets seen: {s['markets']}")
    if s["failures"]:
        uniq = {}
        for name, why in s["failures"]:
            uniq[name] = why
        print(f"\nunresolved names ({len(uniq)} distinct):")
        for name, why in sorted(uniq.items()):
            print(f"  {name:<32} {why}")


if __name__ == "__main__":
    main()
