"""f-26 generic adapter: a regression coefficient a unit published WITH its per-row file.

For a unit that committed the rows behind its headline (c-36 did:
research/results/win_total_drift.json -> team_weeks). No store, no target code:

    python research/f26_reliability/attack_rows.py --rows <json> --key team_weeks \
        --y move_w --x dmodel --fe week --block team --alt-blocks game,week \
        --published 0.724,0.652,0.803 --dp 3 --k 3 --null 1.0 --out D:/temp/f26/c36_t2b.json         --src D:/temp/f27/c36src --boot research.win_total_drift:boot_ols

--src/--boot hand the rows to the TARGET's own bootstrap for the duplication test
(f-27). The function must have c-36's rows-form signature,
`fn(rows, ycol, xcols, fe, block, n_boot=) -> {x: {lo, hi}}`; anything else needs
its own adapter. Without them the blocks step says NOT RUN - it does not pass.

WHAT THIS CANNOT DO, and the verdict must say so: it reproduces the ARITHMETIC
from rows the unit wrote. It does not re-run the unit's pipeline, so it cannot
see a row built wrongly, and it runs NO leakage audit.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--x", required=True, help="comma list; the FIRST is the coefficient under attack")
    ap.add_argument("--fe", default=None)
    ap.add_argument("--block", required=True)
    ap.add_argument("--alt-blocks", default="")
    ap.add_argument("--published", required=True, help="est,lo,hi")
    ap.add_argument("--dp", type=int, default=3)
    ap.add_argument("--k", type=int, required=True, help="specifications the unit itself counted (no default)")
    ap.add_argument("--null", type=float, default=0.0, help="the value the claim is read against")
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", default=None, help="detached worktree of the target's branch")
    ap.add_argument("--boot", default=None, help="module:function of the target's rows-form OLS bootstrap")
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    xs = a.x.split(",")
    rows = [r for r in json.load(open(a.rows, encoding="utf-8"))[a.key]
            if all(r.get(c) is not None for c in [a.y] + xs)]
    n = len(rows)
    if n < len(xs) + 3:
        raise SystemExit("%d usable rows - refusing" % n)
    levels = sorted({r[a.fe] for r in rows}) if a.fe else []
    X = np.array([[r[c] for c in xs] + ([1.0 if r[a.fe] == lv else 0.0 for lv in levels] if a.fe else [1.0])
                  for r in rows], float)
    y = np.array([r[a.y] for r in rows], float)

    def stat(idx):
        Xb = X[idx]
        if np.linalg.matrix_rank(Xb) < Xb.shape[1]:
            return float("nan")
        return float(np.linalg.lstsq(Xb, y[idx], rcond=None)[0][0]) - a.null

    def boot(labels, seed):
        d = L.block_boot(stat, L.blocks_of(labels), seed=seed)
        return L.summ(stat(np.arange(n)), d[~np.isnan(d)])

    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"rows": a.rows, "n": n, "coef": xs[0], "null": a.null, "leakage_audit": "NOT RUN - rows only"}
    est = stat(np.arange(n)) + a.null
    pub = [float(v) for v in a.published.split(",")]
    lab = [str(r[a.block]) for r in rows]
    reg = boot(lab, 51)
    out("== 1. REPRODUCE from the unit's committed rows (arithmetic only): n %d, %s on %s" % (n, a.y, a.x))
    R["reproduce"] = [L.reproduce("coefficient", est, pub[0], a.dp)]
    out("   coefficient measured %+.4f  published %+.*f -> %s"
        % (est, a.dp, pub[0], "REPRODUCES" if R["reproduce"][0]["reproduces"] else "DOES NOT REPRODUCE"))
    R["interval_own_seed"] = {nm: L.same_within_mc(reg[nm] + a.null, pv, reg["se"]) for nm, pv in (("lo", pub[1]), ("hi", pub[2]))}
    for nm, r in R["interval_own_seed"].items():
        out("   %s under this file's own seed %+.4f  published %+.*f -> %s (|diff| %.4f, tolerance 0.25 SE = %.4f)"
            % (nm, r["measured"], a.dp, r["published"], "consistent" if r["consistent"] else "NOT CONSISTENT",
               r["abs_diff"], r["tolerance"]))
    out("== 2. BLOCKS (statistic is coefficient minus %g)" % a.null)
    if a.src and a.boot:
        import importlib
        sys.path.insert(0, os.path.abspath(a.src))
        modname, fname = a.boot.split(":")
        mod = importlib.import_module(modname)
        if not os.path.abspath(mod.__file__).startswith(os.path.abspath(a.src)):
            raise SystemExit("%s resolved outside --src: %s" % (modname, mod.__file__))
        tfn = getattr(mod, fname)

        def width_of(rws):
            r = tfn(rws, a.y, xs, a.fe, a.block, n_boot=600)[xs[0]]
            return r["hi"] - r["lo"]
        th = L.duplication_through(width_of, rows, a.block, fn_name=a.boot, units=len(set(lab)))
        R["duplication_through_target_bootstrap"] = th
        out("   " + L.through_line(th))
    else:
        R["duplication_through_target_bootstrap"] = None
        out("   THROUGH THE TARGET'S BOOTSTRAP: NOT RUN (no --src/--boot). The blocks step has NOT been passed.")
    ic = L.iid_contrast(stat, n, lab, seed=52)
    R["iid_contrast"] = ic
    out("   descriptive: an unblocked row interval would be x%.2f the width of the %s-blocked one"
        % (ic["iid_over_blocked_width"], a.block))
    R["alt_blocks"] = {a.block: dict(reg, n_blocks=len(set(lab)))}
    for b in [b for b in a.alt_blocks.split(",") if b]:
        lb = [str(r[b]) for r in rows]
        R["alt_blocks"][b] = dict(boot(lb, 53), n_blocks=len(set(lb)))
    for b, r in R["alt_blocks"].items():
        r["read"] = r["n_blocks"] >= L.MIN_BLOCKS
        if not r["read"]:
            r["excludes_zero"] = None
        out("   %-8s (%3d blocks) %s" % (b, r["n_blocks"], L.fmt(r, 3)))
    R["seeds"] = L.seeds(stat, n, lab, n_seeds=10)
    out("   10 seeds: share excluding the null %.2f" % R["seeds"]["share_excluding_zero"])
    R["multiplicity"] = L.multiplicity(reg["est"], reg["se"], (a.k,), n_blocks=len(set(lab)))
    R["mde"] = L.mde_ratio(reg["est"], reg["se"])
    out("== 4. z %+.2f against %g; Bonferroni over the unit's own %d: p %.4f -> %s"
        % (R["multiplicity"]["z"], a.null, a.k, R["multiplicity"]["bonferroni"][a.k]["p_adj"],
           "survives" if R["multiplicity"]["bonferroni"][a.k]["survives_0.05"] else "does not survive"))
    out("== 5. |estimate - null| / MDE = %.2f -> %s its MDE" % (R["mde"]["ratio"], R["mde"]["reading"].upper()))
    out("== 3. LEAKAGE: NOT RUN. Rows only; the unit's pipeline was not re-executed.")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)


if __name__ == "__main__":
    main()
