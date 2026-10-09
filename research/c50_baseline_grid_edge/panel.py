"""c-50 - the seed panel and the registered verdict words. Pure: arrays in, dicts out.

PRE-REGISTRATION: docs/C50-baseline-grid-edge-preregistration.md (bc7357b). The
resample is c-28's (`research.ranking_calibration.Pop.boot`): one generator per
seed, each draw `rng.integers(0, G, G)` over blocks, percentile 2.5 / 97.5.
"""
from __future__ import annotations

import math

import numpy as np

SEEDS = tuple(range(1, 201))
DRAWS = 2000
SHARE = 0.95
PUBLISHED_SEED = 24          # research.ranking_calibration.SEED

BELOW, ABOVE, CONTAINS, DEPENDS = "below", "above", "contains 0", "seed-dependent"


def blocks_of(keys):
    """Row-index arrays per block, in sorted key order - `rc.Pop`'s order."""
    by = {}
    for i, k in enumerate(keys):
        by.setdefault(k, []).append(i)
    return [np.array(v) for _k, v in sorted(by.items())]


def sign(lo, hi):
    return BELOW if hi < 0 else ABOVE if lo > 0 else CONTAINS


def panel(D, blocks, seeds=None, draws=None):
    """D is (rows x columns): one column per comparison on the SAME rows, so
    every column sees the same resamples. -> one summary dict per column."""
    seeds = SEEDS if seeds is None else seeds
    draws = DRAWS if draws is None else draws
    D = np.asarray(D, float)
    if D.ndim == 1:
        D = D[:, None]
    bsum = np.array([D[b].sum(axis=0) for b in blocks])        # blocks x columns
    bcnt = np.array([len(b) for b in blocks], float)
    G, C = len(blocks), D.shape[1]
    if G < 2:
        raise ValueError("a panel over %d block(s) has no interval" % G)
    vals = np.empty((len(seeds), draws, C))
    for a, seed in enumerate(seeds):
        rng = np.random.default_rng(seed)
        for t in range(draws):
            pick = rng.integers(0, G, G)
            vals[a, t] = bsum[pick].sum(axis=0) / bcnt[pick].sum()
    out = []
    for c in range(C):
        v = vals[:, :, c]
        lo = np.percentile(v, 2.5, axis=1)
        hi = np.percentile(v, 97.5, axis=1)
        signs = [sign(l, h) for l, h in zip(lo, hi)]
        n = len(seeds)
        share = {s: signs.count(s) / n for s in (BELOW, CONTAINS, ABOVE)}
        flat = v.ravel()
        pooled = {"lo": float(np.percentile(flat, 2.5)), "hi": float(np.percentile(flat, 97.5)),
                  "se": float(flat.std()), "draws": int(flat.size)}
        pooled["sign"] = sign(pooled["lo"], pooled["hi"])
        est = float(D[:, c].mean())
        z = est / pooled["se"] if pooled["se"] > 1e-15 else None
        out.append({
            "est": est, "rows": int(D.shape[0]), "blocks": G, "seeds": n, "draws_per_seed": draws,
            "per_seed": [{"seed": int(s), "lo": float(l), "hi": float(h), "sign": g}
                         for s, l, h, g in zip(seeds, lo, hi, signs)],
            "lo_min_med_max": [float(lo.min()), float(np.median(lo)), float(lo.max())],
            "hi_min_med_max": [float(hi.min()), float(np.median(hi)), float(hi.max())],
            "count": {s: signs.count(s) for s in (BELOW, CONTAINS, ABOVE)},
            "share": share, "pooled": pooled,
            "est_over_mde": abs(est) / (2.8 * pooled["se"]) if z is not None else None,
            "z": z, "p_two_sided": math.erfc(abs(z) / math.sqrt(2)) if z is not None else None,
            "category": category(share, pooled["lo"], pooled["hi"]),
        })
    return out


def category(share, pooled_lo, pooled_hi, threshold=None):
    """The registered four-way reading of one cell: `below` / `above` / `contains 0`
    need the threshold share of seeds AND the pooled interval; anything else is
    seed-dependent."""
    threshold = SHARE if threshold is None else threshold
    ps = sign(pooled_lo, pooled_hi)
    for s in (BELOW, ABOVE, CONTAINS):
        if share[s] >= threshold - 1e-12 and ps == s:
            return s
    return DEPENDS


SENTENCE = {BELOW: "better than", ABOVE: "worse than", CONTAINS: "no better than"}


def weeks_verdict(cat_p, cat_s, cat_p_week_blocks, k_interior):
    """The weeks 1-4 sentence at arm P, with the qualifiers that travel with it."""
    if cat_p == DEPENDS:
        return {"verdict": "SEED-DEPENDENT AT THIS POWER", "sentence": None, "qualifiers": []}
    q = []
    if cat_p in (BELOW, ABOVE):
        word = "CHANGED - " + SENTENCE[cat_p]
        if cat_s != cat_p:
            q.append("specification-dependent")
        if cat_p_week_blocks != cat_p:
            q.append("block-dependent")
        if not k_interior:
            q.append("at a widened grid whose optimum is not interior")
    else:
        word = "UNCHANGED - " + SENTENCE[cat_p]
    return {"verdict": word, "sentence": SENTENCE[cat_p], "qualifiers": q}


def headline_verdict(cat_p, pooled_sign):
    """c-28's headline against plain Elo at arm P, against f-26's 'unchanged'."""
    if cat_p == BELOW:
        return "CONFIRMS f-26 (unchanged)"
    if pooled_sign != BELOW:
        return "CONTRADICTS f-26"
    return "seed-dependent"


def edges(value, grid):
    """'' | 'min' | 'max' for a fitted value on its grid."""
    return "min" if value == min(grid) else "max" if value == max(grid) else ""
