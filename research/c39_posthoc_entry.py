"""c-39, POST HOC - the one open grid edge, extended.

    python -m research.c39_posthoc_entry research/results/cfb_game_forecast.json OUT.json

NOT registered. The registered run (research/cfb_game_forecast.py) fitted
`entry` at -400, the last value on its grid, in 14 of 22 seasons. This holds
the three constants every season agreed on (a none, cap none, conf_w 1.0),
re-fits k / hfa / regress around the registered values with `entry` allowed to
-800, and scores the extended walk against the registered one on the same
games. It can show whether the edge cost anything; it replaces no registered
figure. cfb.db is opened mode=ro; only OUT is written.
"""
import itertools
import json
import sys

from models import cfb_game as C
from research import cfb_game_forecast as R
from research import game_forecast as F

GRID = {"k": [30.0, 40.0, 50.0], "hfa": [40.0, 55.0, 70.0, 85.0], "regress": [0.3, 0.4, 0.5],
        "entry": [-300.0, -400.0, -500.0, -600.0, -800.0]}


def main():
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        fits = {int(y): C.CfbParams(**v["mov"]) for y, v in json.load(f)["fits"].items()}
    con = R.connect()
    games = R.load(con)
    con.close()
    plist = [C.CfbParams(k, h, r, a=None, cap=None, conf_w=1.0, entry=e)
             for k, h, r, e in itertools.product(GRID["k"], GRID["hfa"], GRID["regress"], GRID["entry"])]
    walk = R.Walk(games, plist, mov=True)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    pre = {p: dict(C.run(games, p)[0]) for p in set(fits.values())}
    idx = [i for i, g in enumerate(games) if C.completed(g) and R.SCORE_FROM <= g["season"] <= R.SCORE_TO]
    out("POST HOC, not registered. entry fitted on the extended grid, by season: " + ", ".join(
        "%d:%.0f" % (y, walk.params(y).entry) for y in sorted(fits)))
    still = [y for y in sorted(fits) if walk.params(y).entry == GRID["entry"][-1]]
    out("seasons still on the extended edge (-800): %s" % still)
    rows = [{"game": games[i]["game_id"], "stat": "ml", "line": 0.0,
             "y": 1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0,
             "m": walk.p(i), "k": pre[fits[games[i]["season"]]][i]} for i in idx]
    res = F.compare("POST HOC extended entry vs registered fit", rows, out, [])
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"registered": False, "grid": GRID,
                   "fits": {y: walk.params(y).as_dict() for y in sorted(fits)},
                   "still_on_edge": still, "result": res, "log": lines}, f, indent=1)


if __name__ == "__main__":
    main()
