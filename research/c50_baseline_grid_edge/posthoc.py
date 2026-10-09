"""c-50 POST HOC - where the weeks 1-4 gap moved from. NOT pre-registered.

    LOGGER_DB=<market_log.db> python -m research.c50_baseline_grid_edge.posthoc \
        --json-out research/c50_baseline_grid_edge/results/c50_posthoc.json \
        --log-out  research/c50_baseline_grid_edge/results/c50_posthoc.log

Written AFTER the registered run (results/c50_baseline_grid_edge.log) showed the
K-widened plain Elo scoring a HIGHER Brier than the registered one over all
games (+0.00005). The registered run does not say in which stage the baseline
moved, and the weeks 1-4 sentence turns on exactly that. This prints, per stage,
each forecaster's Brier and log loss and the baseline-against-baseline
difference (plain Elo arm P minus plain Elo registered) on the same panel code.
Everything here is descriptive: no verdict word is computed from it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jobs import season_model as S                      # noqa: E402
from research import game_forecast as GF                # noqa: E402
from research.c50_baseline_grid_edge import analyse as A  # noqa: E402
from research.c50_baseline_grid_edge import panel as P  # noqa: E402

SEEDS = tuple(range(1, 51))      # 50 of the registered panel's seeds; post hoc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--log-out", required=True)
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    con = S.market_log_ro()
    games, _grp, versions = S.load(con)
    con.close()
    out(f"POST HOC, not pre-registered. versions {versions}")
    walk = GF.Walk(games, S.game_losses(games, S.grid()), mov=True)
    w0 = {nm: GF.Walk(games, GF.nomov_losses(games, A.points(A.GRIDS[nm])), mov=False)
          for nm in ("registered", "P")}
    pop1 = [i for i, g in enumerate(games)
            if GF.SCORE_FROM <= g["season"] <= GF.SCORE_TO and GF.scored(g)
            and g["home_score"] != g["away_score"]]
    y = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop1])
    f = {"model": np.array([walk.p(i) for i in pop1]),
         "plain_registered": np.array([w0["registered"].p(i) for i in pop1]),
         "plain_P": np.array([w0["P"].p(i) for i in pop1])}
    reg = np.array([games[i]["game_type"] == "REG" for i in pop1])
    wk = np.array([games[i]["week"] for i in pop1])
    masks = {"all": np.ones(len(pop1), bool), "weeks_1_4": reg & (wk <= 4),
             "weeks_5_plus": reg & (wk > 4), "post": ~reg}
    gkey = [games[i]["game_id"] for i in pop1]

    def brier(p, mk):
        return float(((p[mk] - y[mk]) ** 2).mean())

    def logloss(p, mk):
        q = np.clip(p[mk], 1e-12, 1 - 1e-12)
        return float(-(y[mk] * np.log(q) + (1 - y[mk]) * np.log(1 - q)).mean())

    R = {"posthoc": True, "versions": versions, "seeds": [SEEDS[0], SEEDS[-1]], "draws_per_seed": P.DRAWS,
         "stages": {}}
    out("\nstage         n     | Brier model / plain registered / plain P | log loss model / registered / P")
    for nm, mk in masks.items():
        R["stages"][nm] = {"n": int(mk.sum()),
                           "brier": {k: brier(v, mk) for k, v in f.items()},
                           "log_loss": {k: logloss(v, mk) for k, v in f.items()}}
        s = R["stages"][nm]
        out("%-12s %5d | %.5f / %.5f / %.5f | %.5f / %.5f / %.5f"
            % ((nm, s["n"]) + tuple(s["brier"].values()) + tuple(s["log_loss"].values())))
    d = (f["plain_P"] - y) ** 2 - (f["plain_registered"] - y) ** 2
    out(f"\nBrier, plain Elo arm P MINUS plain Elo registered (positive = the K-widened baseline is WORSE); "
        f"seeds {SEEDS[0]}..{SEEDS[-1]} x {P.DRAWS}, game blocks")
    for nm in ("all", "weeks_1_4", "weeks_5_plus"):
        jj = np.where(masks[nm])[0]
        r = P.panel(d[jj], P.blocks_of([gkey[j] for j in jj]), seeds=SEEDS)[0]
        r.pop("per_seed")
        R["stages"][nm]["plain_P_minus_registered"] = r
        out("   %-12s %+.5f pooled [%+.5f, %+.5f] SE %.5f | seeds below %d contains %d above %d | |est|/MDE %.2f"
            % (nm, r["est"], r["pooled"]["lo"], r["pooled"]["hi"], r["pooled"]["se"], r["count"][P.BELOW],
               r["count"][P.CONTAINS], r["count"][P.ABOVE], r["est_over_mde"]))
    gap = np.abs(f["plain_P"] - f["plain_registered"])
    R["abs_forecast_gap"] = {"mean": float(gap.mean()), "p95": float(np.percentile(gap, 95)), "max": float(gap.max())}
    out("\n|plain P - plain registered| per game: mean %.4f, p95 %.4f, max %.4f"
        % (gap.mean(), np.percentile(gap, 95), gap.max()))
    with open(a.json_out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(R, fh, indent=1, default=str)
    with open(a.log_out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
