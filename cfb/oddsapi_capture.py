"""Odds API capture logic with no network: parsing, the kickoff-hour plan, the P1 order.

The requests themselves are made by `jobs/ingest_cfb.py` through `cfb.oddsapi.Client`.
Everything here is a pure function of archived responses and `cfb.db`, so every
decision it makes can be replayed and tested.

FORWARD CAPTURE (approved 2026-09-17: one bulk h2h/spreads/totals snapshot per
kickoff hour, 3 credits, FORWARD_WEEKLY_CAP a week). A "kickoff hour" is the UTC
clock hour of the Odds API's `commence_time` - that time, not `cfb_games.start_ts`,
is what the books price against, and our schedule has TBD placeholders. The
snapshot for an hour is taken in the window [earliest kickoff - OPEN_S, earliest
kickoff - MIN_LEAD_S], so it is a near-close for that hour's first game and at most
an hour stale for its last. One bulk call returns every listed game, and a quote
is only ever a close for a game that had not kicked off when it was fetched -
deciding that is a query on `commence_ts` and `fetched_ts`, never a column here.

If a week lists more kickoff hours than the cap buys, the hours with the MOST
games are kept (ties to the earlier), and the rest are recorded
`skipped_weekly_cap` - visibly, not silently.
"""
import json
from collections import defaultdict

from cfb import oddsapi
from cfb.oddsapi import iso_ts

OPEN_S = 8 * 60          # window opens 8 minutes before the hour's first kickoff
MIN_LEAD_S = 60          # and closes 1 minute before it; a 5-minute tick always lands inside
REFRESH_MAX_AGE_S = 3 * 3600
REFRESH_NEAR_KICK_S = 40 * 60
REFRESH_NEAR_AGE_S = 4 * 60


# -----------------------------------------------------------------------------
# parsing: raw bytes -> observation rows (schema.ODDS_TABLES)
# -----------------------------------------------------------------------------

def _ts(s):
    return iso_ts(s) if s else None


def parse_events(body: bytes):
    return {"cfb_odds_events": [
        (e["id"], _ts(e.get("commence_time")), e.get("home_team"), e.get("away_team"))
        for e in oddsapi.events_from(body)]}


def parse_odds(body: bytes):
    events, quotes = [], []
    for e in oddsapi.events_from(body):
        ct = _ts(e.get("commence_time"))
        events.append((e["id"], ct, e.get("home_team"), e.get("away_team")))
        for b in e.get("bookmakers") or []:
            blu = _ts(b.get("last_update"))
            for m in b.get("markets") or []:
                mlu = _ts(m.get("last_update"))
                for o in m.get("outcomes") or []:
                    quotes.append((e["id"], ct, b.get("key"), blu, m.get("key"), mlu,
                                   o.get("name"), o.get("price"), o.get("point")))
    return {"cfb_odds_events": events, "cfb_odds_quotes": quotes}


def parse_event_markets(body: bytes):
    e = json.loads(body)
    if not isinstance(e, dict) or "id" not in e:
        raise oddsapi.OddsApiError("event markets response is not a single event object")
    ct = _ts(e.get("commence_time"))
    rows = [(e["id"], ct, b.get("key"), m.get("key"), _ts(m.get("last_update")))
            for b in e.get("bookmakers") or [] for m in b.get("markets") or []]
    return {"cfb_odds_events": [(e["id"], ct, e.get("home_team"), e.get("away_team"))],
            "cfb_odds_event_markets": rows}


PARSERS = {"oddsapi_events": parse_events, "oddsapi_odds": parse_odds,
           "oddsapi_event_markets": parse_event_markets}


def store_rows(conn, file_id, fetched_ts, parsed):
    """Replace this file's rows, table by table. Idempotent per raw file."""
    from cfb import schema
    for table in schema.ODDS_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE src_file_id=?", (file_id,))
    for table, rows in parsed.items():
        cols = [c for c, _t in schema.ODDS_TABLES[table][1]]
        ph = ",".join("?" * (len(cols) + 2))
        conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}, src_file_id, fetched_ts) "
                         f"VALUES ({ph})", [tuple(r) + (file_id, fetched_ts) for r in rows])
    conn.commit()
    return {t: len(r) for t, r in parsed.items()}


# -----------------------------------------------------------------------------
# the forward plan
# -----------------------------------------------------------------------------

def hour_groups(events):
    """hour_ts -> {"earliest": ts, "n": count, "week": week_start_ts}"""
    g = defaultdict(lambda: {"earliest": None, "n": 0})
    for e in events:
        t = iso_ts(e["commence_time"])
        h = g[t // 3600 * 3600]
        h["n"] += 1
        h["earliest"] = t if h["earliest"] is None else min(h["earliest"], t)
    for hour, h in g.items():
        h["week"] = oddsapi.week_start_ts(hour)
    return dict(g)


def needs_refresh(listing_fetched_ts, listing_events, now):
    """Refresh the (free) events listing when it is missing, older than 3 hours, or older
    than 4 minutes with a kickoff inside the next 40 - so a kickoff time that moves is
    seen before the snapshot that depends on it."""
    if listing_fetched_ts is None:
        return True
    age = now - listing_fetched_ts
    if age > REFRESH_MAX_AGE_S:
        return True
    soon = any(now < iso_ts(e["commence_time"]) <= now + REFRESH_NEAR_KICK_S
               for e in listing_events)
    return soon and age > REFRESH_NEAR_AGE_S


def plan_forward(conn, events, now, cap=None):
    """Decide this tick. Returns (due, missed, skipped):
         due     [(hour_ts, group, credits_left_this_week)] - at most one, the earliest
         missed  [(hour_ts, group)] - first kickoff passed with no snapshot row
         skipped [(hour_ts, group)] - in its window but outside the week's allowance
    `cap` defaults to `oddsapi.FORWARD_WEEKLY_CAP` READ AT CALL TIME: as a default
    argument it was bound at import, so raising the constant would not have reached this
    function and a test could not lower it.

    Writes nothing."""
    cap = oddsapi.FORWARD_WEEKLY_CAP if cap is None else cap
    _path, _params, cost = oddsapi.PAID["odds"]
    groups = hour_groups(events)
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT hour_ts, outcome, cost FROM cfb_odds_snapshots")}

    def open_hour(h):                   # never tried, or tried and failed at no cost
        return h not in rows or (rows[h][0] == "error" and rows[h][1] == 0)

    due, missed, skipped = [], [], []
    for hour in sorted(groups):
        grp = groups[hour]
        if not open_hour(hour):
            continue
        if grp["earliest"] - MIN_LEAD_S < now:
            missed.append((hour, grp))
            continue
        if not (grp["earliest"] - OPEN_S <= now):
            continue
        spent = conn.execute("SELECT COALESCE(SUM(cost), 0) FROM cfb_odds_snapshots WHERE "
                             "week_start_ts=?", (grp["week"],)).fetchone()[0]
        left = cap - spent
        # every hour of this week still to be bought, including this one
        pending = sorted((h for h in groups if groups[h]["week"] == grp["week"] and open_hour(h)
                          and groups[h]["earliest"] - MIN_LEAD_S >= now),
                         key=lambda h: (-groups[h]["n"], groups[h]["earliest"]))
        allowed = set(pending[:max(left, 0) // cost])
        if hour in allowed:
            due.append((hour, grp, left))
            break
        skipped.append((hour, grp))
    return due, missed, skipped


# -----------------------------------------------------------------------------
# P1
# -----------------------------------------------------------------------------

DIVISION_ORDER = {"fbs/fbs": 0, "fbs/fcs": 1}


def plan_p1(conn, events, now, season):
    """Pre-match events only, FBS/FBS first, then FBS/FCS, then the rest, then events
    that do not join; ties by kickoff. Returns [(event, division label)]."""
    from cfb.oddsapi_join import div_pair, match
    upcoming = [e for e in events if iso_ts(e["commence_time"]) > now]
    _g, matched, missed, ambiguous, _fb = match(conn, upcoming, season)
    label = {id(e): div_pair(g) for e, g in matched}
    out = [(e, label.get(id(e), "unjoined")) for e in upcoming]
    out.sort(key=lambda x: (DIVISION_ORDER.get(x[1], 2 if x[1] != "unjoined" else 3),
                            iso_ts(x[0]["commence_time"])))
    return out
