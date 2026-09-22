"""Drafting strengths: does any team draft better than its picks predict? (F07)

    python -m analytics.drafting                 # the whole measurement
    python -m analytics.drafting --json out.json # and keep the numbers

A MEASUREMENT, NOT A PUBLISH. Nothing here writes to the store or the export;
the export shape is f-02.

WHAT "DRAFT VALUE REALISED" MEANS HERE - the choice decides the result, so it
is stated first and carried by every figure:

  roster4   PRIMARY. Share of regular-season weeks, over the pick's first FOUR
            seasons, on an NFL team's roster - the 53 or its reserve lists -
            for ANY team. Source: nflverse `roster_weekly`, NFL-sourced, 2002+.
            A FIXED horizon, so a 2004 pick and a 2019 pick are measured over
            the same four years and no class is censored. Classes 2002-2022.
            It is EMPLOYMENT, not playing time, and that is forced: BEFORE 2016
            THE FEED'S `status` IS A SEASON STAMP, NOT A WEEKLY STATUS. Only
            2-9% of 2002-2015 player-seasons carry more than one status, against
            63-72% from 2020; Stephen Hill (NYJ 2012; 23 PFR games, all in
            2012-13) is `RES` in every week of both seasons. So "ACT weeks"
            would score a player who finished the year on IR as never having
            played, and would read teams' IR habits as drafting skill. Presence
            is weekly in every era; active status is not. Practice squad (DEV)
            is excluded: it appears in this feed only from 2006 and the
            population jumps ~1,950 -> ~3,100 player-seasons in 2016-17.
            What it cannot see: QUALITY and HEALTH. A 53rd man, an All-Pro and
            a player on injured reserve all score 1.0 for a week.
  snaps4    Offensive + defensive snaps in the first four regular seasons,
            class-relative. Playing time, which roster4 cannot see. Source:
            `snap_counts` (PFR-sourced), which starts in 2013 - so classes
            2013-2022 only, ten of them. Kickers, punters and snappers score ~0.
  bust      roster4 == 0: on no team's roster for one regular-season week
            in four seasons. SURVIVORSHIP IS THE SIGNAL, not missing data - a
            pick that never plays is scored as exactly that, never dropped.
  w_av      SECONDARY. Pro-Football-Reference weighted career Approximate
            Value, null -> 0 (null is "never played": w_av and games are null
            on exactly the same rows). It sees quality, but it is a CAREER
            TOTAL AS OF THE PULL DATE, so it is censored by class age and is
            not as-of: it cannot back a forecast. Made class-relative (divided
            by the class mean) before anything else. Classes 2002-2021. Also
            PFR-sourced - see the licensing note in F07.

THE NULL IS A TYPICAL TEAM, NOT ZERO. Every outcome is turned into a residual
against the league's expectation AT THAT PICK SLOT, and residuals are centred
within each draft class. So 0 is "drafted exactly as well as the league does
with the same picks", and a team's number is value over the pick-slot
expectation, per pick. Draft capital (how many picks, how high) is therefore
not credited as skill.

INFERENCE, and why each piece:

  - Team intervals: block bootstrap BY DRAFT CLASS, never by pick. A class
    shares a front office, a board and a year; picks within it are not
    independent. Draws are independent PER TEAM (seed from the team id), per
    the shared-denominator rule - residuals are centred within a class, so
    teams in one class share a denominator exactly as teammates do.
  - Separation: between-team SD of team means against a PERMUTATION null that
    shuffles team labels among the picks of each class. That is "no drafting
    skill" made literal, and it keeps each team's pick count per class.
  - Forecast: walk-forward. A team's record over classes whose four-year
    horizon had CLOSED by draft t (classes <= t-4) predicts its class-t
    residual. Anything nearer is look-ahead.
  - Era: 2002-2010 against 2011-2022 - the 2011 CBA's rookie wage scale
    changed what it costs to keep or cut a pick, which is what roster4 counts.
    The 53-man count in the roster feed also steps from ~48 to ~51 per team
    week in 2016 (a status-coding change); class centring absorbs a shift that
    hits every team alike, and nothing here compares a level across it.
  - Position: residuals refitted against a pick-slot expectation WITHIN the
    position group, and the separation test run per group, BH-corrected.
"""
import argparse
import json
import math
import zlib

import numpy as np
import polars as pl

from analytics import paths

DRAFT_ASSET = "draft_picks.parquet"
ROSTER_PATTERN = "roster_weekly_{season}.parquet"

HORIZON = 4                     # seasons in roster4
ROSTER_FIRST = 2002             # roster_weekly starts here
# On a team's 53 or its reserve lists. NOT: DEV (practice squad, in this feed
# only from 2006), CUT, RET, the 2020 free-agent codes, E01/E14 (practice-squad
# exemptions), null. The TR* codes are trade transitions and are kept: before
# 2016 they are stamped on a traded player's whole season with that team.
ON_ROSTER = ("ACT", "INA", "RES", "PUP", "SUS", "NWT", "RSN", "RSR", "EXE",
             "TRD", "TRC", "TRT")
SNAP_PATTERN = "snap_counts_{season}.parquet"
SNAP_FIRST = 2013               # the 2012 file exists and is empty
ERA_SPLIT = 2011                # rookie wage scale
DRAWS = 2000
PERMS = 2000
SEED = 20260922

# Relocations. Franchise, not city: a Raiders pick in 2004 and 2024 is one
# front-office lineage for this question.
FRANCHISE = {"OAK": "LVR", "RAI": "LVR", "SDG": "LAC", "STL": "LAR",
             "RAM": "LAR", "PHO": "ARI"}

# nflverse / GSIS codes (games.parquet, roster_weekly) -> draft_picks (PFR)
# franchise codes. The roster feed mixes both nflverse (GB, LV) and GSIS (ARZ,
# BLT, CLV, HST, SL) spellings; `to_franchise` refuses any code it cannot place.
GAMES_TEAM = {"GB": "GNB", "KC": "KAN", "NE": "NWE", "NO": "NOR", "SF": "SFO",
              "TB": "TAM", "LV": "LVR", "OAK": "LVR", "LA": "LAR", "STL": "LAR",
              "SL": "LAR", "SD": "LAC", "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE",
              "HST": "HOU"}
FRANCHISES = ("ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GNB HOU IND JAX KAN "
              "LAC LAR LVR MIA MIN NOR NWE NYG NYJ PHI PIT SEA SFO TAM TEN WAS").split()


def to_franchise(col: str) -> pl.Expr:
    """Any team code this module reads, as a franchise. Unknown codes become
    null here and are refused by `check_franchises`."""
    m = {**{f: f for f in FRANCHISES}, **FRANCHISE, **GAMES_TEAM}
    return pl.col(col).replace_strict(m, default=None)


def check_franchises(df: pl.DataFrame, col: str) -> None:
    bad = df.filter(to_franchise(col).is_null())[col].unique().to_list()
    if bad:
        raise ValueError(f"team codes with no franchise: {sorted(map(str, bad))}")

# `category` from draft_picks. Singletons are folded, not dropped.
POS_GROUP = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "OL": "OL",
             "OG": "OL", "DL": "DL", "LB": "LB", "DB": "DB", "FS": "DB",
             "K": "ST", "P": "ST", "LS": "ST", "KR": "ST"}


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def load_picks(first: int = ROSTER_FIRST, last: int = None) -> pl.DataFrame:
    """Every pick in [first, last] with franchise and position group.

    `last` defaults to the newest class whose four-year horizon has closed.
    """
    hit = paths.latest_asset(DRAFT_ASSET)
    if hit is None:
        raise FileNotFoundError(DRAFT_ASSET + " is not in the nflverse mirror")
    path, pulled = hit
    d = pl.read_parquet(path)
    if last is None:
        last = int(d["season"].max()) - HORIZON  # 2026 -> 2022
    d = d.filter(pl.col("season").is_between(first, last))
    if d.height == 0:
        raise ValueError(f"no picks in {first}-{last}")
    unknown = set(d["category"].unique().to_list()) - set(POS_GROUP)
    if unknown:
        raise ValueError(f"unmapped position categories {sorted(unknown)}")
    return d.with_columns(
        to_franchise("team").alias("franchise"),
        pl.col("category").replace_strict(POS_GROUP).alias("pos_group"),
        pl.col("w_av").fill_null(0).cast(pl.Float64).alias("w_av0"),
        pl.lit(pulled).alias("pulled"),
    )


def load_rosters(first: int, last: int) -> pl.DataFrame:
    """Regular-season weekly roster rows for seasons [first, last]."""
    frames = []
    for season, path, _day in paths.seasonal_files(ROSTER_PATTERN):
        if first <= season <= last:
            frames.append(pl.read_parquet(
                path, columns=["season", "week", "team", "gsis_id", "pfr_id",
                               "status", "game_type"]))
    if not frames:
        raise FileNotFoundError("no roster_weekly seasons in the mirror")
    r = pl.concat(frames).filter(pl.col("game_type") == "REG")
    got = set(r["season"].unique().to_list())
    missing = sorted(set(range(first, last + 1)) - got)
    if missing:
        raise ValueError(f"roster_weekly missing seasons {missing}")
    return r


def load_snaps(first: int, last: int) -> pl.DataFrame:
    """Regular-season offensive + defensive snaps per (pfr id, season)."""
    frames = [pl.read_parquet(p, columns=["season", "game_type", "pfr_player_id",
                                          "offense_snaps", "defense_snaps"])
              for s, p, _ in paths.seasonal_files(SNAP_PATTERN) if first <= s <= last]
    if not frames:
        raise FileNotFoundError("no snap_counts seasons in the mirror")
    x = pl.concat(frames).filter(pl.col("game_type") == "REG")
    got = set(x["season"].unique().to_list())
    missing = sorted(set(range(first, last + 1)) - got)
    if missing:
        raise ValueError(f"snap_counts missing or empty for seasons {missing}")
    return x.group_by("pfr_player_id", "season").agg(
        (pl.col("offense_snaps").fill_null(0) + pl.col("defense_snaps").fill_null(0))
        .sum().alias("snaps"))


def snaps4(picks: pl.DataFrame, snaps: pl.DataFrame) -> pl.DataFrame:
    """Add `snaps4`: snaps in the pick's first HORIZON regular seasons. A pick
    with no snap row played no snaps - scored 0, never dropped."""
    j = (picks.select("season", "pfr_player_id").with_row_index("_pid")
         .rename({"season": "ds"})
         .join(snaps, on="pfr_player_id")
         .filter((pl.col("season") >= pl.col("ds"))
                 & (pl.col("season") < pl.col("ds") + HORIZON))
         .group_by("_pid").agg(pl.col("snaps").sum().alias("snaps4")))
    return (picks.with_row_index("_pid").join(j, on="_pid", how="left")
            .with_columns(pl.col("snaps4").fill_null(0).cast(pl.Float64))
            .drop("_pid"))


def roster4(picks: pl.DataFrame, rosters: pl.DataFrame) -> pl.DataFrame:
    """Add `roster_weeks`, `possible_weeks`, `roster4`, `bust`, `own_weeks`.

    Joined on gsis_id, falling back to pfr_id. The fallback is kept and
    MEASURED TO RESOLVE NOTHING today: 219 picks in 2002-2022 carry no
    gsis_id, `players.parquet` maps none of their pfr ids to one, and the
    roster feed's own `pfr_id` is only 2-15% populated before 2010. Nine of
    the 219 played (Jon Stinchcomb, 90 games) and score 0 here - a known
    misclassification of 9 in 5,371, never repaired by a name join, and
    counted in `unmatched_played` on every run alongside the players whose
    first four seasons genuinely held no roster week.
    """
    check_franchises(rosters, "team")
    check_franchises(picks, "team")
    weeks = rosters.group_by("season").agg(pl.col("week").n_unique().alias("wk"))
    wk = dict(zip(weeks["season"].to_list(), weeks["wk"].to_list()))
    # Cast the ids: a season whose pfr_id is entirely null reads as dtype Null
    # and will not join against a string key.
    on = rosters.filter(pl.col("status").is_in(list(ON_ROSTER))).select(
        "season", "week", "team", pl.col("gsis_id").cast(pl.Utf8),
        pl.col("pfr_id").cast(pl.Utf8)).unique()

    by_gsis = on.filter(pl.col("gsis_id").is_not_null()).rename(
        {"gsis_id": "key"}).drop("pfr_id")
    by_pfr = on.filter(pl.col("pfr_id").is_not_null()).rename(
        {"pfr_id": "key"}).drop("gsis_id")

    p = picks.with_row_index("_pid").with_columns(
        pl.when(pl.col("gsis_id").is_not_null()).then(pl.col("gsis_id"))
        .otherwise(pl.col("pfr_player_id")).alias("key"),
        pl.when(pl.col("gsis_id").is_not_null()).then(pl.lit("gsis"))
        .otherwise(pl.lit("pfr")).alias("key_kind"))

    def count(src, kind):
        j = (p.filter(pl.col("key_kind") == kind)
             .select("_pid", "key", "season", "team").rename(
                 {"season": "draft_season", "team": "draft_team"})
             .join(src, on="key")
             .filter((pl.col("season") >= pl.col("draft_season"))
                     & (pl.col("season") < pl.col("draft_season") + HORIZON)))
        return j.group_by("_pid").agg(
            pl.struct("season", "week").n_unique().alias("roster_weeks"),
            pl.struct("season", "week").filter(
                to_franchise("team") == to_franchise("draft_team")
            ).n_unique().alias("own_weeks"))

    counts = pl.concat([count(by_gsis, "gsis"), count(by_pfr, "pfr")])
    out = p.join(counts, on="_pid", how="left").with_columns(
        pl.col("roster_weeks").fill_null(0), pl.col("own_weeks").fill_null(0))
    possible = [sum(wk.get(s + k, 0) for k in range(HORIZON))
                for s in out["season"].to_list()]
    out = out.with_columns(pl.Series("possible_weeks", possible))
    if (out["possible_weeks"] == 0).any():
        raise ValueError("a class with no roster weeks in its horizon")
    return out.with_columns(
        (pl.col("roster_weeks") / pl.col("possible_weeks")).alias("roster4"),
        (pl.col("roster_weeks") == 0).cast(pl.Float64).alias("bust"),
    ).drop("_pid", "key")


# ---------------------------------------------------------------------------
# the pick-slot expectation, and residuals centred on a typical team
# ---------------------------------------------------------------------------

def slot_expectation(pick, y, bandwidth: float = 0.15):
    """E[y | overall pick], a Gaussian kernel smoother in log(pick).

    Pooled across every class passed in: 20 classes give ~20 observations at
    pick 1, so a per-class curve would be noise at the top of the board where
    the stakes are highest. Log spacing because value falls fastest early.
    """
    pick = np.asarray(pick, dtype=float)
    y = np.asarray(y, dtype=float)
    grid = np.unique(pick)
    lg, lp = np.log(grid)[:, None], np.log(pick)[None, :]
    w = np.exp(-0.5 * ((lg - lp) / bandwidth) ** 2)
    fit = dict(zip(grid, (w @ y) / w.sum(axis=1)))
    return np.array([fit[p] for p in pick])


def residualize(df: pl.DataFrame, col: str, within: str = None,
                bandwidth: float = 0.15, class_relative: bool = False):
    """`col` minus its pick-slot expectation, centred within each class.

    `within` fits a separate slot curve per group (position). Centring is per
    class (and per group when `within` is set), so every class's residuals sum
    to zero: the league-average team is 0 by construction, which is the null.
    `class_relative` divides by the class mean first - for career totals whose
    level depends on how long the class has had to accrue them.
    """
    y = pl.col(col).cast(pl.Float64)
    if class_relative:
        y = (y / y.mean().over("season")).fill_nan(0.0)
    out = df.with_columns(y.alias("_y"), pl.lit(0.0).alias("_e"))
    groups = [None] if within is None else out[within].unique().to_list()
    parts = []
    for g in groups:
        part = out if g is None else out.filter(pl.col(within) == g)
        bw = bandwidth if g is None else max(bandwidth, 0.30)
        e = slot_expectation(part["pick"].to_numpy(), part["_y"].to_numpy(), bw)
        parts.append(part.with_columns(pl.Series("_e", e)))
    out = pl.concat(parts)
    keys = ["season"] + ([within] if within else [])
    out = out.with_columns((pl.col("_y") - pl.col("_e")).alias("_r"))
    out = out.with_columns(
        (pl.col("_r") - pl.col("_r").mean().over(keys)).alias("resid"))
    return out.drop("_y", "_e", "_r")


# ---------------------------------------------------------------------------
# inference
# ---------------------------------------------------------------------------

def _team_class_sums(df):
    """teams, classes, S[t, c] = residual sum, N[t, c] = pick count."""
    teams = sorted(df["franchise"].unique().to_list())
    classes = sorted(df["season"].unique().to_list())
    ti = {t: i for i, t in enumerate(teams)}
    ci = {c: i for i, c in enumerate(classes)}
    S = np.zeros((len(teams), len(classes)))
    N = np.zeros_like(S)
    for t, c, r in zip(df["franchise"], df["season"], df["resid"]):
        S[ti[t], ci[c]] += r
        N[ti[t], ci[c]] += 1
    return teams, classes, S, N


def _seed(subject):
    """Deterministic per subject. crc32, never hash(): str hashing is salted
    per process and the intervals would not reproduce."""
    return [SEED, zlib.crc32(str(subject).encode("utf-8"))]


def team_intervals(df, draws: int = DRAWS, conf: float = 0.95):
    """Per team: mean residual per pick, class-block bootstrap interval.

    Draws are independent PER TEAM (see module docstring). `n` is the number
    of classes, which is the sample; `picks` is the row count, which is not.
    """
    teams, classes, S, N = _team_class_sums(df)
    k = len(classes)
    out = []
    for i, t in enumerate(teams):
        rng = np.random.default_rng(_seed(t))
        idx = rng.integers(0, k, size=(draws, k))
        s, n = S[i][idx].sum(axis=1), N[i][idx].sum(axis=1)
        stat = np.where(n > 0, s / np.where(n > 0, n, 1), np.nan)
        stat = stat[~np.isnan(stat)]
        est = S[i].sum() / N[i].sum()
        lo, hi = np.quantile(stat, [(1 - conf) / 2, (1 + conf) / 2])
        out.append({"team": t, "est": float(est), "lo": float(min(lo, est)),
                    "hi": float(max(hi, est)), "n": k, "picks": int(N[i].sum()),
                    "excludes_null": bool(lo > 0 or hi < 0)})
    return out


def _between_sd(S, N):
    m = S.sum(axis=1) / N.sum(axis=1)
    return float(np.std(m, ddof=1))


def separation(df, perms: int = PERMS, seed: int = SEED):
    """Do teams differ by more than shuffled labels would?

    Observed between-team SD of mean residual per pick, against a null that
    permutes team labels among each class's picks. Returns the SD in the
    outcome's own units, the null mean and 95th percentile, a one-sided p,
    and the signal share: 1 - E[var_null] / var_obs, floored at 0. That share
    is the reliability of a team's number over this window - the fraction of
    the spread you see that is not noise.
    """
    teams, classes, S, N = _team_class_sums(df)
    obs = _between_sd(S, N)
    rng = np.random.default_rng(seed)
    t_codes = {t: i for i, t in enumerate(teams)}
    by_class = [(df.filter(pl.col("season") == c)["franchise"]
                 .replace_strict(t_codes).to_numpy(),
                 df.filter(pl.col("season") == c)["resid"].to_numpy())
                for c in classes]
    Npicks = N.sum(axis=1)
    null = np.empty(perms)
    for b in range(perms):
        tot = np.zeros(len(teams))
        for labels, r in by_class:
            np.add.at(tot, rng.permutation(labels), r)
        null[b] = np.std(tot / Npicks, ddof=1)
    share = max(0.0, 1.0 - float(np.mean(null ** 2)) / obs ** 2) if obs > 0 else 0.0
    return {"between_sd": obs, "null_sd_mean": float(null.mean()),
            "null_sd_p95": float(np.quantile(null, 0.95)),
            "p": float((1 + (null >= obs).sum()) / (perms + 1)),
            "signal_share": share,
            "signal_sd": math.sqrt(share) * obs,
            "teams": len(teams), "classes": len(classes), "picks": df.height}


def walk_forward(df, lag: int = HORIZON, min_prior: int = 3,
                 draws: int = DRAWS, seed: int = SEED):
    """Does a team's CLOSED draft record predict its next class?

    For each target class t: predictor = the team's mean residual per pick over
    classes <= t - lag (the horizon of class t-lag closed before draft t, so
    this is what a front office could have known), with at least `min_prior`
    such classes. Outcome = the team's mean residual in class t. Statistic =
    Pearson r across teams, averaged over targets; interval by bootstrapping
    TARGET CLASSES. Null is 0.

    Predictor windows of neighbouring targets overlap, so targets are not fully
    independent and the interval is somewhat optimistic. It is reported that
    way rather than hidden.
    """
    teams, classes, S, N = _team_class_sums(df)
    rows = []
    for j, t in enumerate(classes):
        prior = [i for i, c in enumerate(classes) if c <= t - lag]
        if len(prior) < min_prior:
            continue
        ps, pn = S[:, prior].sum(axis=1), N[:, prior].sum(axis=1)
        ok = (pn > 0) & (N[:, j] > 0)
        if ok.sum() < 5:
            continue
        x = ps[ok] / pn[ok]
        y = S[ok, j] / N[ok, j]
        if np.std(x) == 0 or np.std(y) == 0:
            continue
        rows.append({"target": int(t), "r": float(np.corrcoef(x, y)[0, 1]),
                     "slope": float(np.polyfit(x, y, 1)[0]),
                     "teams": int(ok.sum()), "prior_classes": len(prior)})
    if not rows:
        return {"targets": 0, "r": None, "lo": None, "hi": None, "rows": []}
    rs = np.array([r["r"] for r in rows])
    rng = np.random.default_rng(seed)
    boot = rs[rng.integers(0, len(rs), size=(draws, len(rs)))].mean(axis=1)
    lo, hi = np.quantile(boot, [0.025, 0.975])
    est = float(rs.mean())
    return {"targets": len(rows), "r": est, "lo": float(min(lo, est)),
            "hi": float(max(hi, est)),
            "slope": float(np.mean([r["slope"] for r in rows])),
            "readable": len(rows) >= 5, "rows": rows}


def split_half(df):
    """Team means over odd classes against even classes, Pearson r, with the
    Spearman-Brown step-up to the full window. Descriptive persistence only -
    NOT as-of, and not a forecast."""
    teams, classes, S, N = _team_class_sums(df)
    odd = [i for i, c in enumerate(classes) if c % 2]
    even = [i for i, c in enumerate(classes) if not c % 2]
    a = S[:, odd].sum(axis=1) / N[:, odd].sum(axis=1)
    b = S[:, even].sum(axis=1) / N[:, even].sum(axis=1)
    r = float(np.corrcoef(a, b)[0, 1])
    return {"r": r, "spearman_brown": 2 * r / (1 + r) if r > -1 else None,
            "teams": len(teams)}


def bands(df, draws: int = DRAWS, conf: float = 0.95):
    """Bands against the LEADER, never against the row above.

    The leader is the highest estimate still unbanded. Every remaining team
    whose CONTRAST with the leader (team minus leader, bootstrapped as ONE
    quantity over shared class blocks) has an interval containing 0 joins the
    leader's band. Repeat on what is left. Alphabetical within a band; bands
    carry no numbering beyond their order, and a single band is the finding
    "no team separates from the leader".
    """
    teams, classes, S, N = _team_class_sums(df)
    k = len(classes)
    means = S.sum(axis=1) / N.sum(axis=1)
    left = set(range(len(teams)))
    out = []
    while left:
        lead = max(left, key=lambda i: means[i])
        members = [lead]
        for i in sorted(left - {lead}):
            rng = np.random.default_rng(_seed(teams[lead] + "|" + teams[i]))
            idx = rng.integers(0, k, size=(draws, k))
            d = (S[i][idx].sum(1) / N[i][idx].sum(1)
                 - S[lead][idx].sum(1) / N[lead][idx].sum(1))
            lo, hi = np.quantile(d, [(1 - conf) / 2, (1 + conf) / 2])
            if lo <= 0 <= hi:
                members.append(i)
        out.append(sorted(teams[i] for i in members))
        left -= set(members)
    return out


def null_exclusions(df, perms: int = 200, draws: int = 500, seed: int = SEED):
    """How many teams' intervals exclude 0 when there is NO skill, measured.

    `0.05 x 32` assumes each interval has exact 95% coverage. A percentile
    block bootstrap over 10-20 classes does not - it runs narrow on few blocks
    - so the chance baseline is measured instead: permute team labels within
    each class and count exclusions exactly as `team_intervals` would.
    Returns the mean count and its 95th percentile under the null.
    """
    rng = np.random.default_rng(seed)
    classes = sorted(df["season"].unique().to_list())
    parts = [df.filter(pl.col("season") == c) for c in classes]
    counts = []
    for _ in range(perms):
        shuffled = pl.concat([
            p.with_columns(pl.Series("franchise",
                                     rng.permutation(p["franchise"].to_numpy())))
            for p in parts])
        counts.append(sum(t["excludes_null"]
                          for t in team_intervals(shuffled, draws)))
    counts = np.array(counts)
    return {"mean": float(counts.mean()), "p95": float(np.quantile(counts, 0.95)),
            "perms": perms}


def team_win_pct(first: int, last: int) -> dict:
    """Regular-season win share per franchise over seasons [first, last], ties
    as half. The environment a pick's AV accrues in."""
    g = pl.read_parquet(paths.latest_asset("games.parquet")[0]).filter(
        (pl.col("game_type") == "REG") & pl.col("season").is_between(first, last)
        & pl.col("result").is_not_null())
    home = g.select(pl.col("home_team").alias("t"),
                    (pl.col("result").sign() * 0.5 + 0.5).alias("w"))
    away = g.select(pl.col("away_team").alias("t"),
                    (-pl.col("result").sign() * 0.5 + 0.5).alias("w"))
    both = pl.concat([home, away])
    check_franchises(both, "t")
    wp = (both.with_columns(to_franchise("t"))
          .group_by("t").agg(pl.col("w").mean()))
    return dict(zip(wp["t"].to_list(), wp["w"].to_list()))


def environment(df, winpct: dict):
    """Pearson r between a team's mean residual and its win share, with a
    Fisher-z 95% interval over the 32 teams.

    A correlation here does NOT say which way it runs - drafting well wins
    games, and winning inflates an allocated metric like AV - which is the
    point: where it is large, the outcome cannot separate the two.
    """
    teams, _classes, S, N = _team_class_sums(df)
    x = np.array([S[i].sum() / N[i].sum() for i in range(len(teams))])
    missing = [t for t in teams if t not in winpct]
    if missing:
        raise ValueError(f"no win share for {missing}")
    y = np.array([winpct[t] for t in teams])
    r = float(np.corrcoef(x, y)[0, 1])
    z, se = math.atanh(r), 1 / math.sqrt(len(teams) - 3)
    return {"r": r, "lo": math.tanh(z - 1.96 * se), "hi": math.tanh(z + 1.96 * se),
            "teams": len(teams)}


def bh(pvals, q: float = 0.10):
    """Benjamini-Hochberg: which of `pvals` survive at FDR q."""
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    m, keep = len(pvals), set()
    for rank, i in enumerate(order, 1):
        if pvals[i] <= q * rank / m:
            keep = set(order[:rank])
    return [i in keep for i in range(len(pvals))]


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------

def outcome_frames(picks):
    """{name: residualized frame} for the three outcomes, each on its window."""
    av_last = int(picks["season"].max()) - 1   # one more season of accrual
    out = {
        "roster4": residualize(picks, "roster4"),
        "bust": residualize(picks, "bust"),
        "w_av": residualize(picks.filter(pl.col("season") <= av_last), "w_av0",
                            class_relative=True),
    }
    if "snaps4" in picks.columns:
        out["snaps4"] = residualize(
            picks.filter(pl.col("season") >= SNAP_FIRST), "snaps4",
            class_relative=True)
    return out


def measure(perms: int = PERMS, draws: int = DRAWS):
    picks = load_picks()
    first, last = int(picks["season"].min()), int(picks["season"].max())
    rost = load_rosters(first, last + HORIZON - 1)
    picks = roster4(picks, rost)
    picks = snaps4(picks, load_snaps(SNAP_FIRST, last + HORIZON - 1))
    unmatched_played = picks.filter(
        (pl.col("roster_weeks") == 0) & pl.col("games").is_not_null()
        & (pl.col("games") > 0)).height
    res = {"window": [first, last], "pulled": picks["pulled"][0],
           "picks": picks.height, "unmatched_played": unmatched_played,
           "bust_rate": float(picks["bust"].mean()),
           "outcomes": {}, "eras": {}, "positions": {}}

    # The seasons the roster horizons cover (2002-2025): complete seasons only.
    winpct = team_win_pct(first, last + HORIZON - 1)
    for name, f in outcome_frames(picks).items():
        ti = team_intervals(f, draws)
        sep = separation(f, perms)
        res["outcomes"][name] = {
            "classes": [int(f["season"].min()), int(f["season"].max())],
            "separation": sep,
            "environment": environment(f, winpct),
            "null_exclusions": null_exclusions(f, max(50, perms // 10),
                                               max(200, draws // 4)),
            "split_half": split_half(f),
            "walk_forward": (walk_forward(f) if name != "w_av" else
                             {"note": "career AV is a 2026 snapshot; no as-of forecast"}),
            "teams_excluding_null": sum(t["excludes_null"] for t in ti),
            "expected_by_chance": round(0.05 * len(ti), 1),
            "teams": ti,
            "bands": bands(f, draws),
            # The leader is a SELECTED MAXIMUM of 32 noisy numbers, and each
            # contrast against it is one of 31 unadjusted tests - so bands can
            # split a league the separation test cannot tell from shuffled
            # labels. They are read only where that test rejects.
            "bands_readable": sep["p"] < 0.05,
        }

    for label, lo, hi in (("2002-2010", first, ERA_SPLIT - 1),
                          ("2011-%d" % last, ERA_SPLIT, last)):
        sub = picks.filter(pl.col("season").is_between(lo, hi))
        f = residualize(sub, "roster4")
        res["eras"][label] = {"separation": separation(f, perms),
                              "walk_forward": walk_forward(f)}

    fpos = residualize(picks, "roster4", within="pos_group")
    groups = sorted(fpos["pos_group"].unique().to_list())
    for g in groups:
        f = fpos.filter(pl.col("pos_group") == g)
        ti = team_intervals(f, draws)
        res["positions"][g] = {"separation": separation(f, perms),
                               "teams_excluding_null": sum(t["excludes_null"] for t in ti),
                               "min_team_picks": min(t["picks"] for t in ti)}
    ps = [res["positions"][g]["separation"]["p"] for g in groups]
    for g, keep in zip(groups, bh(ps)):
        res["positions"][g]["bh_q10"] = keep
    return res


def _fmt_sep(s):
    return ("between-team SD %.4f  null %.4f (p95 %.4f)  p=%.4f  signal share %.2f"
            % (s["between_sd"], s["null_sd_mean"], s["null_sd_p95"], s["p"],
               s["signal_share"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--perms", type=int, default=PERMS)
    ap.add_argument("--draws", type=int, default=DRAWS)
    a = ap.parse_args(argv)
    r = measure(a.perms, a.draws)
    if r["picks"] < 1000:
        raise SystemExit(f"only {r['picks']} picks read - refusing to report")
    print("WINDOW  classes %d-%d, %d picks, draft_picks pulled %s"
          % (*r["window"], r["picks"], r["pulled"]))
    print("        bust rate %.3f; played but unmatched to any roster row: %d"
          % (r["bust_rate"], r["unmatched_played"]))
    for name, o in r["outcomes"].items():
        print(f"\n{name.upper()}  classes {o['classes'][0]}-{o['classes'][1]}")
        print("  separation   " + _fmt_sep(o["separation"]))
        sh = o["split_half"]
        print("  split-half   r=%.3f  (Spearman-Brown %.3f)" % (sh["r"], sh["spearman_brown"]))
        wf = o["walk_forward"]
        if "r" in wf and wf["r"] is not None:
            print("  walk-forward r=%.3f [%.3f, %.3f] over %d target classes, slope %.3f"
                  % (wf["r"], wf["lo"], wf["hi"], wf["targets"], wf["slope"]))
        else:
            print("  walk-forward " + wf.get("note", "not estimable"))
        ne, ev = o["null_exclusions"], o["environment"]
        print("  teams whose interval excludes a typical team: %d of %d"
              " (nominal chance %.1f; MEASURED under no skill %.1f, p95 %.0f)"
              % (o["teams_excluding_null"], len(o["teams"]), o["expected_by_chance"],
                 ne["mean"], ne["p95"]))
        print("  r(team residual, team win share) = %.3f [%.3f, %.3f]"
              % (ev["r"], ev["lo"], ev["hi"]))
        for t in o["teams"]:
            if t["excludes_null"]:
                print("     %s  %+.4f [%+.4f, %+.4f]  %d picks"
                      % (t["team"], t["est"], t["lo"], t["hi"], t["picks"]))
        print("  bands vs leader: %d  (%s)" % (len(o["bands"]),
              "readable" if o["bands_readable"] else
              "NOT READ - separation test does not reject"))
        for b in o["bands"]:
            print("     " + " ".join(b))
    print("\nERA (roster4)")
    for label, e in r["eras"].items():
        wf = e["walk_forward"]
        wfs = ("r=%.3f [%.3f, %.3f] n=%d" % (wf["r"], wf["lo"], wf["hi"], wf["targets"])
               if wf["r"] is not None else "not estimable")
        print(f"  {label}  " + _fmt_sep(e["separation"]) + "  walk-forward " + wfs)
    print("\nPOSITION (roster4, slot curve fitted within group; BH q=0.10 over groups)")
    for g, p in r["positions"].items():
        print(f"  {g:3s} " + _fmt_sep(p["separation"])
              + "  excl %2d  min picks/team %d  BH %s"
              % (p["teams_excluding_null"], p["min_team_picks"], p["bh_q10"]))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=1)
        print("\nwrote " + a.json)


if __name__ == "__main__":
    main()
