"""f-35 target adapter: c-42's counterfactual ledger on an uncertainty scale, FROM ITS COMMITTED ROWS.

    CLAIM  NFL Board ledger snapshot 2026-10-08T20:16Z, c-40's 477 leans (227 missed, 250
           cleared, 2026 weeks 3-4, 31 games), distance in units of each input's own as-of
           standard error, three of five inputs: own_mean is the flipping input on 97.4% of
           missed leans (T1 +0.640 over 1/3 [+0.615, +0.658], 48-test Holm family) and on
           98.0% of cleared; missed minus cleared (T2) -0.006 [-0.036, +0.022]; realised MDE
           0.041 (T2) and 0.032 (T1); 0 of 24 T2 below p 0.05 unadjusted; the registered
           inequality w*SE_own > (1-w)*SE_group holds on 466 of 468 rows; median missed lean
           3.1 SE from a flip, 26.0% within 2 SE.

READ-ONLY. There is NO leakage step: the standard errors and flip values are arithmetic on
fits that are not rebuilt from a store here. What IS run: all 48 tests recomputed from the
committed rows through the target's own `slice_tests` at its own seed; the blocks test
through that same function; the label-permutation null the claim-about-misses needs; a
POWER PLANT (a missed-vs-cleared difference of known size pushed through the target's own
family and Holm); and the ceiling a share at 0.977 puts on any T2.

    python research/f26_reliability/attack_c42.py --src <c-42 worktree> --out <json>
        [--cache <the unit's scratch components cache>] [--scratch <dir of the unit's own first outputs>]
        [--plant-reps 300] [--budget-s 1500]

`--cache` (optional, the unit's UNCOMMITTED scratch) re-derives every row through the
target's own `analyse()`; without it that step prints NOT RUN.
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
from scipy import stats as sps

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = {"n": {"rows": 477, "missed": 227, "cleared": 250, "games": 31},
       "missed_share": 0.974, "cleared_share": 0.980,
       "T1": (0.640, 0.615, 0.658), "T2": (-0.006, -0.036, 0.022),
       "mde": {"T2": 0.041, "T1": 0.032}, "family": 48, "t2": 24, "t2_below_05": 0, "holm_survivors": 10,
       "ineq": (466, 468), "median_se": 3.1, "within_2se": (59, 0.260), "prereg": "b69b376", "script_commit": "90edc7d"}
PERMS = 5000
PLANT_DELTAS = (0.0, 0.020, 0.041, 0.060, 0.082, 0.128)


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
    ap.add_argument("--cache", default=None)
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--plant-reps", type=int, default=300)
    ap.add_argument("--budget-s", type=float, default=1500.0)
    a = ap.parse_args()
    T0 = time.time()
    sys.path.insert(0, HERE)
    import f26lib as L
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    from research import counterfactual_uncertainty as cu
    for mod in (cu, cu.CL):
        if not os.path.abspath(mod.__file__).startswith(src):
            raise SystemExit("%s resolved outside --src: %s" % (mod.__name__, mod.__file__))
    res = os.path.join(src, "research", "results")
    rec = json.load(open(os.path.join(res, "counterfactual_uncertainty.json"), encoding="utf-8"))
    rows = json.load(open(os.path.join(res, "counterfactual_uncertainty_rows.json"), encoding="utf-8"))["rows"]
    c40 = {r["lean_id"]: r for r in json.load(open(os.path.join(res, "counterfactual_ledger_rows.json"), encoding="utf-8"))["rows"]}
    if len(rows) < 100 or set(c40) != {r["lean_id"] for r in rows}:
        raise SystemExit("%d rows, or their lean_ids are not c-40's - refusing" % len(rows))
    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-42", "pipeline_rerun": False, "leakage_step": "NOT RUN", "partial": False}
    I = cu.INPUTS
    n = len(rows)
    miss = np.array([r["result"] == "missed" for r in rows])
    fi = np.array([I.index(r["flip_se"]) if r["flip_se"] else -1 for r in rows])
    game = [r["game_id"] for r in rows]
    units = len(set(game))
    week = [c40[r["lean_id"]]["week"] for r in rows]
    team = [c40[r["lean_id"]]["team"] for r in rows]
    pg = ["%s|%s" % (r["game_id"], r["gsis_id"]) for r in rows]
    own = fi == I.index("own_mean")
    out("%d rows; %d missed, %d cleared; %d without a flipping input; %d games, %d weeks, %d teams, %d players, "
        "%d player-games" % (n, miss.sum(), (~miss).sum(), (fi < 0).sum(), units, len(set(week)), len(set(team)),
                             len({r["gsis_id"] for r in rows}), len(set(pg))))
    out("target HEAD %s; rows file sha256 %s" % (git(src, "rev-parse", "--short", "HEAD"),
                                                sha(os.path.join(res, "counterfactual_uncertainty_rows.json"))[:12]))

    # ------------------------------------------------------------------ 1
    out("\n== 1. REPRODUCE from the committed rows (pipeline NOT re-run), through cu.slice_tests at seed %d" % cu.SEED)

    def family(rs, seed=cu.SEED):
        rng = np.random.default_rng(seed)
        sl = [("all", rs)] + [("market=%s" % m, [r for r in rs if r["market"] == m]) for m in ("receptions", "rush_attempts")]
        sl += [("side=%s" % s, [r for r in rs if r["side"] == s]) for s in ("over", "under")]
        sl += [("band=%s" % b, [r for r in rs if r["band"] == b]) for b in ("4-6", "6-8", "8+")]
        if tuple(nm for nm, _ in sl) != cu.SLICE_NAMES:
            raise SystemExit("the slices built here are not the target's SLICE_NAMES")
        summaries, tests = [], []
        for nm, x in sl:
            s, t = cu.slice_tests(nm, x, rng)
            summaries.append(s)
            tests += t
        cu.CL.holm(tests)
        return summaries, tests

    summaries, tests = family(rows)
    key = lambda t: (t["slice"], t["test"], t["input"])  # noqa: E731
    mine, theirs = {key(t): t for t in tests}, {key(t): t for t in rec["tests"]}
    if set(mine) != set(theirs):
        raise SystemExit("the recomputed test keys are not the result file's")
    close = lambda x, y: (x is None and y is None) or (x is not None and y is not None and abs(x - y) < 1e-9)  # noqa: E731
    differ = [k for k in mine if not all(close(mine[k][f], theirs[k][f]) for f in ("estimate", "lo", "hi", "p", "p_holm"))]
    R["tests_recomputed"] = {"n": len(mine), "differ_from_result_file": len(differ), "which": [list(k) for k in differ]}
    out("   %d tests recomputed; %d differ from the committed json in estimate, bounds, p or Holm p (tolerance 1e-9, SAME seed)"
        % (len(mine), len(differ)))
    t1, t2 = mine[("all", "T1", "own_mean")], mine[("all", "T2", "own_mean")]
    s0 = summaries[0]
    ceil_m = cu.ceiling([r for r in rows if r["result"] == "missed"])
    caps = {c["cap_se"]: c for c in ceil_m["caps"]}
    pred_rows = [r for r in rows if r["own_nearer_than_group_predicted"] is not None]
    # the inequality recomputed by the attacker from the row's own weight and standard errors, not read from the flag
    my_pred = [(r["weight"] * r["standard_error"]["own_mean"] > (1 - r["weight"]) * r["standard_error"]["group_mean"])
               for r in rows if r["standard_error"]["own_mean"] is not None and r["standard_error"]["group_mean"] is not None]
    t2_all = [t for t in tests if t["test"] == "T2"]
    R["reproduce"] = [L.reproduce("rows", n, PUB["n"]["rows"], 0), L.reproduce("missed", int(miss.sum()), PUB["n"]["missed"], 0),
                      L.reproduce("cleared", int((~miss).sum()), PUB["n"]["cleared"], 0), L.reproduce("games", units, PUB["n"]["games"], 0),
                      L.reproduce("own_mean share, missed", s0["missed"]["share"]["own_mean"], PUB["missed_share"], 3),
                      L.reproduce("own_mean share, cleared", s0["cleared"]["share"]["own_mean"], PUB["cleared_share"], 3)]
    for nm, t, pub in (("T1 own_mean", t1, PUB["T1"]), ("T2 own_mean", t2, PUB["T2"])):
        for part, v, p in zip(("est", "lo", "hi"), (t["estimate"], t["lo"], t["hi"]), pub):
            R["reproduce"].append(L.reproduce("%s %s" % (nm, part), v, p, 3))
    R["reproduce"] += [
        L.reproduce("T2 own_mean realised MDE", t2["mde"], PUB["mde"]["T2"], 3),
        L.reproduce("T1 own_mean realised MDE", t1["mde"], PUB["mde"]["T1"], 3),
        L.reproduce("tests in the family", len(tests), PUB["family"], 0),
        L.reproduce("T2 tests", len(t2_all), PUB["t2"], 0),
        L.reproduce("T2 tests with unadjusted p < 0.05", sum(1 for t in t2_all if t["p"] < 0.05), PUB["t2_below_05"], 0),
        L.reproduce("Holm survivors", sum(1 for t in tests if t["p_holm"] < 0.05), PUB["holm_survivors"], 0),
        L.reproduce("inequality holds (the row flag)", sum(1 for r in pred_rows if r["own_nearer_than_group_predicted"]), PUB["ineq"][0], 0),
        L.reproduce("inequality defined on (the row flag)", len(pred_rows), PUB["ineq"][1], 0),
        L.reproduce("inequality holds (recomputed from weight and SEs)", int(sum(my_pred)), PUB["ineq"][0], 0),
        L.reproduce("inequality defined on (recomputed)", len(my_pred), PUB["ineq"][1], 0),
        L.reproduce("median missed nearest flip, SE", ceil_m["median_nearest_se"], PUB["median_se"], 1),
        L.reproduce("missed within 2 SE, count", caps[2.0]["n_reached"], PUB["within_2se"][0], 0),
        L.reproduce("missed within 2 SE, share", caps[2.0]["share_reached"], PUB["within_2se"][1], 3)]
    for r in R["reproduce"]:
        out("   %-50s measured %+.4f  published %+.4f -> %s" % (r["name"], r["measured"], r["published"],
                                                              "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    v = cu.verdict(summaries[0], tests)
    R["verdict_by_rule"] = {"mine": v["verdict"], "file": rec["verdict"]["verdict"]}
    out("   verdict by the registered rule: %s (file: %s)" % (v["verdict"], rec["verdict"]["verdict"]))

    # internal consistency of the rows: is flip_se the argmin of the row's own SE distances, and is each distance |x'-x|/SE?
    bad_arg = sum(1 for r in rows if cu.flipping({i: r["se"][i] and r["se"][i]["distance"] for i in I}) != r["flip_se"])
    bad_dist = sum(1 for r in rows for i in I if r["se"][i] is not None and abs(
        abs(r["se"][i]["value"] - r["as_of"][i]) / r["standard_error"][i] - r["se"][i]["distance"]) > 1e-6)
    R["row_consistency"] = {"flip_se_not_the_argmin": bad_arg, "distance_not_dx_over_se": bad_dist}
    out("   row consistency: flip_se is not the argmin of the row's own distances on %d rows; a distance is not |x'-x|/SE on %d"
        % (bad_arg, bad_dist))

    out("\n== 1b. THE ROWS THEMSELVES, re-derived through cu.analyse from the unit's scratch components cache")
    if a.cache and os.path.isdir(a.cache):
        t = time.time()
        cache = cu.load_cache(a.cache)
        redo, _no_se = cu.analyse([c40[r["lean_id"]] for r in rows], cache)
        by = {r["lean_id"]: r for r in redo}
        d_flip = sum(1 for r in rows if by[r["lean_id"]]["flip_se"] != r["flip_se"])
        d_se = sum(1 for r in rows for i in I if not close(by[r["lean_id"]]["standard_error"][i], r["standard_error"][i]))
        d_dist = sum(1 for r in rows for i in I if (by[r["lean_id"]]["se"][i] is None) != (r["se"][i] is None) or (
            r["se"][i] is not None and abs(by[r["lean_id"]]["se"][i]["distance"] - r["se"][i]["distance"]) > 1e-9))
        R["rows_rederived"] = {"cache": a.cache, "cache_leans": len(cache), "flip_se_differ": d_flip, "se_differ": d_se,
                               "distance_differ": d_dist, "seconds": time.time() - t,
                               "note": "the cache is the unit's UNCOMMITTED scratch; it was not rebuilt from a store here"}
        out("   %d leans in the cache; the target's frozen gate passed on all %d; flip_se differs on %d rows, a standard error on %d "
            "row-inputs, an SE distance on %d row-inputs (%.0f s). The cache itself is uncommitted scratch and was NOT rebuilt."
            % (len(cache), len(redo), d_flip, d_se, d_dist, time.time() - t))
    else:
        R["rows_rederived"] = "NOT RUN"
        out("   NOT RUN: no --cache given. The per-row standard errors and distances are taken as committed.")

    # ------------------------------------------------------------------ 2
    out("\n== 2a. BLOCKS: THROUGH cu.slice_tests (the function behind every published interval)")

    def width(kind):
        def w(rr):
            _s, tt = cu.slice_tests("dup", rr, np.random.default_rng(cu.SEED))
            t = next(x for x in tt if x["test"] == kind and x["input"] == "own_mean")
            return t["hi"] - t["lo"]
        return w
    R["through"] = {k: L.duplication_through(width(k), rows, "game_id", fn_name="counterfactual_uncertainty.slice_tests", units=units)
                    for k in ("T1", "T2")}
    for k, t in R["through"].items():
        out("   %s own_mean  %s" % (k, L.through_line(t)))
    R["through_verdicts"] = L.require_through(["T1", "T2"], R["through"])
    out("   rows %d against units %d (distinct game_id, counted here): %.1f rows a game" % (n, units, n / units))

    def stat(kind, inp):
        j = I.index(inp)

        def f(idx):
            m, x = miss[idx], fi[idx]
            ok = x >= 0
            a_, b_ = (m & ok).sum(), (~m & ok).sum()
            if a_ == 0 or (kind == "T2" and b_ == 0):
                return float("nan")
            sm = ((x == j) & m).sum() / a_
            return float(sm - 1.0 / len(I)) if kind == "T1" else float(sm - ((x == j) & ~m).sum() / b_)
        return f
    labelings = {"game": game, "week": week, "player": [r["gsis_id"] for r in rows], "player-game": pg, "team": team}
    R["alt_blocks"], R["iid_contrast"] = {}, {}
    for k in ("T1", "T2"):
        st = stat(k, "own_mean")
        R["iid_contrast"][k] = L.iid_contrast(st, n, game, seed=11)
        out("   %s descriptive: %d rows in %d games; an unblocked interval would be x%.2f the width"
            % (k, n, units, R["iid_contrast"][k]["iid_over_blocked_width"]))
        R["alt_blocks"][k] = L.alt_blocks(st, n, labelings, seed=12)
        for nm, r in R["alt_blocks"][k].items():
            out("   %s %-12s (%3d blocks) %s" % (k, nm, r["n_blocks"], L.fmt(r, 3)))
    out("   week blocks: %d blocks, fewer than %d - NOT READ. Kickoff-slot blocks: NOT RUN, neither rows file carries a kickoff "
        "time (read_at is the instant of the read)." % (len(set(week)), L.MIN_BLOCKS))

    # ------------------------------------------------------------------ 2b
    out("\n== 2b. WHAT THE TWO SHARES ARE MADE OF: the rows that are NOT own_mean")
    non = [r for r in rows if r["flip_se"] != "own_mean"]
    no_se = [r for r in non if r["standard_error"]["own_mean"] is None]
    R["non_own"] = {"n": len(non), "missed": sum(r["result"] == "missed" for r in non), "cleared": sum(r["result"] == "cleared" for r in non),
                    "own_mean_has_no_se": len(no_se), "games": len({r["game_id"] for r in non}), "players": len({r["gsis_id"] for r in non}),
                    "player_games": len({(r["game_id"], r["gsis_id"]) for r in non}),
                    "no_se_missed": sum(r["result"] == "missed" for r in no_se), "no_se_cleared": sum(r["result"] == "cleared" for r in no_se),
                    "by_market": dict(Counter(r["market"] for r in non)), "prior_games": sorted(r["prior_games"] for r in non)}
    q = R["non_own"]
    out("   %d of %d rows have a flipping input other than own_mean: %d missed, %d cleared; %d games, %d players, %d player-games; %s"
        % (q["n"], n, q["missed"], q["cleared"], q["games"], q["players"], q["player_games"], q["by_market"]))
    out("   on %d of those %d own_mean has NO standard error (cannot be the flipping input by construction): %d missed, %d cleared; "
        "prior games of the %d: %s" % (q["own_mean_has_no_se"], q["n"], q["no_se_missed"], q["no_se_cleared"], q["n"], q["prior_games"]))
    has = [r for r in rows if r["standard_error"]["own_mean"] is not None]
    xt = Counter((r["own_nearer_than_group_predicted"], r["flip_se"]) for r in rows)
    R["inequality_crosstab"] = {"%s|%s" % k: c for k, c in xt.items()}
    R["own_where_it_has_se"] = {"n": len(has), "own": sum(r["flip_se"] == "own_mean" for r in has),
                                "missed": [sum(r["flip_se"] == "own_mean" and r["result"] == "missed" for r in has), sum(r["result"] == "missed" for r in has)],
                                "cleared": [sum(r["flip_se"] == "own_mean" and r["result"] == "cleared" for r in has), sum(r["result"] == "cleared" for r in has)]}
    w = R["own_where_it_has_se"]
    out("   inequality (row flag) x flipping input: %s" % R["inequality_crosstab"])
    out("   where own_mean HAS a standard error (%d rows) it is the flipping input on %d: missed %d of %d, cleared %d of %d"
        % (w["n"], w["own"], w["missed"][0], w["missed"][1], w["cleared"][0], w["cleared"][1]))
    odds, pf = sps.fisher_exact([[q["missed"], int(miss.sum()) - q["missed"]], [q["cleared"], int((~miss).sum()) - q["cleared"]]])
    R["fisher_non_own"] = {"odds": float(odds), "p": float(pf)}
    out("   Fisher exact, non-own_mean rows missed %d/%d against cleared %d/%d: odds ratio %.2f, p %.3f (rows as independent: anti-conservative)"
        % (q["missed"], miss.sum(), q["cleared"], (~miss).sum(), odds, pf))

    # ------------------------------------------------------------------ 2c
    out("\n== 2c. THE NULL THE CLAIM NEEDS: the missed/cleared label permuted (%d permutations)" % PERMS)
    blocks = L.blocks_of(game)
    obs_d = float(own[miss].mean() - own[~miss].mean())
    R["permutation"] = {}
    for scheme in ("within game", "unrestricted"):
        rng = np.random.default_rng(31)
        df, sm = np.empty(PERMS), np.empty(PERMS)
        for d in range(PERMS):
            lab = miss.copy()
            if scheme == "within game":
                for b in blocks:
                    lab[b] = miss[b][rng.permutation(len(b))]
            else:
                lab = miss[rng.permutation(n)]
            sm[d] = own[lab].mean()
            df[d] = sm[d] - own[~lab].mean()
        p = float((1 + (np.abs(df) >= abs(obs_d) - 1e-12).sum()) / (1 + PERMS))
        lo, hi = np.percentile(df, [2.5, 97.5])
        R["permutation"][scheme] = {"observed_T2": obs_d, "null_mean": float(df.mean()), "null_lo": float(lo), "null_hi": float(hi),
                                    "null_sd": float(df.std()), "observed_percentile": float((df <= obs_d + 1e-12).mean()), "p_two_sided": p,
                                    "distinct_null_values": int(len(np.unique(np.round(df, 9)))),
                                    "null_min": float(df.min()), "null_max": float(df.max()),
                                    "share_abs_ge_stated_mde": float((np.abs(df) >= PUB["mde"]["T2"]).mean()),
                                    "missed_share_null_lo": float(np.percentile(sm, 2.5)), "missed_share_null_hi": float(np.percentile(sm, 97.5))}
        x = R["permutation"][scheme]
        out("   %-12s observed T2 %+.4f; null %+.4f [%+.4f, %+.4f] sd %.4f, range [%+.4f, %+.4f], %d distinct values; observed at the "
            "%.0fth pct; permutation p %.3f; |null T2| >= the stated MDE %.3f in %.2f%% of permutations"
            % (scheme, obs_d, x["null_mean"], lo, hi, x["null_sd"], x["null_min"], x["null_max"], x["distinct_null_values"],
               100 * x["observed_percentile"], p, PUB["mde"]["T2"], 100 * x["share_abs_ge_stated_mde"]))
        out("   %-12s missed share of own_mean under the null: [%.3f, %.3f]; observed %.3f"
            % ("", x["missed_share_null_lo"], x["missed_share_null_hi"], float(own[miss].mean())))
    out("   (rows of one player share an outcome and a flipping input, so a row permutation is ANTI-conservative; a p that is large "
        "here is large a fortiori)")

    # ------------------------------------------------------------------ 2d
    out("\n== 2d. THE CEILING: what a T2 could be at all")
    nm_, nc_ = int(miss.sum()), int((~miss).sum())
    k_non = int((~own).sum())
    p0 = float(own.mean())
    R["ceiling"] = {"pooled_own_share": p0, "non_own_rows": k_non,
                    "max_T2_holding_cleared_share": 1.0 - float(own[~miss].mean()),
                    "min_T2_holding_non_own_count": (nm_ - min(k_non, nm_)) / nm_ - 1.0,
                    "max_T2_holding_non_own_count": 1.0 - (nc_ - min(k_non, nc_)) / nc_,
                    "rows_one_missed_row_moves_T2": 1.0 / nm_, "stated_mde_in_missed_rows": PUB["mde"]["T2"] * nm_}
    c = R["ceiling"]
    out("   own_mean is the flipping input on %d of %d rows (%.3f); %d rows are anything else" % (own.sum(), n, p0, k_non))
    out("   own_mean MORE often on misses: the missed share cannot exceed 1, so with the cleared share where it is T2 <= %+.3f - "
        "%.2f of the stated MDE %.3f. A surplus of own_mean on misses the size of the MDE cannot exist on these rows."
        % (c["max_T2_holding_cleared_share"], c["max_T2_holding_cleared_share"] / PUB["mde"]["T2"], PUB["mde"]["T2"]))
    out("   with the %d non-own_mean rows dealt to either arm in any way, T2 lies in [%+.3f, %+.3f]"
        % (k_non, c["min_T2_holding_non_own_count"], c["max_T2_holding_non_own_count"]))
    out("   one missed row changing its flipping input moves T2 by %.4f; the stated MDE is %.1f missed rows"
        % (c["rows_one_missed_row_moves_T2"], c["stated_mde_in_missed_rows"]))

    # ------------------------------------------------------------------ 3
    out("\n== 3. LEAKAGE: NOT RUN. A standard error and a flip value are arithmetic on a fit as of read_at; scrambling later "
        "inputs needs the facts store and the fits rebuilt (the unit's shards took 342-350 s each against a scratch facts copy). "
        "No store was opened here.")

    # ------------------------------------------------------------------ 4
    out("\n== 4. SPECIFICATIONS")
    try:
        k_reg = L.registered_count(rec, "c-42")
        R["registered_count"] = k_reg
        out("   registered_intervals.count = %d" % k_reg)
    except SystemExit as e:
        R["registered_count"] = "REFUSED: %s" % e
        out("   f26lib.registered_count REFUSES: %s" % e)
        k_reg = rec.get("family_size")
        if not isinstance(k_reg, int) or k_reg != len(rec["tests"]):
            raise SystemExit("no family_size in the result file that equals its own test list - no count to correct over")
        out("   the result file carries family_size = %d instead, and lists %d tests; that count is used below, and named as such"
            % (k_reg, len(rec["tests"])))
    kinds = Counter(t["test"] for t in rec["tests"])
    p1 = [t for t in rec["tests"] if t["p"] == 1.0 and t.get("note")]
    p1_by = Counter("%s %s" % (t["test"], t["note"]) for t in p1)
    live = [t for t in rec["tests"] if not (t["p"] == 1.0 and t.get("note"))]
    live_t2 = [t for t in live if t["test"] == "T2"]
    mirror = sum(1 for t in live_t2 if t["input"] == "own_mean" and any(
        u["slice"] == t["slice"] and u["input"] == "group_mean" and abs(u["estimate"] + t["estimate"]) < 1e-12 and abs(u["p"] - t["p"]) < 1e-9
        for u in live_t2))
    zero_slices = sorted({t["slice"] for t in p1 if t["input"] == "own_mean"})
    surv = [key(t) for t in rec["tests"] if t["p_holm"] < 0.05]
    t2p = sorted((t["p"], key(t)) for t in rec["tests"] if t["test"] == "T2")
    R["spec_count"] = {"family": k_reg, "by_kind": dict(kinds), "entered_at_p1": len(p1), "entered_at_p1_by": dict(p1_by),
                       "p1_not_exactly_one": sum(1 for t in p1 if t["p_holm"] != 1.0),
                       "slices_where_own_mean_has_zero_variance": zero_slices, "live_tests": len(live), "live_T2": len(live_t2),
                       "live_T2_exact_mirror_pairs": mirror, "holm_survivors": len(surv),
                       "t2_below_0.05_unadjusted": sum(1 for p, _ in t2p if p < 0.05), "t2_min_p": t2p[0]}
    out("   %d tests (%s); pre-registration says %d / %d T2 -> %s" % (len(rec["tests"]), dict(kinds), PUB["family"], PUB["t2"],
        "MATCH" if len(rec["tests"]) == PUB["family"] and kinds["T2"] == PUB["t2"] else "MISMATCH"))
    out("   entered at p = 1 with a note: %d of %d (%s); every one has Holm p exactly 1: %s" % (len(p1), len(rec["tests"]), dict(p1_by),
        R["spec_count"]["p1_not_exactly_one"] == 0))
    out("   slices where own_mean itself has zero bootstrap variance (share 1 on both arms): %s" % zero_slices)
    out("   so of the %d T2 tests, %d could return anything but p = 1; those are %d own_mean/group_mean pairs that are exact mirrors "
        "(dispersion is never the flipping input) = %d distinct missed-vs-cleared tests, on nested slices of one %d-row set"
        % (kinds["T2"], len(live_t2), mirror, mirror, k_non))
    out("   Holm survivors %d (published %d): %s" % (len(surv), PUB["holm_survivors"], sorted({"%s %s" % (k[1], k[2]) for k in surv})))
    out("   T2 below p 0.05 unadjusted: %d; smallest %.4f at %s" % (R["spec_count"]["t2_below_0.05_unadjusted"], t2p[0][0], t2p[0][1]))
    R["multiplicity"] = {}
    for k, t in (("T1", t1), ("T2", t2)):
        mu = L.multiplicity(t["estimate"], t["se"], (k_reg, 1000), n_blocks=units)
        R["multiplicity"][k] = dict(mu, se=t["se"])
        out("   %s own_mean z %+.2f  p %.3g  Bonferroni survives k=%d %s, k=1000 %s" % (k, mu["z"], mu["p"], k_reg,
            mu["bonferroni"][k_reg]["survives_0.05"], mu["bonferroni"][1000]["survives_0.05"]))
    # how many runs: the history of the script and of the result files on the target branch
    script = "research/counterfactual_uncertainty.py"
    sc = git(src, "log", "--format=%h %cI", "%s..HEAD" % PUB["prereg"], "--", script).splitlines()
    rc = git(src, "log", "--format=%h %cI", "%s..HEAD" % PUB["prereg"], "--", "research/results").splitlines()
    pre = git(src, "log", "-1", "--format=%h %cI", PUB["prereg"])
    changed = git(src, "diff", "--stat", PUB["script_commit"], "HEAD", "--", script)
    prereg_changed = git(src, "diff", "--stat", PUB["prereg"], "HEAD", "--", "docs/C42-uncertainty-scale-preregistration.md")
    outs = sorted(f for f in os.listdir(res) if f.startswith("counterfactual_uncertainty"))
    literal = '"specifications_tried": 1' in open(os.path.join(src, script), encoding="utf-8").read()
    R["runs"] = {"prereg_commit": pre, "commits_touching_script": sc, "commits_touching_results": rc,
                 "script_changed_since_first_commit": bool(changed), "prereg_changed_since_commit": bool(prereg_changed),
                 "result_files": outs, "specifications_tried_is_a_typed_literal": literal}
    out("   pre-registration %s; commits touching the script since: %d (%s); commits touching research/results since: %d (%s)"
        % (pre, len(sc), "; ".join(sc), len(rc), "; ".join(rc)))
    out("   script differs between its first commit %s and HEAD: %s; pre-registration edited after %s: %s; result files: %s"
        % (PUB["script_commit"], bool(changed), PUB["prereg"], bool(prereg_changed), outs))
    out("   the result file's specifications_tried = %s is %s" % (rec.get("specifications_tried"),
        "a literal typed into the script, not a count of anything" if literal else "not a literal in the script"))
    if a.scratch and os.path.isdir(a.scratch):
        cmp_ = {f: sha(os.path.join(a.scratch, f)) == sha(os.path.join(res, f)) for f in outs if f.endswith(".json") and os.path.exists(os.path.join(a.scratch, f))}
        mt = {f: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(os.path.join(a.scratch, f)))) for f in cmp_}
        R["runs"]["scratch"] = {"dir": a.scratch, "files_in_scratch": sorted(os.listdir(a.scratch)), "identical_to_committed": cmp_, "mtime_local": mt}
        out("   the unit's scratch outputs %s: files %s; byte-identical to the committed ones: %s; written (local) %s"
            % (a.scratch, sorted(os.listdir(a.scratch)), cmp_, mt))
    else:
        R["runs"]["scratch"] = "NOT RUN"
        out("   the unit's scratch outputs: NOT RUN (no --scratch)")
    out("   what this cannot see: a run whose output was overwritten in scratch before the one kept. Git shows one script version "
        "and one result commit; it does not show executions.")

    # ------------------------------------------------------------------ 5
    out("\n== 5. MDE")
    R["mde"] = {}
    for k, t in (("T2", t2), ("T1", t1)):
        R["mde"][k] = {"ratio": L.mde_ratio(t["estimate"], t["se"]), "claim": L.mde_claim(PUB["mde"][k], t["se"]),
                       "alt_se": {nm: float(L.block_boot(stat(k, "own_mean"), L.blocks_of(lb), seed=13).std())
                                  for nm, lb in labelings.items() if nm != "week"}}
        m = R["mde"][k]
        out("   %s own_mean est %+.3f SE %.4f  |est|/MDE %.2f (%s); stated MDE %.3f vs 2.8 x SE %.4f -> %s; 2.8 x SE under other blocks: %s"
            % (k, t["estimate"], t["se"], m["ratio"]["ratio"], m["ratio"]["reading"], PUB["mde"][k], m["claim"]["remeasured"],
               "consistent" if m["claim"]["consistent"] else "INCONSISTENT", ", ".join("%s %.3f" % (kk, 2.8 * s) for kk, s in m["alt_se"].items())))
    out("   registered (before the run) T2 MDE %.3f; realised %.3f - %.1fx smaller, because the share landed at %.3f"
        % (rec["registered_mde"]["T2"]["worst case"], t2["mde"], rec["registered_mde"]["T2"]["worst case"] / t2["mde"], p0))

    # ------------------------------------------------------------------ 6
    out("\n== 6. POWER PLANT: a missed-vs-cleared difference of known size through the target's own 48-test family and Holm")
    out("   each rep: 31 games resampled with replacement; missed/cleared labels permuted within game (true difference 0); then each "
        "missed own_mean row moved to group_mean with probability delta / %.3f (expected T2 = -delta); cu.slice_tests on all 8 "
        "slices at seed %d, CL.holm. 'by player-game' moves all of a player-game's missed rows together." % (p0, cu.SEED))
    by_game = {}
    for i_, r in enumerate(rows):
        by_game.setdefault(r["game_id"], []).append(i_)
    glist = sorted(by_game)
    slim = [{"result": r["result"], "flip_se": r["flip_se"], "market": r["market"], "side": r["side"], "band": r["band"],
             "pg": r["gsis_id"]} for r in rows]

    def one(rng, delta, clustered):
        new = []
        for j, g in enumerate(rng.integers(0, len(glist), len(glist))):
            idx = by_game[glist[g]]
            labs = [slim[i_]["result"] for i_ in idx]
            labs = [labs[p_] for p_ in rng.permutation(len(labs))]
            for i_, lab in zip(idx, labs):
                new.append(dict(slim[i_], result=lab, game_id="g%d" % j, pg="g%d|%s" % (j, slim[i_]["pg"])))
        if delta > 0:
            q_ = delta / p0
            if clustered:
                hit = {u: rng.random() < q_ for u in sorted({r["pg"] for r in new})}
                for r in new:
                    if r["result"] == "missed" and r["flip_se"] == "own_mean" and hit[r["pg"]]:
                        r["flip_se"] = "group_mean"
            else:
                for r in new:
                    if r["result"] == "missed" and r["flip_se"] == "own_mean" and rng.random() < q_:
                        r["flip_se"] = "group_mean"
        _s, tt = family(new)
        t = next(x for x in tt if key(x) == ("all", "T2", "own_mean"))
        g_ = next(x for x in tt if key(x) == ("all", "T2", "group_mean"))
        return (t["estimate"], bool(t["p"] < 0.05), bool(t["lo"] is not None and not (t["lo"] <= 0 <= t["hi"])),
                bool(g_["estimate"] > 0 and g_["p_holm"] < 0.05), t["mde"])

    R["plant"] = {"reps_asked": a.plant_reps, "cells": []}
    for clustered in (False, True):
        for delta in PLANT_DELTAS:
            rng = np.random.default_rng(7000 + int(delta * 1000) + (500 if clustered else 0))
            got = []
            for _ in range(a.plant_reps):
                if time.time() - T0 > a.budget_s:
                    R["partial"] = True
                    break
                got.append(one(rng, delta, clustered))
            if not got:
                out("   %-15s delta %.3f: NOT RUN (time budget reached)" % ("by player-game" if clustered else "row by row", delta))
                continue
            g = np.array([(x[0], x[1], x[2], x[3], np.nan if x[4] is None else x[4]) for x in got], dtype=float)
            cell = {"plant": "by player-game" if clustered else "row by row", "delta": delta, "x_stated_mde": delta / PUB["mde"]["T2"],
                    "reps": len(got), "mean_T2": float(g[:, 0].mean()), "rate_p_unadjusted": float(g[:, 1].mean()),
                    "rate_interval_excludes_zero": float(g[:, 2].mean()), "rate_holm_48": float(g[:, 3].mean()),
                    "median_realised_mde": float(np.nanmedian(g[:, 4])) if np.isfinite(g[:, 4]).any() else None,
                    "zero_variance_reps": int(np.isnan(g[:, 4]).sum())}
            R["plant"]["cells"].append(cell)
            out("   %-15s delta %.3f (%.1fx the stated MDE) reps %3d: mean T2 %+.4f; detected at unadjusted p<0.05 %5.1f%%, interval "
                "excludes zero %5.1f%%, Holm over 48 (the registered rule) %5.1f%%; median realised MDE %s; zero-variance reps %d"
                % (cell["plant"], delta, cell["x_stated_mde"], cell["reps"], cell["mean_T2"], 100 * cell["rate_p_unadjusted"],
                   100 * cell["rate_interval_excludes_zero"], 100 * cell["rate_holm_48"],
                   "n/a" if cell["median_realised_mde"] is None else "%.3f" % cell["median_realised_mde"], cell["zero_variance_reps"]))
    for crit in ("rate_p_unadjusted", "rate_holm_48"):
        for plant in ("row by row", "by player-game"):
            cells = [c_ for c_ in R["plant"]["cells"] if c_["plant"] == plant and c_["reps"] == a.plant_reps]
            first = next((c_["delta"] for c_ in cells if c_[crit] >= 0.80), None)
            R["plant"].setdefault("smallest_planted_delta_at_80", {})["%s|%s" % (crit, plant)] = first
            out("   smallest planted delta detected in >= 80%% of reps, %s, %s: %s (grid %s)"
                % (crit, plant, "none on the grid" if first is None else "%.3f" % first, list(PLANT_DELTAS)))
    out("   the plant can only LOWER the missed share: the other direction is capped at %+.3f (2d)." % c["max_T2_holding_cleared_share"])

    R["seconds"] = time.time() - T0
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(R, fo, indent=1, default=str)
    out("\nwrote %s in %.0f s%s" % (a.out, R["seconds"], "  PARTIAL: the time budget cut the power plant short" if R["partial"] else ""))
    return 2 if R["partial"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
