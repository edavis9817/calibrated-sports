"""f-29 target adapter: c-37's receiving-yards result, FROM ITS ROWS.

    CLAIM  on 18,666 bench-book rungs (814 games, 2023-25) the hurdle-gamma model
           loses to a constant 0.5: Brier Y - N-half +0.0166 [+0.0136, +0.0196],
           and beats the prior-season hit rate, -0.0133 [-0.0169, -0.0099].

READ-ONLY, and ARITHMETIC ONLY: the pipeline is NOT re-run and there is NO
leakage step. The rows are c-37's own per-rung predictions (its --out-dir
scratch file, never committed), so step 1 here is "do the rows give the
published figures", which also dates the file to the committed run.

    python research/f26_reliability/attack_c37.py --src D:/temp/f29/c-37src \
        --rows D:/temp/c37/predictions.csv --out D:/temp/f29/c37.json

The blocks step hands the rows to the target's own `rc.Pop.boot`, the function
behind every Part-2 interval (`research.yards_markets._job`, kind 'diff').
`units` is counted here from the game ids, and the alternative blocks include
the PLAYER: a model that is low on the same receiver all season is one error
seen sixteen times, and a game block does not see that.
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISHED = {"half": (0.0166, 0.0136, 0.0196, 0.0043), "prior": (-0.0133, -0.0169, -0.0099, None),
             "p_bench": (0.0168, None, None, None)}
PUBLISHED_N = {"rungs": 18666, "games": 814}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from research import ranking_calibration as rc
    if not os.path.abspath(rc.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.ranking_calibration resolved outside --src: %s" % rc.__file__)
    recorded = json.load(open(os.path.join(a.src, "research", "results", "yards_markets.json"), encoding="utf-8"))
    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-37", "rows_file": a.rows, "pipeline_rerun": False, "leakage_step": "NOT RUN"}

    raw = list(csv.DictReader(open(a.rows, encoding="utf-8")))
    rows = [r for r in raw if r["p_bench"] not in ("", "None")]
    if not rows:
        raise SystemExit("0 bench rungs in %s - refusing" % a.rows)
    f = lambda k: np.array([float(r[k]) for r in rows])  # noqa: E731
    y, Y = f("y"), f("Y")
    comp = {"half": f("half"), "prior": f("prior"), "p_bench": f("p_bench")}
    game = [r["game"] for r in rows]
    n, units = len(rows), len(set(game))
    sq = lambda p: (p - y) ** 2  # noqa: E731
    D = {b: sq(Y) - sq(c) for b, c in comp.items()}
    out("%d rows in the file, %d with a bench price; %d distinct games, %d distinct players, %d player-games"
        % (len(raw), n, units, len({r["gsis"] for r in rows}), len({(r["game"], r["gsis"]) for r in rows})))
    out("N-half column: min %.3f max %.3f (a constant 0.5 must be exactly that)" % (comp["half"].min(), comp["half"].max()))

    out("\n== 1. REPRODUCE from the rows (pipeline NOT re-run)")
    R["reproduce"] = [L.reproduce("bench rungs", n, PUBLISHED_N["rungs"], 0),
                      L.reproduce("games", units, PUBLISHED_N["games"], 0)]
    for b, d in D.items():
        R["reproduce"].append(L.reproduce("Brier Y - %s" % b, d.mean(), PUBLISHED[b][0], 4))
    for r in R["reproduce"]:
        out("   %-22s measured %+.6f  published %+.4f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    # the registered interval, through the target's own function at its own draws and seed
    R["registered_interval_redrawn"] = {}
    for b in ("half", "prior"):
        rws = [{"game": g, "stat": "receiving_yards", "line": float(r["line"]), "y": float(y[j]), "m": float(Y[j]),
                "k": float(comp[b][j])} for j, (g, r) in enumerate(zip(game, rows))]
        pp = rc.Pop(b, rws, "m", "k")
        r_ = pp.boot(lambda i, pp=pp: rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i]))
        pub = PUBLISHED[b]
        cons = [L.same_within_mc(r_["lo"], pub[1], r_["se"]), L.same_within_mc(r_["hi"], pub[2], r_["se"])]
        R["registered_interval_redrawn"][b] = dict(r_, bounds_consistent=[c["consistent"] for c in cons])
        out("   rc.Pop.boot (seed %d, %d draws) Y - %-6s %+.4f [%+.4f, %+.4f] SE %.5f   published [%+.4f, %+.4f] -> %s"
            % (rc.SEED, rc.BOOT, b, r_["est"], r_["lo"], r_["hi"], r_["se"], pub[1], pub[2],
               "bounds match" if all(c["consistent"] for c in cons) else "BOUNDS DIFFER"))
    seasons = sorted({r["season"] for r in rows})
    R["per_season"] = {}
    for s in seasons:
        jj = np.array([j for j, r in enumerate(rows) if r["season"] == s])
        R["per_season"][s] = {b: float(D[b][jj].mean()) for b in D}
        out("   %s n %5d: Y - half %+.4f   Y - prior %+.4f   Y - p_bench %+.4f"
            % (s, len(jj), D["half"][jj].mean(), D["prior"][jj].mean(), D["p_bench"][jj].mean()))

    out("\n== 2. BLOCKS: THROUGH rc.Pop.boot (the target's function), then coarser blocks")
    R["through"] = {}
    for b in ("half", "prior"):
        rws = [{"game": g, "stat": "receiving_yards", "line": float(r["line"]), "y": float(y[j]), "m": float(Y[j]),
                "k": float(comp[b][j])} for j, (g, r) in enumerate(zip(game, rows))]

        def w_pop(rr):
            pp = rc.Pop("dup", rr, "m", "k")
            q = pp.boot(lambda i: rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i]), draws=400)
            return q["hi"] - q["lo"]
        t = L.duplication_through(w_pop, rws, "game", fn_name="rc.Pop.boot", units=units)
        R["through"][b] = t
        out("   %-6s %s" % (b, L.through_line(t)))
    R["through_verdicts"] = L.require_through(["half", "prior"], R["through"])
    labelings = {"game": game, "season-week": ["_".join(g.split("_")[:2]) for g in game],
                 "player": [r["gsis"] for r in rows], "season": [r["season"] for r in rows]}
    R["alt_blocks"], R["iid_contrast"] = {}, {}
    for b in ("half", "prior", "p_bench"):
        stat = lambda idx, d=D[b]: float(d[idx].mean())  # noqa: E731
        R["iid_contrast"][b] = L.iid_contrast(stat, n, game, seed=11)
        out("   %-7s descriptive: %d rows in %d games; an unblocked interval would be x%.2f the width"
            % (b, n, units, R["iid_contrast"][b]["iid_over_blocked_width"]))
        R["alt_blocks"][b] = L.alt_blocks(stat, n, labelings, seed=12)
        for nm, r in R["alt_blocks"][b].items():
            out("   %-7s %-12s (%4d blocks) %s" % (b, nm, r["n_blocks"], L.fmt(r)))

    out("\n== 3. LEAKAGE: NOT RUN. The pipeline was not re-run; nothing here says the forecasts are as-of.")
    out("   (Reasoning, not a check: a leak would flatter the model, and the claim is that the model LOSES.)")

    out("\n== 4-5. SPECIFICATIONS AND MDE")
    tests = recorded.get("tests") or []
    k_file = len(tests)
    has_reg = isinstance((recorded.get("registered_intervals") or {}).get("count"), int)
    R["spec_count"] = {"registered_intervals_key_present": has_reg, "tests_in_result_file": k_file}
    out("   the result file has %s registered_intervals.count; it lists %d tests, and that count is used as k "
        "(the attacker's count of the unit's own list, not the unit's statement)" % ("a" if has_reg else "NO", k_file))
    if k_file < 1:
        raise SystemExit("no tests in the recorded result - refusing to correct over a made-up k")
    R["multiplicity"] = {}
    for b in ("half", "prior", "p_bench"):
        stat = lambda idx, d=D[b]: float(d[idx].mean())  # noqa: E731
        res = {}
        for nm in ("game", "player", "season-week"):
            blocks = L.blocks_of(labelings[nm])
            se = float(L.block_boot(stat, blocks, seed=13).std())
            mu = L.multiplicity(float(D[b].mean()), se, (k_file, 1000), n_blocks=len(blocks))
            res[nm] = dict(mu, se=se, mde=L.mde_ratio(float(D[b].mean()), se))
            out("   %-7s %-11s blocks: SE %.5f z %+.1f  Bonferroni survives k=%d %s, k=1000 %s  |est|/MDE %.2f (%s)"
                % (b, nm, se, mu["z"], k_file, mu["bonferroni"][k_file]["survives_0.05"],
                   mu["bonferroni"][1000]["survives_0.05"], res[nm]["mde"]["ratio"], res[nm]["mde"]["reading"]))
        R["multiplicity"][b] = res
    cl = L.mde_claim(PUBLISHED["half"][3], R["multiplicity"]["half"]["game"]["se"])
    R["mde_claim_half"] = cl
    out("   stated MDE %.4f against 2.8 x the game-block SE re-measured here %.4f -> %s"
        % (cl["stated"], cl["remeasured"], "consistent" if cl["consistent"] else "INCONSISTENT"))

    out("\n== 4b. THE SPECIFICATION NOBODY COUNTED: is 'loses to 0.5' a property of the model or of its level?")
    lvl = {"mean model P(over)": float(Y.mean()), "realized over rate": float(y.mean()),
           "mean p_bench": float(comp["p_bench"].mean())}
    out("   " + ", ".join("%s %.4f" % kv for kv in lvl.items()))
    shift = float(y.mean() - Y.mean())
    for nm, alt in (("model + its own mean gap (in-sample, the most favourable shift)", np.clip(Y + shift, 0.0, 1.0)),
                    ("model shrunk halfway to 0.5", 0.5 + 0.5 * (Y - 0.5))):
        d = sq(alt) - sq(comp["half"])
        stat = lambda idx, d=d: float(d[idx].mean())  # noqa: E731
        r = L.summ(stat(np.arange(n)), L.block_boot(stat, L.blocks_of(game), seed=15))
        R.setdefault("level", {})[nm] = r
        out("   %-62s Brier - N-half %s" % (nm, L.fmt(r)))
    R["level_figures"] = lvl
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(R, fo, indent=1, default=str)
    out("\nwrote %s" % a.out)


if __name__ == "__main__":
    main()
