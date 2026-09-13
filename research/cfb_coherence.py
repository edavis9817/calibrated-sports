"""BRIEF C01 PART 1 - are Kalshi's college-football period markets priced as
derivatives of the game market, or independently?

    python research/cfb_coherence.py --all
    python research/cfb_coherence.py --ladders     # what is in the bucket
    python research/cfb_coherence.py --overround   # the invariant, per partition
    python research/cfb_coherence.py --coherence   # the relations
    python research/cfb_coherence.py --export      # CSV

READ-ONLY against the CFB probe capture. Touches nothing else.

WHY EXPECTATIONS AND NOT QUANTILES
A period market is a derivative of the game market, and the tie that binds them
needs no model: expectation is LINEAR. E[Q1]+E[Q2] = E[1H] and Sum E[Qi] =
E[game] hold for ANY joint distribution, correlated or not, so a deviation is
the venue's and not the method's. Medians would not do - they do not add.

HOW E IS RECOVERED
Each series-event is a ladder of "Over N" contracts, which IS a survival
function S(t) = P(X > t): a median of 19 rungs on a game total, 11 on a
quarter. Naive integration fails, because the lowest game-total rung is 38.5
and everything under it is unpriced - that head is worth ~37 points and
bounding it costs more than the deviations being tested. So the ladder is
FITTED. On the probit scale a normal survival function is exactly linear,

    t_i = mu + sigma * z_i        z_i = Phi^-1(1 - S_i)

so mu and sigma come from an OLS line through the rungs and E[X] = mu
interpolates among priced rungs instead of extrapolating past them. Lognormal
was tried and is worse on every quantity (game 0.0215 vs 0.0133 rmse, 1H 0.0537
vs 0.0239), so normal it is. `rmse` is reported on the PROBABILITY scale
throughout, because that is the scale a reader can judge.

Quarter ladders fit worst (rmse ~0.04-0.05 against ~0.013 for a game total) and
they should: a quarter's points are lumpy, with mass at 0, 3, 7, 10 and 14, and
no smooth two-parameter family follows that. They are NOT gated out - they are
carried with a second, nearly model-free estimator beside them (`np_mean`:
bounded head, trapezoid body, exponential tail) and no conclusion is drawn
where the two estimators disagree.

BOTH TEAMS ARE QUOTED ON THE SPREAD, ON ALTERNATING RUNGS - 79 of 126 games.
Fitting them as one ladder is not a small error: it produced rmse up to 0.51 and
a median favourite margin of 32 points, which is a blowout every week and
obviously wrong. Split by team, the opponent's ladder is not redundant, it is
the other half of the same curve:

    S_A(-L) = 1 - S_B(L)

because the lines are half-integers and a tie at exactly -L is impossible. That
turns a one-sided ladder from +1.5 up into a two-sided margin curve from -29.5
to +29.5. `KXNCAAFGAME` adds one more rung at t = 0.5, since CFB plays overtime
and P(margin >= 1) = P(A wins).

WHAT MAKES A DEVIATION ACTIONABLE
Nothing here is tradeable at the mid. Every E is computed three times - from the
bid ladder, the mid ladder and the ask ladder - and the [E_bid, E_ask] band is
what crossing the spread costs. A relation is flagged ONLY where |deviation|
exceeds the summed bands of its legs. That is the last column of every
coherence table and it is the only column that could become a trade.
"""
import argparse
import csv
import math
import os
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from statistics import NormalDist
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

ET = ZoneInfo("America/New_York")
N01 = NormalDist()

# STORAGE LOCATION COMES FROM CONFIG, NEVER FROM A PATH LITERAL.
CFB_DB = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)),
                      "cfb_probe.db")

# Pre-kickoff by construction: the earliest college kickoff on a Saturday is
# around noon ET, so 10:00 is ahead of the whole slate. Coherence holds in-game
# too, but mixing a pre-game book with a live one blurs what a deviation means.
SNAPSHOT_HOUR = 10

# An empty Kalshi book quotes 0/1 and prints a meaningless 0.500 mid, so some
# width gate is mandatory. 0.35 rather than a tighter 0.20 because later
# quarters are genuinely thin - only 148 of Q4's 363 rungs are quoted inside
# 0.20 - and over 0.20/0.35/0.60 the gate starves the sample WITHOUT moving the
# answer: every median is flat to two decimals while n on Q1..Q4 goes
# 4 -> 23 -> 27. At 0.10 it does bite - the quarters collapse to n<=1 and the
# team-total relation moves +0.17 -> +0.37 on a differently selected 59 - so
# the flatness is a measured range, not a law. `--sensitivity` prints the
# sweep; read it before changing this number.
MAX_SPREAD = 0.35
MIN_RUNGS = 6          # a two-parameter fit needs room
MAX_RMSE = 0.060       # generous on purpose - quarters are lumpy, see above
FIT_LO, FIT_HI = 0.03, 0.97

TOTALS = {"game": "KXNCAAFTOTAL", "1H": "KXNCAAF1HTOTAL",
          "Q1": "KXNCAAF1QTOTAL", "Q2": "KXNCAAF2QTOTAL",
          "Q3": "KXNCAAF3QTOTAL", "Q4": "KXNCAAF4QTOTAL"}
SPREADS = {"spread": "KXNCAAFSPREAD", "spread1H": "KXNCAAF1HSPREAD"}
QUARTER_WINNER = {"Q1": "KXNCAAF1Q", "Q2": "KXNCAAF2Q",
                  "Q3": "KXNCAAF3Q", "Q4": "KXNCAAF4Q"}
ORDER = ["game", "1H", "Q1", "Q2", "Q3", "Q4", "spread", "spread1H"]


def db():
    if not os.path.exists(CFB_DB):
        raise SystemExit(f"no CFB capture at {CFB_DB}")
    return sqlite3.connect(f"file:{CFB_DB}?mode=ro", uri=True)


# =============================================================================
# the snapshot
# =============================================================================

DATECODE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def game_key(event_id):
    """`KXNCAAFTOTAL-26SEP12ULLUSC` -> `26SEP12ULLUSC`, the cross-series id."""
    return event_id.split("-", 1)[1] if "-" in event_id else event_id


def team_of(market_id):
    """`...-26SEP12ULLUSC-USC42` -> `USC`. Trailing digits are the line."""
    return re.sub(r"\d+$", "", market_id.rsplit("-", 1)[1])


def snapshot_ts(gkey):
    m = DATECODE.match(gkey)
    if not m:
        return None
    yy, mon, dd = m.groups()
    try:
        return datetime(2000 + int(yy), MONTHS[mon], int(dd),
                        SNAPSHOT_HOUR, 0, tzinfo=ET).timestamp()
    except (KeyError, ValueError):
        return None


def probe_window(conn):
    return conn.execute("SELECT MIN(ts), MAX(ts) FROM quotes").fetchone()


def load_snapshot(conn, series, lo, hi):
    """Last quote at or before each game's snapshot, for every market in
    `series`. One indexed query per market (ix_quotes_market_ts)."""
    out = defaultdict(list)
    by_game = defaultdict(list)
    for mid, ev, line, subj in conn.execute(
            "SELECT market_id, event_id, line, subject FROM markets "
            "WHERE market_id LIKE ?||'-%'", (series,)):
        by_game[game_key(ev)].append((mid, line, subj))
    for gkey, rows in by_game.items():
        ts = snapshot_ts(gkey)
        if ts is None:
            continue
        # A game dated after the probe died was never played inside the
        # window, so its last quote is still a pre-kickoff quote.
        ts = min(ts, hi)
        if ts < lo:
            continue
        for mid, line, subj in rows:
            r = conn.execute(
                "SELECT best_bid, best_ask, mid, ts FROM quotes "
                "WHERE venue='cfb_kalshi' AND market_id=? AND ts<=? "
                "ORDER BY ts DESC LIMIT 1", (mid, ts)).fetchone()
            if not r:
                continue
            bid, ask, mid_p, qts = r
            if bid is None or ask is None or ask < bid:
                continue
            out[gkey].append({
                "market_id": mid, "line": line, "subject": subj,
                "team": team_of(mid), "bid": bid, "ask": ask,
                "mid": mid_p if mid_p is not None else (bid + ask) / 2,
                "ts": qts, "spread": ask - bid})
    return out


def split_by_team(points):
    d = defaultdict(list)
    for p in points:
        d[p["team"]].append(p)
    return d


# =============================================================================
# the margin curve: the opponent's ladder is the other half of it
# =============================================================================

def margin_points(own, opp, money):
    """Two-sided survival curve for the reference team's winning margin.

    `own` prices P(margin > L) directly. `opp` prices P(-margin > L), and
    half-integer lines make a tie impossible, so it prices P(margin > -L) as
    its complement - which flips bid and ask, since 1 - ask is the new bid.
    `money` is one more rung at 0.5: CFB plays overtime, so P(margin >= 1) is
    exactly P(win).
    """
    pts = [dict(p) for p in own]
    for p in opp:
        if p["line"] is None:
            continue
        pts.append({**p, "line": -p["line"],
                    "bid": 1.0 - p["ask"], "ask": 1.0 - p["bid"],
                    "mid": 1.0 - p["mid"], "spread": p["spread"],
                    "subject": "complement of " + str(p["subject"])})
    if money is not None:
        pts.append({**money, "line": 0.5, "subject": "moneyline"})
    return pts


# =============================================================================
# fitting
# =============================================================================

def _clean(points, price):
    """Rungs as (line, S) on one price basis, deduplicated and sorted."""
    seen = {}
    for p in points:
        if p["line"] is None or p["spread"] > MAX_SPREAD:
            continue
        s = p[price]
        if s is None or not (0.0 < s < 1.0):
            continue
        seen[p["line"]] = s
    return sorted(seen.items())


def monotonicity(points):
    """A survival function cannot rise with the line. Staleness and crossed
    books both surface here."""
    lad = _clean(points, "mid")
    bad = sum(1 for i in range(1, len(lad)) if lad[i][1] > lad[i - 1][1] + 1e-9)
    return bad, len(lad)


def _isotonic_decreasing(rungs):
    """PAVA projection onto non-increasing S. Equal weights."""
    vals = [[s, 1.0] for _, s in rungs]
    i = 0
    while i < len(vals) - 1:
        if vals[i][0] < vals[i + 1][0] - 1e-12:
            v = (vals[i][0] * vals[i][1] + vals[i + 1][0] * vals[i + 1][1]) \
                / (vals[i][1] + vals[i + 1][1])
            w = vals[i][1] + vals[i + 1][1]
            vals[i:i + 2] = [[v, w]]
            i = max(i - 1, 0)
        else:
            i += 1
    out, k = [], 0
    for v, w in vals:
        for _ in range(int(round(w))):
            out.append((rungs[k][0], v))
            k += 1
    return out


def fit_ladder(points, price="mid"):
    """OLS of line on probit(1-S). Returns mu (= E[X]), sigma, rmse, n."""
    lad = [(t, s) for t, s in _clean(points, price) if FIT_LO <= s <= FIT_HI]
    if len(lad) < MIN_RUNGS:
        return None
    ts = [t for t, _ in lad]
    zs = [N01.inv_cdf(1.0 - s) for _, s in lad]
    n = len(lad)
    mz, mt = sum(zs) / n, sum(ts) / n
    den = sum((z - mz) ** 2 for z in zs)
    if den <= 0:
        return None
    sigma = sum((z - mz) * (t - mt) for z, t in zip(zs, ts)) / den
    if sigma <= 0:                    # a rising ladder is not a distribution
        return None
    mu = mt - sigma * mz
    rmse = math.sqrt(sum((s - (1.0 - N01.cdf((t - mu) / sigma))) ** 2
                         for t, s in lad) / n)
    return {"mu": mu, "sigma": sigma, "rmse": rmse, "n": n,
            "lo": ts[0], "hi": ts[-1], "s_lo": lad[0][1], "s_hi": lad[-1][1]}


def np_mean(points):
    """Nearly model-free E for a NON-NEGATIVE quantity, as a cross-check.

    E = head + body + tail, where the body is a plain trapezoid over the priced
    rungs, the head over [0, t1] is bounded between t1*S1 and t1 (its midpoint
    taken, its half-width returned), and only the tail past the top rung is
    modelled - an exponential fitted to the last three rungs, worth S_n*lambda.

    Returns (E, uncertainty, tail_share). A large tail_share means the ladder
    stops while real probability is still out there and the number leans on the
    one modelled piece.
    """
    lad = _isotonic_decreasing(_clean(points, "mid"))
    if len(lad) < MIN_RUNGS or lad[0][0] < 0:
        return None
    t1, s1 = lad[0]
    head, head_unc = t1 * (1.0 + s1) / 2.0, t1 * (1.0 - s1) / 2.0
    body = sum((lad[i][0] - lad[i - 1][0]) * (lad[i][1] + lad[i - 1][1]) / 2.0
               for i in range(1, len(lad)))
    tn, sn = lad[-1]
    lam = None
    tail_pts = [(t, s) for t, s in lad[-3:] if s > 1e-6]
    if len(tail_pts) >= 2 and tail_pts[0][1] > tail_pts[-1][1]:
        lam = (tail_pts[-1][0] - tail_pts[0][0]) / \
              (math.log(tail_pts[0][1]) - math.log(tail_pts[-1][1]))
    if lam is None or lam <= 0 or lam > 40:
        lam = max(1.0, tn / 4.0)      # a bounded fallback, never unbounded
    tail = sn * lam
    e = head + body + tail
    return e, head_unc + 0.5 * tail, (tail / e if e > 0 else 1.0)


def fit_banded(points, nonneg=True):
    """The mid fit, the bid/ask band, and the model-free cross-check."""
    mid = fit_ladder(points, "mid")
    if mid is None or mid["rmse"] > MAX_RMSE:
        return None
    lo = fit_ladder(points, "bid")
    hi = fit_ladder(points, "ask")
    mid["e_lo"] = lo["mu"] if lo else mid["mu"]
    mid["e_hi"] = hi["mu"] if hi else mid["mu"]
    mid["band"] = max(0.0, mid["e_hi"] - mid["e_lo"]) / 2.0
    npm = np_mean(points) if nonneg else None
    mid["np"] = npm[0] if npm else None
    mid["np_unc"] = npm[1] if npm else None
    mid["tail_share"] = npm[2] if npm else None
    return mid


# =============================================================================
# assembling one game
# =============================================================================

def build(conn):
    lo, hi = probe_window(conn)
    tot = {k: load_snapshot(conn, s, lo, hi) for k, s in TOTALS.items()}
    spr = {k: load_snapshot(conn, s, lo, hi) for k, s in SPREADS.items()}
    team = load_snapshot(conn, "KXNCAAFTEAMTOTAL", lo, hi)
    money = load_snapshot(conn, "KXNCAAFGAME", lo, hi)
    qwin = {k: load_snapshot(conn, s, lo, hi) for k, s in QUARTER_WINNER.items()}

    keys = set()
    for d in list(tot.values()) + list(spr.values()) + [team, money]:
        keys |= set(d)

    games = {}
    for gkey in keys:
        g = {"key": gkey, "fits": {}, "raw": {}, "teams": {}, "notes": []}
        for name in TOTALS:
            pts = tot[name].get(gkey, [])
            g["raw"][name] = pts
            f = fit_banded(pts, nonneg=True)
            if f:
                g["fits"][name] = f
        for t, pts in split_by_team(team.get(gkey, [])).items():
            f = fit_banded(pts, nonneg=True)
            if f:
                g["teams"][t] = f
        g["money"] = {p["team"]: p for p in money.get(gkey, [])}
        g["qwin"] = {k: v.get(gkey, []) for k, v in qwin.items()}

        for name in SPREADS:
            pts = spr[name].get(gkey, [])
            g["raw"][name] = pts
            sides = split_by_team(pts)
            if not sides:
                continue
            # Reference team = the moneyline favourite where one is quoted,
            # else whichever side hung more rungs.
            ref = None
            if g["money"]:
                ref = max(g["money"], key=lambda t: g["money"][t]["mid"])
            if ref not in sides:
                ref = max(sides, key=lambda t: len(sides[t]))
            opp = [t for t in sides if t != ref]
            mp = margin_points(
                sides[ref], sides[opp[0]] if opp else [],
                g["money"].get(ref) if name == "spread" else None)
            g["raw"][name + ":ref"] = mp
            f = fit_banded(mp, nonneg=False)
            if f:
                f["ref"] = ref
                f["two_sided"] = bool(opp)
                g["fits"][name] = f
        games[gkey] = g
    return games


# =============================================================================
# the relations
# =============================================================================

def relations(g):
    f, out = g["fits"], []

    def add(name, lhs, rhs, band, detail, np_lhs=None, np_rhs=None, **kw):
        npdev = (np_lhs - np_rhs) if (np_lhs is not None and np_rhs is not None) else None
        out.append({"name": name, "dev": lhs - rhs, "band": band,
                    "detail": detail, "np_dev": npdev, **kw})

    def npm(*k):
        v = [f[x]["np"] for x in k if x in f]
        return sum(v) if len(v) == len(k) and all(x is not None for x in v) else None

    if all(x in f for x in ("Q1", "Q2", "1H")):
        add("Q1+Q2 = 1H", f["Q1"]["mu"] + f["Q2"]["mu"], f["1H"]["mu"],
            f["Q1"]["band"] + f["Q2"]["band"] + f["1H"]["band"],
            f'{f["Q1"]["mu"]:.1f}+{f["Q2"]["mu"]:.1f} vs {f["1H"]["mu"]:.1f}',
            npm("Q1", "Q2"), npm("1H"))
    if all(x in f for x in ("Q1", "Q2", "Q3", "Q4", "game")):
        s = sum(f[q]["mu"] for q in ("Q1", "Q2", "Q3", "Q4"))
        add("Q1..Q4 = game", s, f["game"]["mu"],
            sum(f[q]["band"] for q in ("Q1", "Q2", "Q3", "Q4")) + f["game"]["band"],
            f'{s:.1f} vs {f["game"]["mu"]:.1f}',
            npm("Q1", "Q2", "Q3", "Q4"), npm("game"))
    if all(x in f for x in ("Q3", "Q4", "game", "1H")):
        lhs, rhs = f["Q3"]["mu"] + f["Q4"]["mu"], f["game"]["mu"] - f["1H"]["mu"]
        a, b = npm("Q3", "Q4"), npm("game")
        c = npm("1H")
        add("Q3+Q4 = game-1H", lhs, rhs,
            f["Q3"]["band"] + f["Q4"]["band"] + f["game"]["band"] + f["1H"]["band"],
            f"{lhs:.1f} vs {rhs:.1f}", a,
            (b - c) if (b is not None and c is not None) else None)
    if len(g["teams"]) == 2 and "game" in f:
        a, b = sorted(g["teams"])
        s = g["teams"][a]["mu"] + g["teams"][b]["mu"]
        na, nb = g["teams"][a]["np"], g["teams"][b]["np"]
        add("teamA+teamB = game total", s, f["game"]["mu"],
            g["teams"][a]["band"] + g["teams"][b]["band"] + f["game"]["band"],
            f'{s:.1f} vs {f["game"]["mu"]:.1f}',
            (na + nb) if (na is not None and nb is not None) else None,
            f["game"]["np"],
            fav_margin=(f["spread"]["mu"] if "spread" in f else None))
    if len(g["teams"]) == 2 and "spread" in f and "ref" in f["spread"]:
        ref = f["spread"]["ref"]
        if ref in g["teams"]:
            dog = [t for t in g["teams"] if t != ref][0]
            diff = g["teams"][ref]["mu"] - g["teams"][dog]["mu"]
            # Split by whether the opponent is quoted. A one-sided margin
            # ladder starts at +1.5 and the fit must extrapolate through the
            # whole left half - on a blowout that is worth 30+ points of
            # nonsense, which is the estimator's and not the market's.
            two = bool(f["spread"].get("two_sided"))
            nm = ("teamRef-teamOpp = spread [2-sided]" if two
                  else "teamRef-teamOpp = spread [1-sided]")
            add(nm, diff, f["spread"]["mu"],
                g["teams"][ref]["band"] + g["teams"][dog]["band"] + f["spread"]["band"],
                f'{diff:.1f} vs {f["spread"]["mu"]:.1f}', two_sided=two)
    return out


# =============================================================================
# reporting
# =============================================================================

def _hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def report_ladders(games):
    _hdr("WHAT IS IN THE BUCKET - every stratum, before any interpretation")
    print(f"""
  A ladder is used only if it has >={MIN_RUNGS} rungs inside S in [{FIT_LO}, {FIT_HI}],
  every rung quoted tighter than {MAX_SPREAD:.2f}, and a normal fit with rmse <= {MAX_RMSE:.3f}.
  'band' is half the [E_bid, E_ask] width IN POINTS - what crossing the book
  costs on that one leg. 'non-mono' counts rungs where the survival probability
  RISES with the line, which it cannot: staleness and crossed books land here.
  'E np' is the near model-free cross-check and 'tail' the share of it that
  comes from the one modelled piece.""")
    print(f"\n    {'quantity':<11}{'games':>6}{'fit':>5}{'rungs':>7}{'rmse':>8}"
          f"{'E':>7}{'band':>7}{'E np':>7}{'tail':>7}{'non-mono':>11}")
    for name in ORDER:
        seen = [g for g in games.values() if g["raw"].get(name)]
        fit = [g["fits"][name] for g in games.values() if name in g["fits"]]
        if not seen:
            continue
        mono = [monotonicity(g["raw"].get(name + ":ref") or g["raw"][name])
                for g in seen]
        nm, tt = sum(x[0] for x in mono), sum(x[1] for x in mono)
        if not fit:
            print(f"    {name:<11}{len(seen):>6}{0:>5}"
                  f"{'':>29}{nm:>5}/{tt:<5}")
            continue
        nps = [f["np"] for f in fit if f["np"] is not None]
        tls = [f["tail_share"] for f in fit if f["tail_share"] is not None]
        print(f"    {name:<11}{len(seen):>6}{len(fit):>5}"
              f"{statistics.median([f['n'] for f in fit]):>7.0f}"
              f"{statistics.median([f['rmse'] for f in fit]):>8.4f}"
              f"{statistics.median([f['mu'] for f in fit]):>7.1f}"
              f"{statistics.median([f['band'] for f in fit]):>7.2f}"
              f"{statistics.median(nps) if nps else float('nan'):>7.1f}"
              f"{(statistics.median(tls) if tls else float('nan')):>7.1%}"
              f"{nm:>5}/{tt:<5}")
    two = sum(1 for g in games.values() if g["fits"].get("spread", {}).get("two_sided"))
    print(f"\n    team totals: {sum(1 for g in games.values() if len(g['teams'])==2)}"
          f" games with BOTH sides fitted")
    print(f"    spread: {two} of {sum(1 for g in games.values() if 'spread' in g['fits'])}"
          f" fitted margin curves are TWO-SIDED (both teams quoted)")


def report_overround(games):
    _hdr("THE OVERROUND INVARIANT, on every genuine partition")
    print("""
  Kalshi is an exchange, so the yes-mid IS the probability and a partition
  should sum to 1.00, not to a bookmaker's 1.05. Mid and ASK sums are both
  shown: the ask sum is what a buyer of every leg actually pays, and only it
  can be arbitraged. A mid sum BELOW 1.00 is not free money - it is the
  bid-ask spread seen from the inside.""")
    for label, want, pick in (
            ("moneyline (2-way)", 2, lambda g: list(g["money"].values())),
            ("Q1 winner (3-way)", 3, lambda g: g["qwin"]["Q1"]),
            ("Q2 winner (3-way)", 3, lambda g: g["qwin"]["Q2"]),
            ("Q3 winner (3-way)", 3, lambda g: g["qwin"]["Q3"]),
            ("Q4 winner (3-way)", 3, lambda g: g["qwin"]["Q4"])):
        mids, asks, bids = [], [], []
        for g in games.values():
            legs = [p for p in pick(g) if p["spread"] <= MAX_SPREAD]
            if len(legs) != want:
                continue
            mids.append(sum(p["mid"] for p in legs))
            asks.append(sum(p["ask"] for p in legs))
            bids.append(sum(p["bid"] for p in legs))
        if not mids:
            print(f"\n    {label:<20} no complete partitions quoted inside the spread gate")
            continue
        viol = sum(1 for m in mids if not (1.00 <= m <= 1.15))
        arb = sum(1 for a in asks if a < 1.0)
        print(f"\n    {label:<20} n={len(mids)}")
        print(f"      bid sum   med {statistics.median(bids):.4f}")
        print(f"      mid sum   med {statistics.median(mids):.4f}"
              f"   min {min(mids):.4f}   max {max(mids):.4f}")
        print(f"      ask sum   med {statistics.median(asks):.4f}"
              f"   min {min(asks):.4f}   max {max(asks):.4f}")
        print(f"      mid outside [1.00, 1.15]: {viol}/{len(mids)}"
              f" ({100*viol/len(mids):.0f}%)   ask sum < 1.00 (buy-all arb): {arb}")


def report_coherence(games):
    _hdr("PART 1 - COHERENCE: is a period market a derivative of the game?")
    buckets = defaultdict(list)
    for g in games.values():
        for r in relations(g):
            buckets[r["name"]].append((r, g["key"]))
    print("""
  SIGNED deviation in POINTS over every game where both legs fit, on the
  UNSELECTED population - no filtering on the size or sign of the gap.
  Positive means the parts price HIGHER than the whole.""")
    print(f"\n    {'relation':<26}{'n':>5}{'mean':>8}{'med':>8}{'p10':>8}"
          f"{'p90':>8}{'sd':>7}{'|dev|>band':>12}")
    for name in ("Q1+Q2 = 1H", "Q1..Q4 = game", "Q3+Q4 = game-1H",
                 "teamA+teamB = game total",
                 "teamRef-teamOpp = spread [2-sided]",
                 "teamRef-teamOpp = spread [1-sided]"):
        rows = buckets.get(name)
        if not rows:
            continue
        devs = sorted(r["dev"] for r, _ in rows)
        n = len(devs)
        q = lambda p: devs[min(n - 1, int(p * n))]
        act = sum(1 for r, _ in rows if abs(r["dev"]) > r["band"])
        print(f"    {name:<36}{n:>5}{statistics.fmean(devs):>8.2f}"
              f"{statistics.median(devs):>8.2f}{q(.10):>8.2f}{q(.90):>8.2f}"
              f"{statistics.pstdev(devs):>7.2f}{act:>7} /{n:<4}")
    print("""
  The last column is the only actionable one: games where the deviation is
  larger than the summed bid-ask bands of its own legs. A deviation inside the
  band is a quote, not an edge.

  The spread relation is split because the split is the result. Where BOTH
  teams are quoted the margin curve is real data on both sides and the two
  legs agree to a third of a point. Where only one team is quoted the fit has
  to extrapolate through the entire left half of the distribution, and on a
  blowout it invents tens of points - the 1-sided row measures this file, not
  Kalshi, and is shown so that it is not mistaken for a finding.""")

    print(f"\n  ESTIMATOR CROSS-CHECK - the same relations under the near")
    print(f"  model-free estimator. If the sign and rough size survive, the")
    print(f"  finding is the market's; if they do not, it was the normal fit.")
    print(f"\n    {'relation':<26}{'n':>5}{'fit med':>10}{'np med':>10}{'agree':>8}")
    for name in ("Q1+Q2 = 1H", "Q1..Q4 = game", "Q3+Q4 = game-1H",
                 "teamA+teamB = game total"):
        rows = [r for r, _ in buckets.get(name, []) if r["np_dev"] is not None]
        if not rows:
            continue
        a = statistics.median([r["dev"] for r in rows])
        b = statistics.median([r["np_dev"] for r in rows])
        same = "yes" if (a * b > 0 and abs(a - b) < max(1.0, abs(a) * 0.75)) else "NO"
        print(f"    {name:<36}{len(rows):>5}{a:>10.2f}{b:>10.2f}{same:>8}")

    # The handful that clear their band are not scattered: they are the
    # mismatches. In a 36-point blowout the underdog's team-total ladder is
    # pinned near zero and the fit is badly conditioned, so this is more
    # likely the estimator running out of road than a tradeable gap.
    rows = buckets.get("teamA+teamB = game total", [])
    if rows:
        print("\n  teamA+teamB = game total, split on how lopsided the game is")
        print(f"    {'favourite margin':<20}{'n':>5}{'med dev':>10}{'sd':>8}{'|dev|>band':>12}")
        for lab, lo_m, hi_m in (("<= 14 pts", -99.0, 14.0),
                                ("14 - 28 pts", 14.0, 28.0),
                                ("> 28 pts", 28.0, 999.0)):
            sub = [(r, k) for r, k in rows
                   if r.get("fav_margin") is not None
                   and lo_m < r["fav_margin"] <= hi_m]
            if not sub:
                continue
            d = [r["dev"] for r, _ in sub]
            a = sum(1 for r, _ in sub if abs(r["dev"]) > r["band"])
            print(f"    {lab:<20}{len(d):>5}{statistics.median(d):>10.2f}"
                  f"{statistics.pstdev(d):>8.2f}{a:>7} /{len(d):<4}")

    for name in ("Q1..Q4 = game", "teamA+teamB = game total"):
        rows = sorted(buckets.get(name, []), key=lambda r: -abs(r[0]["dev"]))[:8]
        if not rows:
            continue
        print(f"\n  largest |deviation|, {name}")
        print(f"    {'game':<20}{'dev':>8}{'band':>8}   parts vs whole")
        for r, key in rows:
            print(f"    {key:<20}{r['dev']:>+8.2f}{r['band']:>8.2f}   {r['detail']}")
    return buckets


def report_sensitivity(conn):
    """The gate admits games; it must not choose the answer. This is the
    evidence for that claim, and it is why MAX_SPREAD is 0.35."""
    _hdr("GATE SENSITIVITY - does the spread filter pick the result?")
    print("""
  MAX_SPREAD decides which rungs count as quoted. If the median deviation moved
  with it, every number in this brief would be a filter artifact. It does not:
  only n moves.""")
    # Rebind THIS module's global, not a re-imported copy of it. Run as a
    # script the file is `__main__`, so `import research.cfb_coherence` loads a
    # SECOND, independent module object; assigning to its MAX_SPREAD changes
    # nothing here. That printed four identical rows and read as perfect
    # robustness, which is the most flattering possible way for a sweep to fail.
    global MAX_SPREAD
    keep = MAX_SPREAD
    names = ("teamA+teamB = game total", "Q1+Q2 = 1H", "Q1..Q4 = game",
             "teamRef-teamOpp = spread [2-sided]")
    print(f"\n    {'gate':>6}  " + "".join(f"{n[:24]:>26}" for n in names))
    for ms in (0.10, 0.20, 0.35, 0.60):
        MAX_SPREAD = ms
        b = defaultdict(list)
        for g in build(conn).values():
            for r in relations(g):
                b[r["name"]].append(r)
        cells = []
        for n in names:
            rows = b.get(n, [])
            if not rows:
                cells.append(f"{'n=0':>26}")
                continue
            d = [r["dev"] for r in rows]
            cells.append(f"{('n=%d  med %+.2f' % (len(d), statistics.median(d))):>26}")
        print(f"    {ms:>6.2f}  " + "".join(cells))
    MAX_SPREAD = keep


def report_notanswerable():
    _hdr("WHAT THIS CAPTURE CANNOT ANSWER")
    print("""
  1. ANY of Part 2. Calibration needs realized scores and line scores, and the
     capture holds none - `outcome_settlement` and `nfl_games` are empty by
     design, since brief 013 scoped settlement out. It needs the
     collegefootballdata.com key, which is not in `.env`.

  2. Whether these deviations PERSIST or are snapshot noise. Everything above
     is one instant, 10:00 ET on each game's own date. The capture holds the
     whole price path, and whether a gap closes toward kickoff is the
     difference between a stale quote and a structural one. Not settled here.

  3. Anything needing a re-parse of raw for the two-writer hours. 43 of 114
     shards are unreadable. The PARSED quotes above are unaffected - they
     reached SQLite before the archive was touched - but a field the parser
     dropped cannot be recovered from 09-11 12:00 ET onward.

  4. Execution beyond top of book. The capture stores best bid and ask only;
     depth capture was deliberately not run for CFB. A deviation that clears
     the quoted spread has NOT been shown to clear it at size, and on Kalshi
     that gap routinely exceeds 20%.

  5. Cross-venue. Polymarket carries the same game shapes but quotes an empty
     book as 0/1 with a meaningless 0.500 mid, so it cannot be pooled with
     Kalshi on price without a liquidity filter this brief does not build.""")


def export(games, buckets):
    d = os.environ.get("TEMP", ".")
    p1 = os.path.join(d, "cfb_coherence_relations.csv")
    with open(p1, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["relation", "game", "deviation_pts", "band_pts",
                    "actionable", "np_deviation_pts", "detail"])
        for name, rows in buckets.items():
            for r, key in rows:
                w.writerow([name, key, f"{r['dev']:.4f}", f"{r['band']:.4f}",
                            int(abs(r["dev"]) > r["band"]),
                            "" if r["np_dev"] is None else f"{r['np_dev']:.4f}",
                            r["detail"]])
    p2 = os.path.join(d, "cfb_coherence_fits.csv")
    with open(p2, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["game", "quantity", "E", "sigma", "rmse", "rungs",
                    "e_lo", "e_hi", "band", "E_np", "tail_share"])
        for g in games.values():
            for k, f in list(g["fits"].items()) + list(g["teams"].items()):
                w.writerow([g["key"], k, f"{f['mu']:.4f}", f"{f['sigma']:.4f}",
                            f"{f['rmse']:.5f}", f["n"], f"{f['e_lo']:.4f}",
                            f"{f['e_hi']:.4f}", f"{f['band']:.4f}",
                            "" if f["np"] is None else f"{f['np']:.4f}",
                            "" if f["tail_share"] is None else f"{f['tail_share']:.4f}"])
    print(f"\n  wrote {p1}\n  wrote {p2}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for flag in ("ladders", "overround", "coherence", "sensitivity",
                 "export", "all"):
        ap.add_argument(f"--{flag}", action="store_true")
    a = ap.parse_args()
    if not any(vars(a).values()):
        a.all = True
    conn = db()
    lo, hi = probe_window(conn)
    print(f"CFB capture {CFB_DB}")
    print(f"  window {datetime.fromtimestamp(lo, ET):%a %m-%d %H:%M} -> "
          f"{datetime.fromtimestamp(hi, ET):%a %m-%d %H:%M} ET"
          f"   snapshot {SNAPSHOT_HOUR:02d}:00 ET on each game's own date")
    games = build(conn)
    print(f"  {len(games)} games with at least one game-level ladder")
    buckets = {}
    if a.ladders or a.all:
        report_ladders(games)
    if a.overround or a.all:
        report_overround(games)
    if a.coherence or a.all:
        buckets = report_coherence(games)
    if a.sensitivity or a.all:
        report_sensitivity(conn)
    if a.all:
        report_notanswerable()
    if a.export:
        export(games, buckets or report_coherence(games))


if __name__ == "__main__":
    main()
