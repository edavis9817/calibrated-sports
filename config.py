"""Configuration. Everything overridable by env var so nothing secret lives in git."""
import os

# Load .env if present. Without this, Windows PowerShell and Unix shells need
# completely different incantations to set env vars - this makes `.env` work
# identically everywhere.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from dataclasses import dataclass, field

# --- Venue base URLs -------------------------------------------------------
# VERIFY THESE ON FIRST RUN with `python probe.py`. Kalshi in particular has
# moved hosts more than once; the probe prints what actually answers.
KALSHI_BASE = os.getenv("KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")
POLY_GAMMA = os.getenv("POLY_GAMMA", "https://gamma-api.polymarket.com")
POLY_CLOB = os.getenv("POLY_CLOB", "https://clob.polymarket.com")

# --- Optional auth ---------------------------------------------------------
# Public market data needs none. Kalshi's /historical/* tier and any order
# placement need an RSA key pair (RSA-PSS over timestamp+METHOD+path).
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")


def kalshi_private_key() -> str | None:
    """The RSA private key PEM, however it was supplied.

    Accepts a path OR the key inline. Inline needs care: a PEM is multi-line
    and dotenv stops at the first newline unless the value is quoted, so an
    unquoted key silently becomes the 31-character string
    "-----BEGIN RSA PRIVATE KEY-----" and every signature fails with something
    unhelpful. When that has happened, reassemble the block from the raw .env
    rather than making the operator reformat a secret by hand.

    Returns None when no key is configured - unauthenticated endpoints still
    work and the caller degrades rather than crashes.
    """
    raw = (KALSHI_PRIVATE_KEY_PATH or "").strip()
    if not raw:
        return None
    if os.path.exists(raw):
        with open(raw, "r", encoding="utf-8") as f:
            return f.read()
    if not raw.startswith("-----BEGIN"):
        return None
    if "PRIVATE KEY-----" in raw and raw.count(chr(10)) > 1:
        return raw                      # already whole, e.g. a quoted value

    # Truncated by dotenv. Recover the block verbatim from the file itself.
    for candidate in (os.getenv("DOTENV_PATH"), ".env"):
        if not candidate or not os.path.exists(candidate):
            continue
        text = open(candidate, "r", encoding="utf-8").read()
        start = text.find("-----BEGIN")
        # Anchor on -----END, not on "KEY-----": the BEGIN header ENDS in
        # "KEY-----", so searching for that finds the header's own tail and
        # returns a 32-character "key" that fails with MalformedFraming.
        end = text.find("-----END", start + 10)
        if start == -1 or end == -1:
            continue
        tail = text.find(chr(10), end)
        end = len(text) if tail == -1 else tail
        return text[start:end].rstrip() + chr(10)
    return None

# --- The Odds API (sportsbook lines) ---------------------------------------
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
ODDS_BASE = os.getenv("ODDS_BASE", "https://api.the-odds-api.com/v4")
ODDS_SPORT = os.getenv("ODDS_SPORT", "americanfootball_nfl")
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "us")
# Bulk endpoint markets (cheap: one call covers every game)
ODDS_GAME_MARKETS = os.getenv("ODDS_GAME_MARKETS", "h2h,spreads,totals")
# Player props are PER-EVENT only and are the expensive part. Ordered by the
# forecastability ranking in the research docs - volume props first.
ODDS_PROP_MARKETS = os.getenv(
    "ODDS_PROP_MARKETS",
    "player_receptions,player_rush_attempts,player_reception_yds")
# Pinnacle is the sharp reference; measure CLV against it, not against the book
# you actually bet at. Empty string = keep every bookmaker returned.
ODDS_SHARP_BOOK = os.getenv("ODDS_SHARP_BOOK", "pinnacle")

# Credit budget. The free tier is 500/month and per-event props burn roughly
# (markets x regions) credits PER GAME PER CALL, so a 60s poll loop would spend
# the whole month in minutes. Snapshot on a schedule instead - see below.
ODDS_MONTHLY_BUDGET = int(os.getenv("ODDS_MONTHLY_BUDGET", 500))
ODDS_RESERVE = int(os.getenv("ODDS_RESERVE", 40))   # never spend below this

# Minutes before kickoff at which to take a book snapshot. The last one is the
# CLOSE and is the only one CLV strictly requires; the earlier ones buy you
# line-movement history. Trim this list first when credits are tight.
ODDS_SNAPSHOTS_MIN = [int(x) for x in os.getenv(
    "ODDS_SNAPSHOTS_MIN", "4320,1440,180,60,10").split(",")]
ODDS_SNAPSHOT_TOLERANCE_MIN = 8   # fire if within this many minutes of target

# --- Storage ---------------------------------------------------------------
DB_PATH = os.getenv("LOGGER_DB", "data/market_log.db")
RAW_DIR = os.getenv("LOGGER_RAW_DIR", "data/raw")

# --- Retention -------------------------------------------------------------
# The archive is the thing every derivation is re-runnable from (invariant #2),
# so nothing here deletes raw data - it MOVES it. Shards older than
# RAW_ROTATE_DAYS go to R2, are verified there, and only then leave local disk.
# A shard's whereabouts stays queryable in the raw_shards manifest.
RAW_ROTATE_DAYS = float(os.getenv("RAW_ROTATE_DAYS", 7))
# Verify by downloading the object back and hashing it, not just by trusting a
# 200 and a size. R2 egress is free, the shard is rotated once, and the whole
# point of the manifest is that "verified" means verified.
RAW_VERIFY_DOWNLOAD = os.getenv("RAW_VERIFY_DOWNLOAD", "1") == "1"
RAW_ROTATE_MAX_SHARDS = int(os.getenv("RAW_ROTATE_MAX_SHARDS", 500))  # per run

# Quotes are DERIVED - the raw archive can re-produce them - so this table is
# the one thing that may be pruned outright. See DECISIONS.md for the bytes/day
# arithmetic behind the default.
QUOTES_RETENTION_DAYS = float(os.getenv("QUOTES_RETENTION_DAYS", 14))
# Retention prunes LIVE capture only. Backfilled rows carry the timestamp of
# the event they describe, not of when they were fetched, so a 14-day window on
# `ts` deletes a 90-day history the moment it lands. That is exactly what
# happened: brief 006 lost 76 days of Kalshi candles and brief 009's entire
# 806-credit pilot, silently, to the hourly maintenance pass.
# Retention exists to bound GROWTH. A historical backfill does not grow, so
# pruning it buys nothing and destroys the expensive thing.
# And the age of a prunable row is measured on `ingest_ts`, never on `ts` -
# invariant 8. Rule 1 (this list) fails silently the first time someone adds a
# source and forgets to list it; rule 2 (ingest_ts) would still delete a paid
# backfill, just fourteen days later. Both, or neither works.
QUOTES_PRUNE_SOURCES = tuple(
    x for x in os.getenv("QUOTES_PRUNE_SOURCES", "live").split(",") if x)
QUOTES_PRUNE_VACUUM = os.getenv("QUOTES_PRUNE_VACUUM", "0") == "1"

# --- nflverse -------------------------------------------------------------
# The reference corpus. Mirrored, never fetched on demand: nflverse applies NFL
# stat corrections retroactively, so a copy you overwrite is a history that
# silently mutates and a backtest you can no longer reproduce (invariant #5).
NFLVERSE_BASE = os.getenv(
    "NFLVERSE_BASE", "https://github.com/nflverse/nflverse-data/releases/download")
NFLVERSE_FIRST_SEASON = int(os.getenv("NFLVERSE_FIRST_SEASON", 1999))
NFLVERSE_TIMEOUT = float(os.getenv("NFLVERSE_TIMEOUT", 120))

# The whole corpus is well under 1GB and weekly in-season snapshots add ~25MB,
# but only because a dated copy is stored when the BYTES CHANGE. Snapshotting
# 26 seasons daily regardless would be ~265GB/year, and would bury the handful
# of versions that represent an actual stat correction in 364 identical ones.
NFLVERSE_DEDUPE_BY_HASH = os.getenv("NFLVERSE_DEDUPE_BY_HASH", "1") == "1"

# In-season refresh, run from the logger's maintenance loop. Only the CURRENT
# season plus the all-season files - re-pulling 27 seasons of history four times
# a day would be 2.4GB/day of downloads to discover that 1999 has not changed.
# 6h covers the fastest cadence the brief asks for (snaps and FTN, 4x/day).
NFLVERSE_INGEST_EVERY = float(os.getenv("NFLVERSE_INGEST_EVERY", 6 * 3600))
NFLVERSE_INGEST_ENABLED = os.getenv("NFLVERSE_INGEST_ENABLED", "1") == "1"

# Raw sources that retention must never touch. nflverse dated snapshots ARE the
# defence against stat corrections rewriting history: rotating them off the box
# or ageing them out defeats the entire point of mirroring. Market data is the
# storage problem; this is a rounding error.
RAW_ROTATE_EXEMPT = tuple(
    x for x in os.getenv("RAW_ROTATE_EXEMPT", "nflverse").split(",") if x)

# --- Cloudflare R2 (S3-compatible) -----------------------------------------
# Credentials come from the environment and nowhere else; .env is gitignored.
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_PREFIX = os.getenv("R2_PREFIX", "raw")
R2_ENDPOINT = os.getenv(
    "R2_ENDPOINT",
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else "")


def r2_configured() -> bool:
    return all((R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT))


# --- Disk headroom ---------------------------------------------------------
# A previous project of this shape died by filling its disk unattended. Below
# the floor the logger stops ARCHIVING rather than stops running: quote rows are
# tiny and are what the scoreboard needs, raw payloads are what fills a disk.
DISK_MIN_FREE_GB = float(os.getenv("DISK_MIN_FREE_GB", 5))
DISK_WARN_FREE_GB = float(os.getenv("DISK_WARN_FREE_GB", 12))
DISK_CHECK_EVERY = float(os.getenv("DISK_CHECK_EVERY", 30))   # seconds, cached

# --- Depth capture (brief 007) ---------------------------------------------
# Top of book is not a tradeable price. Measured 2026-09-10: the BATCHED
# /markets/orderbooks endpoint sustained 13.6 calls/s over 18 consecutive calls
# with zero 429s and returns full L2, so the whole slate costs ~25s per
# snapshot and REST holds it comfortably. (The ~5-call limit noted for Kalshi
# belongs to /candlesticks, a different endpoint.)
# At 60s this is ~0.95 GB/day of VWAP ladders plus allowlist raw L2.
DEPTH_CAPTURE_ENABLED = os.getenv("DEPTH_CAPTURE_ENABLED", "1") == "1"
DEPTH_CAPTURE_EVERY = float(os.getenv("DEPTH_CAPTURE_EVERY", 60))

# --- Liveness --------------------------------------------------------------
# Dead-man switch: if no venue has logged a successful poll in this long, the
# process is up but the data is not arriving, which is the failure that looks
# like success. Longest normal gap is DISCOVERY_EVERY (600s) plus a slow pass.
DEADMAN_MIN = float(os.getenv("DEADMAN_MIN", 20))
DEADMAN_CHECK_EVERY = float(os.getenv("DEADMAN_CHECK_EVERY", 60))
# Housekeeping runs inside the logger too, not only from cron. A box nobody
# touches for three weeks is the requirement, and that rules out depending on a
# scheduler somebody has to remember to install.
MAINTENANCE_EVERY = float(os.getenv("MAINTENANCE_EVERY", 3600))

# External dead-man: healthchecks.io. The logger pings this URL only while the
# internal switch says data is arriving, so SILENCE is the alert - if the
# process dies, wedges, or keeps looping without capturing, the pings stop and
# healthchecks fires on its own timer. That is the part a self-report cannot do:
# a process cannot tell you it is gone.
#
# Set the check to Period 20m / Grace 5m to match DEADMAN_MIN.
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL")
HEALTHCHECK_TIMEOUT = float(os.getenv("HEALTHCHECK_TIMEOUT", 10))

# --- Polling cadence (seconds) ---------------------------------------------
# Tiered because futures don't move and pre-kick game markets do. Tune after
# you see how often quotes actually change; over-polling burns rate limit for
# duplicate rows.
POLL_FUTURES = int(os.getenv("POLL_FUTURES", 300))
POLL_GAME = int(os.getenv("POLL_GAME", 60))
POLL_HOT = int(os.getenv("POLL_HOT", 15))      # inside HOT_WINDOW_MIN of kickoff
HOT_WINDOW_MIN = int(os.getenv("HOT_WINDOW_MIN", 120))
# In-game. REST polling is a stopgap here - live markets really want a
# WebSocket (see docs/briefs/005-live-capture.md). 10s is about as fast as
# polling is worth before you are just paying rate limit for duplicate rows.
POLL_LIVE = int(os.getenv("POLL_LIVE", 10))
LIVE_WINDOW_MIN = int(os.getenv("LIVE_WINDOW_MIN", 240))   # ~4h game window

# --- Rate limiting ---------------------------------------------------------
# Kalshi Basic tier: 200 read tokens/s, 10 tokens/request => ~20 req/s.
# Stay well under. There is NO Retry-After header on a 429, so back off blind.
MAX_RPS = float(os.getenv("MAX_RPS", 5))
BACKOFF_BASE = 1.5
BACKOFF_MAX = 60.0

# One bucket PER VENUE, not one shared bucket. A shared limiter means Kalshi's
# discovery pass starves Polymarket's and, worse, starves the 15s hot tier at
# exactly the moment prices move. The venues have unrelated quotas; sharing one
# number is a coupling with no upside.
VENUE_RPS = {
    "kalshi": float(os.getenv("KALSHI_RPS", 10)),      # ~half the Basic tier
    "polymarket": float(os.getenv("POLY_RPS", 5)),
    "oddsapi": float(os.getenv("ODDS_RPS", 1)),        # credit-bound, not rate-bound
}


def rps_for(venue: str) -> float:
    return VENUE_RPS.get(venue, MAX_RPS)

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", 20))
USER_AGENT = os.getenv("USER_AGENT", "sports-toolbox-logger/0.1")


# --- Kalshi discovery ------------------------------------------------------
# Discovery is /series -> filter to football -> enumerate markets per series.
# Walking /markets blind never reaches sports: there are ~13,900 series and the
# cursor walk hits its page cap long before the football ones.
#
# The series catalogue is ~5.7MB (category=Sports) and changes on the order of
# days, so it is cached rather than refetched every discovery cycle.
KALSHI_SERIES_TTL = int(os.getenv("KALSHI_SERIES_TTL", 86400))       # 1 day
# Only enumerate markets closing inside this horizon for game/prop series.
# Season futures ignore it - they close in February by definition.
KALSHI_CLOSE_HORIZON_DAYS = float(os.getenv("KALSHI_CLOSE_HORIZON_DAYS", 7))
KALSHI_ORDERBOOK_BATCH = 100          # hard API limit: 150 tickers returns 400

# The tracked series. MEASURED 2026-09-09: all 349 NFL series carry 16,756 open
# markets, which at the poll cadences below is gigabytes of SQLite per day and
# fills the disk inside a day. This allowlist is ~3,200 markets: the game core,
# the two priority props from the research (receptions, rush attempts), the SGP
# pre-packs (where the QB<->WR1 correlation of 0.42 gets mispriced) and the
# season futures (fee-favourable: 0.63c/contract at P=0.10 vs 1.75c at P=0.50).
#
# (pattern, market_type, horizon). A trailing '*' is a prefix match; horizon
# "week" applies KALSHI_CLOSE_HORIZON_DAYS, "season" does not.
# Set KALSHI_ALLOWLIST_OFF=1 to track every football series instead - read the
# market-count note above before you do.
KALSHI_SERIES_ALLOW = [
    ("KXNFLGAME",         "moneyline", "week"),
    ("KXNFLSPREAD",       "spread",    "week"),
    ("KXNFLTOTAL",        "total",     "week"),
    ("KXNFLREC",          "prop",      "week"),   # receptions, all k+ thresholds
    ("KXNFLRSHATT",       "prop",      "week"),   # rush attempts
    ("KXNFLPREPACKSGP*",  "parlay",    "week"),   # same-game parlay pre-packs
    ("KXNFLWINS*",        "future",    "season"),
    ("KXNFLAFC*",         "future",    "season"),
    ("KXNFLNFC*",         "future",    "season"),
]
KALSHI_ALLOWLIST_OFF = os.getenv("KALSHI_ALLOWLIST_OFF", "0") == "1"

# --- Polymarket discovery --------------------------------------------------
# Blind /markets pagination 422s at offset 2100 ("use /markets/keyset for deeper
# pagination"). Tag-filtered /events pages cleanly to the end instead.
POLY_TAG_SLUG = os.getenv("POLY_TAG_SLUG", "nfl")
POLY_PAGE_LIMIT = int(os.getenv("POLY_PAGE_LIMIT", 100))
POLY_MAX_OFFSET = int(os.getenv("POLY_MAX_OFFSET", 2000))   # gamma's own ceiling
# 12,620 of the 14,133 tagged markets are tradeable but most are dust. A floor
# of 100 keeps ~3,300 - everything with a book worth quoting against.
POLY_MIN_LIQUIDITY = float(os.getenv("POLY_MIN_LIQUIDITY", 100))
# Gamma carries bestBid/bestAsk inline on every market, so DISCOVERY yields a
# free quote for every market it finds. Refreshing quotes that way does not
# scale though: the events payload is 6.9MB per page x 6 pages for the three
# numbers we keep, which archives at ~26 GB/day. The CLOB reports the same
# top-of-book in 21KB per 250 markets (verified identical to the inline values
# 2026-09-09), so between discoveries the quotes come from there instead.
POLY_SNAPSHOT_TTL = float(os.getenv("POLY_SNAPSHOT_TTL", 300))
POLY_PRICE_BATCH = int(os.getenv("POLY_PRICE_BATCH", 250))   # 300 returns 400

# --- Write policy ----------------------------------------------------------
# Write a quote row when the price moved, plus an unconditional heartbeat row
# every QUOTE_HEARTBEAT_SEC. Without the heartbeat, "price held steady" and
# "the logger was down" are the same thing in the quotes table - and a missing
# T-5min close is indistinguishable from a quiet one, which is precisely the
# row CLV is measured against. poll_log catches a venue-level outage; it does
# not catch one market silently dropping out of discovery.
QUOTE_DEDUPE = os.getenv("QUOTE_DEDUPE", "1") == "1"
QUOTE_HEARTBEAT_SEC = float(os.getenv("QUOTE_HEARTBEAT_SEC", 300))
# The Odds API path is snapshot-scheduled, not polled, and every one of its
# rows is a deliberate capture. Never dedupe it.
QUOTE_DEDUPE_EXEMPT = ("oddsapi",)

@dataclass
class VenueConfig:
    name: str
    enabled: bool = True
    # substrings used to pick NFL markets out of the venue's full catalogue;
    # refine these once probe.py shows you the real ticker/slug conventions
    nfl_filters: list = field(default_factory=lambda: ["NFL", "nfl"])


VENUES = [
    VenueConfig("kalshi", enabled=os.getenv("ENABLE_KALSHI", "1") == "1"),
    VenueConfig("polymarket", enabled=os.getenv("ENABLE_POLYMARKET", "1") == "1"),
]
