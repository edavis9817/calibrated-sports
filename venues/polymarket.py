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
   422 anywhere. Discovery still treats a 422 as "stop paging", never as an
   error, so a future ceiling truncates the catalogue rather than killing it.

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

import config
from venues.base import VenueClient, classify_market, mid_from


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
        while offset <= config.POLY_MAX_OFFSET:
            payload = await self.get_json(
                f"{config.POLY_GAMMA}/events",
                params={"limit": config.POLY_PAGE_LIMIT, "offset": offset,
                        "closed": "false", "tag_slug": config.POLY_TAG_SLUG},
                archive_as="events",
                # A 422 here is gamma's pagination ceiling, not our bug. Keep
                # what we already have and stop; never let it raise.
                soft_status=(422,))
            if payload is None:
                break
            batch = payload if isinstance(payload, list) else payload.get("data", [])
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
