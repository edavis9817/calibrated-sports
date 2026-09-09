import polars as pl, numpy as np

YEARS = list(range(2016, 2026))
frames = []
for y in YEARS:
    d = pl.read_parquet(f"pbp_{y}.parquet")
    keep = ["season","week","season_type","game_id","posteam","defteam","play_type","qb_dropback",
            "pass_oe","shotgun","no_huddle","epa","success","wp","down","ydstogo",
            "half_seconds_remaining","qtr","cpoe","air_yards","fixed_drive","game_seconds_remaining"]
    frames.append(d.select([c for c in keep if c in d.columns]))
pbp = pl.concat(frames, how="diagonal_relaxed")

# regular season, real scrimmage plays
p = pbp.filter(
    (pl.col("season_type") == "REG")
    & pl.col("posteam").is_not_null()
    & pl.col("play_type").is_in(["pass", "run"])
)

# "neutral" script: competitive win prob, not end-of-half clock situations
neutral = p.filter(
    (pl.col("wp") > 0.20) & (pl.col("wp") < 0.80)
    & (pl.col("half_seconds_remaining") > 120)
    & (pl.col("qtr") <= 4)
)

# ---- OFFENSIVE TENDENCY metrics (team-season) ----
tend = neutral.group_by(["season", "posteam"]).agg([
    pl.col("pass_oe").mean().alias("PROE"),
    pl.col("shotgun").mean().alias("shotgun_rate"),
    pl.col("no_huddle").mean().alias("nohuddle_rate"),
    (pl.col("play_type") == "pass").mean().alias("pass_rate"),
    pl.len().alias("n_neutral"),
]).rename({"posteam": "team"})

# ---- OFFENSIVE RESULT metrics (all plays, team-season) ----
off_res = p.group_by(["season", "posteam"]).agg([
    pl.col("epa").mean().alias("off_epa_play"),
    pl.col("success").mean().alias("off_success"),
    pl.len().alias("n_off"),
]).rename({"posteam": "team"})

# ---- DEFENSIVE result metric ----
def_res = p.group_by(["season", "defteam"]).agg([
    pl.col("epa").mean().alias("def_epa_play_allowed"),
]).rename({"defteam": "team"})

ts = tend.join(off_res, on=["season", "team"]).join(def_res, on=["season", "team"])

METRICS = ["PROE","shotgun_rate","nohuddle_rate","pass_rate",
           "off_epa_play","off_success","def_epa_play_allowed"]

# lag-1 join: same team, season -> season+1
nxt = ts.with_columns((pl.col("season") - 1).alias("prev_season"))
pairs = ts.join(
    nxt.select(["prev_season","team"] + METRICS),
    left_on=["season","team"], right_on=["prev_season","team"], suffix="_next"
)

print(f"team-season pairs: {pairs.height}  (seasons {min(YEARS)}-{max(YEARS)})\n")
print(f"{'metric':<24}{'r (yr t -> t+1)':>18}{'r^2':>10}")
print("-" * 52)
rows = []
for m in METRICS:
    a = pairs[m].to_numpy(); b = pairs[m + "_next"].to_numpy()
    ok = ~(np.isnan(a) | np.isnan(b))
    r = float(np.corrcoef(a[ok], b[ok])[0, 1])
    rows.append((m, r))
    print(f"{m:<24}{r:>18.3f}{r*r:>10.3f}")

# also: does last year's PROE predict next year's EPA? (tendency -> result leakage check)
a = pairs["PROE"].to_numpy(); b = pairs["off_epa_play_next"].to_numpy()
ok = ~(np.isnan(a) | np.isnan(b))
print(f"\ncross-check  PROE(t) -> off_epa_play(t+1):  r = {np.corrcoef(a[ok],b[ok])[0,1]:.3f}")
