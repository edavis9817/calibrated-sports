"""Do external/situational factors and player-specific effects survive?

Baseline = player's own strictly-prior expanding mean within season (>=4 games).
Residual = actual - baseline. Then:
  (A) effect size of each situational factor on the residual, with t-stats
  (B) does a player's residual PERSIST year to year? (the "outliers are real" test)
  (C) shape of the outcome distribution, which is what a prop actually prices
"""
import polars as pl, numpy as np

YEARS = list(range(2016, 2025))
pw = pl.concat([pl.read_parquet(f"pw_{y}.parquet") for y in YEARS], how="diagonal_relaxed") \
       .filter(pl.col("season_type") == "REG")

g = pl.read_csv("games.csv", infer_schema_length=8000).filter(
    (pl.col("game_type") == "REG") & pl.col("season").is_in(YEARS))
ctx = pl.concat([
    g.select(season="season", week="week", team="home_team", is_home=pl.lit(1),
             div_game="div_game", rest="home_rest", roof="roof", temp="temp", wind="wind",
             weekday="weekday", gametime="gametime",
             spread=pl.col("spread_line"), total="total_line"),
    g.select(season="season", week="week", team="away_team", is_home=pl.lit(0),
             div_game="div_game", rest="away_rest", roof="roof", temp="temp", wind="wind",
             weekday="weekday", gametime="gametime",
             spread=-pl.col("spread_line"), total="total_line"),
])
ctx = ctx.with_columns([
    pl.col("roof").is_in(["dome", "closed"]).cast(pl.Int8).alias("indoors"),
    (~pl.col("weekday").is_in(["Sunday"])).cast(pl.Int8).alias("not_sunday"),
    (pl.col("gametime").str.slice(0, 2).cast(pl.Int32, strict=False) >= 20).cast(pl.Int8).alias("primetime"),
    (pl.col("rest") <= 4).cast(pl.Int8).alias("short_week"),
    (pl.col("rest") >= 10).cast(pl.Int8).alias("long_rest"),
    # team spread is +ve when this team is favored (home rows keep spread_line, away rows flip it)
    (pl.col("spread") > 0).cast(pl.Int8).alias("favorite"),
    (pl.col("spread").abs() >= 7).cast(pl.Int8).alias("big_spread"),
    pl.col("div_game").cast(pl.Int8).alias("divisional"),
])

def build(stat, pos, min_prior):
    d = pw.filter(pl.col("position").is_in(pos)).sort(["player_id", "season", "week"])
    d = d.with_columns([
        pl.col(stat).cum_sum().over(["player_id","season"]).shift(1).over(["player_id","season"]).alias("cs"),
        pl.int_range(pl.len()).over(["player_id","season"]).alias("gp"),
    ]).with_columns((pl.col("cs")/pl.col("gp")).alias("base"))
    d = d.filter((pl.col("gp") >= 4) & pl.col("base").is_finite() & (pl.col("base") >= min_prior)
                 & pl.col(stat).is_not_null())
    d = d.join(ctx, on=["season","week","team"], how="inner")
    return d.with_columns((pl.col(stat) - pl.col("base")).alias("resid"))

FACTORS = ["is_home","divisional","primetime","not_sunday","short_week","long_rest","favorite","big_spread","indoors"]

for stat, pos, minp, label in [("receptions", ["WR","TE"], 2.0, "receptions (WR/TE)"),
                               ("receiving_yards", ["WR","TE"], 25.0, "receiving yards (WR/TE)"),
                               ("carries", ["RB"], 6.0, "rush attempts (RB)")]:
    d = build(stat, pos, minp)
    r = d["resid"].to_numpy().astype(float)
    print(f"\n=== (A) {label}  —  n={len(r):,}, residual sd = {r.std():.2f} ===")
    print(f"{'factor':<14}{'n(=1)':>8}{'effect':>10}{'t':>8}{'as % of sd':>13}")
    print("-"*55)
    for f in FACTORS:
        x = d[f].to_numpy().astype(float)
        ok = ~np.isnan(x) & ~np.isnan(r)
        x1, r1 = x[ok], r[ok]
        if x1.sum() < 200 or (1-x1).sum() < 200:
            continue
        a, b = r1[x1 == 1], r1[x1 == 0]
        diff = a.mean() - b.mean()
        se = np.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
        print(f"{f:<14}{len(a):>8,}{diff:>10.3f}{diff/se:>8.2f}{abs(diff)/r1.std():>12.1%}")

    # wind, outdoor games only
    od = d.filter((pl.col("indoors") == 0) & pl.col("wind").is_not_null())
    if od.height > 500:
        w = od["wind"].to_numpy().astype(float); rw = od["resid"].to_numpy().astype(float)
        ok = ~np.isnan(w) & ~np.isnan(rw)
        hi, lo = rw[ok][w[ok] >= 15], rw[ok][w[ok] < 15]
        if len(hi) > 100:
            se = np.sqrt(hi.var(ddof=1)/len(hi) + lo.var(ddof=1)/len(lo))
            print(f"{'wind >=15mph':<14}{len(hi):>8,}{hi.mean()-lo.mean():>10.3f}"
                  f"{(hi.mean()-lo.mean())/se:>8.2f}{abs(hi.mean()-lo.mean())/rw[ok].std():>12.1%}")

    # ---- (B) does a player's residual persist year to year? ----
    ps = d.group_by(["player_id","season"]).agg([
        pl.col("resid").mean().alias("mr"), pl.len().alias("n")]).filter(pl.col("n") >= 8)
    nx = ps.with_columns((pl.col("season")-1).alias("ps_"))
    pr = ps.join(nx.select(["ps_","player_id", pl.col("mr").alias("mr_next")]),
                 left_on=["season","player_id"], right_on=["ps_","player_id"])
    a, b = pr["mr"].to_numpy(), pr["mr_next"].to_numpy()
    ok = ~(np.isnan(a)|np.isnan(b))
    rr = np.corrcoef(a[ok], b[ok])[0,1]
    print(f"\n  (B) player mean-residual, season t -> t+1:  r = {rr:.3f}  (n={ok.sum()} player-season pairs)")
    print(f"      => {rr**2:.1%} of a player's 'beats his own baseline' effect repeats")

    # ---- (C) distribution shape ----
    v = d[stat].to_numpy().astype(float)
    m, sd = v.mean(), v.std()
    print(f"  (C) outcome: mean={m:.2f} sd={sd:.2f} var/mean={sd**2/m:.2f} "
          f"skew={((v-m)**3).mean()/sd**3:.2f}  P(0)={np.mean(v==0):.1%}")
