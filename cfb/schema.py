"""The CFB store: one spec per fact table, from which the DDL is generated.

Normalisers must emit exactly these columns in this order - `cfb.versioning`
asserts it - so the DDL and the parser cannot drift apart.

EVERY FACT TABLE IS VERSIONED BY INGESTION TIME (invariants 5 and 8). A row is
valid from the moment we first HELD it (`valid_from_ts`, the fetch time of the
raw file it came from) until a later file no longer carries it unchanged
(`valid_to_ts`). "What did we know at T" is

    valid_from_ts <= T AND (valid_to_ts IS NULL OR valid_to_ts > T)

and the current view is `valid_to_ts IS NULL`. A correction upstream closes one
row and opens another; nothing is overwritten.

COMPONENTS, NOT DERIVED VALUES. Averages, percentages, shares, longs, QBR and
EPA are not stored: shares are recomputed from `targets` / `team_targets`, and
QBR and EPA are someone else's model output rather than a count.

NO AGGREGATE HIT RATES AND NO SETTLEMENT. Per-game grades are permitted only in
a table keyed on game_id. `cfb.guards.scope_violations(TABLES)` must be empty;
`tests/test_ingest_cfb.py` fails otherwise.
"""

SPORT = "cfb"

# (table, key columns, [(column, sqlite type)]) - keys are a subset of columns.
TABLES = {
    "cfb_games": (("game_id",), [
        ("game_id", "INTEGER"), ("season", "INTEGER"), ("week", "INTEGER"),
        ("season_type", "TEXT"), ("start_ts", "REAL"), ("start_time_tbd", "INTEGER"),
        ("completed", "INTEGER"), ("neutral_site", "INTEGER"),
        ("conference_game", "INTEGER"), ("venue_id", "INTEGER"), ("venue", "TEXT"),
        ("home_id", "INTEGER"), ("home_team", "TEXT"), ("home_abbreviation", "TEXT"),
        ("home_division", "TEXT"), ("home_conference", "TEXT"), ("home_points", "INTEGER"),
        ("away_id", "INTEGER"), ("away_team", "TEXT"), ("away_abbreviation", "TEXT"),
        ("away_division", "TEXT"), ("away_conference", "TEXT"), ("away_points", "INTEGER"),
        ("status", "TEXT"), ("playoff_round_name", "TEXT"), ("playoff_bowl_name", "TEXT"),
    ]),
    "cfb_teams": (("season", "team_id"), [
        ("season", "INTEGER"), ("team_id", "INTEGER"), ("abbreviation", "TEXT"),
        ("display_name", "TEXT"), ("short_display_name", "TEXT"), ("school", "TEXT"),
        ("mascot", "TEXT"), ("slug", "TEXT"), ("division", "TEXT"),
        ("classification", "TEXT"), ("conference_id", "INTEGER"),
        ("conference_name", "TEXT"), ("conference_short_name", "TEXT"),
        ("color", "TEXT"), ("alternate_color", "TEXT"), ("logo", "TEXT"),
        ("venue_id", "INTEGER"), ("is_active", "INTEGER"),
    ]),
    "cfb_rosters": (("season", "team_id", "athlete_id"), [
        ("season", "INTEGER"), ("team_id", "INTEGER"), ("athlete_id", "INTEGER"),
        ("first_name", "TEXT"), ("last_name", "TEXT"), ("full_name", "TEXT"),
        ("position", "TEXT"), ("jersey", "TEXT"), ("height_in", "REAL"),
        ("weight_lb", "REAL"), ("experience", "TEXT"), ("games_rostered", "INTEGER"),
        ("status", "TEXT"), ("headshot_url", "TEXT"),
    ]),
    # `starter` is a ONE-SIDED appearance signal: True means the player started.
    # False means nothing - no team-game from 2004 to 2024 flags a starter, and
    # 1,087 of 1,890 in 2025 still carry none. `did_not_play` is deliberately
    # NOT stored: it is False on every row 2004-2026 and a column
    # that is always False reads as "everyone played".
    "cfb_game_rosters": (("game_id", "team_id", "athlete_id"), [
        ("game_id", "INTEGER"), ("season", "INTEGER"), ("week", "INTEGER"),
        ("team_id", "INTEGER"), ("athlete_id", "INTEGER"), ("jersey", "TEXT"),
        ("starter", "INTEGER"),
    ]),
    "cfb_player_game_box": (("game_id", "athlete_id"), [
        ("game_id", "INTEGER"), ("season", "INTEGER"), ("team_id", "INTEGER"),
        ("athlete_id", "INTEGER"), ("athlete_name", "TEXT"),
        ("pass_cmp", "INTEGER"), ("pass_att", "INTEGER"), ("pass_yds", "INTEGER"),
        ("pass_td", "INTEGER"), ("pass_int", "INTEGER"),
        ("rush_att", "INTEGER"), ("rush_yds", "INTEGER"), ("rush_td", "INTEGER"),
        ("rec", "INTEGER"), ("rec_yds", "INTEGER"), ("rec_td", "INTEGER"),
        ("fumbles", "INTEGER"), ("fumbles_lost", "INTEGER"), ("fumbles_rec", "INTEGER"),
        ("tkl_total", "INTEGER"), ("tkl_solo", "INTEGER"), ("sacks", "REAL"),
        ("tfl", "REAL"), ("pass_def", "INTEGER"), ("qb_hurries", "INTEGER"),
        ("def_td", "INTEGER"), ("def_int", "INTEGER"), ("def_int_yds", "INTEGER"),
        ("def_int_td", "INTEGER"),
        ("kr", "INTEGER"), ("kr_yds", "INTEGER"), ("kr_td", "INTEGER"),
        ("pr", "INTEGER"), ("pr_yds", "INTEGER"), ("pr_td", "INTEGER"),
        ("fg_made", "INTEGER"), ("fg_att", "INTEGER"), ("xp_made", "INTEGER"),
        ("xp_att", "INTEGER"), ("punts", "INTEGER"), ("punt_yds", "INTEGER"),
        ("punt_tb", "INTEGER"), ("punt_in20", "INTEGER"),
        ("categories", "TEXT"),
    ]),
    "cfb_player_game_usage": (("game_id", "athlete_id"), [
        ("game_id", "INTEGER"), ("season", "INTEGER"), ("week", "INTEGER"),
        ("team_id", "INTEGER"), ("athlete_id", "INTEGER"), ("athlete_name", "TEXT"),
        ("position_group", "TEXT"),
        ("rushes", "INTEGER"), ("targets", "INTEGER"), ("receptions", "INTEGER"),
        ("touches", "INTEGER"), ("opportunities", "INTEGER"),
        ("rush_yards", "REAL"), ("receiving_yards", "REAL"),
        ("first_downs", "INTEGER"), ("touchdowns", "INTEGER"),
        ("explosive_plays", "INTEGER"), ("successful_plays", "INTEGER"),
        ("rz_rushes", "INTEGER"), ("rz_targets", "INTEGER"), ("rz_touches", "INTEGER"),
        ("rz_touchdowns", "INTEGER"),
        ("third_down_opportunities", "INTEGER"), ("third_down_conversions", "INTEGER"),
        ("team_targets", "INTEGER"), ("team_touches", "INTEGER"),
        ("team_first_downs", "INTEGER"),
    ]),
    # --- CFBD (phase 2). Separate tables per source: a CFBD game and a
    # sportsdataverse game with the same id are two sources' claims, compared
    # by query, never merged on write.
    #
    # Results within hours of the final, ahead of sportsdataverse's lag. Line
    # scores are the verbatim per-period array; elo and win probability are
    # CFBD's model output and are not stored.
    "cfb_cfbd_games": (("game_id",), [
        ("game_id", "INTEGER"), ("season", "INTEGER"), ("week", "INTEGER"),
        ("season_type", "TEXT"), ("start_ts", "REAL"), ("completed", "INTEGER"),
        ("neutral_site", "INTEGER"), ("conference_game", "INTEGER"),
        ("home_id", "INTEGER"), ("home_team", "TEXT"), ("home_classification", "TEXT"),
        ("home_conference", "TEXT"), ("home_points", "INTEGER"),
        ("home_line_scores", "TEXT"),
        ("away_id", "INTEGER"), ("away_team", "TEXT"), ("away_classification", "TEXT"),
        ("away_conference", "TEXT"), ("away_points", "INTEGER"),
        ("away_line_scores", "TEXT"),
    ]),
    # GAME LINES ONLY. One row per (game, provider). CFBD gives no timestamp for
    # either value: `spread` is the provider's last line CFBD holds and
    # `spread_open` its first, which is not the same claim as "the close at
    # kickoff". Which side `spread` is quoted from is checked on every parse
    # against `formattedSpread` (`lines.spread_sign_*`), not assumed.
    "cfb_game_lines": (("game_id", "provider"), [
        ("game_id", "INTEGER"), ("season", "INTEGER"), ("week", "INTEGER"),
        ("season_type", "TEXT"), ("start_ts", "REAL"),
        ("home_id", "INTEGER"), ("home_team", "TEXT"),
        ("away_id", "INTEGER"), ("away_team", "TEXT"),
        # `provider` is canonical (cfb.cfbd_normalize.PROVIDER_CANONICAL);
        # `provider_raw` is the string CFBD wrote.
        ("provider", "TEXT"), ("spread", "REAL"), ("spread_open", "REAL"),
        ("total", "REAL"), ("total_open", "REAL"),
        ("home_moneyline", "REAL"), ("away_moneyline", "REAL"),
        ("provider_raw", "TEXT"),
    ]),
    # --- the exchange probe capture (cfb/probe_promote.py). `capture` marks
    # every row as a probe at non-production cadence; see cfb_limitations.
    "cfb_exchange_markets": (("venue", "market_id"), [
        ("venue", "TEXT"), ("market_id", "TEXT"), ("event_id", "TEXT"),
        ("market_type", "TEXT"), ("subject", "TEXT"), ("line", "REAL"), ("title", "TEXT"),
        ("venue_open_ts", "REAL"), ("venue_close_ts", "REAL"), ("venue_settle_ts", "REAL"),
        ("venue_result", "TEXT"), ("first_seen_ts", "REAL"), ("last_seen_ts", "REAL"),
        ("game_id", "INTEGER"), ("match_note", "TEXT"), ("capture", "TEXT"),
    ]),
    # The last quote STRICTLY BEFORE the game's CFBD kickoff. `age_s` is how long
    # before kickoff that quote was taken; `book_state` says whether the price is
    # a two-sided book or something whose mid means nothing.
    "cfb_exchange_closes": (("venue", "market_id"), [
        ("venue", "TEXT"), ("market_id", "TEXT"), ("game_id", "INTEGER"),
        ("kickoff_ts", "REAL"), ("quote_ts", "REAL"), ("age_s", "REAL"),
        ("best_bid", "REAL"), ("best_ask", "REAL"), ("mid", "REAL"), ("last", "REAL"),
        ("volume", "REAL"), ("open_interest", "REAL"), ("book_state", "TEXT"),
        ("capture", "TEXT"),
    ]),
    # Resolved AT INGEST (CLAUDE.md: "crosswalked at ingest, never in analysis
    # code"). CFBD athlete ids ARE ESPN athlete ids - 20,342 of 22,465 CFBD 2023
    # roster ids appear in ESPN's rosters, 20,210 with the same last name - so
    # this one table serves both CFB sources.
    "cfb_player_xwalk": (("espn_athlete_id",), [
        ("espn_athlete_id", "INTEGER"), ("gsis_id", "TEXT"), ("nfl_display_name", "TEXT"),
        ("nfl_last_name", "TEXT"), ("college_name", "TEXT"),
        ("rookie_season", "INTEGER"),
    ]),
}

# `src_file_id` references cfb_raw_files.file_id rather than repeating the path:
# at 4.3M rows a 95-character path on every row was ~40% of the database.
#
# `src_part` narrows a scope below the season. A sportsdataverse file IS a season,
# so it is NULL there. A CFBD response can be one week ("regular:w3") or a whole
# season ("both"); without it, applying week 3 would close every other week's
# rows as "no longer carried".
META = [("src_dataset", "TEXT NOT NULL"), ("src_season", "INTEGER"),
        ("src_part", "TEXT"),
        ("src_file_id", "INTEGER NOT NULL"), ("row_sha", "TEXT NOT NULL"),
        ("valid_from_ts", "REAL NOT NULL"), ("valid_to_ts", "REAL")]


def columns(table):
    return [c for c, _ in TABLES[table][1]]


def keys(table):
    return list(TABLES[table][0])


def _fact_ddl(table):
    key, cols = TABLES[table]
    body = ",\n    ".join(
        [f"{c} {t}" for c, t in cols]
        + [f"sport TEXT NOT NULL DEFAULT '{SPORT}'"]
        + [f"{c} {t}" for c, t in META]
        + [f"PRIMARY KEY ({', '.join(key)}, valid_from_ts)"])
    return f"CREATE TABLE IF NOT EXISTS {table} (\n    {body}\n);\n"


def _index_ddl(table):
    return (f"CREATE INDEX IF NOT EXISTS ix_{table}_scope "
            f"ON {table}(src_season, src_part, valid_to_ts);\n")


def migrate(conn):
    """Additive changes to tables an earlier version created. Only ever ADDS a
    nullable column and swaps an index; never drops or rewrites data."""
    for table in TABLES:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        for col, typ in TABLES[table][1] + [("src_part", "TEXT")]:
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        conn.execute(f"DROP INDEX IF EXISTS ix_{table}_current")


# Bookkeeping. `cfb_raw_files` is the manifest: every file under cfb/raw has a
# row, and `jobs/ingest_cfb.py --audit` reports any that does not.
CONTROL_DDL = f"""
CREATE TABLE IF NOT EXISTS cfb_raw_files (
    file_id           INTEGER PRIMARY KEY,
    rel_path          TEXT NOT NULL UNIQUE,
    dataset           TEXT NOT NULL,
    season            INTEGER,
    repo              TEXT NOT NULL,
    tag               TEXT NOT NULL,
    asset             TEXT NOT NULL,
    bytes             INTEGER NOT NULL,
    bytes_sha256      TEXT NOT NULL,
    content_sha256    TEXT NOT NULL,
    remote_updated_at TEXT,
    fetched_ts        REAL NOT NULL,
    sport             TEXT NOT NULL DEFAULT '{SPORT}'
);
CREATE INDEX IF NOT EXISTS ix_cfb_raw_files_asset ON cfb_raw_files(dataset, season, fetched_ts);

-- One row per asset looked at, whatever the outcome: a check that found
-- nothing new is still evidence the source was alive.
CREATE TABLE IF NOT EXISTS cfb_fetch_checks (
    ts                REAL NOT NULL,
    dataset           TEXT NOT NULL,
    season            INTEGER,
    asset             TEXT NOT NULL,
    remote_size       INTEGER,
    remote_updated_at TEXT,
    bytes_sha256      TEXT,
    content_sha256    TEXT,
    outcome           TEXT NOT NULL,
    detail            TEXT
);

CREATE TABLE IF NOT EXISTS cfb_http_log (
    ts            REAL NOT NULL,
    url           TEXT NOT NULL,
    status        INTEGER,
    bytes         INTEGER,
    ratelimit_remaining INTEGER
);

CREATE TABLE IF NOT EXISTS cfb_parse_log (
    ts          REAL NOT NULL,
    rel_path    TEXT NOT NULL,
    dataset     TEXT NOT NULL,
    season      INTEGER,
    version_ts  REAL NOT NULL,
    rows_in     INTEGER,
    inserted    INTEGER,
    closed      INTEGER,
    unchanged   INTEGER,
    dropped     TEXT,
    status      TEXT NOT NULL,
    detail      TEXT
);

-- Measured properties of the data, recomputed from each parsed file. The
-- limitations below cite these by key, so a figure on the About page is
-- query-derived rather than typed.
CREATE TABLE IF NOT EXISTS cfb_measurements (
    key          TEXT NOT NULL,
    season       INTEGER NOT NULL,
    value        REAL,
    detail       TEXT,
    src_file     TEXT NOT NULL,
    measured_ts  REAL NOT NULL,
    sport        TEXT NOT NULL DEFAULT '{SPORT}',
    PRIMARY KEY (key, season)
);

-- EVERY CFBD request this job makes, metered or not. The quota is shared with
-- the main clone and with CBBD, so `origin` names the host and checkout, and
-- `--cfbd-status` compares the server's usedCalls with this ledger: the
-- difference is spend this job did not make.
CREATE TABLE IF NOT EXISTS cfbd_requests (
    ts         REAL NOT NULL,
    month      TEXT NOT NULL,
    endpoint   TEXT NOT NULL,
    params     TEXT,
    status     INTEGER,
    metered    INTEGER NOT NULL,
    remaining  INTEGER,
    used       INTEGER,
    bytes      INTEGER,
    file_id    INTEGER,
    run_id     TEXT NOT NULL,
    origin     TEXT NOT NULL,
    outcome    TEXT
);
CREATE INDEX IF NOT EXISTS ix_cfbd_requests_month ON cfbd_requests(month);

-- One row per ingest run. temp_* sizes the process's temp directory at start
-- and end: 24.8 GB accumulated in %TEMP% on 2026-09-16 with no attributed cause,
-- and a before/after figure per run is the cheapest way to rule a job in or out.
CREATE TABLE IF NOT EXISTS cfb_runs (
    run_id            TEXT PRIMARY KEY,
    argv              TEXT,
    started_ts        REAL NOT NULL,
    ended_ts          REAL,
    exit_code         INTEGER,
    temp_dir          TEXT,
    temp_bytes_start  INTEGER,
    temp_files_start  INTEGER,
    temp_bytes_end    INTEGER,
    temp_files_end    INTEGER
);

CREATE TABLE IF NOT EXISTS cfb_limitations (
    id            TEXT PRIMARY KEY,
    sport         TEXT NOT NULL DEFAULT '{SPORT}',
    severity      TEXT NOT NULL,
    title         TEXT NOT NULL,
    statement     TEXT NOT NULL,
    consequence   TEXT NOT NULL,
    evidence_keys TEXT NOT NULL,
    recorded_ts   REAL NOT NULL
);
"""


def ddl() -> str:
    return CONTROL_DDL + "".join(_fact_ddl(t) for t in TABLES)


def index_ddl() -> str:
    return "".join(_index_ddl(t) for t in TABLES)
