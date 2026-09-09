"""Run this FIRST. It answers "does the API work and what shape is it?"

Hits each venue's discovery and orderbook endpoints once, writes the raw
payloads under data/probe/, and prints the top-level keys plus one sample
record. Every field name in venues/*.py was written from documentation, not
from an observed response - this is how you find the mismatches in two minutes
instead of discovering them at 1pm Sunday.

    python probe.py
"""
import asyncio
import json
import os

import httpx

import config
from venues.base import RateLimiter

OUT = "data/probe"


def dump(name, payload):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.json"), "w") as f:
        json.dump(payload, f, indent=2)


def describe(name, payload, sample_path=None):
    print(f"\n--- {name} ---")
    if isinstance(payload, dict):
        print("top-level keys:", list(payload.keys())[:20])
    elif isinstance(payload, list):
        print(f"list of {len(payload)}")
    sample = payload
    if sample_path:
        for k in sample_path:
            if isinstance(sample, dict):
                sample = sample.get(k)
            elif isinstance(sample, list) and sample:
                sample = sample[0]
            if sample is None:
                break
    if isinstance(sample, list) and sample:
        sample = sample[0]
    if isinstance(sample, dict):
        print("sample record keys:", list(sample.keys()))
        print(json.dumps(sample, indent=2)[:1200])


async def main():
    limiter = RateLimiter(config.MAX_RPS)
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT,
                                 headers={"User-Agent": config.USER_AGENT},
                                 follow_redirects=True) as http:

        async def get(url, params=None):
            await limiter.wait()
            r = await http.get(url, params=params)
            print(f"  {r.status_code}  {r.url}")
            r.raise_for_status()
            return r.json()

        print("== KALSHI ==")
        try:
            mk = await get(f"{config.KALSHI_BASE}/markets",
                           {"limit": 20, "status": "open"})
            dump("kalshi_markets", mk)
            describe("kalshi /markets", mk, ["markets"])

            tickers = [m["ticker"] for m in mk.get("markets", [])[:10] if m.get("ticker")]
            if tickers:
                ob = await get(f"{config.KALSHI_BASE}/markets/orderbooks",
                               {"tickers": ",".join(tickers)})
                dump("kalshi_orderbooks", ob)
                describe("kalshi /markets/orderbooks", ob)
                print("\n>>> CHECK: is the ladder [[price_cents, size], ...] under "
                      "'yes'/'no'? venues/kalshi.py::_top assumes that.")
        except Exception as e:
            print("  KALSHI FAILED:", type(e).__name__, e)
            print("  -> if this is a 404, the base URL moved. Try the host shown "
                  "in Kalshi's current API docs and set KALSHI_BASE.")

        print("\n== POLYMARKET ==")
        try:
            gm = await get(f"{config.POLY_GAMMA}/markets",
                           {"limit": 20, "closed": "false"})
            dump("poly_markets", gm)
            describe("gamma /markets", gm)

            batch = gm if isinstance(gm, list) else gm.get("data", [])
            tid = None
            for m in batch:
                raw = m.get("clobTokenIds")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except json.JSONDecodeError:
                        raw = None
                if raw:
                    tid = raw[0]
                    break
            if tid:
                bk = await get(f"{config.POLY_CLOB}/book", {"token_id": tid})
                dump("poly_book", bk)
                describe("clob /book", bk)
                print("\n>>> CHECK: are bids/asks dicts with a 'price' key? "
                      "venues/polymarket.py::_best assumes that.")
        except Exception as e:
            print("  POLYMARKET FAILED:", type(e).__name__, e)

    print(f"\nRaw payloads written to {OUT}/ - diff these against the field names "
          f"in venues/*.py before starting the logger.")


if __name__ == "__main__":
    asyncio.run(main())
