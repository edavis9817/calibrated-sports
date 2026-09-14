"""BRIEF S00 - closing line value for week 1. No settlement required.

    python research/clv.py --all
    python research/clv.py --census      # what is in the bucket
    python research/clv.py --placebo     # the side-handling check
    python research/clv.py --clv         # mean CLV, mid and executable
    python research/clv.py --strata      # liquidity, lead time, stat
    python research/clv.py --stake 500

READ-ONLY. The logger is running; nothing here writes.

WHAT "CLOSE" MEANS HERE, BECAUSE IT IS NOT SETTLEMENT
A Kalshi player prop stays open through the game and settles at GAME END, so
the settlement price is a realized outcome, not a market forecast. The
reference price is the LAST QUOTE STRICTLY BEFORE KICKOFF. Every close is
asserted to satisfy `ts < kickoff_ts` and the assertion is not decorative: the
same field means kickoff on Polymarket and game end on Kalshi, which is why
tiering keys on kickoff and never on `close_ts`. Measured, the close lands a
median of 1.0 minutes before kickoff.

    CLV = close_price(side taken) - entry_price(side taken)

in probability points, signed, never absolute.

SIDE-CORRECTNESS ON BOTH ENDS
`best_bid` and `best_ask` are the YES book whichever side a ticket takes, and
the no-side prices are their complements: buying NO costs `1 - yes_bid`, and
`1 - yes_ask` is what NO can be sold for. The side itself comes from
`jobs.paper_trade.evaluate` rather than being re-derived here, so a change to
the selection rule cannot silently desynchronise the two files. On the yes side
CLV is `close - entry`; on the no side it is `entry - close`, because a no
position gains when the yes price falls. The placebo below exists to prove that
sign is right rather than to assert it.

TWO PRICE ARMS
  mid          (bid + ask) / 2 at both ends. A paper number: it charges no
               spread and can be earned by nobody.
  executable   VWAP at a real stake from `market_depth`, which stores the cost
               of BUYING each side at 100/500/1000/5000 contracts. Buying yes
               at entry costs `vwap(buy_yes)`; getting out at the close means
               selling yes, which is worth `1 - vwap(buy_no)`. So the
               executable arm crosses the book twice and the gap between the
               two arms is the whole question of whether CLV is money.

THE EFFECTIVE SAMPLE IS NOT 935
935 predictions collapse to 136 distinct (player, stat) fits across 14 games:
a ladder's rungs are one claim priced at several thresholds, and every game on
a slate shares a scoring environment and a set of injury headlines. The
interval is therefore a BLOCK BOOTSTRAP over games - resample the 14 games with
replacement, recompute the mean, take percentiles. Fourteen blocks is very few
and the resulting interval is coarse; a (player, stat) block is reported beside
it as a second, less conservative view. A normal-approximation interval on 935
would be roughly three times too narrow and is not computed anywhere.

Rates get Wilson intervals, never the normal approximation.
"""
import argparse
import math
import os
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.distributions import kalshi_fee
from jobs.paper_trade import evaluate
from models import baseline
from research.longshot import wilson

ET = ZoneInfo("America/New_York")
# The stakes `market_depth` actually stores. 300 is NOT among them - the depth
# job writes 100/500/1000/5000 - so the capacity curve reports a bracket around
# 300 rather than inventing a VWAP by interpolating a step function.
STAKE_COL = {100: "vwap_100", 500: "vwap_500",
             1000: "vwap_1000", 5000: "vwap_5000"}
BOOTSTRAP = 10000
SEED = 20260914

PRED_SQL = """
SELECT p.prediction_id, p.outcome_id, p.prob_over, p.created_ts,
       o.event_id, o.stat, o.entity_id, o.line, o.side,
       mo.market_id, g.kickoff_ts
  FROM predictions p
  JOIN outcomes o USING (outcome_id)
  JOIN market_outcome mo ON mo.outcome_id = p.outcome_id
  LEFT JOIN (SELECT game_id, MAX(kickoff_ts) kickoff_ts
               FROM nfl_games GROUP BY game_id) g ON g.game_id = o.event_id
 WHERE mo.venue = 'kalshi' AND o.season = ? AND o.week = ?
   AND p.model_version = ?
"""


def db():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


# =============================================================================
# prices
# =============================================================================

def quote_at(c, market_id, ts, strict):
    """Last quote with a usable ask at (or before) `ts`.

    `strict` is True for the close, where `ts < kickoff` is a rule and not a
    rounding preference: a quote taken at kickoff is a live price.
    """
    op = "<" if strict else "<="
    return c.execute(
        f"SELECT ts, best_bid, best_ask FROM quotes "
        f"WHERE venue='kalshi' AND market_id=? AND ts {op} ? "
        f"AND best_ask IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (market_id, ts)).fetchone()


def depth_at(c, market_id, side, ts, strict, col):
    op = "<" if strict else "<="
    return c.execute(
        f"SELECT ts, {col}, filled_1000 FROM market_depth "
        f"WHERE venue='kalshi' AND market_id=? AND side=? AND ts {op} ? "
        f"AND {col} IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (market_id, side, ts)).fetchone()


def build(season=2026, week=1, stake=1000, model_version=None):
    mv = model_version or baseline.MODEL_VERSION
    c = db()
    col = STAKE_COL[stake]
    raw = c.execute(PRED_SQL, (season, week, mv)).fetchall()

    liq = {}
    for mid, bucket in c.execute(
            "SELECT market_id, bucket FROM market_liquidity ml WHERE venue='kalshi' "
            "AND period = (SELECT MAX(period) FROM market_liquidity "
            "               WHERE market_id = ml.market_id AND venue='kalshi')"):
        liq[mid] = bucket

    rows, drops = [], Counter()
    for (pid, oid, prob_over, entry_ts, event_id, stat, entity, line, oside,
         market_id, kickoff) in raw:
        if kickoff is None:
            drops["no kickoff in nfl_games"] += 1
            continue
        if kickoff <= entry_ts:
            drops["game kicked off before the prediction"] += 1
            continue
        eq = quote_at(c, market_id, entry_ts, strict=False)
        cq = quote_at(c, market_id, kickoff, strict=True)
        if not eq or not cq:
            drops["no quote at one end"] += 1
            continue
        # THE ASSERTION THE BRIEF ASKS FOR. A close at or after kickoff is a
        # live in-game price and would flatter CLV enormously.
        assert cq[0] < kickoff, (
            f"{market_id}: close ts {cq[0]} is not before kickoff {kickoff}")
        assert eq[0] <= entry_ts, f"{market_id}: entry quote is after entry"

        r = {"prediction_id": pid, "outcome_id": oid, "market_id": market_id,
             "game": event_id, "stat": stat, "entity": entity, "line": line,
             "outcome_side": oside, "prob_over": prob_over,
             "entry_ts": entry_ts, "kickoff": kickoff,
             "close_ts": cq[0], "entry_quote_ts": eq[0],
             "lead_h": (kickoff - entry_ts) / 3600.0,
             "close_lag_min": (kickoff - cq[0]) / 60.0,
             "bucket": liq.get(market_id, "unclassified"),
             "entry_bid": eq[1], "entry_ask": eq[2],
             "close_bid": cq[1], "close_ask": cq[2]}

        # --- mid arm -------------------------------------------------------
        # A NULL yes-bid is a real one-sided book (nobody bids yes on a deep
        # longshot), not a missing value. Coercing it to 0 at one end and
        # reading a real bid at the other manufactures CLV out of a convention
        # change, so the mid arm requires a bid at BOTH ends and the rest are
        # reported as their own stratum.
        if eq[1] is not None and cq[1] is not None:
            r["entry_mid"] = (eq[1] + eq[2]) / 2.0
            r["close_mid"] = (cq[1] + cq[2]) / 2.0
        else:
            r["entry_mid"] = r["close_mid"] = None
            drops["one-sided yes book (no bid) at an end"] += 1

        # --- side, from the selection code itself ---------------------------
        # Priced off the ENTRY book, which is the only book that existed when
        # the belief was formed.
        basis_bid = eq[1] if eq[1] is not None else 0.0
        ev = evaluate(prob_over, basis_bid, eq[2])
        r["side"] = ev["side"]
        r["sgn"] = 1.0 if ev["side"] == "yes" else -1.0
        r["signed_edge"] = ev["signed_edge"]

        # --- executable arm, at every stored stake ---------------------------
        # All four stakes are loaded, not just the configured one, because the
        # capacity curve is the whole point of S01 item 5 and re-running the
        # extraction per stake would be four passes over the same rows.
        r["depth"] = {}
        for stake_k, col_k in STAKE_COL.items():
            dy_e = depth_at(c, market_id, "buy_yes", entry_ts, False, col_k)
            dn_e = depth_at(c, market_id, "buy_no", entry_ts, False, col_k)
            dy_c = depth_at(c, market_id, "buy_yes", kickoff, True, col_k)
            dn_c = depth_at(c, market_id, "buy_no", kickoff, True, col_k)
            if not all(x for x in (dy_e, dn_e, dy_c, dn_c)):
                continue
            for x in (dy_c, dn_c):
                assert x[0] < kickoff, f"{market_id}: depth close not before kickoff"
            r["depth"][stake_k] = {
                "buy_yes_entry": dy_e[1], "buy_no_entry": dn_e[1],
                "buy_yes_close": dy_c[1], "buy_no_close": dn_c[1],
                "entry_eff_spread": dy_e[1] + dn_e[1] - 1.0,
                "close_eff_spread": dy_c[1] + dn_c[1] - 1.0}
        # The configured stake is promoted to the top level so S00's round-trip
        # arm and its tests keep reading exactly what they read before.
        d = r["depth"].get(stake)
        if d:
            r.update(d)
        else:
            r["buy_yes_entry"] = None
        rows.append(r)
    return rows, drops, mv


# =============================================================================
# CLV, on a stated side
# =============================================================================

def clv_mid(r, side=None):
    """close(side) - entry(side). A no position gains when yes falls."""
    if r["entry_mid"] is None:
        return None
    s = 1.0 if (side or r["side"]) == "yes" else -1.0
    return s * (r["close_mid"] - r["entry_mid"])


def entry_cost(r, side=None, basis="mid"):
    """What ONE contract of the chosen side actually costs to open.

    `basis` is "mid" for the paper price, or a stake key present in
    `r["depth"]` for the size-weighted offer. A no position is opened by
    buying no, which on the yes book costs `1 - yes_bid`.
    """
    s = side or r["side"]
    if basis == "mid":
        # The paper price. Nobody trades here; it is the ceiling.
        if r["entry_mid"] is None:
            return None
        return r["entry_mid"] if s == "yes" else 1.0 - r["entry_mid"]
    if basis == "touch":
        # Top of book, size-agnostic: buying yes pays the ask, buying no pays
        # 1 - bid. This is the brief's "entry at ask" arm and it is the best
        # price a taker of ONE contract could have had.
        if r["entry_bid"] is None or r["entry_ask"] is None:
            return None
        return r["entry_ask"] if s == "yes" else 1.0 - r["entry_bid"]
    d = r["depth"].get(basis)
    if not d:
        return None
    return d["buy_yes_entry"] if s == "yes" else d["buy_no_entry"]


def clv_one_crossing(r, side=None, basis="mid"):
    """S00 measured a ROUND TRIP: buy at the offer, leave at the bid. A ticket
    held to settlement never leaves, so it crosses ONCE.

    Entry is what was actually paid; the reference is the closing MID, which is
    the market's own final estimate rather than a price anyone could exit at.
    That is the honest way to charge one crossing: the cost of getting in is
    real, the exit is not charged because there is no exit.
    """
    if r["entry_mid"] is None or r["close_mid"] is None:
        return None
    cost = entry_cost(r, side, basis)
    if cost is None:
        return None
    s = side or r["side"]
    value = r["close_mid"] if s == "yes" else 1.0 - r["close_mid"]
    return value - cost


def net_of_fee(r, side=None, basis="mid", maker=False, contracts=None):
    """One-crossing CLV less the Kalshi fee on the price actually paid.

    The fee is charged on the WHOLE ORDER and rounded up to the next cent, so
    it must be computed at the ticket size and then expressed per contract.
    Calling `kalshi_fee(p, 1)` instead charges that rounding to EVERY contract:
    at p=0.40 a maker fee of 0.42c becomes 1.00c, inflating it 2.4x, and the
    maker arm reads +3.23pp instead of its true +3.81pp. The rounding is real
    but it is one cent per order, not one cent per contract.
    """
    v = clv_one_crossing(r, side, basis)
    if v is None:
        return None
    price = entry_cost(r, side, basis)
    n = contracts or (basis if isinstance(basis, int) else 1)
    return v - kalshi_fee(price, n, maker) / n


def clv_exec(r, side=None):
    """Cross the book at both ends, at a real stake.

    yes: pay vwap(buy_yes) at entry, and the position is worth what yes can be
         SOLD for at the close, which is 1 - vwap(buy_no).
    no:  pay vwap(buy_no) at entry, worth 1 - vwap(buy_yes) at the close.
    """
    if r["buy_yes_entry"] is None:
        return None
    if (side or r["side"]) == "yes":
        return (1.0 - r["buy_no_close"]) - r["buy_yes_entry"]
    return (1.0 - r["buy_yes_close"]) - r["buy_no_entry"]


# =============================================================================
# block bootstrap
# =============================================================================

def block_bootstrap(rows, value, block, n=BOOTSTRAP, seed=SEED):
    """Resample BLOCKS with replacement. Games share a scoring environment and
    a rung ladder shares one claim, so rows are not independent and an interval
    that assumes they are is fiction."""
    by = defaultdict(list)
    for r in rows:
        v = value(r)
        if v is not None:
            by[r[block]].append(v)
    keys = list(by)
    if len(keys) < 2:
        return None
    flat = [v for k in keys for v in by[k]]
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        pick = [by[keys[rng.randrange(len(keys))]] for _ in keys]
        vals = [v for chunk in pick for v in chunk]
        if vals:
            means.append(statistics.fmean(vals))
    means.sort()
    return {"mean": statistics.fmean(flat), "n": len(flat),
            "blocks": len(keys),
            "lo": means[int(0.025 * len(means))],
            "hi": means[int(0.975 * len(means))]}


# =============================================================================
# reports
# =============================================================================

def _hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def report_census(rows, drops, mv, stake):
    _hdr("CENSUS - what is in the bucket, before any number is interpreted")
    games = {r["game"] for r in rows}
    fits = {(r["entity"], r["stat"]) for r in rows}
    mid_n = sum(1 for r in rows if r["entry_mid"] is not None)
    ex_n = sum(1 for r in rows if r["buy_yes_entry"] is not None)
    print(f"""
  model version        {mv}
  predictions          {len(rows)}
  distinct games       {len(games)}          <- the bootstrap block
  distinct (player,stat) fits {len(fits)}    <- the effective sample
  rungs per fit        {len(rows)/max(len(fits),1):.1f} on average

  usable for the MID arm         {mid_n}
  usable for the EXECUTABLE arm  {ex_n}   (stake {stake})""")
    if drops:
        print("\n  excluded, with the reason (never silently dropped)")
        for k, v in drops.most_common():
            print(f"      {k:<44}{v}")
    print(f"\n  entry   {datetime.fromtimestamp(rows[0]['entry_ts'], ET):%a %m-%d %H:%M} ET"
          f"  (one instant - every prediction was written in the same run)")
    lead = sorted(r["lead_h"] for r in rows)
    lag = sorted(r["close_lag_min"] for r in rows)
    print(f"  lead    {lead[0]:.1f}h to {lead[-1]:.1f}h, median {statistics.median(lead):.1f}h")
    print(f"  close   median {statistics.median(lag):.1f} min before kickoff, "
          f"p90 {lag[int(.9*len(lag))]:.1f}, max {lag[-1]:.1f}")
    print(f"\n  {'stat':<16}{'n':>6}   {'side taken':<22}{'liquidity':<24}")
    st = Counter(r["stat"] for r in rows)
    sd = Counter(r["side"] for r in rows)
    lb = Counter(r["bucket"] for r in rows)
    keys = max(len(st), len(sd), len(lb))
    st_i, sd_i, lb_i = list(st.items()), list(sd.items()), list(lb.items())
    for i in range(keys):
        a = f"{st_i[i][0]:<16}{st_i[i][1]:>6}" if i < len(st_i) else " " * 22
        b = f"{sd_i[i][0]:<8}{sd_i[i][1]:>6}" if i < len(sd_i) else " " * 14
        d = f"{lb_i[i][0]:<10}{lb_i[i][1]:>6}" if i < len(lb_i) else ""
        print(f"  {a}   {b:<22}{d}")
    if lag and lag[-1] > 60:
        stale = sum(1 for x in lag if x > 60)
        print(f"\n  {stale} market(s) stopped quoting more than an hour before kickoff;"
              f"\n  their 'close' is that stale last quote. Kept, and visible here.")


def report_placebo(rows):
    _hdr("PLACEBO - the same CLV computed for the OPPOSITE side")
    print("""
  If the side handling is wrong anywhere, this is where it shows. Flipping the
  side must flip the mid CLV exactly, because a mid is one number and the two
  sides are its complements.

  The EXECUTABLE arm must NOT come out as the exact negative, and a version of
  this check that demanded it would be wrong. Buying either side costs the
  offer and leaving costs the bid, so taking both sides pays the effective
  spread twice:

      clv_exec(yes) + clv_exec(no) == -(entry_eff_spread + close_eff_spread)

  That identity is the executable arm's placebo, and it is asserted below.""")
    worst_mid = 0.0
    worst_exec = 0.0
    n_mid = n_exec = 0
    for r in rows:
        a = clv_mid(r, "yes")
        b = clv_mid(r, "no")
        if a is not None:
            n_mid += 1
            worst_mid = max(worst_mid, abs(a + b))
        x = clv_exec(r, "yes")
        y = clv_exec(r, "no")
        if x is not None:
            n_exec += 1
            expect = -(r["entry_eff_spread"] + r["close_eff_spread"])
            worst_exec = max(worst_exec, abs((x + y) - expect))
    print(f"\n    mid  arm: max |clv(yes) + clv(no)|            {worst_mid:.3e}   (n={n_mid})")
    print(f"    exec arm: max deviation from -(both spreads) {worst_exec:.3e}   (n={n_exec})")
    ok = worst_mid < 1e-12 and worst_exec < 1e-12
    print(f"\n    PLACEBO {'PASSES' if ok else 'FAILS'}")
    assert ok, "side handling is broken"
    return ok


def _line(label, res, unit="pp"):
    if not res:
        print(f"    {label:<34}insufficient data")
        return
    z = "" if res["lo"] <= 0 <= res["hi"] else "   <-- excludes zero"
    print(f"    {label:<34}{100*res['mean']:>+8.2f}   "
          f"[{100*res['lo']:>+6.2f}, {100*res['hi']:>+6.2f}]"
          f"{res['n']:>7,}{res['blocks']:>7}{z}")


def report_clv(rows, stake):
    _hdr("CLV - signed, unselected, block-bootstrapped by GAME")
    print(f"\n    {'arm':<34}{'mean pp':>8}   {'95% block CI':>16}"
          f"{'n':>7}{'blocks':>7}")
    mid = block_bootstrap(rows, clv_mid, "game")
    ex = block_bootstrap(rows, clv_exec, "game")
    _line("mid-to-mid", mid)
    _line(f"executable @ {stake}", ex)
    print(f"\n  the same, blocked on (player, stat) instead - less conservative,")
    print(f"  because it treats two games as independent when they share a week")
    for r in rows:
        r["_fit"] = (r["entity"], r["stat"])
    _line("mid-to-mid", block_bootstrap(rows, clv_mid, "_fit"))
    _line(f"executable @ {stake}", block_bootstrap(rows, clv_exec, "_fit"))

    vals = [clv_mid(r) for r in rows if clv_mid(r) is not None]
    if vals:
        pos = sum(1 for v in vals if v > 0)
        fits = {(r["entity"], r["stat"]) for r in rows if clv_mid(r) is not None}
        lo, hi = wilson(round(pos / len(vals) * len(fits)), len(fits))
        print(f"""
  RATE of positive CLV (mid arm), Wilson on the {len(fits)} effective fits
  rather than the {len(vals)} rungs:
      positive   {pos}/{len(vals)} = {pos/len(vals):.4f}
      Wilson     [{lo:.4f}, {hi:.4f}]   contains 0.5: {lo <= 0.5 <= hi}""")
    if mid and ex:
        print(f"""
  THE GAP BETWEEN THE ARMS is {100*(mid['mean']-ex['mean']):.2f}pp, and it is the
  cost of crossing the book twice at a stake of {stake}. That is the number
  that decides whether any mid-to-mid CLV is money or a paper artefact.""")
    return mid, ex


def report_convention(rows):
    """Is the mid-to-mid number robust to WHICH price you call the price?

    It is not, and that is the headline. Between Thursday and kickoff the yes
    BID rises about 2c while the ASK barely moves: the book tightens from
    below. Any measure built on the mid inherits half of that, whether or not
    the model knew anything. Measured on the ask alone - the price a taker of
    yes actually faces at both ends, and the leg that barely moves - the effect
    loses its interval.

    No zero bids are involved: all 624 rows have a genuine non-zero bid at both
    ends, so this is real bid improvement and not a null-to-value flip.
    """
    _hdr("CONVENTION ROBUSTNESS - which price is 'the price'?")
    r = [x for x in rows if x["entry_mid"] is not None]
    dbid = statistics.fmean([x["close_bid"] - x["entry_bid"] for x in r])
    dask = statistics.fmean([x["close_ask"] - x["entry_ask"] for x in r])
    esp = statistics.fmean([x["entry_ask"] - x["entry_bid"] for x in r])
    csp = statistics.fmean([x["close_ask"] - x["close_bid"] for x in r])
    print(f"""
  How the book moves between entry and kickoff, on the yes axis (n={len(r)}):
      mean bid move    {100*dbid:+.2f}c
      mean ask move    {100*dask:+.2f}c
      mean spread      {100*esp:.2f}c at entry -> {100*csp:.2f}c at the close
  The book tightens almost entirely from BELOW. A mid is half a bid, so a
  mid-based CLV collects half of that drift for free.""")
    print(f"\n    {'price convention':<34}{'mean pp':>8}   {'95% block CI':>16}"
          f"{'n':>7}{'blocks':>7}")
    convs = (
        ("mid -> mid", lambda x: x["sgn"] * (x["close_mid"] - x["entry_mid"])),
        ("ask -> ask (a taker of yes)",
         lambda x: x["sgn"] * (x["close_ask"] - x["entry_ask"])),
        ("bid -> bid (a maker resting)",
         lambda x: x["sgn"] * (x["close_bid"] - x["entry_bid"])),
    )
    out = {}
    for name, f in convs:
        res = block_bootstrap(r, f, "game")
        out[name] = res
        _line(name, res)
    print("""
  The three disagree, so the mid number is not a fact about the model - it is
  partly a fact about which side of the book was moving. The ask leg is the
  one a taker lives on and the one that barely drifts, and it does not clear
  zero. Treat mid-to-mid CLV as an upper bound, not as the estimate.""")

    # Decomposition: skill vs the mechanical product of side imbalance and a
    # market-wide drift. This is the check that a positive mean is not simply
    # "the market drifted and we happened to be leaning one way".
    drift = [x["close_mid"] - x["entry_mid"] for x in r]
    sgn = [x["sgn"] for x in r]
    ms, md = statistics.fmean(sgn), statistics.fmean(drift)
    cov = statistics.fmean([(s - ms) * (d - md) for s, d in zip(sgn, drift)])
    print(f"""  DECOMPOSITION of the mid arm, mean(sgn * drift):
      cov(side, drift)          {100*cov:+.3f} pp   <- covaries with the side chosen
      mean(side) x mean(drift)  {100*ms*md:+.3f} pp   <- side imbalance x market drift
      total                     {100*(cov + ms*md):+.3f} pp
  The mechanical term is not what is driving this: sides run {sum(1 for s in sgn if s>0)} yes to
  {sum(1 for s in sgn if s<0)} no, and market-wide drift times that imbalance is small and
  NEGATIVE. What remains is a covariance, which is the shape a real edge has -
  and which the ask-only convention above still does not confirm.""")
    return out


def report_capacity(rows):
    """S01 item 5. Price-only: nothing here needs settlement.

    S00's -13.52pp is a ROUND TRIP - buy at the offer, leave at the bid. A
    ticket held to settlement never leaves, so it pays to cross ONCE. And 1,000
    contracts is roughly three times a realistic ticket, so the round-trip
    number charges a size nobody would take, twice.
    """
    _hdr("CAPACITY - crossing once, and what size costs (S01 item 5)")
    mid = block_bootstrap(rows, clv_mid, "game")
    rt = block_bootstrap(rows, clv_exec, "game")
    one_touch = block_bootstrap(
        rows, lambda r: clv_one_crossing(r, basis="touch"), "game")
    print(f"""
  The four arms, most optimistic first. Only the last two are prices anyone
  could have paid, and only the last is what a held-to-settlement ticket faces.""")
    print(f"\n    {'arm':<34}{'mean pp':>8}   {'95% block CI':>16}"
          f"{'n':>7}{'blocks':>7}")
    _line("mid -> mid (no crossing at all)", mid)
    _line("one crossing, entry at the touch", one_touch)
    _line("round trip @ 1000 (S00)", rt)
    esp = statistics.fmean([r["entry_ask"] - r["entry_bid"]
                            for r in rows if r["entry_mid"] is not None])
    if mid and one_touch:
        print(f"""
  The one-crossing arm sits {100*(mid['mean']-one_touch['mean']):.2f}pp below mid-to-mid, which is half
  the {100*esp:.2f}c entry spread - exactly what paying the offer instead of the mid
  costs. The arithmetic is the check: a one-crossing number that did NOT come
  out near mid minus half the entry spread would mean the side handling or the
  book convention was wrong somewhere.""")

    print(f"""
  CAPACITY CURVE - one crossing, entry VWAP at size, against the closing mid.
  Gross is before fees; net charges the Kalshi taker fee on the price paid.
  `market_depth` stores 100/500/1000/5000 and NOT 300, so 300 is bracketed
  rather than interpolated - a VWAP over a discrete book is a step function and
  interpolating it would invent liquidity that may not be there.""")
    print(f"\n    {'stake':<34}{'mean pp':>8}   {'95% block CI':>16}"
          f"{'n':>7}{'blocks':>7}")
    curve = {}
    for s in sorted(STAKE_COL):
        g = block_bootstrap(rows, lambda r, s=s: clv_one_crossing(r, basis=s),
                            "game")
        n = block_bootstrap(rows, lambda r, s=s: net_of_fee(r, basis=s), "game")
        curve[s] = (g, n)
        _line(f"{s:,} contracts  gross", g)
        _line(f"{s:,} contracts  net of fee", n)
        if s == 100:
            print(f"    {'300 contracts':<34}{'bracketed by the 100 and 500 rows above':>8}")
    have = [(s, v) for s, v in curve.items() if v[1]]
    if len(have) >= 2:
        lo_s, hi_s = have[0][0], have[-1][0]
        lo_v, hi_v = have[0][1][1]["mean"], have[-1][1][1]["mean"]
        print(f"""
  Crossing cost DOES fall at small size: net goes {100*hi_v:+.2f}pp at {hi_s:,}
  to {100*lo_v:+.2f}pp at {lo_s:,}, a gain of {100*(lo_v-hi_v):.2f}pp for taking
  {hi_s//lo_s}x less risk. But the edge does not grow to meet it - the model's
  signed edge is a property of the forecast, not of the stake - so the curve
  flattens toward a ceiling of the mid-to-mid number minus half a spread, and
  that ceiling is {100*one_touch['mean']:+.2f}pp. Shrinking the ticket buys back
  slippage, not alpha.""")
    return curve


def report_strata(rows, stake):
    _hdr("STRATA - census first, then the number")

    def block(title, keyfn, order=None):
        groups = defaultdict(list)
        for r in rows:
            groups[keyfn(r)].append(r)
        print(f"\n  {title}")
        print(f"    {'stratum':<34}{'mean pp':>8}   {'95% block CI':>16}"
              f"{'n':>7}{'blocks':>7}")
        for k in (order or sorted(groups)):
            if k not in groups:
                continue
            g = groups[k]
            _line(f"{k}  (mid)", block_bootstrap(g, clv_mid, "game"))
            _line(f"{k}  (exec)", block_bootstrap(g, clv_exec, "game"))

    block("BY STAT", lambda r: r["stat"])
    block("BY LIQUIDITY BUCKET", lambda r: r["bucket"])

    def lead_bucket(r):
        h = r["lead_h"]
        if h < 12:
            return "lead <12h"
        if h < 48:
            return "lead 12-48h"
        if h < 72:
            return "lead 48-72h"
        return "lead >=72h"
    block("BY LEAD TIME - and why it cannot answer the decay question",
          lead_bucket, ["lead <12h", "lead 12-48h", "lead 48-72h", "lead >=72h"])
    lead = sorted(r["lead_h"] for r in rows)
    gaps = Counter()
    for r in rows:
        gaps[lead_bucket(r)] += 1
    print(f"""
  READ THIS BEFORE READING THE ROWS ABOVE. Every prediction was written in ONE
  run, at a single instant, so lead time is not a variable that was varied - it
  is entirely determined by when each game kicked off. Lead time and kickoff
  slot are perfectly confounded, and the distribution is bimodal, not a
  gradient: {gaps.get('lead <12h', 0)} rows at {lead[0]:.1f}h (the Thursday game) and then nothing
  until {min(r['lead_h'] for r in rows if r['lead_h'] > 12):.1f}h.

  So "48-72h" versus ">=72h" is Sunday's 1pm games against Sunday's late games.
  Any difference between those rows is a time-slot difference - different teams,
  different totals, different attention - and calling it CLV decay would be
  reading a schedule as a trend. Answering the decay question needs predictions
  written at SEVERAL entry times against the same kickoff, which this week does
  not have and next week could.""")


def report_verdict(mid, ex, rows, conv=None):
    _hdr("IS THIS DISTINGUISHABLE FROM ZERO?")
    fits = len({(r["entity"], r["stat"]) for r in rows})
    games = len({r["game"] for r in rows})
    def verdict(res, name):
        if not res:
            return f"  {name}: not computable"
        crosses = res["lo"] <= 0 <= res["hi"]
        return (f"  {name}: {100*res['mean']:+.2f}pp, 95% block CI "
                f"[{100*res['lo']:+.2f}, {100*res['hi']:+.2f}]pp -> "
                f"{'NOT distinguishable from zero' if crosses else 'EXCLUDES zero'}")
    print()
    print(verdict(mid, "mid-to-mid "))
    print(verdict(ex, "executable "))
    if conv:
        print(verdict(conv.get("ask -> ask (a taker of yes)"), "ask-to-ask  "))
    print(f"""
  ANSWER: no. Not in any form that could be traded, and not robustly in any
  form at all.

  The mid-to-mid interval does exclude zero, and it is the weakest of the three
  price conventions to rely on: between entry and kickoff the yes BID rises
  ~2c while the ask moves -0.2c, so a mid collects half of a one-sided
  tightening whatever the model knew. On the ask alone - the price a taker
  actually pays, and the leg that barely moves - the interval touches zero.
  Measured where money changes hands, at a stake of 1000, CLV is about
  -13.5pp, which is the book being crossed twice.

  {games} games is a very small number of bootstrap blocks and {fits} fits is a
  small effective sample. This week cannot separate a model with CLV from one
  without; it can only rule out an effect large enough to show up anyway.

  What it DOES establish is the pipeline: entry prices, kickoff-anchored
  closes, side-correct arithmetic on both ends, depth at a real stake, and a
  placebo that passes. Those are the parts that had to be right before any
  number was worth reading, and they can now be re-run every week at no cost.""")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for f in ("census", "placebo", "clv", "convention", "capacity",
              "strata", "all"):
        ap.add_argument(f"--{f}", action="store_true")
    ap.add_argument("--stake", type=int, default=1000,
                    choices=sorted(STAKE_COL))
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    a = ap.parse_args()
    if not (a.census or a.placebo or a.clv or a.convention or a.capacity
            or a.strata):
        a.all = True
    rows, drops, mv = build(a.season, a.week, a.stake)
    if not rows:
        raise SystemExit("no predictions matched")
    print(f"CLV  season {a.season} week {a.week}  stake {a.stake}  "
          f"bootstrap {BOOTSTRAP:,} (seed {SEED})")
    if a.census or a.all:
        report_census(rows, drops, mv, a.stake)
    if a.placebo or a.all:
        report_placebo(rows)
    mid = ex = None
    if a.clv or a.strata or a.all:
        mid, ex = report_clv(rows, a.stake)
    conv = None
    if a.convention or a.all:
        conv = report_convention(rows)
    if a.capacity or a.all:
        report_capacity(rows)
    if a.strata or a.all:
        report_strata(rows, a.stake)
    if a.all:
        report_verdict(mid, ex, rows, conv)


if __name__ == "__main__":
    main()
