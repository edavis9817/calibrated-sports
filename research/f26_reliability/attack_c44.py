"""f-35 target adapter: c-44's cross-sport test on the game total.

    CLAIM  a scores-only total model built by one function for both sports beats the
           team-pair mean, which beats the league-and-season mean, in the NFL (5,698
           games), college FBS-FBS (15,508) and college re-weighted to the NFL's bin
           shares of e, 2005-2025; 17 of 17 primary tests Holm-significant. College
           minus NFL skill gap vs the league mean +0.0794 raw, +0.0134 [+0.0008, +0.0265]
           re-weighted (stated pre-run MDE 0.0187); vs the pair mean +0.0230 raw,
           +0.0183 [+0.0110, +0.0258] re-weighted. Loses to the price 2013-2025:
           NFL +9.59 [+7.15, +12.08], college (CFBD, not a close) +16.52 [+13.63, +19.48].

READ-ONLY. Run as a script against a detached worktree of origin/c-44-cross-sport-total:

    python research/f26_reliability/attack_c44.py --src <worktree> --logger-db <market_log.db> \
        --cache <scratch>/c44rows.pkl --scratch <scratch> --out <scratch>/c44.json

WHAT IS RUN
  1  the target's two loaders ONCE (market_log.db and cfb.db, mode=ro through the target's
     own connect functions), cached; the target's own main() over those games; every
     numeric leaf of its JSON against the committed one; the input versions compared
  2  duplication_through on the target's boot() + stat() + interval(); weights fixed vs
     re-estimated; alt_blocks (pooled) and a per-sport stratified season / week bootstrap
  3  score scramble at two cutoffs per sport through the target's build(), with a misdated
     game planted; a truncated refit of (k, r, a, b) for every scored season, with a plant;
     a description of the kickoff timestamps the 6-hour rule leans on
  4  the registered count from the result file; Bonferroni; the script's git history
  5  mde_ratio, mde_claim; an UNREGISTERED re-binning (alternatives fixed before looking)
WHAT IS NOT RUN
  whatever a step prints as NOT RUN (a deadline inside this script, --budget seconds).
"""
import argparse
import copy
import json
import os
import pickle
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
THROUGH_DRAWS = 300
PUB = [  # (name, path into the target's JSON, published, dp)
    ("n NFL", ("primary", "per_arm", "nfl", "n"), 5698, 0),
    ("n college", ("primary", "per_arm", "cfb", "n"), 15508, 0),
    ("Holm family", ("primary", "holm_family_size"), 17, 0),
    ("Holm-significant", ("primary", "holm_significant"), 17, 0),
    ("Ds_league raw est", ("primary", "between", "Ds_league", "est"), 0.0794, 4),
    ("Dms_league est", ("primary", "between", "Dms_league", "est"), 0.0134, 4),
    ("Dms_league lo", ("primary", "between", "Dms_league", "lo"), 0.0008, 4),
    ("Dms_league hi", ("primary", "between", "Dms_league", "hi"), 0.0265, 4),
    ("Dms_league Holm p", ("primary", "between", "Dms_league", "p_holm"), 0.044, 3),
    ("Ds_pair raw est", ("primary", "between", "Ds_pair", "est"), 0.0230, 4),
    ("Dms_pair est", ("primary", "between", "Dms_pair", "est"), 0.0183, 4),
    ("Dms_pair lo", ("primary", "between", "Dms_pair", "lo"), 0.0110, 4),
    ("Dms_pair hi", ("primary", "between", "Dms_pair", "hi"), 0.0258, 4),
    ("NFL d_pair est", ("primary", "per_arm", "nfl", "tests", "d_pair", "est"), -1.73, 2),
    ("NFL MSE pair", ("primary", "per_arm", "nfl", "mse", "mse_pair"), 186, 0),
    ("stated MDE Dms_league", ("mde_formula", "tests", "Dms_league"), 0.0187, 4),
    ("price NFL est", ("price", "nfl", "dMSE", "est"), 9.59, 2),
    ("price NFL lo", ("price", "nfl", "dMSE", "lo"), 7.15, 2),
    ("price NFL hi", ("price", "nfl", "dMSE", "hi"), 12.08, 2),
    ("price college est", ("price", "cfb", "dMSE", "est"), 16.52, 2),
    ("price college lo", ("price", "cfb", "dMSE", "lo"), 13.63, 2),
    ("price college hi", ("price", "cfb", "dMSE", "hi"), 19.48, 2),
]
STATED_MDE = {"Dms_league": 0.0187, "Dms_pair": 0.0100, "Ds_league": 0.0217, "Ds_pair": 0.0094}


def leaves(a, b, path=""):
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


def dig(d, path):
    for k in path:
        d = d[k]
    return d


def main():
    ap = argparse.ArgumentParser()
    for f in ("--src", "--logger-db", "--cache", "--scratch", "--out"):
        ap.add_argument(f, required=True)
    ap.add_argument("--budget", type=float, default=1500.0, help="seconds; a step past it prints NOT RUN")
    a = ap.parse_args()
    os.environ["LOGGER_DB"] = os.path.abspath(a.logger_db)      # config reads it at import; both opens are mode=ro
    sys.path.insert(0, HERE)
    import f26lib as L
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    os.chdir(src)
    from research import cross_sport_total as X
    if not os.path.abspath(X.__file__).startswith(src):
        raise SystemExit("research.cross_sport_total resolved outside --src: %s" % X.__file__)
    recorded = json.load(open(os.path.join(src, "research", "results", "cross_sport_total.json"), encoding="utf-8"))
    K_ALL = L.registered_count({"registered_intervals": {"count": recorded.get("counts", {}).get("registered_intervals")}},
                               "c-44")
    K_PRIM = recorded["primary"]["holm_family_size"]
    R = {"target": "c-44", "src": src, "not_run": []}
    out = lambda s="": print(s, flush=True)  # noqa: E731
    t0 = time.time()
    left = lambda: a.budget - (time.time() - t0)  # noqa: E731

    def not_run(what, why):
        R["not_run"].append({"what": what, "why": why})
        out("   NOT RUN: %s - %s" % (what, why))

    # ------------------------------------------------------------ load once, cache
    if os.path.exists(a.cache):
        Z = pickle.load(open(a.cache, "rb"))
        out("games from cache %s (stores NOT re-opened in this invocation; read at %s)" % (a.cache, Z["read_at"]))
    else:
        import config
        from cfb import paths as cfb_paths
        quiet = lambda s="": None  # noqa: E731
        t1 = time.time()
        nfl = X.load_nfl(quiet)
        t2 = time.time()
        cfb = X.load_cfb(quiet)
        Z = {"nfl": nfl, "cfb": cfb, "sec": (t2 - t1, time.time() - t2),
             "stores": (os.path.abspath(config.DB_PATH), os.path.abspath(cfb_paths.db_path())),
             "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        pickle.dump(Z, open(a.cache, "wb"))
        out("stores READ (mode=ro, the target's jobs.season_model.market_log_ro and research.cfb_game_forecast.connect):")
        out("   %s  (%.1fs, connection closed by the loader)\n   %s  (%.1fs, connection closed by the loader)"
            % (Z["stores"][0], Z["sec"][0], Z["stores"][1], Z["sec"][1]))
    R["stores"], R["read_at"] = Z["stores"], Z["read_at"]
    nfl_games, cfb_games = Z["nfl"][0], Z["cfb"][0]
    GAMES = {"nfl": nfl_games, "cfb": cfb_games}

    # ------------------------------------------------------------ 1 reproduce
    out("\n== 1. REPRODUCE: the target's own main() over today's stores, against its committed JSON")
    X.load_nfl, X.load_cfb = (lambda o: Z["nfl"]), (lambda o: Z["cfb"])
    mine_path = os.path.join(a.scratch, "cross_sport_total.rerun.json")
    real_stdout, sys.stdout = sys.stdout, open(os.path.join(a.scratch, "cross_sport_total.rerun.stdout"), "w", encoding="utf-8")
    t1 = time.time()
    try:
        rc_main = X.main(["--json-out", mine_path, "--log-out", os.path.join(a.scratch, "cross_sport_total.rerun.log")])
    finally:
        sys.stdout.close()
        sys.stdout = real_stdout
    mine = json.load(open(mine_path, encoding="utf-8"))
    n_leaf, bad = leaves(recorded, mine)
    v_rec, v_mine = recorded["nfl"].get("versions"), mine["nfl"].get("versions")
    R["reproduce"] = {"main_rc": rc_main, "main_seconds": time.time() - t1, "numeric_leaves_compared": n_leaf,
                      "differing": len(bad), "first": bad[:12], "versions_committed": v_rec, "versions_today": v_mine,
                      "items": []}
    out("   main() rc %s in %.0fs; %d numeric leaves compared with research/results/cross_sport_total.json, %d differ"
        % (rc_main, time.time() - t1, n_leaf, len(bad)))
    for p, u, v in bad[:12]:
        out("      differs %s: committed %r, re-run %r" % (p, u, v))
    if n_leaf < 500:
        raise SystemExit("only %d leaves compared - the diff walked nothing" % n_leaf)
    out("   input versions: committed %s | today %s -> %s" % (v_rec, v_mine, "SAME" if v_rec == v_mine else
        "INPUT REBUILT since the registered run (nfl_games); figures below are on the rebuilt input"))
    for name, path, pub, dp in PUB:
        r = L.reproduce(name, dig(mine, path), pub, dp)
        r["committed_json"] = dig(recorded, path)
        R["reproduce"]["items"].append(r)
        same_json = abs(r["measured"] - r["committed_json"]) <= 1e-9 * max(1.0, abs(r["measured"]))
        r["equals_committed_json"] = bool(same_json)
        out("   %-24s measured %+.6f  committed json %+.6f  published %+.*f  -> %s"
            % (name, r["measured"], r["committed_json"], dp, pub, "REPRODUCES" if r["reproduces"] else
               "DOES NOT REPRODUCE at %d dp%s" % (dp, " (re-run == committed JSON: the PUBLISHED figure is not the JSON figure"
                                                  " rounded to %d dp; no input moved it)" % dp if same_json else
                                                  " (re-run differs from the committed JSON)")))
    sg = lambda r: "below" if r["hi"] < 0 else "above" if r["lo"] > 0 else "contains 0"  # noqa: E731
    checks = [("Brier re-weighted Dm_league contains 0", sg(mine["brier"]["between"]["Dm_league"]) == "contains 0"),
              ("reverse (NFL at college shares) Ds_league contains 0", sg(mine["reverse"]["Ds_league"]) == "contains 0"),
              ("NFL d_pair fails Holm in cut 'regular weeks 1-4'",
               not mine["cuts"]["results"]["2 regular weeks 1-4"]["nfl"]["pair"]["holm_significant"]),
              ("NFL d_pair fails Holm in cut '2005-2014'",
               not mine["cuts"]["results"]["4 2005-2014"]["nfl"]["pair"]["holm_significant"]),
              ("PART 1 success", bool(mine["primary"]["success"]))]
    R["reproduce"]["statements"] = {k: bool(v) for k, v in checks}
    for k, v in checks:
        out("   %-52s -> %s" % (k, "REPRODUCES" if v else "DOES NOT REPRODUCE"))

    # ------------------------------------------------------------ the forecasts, built once more for steps 2-5
    BUILT = {s: X.build(GAMES[s]) for s in GAMES}
    ORD, TAB = {}, {}
    for s, (F, y) in BUILT.items():
        ORD[s] = np.argsort(F["game"], kind="stable")          # Tab's own order
        TAB[s] = X.Tab(F, y)
    by_id = {s: {g["game"]: g for g in GAMES[s]} for s in GAMES}
    units_mine = {s: len({(by_id[s][g]["ts"], by_id[s][g]["home"], by_id[s][g]["away"]) for g in BUILT[s][0]["game"]})
                  for s in GAMES}

    def rows_of(s, price=None):
        F, y = BUILT[s]
        rows = []
        for i in range(len(y)):
            r = {"sport": s, "game": "%s:%s" % (s, F["game"][i]), "y": float(y[i])}
            for k in ("mu", "league", "pair", "e", "p_model", "p_pair", "q"):
                r[k] = float(F[k][i])
            r["bin"], r["season"] = int(F["bin"][i]), int(F["season"][i])
            if price is not None:
                r["price"] = price.get(F["game"][i], float("nan"))
            rows.append(r)
        return rows

    guard = {"fired": 0}

    def tab_of(rows, extra=False):
        def mk(rs):
            F = {k: np.array([r[k] for r in rs]) for k in ("game", "mu", "league", "pair", "e", "p_model", "p_pair", "q", "bin")}
            return X.Tab(F, np.array([r["y"] for r in rs]), extra=np.array([r["price"] for r in rs]) if extra else None)
        try:
            return mk(rows)
        except ValueError:                                 # Tab refuses a game that appears twice
            guard["fired"] += 1
            return mk([dict(r, game="%s~%d" % (r["game"], i)) for i, r in enumerate(rows)])

    # ------------------------------------------------------------ 2 blocks
    out("\n== 2. BLOCKS: THROUGH the target's boot() + stat() + interval(), %d draws" % THROUGH_DRAWS)

    def w_within(name, extra=False):
        def w(rows):
            t = tab_of(rows, extra)
            est, dr = X.stat(X.boot(t, X.SEED_OWN, THROUGH_DRAWS))
            r = X.interval(est[name], dr[name], t.n)
            return r["hi"] - r["lo"]
        return w

    def w_between(key, matched):
        def w(rows):
            tn, tc = tab_of([r for r in rows if r["sport"] == "nfl"]), tab_of([r for r in rows if r["sport"] == "cfb"])
            bn, bc = X.boot(tn, X.SEED_NFL, THROUGH_DRAWS), X.boot(tc, X.SEED_CFB, THROUGH_DRAWS)
            en, dn = X.stat(bn)
            ec, dc = X.stat(bc, X.shares(bn)) if matched else X.stat(bc)
            r = X.interval(ec[key] - en[key], dc[key] - dn[key], min(tn.n, tc.n))
            return r["hi"] - r["lo"]
        return w
    rn, rcf = rows_of("nfl"), rows_of("cfb")
    both = rn + rcf
    seasons_price = mine["price"]["seasons"]
    pn = [r for r in rows_of("nfl", Z["nfl"][1]) if r["season"] in seasons_price and r["price"] == r["price"]]
    out("   distinct games counted here from (start, home, away): NFL %d of %d rows, college %d of %d rows"
        % (units_mine["nfl"], len(rn), units_mine["cfb"], len(rcf)))
    R["units"] = {"nfl": units_mine["nfl"], "cfb": units_mine["cfb"], "rows_nfl": len(rn), "rows_cfb": len(rcf)}
    fn_b = "cross_sport_total.boot x2 (seeds 4401/4402)+shares+stat+interval"
    jobs = [("headline Dms_league (+0.0134)", w_between("s_league", True), both, fn_b, units_mine["nfl"] + units_mine["cfb"]),
            ("headline Dms_pair (+0.0183)", w_between("s_pair", True), both, fn_b, units_mine["nfl"] + units_mine["cfb"]),
            ("raw Ds_league (+0.0794)", w_between("s_league", False), both,
             "cross_sport_total.boot x2 (seeds 4401/4402)+stat+interval", units_mine["nfl"] + units_mine["cfb"]),
            ("within nfl d_pair (-1.73)", w_within("d_pair"), rn, "cross_sport_total.boot (seed 24)+stat+interval", units_mine["nfl"]),
            ("within cfb d_pair", w_within("d_pair"), rcf, "cross_sport_total.boot (seed 24)+stat+interval", units_mine["cfb"]),
            ("price nfl d_price (+9.59)", w_within("d_price", True), pn, "cross_sport_total.boot (seed 24)+stat+interval",
             len({r["game"] for r in pn}))]
    R["through"] = {}
    for name, w, rows, fn, units in jobs:
        g0 = guard["fired"]
        t = L.duplication_through(w, rows, "game", fn_name=fn, units=units)
        t["dup_guard_fired"] = guard["fired"] - g0
        R["through"][name] = t
        out("   %-30s %s" % (name, L.through_line(t)))
        out("   %-30s Tab's 'a game appears twice' guard fired %d time(s) on the in-block copies and was bypassed"
            % ("", t["dup_guard_fired"]))
    R["through_verdicts"] = L.require_through([j[0] for j in jobs], R["through"])
    out("   reading of the code: boot() resamples ROWS (rng.integers(0, n, n)) and never sees a game label; one row is one game\n"
        "   because Tab refuses a repeated game id. The between-sport draws are independent per sport (seeds 4401 / 4402), and\n"
        "   the NFL bin shares are RE-ESTIMATED in every NFL draw (shares(ind['nfl'])[1]) and applied to the same-index college draw.")

    # weights fixed vs re-estimated, at the target's 2000 draws and seeds
    ind = {"nfl": X.boot(TAB["nfl"], X.SEED_NFL, 2000), "cfb": X.boot(TAB["cfb"], X.SEED_CFB, 2000)}
    wn = X.shares(ind["nfl"])
    e_n, d_n = X.stat(ind["nfl"])
    e_cm, d_cm = X.stat(ind["cfb"], wn)
    e_cf, d_cf = X.stat(ind["cfb"], (wn[0], np.broadcast_to(wn[0], wn[1].shape)))
    R["weights"] = {}
    for b in X.BASES:
        rs = L.summ(e_cm["s_" + b] - e_n["s_" + b], d_cm["s_" + b] - d_n["s_" + b])
        rf = L.summ(e_cf["s_" + b] - e_n["s_" + b], d_cf["s_" + b] - d_n["s_" + b])
        R["weights"][b] = {"re_estimated": rs, "fixed": rf}
        out("      Dms_%-7s weights re-estimated %s | weights FIXED %s | width fixed/re-estimated %.3f"
            % (b, L.fmt(rs), L.fmt(rf), rf["width"] / rs["width"]))

    # alt blocks: pooled (f26lib) and stratified per sport (own draws)
    tn, tc = TAB["nfl"], TAB["cfb"]
    nN, nb = tn.n, X.N_BINS
    LM = np.vstack([tn.L[:, :3], tc.L[:, :3]])
    BIN = np.concatenate([tn.bin, tc.bin])

    def D_stat(b, matched):
        jb = 1 if b == "league" else 2

        def stat(idx):
            idx = np.asarray(idx)
            i_n, i_c = idx[idx < nN], idx[idx >= nN]
            s_n = 1.0 - LM[i_n, 0].mean() / LM[i_n, jb].mean()
            if not matched:
                return float(1.0 - LM[i_c, 0].mean() / LM[i_c, jb].mean() - s_n)
            w = np.bincount(BIN[i_n], minlength=nb) / len(i_n)
            cnt = np.bincount(BIN[i_c], minlength=nb).astype(float)
            if (cnt[w > 0] == 0).any():
                raise SystemExit("alt_blocks: a resample left a weighted college bin empty")
            m0 = (w[w > 0] * (np.bincount(BIN[i_c], weights=LM[i_c, 0], minlength=nb)[w > 0] / cnt[w > 0])).sum()
            mb = (w[w > 0] * (np.bincount(BIN[i_c], weights=LM[i_c, jb], minlength=nb)[w > 0] / cnt[w > 0])).sum()
            return float(1.0 - m0 / mb - s_n)
        return stat
    meta = {s: [(int(BUILT[s][0]["season"][i]), bool(BUILT[s][0]["regular"][i]), int(BUILT[s][0]["week"][i]) if
                 BUILT[s][0]["week"][i] is not None else -1) for i in ORD[s]] for s in GAMES}
    lab = {"game": ["%s:%d" % (s, i) for s in ("nfl", "cfb") for i in range(TAB[s].n)],
           "season-week (per sport)": ["%s:%s:%s:%s" % (s, m[0], m[1], m[2]) for s in ("nfl", "cfb") for m in meta[s]],
           "season (per sport)": ["%s:%s" % (s, m[0]) for s in ("nfl", "cfb") for m in meta[s]]}
    R["alt_blocks"] = {}
    out("   alt_blocks (f26lib's bootstrap, 1000 draws; blocks POOLED over both sports, so a draw's per-sport block count varies):")
    for nm, b, matched in (("Dms_league", "league", True), ("Dms_pair", "pair", True), ("Ds_league", "league", False),
                           ("Ds_pair", "pair", False)):
        R["alt_blocks"][nm] = L.alt_blocks(D_stat(b, matched), nN + tc.n, lab, draws=1000, seed=35)
        for k, r in R["alt_blocks"][nm].items():
            out("      %-11s %-24s (%5d blocks) %s" % (nm, k, r["n_blocks"], L.fmt(r)))
    jp = 2
    dpair = tn.L[:, 0] - tn.L[:, jp]
    R["alt_blocks"]["nfl d_pair"] = L.alt_blocks(lambda i: float(dpair[np.asarray(i)].mean()), nN,
                                                 {k: v[:nN] for k, v in lab.items()}, draws=1000, seed=35)
    for k, r in R["alt_blocks"]["nfl d_pair"].items():
        out("      %-11s %-24s (%5d blocks) %s" % ("nfl d_pair", k, r["n_blocks"], L.fmt(r, 3)))

    out("   STRATIFIED block bootstrap (own draws, 2000; blocks resampled WITHIN each sport, independent rngs 3501 / 3502,\n"
        "   NFL bin shares re-estimated in every draw - the target's design with the block changed):")
    R["stratified"] = {}

    def block_sums(s, labels):
        t = TAB[s]
        ids = sorted(set(labels))
        pos = {k: j for j, k in enumerate(ids)}
        g = np.array([pos[k] for k in labels])
        C = np.zeros((len(ids), nb))
        S = np.zeros((len(ids), nb, 3))
        np.add.at(C, (g, t.bin), 1.0)
        for c in range(3):
            np.add.at(S[:, :, c], (g, t.bin), t.L[:, c])
        return C, S

    def strat(labels_n, labels_c, draws=2000):
        (Cn, Sn), (Cc, Sc) = block_sums("nfl", labels_n), block_sums("cfb", labels_c)
        r1, r2 = np.random.default_rng(3501), np.random.default_rng(3502)
        res = {k: np.empty(draws) for k in ("Dms_league", "Dms_pair", "Ds_league", "Ds_pair", "nfl_d_pair")}
        dropped = 0

        def one(cn, sn, cc, sc):
            w = cn / cn.sum()
            mn, mc = sn.sum(0) / cn.sum(), sc.sum(0) / cc.sum()
            if ((cc == 0) & (w > 0)).any():
                return None
            with np.errstate(invalid="ignore", divide="ignore"):
                mw = (w[:, None] * np.nan_to_num(sc / cc[:, None])).sum(0)
            return {"Dms_league": (1 - mw[0] / mw[1]) - (1 - mn[0] / mn[1]), "Dms_pair": (1 - mw[0] / mw[2]) - (1 - mn[0] / mn[2]),
                    "Ds_league": (1 - mc[0] / mc[1]) - (1 - mn[0] / mn[1]), "Ds_pair": (1 - mc[0] / mc[2]) - (1 - mn[0] / mn[2]),
                    "nfl_d_pair": mn[0] - mn[2]}
        est = one(Cn.sum(0), Sn.sum(0), Cc.sum(0), Sc.sum(0))
        for d in range(draws):
            i, j = r1.integers(0, len(Cn), len(Cn)), r2.integers(0, len(Cc), len(Cc))
            v = one(Cn[i].sum(0), Sn[i].sum(0), Cc[j].sum(0), Sc[j].sum(0))
            if v is None:
                dropped += 1
                v = {k: np.nan for k in res}
            for k in res:
                res[k][d] = v[k]
        return {k: dict(L.summ(est[k], res[k][~np.isnan(res[k])]), blocks_nfl=len(Cn), blocks_cfb=len(Cc), dropped=dropped)
                for k in res}
    for label, key in (("game", lambda s: ["%d" % i for i in range(TAB[s].n)]),
                       ("season-week", lambda s: ["%s:%s:%s" % m for m in meta[s]]),
                       ("season", lambda s: ["%s" % m[0] for m in meta[s]])):
        R["stratified"][label] = strat(key("nfl"), key("cfb"))
        for k, r in R["stratified"][label].items():
            out("      %-11s %-12s (NFL %5d / college %5d blocks) %s%s" % (k, label, r["blocks_nfl"], r["blocks_cfb"],
                                                                         L.fmt(r, 3 if k == "nfl_d_pair" else 4),
                                                                         "  dropped %d" % r["dropped"] if r["dropped"] else ""))
    hs = R["stratified"]["season"]["Dms_league"]
    out("   HEADLINE +0.0134 under SEASON blocks (21 per sport): %s -> %s"
        % (L.fmt(hs), "still excludes zero" if hs["excludes_zero"] else "CONTAINS ZERO - it needs game blocks to exclude zero"))

    # ------------------------------------------------------------ 3 leakage
    out("\n== 3. LEAKAGE through the target's build()")
    R["leak"], R["refit"], R["timestamps"] = [], {}, {}
    FIELDS = ("mu", "league", "pair", "p_model", "p_pair", "q", "s_model", "s_pair", "sigma")

    def fc(games):
        F, _y = X.build(games)
        M = np.column_stack([F[k] for k in FIELDS])
        return {g: M[i] for i, g in enumerate(F["game"])}, F["fits"]

    def scr(games, cut, true_ts, rng):
        g2 = copy.deepcopy(games)
        for g, tt in zip(g2, true_ts):
            if tt >= cut:
                g["hs"], g["as_"] = g["hs"] + int(rng.integers(1, 15)), g["as_"] + int(rng.integers(1, 15))
        return g2

    def moved(games, cut, true_ts, rng):
        a_, b_ = fc(games)[0], fc(scr(games, cut, true_ts, rng))[0]
        tt = {g["game"]: t for g, t in zip(games, true_ts)}
        early = [g for g in a_ if tt[g] < cut]
        late = [g for g in a_ if tt[g] >= cut]
        df = lambda g: bool(np.nanmax(np.abs(a_[g] - b_[g])) > 1e-9)  # noqa: E731
        return sum(df(g) for g in early), len(early), sum(df(g) for g in late), len(late)
    for s in ("nfl", "cfb"):
        games = GAMES[s]
        true_ts = [g["ts"] for g in games]
        for label, cut in (("first kickoff of 2012", min(g["ts"] for g in games if g["season"] == 2012)),
                           ("median kickoff of 2019", float(np.median([g["ts"] for g in games if g["season"] == 2019])))):
            if left() < 240:
                not_run("scramble %s at %s" % (s, label), "the script's own deadline (%.0fs left)" % left())
                continue
            rng = np.random.default_rng(35)
            mv, chk, lmv, lchk = moved(games, cut, true_ts, rng)
            late_i = max(range(len(games)), key=lambda i: games[i]["ts"])
            ts_sorted = sorted(true_ts)
            early_i = min(range(len(games)), key=lambda i: abs(games[i]["ts"] - ts_sorted[int(0.3 * (len(games) - 1))]))
            planted = copy.deepcopy(games)
            planted[late_i]["ts"] = games[early_i]["ts"] - 86400.0     # one late game filed under an early date and season
            planted[late_i]["season"] = games[early_i]["season"]
            pm, pchk, _l, _lc = moved(planted, cut, true_ts, np.random.default_rng(35))   # audited on TRUE kickoffs
            rec = {"sport": s, "cutoff": label, "games": len(games), "checked": chk, "moved": mv, "later_moved": lmv,
                   "later": lchk, "planted_moved": pm, "planted_fires": bool(pm),
                   "planted_game_true_season": games[late_i]["season"], "planted_as_season": games[early_i]["season"]}
            R["leak"].append(rec)
            out("   %s, scores at/after the %s scrambled (%d games in the walk): %d earlier scored forecasts checked on %d fields,"
                " %d MOVED (%d of %d later moved);\n      plant - one %d game filed under %d: %d earlier moved -> %s"
                % (s, label, len(games), chk, len(FIELDS), mv, lmv, lchk, rec["planted_game_true_season"],
                   rec["planted_as_season"], pm, "FIRES" if pm else "DID NOT FIRE - check is blind"))

    out("   3b truncated refit of (k, r, a, b): the store cut to seasons < T, plus ONE season-T fixture with its score zeroed\n"
        "      (build() stops on a scored season with no game; that row cannot enter the fit, tr = season < T), SCORE_TO = T:")
    keep_to = X.SCORE_TO
    try:
        for s in ("nfl", "cfb"):
            games, walk = GAMES[s], BUILT[s][0]["fits"]
            diff, done = [], 0
            for T in range(X.SCORE_FROM, keep_to + 1):
                if left() < 150:
                    break
                first = min((g for g in games if g["season"] == T), key=lambda g: g["ts"])
                X.SCORE_TO = T
                f = X.build([g for g in games if g["season"] < T] + [dict(first, hs=0, as_=0)])[0]["fits"][T]
                done += 1
                w = walk[T]
                if (f["k"], f["r"]) != (w["k"], w["r"]) or abs(f["a"] - w["a"]) > 1e-9 or abs(f["b"] - w["b"]) > 1e-9 \
                        or f["train_games"] != w["train_games"]:
                    diff.append((T, f, w))
            plant = None
            if left() >= 150:
                T = 2015
                first = min((g for g in games if g["season"] == T), key=lambda g: g["ts"])
                X.SCORE_TO = T
                leak = [dict(g, season=T - 1) for g in games if g["season"] == T]      # season T's games filed as T-1
                f = X.build([g for g in games if g["season"] < T] + leak + [dict(first, game="dummy", hs=0, as_=0)])[0]["fits"][T]
                w = walk[T]
                plant = bool((f["k"], f["r"]) != (w["k"], w["r"]) or abs(f["a"] - w["a"]) > 1e-9 or abs(f["b"] - w["b"]) > 1e-9)
            R["refit"][s] = {"seasons_refit": done, "of": keep_to - X.SCORE_FROM + 1, "differ": [d[0] for d in diff],
                             "plant_fires": plant}
            out("      %s: %d of %d scored seasons refit on a truncated store; %d differ from the walk's constants%s; "
                "plant (2015's games filed as 2014): %s"
                % (s, done, keep_to - X.SCORE_FROM + 1, len(diff), " %s" % [d[0] for d in diff] if diff else "",
                   "NOT RUN" if plant is None else ("FIRES" if plant else "DID NOT FIRE - check is blind")))
            if done < keep_to - X.SCORE_FROM + 1:
                not_run("truncated refit %s, %d seasons" % (s, keep_to - X.SCORE_FROM + 1 - done), "the script's own deadline")
    finally:
        X.SCORE_TO = keep_to

    out("   3c the timestamps the 6-hour rule reads (DESCRIPTIVE - not a test; no source here says when a game really started):")
    for s in ("nfl", "cfb"):
        games = sorted(GAMES[s], key=lambda g: g["ts"])
        ts = np.array([g["ts"] for g in games])
        scored = np.array([X.SCORE_FROM <= g["season"] <= X.SCORE_TO for g in games])
        tod = (ts % 86400).astype(int)
        per = {}
        for T in sorted({g["season"] for g in games}):
            m = np.array([g["season"] == T for g in games])
            v, c = np.unique(tod[m], return_counts=True)
            per[T] = (float(c.max() / m.sum()), int(v[c.argmax()]))
        flagged = [T for T, (sh, _v) in per.items() if sh > 0.5]
        near = np.searchsorted(ts, ts - X.LAG, side="right") - np.searchsorted(ts, ts - 2 * X.LAG, side="right")
        same = np.searchsorted(ts, ts, side="left") - np.searchsorted(ts, ts - X.LAG, side="right")
        R["timestamps"][s] = {"seasons_one_time_of_day_over_half": flagged,
                              "max_share_one_time_of_day": max(sh for sh, _ in per.values()),
                              "scored_with_state_game_6_to_12h_before": int((near[scored] > 0).sum()),
                              "scored_with_game_inside_6h_before": int((same[scored] > 0).sum()), "scored": int(scored.sum())}
        out("      %s: seasons where one UTC time-of-day carries > half the games: %s (largest single-time share in any season %.3f);\n"
            "         scored games whose state already holds a game stamped 6-12h earlier: %d of %d; with a game stamped < 6h earlier"
            " (held out by the rule): %d"
            % (s, flagged or "none", R["timestamps"][s]["max_share_one_time_of_day"],
               R["timestamps"][s]["scored_with_state_game_6_to_12h_before"], int(scored.sum()),
               R["timestamps"][s]["scored_with_game_inside_6h_before"]))

    # ------------------------------------------------------------ 4 specifications / 5 MDE
    out("\n== 4-5. SPECIFICATIONS (the unit's own counts: primary family %d, all registered intervals %d) AND MDE" % (K_PRIM, K_ALL))
    R["multiplicity"] = {}
    tests = [(k, mine["primary"]["between"][k]) for k in ("Ds_league", "Dms_league", "Ds_pair", "Dms_pair")] + \
            [("nfl d_pair", mine["primary"]["per_arm"]["nfl"]["tests"]["d_pair"]),
             ("nfl d_league", mine["primary"]["per_arm"]["nfl"]["tests"]["d_league"]),
             ("cfb d_pair", mine["primary"]["per_arm"]["cfb"]["tests"]["d_pair"]),
             ("cfb@nfl d_pair", mine["primary"]["per_arm"]["cfb_at_nfl"]["tests"]["d_pair"]),
             ("price nfl", mine["price"]["nfl"]["dMSE"]), ("price cfb", mine["price"]["cfb"]["dMSE"])]
    for nm, r in tests:
        stated = STATED_MDE.get(nm)
        mu = L.multiplicity(r["est"], r["se"], (K_PRIM, K_ALL), n_blocks=r["games"])
        R["multiplicity"][nm] = dict(mu, est=r["est"], se=r["se"], p_holm_target=r.get("p_holm"), mde=L.mde_ratio(r["est"], r["se"]),
                                     mde_claim=L.mde_claim(stated, r["se"]) if stated else None)
        out("   %-15s est %+9.4f z %+6.2f p %.2e  Bonferroni k=%d %s (p_adj %.3g), k=%d %s   |est|/(2.8 SE) %.2f (%s)%s"
            % (nm, r["est"], mu["z"], mu["p"], K_PRIM, mu["bonferroni"][K_PRIM]["survives_0.05"], mu["bonferroni"][K_PRIM]["p_adj"],
               K_ALL, mu["bonferroni"][K_ALL]["survives_0.05"], R["multiplicity"][nm]["mde"]["ratio"],
               R["multiplicity"][nm]["mde"]["reading"],
               "" if not stated else "  stated MDE %.4f vs 2.8xSE %.4f -> %s; |est|/stated MDE %.2f"
               % (stated, 2.8 * r["se"], "consistent" if R["multiplicity"][nm]["mde_claim"]["consistent"] else "INCONSISTENT",
                  abs(r["est"]) / stated)))
    hl = mine["primary"]["between"]["Dms_league"]
    out("   the target's Holm p for Dms_league is %.4f = its raw p %.4f: it is the LARGEST p of the 17, so Holm's multiplier on it is 1."
        % (hl["p_holm"], hl["p"]))
    for nm in ("Dms_league", "Dms_pair"):
        for label in ("season",):
            r = R["stratified"][label][nm]
            mu = L.multiplicity(r["est"], r["se"], (K_PRIM, K_ALL), n_blocks=min(r["blocks_nfl"], r["blocks_cfb"]))
            R["multiplicity"]["%s @%s blocks" % (nm, label)] = mu
            out("   %-15s under %s blocks: z %+.2f p %.3g  Bonferroni k=%d %s, k=%d %s"
                % (nm, label, mu["z"], mu["p"], K_PRIM, mu["bonferroni"][K_PRIM]["survives_0.05"], K_ALL,
                   mu["bonferroni"][K_ALL]["survives_0.05"]))

    out("   4b the script's history on the target branch (git, read-only):")
    R["git"] = {}
    for key, cmd in (("log_script", ["git", "log", "--format=%h %ad %s", "--date=iso", "--", "research/cross_sport_total.py"]),
                     ("diff_script_since_6fa8f3c", ["git", "diff", "--stat", "6fa8f3c", "HEAD", "--", "research/cross_sport_total.py",
                                                    "tests/test_cross_sport_total.py"]),
                     ("diff_prereg_since_c92f98e", ["git", "diff", "--stat", "c92f98e", "HEAD", "--",
                                                    "docs/C44-cross-sport-total-preregistration.md"]),
                     ("log_results", ["git", "log", "--format=%h %ad", "--date=iso", "--", "research/results/cross_sport_total.json",
                                      "research/results/cross_sport_total_mde.json"]),
                     ("head", ["git", "rev-parse", "--short", "HEAD"])):
        p = subprocess.run(cmd, cwd=src, capture_output=True, text=True, timeout=60)
        R["git"][key] = {"rc": p.returncode, "out": p.stdout.strip()}
        out("      %s (rc %d): %s" % (key, p.returncode, " | ".join(x[:110] for x in p.stdout.strip().splitlines()) or "<empty>"))
    res_dir = os.path.join(src, "research", "results")
    R["result_files"] = sorted(f for f in os.listdir(res_dir) if "cross_sport_total" in f)
    out("      result files named cross_sport_total*: %s" % R["result_files"])
    out("      BIN_EDGES in the script at HEAD: %s (pre-registration: -6, -4, -2, 0, 2, 4, 6)" % X.BIN_EDGES.tolist())

    out("   5b UNREGISTERED re-binning, DESCRIPTIVE (alternatives fixed in this file before any was computed; the target's seeds,"
        " 2000 draws):")
    R["rebin"] = {}
    e_nfl = BUILT["nfl"][0]["e"]
    alts = [("registered 8 (control)", list(X.BIN_EDGES), None),
            ("6 quantile bins of NFL e", np.quantile(e_nfl, [i / 6 for i in range(1, 6)]).tolist(), None),
            ("12 quantile bins of NFL e", np.quantile(e_nfl, [i / 12 for i in range(1, 12)]).tolist(), None),
            ("common support |e| < 6, 6 bins", [-4.0, -2.0, 0.0, 2.0, 4.0], 6.0)]
    keep = (X.BIN_EDGES, X.N_BINS)
    try:
        for label, edges, trim in alts:
            if left() < 60:
                not_run("re-binning '%s'" % label, "the script's own deadline")
                continue
            X.BIN_EDGES, X.N_BINS = np.array(edges), len(edges) + 1
            tabs = {}
            for s in ("nfl", "cfb"):
                F, y = BUILT[s]
                F2 = dict(F, bin=np.searchsorted(X.BIN_EDGES, F["e"], side="right"))
                tabs[s] = X.Tab(F2, y, mask=None if trim is None else np.abs(F["e"]) < trim)
            bn, bc = X.boot(tabs["nfl"], X.SEED_NFL, 2000), X.boot(tabs["cfb"], X.SEED_CFB, 2000)
            en, dn = X.stat(bn)
            ec, dc = X.stat(bc, X.shares(bn))
            er, dr_ = X.stat(bc)
            R["rebin"][label] = {"edges": edges, "n": {s: tabs[s].n for s in tabs}}
            line = []
            for b in X.BASES:
                r = X.interval(ec["s_" + b] - en["s_" + b], dc["s_" + b] - dn["s_" + b], min(tabs["nfl"].n, tabs["cfb"].n))
                raw = X.interval(er["s_" + b] - en["s_" + b], dr_["s_" + b] - dn["s_" + b], min(tabs["nfl"].n, tabs["cfb"].n))
                r["dropped"] = 2000 - r["draws"]
                R["rebin"][label][b] = {"reweighted": r, "raw": raw}
                R["rebin"][label][b]["share_of_raw_gap_removed"] = 1.0 - r["est"] / raw["est"]
                line.append("Dms_%s %+.4f [%+.4f, %+.4f]%s SE %.4f%s (raw %+.4f, %.0f%% of it removed)"
                            % (b, r["est"], r["lo"], r["hi"], "*" if (r["lo"] > 0 or r["hi"] < 0) else " ", r["se"],
                               " dropped %d" % r["dropped"] if r["dropped"] else "", raw["est"],
                               100 * (1.0 - r["est"] / raw["est"])))
            out("      %-32s n NFL %5d / college %5d | %s" % (label, tabs["nfl"].n, tabs["cfb"].n, " | ".join(line)))
    finally:
        X.BIN_EDGES, X.N_BINS = keep
    out("      mean e inside the two open-ended registered bins (the re-weighting matches bin SHARES, not e inside a bin):")
    for k in (0, nb - 1):
        out("         bin %d: NFL mean e %+.2f (n %d) | college mean e %+.2f (n %d)"
            % (k, tn.e[tn.bin == k].mean(), int((tn.bin == k).sum()), tc.e[tc.bin == k].mean(), int((tc.bin == k).sum())))

    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    out("\nNOT RUN in this invocation: %s" % ("; ".join("%s (%s)" % (n["what"], n["why"]) for n in R["not_run"]) or "nothing"))
    out("wrote %s (%.0fs)" % (a.out, R["seconds"]))


if __name__ == "__main__":
    main()
