import polars as pl, numpy as np

def season_level(path, min_col, min_val, metrics):
    d = pl.read_parquet(path).filter(pl.col("season_type") == "REG")
    # NGS ships week==0 rows as the season aggregate; fall back to averaging weeks if absent
    agg = d.filter(pl.col("week") == 0)
    if agg.height == 0:
        agg = d.group_by(["season", "player_gsis_id"]).agg(
            [pl.col(m).mean() for m in metrics] +
            [pl.col(min_col).sum(), pl.col("team_abbr").mode().first(),
             pl.col("player_display_name").first()])
    return agg.filter(pl.col(min_col) >= min_val)

def transfer_table(df, label, metrics, groups):
    nxt = df.with_columns((pl.col("season") - 1).alias("prev_season"))
    pairs = df.join(
        nxt.select(["prev_season", "player_gsis_id", "team_abbr"] + metrics),
        left_on=["season", "player_gsis_id"], right_on=["prev_season", "player_gsis_id"],
        suffix="_next")
    pairs = pairs.with_columns((pl.col("team_abbr") == pl.col("team_abbr_next")).alias("same_team"))
    stay = pairs.filter(pl.col("same_team")); move = pairs.filter(~pl.col("same_team"))
    print(f"\n=== {label}: year-over-year r  (stayed n={stay.height}, changed team n={move.height}) ===")
    print(f"{'metric':<38}{'STAYED':>9}{'MOVED':>9}{'retained':>10}")
    print("-" * 66)
    for gname, glist in groups.items():
        print(f"  [{gname}]")
        for m in glist:
            def c(sub):
                a, b = sub[m].to_numpy(), sub[m + "_next"].to_numpy()
                ok = ~(np.isnan(a) | np.isnan(b))
                return float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 10 else float("nan")
            rs, rm = c(stay), c(move)
            print(f"  {m:<36}{rs:>9.3f}{rm:>9.3f}{(rm/rs if rs>0 else float('nan')):>9.0%}")

rec_metrics = ["avg_separation","avg_cushion","avg_yac_above_expectation","catch_percentage",
               "avg_intended_air_yards","percent_share_of_intended_air_yards","targets","receptions","yards"]
rec = season_level("ngs_receiving.parquet", "targets", 40, rec_metrics)
transfer_table(rec, "RECEIVERS (>=40 targets both seasons)", rec_metrics, {
    "player-intrinsic (NGS)": ["avg_separation","avg_cushion","avg_yac_above_expectation","catch_percentage"],
    "context / volume":       ["avg_intended_air_yards","percent_share_of_intended_air_yards","targets","receptions","yards"],
})

rush_metrics = ["rush_yards_over_expected_per_att","efficiency","avg_time_to_los",
                "percent_attempts_gte_eight_defenders","avg_rush_yards","rush_attempts","rush_yards"]
rush = season_level("ngs_rushing.parquet", "rush_attempts", 80, rush_metrics)
transfer_table(rush, "RUSHERS (>=80 attempts both seasons)", rush_metrics, {
    "player-intrinsic (NGS)": ["rush_yards_over_expected_per_att","efficiency","avg_time_to_los"],
    "context / volume":       ["percent_attempts_gte_eight_defenders","avg_rush_yards","rush_attempts","rush_yards"],
})

pass_metrics = ["avg_time_to_throw","aggressiveness","completion_percentage_above_expectation",
                "avg_intended_air_yards","attempts","pass_yards"]
pas = season_level("ngs_passing.parquet", "attempts", 200, pass_metrics)
transfer_table(pas, "PASSERS (>=200 attempts both seasons)", pass_metrics, {
    "player-intrinsic (NGS)": ["avg_time_to_throw","aggressiveness","completion_percentage_above_expectation"],
    "context / volume":       ["avg_intended_air_yards","attempts","pass_yards"],
})
