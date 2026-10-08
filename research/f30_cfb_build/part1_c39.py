"""f-30: c-39's settlement result WITH ITS INTERVALS - the pre-registered publish condition.

Model parameters from c-39's committed result file (no refit of the model; the brief forbids
one). Baselines are rebuilt with c-39's own functions: home and record from
CF.baseline_constants, plain Elo from CF.Walk over its 9,600-point grid (the comparator's
fit, as f-29's tail_c39.py does). Each arm goes through GF.boot_many at c-39's draws and
default seed, so a faithful reproduction matches the published bounds exactly, not within MC.

    python research/f30_cfb_build/part1_c39.py --src <c-39 worktree> --db D:/calibrated-sports/data/cfb.db
"""
import argparse, json, os, sqlite3, sys, time
import numpy as np
ap = argparse.ArgumentParser(); ap.add_argument("--src", required=True); ap.add_argument("--db", required=True)
a = ap.parse_args()
src = os.path.abspath(a.src); sys.path.insert(0, src); os.chdir(src)
from models import cfb_game as C
from research import cfb_game_forecast as CF
from research import game_forecast as GF
from research import ranking_calibration as rc
assert os.path.abspath(CF.__file__).startswith(src)
rec = json.load(open(os.path.join(src, "research", "results", "cfb_game_forecast.json"), encoding="utf-8"))
t0 = time.time()
con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
games = CF.load(con); signs = CF.record_signs(con); con.close()
years = list(range(CF.SCORE_FROM, CF.SCORE_TO + 1))
fits = {y: C.CfbParams(**rec["fits"][str(y)]["mov"]) for y in years}
pre = {p: dict(C.run(games, p, mov=True)[0]) for p in set(fits.values())}
walk0 = CF.Walk(games, CF.grid(CF.GRID_PLAIN), mov=False)
plain_same = sum(walk0.params(y).as_dict() == rec["fits"][str(y)]["nomov"] for y in years)
print("plain-Elo comparator refitted here: %d of %d seasons equal the committed fit" % (plain_same, len(years)), flush=True)
pop = [i for i, g in enumerate(games) if C.completed(g) and CF.SCORE_FROM <= g["season"] <= CF.SCORE_TO]
consts = {y: CF.baseline_constants(games, signs, y) for y in years}
def base(b, i):
    g = games[i]; h0, h1, q = consts[g["season"]]; h = h1 if g["neutral"] else h0
    if b == "home": return h
    if b == "record":
        s = signs.get(g["game_id"], 0); return h if s == 0 else (q if s > 0 else 1.0 - q)
    return walk0.p(i)
bad = 0
for b in ("home", "record", "elo_nomov"):
    rows = [{"game": games[i]["game_id"], "stat": "ml", "line": 0.0,
             "y": 1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0,
             "m": pre[fits[games[i]["season"]]][i], "k": base(b, i)} for i in pop]
    pp = rc.Pop(b, rows, "m", "k")
    r = GF.boot_many(pp, lambda i: {"dBrier": rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i])}, draws=2000)["dBrier"]
    p = rec["part1"]["results"][b]["diffs"]["dBrier"]
    same = len(rows) == p["n"] and all(abs(r[k] - p[k]) < 5e-5 for k in ("est", "lo", "hi"))
    bad += not same
    print("vs %-9s n %d (pub %d)  today %+.4f [%+.4f, %+.4f]  published %+.4f [%+.4f, %+.4f]  max abs diff %.1e -> %s"
          % (b, len(rows), p["n"], r["est"], r["lo"], r["hi"], p["est"], p["lo"], p["hi"],
             max(abs(r[k] - p[k]) for k in ("est", "lo", "hi")), "REPRODUCES WITH ITS INTERVAL" if same else "DOES NOT REPRODUCE"), flush=True)
print("%d of 3 arms fail to reproduce (%.0fs)" % (bad, time.time() - t0))
sys.exit(1 if bad else 0)
