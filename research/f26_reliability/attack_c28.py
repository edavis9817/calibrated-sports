"""f-26 target adapter: c-28's primary, as a-63 publishes it in game/nfl/record.json.

    CLAIM  dBrier(model - baseline), settlement 2001-2025, 6,743 decisive games,
           game blocks: home -0.0255, better record -0.0162, plain Elo -0.0027,
           every interval below zero; and +0.0092 against the nflverse moneyline
           close (2006-2025, 5,281 games).

READ-ONLY. Run as a script (not -m: `research` must resolve to the TARGET's
package, loaded from a detached worktree of origin/c-28-game-forecast):

    python research/f26_reliability/attack_c28.py --src D:/temp/f26/c28src \
        --db D:/calibrated-sports/data/market_log.db --out D:/temp/f26/c28.json

The store is opened here, mode=ro, and handed to the target's own loader; the
target's fitting code is imported, the scoring arithmetic is this file's own.
"""
import argparse
import copy
import json
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISHED = {"home": -0.0255, "record": -0.0162, "elo_nomov": -0.0027, "ml_close": 0.0092}
PUBLISHED_N = {"p1": 6743, "ml_close": 5281}
REGISTERED_INTERVALS = 32            # c-28's own count (result.json registered_intervals)
EXTRA_K = (50.0, 60.0, 80.0, 100.0, 120.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cutoffs", type=int, default=5)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from jobs import season_model as S
    from models import season as M
    from research import game_forecast as GF
    from research import ranking_calibration as rc
    if not os.path.abspath(GF.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.game_forecast resolved outside --src: %s" % GF.__file__)

    R = {"target": "c-28 (published by a-63)", "src": a.src}
    out = lambda s="": print(s, flush=True)  # noqa: E731
    t0 = time.time()
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games, _grp, versions = S.load(con)
    ml_raw = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT g.game_id, g.home_moneyline, g.away_moneyline FROM nfl_games g JOIN (SELECT game_id, "
        "MAX(data_version) dv FROM nfl_games GROUP BY game_id) v ON v.game_id = g.game_id AND v.dv = g.data_version")}
    con.close()
    R["versions"] = versions
    out("store versions %s (c-28 ran at nfl_games 2026-09-30)" % versions)

    def build(games, grid0=None, plant_own=False):
        """-> dict of per-game arrays on c-28's Part-1 population, via c-28's code."""
        grid = S.grid()
        walk = GF.Walk(games, S.game_losses(games, grid), mov=True)
        walk0 = GF.Walk(games, GF.nomov_losses(games, grid0 or grid), mov=False)
        signs = GF.record_signs(games)
        pop = [i for i, g in enumerate(games) if GF.SCORE_FROM <= g["season"] <= GF.SCORE_TO
               and GF.scored(g) and g["home_score"] != g["away_score"]]
        consts = {y: GF.baseline_constants(games, signs, y) for y in range(GF.SCORE_FROM, GF.SCORE_TO + 1)}
        m, e0, hm, rec, y = [], [], [], [], []
        for i in pop:
            g = games[i]
            h, q = consts[g["season"]]
            s = signs[i]
            m.append(walk.p(i) + (1e-3 * (g["home_score"] > g["away_score"]) if plant_own else 0.0))
            e0.append(walk0.p(i)); hm.append(h)
            rec.append(h if s == 0 else (q if s > 0 else 1.0 - q))
            y.append(1.0 if g["home_score"] > g["away_score"] else 0.0)
        return {"pop": pop, "m": np.array(m), "elo_nomov": np.array(e0), "home": np.array(hm),
                "record": np.array(rec), "y": np.array(y), "walk": walk, "walk0": walk0}

    B = build(games)
    pop, y, m = B["pop"], B["y"], B["m"]
    n = len(pop)
    out("built %d Part-1 games in %.0fs" % (n, time.time() - t0))
    gid = [games[i]["game_id"] for i in pop]
    labelings = {"game": gid,
                 "season-week": ["%d-%02d" % (games[i]["season"], games[i]["week"]) for i in pop],
                 "season": [games[i]["season"] for i in pop],
                 "home franchise": [M.franchise(games[i]["home"]) for i in pop]}
    sq = lambda p: (p - y) ** 2  # noqa: E731
    D = {b: sq(m) - sq(B[b]) for b in ("home", "record", "elo_nomov")}

    # ------------------------------------------------------------ 1 reproduce
    out("\n== 1. REPRODUCE (today's store, c-28's fitting code, own Brier arithmetic)")
    R["reproduce"] = [L.reproduce("n Part-1 games", n, PUBLISHED_N["p1"], 0)]
    for b, d in D.items():
        R["reproduce"].append(L.reproduce("dBrier vs %s" % b, d.mean(), PUBLISHED[b], 4))
    # the registered interval, draw for draw: rc.Pop.boot is boot_many's sequence
    reg = {}
    for b in D:
        rows = [{"game": g, "stat": "ml", "line": 0.0, "y": float(y[j]), "m": float(m[j]), "k": float(B[b][j])}
                for j, g in enumerate(gid)]
        p = rc.Pop(b, rows, "m", "k")
        reg[b] = p.boot(lambda i, p=p: rc.brier(p.m[i], p.y[i]) - rc.brier(p.k[i], p.y[i]))
    R["registered_interval_redrawn"] = reg
    rows_elo = [{"game": g, "stat": "ml", "line": 0.0, "y": float(y[j]), "m": float(m[j]), "k": float(B["elo_nomov"][j])}
                for j, g in enumerate(gid)]
    mlj = [j for j, i in enumerate(pop) if games[i]["season"] >= GF.ML_FROM
           and ml_raw.get(games[i]["game_id"], (None, None))[0] is not None
           and ml_raw[games[i]["game_id"]][1] is not None]
    ih = np.array([GF.american(ml_raw[gid[j]][0]) for j in mlj])
    ia = np.array([GF.american(ml_raw[gid[j]][1]) for j in mlj])
    mlj = np.array(mlj)
    dml = sq(m)[mlj] - (ih / (ih + ia) - y[mlj]) ** 2
    R["reproduce"].append(L.reproduce("n moneyline-close games", len(mlj), PUBLISHED_N["ml_close"], 0))
    R["reproduce"].append(L.reproduce("dBrier vs nflverse moneyline close", dml.mean(), PUBLISHED["ml_close"], 4))
    for r in R["reproduce"]:
        out("   %-38s measured %+.6f  published %+.4f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    for b, r in reg.items():
        out("   registered draw (seed %d) vs %-9s %+.4f [%+.4f, %+.4f]" % (rc.SEED, b, r["est"], r["lo"], r["hi"]))
    if not all(r["reproduces"] for r in R["reproduce"]):
        out("   STOP: a headline figure does not reproduce; that is the finding.")

    # ------------------------------------------------------------ 2 blocks
    out("\n== 2. BLOCKS: rows x5 inside their block must not narrow; coarser blocks")
    R["duplication"], R["alt_blocks"], R["seeds"] = {}, {}, {}
    for b, d in list(D.items()) + [("ml_close", dml)]:
        stat = lambda idx, d=d: float(d[idx].mean())  # noqa: E731
        lab = gid if b != "ml_close" else [gid[j] for j in mlj]
        du = L.duplication(stat, len(d), lab, seed=11)
        R["duplication"][b] = du
        out("   %-9s blocked width x%.3f (%s)   iid width x%.3f (expected %.3f; check %s)"
            % (b, du["width_ratio_blocked"], "passes" if du["passes"] else "NARROWED",
               du["width_ratio_iid"], du["expected_iid_ratio"],
               "discriminates" if du["discriminates"] else "DOES NOT DISCRIMINATE"))
    for b, d in D.items():
        stat = lambda idx, d=d: float(d[idx].mean())  # noqa: E731
        R["alt_blocks"][b] = L.alt_blocks(stat, n, labelings, seed=12)
        for nm, r in R["alt_blocks"][b].items():
            out("   %-9s %-15s (%4d blocks) %s" % (b, nm, r["n_blocks"], L.fmt(r)))
    R["seeds"]["elo_nomov"] = L.seeds(lambda idx: float(D["elo_nomov"][idx].mean()), n, gid, n_seeds=10)
    out("   elo_nomov over 10 seeds: share excluding zero %.2f, hi in [%+.4f, %+.4f]"
        % (R["seeds"]["elo_nomov"]["share_excluding_zero"], R["seeds"]["elo_nomov"]["hi_min"],
           R["seeds"]["elo_nomov"]["hi_max"]))

    # the same test THROUGH the target's own bootstrap (GF.boot_many, behind every interval it published)
    def width_of(rws):
        pp = rc.Pop("dup", rws, "m", "k")
        r = GF.boot_many(pp, lambda i: {"dBrier": rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i])},
                         draws=400)["dBrier"]
        return r["hi"] - r["lo"]
    dt_ = L.duplication_through(width_of, rows_elo, "game")
    R["duplication_through_target_bootstrap"] = dt_
    out("   THROUGH GF.boot_many: copies inside their game x%.3f (%s); copies as new games x%.3f (expected %.3f; check %s)"
        % (dt_["ratio_copies_in_block"], "passes" if dt_["passes"] else "NARROWED", dt_["ratio_copies_as_new_blocks"],
           dt_["expected_new_blocks"], "discriminates" if dt_["discriminates"] else "DOES NOT DISCRIMINATE"))

    # ------------------------------------------------------------ 3 leakage
    out("\n== 3. LEAKAGE")

    def infoset_audit(games, params):
        """Black box on run_elo's OUTPUT order (`pre` is appended in processing
        order). info[team] = latest kickoff anywhere in the ancestry of its
        rating. A game whose teams' info reaches its own kickoff read the future."""
        pre, _ = M.run_elo(games, params)
        info, leaks = {}, []
        for i, _p in pre:
            g = games[i]
            k = g["kickoff_ts"] or 0.0
            h, aw = M.franchise(g["home"]), M.franchise(g["away"])
            seen = max(info.get(h, -1.0), info.get(aw, -1.0))
            if seen >= k:
                leaks.append((i, seen - k))
            info[h] = info[aw] = max(seen, k)
        return leaks

    p_any = B["walk"].params(2025)
    leaks = infoset_audit(games, p_any)
    in_pop = set(pop)
    lk_pop = [i for i, _ in leaks if i in in_pop]
    R["leak_infoset"] = {"games_walked_with_future_in_ancestry": len(leaks), "of_them_in_part1": len(lk_pop),
                         "kickoff_ts_null": sum(1 for g in games if g["kickoff_ts"] is None),
                         "by_season": {}, "max_lookahead_days": max([d for _, d in leaks], default=0) / 86400.0}
    for i in lk_pop:
        s = games[i]["season"]
        R["leak_infoset"]["by_season"][s] = R["leak_infoset"]["by_season"].get(s, 0) + 1
    out("   3a info-set audit of the rating walk: %d games (%d in Part 1) entered with a LATER kickoff in"
        " their ratings' ancestry; by season %s; furthest look-ahead %.1f days"
        % (len(leaks), len(lk_pop), R["leak_infoset"]["by_season"], R["leak_infoset"]["max_lookahead_days"]))
    # planted: move one late-season game's scheduled week to 1, kickoff untouched
    planted = copy.deepcopy(games)
    late = max((i for i in pop if games[i]["season"] == 2019 and games[i]["week"] == 12),
               key=lambda i: games[i]["kickoff_ts"])
    planted[late]["week"] = 1
    n_planted = len(infoset_audit(planted, p_any))
    R["leak_infoset"]["planted_fires"] = bool(n_planted > len(leaks))
    R["leak_infoset"]["planted_count"] = n_planted
    out("      planted (one 2019 week-12 game rescheduled to week 1): audit reports %d -> %s"
        % (n_planted, "FIRES" if n_planted > len(leaks) else "DID NOT FIRE - audit is blind"))
    if lk_pop:
        keep = np.array([i not in set(lk_pop) for i in pop])
        R["leak_infoset"]["dBrier_excluding_flagged"] = {b: float(D[b][keep].mean()) for b in D}
        out("      dBrier with the flagged games removed: " + ", ".join(
            "%s %+.4f" % (b, v) for b, v in R["leak_infoset"]["dBrier_excluding_flagged"].items()))

    # 3b black box on the whole pipeline: scramble every result at or after a cutoff;
    # no forecast for a game kicking off at or before the cutoff may move.
    rng = np.random.default_rng(26)
    kicks = sorted({games[i]["kickoff_ts"] for i in pop})
    cut = [kicks[int(q * (len(kicks) - 1))] for q in np.linspace(0.15, 0.95, a.cutoffs)]
    flagged = {i for i, _ in leaks}

    def scrambled(c):
        g2 = copy.deepcopy(games)
        for g in g2:
            if g["kickoff_ts"] is not None and g["kickoff_ts"] >= c and GF.scored(g):
                g["home_score"], g["away_score"] = g["away_score"] + int(rng.integers(0, 9)), g["home_score"]
        return g2

    def moved(Bx, c):
        mv = []
        for j, i in enumerate(pop):
            if games[i]["kickoff_ts"] <= c and i not in flagged:
                if abs(Bx["m"][j] - B["m"][j]) > 1e-12 or abs(Bx["elo_nomov"][j] - B["elo_nomov"][j]) > 1e-12 \
                        or abs(Bx["home"][j] - B["home"][j]) > 1e-12 or abs(Bx["record"][j] - B["record"][j]) > 1e-12:
                    mv.append(i)
        return mv

    R["leak_blackbox"] = {"cutoffs": [], "planted_fires": None}
    for c in cut:
        mv = moved(build(scrambled(c)), c)
        nb = sum(1 for i in pop if games[i]["kickoff_ts"] <= c and i not in flagged)
        R["leak_blackbox"]["cutoffs"].append({"cutoff_ts": c, "forecasts_checked": nb, "moved": len(mv)})
        out("   3b results at/after ts %d scrambled: %d earlier forecasts x 4 arms checked, %d moved"
            % (c, nb, len(mv)))
    # planted for 3b: a forecast that reads its OWN result. (A first plant - season T's
    # parameters fitted through season T - did NOT fire here: the grid argmin did not
    # move under the scramble. 3b is blind to a parameter leak; 3c exists for that.)
    c = cut[len(cut) // 2]
    Bp, Bp2 = build(games, plant_own=True), build(scrambled(c), plant_own=True)
    mvp = [i for j, i in enumerate(pop) if games[i]["kickoff_ts"] <= c and i not in flagged
           and abs(Bp2["m"][j] - Bp["m"][j]) > 1e-12]
    R["leak_blackbox"]["planted_fires"] = bool(mvp)
    R["leak_blackbox"]["planted_moved"] = len(mvp)
    R["leak_blackbox"]["blind_to"] = "a fitted-parameter leak that leaves the grid argmin unchanged (see 3c)"
    out("      planted (forecast reads its own result): %d forecasts moved -> %s"
        % (len(mvp), "FIRES" if mvp else "DID NOT FIRE - check is blind"))

    # 3c fitted parameters: season T's must equal a fit on a store TRUNCATED before T
    grid = S.grid()
    mism, mism_planted, checked = [], [], 0
    for yv in range(GF.SCORE_FROM, GF.SCORE_TO + 1):
        trunc = [g for g in games if g["season"] < yv]
        t1 = S.best_params(S.game_losses(trunc, grid), yv)[0]
        t0_ = S.best_params(GF.nomov_losses(trunc, grid), yv)[0]
        checked += 2
        if t1 != B["walk"].params(yv):
            mism.append((yv, "mov"))
        if t0_ != B["walk0"].params(yv):
            mism.append((yv, "plain"))
        if t1 != B["walk"].params(yv + 1) and yv < GF.SCORE_TO:
            mism_planted.append(yv)
    R["leak_params"] = {"fits_checked": checked, "mismatches": mism, "planted_mismatch_seasons": mism_planted,
                        "planted_fires": bool(mism_planted)}
    out("   3c parameters refitted on a store truncated before each season: %d fits checked, %d differ %s"
        % (checked, len(mism), mism))
    out("      planted (season T uses season T+1's parameters): differs in %d seasons %s -> %s"
        % (len(mism_planted), mism_planted, "FIRES" if mism_planted else "DID NOT FIRE - check is blind"))

    # ------------------------------------------------------------ 4 specifications
    out("\n== 4. SPECIFICATIONS")
    R["multiplicity"] = {}
    for b, d in list(D.items()) + [("ml_close", dml)]:
        se = float(L.block_boot(lambda idx, d=d: float(d[idx].mean()), [np.array([j]) for j in range(len(d))],
                                seed=13).std())
        mu = L.multiplicity(float(d.mean()), se, (3, REGISTERED_INTERVALS, 1000))
        R["multiplicity"][b] = dict(mu, se=se, mde=L.mde_ratio(float(d.mean()), se))
        out("   %-9s z %+.1f  Bonferroni survives at k=3 %s, k=%d %s, k=1000 %s   |est|/MDE %.2f%s"
            % (b, mu["z"], mu["bonferroni"][3]["survives_0.05"], REGISTERED_INTERVALS,
               mu["bonferroni"][REGISTERED_INTERVALS]["survives_0.05"], mu["bonferroni"][1000]["survives_0.05"],
               R["multiplicity"][b]["mde"]["ratio"], "  AT ITS MDE" if R["multiplicity"][b]["mde"]["at_mde"] else ""))
    # 4b the comparator's own specification: is plain Elo held at a grid edge?
    fits0 = {yv: B["walk0"].params(yv) for yv in range(GF.SCORE_FROM, GF.SCORE_TO + 1)}
    fits1 = {yv: B["walk"].params(yv) for yv in range(GF.SCORE_FROM, GF.SCORE_TO + 1)}
    kmax = max(S.GRID["k"])
    at_edge0 = sum(1 for p in fits0.values() if p.k == kmax)
    at_edge1 = sum(1 for p in fits1.values() if p.k in (kmax, min(S.GRID["k"])))
    R["grid_edge"] = {"grid_k": S.GRID["k"], "plain_elo_seasons_at_k_max": at_edge0, "mov_seasons_at_k_edge": at_edge1,
                      "seasons": len(fits0)}
    out("   4b K grid %s: plain Elo's fitted K is the grid maximum in %d of %d scored seasons (MOV at an edge: %d)"
        % (S.GRID["k"], at_edge0, len(fits0), at_edge1))
    import itertools
    wide = [M.EloParams(k, h, r) for k, h, r in
            itertools.product(list(S.GRID["k"]) + list(EXTRA_K), S.GRID["hfa"], S.GRID["regress"])]
    Bw = build(games, grid0=wide)
    if np.abs(Bw["m"] - m).max() > 0:
        raise SystemExit("widening the BASELINE grid moved the model's forecasts - adapter bug")
    dw = sq(m) - sq(Bw["elo_nomov"])
    fw = {yv: Bw["walk0"].params(yv) for yv in range(GF.SCORE_FROM, GF.SCORE_TO + 1)}
    ks = sorted({p.k for p in fw.values()})
    stat = lambda idx: float(dw[idx].mean())  # noqa: E731
    rw = L.summ(stat(np.arange(n)), L.block_boot(stat, L.blocks_of(gid), seed=14))
    rw["mde"] = L.mde_ratio(rw["est"], rw["se"])
    rw["seeds"] = L.seeds(stat, n, gid, n_seeds=10)
    rw["week_1_4"], rw["week_5_plus"] = {}, {}
    reg_mask = np.array([games[i]["game_type"] == "REG" for i in pop])
    wk = np.array([games[i]["week"] for i in pop])
    for nm, mask in (("week_1_4", reg_mask & (wk <= 4)), ("week_5_plus", reg_mask & (wk > 4))):
        jj = np.where(mask)[0]
        for lab, dd in (("registered_grid", D["elo_nomov"]), ("widened_grid", dw)):
            st = lambda idx, dd=dd, jj=jj: float(dd[jj[idx]].mean())  # noqa: E731
            rw[nm][lab] = L.summ(st(np.arange(len(jj))), L.block_boot(st, [np.array([q]) for q in range(len(jj))], seed=15))
    R["grid_edge"]["widened"] = {"extra_k": list(EXTRA_K), "fitted_k_values": ks,
                                 "fitted_k_by_season": {yv: p.k for yv, p in fw.items()},
                                 "still_at_max": sum(1 for p in fw.values() if p.k == max(EXTRA_K)),
                                 "brier_plain_registered": float(sq(B["elo_nomov"]).mean()),
                                 "brier_plain_widened": float(sq(Bw["elo_nomov"]).mean()),
                                 "brier_model": float(sq(m).mean()), "dBrier": rw}
    out("      plain Elo refitted walk-forward with K up to %g: fitted K values %s (at new max in %d seasons)"
        % (max(EXTRA_K), ks, R["grid_edge"]["widened"]["still_at_max"]))
    out("      Brier: model %.4f | plain Elo registered grid %.4f | plain Elo widened grid %.4f"
        % (sq(m).mean(), sq(B["elo_nomov"]).mean(), sq(Bw["elo_nomov"]).mean()))
    out("      dBrier(model - plain Elo), widened grid: %s   |est|/MDE %.2f   excl. zero in %.0f%% of 10 seeds"
        % (L.fmt(rw), rw["mde"]["ratio"], 100 * rw["seeds"]["share_excluding_zero"]))
    for nm in ("week_1_4", "week_5_plus"):
        out("      REG %s: registered %s | widened %s" % (nm, L.fmt(rw[nm]["registered_grid"]), L.fmt(rw[nm]["widened_grid"])))

    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s (%.0fs)" % (a.out, R["seconds"]))


if __name__ == "__main__":
    main()
