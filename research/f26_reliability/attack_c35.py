"""f-31 target adapter: c-35's "ladder edges", FROM ITS SCRATCH CACHE, through its own functions.

    CLAIM  Kalshi KXNFLREC / KXNFLRSHATT, over side, NFL 2026 weeks 2-4, one instant per game
           (kickoff - 180 min): 725 ladders, 3,987 rungs, 48 games.
           Q1  1 of 48 registered intervals survives BH (receptions-RB, d=+1: implied 0.140,
               realised 0.065, 124 ladders); its two-leg depth join realised +0.40pp [-9.32, +7.97].
           Q2  one shift per ladder removes 0.958 of the squared model-vs-market disagreement;
               A_R - 0.5 +0.0182, A_L - 0.5 +0.0155 (both span zero); dA -0.0027 [-0.0194, +0.0143]
               at MDE 0.0240, "a comparison of two nulls".
           Q3  (18 games, 16 of them week 3) cost hump 1.44c / ~2.5c / 1.80c at 100 contracts.

RUN: research.ladder_edges.q1 / q2 / q3 from the target's worktree on the unit's own extract
cache (its --cache scratch JSON; no result file was committed). The blocks step hands the rows
to the target's own `ladder_edges.Blocks.boot`. A measured null for the 0.958 (three of them).
NOT RUN: the extract (the store is not opened, the model is not refit), so there is NO leakage
step and "the rows are what the store says" is not checked. Reading-only notes are labelled.

    python research/f26_reliability/attack_c35.py --src <c-35 worktree> --cache <rows.json> \
        --recorded <result_final.json> --first <result.json> --out <c35.json>
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = {"ladders": 725, "rungs": 3987, "games": 48, "q1_tests": 48, "q1_survivors": 1,
       "cell": (-0.0751, -0.1144, -0.0294), "cell_implied": 0.1396, "cell_realised": 0.0645, "cell_n": 124,
       "depth": (0.0040, -0.0932, 0.0797), "depth_mde_log": 0.1262,
       "level": (0.958, 0.951, 0.964), "dA": (-0.0027, -0.0194, 0.0143), "dA_mde": 0.0240,
       "A_R": (0.0182, -0.0352, 0.0733), "A_L": (0.0155, -0.0343, 0.0666), "A_mde_stated": 0.075,
       "hump": {"0.00-0.10": 1.44, "0.35-0.50": 2.43, "0.50-0.65": 2.51, "0.90-1.00": 1.80}}
REGISTERED = {"q1": 55, "q2": 21, "q3": 4}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--recorded", required=True)
    ap.add_argument("--first")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from research import ladder_edges as le
    if not os.path.abspath(le.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.ladder_edges resolved outside --src: %s" % le.__file__)
    out = lambda s="": print(s, flush=True)  # noqa: E731
    quiet = lambda *_a: None  # noqa: E731
    data = json.load(open(a.cache, encoding="utf-8"))
    rec = json.load(open(a.recorded, encoding="utf-8"))
    rows = data["rows"]
    if not rows:
        raise SystemExit("empty cache - refusing")
    R = {"target": "c-35", "pipeline_rerun": "analysis only (q1/q2/q3 on the unit's cache); extract NOT re-run",
         "leakage_step": "NOT RUN"}
    ladders = le.build_ladders(rows)
    games = sorted({r["game"] for r in rows})
    out("cache: %d rungs, %d ladders, %d distinct games (counted here from game ids), by week %s; census %s"
        % (len(rows), len(ladders), len(games), dict(sorted(Counter(l["week"] for l in ladders).items())),
           data["census"]))

    out("\n== 1. REPRODUCE (target's q1/q2/q3 on its own cache; same seed, so bounds must match exactly)")
    r1 = le.q1(ladders, data["ref"], quiet)
    r2 = le.q2(ladders, quiet)
    r3 = le.q3(ladders, quiet)
    cellr = r1["A"]["receptions-RB"]["d=+1"]
    dj = r1["survivor_depth_join"].get("A|receptions-RB|d=+1", {})
    o = r2["ordering"]["all"]
    tri = lambda r: (r["est"], r["lo"], r["hi"])  # noqa: E731
    checks = [("rungs", len(rows), PUB["rungs"], 0), ("ladders", len(ladders), PUB["ladders"], 0),
              ("games", len(games), PUB["games"], 0), ("Q1 intervals computed", r1["n_tests"], PUB["q1_tests"], 0),
              ("Q1 BH survivors", len(r1["bh_survivors"]), PUB["q1_survivors"], 0),
              ("RB d=+1 implied", cellr["implied"], PUB["cell_implied"], 4),
              ("RB d=+1 realised", cellr["realised"], PUB["cell_realised"], 4),
              ("RB d=+1 n", cellr["n"], PUB["cell_n"], 0)]
    for nm, got, pub in (("RB d=+1 diff", tri(cellr), PUB["cell"]), ("depth join net", tri(dj["realised_net"]), PUB["depth"]),
                         ("dA", tri(o["dA"]), PUB["dA"]), ("A_R-0.5", tri(o["A_R"]), PUB["A_R"]),
                         ("A_L-0.5", tri(o["A_L"]), PUB["A_L"])):
        checks += [("%s %s" % (nm, w), g, p, 4) for w, g, p in zip(("est", "lo", "hi"), got, pub)]
    checks += [("level share %s" % w, g, p, 3) for w, g, p in zip(("est", "lo", "hi"), tri(r2["level_share"]), PUB["level"])]
    checks += [("Q3 YES@100 cost c %s" % b, r3["100"]["yes"]["buckets"][b]["cost_c_mean"], v, 2) for b, v in PUB["hump"].items()]
    R["reproduce"] = [L.reproduce(*c) for c in checks]
    for r in R["reproduce"]:
        out("   %-28s measured %+.5f  published %+.4f -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    out("   survivors: %s" % [s["name"] for s in r1["bh_survivors"]])
    q3g = Counter()
    for l in ladders:
        if sum(1 for r in l["rungs"] if r["depth"].get("buy_yes", {}).get("100") is not None) >= 3:
            q3g[(l["game"], l["week"])] += 1
    R["q3_games_by_week"] = dict(Counter(w for (_g, w) in q3g))
    out("   Q3 games with >=3 depth rungs, by week (counted here): %s; Q3 games in result %d"
        % (R["q3_games_by_week"], r3["100"]["yes"]["games"]))
    same = json.dumps(r1["A"], sort_keys=True, default=str) == json.dumps(rec["q1"]["A"], sort_keys=True, default=str)
    out("   recomputed Q1-A block identical to the unit's recorded result file: %s" % same)
    R["recorded_identical_q1A"] = same

    # ---- the rows behind each attacked claim
    Lq = [l for l in ladders if len(l["rungs"]) >= le.MIN_RUNGS]
    cell_rows = []
    for l in Lq:
        if "receptions-RB" not in le.groups_of(l["stat"], l["pos"]) or l["central"] is None:
            continue
        base = math.ceil(l["lines"][l["central"]])
        for xx, q in le.unit_cells(l["lines"], l["surv"]).items():
            if xx - base == 1:
                cell_rows.append({"game": l["game"], "week": l["week"], "gsis": l["gsis"],
                                  "hit": float(int(round(l["x"])) == xx), "q": q})
    djr, _cen = le.cell_depth_join(Lq, "receptions-RB", "d=+1", -1.0)
    gw = {l["game"]: l["week"] for l in ladders}
    depth_rows = [{"game": g, "week": gw[g], "net": net} for g, net, _c, _s, _q in djr]
    rung_rows, lad_rows = [], []
    for l in ladders:
        rs = [r for r in l["rungs"] if r.get("m") is not None]
        if len(rs) < le.MIN_RUNGS:
            continue
        k = np.array([r["k"] for r in rs])
        m = np.array([r["m"] for r in rs])
        c, ss1 = le.fit_shift(k, m)
        ss0 = float(((np.clip(m, le.CLIP, 1 - le.CLIP) - np.clip(k, le.CLIP, 1 - le.CLIP)) ** 2).sum())
        lad_rows.append({"game": l["game"], "week": l["week"], "gsis": l["gsis"], "stat": l["stat"], "ss0": ss0,
                         "ss1": ss1, "k": k, "m": m, "lines": tuple(l["lines"][i] for i, r in enumerate(l["rungs"])
                                                                   if r.get("m") is not None)})
        for r in rs:
            rung_rows.append({"game": l["game"], "week": l["week"], "gsis": l["gsis"],
                              "pg": "%s|%s|%s" % (l["game"], l["gsis"], l["stat"]), "y": r["y"], "c": c,
                              "z": le.probit(r["m"]) - le.probit(r["k"]),
                              "s": (0 if l["stat"] == "receptions" else 1) * 10 + min(int(10 * r["k"]), 9)})

    def f_cell(rr):
        return le.mean_diff([r["hit"] for r in rr], [r["q"] for r in rr])

    def f_depth(rr):
        return le.mean_diff([r["net"] for r in rr], np.zeros(len(rr)))

    def f_dA(rr):
        y, c, z = (np.array([r[kk] for r in rr]) for kk in ("y", "c", "z"))
        s = np.array([r["s"] for r in rr])

        def f(i):
            p, q = le.wauc(c[i], y[i], s[i]), le.wauc(z[i], y[i], s[i])
            return None if p is None or q is None else p - q
        return f

    def f_level(rr):
        s0, s1 = np.array([r["ss0"] for r in rr]), np.array([r["ss1"] for r in rr])
        return lambda i: float(1 - s1[i].sum() / s0[i].sum())
    CL = {"Q1 cell RB d=+1": (cell_rows, f_cell), "Q1 depth join": (depth_rows, f_depth),
          "Q2 dA": (rung_rows, f_dA), "Q2 level share": (lad_rows, f_level)}

    out("\n== 2. BLOCKS: THROUGH ladder_edges.Blocks.boot (the target's function), then coarser/other blocks")
    R["through"], R["alt_blocks"], R["iid_contrast"], R["se_reseeded"] = {}, {}, {}, {}
    for nm, (rr, mk) in CL.items():
        units = len({r["game"] for r in rr})

        def width(x, mk=mk):
            q = le.Blocks([r["game"] for r in x]).boot(mk(x), draws=400)
            return q["hi"] - q["lo"]
        t = L.duplication_through(width, rr, "game", fn_name="ladder_edges.Blocks.boot", units=units)
        R["through"][nm] = t
        out("   %-18s %s" % (nm, L.through_line(t)))
        f, n = mk(rr), len(rr)
        est = f(np.arange(n))
        ref = le.Blocks([r["game"] for r in rr]).boot(f, seed=3131)      # the target's function, another seed
        R["se_reseeded"][nm] = ref
        labelings = {"game": [r["game"] for r in rr], "week": [r["week"] for r in rr]}
        if "gsis" in rr[0]:
            labelings["player"] = [r["gsis"] for r in rr]
        if "pg" in rr[0]:
            labelings["player-game"] = [r["pg"] for r in rr]
        d = 1000 if nm == "Q2 dA" else L.DRAWS
        R["iid_contrast"][nm] = L.iid_contrast(f, n, labelings["game"], draws=d, seed=11)
        out("   %-18s est %+.4f; %d rows in %d games; an unblocked interval would be x%.2f the width"
            % (nm, est, n, units, R["iid_contrast"][nm]["iid_over_blocked_width"]))
        R["alt_blocks"][nm] = L.alt_blocks(f, n, labelings, draws=d, seed=12)
        for bn, r in R["alt_blocks"][nm].items():
            out("   %-18s %-12s (%3d blocks) %s" % (nm, bn, r["n_blocks"], L.fmt(r)))
    R["through_verdicts"] = L.require_through(list(CL), R["through"])

    out("\n== 3. LEAKAGE: NOT RUN (the extract and the model refit were not re-run). Descriptive only:")
    pos = defaultdict(set)
    for r in rows:
        pos[r["gsis"]].add(r["pos"])
    R["position_labels"] = {"players": len(pos), "players_with_more_than_one_pos_across_weeks": sum(len(v) > 1 for v in pos.values()),
                            "rungs_pos_none": sum(r["pos"] is None for r in rows)}
    out("   %s" % R["position_labels"])

    out("\n== 4. SPECIFICATIONS: registered in the prereg vs computed vs read")
    t1 = [r for g in r1["A"].values() for r in g.values()] + [v for g in r1["B"].values() for v in g.values()]
    t2 = [v for sc in r2["ordering"].values() for kk, v in sc.items()
          if kk in ("dA", "A_R", "A_L", "dA_LOO", "spearman_c", "spearman_zc", "spearman_diff")]
    t3 = [r3[s][sd]["crossfit"] for s in ("100", "500") for sd in ("yes", "no") if r3[s][sd].get("read")]
    p1, p2, p3 = [le.pval(r) for r in t1], [le.pval(r) for r in t2], [le.pval(r) for r in t3]
    pmin = min(p1)
    R["spec"] = {"registered": REGISTERED, "computed": {"q1": len(t1), "q2": len(t2), "q3": len(t3)},
                 "q1_lt5_games": sum(r["games"] < le.MIN_GAMES for r in t1),
                 "q1_exclude_zero_uncorrected": sum(1 for r in t1 if r["lo"] is not None and (r["lo"] > 0 or r["hi"] < 0)),
                 "q2_exclude_zero_uncorrected": sum(1 for r in t2 if r["lo"] is not None and (r["lo"] > 0 or r["hi"] < 0)),
                 "q3_zero_variance": sum(1 for r in t3 if not r["se"]), "min_p_q1": pmin}
    fams = {"its own 48": p1, "the 55 it registered (7 missing at p=1)": p1 + [1.0] * (REGISTERED["q1"] - len(t1)),
            "all 73 computed, one family": p1 + p2 + p3,
            "all 80 registered, one family": p1 + p2 + p3 + [1.0] * (sum(REGISTERED.values()) - len(p1 + p2 + p3))}
    R["spec"]["bh"] = {}
    for nm, ps in fams.items():
        keep = le.bh(ps)
        R["spec"]["bh"][nm] = {"k": len(ps), "bh_survivors": len(keep), "bonferroni_p": min(1.0, pmin * len(ps))}
        out("   BH q=0.10 over %-42s k=%2d: %d survivor(s); Bonferroni p of the smallest %.4f"
            % (nm, len(ps), len(keep), min(1.0, pmin * len(ps))))
    out("   %s" % {k: v for k, v in R["spec"].items() if k != "bh"})
    if a.first:
        fr = json.load(open(a.first, encoding="utf-8"))
        mv = sum(1 for g in rec["q1"]["A"] for c in rec["q1"]["A"][g]
                 if json.dumps(rec["q1"]["A"][g][c], sort_keys=True) != json.dumps(fr["q1"]["A"].get(g, {}).get(c), sort_keys=True))
        mv += sum(1 for g in rec["q1"]["B"] if json.dumps(rec["q1"]["B"][g], sort_keys=True) != json.dumps(fr["q1"]["B"].get(g), sort_keys=True))
        mv2 = int(json.dumps(rec["q2"]["ordering"], sort_keys=True) != json.dumps(fr["q2"]["ordering"], sort_keys=True))
        gap = lambda res, g: max(abs(e["implied"] - e["ref_2023_25"]) for e in res["q1"]["C"][g]["cells"].values())  # noqa: E731
        R["first_run"] = {"q1_registered_intervals_moved": mv, "q2_ordering_block_moved": mv2,
                          "Q1C_max_gap_receptions_all_first": gap(fr, "receptions-all"),
                          "Q1C_max_gap_receptions_all_final": gap(rec, "receptions-all"),
                          "first_survivors": [s["name"] for s in fr["q1"]["bh_survivors"]]}
        out("   first run (before addendum 1) vs final: %s" % R["first_run"])
    big = sorted(((abs(e["implied"] - e["ref_2023_25"]), e["n"], g, d) for g in ("receptions-WR", "receptions-TE", "receptions-RB")
                  for d, e in r1["C"][g]["cells"].items()), reverse=True)[:6]
    R["Q1C_largest_position_gaps"] = big
    out("   Q1-C largest |Kalshi implied - 2023-25| in a position cell (gap, n ladders, group, cell): %s"
        % [(round(g_, 4), n_, grp, d) for g_, n_, grp, d in big])

    out("\n== 5. MDE (|est| / 2.8 SE; and the unit's stated MDE against 2.8 x an SE re-drawn under another seed)")
    R["mde"] = {}
    for nm, stated in (("Q2 dA", PUB["dA_mde"]), ("Q1 depth join", PUB["depth_mde_log"]), ("Q1 cell RB d=+1", None),
                       ("Q2 level share", None)):
        rs = R["se_reseeded"][nm]
        R["mde"][nm] = {"ratio": L.mde_ratio(rs["est"], rs["se"]), "claim": L.mde_claim(stated, rs["se"]) if stated else None}
        out("   %-18s est %+.4f SE %.5f  |est|/MDE %.2f (%s)%s" % (
            nm, rs["est"], rs["se"], R["mde"][nm]["ratio"]["ratio"], R["mde"][nm]["ratio"]["reading"],
            "" if not stated else "  stated MDE %.4f vs re-measured %.4f -> %s" % (
                stated, 2.8 * rs["se"], "consistent" if R["mde"][nm]["claim"]["consistent"] else "INCONSISTENT")))
    for nm in ("A_R", "A_L"):
        r = o[nm]
        cl = L.mde_claim(PUB["A_mde_stated"], r["se"])
        R["mde"][nm] = {"ratio": L.mde_ratio(r["est"], r["se"]), "claim": cl}
        out("   %-18s est %+.4f SE %.5f (target's own draw)  |est|/MDE %.2f (%s)  stated 'about 0.075' vs %.4f -> %s"
            % (nm + " - 0.5", r["est"], r["se"], R["mde"][nm]["ratio"]["ratio"], R["mde"][nm]["ratio"]["reading"],
               2.8 * r["se"], "consistent" if cl["consistent"] else "INCONSISTENT"))
    net_gap = dj["gap_pp"] / 100 - dj["cost_c"] / 100
    R["depth_join_power"] = {"gap_minus_cost": net_gap, "mde": 2.8 * dj["realised_net"]["se"]}
    out("   depth join: the mid gap less the cost is %+.4f; its MDE is %.4f - the executable test could not see "
        "the edge the cell implies (ratio %.2f)" % (net_gap, 2.8 * dj["realised_net"]["se"], net_gap / (2.8 * dj["realised_net"]["se"])))

    out("\n== 5b. A MEASURED NULL for the 0.958 level share (the unit's benchmark was arithmetic)")
    rng = np.random.default_rng(31)
    s0 = sum(r["ss0"] for r in lad_rows)
    rms = math.sqrt(s0 / sum(len(r["k"]) for r in lad_rows))
    gaps = np.concatenate([np.clip(r["m"], le.CLIP, 1 - le.CLIP) - np.clip(r["k"], le.CLIP, 1 - le.CLIP) for r in lad_rows])

    def share(pairs):
        a0 = a1 = 0.0
        for k, m in pairs:
            m = np.clip(m, le.CLIP, 1 - le.CLIP)
            a1 += le.fit_shift(k, m)[1]
            a0 += float(((m - np.clip(k, le.CLIP, 1 - le.CLIP)) ** 2).sum())
        return 1 - a1 / a0
    obs = share([(r["k"], r["m"]) for r in lad_rows])
    nulls = {"iid": [], "perm": [], "swap": []}
    by = defaultdict(list)
    for i, r in enumerate(lad_rows):
        by[(r["stat"], r["lines"])].append(i)
    swappable = sum(len(v) for v in by.values() if len(v) > 1)
    for _ in range(20):
        nulls["iid"].append(share([(r["k"], r["k"] + rng.normal(0, rms, len(r["k"]))) for r in lad_rows]))
        pg, j, pairs = rng.permutation(gaps), 0, []
        for r in lad_rows:
            pairs.append((r["k"], np.clip(r["k"], le.CLIP, 1 - le.CLIP) + pg[j:j + len(r["k"])]))
            j += len(r["k"])
        nulls["perm"].append(share(pairs))
        pairs = []
        for idx in by.values():
            if len(idx) < 2:
                continue
            sh = rng.permutation(idx)
            pairs += [(lad_rows[i]["k"], lad_rows[j_]["m"]) for i, j_ in zip(idx, sh) if i != j_]
        nulls["swap"].append(share(pairs))
    R["level_share_null"] = {"observed": obs, "rms_gap": rms, "mean_rungs": float(np.mean([len(r["k"]) for r in lad_rows])),
                             "ladders_swappable": swappable, "ladders": len(lad_rows),
                             **{k: {"mean": float(np.mean(v)), "min": float(min(v)), "max": float(max(v))} for k, v in nulls.items()}}
    out("   observed %.3f; rms gap %.4f; mean rungs a ladder %.2f (arithmetic benchmark 1/n = %.3f)"
        % (obs, rms, R["level_share_null"]["mean_rungs"], 1 / R["level_share_null"]["mean_rungs"]))
    out("   null A, iid normal noise of the same rms on each rung:           %.3f [%.3f, %.3f] over 20 draws"
        % tuple(R["level_share_null"]["iid"].values()))
    out("   null B, the real gaps permuted across all rungs:                 %.3f [%.3f, %.3f]"
        % tuple(R["level_share_null"]["perm"].values()))
    out("   null C, ANOTHER player's model ladder on the same stat and lines: %.3f [%.3f, %.3f]  (%d of %d ladders have a twin)"
        % (*R["level_share_null"]["swap"].values(), swappable, len(lad_rows)))
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(R, fo, indent=1, default=str)
    out("\nwrote %s" % a.out)


if __name__ == "__main__":
    main()
