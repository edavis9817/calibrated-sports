"""c-30 - against the spread: the key-number margin, scored.

    LOGGER_DB=<market_log.db> python -m research.against_the_spread --json-out D:/temp/c30/result.json

PRE-REGISTRATION: docs/C30-against-the-spread-preregistration.md, committed and
pushed at 1bb405f BEFORE this script or `models/key_margin.py` existed. This
file implements it; comments say only where the code carries a rule out.

The Elo walk, sigma_m, the Kalshi close rule and the bootstrap loop are c-28's
(`research.game_forecast`), imported, not copied; the statistics are c-24's
(`research.ranking_calibration`). market_log.db is opened mode=ro and nothing
is written except --json-out. Everything printed is an aggregate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import defaultdict

import numpy as np
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import season_model as S                      # noqa: E402
from models import key_margin as KM                     # noqa: E402
from research import game_forecast as GF                # noqa: E402
from research import ranking_calibration as rc          # noqa: E402
from research.structural import ladder_key              # noqa: E402

FIT_FROM = GF.FIT_FROM            # 2000
SCORE_FROM, SCORE_TO = 2001, 2025
ODDS_FROM = 2006
HALVES = ((2006, 2015), (2016, 2025))
KALSHI_SEASON = GF.KALSHI_SEASON
C28_P1_BRIER = 0.2205815236861795       # c-28's recorded Part 1 model Brier (result.json)
NEAR_KEYS = {2.5, 3.0, 3.5, 6.5, 7.0, 7.5}
SPIKES = (3, 7, 10, 14, 6, 4)
STRATA = ("on 3", "on 7", "other whole", "half point")
ARMS = ("E", "N0", "N1")


def stratum(line):
    a = abs(float(line))
    if a == 3:
        return "on 3"
    if a == 7:
        return "on 7"
    return "other whole" if a == int(a) else "half point"


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def payout(odds):
    o = float(odds)
    return 100.0 / -o if o < 0 else o / 100.0


def boot_rows(name, rows, fns, draws):
    """c-28's `boot_many` on a population whose rows carry `game`."""
    pop = rc.Pop(name, rows, "m", "k")
    return pop, GF.boot_many(pop, lambda i: fns(pop, i), draws=draws)


# =============================================================================
# the fit
# =============================================================================

def season_fits(games, walk, sig, years):
    """{T: (w, t, info)} fitted on seasons FIT_FROM..T-1, mu under T's params."""
    fits = {}
    for T in years:
        pre = walk.pre(walk.params(T))
        idx = [i for i in pre if FIT_FROM <= games[i]["season"] < T and GF.scored(games[i])]
        p = np.clip(np.array([pre[i] for i in idx]), 1e-12, 1 - 1e-12)
        mu = sig[T] * norm.ppf(p)
        m = np.array([games[i]["home_score"] - games[i]["away_score"] for i in idx], int)
        w, info = KM.fit_weights(mu, np.full(len(mu), sig[T]), m)
        t = float((m == 0).mean())
        info.update(games=len(idx), ties=int((m == 0).sum()))
        fits[T] = (w, t, info)
    return fits


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

    tests = []            # (part, name, measure, interval) - the registered ones only

    def reg(part, name, measure, r):
        tests.append((part, name, measure, r))

    result = {"preregistration": "docs/C30-against-the-spread-preregistration.md @ 1bb405f"}
    con = S.market_log_ro()
    games, _g, versions = S.load(con)
    result["versions"] = versions
    by_id = {g["game_id"]: g for g in games}
    out(f"games loaded {len(games):,}; versions {versions}")
    if versions["nfl_games"] != "2026-09-30":
        out("   NOTE: nfl_games has moved since c-28's run; the byte check below will say whether it matters")

    t0 = time.time()
    walk = GF.Walk(games, S.game_losses(games, S.grid()), mov=True)
    years = list(range(SCORE_FROM, SCORE_TO + 1)) + [KALSHI_SEASON]
    sig = GF.margin_sigmas(games, walk, years)
    tot = GF.total_params(games)
    out(f"walk + sigma_m {time.time() - t0:.0f}s")

    # ------------------------------------------------ the moneyline must not move
    pop1 = [i for i, g in enumerate(games)
            if SCORE_FROM <= g["season"] <= SCORE_TO and GF.scored(g)
            and g["home_score"] != g["away_score"]]
    pm = np.array([walk.p(i) for i in pop1])
    ym = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop1])
    bs = rc.corp(pm, ym)["bs"]
    digest = hashlib.sha256("|".join(repr(float(x)) for x in pm).encode()).hexdigest()
    out(f"moneyline: Part-1 Brier {bs!r} (c-28 recorded {C28_P1_BRIER!r}); sha256(p_home) {digest}")
    if bs != C28_P1_BRIER:
        raise SystemExit("the moneyline moved: Part-1 Brier differs from c-28's recorded run")
    result["moneyline_identity"] = {"part1_brier": bs, "c28_recorded": C28_P1_BRIER,
                                    "byte_identical": True, "sha256_p_home": digest, "n": len(pop1)}

    t0 = time.time()
    fits = season_fits(games, walk, sig, years)
    out(f"key-number fits {time.time() - t0:.0f}s")
    wf = fits[KALSHI_SEASON][0]
    out("final w (fit 2000-2025, used for 2026): " +
        " ".join(f"{j + 1}:{wf[j]:.2f}" for j in range(KM.J)))
    result["fits"] = {T: {"w": [float(x) for x in fits[T][0]], "t": fits[T][1], **fits[T][2],
                          "sigma_m": sig[T], "params": walk.params(T).as_dict()} for T in years}

    # ------------------------------------------------ per-game arrays, 2001-2025
    lines_ = {r[0]: r[1:] for r in con.execute(
        "SELECT g.game_id, g.spread_line, g.home_spread_odds, g.away_spread_odds FROM nfl_games g "
        "JOIN (SELECT game_id, MAX(data_version) dv FROM nfl_games GROUP BY game_id) v "
        "ON v.game_id = g.game_id AND v.dv = g.data_version")}
    allg = [i for i, g in enumerate(games) if SCORE_FROM <= g["season"] <= SCORE_TO and GF.scored(g)]
    per = {}
    for i in allg:
        g = games[i]
        f = GF.forecast(games, i, walk, sig, tot, "kickoff")
        per[i] = {"p": f.p_home, "mu": f.mu_m, "sig": f.sigma_m,
                  "m": g["home_score"] - g["away_score"], "T": g["season"]}
    # pmf per game under E and N1, season by season
    pm_E, pm_N1 = {}, {}
    for T in range(SCORE_FROM, SCORE_TO + 1):
        idx = [i for i in allg if games[i]["season"] == T]
        w, t, _ = fits[T]
        P = KM.pmf([per[i]["p"] for i in idx], [per[i]["mu"] for i in idx], [per[i]["sig"] for i in idx], w, t)
        P1 = KM.pmf([per[i]["p"] for i in idx], [per[i]["mu"] for i in idx], [per[i]["sig"] for i in idx],
                    np.ones(KM.J), t)
        for r, i in enumerate(idx):
            pm_E[i], pm_N1[i] = P[r], P1[r]

    # E's moneyline is the base's, game by game
    K = KM._K
    worst = 0.0
    for i in allg:
        P = pm_E[i]
        cond = P[K >= 1].sum() / (1 - P[K == 0].sum())
        worst = max(worst, abs(cond - per[i]["p"]))
        assert abs(P.sum() - 1) < 1e-9
    out(f"   max |P_E(M>0 | M!=0) - p_home| over {len(allg):,} games: {worst:.2e}")
    if worst > 1e-12:
        raise SystemExit("E moved the moneyline")
    result["moneyline_identity"]["max_abs_side_error"] = worst
    # spot check: the object and the vectorised path agree
    spot = allg[::500]
    for i in spot:
        g = games[i]
        base = GF.forecast(games, i, walk, sig, tot, "kickoff")
        obj = KM.KeyMarginForecast(base, tuple(fits[g["season"]][0]), fits[g["season"]][1])
        assert obj.prob_home_win() is base.prob_home_win() or obj.prob_home_win() == base.p_home
        assert abs(obj.prob_margin_over(2.5) - pm_E[i][K > 2.5].sum()) < 1e-12

    # ------------------------------------------------ the spike table
    out("\n######## SPIKE TABLE - |margin| masses, 2001-2025, every scored game")
    absm = np.array([abs(per[i]["m"]) for i in allg])
    AE = np.array([pm_E[i] for i in allg])
    AN = np.array([pm_N1[i] for i in allg])
    spike = {}
    out(f"   {'j':>3} {'empirical':>9} {'Wilson 95%':>17} {'Normal':>7} {'E (wf)':>7} {'w_final':>7}")
    for j in range(0, 22):
        k = int((absm == j).sum())
        lo, hi = wilson(k, len(absm))
        nm = float(AN[:, (np.abs(K) == j)].sum(axis=1).mean())
        em = float(AE[:, (np.abs(K) == j)].sum(axis=1).mean())
        wj = float(wf[j - 1]) if 1 <= j <= KM.J else None
        spike[j] = {"n": k, "empirical": k / len(absm), "wilson": [lo, hi], "normal": nm, "E": em, "w_final": wj}
        mark = "  <-" if j in SPIKES else ""
        out(f"   {j:>3} {k / len(absm):>9.4f} [{lo:.4f}, {hi:.4f}] {nm:>7.4f} {em:>7.4f} "
            f"{'' if wj is None else f'{wj:>7.2f}'}{mark}")
    result["spike_table"] = {"n_games": len(absm), "rows": spike}

    # ------------------------------------------------ Part 1 rows
    rows = []                          # decisive-for-cover rows (no push)
    push_rows = []                     # whole-number lines, pushes included
    for i in allg:
        g = games[i]
        L = lines_.get(g["game_id"], (None,))[0]
        if L is None:
            continue
        L = float(L)
        m = per[i]["m"]
        PE, PN = pm_E[i], pm_N1[i]
        oE, eE = PE[K > L].sum(), PE[K == L].sum()
        oN, eN = PN[K > L].sum(), PN[K == L].sum()
        n0 = float(norm.sf(L, loc=per[i]["mu"], scale=per[i]["sig"]))
        st = stratum(L)
        if L == int(L):
            push_rows.append({"game": g["game_id"], "stat": st, "line": L, "y": float(m == L),
                              "E": float(eE), "N1": float(eN), "N0": 0.0, "m": 0.0, "k": 0.0})
        if m == L:
            continue
        rows.append({"game": g["game_id"], "stat": st, "line": L, "season": g["season"],
                     "y": 1.0 if m > L else 0.0, "margin": m,
                     "E": float(oE / (1 - eE)), "N1": float(oN / (1 - eN)), "N0": n0,
                     "odds": lines_[g["game_id"]][1:]})
    if len({r["game"] for r in rows}) != len(rows):
        raise SystemExit("a game appears twice in the cover population")
    result["part1_population"] = {"cover_rows": len(rows), "push_rows": len(push_rows),
                                  "pushes": int(sum(r["y"] for r in push_rows)),
                                  "by_stratum": {s: sum(r["stat"] == s for r in rows) for s in STRATA}}
    out(f"\n######## PART 1 - cover against the nflverse closing spread, {SCORE_FROM}-{SCORE_TO} (PRIMARY)")
    out(f"   cover rows {len(rows):,} (pushes excluded), whole-line rows {len(push_rows):,} "
        f"with {result['part1_population']['pushes']} pushes; by stratum {result['part1_population']['by_stratum']}")

    def cell_rows(cell):
        return rows if cell == "pooled" else [r for r in rows if r["stat"] == cell]

    part1 = {}
    for cell in STRATA + ("pooled",):
        cr = cell_rows(cell)
        y = np.array([r["y"] for r in cr])
        desc = {"n": len(cr), "realized_cover": float(y.mean())}
        for arm in ARMS:
            f = np.array([r[arm] for r in cr])
            c = rc.corp(f, y)
            desc[arm] = {**c, "mean_f": float(f.mean()), "citl": float(f.mean() - y.mean()),
                         "auc": rc.auc(f, y)}
        out(f"\n== {cell}: n {len(cr):,}  realized home cover {y.mean():.4f}")
        for arm in ARMS:
            d = desc[arm]
            out(f"   {arm:<3} BS {d['bs']:.4f} = MCB {d['mcb']:.4f} - DSC {d['dsc']:.4f} + UNC {d['unc']:.4f}"
                f"   mean f {d['mean_f']:.4f}  CITL {d['citl']:+.4f}  AUC {d['auc']:.4f}")
        diffs = {}
        for comp in ("N0", "N1"):
            prow = [{"game": r["game"], "stat": "x", "line": 0.0, "y": r["y"], "m": r["E"], "k": r[comp]}
                    for r in cr]

            def fns(pop, i):
                ce, ck = rc.corp(pop.m[i], pop.y[i]), rc.corp(pop.k[i], pop.y[i])
                yb = pop.y[i].mean()
                return {"dBrier": ce["bs"] - ck["bs"], "dMCB": ce["mcb"] - ck["mcb"],
                        "d|CITL|": abs(pop.m[i].mean() - yb) - abs(pop.k[i].mean() - yb)}
            _p, d = boot_rows(cell, prow, fns, a.draws)
            diffs[f"E-{comp}"] = d
            for nm, r in d.items():
                out(f"   E - {comp:<3} {nm:<8} {rc.fmt(r)}  -> {rc.sign(r)}")
                reg("P1", f"{cell} E-{comp}", nm, r)
        part1[cell] = {"descriptive": desc, "diffs": diffs}
    v3 = rc.sign(part1["on 3"]["diffs"]["E-N0"]["dMCB"])
    v7 = rc.sign(part1["on 7"]["diffs"]["E-N0"]["dMCB"])
    verdict = ("YES: better calibrated at 3 and at 7" if v3 == v7 == "below" else
               "at 3 only" if v3 == "below" else "at 7 only" if v7 == "below" else
               "NO: no better calibrated than the Normal at the key numbers")
    m3 = rc.sign(part1["on 3"]["diffs"]["E-N1"]["dMCB"])
    m7 = rc.sign(part1["on 7"]["diffs"]["E-N1"]["dMCB"])
    out(f"\n   REGISTERED VERDICT (dMCB E-N0 on 3: {v3}, on 7: {v7}) -> {verdict}")
    out(f"   mechanism reading (dMCB E-N1 on 3: {m3}, on 7: {m7})")
    result["part1"] = {"cells": part1, "verdict": verdict, "signs": {"on 3": v3, "on 7": v7},
                       "mechanism_signs": {"on 3": m3, "on 7": m7}}

    # per-season stability of the registered cells (cut, not verdict)
    out("\n-- cut: dMCB E-N0 by season half (not a verdict)")
    cuts = {}
    for lo_, hi_ in ((2001, 2012), (2013, 2025)):
        for cell in ("on 3", "on 7"):
            cr = [r for r in cell_rows(cell) if lo_ <= r["season"] <= hi_]
            prow = [{"game": r["game"], "stat": "x", "line": 0.0, "y": r["y"], "m": r["E"], "k": r["N0"]} for r in cr]
            _p, d = boot_rows(cell, prow, lambda pop, i: {
                "dMCB": rc.corp(pop.m[i], pop.y[i])["mcb"] - rc.corp(pop.k[i], pop.y[i])["mcb"]},
                min(a.draws, 1000))
            cuts[f"{lo_}-{hi_} {cell}"] = d["dMCB"]
            out(f"   {lo_}-{hi_} {cell:<5} n {len(cr):>4}  dMCB {rc.fmt(d['dMCB'])}")
    result["part1"]["cuts_by_half"] = cuts

    # ------------------------------------------------ POST-HOC (not registered, not in the count)
    # Added after the 20-draw smoke run showed E LESS calibrated than N0 on 3.
    # Two readings separate the shape from the model's mean: (i) how far each
    # arm's forecasts spread from 0.5, and (ii) every arm re-centred on the LINE
    # itself (mu = spread_line, p = Phi(L / sigma)), so the only thing left to
    # differ is the shape. Signed strata split home-favoured from home-underdog.
    out("\n######## POST-HOC (added after the smoke run; not registered, not counted)")
    ph = {"dispersion": {}, "line_centred": {}}
    for cell in STRATA + ("pooled",):
        cr = cell_rows(cell)
        ph["dispersion"][cell] = {arm: float(np.std([r[arm] for r in cr])) for arm in ARMS}
    out("   sd of forecast f by stratum: " + "; ".join(
        f"{c} " + " ".join(f"{arm} {v:.4f}" for arm, v in d.items()) for c, d in ph["dispersion"].items()))
    for r in rows:
        T = r["season"]
        L = r["line"]
        s_ = sig[T]
        p_line = float(norm.cdf(L / s_))
        w, t, _ = fits[T]
        oE, eE = KM.cover_probs([p_line], [L], [s_], w, t, [L])
        oN, eN = KM.cover_probs([p_line], [L], [s_], np.ones(KM.J), t, [L])
        r["E@L"] = float(oE[0] / (1 - eE[0]))
        r["N1@L"] = float(oN[0] / (1 - eN[0]))
        r["N0@L"] = 0.5
    for cell in ("on 3", "on 7", "other whole", "half point"):
        for sgn, nm in ((1, "home fav"), (-1, "home dog")):
            cr = [r for r in rows if r["stat"] == cell and (r["line"] > 0 if sgn > 0 else r["line"] < 0)]
            if len(cr) < 30:
                continue
            key = f"{cell} {nm}"
            y = np.array([r["y"] for r in cr])
            k = int(y.sum())
            lo, hi = wilson(k, len(cr))
            d = {"n": len(cr), "realized": float(y.mean()), "wilson": [lo, hi]}
            for arm in ("E@L", "N1@L", "N0@L"):
                prow = [{"game": r["game"], "stat": "x", "line": 0.0, "y": r["y"], "m": r[arm], "k": 0.0} for r in cr]
                _p, b = boot_rows(key, prow, lambda pop, i: {"CITL": float(pop.m[i].mean() - pop.y[i].mean())},
                                  min(a.draws, 1000))
                d[arm] = {"mean_f": float(np.mean([r[arm] for r in cr])), "CITL": b["CITL"]}
            ph["line_centred"][key] = d
            out(f"   line-centred {key:<22} n {len(cr):>4}  realized {y.mean():.4f} [{lo:.4f}, {hi:.4f}]  "
                + "  ".join(f"{arm} {d[arm]['mean_f']:.4f} (CITL {rc.fmt(d[arm]['CITL'])})" for arm in ("E@L", "N1@L")))
    result["post_hoc"] = ph

    # ------------------------------------------------ push-rate calibration
    out("\n######## PUSH RATE - predicted P(M = L) against realised, whole-number lines")
    pushes = {}
    for cell in ("on 3", "on 7", "other whole"):
        pr = [r for r in push_rows if r["stat"] == cell]
        pushes[cell] = {"n": len(pr), "realized": float(np.mean([r["y"] for r in pr]))}
        for arm in ("E", "N1"):
            prow = [{"game": r["game"], "stat": "x", "line": 0.0, "y": r["y"], "m": r[arm], "k": 0.0} for r in pr]
            _p, d = boot_rows(cell, prow, lambda pop, i: {"pred-real": float(pop.m[i].mean() - pop.y[i].mean())},
                              a.draws)
            pushes[cell][arm] = {"mean_pred": float(np.mean([r[arm] for r in pr])), **d}
            reg("PUSH", f"{cell} {arm}", "pred-real", d["pred-real"])
        k = int(sum(r["y"] for r in pr))
        lo, hi = wilson(k, len(pr))
        out(f"   {cell:<11} n {len(pr):>4}  realized {k / len(pr):.4f} [{lo:.4f}, {hi:.4f}]   "
            f"E {pushes[cell]['E']['mean_pred']:.4f} ({rc.fmt(pushes[cell]['E']['pred-real'])})   "
            f"N1 {pushes[cell]['N1']['mean_pred']:.4f} ({rc.fmt(pushes[cell]['N1']['pred-real'])})   N0 0.0000")
    result["push_rate"] = pushes

    # ------------------------------------------------ Part 2a Kalshi
    out(f"\n######## PART 2a - Kalshi KXNFLSPREAD, {KALSHI_SEASON} weeks {GF.KALSHI_WEEKS}")
    out("   POWER (stated before the result): c-28 measured 829 rungs / 32 games, dBrier SE 0.0085, "
        "MDE 0.0237; the book close's edge over the model on the moneyline is 0.009.")
    closes, census = GF.kalshi_closes(con, by_id)
    result["kalshi_census"] = census
    wk, tk = fits[KALSHI_SEASON][0], fits[KALSHI_SEASON][1]
    kr = []
    keys = defaultdict(set)
    for c in closes:
        if c["series"] != "KXNFLSPREAD" or c["line"] is None:
            continue
        g = by_id[c["game_id"]]
        if not GF.scored(g) or g["game_type"] != "REG":
            continue
        L = float(c["line"])
        if abs(L - math.floor(L) - 0.5) > 1e-9:
            continue
        keys[ladder_key(c["market_id"])].add(c["team"])
        i = games.index(g)
        base = GF.forecast(games, i, walk, sig, tot, "kickoff")
        obj = KM.KeyMarginForecast(base, tuple(wk), tk)
        side = 1 if c["team"] == g["home"] else (-1 if c["team"] == g["away"] else None)
        if side is None:
            raise SystemExit(f"{c['market_id']}: team not in game")
        tm = (g["home_score"] - g["away_score"]) * side
        kr.append({"game": c["game_id"], "stat": "spread:" + ("home" if side > 0 else "away"), "line": L,
                   "y": 1.0 if tm > L else 0.0, "E": obj.prob_team_by_over(c["team"], L),
                   "N0": base.prob_team_by_over(c["team"], L), "mkt": c["mid"]})
    bad = {k: v for k, v in keys.items() if len(v) != 1}
    if bad:
        raise SystemExit(f"a ladder spans two teams: {list(bad.items())[:3]}")
    p2a = {"n_rungs": len(kr), "n_games": len({r['game'] for r in kr})}
    t2a = []
    for nm, mk, kk in (("E - market", "E", "mkt"), ("N0 - market", "N0", "mkt"), ("E - N0", "E", "N0")):
        rr = [{**r, "m": r[mk], "k": r[kk]} for r in kr]
        res = GF.compare(f"2a {nm}", rr, out, t2a, within_line=True, draws=a.draws)
        p2a[nm] = res
        for _n, meas, r in t2a[-5:]:
            if nm != "E - N0" or meas == "dBrier":
                reg("P2a", nm, meas, r)
    result["part2a_kalshi"] = p2a

    # ------------------------------------------------ Part 2b / 2c - the book price
    out(f"\n######## PART 2b - against the book cover price, {ODDS_FROM}-{SCORE_TO}")
    orow = [r for r in rows if r["season"] >= ODDS_FROM and r["odds"][0] is not None and r["odds"][1] is not None]
    for r in orow:
        ih, ia = GF.american(r["odds"][0]), GF.american(r["odds"][1])
        r["book"] = ih / (ih + ia)
    p2b = {}
    for cell in STRATA + ("pooled",):
        cr = orow if cell == "pooled" else [r for r in orow if r["stat"] == cell]
        yb = np.array([r["y"] for r in cr])
        p2b[cell] = {"n": len(cr), "brier_book": rc.brier(np.array([r["book"] for r in cr]), yb)}
        for arm in ("E", "N0"):
            prow = [{"game": r["game"], "stat": "x", "line": 0.0, "y": r["y"], "m": r[arm], "k": r["book"]} for r in cr]
            _p, d = boot_rows(cell, prow, lambda pop, i: {
                "dBrier": rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i])}, a.draws)
            p2b[cell][f"{arm}-book"] = d["dBrier"]
            reg("P2b", f"{cell} {arm}-book", "dBrier", d["dBrier"])
        out(f"   {cell:<11} n {len(cr):>5}  Brier book {p2b[cell]['brier_book']:.4f}   "
            f"E-book {rc.fmt(p2b[cell]['E-book'])}   N0-book {rc.fmt(p2b[cell]['N0-book'])}")
    result["part2b_book"] = p2b

    out(f"\n######## PART 2c - the registered honest-positive test, flat bets {ODDS_FROM}-{SCORE_TO}")
    # the betting population includes pushes (a push returns 0)
    brow = []
    for i in allg:
        g = games[i]
        if g["season"] < ODDS_FROM:
            continue
        L, ho, ao = lines_.get(g["game_id"], (None, None, None))
        if L is None or ho is None or ao is None:
            continue
        L = float(L)
        m = per[i]["m"]
        PE, PN = pm_E[i], pm_N1[i]
        f = {"E": PE[K > L].sum() / (1 - PE[K == L].sum()),
             "N1": PN[K > L].sum() / (1 - PN[K == L].sum()),
             "N0": float(norm.sf(L, loc=per[i]["mu"], scale=per[i]["sig"]))}
        brow.append({"game": g["game_id"], "season": g["season"], "L": L, "m": m, "ho": ho, "ao": ao, "f": f})
    p2c = {}
    for arm in ARMS:
        bets = []
        for b in brow:
            eh = b["f"][arm] - GF.american(b["ho"])
            ea = (1 - b["f"][arm]) - GF.american(b["ao"])
            if max(eh, ea) <= 0:
                continue
            home = eh >= ea
            if b["m"] == b["L"]:
                ret = 0.0
            elif (b["m"] > b["L"]) == home:
                ret = payout(b["ho"] if home else b["ao"])
            else:
                ret = -1.0
            bets.append({"game": b["game"], "stat": "x", "line": 0.0, "y": 0.0, "m": ret, "k": 0.0,
                         "season": b["season"], "near": abs(b["L"]) in NEAR_KEYS})
        cells = {"away pooled": [x for x in bets if not x["near"]],
                 "away 2006-2015": [x for x in bets if not x["near"] and x["season"] <= 2015],
                 "away 2016-2025": [x for x in bets if not x["near"] and x["season"] >= 2016],
                 "key lines": [x for x in bets if x["near"]],
                 "pooled": bets}
        p2c[arm] = {}
        for cn, cb in cells.items():
            _p, d = boot_rows(cn, cb, lambda pop, i: {"ROI": float(pop.m[i].mean())}, a.draws)
            p2c[arm][cn] = {"bets": len(cb), "win_rate_ex_push": float(np.mean([x["m"] > 0 for x in cb if x["m"] != 0])),
                            **d["ROI"]}
            reg("P2c", f"{arm} {cn}", "ROI", d["ROI"])
            out(f"   {arm:<3} {cn:<15} bets {len(cb):>5}  ROI {rc.fmt(d['ROI'])}  -> {rc.sign(d['ROI'])}")
        ok = (rc.sign(p2c[arm]["away pooled"]) == "above" and rc.sign(p2c[arm]["away 2006-2015"]) == "above"
              and rc.sign(p2c[arm]["away 2016-2025"]) == "above")
        p2c[arm]["honest_positive"] = ok
    pos = [arm for arm in ARMS if p2c[arm]["honest_positive"]]
    out(f"\n   HONEST POSITIVE (registered conditions 1 and 2): {pos if pos else 'none'}")
    result["part2c_bets"] = p2c
    result["honest_positive"] = pos
    con.close()

    n_ex = sum(1 for *_x, r in tests if rc.sign(r) in ("below", "above"))
    by_part = defaultdict(int)
    for part, *_x in tests:
        by_part[part] += 1
    result["registered_intervals"] = {"count": len(tests), "exclude_zero": n_ex, "by_part": dict(by_part)}
    out(f"\n   registered intervals: {len(tests)} ({dict(by_part)}), {n_ex} exclude zero; no multiplicity correction")
    result["log"] = lines
    with open(a.json_out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=lambda o: o.as_dict() if hasattr(o, "as_dict") else str(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
