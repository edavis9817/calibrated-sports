"""The logger loop. One long-lived process, tiered polling, survives restarts.

    python run_logger.py

Deliberately NOT serverless: cron minimums and cold starts make 15-second
pre-kickoff polling impossible, and a warm process keeps HTTP connections open.
Run it under systemd / a Fly or Render worker with restart-always.

Cadence: futures every 5 min, game markets every 60s, anything inside 2 hours
of close every 15s. Market discovery refreshes every 10 minutes so new props
appear without a restart.

Two background tasks run alongside the venue workers, both there to satisfy the
same rule - the system must survive three weeks of total neglect:

  watchdog()     dead-man switch, and the external healthcheck ping. The ping
                 goes out only while the switch is happy, so silence is the
                 alert - which is the only thing that survives the process
                 being killed outright.
  maintenance()  rotates raw shards to R2, prunes the quotes table, and
                 refreshes the live-tier nflverse mirror for the current season,
                 so none of it depends on somebody remembering a cron entry.
"""
import asyncio
import hashlib
import os
import signal
import time
from datetime import datetime, timezone

import httpx

import config
import nflverse
import store
from jobs import ingest_nflverse, prune_quotes, rotate_raw
from venues.base import RateLimiter
from venues.kalshi import KalshiClient
from venues.oddsapi import OddsApiClient
from venues.polymarket import PolymarketClient

DISCOVERY_EVERY = 600
_stop = asyncio.Event()


def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z {msg}", flush=True)


def tier_for(market: dict) -> str:
    """Which cadence bucket a market belongs to right now.

    Four tiers. The 'live' one matters: once kickoff passes, close_ts is in the
    past, so without this branch an in-progress game silently drops back to the
    60s 'game' cadence - the exact window where prices move fastest.
    """
    if market.get("market_type") == "future":
        return "futures"
    close = market.get("close_ts")
    if not close:
        return "game"
    secs_to_close = close - time.time()
    if 0 < secs_to_close < config.HOT_WINDOW_MIN * 60:
        return "hot"                      # pre-kickoff run-up
    if -config.LIVE_WINDOW_MIN * 60 < secs_to_close <= 0:
        return "live"                     # game in progress
    return "game"


async def poll_venue(client, markets, tier):
    subset = [m for m in markets if tier_for(m) == tier]
    if not subset:
        return
    t0 = time.time()
    try:
        rows = await client.fetch_quotes(subset)
        n = store.write_quotes(rows)
        store.log_poll(client.name, f"quotes:{tier}", len(subset), n, True, None,
                       time.time() - t0)
        log(f"{client.name:11s} {tier:7s} {len(subset):4d} markets -> {n:4d} quotes")
    except Exception as e:
        store.log_poll(client.name, f"quotes:{tier}", len(subset), 0, False, e,
                       time.time() - t0)
        log(f"{client.name:11s} {tier:7s} ERROR {type(e).__name__}: {e}")


async def venue_worker(client):
    """One worker per venue: refreshes its catalogue, then polls each tier on
    its own schedule. Failures are logged and retried, never fatal - a logger
    that dies on a bad response loses the data it exists to capture."""
    markets, last_discovery = [], 0.0
    next_run = {"futures": 0.0, "game": 0.0, "hot": 0.0, "live": 0.0}
    cadence = {"futures": config.POLL_FUTURES,
               "game": config.POLL_GAME,
               "hot": config.POLL_HOT,
               "live": config.POLL_LIVE}

    while not _stop.is_set():
        now = time.time()
        if now - last_discovery > DISCOVERY_EVERY:
            t0 = time.time()
            try:
                markets = await client.list_markets()
                store.upsert_markets(markets)
                store.log_poll(client.name, "discovery", len(markets), 0, True,
                               None, time.time() - t0)
                log(f"{client.name:11s} discovery -> {len(markets)} NFL markets")
            except Exception as e:
                store.log_poll(client.name, "discovery", 0, 0, False, e,
                               time.time() - t0)
                log(f"{client.name:11s} discovery ERROR {type(e).__name__}: {e}")
            last_discovery = now

        for tier, every in cadence.items():
            if now >= next_run[tier]:
                await poll_venue(client, markets, tier)
                next_run[tier] = now + every

        try:
            await asyncio.wait_for(_stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


def code_fingerprint() -> str:
    """Short hash over the files that define behaviour.

    Printed at startup so "is the running process on the new code?" is a
    question with an answer, rather than an inference from a restart time.
    """
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("run_logger.py", "store.py", "config.py", "r2.py",
                "nflverse.py", "queries.py",
                "venues/base.py", "venues/kalshi.py", "venues/polymarket.py",
                "venues/oddsapi.py", "jobs/rotate_raw.py", "jobs/prune_quotes.py",
                "jobs/ingest_nflverse.py"):
        path = os.path.join(here, rel)
        if os.path.exists(path):
            h.update(rel.encode())
            h.update(open(path, "rb").read())
    return h.hexdigest()[:12]


def deadman_status(rows, now=None, limit_min=None):
    """Given [(venue, last_ok_ts), ...], is the logger actually capturing?

    Split out from watchdog() so the condition can be tested directly. A switch
    that never fires is indistinguishable from one that is never needed, and
    only one of those is worth having.

    Returns (dead, stale_venues, newest_ts). `dead` means NOTHING has succeeded
    inside the window; `stale_venues` are the individual venues that have gone
    quiet while others still report.
    """
    now = time.time() if now is None else now
    limit = (config.DEADMAN_MIN if limit_min is None else limit_min) * 60
    seen = [(v, ts) for v, ts in rows if ts]
    newest = max((ts for _, ts in seen), default=None)
    if newest is None or now - newest > limit:
        return True, [v for v, _ in seen], newest
    return False, [v for v, ts in seen if now - ts > limit], newest


async def send_healthcheck(client) -> bool:
    """Ping the external dead-man. Never raises, never blocks the loop.

    A failed ping is a monitoring problem, not a capture problem: the logger
    must not care whether healthchecks.io is reachable.
    """
    if not config.HEALTHCHECK_URL:
        return False
    try:
        r = await client.get(config.HEALTHCHECK_URL,
                             timeout=config.HEALTHCHECK_TIMEOUT)
        ok = r.status_code < 400
        store.record_health("healthcheck", ok,
                            f"pinged, HTTP {r.status_code}" if ok
                            else f"ping rejected, HTTP {r.status_code}")
        return ok
    except Exception as e:
        store.record_health("healthcheck", False, f"{type(e).__name__}: {e}")
        return False


async def watchdog_tick(client=None, venues=None) -> bool:
    """One liveness check. Returns True if the external ping was sent.

    The ping is gated on exactly the condition the dead-man switch reports, in
    the same place, on purpose. Pinging from the poll loop instead would create
    two code paths that answer "is this thing capturing?" separately, and the
    failure that matters is the one where they disagree - a logger that keeps
    reassuring an external monitor while its own switch is tripped.

    When the switch IS tripped it stays silent rather than sending a /fail.
    Silence is what healthchecks turns into an alert on its own timer, and it
    also covers the cases a self-report never can: killed process, wedged loop,
    dead box.
    """
    try:
        with store.db() as c:
            rows = c.execute(
                "SELECT venue, MAX(ts) FROM poll_log WHERE ok=1 GROUP BY venue"
            ).fetchall()
    except Exception as e:
        log(f"watchdog ERROR {type(e).__name__}: {e}")
        return False

    # Only venues this process is actually running. poll_log keeps history
    # forever, so a venue you switch off would otherwise look permanently
    # stale and suppress every future ping.
    if venues:
        rows = [(v, ts) for v, ts in rows if v in venues]

    now = time.time()
    dead, stale, newest = deadman_status(rows, now)

    if dead:
        age = "never" if newest is None else f"{(now - newest)/60:.1f} min ago"
        log("!" * 68)
        log(f"DEAD-MAN SWITCH: no successful poll in {config.DEADMAN_MIN:g} "
            f"min (last: {age})")
        log("the process is up but data is NOT arriving - check venue "
            "discovery and credentials")
        for venue, ts in rows:
            log(f"    {venue:<12} last ok {(now - ts)/60:8.1f} min ago")
        log("HEALTHCHECK PING SUPPRESSED - external monitor will fire on its "
            "own timer")
        log("!" * 68)
        store.record_health("liveness", False,
                            f"no successful poll in {config.DEADMAN_MIN:g} min "
                            f"(last {age})", watermark=newest)
        return False

    if stale:
        log(f"WARNING: no successful poll from {', '.join(stale)} in "
            f"{config.DEADMAN_MIN:g} min - healthcheck ping suppressed")
    store.record_health(
        "liveness", not stale,
        f"last poll {(now - newest)/60:.1f} min ago"
        + (f"; STALE: {', '.join(stale)}" if stale else ""),
        watermark=newest)

    if stale or client is None:
        return False
    return await send_healthcheck(client)


async def watchdog(client=None, venues=None):
    """Dead-man switch.

    The failure this exists for is not a crash - a crash is loud and the
    supervisor restarts it. It is the process that stays up, keeps looping, and
    quietly stops capturing: an expired session, a venue that changed a filter
    out from under us, a discovery that now returns nothing. From the outside
    that looks identical to a healthy logger on a quiet night.

    So: if no venue has logged a successful poll inside DEADMAN_MIN, say so
    loudly and record it in source_health, which is the thing an outside
    monitor - or the publish job refusing to run on stale inputs - reads.
    """
    while not _stop.is_set():
        try:
            await watchdog_tick(client, venues)
        except Exception as e:
            log(f"watchdog ERROR {type(e).__name__}: {e}")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=config.DEADMAN_CHECK_EVERY)
        except asyncio.TimeoutError:
            pass


async def maintenance():
    """Rotation and pruning, in-process.

    These are standalone jobs and stay independently runnable, but they also run
    from here on a timer. The requirement is that the system survives three
    weeks of total neglect, and a retention scheme that only works when somebody
    remembers to install a cron entry does not survive neglect - it just moves
    where the failure comes from.

    Blocking work goes to a thread so a slow R2 round-trip cannot stall the
    poll loop. Nothing in here is allowed to raise.
    """
    while not _stop.is_set():
        try:
            await asyncio.wait_for(_stop.wait(), timeout=config.MAINTENANCE_EVERY)
            return                                    # shutting down
        except asyncio.TimeoutError:
            pass
        try:
            stats = await asyncio.to_thread(rotate_raw.run)
            if stats.get("skipped"):
                log(f"maintenance  rotate: {stats['due']} due, {stats['skipped']}")
            elif stats["due"]:
                log(f"maintenance  rotate: {stats['rotated']}/{stats['due']} shards "
                    f"-> R2, {stats['bytes']/1e6:.0f}MB reclaimed, "
                    f"{stats['failed']} failed")
        except Exception as e:
            log(f"maintenance  rotate ERROR {type(e).__name__}: {e}")
            store.record_health("rotate_raw", False, f"{type(e).__name__}: {e}")
        try:
            stats = await asyncio.to_thread(prune_quotes.run)
            if stats["deleted"]:
                log(f"maintenance  prune: {stats['deleted']} quote rows older than "
                    f"{config.QUOTES_RETENTION_DAYS:g}d, {stats['remaining']} remain")
        except Exception as e:
            log(f"maintenance  prune ERROR {type(e).__name__}: {e}")
            store.record_health("prune_quotes", False, f"{type(e).__name__}: {e}")

        await _refresh_nflverse()


_nflverse_last = 0.0


async def _refresh_nflverse():
    """Pull the live-tier nflverse datasets for the CURRENT season.

    Only the current season plus the all-season files: re-checking 27 seasons of
    history four times a day would be 2.4GB/day of downloads to rediscover that
    1999 has not changed. Content-hash dedupe means an unchanged pull writes
    nothing at all - no archive copy, no data_version, just a recorded check.

    The offseason tier is deliberately absent. participation does not refresh
    in-season, so polling it for the live season would be traffic in exchange
    for nothing; it is backfilled by hand after the postseason.
    """
    global _nflverse_last
    if not config.NFLVERSE_INGEST_ENABLED:
        return
    now = time.time()
    if now - _nflverse_last < config.NFLVERSE_INGEST_EVERY:
        return
    _nflverse_last = now
    season = nflverse.current_season()
    try:
        stats = await asyncio.to_thread(
            ingest_nflverse.run, None, [season], None, nflverse.LIVE)
        log(f"maintenance  nflverse {season}: {stats['ok']} updated, "
            f"{stats['unchanged']} unchanged, {stats['not_published']} "
            f"not published, {stats['failed']} failed, {stats['rows']} rows")
    except Exception as e:
        log(f"maintenance  nflverse ERROR {type(e).__name__}: {e}")
        store.record_health("nflverse", False, f"{type(e).__name__}: {e}")


async def main():
    store.init_db()
    # One bucket per venue. A single shared limiter makes Kalshi's discovery
    # pass throttle Polymarket's quotes and, worse, throttle the 15s hot tier
    # right when prices move. The venues have unrelated quotas.
    def limiter_for(name):
        return RateLimiter(config.rps_for(name))

    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT,
                                 headers={"User-Agent": config.USER_AGENT},
                                 follow_redirects=True) as http:
        clients = []
        enabled = {v.name for v in config.VENUES if v.enabled}
        if "kalshi" in enabled:
            clients.append(KalshiClient(http, limiter_for("kalshi")))
        if "polymarket" in enabled:
            clients.append(PolymarketClient(http, limiter_for("polymarket")))
        if config.ODDS_API_KEY:
            clients.append(OddsApiClient(http, limiter_for("oddsapi")))
        if not clients:
            log("no venues enabled - check ENABLE_KALSHI / ENABLE_POLYMARKET")
            return

        free_gb = store.disk_free_bytes() / 1e9
        log(f"build {code_fingerprint()}  pid {os.getpid()}")
        log(f"logging {[c.name for c in clients]} -> {config.DB_PATH}")
        log(f"disk {free_gb:.1f}GB free (floor {config.DISK_MIN_FREE_GB:g}GB) | "
            f"raw rotates at {config.RAW_ROTATE_DAYS:g}d "
            f"({'R2 configured' if config.r2_configured() else 'R2 NOT CONFIGURED'}) | "
            f"quotes kept {config.QUOTES_RETENTION_DAYS:g}d | "
            f"dead-man {config.DEADMAN_MIN:g}min")
        log(f"nflverse mirror: season {nflverse.current_season()}, live tier "
            f"every {config.NFLVERSE_INGEST_EVERY/3600:g}h "
            f"({'on' if config.NFLVERSE_INGEST_ENABLED else 'OFF'}), "
            f"exempt from rotation")
        log("healthcheck: " + ("pinging every "
            f"{config.DEADMAN_CHECK_EVERY:g}s while liveness is healthy"
            if config.HEALTHCHECK_URL else
            "HEALTHCHECK_URL NOT SET - no external dead-man"))
        names = {c.name for c in clients}
        await asyncio.gather(*(venue_worker(c) for c in clients),
                             watchdog(http, names), maintenance())


def _handle_signal(*_):
    log("shutdown requested")
    _stop.set()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            signal.signal(sig, _handle_signal)
    loop.run_until_complete(main())
