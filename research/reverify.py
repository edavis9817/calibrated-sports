"""Re-verification of the three settled research findings, on the corrected mirror.

    python -m research.reverify --train 2016-2022 --test 2023-2024   # pass 1
    python -m research.reverify --train 2016-2024 --test 2025        # pass 2
    python -m research.reverify --train 2016-2022 --test 2023-2024 --source legacy
    python -m research.reverify --build-legacy 2016-2024

WHAT THIS CAN AND CANNOT TELL YOU
---------------------------------
The original study does not exist. There is no `research/` directory, no
notebook, no saved fit - only the resulting figures in CLAUDE.md and
DECISIONS.md. So the feature set, the sample filter, the estimator and the R^2
convention below are a RECONSTRUCTION, not the original.

That has a sharp consequence: a level difference between a number here and a
reference number confounds two causes - the data source changing, and this
methodology differing from the original. It cannot separate them, and a
"delta vs reference" should not be read as if it could.

What DOES separate them is running this identical pipeline over both data
sources. `--source legacy` rebuilds the inputs from the dead `player_stats`
release exactly as the old code addressed it, so legacy-vs-current is a clean
A/B in which methodology is held fixed. That is the comparison that answers
"did the dead release give us bad numbers".

The ORDERING of markets is the decision-relevant output and is far more robust
to methodology than the levels are, which is why it is reported first and
separately.

THE SPEC, stated so it can be disagreed with
--------------------------------------------
sample      REG season only; latest data_version; position-appropriate rows;
            player must have >= MIN_PRIOR_GAMES earlier games that season, so
            the trailing features exist and are not mostly padding.
features    usage only, per "usage carries the signal, efficiency is noise":
            trailing-4 mean of the target stat, season-to-date mean before this
            week, games played so far, prior-season per-game mean.
estimator   ridge (alpha=1.0) on standardized features, fit on the train
            seasons only. Standardization uses TRAIN moments applied to test.
metric      out-of-sample R^2 = 1 - SSE/SST, SST taken about the TEST mean.
            Nothing from the test seasons touches the fit.

This is deliberately a naive usage-only baseline, not the `edge/` feature set.
"""
import argparse
import io
import sqlite3
import sys

import numpy as np

MIN_PRIOR_GAMES = 3
RIDGE_ALPHA = 1.0
TRAIL = 4

# (label, column expression, position filter, reference OOS R^2 from CLAUDE.md)
MARKETS = [
    ("targets",         "targets",                    ("WR", "TE", "RB"), 0.277),
    ("rush attempts",   "carries",                    ("RB",),            0.254),
    ("receptions",      "receptions",                 ("WR", "TE", "RB"), 0.234),
    ("receiving yards", "receiving_yards",            ("WR", "TE", "RB"), 0.145),
    ("TDs",             "receiving_tds+rushing_tds",  ("WR", "TE", "RB"), 0.05),
    ("QB attempts",     "attempts",                   ("QB",),            0.039),
]

# Reference moments, from core/distributions.py: mean, var/mean, skew, P(0)
REF_MOMENTS = {
    "receptions (WR/TE)":     (3.75, 1.69, 0.83, 0.061),
    "rush attempts (RB)":     (12.10, 3.72, 0.45, 0.012),
    "receiving yards (WR/TE)": (47.70, 28.60, 1.15, 0.058),
}

REF_CORR = {"QB<->WR1": 0.42, "WR1<->WR2": 0.02}


# ---- data -------------------------------------------------------------------

def load(db_path, seasons):
    """Player-weeks at the newest data_version, REG season only."""
    import polars as pl
    lo, hi = min(seasons), max(seasons)
    sql = """
        WITH latest AS (
            SELECT gsis_id, season, week, season_type, MAX(data_version) AS dv
              FROM nfl_player_week
             WHERE season BETWEEN ? AND ? AND season_type = 'REG'
             GROUP BY gsis_id, season, week, season_type
        )
        SELECT p.gsis_id, p.season, p.week, p.player_name, p.position, p.team,
               p.receptions, p.targets, p.receiving_yards, p.receiving_tds,
               p.carries, p.rushing_yards, p.rushing_tds,
               p.attempts, p.completions, p.passing_yards
          FROM nfl_player_week p
          JOIN latest l ON p.gsis_id=l.gsis_id AND p.season=l.season
           AND p.week=l.week AND p.season_type=l.season_type
           AND p.data_version=l.dv
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(sql, (lo, hi)).fetchall()
        cols = [d[0] for d in con.execute(sql, (lo, hi)).description]
    finally:
        con.close()
    df = pl.DataFrame(rows, schema=cols, orient="row")
    return df.with_columns([pl.col(c).cast(pl.Float64) for c in cols
                            if c not in ("gsis_id", "player_name", "position", "team")])


def with_target(df, expr):
    import polars as pl
    if "+" in expr:
        a, b = expr.split("+")
        return df.with_columns((pl.col(a.strip()).fill_null(0)
                                + pl.col(b.strip()).fill_null(0)).alias("y"))
    return df.with_columns(pl.col(expr).alias("y"))


def build_features(df):
    """Trailing usage, computed strictly from earlier weeks of the same season.

    Every feature here is shifted by one game before any window is taken, so a
    row can never see its own outcome. That is invariant #5 in miniature: get it
    wrong and the R^2 is spectacular and meaningless.
    """
    import polars as pl
    df = df.sort(["gsis_id", "season", "week"])
    prior = pl.col("y").shift(1).over(["gsis_id", "season"])
    prev_season = (df.group_by(["gsis_id", "season"])
                     .agg(pl.col("y").mean().alias("prev_mean"))
                     .with_columns((pl.col("season") + 1).alias("season")))
    out = df.with_columns([
        prior.rolling_mean(window_size=TRAIL, min_samples=1).alias("trail4"),
        prior.cum_sum().alias("_cs"),
        pl.col("y").cum_count().over(["gsis_id", "season"]).alias("_n"),
    ])
    out = out.with_columns([
        (pl.col("_n") - 1).alias("games_prior"),
        (pl.col("_cs") / (pl.col("_n") - 1)).alias("std_mean"),
    ])
    out = out.join(prev_season, on=["gsis_id", "season"], how="left")
    return out.with_columns(pl.col("prev_mean").fill_null(
        pl.col("std_mean")).fill_null(0.0))


FEATURES = ["trail4", "std_mean", "games_prior", "prev_mean"]


def matrix(df):
    import polars as pl
    d = df.filter(pl.col("games_prior") >= MIN_PRIOR_GAMES).drop_nulls(
        FEATURES + ["y"])
    X = d.select(FEATURES).to_numpy().astype(float)
    y = d["y"].to_numpy().astype(float)
    return X, y, d


def ridge_fit(X, y, alpha=RIDGE_ALPHA):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    Z1 = np.hstack([np.ones((len(Z), 1)), Z])
    A = np.eye(Z1.shape[1]) * alpha
    A[0, 0] = 0.0                      # never penalise the intercept
    beta = np.linalg.solve(Z1.T @ Z1 + A, Z1.T @ y)
    return beta, mu, sd


def ridge_predict(model, X):
    beta, mu, sd = model
    Z = (X - mu) / sd
    return np.hstack([np.ones((len(Z), 1)), Z]) @ beta


def oos_r2(y, pred):
    sse = float(((y - pred) ** 2).sum())
    sst = float(((y - y.mean()) ** 2).sum())
    return 1.0 - sse / sst if sst > 0 else float("nan")


# ---- study 1: forecastability ----------------------------------------------

def study_forecastability(df, train_seasons, test_seasons):
    import polars as pl
    results = []
    for label, expr, positions, ref in MARKETS:
        sub = df.filter(pl.col("position").is_in(list(positions)))
        sub = build_features(with_target(sub, expr))
        tr = sub.filter(pl.col("season").is_in(list(train_seasons)))
        te = sub.filter(pl.col("season").is_in(list(test_seasons)))
        Xtr, ytr, _ = matrix(tr)
        Xte, yte, _ = matrix(te)
        if len(ytr) < 100 or len(yte) < 100:
            results.append((label, float("nan"), ref, 0, 0))
            continue
        model = ridge_fit(Xtr, ytr)
        r2 = oos_r2(yte, ridge_predict(model, Xte))
        results.append((label, r2, ref, len(ytr), len(yte)))
    return results


# ---- study 2: distribution moments -----------------------------------------

def moments(v):
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    if len(v) < 50:
        return (float("nan"),) * 4
    m, var = v.mean(), v.var()
    skew = float(((v - m) ** 3).mean() / v.std() ** 3) if v.std() > 0 else float("nan")
    return float(m), float(var / m) if m else float("nan"), skew, float((v == 0).mean())


def study_moments(df, seasons):
    import polars as pl
    d = df.filter(pl.col("season").is_in(list(seasons)))
    rec = d.filter(pl.col("position").is_in(["WR", "TE"]))
    rb = d.filter(pl.col("position") == "RB")
    out = {}
    # Three filters reported side by side rather than one chosen after the
    # fact: the reference figures imply a usage screen that was never written
    # down, and picking whichever matches would be fitting the answer.
    for name, frame, col in (("receptions (WR/TE)", rec, "receptions"),
                             ("rush attempts (RB)", rb, "carries"),
                             ("receiving yards (WR/TE)", rec, "receiving_yards")):
        gate = "targets" if "receptions" in name or "receiving" in name else "carries"
        variants = {
            "all rows": frame,
            ">=1 touch": frame.filter(pl.col(gate).fill_null(0) >= 1),
            "trail4>=2": build_features(with_target(frame, gate)).filter(
                pl.col("trail4") >= 2),
        }
        out[name] = {k: moments(v[col].to_numpy()) for k, v in variants.items()}
    return out


# ---- study 3: same-game correlations ---------------------------------------

def study_correlations(df, seasons):
    """QB<->WR1 and WR1<->WR2 receiving/passing yards within a team-game.

    WR1 and WR2 are ranked by SEASON target totals, not by the game in hand.
    Ranking within the game would pick the receiver who happened to produce,
    which manufactures exactly the correlation the study is trying to measure.
    """
    import polars as pl
    d = df.filter(pl.col("season").is_in(list(seasons)))

    wr = d.filter(pl.col("position") == "WR")
    rank = (wr.group_by(["gsis_id", "season", "team"])
              .agg(pl.col("targets").fill_null(0).sum().alias("szn_targets"))
              .sort(["season", "team", "szn_targets"], descending=[False, False, True])
              .with_columns(pl.int_range(pl.len()).over(["season", "team"]).alias("wr_rank")))
    wr = wr.join(rank.select(["gsis_id", "season", "team", "wr_rank"]),
                 on=["gsis_id", "season", "team"], how="left")

    w1 = wr.filter(pl.col("wr_rank") == 0).select(
        ["season", "week", "team", pl.col("receiving_yards").alias("wr1_yds"),
         pl.col("receptions").alias("wr1_rec")])
    w2 = wr.filter(pl.col("wr_rank") == 1).select(
        ["season", "week", "team", pl.col("receiving_yards").alias("wr2_yds"),
         pl.col("receptions").alias("wr2_rec")])

    qb = (d.filter(pl.col("position") == "QB")
            .sort(["season", "week", "team", "attempts"], descending=[False]*3+[True])
            .group_by(["season", "week", "team"]).first()
            .select(["season", "week", "team",
                     pl.col("passing_yards").alias("qb_yds")]))

    j = (w1.join(qb, on=["season", "week", "team"], how="inner")
           .join(w2, on=["season", "week", "team"], how="inner")).drop_nulls()

    def corr(a, b):
        a, b = j[a].to_numpy().astype(float), j[b].to_numpy().astype(float)
        return float(np.corrcoef(a, b)[0, 1]) if len(a) > 30 else float("nan")

    return {"QB<->WR1": corr("qb_yds", "wr1_yds"),
            "WR1<->WR2": corr("wr1_yds", "wr2_yds"),
            "QB<->WR1 (rec)": corr("qb_yds", "wr1_rec"),
            "n team-games": j.height}


# ---- legacy A/B -------------------------------------------------------------

def build_legacy(db_path, seasons):
    """Rebuild nfl_player_week from the DEAD `player_stats` release, addressed
    exactly as the old code did - including the 2019 fallback that turned out to
    be the offense-only product rather than the full weekly file."""
    import httpx
    import config
    import store
    from jobs import ingest_nflverse

    base = config.NFLVERSE_BASE
    old = config.DB_PATH
    config.DB_PATH = db_path
    try:
        store.init_db()
        with httpx.Client(timeout=180, follow_redirects=True) as c:
            for s in seasons:
                name = (f"stats_player_week_{s}.parquet" if s != 2019
                        else "player_stats_2019.parquet")
                url = f"{base}/player_stats/{name}"
                r = c.get(url)
                if r.status_code != 200:
                    print(f"  {r.status_code} {name}")
                    continue
                table, cols, rows = ingest_nflverse.normalize_weekly_stats(
                    r.content, "legacy")
                store.replace_rows(table, cols, rows, None)
                print(f"  legacy {s}: {len(rows):,} rows from {name}")
    finally:
        config.DB_PATH = old


# ---- reporting --------------------------------------------------------------

def _rng(spec):
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def report(df, train, test, label, source):
    print("=" * 74)
    print(f"{label}   source={source}   train {train[0]}-{train[-1]}   "
          f"test {test[0]}" + (f"-{test[-1]}" if len(test) > 1 else ""))
    print("=" * 74)

    print("\n1. FORECASTABILITY  (out-of-sample R^2, ranked)")
    res = study_forecastability(df, train, test)
    ranked = sorted([r for r in res if not np.isnan(r[1])],
                    key=lambda r: -r[1])
    print(f"   {'rank':>4}  {'market':<17} {'OOS R2':>8} {'ref':>7} {'delta':>8}"
          f" {'n_train':>9} {'n_test':>8}")
    for i, (m, r2, ref, ntr, nte) in enumerate(ranked, 1):
        print(f"   {i:>4}  {m:<17} {r2:>8.3f} {ref:>7.3f} {r2-ref:>+8.3f}"
              f" {ntr:>9,} {nte:>8,}")
    ref_order = [m for m, _, _, _ in sorted(
        [(m, r2, ref, 0) for m, r2, ref, _, _ in res], key=lambda r: -r[2])]
    got_order = [m for m, _, _, _, _ in ranked]
    print(f"\n   reference order: {' > '.join(ref_order)}")
    print(f"   measured order : {' > '.join(got_order)}")
    print(f"   ORDERING {'HOLDS' if got_order == ref_order else 'REORDERS'}")

    print("\n2. DISTRIBUTION MOMENTS  (test seasons only)")
    mom = study_moments(df, test)
    print(f"   {'series':<24} {'filter':<11} {'mean':>7} {'var/mean':>9} "
          f"{'skew':>6} {'P(0)':>6}   reference")
    for name, variants in mom.items():
        ref = REF_MOMENTS[name]
        for k, (m, vmr, sk, p0) in variants.items():
            tag = (f"   ref {ref[0]:.2f} / {ref[1]:.2f} / {ref[2]:.2f} / "
                   f"{ref[3]:.3f}") if k == "all rows" else ""
            print(f"   {name if k == 'all rows' else '':<24} {k:<11} "
                  f"{m:>7.2f} {vmr:>9.2f} {sk:>6.2f} {p0:>6.3f}{tag}")

    print("\n3. SAME-GAME CORRELATIONS  (test seasons only)")
    cor = study_correlations(df, test)
    for k in ("QB<->WR1", "WR1<->WR2"):
        ref = REF_CORR[k]
        print(f"   {k:<16} {cor[k]:>7.3f}   ref {ref:>6.2f}   "
              f"delta {cor[k]-ref:>+6.3f}")
    print(f"   {'QB<->WR1 (rec)':<16} {cor['QB<->WR1 (rec)']:>7.3f}   "
          f"(secondary, no reference)")
    print(f"   n team-games     {cor['n team-games']:>7,}")
    return {"forecast": res, "order_holds": got_order == ref_order,
            "moments": mom, "corr": cor}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="2016-2022")
    ap.add_argument("--test", default="2023-2024")
    ap.add_argument("--label", default="")
    ap.add_argument("--source", default="current", choices=("current", "legacy"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--build-legacy", dest="build_legacy")
    args = ap.parse_args()

    import config
    if args.build_legacy:
        build_legacy(args.db or "data/legacy_player_stats.db",
                     _rng(args.build_legacy))
        return

    db = args.db or ("data/legacy_player_stats.db" if args.source == "legacy"
                     else config.DB_PATH)
    train, test = _rng(args.train), _rng(args.test)
    df = load(db, train + test)
    report(df, train, test, args.label or f"{args.train} -> {args.test}",
           args.source)


if __name__ == "__main__":
    main()
