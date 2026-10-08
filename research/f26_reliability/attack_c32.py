"""f-26 target adapter: c-32's line-move slope (docs/findings/open-line-move.md).

    CLAIM  Kalshi, NFL 2026 weeks 2-3, 32 games: close-open regressed on
           model-open has slope +0.104 [+0.039, +0.176] (moneyline) and
           +0.106 [+0.027, +0.178] (spread) - "the line moved toward the model".

READ-ONLY. Step 0 is c-32's OWN script re-run on today's store from a detached
worktree of origin/c-32-open-line-move (that is the reproduction):

    cd D:/temp/f26/c32src
    LOGGER_DB=<market_log.db> python -m research.open_line_move --json-out D:/temp/f26/c32_rerun.json

Then, as a script (see attack_c28.py for why not -m):

    python research/f26_reliability/attack_c32.py --src D:/temp/f26/c32src \
        --db D:/calibrated-sports/data/market_log.db --rerun D:/temp/f26/c32_rerun.json \
        --recorded D:/temp/c32/result-final.json --out D:/temp/f26/c32.json
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISHED = {"moneyline": (0.104, 0.039, 0.176), "spread": (0.106, 0.027, 0.178), "total": (-0.026, -0.174, 0.112)}
SLOPES_COUNTED = 12          # c-32's own count ("no multiplicity correction over ~12 slopes")
PERMS = 20000


def slope(x, y):
    if len(x) < 3 or np.std(x) == 0:
        return float("nan")
    return float(np.polyfit(x, y, 1)[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--rerun", required=True)
    ap.add_argument("--recorded", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from jobs import season_model as S
    from models import season as M
    from research import open_line_move as OL

    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-32"}
    new = json.load(open(a.rerun, encoding="utf-8"))
    old = json.load(open(a.recorded, encoding="utf-8"))
    out("re-run store %s; recorded store %s" % (new["versions"], old["versions"]))

    # ------------------------------------------------------------ 1 reproduce
    out("\n== 1. REPRODUCE (c-32's own script, today's store)")
    R["reproduce"] = []
    for mk, (est, lo, hi) in PUBLISHED.items():
        s = new["markets"][mk]["slope"]
        for nm, got, pub in (("slope", s["est"], est), ("lo", s["lo"], lo), ("hi", s["hi"], hi)):
            R["reproduce"].append(L.reproduce("%s %s" % (mk, nm), got, pub, 3))
    for r in R["reproduce"]:
        out("   %-16s measured %+.4f  published %+.3f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    drift = {}
    for mk in PUBLISHED:
        o = {r["game_id"]: r for r in old["per_game"][mk]}
        d = [(abs(r["model"] - o[r["game_id"]]["model"]), abs(r["open"] - o[r["game_id"]]["open"]),
              abs(r["close"] - o[r["game_id"]]["close"])) for r in new["per_game"][mk]]
        drift[mk] = {"games": len(d), "max_abs_model": max(x[0] for x in d), "max_abs_open": max(x[1] for x in d),
                     "max_abs_close": max(x[2] for x in d)}
        out("   %-9s per-game drift since the recorded run: model %.3g, open %.3g, close %.3g"
            % (mk, drift[mk]["max_abs_model"], drift[mk]["max_abs_open"], drift[mk]["max_abs_close"]))
    R["per_game_drift"] = drift

    # independent re-derivation of the moneyline open and close from the quotes table
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games, _grp, _v = S.load(con)
    by_id = {g["game_id"]: g for g in games}
    idx_of = {g["game_id"]: i for i, g in enumerate(games)}
    rows_ml = new["per_game"]["moneyline"]
    worst, widths = 0.0, []
    for r in rows_ml:
        g = by_id[r["game_id"]]
        k = g["kickoff_ts"]
        mids = [m[0] for m in con.execute(
            "SELECT m.market_id FROM markets m JOIN market_outcome mo ON mo.venue = m.venue AND "
            "mo.market_id = m.market_id JOIN outcomes o ON o.outcome_id = mo.outcome_id WHERE m.venue = 'kalshi' "
            "AND m.market_id LIKE 'KXNFLGAME-%' AND o.event_id = ?", (r["game_id"],))]
        team = {m: con.execute("SELECT o.entity_id FROM market_outcome mo JOIN outcomes o ON o.outcome_id = "
                               "mo.outcome_id WHERE mo.venue = 'kalshi' AND mo.market_id = ?", (m,)).fetchone()[0]
                for m in mids}
        so, sc = {}, {}
        for m in mids:
            q = con.execute("SELECT best_bid, best_ask FROM quotes WHERE venue = 'kalshi' AND market_id = ? AND "
                            "source = 'live' AND ts >= ? AND ts < ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
                            "ORDER BY ts LIMIT 1", (m, r["open_ts"], min(r["open_ts"] + 3600, k))).fetchone()
            c = con.execute("SELECT best_bid, best_ask FROM quotes WHERE venue = 'kalshi' AND market_id = ? AND "
                            "source = 'live' AND ts < ? AND ts >= ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
                            "ORDER BY ts DESC LIMIT 1", (m, k, k - 1800)).fetchone()
            if q:
                so[team[m]] = (q[0] + q[1]) / 2
                widths.append(q[1] - q[0])
            if c:
                sc[team[m]] = (c[0] + c[1]) / 2

        def home(s):
            h, aw = s.get(g["home"]), s.get(g["away"])
            return h / (h + aw) if h is not None and aw is not None else (h if h is not None else 1 - aw)
        worst = max(worst, abs(home(so) - r["open"]), abs(home(sc) - r["close"]))
    con.close()
    R["independent_quotes"] = {"games": len(rows_ml), "max_abs_diff_open_or_close": worst,
                               "open_spread_median": float(np.median(widths)), "open_spread_max": float(max(widths))}
    out("   moneyline open and close re-read from quotes by this file: max |diff| %.3g over %d games; "
        "open bid-ask width median %.3f, max %.3f" % (worst, len(rows_ml), np.median(widths), max(widths)))

    R["markets"] = {}
    for mk in ("moneyline", "spread"):
        rows = new["per_game"][mk]
        n = len(rows)
        x = np.array([r["model_minus_open"] for r in rows])
        y = np.array([r["close_minus_open"] for r in rows])
        mdl = np.array([r["model"] for r in rows])
        opn = np.array([r["open"] for r in rows])
        cls = np.array([r["close"] for r in rows])
        gid = [r["game_id"] for r in rows]
        kick = [by_id[g]["kickoff_ts"] for g in gid]
        stat = lambda idx: slope(x[idx], y[idx])  # noqa: E731
        est = stat(np.arange(n))
        T = {"n": n, "est": est}
        out("\n######## %s (n %d, slope %+.4f)" % (mk.upper(), n, est))

        # -------------------------------------------------------- 2 blocks
        out("== 2. BLOCKS")
        # c-32's own boot() takes no block: it resamples ROWS. That is a game bootstrap only
        # while there is exactly one row per game - so assert it, and show what it does otherwise.
        if len(set(gid)) != n:
            raise SystemExit("%s: %d rows but %d games - c-32's row bootstrap is not a game bootstrap" % (mk, n, len(set(gid))))

        def width_of(rws):
            xx = np.array([r["model_minus_open"] for r in rws])
            yy = np.array([r["close_minus_open"] for r in rws])
            r = OL.boot(lambda ix: OL.slope_r(xx[ix], yy[ix])[0], len(rws), 400)
            return r["hi"] - r["lo"]
        dt_ = L.duplication_through(width_of, rows, "game_id", fn_name="OL.boot", units=len(set(gid)))
        T["through"] = dt_
        out("   " + L.through_line(dt_))
        out("   c-32's boot() has no block argument and narrows on ANY repeated row; it is a game bootstrap here "
            "only because rows == distinct games (%d == %d)" % (n, len(set(gid))))
        day = [dt.datetime.fromtimestamp(k, dt.timezone.utc).strftime("%Y-%m-%d") for k in kick]
        slot = [str(k) for k in kick]
        T["alt_blocks"] = L.alt_blocks(stat, n, {"game": gid, "kickoff instant": slot, "kickoff UTC day": day}, seed=22)
        for nm, r in T["alt_blocks"].items():
            out("   %-16s (%2d blocks) %s" % (nm, r["n_blocks"], L.fmt(r, 3)))
        T["seeds"] = L.seeds(stat, n, gid, n_seeds=20)
        out("   20 seeds, game blocks: share excluding zero %.2f; lower bound from %+.3f to %+.3f"
            % (T["seeds"]["share_excluding_zero"], T["seeds"]["lo_min"], T["seeds"]["lo_max"]))

        # -------------------------------------------------------- 3 leakage
        if mk == "moneyline":
            pre, _ = M.run_elo(games, S.best_params(S.game_losses(games, S.grid()), OL.SEASON)[0])
            info, anc = {}, {}
            for i, _p in pre:
                g = games[i]
                h, aw = M.franchise(g["home"]), M.franchise(g["away"])
                anc[i] = max(info.get(h, -1.0), info.get(aw, -1.0))
                info[h] = info[aw] = max(anc[i], g["kickoff_ts"] or 0.0)
            # unplayed-at-fit games are not in `pre`; the ancestry of a target game is its teams' info then
            slack = []
            for r in rows:
                i = idx_of[r["game_id"]]
                if i not in anc:
                    raise SystemExit("%s not walked by run_elo - cannot audit" % r["game_id"])
                slack.append(r["open_ts"] - (anc[i] + OL.FINISH))
            slack = np.array(slack)
            planted = slack.copy()
            planted[0] -= (slack[0] + 3600)                       # an open read an hour before that game ended
            T["leak"] = {"games": n, "open_before_model_inputs_final": int((slack < 0).sum()),
                         "min_slack_h": float(slack.min() / 3600), "planted_fires": bool((planted < 0).sum() == 1)}
            out("== 3. as-of: the model's rating ancestry ends (+5h) before the open quote in %d of %d games "
                "(min slack %.1fh); planted early open -> %s"
                % (int((slack >= 0).sum()), n, slack.min() / 3600, "FIRES" if T["leak"]["planted_fires"] else "BLIND"))

        # -------------------------------------------------------- 4 specifications
        se = float(L.block_boot(stat, L.blocks_of(gid), seed=23).std())
        T["multiplicity"] = L.multiplicity(est, se, (2, SLOPES_COUNTED), n_blocks=len(set(gid)))
        T["mde"] = L.mde_ratio(est, se)
        out("== 4. z %+.2f (p %.4f); Bonferroni k=2 p %.3f, k=%d p %.3f -> %s"
            % (T["multiplicity"]["z"], T["multiplicity"]["p"], T["multiplicity"]["bonferroni"][2]["p_adj"],
               SLOPES_COUNTED, T["multiplicity"]["bonferroni"][SLOPES_COUNTED]["p_adj"],
               "survives" if T["multiplicity"]["bonferroni"][SLOPES_COUNTED]["survives_0.05"] else "DOES NOT SURVIVE"))
        out("== 5. |estimate| / MDE = %.2f%s" % (T["mde"]["ratio"], "  -> AT ITS MDE" if T["mde"]["at_mde"] else ""))

        # -------------------------------------------------------- 6 the null the claim needs
        # x and y share the open. Under "the model knows nothing about THIS game" the slope is
        # not zero; it is whatever a model number drawn from another game produces.
        rng = np.random.default_rng(26)
        perm = np.array([slope(mdl[rng.permutation(n)] - opn, y) for _ in range(PERMS)])
        T["permutation"] = {"perms": PERMS, "null_mean": float(perm.mean()), "null_sd": float(perm.std()),
                            "null_p95": float(np.percentile(perm, 95)), "p_one_sided": float((perm >= est).mean())}
        out("== 6. permutation null (model numbers shuffled across games, %d perms): null slope mean %+.3f, "
            "sd %.3f, 95th pct %+.3f; share >= observed %.4f"
            % (PERMS, perm.mean(), perm.std(), np.percentile(perm, 95), T["permutation"]["p_one_sided"]))

        def joint(idx):
            A = np.column_stack([np.ones(len(idx)), mdl[idx], opn[idx]])
            if np.linalg.matrix_rank(A) < 3:
                return float("nan")
            return float(np.linalg.lstsq(A, y[idx], rcond=None)[0][1])
        jb = L.block_boot(joint, L.blocks_of(gid), seed=24)
        jb = jb[~np.isnan(jb)]
        T["joint_model_coef"] = L.summ(joint(np.arange(n)), jb)
        T["joint_model_coef"]["corr_model_open"] = float(np.corrcoef(mdl, opn)[0, 1])
        out("   close-open on model AND open jointly: coefficient on the model %s  (corr(model, open) %.2f)"
            % (L.fmt(T["joint_model_coef"], 3), T["joint_model_coef"]["corr_model_open"]))
        loo = np.array([slope(np.delete(x, j), np.delete(y, j)) for j in range(n)])
        T["leave_one_out"] = {"min": float(loo.min()), "max": float(loo.max())}
        out("   leave one game out: slope from %+.3f to %+.3f" % (loo.min(), loo.max()))
        _ = cls
        R["markets"][mk] = T

    R["through_verdicts"] = L.require_through(["moneyline", "spread"],
                                              {mk: R["markets"][mk]["through"] for mk in R["markets"]})
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s" % a.out)


if __name__ == "__main__":
    main()
