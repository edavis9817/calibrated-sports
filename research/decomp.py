import polars as pl, numpy as np

YEARS = list(range(2016, 2025))
pw = pl.concat([pl.read_parquet(f"pw_{y}.parquet") for y in YEARS], how="diagonal_relaxed") \
       .filter(pl.col("season_type") == "REG")

g = pl.read_csv("games.csv", infer_schema_length=8000).filter(
    (pl.col("game_type") == "REG") & pl.col("season").is_in(YEARS))
# nfldata: spread_line is from the HOME team's perspective, positive = home favored
lines = pl.concat([
    g.select(season="season", week="week", team="home_team",
             implied=(pl.col("total_line")/2 + pl.col("spread_line")/2),
             spread=pl.col("spread_line"), total="total_line"),
    g.select(season="season", week="week", team="away_team",
             implied=(pl.col("total_line")/2 - pl.col("spread_line")/2),
             spread=-pl.col("spread_line"), total="total_line"),
]).drop_nulls()

# ---------- team-week defensive strength allowed (pass), trailing only ----------
teamwk = pw.group_by(["season","week","opponent_team"]).agg([
    pl.col("receiving_yards").sum().alias("pass_yds_allowed"),
    pl.col("targets").sum().alias("targets_allowed"),
]).rename({"opponent_team":"team"}).sort(["team","season","week"])
teamwk = teamwk.with_columns([
    pl.col("pass_yds_allowed").cum_sum().over(["team","season"]).shift(1).over(["team","season"]).alias("cum_pya"),
    pl.int_range(pl.len()).over(["team","season"]).alias("gp_prior"),
]).with_columns(
    (pl.col("cum_pya")/pl.col("gp_prior")).alias("opp_pass_yds_allowed_pg")
).select(["season","week","team","opp_pass_yds_allowed_pg","gp_prior"])

# ---------- receiver rows with strictly-prior usage ----------
rec = pw.filter(pl.col("position").is_in(["WR","TE"])).sort(["player_id","season","week"])
rec = rec.with_columns([
    pl.col("target_share").cum_sum().over(["player_id","season"]).shift(1).over(["player_id","season"]).alias("cs_ts"),
    pl.col("air_yards_share").cum_sum().over(["player_id","season"]).shift(1).over(["player_id","season"]).alias("cs_ays"),
    pl.col("receiving_yards").cum_sum().over(["player_id","season"]).shift(1).over(["player_id","season"]).alias("cs_ry"),
    pl.col("targets").cum_sum().over(["player_id","season"]).shift(1).over(["player_id","season"]).alias("cs_tg"),
    pl.int_range(pl.len()).over(["player_id","season"]).alias("g_prior"),
])
rec = rec.with_columns([
    (pl.col("cs_ts")/pl.col("g_prior")).alias("prior_ts"),
    (pl.col("cs_ays")/pl.col("g_prior")).alias("prior_ays"),
    (pl.col("cs_ry")/pl.col("cs_tg")).alias("prior_ypt"),
]).filter((pl.col("g_prior") >= 4) & pl.col("prior_ts").is_not_null())

d = (rec.join(lines, on=["season","week","team"], how="inner")
        .join(teamwk.rename({"team":"opponent_team"}),
              left_on=["season","week","opponent_team"], right_on=["season","week","opponent_team"],
              how="inner")
        .filter(pl.col("gp_prior") >= 4)
        .drop_nulls(["prior_ts","prior_ays","prior_ypt","implied","spread","opp_pass_yds_allowed_pg"]))

BLOCKS = {
    "usage (prior target/AY share, prior yds/target)": ["prior_ts","prior_ays","prior_ypt"],
    "+ game script (implied team total, spread)":      ["implied","spread"],
    "+ opponent pass defense (yds allowed/gm)":        ["opp_pass_yds_allowed_pg"],
}
FEATS = [c for blk in BLOCKS.values() for c in blk]
d = d.with_columns([pl.when(pl.col(c).is_finite()).then(pl.col(c)).otherwise(None) for c in FEATS]) \
     .drop_nulls(FEATS + ["receiving_yards"])

train = d.filter(pl.col("season") <= 2022); test = d.filter(pl.col("season") >= 2023)
y_tr = train["receiving_yards"].to_numpy().astype(float)
y_te = test["receiving_yards"].to_numpy().astype(float)

def r2(cols):
    if not cols:
        return 1 - ((y_te - y_tr.mean())**2).sum() / ((y_te - y_te.mean())**2).sum()
    X = np.column_stack([np.ones(len(y_tr))] + [train[c].to_numpy().astype(float) for c in cols])
    Xe = np.column_stack([np.ones(len(y_te))] + [test[c].to_numpy().astype(float) for c in cols])
    beta, *_ = np.linalg.lstsq(X, y_tr, rcond=None)
    p = Xe @ beta
    return 1 - ((y_te - p)**2).sum() / ((y_te - y_te.mean())**2).sum()

print(f"WR/TE weekly receiving yards — out-of-sample R^2 (train 2016-22, test 2023-24)")
print(f"train n={train.height:,}  test n={test.height:,}\n")
print(f"{'model':<52}{'OOS R^2':>10}{'delta':>9}")
print("-"*71)
cols, prev = [], r2([])
print(f"{'baseline (league mean)':<52}{prev:>10.4f}{'':>9}")
for name, blk in BLOCKS.items():
    cols += blk
    cur = r2(cols)
    print(f"{name:<52}{cur:>10.4f}{cur-prev:>9.4f}")
    prev = cur

# ---------- same-game correlation structure ----------
print("\n\nSame-game correlation of weekly production (z-scored within player-season)")
print("-"*71)
z = pw.filter(pl.col("season_type")=="REG").with_columns([
    ((pl.col("receiving_yards") - pl.col("receiving_yards").mean().over(["player_id","season"]))
     / pl.col("receiving_yards").std().over(["player_id","season"])).alias("z_rec"),
    ((pl.col("passing_yards") - pl.col("passing_yards").mean().over(["player_id","season"]))
     / pl.col("passing_yards").std().over(["player_id","season"])).alias("z_pass"),
    ((pl.col("rushing_yards") - pl.col("rushing_yards").mean().over(["player_id","season"]))
     / pl.col("rushing_yards").std().over(["player_id","season"])).alias("z_rush"),
])
# season target-share ranking to identify WR1 / WR2 / lead RB / starting QB
seas = pw.group_by(["player_id","season","team"]).agg([
    pl.col("targets").sum().alias("tg"), pl.col("attempts").sum().alias("att"),
    pl.col("carries").sum().alias("car"), pl.col("position").first()])
wr = seas.filter(pl.col("position").is_in(["WR","TE"])).sort(["season","team","tg"], descending=[False,False,True]) \
         .with_columns(pl.int_range(pl.len()).over(["season","team"]).alias("rk"))
qb = seas.filter(pl.col("position")=="QB").sort(["season","team","att"], descending=[False,False,True]) \
         .with_columns(pl.int_range(pl.len()).over(["season","team"]).alias("rk"))
rb = seas.filter(pl.col("position")=="RB").sort(["season","team","car"], descending=[False,False,True]) \
         .with_columns(pl.int_range(pl.len()).over(["season","team"]).alias("rk"))

def slot(tbl, rank, zcol, name):
    ids = tbl.filter(pl.col("rk")==rank).select(["player_id","season","team"])
    return (z.join(ids, on=["player_id","season","team"])
             .select(["season","week","team","opponent_team", pl.col(zcol).alias(name)])
             .group_by(["season","week","team","opponent_team"]).agg(pl.col(name).first()))

wr1, wr2 = slot(wr,0,"z_rec","wr1"), slot(wr,1,"z_rec","wr2")
qb1, rb1 = slot(qb,0,"z_pass","qb"), slot(rb,0,"z_rush","rb1")
tg = wr1.join(wr2, on=["season","week","team","opponent_team"], how="inner") \
        .join(qb1, on=["season","week","team","opponent_team"], how="inner") \
        .join(rb1, on=["season","week","team","opponent_team"], how="inner")
opp = tg.select(["season","week", pl.col("opponent_team").alias("team"),
                 pl.col("wr1").alias("opp_wr1"), pl.col("qb").alias("opp_qb")])
tg = tg.join(opp, on=["season","week","team"], how="inner")

def show(a, b, label):
    x, y_ = tg[a].to_numpy(), tg[b].to_numpy()
    ok = ~(np.isnan(x)|np.isnan(y_))
    print(f"  {label:<48}{np.corrcoef(x[ok],y_[ok])[0,1]:>8.3f}   (n={ok.sum():,})")

show("qb","wr1",  "QB pass yds  <->  WR1 rec yds   (same team)")
show("qb","wr2",  "QB pass yds  <->  WR2 rec yds   (same team)")
show("wr1","wr2", "WR1          <->  WR2           (same team)")
show("qb","rb1",  "QB pass yds  <->  RB1 rush yds  (same team)")
show("wr1","rb1", "WR1 rec yds  <->  RB1 rush yds  (same team)")
show("qb","opp_qb",  "QB           <->  opposing QB")
show("wr1","opp_wr1","WR1          <->  opposing WR1")
