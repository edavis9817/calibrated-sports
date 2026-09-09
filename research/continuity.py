import polars as pl, numpy as np

YEARS = list(range(2016, 2026))
frames = []
for y in YEARS:
    d = pl.read_parquet(f"pbp_{y}.parquet")
    keep = ["season","season_type","posteam","defteam","play_type","pass_oe","shotgun",
            "no_huddle","epa","success","wp","half_seconds_remaining","qtr"]
    frames.append(d.select([c for c in keep if c in d.columns]))
pbp = pl.concat(frames, how="diagonal_relaxed")

p = pbp.filter((pl.col("season_type")=="REG") & pl.col("posteam").is_not_null()
               & pl.col("play_type").is_in(["pass","run"]))
neutral = p.filter((pl.col("wp")>0.20) & (pl.col("wp")<0.80)
                   & (pl.col("half_seconds_remaining")>120) & (pl.col("qtr")<=4))

tend = neutral.group_by(["season","posteam"]).agg([
    pl.col("pass_oe").mean().alias("PROE"),
    pl.col("shotgun").mean().alias("shotgun_rate"),
    pl.col("no_huddle").mean().alias("nohuddle_rate"),
]).rename({"posteam":"team"})
off = p.group_by(["season","posteam"]).agg([
    pl.col("epa").mean().alias("off_epa_play"),
    pl.col("success").mean().alias("off_success"),
]).rename({"posteam":"team"})
dfn = p.group_by(["season","defteam"]).agg([
    pl.col("epa").mean().alias("def_epa_allowed")]).rename({"defteam":"team"})
ts = tend.join(off, on=["season","team"]).join(dfn, on=["season","team"])

# --- head coach + primary QB per team-season, from the games file ---
g = pl.read_csv("games.csv", infer_schema_length=8000).filter(
    (pl.col("game_type")=="REG") & pl.col("season").is_in(YEARS))
long = pl.concat([
    g.select(season="season", team="home_team", coach="home_coach", qb="home_qb_name"),
    g.select(season="season", team="away_team", coach="away_coach", qb="away_qb_name"),
])
staff = (long.group_by(["season","team"])
           .agg([pl.col("coach").mode().first().alias("coach"),
                 pl.col("qb").mode().first().alias("qb")]))
ts = ts.join(staff, on=["season","team"], how="left")

METRICS = ["PROE","shotgun_rate","nohuddle_rate","off_epa_play","off_success","def_epa_allowed"]
nxt = ts.with_columns((pl.col("season")-1).alias("prev_season"))
pairs = ts.join(nxt.select(["prev_season","team","coach","qb"]+METRICS),
                left_on=["season","team"], right_on=["prev_season","team"], suffix="_next")
pairs = pairs.with_columns([
    (pl.col("coach")==pl.col("coach_next")).alias("same_coach"),
    (pl.col("qb")==pl.col("qb_next")).alias("same_qb"),
])

def corr(sub, m):
    a, b = sub[m].to_numpy(), sub[m+"_next"].to_numpy()
    ok = ~(np.isnan(a)|np.isnan(b))
    return (float(np.corrcoef(a[ok],b[ok])[0,1]), int(ok.sum())) if ok.sum()>8 else (float("nan"),int(ok.sum()))

for label, col in [("HEAD COACH","same_coach"), ("PRIMARY QB","same_qb")]:
    keep_ = pairs.filter(pl.col(col)); chg = pairs.filter(~pl.col(col))
    print(f"\n=== year-over-year r, split by {label} continuity  "
          f"(same n={keep_.height}, changed n={chg.height}) ===")
    print(f"{'metric':<20}{'SAME':>10}{'CHANGED':>10}{'drop':>10}")
    print("-"*50)
    for m in METRICS:
        rs,_ = corr(keep_, m); rc,_ = corr(chg, m)
        print(f"{m:<20}{rs:>10.3f}{rc:>10.3f}{rs-rc:>10.3f}")
