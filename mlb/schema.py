"""The MLB store. Game grain, like the NFL spine; season totals are computed at read.

WHY GAME GRAIN AND NOT SEASON ROWS. "Components, not derived totals": the NFL spine
stores player-weeks and the site aggregates. MLB's natural period is a game. A season
line is a SUM over these rows, done in `jobs.ingest_mlb.season_totals`, never stored -
so a restated game corrects every total that includes it, and nothing can disagree.

WHY `team` IS IN EVERY PLAYER KEY. A player can appear for BOTH teams in one game: Danny
Jansen batted for TOR and for BOS in BOS202406260 (a game suspended 06-26 and completed
08-26, after he was traded). Measured in the 2024 bundle - the only (game, player)
duplicate in 73,000 batting rows. A key of (game_id, player_id) would silently drop one
of the two lines.

WHY `stattype` IS IN EVERY STAT KEY. Retrosheet may publish several lines for one player
or team: `value` (its best estimate - always present), `official`, and `lower`/`upper`
bounds where it is uncertain. Every modern season measured so far carries `value` only,
but a key without it would collapse the bounds into one row. Totals read `value`.

NULL IS UNKNOWN, NEVER ZERO. Retrosheet leaves a cell blank where it has no information.
Blank stat cells are stored NULL. The credit FLAGS (dh, ph, pr, wp, lp, save, p_gs, p_gf,
p_cg) are blank in the source where the credit was not given, so for them blank means 0 -
but only in a game Retrosheet has a box score for (`box = 'y'`); otherwise NULL.
"""
SPORT = "mlb"

META = [("src_dataset", "TEXT NOT NULL"), ("src_season", "INTEGER"),
        ("src_part", "TEXT"), ("src_file_id", "INTEGER NOT NULL"),
        ("row_sha", "TEXT NOT NULL"), ("valid_from_ts", "REAL NOT NULL"),
        ("valid_to_ts", "REAL")]

BAT_STATS = ["b_pa", "b_ab", "b_r", "b_h", "b_d", "b_t", "b_hr", "b_rbi", "b_sh", "b_sf",
             "b_hbp", "b_w", "b_iw", "b_k", "b_sb", "b_cs", "b_gdp", "b_xi", "b_roe"]
PIT_STATS = ["p_ipouts", "p_noout", "p_bfp", "p_h", "p_d", "p_t", "p_hr", "p_r", "p_er",
             "p_w", "p_iw", "p_k", "p_hbp", "p_wp", "p_bk", "p_sh", "p_sf", "p_sb", "p_cs",
             "p_pb"]
FLD_STATS = ["d_po", "d_a", "d_e", "d_dp", "d_tp", "d_pb", "d_wp", "d_sb", "d_cs"]
BAT_FLAGS = ["dh", "ph", "pr"]
PIT_FLAGS = ["wp", "lp", "save", "p_gs", "p_gf", "p_cg"]
APPEARANCES = ["g", "g_p", "g_sp", "g_rp", "g_c", "g_1b", "g_2b", "g_3b", "g_ss", "g_lf",
               "g_cf", "g_rf", "g_of", "g_dh", "g_ph", "g_pr"]

# The per-game context every team and player line repeats. Kept on the row, not joined,
# because it is what Retrosheet published ON that line.
CONTEXT = [("season", "INTEGER"), ("date", "TEXT"), ("number", "INTEGER"),
           ("vishome", "TEXT"), ("opp", "TEXT"), ("win", "INTEGER"), ("loss", "INTEGER"),
           ("tie", "INTEGER"), ("gametype", "TEXT"), ("box", "TEXT"), ("pbp", "TEXT")]

I = "INTEGER"
T = "TEXT"

# table -> (key columns, [(column, type)])
TABLES = {
    "mlb_games": (("sport", "game_id"), [
        ("sport", T), ("game_id", T), ("season", I), ("date", T), ("number", I),
        ("visteam", T), ("hometeam", T), ("site", T), ("gametype", T), ("starttime", T),
        ("daynight", T), ("innings", I), ("tiebreaker", I), ("usedh", T),
        ("timeofgame", I), ("attendance", I), ("fieldcond", T), ("precip", T), ("sky", T),
        ("temp", I), ("winddir", T), ("windspeed", I), ("forfeit", T), ("suspend", T),
        ("vruns", I), ("hruns", I), ("wteam", T), ("lteam", T),
        ("wp", T), ("lp", T), ("save", T), ("box", T), ("pbp", T),
    ]),
    "mlb_team_games": (("sport", "game_id", "team", "stattype"), [
        ("sport", T), ("game_id", T), ("team", T), ("stattype", T), *CONTEXT,
        ("lob", I), ("mgr", T),
        *[(c, I) for c in BAT_STATS + PIT_STATS + FLD_STATS],
    ]),
    "mlb_batting": (("sport", "game_id", "player_id", "team", "stattype"), [
        ("sport", T), ("game_id", T), ("player_id", T), ("team", T), ("stattype", T),
        *CONTEXT, ("b_lp", I), ("b_seq", I),
        *[(c, I) for c in BAT_STATS + BAT_FLAGS],
    ]),
    "mlb_pitching": (("sport", "game_id", "player_id", "team", "stattype"), [
        ("sport", T), ("game_id", T), ("player_id", T), ("team", T), ("stattype", T),
        *CONTEXT, ("p_seq", I),
        *[(c, I) for c in PIT_STATS + PIT_FLAGS],
    ]),
    # allplayers: one row per player per team per season, with games by position.
    # A traded player has one row per team. `g` here IS an appearance count - the
    # signal CFB does not have.
    "mlb_player_teams": (("sport", "season", "player_id", "team"), [
        ("sport", T), ("season", I), ("player_id", T), ("team", T),
        ("last", T), ("first", T), ("bat", T), ("throw", T),
        *[(c, I) for c in APPEARANCES], ("first_g", T), ("last_g", T),
    ]),
}

# bundle member (without the season prefix) -> table
MEMBERS = {
    "gameinfo": "mlb_games",
    "teamstats": "mlb_team_games",
    "batting": "mlb_batting",
    "pitching": "mlb_pitching",
    "allplayers": "mlb_player_teams",
}
# In the bundle and archived, deliberately NOT parsed yet: fielding (per position per
# game) and plays (~108 MB/season of play-by-play). Re-derivable from the archive.
NOT_PARSED = ("fielding", "plays")

CONTROL_DDL = """
CREATE TABLE IF NOT EXISTS mlb_raw_files (
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
CREATE INDEX IF NOT EXISTS ix_mlb_raw_files_feed ON mlb_raw_files(feed, fetched_ts);

CREATE TABLE IF NOT EXISTS mlb_http_log (
    ts       REAL NOT NULL,
    url      TEXT NOT NULL,
    status   INTEGER,
    bytes    INTEGER,
    outcome  TEXT
);

CREATE TABLE IF NOT EXISTS mlb_parse_log (
    ts        REAL NOT NULL,
    label     TEXT NOT NULL,
    src_file_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    rows_in   INTEGER,
    inserted  INTEGER,
    closed    INTEGER,
    unchanged INTEGER,
    status    TEXT NOT NULL,
    detail    TEXT
);

CREATE TABLE IF NOT EXISTS mlb_measurements (
    key         TEXT NOT NULL,
    scope       TEXT NOT NULL,
    value       REAL,
    detail      TEXT,
    measured_ts REAL NOT NULL,
    PRIMARY KEY (key, scope)
);

CREATE TABLE IF NOT EXISTS mlb_limitations (
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
    return (f"CREATE TABLE IF NOT EXISTS {table} (\n    {body}\n);\n"
            f"CREATE INDEX IF NOT EXISTS ix_{table}_current ON {table}"
            f"(src_dataset, src_season, valid_to_ts);\n")


def ddl() -> str:
    return CONTROL_DDL + "".join(_fact_ddl(t) for t in TABLES)
