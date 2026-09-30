"""c-31 - the total, built from two teams instead of a league constant.

    LOGGER_DB=<market_log.db> python -m research.game_total --json-out D:/temp/c31/result.json

PRE-REGISTRATION: docs/C31-game-total-preregistration.md, committed and pushed
at 1870535 BEFORE this script existed. This file implements it; it does not
extend it. Comments say only where the code carries a rule out.

The margin is c-28's `GameForecast`, built by c-28's own code
(`research.game_forecast`: Walk, margin_sigmas, total_params, forecast,
kalshi_closes, compare, mse_compare), imported, not copied. The total is
`models.game_total.GameTotalForecast`.

Opened read-only: market_log.db (mode=ro via jobs.season_model.market_log_ro),
analytics.db (mode=ro, track F's `f_team_game_pace`), and the nflverse
games.parquet from the raw archive (`wind`, `roof`). Nothing is written except
--json-out. Everything printed is an aggregate or an interval.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from itertools import groupby

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                           # noqa: E402
from jobs import season_model as S                      # noqa: E402
from models import game as G                            # noqa: E402
from models import game_total as GT                     # noqa: E402
from research import game_forecast as GF                # noqa: E402
from research import ranking_calibration as rc          # noqa: E402

FIT_FROM = GF.FIT_FROM             # 2000
SCORE_FROM, SCORE_TO = 2001, 2025
BOOK_FROM = 2006
KALSHI_SEASON = GF.KALSHI_SEASON
C28_P1_BRIER = 0.2205815236861795
LADDER = (33.5, 36.5, 39.5, 42.5, 45.5, 48.5, 51.5, 54.5)
K_GRID = (2, 4, 6, 8, 12, 16)
R_GRID = (0.2, 0.35, 0.5, 0.65, 0.8)
EDGE = 0.03
ARMS = ("FULL", "PLAYS_ONLY", "PPP_ONLY", "NO_SCRIPT", "NO_WIND")


# =============================================================================
# inputs
# =============================================================================

def analytics_ro():
    path = os.path.abspath(config.storage_path("analytics.db"))
    return sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True, timeout=5)


def load_pace(games_by_id):
    """{(game_id, franchise(team)): plays} from f_team_game_pace, situation 'all'."""
    con = analytics_ro()
    rows = con.execute("SELECT game_id, team, plays, drives FROM f_team_game_pace "
                       "WHERE situation = 'all'").fetchall()
    con.close()
    out, drives, unmatched = {}, {}, 0
    for gid, team, plays, dr in rows:
        g = games_by_id.get(gid)
        t = G.franchise(team)
        if g is None or t not in (G.franchise(g["home"]), G.franchise(g["away"])):
            unmatched += 1
            continue
        out[(gid, t)] = float(plays)
        drives[(gid, t)] = float(dr)
    return out, drives, unmatched


def load_weather(con, version):
    """{game_id: (roof, wind)} from the archived games.parquet of the nfl_games version."""
    import polars as pl
    rel = con.execute("SELECT rel_path FROM nflverse_versions WHERE dataset = 'games' "
                      "AND data_version = ? ORDER BY ingested_ts DESC LIMIT 1", (version,)).fetchone()
    if rel is None:
        raise SystemExit(f"no archived games.parquet for data_version {version}")
    path = config.storage_path("raw", *rel[0].split("/"))
    d = pl.read_parquet(path, columns=["game_id", "roof", "wind"])
    return {r[0]: (r[1] or "", r[2]) for r in d.iter_rows()}, rel[0]


def weather_x(w):
    roof, wind = w if w is not None else ("", None)
    outdoor = roof in ("outdoors", "open")
    wind_out = float(wind) if outdoor and wind is not None and not _nan(wind) else 0.0
    dome = 1.0 if roof in ("dome", "closed") else 0.0
    wind_na = 1.0 if (roof in ("outdoors", "open", "") and (wind is None or _nan(wind))) else 0.0
    return wind_out, dome, wind_na


def _nan(x):
    return isinstance(x, float) and math.isnan(x)


# =============================================================================
# the factors - walk the league once, in kickoff order
# =============================================================================

def factor_states(games, pace):
    """{(game index, 'home'|'away'): state} frozen before the game's kickoff instant.
    Games sharing a kickoff never see each other: every state in a kickoff group
    is read before any of that group is added."""
    order = sorted((i for i, g in enumerate(games) if g["kickoff_ts"] is not None),
                   key=lambda i: (games[i]["kickoff_ts"], games[i]["game_id"]))
    book = GT.FactorBook()
    states = {}
    for _k, grp in groupby(order, key=lambda i: games[i]["kickoff_ts"]):
        grp = list(grp)
        for i in grp:
            g = games[i]
            h, a = G.franchise(g["home"]), G.franchise(g["away"])
            for side, t, o in (("home", h, a), ("away", a, h)):
                st = book.raw_state(t, o, g["season"])
                if st is not None:
                    states[(i, side)] = st
        for i in grp:
            g = games[i]
            if g["home_score"] is None:
                continue
            h, a = G.franchise(g["home"]), G.franchise(g["away"])
            for t, o, pts in ((h, a, g["home_score"]), (a, h, g["away_score"])):
                p = pace.get((g["game_id"], t))
                if p is None or p <= 0:
                    continue                      # no pace row: plays AND points unused
                book.add(GT.TeamGame(g["game_id"], g["season"], g["kickoff_ts"], t, o, p, float(pts)))
    return states


def fit_factor_params(games, pace, states, years):
    """{T: (kp, rp, kq, rq)} minimising training error on team-games 2000..T-1."""
    tg = []         # (season, state, plays, points)
    for (i, side), st in states.items():
        g = games[i]
        if g["home_score"] is None:
            continue
        t = G.franchise(g["home"] if side == "home" else g["away"])
        p = pace.get((g["game_id"], t))
        if p is None or p <= 0:
            continue
        pts = g["home_score"] if side == "home" else g["away_score"]
        tg.append((g["season"], st, p, float(pts)))
    seasons = np.array([x[0] for x in tg])
    plays = np.array([x[2] for x in tg])
    ppp = np.array([x[3] / x[2] for x in tg])
    sse_p, sse_q = {}, {}
    for k in K_GRID:
        for r in R_GRID:
            ph = np.array([GT.team_expectation(x[1], k, r, 1, 0.5, use_ppp=False)[0] for x in tg])
            qh = np.array([GT.team_expectation(x[1], 1, 0.5, k, r, use_plays=False)[1] for x in tg])
            sse_p[(k, r)] = (plays - ph) ** 2
            sse_q[(k, r)] = plays * (ppp - qh) ** 2
    out = {}
    for T in years:
        m = (seasons >= FIT_FROM) & (seasons < T)
        bp = min(sse_p, key=lambda kr: (float(sse_p[kr][m].sum()), kr))
        bq = min(sse_q, key=lambda kr: (float(sse_q[kr][m].sum()), kr))
        out[T] = (bp[0], bp[1], bq[0], bq[1])
    return out, len(tg)


# =============================================================================
# baselines
# =============================================================================

def season_avg_means(games, tot):
    """{game index: (PF_H + PA_H + PF_A + PA_A) / 2} from season-to-date per-game
    averages before kickoff; no current-season game -> the full previous season;
    neither -> the league window mean."""
    order = sorted((i for i, g in enumerate(games) if g["kickoff_ts"] is not None),
                   key=lambda i: (games[i]["kickoff_ts"], games[i]["game_id"]))
    acc = defaultdict(lambda: [0, 0.0, 0.0])      # (team, season) -> n, pf, pa
    out = {}
    for _k, grp in groupby(order, key=lambda i: games[i]["kickoff_ts"]):
        grp = list(grp)
        for i in grp:
            g = games[i]
            parts = []
            for t in (G.franchise(g["home"]), G.franchise(g["away"])):
                c = acc.get((t, g["season"]))
                if c and c[0]:
                    parts.append((c[1] + c[2]) / c[0])
                else:
                    pc = acc.get((t, g["season"] - 1))
                    parts.append((pc[1] + pc[2]) / pc[0] if pc and pc[0] else
                                 (tot[i][0] if i in tot else None))
            if None not in parts:
                out[i] = (parts[0] + parts[1]) / 2.0
        for i in grp:
            g = games[i]
            if g["home_score"] is None:
                continue
            for t, pf, pa in ((G.franchise(g["home"]), g["home_score"], g["away_score"]),
                              (G.franchise(g["away"]), g["away_score"], g["home_score"])):
                c = acc[(t, g["season"])]
                c[0] += 1
                c[1] += pf
                c[2] += pa
    return out


def train_sd(values_by_i, games, T):
    """SD of (total - prediction) over scored games in 2000..T-1."""
    r = [games[i]["home_score"] + games[i]["away_score"] - v for i, v in values_by_i.items()
         if FIT_FROM <= games[i]["season"] < T and games[i]["home_score"] is not None]
    return float(np.std(r, ddof=1))


# =============================================================================
# the stack
# =============================================================================

def design(arm, raw, eam, wx):
    wind_out, dome, wind_na = wx
    if arm == "NO_SCRIPT":
        return [1.0, raw, wind_out, dome, wind_na]
    if arm == "NO_WIND":
        return [1.0, raw, eam]
    return [1.0, raw, eam, wind_out, dome, wind_na]


def fit_stacks(feat, games, years):
    """{arm: {T: (beta, gamma, s_e)}}. OLS on scored games 2000..T-1, then gamma from
    the residual on (|M| - E|M|), no intercept; s_e ddof = terms fitted."""
    fits = {a: {} for a in ARMS}
    idx = [i for i in feat if games[i]["home_score"] is not None]
    seasons = np.array([games[i]["season"] for i in idx])
    y = np.array([games[i]["home_score"] + games[i]["away_score"] for i in idx], float)
    absm = np.array([abs(games[i]["home_score"] - games[i]["away_score"]) for i in idx], float)
    eam = np.array([feat[i]["eam"] for i in idx])
    for arm in ARMS:
        rawkey = {"PLAYS_ONLY": "raw_plays", "PPP_ONLY": "raw_ppp"}.get(arm, "raw")
        X = np.array([design(arm, feat[i][rawkey], feat[i]["eam"], feat[i]["wx"]) for i in idx])
        for T in years:
            m = (seasons >= FIT_FROM) & (seasons < T)
            beta, res = GT.ols(X[m], y[m])
            if arm == "NO_SCRIPT":
                gamma, rem, nterm = 0.0, res, len(beta)
            else:
                z = absm[m] - eam[m]
                gamma = float(z @ res / (z @ z))
                rem, nterm = res - gamma * z, len(beta) + 1
            s_e = float(np.sqrt((rem ** 2).sum() / (m.sum() - nterm)))
            fits[arm][T] = (beta, gamma, s_e)
    return fits


def mean_of(arm, fits, feat, games, i):
    rawkey = {"PLAYS_ONLY": "raw_plays", "PPP_ONLY": "raw_ppp"}.get(arm, "raw")
    beta, _g, _s = fits[arm][games[i]["season"]]
    return float(np.dot(beta, design(arm, feat[i][rawkey], feat[i]["eam"], feat[i]["wx"])))


# =============================================================================
# bootstrap helpers (game blocks, c-24's draw rule)
# =============================================================================

def boot_rows(fn, n, draws, seed=None):
    """One row per game: resample rows. -> {est, lo, hi, se, games, n}."""
    seed = rc.SEED if seed is None else seed
    est = fn(np.arange(n))
    rng = np.random.default_rng(seed)
    v = np.array([fn(rng.integers(0, n, n)) for _ in range(draws)])
    return {"est": est, "lo": float(np.percentile(v, 2.5)), "hi": float(np.percentile(v, 97.5)),
            "se": float(v.std()), "games": n, "n": n}


def fisher_r(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    r = float(np.corrcoef(x, y)[0, 1])
    z, se = math.atanh(r), 1.0 / math.sqrt(len(x) - 3)
    return {"r": r, "lo": math.tanh(z - 1.96 * se), "hi": math.tanh(z + 1.96 * se), "n": len(x)}


def american_profit(o):
    o = float(o)
    return o / 100.0 if o > 0 else 100.0 / -o


# =============================================================================
# main
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--draws", type=int, default=rc.BOOT)
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    tests = []
    result = {"preregistration": "docs/C31-game-total-preregistration.md @ 1870535"}
    con = S.market_log_ro()
    games, _grp, versions = S.load(con)
    by_id = {g["game_id"]: g for g in games}
    result["versions"] = versions
    out(f"games loaded {len(games):,}; versions {versions}")

    # ---------------------------------------------------------------- c-28, unmoved
    t0 = time.time()
    grid = S.grid()
    walk = GF.Walk(games, S.game_losses(games, grid), mov=True)
    years = list(range(SCORE_FROM, SCORE_TO + 1)) + [KALSHI_SEASON]
    sig = GF.margin_sigmas(games, walk, years)
    tot = GF.total_params(games)
    pop1 = [i for i, g in enumerate(games) if SCORE_FROM <= g["season"] <= SCORE_TO
            and GF.scored(g) and g["home_score"] != g["away_score"]]
    pm = np.array([walk.p(i) for i in pop1])
    ym = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop1])
    b = rc.brier(pm, ym)
    out(f"c-28 moneyline Part-1 Brier {b!r} (recorded {C28_P1_BRIER!r})  [{time.time() - t0:.0f}s]")
    if b != C28_P1_BRIER:
        raise SystemExit("c-28's moneyline moved - refusing to score")
    result["c28_moneyline_brier"] = b

    # ---------------------------------------------------------------- inputs
    pace, drives, unmatched = load_pace(by_id)
    wx_raw, wpath = load_weather(con, versions["nfl_games"])
    out(f"pace rows {len(pace):,} (unmatched {unmatched}); weather rows {len(wx_raw):,} from {wpath}")
    result["inputs"] = {"pace_rows": len(pace), "pace_unmatched": unmatched,
                        "weather_file": wpath, "weather_rows": len(wx_raw)}

    t0 = time.time()
    states = factor_states(games, pace)
    params, n_tg = fit_factor_params(games, pace, states, years)
    out(f"factor states {len(states):,}; team-games in fits {n_tg:,}  [{time.time() - t0:.0f}s]")
    out("factor params (kp, rp, kq, rq) by season: " +
        ", ".join(f"{T}:{params[T]}" for T in (2001, 2010, 2020, 2025, 2026)))
    result["factor_params"] = {T: params[T] for T in years}

    # ---------------------------------------------------------------- per-game features
    feat = {}
    base_fc = {}
    for i, g in enumerate(games):
        if (i, "home") not in states or (i, "away") not in states:
            continue
        if not (FIT_FROM <= g["season"] <= SCORE_TO or g["season"] == KALSHI_SEASON):
            continue
        if not GF.scored(g):
            continue                  # the rating walk carries only games that have been played
        if i not in tot:
            continue
        # Season-2000 rows exist only to TRAIN the 2001 stack. Nothing is fitted before
        # 2000, so they take season 2001's factor params, Elo params and sigma_m (each
        # fitted on 2000 alone): in-sample for those training rows, never for a scored
        # forecast. Addendum 1 in the pre-registration.
        T_par = g["season"] if g["season"] >= SCORE_FROM else SCORE_FROM
        kp, rp, kq, rq = params[T_par]
        th = GT.team_expectation(states[(i, "home")], kp, rp, kq, rq)
        ta = GT.team_expectation(states[(i, "away")], kp, rp, kq, rq)
        Lp, Lq = states[(i, "home")]["L"]
        if g["season"] >= SCORE_FROM:
            fc = GF.forecast(games, i, walk, sig, tot, "kickoff")
        else:
            mu_t, sd_t = tot[i]
            fc = G.GameForecast(game_id=g["game_id"], home=g["home"], away=g["away"], as_of="kickoff",
                                p_home=walk.pre(walk.params(SCORE_FROM))[i], sigma_m=sig[SCORE_FROM],
                                mu_t=mu_t, sigma_t=sd_t)
        base_fc[i] = fc
        feat[i] = {"raw": th[0] * th[1] + ta[0] * ta[1],
                   "raw_plays": th[0] * Lq + ta[0] * Lq,
                   "raw_ppp": Lp * th[1] + Lp * ta[1],
                   "plays_h": th[0], "plays_a": ta[0], "ppp_h": th[1], "ppp_a": ta[1],
                   "eam": GT.expected_abs_normal(fc.mu_m, fc.sigma_m),
                   "wx": weather_x(wx_raw.get(g["game_id"]))}
    out(f"games with features {len(feat):,}")
    stacks = fit_stacks(feat, games, [y for y in years if y > FIT_FROM])
    for T in (2001, 2025, 2026):
        bt, gm, se = stacks["FULL"][T]
        out(f"   FULL stack T={T}: beta {np.round(bt, 4).tolist()}  gamma {gm:.4f}  s_e {se:.3f}")
    result["stacks"] = {arm: {T: {"beta": v[0].tolist(), "gamma": v[1], "s_e": v[2]}
                              for T, v in d.items()} for arm, d in stacks.items()}

    def total_fc(i):
        beta, gamma, s_e = stacks["FULL"][games[i]["season"]]
        return GT.GameTotalForecast(base=base_fc[i], mu=mean_of("FULL", stacks, feat, games, i),
                                    gamma=gamma, s_e=s_e)

    # ---------------------------------------------------------------- Part 1
    sa = season_avg_means(games, tot)
    tl = {r[0]: r[1:] for r in con.execute(
        "SELECT g.game_id, g.total_line, g.over_odds, g.under_odds FROM nfl_games g JOIN "
        "(SELECT game_id, MAX(data_version) dv FROM nfl_games GROUP BY game_id) v "
        "ON v.game_id = g.game_id AND v.dv = g.data_version")}
    close_mean = {i: float(tl[games[i]["game_id"]][0]) for i in feat
                  if tl.get(games[i]["game_id"], (None,))[0] is not None}
    sd_sa = {T: train_sd({i: v for i, v in sa.items() if i in feat}, games, T) for T in years}
    sd_cl = {T: train_sd(close_mean, games, T) for T in years}

    P = [i for i in feat if SCORE_FROM <= games[i]["season"] <= SCORE_TO
         and GF.scored(games[i]) and i in sa and i in close_mean]
    missing = [i for i in feat if SCORE_FROM <= games[i]["season"] <= SCORE_TO and GF.scored(games[i])
               and (i not in sa or i not in close_mean)]
    n_scored = sum(1 for g in games if SCORE_FROM <= g["season"] <= SCORE_TO and GF.scored(g))
    out(f"\n######## PART 1 - against settlement {SCORE_FROM}-{SCORE_TO} (PRIMARY)")
    out(f"   population {len(P):,} of {n_scored:,} scored games (dropped for a missing baseline: {len(missing)})")
    tfc = {i: total_fc(i) for i in P}
    ytot = np.array([games[i]["home_score"] + games[i]["away_score"] for i in P], float)
    mu_full = np.array([tfc[i].mean() for i in P])
    bmean = {"league": np.array([tot[i][0] for i in P]),
             "season_avg": np.array([sa[i] for i in P]),
             "close": np.array([close_mean[i] for i in P])}
    blocks = [games[i]["game_id"] for i in P]
    from scipy.stats import norm
    bprob = {"league": lambda i, L: float(norm.sf(L, tot[i][0], tot[i][1])),
             "season_avg": lambda i, L: float(norm.sf(L, sa[i], sd_sa[games[i]["season"]])),
             "close": lambda i, L: float(norm.sf(L, close_mean[i], sd_cl[games[i]["season"]]))}
    mprob = {i: {L: tfc[i].prob_total_over(L) for L in LADDER} for i in P}

    p1 = {"pa": {}, "pb": {}}
    for bname in ("league", "season_avg", "close"):
        p1["pa"][bname] = GF.mse_compare(f"P-a mean total: model vs {bname}", ytot, mu_full, bmean[bname],
                                         blocks, out, tests, draws=a.draws)
    for bname in ("league", "season_avg", "close"):
        rows = [{"game": games[i]["game_id"], "stat": "total", "line": L,
                 "y": 1.0 if games[i]["home_score"] + games[i]["away_score"] > L else 0.0,
                 "m": mprob[i][L], "k": bprob[bname](i, L)} for i in P for L in LADDER]
        p1["pb"][bname] = GF.compare(f"P-b ladder: model vs {bname}", rows, out, tests,
                                     within_line=True, draws=a.draws)
    # P-c over the closing total, pushes out, comparator 0.5
    pc_rows = []
    for i in P:
        L = close_mean[i]
        T_ = games[i]["home_score"] + games[i]["away_score"]
        if T_ == L:
            continue
        pc_rows.append({"game": games[i]["game_id"], "stat": "over_close", "line": 0.0,
                        "y": 1.0 if T_ > L else 0.0, "m": tfc[i].prob_over_push_void(L), "k": 0.5})
    p1["pc"] = GF.compare("P-c over the closing total: model vs close (0.5)", pc_rows, out, tests,
                          draws=a.draws)
    v = {bn: rc.sign(p1["pb"][bn]["diffs"]["dBrier"]) for bn in p1["pb"]}
    success = v["league"] == "below" and v["season_avg"] == "below"
    p1["verdict"] = {"ladder_dBrier_sign": v, "success": success}
    out(f"\n   PRIMARY VERDICT: ladder dBrier vs league {v['league']}, season_avg {v['season_avg']}, "
        f"close {v['close']} -> {'SUCCESS' if success else 'NOT both naive baselines'}")

    # cuts (not verdicts)
    cuts = {}
    for cname, keep in (("REG only", lambda i: games[i]["game_type"] == "REG"),
                        ("2001-2012", lambda i: games[i]["season"] <= 2012),
                        ("2013-2025", lambda i: games[i]["season"] >= 2013)):
        cuts[cname] = {}
        for bname in ("league", "season_avg", "close"):
            rows = [{"game": games[i]["game_id"], "stat": "total", "line": L,
                     "y": 1.0 if games[i]["home_score"] + games[i]["away_score"] > L else 0.0,
                     "m": mprob[i][L], "k": bprob[bname](i, L)} for i in P if keep(i) for L in LADDER]
            pop = rc.Pop(cname, rows, "m", "k")
            r = pop.boot(lambda ix, pop=pop: rc.brier(pop.m[ix], pop.y[ix]) - rc.brier(pop.k[ix], pop.y[ix]),
                         draws=min(a.draws, 1000))
            cuts[cname][bname] = r
            out(f"   cut {cname:<9} ladder dBrier vs {bname:<10} {rc.fmt(r)} -> {rc.sign(r)}")
    per_season = {}
    for y in range(SCORE_FROM, SCORE_TO + 1):
        row = []
        per_season[y] = {}
        for bname in ("league", "season_avg", "close"):
            rows = [{"game": games[i]["game_id"], "stat": "total", "line": L,
                     "y": 1.0 if games[i]["home_score"] + games[i]["away_score"] > L else 0.0,
                     "m": mprob[i][L], "k": bprob[bname](i, L)} for i in P if games[i]["season"] == y
                    for L in LADDER]
            pop = rc.Pop(str(y), rows, "m", "k")
            r = pop.boot(lambda ix, pop=pop: rc.brier(pop.m[ix], pop.y[ix]) - rc.brier(pop.k[ix], pop.y[ix]),
                         draws=min(a.draws, 1000))
            per_season[y][bname] = r
            row.append(f"{bname} {r['est']:+.4f} {rc.sign(r)}")
        out(f"   {y}: " + " | ".join(row))
    counts = {bn: {s: sum(1 for y in per_season if rc.sign(per_season[y][bn]) == s)
                   for s in ("below", "contains 0", "above")} for bn in ("league", "season_avg", "close")}
    out(f"   per-season interval counts: {counts}")
    p1["cuts"], p1["per_season"], p1["per_season_counts"] = cuts, per_season, counts
    result["part1"] = p1

    # ---------------------------------------------------------------- diagnostics
    out("\n######## DIAGNOSTICS - the decomposition")
    diag = {}
    # D1 / D2 on team-seasons, REG
    ts = defaultdict(lambda: {"early": [], "late": [], "all": []})
    for i, g in enumerate(games):
        if not (SCORE_FROM <= g["season"] <= SCORE_TO) or g["game_type"] != "REG" or not GF.scored(g):
            continue
        h, aw = G.franchise(g["home"]), G.franchise(g["away"])
        ph, pa_ = pace.get((g["game_id"], h)), pace.get((g["game_id"], aw))
        if not ph or not pa_:
            continue
        for t, pl_, pts, opl, opts in ((h, ph, g["home_score"], pa_, g["away_score"]),
                                       (aw, pa_, g["away_score"], ph, g["home_score"])):
            rec = (pl_, pts, opl, opts)
            ts[(t, g["season"])]["all"].append(rec)
            ts[(t, g["season"])]["early" if g["week"] <= 8 else "late"].append(rec)

    def agg(recs):
        a_ = np.array(recs, float)
        return {"off_plays": a_[:, 0].mean(), "def_plays": a_[:, 2].mean(),
                "off_ppp": a_[:, 1].sum() / a_[:, 0].sum(), "def_ppp": a_[:, 3].sum() / a_[:, 2].sum(),
                "ppg": a_[:, 1].mean()}
    halves = [(agg(v["early"]), agg(v["late"])) for v in ts.values()
              if len(v["early"]) >= 4 and len(v["late"]) >= 4]
    d1 = {}
    for f_ in ("off_plays", "def_plays", "off_ppp", "def_ppp", "ppg"):
        d1[f_] = fisher_r([e[f_] for e, _l in halves], [l_[f_] for _e, l_ in halves])
        out(f"   D1 split-half r {f_:<9} {d1[f_]['r']:+.3f} [{d1[f_]['lo']:+.3f}, {d1[f_]['hi']:+.3f}]  n {d1[f_]['n']}")
    d1["P1_holds"] = d1["off_plays"]["r"] > d1["off_ppp"]["r"]
    out(f"   P1 (off_plays r > off_ppp r): {d1['P1_holds']}")
    diag["D1"] = d1
    full = [agg(v["all"]) for v in ts.values() if len(v["all"]) >= 8]
    lp = np.log([x["ppg"] for x in full])
    lpl = np.log([x["off_plays"] for x in full])
    lq = np.log([x["off_ppp"] for x in full])
    vt = lp.var(ddof=1)
    d2 = {"n_team_seasons": len(full), "var_log_ppg": float(vt),
          "share_plays": float(lpl.var(ddof=1) / vt), "share_ppp": float(lq.var(ddof=1) / vt),
          "share_2cov": float(2 * np.cov(lpl, lq)[0, 1] / vt),
          "sd_plays_per_game": float(np.std([x["off_plays"] for x in full], ddof=1)),
          "sd_ppp": float(np.std([x["off_ppp"] for x in full], ddof=1)),
          "mean_plays_per_game": float(np.mean([x["off_plays"] for x in full])),
          "mean_ppp": float(np.mean([x["off_ppp"] for x in full]))}
    d2["P2_holds"] = d2["share_ppp"] > d2["share_plays"]
    out(f"   D2 var(log PPG) shares over {d2['n_team_seasons']} team-seasons: plays {d2['share_plays']:.3f}, "
        f"ppp {d2['share_ppp']:.3f}, 2cov {d2['share_2cov']:+.3f}  -> P2 {d2['P2_holds']}")
    out(f"      team plays/g mean {d2['mean_plays_per_game']:.1f} sd {d2['sd_plays_per_game']:.2f}; "
        f"ppp mean {d2['mean_ppp']:.4f} sd {d2['sd_ppp']:.4f}")
    diag["D2"] = d2
    d3 = {}
    for arm in ("PLAYS_ONLY", "PPP_ONLY", "NO_SCRIPT", "NO_WIND"):
        mu_arm = np.array([mean_of(arm, stacks, feat, games, i) for i in P])
        d3[arm] = GF.mse_compare(f"D3 ablation: FULL vs {arm}", ytot, mu_full, mu_arm, blocks, out, tests,
                                 draws=a.draws)
    d3["P3_holds"] = abs(d3["PPP_ONLY"]["dMSE"]["est"]) < abs(d3["PLAYS_ONLY"]["dMSE"]["est"])
    out(f"   P3 (|FULL-PPP_ONLY| < |FULL-PLAYS_ONLY|): {d3['P3_holds']}")
    diag["D3"] = d3
    # D4 wind
    X = np.array([design("FULL", feat[i]["raw"], feat[i]["eam"], feat[i]["wx"]) for i in P])
    beta_all, _res = GT.ols(X, ytot)
    d4i = boot_rows(lambda ix: float(GT.ols(X[ix], ytot[ix])[0][3]), len(P), a.draws)
    out(f"   D4(i) wind_out coefficient, full stack pooled {SCORE_FROM}-{SCORE_TO}: {rc.fmt(d4i)} points per mph")
    tests.append(("D4 wind", "coef full stack", d4i))
    Xc = np.array([[1.0, *feat[i]["wx"]] for i in P])
    rc_ = ytot - bmean["close"]
    d4ii = boot_rows(lambda ix: float(GT.ols(Xc[ix], rc_[ix])[0][1]), len(P), a.draws)
    out(f"   D4(ii) wind_out coefficient on (total - total_line): {rc.fmt(d4ii)} points per mph")
    tests.append(("D4 wind", "coef vs close residual", d4ii))
    wo = np.array([feat[i]["wx"][0] for i in P])
    nwind = int((wo > 0).sum())
    bins = {}
    for lo_, hi_ in ((0.01, 5), (5, 10), (10, 15), (15, 20), (20, 99)):
        mk = (wo >= lo_) & (wo < hi_)
        if mk.sum():
            bins[f"{lo_:g}-{hi_:g}"] = {"n": int(mk.sum()), "mean_total_minus_close": float(rc_[mk].mean()),
                                        "mean_total": float(ytot[mk].mean()),
                                        "mean_close": float(bmean["close"][mk].mean())}
    out(f"   wind recorded (>0 mph) on {nwind} of {len(P)} games; by band (total - close): " +
        "; ".join(f"{k} n {v['n']} {v['mean_total_minus_close']:+.2f}" for k, v in bins.items()))
    diag["D4"] = {"i_full_stack": d4i, "ii_vs_close": d4ii, "pooled_beta_full": beta_all.tolist(),
                  "bands": bins, "n_wind_recorded": nwind}
    d5 = p1["pb"]["league"]["diffs"]["dDSC"]
    out(f"   D5 ladder dDSC model - league {rc.fmt(d5)}  (c-27's decomposition: +0.0004)")
    diag["D5"] = {"dDSC_vs_league": d5, "c27": 0.0004}
    result["diagnostics"] = diag

    # ---------------------------------------------------------------- Part 2
    out(f"\n######## PART 2 - against the price (SECONDARY)")
    kg = [i for i, g in enumerate(games) if g["season"] == KALSHI_SEASON and g["week"] in GF.KALSHI_WEEKS
          and g["game_type"] == "REG" and GF.scored(g) and i in feat]
    kfc = {games[i]["game_id"]: total_fc(i) for i in kg}
    kidx = {games[i]["game_id"]: i for i in kg}
    closes, census = GF.kalshi_closes(con, by_id)
    krows, klrows = [], []
    for c in closes:
        if c["series"] != "KXNFLTOTAL" or c["game_id"] not in kfc or c["line"] is None:
            continue
        L = float(c["line"])
        if abs(L - math.floor(L) - 0.5) > 1e-9:
            continue
        g = by_id[c["game_id"]]
        y_ = 1.0 if g["home_score"] + g["away_score"] > L else 0.0
        mp = kfc[c["game_id"]].prob_total_over(L)
        krows.append({"game": c["game_id"], "stat": "total", "line": L, "y": y_, "m": mp, "k": c["mid"]})
        i = kidx[c["game_id"]]
        klrows.append({"game": c["game_id"], "stat": "total", "line": L, "y": y_, "m": mp,
                       "k": float(norm.sf(L, tot[i][0], tot[i][1]))})
    out(f"   KXNFLTOTAL census {json.dumps(census.get('KXNFLTOTAL', {}))}; scored rungs {len(krows)} "
        f"over {len({r['game'] for r in krows})} games")
    p2 = {}
    if len({r["game"] for r in krows}) >= 2:
        # power first: the MDE of dBrier on this population, before any difference is read
        pop = rc.Pop("S1 power", krows, "m", "k")
        pw = pop.boot(lambda ix: rc.brier(pop.m[ix], pop.y[ix]) - rc.brier(pop.k[ix], pop.y[ix]), draws=a.draws)
        out(f"   S1 POWER: dBrier bootstrap SE {pw['se']:.4f} -> MDE (2.8 SE) {2.8 * pw['se']:.4f}")
        p2["S1_power"] = {"se": pw["se"], "mde": 2.8 * pw["se"], "rungs": len(krows), "games": pop.games}
        p2["S1 Kalshi"] = GF.compare("S1 KXNFLTOTAL close: model vs Kalshi mid", krows, out, tests,
                                     within_line=True, draws=a.draws)
        popl = rc.Pop("S1 league", klrows, "m", "k")
        rl = popl.boot(lambda ix: rc.brier(popl.m[ix], popl.y[ix]) - rc.brier(popl.k[ix], popl.y[ix]),
                       draws=a.draws)
        out(f"   S1 model vs c-28 league on the same rungs: dBrier {rc.fmt(rl)} -> {rc.sign(rl)}")
        tests.append(("S1 vs league", "dBrier", rl))
        p2["S1 vs league"] = rl
    else:
        out("   S1: fewer than 2 games - not scored")
    # S2 book close + S3 ROI
    brows, bets = [], []
    for i in P:
        g = games[i]
        if g["season"] < BOOK_FROM:
            continue
        L, oo, uo = tl[g["game_id"]]
        if oo is None or uo is None:
            continue
        io, iu = GF.american(oo), GF.american(uo)
        po = io / (io + iu)
        T_ = g["home_score"] + g["away_score"]
        mp = tfc[i].prob_over_push_void(float(L))
        if T_ != L:
            brows.append({"game": g["game_id"], "stat": "over_close", "line": 0.0,
                          "y": 1.0 if T_ > L else 0.0, "m": mp, "k": po})
        if abs(mp - po) >= EDGE:
            over = mp > po
            if T_ == L:
                prof = 0.0
            elif (T_ > L) == over:
                prof = american_profit(oo if over else uo)
            else:
                prof = -1.0
            bets.append({"game": g["game_id"], "season": g["season"], "profit": prof, "over": over})
    out(f"   S2 book close {BOOK_FROM}-{SCORE_TO}: {len(brows):,} games (pushes out)")
    p2["S2 book"] = GF.compare(f"S2 over the book close: model vs de-vigged book", brows, out, tests,
                               draws=a.draws)
    s3 = {}
    for nm, keep in (("pooled", lambda b_: True), ("2006-2015", lambda b_: b_["season"] <= 2015),
                     ("2016-2025", lambda b_: b_["season"] >= 2016)):
        bb = [b_ for b_ in bets if keep(b_)]
        prof = np.array([b_["profit"] for b_ in bb])
        r = boot_rows(lambda ix: float(prof[ix].mean()), len(bb), a.draws)
        r["over_share"] = float(np.mean([b_["over"] for b_ in bb]))
        s3[nm] = r
        out(f"   S3 ROI {nm:<9} bets {len(bb):,} (over {r['over_share']:.2f}): {rc.fmt(r)} -> {rc.sign(r)}")
        tests.append(("S3 ROI", nm, r))
    s3["honest_positive"] = all(rc.sign(s3[k]) == "above" for k in ("pooled", "2006-2015", "2016-2025"))
    out(f"   S3 honest positive: {s3['honest_positive']}")
    p2["S3"] = s3
    beats = rc.sign(p2["S1 Kalshi"]["diffs"]["dBrier"]) == "below" if "S1 Kalshi" in p2 else False
    p2["cost"] = "REQUIRED - filed, not run" if beats else "not required: no Kalshi dBrier interval below zero"
    out(f"   COST: {p2['cost']}")
    result["part2"] = p2
    con.close()

    n_ex = sum(1 for _n, _s, r in tests if rc.sign(r) in ("below", "above"))
    result["registered_intervals"] = {"count": len(tests), "exclude_zero": n_ex,
                                      "list": [(n, s, rc.sign(r)) for n, s, r in tests]}
    out(f"\n   registered intervals: {len(tests)}, {n_ex} exclude zero; no multiplicity correction")
    result["log"] = lines
    os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
