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
from venues.mapping import Unresolved, parse_book_name, resolve_player

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


# The de-vig benchmark. bovada and betonlineag are kept in the data but excluded
# from the consensus: they are the widest of the seven and would drag a median
# that is meant to represent where the sharp money sits. Pinnacle is absent from
# historical us-region props entirely (brief 009), which is why a median of
# three is the benchmark rather than one book.
BENCHMARK_BOOKS = ("draftkings", "fanduel", "betmgm")

# Two sides of one line must sum to slightly more than 1. The excess is the vig.
OVERROUND_MIN, OVERROUND_MAX = 1.00, 1.15


class OverroundViolation(ValueError):
    """A two-sided pair whose implied probabilities do not sum to a sane vig.

    Near 1.0 means the feed is already de-vigged and de-vigging again would be
    a second, silent adjustment. Far from 1.0 means the two rows are not
    actually opposite sides of the same claim - a mismatch that produces
    plausible numbers and wrong ones.
    """


def devig_pair(p_over, p_under, label=""):
    """Multiplicative de-vig. Returns (over, under, overround)."""
    if p_over is None or p_under is None:
        return None, None, None
    total = p_over + p_under
    if not (OVERROUND_MIN < total < OVERROUND_MAX):
        raise OverroundViolation(
            f"{label}: sides sum to {total:.4f}, outside "
            f"({OVERROUND_MIN}, {OVERROUND_MAX}) - "
            + ("already de-vigged?" if total <= OVERROUND_MIN
               else "sides mismatched?"))
    return p_over / total, p_under / total, total


class Spend:
    """Tracks real credit usage from the response headers."""

    def __init__(self):
        self.calls = 0
        self.credits = 0
        self.start = None
        self.remaining = None

    def __init__(self, reserve=None):
        self.calls = 0
        self.credits = 0
        self.start = None
        self.remaining = None
        self.last_cost = 0
        self.reserve = reserve

    def note(self, r):
        self.calls += 1
        last = r.headers.get("x-requests-last")
        rem = r.headers.get("x-requests-remaining")
        self.last_cost = int(last) if last is not None else 0
        if last is not None:
            self.credits += int(last)
        if rem is not None:
            self.remaining = int(rem)
            if self.start is None:
                self.start = int(rem) + int(last or 0)
        # ABORT, not warn. A budget guard that logs and continues is not a
        # guard; by the time anyone reads the warning the credits are gone.
        if self.reserve is not None and self.remaining is not None:
            if self.remaining <= self.reserve:
                raise ReserveExhausted(
                    f"x-requests-remaining={self.remaining} at or below reserve "
                    f"{self.reserve}; stopping. {self.credits} credits spent "
                    f"this run over {self.calls} calls.")


class ReserveExhausted(RuntimeError):
    """The credit floor was reached. Stop immediately; resume later."""


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




# =====================================================================
# FULL BACKFILL (brief 010) - resumable, reserve-guarded, de-vigged
# =====================================================================

FULL_MARKETS = ("player_receptions", "player_rush_attempts",
                "player_reception_yds", "player_tackles_assists",
                "player_sacks")
FEATURED_MARKETS = ("h2h", "spreads", "totals")
# player_receptions_alternate is DROPPED: FanDuel-only in 2024, and Kalshi
# lists a native threshold ladder free on the venue we would actually trade.
# Paying 8,550 credits to duplicate it would be buying the same information
# twice, once at a worse price.


def _checkpoint(kind, key, season, credits, rows, status, detail=None):
    with store.db() as c:
        c.execute(
            """INSERT INTO oddsapi_progress
                 (kind, key, season, credits, rows, status, detail, done_ts)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(kind, key) DO UPDATE SET
                 credits=excluded.credits, rows=excluded.rows,
                 status=excluded.status, detail=excluded.detail,
                 done_ts=excluded.done_ts""",
            (kind, key, season, credits, rows, status, detail, time.time()))


def _done(kind):
    with store.db() as c:
        return {r[0] for r in c.execute(
            "SELECT key FROM oddsapi_progress WHERE kind=? AND status IN "
            "('done','empty')", (kind,))}


def _pair_key(mkey, oc):
    """What makes two outcomes opposite sides of ONE claim."""
    if mkey in ("h2h",):
        return ("h2h",)
    if mkey in ("spreads",):
        return ("spreads", abs(float(oc.get("point") or 0)))
    if mkey in ("totals",):
        return ("totals", oc.get("point"))
    return (mkey, oc.get("description"), oc.get("point"))


def normalize_props(body, season, week, game_id, teams, stats, violations):
    """Props -> rows, with the vig removed across each two-sided pair."""
    data = body.get("data") or {}
    snap = body.get("timestamp")
    ts = dt.datetime.strptime(snap, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc).timestamp() if snap else time.time()

    rows, failures, seen = [], [], {}
    devig_by_outcome = {}
    for bk in data.get("bookmakers") or []:
        book = bk.get("key")
        for mkt in bk.get("markets") or []:
            stat = MARKET_STAT.get(mkt.get("key"))
            if stat is None:
                continue
            stats[mkt["key"]] = stats.get(mkt["key"], 0) + 1
            pairs = {}
            for oc in mkt.get("outcomes") or []:
                pairs.setdefault(_pair_key(mkt["key"], oc), []).append(oc)

            for pk, ocs in pairs.items():
                probs = {str(o.get("name", "")).strip().lower():
                         american_to_prob(o.get("price")) for o in ocs}
                dv = {}
                if len(ocs) == 2 and "over" in probs and "under" in probs:
                    label = (f"{book} {mkt['key']} "
                             f"{ocs[0].get('description')} {ocs[0].get('point')}")
                    try:
                        o_dv, u_dv, over = devig_pair(probs["over"],
                                                      probs["under"], label)
                        dv = {"over": o_dv, "under": u_dv}
                    except OverroundViolation as e:
                        violations.append(str(e))
                for oc in ocs:
                    name = oc.get("description")
                    side = SIDE.get(str(oc.get("name", "")).strip().lower())
                    line = oc.get("point")
                    if not name or side is None or line is None:
                        continue
                    if name not in seen:
                        # The book may already have told us the team or the
                        # position. Use it - it is the strongest fact on offer.
                        clean, team_hint, pos_hint = parse_book_name(name)
                        hint = (team_hint,) if team_hint else teams
                        try:
                            seen[name] = resolve_player(clean, season,
                                                        position=pos_hint,
                                                        teams=hint)
                        except Unresolved as e:
                            seen[name] = None
                            failures.append((name, str(e)[:90]))
                    got = seen[name]
                    if got is None:
                        continue
                    gsis = got[0]
                    o = player_prop(season, week, gsis, stat, float(line),
                                    side, event_id=game_id)
                    oid = store.upsert_outcome(o, game_id)
                    prob = american_to_prob(oc.get("price"))
                    pdv = dv.get(side.value)
                    if pdv is not None and book in BENCHMARK_BOOKS:
                        devig_by_outcome.setdefault(oid, []).append((book, pdv))
                    rows.append(_row(ts, book, game_id, mkt["key"], name, oc,
                                     gsis, float(line), side, prob, pdv, oid))
    _write_benchmarks(devig_by_outcome, ts)
    return rows, failures


def normalize_featured(body, season, week, games_by_pair, stats, violations):
    """h2h / spreads / totals -> team and game outcomes."""
    from venues.mapping import team_abbr
    data = body.get("data") or []
    snap = body.get("timestamp")
    ts = dt.datetime.strptime(snap, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc).timestamp() if snap else time.time()

    rows = []
    devig_by_outcome = {}
    for ev in data:
        away, home = team_abbr(ev.get("away_team")), team_abbr(ev.get("home_team"))
        game_id = games_by_pair.get((away, home))
        if not game_id:
            continue
        for bk in ev.get("bookmakers") or []:
            book = bk.get("key")
            for mkt in bk.get("markets") or []:
                mkey = mkt.get("key")
                if mkey not in FEATURED_MARKETS:
                    continue
                stats[mkey] = stats.get(mkey, 0) + 1
                ocs = mkt.get("outcomes") or []
                probs = [american_to_prob(o.get("price")) for o in ocs]
                dv = {}
                if len(ocs) == 2 and all(p is not None for p in probs):
                    label = f"{book} {mkey} {game_id}"
                    try:
                        a, b, _ = devig_pair(probs[0], probs[1], label)
                        dv = {ocs[0].get("name"): a, ocs[1].get("name"): b}
                    except OverroundViolation as e:
                        violations.append(str(e))
                for oc in ocs:
                    price = american_to_prob(oc.get("price"))
                    point = oc.get("point")
                    nm = oc.get("name")
                    if mkey == "totals":
                        side = SIDE.get(str(nm).strip().lower())
                        if side is None or point is None:
                            continue
                        subject, mt = game_id, MarketType.TOTAL
                    else:
                        team = team_abbr(nm)
                        if not team:
                            continue
                        side = Side.OVER if mkey == "spreads" else Side.YES
                        subject, mt = team, (MarketType.SPREAD if mkey == "spreads"
                                             else MarketType.MONEYLINE)
                    o = Outcome(Sport.NFL, season, week, mt, subject, None,
                                float(point) if point is not None else None,
                                side, game_id)
                    oid = store.upsert_outcome(o, game_id)
                    pdv = dv.get(nm)
                    if pdv is not None and book in BENCHMARK_BOOKS:
                        devig_by_outcome.setdefault(oid, []).append((book, pdv))
                    rows.append({
                        "ts": ts, "sport": "nfl", "venue": f"oddsapi:{book}",
                        "event_id": game_id,
                        "market_id": f"{game_id}|{mkey}|{nm}|{point}",
                        "market_type": mkey, "subject": subject,
                        "line": float(point) if point is not None else None,
                        "side": side.value, "best_bid": None, "best_ask": None,
                        "mid": price,
                        "last": float(oc["price"]) if oc.get("price") is not None else None,
                        "volume": None, "open_interest": None,
                        "raw_ref": "oddsapi_historical/featured",
                        "source": SOURCE, "prob_devig": pdv,
                        "_outcome_id": oid,
                    })
    _write_benchmarks(devig_by_outcome, ts)
    return rows


def _row(ts, book, game_id, mkey, name, oc, gsis, line, side, prob, pdv, oid):
    return {
        "ts": ts, "sport": "nfl", "venue": f"oddsapi:{book}",
        "event_id": game_id,
        "market_id": f"{game_id}|{mkey}|{name}|{oc.get('name')}|{line}",
        "market_type": "prop", "subject": gsis, "line": line,
        "side": side.value, "best_bid": None, "best_ask": None, "mid": prob,
        "last": float(oc["price"]) if oc.get("price") is not None else None,
        "volume": None, "open_interest": None,
        "raw_ref": "oddsapi_historical/event_odds", "source": SOURCE,
        "prob_devig": pdv, "_outcome_id": oid,
    }


def _write_benchmarks(devig_by_outcome, ts):
    """Median de-vigged price across the benchmark books, plus dispersion."""
    import statistics
    rows = []
    for oid, pairs in devig_by_outcome.items():
        vals = [p for _b, p in pairs]
        if not vals:
            continue
        rows.append((oid, ts, len(vals), statistics.median(vals), min(vals),
                     max(vals), max(vals) - min(vals),
                     ",".join(sorted(b for b, _p in pairs))))
    if rows:
        store.replace_rows(
            "outcome_benchmark",
            ("outcome_id", "snapshot_ts", "n_books", "median_devig",
             "min_devig", "max_devig", "dispersion", "books"), rows, None)


def run_full(seasons=(2023, 2024, 2025), reserve=20000, do_props=True,
             do_featured=True, limit=None):
    """The paid backfill. Resumable, aborts hard at the reserve."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    slots = []
    for season in seasons:
        rows = con.execute(
            """SELECT game_id, MAX(kickoff_ts), MAX(away_team), MAX(home_team),
                      MAX(week) FROM nfl_games WHERE season=? GROUP BY game_id
                ORDER BY 2""", (season,)).fetchall()
        by_kick = {}
        for gid, kick, away, home, week in rows:
            by_kick.setdefault(kick, []).append((gid, away, home, week))
        for kick, games in sorted(by_kick.items()):
            iso = dt.datetime.fromtimestamp(kick - 600, dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            slots.append((season, iso, games))
    con.close()

    spend = Spend(reserve=reserve)
    done_props, done_feat = _done("props"), _done("featured")
    stats, violations, failures = {}, [], []
    totals = {"props": 0, "featured": 0, "rows": 0, "skipped": 0}
    aborted = None

    with httpx.Client(timeout=120, follow_redirects=True) as client:
        try:
            for season, iso, games in slots:
                pending = [g for g in games if g[0] not in done_props]
                need_feat = do_featured and iso not in done_feat
                if not pending and not need_feat:
                    totals["skipped"] += len(games)
                    continue

                events, code = list_events(client, spend, iso)
                if code != 200:
                    _checkpoint("listing", iso, season, 0, 0, "failed", str(code))
                    continue
                by_pair = {}
                from venues.mapping import team_abbr
                for e in events:
                    by_pair[(team_abbr(e["away_team"]),
                             team_abbr(e["home_team"]))] = e["id"]

                if need_feat:
                    r = client.get(
                        f"{config.ODDS_BASE}/historical/sports/"
                        f"{config.ODDS_SPORT}/odds",
                        params={"apiKey": config.ODDS_API_KEY,
                                "regions": config.ODDS_REGIONS,
                                "markets": ",".join(FEATURED_MARKETS),
                                "oddsFormat": "american", "date": iso})
                    spend.note(r)
                    if r.status_code == 200:
                        body = r.json()
                        store.archive_raw("oddsapi_historical",
                                          f"featured/{iso}", body)
                        gbp = {(a, h): g for g, a, h, _w in
                               [(g, a, h, w) for g, a, h, w in games]}
                        week = games[0][3]
                        frows = normalize_featured(body, season, week, gbp,
                                                   stats, violations)
                        _replace_and_write(frows, [iso], "featured")
                        totals["featured"] += 1
                        totals["rows"] += len(frows)
                        _checkpoint("featured", iso, season,
                                    int(r.headers.get("x-requests-last") or 0),
                                    len(frows), "done")
                    else:
                        _checkpoint("featured", iso, season, 0, 0, "failed",
                                    str(r.status_code))

                if do_props:
                    for gid, away, home, week in pending:
                        if limit and totals["props"] >= limit:
                            break
                        eid = by_pair.get((away, home))
                        if not eid:
                            _checkpoint("props", gid, season, 0, 0, "empty",
                                        "no historical event id")
                            continue
                        body, code = event_odds(client, spend, eid, iso,
                                                FULL_MARKETS)
                        if code != 200 or body is None:
                            _checkpoint("props", gid, season, 0, 0, "failed",
                                        str(code))
                            continue
                        prows, fails = normalize_props(body, season, week, gid,
                                                       (away, home), stats,
                                                       violations)
                        failures.extend(fails)
                        _replace_and_write(prows, [gid], "props")
                        totals["props"] += 1
                        totals["rows"] += len(prows)
                        # The REAL cost from the header, not a literal 50.
                        # The API bills for markets it actually returns, so a
                        # 2023 event costs ~34 where a 2025 one costs 50.
                        _checkpoint("props", gid, season, spend.last_cost,
                                    len(prows), "done")
                    if limit and totals["props"] >= limit:
                        break
                print(f"  {season} {iso}  props={totals['props']:>4} "
                      f"feat={totals['featured']:>4} rows={totals['rows']:>8,} "
                      f"spent={spend.credits:>6,} left={spend.remaining:,}",
                      flush=True)
        except ReserveExhausted as e:
            aborted = str(e)

    store.record_health("backfill_oddsapi_full", aborted is None,
                        f"{totals['rows']} rows, {spend.credits} credits, "
                        f"{len(violations)} overround violations"
                        + (f", ABORTED: {aborted}" if aborted else ""),
                        watermark=time.time())
    return {**totals, "credits": spend.credits, "calls": spend.calls,
            "remaining": spend.remaining, "violations": violations,
            "failures": failures, "markets": stats, "aborted": aborted}


def rederive_full():
    """Re-normalize EVERY archived payload. Costs nothing.

    The 51,953 credits bought bytes, not rows. Rows are a derivation, and this
    is the third time a name-normalization fix has been applied by re-parsing
    rather than re-buying - which is the entire return on invariant #2.
    """
    import glob
    import gzip
    import json
    import os
    from venues.mapping import team_abbr

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    meta = {}
    for gid, season, week, away, home in con.execute(
            """SELECT game_id, MAX(season), MAX(week), MAX(away_team),
                      MAX(home_team) FROM nfl_games
                WHERE season IN (2023,2024,2025) GROUP BY game_id"""):
        meta[(away, home, season)] = (gid, season, week, (away, home))
    con.close()

    stats, violations, failures = {}, [], []
    n_props = n_feat = rows_written = 0
    for f in sorted(glob.glob(os.path.join(
            config.RAW_DIR, "oddsapi_historical", "*", "*.jsonl.gz"))):
        for line in gzip.open(f, "rt", encoding="utf-8"):
            rec = json.loads(line)
            ep = rec["endpoint"]
            payload = rec["payload"]
            if ep.startswith("event_odds"):
                d = payload.get("data") or {}
                a, h = team_abbr(d.get("away_team")), team_abbr(d.get("home_team"))
                yr = int(str(d.get("commence_time", "0"))[:4] or 0)
                hit = meta.get((a, h, yr)) or meta.get((a, h, yr - 1))
                if not hit:
                    continue
                gid, season, week, teams = hit
                r, fails = normalize_props(payload, season, week, gid, teams,
                                           stats, violations)
                failures.extend(fails)
                rows_written += _replace_and_write(r, [gid], "props")
                n_props += 1
            elif ep.startswith("featured"):
                data = payload.get("data") or []
                gbp, season, week, ids = {}, None, None, []
                for ev in data:
                    a, h = (team_abbr(ev.get("away_team")),
                            team_abbr(ev.get("home_team")))
                    yr = int(str(ev.get("commence_time", "0"))[:4] or 0)
                    hit = meta.get((a, h, yr)) or meta.get((a, h, yr - 1))
                    if hit:
                        gbp[(a, h)] = hit[0]
                        season, week = hit[1], hit[2]
                        ids.append(hit[0])
                if not gbp:
                    continue
                r = normalize_featured(payload, season, week, gbp, stats,
                                       violations)
                rows_written += _replace_and_write(r, ids, "featured")
                n_feat += 1
            if (n_props + n_feat) % 200 == 0 and (n_props + n_feat):
                print(f"    re-derived {n_props} props / {n_feat} featured, "
                      f"{rows_written:,} rows", flush=True)
    return {"props": n_props, "featured": n_feat, "rows": rows_written,
            "violations": violations, "failures": failures, "markets": stats}


def _replace_and_write(rows, event_ids, kind):
    """A re-parse replaces its own prior derivation; it never appends."""
    if event_ids:
        ph = ",".join("?" for _ in event_ids)
        with store.db() as c:
            c.execute(
                f"DELETE FROM quotes WHERE source=? AND event_id IN ({ph}) "
                f"AND raw_ref LIKE ?",
                (SOURCE, *event_ids,
                 "oddsapi_historical/featured" if kind == "featured"
                 else "oddsapi_historical/event_odds"))
    if not rows:
        return 0
    n = store.write_quotes(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        dedupe=False)
    for r in rows:
        store.record_mapping(r["venue"], r["market_id"], r["_outcome_id"],
                             "oddsapi_historical", 1.0)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--week", type=int, default=8)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--from-archive", action="store_true",
                    help="re-normalize the archived payloads; costs 0 credits")
    ap.add_argument("--full", action="store_true", help="THE PAID BACKFILL")
    ap.add_argument("--reserve", type=int, default=20000)
    ap.add_argument("--seasons", default="2023,2024,2025")
    ap.add_argument("--no-props", action="store_true")
    ap.add_argument("--no-featured", action="store_true")
    ap.add_argument("--progress", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.verify:
        verify(args.season, args.week)
        return
    if args.progress:
        with store.db() as c:
            for k, st, n, cr, rw in c.execute(
                    """SELECT kind, status, COUNT(*), SUM(credits), SUM(rows)
                         FROM oddsapi_progress GROUP BY 1,2 ORDER BY 1,2"""):
                print(f"  {k:<9} {st:<7} {n:>5} keys  {cr or 0:>7,} credits  "
                      f"{rw or 0:>9,} rows")
        return
    if args.from_archive and args.full:
        s = rederive_full()
        print(f"re-derived {s['props']} props + {s['featured']} featured -> "
              f"{s['rows']:,} rows, CREDITS SPENT=0")
        print(f"markets: {s['markets']}")
        print(f"overround violations: {len(s['violations'])}")
        for v in s["violations"][:20]:
            print(f"  {v}")
        uniq = {n: w for n, w in s["failures"]}
        print(f"unresolved names: {len(uniq)}")
        for n, w in sorted(uniq.items()):
            print(f"  {n:<34} {w}")
        return
    if args.full:
        seasons = tuple(int(x) for x in args.seasons.split(","))
        print(f"PAID BACKFILL seasons={seasons} reserve={args.reserve:,}")
        s2 = run_full(seasons, args.reserve, not args.no_props,
                      not args.no_featured, args.limit)
        print("")
        print(f"props={s2['props']} featured={s2['featured']} "
              f"rows={s2['rows']:,} CREDITS={s2['credits']:,} "
              f"remaining={s2['remaining']:,}")
        if s2["aborted"]:
            print(f"ABORTED: {s2['aborted']}")
        print(f"overround violations: {len(s2['violations'])}")
        for v in s2["violations"][:10]:
            print(f"  {v}")
        uniq = {n: w for n, w in s2["failures"]}
        print(f"unresolved names: {len(uniq)}")
        for n, w in sorted(uniq.items())[:20]:
            print(f"  {n:<30} {w}")
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
