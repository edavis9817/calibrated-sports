"""f-23 - attack c-27's primary: dDSC(decomposed - baseline) on P1 pooled.

READ-ONLY. Reads c-27's per-outcome predictions (scratch, never committed) and
c-27's own scoring module from a checkout of origin/c-27-decomposed-usage:

    python -m research.f23_c27_attack.attack --c27-src D:/temp/f23/c27src \
        --pred D:/temp/f23/repro/predictions.csv --out D:/temp/f23/attack.json

Parts (each prints its own section):
  seeds    the registered interval re-drawn under 20 bootstrap seeds (2000 draws each)
  dup      rows duplicated 5x inside their week block must NOT narrow the interval;
           an iid row bootstrap is shown to narrow, so the check discriminates
  blocks   the same statistic blocked on player, team-less game, and player-season
  oos      DSC with the PAV recalibration fitted OUT of fold (leave-one-week-out),
           which removes PAV's in-sample optimism; and a shuffled-forecast null
  recal    is the Brier gain recalibration? decomposed vs an out-of-fold
           recalibrated baseline (isotonic and Platt)
"""
import argparse
import csv
import json
import time
from collections import defaultdict

import numpy as np
from scipy.optimize import isotonic_regression

DRAWS = 2000
SEED = 27


def load(path):
    rows = [r for r in csv.DictReader(open(path, newline="", encoding="utf-8")) if r["pop"] == "P1"]
    if len(rows) != 16041:
        raise SystemExit(f"expected 16,041 P1 rows, read {len(rows)} - refusing")
    return rows


# ---- fast CORP, asserted equal to c-27's rc.corp on the full sample ----------

def iso_fit_sorted(p, y):
    """PAV of y on p with identical p pooled first (rc.pav's convention).
    -> (unique p, fitted value per unique p)."""
    ux, inv = np.unique(p, return_inverse=True)
    w = np.bincount(inv).astype(float)
    sy = np.bincount(inv, weights=y)
    fit = isotonic_regression(sy / w, weights=w).x
    return ux, fit, inv


def dsc(p, y):
    ux, fit, inv = iso_fit_sorted(p, y)
    ybar = y.mean()
    return ybar * (1 - ybar) - np.mean((fit[inv] - y) ** 2)


def block_boot(stat, blocks, n_draws, seed):
    """stat(idx) -> float; blocks: list of index arrays. -> array of draws."""
    rng = np.random.default_rng(seed)
    G = len(blocks)
    out = np.empty(n_draws)
    for i in range(n_draws):
        pick = rng.integers(0, G, G)
        out[i] = stat(np.concatenate([blocks[j] for j in pick]))
    return out


def summ(est, draws):
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {"est": float(est), "lo": float(lo), "hi": float(hi), "se": float(draws.std()),
            "excludes_zero": bool(lo > 0 or hi < 0)}


def fmt(r):
    return f"{r['est']:+.5f} [{r['lo']:+.5f}, {r['hi']:+.5f}] SE {r['se']:.5f}" + (" *" if r["excludes_zero"] else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--c27-src", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", type=int, default=20)
    a = ap.parse_args()
    # c-27's scoring module, loaded by path: `research` here is this checkout's package
    import importlib.util
    spec = importlib.util.spec_from_file_location("c27_rc", f"{a.c27_src}/research/ranking_calibration.py")
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)

    t0 = time.time()
    out = lambda s: print(s, flush=True)  # noqa: E731
    R = {}
    rows = load(a.pred)
    d = np.array([float(r["decomposed"]) for r in rows])
    m = np.array([float(r["baseline"]) for r in rows])
    k = np.array([float(r["market"]) for r in rows])
    y = np.array([float(r["y"]) for r in rows])
    n = len(y)

    # equality with c-27's CORP
    for lab, p in (("decomposed", d), ("baseline", m), ("market", k)):
        ref = rc.corp(p, y)["dsc"]
        mine = dsc(p, y)
        if abs(ref - mine) > 1e-12:
            raise SystemExit(f"fast DSC != rc.corp on {lab}: {mine} vs {ref}")
    est = dsc(d, y) - dsc(m, y)
    out(f"fast CORP == rc.corp on all three forecasts (|diff| <= 1e-12); dDSC est {est:+.6f}")
    R["dDSC_est"] = est
    R["unique_values"] = {"decomposed": int(len(np.unique(d))), "baseline": int(len(np.unique(m))),
                          "market": int(len(np.unique(k)))}
    R["pav_blocks"] = {lab: int(len(np.unique(iso_fit_sorted(p, y)[1])))
                       for lab, p in (("decomposed", d), ("baseline", m))}
    out(f"unique forecast values {R['unique_values']}; PAV levels {R['pav_blocks']}")

    stat = lambda idx: dsc(d[idx], y[idx]) - dsc(m[idx], y[idx])  # noqa: E731
    wk = defaultdict(list)
    for i, r in enumerate(rows):
        wk[f"{r['season']}-{int(r['week']):02d}"].append(i)
    week_blocks = [np.array(v) for _k, v in sorted(wk.items())]
    out(f"week blocks: {len(week_blocks)} (c-27 reports 66)")
    R["week_blocks"] = len(week_blocks)

    # 0. the registered draw, exactly (seed 27 through rc.Pop.boot) - reproduces c-27
    pop = rc.Pop("P1", [dict(r, game=f"{r['season']}-{int(r['week']):02d}", m=0, k=0) for r in rows], "m", "k")
    pop.m, pop.k = d, m
    reg = pop.boot(lambda idx: stat(idx), draws=DRAWS, seed=SEED)
    out(f"\n== 0. registered interval via rc.Pop.boot seed 27: {reg['est']:+.6f} [{reg['lo']:+.6f}, {reg['hi']:+.6f}]")
    R["registered"] = reg

    # 1. seed sensitivity
    out(f"\n== 1. seeds: {a.seeds} x {DRAWS} draws, week blocks")
    seeds = []
    for s in range(a.seeds):
        dr = block_boot(stat, week_blocks, DRAWS, 1000 + s)
        seeds.append(summ(est, dr))
    los = np.array([x["lo"] for x in seeds])
    R["seeds"] = {"n": len(seeds), "lo_min": float(los.min()), "lo_median": float(np.median(los)),
                  "lo_max": float(los.max()), "share_lo_gt_0": float((los > 0).mean()),
                  "share_lo_gt_0_at_4dp": float((np.round(los, 4) > 0).mean()), "each": seeds}
    out(f"   lower bound over seeds: min {los.min():+.6f} median {np.median(los):+.6f} max {los.max():+.6f}")
    out(f"   share of seeds with lo > 0: {(los > 0).mean():.2f}; printed as >0 at 4 dp: {(np.round(los, 4) > 0).mean():.2f}")
    # one long bootstrap: the percentile with little Monte Carlo error left in it
    big = block_boot(stat, week_blocks, 20000, 777)
    R["pooled_20000"] = summ(est, big)
    R["pooled_20000"]["share_draws_le_0"] = float((big <= 0).mean())
    out(f"   one 20,000-draw bootstrap: {fmt(R['pooled_20000'])}; share of draws <= 0: {(big <= 0).mean():.4f}")

    # 2. duplication test
    out("\n== 2. duplicate every row 5x inside its week block")
    rep = 5
    d5, m5, y5 = np.repeat(d, rep), np.repeat(m, rep), np.repeat(y, rep)
    stat5 = lambda idx: dsc(d5[idx], y5[idx]) - dsc(m5[idx], y5[idx])  # noqa: E731
    blocks5 = [np.concatenate([np.arange(i * rep, i * rep + rep) for i in b]) for b in week_blocks]
    base = summ(est, block_boot(stat, week_blocks, 500, 5))
    dup = summ(stat5(np.arange(n * rep)), block_boot(stat5, blocks5, 500, 5))
    iid = summ(est, block_boot(stat, [np.array([i]) for i in range(n)], 500, 5))
    iid5 = summ(stat5(np.arange(n * rep)), block_boot(stat5, [np.array([i]) for i in range(n * rep)], 500, 5))
    R["dup"] = {"week_block_1x": base, "week_block_5x": dup, "iid_1x": iid, "iid_5x": iid5}
    out(f"   week-block, original      {fmt(base)}")
    out(f"   week-block, rows x5       {fmt(dup)}   (must not narrow)")
    out(f"   iid rows,   original      {fmt(iid)}")
    out(f"   iid rows,   rows x5       {fmt(iid5)}   (a non-blocking bootstrap narrows ~sqrt(5))")

    # 3. alternative blocks
    out("\n== 3. alternative blocks (2000 draws, seed 27)")
    def blocks_by(keyf):
        g = defaultdict(list)
        for i, r in enumerate(rows):
            g[keyf(r)].append(i)
        return [np.array(v) for _k, v in sorted(g.items())]
    alts = {"week": lambda r: (r["season"], r["week"]),
            "game": lambda r: r["game"],
            "player": lambda r: r["gsis"],
            "player-season": lambda r: (r["gsis"], r["season"]),
            "season-week-stat": lambda r: (r["season"], r["week"], r["stat"])}
    R["blocks"] = {}
    for lab, f in alts.items():
        bl = blocks_by(f)
        r_ = summ(est, block_boot(stat, bl, DRAWS, SEED))
        r_["blocks"] = len(bl)
        R["blocks"][lab] = r_
        out(f"   {lab:<18} {len(bl):>5} blocks  {fmt(r_)}")

    # 4. out-of-fold DSC (leave-one-week-out PAV), and a shuffled null
    out("\n== 4. PAV optimism: out-of-fold recalibration, and a shuffled-forecast null")
    wid = np.empty(n, int)
    for j, b in enumerate(week_blocks):
        wid[b] = j

    def oof(p):
        """leave-one-week-out isotonic recalibration of p; -> out-of-fold fitted values."""
        q = np.empty(n)
        for j, b in enumerate(week_blocks):
            tr = wid != j
            ux, fit, _inv = iso_fit_sorted(p[tr], y[tr])
            q[b] = np.interp(p[b], ux, fit)
        return q

    ybar = y.mean()
    unc = ybar * (1 - ybar)
    qd, qm, qk = oof(d), oof(m), oof(k)
    # per-week contribution of squared error, so a block bootstrap needs no refit
    ld, lm, lk = (qd - y) ** 2, (qm - y) ** 2, (qk - y) ** 2
    oos = {lab: float(unc - l.mean()) for lab, l in (("decomposed", ld), ("baseline", lm), ("market", lk))}
    ins = {"decomposed": float(dsc(d, y)), "baseline": float(dsc(m, y)), "market": float(dsc(k, y))}
    diff_w = lambda idx: lm[idx].mean() - ld[idx].mean()  # noqa: E731  (= dDSC_oos on the resample, fixed maps)
    r_oos = summ(lm.mean() - ld.mean(), block_boot(diff_w, week_blocks, DRAWS, SEED))
    R["oos"] = {"dsc_in_sample": ins, "dsc_out_of_fold": oos, "optimism": {k_: ins[k_] - oos[k_] for k_ in ins},
                "dDSC_oof": r_oos}
    for lab in ins:
        out(f"   {lab:<10} DSC in-sample {ins[lab]:+.5f}  out-of-fold {oos[lab]:+.5f}  optimism {ins[lab] - oos[lab]:+.5f}")
    out(f"   dDSC out-of-fold (decomposed - baseline), week-block, maps held fixed: {fmt(r_oos)}")
    rng = np.random.default_rng(99)
    null = {"decomposed": [], "baseline": []}
    for _ in range(200):
        perm = rng.permutation(n)
        null["decomposed"].append(dsc(d[perm], y))
        null["baseline"].append(dsc(m[perm], y))
    nd, nm = np.array(null["decomposed"]), np.array(null["baseline"])
    R["shuffle_null"] = {"decomposed_mean": float(nd.mean()), "baseline_mean": float(nm.mean()),
                         "diff_mean": float((nd - nm).mean()), "diff_sd": float((nd - nm).std())}
    out(f"   shuffled forecast (no information), 200 perms: DSC decomposed {nd.mean():.5f}, "
        f"baseline {nm.mean():.5f}, diff {(nd - nm).mean():+.5f} sd {(nd - nm).std():.5f}")

    # 5. is the Brier gain recalibration?
    out("\n== 5. Brier: decomposed vs an out-of-fold RECALIBRATED baseline")
    def oof_platt(p):
        q = np.empty(n)
        for j, b in enumerate(week_blocks):
            tr = wid != j
            f, _ab = rc.platt_fit(p[tr], y[tr])
            q[b] = f(p[b])
        return q
    pm = oof_platt(m)
    pd_ = oof_platt(d)
    bs = lambda p: float(np.mean((p - y) ** 2))  # noqa: E731
    R["recal"] = {"brier": {"baseline": bs(m), "decomposed": bs(d), "market": bs(k), "base_rate": float(unc),
                            "baseline_iso_oof": bs(qm), "baseline_platt_oof": bs(pm),
                            "decomposed_iso_oof": bs(qd), "decomposed_platt_oof": bs(pd_)}}
    b = R["recal"]["brier"]
    for kk, v in b.items():
        out(f"   Brier {kk:<22} {v:.5f}")
    dl = lambda p1, p2: (lambda idx: np.mean((p1[idx] - y[idx]) ** 2) - np.mean((p2[idx] - y[idx]) ** 2))  # noqa: E731
    for lab, (p1, p2) in {"decomposed - baseline (raw)": (d, m),
                          "decomposed - baseline_platt_oof": (d, pm),
                          "decomposed_platt_oof - baseline_platt_oof": (pd_, pm),
                          "decomposed_iso_oof - baseline_iso_oof": (qd, qm)}.items():
        f = dl(p1, p2)
        r_ = summ(f(np.arange(n)), block_boot(f, week_blocks, DRAWS, SEED))
        R["recal"][lab] = r_
        out(f"   dBrier {lab:<44} {fmt(r_)}")
    from scipy.stats import spearmanr
    R["spearman_d_m"] = float(spearmanr(d, m).statistic)
    out(f"   Spearman(decomposed, baseline) = {R['spearman_d_m']:.4f}")
    frac = -(R["dDSC_est"]) / (b["decomposed"] - b["baseline"])
    R["share_of_brier_gain_from_dsc"] = float(frac)
    out(f"   share of the raw Brier gain that is resolution: {frac:.3f}")

    json.dump(R, open(a.out, "w"), indent=1, default=float)
    out(f"\nwrote {a.out}; runtime {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
