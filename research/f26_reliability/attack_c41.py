"""f-31 target adapter: c-41, "what predicts the residual given the line" (research/residual_given_line.py).

    CLAIM  A NULL. NFL receiving-yards overs, regular season 2023-2025, de-vigged DK/FD/MGM bench
           price ~14 min before kickoff as the forecast: 0 of 6 pre-registered linear candidates pass
           the registered rule (Holm over 42 + same sign in 3 seasons + walk-forward Brier gain beyond
           its realized MDE). 18,666 rungs / 10,016 player-games / 814 games. Nearest miss team_total
           +0.0148 [+0.0048, +0.0242], Holm p 0.12, Brier gain 0.000140 vs MDE 0.000141. Intercept
           arm -0.00047 [-0.00074, -0.00019].

READ-ONLY, ROWS-IN. This file opens no store. It takes the per-rung rows the target's own script wrote
(`--out-dir`/rows.json), either the unit's scratch copy or a re-run made today:

    python -m research.residual_given_line --db <market_log.db> --out-dir <scratch> --results <scratch>/result.json
    python research/f26_reliability/attack_c41.py --src <c-41 worktree> --rows <scratch>/rows.json \
        [--rerun <scratch>/result.json] [--plant-reps 100] --out <scratch>/c41.json

WHAT IS RUN  1 reproduce (target's analyse() + verdict() on the rows, against the committed json, all 46
             tests); 2 blocks THROUGH M.coef_test / M.brier_test with M.block_weights; 3 leakage at
             FUNCTION level only (features / prior_games / walk_forward, each with a planted leak);
             4 specification count; 5 MDE; 6 POWER - a synthetic candidate of known size pushed through
             the target's own analyse() + verdict(), in the slot of one real candidate.
WHAT IS NOT  build() is not re-run under a scramble (no store-level leakage audit); team_total's input
             (untimestamped nflverse closing lines) is not compared with any timestamped line.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = {"team_total_b": 0.0148, "team_total_lo": 0.0048, "team_total_hi": 0.0242, "team_total_holm": 0.12,
       "tt_brier": -0.000140, "tt_brier_mde": 0.000141, "a_brier": -0.00047, "a_lo": -0.00074, "a_hi": -0.00019,
       "rungs": 18666, "player_games": 10016, "games": 814, "registered": 42, "specifications": 46,
       "se_registered": 0.0050, "mde_registered": 0.0140, "brier_bar_b": 0.033}
SLOT = "log_line"            # the real candidate whose column the synthetic one replaces (a pooled null)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--rerun", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plant-reps", type=int, default=0)
    ap.add_argument("--plant-b", default="0,0.0148,0.020,0.033,0.040,0.050,0.060")
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from research import residual_given_line as M
    if not os.path.abspath(M.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.residual_given_line resolved outside --src: %s" % M.__file__)
    out = lambda s="": print(s, flush=True)  # noqa: E731
    quiet = lambda s="": None  # noqa: E731
    rec = json.load(open(os.path.join(a.src, "research", "results", "residual_given_line.json"), encoding="utf-8"))
    recs = json.load(open(a.rows, encoding="utf-8"))
    if len(recs) < 1000:
        raise SystemExit("only %d rows in %s - refusing" % (len(recs), a.rows))
    R = {"target": "c-41", "rows_file": os.path.abspath(a.rows)}
    games = len({d["event"] for d in recs})
    pgs = len({(d["gsis"], d["event"]) for d in recs})
    te_recs = [d for d in recs if d["season"] in M.TEST_SEASONS]
    te_games = len({d["event"] for d in te_recs})

    out("== 1. REPRODUCE (target's analyse() + verdict() on %s)" % a.rows)
    out("   counted here: rungs %d (published %d), player-games %d (%d), games %d (%d); test-season rungs %d on %d games"
        % (len(recs), PUB["rungs"], pgs, PUB["player_games"], games, PUB["games"], len(te_recs), te_games))
    tests, _ex = M.analyse(recs, quiet)
    verd = M.verdict(tests)
    key = lambda t: (t["kind"], t["pop"], t["cut"], t["cand"])  # noqa: E731
    mine, theirs = {key(t): t for t in tests}, {key(t): t for t in rec["tests"]}
    if set(mine) != set(theirs) or len(theirs) != len(rec["tests"]):
        raise SystemExit("test keys differ between the re-run and the committed json")
    worst = max(abs(mine[k][f] - theirs[k][f]) for k in mine for f in ("est", "se", "lo", "hi", "p"))
    n_pass = sum(v["passes"] for v in verd.values())
    tt = mine[("coef", "RB", "pooled", "team_total")]
    tb = mine[("brier", "RB", "2024+2025", "team_total")]
    ab = mine[("brier", "RB", "2024+2025", "close+a")]
    R["reproduce"] = {"tests": len(mine), "max_abs_diff_vs_committed_json": worst, "passing": n_pass,
                      "verdict_equal": verd == rec["verdict"], "team_total": tt, "team_total_brier": tb, "close_a": ab,
                      "checks": [L.reproduce("team_total b", tt["est"], PUB["team_total_b"], 4),
                                 L.reproduce("team_total lo", tt["lo"], PUB["team_total_lo"], 4),
                                 L.reproduce("team_total hi", tt["hi"], PUB["team_total_hi"], 4),
                                 L.reproduce("team_total Holm p", tt["p_holm"], PUB["team_total_holm"], 2),
                                 L.reproduce("team_total Brier", tb["est"], PUB["tt_brier"], 6),
                                 L.reproduce("team_total Brier MDE", tb["mde"], PUB["tt_brier_mde"], 6),
                                 L.reproduce("close+a Brier", ab["est"], PUB["a_brier"], 5),
                                 L.reproduce("close+a lo", ab["lo"], PUB["a_lo"], 5),
                                 L.reproduce("close+a hi", ab["hi"], PUB["a_hi"], 5)]}
    out("   %d tests recomputed; largest |difference| from the committed json over est/se/lo/hi/p: %.3g; verdict dict equal: %s"
        % (len(mine), worst, verd == rec["verdict"]))
    out("   candidates passing: %d of %d (published 0 of 6)" % (n_pass, len(verd)))
    for c in R["reproduce"]["checks"]:
        out("   %-22s measured %+.6f published %+.6f -> %s" % (c["name"], c["measured"], c["published"],
                                                              "reproduces" if c["reproduces"] else "DOES NOT REPRODUCE"))
    if a.rerun:
        now = json.load(open(a.rerun, encoding="utf-8"))
        nw = {key(t): t for t in now["tests"]}
        w2 = max(abs(nw[k][f] - theirs[k][f]) for k in theirs for f in ("est", "se", "lo", "hi", "p"))
        R["rerun_today"] = {"counts": now["counts"], "max_abs_diff_vs_committed_json": w2,
                            "verdict_equal": now["verdict"] == rec["verdict"], "counts_equal": now["counts"] == rec["counts"]}
        out("   TODAY'S STORE (target's script re-run, mode=ro): counts equal %s, verdict equal %s, largest |difference| %.3g"
            % (now["counts"] == rec["counts"], now["verdict"] == rec["verdict"], w2))
    else:
        out("   pipeline re-run result NOT supplied (--rerun): step 1 is on saved rows only")

    out("\n== 2. BLOCKS: THROUGH M.coef_test / M.brier_test (weights from M.block_weights)")

    def w_coef(rr):
        rows = M.columns(rr)
        W = M.block_weights(rows["game"], rows["n_games"], np.random.default_rng(M.SEED))
        z = M.standardise(rows["team_total"], *M.moments(rows["team_total"]))
        t = M.coef_test(z, rows["r"], rows["game"], rows["n_games"], W)
        return t["hi"] - t["lo"]

    def w_brier(cols, add_a, slopes):
        def f(rr):
            rows = M.columns(rr)
            te = np.isin(rows["season"], M.TEST_SEASONS)
            tr = M.subset(rows, te)
            W = M.block_weights(tr["game"], tr["n_games"], np.random.default_rng(M.SEED))
            q, _ = M.walk_forward(rows, cols, add_intercept=add_a, slopes=slopes)
            t = M.brier_test(q[te], tr["p"], tr["y"], tr["game"], tr["n_games"], W)
            return t["hi"] - t["lo"]
        return f

    th = {"team_total coefficient": L.duplication_through(w_coef, recs, "event", fn_name="M.coef_test+M.block_weights",
                                                         units=games),
          "close+a Brier": L.duplication_through(w_brier([], True, False), recs, "event",
                                                 fn_name="M.brier_test+M.walk_forward+M.block_weights", units=games),
          "team_total Brier": L.duplication_through(w_brier(["team_total"], False, True), recs, "event",
                                                    fn_name="M.brier_test+M.walk_forward+M.block_weights", units=games)}
    for k, t in th.items():
        out("   %-24s %s" % (k, L.through_line(t)))
    R["through"] = th
    R["through_required"] = L.require_through(list(th), th)
    out("   rows %d vs units %d games: %.1f rungs a game, so the block LABEL matters - alt blocks below"
        % (len(recs), games, len(recs) / games))

    rows = M.columns(recs)
    n = len(recs)
    z = M.standardise(rows["team_total"], *M.moments(rows["team_total"]))
    r = rows["r"]
    te = np.isin(rows["season"], M.TEST_SEASONS)
    qa, _ = M.walk_forward(rows, [], add_intercept=True, slopes=False)
    qt, _ = M.walk_forward(rows, ["team_total"], add_intercept=False, slopes=True)
    te_i = np.flatnonzero(te)
    da = ((qa - rows["y"]) ** 2 - (rows["p"] - rows["y"]) ** 2)[te_i]
    dt = ((qt - rows["y"]) ** 2 - (rows["p"] - rows["y"]) ** 2)[te_i]

    def slope(idx):
        zz, rr = z[idx], r[idx]
        v = zz.var()
        return float(((zz * rr).mean() - zz.mean() * rr.mean()) / v) if v > 0 else 0.0

    lab = {"game": [d["event"] for d in recs], "player": [d["gsis"] for d in recs],
           "season-week": [d["event"][:7] for d in recs], "team-season (home side of id)": [
               "%s|%s" % (d["season"], d["event"].split("_")[-1]) for d in recs],
           "season": [d["season"] for d in recs]}
    R["alt"] = {}
    for name, stat, m, sub in (("team_total coefficient", slope, n, None),
                               ("close+a Brier", lambda i: float(da[i].mean()), len(te_i), te_i),
                               ("team_total Brier", lambda i: float(dt[i].mean()), len(te_i), te_i)):
        labs = {k: (v if sub is None else [v[j] for j in sub]) for k, v in lab.items()}
        alt = L.alt_blocks(stat, m, labs, draws=1000)
        ic = L.iid_contrast(stat, m, labs["game"], draws=1000)
        R["alt"][name] = {"alt": alt, "iid_over_blocked_width": ic["iid_over_blocked_width"]}
        out("   %s  (iid/blocked width x%.3f)" % (name, ic["iid_over_blocked_width"]))
        for k, v in alt.items():
            out("      by %-30s %4d blocks  %s" % (k, v["n_blocks"], L.fmt(v, 5)))

    out("\n== 3. LEAKAGE (function level only; build() NOT re-run under a scramble)")
    g = {"k": 1000.0, "season": 2024, "home": "AAA", "away": "BBB", "total": 44.0, "spread": 3.0}
    hist = {"p": [(900.0, 2024, 50.0, "AAA", "WR"), (950.0, 2024, 70.0, "AAA", "WR"),
                  (1000.0, 2024, 999.0, "AAA", "WR"), (1100.0, 2024, 999.0, "AAA", "WR")]}
    rung = {"line": 55.5, "p_bench": 0.5, "p_all": 0.51}
    pr = M.prior_games(hist, "p", 2024, 1000.0)
    f0 = M.features(rung, g, pr, 55.5, "AAA")
    hist2 = {"p": hist["p"][:2] + [(1000.0, 2024, -5.0, "AAA", "WR"), (1100.0, 2024, 12345.0, "AAA", "WR")]}
    f1 = M.features(rung, g, M.prior_games(hist2, "p", 2024, 1000.0), 55.5, "AAA")
    try:
        M.features(rung, g, hist["p"][:3], 55.5, "AAA")
        planted = False
    except M.LeakError:
        planted = True
    out("   prior_games keeps %d of 4 games (2 are at/after kickoff); scrambling those leaves features equal: %s; "
        "planted same-kickoff prior raises LeakError: %s" % (len(pr), f0 == f1, planted))
    rng = np.random.default_rng(7)
    sc = dict(rows)
    m25 = rows["season"] == 2025
    sc["r"] = np.where(m25, rng.permutation(rows["r"]), rows["r"])
    q1, _ = M.walk_forward(sc, ["team_total"])
    moved24 = float(np.nanmax(np.abs(q1[rows["season"] == 2024] - qt[rows["season"] == 2024])))
    pl = dict(sc)
    pl["season"] = np.where(m25 & (np.arange(n) % 2 == 0), 2023, rows["season"])      # the plant: 2025 rows filed as 2023
    pl0 = dict(rows)
    pl0["season"] = pl["season"]
    qp1, _ = M.walk_forward(pl, ["team_total"])
    qp0, _ = M.walk_forward(pl0, ["team_total"])
    k24 = rows["season"] == 2024
    moved_plant = float(np.nanmax(np.abs(qp1[k24] - qp0[k24])))
    R["leakage"] = {"features_equal_under_scramble": f0 == f1, "leakerror_on_plant": planted,
                    "wf_2024_moved_when_2025_scrambled": moved24, "wf_2024_moved_with_planted_leak": moved_plant}
    out("   walk_forward: scramble 2025 residuals -> largest move of a 2024 forecast %.3g (must be 0); with 2025 rows "
        "planted as 2023 -> %.3g (must be > 0)" % (moved24, moved_plant))

    out("\n== 4. SPECIFICATIONS")
    reg = [t for t in rec["tests"] if t["registered"]]
    kinds = {}
    for t in rec["tests"]:
        kk = (t["kind"], "registered" if t["registered"] else "descriptive")
        kinds[kk] = kinds.get(kk, 0) + 1
    out("   counted in the committed json: %d tests, %d registered (%s); the unit reported %d / %d; 'specifications' key %s"
        % (len(rec["tests"]), len(reg), kinds, PUB["specifications"], PUB["registered"], rec.get("specifications")))
    out("   the json has no registered_intervals.count key (f26lib.registered_count would refuse); k is counted from its tests list")
    R["specs"] = {"tests": len(rec["tests"]), "registered": len(reg), "kinds": {"%s/%s" % k: v for k, v in kinds.items()}}
    for name, t in (("team_total coefficient", tt), ("team_total Brier", tb), ("close+a Brier", ab)):
        mu = L.multiplicity(t["est"], t["se"], (len(reg), len(rec["tests"])), n_blocks=t["games"])
        R["specs"][name] = mu
        out("   %-24s z %+.2f p %.4f  Bonferroni x%d %.3f, x%d %.3f" % (name, mu["z"], mu["p"], len(reg),
            mu["bonferroni"][len(reg)]["p_adj"], len(rec["tests"]), mu["bonferroni"][len(rec["tests"])]["p_adj"]))

    out("\n== 5. MDE")
    R["mde"] = {}
    for name, t, stated in (("team_total coefficient", tt, PUB["mde_registered"]), ("team_total Brier", tb, PUB["tt_brier_mde"]),
                            ("close+a Brier", ab, None)):
        mr, mc = L.mde_ratio(t["est"], t["se"]), L.mde_claim(stated, t["se"])
        R["mde"][name] = {"ratio": mr, "claim": mc}
        out("   %-24s |est|/MDE %.3f (%s); stated MDE %s vs 2.8 x SE here %.6f -> %s"
            % (name, mr["ratio"], mr["reading"], stated, mc["remeasured"],
               "consistent" if mc["consistent"] else ("no stated MDE" if stated is None else "INCONSISTENT")))
    ses = [mine[("coef", "RB", "pooled", c)]["se"] for c in M.CANDIDATES]
    out("   RB pooled coefficient SEs %.4f-%.4f (registered %.4f): single-interval MDE %.4f-%.4f, Holm worst case (4.08 SE) %.4f-%.4f"
        % (min(ses), max(ses), PUB["se_registered"], 2.8 * min(ses), 2.8 * max(ses), 4.08 * min(ses), 4.08 * max(ses)))

    if a.plant_reps:
        out("\n== 6. POWER: a synthetic candidate in the '%s' slot, through M.analyse + M.verdict (%d reps a size)"
            % (SLOT, a.plant_reps))
        out("   x = lam * r_rung + e, e ~ N(0,1) drawn once per PLAYER-GAME; real outcomes, prices, games and the other five")
        out("   candidates untouched, so the Holm family is the real one with 7 of its 42 tests replaced.")
        vr = float(np.var(r))
        pg = {}
        pg_i = np.array([pg.setdefault((d["gsis"], d["event"]), len(pg)) for d in recs])
        R["power"] = []
        out("   %7s %9s %9s | %6s %6s %6s %6s | %s" % ("b tgt", "b pooled", "Brier", "Holm", "sign", "cond2", "PASS", "share of reps"))
        for bs in a.plant_b.split(","):
            b = float(bs)
            lam = math.sqrt(b * b / (vr * vr - vr * b * b)) if b else 0.0
            rng = np.random.default_rng(4100 + int(round(b * 1e5)))
            agg = {"holm": 0, "sign": 0, "c2": 0, "pass": 0, "b": [], "gain": [], "z_gain": []}
            for _ in range(a.plant_reps):
                x = lam * r + rng.standard_normal(len(pg))[pg_i]
                rr = [dict(d, **{SLOT: float(v)}) for d, v in zip(recs, x)]
                ts = M.analyse(rr, quiet)[0]
                v = M.verdict(ts)[SLOT]
                tc = next(t for t in ts if key(t) == ("coef", "RB", "pooled", SLOT))
                tbr = next(t for t in ts if key(t) == ("brier", "RB", "2024+2025", SLOT))
                agg["holm"] += v["coef_excludes_zero_after_holm"]
                agg["sign"] += v["same_sign_all_seasons"]
                agg["c2"] += v["condition_2_brier_beyond_mde"]
                agg["pass"] += v["passes"]
                agg["b"].append(tc["est"])
                agg["gain"].append(-tbr["est"])
                agg["z_gain"].append(-tbr["est"] / tbr["se"] if tbr["se"] else 0.0)
            k = a.plant_reps
            row = {"b_target": b, "lam": lam, "reps": k, "b_pooled_mean": float(np.mean(agg["b"])),
                   "brier_gain_mean": float(np.mean(agg["gain"])), "z_gain_mean": float(np.mean(agg["z_gain"])),
                   "holm": agg["holm"] / k, "same_sign": agg["sign"] / k, "cond2": agg["c2"] / k, "pass": agg["pass"] / k}
            R["power"].append(row)
            out("   %7.4f %+9.4f %9.6f | %6.2f %6.2f %6.2f %6.2f | mean z of the Brier gain %.2f"
                % (b, row["b_pooled_mean"], row["brier_gain_mean"], row["holm"], row["same_sign"], row["cond2"],
                   row["pass"], row["z_gain_mean"]))
    else:
        out("\n== 6. POWER: NOT RUN (--plant-reps 0)")
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
