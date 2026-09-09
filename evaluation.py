"""Scoreboard: Brier, calibration, CLV.

    python -m evaluation --season 2026 --week 1

These are near-meaningless on one week. They are built now so that Week 2 has
somewhere to land, and so the shape of the answer is fixed before anyone has an
opinion about what it should say.

The brand promise is that the calibration page ships early and stays honest,
including when it looks bad - which is much easier to hold to if the harness
predates the first result rather than being written once the numbers are known.

NOTHING HERE PUBLISHES. Model-vs-market disagreements are only publishable
after settlement, and a disagreement is not a finding until it has been scored.
"""
import argparse
import sqlite3

import config

# Calibration buckets. Ten is conventional and, at one week of data, every one
# of them will be empty or hold three observations. That is the point: the
# harness should make the thinness obvious rather than smooth it away.
BUCKETS = [(i / 10, (i + 1) / 10) for i in range(10)]


def scored_rows(con, season=None, week=None, model_version=None):
    """(predicted_prob, realized_hit) for every settled prediction.

    `realized` is 1 when the outcome went over, 0 under. Pushes are DROPPED,
    not scored as a half: a push is a returned stake, and scoring it as 0.5
    would quietly reward a model for landing exactly on a line it never took a
    position against.
    """
    q = """
        SELECT p.prob_over, s.result, p.model_version, o.stat
          FROM predictions p
          JOIN outcome_settlement s ON s.outcome_id = p.outcome_id
          JOIN outcomes o ON o.outcome_id = p.outcome_id
         WHERE s.result IN ('over', 'under')
           AND p.as_of_ts = (SELECT MAX(as_of_ts) FROM predictions
                              WHERE outcome_id = p.outcome_id
                                AND model_version = p.model_version)
    """
    args = []
    if season:
        q += " AND o.season = ?"
        args.append(season)
    if week:
        q += " AND o.week = ?"
        args.append(week)
    if model_version:
        q += " AND p.model_version = ?"
        args.append(model_version)
    return [(prob, 1 if res == "over" else 0, mv, stat)
            for prob, res, mv, stat in con.execute(q, args)]


def brier(rows):
    """Mean squared error of a probability. Lower is better; 0.25 is a coin."""
    if not rows:
        return None
    return sum((p - y) ** 2 for p, y, _, _ in rows) / len(rows)


def calibration(rows, buckets=BUCKETS):
    """Predicted-probability bucket vs realized hit rate.

    The single most important plot this project will publish, and the one most
    easily faked by choosing buckets after seeing the data - so the buckets are
    fixed here, in advance, and never tuned.
    """
    out = []
    for lo, hi in buckets:
        b = [(p, y) for p, y, _, _ in rows if lo <= p < hi]
        out.append({
            "lo": lo, "hi": hi, "n": len(b),
            "predicted": sum(p for p, _ in b) / len(b) if b else None,
            "realized": sum(y for _, y in b) / len(b) if b else None,
        })
    return out


def clv(con, model_version=None):
    """Closing-line value: entry price vs the last price before kickoff.

    CLV is the only feedback available before results accumulate, and it is
    available on every ticket rather than only on the ones that settled. It is
    also the honest early metric: beating the close is evidence, winning one
    week is not.
    """
    rows = con.execute(
        """SELECT l.ticket_id, l.side, l.market_prob, l.venue, l.market_id,
                  l.entry_ts, l.kickoff_ts, p.model_version
             FROM paper_ledger l JOIN predictions p USING (prediction_id)"""
    ).fetchall()
    out = []
    for tid, side, entry_p, venue, market_id, entry_ts, kickoff, mv in rows:
        if model_version and mv != model_version:
            continue
        if not kickoff:
            continue
        close = con.execute(
            """SELECT mid FROM quotes
                WHERE venue = ? AND market_id = ? AND mid IS NOT NULL
                  AND ts < ? AND ts > ?
                ORDER BY ts DESC LIMIT 1""",
            (venue, market_id, kickoff, entry_ts)).fetchone()
        if not close:
            continue                       # no later price yet; not a zero
        close_yes = close[0]
        close_p = close_yes if side == "yes" else 1.0 - close_yes
        out.append({"ticket_id": tid, "entry": entry_p, "close": close_p,
                    "clv": close_p - entry_p})
    return out


def report(season=None, week=None, model_version=None):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    rows = scored_rows(con, season, week, model_version)

    print(f"settled predictions scored: {len(rows):,}")
    if rows:
        b = brier(rows)
        base = sum(y for _, y, _, _ in rows) / len(rows)
        print(f"Brier            : {b:.4f}")
        print(f"  always-base-rate: {base * (1 - base):.4f}  (base rate {base:.3f})")
        print(f"  always-0.5      : 0.2500")
    else:
        print("  nothing has settled yet - Week 1 has not been played.")
        print("  Brier and calibration stay empty until it has; they are not")
        print("  zero, and an empty scoreboard must not read as a good one.")

    print()
    print("calibration")
    print(f"  {'bucket':<12} {'n':>5} {'predicted':>10} {'realized':>9}")
    for c in calibration(rows):
        pred = f"{c['predicted']:.3f}" if c["predicted"] is not None else "-"
        real = f"{c['realized']:.3f}" if c["realized"] is not None else "-"
        print(f"  {c['lo']:.1f}-{c['hi']:.1f}      {c['n']:>5} {pred:>10} {real:>9}")

    print()
    c = clv(con, model_version)
    if c:
        avg = sum(x["clv"] for x in c) / len(c)
        beat = sum(1 for x in c if x["clv"] > 0)
        print(f"CLV              : {len(c):,} tickets with a later price")
        print(f"  mean CLV       : {avg:+.4f}")
        print(f"  beat the close : {beat}/{len(c)} ({beat / len(c):.1%})")
    else:
        print("CLV              : no ticket has a later price yet")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--model-version", dest="model_version")
    args = ap.parse_args()
    report(args.season, args.week, args.model_version)


if __name__ == "__main__":
    main()
