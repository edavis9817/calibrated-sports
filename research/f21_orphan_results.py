"""f-21: the graded results of Board leans that are ABSENT from the latest read, against
those a page can see. Read-only on a Board tree.

    python research/f21_orphan_results.py --a31 <a-31 checkout> --tree <board tree> --season 2026 --week 2
"""
import argparse
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--a31", required=True)
ap.add_argument("--tree", required=True)
ap.add_argument("--season", type=int, required=True)
ap.add_argument("--week", type=int, required=True)
a = ap.parse_args()
sys.path.insert(0, a.a31)
import polars as pl  # noqa: E402
from core import board as B  # noqa: E402
from jobs import board_read as J  # noqa: E402

led = pl.read_parquet(J.ledger_path(a.tree)).to_dicts()
wd = J.week_dir(a.tree, a.season, a.week)
idx = json.load(open(os.path.join(wd, "index.json"), encoding="utf-8"))
doc = json.load(open(os.path.join(wd, J.read_name(idx["latest"])), encoding="utf-8"))
visible = {B.lean_id(r["claim_id"], r["line"], r["lean"]) for r in doc["rows"] if r.get("lean")}
pub = {e["lean_id"]: e for e in led if e["event"] == "published"
       and e["season"] == a.season and e["week"] == a.week}
term = {e["lean_id"]: e for e in led if e["event"] in ("graded", "void")}
out = {}
for name, ids in (("visible", [l for l in pub if l in visible]),
                  ("orphaned", [l for l in pub if l not in visible])):
    res = {}
    for l in ids:
        t = term.get(l)
        k = t["result"] if t and t["event"] == "graded" else (t["event"] if t else "open")
        res[k] = res.get(k, 0) + 1
    out[name] = {"n": len(ids), "results": res,
                 "by_market": {m: sum(1 for l in ids if pub[l]["market"] == m)
                               for m in sorted({pub[l]["market"] for l in ids})}}
if not pub:
    raise SystemExit("no published leans for that week - refusing to print an empty table")
print(json.dumps(out, indent=1))
