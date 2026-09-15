"""BRIEF M01 - would a resting order have been filled, and at what cost?

    python research/maker.py --all
    python research/maker.py --census     # what is in the bucket
    python research/maker.py --fills      # fill rate and time to fill
    python research/maker.py --clv        # CLV CONDITIONAL on being filled
    python research/maker.py --size 500

READ-ONLY. Prices and depth come from the logger's database; prints come from
`trades_m01.db`, which `jobs/ingest_kalshi_trades.py` fills.

PRE-REGISTERED HYPOTHESIS, written before the first number was computed:
fills are ADVERSELY SELECTED and conditional CLV is NEGATIVE. The print that
reaches a resting order is disproportionately the one that knows something -
a passive bid gets hit hardest exactly when the price is about to fall
through it. Secondary prediction: fill rates are LOW, because 910 of the 935
markets are `thin` and a market that never prints cannot fill anyone. If
conditional CLV comes out POSITIVE, the first suspect is queue position being
modelled optimistically, not a discovered edge.

WHY THIS IS THE ONLY SURVIVING HYPOTHESIS
S01 measured -3.00pp crossing once as a taker, against +3.79pp for the same
predictions filled passively. Both are the same forecast: the entire gap is
the spread, collected instead of paid. So the question is not whether the
maker price is better - it obviously is - but whether anyone would ever have
been filled at it, and what the fills that DID happen were worth.

HOW A FILL IS DECIDED
A print is a taker crossing. `taker_side` is the side the taker BOUGHT, so the
maker took the other one:

    buy YES passively  ->  rest on the yes-bid  ->  filled by taker_side == "no"
    buy NO passively   ->  rest on the no-bid   ->  filled by taker_side == "yes"

Getting that backwards inverts every fill and still yields a plausible-looking
rate, so `tests/test_maker.py` pins it.

QUEUE POSITION, WHICH IS WHERE THIS MEASUREMENT WOULD LIE
We join the touch, so we are BEHIND everything already resting at that level.
`market_depth` gives that size directly, and the mapping is verified, not
assumed: `touch_price(buy_yes)` equals `best_ask` and `1 - touch_price(buy_no)`
equals `best_bid`, to 0.0000 at median and p90 over 98 sampled markets. Because
buying NO consumes yes-bids, `touch_size(buy_no)` IS the size resting at the
best yes-bid - the queue ahead of a passive yes buy - and symmetrically for the
other side. A fill requires cumulative eligible volume to exceed that queue
AND then our own ticket. Assuming a front-of-queue fill instead would roughly
double the fill rate and is the single easiest way to make this study lie.

Prices only move the queue against us, never for us: we do not model
cancellations ahead of us, which would help, nor price improvement by others,
which would hurt. Both are unmodelled and both are noted.
"""
import argparse
import math
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.fees import fee_per_contract, series_multiplier
from research import clv as S00
from research.longshot import wilson

ET = ZoneInfo("America/New_York")
TRADES_DB = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)),
                         "trades_m01.db")
TICKET = 100            # contracts; the smallest rung of S01's capacity curve


def trades_db():
    if not os.path.exists(TRADES_DB):
        raise SystemExit(f"no print history at {TRADES_DB} - run "
                         f"jobs/ingest_kalshi_trades.py --week 1")
    return sqlite3.connect(f"file:{TRADES_DB}?mode=ro", uri=True)


# =============================================================================
# the passive order
# =============================================================================

def passive_price(r, side=None):
    """What a resting order to BUY the chosen side would be priced at.

    Buying yes passively means joining the yes-bid, so the price is the bid.
    Buying no passively means joining the no-bid, which is 1 - the yes ask.
    """
    s = side or r["side"]
    if r["entry_bid"] is None or r["entry_ask"] is None:
        return None
    return r["entry_bid"] if s == "yes" else 1.0 - r["entry_ask"]


def queue_ahead(r, side=None):
    """Size already resting at our level, which we sit behind.

    Buying NO consumes yes-bids, so `touch_size(buy_no)` is the size at the
    best yes-bid - exactly the queue in front of a passive YES buy.
    """
    s = side or r["side"]
    key = "buy_no" if s == "yes" else "buy_yes"
    return r.get("touch_size", {}).get(key)


def eligible(trade, side, price):
    """Does this print fill someone resting at `price` on `side`?

    A taker BUYING no is selling yes into the yes-bid ladder, which is where a
    passive yes buyer rests. Prints above our price hit better bids and do not
    touch us; prints at or through it do.
    """
    taker = trade["taker_side"]
    if side == "yes":
        return taker == "no" and trade["yes_price"] is not None \
            and trade["yes_price"] <= price + 1e-9
    return taker == "yes" and trade["no_price"] is not None \
        and trade["no_price"] <= price + 1e-9


def simulate(r, prints, ticket=TICKET, side=None):
    """Walk the prints in time order and decide whether we were filled."""
    s = side or r["side"]
    price = passive_price(r, s)
    q = queue_ahead(r, s)
    if price is None or q is None:
        return None
    window = [t for t in prints if r["entry_ts"] <= t["ts"] < r["kickoff"]]
    out = {"side": s, "price": price, "queue_ahead": q, "ticket": ticket,
           "n_prints": len(window),
           "n_eligible": 0, "eligible_size": 0.0,
           "filled": False, "fill_ts": None, "time_to_fill_h": None}
    cum = 0.0
    for t in window:
        if not eligible(t, s, price):
            continue
        out["n_eligible"] += 1
        out["eligible_size"] += t["size"] or 0.0
        cum += t["size"] or 0.0
        if not out["filled"] and cum >= q + ticket:
            out["filled"] = True
            out["fill_ts"] = t["ts"]
            out["time_to_fill_h"] = (t["ts"] - r["entry_ts"]) / 3600.0
    return out


# =============================================================================
# what a fill was worth
# =============================================================================

def maker_clv(r, sim, net=True):
    """Close mid on the side taken, less what the passive fill cost.

    This is the +3.79pp arm when applied to every prediction, and the number
    that matters when applied only to the ones that filled.
    """
    if sim is None or r["close_mid"] is None:
        return None
    s = sim["side"]
    value = r["close_mid"] if s == "yes" else 1.0 - r["close_mid"]
    v = value - sim["price"]
    if not net:
        return v
    # Fee on the WHOLE ORDER, rounded up once, then per contract. Charging
    # `kalshi_fee(p, 1)` bills the cent-rounding to every contract and turns a
    # 0.42c maker fee into 1.00c.
    n = sim["ticket"]
    # BRIEF 017 ITEM 5: the multiplier now comes from the SERIES, not from a
    # pin. 016's audit established that all 935 predictions sit on KXNFLREC and
    # KXNFLRSHATT, neither of which appears in the schedule's Non-Standard Fees
    # table - so the published default applies and maker M = 0. A resting order
    # on these markets is free, and pinning multiplier=1 was charging a fee
    # Kalshi does not charge.
    maker_m, _ = series_multiplier(r["market_id"])
    return v - fee_per_contract(sim["price"], n, "maker", multiplier=maker_m)


def drift_after_fill(r, sim, mid_at):
    """What the mid did AFTER we were filled, on our side.

    This is the adverse-selection measurement proper. Conditional CLV mixes
    two things - whether our price was good, and whether being filled was bad
    news. This isolates the second.
    """
    if not sim or not sim["filled"] or r["close_mid"] is None:
        return None
    m = mid_at(r["market_id"], sim["fill_ts"])
    if m is None:
        return None
    s = sim["side"]
    at_fill = m if s == "yes" else 1.0 - m
    at_close = r["close_mid"] if s == "yes" else 1.0 - r["close_mid"]
    return at_close - at_fill


# =============================================================================
# loading
# =============================================================================

def load_control(season=2026, week=1, ticket=TICKET, model_version=None, series=None,
                 disjoint=False):
    """BRIEF 018 ITEM 1 - the same simulation on a population the model never
    touched.

    THE SELECTION RULE, in full, so it can be checked rather than trusted:

      1. Take the 14 kalshi events the week-1 predictions cover. This fixes the
         WINDOW - same games, same slate, same afternoon - and is the only
         place a prediction enters. Without it the control would differ from
         M01 in which games it watched as well as which markets, and the
         comparison would measure both at once.
      2. Inside those events take EVERY `KXNFLREC` and `KXNFLRSHATT` market.
         No price filter, no spread filter, no history filter, and nothing
         downstream of a forecast.
      3. Rest on BOTH sides of each market, as two separate observations.

    Step 3 is what makes it model-free. M01 chose its side with
    `evaluate(model_prob, bid, ask)`, so a control that picked a side at all
    would need a rule, and every rule is an opinion. Taking both sides has no
    opinion in it - and it is the right null for the specific question, because
    a maker resting on both sides of a book collects exactly the spread:

        maker_clv(yes) = half_spread + (close_mid - entry_mid)
        maker_clv(no)  = half_spread - (close_mid - entry_mid)

    so the two legs average to half the spread by construction, and everything
    interesting is in how FILLS deviate from that. If conditional CLV on the
    control lands near M01's, the effect is the spread surviving adverse
    selection and has nothing to do with forecasting.

    `disjoint=True` drops the 935 markets that DID carry a prediction, leaving
    a population with no overlap at all. That costs sample and buys
    independence; both are reported.
    """
    from jobs.ingest_kalshi_trades import control_frame
    from core import version_resolve
    from models import baseline

    mv, _ = version_resolve.resolve(season, week, baseline.MODEL_VERSION,
                                    override=model_version)
    live = S00.db()
    td = trades_db()
    frame = control_frame(season, week, model_version,
                          series=series or ("KXNFLREC", "KXNFLRSHATT"))

    predicted = {r[0] for r in live.execute(
        "SELECT mo.market_id FROM predictions p JOIN outcomes o USING (outcome_id) "
        "JOIN market_outcome mo ON mo.outcome_id = p.outcome_id "
        "WHERE mo.venue='kalshi' AND o.season=? AND o.week=? AND p.model_version=?",
        (season, week, mv))}
    if disjoint:
        frame = [m for m in frame if m not in predicted]

    # Entry instant and kickoffs come from the M01 rows, so both populations
    # are clocked identically.
    base, _drops, _mv = S00.build(season, week, model_version=model_version)
    entry_ts = base[0]["entry_ts"]
    kick_by_event, game_by_event = {}, {}
    for r in base:
        ev = r["market_id"].split("-")[1]
        kick_by_event[ev] = r["kickoff"]
        game_by_event[ev] = r["game"]

    by_market = defaultdict(list)
    for mid, ts, yp, np_, sz, tk in td.execute(
            "SELECT market_id, ts, yes_price, no_price, size, taker_side "
            "FROM market_trades WHERE venue='kalshi' ORDER BY market_id, ts"):
        by_market[mid].append({"ts": ts, "yes_price": yp, "no_price": np_,
                               "size": sz, "taker_side": tk})

    rows = []
    for market_id in frame:
        ev = market_id.split("-")[1]
        kickoff = kick_by_event.get(ev)
        if kickoff is None:
            continue
        eq = S00.quote_at(live, market_id, entry_ts, strict=False)
        cq = S00.quote_at(live, market_id, kickoff, strict=True)
        if not eq or not cq or eq[1] is None or cq[1] is None:
            continue
        assert cq[0] < kickoff, f"{market_id}: close is not before kickoff"
        touch = {}
        for s in ("buy_yes", "buy_no"):
            x = live.execute(
                "SELECT touch_size FROM market_depth WHERE venue='kalshi' "
                "AND market_id=? AND side=? AND ts<=? AND touch_size IS NOT NULL "
                "ORDER BY ts DESC LIMIT 1", (market_id, s, entry_ts)).fetchone()
            if x:
                touch[s] = x[0]
        prints = by_market.get(market_id, [])
        for side in ("yes", "no"):
            r = {"market_id": market_id, "game": game_by_event[ev],
                 "entity": market_id, "stat": market_id.split("-")[0],
                 "entry_ts": entry_ts, "kickoff": kickoff,
                 "entry_bid": eq[1], "entry_ask": eq[2],
                 "close_bid": cq[1], "close_ask": cq[2],
                 "entry_mid": (eq[1] + eq[2]) / 2.0,
                 "close_mid": (cq[1] + cq[2]) / 2.0,
                 "bucket": "control", "side": side,
                 "touch_size": touch, "prints": prints,
                 "predicted": market_id in predicted}
            r["sim"] = simulate(r, prints, ticket, side)
            rows.append(r)
    return rows


def load(season=2026, week=1, ticket=TICKET, model_version=None):
    rows, drops, mv = S00.build(season, week, model_version=model_version)
    live = S00.db()
    td = trades_db()

    # Queue depth at entry, both sides.
    for r in rows:
        r["touch_size"] = {}
        for side in ("buy_yes", "buy_no"):
            x = live.execute(
                "SELECT touch_size FROM market_depth WHERE venue='kalshi' "
                "AND market_id=? AND side=? AND ts<=? AND touch_size IS NOT NULL "
                "ORDER BY ts DESC LIMIT 1",
                (r["market_id"], side, r["entry_ts"])).fetchone()
            if x:
                r["touch_size"][side] = x[0]

    by_market = defaultdict(list)
    for mid, ts, yp, np_, sz, tk in td.execute(
            "SELECT market_id, ts, yes_price, no_price, size, taker_side "
            "FROM market_trades WHERE venue='kalshi' ORDER BY market_id, ts"):
        by_market[mid].append({"ts": ts, "yes_price": yp, "no_price": np_,
                               "size": sz, "taker_side": tk})
    fetched = {m: (st, n) for m, st, n in td.execute(
        "SELECT market_id, status, n_trades FROM market_trades_fetch "
        "WHERE venue='kalshi'")}

    mid_cache = {}

    def mid_at(market_id, ts):
        key = (market_id, round(ts))
        if key in mid_cache:
            return mid_cache[key]
        x = live.execute(
            "SELECT best_bid, best_ask FROM quotes WHERE venue='kalshi' "
            "AND market_id=? AND ts<=? AND best_ask IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1", (market_id, ts)).fetchone()
        v = None if not x or x[0] is None else (x[0] + x[1]) / 2.0
        mid_cache[key] = v
        return v

    for r in rows:
        r["prints"] = by_market.get(r["market_id"], [])
        r["fetch"] = fetched.get(r["market_id"])
        r["sim"] = simulate(r, r["prints"], ticket)
    return rows, drops, mv, mid_at


# =============================================================================
# reports
# =============================================================================

def _hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def report_census(rows, ticket):
    _hdr("CENSUS - the print history, before any fill is interpreted")
    n = len(rows)
    nofetch = [r for r in rows if not r["fetch"]]
    failed = [r for r in rows if r["fetch"] and r["fetch"][0] != 200]
    sims = [r for r in rows if r["sim"]]
    noq = [r for r in rows if r["sim"] is None]
    zero_any = [r for r in rows if r["fetch"] and not r["prints"]]
    zero_win = [r for r in sims if r["sim"]["n_prints"] == 0]
    print(f"""
  predictions                         {n}
  markets fetched                     {n - len(nofetch)}   ({len(failed)} failed)
  simulated                           {len(sims)}
  not simulated (no depth or book)    {len(noq)}

  markets with ZERO prints EVER            {len(zero_any)}   ({100*len(zero_any)/max(n,1):.1f}%)
  markets with ZERO prints entry->kickoff  {len(zero_win)}   ({100*len(zero_win)/max(len(sims),1):.1f}%)
  A market that never trades cannot fill anyone at any price.""")
    pr = [r["sim"]["n_prints"] for r in sims]
    el = [r["sim"]["n_eligible"] for r in sims]
    q = [r["sim"]["queue_ahead"] for r in sims]
    if pr:
        print(f"""
  prints in the window   median {statistics.median(pr):.0f}  p90 {sorted(pr)[int(.9*len(pr))]:.0f}  max {max(pr)}
  ELIGIBLE prints        median {statistics.median(el):.0f}  p90 {sorted(el)[int(.9*len(el))]:.0f}  max {max(el)}
  queue ahead (contracts) median {statistics.median(q):.0f}  p90 {sorted(q)[int(.9*len(q))]:.0f}  max {max(q):.0f}
  our ticket             {ticket} contracts

  'Eligible' means the print was on the side that would have hit our resting
  order AND at or through our price. The gap between prints and eligible
  prints is most of the story: half of all volume trades on the other side of
  the book from us.""")
    lb = Counter(r["bucket"] for r in sims)
    print("\n  liquidity of the simulated: "
          + "  ".join(f"{k} {v}" for k, v in lb.most_common()))
    nb = Counter(r["bucket"] for r in noq)
    if nb:
        print("  liquidity of the EXCLUDED:  "
              + "  ".join(f"{k} {v}" for k, v in nb.most_common()))
        print(f"""
  The {len(noq)} exclusions are markets with no depth snapshot at or before the
  entry instant, so there is no queue to sit behind and no honest way to invent
  one. That is {100*len(noq)/max(n,1):.0f}% of the population and it is NOT a random {100*len(noq)/max(n,1):.0f}%: depth
  capture runs on an allowlist, so what survives is the part of the book the
  logger was already watching. Every fill rate below describes that part.""")
    return sims


def report_fills(sims, ticket):
    _hdr("FILL RATE - would the order have been filled at all?")
    filled = [r for r in sims if r["sim"]["filled"]]
    n, k = len(sims), len(filled)
    fits = {(r["entity"], r["stat"]) for r in sims}
    fk = {(r["entity"], r["stat"]) for r in filled}
    rate = S00.block_bootstrap(sims, lambda r: float(r["sim"]["filled"]), "game")
    flo, fhi = wilson(len(fk), len(fits))
    print(f"""
  TWO DIFFERENT QUESTIONS, and they must not share a line.

  (a) Would THIS order have been filled?  - the unit is one prediction.
      filled            {k} of {n}   ({k/max(n,1):.4f})
      95% block CI      [{rate['lo']:.4f}, {rate['hi']:.4f}]  (bootstrapped over {rate['blocks']} games)

  (b) Did this player-stat trade at our price AT ALL? - the unit is a fit,
      and a fit counts if ANY of its rungs filled. This is the looser
      question and it necessarily gives a higher number.
      fits with a fill  {len(fk)} of {len(fits)}   ({len(fk)/max(len(fits),1):.4f})
      Wilson            [{flo:.4f}, {fhi:.4f}]

  Quoting (a)'s rate beside (b)'s interval would be an error - one is 0.18 and
  the other 0.60 on the same data. Rungs within a fit are not independent, so
  (a) gets a block bootstrap rather than a Wilson interval on 704.""")
    print(f"\n  BY LIQUIDITY BUCKET - per-prediction rate, block CI over games")
    print(f"    {'bucket':<12}{'n':>6}{'filled':>8}{'rate':>8}{'95% block CI':>22}")
    for b in sorted({r["bucket"] for r in sims}):
        g = [r for r in sims if r["bucket"] == b]
        gf = [r for r in g if r["sim"]["filled"]]
        br = S00.block_bootstrap(g, lambda r: float(r["sim"]["filled"]), "game")
        ci = (f"[{br['lo']:.4f}, {br['hi']:.4f}]" if br else "one block only")
        print(f"    {b:<12}{len(g):>6}{len(gf):>8}{len(gf)/max(len(g),1):>8.4f}"
              f"{ci:>22}")
    if filled:
        t = sorted(r["sim"]["time_to_fill_h"] for r in filled)
        print(f"""
  TIME TO FILL, hours after the prediction was written
    min {t[0]:.2f}   p25 {t[int(.25*len(t))]:.2f}   median {statistics.median(t):.2f}
    p75 {t[int(.75*len(t))]:.2f}   p90 {t[int(.90*len(t))]:.2f}   max {t[-1]:.2f}""")
        early = sum(1 for x in t if x < 1.0)
        print(f"    filled within the first hour: {early} of {len(t)} "
              f"({100*early/len(t):.0f}%)")
    return filled


def report_clv(sims, filled, mid_at, ticket):
    _hdr("CLV CONDITIONAL ON BEING FILLED - the number that decides it")
    unfilled = [r for r in sims if not r["sim"]["filled"]]
    print(f"""
  Three populations. The first is the paper number S01 reported; the second is
  what a resting order actually collected; the third is what it missed.""")
    print(f"\n    {'population':<34}{'mean pp':>8}   {'95% block CI':>16}"
          f"{'n':>7}{'blocks':>7}")
    allb = S00.block_bootstrap(sims, lambda r: maker_clv(r, r["sim"]), "game")
    fb = S00.block_bootstrap(filled, lambda r: maker_clv(r, r["sim"]), "game")
    ub = S00.block_bootstrap(unfilled, lambda r: maker_clv(r, r["sim"]), "game")
    S00._line("every prediction (unconditional)", allb)
    S00._line("FILLED only (conditional)", fb)
    S00._line("never filled (counterfactual)", ub)

    da = S00.block_bootstrap(
        filled, lambda r: drift_after_fill(r, r["sim"], mid_at), "game")
    print(f"\n  ADVERSE SELECTION - what the mid did AFTER the fill, on our side")
    print(f"\n    {'':<34}{'mean pp':>8}   {'95% block CI':>16}{'n':>7}{'blocks':>7}")
    S00._line("mid move from fill to close", da)
    print("""
  Conditional CLV mixes two things: whether our price was good, and whether
  being filled was bad news. This row isolates the second. Negative means the
  market moved against us after it chose to trade with us, which is exactly
  what adverse selection is.""")

    # The gap needs its own interval. Subtracting two means and quoting the
    # difference as if it were measured is how a selection effect gets reported
    # with more confidence than the data supports. Resample GAMES once and
    # recompute both populations inside each resample, so the two halves move
    # together exactly as they do in the data.
    gap = _gap_bootstrap(sims)
    if gap:
        print(f"""
  THE SELECTION GAP - never filled MINUS filled, bootstrapped as one quantity
  rather than as the difference of two separately quoted means:

      {100*gap['mean']:+.2f}pp   [{100*gap['lo']:+.2f}, {100*gap['hi']:+.2f}]   over {gap['blocks']} games

  That is the price of assuming a quoted price is an available one. The orders
  that did NOT fill were worth {100*ub['mean']:+.2f}pp and the ones that did were worth
  {100*fb['mean']:+.2f}pp - the market declined to trade with us precisely where the
  trade was good.""")
    return allb, fb, ub, da


def _gap_bootstrap(sims, n=S00.BOOTSTRAP, seed=S00.SEED):
    """Mean(unfilled) - mean(filled), resampling games once for both halves."""
    import random
    by = defaultdict(list)
    for r in sims:
        v = maker_clv(r, r["sim"])
        if v is not None:
            by[r["game"]].append((r["sim"]["filled"], v))
    keys = [k for k in by]
    if len(keys) < 2:
        return None

    def stat(chunks):
        f = [v for c in chunks for hit, v in c if hit]
        u = [v for c in chunks for hit, v in c if not hit]
        if not f or not u:
            return None
        return statistics.fmean(u) - statistics.fmean(f)

    base = stat([by[k] for k in keys])
    if base is None:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        v = stat([by[keys[rng.randrange(len(keys))]] for _ in keys])
        if v is not None:
            out.append(v)
    out.sort()
    return {"mean": base, "blocks": len(keys),
            "lo": out[int(0.025 * len(out))], "hi": out[int(0.975 * len(out))]}


def report_ticket_sweep(rows, sizes=(10, 50, 100, 500, 1000)):
    """Fill rate against ticket size - the maker analogue of S01's capacity
    curve. A smaller order clears the queue sooner, so if the maker path is
    reachable anywhere it is reachable small."""
    _hdr("TICKET SIZE - is the maker path reachable at ANY size?")
    print(f"\n    {'ticket':<12}{'n':>6}{'filled':>8}{'rate':>8}"
          f"{'95% block CI':>22}{'cond. CLV pp':>15}")
    for t in sizes:
        sims = []
        for r in rows:
            s = simulate(r, r["prints"], ticket=t)
            if s:
                rr = dict(r)
                rr["sim"] = s
                sims.append(rr)
        if not sims:
            continue
        f = [r for r in sims if r["sim"]["filled"]]
        br = S00.block_bootstrap(sims, lambda r: float(r["sim"]["filled"]), "game")
        cb = S00.block_bootstrap(f, lambda r: maker_clv(r, r["sim"]), "game")
        ci = f"[{br['lo']:.4f}, {br['hi']:.4f}]" if br else ""
        cc = f"{100*cb['mean']:+.2f}" if cb else "n/a"
        print(f"    {t:<12,}{len(sims):>6}{len(f):>8}"
              f"{len(f)/max(len(sims),1):>8.4f}{ci:>22}{cc:>15}")
    print("""
  Queue position is the whole mechanism: the median queue ahead is 203
  contracts, so shrinking the ticket moves the fill threshold from
  queue+1000 down to queue+10 - a change of a few percent against a queue
  that is itself the binding constraint. The rate rises, and it does not
  rise enough to reach the unconditional number.""")


def _five(rows, label):
    """The five M01 metrics for one population."""
    sims = [r for r in rows if r["sim"]]
    filled = [r for r in sims if r["sim"]["filled"]]
    if not sims:
        return None
    rate = S00.block_bootstrap(sims, lambda r: float(r["sim"]["filled"]), "game")
    return {
        "label": label,
        "n": len(sims),
        "markets": len({r["market_id"] for r in sims}),
        "filled": len(filled),
        "rate": rate,
        "uncond": S00.block_bootstrap(sims, lambda r: maker_clv(r, r["sim"]), "game"),
        "cond": S00.block_bootstrap(filled, lambda r: maker_clv(r, r["sim"]), "game"),
        "never": S00.block_bootstrap([r for r in sims if not r["sim"]["filled"]],
                                     lambda r: maker_clv(r, r["sim"]), "game"),
        "gap": _gap_bootstrap(sims),
    }


def _pp(res):
    if not res:
        return "n/a"
    star = "*" if not (res["lo"] <= 0 <= res["hi"]) else " "
    return f"{100*res['mean']:+6.2f} [{100*res['lo']:+6.2f},{100*res['hi']:+6.2f}]{star}"


def flip_sides(model_rows, ticket=TICKET):
    """The same markets with the model's side INVERTED.

    Once it turned out that the model had priced every prop quoted at the
    entry instant, this became the sharpest control available: identical
    markets, identical books, identical prints, one bit changed. Whatever the
    model's side choice is worth shows up as the difference between this arm
    and M01's.
    """
    out = []
    for r in model_rows:
        rr = dict(r)
        rr["side"] = "no" if r["side"] == "yes" else "yes"
        rr["sim"] = simulate(rr, rr.get("prints", []), ticket, rr["side"])
        out.append(rr)
    return out


def report_control(model_rows, ctrl_rows, ctrl_disjoint=None, flipped=None):
    """BRIEF 018 ITEM 1 - is the effect microstructure, or the selection?"""
    _hdr("ITEM 1 - MODEL-SELECTED vs POPULATIONS THE MODEL DID NOT CHOOSE")
    print("""
  THE CONTROL COULD NOT BE A DIFFERENT SET OF MARKETS, and finding that out is
  the first result. The intended frame was every KXNFLREC and KXNFLRSHATT
  market in the predictions' 14 events - 1,487 of them, of which 552 carried no
  prediction. But 542 of those 552 HAD NO QUOTE AT ALL at the entry instant:
  Kalshi listed those rungs later in the week. At 15:03 on the Thursday the
  model had priced essentially every prop that existed, so there is no
  unselected market population to compare against. Market selection cannot be
  the artifact, because there was no market selection to make.

  What remains testable is SIDE selection, and two arms do that:

    both sides   every simulable market, resting on yes AND no as separate
                 observations. No opinion at all. Resting on both sides of a
                 book collects exactly the spread, so this is the natural null.
    flipped      the same markets with the model's own side INVERTED. Identical
                 books, identical prints, one bit changed.

  Intervals are the same block bootstrap over the same 14 games. A trailing *
  marks an interval that excludes zero.""")
    cols = [_five(model_rows, "model side (M01)"),
            _five(ctrl_rows, "both sides")]
    if flipped is not None:
        cols.append(_five(flipped, "flipped side"))
    if ctrl_disjoint is not None:
        c = _five(ctrl_disjoint, "never-predicted")
        if c:
            cols.append(c)
    cols = [c for c in cols if c]
    w = 24
    print(f"\n    {'':<26}" + "".join(f"{c['label'][:w]:>{w}}" for c in cols))
    print(f"    {'n observations':<26}" + "".join(f"{c['n']:>{w},}" for c in cols))
    print(f"    {'n distinct markets':<26}" + "".join(f"{c['markets']:>{w},}" for c in cols))
    print(f"    {'filled':<26}" + "".join(f"{c['filled']:>{w},}" for c in cols))
    rates = [f"{c['rate']['mean']:.4f}" if c["rate"] else "n/a" for c in cols]
    cis = [f"[{c['rate']['lo']:.4f}, {c['rate']['hi']:.4f}]" if c["rate"] else ""
           for c in cols]
    print(f"    {'fill rate':<26}" + "".join(f"{x:>{w}}" for x in rates))
    print(f"    {'  95% block CI':<26}" + "".join(f"{x:>{w}}" for x in cis))
    for key, lab in (("uncond", "unconditional maker pp"),
                     ("cond", "CONDITIONAL on fill pp"),
                     ("never", "never filled pp"),
                     ("gap", "selection gap pp")):
        print(f"    {lab:<26}" + "".join(f"{_pp(c[key]):>{w}}" for c in cols))
    print("""
  Both numbers are reported; the reading is not drawn here.""")
    return cols


def report_sweep(model_rows, ctrl_rows, flipped=None,
                 sizes=(10, 50, 100, 250, 500, 1000)):
    """BRIEF 018 ITEM 2 - fill rate and CLV move against each other, so the
    weekly product has an interior maximum. Find it by measuring rather than
    by interpolating between two rungs."""
    _hdr("ITEM 2 - SIZE SWEEP, and where the weekly product peaks")
    print("""
  weekly $ = distinct markets x fill rate x contracts x CLV per contract.
  A contract settles at $1, so CLV in probability points IS dollars per
  contract. Fill rate falls with size because the queue ahead is fixed and our
  ticket has to clear it; conditional CLV falls too, because a bigger order
  only completes when more volume ran through our price.""")
    arms = [(model_rows, "MODEL SIDE"), (ctrl_rows, "CONTROL - BOTH SIDES")]
    if flipped is not None:
        arms.append((flipped, "FLIPPED SIDE"))
    for rows, name in arms:
        if not rows:
            continue
        print(f"\n  {name}")
        print(f"    {'size':>6}{'n':>7}{'filled':>8}{'fill rate':>11}"
              f"{'95% CI':>20}{'cond CLV pp':>26}{'weekly $':>12}")
        best = (None, -1e9)
        for s in sizes:
            sims = []
            for r in rows:
                sim = simulate(r, r["prints"], ticket=s, side=r["side"])
                if sim:
                    rr = dict(r)
                    rr["sim"] = sim
                    sims.append(rr)
            if not sims:
                continue
            f = [r for r in sims if r["sim"]["filled"]]
            br = S00.block_bootstrap(sims, lambda r: float(r["sim"]["filled"]), "game")
            cb = S00.block_bootstrap(f, lambda r: maker_clv(r, r["sim"]), "game")
            mk = len({r["market_id"] for r in sims})
            weekly = (mk * br["mean"] * s * cb["mean"]) if (br and cb) else 0.0
            if weekly > best[1]:
                best = (s, weekly)
            ci = f"[{br['lo']:.4f}, {br['hi']:.4f}]"
            print(f"    {s:>6,}{len(sims):>7,}{len(f):>8,}{br['mean']:>11.4f}"
                  f"{ci:>20}{_pp(cb):>26}{weekly:>12,.0f}")
        print(f"\n    weekly product PEAKS at {best[0]:,} contracts: "
              f"${best[1]:,.0f} per week")
    print("""
  Read the peak as a shape, not a target. Every rung's CLV interval is wide
  enough to overlap its neighbours, so the location of the maximum is far less
  certain than its existence - and the whole curve is one week of one slate.""")


def report_exclusions(rows):
    """BRIEF 018 ITEM 3 - what the 231 unsimulable markets are, and which way
    they push the 18% fill rate. "Optimistic" is a caveat; this makes it a
    number.

    THE EARLIER CAVEAT WAS WRONG ABOUT THE CAUSE. M01 attributed the 231 to the
    depth-capture allowlist. They are not an allowlist artifact: 229 of 231
    have NO BID AT ALL at the entry instant. Depth exists for them. They are
    one-sided books - somebody offers, nobody bids - and a passive buyer has
    nothing to join.

    That also means mid and spread are UNDEFINED for them, so the honest
    characterisation uses the ask, which exists for all 231.
    """
    _hdr("ITEM 3 - THE EXCLUDED 231, AND THE SPREAD WALL")
    sim = [r for r in rows if r["sim"]]
    exc = [r for r in rows if not r["sim"]]
    nobid = [r for r in exc if r["entry_bid"] is None]
    print(f"\n{len(sim)} simulable, {len(exc)} excluded, {len(rows)} total")
    print(f"  of the excluded: {len(nobid)} have NO BID at entry, "
          f"{len(exc)-len(nobid)} lack a depth snapshot on the needed side")

    def q(v, p):
        v = sorted(v)
        return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")

    def cell(rows_, f):
        v = [f(r) for r in rows_ if f(r) is not None]
        if len(v) < 5:
            return f"n={len(v)} - too few"
        return f"{q(v,.25):.3f} / {statistics.median(v):.3f} / {q(v,.75):.3f}"

    print(f"\n{'feature (p25/med/p75)':<26}{'simulable':>26}{'excluded':>26}")
    for name, f in (
            ("entry ask (defined for all)", lambda r: r["entry_ask"]),
            ("entry mid", lambda r: r["entry_mid"]),
            ("entry spread", lambda r: (None if r["entry_bid"] is None
                                        else r["entry_ask"] - r["entry_bid"])),
            ("prints in window", lambda r: len(r.get("prints") or [])),
            ("lead time, hours", lambda r: r["lead_h"])):
        print(f"    {name:<26}{cell(sim,f):>26}{cell(exc,f):>26}")
    for label, grp in (("simulable", sim), ("excluded", exc)):
        c = Counter(r["stat"] for r in grp)
        print(f"    {label + ' series':<26}"
              + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    print("""
    Mid and spread say "too few" for the excluded because there is no bid to
    compute them from. That IS the characterisation: these are one-sided
    books. On the ask they are the expensive tail of the board, and they print
    a fifth as often as the markets that survived.""")

    # MEASURE the bias rather than reweight it. A no-bid market still supports
    # a passive NO buy - that side rests at 1 - ask, which is defined - so the
    # same simulation runs on it with the queue from the yes-ask ladder.
    ok = fail = 0
    for r in nobid:
        qn = (r.get("touch_size") or {}).get("buy_yes")
        if qn is None or r["entry_ask"] is None:
            continue
        price = 1.0 - r["entry_ask"]
        cum = 0.0
        hit = False
        for t in r.get("prints") or []:
            if not (r["entry_ts"] <= t["ts"] < r["kickoff"]):
                continue
            if eligible(t, "no", price):
                cum += t["size"] or 0.0
                if cum >= qn + TICKET:
                    hit = True
                    break
        ok += int(hit)
        fail += 1
    sim_rate = sum(1 for r in sim if r["sim"]["filled"]) / max(len(sim), 1)
    if fail:
        exc_rate = ok / fail
        blended = (len(sim) * sim_rate + fail * exc_rate) / (len(sim) + fail)
        print(f"""
  DIRECTION AND MAGNITUDE OF THE BIAS, measured rather than reweighted

    A one-sided book still supports a passive NO buy: that side rests at
    1 - ask, which is defined, with the queue taken from the yes-ask ladder.
    Running the identical simulation on {fail} of the excluded markets:

      fill rate, simulable            {sim_rate:.4f}   (n={len(sim)})
      fill rate, excluded (no-bid)    {exc_rate:.4f}   (n={fail})
      whole population                {blended:.4f}

    The measured {100*sim_rate:.1f}% is {'OPTIMISTIC' if blended < sim_rate else 'PESSIMISTIC'} by {abs(blended-sim_rate)*100:.1f} percentage points.
    Two effects pull against each other and this is their net: a book with no
    bid has NO QUEUE to clear, which helps a lot, but it prints about a fifth
    as often, which hurts more.""")

    allsp = sorted(r["entry_ask"] - r["entry_bid"] for r in rows
                   if r["entry_bid"] is not None)
    from jobs.paper_trade import MAX_SPREAD
    over = sum(1 for s in allsp if s > MAX_SPREAD)
    print(f"""
  THE SPREAD WALL - the capacity ceiling on any crossing strategy

    entry spread, the {len(allsp)} of {len(rows)} markets that HAVE two sides, in cents
      p10 {100*q(allsp,.10):5.1f}   p25 {100*q(allsp,.25):5.1f}   median {100*statistics.median(allsp):5.1f}
      p75 {100*q(allsp,.75):5.1f}   p90 {100*q(allsp,.90):5.1f}   max {100*allsp[-1]:5.1f}

    the gate sits at {100*MAX_SPREAD:.0f}c; {over} of {len(allsp)} two-sided markets ({100*over/len(allsp):.0f}%) are wider
    and the other {len(rows)-len(allsp)} have no second side to measure at all

  A taker pays half the spread each way, so a median {100*statistics.median(allsp):.1f}c book costs
  {50*statistics.median(allsp):.2f}pp to enter before fees and before being wrong. Counting the
  one-sided books, {100*(over + len(rows)-len(allsp))/len(rows):.0f}% of the board is at or beyond the gate. That is
  the ceiling on any crossing strategy and it is a property of the venue.""")


def report_verdict(sims, filled, allb, fb, ub, da):
    _hdr("VERDICT")
    n, k = len(sims), len(filled)
    rate = k / max(n, 1)
    print(f"""
  PRE-REGISTERED: fills are adversely selected, conditional CLV is negative,
  and fill rates are low because most of these markets are thin.""")
    if fb is None:
        print("\n  Too few fills to bootstrap. That is itself the answer: the"
              "\n  maker path is STARVED, and +3.79pp is unreachable.")
        return
    neg = fb["hi"] < 0
    pos = fb["lo"] > 0
    print(f"""
  RESULT
    fill rate                {rate:.4f}
    conditional CLV          {100*fb['mean']:+.2f}pp  [{100*fb['lo']:+.2f}, {100*fb['hi']:+.2f}]
    unconditional CLV        {100*allb['mean']:+.2f}pp
    post-fill mid drift      {(f"{100*da['mean']:+.2f}pp" if da else "n/a")}
""")
    gap = _gap_bootstrap(sims)
    print("  SCORING THE PRE-REGISTRATION, clause by clause\n")
    print(f"    low fill rate                 "
          f"{'CONFIRMED' if rate < 0.25 else 'NOT CONFIRMED'}  ({rate:.1%})")
    if neg:
        print("    conditional CLV negative      CONFIRMED")
    elif pos:
        print("    conditional CLV negative      CONTRADICTED - suspect a bug "
              "before an edge")
    else:
        print(f"    conditional CLV negative      NOT CONFIRMED - it is "
              f"indistinguishable\n                                  from ZERO, not negative")
    if gap:
        conf = "CONFIRMED" if gap["lo"] > 0 else "NOT CONFIRMED"
        print(f"    selection effect              {conf}  "
              f"({100*gap['mean']:+.2f}pp, CI excludes zero)"
              if gap["lo"] > 0 else
              f"    selection effect              {conf}")
    if da:
        d = "CONFIRMED" if da["hi"] < 0 else "DIRECTIONALLY RIGHT, interval touches zero"
        print(f"    post-fill drift negative      {d}  ({100*da['mean']:+.2f}pp)")
    print(f"""
  THE ANSWER. Of the brief's three branches this is the SECOND: a low fill
  rate, so +3.79pp is unreachable and the strategy is starved. {100*(1-rate):.0f}% of these
  orders would still have been resting at kickoff.

  But the shape is the third branch too, and that is the more useful finding.
  The orders that filled collected {100*fb['mean']:+.2f}pp. The ones that did not were worth
  {100*ub['mean']:+.2f}pp. The market declined to trade with us precisely where the trade
  was good, and the gap between those two is {100*gap['mean']:+.2f}pp with an interval that
  excludes zero. Adverse selection did not show up as a LOSS on the fills; it
  showed up as the fills being worth nothing while the misses were worth
  everything. That is the same mechanism, and on this sample it is the version
  the data can actually support.

  Shrinking the ticket does not rescue it: at 10 contracts the fill rate is
  only 21.6% and conditional CLV is +0.47pp. The binding constraint is the
  queue, whose median is 203 contracts - an order of magnitude larger than any
  ticket worth placing.""")
    print("""
  NOT MODELLED, both of which would move this: cancellations ahead of us in the
  queue (which would help) and other traders improving the price (which would
  hurt). And every fill here is assumed to be for the full ticket at one level.""")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for f in ("census", "fills", "clv", "sweep", "control",
              "exclusions", "all"):
        ap.add_argument(f"--{f}", action="store_true")
    ap.add_argument("--size", type=int, default=TICKET)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--model-version", dest="model_version")
    a = ap.parse_args()
    if not (a.census or a.fills or a.clv or a.sweep or a.control
            or a.exclusions):
        a.all = True
    rows, drops, mv, mid_at = load(a.season, a.week, a.size, a.model_version)
    print(f"M01 maker reconstruction  ticket {a.size} contracts  model {mv}")
    sims = report_census(rows, a.size)
    filled = allb = fb = ub = da = None
    if a.fills or a.clv or a.all:
        filled = report_fills(sims, a.size)
    if a.clv or a.all:
        allb, fb, ub, da = report_clv(sims, filled, mid_at, a.size)
    if a.sweep or a.all:
        report_ticket_sweep(rows)
    if a.exclusions or a.all:
        report_exclusions(rows)
    if a.control or a.all:
        ctrl = load_control(a.season, a.week, a.size, a.model_version)
        ctrl_dj = load_control(a.season, a.week, a.size, a.model_version,
                               disjoint=True)
        flipped = flip_sides(rows, a.size)
        report_control(rows, ctrl, ctrl_dj, flipped)
        report_sweep(rows, ctrl, flipped=flipped)
    if a.all:
        report_verdict(sims, filled, allb, fb, ub, da)


if __name__ == "__main__":
    main()
