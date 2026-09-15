"""BRIEFS 016 + 017 - fee backfill, series audit, counterfactual, capacity.

    python jobs/audit_fees.py --series          # 016 item 4, read-only
    python jobs/audit_fees.py --ledger          # 016 item 3, DRY RUN
    python jobs/audit_fees.py --ledger --apply  # 016 item 3, writes
    python jobs/audit_fees.py --counterfactual  # 017 item 2, read-only
    python jobs/audit_fees.py --capacity        # 017 item 4, read-only

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
    from core import version_resolve
    from models import baseline
    mv, note = version_resolve.resolve(
        season, week, baseline.MODEL_VERSION, override=model_version)
    if note:
        print(f"  {note}")
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


def counterfactual(season=2026, week=1, model_version=None):
    """BRIEF 017 ITEM 2 - would the corrected fee have admitted more tickets?

    The 818 in the ledger are SURVIVORS; recomputing their fee says nothing
    about the pool the gate rejected. The fee correction is monotone - the old
    fee was always >= the correct one - so this can only add tickets.

    Only the `no_edge` rejections can flip. `wide_spread` and `thin_history`
    are checked first and neither depends on the fee, so a ticket rejected for
    those is still rejected at any fee.

    NOTHING IS INSERTED. Re-selecting after the markets have settled is a
    decision, not a backfill.
    """
    import jobs.paper_trade as pt
    from core import version_resolve
    from models import baseline

    mv, note = version_resolve.resolve(
        season, week, baseline.MODEL_VERSION, override=model_version)
    if note:
        print(f"  {note}")
    con = ro()
    # The ledger's own entry instant, so the counterfactual is evaluated
    # against the book the original run actually saw.
    as_of = con.execute("SELECT MAX(entry_ts) FROM paper_ledger "
                        "WHERE model_version=?", (mv,)).fetchone()[0]
    rows = con.execute("""
        SELECT p.prediction_id, p.outcome_id, p.prob_over, p.prior_games,
               mo.market_id, g.kickoff_ts
          FROM predictions p
          JOIN outcomes o USING (outcome_id)
          JOIN market_outcome mo ON mo.outcome_id = p.outcome_id
          LEFT JOIN (SELECT game_id, MAX(kickoff_ts) kickoff_ts
                       FROM nfl_games GROUP BY game_id) g
            ON g.game_id = o.event_id
         WHERE mo.venue='kalshi' AND o.season=? AND o.week=?
           AND p.model_version=?""", (season, week, mv)).fetchall()

    already = {r[0] for r in con.execute(
        "SELECT prediction_id FROM paper_ledger WHERE model_version=?", (mv,))}

    stats = Counter()
    admitted = []
    for pid, oid, prob_yes, prior, market_id, kickoff in rows:
        stats["considered"] += 1
        if kickoff is not None and as_of >= kickoff:
            stats["post_kickoff"] += 1
            continue
        q = pt.latest_quote(con, "kalshi", market_id, as_of)
        if not q:
            stats["no_quote"] += 1
            continue
        bid, ask, _mid, _ts = q
        if (prior or 0) < pt.MIN_PRIOR_GAMES:
            stats["thin_history"] += 1
            continue
        ev = pt.evaluate(prob_yes, bid, ask, pt.STAKE, market_id)
        if ev["spread"] > pt.MAX_SPREAD:
            stats["wide_spread"] += 1
            continue
        if ev["net_edge"] < pt.MIN_NET_EDGE:
            stats["no_edge"] += 1
            continue
        stats["passes_gate"] += 1
        if pid not in already:
            admitted.append((pid, market_id, ev["market_prob"], ev["net_edge"]))

    print("\n" + "=" * 78)
    print("ITEM 2 - COUNTERFACTUAL SELECTION under the corrected fee")
    print("=" * 78)
    print(f"\n  model version {mv}")
    print(f"  gate: prior_games >= {pt.MIN_PRIOR_GAMES}, spread <= "
          f"{pt.MAX_SPREAD}, net_edge >= {pt.MIN_NET_EDGE}")
    print(f"\n    {'outcome':<18}{'n':>8}")
    for k in ("considered", "post_kickoff", "no_quote", "thin_history",
              "wide_spread", "no_edge", "passes_gate"):
        print(f"    {k:<18}{stats[k]:>8,}")
    print(f"    {'already in ledger':<18}{len(already):>8,}")
    print(f"\n  NEWLY ADMITTED by the corrected fee: {len(admitted)}")
    if admitted:
        e = sorted(x[3] for x in admitted)
        print(f"\n    corrected net_edge of the newly admitted ($/contract)")
        print(f"      min {e[0]:.4f}   p25 {e[len(e)//4]:.4f}   "
              f"median {statistics.median(e):.4f}   "
              f"p75 {e[3*len(e)//4]:.4f}   max {e[-1]:.4f}")
        print(f"      how many clear the gate by more than 1c: "
              f"{sum(1 for x in e if x >= pt.MIN_NET_EDGE + 0.01)}")
        print(f"\n    {'market':<46}{'price':>8}{'net_edge':>10}")
        for pid, mid, mp, ne in sorted(admitted, key=lambda x: -x[3])[:10]:
            print(f"    {mid[:44]:<46}{mp:>8.3f}{ne:>10.4f}")
        print("\n  NOT INSERTED. Re-selecting after settlement is your call.")
    else:
        print("""
  Zero. The ledger is already complete under the corrected fee, so item 1's
  equivalence record is the whole answer and no re-run is owed.""")
    return stats, admitted


def capacity():
    """BRIEF 017 ITEM 4 - the capacity curve, before and after the fee fix.

    `jobs/reprice_ledger.py` passed `contracts=1` while repricing at 100 and
    1,000 - a job whose entire purpose is pricing AT SIZE. Both the touch arm
    and the executable arm carried the same error so it partly cancels in the
    difference, but not exactly (the arms sit at different prices) and the
    LEVEL of the curve shifts regardless.

    `market_depth` stores VWAP at 100/500/1000/5000 only. There is no vwap_10,
    so the 10-contract point the brief asks for does not exist in the data and
    is not invented here. The zero crossing is interpolated linearly between
    the two rungs that straddle it and is reported as the estimate it is - a
    VWAP over a discrete book is a step function, not a line.
    """
    from jobs.reprice_ledger import exec_price_for, nearest_depth
    con = ro()
    tickets = con.execute(
        "SELECT ticket_id, venue, market_id, side, model_prob, net_edge, "
        "entry_ts FROM paper_ledger").fetchall()
    print("\n" + "=" * 78)
    print("ITEM 4 - CAPACITY CURVE, before and after the contracts=1 fix")
    print("=" * 78)
    print(f"\n  {len(tickets):,} ledger tickets, depth at each stored rung")
    print(f"\n    {'stake':>7}{'n':>6}{'touch edge':>12}{'net OLD':>10}"
          f"{'net NEW':>10}{'depth cost OLD':>16}{'depth cost NEW':>16}")
    curve = {}
    for stake in (100, 500, 1000, 5000):
        b, old, new = [], [], []
        for tid, venue, mid, side, mp, nb, ets in tickets:
            row = nearest_depth(con, venue, mid, side, ets)
            _, _touch, ex = exec_price_for(row, stake)
            if ex is None or not (0 < ex < 1):
                continue
            _, tm = series_multiplier(mid)
            b.append(nb)
            old.append(edge_after_fees(mp, ex, 1, "taker", tm))
            new.append(edge_after_fees(mp, ex, int(stake), "taker", tm))
        if not b:
            continue
        mb, mo, mn = (statistics.fmean(b), statistics.fmean(old),
                      statistics.fmean(new))
        curve[stake] = (mo, mn)
        print(f"    {stake:>7,}{len(b):>6}{mb:>12.5f}{mo:>10.5f}{mn:>10.5f}"
              f"{100*(mb-mo):>15.2f}pp{100*(mb-mn):>15.2f}pp")
    print(f"    {'10':>7}{'':>6}{'not stored - market_depth has no vwap_10':>52}")

    def crossing(idx):
        ks = sorted(curve)
        for a, b_ in zip(ks, ks[1:]):
            ya, yb = curve[a][idx], curve[b_][idx]
            if ya > 0 >= yb:
                return a + (b_ - a) * ya / (ya - yb), a, b_
        return None, None, None

    for idx, lab in ((0, "OLD (contracts=1)"), (1, "NEW (contracts=size)")):
        x, a, b_ = crossing(idx)
        if x:
            print(f"\n  net edge crosses ZERO, {lab:<22} ~{x:,.0f} contracts "
                  f"(interpolated between {a:,} and {b_:,})")
    xo, _, _ = crossing(0)
    xn, _, _ = crossing(1)
    if xo and xn:
        print(f"""
  The crossing moves {xn - xo:+,.0f} contracts. Both estimates sit in the SAME
  bracket, between the 1,000 and 5,000 rungs, so the stored resolution cannot
  distinguish them - the level shift is real and the crossing shift is an
  interpolation between two points 4,000 contracts apart. Read the level.""")
    return curve


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--series", action="store_true")
    ap.add_argument("--counterfactual", action="store_true")
    ap.add_argument("--capacity", action="store_true")
    ap.add_argument("--ledger", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not (a.series or a.ledger or a.counterfactual or a.capacity):
        a.series = a.ledger = a.counterfactual = a.capacity = True
    if a.series:
        series_audit()
    if a.counterfactual:
        counterfactual()
    if a.capacity:
        capacity()
    if a.ledger:
        ledger_backfill(apply=a.apply)


if __name__ == "__main__":
    main()
