"""f-26 - the attack primitives every target adapter uses. No store access here.

Each primitive answers one line of the checklist in README.md and returns a
dict the adapter prints and writes; none of them decides a verdict.

A statistic is `stat(idx) -> float` over row indices, so the same primitive
serves a mean difference and a regression slope.
"""
import math

import numpy as np

DRAWS = 2000
DUP = 5


def blocks_of(labels):
    """[index array per distinct label], in sorted label order."""
    by = {}
    for i, b in enumerate(labels):
        by.setdefault(b, []).append(i)
    return [np.array(v) for _k, v in sorted(by.items(), key=lambda kv: str(kv[0]))]


def block_boot(stat, blocks, draws=DRAWS, seed=0):
    rng = np.random.default_rng(seed)
    G = len(blocks)
    out = np.empty(draws)
    single = np.array([b[0] for b in blocks]) if all(len(b) == 1 for b in blocks) else None
    for d in range(draws):
        pick = rng.integers(0, G, G)                      # same draw sequence either way
        out[d] = stat(single[pick] if single is not None else np.concatenate([blocks[j] for j in pick]))
    return out


def iid_boot(stat, n, draws=DRAWS, seed=0):
    rng = np.random.default_rng(seed)
    return np.array([stat(rng.integers(0, n, n)) for _ in range(draws)])


def summ(est, draws):
    lo, hi = np.percentile(draws, [2.5, 97.5])
    se = float(draws.std())
    return {"est": float(est), "lo": float(lo), "hi": float(hi), "se": se,
            "width": float(hi - lo), "excludes_zero": bool(lo > 0 or hi < 0)}


def fmt(r, d=4):
    return "%+.*f [%+.*f, %+.*f] SE %.*f%s" % (d, r["est"], d, r["lo"], d, r["hi"], d + 1, r["se"],
                                                " *" if r["excludes_zero"] else "")


def reproduce(name, measured, published, dp):
    """Does `measured` equal `published` at the precision the claim was stated to?"""
    ok = round(measured, dp) == round(published, dp)
    return {"name": name, "measured": float(measured), "published": published, "dp": dp, "reproduces": bool(ok)}


def duplication(stat, n, labels, draws=DRAWS, seed=0, k=DUP):
    """Every row k times inside its own block. A correctly blocked interval does
    not narrow; an iid row bootstrap narrows by about sqrt(k), which is what
    shows the check can return the other answer."""
    labels = list(labels)
    base = summ(stat(np.arange(n)), block_boot(stat, blocks_of(labels), draws, seed))
    rep = np.repeat(np.arange(n), k)                     # row -> original row
    dstat = lambda idx: stat(rep[idx])                   # noqa: E731
    dlabels = [labels[i] for i in rep]
    dup = summ(dstat(np.arange(len(rep))), block_boot(dstat, blocks_of(dlabels), draws, seed))
    iid0 = summ(base["est"], iid_boot(stat, n, draws, seed))
    iid5 = summ(base["est"], iid_boot(dstat, len(rep), draws, seed))
    ratio = dup["width"] / base["width"]
    iid_ratio = iid5["width"] / iid0["width"]
    return {"k": k, "blocked": base, "blocked_dup": dup, "width_ratio_blocked": ratio,
            "iid": iid0, "iid_dup": iid5, "width_ratio_iid": iid_ratio,
            "expected_iid_ratio": 1 / math.sqrt(k),
            "discriminates": bool(iid_ratio < 0.6),       # the wrong bootstrap DID narrow
            "passes": bool(ratio > 0.9)}


def alt_blocks(stat, n, labelings, draws=DRAWS, seed=0):
    """{name: labels} -> the same statistic under coarser blocks. A result that
    needs the finest block to exclude zero is carried by dependence it ignored."""
    est = stat(np.arange(n))
    out = {}
    for name, labels in labelings.items():
        b = blocks_of(labels)
        out[name] = dict(summ(est, block_boot(stat, b, draws, seed)), n_blocks=len(b))
    return out


def seeds(stat, n, labels, n_seeds=20, draws=DRAWS):
    b = blocks_of(labels)
    est = stat(np.arange(n))
    each = [summ(est, block_boot(stat, b, draws, 1000 + s)) for s in range(n_seeds)]
    return {"n_seeds": n_seeds, "share_excluding_zero": float(np.mean([e["excludes_zero"] for e in each])),
            "lo_min": min(e["lo"] for e in each), "lo_max": max(e["lo"] for e in each),
            "hi_min": min(e["hi"] for e in each), "hi_max": max(e["hi"] for e in each)}


def _p_two_sided(z):
    return math.erfc(abs(z) / math.sqrt(2.0))


def multiplicity(est, se, ks):
    """Bonferroni over k specifications on z = est / bootstrap SE (a percentile
    p floors at 1/draws and cannot be corrected - CLAUDE.md, brief 022)."""
    z = est / se if se else float("nan")
    p = _p_two_sided(z)
    return {"z": z, "p": p,
            "bonferroni": {int(k): {"p_adj": min(1.0, p * k), "survives_0.05": bool(p * k < 0.05)} for k in ks}}


def mde_ratio(est, se):
    """|estimate| over its own minimum detectable effect at 80% power (2.8 SE).
    Three readings, because one flag for two of them misreads a null as a result:
      below  (< 0.8)    the population could not have resolved an effect this size - a null
      at     (0.8-1.25) the estimate is conditioned on having cleared the bar: a coin flip
      clear  (>= 1.25)  the estimate is larger than what the population can just detect
    """
    mde = 2.8 * se
    r = abs(est) / mde if mde else float("nan")
    reading = "below" if r < 0.8 else ("at" if r < 1.25 else "clear")
    return {"mde": mde, "ratio": r, "reading": reading, "at_mde": bool(reading == "at")}


def same_within_mc(measured, published, se, tol=0.25):
    """An interval bound redrawn under a DIFFERENT seed is not a reproduction failure
    when it sits within `tol` bootstrap SEs of the published bound."""
    return {"measured": float(measured), "published": published, "abs_diff": abs(measured - published),
            "tolerance": tol * se, "consistent": bool(abs(measured - published) <= tol * se)}


def duplication_through(width_of, rows, block_key, k=DUP):
    """The duplication test run THROUGH THE TARGET'S OWN bootstrap function.

    `duplication()` above resamples with this file's bootstrap, so its blocked
    ratio is 1.000 by construction: it shows what a correct interval does, not
    what the target did. This one hands the target's function `rows` and then
    the same rows k times, and compares the widths it returns. The twin gives
    every copy its own block label, which a function that honours blocks must
    narrow on - that is the check returning its other answer.
    `width_of(rows) -> hi - lo`."""
    w0 = width_of(rows)
    same = width_of([dict(r) for r in rows for _ in range(k)])
    own = width_of([dict(r, **{block_key: "%s#%d" % (r[block_key], j)}) for r in rows for j in range(k)])
    return {"k": k, "width": w0, "ratio_copies_in_block": same / w0, "ratio_copies_as_new_blocks": own / w0,
            "expected_new_blocks": 1 / math.sqrt(k),
            "passes": bool(same / w0 > 0.9), "discriminates": bool(own / w0 < 0.6)}
