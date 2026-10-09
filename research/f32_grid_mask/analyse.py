"""f-32: read the walks mask_audit.py wrote and apply the pre-registered rule.

    python research/f32_grid_mask/analyse.py --src D:/temp/f32/c-39src \
        --db D:/calibrated-sports/data/cfb.db --work D:/temp/f32 \
        --json-out research/f32_grid_mask/results/f32_mask_audit.json

READ-ONLY (cfb.db mode=ro). Refuses, rather than printing a verdict, when a file
it needs is missing or the instrumented walk does not equal c-39's own sums.
"""
import argparse
import json
import os
import sqlite3
import sys

import numpy as np

NEVER = 9999
WIDTH = {"home": 0.0061, "record": 0.0054, "elo_nomov": 0.0015}      # PREREGISTRATION.md
PUBLISHED = {"home": -0.0615, "record": -0.0380, "elo_nomov": -0.0042}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args()
    jout = os.path.abspath(a.json_out)
    sys.path.insert(0, os.path.abspath(a.src))
    os.chdir(a.src)
    from models import cfb_game as C
    from research import cfb_game_forecast as CF
    W = lambda n: os.path.join(a.work, n)  # noqa: E731
    out = lambda s="": print(s, flush=True)  # noqa: E731
    J = lambda n: json.load(open(W(n), encoding="utf-8"))  # noqa: E731
    R = {}

    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games = CF.load(con)
    signs = CF.record_signs(con)
    con.close()
    gm, gp = CF.grid(CF.GRID_MOV), CF.grid(CF.GRID_PLAIN)
    P = len(gm)
    a_none = np.array([p.a is None for p in gm])
    scored = list(range(CF.SCORE_FROM, CF.SCORE_TO + 1))
    years = scored + [CF.CURRENT]
    recorded = json.load(open(os.path.join(a.src, "research", "results", "cfb_game_forecast.json"), encoding="utf-8"))
    rec_fit = {int(k): v for k, v in recorded["fits"].items()}

    inst = np.load(W("inst.npz"))
    seasons = [int(s) for s in inst["seasons"]]
    sums, counts, fb = inst["sums"], inst["counts"], inst["first_bad"]
    ever = fb != NEVER
    if sums.shape != (len(seasons), P):
        raise SystemExit("inst.npz has shape %r, expected (%d, %d)" % (sums.shape, len(seasons), P))

    # ------------------------------------------------- 0 is the instrumented walk the thing
    out("== 0. THE INSTRUMENTED WALK AGAINST c-39's OWN grid_fit")
    R["equivalence"] = {}
    for name in ("base_sums.npz", "base_chunk_sums.npz"):
        if not os.path.exists(W(name)):
            R["equivalence"][name] = "MISSING"
            out("   %-22s MISSING" % name)
            continue
        b = np.load(W(name))
        nan_same = bool(np.array_equal(np.isnan(b["sums"][0]), ever))
        ok = ~ever
        d = float(np.abs(b["sums"][:, ok] - sums[:, ok]).max())
        cnt = bool(np.array_equal(b["counts"], counts)) and [int(s) for s in b["seasons"]] == seasons
        R["equivalence"][name] = {"nan_mask_equals_ever_bad": nan_same, "max_abs_sum_diff": d, "counts_seasons_equal": cnt,
                                  "points_compared": int(ok.sum()), "bad_points": int(np.isnan(b["sums"][0]).sum())}
        out("   %-22s nan mask == instrumented ever-bad: %s; max |sum diff| on %d kept points x %d seasons: %.2e; "
            "seasons and counts equal: %s" % (name, nan_same, int(ok.sum()), len(seasons), d, cnt))
        if not (nan_same and cnt and d < 1e-6):
            raise SystemExit("the instrumented walk is not grid_fit - nothing below can be read")
    if R["equivalence"].get("base_sums.npz") == "MISSING" and R["equivalence"].get("base_chunk_sums.npz") == "MISSING":
        raise SystemExit("no genuine grid_fit reference on disk")
    R["unchunked_reference_present"] = R["equivalence"].get("base_sums.npz") != "MISSING"

    def choose(year, mask_bad, sub=None):
        """C.best_params on the instrumented sums with `mask_bad` points made nan."""
        s = sums.copy()
        s[:, mask_bad] = np.nan
        if sub is not None:
            s[:, ~sub] = np.nan
        return C.best_params(gm, seasons, s, counts, CF.FIT_FROM, year)

    # ------------------------------------------------- 1 the mask, all seasons
    out("\n== 1. THE MASK, SEASON BY SEASON (grid %d points; %d ever bad over the whole walk)" % (P, int(ever.sum())))
    out("   season  bad as-of  removed by the future  share of grid  share of as-of set   of which in season T itself")
    R["mask"] = {}
    for T in years:
        asof = fb < T
        fut = ever & ~asof
        own = fb == T
        R["mask"][T] = {"bad_as_of": int(asof.sum()), "removed_by_future": int(fut.sum()),
                        "share_of_grid": float(fut.sum() / P), "share_of_as_of_set": float(fut.sum() / (P - asof.sum())),
                        "removed_by_own_season": int(own.sum()), "scored": T in scored}
        out("   %d %10d %12d %19.4f %14.4f %20d%s"
            % (T, asof.sum(), fut.sum(), fut.sum() / P, fut.sum() / (P - asof.sum()), own.sum(),
               "" if T in scored else "   (current-season fit, not scored)"))
    R["mask_first_bad_by_season"] = {int(s): int((fb == s).sum()) for s in seasons}
    R["a_none_ever_bad"] = int((ever & a_none).sum())
    out("   points newly bad per season: %s" % R["mask_first_bad_by_season"])
    out("   a = None points ever bad: %d (the mask cannot touch them: %s)" % (R["a_none_ever_bad"], R["a_none_ever_bad"] == 0))

    # ------------------------------------------------- 2 truncated refits
    out("\n== 2. TRUNCATED REFITS: as-of choice vs the walk's, every season")
    out("   season  walk == recorded  as-of(inst) == walk  genuine truncated C.grid_fit   closest future-removed point")
    R["refit"] = {}
    differ, genuine_done, genuine_missing, inst_vs_genuine_bad = [], [], [], []
    sub = ~a_none
    for T in years:
        pw, lw, n = choose(T, ever)
        pa, la, _ = choose(T, fb < T)
        fut = ever & ~(fb < T)
        m = np.array([CF.FIT_FROM <= s < T for s in seasons])
        mean = sums[m].sum(axis=0) / n
        gap = float(mean[fut].min() - lw) if fut.any() else None      # > 0: every removed point is worse than the choice
        rec_same = pw.as_dict() == rec_fit[T]["mov"]
        row = {"walk": pw.as_dict(), "as_of": pa.as_dict(), "walk_equals_recorded": rec_same, "as_of_equals_walk": pa == pw,
               "fit_games": n, "chosen_mean_log_loss": lw, "best_future_removed_minus_chosen": gap}
        g = "not run / not finished"
        if os.path.exists(W("trunc_%d.json" % T)):
            t = J("trunc_%d.json" % T)
            tb = np.unpackbits(np.load(W("trunc_bad_%d.npy" % T)))[:P].astype(bool)
            tf = np.load(W("trunc_fitsum_%d.npy" % T))
            keep = ~tb
            same_mask = bool(np.array_equal(tb, fb < T))
            dsum = float(np.abs(tf[keep] - sums[m].sum(axis=0)[keep]).max())
            row["genuine"] = {"mov": t["mov"], "equals_walk": t["mov"] == pw.as_dict(), "equals_inst_as_of": t["mov"] == pa.as_dict(),
                              "plain": t["plain"], "plain_equals_recorded": t["plain"] == rec_fit[T]["nomov"],
                              "bad_as_of": t["bad_as_of"], "bad_mask_equals_inst": same_mask, "max_abs_fitsum_diff": dsum,
                              "plain_nan_points": t["plain_nan_points"], "games": t["games"]}
            genuine_done.append(T)
            if not (same_mask and dsum < 1e-6 and row["genuine"]["equals_inst_as_of"]):
                inst_vs_genuine_bad.append(T)
            if not row["genuine"]["equals_walk"] or not row["genuine"]["plain_equals_recorded"]:
                differ.append(T)
            g = "MOV %s, plain %s (mask==inst %s, |dsum| %.1e)" % (
                "SAME" if row["genuine"]["equals_walk"] else "DIFFERS", "SAME" if row["genuine"]["plain_equals_recorded"] else "DIFFERS",
                same_mask, dsum)
        else:
            genuine_missing.append(T)
        if pa != pw and T not in differ:
            differ.append(T)
        R["refit"][T] = row
        out("   %d %12s %18s      %-52s %s" % (T, rec_same, "SAME" if pa == pw else "DIFFERS", g,
                                               "n/a" if gap is None else "%+.5f log loss" % gap))
    R["refit_summary"] = {"seasons_where_constants_differ": sorted(differ), "genuine_truncated_done": genuine_done,
                          "genuine_truncated_missing": genuine_missing, "inst_disagrees_with_genuine": inst_vs_genuine_bad,
                          "walk_differs_from_recorded": [T for T in years if not R["refit"][T]["walk_equals_recorded"]]}
    out("   constants differ in: %s; genuine truncated refits done for %d of %d seasons (missing %s); instrumented "
        "disagrees with genuine in %s" % (sorted(differ), len(genuine_done), len(years), genuine_missing, inst_vs_genuine_bad))
    if inst_vs_genuine_bad:
        raise SystemExit("instrumented as-of choice disagrees with a genuine truncated refit - stop")

    # the same question on the declared sub-grid (registered grid without a = None): CAN the mask change a choice
    out("\n   the declared sub-grid (a != None, %d points): does the mask change a choice there" % int(sub.sum()))
    R["subgrid"] = {}
    sub_differ = []
    for T in years:
        pw, lw, _ = choose(T, ever, sub)
        pa, la, _ = choose(T, fb < T, sub)
        j = gm.index(pa)
        R["subgrid"][T] = {"walk": pw.as_dict(), "as_of": pa.as_dict(), "same": pa == pw, "as_of_choice_first_bad_season":
                           None if fb[j] == NEVER else int(fb[j]), "log_loss_walk": lw, "log_loss_as_of": la}
        if pa != pw:
            sub_differ.append(T)
            out("      %d DIFFERS: as-of %s (goes bad in %d, fit log loss %.5f) vs walk %s (%.5f)"
                % (T, pa.as_dict(), fb[j], la, pw.as_dict(), lw))
    R["subgrid_seasons_differ"] = sub_differ
    out("      seasons where the mask changes the sub-grid choice: %d of %d %s" % (len(sub_differ), len(years), sub_differ))

    # ------------------------------------------------- 3 scramble and plants
    out("\n== 3. FULL-PIPELINE SCRAMBLE (genuine grid_fit + best_params + scalar walk)")
    ts = {g["game_id"]: g["start_ts"] for g in games}
    season_of = {g["game_id"]: g["season"] for g in games}
    R["scramble"] = {}

    def compare(base, other, label):
        c = base["cutoff_ts"]
        if other["cutoff_ts"] != c:
            raise SystemExit("cutoffs differ")
        cs = max(season_of[g] for g in base["p"] if ts[g] < c)
        pre = [g for g in base["p"] if ts[g] < c]
        post = [g for g in base["p"] if ts[g] >= c]
        missing = [g for g in pre if g not in other["p"]]
        mv = sum(1 for g in pre if g in other["p"] and abs(base["p"][g] - other["p"][g]) > 1e-12)
        mvp = sum(1 for g in post if g in other["p"] and abs(base["p"][g] - other["p"][g]) > 1e-12)
        fits_pre = [y for y in base["fits"] if int(y) <= cs and base["fits"][y] != other["fits"].get(y)]
        fits_post = [y for y in base["fits"] if int(y) > cs and base["fits"][y] != other["fits"].get(y)]
        r = {"cutoff_ts": c, "cutoff_season": cs, "pre_cutoff_checked": len(pre), "pre_cutoff_moved": mv,
             "pre_cutoff_missing": len(missing), "post_cutoff_checked": len(post), "post_cutoff_moved": mvp,
             "fits_changed_at_or_before_cutoff_season": fits_pre, "fits_changed_after": fits_post,
             "bad_full_base": base["bad_full"], "bad_full_other": other["bad_full"],
             "scrambled_games": other.get("scrambled_games"), "errors": other["errors"], "base_errors": base["errors"]}
        R["scramble"][label] = r
        out("   %-30s cutoff ts %d (season %d): %d pre-cutoff forecasts checked, %d MOVED (%d missing); fits changed for "
            "seasons <= %d: %s; after: %d seasons; post-cutoff forecasts moved %d of %d; `bad` points %d -> %d; errors %s"
            % (label, c, cs, len(pre), mv, len(missing), cs, fits_pre, len(fits_post), mvp, len(post),
               base["bad_full"], other["bad_full"], other["errors"] or "none"))
        return r

    need = [n for n in ("scr.json", "plant_base.json", "plant_scr.json", "scr_sub.json") if not os.path.exists(W(n))]
    base_name = "base.json" if os.path.exists(W("base.json")) else "base_chunk.json"
    R["scramble_base_file"] = base_name
    R["scramble_missing"] = need
    base = J(base_name)
    if set(base["p"]) != {games[i]["game_id"] for i in range(len(games)) if C.completed(games[i])
                           and CF.SCORE_FROM <= games[i]["season"] <= CF.SCORE_TO}:
        raise SystemExit("base forecasts do not cover Part 1")
    if "scr.json" not in need:
        compare(base, J("scr.json"), "registered grid, scrambled")
    if not {"plant_base.json", "plant_scr.json"} & set(need):
        r = compare(J("plant_base.json"), J("plant_scr.json"), "P1 misdated game, scrambled")
        r["fires"] = r["pre_cutoff_moved"] > 0
        out("      P1 (rating channel): %s" % ("FIRES" if r["fires"] else "DID NOT FIRE - detector blind"))
    if "scr_sub.json" not in need:
        # the sub-grid's unscrambled pipeline: CF.Walk's own params()/p() on the genuine masked sums, sub-grid columns
        ref = np.load(W("base_sums.npz" if os.path.exists(W("base_sums.npz")) else "base_chunk_sums.npz"))
        w = CF.Walk.__new__(CF.Walk)
        gs = [p for p in gm if p.a is not None]
        w.games, w.plist, w.mov = games, gs, True
        w.seasons, w.sums, w.counts = [int(s) for s in ref["seasons"]], ref["sums"][:, sub], ref["counts"]
        w._pre, w.fits = {}, {}
        sb = {"cutoff_ts": base["cutoff_ts"], "fits": {str(y): w.params(y).as_dict() for y in years}, "errors": {},
              "bad_full": int(np.isnan(w.sums[0]).sum()),
              "p": {games[i]["game_id"]: w.p(i) for i in range(len(games)) if games[i]["game_id"] in base["p"]}}
        r = compare(sb, J("scr_sub.json"), "P2 sub-grid (a != None), scrambled")
        r["fires"] = r["pre_cutoff_moved"] > 0
        out("      P2 (mask channel, declared sub-grid): %s"
            % ("FIRES - a future-only change moved %d earlier forecasts through the mask" % r["pre_cutoff_moved"]
               if r["fires"] else "DID NOT FIRE on this scramble"))

    # ------------------------------------------------- 4 the figures
    out("\n== 4. THE PUBLISHED FIGURES UNDER AS-OF FITS")
    asof_fits = {T: C.CfbParams(**R["refit"][T]["as_of"]) for T in scored}
    walk_fits = {T: C.CfbParams(**R["refit"][T]["walk"]) for T in scored}
    R["figures"] = {"threshold": WIDTH, "binding_threshold": 0.0015}
    if asof_fits == walk_fits:
        R["figures"]["moves"] = {b: 0.0 for b in WIDTH}
        R["figures"]["note"] = "as-of fits equal the walk's in every scored season: the forecasts are the same objects"
        out("   as-of constants equal the walk's in all %d scored seasons -> every forecast is unchanged; move 0.0000 in "
            "each arm (thresholds %s; binding 0.0015: NOT reached)" % (len(scored), WIDTH))
    else:
        pop = [i for i, g in enumerate(games) if C.completed(g) and CF.SCORE_FROM <= g["season"] <= CF.SCORE_TO]
        y = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop])
        gpw = CF.Walk(games, gp, mov=False)
        e0 = np.array([gpw.p(i) for i in pop])
        consts = {yv: CF.baseline_constants(games, signs, yv) for yv in scored}
        hm, rec = [], []
        for i in pop:
            g = games[i]
            h0, h1, q = consts[g["season"]]
            h = h1 if g["neutral"] else h0
            s = signs.get(g["game_id"], 0)
            hm.append(h)
            rec.append(h if s == 0 else (q if s > 0 else 1.0 - q))
        Bz = {"home": np.array(hm), "record": np.array(rec), "elo_nomov": e0}

        def forecasts(fits):
            cache, v, sub_used = {}, [], []
            for i in pop:
                p = fits[games[i]["season"]]
                if p not in cache:
                    try:
                        cache[p] = dict(C.run(games, p, mov=True)[0])
                    except ValueError:
                        cache[p] = None
                        sub_used.append(games[i]["season"])
                v.append(np.nan if cache[p] is None else cache[p][i])
            return np.array(v), sorted(set(sub_used))
        mw, _ = forecasts(walk_fits)
        ma, refused = forecasts(asof_fits)
        R["figures"]["scalar_walk_refuses_as_of_fit_in"] = refused
        ok = ~np.isnan(ma)
        R["figures"]["moves"], R["figures"]["n_compared"] = {}, int(ok.sum())
        for b, k in Bz.items():
            dw = float((((mw - y) ** 2) - ((k - y) ** 2))[ok].mean())
            da = float((((ma - y) ** 2) - ((k - y) ** 2))[ok].mean())
            R["figures"]["moves"][b] = da - dw
            out("   %-10s dBrier walk %+.5f, as-of %+.5f, move %+.5f (threshold %.4f) on %d games; scalar walk refused "
                "the as-of fit in seasons %s" % (b, dw, da, da - dw, WIDTH[b], int(ok.sum()), refused))
    mv = R["figures"]["moves"]
    material = any(abs(mv[b]) > WIDTH[b] for b in WIDTH)
    visible = any(abs(mv[b]) >= 0.00005 for b in WIDTH)

    # ------------------------------------------------- verdict, by the pre-registered rule
    covered = sorted(set(genuine_done) & set(scored))
    sc = R["scramble"].get("registered grid, scrambled")
    p1 = R["scramble"].get("P1 misdated game, scrambled", {}).get("fires")
    p2 = R["scramble"].get("P2 sub-grid (a != None), scrambled", {}).get("fires")
    changed = bool(R["refit_summary"]["seasons_where_constants_differ"])
    if changed:
        verdict = "(b) material" if material else "(c) material but bounded"
    elif sc is not None and sc["pre_cutoff_moved"] == 0 and not sc["fits_changed_at_or_before_cutoff_season"] and p1:
        verdict = "(a) harmless"
    else:
        verdict = "(a) harmless ON THE TRUNCATED REFITS ALONE - the scramble cannot support it (missing, moved or plant blind)"
    R["verdict"] = {"verdict": verdict, "constants_changed": changed, "material": material, "visible_at_4dp": visible,
                    "scored_seasons_with_genuine_truncated_refit": covered,
                    "scored_seasons_instrumented_only": [T for T in scored if T not in covered],
                    "scramble_pre_cutoff_moved": None if sc is None else sc["pre_cutoff_moved"],
                    "plant_p1_fires": p1, "plant_p2_fires": p2,
                    "mask_can_change_a_choice_on_subgrid": bool(sub_differ), "published": PUBLISHED}
    out("\n== VERDICT (pre-registered rule): %s" % verdict)
    out("   scored seasons with a genuine truncated refit: %d of %d; instrumented-only: %s"
        % (len(covered), len(scored), R["verdict"]["scored_seasons_instrumented_only"]))
    out("   independent of the verdict - can the mask change a choice at all: on the declared sub-grid, %s"
        % ("YES, in %d seasons" % len(sub_differ) if sub_differ else "not on this store"))
    os.makedirs(os.path.dirname(jout), exist_ok=True)
    with open(jout, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s" % jout)


if __name__ == "__main__":
    main()
