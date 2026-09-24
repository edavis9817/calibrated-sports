"""Is the market's closing price the probability it claims to be?

    python -m research.calibration --build       # closing consensus per outcome
    python -m research.calibration               # the curve
    python -m research.calibration --skew        # the under skew, isolated
    python -m research.calibration --trust       # the data-trust checks
    python -m research.calibration --export      # CSVs
    python -m research.calibration --register    # the register figure, read-only

This is the scoreboard the whole repo exists to keep honest. Every number below
is measured on SETTLED outcomes only: a claim whose truth is known from
nflverse, priced by books that did not know it yet.

Three things it is careful about, because each of them can manufacture a
result:

1. CLOSING means the last snapshot at or before kickoff. Not the last snapshot.
   A price stamped after the whistle is not a forecast.

2. Over and under are SEPARATE bettable claims and both are counted. That makes
   the aggregate curve near-symmetric by construction, so a "well calibrated"
   headline proves less than it looks. Every table therefore also reports the
   over side alone, where an asymmetry cannot hide inside its own complement.

3. Liquidity is carried, not assumed. These are sportsbook quotes with no size
   attached, so the structural bucket from brief 006 is `unknown` for all of
   them, and saying so is the point. The stratum that DOES carry information
   here is how many books quoted the line and how far apart they were.
"""
import argparse
import csv
import datetime as dt
import math
import os
import sqlite3
import statistics
import time

import config
import store

BENCH_BOOKS = ("draftkings", "fanduel", "betmgm")
SOURCE = "oddsapi_historical"
# From config, not from a literal: "data/exports" resolves against the working
# directory, which is the repo on C:, while every other store is on D:.
EXPORT_DIR = config.storage_path("exports")

# 0.05 bins. Narrower than a decile because the interesting region for a
# two-sided prop is 0.40-0.60 and a decile smears it into one number.
EDGES = [i / 20 for i in range(21)]


def _bin(p):
    if p is None:
        return None
    i = min(int(p * 20), 19)
    return "%.2f-%.2f" % (EDGES[i], EDGES[i + 1])


def _ro():
    return sqlite3.connect("file:%s?mode=ro" % config.DB_PATH, uri=True)


# --------------------------------------------------------------------------
# build: the closing consensus, one row per outcome
# --------------------------------------------------------------------------

_BUILD_SQL = """
WITH oq AS (
  SELECT mo.outcome_id AS oid, q.ts AS ts,
         substr(q.venue, 9) AS book, q.prob_devig AS p
    FROM quotes q
    JOIN market_outcome mo
      ON mo.venue = q.venue AND mo.market_id = q.market_id
   WHERE q.source = ?
     AND q.prob_devig IS NOT NULL
     AND mo.outcome_id IS NOT NULL
),
kick AS (
  SELECT o.outcome_id AS oid, g.kickoff_ts AS k
    FROM outcomes o JOIN nfl_games g ON g.game_id = o.event_id
),
last AS (
  SELECT oq.oid AS oid, MAX(oq.ts) AS ts
    FROM oq JOIN kick ON kick.oid = oq.oid
   WHERE oq.ts <= kick.k
   GROUP BY oq.oid
)
SELECT oq.oid, oq.ts, oq.book, oq.p, kick.k
  FROM oq
  JOIN last ON last.oid = oq.oid AND last.ts = oq.ts
  JOIN kick ON kick.oid = oq.oid
"""


def build():
    """Collapse every de-vigged book quote at the closing snapshot to a median.

    The median is across BOOKS at ONE timestamp. Taking it across a window
    instead would average a Wednesday line with a Sunday line and call the
    result a close.
    """
    con = _ro()
    print("  reading de-vigged quotes...", flush=True)
    rows = con.execute(_BUILD_SQL, (SOURCE,)).fetchall()
    con.close()
    print("  %s closing book-quotes" % format(len(rows), ","), flush=True)

    agg = {}
    for oid, ts, book, p, kick in rows:
        a = agg.get(oid)
        if a is None:
            a = agg[oid] = {"ts": ts, "kick": kick, "bench": [], "all": []}
        a["all"].append(p)
        if book in BENCH_BOOKS:
            a["bench"].append(p)

    now = time.time()
    out = []
    for oid, a in agg.items():
        allp = a["all"]
        out.append((
            oid, a["ts"], a["kick"],
            (a["kick"] - a["ts"]) / 60.0 if a["kick"] else None,
            statistics.median(a["bench"]) if a["bench"] else None,
            len(a["bench"]),
            statistics.median(allp) if allp else None, len(allp),
            (max(allp) - min(allp)) if allp else None,
            now))

    with store.db() as c:
        c.execute("DELETE FROM outcome_close")
        c.executemany(
            "INSERT INTO outcome_close (outcome_id, close_ts, kickoff_ts, "
            "lead_min, p_bench, n_bench, p_all, n_all, dispersion, built_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", out)
    store.record_health("calibration_build", True,
                        "%d outcomes closed from %d quotes" % (len(out),
                                                               len(rows)),
                        watermark=now)
    print("  wrote %s outcome_close rows" % format(len(out), ","))
    return len(out)


# --------------------------------------------------------------------------
# the population every table below is drawn from
# --------------------------------------------------------------------------

_POP = """
SELECT o.outcome_id, o.season, o.week, o.stat, o.line, o.side,
       s.result, s.actual, c.p_bench, c.n_bench, c.p_all, c.n_all,
       c.dispersion, c.lead_min,
       COALESCE((SELECT l.bucket FROM market_outcome mo
                   JOIN market_liquidity l
                     ON l.venue = mo.venue AND l.market_id = mo.market_id
                  WHERE mo.outcome_id = s.outcome_id
                    AND mo.venue NOT LIKE 'oddsapi%' LIMIT 1),
                'unknown') AS bucket
  FROM outcome_settlement s
  JOIN outcomes o      ON o.outcome_id = s.outcome_id
  JOIN outcome_close c ON c.outcome_id = s.outcome_id
 WHERE s.result IN ('over','under')
"""

FIELDS = ("oid", "season", "week", "stat", "line", "side", "result", "actual",
          "p_bench", "n_bench", "p_all", "n_all", "dispersion", "lead_min",
          "bucket")


class Row(object):
    __slots__ = FIELDS

    def __init__(self, t):
        for k, v in zip(FIELDS, t):
            setattr(self, k, v)

    @property
    def hit(self):
        """1 if this side won. The side IS the claim; `result` is the fact."""
        return 1 if self.result == self.side else 0


def population(price="bench"):
    con = _ro()
    rows = [Row(t) for t in con.execute(_POP)]
    con.close()
    key = "p_bench" if price == "bench" else "p_all"
    return [r for r in rows if getattr(r, key) is not None]


# --------------------------------------------------------------------------
# the metrics
# --------------------------------------------------------------------------

def _key(price):
    return "p_bench" if price == "bench" else "p_all"


def score(rows, price="bench"):
    """Brier, log loss and expected calibration error over one slice."""
    k = _key(price)
    n = len(rows)
    if not n:
        return None
    brier = sum((getattr(r, k) - r.hit) ** 2 for r in rows) / n
    ll = 0.0
    for r in rows:
        p = getattr(r, k) if r.hit else 1 - getattr(r, k)
        ll -= math.log(min(max(p, 1e-9), 1 - 1e-9))
    bins = {}
    for r in rows:
        bins.setdefault(_bin(getattr(r, k)), []).append(r)
    ece = sum(len(v) * abs(sum(x.hit for x in v) / len(v)
                           - sum(getattr(x, k) for x in v) / len(v))
              for v in bins.values()) / n
    return {"n": n, "brier": brier, "log_loss": ll / n, "ece": ece,
            "mean_p": sum(getattr(r, k) for r in rows) / n,
            "hit_rate": sum(r.hit for r in rows) / n}


def _se(rows, price):
    """Standard error of the realized rate UNDER THE NULL that the market is
    right. That means the variance comes from the PREDICTED probabilities, not
    from the observed rate:

        Var(sum of wins) = sum p_i (1 - p_i)      ->    se = sqrt(that) / n

    Using the observed rate instead collapses to zero whenever a small bucket
    happens to go 0-for-n or n-for-n, and the z-score explodes. One bucket of
    n=1 printed z = +286,113 before this. The predicted probability is never 0
    or 1, so this form cannot degenerate.
    """
    k = _key(price)
    n = len(rows)
    if not n:
        return 0.0
    var = sum(getattr(r, k) * (1 - getattr(r, k)) for r in rows)
    return math.sqrt(var) / n


def curve(rows, price="bench"):
    """The bucket table. `se` is the standard error of the realized rate under
    the null - without it a 2-point gap on n=180 reads like a finding."""
    k = _key(price)
    bins = {}
    for r in rows:
        bins.setdefault(_bin(getattr(r, k)), []).append(r)
    out = []
    for b in sorted(bins):
        v = bins[b]
        n = len(v)
        pred = sum(getattr(x, k) for x in v) / n
        real = sum(x.hit for x in v) / n
        se = _se(v, price)
        out.append({"bucket": b, "n": n, "predicted": pred, "realized": real,
                    "diff": real - pred, "se": se,
                    "z": (real - pred) / se if se else 0.0})
    return out


def _print_curve(title, rows, price="bench"):
    c = curve(rows, price)
    s = score(rows, price)
    if not s:
        print("\n%s   (no rows)" % title)
        return [], None
    print("\n%s   (n=%s  brier=%.4f  logloss=%.4f  ece=%.4f)"
          % (title, format(s["n"], ","), s["brier"], s["log_loss"], s["ece"]))
    print("  %-14s%8s%11s%10s%9s%8s%8s"
          % ("price bucket", "n", "predicted", "realized", "diff", "se", "z"))
    for b in c:
        star = " *" if abs(b["z"]) > 3 and b["n"] >= 100 else ""
        print("  %-14s%8s%11.4f%10.4f%+9.4f%8.4f%+8.1f%s"
              % (b["bucket"], format(b["n"], ","), b["predicted"],
                 b["realized"], b["diff"], b["se"], b["z"], star))
    return c, s


def _print_slices(title, rows, keyfn, price="bench", minimum=200):
    """Slices are reported on the OVER SIDE ONLY.

    Every line produces two outcomes that are exact complements, so across both
    sides the mean price and the hit rate are 0.5000 in every slice, always, by
    construction - a table of identical numbers that reads as perfect
    calibration and is arithmetic. Brier and ECE do still vary, but the two
    columns a reader looks at first do not.
    """
    over = [r for r in rows if r.side == "over"]
    print("\n%s   [over side, n=%s]" % (title, format(len(over), ",")))
    print("  %-22s%9s%11s%10s%9s%7s%9s%8s"
          % ("slice", "n", "mean price", "hit rate", "diff", "z", "brier",
             "ece"))
    groups = {}
    for r in over:
        groups.setdefault(keyfn(r), []).append(r)
    for g in sorted(groups, key=lambda x: -len(groups[x])):
        v = groups[g]
        if len(v) < minimum:
            continue
        s = score(v, price)
        se = _se(v, price)
        d = s["hit_rate"] - s["mean_p"]
        print("  %-22s%9s%11.4f%10.4f%+9.4f%+7.1f%9.4f%9.4f"
              % (str(g)[:22], format(s["n"], ","), s["mean_p"],
                 s["hit_rate"], d, d / se if se else 0.0, s["brier"],
                 s["ece"]))
    return groups


# --------------------------------------------------------------------------
# the under skew
# --------------------------------------------------------------------------

def skew(rows, price="bench", lo=0.45, hi=0.55):
    """Settled unders outnumber settled overs 101,111 to 85,681 - 54.1%. The
    question is whether that is a PRICING error or a LINE-PLACEMENT fact.

    Those are different claims and only one of them is tradeable:

      line placement  the book hangs the line near the MEAN of a right-skewed
                      distribution, so the median result falls below it and the
                      under wins more than half the time. The price already
                      says so. Nothing to bet.

      pricing error   at a given price the under wins MORE OFTEN than that
                      price implies. That is money.

    Restricting to prices in [0.45, 0.55] isolates the second: inside that band
    the two sides are near-equally priced, so any residual asymmetry cannot be
    explained by the book having told you which side was favoured.
    """
    k = _key(price)
    overs = [r for r in rows if r.side == "over"]
    band = [r for r in overs if lo <= getattr(r, k) <= hi]

    def blk(v):
        if not v:
            return None
        n = len(v)
        po = sum(getattr(r, k) for r in v) / n
        ro = sum(r.hit for r in v) / n
        se = _se(v, price)
        return {"n": n, "priced_over": po, "realized_over": ro,
                "priced_under": 1 - po, "realized_under": 1 - ro,
                "diff": ro - po, "se": se, "z": (ro - po) / se if se else 0.0,
                # How far the actual result sat from the line, in stat units.
                # This is the line-placement axis, independent of any price.
                "median_margin": statistics.median(
                    [r.actual - r.line for r in v if r.actual is not None
                     and r.line is not None] or [float("nan")]),
                "mean_margin": statistics.fmean(
                    [r.actual - r.line for r in v if r.actual is not None
                     and r.line is not None] or [float("nan")])}
    return {"all": blk(overs), "band": blk(band), "lo": lo, "hi": hi}


def _print_tradeability(b):
    """An edge is only an edge after the cost of taking it.

    The skew below is measured on a price with the vig REMOVED, and nobody
    trades that price. Comparing it to what a fill actually costs is the whole
    difference between a finding and a bet - the same check brief 004 forced on
    the model side, applied to the market side.
    """
    from core.fees import fee_per_contract
    if not b:
        return
    edge = -b["diff"]                       # the UNDER is the underpriced side
    p = b["priced_under"]
    # Per contract AT SIZE. kalshi_fee rounds up to whole cents, so on one
    # contract a 1.75c taker fee bills 2c and a 0.44c maker fee bills 1c - a
    # 14% and 128% overstatement that lands on exactly the coin-flip prices
    # this section is about. Divide a block fee instead.
    size = 1000
    taker = fee_per_contract(p, size, "taker")
    # Brief 016: explicit multiplier=1 keeps this published number stable.
    # Unlisted series (every NFL prop) are maker M=0 under the defaults.
    maker = fee_per_contract(p, size, "maker", multiplier=1)
    # A sportsbook charges the vig rather than a fee. Half the overround is
    # what one side of a two-way market pays; the measured median overround on
    # this archive is 1.0675, so a side pays about 3.4 cents.
    vig_side = 0.0675 / 2
    print("\nWhat it would cost to take that edge:")
    print("  measured under-side edge at the close   %+.4f per contract" % edge)
    for label, cost in (("Kalshi taker fee at P=%.2f, per contract" % p, taker),
                        ("Kalshi maker fee at P=%.2f, per contract" % p, maker),
                        ("a sportsbook, half a 1.0675 overround", vig_side)):
        print("  %-40s %.4f   %s"
              % (label, cost, "CLEARS" if edge > cost else "does NOT clear"))
    print("  The skew is REAL and mostly UNTRADEABLE: smaller than the vig at"
          "\n  the books that produced it, and smaller than a Kalshi taker fee."
          "\n  It clears a maker fill only - on a venue that quotes these props,"
          "\n  which is a separate claim needing its own evidence. Nothing here"
          "\n  is a bet.")


def _print_skew_block(label, b):
    if not b:
        print("  %-24s (no rows)" % label)
        return
    print("  %-24s n=%-9s priced over %.4f  realized over %.4f  "
          "diff %+.4f (z %+.1f)  margin med %+.2f mean %+.2f"
          % (label, format(b["n"], ","), b["priced_over"], b["realized_over"],
             b["diff"], b["z"], b["median_margin"], b["mean_margin"]))


def report_skew(rows, price="bench"):
    print("\n" + "=" * 78)
    print("THE UNDER SKEW")
    print("=" * 78)
    con = _ro()
    tot = dict(con.execute(
        "SELECT result, COUNT(*) FROM outcome_settlement GROUP BY 1"))
    con.close()
    n_ou = tot.get("over", 0) + tot.get("under", 0)
    print("\nraw settlement, every settled outcome in the store:")
    print("  under %s   over %s   push %s   -> %.2f%% under"
          % (format(tot.get("under", 0), ","), format(tot.get("over", 0), ","),
             format(tot.get("push", 0), ","),
             100.0 * tot.get("under", 0) / n_ou if n_ou else 0.0))
    print("  (that counts each LINE twice, once per side; the rate below is the"
          "\n   same fact stated once, on the over side only)")

    s = skew(rows, price)
    print("\nover side only, priced vs realized:")
    _print_skew_block("all priced outcomes", s["all"])
    _print_skew_block("priced %.2f-%.2f" % (s["lo"], s["hi"]), s["band"])

    k = _key(price)
    band = [r for r in rows if r.side == "over"
            and s["lo"] <= getattr(r, k) <= s["hi"]]
    print("\ninside the band, by stat:")
    print("  %-20s%9s%12s%12s%9s%7s%9s%9s"
          % ("stat", "n", "priced over", "real over", "diff", "z",
             "med marg", "mean marg"))
    rows_out = []
    by = {}
    for r in band:
        by.setdefault(r.stat, []).append(r)
    for st in sorted(by, key=lambda x: -len(by[x])):
        b = skew(by[st], price, s["lo"], s["hi"])["all"]
        if b["n"] < 100:
            continue
        print("  %-20s%9s%12.4f%12.4f%+9.4f%+7.1f%+9.2f%+9.2f"
              % (st, format(b["n"], ","), b["priced_over"], b["realized_over"],
                 b["diff"], b["z"], b["median_margin"], b["mean_margin"]))
        rows_out.append(dict(b, stat=st, season="all", slice="band"))

    print("\ninside the band, by season:")
    for sn in sorted({r.season for r in band}):
        v = [r for r in band if r.season == sn]
        b = skew(v, price, s["lo"], s["hi"])["all"]
        if not b or b["n"] < 100:
            continue
        print("  %-20s%9s%12.4f%12.4f%+9.4f%+7.1f%+9.2f%+9.2f"
              % (sn, format(b["n"], ","), b["priced_over"],
                 b["realized_over"], b["diff"], b["z"], b["median_margin"],
                 b["mean_margin"]))
        rows_out.append(dict(b, stat="all", season=sn, slice="band"))

    _print_tradeability(s["band"])

    print("\nWHOLE population by stat, for contrast (line placement shows here,"
          "\nnot in the band):")
    print("  %-20s%9s%12s%12s%9s%7s%9s"
          % ("stat", "n", "priced over", "real over", "diff", "z", "med marg"))
    byall = {}
    for r in rows:
        if r.side == "over":
            byall.setdefault(r.stat, []).append(r)
    for st in sorted(byall, key=lambda x: -len(byall[x])):
        b = skew(byall[st], price)["all"]
        if b["n"] < 200:
            continue
        print("  %-20s%9s%12.4f%12.4f%+9.4f%+7.1f%+9.2f"
              % (st, format(b["n"], ","), b["priced_over"], b["realized_over"],
                 b["diff"], b["z"], b["median_margin"]))
        rows_out.append(dict(b, stat=st, season="all", slice="all"))
    return s, rows_out


# --------------------------------------------------------------------------
# trust checks
# --------------------------------------------------------------------------

def _tbl(title, header, rows, widths=None):
    print("\n%s" % title)
    if not rows:
        print("  (none)")
        return
    widths = widths or [max(len(str(h)), 10) for h in header]
    print("  " + "".join(str(h).ljust(w) for h, w in zip(header, widths)))
    for r in rows:
        print("  " + "".join(str(x).ljust(w) for x, w in zip(r, widths)))


_UNSETTLED_WHY = """
SELECT CASE
    WHEN o.season >= 2026 THEN 'a. season not played yet'
    WHEN o.stat NOT IN (SELECT DISTINCT o2.stat FROM outcomes o2
                          JOIN outcome_settlement s2
                            ON s2.outcome_id = o2.outcome_id)
         THEN 'b. stat has no settlement rule'
    WHEN NOT EXISTS (SELECT 1 FROM nfl_player_week w
                      WHERE w.gsis_id = o.entity_id
                        AND w.season = o.season AND w.week = o.week
                        AND w.season_type = 'REG')
         THEN 'c. no stat line that week (inactive / DNP / wrong week)'
    WHEN o.line IS NULL THEN 'd. no line on the outcome'
    ELSE 'e. unexplained' END AS why,
    COUNT(*)
  FROM outcomes o
 WHERE o.entity_type = 'player'
   AND NOT EXISTS (SELECT 1 FROM outcome_settlement s
                    WHERE s.outcome_id = o.outcome_id)
 GROUP BY 1 ORDER BY 2 DESC
"""

_COLLISIONS = """
SELECT o.entity_type, COUNT(*) FROM (
  SELECT mo.outcome_id AS oid,
         COUNT(DISTINCT substr(mo.market_id, 1,
                               instr(mo.market_id, '|') - 1)) AS g
    FROM market_outcome mo
   WHERE mo.outcome_id IS NOT NULL AND mo.venue LIKE 'oddsapi:%'
   GROUP BY 1 HAVING g > 1) x
JOIN outcomes o ON o.outcome_id = x.oid
GROUP BY 1 ORDER BY 2 DESC
"""


def trust():
    con = _ro()

    def q(s, *a):
        return con.execute(s, a).fetchall()

    print("\n" + "=" * 78)
    print("TRUST CHECK 1 - what is NOT settled, and why")
    print("=" * 78)
    tot = q("SELECT COUNT(*) FROM outcomes WHERE entity_type='player'")[0][0]
    # SETTLED means GRADED. A void resolved - the bet did not run - but it has
    # no side and scores nothing, so counting it here would report coverage the
    # curves above do not have (they all filter `result IN ('over','under')`).
    # The NOT EXISTS guards below are already right: a void row exists, so a
    # voided outcome correctly stops being "unsettled".
    got = q("SELECT COUNT(DISTINCT outcome_id) FROM outcome_settlement "
            "WHERE result IN ('over','under','push')")[0][0]
    voided = q("SELECT COUNT(DISTINCT outcome_id) FROM outcome_settlement "
               "WHERE result = 'void'")[0][0]
    # Three buckets, not two. A void resolved but scores nothing, so folding it
    # into either "settled" or "unsettled" misreports one of them: it is not
    # coverage the curves have, and it is not a gap in our data either.
    print("\n  player-prop outcomes %s, graded %s (%.2f%%), void %s, unsettled %s"
          % (format(tot, ","), format(got, ","), 100.0 * got / tot,
             format(voided, ","), format(tot - got - voided, ",")))
    _tbl("  composition of the unsettled:", ("cause", "n"),
         [(w, format(n, ",")) for w, n in q(_UNSETTLED_WHY)], [56, 10])
    _tbl("  unsettled by stat, seasons already played:", ("stat", "n"),
         [(s or "(null)", format(n, ",")) for s, n in q(
             """SELECT o.stat, COUNT(*) FROM outcomes o
                 WHERE o.entity_type='player' AND o.season < 2026
                   AND NOT EXISTS (SELECT 1 FROM outcome_settlement s
                                    WHERE s.outcome_id=o.outcome_id)
                 GROUP BY 1 ORDER BY 2 DESC LIMIT 12""")], [26, 10])

    print("\n" + "=" * 78)
    print("TRUST CHECK 2 - is the closing snapshot actually a close?")
    print("=" * 78)
    _tbl("  lead time from snapshot to kickoff (minutes), settled outcomes:",
         ("season", "n", "min", "mean", "max", ">60min"),
         [(s, format(n, ","), "%.1f" % lo, "%.1f" % av, "%.1f" % hi,
           format(bad, ","))
          for s, n, lo, av, hi, bad in q(
              """SELECT o.season, COUNT(*), MIN(c.lead_min), AVG(c.lead_min),
                        MAX(c.lead_min), SUM(c.lead_min > 60)
                   FROM outcome_close c
                   JOIN outcomes o USING(outcome_id)
                   JOIN outcome_settlement s ON s.outcome_id=c.outcome_id
                  GROUP BY 1 ORDER BY 1""")], [10, 12, 10, 12, 14, 10])
    print("  A mean far above the min is the tell: it means some outcomes were"
          "\n  priced at a DIFFERENT game's snapshot.")

    print("\n" + "=" * 78)
    print("TRUST CHECK 3 - outcomes with no de-vigged closing price")
    print("=" * 78)
    _tbl("  settled outcomes excluded from every curve above:",
         ("season", "n"),
         [(s, format(n, ",")) for s, n in q(
             """SELECT o.season, COUNT(*) FROM outcomes o
                  JOIN outcome_settlement s ON s.outcome_id=o.outcome_id
                  LEFT JOIN outcome_close c ON c.outcome_id=o.outcome_id
                 WHERE c.outcome_id IS NULL GROUP BY 1 ORDER BY 1""")],
         [10, 12])
    print("  A de-vig needs BOTH sides from the same book at the same"
          "\n  snapshot. These are the outcomes where no book quoted the pair."
          "\n  They are excluded, not scored as misses.")
    _tbl("  benchmark-book coverage of settled outcomes:",
         ("n_bench", "outcomes"),
         [(nb, format(n, ",")) for nb, n in q(
             """SELECT c.n_bench, COUNT(*) FROM outcome_close c
                  JOIN outcome_settlement s ON s.outcome_id=c.outcome_id
                 GROUP BY 1 ORDER BY 1""")], [10, 12])

    print("\n" + "=" * 78)
    print("TRUST CHECK 4 - book consolidation over the three seasons")
    print("=" * 78)
    _tbl("  distinct books and prop outcomes quoting, by season:",
         ("season", "books", "outcomes", "quotes"),
         [(s, b, format(o, ","), format(n, ",")) for s, b, o, n in q(
             """SELECT ou.season, COUNT(DISTINCT q.venue),
                       COUNT(DISTINCT mo.outcome_id), COUNT(*)
                  FROM quotes q
                  JOIN market_outcome mo ON mo.venue=q.venue
                                        AND mo.market_id=q.market_id
                  JOIN outcomes ou ON ou.outcome_id=mo.outcome_id
                 WHERE q.source=? AND q.market_type='prop'
                 GROUP BY 1 ORDER BY 1""", SOURCE)], [10, 10, 12, 12])
    _tbl("  prop quotes per book per season:",
         ("book", "2023", "2024", "2025"),
         q("""SELECT substr(q.venue,9), SUM(ou.season=2023),
                     SUM(ou.season=2024), SUM(ou.season=2025)
                FROM quotes q
                JOIN market_outcome mo ON mo.venue=q.venue
                                      AND mo.market_id=q.market_id
                JOIN outcomes ou ON ou.outcome_id=mo.outcome_id
               WHERE q.source=? AND q.market_type='prop'
               GROUP BY 1 ORDER BY 2+3+4 DESC""", SOURCE), [18, 10, 10, 10])

    print("\n" + "=" * 78)
    print("TRUST CHECK 5 - outcome_id collisions (one claim, two games)")
    print("=" * 78)
    _tbl("  outcomes whose mapped markets span more than one game:",
         ("entity_type", "n"),
         [(t, format(n, ",")) for t, n in q(_COLLISIONS)] or [("none", "0")],
         [16, 10])
    print("  An outcome_id is a SEMANTIC claim, and the key for a team market"
          "\n  is (season, week, team, side) and nothing else. Two games behind"
          "\n  one id means every price, benchmark and settlement on it is a"
          "\n  blend of two unrelated events.")
    con.close()


# --------------------------------------------------------------------------
# hand verification - the only check an aggregate cannot do for you
# --------------------------------------------------------------------------

_HAND_SQL = """
SELECT o.outcome_id, o.key, o.season, o.week, o.entity_id, o.stat, o.line,
       o.side, s.result, s.actual, c.p_bench, c.p_all, c.close_ts,
       c.lead_min, o.event_id
  FROM outcome_settlement s
  JOIN outcomes o      ON o.outcome_id = s.outcome_id
  JOIN outcome_close c ON c.outcome_id = s.outcome_id
 WHERE c.p_bench IS NOT NULL AND s.result IN ('over','under')
"""

_FACT_COLS = ("receptions", "targets", "carries", "rushing_yards",
              "receiving_yards", "passing_yards", "attempts", "completions")


def _raw_rows(close_ts, player, line):
    """The archived payload lines THIS outcome was parsed out of.

    Pinned to the closing snapshot, not just to the player and the line. A
    player and a line recur across seasons - DeAndre Hopkins over 51.5 receiving
    yards appears in 2023, 2023 again and 2024 - and printing all of them under
    one claim is the kind of near-miss evidence a hand check is supposed to
    rule out, not manufacture.
    """
    import glob
    import gzip
    import json
    out = []
    pat = os.path.join(config.RAW_DIR, "oddsapi_historical", "*", "*.jsonl.gz")
    for f in sorted(glob.glob(pat)):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for ln in fh:
                if player and player not in ln:
                    continue
                rec = json.loads(ln)
                pay = rec.get("payload") or {}
                stamp = pay.get("timestamp")
                try:
                    snap = dt.datetime.strptime(
                        str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(
                            tzinfo=dt.timezone.utc).timestamp()
                except (ValueError, TypeError):
                    continue
                if abs(snap - close_ts) > 1:
                    continue
                d = pay.get("data") or {}
                if not isinstance(d, dict):
                    continue
                for bk in d.get("bookmakers") or []:
                    for m in bk.get("markets") or []:
                        for oc in m.get("outcomes") or []:
                            if (oc.get("description") == player
                                    and oc.get("point") == line):
                                out.append((d.get("away_team"),
                                            d.get("home_team"), bk.get("key"),
                                            m.get("key"), oc.get("name"),
                                            oc.get("point"), oc.get("price"),
                                            stamp))
    return out


def hand_check(n=5, seed=None):
    """Print n settled props in full - outcome key, per-book closing quotes,
    the nflverse fact and the raw archived payload lines - so a human can
    check that the line, the side and the price mean what the schema says.

    An aggregate cannot catch a polarity bug. Three raw rows caught the one in
    paper_ledger after four rounds of aggregate queries had agreed with each
    other.
    """
    import random

    con = _ro()
    rows = con.execute(_HAND_SQL).fetchall()
    picks = random.Random(seed).sample(rows, min(n, len(rows)))

    for i, r in enumerate(picks, 1):
        (oid, key, season, week, gsis, stat, line, side, result, actual,
         pb, pa, close_ts, lead, event_id) = r
        nm = con.execute("SELECT display_name FROM player_xwalk WHERE gsis_id=?",
                         (gsis,)).fetchone()
        player = nm[0] if nm else None
        print("\n" + "-" * 78)
        print("[%d] %s" % (i, key))
        print("     outcome_id  %s" % oid)
        print("     player      %s  (%s)" % (player or "?", gsis))
        print("     game        %s     close %s min before kickoff"
              % (event_id, ("%.1f" % lead) if lead is not None else "?"))
        print("     the claim   %s %s %s" % (side.upper(), stat, line))
        print("     closing p   bench %.4f    all books %.4f" % (pb, pa))
        print("     settled     %s   actual %s" % (result.upper(), actual))

        # The fact, recomputed by the settlement job's OWN expression rather
        # than by re-reading `outcome_settlement`. Reading the settlement back
        # would only prove the table agrees with itself; this re-derives the
        # number from nflverse and shows which column produced it - and for a
        # defensive prop that column is a sum of three, which is the one place
        # this repo has already been wrong.
        from jobs.settle_outcomes import STAT_COLUMN, resolve
        col = STAT_COLUMN.get(stat)
        fact = con.execute(
            "SELECT %s, data_version FROM nfl_player_week WHERE gsis_id=? "
            "AND season=? AND week=? AND season_type='REG' "
            "ORDER BY data_version DESC LIMIT 1" % col,
            (gsis, season, week)).fetchone() if col else None
        if fact and fact[0] is not None:
            again = resolve(float(fact[0]), line, False)
            print("     nflverse    %s = %s   (%s, version %s)"
                  % (col if len(col) < 40 else stat, fact[0], stat, fact[1]))
            print("     re-settles  %s   %s the stored %s"
                  % (again.upper(),
                     "AGREES with" if again == result else "DISAGREES with",
                     result.upper()))
        else:
            print("     nflverse    (no player-week row)")
        ctx = con.execute(
            "SELECT " + ", ".join(_FACT_COLS) + " FROM nfl_player_week "
            "WHERE gsis_id=? AND season=? AND week=? AND season_type='REG' "
            "ORDER BY data_version DESC LIMIT 1",
            (gsis, season, week)).fetchone()
        if ctx:
            print("     context     " + "  ".join(
                "%s=%s" % (c, v) for c, v in zip(_FACT_COLS, ctx)))

        print("     per-book quotes at the closing snapshot:")
        for v, mid, dv, last in con.execute(
                """SELECT q.venue, q.mid, q.prob_devig, q.last FROM quotes q
                     JOIN market_outcome mo ON mo.venue=q.venue
                                           AND mo.market_id=q.market_id
                    WHERE mo.outcome_id=? AND q.ts=? ORDER BY 1""",
                (oid, close_ts)):
            print("        %-26s american %-7s implied %-8s devig %s"
                  % (v, ("%+d" % last) if last is not None else "?",
                     ("%.4f" % mid) if mid is not None else "none",
                     ("%.4f" % dv) if dv is not None else "none"))

        print("     raw archive lines at THIS snapshot:")
        raw = _raw_rows(close_ts, player, line)
        if not raw:
            print("        (not found on disk)")
        for away, home, book, mkey, name, point, price, tstamp in raw[:10]:
            print("        %-11s %-26s %-6s %-6s %+5d   %s @ %s @ %s"
                  % (book, mkey, name, point, price, away, home, tstamp))
    con.close()


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------

def _write_csv(name, header, rows):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, name)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    print("  wrote %s  (%d rows)" % (path, len(rows)))
    return path


def export(rows, price="bench"):
    """Two CSVs. Both carry the slice columns, so nothing downstream has to
    guess which population a number came from."""
    k = _key(price)
    out = []
    slices = [("all", "all", "all", "all")]
    slices += [("season", str(s), "all", "all")
               for s in sorted({r.season for r in rows})]
    slices += [("stat", str(s), "all", "all")
               for s in sorted({r.stat for r in rows if r.stat})]
    slices += [("side", s, "all", "all") for s in ("over", "under")]
    slices += [("liquidity", b, "all", "all")
               for b in sorted({r.bucket for r in rows})]
    slices += [("n_books", str(nb), "all", "all")
               for nb in sorted({r.n_all for r in rows})]

    def sel(dim, val):
        if dim == "all":
            return rows
        if dim == "season":
            return [r for r in rows if str(r.season) == val]
        if dim == "stat":
            return [r for r in rows if r.stat == val]
        if dim == "side":
            return [r for r in rows if r.side == val]
        if dim == "liquidity":
            return [r for r in rows if r.bucket == val]
        if dim == "n_books":
            return [r for r in rows if str(r.n_all) == val]
        return []

    for dim, val, _a, _b in slices:
        sub = sel(dim, val)
        if len(sub) < 100:
            continue
        s = score(sub, price)
        for b in curve(sub, price):
            out.append((dim, val, b["bucket"], b["n"],
                        "%.6f" % b["predicted"], "%.6f" % b["realized"],
                        "%.6f" % b["diff"], "%.6f" % b["se"], "%.3f" % b["z"],
                        "%.6f" % s["brier"], "%.6f" % s["log_loss"],
                        "%.6f" % s["ece"], s["n"], price))
    cal = _write_csv(
        "calibration_curve.csv",
        ("slice_dim", "slice_value", "price_bucket", "n", "predicted",
         "realized", "diff", "se", "z", "slice_brier", "slice_log_loss",
         "slice_ece", "slice_n", "price_source"), out)

    sk = []
    for dim, val in ([("all", "all")]
                     + [("stat", s) for s in sorted({r.stat for r in rows
                                                     if r.stat})]
                     + [("season", str(s))
                        for s in sorted({r.season for r in rows})]
                     + [("liquidity", b) for b in sorted({r.bucket
                                                          for r in rows})]):
        sub = sel(dim, val)
        if len(sub) < 100:
            continue
        s = skew(sub, price)
        for band, blk in (("all", s["all"]), ("0.45-0.55", s["band"])):
            if not blk or blk["n"] < 50:
                continue
            sk.append((dim, val, band, blk["n"],
                       "%.6f" % blk["priced_over"],
                       "%.6f" % blk["realized_over"],
                       "%.6f" % blk["priced_under"],
                       "%.6f" % blk["realized_under"],
                       "%.6f" % blk["diff"], "%.6f" % blk["se"],
                       "%.3f" % blk["z"], "%.4f" % blk["median_margin"],
                       "%.4f" % blk["mean_margin"], price))
    und = _write_csv(
        "under_skew.csv",
        ("slice_dim", "slice_value", "price_band", "n", "priced_over",
         "realized_over", "priced_under", "realized_under", "diff", "se", "z",
         "median_margin", "mean_margin", "price_source"), sk)
    return cal, und


# --------------------------------------------------------------------------
# the register's figure (unit a-29)
# --------------------------------------------------------------------------

# The last regular-season week. Postseason weeks (19-22) are EXCLUDED from the
# register figure, and not as a preference: jobs/settle_outcomes.py reads
# `nfl_player_week` with season_type = 'REG' only, so a postseason prop has no
# stat row, and since the 2026-09-17 settlement fix a player with snaps and no
# row settles at 0. Every postseason over in this population therefore settles
# as a loss whatever the player did - measured 2026-09-24: 2,941 postseason over
# outcomes, all `under` at 0.0, while e.g. Nico Collins 2023 wk19 has 96 POST
# receiving yards against a 76.5 line. Included, they move the over-side gap
# from -2.43pp to -5.29pp. Remove this filter only after the settlement is
# fixed AND re-run.
LAST_REG_WEEK = 18
REGISTER_DRAWS = 2000
REGISTER_SEED = 29


def register_figure(price="bench", draws=REGISTER_DRAWS, seed=REGISTER_SEED):
    """The over-side pricing gap as the register publishes it.

    estimate = realized over rate - priced over rate, in pp, over settled
    regular-season outcomes carrying a benchmark close. The interval is a GAME
    block bootstrap: props in one game share a scoring environment, so the
    effective sample is games, not outcomes, and the null-variance `se` the
    curve prints (z = diff / se) treats 40,000+ outcomes as independent. Both
    are returned; the register quotes the bootstrap.
    """
    import random
    k = _key(price)
    rows = [r for r in population(price)
            if r.side == "over" and r.week is not None and r.week <= LAST_REG_WEEK]
    con = _ro()
    game = dict(con.execute("SELECT outcome_id, event_id FROM outcomes"))
    post = con.execute(
        "SELECT COUNT(*) FROM outcome_settlement s JOIN outcomes o USING (outcome_id) "
        "JOIN outcome_close c USING (outcome_id) WHERE o.side = 'over' AND o.week > ? "
        "AND s.result IN ('over','under') AND c.%s IS NOT NULL" % k,
        (LAST_REG_WEEK,)).fetchone()[0]
    con.close()
    by = {}
    for r in rows:
        g = game.get(r.oid)
        if g is None:
            raise RuntimeError("outcome %s has no event_id; cannot block by game" % r.oid)
        s = by.setdefault(g, [0.0, 0])
        s[0] += r.hit - getattr(r, k)
        s[1] += 1
    if not rows:
        raise RuntimeError("register_figure: empty population - refusing to print a figure")
    n = len(rows)
    est = sum(v[0] for v in by.values()) / n
    blocks = list(by.values())
    rng = random.Random(seed)
    out = []
    for _ in range(draws):
        tot = cnt = 0
        for _ in range(len(blocks)):
            b = blocks[rng.randrange(len(blocks))]
            tot += b[0]
            cnt += b[1]
        out.append(tot / cnt)
    out.sort()
    se_null = _se(rows, price)
    return {"n": n, "games": len(blocks), "seasons": sorted({r.season for r in rows}),
            "priced_over": sum(getattr(r, k) for r in rows) / n,
            "realized_over": sum(r.hit for r in rows) / n,
            "est_pp": 100 * est,
            "lo_pp": 100 * out[int(0.025 * draws)],
            "hi_pp": 100 * out[int(0.975 * draws) - 1],
            "boot_se_pp": 100 * statistics.pstdev(out),
            "z_null": est / se_null if se_null else None,
            "excluded_postseason": post, "draws": draws, "seed": seed}


def print_register(price="bench"):
    f = register_figure(price)
    print("REGISTER FIGURE - over side, %s close, regular season %s"
          % (price, "-".join(str(s) for s in (f["seasons"][0], f["seasons"][-1]))))
    print("  n %s outcomes, %s games; %s postseason overs EXCLUDED (see LAST_REG_WEEK)"
          % (format(f["n"], ","), f["games"], format(f["excluded_postseason"], ",")))
    print("  priced over %.4f   realized over %.4f" % (f["priced_over"], f["realized_over"]))
    print("  realized - priced %+.2fpp   game-block 95%% [%+.2f, %+.2f]   boot se %.2fpp"
          % (f["est_pp"], f["lo_pp"], f["hi_pp"], f["boot_se_pp"]))
    print("  z under the null-variance se (outcomes treated as independent): %+.1f"
          % f["z_null"])
    print("  bootstrap: %d draws, seed %d" % (f["draws"], f["seed"]))
    return f


def report(price="bench"):
    rows = population(price)
    print("\n" + "=" * 78)
    print("MARKET CALIBRATION CURVE")
    print("=" * 78)
    print("\npopulation: settled outcomes carrying a de-vigged closing price")
    print("  price source: %s" % ("median across draftkings / fanduel / betmgm"
                                  if price == "bench"
                                  else "median across every book quoting"))
    print("  n = %s" % format(len(rows), ","))

    _print_curve("ALL settled outcomes, both sides", rows, price)
    _print_curve("OVER side only (an asymmetry cannot hide in its complement)",
                 [r for r in rows if r.side == "over"], price)
    _print_curve("UNDER side only",
                 [r for r in rows if r.side == "under"], price)

    if price == "bench":
        # The benchmark is draftkings/fanduel/betmgm, and on props those three
        # cover barely half of what settled: betrivers is the widest prop feed
        # in this archive and is not one of them. A curve drawn on the
        # benchmark alone is drawn on half the data, so the same question gets
        # asked again with every book that quoted. If the two disagree, the
        # benchmark is a selection effect and not a consensus.
        wide = population("all")
        print("\n" + "-" * 78)
        print("THE SAME QUESTION ON THE WIDER POPULATION")
        print("-" * 78)
        _print_curve("ALL settled outcomes, median across EVERY book quoting",
                     wide, "all")
        _print_curve("OVER side only, every book",
                     [r for r in wide if r.side == "over"], "all")
        print("-" * 78)

    _print_slices("BY STAT", rows, lambda r: r.stat, price)
    _print_slices("BY SEASON", rows, lambda r: r.season, price)
    _print_slices("BY LIQUIDITY BUCKET (brief 006 stratification)", rows,
                  lambda r: r.bucket, price)
    print("  Sportsbook quotes carry no size, so the structural bucket is"
          "\n  `unknown` for all of them. That is a fact about the venue, not"
          "\n  a gap: the tradeable venues are Kalshi and Polymarket, and no"
          "\n  settled outcome in this store has a market on either yet.")
    _print_slices("BY BOOK COUNT AT CLOSE (the liquidity proxy that does "
                  "carry information here)", rows, lambda r: "%02d books"
                  % r.n_all, price)
    _print_slices("BY CROSS-BOOK DISPERSION", rows,
                  lambda r: ("a. <0.02" if (r.dispersion or 0) < 0.02 else
                             "b. 0.02-0.05" if r.dispersion < 0.05 else
                             "c. 0.05-0.10" if r.dispersion < 0.10 else
                             "d. >=0.10"), price)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true",
                    help="rebuild outcome_close from quotes")
    ap.add_argument("--price", choices=("bench", "all"), default="bench")
    ap.add_argument("--skew", action="store_true")
    ap.add_argument("--trust", action="store_true")
    ap.add_argument("--hand", type=int, metavar="N",
                    help="print N settled props in full for hand checking")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--all", action="store_true", help="everything")
    ap.add_argument("--register", action="store_true",
                    help="print the figure docs/hypotheses.json carries (read-only)")
    args = ap.parse_args()

    # Before init_db, which opens the store read-write. The register figure
    # only reads, so it must be runnable against the live store.
    if args.register:
        print_register(args.price)
        return

    store.init_db()
    if args.build or args.all:
        build()
    if args.hand:
        hand_check(args.hand, args.seed)
        if not args.all:
            return
    if args.trust and not args.all:
        trust()
        return
    if args.skew and not args.all:
        report_skew(population(args.price), args.price)
        return

    rows = report(args.price)
    if args.skew or args.all:
        report_skew(rows, args.price)
    if args.trust or args.all:
        trust()
    if args.export or args.all:
        print("\n" + "=" * 78)
        print("EXPORTS")
        print("=" * 78)
        export(rows, args.price)


if __name__ == "__main__":
    main()
