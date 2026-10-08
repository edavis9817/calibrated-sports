"""f-29: steps 3c, 4 and 5 for c-39, without the 207,360-point walk over every season.

attack_c39.py was stopped by the harness's 30-minute limit after steps 1-3b
(run 3). This file finishes it. It takes the MODEL's per-season parameters from
c-39's committed result file instead of refitting them - valid only because
attack_c39.py step 1 printed `fitted constants vs the committed result file:
0 of 21 seasons differ` in the same run; if that line ever reads otherwise,
do not use this file. The plain-Elo comparator is refitted here (9,600 points).

    python research/f26_reliability/tail_c39.py --src D:/temp/f29/c-39src \
        --db D:/calibrated-sports/data/cfb.db --out D:/temp/f29/c39_tail.json
"""
import argparse
import itertools
import json
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISHED = {"home": -0.0615, "record": -0.0380, "elo_nomov": -0.0042, "ml": 0.0107}
STATED_MDE = {"home": 0.0044, "record": 0.0039, "elo_nomov": 0.0011}
TRUNC = 2006


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    os.chdir(a.src)
    from models import cfb_game as C
    from research import cfb_game_forecast as CF
    recorded = json.load(open(os.path.join(a.src, "research", "results", "cfb_game_forecast.json"), encoding="utf-8"))
    K_REG = L.registered_count(recorded, "c-39")
    out = lambda s="": print(s, flush=True)  # noqa: E731
    t0 = time.time()
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games = CF.load(con)
    signs = CF.record_signs(con)
    ml, _sp, _c = CF.cfbd_lines(con)
    con.close()
    years = list(range(CF.SCORE_FROM, CF.SCORE_TO + 1))
    fits = {yv: C.CfbParams(**recorded["fits"][str(yv)]["mov"]) for yv in years + [CF.SCORE_TO + 1]}
    pre = {p: dict(C.run(games, p, mov=True)[0]) for p in set(fits.values())}
    gp = CF.grid(CF.GRID_PLAIN)
    walk0 = CF.Walk(games, gp, mov=False)
    pop = [i for i, g in enumerate(games) if C.completed(g) and CF.SCORE_FROM <= g["season"] <= CF.SCORE_TO]
    consts = {yv: CF.baseline_constants(games, signs, yv) for yv in years}
    y = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop])
    m = np.array([pre[fits[games[i]["season"]]][i] for i in pop])
    e0 = np.array([walk0.p(i) for i in pop])
    hm, rec = [], []
    for i in pop:
        g = games[i]
        h0, h1, q = consts[g["season"]]
        h = h1 if g["neutral"] else h0
        s = signs.get(g["game_id"], 0)
        hm.append(h)
        rec.append(h if s == 0 else (q if s > 0 else 1.0 - q))
    gid = [games[i]["game_id"] for i in pop]
    sq = lambda p: (p - y) ** 2  # noqa: E731
    D = {"home": sq(m) - sq(np.array(hm)), "record": sq(m) - sq(np.array(rec)), "elo_nomov": sq(m) - sq(e0)}
    mlj = np.array([j for j, i in enumerate(pop) if games[i]["season"] >= CF.ML_FROM and games[i]["game_id"] in ml])
    D["ml"] = sq(m)[mlj] - (np.array([ml[gid[j]] for j in mlj]) - y[mlj]) ** 2
    R = {"target": "c-39 (tail)", "reproduce": []}
    out("== the recorded-fit route gives the same headline (else this file's premise is false)")
    for b, d in D.items():
        r = L.reproduce("dBrier vs %s" % b, d.mean(), PUBLISHED[b], 4)
        R["reproduce"].append(r)
        out("   %-22s measured %+.6f  published %+.4f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    if not all(r["reproduces"] for r in R["reproduce"]):
        raise SystemExit("the recorded fits do not reproduce the headline - stop")

    out("\n== 3c. PARAMETERS refitted on a store truncated before %d" % TRUNC)
    gm = CF.grid(CF.GRID_MOV)
    trunc = [g for g in games if g["season"] < TRUNC]
    seasons, sums, counts = C.grid_fit(trunc, gm, mov=True)
    t1 = C.best_params(gm, seasons, sums, counts, CF.FIT_FROM, TRUNC)[0]
    s0, u0, c0 = C.grid_fit(trunc, gp, mov=False)
    t00 = C.best_params(gp, s0, u0, c0, CF.FIT_FROM, TRUNC)[0]
    bad_t = int(np.isnan(sums[0]).sum())
    plant = t1 != fits[TRUNC + 1]
    R["leak_params"] = {"season": TRUNC, "mov_same": t1 == fits[TRUNC], "plain_same": t00 == walk0.params(TRUNC),
                        "truncated_fit": t1.as_dict(), "bad_as_of": bad_t, "planted_fires": bool(plant),
                        "fits_checked": 2, "seasons_not_checked": [yv for yv in years if yv != TRUNC]}
    out("   MOV fit %s the walk's, plain fit %s; grid points dropped as `bad` as of then: %d (29,505 over the whole walk)"
        % ("EQUALS" if t1 == fits[TRUNC] else "DIFFERS FROM", "EQUALS" if t00 == walk0.params(TRUNC) else "DIFFERS", bad_t))
    out("      planted (season %d given season %d's parameters): %s" % (TRUNC, TRUNC + 1, "FIRES" if plant else "DID NOT FIRE"))
    out("      ONE season of 21 checked this way (2 fits). The other 20 rest on 3b and on the fitted damping constant "
        "being None in every season, which the `bad` mask cannot remove.")

    out("\n== 4-5. SPECIFICATIONS (k = c-39's own %d registered intervals) AND MDE" % K_REG)
    R["multiplicity"] = {}
    for b, d in D.items():
        se = float(L.block_boot(lambda idx, d=d: float(d[idx].mean()), [np.array([j]) for j in range(len(d))],
                                seed=13).std())
        mu = L.multiplicity(float(d.mean()), se, (3, K_REG, 1000), n_blocks=len(d))
        md = L.mde_ratio(float(d.mean()), se)
        cl = L.mde_claim(STATED_MDE[b], se) if b in STATED_MDE else None
        R["multiplicity"][b] = dict(mu, se=se, mde=md, mde_claim=cl)
        out("   %-9s z %+.1f  Bonferroni survives k=3 %s, k=%d %s, k=1000 %s   |est|/MDE %.2f (%s)%s"
            % (b, mu["z"], mu["bonferroni"][3]["survives_0.05"], K_REG, mu["bonferroni"][K_REG]["survives_0.05"],
               mu["bonferroni"][1000]["survives_0.05"], md["ratio"], md["reading"],
               "" if cl is None else "  stated MDE %.4f vs re-measured %.4f -> %s"
               % (cl["stated"], cl["remeasured"], "consistent" if cl["consistent"] else "INCONSISTENT")))

    out("\n== 4b. THE COMPARATOR'S OWN GRID: plain Elo's entry offset")
    fits0 = {yv: walk0.params(yv) for yv in years}
    edge0 = {nm: sum(1 for p in fits0.values() if getattr(p, nm) in (CF.GRID_PLAIN[nm][0], CF.GRID_PLAIN[nm][-1]))
             for nm in ("k", "hfa", "regress", "entry")}
    wide = dict(CF.GRID_PLAIN, entry=list(CF.GRID_PLAIN["entry"]) + [-500.0, -600.0, -800.0])
    ww = CF.Walk(games, [C.CfbParams(*v) for v in itertools.product(*(wide[nm] for nm in CF.NAMES))], mov=False)
    ew = np.array([ww.p(i) for i in pop])
    dw = sq(m) - sq(ew)
    stat = lambda idx: float(dw[idx].mean())  # noqa: E731
    rw = L.summ(stat(np.arange(len(pop))), L.block_boot(stat, L.blocks_of(gid), seed=14))
    fw = sorted({ww.params(yv).entry for yv in years})
    R["grid_edge"] = {"plain_seasons_at_an_edge": edge0, "widened_entry_values": fw, "dBrier_widened": rw,
                      "brier_plain_registered": float(sq(e0).mean()), "brier_plain_widened": float(sq(ew).mean()),
                      "still_at_new_edge": sum(1 for yv in years if ww.params(yv).entry == -800.0)}
    out("   plain Elo on a grid edge, seasons of %d: %s" % (len(years), edge0))
    out("   refitted walk-forward with entry allowed to -800: fitted entry values %s (%d seasons on the new edge); "
        "Brier plain %.4f -> %.4f; dBrier(model - plain) %s"
        % (fw, R["grid_edge"]["still_at_new_edge"], sq(e0).mean(), sq(ew).mean(), L.fmt(rw)))
    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s (%.0fs)" % (a.out, R["seconds"]))


if __name__ == "__main__":
    main()
