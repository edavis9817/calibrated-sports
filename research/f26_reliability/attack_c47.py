"""f-35 target adapter: c-47, c-41's residual-given-the-line frame on receptions and rush attempts
(research/residual_two_markets.py).

    CLAIM  A NULL WITH THREE STATES. NFL receptions and rush attempts, over side, REG 2023-2025, de-vigged
           DK/FD/MGM bench price ~14 min before kickoff, c-41's six linear candidates: 0 of 12 cells
           detected, 0 of 84 tests after Holm. 0.020 per sd "ruled out" in ONE cell (receptions line_pos,
           Bonferroni interval [-0.0177, +0.0093], pre-run power 0.98). Rush-attempts form_gap UNRESOLVED
           +0.0201 [+0.0033, +0.0368]; receptions book_gap +0.0140 [+0.0037, +0.0246], Holm p 0.64.

READ-ONLY, ROWS-IN. This file opens no store and no network. It takes the per-rung rows the target's own
script wrote (--out-dir), and the committed result / power JSON in the target worktree:

    python research/f26_reliability/attack_c47.py --src <c-47 worktree> --rows-dir <scratch>/rows \
        --scratch <unit scratch dir, listed only> --out <scratch>/c47.json [--plant-reps 60] [--boot-reps 40]

WHAT IS RUN  1 reproduce (target's prepare/set_y/analyse_market/holm/verdict on the rows vs the committed
             json, all 98 tests); 2 blocks THROUGH M.coef_test / M.brier_test with M._cut's weights;
             3 leakage at FUNCTION level only; 4 specification count + file/commit times; 5 MDE;
             6a POWER - a synthetic candidate of known size in the slot of a real null candidate, REAL
             outcomes, through the target's analyse_market + holm + verdict; 6b the line_pos exclusion on
             game-resampled real rows with an outcome plant on the REAL line_pos column.
WHAT IS NOT  build() is not re-run (no store): whether the rows equal what the store holds, and any
             store-level leakage, are NOT CHECKED. The unit's own 600-rep simulation is not re-drawn.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = {"rush_form_b": 0.0201, "rush_form_lo": 0.0033, "rush_form_hi": 0.0368, "rush_form_bonf_hi": 0.0494,
       "rec_book_b": 0.0140, "rec_book_lo": 0.0037, "rec_book_hi": 0.0246, "rec_book_holm": 0.64,
       "rec_line_bonf_lo": -0.0177, "rec_line_bonf_hi": 0.0093, "rec_a_brier": -0.00058, "rec_a_mde": 0.00040,
       "rush_pg": 3654, "rush_rungs": 4581, "rush_qb": 1408, "void": 14, "registered": 84, "specs": 98}
SIM = {"rush_attempts": {"0.020": "<=0.12", "0.033": "0.07-0.50", "0.045": "0.18-0.31 (five of six)"},
       "receptions": {"0.020": "0.06-0.28", "0.033": "0.33-0.84", "0.045": "0.69-1.00"}}
SLOT = "log_line"
F = ("est", "se", "lo", "hi", "p")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--rows-dir", required=True)
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plant-reps", type=int, default=0)
    ap.add_argument("--boot-reps", type=int, default=0)
    ap.add_argument("--budget-s", type=float, default=450.0)
    a = ap.parse_args()
    t_start = time.time()
    left = lambda: a.budget_s - (time.time() - t_start)  # noqa: E731
    sys.path.insert(0, HERE)
    import f26lib as L
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    from research import residual_two_markets as M
    from research import residual_given_line as c41
    for mod in (M, c41):
        if not os.path.abspath(mod.__file__).startswith(src):
            raise SystemExit("%s resolved outside --src: %s" % (mod.__name__, mod.__file__))
    out = lambda s="": print(s, flush=True)  # noqa: E731
    rec = json.load(open(os.path.join(src, "research", "results", "residual_two_markets.json"), encoding="utf-8"))
    pw_file = json.load(open(os.path.join(src, "research", "results", "residual_two_markets_power.json"), encoding="utf-8"))
    power = pw_file["power"]
    MK = list(M.MARKETS)
    recs = {m: json.load(open(os.path.join(a.rows_dir, "rows_%s.json" % m), encoding="utf-8")) for m in MK}
    for m in MK:
        if len(recs[m]) < 1000:
            raise SystemExit("only %d rows for %s - refusing" % (len(recs[m]), m))
    R = {"target": "c-47", "rows_dir": os.path.abspath(a.rows_dir)}
    key = lambda t: (t["market"], t["kind"], t["pop"], t["cut"], t["cand"])  # noqa: E731

    # ------------------------------------------------------------------ 1
    out("== 1. REPRODUCE (target's prepare + set_y + analyse_market + holm + verdict on %s)" % a.rows_dir)
    R["counts"] = {}
    for m in MK:
        rr, c = recs[m], rec["counts"][m]
        n, pg, g = len(rr), len({(d["gsis"], d["event"]) for d in rr}), len({d["event"] for d in rr})
        qb = sum(d["pos"] == "QB" for d in rr)
        ok = (n, pg, g) == (c["rb_rungs"], c["rb_player_games"], c["rb_games"])
        R["counts"][m] = {"rungs": n, "player_games": pg, "games": g, "qb_rungs": qb, "equal_committed": ok}
        out("   %-13s counted here: rungs %d (committed %d), player-games %d (%d), games %d (%d), QB rungs %d -> %s"
            % (m, n, c["rb_rungs"], pg, c["rb_player_games"], g, c["rb_games"], qb, "equal" if ok else "DIFFER"))
    ra = rec["counts"]["rush_attempts"]
    void = sum(rec["counts"][m].get("drop_void", 0) for m in MK)
    out("   committed counts: rush bench rungs %d, QB of RB rungs %d of %d = %.1f%% (unit: 1,408 of 4,581, 31%%); "
        "void drops %d (unit: %d)" % (ra["bench_rungs"], ra["rb_rungs_QB"], ra["rb_rungs"],
                                      100.0 * ra["rb_rungs_QB"] / ra["rb_rungs"], void, PUB["void"]))
    out("   whether these rows equal what the store holds: NOT CHECKED (rows-in; no store opened)")
    preps, tests = {}, []
    for i, m in enumerate(MK):
        prep = M.prepare(recs[m], [M.SEED, i])
        M.set_y(prep, [d["y"] for d in recs[m]])
        mt = M.analyse_market(prep)
        for t in mt:
            t["market"] = m
        preps[m] = prep
        tests += mt
    reg = [t for t in tests if t["registered"]]
    for t, adj in zip(reg, c41.holm([t["p"] for t in reg])):
        t["p_holm"] = adj
    verd = M.verdict(tests, power)
    mine, theirs = {key(t): t for t in tests}, {key(t): t for t in rec["tests"]}
    if set(mine) != set(theirs) or len(theirs) != len(rec["tests"]):
        raise SystemExit("test keys differ between the re-run and the committed json")
    worst = max(abs(mine[k][f] - theirs[k][f]) for k in mine for f in F)
    differ = sum(any(abs(mine[k][f] - theirs[k][f]) > 1e-9 for f in F) for k in mine)
    differ_reg = sum(any(abs(mine[k][f] - theirs[k][f]) > 1e-9 for f in F + ("p_holm",)) for k in mine if mine[k]["registered"])
    verd_eq = json.loads(json.dumps(verd)) == rec["verdict"]
    states = {m: {c: verd[m][c]["state"] for c in M.CANDIDATES} for m in MK}
    n_det = sum(verd[m][c]["detected"] for m in MK for c in M.CANDIDATES)
    n_holm = sum(t["p_holm"] < 0.05 for t in reg)
    out("   %d tests recomputed (%d registered): %d differ from the committed json at 1e-9 over est/se/lo/hi/p "
        "(registered incl. Holm p: %d); largest |difference| %.3g; verdict dict equal: %s"
        % (len(mine), len(reg), differ, differ_reg, worst, verd_eq))
    out("   detected cells %d of 12 (published 0 of 12); registered tests with Holm p < 0.05: %d of %d (published 0 of 84)"
        % (n_det, n_holm, len(reg)))
    for m in MK:
        out("   %-13s states: %s" % (m, ", ".join("%s=%s" % (c, states[m][c]) for c in M.CANDIDATES)))
    n20 = sum(verd[m][c]["may_say_rules_out_0020"] for m in MK for c in M.CANDIDATES)
    out("   cells where 0.020 is excluded: %d (published 1: receptions line_pos); excluded at <=0.033: receptions %d of 6, "
        "rush %s; rush cells not excluded at 0.045: %s"
        % (n20, sum(verd["receptions"][c]["smallest_size_excluded"] in (0.02, 0.033) for c in M.CANDIDATES),
           [c for c in M.CANDIDATES if verd["rush_attempts"][c]["smallest_size_excluded"] in (0.02, 0.033)],
           [c for c in M.CANDIDATES if verd["rush_attempts"][c]["smallest_size_excluded"] is None]))
    rf = mine[("rush_attempts", "coef", "RB", "pooled", "form_gap")]
    rbk = mine[("receptions", "coef", "RB", "pooled", "book_gap")]
    rl = mine[("receptions", "coef", "RB", "pooled", "line_pos")]
    ab = mine[("receptions", "brier", "RB", "2024+2025", "close+a")]
    ab2 = mine[("rush_attempts", "brier", "RB", "2024+2025", "close+a")]
    chk = [L.reproduce("rush form_gap b", rf["est"], PUB["rush_form_b"], 4),
           L.reproduce("rush form_gap lo", rf["lo"], PUB["rush_form_lo"], 4),
           L.reproduce("rush form_gap hi", rf["hi"], PUB["rush_form_hi"], 4),
           L.reproduce("rush form_gap Bonf hi", rf["bonf_hi"], PUB["rush_form_bonf_hi"], 4),
           L.reproduce("rush form_gap Holm p", rf["p_holm"], 1.00, 2),
           L.reproduce("rec book_gap b", rbk["est"], PUB["rec_book_b"], 4),
           L.reproduce("rec book_gap lo", rbk["lo"], PUB["rec_book_lo"], 4),
           L.reproduce("rec book_gap hi", rbk["hi"], PUB["rec_book_hi"], 4),
           L.reproduce("rec book_gap Holm p", rbk["p_holm"], PUB["rec_book_holm"], 2),
           L.reproduce("rec line_pos Bonf lo", rl["bonf_lo"], PUB["rec_line_bonf_lo"], 4),
           L.reproduce("rec line_pos Bonf hi", rl["bonf_hi"], PUB["rec_line_bonf_hi"], 4),
           L.reproduce("rec close+a Brier", ab["est"], PUB["rec_a_brier"], 5),
           L.reproduce("rec close+a MDE", ab["mde"], PUB["rec_a_mde"], 5)]
    for c in chk:
        out("   %-22s measured %+.6f published %+.6f -> %s" % (c["name"], c["measured"], c["published"],
                                                              "reproduces" if c["reproduces"] else "DOES NOT REPRODUCE"))
    seas = [mine[("rush_attempts", "coef", "RB", str(s), "form_gap")]["est"] for s in M.SEASONS]
    out("   rush form_gap by season: %s (unit: same sign in three seasons)" % ", ".join("%+.4f" % v for v in seas))
    out("   rush close+a Brier %+.5f [%+.5f, %+.5f] MDE %.5f -> beyond its MDE: %s (unit: receptions only)"
        % (ab2["est"], ab2["lo"], ab2["hi"], ab2["mde"], bool(ab2["hi"] < 0 and -ab2["est"] > ab2["mde"])))
    pr = {m: sorted(power[m][c]["0.020"]["detected"] for c in M.CANDIDATES) for m in MK}
    out("   committed power json (%d reps): detection at 0.020 rush max %.2f; receptions %.2f-%.2f; receptions line_pos "
        "pooled coef at the bound at 0.020: %.2f" % (pw_file["reps"], pr["rush_attempts"][-1], pr["receptions"][0],
                                                     pr["receptions"][-1], power["receptions"]["line_pos"]["0.020"]["coef_pooled_bound"]))
    R["reproduce"] = {"tests": len(mine), "registered": len(reg), "differ_1e-9": differ, "differ_registered": differ_reg,
                      "max_abs_diff": worst, "verdict_equal": verd_eq, "detected": n_det, "holm_lt_05": n_holm,
                      "states": states, "checks": chk}

    # ------------------------------------------------------------------ 2
    out("\n== 2. BLOCKS: THROUGH M.coef_test / M.brier_test (weights and z from M._cut)")

    def w_coef(i, cand):
        def f(rr):
            rows = c41.columns(rr)
            cut = M._cut(rows, np.arange(len(rr)), np.random.default_rng([M.SEED, i]))
            t = M.coef_test(cut["z"][cand][0], rows["r"], cut["game"], cut["n_games"], cut["W"])
            return t["hi"] - t["lo"]
        return f

    def w_brier(i):
        def f(rr):
            rows = c41.columns(rr)
            tr = M._cut(rows, np.flatnonzero(np.isin(rows["season"], M.TEST_SEASONS)), np.random.default_rng([M.SEED, i]))
            q, _ = c41.walk_forward(rows, [], add_intercept=True, slopes=False)
            t = M.brier_test(q[tr["src"]], tr["p"], rows["y"][tr["src"]], tr["game"], tr["n_games"], tr["W"])
            return t["hi"] - t["lo"]
        return f

    G = {m: R["counts"][m]["games"] for m in MK}
    th = {"rush form_gap coefficient": L.duplication_through(w_coef(1, "form_gap"), recs["rush_attempts"], "event",
                                                            fn_name="M.coef_test+M._cut", units=G["rush_attempts"]),
          "receptions book_gap coefficient": L.duplication_through(w_coef(0, "book_gap"), recs["receptions"], "event",
                                                                  fn_name="M.coef_test+M._cut", units=G["receptions"]),
          "receptions line_pos coefficient": L.duplication_through(w_coef(0, "line_pos"), recs["receptions"], "event",
                                                                  fn_name="M.coef_test+M._cut", units=G["receptions"]),
          "receptions close+a Brier": L.duplication_through(w_brier(0), recs["receptions"], "event",
                                                           fn_name="M.brier_test+c41.walk_forward+M._cut", units=G["receptions"])}
    for k, t in th.items():
        out("   %-32s %s" % (k, L.through_line(t)))
    R["through"] = th
    R["through_required"] = L.require_through(list(th), th)
    R["alt"] = {}
    for name, m, cand, t0 in (("rush form_gap coefficient", "rush_attempts", "form_gap", rf),
                              ("receptions book_gap coefficient", "receptions", "book_gap", rbk),
                              ("receptions line_pos coefficient", "receptions", "line_pos", rl)):
        rr = recs[m]
        z = preps[m]["cuts"][0][2]["z"][cand][0]
        r = preps[m]["RB"]["r"]

        def slope(idx, z=z, r=r):
            zz, q = z[idx], r[idx]
            v = zz.var()
            return float(((zz * q).mean() - zz.mean() * q.mean()) / v) if v > 0 else 0.0

        lab = {"game": [d["event"] for d in rr], "player": [d["gsis"] for d in rr],
               "season-week": [d["event"][:7] for d in rr], "season": [d["season"] for d in rr]}
        alt = L.alt_blocks(slope, len(rr), lab, draws=1000)
        ic = L.iid_contrast(slope, len(rr), lab["game"], draws=1000)
        R["alt"][name] = {"alt": alt, "iid_over_blocked_width": ic["iid_over_blocked_width"], "target_se": t0["se"]}
        out("   %s: %d rows on %d games (%.1f a game); target SE %.5f; iid/blocked width x%.3f"
            % (name, len(rr), G[m], len(rr) / G[m], t0["se"], ic["iid_over_blocked_width"]))
        for k, v in alt.items():
            out("      by %-12s %4d blocks  %s   Bonferroni(84) [%+.4f, %+.4f]"
                % (k, v["n_blocks"], L.fmt(v, 5), v["est"] - M.Z_BONF * v["se"], v["est"] + M.Z_BONF * v["se"]))

    # ------------------------------------------------------------------ 3
    out("\n== 3. LEAKAGE (function level only; store-level leakage NOT RUN - build() needs the store)")
    g = {"k": 1000.0, "season": 2024, "home": "AAA", "away": "BBB", "total": 44.0, "spread": 3.0}
    hist = {"p": [(900.0, 2024, 5.0, "AAA", "WR"), (950.0, 2024, 7.0, "AAA", "WR"),
                  (1000.0, 2024, 99.0, "AAA", "WR"), (1100.0, 2024, 99.0, "AAA", "WR")]}
    rung = {"line": 5.5, "p_bench": 0.5, "p_all": 0.51}
    pr0 = c41.prior_games(hist, "p", 2024, 1000.0)
    f0 = c41.features(rung, g, pr0, 5.5, "AAA")
    hist2 = {"p": hist["p"][:2] + [(1000.0, 2024, -5.0, "AAA", "WR"), (1100.0, 2024, 12345.0, "AAA", "WR")]}
    f1 = c41.features(rung, g, c41.prior_games(hist2, "p", 2024, 1000.0), 5.5, "AAA")
    try:
        c41.features(rung, g, hist["p"][:3], 5.5, "AAA")
        planted = False
    except c41.LeakError:
        planted = True
    out("   prior_games keeps %d of 4 games (2 are at/after kickoff); scrambling those leaves features equal: %s; "
        "planted same-kickoff prior raises LeakError: %s" % (len(pr0), f0 == f1, planted))
    R["leakage"] = {"features_equal_under_scramble": f0 == f1, "leakerror_on_plant": planted}
    for m in MK:
        rows = c41.columns(recs[m])
        n = len(recs[m])
        qt, _ = c41.walk_forward(rows, ["form_gap"])
        rng = np.random.default_rng(7)
        sc = dict(rows)
        m25 = rows["season"] == 2025
        sc["r"] = np.where(m25, rng.permutation(rows["r"]), rows["r"])
        q1, _ = c41.walk_forward(sc, ["form_gap"])
        k24 = rows["season"] == 2024
        moved24 = float(np.nanmax(np.abs(q1[k24] - qt[k24])))
        pl = dict(sc)
        pl["season"] = np.where(m25 & (np.arange(n) % 2 == 0), 2023, rows["season"])
        pl0 = dict(rows)
        pl0["season"] = pl["season"]
        qp1, _ = c41.walk_forward(pl, ["form_gap"])
        qp0, _ = c41.walk_forward(pl0, ["form_gap"])
        moved_plant = float(np.nanmax(np.abs(qp1[k24] - qp0[k24])))
        R["leakage"][m] = {"wf_2024_moved_when_2025_scrambled": moved24, "wf_2024_moved_with_planted_leak": moved_plant}
        out("   %-13s walk_forward: scramble 2025 residuals -> largest move of a 2024 forecast %.3g (must be 0); with 2025 "
            "rows planted as 2023 -> %.3g (must be > 0)" % (m, moved24, moved_plant))

    # ------------------------------------------------------------------ 4
    out("\n== 4. SPECIFICATIONS")
    kinds = {}
    for t in rec["tests"]:
        kk = "%s/%s/%s" % (t["kind"], "registered" if t["registered"] else "descriptive", t["cut"] if not t["registered"] else "-")
        kinds[kk] = kinds.get(kk, 0) + 1
    n_reg = sum(t["registered"] for t in rec["tests"])
    try:
        L.registered_count(rec, "c-47")
        refused = False
    except SystemExit as e:
        refused = True
        out("   f26lib.registered_count REFUSES: %s" % e)
    out("   key used instead: top-level 'registered' = %s, 'specifications' = %s; counted from the tests list: %d registered of %d"
        % (rec.get("registered"), rec.get("specifications"), n_reg, len(rec["tests"])))
    out("   by kind: %s" % kinds)
    out("   84 vs 98: 84 registered (2 x [30 coef + 12 Brier]) + %d descriptive Brier arms (close+a, close+joint; RB and RM; "
        "2 markets) + %d no-QB coefficients (rush only) = %d"
        % (sum(1 for t in rec["tests"] if not t["registered"] and t["kind"] == "brier"),
           sum(1 for t in rec["tests"] if not t["registered"] and t["kind"] == "coef"), len(rec["tests"])))
    R["specs"] = {"registered": n_reg, "tests": len(rec["tests"]), "kinds": kinds, "registered_count_refused": refused}
    for name, t in (("rush form_gap coefficient", rf), ("receptions book_gap coefficient", rbk)):
        mu = L.multiplicity(t["est"], t["se"], (n_reg, len(rec["tests"])), n_blocks=t["games"])
        R["specs"][name] = mu
        out("   %-32s z %+.2f p %.4f  Bonferroni x%d %.3f, x%d %.3f; Holm p (target) %.3f"
            % (name, mu["z"], mu["p"], n_reg, mu["bonferroni"][n_reg]["p_adj"], len(rec["tests"]),
               mu["bonferroni"][len(rec["tests"])]["p_adj"], t["p_holm"]))
    out("   runs on real outcomes beyond the registered one - what the files show (times local, -0400):")
    try:
        gl = subprocess.run(["git", "-C", src, "log", "-4", "--format=%h %ci %s", "--", "research/residual_two_markets.py",
                             "research/results/residual_two_markets.json", "research/results/residual_two_markets_power.json",
                             "docs/C47-two-markets-preregistration.md", "docs/findings/c47-two-markets.md"],
                            capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
        for ln in gl:
            out("      commit %s" % ln[:110])
        dd = subprocess.run(["git", "-C", src, "diff", "--stat", "d8a227e", "23b3271", "--", "research/residual_two_markets.py"],
                            capture_output=True, text=True, timeout=30).stdout.strip()
        out("      script diff d8a227e..23b3271: %s" % (dd or "EMPTY (the script is the one committed with the power, before the run)"))
        R["specs"]["commits"] = gl
    except Exception as e:  # noqa: BLE001
        out("      git log NOT RUN: %r" % (e,))
    if a.scratch and os.path.isdir(a.scratch):
        ent = []
        for nm in sorted(os.listdir(a.scratch)):
            p = os.path.join(a.scratch, nm)
            ent.append((nm, "dir" if os.path.isdir(p) else "file", time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(p)))))
        for nm in ("rows_receptions.json", "rows_rush_attempts.json"):
            p = os.path.join(a.rows_dir, nm)
            ent.append(("rows/" + nm, "file", time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(p)))))
        for e in ent:
            out("      scratch %-28s %-4s mtime %s" % e)
        R["specs"]["scratch"] = ent
        dry = os.path.join(a.scratch, "dry.txt")
        if os.path.exists(dry):
            txt = open(dry, encoding="utf-8", errors="replace").read()
            out("      dry.txt: %d lines; contains 'DRY RUN' %s; contains an over rate / residual / Brier / coefficient table: %s; "
                "its rb_rungs are the PRE-settlement counts (10,536 / 4,586), not the run's (10,527 / 4,581): %s"
                % (len(txt.splitlines()), "DRY RUN" in txt,
                   any(w in txt for w in ("over rate", "Brier", "COEFFICIENTS", "Holm")), ("10,536" in txt and "4,586" in txt)))
        ptm = os.path.join(a.scratch, "power_timing.txt")
        if os.path.exists(ptm):
            txt = open(ptm, encoding="utf-8", errors="replace").read()
            out("      power_timing.txt: first line %r; mentions settlement joined: %s"
                % (txt.strip().splitlines()[0].strip(), "NO settlement joined" in txt))
    else:
        out("      scratch listing NOT RUN (no --scratch)")
    rl_log = os.path.join(src, "research", "results", "residual_two_markets.log")
    el = [ln.strip() for ln in open(rl_log, encoding="utf-8", errors="replace") if "elapsed" in ln]
    out("      run log elapsed line(s): %s" % el)
    out("      CAN be shown: one rows file per market, written after the power commit; one result json; script unchanged after "
        "d8a227e; dry.txt and power_timing.txt carry no outcome-joined estimate.")
    out("      CANNOT be shown: that the script was not executed on real outcomes earlier and its output discarded - scratch "
        "files are overwritten in place and a terminal leaves no file. Commit order is evidence of what was committed, not of what was run.")

    # ------------------------------------------------------------------ 5
    out("\n== 5. MDE")
    R["mde"] = {}
    for name, t in (("rush form_gap coefficient", rf), ("receptions book_gap coefficient", rbk),
                    ("receptions line_pos coefficient", rl)):
        mine_se = R["alt"][name]["alt"]["game"]["se"]
        mr, mc = L.mde_ratio(t["est"], t["se"]), L.mde_claim(t["mde"], mine_se)
        R["mde"][name] = {"ratio": mr, "claim": mc, "bound80_stated": t["mde_bound_80"], "bound80_here": (M.Z_BONF + M.Z_80) * mine_se}
        out("   %-32s |est|/MDE %.3f (%s); stated MDE %.5f vs 2.8 x SE re-drawn here (f26lib game blocks) %.5f -> %s; "
            "Bonferroni-80%% bound stated %.4f, here %.4f"
            % (name, mr["ratio"], mr["reading"], t["mde"], mc["remeasured"], "consistent" if mc["consistent"] else "INCONSISTENT",
               t["mde_bound_80"], (M.Z_BONF + M.Z_80) * mine_se))
    for m in MK:
        ses = [mine[(m, "coef", "RB", "pooled", c)]["se"] for c in M.CANDIDATES]
        out("   %-13s pooled SEs %.4f-%.4f: single-interval MDE %.4f-%.4f; at the 84-test bound (4.275 SE) %.4f-%.4f"
            % (m, min(ses), max(ses), 2.8 * min(ses), 2.8 * max(ses), 4.275 * min(ses), 4.275 * max(ses)))

    # ------------------------------------------------------------------ 6 helpers
    base = {m: [t for t in tests if t["market"] == m and t["registered"]] for m in MK}

    def cuts_of(prep):
        cs = [(pop, rows) for pop, _c, rows in prep["cuts"]] + [(pop, prep["test"][pop]) for pop in ("RB", "RM")]
        if "no_qb" in prep:
            cs.append(("RB", prep["no_qb"]))
        return cs

    def plant_col(prep, slot, x_rb):
        prep["RB"][slot] = x_rb
        prep["RM"][slot] = x_rb[prep["RM"]["rb_index"]]
        for pop, cut in cuts_of(prep):
            cut[slot] = prep[pop][slot][cut["src"]]
            mu, sd = c41.moments(cut[slot])
            cut["z"][slot] = (c41.standardise(cut[slot], mu, sd), mu, sd)

    def family(m, slot_tests, slot):
        """The 84-test family with `slot`'s 7 tests of market m replaced; Holm; the target's verdict."""
        fam = []
        for mm in MK:
            for t in base[mm]:
                if mm == m and t["cand"] == slot:
                    continue
                fam.append({k: v for k, v in t.items() if k != "p_holm"})
        for t in slot_tests:
            t["market"] = m
            fam.append(t)
        if len(fam) != M.N_REGISTERED:
            raise SystemExit("family is %d tests, not %d" % (len(fam), M.N_REGISTERED))
        for t, adj in zip(fam, c41.holm([t["p"] for t in fam])):
            t["p_holm"] = adj
        return fam, M.verdict(fam, power)[m][slot]

    # faithfulness of the plant helper: putting the REAL column back must give the registered tests
    for m in MK:
        prep = preps[m]
        real = prep["RB"][SLOT].copy()
        plant_col(prep, SLOT, real)
        st = M.analyse_market(prep, cands=(SLOT,), descriptive=False)
        fam, v = family(m, st, SLOT)
        w = max(abs(t[f] - mine[key(t)][f]) for t in fam for f in F + ("p_holm",))
        out("\n   helper check %-13s real '%s' column re-planted: largest |difference| from the registered tests %.3g; state %s"
            % (m, SLOT, w, v["state"]) if m == MK[0] else
            "   helper check %-13s real '%s' column re-planted: largest |difference| from the registered tests %.3g; state %s"
            % (m, SLOT, w, v["state"]))

    # ------------------------------------------------------------------ 6a
    if a.plant_reps:
        out("\n== 6a. POWER ON REAL OUTCOMES: a synthetic candidate in the '%s' slot, through M.analyse_market + c41.holm + "
            "M.verdict (%d reps a cell)" % (SLOT, a.plant_reps))
        out("   x = lam * r_rung + e, e ~ N(0,1) drawn once per PLAYER-GAME; real outcomes, prices, games, block weights and the")
        out("   other 77 tests untouched. Sampling variation across reps is e only (r is the one real realization).")
        out("   'unit sim' = the unit's simulated detection for that market and size (player-games independent inside a game).")
        R["power"] = []
        out("   %-13s %6s %9s %8s %8s | %5s %5s %5s %8s | %s" % ("market", "b tgt", "b pooled", "sd reps", "boot SE", "Holm", "sign",
                                                              "cond2", "DETECTED", "unit sim; states"))
        for i, m in enumerate(MK):
            prep = preps[m]
            real = prep["RB"][SLOT].copy()
            r = prep["RB"]["r"]
            vr = float(np.var(r))
            pg, npg = prep["RB"]["pg"], prep["RB"]["n_pg"]
            for b in (0.0, 0.020, 0.033, 0.045):
                if left() < 25:
                    out("   %-13s %6.3f NOT RUN (budget)" % (m, b))
                    continue
                lam = math.sqrt(b * b / (vr * vr - vr * b * b)) if b else 0.0
                rng = np.random.default_rng([4700, i, int(round(b * 1e5))])
                agg = {"holm": 0, "sign": 0, "c2": 0, "det": 0, "b": [], "se": [], "cover": 0, "states": {}}
                done = 0
                for _ in range(a.plant_reps):
                    plant_col(prep, SLOT, lam * r + rng.standard_normal(npg)[pg])
                    st = M.analyse_market(prep, cands=(SLOT,), descriptive=False)
                    fam, v = family(m, st, SLOT)
                    tc = next(t for t in st if t["kind"] == "coef" and t["pop"] == "RB" and t["cut"] == "pooled")
                    agg["holm"] += v["coef_excludes_zero_after_holm"]
                    agg["sign"] += v["same_sign_all_seasons"]
                    agg["c2"] += v["condition_2_brier_beyond_mde"]
                    agg["det"] += v["detected"]
                    agg["b"].append(tc["est"])
                    agg["se"].append(tc["se"])
                    agg["cover"] += bool(tc["bonf_lo"] <= b <= tc["bonf_hi"])
                    agg["states"][v["state"]] = agg["states"].get(v["state"], 0) + 1
                    done += 1
                k = done
                row = {"market": m, "b_target": b, "reps": k, "b_mean": float(np.mean(agg["b"])), "b_sd_reps": float(np.std(agg["b"])),
                       "boot_se_mean": float(np.mean(agg["se"])), "holm": agg["holm"] / k, "same_sign": agg["sign"] / k,
                       "cond2": agg["c2"] / k, "detected": agg["det"] / k, "bonf_covers_b": agg["cover"] / k, "states": agg["states"]}
                R["power"].append(row)
                out("   %-13s %6.3f %+9.4f %8.4f %8.4f | %5.2f %5.2f %5.2f %8.2f | %s; Bonf interval covers b %.2f; %s"
                    % (m, b, row["b_mean"], row["b_sd_reps"], row["boot_se_mean"], row["holm"], row["same_sign"], row["cond2"],
                       row["detected"], SIM[m].get("%.3f" % b, "0.00"), row["bonf_covers_b"], dict(sorted(agg["states"].items()))))
            plant_col(prep, SLOT, real)
        out("   committed simulation, same slot (%s): %s" % (SLOT, {m: {d: round(power[m][SLOT][d]["detected"], 2)
                                                                        for d in ("0.000", "0.020", "0.033", "0.045")} for m in MK}))
    else:
        out("\n== 6a. POWER ON REAL OUTCOMES: NOT RUN (--plant-reps 0)")

    # ------------------------------------------------------------------ 6b
    if a.boot_reps:
        m, cand, i = "receptions", "line_pos", 0
        out("\n== 6b. DOES '0.020 ruled out in receptions line_pos' SURVIVE A 0.020 EFFECT ON REAL ROWS? (%d game-resampled worlds an arm)"
            % a.boot_reps)
        out("   each world: the %d real games drawn with replacement (a copy is its own game), real outcomes and the REAL line_pos" % G[m])
        out("   column; then outcomes are moved by d per sd of line_pos (one uniform per player-game: an under flips to over with")
        out("   probability d z / (1 - p), an over to under with -d z / p), M.prepare + set_y + analyse_market(line_pos) + holm + verdict.")
        out("   The real rows already carry %+.4f, so the world's truth is taken as %+.4f + the shift the plant achieved (measured" % (rl["est"], rl["est"]))
        out("   in the same world, planted minus unplanted). Arm 'to 0.020' sets d = 0.020 - (%+.4f)." % rl["est"])
        by_game = {}
        for d in recs[m]:
            by_game.setdefault(d["event"], []).append(d)
        evs = sorted(by_game)
        R["exclusion"] = []
        out("   %-10s %7s | %9s %9s %8s | %14s %12s %14s %12s" % ("arm", "d", "est mean", "shift", "truth", "Bonf misses", "hi < 0.020",
                                                               "EXCLUDED@0.020", "sd(est)/SE"))
        for arm, d_eff in (("null", 0.0), ("plus 0.020", 0.020), ("to 0.020", 0.020 - rl["est"])):
            rng = np.random.default_rng([4701, int(round(d_eff * 1e6))])
            acc = {"est": [], "shift": [], "miss": 0, "hi": 0, "exc": 0, "se": []}
            done = 0
            for _ in range(a.boot_reps):
                if left() < 12:
                    break
                pick = rng.integers(0, len(evs), len(evs))
                world = [dict(dd, event="%s#%d" % (dd["event"], j)) for j, gi in enumerate(pick) for dd in by_game[evs[gi]]]
                prep = M.prepare(world, [M.SEED, i])
                y0 = np.array([dd["y"] for dd in world], dtype=float)
                M.set_y(prep, y0)
                t0 = next(t for t in M.analyse_market(prep, cands=(cand,), descriptive=False)
                          if t["kind"] == "coef" and t["pop"] == "RB" and t["cut"] == "pooled")
                z = np.nan_to_num(prep["cuts"][0][2]["z"][cand][0])
                p = prep["RB"]["p"]
                s = d_eff * z
                u = rng.random(prep["RB"]["n_pg"])[prep["RB"]["pg"]]
                y1 = y0.copy()
                y1[(s > 0) & (y0 == 0) & (u < s / np.maximum(1 - p, 1e-9))] = 1.0
                y1[(s < 0) & (y0 == 1) & (u < -s / np.maximum(p, 1e-9))] = 0.0
                M.set_y(prep, y1)
                st = M.analyse_market(prep, cands=(cand,), descriptive=False)
                fam, v = family(m, st, cand)
                tc = next(t for t in st if t["kind"] == "coef" and t["pop"] == "RB" and t["cut"] == "pooled")
                acc["est"].append(tc["est"])
                acc["shift"].append(tc["est"] - t0["est"])
                acc["se"].append(tc["se"])
                acc["_last"] = (tc, v)
                acc.setdefault("rows", []).append((tc["est"], tc["bonf_lo"], tc["bonf_hi"], v["state"]))
                done += 1
            if not done:
                out("   %-10s NOT RUN (budget)" % arm)
                continue
            truth = rl["est"] + float(np.mean(acc["shift"]))
            miss = sum(not (lo <= truth <= hi) for _e, lo, hi, _s in acc["rows"])
            hi20 = sum(hi < 0.020 for _e, _lo, hi, _s in acc["rows"])
            exc = sum(s == "excluded at 0.020" for _e, _lo, _hi, s in acc["rows"])
            row = {"arm": arm, "d": d_eff, "worlds": done, "requested": a.boot_reps, "est_mean": float(np.mean(acc["est"])),
                   "shift_mean": float(np.mean(acc["shift"])), "truth": truth, "bonf_misses_truth": miss / done,
                   "bonf_hi_below_0020": hi20 / done, "excluded_at_0020": exc / done,
                   "sd_est_over_mean_se": float(np.std(acc["est"]) / np.mean(acc["se"]))}
            R["exclusion"].append(row)
            out("   %-10s %7.4f | %+9.4f %+9.4f %+8.4f | %8d of %-3d %6d of %-3d %8d of %-3d %10.2f%s"
                % (arm, d_eff, row["est_mean"], row["shift_mean"], truth, miss, done, hi20, done, exc, done,
                   row["sd_est_over_mean_se"], "" if done == a.boot_reps else "  (BUDGET-TRUNCATED: %d of %d)" % (done, a.boot_reps)))
        out("   A game-resampled world inherits whatever dependence a game block carries and none that crosses games; it cannot")
        out("   show an interval too narrow for a reason the game bootstrap itself ignores (see the season-week blocks in step 2).")
    else:
        out("\n== 6b. line_pos exclusion on resampled real rows: NOT RUN (--boot-reps 0)")
    out("\n== NOT RUN: store-level leakage and rows-vs-store equality (no store opened); the unit's own 600-rep simulation was not")
    out("   re-drawn (its committed json is read); no timestamped line was compared with team_total's untimestamped input.")
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1, default=float)
    print("wall %.1fs" % (time.time() - t_start), file=sys.stderr)


if __name__ == "__main__":
    main()
