"""lab.run(strategy, data) -> result. A pure function.

No I/O, no clock, no global state: the same strategy over the same universe
returns the same bytes, and the random draws are seeded from the strategy's
own canonical hash. That is what lets the research scripts, the tests and
whatever serves the Lab later all run THE SAME CODE a user runs (AUDIT 6.8) -
and it is why this module never opens the store. `lab.universe` builds the
table; this reads it.

WHAT IT DOES, in order (AUDIT 6.5):

  universe   rows for the bet type, markets, seasons, weeks, with a price at the
             chosen timing and a settled outcome
  price      the chosen books' close, aggregated per claim and side. With
             use_posted_juice the BOOK'S price is paid; the de-vigged
             probability is used only for conditions and for break-even display
  side, line the side, then one line per claim-group (main / all rungs /
             nearest_to)
  conditions every one a stored as-of column (section 6.4); a null fails
  limits     one line per player per week by default - rungs on one game settle
             off one stat line, so they are one event, not several
  settle     cleared / missed / push (refund) / void (no snap)
  report     counts, Wilson hit rate, break-even, ROI and units with a
             WEEK-block bootstrap, drawdown, losing run, CLV, by season,
             segments, the luck check, the variants arithmetic, the holdout
             lock, and the verdict chip by fixed rule

THE HOLDOUT LOCK. Rows of the holdout season are removed before anything is
computed unless `holdout.revealed` is true, so no number in an unrevealed
result - not a count, not an interval, not the luck draw - can carry them.
"""
import datetime as dt
import math
import statistics
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from core.stats import wilson
from lab import strategy as S

ET = ZoneInfo("America/New_York")

# Outcome codes on the universe, relative to the row's own side.
CLEARED, MISSED, PUSH, VOID = "cleared", "missed", "push", "void"

# Verdict chips. The first four are AUDIT 6.5 verbatim. The last three name
# cases 6.5 leaves unassigned, so that no input falls through to a chip the
# rule did not earn (decision taken in a-27; see the report):
LOSES = "LOSES"
NO_EVIDENCE = "NO EVIDENCE OF EDGE"
FORWARD_TEST = "WORTH A FORWARD TEST"
SURVIVED = "SURVIVED HOLDOUT"
FAILED_HOLDOUT = "FAILED HOLDOUT"      # above zero in-sample, holdout ROI <= 0
UNCLEAR = "UNCLEAR"                    # interval spans zero, luck outside 5-95
NO_BETS = "NO BETS"                    # nothing graded, or nothing staked
VERDICTS = (LOSES, NO_EVIDENCE, FORWARD_TEST, SURVIVED, FAILED_HOLDOUT,
            UNCLEAR, NO_BETS)

# E[max of k standard normals], AUDIT 6.5, then Blom's approximation.
E_MAX = {1: 0.0, 2: 0.56, 3: 0.85, 4: 1.03, 5: 1.16, 6: 1.27, 7: 1.35}

SEGMENT_MIN = 100
CHART_POINTS = 300
PRICE_BUCKETS = ((0.0, 0.40, "<0.40"), (0.40, 0.50, "0.40-0.50"),
                 (0.50, 0.60, "0.50-0.60"), (0.60, 1.01, ">=0.60"))


# =============================================================================
# small pure pieces
# =============================================================================

def american_to_decimal(a):
    a = float(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / -a)


def decimal_to_american(d):
    if d is None or d <= 1.0:
        return None
    return round((d - 1.0) * 100.0) if d >= 2.0 else round(-100.0 / (d - 1.0))


def expected_max(k):
    k = int(k)
    if k <= 1:
        return 0.0
    if k in E_MAX:
        return E_MAX[k]
    return statistics.NormalDist().inv_cdf((k - 0.375) / (k + 0.25))


def noise_best_roi(k, mu0, sigma):
    """The best ROI noise alone would hand you after trying k rules."""
    if mu0 is None or sigma is None:
        return None
    return mu0 + sigma * expected_max(k)


def verdict(roi_lo, roi_hi, luck_pct, revealed, holdout_roi, graded):
    """The chip. Fixed rules, no judgement (AUDIT 6.5)."""
    if not graded or roi_lo is None or roi_hi is None:
        return NO_BETS
    if roi_hi < 0:
        return LOSES
    if roi_lo > 0:
        if not revealed:
            return FORWARD_TEST
        return SURVIVED if (holdout_roi is not None and holdout_roi > 0) else FAILED_HOLDOUT
    if luck_pct is not None and 5 <= luck_pct <= 95:
        return NO_EVIDENCE
    return UNCLEAR


def _f(x, nd=6):
    """JSON-safe float: NaN and inf are not representable, so they are None."""
    if x is None:
        return None
    x = float(x)
    return None if (math.isnan(x) or math.isinf(x)) else round(x, nd)


def _date(ts):
    return (dt.datetime.fromtimestamp(ts, ET).date().isoformat()
            if ts is not None else None)


def week_blocks(season, week):
    """Block id per bet: one per (season, week). Bets in one week share
    game-level shocks, so the week - not the bet - is the resampling unit."""
    keys = np.asarray(season, dtype=np.int64) * 100 + np.asarray(week, dtype=np.int64)
    _u, inv = np.unique(keys, return_inverse=True)
    return inv


def block_bootstrap_roi(profit, staked, blocks, draws, seed):
    """Percentile interval for sum(profit)/sum(staked), resampling BLOCKS.
    -> (lo, hi, se, samples). None throughout when nothing was staked."""
    profit = np.asarray(profit, dtype=float)
    staked = np.asarray(staked, dtype=float)
    if staked.sum() <= 0 or len(profit) == 0:
        return None, None, None, None
    nb = int(blocks.max()) + 1
    bp = np.bincount(blocks, weights=profit, minlength=nb)
    bs = np.bincount(blocks, weights=staked, minlength=nb)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, nb, size=(draws, nb))
    num = bp[idx].sum(axis=1)
    den = bs[idx].sum(axis=1)
    ok = den > 0
    samples = num[ok] / den[ok]
    if not len(samples):
        return None, None, None, None
    lo, hi = np.percentile(samples, [2.5, 97.5])
    return float(lo), float(hi), float(samples.std()), samples


def block_bootstrap_sum(profit, blocks, draws, seed):
    """Percentile interval for sum(profit), resampling the same week BLOCKS
    with the same seed as `block_bootstrap_roi` - the units interval that sits
    beside the ROI interval (a-32). -> (lo, hi), None throughout when empty."""
    profit = np.asarray(profit, dtype=float)
    if len(profit) == 0:
        return None, None
    nb = int(blocks.max()) + 1
    bp = np.bincount(blocks, weights=profit, minlength=nb)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, nb, size=(draws, nb))
    lo, hi = np.percentile(bp[idx].sum(axis=1), [2.5, 97.5])
    return float(lo), float(hi)


# =============================================================================
# the pipeline
# =============================================================================

def _unpack(data):
    if isinstance(data, dict):
        return data["rows"], data.get("meta", {})
    return data.rows, data.meta


def _universe_slice(rows, s):
    se = s["seasons"]
    w = se["weeks"]
    q = rows.filter(
        (pl.col("bet_type") == s["bet_type"])
        & pl.col("market").is_in(s["markets"])
        & pl.col("season").is_between(se["from"], se["to"])
        & (pl.col("season_type") == se["season_type"])
        & pl.col("week").is_between(w["from"], w["to"])
        & pl.col("outcome").is_in([CLEARED, MISSED, PUSH, VOID]))
    return q


def _price(rows, s):
    """One row per (claim, side): the chosen books' close aggregated, plus
    provenance per season. Game bet types fall back to the nflverse line for
    seasons the chosen books do not cover - labelled, never called a close."""
    p = s["price"]
    books = p["books"]
    juice = S_decimal(p["assume_juice_if_missing"])
    odds = rows.filter((pl.col("source") == "oddsapi_close") & pl.col("book").is_in(books))
    covered = set(odds.get_column("season").unique().to_list())
    fallback = rows.filter((pl.col("source") == "nflverse_line")
                           & ~pl.col("season").is_in(sorted(covered)))
    chosen = pl.concat([odds, fallback], how="vertical_relaxed")
    provenance = {}
    for season in sorted(set(chosen.get_column("season").unique().to_list())):
        if season in covered:
            provenance[str(season)] = {
                "source": "The Odds API historical close, %s" % ", ".join(books),
                "is_close": True,
                "note": "last snapshot at or before kickoff"}
        else:
            provenance[str(season)] = {
                "source": "nflverse games line", "is_close": False,
                "note": "one line per game, provider not verified per season; "
                        "not labelled a close. Juice from nflverse odds where "
                        "present, else assumed %s" % p["assume_juice_if_missing"]}
    # An empty slice still goes through the aggregation below, so it comes out
    # with the price columns every later step reads: returning the bare slice
    # made a rule with no rows crash (ColumnNotFoundError "decimal", a-32)
    # instead of reporting NO BETS.
    chosen = chosen.with_columns(
        pl.when(pl.col("american").is_not_null())
        .then(pl.col("american").map_elements(american_to_decimal, return_dtype=pl.Float64))
        .otherwise(pl.lit(juice)).alias("_dec"))
    feature_cols = [c for c in rows.columns if "." in c]
    first_cols = ["bet_type", "market", "season", "week", "season_type", "game_id",
                  "kickoff_ts", "subject", "event_subject", "line", "team", "opp",
                  "outcome", "actual", "source"] + feature_cols
    agg_dec = {"consensus_close": pl.col("_dec").median(),
               "best_close": pl.col("_dec").max(),
               "book": pl.col("_dec").median()}[p["source"]]
    out = chosen.group_by(["claim", "side"]).agg(
        [pl.col(c).first() for c in first_cols]
        + [agg_dec.alias("decimal"),
           pl.col("p_devig").median().alias("p_devig"),
           pl.col("book_hold").median().alias("price.hold"),
           pl.col("book").n_unique().cast(pl.Int64).alias("price.books_quoting")])
    if not p["use_posted_juice"]:
        out = out.with_columns((1.0 / pl.col("p_devig")).alias("decimal"))
    out = out.filter(pl.col("decimal").is_not_null() & (pl.col("decimal") > 1.0))
    out = out.with_columns(
        pl.col("line").alias("price.line"),
        pl.col("p_devig").alias("price.p_devig"),
        (1.0 / pl.col("decimal")).alias("price.implied"))
    return out, provenance


def S_decimal(american):
    return american_to_decimal(american)


def _side(rows, s):
    side, bt = s["side"], s["bet_type"]
    if bt in ("prop", "total") or side in ("home", "away"):
        return rows.filter(pl.col("side") == side)
    if bt == "spread":
        fav = pl.col("line") < 0
        dog = pl.col("line") > 0
    else:
        fav = pl.col("p_devig") > 0.5
        dog = pl.col("p_devig") < 0.5
    return rows.filter(fav if side == "favourite" else dog)


def _line_choice(rows, s):
    lc = s["line_choice"]
    if lc == "all_rungs" or rows.is_empty():
        return rows
    target = S.nearest_target(lc)
    group = ["season", "week", "game_id", "subject", "market", "side"]
    if target is None:
        rows = rows.with_columns(
            (pl.col("p_devig") - 0.5).abs().alias("_k1"),
            (-pl.col("price.books_quoting")).alias("_k2"))
    else:
        rows = rows.with_columns(
            (pl.col("line") - target).abs().alias("_k1"),
            (pl.col("p_devig") - 0.5).abs().alias("_k2"))
    return (rows.sort(["_k1", "_k2", "line"])
            .group_by(group, maintain_order=True).first()
            .drop(["_k1", "_k2"]))


_OPS = {
    "==": lambda c, v: c == v, "!=": lambda c, v: c != v,
    "<": lambda c, v: c < v, "<=": lambda c, v: c <= v,
    ">": lambda c, v: c > v, ">=": lambda c, v: c >= v,
    "in": lambda c, v: c.is_in(v), "not_in": lambda c, v: ~c.is_in(v),
    "between": lambda c, v: c.is_between(v[0], v[1]),
}


def _conditions(rows, s):
    for cond in s["conditions"]:
        col = pl.col(cond["feature"])
        if cond["feature"] not in rows.columns:
            # A catalogued feature with no column is a universe/catalogue
            # disagreement - raising beats a rule that silently selects nothing.
            raise S.Unsupported(["feature %r is catalogued but the universe "
                                 "carries no column for it" % cond["feature"]])
        rows = rows.filter(col.is_not_null() & _OPS[cond["op"]](col, cond["value"]))
    return rows


def _order(rows):
    return rows.with_columns((pl.col("p_devig") - 0.5).abs().alias("_c")).sort(
        ["kickoff_ts", "_c", "claim", "side"]).drop("_c")


def _limits(rows, lim):
    rows = _order(rows)
    for key, cols in (("per_player_week", ["season", "week", "event_subject"]),
                      ("per_game", ["game_id"]),
                      ("per_week", ["season", "week"])):
        k = lim.get(key)
        if k:
            rows = (rows.with_columns(pl.int_range(pl.len()).over(cols).alias("_r"))
                    .filter(pl.col("_r") < k).drop("_r"))
    return rows


def _stakes(bets, s):
    """(stake, profit) arrays in kickoff order. Voids stake nothing."""
    st = s["staking"]
    dec = bets.get_column("decimal").to_numpy()
    out = bets.get_column("outcome").to_numpy()
    n = len(dec)
    stake = np.zeros(n)
    profit = np.zeros(n)
    win = out == CLEARED
    loss = out == MISSED
    live = out != VOID
    if st["method"] == "flat":
        stake[live] = st["unit"]
        unit = st["unit"]
    elif st["method"] == "kelly_fraction":
        unit = st.get("unit", 1)          # stakes stay zero: see strategy.check
    else:
        bank = float(st["bankroll"])
        unit = st["pct"] * bank
        for i in range(n):
            if not live[i]:
                continue
            stake[i] = st["pct"] * bank
            if win[i]:
                bank += stake[i] * (dec[i] - 1.0)
            elif loss[i]:
                bank -= stake[i]
    profit[win] = stake[win] * (dec[win] - 1.0)
    profit[loss] = -stake[loss]
    return stake, profit, unit


def _flat(bets):
    dec = bets.get_column("decimal").to_numpy()
    out = bets.get_column("outcome").to_numpy()
    pr = np.where(out == CLEARED, dec - 1.0, np.where(out == MISSED, -1.0, 0.0))
    st = (out != VOID).astype(float)
    return pr, st


# =============================================================================
# evaluation
# =============================================================================

def _summary(bets, stake, profit, unit, draws, seed):
    out = bets.get_column("outcome").to_numpy()
    n_c, n_m = int((out == CLEARED).sum()), int((out == MISSED).sum())
    n_p, n_v = int((out == PUSH).sum()), int((out == VOID).sum())
    graded = n_c + n_m
    lo, hi = wilson(n_c, graded) if graded else (None, None)
    dec = bets.get_column("decimal").to_numpy()
    live = out != VOID
    staked = stake.sum()
    roi = profit.sum() / staked if staked > 0 else None
    if len(bets):
        blocks = week_blocks(bets.get_column("season").to_numpy(),
                             bets.get_column("week").to_numpy())
        r_lo, r_hi, se, _samp = block_bootstrap_roi(profit, stake, blocks, draws, seed)
        u_lo, u_hi = block_bootstrap_sum(profit, blocks, draws, seed)
    else:
        r_lo = r_hi = se = u_lo = u_hi = None
    p = bets.get_column("p_devig").to_numpy()
    gmask = (out == CLEARED) | (out == MISSED)
    mean_dec = dec[live].mean() if live.any() else None
    return {
        "bets": int(len(bets)), "cleared": n_c, "missed": n_m, "push": n_p,
        "void": n_v, "weeks": int(len(np.unique(week_blocks(
            bets.get_column("season").to_numpy(),
            bets.get_column("week").to_numpy())))) if len(bets) else 0,
        "hit_rate": _f(n_c / graded) if graded else None,
        "hit_rate_lo": _f(lo), "hit_rate_hi": _f(hi),
        "break_even": _f(1.0 / mean_dec) if mean_dec else None,
        "mean_price_decimal": _f(mean_dec),
        "mean_devig_prob": _f(p[gmask].mean()) if gmask.any() else None,
        # realized minus priced on graded bets: the calibration register's
        # quantity, so a preset can be checked against it directly.
        "pricing_gap_pp": _f(100 * (n_c / graded - p[gmask].mean())) if graded else None,
        "staked": _f(staked), "profit": _f(profit.sum()),
        "units": _f(profit.sum() / unit) if unit else None,
        "units_lo": _f(u_lo / unit) if (unit and u_lo is not None) else None,
        "units_hi": _f(u_hi / unit) if (unit and u_hi is not None) else None,
        "roi": _f(roi), "roi_lo": _f(r_lo), "roi_hi": _f(r_hi), "roi_se": _f(se),
    }


def _drawdown(bets, profit, unit):
    if not len(bets):
        return {"max_units": None, "peak_date": None, "trough_date": None,
                "longest_losing_run": 0}
    cum = np.cumsum(profit) / (unit or 1)
    ts = bets.get_column("kickoff_ts").to_numpy()
    peak, peak_i, worst, w_peak, w_trough = 0.0, None, 0.0, None, None
    for i, v in enumerate(cum):
        if v > peak:
            peak, peak_i = v, i
        if peak - v > worst:
            worst, w_peak, w_trough = peak - v, peak_i, i
    out = bets.get_column("outcome").to_numpy()
    run = best = 0
    for o in out:
        if o == MISSED:
            run += 1
            best = max(best, run)
        elif o == CLEARED:
            run = 0
    return {"max_units": _f(worst),
            "peak_date": _date(ts[w_peak]) if w_peak is not None else
            (_date(ts[0]) if w_trough is not None else None),
            "trough_date": _date(ts[w_trough]) if w_trough is not None else None,
            "longest_losing_run": int(best),
            "note": "pushes and voids neither extend nor break a losing run"}


def _by(bets, stake, profit, col, draws, seed, label=None):
    if col not in bets.columns or not len(bets):
        return []
    vals = bets.get_column(col).to_list()
    out = []
    keys = sorted({v for v in vals if v is not None}, key=lambda x: str(x))
    arr = np.asarray(vals, dtype=object)
    for k in keys:
        m = arr == k
        sub = bets.filter(pl.Series(m))
        s_ = _summary(sub, stake[m], profit[m], 1.0, draws, seed)
        out.append({"key": label(k) if label else k, "bets": s_["bets"],
                    "weeks": s_["weeks"],
                    "cleared": s_["cleared"], "missed": s_["missed"],
                    "push": s_["push"], "void": s_["void"],
                    "hit_rate": s_["hit_rate"], "hit_rate_lo": s_["hit_rate_lo"],
                    "hit_rate_hi": s_["hit_rate_hi"], "roi": s_["roi"],
                    "roi_lo": s_["roi_lo"], "roi_hi": s_["roi_hi"],
                    "thin": s_["bets"] < SEGMENT_MIN})
    return out


def _segments(bets, stake, profit, draws, seed):
    b = bets.with_columns(pl.col("p_devig").map_elements(
        lambda p: next((lab for lo, hi, lab in PRICE_BUCKETS if lo <= p < hi), None),
        return_dtype=pl.Utf8).alias("_bucket"))
    seg = {"market": _by(b, stake, profit, "market", draws, seed),
           "price_bucket": _by(b, stake, profit, "_bucket", draws, seed),
           "home_away": _by(b, stake, profit, "game.home", draws, seed,
                            lambda k: "home" if k else "away"),
           "dome": _by(b, stake, profit, "game.dome", draws, seed,
                       lambda k: "dome" if k else "open air")}
    if "player.position" in b.columns:
        seg["position"] = _by(b, stake, profit, "player.position", draws, seed)
    return seg


def _luck(pool, rule_bets, draws, seed):
    """Same number of bets, drawn at random from the same universe and side,
    `draws` times. Flat stakes on both sides of the comparison."""
    n = int((rule_bets.get_column("outcome") != VOID).sum()) if len(rule_bets) else 0
    pool = pool.filter(pl.col("outcome") != VOID)
    m = len(pool)
    if n == 0:
        return {"percentile": None, "draws": 0, "note": "no bets to compare"}
    r_pr, r_st = _flat(rule_bets.filter(pl.col("outcome") != VOID))
    rule_roi = r_pr.sum() / r_st.sum()
    if m <= n:
        return {"percentile": None, "draws": 0, "pool": m, "n": n,
                "note": "the rule takes every bet the universe offers at this "
                        "side, so a random draw of the same size IS the rule"}
    pr, _st = _flat(pool)
    rng = np.random.default_rng(seed + 1)
    checkpoints = np.unique(np.linspace(0, n - 1, min(CHART_POINTS, n)).astype(int))
    rois = np.empty(draws)
    paths = np.empty((draws, len(checkpoints)))
    for d in range(draws):
        idx = np.sort(rng.choice(m, size=n, replace=False))
        x = pr[idx]
        rois[d] = x.sum() / n
        paths[d] = np.cumsum(x)[checkpoints]
    pct = 100.0 * ((rois < rule_roi).sum() + 0.5 * (rois == rule_roi).sum()) / draws
    lo5, hi95 = np.percentile(rois, [5, 95])
    return {"percentile": _f(pct, 2), "draws": int(draws), "pool": m, "n": n,
            "rule_roi_flat": _f(rule_roi), "null_mean_roi": _f(rois.mean()),
            "null_roi_p5": _f(lo5), "null_roi_p95": _f(hi95),
            "band": {"index": checkpoints.tolist(),
                     "p5": [_f(v, 3) for v in np.percentile(paths, 5, axis=0)],
                     "p95": [_f(v, 3) for v in np.percentile(paths, 95, axis=0)]}}


def _chart(bets, profit, unit):
    if not len(bets):
        return {"index": [], "units": [], "season_starts": []}
    cum = np.cumsum(profit) / (unit or 1)
    n = len(cum)
    idx = np.unique(np.linspace(0, n - 1, min(CHART_POINTS, n)).astype(int))
    seasons = bets.get_column("season").to_list()
    starts = [i for i in range(n) if i == 0 or seasons[i] != seasons[i - 1]]
    return {"index": idx.tolist(), "units": [_f(cum[i], 3) for i in idx],
            "season_starts": [{"index": i, "season": seasons[i]} for i in starts]}


def _bet_list(bets, stake, profit):
    cols = ["season", "week", "kickoff_ts", "game_id", "subject", "team", "opp",
            "market", "line", "side", "decimal", "p_devig", "price.books_quoting",
            "outcome", "actual"]
    rows = bets.select([c for c in cols if c in bets.columns]).to_dicts()
    out = []
    for r, st, pr in zip(rows, stake, profit):
        out.append({"season": r["season"], "week": r["week"],
                    "date": _date(r["kickoff_ts"]), "game_id": r["game_id"],
                    "subject": r["subject"], "team": r.get("team"),
                    "opp": r.get("opp"), "market": r["market"],
                    "line": r["line"], "side": r["side"],
                    "price_american": decimal_to_american(r["decimal"]),
                    "price_decimal": _f(r["decimal"], 4),
                    "p_devig": _f(r["p_devig"], 4),
                    "books_quoting": r.get("price.books_quoting"),
                    "stake": _f(st, 4), "outcome": r["outcome"],
                    "profit": _f(pr, 4), "actual": r["actual"]})
    return out


def _evaluate(bets, s, seed, draws):
    stake, profit, unit = _stakes(bets, s)
    summ = _summary(bets, stake, profit, unit, draws, seed)
    return summ, stake, profit, unit


# =============================================================================
# the entry point
# =============================================================================

def run(strategy, data, variants_tried=1):
    """-> a `lab.result/1` dict. Raises strategy.Invalid / Unsupported."""
    rows, meta = _unpack(data)
    approved = S.check(strategy, meta.get("features", {}),
                       meta.get("price_coverage"),
                       meta.get("latest_complete_season"))
    s = approved.strategy
    seed = int(S.strategy_hash(s)[:12], 16)
    draws = s["evaluation"]["resamples"]
    null_draws = s["evaluation"]["null_draws"]
    hold = s["holdout"]
    revealed = bool(hold["revealed"])

    sliced = _universe_slice(rows, s)
    priced, provenance = _price(sliced, s)
    sided = _side(priced, s) if len(priced) else priced
    lined = _line_choice(sided, s)

    # THE LOCK. Split before conditions, limits, the luck pool - everything.
    in_rows = lined.filter(pl.col("season") != hold["season"])
    ho_rows = lined.filter(pl.col("season") == hold["season"]) if revealed else lined.clear()

    bets = _limits(_conditions(in_rows, s), s["limits"])
    summ, stake, profit, unit = _evaluate(bets, s, seed, draws)
    pool = _limits(in_rows, {"per_player_week": s["limits"].get("per_player_week")})
    luck = _luck(_order(pool), bets, null_draws, seed)

    by_season = _by(bets, stake, profit, "season", draws, seed)
    ho = None
    all_seasons = None
    if revealed:
        hb = _limits(_conditions(ho_rows, s), s["limits"])
        hs, hst, hpr, hunit = _evaluate(hb, s, seed + 7, draws)
        ho = dict(hs, season=hold["season"])
        both = _limits(_conditions(lined, s), s["limits"])
        all_seasons, _a, _b, _c = _evaluate(both, s, seed + 11, draws)
        by_season = by_season + _by(hb, hst, hpr, "season", draws, seed)

    k = max(int(variants_tried or 1), 1)
    best_noise = noise_best_roi(k, luck.get("null_mean_roi"), summ["roi_se"])
    variants = {
        "k": k, "expected_max_z": _f(expected_max(k), 4),
        "null_mean_roi": luck.get("null_mean_roi"), "roi_se": summ["roi_se"],
        "best_roi_from_noise": _f(best_noise),
        "sentence": (None if best_noise is None else
                     "After %d rule%s, the best ROI noise alone would be expected "
                     "to produce is %+.1f%%." % (k, "" if k == 1 else "s",
                                                 100 * best_noise)),
    }
    chip = verdict(summ["roi_lo"], summ["roi_hi"], luck.get("percentile"),
                   revealed, ho["roi"] if ho else None,
                   summ["cleared"] + summ["missed"])

    return {
        "schema": S.RESULT_ID,
        "strategy": s,
        "strategy_hash": S.strategy_hash(s),
        "statement": approved.statement,
        "notes": approved.notes + ([] if meta.get("settlement_fixed", True) else [
            "UNFIXED SETTLEMENT: this universe predates the 2026-09-17 fix and "
            "is for reproducing a pre-fix figure only"]),
        "publishable": bool(meta.get("settlement_fixed", False)),
        "verdict": chip,
        "summary": summ,
        "clv": {"value": None, "note": "n/a: bets at the close have none"},
        "drawdown": _drawdown(bets, profit, unit),
        "by_season": by_season,
        "segments": _segments(bets, stake, profit, draws, seed) if len(bets) else {},
        "luck": luck,
        "variants": variants,
        "holdout": {"season": hold["season"], "revealed": revealed,
                    "revealed_at": hold.get("revealed_at"),
                    "result": ho,
                    "note": (None if revealed else
                             "held out: no figure in this result includes the "
                             "%d season" % hold["season"])},
        "all_seasons": all_seasons,
        "chart": _chart(bets, profit, unit),
        "provenance": provenance,
        "universe": {"claims_priced": int(len(priced)),
                     "after_side_and_line": int(len(lined)),
                     "in_sample_candidates": int(len(in_rows)),
                     "built": meta.get("built"), "source_note": meta.get("note")},
        "bet_list": _bet_list(bets, stake, profit),
    }
