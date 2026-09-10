"""Backfill venue price history for every mapped market.

    python -m jobs.backfill_history                 # both venues
    python -m jobs.backfill_history --venue kalshi --limit 50
    python -m jobs.backfill_history --report

Both endpoints are FREE and need no auth. Probed 2026-09-09 (probe_history.py):

  Kalshi  /series/{s}/markets/{t}/candlesticks
          `period_interval` is in MINUTES (1 / 60 / 1440), not seconds.
          A 90-day span returns; 365 days is HTTP 400, so long ranges must be
          chunked. Carries volume_fp AND open_interest_fp, both populated on
          liquid markets. `price` is {} when nothing has traded - bid/ask still
          exist, so a market with no prints is not a market with no quotes.
          RATE LIMITED HARD: 429 after ~5 rapid requests, and no rate headers
          and no Retry-After, so back off blind.

  Polymarket /prices-history?market={token}&interval=max&fidelity={min}
          ~31 days available. Returns ONLY {t, p} - no volume, no open
          interest, no bid/ask. That is a fact about the venue, not a gap in
          this job, and it is why the liquidity map treats the two venues
          differently rather than averaging them into one number.

Raw first (invariant #2): every response is archived verbatim before it is
parsed, so a normalizer fix is a re-parse and not a re-fetch.
"""
import argparse
import json
import sqlite3
import time

import httpx

import config
import store

# Kalshi 429s after ~5 requests fired back to back. This is well under.
KALSHI_RPS = 3.0
POLY_RPS = 6.0
KALSHI_CHUNK_DAYS = 80          # 90 works, 365 is a 400; leave headroom
KALSHI_PERIOD_MIN = 60          # hourly. 1-minute is 55x the rows for noise.
POLY_FIDELITY_MIN = 60


class Pacer:
    """Blind token bucket with exponential backoff. Neither venue returns a
    Retry-After, so the only safe strategy is to stay under and slow down when
    told off."""

    def __init__(self, rps):
        self.interval = 1.0 / rps
        self.next_at = 0.0
        self.penalty = 0.0

    def wait(self):
        now = time.monotonic()
        due = self.next_at + self.penalty
        if now < due:
            time.sleep(due - now)
        self.next_at = time.monotonic() + self.interval

    def throttled(self):
        self.penalty = min(max(self.penalty * 2, 0.5), 30.0)

    def ok(self):
        self.penalty *= 0.5 if self.penalty > 0.01 else 0.0


def _get(client, pacer, url, params, tries=6):
    for attempt in range(tries):
        pacer.wait()
        try:
            r = client.get(url, params=params)
        except Exception:
            pacer.throttled()
            continue
        if r.status_code == 429 or r.status_code >= 500:
            pacer.throttled()
            continue
        pacer.ok()
        if r.status_code != 200:
            return None, r.status_code
        return r.json(), 200
    return None, 429


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dollars(block, key="close_dollars"):
    return _f(block.get(key)) if isinstance(block, dict) else None


# ---- Kalshi -----------------------------------------------------------------

def kalshi_candles(client, pacer, ticker, days=None):
    """Every candle for one market, chunked to stay inside the span limit."""
    series = ticker.split("-")[0]
    now = int(time.time())
    days = days or 90
    out, status = [], 200
    start = now - days * 86400
    while start < now:
        end = min(start + KALSHI_CHUNK_DAYS * 86400, now)
        body, status = _get(
            client, pacer,
            f"{config.KALSHI_BASE}/series/{series}/markets/{ticker}/candlesticks",
            {"start_ts": start, "end_ts": end,
             "period_interval": KALSHI_PERIOD_MIN})
        if body:
            store.archive_raw("kalshi_history", f"candles/{ticker}", body)
            out.extend(body.get("candlesticks") or [])
        start = end
    return out, status


def kalshi_rows(ticker, meta, candles):
    rows = []
    for cs in candles:
        ts = cs.get("end_period_ts")
        if not ts:
            continue
        bid = _dollars(cs.get("yes_bid"))
        ask = _dollars(cs.get("yes_ask"))
        # `price` is {} on a market with no prints. Bid/ask still exist, so the
        # row is real - it just has no trade in it.
        last = _dollars(cs.get("price"))
        rows.append({
            "ts": float(ts), "sport": "nfl", "venue": "kalshi",
            "event_id": meta.get("event_id"), "market_id": ticker,
            "market_type": meta.get("market_type"), "subject": meta.get("subject"),
            "line": meta.get("line"), "side": "yes",
            "best_bid": bid, "best_ask": ask,
            "mid": (bid + ask) / 2.0 if bid is not None and ask is not None else None,
            "last": last,
            "volume": _f(cs.get("volume_fp")),
            "open_interest": _f(cs.get("open_interest_fp")),
            "raw_ref": "kalshi_history/candles",
            "source": "backfill:kalshi_candles",
        })
    return rows


# ---- Polymarket -------------------------------------------------------------

def poly_history(client, pacer, token):
    body, status = _get(client, pacer, f"{config.POLY_CLOB}/prices-history",
                        {"market": token, "interval": "max",
                         "fidelity": POLY_FIDELITY_MIN})
    if body:
        store.archive_raw("polymarket_history", f"prices/{token[:24]}", body)
        return body.get("history") or [], status
    return [], status


def poly_rows(token, meta, points):
    rows = []
    for p in points:
        ts, price = p.get("t"), p.get("p")
        if ts is None or price is None:
            continue
        rows.append({
            "ts": float(ts), "sport": "nfl", "venue": "polymarket",
            "event_id": meta.get("event_id"), "market_id": token,
            "market_type": meta.get("market_type"), "subject": meta.get("subject"),
            "line": meta.get("line"), "side": "yes",
            # prices-history returns a single price, not a book. Recording it as
            # `mid` with no bid/ask is the honest shape: there is no spread to
            # be had here, and a fabricated one would be worse than a null.
            "best_bid": None, "best_ask": None,
            "mid": float(price), "last": float(price),
            "volume": None, "open_interest": None,
            "raw_ref": "polymarket_history/prices",
            "source": "backfill:poly_history",
        })
    return rows


# ---- the job ----------------------------------------------------------------

def targets(venue, limit=None):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    q = """SELECT mo.market_id, m.event_id, m.market_type, m.subject, m.line
             FROM market_outcome mo
             JOIN markets m ON m.venue = mo.venue AND m.market_id = mo.market_id
            WHERE mo.venue = ? AND mo.outcome_id IS NOT NULL"""
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = con.execute(q, (venue,)).fetchall()
    con.close()
    return [(r[0], {"event_id": r[1], "market_type": r[2], "subject": r[3],
                    "line": r[4]}) for r in rows]


def run(venue, limit=None, days=90):
    tgts = targets(venue, limit)
    pacer = Pacer(KALSHI_RPS if venue == "kalshi" else POLY_RPS)
    stats = {"markets": len(tgts), "with_history": 0, "empty": 0,
             "failed": 0, "rows": 0}
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": config.USER_AGENT}) as client:
        for i, (market_id, meta) in enumerate(tgts, 1):
            try:
                if venue == "kalshi":
                    raw, status = kalshi_candles(client, pacer, market_id, days)
                    rows = kalshi_rows(market_id, meta, raw)
                else:
                    raw, status = poly_history(client, pacer, market_id)
                    rows = poly_rows(market_id, meta, raw)
            except Exception:
                stats["failed"] += 1
                continue
            if status != 200 and not raw:
                stats["failed"] += 1
                continue
            if not rows:
                stats["empty"] += 1
                continue
            stats["with_history"] += 1
            # A re-run REPLACES this market's prior derivation. Appending is
            # what turned an 578,708-row Kalshi backfill into 857,676 after a
            # single restore - the same bug the Odds API job had, and it is
            # invisible until someone counts.
            with store.db() as c:
                c.execute("DELETE FROM quotes WHERE venue=? AND market_id=? "
                          "AND source=?",
                          (venue, market_id,
                           "backfill:kalshi_candles" if venue == "kalshi"
                           else "backfill:poly_history"))
            stats["rows"] += store.write_quotes(rows, dedupe=False)
            if i % 200 == 0:
                print(f"    {venue} {i}/{len(tgts)}  rows={stats['rows']:,}  "
                      f"{time.time()-t0:.0f}s")
    stats["elapsed"] = round(time.time() - t0, 1)
    store.record_health(f"backfill:{venue}", stats["failed"] == 0,
                        ", ".join(f"{k}={v}" for k, v in stats.items()),
                        watermark=time.time())
    return stats


def report():
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print(f"{'venue':<12} {'source':<26} {'rows':>10} {'markets':>8} "
          f"{'earliest':>12} {'latest':>12} {'days':>6}")
    for venue, src, n, mk, lo, hi in con.execute(
            """SELECT venue, source, COUNT(*), COUNT(DISTINCT market_id),
                      MIN(ts), MAX(ts) FROM quotes GROUP BY venue, source
                ORDER BY venue, source"""):
        print(f"{venue:<12} {src:<26} {n:>10,} {mk:>8,} {lo:>12.0f} "
              f"{hi:>12.0f} {(hi-lo)/86400:>6.1f}")
    print()
    for venue, n, v, oi in con.execute(
            """SELECT venue, COUNT(*), SUM(volume IS NOT NULL),
                      SUM(open_interest IS NOT NULL) FROM quotes
                WHERE source LIKE 'backfill%' GROUP BY venue"""):
        print(f"  {venue:<12} backfilled {n:>9,} rows | volume on {v:>9,} "
              f"| open interest on {oi:>9,}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--venue", choices=("kalshi", "polymarket"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.report:
        report()
        return
    for venue in ([args.venue] if args.venue else ["kalshi", "polymarket"]):
        print(f"\n=== {venue} ===")
        s = run(venue, args.limit, args.days)
        print("  " + " ".join(f"{k}={v}" for k, v in s.items()))
    print()
    report()


if __name__ == "__main__":
    main()
