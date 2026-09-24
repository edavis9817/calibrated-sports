"""The Board's "leans this size" base rates (audit 5.3, unit a-26).

    python -m research.walkforward --ledger-out D:/scratch/walkforward_ledger.csv
    python -m research.board_bands --ledger D:/scratch/walkforward_ledger.csv

For every walk-forward outcome (brief 023 Part 1: receptions and rush attempts,
2023-2025, over side, constants refit on seasons <= T-1, features strictly
as-of), the lean the Board WOULD have shown at the close:

    market   p_bench - the median of DraftKings / FanDuel / BetMGM de-vigged
             over probabilities at the closing snapshot (outcome_close)
    gap      (p_model - p_bench) x 100, signed
    lean     over at gap >= +T, under at gap <= -T; T = the Board's FIRST
             threshold-log entry (4.0), never a value tuned on this output
    band     |gap| in [4,6), [6,8), [8,inf)
    price    the lean side's AMERICAN price at that same snapshot, median in
             decimal odds across the benchmark books that quoted both sides -
             "the price that stood", vig included. NOT the best price: the best
             of three books is a selection the reader would not have made.
    cleared  the lean side won (settled by core.settlement via walkforward,
             corrected arm: played with no stat row = 0). Pushes are excluded
             upstream by walkforward; a push refunds, so it moves no ROI.

Per (market, band): n, cleared rate with a Wilson 95% interval, and ROI per unit
staked with a GAME-BLOCK bootstrap interval (2,000 draws). The Wilson interval
treats leans as independent; rungs of one game are not, so it is narrower than
the truth - the ROI interval is the one that respects the clustering. Both are
labelled.

Writes research/results/board_bands.json, which jobs/board_read.py reads.

KNOWN SCOPE LIMITS - stated here so they travel with the numbers:
  * the Board's LIVE model is the committed-constant baseline fit as of each
    read, while these rates are from the walk-forward refit. Same model family
    and feature set, different fitted constants.
  * the close is the snapshot <= 15 min before kickoff; a Board lean is priced at
    its read, often days earlier. The base rate is a close-time base rate.
  * market = p_bench, which exists for ~43-48% of 2023-24 receptions (walkforward
    docstring); outcomes with no p_bench are not in any band.
"""
import argparse
import csv
import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core import board as B  # noqa: E402
from core.stats import wilson  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "board_bands.json")


def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def close_prices(con, oid):
    """{book: (over_american, under_american)} at the outcome's closing
    snapshot, benchmark books only."""
    r = con.execute("SELECT close_ts FROM outcome_close WHERE outcome_id=?", (oid,)).fetchone()
    if not r:
        return {}
    close_ts = r[0]
    out = {}
    for venue, mid in con.execute(
            "SELECT venue, market_id FROM market_outcome WHERE outcome_id=?", (oid,)):
        book = venue.split(":", 1)[-1]
        if book not in config.BOARD_BENCH_BOOKS or "|Over|" not in mid:
            continue
        under = mid.replace("|Over|", "|Under|")
        got = dict(con.execute(
            "SELECT market_id, last FROM quotes WHERE venue=? AND market_id IN (?,?) AND ts=?",
            (venue, mid, under, close_ts)).fetchall())
        if got.get(mid) is not None and got.get(under) is not None:
            out[book] = (got[mid], got[under])
    return out


def leans(rows, prices, threshold):
    out = []
    for r in rows:
        if r["p_bench"] in ("", None):
            continue
        gap = B.gap_pp(float(r["p_model"]), float(r["p_bench"]))
        side = B.lean(gap, threshold)
        if side is None:
            continue
        book_prices = prices.get(r["outcome_id"]) or {}
        dec = [B.american_to_decimal(o if side == "over" else u) for o, u in book_prices.values()]
        if not dec:
            continue
        y = float(r["y"]) == 1.0
        out.append({"market": r["stat"], "band": B.band_for(gap), "gap": gap, "side": side,
                    "cleared": y if side == "over" else not y,
                    "payout": statistics.median(dec), "books": len(dec),
                    "game": r["game_id"], "season": int(r["season"])})
    return out


def band_key(market, band):
    lo, hi = band
    return f"{market}|{lo:g}-{hi:g}" if hi is not None else f"{market}|{lo:g}+"


def summarise(lean_rows):
    from research.sweep import common as SW
    groups = defaultdict(list)
    for x in lean_rows:
        groups[band_key(x["market"], x["band"])].append(x)
    out = {}
    for key, xs in sorted(groups.items()):
        s = B.band_stats(xs)
        roi = SW.boot(xs, lambda rs: statistics.fmean(
            (r["payout"] - 1.0) if r["cleared"] else -1.0 for r in rs) if rs else None)
        lo, hi = wilson(s["k"], s["n"])
        out[key] = {"n": s["n"], "k": s["k"], "cleared": round(s["cleared"], 4),
                    "ci": [round(lo, 4), round(hi, 4)], "ci_method": "wilson_95",
                    "roi": round(s["roi"], 4),
                    "roi_ci": [round(roi["lo"], 4), round(roi["hi"], 4)] if roi else None,
                    "roi_ci_method": "game_block_bootstrap_2000",
                    "games": roi["games"] if roi else None,
                    "by_season": {str(t): {"n": len(v), "k": sum(1 for r in v if r["cleared"])}
                                  for t, v in sorted(_by(xs, "season").items())},
                    "break_even": round(1.0 / statistics.median(r["payout"] for r in xs), 4)}
    return out


def _by(xs, k):
    d = defaultdict(list)
    for x in xs:
        d[x[k]].append(x)
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", required=True, help="walkforward --ledger-out CSV")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    with open(a.ledger, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("walk-forward ledger is empty")
    threshold = config.BOARD_LEAN_THRESHOLD_LOG[0][1]
    con = ro()
    try:
        cands = [r for r in rows if r["p_bench"] not in ("", None)
                 and B.lean(B.gap_pp(float(r["p_model"]), float(r["p_bench"])), threshold)]
        prices = {r["outcome_id"]: close_prices(con, r["outcome_id"]) for r in cands}
    finally:
        con.close()
    lr = leans(rows, prices, threshold)
    if not lr:
        raise SystemExit("no leans reconstructed - refusing to write an empty band table")
    bands = summarise(lr)
    doc = {"generated_by": "research/board_bands.py", "threshold_pp": threshold,
           "seasons": sorted({int(r["season"]) for r in rows}),
           "population": {"ledger_rows": len(rows),
                          "with_p_bench": sum(1 for r in rows if r["p_bench"] not in ("", None)),
                          "leans": len(cands), "leans_priced": len(lr),
                          "leans_unpriced": len(cands) - len(lr)},
           "definitions": __doc__.split("Per (market, band)")[0].strip(),
           "bands": bands}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    print(json.dumps(doc["population"]))
    for k, v in bands.items():
        print(f"  {k:<24} n={v['n']:>5} cleared {v['cleared']:.3f} {v['ci']}  "
              f"roi {v['roi']:+.3f} {v['roi_ci']}  games {v['games']}  BE {v['break_even']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
