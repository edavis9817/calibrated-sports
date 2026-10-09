"""f-30: the three c-39 price claims f-29 did not attack, and the comparator itself.

f-29 (run 3) reproduced c-39's Part 1 and the CFBD-moneyline gap (2a), ran them through
GF.boot_many, audited leakage with plants and corrected over k = 1000. It did not touch:

  2c   +0.0321 on 168 games - the ONLY timestamped price, three weeks of one season
  2b   the model's side covers 0.4980 [0.4880, 0.5075] against the CFBD spread, n 9,652
  2a's comparator: c-39 labels the CFBD moneyline "the provider's last value, read after
       the game - not a timestamped close", and its registered verdict ("the method is the
       limit") is conditioned on 2a. Whether that value BEHAVES like a pre-kickoff price is
       measurable on the 2026 games that carry both it and a timestamped Odds API quote.

Steps on each: reproduce; duplication THROUGH the target's own bootstrap; coarser blocks;
Bonferroni over c-39's own registered count; the estimate against its MDE. The comparator
step carries a plant (a share of CFBD values nudged toward the result) and must see it fire.

The model's per-season parameters come from c-39's committed result file, NOT a refit
(the brief forbids one). That is valid only while the recorded-fit route reproduces the
published 2a figure, which this file checks first and refuses on.

    python research/f30_cfb_build/attack_c39_prices.py --src <c-39 worktree> \
        --db D:/calibrated-sports/data/cfb.db --out D:/temp/f30/c39_prices.json
"""
import argparse
import json
import os
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "f26_reliability"))
    import f26lib as L
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    os.chdir(src)
    from models import cfb_game as C
    from models import game as G
    from research import cfb_game_forecast as CF
    from research import game_forecast as GF
    from research import ranking_calibration as rc
    for mod in (C, CF, GF, rc):
        assert os.path.abspath(mod.__file__).startswith(src), mod.__file__
    recorded = json.load(open(os.path.join(src, "research", "results", "cfb_game_forecast.json"), encoding="utf-8"))
    K_REG = L.registered_count(recorded, "c-39")
    # the published figures are read from the TARGET's committed result file, never kept here (f-34)
    pub2a = recorded["part2"]["2a"]["diffs"]["dBrier"]
    out = lambda s="": print(s, flush=True)  # noqa: E731
    t0 = time.time()
    R = {}
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games = CF.load(con)
    ml, sp, _cen = CF.cfbd_lines(con)
    oc, ocen = CF.odds_api_closes(con)
    con.close()
    years = list(range(CF.SCORE_FROM, CF.CURRENT + 1))
    fits = {yv: C.CfbParams(**recorded["fits"][str(yv)]["mov"]) for yv in years}
    sig = {yv: recorded["fits"][str(yv)]["sigma_m"] for yv in years}
    pre = {p: dict(C.run(games, p, mov=True)[0]) for p in set(fits.values())}
    P = lambda i: pre[fits[games[i]["season"]]][i]  # noqa: E731
    done = [i for i, g in enumerate(games) if C.completed(g)]
    pop1 = [i for i in done if CF.SCORE_FROM <= games[i]["season"] <= CF.SCORE_TO]
    cur = [i for i in done if games[i]["season"] == CF.CURRENT]
    Y = lambda i: 1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0  # noqa: E731
    row = lambda i, k: {"game": games[i]["game_id"], "stat": "ml", "line": 0.0, "y": Y(i), "m": P(i), "k": k}  # noqa: E731

    def dbrier(rows, draws=2000):
        pp = rc.Pop("x", rows, "m", "k")
        return GF.boot_many(pp, lambda i: {"dBrier": rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i])},
                            draws=draws)["dBrier"]

    out("== 0. PREMISE: the recorded-fit route reproduces c-39's 2a figure (else stop)")
    r2a = [row(i, ml[games[i]["game_id"]]) for i in pop1 if games[i]["season"] >= CF.ML_FROM and games[i]["game_id"] in ml]
    e2a = dbrier(r2a, 200)
    ok = len(r2a) == pub2a["n"] and round(e2a["est"], 4) == round(pub2a["est"], 4)
    out("   2a n %d (published %d), dBrier %+.6f (published %+.4f) -> %s"
        % (len(r2a), pub2a["n"], e2a["est"], pub2a["est"], "REPRODUCES" if ok else "DOES NOT REPRODUCE"))
    if not ok:
        raise SystemExit("the recorded-fit route does not reproduce 2a on today's store; nothing below is valid")
    R["premise_2a"] = {"n": len(r2a), "est": e2a["est"]}

    # ------------------------------------------------------------------ 2c
    out("\n== 2c. the timestamped Odds API pre-kickoff h2h, %d" % CF.CURRENT)
    idx = [i for i in cur if games[i]["game_id"] in oc]
    rows = [row(i, oc[games[i]["game_id"]][0]) for i in idx]
    r = dbrier(rows)
    pub = recorded["part2"]["2c"]["result"]["diffs"]["dBrier"]
    out("   census today %s" % json.dumps(ocen))
    out("   today: n %d, dBrier %+.4f [%+.4f, %+.4f] SE %.4f   published: n %d, %+.4f [%+.4f, %+.4f] SE %.4f"
        % (len(rows), r["est"], r["lo"], r["hi"], r["se"], pub["n"], pub["est"], pub["lo"], pub["hi"], pub["se"]))
    moved = len(rows) != pub["n"]
    out("   -> %s" % ("THE POPULATION MOVED since c-39 ran (%+d games); the published figure is a reading at that time"
                      % (len(rows) - pub["n"]) if moved else
                      ("REPRODUCES to 4 dp" if round(r["est"], 4) == round(pub["est"], 4) else "SAME n, DIFFERENT ESTIMATE")))
    # the published 168, if the population grew: the games c-39 could have seen are those kicked before its run
    wk = [CF.week_block(games[i]) for i in idx]
    out("   games by CFB week block: %s" % json.dumps({w: wk.count(w) for w in sorted(set(wk))}))
    w = lambda rws: (lambda q: q["hi"] - q["lo"])(dbrier(rws, 200))  # noqa: E731
    t = L.duplication_through(w, rows, "game", fn_name="GF.boot_many", units=len({x["game"] for x in rows}))
    out("   " + L.through_line(t))
    d = np.array([(x["m"] - x["y"]) ** 2 - (x["k"] - x["y"]) ** 2 for x in rows])
    stat = lambda ii: float(d[ii].mean())  # noqa: E731
    day = [int(games[i]["start_ts"] // 86400) for i in idx]
    slot = [int(games[i]["start_ts"] // 3600) for i in idx]
    labs = {"game": [x["game"] for x in rows], "kickoff hour": slot, "kickoff day": day, "week": wk,
            "home conference": ["%s" % games[i]["home_conf"] for i in idx]}
    alt = L.alt_blocks(stat, len(d), labs, seed=30)
    for nm, q in alt.items():
        out("   blocks: %-16s (%4d) %s" % (nm, q["n_blocks"], L.fmt(q)))
    mult = L.multiplicity(r["est"], r["se"], (1, K_REG, 1000), n_blocks=len(rows))
    mde = L.mde_ratio(r["est"], r["se"])
    claim = L.mde_claim(pub.get("mde"), r["se"])
    out("   z %+.2f  p %.5f  Bonferroni survives k=%d: %s (p_adj %.4f), k=1000: %s"
        % (mult["z"], mult["p"], K_REG, mult["bonferroni"][K_REG]["survives_0.05"], mult["bonferroni"][K_REG]["p_adj"],
           mult["bonferroni"][1000]["survives_0.05"]))
    out("   MDE (80%% power, 2.8 SE) %.4f; |estimate| / MDE %.2f -> %s; c-39 stated MDE %s -> %s"
        % (mde["mde"], mde["ratio"], mde["reading"], pub.get("mde"), "consistent" if claim["consistent"] else "NOT consistent"))
    # the worst coarser-block SE gives the honest ratio
    worst = max((q for q in alt.values() if q.get("n_blocks", 0) >= 5 and q.get("se")), key=lambda q: q["se"])
    wname = [k for k, q in alt.items() if q is worst][0]
    out("   under the widest readable blocks (%s, %d): SE %.4f, |estimate| / MDE %.2f -> %s"
        % (wname, worst["n_blocks"], worst["se"], abs(r["est"]) / (2.8 * worst["se"]),
           L.mde_ratio(r["est"], worst["se"])["reading"]))
    R["2c"] = {"n": len(rows), "result": r, "published": pub, "through": t, "alt_blocks": alt, "multiplicity": mult,
               "mde": mde, "mde_claim": claim, "weeks": {w_: wk.count(w_) for w_ in set(wk)},
               "widest_block": {"name": wname, "se": worst["se"], "ratio": abs(r["est"]) / (2.8 * worst["se"])}}

    # ------------------------------------------------------------------ 2b(iii)
    out("\n== 2b(iii). the model's side against the CFBD spread, %d-%d" % (CF.SPREAD_FROM, CF.SCORE_TO))
    idx = [i for i in pop1 if games[i]["season"] >= CF.SPREAD_FROM and games[i]["game_id"] in sp]
    marg = np.array([games[i]["home_score"] - games[i]["away_score"] for i in idx], float)
    line = np.array([-sp[games[i]["game_id"]] for i in idx])
    mu = np.array([G.GameForecast(game_id=games[i]["game_id"], home=games[i]["home"], away=games[i]["away"],
                                  as_of="kickoff", p_home=P(i), sigma_m=sig[games[i]["season"]], mu_t=0.0,
                                  sigma_t=1.0).margin_mean() for i in idx])
    corr = float(np.corrcoef(line, marg)[0, 1])
    out("   sign check: corr(-spread, home margin) %+.3f on %d games (c-39: +0.668 on 9,822)" % (corr, len(idx)))
    R["2b"] = {"n_games": len(idx), "corr": corr, "hit": {}}
    for thr in CF.EDGE_POINTS:
        rs, keep = [], []
        for j, i in enumerate(idx):
            e = mu[j] - line[j]
            if marg[j] == line[j] or e == 0 or abs(e) < thr:
                continue
            rs.append({"game": games[i]["game_id"], "stat": "x", "line": 0.0, "m": 0.0, "k": 0.0,
                       "y": 1.0 if (marg[j] > line[j]) == (e > 0) else 0.0})
            keep.append(i)

        def hit(rws, draws=2000):
            pp = rc.Pop("hit", rws, "m", "k")
            return pp.boot(lambda ii, pp=pp: float(pp.y[ii].mean()), draws=draws)
        q = hit(rs)
        pubh = recorded["part2"]["2b"]["hit_rate"][str(thr)]
        t = L.duplication_through(lambda rws: (lambda z: z["hi"] - z["lo"])(hit(rws, 200)), rs, "game",
                                  fn_name="rc.Pop.boot", units=len({x["game"] for x in rs}))
        yv = np.array([x["y"] for x in rs])
        alt = L.alt_blocks(lambda ii: float(yv[ii].mean() - 0.5), len(yv),
                           {"game": [x["game"] for x in rs], "season-week": [CF.week_block(games[i]) for i in keep],
                            "season": [games[i]["season"] for i in keep]}, seed=30)
        mde = 2.8 * q["se"]
        gap_be = (CF.BREAK_EVEN - q["est"]) / q["se"]
        out("   |edge| >= %.0f: n %d (published %d)  %.4f [%.4f, %.4f] (published %.4f [%.4f, %.4f]) -> %s"
            % (thr, len(rs), pubh["n"], q["est"], q["lo"], q["hi"], pubh["est"], pubh["lo"], pubh["hi"],
               "REPRODUCES" if len(rs) == pubh["n"] and round(q["est"], 4) == round(pubh["est"], 4) else "DOES NOT REPRODUCE"))
        out("      " + L.through_line(t))
        out("      season blocks (%d): hit - 0.5 = %s;  detectable hit rate at 80%% power: 0.5 +/- %.4f; break-even %.4f is %.1f SE above the estimate"
            % (alt["season"]["n_blocks"], L.fmt(alt["season"]), mde, CF.BREAK_EVEN, gap_be))
        R["2b"]["hit"][str(thr)] = {"n": len(rs), "result": q, "published": pubh, "through": t, "alt_blocks": alt,
                                    "mde_hit": mde, "se_below_break_even": gap_be}
    L.require_through(["0.0", "3.0", "7.0"], {k: v["through"] for k, v in R["2b"]["hit"].items()})

    # ------------------------------------------------------------------ the comparator
    out("\n== THE COMPARATOR. does CFBD's untimestamped moneyline behave like a pre-kickoff price?")
    both = [i for i in cur if games[i]["game_id"] in oc and games[i]["game_id"] in ml]
    out("   %d %d games carry BOTH a CFBD moneyline and a timestamped pre-kickoff Odds API h2h (of %d with the latter)"
        % (CF.CURRENT, len(both), len([i for i in cur if games[i]["game_id"] in oc])))
    R["comparator"] = {"n": len(both)}
    if len(both) >= 30:
        y = np.array([Y(i) for i in both])
        cf = np.array([ml[games[i]["game_id"]] for i in both])
        oa = np.array([oc[games[i]["game_id"]][0] for i in both])
        mm = np.array([P(i) for i in both])
        gl = [games[i]["game_id"] for i in both]
        ad = np.abs(cf - oa)

        def toward(c):
            # > 0 when the CFBD value sits closer to the RESULT than the timestamped pre-kickoff quote does
            return lambda ii: float(((c[ii] - oa[ii]) * (2 * y[ii] - 1)).mean())
        bl = L.blocks_of(gl)
        s_t = L.summ(toward(cf)(np.arange(len(y))), L.block_boot(toward(cf), bl, seed=30))
        db = lambda ii: float((((cf[ii] - y[ii]) ** 2) - ((oa[ii] - y[ii]) ** 2)).mean())  # noqa: E731
        s_b = L.summ(db(np.arange(len(y))), L.block_boot(db, bl, seed=30))
        g_cf = lambda ii: float((((mm[ii] - y[ii]) ** 2) - ((cf[ii] - y[ii]) ** 2)).mean())  # noqa: E731
        g_oa = lambda ii: float((((mm[ii] - y[ii]) ** 2) - ((oa[ii] - y[ii]) ** 2)).mean())  # noqa: E731
        s_gc = L.summ(g_cf(np.arange(len(y))), L.block_boot(g_cf, bl, seed=30))
        s_go = L.summ(g_oa(np.arange(len(y))), L.block_boot(g_oa, bl, seed=30))
        out("   |CFBD - pre-kickoff|: median %.4f, p90 %.4f, max %.4f; beyond 0.05 on %d, beyond 0.10 on %d of %d"
            % (np.median(ad), np.percentile(ad, 90), ad.max(), int((ad > 0.05).sum()), int((ad > 0.10).sum()), len(ad)))
        out("   mean signed move TOWARD the result, CFBD minus pre-kickoff: %s   (in-game values would make this positive)" % L.fmt(s_t))
        out("   Brier(CFBD) - Brier(pre-kickoff): %s   (Brier %.4f vs %.4f)" % (L.fmt(s_b), ((cf - y) ** 2).mean(), ((oa - y) ** 2).mean()))
        out("   the model's gap on these games: to CFBD %s; to the timestamped quote %s" % (L.fmt(s_gc), L.fmt(s_go)))
        # the plant: one game in ten carries a value moved 30% of the way to the result
        rng = np.random.default_rng(30)
        pl = cf.copy()
        hitg = rng.random(len(pl)) < 0.10
        pl[hitg] = 0.7 * pl[hitg] + 0.3 * y[hitg]
        s_p = L.summ(toward(pl)(np.arange(len(y))), L.block_boot(toward(pl), bl, seed=30))
        fires = bool(s_p["lo"] > 0)
        out("   PLANT (%d of %d values moved 30%% of the way to the result): %s -> %s"
            % (int(hitg.sum()), len(pl), L.fmt(s_p), "FIRES" if fires else "DID NOT FIRE - the test cannot see contamination this size"))
        mdet = 2.8 * s_t["se"]
        out("   smallest mean move toward the result this sample could detect (80%% power): %.4f" % mdet)
        R["comparator"].update(abs_diff={"median": float(np.median(ad)), "p90": float(np.percentile(ad, 90)),
                                         "max": float(ad.max()), "gt05": int((ad > 0.05).sum()), "gt10": int((ad > 0.10).sum())},
                               toward_result=s_t, brier_cfbd_minus_prekick=s_b, model_gap_cfbd=s_gc, model_gap_prekick=s_go,
                               plant=s_p, plant_fires=fires, mde_toward=mdet)
    else:
        out("   fewer than 30 games - not read")
    json.dump(R, open(a.out, "w", encoding="utf-8"), indent=1, default=float)
    out("\nwrote %s (%.0fs)" % (a.out, time.time() - t0))


if __name__ == "__main__":
    main()
