"""f-29 target adapter: c-39's college game model.

    CLAIM  dBrier(model - baseline), settlement 2005-2025, 15,508 decisive FBS-FBS
           games, game blocks: home -0.0615, better record -0.0380, plain Elo
           -0.0042, every interval below zero; and +0.0107 [+0.0070, +0.0144]
           against the CFBD moneyline (2021-25, 3,768 games).

READ-ONLY. Run as a script against a detached worktree of origin/c-39-cfb-game-model:

    python research/f26_reliability/attack_c39.py --src D:/temp/f29/c-39src \
        --db D:/calibrated-sports/data/cfb.db --out D:/temp/f29/c39.json

cfb.db is opened here, mode=ro, and handed to the target's own loader. The
fitting code is the target's; the Brier arithmetic is this file's.

WHAT THE LEAKAGE STEP IS AND IS NOT. A full-pipeline scramble costs one 207,360
point grid walk (~5.5 min) per build, so it is NOT run. Instead:
  3a  info-set audit of the rating walk's own output order, with a planted misdated game
  3b  scores at/after a cutoff scrambled, the SCALAR walk re-run at every fitted
      parameter set, no earlier forecast may move; planted misdated game must move one
  3c  the fitted parameters for chosen seasons refitted on a store TRUNCATED before
      the season (the audit the scramble is blind to). This is also the only check
      that sees `grid_fit`'s `bad` mask, which is accumulated over EVERY game walked,
      later seasons included, and removes grid points from every season's choice set.
"""
import argparse
import copy
import itertools
import json
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISHED = {"home": -0.0615, "record": -0.0380, "elo_nomov": -0.0042, "ml": 0.0107}
PUBLISHED_N = {"p1": 15508, "ml": 3768}
STATED_MDE = {"home": 0.0044, "record": 0.0039, "elo_nomov": 0.0011}
TRUNC_SEASONS = (2006, 2008)


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
    from research import game_forecast as GF
    from research import ranking_calibration as rc
    if not os.path.abspath(CF.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.cfb_game_forecast resolved outside --src: %s" % CF.__file__)
    recorded = json.load(open(os.path.join(a.src, "research", "results", "cfb_game_forecast.json"), encoding="utf-8"))
    K_REG = L.registered_count(recorded, "c-39")

    R = {"target": "c-39", "src": a.src}
    out = lambda s="": print(s, flush=True)  # noqa: E731
    t0 = time.time()
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games = CF.load(con)
    signs = CF.record_signs(con)
    ml, _sp, _census = CF.cfbd_lines(con)
    con.close()
    gm, gp = CF.grid(CF.GRID_MOV), CF.grid(CF.GRID_PLAIN)
    walk = CF.Walk(games, gm, mov=True)
    walk0 = CF.Walk(games, gp, mov=False)
    bad_full = int(np.isnan(walk.sums[0]).sum())
    out("loaded %d games; MOV grid %d points, %d dropped as `bad` over the whole walk (%.0fs)"
        % (len(games), len(gm), bad_full, time.time() - t0))
    years = list(range(CF.SCORE_FROM, CF.SCORE_TO + 1))
    pop = [i for i, g in enumerate(games) if C.completed(g) and CF.SCORE_FROM <= g["season"] <= CF.SCORE_TO]
    consts = {yv: CF.baseline_constants(games, signs, yv) for yv in years}
    n = len(pop)
    y = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop])
    m = np.array([walk.p(i) for i in pop])
    e0 = np.array([walk0.p(i) for i in pop])
    hm, rec = [], []
    for i in pop:
        g = games[i]
        h0, h1, q = consts[g["season"]]
        h = h1 if g["neutral"] else h0
        s = signs.get(g["game_id"], 0)
        hm.append(h)
        rec.append(h if s == 0 else (q if s > 0 else 1.0 - q))
    B = {"home": np.array(hm), "record": np.array(rec), "elo_nomov": e0}
    gid = [games[i]["game_id"] for i in pop]
    sq = lambda p: (p - y) ** 2  # noqa: E731
    D = {b: sq(m) - sq(B[b]) for b in B}
    mlj = np.array([j for j, i in enumerate(pop) if games[i]["season"] >= CF.ML_FROM and games[i]["game_id"] in ml])
    mlp = np.array([ml[gid[j]] for j in mlj])
    dml = sq(m)[mlj] - (mlp - y[mlj]) ** 2

    # ------------------------------------------------------------ 1 reproduce
    out("\n== 1. REPRODUCE (today's cfb.db, c-39's fitting code, own Brier arithmetic)")
    R["reproduce"] = [L.reproduce("n Part-1 games", n, PUBLISHED_N["p1"], 0)]
    for b, d in D.items():
        R["reproduce"].append(L.reproduce("dBrier vs %s" % b, d.mean(), PUBLISHED[b], 4))
    R["reproduce"].append(L.reproduce("n CFBD-moneyline games", len(mlj), PUBLISHED_N["ml"], 0))
    R["reproduce"].append(L.reproduce("dBrier vs CFBD moneyline", dml.mean(), PUBLISHED["ml"], 4))
    for r in R["reproduce"]:
        out("   %-28s measured %+.6f  published %+.4f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    fits = {yv: walk.params(yv) for yv in years}
    fits0 = {yv: walk0.params(yv) for yv in years}
    rec_fit = recorded["fits"]
    fit_diff = [yv for yv in years if rec_fit[str(yv)]["mov"] != fits[yv].as_dict()
                or rec_fit[str(yv)]["nomov"] != fits0[yv].as_dict()]
    R["fits_differ_from_recorded"] = fit_diff
    out("   fitted constants vs the committed result file: %d of %d seasons differ %s" % (len(fit_diff), len(years), fit_diff))

    # ------------------------------------------------------------ 2 blocks
    out("\n== 2. BLOCKS: every arm THROUGH GF.boot_many (the function behind compare()'s intervals)")
    arm_rows = {b: [{"game": g, "stat": "ml", "line": 0.0, "y": float(y[j]), "m": float(m[j]), "k": float(B[b][j])}
                    for j, g in enumerate(gid)] for b in D}
    arm_rows["ml"] = [{"game": gid[j], "stat": "ml", "line": 0.0, "y": float(y[j]), "m": float(m[j]),
                       "k": float(mlp[q])} for q, j in enumerate(mlj)]

    def w_many(rws):
        pp = rc.Pop("dup", rws, "m", "k")
        r = GF.boot_many(pp, lambda i: {"dBrier": rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i])},
                         draws=200)["dBrier"]
        return r["hi"] - r["lo"]
    R["through"] = {}
    for b in ("elo_nomov", "ml"):                      # the two narrow headline claims; home/record are 20+ SE
        rws = arm_rows[b]
        t = L.duplication_through(w_many, rws, "game", fn_name="GF.boot_many", units=len({r["game"] for r in rws}))
        R["through"][b] = t
        out("   %-9s %s" % (b, L.through_line(t)))
    R["through_verdicts"] = L.require_through(["elo_nomov", "ml"], R["through"])
    R["through_not_run"] = ["home", "record"]
    out("   home, record: THROUGH NOT RUN (same function, same rows-per-game; z < -25) - named, not passed")
    week = [CF.week_block(games[i]) for i in pop]
    labelings = {"game": gid, "season-week": week, "season": [games[i]["season"] for i in pop],
                 "home team": [games[i]["home"] for i in pop],
                 "home conference": ["%s" % games[i]["home_conf"] for i in pop]}
    R["alt_blocks"] = {}
    for b, d in list(D.items()) + [("ml", dml)]:
        stat = lambda idx, d=d: float(d[idx].mean())  # noqa: E731
        labs = labelings if b != "ml" else {k: [v[j] for j in mlj] for k, v in labelings.items()}
        R["alt_blocks"][b] = L.alt_blocks(stat, len(d), labs, seed=12)
        for nm, r in R["alt_blocks"][b].items():
            out("   %-9s %-15s (%5d blocks) %s" % (b, nm, r["n_blocks"], L.fmt(r)))

    # ------------------------------------------------------------ 3 leakage
    out("\n== 3. LEAKAGE (full-pipeline scramble NOT RUN - see the module docstring)")

    def infoset(gs, params):
        pre, _r, _s = C.run(gs, params, mov=True)
        info, leaks = {}, []
        for i, _p in pre:
            g = gs[i]
            k = g["start_ts"] or 0.0
            seen = max(info.get(g["home"], -1.0), info.get(g["away"], -1.0))
            if seen >= k:
                leaks.append((i, seen - k))
            info[g["home"]] = info[g["away"]] = max(seen, k)
        return leaks
    p25 = fits[2025]
    leaks = infoset(games, p25)
    in_pop = set(pop)
    lk_pop = [(i, d) for i, d in leaks if i in in_pop]
    late = max((i for i in pop if games[i]["season"] == 2019), key=lambda i: games[i]["start_ts"])
    early_ts = min(games[i]["start_ts"] for i in pop if games[i]["season"] == 2019)
    planted = copy.deepcopy(games)
    planted[late]["start_ts"] = early_ts - 86400.0        # misdated: a bowl result filed before week 1
    R["leak_infoset"] = {"flagged": len(leaks), "flagged_in_part1": len(lk_pop),
                         "max_lookahead_days": max([d for _, d in leaks], default=0.0) / 86400.0,
                         "start_ts_null": sum(1 for g in games if g["start_ts"] is None)}
    out("   3a info-set audit of the rating walk: %d games (%d in Part 1) entered with a kickoff at or after their own in"
        " their ratings' ancestry; furthest %.2f days; start_ts null on %d rows"
        % (len(leaks), len(lk_pop), R["leak_infoset"]["max_lookahead_days"], R["leak_infoset"]["start_ts_null"]))
    if lk_pop:
        keep = np.array([i not in {q for q, _ in lk_pop} for i in pop])
        R["leak_infoset"]["dBrier_excluding_flagged"] = {b: float(D[b][keep].mean()) for b in D}
        out("      dBrier with the flagged games removed: " + ", ".join(
            "%s %+.4f" % (b, v) for b, v in R["leak_infoset"]["dBrier_excluding_flagged"].items()))
    # the walk orders by start_ts, so a misdated row is WALKED early and the ancestry audit cannot see it;
    # the plant that the audit must see is the true kickoff carried separately
    true_ts = {g["game_id"]: g["start_ts"] for g in games}

    def infoset_true(gs, params):
        pre, _r, _s = C.run(gs, params, mov=True)
        info, bad = {}, 0
        for i, _p in pre:
            g = gs[i]
            k = true_ts[g["game_id"]] or 0.0
            seen = max(info.get(g["home"], -1.0), info.get(g["away"], -1.0))
            bad += seen >= k
            info[g["home"]] = info[g["away"]] = max(seen, k)
        return int(bad)
    base_true, plant_true = infoset_true(games, p25), infoset_true(planted, p25)
    R["leak_infoset"].update(planted_count=plant_true, planted_fires=bool(plant_true > base_true))
    out("      planted (one 2019 postseason game dated before the 2019 opener, audited on TRUE kickoffs): %d -> %d : %s"
        % (base_true, plant_true, "FIRES" if plant_true > base_true else "DID NOT FIRE - audit is blind"))

    rng = np.random.default_rng(29)
    kicks = sorted(games[i]["start_ts"] for i in pop)
    cuts = [kicks[int(q * (len(kicks) - 1))] for q in (0.25, 0.6, 0.9)]
    psets = sorted(set(fits.values()) | set(fits0.values()), key=repr)

    def scrambled(gs, c):
        g2 = copy.deepcopy(gs)
        for g in g2:
            if C.completed(g) and true_ts[g["game_id"]] >= c:
                g["home_score"], g["away_score"] = g["away_score"] + int(rng.integers(1, 9)), g["home_score"]
                if g["home_score"] == g["away_score"]:
                    g["home_score"] += 1
        return g2

    def moved(gs, c):
        g2, n_moved, n_checked, n_later = scrambled(gs, c), 0, 0, 0
        for p in psets:
            mov = p in set(fits.values())
            a_ = dict(C.run(gs, p, mov=mov)[0])
            b_ = dict(C.run(g2, p, mov=mov)[0])
            for i in pop:
                if true_ts[games[i]["game_id"]] < c:
                    n_checked += 1
                    n_moved += abs(a_[i] - b_[i]) > 1e-12
                else:
                    n_later += abs(a_[i] - b_[i]) > 1e-12
        return n_moved, n_checked, n_later
    R["leak_scalar"] = {"param_sets": len(psets), "cutoffs": []}
    for c in cuts:
        mv, chk, later = moved(games, c)
        R["leak_scalar"]["cutoffs"].append({"cutoff_ts": c, "checked": chk, "moved": mv, "later_moved": later})
        out("   3b scores at/after ts %d scrambled, scalar walk at %d fitted parameter sets: %d earlier forecasts checked, "
            "%d moved (%d later ones moved - proves the scramble ran, nothing more)" % (c, len(psets), chk, mv, later))
    cmid = [k for k in kicks if games[late]["season"] == 2019 and k < true_ts[games[late]["game_id"]]]
    c_pl = cmid[int(0.5 * len([k for k in cmid if k >= early_ts])) + len([k for k in cmid if k < early_ts])]
    mvp, chkp, _ = moved(planted, c_pl)
    R["leak_scalar"].update(planted_moved=int(mvp), planted_fires=bool(mvp))
    out("      planted (the misdated game, cutoff mid-2019): %d earlier forecasts moved -> %s"
        % (mvp, "FIRES" if mvp else "DID NOT FIRE - check is blind"))

    mism, planted_mism, R["leak_params"] = [], [], {"seasons": list(TRUNC_SEASONS), "bad_as_of": {}}
    for yv in TRUNC_SEASONS:
        trunc = [g for g in games if g["season"] < yv]
        wt, wt0 = CF.Walk(trunc, gm, mov=True), CF.Walk(trunc, gp, mov=False)
        t1 = C.best_params(gm, wt.seasons, wt.sums, wt.counts, CF.FIT_FROM, yv)[0]
        t00 = C.best_params(gp, wt0.seasons, wt0.sums, wt0.counts, CF.FIT_FROM, yv)[0]
        bad_t = int(np.isnan(wt.sums[0]).sum())
        R["leak_params"]["bad_as_of"][yv] = bad_t
        if t1 != fits[yv]:
            mism.append((yv, "mov"))
        if t00 != fits0[yv]:
            mism.append((yv, "plain"))
        if t1 != walk.params(yv + 1):
            planted_mism.append(yv)
        # the losses themselves, on every grid point both walks kept, for every fit season before yv
        ok = ~np.isnan(wt.sums[0]) & ~np.isnan(walk.sums[0])
        srows = [walk.seasons.index(s) for s in wt.seasons]
        dmax = float(np.abs(walk.sums[srows][:, ok] - wt.sums[:, ok]).max())
        R["leak_params"]["max_abs_loss_diff_%d" % yv] = dmax
        out("   3c season %d refitted on a store truncated before it: MOV %s, plain %s; per-season losses differ by at most "
            "%.2e on %d shared grid points; `bad` points as of then %d against %d over the whole walk"
            % (yv, "SAME" if t1 == fits[yv] else "DIFFERS", "SAME" if t00 == fits0[yv] else "DIFFERS", dmax,
               int(ok.sum()), bad_t, bad_full))
    R["leak_params"].update(mismatches=mism, planted_mismatch_seasons=planted_mism, planted_fires=bool(planted_mism),
                            bad_full=bad_full)
    out("      planted (season T given season T+1's parameters): differs in %s -> %s"
        % (planted_mism, "FIRES" if planted_mism else "DID NOT FIRE - check is blind"))
    chosen_a = sorted({str(p.a) for p in fits.values()})
    out("      the `bad` mask reads later seasons, but it can only remove points with a damping constant; fitted a over "
        "%d seasons: %s" % (len(years), chosen_a))
    R["leak_params"]["fitted_a_values"] = chosen_a

    # ------------------------------------------------------------ 4 specifications / 5 MDE
    out("\n== 4-5. SPECIFICATIONS (k = c-39's own %d registered intervals) AND MDE" % K_REG)
    R["multiplicity"] = {}
    for b, d in list(D.items()) + [("ml", dml)]:
        se = float(L.block_boot(lambda idx, d=d: float(d[idx].mean()), [np.array([j]) for j in range(len(d))],
                                seed=13).std())
        mu = L.multiplicity(float(d.mean()), se, (3, K_REG, 1000), n_blocks=len(d))
        R["multiplicity"][b] = dict(mu, se=se, mde=L.mde_ratio(float(d.mean()), se),
                                    mde_claim=L.mde_claim(STATED_MDE.get(b), se) if b in STATED_MDE else None)
        out("   %-9s z %+.1f  Bonferroni survives k=3 %s, k=%d %s, k=1000 %s   |est|/MDE %.2f (%s)%s"
            % (b, mu["z"], mu["bonferroni"][3]["survives_0.05"], K_REG, mu["bonferroni"][K_REG]["survives_0.05"],
               mu["bonferroni"][1000]["survives_0.05"], R["multiplicity"][b]["mde"]["ratio"],
               R["multiplicity"][b]["mde"]["reading"],
               "" if b not in STATED_MDE else "  stated MDE %.4f vs re-measured %.4f -> %s"
               % (STATED_MDE[b], 2.8 * se, "consistent" if R["multiplicity"][b]["mde_claim"]["consistent"] else "INCONSISTENT")))
    # 4b the comparator's specification: plain Elo's entry offset sits on the grid edge
    edge0 = {nm: sum(1 for p in fits0.values() if getattr(p, nm) in (CF.GRID_PLAIN[nm][0], CF.GRID_PLAIN[nm][-1]))
             for nm in ("k", "hfa", "regress", "entry")}
    wide_spec = dict(CF.GRID_PLAIN, entry=list(CF.GRID_PLAIN["entry"]) + [-500.0, -600.0, -800.0])
    gw = [C.CfbParams(*v) for v in itertools.product(*(wide_spec[nm] for nm in CF.NAMES))]
    ww = CF.Walk(games, gw, mov=False)
    ew = np.array([ww.p(i) for i in pop])
    dw = sq(m) - sq(ew)
    stat = lambda idx: float(dw[idx].mean())  # noqa: E731
    rw = L.summ(stat(np.arange(n)), L.block_boot(stat, L.blocks_of(gid), seed=14))
    fw = sorted({ww.params(yv).entry for yv in years})
    R["grid_edge"] = {"plain_seasons_at_an_edge": edge0, "widened_entry_values": fw, "dBrier_widened": rw,
                      "brier_plain_registered": float(sq(e0).mean()), "brier_plain_widened": float(sq(ew).mean()),
                      "still_at_new_edge": sum(1 for yv in years if ww.params(yv).entry == -800.0)}
    out("   4b plain Elo (the comparator) sits on a grid edge in this many of %d seasons: %s" % (len(years), edge0))
    out("      refitted walk-forward with entry allowed to -800: fitted entry values %s (%d seasons on the new edge); "
        "Brier plain %.4f -> %.4f; dBrier(model - plain) %s"
        % (fw, R["grid_edge"]["still_at_new_edge"], sq(e0).mean(), sq(ew).mean(), L.fmt(rw)))
    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s (%.0fs)" % (a.out, R["seconds"]))


if __name__ == "__main__":
    main()
