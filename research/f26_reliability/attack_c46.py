"""f-35 target adapter: c-46's population-and-null, FROM ITS COMMITTED ROWS.

    CLAIM  NFL Board ledger snapshot 2026-10-08 (2026 weeks 3-4, 31 games). All 19 leans c-40
           excluded recover under R1 (facts with ingested_ts <= read_at); verdict population
           496 graded leans (240 missed, 256 cleared). "not diagnostic, at this power": own
           mean is the flipping input on 67.5% of missed and 68.8% of cleared; missed minus
           cleared -0.012 [-0.102, +0.076], within-game label permutation p 0.785 (10,000
           draws), MDE 0.121 stated before the run; 0 of 80 survive Holm, 0 below p 0.05
           unadjusted. On c-40's 477: -0.019, p 0.738. R1 breaks 12 of the 477. The 477's own
           mean missed share sits at the 58.5th percentile [57.6, 59.5] with half ties, 62.6%
           counting ties as below, 8% of draws tied. Pre-stated MDE 1.5x-2.4x the realised
           one in 7 of 50 tests.

READ-ONLY, NO STORE OPENED, NO NETWORK. There is NO leakage step and NO re-fit: the 19 fits are
taken from the committed result file. What IS run: all 80 tests recomputed from the committed
rows through the target's own `analyse` at its own seed; the blocks test through its own
`slice_tests`; the permutation with whole LADDERS moved instead of rows; the count of runs from
git and from the unit's scratch outputs (read-only); and a POWER PLANT - a missed-vs-cleared
difference of known size pushed through the target's own test and its registered three-part
rule.

    python research/f26_reliability/attack_c46.py --src <c-46 worktree> --out <json>
        [--scratch D:/temp/c46] [--plant-reps 300] [--family-reps 30] [--budget-s 440]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = {"rows": 496, "missed": 240, "cleared": 256, "games": 31, "recovered": 19, "broken": 12,
       "missed_share": 0.675, "cleared_share": 0.688, "est": (-0.012, -0.102, 0.076), "p": 0.785,
       "mde": 0.121, "family": 80, "holm": 0, "below05": 0, "est477": -0.019, "p477": 0.738,
       "pct477": (58.5, 57.6, 59.5), "atbelow477": 62.6, "ties_pct": 8, "drift": (7, 50, 1.5, 2.4),
       "prereg": "0bc9d74", "run_commit": "66fb2a3", "head": "e0cf03f"}
MULTS = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
LADDER_PERMS = 5000


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def git(src, *args):
    r = subprocess.run(["git", "-C", src] + list(args), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--plant-reps", type=int, default=300)
    ap.add_argument("--family-reps", type=int, default=30)
    ap.add_argument("--budget-s", type=float, default=440.0)
    a = ap.parse_args()
    T0 = time.time()
    sys.path.insert(0, HERE)
    import f26lib as L
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    from research import counterfactual_population as cp
    for mod in (cp, cp.CL):
        if not os.path.abspath(mod.__file__).startswith(src):
            raise SystemExit("%s resolved outside --src: %s" % (mod.__name__, mod.__file__))
    res = os.path.join(src, "research", "results")
    ld = lambda f: json.load(open(os.path.join(res, f), encoding="utf-8"))  # noqa: E731
    rec = ld("counterfactual_population.json")
    new_rows = ld("counterfactual_population_rows.json")["rows"]
    c40 = ld("counterfactual_ledger.json")
    rows477 = ld("counterfactual_ledger_rows.json")["rows"]
    rows = rows477 + new_rows                      # the target's own order: P496 = rows477 + new_rows
    if len(rows477) < 100 or len(new_rows) < 1 or len({r["lean_id"] for r in rows}) != len(rows):
        raise SystemExit("%d + %d rows, or a repeated lean_id - refusing" % (len(rows477), len(new_rows)))
    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-46", "pipeline_rerun": False, "leakage_step": "NOT RUN", "partial": False, "not_run": []}
    I = cp.INPUTS
    k0 = I.index("own_mean")
    mde_pre = {(t["slice"], t["input"]): t["mde"] for t in c40["tests"] if t["test"] == "T2"}
    n = len(rows)
    nofl = sum(1 for r in rows if r["flipping_input"] is None)
    have = [r for r in rows if r["flipping_input"] is not None]
    miss = np.array([r["result"] == "missed" for r in have])
    own = np.array([r["flipping_input"] == "own_mean" for r in have])
    game = [r["game_id"] for r in have]
    units = len(set(r["game_id"] for r in rows))
    pg = ["%s|%s" % (r["game_id"], r["gsis_id"]) for r in have]
    lad = ["%s|%s|%s" % (r["game_id"], r["gsis_id"], r["market"]) for r in have]
    out("%d rows (%d + %d); %d missed, %d cleared; %d without a flipping input; %d games, %d weeks, %d teams, %d players, "
        "%d player-games, %d ladders (player-game-market)"
        % (n, len(rows477), len(new_rows), sum(r["result"] == "missed" for r in rows), sum(r["result"] == "cleared" for r in rows),
           nofl, units, len({r["week"] for r in rows}), len({r["team"] for r in rows}), len({r["gsis_id"] for r in rows}),
           len({(r["game_id"], r["gsis_id"]) for r in rows}), len({(r["game_id"], r["gsis_id"], r["market"]) for r in rows})))
    out("target HEAD %s; result json sha256 %s; rows json sha256 %s"
        % (git(src, "rev-parse", "--short", "HEAD"), sha(os.path.join(res, "counterfactual_population.json"))[:12],
           sha(os.path.join(res, "counterfactual_population_rows.json"))[:12]))

    # ------------------------------------------------------------------ 1
    out("\n== 1. REPRODUCE from the committed rows (fits NOT rebuilt), through cp.analyse at seed %d, %d perm draws, %d boot draws"
        % (cp.SEED, cp.PERM, cp.BOOT))
    tests, pcts = cp.analyse({"P477": rows477, "P496": rows}, mde_pre)
    key = lambda t: (t["population"], t["slice"], t["input"])  # noqa: E731
    mine, theirs = {key(t): t for t in tests}, {key(t): t for t in rec["tests"]}
    if set(mine) != set(theirs):
        raise SystemExit("the recomputed test keys are not the result file's")
    close = lambda x, y: (x is None and y is None) or (x is not None and y is not None and abs(x - y) < 1e-9)  # noqa: E731
    F = ("estimate", "lo", "hi", "p", "p_holm", "mde_realised", "mde_used", "mde_pre")
    differ = [k for k in mine if not all(close(mine[k].get(f), theirs[k].get(f)) for f in F)]
    R["tests_recomputed"] = {"n": len(mine), "differ": len(differ), "which": [list(k) for k in differ]}
    out("   %d tests recomputed; %d differ from the committed json in %s (tolerance 1e-9, SAME seed)" % (len(mine), len(differ), "/".join(F)))
    pdiff = [(p_, f) for p_ in cp.POPS for f in pcts[p_] if isinstance(pcts[p_][f], (int, float))
             and abs(pcts[p_][f] - rec["own_mean_percentile"][p_][f]) > 1e-9]
    out("   own_mean_percentile block recomputed; fields differing from the committed json: %d" % len(pdiff))
    t496, t477 = mine[("P496", "all", "own_mean")], mine[("P477", "all", "own_mean")]
    q477 = pcts["P477"]
    v = cp.verdict(tests, "P496")
    nineteen = rec["the_19"]["rows"]
    drift_all = [t for t in tests if t.get("mde_drift")]
    drift = [t for t in drift_all if not (1 / cp.MDE_DRIFT <= t["mde_drift"] <= cp.MDE_DRIFT)]
    pre_over_real = sorted(1 / t["mde_drift"] for t in drift)
    R["reproduce"] = [
        L.reproduce("rows", n, PUB["rows"], 0), L.reproduce("missed", t496["n_missed"], PUB["missed"], 0),
        L.reproduce("cleared", t496["n_cleared"], PUB["cleared"], 0), L.reproduce("games (counted here)", units, PUB["games"], 0),
        L.reproduce("the 19: rows in the rows file", len(new_rows), PUB["recovered"], 0),
        L.reproduce("the 19: r1_within_tol true (result file)", sum(f["r1_within_tol"] for f in nineteen), PUB["recovered"], 0),
        L.reproduce("the 19: fit_basis r1_recovered (rows file)", sum(r["fit_basis"] == "r1_recovered" for r in new_rows), PUB["recovered"], 0),
        L.reproduce("control: of the 477, broken by R1", rec["r1"]["control_477"]["of"] - rec["r1"]["control_477"]["still_within_tol"], PUB["broken"], 0),
        L.reproduce("control: failed rows listed", len(rec["r1"]["control_477"]["failed"]), PUB["broken"], 0),
        L.reproduce("own_mean share, missed", t496["missed_share"], PUB["missed_share"], 3),
        L.reproduce("own_mean share, cleared", t496["cleared_share"], PUB["cleared_share"], 3),
        L.reproduce("P496 est", t496["estimate"], PUB["est"][0], 3), L.reproduce("P496 lo", t496["lo"], PUB["est"][1], 3),
        L.reproduce("P496 hi", t496["hi"], PUB["est"][2], 3), L.reproduce("P496 permutation p", t496["p"], PUB["p"], 3),
        L.reproduce("P496 MDE used (pre-stated)", t496["mde_used"], PUB["mde"], 3),
        L.reproduce("tests in the family", len(tests), PUB["family"], 0),
        L.reproduce("Holm survivors", sum(t["p_holm"] < 0.05 for t in tests), PUB["holm"], 0),
        L.reproduce("p < 0.05 unadjusted", sum(t["p"] < 0.05 for t in tests), PUB["below05"], 0),
        L.reproduce("P477 est", t477["estimate"], PUB["est477"], 3), L.reproduce("P477 permutation p", t477["p"], PUB["p477"], 3),
        L.reproduce("P477 percentile, half ties", 100 * q477["percentile"], PUB["pct477"][0], 1),
        L.reproduce("P477 percentile Wilson lo", 100 * q477["wilson95"][0], PUB["pct477"][1], 1),
        L.reproduce("P477 percentile Wilson hi", 100 * q477["wilson95"][1], PUB["pct477"][2], 1),
        L.reproduce("P477 at or below (ties as below), %", 100 * q477["share_at_or_below"], PUB["atbelow477"], 1),
        L.reproduce("P477 draws tied, %", 100 * q477["draws_tied"] / q477["draws"], PUB["ties_pct"], 0),
        L.reproduce("MDE drift: tests outside [1/1.5, 1.5]", len(drift), PUB["drift"][0], 0),
        L.reproduce("MDE drift: tests with both MDEs", len(drift_all), PUB["drift"][1], 0),
        L.reproduce("MDE drift: smallest pre/realised among them", pre_over_real[0] if drift else float("nan"), PUB["drift"][2], 1),
        L.reproduce("MDE drift: largest pre/realised among them", pre_over_real[-1] if drift else float("nan"), PUB["drift"][3], 1)]
    for r in R["reproduce"]:
        out("   %-48s measured %+.4f  published %+.4f -> %s" % (r["name"], r["measured"], r["published"],
                                                              "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    R["verdict_by_rule"] = {"mine": v["verdict"], "file": rec["verdict"]["verdict"]}
    out("   verdict by the registered rule: %r (file: %r); smallest p in the family %.4f at %s"
        % (v["verdict"], rec["verdict"]["verdict"], min(t["p"] for t in tests), key(min(tests, key=lambda t: t["p"]))))
    out("   MDE drift: all %d outside the band have realised %s the pre-stated one"
        % (len(drift), "BELOW" if all(t["mde_drift"] < 1 for t in drift) else "on both sides of"))
    # the rows' own consistency: is flipping_input the argmin of the row's flip distances, by c-40's function?
    bad = sum(1 for r in new_rows if cp.CL.flipping_input(r["flip"]) != r["flipping_input"])
    out("   the 19 rows: flipping_input is not CL.flipping_input(row['flip']) on %d of %d" % (bad, len(new_rows)))
    R["row_consistency_19"] = bad

    # ------------------------------------------------------------------ 2
    out("\n== 2a. BLOCKS: THROUGH cp.slice_tests (the function behind the published interval)")

    def width_of(pop, seed):
        def w(rr):
            tt, _ = cp.slice_tests(pop, "all", rr, seed, mde_pre, draws=200)
            t = next(x for x in tt if x["input"] == "own_mean")
            return t["hi"] - t["lo"]
        return w
    R["through"] = {"P496": L.duplication_through(width_of("P496", [cp.SEED, 1, 0]), rows, "game_id",
                                                  fn_name="counterfactual_population.slice_tests", units=units),
                    "P477": L.duplication_through(width_of("P477", [cp.SEED, 0, 0]), rows477, "game_id",
                                                  fn_name="counterfactual_population.slice_tests",
                                                  units=len({r["game_id"] for r in rows477}))}
    for k, t in R["through"].items():
        out("   %s own_mean  %s" % (k, L.through_line(t)))
    R["through_verdicts"] = L.require_through(["P496", "P477"], R["through"])
    out("   (the bootstrap stream is spawned separately from the permutation's, so draws=200 on the permutation leaves the interval "
        "as published: width here %.6f, published %.6f)" % (R["through"]["P496"]["width"], t496["hi"] - t496["lo"]))
    out("   rows %d against units %d (distinct game_id, counted here): %.1f rows a game" % (n, units, n / units))

    def stat(idx):
        m, o = miss[idx], own[idx]
        if m.sum() == 0 or (~m).sum() == 0:
            return float("nan")
        return float(o[m].mean() - o[~m].mean())
    labelings = {"game": game, "week": [r["week"] for r in have], "player": [r["gsis_id"] for r in have],
                 "player-game": pg, "ladder": lad, "team": [r["team"] for r in have]}
    R["iid_contrast"] = L.iid_contrast(stat, len(have), game, seed=11)
    out("   descriptive: an unblocked interval would be x%.2f the width of the game-blocked one" % R["iid_contrast"]["iid_over_blocked_width"])
    R["alt_blocks"] = L.alt_blocks(stat, len(have), labelings, seed=12)
    for nm, r in R["alt_blocks"].items():
        out("   %-12s (%3d blocks) %s" % (nm, r["n_blocks"], L.fmt(r, 3)))
    out("   week blocks: %d blocks, fewer than %d - NOT READ. Kickoff-slot blocks: NOT RUN, no rows file carries a kickoff time."
        % (len(set(labelings["week"])), L.MIN_BLOCKS))
    R["not_run"].append("kickoff-slot blocks: no kickoff time in any committed row")

    out("\n== 2b. THE PERMUTATION WITH LADDERS MOVED, NOT ROWS (%d permutations, seed 46)" % LADDER_PERMS)
    by_l = {}
    for i_, l_ in enumerate(lad):
        by_l.setdefault(l_, []).append(i_)
    uni_f = sum(1 for ix in by_l.values() if len(set(own[ix])) == 1)
    uni_r = sum(1 for ix in by_l.values() if len(set(miss[ix])) == 1)
    multi = sum(1 for ix in by_l.values() if len(ix) > 1)
    R["ladders"] = {"n": len(by_l), "rows_per_ladder_mean": len(have) / len(by_l), "more_than_one_rung": multi,
                    "own_mean_uniform_within": uni_f, "result_uniform_within": uni_r,
                    "sizes": dict(sorted(Counter(len(ix) for ix in by_l.values()).items()))}
    out("   %d ladders (game|player|market), %.2f rows each, sizes %s; %d have more than one rung; own_mean-or-not is the same on every "
        "rung of %d; the result is the same on every rung of %d"
        % (len(by_l), len(have) / len(by_l), R["ladders"]["sizes"], multi, uni_f, uni_r))
    obs = stat(np.arange(len(have)))
    lg = {l_: l_.split("|")[0] for l_ in by_l}
    m_l = {l_: int(miss[ix].sum()) for l_, ix in by_l.items()}
    c_l = {l_: int((~miss[ix]).sum()) for l_, ix in by_l.items()}
    p_l = {l_: float(own[ix].mean()) for l_, ix in by_l.items()}
    R["ladder_permutation"] = {}
    # A: each ladder keeps its results; the SHARE of its rungs on own_mean is exchanged between ladders of the same game
    names = sorted(by_l)
    gl = L.blocks_of([lg[l_] for l_ in names])
    mv, cv, pv = (np.array([d[l_] for l_ in names], dtype=float) for d in (m_l, c_l, p_l))
    obsA = float((mv * pv).sum() / mv.sum() - (cv * pv).sum() / cv.sum())
    rng = np.random.default_rng(46)
    nullA = np.empty(LADDER_PERMS)
    for d in range(LADDER_PERMS):
        pp = pv.copy()
        for b in gl:
            pp[b] = pv[b][rng.permutation(len(b))]
        nullA[d] = (mv * pp).sum() / mv.sum() - (cv * pp).sum() / cv.sum()
    # B: whole flipping-input vectors exchanged between ladders of the same game AND the same number of rungs (rungs in line order)
    order = {l_: sorted(ix, key=lambda i_: (have[i_]["line"], have[i_]["side"])) for l_, ix in by_l.items()}
    strata = {}
    for l_ in names:
        strata.setdefault((lg[l_], len(by_l[l_])), []).append(l_)
    movable = sum(len(v_) for v_ in strata.values() if len(v_) > 1)
    rng = np.random.default_rng(46)
    nullB = np.empty(LADDER_PERMS)
    for d in range(LADDER_PERMS):
        o2 = own.copy()
        for v_ in strata.values():
            if len(v_) > 1:
                for dst, s_ in zip(v_, rng.permutation(len(v_))):
                    o2[order[dst]] = own[order[v_[s_]]]
        nullB[d] = o2[miss].mean() - o2[~miss].mean()
    # C: the target's scheme, redrawn here at this seed, as the yardstick for A and B
    rng = np.random.default_rng(46)
    gb = L.blocks_of(game)
    nullC = np.empty(LADDER_PERMS)
    for d in range(LADDER_PERMS):
        lab = miss.copy()
        for b in gb:
            lab[b] = miss[b][rng.permutation(len(b))]
        nullC[d] = own[lab].mean() - own[~lab].mean()
    for nm, o_, nu, note in (("rows within game (the target's scheme, redrawn)", obs, nullC, ""),
                             ("A ladder own_mean share exchanged within game", obsA, nullA,
                              " [statistic in ladder-share form: observed %+.4f against the row statistic %+.4f]" % (obsA, obs)),
                             ("B whole ladders exchanged within game and size", obs, nullB,
                              " [%d of %d ladders have a same-game same-size partner]" % (movable, len(names)))):
        p = float((1 + (np.abs(nu) >= abs(o_) - 1e-12).sum()) / (1 + LADDER_PERMS))
        x = {"observed": o_, "null_sd": float(nu.std(ddof=1)), "p_two_sided": p, "mde_2.8sd": 2.8 * float(nu.std(ddof=1)),
             "distinct_null_values": int(len(np.unique(np.round(nu, 9))))}
        R["ladder_permutation"][nm] = x
        out("   %-48s observed %+.4f; null sd %.4f (2.8 sd = %.3f); p %.3f; %d distinct null values%s"
            % (nm, o_, x["null_sd"], x["mde_2.8sd"], p, x["distinct_null_values"], note))
    out("   published p %.3f at 10,000 draws; MC error of a p near 0.79 at %d draws is %.3f"
        % (PUB["p"], LADDER_PERMS, (0.785 * 0.215 / LADDER_PERMS) ** 0.5))

    # ------------------------------------------------------------------ 3
    out("\n== 3. LEAKAGE: NOT RUN. R1 is a restriction on ingested_ts inside a facts store and a model re-fit; no store was opened "
        "and no fit rebuilt here. The 19 fits below are the committed file's, not re-derived.")
    R["not_run"].append("leakage scramble and the R1 re-fit: need the facts store")
    ra = sorted({f["read_at"] for f in nineteen})
    d19 = np.array([abs(f["r1"] - f["ledgered"]) for f in nineteen])
    dt = np.array([abs(f["todays_facts"] - f["ledgered"]) for f in nineteen])
    ctl = rec["r1"]["control_477"]["failed"]
    dc = np.array([abs(f["r1"] - f["ledgered"]) for f in ctl])
    R["the_19"] = {"read_at_min": ra[0], "read_at_max": ra[-1], "distinct_read_at": len(ra), "weeks": sorted({f["week"] for f in nineteen}),
                   "r1_within_1e-4": int((d19 <= 1e-4).sum()), "r1_within_5e-5": int((d19 <= 5e-5).sum()), "r1_max_abs": float(d19.max()),
                   "todays_within_1e-4": int((dt <= 1e-4).sum()), "todays_min_abs": float(dt.min()), "todays_max_abs": float(dt.max()),
                   "exposure_min": min(f["player_weeks_kicked_off_not_ingested"] for f in nineteen),
                   "exposure_max": max(f["player_weeks_kicked_off_not_ingested"] for f in nineteen),
                   "result": dict(Counter(f["result"] for f in nineteen)),
                   "flipping_input": dict(Counter("%s|%s" % (r["result"], r["flipping_input"]) for r in new_rows)),
                   "control_failed_games": dict(Counter(f["game_id"] for f in ctl)), "control_failed_read_at": sorted({f["read_at"] for f in ctl}),
                   "control_abs_min": float(dc.min()), "control_abs_max": float(dc.max())}
    q = R["the_19"]
    out("   the 19: week %s; %d distinct read_at from %s to %s. The rows carry NO kickoff time, so 'after a week-4 kickoff' is NOT "
        "CHECKED against a kickoff here - only that every read_at is on or after 2026-10-02T00:15Z, the kickoff the findings name: %s"
        % (q["weeks"], q["distinct_read_at"], q["read_at_min"], q["read_at_max"], all(x >= "2026-10-02T00:15" for x in ra)))
    R["not_run"].append("read_at against a kickoff time: no kickoff in the committed rows; the 00:15Z instant is the target's own")
    out("   the 19: |R1 fit - ledgered P(over)| <= 1e-4 (the target's tolerance) on %d of 19; <= 5e-5 (the ledger's 4-dp rounding) on "
        "%d of 19; largest %.6f" % (q["r1_within_1e-4"], q["r1_within_5e-5"], q["r1_max_abs"]))
    out("   the 19 on TODAY's facts: within 1e-4 on %d of 19; |diff| from %.6f to %.6f (the exclusion c-40 made was over fits this far off)"
        % (q["todays_within_1e-4"], q["todays_min_abs"], q["todays_max_abs"]))
    out("   the 19: exposure (kicked-off player-weeks not yet ingested) %d to %d; results %s; result|flipping input %s"
        % (q["exposure_min"], q["exposure_max"], q["result"], q["flipping_input"]))
    out("   control: the %d of c-40's 477 that R1 breaks are in games %s, read at %s; |R1 - ledgered| from %.6f to %.6f"
        % (len(ctl), q["control_failed_games"], q["control_failed_read_at"], q["control_abs_min"], q["control_abs_max"]))

    # ------------------------------------------------------------------ 4
    out("\n== 4. SPECIFICATIONS")
    try:
        k_reg = L.registered_count(rec, "c-46")
        out("   registered_intervals.count = %d" % k_reg)
    except SystemExit as e:
        out("   f26lib.registered_count REFUSES: %s" % e)
        k_reg = rec.get("family", {}).get("tests")
        if not isinstance(k_reg, int) or k_reg != len(rec["tests"]):
            raise SystemExit("no family.tests in the result file that equals its own test list - no count to correct over")
        out("   the result file carries family.tests = %d instead, and lists %d tests; THAT key is used below" % (k_reg, len(rec["tests"])))
    R["registered_count"] = k_reg
    notes = Counter(t["note"] for t in rec["tests"] if t["note"])
    live = [t for t in rec["tests"] if not t["note"]]
    mirrors = 0
    seen = set()
    for t in live:
        for u in live:
            if key(t) < key(u) and t["population"] == u["population"] and t["slice"] == u["slice"] \
                    and abs(t["estimate"] + u["estimate"]) < 1e-12 and abs(t["p"] - u["p"]) < 1e-12 and key(u) not in seen:
                mirrors += 1
                seen.add(key(u))
    by_pop = Counter(t["population"] for t in live)
    by_inp = Counter(t["input"] for t in live)
    twin = sum(1 for t in live if t["population"] == "P496" and ("P477", t["slice"], t["input"]) in {key(u) for u in live})
    R["spec_count"] = {"family": k_reg, "entered_at_p1": dict(notes), "live": len(live), "live_by_population": dict(by_pop),
                       "live_by_input": dict(by_inp), "exact_mirror_pairs": mirrors, "P496_tests_with_a_P477_twin": twin,
                       "p1_not_exactly_one": sum(1 for t in rec["tests"] if t["note"] and t["p_holm"] != 1.0)}
    out("   %d tests; entered at p = 1 with a note: %d (%s); live: %d (%s; by input %s)"
        % (len(rec["tests"]), sum(notes.values()), dict(notes), len(live), dict(by_pop), dict(by_inp)))
    out("   exact mirror pairs among the live tests (estimate negated, p identical, same slice and population): %d" % mirrors)
    out("   %d of the live P496 tests have a live P477 twin on %d of the same %d rows (the family the pre-registration calls conservative)"
        % (twin, len(rows477), n))
    out("   so distinct live tests on distinct rows: at most %d (live, one population) less %d mirrors"
        % (by_pop.get("P496", 0), sum(1 for k_ in seen if k_[0] == "P496")))
    bse = R["alt_blocks"]["game"]["se"]
    mu = L.multiplicity(t496["estimate"], bse, (k_reg, 1), n_blocks=units)
    R["multiplicity"] = dict(mu, se=bse)
    out("   P496 own_mean z %+.2f (game-block SE %.4f, drawn here)  p %.3g; a null - Bonferroni over k=%d is moot (survives: %s)"
        % (mu["z"], bse, mu["p"], k_reg, mu["bonferroni"][k_reg]["survives_0.05"]))

    out("   -- how many runs --")
    script = "research/counterfactual_population.py"
    log3 = git(src, "log", "--format=%h %cI %s", "-3").splitlines()
    for ln in log3:
        out("   git: %s" % ln[:170])
    d_res = git(src, "diff", "--stat", PUB["run_commit"], PUB["head"], "--", "research/results", "docs")
    d_scr = git(src, "diff", "--numstat", PUB["run_commit"], PUB["head"], "--", script)
    d_pre = git(src, "diff", "--stat", PUB["prereg"], PUB["head"], "--", "docs/C46-population-and-null-preregistration.md")
    findings = open(os.path.join(src, "docs", "findings", "c46-population-and-null.md"), encoding="utf-8").read()
    R["runs"] = {"results_or_docs_changed_run_commit_to_head": bool(d_res), "script_numstat_run_commit_to_head": d_scr,
                 "prereg_changed_since_its_commit": bool(d_pre), "findings_say_runs_2": "**Runs: 2.**" in findings,
                 "findings_mention_run_3": "run 3" in findings.lower(), "head_commit_says_run_3": "run 3" in log3[0]}
    out("   %s..%s: research/results or docs changed: %s; script numstat: %s; pre-registration edited after %s: %s"
        % (PUB["run_commit"], PUB["head"], bool(d_res), d_scr.replace("\t", " "), PUB["prereg"], bool(d_pre)))
    out("   the findings say '**Runs: 2.**': %s; the findings mention a run 3: %s; the HEAD commit message says 'run 3': %s"
        % (R["runs"]["findings_say_runs_2"], R["runs"]["findings_mention_run_3"], R["runs"]["head_commit_says_run_3"]))
    if a.scratch and os.path.isdir(a.scratch):
        sc = {}
        for dname in ("out", "out2", "out3"):
            for f in ("counterfactual_population.json", "counterfactual_population_rows.json"):
                p = os.path.join(a.scratch, dname, f)
                if os.path.exists(p):
                    sc["%s/%s" % (dname, f)] = {"identical_to_committed": sha(p) == sha(os.path.join(res, f)), "sha": sha(p)[:12],
                                                "mtime_local": time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(p)))}
        for k_, v_ in sc.items():
            out("   scratch %-45s sha %s written %s local; byte-identical to the committed file: %s"
                % (k_, v_["sha"], v_["mtime_local"], v_["identical_to_committed"]))
        p1 = os.path.join(a.scratch, "out", "counterfactual_population.json")
        if os.path.exists(p1):
            r1 = json.load(open(p1, encoding="utf-8"))
            t1 = {key(t): t for t in r1["tests"]}
            allf = sorted({f for t in rec["tests"] for f in t} | {f for t in r1["tests"] for f in t})
            dnum = [(k_, f) for k_ in theirs for f in allf
                    if (theirs[k_].get(f) != t1.get(k_, {}).get(f))]
            top = [k_ for k_ in sorted(set(rec) | set(r1)) if k_ not in ("tests", "own_mean_percentile") and rec.get(k_) != r1.get(k_)]
            pk = {p_: sorted(set(rec["own_mean_percentile"][p_]) ^ set(r1["own_mean_percentile"][p_])) for p_ in cp.POPS}
            pv_ = [(p_, f) for p_ in cp.POPS for f in r1["own_mean_percentile"][p_]
                   if r1["own_mean_percentile"][p_][f] != rec["own_mean_percentile"][p_].get(f)]
            sc["run1_vs_committed"] = {"test_fields_differing": len(dnum), "tests_in_run1": len(t1), "top_level_keys_differing": top,
                                       "percentile_keys_only_in_one": pk, "percentile_values_differing_on_shared_keys": len(pv_)}
            out("   run 1's json against the committed one: %d of %d x %d test fields differ; top-level blocks differing (other than "
                "tests, own_mean_percentile): %s; own_mean_percentile keys present in only one: %s; values differing on shared keys: %d"
                % (len(dnum), len(theirs), len(allf), top, pk, len(pv_)))
        logs = {}
        norm = lambda p: open(p, "rb").read().replace(b"\r\n", b"\n")  # noqa: E731
        for f in ("run1.log", "run2.log", "run3.log"):
            p = os.path.join(a.scratch, f)
            if os.path.exists(p):
                logs[f] = norm(p)
        com = norm(os.path.join(res, "counterfactual_population.log"))
        if len(logs) == 3:
            strip = lambda b: b"\n".join(l_ for l_ in b.split(b"\n") if not l_.startswith(b"done in"))  # noqa: E731
            sc["logs"] = {"run2_eq_run3": logs["run2.log"] == logs["run3.log"],
                          "run2_eq_run3_but_for_the_done_line": strip(logs["run2.log"]) == strip(logs["run3.log"]),
                          "committed_log_contains_run1": logs["run1.log"].strip() in com,
                          "committed_log_contains_run2": logs["run2.log"].strip() in com,
                          "committed_log_contains_run3": logs["run3.log"].strip() in com,
                          "run1_lines_not_in_run2": len(set(logs["run1.log"].split(b"\n")) - set(logs["run2.log"].split(b"\n"))),
                          "run2_lines_not_in_run1": len(set(logs["run2.log"].split(b"\n")) - set(logs["run1.log"].split(b"\n")))}
            out("   scratch logs: %s" % sc["logs"])
        R["runs"]["scratch"] = sc
    else:
        R["runs"]["scratch"] = "NOT RUN"
        R["not_run"].append("scratch comparison: no --scratch")
        out("   the unit's scratch outputs: NOT RUN (no --scratch)")
    out("   what this cannot see: an execution whose output was overwritten or never written. Three output directories and three "
        "logs are a floor on the number of runs, not a count.")

    # ------------------------------------------------------------------ 5
    out("\n== 5. MDE")
    perm_sd = t496["mde_realised"] / cp.MDE_MULT
    R["mde"] = {"ratio": L.mde_ratio(t496["estimate"], bse), "claim_vs_boot_se": L.mde_claim(PUB["mde"], bse),
                "claim_vs_perm_sd": L.mde_claim(PUB["mde"], perm_sd),
                "alt": {nm: 2.8 * r["se"] for nm, r in R["alt_blocks"].items() if r["read"]},
                "ladder_perm": {nm: x["mde_2.8sd"] for nm, x in R["ladder_permutation"].items()}}
    m = R["mde"]
    out("   est %+.4f; |est| / (2.8 x game-block SE) = %.2f (%s)" % (t496["estimate"], m["ratio"]["ratio"], m["ratio"]["reading"]))
    out("   stated MDE %.3f vs 2.8 x game-block bootstrap SE %.4f -> %s (rel %+.2f)"
        % (PUB["mde"], m["claim_vs_boot_se"]["remeasured"], "consistent" if m["claim_vs_boot_se"]["consistent"] else "INCONSISTENT",
           m["claim_vs_boot_se"]["rel_diff"]))
    out("   stated MDE %.3f vs 2.8 x the target's permutation sd %.4f (its 'realised') -> %s (rel %+.2f)"
        % (PUB["mde"], m["claim_vs_perm_sd"]["remeasured"], "consistent" if m["claim_vs_perm_sd"]["consistent"] else "INCONSISTENT",
           m["claim_vs_perm_sd"]["rel_diff"]))
    out("   2.8 x SE under other blocks: %s; 2.8 x null sd under the ladder permutations: %s"
        % (", ".join("%s %.3f" % kv for kv in m["alt"].items()), ", ".join("%s %.3f" % (k_[:1], v_) for k_, v_ in m["ladder_perm"].items())))

    # ------------------------------------------------------------------ 6
    out("\n== 6. POWER PLANT: a known missed-minus-cleared difference in own_mean share through the target's own test and rule")
    p0 = float(own.mean())
    out("   each rep: %d games resampled with replacement; missed/cleared permuted within game (true difference 0); then each missed "
        "own_mean row moved to group_mean with probability delta / %.4f (expected contrast = -delta). The target's own "
        "cp.slice_tests on the all-rows P496 slice at its own seed and draws (%d perm, %d boot). For the first %d reps of each cell "
        "the WHOLE 80-test family is rebuilt through cp.analyse + Holm and cp.verdict is read." % (units, p0, cp.PERM, cp.BOOT, a.family_reps))
    out("   bracket on the registered rule where the family is not rebuilt: LOWER = 80 x p < 0.05 and interval excludes zero and "
        "|est| > max(pre-stated, realised) MDE (Holm p <= 80 p, so this implies the rule); UPPER = the same with unadjusted p < 0.05.")
    by_game = {}
    for r in rows:
        by_game.setdefault(r["game_id"], []).append(r)
    glist = sorted(by_game)
    in477 = {r["lean_id"] for r in rows477}
    keep = ("lean_id", "result", "flipping_input", "market", "side", "band")

    def dataset(rng, delta):
        new = []
        for j, g in enumerate(rng.integers(0, len(glist), len(glist))):
            rs = by_game[glist[g]]
            labs = [r["result"] for r in rs]
            for r, p_ in zip(rs, rng.permutation(len(rs))):
                x = {f: r[f] for f in keep}
                x.update(result=labs[p_], game_id="g%d" % j)
                if delta > 0 and x["result"] == "missed" and x["flipping_input"] == "own_mean" and rng.random() < delta / p0:
                    x["flipping_input"] = "group_mean"
                new.append(x)
        return new

    R["plant"] = {"reps_asked": a.plant_reps, "family_reps_asked": a.family_reps, "cells": []}
    for mult in MULTS:
        delta = mult * PUB["mde"]
        rng = np.random.default_rng(4600 + int(round(mult * 10)))
        got, fam = [], []
        for rep in range(a.plant_reps):
            if time.time() - T0 > a.budget_s:
                R["partial"] = True
                break
            new = dataset(rng, delta)
            if rep < a.family_reps:
                tt, _ = cp.analyse({"P477": [r for r in new if r["lean_id"] in in477], "P496": new}, mde_pre)
                t = next(x for x in tt if key(x) == ("P496", "all", "own_mean"))
                vv = cp.verdict(tt, "P496")
                fam.append((vv["verdict"] == "diagnostic", "own_mean" in vv["inputs"], cp.is_diagnostic(t)))
            else:
                tt, _ = cp.slice_tests("P496", "all", new, [cp.SEED, 1, 0], mde_pre)
                t = next(x for x in tt if x["input"] == "own_mean")
            ok = t["lo"] is not None and t["mde_used"] is not None
            excl = bool(ok and (t["lo"] > 0 or t["hi"] < 0))
            big = bool(ok and abs(t["estimate"]) > t["mde_used"])
            got.append((t["estimate"], t["p"] < 0.05, excl, big, t["p"] < 0.05 and excl and big, 80 * t["p"] < 0.05 and excl and big))
        if not got:
            out("   delta %.3f (%.1fx): NOT RUN (time budget reached)" % (delta, mult))
            R["not_run"].append("plant cell %.1fx: time budget" % mult)
            continue
        g = np.array(got, dtype=float)
        cell = {"mult": mult, "delta": delta, "reps": len(got), "mean_contrast": float(g[:, 0].mean()), "sd_contrast": float(g[:, 0].std(ddof=1)),
                "rate_p05": float(g[:, 1].mean()), "rate_interval_excludes_zero": float(g[:, 2].mean()),
                "rate_abs_est_above_mde_used": float(g[:, 3].mean()), "rule_upper": float(g[:, 4].mean()), "rule_lower": float(g[:, 5].mean()),
                "family_reps": len(fam), "rule_exact_any_input": float(np.mean([f[0] for f in fam])) if fam else None,
                "rule_exact_own_mean": float(np.mean([f[2] for f in fam])) if fam else None,
                "bracket_holds_on_family_reps": bool(all(got[i][5] <= fam[i][2] <= got[i][4] for i in range(len(fam))))}
        R["plant"]["cells"].append(cell)
        out("   delta %.3f (%.1fx MDE) reps %3d: mean contrast %+.4f (sd %.4f); p<0.05 %5.1f%%; interval excl. zero %5.1f%%; |est| > MDE "
            "used %5.1f%%; RULE on own_mean: lower %5.1f%% upper %5.1f%%; exact on the first %d reps: own_mean %s, any input (the "
            "verdict) %s; bracket holds: %s"
            % (delta, mult, cell["reps"], cell["mean_contrast"], cell["sd_contrast"], 100 * cell["rate_p05"],
               100 * cell["rate_interval_excludes_zero"], 100 * cell["rate_abs_est_above_mde_used"], 100 * cell["rule_lower"],
               100 * cell["rule_upper"], cell["family_reps"],
               "n/a" if not fam else "%.0f%%" % (100 * cell["rule_exact_own_mean"]),
               "n/a" if not fam else "%.0f%%" % (100 * cell["rule_exact_any_input"]), cell["bracket_holds_on_family_reps"]))
    full = [c_ for c_ in R["plant"]["cells"] if c_["reps"] == a.plant_reps]
    for crit, lab in (("rate_p05", "unadjusted p < 0.05"), ("rule_upper", "the rule, upper bracket"), ("rule_lower", "the rule, lower bracket")):
        first = next((c_ for c_ in full if c_[crit] >= 0.80), None)
        R["plant"].setdefault("first_cell_at_80", {})[crit] = None if first is None else first["mult"]
        out("   smallest planted difference detected in >= 80%% of reps, %s: %s (grid %s x %.3f)"
            % (lab, "none on the grid" if first is None else "%.1fx = %.3f" % (first["mult"], first["delta"]), list(MULTS), PUB["mde"]))
    out("   NOT RUN: a plant in the other direction (own_mean MORE often on misses), and a plant clustered by ladder.")
    R["not_run"] += ["plant with own_mean more often on misses", "plant clustered by ladder"]

    out("\nNOT RUN, collected: %s" % "; ".join(R["not_run"]))
    R["seconds"] = time.time() - T0
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(R, fo, indent=1, default=str)
    out("\nwrote %s%s" % (a.out, "  PARTIAL: the time budget cut the power plant short" if R["partial"] else ""))
    print("wall %.0f s" % R["seconds"], file=sys.stderr)
    return 2 if R["partial"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
