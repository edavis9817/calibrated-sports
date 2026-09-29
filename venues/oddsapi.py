"""The Odds API adapter — sportsbook lines.

THIS ONE IS DIFFERENT FROM THE EXCHANGES AND THE DIFFERENCE MATTERS.

Kalshi and Polymarket are free to poll, so we tick them every 15-60s. The Odds
API bills credits, and player props are **per-event** calls costing roughly
(markets x regions) credits PER GAME. A 60-second poll over a 13-game slate
would spend a 500-credit month in under two minutes.

So this adapter does not poll. It takes **snapshots on a schedule keyed to
kickoff** (config.ODDS_SNAPSHOTS_MIN). That is not a compromise - it is what
CLV actually needs. Closing-line value requires the CLOSE, not a tick history:
one accurate snapshot at T-10min is worth more than a thousand at T-6h.

Credit arithmetic, so the tradeoff is explicit:
    bulk game lines : ~1 credit x markets x regions, ALL games in one call
    player props    : ~1 credit x markets x regions, PER GAME

    13 games x 3 prop markets x 5 snapshots = ~195 credits/week
    => the 500/month free tier covers roughly 2.5 weeks of full-slate props.

Trim in this order when short: drop the T-4320 and T-1440 snapshots first, then
restrict to a subset of games. Never drop the T-10 snapshot - that one IS the
measurement.

Endpoints (v4):
    GET /sports/{sport}/odds                      bulk game lines
    GET /sports/{sport}/events                    event list (free)
    GET /sports/{sport}/events/{id}/odds          player props, per event

Response headers `x-requests-remaining` / `x-requests-used` report true spend;
we persist them every call so budget is observed, not estimated.

!! UNVERIFIED SHAPES !! Written from documentation - this session could not
reach the API. Run `python verify.py` before trusting field names.
"""
import time
from datetime import datetime, timezone

import config
import store
from core import outcomes
from venues import mapping
from venues.base import VenueClient

# American odds -> implied probability (still contains the vig; de-vig later,
# in analysis, never at ingest - raw first)
def american_to_prob(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


class OddsApiClient(VenueClient):
    name = "oddsapi"

    def __init__(self, http, limiter):
        super().__init__(http, limiter)
        self.remaining = None
        self.used = None
        self.last_cost = None           # x-requests-last: what the last call billed
        self._snapshots_taken = {}      # (event_id, target_min) -> ts
        # Halftime capture state. Kickoffs are REMEMBERED across discoveries so
        # a game that has started is not forgotten if the event list drops it.
        self._kickoffs = {}             # event_id -> scheduled kickoff ts
        self._halftime_last = 0.0
        self._halftime_day = (None, 0)  # (UTC date, credits spent that day)

    # ---- credit accounting -------------------------------------------------

    async def _get(self, path, params, archive_as):
        params = dict(params or {})
        params["apiKey"] = config.ODDS_API_KEY
        await self.limiter.wait()
        r = await self.http.get(f"{config.ODDS_BASE}{path}", params=params)
        # capture quota headers even on an error response
        self.remaining = _int(r.headers.get("x-requests-remaining"), self.remaining)
        self.used = _int(r.headers.get("x-requests-used"), self.used)
        self.last_cost = _int(r.headers.get("x-requests-last"), None)
        r.raise_for_status()
        payload = r.json()
        store.archive_raw(self.name, archive_as, payload)
        return payload

    def budget_ok(self) -> bool:
        if self.remaining is None:
            return True                      # unknown until the first call
        return self.remaining > config.ODDS_RESERVE

    # ---- discovery ---------------------------------------------------------

    async def list_markets(self) -> list[dict]:
        """Event list is free of charge and gives us kickoff times, which drive
        the snapshot schedule."""
        events = await self._get(f"/sports/{config.ODDS_SPORT}/events",
                                 {}, "events")
        out = []
        for e in events or []:
            out.append({
                "venue": self.name,
                "market_id": f"game:{e.get('id')}",
                "event_id": e.get("id"),
                "market_type": "game",
                "subject": None,
                "line": None,
                "title": f"{e.get('away_team')} @ {e.get('home_team')}",
                "open_ts": None,
                "close_ts": _iso(e.get("commence_time")),
                "settle_ts": None,
                "result": None,
            })
        return out

    # ---- snapshot scheduling ----------------------------------------------

    def due_snapshots(self, markets: list[dict]) -> list[tuple[dict, int]]:
        """Which (event, target) snapshots are due right now."""
        now, due = time.time(), []
        tol = config.ODDS_SNAPSHOT_TOLERANCE_MIN * 60
        for m in markets:
            kick = m.get("close_ts")
            if not kick:
                continue
            mins_out = (kick - now) / 60.0
            for target in config.ODDS_SNAPSHOTS_MIN:
                key = (m["event_id"], target)
                if key in self._snapshots_taken:
                    continue
                if 0 <= (target - mins_out) * 60 <= tol:
                    due.append((m, target))
        return due

    async def fetch_quotes(self, markets: list[dict]) -> list[dict]:
        """Take any snapshots that are due. Returns normalized quote rows."""
        if not self.budget_ok():
            return []
        due = self.due_snapshots(markets)
        if not due:
            return []

        rows = []
        # one bulk call covers game lines for every event - always worth it
        try:
            bulk = await self._get(f"/sports/{config.ODDS_SPORT}/odds",
                                   {"regions": config.ODDS_REGIONS,
                                    "markets": config.ODDS_GAME_MARKETS,
                                    "oddsFormat": "american"}, "odds_bulk")
            rows.extend(self._parse_events(bulk, "game"))
        except Exception:
            pass    # a failed bulk call must not block the per-event props

        for m, target in due:
            if not self.budget_ok():
                break
            try:
                ev = await self._get(
                    f"/sports/{config.ODDS_SPORT}/events/{m['event_id']}/odds",
                    {"regions": config.ODDS_REGIONS,
                     "markets": config.ODDS_PROP_MARKETS,
                     "oddsFormat": "american"}, "odds_props")
                rows.extend(self._parse_events([ev], "prop"))
                self._snapshots_taken[(m["event_id"], target)] = time.time()
            except Exception:
                continue
        return rows

    # ---- halftime capture (brief 021 B2) ------------------------------------

    def remember(self, markets, now=None):
        now = time.time() if now is None else now
        for m in markets or []:
            if m.get("event_id") and m.get("close_ts"):
                self._kickoffs[m["event_id"]] = m["close_ts"]
        horizon = config.ODDS_HALFTIME_TO_MIN * 60 + 3600
        self._kickoffs = {e: k for e, k in self._kickoffs.items() if now - k < horizon}

    def halftime_events(self, now=None) -> list[str]:
        """Events whose halftime window contains `now`."""
        now = time.time() if now is None else now
        lo, hi = config.ODDS_HALFTIME_FROM_MIN * 60, config.ODDS_HALFTIME_TO_MIN * 60
        return sorted(e for e, k in self._kickoffs.items() if lo <= now - k <= hi)

    def halftime_active(self, now=None) -> bool:
        return config.ODDS_HALFTIME_ENABLED and bool(self.halftime_events(now))

    def _halftime_spent(self, now):
        day = datetime.fromtimestamp(now, timezone.utc).date()
        d, spent = self._halftime_day
        return 0 if d != day else spent

    def halftime_due(self, now=None) -> bool:
        now = time.time() if now is None else now
        return (self.halftime_active(now)
                and now - self._halftime_last >= config.ODDS_HALFTIME_EVERY - 1
                and self.budget_ok()
                and self._halftime_spent(now) < config.ODDS_HALFTIME_DAILY_CAP)

    async def fetch_halftime(self, now=None) -> list[dict]:
        """One bulk spreads+totals call for the events in a halftime window.
        Every row carries the book's own `last_update` as `source_ts`."""
        now = time.time() if now is None else now
        if not self.halftime_due(now):
            return []
        ids = self.halftime_events(now)
        self._halftime_last = now
        payload = await self._get(f"/sports/{config.ODDS_SPORT}/odds",
                                  {"regions": config.ODDS_REGIONS,
                                   "markets": config.ODDS_HALFTIME_MARKETS,
                                   "oddsFormat": "american",
                                   "eventIds": ",".join(ids)}, "odds_halftime")
        cost = self.last_cost
        if cost is None:   # header missing: bill the documented price, never zero
            cost = len(config.ODDS_HALFTIME_MARKETS.split(",")) * len(config.ODDS_REGIONS.split(","))
        day = datetime.fromtimestamp(now, timezone.utc).date()
        self._halftime_day = (day, self._halftime_spent(now) + cost)
        return self._parse_events(payload, "game", tag="halftime")

    # ---- parsing -----------------------------------------------------------

    def _parse_events(self, events, kind, tag=None) -> list[dict]:
        rows, now = [], time.time()
        if isinstance(events, dict):
            events = [events]
        for e in events or []:
            eid = e.get("id")
            for bk in e.get("bookmakers", []) or []:
                book = bk.get("key")
                if config.ODDS_SHARP_BOOK and book != config.ODDS_SHARP_BOOK:
                    # keep everything if no sharp book is configured; otherwise
                    # store all books but tag the sharp one for CLV
                    pass
                for mkt in bk.get("markets", []) or []:
                    mkey = mkt.get("key")
                    # The BOOK's clock, per market, falling back to the book's.
                    # Brief 020: books on one line disagreed by up to ~35pp
                    # in-game because one was live and one stale; without this
                    # the two cannot be told apart.
                    source_ts = _iso(mkt.get("last_update") or bk.get("last_update"))
                    for oc in mkt.get("outcomes", []) or []:
                        price = american_to_prob(oc.get("price"))
                        rows.append({
                            "ts": now, "sport": "nfl", "venue": f"{self.name}:{book}",
                            "event_id": eid,
                            "market_id": f"{eid}|{mkey}|{oc.get('description') or ''}|{oc.get('name')}",
                            "market_type": kind,
                            "subject": oc.get("description") or oc.get("name"),
                            "line": _f(oc.get("point")),
                            "side": oc.get("name"),
                            "best_bid": None, "best_ask": None,
                            "mid": price,        # vig-inclusive; de-vig in analysis
                            "last": _f(oc.get("price")),
                            "volume": None, "open_interest": None,
                            "raw_ref": f"oddsapi/{tag or kind}",
                            "source_ts": source_ts,
                        })
        return rows


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---- outcome mapping (brief 003) -------------------------------------------

# The Odds API market keys. Structural, so a renamed display label cannot move
# a market onto a different stat.
MARKET_STAT = {
    "player_receptions": outcomes.Stat.RECEPTIONS,
    "player_reception_yds": outcomes.Stat.RECEIVING_YARDS,
    "player_rush_attempts": outcomes.Stat.RUSH_ATTEMPTS,
    "player_rush_yds": outcomes.Stat.RUSH_YARDS,
    "player_pass_yds": outcomes.Stat.PASSING_YARDS,
    "player_pass_attempts": outcomes.Stat.PASS_ATTEMPTS,
    "player_pass_completions": outcomes.Stat.COMPLETIONS,
    "player_anytime_td": outcomes.Stat.ANYTIME_TD,
    "player_tackles_assists": outcomes.Stat.TACKLES_ASSISTS,
    "player_sacks": outcomes.Stat.SACKS,
    # An ALTERNATE ladder is the same claim at more thresholds - over 4.5 on
    # `player_receptions_alternate` is Kalshi's "5+ receptions" exactly - so it
    # maps to the base stat, as jobs/backfill_oddsapi.py already does for
    # receptions. Measured 2026-09-29: the live capture carries 60,108
    # alternate (book, player, side, line) rows, and before these entries every
    # one was recorded "not a player prop".
    "player_receptions_alternate": outcomes.Stat.RECEPTIONS,
    "player_reception_yds_alternate": outcomes.Stat.RECEIVING_YARDS,
    "player_rush_attempts_alternate": outcomes.Stat.RUSH_ATTEMPTS,
    "player_rush_yds_alternate": outcomes.Stat.RUSH_YARDS,
    "player_pass_yds_alternate": outcomes.Stat.PASSING_YARDS,
}

SIDE = {"over": outcomes.Side.OVER, "under": outcomes.Side.UNDER,
        "yes": outcomes.Side.YES, "no": outcomes.Side.NO}


# ---- the live prop join (a-53) ----------------------------------------------
#
# A live prop QUOTE's market_id is `{event}|{market key}|{player}|{side}` and
# carries NO LINE. It is not an instrument: measured 2026-09-29, one such id
# carries up to 28 distinct lines inside a single snapshot and up to 85 across
# its life, because the line moves and some keys are ladders. An outcome_id
# includes the line, so the join is keyed one level down - one `markets` row per
# (book, quote id, line), with the line appended:
#
#     {event}|{market key}|{player}|{side}|{line}
#
# which is the same five-part shape jobs/backfill_oddsapi.py already uses for
# the 2023-2025 closes. The quote rows themselves are NOT rewritten (raw first,
# and jobs/board_read.py parses the four-part id): a study goes from an outcome
# to its markets rows, then `split_prop_market_id` gives the (quote id, line)
# to select the quotes with - an indexed lookup on (venue, market_id).

def prop_market_id(quote_market_id: str, line) -> str:
    """The instrument id of ONE line of a live book prop."""
    return f"{quote_market_id}|{float(line)!r}"


def split_prop_market_id(market_id: str):
    """(quote market_id, line) from an instrument id; the inverse of
    `prop_market_id`. Raises ValueError on anything else - a caller holding a
    four-part quote id must not get a line back that was never there."""
    head, sep, line = (market_id or "").rpartition("|")
    if not sep or head.count("|") != 3:
        raise ValueError(f"not a five-part prop instrument id: {market_id!r}")
    return head, float(line)


def derive_prop_markets(con, event_ids=None):
    """`markets` rows for every (book, prop, line) in the LIVE quote log.

    `con` is a read-only connection. Keyed per Odds API event through the
    (source, event_id) index - a venue-prefix scan of `quotes` walks every
    Kalshi row too. `first_seen` / `last_seen` are the first and last quote
    captured for that line, and `close_ts` is the game's kickoff from the
    discovery row, so `close_ts - last_seen` is how long before kickoff the
    book's last price for this line was taken: a game whose last snapshot fell
    outside a close window is a FIELD on every row, not a silent exclusion.

    Returns (rows, census). A quote with no line (a one-sided market) cannot
    be an instrument here and is counted, never dropped silently.
    """
    games = {eid: (title, close_ts) for eid, title, close_ts in con.execute(
        "SELECT event_id, title, close_ts FROM markets "
        "WHERE venue = 'oddsapi' AND market_type = 'game'")}
    if event_ids is not None:
        wanted = set(event_ids)
        games = {e: g for e, g in games.items() if e in wanted}
    rows, census = [], {"events": len(games), "events_with_props": 0,
                        "no_line_quote_ids": 0}
    for eid, (title, kick) in sorted(games.items()):
        got = con.execute(
            "SELECT venue, market_id, line, MAX(subject), MIN(ts), MAX(ts) "
            "FROM quotes WHERE source = 'live' AND event_id = ? "
            "AND market_type = 'prop' AND venue LIKE 'oddsapi:%' "
            "GROUP BY venue, market_id, line", (eid,)).fetchall()
        if got:
            census["events_with_props"] += 1
        for venue, qmid, line, subject, first, last in got:
            if line is None:
                census["no_line_quote_ids"] += 1
                continue
            rows.append({
                "venue": venue, "market_id": prop_market_id(qmid, line),
                "event_id": eid, "sport": "nfl", "market_type": "prop",
                "subject": subject, "line": float(line), "title": title,
                "open_ts": None, "close_ts": kick, "settle_ts": None,
                "result": None, "first_seen": first, "last_seen": last,
            })
    census["rows"] = len(rows)
    return rows, census


class BookOnly(mapping.Unresolved):
    """The claim resolved, and no outcome row exists for it.

    Not an identity failure: the player, game, stat, line and side are all
    known and so is the outcome_id, which the reason carries. It is left
    unlinked on purpose - creating the outcome would add it to the settled
    record `jobs/export_web.py` publishes as prop history (a-53).
    """
    PREFIX = "book-only claim"


def book_only_reason(o) -> str:
    """`book-only claim <outcome_id> <key>: ...` - the claim's full identity,
    so a study can count and join book-only lines from the store without the
    outcome row existing. `parse_book_only` is the inverse."""
    return f"{BookOnly.PREFIX} {o.outcome_id} {o.key}: outcome not created (a-53)"


def parse_book_only(reason):
    """(outcome_id, key) from a book-only unmapped_reason, else None."""
    if not reason or not reason.startswith(BookOnly.PREFIX + " "):
        return None
    rest = reason[len(BookOnly.PREFIX) + 1:]
    oid, _, tail = rest.partition(" ")
    key = tail.split(": ", 1)[0]
    return (oid, key) if oid and key else None


def _memo(cache, key, fn):
    """fn() memoised in `cache` (if given), exceptions included."""
    if cache is not None and key in cache:
        got = cache[key]
    else:
        try:
            got = fn()
        except mapping.Unresolved as e:
            got = e
        if cache is not None:
            cache[key] = got
    if isinstance(got, Exception):
        raise got
    return got


def _prop_claim(row: dict, game=None, player=None):
    """Resolve one five-part live prop row to (Outcome, game_id, method, conf).

    `game` and `player` are optional memo dicts so a batch resolves each event
    and each (name, season, teams) once. Raises mapping.Unresolved with a
    reason for anything that is not a resolvable player prop.
    """
    try:
        qmid, line = split_prop_market_id(row.get("market_id"))
    except ValueError as e:
        raise mapping.Unresolved(str(e)[:120])
    _eid, mkey, player_name, side_name = qmid.split("|")
    stat = MARKET_STAT.get(mkey)
    if stat is None:
        raise mapping.Unresolved(f"market key {mkey!r} is not a player prop")
    side = SIDE.get((side_name or "").strip().lower())
    if side is None:
        raise mapping.Unresolved(f"unknown side {side_name!r}")
    close_ts = row.get("close_ts")
    if not close_ts:
        raise mapping.Unresolved("no kickoff time to place the game")
    title = row.get("title") or ""
    if " @ " not in title:
        raise mapping.Unresolved(f"no team pair on the discovery row: {title!r}")
    # The title is OUR construction from the event's structured away_team /
    # home_team fields (list_markets), the same read jobs/board_read.py makes.
    away, home = title.split(" @ ", 1)
    day = datetime.fromtimestamp(close_ts, timezone.utc).strftime("%Y-%m-%d")
    season, week, game_id = _memo(game, (away, home, day),
                                  lambda: mapping.game_for(away, home, day))
    teams = tuple(t for t in (mapping.team_abbr(away), mapping.team_abbr(home)) if t)
    clean, team_hint, pos_hint = mapping.parse_book_name(player_name)
    hint = (team_hint,) if team_hint else teams
    gsis, how, conf = _memo(
        player, (clean, season, hint, pos_hint),
        lambda: mapping.resolve_player(clean, season, position=pos_hint, teams=hint))
    o = outcomes.player_prop(season, week, gsis, stat, float(line), side,
                             event_id=game_id)
    return o, game_id, f"oddsapi:{mkey}+{how}", conf


def map_prop_rows(rows, existing, create=False):
    """Batch-map five-part live prop rows.

    `existing` is the set of outcome_ids already in `outcomes`. A claim whose
    outcome exists is linked. One whose outcome does NOT exist is recorded as
    `book-only claim <outcome_id>` unless `create` - see BookOnly for why that
    is off by default. Returns (mappings, new_outcomes, census): mappings are
    `store.record_mappings` tuples, new_outcomes `store.upsert_outcomes` pairs
    (always empty unless `create`).
    """
    game, player = {}, {}
    mappings, new = [], {}
    census = {"linked": 0, "book_only": 0, "unresolved": 0}
    for r in rows:
        try:
            o, gid, method, conf = _prop_claim(r, game, player)
        except mapping.Unresolved as e:
            mappings.append((r["venue"], r["market_id"], None, None, None,
                             str(e)[:200]))
            census["unresolved"] += 1
            continue
        oid = o.outcome_id
        if oid in existing or create:
            if oid not in existing:
                new[oid] = (o, gid)
            mappings.append((r["venue"], r["market_id"], oid, method, conf, None))
            census["linked"] += 1
        else:
            mappings.append((r["venue"], r["market_id"], None, None, None,
                             book_only_reason(o)))
            census["book_only"] += 1
    census["new_outcomes"] = len(new)
    return mappings, list(new.values()), census


def map_market(row: dict):
    """One Odds API row -> (outcome_id, method, confidence).

    Five-part rows are live prop instruments (`derive_prop_markets`); they are
    LINKED to an existing outcome and never create one (BookOnly). A four-part
    quote id carries no line and cannot name a claim. Discovery rows carry
    `game:{event_id}` and describe a whole game rather than a claim, so they
    are recorded unmapped with that reason rather than being forced into an
    outcome.
    """
    mid = row.get("market_id") or ""
    if mid.startswith("game:"):
        raise mapping.Unresolved(
            "discovery row: an event, not a claim - props arrive as quote rows "
            "at snapshot time")
    if mid.count("|") != 4:
        raise mapping.Unresolved(
            f"not a five-part prop instrument id (no line): {mid[:60]!r}")
    o, _gid, method, conf = _prop_claim(row)
    with store.db() as c:
        have = c.execute("SELECT 1 FROM outcomes WHERE outcome_id = ?",
                         (o.outcome_id,)).fetchone()
    if not have:
        raise BookOnly(book_only_reason(o))
    return o.outcome_id, method, conf
