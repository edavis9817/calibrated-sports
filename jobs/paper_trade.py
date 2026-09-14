"""Price predictions against the market and record a flat-stake paper ledger.

    python -m jobs.paper_trade --season 2026 --week 1
    python -m jobs.paper_trade --report

Three things here are easy to get wrong and expensive:

1. KALSHI PRICE IS ALREADY A PROBABILITY. Dollars, settles at $1. Do not
   divide by 100 - that was the brief 001 bug and it reads as "nobody trades
   this market" rather than as an error.

2. THE MID IS NOT A PRICE. It is the midpoint of a spread you would have to
   cross. A 2-cent edge inside a 6-cent spread is not an edge; it is a rounding
   artifact of a market nobody is quoting tightly. The spread is recorded on
   every ticket so the backtest can tell the difference later, and wide markets
   are filtered out before a ticket is written at all.

3. EDGE MUST CLEAR THE FEE. Kalshi's taker fee is 0.07*C*P*(1-P), which peaks
   at P=0.5 and collapses at the tails. That shape changes which side of a
   market is worth taking: a 3-cent edge on a coin flip is gone after fees, and
   the same 3 cents at P=0.08 survives comfortably.

FLAT STAKES ONLY. QB<->WR1 is ~0.42, so a diagonal covariance systematically
over-bets a correlated slate; Kelly waits for the sizing brief.
"""
import argparse
import sqlite3
import time

import config
import store
from core.fees import edge_after_fees, fee_per_contract, series_multiplier
from models import baseline

STAKE = 100.0                 # contracts per ticket, flat, always
MIN_PRIOR_GAMES = 4           # below this the "edge" is model variance
MAX_SPREAD = 0.06             # wider than this and the mid is not tradeable
MIN_NET_EDGE = 0.02           # after fees, per contract, in dollars


def latest_quote(con, venue, market_id, as_of_ts):
    return con.execute(
        """SELECT best_bid, best_ask, mid, ts FROM quotes
            WHERE venue = ? AND market_id = ? AND ts <= ?
              AND best_bid IS NOT NULL AND best_ask IS NOT NULL
            ORDER BY ts DESC LIMIT 1""",
        (venue, market_id, as_of_ts)).fetchone()


def evaluate(model_prob_yes: float, bid: float, ask: float,
             contracts: float = None, market_id: str = None):
    """Pick a side and price it. Pure: no database, so the fee arithmetic can
    be tested directly.

    `gross_edge` IS POSITIVE BY CONSTRUCTION and is not a measure of anything.
    The side is chosen so the model is above the market, so gross_edge is
    |model - market| and a model of pure noise scores high on it. Averaging it
    over tickets that were then filtered to >= MIN_NET_EDGE reported 13-18pp
    and was read as an edge for two sessions. The number that means something
    is `signed_edge` below: model_prob_yes - mid, on a fixed side, which can be
    negative and averages to zero for a calibrated model.

    Every consumer that reports an aggregate must use `signed_edge`.
    `gross_edge` and `net_edge` describe ONE ticket's economics - what it would
    cost and return - and are meaningless averaged across tickets.
    """
    mid = (bid + ask) / 2.0
    spread = ask - bid
    # Take the side the model disagrees with the market about. The fee is a
    # function of the PRICE PAID, so it differs between the two sides and has
    # to be computed on the side actually taken.
    if model_prob_yes >= mid:
        side, model_p, market_p = "yes", model_prob_yes, mid
    else:
        side, model_p, market_p = "no", 1.0 - model_prob_yes, 1.0 - mid
    gross = model_p - market_p
    # THE FEE IS ON THE WHOLE ORDER, so it needs the real ticket size. Passing
    # 1 here billed the ceil-to-cent to every contract and overstated the fee
    # ~2.4x, which made every `net_edge` in the ledger too pessimistic.
    # Reported PER CONTRACT, because that is the unit `gross_edge` is in.
    c = int(contracts or STAKE)
    # A taker order pays the series multiplier, which defaults to 1 and is 1
    # for every football series in the schedule's table anyway. Resolving it
    # from the ticker keeps the one series that differs (KXMVE) honest.
    _, taker_m = series_multiplier(market_id) if market_id else (None, None)
    fee = fee_per_contract(market_p, c, "taker", taker_m)
    net = edge_after_fees(model_p, market_p, c, "taker", taker_m)
    return {"side": side, "model_prob": model_p, "market_prob": market_p,
            "mid": mid, "spread": spread, "gross_edge": gross, "fee": fee,
            "net_edge": net,
            # ALWAYS on the yes axis, whichever side the ticket takes. This is
            # the only column in this dict that can be averaged.
            "signed_edge": model_prob_yes - mid}


def run(season=2026, week=1, as_of_ts=None, venue="kalshi", dry_run=False,
        model_version=None):
    # Only ever price the CURRENT model. Superseded versions stay in the
    # predictions table by design and must not leak into a live ledger.
    model_version = model_version or baseline.MODEL_VERSION
    as_of_ts = as_of_ts or time.time()
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT p.prediction_id, p.outcome_id, p.prob_over, p.prior_games,
                  mo.market_id, g.kickoff_ts
             FROM predictions p
             JOIN outcomes o USING (outcome_id)
             JOIN market_outcome mo ON mo.outcome_id = p.outcome_id
             LEFT JOIN (SELECT game_id, MAX(kickoff_ts) kickoff_ts
                          FROM nfl_games GROUP BY game_id) g
               ON g.game_id = o.event_id
            WHERE mo.venue = ? AND o.season = ? AND o.week = ?
              AND p.model_version = ?
              AND p.as_of_ts = (SELECT MAX(as_of_ts) FROM predictions
                                 WHERE outcome_id = p.outcome_id
                                   AND model_version = ?)""",
        (venue, season, week, model_version, model_version)).fetchall()

    stats = {"considered": 0, "no_quote": 0, "thin_history": 0,
             "wide_spread": 0, "no_edge": 0, "written": 0, "post_kickoff": 0}
    for pid, oid, prob_yes, prior_games, market_id, kickoff in rows:
        stats["considered"] += 1
        if kickoff is not None and as_of_ts >= kickoff:
            stats["post_kickoff"] += 1
            continue
        q = latest_quote(con, venue, market_id, as_of_ts)
        if not q:
            stats["no_quote"] += 1
            continue
        bid, ask, _mid, _ts = q
        if (prior_games or 0) < MIN_PRIOR_GAMES:
            stats["thin_history"] += 1
            continue
        ev = evaluate(prob_yes, bid, ask, STAKE, market_id)
        if ev["spread"] > MAX_SPREAD:
            stats["wide_spread"] += 1
            continue
        if ev["net_edge"] < MIN_NET_EDGE:
            stats["no_edge"] += 1
            continue
        stats["written"] += 1
        if dry_run:
            continue
        store.record_ticket({
            "outcome_id": oid, "prediction_id": pid, "venue": venue,
            "market_id": market_id, "side": ev["side"],
            "model_prob": ev["model_prob"], "market_prob": ev["market_prob"],
            "best_bid": bid, "best_ask": ask, "spread": ev["spread"],
            "gross_edge": ev["gross_edge"], "fee": ev["fee"],
            "net_edge": ev["net_edge"], "signed_edge": ev["signed_edge"],
            "stake": STAKE,
            "entry_ts": as_of_ts, "kickoff_ts": kickoff,
            "model_version": model_version,
        })
    con.close()
    store.record_health("paper_ledger", True,
                        ", ".join(f"{k}={v}" for k, v in stats.items()),
                        watermark=as_of_ts)
    return stats


UNSELECTED_SQL = """
SELECT o.stat, p.prob_over, p.prior_games, mo.market_id
  FROM predictions p
  JOIN market_outcome mo ON mo.outcome_id = p.outcome_id
  JOIN outcomes o        ON o.outcome_id  = p.outcome_id
 WHERE p.model_version = ? AND mo.venue = ? AND p.prob_over IS NOT NULL
"""


def unselected(model_version=None, venue="kalshi", as_of_ts=None):
    """Signed model-vs-market bias over EVERY priced outcome, per stat.

    THE REPORT THAT WOULD HAVE CAUGHT IT. Ledger tickets are what survived
    `net_edge >= MIN_NET_EDGE` on a quantity that is positive by construction,
    so any statistic over them is a property of the filter. A model of pure
    noise produces a large positive average `net_edge` on the selected sample
    and a bias of zero here.

    Nothing is filtered on edge. A wide spread and a thin prior are still
    excluded, because those are the conditions under which the model declines
    to have an opinion at all - not conditions on the opinion's content.
    """
    model_version = model_version or baseline.MODEL_VERSION
    as_of_ts = as_of_ts or time.time()
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    per, skipped = {}, {"no_quote": 0, "wide_spread": 0, "thin_history": 0}
    for stat, prob, prior_games, market_id in con.execute(
            UNSELECTED_SQL, (model_version, venue)):
        q = latest_quote(con, venue, market_id, as_of_ts)
        if not q:
            skipped["no_quote"] += 1
            continue
        bid, ask, mid, _ts = q
        if mid is None:
            skipped["no_quote"] += 1
            continue
        if (ask - bid) > MAX_SPREAD:
            skipped["wide_spread"] += 1
            continue
        if (prior_games or 0) < MIN_PRIOR_GAMES:
            skipped["thin_history"] += 1
            continue
        per.setdefault(stat, []).append(prob - mid)
    con.close()
    return per, skipped


def _print_unselected(per, skipped):
    import math
    print("\nUNSELECTED - every priced outcome, no edge filter")
    print("  This is the number that means something. `net_edge` below is")
    print("  positive by construction and is a property of the filter.")
    print(f"  {'stat':<18}{'n':>7}{'signed':>10}{'se':>9}{'z':>7}"
          f"{'|signed|':>10}")
    allv = []
    for stat, v in sorted(per.items(), key=lambda kv: -len(kv[1])):
        allv.extend(v)
        n = len(v)
        m = sum(v) / n
        sd = (sum((x - m) ** 2 for x in v) / max(n - 1, 1)) ** 0.5
        se = sd / math.sqrt(n) if n else 0.0
        print(f"  {stat:<18}{n:>7,}{m:>+10.4f}{se:>9.4f}"
              f"{(m/se if se else 0):>+7.1f}"
              f"{sum(abs(x) for x in v)/n:>10.4f}")
    if allv:
        n = len(allv)
        m = sum(allv) / n
        sd = (sum((x - m) ** 2 for x in allv) / max(n - 1, 1)) ** 0.5
        se = sd / math.sqrt(n)
        print(f"  {'ALL':<18}{n:>7,}{m:>+10.4f}{se:>9.4f}"
              f"{(m/se if se else 0):>+7.1f}"
              f"{sum(abs(x) for x in allv)/n:>10.4f}")
    print(f"  excluded: " + ", ".join(f"{k}={v}" for k, v in skipped.items())
          + "  (conditions on HAVING an opinion, not on its content)")


def report(model_version=None):
    """Ledger summary for ONE model version.

    Version-scoped on purpose: the table holds tickets from every model that has
    ever run, and averaging a superseded model's tickets into the current one's
    is how a ledger starts flattering itself.
    """
    model_version = model_version or baseline.MODEL_VERSION
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)

    versions = con.execute(
        "SELECT p.model_version, COUNT(*) FROM paper_ledger l "
        "JOIN predictions p USING (prediction_id) GROUP BY 1").fetchall()
    print(f"model         : {model_version}")
    other = [f"{v}={c}" for v, c in versions if v != model_version]
    if other:
        print(f"  superseded versions still in the ledger: {', '.join(other)}")

    row = con.execute(
        """SELECT COUNT(*), SUM(l.stake), AVG(l.net_edge), AVG(l.spread),
                  SUM(l.side='yes'), SUM(l.side='no'), AVG(l.model_prob),
                  AVG(l.market_prob), SUM(l.net_edge * l.stake)
             FROM paper_ledger l JOIN predictions p USING (prediction_id)
            WHERE p.model_version = ?""", (model_version,)).fetchone()
    if not row[0]:
        print("ledger empty for this version")
        con.close()
        return
    print(f"tickets       : {row[0]:,}   ({row[4]} yes / {row[5]} no)")
    print(f"stake         : {row[1]:,.0f} contracts flat, {STAKE:g} each")
    print(f"avg net edge  : {row[2]:+.4f}/contract   notional EV ${row[8]:+,.2f}")
    print(f"avg spread    : {row[3]:.4f}")
    print(f"avg model p   : {row[6]:.3f}   avg market p: {row[7]:.3f}")
    print()
    print(f"{'player':<22} {'stat':<14} {'line':>5} {'side':<4} {'model':>6} "
          f"{'mkt':>6} {'net':>7}")
    for r in con.execute(
            """SELECT x.display_name, o.stat, o.line, l.side, l.model_prob,
                      l.market_prob, l.net_edge
                 FROM paper_ledger l
                 JOIN predictions p USING (prediction_id)
                 JOIN outcomes o ON o.outcome_id = l.outcome_id
                 LEFT JOIN player_xwalk x ON x.gsis_id = o.entity_id
                WHERE p.model_version = ?
                ORDER BY l.net_edge DESC LIMIT 10""", (model_version,)):
        print(f"{str(r[0])[:22]:<22} {r[1]:<14} {r[2]:>5g} {r[3]:<4} "
              f"{r[4]:>6.3f} {r[5]:>6.3f} {r[6]:>+7.3f}")
    print()
    for s, c, e in con.execute(
            """SELECT o.stat, COUNT(*), AVG(l.net_edge)
                 FROM paper_ledger l JOIN predictions p USING (prediction_id)
                 JOIN outcomes o ON o.outcome_id = l.outcome_id
                WHERE p.model_version = ? GROUP BY 1 ORDER BY 2 DESC""",
            (model_version,)):
        print(f"  {s:<16} {c:>4} tickets   avg net {e:+.3f}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--venue", default="kalshi")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--unselected", action="store_true",
                    help="signed bias over every priced outcome, no filter")
    args = ap.parse_args()

    store.init_db()
    if args.unselected:
        _print_unselected(*unselected(venue=args.venue))
        return
    if args.report:
        report()
        _print_unselected(*unselected(venue=args.venue))
        return
    s = run(args.season, args.week, venue=args.venue, dry_run=args.dry_run)
    print(" ".join(f"{k}={v}" for k, v in s.items()))
    print()
    report()
    # ALWAYS printed alongside the ledger, never on request. The selected
    # sample is what got believed for two sessions; it does not get to appear
    # on its own again.
    _print_unselected(*unselected(venue=args.venue))


if __name__ == "__main__":
    main()
