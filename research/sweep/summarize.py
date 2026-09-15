"""Brief 022 Part 4 - count everything, correct once, and grade every candidate.

    python -m research.sweep.summarize

Reads every registry in research/sweep/results/*.jsonl and the candidate spec
research/sweep/results/candidates.json, applies Benjamini-Hochberg at q = 0.10
to ALL search tests from Parts 2 and 3 together, and prints the five-bar table.

candidates.json is a list of:
  {"id": "...", "search_test": [family, name], "mechanism": "...",
   "other_side": "...", "microstructure": true|false,
   "replication_test": [family, name] | null,       # role "replication"
   "cost": {"value": float, "threshold": float, "unit": "pp", "basis": "..."},
   "persistence_s": float | null}
Bars 2 and 3 are computed from the registries, not asserted in the spec.
"""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from research.sweep import common as S  # noqa: E402

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def commit_time(path):
    r = subprocess.run(["git", "log", "--diff-filter=A", "--format=%ct", "--", path],
                       cwd=S.ROOT, capture_output=True, text=True)
    lines = r.stdout.split()
    return float(lines[-1]) if lines else None


def grade(cand, index, doc_ts):
    """Five bars -> (list of booleans/None, first failed bar or None)."""
    st = index.get(tuple(cand["search_test"]))
    rep = index.get(tuple(cand["replication_test"])) if cand.get("replication_test") else None
    other = (cand.get("other_side") or "").strip()
    # "none - ..." names no counterparty, whatever else it says.
    b1 = bool(cand.get("mechanism") and other and not other.lower().startswith("none")) and (
        rep is None or (doc_ts is not None and rep.get("ts", 0) > doc_ts))
    b2 = bool(rep and rep.get("estimable") and rep.get("excludes_zero")
              and st and (rep["est"] > 0) == (st["est"] > 0))
    b3 = bool(st and st.get("bh_survives"))
    c = cand.get("cost") or {}
    b4 = (c.get("value") is not None and c.get("threshold") is not None
          and c["value"] > c["threshold"])
    p = cand.get("persistence_s")
    b5 = p is not None and p >= 60
    bars = [b1, b2, b3, b4, b5]
    first = next((i + 1 for i, b in enumerate(bars) if not b), None)
    return bars, first


def main():
    paths = sorted(glob.glob(os.path.join(RESULTS, "*.jsonl")))
    recs = S.load_registries(paths)
    s = S.summarize(recs)
    print("=" * 78)
    print("REGISTRIES:", ", ".join(os.path.basename(p) for p in paths))
    for k, v in s.items():
        print(f"  {k:<28} {v:g}" if isinstance(v, float) else f"  {k:<28} {v}")
    by_pop = {}
    for r in recs:
        by_pop.setdefault((r["role"], r["population"]), 0)
        by_pop[(r["role"], r["population"])] += 1
    print("  records by role x population:", dict(sorted(by_pop.items())))
    surv = [r for r in recs if r.get("bh_survives")]
    print(f"\nBH SURVIVORS (q=0.10) - {len(surv)}; money-positive ones listed first, "
          f"then the rest by p (money- = significant on the LOSING side)")
    for r in sorted(surv, key=lambda r: (S.money_direction(r) != "money+", S.bh_p(r), r["p"])):
        print(f"  [{S.money_direction(r):<6}]", end="")
        print(f"  {r['family']:<22} {r['name'][:46]:<46} est {r['est']:+.4f} "
              f"[{r['lo']:+.4f}, {r['hi']:+.4f}] p={r['p']:.2e} n={r['n']} games={r['games']}"
              + ("" if r.get("readable") else "  (too few games to read)"))

    index = {(r["family"], r["name"]): r for r in recs}
    cpath = os.path.join(RESULTS, "candidates.json")
    if not os.path.exists(cpath):
        print("\nno candidates.json - five-bar table not built")
        return
    cands = json.load(open(cpath, encoding="utf-8"))
    doc_ts = commit_time(S.CANDIDATES_DOC)
    print(f"\nFIVE-BAR TABLE  (candidates doc committed at {doc_ts})")
    print(f"  {'candidate':<34} {'1 mech':>6} {'2 repl':>6} {'3 BH':>5} {'4 cost':>6} {'5 persist':>9}  failed at")
    findings = 0
    for cand in cands:
        bars, first = grade(cand, index, doc_ts)
        findings += first is None
        mark = lambda b: "pass" if b else "FAIL"
        print(f"  {cand['id'][:34]:<34} {mark(bars[0]):>6} {mark(bars[1]):>6} {mark(bars[2]):>5} "
              f"{mark(bars[3]):>6} {mark(bars[4]):>9}  {'-' if first is None else f'bar {first}'}")
    print(f"\n  FINDINGS: {findings} of {len(cands)} candidates clear all five bars")


if __name__ == "__main__":
    main()
