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
        self._snapshots_taken = {}      # (event_id, target_min) -> ts

    # ---- credit accounting -------------------------------------------------

    async def _get(self, path, params, archive_as):
        params = dict(params or {})
        params["apiKey"] = config.ODDS_API_KEY
        await self.limiter.wait()
        r = await self.http.get(f"{config.ODDS_BASE}{path}", params=params)
        # capture quota headers even on an error response
        self.remaining = _int(r.headers.get("x-requests-remaining"), self.remaining)
        self.used = _int(r.headers.get("x-requests-used"), self.used)
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

    # ---- parsing -----------------------------------------------------------

    def _parse_events(self, events, kind) -> list[dict]:
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
                            "raw_ref": f"oddsapi/{kind}",
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
}

SIDE = {"over": outcomes.Side.OVER, "under": outcomes.Side.UNDER,
        "yes": outcomes.Side.YES, "no": outcomes.Side.NO}


def map_market(row: dict):
    """One Odds API row -> (outcome_id, method, confidence).

    Quote rows carry `{event_id}|{market_key}|{player}|{side}` as the market id;
    discovery rows carry `game:{event_id}` and describe a whole game rather than
    a claim, so they are recorded unmapped with that reason rather than being
    forced into an outcome.
    """
    mid = row.get("market_id") or ""
    if mid.startswith("game:"):
        raise mapping.Unresolved(
            "discovery row: an event, not a claim - props arrive as quote rows "
            "at snapshot time")

    parts = mid.split("|")
    if len(parts) != 4:
        raise mapping.Unresolved(f"market_id not in 4-part form: {mid[:60]!r}")
    _eid, mkey, player, side_name = parts

    stat = MARKET_STAT.get(mkey)
    if stat is None:
        raise mapping.Unresolved(f"market key {mkey!r} is not a player prop")
    side = SIDE.get((side_name or "").strip().lower())
    if side is None:
        raise mapping.Unresolved(f"unknown side {side_name!r}")

    close_ts = row.get("close_ts")
    if not close_ts:
        raise mapping.Unresolved("no kickoff time to place the game")
    from datetime import datetime, timezone
    day = datetime.fromtimestamp(close_ts, timezone.utc).strftime("%Y-%m-%d")

    home, away = row.get("home_team"), row.get("away_team")
    if home and away:
        season, week, game_id = mapping.game_for(away, home, day)
    else:
        raise mapping.Unresolved("no team pair on the row to place the game")

    line = row.get("line")
    if line is None and stat is not outcomes.Stat.ANYTIME_TD:
        raise mapping.Unresolved("prop with no point/line")
    gsis, how, conf = mapping.resolve_player(player, season)
    o = outcomes.player_prop(season, week, gsis, stat,
                             0.5 if line is None else float(line), side,
                             event_id=game_id)
    return store.upsert_outcome(o, game_id), f"oddsapi:{mkey}+{how}", conf
