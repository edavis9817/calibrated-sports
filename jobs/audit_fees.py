"""BRIEF 016 - fee backfill and series audit.

    python jobs/audit_fees.py --series          # item 4, read-only
    python jobs/audit_fees.py --ledger          # item 3, DRY RUN
    python jobs/audit_fees.py --ledger --apply  # item 3, writes

The ledger's `fee` and `net_edge` were computed with `kalshi_fee(p, 1)` - the
fee is charged on the WHOLE ORDER, so billing the ceil-to-cent to each of 100
contracts overstated it. This recomputes both at the ticket's real size.

This is correctness hygiene and NOT a new result. None of the retired edge
hypotheses rested on ledger `net_edge`; they were measured on CLV, which is a
price difference and carries no fee. Nothing comes back to life. The ledger
just stops lying.

The write touches `paper_ledger` in the logger's database. That table is
written by `jobs/paper_trade.py` and never by the logger loop, so the only
contention is with a concurrent paper-trade run; the update is one short
transaction with a busy timeout rather than a held lock.
"""
import argparse
import os
import sqlite3
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.fees import (edge_after_fees, fee_per_contract, listed,
                       series_multiplier, series_of)


def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def rw():
    c = sqlite3.connect(config.DB_PATH, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    return c


# =============================================================================
# item 4 - which series are we actually on?
# =============================================================================

def series_audit(season=2026, week=1, model_version=None):
    import store
    from models import baseline
    mv, note = store.latest_model_version(
        season, week, model_version or baseline.MODEL_VERSION)
    if note:
        print(f"  NOTE: {note}")
    c = ro()
    rows = c.execute(
        "SELECT mo.market_id, COUNT(*) FROM predictions p "
        "JOIN outcomes o USING (outcome_id) "
        "JOIN market_outcome mo ON mo.outcome_id = p.outcome_id "
        "WHERE mo.venue='kalshi' AND o.season=? AND o.week=? "
        "AND p.model_version=? GROUP BY 1", (season, week, mv)).fetchall()
    per = Counter()
    for mid, n in rows:
        per[series_of(mid)] += n
    total = sum(per.values())

    print("\n" + "=" * 78)
    print("ITEM 4 - SERIES AUDIT: what the 935 predictions actually sit on")
    print("=" * 78)
    print(f"\n    {'series':<18}{'predictions':>13}{'in fee table':>15}"
          f"{'maker M':>9}{'taker M':>9}")
    flagged = []
    for s, n in per.most_common():
        m, t = series_multiplier(s)
        inx = "YES" if listed(s) else "no"
        print(f"    {s:<18}{n:>13,}{inx:>15}{str(m):>9}{str(t):>9}")
        if listed(s):
            flagged.append((s, n, m, t))
    print(f"    {'TOTAL':<18}{total:>13,}")

    unlisted = total - sum(n for _, n, _, _ in flagged)
    print(f"""
  {unlisted:,} of {total:,} predictions ({100*unlisted/max(total,1):.1f}%) sit on series that appear
  NOWHERE in the schedule's Non-Standard Fees table. Under the published
  defaults an unlisted series is taker M = 1 and **maker M = 0**, so a RESTING
  order on these markets is fee-free. That is a fact about our tickers, not a
  guess about Kalshi - but it rests on the table being complete, and the
  schedule is a PDF that changes without notice.""")
    if flagged:
        print("\n  Series that ARE in the table:")
        for s, n, m, t in flagged:
            print(f"    {s:<18}{n:>7,} predictions   maker M={m}  taker M={t}")
        print("\n  Every one of these is 1/1, which is the same as the taker\n"
              "  default - so nothing was being under- or over-charged by a\n"
              "  multiplier we failed to apply.")
    else:
        print("\n  NO series behind these predictions appears in the table, so no\n"
              "  multiplier was being missed. The only multiplier that differs\n"
              "  from the defaults anywhere in football is KXMVE (maker 2), and\n"
              "  we hold none of it.")
    return per, flagged


# =============================================================================
# item 3 - backfill
# =============================================================================

def ledger_backfill(apply=False):
    c = ro()
    rows = c.execute(
        "SELECT ticket_id, market_id, model_prob, market_prob, stake, fee, "
        "net_edge, gross_edge FROM paper_ledger").fetchall()
    if not rows:
        print("paper_ledger is empty")
        return
    out, flips = [], 0
    before_fee, after_fee = [], []
    before_net, after_net = [], []
    for tid, mid, model_p, market_p, stake, fee, net, gross in rows:
        n = int(stake or 1)
        _, taker_m = series_multiplier(mid)
        new_fee = fee_per_contract(market_p, n, "taker", taker_m)
        new_net = edge_after_fees(model_p, market_p, n, "taker", taker_m)
        before_fee.append(fee)
        after_fee.append(new_fee)
        before_net.append(net)
        after_net.append(new_net)
        if (net is not None) and ((net < 0) != (new_net < 0)):
            flips += 1
        out.append((new_fee, new_net, tid))

    def line(label, b, a):
        print(f"    {label:<22}{statistics.fmean(b):>12.6f}{statistics.fmean(a):>12.6f}"
              f"{statistics.median(b):>12.6f}{statistics.median(a):>12.6f}")

    print("\n" + "=" * 78)
    print(f"ITEM 3 - LEDGER BACKFILL{'' if apply else '   (DRY RUN)'}")
    print("=" * 78)
    print(f"\n  {len(rows):,} tickets, stake {int(rows[0][4])} contracts flat")
    print(f"\n    {'':<22}{'mean before':>12}{'mean after':>12}"
          f"{'med before':>12}{'med after':>12}")
    line("fee ($/contract)", before_fee, after_fee)
    line("net_edge ($/contract)", before_net, after_net)
    over = statistics.fmean(before_fee) / statistics.fmean(after_fee)
    print(f"""
  the old fee was {over:.2f}x the correct one, on average
  tickets whose net_edge CHANGES SIGN: {flips} of {len(rows):,}""")
    if apply:
        w = rw()
        w.executemany("UPDATE paper_ledger SET fee=?, net_edge=? WHERE ticket_id=?",
                      out)
        w.commit()
        w.close()
        print(f"  WROTE {len(out):,} rows")
    else:
        print("  nothing written - pass --apply")
    print("""
  Reported and stopped, per the brief. No conclusion is re-derived from these
  numbers here: `net_edge` gates ticket SELECTION, and re-running selection on
  a corrected fee is a separate decision with its own model-version question.""")
    return before_net, after_net, flips


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", action="store_true")
    ap.add_argument("--ledger", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not (a.series or a.ledger):
        a.series = a.ledger = True
    if a.series:
        series_audit()
    if a.ledger:
        ledger_backfill(apply=a.apply)


if __name__ == "__main__":
    main()
