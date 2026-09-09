"""Cross-venue price disagreement on outcomes quoted in both places.

    python -m jobs.cross_venue
    python -m jobs.cross_venue --top 20 --bin 300

This is the payoff of the outcome_id work in brief 003: two venues quoting the
same semantic claim can finally be put side by side. It is also the sharpest
available test of whether that mapping is right, because a mapping error and a
real arbitrage look identical in a P&L and completely different here.

Every large disagreement is one of exactly three things, and all three matter:

  real spread     the venues genuinely disagree. Tradeable, if you can cross
                  both books - which is a liquidity question, so the bucket
                  travels with the row.
  stale quote     one side has not moved inside a wide book. The mid is not a
                  price; it is the midpoint of an absence.
  mapping error   the two markets are not actually the same claim. This is the
                  one that silently corrupts everything downstream, and the
                  only way it shows up is as a disagreement too large to be
                  either of the other two.

Prices are matched on a time bin rather than exactly: the two loggers poll on
unrelated schedules, so demanding identical timestamps would find nothing.
"""
import argparse
import sqlite3
import statistics

import config

DEFAULT_BIN = 300           # 5 minutes


def matched(con, bin_seconds=DEFAULT_BIN, sources=("live",)):
    """(outcome_id, bin, kalshi_mid, poly_mid, spreads, volume, ids) pairs.

    Backfilled rows are excluded by default: Polymarket's history carries no
    book at all, so comparing it to a Kalshi two-sided quote would be comparing
    a last price to a midpoint and would manufacture gaps that are not there.
    """
    src_list = list(sources)
    placeholders = ",".join("?" for _ in src_list)
    q = f"""
        WITH binned AS (
            SELECT mo.outcome_id,
                   CAST(q.ts / ? AS INTEGER) AS tbin,
                   q.venue, q.market_id,
                   AVG(q.mid) AS mid,
                   AVG(q.best_ask - q.best_bid) AS spread,
                   MAX(q.volume) AS volume,
                   COUNT(*) AS n
              FROM quotes q
              JOIN market_outcome mo
                ON mo.venue = q.venue AND mo.market_id = q.market_id
             WHERE mo.outcome_id IS NOT NULL AND q.mid IS NOT NULL
               AND q.source IN ({placeholders})
               AND q.venue IN ('kalshi', 'polymarket')
             GROUP BY mo.outcome_id, tbin, q.venue
        )
        SELECT k.outcome_id, k.tbin, k.mid, p.mid, k.spread, p.spread,
               k.volume, k.market_id, p.market_id
          FROM binned k
          JOIN binned p
            ON p.outcome_id = k.outcome_id AND p.tbin = k.tbin
         WHERE k.venue = 'kalshi' AND p.venue = 'polymarket'
    """
    return con.execute(q, (bin_seconds, *src_list)).fetchall()


def classify(diff, k_spread, p_spread, k_mid=None, p_mid=None):
    """Which of the three explanations fits, on the evidence available.

    ORDER MATTERS and got this wrong first time round. Checking "is the gap
    big?" before "does the spread already explain it?" labelled every empty
    Polymarket book a mapping error: an empty CLOB book quotes 0/1, so its mid
    is 0.500 and it disagrees with everything by up to half a dollar while
    telling you nothing at all. A claim of "27 suspect mappings" would have
    sent someone chasing a bug that does not exist.
    """
    widest = max(k_spread or 0.0, p_spread or 0.0)
    # An empty book is not a price. Name it before anything else.
    if (p_spread or 0) >= 0.95 or (k_spread or 0) >= 0.95:
        return "no book"
    if abs(diff) <= widest:
        return "inside the spread"
    if widest >= 0.05:
        return "wide book / stale"
    # Only now is a large gap suspicious: both books are tight and they still
    # disagree by more than a quarter, which no spread explains.
    if abs(diff) > 0.25:
        return "suspect mapping"
    return "real disagreement"


def report(bin_seconds=DEFAULT_BIN, top=20):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    rows = matched(con, bin_seconds, ("live",))
    if not rows:
        print("no matched observations")
        con.close()
        return

    diffs = [k - p for _o, _t, k, p, *_ in rows]
    outcomes = {r[0] for r in rows}
    absd = sorted(abs(d) for d in diffs)

    def pct(p):
        return absd[min(int(len(absd) * p), len(absd) - 1)]

    print(f"matched observations : {len(rows):,} over {len(outcomes):,} outcomes")
    print(f"time bin             : {bin_seconds}s")
    print()
    print("kalshi_mid - polymarket_mid")
    print(f"  mean   {statistics.mean(diffs):+.4f}     "
          f"median {statistics.median(diffs):+.4f}     "
          f"sd {statistics.pstdev(diffs):.4f}")
    print(f"  |diff| p50 {pct(0.50):.4f}  p75 {pct(0.75):.4f}  "
          f"p90 {pct(0.90):.4f}  p95 {pct(0.95):.4f}  p99 {pct(0.99):.4f}  "
          f"max {absd[-1]:.4f}")
    print()
    for lo, hi in ((0, .01), (.01, .02), (.02, .05), (.05, .10), (.10, .25),
                   (.25, 1.01)):
        n = sum(1 for d in absd if lo <= d < hi)
        bar = "#" * int(40 * n / len(absd))
        print(f"  {lo:.2f}-{hi:.2f}  {n:>7,} {n/len(absd):>6.1%}  {bar}")

    print()
    print(f"largest {top} disagreements, per outcome")
    per = {}
    for oid, tbin, k, p, ks, ps, vol, kmkt, pmkt in rows:
        d = k - p
        if oid not in per or abs(d) > abs(per[oid][0]):
            per[oid] = (d, k, p, ks, ps, vol, kmkt, pmkt)
    worst = sorted(per.items(), key=lambda kv: -abs(kv[1][0]))[:top]

    print(f"  {'player / claim':<40} {'line':>5} {'kalshi':>7} {'poly':>7} "
          f"{'diff':>7} {'kspr':>6} {'pspr':>6}  verdict")
    for oid, (d, k, p, ks, ps, vol, kmkt, pmkt) in worst:
        meta = con.execute(
            """SELECT x.display_name, o.stat, o.line
                 FROM outcomes o LEFT JOIN player_xwalk x
                   ON x.gsis_id = o.entity_id WHERE o.outcome_id = ?""",
            (oid,)).fetchone() or (None, None, None)
        label = f"{meta[0] or oid} {meta[1] or ''}".strip()[:40]
        print(f"  {label:<40} {(meta[2] if meta[2] is not None else 0):>5g} "
              f"{k:>7.3f} {p:>7.3f} {d:>+7.3f} "
              f"{(ks or 0):>6.3f} {(ps or 0):>6.3f}  "
              f"{classify(d, ks, ps)}")

    print()
    counts = {}
    for oid, (d, k, p, ks, ps, *_rest) in per.items():
        counts[classify(d, ks, ps)] = counts.get(classify(d, ks, ps), 0) + 1
    print("verdicts across all both-venue outcomes:")
    for k2, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k2:<20} {v:>5,}")
    print()
    print("  `suspect mapping` is the one to chase first: a real spread costs")
    print("  you a trade, a bad mapping corrupts every number downstream.")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", type=int, default=DEFAULT_BIN)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    report(args.bin, args.top)


if __name__ == "__main__":
    main()
