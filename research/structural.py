"""BRIEF 019 - the structural hunt. Are Kalshi's prices consistent with each other?

    python research/structural.py --all
    python research/structural.py --catalogue       # item 0
    python research/structural.py --monotonicity    # item 1
    python research/structural.py --partition       # item 2
    python research/structural.py --capture         # item 3

READ-ONLY against the logger's database. Catalogue snapshot from
`jobs/snapshot_kalshi_series.py` (board_019.db), prints from
`jobs/ingest_kalshi_trades.py` (trades_m01.db).

PRE-REGISTERED, fixed before any result was seen, and NOT to be extended here:

    H1  a threshold ladder violates monotonicity ON THE TOUCH somewhere in the
        logged history (item 1)
    H2  a first-TD-scorer partition violates sum(ask) < 1 or sum(bid) > 1
        (item 2) - conditional on the partition being complete
    H3  both-sides spread capture differs by series, with the standing
        prediction that it is WORSE on tighter, deeper series (item 3)

Item 0 is descriptive and tests nothing. Anything else that turns up is written
down as a candidate for the next brief, never chased inside this one.

WHY THE TOUCH AND NOT THE MID (item 1)
For rungs t_i < t_j on one player-stat, {X > t_j} is a subset of {X > t_i}.
Buy YES on the lower rung at ask_i and buy NO on the higher rung at 1 - bid_j:
the payoff is 2 when t_i < X <= t_j and 1 otherwise, so never below 1, for a
cost of ask_i + 1 - bid_j. The trade is riskless profit exactly when

    ask_i < bid_j

A mid-based version of this test finds "violations" that no order could have
captured, which is the same failure as a mid-based CLV. Only the touch counts.

WHAT A LOGGED BOOK CAN AND CANNOT SEE
Quotes are written on CHANGE plus a five-minute heartbeat, and rungs of one
event are polled together, so a rung's state at any poll of its event is its
last write. The scan reconstructs that as-of state. Two consequences:

  * persistence is only visible down to the poll cadence - median ~60s on the
    props, ~10s at best in the hot and live tiers. A violation lasting four
    seconds is invisible here, and so is the latency race it would be.
  * a leg whose polling stopped would freeze into a phantom violation, so a
    leg older than MAX_STALE is not evaluated at all.

`strict` additionally requires both legs to have been WRITTEN within SAME_BATCH
of each other, which removes any doubt about simultaneity at the price of
missing violations against a rung that simply did not move.
"""
import argparse
import gzip
import json
import math
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.fees import fee_per_contract, series_multiplier

ET = ZoneInfo("America/New_York")
STORE_DIR = os.path.dirname(os.path.abspath(config.DB_PATH))
BOARD_DB = os.path.join(STORE_DIR, "board_019.db")
TRADES_DB = os.path.join(STORE_DIR, "trades_m01.db")

# Every tracked series whose markets are "over / at least / k+" rungs on one
# quantity. Checked against titles, not assumed: receptions "6+", rush attempts
# "3+", spread "wins by over 9.5", total "over 27.5", season wins "at least 5",
# wins-by-week "at least 1 game in the first 12 weeks", streak "at least 5".
LADDER_SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD", "KXNFLTOTAL",
                 "KXNFLWINS", "KXNFLWINSWEEK", "KXNFLWINSTREAK")

MAX_STALE = 660.0     # s. Cold tier polls every 600s; beyond this, not polled.
SAME_BATCH = 15.0     # s. Both legs written inside one poll cycle.
EPS = 1e-9
SIZES = (10, 50, 100)
DEPTH_WINDOW = 60.0   # s either side of an episode start

# A first-TD-scorer leg that is the "nobody scores" outcome. Anchored to the
# END of the ticker so a player code that merely contains the letters cannot
# match - "NONE" inside a surname is not a no-touchdown leg.
NO_TD = re.compile(r"(NO-?TD|NONE)$")


def live():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def _hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")


# =============================================================================
# ladder structure
# =============================================================================

def ladder_key(market_id):
    """The quantity a rung is a threshold ON - the market id with its trailing
    threshold number removed.

        KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6  ->  ...-NYGOBECKHAM13
        KXNFLSPREAD-26SEP13DALNYG-DAL10          ->  ...-DAL
        KXNFLTOTAL-26SEP13DALNYG-28              ->  KXNFLTOTAL-26SEP13DALNYG
        KXNFLWINS-27DAL-1                        ->  KXNFLWINS-27DAL

    The spread case is why this strips digits and not a whole segment: both
    teams' ladders live in ONE event on alternating rungs, and pooling them
    would compare Dallas-by-10 against New-York-by-7 as if they were one curve.
    """
    return re.sub(r"-?\d+$", "", market_id)


def violates(ask_lo, bid_hi):
    """The executable monotonicity arbitrage, derived in the module docstring.
    Needs an ask on the LOWER rung and a bid on the HIGHER one; a one-sided
    book on either leg is not a trade."""
    if ask_lo is None or bid_hi is None:
        return False
    return ask_lo < bid_hi - EPS


def net_per_contract(ask_lo, bid_hi, contracts, taker_m=None):
    """Profit per contract after the taker fee on BOTH legs, at a real order
    size. Leg one pays `ask_lo`; leg two buys NO at `1 - bid_hi`, and the fee
    is charged on the price paid. None where a leg's price is not tradeable."""
    p1, p2 = ask_lo, 1.0 - bid_hi
    if not (0 < p1 < 1 and 0 < p2 < 1):
        return None
    return ((bid_hi - ask_lo)
            - fee_per_contract(p1, contracts, "taker", taker_m)
            - fee_per_contract(p2, contracts, "taker", taker_m))


def net_continuous(ask_lo, bid_hi):
    """The brief's 7*p*(1-p) pp per leg, unrounded - the fee a very large
    order converges to."""
    p1, p2 = ask_lo, 1.0 - bid_hi
    return (bid_hi - ask_lo) - 0.07 * p1 * (1 - p1) - 0.07 * p2 * (1 - p2)


class Episode:
    """One continuous stretch during which a rung pair was in violation."""
    __slots__ = ("series", "key", "event", "lo", "hi", "line_lo", "line_hi",
                 "start", "last", "end", "n_obs", "strict_any", "ask_lo",
                 "bid_hi", "gap", "max_gap", "reason")

    def __init__(self, series, key, event, lo, hi, line_lo, line_hi, t,
                 ask_lo, bid_hi, strict):
        self.series, self.key, self.event = series, key, event
        self.lo, self.hi, self.line_lo, self.line_hi = lo, hi, line_lo, line_hi
        self.start = self.last = t
        self.end = None
        self.n_obs = 1
        self.strict_any = strict
        self.ask_lo, self.bid_hi = ask_lo, bid_hi
        self.gap = self.max_gap = bid_hi - ask_lo
        self.reason = "open"

    @property
    def lower_s(self):
        return self.last - self.start

    @property
    def upper_s(self):
        return None if self.end is None else self.end - self.start


class LadderScan:
    """As-of book reconstruction for one event, with per-pair episodes.

    A pair's status can only change when one of its two legs is written, so at
    each instant only pairs touching a written rung are evaluated - O(rungs)
    per write rather than O(rungs^2) per instant.
    """

    def __init__(self, series, event, lines, max_stale=MAX_STALE,
                 same_batch=SAME_BATCH):
        self.series, self.event = series, event
        self.max_stale, self.same_batch = max_stale, same_batch
        self.line = dict(lines)
        self.ladders = defaultdict(list)
        for mid in lines:
            self.ladders[ladder_key(mid)].append(mid)
        for k in self.ladders:
            self.ladders[k].sort(key=lambda m: self.line[m])
        self.state = {}
        self.open = {}
        self.episodes = []
        self.obs = 0
        self.obs_strict = 0
        self.instants = []

    def write(self, t, market_id, bid, ask):
        self.state[market_id] = (bid, ask, t)

    def evaluate(self, t, touched):
        self.instants.append(t)
        seen = set()
        for mid in touched:
            if mid not in self.line:
                continue
            for other in self.ladders[ladder_key(mid)]:
                if other == mid or self.line[other] == self.line[mid]:
                    continue
                lo, hi = ((mid, other) if self.line[mid] < self.line[other]
                          else (other, mid))
                if (lo, hi) in seen:
                    continue
                seen.add((lo, hi))
                self._pair(t, lo, hi)

    def _pair(self, t, lo, hi):
        s_lo, s_hi = self.state.get(lo), self.state.get(hi)
        ep = self.open.get((lo, hi))
        fresh = (s_lo is not None and s_hi is not None
                 and t - s_lo[2] <= self.max_stale
                 and t - s_hi[2] <= self.max_stale)
        if not fresh:
            if ep:
                ep.reason = "went stale"
                self.episodes.append(ep)
                del self.open[(lo, hi)]
            return
        ask_lo, bid_hi = s_lo[1], s_hi[0]
        if violates(ask_lo, bid_hi):
            strict = abs(s_lo[2] - s_hi[2]) <= self.same_batch
            self.obs += 1
            self.obs_strict += int(strict)
            if ep is None:
                self.open[(lo, hi)] = Episode(
                    self.series, ladder_key(lo), self.event, lo, hi,
                    self.line[lo], self.line[hi], t, ask_lo, bid_hi, strict)
            else:
                ep.last = t
                ep.n_obs += 1
                ep.strict_any = ep.strict_any or strict
                ep.max_gap = max(ep.max_gap, bid_hi - ask_lo)
        elif ep:
            ep.end = t
            ep.reason = "closed"
            self.episodes.append(ep)
            del self.open[(lo, hi)]

    def finish(self):
        for ep in self.open.values():
            ep.reason = "censored"
            self.episodes.append(ep)
        self.open = {}
        return self.episodes


def scan_series(con, series):
    lines = {m: ln for m, ln in con.execute(
        "SELECT market_id, line FROM markets WHERE venue='kalshi' "
        "AND market_id >= ? AND market_id < ? AND line IS NOT NULL",
        (series + "-", series + "."))}
    events = defaultdict(dict)
    for mid, ln in lines.items():
        parts = mid.split("-")
        events[parts[0] + "-" + parts[1]][mid] = ln
    episodes, obs, obs_strict, gaps = [], 0, 0, []
    for ev, ev_lines in events.items():
        scan = LadderScan(series, ev, ev_lines)
        pending, touched = None, set()
        for ts, mid, bid, ask in con.execute(
                "SELECT ts, market_id, best_bid, best_ask FROM quotes "
                "WHERE venue='kalshi' AND event_id=? AND source='live' "
                "ORDER BY ts", (ev,)):
            if pending is not None and ts != pending:
                scan.evaluate(pending, touched)
                touched = set()
            scan.write(ts, mid, bid, ask)
            touched.add(mid)
            pending = ts
        if pending is not None:
            scan.evaluate(pending, touched)
        episodes += scan.finish()
        obs += scan.obs
        obs_strict += scan.obs_strict
        gaps += [b - a for a, b in zip(scan.instants, scan.instants[1:])]
    return episodes, obs, obs_strict, gaps


def depth_at(con, market_id, side, t):
    return con.execute(
        "SELECT ts, touch_price, touch_size FROM market_depth "
        "WHERE venue='kalshi' AND market_id=? AND side=? AND ts BETWEEN ? AND ? "
        "AND touch_price IS NOT NULL ORDER BY ABS(ts - ?) LIMIT 1",
        (market_id, side, t - DEPTH_WINDOW, t + DEPTH_WINDOW, t)).fetchone()


def feasibility(con, ep):
    """Depth at the episode's start, and whether it describes the SAME book.

    A depth snapshot up to a minute away may show different touch prices; if
    so it is not evidence of size at the violating prices, and the episode is
    marked unverifiable rather than feasible."""
    a = depth_at(con, ep.lo, "buy_yes", ep.start)
    b = depth_at(con, ep.hi, "buy_no", ep.start)
    if not a or not b:
        return "no depth snapshot", None
    same = (abs(a[1] - ep.ask_lo) < 1e-6 and abs((1 - b[1]) - ep.bid_hi) < 1e-6)
    if not same:
        return "depth shows a different book", None
    return "ok", min(a[2] or 0.0, b[2] or 0.0)


def tt_bucket(kick, t):
    if kick is None:
        return "no kickoff (future)"
    h = (kick - t) / 3600.0
    if h > 24:
        return "a >24h pre"
    if h > 4:
        return "b 4-24h pre"
    if h > 0:
        return "c 0-4h pre (hot)"
    if h > -4:
        return "d live (0-4h post)"
    return "e post-game"


def persistence_buckets(eps):
    b = Counter()
    for e in eps:
        s = e.lower_s
        b["1 seen once" if e.n_obs == 1 else
          "2 <15s" if s < 15 else "3 15-60s" if s < 60 else
          "4 1-5 min" if s < 300 else "5 5-30 min" if s < 1800 else
          "6 >30 min"] += 1
    return b


def report_monotonicity():
    _hdr("ITEM 1 - LADDER MONOTONICITY ON THE TOUCH  (H1)")
    import store
    con = live()
    kick = store.kickoff_map(("kalshi",))
    all_eps, tot_obs, tot_strict = [], 0, 0
    print(f"\n    {'series':<16}{'events':>7}{'viol obs':>10}{'strict':>8}"
          f"{'episodes':>10}{'median poll gap s':>19}")
    for s in LADDER_SERIES:
        eps, obs, obs_strict, gaps = scan_series(con, s)
        all_eps += eps
        tot_obs += obs
        tot_strict += obs_strict
        n_ev = len({e.event for e in eps})
        g = statistics.median(gaps) if gaps else float("nan")
        print(f"    {s:<16}{n_ev:>7}{obs:>10,}{obs_strict:>8,}{len(eps):>10,}"
              f"{g:>19.1f}")
    print(f"    {'TOTAL':<16}{'':>7}{tot_obs:>10,}{tot_strict:>8,}{len(all_eps):>10,}")
    print("""
  'viol obs' counts (player, stat, rung pair, poll instant) violations - the
  brief's raw count. 'strict' keeps only those where both legs were written in
  the same poll cycle. 'episodes' collapses consecutive violating instants of
  one pair into one event, which is the unit that could be traded. 'events' is
  how many kalshi events contained at least one episode.""")
    if not all_eps:
        print("\n  NO VIOLATIONS. H1 is not supported anywhere in the logged history.")
        return all_eps

    gaps_c = [100 * e.gap for e in all_eps]
    print(f"""
  bid_j - ask_i at episode start, cents (n={len(all_eps):,} episodes)
    min {min(gaps_c):.2f}  p25 {q(gaps_c,.25):.2f}  median {statistics.median(gaps_c):.2f}  p75 {q(gaps_c,.75):.2f}  p90 {q(gaps_c,.90):.2f}  max {max(gaps_c):.2f}""")

    rows = []
    for e in all_eps:
        _, tm = series_multiplier(e.lo)
        r = {"ep": e, "cont": net_continuous(e.ask_lo, e.bid_hi)}
        for n in SIZES:
            r[n] = net_per_contract(e.ask_lo, e.bid_hi, n, tm)
        rows.append(r)
    surv_cont = [r for r in rows if r["cont"] > 0]
    print(f"\n  POST-FEE SURVIVAL, taker fee on both legs")
    print(f"    continuous 7p(1-p) per leg        {len(surv_cont):>6,} of {len(rows):,}")
    for n in SIZES:
        sv = [r for r in rows if r[n] is not None and r[n] > 0]
        print(f"    at {n:>3} contracts, rounded fee      {len(sv):>6,} of {len(rows):,}")

    survivors = [r for r in rows
                 if any(r[n] is not None and r[n] > 0 for n in SIZES)]
    feas, size_ok = Counter(), Counter()
    for r in survivors:
        status, size = feasibility(con, r["ep"])
        r["depth"] = (status, size)
        feas[status] += 1
        if status == "ok":
            for n in SIZES:
                if r[n] is not None and r[n] > 0 and size >= n:
                    size_ok[n] += 1
    print(f"\n  DEPTH at the violating touch, for the {len(survivors):,} post-fee survivors")
    for k, v in feas.most_common():
        print(f"    {k:<32}{v:>6,}")
    for n in SIZES:
        print(f"    fee-positive AND >= {n:>3} contracts on both legs   {size_ok[n]:>6,}")
    verified = [r["depth"][1] for r in survivors if r["depth"][0] == "ok"]
    if verified:
        print(f"    min touch size on the thinner leg, depth-verified survivors "
              f"(n={len(verified)}): median {statistics.median(verified):.1f}  "
              f"max {max(verified):.1f} contracts")
    print("""
    'no depth snapshot' and 'different book' are UNVERIFIABLE, not infeasible:
    depth is captured on its own cadence, and an in-game book a minute away is
    a different book. Only the verified rows are evidence about size.""")

    def persistence(eps, label):
        lo = [e.lower_s for e in eps]
        b = persistence_buckets(eps)
        print(f"\n  PERSISTENCE, {label} (n={len(eps):,}) - lower bound, seconds")
        if lo:
            print(f"    median {statistics.median(lo):.0f}  p75 {q(lo,.75):.0f}  "
                  f"p90 {q(lo,.90):.0f}  max {max(lo):.0f}")
        for k in sorted(b):
            print(f"    {k[2:]:<14}{b[k]:>7,}  {100*b[k]/max(len(eps),1):5.1f}%")
        why = Counter(e.reason for e in eps)
        if why:
            print("    ended by: " + "  ".join(f"{k}={v}" for k, v in why.most_common()))

    persistence(all_eps, "all episodes")
    persistence([r["ep"] for r in survivors], "POST-FEE SURVIVORS")
    persistence([r["ep"] for r in survivors
                 if r["depth"][0] == "ok" and r["ep"].strict_any],
                "survivors, depth-verified AND strict")

    def clusters(eps, label):
        print(f"\n  CLUSTERING - {label}")
        for name, f in (
                ("by series", lambda e: e.series),
                ("by time to kickoff",
                 lambda e: tt_bucket(kick.get(("kalshi", e.lo)), e.start)),
                ("by hour of day ET",
                 lambda e: f"{datetime.fromtimestamp(e.start, ET):%H}h")):
            c = Counter(f(e) for e in eps)
            print(f"    {name}: " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))

    clusters(all_eps, "all episodes")
    clusters([r["ep"] for r in survivors], "post-fee survivors")
    print("""
  RESOLUTION FLOOR. Persistence is measured in poll instants. At a ~60s median
  cadence an episode 'seen once' lasted anywhere from zero to about a minute,
  and one that lasted four seconds between two polls was never seen at all.""")
    return all_eps


# =============================================================================
# item 0 - the catalogue
# =============================================================================

def classify(event_rows, market_tickers):
    """Series -> (structure, rungs-per-ladder, legs-per-partition).

    `event_rows` is (event_ticker, series, mutually_exclusive, n_markets);
    `market_tickers` maps event_ticker -> [market tickers].
    `mutually_exclusive` is Kalshi's statement that at most one leg resolves
    yes; it does NOT say the legs are exhaustive.
    """
    per = defaultdict(list)
    for ev, s, me, n in event_rows:
        per[s].append((ev, me, n))
    out = {}
    for s, evs in per.items():
        kinds, rungs, legs = Counter(), [], []
        for ev, me, n in evs:
            if not n:
                continue
            groups = Counter(ladder_key(t) for t in market_tickers.get(ev, []))
            ladderish = [g for g in groups.values() if g >= 2]
            if me and n > 1:
                kinds["partition"] += 1
                legs.append(n)
            elif ladderish:
                kinds["ladder"] += 1
                rungs += ladderish
            elif n == 1:
                kinds["standalone"] += 1
            else:
                kinds["multi-binary"] += 1
        st = kinds.most_common(1)[0][0] if kinds else "no open markets"
        out[s] = (st, statistics.median(rungs) if rungs else None,
                  statistics.median(legs) if legs else None)
    return out


def report_catalogue():
    _hdr("ITEM 0 - THE BOARD  (descriptive; tests nothing)")
    if not os.path.exists(BOARD_DB):
        print("  no snapshot - run jobs/snapshot_kalshi_series.py")
        return
    board = sqlite3.connect(f"file:{BOARD_DB}?mode=ro", uri=True)
    con = live()
    series = board.execute(
        "SELECT ticker, title, fee_type, fee_multiplier, tracked FROM series").fetchall()
    mk = defaultdict(list)
    for tk, ev in board.execute("SELECT ticker, event_ticker FROM markets"):
        mk[ev].append(tk)
    struct = classify(board.execute(
        "SELECT event_ticker, series, mutually_exclusive, n_markets FROM events"
    ).fetchall(), mk)
    snap = {}
    for s, bid, ask, vol in board.execute(
            "SELECT series, yes_bid, yes_ask, volume FROM markets"):
        d = snap.setdefault(s, {"n": 0, "spread": [], "vol": []})
        d["n"] += 1
        if bid is not None and ask is not None and bid > 0 and ask < 1 and ask >= bid:
            d["spread"].append(100 * (ask - bid))
        if vol is not None:
            d["vol"].append(vol)

    per_mkt = defaultdict(list)
    if os.path.exists(TRADES_DB):
        tr = sqlite3.connect(f"file:{TRADES_DB}?mode=ro", uri=True)
        for mid, n in tr.execute("SELECT market_id, n_trades FROM market_trades_fetch "
                                 "WHERE status=200"):
            per_mkt[mid.split("-")[0]].append(n)

    hist = {}
    for s in {r[0] for r in series if r[4]}:
        sp = [100 * (a - b) for b, a in con.execute(
            "SELECT best_bid, best_ask FROM quotes WHERE venue='kalshi' AND source='live' "
            "AND market_id >= ? AND market_id < ? AND best_bid IS NOT NULL "
            "AND best_ask IS NOT NULL", (s + "-", s + "."))]
        rows_per = [n for (n,) in con.execute(
            "SELECT COUNT(*) FROM quotes WHERE venue='kalshi' AND source='live' "
            "AND market_id >= ? AND market_id < ? GROUP BY market_id", (s + "-", s + "."))]
        if sp or rows_per:
            hist[s] = (statistics.median(sp) if sp else None,
                       statistics.median(rows_per) if rows_per else None)

    n_open = sum(1 for s in snap if snap[s]["n"])
    print(f"""
  Kalshi lists {len(series)} NFL series. The logger tracks {sum(r[4] for r in series)} of them
  (config.KALSHI_SERIES_ALLOW) - all of them would be ~16,700 open markets and
  fill the disk in a day. {n_open} series had open markets at the snapshot.

  SNAP columns (mkts, spread, vol) come from one current listing and are all
  that exists for an untracked series. HIST columns (H spr, H rows) come from
  logged quotes, and prints from fetched trade history - tracked series only.
  The two are not interchangeable: a snapshot is week 2 as of now, the history
  is week 1.

  maker: Kalshi's /series `fee_type`. `quadratic_with_maker_fees` charges a
  maker fee; `quadratic` does not. That is the exchange's own billing field,
  and `core/fees.SERIES_M` is now reconciled against it.""")

    table = []
    for tk, title, fee_type, fm, trk in series:
        d = snap.get(tk, {"n": 0, "spread": [], "vol": []})
        st, rungs, legs = struct.get(tk, ("no open markets", None, None))
        h = hist.get(tk, (None, None))
        pm = per_mkt.get(tk)
        table.append({
            "tk": tk, "title": (title or "")[:34], "n": d["n"], "st": st,
            "rungs": rungs, "legs": legs,
            "spread": statistics.median(d["spread"]) if d["spread"] else None,
            "vol": statistics.median(d["vol"]) if d["vol"] else None,
            "maker": fee_type == "quadratic_with_maker_fees",
            "tracked": bool(trk), "h_spread": h[0], "h_rows": h[1],
            "prints": statistics.median(pm) if pm else None})

    def fmt(x, f):
        return f"{x:{f}}" if x is not None else "-"

    live_rows = [r for r in table if r["n"]]
    print(f"\n  RANKED BY SNAPSHOT MEDIAN TOUCH SPREAD (tightest first)")
    print(f"    {'series':<22}{'structure':<14}{'mkts':>6}{'rungs':>6}{'legs':>5}"
          f"{'spread c':>9}{'vol':>10}{'maker':>6}{'trk':>4}{'H spr':>7}{'H rows':>8}{'prints':>7}")
    for r in sorted([r for r in live_rows if r["spread"] is not None],
                    key=lambda r: r["spread"]):
        print(f"    {r['tk']:<22}{r['st']:<14}{r['n']:>6}{fmt(r['rungs'],'.0f'):>6}"
              f"{fmt(r['legs'],'.0f'):>5}{fmt(r['spread'],'.1f'):>9}{fmt(r['vol'],',.0f'):>10}"
              f"{('yes' if r['maker'] else 'no'):>6}{('Y' if r['tracked'] else ''):>4}"
              f"{fmt(r['h_spread'],'.1f'):>7}{fmt(r['h_rows'],',.0f'):>8}{fmt(r['prints'],'.0f'):>7}")
    one = [r for r in live_rows if r["spread"] is None]
    if one:
        print(f"\n    {len(one)} series had open markets but no two-sided market at the "
              f"snapshot: " + ", ".join(r["tk"] for r in one))

    print(f"\n  RANKED BY SNAPSHOT MEDIAN VOLUME PER MARKET (most active first, top 25)")
    for r in sorted([r for r in live_rows if r["vol"]], key=lambda r: -r["vol"])[:25]:
        print(f"    {r['tk']:<22}{r['st']:<14}{fmt(r['vol'],',.0f'):>12}"
              f"   spread {fmt(r['spread'],'.1f')}c   maker {'yes' if r['maker'] else 'no'}")
    have_prints = [r for r in table if r["prints"] is not None]
    if have_prints:
        print(f"\n  RANKED BY LOGGED PRINTS PER MARKET (only series whose prints were fetched)")
        for r in sorted(have_prints, key=lambda r: -r["prints"]):
            print(f"    {r['tk']:<22}{fmt(r['prints'],'.0f'):>8}")
    print("\n  structure across all listed series: "
          + "  ".join(f"{k}={v}" for k, v in Counter(r["st"] for r in table).most_common()))
    print(f"  maker-fee series: {sum(r['maker'] for r in table)} of {len(table)}")
    return table


# =============================================================================
# item 2 - partitions
# =============================================================================

def leg_kind(leg):
    """'no-td', 'dst' or 'player', for a market dict or a bare ticker.

    The market's own `yes_sub_title` is the authority when present. A ticker
    regex is a guess about naming: week 1's SF@LA event carried two "No
    Touchdown" legs, `-NONE` and `-LARNONE`, and only an end-anchored pattern
    happened to catch both. The subtitle says "No Touchdown" on each.
    """
    if isinstance(leg, dict):
        sub = (leg.get("yes_sub_title") or "").strip().lower()
        ticker = leg.get("ticker", "")
        if sub:
            if "no touchdown" in sub:
                return "no-td"
            if sub.endswith("d/st") or ticker.endswith("DST"):
                return "dst"
            return "player"
    else:
        ticker = leg
    if NO_TD.search(ticker):
        return "no-td"
    if ticker.endswith("DST"):
        return "dst"
    return "player"


def partition_completeness(legs, check_tradeable=True):
    """Verdict for one first-TD-scorer event, BEFORE any sum is computed.

    A first-TD-scorer event is a partition only if it offers the plausible
    scorers AND the no-touchdown outcome - without that leg the listed
    outcomes cannot sum to one, because "nobody scored" is real probability
    mass that no contract carries.

    `check_tradeable` judges each leg's book. It must be FALSE for settled
    markets: those quote 0/1 after resolution, so every leg would read as
    one-sided and every event as incomplete for a reason that has nothing to
    do with the partition. Tradeability before kickoff needs a logged pre-game
    book, which an untracked series does not have.
    """
    kinds = Counter(leg_kind(m) for m in legs)
    problems = []
    if kinds["no-td"] == 0:
        problems.append("no 'no touchdown' outcome")
    if kinds["no-td"] > 1:
        # Two contracts on one outcome. If nobody scores both pay, so the legs
        # overlap and cannot sum to one - not a partition, whatever else it is.
        problems.append(f"{kinds['no-td']} 'no touchdown' legs - the same outcome "
                        "listed twice, so the legs overlap")
    if kinds["player"] == 0:
        problems.append("no PLAYER outcomes - only D/ST and no-TD")
    if check_tradeable:
        bad = 0
        for m in legs:
            try:
                b = float(m.get("yes_bid_dollars"))
                a = float(m.get("yes_ask_dollars"))
            except (TypeError, ValueError):
                bad += 1
                continue
            if b <= 0 or a >= 1:
                bad += 1
        if bad:
            problems.append(f"{bad} leg(s) one-sided or empty")
    return (not problems), dict(kinds), problems


def settlement_proof(legs):
    """For a SETTLED event: did any listed leg resolve yes? If none did, the
    outcome that actually happened was not listed - which is direct evidence of
    incompleteness, stronger than any structural argument."""
    results = [m.get("result") for m in legs]
    if not any(r in ("yes", "no") for r in results):
        return None
    return any(r == "yes" for r in results)


def load_firsttd_payloads():
    found = []
    for venue in ("kalshi_series", "kalshi_series_settled"):
        root = os.path.join(config.RAW_DIR, venue)
        if not os.path.isdir(root):
            continue
        for day in sorted(os.listdir(root)):
            for fn in sorted(os.listdir(os.path.join(root, day))):
                with gzip.open(os.path.join(root, day, fn), "rt", encoding="utf-8") as f:
                    for line in f:
                        rec = json.loads(line)
                        ep = rec.get("endpoint", "")
                        if ep.startswith("markets:KXNFLFIRSTTD"):
                            found.append((ep, rec["payload"]))
    return found


def report_partition():
    _hdr("ITEM 2 - FIRST-TD-SCORER PARTITION SUMS  (H2)")
    tracked = any(p == "KXNFLFIRSTTD" or (p.endswith("*") and "KXNFLFIRSTTD".startswith(p[:-1]))
                  for p, _m, _h in config.KALSHI_SERIES_ALLOW)
    print(f"""
  The player first-TD-scorer partition on Kalshi is KXNFLFIRSTTD. Anytime-TD
  (KXNFLANYTD) and the 1+/2+ ladder (KXNFLTD) are NOT partitions and are kept
  out by construction - the only payloads read are KXNFLFIRSTTD listings.

  Tracked by the logger: {tracked}. So there is NO logged order-book history for
  this series, and whatever the completeness verdict a historical sum and its
  persistence cannot be computed from this data. Candlesticks carry per-period
  bid/ask, but candle closes on 20+ legs are not simultaneous touches and cannot
  support an executable sum.""")
    verdicts = []
    for ep, payload in load_firsttd_payloads():
        settled = ep.endswith(":settled")
        by = defaultdict(list)
        for m in payload.get("markets") or []:
            by[m.get("event_ticker")].append(m)
        print(f"\n  source {ep}  ({'settled - composition only, tradeability unknowable' if settled else 'open - composition AND tradeability'})")
        print(f"    {'event':<32}{'legs':>5}{'player':>7}{'dst':>5}{'no-td':>6}"
              f"{'complete':>10}{'a leg won':>11}")
        for ev, legs in sorted(by.items()):
            ok, kinds, problems = partition_completeness(legs, not settled)
            proof = settlement_proof(legs) if settled else None
            verdicts.append(ok)
            print(f"    {ev[-30:]:<32}{len(legs):>5}{kinds.get('player',0):>7}"
                  f"{kinds.get('dst',0):>5}{kinds.get('no-td',0):>6}{str(ok):>10}"
                  f"{('-' if proof is None else str(proof)):>11}")
            if problems:
                print(f"      {'; '.join(problems)}")
    n_ok = sum(verdicts)
    print(f"""
  VERDICT: {len(verdicts) - n_ok} of {len(verdicts)} listed first-TD-scorer events are INCOMPLETE.""")
    if n_ok:
        print(f"""  The {n_ok} that pass on composition have no logged pre-game book, so neither
  their tradeability nor their sum can be measured here.""")
    print("""
  Per the brief, the finding is the incompleteness, and the test stops: H2 is
  NOT TESTED - not supported, not refuted. Testing it properly means adding
  KXNFLFIRSTTD to the logger's allowlist for a slate AND only scoring events
  that list a no-touchdown leg.""")
    return verdicts


# =============================================================================
# item 3 - spread capture per series
# =============================================================================

CAPTURE_SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD", "KXNFLTOTAL",
                  "KXNFLGAME")


def correlation(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy) if vx and vy else float("nan")


def report_capture(ticket=100):
    _hdr("ITEM 3 - BOTH-SIDES SPREAD CAPTURE, PER SERIES  (H3)")
    from research import maker
    print("""
  The 018 both-sides maker simulation, run separately on each series in the
  same 14 events, from the same entry instant, sitting behind the same queue.
  No view at all: rest on yes AND no. The maker fee comes from each market's
  series (item 0): KXNFLREC and KXNFLRSHATT pay nothing; the game-level series
  pay 1.75*p*(1-p) per contract, billed on the order.

  STANDING PREDICTION, recorded in the brief before this ran: capture is WORSE
  on the tighter, deeper series.""")
    results = []
    for s in CAPTURE_SERIES:
        rows = maker.load_control(2026, 1, ticket, series=(s,))
        sims = [r for r in rows if r["sim"]]
        if not sims:
            print(f"\n  {s}: no simulable markets")
            continue
        five = maker._five(rows, s)
        sp = [100 * (r["entry_ask"] - r["entry_bid"]) for r in sims]
        maker_m, _ = series_multiplier(s)
        results.append((s, five, statistics.median(sp), maker_m))
    print(f"\n    {'series':<13}{'mkts':>5}{'obs':>6}{'spread c':>9}{'maker M':>8}"
          f"{'fill rate':>22}{'conditional pp':>24}{'never filled pp':>24}{'gap pp':>24}")
    for s, f, sp, mm in results:
        rate = (f"{f['rate']['mean']:.3f} [{f['rate']['lo']:.3f},{f['rate']['hi']:.3f}]"
                if f["rate"] else "n/a")
        print(f"    {s:<13}{f['markets']:>5}{f['n']:>6}{sp:>9.1f}{str(mm):>8}{rate:>22}"
              f"{maker._pp(f['cond']):>24}{maker._pp(f['never']):>24}{maker._pp(f['gap']):>24}")
    pts = [(sp, f["cond"]["mean"]) for s, f, sp, _ in results if f["cond"]]
    if len(pts) >= 3:
        r = correlation([a for a, _ in pts], [b for _, b in pts])
        held = r > 0
        print(f"""
  PREDICTION CHECK across {len(pts)} series: correlation of median entry spread with
  conditional capture is {r:+.2f}. "Tighter captures worse" means wider spread goes
  with MORE capture, i.e. a POSITIVE correlation.

  The standing prediction {'HELD' if held else 'DID NOT HOLD'} on this sample. {len(pts)} points is an
  ordering, not a test, and every per-series interval above is wide enough to
  reverse it next week.""")
    return results


def report_count(partition_run, n_capture):
    _hdr("TEST COUNT - for correcting any statistical claim above")
    intervals = n_capture * 4
    total = 1 + (1 if partition_run else 0) + intervals + 1
    print(f"""
  pre-registered hypotheses            3   (H1, H2, H3)
  H1  monotonicity                     1 existence test across {len(LADDER_SERIES)} ladder series
                                       (clustering is descriptive, not tested)
  H2  partition sums                   {'1' if partition_run else '0 - not run: the partition is incomplete'}
  H3  spread capture                   {n_capture} series x 4 intervals = {intervals}
                                       + 1 ordering prediction
  -------------------------------------------------
  statistical outputs                  {total}

  With {intervals} 95% intervals from H3, about {0.05 * intervals:.1f} would exclude zero by chance
  alone. Reading any single one as significant needs ~{100 * (1 - 0.05 / max(intervals, 1)):.2f}%
  coverage under Bonferroni, not 95%.""")


CANDIDATES = (
    ("in-game ladder latency",
     "95% of monotonicity episodes happen in the four hours after kickoff, when "
     "rungs of one ladder update out of step. At a 10s poll this is a latency "
     "race nobody polling REST can win or even measure - it needs a websocket "
     "feed before it is a hypothesis."),
    ("division and conference partitions",
     "KXNFLAFCEAST..NFCWEST (4 legs) and KXNFLAFCCHAMP/NFCCHAMP (16 legs) are "
     "mutually exclusive AND exhaustive by construction - exactly one team wins "
     "- AND they are already on the allowlist with logged history. The one "
     "partition-sum test this data can actually run."),
    ("team first-TD partition",
     "KXNFLFIRSTTDTEAM lists both teams plus 'no team scores a TD' - a genuine "
     "3-way partition. Untracked, so no history."),
    ("TD count ladders",
     "KXNFLTD (1+/2+/3+ per player) and KXNFLANYTD are ladders the monotonicity "
     "scan never saw, because neither is on the allowlist."),
    ("spread cross-team constraint",
     "P(A wins by > L) + P(B wins by > L') <= 1 across the two team ladders of "
     "one spread event. A different inequality from within-ladder "
     "monotonicity, and not part of H1."),
    ("moneyline has no tie leg",
     "KXNFLGAME is two-way in a sport that ties. Its ask sum below 1 may price "
     "the tie rather than an arbitrage, so it is not a complete partition."),
    ("fee-table drift check",
     "The /series payload carries fee_type per series. A startup check that "
     "core/fees.SERIES_M agrees with the latest archived payload would have "
     "caught SPREAD and TOTAL a brief earlier."),
)


def report_candidates():
    _hdr("CANDIDATES FOR THE NEXT BRIEF - noticed, written down, NOT chased")
    print("""
  The pre-registration forbids adding a hypothesis after seeing a result.
  These turned up along the way and are recorded so that none of them quietly
  becomes a fourth test inside this brief.""")
    for i, (name, why) in enumerate(CANDIDATES, 1):
        print(f"\n  C{i}  {name}")
        words, line = why.split(), "      "
        for w in words:
            if len(line) + len(w) > 78:
                print(line)
                line = "      "
            line += w + " "
        print(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for f in ("catalogue", "monotonicity", "partition", "capture",
              "candidates", "all"):
        ap.add_argument(f"--{f}", action="store_true")
    a = ap.parse_args()
    if a.candidates:
        report_candidates()
        return
    if not (a.catalogue or a.monotonicity or a.partition or a.capture):
        a.all = True
    if a.catalogue or a.all:
        report_catalogue()
    if a.monotonicity or a.all:
        report_monotonicity()
    # H2's sum test has no code path to run: report_partition stops at
    # completeness, and even a complete event has no logged book to sum. So the
    # count is zero by construction, not by default - change it only alongside
    # a sum test that actually executes.
    ran_partition = False
    if a.partition or a.all:
        report_partition()
    res = []
    if a.capture or a.all:
        res = report_capture()
    if a.all:
        report_count(ran_partition, len(res))


if __name__ == "__main__":
    main()
