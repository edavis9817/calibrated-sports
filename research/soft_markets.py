"""c-33 - where is the book's price weakest, measured by the book's own behaviour.

    LOGGER_DB=<market_log.db> python -m research.soft_markets --cache D:/temp/c33 --json-out D:/temp/c33/result.json

PRE-REGISTRATION: docs/C33-soft-markets-preregistration.md, committed and pushed
at a112411 BEFORE this script existed, and addendum 1 at 76af522 after one smoke
run and before the real run. This file implements both; it does not extend them. Comments say only where the code carries a rule out.

No forecast and no model. Three proxies per Odds API market key - hold,
disagreement between books, line movement - and, separately, how forecastable
the settling stat is. Spends no credits and makes no HTTP request: it imports
no client. market_log.db is opened mode=ro; nothing is written except
--json-out and the extract cache under --cache. Printed output is aggregates.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time

import numpy as np
import polars as pl
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                            # noqa: E402

SEED = 33
DRAWS = 2000
FC_DRAWS = 1000
BENCH = tuple(config.BOARD_BENCH_BOOKS)
MAX_HOLD = 0.25
CLOSE_MAX_LEAD = 120 * 60
FRESH_MAX_AGE = 900
OPEN_MIN_LEAD, OPEN_MAX_LEAD = 24 * 3600, 72 * 3600
BAND = (0.40, 0.60)
KAPPA_MIN_PAIRS = 30
MIN_GAMES_RANKED = 10
MIN_GAMES_READ = 5
LIVE_FLOOR_TS = 1780000000.0          # 2026-05: every live Odds API row is after it

RANKED_L = ("player_receptions", "player_reception_yds", "player_rush_attempts",
            "player_rush_yds", "player_pass_yds", "player_pass_attempts",
            "player_pass_tds", "player_tackles_assists")
RANKED_H = ("player_receptions", "player_reception_yds", "player_rush_attempts",
            "player_tackles_assists", "player_sacks")
REFERENCE = ("spreads_h1", "totals_h1", "team_totals_h1", "h2h", "spreads", "totals")
ONE_SIDED = ("player_anytime_td",)
NO_KAPPA = ("h2h", "spreads", "spreads_h1")

# key -> (nfl_player_week expression name, QB only)
STAT = {"player_receptions": ("receptions", False),
        "player_reception_yds": ("receiving_yards", False),
        "player_rush_attempts": ("carries", False),
        "player_rush_yds": ("rushing_yards", False),
        "player_pass_attempts": ("attempts", True),
        "player_pass_yds": ("passing_yards", True),
        "player_pass_tds": ("passing_tds", True),
        "player_tackles_assists": ("tackles_assists", False),
        "player_sacks": ("def_sacks", False),
        "player_anytime_td": ("anytime_td", False)}


# =============================================================================
# pure pieces (tested in tests/test_soft_markets.py)
# =============================================================================

def hold_and_p(o, u):
    """(hold, de-vigged P(over)) of a two-sided raw pair, or None when not valid."""
    if o is None or u is None:
        return None
    hold = o + u - 1.0
    if not (0.0 < hold <= MAX_HOLD):
        return None
    return hold, o / (o + u)


def belief(p, line, kappa):
    """b = p + kappa * ln(line): a price and a line on one scale."""
    return p + kappa * math.log(line)


def kappa_from_pairs(dp, dlnl):
    """-median((p_i - p_j) / (ln L_i - ln L_j)) over different-line pairs; None if unusable."""
    dp, dlnl = np.asarray(dp, float), np.asarray(dlnl, float)
    ok = np.abs(dlnl) > 1e-12
    if ok.sum() < KAPPA_MIN_PAIRS:
        return None
    k = -float(np.median(dp[ok] / dlnl[ok]))
    return k if k > 0 else None


def boot_means(components, n_games, draws=None, seed=None):
    """Game-block bootstrap of a key statistic.

    `components` is a list of length-n_games arrays (NaN = the key has no value
    in that game). The statistic is the mean over components of the nan-mean
    over games - one component for most proxies, one per bench book for hold.
    Returns (estimate, array of draw values). Every key passed the same seed
    and n_games sees the SAME resampled games, so ranks share draws.
    """
    draws = DRAWS if draws is None else draws
    seed = SEED if seed is None else seed
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_games, size=(draws, n_games))
    est, out = [], []
    for v in components:
        v = np.asarray(v, float)
        est.append(np.nanmean(v) if np.isfinite(v).any() else np.nan)
        s = v[idx]
        cnt = np.isfinite(s).sum(axis=1)
        tot = np.nansum(s, axis=1)
        out.append(np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan))
    return float(np.mean(est)), np.mean(np.vstack(out), axis=0)


def ranks_desc(values):
    """Rank 1 = largest. NaN keeps NaN. Ties share the average rank."""
    v = np.asarray(values, float)
    out = np.full(v.shape, np.nan)
    ok = np.isfinite(v)
    if ok.any():
        from scipy.stats import rankdata
        out[ok] = rankdata(-v[ok], method="average")
    return out


def pct(a, q):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(np.percentile(a, q)) if a.size else float("nan")


# =============================================================================
# extract (mode=ro, cached)
# =============================================================================

def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)


def extract(db, cache, refresh=False):
    """Pull the Odds API rows once and close the connection (short ro session)."""
    fl, fh, fk = (os.path.join(cache, n) for n in
                  ("c33_live.parquet", "c33_hist.parquet", "c33_kick.parquet"))
    if not refresh and all(os.path.exists(f) for f in (fl, fh, fk)):
        return pl.read_parquet(fl), pl.read_parquet(fh), pl.read_parquet(fk)
    con = _ro(db)
    try:
        books, s = [], "oddsapi:"
        while True:
            r = con.execute("select min(venue) from quotes where venue>? and venue<'oddsapi;'",
                            (s,)).fetchone()[0]
            if r is None:
                break
            books.append(r)
            s = r
        cols = ["ts", "venue", "event_id", "market_id", "line", "raw", "source_ts"]
        live = []
        for b in books:
            # unary + keeps the planner on (venue, market_id, ts): with a bare ts or source
            # predicate it walks every venue's rows and holds a read transaction for minutes
            live += con.execute(
                "select ts, venue, event_id, market_id, line, mid, source_ts from quotes "
                "where venue=? and +ts>? and +source='live'", (b, LIVE_FLOOR_TS)).fetchall()
        hist = con.execute(
            "select ts, venue, event_id, market_id, line, mid, source_ts from quotes "
            "where source='oddsapi_historical'").fetchall()
        kl = con.execute("select event_id, close_ts from markets where venue='oddsapi' "
                         "and market_id like 'game:%'").fetchall()
        kh = con.execute("select game_id, max(kickoff_ts) from nfl_games "
                         "where season between 2023 and 2025 group by 1").fetchall()
    finally:
        con.close()
    schema = {"ts": pl.Float64, "venue": pl.Utf8, "event_id": pl.Utf8, "market_id": pl.Utf8,
              "line": pl.Float64, "raw": pl.Float64, "source_ts": pl.Float64}
    L = pl.DataFrame(live, schema=schema, orient="row")
    H = pl.DataFrame(hist, schema=schema, orient="row")
    K = pl.DataFrame(kl + kh, schema={"event_id": pl.Utf8, "kick": pl.Float64}, orient="row")
    assert L.height > 0 and H.height > 0 and K.height > 0, "empty extract"
    os.makedirs(cache, exist_ok=True)
    L.write_parquet(fl), H.write_parquet(fh), K.write_parquet(fk)
    assert cols == L.columns
    return L, H, K


def normalise(df, K, panel):
    """Rows -> (book, event, key, subj, pline, side A|B, raw, ts, source_ts), pre-kickoff."""
    parts = pl.col("market_id").str.split("|")
    df = df.with_columns(book=pl.col("venue").str.replace("oddsapi:", ""),
                         key=parts.list.get(1, null_on_oob=True),
                         f2=parts.list.get(2, null_on_oob=True),
                         f3=parts.list.get(3, null_on_oob=True)).filter(pl.col("key").is_not_null())
    if panel == "L":                      # event|key|player|side ; game rows event|key||team
        df = df.with_columns(name=pl.col("f3"), subj0=pl.col("f2"))
    else:                                 # props event|key|player|side|line ; spreads/h2h event|key|team|x ; totals event|totals|side|line
        game = pl.col("key").is_in(["h2h", "spreads", "totals"])
        df = df.with_columns(
            name=pl.when(game).then(pl.col("f2")).otherwise(pl.col("f3")),
            subj0=pl.when(game).then(pl.lit("")).otherwise(pl.col("f2")))
    df = df.join(K, on="event_id", how="inner").filter(
        (pl.col("ts") < pl.col("kick")) & pl.col("raw").is_not_null())
    team_side = pl.col("key").is_in(["h2h", "spreads", "spreads_h1"])
    grp = ["book", "event_id", "key", "ts"]
    df = df.with_columns(
        subj=pl.when(team_side).then(pl.lit("")).otherwise(pl.col("subj0")),
        pline=pl.when(pl.col("key") == "h2h").then(pl.lit(-1.0))
        .when(team_side).then(pl.col("line").abs()).otherwise(pl.col("line")),
        side=pl.when(pl.col("key") == "h2h")
        .then(pl.when(pl.col("name") == pl.col("name").min().over(grp)).then(pl.lit("A")).otherwise(pl.lit("B")))
        .when(team_side)
        .then(pl.when(pl.col("line") < 0).then(pl.lit("A")).when(pl.col("line") > 0).then(pl.lit("B")))
        .when(pl.col("name").is_in(["Over", "Yes"])).then(pl.lit("A"))
        .when(pl.col("name") == "Under").then(pl.lit("B")))
    return df.select("book", "event_id", "key", "subj", "pline", "side", "raw", "ts",
                     "source_ts", "kick")


def pairs(n):
    """Two-sided pairs per (book, event, key, subj, pline, ts), with hold and p."""
    g = ["book", "event_id", "key", "subj", "pline", "ts"]
    two = n.filter(~pl.col("key").str.ends_with("_alternate") & ~pl.col("key").is_in(ONE_SIDED)
                   & pl.col("pline").is_not_null() & pl.col("side").is_not_null())
    p = two.group_by(g).agg(
        nA=(pl.col("side") == "A").sum(), nB=(pl.col("side") == "B").sum(),
        o=pl.col("raw").filter(pl.col("side") == "A").first(),
        u=pl.col("raw").filter(pl.col("side") == "B").first(),
        source_ts=pl.col("source_ts").max(), kick=pl.col("kick").first())
    raw_groups = p.height
    p = p.filter((pl.col("nA") == 1) & (pl.col("nB") == 1)).with_columns(
        hold=pl.col("o") + pl.col("u") - 1.0, p=pl.col("o") / (pl.col("o") + pl.col("u")))
    both = p.height
    p = p.filter((pl.col("hold") > 0) & (pl.col("hold") <= MAX_HOLD))
    return p, {"groups": raw_groups, "two_sided": both, "valid": p.height}


def main_at(p, when):
    """Per (book, event, key, subj): the main-line pair at the read chosen by `when`.

    `when` is a polars expression over (ts, kick) that is True for eligible
    reads; the latest eligible read is taken, or the earliest for when="first".
    """
    g = ["book", "event_id", "key", "subj"]
    if isinstance(when, str) and when == "first":
        sel = p.filter(pl.col("ts") == pl.col("ts").min().over(g))
    else:
        e = p.filter(when)
        sel = e.filter(pl.col("ts") == pl.col("ts").max().over(g))
    sel = sel.with_columns(d=(pl.col("p") - 0.5).abs())
    return sel.sort(g + ["d", "pline"]).unique(subset=g, keep="first", maintain_order=True)


# =============================================================================
# proxies -> per-game values
# =============================================================================

def game_vec(df, value, games, key, extra=None):
    """Length-len(games) array of the per-game mean of `value` for one key."""
    d = df.filter(pl.col("key") == key)
    if extra is not None:
        d = d.filter(extra)
    d = d.filter(pl.col(value).is_not_null() & pl.col(value).is_finite())
    m = dict(d.group_by("event_id").agg(pl.col(value).mean()).iter_rows())
    return np.array([m.get(g, np.nan) for g in games], float)


def book_pairs(close):
    """Self-join of close main-line rows: one row per (claim, book_i < book_j)."""
    c = close.select("event_id", "key", "subj", "book", "p", "pline")
    j = c.join(c, on=["event_id", "key", "subj"], suffix="_j").filter(pl.col("book") < pl.col("book_j"))
    return j.with_columns(dp=pl.col("p") - pl.col("p_j"),
                          same=(pl.col("pline") - pl.col("pline_j")).abs() < 1e-9)


def kappas(bp, keys):
    out = {}
    for k in keys:
        if k in NO_KAPPA:
            out[k] = None
            continue
        d = bp.filter((pl.col("key") == k) & ~pl.col("same") & (pl.col("pline") > 0) & (pl.col("pline_j") > 0))
        dln = (d["pline"].log() - d["pline_j"].log()).to_numpy()
        out[k] = kappa_from_pairs(d["dp"].to_numpy(), dln)
    return out


def ladder_kappa(n, close):
    """Addendum 1: one book's own slope across its alternate ladder at the close read."""
    alt = n.filter(pl.col("key").str.ends_with("_alternate") & (pl.col("side") == "A") & (pl.col("pline") > 0))
    alt = alt.with_columns(key=pl.col("key").str.replace("_alternate", "")).select(
        "book", "event_id", "key", "subj", "ts", aline="pline", araw="raw")
    g = ["book", "event_id", "key", "subj"]
    j = alt.join(close.select(g + ["ts", "pline", "hold"]), on=g + ["ts"]).with_columns(
        ap=pl.col("araw") / (1.0 + pl.col("hold")))
    lo = j.filter(pl.col("aline") < pl.col("pline")).sort(g + ["aline"]).unique(
        subset=g, keep="last", maintain_order=True)
    hi = j.filter(pl.col("aline") > pl.col("pline")).sort(g + ["aline"]).unique(
        subset=g, keep="first", maintain_order=True)
    b = lo.select(g + ["aline", "ap"]).join(hi.select(g + ["aline", "ap"]), on=g, suffix="_hi").with_columns(
        k=-(pl.col("ap_hi") - pl.col("ap")) / (pl.col("aline_hi").log() - pl.col("aline").log()))
    out = {}
    for (key,), d in b.group_by(["key"]):
        v = d["k"].to_numpy()
        v = v[np.isfinite(v)]
        med = float(np.median(v)) if v.size else float("nan")
        out[key] = {"kappa": med if (v.size >= KAPPA_MIN_PAIRS and med > 0) else None, "claims": int(v.size),
                    "q25": pct(v, 25), "q75": pct(v, 75)}
    return out


def with_kappa(df, kap, a="pline", b="pline_j", dp="dp"):
    """Add |b_i - b_j| (combined) using the key's kappa; null where the key has none."""
    km = pl.DataFrame({"key": list(kap), "kappa": [kap[k] for k in kap]},
                      schema={"key": pl.Utf8, "kappa": pl.Float64})
    df = df.join(km, on="key", how="left")
    lr = pl.when((pl.col(a) > 0) & (pl.col(b) > 0)).then(pl.col(a).log() - pl.col(b).log())
    return df.with_columns(comb=(pl.col(dp) + pl.col("kappa") * lr).abs())


def disagreement(close, kap):
    """Per claim: same-line mean |dp|, line-split share, combined mean |db|, n books."""
    bp = with_kappa(book_pairs(close), kap)
    g = ["event_id", "key", "subj"]
    cl = bp.group_by(g).agg(
        same_line=pl.col("dp").abs().filter(pl.col("same")).mean(),
        split=(~pl.col("same")).mean(),
        combined=pl.col("comb").mean())
    nb = close.group_by(g).agg(n_books=pl.col("book").n_unique())
    return cl.join(nb, on=g), bp


def movement(open_, close, kap):
    g = ["book", "event_id", "key", "subj"]
    j = open_.select(g + ["p", "pline", "ts"]).join(
        close.select(g + ["p", "pline", "ts", "kick"]), on=g, suffix="_c")
    j = j.with_columns(dp=pl.col("p_c") - pl.col("p"),
                       changed=(pl.col("pline_c") - pl.col("pline")).abs() > 1e-9,
                       lead_h=(pl.col("kick") - pl.col("ts")) / 3600.0)
    j = with_kappa(j, kap, a="pline_c", b="pline", dp="dp")
    return j.with_columns(
        move=pl.col("comb"),
        same_move=pl.when(~pl.col("changed")).then(pl.col("dp").abs()),
        chg=pl.col("changed").cast(pl.Float64))


# =============================================================================
# panel driver
# =============================================================================

def summarise(name, comps_by_key, games, direction, ranked):
    """Bootstrap every key on the same draws; return rows and the rank draws."""
    n = len(games)
    est, drw = {}, {}
    for k, comps in comps_by_key.items():
        est[k], drw[k] = boot_means(comps, n)
    rows = {}
    ng = {k: int(max(np.isfinite(np.asarray(c, float)).sum() for c in comps_by_key[k])) for k in est}
    rk = [k for k in ranked if k in est and ng[k] >= MIN_GAMES_RANKED and math.isfinite(est[k])]
    sign = 1.0 if direction == "high" else -1.0
    R = np.vstack([ranks_desc(sign * np.array([drw[k][i] for k in rk])) for i in range(DRAWS)]) if rk else None
    r0 = ranks_desc(sign * np.array([est[k] for k in rk])) if rk else []
    for k in est:
        row = {"est": est[k], "lo": pct(drw[k], 2.5), "hi": pct(drw[k], 97.5), "games": ng[k],
               "read": ng[k] >= MIN_GAMES_READ}
        if k in rk:
            i = rk.index(k)
            row.update(rank=float(r0[i]), rank_lo=pct(R[:, i], 2.5), rank_hi=pct(R[:, i], 97.5))
        rows[k] = row
    return rows, (est, drw)


def run_panel(panel, n, ranked, now, ladder=None, no_dis=()):
    info = {}
    played = n.filter(pl.col("kick") < now)
    p, info["pair_counts"] = pairs(played)
    lead = pl.col("kick") - pl.col("ts")
    if panel == "L":
        close_all = main_at(p, lead <= CLOSE_MAX_LEAD)
        fresh = (pl.col("ts") - pl.col("source_ts")) <= FRESH_MAX_AGE
        close = close_all.filter(fresh)
        info["close_fresh_share"] = close.height / max(close_all.height, 1)
    else:
        # props are one snapshot; for spreads/totals/h2h the close is the last one
        close_all = main_at(p, lead > 0)
        close = close_all
    games = sorted(close["event_id"].unique().to_list())
    keys = [k for k in ranked + REFERENCE if close.filter(pl.col("key") == k).height]
    info["games"] = len(games)
    info["window"] = [float(played["ts"].min()), float(played["ts"].max())]

    # ---- kappa
    bp0 = book_pairs(close)
    kap_x = kappas(bp0, keys)
    if panel == "L":
        ladder = ladder_kappa(played, close)
    ladder = ladder or {}
    has = lambda k: bool((ladder.get(k) or {}).get("kappa")) and k not in NO_KAPPA
    kap = {k: (ladder[k]["kappa"] if has(k) else kap_x[k]) for k in keys}
    info["kappa_cross_book"] = {k: kap_x[k] for k in keys}
    info["kappa_ladder"] = {k: ladder.get(k) for k in keys}
    info["kappa_source"] = {k: ("ladder" if has(k) else ("cross-book" if kap_x[k] else None)) for k in keys}
    info["kappa"] = {k: kap[k] for k in keys}
    info["kappa_pairs"] = {k: bp0.filter((pl.col("key") == k) & ~pl.col("same")).height for k in keys}

    out = {"info": info, "keys": keys}

    # ---- proxy 1: hold
    def hold_comps(src, books, extra=None):
        return {k: [game_vec(src.filter(pl.col("book") == b), "hold", games, k, extra) for b in books]
                for k in keys}
    drop = lambda d: {k: [c for c in v if np.isfinite(c).any()] or [np.full(len(games), np.nan)]
                      for k, v in d.items()}
    out["hold_bench"], hr = summarise("hold", drop(hold_comps(close, BENCH)), games, "high", ranked)
    out["hold_all"], _ = summarise("hold_all", {k: [game_vec(close, "hold", games, k)] for k in keys},
                                   games, "high", ranked)
    band = (pl.col("p") >= BAND[0]) & (pl.col("p") <= BAND[1])
    out["hold_bench_band"], _ = summarise("hold_band", drop(hold_comps(close, BENCH, band)), games, "high", ranked)
    for b in BENCH:
        out["hold_" + b], _ = summarise(b, {k: [game_vec(close.filter(pl.col("book") == b), "hold", games, k)]
                                             for k in keys}, games, "high", ranked)
    if panel == "L":
        out["hold_bench_unfiltered"], _ = summarise(
            "hold_unf", drop(hold_comps(close_all, BENCH)), games, "high", ranked)
    out["hold_books"] = {k: sorted(close.filter(pl.col("key") == k)["book"].unique().to_list()) for k in keys}

    # ---- proxy 2: disagreement
    cl, _ = disagreement(close, kap)
    use = {k: ("combined" if kap.get(k) else "same_line") for k in keys}
    cl = cl.with_columns(ranked_fig=pl.when(pl.col("key").is_in([k for k in keys if kap.get(k)]))
                         .then(pl.col("combined")).otherwise(pl.col("same_line")))
    out["dis"], dr = summarise("dis", {k: [game_vec(cl, "ranked_fig", games, k)] for k in keys},
                               games, "high", ranked)
    out["dis_same_line"], _ = summarise("dis_sl", {k: [game_vec(cl, "same_line", games, k)] for k in keys},
                                        games, "high", ranked)
    out["dis_split"], _ = summarise("dis_split", {k: [game_vec(cl, "split", games, k)] for k in keys},
                                    games, "high", ranked)
    clb, _ = disagreement(close.filter(pl.col("book").is_in(BENCH)), kap)
    clb = clb.with_columns(ranked_fig=pl.when(pl.col("key").is_in([k for k in keys if kap.get(k)]))
                           .then(pl.col("combined")).otherwise(pl.col("same_line")))
    out["dis_bench"], _ = summarise("dis_bench", {k: [game_vec(clb, "ranked_fig", games, k)] for k in keys},
                                    games, "high", ranked)
    cx, _ = disagreement(close, kap_x)
    cx = cx.with_columns(ranked_fig=pl.when(pl.col("key").is_in([k for k in keys if kap_x.get(k)]))
                         .then(pl.col("combined")).otherwise(pl.col("same_line")))
    out["dis_registered_kappa"], _ = summarise("dis_reg", {k: [game_vec(cx, "ranked_fig", games, k)] for k in keys},
                                               games, "high", ranked)
    out["dis_not_comparable"] = [k for k in keys if k in no_dis]
    out["dis_figure"] = use
    out["dis_books_median"] = {k: float(cl.filter(pl.col("key") == k)["n_books"].median() or 0) for k in keys}
    out["claims_per_game"] = {
        k: float(close.filter(pl.col("key") == k).group_by("event_id")
                 .agg(pl.struct("subj").n_unique())["subj"].median() or 0) for k in keys}
    out["main_line_p10"] = {k: float(close.filter(pl.col("key") == k)["pline"].quantile(0.10) or float("nan"))
                            for k in keys}
    if panel == "L":
        cu, _ = disagreement(close_all, kap)
        cu = cu.with_columns(ranked_fig=pl.when(pl.col("key").is_in([k for k in keys if kap.get(k)]))
                             .then(pl.col("combined")).otherwise(pl.col("same_line")))
        out["dis_unfiltered"], _ = summarise("dis_unf", {k: [game_vec(cu, "ranked_fig", games, k)] for k in keys},
                                             games, "high", ranked)

    # ---- proxy 3: movement
    mr = None
    mv_keys = keys if panel == "L" else [k for k in keys if k in REFERENCE]
    if mv_keys:
        open_ = main_at(p, (lead >= OPEN_MIN_LEAD) & (lead <= OPEN_MAX_LEAD))
        first = main_at(p, "first")
        if panel == "L":
            open_ = open_.filter(fresh)
        mv = movement(open_, close, kap)
        mf = movement(first, close, kap).filter(pl.col("lead_h") >= 24.0)
        fig = lambda d: d.with_columns(ranked_fig=pl.when(pl.col("key").is_in([k for k in keys if kap.get(k)]))
                                       .then(pl.col("move")).otherwise(pl.col("same_move")))
        fig_x = lambda d: d.with_columns(
            ranked_fig=pl.when(pl.col("key").is_in([k for k in keys if kap_x.get(k)]))
            .then(pl.col("move")).otherwise(pl.col("same_move")))
        mx = fig_x(movement(open_, close, kap_x))
        out["mv_registered_kappa"], _ = summarise(
            "mv_reg", {k: [game_vec(mx, "ranked_fig", games, k)] for k in mv_keys}, games, "low", ranked)
        mv, mf = fig(mv), fig(mf)
        out["mv"], mr = summarise("mv", {k: [game_vec(mv, "ranked_fig", games, k)] for k in mv_keys},
                                  games, "low", ranked)
        out["mv_same_line"], _ = summarise("mv_sl", {k: [game_vec(mv, "same_move", games, k)] for k in mv_keys},
                                           games, "low", ranked)
        out["mv_line_changed"], _ = summarise("mv_chg", {k: [game_vec(mv, "chg", games, k)] for k in mv_keys},
                                              games, "low", ranked)
        out["mv_signed"], _ = summarise("mv_signed", {k: [game_vec(mv, "dp", games, k, ~pl.col("changed"))]
                                                      for k in mv_keys}, games, "low", ranked)
        out["mv_first"], _ = summarise("mv_first", {k: [game_vec(mf, "ranked_fig", games, k)] for k in mv_keys},
                                       games, "low", ranked)
        out["mv_open_lead_h"] = {k: float(mv.filter(pl.col("key") == k)["lead_h"].median() or float("nan"))
                                 for k in mv_keys}
        out["mv_first_lead_h"] = {k: float(mf.filter(pl.col("key") == k)["lead_h"].median() or float("nan"))
                                  for k in mv_keys}
        out["mv_rows"] = {k: mv.filter(pl.col("key") == k).height for k in mv_keys}

    # ---- composites over the ranked keys, on the shared draws; ranks taken within the common set
    comp = {}
    _, hdk = summarise("hdk", {k: [game_vec(close.filter(pl.col("book") == "draftkings"), "hold", games, k)]
                               for k in keys}, games, "high", ranked)
    parts = {"hold": (hr, 1.0, out["hold_bench"]), "hold_dk": (hdk, 1.0, out.get("hold_draftkings")),
             "dis": (dr, 1.0, out["dis"]), "mv": (mr, -1.0, out.get("mv"))}
    for label, use_p in (("A", ("hold", "dis", "mv")), ("B", ("hold", "dis")), ("B-DK", ("hold_dk", "dis"))):
        if any(parts[x][0] is None or parts[x][2] is None for x in use_p):
            continue
        rk = [k for k in ranked
              if all(k in parts[x][2] and parts[x][2][k]["games"] >= MIN_GAMES_RANKED
                     and math.isfinite(parts[x][0][0][k]) for x in use_p)
              and not ("dis" in use_p and k in no_dis)]
        if len(rk) < 3:
            continue
        pt = np.mean([ranks_desc(parts[x][1] * np.array([parts[x][0][0][k] for k in rk])) for x in use_p], axis=0)
        D = np.mean([np.vstack([ranks_desc(parts[x][1] * np.array([parts[x][0][1][k][i] for k in rk]))
                                for i in range(DRAWS)]) for x in use_p], axis=0)
        order = ranks_desc(-pt)
        Dord = np.vstack([ranks_desc(-D[i]) for i in range(D.shape[0])])
        half = len(rk) / 2.0
        comp[label] = {k: {"mean_rank": float(pt[i]), "lo": pct(D[:, i], 2.5), "hi": pct(D[:, i], 97.5),
                           "order": float(order[i]), "p_top_half": float(np.mean(Dord[:, i] <= half)),
                           "soft": bool(order[i] <= half)} for i, k in enumerate(rk)}
    out["composite"] = comp
    tri = [k for k in ranked if k in out["hold_bench"] and k in out["dis"] and (mr is None or k in out.get("mv", {}))]
    if len(tri) >= 4:
        h = [out["hold_bench"][k]["est"] for k in tri]
        d = [out["dis"][k]["est"] for k in tri]
        sp = {"hold_dis": float(spearmanr(h, d)[0])}
        if mr is not None:
            m = [out["mv"][k]["est"] for k in tri]
            sp["hold_mv"] = float(spearmanr(h, m)[0])
            sp["dis_mv"] = float(spearmanr(d, m)[0])
        out["proxy_spearman"] = sp
    return out, close, n


def one_sided(n, now, key="player_anytime_td"):
    """Raw-price descriptive figures for a one-sided key (no de-vig applies)."""
    d = n.filter((pl.col("key") == key) & (pl.col("side") == "A") & (pl.col("kick") < now))
    if not d.height:
        return None
    g = ["book", "event_id", "key", "subj"]
    lead = pl.col("kick") - pl.col("ts")
    last = lambda e: e.filter(pl.col("ts") == pl.col("ts").max().over(g)).unique(subset=g, keep="first")
    close = last(d.filter((lead <= CLOSE_MAX_LEAD) & ((pl.col("ts") - pl.col("source_ts")) <= FRESH_MAX_AGE)))
    open_ = last(d.filter((lead >= OPEN_MIN_LEAD) & (lead <= OPEN_MAX_LEAD)
                          & ((pl.col("ts") - pl.col("source_ts")) <= FRESH_MAX_AGE)))
    games = sorted(close["event_id"].unique().to_list())
    c = close.select("event_id", "key", "subj", "book", "raw")
    j = c.join(c, on=["event_id", "key", "subj"], suffix="_j").filter(pl.col("book") < pl.col("book_j"))
    cl = j.group_by(["event_id", "key", "subj"]).agg(v=(pl.col("raw") - pl.col("raw_j")).abs().mean())
    m = open_.select(g + ["raw"]).join(close.select(g + ["raw"]), on=g, suffix="_c").with_columns(
        v=(pl.col("raw_c") - pl.col("raw")).abs())
    dis, _ = summarise("td_dis", {key: [game_vec(cl, "v", games, key)]}, games, "high", ())
    mv, _ = summarise("td_mv", {key: [game_vec(m, "v", games, key)]}, games, "low", ())
    return {"games": len(games), "raw_same_claim_dispersion": dis[key], "raw_move": mv[key],
            "books": sorted(close["book"].unique().to_list()),
            "claims_per_game": float(close.group_by("event_id").agg(pl.col("subj").n_unique())["subj"].median())}


def catalogue(n, panel):
    rows = []
    for (k,), d in n.group_by(["key"]):
        sides = set(d["side"].drop_nulls().unique().to_list())
        per_game = d.group_by("event_id").agg(c=pl.struct("subj", "pline").n_unique())["c"].median()
        rows.append({"key": k, "panel": panel, "rows": d.height, "books": d["book"].n_unique(),
                     "games": d["event_id"].n_unique(), "claim_lines_per_game_median": float(per_game),
                     "two_sided": sides == {"A", "B"},
                     "first_ts": float(d["ts"].min()), "last_ts": float(d["ts"].max())})
    return sorted(rows, key=lambda r: r["key"])


# =============================================================================
# forecastability
# =============================================================================

def forecastability(db, screens, draws=None):
    """R2 of the as-of trailing-8 mean against the actual, REG 2023-2025."""
    draws = FC_DRAWS if draws is None else draws
    con = _ro(db)
    try:
        cols = ["gsis_id", "season", "week", "position", "receptions", "receiving_yards", "carries",
                "rushing_yards", "attempts", "passing_yards", "passing_tds", "rushing_tds", "receiving_tds",
                "def_tackles_solo", "def_tackles_with_assist", "def_tackle_assists", "def_sacks"]
        rows = con.execute("select %s from nfl_player_week where sport='nfl' and season between 2022 and 2025 "
                           "and season_type='REG'" % ", ".join(cols)).fetchall()
    finally:
        con.close()
    df = pl.DataFrame(rows, schema=cols, orient="row", infer_schema_length=None)
    assert df.height > 50000, "nfl_player_week extract too small: %d" % df.height
    assert df.select(pl.struct("gsis_id", "season", "week").is_duplicated().sum()).item() == 0, \
        "duplicate player-weeks (more than one data_version)"
    num = [c for c in cols if c not in ("gsis_id", "season", "week", "position")]
    df = df.with_columns([pl.col(c).cast(pl.Float64).fill_null(0.0).fill_nan(0.0) for c in num])
    df = df.with_columns(
        tackles_assists=pl.col("def_tackles_solo") + pl.col("def_tackles_with_assist") + pl.col("def_tackle_assists"),
        anytime_td=((pl.col("rushing_tds") + pl.col("receiving_tds")) > 0).cast(pl.Float64),
    ).sort("gsis_id", "season", "week")
    out = {}
    rng = np.random.default_rng(SEED)
    prior = lambda c: pl.col(c).shift(1).rolling_mean(window_size=8, min_samples=4).over("gsis_id")
    for key, (stat, qb) in STAT.items():
        d = df.filter(pl.col("position") == "QB") if qb else df
        d = d.with_columns(x=prior(stat), y=pl.col(stat))
        if key == "player_anytime_td":
            # no line exists for a one-sided market: the population is players passing the
            # receptions OR the rush-attempts screen (declared in the findings as a gap filled)
            d = d.with_columns(xr=prior("receptions"), xc=prior("carries")).filter(
                (pl.col("xr") >= screens["player_receptions"]) | (pl.col("xc") >= screens["player_rush_attempts"]))
            scr = None
        else:
            scr = screens.get(key)
            if scr is None or not math.isfinite(scr):
                continue
            d = d.filter(pl.col("x") >= scr)
        d = d.filter((pl.col("season") >= 2023) & pl.col("x").is_not_null())
        if d.height < 200:
            continue
        x, y = d["x"].to_numpy(), d["y"].to_numpy()
        _, inv = np.unique(d["gsis_id"].to_numpy(), return_inverse=True)
        P = inv.max() + 1
        S = np.zeros((P, 7))
        for j, v in enumerate((np.ones_like(x), x, y, x * x, y * y, x * y, (y - x) ** 2)):
            S[:, j] = np.bincount(inv, weights=v, minlength=P)

        def stats(T):
            nn, sx, sy, sxx, syy, sxy, sr = T
            cov = sxy - sx * sy / nn
            vx, vy = sxx - sx * sx / nn, syy - sy * sy / nn
            r2 = cov * cov / (vx * vy) if vx > 0 and vy > 0 else float("nan")
            mres = (sy - sx) / nn
            cv = math.sqrt(max(sr / nn - mres * mres, 0.0)) / (sy / nn) if sy > 0 else float("nan")
            return r2, cv
        h = 0.25
        pos = x > 0
        k_emp = float((np.mean(y[pos] > x[pos] * math.exp(-h)) - np.mean(y[pos] > x[pos] * math.exp(h))) / (2 * h))
        r2, cv = stats(S.sum(axis=0))
        bs = np.array([stats(S[rng.integers(0, P, P)].sum(axis=0)) for _ in range(draws)])
        out[key] = {"stat": stat, "n": int(d.height), "players": int(P), "screen": scr,
                    "r2": r2, "r2_lo": pct(bs[:, 0], 2.5), "r2_hi": pct(bs[:, 0], 97.5),
                    "spearman": float(spearmanr(x, y)[0]),
                    "cv": cv, "cv_lo": pct(bs[:, 1], 2.5), "cv_hi": pct(bs[:, 1], 97.5),
                    "mean_actual": float(y.mean()), "kappa_empirical": k_emp}
    return out


def shortlist(panel_out, fc, ranked):
    res = {}
    for label, comp in panel_out["composite"].items():
        if label not in ("A", "B"):          # B-DK is a sensitivity (addendum 1.3), not part of the rule
            continue
        ks = [k for k in ranked if k in comp and k in fc]
        if not ks:
            continue
        fr = ranks_desc(np.array([fc[k]["r2"] for k in ks]))
        res[label] = {k: {"soft": comp[k]["soft"], "forecastable": bool(fr[i] <= len(ks) / 2.0),
                          "r2_rank": float(fr[i]), "soft_order": comp[k]["order"]} for i, k in enumerate(ks)}
    keys = set().union(*[set(v) for v in res.values()]) if res else set()
    verdict = {}
    for k in keys:
        hits = [lab for lab, v in res.items() if k in v and v[k]["soft"] and v[k]["forecastable"]]
        verdict[k] = "shortlisted" if len(hits) == len(res) else ("conditional" if hits else "no")
    return {"by_composite": res, "verdict": verdict}


# =============================================================================
# printing
# =============================================================================

def _f(r, scale=100.0, nd=2):
    if r is None or not math.isfinite(r.get("est", float("nan"))):
        return "      n/a"
    s = "%6.*f [%6.*f,%6.*f]" % (nd, r["est"] * scale, nd, r["lo"] * scale, nd, r["hi"] * scale)
    if not r.get("read", True):
        s += " (<5 games: not read)"
    if "rank" in r:
        s += "  rank %.0f [%.0f,%.0f]" % (r["rank"], r["rank_lo"], r["rank_hi"])
    return s


def show(title, tbl, keys, scale=100.0, nd=2):
    print("\n  %s" % title)
    for k in keys:
        if k in tbl:
            print("    %-26s n=%-4d %s" % (k, tbl[k]["games"], _f(tbl[k], scale, nd)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--json-out")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)
    db = os.environ.get("LOGGER_DB")
    if not db or not os.path.exists(db):
        raise SystemExit("LOGGER_DB must name market_log.db (opened mode=ro)")
    now = time.time()
    t0 = time.time()
    L, H, K = extract(db, a.cache, a.refresh)
    print("c-33 soft markets | run %s UTC | extract live %d rows, historical %d rows, kickoffs %d"
          % (time.strftime("%Y-%m-%d %H:%M", time.gmtime(now)), L.height, H.height, K.height))
    nL, nH = normalise(L, K, "L"), normalise(H, K, "H")
    assert nL.height > 0 and nH.height > 0, "normalise produced no rows"
    res = {"run_ts": now, "catalogue": catalogue(nL, "L") + catalogue(nH, "H")}
    print("\nCATALOGUE (pre-kickoff rows on disk)")
    for r in res["catalogue"]:
        print("  %s %-32s books %-2d games %-3d claim-lines/game %-6.0f two-sided %-5s %s -> %s  rows %d"
              % (r["panel"], r["key"], r["books"], r["games"], r["claim_lines_per_game_median"], r["two_sided"],
                 time.strftime("%Y-%m-%d", time.gmtime(r["first_ts"])),
                 time.strftime("%Y-%m-%d", time.gmtime(r["last_ts"])), r["rows"]))

    fc_screens = {}
    ladder = None
    for panel, n, ranked in (("L", nL, RANKED_L), ("H", nH, RANKED_H)):
        out, close, _ = run_panel(panel, n, ranked, now, ladder=ladder,
                                  no_dis=("player_sacks",) if panel == "H" else ())
        if panel == "L":
            ladder = out["info"]["kappa_ladder"]
        res[panel] = out
        keys = out["keys"]
        i = out["info"]
        print("\n" + "=" * 100)
        print("PANEL %s | games %d | rows %s -> %s UTC | pair groups %d, two-sided %d, valid %d%s"
              % (panel, i["games"], time.strftime("%Y-%m-%d %H:%M", time.gmtime(i["window"][0])),
                 time.strftime("%Y-%m-%d %H:%M", time.gmtime(i["window"][1])),
                 i["pair_counts"]["groups"], i["pair_counts"]["two_sided"], i["pair_counts"]["valid"],
                 (" | fresh share of closes %.3f" % i["close_fresh_share"]) if panel == "L" else ""))
        print("  kappa per key: used [source] | cross-book (pairs) | ladder median [q25, q75] (claims)")
        for k in keys:
            ld = i["kappa_ladder"].get(k)
            print("    %-26s used %-6s [%-10s] | cross-book %-6s (%6d) | ladder %s" % (
                k, ("%.3f" % i["kappa"][k]) if i["kappa"][k] else "n/a", i["kappa_source"][k],
                ("%.3f" % i["kappa_cross_book"][k]) if i["kappa_cross_book"][k] else "n/a", i["kappa_pairs"][k],
                ("%.3f [%.3f, %.3f] (%d)" % (ld["kappa"] or float("nan"), ld["q25"], ld["q75"], ld["claims"]))
                if ld else "none"))
        show("PROXY 1 hold, pp - bench books equal-weighted (PRIMARY, high = soft)", out["hold_bench"], keys)
        show("  hold, bench, p in [0.40,0.60] only", out["hold_bench_band"], keys)
        show("  hold, all books pooled", out["hold_all"], keys)
        for b in BENCH:
            show("  hold, %s alone" % b, out["hold_" + b], keys)
        if panel == "L":
            show("  hold, bench, no freshness filter", out["hold_bench_unfiltered"], keys)
        print("\n  books quoting two-sided at the close: " + "; ".join(
            "%s %d" % (k.replace("player_", ""), len(out["hold_books"][k])) for k in keys))
        show("PROXY 2 disagreement, pp - all books (PRIMARY, high = soft); figure per key: "
             + ", ".join("%s=%s" % (k.replace("player_", ""), out["dis_figure"][k]) for k in keys), out["dis"], keys)
        if out["dis_not_comparable"]:
            print("    NOT COMPARABLE (addendum 1.2, line formats differ across books): %s"
                  % ", ".join(out["dis_not_comparable"]))
        show("  as REGISTERED (cross-book kappa) - superseded by addendum 1, shown for the size of the correction",
             out["dis_registered_kappa"], keys)
        show("  same-line mean |dp|, pp", out["dis_same_line"], keys)
        show("  share of book pairs on different lines, %", out["dis_split"], keys, nd=1)
        show("  disagreement, bench books only", out["dis_bench"], keys)
        if panel == "L":
            show("  disagreement, no freshness filter", out["dis_unfiltered"], keys)
        print("\n  median books per claim: " + "; ".join("%s %.0f" % (k.replace("player_", ""), out["dis_books_median"][k]) for k in keys))
        print("  median claims per game: " + "; ".join("%s %.0f" % (k.replace("player_", ""), out["claims_per_game"][k]) for k in keys))
        if "mv" in out:
            mk = [k for k in keys if k in out["mv"]]
            show("PROXY 3 movement T-24h -> close, pp (PRIMARY, LOW = soft)", out["mv"], mk)
            show("  as REGISTERED (cross-book kappa) - superseded by addendum 1", out["mv_registered_kappa"], mk)
            show("  same-line |dp|, pp", out["mv_same_line"], mk)
            show("  share whose main line changed, %", out["mv_line_changed"], mk, nd=1)
            show("  signed same-line dp (over side), pp", out["mv_signed"], mk)
            show("  earliest read on disk (>=24h) -> close, pp", out["mv_first"], mk)
            print("\n  median open lead h (T-24h rule / earliest): " + "; ".join(
                "%s %.0f/%.0f" % (k.replace("player_", ""), out["mv_open_lead_h"][k], out["mv_first_lead_h"][k]) for k in mk))
        for lab, comp in out["composite"].items():
            print("\n  COMPOSITE %s (mean rank, 1 = softest)" % lab)
            for k, v in sorted(comp.items(), key=lambda kv: kv[1]["mean_rank"]):
                print("    %-26s %.2f [%.2f, %.2f]  order %.0f  P(top half) %.2f" %
                      (k, v["mean_rank"], v["lo"], v["hi"], v["order"], v["p_top_half"]))
        if "proxy_spearman" in out:
            print("\n  Spearman among proxies across ranked keys (descriptive): %s" %
                  ", ".join("%s %+.2f" % kv for kv in out["proxy_spearman"].items()))
        for k in ranked:
            if k in out["main_line_p10"] and (panel == "L" or k not in fc_screens):
                fc_screens[k] = out["main_line_p10"][k]

    td = one_sided(nL, now)
    res["anytime_td"] = td
    if td:
        print("\nONE-SIDED player_anytime_td (raw prices, no de-vig, NOT ranked): games %d, books %d, "
              "claims/game %.0f\n    raw same-claim dispersion pp %s\n    raw move T-24h->close pp %s"
              % (td["games"], len(td["books"]), td["claims_per_game"],
                 _f(td["raw_same_claim_dispersion"]), _f(td["raw_move"])))

    fc = forecastability(db, fc_screens)
    res["forecastability"] = fc
    res["screens"] = fc_screens
    print("\n" + "=" * 100)
    print("AXIS 2 forecastability - trailing-8 as-of mean vs actual, REG 2023-2025 (player-block bootstrap)")
    for k, v in sorted(fc.items(), key=lambda kv: -kv[1]["r2"]):
        print("  %-26s %-16s n=%-6d players=%-4d screen>=%-6s R2 %.3f [%.3f, %.3f]  spearman %.3f  CV %.2f [%.2f, %.2f]  kappa_emp %.2f"
              % (k, v["stat"], v["n"], v["players"], ("%.1f" % v["screen"]) if v["screen"] is not None else "rec|rush",
                 v["r2"], v["r2_lo"], v["r2_hi"], v["spearman"], v["cv"], v["cv_lo"], v["cv_hi"], v["kappa_empirical"]))
    for panel, ranked in (("L", RANKED_L), ("H", RANKED_H)):
        sl = shortlist(res[panel], fc, ranked)
        res[panel]["shortlist"] = sl
        print("\nSHORTLIST panel %s (soft = top half of composite; forecastable = top half of R2)" % panel)
        for k in ranked:
            if k in sl["verdict"]:
                print("  %-26s %-12s %s" % (k, sl["verdict"][k], "  ".join(
                    "%s: soft=%s fc=%s" % (lab, v[k]["soft"], v[k]["forecastable"])
                    for lab, v in sl["by_composite"].items() if k in v)))
    print("\nelapsed %.1fs" % (time.time() - t0))
    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(res, f, indent=1, default=lambda o: None if isinstance(o, float) and not math.isfinite(o) else str(o))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
