"""c-23: what a two-leg multiple has to clear, from c-22's committed aggregates.

Pre-registered at docs/C23-the-joint-preregistration.md (e0db8e3). Reads
research/results/bias_by_moneyness.json only - no database, no network.

A book prices a multiple as the product of its vigged legs, so with both legs at
de-vigged p and book cost c the paid price is (p+c)^2 and:

    break-even joint mispricing   (p+c)^2 - p^2          probability points
    return at fair, independent   p^2/(p+c)^2 - 1

The exchange route is two separate contracts, which pay additively: it cannot
collect a joint mispricing at all, only the sum of the legs' own edges.

    python -m research.joint_headwind [--json research/results/bias_by_moneyness.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SOURCE = Path(__file__).parent / "results" / "bias_by_moneyness.json"


def two_leg(p: float, c: float) -> dict:
    """p, c as probabilities (not pp). Both legs identical and independent."""
    if not (0 < p < 1 and 0 <= c < 1 - p):
        raise ValueError(f"p={p} c={c} out of range")
    paid = (p + c) ** 2
    return {
        "single_breakeven_pp": 100 * c,
        "single_return": p / (p + c) - 1,
        "double_breakeven_pp": 100 * (paid - p * p),
        "double_return": p * p / paid - 1,
    }


def rows(doc: dict) -> list[dict]:
    res = [r for r in doc["results"] if r["price"] == "all"]
    if len(res) != 1:
        raise ValueError(f"expected one price=all population, got {len(res)}")
    out = []
    for b in res[0]["buckets"]:
        p, c = b["priced"], b["book_cost_over_pp"] / 100
        out.append({"bucket": b["bucket"], "n": b["n"], "p": p,
                    "c_pp": 100 * c, "gap": b["gap"], **two_leg(p, c)})
    if len(out) != 10:
        raise ValueError(f"expected 10 buckets, got {len(out)}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=SOURCE)
    a = ap.parse_args(argv)
    doc = json.loads(a.json.read_text())
    print(f"source {a.json}  ({doc['preregistration']})")
    print(f"{'bucket':<10}{'n':>7}{'p':>7}{'c.ovr':>7}  {'leg gap pp [95%]':<24}"
          f"{'1-leg be':>9}{'1-leg ret':>10}{'2-leg be':>9}{'2-leg ret':>10}")
    for r in rows(doc):
        g = r["gap"]
        gap = f"{g['est_pp']:+.2f} [{g['lo_pp']:+.2f}, {g['hi_pp']:+.2f}]"
        print(f"{r['bucket']:<10}{r['n']:>7,}{r['p']:>7.3f}{r['c_pp']:>7.2f}  {gap:<24}"
              f"{r['single_breakeven_pp']:>9.2f}{100*r['single_return']:>9.1f}%"
              f"{r['double_breakeven_pp']:>9.2f}{100*r['double_return']:>9.1f}%")
    # the brief's figure, at the brief's inputs
    b = two_leg(0.5, 0.0338)
    print(f"brief's inputs p=0.50 c=3.38: 2-leg break-even {b['double_breakeven_pp']:.2f}pp, "
          f"return {100*b['double_return']:.1f}%")


if __name__ == "__main__":
    main()
