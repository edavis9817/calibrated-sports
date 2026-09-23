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
        # ADDED f-05, appended so the columns above keep their positions. Every reading
        # carries how far from the venue it was measured, in two parts:
        #   coord_offset_km - the coordinate asked for, to the venue itself (0.0 = inside
        #     its footprint). NULL where it was never measured, which is every CFB row.
        #   grid_offset_km  - the coordinate asked for, to the grid cell the provider
        #     answered from (`provider_latitude/longitude`, from the response itself).
        ("provider_latitude", "REAL"), ("provider_longitude", "REAL"),
        ("grid_offset_km", "REAL"),
        ("coord_source", "TEXT"), ("coord_ref", "TEXT"), ("coord_offset_km", "REAL"),
        # When a FORECAST was taken, so its horizon (kickoff_ts - fetched_ts) is a query.
        # NULL on archive rows: a reanalysis does not depend on when it was asked for.
        ("fetched_ts", "REAL"),
        # The structure (open_air | fixed | retractable), the game's own roof label as
        # nflverse wrote it, and whether this outdoor reading IS the playing conditions:
        # 1 yes, 0 no (roof shut), NULL not known. NULL on CFB rows (domes are skipped).
        ("roof_type", "TEXT"), ("game_roof", "TEXT"), ("playing_conditions", "INTEGER"),
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

# Columns that SILENCE may not unsay (`cfb.versioning.apply(preserve=...)`): every
# descriptive injury column. Upstream is a weekly, re-published file, and the 09-19
# players release showed a re-publish can omit a field it carried the week before.
# `upstream_asof_ts` is not here but in DATED_BY: it dates the other columns, so it is
# carried forward only while they are unchanged.
PRESERVE = {
    "injury_reports": ("player_name", "position", "report_primary_injury",
                       "report_secondary_injury", "report_status",
                       "practice_primary_injury", "practice_secondary_injury",
                       "practice_status", "game_type"),
}
DATED_BY = {
    "injury_reports": ("upstream_asof_ts",),
}

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

-- Every time upstream went SILENT on a value we hold, and what was done about it.
-- `kept`: the held value was carried into the current version. `not_carried_row_restated`:
-- a capture time was NOT carried, because the row it dated had changed. A withdrawn
-- designation and an omitted one look identical in the file, so this is the record that
-- lets a reader tell the store's answer from upstream's.
CREATE TABLE IF NOT EXISTS feeds_preserved_nulls (
    ts          REAL NOT NULL,
    feed        TEXT NOT NULL,
    src_season  INTEGER,
    src_file_id INTEGER NOT NULL,
    table_name  TEXT NOT NULL,
    row_key     TEXT NOT NULL,
    column_name TEXT NOT NULL,
    held_value  TEXT,
    outcome     TEXT NOT NULL
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


def added_columns(con):
    """[(table, column, type)] the live table lacks. `CREATE TABLE IF NOT EXISTS` never
    alters a table that exists, so a column appended here must be ADDED to a store
    created before it - otherwise every insert names a column the table does not have."""
    out = []
    for table, (_key, cols) in TABLES.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        out += [(table, c, t) for c, t in cols if have and c not in have]
    return out


def ddl() -> str:
    return CONTROL_DDL + "".join(_fact_ddl(t) for t in TABLES)


def news_column_violations():
    """Any news column that would hold someone else's words. Must be empty."""
    return [c for c in columns("news_items")
            if any(f in c.lower() for f in NEWS_FORBIDDEN)]
