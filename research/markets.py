"""How modelable is each PROP MARKET, apples to apples?

For every stat, the same test: predict this week's value from the player's own
strictly-prior expanding mean within the season (>=4 prior games), train on
2016-22, score out of sample on 2023-24. Ranks markets by how much of the
weekly variation is forecastable at all.
"""
import polars as pl, numpy as np

YEARS = list(range(2016, 2025))
pw = pl.concat([pl.read_parquet(f"pw_{y}.parquet") for y in YEARS], how="diagonal_relaxed") \
       .filter(pl.col("season_type") == "REG")

MARKETS = [
    ("receptions",       ["WR","TE"],  2.0,  "receptions (WR/TE)"),
    ("targets",          ["WR","TE"],  3.0,  "targets (WR/TE)"),
    ("receiving_yards",  ["WR","TE"], 25.0,  "receiving yards (WR/TE)"),
    ("carries",          ["RB"],       6.0,  "rush attempts (RB)"),
    ("rushing_yards",    ["RB"],      25.0,  "rushing yards (RB)"),
    ("receptions",       ["RB"],       1.5,  "receptions (RB)"),
    ("attempts",         ["QB"],      15.0,  "pass attempts (QB)"),
    ("completions",      ["QB"],      10.0,  "completions (QB)"),
    ("passing_yards",    ["QB"],     120.0,  "passing yards (QB)"),
    ("receiving_tds",    ["WR","TE"],  0.0,  "receiving TDs (WR/TE)"),
    ("rushing_tds",      ["RB"],       0.0,  "rushing TDs (RB)"),
]

rows = []
for stat, pos, min_prior, label in MARKETS:
    d = pw.filter(pl.col("position").is_in(pos)).sort(["player_id","season","week"])
    d = d.with_columns([
        pl.col(stat).cum_sum().over(["player_id","season"]).shift(1).over(["player_id","season"]).alias("cs"),
        pl.int_range(pl.len()).over(["player_id","season"]).alias("gp"),
    ]).with_columns((pl.col("cs")/pl.col("gp")).alias("prior_mean"))
    d = d.filter((pl.col("gp") >= 4) & pl.col("prior_mean").is_not_null()
                 & pl.col("prior_mean").is_finite() & (pl.col("prior_mean") >= min_prior)
                 & pl.col(stat).is_not_null())

    tr = d.filter(pl.col("season") <= 2022); te = d.filter(pl.col("season") >= 2023)
    if te.height < 300:
        continue
    x_tr, y_tr = tr["prior_mean"].to_numpy().astype(float), tr[stat].to_numpy().astype(float)
    x_te, y_te = te["prior_mean"].to_numpy().astype(float), te[stat].to_numpy().astype(float)
    X = np.column_stack([np.ones(len(x_tr)), x_tr])
    beta, *_ = np.linalg.lstsq(X, y_tr, rcond=None)
    pred = beta[0] + beta[1]*x_te
    r2 = 1 - ((y_te-pred)**2).sum() / ((y_te-y_te.mean())**2).sum()
    cv = y_te.std() / y_te.mean() if y_te.mean() > 0 else float("nan")
    rows.append((label, r2, cv, te.height))

rows.sort(key=lambda r: -r[1])
print("Out-of-sample R^2 predicting the week's value from the player's own prior mean")
print("train 2016-22 / test 2023-24\n")
print(f"{'market':<28}{'OOS R^2':>9}{'CV (sd/mean)':>14}{'test n':>9}")
print("-"*60)
for label, r2, cv, n in rows:
    print(f"{label:<28}{r2:>9.3f}{cv:>14.2f}{n:>9,}")

print("\nCV = coefficient of variation of the weekly outcome. High CV means the")
print("outcome is noisy relative to its own level, which is what makes a threshold")
print("bet on it hard even when the mean is well estimated.")
