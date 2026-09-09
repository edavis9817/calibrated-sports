"""Find how Kalshi and Polymarket actually name their NFL markets.

The current filters look for the literal string "NFL" and find nothing, and
Polymarket 422s past offset ~2000. This tries several discovery strategies
against both venues and reports which ones work, so the filters can be written
from evidence instead of guesses.

    python find_nfl.py

Read-only. Costs nothing (both venues are free).
"""
import json
import os
import sys

import httpx

KALSHI = os.getenv("KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")
GAMMA = os.getenv("POLY_GAMMA", "https://gamma-api.polymarket.com")

# Words that would appear in a football market but almost nothing else.
FOOTBALL = ["nfl", "patriots", "seahawks", "chiefs", "eagles", "cowboys",
            "49ers", "packers", "bills", "ravens", "touchdown", "receptions",
            "rushing", "passing yards", "quarterback", "super bowl"]
OUT = "data/probe"


def looks_football(*parts) -> bool:
    blob = " ".join(str(p or "") for p in parts).lower()
    return any(w in blob for w in FOOTBALL)


def dump(name, payload):
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/{name}.json", "w") as f:
        json.dump(payload, f, indent=2)
    print(f"      (saved {OUT}/{name}.json)")


def get(client, url, params=None, label=""):
    try:
        r = client.get(url, params=params)
        if r.status_code != 200:
            print(f"   {r.status_code}  {label or url}")
            return None
        return r.json()
    except Exception as e:
        print(f"   ERR  {label or url}: {type(e).__name__}")
        return None


def kalshi(c):
    print("\n" + "=" * 70)
    print("KALSHI")
    print("=" * 70)

    # --- 1. series catalogue. Kalshi groups markets under series tickers;
    #        finding the football series is far cheaper than walking markets.
    print("\n1. series catalogue")
    for params in ({}, {"category": "Sports"}, {"category": "Sports and Entertainment"}):
        d = get(c, f"{KALSHI}/series", params, f"/series {params}")
        if not d:
            continue
        series = d.get("series") or (d if isinstance(d, list) else [])
        if not series:
            continue
        hits = [s for s in series
                if looks_football(s.get("ticker"), s.get("title"), s.get("category"))]
        print(f"   {len(series)} series, {len(hits)} look like football")
        for s in hits[:20]:
            print(f"      {s.get('ticker'):<22} {str(s.get('title'))[:50]}"
                  f"   [{s.get('category')}]")
        if hits:
            dump("kalshi_series_football", hits)
            return [s.get("ticker") for s in hits]
        # show the shape so we can see what categories exist at all
        cats = sorted({str(s.get("category")) for s in series})
        print(f"   categories present: {cats[:15]}")
        break

    # --- 2. events, which carry titles even when tickers are opaque
    print("\n2. events")
    cursor, seen, hits = None, 0, []
    for _ in range(15):
        p = {"limit": 200, "status": "open"}
        if cursor:
            p["cursor"] = cursor
        d = get(c, f"{KALSHI}/events", p, "/events")
        if not d:
            break
        evs = d.get("events", [])
        seen += len(evs)
        hits += [e for e in evs
                 if looks_football(e.get("event_ticker"), e.get("title"),
                                   e.get("sub_title"), e.get("series_ticker"))]
        cursor = d.get("cursor")
        if not cursor or not evs:
            break
    print(f"   walked {seen} events, {len(hits)} look like football")
    for e in hits[:20]:
        print(f"      {str(e.get('event_ticker')):<24} {str(e.get('title'))[:55]}")
    if hits:
        dump("kalshi_events_football", hits[:100])
        return [e.get("series_ticker") for e in hits if e.get("series_ticker")]

    # --- 3. brute force the market catalogue and show what IS there
    print("\n3. market catalogue sample (what do tickers actually look like?)")
    d = get(c, f"{KALSHI}/markets", {"limit": 100, "status": "open"}, "/markets")
    if d:
        ms = d.get("markets", [])
        print(f"   {len(ms)} markets on page 1. First 15 tickers:")
        for m in ms[:15]:
            print(f"      {str(m.get('ticker')):<28} {str(m.get('title'))[:45]}")
        prefixes = sorted({str(m.get("ticker", "")).split("-")[0] for m in ms})
        print(f"\n   distinct ticker prefixes on this page: {prefixes[:25]}")
        dump("kalshi_markets_sample", ms)
    return []


def polymarket(c):
    print("\n" + "=" * 70)
    print("POLYMARKET")
    print("=" * 70)

    # --- 1. tag-filtered events. Walking /markets blind 422s past ~2000.
    print("\n1. tag-filtered lookups")
    for path, params in [
        ("/events", {"limit": 100, "closed": "false", "tag_slug": "nfl"}),
        ("/events", {"limit": 100, "closed": "false", "tag": "nfl"}),
        ("/markets", {"limit": 100, "closed": "false", "tag_slug": "nfl"}),
        ("/events", {"limit": 100, "closed": "false", "slug": "nfl"}),
    ]:
        d = get(c, f"{GAMMA}{path}", params, f"{path} {params}")
        if not d:
            continue
        rows = d if isinstance(d, list) else d.get("data", [])
        hits = [r for r in rows if looks_football(r.get("slug"), r.get("title"),
                                                  r.get("question"))]
        print(f"   {path} {list(params)[-1]}: {len(rows)} rows, {len(hits)} football")
        if hits:
            for r in hits[:12]:
                print(f"      {str(r.get('slug'))[:60]}")
            dump("poly_football", hits[:100])
            return

    # --- 2. bounded pagination. Cap the offset so we never hit the 422.
    print("\n2. bounded pagination (offset capped at 2000)")
    found, offset = [], 0
    while offset < 2000:
        d = get(c, f"{GAMMA}/markets",
                {"limit": 100, "offset": offset, "closed": "false"})
        if not d:
            break
        rows = d if isinstance(d, list) else d.get("data", [])
        if not rows:
            break
        found += [r for r in rows if looks_football(r.get("slug"), r.get("question"))]
        offset += len(rows)
    print(f"   scanned {offset} markets, {len(found)} football")
    for r in found[:15]:
        print(f"      {str(r.get('slug'))[:65]}")
    if found:
        dump("poly_football", found[:100])
    else:
        d = get(c, f"{GAMMA}/markets", {"limit": 20, "closed": "false"})
        rows = d if isinstance(d, list) else (d or {}).get("data", [])
        print("\n   no football found. sample slugs, to see the convention:")
        for r in rows[:15]:
            print(f"      {str(r.get('slug'))[:65]}")


if __name__ == "__main__":
    with httpx.Client(timeout=25, follow_redirects=True,
                      headers={"User-Agent": "calibrated-sports/0.1"}) as c:
        try:
            kalshi(c)
        except Exception as e:
            print("KALSHI FAILED:", type(e).__name__, e)
        try:
            polymarket(c)
        except Exception as e:
            print("POLYMARKET FAILED:", type(e).__name__, e)
    print("\nPaste the output. Whatever pattern shows up becomes the filter.")
