"""f-23 - two follow-ups to attack.py, read-only.

    python -m research.f23_c27_attack.extras --c27-src D:/temp/f23/c27src \
        --pred D:/temp/f23/repro/predictions.csv --smoke D:/temp/c27/smoke/result.json

  smoke         which draw count reproduces c-27's smoke-run interval, whose verdict
                was FAILED on the identical point estimate
  multiplicity  from a 20,000-draw week-block bootstrap: the lower bound at
                1 - 0.05/k for k = 1..8 (Bonferroni over k specifications), and the
                one-sided bootstrap share of draws <= 0
"""
import argparse
import importlib.util
import json
from collections import defaultdict

import numpy as np

from research.f23_c27_attack.attack import block_boot, dsc, load


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--c27-src", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--smoke", required=True)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("c27_rc", f"{a.c27_src}/research/ranking_calibration.py")
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)

    rows = load(a.pred)
    d = np.array([float(r["decomposed"]) for r in rows])
    m = np.array([float(r["baseline"]) for r in rows])
    y = np.array([float(r["y"]) for r in rows])
    stat = lambda idx: dsc(d[idx], y[idx]) - dsc(m[idx], y[idx])  # noqa: E731

    smoke = json.load(open(a.smoke))["scores"]["P1 pooled"]["primary_dDSC"]
    print(f"smoke primary: lo {smoke['lo']:+.8f} hi {smoke['hi']:+.8f} se {smoke['se']:.8f}")
    pop = rc.Pop("P1", [dict(r, game=f"{r['season']}-{int(r['week']):02d}", m=0, k=0) for r in rows], "m", "k")
    hit = None
    for n in (50, 100, 150, 200, 250, 300, 400, 500, 1000):
        r = pop.boot(stat, draws=n, seed=27)
        match = abs(r["lo"] - smoke["lo"]) < 1e-12 and abs(r["hi"] - smoke["hi"]) < 1e-12
        print(f"  draws {n:>5} seed 27: lo {r['lo']:+.8f} hi {r['hi']:+.8f}" + ("   <- MATCHES SMOKE" if match else ""))
        if match:
            hit = n
    print(f"smoke draw count: {hit if hit else 'not among those tried'}")

    wk = defaultdict(list)
    for i, r in enumerate(rows):
        wk[(r["season"], r["week"])].append(i)
    blocks = [np.array(v) for _k, v in sorted(wk.items())]
    big = block_boot(stat, blocks, 20000, 777)
    p1 = float((big <= 0).mean())
    print(f"\nmultiplicity: 20,000 week-block draws; one-sided share <= 0 = {p1:.4f} (two-sided ~{2 * p1:.4f})")
    for k in range(1, 9):
        lo = float(np.percentile(big, 100 * 0.025 / k))
        print(f"  k={k}: lower bound at {100 * (1 - 0.05 / k):.2f}% two-sided = {lo:+.6f}" + ("" if lo > 0 else "   <- contains zero"))


if __name__ == "__main__":
    main()
