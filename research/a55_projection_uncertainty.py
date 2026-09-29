"""a-55: why the projection draws ratings, and what the pace line cost.

    python -m research.a55_projection_uncertainty

Re-derives the two findings `jobs/season_projection.py` rests on, from the
store (mode=ro) and nothing else:

  1. With ratings HELD FIXED inside a simulation (the division model's rule),
     the central 80% interval of final wins holds the real final record far
     less often than the share of simulations inside it - overconfident, and
     worst early in the season. Drawing each simulation's ratings once,
     N(rating, sigma^2) with sigma chosen on earlier seasons, closes the gap.
  2. On the same forecasts, the pace line (win rate so far x games on the
     schedule) misses the final record by far more than the model does, most
     of all in the first weeks - the weeks the team page is read in.

Both arms run the same walk-forward (`season_projection.walk_forward`), the
fixed arm with the sigma grid pinned to {0}. Prints; writes nothing.
"""
from __future__ import annotations

import numpy as np

from jobs import season_model as J
from jobs import season_projection as P


def arm(games, groupings, losses, grid):
    saved = P.SIGMA_GRID
    P.SIGMA_GRID = grid
    try:
        rows, sigma, _ = P.walk_forward(J, games, groupings, losses, log=lambda *a: None)
    finally:
        P.SIGMA_GRID = saved
    return rows, sigma


def main():
    con = J.market_log_ro()
    try:
        games, groupings, _ = J.load(con)
    finally:
        con.close()
    losses = J.game_losses(games, J.grid())
    arms = {"fixed ratings (sigma 0)": arm(games, groupings, losses, [0.0]),
            "drawn ratings (sigma fitted)": arm(games, groupings, losses, list(P.SIGMA_GRID))}
    n_expected = None
    for name, (rows, sigma) in arms.items():
        if not rows:
            raise SystemExit("no forecasts - refusing to print an empty table")
        n_expected = n_expected or len(rows)
        assert len(rows) == n_expected, "the two arms scored different forecasts"
        s = P.summarise(rows)
        print("\n== %s: %d forecasts, sigma by season %s" % (
            name, len(rows), sorted({v["sigma"] for v in sigma.values()})))
        for lvl in ("80", "95"):
            c = s["coverage"][lvl]
            print("  central %s%%: realised %.3f, simulated mass %.3f, difference %s -> %s"
                  % (lvl, c["realised"], c["mass"],
                     "[%+.3f, %+.3f]" % tuple(c["difference_interval"]), c["verdict"]))
        print("  rmse final wins: model %.2f  pace %.2f  coin-flip %.2f  (n=%d with pace defined)"
              % (s["rmse"]["model"], s["rmse"]["pace"], s["rmse"]["coinflip"], s["n_forecasts"]))
        for k in (0, 1, 2, 3, 4, 8, 12, 16):
            w = next(x for x in s["by_week"] if x["after_week"] == k)
            print("   after week %2d: model %.2f  pace %s  coin %.2f  cov80 %.3f mass80 %.3f"
                  % (k, w["model"], "  -  " if w["pace"] is None else "%.2f" % w["pace"],
                     w["coinflip"], w["coverage80"], w["mass80"]))
        for b in ("pace", "coinflip"):
            d = s["vs"][b]
            print("  MSE model - %s: %+.2f [%+.2f, %+.2f] over %d seasons -> %s"
                  % (b, d["estimate"], *d["interval"], d["n_blocks"], d["verdict"]))
    return 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    raise SystemExit(main())
