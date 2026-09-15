"""Storage: SQLite for queryable quotes, gzipped JSONL for the raw archive.

Design rules, both learned the hard way in market-data work:

1. RAW FIRST. Every API response is archived verbatim before any parsing.
   Parsers have bugs and venues change shapes mid-season; the raw archive is
   the only thing that lets you re-derive history after you fix a parser.
2. APPEND ONLY. Never update a quote row. Storage is cheap, a corrupted
   time series is not.

SQLite is deliberate for v1 - zero infra, one file, and it will comfortably
handle a season of NFL at these cadences. Migrate to Postgres/Timescale when
you add a second sport or want concurrent writers, not before.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import config

# The full defensive set from stats_player_week. Defined once: the CREATE TABLE
# below and the ALTER TABLE migrations are both generated from this list, so the
# two cannot drift apart.
DEF_COLS = (
    "def_tackles_solo", "def_tackles_with_assist", "def_tackle_assists",
    "def_tackles_for_loss", "def_tackles_for_loss_yards",
    "def_sacks", "def_sack_yards", "def_qb_hits",
    "def_interceptions", "def_interception_yards", "def_pass_defended",
    "def_fumbles_forced", "def_fumbles", "def_tds", "def_safeties",
    "def_punt_blocks", "def_pat_blocks", "def_fg_blocks",
    "def_2pt_atts", "def_2pt_made",
)
_DEF_DDL = "".join(f"    {c:<28} REAL,\n" for c in DEF_COLS)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS quotes (
    id           INTEGER PRIMARY KEY,
    ts           REAL    NOT NULL,          -- unix seconds, capture time
    sport        TEXT    NOT NULL DEFAULT 'nfl',
    venue        TEXT    NOT NULL,
    event_id     TEXT,                      -- venue's event/game id
    market_id    TEXT    NOT NULL,          -- venue's market ticker / token id
    market_type  TEXT,                      -- spread | total | moneyline | prop | future
    subject      TEXT,                      -- team or player the market is about
    line         REAL,                      -- the threshold, where one exists
    side         TEXT,                      -- yes/no, over/under, home/away
    best_bid     REAL,
    best_ask     REAL,
    mid          REAL,
    last         REAL,
    volume       REAL,
    open_interest REAL,
    raw_ref      TEXT,                      -- filename of the raw archive batch
    -- Vig removed multiplicatively across the two sides of the same line.
    -- `mid` keeps the vigged implied price and `last` the raw American odds,
    -- so nothing here is lossy: de-vigging is a derivation, not a replacement.
    prob_devig   REAL,
    -- live      = captured by the polling logger at that instant
    -- backfill:* = reconstructed from a venue history endpoint after the fact
    -- One table, one outcome_id, one query surface - but a backtest that cannot
    -- tell a logged tick from a reconstructed candle is a backtest that will
    -- quietly claim it could have traded a price nobody was quoting.
    source       TEXT NOT NULL DEFAULT 'live',
    -- WHEN THIS ROW WAS WRITTEN, as distinct from when the price existed.
    -- `ts` is the observation time and for anything backfilled it is old by
    -- definition: a 2023 closing line lands with ts two years in the past the
    -- second it is parsed. A retention window keyed on `ts` therefore deletes
    -- paid history on arrival, which is exactly what happened - see
    -- jobs/prune_quotes.py. Every time-based POLICY keys on this column; only
    -- analysis keys on `ts`.
    ingest_ts    REAL
);
CREATE INDEX IF NOT EXISTS ix_quotes_market_ts ON quotes(venue, market_id, ts);
CREATE INDEX IF NOT EXISTS ix_quotes_ts        ON quotes(ts);
CREATE INDEX IF NOT EXISTS ix_quotes_event     ON quotes(venue, event_id, ts);
-- A re-parse deletes its own prior rows by (source, event_id) and the
-- venue-leading index cannot serve that: one backfill payload meant a full
-- scan of every quote in the store.
CREATE INDEX IF NOT EXISTS ix_quotes_src_event ON quotes(source, event_id);

CREATE TABLE IF NOT EXISTS markets (
    venue        TEXT NOT NULL,
    market_id    TEXT NOT NULL,
    event_id     TEXT,
    sport        TEXT DEFAULT 'nfl',
    market_type  TEXT,
    subject      TEXT,
    line         REAL,
    title        TEXT,
    open_ts      REAL,
    close_ts     REAL,       -- when trading stops (kickoff, usually)
    settle_ts    REAL,
    result       TEXT,
    first_seen   REAL,
    last_seen    REAL,
    PRIMARY KEY (venue, market_id)
);
CREATE INDEX IF NOT EXISTS ix_markets_close ON markets(close_ts);

-- Every poll cycle, so gaps in coverage are visible rather than silent.
CREATE TABLE IF NOT EXISTS poll_log (
    ts        REAL NOT NULL,
    venue     TEXT NOT NULL,
    endpoint  TEXT,
    n_markets INTEGER,
    n_quotes  INTEGER,
    ok        INTEGER,
    error     TEXT,
    elapsed_s REAL
);
CREATE INDEX IF NOT EXISTS ix_poll_ts ON poll_log(ts);

-- Where every raw shard lives. The archive is the thing derivations are
-- re-runnable from, so a shard is never deleted - it is moved to object
-- storage and this table stays the answer to "where is that hour?".
CREATE TABLE IF NOT EXISTS raw_shards (
    rel_path        TEXT PRIMARY KEY,   -- venue/YYYY-MM-DD/HH.jsonl.gz
    venue           TEXT NOT NULL,
    day             TEXT NOT NULL,
    hour            TEXT,
    bytes           INTEGER,
    sha256          TEXT,
    state           TEXT NOT NULL,      -- local | remote
    kind            TEXT DEFAULT 'market',  -- market | reference; see RAW_ROTATE_EXEMPT
    remote_bucket   TEXT,
    remote_key      TEXT,
    uploaded_ts     REAL,
    verified_ts     REAL,               -- set ONLY after a byte-for-byte check
    deleted_local_ts REAL,
    first_seen      REAL,
    last_seen       REAL
);
CREATE INDEX IF NOT EXISTS ix_shards_state ON raw_shards(state, day);

-- One row per named source: ingestion, rotation, disk, liveness. This is what
-- an outside observer reads to decide whether the box is healthy, and what the
-- publish job will refuse to run against when it goes stale.
CREATE TABLE IF NOT EXISTS source_health (
    source       TEXT PRIMARY KEY,
    ok           INTEGER NOT NULL,
    detail       TEXT,
    watermark    REAL,          -- newest data this source has, unix seconds
    last_ok_ts   REAL,
    last_fail_ts REAL,
    updated_ts   REAL NOT NULL
);

-- ===================== nflverse: the reference corpus =====================
-- Three layers, per brief 002: raw archive (dated, immutable, on disk under
-- data/raw/nflverse/), this normalized store, and a serving layer later.
--
-- Every normalized row carries data_version - the pull date the bytes came
-- from - so "what did we know on 2026-09-14?" is a WHERE clause, and a stat
-- correction ADDS a version instead of overwriting one. That is invariant #5
-- and the entire reason this is mirrored rather than fetched on demand.

-- What was pulled, when, and what the bytes hashed to. A new row appears only
-- when the content actually changed; an unchanged pull bumps last_checked_ts.
CREATE TABLE IF NOT EXISTS nflverse_versions (
    dataset       TEXT NOT NULL,   -- weekly_stats | pbp | games | ...
    season        INTEGER,         -- NULL for all-season files (ngs, contracts)
    data_version  TEXT NOT NULL,   -- pull date, YYYY-MM-DD
    sha256        TEXT NOT NULL,
    bytes         INTEGER,
    rel_path      TEXT,            -- into raw_shards / data/raw
    rows          INTEGER,         -- normalized rows written, NULL if raw-only
    tier          TEXT,            -- live | offseason
    ingested_ts   REAL NOT NULL,
    last_checked_ts REAL,
    PRIMARY KEY (dataset, season, data_version)
);
CREATE INDEX IF NOT EXISTS ix_nflv_ver ON nflverse_versions(dataset, season,
                                                            ingested_ts);

-- Weekly player stats. The table the priority markets read: receptions,
-- targets and carries were the three with real out-of-sample signal.
CREATE TABLE IF NOT EXISTS nfl_player_week (
    sport         TEXT NOT NULL DEFAULT 'nfl',
    gsis_id       TEXT NOT NULL,   -- resolved AT INGEST, never in analysis code
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    season_type   TEXT NOT NULL,   -- REG | POST
    data_version  TEXT NOT NULL,
    player_name   TEXT,
    position      TEXT,
    team          TEXT,
    opponent      TEXT,
    receptions    REAL,
    targets       REAL,
    receiving_yards REAL,
    receiving_tds REAL,
    target_share  REAL,
    carries       REAL,
    rushing_yards REAL,
    rushing_tds   REAL,
    attempts      REAL,
    completions   REAL,
    passing_yards REAL,
    passing_tds   REAL,
    interceptions REAL,
    fantasy_points_ppr REAL,
{_DEF_DDL}    source        TEXT NOT NULL,
    ingested_ts   REAL NOT NULL,
    PRIMARY KEY (gsis_id, season, week, season_type, data_version)
);
CREATE INDEX IF NOT EXISTS ix_pw_season  ON nfl_player_week(season, week);
CREATE INDEX IF NOT EXISTS ix_pw_player  ON nfl_player_week(gsis_id, season);
CREATE INDEX IF NOT EXISTS ix_pw_version ON nfl_player_week(data_version);

-- Schedules AND closing game lines back to 1999 - free backtest data for the
-- game markets, ingested deliberately rather than as a side effect.
CREATE TABLE IF NOT EXISTS nfl_games (
    sport         TEXT NOT NULL DEFAULT 'nfl',
    game_id       TEXT NOT NULL,
    data_version  TEXT NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER,
    game_type     TEXT,
    gameday       TEXT,
    kickoff_ts    REAL,
    home_team     TEXT,
    away_team     TEXT,
    home_score    REAL,
    away_score    REAL,
    spread_line   REAL,          -- home-relative closing spread
    total_line    REAL,
    home_moneyline REAL,
    away_moneyline REAL,
    over_odds     REAL,
    under_odds    REAL,
    home_spread_odds REAL,
    away_spread_odds REAL,
    roof          TEXT,
    surface       TEXT,
    stadium       TEXT,
    home_coach    TEXT,          -- head coach, the only scheme proxy we have
    away_coach    TEXT,
    source        TEXT NOT NULL,
    ingested_ts   REAL NOT NULL,
    PRIMARY KEY (game_id, data_version)
);
CREATE INDEX IF NOT EXISTS ix_games_season ON nfl_games(season, week);

CREATE TABLE IF NOT EXISTS nfl_snap_counts (
    sport         TEXT NOT NULL DEFAULT 'nfl',
    pfr_player_id TEXT NOT NULL,
    game_id       TEXT NOT NULL,
    data_version  TEXT NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER,
    player        TEXT,
    position      TEXT,
    team          TEXT,
    offense_snaps REAL,
    offense_pct   REAL,
    defense_snaps REAL,
    defense_pct   REAL,
    st_snaps      REAL,
    st_pct        REAL,
    source        TEXT NOT NULL,
    ingested_ts   REAL NOT NULL,
    PRIMARY KEY (pfr_player_id, game_id, data_version)
);
CREATE INDEX IF NOT EXISTS ix_snaps_season ON nfl_snap_counts(season, week);

-- ==================== the join key (brief 003) ====================
-- An OUTCOME is a semantic claim independent of venue. A MARKET is one venue's
-- instrument pointing at one. Cross-venue comparison and CLV against a book you
-- did not bet at both live or die on these two tables agreeing.

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id    TEXT PRIMARY KEY,   -- sha1(key)[:16], see core/outcomes.py
    key           TEXT NOT NULL,      -- the canonical string it hashes
    sport         TEXT NOT NULL,
    season        INTEGER NOT NULL,
    week          INTEGER,            -- NULL for season-long claims
    entity_type   TEXT NOT NULL,      -- player | team | game
    entity_id     TEXT NOT NULL,      -- gsis_id for players, abbr for teams
    stat          TEXT,
    line          REAL,
    side          TEXT NOT NULL,
    push_possible INTEGER NOT NULL,
    event_id      TEXT,               -- canonical nflverse game_id when known
    created_ts    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outcomes_entity ON outcomes(entity_id, season, week);
CREATE INDEX IF NOT EXISTS ix_outcomes_event  ON outcomes(event_id);

-- Every logged market gets a row here, mapped or not. An unmapped market with a
-- reason is a work item; an unmapped market that was silently dropped is a
-- coverage number that lies.
CREATE TABLE IF NOT EXISTS market_outcome (
    venue           TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    outcome_id      TEXT,             -- NULL when unmapped
    method          TEXT,             -- how it was resolved
    confidence      REAL,
    unmapped_reason TEXT,             -- NULL when mapped
    mapped_ts       REAL NOT NULL,
    PRIMARY KEY (venue, market_id)
);
CREATE INDEX IF NOT EXISTS ix_mo_outcome ON market_outcome(outcome_id);
CREATE INDEX IF NOT EXISTS ix_mo_unmapped ON market_outcome(venue, unmapped_reason);

-- Identity, resolved AT INGEST. Analysis code joins on gsis_id and never sees
-- a name; that is the whole point of crosswalking here rather than there.
CREATE TABLE IF NOT EXISTS player_xwalk (
    gsis_id       TEXT PRIMARY KEY,
    display_name  TEXT,
    first_name    TEXT,
    last_name     TEXT,
    position      TEXT,
    last_team     TEXT,
    last_season   INTEGER,
    status        TEXT,
    pfr_id        TEXT,
    espn_id       TEXT,
    sleeper_id    TEXT,
    yahoo_id      TEXT,
    pff_id        TEXT,
    ingested_ts   REAL
);
CREATE INDEX IF NOT EXISTS ix_xwalk_pfr ON player_xwalk(pfr_id);

-- Normalized name -> gsis_id. Many aliases per player; a name that maps to more
-- than one ACTIVE player is ambiguous and must not be guessed.
CREATE TABLE IF NOT EXISTS player_alias (
    alias        TEXT NOT NULL,      -- normalized, see venues/mapping.norm_name
    gsis_id      TEXT NOT NULL,
    source       TEXT NOT NULL,      -- display | short | first_last | ...
    last_season  INTEGER,
    PRIMARY KEY (alias, gsis_id)
);
CREATE INDEX IF NOT EXISTS ix_alias ON player_alias(alias);

-- Settlement is a FACT (invariant #6). Beliefs never land in this table, and a
-- correction restates it under a new data_version rather than editing history.
-- ==================== beliefs (brief 004) ====================
-- Invariant #6: facts and beliefs live in separate stores. Everything above
-- this line may be corrected in place when a source restates. Everything below
-- it is APPEND ONLY and immutable - a prediction that can be edited after the
-- fact is not a prediction, it is a story about one.

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id    INTEGER PRIMARY KEY,   -- surfaced only so rows are ordered
    outcome_id       TEXT NOT NULL,
    model_version    TEXT NOT NULL,
    code_fingerprint TEXT NOT NULL,         -- the build that produced it
    as_of_ts         REAL NOT NULL,         -- nothing later than this was read
    created_ts       REAL NOT NULL,
    -- The DISTRIBUTION, not a scalar (invariant #3). family + params is enough
    -- to rebuild the object and re-ask it anything later.
    family           TEXT NOT NULL,         -- negative_binomial | zero_inflated_gamma
    params_json      TEXT NOT NULL,
    -- Cached answers, so the common queries do not need a rebuild. Derived
    -- from the params above and never independently edited.
    mean             REAL,
    prob_over        REAL,                  -- at the outcome's own line
    push_prob        REAL,
    prior_games      INTEGER,               -- how much evidence is behind it
    shrink_weight    REAL,                  -- weight on the player's own history
    UNIQUE (outcome_id, model_version, as_of_ts)
);
CREATE INDEX IF NOT EXISTS ix_pred_outcome ON predictions(outcome_id, as_of_ts);

-- What a flat-stake paper strategy would have done. FLAT STAKES ONLY: QB<->WR1
-- is ~0.42 and a diagonal covariance systematically over-bets, so Kelly waits
-- for the correlated-sizing brief.
CREATE TABLE IF NOT EXISTS paper_ledger (
    ticket_id      INTEGER PRIMARY KEY,
    outcome_id     TEXT NOT NULL,
    prediction_id  INTEGER NOT NULL,
    venue          TEXT NOT NULL,
    market_id      TEXT NOT NULL,
    side           TEXT NOT NULL,          -- yes | no  (the side actually taken)
    model_prob     REAL NOT NULL,          -- our probability for THAT side
    market_prob    REAL NOT NULL,          -- mid at entry, for THAT side
    best_bid       REAL,
    best_ask       REAL,
    spread         REAL NOT NULL,          -- recorded separately: a mid inside a
                                           -- wide spread is not a tradeable price
    gross_edge     REAL NOT NULL,
    fee            REAL NOT NULL,
    net_edge       REAL NOT NULL,
    stake          REAL NOT NULL,
    entry_ts       REAL NOT NULL,
    kickoff_ts     REAL,
    UNIQUE (outcome_id, prediction_id, side)
);
CREATE INDEX IF NOT EXISTS ix_ledger_entry ON paper_ledger(entry_ts);

-- Checkpoint for the paid backfill. A 55k-credit job that cannot restart from
-- where it stopped is one network blip away from being re-bought.
CREATE TABLE IF NOT EXISTS oddsapi_progress (
    kind        TEXT NOT NULL,       -- props | featured | listing
    key         TEXT NOT NULL,       -- game_id, or the slot's ISO timestamp
    season      INTEGER,
    credits     INTEGER,
    rows        INTEGER,
    status      TEXT NOT NULL,       -- done | empty | failed
    detail      TEXT,
    done_ts     REAL NOT NULL,
    PRIMARY KEY (kind, key)
);

-- The de-vigged consensus per outcome, and how much the books disagreed.
-- Dispersion is a confidence signal: a line every book agrees on is a
-- different object from one where they are 8 points apart.
CREATE TABLE IF NOT EXISTS outcome_benchmark (
    outcome_id   TEXT NOT NULL,
    snapshot_ts  REAL NOT NULL,
    n_books      INTEGER NOT NULL,
    median_devig REAL,
    min_devig    REAL,
    max_devig    REAL,
    dispersion   REAL,              -- max - min across benchmark books
    books        TEXT,
    PRIMARY KEY (outcome_id, snapshot_ts)
);
CREATE INDEX IF NOT EXISTS ix_bench_outcome ON outcome_benchmark(outcome_id);

-- The closing consensus per outcome: one row, the last snapshot at or before
-- kickoff, collapsed to a median across books. Derived entirely from `quotes`,
-- so it is rebuildable and never a second source of truth - but the calibration
-- curve is read off it often enough that recomputing the median every time is
-- the wrong trade.
--
-- `n_bench` is the benchmark books (draftkings, fanduel, betmgm) and `n_all`
-- every book that quoted the outcome. They differ a lot on props: betrivers is
-- the widest prop feed in this archive and it is not a benchmark book, so an
-- outcome can have n_all=6 and n_bench=0. A curve drawn on n_bench alone is
-- drawn on half the data, which is why both are here.
CREATE TABLE IF NOT EXISTS outcome_close (
    outcome_id   TEXT PRIMARY KEY,
    close_ts     REAL NOT NULL,     -- snapshot used; <= kickoff_ts
    kickoff_ts   REAL,
    lead_min     REAL,              -- (kickoff - close) / 60
    p_bench      REAL,              -- median de-vigged across BENCHMARK_BOOKS
    n_bench      INTEGER NOT NULL,
    p_all        REAL,              -- median de-vigged across every book
    n_all        INTEGER NOT NULL,
    dispersion   REAL,              -- max - min across every book
    built_ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_close_p ON outcome_close(p_bench);

CREATE TABLE IF NOT EXISTS outcome_settlement (
    outcome_id   TEXT NOT NULL,
    data_version TEXT NOT NULL,
    result       TEXT NOT NULL,      -- over | under | push | unsettled
    actual       REAL,
    source       TEXT NOT NULL,
    settled_ts   REAL NOT NULL,
    PRIMARY KEY (outcome_id, data_version)
);
"""


def _conn():
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")       # concurrent reads while logging
    c.execute("PRAGMA synchronous=NORMAL")
    return c


# Columns added after the first deploy. SQLite has no "ADD COLUMN IF NOT
# EXISTS", and there is a live database on this box that predates them.
MIGRATIONS = [
    ("raw_shards", "kind", "TEXT DEFAULT 'market'"),
    ("quotes", "source", "TEXT DEFAULT 'live'"),
    ("quotes", "prob_devig", "REAL"),
    ("quotes", "ingest_ts", "REAL"),
    # Hashed when the shard's hour CLOSES, not when it is rotated. See
    # seal_shards(): rotation's own hash is taken seven days later and proves
    # only that the transfer was faithful, never that the bytes were.
    ("raw_shards", "sha256_sealed", "TEXT"),
    ("raw_shards", "sealed_ts", "REAL"),
    # model_prob_yes - mid, on a FIXED axis. `gross_edge` and `net_edge` are
    # positive by construction (the side is chosen so the model is above the
    # market), so they cannot be averaged across tickets - doing so reported
    # 13-18pp of "edge" from a baseline model for two sessions.
    ("paper_ledger", "signed_edge", "REAL"),
    # Denormalised from predictions ON PURPOSE. The ledger is the decision
    # record and "which model decided this" has to be answerable from the row
    # itself, not by a join that a future query might forget. 332 tickets split
    # 171/161 across two builds were averaged into one number because nothing
    # in the row said they were different models.
    ("paper_ledger", "model_version", "TEXT"),
    ("nfl_games", "home_coach", "TEXT"),
    ("nfl_games", "away_coach", "TEXT"),
] + [("nfl_player_week", c, "REAL") for c in DEF_COLS]


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        # After the migrations: these index columns are themselves migrations,
        # so they cannot live in SCHEMA, which runs first.
        c.execute("CREATE INDEX IF NOT EXISTS ix_quotes_ingest "
                  "ON quotes(source, ingest_ts)")


@contextmanager
def db():
    c = _conn()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def record_health(source: str, ok: bool, detail: str = None,
                  watermark: float = None):
    """Upsert one source's health row. Never raises - a health write failing
    must not take down the thing it is reporting on."""
    now = time.time()
    try:
        with db() as c:
            c.execute(
                """INSERT INTO source_health
                     (source, ok, detail, watermark, last_ok_ts, last_fail_ts, updated_ts)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(source) DO UPDATE SET
                     ok=excluded.ok, detail=excluded.detail,
                     watermark=COALESCE(excluded.watermark, source_health.watermark),
                     last_ok_ts=COALESCE(excluded.last_ok_ts, source_health.last_ok_ts),
                     last_fail_ts=COALESCE(excluded.last_fail_ts,
                                           source_health.last_fail_ts),
                     updated_ts=excluded.updated_ts""",
                (source, int(bool(ok)), detail, watermark,
                 now if ok else None, None if ok else now, now))
    except Exception:
        pass


def health(source: str = None):
    with db() as c:
        cols = ("SELECT source, ok, detail, watermark, last_ok_ts, last_fail_ts, "
                "updated_ts FROM source_health")
        if source:
            return c.execute(cols + " WHERE source=?", (source,)).fetchone()
        return c.execute(cols + " ORDER BY source").fetchall()


# ---- disk headroom ---------------------------------------------------------

_disk_cache = [0.0, None]          # (checked_at, free_bytes)


def disk_free_bytes(path: str = None) -> int:
    """Free bytes on the volume holding the raw archive, cached briefly.

    Cached because archive_raw runs thousands of times an hour and a stat per
    call buys nothing - the disk does not empty in thirty seconds.
    """
    now = time.time()
    if _disk_cache[1] is not None and now - _disk_cache[0] < config.DISK_CHECK_EVERY:
        return _disk_cache[1]
    target = path or config.RAW_DIR
    while target and not os.path.isdir(target):
        parent = os.path.dirname(target)
        if parent == target:
            break
        target = parent
    free = shutil.disk_usage(target or ".").free
    _disk_cache[0], _disk_cache[1] = now, free
    return free


def reset_disk_cache():
    _disk_cache[0], _disk_cache[1] = 0.0, None


def disk_headroom_ok() -> bool:
    """Is there room to keep archiving? Records health either way.

    Below the floor the archive stops and everything else keeps going. That is
    the deliberate degradation: a quote row is a few hundred bytes and is what
    the scoreboard reads, a raw payload is a megabyte and is what fills a disk.
    A logger that dies on a full disk loses the hours it exists to capture.
    """
    free = disk_free_bytes()
    gb = free / 1e9
    if gb < config.DISK_MIN_FREE_GB:
        record_health("disk", False,
                      f"{gb:.1f}GB free, floor {config.DISK_MIN_FREE_GB:.1f}GB - "
                      f"raw archiving suspended")
        return False
    warn = " (below warn threshold)" if gb < config.DISK_WARN_FREE_GB else ""
    record_health("disk", True, f"{gb:.1f}GB free{warn}", watermark=free)
    return True


# ---- raw shard manifest ----------------------------------------------------

def shard_rel_path(venue: str, day: str, name: str) -> str:
    return f"{venue}/{day}/{name}"


def note_shard(rel_path: str, **cols):
    """Record or update where one raw shard is. Idempotent by rel_path."""
    parts = rel_path.replace(os.sep, "/").split("/")
    venue = parts[0] if parts else ""
    day = parts[1] if len(parts) > 1 else ""
    # Only the market archive is hour-sharded; a reference mirror is named for
    # its dataset, and stuffing "pbp_2024.parquet" into `hour` helps nobody.
    tail = parts[2] if len(parts) > 2 else None
    hour = tail[:2] if tail and tail.endswith(".jsonl.gz") else None
    fields = {k: v for k, v in cols.items() if v is not None}
    fields.setdefault("state", "local")
    keys = list(fields)
    now = time.time()
    placeholders = ",".join("?" for _ in keys)
    sets = ", ".join(f"{k}=excluded.{k}" for k in keys)
    with db() as c:
        c.execute(
            f"""INSERT INTO raw_shards
                  (rel_path, venue, day, hour, {','.join(keys)}, first_seen, last_seen)
                VALUES (?,?,?,?,{placeholders},?,?)
                ON CONFLICT(rel_path) DO UPDATE SET
                  {sets}, last_seen=excluded.last_seen""",
            (rel_path, venue, day, hour, *[fields[k] for k in keys], now, now))


# Newest data_version wins, not MAX(kickoff_ts). They agree today because no
# 2026 game has moved yet, but flex scheduling exists precisely to move games -
# and it moves them EARLIER as often as later, so a MAX() would eventually hand
# the poller a kickoff that has already passed and call the market cold.
#
# Deliberately TWO indexed queries joined in Python rather than one statement.
# The single-statement form with a ROW_NUMBER() CTE took 10.9 seconds against
# this store, because the window has to be materialized over every game before
# the venue filter can touch it - and this runs on the polling path, where ten
# seconds is most of a `live` tier interval.
_GAMES_SQL = "SELECT game_id, data_version, kickoff_ts FROM nfl_games "              "WHERE kickoff_ts IS NOT NULL"
_MARKET_EVENT_SQL = """
SELECT mo.venue, mo.market_id, o.event_id
  FROM market_outcome mo
  JOIN outcomes o ON o.outcome_id = mo.outcome_id
 WHERE mo.outcome_id IS NOT NULL AND o.event_id IS NOT NULL
"""


def kickoff_map(venues=None) -> dict:
    """{(venue, market_id): kickoff_ts} for every mapped market.

    This is the join the polling tiers key on. A venue's own `close_ts` means
    whatever that venue decided it means - Kalshi closes a prop at GAME END and
    Polymarket closes the same claim at KICKOFF - so tiering on it runs two
    venues at two cadences for one event. The game clock is the thing both
    venues are actually about.

    Pass `venues` on the polling path. Unfiltered this covers 650k markets and
    645k of them are `oddsapi:<book>` rows from the historical backfill -
    markets nothing polls, for games played two years ago.
    """
    sql, args = _MARKET_EVENT_SQL, ()
    if venues:
        vs = tuple(venues)
        sql += " AND mo.venue IN (%s)" % ",".join("?" for _ in vs)
        args = vs
    with db() as c:
        best = {}
        for gid, ver, kick in c.execute(_GAMES_SQL):
            cur = best.get(gid)
            if cur is None or (ver or "") >= cur[0]:
                best[gid] = ((ver or ""), kick)
        out = {}
        for venue, market_id, event_id in c.execute(sql, args):
            hit = best.get(event_id)
            if hit:
                out[(venue, market_id)] = hit[1]
    return out


def sealed_hash(rel_path: str):
    """The hash taken when this shard's hour closed, or None if never sealed."""
    with db() as c:
        row = c.execute("SELECT sha256_sealed FROM raw_shards WHERE rel_path=?",
                        (rel_path,)).fetchone()
    return row[0] if row else None


def audit_shards(root=None) -> dict:
    """Every raw file on disk must have a manifest row. Register any that do not.

    The manifest held ONLY the nflverse mirror: archive_file() registered what
    it wrote and archive_raw() did not, so 163 shards and 2.07 GB of live
    kalshi, polymarket and depth capture existed with no row. Rotation still
    found them - it walks the disk - but locate_shard() could not answer "where
    is this hour?" for anything the logger itself produced, and nothing had a
    write-time hash to be checked against later.

    Self-healing on purpose, and loud on purpose. Registering silently would
    hide a write path that forgot to register; refusing to start would trade a
    manifest gap for a capture outage, which is the worse of the two.
    """
    root = root or config.RAW_DIR
    found, registered = 0, []
    if os.path.isdir(root):
        with db() as c:
            known = {r[0] for r in c.execute("SELECT rel_path FROM raw_shards")}
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(".part"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name),
                                      root).replace(os.sep, "/")
                found += 1
                if rel not in known:
                    registered.append(rel)
    for rel in registered:
        kind = "market" if rel.endswith(".jsonl.gz") else "reference"
        try:
            note_shard(rel, kind=kind, state="local",
                       bytes=os.path.getsize(os.path.join(
                           root, rel.replace("/", os.sep))))
        except Exception:
            pass
    stats = {"on_disk": found, "unregistered": len(registered),
             "registered_now": len(registered)}
    record_health("shard_audit", not registered,
                  f"{found} files on disk, {len(registered)} had no manifest "
                  f"row and were registered", watermark=time.time())
    return dict(stats, paths=registered)


def locate_shard(rel_path: str):
    """Where is this hour of raw data? -> (state, bucket, key, verified_ts)."""
    with db() as c:
        return c.execute("SELECT state, remote_bucket, remote_key, verified_ts "
                         "FROM raw_shards WHERE rel_path=?", (rel_path,)).fetchone()


def shard_counts():
    with db() as c:
        return dict(c.execute("SELECT state, COUNT(*) FROM raw_shards "
                              "GROUP BY state").fetchall())


def archive_raw(venue: str, endpoint: str, payload) -> str:
    """Write the verbatim response to a gzipped JSONL shard, return its name.

    Sharded by UTC hour so a day's archive is a handful of files rather than
    thousands, and so a partial write can never corrupt earlier data.
    """
    # Refuse to start a new archive write below the floor. Returning None is a
    # degraded capture, not a failure: the caller still writes its quote row.
    if not disk_headroom_ok():
        return None
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    d = os.path.join(config.RAW_DIR, venue, day)
    os.makedirs(d, exist_ok=True)
    name = f"{now.strftime('%H')}.jsonl.gz"
    path = os.path.join(d, name)
    rec = {"ts": time.time(), "endpoint": endpoint, "payload": payload}
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    rel = os.path.join(venue, day, name).replace(os.sep, "/")
    # REGISTER THE SHARD. Without this the manifest held only the nflverse
    # mirror - 2.07 GB of live kalshi, polymarket and depth capture had no row
    # at all, so locate_shard() could not answer "where is this hour of raw
    # data?" for anything the logger itself wrote. Once per shard per process,
    # not once per poll: this sits on the hot path of every venue tick.
    if rel not in _shards_seen:
        _shards_seen.add(rel)
        try:
            note_shard(rel, kind="market", state="open")
        except Exception:
            pass          # a manifest write must never fail an archive write
    return rel


# Shards this process has already registered. Bounded by hours x venues, so a
# few hundred entries a week, and the process restarts long before that matters.
_shards_seen = set()

# A shard is hour-sharded, so it is final once its hour is over. Ten minutes of
# quiet is a generous margin against a slow last append.
SEAL_QUIET_SECONDS = 600


def seal_shards(now=None, root=None) -> dict:
    """Hash every shard whose hour has closed, and record it.

    THE POINT: rotate_raw hashes a shard seven days after it was written, so a
    faithful upload of already-corrupt bytes verifies perfectly and then deletes
    the only other copy. The read-back verification proves the TRANSFER. Nothing
    proved the CONTENT. Hashing at the hour boundary - when the file is final
    and still minutes old - is what makes corruption at rest detectable at all.

    Idempotent; skips anything already sealed or still being written to.
    """
    now = time.time() if now is None else now
    root = root or config.RAW_DIR
    stats = {"sealed": 0, "bytes": 0, "skipped": 0, "failed": 0}
    with db() as c:
        rows = c.execute(
            "SELECT rel_path FROM raw_shards WHERE sha256_sealed IS NULL "
            "AND kind='market'").fetchall()
    for (rel,) in rows:
        abs_path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.exists(abs_path):
            stats["skipped"] += 1
            continue
        # Still being appended to? Leave it. Hashing mid-write records the hash
        # of a prefix, which is worse than having no hash at all: it would fail
        # the rotation check every time and teach whoever sees it to ignore it.
        if now - os.path.getmtime(abs_path) < SEAL_QUIET_SECONDS:
            stats["skipped"] += 1
            continue
        try:
            h = hashlib.sha256()
            with open(abs_path, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            note_shard(rel, bytes=os.path.getsize(abs_path),
                       sha256_sealed=h.hexdigest(), sealed_ts=now,
                       state="local")
            stats["sealed"] += 1
            stats["bytes"] += os.path.getsize(abs_path)
        except Exception:
            stats["failed"] += 1
    record_health("seal_shards", stats["failed"] == 0,
                  ", ".join(f"{k}={v}" for k, v in stats.items()),
                  watermark=now)
    return stats


def upsert_outcome(o, event_id=None) -> str:
    """Persist an Outcome and return its id. Idempotent by construction: the id
    IS the hash of the key, so re-inserting the same claim is a no-op."""
    from core.outcomes import MarketType, is_push_possible
    entity_type = {
        MarketType.PLAYER_PROP: "player",
        MarketType.SPREAD: "team",
        MarketType.MONEYLINE: "team",
        MarketType.FUTURE: "team",
        MarketType.TOTAL: "game",
    }.get(o.market_type, "team")
    with db() as c:
        c.execute(
            """INSERT INTO outcomes
                 (outcome_id, key, sport, season, week, entity_type, entity_id,
                  stat, line, side, push_possible, event_id, created_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(outcome_id) DO UPDATE SET
                 event_id=COALESCE(excluded.event_id, outcomes.event_id)""",
            (o.outcome_id, o.key, o.sport.value, o.season, o.week, entity_type,
             o.subject, o.stat.value if o.stat else None, o.line, o.side.value,
             int(is_push_possible(o.line, o.stat)),
             event_id or o.event_id, time.time()))
    return o.outcome_id


def record_mapping(venue, market_id, outcome_id=None, method=None,
                   confidence=None, unmapped_reason=None):
    with db() as c:
        c.execute(
            """INSERT INTO market_outcome
                 (venue, market_id, outcome_id, method, confidence,
                  unmapped_reason, mapped_ts)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(venue, market_id) DO UPDATE SET
                 outcome_id=excluded.outcome_id, method=excluded.method,
                 confidence=excluded.confidence,
                 unmapped_reason=excluded.unmapped_reason,
                 mapped_ts=excluded.mapped_ts""",
            (venue, market_id, outcome_id, method, confidence,
             unmapped_reason, time.time()))


def upsert_outcomes(pairs):
    """The batch form of upsert_outcome. `pairs` is (Outcome, event_id).

    One connection for the whole list. Per-outcome it was a connection, a
    transaction and an fsync each - half a million of them in a full re-derive,
    which is why the first one never finished.
    """
    from core.outcomes import MarketType, is_push_possible
    kinds = {
        MarketType.PLAYER_PROP: "player",
        MarketType.SPREAD: "team",
        MarketType.MONEYLINE: "team",
        MarketType.FUTURE: "team",
        MarketType.TOTAL: "game",
    }
    now = time.time()
    payload = [
        (o.outcome_id, o.key, o.sport.value, o.season, o.week,
         kinds.get(o.market_type, "team"), o.subject,
         o.stat.value if o.stat else None, o.line, o.side.value,
         int(is_push_possible(o.line, o.stat)), eid or o.event_id, now)
        for o, eid in pairs]
    if not payload:
        return 0
    with db() as c:
        c.executemany(
            """INSERT INTO outcomes
                 (outcome_id, key, sport, season, week, entity_type, entity_id,
                  stat, line, side, push_possible, event_id, created_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(outcome_id) DO UPDATE SET
                 event_id=COALESCE(excluded.event_id, outcomes.event_id)""",
            payload)
    return len(payload)


def record_mappings(rows):
    """The batch form. One connection for the whole list rather than one per
    row - at 500k rows the per-row connection was the slowest thing in the
    re-derive by an order of magnitude.

    rows: iterable of (venue, market_id, outcome_id, method, confidence,
                       unmapped_reason)
    """
    now = time.time()
    payload = [(*r, now) for r in rows]
    if not payload:
        return 0
    with db() as c:
        c.executemany(
            """INSERT INTO market_outcome
                 (venue, market_id, outcome_id, method, confidence,
                  unmapped_reason, mapped_ts)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(venue, market_id) DO UPDATE SET
                 outcome_id=excluded.outcome_id, method=excluded.method,
                 confidence=excluded.confidence,
                 unmapped_reason=excluded.unmapped_reason,
                 mapped_ts=excluded.mapped_ts""", payload)
    return len(payload)


class VersionMismatch(RuntimeError):
    """The declared model_version does not carry the running fingerprint.

    A version string that is typed can drift from the code it names, and when
    it does the store cannot tell two models apart. That is not hypothetical:
    `baseline-usage-0.2` was written by TWO different builds (24e5b62d5819 and
    c4479a8b0627, 1,049 rows each), and the paper ledger then averaged 171
    tickets from one and 161 from the other into a single number that described
    neither. Refusing the write is the only place this can be caught, because
    by the time anything reads the table both rows look identical.
    """


def record_prediction(row: dict) -> int:
    """Append one immutable prediction. Returns its id.

    REFUSES when `model_version` does not embed `code_fingerprint`. See
    VersionMismatch.

    There is deliberately no update path. A revised belief is a NEW row with a
    later as_of_ts; the old one stays exactly as it was written, because the
    only way to check whether a model was calibrated is to still have what it
    actually said at the time.
    """
    version = row.get("model_version") or ""
    fingerprint = row.get("code_fingerprint") or ""
    if not fingerprint:
        raise VersionMismatch(
            f"prediction for {row.get('outcome_id')} carries no "
            f"code_fingerprint; model_version={version!r}")
    if fingerprint not in version:
        raise VersionMismatch(
            f"model_version {version!r} does not embed the running "
            f"code_fingerprint {fingerprint!r}. Derive the version from the "
            f"fingerprint (models.baseline.model_version()) rather than "
            f"declaring it - a typed version drifts from its code silently, "
            f"and two builds sharing one label cannot be told apart later.")
    cols = ("outcome_id", "model_version", "code_fingerprint", "as_of_ts",
            "created_ts", "family", "params_json", "mean", "prob_over",
            "push_prob", "prior_games", "shrink_weight")
    with db() as c:
        cur = c.execute(
            f"INSERT OR IGNORE INTO predictions ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            tuple(row.get(k) for k in cols))
        if cur.lastrowid:
            return cur.lastrowid
        got = c.execute(
            "SELECT prediction_id FROM predictions WHERE outcome_id=? AND "
            "model_version=? AND as_of_ts=?",
            (row["outcome_id"], row["model_version"], row["as_of_ts"])).fetchone()
        return got[0] if got else None


def record_ticket(row: dict) -> int:
    cols = ("outcome_id", "prediction_id", "venue", "market_id", "side",
            "model_prob", "market_prob", "best_bid", "best_ask", "spread",
            "gross_edge", "fee", "net_edge", "signed_edge", "stake",
            "entry_ts", "kickoff_ts", "model_version")
    with db() as c:
        cur = c.execute(
            f"INSERT OR IGNORE INTO paper_ledger ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            tuple(row.get(k) for k in cols))
        return cur.lastrowid


def record_settlement(outcome_id, result, actual, data_version, source):
    with db() as c:
        c.execute(
            """INSERT INTO outcome_settlement
                 (outcome_id, data_version, result, actual, source, settled_ts)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(outcome_id, data_version) DO UPDATE SET
                 result=excluded.result, actual=excluded.actual,
                 settled_ts=excluded.settled_ts""",
            (outcome_id, data_version, result, actual, source, time.time()))


def record_settlements(rows):
    """The batch form. rows: (outcome_id, result, actual, data_version, source).

    Settlement runs over every player outcome in the store at once, so the
    per-row connection was ~200k transactions for one pass.
    """
    now = time.time()
    payload = [(oid, ver, res, act, src, now)
               for oid, res, act, ver, src in rows]
    if not payload:
        return 0
    with db() as c:
        c.executemany(
            """INSERT INTO outcome_settlement
                 (outcome_id, data_version, result, actual, source, settled_ts)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(outcome_id, data_version) DO UPDATE SET
                 result=excluded.result, actual=excluded.actual,
                 settled_ts=excluded.settled_ts""", payload)
    return len(payload)


def archive_file(source: str, name: str, data: bytes, day: str = None,
                 kind: str = "reference", strict: bool = True) -> str:
    """Archive a verbatim BINARY payload - a parquet release asset, say.

    archive_raw() is for JSON API responses and re-encodes them into a gzipped
    JSONL shard. A parquet file cannot survive that round trip, and re-encoding
    would break "archive verbatim" anyway: the bytes we keep must be the bytes
    nflverse published, so a parser fix can be replayed against them.

    Partitioned by pull date, never by hour: this is a daily-or-slower mirror,
    and the pull date IS the data_version that makes a stat correction legible.

    `strict` is the difference between a capture and a job. The logger degrades
    when the disk is low because a missed hour of quotes is gone forever. An
    ingest that cannot archive should fail loudly and be re-run - its upstream
    is still sitting there.
    """
    if not disk_headroom_ok():
        if strict:
            raise OSError(
                f"disk below {config.DISK_MIN_FREE_GB:g}GB floor - refusing to "
                f"archive {source}/{name}; free space and re-run")
        return None
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = os.path.join(config.RAW_DIR, source, day)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    # Write-then-rename: a half-written parquet that looks complete is worse
    # than no parquet, and this archive is what everything re-derives from.
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

    rel = f"{source}/{day}/{name}"
    note_shard(rel, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
               state="local", kind=kind)
    return rel


def read_archived(rel_path: str) -> bytes:
    """Read one archived binary shard back off local disk."""
    with open(os.path.join(config.RAW_DIR, *rel_path.split("/")), "rb") as f:
        return f.read()


# ---- nflverse version ledger -----------------------------------------------

def latest_version(dataset: str, season=None):
    """The newest pull of this dataset -> (data_version, sha256, rel_path)."""
    with db() as c:
        return c.execute(
            "SELECT data_version, sha256, rel_path FROM nflverse_versions "
            "WHERE dataset=? AND season IS ? ORDER BY data_version DESC LIMIT 1",
            (dataset, season)).fetchone()


def record_version(dataset: str, season, data_version: str, sha256: str,
                   bytes_: int, rel_path: str, rows=None, tier=None):
    now = time.time()
    with db() as c:
        c.execute(
            """INSERT INTO nflverse_versions
                 (dataset, season, data_version, sha256, bytes, rel_path, rows,
                  tier, ingested_ts, last_checked_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(dataset, season, data_version) DO UPDATE SET
                 sha256=excluded.sha256, bytes=excluded.bytes,
                 rel_path=excluded.rel_path,
                 rows=COALESCE(excluded.rows, nflverse_versions.rows),
                 last_checked_ts=excluded.last_checked_ts""",
            (dataset, season, data_version, sha256, bytes_, rel_path, rows,
             tier, now, now))


def touch_version(dataset: str, season, data_version: str):
    """Upstream was checked and had not changed. Record the check, not a copy."""
    with db() as c:
        c.execute("UPDATE nflverse_versions SET last_checked_ts=? "
                  "WHERE dataset=? AND season IS ? AND data_version=?",
                  (time.time(), dataset, season, data_version))


def versions(dataset: str = None, season=None):
    q = ("SELECT dataset, season, data_version, sha256, bytes, rows, tier, "
         "ingested_ts, last_checked_ts FROM nflverse_versions")
    where, args = [], []
    if dataset:
        where.append("dataset=?")
        args.append(dataset)
    if season is not None:
        where.append("season=?")
        args.append(season)
    if where:
        q += " WHERE " + " AND ".join(where)
    with db() as c:
        return c.execute(q + " ORDER BY dataset, season, data_version",
                         args).fetchall()


def replace_rows(table: str, cols, rows, key_cols):
    """Insert normalized rows, replacing only this (key..., data_version) slice.

    Deliberately NOT a blanket delete-and-reload: an older data_version must
    survive untouched, because proving a backtest used only what was known at
    the time is the whole point of versioning these tables.
    """
    if not rows:
        return 0
    placeholders = ",".join("?" * len(cols))
    with db() as c:
        c.executemany(
            f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
            f"VALUES ({placeholders})", rows)
    return len(rows)


# (venue, market_id) -> (best_bid, best_ask, mid, ts_of_last_written_row).
# In memory only: after a restart the first poll writes everything, which is
# exactly the heartbeat behaviour we want anyway.
_last_written = {}


def reset_quote_state():
    """Drop the dedupe state. Tests and a manual re-seed; nothing else."""
    _last_written.clear()


def should_write(row) -> bool:
    """Write on a price change, and unconditionally every heartbeat.

    Polling every 15-60s writes the same price over and over: storage is cheap
    but a table where 99% of rows are duplicates is slow to query and buries the
    ticks that matter. Dropping the duplicates outright, though, makes "the
    price held steady" and "the logger was down" identical in the quotes table -
    and a missing T-5min row is then indistinguishable from a quiet one, which
    is the row CLV is measured against. poll_log catches a venue-level outage;
    it does not catch one market silently dropping out of discovery.

    So: change rows whenever the price moves, plus a forced row per market every
    QUOTE_HEARTBEAT_SEC. Absence in the quotes table then means absence.
    """
    if not config.QUOTE_DEDUPE:
        return True
    venue = row.get("venue") or ""
    # The Odds API path is snapshot-scheduled, not polled - every row it emits
    # is a deliberate capture at a scheduled moment. Never drop one.
    if any(venue.startswith(x) for x in config.QUOTE_DEDUPE_EXEMPT):
        return True
    key = (venue, row.get("market_id"))
    prev = _last_written.get(key)
    if prev is None:
        return True
    price = (row.get("best_bid"), row.get("best_ask"), row.get("mid"))
    if prev[:3] != price:
        return True
    return (row.get("ts") or time.time()) - prev[3] >= config.QUOTE_HEARTBEAT_SEC


def write_quotes(rows, dedupe=True):
    """Append quote rows. `dedupe` off for backfills.

    The change-plus-heartbeat filter exists because polling writes the same
    price hundreds of times. A history endpoint does not: every candle it
    returns is already one observation per period, and suppressing the
    unchanged ones would punch holes in exactly the quiet stretches a
    liquidity map is trying to measure.
    """
    # State is updated AS the batch is filtered, not after it. Filtering the
    # whole list first meant every row in one call saw the previous call's
    # state, so two rows for the same market in a single batch both got
    # written - invisible for the live logger, which sends one row per market,
    # and wrong the moment anything batches.
    kept = []
    for r in (rows or []):
        if dedupe and not should_write(r):
            continue
        kept.append(r)
        _last_written[(r.get("venue"), r.get("market_id"))] = (
            r.get("best_bid"), r.get("best_ask"), r.get("mid"),
            r.get("ts") or time.time())
    rows = kept
    if not rows:
        return 0
    cols = ("ts","sport","venue","event_id","market_id","market_type","subject",
            "line","side","best_bid","best_ask","mid","last","volume",
            "open_interest","raw_ref","source","prob_devig","ingest_ts")
    # Stamped here from the wall clock, never taken from the caller. A row that
    # could name its own ingestion time could name one in the past, and then a
    # retention window would delete it - the whole point of the column is that
    # it is the one timestamp no upstream feed controls.
    now = time.time()
    with db() as c:
        c.executemany(
            f"INSERT INTO quotes ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            # `source` defaults to 'live' here rather than relying on the
            # column default: an explicit NULL does not trigger a DEFAULT and
            # would fail the NOT NULL constraint instead.
            [tuple(now if k == "ingest_ts"
                   else (r.get("source") or "live") if k == "source"
                   else r.get(k) for k in cols) for r in rows],
        )
    return len(rows)


def upsert_markets(rows):
    if not rows:
        return 0
    now = time.time()
    with db() as c:
        for r in rows:
            c.execute(
                """INSERT INTO markets
                   (venue,market_id,event_id,sport,market_type,subject,line,title,
                    open_ts,close_ts,settle_ts,result,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(venue,market_id) DO UPDATE SET
                     event_id=excluded.event_id, market_type=excluded.market_type,
                     subject=excluded.subject, line=excluded.line, title=excluded.title,
                     close_ts=excluded.close_ts, settle_ts=excluded.settle_ts,
                     result=COALESCE(excluded.result, markets.result),
                     last_seen=excluded.last_seen""",
                (r.get("venue"), r.get("market_id"), r.get("event_id"),
                 r.get("sport","nfl"), r.get("market_type"), r.get("subject"),
                 r.get("line"), r.get("title"), r.get("open_ts"), r.get("close_ts"),
                 r.get("settle_ts"), r.get("result"), now, now),
            )
    return len(rows)


def log_poll(venue, endpoint, n_markets, n_quotes, ok, error, elapsed):
    with db() as c:
        c.execute(
            "INSERT INTO poll_log (ts,venue,endpoint,n_markets,n_quotes,ok,error,elapsed_s)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), venue, endpoint, n_markets, n_quotes, int(ok),
             (str(error)[:500] if error else None), elapsed),
        )
