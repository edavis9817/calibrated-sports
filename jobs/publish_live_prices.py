"""Live exchange prices for the site, published by the logger to R2 (unit a-09).

    python -m jobs.publish_live_prices --from-store [--out PATH]    stage only

WHY THIS EXISTS. The site's live page read the exchange from the Worker at
request time, and in production the exchange refused every read: b-11 measured
`HTTP 429` on 15 of 15 cache misses, one read per 16 s, from one colo. The
logger already holds a connection to the same exchange and already reads these
prices every 10-600 s depending on how close the game is. Reading them a second
time from every Cloudflare colo was both the cause of the 429 and redundant.
So the logger publishes what it has already read, and the Worker reads R2.

WHAT IS IN THE FILE, AND WHAT IS NOT.
  * Only the series the live page reads (`LIVE_PRICES_SERIES`, the site's
    `gameSeries`), capped at `LIVE_PRICES_MAX_MARKETS`, with anything over the
    cap COUNTED in `counts.omitted` rather than silently dropped.
  * Every price carries `read_at`: the start of the poll that read it. That is
    the moment it was true at the exchange, to within one poll - and it is
    deliberately the EARLIER edge, so a price is never presented as fresher
    than it was. It is not the publish time and it is not the upload time.
  * `expected_every_s` is the cadence the market was being polled at when it
    was read. A 9-minute-old price on a market polled every 600 s is normal; on
    one polled every 10 s it is a stall. Only the pair says which.
  * `stale_after` is the deadline for the NEXT write. Past it, the producer has
    stopped, whatever the prices say. The Worker decides what to render; this
    file's job is to make its own age impossible to miss.
  * No mid, no de-vig, no derived number. The Worker already computes the mid
    only where both sides are quoted; a second derivation here would be a
    second place for the two to disagree.
  * NOT the price path. The featured game's candle path is a separate read the
    Worker still makes; it is not covered by this file.

HOW IT RUNS. Inside the logger, which is the only process allowed to open
`market_log.db` read-write, and this module does not open it at all on the
logger path: `PriceBook` is fed in memory from the rows each poll has just
fetched (`run_logger.poll_venue`). The `--from-store` CLI reads the store
READ-ONLY to stage a file for inspection and has no upload path at all.

THE DELETION TRAP. `export_web.upload()` deletes by absence inside declared
prefixes and runs unconditionally from `weekly_refresh`. A high-frequency
writer into the same bucket is a new way to lose keys, in both directions:
  * this writer owns exactly ONE key per sport under `live/`, puts it, and has
    no delete call anywhere in the module (asserted by AST in the tests);
  * it never touches the batch uploader's state file, so the batch uploader
    never learns the key exists and cannot compute it as "removed";
  * and `export_web.upload()` refuses any declaration that reaches `live/`,
    never uploads a `live/` file from the export tree (a stale local copy would
    overwrite a fresh price), and never deletes under it - a `live/` key it
    somehow remembers is reported in `removed_withheld`, not removed.

WRITE BUDGET. One PUT per write. At most one write per `LIVE_PRICES_EVERY`
(15 s): a ceiling of 5,760 R2 Class A operations a day. A write happens only
when a newer read exists or `LIVE_PRICES_HEARTBEAT` (300 s) has passed, so a
day with no game inside 24 h - every market on the 600 s cold tier - writes
~288 times. The figures in the a-09 report are measured from `poll_log`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(ROOT, "web", "contract", "v2", "contract.schema.json")

KIND = "live.prices"
# The ONE top-level prefix this writer owns. export_web.upload() refuses any
# declaration that reaches it; see the module docstring.
LIVE_PREFIX = "live/"
CACHE_CONTROL = "no-store"
# A write that has not happened by generated_at + heartbeat is late; this much
# more before the file calls itself stale, so one slow PUT is not an outage.
GRACE_S = 2


def key_for(sport: str) -> str:
    return f"{LIVE_PREFIX}{sport}/prices.json"


def declared_prefix(sport: str) -> str:
    """The prefix this writer declares. Exactly its own sport's, nothing wider."""
    return f"{LIVE_PREFIX}{sport}/"


def iso(ts: float) -> str:
    """Contract `Timestamp` (to the second, Z). FLOORED: never later than true."""
    return datetime.fromtimestamp(math.floor(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _price(v):
    """A quote side, or None. 0 and 1 are not quotes on an exchange contract."""
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    return p if 0.0 < p < 1.0 else None


class PriceBook:
    """The latest read of every market the site shows, held in memory.

    Fed by the logger's poll loop from rows it has already fetched. Never
    raises into that loop: `observe` swallows a malformed row rather than
    costing the venue a poll.
    """

    def __init__(self, series=None, venue="kalshi"):
        self.series = tuple(config.LIVE_PRICES_SERIES if series is None else series)
        self.venue = venue
        self.entries: dict[str, dict] = {}
        self.catalogue: set[str] | None = None     # None = no discovery seen yet

    def wants(self, market_id) -> bool:
        return isinstance(market_id, str) and any(
            market_id == s or market_id.startswith(s + "-") for s in self.series)

    def observe(self, rows, every_s, markets=None):
        """Take the rows one poll produced. -> how many entries were updated."""
        meta = {m.get("market_id"): m for m in (markets or []) if isinstance(m, dict)}
        n = 0
        for r in rows or []:
            try:
                if r.get("venue") != self.venue or not self.wants(r.get("market_id")):
                    continue
                ts = float(r["ts"])
                tk = r["market_id"]
                prev = self.entries.get(tk)
                if prev is not None and prev["read_ts"] > ts:
                    continue                      # never let an older read win
                m = meta.get(tk) or {}
                close = m.get("close_ts", prev["close_ts"] if prev else None)
                self.entries[tk] = {
                    "ticker": tk,
                    "event": r.get("event_id") or (prev or {}).get("event") or "",
                    "bid": _price(r.get("best_bid")),
                    "ask": _price(r.get("best_ask")),
                    "read_ts": ts,
                    "every_s": int(every_s),
                    "close_ts": float(close) if close else None,
                }
                n += 1
            except Exception:                     # noqa: BLE001 - never into the poll loop
                continue
        return n

    def set_catalogue(self, markets):
        """The venue's latest successful discovery: which markets are still open."""
        self.catalogue = {m.get("market_id") for m in markets or [] if isinstance(m, dict)}

    def newest_read(self) -> float:
        return max((e["read_ts"] for e in self.entries.values()), default=0.0)

    def prune(self, now, keep_s=None):
        keep_s = config.LIVE_PRICES_KEEP_S if keep_s is None else keep_s
        gone = [k for k, e in self.entries.items() if now - e["read_ts"] > keep_s]
        for k in gone:
            del self.entries[k]
        return len(gone)


def build(book: PriceBook, now=None, sport=None, every_s=None, heartbeat_s=None,
          max_markets=None, source=None) -> dict:
    """The contract document for this instant. Pure: no I/O."""
    now = time.time() if now is None else now
    sport = config.LIVE_PRICES_SPORT if sport is None else sport
    every_s = config.LIVE_PRICES_EVERY if every_s is None else every_s
    heartbeat_s = config.LIVE_PRICES_HEARTBEAT if heartbeat_s is None else heartbeat_s
    max_markets = config.LIVE_PRICES_MAX_MARKETS if max_markets is None else max_markets

    # Nearest close first: when the cap bites, it is next week's games that go.
    ordered = sorted(book.entries.values(),
                     key=lambda e: (e["close_ts"] is None, e["close_ts"] or 0, e["ticker"]))
    kept, omitted = ordered[:max_markets], ordered[max_markets:]
    markets = [{
        "ticker": e["ticker"],
        "event": e["event"],
        "bid": e["bid"],
        "ask": e["ask"],
        "read_at": iso(e["read_ts"]),
        "expected_every_s": max(1, int(e["every_s"])),
        "close_at": iso(e["close_ts"]) if e["close_ts"] else None,
        # Unknown until a discovery has been seen; then "still listed as open".
        "in_catalogue": None if book.catalogue is None else e["ticker"] in book.catalogue,
    } for e in kept]
    return {
        "schema_version": _contract()["x-contract"]["schema_version"],
        "generated_at": iso(now),
        "kind": KIND,
        "sport": sport,
        "venue": book.venue,
        "source": source or ("logger in-memory book: each price is the logger's own poll of "
                             "the exchange order book, stamped with the poll's start"),
        "series": list(book.series),
        "publish_every_s": max(1, int(round(every_s))),
        "heartbeat_s": max(1, int(round(heartbeat_s))),
        "stale_after": iso(now + heartbeat_s + every_s + GRACE_S),
        "counts": {"markets": len(markets),
                   "two_sided": sum(1 for m in markets
                                    if m["bid"] is not None and m["ask"] is not None),
                   "omitted": len(omitted)},
        "markets": markets,
    }


# ------------------------------------------------------------------ contract

_CONTRACT = None
_VALIDATOR = None


def _contract():
    global _CONTRACT
    if _CONTRACT is None:
        with open(CONTRACT_PATH, encoding="utf-8") as f:
            _CONTRACT = json.load(f)
    return _CONTRACT


class ContractError(ValueError):
    pass


def validate(key: str, doc: dict) -> dict:
    """Refuse a document the contract refuses, and a key that routes elsewhere.

    Derived from the contract - kind name, $def and key pattern - rather than
    copied, the same rule export_web follows. Returns the document.
    """
    import re
    from jsonschema import Draft202012Validator

    global _VALIDATOR
    c = _contract()
    routed = [k["kind"] for k in c["x-contract"]["keys"] if re.match(k["pattern"], key)]
    if routed != [KIND]:
        raise ContractError(f"{key} routes to {routed}, not [{KIND!r}]")
    if doc.get("kind") != KIND:
        raise ContractError(f"{key}: the file says kind {doc.get('kind')!r}")
    if _VALIDATOR is None:
        name = c["x-contract"]["kinds"][KIND]
        _VALIDATOR = Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": c["$defs"]})
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
              for e in _VALIDATOR.iter_errors(doc)]
    if errors:
        raise ContractError(f"{len(errors)} contract violation(s) in {key}: " + "; ".join(errors[:5]))
    return doc


# ------------------------------------------------------------------ writing

def r2_client():
    """The site bucket's client, with SHORT timeouts and one retry.

    Not export_web.r2_client(): that one retries five times with botocore's
    60 s timeouts, which is right for a weekly batch and wrong for a 15 s
    cadence - a stuck PUT must cost one cycle, not several minutes.
    """
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=config.R2_ENDPOINT, region_name="auto",
        aws_access_key_id=config.WEB_R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.WEB_R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=10,
                      retries={"max_attempts": 1, "mode": "standard"}))


def configured() -> bool:
    return bool(config.WEB_R2_BUCKET and config.WEB_R2_ACCESS_KEY_ID
                and config.WEB_R2_SECRET_ACCESS_KEY and config.R2_ENDPOINT)


class Publisher:
    """Decides when to write, and writes exactly one key.

    A write is DUE when the book holds a read newer than the last write, or
    when the heartbeat has run out - so the file's `generated_at` keeps moving
    even on a quiet Tuesday, and a stopped producer is visible as a
    `stale_after` in the past rather than as a plausible old price.
    """

    def __init__(self, book: PriceBook, client=None, bucket=None, sport=None,
                 every_s=None, heartbeat_s=None):
        self.book = book
        self.client = client
        self.bucket = bucket
        self.sport = config.LIVE_PRICES_SPORT if sport is None else sport
        self.every_s = config.LIVE_PRICES_EVERY if every_s is None else every_s
        self.heartbeat_s = config.LIVE_PRICES_HEARTBEAT if heartbeat_s is None else heartbeat_s
        self.last_write_ts = 0.0
        self.last_newest_read = 0.0
        self.writes = 0

    @property
    def key(self):
        return key_for(self.sport)

    def due(self, now) -> str | None:
        """Why a write is due now, or None."""
        if now - self.last_write_ts < self.every_s:
            return None
        if self.book.newest_read() > self.last_newest_read:
            return "newer read"
        if now - self.last_write_ts >= self.heartbeat_s:
            return "heartbeat"
        return None

    def publish(self, now=None) -> dict:
        """Build, validate, PUT. Raises on failure; the caller decides."""
        now = time.time() if now is None else now
        self.book.prune(now)
        doc = validate(self.key, build(self.book, now=now, sport=self.sport,
                                       every_s=self.every_s, heartbeat_s=self.heartbeat_s))
        body = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
        client = self.client if self.client is not None else r2_client()
        bucket = self.bucket or config.WEB_R2_BUCKET
        client.put_object(Bucket=bucket, Key=self.key, Body=body,
                          ContentType="application/json", CacheControl=CACHE_CONTROL)
        self.client = client
        self.last_write_ts = now
        self.last_newest_read = self.book.newest_read()
        self.writes += 1
        # The same report shape as export_web.upload(), because a reader of
        # either should not have to learn a second vocabulary. This writer has
        # no delete path at all, so both deletion counts are 0 by construction
        # - reported rather than omitted, so "0" is a statement.
        return {"key": self.key, "bytes": len(body), "markets": doc["counts"]["markets"],
                "two_sided": doc["counts"]["two_sided"], "omitted": doc["counts"]["omitted"],
                "declared_prefixes": [declared_prefix(self.sport)],
                "deleted": 0, "removed_withheld": 0}


# ------------------------------------------------------------------ staging CLI

def book_from_store(db_path=None, now=None, series=None, keep_s=None) -> PriceBook:
    """A book from the store's latest quotes, READ-ONLY. Staging only.

    NOT what the logger publishes, and older than it: `write_quotes` dedupes on
    change-plus-heartbeat, so the newest stored row can predate the newest read
    by up to that heartbeat. Every `read_at` here is therefore conservative -
    never later than the true read. `expected_every_s` is unknown from the
    store and is set to the cold cadence, the widest there is.
    """
    now = time.time() if now is None else now
    keep_s = config.LIVE_PRICES_KEEP_S if keep_s is None else keep_s
    book = PriceBook(series=series)
    db_path = db_path or config.DB_PATH
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        # One indexed lookup per market (ix_quotes_market_ts), not a GROUP BY
        # over the quotes table: a long read transaction on the live store pins
        # its WAL, so this stays short.
        clauses = " OR ".join("market_id LIKE ?" for _ in book.series)
        close = dict(con.execute(
            f"SELECT market_id, close_ts FROM markets WHERE venue = ? AND ({clauses})",
            [book.venue, *[s + "-%" for s in book.series]]).fetchall())
        rows = []
        for mid in sorted(close):
            r = con.execute(
                "SELECT venue, market_id, event_id, best_bid, best_ask, ts FROM quotes "
                "WHERE venue = ? AND market_id = ? AND source = 'live' AND ts >= ? "
                "ORDER BY ts DESC LIMIT 1", (book.venue, mid, now - keep_s)).fetchone()
            if r is not None:
                rows.append(dict(r))
    finally:
        con.close()
    for r in rows:
        book.observe([r], config.POLL_COLD,
                     markets=[{"market_id": r["market_id"], "close_ts": close.get(r["market_id"])}])
    return book


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-store", action="store_true",
                    help="stage a file from the store's latest quotes (read-only). There is "
                         "deliberately no upload flag: publishing happens inside the logger")
    ap.add_argument("--out", help="write the staged file here instead of stdout")
    a = ap.parse_args(argv)
    if not a.from_store:
        ap.error("nothing to do: --from-store is the only mode (the logger publishes)")
    now = time.time()
    book = book_from_store(now=now)
    doc = validate(key_for(config.LIVE_PRICES_SPORT),
                   build(book, now=now, source="STAGED from the store, read-only: read_at is "
                                               "the newest STORED row, never later than the read"))
    body = json.dumps(doc, separators=(",", ":"), sort_keys=True)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(body)
    else:
        print(body)
    c = doc["counts"]
    print(f"staged {key_for(config.LIVE_PRICES_SPORT)}: {c['markets']} markets, "
          f"{c['two_sided']} two-sided, {c['omitted']} omitted, {len(body)} bytes",
          file=sys.stderr)
    if c["markets"] == 0:
        # Exit 0 is not a result: an empty staged file is a failed staging.
        print("REFUSING: zero markets staged", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
