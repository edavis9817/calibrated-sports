"""The play-level facts every track F analytic reads. FACTS layer, nothing else.

    python -m analytics.spine --build
    python -m analytics.spine --build --season 2025
    python -m analytics.spine --status

Two tables, both components and no derived totals:

`f_play_usage`  one row per (play, player in a usage role). Roles are `passer`,
                `rusher` and `receiver`, so a completed pass writes two rows and
                a handoff writes one. ~1.3M rows over 28 seasons.

`f_team_game_pace`  one row per (game, team, situation), carrying plays,
                inter-snap seconds and drives as COUNTS. Pace is a division at
                read time, over a sample the caller can see.

`f_team_game_units`  one row per (game, offence, situation): the counts behind
                the team unit table - early-down plays and dropbacks, EPA summed
                over the plays it was measured on, sacks-or-hits, and charted
                pressures where participation exists. Defence is the same row
                read from the other side (`opponent`), so the two cannot drift.

WHY A SPINE AT ALL. All five analytics slice the same three facts - who was
involved, what the score state was, what the down and distance was. Deriving
that five times gives five chances for the definitions to drift apart, and this
project has already paid for that once: the settlement rule existed twice and
the copies disagreed on 3,272 outcomes.

THE BUCKETS ARE DEFINED HERE AND ONLY HERE.

    script   trailing_7plus / trailing_1_6 / tied / leading_1_6 / leading_7plus
             on `score_differential` for the team with the ball, at the snap
    down     1st / 2nd / 3rd_short (<=2) / 3rd_med (3-6) / 3rd_long (>=7) /
             4th / other
    neutral  wp in [0.20, 0.80], more than 2:00 left in the half, regulation,
             a run or pass, not a kneel or spike

`neutral` is the garbage-time exclusion and it uses `wp`, the game-state win
probability, NOT `vegas_wp`: the latter carries the closing spread, so a team
that was a heavy favourite is scored as comfortable before a snap is taken.

SEASON RANGE IS A PROPERTY OF THE COLUMN, NOT OF THE TABLE. Every row is
written for every season 1999-2026; `air_yards` is simply null before 2006 and
`is_target` is null-on-incompletion for 2003-2008. The analytics restrict, and
each one declares the range it restricted to. See `docs/F01-pbp-survey.md`.
"""
import argparse
import sys
import time

from analytics import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS f_play_usage (
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    season_type   TEXT    NOT NULL,
    game_id       TEXT    NOT NULL,
    play_id       INTEGER NOT NULL,
    player_id     TEXT    NOT NULL,
    role          TEXT    NOT NULL,   -- passer | rusher | receiver
    team          TEXT,
    opponent      TEXT,
    script        TEXT    NOT NULL,   -- see module docstring
    down_bucket   TEXT    NOT NULL,
    is_neutral    INTEGER NOT NULL,
    shotgun       INTEGER,
    -- components. Never a share, never a per-play rate: analytics.gate would
    -- refuse a stored rate and it would be right to.
    is_target     INTEGER,
    is_reception  INTEGER,
    is_carry      INTEGER,
    is_dropback   INTEGER,
    air_yards     REAL,
    yards         REAL,
    yac           REAL,
    PRIMARY KEY (game_id, play_id, player_id, role)
);
CREATE INDEX IF NOT EXISTS ix_fpu_player ON f_play_usage(player_id, season, week);
CREATE INDEX IF NOT EXISTS ix_fpu_game ON f_play_usage(season, game_id);
CREATE INDEX IF NOT EXISTS ix_fpu_role ON f_play_usage(season, role, script);

CREATE TABLE IF NOT EXISTS f_team_game_pace (
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    season_type  TEXT    NOT NULL,
    game_id      TEXT    NOT NULL,
    team         TEXT    NOT NULL,
    opponent     TEXT,
    situation    TEXT    NOT NULL,   -- all | neutral
    plays        INTEGER NOT NULL,
    seconds      REAL,               -- summed inter-snap deltas within drives
    timed_plays  INTEGER NOT NULL,   -- plays the seconds sum was measured on
    drives       INTEGER NOT NULL,
    PRIMARY KEY (game_id, team, situation)
);
CREATE INDEX IF NOT EXISTS ix_ftgp ON f_team_game_pace(season, team, situation);

-- THE UNIT TABLE'S COUNTS. Offence is `team`; a defence is the rows whose
-- `opponent` it is. `charted_dropbacks` and `pressures` are NULL - not 0 - for
-- a season with no participation file: absence of a feed is not zero pressure,
-- and a zero here would be the silent-zero class written on purpose.
CREATE TABLE IF NOT EXISTS f_team_game_units (
    season            INTEGER NOT NULL,
    week              INTEGER NOT NULL,
    season_type       TEXT    NOT NULL,
    game_id           TEXT    NOT NULL,
    team              TEXT    NOT NULL,   -- the offence
    opponent          TEXT,               -- the defence
    situation         TEXT    NOT NULL,   -- all | neutral
    plays             INTEGER NOT NULL,   -- runs and passes, no kneels/spikes
    dropbacks         INTEGER NOT NULL,   -- `pass` = 1: includes sacks, scrambles
    early_plays       INTEGER NOT NULL,   -- downs 1 and 2
    early_dropbacks   INTEGER NOT NULL,
    epa_plays         INTEGER NOT NULL,   -- plays carrying a non-null epa
    epa_sum           REAL,
    sack_or_hit       INTEGER NOT NULL,   -- dropbacks with sack = 1 or qb_hit = 1
    charted_dropbacks INTEGER,            -- dropbacks with a non-null was_pressure
    pressures         INTEGER,            -- of those, was_pressure = true
    PRIMARY KEY (game_id, team, situation)
);
CREATE INDEX IF NOT EXISTS ix_ftgu ON f_team_game_units(season, team, situation);
CREATE INDEX IF NOT EXISTS ix_ftgu_opp ON f_team_game_units(season, opponent, situation);

CREATE TABLE IF NOT EXISTS f_spine_build (
    season      INTEGER PRIMARY KEY,
    pull_date   TEXT    NOT NULL,
    usage_rows  INTEGER NOT NULL,
    pace_rows   INTEGER NOT NULL,
    plays       INTEGER NOT NULL,
    games       INTEGER NOT NULL,
    built_ts    INTEGER NOT NULL
);
"""

# The columns read from the play-by-play. Named rather than `select(*)` so a
# column that disappears upstream fails here, loudly, instead of arriving as
# nulls in a leaderboard.
PBP_COLUMNS = (
    "season", "week", "season_type", "game_id", "play_id", "posteam", "defteam",
    "score_differential", "down", "ydstogo", "wp", "half_seconds_remaining",
    "game_seconds_remaining", "qtr", "play_type", "qb_kneel", "qb_spike",
    "qb_dropback", "pass_attempt", "rush_attempt", "complete_pass", "shotgun",
    "fixed_drive", "passer_player_id", "receiver_player_id", "rusher_player_id",
    "air_yards", "yards_gained", "yards_after_catch", "receiving_yards",
    "rushing_yards", "passing_yards", "pass", "epa", "sack", "qb_hit",
)

# The participation file that carries `was_pressure`, 2016 onward. Read by
# `build_units` when it exists for the season and never assumed to.
PARTICIPATION = "pbp_participation_{season}.parquet"

SCRIPTS = ("trailing_7plus", "trailing_1_6", "tied", "leading_1_6",
           "leading_7plus")
DOWN_BUCKETS = ("1st", "2nd", "3rd_short", "3rd_med", "3rd_long", "4th", "other")
NEUTRAL_WP = (0.20, 0.80)
NEUTRAL_HALF_SECONDS = 120
EARLY_DOWNS = (1, 2)


def neutral_definition() -> str:
    """The neutral rule IN WORDS, generated from the constants that apply it.

    Every metric that restricts to neutral plays carries this sentence in its
    `unit`, so a page states the definition the number was computed under
    rather than one somebody remembered. Built from `NEUTRAL_WP` and
    `NEUTRAL_HALF_SECONDS`, so editing the rule edits the sentence.
    """
    lo, hi = NEUTRAL_WP
    return ("neutral = offence's win probability between %d%% and %d%% "
            "(game state only, not the betting line), more than %d:%02d left "
            "in the half, regulation, runs and passes only, no kneels or spikes"
            % (round(lo * 100), round(hi * 100), NEUTRAL_HALF_SECONDS // 60,
               NEUTRAL_HALF_SECONDS % 60))


def _expressions():
    import polars as pl
    d = pl.col("score_differential")
    script = (pl.when(d.is_null()).then(pl.lit("tied"))
              .when(d <= -7).then(pl.lit("trailing_7plus"))
              .when(d < 0).then(pl.lit("trailing_1_6"))
              .when(d == 0).then(pl.lit("tied"))
              .when(d < 7).then(pl.lit("leading_1_6"))
              .otherwise(pl.lit("leading_7plus")).alias("script"))
    dn, yd = pl.col("down"), pl.col("ydstogo")
    down_bucket = (pl.when(dn == 1).then(pl.lit("1st"))
                   .when(dn == 2).then(pl.lit("2nd"))
                   .when((dn == 3) & (yd <= 2)).then(pl.lit("3rd_short"))
                   .when((dn == 3) & (yd <= 6)).then(pl.lit("3rd_med"))
                   .when(dn == 3).then(pl.lit("3rd_long"))
                   .when(dn == 4).then(pl.lit("4th"))
                   .otherwise(pl.lit("other")).alias("down_bucket"))
    neutral = (pl.col("wp").is_between(*NEUTRAL_WP)
               & (pl.col("half_seconds_remaining") > NEUTRAL_HALF_SECONDS)
               & (pl.col("qtr") <= 4)
               & pl.col("play_type").is_in(["pass", "run"])
               & (pl.col("qb_kneel").fill_null(0) == 0)
               & (pl.col("qb_spike").fill_null(0) == 0)
               ).fill_null(False).alias("is_neutral")
    return script, down_bucket, neutral


def _scrimmage(path):
    """Scrimmage plays with the buckets attached, as a polars LazyFrame."""
    import polars as pl
    lf = pl.scan_parquet(path)
    missing = set(PBP_COLUMNS) - set(lf.collect_schema().names())
    if missing:
        raise KeyError("play-by-play is missing %s - the spine reads named "
                       "columns so this fails here rather than arriving as "
                       "nulls downstream" % sorted(missing))
    script, down_bucket, neutral = _expressions()
    return (lf.select(PBP_COLUMNS)
            .filter(pl.col("posteam").is_not_null())
            .with_columns(script, down_bucket, neutral))


def build_usage(path):
    """One row per (play, player, role). Returns a polars DataFrame."""
    import polars as pl
    base = _scrimmage(path)
    keep = ["season", "week", "season_type", "game_id", "play_id", "posteam",
            "defteam", "script", "down_bucket", "is_neutral", "shotgun"]

    def arm(id_col, role, **cols):
        out = {"player_id": pl.col(id_col), "role": pl.lit(role)}
        out.update(cols)
        return (base.filter(pl.col(id_col).is_not_null())
                .select(keep + [v.alias(k) for k, v in out.items()]))

    zero, null = pl.lit(0, dtype=pl.Int64), pl.lit(None, dtype=pl.Float64)
    passer = arm("passer_player_id", "passer",
                 is_target=zero, is_reception=zero, is_carry=zero,
                 is_dropback=pl.col("qb_dropback").fill_null(0).cast(pl.Int64),
                 air_yards=pl.col("air_yards").cast(pl.Float64),
                 yards=pl.col("passing_yards").cast(pl.Float64),
                 yac=pl.col("yards_after_catch").cast(pl.Float64))
    receiver = arm("receiver_player_id", "receiver",
                   is_target=pl.col("pass_attempt").fill_null(0).cast(pl.Int64),
                   is_reception=pl.col("complete_pass").fill_null(0).cast(pl.Int64),
                   is_carry=zero, is_dropback=zero,
                   air_yards=pl.col("air_yards").cast(pl.Float64),
                   yards=pl.col("receiving_yards").cast(pl.Float64),
                   yac=pl.col("yards_after_catch").cast(pl.Float64))
    rusher = arm("rusher_player_id", "rusher",
                 is_target=zero, is_reception=zero,
                 is_carry=pl.col("rush_attempt").fill_null(0).cast(pl.Int64),
                 is_dropback=zero, air_yards=null,
                 yards=pl.col("rushing_yards").cast(pl.Float64),
                 yac=null)
    df = pl.concat([passer, receiver, rusher], how="vertical").collect()
    # A lateral can name the same player twice in one play under one role; the
    # primary key would reject the second silently inside executemany, so drop
    # it here where the count is visible.
    return df.unique(subset=["game_id", "play_id", "player_id", "role"],
                     keep="first")


def build_pace(path):
    """One row per (game, team, situation), components only."""
    import polars as pl
    base = _scrimmage(path)
    # Inter-snap seconds: the drop in game_seconds_remaining from the previous
    # play of the SAME DRIVE. Across a drive boundary the gap contains the other
    # team's possession, so it is not this team's pace and is left null.
    timed = (base.sort("game_id", "play_id")
             .with_columns(
                 (pl.col("game_seconds_remaining").shift(1).over(["game_id", "fixed_drive"])
                  - pl.col("game_seconds_remaining")).alias("secs"))
             .with_columns(pl.when(pl.col("secs").is_between(1, 60))
                           .then(pl.col("secs")).otherwise(None).alias("secs")))

    def agg(lf, situation):
        return (lf.group_by(["season", "week", "season_type", "game_id",
                             "posteam", "defteam"])
                .agg(pl.len().alias("plays"),
                     pl.col("secs").sum().alias("seconds"),
                     pl.col("secs").count().alias("timed_plays"),
                     pl.col("fixed_drive").n_unique().alias("drives"))
                .with_columns(pl.lit(situation).alias("situation")))

    scrimmage = timed.filter(pl.col("play_type").is_in(["pass", "run"]))
    return pl.concat([agg(scrimmage, "all"),
                      agg(scrimmage.filter(pl.col("is_neutral")), "neutral")],
                     how="vertical").collect()


def build_units(path, participation_path=None):
    """One row per (game, offence, situation), components only.

    `participation_path` is the season's participation parquet, or None. With
    it, `was_pressure` is joined on (game_id, play_id) and counted over the
    dropbacks it charts; without it both pressure columns are NULL.
    """
    import polars as pl
    plays = (_scrimmage(path)
             .filter(pl.col("play_type").is_in(["pass", "run"]))
             .with_columns(pl.col("play_id").cast(pl.Int64)))
    if participation_path:
        part = (pl.scan_parquet(participation_path)
                .select(pl.col("nflverse_game_id").alias("game_id"),
                        pl.col("play_id").cast(pl.Int64),
                        pl.col("was_pressure"))
                .unique(subset=["game_id", "play_id"], keep="first"))
        plays = plays.join(part, on=["game_id", "play_id"], how="left")
    else:
        plays = plays.with_columns(pl.lit(None, dtype=pl.Boolean)
                                   .alias("was_pressure"))
    db = pl.col("pass").fill_null(0) == 1
    early = pl.col("down").is_in(list(EARLY_DOWNS))

    def agg(lf, situation):
        out = (lf.group_by(["season", "week", "season_type", "game_id",
                            "posteam", "defteam"])
               .agg(pl.len().alias("plays"),
                    db.sum().alias("dropbacks"),
                    early.sum().alias("early_plays"),
                    (early & db).sum().alias("early_dropbacks"),
                    pl.col("epa").is_not_null().sum().alias("epa_plays"),
                    pl.col("epa").sum().alias("epa_sum"),
                    (db & ((pl.col("sack").fill_null(0) == 1)
                           | (pl.col("qb_hit").fill_null(0) == 1)))
                    .sum().alias("sack_or_hit"),
                    (db & pl.col("was_pressure").is_not_null())
                    .sum().alias("charted_dropbacks"),
                    (db & pl.col("was_pressure").fill_null(False))
                    .sum().alias("pressures"))
               .with_columns(pl.lit(situation).alias("situation")))
        if not participation_path:
            out = out.with_columns(
                pl.lit(None, dtype=pl.Int64).alias("charted_dropbacks"),
                pl.lit(None, dtype=pl.Int64).alias("pressures"))
        return out

    return pl.concat([agg(plays, "all"),
                      agg(plays.filter(pl.col("is_neutral")), "neutral")],
                     how="vertical_relaxed").collect()


USAGE_COLS = ("season", "week", "season_type", "game_id", "play_id",
              "player_id", "role", "team", "opponent", "script", "down_bucket",
              "is_neutral", "shotgun", "is_target", "is_reception", "is_carry",
              "is_dropback", "air_yards", "yards", "yac")
PACE_COLS = ("season", "week", "season_type", "game_id", "team", "opponent",
             "situation", "plays", "seconds", "timed_plays", "drives")
UNITS_COLS = ("season", "week", "season_type", "game_id", "team", "opponent",
              "situation", "plays", "dropbacks", "early_plays", "early_dropbacks",
              "epa_plays", "epa_sum", "sack_or_hit", "charted_dropbacks",
              "pressures")


def run_build(seasons=None, verbose=True):
    con = paths.connect()
    con.executescript(SCHEMA)
    files = [f for f in paths.pbp_files() if not seasons or f[0] in seasons]
    if not files:
        raise SystemExit("no play-by-play in the archive - nothing built. A "
                         "build that writes nothing and exits 0 is not a build.")
    stats = {"seasons": 0, "usage_rows": 0, "pace_rows": 0, "units_rows": 0}
    now = int(time.time())
    # Participation by season, from the same mirror. A season without a file
    # builds its units with NULL pressure columns, never zeros.
    part = {s: p for s, p, _pull in paths.seasonal_files(PARTICIPATION)}
    for season, path, pull in files:
        t0 = time.time()
        usage = build_usage(path).rename({"posteam": "team", "defteam": "opponent"})
        pace = build_pace(path).rename({"posteam": "team", "defteam": "opponent"})
        units = build_units(path, part.get(season)).rename(
            {"posteam": "team", "defteam": "opponent"})
        con.execute("DELETE FROM f_play_usage WHERE season=?", (season,))
        con.execute("DELETE FROM f_team_game_pace WHERE season=?", (season,))
        con.execute("DELETE FROM f_team_game_units WHERE season=?", (season,))
        con.executemany(
            "INSERT INTO f_play_usage VALUES (%s)" % ",".join("?" * len(USAGE_COLS)),
            usage.select(USAGE_COLS).rows())
        con.executemany(
            "INSERT INTO f_team_game_pace VALUES (%s)" % ",".join("?" * len(PACE_COLS)),
            pace.select(PACE_COLS).rows())
        con.executemany(
            "INSERT INTO f_team_game_units VALUES (%s)" % ",".join("?" * len(UNITS_COLS)),
            units.select(UNITS_COLS).rows())
        games = con.execute("SELECT COUNT(DISTINCT game_id) FROM f_play_usage "
                            "WHERE season=?", (season,)).fetchone()[0]
        plays = con.execute("SELECT COUNT(DISTINCT game_id || ':' || play_id) "
                            "FROM f_play_usage WHERE season=?", (season,)).fetchone()[0]
        con.execute("INSERT OR REPLACE INTO f_spine_build VALUES (?,?,?,?,?,?,?)",
                    (season, pull, len(usage), len(pace), plays, games, now))
        con.commit()
        stats["seasons"] += 1
        stats["usage_rows"] += len(usage)
        stats["pace_rows"] += len(pace)
        stats["units_rows"] += len(units)
        if verbose:
            print("  %d  usage %7d  pace %5d  units %5d%s  games %4d  %.1fs"
                  % (season, len(usage), len(pace), len(units),
                     "" if season in part else " (no participation)", games,
                     time.time() - t0), flush=True)
    return stats


def status(con=None):
    con = con or paths.connect(read_only=True)
    rows = con.execute(
        "SELECT season, pull_date, usage_rows, pace_rows, plays, games "
        "FROM f_spine_build ORDER BY season").fetchall()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--season", type=int, action="append")
    a = ap.parse_args(argv)
    if a.build:
        s = run_build(seasons=set(a.season) if a.season else None)
        print("built %d seasons: %d usage rows, %d pace rows, %d units rows -> %s"
              % (s["seasons"], s["usage_rows"], s["pace_rows"], s["units_rows"],
                 paths.db_path()))
    if a.status:
        rows = status()
        if not rows:
            raise SystemExit("the spine is empty - run --build")
        print("%6s %11s %10s %8s %8s %6s"
              % ("season", "pull", "usage", "pace", "plays", "games"))
        for r in rows:
            print("%6d %11s %10d %8d %8d %6d" % r)
        print("%6s %11s %10d %8d %8d %6d"
              % ("total", "", sum(r[2] for r in rows), sum(r[3] for r in rows),
                 sum(r[4] for r in rows), sum(r[5] for r in rows)))
    if not (a.build or a.status):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
