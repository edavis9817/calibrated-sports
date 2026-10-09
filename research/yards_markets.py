"""c-37 - a hurdle-gamma yardage model: shape fit first, then the book close.

    set LOGGER_DB=D:/calibrated-sports/data/market_log.db   (opened mode=ro only)
    python -m research.yards_markets --smoke --out-dir D:/temp/c37     # Part 1, target 2022
    python -m research.yards_markets --out-dir D:/temp/c37 --workers 10

PRE-REGISTRATION: docs/C37-yards-markets-preregistration.md, committed at
4134f35 BEFORE this script existed. This file implements it and its addendum 1;
it does not extend them. The scoring is research.ranking_calibration (c-24) and
the frame is research.decomposed_usage.Panel (c-27), both IMPORTED, never copied.

Everything PRINTED is an aggregate or an interval (BET_LIST_RESTRICTION). The
per-outcome predictions go to --out-dir, which is scratch and never committed.
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np
from scipy import stats as sps
from scipy.optimize import minimize
from scipy.special import gamma as gammafn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.distributions import ZeroInflatedGamma  # noqa: E402
from models import baseline  # noqa: E402
from research import decomposed_usage as du  # noqa: E402  (the c-27 frame)
from research import ranking_calibration as rc  # noqa: E402  (the c-24 scoring code)
from research.walkforward import LeakError  # noqa: E402

TEST_SEASONS = (2023, 2024, 2025)
FIRST_TRAIN = 2016
SHAPE_TRAIN_SPAN = 3                  # shape / recalibration rows: T-3 .. T-1
GRID = 500                            # CRPS grid, yards 0..500
MIN_OWN = 4
SEED = 37
STATS = {"receiving_yards": {"col": "ryd", "pos": ("WR", "TE", "RB"), "opp": "tgt"},
         "rush_yards": {"col": "rsh", "pos": ("RB",), "opp": "car"}}
ARMS = ("S0", "S1", "Y", "Y-lognormal", "Y-weibull")
QS = (0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
KS_OK, COV_OK = 0.03, 0.02


# =============================================================================
# the distribution - pure, vectorised
# =============================================================================

def pos_cdf(fam, mup, disp, x):
    """G(x): the CONTINUOUS positive-part CDF with mean `mup`, dispersion `disp`."""
    x = np.maximum(x, 0.0)
    if fam == "gamma":
        return sps.gamma.cdf(x, disp, scale=mup / disp)
    if fam == "lognormal":
        return sps.lognorm.cdf(x, disp, scale=mup * np.exp(-disp ** 2 / 2))
    if fam == "weibull":
        return sps.weibull_min.cdf(x, disp, scale=mup / gammafn(1 + 1 / disp))
    raise ValueError(fam)


def cdf_int(arm, k):
    """P(X <= k) for integer k >= 0 (X <= 0 is the hurdle mass)."""
    return arm["pi0"] + (1 - arm["pi0"]) * pos_cdf(arm["fam"], arm["mup"], arm["disp"], k + 0.5)


def pmf_pos(fam, mup, disp, y):
    """Mass of integer y >= 1 GIVEN X > 0; G(0.5) is folded into y = 1."""
    hi = pos_cdf(fam, mup, disp, y + 0.5)
    lo = np.where(y >= 2, pos_cdf(fam, mup, disp, y - 0.5), 0.0)
    return hi - lo


def prob_over(arm, line, push):
    """P(over wins | not a push) at `line`; arrays. An integer line with the
    push flag is conditioned on no push; without the flag `actual == line`
    settles OVER (core.settlement.resolve), so its mass is added."""
    line = np.asarray(line, float)
    fl = np.floor(line)
    is_int = fl == line
    over = (1 - arm["pi0"]) * (1 - pos_cdf(arm["fam"], arm["mup"], arm["disp"], fl + 0.5))
    lo = np.where(fl >= 2, pos_cdf(arm["fam"], arm["mup"], arm["disp"], fl - 0.5), 0.0)
    at = np.where(is_int & (fl >= 1),
                  (1 - arm["pi0"]) * (pos_cdf(arm["fam"], arm["mup"], arm["disp"], fl + 0.5) - lo), 0.0)
    push = np.asarray(push, bool)
    p = np.where(is_int & push, over / np.maximum(1 - at, 1e-12), np.where(is_int, over + at, over))
    return np.clip(p, 0.0, 1.0)


def pit(arm, y, rng):
    """Randomised PIT: uniform within the mass of the realised integer."""
    y = np.maximum(np.asarray(y, float), 0.0)
    hi = np.where(y <= 0, arm["pi0"], cdf_int(arm, y))
    lo = np.where(y <= 0, 0.0, np.where(y >= 2, cdf_int(arm, y - 1), arm["pi0"]))
    return lo + rng.random(len(y)) * (hi - lo)


def logscore(arm, y):
    y = np.maximum(np.asarray(y, float), 0.0)
    m = np.where(y <= 0, arm["pi0"],
                 (1 - arm["pi0"]) * pmf_pos(arm["fam"], arm["mup"], arm["disp"], np.maximum(y, 1)))
    return -np.log(np.maximum(m, 1e-12))


def crps_from_cdf(F, y):
    """F: (n, GRID+1) of P(X <= k), k = 0..GRID. y clipped to [0, GRID]."""
    y = np.clip(np.asarray(y, float), 0, GRID)
    ind = (np.arange(GRID + 1)[None, :] >= y[:, None]).astype(float)
    return ((F - ind) ** 2).sum(axis=1)


def crps(arm, y, chunk=4000):
    out = np.empty(len(y))
    ks = np.arange(GRID + 1)[None, :]
    for a in range(0, len(y), chunk):
        sub = {k: (v[a:a + chunk, None] if isinstance(v, np.ndarray) else v) for k, v in arm.items()}
        out[a:a + chunk] = crps_from_cdf(cdf_int(sub, ks), y[a:a + chunk])
    return out


# =============================================================================
# the frame: as-of features on c-27's Panel
# =============================================================================

def build_rows(panel, fc, stat, seasons):
    """One row per PLAYED player-game at the stat's positions in `seasons`,
    with features from games kicked off strictly earlier, seasons (S-1, S)."""
    cfg = STATS[stat]
    col = cfg["col"]
    seasons = set(seasons)
    rows = []
    for gs, games in panel.player.items():
        for i, (k, s, g, r) in enumerate(games):
            if s not in seasons or r["pos"] not in cfg["pos"]:
                continue
            n = sy = syy = sz = snaps = 0.0
            vals = []
            for j in range(i - 1, -1, -1):
                kj, sj, _gj, rj = games[j]
                if sj < s - 1:
                    break
                if kj >= k:
                    raise LeakError(f"window game at {kj} not before kickoff {k}")
                w = du.PREV_W if sj == s - 1 else 1.0
                v = float(rj[col])
                n += w; sy += w * v; syy += w * v * v; sz += w * (v <= 0)  # noqa: E702
                snaps += w * (rj["snaps"] or 0)
                vals.append(v)
            if n == 0:
                bucket = 2
            else:
                mates = fc._team_players(r["team"], s, k)
                bucket = min(1 + sum(1 for g2, (p2, sn) in mates.items()
                                     if g2 != gs and p2 == r["pos"] and sn > snaps), 3)
            ybar = sy / n if n else 0.0
            rows.append({"gsis": gs, "game": g, "season": s, "kick": k, "pos": r["pos"],
                         "pb": f"{r['pos']}|{bucket}", "n": n, "ybar": ybar,
                         "z": sz / n if n else 0.0,
                         "pvar": max(syy / n - ybar * ybar, 0.0) if n else 0.0,
                         "vals": vals, "y": float(r[col]), "opp": r[cfg["opp"]]})
    return rows


def fit_tables(train, T):
    """Prior tables and shrinkage strengths from frame rows in seasons < T."""
    if not train or max(r["season"] for r in train) >= T:
        raise LeakError(f"prior tables for {T} need training rows, all from seasons < {T}")
    tab = {"m0": {}, "z0": {}, "v0": {}, "k_y": {}, "k_z": {}, "within": {}, "emp": {}}
    by = defaultdict(list)
    ps = defaultdict(list)
    for r in sorted(train, key=lambda r: r["kick"]):
        by[r["pb"]].append(r)
        ps[(r["gsis"], r["season"], r["pos"])].append(r)
    dev = defaultdict(list)
    for (_g, _s, _p), rs in ps.items():
        if len(rs) >= 2:
            m = np.mean([r["y"] for r in rs])
            for r in rs:
                dev[r["pb"]].append((r["y"] - m) ** 2 * len(rs) / (len(rs) - 1))
    for pb, rs in by.items():
        y = np.array([r["y"] for r in rs])
        tab["m0"][pb] = float(y.mean())
        tab["z0"][pb] = float((y <= 0).mean())
        tab["v0"][pb] = float(np.mean(dev[pb])) if dev[pb] else float(y.var())
        tab["emp"][pb] = np.sort(np.clip(y, 0, GRID))
    for pos in {p for (_g, _s, p) in ps}:
        gy = [[r["y"] for r in rs] for (_g, _s, p), rs in ps.items() if p == pos]
        gz = [[float(r["y"] <= 0) for r in rs] for (_g, _s, p), rs in ps.items() if p == pos]
        my, mz = du.mom(gy), du.mom(gz)
        tab["k_y"][pos] = my["k"] if np.isfinite(my["k"]) else 1e6
        tab["k_z"][pos] = mz["k"] if np.isfinite(mz["k"]) else 1e6
        tab["within"][pos] = my["within"]
    tab["fit_seasons"] = sorted({r["season"] for r in train})
    return tab


def centre(rows, tab):
    """-> arrays mu_hat, pi_hat, mup_hat, var_s0 (S0's overall variance)."""
    n = np.array([r["n"] for r in rows])
    ybar = np.array([r["ybar"] for r in rows])
    z = np.array([r["z"] for r in rows])
    pvar = np.array([r["pvar"] for r in rows])
    fb = lambda d, r: d.get(r["pb"], d.get(f"{r['pos']}|2"))  # noqa: E731
    m0 = np.array([fb(tab["m0"], r) for r in rows])
    z0 = np.array([fb(tab["z0"], r) for r in rows])
    v0 = np.array([fb(tab["v0"], r) for r in rows])
    ky = np.array([tab["k_y"][r["pos"]] for r in rows])
    kz = np.array([tab["k_z"][r["pos"]] for r in rows])
    within = np.array([tab["within"][r["pos"]] for r in rows])
    mu = np.maximum((n * ybar + ky * m0) / (n + ky), 0.5)
    pi = np.clip((n * z + kz * z0) / (n + kz), 0.002, 0.95)
    wv = n / (n + baseline.SHRINK_GAMES_VMR)
    var = wv * pvar + (1 - wv) * v0 + within / (n + ky)
    return mu, pi, mu / (1 - pi), var


def _nll(fam, mup, disp, y):
    return -np.log(np.maximum(pmf_pos(fam, mup, disp, y), 1e-300)).sum()


def _disp(c, lm):
    return np.exp(np.clip(c[0] + c[1] * lm, -3.0, 4.0))


def fit_shapes(train, tab, T):
    """ML shape and recalibration parameters from rows in T-3..T-1."""
    seas = sorted({r["season"] for r in train})
    if not seas or max(seas) >= T:
        raise LeakError(f"shape parameters for {T} fitted on {seas}")
    _mu, pi, mup, _v = centre(train, tab)
    y = np.array([r["y"] for r in train])
    posm = y > 0
    lm, yp = np.log(mup[posm]), y[posm]
    opt = {"maxiter": 4000, "xatol": 1e-5, "fatol": 1e-4}
    s1 = minimize(lambda c: _nll("gamma", np.exp(lm), _disp(c, lm), yp), [0.4, 0.0],
                  method="Nelder-Mead", options=opt)
    yy = minimize(lambda c: _nll("gamma", np.exp(c[0] + c[1] * lm), _disp(c[2:], c[0] + c[1] * lm), yp),
                  [0.0, 1.0, 0.4, 0.0], method="Nelder-Mead", options=opt)
    b = yy.x[:2]
    lmy = b[0] + b[1] * lm
    ln = minimize(lambda c: _nll("lognormal", np.exp(lmy), _disp(c, lmy), yp), [-0.2, 0.0],
                  method="Nelder-Mead", options=opt)
    wb = minimize(lambda c: _nll("weibull", np.exp(lmy), _disp(c, lmy), yp), [0.2, 0.0],
                  method="Nelder-Mead", options=opt)
    _f, ab = rc.platt_fit(pi, (y <= 0).astype(float))
    return {"S1": [float(x) for x in s1.x], "Y": [float(x) for x in yy.x],
            "Y-lognormal": [float(x) for x in ln.x], "Y-weibull": [float(x) for x in wb.x],
            "hurdle": [float(x) for x in ab], "fit_seasons": seas, "n_pos": int(posm.sum()),
            "n": len(train), "nll_per_pos": {"S1": float(s1.fun / posm.sum()), "Y": float(yy.fun / posm.sum()),
                                             "Y-lognormal": float(ln.fun / posm.sum()),
                                             "Y-weibull": float(wb.fun / posm.sum())}}


def arms_for(rows, tab, sh):
    mu, pi, mup, var = centre(rows, tab)
    out = {}
    zig = [ZeroInflatedGamma.from_overall_moments(float(m), float(v), float(p))
           for m, v, p in zip(mu, var, pi)]
    shape0 = np.array([d._shape for d in zig])
    scale0 = np.array([d._scale for d in zig])
    out["S0"] = {"fam": "gamma", "pi0": pi, "mup": shape0 * scale0, "disp": shape0}
    lm = np.log(mup)
    out["S1"] = {"fam": "gamma", "pi0": pi, "mup": mup, "disp": _disp(sh["S1"], lm)}
    a, b = sh["hurdle"]
    piy = np.clip(1 / (1 + np.exp(-(a + b * rc.logit(pi)))), rc.CLIP, 1 - rc.CLIP)
    lmy = sh["Y"][0] + sh["Y"][1] * lm
    out["Y"] = {"fam": "gamma", "pi0": piy, "mup": np.exp(lmy), "disp": _disp(sh["Y"][2:], lmy)}
    out["Y-lognormal"] = {"fam": "lognormal", "pi0": piy, "mup": np.exp(lmy), "disp": _disp(sh["Y-lognormal"], lmy)}
    out["Y-weibull"] = {"fam": "weibull", "pi0": piy, "mup": np.exp(lmy), "disp": _disp(sh["Y-weibull"], lmy)}
    return out


# =============================================================================
# block bootstrap of a mean over games - vectorised
# =============================================================================

def boot_mean(d, games, draws=rc.BOOT, seed=rc.SEED):
    d = np.asarray(d, float)
    ug, inv = np.unique(np.asarray(games), return_inverse=True)
    G = len(ug)
    if not len(d):
        return None
    S = np.bincount(inv, weights=d, minlength=G)
    C = np.bincount(inv, minlength=G).astype(float)
    est = float(d.mean())
    if G < 2:
        return {"est": est, "lo": None, "hi": None, "se": None, "games": G, "n": len(d)}
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, G, (draws, G))
    v = S[pick].sum(axis=1) / C[pick].sum(axis=1)
    return {"est": est, "lo": float(np.percentile(v, 2.5)), "hi": float(np.percentile(v, 97.5)),
            "se": float(v.std()), "games": G, "n": len(d), "mde": float(2.8 * v.std())}


# =============================================================================
# Part 1 - the shape fit
# =============================================================================

def naive_crps(rows, tab):
    """CRPS of N-pos (training empirical by position|bucket) and N-own (the
    player's own window, n >= MIN_OWN games, else nan)."""
    ks = np.arange(GRID + 1)
    cache = {}
    npos = np.empty(len(rows))
    nown = np.full(len(rows), np.nan)
    for i, r in enumerate(rows):
        pb = r["pb"] if r["pb"] in tab["emp"] else f"{r['pos']}|2"
        if pb not in cache:
            e = tab["emp"][pb]
            cache[pb] = np.searchsorted(e, ks, side="right") / len(e)
        yv = min(max(r["y"], 0), GRID)
        ind = (ks >= yv).astype(float)
        npos[i] = ((cache[pb] - ind) ** 2).sum()
        if len(r["vals"]) >= MIN_OWN:
            e = np.sort(np.clip(r["vals"], 0, GRID))
            nown[i] = ((np.searchsorted(e, ks, side="right") / len(e) - ind) ** 2).sum()
    return npos, nown


def shape_table(name, mask, arms, y, u, out):
    res = {"n": int(mask.sum())}
    out(f"\n   {name}: n {res['n']:,}   realized share <= 0: {(y[mask] <= 0).mean():.4f}   "
        f"mean {y[mask].mean():.2f}")
    out(f"   {'arm':<12} {'KS':>6} {'maxbin':>7} " + " ".join(f"q{int(q * 100):02d}" .rjust(6) for q in QS)
        + f" {'pi0':>7} {'logS':>7} {'CRPS':>7}")
    for a in ARMS:
        uu = np.sort(u[a][mask])
        ks = float(np.max(np.abs(np.arange(1, len(uu) + 1) / len(uu) - uu)))
        hist = np.histogram(uu, bins=10, range=(0, 1))[0] / len(uu)
        cov = [float((uu <= q).mean()) for q in QS]
        res[a] = {"ks": ks, "maxbin": float(np.abs(hist - 0.1).max()), "hist": hist.tolist(), "cov": cov,
                  "pi0_mean": float(arms[a]["pi0"][mask].mean()), "logscore": float(arms[a]["_ls"][mask].mean()),
                  "crps": float(arms[a]["_crps"][mask].mean())}
        out(f"   {a:<12} {ks:6.4f} {res[a]['maxbin']:7.4f} " + " ".join(f"{c:6.3f}" for c in cov)
            + f" {res[a]['pi0_mean']:7.4f} {res[a]['logscore']:7.4f} {res[a]['crps']:7.3f}")
    return res


def part1(stat, frames, preds, priced_keys, out, tests):
    """frames: {T: rows}, preds: {T: (arms, tab)}. Pooled over the test seasons."""
    rows = [r for T in sorted(frames) for r in frames[T]]
    y = np.array([r["y"] for r in rows])
    games = np.array([r["game"] for r in rows])
    arms = {a: {k: (np.concatenate([np.broadcast_to(preds[T][0][a][k], (len(frames[T]),)) for T in sorted(frames)])
                    if k != "fam" else preds[sorted(frames)[0]][0][a][k])
                for k in ("fam", "pi0", "mup", "disp")} for a in ARMS}
    rng = np.random.default_rng(SEED)
    u = {}
    for a in ARMS:
        u[a] = pit(arms[a], y, rng)
        arms[a]["_ls"] = logscore(arms[a], y)
        arms[a]["_crps"] = crps(arms[a], y)
    nv = [naive_crps(frames[T], preds[T][1]) for T in sorted(frames)]
    npos = np.concatenate([x[0] for x in nv])
    nown = np.concatenate([x[1] for x in nv])
    n = np.array([r["n"] for r in rows])
    res = {"seasons": sorted(frames)}
    out(f"\n== PART 1 shape fit: {stat}, seasons {sorted(frames)} (randomised PIT; coverage = share of PIT <= q)")
    allm = np.ones(len(rows), bool)
    res["F"] = shape_table("F  all played player-games", allm, arms, y, u, out)
    res["F_n4"] = shape_table("F  as-of n >= 4", n >= 4, arms, y, u, out)
    res["F_nlt4"] = shape_table("F  as-of n < 4", n < 4, arms, y, u, out)
    if priced_keys:
        pm = np.array([(r["gsis"], r["game"]) in priced_keys for r in rows])
        if pm.sum():
            res["priced"] = shape_table("priced player-games (>= 1 rung)", pm, arms, y, u, out)
    out(f"   naive CRPS: N-pos {npos.mean():.3f} (all)   N-own {np.nanmean(nown):.3f} "
        f"(n {int(np.isfinite(nown).sum()):,} with >= {MIN_OWN} window games)")
    res["naive_crps"] = {"N-pos": float(npos.mean()), "N-own": float(np.nanmean(nown))}
    cy = arms["Y"]["_crps"]
    own = np.isfinite(nown)
    res["tests"] = {}
    for label, d, g in (("CRPS Y - N-pos", cy - npos, games),
                        ("CRPS Y - N-own", (cy - nown)[own], games[own]),
                        ("CRPS Y - S0", cy - arms["S0"]["_crps"], games),
                        ("CRPS Y - S1", cy - arms["S1"]["_crps"], games),
                        ("CRPS S1 - S0", arms["S1"]["_crps"] - arms["S0"]["_crps"], games)):
        r = boot_mean(d, g)
        res["tests"][label] = r
        tests.append((f"P1 {stat}", label, r))
        out(f"   {label:<16} {rc.fmt(r, 3)}  MDE {r['mde']:.3f}")
    fy = res["F"]["Y"]
    bad = [f"q{int(q * 100)} {c:.3f}" for q, c in zip(QS, fy["cov"]) if abs(c - q) > COV_OK]
    fits = fy["ks"] <= KS_OK and not bad
    res["verdict"] = "FITS" if fits else "DOES NOT FIT"
    res["verdict_detail"] = {"ks": fy["ks"], "outside": bad}
    out(f"   REGISTERED SHAPE VERDICT ({stat}, Y, F pooled): {res['verdict']}  "
        f"KS {fy['ks']:.4f} (bar {KS_OK}); coverage outside +/-{COV_OK}: {bad or 'none'}")
    for a in ("S0", "S1", "Y-lognormal", "Y-weibull"):
        fa = res["F"][a]
        b2 = [f"q{int(q * 100)} {c:.3f}" for q, c in zip(QS, fa["cov"]) if abs(c - q) > COV_OK]
        out(f"      {a:<12} KS {fa['ks']:.4f}; outside: {b2 or 'none'}")
    return res


# =============================================================================
# Part 2 - receiving yards against the close
# =============================================================================

CLOSE_SQL = """SELECT o.outcome_id, o.key, o.sport, o.season, o.week, o.entity_type, o.entity_id,
       o.stat, o.line, o.side, o.push_possible, o.event_id,
       oc.p_bench, oc.n_bench, oc.p_all, oc.n_all
  FROM outcome_close oc JOIN outcomes o USING (outcome_id)
 WHERE o.season = ? AND o.side = 'over' AND o.stat = 'receiving_yards' AND oc.lead_min <= 15"""


def load_rungs(con, panel, T, census):
    """Settled, non-push, regular-season rungs of season T. Every row is
    settled through jobs.settle_outcomes.settle_one (the shared rule)."""
    from jobs.settle_outcomes import settle_one, OVER, UNDER
    rows = []
    for r in con.execute(CLOSE_SQL, (T,)).fetchall():
        (oid, key, sport, season, week, etype, gsis, stat, line, side, push, game,
         pb, nb, pa, na) = r
        census["rungs"] += 1
        if game not in panel.games:
            census["not a regular-season game (dropped)"] += 1
            continue
        result, actual, _v, _void = settle_one(con, (oid, key, sport, season, week, etype, gsis,
                                                     stat, line, side, push))
        if result not in (OVER, UNDER):
            census[f"settle_one: {result} (dropped)"] += 1
            continue
        census["settled over/under"] += 1
        rows.append({"oid": oid, "season": T, "gsis": gsis, "game": game, "stat": "receiving_yards",
                     "line": float(line), "push": bool(push), "y": 1.0 if result == OVER else 0.0,
                     "actual": actual, "p_bench": pb, "n_bench": nb, "p_all": pa, "n_all": na})
    return rows


def naive_prior(panel, T):
    """-> fn(gsis, line): research.score.naive_prob on season T-1 (yards, targets)."""
    from research.score import naive_prob
    hist = defaultdict(list)
    pool = []
    for gs, games in panel.player.items():
        for k, s, g, r in games:
            if s == T - 1:
                hist[gs].append((r["ryd"], r["tgt"]))
                if r["tgt"] >= 1:
                    pool.append(r["ryd"])
    pool = np.sort(np.array(pool, float))
    if not len(pool):
        raise SystemExit(f"no season {T - 1} rows for the naive prior - refusing")

    def f(gsis, line):
        hits = len(pool) - int(np.searchsorted(pool, line, side="right"))
        return naive_prob(hist.get(gsis, []), (hits, len(pool)), line)[0]
    return f


def modal_rungs(rows):
    """One claim per player-game: the rung with the largest n_all; ties to the
    rung nearest the n_all-weighted mean line, then to the lower line."""
    by = defaultdict(list)
    for r in rows:
        if r["p_all"] is not None:
            by[(r["gsis"], r["game"])].append(r)
    out = []
    for _key, rs in sorted(by.items()):
        tot = sum(r["n_all"] for r in rs)
        mean_line = sum(r["n_all"] * r["line"] for r in rs) / tot
        out.append(min(rs, key=lambda r: (-r["n_all"], abs(r["line"] - mean_line), r["line"])))
    return out


def _stat(name, a, b, y):
    if name == "brier":
        return rc.brier(a, y) - rc.brier(b, y)
    if name == "mcb":
        return rc.corp(a, y)["mcb"] - rc.corp(b, y)["mcb"]
    if name == "dsc":
        return rc.corp(a, y)["dsc"] - rc.corp(b, y)["dsc"]
    raise ValueError(name)


def _job(spec):
    """Run in a worker. ('disc', name, rows, mkey, kkey) or
    ('diff', label, rows, stat, akey, bkey) or ('level', label, rows, stat, akey)."""
    kind = spec[0]
    if kind == "disc":
        _k, name, rows, mkey, kkey = spec
        lines, tests = [], []
        res = rc.discriminate(rc.Pop(name, rows, mkey, kkey), lines.append, tests)
        return {"kind": kind, "name": name, "lines": lines, "res": res,
                "tests": [(p, n, r) for p, n, r in tests]}
    if kind == "diff":
        _k, label, rows, stat, akey, bkey = spec
        pop = rc.Pop(label, rows, akey, bkey)
        a, b, y = pop.m, pop.k, pop.y
        r = pop.boot(lambda i: _stat(stat, a[i], b[i], y[i]))
        r["mde"] = 2.8 * r["se"] if r.get("se") else None
        return {"kind": kind, "name": label, "r": r}
    _k, label, rows, stat, akey = spec
    pop = rc.Pop(label, rows, akey, akey)
    a, y = pop.m, pop.y
    fn = {"mcb": lambda i: rc.corp(a[i], y[i])["mcb"], "dsc": lambda i: rc.corp(a[i], y[i])["dsc"],
          "auc": lambda i: rc.auc(a[i], y[i]), "brier": lambda i: rc.brier(a[i], y[i])}[stat]
    return {"kind": kind, "name": label, "r": pop.boot(fn)}


def slim(rows, keys):
    return [{k: r[k] for k in ("game", "stat", "line", "y") + tuple(keys)} for r in rows]


def part2(rungs, out, tests, workers):
    res = {}
    RB = [r for r in rungs if r["p_bench"] is not None]
    RA = [r for r in rungs if r["p_all"] is not None]
    RM = modal_rungs(rungs)
    if not RB or not RM:
        raise SystemExit("Part 2 population is EMPTY - refusing to report")
    nb = Counter(r["n_bench"] for r in RB)
    res["counts"] = {"RB": len(RB), "RA": len(RA), "RM": len(RM),
                     "RB_games": len({r["game"] for r in RB}),
                     "RB_player_games": len({(r["gsis"], r["game"]) for r in RB}),
                     "RA_player_games": len({(r["gsis"], r["game"]) for r in RA}),
                     "RB_n_bench": {str(k): v for k, v in sorted(nb.items())},
                     "RM_single_book_share": float(np.mean([r["n_all"] == 1 for r in RM])),
                     "rungs_per_player_game_RA": len(RA) / max(len({(r['gsis'], r['game']) for r in RA}), 1),
                     "integer_lines": int(sum(1 for r in rungs if float(r["line"]).is_integer()))}
    out(f"\n== PART 2 populations: RB {len(RB):,} rungs ({res['counts']['RB_player_games']:,} player-games, "
        f"{res['counts']['RB_games']} games)   RA {len(RA):,}   RM {len(RM):,} player-games")
    out(f"   RB rungs by n_bench books: {dict(sorted(nb.items()))}   "
        f"RM modal rung is one book: {res['counts']['RM_single_book_share']:.3f}   "
        f"RA rungs per player-game {res['counts']['rungs_per_player_game_RA']:.2f}   "
        f"integer lines {res['counts']['integer_lines']}")
    specs = []
    for T in TEST_SEASONS:
        specs.append(("disc", f"RB-{T} Y vs p_bench", slim([r for r in RB if r["season"] == T], ("Y", "p_bench")),
                      "Y", "p_bench"))
    specs.append(("disc", "RB-pooled Y vs p_bench", slim(RB, ("Y", "p_bench")), "Y", "p_bench"))
    specs.append(("disc", "RA-pooled Y vs p_all", slim(RA, ("Y", "p_all")), "Y", "p_all"))
    specs.append(("disc", "RM-pooled Y vs p_all (modal line)", slim(RM, ("Y", "p_all")), "Y", "p_all"))
    rbs = slim(RB, ("S0", "S1", "Y"))
    for st in ("mcb", "dsc"):
        specs.append(("diff", f"RB-pooled {st.upper()} S1 - S0 (shape, centre fixed)", rbs, st, "S1", "S0"))
        specs.append(("diff", f"RB-pooled {st.upper()} Y - S1 (centre, family fixed)", rbs, st, "Y", "S1"))
    for arm in ("S0", "S1", "Y"):
        for st in ("mcb", "dsc", "auc"):
            specs.append(("level", f"RB-pooled {st.upper()} {arm}", rbs, st, arm))
    t0 = time.time()
    if workers > 1:
        import multiprocessing as mp
        with mp.Pool(min(workers, len(specs))) as pool:
            done = pool.map(_job, specs, chunksize=1)
    else:
        done = [_job(s) for s in specs]
    out(f"   (bootstraps: {len(specs)} jobs in {time.time() - t0:.0f}s)")
    res["disc"], res["decomp"], res["levels"] = {}, {}, {}
    for d in done:
        if d["kind"] == "disc":
            for ln in d["lines"]:
                out(ln)
            res["disc"][d["name"]] = d["res"]
            if d["name"].startswith("RB-pooled"):
                mde = d["res"]["diffs"]["dDSC"]["mde"]
                res["dDSC_mde_pooled"] = mde
                out(f"   REALIZED pooled dDSC MDE {mde:.4f} (expected ~0.0015; under-powered bar 0.0030): "
                    f"{'UNDER-POWERED' if mde > 0.003 else 'powered as registered'}")
            tests.extend(d["tests"])
    # cheap Brier differences (vectorised block bootstrap of a mean)
    out("\n== naive baselines and decomposition (Brier differences; negative = first is better)")
    res["brier"] = {}

    def bd(label, rows, a, b, declared=True):
        y = np.array([r["y"] for r in rows])
        d = (np.array([r[a] for r in rows]) - y) ** 2 - (np.array([r[b] for r in rows]) - y) ** 2
        r = boot_mean(d, [r["game"] for r in rows])
        res["brier"][label] = r
        if declared:
            tests.append(("P2", label, r))
        out(f"   {label:<58} {rc.fmt(r)}  MDE {r['mde']:.4f}  n {r['n']:,}")
        return r
    for T in TEST_SEASONS + ("pooled",):
        sub = RB if T == "pooled" else [r for r in RB if r["season"] == T]
        bd(f"RB-{T} Brier Y - N-half", sub, "Y", "half")
        bd(f"RB-{T} Brier Y - N-prior", sub, "Y", "prior")
    bd("RM Brier Y - N-half", RM, "Y", "half")
    bd("RM Brier Y - N-prior", RM, "Y", "prior")
    bd("RB-pooled Brier S1 - S0 (shape, centre fixed)", RB, "S1", "S0")
    bd("RB-pooled Brier Y - S1 (centre, family fixed)", RB, "Y", "S1")
    bd("RB-pooled Brier Y-lognormal - Y", RB, "Y-lognormal", "Y")
    bd("RB-pooled Brier Y-weibull - Y", RB, "Y-weibull", "Y")
    out("   -- descriptive, not among the 58 declared:")
    for lab, a, b in (("RB-pooled Brier Y - p_bench", "Y", "p_bench"), ("RB-pooled Brier S0 - p_bench", "S0", "p_bench"),
                      ("RB-pooled Brier N-prior - p_bench", "prior", "p_bench"),
                      ("RB-pooled Brier N-half - p_bench", "half", "p_bench"),
                      ("RB-pooled Brier p_bench - N-half", "p_bench", "half")):
        bd(lab, RB, a, b, declared=False)
    bd("RM Brier Y - p_all", RM, "Y", "p_all", declared=False)
    bd("RM single-book modal rung: Brier Y - p_all", [r for r in RM if r["n_all"] == 1], "Y", "p_all", declared=False)
    bd("RM multi-book modal rung:  Brier Y - p_all", [r for r in RM if r["n_all"] >= 2], "Y", "p_all", declared=False)
    for d in done:
        if d["kind"] == "diff":
            res["decomp"][d["name"]] = d["r"]
            tests.append(("P2", d["name"], d["r"]))
            out(f"   {d['name']:<58} {rc.fmt(d['r'])}  MDE {d['r']['mde']:.4f}")
    out("   levels on RB pooled (bootstrap intervals):")
    for d in done:
        if d["kind"] == "level":
            res["levels"][d["name"]] = d["r"]
            r = d["r"]
            out(f"      {d['name']:<26} {r['est']:.4f} [{r['lo']:.4f}, {r['hi']:.4f}]")
    y = np.array([r["y"] for r in RB])
    out("   point levels on RB pooled:  " + "   ".join(
        f"{k} Brier {rc.brier(np.array([r[k] for r in RB]), y):.4f}" for k in ("half", "prior", "S0", "S1", "Y", "p_bench")))
    res["point_brier"] = {k: rc.brier(np.array([r[k] for r in RB]), y) for k in
                          ("half", "prior", "S0", "S1", "Y", "Y-lognormal", "Y-weibull", "p_bench")}
    res["over_rate_RB"] = float(y.mean())
    # verdicts
    b = res["brier"]
    below = lambda r: r["hi"] is not None and r["hi"] < 0  # noqa: E731
    ok_half = below(b["RB-pooled Brier Y - N-half"]) and b["RM Brier Y - N-half"]["est"] < 0
    ok_prior = below(b["RB-pooled Brier Y - N-prior"]) and b["RM Brier Y - N-prior"]["est"] < 0
    res["success"] = {"beats_N_half": bool(ok_half), "beats_N_prior": bool(ok_prior),
                      "verdict": "beats the naive baselines" if ok_half and ok_prior else
                      "does not beat the naive baselines: fails against "
                      + ", ".join(n for n, k in (("N-half", ok_half), ("N-prior", ok_prior)) if not k)}
    out(f"\n   REGISTERED SUCCESS CONDITION: {res['success']['verdict']}")
    dp = res["disc"]["RB-pooled Y vs p_bench"]["diffs"]
    word = {"below": "resolves worse than the close", "contains 0": "not distinguishable from the close",
            "above": "a candidate, not an edge", "not read": "not read"}
    res["close"] = {"dDSC": word[rc.sign(dp["dDSC"])], "dAUC": word[rc.sign(dp["dAUC"])],
                    "d_wAUC": word[rc.sign(dp["d_wAUC"])]}
    out(f"   SECONDARY (the close), RB pooled: dDSC -> {res['close']['dDSC']} (MDE {dp['dDSC']['mde']:.4f}); "
        f"dAUC -> {res['close']['dAUC']} (MDE {dp['dAUC']['mde']:.4f}); d_wAUC -> {res['close']['d_wAUC']}")
    return res


# =============================================================================
# main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True, help="scratch; never committed")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--smoke", action="store_true", help="Part 1 only, target season 2022 (not a test season)")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    log = open(os.path.join(a.out_dir, "smoke.log" if a.smoke else "run.log"), "w", encoding="utf-8")

    def out(s):
        print(s, flush=True)
        log.write(s + "\n")
        log.flush()
    t0 = time.time()
    con = du.ro()
    panel = du.Panel(con)
    fc = du.Forecaster(panel, None)
    out(f"panel: {len(panel.pg):,} player-games, census {dict(panel.census)}  ({time.time() - t0:.0f}s)")
    targets = (2022,) if a.smoke else TEST_SEASONS
    tests, result = [], {"prereg": "4134f35", "targets": list(targets)}
    all_rungs = []
    for stat in STATS:
        rows = build_rows(panel, fc, stat, range(FIRST_TRAIN, max(targets) + 1))
        out(f"\n#### {stat}: {len(rows):,} frame rows {FIRST_TRAIN}-{max(targets)}  ({time.time() - t0:.0f}s)")
        frames, preds, consts = {}, {}, {}
        for T in targets:
            tab = fit_tables([r for r in rows if r["season"] < T], T)
            sh = fit_shapes([r for r in rows if T - SHAPE_TRAIN_SPAN <= r["season"] < T], tab, T)
            frames[T] = [r for r in rows if r["season"] == T]
            preds[T] = (arms_for(frames[T], tab, sh), tab)
            consts[T] = {"k_y": tab["k_y"], "k_z": tab["k_z"], "m0": tab["m0"], "z0": tab["z0"],
                         "prior_fit_seasons": tab["fit_seasons"], "shapes": sh}
            out(f"   T={T}: priors on {tab['fit_seasons'][0]}-{tab['fit_seasons'][-1]}, shapes on {sh['fit_seasons']} "
                f"(n {sh['n']:,}, positive {sh['n_pos']:,})")
            out(f"      k_y {({k: round(v, 1) for k, v in tab['k_y'].items()})}  k_z "
                f"{({k: round(v, 1) for k, v in tab['k_z'].items()})}")
            out(f"      S1 (c0, c1) {np.round(sh['S1'], 3).tolist()}   Y (b0, b1, c0, c1) {np.round(sh['Y'], 3).tolist()}"
                f"   hurdle (a, b) {np.round(sh['hurdle'], 3).tolist()}")
            out(f"      train NLL per positive: " + "  ".join(f"{k} {v:.4f}" for k, v in sh["nll_per_pos"].items()))
            mid = float(np.median(preds[T][0]["Y"]["disp"]))
            out(f"      median gamma shape of Y on season {T}: {mid:.3f}")
        result.setdefault("constants", {})[stat] = consts
        priced = set()
        if stat == "receiving_yards" and not a.smoke:
            census = Counter()
            for T in targets:
                rungs = load_rungs(con, panel, T, census)
                idx = {(r["gsis"], r["game"]): i for i, r in enumerate(frames[T])}
                keep = [r for r in rungs if (r["gsis"], r["game"]) in idx]
                census["settled but not in the frame (position not WR/TE/RB or no panel row; dropped)"] += \
                    len(rungs) - len(keep)
                ii = np.array([idx[(r["gsis"], r["game"])] for r in keep])
                line = np.array([r["line"] for r in keep])
                push = np.array([r["push"] for r in keep])
                np_ = naive_prior(panel, T)
                for arm in ARMS:
                    A = preds[T][0][arm]
                    sub = {"fam": A["fam"], "pi0": A["pi0"][ii], "mup": A["mup"][ii], "disp": A["disp"][ii]}
                    p = prob_over(sub, line, push)
                    for r, v in zip(keep, p):
                        r[arm] = float(v)
                for r in keep:
                    r["half"] = 0.5
                    r["prior"] = float(np_(r["gsis"], r["line"]))
                    fr = frames[T][idx[(r["gsis"], r["game"])]]
                    if (fr["y"] > r["line"]) != bool(r["y"]):
                        raise SystemExit("panel actual disagrees with settle_one - refusing")
                    priced.add((r["gsis"], r["game"]))
                all_rungs += keep
                out(f"   rungs {T}: {len(keep):,} scored")
            out(f"   rung census: {dict(census)}")
            result["rung_census"] = dict(census)
        result.setdefault("part1", {})[stat] = part1(stat, frames, preds, priced, out, tests)
    if not a.smoke:
        result["part2"] = part2(all_rungs, out, tests, a.workers)
        import csv
        with open(os.path.join(a.out_dir, "predictions.csv"), "w", newline="", encoding="utf-8") as f:
            cols = ["season", "game", "gsis", "line", "y", "p_bench", "n_bench", "p_all", "n_all",
                    "half", "prior"] + list(ARMS)
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows([[r[c] for c in cols] for r in all_rungs])
    n_ex = sum(1 for *_x, r in tests if r and r.get("lo") is not None and (r["lo"] > 0 or r["hi"] < 0))
    out(f"\n== SUMMARY: intervals computed {len(tests)} (declared 58); excluding zero {n_ex}; "
        f"runtime {time.time() - t0:.0f}s")
    result["tests"] = [{"pop": p, "name": n, **(r or {})} for p, n, r in tests]
    with open(os.path.join(a.out_dir, "smoke.json" if a.smoke else "result.json"), "w") as f:
        json.dump(result, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
