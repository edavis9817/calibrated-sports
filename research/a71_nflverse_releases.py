"""a-71: what the newly fetched nflverse releases hold, and whether they agree
with what was already on disk.

    python -m research.a71_nflverse_releases --db <store> --raw <raw dir> \
        [--player-raw <raw dir holding stats_player_week>]

Reads only. `--db` is opened `mode=ro`; the raw directories are read as files.
Nothing here is a model input or a feature - it is a census and a
reconciliation, and every figure quoted from a-71 comes out of this script.

  1. census      rows, seasons and the newest 2026 week per new table
  2. team_week   stats_team_week against the SUM OF PLAYER ROWS in
                 stats_player_week, per team-game, column by column
  3. officials   whether the ten-digit game_id resolves through games.parquet
  4. depth       the change-point table rebuilt into full snapshots and
                 compared with the published file - is the storage lossless
"""
import argparse
import glob
import os
import sqlite3
import sys

import polars as pl

# team-week column -> the player-week column(s) it should be the sum of
PAIRS = (
    ("passing_yards", "passing_yards"), ("rushing_yards", "rushing_yards"),
    ("receiving_yards", "receiving_yards"), ("attempts", "attempts"),
    ("completions", "completions"), ("carries", "carries"), ("targets", "targets"),
    ("receptions", "receptions"), ("passing_tds", "passing_tds"),
    ("rushing_tds", "rushing_tds"), ("passing_interceptions", "passing_interceptions"),
    ("sacks_suffered", "sacks_suffered"), ("fumbles_lost_total", "fumbles_lost_total"),
    ("sack_fumbles_lost", "sack_fumbles_lost"), ("rushing_fumbles_lost", "rushing_fumbles_lost"),
    ("receiving_fumbles_lost", "receiving_fumbles_lost"),
    ("def_interceptions", "def_interceptions"), ("def_sacks", "def_sacks"),
)


def newest(raw, stem, season=None):
    name = f"{stem}_{season}.parquet" if season else f"{stem}.parquet"
    hits = sorted(glob.glob(os.path.join(raw, "nflverse", "*", name)))
    return hits[-1] if hits else None


def ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)


def census(db):
    print("== 1. census")
    c = ro(db)
    have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("nfl_team_week", "nfl_officials", "nfl_depth_chart", "nfl_depth_chart_snapshots"):
        if t not in have:
            print(f"  {t:28s} not in this store")
            continue
        n, lo, hi = c.execute(f"SELECT COUNT(*), MIN(season), MAX(season) FROM {t}").fetchone()
        extra = ""
        if t in ("nfl_team_week", "nfl_officials"):
            wk = c.execute(f"SELECT MAX(week), COUNT(*) FROM {t} WHERE season=2026").fetchone()
            extra = f"  2026: {wk[1]:,} rows, newest week {wk[0]}"
        else:
            d = c.execute(f"SELECT MAX(dt), COUNT(*) FROM {t} WHERE season=2026").fetchone()
            extra = f"  2026: {d[1]:,} rows, newest dt {d[0]}"
        print(f"  {t:28s} {n:>9,} rows  seasons {lo}-{hi}{extra}")
    print("  per dataset in nflverse_versions:")
    for ds, n, lo, hi, rows, mb in c.execute(
            "SELECT dataset, COUNT(*), MIN(season), MAX(season), SUM(rows), SUM(bytes)/1e6 "
            "FROM nflverse_versions GROUP BY 1 ORDER BY 1"):
        print(f"    {ds:22s} {n:>3} files  seasons {lo}-{hi}  rows {rows or 0:>9,}  {mb:6.2f} MB")
    c.close()


def reconcile(team_raw, player_raw, seasons):
    print("\n== 2. stats_team_week against the sum of stats_player_week rows")
    tot = {a: [0, 0, 0, 0] for a, _ in PAIRS}       # compared, differ, team>players, team<players
    fl_by_season, missing, games = [], [], 0
    cle = None
    for s in seasons:
        tf, pf = newest(team_raw, "stats_team_week", s), newest(player_raw, "stats_player_week", s)
        if not tf or not pf:
            missing.append(s)
            continue
        t, p = pl.read_parquet(tf), pl.read_parquet(pf)
        have = [(a, b) for a, b in PAIRS if a in t.columns and b in p.columns]
        ps = (p.group_by(["season", "week", "team"])
              .agg([pl.col(b).cast(pl.Float64).fill_null(0).sum().alias("p_" + a) for a, b in have]))
        j = t.select(["season", "week", "team", *[a for a, _ in have]]).join(
            ps, on=["season", "week", "team"], how="left")
        games += j.height
        unmatched = j.filter(pl.col("p_" + have[0][0]).is_null()).height
        if unmatched:
            print(f"  {s}: {unmatched} team-games with no player rows")
        for a, _ in have:
            d = j.select((pl.col(a).cast(pl.Float64).fill_null(0) - pl.col("p_" + a).fill_null(0)).alias("d"))["d"]
            tot[a][0] += j.height
            tot[a][1] += int((d.abs() > 1e-9).sum())
            tot[a][2] += int((d > 1e-9).sum())
            tot[a][3] += int((d < -1e-9).sum())
            if a == "fumbles_lost_total":
                fl_by_season.append((s, j.height, int((d.abs() > 1e-9).sum()), float(d.sum())))
        if s == 2026:
            cle = j.filter((pl.col("team") == "CLE") & (pl.col("week") == 3)).select(
                "team", "week", "fumbles_lost_total", "p_fumbles_lost_total", "passing_yards",
                "p_passing_yards", "rushing_yards", "p_rushing_yards")
    if not games:
        raise SystemExit("no team-game was compared - check --raw / --player-raw")
    print(f"  seasons compared {seasons[0]}-{seasons[-1]}, missing a file: {missing or 'none'}; "
          f"{games:,} team-games")
    print(f"  {'column':26s} {'compared':>9} {'differ':>7} {'share':>7} {'team>sum':>9} {'team<sum':>9}")
    for a, _ in PAIRS:
        n, d, hi, lo = tot[a]
        if n:
            print(f"  {a:26s} {n:>9,} {d:>7,} {d / n:>7.4f} {hi:>9,} {lo:>9,}")
    print("  fumbles_lost_total by season (team-games, differing, sum of team minus players):")
    for s, n, d, net in fl_by_season:
        print(f"    {s}  {n:>4}  {d:>4}  {net:+.0f}")
    if cle is not None:
        print("  a-64's case, CLE 2026 week 3:", cle.to_dicts())


def officials(db, raw, games_raw):
    print("\n== 3. officials")
    c = ro(db)
    rows = c.execute("SELECT game_id, official_id, position, season, week FROM nfl_officials").fetchall()
    c.close()
    o = pl.DataFrame(rows, schema=["game_id", "official_id", "position", "season", "week"], orient="row")
    src = newest(raw, "officials")
    if src:
        f = pl.read_parquet(src)
        key = ["game_id", "official_id", "position"]
        print(f"  release rows {f.height:,}; stored {o.height:,}; release rows sharing a "
              f"(game, official, position) key with another: "
              f"{f.select(pl.struct(key).is_duplicated().sum()).item()}; "
              f"official_id empty or null: {f.filter(pl.col('official_id').is_null() | (pl.col('official_id') == '')).height}")
    per = o.group_by("game_id").len()
    print("  officials per game:", dict(sorted(per["len"].value_counts().iter_rows())))
    print("  2026 weeks present:", sorted(o.filter(pl.col("season") == 2026)["week"].unique().to_list()))
    gf = newest(games_raw, "games")
    if not gf:
        print("  games.parquet not in --raw; join through old_game_id not measured")
        return
    g = pl.read_parquet(gf)
    col = "old_game_id" if "old_game_id" in g.columns else None
    print(f"  games.parquet columns usable as the join: {[x for x in ('old_game_id', 'gsis') if x in g.columns]}")
    if col:
        g = g.with_columns(pl.col(col).cast(pl.String))
        j = per.join(g.select(col, "season", "game_id"), left_on="game_id", right_on=col, how="left")
        print(f"  games with officials {per.height:,}; resolving to a games.parquet row through "
              f"{col}: {j.filter(pl.col('game_id_right').is_not_null()).height:,}")
        played = g.filter(pl.col("season") >= 2015).filter(pl.col("home_score").is_not_null())
        cov = played.join(per, left_on=col, right_on="game_id", how="left").group_by("season").agg(
            pl.len().alias("played"), pl.col("len").is_not_null().sum().alias("with_officials")).sort("season")
        print("  played games with an officials row, by season:", cov.to_dicts())


def depth(db, raw, season):
    print(f"\n== 4. depth chart {season}: rebuild full snapshots from the change points")
    src = newest(raw, "depth_charts", season)
    f = pl.read_parquet(src)
    key = ["team", "dt", "pos_grp_id", "pos_id", "pos_rank"]
    c = ro(db)
    st = pl.DataFrame(c.execute(
        "SELECT team, dt, pos_grp_id, pos_id, pos_rank, pos_slot, espn_id FROM nfl_depth_chart "
        "WHERE season=?", (season,)).fetchall(),
        schema=["team", "dt", "pos_grp_id", "pos_id", "pos_rank", "pos_slot", "espn_id"], orient="row")
    snaps = [r[0] for r in c.execute(
        "SELECT dt FROM nfl_depth_chart_snapshots WHERE season=? ORDER BY dt", (season,))]
    c.close()
    print(f"  file {os.path.basename(os.path.dirname(src))}: {f.height:,} rows, {f['dt'].n_unique()} snapshots; "
          f"stored {st.height:,} rows, {len(snaps)} snapshots recorded")
    # the chart in force for (team, dt) is the newest stored dt <= dt for that team
    grid = pl.DataFrame({"dt": snaps}).join(pl.DataFrame({"team": st["team"].unique()}), how="cross").sort("dt")
    cp = st.select("team", pl.col("dt").alias("cp")).unique().sort("cp")
    asof = grid.join_asof(cp, left_on="dt", right_on="cp", by="team", strategy="backward")
    rebuilt = asof.join(st.rename({"dt": "cp"}), on=["team", "cp"], how="inner").drop("cp")
    cols = ["team", "dt", "pos_grp_id", "pos_id", "pos_rank", "pos_slot", "espn_id"]
    a = rebuilt.select(cols).with_columns(pl.col("pos_rank").cast(pl.Int64), pl.col("pos_slot").cast(pl.Int64)).sort(cols)
    b = f.select(cols).with_columns(pl.col("pos_rank").cast(pl.Int64), pl.col("pos_slot").cast(pl.Int64)).sort(cols)
    print(f"  rebuilt {a.height:,} rows against {b.height:,} published; identical: {a.equals(b)}")
    if not a.equals(b):
        print("  rows only in the file:", b.join(a, on=key, how="anti").height,
              " only in the rebuild:", a.join(b, on=key, how="anti").height)
    print("  gsis_id null in the file:", f["gsis_id"].null_count(), "of", f.height)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--raw", required=True, help="raw dir holding the new releases")
    ap.add_argument("--player-raw", help="raw dir holding stats_player_week (default --raw)")
    ap.add_argument("--first", type=int, default=1999)
    ap.add_argument("--last", type=int, default=2026)
    a = ap.parse_args()
    census(a.db)
    reconcile(a.raw, a.player_raw or a.raw, list(range(a.first, a.last + 1)))
    officials(a.db, a.raw, a.player_raw or a.raw)
    for s in (2025, 2026):
        depth(a.db, a.raw, s)


if __name__ == "__main__":
    main()
