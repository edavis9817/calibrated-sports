"""Probe the venues' historical price endpoints before writing an ingester.

    python probe_history.py

Hits ONE known market per venue, prints the raw response shape, and saves the
payload to data/probe/. Nothing here writes to the store.

The rule this enforces: do not build against remembered API docs. Every venue
fact in CLAUDE.md that cost a session to learn - dollars not cents, repeated
`tickers` params, the dead `player_stats` release - was something the docs
either did not say or said differently from what the wire actually returns.
"""
import base64
import json
import os
import time

import httpx

import config

OUT = "data/probe"
KALSHI_MARKET = "KXNFLREC-26SEP13DALNYG-NYGILIKELY9-4"   # a mapped week-1 prop
KALSHI_SERIES = "KXNFLREC"


def dump(name, payload):
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/{name}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"      saved {OUT}/{name}.json")


def kalshi_headers(method: str, path: str):
    """RSA-PSS over `timestamp + METHOD + path`, if a key is configured.

    Returns {} when there is no usable key, so the caller can find out whether
    the endpoint needed auth at all rather than assuming it did.
    """
    key_pem = config.kalshi_private_key()
    if not (key_pem and config.KALSHI_KEY_ID):
        return {}
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except Exception as e:
        print(f"      [key unusable] {type(e).__name__}: {str(e)[:80]}")
        return {}
    ts = str(int(time.time() * 1000))
    msg = (ts + method + path).encode()
    sig = key.sign(msg,
                   padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                               salt_length=padding.PSS.DIGEST_LENGTH),
                   hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": config.KALSHI_KEY_ID,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


def describe(payload, name, depth=0, limit=6):
    pad = "  " * (depth + 3)
    if isinstance(payload, dict):
        print(f"{pad}{name}: dict, keys={list(payload)[:10]}")
        for k in list(payload)[:limit]:
            describe(payload[k], k, depth + 1, limit)
    elif isinstance(payload, list):
        print(f"{pad}{name}: list[{len(payload)}]")
        if payload:
            describe(payload[0], f"{name}[0]", depth + 1, limit)
    else:
        v = repr(payload)
        print(f"{pad}{name}: {type(payload).__name__} = {v[:60]}")


def probe_kalshi(c):
    print("\n" + "=" * 72)
    print("KALSHI candlesticks")
    print("=" * 72)
    key = config.kalshi_private_key()
    print(f"  private key configured: {bool(key)}"
          + ("" if not key else f" ({key.count(chr(10))} lines)"))

    now = int(time.time())
    path = (f"/trade-api/v2/series/{KALSHI_SERIES}/markets/{KALSHI_MARKET}"
            f"/candlesticks")
    params = {"start_ts": now - 30 * 86400, "end_ts": now, "period_interval": 60}

    for label, hdrs in (("unauthenticated", {}),
                        ("authenticated", kalshi_headers("GET", path))):
        if label == "authenticated" and not hdrs:
            print("\n  authenticated: SKIPPED - no usable key")
            continue
        t0 = time.time()
        r = c.get(f"{config.KALSHI_BASE}/series/{KALSHI_SERIES}/markets/"
                  f"{KALSHI_MARKET}/candlesticks", params=params, headers=hdrs)
        print(f"\n  {label}: HTTP {r.status_code} in {time.time()-t0:.2f}s, "
              f"{len(r.content):,} bytes")
        for h in ("x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"):
            if h in r.headers:
                print(f"      {h}: {r.headers[h]}")
        if r.status_code != 200:
            print(f"      body: {r.text[:200]}")
            continue
        body = r.json()
        describe(body, "response")
        dump(f"kalshi_candlesticks_{label}", body)
        cs = body.get("candlesticks") or []
        if cs:
            print(f"\n      {len(cs)} candles")
            print(f"      first: {json.dumps(cs[0])[:300]}")
            ts = [x.get("end_period_ts") for x in cs if x.get("end_period_ts")]
            if ts:
                print(f"      range: {min(ts)} .. {max(ts)}  "
                      f"({(max(ts)-min(ts))/3600:.1f}h)")
                gaps = sorted({ts[i+1] - ts[i] for i in range(len(ts) - 1)})
                print(f"      granularity (distinct gaps, s): {gaps[:5]}")
            has = lambda k: sum(1 for x in cs if _find(x, k) is not None)
            for field in ("volume", "open_interest", "price", "yes_bid",
                          "yes_ask"):
                print(f"      carries {field:<14}: {has(field)}/{len(cs)}")
        return body
    return None


def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            got = _find(v, key)
            if got is not None:
                return got
    return None


def probe_polymarket(c, token_id=None):
    print("\n" + "=" * 72)
    print("POLYMARKET CLOB prices-history")
    print("=" * 72)
    if not token_id:
        print("  no token id supplied")
        return None
    print(f"  token: {token_id[:36]}...")
    for label, params in (
            ("max interval", {"market": token_id, "interval": "max",
                              "fidelity": 60}),
            ("1m fidelity", {"market": token_id, "interval": "max",
                             "fidelity": 1}),
    ):
        t0 = time.time()
        r = c.get(f"{config.POLY_CLOB}/prices-history", params=params)
        print(f"\n  {label}: HTTP {r.status_code} in {time.time()-t0:.2f}s, "
              f"{len(r.content):,} bytes")
        if r.status_code != 200:
            print(f"      body: {r.text[:200]}")
            continue
        body = r.json()
        describe(body, "response")
        pts = body.get("history") or []
        if pts:
            print(f"\n      {len(pts)} points")
            print(f"      first: {json.dumps(pts[0])}")
            print(f"      last : {json.dumps(pts[-1])}")
            ts = [p["t"] for p in pts if "t" in p]
            if len(ts) > 1:
                gaps = sorted({ts[i+1] - ts[i] for i in range(len(ts) - 1)})
                print(f"      range: {min(ts)} .. {max(ts)} "
                      f"({(max(ts)-min(ts))/86400:.2f} days)")
                print(f"      granularity (distinct gaps, s): {gaps[:5]}")
            print(f"      fields per point: {sorted(pts[0])}")
            print(f"      carries volume/OI: "
                  f"{any(k in pts[0] for k in ('v', 'volume', 'oi'))}")
        dump(f"poly_prices_history_{label.replace(' ', '_')}", body)
        if label == "max interval":
            saved = body
    return locals().get("saved")


def rate_limit_probe(c, url, params, n=8):
    """Fire a short burst and report what the venue does about it."""
    print(f"\n  rate limit: {n} rapid requests")
    codes, t0 = [], time.time()
    for _ in range(n):
        try:
            codes.append(c.get(url, params=params).status_code)
        except Exception as e:
            codes.append(type(e).__name__)
    el = time.time() - t0
    print(f"      {codes}  in {el:.2f}s  ({n/el:.1f} req/s sustained)")


def main():
    import sqlite3
    token = None
    try:
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        row = con.execute(
            """SELECT mo.market_id FROM market_outcome mo
                WHERE mo.venue='polymarket' AND mo.outcome_id IS NOT NULL
                LIMIT 1""").fetchone()
        token = row[0] if row else None
        con.close()
    except Exception as e:
        print(f"(no db: {e})")

    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": config.USER_AGENT}) as c:
        probe_kalshi(c)
        probe_polymarket(c, token)
        rate_limit_probe(c, f"{config.POLY_CLOB}/prices-history",
                         {"market": token, "interval": "max", "fidelity": 60})

    print("\nRead the shapes above before writing the backfill.\n")


if __name__ == "__main__":
    main()
