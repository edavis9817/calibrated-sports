"""Credential + endpoint check. Run this before deploying anything.

Answers four questions in about 20 seconds:
  1. Does each endpoint answer at all?
  2. Do the field names match what the adapters assume?
  3. How many Odds API credits do I actually have, and what does one call cost?
  4. Are any NFL markets currently listed on the exchanges?

    python verify.py
"""
import asyncio
import json
import os
from collections import Counter

import httpx

import config
from venues.base import RateLimiter
from venues.kalshi import KalshiClient
from venues.polymarket import PolymarketClient

OK, BAD, WARN = "  [ok] ", "  [FAIL] ", "  [warn] "


async def main():
    limiter = RateLimiter(config.MAX_RPS)
    results = {}
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT, follow_redirects=True,
                                 headers={"User-Agent": config.USER_AGENT}) as http:

        # ---------------- The Odds API ----------------
        print("\nTHE ODDS API")
        if not config.ODDS_API_KEY:
            print(BAD + "ODDS_API_KEY not set (export it or put it in .env)")
        else:
            try:
                await limiter.wait()
                r = await http.get(f"{config.ODDS_BASE}/sports",
                                   params={"apiKey": config.ODDS_API_KEY})
                r.raise_for_status()
                rem = r.headers.get("x-requests-remaining")
                used = r.headers.get("x-requests-used")
                print(OK + f"key valid. credits remaining={rem} used={used}")
                results["odds_remaining"] = rem

                await limiter.wait()
                r2 = await http.get(f"{config.ODDS_BASE}/sports/{config.ODDS_SPORT}/events",
                                    params={"apiKey": config.ODDS_API_KEY})
                r2.raise_for_status()
                evs = r2.json()
                print(OK + f"{len(evs)} NFL events listed "
                           f"(events endpoint is free: remaining still "
                           f"{r2.headers.get('x-requests-remaining')})")
                if evs:
                    e = evs[0]
                    print(f"       next: {e.get('away_team')} @ {e.get('home_team')}"
                          f"  {e.get('commence_time')}")
                    _dump("odds_events", evs)

                    # measure the true cost of ONE per-event prop call
                    before = int(r2.headers.get("x-requests-remaining", 0))
                    await limiter.wait()
                    r3 = await http.get(
                        f"{config.ODDS_BASE}/sports/{config.ODDS_SPORT}/events/{e['id']}/odds",
                        params={"apiKey": config.ODDS_API_KEY,
                                "regions": config.ODDS_REGIONS,
                                "markets": config.ODDS_PROP_MARKETS,
                                "oddsFormat": "american"})
                    if r3.status_code == 422:
                        print(WARN + "prop markets rejected (422) - your tier may not "
                                     "include player props, or a market key is wrong")
                        print("       requested: " + config.ODDS_PROP_MARKETS)
                    else:
                        r3.raise_for_status()
                        after = int(r3.headers.get("x-requests-remaining", 0))
                        body = r3.json()
                        books = body.get("bookmakers", [])
                        print(OK + f"props returned from {len(books)} bookmakers; "
                                   f"ONE event cost {before - after} credits")
                        print(f"       => full slate (13 games) x 5 snapshots ~= "
                              f"{(before-after)*13*5} credits/week")
                        names = [b.get("key") for b in books]
                        print(f"       books: {names[:12]}")
                        if config.ODDS_SHARP_BOOK not in names:
                            print(WARN + f"'{config.ODDS_SHARP_BOOK}' not among them - "
                                         "pick another sharp reference for CLV")
                        _dump("odds_props", body)
            except httpx.HTTPStatusError as e:
                print(BAD + f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            except Exception as e:
                print(BAD + f"{type(e).__name__}: {e}")

        # ---------------- Kalshi ----------------
        # Exercised through the real adapter, not a hand-rolled request, so
        # this can never pass while the logger's own discovery is broken.
        print("\nKALSHI")
        try:
            kal = KalshiClient(http, RateLimiter(config.rps_for("kalshi")))
            series = await kal.tracked_series()
            allf = await kal._football_series()
            print(OK + f"{len(allf)} football series in the catalogue, "
                       f"{len(series)} tracked by the allowlist")
            _dump("kalshi_series_tracked", series)

            markets = await kal.list_markets()
            print((OK if markets else BAD) + f"{len(markets)} NFL markets discovered")
            results["kalshi_markets"] = len(markets)
            if markets:
                _dump("kalshi_markets", markets[:200])
                by_type = Counter(m["market_type"] for m in markets)
                print(f"       by type: {dict(by_type)}")
                ex = next((m for m in markets if m["market_type"] == "prop"), markets[0])
                print(f"       e.g. {ex['market_id']}  {str(ex['title'])[:48]!r} "
                      f"line={ex['line']}")

                rows = await kal.fetch_quotes(markets[:100])
                priced = [r for r in rows if r["mid"] is not None]
                print((OK if priced else BAD) +
                      f"{len(rows)} quotes from a 100-ticker batch, "
                      f"{len(priced)} with a non-null mid")
                results["kalshi_mids"] = len(priced)
                for r in priced[:3]:
                    print(f"       {r['market_id']:<38} bid={r['best_bid']} "
                          f"ask={r['best_ask']} mid={r['mid']}")
                if priced and max(r["mid"] for r in priced) > 1.0:
                    print(BAD + "a mid exceeded 1.0 - prices are DOLLARS on this "
                                "API and must never be divided by 100")
        except httpx.HTTPStatusError as e:
            print(BAD + f"HTTP {e.response.status_code} - base URL may have moved. "
                        f"Set KALSHI_BASE.")
        except Exception as e:
            print(BAD + f"{type(e).__name__}: {e}")

        # ---------------- Polymarket ----------------
        print("\nPOLYMARKET")
        try:
            poly = PolymarketClient(http, RateLimiter(config.rps_for("polymarket")))
            events = await poly._events()
            print(OK + f"{len(events)} open events via tag_slug="
                       f"{config.POLY_TAG_SLUG} (blind /markets pagination 422s "
                       f"at offset 2100; this pages to the end)")

            markets = await poly.list_markets()
            nested = sum(len(e.get("markets") or []) for e in events)
            print((OK if markets else BAD) +
                  f"{len(markets)} NFL markets discovered "
                  f"(of {nested} nested; liquidity floor "
                  f"{config.POLY_MIN_LIQUIDITY:g})")
            results["poly_markets"] = len(markets)
            if markets:
                _dump("poly_markets", markets[:200])
                rows = await poly.fetch_quotes(markets)
                priced = [r for r in rows if r["mid"] is not None]
                print((OK if priced else BAD) +
                      f"{len(rows)} quotes, {len(priced)} with a non-null mid "
                      f"(batched CLOB top-of-book, "
                      f"{config.POLY_PRICE_BATCH} markets per call)")
                results["poly_mids"] = len(priced)
                for r in priced[:3]:
                    print(f"       {str(r['subject'] or r['market_id'])[:34]:<34} "
                          f"bid={r['best_bid']} ask={r['best_ask']} mid={r['mid']}")
        except Exception as e:
            print(BAD + f"{type(e).__name__}: {e}")

    # ---------------- verdict ----------------
    print("\nACCEPTANCE")
    for label, key in (("kalshi NFL markets", "kalshi_markets"),
                       ("kalshi non-null mids", "kalshi_mids"),
                       ("polymarket NFL markets", "poly_markets"),
                       ("polymarket non-null mids", "poly_mids")):
        n = results.get(key, 0)
        print((OK if n else BAD) + f"{label}: {n}")

    print("\nRaw payloads in data/probe/. Diff them against the field names in "
          "venues/*.py, fix any mismatch, then run run_logger.py.\n")


def _dump(name, payload):
    os.makedirs("data/probe", exist_ok=True)
    with open(f"data/probe/{name}.json", "w") as f:
        json.dump(payload, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
