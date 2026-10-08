"""c-24 - is the model badly calibrated, or genuinely uninformative?

    python -m research.ranking_calibration --ledger D:/temp/c24/wf_ledger.csv \
        --cache D:/temp/c19/extract.sqlite --rows D:/temp/c24/rows.json \
        --json-out D:/temp/c24/result.json

PRE-REGISTRATION: docs/C24-ranking-versus-calibration-preregistration.md,
committed at 78ef152 BEFORE this script existed. This file implements it; it
does not extend it. The comments here say only where the code carries a rule
out.

Inputs, none of them written:
  --ledger  `python -m research.walkforward --ledger-out F` (P1). Per-outcome
            predictions - scratch, never committed.
  --cache   the c-19 scratch cache (P2), read by research.venue_spread.
  --rows    scratch JSON of the P2/P3 rows, built on the first run so a re-run
            does not refit ~450 player-stats; delete it to rebuild.
market_log.db is opened mode=ro only (venue_spread's resolver redirects the
store; research.score opens mode=ro itself).

Everything PRINTED is an aggregate or an interval (BET_LIST_RESTRICTION).
"""
import argparse
import csv
import json
import math
import os
import sys
import zlib
from collections import Counter, defaultdict

import numpy as np
from scipy.stats import rankdata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOOT, SEED = 2000, 24
CLIP = 1e-4
MIN_GAMES = 5
# the committed restatements the populations must reproduce (4 dp)
P1_EXPECT = {2023: (4785, 0.0229), 2024: (5225, 0.0237), 2025: (6031, 0.0195)}
P2_EXPECT = (2513, 0.0121)


# =============================================================================
# the statistics - pure, numpy arrays in, floats out
# =============================================================================

def brier(p, y):
    return float(np.mean((p - y) ** 2))


def auc(p, y):
    """Mann-Whitney concordance, ties 1/2 (average ranks)."""
    n1 = float(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    r = rankdata(p)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def spearman(p, y):
    rp, ry = rankdata(p), rankdata(y)
    if rp.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rp, ry)[0, 1])


def wauc(p, y, strata):
    """Concordance over (over, under) pairs sharing a stratum; numerators and
    denominators summed across strata. `strata` is an int array."""
    num = den = 0.0
    order = np.argsort(strata, kind="stable")
    s = strata[order]
    cuts = np.flatnonzero(np.diff(s)) + 1
    for idx in np.split(order, cuts):
        yy = y[idx]
        n1 = float(yy.sum())
        n0 = len(yy) - n1
        if n1 == 0 or n0 == 0:
            continue
        r = rankdata(p[idx])
        num += r[yy == 1].sum() - n1 * (n1 + 1) / 2
        den += n1 * n0
    return float(num / den) if den else None


def pav(p, y):
    """Isotonic (non-decreasing) regression of y on p, identical p pooled
    first. -> (fitted values in the input order, block x-centres, block values)."""
    order = np.argsort(p, kind="stable")
    ps, ys = p[order], y[order]
    ux, start = np.unique(ps, return_index=True)
    w = np.diff(np.append(start, len(ps))).astype(float)
    sy = np.add.reduceat(ys, start)
    sx = np.add.reduceat(ps, start)
    # stack of blocks: [sum_y, weight, sum_x, n_unique]
    by, bw, bx, bn = [], [], [], []
    for i in range(len(ux)):
        cy, cw, cx, cn = sy[i], w[i], sx[i], 1
        while by and by[-1] / bw[-1] >= cy / cw:
            cy += by.pop()
            cw += bw.pop()
            cx += bx.pop()
            cn += bn.pop()
        by.append(cy)
        bw.append(cw)
        bx.append(cx)
        bn.append(cn)
    vals = np.array(by) / np.array(bw)
    fitted_sorted = np.repeat(vals, np.array(bw).astype(int))
    out = np.empty_like(fitted_sorted)
    out[order] = fitted_sorted
    centres = np.array(bx) / np.array(bw)
    return out, centres, vals


def corp(p, y):
    """CORP decomposition: BS = MCB - DSC + UNC, exact."""
    iso, _c, _v = pav(p, y)
    bs = brier(p, y)
    ybar = float(y.mean())
    unc = ybar * (1 - ybar)
    bs_iso = brier(iso, y)
    mcb, dsc = bs - bs_iso, unc - bs_iso
    assert abs(bs - (mcb - dsc + unc)) < 1e-9
    return {"bs": bs, "mcb": mcb, "dsc": dsc, "unc": unc, "bs_iso": bs_iso}


def murphy(p, y, bins=10):
    """Binned Murphy decomposition, equal-width bins; residual closes the identity."""
    b = np.minimum((p * bins).astype(int), bins - 1)
    ybar = y.mean()
    rel = res = 0.0
    n = len(p)
    for k in range(bins):
        m = b == k
        nk = m.sum()
        if nk:
            fk, ok = p[m].mean(), y[m].mean()
            rel += nk * (fk - ok) ** 2 / n
            res += nk * (ok - ybar) ** 2 / n
    unc = ybar * (1 - ybar)
    bs = brier(p, y)
    return {"rel": rel, "res": res, "unc": unc, "resid": bs - (rel - res + unc)}


def logit(p):
    p = np.clip(p, CLIP, 1 - CLIP)
    return np.log(p / (1 - p))


def platt_fit(p, y, iters=50):
    x = logit(p)
    a, b = 0.0, 1.0
    for _ in range(iters):
        q = 1 / (1 + np.exp(-(a + b * x)))
        wgt = q * (1 - q)
        g = np.array([np.sum(y - q), np.sum((y - q) * x)])
        H = np.array([[wgt.sum(), (wgt * x).sum()], [(wgt * x).sum(), (wgt * x * x).sum()]])
        step = np.linalg.solve(H, g)
        a, b = a + step[0], b + step[1]
        if np.abs(step).max() < 1e-10:
            break
    return lambda pp: np.clip(1 / (1 + np.exp(-(a + b * logit(pp)))), CLIP, 1 - CLIP), (a, b)


def iso_fit(p, y):
    _f, centres, vals = pav(p, y)
    return lambda pp: np.clip(np.interp(pp, centres, vals), CLIP, 1 - CLIP), len(vals)


# =============================================================================
# game-block bootstrap
# =============================================================================

class Pop:
    """One population: arrays on identical rows, blocks = games."""

    def __init__(self, name, rows, mkey, kkey):
        self.name = name
        self.rows = rows
        self.m = np.array([r[mkey] for r in rows], float)
        self.k = np.array([r[kkey] for r in rows], float)
        self.y = np.array([r["y"] for r in rows], float)
        keys = sorted({(r["stat"], float(r["line"])) for r in rows})
        sid = {k: i for i, k in enumerate(keys)}
        self.s = np.array([sid[(r["stat"], float(r["line"]))] for r in rows])
        games = defaultdict(list)
        for i, r in enumerate(rows):
            games[r["game"]].append(i)
        self.gidx = [np.array(v) for _g, v in sorted(games.items())]
        self.n, self.games = len(rows), len(self.gidx)

    def boot(self, fn, draws=BOOT, seed=SEED):
        """fn(idx) -> float. -> dict(est, lo, hi, se, games)."""
        est = fn(np.arange(self.n))
        rng = np.random.default_rng(seed)
        vals = []
        G = self.games
        for _ in range(draws):
            pick = rng.integers(0, G, G)
            idx = np.concatenate([self.gidx[j] for j in pick])
            v = fn(idx)
            if v is not None:
                vals.append(v)
        vals = np.array(vals)
        if G < 2 or est is None or not len(vals):
            return {"est": est, "lo": None, "hi": None, "se": None, "games": G, "n": self.n}
        return {"est": est, "lo": float(np.percentile(vals, 2.5)),
                "hi": float(np.percentile(vals, 97.5)), "se": float(vals.std()),
                "games": G, "n": self.n}


def fmt(r, d=4):
    if r is None or r.get("est") is None:
        return "n/a"
    if r["lo"] is None:
        return f"{r['est']:+.{d}f} [no interval]"
    star = "*" if (r["lo"] > 0 or r["hi"] < 0) else " "
    read = "" if r["games"] >= MIN_GAMES else "  (<5 games, not read)"
    return f"{r['est']:+.{d}f} [{r['lo']:+.{d}f}, {r['hi']:+.{d}f}]{star} SE {r['se']:.4f}{read}"


def sign(r):
    if r is None or r["lo"] is None or r["games"] < MIN_GAMES:
        return "not read"
    return "below" if r["hi"] < 0 else "above" if r["lo"] > 0 else "contains 0"


# =============================================================================
# per-population measurement
# =============================================================================

def discriminate(pop, out, tests):
    m, k, y, s = pop.m, pop.k, pop.y, pop.s
    res = {"n": pop.n, "games": pop.games, "brier_m": brier(m, y), "brier_k": brier(k, y),
           "realized": float(y.mean())}
    out(f"\n== {pop.name}: n {pop.n:,}  games {pop.games}  realized over rate {y.mean():.4f}")
    out(f"   Brier model {res['brier_m']:.4f}  market {res['brier_k']:.4f}  "
        f"diff {res['brier_m'] - res['brier_k']:+.4f}")
    lv = {}
    for nm, f in (("AUC", auc), ("Spearman", spearman)):
        lv[nm] = {"m": pop.boot(lambda i, f=f: f(m[i], y[i])),
                  "k": pop.boot(lambda i, f=f: f(k[i], y[i]))}
    lv["wAUC"] = {"m": pop.boot(lambda i: wauc(m[i], y[i], s[i])),
                  "k": pop.boot(lambda i: wauc(k[i], y[i], s[i]))}
    for nm in ("AUC", "Spearman", "wAUC"):
        out(f"   {nm:<9} model {fmt(lv[nm]['m'])}")
        out(f"   {'':<9} market {fmt(lv[nm]['k'])}")
    res["levels"] = lv

    def dstat(f):
        def g(i):
            a, b = f(m[i], y[i], i), f(k[i], y[i], i)
            return None if a is None or b is None else a - b
        return g
    diffs = {
        "dAUC": pop.boot(dstat(lambda p, yy, i: auc(p, yy))),
        "d_rho": pop.boot(dstat(lambda p, yy, i: spearman(p, yy))),
        "d_wAUC": pop.boot(dstat(lambda p, yy, i: wauc(p, yy, s[i]))),
    }
    cm, ck = corp(m, y), corp(k, y)
    diffs["dMCB"] = pop.boot(lambda i: corp(m[i], y[i])["mcb"] - corp(k[i], y[i])["mcb"])
    diffs["dDSC"] = pop.boot(lambda i: corp(m[i], y[i])["dsc"] - corp(k[i], y[i])["dsc"])
    res["corp"] = {"model": cm, "market": ck}
    res["corp_levels"] = {
        "mcb_m": pop.boot(lambda i: corp(m[i], y[i])["mcb"]),
        "mcb_k": pop.boot(lambda i: corp(k[i], y[i])["mcb"]),
        "dsc_m": pop.boot(lambda i: corp(m[i], y[i])["dsc"]),
        "dsc_k": pop.boot(lambda i: corp(k[i], y[i])["dsc"]),
    }
    out(f"   CORP  model  BS {cm['bs']:.4f} = MCB {cm['mcb']:.4f} - DSC {cm['dsc']:.4f} + UNC {cm['unc']:.4f}")
    out(f"   CORP  market BS {ck['bs']:.4f} = MCB {ck['mcb']:.4f} - DSC {ck['dsc']:.4f} + UNC {ck['unc']:.4f}")
    for nm in ("mcb_m", "mcb_k", "dsc_m", "dsc_k"):
        out(f"     {nm:<6} {fmt(res['corp_levels'][nm])}")
    for nm, r in diffs.items():
        mde = 2.8 * r["se"] if r.get("se") else None
        r["mde"] = mde
        out(f"   {nm:<7} (model - market) {fmt(r)}  MDE {mde:.4f}" if mde else f"   {nm:<7} {fmt(r)}")
        tests.append((pop.name, nm, r))
    res["diffs"] = diffs
    dbs = cm["bs"] - ck["bs"]
    dmcb, ddsc = cm["mcb"] - ck["mcb"], cm["dsc"] - ck["dsc"]
    res["shares"] = {"dBS": dbs, "dMCB": dmcb, "dDSC": ddsc,
                     "mcb_share": dmcb / dbs if dbs else None,
                     "dsc_share": -ddsc / dbs if dbs else None}
    out(f"   dBS {dbs:+.4f} = dMCB {dmcb:+.4f} - dDSC {ddsc:+.4f}   "
        f"shares: calibration {res['shares']['mcb_share']:.2f}, resolution {res['shares']['dsc_share']:.2f}")
    mm, mk = murphy(m, y), murphy(k, y)
    res["murphy"] = {"model": mm, "market": mk}
    out(f"   binned Murphy model  REL {mm['rel']:.4f} RES {mm['res']:.4f} UNC {mm['unc']:.4f} resid {mm['resid']:+.4f}")
    out(f"   binned Murphy market REL {mk['rel']:.4f} RES {mk['res']:.4f} UNC {mk['unc']:.4f} resid {mk['resid']:+.4f}")
    res["oracle"] = {"bs_iso_model": cm["bs_iso"], "bs_market": ck["bs"],
                     "oracle_minus_market": cm["bs_iso"] - ck["bs"]}
    out(f"   ORACLE (in-sample isotonic model, optimistic) Brier {cm['bs_iso']:.4f} vs market {ck['bs']:.4f}: "
        f"{cm['bs_iso'] - ck['bs']:+.4f}")
    # verdicts, per the pre-registration
    da, dd, dmc = sign(diffs["dAUC"]), sign(diffs["dDSC"]), sign(diffs["dMCB"])
    disc = {"below": "model orders WORSE than the market", "above": "model orders BETTER than the market",
            "contains 0": "no difference in ordering detected", "not read": "not read"}
    res["verdict_auc"] = disc[da]
    res["verdict_dsc"] = disc[dd]
    if dmc == "above" and dd in ("contains 0", "above"):
        cls = "miscalibrated, not blind"
    elif dd == "below" and dmc == "contains 0":
        cls = "uninformative relative to the market, calibration not the problem"
    elif dd == "below" and dmc == "above":
        cls = "both"
    else:
        cls = f"unclassified by the registered rule (dMCB {dmc}, dDSC {dd})"
    res["classification"] = cls
    out(f"   VERDICT ordering (dAUC): {disc[da]};  (dDSC): {disc[dd]}")
    out(f"   CLASSIFICATION: {cls}")
    return res


def recal(name, train_p, train_y, ev, out, tests, train_k=None):
    """Fit Platt and isotonic on TRAIN, freeze, score on EVAL (a Pop)."""
    res = {"train_n": len(train_y), "eval_n": ev.n, "eval_games": ev.games}
    out(f"\n-- recalibration {name}: train n {len(train_y):,}  ->  eval n {ev.n:,}, {ev.games} games")
    for mname, fitter in (("platt", platt_fit), ("isotonic", iso_fit)):
        fm, info = fitter(train_p, train_y)
        rm = fm(ev.m)
        y, k, m = ev.y, ev.k, ev.m
        a = ev.boot(lambda i: brier(rm[i], y[i]) - brier(k[i], y[i]))
        b = ev.boot(lambda i: brier(rm[i], y[i]) - brier(m[i], y[i]))
        tests.append((name, f"{mname}: Brier(recal model) - Brier(market)", a))
        tests.append((name, f"{mname}: Brier(recal model) - Brier(raw model)", b))
        r = {"info": [float(x) for x in info] if isinstance(info, tuple) else info,
             "brier_recal": brier(rm, y), "brier_raw": brier(m, y), "brier_market": brier(k, y),
             "recal_minus_market": a, "recal_minus_raw": b}
        if train_k is not None:
            fk, _ = fitter(train_k, train_y)
            rk = fk(k)
            c = ev.boot(lambda i: brier(rm[i], y[i]) - brier(rk[i], y[i]))
            r["recal_minus_recal_market"] = c
            r["brier_recal_market"] = brier(rk, y)
        s = sign(a)
        r["verdict"] = {"above": "the gap survives recalibration",
                        "contains 0": "recalibration closes the gap to within detection",
                        "below": "recalibrated model beats the market (a candidate, not an edge)",
                        "not read": "not read"}[s]
        out(f"   {mname:<8} {('a %.3f b %.3f' % tuple(info)) if isinstance(info, tuple) else f'{info} blocks'}"
            f"   Brier recal {r['brier_recal']:.4f}  raw {r['brier_raw']:.4f}  market {r['brier_market']:.4f}")
        out(f"            recal - market {fmt(a)}")
        out(f"            recal - raw    {fmt(b)}")
        if "recal_minus_recal_market" in r:
            out(f"            recal - recal market (secondary) {fmt(r['recal_minus_recal_market'])}  "
                f"[recal market Brier {r['brier_recal_market']:.4f}]")
        out(f"            VERDICT: {r['verdict']}")
        res[mname] = r
    return res


# =============================================================================
# loading
# =============================================================================

def load_p1(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["p_bench"] in ("", "None"):
                continue
            rows.append({"season": int(r["season"]), "week": int(r["week"]), "game": r["game_id"],
                         "stat": r["stat"], "line": float(r["line"]), "y": float(r["y"]),
                         "m": float(r["p_model"]), "k": float(r["p_bench"]), "moved": int(r["moved"])})
    if not rows:
        raise SystemExit(f"{path}: no P1 rows - refusing to report")
    return rows


def build_p2_p3(cache, workers):
    from research import venue_spread as vs
    games, kout, kmeta, kq, _depth, book = vs.load(cache)
    if not book or not kmeta:
        raise SystemExit("empty c-19 cache - refusing")
    resolve = vs.resolver()
    _m, _c, _a, _g, _mu, cons = vs.pair(games, kout, kmeta, kq, book, resolve)
    scored = []
    vs.step4(games, kmeta, kq, cons, resolve, workers, print, rows_out=scored)
    p2 = [{"game": r["game"], "stat": r["stat"], "line": float(r["line"]), "y": r["y"],
           "m": r["m"], "k": r["k"], "b": r["b"], "week": kmeta[r["mid"]]["week"]} for r in scored]
    from research import score
    rows, census, mv, _note, _v, _bf, _f = score.load()
    p3 = [{"game": r["game"], "stat": r["stat"], "line": float(r["line"]), "y": r["y"],
           "m": r["model"], "k": r["market_p"]} for r in rows if r["market_p"] is not None]
    return {"p2": p2, "p3": p3, "p3_model_version": mv, "p3_census": dict(census)}


# =============================================================================
# main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--rows", required=True, help="scratch JSON of P2/P3 rows, never committed")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json-out")
    ap.add_argument("--build-rows", action="store_true", help="build --rows and exit")
    a = ap.parse_args()
    if a.build_rows:
        built = build_p2_p3(a.cache, a.workers)
        with open(a.rows, "w") as f:
            json.dump(built, f)
        print(f"rows written: P2 {len(built['p2'])}  P3 {len(built['p3'])}", flush=True)
        return 0
    out = lambda s: print(s, flush=True)  # noqa: E731
    tests = []
    result = {"prereg": "78ef152"}

    p1 = load_p1(a.ledger)
    if os.path.exists(a.rows):
        with open(a.rows) as f:
            built = json.load(f)
    else:
        built = build_p2_p3(a.cache, a.workers)
        with open(a.rows, "w") as f:
            json.dump(built, f)
    p2, p3 = built["p2"], built["p3"]

    # ---- reproduction checks (the populations ARE the verdicts' populations)
    out("\n== REPRODUCTION CHECKS ==")
    repro = {}
    for T, (n_exp, d_exp) in P1_EXPECT.items():
        rs = [r for r in p1 if r["season"] == T]
        d = np.mean([(r["m"] - r["y"]) ** 2 - (r["k"] - r["y"]) ** 2 for r in rs])
        ok = len(rs) == n_exp and round(float(d), 4) == d_exp
        repro[f"P1-{T}"] = {"n": len(rs), "diff": float(d), "expected": [n_exp, d_exp], "ok": ok}
        out(f"   P1 {T}: n {len(rs)} (expect {n_exp})  Brier diff {d:+.4f} (expect +{d_exp:.4f})  "
            f"{'OK' if ok else 'MISMATCH'}")
    d2 = np.mean([(r["m"] - r["y"]) ** 2 - (r["k"] - r["y"]) ** 2 for r in p2])
    ok2 = len(p2) == P2_EXPECT[0] and round(float(d2), 4) == P2_EXPECT[1]
    repro["P2"] = {"n": len(p2), "diff": float(d2), "expected": list(P2_EXPECT), "ok": ok2}
    out(f"   P2: n {len(p2)} (expect {P2_EXPECT[0]})  Brier diff {d2:+.4f} (expect +{P2_EXPECT[1]:.4f})  "
        f"{'OK' if ok2 else 'MISMATCH'}")
    out(f"   P3: n {len(p3)}  (model version {built.get('p3_model_version')}, market column = stale candle)")
    result["reproduction"] = repro

    pops = {}
    for T in (2023, 2024, 2025):
        if repro[f"P1-{T}"]["ok"]:
            pops[f"P1-{T}"] = Pop(f"P1-{T} walk-forward vs book close", [r for r in p1 if r["season"] == T], "m", "k")
        else:
            out(f"   P1-{T} NOT READ (reproduction failed)")
    if all(repro[f"P1-{T}"]["ok"] for T in (2023, 2024, 2025)):
        pops["P1-pooled"] = Pop("P1-pooled 2023-2025 walk-forward vs book close", p1, "m", "k")
    if ok2:
        pops["P2"] = Pop("P2 weeks 2-3 2026 vs Kalshi mid (T-180)", p2, "m", "k")
    result["populations"] = {}
    for key, pop in pops.items():
        result["populations"][key] = discriminate(pop, out, tests)

    # ---- P2b: the three-way rows, each forecaster against the book
    if ok2:
        three = [r for r in p2 if r["b"] is not None]
        out(f"\n== P2b three-way rows vs book p_bench: n {len(three)}")
        p2b = {}
        for lab, key in (("model - book", "m"), ("Kalshi - book", "k")):
            pop = Pop(f"P2b {lab}", three, key, "b")
            x, b_, y = pop.m, pop.k, pop.y
            dA = pop.boot(lambda i: (lambda u, v: None if u is None or v is None else u - v)(
                auc(x[i], y[i]), auc(b_[i], y[i])))
            dD = pop.boot(lambda i: corp(x[i], y[i])["dsc"] - corp(b_[i], y[i])["dsc"])
            tests.append(("P2b", f"dAUC {lab}", dA))
            tests.append(("P2b", f"dDSC {lab}", dD))
            p2b[lab] = {"dAUC": dA, "dDSC": dD, "auc_x": auc(x, y), "auc_book": auc(b_, y),
                        "games": pop.games}
            out(f"   {lab:<14} AUC {auc(x, y):.4f} vs book {auc(b_, y):.4f}   dAUC {fmt(dA)}   dDSC {fmt(dD)}")
        result["p2b"] = p2b

    # ---- P3: week 1, model only
    if p3:
        pop = Pop("P3 week 1 2026 (market = stale candle, descriptive)", p3, "m", "k")
        m, y, s = pop.m, pop.y, pop.s
        cm, ck = corp(m, y), corp(pop.k, y)
        p3r = {"n": pop.n, "games": pop.games,
               "auc_m": pop.boot(lambda i: auc(m[i], y[i])),
               "wauc_m": pop.boot(lambda i: wauc(m[i], y[i], s[i])),
               "mcb_m": pop.boot(lambda i: corp(m[i], y[i])["mcb"]),
               "dsc_m": pop.boot(lambda i: corp(m[i], y[i])["dsc"]),
               "corp_model": cm, "corp_stale_market": ck,
               "auc_stale_market": auc(pop.k, y), "wauc_stale_market": wauc(pop.k, y, s),
               "brier_m": brier(m, y), "brier_stale_market": brier(pop.k, y)}
        out(f"\n== {pop.name}: n {pop.n}  games {pop.games}")
        out(f"   model AUC {fmt(p3r['auc_m'])}   wAUC {fmt(p3r['wauc_m'])}")
        out(f"   model CORP BS {cm['bs']:.4f} MCB {cm['mcb']:.4f} DSC {cm['dsc']:.4f} UNC {cm['unc']:.4f}")
        out(f"   model MCB {fmt(p3r['mcb_m'])}   DSC {fmt(p3r['dsc_m'])}")
        out(f"   [stale candle, descriptive only] AUC {p3r['auc_stale_market']:.4f}  wAUC "
            f"{p3r['wauc_stale_market']:.4f}  Brier {p3r['brier_stale_market']:.4f}  "
            f"MCB {ck['mcb']:.4f} DSC {ck['dsc']:.4f}")
        result["p3"] = p3r

    # ---- recalibration, out of sample
    result["recal"] = {}
    P1arr = lambda rs, f: np.array([r[f] for r in rs], float)  # noqa: E731
    for T, train_seasons in ((2024, (2023,)), (2025, (2023, 2024))):
        if f"P1-{T}" not in pops or not all(f"P1-{s}" in pops for s in train_seasons):
            continue
        tr = [r for r in p1 if r["season"] in train_seasons]
        result["recal"][f"R1-{T}"] = recal(f"R1-{T} (train {'+'.join(map(str, train_seasons))})",
                                            P1arr(tr, "m"), P1arr(tr, "y"), pops[f"P1-{T}"], out, tests,
                                            train_k=P1arr(tr, "k"))
    if "P2" in pops and "P1-pooled" in pops:
        result["recal"]["R2"] = recal("R2 (train P1 2023-2025 -> eval P2)", P1arr(p1, "m"), P1arr(p1, "y"),
                                      pops["P2"], out, tests, train_k=P1arr(p1, "k"))
        w2 = [r for r in p2 if r["week"] == 2]
        w3 = [r for r in p2 if r["week"] == 3]
        result["recal"]["R3"] = recal("R3 (train P2 week 2 -> eval week 3)", P1arr(w2, "m"), P1arr(w2, "y"),
                                      Pop("P2 week 3", w3, "m", "k"), out, tests, train_k=P1arr(w2, "k"))
    if "P1-2023" in pops:
        rs = [r for r in p1 if r["season"] == 2023]
        fold = lambda r: zlib.crc32(r["game"].encode()) % 2  # noqa: E731
        # two-fold cross-fit: each fold scored by the map fitted on the other,
        # then the two scored halves are pooled into one eval population.
        sub = {}
        for mname, fitter in (("platt", platt_fit), ("isotonic", iso_fit)):
            scored = []
            for f_ in (0, 1):
                tr = [r for r in rs if fold(r) != f_]
                ev = [r for r in rs if fold(r) == f_]
                fm, _ = fitter(P1arr(tr, "m"), P1arr(tr, "y"))
                fk, _ = fitter(P1arr(tr, "k"), P1arr(tr, "y"))
                rm, rk = fm(P1arr(ev, "m")), fk(P1arr(ev, "k"))
                for r, a1, a2 in zip(ev, rm, rk):
                    scored.append(dict(r, rm=float(a1), rk=float(a2)))
            pop = Pop("R4", scored, "m", "k")
            rm = np.array([r["rm"] for r in scored])
            rk = np.array([r["rk"] for r in scored])
            y, k, m = pop.y, pop.k, pop.m
            aa = pop.boot(lambda i: brier(rm[i], y[i]) - brier(k[i], y[i]))
            bb = pop.boot(lambda i: brier(rm[i], y[i]) - brier(m[i], y[i]))
            cc = pop.boot(lambda i: brier(rm[i], y[i]) - brier(rk[i], y[i]))
            tests.append(("R4-2023", f"{mname}: Brier(recal model) - Brier(market)", aa))
            tests.append(("R4-2023", f"{mname}: Brier(recal model) - Brier(raw model)", bb))
            sub[mname] = {"recal_minus_market": aa, "recal_minus_raw": bb, "recal_minus_recal_market": cc,
                          "brier_recal": brier(rm, y), "brier_raw": brier(m, y), "brier_market": brier(k, y)}
            out(f"\n-- recalibration R4-2023 two-fold cross-fit {mname}: Brier recal {brier(rm, y):.4f} raw "
                f"{brier(m, y):.4f} market {brier(k, y):.4f}")
            out(f"   recal - market {fmt(aa)}\n   recal - raw    {fmt(bb)}\n   recal - recal market {fmt(cc)}")
        result["recal"]["R4-2023"] = sub

    # ---- POST-HOC, NOT REGISTERED (added after the registered run, labelled so).
    # Two readings of the registered recalibration verdicts need context the
    # registration did not ask for: (1) how close a recalibrated model is to a
    # CONSTANT forecast (the training base rate), and (2) whether "closes the
    # gap to within detection" on R3's 15 games is recalibration or power - the
    # raw gap on the SAME eval rows answers that. Not counted among the 49.
    out("\n== POST-HOC (not registered, descriptive context for the recalibration verdicts) ==")
    ph = {}
    splits = []
    for T, trs in ((2024, (2023,)), (2025, (2023, 2024))):
        if f"P1-{T}" in pops:
            splits.append((f"R1-{T}", [r for r in p1 if r["season"] in trs], pops[f"P1-{T}"]))
    if "P2" in pops:
        splits.append(("R2", p1, pops["P2"]))
        splits.append(("R3", [r for r in p2 if r["week"] == 2],
                       Pop("P2 week 3", [r for r in p2 if r["week"] == 3], "m", "k")))
    for name, tr, ev in splits:
        base = float(np.mean([r["y"] for r in tr]))
        y, k, m = ev.y, ev.k, ev.m
        fm, _ = platt_fit(P1arr(tr, "m"), P1arr(tr, "y"))
        rm = fm(m)
        cb = ev.boot(lambda i: brier(rm[i], y[i]) - brier(np.full(len(i), base), y[i]))
        kb = ev.boot(lambda i: brier(k[i], y[i]) - brier(np.full(len(i), base), y[i]))
        raw = ev.boot(lambda i: brier(m[i], y[i]) - brier(k[i], y[i]))
        ph[name] = {"train_base_rate": base, "brier_constant": brier(np.full(ev.n, base), y),
                    "platt_minus_constant": cb, "market_minus_constant": kb, "raw_model_minus_market": raw}
        out(f"   {name}: constant (train base rate {base:.4f}) Brier {ph[name]['brier_constant']:.4f}")
        out(f"      Platt model - constant {fmt(cb)}")
        out(f"      market - constant      {fmt(kb)}")
        out(f"      RAW model - market on these eval rows {fmt(raw)}")
    result["posthoc"] = ph

    out("\n== SUMMARY ==")
    out(f"   intervals computed {len(tests)} (declared 49); excluding zero "
        f"{sum(1 for *_x, r in tests if r and r.get('lo') is not None and (r['lo'] > 0 or r['hi'] < 0))}")
    result["tests"] = [{"pop": p, "name": n, **(r or {})} for p, n, r in tests]
    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(result, f, indent=1, default=float)
    return 0


if __name__ == "__main__":
    sys.exit(main())
