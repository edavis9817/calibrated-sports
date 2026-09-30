"""c-25: preserve the exact quote rows research/season_vs_market.py read.

    python -m research.c25_extract_quotes --db .../market_log.db --out research/results/c25_quotes_at_T.json

`quotes` rows with source='live' are pruned 14 days after ingestion, so the
T_1 (2026-09-16) prices behind c-25's P2 leave the store on ~2026-09-30 and
T_3's by ~2026-10-13. This writes, for every season-future market, the same
row `season_vs_market.quote_at` selects at each T_k. Read-only (mode=ro).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import season_vs_market as S   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    con = S.ro(a.db)
    rows = []
    for mid, ev, title, close_ts in S.future_markets(con):
        for k, T in S.T_OF.items():
            q = S.quote_at(con, mid, T)
            rows.append({"market_id": mid, "state": k, "T": T, "quote": q})
    n = sum(1 for r in rows if r["quote"])
    if n == 0:
        raise SystemExit("REFUSED: zero quotes found at any instant - nothing preserved")
    json.dump({"kind": "c25.quotes_at_T", "T": S.T_OF, "max_stale_s": S.MAX_STALE,
               "rows": rows}, open(a.out, "w"), indent=0)
    print(f"{len(rows)} (market, state) rows, {n} with a quote -> {a.out}")


if __name__ == "__main__":
    main()
