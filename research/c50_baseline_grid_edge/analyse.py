"""c-50 - plain Elo's K grid, widened, and the weeks 1-4 sentence over seeds.

    LOGGER_DB=<market_log.db> python -m research.c50_baseline_grid_edge.analyse \
        --json-out research/c50_baseline_grid_edge/results/c50_baseline_grid_edge.json \
        --log-out  research/c50_baseline_grid_edge/results/c50_baseline_grid_edge.log

PRE-REGISTRATION: docs/C50-baseline-grid-edge-preregistration.md, committed and
pushed at bc7357b BEFORE this script existed. This file implements it; comments
say only where the code carries a rule out.

The walk, the fit rule, the baselines' population and the bootstrap are c-28's
(`models.season.run_elo`, `jobs.season_model.best_params`,
`research.game_forecast`, `research.ranking_calibration.Pop.boot`), imported.
market_log.db is opened mode=ro and only nfl_games / nfl_teams are read.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jobs import season_model as S                      # noqa: E402
from models import season as M                          # noqa: E402
from research import game_forecast as GF                # noqa: E402
from research import ranking_calibration as rc          # noqa: E402
from research.c50_baseline_grid_edge import panel as P  # noqa: E402

# ---- the registered grids
K_WIDE = [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 75.0,
          80.0, 100.0, 120.0]
HFA_WIDE = [0.0, 25.0, 50.0, 75.0, 100.0]
REG_WIDE = [0.0, 0.25, 1.0 / 3.0, 0.5, 0.6, 0.75, 0.9]
GRIDS = {
    "registered": {"k": list(S.GRID["k"]), "hfa": list(S.GRID["hfa"]), "regress": list(S.GRID["regress"])},
    "P": {"k": K_WIDE, "hfa": list(S.GRID["hfa"]), "regress": list(S.GRID["regress"])},
    "S": {"k": K_WIDE, "hfa": HFA_WIDE, "regress": REG_WIDE},
}
INTERIOR_MIN = 20            # of the 25 scored seasons
PUBLISHED = {"all": -0.0027, "weeks_1_4": (-0.0008, -0.0024, 0.0007)}
F26 = {"all_widened": (-0.0027, -0.0036, -0.0019),
       "weeks_1_4_registered": (-0.0008, -0.0024, 0.0009),
       "weeks_1_4_widened": (-0.0017, -0.0031, -0.0004)}
EXPECT_N = {"all": 6743, "weeks_1_4": 1547}


def points(g):
    return [M.EloParams(k, h, r) for k, h, r in itertools.product(g["k"], g["hfa"], g["regress"])]


def fit_table(walk, grid, years, losses):
    """Per season: the fitted point, its fit log loss, its edges, and the K profile."""
    rows = {}
    for y in years:
        p = walk.params(y)
        prof = {}
        for q, (s, ll) in losses.items():
            m = (s >= S.FIT_FROM) & (s < y)
            v = float(ll[m].mean())
            if q.k not in prof or v < prof[q.k]:
                prof[q.k] = v
        ks = grid["k"]
        j = ks.index(p.k)
        nb = [prof[ks[t]] for t in (j - 1, j + 1) if 0 <= t < len(ks)]
        rows[y] = {"k": p.k, "hfa": p.hfa, "regress": p.regress,
                   "fit_log_loss": walk.fits[y]["fit_log_loss"],
                   "edge": {d: P.edges(getattr(p, d), grid[d]) for d in ("k", "hfa", "regress")},
                   "k_strictly_below_neighbours": all(prof[p.k] < v for v in nb),
                   "k_profile": {("%g" % k): prof[k] for k in ks}}
    return rows


def edge_counts(table, years):
    out = {}
    for d in ("k", "hfa", "regress"):
        e = [table[y]["edge"][d] for y in years]
        out[d] = {"min": e.count("min"), "max": e.count("max"), "interior": e.count("")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--log-out", required=True)
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    t0 = time.time()
    R = {"preregistration": "docs/C50-baseline-grid-edge-preregistration.md @ bc7357b",
         "grids": GRIDS, "seeds": [P.SEEDS[0], P.SEEDS[-1]], "draws_per_seed": P.DRAWS,
         "share_threshold": P.SHARE, "store_moved": []}
    con = S.market_log_ro()
    games, _grp, versions = S.load(con)
    con.close()
    R["versions"] = versions
    out(f"games loaded {len(games):,}; versions {versions}")
    for nm, g in GRIDS.items():
        out(f"grid {nm:<10} K {g['k'][0]:g}..{g['k'][-1]:g} ({len(g['k'])} values: "
            f"{', '.join('%g' % k for k in g['k'])}) | hfa {', '.join('%g' % h for h in g['hfa'])} | "
            f"regress {', '.join('%.3g' % r for r in g['regress'])} | {len(points(g))} points")

    # ---- fits. Plain Elo is walked once per point of S; registered and P are subsets of S,
    #      each rebuilt in its own itertools.product order (the tie rule reads grid order).
    years = list(range(GF.SCORE_FROM, GF.SCORE_TO + 1))
    all_plain = GF.nomov_losses(games, points(GRIDS["S"]))
    out(f"plain Elo walked on {len(all_plain)} points, {time.time() - t0:.0f}s")
    mov_losses = S.game_losses(games, S.grid())
    walk = GF.Walk(games, mov_losses, mov=True)
    walks0, tables = {}, {}
    for nm, g in GRIDS.items():
        sub = {p: all_plain[p] for p in points(g)}
        walks0[nm] = GF.Walk(games, sub, mov=False)
        tables[nm] = fit_table(walks0[nm], g, years + [GF.KALSHI_SEASON], sub)
    tables["model_mov"] = fit_table(walk, GRIDS["registered"], years + [GF.KALSHI_SEASON], mov_losses)
    R["fits"] = tables
    R["edge_counts"] = {nm: edge_counts(t, years) for nm, t in tables.items()}

    out("\n######## fitted constants per season (K / hfa / regress; * = that dimension is at a grid endpoint)")
    out("   season | plain REGISTERED   | plain P (K widened) | plain S (all widened) | MODEL (MOV, registered grid)")

    def cell(r):
        return "%5g%s %4g%s %5.3g%s" % (r["k"], "*" if r["edge"]["k"] else " ", r["hfa"],
                                        "*" if r["edge"]["hfa"] else " ", r["regress"],
                                        "*" if r["edge"]["regress"] else " ")
    for y in years + [GF.KALSHI_SEASON]:
        out("   %d   | %s | %s  | %s    | %s%s" % (y, cell(tables["registered"][y]), cell(tables["P"][y]),
                                               cell(tables["S"][y]), cell(tables["model_mov"][y]),
                                               "   (2026: reported, not counted)" if y == GF.KALSHI_SEASON else ""))
    for nm in ("registered", "P", "S", "model_mov"):
        ec = R["edge_counts"][nm]
        out(f"   edges over 25 scored seasons, {nm:<10}: " + " | ".join(
            f"{d} min {ec[d]['min']} max {ec[d]['max']} interior {ec[d]['interior']}" for d in ("k", "hfa", "regress")))
    k_int = {nm: R["edge_counts"][nm]["k"]["interior"] for nm in ("registered", "P", "S")}
    strict = {nm: sum(tables[nm][y]["k_strictly_below_neighbours"] for y in years) for nm in ("P", "S")}
    R["k_interior"] = {"seasons_interior": k_int, "rule_min": INTERIOR_MIN,
                       "P_interior": k_int["P"] >= INTERIOR_MIN, "S_interior": k_int["S"] >= INTERIOR_MIN,
                       "strictly_below_both_neighbours": strict}
    out(f"   INTERIOR RULE (K not a grid endpoint in >= {INTERIOR_MIN} of 25): registered {k_int['registered']}, "
        f"P {k_int['P']} -> {'INTERIOR' if R['k_interior']['P_interior'] else 'NOT INTERIOR'}, "
        f"S {k_int['S']} -> {'INTERIOR' if R['k_interior']['S_interior'] else 'NOT INTERIOR'}; "
        f"profile strictly below both neighbours: P {strict['P']}, S {strict['S']} of 25")
    out("   K profile, plain Elo arm P, fit for 2025 (mean log loss on 2000-2024, best hfa/regress at each K):")
    out("      " + "  ".join(f"{k}:{v:.5f}" for k, v in tables["P"][2025]["k_profile"].items()))
    out("   K profile, plain Elo arm P, fit for 2001 (2000 only):")
    out("      " + "  ".join(f"{k}:{v:.5f}" for k, v in tables["P"][2001]["k_profile"].items()))

    # ---- populations (c-28's)
    pop1 = [i for i, g in enumerate(games)
            if GF.SCORE_FROM <= g["season"] <= GF.SCORE_TO and GF.scored(g)
            and g["home_score"] != g["away_score"]]
    y = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop1])
    m = np.array([walk.p(i) for i in pop1])
    names = ["registered", "P", "S"]
    B = {nm: np.array([walks0[nm].p(i) for i in pop1]) for nm in names}
    # check 2: the model is walked from the MOV losses alone; a second walk must agree exactly.
    m2 = np.array([GF.Walk(games, mov_losses, mov=True).p(i) for i in pop1])
    if np.abs(m2 - m).max() != 0.0:
        raise SystemExit("the model's forecasts are not reproducible across walks - refusing")

    def sq(p):
        return (p - y) ** 2
    D = np.column_stack([sq(m) - sq(B[nm]) for nm in names])
    R["brier"] = {"model": float(sq(m).mean()), **{"plain_" + nm: float(sq(B[nm]).mean()) for nm in names}}
    out(f"\n######## Brier on {len(pop1):,} decisive games {GF.SCORE_FROM}-{GF.SCORE_TO}: model {sq(m).mean():.4f} | "
        + " | ".join(f"plain {nm} {sq(B[nm]).mean():.4f}" for nm in names))
    out("   Brier plain(arm) - plain(registered): " + ", ".join(
        f"{nm} {sq(B[nm]).mean() - sq(B['registered']).mean():+.5f}" for nm in ("P", "S")))

    reg = np.array([games[i]["game_type"] == "REG" for i in pop1])
    wk = np.array([games[i]["week"] for i in pop1])
    masks = {"all": np.ones(len(pop1), bool), "weeks_1_4": reg & (wk <= 4), "weeks_5_plus": reg & (wk > 4)}
    gkey = [games[i]["game_id"] for i in pop1]
    wkey = ["%d-%02d" % (games[i]["season"], games[i]["week"]) for i in pop1]
    skey = [games[i]["season"] for i in pop1]
    R["n"] = {k: int(v.sum()) for k, v in masks.items()}
    for k, exp in EXPECT_N.items():
        if R["n"][k] != exp:
            R["store_moved"].append(f"n {k}: {R['n'][k]} against {exp}")
    out("   n: " + ", ".join(f"{k} {v:,}" for k, v in R["n"].items())
        + f"   (expected all {EXPECT_N['all']:,}, weeks_1_4 {EXPECT_N['weeks_1_4']:,})")

    # ---- check 1 and check 3 on the published seed, through c-28's own function
    def pop_boot(mask, nm):
        jj = np.where(mask)[0]
        rows = [{"game": gkey[j], "stat": "ml", "line": 0.0, "y": float(y[j]), "m": float(m[j]),
                 "k": float(B[nm][j])} for j in jj]
        pop = rc.Pop("check", rows, "m", "k")
        return pop.boot(lambda i, pop=pop: rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i]),
                        draws=P.DRAWS, seed=P.PUBLISHED_SEED)
    c1 = pop_boot(masks["all"], "registered")
    c3 = pop_boot(masks["weeks_1_4"], "registered")
    kmax = R["edge_counts"]["registered"]["k"]["max"]
    R["checks"] = {"c28_all_seed24": c1, "c28_weeks_1_4_seed24": c3, "registered_k_at_max": kmax}
    out(f"\n######## checks (c-28's Pop.boot, seed {P.PUBLISHED_SEED}, {P.DRAWS} draws, registered grid)")
    out(f"   ALL       {rc.fmt(c1)}   published {PUBLISHED['all']:+.4f}")
    out("   weeks 1-4 %s   published %+.4f [%+.4f, %+.4f]" % ((rc.fmt(c3),) + PUBLISHED["weeks_1_4"]))
    out(f"   plain Elo registered K at grid max in {kmax} of 25 (f-26: 25 of 25)")
    if round(c1["est"], 4) != PUBLISHED["all"]:
        R["store_moved"].append(f"ALL dBrier {c1['est']:+.4f} against published {PUBLISHED['all']:+.4f}")
    if (round(c3["est"], 4), round(c3["lo"], 4), round(c3["hi"], 4)) != PUBLISHED["weeks_1_4"]:
        R["store_moved"].append("weeks 1-4 %+.4f [%+.4f, %+.4f] against published %+.4f [%+.4f, %+.4f]"
                                % ((c3["est"], c3["lo"], c3["hi"]) + PUBLISHED["weeks_1_4"]))
    if kmax != 25:
        R["store_moved"].append(f"registered K at max in {kmax} of 25, f-26 measured 25")
    out("   STORE MOVED SINCE c-28: " + ("; ".join(R["store_moved"]) if R["store_moved"] else "no"))

    # ---- the seed panel
    cells = {}
    out(f"\n######## seed panel: seeds {P.SEEDS[0]}..{P.SEEDS[-1]}, {P.DRAWS} draws each, pooled {len(P.SEEDS) * P.DRAWS:,}")

    def show(label, r):
        out("   %-36s est %+.4f | pooled [%+.4f, %+.4f] SE %.5f | seeds below %3d contains %3d above %3d | "
            "hi min/med/max %+.5f/%+.5f/%+.5f | |est|/MDE %.2f | -> %s"
            % (label, r["est"], r["pooled"]["lo"], r["pooled"]["hi"], r["pooled"]["se"],
               r["count"][P.BELOW], r["count"][P.CONTAINS], r["count"][P.ABOVE],
               r["hi_min_med_max"][0], r["hi_min_med_max"][1], r["hi_min_med_max"][2],
               r["est_over_mde"], r["category"]))

    for pk, mask in masks.items():
        jj = np.where(mask)[0]
        res = P.panel(D[jj], P.blocks_of([gkey[j] for j in jj]))
        for nm, r in zip(names, res):
            cells[f"{pk}|{nm}|game"] = r
            show(f"{pk} / {nm} / game blocks", r)
        out(f"      ({time.time() - t0:.0f}s)")
    for pk in ("all", "weeks_1_4"):
        jj = np.where(masks[pk])[0]
        for bn, key in (("week", wkey), ("season", skey)):
            res = P.panel(D[jj], P.blocks_of([key[j] for j in jj]))
            for nm, r in zip(names, res):
                cells[f"{pk}|{nm}|{bn}"] = r
                show(f"{pk} / {nm} / {bn} blocks ({r['blocks']})", r)
    R["cells"] = cells

    # check 3: my loop against c-28's function, seed 24, weeks 1-4, registered
    mine = next(s for s in cells["weeks_1_4|registered|game"]["per_seed"] if s["seed"] == P.PUBLISHED_SEED)
    gap = max(abs(mine["lo"] - c3["lo"]), abs(mine["hi"] - c3["hi"]))
    R["checks"]["loop_vs_pop_boot_max_abs"] = gap
    out(f"\n   check 3: panel loop against rc.Pop.boot on seed {P.PUBLISHED_SEED}, weeks 1-4 registered: max abs {gap:.2e}")
    if gap > 1e-15:
        raise SystemExit("the seed loop does not reproduce rc.Pop.boot - refusing to read the panel")

    # ---- verdicts
    w = P.weeks_verdict(cells["weeks_1_4|P|game"]["category"], cells["weeks_1_4|S|game"]["category"],
                        cells["weeks_1_4|P|week"]["category"], R["k_interior"]["P_interior"])
    w_reg = cells["weeks_1_4|registered|game"]["category"]
    hp = cells["all|P|game"]
    h = P.headline_verdict(hp["category"], hp["pooled"]["sign"])
    R["verdicts"] = {"weeks_1_4_arm_P": w, "weeks_1_4_registered_category": w_reg,
                     "weeks_1_4_arm_S_category": cells["weeks_1_4|S|game"]["category"],
                     "weeks_1_4_arm_P_week_blocks_category": cells["weeks_1_4|P|week"]["category"],
                     "headline_arm_P": h, "headline_arm_S_category": cells["all|S|game"]["category"]}
    c = cells["weeks_1_4|P|game"]
    out("\n######## REGISTERED VERDICTS")
    out(f"   weeks 1-4, registered grid (the published sentence's own grid): {w_reg}")
    out("   WEEKS 1-4 AT ARM P: %s%s" % (w["verdict"], (" ; " + ", ".join(w["qualifiers"])) if w["qualifiers"] else ""))
    out("      %+.4f pooled [%+.4f, %+.4f]; below zero in %d of %d seeds, contains zero in %d (share %.3f); "
        "z %.2f, p %.4f, Bonferroni k=2 p %.4f; |est|/MDE %.2f"
        % (c["est"], c["pooled"]["lo"], c["pooled"]["hi"], c["count"][P.BELOW], c["seeds"],
           c["count"][P.CONTAINS], c["share"][P.CONTAINS], c["z"], c["p_two_sided"],
           min(1.0, 2 * c["p_two_sided"]), c["est_over_mde"]))
    out("      f-26 (one seed, its own bootstrap): %+.4f [%+.4f, %+.4f]" % F26["weeks_1_4_widened"])
    out(f"      arm S: {cells['weeks_1_4|S|game']['category']}; arm P season-week blocks: "
        f"{cells['weeks_1_4|P|week']['category']}; arm P season blocks: {cells['weeks_1_4|P|season']['category']}")
    out(f"   HEADLINE AT ARM P: {h}")
    out("      %+.4f pooled [%+.4f, %+.4f]; below zero in %d of %d seeds; |est|/MDE %.2f;  c-28 published %+.4f; "
        "f-26 %+.4f [%+.4f, %+.4f]"
        % ((hp["est"], hp["pooled"]["lo"], hp["pooled"]["hi"], hp["count"][P.BELOW], hp["seeds"],
            hp["est_over_mde"], PUBLISHED["all"]) + F26["all_widened"]))
    R["seconds"] = time.time() - t0
    out(f"\n{R['seconds']:.0f}s")
    with open(a.json_out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(R, f, indent=1, default=str)
    with open(a.log_out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
