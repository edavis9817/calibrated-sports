"""f-35: b-106's Receipts sentence - '481 of the 496 graded leans carry a valid price' beside the hit
rate 51.6% [47.2, 56.0] b-99 read (a carrier of the figure run 3 attacked through receipts_blocks.py).
READ-ONLY. Re-counts from the Board ledger with this file's own rule (|price| >= 100, finite - the rule
b-106's lib/ledger.ts validPrice states), re-draws the hit rate under blocks, and compares the local
ledger with the one production serves when --served <file> is given.

    python research/f26_reliability/receipts_b106.py --ledger <ledger.parquet> [--served <fetched parquet>]

No interval is computed by a target function here (the site computes it in TypeScript), so the
through-the-target blocks test is NOT RUN and this file is not named attack_*.py.
"""
import argparse, hashlib, math, os, sys
import numpy as np, polars as pl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f26lib as L
sys.stdout.reconfigure(encoding="utf-8")

PUB = {"graded": 496, "priced": 481, "unpriced": 15, "wk4": 8, "wk3": 7, "rate": 0.516, "lo": 0.472, "hi": 0.560}


def wilson(k, n, z=1.959964):
    p = k / n; den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return p, c - h, c + h


def census(path, tag):
    raw = pl.read_parquet(path)
    df = raw.filter(pl.col("result").is_not_null())
    if df.height == 0:
        raise SystemExit("%s: 0 graded rows - refusing" % tag)
    if df["lean_id"].n_unique() != df.height:
        raise SystemExit("%s: a lean is graded twice (%d rows, %d leans)" % (tag, df.height, df["lean_id"].n_unique()))
    price = df["price"].to_numpy().astype(float)
    valid = np.isfinite(price) & (np.abs(price) >= 100)
    null_price = int(df["price"].null_count()); nan_price = int(np.isnan(price).sum()) - null_price
    bad = df.filter(pl.Series(~valid))
    y = (df["result"] == "cleared").to_numpy()
    k, n = int(y.sum()), len(y)
    p, lo, hi = wilson(k, n)
    print("[%s] %s  sha256 %s  rows %d" % (tag, path, hashlib.sha256(open(path, "rb").read()).hexdigest()[:12], raw.height))
    print("  graded %d (weeks %s; results %s)" % (n, dict(sorted(df["week"].value_counts().iter_rows())),
                                                   dict(sorted(df["result"].value_counts().iter_rows()))))
    print("  cleared %d missed %d  rate %.4f  Wilson per lean [%.4f, %.4f]" % (k, n - k, p, lo, hi))
    print("  valid price (finite, |price| >= 100): %d ; not valid: %d (null %d, NaN %d, inside (-100, +100): %d)"
          % (int(valid.sum()), int((~valid).sum()), null_price, nan_price, int((~valid).sum()) - null_price - nan_price))
    print("  not-valid by week: %s ; their prices min %.4g max %.4g ; their results %s"
          % (dict(sorted(bad["week"].value_counts().iter_rows())), bad["price"].min(), bad["price"].max(),
             dict(sorted(bad["result"].value_counts().iter_rows()))))
    return {"df": df, "y": y.astype(float), "valid": valid, "n": n, "k": k, "p": p, "lo": lo, "hi": hi,
            "bad_weeks": dict(bad["week"].value_counts().iter_rows())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--served")
    a = ap.parse_args()
    C = census(a.ledger, "local")
    ok = True
    for name, got, want in (("graded", C["n"], PUB["graded"]), ("valid price", int(C["valid"].sum()), PUB["priced"]),
                            ("not valid", int((~C["valid"]).sum()), PUB["unpriced"]),
                            ("not valid wk4", C["bad_weeks"].get(4, 0), PUB["wk4"]),
                            ("not valid wk3", C["bad_weeks"].get(3, 0), PUB["wk3"])):
        w = "REPRODUCES" if got == want else "DOES NOT REPRODUCE"
        ok &= got == want
        print("  1 reproduce  %-14s measured %d  published %d  %s" % (name, got, want, w))
    for name, got, want in (("rate", C["p"], PUB["rate"]), ("lo", C["lo"], PUB["lo"]), ("hi", C["hi"], PUB["hi"])):
        w = "REPRODUCES" if round(got, 3) == want else "DOES NOT REPRODUCE"
        ok &= round(got, 3) == want
        print("  1 reproduce  %-14s measured %.4f published %.3f %s" % (name, got, want, w))
    df, y, n = C["df"], C["y"], C["n"]
    labs = {"lean (iid)": list(range(n)), "game": df["game_id"].to_list(),
            "player-game": [g + "|" + s for g, s in zip(df["game_id"].to_list(), df["gsis_id"].to_list())],
            "kickoff slot": [str(int(t)) for t in df["kickoff_ts"].to_list()], "week": df["week"].to_list()}
    print("  2 blocks: through-the-target test NOT RUN (the interval is computed by the site, in TypeScript). Re-drawn here:")
    R = L.alt_blocks(lambda idx: float(y[idx].mean()), n, labs, seed=12)
    for kk, r in R.items():
        print("    %-12s (%3d blocks) %.4f [%.4f, %.4f] SE %.4f  0.5 inside: %s%s"
              % (kk, r["n_blocks"], r["est"], r["lo"], r["hi"], r["se"], r["lo"] <= 0.5 <= r["hi"],
                 "" if r["read"] else "  NOT READ (<5 blocks)"))
    # the 15: does leaving them in the record and out of the units move the record?
    v = C["valid"]
    for tag, m in (("valid-price leans only", v), ("the not-valid leans", ~v)):
        kk, nn = int(y[m].sum()), int(m.sum())
        p, lo, hi = wilson(kk, nn)
        print("  hit rate on %-22s %d of %d = %.4f [%.4f, %.4f]" % (tag, kk, nn, p, lo, hi))
    se = R["game"]["se"]
    print("  5 MDE: |rate - 0.5| = %.4f against 2.8 x game-block SE %.4f = %.4f -> ratio %.2f (a null below its MDE)"
          % (abs(C["p"] - 0.5), se, 2.8 * se, abs(C["p"] - 0.5) / (2.8 * se)))
    print("  3 leakage: NOT APPLICABLE to a graded record; whether each lean was published before kickoff is a-57's rule, not re-proved here")
    print("  4 specifications: b-106 computed no estimate; it added a count beside one. Nothing to count.")
    if a.served:
        S = census(a.served, "served")
        same = (S["n"], S["k"], int(S["valid"].sum())) == (C["n"], C["k"], int(C["valid"].sum()))
        print("  served vs local: graded/cleared/valid %s" % ("EQUAL" if same else "DIFFER"))
    print("RESULT: %s" % ("every published count reproduces" if ok else "a published count does not reproduce"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
