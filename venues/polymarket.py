"""Polymarket adapter.

Two services:
  gamma-api.polymarket.com/events   - discovery, tag-filtered; carries quotes
  clob.polymarket.com/prices        - BATCHED top-of-book, <=500 entries

A Polymarket "market" resolves to a pair of ERC1155 outcome tokens; you quote
the YES token. Prices are already probabilities in 0-1, unlike a cents venue.

VERIFIED AGAINST THE LIVE API 2026-09-09:

1. BLIND /markets PAGINATION 422s AT offset=2100 - the body says so outright:
   "offset too large, use /markets/keyset for deeper pagination". Football sits
   far past that, which is why the old walk found nothing. `/events` filtered by
   `tag_slug=nfl` pages cleanly to the end instead: 588 events in 6 calls, no
   422 anywhere. Discovery now RAISES at POLY_MAX_OFFSET rather than treating a
   ceiling as "stop paging": a truncated catalogue looks exactly like a quiet
   day - the loop keeps succeeding and the dead-man stays green - and the gap
   only surfaces later as history nobody can re-derive. If this ever fires, the
   answer is /markets/keyset or a narrower tag, never a bigger cap.

2. GAMMA CARRIES `bestBid`/`bestAsk` INLINE on the nested markets - 14,011 of
   14,133 of them - so discovery yields a free quote for everything it finds.
   It is not a way to REFRESH quotes though: the events payload is 6.9MB per
   page and there are six pages, so polling it archives ~26GB/day to carry
   three numbers per market. `POST /clob/prices` reports the same top-of-book
   (verified value-for-value identical against the inline fields on 2026-09-09)
   in 21KB per 250 markets. So: discovery quotes come from gamma, refreshes
   come from the CLOB. `/clob/books` stays unused - that one is depth, and
   nothing consumes depth yet.

3. Slugs are trademark-avoidant the same way Kalshi's titles are:
   `pro-football-2026-27-passing-yards-leader`, not "nfl-...". Trusting the
   `nfl` tag rather than keyword-matching the slug is what makes discovery
   complete - a slug filter drops 62 of every 100 tagged events.

Most of the tagged catalogue is dust: 12,620 markets are tradeable but only
~3,300 carry liquidity worth quoting against, hence POLY_MIN_LIQUIDITY.
"""
import json
import time

import re

import config
import store
from core import outcomes
from venues import mapping
from venues.base import VenueClient, classify_market, mid_from


class PaginationCeiling(RuntimeError):
    """Discovery hit the end of what an endpoint will page through blindly.

    THIS MUST RAISE, NOT RETURN A SHORT LIST. A truncated catalogue is
    indistinguishable from a quiet day: markets simply stop being quoted, the
    poll loop keeps succeeding, the dead-man switch stays green, and the gap
    only shows up later as missing history nobody can re-derive. The 422 that
    hid football behind offset 2100 was exactly this shape.
    """


class PolymarketClient(VenueClient):
    name = "polymarket"

    def __init__(self, client, limiter):
        super().__init__(client, limiter)
        self._snapshot = []        # last events payload
        self._snapshot_ts = 0.0

    # ---- discovery ---------------------------------------------------------

    async def _events(self) -> list[dict]:
        """Every open NFL event, tag-filtered and paged to the end.

        Cached for POLY_SNAPSHOT_TTL so the four polling tiers of one loop pass
        share a single fetch instead of paging the catalogue four times.
        """
        if self._snapshot and time.time() - self._snapshot_ts < config.POLY_SNAPSHOT_TTL:
            return self._snapshot

        events, offset = [], 0
        while True:
            # HARD GUARD. Blind offset pagination is how discovery found zero
            # NFL markets for weeks: gamma 422s past offset 2100 and the walk
            # never reached football. Refusing at the ceiling - rather than
            # discovering it by getting a 422 - is what keeps a future ceiling
            # from becoming a silent truncation.
            if offset > config.POLY_MAX_OFFSET:
                raise PaginationCeiling(
                    f"/events reached offset {offset} > POLY_MAX_OFFSET "
                    f"{config.POLY_MAX_OFFSET} with {len(events)} events "
                    f"collected. gamma refuses blind pagination past ~2100 and "
                    f"names /markets/keyset for deeper paging. Do NOT raise the "
                    f"cap - use keyset, or narrow the tag filter.")
            payload = await self.get_json(
                f"{config.POLY_GAMMA}/events",
                params={"limit": config.POLY_PAGE_LIMIT, "offset": offset,
                        "closed": "false", "tag_slug": config.POLY_TAG_SLUG},
                archive_as="events")
            batch = payload if isinstance(payload, list) else (payload or {}).get("data", [])
            if not batch:
                break
            events.extend(batch)
            offset += len(batch)

        self._snapshot = events
        self._snapshot_ts = time.time()
        return events

    def _tradeable(self, events: list[dict]):
        """(event, market) pairs worth carrying, newest liquidity filter applied."""
        for e in events:
            for m in e.get("markets") or []:
                if not (m.get("active") and not m.get("closed")
                        and m.get("enableOrderBook") and m.get("acceptingOrders")):
                    continue
                if _f(m.get("liquidityNum")) < config.POLY_MIN_LIQUIDITY:
                    continue
                if not _token_ids(m):
                    continue
                yield e, m

    async def list_markets(self) -> list[dict]:
        out = []
        for e, m in self._tradeable(await self._events()):
            out.append({
                "venue": self.name,
                "market_id": str(_token_ids(m)[0]),      # YES token
                "event_id": e.get("slug") or e.get("ticker"),
                "market_type": classify_market(m.get("question", ""),
                                               e.get("slug", "")),
                "subject": m.get("groupItemTitle") or None,
                "line": None,
                "title": m.get("question"),
                "open_ts": _iso(m.get("startDate")),
                # gameStartTime is the kickoff where gamma knows one; endDate is
                # resolution, which for a game market is hours later. The
                # polling tier keys off close_ts, so kickoff is the right value.
                "close_ts": _iso(m.get("gameStartTime")) or _iso(m.get("endDate")),
                "settle_ts": _iso(m.get("endDate")),
                "result": None,
            })
        return out

    # ---- quotes ------------------------------------------------------------

    async def fetch_quotes(self, markets: list[dict]) -> list[dict]:
        """Top-of-book for the requested markets, batched through the CLOB.

        Prices come back as {token_id: {"BUY": bid, "SELL": ask}} - BUY is the
        best bid, SELL the best offer, both already probabilities. 500 entries
        per request is the ceiling (600 returns "Payload exceeds the limit"),
        and each market costs two entries, hence POLY_PRICE_BATCH markets.

        On a CLOB failure this falls back to the inline gamma prices from the
        last discovery rather than returning nothing: a stale quote that is
        labelled by its ts beats a hole in the series.
        """
        if not markets:
            return []
        rows, now = [], time.time()
        for i in range(0, len(markets), config.POLY_PRICE_BATCH):
            chunk = markets[i:i + config.POLY_PRICE_BATCH]
            body = []
            for m in chunk:
                body.append({"token_id": m["market_id"], "side": "BUY"})
                body.append({"token_id": m["market_id"], "side": "SELL"})
            try:
                prices = await self.post_json(f"{config.POLY_CLOB}/prices", body,
                                              archive_as="prices") or {}
            except Exception:
                prices = {}
                rows.extend(await self._fallback_quotes(chunk, now))
                continue
            for m in chunk:
                px = prices.get(m["market_id"]) or {}
                bid, ask = _f_or_none(px.get("BUY")), _f_or_none(px.get("SELL"))
                rows.append(self._row(m, bid, ask, now, "polymarket/prices"))
        return rows

    async def _fallback_quotes(self, markets, now) -> list[dict]:
        """Inline gamma prices from the cached catalogue. Same numbers, older."""
        inline = {}
        for _, m in self._tradeable(self._snapshot):
            inline[str(_token_ids(m)[0])] = (_f_or_none(m.get("bestBid")),
                                             _f_or_none(m.get("bestAsk")))
        out = []
        for m in markets:
            bid, ask = inline.get(m["market_id"], (None, None))
            out.append(self._row(m, bid, ask, now, "polymarket/events"))
        return out

    def _row(self, m, bid, ask, now, raw_ref) -> dict:
        return {
            "ts": now, "sport": "nfl", "venue": self.name,
            "event_id": m.get("event_id"), "market_id": m["market_id"],
            "market_type": m.get("market_type"), "subject": m.get("subject"),
            "line": m.get("line"), "side": "yes",
            "best_bid": bid, "best_ask": ask, "mid": mid_from(bid, ask),
            "last": None, "volume": None, "open_interest": None,
            "raw_ref": raw_ref,
        }


def _token_ids(m):
    raw = m.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)          # gamma returns this JSON-encoded
        except json.JSONDecodeError:
            return []
    return raw if isinstance(raw, list) else []


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _f_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(s):
    if not s:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---- outcome mapping (brief 003) -------------------------------------------

# nfl-ne-sea-2026-09-10-player-props  ->  away, home, date
_EVENT = re.compile(r"^nfl-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-"
                    r"(?P<date>\d{4}-\d{2}-\d{2})(?P<tail>-.*)?$")

_OU = re.compile(r"^(?P<name>.+?):\s*(?P<stat>.+?)\s*O/U\s*(?P<line>[\d.]+)$", re.I)
_ANYTIME = re.compile(r"^(?P<name>.+?):\s*Anytime\s+Touchdown\s*$", re.I)
_NPLUS = re.compile(r"^(?P<name>.+?):\s*(?P<n>\d+)\+\s*(?P<stat>.+?)\s*$", re.I)
_SPREAD = re.compile(r"^Spread:\s*(?P<team>.+?)\s*\((?P<line>[-+][\d.]+)\)\s*$", re.I)
_TEAM_TOTAL = re.compile(r"^(?P<team>.+?)\s+Team Total:\s*O/U\s*(?P<line>[\d.]+)$", re.I)
_GAME_TOTAL = re.compile(
    r"^(?:(?:Total|Game Total)|.+?\s+vs\.?\s+.+?):\s*O/U\s*(?P<line>[\d.]+)$", re.I)
_MONEYLINE = re.compile(r"^Moneyline:\s*(?P<team>.+?)\s*$", re.I)

# Segment markets - quarters and halves. We have no outcome type for a partial
# game, and silently mapping "1Q Spread" onto the full-game spread would be a
# wrong number rather than a missing one.
_SEGMENT = re.compile(r"\b(1Q|2Q|3Q|4Q|1H|2H|first half|second half|quarter)\b", re.I)


def _event_game(event_id: str):
    m = _EVENT.match((event_id or "").lower())
    if not m:
        raise mapping.Unresolved(f"event {event_id!r} is not a game slug")
    return mapping.game_for(m.group("a"), m.group("b"), m.group("date"))


def map_market(row: dict):
    """One logged Polymarket market -> (outcome_id, method, confidence)."""
    title = (row.get("title") or "").strip()
    event_id = row.get("event_id") or ""

    if not title:
        raise mapping.Unresolved("no title")
    if _SEGMENT.search(title):
        raise mapping.Unresolved("segment market (quarter/half), no outcome type")
    if not _EVENT.match(event_id.lower()):
        # Season-long leader and futures markets carry a non-game slug. They are
        # real claims, just not week-keyed ones, and the outcomes table wants a
        # week. Recorded, not dropped.
        raise mapping.Unresolved("season-long or non-game event slug")

    season, week, game_id = _event_game(event_id)

    m = _GAME_TOTAL.match(title)
    if m:
        o = outcomes.Outcome(outcomes.Sport.NFL, season, week,
                             outcomes.MarketType.TOTAL, game_id, None,
                             float(m.group("line")), outcomes.Side.OVER, game_id)
        return store.upsert_outcome(o, game_id), "poly:game_total", 0.95

    m = _TEAM_TOTAL.match(title)
    if m:
        team = mapping.team_abbr(m.group("team"))
        if not team:
            raise mapping.Unresolved(f"unknown team {m.group('team')!r}")
        o = outcomes.Outcome(outcomes.Sport.NFL, season, week,
                             outcomes.MarketType.TOTAL, team, None,
                             float(m.group("line")), outcomes.Side.OVER, game_id)
        return store.upsert_outcome(o, game_id), "poly:team_total", 0.95

    m = _OU.match(title)
    if m:
        stat = mapping.stat_from_words(m.group("stat"))
        if stat is None:
            raise mapping.Unresolved(f"unknown stat {m.group('stat')!r}")
        gsis, how, conf = mapping.resolve_player(m.group("name"), season)
        o = outcomes.player_prop(season, week, gsis, stat,
                                 float(m.group("line")), outcomes.Side.OVER,
                                 event_id=game_id)
        return store.upsert_outcome(o, game_id), f"poly:ou+{how}", conf

    m = _ANYTIME.match(title)
    if m:
        gsis, how, conf = mapping.resolve_player(m.group("name"), season)
        o = outcomes.player_prop(season, week, gsis, outcomes.Stat.ANYTIME_TD,
                                 0.5, outcomes.Side.OVER, event_id=game_id)
        return store.upsert_outcome(o, game_id), f"poly:anytime+{how}", conf

    m = _NPLUS.match(title)
    if m:
        stat = mapping.stat_from_words(m.group("stat"))
        if stat is None:
            raise mapping.Unresolved(f"unknown stat {m.group('stat')!r}")
        gsis, how, conf = mapping.resolve_player(m.group("name"), season)
        # "2+ Touchdowns" pays on >= 2, i.e. the over on 1.5 - the same
        # threshold-to-line conversion Kalshi needs.
        o = outcomes.player_prop(season, week, gsis, stat,
                                 float(m.group("n")) - 0.5, outcomes.Side.OVER,
                                 event_id=game_id)
        return store.upsert_outcome(o, game_id), f"poly:nplus+{how}", conf

    m = _SPREAD.match(title)
    if m:
        team = mapping.team_abbr(m.group("team"))
        if not team:
            raise mapping.Unresolved(f"unknown team {m.group('team')!r}")
        o = outcomes.Outcome(outcomes.Sport.NFL, season, week,
                             outcomes.MarketType.SPREAD, team, None,
                             float(m.group("line")), outcomes.Side.OVER, game_id)
        return store.upsert_outcome(o, game_id), "poly:spread", 0.95

    m = _GAME_TOTAL.match(title)
    if m:
        o = outcomes.Outcome(outcomes.Sport.NFL, season, week,
                             outcomes.MarketType.TOTAL, game_id, None,
                             float(m.group("line")), outcomes.Side.OVER, game_id)
        return store.upsert_outcome(o, game_id), "poly:game_total", 0.95

    m = _MONEYLINE.match(title)
    if m:
        team = mapping.team_abbr(m.group("team"))
        if not team:
            raise mapping.Unresolved(f"unknown team {m.group('team')!r}")
        o = outcomes.Outcome(outcomes.Sport.NFL, season, week,
                             outcomes.MarketType.MONEYLINE, team, None, None,
                             outcomes.Side.YES, game_id)
        return store.upsert_outcome(o, game_id), "poly:moneyline", 1.0

    raise mapping.Unresolved(f"title shape not recognised: {title[:70]!r}")
