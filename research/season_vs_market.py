"""c-25 - the season model meets a market for the first time.

    python -m research.season_vs_market --db D:/calibrated-sports/data/market_log.db \
        --published D:/calibrated-sports/data/web_export/season/nfl \
        --series-raw D:/temp/c25/series_raw.json \
        --json-out research/results/season_vs_market.json \
        --register research/registers/c25_forward_calls.json

PRE-REGISTRATION: docs/C25-season-model-meets-market-preregistration.md,
committed at ecb3048 BEFORE this script existed. This file implements it; it
does not extend it. Anything added after is marked POST HOC where it prints.

What it is. a-41/a-55's season model (margin-of-victory Elo, per-simulation
rating draws, the real remaining schedule) priced on Kalshi's season-future
rungs at three as-of instants, against the exchange mid. Nothing here has
settled, so nothing here scores the model against the exchange: the settled
evidence is the model's own walk-forward 2002-2025 (P1), the comparison is a
disagreement map (P2), and the output that matters is a frozen forward
shortlist (P3) with the power January will have to score it (P4).

The statistics are c-24's (`research.ranking_calibration`: brier, auc, corp,
Pop.boot), unchanged, blocked on SEASON for the walk-forward (32 final records
in one season are zero-sum) and on TEAM for anything market-side.

market_log.db is opened mode=ro only. Writes: the results JSON and the
register file named on the command line, nothing else. The simulation's
output is not changed: WINSWEEK and WINSTREAK prices are second queries on the
per-game result matrix `models.season.simulate` already returns.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fees                                      # noqa: E402
from jobs import season_model as J                          # noqa: E402
from jobs import season_projection as P                     # noqa: E402
from models import season as M                              # noqa: E402
from research import ranking_calibration as RC              # noqa: E402

YEAR = 2026
N_CUR = 20000
N_WALK = P.N_PROJ_WALK          # 1000 - a-55's walk-forward, reproduced exactly
N_BOOT = 2000
N_POWER = 2000
CONTRACTS = 100
MAX_STALE = 660.0
STATES = (1, 2, 3)
T_OF = {1: dt.datetime(2026, 9, 16, 16, tzinfo=dt.timezone.utc).timestamp(),
        2: dt.datetime(2026, 9, 23, 16, tzinfo=dt.timezone.utc).timestamp(),
        3: dt.datetime(2026, 9, 29, 16, tzinfo=dt.timezone.utc).timestamp()}
WEEK_N = (4, 8, 12)
KALSHI_TO_NFLVERSE = {"JAC": "JAX", "LAR": "LA"}
DIV_OF = {"AFCEAST": "AFC East", "AFCNORTH": "AFC North", "AFCSOUTH": "AFC South",
          "AFCWEST": "AFC West", "NFCEAST": "NFC East", "NFCNORTH": "NFC North",
          "NFCSOUTH": "NFC South", "NFCWEST": "NFC West"}
FAMILIES = ("WINS", "WINSWEEK", "DIVISION")

RE_WINS = re.compile(r"^KXNFLWINS-27([A-Z]+)-(\d+)$")
RE_WEEK = re.compile(r"^KXNFLWINSWEEK-26W(\d+)-([A-Z]+?)(\d+)$")
RE_DIV = re.compile(r"^KXNFL(AFC|NFC)(EAST|NORTH|SOUTH|WEST)-27-([A-Z]+)$")
RE_STREAK = re.compile(r"^KXNFLWINSTREAK-27-(\d+)$")

LOG = []


def out(s=""):
    print(s)
    LOG.append(s)


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def team(code):
    return KALSHI_TO_NFLVERSE.get(code, code)


# =============================================================================
# pure helpers (tested)
# =============================================================================

def whole_wins(season, R, max_week=None):
    """(n x T) WHOLE wins - a tie is not a win on a Kalshi rung. `max_week`
    restricts to games with schedule week <= max_week."""
    mask = np.ones(season.G, bool) if max_week is None else \
        np.array([g[2] <= max_week for g in season.games])
    H, A = season.H[mask], season.A[mask]
    Rm = R[:, mask]
    return (Rm == 1.0).astype(float) @ H + (Rm == 0.0).astype(float) @ A


def games_by_team(season, max_week=None):
    mask = np.ones(season.G, bool) if max_week is None else \
        np.array([g[2] <= max_week for g in season.games])
    return (season.H[mask] + season.A[mask]).sum(axis=0)


def longest_streak(res):
    """res (n x g) 0/1/0.5 results in week order -> (n,) longest run of 1s.
    A tie breaks a streak."""
    n, g = res.shape
    cur = np.zeros(n)
    best = np.zeros(n)
    for c in range(g):
        cur = np.where(res[:, c] == 1.0, cur + 1, 0)
        best = np.maximum(best, cur)
    return best


def league_streak(season, R):
    best = np.zeros(R.shape[0])
    for t in season.teams:
        _gi, res = M.team_results(season, R, t)
        best = np.maximum(best, longest_streak(res))
    return best


def decided(cur_whole, remaining, k):
    """None if undecided, else the known answer to 'at least k'."""
    if cur_whole >= k:
        return 1
    if cur_whole + remaining < k:
        return 0
    return None


def coinflip_survival(cur_whole, remaining, k):
    """P(cur + Binomial(remaining, 0.5) >= k)."""
    from math import comb
    need = k - cur_whole
    if need <= 0:
        return 1.0
    if need > remaining:
        return 0.0
    return sum(comb(remaining, i) for i in range(need, remaining + 1)) / 2.0 ** remaining


def band_for(bands, p):
    """The reliability band whose bin holds p. bands: list of dicts with 'bin'
    [lo, hi) (the last bin closed) and 'interval' [lo, hi]."""
    for b in bands:
        lo, hi = b["bin"]
        if lo <= p < hi or (hi >= 1.0 and p == 1.0):
            return b
    return None


def clears(direction, band, bid, ask, contracts=CONTRACTS, multiplier=1):
    """Pre-registered (b): the model-direction trade, valued at the band's
    CONSERVATIVE edge, net of the taker fee on a `contracts` order.
    -> (clears, edge_at_band_edge per contract)."""
    lo, hi = band["interval"]
    if direction == "yes":
        fee = fees.fee_per_contract(ask, contracts, "taker", multiplier)
        e = lo - ask - fee
    else:
        price = round(1.0 - bid, 6)
        fee = fees.fee_per_contract(price, contracts, "taker", multiplier)
        e = (1.0 - hi) - price - fee
    return e > 0, e


def reliability(p, y, blocks, bins=10, n_boot=N_BOOT, seed=25):
    """Decile-bin realised rate with a block-bootstrap 95% interval."""
    keys = sorted(set(blocks))
    bix = {k: i for i, k in enumerate(keys)}
    bi = np.array([bix[b] for b in blocks])
    out_ = []
    edges = np.linspace(0, 1, bins + 1)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    for j in range(bins):
        lo, hi = edges[j], edges[j + 1]
        m = (p >= lo) & ((p < hi) if j < bins - 1 else (p <= hi))
        if not m.any():
            continue
        sy = np.bincount(bi[m], weights=y[m], minlength=len(keys))
        sn = np.bincount(bi[m], minlength=len(keys)).astype(float)
        num = sy[draws].sum(1)
        den = sn[draws].sum(1)
        ok = den > 0
        r = num[ok] / den[ok]
        out_.append({"bin": [float(lo), float(hi)], "n": int(m.sum()),
                     "n_blocks": int((sn > 0).sum()), "forecast": float(p[m].mean()),
                     "realised": float(y[m].mean()),
                     "interval": [float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))]})
    return out_


def crossing(pts):
    """[(k, P(W >= k))] sorted by k -> the k at which the survival curve
    crosses 0.5, linearly interpolated between rungs. None if it never does."""
    pts = sorted(pts)
    for (k0, p0), (k1, p1) in zip(pts, pts[1:]):
        if p0 >= 0.5 > p1:
            return k0 + (p0 - 0.5) / (p0 - p1) * (k1 - k0)
    return None


def pop_from(p, k, y, blocks, name):
    rows = [{"m": float(a), "k": float(b), "y": float(c), "stat": "w", "line": 0.0, "game": g}
            for a, b, c, g in zip(p, k, y, blocks)]
    return RC.Pop(name, rows, "m", "k")


def slope(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 3 or x.std() == 0:
        return None
    return float(np.polyfit(x, y, 1)[0])


def team_boot(rows, fn, n_boot=N_BOOT, seed=251):
    """rows carry 'team'; fn(list_of_rows) -> float. Team-block bootstrap."""
    by = defaultdict(list)
    for r in rows:
        by[r["team"]].append(r)
    keys = sorted(by)
    est = fn(rows)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        v = fn([r for j in pick for r in by[keys[j]]])
        if v is not None:
            vals.append(v)
    if est is None or not vals:
        return {"est": est, "lo": None, "hi": None, "n_blocks": len(keys), "n": len(rows)}
    return {"est": est, "lo": float(np.percentile(vals, 2.5)),
            "hi": float(np.percentile(vals, 97.5)), "n_blocks": len(keys), "n": len(rows)}


def fmt_iv(d, nd=4):
    if d.get("est") is None:
        return "n/a"
    if d.get("lo") is None:
        return f"{d['est']:+.{nd}f} [no interval]"
    return f"{d['est']:+.{nd}f} [{d['lo']:+.{nd}f}, {d['hi']:+.{nd}f}] n_blocks {d['n_blocks']}"


# =============================================================================
# store
# =============================================================================

def ro(path):
    return sqlite3.connect("file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro",
                           uri=True, timeout=5)


def future_markets(con):
    return [r for r in con.execute(
        "SELECT market_id, event_id, title, close_ts FROM markets WHERE venue='kalshi' AND "
        "(market_id LIKE 'KXNFLWINS%' OR market_id LIKE 'KXNFLAFC%' OR market_id LIKE 'KXNFLNFC%')")]


def census(con, mkts):
    agg = defaultdict(Counter)
    span = [None, None]
    for mid, _e, _t, _c in mkts:
        fam = mid.split("-")[0]
        if RE_DIV.match(mid):
            fam = "DIVISION"
        q = con.execute("SELECT count(*), sum(best_bid>0 AND best_ask<1 AND best_ask>best_bid), "
                        "min(ts), max(ts) FROM quotes WHERE venue='kalshi' AND market_id=?",
                        (mid,)).fetchone()
        d = con.execute("SELECT count(*) FROM market_depth WHERE venue='kalshi' AND market_id=?",
                        (mid,)).fetchone()[0]
        t = con.execute("SELECT count(*) FROM market_trades WHERE venue='kalshi' AND market_id=?",
                        (mid,)).fetchone()[0]
        a = agg[fam]
        a["markets"] += 1
        a["quoted"] += q[0] > 0
        a["two_sided_ever"] += (q[1] or 0) > 0
        a["quote_rows"] += q[0]
        a["with_depth"] += d > 0
        a["with_trades"] += t > 0
        if q[2] is not None:
            span[0] = q[2] if span[0] is None else min(span[0], q[2])
            span[1] = q[3] if span[1] is None else max(span[1], q[3])
    return {k: dict(v) for k, v in agg.items()}, span


def quote_at(con, mid, T):
    r = con.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' AND "
                    "market_id=? AND side='yes' AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
                    (mid, T, T - MAX_STALE)).fetchone()
    if not r:
        return None
    ts, bid, ask = r
    if bid is None or ask is None or not (0 < bid < ask < 1):
        return {"ts": ts, "bid": bid, "ask": ask, "two_sided": False}
    return {"ts": ts, "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "two_sided": True}


# =============================================================================
# the model at a 2026 state
# =============================================================================

def state_model(games, groupings, params, ratings_at, sigma, k, n=N_CUR):
    s = J.season_at(games, groupings, YEAR, k)
    tag = J.rng_for("proj-current", YEAR) if k == 3 else J.rng_for("proj-current", YEAR, k)
    draws = M.rating_draws(s, ratings_at, sigma, n, tag)
    R = M.simulate(s, M.p_home_draws(s, draws, params.hfa), n, tag)
    # the division model as published: FIXED ratings, its own rng
    dtag = J.rng_for("cur-model", YEAR) if k == 3 else J.rng_for("cur-model", YEAR, k)
    div_fixed = M.division_probs(s, J.p_home_for(s, ratings_at, params), n, dtag)
    return s, R, div_fixed


def check_published(s, R, div_fixed, published):
    W = M.final_wins(s, R)
    pub = {t["team"]: t for t in published["projection"]["teams"]}
    worst = 0.0
    for t in s.teams:
        m = float(W[:, s.idx[t]].mean())
        worst = max(worst, abs(m - pub[t]["projection"]["mean"]))
    # CHANGED after the first run (disclosed in the findings): an exact match was
    # demanded first, and it failed by up to 0.0033. main's own `J.current()`
    # on the same store reproduces THIS function's numbers, not the file's, with
    # identical ratings and identical games - so the file came from a different
    # Monte Carlo stream for a reason not determined here. Two independent
    # 20,000-sim estimates differ by sqrt(2) x mc_se in SD, so the check is
    # every team within 3 x sqrt(2) x its published mc_se.
    dpub = {x["team"]: (x["p"], x["mc_se"]) for d in published["division"]["divisions"]
            for x in d["teams"]}
    dworst = max(abs(div_fixed[s.division[t]][t] - dpub[t][0]) for t in s.teams)
    dz = max(abs(div_fixed[s.division[t]][t] - dpub[t][0]) / max(dpub[t][1], 1e-4)
             / np.sqrt(2) for t in s.teams)
    return worst, dworst, dz


# =============================================================================
# P1 - the walk-forward, reproduced at states 1..3 (and the W-N queries)
# =============================================================================

def walk_rows(games, groupings, losses, sigma_by, published_by_week, log=out):
    t0 = time.time()
    rows_cov = []            # a-55 coverage rows (ties half, as published)
    wins_rungs = []          # (season, state, team, p, coin, y)
    week_rungs = []          # (season, state, N, team, p, coin, y)
    zero_one = Counter()
    for year in range(J.SCORE_FROM, J.SCORE_TO + 1):
        params, _, _ = J.best_params(losses, year)
        sigma = sigma_by[year]
        _, snaps = M.run_elo(games, params, snapshot_at=[(year, k) for k in STATES])
        final = J.season_at(games, groupings, year, 99)
        R_true = np.array([[np.nan if g[3] is None else g[3] for g in final.games]])
        true_whole = whole_wins(final, R_true)[0]
        rec = M.standings(final)
        actual_half = np.array([rec[t][0] + 0.5 * rec[t][2] for t in final.teams])
        true_week = {N: whole_wins(final, R_true, N)[0] for N in WEEK_N}
        for k in STATES:
            s, R = P._state_sims(J, games, groupings, year, k, params, snaps[(year, k)],
                                 sigma, N_WALK, "proj-walk")
            W = M.final_wins(s, R)
            for t in s.teams:
                j = s.idx[t]
                w = W[:, j]
                row = {"season": year, "state": k, "actual": float(actual_half[j]),
                       "model": float(w.mean())}
                lo, hi = np.quantile(w, [0.10, 0.90])
                row["in80"] = bool(lo <= actual_half[j] <= hi)
                row["mass80"] = float(np.mean((w >= lo) & (w <= hi)))
                rows_cov.append(row)
            # Kalshi-shaped rungs: whole wins
            Ww = whole_wins(s, R)
            R0 = np.array([[np.nan if g[3] is None else g[3] for g in s.games]])
            cur = np.nan_to_num(whole_wins(s, np.where(np.isnan(R0), -1, R0))[0])
            gp_all = games_by_team(s)
            played = np.array([g[3] is not None for g in s.games])
            gp_now = (s.H[played] + s.A[played]).sum(axis=0)
            for t in s.teams:
                j = s.idx[t]
                rem = int(gp_all[j] - gp_now[j])
                for kk in range(1, int(gp_all[j]) + 1):
                    if decided(cur[j], rem, kk) is not None:
                        continue
                    p = float(np.mean(Ww[:, j] >= kk))
                    if p in (0.0, 1.0):
                        zero_one["wins"] += 1
                        continue
                    wins_rungs.append((year, k, t, p, coinflip_survival(int(cur[j]), rem, kk),
                                       float(true_whole[j] >= kk)))
            for N in WEEK_N:
                if N <= k:
                    continue
                WN = whole_wins(s, R, N)
                gN = games_by_team(s, N)
                playedN = played & np.array([g[2] <= N for g in s.games])
                gpN = (s.H[playedN] + s.A[playedN]).sum(axis=0)
                for t in s.teams:
                    j = s.idx[t]
                    rem = int(gN[j] - gpN[j])
                    for kk in range(1, int(gN[j]) + 1):
                        if decided(cur[j], rem, kk) is not None:
                            continue
                        p = float(np.mean(WN[:, j] >= kk))
                        if p in (0.0, 1.0):
                            zero_one["week"] += 1
                            continue
                        week_rungs.append((year, k, N, t, p,
                                           coinflip_survival(int(cur[j]), rem, kk),
                                           float(true_week[N][j] >= kk)))
    log(f"  walk-forward states {STATES}, {J.SCORE_FROM}-{J.SCORE_TO}: {time.time() - t0:.0f}s")
    # reproduction check against the published record
    rep = {}
    for k in STATES:
        rs = [r for r in rows_cov if r["state"] == k]
        rmse = float(np.sqrt(np.mean([(r["model"] - r["actual"]) ** 2 for r in rs])))
        pub = next(b for b in published_by_week if b["after_week"] == k)
        rep[k] = (rmse, pub["model"], float(np.mean([r["in80"] for r in rs])), pub["coverage80"])
    return rows_cov, wins_rungs, week_rungs, zero_one, rep


def coverage_gate(rows_cov, k, n_boot=N_BOOT, seed=56):
    rs = [r for r in rows_cov if r["state"] == k]
    keys = sorted({r["season"] for r in rs})
    ix = {kk: i for i, kk in enumerate(keys)}
    hit = np.zeros(len(keys))
    mass = np.zeros(len(keys))
    c = np.zeros(len(keys))
    for r in rs:
        j = ix[r["season"]]
        hit[j] += r["in80"]
        mass[j] += r["mass80"]
        c[j] += 1
    rng = np.random.default_rng(seed)
    d = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    diff = (hit[d].sum(1) - mass[d].sum(1)) / c[d].sum(1)
    lo, hi = np.percentile(diff, [2.5, 97.5])
    verdict = "too_narrow" if hi < 0 else ("too_wide" if lo > 0 else "within")
    return {"state": k, "realised": float(hit.sum() / c.sum()), "mass": float(mass.sum() / c.sum()),
            "diff": float((hit.sum() - mass.sum()) / c.sum()), "interval": [float(lo), float(hi)],
            "n_blocks": len(keys), "n": int(c.sum()), "verdict": verdict,
            "interpretable": verdict != "too_narrow"}


def corp_block(p, k, y, blocks, name):
    pop = pop_from(p, k, y, blocks, name)
    m, kk, yy = pop.m, pop.k, pop.y
    res = {"n": pop.n, "n_blocks": pop.games, "realised": float(yy.mean()),
           "corp_model": RC.corp(m, yy), "corp_coinflip": RC.corp(kk, yy),
           "auc_model": RC.auc(m, yy), "auc_coinflip": RC.auc(kk, yy)}
    res["brier_diff"] = pop.boot(lambda i: RC.brier(m[i], yy[i]) - RC.brier(kk[i], yy[i]))
    res["mcb_model"] = pop.boot(lambda i: RC.corp(m[i], yy[i])["mcb"])
    res["dsc_model"] = pop.boot(lambda i: RC.corp(m[i], yy[i])["dsc"])
    res["auc_diff"] = pop.boot(lambda i: (RC.auc(m[i], yy[i]) or 0) - (RC.auc(kk[i], yy[i]) or 0))
    return res


# =============================================================================
# main
# =============================================================================

def git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--published", required=True,
                    help="directory holding season/nfl projection.json and division.json")
    ap.add_argument("--series-raw", required=True, help="the /series responses, saved")
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--register", required=True)
    a = ap.parse_args(argv)

    published = {"projection": json.load(open(os.path.join(a.published, "projection.json"))),
                 "division": json.load(open(os.path.join(a.published, "division.json")))}
    series_raw = json.load(open(a.series_raw))
    result = {"unit": "c-25", "preregistration": "docs/C25-season-model-meets-market-preregistration.md",
              "prereg_commit": "ecb3048", "script_commit": git_head(),
              "published_generated_at": {k: v["generated_at"] for k, v in published.items()}}

    con = ro(a.db)
    # ---------------------------------------------------------------- P0
    out("== P0 census (market_log.db, mode=ro)")
    mkts = future_markets(con)
    cen, span = census(con, mkts)
    for fam, v in sorted(cen.items()):
        out(f"  {fam:<16} {v}")
    out(f"  quote span {iso(span[0])} -> {iso(span[1])}")
    settled = sum(1 for m in mkts if con.execute(
        "SELECT result FROM markets WHERE venue='kalshi' AND market_id=?", (m[0],)).fetchone()[0])
    out(f"  markets with a result: {settled}")
    result["census"] = {"families": cen, "span": [span[0], span[1]], "settled": settled}
    fee_rows = {}
    for s_, v in series_raw.items():
        b = v["body"].get("series", {}) if isinstance(v["body"], dict) else {}
        fee_rows[s_] = {"fee_type": b.get("fee_type"), "fee_multiplier": b.get("fee_multiplier"),
                        "fetched_ts": v["fetched_ts"]}
        out(f"  /series {s_:<16} fee_type {b.get('fee_type')}  multiplier {b.get('fee_multiplier')}")
    result["fees_from_series_endpoint"] = fee_rows

    games, groupings, versions = J.load(con)
    result["nfl_versions"] = versions
    reg = [g for g in games if g["season"] == YEAR and g["game_type"] == "REG"]
    for k in STATES:
        wk = [g for g in reg if g["week"] == k]
        nx = [g for g in reg if g["week"] == k + 1]
        assert all(g["home_score"] is not None for g in wk), f"week {k} not complete"
        assert max(g["kickoff_ts"] for g in wk) <= T_OF[k] - 4 * 3600, f"T_{k} too early"
        assert min(g["kickoff_ts"] for g in nx) > T_OF[k], f"T_{k} after week {k + 1} began"
    out("  as-of instants verified: every week-k game kicked >= 4h before T_k, none of week k+1 before")

    # ---------------------------------------------------------------- model constants
    t0 = time.time()
    losses = J.game_losses(games, J.grid())
    params, _, _ = J.best_params(losses, YEAR)
    pc = published["projection"]["method"]["constants"]
    assert (params.k, params.hfa, params.regress) == (pc["k"], pc["hfa"], pc["regress"]), \
        f"refit params {params} differ from published {pc}"
    sigma_by = {r["season"]: r["sigma"] for r in published["projection"]["record"]["sigma_by_season"]}
    sigma = published["projection"]["method"]["sigma"]["value"]
    out(f"\n== model: params {params.as_dict()} (refit = published), sigma {sigma} "
        f"(published; chosen on 2001-2025 by CRPS)  {time.time() - t0:.0f}s")
    result["model"] = {"params": params.as_dict(), "sigma": sigma, "n_sims": N_CUR}

    # ---------------------------------------------------------------- P1
    out("\n== P1 the model's own honesty at weeks 1-3 (walk-forward 2002-2025, settled)")
    rows_cov, wins_rungs, week_rungs, zero_one, rep = walk_rows(
        games, groupings, losses, sigma_by, published["projection"]["record"]["by_week"])
    for k, (mine, pub, cov, pcov) in rep.items():
        out(f"  reproduce after week {k}: RMSE {mine:.4f} vs published {pub:.4f}; "
            f"cov80 {cov:.4f} vs {pcov:.4f}")
        if abs(mine - pub) > 5e-5 or abs(cov - pcov) > 5e-5:
            raise SystemExit(f"REFUSED: walk-forward reproduction failed at week {k}")
    gates = {k: coverage_gate(rows_cov, k) for k in STATES}
    for k, g in gates.items():
        out(f"  coverage80 after week {k}: realised {g['realised']:.4f} vs mass {g['mass']:.4f}, "
            f"diff {g['diff']:+.4f} [{g['interval'][0]:+.4f}, {g['interval'][1]:+.4f}] "
            f"n_blocks {g['n_blocks']} n {g['n']} -> {g['verdict']}")
    result["P1_coverage"] = gates

    p1 = {}
    for k in STATES:
        rr = [r for r in wins_rungs if r[1] == k]
        p = np.array([r[3] for r in rr])
        c = np.array([r[4] for r in rr])
        y = np.array([r[5] for r in rr])
        b = [r[0] for r in rr]
        res = corp_block(p, c, y, b, f"WINS after week {k}")
        res["bands"] = reliability(p, y, b)
        p1[f"WINS_k{k}"] = res
        cm = res["corp_model"]
        out(f"  WINS rungs after week {k}: n {res['n']:,} seasons {res['n_blocks']}  "
            f"Brier {cm['bs']:.4f} = MCB {cm['mcb']:.4f} - DSC {cm['dsc']:.4f} + UNC {cm['unc']:.4f}; "
            f"coin-flip {res['corp_coinflip']['bs']:.4f}; model-coin {RC.fmt(res['brier_diff'])}; "
            f"AUC {res['auc_model']:.4f} vs {res['auc_coinflip']:.4f}")
    rr = week_rungs
    p = np.array([r[4] for r in rr])
    c = np.array([r[5] for r in rr])
    y = np.array([r[6] for r in rr])
    b = [r[0] for r in rr]
    res = corp_block(p, c, y, b, "WINSWEEK pooled")
    res["bands"] = reliability(p, y, b)
    res["by_N"] = {int(N): int(sum(1 for r in rr if r[2] == N)) for N in WEEK_N}
    p1["WINSWEEK"] = res
    cm = res["corp_model"]
    out(f"  WINSWEEK rungs (N 4/8/12, states < N): n {res['n']:,} seasons {res['n_blocks']}  "
        f"Brier {cm['bs']:.4f} = MCB {cm['mcb']:.4f} - DSC {cm['dsc']:.4f} + UNC {cm['unc']:.4f}; "
        f"coin-flip {res['corp_coinflip']['bs']:.4f}; model-coin {RC.fmt(res['brier_diff'])}")
    out(f"  excluded as priced exactly 0/1 from an undecided state: {dict(zero_one)}")
    div_bands = published["division"]["record"]["calibration"]
    out("  division bands: a-41's published calibration record (pooled over states, "
        "division-season blocks), not recomputed")
    for fam, bands in (("WINS_k3", p1["WINS_k3"]["bands"]), ("WINSWEEK", p1["WINSWEEK"]["bands"]),
                       ("DIVISION", div_bands)):
        out(f"  bands {fam}: " + "  ".join(
            f"[{x['bin'][0]:.1f}) f{x['forecast']:.3f} r{x['realised']:.3f} "
            f"[{x['interval'][0]:.3f},{x['interval'][1]:.3f}]" for x in bands))
    result["P1_calibration"] = {k: {kk: vv for kk, vv in v.items()} for k, v in p1.items()}
    result["P1_calibration"]["DIVISION_published_bands"] = div_bands
    result["P1_excluded_zero_one"] = dict(zero_one)

    # ---------------------------------------------------------------- the 2026 states
    out("\n== the model at the 2026 states")
    _, snaps = M.run_elo(games, params, snapshot_at=[(YEAR, k) for k in STATES])
    parsed = []
    for mid, ev, title, close_ts in mkts:
        m = RE_WINS.match(mid)
        if m:
            parsed.append({"market_id": mid, "family": "WINS", "team": team(m.group(1)),
                           "k": int(m.group(2)), "close_ts": close_ts})
            continue
        m = RE_WEEK.match(mid)
        if m:
            parsed.append({"market_id": mid, "family": "WINSWEEK", "N": int(m.group(1)),
                           "team": team(m.group(2)), "k": int(m.group(3)), "close_ts": close_ts})
            continue
        m = RE_DIV.match(mid)
        if m:
            parsed.append({"market_id": mid, "family": "DIVISION",
                           "division": DIV_OF[m.group(1) + m.group(2)], "team": team(m.group(3)),
                           "close_ts": close_ts})
            continue
        m = RE_STREAK.match(mid)
        if m:
            parsed.append({"market_id": mid, "family": "WINSTREAK", "k": int(m.group(1)),
                           "close_ts": close_ts})
            continue
    out(f"  parsed {Counter(x['family'] for x in parsed)}; conference champion excluded "
        f"(no playoff simulation): {sum(1 for x in mkts if 'CHAMP' in x[0])}")

    priced = {}          # (k, market_id) -> row
    sims = {}
    for k in STATES:
        t0 = time.time()
        s, R, div_fixed = state_model(games, groupings, params, snaps[(YEAR, k)], sigma, k)
        if k == 3:
            worst, dworst, dz = check_published(s, R, div_fixed, published)
            out(f"  k=3 against the published files: max |mean wins - published| {worst:.4f} "
                f"(published to 1 dp); max |division p - published| {dworst:.4f}, "
                f"max z over sqrt(2) mc_se {dz:.2f}")
            if worst > 0.051 or dz > 3.0:
                raise SystemExit("REFUSED: k=3 does not reproduce the published projection")
            result["reproduction_k3"] = {"mean_wins_max_abs": worst, "division_p_max_abs": dworst,
                                         "division_max_z": dz}
        W = whole_wins(s, R)
        R0 = np.array([[np.nan if g[3] is None else g[3] for g in s.games]])
        cur = np.nan_to_num(whole_wins(s, np.where(np.isnan(R0), -1, R0))[0])
        played = np.array([g[3] is not None for g in s.games])
        gp_all = games_by_team(s)
        gp_now = (s.H[played] + s.A[played]).sum(axis=0)
        WN = {N: whole_wins(s, R, N) for N in WEEK_N}
        gN = {N: games_by_team(s, N) for N in WEEK_N}
        gpN = {N: (s.H[played & np.array([g[2] <= N for g in s.games])]
                   + s.A[played & np.array([g[2] <= N for g in s.games])]).sum(axis=0)
               for N in WEEK_N}
        # secondary division: from the sigma-drawn sims (labelled)
        tab = M.Tables(s, R)
        dw = M.division_winners(tab, J.rng_for("c25-div-drawn", YEAR, k))
        streak = league_streak(s, R)
        sims[k] = {"s": s, "R": R, "W": W, "WN": WN, "dw": dw, "streak": streak,
                   "div_fixed": div_fixed}
        T = T_OF[k]
        for x in parsed:
            q = quote_at(con, x["market_id"], T)
            row = dict(x, state=k, T=T, quote=q)
            if x["family"] in ("WINS", "WINSWEEK"):
                j = s.idx[x["team"]]
                if x["family"] == "WINS":
                    rem = int(gp_all[j] - gp_now[j])
                    row["p_model"] = float(np.mean(W[:, j] >= x["k"]))
                else:
                    rem = int(gN[x["N"]][j] - gpN[x["N"]][j])
                    row["p_model"] = float(np.mean(WN[x["N"]][:, j] >= x["k"]))
                row["decided"] = decided(cur[j], rem, x["k"])
                row["cur_wins"] = int(cur[j])
            elif x["family"] == "DIVISION":
                row["p_model"] = div_fixed[x["division"]][x["team"]]
                row["p_model_drawn"] = float(np.mean(dw[x["division"]] == x["team"]))
                row["decided"] = None
            else:
                row["p_model"] = float(np.mean(streak >= x["k"]))
                row["decided"] = 1 if bool(np.all(streak >= x["k"])) else None
            priced[(k, x["market_id"])] = row
        out(f"  state {k}: T {iso(T)}  simulated {time.time() - t0:.0f}s")

    # ---------------------------------------------------------------- P2
    out("\n== P2 disagreement map (descriptive - no outcome exists)")
    p2 = {}
    for fam in FAMILIES + ("WINSTREAK",):
        for k in STATES:
            rows = [r for (kk, _m), r in priced.items() if kk == k and r["family"] == fam]
            n_all = len(rows)
            n_q = sum(1 for r in rows if r["quote"])
            dec = sum(1 for r in rows if r["decided"] is not None)
            use = [r for r in rows if r["decided"] is None and r["quote"] and r["quote"]["two_sided"]]
            if not use:
                out(f"  {fam:<9} k{k}: 0 usable (markets {n_all}, quoted {n_q}, decided {dec})")
                continue
            gap = np.array([r["p_model"] - r["quote"]["mid"] for r in use])
            pm = np.array([r["p_model"] for r in use])
            mm = np.array([r["quote"]["mid"] for r in use])
            from scipy.stats import spearmanr
            rho = float(spearmanr(pm, mm).statistic) if len(use) > 2 else None
            spr = np.array([r["quote"]["ask"] - r["quote"]["bid"] for r in use])
            d = {"markets": n_all, "quoted_at_T": n_q, "decided": dec, "usable": len(use),
                 "abs_gap_median": float(np.median(np.abs(gap))),
                 "abs_gap_p90": float(np.percentile(np.abs(gap), 90)),
                 "signed_gap_mean": float(gap.mean()), "spearman": rho,
                 "spread_median": float(np.median(spr)),
                 "teams": len({r.get("team") for r in use})}
            if fam != "WINSTREAK":
                d["signed_gap_team_boot"] = team_boot(
                    [dict(team=r["team"], g=r["p_model"] - r["quote"]["mid"]) for r in use],
                    lambda rs: float(np.mean([x["g"] for x in rs])) if rs else None)
            p2[f"{fam}_k{k}"] = d
            out(f"  {fam:<9} k{k}: usable {len(use):>3} of {n_all} (quoted {n_q}, decided {dec})  "
                f"|gap| med {d['abs_gap_median']:.3f} p90 {d['abs_gap_p90']:.3f}  "
                f"signed mean {d['signed_gap_mean']:+.4f}"
                + (f" {fmt_iv(d['signed_gap_team_boot'])}" if 'signed_gap_team_boot' in d else "")
                + f"  spearman {rho if rho is None else round(rho, 3)}  spread med {d['spread_median']:.3f}")
    # per-team mean wins, model vs market, where the ladder is complete
    team_means = {}
    for k in STATES:
        for t in sorted({r["team"] for (kk, _m), r in priced.items() if r["family"] == "WINS"}):
            lad = sorted([r for (kk, _m), r in priced.items() if kk == k and r["family"] == "WINS"
                          and r["team"] == t], key=lambda r: r["k"])
            mk = 0.0
            ok = True
            for r in lad:
                if r["decided"] is not None:
                    mk += r["decided"]
                elif r["quote"] and r["quote"]["two_sided"]:
                    mk += r["quote"]["mid"]
                else:
                    ok = False
            s = sims[k]["s"]
            team_means[(k, t)] = {"model": float(sims[k]["W"][:, s.idx[t]].mean()),
                                  "market": mk if ok and len(lad) == 17 else None,
                                  "rungs": len(lad)}
    for k in STATES:
        tm = [(t, v) for (kk, t), v in team_means.items() if kk == k]
        comp = [(t, v["model"] - v["market"]) for t, v in tm if v["market"] is not None]
        if comp:
            gaps = np.array([g for _t, g in comp])
            out(f"  mean wins model - market, k{k}: {len(comp)} of {len(tm)} teams with a complete "
                f"two-sided ladder; mean {gaps.mean():+.2f}, |gap| median {np.median(np.abs(gaps)):.2f}, "
                f"max {comp[int(np.argmax(np.abs(gaps)))][0]} {gaps[np.argmax(np.abs(gaps))]:+.2f}")
        else:
            out(f"  mean wins model - market, k{k}: 0 of {len(tm)} teams have a complete two-sided ladder")
    # POST HOC (not in the pre-registration; added after the first run showed
    # the shortlist's direction splitting by team strength): the win count at
    # which each team's ladder crosses 0.5, model against market. Where a rung
    # is decided it is 0/1; unquoted rungs are skipped, which is harmless here
    # because only the crossing is read and it sits in the quoted middle.
    med_rows = []
    for k in STATES:
        for t in sorted({r["team"] for (kk, _m), r in priced.items() if r["family"] == "WINS"}):
            lad = sorted([r for (kk, _m), r in priced.items() if kk == k and r["family"] == "WINS"
                          and r["team"] == t], key=lambda r: r["k"])
            pts_m, pts_k = [], []
            for r in lad:
                v = r["decided"] if r["decided"] is not None else (
                    r["quote"]["mid"] if r["quote"] and r["quote"]["two_sided"] else None)
                pts_m.append((r["k"], r["p_model"]))
                if v is not None:
                    pts_k.append((r["k"], float(v)))
            mm_, mk_ = crossing(pts_m), crossing(pts_k)
            if mm_ is not None and mk_ is not None:
                med_rows.append({"team": t, "k": k, "model": mm_, "market": mk_})
    disp = {}
    for k in STATES:
        rs = [r for r in med_rows if r["k"] == k]
        if len(rs) < 3:
            continue
        mo = np.array([r["model"] for r in rs])
        mk = np.array([r["market"] for r in rs])
        disp[k] = {"teams": len(rs), "sd_model": float(mo.std()), "sd_market": float(mk.std()),
                   "slope_model_on_market": team_boot(
                       [dict(team=r["team"], x=r["market"], y=r["model"]) for r in rs],
                       lambda q: slope([z["x"] for z in q], [z["y"] for z in q])),
                   "abs_gap_median": float(np.median(np.abs(mo - mk))),
                   "abs_gap_max": float(np.max(np.abs(mo - mk)))}
        out(f"  POST HOC ladder median (0.5 crossing), k{k}: teams {len(rs)}; SD across teams "
            f"model {mo.std():.2f} vs market {mk.std():.2f} wins; slope model-on-market "
            f"{fmt_iv(disp[k]['slope_model_on_market'], 3)}; |gap| median "
            f"{disp[k]['abs_gap_median']:.2f}, max {disp[k]['abs_gap_max']:.2f}")
    result["P2_posthoc_ladder_median"] = {"rows": med_rows, "by_state": disp}
    result["P2"] = p2
    result["P2_team_mean_wins"] = {f"k{k}_{t}": v for (k, t), v in team_means.items()}

    # movement: fixed rung = the team's WINS rung with mid nearest 0.5 at T_k
    mv = []
    for k in (1, 2):
        for t in sorted({r["team"] for (kk, _m), r in priced.items() if r["family"] == "WINS"}):
            cands = [r for (kk, _m), r in priced.items() if kk == k and r["family"] == "WINS"
                     and r["team"] == t and r["decided"] is None and r["quote"]
                     and r["quote"]["two_sided"]]
            if not cands:
                continue
            r0 = min(cands, key=lambda r: abs(r["quote"]["mid"] - 0.5))
            r1 = priced.get((k + 1, r0["market_id"]))
            if not r1 or not r1["quote"] or not r1["quote"]["two_sided"]:
                continue
            mv.append({"team": t, "k": k, "gap": r0["p_model"] - r0["quote"]["mid"],
                       "d_mkt": r1["quote"]["mid"] - r0["quote"]["mid"],
                       "d_model": r1["p_model"] - r0["p_model"]})
    sm = team_boot(mv, lambda rs: slope([x["gap"] for x in rs], [x["d_mkt"] for x in rs]))
    so = team_boot(mv, lambda rs: slope([x["gap"] for x in rs], [x["d_model"] for x in rs]))
    out(f"  movement (descriptive, n {len(mv)}): slope of d-market on gap {fmt_iv(sm, 3)}; "
        f"slope of d-model on gap {fmt_iv(so, 3)}")
    result["P2_movement"] = {"market_on_gap": sm, "model_on_gap": so, "n": len(mv)}

    # ---------------------------------------------------------------- P3
    out("\n== P3 forward shortlist, frozen at T_3 = " + iso(T_OF[3]))
    gate3 = gates[3]["interpretable"]
    fam_gate = {"WINS": gate3, "WINSWEEK": gate3, "DIVISION": gate3}
    bands = {"WINS": p1["WINS_k3"]["bands"], "WINSWEEK": p1["WINSWEEK"]["bands"],
             "DIVISION": div_bands}
    short, disagree_only, considered = [], [], Counter()
    for (k, _mid), r in sorted(priced.items()):
        if k != 3 or r["family"] not in FAMILIES:
            continue
        if r["decided"] is not None or not r["quote"] or not r["quote"]["two_sided"]:
            continue
        considered[r["family"]] += 1
        if not fam_gate[r["family"]]:
            continue
        b = band_for(bands[r["family"]], r["p_model"])
        if b is None:
            continue
        mid = r["quote"]["mid"]
        lo, hi = b["interval"]
        if lo <= mid <= hi:
            continue
        direction = "yes" if mid < lo else "no"
        ok, edge = clears(direction, b, r["quote"]["bid"], r["quote"]["ask"])
        rec = {"market_id": r["market_id"], "family": r["family"], "team": r["team"],
               "k": r.get("k"), "N": r.get("N"), "division": r.get("division"),
               "direction": direction, "p_model": round(r["p_model"], 4),
               "p_model_drawn": (round(r["p_model_drawn"], 4) if "p_model_drawn" in r else None),
               "band": [round(lo, 4), round(hi, 4)], "band_bin": b["bin"],
               "bid": r["quote"]["bid"], "ask": r["quote"]["ask"], "mid": round(mid, 4),
               "quote_ts": r["quote"]["ts"], "close_ts": r["close_ts"],
               "close": iso(r["close_ts"]) if r["close_ts"] else None,
               "edge_at_band_edge": round(edge, 4),
               "maker_fee_series": r["family"] == "DIVISION"}
        (short if ok else disagree_only).append(rec)
    out(f"  considered (two-sided, undecided at T_3): {dict(considered)}")
    out(f"  shortlisted (a) and (b): {len(short)}   disagree but do not clear cost: {len(disagree_only)}")
    for x in sorted(short, key=lambda x: -x["edge_at_band_edge"]):
        out(f"    {x['market_id']:<32} buy {x['direction'].upper():<3} model {x['p_model']:.3f} "
            f"band [{x['band'][0]:.3f},{x['band'][1]:.3f}] bid/ask {x['bid']:.2f}/{x['ask']:.2f} "
            f"edge@band {x['edge_at_band_edge']:+.3f}  closes {x['close']}")
    result["P3"] = {"as_of_ts": T_OF[3], "as_of": iso(T_OF[3]), "considered": dict(considered),
                    "shortlist": short, "disagree_not_cost": disagree_only,
                    "maker_arm": "not run - no market_depth and no market_trades rows exist for any "
                                 "futures market, so a maker fill cannot be simulated",
                    "size_at_touch": "unverified - no depth snapshots; 100 contracts assumed"}

    # ---------------------------------------------------------------- P4
    out("\n== P4 power at settlement, under the model (upper bound)")
    s3 = sims[3]
    rng = np.random.default_rng(2525)
    pick = rng.choice(N_CUR, N_POWER, replace=False)
    fam_rows = defaultdict(list)
    for (k, _m), r in priced.items():
        if k == 3 and r["family"] in FAMILIES and r["decided"] is None and r["quote"] \
                and r["quote"]["two_sided"]:
            fam_rows[r["family"]].append(r)

    def truth(r, i):
        s = s3["s"]
        if r["family"] == "WINS":
            return float(s3["W"][i, s.idx[r["team"]]] >= r["k"])
        if r["family"] == "WINSWEEK":
            return float(s3["WN"][r["N"]][i, s.idx[r["team"]]] >= r["k"])
        return float(s3["dw"][r["division"]][i] == r["team"])

    p4 = {}
    for fam, rows in fam_rows.items():
        pm = np.array([r["p_model"] for r in rows])
        mm = np.array([r["quote"]["mid"] for r in rows])
        diffs = []
        for i in pick:
            y = np.array([truth(r, i) for r in rows])
            diffs.append(float(np.mean((pm - y) ** 2) - np.mean((mm - y) ** 2)))
        diffs = np.array(diffs)
        teams = len({r["team"] for r in rows})
        p4[fam] = {"n_rungs": len(rows), "n_blocks": teams, "expected_diff": float(diffs.mean()),
                   "sd": float(diffs.std()), "mde": float(2.8 * diffs.std()),
                   "share_draws_model_better": float(np.mean(diffs < 0))}
        out(f"  {fam:<9} rungs {len(rows):>3} teams {teams}: E[Brier model - mid] {diffs.mean():+.4f}, "
            f"SD {diffs.std():.4f}, MDE {2.8 * diffs.std():.4f}; model better in "
            f"{np.mean(diffs < 0):.1%} of seasons drawn from the model itself")
    if short:
        def pnl(x, y):
            fee = fees.fee_per_contract(x["ask"] if x["direction"] == "yes" else round(1 - x["bid"], 6),
                                        CONTRACTS, "taker", 1)
            return (y - x["ask"] - fee) if x["direction"] == "yes" else ((1 - y) - (1 - x["bid"]) - fee)
        lookup = {r["market_id"]: r for rows in fam_rows.values() for r in rows}
        pn = np.array([np.mean([pnl(x, truth(lookup[x["market_id"]], i)) for x in short])
                       for i in pick])
        p4["shortlist_pnl"] = {"n": len(short), "n_blocks": len({x["team"] for x in short}),
                               "expected_per_contract": float(pn.mean()), "sd": float(pn.std()),
                               "mde": float(2.8 * pn.std()),
                               "share_draws_positive": float(np.mean(pn > 0))}
        out(f"  shortlist P&L per contract under the model: E {pn.mean():+.4f}, SD {pn.std():.4f}, "
            f"MDE {2.8 * pn.std():.4f}, positive in {np.mean(pn > 0):.1%} of drawn seasons")
    result["P4"] = p4

    # ---------------------------------------------------------------- write
    register = {
        "kind": "c25.forward_calls", "unit": "c-25",
        "preregistration": "docs/C25-season-model-meets-market-preregistration.md (ecb3048)",
        "script": "research/season_vs_market.py", "script_commit": git_head(),
        "as_of_ts": T_OF[3], "as_of": iso(T_OF[3]),
        "model": {"params": params.as_dict(), "sigma": sigma, "state": "after week 3 of 2026",
                  "n_sims": N_CUR, "division": "a-41 fixed ratings (published object)"},
        "rule": "shortlisted = mid outside the model's walk-forward reliability band for its bin AND "
                "the model-direction taker trade at 100 contracts clears at the band's conservative "
                "edge; see the pre-registration P3",
        "scoring": "at settlement, per family as it settles: shortlist P&L per contract at the frozen "
                   "executable price net of the frozen fee, team-block bootstrap; Brier(model) - "
                   "Brier(mid) over every two-sided undecided rung at T_3, team-block bootstrap. "
                   "Retire as 'not detectable at n blocks' if the interval contains zero and the MDE "
                   "exceeds the claimed edge.",
        "power_under_model": p4,
        "calls": short, "disagree_not_cost": disagree_only,
        "all_rungs_at_T3": [
            {"market_id": r["market_id"], "family": r["family"], "p_model": round(r["p_model"], 4),
             "bid": r["quote"]["bid"], "ask": r["quote"]["ask"], "quote_ts": r["quote"]["ts"]}
            for rows in fam_rows.values() for r in rows],
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.register)), exist_ok=True)
    if os.path.exists(a.register):
        raise SystemExit(f"REFUSED: {a.register} exists - the register is append-only; "
                         "a re-run writes elsewhere")
    json.dump(register, open(a.register, "w"), indent=1)
    result["log"] = LOG
    os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
    json.dump(result, open(a.json_out, "w"), indent=1, default=str)
    out(f"\nwrote {a.json_out} and {a.register}")


if __name__ == "__main__":
    main()
