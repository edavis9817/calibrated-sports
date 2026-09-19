"""Next Gen Stats: what it adds over the play-by-play, and what it repeats.

    python -m analytics.ngs --build
    python -m analytics.ngs --redundancy
    python -m analytics.ngs --stability
    python -m analytics.ngs --publish

THE QUESTION IS VALUE, NOT AVAILABILITY. Availability was settled in
`docs/F01-pbp-survey.md` section 3b: NGS is LIVE tier and carries the current
season, unlike `pbp_participation`. What is open is whether its columns say
anything the play-by-play does not.

A column earns its place on two counts, and it needs BOTH:

    1. the play-by-play cannot reproduce it.  A column that correlates 0.99
       with something already derivable is a second name for a number we have.
    2. it carries WITHIN-PLAYER signal.  A tracking column that is pure weekly
       noise around a player's own mean tells you nothing next week, however
       exotic it sounds - which is exactly what `docs/F02-usage-stability.md`
       found for receiving usage, using this same decomposition.

TWO TRAPS, BOTH WRITTEN DOWN BEFORE THIS MODULE WAS WRITTEN.

WEEK 0 IS THE SEASON TOTAL, so aggregating over all weeks doubles every figure
- and a silent double survives every sanity check a reader would apply, because
ratios, rankings and correlations are all unchanged and only magnitudes move.
`build` excludes it AND reconciles it rather than assuming.

THE RECONCILIATION EARNED ITSELF ON THE FIRST RUN. Written as an equality it
refused the build at 1,166 of 1,325 receiving rows, and the refusal was correct:
NGS FILTERS WEEKLY ROWS TOO, so a player below the weekly qualifying threshold
simply has no row for that week while his targets still count in the season
total. The invariant is `week0 >= sum(weeks)`; a week-0 figure SMALLER than its
own weeks would mean the trap has changed shape, and that still refuses.

NGS IS A QUALIFYING-THRESHOLD LEADERBOARD, TWICE OVER. 120-132 receivers a
season against ~500 with at least one target, and then the weeks within a
covered player's season are filtered again - 1,166 of 1,325 receiving
player-seasons are missing at least one week. Every figure here is over the
covered population and says so; none of it generalises to a roster, and the
lag-1 pairing only ever uses CONSECUTIVE calendar weeks, so a filtered-out week
breaks a pair rather than silently spanning it.
"""
import argparse
import sys
import time

from analytics import metrics, paths

FAMILIES = {
    "receiving": ("ngs_receiving.parquet", "targets"),
    "rushing": ("ngs_rushing.parquet", "rush_attempts"),
    "passing": ("ngs_passing.parquet", "attempts"),
}

# NGS column -> how the play-by-play makes the same number, if it can.
# `None` means the play-by-play has no way to it: these are the tracking
# columns, and they are the only ones that can add anything.
PBP_TWIN = {
    "receiving": {
        "targets": "SUM(is_target)",
        "receptions": "SUM(is_reception)",
        "yards": "SUM(yards)",
        "avg_intended_air_yards": "AVG(air_yards)",
        "avg_yac": "AVG(yac)",
        "catch_percentage": "100.0*SUM(is_reception)/SUM(is_target)",
        "avg_cushion": None,
        "avg_separation": None,
        "avg_expected_yac": None,
        "avg_yac_above_expectation": None,
        "percent_share_of_intended_air_yards": None,   # team-denominated
    },
    "rushing": {
        "rush_attempts": "SUM(is_carry)",
        "rush_yards": "SUM(yards)",
        "avg_rush_yards": "AVG(yards)",
        "efficiency": None,
        "percent_attempts_gte_eight_defenders": None,
        "avg_time_to_los": None,
        "expected_rush_yards": None,
        "rush_yards_over_expected_per_att": None,
        "rush_pct_over_expected": None,
    },
    "passing": {
        "attempts": "SUM(is_dropback)",
        "avg_intended_air_yards": "AVG(air_yards)",
        "avg_time_to_throw": None,
        "aggressiveness": None,
        "avg_air_yards_to_sticks": None,
        "expected_completion_percentage": None,
        "completion_percentage_above_expectation": None,
        "avg_air_distance": None,
        "max_air_distance": None,
    },
}

ROLE = {"receiving": "receiver", "rushing": "rusher", "passing": "passer"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS f_ngs_week (
    family      TEXT    NOT NULL,
    season      INTEGER NOT NULL,
    week        INTEGER NOT NULL,   -- 1+ only; week 0 is the SEASON TOTAL
    player_id   TEXT    NOT NULL,
    column_name TEXT    NOT NULL,
    value       REAL,
    PRIMARY KEY (family, season, week, player_id, column_name)
);
CREATE INDEX IF NOT EXISTS ix_fngs ON f_ngs_week(family, column_name, player_id);

CREATE TABLE IF NOT EXISTS f_ngs_build (
    family        TEXT PRIMARY KEY,
    pull_date     TEXT    NOT NULL,
    seasons       TEXT    NOT NULL,
    week0_rows    INTEGER NOT NULL,
    weekly_rows   INTEGER NOT NULL,
    players       INTEGER NOT NULL,
    reconciled    INTEGER NOT NULL,   -- week-0 totals equal to the week sum
    unreconciled  INTEGER NOT NULL,   -- totals EXCEEDING it: weeks are missing
    built_ts      INTEGER NOT NULL
);
"""


def assert_week_zero_reconciles(df, family, volume_col, tol=1e-6):
    """Prove week 0 IS the season total before excluding it.

    Returns (exact, short, over). A silent double is invisible to every check a
    reader would run, so the check has to be the redundant-looking one: does
    the part sum to the whole, or is the whole sitting among the parts.

    THE INVARIANT IS `week0 >= sum(weeks)`, NOT EQUALITY, and finding that out
    is why this check exists. Written with equality it refused the build at
    1,166 of 1,325 receiving rows - and the refusal was right, because the
    reason is a second coverage fact nobody had measured: NGS FILTERS WEEKLY
    ROWS TOO. A player below the weekly qualifying threshold has no row for
    that week, while his targets still count in the season total. Ja'Marr Chase
    2024 reconciles exactly at 175; Tyreek Hill reads 123 against 114 summed
    over 14 weekly rows, and the missing 9 are weeks he does not appear in.

    `over` must stay zero. A week-0 figure SMALLER than the sum of its own weeks
    would mean week 0 is not the total and the double-count trap has changed
    shape.
    """
    import polars as pl
    wk0 = (df.filter(pl.col("week") == 0)
           .select(["season", "player_gsis_id", volume_col])
           .rename({volume_col: "total"}))
    rest = (df.filter(pl.col("week") > 0)
            .group_by(["season", "player_gsis_id"])
            .agg(pl.col(volume_col).sum().alias("summed")))
    j = wk0.join(rest, on=["season", "player_gsis_id"], how="inner")
    if j.height == 0:
        raise SystemExit("no week-0 rows to reconcile for %s - the trap this "
                         "checks for may have changed shape" % family)
    exact = j.filter((pl.col("total") - pl.col("summed")).abs() <= tol).height
    over = j.filter(pl.col("summed") - pl.col("total") > tol).height
    return exact, j.height - exact - over, over


def build(verbose=True):
    import polars as pl
    con = paths.connect()
    con.executescript(SCHEMA)
    now = int(time.time())
    for family, (asset, volume_col) in FAMILIES.items():
        a = paths.latest_asset(asset)
        if not a:
            raise SystemExit("%s is not in the archive" % asset)
        df = pl.read_parquet(a[0]).filter(pl.col("season_type") == "REG")
        exact, short, over = assert_week_zero_reconciles(df, family, volume_col)
        if over:
            raise SystemExit(
                "%s: %d week-0 rows are SMALLER than the sum of their own weekly "
                "rows. Week 0 is then not the season total and the double-count "
                "trap has changed shape - re-derive it before trusting anything "
                "built on this." % (family, over))
        wk0_rows = df.filter(pl.col("week") == 0).height
        weekly = df.filter(pl.col("week") > 0)
        cols = [c for c in PBP_TWIN[family] if c in weekly.columns]
        long = (weekly.select(["season", "week", "player_gsis_id"] + cols)
                .unpivot(index=["season", "week", "player_gsis_id"],
                         variable_name="column_name", value_name="value")
                .filter(pl.col("player_gsis_id").is_not_null()))
        con.execute("DELETE FROM f_ngs_week WHERE family=?", (family,))
        con.executemany(
            "INSERT OR REPLACE INTO f_ngs_week VALUES (?,?,?,?,?,?)",
            [(family, s, w, p, c, float(v) if v is not None else None)
             for s, w, p, c, v in long.rows()])
        players = weekly["player_gsis_id"].n_unique()
        con.execute("INSERT OR REPLACE INTO f_ngs_build VALUES (?,?,?,?,?,?,?,?,?)",
                    (family, a[1], "%d-%d" % (weekly["season"].min(),
                                              weekly["season"].max()),
                     wk0_rows, weekly.height, players, exact, short, now))
        con.commit()
        if verbose:
            print("  %-10s %s  weeks 1+ %6d rows  %4d players  week0 %5d  "
                  "exact %4d  short %4d (weeks missing below the weekly "
                  "threshold)" % (family, a[1], weekly.height, players,
                                  wk0_rows, exact, short), flush=True)
    return True


# ---------------------------------------------------------------------------
# 1. can the play-by-play already make this number?
# ---------------------------------------------------------------------------

def redundancy(con, family):
    """[(column, n, pearson_r)] against the play-by-play twin, where one exists."""
    import math
    role = ROLE[family]
    out = []
    for column, expr in PBP_TWIN[family].items():
        if expr is None:
            out.append((column, 0, None))
            continue
        pbp = {}
        for s, w, p, v in con.execute(
                "SELECT season, week, player_id, %s FROM f_play_usage "
                "WHERE role=? AND season_type='REG' "
                "GROUP BY season, week, player_id" % expr, (role,)):
            if v is not None:
                pbp[(s, w, p)] = float(v)
        xs, ys = [], []
        for s, w, p, v in con.execute(
                "SELECT season, week, player_id, value FROM f_ngs_week "
                "WHERE family=? AND column_name=? AND value IS NOT NULL",
                (family, column)):
            got = pbp.get((s, w, p))
            if got is not None:
                xs.append(float(v))
                ys.append(got)
        if len(xs) < 50:
            out.append((column, len(xs), None))
            continue
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        vx = sum((a - mx) ** 2 for a in xs)
        vy = sum((b - my) ** 2 for b in ys)
        r = cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else None
        out.append((column, n, r))
    return out


# ---------------------------------------------------------------------------
# 2. does it carry within-player signal?
# ---------------------------------------------------------------------------

def stability(con, family, column, draws=2000):
    """The F02 decomposition, on an NGS column. {family: Estimate}."""
    from analytics import stability as st
    from analytics.intervals import histogram_bootstrap
    by_player = {}
    for s, w, p, v in con.execute(
            "SELECT season, week, player_id, value FROM f_ngs_week "
            "WHERE family=? AND column_name=? AND value IS NOT NULL",
            (family, column)):
        by_player.setdefault(p, {})[(s, w)] = float(v)
    vec, rows = st._sufficient(by_player)
    if len(vec) < 30:
        return None
    return histogram_bootstrap(vec, {"within_lag1": st._pearson,
                                     "naive_lag1": st._naive,
                                     "between": st._between},
                               draws=draws, rows_by_block=rows,
                               subject="ngs.%s.%s" % (family, column))


UNIQUE = [(f, c) for f, cols in PBP_TWIN.items()
          for c, expr in cols.items() if expr is None]


def metric_for(family, column, kind):
    return metrics.Metric(
        key="ngs_stability.%s.%s.%s" % (kind, family, column),
        label="%s, %s (Next Gen Stats)" % (column, kind),
        unit=("lag-1 autocorrelation of the player's weekly deviation from his "
              "own season mean" if kind == "within_lag1" else
              "pooled lag-1 correlation of raw weekly values" if kind == "naive_lag1"
              else "share of variance between player-seasons"),
        subject_type="league", block="player", basis="ngs",
        availability="current", slice_kind="",
        shares_denominator="team" if "share" in column else "own",
        # NGS is a single non-seasonal release, so the coverage survey - which
        # scans `..._{season}.parquet` assets - has nothing to measure it
        # against and `derive_range` would answer "the whole archive". That is
        # what `floor_season` is for: a bound the data cannot express. Measured,
        # not assumed: `f_ngs_week` holds 2016 onward and F01 section 3b records
        # the release carrying 2016-2026.
        floor_season=2016,
        floor_reason="ngs (first season on the release, measured in f_ngs_week)",
        requires=())


def publish(con, verbose=True):
    written = 0
    for family, column in UNIQUE:
        got = stability(con, family, column)
        if got is None:
            if verbose:
                print("  %-12s %-42s too few players" % (family, column))
            continue
        for kind, est in got.items():
            m = metric_for(family, column, kind)
            written += metrics.publish(con, m, [("_league", "", est)],
                                       *_seasons(con, family))
        if verbose:
            w = got["within_lag1"]
            print("  %-12s %-42s within %+.3f [%+.3f, %+.3f]  n=%d players"
                  % (family, column, w.est, w.lo, w.hi, w.n), flush=True)
    return written


def _seasons(con, family):
    row = con.execute("SELECT MIN(season), MAX(season) FROM f_ngs_week "
                      "WHERE family=?", (family,)).fetchone()
    return int(row[0]), int(row[1])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--redundancy", action="store_true")
    ap.add_argument("--stability", action="store_true")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args(argv)
    if a.build:
        build()
    con = paths.connect(read_only=not (a.build or a.publish))
    if a.redundancy:
        print("\nCan the play-by-play already make this number?\n")
        print("  %-12s %-42s %8s %8s  %s"
              % ("family", "column", "n", "r", "verdict"))
        for family in FAMILIES:
            for column, n, r in redundancy(con, family):
                if r is None and n == 0:
                    verdict = "NO PBP EQUIVALENT - candidate"
                    rs = "-"
                elif r is None:
                    verdict = "too few paired rows"
                    rs = "-"
                else:
                    rs = "%.4f" % r
                    verdict = ("redundant" if r >= 0.99 else
                               "near-duplicate" if r >= 0.95 else
                               "related" if r >= 0.7 else "different")
                print("  %-12s %-42s %8d %8s  %s" % (family, column, n, rs, verdict))
    if a.stability:
        print("\nDoes it carry within-player signal?\n")
        print("  %-12s %-38s %22s %22s %16s %7s"
              % ("family", "column", "within-player lag-1", "naive lag-1",
                 "between-player", "players"))
        for family, column in UNIQUE:
            got = stability(con, family, column)
            if got is None:
                print("  %-12s %-38s %22s" % (family, column, "too few players"))
                continue
            w, nv, bt = got["within_lag1"], got["naive_lag1"], got["between"]
            print("  %-12s %-38s %+.3f [%+.3f,%+.3f] %+.3f [%+.3f,%+.3f] "
                  "%.3f [%.3f,%.3f] %7d"
                  % (family, column, w.est, w.lo, w.hi, nv.est, nv.lo, nv.hi,
                     bt.est, bt.lo, bt.hi, w.n))
    if a.publish:
        n = publish(con)
        print("published %d values" % n)
    if not (a.build or a.redundancy or a.stability or a.publish):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
