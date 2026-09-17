"""Does the longshot bias survive on the venue we can actually trade?

    python -m research.longshot --kalshi      # arm we can trade
    python -m research.longshot --books       # arm the finding came from
    python -m research.longshot --fees        # net of fees, maker and taker
    python -m research.longshot --artifact    # is it a single-book artifact?
    python -m research.longshot --all

THE FINDING UNDER TEST. Brief 012 measured the closing market pricing 0.1375 in
the 0.10-0.15 bucket against a realized 0.0674. F01 showed Shin de-vig only
moves that to 0.1250, so ~83% of the gap is the market's opinion rather than the
bookmaker's margin. At those prices a Kalshi fee is well under a cent, so the
edge is large relative to cost - which is what makes it worth this much care.

TWO THINGS STAND BETWEEN THAT AND A STRATEGY, and this brief only measures them:

  VENUE   the effect was measured on SPORTSBOOKS and we can only trade Kalshi.
          A bias that exists at DraftKings and not on the exchange is real and
          untradeable.
  SAMPLE  n=89 in the original bucket. A 5.8pp effect there is ~1.9 SE, which
          is not enough to size anything.

Nothing here sizes or trades. It answers whether there is something to size.
"""
import argparse
import math
import sqlite3
import statistics
from collections import defaultdict

import config
from core.fees import fee_per_contract
from research.implied import shin_devig

# Finer than brief 012's 0.05 bins. The whole question lives between 0.05 and
# 0.20 and a single 0.05 bin there pools a 2-to-1 range of prices.
FINE_EDGES = [0.02, 0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20, 0.25, 0.30]


def _ro():
    return sqlite3.connect("file:%s?mode=ro" % config.DB_PATH, uri=True)


def fine_bucket(p):
    for i in range(len(FINE_EDGES) - 1):
        if FINE_EDGES[i] <= p < FINE_EDGES[i + 1]:
            return f"{FINE_EDGES[i]:.3f}-{FINE_EDGES[i+1]:.3f}"
    return None


def wilson(k, n, z=1.96):
    """Wilson interval. At n=11 and p=0.09 a normal interval runs below zero,
    which is where every naive longshot standard error in this project has
    been quoted from."""
    if n == 0:
        return (0.0, 0.0)
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    m = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    # Clamped. The closed form can overshoot 1.0 by a float epsilon at k=n,
    # and a probability of 1.0000000000000002 in a report is the kind of detail
    # that makes a reader doubt the rest of the table.
    return (min(max((c - m) / d, 0.0), 1.0), min(max((c + m) / d, 0.0), 1.0))


# ---------------------------------------------------------------- KALSHI arm

KALSHI_SQL = """
SELECT o.outcome_id, o.stat, o.side, s.result, q.best_bid, q.best_ask, q.ts,
       g.kickoff_ts
  FROM outcome_settlement s
  JOIN outcomes o        ON o.outcome_id = s.outcome_id
  JOIN market_outcome mo ON mo.outcome_id = o.outcome_id AND mo.venue = 'kalshi'
  JOIN (SELECT game_id, MAX(kickoff_ts) kickoff_ts FROM nfl_games
         GROUP BY game_id) g ON g.game_id = o.event_id
  JOIN quotes q ON q.venue = 'kalshi' AND q.market_id = mo.market_id
 WHERE q.best_bid IS NOT NULL AND q.best_ask IS NOT NULL
   -- A VOID IS NOT A MISS. `kalshi_rows` scores `hit` as `res == side`, and a
   -- void equals neither side, so every voided outcome would land as a realized
   -- 0 - depressing precisely the longshot buckets this study reports on.
   -- BOOK_SQL below already filters this way; the two arms of one study
   -- disagreed. No effect on any published number: there are no void rows yet,
   -- which is why this lands BEFORE the migration that creates them.
   AND s.result IN ('over', 'under')
   AND q.ts <= g.kickoff_ts
   AND q.ts = (SELECT MAX(ts) FROM quotes q2
                WHERE q2.venue = 'kalshi' AND q2.market_id = mo.market_id
                  AND q2.ts <= g.kickoff_ts
                  AND q2.best_bid IS NOT NULL AND q2.best_ask IS NOT NULL)
"""


def kalshi_rows():
    """Settled Kalshi outcomes with their last pre-kickoff two-sided quote.

    No de-vig. Kalshi is an exchange: the spread is the cost of crossing, not a
    bookmaker's margin, so the mid IS the price and Shin has nothing to remove.
    """
    con = _ro()
    out = []
    for oid, stat, side, res, bid, ask, ts, kick in con.execute(KALSHI_SQL):
        out.append({
            "stat": stat, "side": side,
            "price": (bid + ask) / 2.0, "bid": bid, "ask": ask,
            "spread": ask - bid,
            "hit": 1 if res == side else 0,
            "lead_min": (kick - ts) / 60.0,
        })
    con.close()
    return out


# ------------------------------------------------------------ SPORTSBOOK arm

BOOK_SQL = """
SELECT o.entity_id, o.event_id, o.stat, o.line, o.side, s.result,
       c.p_all, c.n_all, c.dispersion
  FROM outcome_settlement s
  JOIN outcomes o      ON o.outcome_id = s.outcome_id
  JOIN outcome_close c ON c.outcome_id = s.outcome_id
 WHERE o.entity_type = 'player' AND o.line IS NOT NULL AND c.p_all IS NOT NULL
   AND s.result IN ('over', 'under')
"""


def book_rows():
    """Settled sportsbook outcomes, Shin de-vigged from the consensus pair.

    outcome_close.p_all is the MULTIPLICATIVE de-vig from brief 010. Re-derived
    here with Shin because that is the whole point: multiplicative leaves the
    longshot too high, and the size of the effect depends on which method is
    used to remove the margin.
    """
    con = _ro()
    sides = defaultdict(dict)
    meta = {}
    density = defaultdict(set)
    for gsis, event, stat, line, side, res, p, nb, disp in con.execute(BOOK_SQL):
        key = (gsis, event, stat, line)
        sides[key][side] = (p, res)
        meta[key] = {"n_books": nb or 0, "dispersion": disp,
                     "stat": stat, "gsis": gsis, "event": event}
        density[(gsis, event, stat)].add(line)

    out = []
    for key, sd in sides.items():
        if "over" not in sd or "under" not in sd:
            continue
        (p_over, res), (p_under, _r2) = sd["over"], sd["under"]
        fair_over = shin_devig(p_over, p_under)
        if fair_over is None:
            continue
        m = meta[key]
        n_lines = len(density[(key[0], key[1], key[2])])
        for side, fair in (("over", fair_over), ("under", 1.0 - fair_over)):
            out.append({
                "stat": m["stat"], "side": side, "price": fair,
                "vigged": p_over if side == "over" else p_under,
                "hit": 1 if res == side else 0,
                "n_books": m["n_books"], "dispersion": m["dispersion"],
                "n_lines": n_lines,
            })
    con.close()
    return out


# ---------------------------------------------------------------- reporting

def bucket_table(rows, label, price_key="price", lo=0.02, hi=0.30):
    """Realized vs priced in fine buckets, with Wilson intervals and n."""
    b = defaultdict(list)
    for r in rows:
        p = r[price_key]
        if p is None or not (lo <= p < hi):
            continue
        k = fine_bucket(p)
        if k:
            b[k].append(r)
    print(f"\n  {label}")
    print(f"    {'bucket':<15}{'n':>7}{'priced':>9}{'realized':>10}{'edge pp':>9}"
          f"{'95% CI on realized':>24}{'sig':>5}")
    total = 0
    for k in sorted(b, key=lambda x: float(x.split("-")[0])):
        v = b[k]
        n = len(v)
        total += n
        priced = statistics.fmean(x[price_key] for x in v)
        hits = sum(x["hit"] for x in v)
        realized = hits / n
        loci, hici = wilson(hits, n)
        # significant when the priced value sits outside the realized interval
        sig = "*" if priced < loci or priced > hici else ""
        print(f"    {k:<15}{n:>7,}{priced:>9.4f}{realized:>10.4f}"
              f"{100*(priced-realized):>+9.2f}"
              f"{'['+format(loci,'.4f')+', '+format(hici,'.4f')+']':>24}{sig:>5}")
    print(f"    {'TOTAL':<15}{total:>7,}")
    return b


def fee_table(buckets, price_key="price"):
    """Edge net of the Kalshi fee, maker and taker, at each price level.

    Fees are computed at SIZE. kalshi_fee rounds up to whole cents, so one
    contract at P=0.12 bills a cent for a 0.07c fee - a 14x overstatement at
    exactly the prices this brief is about.
    """
    size = 1000
    print(f"\n    {'bucket':<15}{'n':>7}{'gross pp':>10}{'taker pp':>10}"
          f"{'maker pp':>10}{'net taker':>11}{'net maker':>11}")
    for k in sorted(buckets, key=lambda x: float(x.split("-")[0])):
        v = buckets[k]
        n = len(v)
        priced = statistics.fmean(x[price_key] for x in v)
        realized = sum(x["hit"] for x in v) / n
        gross = priced - realized              # edge to the SELLER of the longshot
        taker = fee_per_contract(priced, size, "taker")
        # Brief 016: maker fees keep an EXPLICIT multiplier=1 here so this
        # previously-reported number does not move under the new default.
        # Under the published defaults an UNLISTED series - which is every
        # NFL player prop - is maker M=0, i.e. free. That makes the maker
        # figures below conservative. See docs/kalshi-fee-mechanics.md.
        maker = fee_per_contract(priced, size, "maker", multiplier=1)
        print(f"    {k:<15}{n:>7,}{100*gross:>10.2f}{100*taker:>10.2f}"
              f"{100*maker:>10.2f}{100*(gross-taker):>+11.2f}"
              f"{100*(gross-maker):>+11.2f}")


def strat_table(rows, keyfn, label, lo=0.05, hi=0.20):
    """The artifact check: is the bias concentrated in the worst inputs?"""
    b = defaultdict(list)
    for r in rows:
        if r["price"] is not None and lo <= r["price"] < hi:
            b[keyfn(r)].append(r)
    print(f"\n  {label}  (longshot zone {lo:.2f}-{hi:.2f})")
    print(f"    {'stratum':<20}{'n':>7}{'priced':>9}{'realized':>10}{'edge pp':>9}"
          f"{'95% CI':>22}")
    for k in sorted(b, key=lambda x: -len(b[x])):
        v = b[k]
        n = len(v)
        if n < 20:
            print(f"    {str(k):<20}{n:>7,}   (below the 20-observation floor)")
            continue
        priced = statistics.fmean(x["price"] for x in v)
        hits = sum(x["hit"] for x in v)
        lo_ci, hi_ci = wilson(hits, n)
        print(f"    {str(k):<20}{n:>7,}{priced:>9.4f}{hits/n:>10.4f}"
              f"{100*(priced-hits/n):>+9.2f}"
              f"{'['+format(lo_ci,'.3f')+', '+format(hi_ci,'.3f')+']':>22}")


# ------------------------------------------------------------------ spreads

def executable_spread():
    """What it costs to cross, at longshot prices, on each venue.

    A 5.8pp edge inside a 27c spread is not an edge. Kalshi's spread is
    observable directly. A sportsbook's is not quoted as a spread at all, so
    the comparable number is the OVERROUND - what the two sides sum to before
    de-vigging - which is the same thing in a different coat.
    """
    con = _ro()
    out = {}
    ks = con.execute("""
        SELECT q.best_bid, q.best_ask FROM quotes q
          JOIN market_outcome mo ON mo.venue='kalshi' AND mo.market_id=q.market_id
          JOIN outcomes o ON o.outcome_id=mo.outcome_id
         WHERE q.venue='kalshi' AND q.best_bid IS NOT NULL AND q.best_ask IS NOT NULL
           AND o.entity_type='player'
           AND (q.best_bid+q.best_ask)/2 BETWEEN 0.05 AND 0.20""").fetchall()
    if ks:
        sp = sorted(a - b for b, a in ks)
        out["kalshi"] = {"n": len(sp), "median": statistics.median(sp),
                         "p25": sp[len(sp)//4], "p75": sp[3*len(sp)//4]}

    # depth: what a real size costs against the top of book
    try:
        dep = con.execute("""
            SELECT d.touch_price, d.vwap_100, d.vwap_1000 FROM market_depth d
             WHERE d.venue='kalshi' AND d.touch_price BETWEEN 0.05 AND 0.20
               AND d.vwap_1000 IS NOT NULL""").fetchall()
        if dep:
            s100 = sorted(v - t for t, v, _w in dep if v)
            s1000 = sorted(w - t for t, _v, w in dep if w)
            out["kalshi_depth"] = {
                "n": len(dep),
                "slip_100": statistics.median(s100) if s100 else None,
                "slip_1000": statistics.median(s1000) if s1000 else None}
    except sqlite3.OperationalError:
        pass

    # RAW vigged prices, not p_all. outcome_close.p_all is the multiplicative
    # de-vig from brief 010 and sums to 1.0 across a pair BY CONSTRUCTION - the
    # first version of this query measured that and reported a 0.0% overround,
    # which is not a number any bookmaker has ever quoted.
    ov = con.execute("""
        SELECT qa.mid, qb.mid
          FROM outcomes oa
          JOIN market_outcome ma ON ma.outcome_id=oa.outcome_id
                                AND ma.venue LIKE 'oddsapi:%'
          JOIN quotes qa ON qa.venue=ma.venue AND qa.market_id=ma.market_id
          JOIN outcomes ob ON ob.entity_id=oa.entity_id AND ob.event_id=oa.event_id
                          AND ob.stat=oa.stat AND ob.line=oa.line AND ob.side='under'
          JOIN market_outcome mb ON mb.outcome_id=ob.outcome_id
                                AND mb.venue = ma.venue
          JOIN quotes qb ON qb.venue=mb.venue AND qb.market_id=mb.market_id
                        AND qb.ts = qa.ts
         WHERE oa.side='over' AND qa.mid IS NOT NULL AND qb.mid IS NOT NULL
           AND qa.mid BETWEEN 0.05 AND 0.20
         LIMIT 40000""").fetchall()
    if ov:
        rr = sorted(x + y for x, y in ov if x and y)
        out["book_overround"] = {"n": len(rr), "median": statistics.median(rr),
                                 "p75": rr[3*len(rr)//4]}
    con.close()
    return out


def power_note(n_now, per_game, effect=0.058, p0=0.125):
    """per_game is MEASURED from the settled sample, not assumed."""
    for power, zb in ((0.80, 0.842), (0.90, 1.282)):
        za = 1.960
        n = ((za * math.sqrt(p0 * (1 - p0))
              + zb * math.sqrt((p0 - effect) * (1 - p0 - effect))) / effect) ** 2
        games = max(0.0, (n - n_now) / per_game)
        print(f"    power {power:.0%} at alpha 0.05 needs n={n:,.0f}"
              f"  ->  {games:,.0f} more games, about {games/14:.1f} NFL weeks")


def why_no_longshots():
    """Where each stat can even PRICE a longshot.

    THE ANSWER TO THE WHOLE BRIEF. A sportsbook hangs an over/under line near
    the median outcome, so the de-vigged probability clusters at 0.5 and has a
    FLOOR: receiving yards and receptions never go below 0.165, rush attempts
    never below 0.338. There is no longshot zone for them to be biased in.

    Sacks is the exception, because "over 0.5 sacks" is a natural threshold
    that sits near 0.10 for most defenders. So every observation below 0.15 in
    this dataset is a sack prop - and the "longshot bias" is a statement about
    one stat, on one side, from one book, not about longshots.
    """
    br = book_rows()
    per = defaultdict(list)
    for r in br:
        if r["price"] is not None:
            per[r["stat"]].append(r["price"])
    print("\n  de-vigged price floor by stat - can this market quote a longshot?")
    print(f"    {'stat':<18}{'n':>9}{'min':>8}{'p1':>8}{'p10':>8}{'median':>9}"
          f"{'n < 0.15':>10}")
    for st, v in sorted(per.items(), key=lambda kv: -len(kv[1])):
        v = sorted(v)
        n = len(v)
        print(f"    {st:<18}{n:>9,}{v[0]:>8.3f}{v[n//100]:>8.3f}{v[n//10]:>8.3f}"
              f"{v[n//2]:>9.3f}{sum(1 for x in v if x < 0.15):>10,}")
    sub = [r for r in br if r["price"] is not None and r["price"] < 0.15]
    print(f"\n  every one of the {len(sub)} observations below 0.15 is:")
    for f in ("stat", "side"):
        vals = {r[f] for r in sub}
        print(f"    {f:<8} {sorted(vals)}")
    print(f"    books    {sorted({int(r['n_books']) for r in sub})}")
    print(f"    lines    {sorted({r['n_lines'] for r in sub})}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for f in ("kalshi", "books", "fees", "artifact", "spread", "all"):
        ap.add_argument(f"--{f}", action="store_true")
    a = ap.parse_args()

    if a.kalshi or a.all:
        print("=" * 86)
        print("ARM 1 - KALSHI, the venue we can trade")
        print("=" * 86)
        kr = kalshi_rows()
        print(f"\n  settled outcomes with a pre-kickoff two-sided quote: {len(kr):,}")
        con = _ro()
        ng = con.execute("""SELECT COUNT(DISTINCT o.event_id) FROM outcome_settlement s
            JOIN outcomes o ON o.outcome_id=s.outcome_id
            JOIN market_outcome mo ON mo.outcome_id=o.outcome_id AND mo.venue='kalshi'
        """).fetchone()[0]
        con.close()
        print(f"  from {ng} settled games - Kalshi player props exist only for 2026 wk1")
        b = bucket_table(kr, "Kalshi closing mid vs realized")
        n_ls = sum(len(v) for k, v in b.items()
                   if 0.05 <= float(k.split("-")[0]) < 0.20)
        print(f"\n  LONGSHOT ZONE 0.05-0.20: n = {n_ls}")
        print("  This does not answer the question. At n=11 in a bucket the Wilson")
        print("  interval is roughly +/-10pp and the effect being hunted is 5.8pp.")
        per_game = n_ls / max(ng, 1)
        print(f"  accumulating {n_ls/max(ng,1):.0f} longshot observations per "
              f"settled game")
        power_note(n_ls, per_game)

    if a.books or a.all:
        print("\n" + "=" * 86)
        print("ARM 2 - SPORTSBOOKS, where the finding came from (Shin de-vigged)")
        print("=" * 86)
        br = book_rows()
        bb = bucket_table(br, "Shin-de-vigged close vs realized, fine buckets")
        if a.fees or a.all:
            print("\n  NET OF FEES - Kalshi fee at 1,000 contracts, per contract")
            print("  gross is the edge to the SELLER of the longshot")
            fee_table(bb)

    if a.artifact or a.all:
        print("\n" + "=" * 86)
        print("ARTIFACT CHECK - is the bias concentrated in the worst inputs?")
        print("=" * 86)
        br = book_rows()
        # THE ZONE MATTERS. 0.05-0.20 is 94% populated above 0.15, where the
        # market is calibrated to two decimal places, so stratifying the whole
        # zone measures the calibrated part and averages the effect away.
        for lo, hi, note in ((0.05, 0.20, "whole zone - dominated by 0.15-0.20"),
                             (0.02, 0.15, "WHERE THE EFFECT ACTUALLY LIVES")):
            print(f"\n  --- {note} ---")
            strat_table(br, lambda r: (
                "1 book" if r["n_books"] < 2 else
                f"{int(r['n_books'])} books" if r["n_books"] < 5 else "5+ books"),
                "by book count", lo, hi)
            strat_table(br, lambda r: (
                "1-2 lines" if r["n_lines"] <= 2 else
                "3-4 lines" if r["n_lines"] <= 4 else "5+ lines"),
                "by ladder density", lo, hi)
            strat_table(br, lambda r: r["stat"], "by stat", lo, hi)

    if a.artifact or a.all:
        print("\n" + "=" * 86)
        print("WHY THERE IS ALMOST NO LONGSHOT ZONE TO MEASURE")
        print("=" * 86)
        why_no_longshots()

    if a.spread or a.all:
        print("\n" + "=" * 86)
        print("EXECUTABLE COST at longshot prices")
        print("=" * 86)
        sp = executable_spread()
        k = sp.get("kalshi")
        if k:
            print(f"\n  Kalshi quoted spread, mid in 0.05-0.20  (n={k['n']:,})")
            print(f"    p25 {k['p25']*100:.1f}c   median {k['median']*100:.1f}c"
                  f"   p75 {k['p75']*100:.1f}c")
        d = sp.get("kalshi_depth")
        if d:
            print(f"\n  Kalshi slippage from touch  (n={d['n']:,} book-snapshots)")
            if d["slip_100"] is not None:
                print(f"    100 contracts  {d['slip_100']*100:+.2f}c")
            if d["slip_1000"] is not None:
                print(f"    1000 contracts {d['slip_1000']*100:+.2f}c")
        o = sp.get("book_overround")
        if o:
            print(f"\n  Sportsbook overround on longshot pairs  (n={o['n']:,})")
            print(f"    median {o['median']:.4f}   p75 {o['p75']:.4f}")
            print(f"    i.e. the two sides sum to {100*(o['median']-1):.1f}% over fair;")
            print(f"    half of that, {100*(o['median']-1)/2:.1f}pp, is what one side pays")


if __name__ == "__main__":
    main()
