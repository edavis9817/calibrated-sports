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

    role.touch_share      1999-2026, availability CURRENT
                          share of his team's touches in the bucket
    role.onfield_share    2016-2025, availability HISTORICAL
                          share of his team's plays in the bucket he was on
                          the field for

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
"""
import argparse
import sys
import time

from analytics import metrics, paths
from analytics.intervals import share_bootstrap

MIN_GAMES = 8
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
        requires=(("pbp", "down", ""), ("pbp", "ydstogo", ""))),
    "onfield": dict(
        label="Down-and-distance on-field share",
        unit=("share of his team's plays in the bucket he was on the field "
              "for; participation is offseason-tier, so there is no current "
              "season and there will not be one in-season"),
        basis="participation", availability="historical",
        requires=(("participation", "offense_players", ""),)),
}


def metric_for(kind):
    spec = KINDS[kind]
    return metrics.Metric(
        key="role.%s_share" % kind, label=spec["label"], unit=spec["unit"],
        subject_type="player", block="game", basis=spec["basis"],
        availability=spec["availability"], slice_kind="down_bucket",
        requires=spec["requires"])


def _touch_blocks(con, season_from, season_to):
    team = {}
    for game, tm, bucket, n in con.execute(
            "SELECT game_id, team, down_bucket, "
            "SUM(is_carry) + SUM(is_target) FROM f_play_usage "
            "WHERE season BETWEEN ? AND ? AND role IN ('rusher','receiver') "
            "GROUP BY game_id, team, down_bucket", (season_from, season_to)):
        team[(game, tm, bucket)] = n or 0
    out = {}
    for player, game, tm, bucket, n in con.execute(
            "SELECT player_id, game_id, team, down_bucket, "
            "SUM(is_carry) + SUM(is_target) FROM f_play_usage "
            "WHERE season BETWEEN ? AND ? AND role IN ('rusher','receiver') "
            "GROUP BY player_id, game_id, team, down_bucket",
            (season_from, season_to)):
        d = team.get((game, tm, bucket), 0)
        if d:
            out.setdefault((player, bucket), {})[game] = (n or 0, d)
    return out


def _onfield_blocks(con, season_from, season_to):
    team = {}
    for game, tm, bucket, n in con.execute(
            "SELECT game_id, team, down_bucket, plays FROM f_onfield_team_game "
            "WHERE season BETWEEN ? AND ?", (season_from, season_to)):
        team[(game, tm, bucket)] = n or 0
    out = {}
    for player, game, tm, bucket, n in con.execute(
            "SELECT player_id, game_id, team, down_bucket, snaps "
            "FROM f_onfield_game WHERE season BETWEEN ? AND ?",
            (season_from, season_to)):
        d = team.get((game, tm, bucket), 0)
        if d:
            out.setdefault((player, bucket), {})[game] = (n or 0, d)
    return out


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


def publish(con, kinds=None, verbose=True):
    written = {}
    for kind in (kinds or KINDS):
        m = metric_for(kind)
        lo, hi, note = metrics.derive_range(con, m)
        rows = compute(con, kind, lo, hi)
        written[m.key] = metrics.publish(con, m, rows, lo, hi)
        if verbose:
            print("  %-22s %d-%d  %-11s %d players, %d values"
                  % (m.key, lo, hi, m.availability,
                     len({p for p, _s, _e in rows}), written[m.key]), flush=True)
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
            print("%-20s %-10s %.3f [%.3f, %.3f]  n=%d  %s %d-%d"
                  % (metric, sl, est, lo, hi, n, av, s0, s1))
    if not (a.build_onfield or a.publish or a.show):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
