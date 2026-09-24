"""f-21 part 3: which `quotes.source` supplied each Board `kalshi_mid`.

a-31 narrows jobs.board_read.kalshi_mid's quotes read to kalshi.ladders (the live poll),
but its SQL filters on venue only - a backfilled candle row (kalshi.price_history) is
eligible too. This repeats kalshi_mid's two queries for every row that carries a
kalshi_mid and records the source of the row it would have picked. mode=ro.

    python research/f21_kalshi_mid_source.py --tree <board tree> --db <market_log.db>
"""
import argparse
import collections
import glob
import json
import os
import sqlite3
from datetime import datetime

ap = argparse.ArgumentParser()
ap.add_argument("--tree", required=True)
ap.add_argument("--db", required=True)
a = ap.parse_args()
con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
by_source, n, mismatched = collections.Counter(), 0, 0
per_read = {}
for path in sorted(glob.glob(os.path.join(a.tree, "board", "nfl", "*", "wk*", "read-*.json"))):
    doc = json.load(open(path, encoding="utf-8"))
    ts = datetime.fromisoformat(doc["read_at"].replace("Z", "+00:00")).timestamp()
    c = collections.Counter()
    for r in doc["rows"]:
        if r.get("kalshi_mid") is None or r["status"] != "upcoming":
            continue   # frozen rows carry the mid of an earlier read
        m = con.execute(
            """SELECT mo.market_id FROM outcomes o JOIN market_outcome mo USING (outcome_id)
                WHERE o.entity_id=? AND o.season=? AND o.week=? AND o.stat=? AND o.line=?
                  AND o.side IN ('over','yes') AND mo.venue='kalshi' LIMIT 1""",
            (r["gsis_id"], r["season"], r["week"], r["market"], float(r["line"]))).fetchone()
        if not m:
            continue
        q = con.execute("SELECT mid, source FROM quotes WHERE venue='kalshi' AND market_id=? "
                        "AND ts<=? ORDER BY ts DESC LIMIT 1", (m[0], ts)).fetchone()
        if not q:
            continue
        n += 1
        mismatched += round(q[0], 4) != r["kalshi_mid"]
        by_source[q[1]] += 1
        c[q[1]] += 1
    per_read[doc["read_at"]] = dict(c)
con.close()
if n == 0:
    raise SystemExit("no upcoming row carried a kalshi_mid - nothing measured")
print(json.dumps({"rows_checked": n, "mid_differs_from_row": mismatched,
                  "by_quotes_source": dict(by_source), "per_read": per_read}, indent=1))
