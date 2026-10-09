"""f-32, POST-HOC and outside the pre-registered rule: make the mask channel visible.

On today's store the whole-walk `bad` mask never changes a choice - not on the
registered grid, not on the declared sub-grid (a != None, which always picks
a = 4.0, a slice with no bad point at all), not on any single-`a` slice. So a
detector for "the future moved the past through the mask" has nothing natural to
fire on. This builds the smallest store on which it must: the grid restricted to
a = 1.0, plus ONE synthetic game dated after every real game, in which the
lowest-rated team (under the 2005 fit) wins at the highest-rated team. If the
rating gap plus home advantage reaches 1000 * a, that fit's denominator is <= 0
in that one game, `grid_fit` masks the point for EVERY season, and forecasts
from 2005 on move because of a game played after all of them.

It says nothing about c-39's published figures. READ-ONLY; cfb.db mode=ro.

    python research/f32_grid_mask/plant_mask.py --src D:/temp/f32/c-39src \
        --db D:/calibrated-sports/data/cfb.db --out D:/temp/f32/plant_mask.json
"""
import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--a", type=float, default=1.0)
    ap.add_argument("--chunk", type=int, default=8640)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import mask_audit as M
    sys.path.insert(0, os.path.abspath(a.src))
    os.chdir(a.src)
    from models import cfb_game as C
    from research import cfb_game_forecast as CF
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games = CF.load(con)
    con.close()
    grid = CF.grid(dict(CF.GRID_MOV, a=[a.a]))
    years = list(range(CF.SCORE_FROM, CF.CURRENT + 1))
    pop = [i for i, g in enumerate(games) if C.completed(g) and CF.SCORE_FROM <= g["season"] <= CF.SCORE_TO]
    w0 = M.make_walk(CF, C, games, grid, True, a.chunk)
    f0 = {y: w0.params(y) for y in years}
    p0 = {i: w0.p(i) for i in pop}
    target = f0[CF.SCORE_FROM]
    _pre, r, _s = C.run(games, target, mov=True)
    last = max(g["start_ts"] for g in games)
    live = {t for g in games if g["season"] == CF.CURRENT for t in (g["home"], g["away"])}
    hi = max(live, key=lambda t: r[t])
    lo = min(live, key=lambda t: r[t])
    conf = C.conferences(games)
    gap = r[hi] - r[lo] + target.hfa
    R = {"a": a.a, "grid_points": len(grid), "target_fit_season": CF.SCORE_FROM, "target": target.as_dict(),
         "home": hi, "away": lo, "rating_gap_plus_hfa": gap, "needed": 1000.0 * a.a, "feasible": gap >= 1000.0 * a.a,
         "bad_before": int((w0.sums[0] != w0.sums[0]).sum())}
    print("target (2005 fit on the a=%s grid): %s" % (a.a, target.as_dict()), flush=True)
    print("synthetic game: team %s (%.0f) at team %s (%.0f), away side wins 1-0, one day after the last real game; "
          "gap + hfa %.0f against %.0f needed -> %s" % (lo, r[lo], hi, r[hi], gap, 1000.0 * a.a,
                                                        "feasible" if R["feasible"] else "NOT FEASIBLE"), flush=True)
    if R["feasible"]:
        fake = {"game_id": "f32-synthetic", "season": CF.CURRENT, "week": 99, "season_type": "regular",
                "start_ts": last + 86400.0, "neutral": 0, "conference_game": 0, "home": hi, "away": lo,
                "home_conf": conf[(CF.CURRENT, hi)], "away_conf": conf[(CF.CURRENT, lo)],
                "home_score": 0, "away_score": 1, "fit": True}
        g1 = games + [fake]
        if C.conferences(g1) != conf:
            raise SystemExit("the synthetic row changed a conference label - it must not")
        w1 = M.make_walk(CF, C, g1, grid, True, a.chunk)
        f1, errs = {}, {}
        for y in years:
            try:
                f1[y] = w1.params(y)
            except (SystemExit, ValueError) as e:
                errs[y] = repr(e)
        moved = sum(1 for i in pop if games[i]["season"] in f1 and abs(w1.p(i) - p0[i]) > 1e-12)
        changed = [y for y in years if f1.get(y) != f0[y]]
        R.update(bad_after=int((w1.sums[0] != w1.sums[0]).sum()), fits_changed=changed, errors=errs,
                 forecasts_checked=len(pop), forecasts_moved=moved, all_checked_kick_before_the_synthetic_game=True,
                 fires=moved > 0,
                 example={str(y): {"before": f0[y].as_dict(), "after": f1[y].as_dict()} for y in changed[:2]})
        print("`bad` points %d -> %d; fits changed in %d of %d seasons %s; %d of %d Part-1 forecasts (every one kicked "
              "off before the synthetic game) MOVED -> %s" % (R["bad_before"], R["bad_after"], len(changed), len(years),
                                                             changed, moved, len(pop),
                                                             "FIRES" if moved else "DID NOT FIRE"), flush=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1)


if __name__ == "__main__":
    main()
