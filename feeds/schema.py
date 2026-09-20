"""The feeds store. Facts are versioned by ingestion time; news carries no body.

WHY INJURIES ARE VERSIONED AND NOT OVERWRITTEN. The value of an injury report is that it
says what was KNOWN on Sunday morning, not what turned out to be true. Upstream publishes
a capture time for 2009-2024 (`date_modified`) and REMOVED it in 2025 - measured, not
assumed - so for the seasons the site is actually about, our ingestion time is the only
as-of there is. Row versioning by ingestion time makes
"the report as of T" a query - `valid_from_ts <= T AND (valid_to_ts IS NULL OR
valid_to_ts > T)` - and overwriting would destroy exactly the thing worth having.

WHY NEWS HAS NO BODY COLUMN. Headline, source, timestamp, link. Not a description, not a
summary, not an extract: the only words this project publishes about someone else's
article are the headline they wrote and a link to it. The column does not exist, so a
future parser cannot quietly start filling one.
"""
SPORTS = ("nfl", "cfb")

# The SAME meta shape `cfb.versioning` expects, by name: reuse means conforming to the
# implementation's contract, not parameterising it until it fits. `src_dataset` is the
# feed, `src_season` its integer scope where it has one (a season), `src_part` the
# string scope where it does not (a weather date and endpoint).
META = [("src_dataset", "TEXT NOT NULL"), ("src_season", "INTEGER"),
        ("src_part", "TEXT"), ("src_file_id", "INTEGER NOT NULL"),
        ("row_sha", "TEXT NOT NULL"), ("valid_from_ts", "REAL NOT NULL"),
        ("valid_to_ts", "REAL")]

# table -> (key columns, [(column, type)])
TABLES = {
    # One row per venue per season: coordinates, elevation, timezone and whether it has
    # a roof. `dome` is why a weather row can be absent without anything being missing.
    "venues": (("sport", "season", "venue_id"), [
        ("sport", "TEXT"), ("season", "INTEGER"), ("venue_id", "INTEGER"),
        ("name", "TEXT"), ("city", "TEXT"), ("state", "TEXT"), ("country_code", "TEXT"),
        ("latitude", "REAL"), ("longitude", "REAL"), ("elevation_m", "REAL"),
        ("timezone", "TEXT"), ("dome", "INTEGER"), ("grass", "INTEGER"),
        ("capacity", "INTEGER"),
    ]),
    # The official injury report AS PUBLISHED. `report_status` is the game-status
    # designation; the practice columns are the participation rows beneath it.
    "injury_reports": (("sport", "season", "season_type", "week", "team", "player_id"), [
        ("sport", "TEXT"), ("season", "INTEGER"), ("season_type", "TEXT"),
        ("week", "INTEGER"), ("team", "TEXT"), ("player_id", "TEXT"),
        ("player_name", "TEXT"), ("position", "TEXT"),
        ("report_primary_injury", "TEXT"), ("report_secondary_injury", "TEXT"),
        ("report_status", "TEXT"), ("practice_primary_injury", "TEXT"),
        ("practice_secondary_injury", "TEXT"), ("practice_status", "TEXT"),
        ("game_type", "TEXT"),
        # The upstream capture time WHERE UPSTREAM PUBLISHES ONE: present 2009-2024,
        # REMOVED from 2025. Null means the only as-of is our ingestion stamp.
        ("upstream_asof_ts", "REAL"),
    ]),
    # The weather AT KICKOFF HOUR for one game, from one provider run. Versioned like
    # everything else: a forecast fetched on Tuesday and the archive figure fetched on
    # Monday are two rows about the same kickoff, and which was known when is the point.
    "weather_at_kickoff": (("sport", "game_id", "provider", "kind"), [
        ("sport", "TEXT"), ("game_id", "TEXT"), ("provider", "TEXT"),
        ("kind", "TEXT"),                       # archive | forecast
        ("venue_id", "INTEGER"), ("latitude", "REAL"), ("longitude", "REAL"),
        ("kickoff_ts", "REAL"), ("observed_hour_ts", "REAL"),
        ("temperature_f", "REAL"), ("relative_humidity_pct", "REAL"),
        ("precipitation_in", "REAL"), ("wind_speed_mph", "REAL"),
        ("wind_gusts_mph", "REAL"), ("cloud_cover_pct", "REAL"),
        ("provider_elevation_m", "REAL"),
    ]),
    # Headline, source, timestamp, link. Nothing else - see the module docstring.
    "news_items": (("sport", "feed", "guid"), [
        ("sport", "TEXT"), ("feed", "TEXT"), ("guid", "TEXT"),
        ("title", "TEXT"), ("link", "TEXT"), ("source", "TEXT"),
        ("published_ts", "REAL"),
    ]),
}

# Columns a news row may NEVER have. `feeds.rss` drops them at parse and a test asserts
# both that the table lacks them and that the parser does not return them.
NEWS_FORBIDDEN = ("description", "summary", "content", "body", "text", "excerpt",
                  "content_encoded", "abstract")

CONTROL_DDL = """
CREATE TABLE IF NOT EXISTS feeds_raw_files (
    file_id        INTEGER PRIMARY KEY,
    rel_path       TEXT NOT NULL UNIQUE,
    feed           TEXT NOT NULL,
    scope          TEXT,
    url            TEXT,
    bytes          INTEGER NOT NULL,
    bytes_sha256   TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    fetched_ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_feeds_raw_files_feed ON feeds_raw_files(feed, fetched_ts);

CREATE TABLE IF NOT EXISTS feeds_http_log (
    ts       REAL NOT NULL,
    feed     TEXT NOT NULL,
    url      TEXT NOT NULL,
    status   INTEGER,
    bytes    INTEGER,
    outcome  TEXT
);

CREATE TABLE IF NOT EXISTS feeds_parse_log (
    ts        REAL NOT NULL,
    rel_path  TEXT NOT NULL,
    feed      TEXT NOT NULL,
    rows_in   INTEGER,
    inserted  INTEGER,
    closed    INTEGER,
    unchanged INTEGER,
    dropped   TEXT,
    status    TEXT NOT NULL,
    detail    TEXT
);

CREATE TABLE IF NOT EXISTS feeds_measurements (
    key         TEXT NOT NULL,
    scope       TEXT NOT NULL,
    value       REAL,
    detail      TEXT,
    measured_ts REAL NOT NULL,
    PRIMARY KEY (key, scope)
);

CREATE TABLE IF NOT EXISTS feeds_runs (
    run_id     TEXT PRIMARY KEY,
    argv       TEXT,
    started_ts REAL NOT NULL,
    ended_ts   REAL,
    exit_code  INTEGER
);

CREATE TABLE IF NOT EXISTS feeds_limitations (
    id          TEXT PRIMARY KEY,
    severity    TEXT NOT NULL,
    title       TEXT NOT NULL,
    statement   TEXT NOT NULL,
    consequence TEXT NOT NULL,
    recorded_ts REAL NOT NULL
);
"""


def columns(table):
    return [c for c, _t in TABLES[table][1]]


def keys(table):
    return list(TABLES[table][0])


def _fact_ddl(table):
    key, cols = TABLES[table]
    body = ",\n    ".join([f"{c} {t}" for c, t in cols] + [f"{c} {t}" for c, t in META]
                          + [f"PRIMARY KEY ({', '.join(key)}, valid_from_ts)"])
    return f"CREATE TABLE IF NOT EXISTS {table} (\n    {body}\n);\n"


def ddl() -> str:
    return CONTROL_DDL + "".join(_fact_ddl(t) for t in TABLES)


def news_column_violations():
    """Any news column that would hold someone else's words. Must be empty."""
    return [c for c in columns("news_items")
            if any(f in c.lower() for f in NEWS_FORBIDDEN)]
