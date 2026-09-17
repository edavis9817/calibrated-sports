"""Normalisers: one raw parquet frame -> rows for one fact table.

Each returns a `Normalized` with rows in `cfb.schema` column order, a count of
every row dropped and why, and measurements of the file. Nothing is dropped
silently: every drop reason is counted, logged to `cfb_parse_log` and printed.

Upstream column types drift between seasons (`age` is Float64 in 2025 and
String in 2026; every box-score stat is a String), so every column is cast
explicitly and a column missing from an older season becomes null rather than
failing a 23-season backfill.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

from cfb import schema


@dataclass
class Normalized:
    table: str
    rows: list
    dropped: dict = field(default_factory=dict)
    measurements: list = field(default_factory=list)   # (key, value, detail)


class RefusedFile(Exception):
    """The file is structurally wrong in a way a row filter would hide."""


def _col(df, name, dtype=None):
    if name not in df.columns:
        return pl.lit(None, dtype=dtype or pl.Utf8).alias(name)
    return pl.col(name)


def _num(df, name, alias, integer=True):
    """A numeric column from whatever upstream typed it as. '--' and '' become
    null; a string like '12' becomes 12."""
    if name not in df.columns:
        return pl.lit(None, dtype=pl.Int64 if integer else pl.Float64).alias(alias)
    e = pl.col(name).cast(pl.Utf8).str.strip_chars().cast(pl.Float64, strict=False)
    return (e.round(0).cast(pl.Int64) if integer else e).alias(alias)


def _bool(df, name, alias=None):
    if name not in df.columns:
        return pl.lit(None, dtype=pl.Int64).alias(alias or name)
    return pl.col(name).cast(pl.Boolean, strict=False).cast(pl.Int64).alias(alias or name)


def _text(df, name, alias=None):
    return _col(df, name).cast(pl.Utf8).alias(alias or name)


def _iso_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _rows(df, table):
    cols = schema.columns(table)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise AssertionError(f"normaliser for {table} did not produce {missing}")
    return df.select(cols).rows()


def _drop_null_keys(df, table, dropped):
    for k in schema.keys(table):
        n = df[k].null_count()
        if n:
            dropped[f"null_{k}"] = dropped.get(f"null_{k}", 0) + n
            df = df.filter(pl.col(k).is_not_null())
    return df


def _drop_duplicate_keys(df, table, dropped):
    """A key that appears twice in ONE file has no honest single value. Both
    copies go, and the count is reported."""
    key = schema.keys(table)
    dup = df.group_by(key).len().filter(pl.col("len") > 1)
    if dup.height:
        dropped["duplicate_key_rows"] = int(dup["len"].sum())
        df = df.join(dup.select(key), on=key, how="anti")
    return df


def _finish(df, table, dropped):
    df = _drop_null_keys(df, table, dropped)
    df = _drop_duplicate_keys(df, table, dropped)
    return df


# =============================================================================
# games, teams, rosters
# =============================================================================

def games(df, season):
    t = "cfb_games"
    dropped = {}
    out = df.select(
        _num(df, "game_id", "game_id"), _num(df, "season", "season"),
        _num(df, "week", "week"), _text(df, "season_type"),
        _text(df, "start_date"),
        _bool(df, "start_time_tbd"), _bool(df, "completed"), _bool(df, "neutral_site"),
        _bool(df, "conference_game"), _num(df, "venue_id", "venue_id"), _text(df, "venue"),
        _num(df, "home_id", "home_id"), _text(df, "home_team"),
        _text(df, "home_abbreviation"), _text(df, "home_division"),
        _text(df, "home_conference"), _num(df, "home_points", "home_points"),
        _num(df, "away_id", "away_id"), _text(df, "away_team"),
        _text(df, "away_abbreviation"), _text(df, "away_division"),
        _text(df, "away_conference"), _num(df, "away_points", "away_points"),
        _text(df, "status"), _text(df, "playoff_round_name"), _text(df, "playoff_bowl_name"),
    )
    out = out.with_columns(
        pl.col("start_date").map_elements(_iso_ts, return_dtype=pl.Float64).alias("start_ts"))
    bad_ts = out.filter(pl.col("start_date").is_not_null() & pl.col("start_ts").is_null()).height
    if bad_ts:
        dropped["unparseable_start_date_kept_null"] = bad_ts
    out = _finish(out, t, dropped)
    fbs = out.filter((pl.col("home_division") == "fbs") | (pl.col("away_division") == "fbs"))
    m = [("games.rows", out.height, None),
         ("games.completed", int(out["completed"].fill_null(0).sum()), None),
         ("games.fbs_involved", fbs.height, None)]
    return Normalized(t, _rows(out, t), dropped, m)


def teams(df, season):
    t = "cfb_teams"
    dropped = {}
    out = df.select(
        _num(df, "season", "season"), _num(df, "team_id", "team_id"),
        _text(df, "abbreviation"), _text(df, "display_name"),
        _text(df, "short_display_name"), _text(df, "school"), _text(df, "mascot"),
        _text(df, "slug"), _text(df, "division"), _text(df, "classification"),
        _num(df, "conference_id", "conference_id"), _text(df, "conference_name"),
        _text(df, "conference_short_name"), _text(df, "color"),
        _text(df, "alternate_color"), _text(df, "team_logo", "logo"),
        _num(df, "venue_id", "venue_id"), _bool(df, "is_active"),
    )
    out = _finish(out, t, dropped)
    return Normalized(t, _rows(out, t), dropped, [("teams.rows", out.height, None)])


def rosters(df, season):
    t = "cfb_rosters"
    dropped = {}
    out = df.select(
        _num(df, "season", "season"), _num(df, "team_id", "team_id"),
        _num(df, "athlete_id", "athlete_id"), _text(df, "first_name"),
        _text(df, "last_name"), _text(df, "full_name"),
        _text(df, "position_abbreviation", "position"), _text(df, "jersey"),
        _num(df, "height", "height_in", integer=False),
        _num(df, "weight", "weight_lb", integer=False),
        _text(df, "experience_abbreviation", "experience"),
        _num(df, "games_rostered", "games_rostered"),
        _text(df, "status_type", "status"), _text(df, "headshot_href", "headshot_url"),
    )
    out = _finish(out, t, dropped)
    m = [("rosters.rows", out.height, None),
         ("rosters.teams", out["team_id"].n_unique(), None)]
    return Normalized(t, _rows(out, t), dropped, m)


def game_rosters(df, season):
    """Starter flags, and the measurement that makes the appearance gap a fact
    rather than a remark: how often `did_not_play` is ever True, and how many
    team-games carry a starting lineup at all."""
    t = "cfb_game_rosters"
    dropped = {}
    out = df.select(
        _num(df, "game_id", "game_id"), _num(df, "season", "season"),
        _num(df, "week", "week"), _num(df, "team_id", "team_id"),
        _num(df, "athlete_id", "athlete_id"), _text(df, "jersey"),
        _bool(df, "starter"),
    )
    out = _finish(out, t, dropped)
    dnp_true = (int(df["did_not_play"].cast(pl.Boolean, strict=False).fill_null(False).sum())
                if "did_not_play" in df.columns else None)
    tg = out.group_by("game_id", "team_id").agg(pl.col("starter").fill_null(0).sum().alias("s"))
    m = [("game_rosters.rows", out.height, None),
         ("game_rosters.did_not_play_true_rows", dnp_true,
          "column absent" if dnp_true is None else None),
         ("game_rosters.team_games", tg.height, None),
         ("game_rosters.team_games_full_starting_lineup",
          tg.filter(pl.col("s").is_between(20, 26)).height, "20-26 starters flagged"),
         ("game_rosters.team_games_no_starters", tg.filter(pl.col("s") == 0).height, None)]
    return Normalized(t, _rows(out, t), dropped, m)


# =============================================================================
# the box score
# =============================================================================

# category -> [(upstream column, our column, integer?)]. Upstream reuses
# `interceptions` for INTs THROWN (passing) and INTs MADE (interceptions); the
# category is what tells them apart.
BOX_MAP = {
    "rushing": [("rushingAttempts", "rush_att", True), ("rushingYards", "rush_yds", True),
                ("rushingTouchdowns", "rush_td", True)],
    "receiving": [("receptions", "rec", True), ("receivingYards", "rec_yds", True),
                  ("receivingTouchdowns", "rec_td", True)],
    "fumbles": [("fumbles", "fumbles", True), ("fumblesLost", "fumbles_lost", True),
                ("fumblesRecovered", "fumbles_rec", True)],
    "defensive": [("totalTackles", "tkl_total", True), ("soloTackles", "tkl_solo", True),
                  ("sacks", "sacks", False), ("tacklesForLoss", "tfl", False),
                  ("passesDefended", "pass_def", True), ("hurries", "qb_hurries", True),
                  ("defensiveTouchdowns", "def_td", True)],
    "interceptions": [("interceptions", "def_int", True),
                      ("interceptionYards", "def_int_yds", True),
                      ("interceptionTouchdowns", "def_int_td", True)],
    "kickReturns": [("kickReturns", "kr", True), ("kickReturnYards", "kr_yds", True),
                    ("kickReturnTouchdowns", "kr_td", True)],
    "puntReturns": [("puntReturns", "pr", True), ("puntReturnYards", "pr_yds", True),
                    ("puntReturnTouchdowns", "pr_td", True)],
    "punting": [("punts", "punts", True), ("puntYards", "punt_yds", True),
                ("touchbacks", "punt_tb", True), ("puntsInside20", "punt_in20", True)],
}

# PASSING IS SPLIT ACROSS TWO LAYOUTS IN ONE FILE. Most seasons carry named
# columns; 466 of 534 passing rows in 2026 carry them in stat_1..stat_5 instead
# as (C/ATT, YDS, AVG, TD, INT). The order is verified, not assumed: AVG equals
# YDS/ATT on all 466, and stat_4/stat_5 average 0.69/0.28 against the named
# rows' TD/INT of 0.90/0.35 - TD before INT.
PASS_NAMED = ("completions/passingAttempts", "passingYards", "passingTouchdowns",
              "interceptions")
PASS_POSITIONAL = ("stat_1", "stat_2", "stat_4", "stat_5")


def _split(e, i):
    return e.str.split("/").list.get(i, null_on_oob=True).str.strip_chars() \
        .cast(pl.Float64, strict=False).round(0).cast(pl.Int64)


def _str(df, name):
    return pl.col(name).cast(pl.Utf8) if name in df.columns else pl.lit(None, dtype=pl.Utf8)


def player_box(df, season):
    t = "cfb_player_game_box"
    dropped = {}
    key = ["game_id", "athlete_id"]
    base = df.with_columns(_num(df, "game_id", "game_id"), _num(df, "athlete_id", "athlete_id"),
                           _num(df, "team_id", "team_id"))
    for k in key:
        n = base[k].null_count()
        if n:
            dropped[f"null_{k}"] = n
            base = base.filter(pl.col(k).is_not_null())

    dup = base.group_by(key + ["category"]).len().filter(pl.col("len") > 1)
    if dup.height:
        # Two passing lines for one player in one game cannot be merged
        # honestly. Refuse the file rather than pick one.
        raise RefusedFile(f"{dup.height} (game, athlete, category) keys repeat")

    known = set(BOX_MAP) | {"passing", "kicking"}
    cats = base["category"].unique().to_list()
    unknown = sorted(c for c in cats if c not in known)
    if unknown:
        n = base.filter(pl.col("category").is_in(unknown)).height
        dropped["unknown_category_rows"] = n
        base = base.filter(~pl.col("category").is_in(unknown))

    parts = []
    for cat, mapping in BOX_MAP.items():
        sub = base.filter(pl.col("category") == cat)
        if sub.height:
            parts.append(sub.select(key + ["team_id", "athlete_name"]
                                    + [_num(sub, src, dst, integer) for src, dst, integer in mapping]
                                    ).with_columns(pl.lit(cat).alias("category")))

    sub = base.filter(pl.col("category") == "passing")
    positional = 0
    if sub.height:
        named = sub[PASS_NAMED[1]].is_not_null() if PASS_NAMED[1] in sub.columns \
            else pl.Series([False] * sub.height)
        positional = int((~named).sum())
        pick = [pl.when(_str(sub, n).is_not_null()).then(_str(sub, n)).otherwise(_str(sub, p))
                for n, p in zip(PASS_NAMED, PASS_POSITIONAL)]
        parts.append(sub.select(
            *key, "team_id", "athlete_name",
            _split(pick[0], 0).alias("pass_cmp"), _split(pick[0], 1).alias("pass_att"),
            pick[1].cast(pl.Float64, strict=False).round(0).cast(pl.Int64).alias("pass_yds"),
            pick[2].cast(pl.Float64, strict=False).round(0).cast(pl.Int64).alias("pass_td"),
            pick[3].cast(pl.Float64, strict=False).round(0).cast(pl.Int64).alias("pass_int"),
        ).with_columns(pl.lit("passing").alias("category")))

    sub = base.filter(pl.col("category") == "kicking")
    if sub.height:
        fg, xp = _str(sub, "fieldGoalsMade/fieldGoalAttempts"), _str(sub, "extraPointsMade/extraPointAttempts")
        parts.append(sub.select(
            *key, "team_id", "athlete_name",
            _split(fg, 0).alias("fg_made"), _split(fg, 1).alias("fg_att"),
            _split(xp, 0).alias("xp_made"), _split(xp, 1).alias("xp_att"),
        ).with_columns(pl.lit("kicking").alias("category")))

    stat_cols = [c for c in schema.columns(t)
                 if c not in ("game_id", "season", "team_id", "athlete_id", "athlete_name", "categories")]
    if not parts:
        return Normalized(t, [], dropped, [("player_box.rows", 0, None)])
    long = pl.concat(parts, how="diagonal_relaxed")
    for c in stat_cols:
        if c not in long.columns:
            long = long.with_columns(pl.lit(None, dtype=pl.Int64).alias(c))

    # A player carried by two teams in one game is a data error; drop the key.
    teams_per = long.group_by(key).agg(pl.col("team_id").n_unique().alias("n"))
    conflict = teams_per.filter(pl.col("n") > 1)
    if conflict.height:
        dropped["team_conflict_players"] = conflict.height
        long = long.join(conflict.select(key), on=key, how="anti")

    wide = long.group_by(key).agg(
        pl.col("team_id").first(), pl.col("athlete_name").drop_nulls().first(),
        *[pl.col(c).drop_nulls().first() for c in stat_cols],
        pl.col("category").sort().str.join(",").alias("categories"),
    ).with_columns(pl.lit(season, dtype=pl.Int64).alias("season")).sort(key)

    per_cat = base.group_by("category").len().sort("category")
    m = [("player_box.rows", wide.height, None),
         ("player_box.games", wide["game_id"].n_unique(), None),
         ("player_box.passing_rows_positional_layout", positional,
          "passing stats read from stat_1..stat_5"),
         ("player_box.categories", len(per_cat),
          json.dumps(dict(per_cat.rows())))]
    return Normalized(t, _rows(wide, t), dropped, m)


def player_usage(df, season):
    t = "cfb_player_game_usage"
    dropped = {}
    ints = ["rushes", "targets", "receptions", "touches", "opportunities", "first_downs",
            "touchdowns", "explosive_plays", "successful_plays", "rz_rushes", "rz_targets",
            "rz_touches", "rz_touchdowns", "third_down_opportunities",
            "third_down_conversions", "team_targets", "team_touches", "team_first_downs"]
    out = df.select(
        _num(df, "game_id", "game_id"), _num(df, "season", "season"), _num(df, "week", "week"),
        _num(df, "pos_team_id", "team_id"), _num(df, "player_id", "athlete_id"),
        _text(df, "player_name", "athlete_name"), _text(df, "position_group"),
        *[_num(df, c, c) for c in ints],
        _num(df, "rush_yards", "rush_yards", integer=False),
        _num(df, "receiving_yards", "receiving_yards", integer=False),
    )
    # Rows with no player id are players upstream could not identify - mostly
    # real targets ("Carson Brown", 2 targets), some parse debris ("Barika
    # Kpeenu 15 Yd"). They stay in `team_targets` and are attributed to no
    # player, so player targets need not sum to the team's. Measured, then
    # dropped.
    unid = out.filter(pl.col("athlete_id").is_null())
    out = _finish(out, t, dropped)
    m = [("player_usage.rows", out.height, None),
         ("player_usage.games", out["game_id"].n_unique(), None),
         ("player_usage.unattributed_targets", int(unid["targets"].fill_null(0).sum()),
          f"of {int(df['targets'].fill_null(0).sum()) if 'targets' in df.columns else 0} targets"),
         ("player_usage.unattributed_touches", int(unid["touches"].fill_null(0).sum()),
          f"of {int(df['touches'].fill_null(0).sum()) if 'touches' in df.columns else 0} touches")]
    return Normalized(t, _rows(out, t), dropped, m)


def nfl_players(df, season):
    t = "cfb_player_xwalk"
    dropped = {}
    out = df.select(
        _num(df, "espn_id", "espn_athlete_id"), _text(df, "gsis_id"),
        _text(df, "display_name", "nfl_display_name"), _text(df, "last_name", "nfl_last_name"),
        _text(df, "college_name"), _num(df, "rookie_season", "rookie_season"),
    )
    n_null = out["espn_athlete_id"].null_count()
    out = out.filter(pl.col("espn_athlete_id").is_not_null())
    if n_null:
        # Not an error: most NFL players in history carry no ESPN id.
        dropped["no_espn_id"] = n_null
    n_nogsis = out["gsis_id"].null_count()
    if n_nogsis:
        dropped["no_gsis_id"] = n_nogsis
        out = out.filter(pl.col("gsis_id").is_not_null())
    out = _drop_duplicate_keys(out, t, dropped)
    return Normalized(t, _rows(out, t), dropped, [("xwalk.rows", out.height, None)])


NORMALIZERS = {
    "games": games, "teams": teams, "rosters": rosters, "game_rosters": game_rosters,
    "player_box": player_box, "player_usage": player_usage, "nfl_players": nfl_players,
}
