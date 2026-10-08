"""f-26 target adapter: c-30's spread record, as a-64 publishes it in game/nfl/record_spread.json.

    CLAIMS (cover of the nflverse closing spread, 2001-2025, one row per game)
      registered  dMCB(E - N0) on lines of exactly 3: +0.0010 [+0.0005, +0.0014]
                  -> "the key-number margin is WORSE calibrated than the Normal on 3"
      published   pooled dBrier(E - N0) +0.0009 [+0.0003, +0.0016], 6,580 games
      published   dBrier(E - book cover price) +0.0081 [+0.0054, +0.0107], 5,162 games

READ-ONLY. c-30's main() is driven from a detached worktree of
origin/c-30-against-the-spread up to its first bootstrap and its rows captured:

    LOGGER_DB=<market_log.db> python research/f26_reliability/attack_c30.py \
        --src D:/temp/f26/c30src --db <market_log.db> --recorded D:/temp/c30/result.json --out D:/temp/f26/c30.json
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
PUB = {"on3_dMCB": (0.0010, 0.0005, 0.0014), "pooled_dBrier": (0.0009, 0.0003, 0.0016),
       "book_dBrier": (0.0081, 0.0054, 0.0107)}
PUB_N = {"cover_rows": 6580, "on 3": 985, "book": 5162}


class _Stop(Exception):
    pass


class _Anything:
    def __ne__(self, other):
        return False

    def __eq__(self, other):
        return True

    def __repr__(self):
        return "<identity check disabled for a scrambled run>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--recorded", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cutoffs", type=int, default=3)
    a = ap.parse_args()
    if not os.environ.get("LOGGER_DB"):
        raise SystemExit("LOGGER_DB is not set - c-30's config would fall back to a relative path")
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from jobs import season_model as S
    from research import against_the_spread as ATS
    from research import game_forecast as GF
    from research import ranking_calibration as rc
    if not os.path.abspath(ATS.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.against_the_spread resolved outside --src: %s" % ATS.__file__)
    orig = {"load": S.load, "boot_rows": ATS.boot_rows, "c28": ATS.C28_P1_BRIER}
    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-30 (published by a-64)"}
    t0 = time.time()
    n_cells = len(ATS.STRATA) + 1

    def capture(mut_games=None):
        """-> ({game: {y, E, N0, N1, stat}}, games) from c-30's first ten boot_rows calls."""
        calls, keep = [], {}

        def load(con):
            games, grp, ver = orig["load"](con)
            keep["games"] = games if mut_games is None else mut_games(games)
            return keep["games"], grp, ver

        def boot_rows(name, rows, fns, draws):
            calls.append((name, rows))
            if len(calls) == 2 * n_cells:
                raise _Stop()
            pop = rc.Pop(name, rows, "m", "k")
            return pop, {k: {"est": v, "lo": -1.0, "hi": 1.0, "se": 1.0, "games": pop.games, "n": pop.n, "mde": 2.8}
                         for k, v in fns(pop, np.arange(pop.n)).items()}

        S.load, ATS.boot_rows = load, boot_rows
        ATS.C28_P1_BRIER = _Anything() if mut_games is not None else orig["c28"]
        saved = sys.stdout
        try:
            sys.stdout = open(os.devnull, "w")
            try:
                ATS.main(["--json-out", os.devnull, "--draws", "1"])
            finally:
                sys.stdout.close()
                sys.stdout = saved
        except _Stop:
            pass
        finally:
            S.load, ATS.boot_rows, ATS.C28_P1_BRIER = orig["load"], orig["boot_rows"], orig["c28"]
        if len(calls) != 2 * n_cells:
            raise SystemExit("captured %d of %d Part-1 populations" % (len(calls), 2 * n_cells))
        cells = list(ATS.STRATA) + ["pooled"]
        got = {}
        for j, cell in enumerate(cells):
            (n0name, r0), (n1name, r1) = calls[2 * j], calls[2 * j + 1]
            if n0name != cell or n1name != cell or [r["game"] for r in r0] != [r["game"] for r in r1]:
                raise SystemExit("c-30's call order is not (cell, N0), (cell, N1): %s %s" % (n0name, n1name))
            for x, z in zip(r0, r1):
                if cell == "pooled":
                    got.setdefault(x["game"], {}).update(y=x["y"], E=x["m"], N0=x["k"], N1=z["k"])
                else:
                    got.setdefault(x["game"], {})["stat"] = cell
        return got, keep["games"]

    base, games = capture()
    by_id = {g["game_id"]: g for g in games}
    gid = sorted(base)
    n = len(gid)
    y = np.array([base[g]["y"] for g in gid])
    E = np.array([base[g]["E"] for g in gid])
    N0 = np.array([base[g]["N0"] for g in gid])
    st = np.array([base[g]["stat"] for g in gid])
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    odds = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT g.game_id, g.home_spread_odds, g.away_spread_odds FROM nfl_games g JOIN (SELECT game_id, "
        "MAX(data_version) dv FROM nfl_games GROUP BY game_id) v ON v.game_id = g.game_id AND v.dv = g.data_version")}
    con.close()
    bj = np.array([j for j, g in enumerate(gid) if by_id[g]["season"] >= ATS.ODDS_FROM
                   and odds[g][0] is not None and odds[g][1] is not None])
    ih = np.array([GF.american(odds[gid[j]][0]) for j in bj])
    ia = np.array([GF.american(odds[gid[j]][1]) for j in bj])
    book = ih / (ih + ia)
    j3 = np.where(st == "on 3")[0]
    out("captured %d cover rows (%d on 3; %d with a book price) in %.0fs" % (n, len(j3), len(bj), time.time() - t0))

    d_pool = (E - y) ** 2 - (N0 - y) ** 2
    d_book = (E[bj] - y[bj]) ** 2 - (book - y[bj]) ** 2
    T = {
        "on3_dMCB": (lambda idx: float(rc.corp(E[j3[idx]], y[j3[idx]])["mcb"] - rc.corp(N0[j3[idx]], y[j3[idx]])["mcb"]),
                     [gid[j] for j in j3]),
        "pooled_dBrier": (lambda idx: float(d_pool[idx].mean()), gid),
        "book_dBrier": (lambda idx: float(d_book[idx].mean()), [gid[j] for j in bj]),
    }

    # ------------------------------------------------------------ 1 reproduce
    out("\n== 1. REPRODUCE (c-30's code, today's store; book price re-derived here from nfl_games)")
    R["reproduce"] = [L.reproduce("cover rows", n, PUB_N["cover_rows"], 0), L.reproduce("rows on 3", len(j3), PUB_N["on 3"], 0),
                      L.reproduce("rows with a book price", len(bj), PUB_N["book"], 0)]
    reg = {}
    for nm, (stat, lab) in T.items():
        reg[nm] = L.summ(stat(np.arange(len(lab))), L.block_boot(stat, L.blocks_of(lab), draws=rc.BOOT, seed=rc.SEED))
        for part, got_, pub in zip(("est", "lo", "hi"), (reg[nm]["est"], reg[nm]["lo"], reg[nm]["hi"]), PUB[nm]):
            R["reproduce"].append(L.reproduce("%s %s" % (nm, part), got_, pub, 4))
    R["registered_interval_redrawn"] = reg
    for r in R["reproduce"]:
        out("   %-24s measured %+.5f  published %+.4f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))

    # ------------------------------------------------------------ 2 blocks, 4 specs, 5 MDE
    rec = json.load(open(a.recorded, encoding="utf-8"))
    k_reg = (rec.get("registered_intervals") or {}).get("count") or 0
    R["registered_intervals_c30"] = k_reg
    R["claims"] = {}
    for nm, (stat, lab) in T.items():
        m_ = len(lab)
        C = {}
        out("\n######## %s (n %d)  %s" % (nm, m_, L.fmt(reg[nm])))
        du = L.duplication(stat, m_, lab, seed=41, draws=1000 if nm == "on3_dMCB" else 2000)
        C["duplication"] = du
        out("== 2. rows x5 inside their game: blocked width x%.3f (%s); iid width x%.3f (expected %.3f; check %s)"
            % (du["width_ratio_blocked"], "passes" if du["passes"] else "NARROWED", du["width_ratio_iid"],
               du["expected_iid_ratio"], "discriminates" if du["discriminates"] else "DOES NOT DISCRIMINATE"))
        C["alt_blocks"] = L.alt_blocks(stat, m_, {
            "game": lab, "season-week": ["%d-%02d" % (by_id[g]["season"], by_id[g]["week"]) for g in lab],
            "season": [by_id[g]["season"] for g in lab]}, seed=42, draws=1000 if nm == "on3_dMCB" else 2000)
        for b, r in C["alt_blocks"].items():
            out("   %-12s (%4d blocks) %s" % (b, r["n_blocks"], L.fmt(r)))
        C["seeds"] = L.seeds(stat, m_, lab, n_seeds=10, draws=1000 if nm == "on3_dMCB" else 2000)
        out("   10 seeds: share excluding zero %.2f; lower bound from %+.5f to %+.5f"
            % (C["seeds"]["share_excluding_zero"], C["seeds"]["lo_min"], C["seeds"]["lo_max"]))
        C["multiplicity"] = L.multiplicity(reg[nm]["est"], reg[nm]["se"], (2, max(k_reg, 2)))
        C["mde"] = L.mde_ratio(reg[nm]["est"], reg[nm]["se"])
        mu = C["multiplicity"]
        out("== 4. z %+.2f (p %.5f); Bonferroni over c-30's own %d registered intervals: p %.4f -> %s"
            % (mu["z"], mu["p"], k_reg, mu["bonferroni"][max(k_reg, 2)]["p_adj"],
               "survives" if mu["bonferroni"][max(k_reg, 2)]["survives_0.05"] else "DOES NOT SURVIVE"))
        out("== 5. |estimate| / MDE = %.2f%s" % (C["mde"]["ratio"], "  -> AT ITS MDE" if C["mde"]["at_mde"] else ""))
        R["claims"][nm] = C

    # ------------------------------------------------------------ 3 leakage
    out("\n== 3. LEAKAGE: scramble every result at or after a cutoff; no earlier forecast (E, N0, N1) may move")
    kicks = sorted({by_id[g]["kickoff_ts"] for g in gid})
    R["leak"] = []
    for c in [kicks[int(q * (len(kicks) - 1))] for q in np.linspace(0.25, 0.9, a.cutoffs)]:
        rng = np.random.default_rng(int(c) % 9973)

        def mg(gs, c=c, rng=rng):
            g2 = copy.deepcopy(gs)
            for g in g2:
                if g["kickoff_ts"] is not None and g["kickoff_ts"] >= c and g["home_score"] is not None:
                    g["home_score"], g["away_score"] = g["away_score"] + int(rng.integers(0, 9)), g["home_score"]
            return g2

        got, _g = capture(mut_games=mg)
        checked = moved = 0
        for g, v in got.items():
            if g in base and by_id[g]["kickoff_ts"] <= c:
                checked += 1
                moved += any(abs(v[k] - base[g][k]) > 1e-12 for k in ("E", "N0", "N1"))
        # the same comparison on games strictly AFTER the cutoff must move, or the check is blind
        after = sum(1 for g, v in got.items() if g in base and by_id[g]["kickoff_ts"] > c
                    and any(abs(v[k] - base[g][k]) > 1e-12 for k in ("E", "N0", "N1")))
        R["leak"].append({"cutoff_ts": c, "forecasts_checked": checked, "moved": moved, "later_forecasts_moved": after})
        out("   cutoff %d: %d earlier forecasts x 3 arms checked, %d moved; %d LATER forecasts moved (%s)"
            % (c, checked, moved, after, "the check can see a change" if after else "CHECK IS BLIND"))

    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s (%.0fs)" % (a.out, R["seconds"]))


if __name__ == "__main__":
    main()
