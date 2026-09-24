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

# --- collegefootballdata.com ------------------------------------------------
# 1,000 requests per CALENDAR MONTH on the free tier, and unlike the Odds API
# there is no per-call credit weighting: one HTTP request is one unit whatever
# it returns. That makes the whole discipline "use week-level endpoints".
# `/games?year=&week=` returns every game for a week WITH line scores in a
# single call, so a season is ~15 requests and a per-game loop is ~800 - the
# failure mode that burns the month in one run.
#
# Because a re-parse must cost nothing, invariant 2 is a BUDGET rule here and
# not only a principle: archive verbatim, then re-derive from the archive
# forever. `jobs/ingest_cfbd.py --from-archive` re-parses at zero requests.
#
# A full historical backfill is NOT an API job - CFBD ships downloadable CSVs
# in the Starter Pack. Reach for that, never for a loop.
CFBD_API_KEY = os.getenv("CFBD_API_KEY")
CFBD_BASE = os.getenv("CFBD_BASE", "https://api.collegefootballdata.com")
CFBD_MONTHLY_BUDGET = int(os.getenv("CFBD_MONTHLY_BUDGET", 1000))
CFBD_RESERVE = int(os.getenv("CFBD_RESERVE", 100))  # never spend below this

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
# THE STORAGE ROOT. Every store - databases, exports, scratch - derives its
# location from here and never from a path literal. `data/x.db` resolves against
# the process working directory, which is the repo on C:, while RAW_DIR points at
# D:. That split put a 4.3 GB probe database on the disk with 22 GB free.
STORAGE_DIR = os.path.dirname(os.path.abspath(DB_PATH))


def storage_path(*parts) -> str:
    """A path inside the configured store. Use this instead of joining "data"."""
    return os.path.join(STORAGE_DIR, *parts)


# The website (contract v2, docs/web-schema.md). NO DEFAULTS, deliberately: a
# guessed path is how 6e42f09's stray database on the wrong disk happened, and a
# guessed bucket would publish into the wrong one. jobs/export_web.py refuses to
# export without WEB_EXPORT_DIR and to upload without WEB_R2_BUCKET; missing R2
# credentials skip the upload (logged) rather than failing the export.
WEB_EXPORT_DIR = os.getenv("WEB_EXPORT_DIR")              # local mirror of the R2 keys
WEB_R2_BUCKET = os.getenv("WEB_R2_BUCKET")                # calibrated-sports-site, NOT the raw bucket
WEB_R2_ACCESS_KEY_ID = os.getenv("WEB_R2_ACCESS_KEY_ID")  # token scoped to WEB_R2_BUCKET
WEB_R2_SECRET_ACCESS_KEY = os.getenv("WEB_R2_SECRET_ACCESS_KEY")
WEB_SITE_URL = os.getenv("WEB_SITE_URL")                  # used only to validate a refresh

# --- Live prices to the site (unit a-09) -------------------------------------
# The logger publishes the exchange prices the site's live page shows to
# `live/{sport}/prices.json` in WEB_R2_BUCKET, so the Worker reads R2 instead of
# calling the exchange from Cloudflare's egress (b-11: 15 of 15 reads 429).
# OFF BY DEFAULT. Turning on a 15-second writer is Ethan's decision, not a side
# effect of a restart: see jobs/publish_live_prices.py for the write budget.
LIVE_PRICES_ENABLED = os.getenv("LIVE_PRICES_ENABLED", "0") == "1"
LIVE_PRICES_SPORT = os.getenv("LIVE_PRICES_SPORT", "nfl")
# The series the live page reads - its `gameSeries`. Bounded on purpose: the
# site shows game winners, not the board.
LIVE_PRICES_SERIES = tuple(x for x in os.getenv(
    "LIVE_PRICES_SERIES", "KXNFLGAME").split(",") if x)
LIVE_PRICES_EVERY = float(os.getenv("LIVE_PRICES_EVERY", 15))       # check cadence, s
# A write happens when a newer read exists, or at least this often regardless,
# so an unchanged file still proves the producer is alive.
LIVE_PRICES_HEARTBEAT = float(os.getenv("LIVE_PRICES_HEARTBEAT", 300))
LIVE_PRICES_MAX_MARKETS = int(os.getenv("LIVE_PRICES_MAX_MARKETS", 200))
# A market not read for this long leaves the file rather than sitting in it.
LIVE_PRICES_KEEP_S = float(os.getenv("LIVE_PRICES_KEEP_S", 6 * 3600))

# --- The Live page's snapshot (unit a-23) ------------------------------------
# One scheduled reader writes `live/{sport}/snapshot.json`; no page request calls a
# third party. See jobs/live_snapshot.py. Cadence by mode, in seconds:
LIVE_SNAPSHOT_EVERY_LIVE = float(os.getenv("LIVE_SNAPSHOT_EVERY_LIVE", 30))        # a game in its window
LIVE_SNAPSHOT_EVERY_GAMEDAY = float(os.getenv("LIVE_SNAPSHOT_EVERY_GAMEDAY", 900))  # ET date with a kickoff
LIVE_SNAPSHOT_EVERY_IDLE = float(os.getenv("LIVE_SNAPSHOT_EVERY_IDLE", 3600))
# `stale_after` = generated_at + the cadence + this; covers a slow cycle and PUT.
LIVE_SNAPSHOT_STALE_GRACE = float(os.getenv("LIVE_SNAPSHOT_STALE_GRACE", 120))
LIVE_SNAPSHOT_TIMEOUT = float(os.getenv("LIVE_SNAPSHOT_TIMEOUT", 10))
# Per-source exponential backoff on failure; a longer Retry-After wins, up to the cap.
LIVE_SNAPSHOT_BACKOFF_BASE = float(os.getenv("LIVE_SNAPSHOT_BACKOFF_BASE", 30))
LIVE_SNAPSHOT_BACKOFF_MAX = float(os.getenv("LIVE_SNAPSHOT_BACKOFF_MAX", 3600))
# The exchange's game-winner quotes. ON by default because the 2026-09-24 audit asks
# for them; the 2026-09-23 ledger dropped a-09's logger publisher. Set 0 to drop them.
LIVE_SNAPSHOT_PRICES = os.getenv("LIVE_SNAPSHOT_PRICES", "1") == "1"
LIVE_SNAPSHOT_SCHEDULE_EVERY = float(os.getenv("LIVE_SNAPSHOT_SCHEDULE_EVERY", 3600))
# How often `jobs.ingest_feeds --injuries` is run to capture the report.
LIVE_SNAPSHOT_INJURIES_EVERY = float(os.getenv("LIVE_SNAPSHOT_INJURIES_EVERY", 6 * 3600))
# One failure event per (source, reason) per this long on the logger's check.
LIVE_SNAPSHOT_LOG_THROTTLE = float(os.getenv("LIVE_SNAPSHOT_LOG_THROTTLE", 900))
# This job's OWN dead-man. Never the logger's HEALTHCHECK_URL: a success ping from a
# second process would keep the logger's check green while the logger was dead.
LIVE_HEALTHCHECK_URL = os.getenv("LIVE_HEALTHCHECK_URL")


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
# Depth is tiered on kickoff for the same reason quotes are, and for one more:
# a full snapshot costs ~27s, so sampling every mapped market at one rate means
# the 13 books actually in play on a Sunday get sampled every 86s. Dropping
# week-17 futures out of the cycle is what buys the in-play cadence.
DEPTH_TIER_ENABLED = os.getenv("DEPTH_TIER_ENABLED", "1") == "1"
# Which tiers are worth a depth snapshot at all. Futures books do not move and
# a cold market has no game inside 24 hours.
DEPTH_TIERS = tuple(x for x in os.getenv(
    "DEPTH_TIERS", "hot,live,game").split(",") if x)

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
# Widened from 120 to 240 to capture cross-game line movement across the first
# full 13-game slate - afternoon and night lines move all day in response to
# early results, and that data is unrecoverable after the fact. Re-tune after
# measuring what the 120-240 band actually contains.
# Note: the windows that matter operationally are the T-90 inactive report and
# the final hour, both of which 120 already covered.
HOT_WINDOW_MIN = int(os.getenv("HOT_WINDOW_MIN", 240))
# In-game. REST polling is a stopgap here - live markets really want a
# WebSocket (see docs/briefs/005-live-capture.md). 10s is about as fast as
# polling is worth before you are just paying rate limit for duplicate rows.
POLL_LIVE = int(os.getenv("POLL_LIVE", 10))
LIVE_WINDOW_MIN = int(os.getenv("LIVE_WINDOW_MIN", 240))   # ~4h game window
# Midweek there is no game inside 24 hours and nothing to be fast about.
# Polling 3,265 Polymarket markets every 60s from Monday to Friday spends the
# rate limit of a venue we intend to trade on, to record prices that are not
# moving. COLD is what pays for the Sunday cadence.
POLL_COLD = int(os.getenv("POLL_COLD", 600))
# The FUTURE side only. Everything after the live window closes is cold; see
# tier_for(). Post-game prices converge to 0/1 and stay there.
COLD_WINDOW_HOURS = float(os.getenv("COLD_WINDOW_HOURS", 24))

# TIERS KEY ON KICKOFF, NOT ON THE VENUE'S CLOSE. Kalshi props close at GAME
# END and Polymarket's close at kickoff, so one close_ts means two different
# instants and a mid-game Kalshi market silently drops to the 60s tier while
# the identical Polymarket claim correctly runs at 10s. Observed live on the
# 2026-09-10 opener. close_ts is the fallback for markets with no mapped game.
TIER_ON_KICKOFF = os.getenv("TIER_ON_KICKOFF", "1") == "1"
# How often to rebuild the market -> kickoff map. It only changes when
# discovery finds new markets or the schedule moves.
KICKOFF_MAP_EVERY = float(os.getenv("KICKOFF_MAP_EVERY", 300))

# The Odds API is snapshot-scheduled (see venues/oddsapi.py), not polled. It
# has no business in the cadence tiers: it was burning a 10s loop to print
# "1 markets -> 0 quotes" 1,173 times. Check its ladder on its own timer.
ODDS_CHECK_EVERY = float(os.getenv("ODDS_CHECK_EVERY", 60))

# Halftime capture (brief 021 B2). Dense bulk polls of full-game spreads and
# totals ONLY inside each game's halftime window, keyed to SCHEDULED kickoff.
# Offsets come from `jobs/halftime_plan.py` over 1,086 regular-season games
# 2022-25: last Q2 play p5 76 min after scheduled kickoff, first Q3 play p95
# 113 min, halftime a steady ~14 min. [p5 end-Q2 - 10, p95 Q3 start + 10] =
# 66..123 min covers the whole halftime-plus-margin in 90% of games; a window
# on the MEDIAN covers it in 2%. OFF by default: it spends credits, and the
# spend is approved by a human before it is switched on.
ODDS_HALFTIME_ENABLED = os.getenv("ODDS_HALFTIME_ENABLED", "0") == "1"
ODDS_HALFTIME_MARKETS = os.getenv("ODDS_HALFTIME_MARKETS", "spreads,totals")
ODDS_HALFTIME_EVERY = float(os.getenv("ODDS_HALFTIME_EVERY", 30))
ODDS_HALFTIME_FROM_MIN = float(os.getenv("ODDS_HALFTIME_FROM_MIN", 66))
ODDS_HALFTIME_TO_MIN = float(os.getenv("ODDS_HALFTIME_TO_MIN", 123))
# Hard stop per UTC day, counted from the API's own x-requests-last. A wrong
# kickoff or a stuck window must not be able to drain the month.
ODDS_HALFTIME_DAILY_CAP = int(os.getenv("ODDS_HALFTIME_DAILY_CAP", 1200))

# How often the venue worker wakes to check whether any tier is due. It bounds
# the resolution of every cadence above: a 5s tick cannot honour a 3s tier, and
# it adds up to half a tick of jitter to the 10s `live` one. The loop body is
# cheap when nothing is due, so tick faster than the fastest tier.
LOOP_TICK = float(os.getenv("LOOP_TICK", 1))

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

# --- The Board (a-26, audit 5.3 / 5.5) ---------------------------------------
# The lean threshold T, in probability points. NOT read from the environment on
# purpose: T is set before the season's first board and is never tuned on
# results, so a changed T is a new LOG ENTRY here - (effective from, T, why) -
# not an edit to an existing one. `core.board.lean_threshold_at` picks the entry
# in force at a read, and the index file records the value it used.
# tests/test_board.py asserts the log is sorted and that its first entry is 4.0.
BOARD_LEAN_THRESHOLD_LOG = (
    ("2026-09-01T00:00:00Z", 4.0, "decided 2026-09-24 (audit 5.3, unit a-26) before the first "
     "board; effective from season start because no board read predates it"),
)
# The market probability is the median over THESE books' de-vigged over prices.
BOARD_BENCH_BOOKS = ("draftkings", "fanduel", "betmgm")
# Markets the Board lists, Odds API key -> the stat name used everywhere else.
BOARD_MARKETS = {"player_receptions": "receptions",
                 "player_rush_attempts": "rush_attempts",
                 "player_reception_yds": "receiving_yards"}
# Markets the MODEL prices on the Board: only those with a walk-forward record
# (brief 023 Part 1 scored receptions and rush attempts, 2023-2025). Any other
# market is listed with model and gap "-" and never leans.
BOARD_MODEL_STATS = ("receptions", "rush_attempts")
# A row whose market P(over) is below this, or above 1 minus it, is flagged
# `longshot`: multiplicative de-vig is biased at the extremes (revisit Shin or
# power de-vig once the Lab has settled data). 0.15 is research/longshot.py's
# bucket edge.
BOARD_LONGSHOT_P = 0.15
# Gap bands for "leans this size", in absolute probability points.
BOARD_GAP_BANDS = ((4.0, 6.0), (6.0, 8.0), (8.0, None))
# Cadence (audit 5.5): hourly from Tuesday 12:00 ET to kickoff, every 15 min in
# the last two hours before each kickoff slot, grading within an hour of final
# stats.
BOARD_READ_EVERY_MIN = 60
BOARD_CLOSE_READ_EVERY_MIN = 15
BOARD_CLOSE_WINDOW_MIN = 120
BOARD_GRADE_EVERY_MIN = 60
