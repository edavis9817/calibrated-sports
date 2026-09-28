"""3. Down-and-distance role: who is out there on 3rd and short, medium, long.

    python -m analytics.role --build-onfield     # explode participation, once
    python -m analytics.role --publish
    python -m analytics.role --show 00-0036355

THE QUESTION. Snap share hides this entirely. A back at 55% of snaps who never
sees third-and-long is a different player from a back at 55% who is the
third-down back, and the season line is the same number.

THIS IS TWO METRICS, NOT ONE, AND THEY ARE NOT INTERCHANGEABLE. The
play-by-play names the player who TOUCHED the ball and nobody else; only
`pbp_participation` names the other twenty-one. So:

    role.touch_share      2009-2026, availability CURRENT
                          share of his team's touches in the bucket
    role.onfield_share    2016-2025, availability HISTORICAL
                          share of his team's plays in the bucket he was on
                          the field for

(each also `.by_season` - see TWO GRAINS below; ranges are derived by the
survey, these are what it returned on 2026-09-27.)

They answer different questions and the second one is the one people mean.
**The on-field metric HAS NO CURRENT SEASON AND NEVER WILL IN-SEASON**:
`pbp_participation` is offseason-tier and refreshes only after the postseason,
and 2026 is simply not on disk. It is published as `historical` and a page must
say so rather than fall back to touches - a metric that quietly changes
definition by season is worse than one that says it cannot answer.

BUCKETS come from `analytics.spine` and nowhere else: 3rd_short (<=2 to go),
3rd_med (3-6), 3rd_long (>=7), plus 1st, 2nd and 4th.

PARTICIPATION IS NOT COMPLETE EVEN WHERE IT EXISTS. `offense_players` is
populated on 91-92% of 2016-2022 plays and 100% of 2023-2025. The build records
the covered play count per game so a share is always over the plays that could
carry it, and `rows` on every estimate says how many that was.

`n` IS GAMES.

THE GAMES A SHARE IS OVER DO NOT DEPEND ON THE BUCKET (a-40). The first
version built each (player, bucket) from the games in which he had a row IN
THAT BUCKET, so a game where he played forty snaps and none of them on 3rd and
short simply was not in his 3rd-and-short share. That is selection on the
numerator: every game with a zero was dropped and the share read high. Measured
on 2025, players with 8+ games (`research/a40_role_zero_games.py`): on-field
dropped 15.6% of player-game-bucket cells and read the median player +7.5pp high
on 3rd and short, +12.7pp on 4th; touch dropped 52.7% of cells and read +21.9pp
high on 3rd and short, +29.2pp on 4th. Now a player's games are fixed first - on-field:
any participation snap in the game; touch: any carry or target in the game - and
every bucket the team ran a play in counts, at zero when he was not in it.

TOUCH SHARE STARTS WHEN TARGETS DO. A touch is a carry or a target, and
2003-2008 name the receiver on completions only, so a touch share there is
carries plus RECEPTIONS - a different quantity under the same name. The touch
kind now requires the target columns the way `script_elasticity.targets` does,
and the survey bounds the range.

TWO GRAINS. `role.<kind>_share` pools the whole range into one number per
player per bucket. `role.<kind>_share.by_season` is the same share per player
per SEASON - what a player card means by "his 3rd-and-short role" - with slice
`<season>|<bucket>`. A season is at most ~20 games, and a percentile bootstrap
over that few blocks under-covers (research/a25_small_n_coverage.py: ~0.5 at two
games while built at 0.95), so the by-season grain uses the cluster-robust t
interval clipped to [0, 1], which that study measured at 0.94-0.97 from two
games up.
"""
import argparse
import sys
import time

from analytics import metrics, paths
from analytics.intervals import ratio_t, share_bootstrap

MIN_GAMES = 8
# A player-season needs two games for the cluster t to have a spread at all;
# one game is an unbounded interval, which the export would drop and count.
MIN_GAMES_SEASON = 2
SEASON_SLICE_KIND = "season|down_bucket"
BUCKETS = ("3rd_short", "3rd_med", "3rd_long", "1st", "2nd", "4th")

SCHEMA = """
CREATE TABLE IF NOT EXISTS f_onfield_game (
    season      INTEGER NOT NULL,
    game_id     TEXT    NOT NULL,
    team        TEXT    NOT NULL,
    player_id   TEXT    NOT NULL,
    down_bucket TEXT    NOT NULL,
    snaps       INTEGER NOT NULL,
    PRIMARY KEY (game_id, team, player_id, down_bucket)
);
CREATE INDEX IF NOT EXISTS ix_fog_player ON f_onfield_game(player_id, season);

CREATE TABLE IF NOT EXISTS f_onfield_team_game (
    season      INTEGER NOT NULL,
    game_id     TEXT    NOT NULL,
    team        TEXT    NOT NULL,
    down_bucket TEXT    NOT NULL,
    plays       INTEGER NOT NULL,   -- plays with a populated participation row
    PRIMARY KEY (game_id, team, down_bucket)
);

CREATE TABLE IF NOT EXISTS f_onfield_build (
    season        INTEGER PRIMARY KEY,
    pull_date     TEXT    NOT NULL,
    plays         INTEGER NOT NULL,   -- participation rows for the season
    plays_covered INTEGER NOT NULL,   -- rows with a non-empty offense_players
    player_rows   INTEGER NOT NULL,
    built_ts      INTEGER NOT NULL
);
"""


# ---------------------------------------------------------------------------
# the on-field build
# ---------------------------------------------------------------------------

def build_onfield(seasons=None, verbose=True):
    """Explode `offense_players` and join it to the spine's down buckets."""
    import polars as pl
    con = paths.connect()
    con.executescript(SCHEMA)
    files = paths.seasonal_files("pbp_participation_{season}.parquet")
    files = [f for f in files if not seasons or f[0] in seasons]
    if not files:
        raise SystemExit("no pbp_participation in the archive - nothing built. "
                         "It is offseason-tier; the live season will not be there.")
    now = int(time.time())
    total = {"seasons": 0, "player_rows": 0}
    for season, path, pull in files:
        t0 = time.time()
        buckets = pl.DataFrame(
            con.execute(
                "SELECT DISTINCT game_id, play_id, down_bucket FROM f_play_usage "
                "WHERE season=?", (season,)).fetchall(),
            schema=["game_id", "play_id", "down_bucket"], orient="row")
        part = pl.scan_parquet(path)
        names = part.collect_schema().names()
        for need in ("nflverse_game_id", "play_id", "possession_team",
                     "offense_players"):
            if need not in names:
                raise KeyError("participation %d has no %s" % (season, need))
        # participation types `play_id` as f64 and the spine as i64. Polars
        # refuses the join rather than casting silently, which is the right
        # behaviour and is why this cast is explicit and named.
        part = (part.select(["nflverse_game_id", "play_id", "possession_team",
                             "offense_players"])
                .rename({"nflverse_game_id": "game_id",
                         "possession_team": "team"})
                .with_columns(pl.col("offense_players").fill_null(""),
                              pl.col("play_id").cast(pl.Int64)))
        n_plays = part.select(pl.len()).collect().item()
        covered = part.filter(pl.col("offense_players").str.len_chars() > 0)
        n_covered = covered.select(pl.len()).collect().item()
        joined = (covered.collect()
                  .join(buckets, on=["game_id", "play_id"], how="inner"))
        team_game = (joined.group_by(["game_id", "team", "down_bucket"])
                     .agg(pl.len().alias("plays"))
                     .with_columns(pl.lit(season).alias("season")))
        exploded = (joined
                    .with_columns(pl.col("offense_players").str.split(";"))
                    .explode("offense_players")
                    .rename({"offense_players": "player_id"})
                    .filter(pl.col("player_id").str.len_chars() > 0)
                    .group_by(["game_id", "team", "player_id", "down_bucket"])
                    .agg(pl.len().alias("snaps"))
                    .with_columns(pl.lit(season).alias("season")))
        con.execute("DELETE FROM f_onfield_game WHERE season=?", (season,))
        con.execute("DELETE FROM f_onfield_team_game WHERE season=?", (season,))
        con.executemany(
            "INSERT INTO f_onfield_game VALUES (?,?,?,?,?,?)",
            exploded.select(["season", "game_id", "team", "player_id",
                             "down_bucket", "snaps"]).rows())
        con.executemany(
            "INSERT INTO f_onfield_team_game VALUES (?,?,?,?,?)",
            team_game.select(["season", "game_id", "team", "down_bucket",
                              "plays"]).rows())
        con.execute("INSERT OR REPLACE INTO f_onfield_build VALUES (?,?,?,?,?,?)",
                    (season, pull, n_plays, n_covered, len(exploded), now))
        con.commit()
        total["seasons"] += 1
        total["player_rows"] += len(exploded)
        if verbose:
            print("  %d  plays %6d  covered %6d (%.3f)  player-rows %7d  %.1fs"
                  % (season, n_plays, n_covered, n_covered / max(n_plays, 1),
                     len(exploded), time.time() - t0), flush=True)
    return total


# ---------------------------------------------------------------------------
# the two metrics
# ---------------------------------------------------------------------------

KINDS = {
    "touch": dict(
        label="Down-and-distance touch share",
        unit=("share of his team's touches in the bucket - the player who "
              "carried or was targeted, which is all the play-by-play knows"),
        basis="pbp", availability="current",
        # The target requirements are `script_elasticity.targets`' own: a
        # touch counts targets, and the survey measures where they exist.
        requires=(("pbp", "down", ""), ("pbp", "ydstogo", ""),
                  ("pbp", "rusher_player_id", "rush_attempt"),
                  ("pbp", "receiver_player_id", "incomplete_pass"),
                  ("pbp", "receiver_player_id", "pass_attempt"))),
    "onfield": dict(
        label="Down-and-distance on-field share",
        unit=("share of his team's plays in the bucket he was on the field "
              "for; participation is offseason-tier, so there is no current "
              "season and there will not be one in-season"),
        basis="participation", availability="historical",
        requires=(("participation", "offense_players", ""),)),
}


def metric_for(kind, by_season=False):
    spec = KINDS[kind]
    if by_season:
        return metrics.Metric(
            key="role.%s_share.by_season" % kind,
            label="%s, by season" % spec["label"],
            unit=("%s; one value per player per season, slice "
                  "'<season>|<bucket>'" % spec["unit"]),
            subject_type="player", block="game", basis=spec["basis"],
            availability=spec["availability"], slice_kind=SEASON_SLICE_KIND,
            shares_denominator="team", requires=spec["requires"])
    return metrics.Metric(
        key="role.%s_share" % kind, label=spec["label"], unit=spec["unit"],
        subject_type="player", block="game", basis=spec["basis"],
        availability=spec["availability"], slice_kind="down_bucket",
        shares_denominator="team",
        requires=spec["requires"])


def _team_plays(con, kind, season_from, season_to):
    """{(game, team, bucket): the team's denominator in that bucket}."""
    if kind == "touch":
        sql = ("SELECT game_id, team, down_bucket, "
               "SUM(is_carry) + SUM(is_target) FROM f_play_usage "
               "WHERE season BETWEEN ? AND ? AND role IN ('rusher','receiver') "
               "GROUP BY game_id, team, down_bucket")
    else:
        sql = ("SELECT game_id, team, down_bucket, plays FROM f_onfield_team_game "
               "WHERE season BETWEEN ? AND ?")
    return {(g, t, b): n or 0
            for g, t, b, n in con.execute(sql, (season_from, season_to))}


def _player_counts(con, kind, season_from, season_to):
    """{(player, game, team): (season, {bucket: his count})} over EVERY game he
    played - a row in any bucket, 'other' included - so a bucket he was absent
    from reads as a zero rather than as a game that did not happen."""
    if kind == "touch":
        sql = ("SELECT player_id, game_id, team, season, down_bucket, "
               "SUM(is_carry) + SUM(is_target) FROM f_play_usage "
               "WHERE season BETWEEN ? AND ? AND role IN ('rusher','receiver') "
               "GROUP BY player_id, game_id, team, season, down_bucket")
    else:
        sql = ("SELECT player_id, game_id, team, season, down_bucket, snaps "
               "FROM f_onfield_game WHERE season BETWEEN ? AND ?")
    out = {}
    for player, game, tm, season, bucket, n in con.execute(
            sql, (season_from, season_to)):
        out.setdefault((player, game, tm), (season, {}))[1][bucket] = n or 0
    return out


def _blocks(con, kind, season_from, season_to):
    """{(player, bucket): {game: (season, his count, team count)}}.

    Every game the player played contributes to every bucket his team ran a
    play in, at zero where he had none. See the module docstring for the
    defect this replaces."""
    team = _team_plays(con, kind, season_from, season_to)
    out = {}
    for (player, game, tm), (season, by_bucket) in _player_counts(
            con, kind, season_from, season_to).items():
        for bucket in BUCKETS:
            d = team.get((game, tm, bucket), 0)
            if d:
                out.setdefault((player, bucket), {})[game] = (
                    season, by_bucket.get(bucket, 0), d)
    return out


def _touch_blocks(con, season_from, season_to):
    return {k: {g: (a, b) for g, (_s, a, b) in v.items()}
            for k, v in _blocks(con, "touch", season_from, season_to).items()}


def _onfield_blocks(con, season_from, season_to):
    return {k: {g: (a, b) for g, (_s, a, b) in v.items()}
            for k, v in _blocks(con, "onfield", season_from, season_to).items()}


def compute(con, kind, season_from, season_to, min_games=MIN_GAMES):
    blocks = (_touch_blocks if kind == "touch" else _onfield_blocks)(
        con, season_from, season_to)
    out = []
    for (player, bucket), by_game in blocks.items():
        if bucket not in BUCKETS or len(by_game) < min_games:
            continue
        e = share_bootstrap(by_game, subject="%s|%s" % (player, bucket))["share"]
        if e.est is not None:
            out.append((player, bucket, e))
    return out


def season_slice(season, bucket):
    return "%d|%s" % (season, bucket)


def compute_by_season(con, kind, season_from, season_to,
                      min_games=MIN_GAMES_SEASON):
    """[(player, '<season>|<bucket>', Estimate)], one per player-season-bucket
    with at least `min_games` games: cluster t over games, clipped to [0, 1]."""
    out = []
    for (player, bucket), by_game in _blocks(
            con, kind, season_from, season_to).items():
        seasons = {}
        for game, (season, a, b) in by_game.items():
            seasons.setdefault(season, {})[game] = (a, b)
        for season, games in seasons.items():
            if len(games) < min_games:
                continue
            e = ratio_t(games, bounds=(0.0, 1.0))
            if e.est is not None:
                out.append((player, season_slice(season, bucket), e))
    return out


def publish(con, kinds=None, verbose=True):
    written = {}
    for kind in (kinds or KINDS):
        for by_season in (False, True):
            m = metric_for(kind, by_season=by_season)
            lo, hi, note = metrics.derive_range(con, m)
            rows = (compute_by_season if by_season else compute)(
                con, kind, lo, hi)
            written[m.key] = metrics.publish(con, m, rows, lo, hi)
            if verbose:
                print("  %-32s %d-%d  %-11s %d players, %d values"
                      % (m.key, lo, hi, m.availability,
                         len({p for p, _s, _e in rows}), written[m.key]),
                      flush=True)
                print("   %s" % note, flush=True)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build-onfield", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--kind", action="append", choices=sorted(KINDS))
    ap.add_argument("--season", type=int, action="append")
    ap.add_argument("--show", help="a gsis_id")
    a = ap.parse_args(argv)
    if a.build_onfield:
        t = build_onfield(seasons=set(a.season) if a.season else None)
        print("built %d seasons, %d player rows" % (t["seasons"], t["player_rows"]))
    if a.publish:
        publish(paths.connect(), a.kind)
    if a.show:
        con = paths.connect(read_only=True)
        rows = con.execute(
            "SELECT v.metric, v.slice, v.est, v.lo, v.hi, v.n, m.availability, "
            "m.season_from, m.season_to FROM f_metric_values v "
            "JOIN f_metrics m USING (metric) WHERE v.subject_id=? "
            "AND v.metric LIKE 'role.%' ORDER BY v.metric, v.slice",
            (a.show,)).fetchall()
        if not rows:
            raise SystemExit("nothing published for %r" % a.show)
        for metric, sl, est, lo, hi, n, av, s0, s1 in rows:
            print("%-32s %-15s %.3f [%.3f, %.3f]  n=%d  %s %d-%d"
                  % (metric, sl, est, lo, hi, n, av, s0, s1))
    if not (a.build_onfield or a.publish or a.show):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
