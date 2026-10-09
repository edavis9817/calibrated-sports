"""f-31 target adapter: c-40's counterfactual ledger, FROM ITS COMMITTED ROWS.

    CLAIM  NFL Board ledger snapshot 2026-10-08T20:16Z, 477 analysed leans (227 missed,
           250 cleared, 2026 weeks 3-4, 31 games), proportional-distance scale: the
           player's own prior mean is the flipping input on 66.1% of missed leans
           (T1 +0.461 over 1/5 [+0.369, +0.557]) and on 68.0% of cleared ones; missed
           minus cleared (T2) -0.019 [-0.099, +0.067]; stated T2 MDE 0.12 (own_mean),
           0.11 (line). Ceiling: 0 of 227 lack a flip; 3 / 51 / 120 within 10/25/50%.

READ-ONLY and ARITHMETIC ONLY. The pipeline is NOT re-run (its fits took 890 s) and
there is NO leakage step: the per-lean flip values are taken as committed. What IS
run: every one of the 80 tests is recomputed from the rows through the target's own
`slice_tests` at its own seed; the blocks test hands rows to that same function; and
the null the claim-about-misses needs - the missed/cleared label permuted within
game - is measured, because 1/5 is a null the model's structure violates by itself.

    python research/f26_reliability/attack_c40.py --src <c-40 worktree> --out <json>
        [--ledger <the unit's ledger snapshot>] [--run1 <dir of the unit's first run>]

`--ledger` (optional) identifies the 19 leans the unit excluded; without it the
exclusion step uses the counts the result file implies and says so.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter

import numpy as np
from scipy import stats as sps

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = {"missed_share": 0.661, "cleared_share": 0.680, "T1": (0.461, 0.369, 0.557), "T2": (-0.019, -0.099, 0.067),
       "n": {"rows": 477, "missed": 227, "cleared": 250, "games": 31}, "caps": {"10%": 3, "25%": 51, "50%": 120},
       "cap_share": {"10%": 0.013, "25%": 0.225, "50%": 0.529}, "no_flip": 0,
       "mde": {"own_mean": 0.12, "line": 0.11}, "family": 80, "t2": 40, "holm_survivors": 8}
PERMS = 5000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--run1", default=None)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    from research import counterfactual_ledger as cl
    if not os.path.abspath(cl.__file__).startswith(src):
        raise SystemExit("research.counterfactual_ledger resolved outside --src: %s" % cl.__file__)
    res = os.path.join(src, "research", "results")
    rec = json.load(open(os.path.join(res, "counterfactual_ledger.json"), encoding="utf-8"))
    rows = json.load(open(os.path.join(res, "counterfactual_ledger_rows.json"), encoding="utf-8"))["rows"]
    if len(rows) < 100:
        raise SystemExit("%d rows in the committed file - refusing" % len(rows))
    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-40", "pipeline_rerun": False, "leakage_step": "NOT RUN"}
    I = cl.INPUTS
    n = len(rows)
    miss = np.array([r["result"] == "missed" for r in rows])
    fi = np.array([I.index(r["flipping_input"]) if r["flipping_input"] else -1 for r in rows])
    game = [r["game_id"] for r in rows]
    units = len(set(game))
    pg = ["%s|%s" % (r["game_id"], r["gsis_id"]) for r in rows]
    out("%d rows; %d missed, %d cleared; %d without a flipping input; %d games, %d weeks, %d players, %d player-games, "
        "%d player-game-markets" % (n, miss.sum(), (~miss).sum(), (fi < 0).sum(), units, len({r["week"] for r in rows}),
                                    len({r["gsis_id"] for r in rows}), len(set(pg)),
                                    len({(p, r["market"]) for p, r in zip(pg, rows)})))

    out("\n== 1. REPRODUCE from the committed rows (pipeline NOT re-run), through cl.slice_tests at seed %d" % cl.SEED)
    rng = np.random.default_rng(cl.SEED)
    slices = [("all", rows)] + [("market=%s" % m, [r for r in rows if r["market"] == m]) for m in ("receptions", "rush_attempts")]
    slices += [("side=%s" % s, [r for r in rows if r["side"] == s]) for s in ("over", "under")]
    slices += [("band=%s" % b, [r for r in rows if r["band"] == b]) for b, _, _ in cl.BANDS]
    summaries, tests = [], []
    for nm, rs in slices:
        s, t = cl.slice_tests(nm, rs, rng)
        summaries.append(s)
        tests += t
    cl.holm(tests)
    key = lambda t: (t["slice"], t["test"], t["input"])  # noqa: E731
    mine, theirs = {key(t): t for t in tests}, {key(t): t for t in rec["tests"]}
    if set(mine) != set(theirs):
        raise SystemExit("the recomputed test keys are not the result file's")
    close = lambda x, y: (x is None and y is None) or (x is not None and y is not None and abs(x - y) < 1e-9)  # noqa: E731
    differ = [k for k in mine if not all(close(mine[k][f], theirs[k][f]) for f in ("estimate", "lo", "hi", "p", "p_holm"))]
    R["tests_recomputed"] = {"n": len(mine), "differ_from_result_file": len(differ)}
    out("   %d tests recomputed; %d differ from the committed json in estimate, bounds, p or Holm p" % (len(mine), len(differ)))
    t1, t2 = mine[("all", "T1", "own_mean")], mine[("all", "T2", "own_mean")]
    s0 = summaries[0]
    ceil_m, ceil_c = cl.ceiling([r for r in rows if r["result"] == "missed"]), cl.ceiling([r for r in rows if r["result"] == "cleared"])
    caps = {c["cap"]: c for c in ceil_m["caps"]}
    R["reproduce"] = [L.reproduce("rows", n, PUB["n"]["rows"], 0), L.reproduce("missed", int(miss.sum()), PUB["n"]["missed"], 0),
                      L.reproduce("cleared", int((~miss).sum()), PUB["n"]["cleared"], 0), L.reproduce("games", units, PUB["n"]["games"], 0),
                      L.reproduce("own_mean share, missed", s0["missed"]["share"]["own_mean"], PUB["missed_share"], 3),
                      L.reproduce("own_mean share, cleared", s0["cleared"]["share"]["own_mean"], PUB["cleared_share"], 3)]
    for nm, t, pub in (("T1 own_mean", t1, PUB["T1"]), ("T2 own_mean", t2, PUB["T2"])):
        for part, v, p in zip(("est", "lo", "hi"), (t["estimate"], t["lo"], t["hi"]), pub):
            R["reproduce"].append(L.reproduce("%s %s" % (nm, part), v, p, 3))
    R["reproduce"].append(L.reproduce("missed with no single-input flip", ceil_m["no_single_input_flip"], PUB["no_flip"], 0))
    for c, k in PUB["caps"].items():
        R["reproduce"].append(L.reproduce("missed flipped within %s" % c, caps[c]["n_reached"], k, 0))
        R["reproduce"].append(L.reproduce("  share within %s" % c, caps[c]["share_reached"], PUB["cap_share"][c], 3))
    for r in R["reproduce"]:
        out("   %-34s measured %+.4f  published %+.4f -> %s" % (r["name"], r["measured"], r["published"],
                                                              "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    v = cl.verdict(tests)
    out("   verdict by the registered rule: %s (file: %s)" % (v, rec["verdict"]))

    out("\n== 2a. BLOCKS: THROUGH cl.slice_tests (the function behind every published interval)")
    def width(kind):
        def w(rr):
            _s, tt = cl.slice_tests("dup", rr, np.random.default_rng(cl.SEED))
            t = next(x for x in tt if x["test"] == kind and x["input"] == "own_mean")
            return t["hi"] - t["lo"]
        return w
    R["through"] = {k: L.duplication_through(width(k), rows, "game_id", fn_name="counterfactual_ledger.slice_tests", units=units)
                    for k in ("T1", "T2")}
    for k, t in R["through"].items():
        out("   %s own_mean  %s" % (k, L.through_line(t)))
    R["through_verdicts"] = L.require_through(["T1", "T2"], R["through"])

    def stat(kind, inp):
        j = I.index(inp)
        def f(idx):
            m, x = miss[idx], fi[idx]
            ok = x >= 0
            a_, b_ = (m & ok).sum(), (~m & ok).sum()
            if a_ == 0 or (kind == "T2" and b_ == 0):
                return float("nan")
            sm = ((x == j) & m).sum() / a_
            return float(sm - 1.0 / len(I)) if kind == "T1" else float(sm - ((x == j) & ~m).sum() / b_)
        return f
    labelings = {"game": game, "week": [r["week"] for r in rows], "player": [r["gsis_id"] for r in rows],
                 "player-game": pg, "team": [r["team"] for r in rows]}
    R["alt_blocks"], R["iid_contrast"] = {}, {}
    for k in ("T1", "T2"):
        st = stat(k, "own_mean")
        R["iid_contrast"][k] = L.iid_contrast(st, n, game, seed=11)
        out("   %s descriptive: %d rows in %d games; an unblocked interval would be x%.2f the width"
            % (k, n, units, R["iid_contrast"][k]["iid_over_blocked_width"]))
        R["alt_blocks"][k] = L.alt_blocks(st, n, labelings, seed=12)
        for nm, r in R["alt_blocks"][k].items():
            out("   %s %-12s (%3d blocks) %s" % (k, nm, r["n_blocks"], L.fmt(r, 3)))

    out("\n== 2b. THE NULL THE CLAIM NEEDS: 1/5 is not what the model's structure gives")
    ok = fi >= 0
    pooled = {i: float((fi[ok] == j).mean()) for j, i in enumerate(I)}
    out("   share of ALL %d analysed leans, outcome ignored: %s" % (ok.sum(), ", ".join("%s %.3f" % kv for kv in pooled.items())))
    R["pooled_share"] = pooled
    blocks = L.blocks_of(game)
    R["permutation"] = {}
    for scheme in ("within game", "unrestricted"):
        rng = np.random.default_rng(31)
        sm = np.empty((PERMS, len(I)))
        df = np.empty((PERMS, len(I)))
        for d in range(PERMS):
            lab = miss.copy()
            if scheme == "within game":
                for b in blocks:
                    lab[b] = miss[b][rng.permutation(len(b))]
            else:
                lab = miss[rng.permutation(n)]
            for j in range(len(I)):
                sm[d, j] = (fi[lab] == j).mean()
                df[d, j] = sm[d, j] - (fi[~lab] == j).mean()
        R["permutation"][scheme] = {}
        for j, i in enumerate(I):
            obs_s, obs_d = float((fi[miss] == j).mean()), float((fi[miss] == j).mean() - (fi[~miss] == j).mean())
            p = float((1 + (np.abs(df[:, j]) >= abs(obs_d) - 1e-12).sum()) / (1 + PERMS))
            lo, hi = np.percentile(sm[:, j], [2.5, 97.5])
            R["permutation"][scheme][i] = {"observed_missed_share": obs_s, "null_mean": float(sm[:, j].mean()), "null_lo": float(lo),
                                           "null_hi": float(hi), "observed_percentile": float((sm[:, j] <= obs_s).mean()),
                                           "observed_diff": obs_d, "p_two_sided": p}
            out("   %-12s %-10s missed share %.3f; label-permuted null %.3f [%.3f, %.3f]; observed at the %.0fth pct; "
                "missed-cleared %+.3f, permutation p %.3f" % (scheme, i, obs_s, sm[:, j].mean(), lo, hi,
                                                              100 * (sm[:, j] <= obs_s).mean(), obs_d, p))
    out("   (rows of one player share an outcome and a flipping input, so a row permutation is ANTI-conservative; "
        "a p that is large here is large a fortiori)")

    out("\n== 3. LEAKAGE: NOT RUN. A flip value is arithmetic on a fit; the fits were not rebuilt here.")

    out("\n== 4. SPECIFICATIONS")
    kinds = Counter(t["test"] for t in rec["tests"])
    flat = sum(1 for t in rec["tests"] if t["p"] == 1.0 and t.get("note"))
    surv = [key(t) for t in rec["tests"] if t["p_holm"] < 0.05]
    t2p = sorted((t["p"], key(t)) for t in rec["tests"] if t["test"] == "T2")
    R["spec_count"] = {"registered_intervals_key_present": "registered_intervals" in rec, "tests_in_result_file": len(rec["tests"]),
                       "by_kind": dict(kinds), "entered_at_p1": flat, "holm_survivors": len(surv),
                       "t2_below_0.05_unadjusted": sum(1 for p, _ in t2p if p < 0.05), "t2_min_p": t2p[0]}
    out("   result file: NO registered_intervals.count; it lists %d tests (%s); pre-registration says %d / %d T2 -> %s"
        % (len(rec["tests"]), dict(kinds), PUB["family"], PUB["t2"],
           "MATCH" if len(rec["tests"]) == PUB["family"] and kinds["T2"] == PUB["t2"] else "MISMATCH"))
    out("   %d tests entered at p = 1 with a note; Holm survivors %d (published %d): %s" % (flat, len(surv), PUB["holm_survivors"],
        sorted({"%s %s" % (k[1], k[2]) for k in surv})))
    out("   T2 tests below p 0.05 unadjusted: %d; smallest %.4f at %s" % (R["spec_count"]["t2_below_0.05_unadjusted"], t2p[0][0], t2p[0][1]))
    R["multiplicity"] = {}
    for k, t in (("T1", t1), ("T2", t2)):
        mu = L.multiplicity(t["estimate"], t["se"], (len(rec["tests"]), 1000), n_blocks=units)
        R["multiplicity"][k] = dict(mu, se=t["se"])
        out("   %s own_mean z %+.2f  Bonferroni survives k=%d %s, k=1000 %s" % (k, mu["z"], len(rec["tests"]),
            mu["bonferroni"][len(rec["tests"])]["survives_0.05"], mu["bonferroni"][1000]["survives_0.05"]))
    if a.run1:
        r1 = json.load(open(os.path.join(a.run1, "counterfactual_ledger.json"), encoding="utf-8"))
        h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731
        same_rows = h(os.path.join(a.run1, "counterfactual_ledger_rows.json")) == h(os.path.join(res, "counterfactual_ledger_rows.json"))
        o = {key(t): t for t in r1["tests"]}
        R["run1"] = {"rows_file_identical": same_rows,
                     "estimates_differ": sum(1 for k in o if abs(o[k]["estimate"] - theirs[k]["estimate"]) > 1e-12),
                     "p_differ": sum(1 for k in o if o[k]["p"] != theirs[k]["p"]),
                     "p_zero_in_run1": sum(1 for k in o if o[k]["p"] == 0.0),
                     "holm_survivors_run1": sum(1 for k in o if o[k]["p_holm"] < 0.05), "verdict_run1": r1["verdict"]}
        out("   the unit's FIRST run (scratch): %s" % R["run1"])

    out("\n== 5. MDE")
    R["mde"] = {}
    for inp in ("own_mean", "line"):
        t = mine[("all", "T2", inp)]
        R["mde"][inp] = {"ratio": L.mde_ratio(t["estimate"], t["se"]), "claim": L.mde_claim(PUB["mde"][inp], t["se"]),
                         "alt_se": {nm: float(L.block_boot(stat("T2", inp), L.blocks_of(lb), seed=13).std())
                                    for nm, lb in labelings.items() if nm != "week"}}
        m = R["mde"][inp]
        out("   T2 %-8s est %+.3f SE %.4f  |est|/MDE %.2f (%s); stated MDE %.2f vs 2.8 x SE %.3f -> %s; 2.8 x SE under other blocks: %s"
            % (inp, t["estimate"], t["se"], m["ratio"]["ratio"], m["ratio"]["reading"], PUB["mde"][inp], m["claim"]["remeasured"],
               "consistent" if m["claim"]["consistent"] else "INCONSISTENT", ", ".join("%s %.3f" % (k, 2.8 * s) for k, s in m["alt_se"].items())))
    m1 = L.mde_ratio(t1["estimate"], t1["se"])
    out("   T1 own_mean |est|/MDE %.2f (%s) - against the 1/5 null only" % (m1["ratio"], m1["reading"]))

    out("\n== 6. THE EXCLUDED LEANS")
    pop = rec["population"]
    ex_m, ex_c = pop["missed"] - int(miss.sum()), pop["cleared"] - int((~miss).sum())
    src_note = "implied by the result file's population counts"
    if a.ledger:
        import polars as pl
        body = cl.R.build_published(pl.read_parquet(a.ledger).to_dicts(), time.time())
        by = {x["lean_id"]: x for x in body["leans"]}
        bad = [by[f["lean_id"]] for f in rec["gate"]["failed"]]
        ex_m, ex_c = sum(x["result"] == "missed" for x in bad), sum(x["result"] == "cleared" for x in bad)
        src_note = "read from the ledger snapshot (sha256 %s)" % hashlib.sha256(open(a.ledger, "rb").read()).hexdigest()[:12]
        R["excluded_detail"] = {"games": len({x["game_id"] for x in bad}), "players": len({x["gsis_id"] for x in bad}),
                                "by_market_side": dict(Counter("%s|%s" % (x["market"], x["side"]) for x in bad)),
                                "by_week": dict(Counter(x["week"] for x in bad))}
        out("   the %d excluded: %s" % (len(bad), R["excluded_detail"]))
    odds, pf = sps.fisher_exact([[ex_m, ex_c], [int(miss.sum()), int((~miss).sum())]])
    R["excluded"] = {"missed": ex_m, "cleared": ex_c, "source": src_note, "fisher_odds": float(odds), "fisher_p": float(pf), "bounds": {}}
    out("   excluded %d missed / %d cleared (%s) against %d / %d analysed: odds ratio %.2f, Fisher exact p %.3f"
        % (ex_m, ex_c, src_note, miss.sum(), (~miss).sum(), odds, pf))
    for j, i in enumerate(I):
        cm, cc = int(((fi == j) & miss).sum()), int(((fi == j) & ~miss).sum())
        nm_, nc_ = int(miss.sum()) + ex_m, int((~miss).sum()) + ex_c
        lo, hi = cm / nm_ - (cc + ex_c) / nc_, (cm + ex_m) / nm_ - cc / nc_
        R["excluded"]["bounds"][i] = {"lo": lo, "hi": hi}
        out("   %-10s missed-cleared with all %d included: between %+.3f and %+.3f (analysed %+.3f)"
            % (i, ex_m + ex_c, lo, hi, cm / miss.sum() - cc / (~miss).sum()))

    out("\n== 7. THE SPECIFICATION NOBODY REGISTERED (attacker's, descriptive): distance, not the argmin")
    dmin = np.array([min(r["flip"][i]["distance"] for i in I if r["flip"][i] is not None) for r in rows])
    for nm, f in (("mean log distance to the nearest flip, missed - cleared", lambda idx: float(dmin[idx][miss[idx]].mean() - dmin[idx][~miss[idx]].mean())),
                  ("share within a 25% change, missed - cleared", lambda idx: float((dmin[idx][miss[idx]] <= np.log(1.25)).mean()
                                                                                   - (dmin[idx][~miss[idx]] <= np.log(1.25)).mean()))):
        r = L.summ(f(np.arange(n)), L.block_boot(f, blocks, seed=17))
        R.setdefault("distance_contrast", {})[nm] = r
        out("   %-58s %s  (game blocks; 1 of 2 unregistered looks)" % (nm, L.fmt(r, 3)))
    out("   medians: missed %.1f%%, cleared %.1f%% proportional change" % (100 * (np.exp(np.median(dmin[miss])) - 1),
                                                                         100 * (np.exp(np.median(dmin[~miss])) - 1)))
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(R, fo, indent=1, default=str)
    out("\nwrote %s" % a.out)


if __name__ == "__main__":
    main()
