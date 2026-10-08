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
MIN_BLOCKS = 5


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
                                                " *" if r["excludes_zero"] else
                                                ("  (<%d blocks: NOT READ)" % MIN_BLOCKS
                                                 if r["excludes_zero"] is None else ""))


def reproduce(name, measured, published, dp):
    """Does `measured` equal `published` at the precision the claim was stated to?"""
    ok = round(measured, dp) == round(published, dp)
    return {"name": name, "measured": float(measured), "published": published, "dp": dp, "reproduces": bool(ok)}


class VacuousCheck(RuntimeError):
    pass


def duplication(*_a, **_k):
    """RETIRED by f-27, and it raises rather than returning so no stale call site
    keeps printing a pass. It duplicated rows inside their block and resampled
    with THIS file's block bootstrap, so `width_ratio_blocked` was 1.000 by
    construction and `passes` could not be False for any target: the attacker
    was testing the attacker. It printed x1.000 (passes) on all 15 statistics of
    f-26 run 1."""
    raise VacuousCheck("f26lib.duplication() cannot fail and is retired. Use duplication_through() with the "
                       "TARGET's bootstrap function for the test, and iid_contrast() for the descriptive ratio.")


def iid_contrast(stat, n, labels, draws=DRAWS, seed=0):
    """DESCRIPTIVE, NOT A TEST - it has no pass/fail key on purpose. How much
    narrower would this statistic's interval be if rows were resampled singly
    instead of by `labels`? ~1.0 means the blocking is immaterial here (one row
    per block, or no within-block dependence); well below 1 means an unblocked
    interval would have overstated the evidence by that factor. It says nothing
    about what the target did - duplication_through() does."""
    labels = list(labels)
    base = summ(stat(np.arange(n)), block_boot(stat, blocks_of(labels), draws, seed))
    iid = summ(base["est"], iid_boot(stat, n, draws, seed))
    return {"blocked": base, "iid": iid, "iid_over_blocked_width": iid["width"] / base["width"],
            "rows": n, "blocks": len(set(labels))}


def alt_blocks(stat, n, labelings, draws=DRAWS, seed=0):
    """{name: labels} -> the same statistic under coarser blocks. A result that
    needs the finest block to exclude zero is carried by dependence it ignored."""
    est = stat(np.arange(n))
    out = {}
    for name, labels in labelings.items():
        b = blocks_of(labels)
        r = dict(summ(est, block_boot(stat, b, draws, seed)), n_blocks=len(b), read=len(b) >= MIN_BLOCKS)
        if not r["read"]:
            r["excludes_zero"] = None                     # an interval over <5 blocks is not read (brief 020)
        out[name] = r
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


def multiplicity(est, se, ks, n_blocks=None):
    """Bonferroni over k specifications on z = est / bootstrap SE (a percentile
    p floors at 1/draws and cannot be corrected - CLAUDE.md, brief 022).
    A zero-variance bootstrap and an interval on fewer than MIN_BLOCKS blocks
    enter at p = 1, never at p = 0. k < 1 is refused: a count that was not
    found is not a count of one."""
    ks = tuple(ks)
    if not ks or any((not isinstance(k, (int, np.integer))) or k < 1 for k in ks):
        raise ValueError("multiplicity needs the specification count(s) as integers >= 1, got %r" % (ks,))
    degenerate = (not se) or se != se or (n_blocks is not None and n_blocks < MIN_BLOCKS)
    z = float("nan") if degenerate else est / se
    p = 1.0 if degenerate else _p_two_sided(z)
    return {"z": z, "p": p, "degenerate": bool(degenerate),
            "bonferroni": {int(k): {"p_adj": min(1.0, p * k), "survives_0.05": bool(p * k < 0.05)} for k in ks}}


def registered_count(recorded, what):
    """The target's OWN count of the intervals it registered, or a refusal. f-26's adapters
    read it with `.get(...) or 0` and then took max(k, 2): a result file without the key
    would have been corrected over 2 and printed as 'its own 0 intervals'."""
    k = ((recorded or {}).get("registered_intervals") or {}).get("count")
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise SystemExit("%s: no registered_intervals.count in the recorded result - refusing to correct over a "
                         "made-up k" % what)
    return k


def mde_ratio(est, se):
    """|estimate| over its own minimum detectable effect at 80% power (2.8 SE).
    Three readings, because one flag for two of them misreads a null as a result:
      below  (< 0.8)    the population could not have resolved an effect this size - a null
      at     (0.8-1.25) the estimate is conditioned on having cleared the bar: a coin flip
      clear  (>= 1.25)  the estimate is larger than what the population can just detect

    NOT AN INDEPENDENT STEP (f-27): the ratio is |z| / 2.8, a relabelling of the z
    that multiplicity() already used. An interval that excludes zero has |z| > ~1.96,
    so its ratio is > ~0.70 and a headline that excludes zero can essentially only
    read `at` or `clear`. What CAN disagree with the target is `mde_claim()` below.
    """
    mde = 2.8 * se
    r = abs(est) / mde if mde else float("nan")
    reading = "below" if r < 0.8 else ("at" if r < 1.25 else "clear")
    return {"mde": mde, "ratio": r, "reading": reading, "at_mde": bool(reading == "at")}


def mde_claim(stated_mde, se, tol=0.10):
    """Does the MDE the target STATED equal 2.8 x the SE re-measured here? This
    is the part of step 5 that can contradict the target: a unit that quoted a
    smaller MDE than its data supports called a null 'powered'."""
    mine = 2.8 * se
    ok = stated_mde is not None and mine > 0 and abs(stated_mde - mine) <= tol * mine
    return {"stated": stated_mde, "remeasured": mine,
            "rel_diff": None if not stated_mde or not mine else (stated_mde - mine) / mine, "consistent": bool(ok)}


def same_within_mc(measured, published, se, tol=0.25):
    """An interval bound redrawn under a DIFFERENT seed is not a reproduction failure
    when it sits within `tol` bootstrap SEs of the published bound."""
    return {"measured": float(measured), "published": published, "abs_diff": abs(measured - published),
            "tolerance": tol * se, "consistent": bool(abs(measured - published) <= tol * se)}


def duplication_through(width_of, rows, block_key, k=DUP, fn_name=None, units=None):
    """THE blocks test: the duplication run THROUGH THE TARGET'S OWN bootstrap.

    `width_of(rows) -> hi - lo` must call the function the target's published
    interval came from (name it in `fn_name`). It is handed `rows`, then the
    same rows k times with their block label kept, then a twin where every copy
    is its own block. A function that honours blocks returns x1.0 on the first
    and ~1/sqrt(k) on the twin; one that resamples rows narrows on BOTH, and
    that is the finding.

    `units` = distinct dependence units among `rows`, counted by the ATTACKER
    (distinct games). A row bootstrap is still a valid game bootstrap when
    rows == units, so the verdict needs that number and refuses without it:
        honours_blocks             copies inside a block did not narrow it
        row_bootstrap_one_per_unit narrows on copies, but rows == units here
        NARROWS                    narrows on copies and rows > units: the published
                                   interval is too narrow by about sqrt(rows / units)

    WHAT THIS DOES NOT TEST: whether the block LABEL is the real dependence unit.
    Copies carry their row's label, so a target that labelled every rung its own
    'game' passes here. That is `units` (counted independently) and alt_blocks().
    """
    if units is None or not fn_name:
        raise ValueError("duplication_through needs `fn_name` (the target's function) and `units` "
                         "(distinct dependence units, counted by the attacker)")
    rows = list(rows)
    w0 = width_of(rows)
    same = width_of([dict(r) for r in rows for _ in range(k)])
    own = width_of([dict(r, **{block_key: "%s#%d" % (r[block_key], j)}) for r in rows for j in range(k)])
    honours = bool(same / w0 > 0.9)
    verdict = "honours_blocks" if honours else ("row_bootstrap_one_per_unit" if len(rows) == units else "NARROWS")
    return {"fn": fn_name, "k": k, "rows": len(rows), "units": units, "width": w0,
            "ratio_copies_in_block": same / w0, "ratio_copies_as_new_blocks": own / w0,
            "expected_new_blocks": 1 / math.sqrt(k),
            "passes": honours, "discriminates": bool(own / w0 < 0.6), "verdict": verdict,
            "survives": verdict != "NARROWS"}


def through_line(t):
    return ("THROUGH %s (%d rows, %d units): copies inside their block x%.3f, copies as new blocks x%.3f "
            "(expected %.3f) -> %s" % (t["fn"], t["rows"], t["units"], t["ratio_copies_in_block"],
                                       t["ratio_copies_as_new_blocks"], t["expected_new_blocks"], t["verdict"]))


def require_through(claims, through):
    """An adapter's blocks step is not done until EVERY claim it attacks has a
    through-the-target result. A guard asserts only over the shapes it walks, so
    this one reports the gap instead of passing on the subset."""
    missing = [c for c in claims if c not in through]
    if missing:
        raise SystemExit("blocks step incomplete: no through-the-target duplication for %s" % missing)
    return {c: through[c]["verdict"] for c in claims}
