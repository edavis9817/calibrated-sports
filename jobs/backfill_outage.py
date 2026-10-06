"""Reconstruct Kalshi prices for a window the logger was down.

    python -m jobs.backfill_outage --from 2026-10-04T14:50:22Z --to 2026-10-04T15:28:03Z
    python -m jobs.backfill_outage --from ... --to ... --dry-run

THESE ROWS ARE RECONSTRUCTED, NOT CAPTURED. They come from Kalshi's 1-minute
candlesticks, which are free, need no auth, and are the only record of a minute
nobody polled. What a candle is and is not:

  - one row per market per minute, stamped at the minute's END, carrying the
    CLOSING yes bid and ask of that minute. A tick it is not: the logger polls
    a hot market every 15 s and a live one every 10 s, and whatever happened
    inside the minute is gone.
  - `volume` is the contracts traded IN THAT MINUTE. A live row's volume is
    cumulative. Summing the two produced a 6.7-billion-contract week once.
  - no depth. A book not captured live is not recoverable at any price, and
    Polymarket's history endpoint carries no bid or ask at all, so neither is
    attempted here.

So they are written under their own `source`, and that label does two jobs:

    source = 'reconstructed:kalshi_candles_1m'

  1. retention. Only sources in QUOTES_PRUNE_SOURCES ('live') are prunable, so
     these rows are never deleted by the prune however old their `ts` is
     (invariant 8; `ingest_ts` is stamped by store.write_quotes regardless).
  2. honesty downstream. Anything that reads `source = 'live'` to mean "a
     price we saw at that instant" does not see them, and anything that wants
     the gap filled has to ask for them by name.

It is NOT 'backfill:kalshi_candles': jobs/backfill_history.py deletes every row
of that source for a market before it rewrites the hourly history, and would
take these with it.

WHICH MARKETS. Those the logger would have had in its `hot` or `live` tier at
some instant inside the window - a kickoff within HOT_WINDOW_MIN after the
window's start or LIVE_WINDOW_MIN before its end, by store.kickoff_map. Season
futures and next week's markets are left out: they are polled every 5 to 10
minutes, and the hourly candles in backfill_history already describe them.

Raw first (invariant 2): each response is archived verbatim before it is parsed.
A re-run replaces its OWN rows for the window and nothing else.
"""
import argparse
import calendar
import sqlite3
import time

import httpx

import config
import store
from jobs.backfill_history import KALSHI_RPS, Pacer, _get, kalshi_rows

SOURCE = "reconstructed:kalshi_candles_1m"
RAW_REF = "kalshi_history/outage"
PERIOD_MIN = 1


def parse_utc(s: str) -> float:
    return float(calendar.timegm(time.strptime(s.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")))


def targets(t_from: float, t_to: float, kickoffs: dict = None):
    """[(ticker, meta)] for the Kalshi markets in play during the window."""
    if kickoffs is None:
        kickoffs = store.kickoff_map(("kalshi",))
    lo = t_from - config.LIVE_WINDOW_MIN * 60      # kicked off, still live at t_from
    hi = t_to + config.HOT_WINDOW_MIN * 60         # kicks off soon after t_to
    wanted = {m for (v, m), kick in kickoffs.items()
              if v == "kalshi" and kick is not None and lo < kick < hi}
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT market_id, event_id, market_type, subject, line FROM markets "
            "WHERE venue = 'kalshi'").fetchall()
    finally:
        con.close()
    return sorted((r[0], {"event_id": r[1], "market_type": r[2], "subject": r[3],
                          "line": r[4]}) for r in rows if r[0] in wanted)


def window_rows(ticker, meta, candles, t_from: float, t_to: float):
    """Candles whose minute ENDS inside the gap, relabelled as reconstructed."""
    out = []
    for r in kalshi_rows(ticker, meta, candles):
        if t_from < r["ts"] < t_to:
            r["source"], r["raw_ref"] = SOURCE, RAW_REF
            out.append(r)
    return out


def fetch(client, pacer, ticker, t_from: float, t_to: float):
    series = ticker.split("-")[0]
    body, status = _get(
        client, pacer,
        f"{config.KALSHI_BASE}/series/{series}/markets/{ticker}/candlesticks",
        {"start_ts": int(t_from) - 60, "end_ts": int(t_to) + 60,
         "period_interval": PERIOD_MIN})
    if body:
        store.archive_raw("kalshi_history", f"outage/{ticker}", body)
        return body.get("candlesticks") or [], status
    return [], status


def run(t_from: float, t_to: float, dry_run: bool = False, limit: int = None,
        client=None, pacer=None, kickoffs: dict = None) -> dict:
    if not t_from < t_to:
        raise ValueError("--from must be before --to")
    tgts = targets(t_from, t_to, kickoffs)
    if limit:
        tgts = tgts[:limit]
    stats = {"window_min": round((t_to - t_from) / 60, 1), "markets": len(tgts),
             "with_rows": 0, "empty": 0, "failed": 0, "rows": 0, "source": SOURCE}
    if dry_run:
        return stats
    pacer = pacer or Pacer(KALSHI_RPS)
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True,
                                    headers={"User-Agent": config.USER_AGENT})
    t0 = time.time()
    try:
        for i, (ticker, meta) in enumerate(tgts, 1):
            try:
                candles, status = fetch(client, pacer, ticker, t_from, t_to)
            except Exception:                     # noqa: BLE001 - one market
                stats["failed"] += 1
                continue
            if status != 200 and not candles:
                stats["failed"] += 1
                continue
            rows = window_rows(ticker, meta, candles, t_from, t_to)
            if not rows:
                stats["empty"] += 1
                continue
            # Replace this job's OWN rows for this market and window, so a
            # re-run is idempotent. Nothing of any other source is touched.
            with store.db() as c:
                c.execute("DELETE FROM quotes WHERE venue = 'kalshi' AND market_id = ? "
                          "AND source = ? AND ts > ? AND ts < ?",
                          (ticker, SOURCE, t_from, t_to))
            stats["rows"] += store.write_quotes(rows, dedupe=False)
            stats["with_rows"] += 1
            if i % 200 == 0:
                print(f"    {i}/{len(tgts)}  rows={stats['rows']:,}  "
                      f"failed={stats['failed']}  {time.time() - t0:.0f}s", flush=True)
    finally:
        if own:
            client.close()
    stats["elapsed"] = round(time.time() - t0, 1)
    store.record_health(
        "backfill_outage", stats["failed"] == 0,
        f"RECONSTRUCTED from 1-minute candles, "
        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t_from))} to "
        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t_to))}: "
        + ", ".join(f"{k}={v}" for k, v in stats.items()),
        watermark=time.time())
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="t_from", required=True, help="UTC, 2026-10-04T14:50:22Z")
    ap.add_argument("--to", dest="t_to", required=True)
    ap.add_argument("--dry-run", action="store_true", help="count the markets, fetch nothing")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    s = run(parse_utc(args.t_from), parse_utc(args.t_to), dry_run=args.dry_run,
            limit=args.limit)
    print(", ".join(f"{k}={v}" for k, v in s.items()))


if __name__ == "__main__":
    main()
