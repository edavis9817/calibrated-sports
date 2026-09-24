"""f-21 part 3: the Board's DECLARED sources against the tables SQLite reported on real reads.

Input: the JSON summary lines `jobs.board_read` logs per read (tables_read, sources).
    python research/f21_board_declared_vs_read.py --a31 <a-31 checkout> <read log>...
"""
import argparse
import json
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--a31", required=True)
ap.add_argument("logs", nargs="+")
a = ap.parse_args()
sys.path.insert(0, a.a31)
from jobs import source_registry as R  # noqa: E402

tables, n = set(), 0
for p in a.logs:
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line.startswith("{") and '"tables_read"' in line:
            tables |= set(json.loads(line)["tables_read"])
            n += 1
if n == 0:
    raise SystemExit("no read summaries found - refusing to compare against nothing")
m = R.SPORT_TABLE_SOURCES["nfl"]
reached = sorted({i for t in tables for i in m.get(t, ())})
declared = list(R.DECLARED["nfl"]["board_read"])
extra = R.KIND_EXTRA["nfl"].get("board_read", ())
print(json.dumps({
    "reads": n, "tables_read_union": sorted(tables),
    "unmapped_tables": sorted(t for t in tables if t not in m),
    "sources_reachable_from_tables_unnarrowed": reached,
    "declared_board_read": declared, "hand_declared_KIND_EXTRA": list(extra),
    "declared_not_reachable_from_any_table_read": sorted(set(declared) - set(reached) - set(extra)),
    "reachable_not_declared": sorted(set(reached) - set(declared)),
}, indent=1))
