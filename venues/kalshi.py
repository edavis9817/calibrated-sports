"""Kalshi adapter.

Public endpoints used (no auth needed for market data):
  GET /series?category=Sports      - the ONLY way to reach sports; cached
  GET /markets?series_ticker=...   - market catalogue for one series
  GET /markets/orderbooks          - batched books, <=100 tickers per call

Auth (RSA-PSS over `timestamp + METHOD + path`) is only needed for /portfolio/*
and the /historical/* tier. Kalshi split live vs historical data on 2026-02-19,
so anything aged out is unreachable without a key - which is the whole reason
to start logging now rather than backfilling later.

VERIFIED AGAINST THE LIVE API 2026-09-09. Four things that are not guessable
and that each fail silently if you get them wrong:

1. PRICES ARE DOLLARS, NOT CENTS. The fields are `yes_bid_dollars`,
   `yes_ask_dollars`, and the book is `orderbook_fp.yes_dollars` /
   `no_dollars`, all as decimal STRINGS: "0.1800". A contract settles at $1, so
   the dollar price IS the probability - float() it and stop. There is no
   `yes_bid` field and no cents anywhere; dividing by 100 puts every price a
   factor of 100 too low, which reads as a market nobody trades.

2. `/markets/orderbooks?tickers=a,b,c` COMMA-JOINED RETURNS HTTP 200 WITH ONE
   EMPTY BOOK whose ticker is the joined string. It does not split on commas.
   Repeated params - tickers=a&tickers=b - return all the books. The failure
   mode of the comma form is null mids that read as "no liquidity" for weeks.

3. Discovery must go /series -> filter -> per-series /markets. Walking
   /markets by cursor never reaches sports: ~13,900 series deep, the page cap
   trips first. /series?category=Sports halves the payload (5.7MB vs 16.7MB)
   but still leaks 39 non-Sports rows, so the category guard stays client-side.

4. TITLES SAY "PRO FOOTBALL", NOT "NFL" - trademark avoidance. Match both, and
   see is_football_series() for why matching "NFL" naively is a trap.

Threshold props: Kalshi lists "6+ receptions" as its own market with
`floor_strike` 5.5, all thresholds under one series (KXNFLREC covers 2+ through
6+ for every player). Half-integer strikes mean no push handling is needed here.
"""
import re
import time

import config
from venues.base import VenueClient, mid_from

# THE TRAP: "inflation" contains "nfl", so `"NFL" in title.upper()` matches
# every CPI market on the exchange - and "NFLX" makes it match Netflix too.
# Guard both ends: `(?<!I)` drops INFLATION, `(?!X)` drops NFLX. This is a
# ticker-shaped match, not a word match, because KXNFLGAME has no boundaries.
NFL_RE = re.compile(r"(?<!I)NFL(?!X)")
# Kalshi files college football under the same Sports category, and
# KXNCAAFCONFLEAVE contains "NFL" by accident (CO-NFL-EAVE).
COLLEGE_RE = re.compile(r"NCAA|COLLEGE")


def is_football_series(ticker: str, title: str = "", category: str = "") -> bool:
    """Is this series an NFL market?

    Guards on category AND a ticker/title-level match, because either alone is
    wrong: category "Sports" alone lets college football in, and an "NFL"
    substring alone lets in every inflation market, Netflix, and NCAAF.
    """
    t = (ticker or "").upper()
    ti = (title or "").upper()
    if (category or "").strip().lower() != "sports":
        return False
    if COLLEGE_RE.search(t) or COLLEGE_RE.search(ti):
        return False
    return bool(NFL_RE.search(t) or NFL_RE.search(ti) or "PRO FOOTBALL" in ti)


def _match(ticker: str, pattern: str) -> bool:
    """Allowlist patterns: exact, or a prefix when they end in '*'."""
    return ticker.startswith(pattern[:-1]) if pattern.endswith("*") else ticker == pattern


class KalshiClient(VenueClient):
    name = "kalshi"

    def __init__(self, client, limiter):
        super().__init__(client, limiter)
        self._series = []          # cached football series catalogue
        self._series_ts = 0.0

    # ---- discovery ---------------------------------------------------------

    async def _football_series(self) -> list[dict]:
        """The football series catalogue, cached for KALSHI_SERIES_TTL.

        5.7MB per fetch and it changes on the order of days; refetching it every
        discovery cycle would be ~56k calls/day for a list that barely moves.
        """
        if self._series and time.time() - self._series_ts < config.KALSHI_SERIES_TTL:
            return self._series
        payload = await self.get_json(f"{config.KALSHI_BASE}/series",
                                      params={"category": "Sports"},
                                      archive_as="series")
        rows = payload.get("series", []) if isinstance(payload, dict) else (payload or [])
        self._series = [s for s in rows
                        if is_football_series(s.get("ticker", ""), s.get("title", ""),
                                              s.get("category", ""))]
        self._series_ts = time.time()
        return self._series

    async def tracked_series(self) -> list[tuple[str, str, str]]:
        """(ticker, market_type, horizon) for every series we actually poll."""
        football = await self._football_series()
        if config.KALSHI_ALLOWLIST_OFF:
            return [(s["ticker"], "unknown", "season") for s in football if s.get("ticker")]
        out = []
        for s in football:
            tk = s.get("ticker") or ""
            for pattern, mtype, horizon in config.KALSHI_SERIES_ALLOW:
                if _match(tk, pattern):
                    out.append((tk, mtype, horizon))
                    break
        return out

    async def list_markets(self) -> list[dict]:
        out = []
        now = time.time()
        horizon = config.KALSHI_CLOSE_HORIZON_DAYS * 86400
        for ticker, mtype, hz in await self.tracked_series():
            params = {"series_ticker": ticker, "status": "open", "limit": 200}
            if hz == "week":
                # Server-side window. Season futures skip it - they close in
                # February and the window would silently drop every one of them.
                params["min_close_ts"] = int(now)
                params["max_close_ts"] = int(now + horizon)
            cursor = None
            for _ in range(30):                  # page cap; a series is bounded
                p = dict(params)
                if cursor:
                    p["cursor"] = cursor
                payload = await self.get_json(f"{config.KALSHI_BASE}/markets",
                                              params=p, archive_as="markets")
                markets = payload.get("markets", []) if isinstance(payload, dict) else []
                for m in markets:
                    tk = m.get("ticker")
                    if not tk:
                        continue
                    out.append({
                        "venue": self.name,
                        "market_id": tk,
                        "event_id": m.get("event_ticker"),
                        # Typed off the series, not the title. Titles get
                        # reworded; the series a market hangs off does not, and
                        # this is what routes the market to a polling tier.
                        "market_type": mtype,
                        "subject": m.get("yes_sub_title") or m.get("no_sub_title"),
                        "line": _f(m.get("floor_strike"))
                                if m.get("floor_strike") is not None
                                else _f(m.get("cap_strike")),
                        "title": m.get("title") or "",
                        "open_ts": _iso(m.get("open_time")),
                        "close_ts": _iso(m.get("close_time")),
                        "settle_ts": _iso(m.get("expiration_time")),
                        "result": m.get("result") or None,
                        # carried into the quote row; see fetch_quotes
                        "_volume": _f(m.get("volume_fp")),
                        "_open_interest": _f(m.get("open_interest_fp")),
                    })
                cursor = payload.get("cursor") if isinstance(payload, dict) else None
                if not cursor or not markets:
                    break
        return out

    # ---- quotes ------------------------------------------------------------

    async def fetch_quotes(self, markets: list[dict]) -> list[dict]:
        """Batched orderbook pull - up to 100 tickers per request.

        The batching is the single most important efficiency decision in the
        logger; one market at a time would blow the rate limit before it had
        covered a Sunday slate. It only works with REPEATED `tickers` params.
        """
        rows, now = [], time.time()
        meta = {m["market_id"]: m for m in markets}
        tickers = list(meta)

        for i in range(0, len(tickers), config.KALSHI_ORDERBOOK_BATCH):
            chunk = tickers[i:i + config.KALSHI_ORDERBOOK_BATCH]
            payload = await self.get_json(
                f"{config.KALSHI_BASE}/markets/orderbooks",
                # NOT ",".join(chunk) - see note 2 in the module docstring.
                params=[("tickers", t) for t in chunk], archive_as="orderbooks")
            books = (payload or {}).get("orderbooks") or []

            for b in books:
                tk = b.get("ticker")
                m = meta.get(tk)
                if m is None:
                    continue
                ob = b.get("orderbook_fp") or {}
                # Two one-sided ladders: bids to buy YES, bids to buy NO. The
                # best ask on YES is implied by the best NO bid - a resting
                # offer to buy NO at 0.81 is an offer to sell YES at 0.19.
                bid = _top(ob.get("yes_dollars"))
                no_bid = _top(ob.get("no_dollars"))
                ask = round(1.0 - no_bid, 4) if no_bid is not None else None
                rows.append({
                    "ts": now, "sport": "nfl", "venue": self.name,
                    "event_id": m.get("event_id"), "market_id": tk,
                    "market_type": m.get("market_type"), "subject": m.get("subject"),
                    "line": m.get("line"), "side": "yes",
                    "best_bid": bid, "best_ask": ask, "mid": mid_from(bid, ask),
                    "last": None,
                    # As of the last discovery pass, not of ts - the orderbook
                    # response carries no volume. Fine for liquidity screening,
                    # not a time series.
                    "volume": m.get("_volume"),
                    "open_interest": m.get("_open_interest"),
                    "raw_ref": "kalshi/orderbooks",
                })
        return rows


def _top(ladder):
    """Best (highest) price from a Kalshi ladder: [["0.1800", "571.00"], ...].

    Prices are dollar strings and a contract settles at $1, so this is already
    a probability. max() over the raw strings would compare lexicographically -
    "0.9" beats "0.1000" - so parse before comparing.
    """
    best = None
    for lvl in ladder or []:
        try:
            p = float(lvl[0])
        except (TypeError, ValueError, IndexError):
            continue
        if best is None or p > best:
            best = p
    return best


def _f(v):
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
