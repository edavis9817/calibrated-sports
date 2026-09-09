"""Shared HTTP client: rate limiting, blind backoff, raw archiving.

Every venue adapter returns the same normalized quote dict so the logger loop
and every downstream analysis stay venue-agnostic. When you add a sportsbook
odds API, it implements this same interface and nothing else changes.
"""
import asyncio
import random
import time
from typing import Any

import httpx

import config
import store


class RateLimiter:
    """Simple token bucket. Kalshi returns no Retry-After on 429, so the only
    safe strategy is to stay conservatively under the limit and back off blind."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval


class VenueClient:
    name = "base"

    def __init__(self, client: httpx.AsyncClient, limiter: RateLimiter):
        self.http = client
        self.limiter = limiter

    async def get_json(self, url: str, params: Any = None,
                       archive_as: str | None = None, max_retries: int = 5,
                       soft_status: tuple = ()) -> Any:
        """GET and parse JSON, archiving the verbatim payload first.

        `params` is handed to httpx unchanged, so a list of (key, value) pairs
        works and is sometimes REQUIRED - see the repeated-`tickers` note in
        venues/kalshi.py.

        `soft_status` lists status codes that return None instead of raising.
        Use it for a limit the API imposes rather than an error we caused, e.g.
        gamma's 422 past offset 2100: hitting the end of what an endpoint will
        page through must truncate discovery, never kill it.
        """
        delay = config.BACKOFF_BASE
        for attempt in range(max_retries):
            await self.limiter.wait()
            try:
                r = await self.http.get(url, params=params)
                if r.status_code in soft_status:
                    return None
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"status {r.status_code}",
                                                request=r.request, response=r)
                r.raise_for_status()
                payload = r.json()
                if archive_as:
                    store.archive_raw(self.name, archive_as, payload)
                return payload
            except Exception:
                if attempt == max_retries - 1:
                    raise
                # jitter matters: without it, every market retries in lockstep
                await asyncio.sleep(min(delay, config.BACKOFF_MAX) * (1 + random.random()))
                delay *= 2
        raise RuntimeError("unreachable")

    async def post_json(self, url: str, body: Any, archive_as: str | None = None,
                        max_retries: int = 5) -> Any:
        """POST and parse JSON. Same archiving and backoff contract as get_json.

        Needed only where a venue puts a batched READ behind POST because the
        id list is too long for a query string - Polymarket's /prices is the
        case in hand. Nothing here mutates anything on a venue.
        """
        delay = config.BACKOFF_BASE
        for attempt in range(max_retries):
            await self.limiter.wait()
            try:
                r = await self.http.post(url, json=body)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"status {r.status_code}",
                                                request=r.request, response=r)
                r.raise_for_status()
                payload = r.json()
                if archive_as:
                    store.archive_raw(self.name, archive_as, payload)
                return payload
            except Exception:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(min(delay, config.BACKOFF_MAX) * (1 + random.random()))
                delay *= 2
        raise RuntimeError("unreachable")

    # ---- interface every venue adapter implements ----

    async def list_markets(self) -> list[dict]:
        """Return normalized market metadata dicts (see store.markets columns)."""
        raise NotImplementedError

    async def fetch_quotes(self, markets: list[dict]) -> list[dict]:
        """Return normalized quote dicts (see store.quotes columns)."""
        raise NotImplementedError


def mid_from(bid, ask):
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def classify_market(title: str, ticker: str = "") -> str:
    """Crude market-type classifier off the human-readable title.

    Deliberately dumb for v1. Once probe.py shows you the real ticker grammar
    for each venue, replace this with structural parsing of the ticker - titles
    get reworded, tickers don't.
    """
    t = (title or "").lower()
    k = (ticker or "").lower()
    if any(w in t for w in ("super bowl", "division", "conference", "win total",
                            "season wins", "mvp", "make the playoffs")):
        return "future"
    if "spread" in t or "cover" in t or "by more than" in t:
        return "spread"
    if any(w in t for w in ("total points", "combined", "over/under", "o/u")):
        return "total"
    if any(w in t for w in ("receiving yards", "rushing yards", "passing yards",
                            "receptions", "touchdown", "completions", "attempts",
                            "sacks", "interceptions")):
        return "prop"
    if "win" in t or "beat" in t or "-vs-" in k:
        return "moneyline"
    return "unknown"
