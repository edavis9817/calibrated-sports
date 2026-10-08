"""f-27 - every step of the attack checklist, driven to its FAILING answer.

    python research/f26_reliability/selfcheck.py            # ~10 s, no store, no network

f-26 run 1 printed "x1.000 (passes)" on 18 statistics from a duplication test
that resampled with the attacker's own bootstrap and so could not fail. A check
that cannot fail is worse than none. This file plants, for each of the five
steps, a case the step MUST reject, and exits 1 if any step lets its plant
through. Run it before a run; `tests/test_f26_reliability.py` runs it too.

It also pins the blind spots found while planting - cases a step is KNOWN not
to see. They are asserted as blind so that nobody reads a clean result from
that step as covering them, and so a future fix has a test to flip.

Synthetic data only: rungs that share a game effect, a toy as-of pipeline.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f26lib as L  # noqa: E402


def clustered(n_blocks=80, per=8, seed=0, within=0.2):
    rng = np.random.default_rng(seed)
    eff = rng.normal(0.3, 1.0, n_blocks)
    v = np.concatenate([e + rng.normal(0, within, per) for e in eff])
    return v, [b for b in range(n_blocks) for _ in range(per)]


# two stand-ins for a TARGET's bootstrap: one honours the block key, one resamples rows
def target_blocked(rows, draws=300):
    v = np.array([r["v"] for r in rows])
    d = L.block_boot(lambda i: float(v[i].mean()), L.blocks_of([r["game"] for r in rows]), draws, 3)
    return float(np.percentile(d, 97.5) - np.percentile(d, 2.5))


def target_rows(rows, draws=300):
    v = np.array([r["v"] for r in rows])
    d = L.iid_boot(lambda i: float(v[i].mean()), len(v), draws, 3)
    return float(np.percentile(d, 97.5) - np.percentile(d, 2.5))


def toy_pipeline(results, when, leak=None):
    """Forecast for game i = mean of results strictly before it (as-of).
    leak='future'  -> the mean of ALL results (reads the future)
    leak='param'   -> as-of mean times a shrink chosen on the FULL sample from a 2-point grid"""
    order = np.argsort(when)
    out = np.zeros(len(results))
    shrink = 1.0
    if leak == "param":
        shrink = min((0.5, 1.0), key=lambda s: float(((results * s) ** 2).mean()))
    for rank, i in enumerate(order):
        prior = results[order[:rank]]
        out[i] = (results.mean() if leak == "future" else (prior.mean() if len(prior) else 0.0)) * shrink
    return out


def moved_before(base, scrambled, when, cutoff):
    return int(sum(1 for b, s, w in zip(base, scrambled, when) if w < cutoff and abs(b - s) > 1e-12))


def main():
    rows_out, bad = [], []

    def record(step, plant, expect_fires, fired, note=""):
        ok = (fired == expect_fires)
        rows_out.append((step, plant, expect_fires, fired, ok, note))
        if not ok:
            bad.append((step, plant))

    v, lab = clustered()
    n = len(v)
    stat = lambda i: float(v[i].mean())  # noqa: E731
    rows = [{"game": g, "v": float(x)} for g, x in zip(lab, v)]
    units = len(set(lab))
    blocked = L.summ(stat(np.arange(n)), L.block_boot(stat, L.blocks_of(lab), 2000, 1))
    iid = L.summ(blocked["est"], L.iid_boot(stat, n, 2000, 1))

    # ---------------------------------------------------------------- 2 blocks (the step that was vacuous)
    try:
        L.duplication(stat, n, lab)
        retired = False
    except L.VacuousCheck:
        retired = True
    record("2 blocks", "the retired f-26 duplication() is still callable", True, retired, "it raises VacuousCheck")
    t_bad = L.duplication_through(target_rows, rows, "game", fn_name="row bootstrap", units=units)
    record("2 blocks", "target resamples ROWS on 8 rungs a game", True, t_bad["verdict"] == "NARROWS",
           "copies x%.3f -> %s" % (t_bad["ratio_copies_in_block"], t_bad["verdict"]))
    t_ok = L.duplication_through(target_blocked, rows, "game", fn_name="block bootstrap", units=units)
    record("2 blocks", "target honours game blocks (must NOT fire)", False, not t_ok["survives"],
           "copies x%.3f, twin x%.3f -> %s" % (t_ok["ratio_copies_in_block"], t_ok["ratio_copies_as_new_blocks"], t_ok["verdict"]))
    one = [dict(r) for r in rows[::8]]
    t_one = L.duplication_through(target_rows, one, "game", fn_name="row bootstrap", units=len(one))
    record("2 blocks", "row bootstrap on ONE row a game (must NOT fire)", False, not t_one["survives"], t_one["verdict"])
    relabel = [dict(r, game="%s/%d" % (r["game"], j)) for j, r in enumerate(rows)]
    t_lab = L.duplication_through(target_blocked, relabel, "game", fn_name="block bootstrap", units=len(relabel))
    record("2 blocks", "BLIND SPOT: target labels every rung its own game, attacker counts units from the same label",
           False, not t_lab["survives"], "passes - the duplication cannot see a wrong LABEL; `units` must be counted "
           "independently (here it was not) and alt_blocks() run")
    t_lab2 = L.duplication_through(target_blocked, relabel, "game", fn_name="block bootstrap", units=units)
    record("2 blocks", "same wrong label, units counted independently (80 games)", False, not t_lab2["survives"],
           "STILL passes: verdict %s with rows %d != units %d - read rows vs units, not the verdict alone"
           % (t_lab2["verdict"], t_lab2["rows"], t_lab2["units"]))
    ab = L.alt_blocks(stat, n, {"mislabelled": list(range(n)), "game": lab}, draws=600, seed=2)
    record("2 blocks", "alt_blocks on the mislabelled rungs: game blocks must be wider than rung blocks", True,
           ab["game"]["width"] > 1.5 * ab["mislabelled"]["width"],
           "rung x1.00, game x%.2f" % (ab["game"]["width"] / ab["mislabelled"]["width"]))
    few = L.alt_blocks(stat, n, {"4 blocks": [g % 4 for g in lab]}, draws=300, seed=2)["4 blocks"]
    record("2 blocks", "an interval over 4 blocks is NOT READ", True, few["excludes_zero"] is None and not few["read"])

    # ---------------------------------------------------------------- 1 reproduce
    record("1 reproduce", "figure off by one unit in the last stated digit", True,
           not L.reproduce("x", -0.0027 - 0.0001, -0.0027, 4)["reproduces"])
    record("1 reproduce", "a figure moved by a rebuilt input (c-32's total, -0.019 vs -0.026)", True,
           not L.reproduce("x", -0.0189, -0.026, 3)["reproduces"])
    record("1 reproduce", "published GAME-block bound, re-measured with a row bootstrap (8 rungs a game)", True,
           not L.same_within_mc(iid["lo"], round(blocked["lo"], 4), blocked["se"])["consistent"],
           "row interval is x%.2f the width" % (iid["width"] / blocked["width"]))
    record("1 reproduce", "same seed re-draw of the true interval (must NOT fire)", False,
           not L.same_within_mc(blocked["lo"], round(blocked["lo"], 4), blocked["se"])["consistent"])
    # blind spot: weak clustering. The bound moves ~1.96 SE (1 - r); tolerance is 0.25 SE -> blind for r > ~0.87
    vw, labw = clustered(n_blocks=400, per=2, seed=5, within=2.1)
    sw = lambda i: float(vw[i].mean())  # noqa: E731
    bw = L.summ(sw(np.arange(len(vw))), L.block_boot(sw, L.blocks_of(labw), 2000, 1))
    iw = L.summ(bw["est"], L.iid_boot(sw, len(vw), 2000, 1))
    r = iw["width"] / bw["width"]
    record("1 reproduce", "BLIND SPOT: wrongly blocked interval when the design effect is small (width ratio %.2f)" % r,
           False, not L.same_within_mc(iw["lo"], bw["lo"], bw["se"])["consistent"],
           "an interval match within 0.25 SE cannot tell blocks from rows above ratio ~0.87; step 2 must")

    # ---------------------------------------------------------------- 3 leakage
    rng = np.random.default_rng(7)
    res = rng.normal(0, 1, 400)
    when = np.arange(400.0)
    cut = 250.0
    scr = res.copy()
    scr[when >= cut] = rng.normal(5, 1, int((when >= cut).sum()))
    for leak, expect, plant in ((None, False, "as-of pipeline (must NOT fire)"),
                                ("future", True, "forecast reads the full-sample mean")):
        mv = moved_before(toy_pipeline(res, when, leak), toy_pipeline(scr, when, leak), when, cut)
        record("3 leakage", plant, expect, mv > 0, "%d earlier forecasts moved" % mv)
    mv = moved_before(toy_pipeline(res, when, "param"), toy_pipeline(res + 0.0 * scr, when, "param"), when, cut)
    small = res.copy()
    small[when >= cut] = res[when >= cut] * 1.01
    mvp = moved_before(toy_pipeline(res, when, "param"), toy_pipeline(small, when, "param"), when, cut)
    record("3 leakage", "BLIND SPOT: a parameter fitted on the full sample whose grid argmin the scramble does not move",
           False, mvp > 0, "%d moved - the scramble test is blind to it; the truncated-refit audit (attack_c28 3c) is "
           "the check, and only c-28's adapter has one" % mvp)
    later = sum(1 for b, s, w in zip(toy_pipeline(res, when), toy_pipeline(scr, when), when) if w > cut and abs(b - s) > 1e-12)
    record("3 leakage", "'later forecasts moved' on the CLEAN pipeline", True, later > 0,
           "%d moved - this fires on a leak-free pipeline, so it proves the scramble ran, NOT that a leak would be seen" % later)

    # ---------------------------------------------------------------- 4 specifications
    record("4 specifications", "z 2.79 over 72 registered intervals (c-30's pooled figure)", True,
           not L.multiplicity(0.0009, 0.00033, (72,))["bonferroni"][72]["survives_0.05"])
    record("4 specifications", "z 4.1 over 72 (must NOT fire)", False,
           not L.multiplicity(0.0010, 0.00024, (72,))["bonferroni"][72]["survives_0.05"])
    record("4 specifications", "zero-variance bootstrap enters at p = 1", True,
           not L.multiplicity(0.05, 0.0, (1,))["bonferroni"][1]["survives_0.05"])
    record("4 specifications", "SE 1e-9 from 4 identical blocks enters at p = 1", True,
           not L.multiplicity(0.05, 1e-9, (1,), n_blocks=4)["bonferroni"][1]["survives_0.05"])
    record("4 specifications", "BLIND SPOT: the same SE 1e-9 when the adapter does not pass n_blocks", False,
           not L.multiplicity(0.05, 1e-9, (1,))["bonferroni"][1]["survives_0.05"],
           "survives - n_blocks is optional; every adapter in this directory now passes it")
    try:
        L.multiplicity(0.05, 0.01, (0,))
        refused = False
    except ValueError:
        refused = True
    record("4 specifications", "a specification count of 0 (count not found)", True, refused, "refused")
    try:
        L.registered_count({"registered_intervals": {}}, "selfcheck")
        refused = False
    except SystemExit:
        refused = True
    record("4 specifications", "a recorded result with no registered_intervals.count", True, refused, "refused")
    null_z = np.random.default_rng(9).normal(0, 1, (2000, 72))
    fwer = float(np.mean([any(L.multiplicity(z, 1.0, (72,))["bonferroni"][72]["survives_0.05"] for z in row) for row in null_z]))
    record("4 specifications", "72 pure-noise intervals, 2,000 times: a survivor in under 6% of families", True, fwer < 0.06,
           "family-wise rate %.3f" % fwer)
    record("4 specifications", "BLIND SPOT: k is the unit's OWN count", False, False,
           "nothing here counts a specification the unit did not register; that part of step 4 is reading, not code")

    # ---------------------------------------------------------------- 5 MDE
    record("5 MDE", "estimate exactly on 2.8 SE reads `at`", True, L.mde_ratio(0.0028, 0.001)["reading"] == "at")
    record("5 MDE", "a null at 0.46 of its MDE reads `below`, never `at`", True, L.mde_ratio(0.050, 0.039)["reading"] == "below")
    record("5 MDE", "5.4x its MDE reads `clear` (must NOT flag)", False, L.mde_ratio(-0.0255, 0.0017)["at_mde"])
    record("5 MDE", "target states an MDE of 0.10 where 2.8 SE is 0.20", True, not L.mde_claim(0.10, 0.0725)["consistent"])
    record("5 MDE", "target's stated 0.203 against 2.8 x 0.0725 (must NOT fire)", False, not L.mde_claim(0.203, 0.0725)["consistent"])
    zs = np.linspace(1.97, 6, 200)
    below = sum(L.mde_ratio(z, 1.0)["reading"] == "below" for z in zs)
    record("5 MDE", "NOT INDEPENDENT: can an interval that excludes zero (|z| >= 1.97) read `below`?", True, below > 0,
           "%d of 200 - only for |z| < 2.24; otherwise the reading is a function of the z step 4 already used" % below)

    w = max(len(p) for _s, p, *_ in rows_out)
    print("step               %-*s  must fire  fired  ok   note" % (w, "plant"))
    for step, plant, exp, fired, ok, note in rows_out:
        print("%-18s %-*s  %-9s  %-5s  %-3s  %s" % (step, w, plant, exp, fired, "ok" if ok else "BAD", note))
    blind = sum(1 for _s, p, *_ in rows_out if p.startswith(("BLIND SPOT", "NOT INDEPENDENT")))
    print("\n%d plants, %d behaved as required, %d of them pinned blind spots or dependencies" % (
        len(rows_out), len(rows_out) - len(bad), blind))
    if bad:
        print("A STEP LET ITS PLANT THROUGH: %s" % bad)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
