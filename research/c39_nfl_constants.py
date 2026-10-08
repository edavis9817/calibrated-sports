"""c-39 - c-28's NFL constants, read back for the NFL-beside-college table.

    LOGGER_DB=<market_log.db> python -m research.c39_nfl_constants OUT.json

Runs c-28's own fit (`research.game_forecast.Walk`, the season model's grid and
`best_params`) and writes the 2026 fit - k, home advantage, preseason
regression for the margin-of-victory walk and for plain Elo - with `sigma_m`
and c-28's gap to the nflverse moneyline close (Brier of both, no interval:
the interval is c-28's and is not re-derived here). Nothing is fitted that
c-28 did not fit. market_log.db is opened `mode=ro`; only OUT is written.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import season_model as S                      # noqa: E402
from research import game_forecast as F                 # noqa: E402
from research import ranking_calibration as rc          # noqa: E402

YEAR = F.KALSHI_SEASON


def main():
    out = sys.argv[1]
    con = S.market_log_ro()
    games, _g, versions = S.load(con)
    grid = S.grid()
    walk = F.Walk(games, S.game_losses(games, grid), mov=True)
    walk0 = F.Walk(games, F.nomov_losses(games, grid), mov=False)
    sig = F.margin_sigmas(games, walk, [YEAR])[YEAR]
    m, k, y = [], [], []
    for i, g in enumerate(games):
        if not (F.ML_FROM <= g["season"] <= F.SCORE_TO) or not F.scored(g) \
                or g["home_score"] == g["away_score"]:
            continue
        raw = con.execute("SELECT home_moneyline, away_moneyline FROM nfl_games WHERE game_id = ? "
                          "AND data_version = ?", (g["game_id"], g["data_version"])).fetchone()
        if raw is None or raw[0] is None or raw[1] is None:
            continue
        ih, ia = F.american(raw[0]), F.american(raw[1])
        m.append(walk.p(i))
        k.append(ih / (ih + ia))
        y.append(1.0 if g["home_score"] > g["away_score"] else 0.0)
    con.close()
    if len(y) < 4000:
        raise SystemExit("only %d NFL games carry a moneyline - refusing" % len(y))
    bm, bk = rc.brier(np.array(m), np.array(y)), rc.brier(np.array(k), np.array(y))
    res = {"year": YEAR, "versions": versions, "grid": S.GRID,
           "mov": walk.params(YEAR).as_dict(), "nomov": walk0.params(YEAR).as_dict(), "sigma_m": sig,
           "c28_moneyline_gap": {"n": len(y), "brier_model": bm, "brier_close": bk,
                                 "dBrier": bm - bk, "brier_ratio": bm / bk}}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
