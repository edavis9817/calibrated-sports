"""f-35 target adapter: c-43's "interval between two rungs", FROM c-35's SCRATCH CACHE, through its own functions.

    CLAIM  Kalshi KXNFLREC / KXNFLRSHATT, NFL 2026 weeks 2-4, one instant per game (kickoff - 180 min):
           725 ladders, 3,987 rungs, 48 games, 11,145 rung pairs.
           1  coherence: 0 of 11,145 intervals negative, 0 crossed at the touch.
           2  calibration, registered verdict "a deviation survives correction below its MDE": 6 of 46
              readable cells survive BH q=0.10, none above its pre-run MDE (median 7.7pp); 0 on an
              outcome-independent SE (post hoc). Receptions width 3 pooled -2.85pp [-5.07, -0.81].
              Straddle (S_all) -3.64pp [-7.62, +0.26] vs MDE 6.09pp, "not detected".
           3  cost: two legs 4.36c vs 2.30c nearest single rung at 100: +2.06c [+1.94, +2.17], 18 games
              (16 of them week 3); width 1 costs 34.1% of value, width 4 8.5%.

RUN: research.interval_mass coherence / calibration / cost from the target's worktree on c-35's extract
cache. Every interval claim is handed to the target's own `interval_mass.boot_mean`. A POWER PLANT pushes
simulated settlements (the ladder's own cells, and the same cells made s x wider) through the target's own
`calibration()` and reads its verdict rule.
NOT RUN: the extract (the store is not opened), so there is NO leakage step and "the cache is what the
store holds" is NOT CHECKED. The cache carries no quote timestamp; only the depth snapshot's age.

    python research/f26_reliability/attack_c43.py --src <c-43 worktree> --cache <rows.json> --out <c43.json>
"""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FN = "interval_mass.boot_mean"
PUB = {"ladders": 725, "rungs": 3987, "games": 48, "intervals": 11145, "negative": 0, "crossed": 0,
       "registered": 72, "exist": 50, "readable": 46, "bh": 6, "posthoc_bh": 0, "mde_median_pp": 7.7,
       "verdict": "a deviation survives correction below its MDE",
       "w3": (-2.85, -5.07, -0.81), "w3_mde_pp": 3.41, "S_all": (-3.64, -7.62, 0.26), "S_all_mde_pp": 6.09,
       "two_leg_c": 4.36, "single_c": 2.30, "diff_c": (2.06, 1.94, 2.17), "cost_games": 18,
       "share_w1": 34.1, "share_w4": 8.5, "zero_cell_z": -34.0, "zero_cell_null_z": -1.36,
       "cache_sha256_prefix": "1c5f8b269ba2b9ec"}
ZERO_CELL = "receptions|w=3|0.05-0.10"
PREREG, SCRIPT0, RUN, FINAL = "dbf8b03", "a62133d", "d4b6d5f", "9d876e5"


def git(src, *args):
    try:
        p = subprocess.run(["git", "-C", src] + list(args), capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout.strip()
    except Exception as e:  # noqa: BLE001
        return 1, "git failed: %r" % (e,)


def main():
    t0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plant-reps", type=int, default=80)
    ap.add_argument("--budget-s", type=float, default=540.0, help="wall-clock deadline for the whole adapter")
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    from research import interval_mass as im
    from research import ladder_edges as le
    for mod in (im, le):
        if not os.path.abspath(mod.__file__).startswith(src):
            raise SystemExit("%s resolved outside --src: %s" % (mod.__name__, mod.__file__))
    out = lambda s="": print(s, flush=True)  # noqa: E731
    quiet = lambda *_a: None  # noqa: E731
    deadline = t0 + a.budget_s
    tri = lambda r, k=100.0: (k * r["est"], k * r["lo"], k * r["hi"])  # noqa: E731

    st = os.stat(a.cache)
    sha = hashlib.sha256(open(a.cache, "rb").read()).hexdigest()
    out("cache %s: %d bytes, mtime %s, sha256 %s (pre-registration names %s...: %s)"
        % (a.cache, st.st_size, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)), sha,
           PUB["cache_sha256_prefix"], "MATCH" if sha.startswith(PUB["cache_sha256_prefix"]) else "DIFFERENT FILE"))
    rcd, head = git(src, "rev-parse", "--short", "HEAD")
    out("target worktree %s at %s" % (src, head))
    resdir = os.path.join(src, "research", "results")
    rec = json.load(open(os.path.join(resdir, "interval_mass.json"), encoding="utf-8"))
    mj = json.load(open(os.path.join(resdir, "interval_mde.json"), encoding="utf-8"))
    R = {"target": "c-43", "cache": {"path": a.cache, "bytes": st.st_size, "mtime": st.st_mtime, "sha256": sha},
         "pipeline_rerun": "analysis only (coherence/calibration/cost on c-35's cache); extract NOT re-run",
         "leakage_step": "NOT RUN", "cache_equals_store": "NOT CHECKED"}

    raw = json.load(open(a.cache, encoding="utf-8"))["rows"]          # untouched copy, for step 3
    rows, _meta = im.load(a.cache, outcomes=True)
    ladders = im.build(rows)
    shape = im.check_shape(ladders)
    ivs = im.intervals(ladders)
    if not ivs:
        raise SystemExit("no intervals - refusing")
    games_all = sorted({l["game"] for l in ladders})                  # counted here, from game ids
    out("cache: %d ladders, %d rungs, %d distinct games (counted here), %d intervals; ladders by week %s"
        % (shape[0], shape[1], len(games_all), len(ivs), dict(sorted(Counter(l["week"] for l in ladders).items()))))

    # ------------------------------------------------------------------ 1
    out("\n== 1. REPRODUCE (target's coherence/calibration/cost on the cache; same seeds, so bounds must match)")
    mde = dict(mj["family"])
    mde.update({"pre|" + k: v for k, v in mj["precheck"].items()})
    coh = im.coherence(ladders, ivs, quiet)
    cal = im.calibration(ladders, ivs, mde, quiet)
    cst = im.cost(ladders, ivs, quiet)
    allb = cst["100|buy"]["all_tested_widths"]
    w3 = cal["cells"]["receptions|w=3|all"]
    sall = cal["precheck"]["S_all"]
    bw = cst["100|buy"]["by_width"]
    checks = [("ladders", shape[0], PUB["ladders"], 0), ("rungs", shape[1], PUB["rungs"], 0),
              ("games", len(games_all), PUB["games"], 0), ("intervals", len(ivs), PUB["intervals"], 0),
              ("negative mass", coh["total"]["C1_negative"], PUB["negative"], 0),
              ("crossed at touch", coh["total"]["C3_crossed_at_touch"], PUB["crossed"], 0),
              ("cells registered", cal["n_registered"], PUB["registered"], 0),
              ("cells that exist", cal["n_exist"], PUB["exist"], 0),
              ("cells readable", cal["n_readable"], PUB["readable"], 0),
              ("BH survivors", cal["n_bh"], PUB["bh"], 0),
              ("survivors above pre-run MDE", sum(s["exceeds_mde"] for s in cal["survivors"]), 0, 0),
              ("post hoc BH survivors", cal["posthoc_null_se"]["n_bh"], PUB["posthoc_bh"], 0),
              ("pre-run MDE median pp", 100 * cal["mde_prerun_median"], PUB["mde_median_pp"], 1),
              ("w3 pre-run MDE pp", 100 * w3["mde_prerun"], PUB["w3_mde_pp"], 2),
              ("S_all pre-run MDE pp", 100 * sall["mde_prerun"], PUB["S_all_mde_pp"], 2),
              ("two-leg cost c (buy,100)", allb["cost_c_mean"], PUB["two_leg_c"], 2),
              ("nearest single c", allb["single_c_mean"], PUB["single_c"], 2),
              ("cost games", allb["games"], PUB["cost_games"], 0),
              ("w=1 cost share of value %", 100 * bw["receptions|w=1"]["cost_share_of_value"], PUB["share_w1"], 1),
              ("w=4 cost share of value %", 100 * bw["receptions|w=4"]["cost_share_of_value"], PUB["share_w4"], 1)]
        # the three interval claims
    for nm, got, pub in (("rec w=3 pooled pp", tri(w3), PUB["w3"]), ("S_all pp", tri(sall), PUB["S_all"]),
                         ("cost diff c", tri(allb["diff"]), PUB["diff_c"])):
        checks += [("%s %s" % (nm, w), g, p, 2) for w, g, p in zip(("est", "lo", "hi"), got, pub)]
    R["reproduce"] = [L.reproduce(*c) for c in checks]
    for r in R["reproduce"]:
        out("   %-30s measured %+12.5f  published %+10.4f -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    R["verdict_string"] = {"measured": cal["verdict"], "published": PUB["verdict"],
                           "reproduces": cal["verdict"] == PUB["verdict"]}
    out("   verdict (target's rule): %r -> %s" % (cal["verdict"], "REPRODUCES" if R["verdict_string"]["reproduces"]
                                                 else "DOES NOT REPRODUCE"))
    out("   survivors: %s" % [s["name"] for s in cal["survivors"]])
    norm_ = lambda o: json.loads(json.dumps(o, default=str))  # noqa: E731
    same = {k: norm_(v) == rec[k] for k, v in (("coherence", coh), ("calibration", cal), ("cost", cst))}
    R["identical_to_committed_result"] = same
    out("   recomputed blocks identical to the committed interval_mass.json: %s" % same)
    if not all(r["reproduces"] for r in R["reproduce"]) or not R["verdict_string"]["reproduces"]:
        out("   STEP 1 FAILED on at least one figure - the later steps are still printed, read them with that in mind")
    need_depth = sum(1 for r in raw if r["depth"])
    cg = Counter()
    pr = im.priced_pairs(ladders, ivs, 100, "buy")
    pr = [r for r in pr if r["iv"]["w"] in im.WIDTHS[r["iv"]["stat"]]]
    gw = {l["game"]: l["week"] for l in ladders}
    for g in {r["iv"]["game"] for r in pr}:
        cg[gw[g]] += 1
    R["cost_population"] = {"rungs_with_any_depth": need_depth, "rungs": len(raw), "pairs": len(pr),
                            "games_by_week": dict(cg)}
    out("   COST needs depth: it is IN the cache (%d of %d rungs carry a depth snapshot); %d priced pairs on %d "
        "games, by week %s (counted here)" % (need_depth, len(raw), len(pr), sum(cg.values()), dict(sorted(cg.items()))))

    # ------------------------------------------------------------------ rows behind each attacked claim
    fam, pre = im.test_cells(ivs)
    lad_of = lambda v: ladders[v["lad"]]  # noqa: E731

    def date_of(v):                                                   # kickoff DATE, from the Kalshi ticker
        return lad_of(v)["rungs"][0]["mid"].split("-")[1][:7]

    def cal_rows(idx):
        return [{"game": ivs[n]["game"], "week": gw[ivs[n]["game"]], "gsis": lad_of(ivs[n])["gsis"],
                 "lad": ivs[n]["lad"], "date": date_of(ivs[n]), "d": ivs[n]["h"] - ivs[n]["p"]} for n in idx]
    cost_rows = [{"game": r["iv"]["game"], "week": gw[r["iv"]["game"]], "gsis": lad_of(r["iv"])["gsis"],
                  "lad": r["iv"]["lad"], "date": date_of(r["iv"]), "d": r["cost"] - r["single"],
                  "cost": r["cost"], "single": r["single"], "fee": r["fee"], "lower": r["lower_leg"],
                  "upper": r["upper_leg"]} for r in pr]
    CL = {"cost diff (buy,100)": (cost_rows, "cost|100|buy|all"),
          "rec w=3 pooled": (cal_rows(fam["receptions|w=3|all"]), "receptions|w=3|all"),
          "S_all straddle": (cal_rows(pre["S_all"]), "pre|S_all"),
          "zero-hit cell": (cal_rows(fam[ZERO_CELL]), ZERO_CELL)}

    out("\n== 2. BLOCKS: THROUGH %s (the target's function), then rows vs units, then other blocks" % FN)
    R["through"], R["alt_blocks"], R["iid_contrast"], R["own"] = {}, {}, {}, {}
    for nm, (rr, seedname) in CL.items():
        units = len({r["game"] for r in rr})

        def width(x, seedname=seedname):
            q = im.boot_mean([r["d"] for r in x], [r["game"] for r in x], seedname, draws=1000)
            return q["hi"] - q["lo"]
        t = L.duplication_through(width, rr, "game", fn_name=FN, units=units)
        R["through"][nm] = t
        out("   %-20s %s" % (nm, L.through_line(t)))
        d = np.array([r["d"] for r in rr])
        n = len(rr)
        f = lambda i, d=d: float(d[i].mean())  # noqa: E731
        own = im.boot_mean(d, [r["game"] for r in rr], seedname)       # the target's function, its own seed
        other = im.boot_mean(d, [r["game"] for r in rr], seedname + "|f35-reseed")
        R["own"][nm] = {"own": own, "reseeded": other,
                        "lo_within_mc": L.same_within_mc(other["lo"], own["lo"], own["se"]),
                        "hi_within_mc": L.same_within_mc(other["hi"], own["hi"], own["se"])}
        R["iid_contrast"][nm] = L.iid_contrast(f, n, [r["game"] for r in rr], seed=11)
        out("   %-20s est %+.5f; %d rows in %d games (%.0f rows a game); %d ladders, %d players; an unblocked "
            "interval would be x%.2f the width" % (nm, f(np.arange(n)), n, units, n / units, len({r["lad"] for r in rr}),
                                                   len({r["gsis"] for r in rr}), R["iid_contrast"][nm]["iid_over_blocked_width"]))
        labelings = {"game": [r["game"] for r in rr], "week": [r["week"] for r in rr],
                     "kickoff date": [r["date"] for r in rr], "player": [r["gsis"] for r in rr],
                     "ladder": [r["lad"] for r in rr]}
        R["alt_blocks"][nm] = L.alt_blocks(f, n, labelings, seed=12)
        for bn, r in R["alt_blocks"][nm].items():
            out("   %-20s %-12s (%3d blocks) %s" % (nm, bn, r["n_blocks"], L.fmt(r, 5)))
    R["through_verdicts"] = L.require_through(list(CL), R["through"])
    # the cost claim: one week carries it
    by_week, by_game = {}, {}
    for r in cost_rows:
        by_week.setdefault(r["week"], []).append(r["d"])
        by_game.setdefault(r["game"], []).append(r["d"])
    gm = {g: float(np.mean(v)) for g, v in by_game.items()}
    R["cost_by_week"] = {w: {"pairs": len(v), "games": cg[w], "mean_c": 100 * float(np.mean(v))} for w, v in by_week.items()}
    R["cost_game_means_c"] = {"min": 100 * min(gm.values()), "max": 100 * max(gm.values()),
                              "games_positive": sum(v > 0 for v in gm.values()), "games": len(gm),
                              "pairs_in_largest_game": max(len(v) for v in by_game.values())}
    out("   cost diff by week (descriptive; 3 week blocks cannot be read): %s" % R["cost_by_week"])
    out("   cost diff, per-game means in cents: %s" % R["cost_game_means_c"])
    eq = sum(1 for g in gm.values()) and float(np.mean(list(gm.values())))
    out("   cost diff, mean of the 18 game means (each game one vote): %+.3fc against the pair-weighted %+.3fc"
        % (100 * eq, 100 * float(np.mean([r["d"] for r in cost_rows]))))
    R["cost_equal_game_weight_c"] = 100 * eq

    # ------------------------------------------------------------------ 3
    out("\n== 3. LEAKAGE: NOT RUN - the extract is not re-run and the store is not opened, so nothing is scrambled.")
    keys = sorted({k for r in raw for k in r})
    R["cache_row_keys"] = keys
    out("   cache row keys: %s" % keys)
    out("   The cache carries NO quote timestamp and NO kickoff time: 'every quote is at or before kickoff - 180 min' "
        "is NOT CHECKABLE from it. The one clock it carries is the depth snapshot's age = entry - snapshot time.")

    def audit(rws):
        bad_age = bad_book = 0
        ages = []
        for r in rws:
            if not (0 < r["bid"] < r["ask"] < 1) or abs(r["k"] - (r["bid"] + r["ask"]) / 2) > 1e-9:
                bad_book += 1
            for d in r["depth"].values():
                ages.append(d["age"])
                if not (0 <= d["age"] <= 600):
                    bad_age += 1
        return {"depth_snapshots": len(ages), "age_outside_0_600s": bad_age, "not_two_sided_or_k_not_mid": bad_book,
                "age_min": min(ages) if ages else None, "age_max": max(ages) if ages else None}
    R["depth_age_audit"] = audit(raw)
    out("   depth-age audit (0 <= age <= 600 s, i.e. snapshot at or before entry; two-sided book; k is the mid): %s"
        % R["depth_age_audit"])
    planted = copy.deepcopy(raw[:50])
    tgt = next(r for r in planted if r["depth"])
    side = next(iter(tgt["depth"]))
    tgt["depth"][side]["age"] = -5.0                                   # a snapshot taken AFTER entry
    planted[1]["bid"], planted[1]["ask"] = 0.6, 0.5                    # a crossed book
    base, pl = audit(raw[:50]), audit(planted)
    fires = pl["age_outside_0_600s"] == base["age_outside_0_600s"] + 1 and \
        pl["not_two_sided_or_k_not_mid"] == base["not_two_sided_or_k_not_mid"] + 1
    R["depth_age_plant"] = {"base": base, "planted": pl, "fires": bool(fires)}
    out("   PLANT (in memory: one depth age set to -5 s, one book crossed) on 50 rows: before %d/%d, after %d/%d -> %s"
        % (base["age_outside_0_600s"], base["not_two_sided_or_k_not_mid"], pl["age_outside_0_600s"],
           pl["not_two_sided_or_k_not_mid"], "check FIRES" if fires else "CHECK DID NOT FIRE"))
    if not fires:
        raise SystemExit("the depth-age audit cannot fail - stop")

    # ------------------------------------------------------------------ 4
    out("\n== 4. SPECIFICATIONS")
    try:
        L.registered_count(rec, "c-43")
        R["registered_count"] = "present"
    except SystemExit as e:
        R["registered_count"] = "REFUSED: %s" % e
        out("   f26lib.registered_count -> REFUSED (%s)" % e)
        out("   so k is read from a DIFFERENT key, and labelled: calibration.n_registered = %d, n_exist = %d, "
            "n_readable = %d; precheck family %d; specifications_tried (the unit's own field) = %s"
            % (rec["calibration"]["n_registered"], rec["calibration"]["n_exist"], rec["calibration"]["n_readable"],
               len(rec["calibration"]["precheck"]), rec.get("specifications_tried")))
    cells = cal["cells"]
    names = list(cells)
    ps = [cells[n]["p"] for n in names]
    n_cost_iv = sum(1 for k in cst for grp in ("by_width", "by_bin") for _ in cst[k].get(grp, {})) + \
        sum(1 for k in cst if "all_tested_widths" in cst[k])
    R["spec"] = {"family_registered": cal["n_registered"], "exist": cal["n_exist"], "readable": cal["n_readable"],
                 "precheck_registered": len(cal["precheck"]), "cost_intervals_printed_no_family": n_cost_iv,
                 "bh_family_used_by_target": len(ps)}
    out("   registered: %d family cells (%d exist, %d readable) + %d pre-check intervals; the cost arm prints %d "
        "intervals in NO family (registered as 'no test, no verdict')" % (cal["n_registered"], cal["n_exist"],
                                                                         cal["n_readable"], len(cal["precheck"]), n_cost_iv))
    fams = {"the %d that exist (the target's BH)" % len(ps): ps,
            "the 72 registered (22 absent at p=1)": ps + [1.0] * (cal["n_registered"] - len(ps)),
            "72 + the 9 pre-check, one family": ps + [1.0] * (cal["n_registered"] - len(ps))
            + [im.pval(r) for r in cal["precheck"].values()]}
    R["spec"]["bh"] = {}
    for nm, pp in fams.items():
        keep = le.bh(pp, im.BH_Q)
        R["spec"]["bh"][nm] = {"k": len(pp), "bh_survivors": len(keep), "holm": len(im.holm(pp))}
        out("   BH q=0.10 over %-40s k=%2d: %d survivor(s); Holm 0.05: %d" % (nm, len(pp), len(keep), len(im.holm(pp))))
    R["spec"]["six"] = {}
    out("   the six BH survivors, Bonferroni on z = est / SE over 46 / 50 / 72 / 81, on the bootstrap SE and on the "
        "pre-run (outcome-independent) SE:")
    for s in cal["survivors"]:
        c = cells[s["name"]]
        mb = L.multiplicity(c["est"], c["se"], (46, 50, 72, 81), n_blocks=c["games"])
        mn = L.multiplicity(c["est"], c["mde_prerun"] / 2.8, (46, 50, 72, 81), n_blocks=c["games"])
        R["spec"]["six"][s["name"]] = {"n": c["n"], "games": c["games"], "realised": c["realised"], "bootstrap": mb, "null_se": mn}
        out("     %-28s n %4d g %2d est %+.4f | boot SE %.4f z %+6.2f Bonf(72) %s | null SE %.4f z %+5.2f Bonf(72) %s"
            % (s["name"], c["n"], c["games"], c["est"], c["se"], mb["z"],
               "survives" if mb["bonferroni"][72]["survives_0.05"] else "fails", c["mde_prerun"] / 2.8, mn["z"],
               "survives" if mn["bonferroni"][72]["survives_0.05"] else "fails"))
    R["spec"]["six_bonferroni72_bootstrap"] = sum(v["bootstrap"]["bonferroni"][72]["survives_0.05"] for v in R["spec"]["six"].values())
    R["spec"]["six_bonferroni72_null_se"] = sum(v["null_se"]["bonferroni"][72]["survives_0.05"] for v in R["spec"]["six"].values())
    out("   Bonferroni over 72: %d of 6 survive on the bootstrap SE, %d of 6 on the outcome-independent SE"
        % (R["spec"]["six_bonferroni72_bootstrap"], R["spec"]["six_bonferroni72_null_se"]))
    # what changed between the pre-registration and the run
    R["git"] = {}
    for nm, args in (("commits prereg..final (author time)", ("log", "--format=%h %ad %s", "--date=iso", "--reverse", PREREG + "^.." + FINAL, "--",
                                                              "research/interval_mass.py", "docs/C43-interval-preregistration.md",
                                                              "research/results/interval_mass.json", "research/results/interval_mde.json")),
                     ("script: first written -> registered run", ("diff", "--shortstat", SCRIPT0, RUN, "--", "research/interval_mass.py")),
                     ("script: registered run -> final", ("diff", "--shortstat", RUN, FINAL, "--", "research/interval_mass.py")),
                     ("prereg: as registered -> final", ("diff", "--shortstat", PREREG, FINAL, "--", "docs/C43-interval-preregistration.md")),
                     ("prereg lines REMOVED after registration", ("diff", "--numstat", PREREG, FINAL, "--", "docs/C43-interval-preregistration.md"))):
        rc_, txt = git(src, *args)
        R["git"][nm] = txt if rc_ == 0 else "GIT FAILED rc=%s %s" % (rc_, txt)
        out("   git %s:\n%s" % (nm, "\n".join("       " + ln[:150] for ln in R["git"][nm].splitlines())))
    rc_, first_txt = git(src, "show", RUN + ":research/results/interval_mass.json")
    if rc_ == 0:
        fr = json.loads(first_txt)
        diffs = []

        def walk(x, y, path=""):
            if isinstance(x, dict) and isinstance(y, dict):
                for k in sorted(set(x) | set(y)):
                    if k not in x or k not in y:
                        diffs.append(path + "/" + k + (" (only in final)" if k not in x else " (only in first)"))
                    else:
                        walk(x[k], y[k], path + "/" + k)
            elif x != y:
                diffs.append(path)
        walk(fr, rec)
        R["first_run_vs_final"] = diffs
        out("   result file at the registered-run commit %s vs the final one: %d differing path(s) %s"
            % (RUN, len(diffs), diffs[:6]))
    else:
        R["first_run_vs_final"] = "NOT RUN: git show failed"
        out("   first-run result: NOT RUN (git show failed)")
    out("   READING, not code: all six commits dbf8b03..9d876e5 carry author times inside ~10 minutes, so the commit "
        "ORDER is on record and the order of the WORK is not; the cache (with outcomes in it) pre-dates all of them.")

    # ------------------------------------------------------------------ 5
    out("\n== 5. MDE and the nulls the claims need")
    R["mde"] = {}
    for nm, stated, cell in (("rec w=3 pooled", PUB["w3_mde_pp"] / 100, w3), ("S_all straddle", PUB["S_all_mde_pp"] / 100, sall)):
        rs = R["own"][nm]["reseeded"]
        R["mde"][nm] = {"ratio": L.mde_ratio(rs["est"], rs["se"]), "claim": L.mde_claim(stated, rs["se"]),
                        "vs_prerun": abs(rs["est"]) / stated}
        out("   %-18s est %+.4f boot SE %.5f (target's fn, another seed)  |est|/2.8SE %.2f (%s); stated pre-run MDE %.4f vs "
            "2.8 x SE %.4f -> %s (rel %+.2f); |est| / stated MDE %.2f"
            % (nm, rs["est"], rs["se"], R["mde"][nm]["ratio"]["ratio"], R["mde"][nm]["ratio"]["reading"], stated,
               2.8 * rs["se"], "consistent" if R["mde"][nm]["claim"]["consistent"] else "INCONSISTENT",
               R["mde"][nm]["claim"]["rel_diff"], R["mde"][nm]["vs_prerun"]))
    out("   (mde_claim's tolerance is 10%. rel > 0 means the unit STATED A LARGER MDE than 2.8 x the bootstrap SE - it "
        "claimed less power than the bootstrap gives it, the direction that cannot dress a null as powered)")
    rd = [k for k in names if cells[k]["games"] >= im.MIN_GAMES and cells[k]["se"]]
    boot_mdes = [2.8 * cells[k]["se"] for k in rd]
    pre_mdes = [cells[k]["mde_prerun"] for k in rd]
    cl = L.mde_claim(PUB["mde_median_pp"] / 100, float(np.median(boot_mdes)) / 2.8)
    ratio = np.array(boot_mdes) / np.array(pre_mdes)
    R["mde"]["median_cell"] = {"claim": cl, "prerun_median": float(np.median(pre_mdes)), "bootstrap_median": float(np.median(boot_mdes)),
                               "boot_over_pre_min": float(ratio.min()), "boot_over_pre_p10": float(np.percentile(ratio, 10)),
                               "cells_boot_under_half_pre": int((ratio < 0.5).sum()), "readable": len(rd)}
    out("   readable cells %d: stated MDE median %.4f vs median of 2.8 x bootstrap SE %.4f -> %s; bootstrap/pre-run ratio "
        "min %.3f p10 %.2f; cells whose bootstrap MDE is under half the pre-run one: %d"
        % (len(rd), PUB["mde_median_pp"] / 100, float(np.median(boot_mdes)), "consistent" if cl["consistent"] else "INCONSISTENT",
           ratio.min(), np.percentile(ratio, 10), int((ratio < 0.5).sum())))
    # 5c the zero-hit cell
    zc = cells[ZERO_CELL]
    zrows = CL["zero-hit cell"][0]
    hits = sum(ivs[n]["h"] for n in fam[ZERO_CELL])
    nsd = im.null_sd(ladders, ivs, {ZERO_CELL: fam[ZERO_CELL]})[ZERO_CELL]
    nsd2 = im.null_sd(ladders, ivs, {ZERO_CELL: fam[ZERO_CELL]}, seed=3535)[ZERO_CELL]
    p_all_miss_indep = float(np.prod([1 - ivs[n]["p"] for n in fam[ZERO_CELL]]))
    R["zero_cell"] = {"n": zc["n"], "games": zc["games"], "ladders": len({r["lad"] for r in zrows}), "hits": hits,
                      "est": zc["est"], "boot_se": zc["se"], "z_boot": zc["est"] / zc["se"],
                      "null_se_target_seed": nsd, "z_null": zc["est"] / nsd, "null_se_other_seed": nsd2,
                      "prob_all_miss_if_priced_right_indep": p_all_miss_indep}
    out("   zero-hit cell %s: %d intervals, %d ladders, %d games, %d hit; est %+.4f; bootstrap SE %.5f -> z %+.1f "
        "(published %+.0f); SE under the ladder's own cells %.4f (other seed %.4f) -> z %+.2f (published %+.2f)"
        % (ZERO_CELL, zc["n"], len({r["lad"] for r in zrows}), zc["games"], hits, zc["est"], zc["se"], zc["est"] / zc["se"],
           PUB["zero_cell_z"], nsd, nsd2, zc["est"] / nsd, PUB["zero_cell_null_z"]))
    out("   ... and if every one of them were priced right and independent, P(all %d miss) = %.3f - an ordinary outcome"
        % (zc["n"], p_all_miss_indep))
    R["all_zero_or_all_one_cells"] = [k for k in names if cells[k]["n"] > 1 and cells[k]["realised"] in (0.0, 1.0)]
    out("   cells whose realised rate is exactly 0 or 1: %s" % R["all_zero_or_all_one_cells"])

    # 5a the cost claim: what could have come out otherwise
    c = np.array([r["cost"] for r in cost_rows])
    s = np.array([r["single"] for r in cost_rows])
    lo_, up_ = np.array([r["lower"] for r in cost_rows]), np.array([r["upper"] for r in cost_rows])
    fee = np.array([r["fee"] for r in cost_rows])
    g_ = [r["game"] for r in cost_rows]
    R["cost_null"] = {"rows": len(c), "share_diff_positive": float((c - s > 1e-12).mean()),
                      "rows_diff_le_0": int((c - s <= 1e-12).sum()), "min_diff_c": 100 * float((c - s).min()),
                      "share_each_leg_cost_positive": float(((lo_ > 0) & (up_ > 0)).mean()),
                      "share_single_is_cheaper_than_cheaper_own_leg": float((s < np.minimum(lo_, up_) - 1e-12).mean()),
                      "share_single_ge_dearer_own_leg": float((s >= np.maximum(lo_, up_) - 1e-12).mean()),
                      "fee_two_legs_c": 100 * float(fee.mean()),
                      "ratio_two_leg_over_single": float(c.mean() / s.mean())}
    twice = im.boot_mean(c - 2 * s, g_, "f35|c-2s")
    vs_own = im.boot_mean(c - (lo_ + up_), g_, "f35|identity")
    R["cost_null"]["two_leg_minus_twice_single"] = twice
    R["cost_null"]["identity_cost_minus_sum_of_legs_max_abs"] = float(np.abs(c - (lo_ + up_)).max())
    out("   COST: two-leg minus nearest single is > 0 on %.4f of %d pairs (%d pairs <= 0, min %+.3fc); both of the pair's "
        "own legs cost > 0 on %.4f of pairs (VWAP on the bought side + fee, less the mid)"
        % (R["cost_null"]["share_diff_positive"], len(c), R["cost_null"]["rows_diff_le_0"], R["cost_null"]["min_diff_c"],
           R["cost_null"]["share_each_leg_cost_positive"]))
    out("   COST: the nearest single rung costs at least the pair's dearer own leg on %.3f of pairs, less than its cheaper "
        "own leg on %.3f; identity cost == lower + upper holds to %.1e (bootstrap of the identity: SE %s)"
        % (R["cost_null"]["share_single_ge_dearer_own_leg"], R["cost_null"]["share_single_is_cheaper_than_cheaper_own_leg"],
           R["cost_null"]["identity_cost_minus_sum_of_legs_max_abs"], vs_own["se"]))
    out("   COST, a null that COULD read either way - 'the pair costs twice a single rung' (two-leg - 2 x single), cents: "
        "%+.3f [%+.3f, %+.3f] on %d games; ratio two-leg / single %.3f"
        % (*tri(twice), twice["games"], R["cost_null"]["ratio_two_leg_over_single"]))

    # 5b POWER PLANT: simulated settlements through the target's own calibration() and verdict rule
    out("\n== 5b. POWER PLANT through im.calibration (the target's verdict rule), settlements simulated from the ladders")
    out("   each ladder's count is drawn from its own implied cells with the survival curve made s x wider on the probit "
        "scale (s = 1: priced exactly right; the pre-registration's own mechanism, which states s = 1.067 for 1.5-3pp); "
        "ladders independent, as in the target's own MDE simulation")
    from scipy.stats import norm
    lad2, ivs2 = copy.deepcopy(ladders), None
    ivs2 = im.intervals(lad2)
    fam2, pre2 = im.test_cells(ivs2)
    p_arr = np.array([v["p"] for v in ivs2])
    rng = np.random.default_rng(3543)
    R["plant"] = {}
    scales = (1.0, 1.15, 1.25, 1.067, 1.4)                             # the informative ones first: a budget may cut the tail
    reps = a.plant_reps
    t_plant = time.time()
    per = None
    for sc in scales:
        if time.time() > deadline:
            out("   s = %.3f: NOT RUN (the adapter's wall-clock budget of %.0f s was reached)" % (sc, a.budget_s))
            R["plant"]["%.3f" % sc] = "NOT RUN: budget"
            continue
        cellsq = []
        for l in lad2:
            sv = np.clip(np.array(le.pav_decreasing(l["raw"]), float), 1e-6, 1 - 1e-6)
            q = np.clip(le.ladder_cells(l["lines"], list(norm.cdf(norm.ppf(sv) / sc))), 0, None)
            cellsq.append(q / q.sum())
        tally, eff, eff3, done = Counter(), [], [], 0
        for _ in range(reps):
            if time.time() > deadline:
                break
            for l, q in zip(lad2, cellsq):
                J = int(rng.choice(len(q), p=q))
                l["x"] = l["lines"][0] - 0.5 if J == 0 else l["lines"][J - 1] + 0.5
            for v in ivs2:
                l = lad2[v["lad"]]
                v["h"] = im.hit(l["lines"][v["i"]], l["lines"][v["j"]], l["x"])
            r_ = im.calibration(lad2, ivs2, mde, quiet)
            h_arr = np.array([v["h"] for v in ivs2])
            done += 1
            tally["verdict: " + r_["verdict"]] += 1
            tally["S_all: " + r_["precheck_reading"]["straddle"]] += 1
            tally["any BH survivor"] += r_["n_bh"] > 0
            tally["any Holm survivor"] += r_["n_holm"] > 0
            tally["posthoc (null SE) any BH survivor"] += r_["posthoc_null_se"]["n_bh"] > 0
            tally["bh>=6"] += r_["n_bh"] >= 6
            eff.append(float((h_arr[pre2["S_all"]] - p_arr[pre2["S_all"]]).mean()))
            i3 = fam2["receptions|w=3|all"]
            eff3.append(float((h_arr[i3] - p_arr[i3]).mean()))
            if per is None:
                per = time.time() - t_plant
        if not done:
            R["plant"]["%.3f" % sc] = "NOT RUN: budget"
            out("   s = %.3f: NOT RUN (budget)" % sc)
            continue
        e = {"reps": done, "true_S_all_effect_pp": 100 * float(np.mean(eff)), "sd_S_all_pp": 100 * float(np.std(eff)),
             "true_w3_effect_pp": 100 * float(np.mean(eff3)), "sd_w3_pp": 100 * float(np.std(eff3)),
             **{k: v / done for k, v in sorted(tally.items())}}
        R["plant"]["%.3f" % sc] = e
        out("   s = %.3f (%d reps): true S_all effect %+.2fpp (sd across simulated seasons %.2f), w=3 pooled %+.2fpp (sd %.2f)"
            % (sc, done, e["true_S_all_effect_pp"], e["sd_S_all_pp"], e["true_w3_effect_pp"], e["sd_w3_pp"]))
        out("        P(any BH survivor) %.2f  P(>= 6 BH survivors) %.2f  P(any Holm) %.2f  P(any BH survivor on the null SE, "
            "post hoc rule) %.2f" % (e["any BH survivor"], e["bh>=6"], e["any Holm survivor"], e["posthoc (null SE) any BH survivor"]))
        out("        verdicts: %s" % {k[9:]: round(v, 2) for k, v in e.items() if isinstance(k, str) and k.startswith("verdict: ")})
        out("        S_all reading: %s" % {k[7:]: round(v, 2) for k, v in e.items() if isinstance(k, str) and k.startswith("S_all: ")})
    R["plant_seconds_per_rep_first"] = per
    R["wall_s"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(R, fo, indent=1, default=str)
    out("\nwrote %s; wall %.0f s" % (a.out, R["wall_s"]))


if __name__ == "__main__":
    main()
