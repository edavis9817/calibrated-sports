"""c-49: the walks behind the `grid_fit` as-of repair. READ-ONLY.

PRE-REGISTRATION: docs/C49-grid-fit-asof-preregistration.md (4ceaeca), committed
before this file or the repair existed.

One walk per process. `--src` names the code under test: a detached worktree of
the base commit (e88f114, the whole-walk mask) or this repository (the repair).
cfb.db is opened mode=ro; nothing is written except --out and its .npy sibling.

    python research/c49_grid_fit_asof/walks.py --src D:/temp/c49/base --grid a1 \
        --db D:/calibrated-sports/data/cfb.db --out D:/temp/c49/base_a1.json \
        --make-plant D:/temp/c49/plant.json
    python research/c49_grid_fit_asof/walks.py --src . --grid a1 --plant D:/temp/c49/plant.json ...

    --grid mov    c-39's registered GRID_MOV, 207,360 points
    --grid plain  c-39's registered GRID_PLAIN, mov = False
    --grid a1     GRID_MOV with a = [1.0], 51,840 points - f-32's planted case

THE PLANTED CASE is f-32's, copied from
origin/f-32-grid-fit-mask (b498d2c) research/f32_grid_mask/plant_mask.py and
mask_audit.py (`gfit`, `make_walk`); that directory is track F's and is not
edited. --make-plant builds the synthetic game exactly as plant_mask.py does -
one game a day after the last real one, the lowest-rated current-season team
winning 1-0 at the highest-rated, both under THIS --src's 2005 fit - and writes
it, so both codes can be handed the same planted store with --plant.

`grid_fit` is called on --chunk grid points at a time (8,640, registered). Every
operation in its loop is elementwise over grid points.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

import numpy as np

CHUNK = 8640


def gfit(C, games, plist, mov, chunk):
    """C.grid_fit on `chunk` points at a time. -> (seasons, sums, counts, first_bad or None);
    the base code returns three values and masks its own sums."""
    parts = [C.grid_fit(games, plist[j:j + chunk], mov=mov) for j in range(0, len(plist), chunk)]
    width = {len(p) for p in parts}
    if width not in ({3}, {4}):
        raise SystemExit("grid_fit returned tuples of length %s" % sorted(width))
    for p in parts[1:]:
        if p[0] != parts[0][0] or not np.array_equal(p[2], parts[0][2]):
            raise SystemExit("chunks disagree on seasons or counts")
    fb = np.concatenate([p[3] for p in parts]) if width == {4} else None
    return parts[0][0], np.concatenate([p[1] for p in parts], axis=1), parts[0][2], fb


def make_walk(CF, C, games, plist, mov, chunk):
    """CF.Walk with its grid_fit call chunked; params(), pre() and p() are CF's own."""
    w = CF.Walk.__new__(CF.Walk)
    w.games, w.plist, w.mov = games, plist, mov
    w.seasons, w.sums, w.counts, fb = gfit(C, games, plist, mov, chunk)
    w._pre, w.fits = {}, {}
    if fb is not None:
        w.first_bad, w.undefined = fb, {}
    return w


def fingerprint(games):
    h = hashlib.sha256()
    for g in sorted(games, key=lambda g: g["game_id"]):
        h.update(repr((g["game_id"], g["season"], g["start_ts"], g["home"], g["away"], g["home_conf"],
                       g["away_conf"], g["neutral"], g["home_score"], g["away_score"], g["fit"])).encode())
    return h.hexdigest()


def build_plant(C, CF, games, walk):
    """plant_mask.py's synthetic game, line for line."""
    target = walk.params(CF.SCORE_FROM)
    _pre, r, _s = C.run(games, target, mov=True)
    last = max(g["start_ts"] for g in games)
    live = {t for g in games if g["season"] == CF.CURRENT for t in (g["home"], g["away"])}
    hi = max(live, key=lambda t: r[t])
    lo = min(live, key=lambda t: r[t])
    conf = C.conferences(games)
    gap = r[hi] - r[lo] + target.hfa
    need = 1000.0 * target.a
    fake = {"game_id": "f32-synthetic", "season": CF.CURRENT, "week": 99, "season_type": "regular",
            "start_ts": last + 86400.0, "neutral": 0, "conference_game": 0, "home": hi, "away": lo,
            "home_conf": conf[(CF.CURRENT, hi)], "away_conf": conf[(CF.CURRENT, lo)],
            "home_score": 0, "away_score": 1, "fit": True}
    if C.conferences(games + [fake]) != conf:
        raise SystemExit("the synthetic row changed a conference label - it must not")
    return {"game": fake, "target": target.as_dict(), "rating_hi": r[hi], "rating_lo": r[lo],
            "rating_gap_plus_hfa": gap, "needed": need, "feasible": bool(gap >= need)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--grid", required=True, choices=["mov", "plain", "a1"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--plant")
    ap.add_argument("--make-plant")
    ap.add_argument("--chunk", type=int)
    a = ap.parse_args()
    chunk = CHUNK if a.chunk is None else a.chunk
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    os.chdir(src)
    from models import cfb_game as C
    from research import cfb_game_forecast as CF
    for mod in (C, CF):
        if not os.path.abspath(mod.__file__).startswith(src):
            raise SystemExit("%s resolved outside --src" % mod.__file__)
    t0 = time.time()
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games = CF.load(con)
    con.close()
    real = fingerprint(games)
    pop = [i for i, g in enumerate(games) if C.completed(g) and CF.SCORE_FROM <= g["season"] <= CF.SCORE_TO]
    last_real = max(g["start_ts"] for g in games)
    plant = None
    if a.plant:
        with open(a.plant, encoding="utf-8") as f:
            plant = json.load(f)
        if plant["real_fingerprint"] != real:
            raise SystemExit("the store moved since the plant was built - refusing")
        games = games + [plant["game"]]
    mov = a.grid != "plain"
    spec = {"mov": CF.GRID_MOV, "plain": CF.GRID_PLAIN, "a1": dict(CF.GRID_MOV, a=[1.0])}[a.grid]
    plist = CF.grid(spec)
    walk = make_walk(CF, C, games, plist, mov, chunk)
    repaired = hasattr(walk, "first_bad")
    years = list(range(CF.SCORE_FROM, CF.CURRENT + 1))
    fits, errs = {}, {}
    for y in years:
        try:
            fits[y] = walk.params(y)
        except (SystemExit, ValueError) as e:              # recorded, not swallowed
            errs[str(y)] = repr(e)
    ps, ys = {}, {}
    for i in pop:
        g = games[i]
        ys[g["game_id"]] = 1 if g["home_score"] > g["away_score"] else 0
        if g["season"] in fits:
            try:
                ps[g["game_id"]] = walk.p(i)
            except (SystemExit, ValueError) as e:
                errs.setdefault("p%d" % g["season"], repr(e))
    R = {"src": src, "repaired_code": repaired, "grid": a.grid, "grid_points": len(plist), "chunk": chunk,
         "planted": bool(plant), "real_fingerprint": real, "n_games": len(games), "n_pop": len(pop),
         "last_real_start_ts": last_real, "kick_max_pop": max(games[i]["start_ts"] for i in pop),
         "fits": {str(y): p.as_dict() for y, p in fits.items()},
         "fit_log_loss": {str(y): walk.fits[y]["fit_log_loss"] for y in fits},
         "errors": errs, "p": ps, "y": ys}
    if repaired:
        fb = walk.first_bad
        R["ever_bad"] = int((fb != C.NEVER).sum())
        R["first_bad_by_season"] = {str(int(s)): int((fb == s).sum()) for s in np.unique(fb) if s != C.NEVER}
        R["choice_set"] = {str(y): int((fb >= y).sum()) for y in years + [CF.CURRENT + 1]}
        R["undefined"] = {str(y): {"through_own_season": sum(1 for _i, s in walk.undefined[fits[y]] if s <= y),
                                   "whole_walk": len(walk.undefined[fits[y]])} for y in fits}
        np.save(os.path.splitext(a.out)[0] + "_first_bad.npy", fb)
    else:
        R["ever_bad"] = int(np.isnan(walk.sums[0]).sum())
    if a.make_plant:
        if plant:
            raise SystemExit("--make-plant on an already planted store")
        P = build_plant(C, CF, games, walk)
        P["real_fingerprint"] = real
        P["built_by"] = src
        with open(a.make_plant, "w", encoding="utf-8") as f:
            json.dump(P, f, indent=1)
        print("plant: team %s (%.0f) at team %s (%.0f), gap + hfa %.0f against %.0f -> %s"
              % (P["game"]["away"], P["rating_lo"], P["game"]["home"], P["rating_hi"], P["rating_gap_plus_hfa"],
                 P["needed"], "feasible" if P["feasible"] else "NOT FEASIBLE"), flush=True)
    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f)
    print("%s %s%s: %d games, %d in the population, %d points, ever bad %d, %d fits, %d forecasts, %d errors (%.0fs)"
          % ("repaired" if repaired else "base", a.grid, " PLANTED" if plant else "", len(games), len(pop),
             len(plist), R["ever_bad"], len(fits), len(ps), len(errs), R["seconds"]), flush=True)


if __name__ == "__main__":
    main()
