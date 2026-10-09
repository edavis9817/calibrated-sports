"""f-31 target adapter: c-38's cross-sport backtest.

    CLAIM  the margin-of-victory Elo game model's advantage over home / better
           record / plain Elo, against settlement, has the same sign and ordering
           in the NFL (2001-2025, 6,743 games) and college FBS-FBS (2005-2025,
           15,508 games): 16 of 16 primary tests Holm-significant; college minus
           NFL dBrier -0.0359 / -0.0217 / -0.0015; re-weighted to the NFL's
           favourite-probability bins -0.0028 [-0.0071, +0.0012] / -0.0073 / -0.0032.

READ-ONLY. Run as a script against a detached worktree of origin/c-38-cross-sport:

    python research/f26_reliability/attack_c38.py --src <worktree> --logger-db <market_log.db> \
        --cache <scratch>/rows.pkl --scratch <scratch> --out <scratch>/c38.json

WHAT IS RUN
  1  the target's loaders (c-28's and c-39's walks, both stores mode=ro through the
     target's own connect functions) ONCE, cached; then the target's own main() over
     those rows, and every numeric leaf of its JSON compared with the committed one
  2  duplication_through on the target's `boot` + `interval` (within-sport d, the
     between-sport D, the matched Dm via `matched`); weights fixed vs resampled;
     alt_blocks by season and season-week for D and Dm
  3  ONLY the transfer arms: scores at/after a cutoff scrambled, the four carried
     scalar walks re-run, no earlier forecast may move; a planted misdated game
  4  the registered count from the result file; Bonferroni over 16 and over all;
     which of the 16 could have failed given what c-28 / c-39 had published;
     an unregistered re-binning of the matched comparison
  5  mde_ratio and mde_claim against the MDEs the pre-registration stated
WHAT IS NOT RUN
  the walk-forward fits' own leakage (c-28's and c-39's - attacked by f-26 / f-29),
  any truncated refit, and the price arms beyond the JSON diff.
"""
import argparse
import copy
import json
import math
import os
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUB_D = {"home": -0.0359, "record": -0.0217, "elo_nomov": -0.0015}
PUB_DM = {"home": (-0.0028, -0.0071, 0.0012), "record": (-0.0073, -0.0115, -0.0029),
          "elo_nomov": (-0.0032, -0.0046, -0.0018)}
STATED_MDE_D = {"home": 0.0065, "record": 0.0055, "elo_nomov": 0.0016}      # the pre-registration's
STATED_MDE_DM = {"home": 0.0061}                                            # findings doc, matched home
THROUGH_DRAWS = 300


def leaves(a, b, path=""):
    """(compared, [(path, a, b) differing]) over numeric leaves present in both."""
    n, bad = 0, []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k in ("log", "versions") or k not in b:
                continue
            m, x = leaves(a[k], b[k], path + "/" + str(k))
            n, bad = n + m, bad + x
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (u, v) in enumerate(zip(a, b)):
            m, x = leaves(u, v, path + "/%d" % i)
            n, bad = n + m, bad + x
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        n = 1
        if not (a == b or abs(a - b) <= 1e-9 * max(1.0, abs(a))):
            bad = [(path, a, b)]
    return n, bad


def main():
    ap = argparse.ArgumentParser()
    for f in ("--src", "--logger-db", "--cache", "--scratch", "--out"):
        ap.add_argument(f, required=True)
    a = ap.parse_args()
    os.environ["LOGGER_DB"] = os.path.abspath(a.logger_db)      # config reads it at import; both opens are mode=ro
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    os.chdir(a.src)
    from models import cfb_game as C
    from research import cross_sport as X
    if not os.path.abspath(X.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.cross_sport resolved outside --src: %s" % X.__file__)
    recorded = json.load(open(os.path.join(a.src, "research", "results", "cross_sport.json"), encoding="utf-8"))
    K_ALL = L.registered_count({"registered_intervals": {"count": recorded.get("counts", {}).get("registered_intervals")}},
                               "c-38")
    K_PRIM = recorded["primary"]["holm_family_size"]
    R = {"target": "c-38", "src": a.src}
    out = lambda s="": print(s, flush=True)  # noqa: E731
    t0 = time.time()

    # ------------------------------------------------------------ load once (the pipeline), cache
    if os.path.exists(a.cache):
        Z = pickle.load(open(a.cache, "rb"))
        out("rows from cache %s (pipeline NOT re-run in this invocation)" % a.cache)
    else:
        runs, real_run = [], C.run

        def spy(games, params, **kw):                    # the four carried scalar walks, for step 3
            runs.append((games, params, kw))
            return real_run(games, params, **kw)
        C.run = spy
        quiet = lambda s="": None  # noqa: E731
        nr, nm = X.load_nfl(quiet)
        nfl_runs = list(runs)
        if len(nfl_runs) != 2:
            raise SystemExit("expected the NFL loader to make 2 carried walks, saw %d" % len(nfl_runs))
        t1 = time.time()
        cr, cc, cm = X.load_cfb(quiet)
        C.run = real_run
        carried = [r for r in runs[2:] if r[2].get("hfa_on_neutral")]     # arm N / N0; c-39's Walk calls C.run lazily too
        if len(carried) != 2:
            raise SystemExit("expected 2 carried walks over college, saw %d" % len(carried))
        Z = {"nfl": (nr, nm), "cfb": (cr, cc, cm), "runs": nfl_runs + carried, "n_runs": len(runs),
             "sec": (t1 - t0, time.time() - t1)}
        pickle.dump(Z, open(a.cache, "wb"))
        out("pipeline RE-RUN: NFL loader %.0fs, college loader %.0fs; %d scalar C.run calls seen" % (*Z["sec"], len(runs)))
    nfl_rows, cfb_rows = Z["nfl"][0], Z["cfb"][0]
    if [bool(kw.get("hfa_on_neutral")) for _g, _p, kw in Z["runs"]] != [False, False, True, True]:
        raise SystemExit("the cached walks are not arm R, R0, N, N0")

    # ------------------------------------------------------------ 1 reproduce
    out("\n== 1. REPRODUCE: the target's own main() over the re-derived rows, against its committed JSON")
    X.load_nfl, X.load_cfb = (lambda o: Z["nfl"]), (lambda o: Z["cfb"])
    mine_path = os.path.join(a.scratch, "cross_sport.rerun.json")
    real_stdout, sys.stdout = sys.stdout, open(os.path.join(a.scratch, "cross_sport.rerun.stdout"), "w", encoding="utf-8")
    try:
        rc_main = X.main(["--json-out", mine_path, "--log-out", os.path.join(a.scratch, "cross_sport.rerun.log")])
    finally:
        sys.stdout.close()
        sys.stdout = real_stdout
    mine = json.load(open(mine_path, encoding="utf-8"))
    n_leaf, bad = leaves(recorded, mine)
    R["reproduce"] = {"main_rc": rc_main, "numeric_leaves_compared": n_leaf, "differing": len(bad), "first": bad[:12],
                      "items": []}
    out("   main() rc %s; %d numeric leaves compared with research/results/cross_sport.json, %d differ"
        % (rc_main, n_leaf, len(bad)))
    for p, u, v in bad[:12]:
        out("      differs %s: committed %r, re-run %r" % (p, u, v))
    if n_leaf < 500:
        raise SystemExit("only %d leaves compared - the diff walked nothing" % n_leaf)
    for b in X.BASES:
        r = mine["primary"]["between"]["D_" + b]
        R["reproduce"]["items"].append(L.reproduce("D_%s college - NFL" % b, r["est"], PUB_D[b], 4))
        m = mine["matched"]["tests"][b]
        R["reproduce"]["items"].append(L.reproduce("Dm_%s est" % b, m["est"], PUB_DM[b][0], 4))
        R["reproduce"]["items"].append(L.reproduce("Dm_%s lo" % b, m["lo"], PUB_DM[b][1], 4))
        R["reproduce"]["items"].append(L.reproduce("Dm_%s hi" % b, m["hi"], PUB_DM[b][2], 4))
    R["reproduce"]["items"].append(L.reproduce("Holm-significant of 16", mine["primary"]["holm_significant"], 16, 0))
    R["reproduce"]["items"].append(L.reproduce("n NFL", len(nfl_rows), 6743, 0))
    R["reproduce"]["items"].append(L.reproduce("n college", len(cfb_rows), 15508, 0))
    for r in R["reproduce"]["items"]:
        out("   %-26s measured %+.6f  published %+.4f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))

    # ------------------------------------------------------------ 2 blocks
    out("\n== 2. BLOCKS: THROUGH the target's boot() + interval() (+ matched() for Dm), %d draws" % THROUGH_DRAWS)
    guard = {"fired": 0}

    def tab_of(rows):
        try:
            return X.Tab(rows, X.COLS)
        except ValueError:                               # Tab refuses a game that appears twice
            guard["fired"] += 1
            return X.Tab([dict(r, game="%s~%d" % (r["game"], i)) for i, r in enumerate(rows)], X.COLS)

    def w_within(b):
        def w(rows):
            t = tab_of(rows)
            est, dr = X.boot(t, X.SEED_OWN, THROUGH_DRAWS)
            r = X.interval(est["d_" + b], dr["d_" + b], t.n)
            return r["hi"] - r["lo"]
        return w

    def w_between(b, matched):
        def w(rows):
            tn, tc = tab_of([r for r in rows if r["sport"] == "nfl"]), tab_of([r for r in rows if r["sport"] == "cfb"])
            en, dn = X.boot(tn, X.SEED_NFL, THROUGH_DRAWS, bins=True)
            ec, dc = X.boot(tc, X.SEED_CFB, THROUGH_DRAWS, bins=True)
            if matched:
                e, v = X.matched(en, dn, ec, dc, b)
            else:
                e, v = ec["d_" + b] - en["d_" + b], dc["d_" + b] - dn["d_" + b]
            r = X.interval(e, v, min(tn.n, tc.n))
            return r["hi"] - r["lo"]
        return w
    both = [dict(r, sport="nfl", game="nfl:%s" % r["game"]) for r in nfl_rows] + \
           [dict(r, sport="cfb", game="cfb:%s" % r["game"]) for r in cfb_rows]
    units_both = len({r["game"] for r in both})
    R["through"] = {}
    jobs = [("within cfb d_elo_nomov", w_within("elo_nomov"), cfb_rows, "cross_sport.boot+interval"),
            ("within nfl d_elo_nomov", w_within("elo_nomov"), nfl_rows, "cross_sport.boot+interval"),
            ("between D_elo_nomov", w_between("elo_nomov", False), both, "cross_sport.boot x2 (seeds 3801/3802)+interval"),
            ("between D_home", w_between("home", False), both, "cross_sport.boot x2 (seeds 3801/3802)+interval"),
            ("matched Dm_home", w_between("home", True), both, "cross_sport.boot x2 (bins)+matched+interval"),
            ("matched Dm_elo_nomov", w_between("elo_nomov", True), both, "cross_sport.boot x2 (bins)+matched+interval")]
    for name, w, rows, fn in jobs:
        g0 = guard["fired"]
        t = L.duplication_through(w, rows, "game", fn_name=fn, units=len({r["game"] for r in rows}))
        t["dup_guard_fired"] = guard["fired"] - g0
        R["through"][name] = t
        out("   %-24s %s" % (name, L.through_line(t)))
        out("   %-24s Tab's 'a game appears twice' guard fired %d time(s) on the in-block copies and was bypassed"
            % ("", t["dup_guard_fired"]))
    R["through_verdicts"] = L.require_through([j[0] for j in jobs], R["through"])
    out("   rows == units in every claim (%d rows, %d distinct games across both sports): the target's bootstrap is a ROW"
        " bootstrap\n   that never sees a game label; it is a game bootstrap because Tab refuses a repeated game."
        % (len(both), units_both))

    tn, tc = X.Tab(nfl_rows, X.COLS), X.Tab(cfb_rows, X.COLS)
    ind = {"nfl": X.boot(tn, X.SEED_NFL, 2000, bins=True), "cfb": X.boot(tc, X.SEED_CFB, 2000, bins=True)}
    R["weights"] = {}
    out("   the matched interval: NFL bin shares are recomputed in every NFL resample (cross_sport.matched reads"
        " dr_n['share'][j]); held FIXED instead:")
    for b in X.BASES:
        e, v = X.matched(ind["nfl"][0], ind["nfl"][1], ind["cfb"][0], ind["cfb"][1], b)
        w = ind["nfl"][0]["share"]
        vf = (ind["cfb"][1]["dbin_" + b] * w).sum(axis=1) - ind["nfl"][1]["d_" + b]
        rs, rf = L.summ(e, v[~np.isnan(v)]), L.summ(e, vf[~np.isnan(vf)])
        R["weights"][b] = {"resampled": rs, "fixed": rf, "dropped": int(np.isnan(v).sum())}
        out("      Dm_%-10s resampled %s | fixed %s | width ratio fixed/resampled %.3f | draws dropped %d"
            % (b, L.fmt(rs), L.fmt(rf), rf["width"] / rs["width"], int(np.isnan(v).sum())))

    nN, jm = tn.n, X.COLS.index("model")
    E = np.vstack([tn.E, tc.E])
    BIN = np.concatenate([tn.bin, tc.bin])
    nb = X.N_BINS

    def D_stat(b, matched):
        jb = X.COLS.index(b)
        d = E[:, jm] - E[:, jb]

        def stat(idx):
            idx = np.asarray(idx)
            i_n, i_c = idx[idx < nN], idx[idx >= nN]
            if not matched:
                return float(d[i_c].mean() - d[i_n].mean())
            w = np.bincount(BIN[i_n], minlength=nb) / len(i_n)
            cnt = np.bincount(BIN[i_c], minlength=nb).astype(float)
            if (cnt[w > 0] == 0).any():
                raise SystemExit("alt_blocks: a resample left a weighted college bin empty")
            dbin = np.bincount(BIN[i_c], weights=d[i_c], minlength=nb)[w > 0] / cnt[w > 0]
            return float((w[w > 0] * dbin).sum() - d[i_n].mean())
        return stat
    rows_all = tn.rows + tc.rows
    sp = ["nfl"] * nN + ["cfb"] * tc.n
    labelings = {"game": ["%s:%s" % (s, r["game"]) for s, r in zip(sp, rows_all)],
                 "season-week (per sport)": ["%s:%s:%s:%s" % (s, r["season"], r["regular"], r["week"]) for s, r in zip(sp, rows_all)],
                 "season (per sport)": ["%s:%s" % (s, r["season"]) for s, r in zip(sp, rows_all)]}
    R["alt_blocks"] = {}
    out("   alt_blocks (f26lib's bootstrap, blocks pooled over both sports so the per-sport block count is not held fixed):")
    for matched in (False, True):
        for b in X.BASES:
            nm = ("Dm_" if matched else "D_") + b
            R["alt_blocks"][nm] = L.alt_blocks(D_stat(b, matched), len(E), labelings, draws=1000, seed=31)
            for k, r in R["alt_blocks"][nm].items():
                out("      %-13s %-24s (%5d blocks) %s" % (nm, k, r["n_blocks"], L.fmt(r)))

    # ------------------------------------------------------------ 3 leakage (transfer arms only)
    out("\n== 3. LEAKAGE - TRANSFER ARMS ONLY (the walk-forward fits are c-28's / c-39's: NOT RUN here)")
    R["leak_transfer"] = []
    rng = np.random.default_rng(31)
    for games, params, kw in Z["runs"]:
        key = "start_ts"
        done = [i for i, g in enumerate(games) if C.completed(g) and g[key]]
        ts = sorted(games[i][key] for i in done)
        cut = ts[int(0.6 * (len(ts) - 1))]

        def scr(gs, c, true_ts):                          # scrambled by TRUE kickoff, so a misdated row is still scrambled
            g2 = copy.deepcopy(gs)
            for g, tt in zip(g2, true_ts):
                if C.completed(g) and (tt or 0) >= c:
                    g["home_score"], g["away_score"] = g["away_score"] + int(rng.integers(1, 9)), g["home_score"]
                    if g["home_score"] == g["away_score"]:
                        g["home_score"] += 1
            return g2

        def moved(gs, c, true_ts):
            a_, b_ = dict(C.run(gs, params, **kw)[0]), dict(C.run(scr(gs, c, true_ts), params, **kw)[0])
            early = [i for i in a_ if (true_ts[i] or 0) < c]
            return sum(abs(a_[i] - b_[i]) > 1e-12 for i in early), len(early), \
                sum(abs(a_[i] - b_[i]) > 1e-12 for i in a_ if (true_ts[i] or 0) >= c)
        true_ts = [g[key] for g in games]
        mv, chk, later = moved(games, cut, true_ts)
        # the plant: one late game (after the cutoff) filed before an early one, so its scrambled score reaches earlier forecasts
        late = max(done, key=lambda i: games[i][key])
        planted = copy.deepcopy(games)
        early = min(done, key=lambda i: abs(games[i][key] - ts[int(0.3 * (len(ts) - 1))]))
        planted[late][key] = games[early][key] - 1.0      # the walk orders by (season, start_ts): misfile both
        planted[late]["season"] = games[early]["season"]
        true_p = list(true_ts)                            # audited on TRUE kickoffs: the planted row still counts as late
        pm, _pc, _pl = moved(planted, cut, true_p)
        sport = "nfl" if len(games) < 10000 else "cfb"
        rec = {"receiving_sport": sport, "mov": bool(kw.get("mov")), "games": len(games), "checked": int(chk), "moved": int(mv),
               "later_moved": int(later), "planted_moved": int(pm), "planted_fires": bool(pm)}
        R["leak_transfer"].append(rec)
        out("   carried %-5s walk over %s (%d games): scores at/after the 60th-percentile kickoff scrambled, %d earlier forecasts "
            "checked, %d moved (%d later moved); planted misdated game: %d earlier moved -> %s"
            % ("MOV" if rec["mov"] else "plain", sport, len(games), chk, mv, later, pm,
               "FIRES" if pm else "DID NOT FIRE - check is blind"))
    out("   the carried constants: fitted by c-28 / c-39 on the OTHER sport's seasons through 2025 and applied to every season"
        " of the\n   receiving sport from its first - not walk-forward in time. No receiving-sport game enters them (reading).")

    # ------------------------------------------------------------ 4 specifications / 5 MDE
    out("\n== 4-5. SPECIFICATIONS (the unit's own counts: primary family %d, all registered intervals %d) AND MDE" % (K_PRIM, K_ALL))
    R["multiplicity"] = {}
    tests = [("D_" + b, mine["primary"]["between"]["D_" + b], STATED_MDE_D.get(b)) for b in X.BASES] + \
            [("Ds_" + b, mine["primary"]["between"]["Ds_" + b], None) for b in X.BASES] + \
            [("Dm_" + b, mine["matched"]["tests"][b], STATED_MDE_DM.get(b)) for b in X.BASES]
    for nm, r, stated in tests:
        mu = L.multiplicity(r["est"], r["se"], (3, K_PRIM, K_ALL), n_blocks=r["games"])
        R["multiplicity"][nm] = dict(mu, se=r["se"], mde=L.mde_ratio(r["est"], r["se"]),
                                     mde_claim=L.mde_claim(stated, r["se"]) if stated else None)
        out("   %-13s z %+6.2f p %.2e  Bonferroni k=3 %s, k=%d %s, k=%d %s   |est|/MDE %.2f (%s)%s"
            % (nm, mu["z"], mu["p"], mu["bonferroni"][3]["survives_0.05"], K_PRIM, mu["bonferroni"][K_PRIM]["survives_0.05"],
               K_ALL, mu["bonferroni"][K_ALL]["survives_0.05"], R["multiplicity"][nm]["mde"]["ratio"],
               R["multiplicity"][nm]["mde"]["reading"],
               "" if not stated else "  stated MDE %.4f vs 2.8xSE %.4f -> %s"
               % (stated, 2.8 * r["se"], "consistent" if R["multiplicity"][nm]["mde_claim"]["consistent"] else "INCONSISTENT")))

    # 4b could the registered SUCCESS have failed? It draws on d_b (x6) and c1, c2 (x4). The six d_b are gated to the
    # published values. For c1/c2 the estimate is arithmetic on published figures; the worst SE is the sum of the two.
    out("   4b SUCCESS draws on 10 of the 16 (6 d_b + 4 contrasts). Worst case for each contrast - the two d_b perfectly"
        " anti-correlated,\n      SE = SE_1 + SE_2 - against Holm's largest multiplier (%d):" % K_PRIM)
    R["foregone"] = {}
    for s in ("nfl", "cfb"):
        t = mine["primary"]["per_sport"][s]["tests"]
        for c, (u, v) in (("c1", ("d_home", "d_record")), ("c2", ("d_record", "d_elo_nomov"))):
            se_w = t[u]["se"] + t[v]["se"]
            z = t[c]["est"] / se_w
            p = math.erfc(abs(z) / math.sqrt(2.0)) * K_PRIM
            R["foregone"]["%s_%s" % (s, c)] = {"est": t[c]["est"], "worst_se": se_w, "z_worst": z, "p_x16": p,
                                               "could_fail": bool(p >= 0.05), "se_actual": t[c]["se"]}
            out("      %s %s est %+.4f  worst-case SE %.4f (actual %.4f)  z %.2f  p x %d = %.3g -> %s"
                % (s, c, t[c]["est"], se_w, t[c]["se"], z, K_PRIM, p,
                   "COULD have failed" if p >= 0.05 else "could NOT have failed once the gate passed"))
    for s in ("nfl", "cfb"):
        for b in X.BASES:
            r = mine["primary"]["per_sport"][s]["tests"]["d_" + b]
            out("      %s d_%-10s z %.1f - gated: the script stops unless it equals the published value to 4 dp" % (s, b, r["est"] / r["se"]))

    # 4c the specification nobody counted: the matched comparison under other bins (UNREGISTERED - descriptive)
    out("   4c UNREGISTERED re-binning of the matched comparison (registered: 8 bins, top bin [0.85, 1.00]):")
    R["rebin"] = {}
    keep = (X.BIN_EDGES, X.N_BINS)
    for label, edges in (("registered 8", list(keep[0])),
                         ("top bin split at 0.90", list(keep[0]) + [0.90]),
                         ("top split at 0.90, 0.95", list(keep[0]) + [0.90, 0.95]),
                         ("4 bins (0.60/0.70/0.80)", [0.60, 0.70, 0.80]),
                         ("20 bins of 0.025", [0.5 + 0.025 * i for i in range(1, 20)])):
        X.BIN_EDGES, X.N_BINS = np.array(edges), len(edges) + 1
        a_n, a_c = X.Tab(nfl_rows, X.COLS), X.Tab(cfb_rows, X.COLS)
        i_n, i_c = X.boot(a_n, X.SEED_NFL, 1000, bins=True), X.boot(a_c, X.SEED_CFB, 1000, bins=True)
        R["rebin"][label] = {}
        line = []
        for b in X.BASES:
            e, v = X.matched(i_n[0], i_n[1], i_c[0], i_c[1], b)
            r = X.interval(e, v, min(a_n.n, a_c.n))
            r["dropped"] = int(np.isnan(v).sum())
            R["rebin"][label][b] = r
            line.append("%s %+.4f [%+.4f, %+.4f]%s" % (b, r["est"], r["lo"], r["hi"], " (dropped %d)" % r["dropped"] if r["dropped"] else ""))
        top = (int((a_n.bin == X.N_BINS - 1).sum()), int((a_c.bin == X.N_BINS - 1).sum()))
        out("      %-26s top-bin n NFL %4d / college %5d | %s" % (label, top[0], top[1], " | ".join(line)))
    X.BIN_EDGES, X.N_BINS = keep
    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    out("\nwrote %s (%.0fs)" % (a.out, R["seconds"]))


if __name__ == "__main__":
    main()
