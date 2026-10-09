"""f-31 target adapter: c-34's "known before kickoff" null.

    CLAIM  NFL receptions and rush attempts, over side, c-24's P1 2023-2024 REGULAR
           SEASON (9,418 rows, 36 season-weeks) against the DK/FD/MGM close: adding
           injury status (arm 1) or teammate absence (arm 3) as a walk-forward
           stat-level multiplier does not raise CORP resolution -
           dDSC +0.00010 [-0.00022, +0.00040] (MDE 0.00044) and
           +0.00002 [-0.00017, +0.00019] (MDE 0.00026) - and lowers miscalibration,
           dMCB -0.00075 [-0.00151, -0.00002] and -0.00140 [-0.00206, -0.00078].
           "MDEs are 6-11% of the DSC gap, so this is a tight null."

READ-ONLY. Run as a script against a detached worktree of origin/c-34-known-before-kickoff:

    python research/f26_reliability/attack_c34.py --src <worktree> --rows <c34 scratch>/predictions.csv \
        --recorded <c34 scratch>/result.json --out <scratch>/c34.json \
        [--logger-db <market_log.db> --ledger <c24 scratch>/wf_ledger.csv]

WHAT IS RUN, part A (from the unit's own per-row predictions; arithmetic only)
  1  dDSC / dMCB / dAUC of both arms through the target's rc.Pop.boot (2,000 week-block
     draws, seed 34) against its result.json and the published figures
  1b IS THE ARM INERT: share of rows whose forecast moves, |shift| quantiles, and the same
     statistics on the AFFECTED rows only (post hoc, the attacker's, not registered)
  1c WOULD A SIGNAL BE SEEN: the forecast nudged toward the realised outcome (oracle) and
     toward the close on the affected rows, through the same test
  2  duplication_through on rc.Pop.boot for dDSC and dMCB; alt_blocks by game, player, row
  4  the unit's own interval count; Bonferroni on z = est / SE for the dMCB results
  5  mde_ratio, mde_claim against the MDEs the unit stated
WHAT IS RUN, part B (only with --logger-db; both stores opened mode=ro by the target's code)
  3a as-of of every injury row behind a scored team-game, re-counted here; a planted late row
  3b the fits replicated from the target's functions and compared with its predictions; then
     the planted leak - coefficients fitted on seasons INCLUDING the scored one
  3c scramble: every stat line and injury status of season >= 2024 scrambled, 2023 forecasts
     must not move; the leaky fit must move them
WHAT IS NOT RUN
  the baseline's and c-24's own leakage (the ledger is taken as given); arm 2; P2; whether
  upstream's date_modified is honest (no contemporaneous capture exists).
"""
import argparse
import copy
import csv
import json
import os
import sys
import time
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = {"arm1": {"dDSC": (0.00010, -0.00022, 0.00040), "dMCB": (-0.00075, -0.00151, -0.00002),
                "dAUC": (0.00015, None, None)},
       "arm3": {"dDSC": (0.00002, -0.00017, 0.00019), "dMCB": (-0.00140, -0.00206, -0.00078),
                "dAUC": (-0.00043, None, None)}}
STATED_MDE = {"arm1": 0.00044, "arm3": 0.00026}
PUB_N, PUB_WEEKS, SEED = 9418, 36, 34
FAST = 200


def main():
    ap = argparse.ArgumentParser()
    for f in ("--src", "--rows", "--recorded", "--out"):
        ap.add_argument(f, required=True)
    ap.add_argument("--logger-db")
    ap.add_argument("--ledger")
    ap.add_argument("--part", choices=("all", "a", "b", "blocks"), default="all", help="a = steps 1, 4, 5; blocks = step 2; b = step 3 (the pipeline)")
    a = ap.parse_args()
    if a.logger_db:
        os.environ["LOGGER_DB"] = os.path.abspath(a.logger_db)   # config reads it at import; opens are mode=ro
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from research import decomposed_usage as du
    from research import ranking_calibration as rc
    for m in (du, rc):
        if not os.path.abspath(m.__file__).startswith(os.path.abspath(a.src)):
            raise SystemExit("%s resolved outside --src" % m.__file__)
    out = lambda s="": print(s, flush=True)  # noqa: E731
    recorded = json.load(open(a.recorded, encoding="utf-8"))
    R = {"target": "c-34"}

    raw = [r for r in csv.DictReader(open(a.rows, encoding="utf-8"))
           if r["pop"] == "P1" and r["inj_ok"] == "1" and r["season"] in ("2023", "2024")]
    rows = [dict(season=int(r["season"]), week=int(r["week"]), real_game=r["game"], gsis=r["gsis"], stat=r["stat"],
                 line=float(r["line"]), y=float(r["y"]), m=float(r["baseline"]), k=float(r["market"]),
                 arm1=float(r["arm1"]), arm3=float(r["arm3"])) for r in raw]
    wk = [dict(r, game="%d-%02d" % (r["season"], r["week"])) for r in rows]    # = du.week_rows, plus real_game kept
    assert [w["game"] for w in wk] == [w["game"] for w in du.week_rows([dict(r, game=r["real_game"]) for r in rows])]
    n = len(wk)
    weeks, games, players = len({w["game"] for w in wk}), len({w["real_game"] for w in wk}), len({w["gsis"] for w in wk})
    out("rows %d (published %d)  season-weeks %d (published %d)  games %d  players %d" %
        (n, PUB_N, weeks, PUB_WEEKS, games, players))
    if n != PUB_N or weeks != PUB_WEEKS:
        raise SystemExit("row or week count differs from the published population - refusing")
    y = np.array([w["y"] for w in wk])
    base = np.array([w["m"] for w in wk])
    close = np.array([w["k"] for w in wk])
    FN = {"dDSC": du.dsc, "dMCB": du.mcb, "dAUC": rc.auc}

    def boot(rr, key, stat, draws):
        pop = rc.Pop("x", rr, key, "m")
        r = pop.boot(du._diff(FN[stat], pop.m, pop.k, pop.y), draws=draws, seed=SEED)
        return dict(r, width=r["hi"] - r["lo"], excludes_zero=bool(r["lo"] > 0 or r["hi"] < 0))

    f5 = lambda r: "%+.5f [%+.5f, %+.5f] SE %.6f%s" % (r["est"], r["lo"], r["hi"], r["se"],  # noqa: E731
                                                     " *" if r["excludes_zero"] else "")

    if a.part == "blocks":
        R.update(blocks(L, du, rc, wk, FN, boot, f5, out, (weeks, games, players, n), base, y))
    if a.part == "b":
        R["pipeline"] = part_b(a, L, du, rc, wk, out, boot, f5)
    if a.part in ("b", "blocks"):
        json.dump(R, open(a.out, "w", encoding="utf-8"), indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
        return
    # ---- 1 reproduce --------------------------------------------------------
    out("\n== 1 REPRODUCE - rc.Pop.boot, 2,000 week-block draws, seed 34, on the unit's rows")
    rec = {(t["pop"], t["stat"]): t for t in recorded["tests"]}
    main_r, R["reproduce"] = {}, {}
    for arm in ("arm1", "arm3"):
        for stat in ("dDSC", "dMCB", "dAUC"):
            r = boot(wk, arm, stat, 2000)
            main_r[(arm, stat)] = r
            t = rec[("%s P1 PRIMARY" % arm, stat)]
            exact = all(abs(r[q] - t[q]) < 1e-12 for q in ("est", "lo", "hi", "se"))
            pub = PUB[arm][stat]
            rounds = all(p is None or round(v, 5) == p for v, p in zip((r["est"], r["lo"], r["hi"]), pub))
            R["reproduce"]["%s %s" % (arm, stat)] = dict(r, equals_result_json=exact, rounds_to_published=rounds)
            out("   %s %-5s %s  == result.json %s; rounds to published %s" % (arm, stat, f5(r), exact, rounds))

    # ---- 1b inert? -----------------------------------------------------------
    out("\n== 1b IS THE ARM INERT (post hoc, the attacker's; not registered)")
    R["inert"] = {}
    aff = {}
    for arm in ("arm1", "arm3"):
        p = np.array([w[arm] for w in wk])
        mv = p != base
        aff[arm] = mv
        sh = np.abs(p - base)[mv]
        q = np.percentile(sh, [10, 50, 90, 99])
        big = {c: int((np.abs(p - base) > c).sum()) for c in (0.01, 0.02, 0.05, 0.10)}
        gap_all = du.dsc(close, y) - du.dsc(base, y)
        sub = [w for w, f in zip(wk, mv) if f]
        ys, bs, ks = y[mv], base[mv], close[mv]
        gap_aff = du.dsc(ks, ys) - du.dsc(bs, ys)
        d = {s: boot(sub, arm, s, 600) for s in ("dDSC", "dMCB", "dAUC")}
        ceil_ = base.copy()
        ceil_[mv] = close[mv]
        ceiling = du.dsc(ceil_, y) - du.dsc(base, y)
        R["inert"][arm] = {"moved": int(mv.sum()), "share": float(mv.mean()), "abs_shift_p10_50_90_99": q.tolist(),
                           "mean_abs_shift_all_rows": float(np.abs(p - base).mean()), "rows_shift_gt": big,
                           "gap_all": gap_all, "gap_affected": gap_aff, "affected": d,
                           "dDSC_if_affected_rows_were_the_close": ceiling}
        out("   %s: %d of %d rows move (%.1f%%); |shift| on moved rows p10 %.4f p50 %.4f p90 %.4f p99 %.4f; "
            "mean |shift| over ALL rows %.4f" % (arm, mv.sum(), n, 100 * mv.mean(), q[0], q[1], q[2], q[3],
                                                 np.abs(p - base).mean()))
        out("      rows moved by more than 0.01 / 0.02 / 0.05 / 0.10: %s  (%.1f%% / %.1f%% / %.1f%% / %.1f%% of all)"
            % (list(big.values()), *[100 * v / n for v in big.values()]))
        out("      DSC gap to the close: all rows %.5f; affected rows only %.5f; CEILING - dDSC if the affected "
            "rows were replaced by the close itself: %+.5f (%.0f%% of the all-rows gap)"
            % (gap_all, gap_aff, ceiling, 100 * ceiling / gap_all))
        for s in ("dDSC", "dMCB", "dAUC"):
            out("      AFFECTED ROWS ONLY (n %d, %d weeks, 600 draws) %-5s %s  MDE %.5f%s" % (
                len(sub), d[s]["games"], s, f5(d[s]), 2.8 * d[s]["se"],
                "  = %.0f%% of the affected-row gap" % (100 * 2.8 * d[s]["se"] / gap_aff) if s == "dDSC" else ""))
        out("      stated: MDE %.5f = %.0f%% of the all-rows gap %.5f (re-measured MDE %.5f = %.0f%%)" % (
            STATED_MDE[arm], 100 * STATED_MDE[arm] / gap_all, gap_all, 2.8 * main_r[(arm, "dDSC")]["se"],
            100 * 2.8 * main_r[(arm, "dDSC")]["se"] / gap_all))

    # ---- 1c would a signal be seen --------------------------------------------
    out("\n== 1c PLANTS through the same test (rc.Pop.boot, %d draws): affected rows only are altered" % FAST)
    R["plants"] = {}
    for arm in ("arm1", "arm3"):
        mv = aff[arm]
        p = np.array([w[arm] for w in wk])
        for kind, grid in (("oracle: arm + eps * (y - arm)", (0.01, 0.02, 0.05)),
                           ("toward the close: arm + lam * (close - arm)", (0.25, 0.5, 1.0))):
            for g in grid:
                tgt = y if kind.startswith("oracle") else close
                q = p.copy()
                q[mv] = np.clip(p[mv] + g * (tgt[mv] - p[mv]), rc.CLIP, 1 - rc.CLIP)
                rr = [dict(w, plant=float(v)) for w, v in zip(wk, q)]
                r = boot(rr, "plant", "dDSC", FAST)
                R["plants"]["%s|%s|%s" % (arm, kind, g)] = r
                out("   %s %-44s %-6s dDSC %s -> %s" % (arm, kind, g, f5(r),
                                                       "DETECTED" if r["lo"] > 0 else "not detected"))

    if a.part == "all":
        R.update(blocks(L, du, rc, wk, FN, boot, f5, out, (weeks, games, players, n), base, y))
    else:
        out("")
        out("== 2 BLOCKS: in the --part blocks log")

    # ---- 4 specifications --------------------------------------------------------
    out("\n== 4 SPECIFICATIONS")
    try:
        k = L.registered_count(recorded, "c-34")
    except SystemExit as e:
        k = len(recorded["tests"])
        out("   registered_count() refuses (%s); the unit's result.json carries a `tests` list instead: %d entries" % (e, k))
    nex = sum(1 for t in recorded["tests"] if t["lo"] is not None and (t["lo"] > 0 or t["hi"] < 0))
    out("   pre-registration: 47 intervals (3 primaries + 44); addendum 1 removes arm 2's P1 set -> 36. "
        "result.json: %d computed, %d exclude zero (reported: 36 and 7)" % (k, nex))
    R["specifications"] = {"k": k, "exclude_zero": nex, "bonferroni": {}}
    for arm in ("arm1", "arm3"):
        for stat in ("dMCB", "dDSC"):
            r = main_r[(arm, stat)]
            mu = L.multiplicity(r["est"], r["se"], (2, k, 47), n_blocks=weeks)
            R["specifications"]["bonferroni"]["%s %s" % (arm, stat)] = mu
            out("   %s %s z %+.2f p %.4g  Bonferroni survives over 2 / %d / 47: %s" % (
                arm, stat, mu["z"], mu["p"], k, [mu["bonferroni"][q]["survives_0.05"] for q in (2, k, 47)]))

    # ---- 5 MDE ------------------------------------------------------------------
    out("\n== 5 MDE")
    R["mde"] = {}
    for arm in ("arm1", "arm3"):
        r = main_r[(arm, "dDSC")]
        mr, mc = L.mde_ratio(r["est"], r["se"]), L.mde_claim(STATED_MDE[arm], r["se"])
        R["mde"][arm] = {"ratio": mr, "claim": mc}
        out("   %s dDSC: |est| / MDE %.2f (%s); stated MDE %.5f vs re-measured %.5f -> consistent %s" % (
            arm, mr["ratio"], mr["reading"], mc["stated"], mc["remeasured"], mc["consistent"]))

    if a.logger_db and a.part == "all":
        R["pipeline"] = part_b(a, L, du, rc, wk, out, boot, f5)
    else:
        out("\n== 3 LEAKAGE: NOT RUN in this process (no --logger-db, or --part a: see the --part b log)")
        R["pipeline"] = "NOT RUN"
    json.dump(R, open(a.out, "w", encoding="utf-8"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    out("\nwrote %s" % a.out)


def blocks(L, du, rc, wk, FN, boot, f5, out, counts, base, y):
    weeks, games, players, n = counts
    # ---- 2 blocks --------------------------------------------------------------
    out("\n== 2 BLOCKS - the target's blocks are SEASON-WEEKS (du.week_rows relabels `game`); "
        "units counted here: %d weeks, %d games, %d players, %d rows" % (weeks, games, players, n))
    out("   CORP is REFIT inside every draw: rc.Pop.boot calls fn(idx) and du._diff -> rc.corp -> rc.pav runs on the "
        "resampled rows (read from the code; nothing is held fixed)")
    through, R = {}, {"blocks": {}}
    for arm in ("arm1", "arm3"):
        for stat in ("dDSC", "dMCB"):
            t = L.duplication_through(lambda rr, arm=arm, stat=stat: boot(rr, arm, stat, 120)["width"], wk, "game",
                                      fn_name="research.ranking_calibration.Pop.boot", units=weeks)
            through["%s %s" % (arm, stat)] = t
            out("   %s %s: %s" % (arm, stat, L.through_line(t)))
            p = np.array([w[arm] for w in wk])
            fn = du._diff(FN[stat], p, base, y)
            alt = L.alt_blocks(fn, n, {"game": [w["real_game"] for w in wk],
                                       "player": [w["gsis"] for w in wk],
                                       "row (iid)": list(range(n))}, draws=300, seed=SEED)
            R["blocks"]["%s %s" % (arm, stat)] = {"through": t, "alt": alt}
            for nm, r in alt.items():
                out("      alt %-28s %4d blocks  %s" % (nm, r["n_blocks"], f5(r)))
    R["through_verdicts"] = L.require_through(["%s %s" % (x, s) for x in ("arm1", "arm3") for s in ("dDSC", "dMCB")],
                                              through)

    return R


def part_b(a, L, du, rc, wk, out, boot, f5):
    from research import known_before_kickoff as K
    if not os.path.abspath(K.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("known_before_kickoff resolved outside --src")
    t0 = time.time()
    out("\n== 3 LEAKAGE - the target's own build(), stores mode=ro")
    panel, ctx, _cap, _root = K.build(out)
    P = {}

    def pop_rows(c):
        p1 = du.load_p1_rows(a.ledger)
        K.attach(p1, panel, c, 0, lambda s: None, "P1")
        return [r for r in p1 if r["f"] and r["f"]["inj_ok"]]

    rows = pop_rows(ctx)
    out("   scored rows rebuilt: %d (published %d)" % (len(rows), PUB_N))

    # ---- 3a the as-of of every injury row behind a scored team-game -------------
    def asof_audit(stamped):
        tgs, nrow, late, early, nostamp, lead = set(), 0, 0, 0, 0, []
        for r in rows:
            team = panel.pg[(r["gsis"], r["game"])]["team"]
            s, w, kick = panel.games[r["game"]][:3]
            if (s, w, team) in tgs:
                continue
            tgs.add((s, w, team))
            for _g, _st, _pr, asof in stamped.get((s, w, team), []):
                nrow += 1
                if asof is None:
                    nostamp += 1
                elif asof >= kick:
                    late += 1
                elif asof <= kick - 8 * 86400:
                    early += 1
                else:
                    lead.append((kick - asof) / 3600)
        return {"team_games": len(tgs), "injury_rows": nrow, "stamped_at_or_after_kickoff": late,
                "more_than_8d_early": early, "no_stamp": nostamp,
                "min_lead_h": min(lead) if lead else None, "median_lead_h": float(np.median(lead)) if lead else None,
                "share_lead_under_2h": float(np.mean(np.array(lead) < 2)) if lead else None}
    P["asof"] = asof_audit(ctx.stamped)
    out("   3a as-of, counted here: %s" % P["asof"])
    key = next(k for k in ctx.stamped if k[0] == 2024 and ctx.stamped[k]
               and any(panel.pg.get((r["gsis"], r["game"]), {}).get("team") == k[2] and
                       panel.games[r["game"]][:2] == k[:2] for r in rows[:4000] + rows[-4000:]))
    planted = dict(ctx.stamped)
    g0 = next(g for g, v in panel.games.items() if v[:2] == key[:2] and key[2] in v[3:])
    kick0 = panel.games[g0][2]
    planted[key] = [ctx.stamped[key][0][:3] + (kick0 + 60,)] + list(ctx.stamped[key][1:])
    P["asof_planted"] = asof_audit(planted)
    ok_t, why_t = K.team_report_usable(planted[key], kick0)
    P["asof_plant_target_guard"] = [ok_t, why_t]
    out("   3a PLANT one row of %s stamped kickoff + 60s: my count of late rows %d -> %d; the target's "
        "team_report_usable -> (%s, %r)" % (key, P["asof"]["stamped_at_or_after_kickoff"],
                                            P["asof_planted"]["stamped_at_or_after_kickoff"], ok_t, why_t))

    # ---- 3b the fits, replicated from the target's functions ---------------------
    def fit(c, T, upto):
        """coefficients for test season T from seasons FIRST_TRAIN..upto-1 (honest: upto == T)."""
        f, v = {}, {}
        for stat in K.VOL:
            seasons = set(range(K.FIRST_TRAIN, upto))
            pri = K.role_priors(panel, c, seasons, stat)
            sr = K.stat_rows(panel, c, stat, seasons, pri, True)
            v[stat] = K.training_vmr(sr)
            tr = [(s, yy, e, ff["x"]) for s, _g, _gs, yy, e, ff in sr if ff["inj_ok"]]
            for arm in ("arm1", "arm3"):
                f[(arm, stat)] = K.fit_arm(tr, K.ARMS[arm], upto)      # the guard is told `upto`, so it passes
        return f, v

    def predict(rr, f, v, arm):
        o = {}
        for stat in K.VOL:
            sub = [r for r in rr if r["stat"] == stat]
            m = np.array([K.multiplier(f[(arm, stat)]["beta"], r["f"]["x"]) if r["f"].get("modelled") else 1.0
                          for r in sub])
            p = K.apply_multiplier([r["m"] for r in sub], m, max(v[stat], K.VMR_FLOOR), [r["line"] for r in sub])
            for r, pp in zip(sub, p):
                o[(r["game"], r["gsis"], r["stat"], r["line"])] = float(pp)
        return o

    csvp = {arm: {(w["real_game"], w["gsis"], w["stat"], w["line"]): w[arm] for w in wk} for arm in ("arm1", "arm3")}
    honest, leaky = {"arm1": {}, "arm3": {}}, {"arm1": {}, "arm3": {}}
    for T in (2023, 2024):
        rr = [r for r in rows if r["season"] == T]
        fh, vh = fit(ctx, T, T)
        fl, vl = fit(ctx, T, T + 1)
        for arm in ("arm1", "arm3"):
            honest[arm].update(predict(rr, fh, vh, arm))
            leaky[arm].update(predict(rr, fl, vl, arm))
    P["replication"], P["leak_plant"] = {}, {}
    for arm in ("arm1", "arm3"):
        d = np.array([abs(honest[arm][k] - csvp[arm][k]) for k in csvp[arm]])
        P["replication"][arm] = {"rows": len(d), "max_abs_diff": float(d.max()), "rows_differing_1e-9": int((d > 1e-9).sum())}
        out("   3b %s re-fitted today vs the unit's predictions.csv: %d rows, max |diff| %.2e, %d differ by > 1e-9"
            % (arm, len(d), d.max(), (d > 1e-9).sum()))
        rr = [dict(w, leak=leaky[arm][(w["real_game"], w["gsis"], w["stat"], w["line"])]) for w in wk]
        moved = sum(1 for w in rr if abs(w["leak"] - w[arm]) > 1e-9)
        P["leak_plant"][arm] = {"rows_moved_vs_honest": moved, **{s: boot(rr, "leak", s, FAST) for s in ("dDSC", "dMCB")}}
        out("   3b PLANTED LEAK %s - coefficients, role priors and dispersion fitted on seasons THROUGH the scored one: "
            "%d forecasts move; dDSC %s; dMCB %s" % (arm, moved, f5(P["leak_plant"][arm]["dDSC"]),
                                                     f5(P["leak_plant"][arm]["dMCB"])))

    # ---- 3c scramble season >= 2024, score 2023 ----------------------------------
    rng = np.random.default_rng(31)
    late = [r for (gs, g), r in panel.pg.items() if panel.games[g][0] >= 2024]
    for c in ("rec", "car", "tgt"):
        vals = rng.permutation([r[c] for r in late])
        for r, x in zip(late, vals):
            r[c] = int(x)                                     # the row dicts are shared with panel.player
    for (g, _tm), t in panel.tg.items():
        if panel.games[g][0] >= 2024:
            t["tgt"], t["car"] = int(t["tgt"] * rng.uniform(0.5, 1.5)) + 1, int(t["car"] * rng.uniform(0.5, 1.5)) + 1
    st2 = dict(ctx.stamped)
    keys24 = [k for k in st2 if k[0] >= 2024]
    pool = [x[1:3] for k in keys24 for x in st2[k]]
    perm = rng.permutation(len(pool))
    i = 0
    for k in keys24:
        new = []
        for x in st2[k]:
            new.append((x[0],) + tuple(pool[perm[i]]) + (x[3],))
            i += 1
        st2[k] = new
    ctx2 = K.Context(panel, st2, ctx.versioned, ctx.lab, ctx.dts)
    rows2 = [r for r in pop_rows(ctx2) if r["season"] == 2023]
    fh2, vh2 = fit(ctx2, 2023, 2023)
    fl2, vl2 = fit(ctx2, 2023, 2025)
    P["scramble"] = {"scrambled_player_games": len(late), "scrambled_team_weeks": len(keys24)}
    for arm in ("arm1", "arm3"):
        h2, l2 = predict(rows2, fh2, vh2, arm), predict(rows2, fl2, vl2, arm)
        mh = sum(1 for k, v in h2.items() if abs(v - honest[arm][k]) > 1e-12)
        ml = sum(1 for k, v in l2.items() if abs(v - honest[arm][k]) > 1e-12)
        P["scramble"][arm] = {"rows_2023": len(h2), "honest_moved": mh, "leaky_moved": ml}
        out("   3c SCRAMBLE season >= 2024 (%d player-games, %d team-week reports): %s 2023 forecasts moved under the "
            "target's fit %d of %d; under the planted leaky fit (trained through 2024) %d -> %s" % (
                len(late), len(keys24), arm, mh, len(h2), ml,
                "clean, and the audit can fire" if mh == 0 and ml > 0 else "FAILS" if mh else "BLIND (plant did not fire)"))
    P["seconds"] = time.time() - t0
    return P


if __name__ == "__main__":
    main()
