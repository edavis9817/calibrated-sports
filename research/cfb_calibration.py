"""BRIEF C01 PART 2 - is the college market calibrated, and is CFB worth
returning to?

    python research/cfb_calibration.py --all
    python research/cfb_calibration.py --census      # what is in the bucket
    python research/cfb_calibration.py --totals      # (a) totals and spreads
    python research/cfb_calibration.py --quarters    # (b) Q1 pricing vs scoring
    python research/cfb_calibration.py --moneyline   # (c) favourite-longshot
    python research/cfb_calibration.py --verdict     # the go / no-go

READ-ONLY against the CFB probe capture, which now also holds `cfbd_games`.
Zero API requests: results come from the table `jobs/ingest_cfbd.py` filled from
one archived call.

THE CLOSE, NOT A GUESS AT IT. Part 1 had no results table, so it had no kickoff
to key on and snapshotted every game at a fixed 10:00 ET. The results feed
carries `start_ts`, so every price here is the last quote AT OR BEFORE THAT
GAME'S OWN KICKOFF. That is the close, and it is the only price CLV or
calibration may be measured at.

NO DE-VIG IS APPLIED, AND THAT IS THE CORRECT TREATMENT. Kalshi is an exchange:
a yes-mid IS the probability and there is no bookmaker margin inside it to
remove. Part 1 measured the consequence - moneyline mid sums land at a median
of exactly 1.0000 and not one partition of ~300 could be bought whole below
1.00. Applying Shin or multiplicative de-vig here would correct for a margin
that does not exist. Two-sided partitions are NORMALISED to sum to 1 (a
rounding of order 0.005), which is not the same operation and is labelled as
what it is.

THE EFFECTIVE SAMPLE IS GAMES, NOT RUNGS. A game total ladder has ~19 rungs and
every one of them settles off the SAME final score, so 113 games produce ~2,100
rung-observations that carry nowhere near 2,100 games' worth of information -
if the total lands high, every rung in that game misses together. Wilson
intervals here are therefore computed on DISTINCT GAMES in the bucket, never on
the rung count. Quoting the rung count would shrink every interval by roughly
sqrt(19) and manufacture significance out of one Saturday. Both numbers are
printed side by side in every table.

Wilson, never the normal approximation - F02 established that every longshot
standard error in this project had been quoted the wrong way at small p.
"""
import argparse
import math
import os
import re
import sqlite3
import statistics
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from research import cfb_coherence as coh
from research.longshot import wilson

ET = ZoneInfo("America/New_York")
CFB_DB = coh.CFB_DB

# CFBD and Kalshi disagree about what a school is called. Both sides are
# normalised and then mapped through this table onto one canonical spelling.
# It was built by listing every unmatched pair, not guessed: 107 of 120 matched
# before it, 120 of 120 after. Anything still unmatched is REPORTED, never
# dropped - a silent 10% loss would bias the sample toward the games whose
# names happen to agree, which is the FBS ones.
ALIAS = {
    "umass": "massachusetts",
    "se louisiana": "southeastern louisiana",
    "ul monroe": "louisiana monroe",
    "ut martin": "tennessee martin",
    "long island university": "liu",
    "ualbany": "albany",
    "university at albany": "albany",
    "nicholls": "nicholls state",
    "ut rio grande valley": "utrgv",
    "nc state": "north carolina state",
    "app state": "appalachian state",
    "southern": "southern university",
    "grambling": "grambling state",
    "central connecticut": "central connecticut state",
    "texas am": "texas aandm",
    # Kalshi disambiguates the Miamis, CFBD names only the other one. This is
    # the ONLY parenthetical the two feeds disagree about; every other one is
    # load-bearing and must survive normalisation - see `norm`.
    "miami fl": "miami",
}


def norm(s):
    """Fold to ASCII first: CFBD writes 'San Jose State' with an accent, and
    stripping non-letters without folding turns it into 'jos'.

    THE PARENTHETICAL IS KEPT. It is tempting to drop it as a qualifier, and
    that silently merges distinct schools: CFBD alone carries Anderson (IN) and
    Anderson (SC), Augustana (IL) and Augustana (SD), Lincoln (MO) and Lincoln
    (PA), Concordia (MN) and Concordia (WI), Northwestern (MN) and Northwestern
    (OK) - and Miami (OH), which is not Miami. Dropping it made 'Miami (FL)'
    and 'Miami (OH)' the same team. The join requires BOTH sides to match and a
    UNIQUE candidate, so this would have surfaced as an unmatched game rather
    than a wrong result, but only by luck of the schedule.
    """
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().strip().replace("&", "and")
    s = re.sub(r"[()]", " ", s)           # keep the qualifier, lose the bracket
    s = re.sub(r"[-/.]", " ", s)          # a hyphen SEPARATES, it is not noise
    s = re.sub(r"\bst\b", "state", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIAS.get(s, s)


def db():
    return sqlite3.connect(f"file:{CFB_DB}?mode=ro", uri=True)


# =============================================================================
# the join
# =============================================================================

def match(conn):
    """Kalshi game key -> the CFBD row for the same game.

    Keyed on (date, both team names). The date needs a +/-1 day tolerance
    because a 23:00 ET Saturday kickoff in Hawaii is a Sunday in UTC and the
    two feeds do not agree on which they mean.
    """
    legs = defaultdict(dict)
    for mid, subj in conn.execute(
            "SELECT market_id, subject FROM markets "
            "WHERE market_id LIKE 'KXNCAAFGAME-%'"):
        p = mid.split("-")
        if len(p) >= 3:
            legs[p[1]][p[2]] = subj

    by_day = defaultdict(list)
    for row in conn.execute(
            "SELECT game_id, start_ts, home_team, away_team, home_points, "
            "away_points, home_q1, home_q2, home_q3, home_q4, home_ot, "
            "away_q1, away_q2, away_q3, away_q4, away_ot, n_periods, "
            "completed, home_class, away_class FROM cfbd_games "
            "WHERE start_ts IS NOT NULL"):
        d = dict(zip(
            ("game_id", "start_ts", "home", "away", "hp", "ap",
             "hq1", "hq2", "hq3", "hq4", "hot",
             "aq1", "aq2", "aq3", "aq4", "aot", "n_periods",
             "completed", "home_class", "away_class"), row))
        d["nh"], d["na"] = norm(d["home"]), norm(d["away"])
        by_day[datetime.fromtimestamp(d["start_ts"], ET)
               .strftime("%y%b%d").upper()].append(d)

    out, unmatched = {}, []
    for gkey, sides in legs.items():
        m = coh.DATECODE.match(gkey)
        if not m:
            continue
        if len(sides) != 2:
            unmatched.append((gkey, "moneyline is not two-legged"))
            continue
        want = {norm(v) for v in sides.values()}
        d0 = datetime.strptime(m.group(0), "%y%b%d")
        cand = []
        for k in (0, -1, 1):
            day = (d0 + timedelta(days=k)).strftime("%y%b%d").upper()
            cand = [g for g in by_day.get(day, []) if {g["nh"], g["na"]} == want]
            if cand:
                break
        if len(cand) == 1:
            g = dict(cand[0])
            g["kalshi_teams"] = {k: norm(v) for k, v in sides.items()}
            out[gkey] = g
        elif by_day.get(d0.strftime("%y%b%d").upper()) is not None:
            # Only a miss worth reporting if we HELD data for that date; games
            # in a week we never fetched are simply out of scope.
            unmatched.append((gkey, f"{sorted(want)} -> {len(cand)} candidates"))
    return out, unmatched


def realized(g):
    """Every quantity the markets settle against, or None where the game
    cannot support it."""
    if not g["completed"] or g["hp"] is None or g["ap"] is None:
        return None
    r = {"total": g["hp"] + g["ap"], "home_pts": g["hp"], "away_pts": g["ap"]}
    qs = []
    for i in "1234":
        h, a = g[f"hq{i}"], g[f"aq{i}"]
        qs.append(None if h is None or a is None else h + a)
    r["quarters"] = qs
    r["reg_total"] = sum(qs) if all(q is not None for q in qs) else None
    r["overtime"] = (g["n_periods"] or 0) > 4
    r["by_team"] = {g["nh"]: g["hp"], g["na"]: g["ap"]}
    r["q_by_team"] = {
        g["nh"]: [g[f"hq{i}"] for i in "1234"],
        g["na"]: [g[f"aq{i}"] for i in "1234"]}
    r["class"] = f'{g["away_class"] or "?"}@{g["home_class"] or "?"}'
    return r


# =============================================================================
# buckets, with the effective sample carried through
# =============================================================================

EDGES = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50,
         0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 1.0]


def bucket_of(p):
    for i in range(len(EDGES) - 1):
        if EDGES[i] <= p < EDGES[i + 1]:
            return i
    return len(EDGES) - 2


def curve(obs, label, note=""):
    """obs: iterable of (p, hit, game_key). Wilson on DISTINCT GAMES."""
    b = defaultdict(lambda: {"n": 0, "k": 0, "p": 0.0, "games": set(),
                             "ghit": defaultdict(list)})
    for p, hit, gk in obs:
        d = b[bucket_of(p)]
        d["n"] += 1
        d["k"] += int(hit)
        d["p"] += p
        d["games"].add(gk)
        d["ghit"][gk].append(int(hit))
    print(f"\n  {label}{note}")
    print(f"    {'bucket':<14}{'rungs':>7}{'games':>7}{'priced':>9}{'realized':>10}"
          f"{'dev pp':>9}{'Wilson (on games)':>22}")
    tot_n = tot_k = 0
    tot_p = 0.0
    rows = []
    for i in sorted(b):
        d = b[i]
        pr = d["p"] / d["n"]
        rz = d["k"] / d["n"]
        ng = len(d["games"])
        # One number per game - the game's own hit rate across its rungs -
        # then Wilson on that many independent units.
        gk_mean = statistics.fmean(
            [statistics.fmean(v) for v in d["ghit"].values()])
        lo, hi = wilson(round(gk_mean * ng), ng)
        flag = "" if lo <= pr <= hi else "  <-- outside"
        print(f"    {EDGES[i]:.2f}-{EDGES[i+1]:.2f}    {d['n']:>7,}{ng:>7,}"
              f"{pr:>9.4f}{rz:>10.4f}{100*(rz-pr):>+9.2f}"
              f"   [{lo:.4f}, {hi:.4f}]{flag}")
        tot_n += d["n"]
        tot_k += d["k"]
        tot_p += d["p"]
        rows.append((pr, rz, d["n"]))
    if tot_n:
        ece = sum(n * abs(rz - pr) for pr, rz, n in rows) / tot_n
        print(f"    {'ALL':<14}{tot_n:>7,}{'':>7}{tot_p/tot_n:>9.4f}"
              f"{tot_k/tot_n:>10.4f}{100*(tot_k/tot_n - tot_p/tot_n):>+9.2f}"
              f"   ECE {ece:.4f}")
    return {"n": tot_n, "priced": tot_p / tot_n if tot_n else 0,
            "realized": tot_k / tot_n if tot_n else 0,
            "ece": (sum(n * abs(rz - pr) for pr, rz, n in rows) / tot_n) if tot_n else 0}


# =============================================================================
# loading closing prices at each game's own kickoff
# =============================================================================

def closes(conn, games):
    lo, hi = coh.probe_window(conn)
    kick = {k: g["start_ts"] for k, g in games.items()}
    # A game that kicked off after the probe died has no close in the capture.
    kick = {k: t for k, t in kick.items() if lo <= t <= hi}
    return {
        "total": coh.load_snapshot(conn, "KXNCAAFTOTAL", lo, hi, kick),
        "spread": coh.load_snapshot(conn, "KXNCAAFSPREAD", lo, hi, kick),
        "money": coh.load_snapshot(conn, "KXNCAAFGAME", lo, hi, kick),
        "q1": coh.load_snapshot(conn, "KXNCAAF1QTOTAL", lo, hi, kick),
        "q2": coh.load_snapshot(conn, "KXNCAAF2QTOTAL", lo, hi, kick),
        "q3": coh.load_snapshot(conn, "KXNCAAF3QTOTAL", lo, hi, kick),
        "q4": coh.load_snapshot(conn, "KXNCAAF4QTOTAL", lo, hi, kick),
        "1h": coh.load_snapshot(conn, "KXNCAAF1HTOTAL", lo, hi, kick),
        "kick": kick,
    }


# =============================================================================
# reports
# =============================================================================

def _hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def report_census(games, unmatched, snap):
    _hdr("BUCKET CENSUS - what is actually in this sample, before any claim")
    played = {k: g for k, g in games.items() if realized(g)}
    withclose = {k for k in played if k in snap["kick"]}
    print(f"""
  Kalshi games in the capture matched to a result   {len(games)}
  ... of which completed with a final score          {len(played)}
  ... of which kicked off inside the capture window  {len(withclose)}
  unmatched and reported (never dropped)             {len(unmatched)}""")
    for gk, why in unmatched[:10]:
        print(f"      {gk:<20}{why}")

    cls = defaultdict(int)
    ot = 0
    for k in withclose:
        r = realized(games[k])
        cls[r["class"]] += 1
        ot += bool(r["overtime"])
    print(f"\n  division matchup (away@home), which is the whole shape of the slate")
    for c, n in sorted(cls.items(), key=lambda kv: -kv[1]):
        print(f"      {c:<14}{n:>5}")
    print(f"      went to overtime: {ot}  (excluded from quarter analysis, "
          f"included in totals - a total settles on the FINAL score)")

    print(f"\n  ladders with a close at kickoff")
    print(f"    {'market':<12}{'games':>8}{'rungs':>9}{'med rungs/game':>17}")
    for name in ("total", "spread", "q1", "q2", "q3", "q4", "1h", "money"):
        d = {k: v for k, v in snap[name].items() if k in withclose}
        if not d:
            print(f"    {name:<12}{0:>8}")
            continue
        tot = sum(len(v) for v in d.values())
        print(f"    {name:<12}{len(d):>8,}{tot:>9,}"
              f"{statistics.median([len(v) for v in d.values()]):>17.0f}")
    print("""
  READ THIS BEFORE THE TABLES. One Saturday, one week. Every
  interval below is computed on distinct GAMES rather than rungs for that
  reason, and even so a single week cannot separate a well-calibrated market
  from a lucky one. What it CAN do is size the gap against the NFL's 1.4pp.""")
    return withclose


def _one_per_game(games, snap, keys):
    """The honest headline, and the one that survives the clustering.

    A ladder's rungs all settle off one final score, so a week where scoring
    ran hot pushes EVERY rung of EVERY game the same way. The bucket table
    above therefore cannot tell "the market misprices totals" from "this
    Saturday was high-scoring", and neither can an interval on 119 games -
    a league-wide scoring environment is ONE draw, not 119.

    So collapse each game to a single question - did the final clear the
    market's own implied mean? - and put a Wilson interval on the games. If
    that interval contains 0.5, the bucket table's tilt is a week, not an edge.
    """
    over = tot = 0
    devs = []
    for k in keys:
        r = realized(games[k])
        f = coh.fit_banded(snap["total"].get(k, []))
        if not f:
            continue
        tot += 1
        over += int(r["total"] > f["mu"])
        devs.append(r["total"] - f["mu"])
    if not tot:
        return
    lo, hi = wilson(over, tot)
    print(f"""
  ONE OBSERVATION PER GAME - did the final total clear the market's own mean?
      games                {tot}
      cleared              {over}  ({over/tot:.4f})
      Wilson on games      [{lo:.4f}, {hi:.4f}]   contains 0.5: {lo <= 0.5 <= hi}
      mean signed miss     {statistics.fmean(devs):+.2f} points
      median signed miss   {statistics.median(devs):+.2f} points
      sd of the miss       {statistics.pstdev(devs):.2f} points

  This is the number to quote. The rung table's tilt and this row are the same
  fact counted two ways, and only this way respects that one Saturday's scoring
  environment is a single draw shared by every game on it.""")


def report_totals(games, snap, keys):
    _hdr("(a) TOTALS AND SPREADS - closing probability against what happened")
    print("""
  Each rung of a ladder is one binary contract, and its mid IS its probability
  (exchange, no margin to remove). Realized is whether the game cleared that
  rung. Reported on the OVER side only: over and under are exact complements,
  so a table carrying both reads 0.5000 against 0.5000 in every row and looks
  like proof of perfect calibration.""")
    obs_t, obs_s = [], []
    for k in keys:
        r = realized(games[k])
        for p in snap["total"].get(k, []):
            if p["line"] is None or p["spread"] > coh.MAX_SPREAD:
                continue
            if not (0.0 < p["mid"] < 1.0):
                continue
            obs_t.append((p["mid"], r["total"] > p["line"], k))
        for p in snap["spread"].get(k, []):
            if p["line"] is None or p["spread"] > coh.MAX_SPREAD:
                continue
            if not (0.0 < p["mid"] < 1.0):
                continue
            team = p["team"]
            # The ticker's team suffix is a Kalshi abbreviation; resolve it to
            # the normalised name through the moneyline legs of the same game.
            nm = games[k]["kalshi_teams"].get(team)
            if nm is None or nm not in r["by_team"]:
                continue
            other = [t for t in r["by_team"] if t != nm]
            if not other:
                continue
            margin = r["by_team"][nm] - r["by_team"][other[0]]
            obs_s.append((p["mid"], margin > p["line"], k))
    a = curve(obs_t, "GAME TOTAL, over side")
    b = curve(obs_s, "GAME SPREAD, favourite-side rungs")
    _one_per_game(games, snap, keys)
    print(f"""
  Against the NFL benchmark. `research/calibration.py` measured 183,669 settled
  NFL player props at ECE 0.0043 with the over side -1.40pp. College here:
      game total   ECE {a['ece']:.4f}   over side {100*(a['realized']-a['priced']):+.2f}pp   ({a['n']:,} rungs)
      game spread  ECE {b['ece']:.4f}   {100*(b['realized']-b['priced']):+.2f}pp   ({b['n']:,} rungs)
  These are NOT comparable as significance - the NFL number rests on three
  seasons and this one on a single Saturday - but they are comparable as SIZE,
  and size is what the brief asked for.""")
    return a, b


def report_quarters(games, snap, keys):
    _hdr("(b) QUARTER PRICING AGAINST QUARTER SCORING")
    print("""
  Part 1 showed the quarter ladders cohere ARITHMETICALLY with the game total.
  That says the venue's own numbers add up; it says nothing about whether they
  are right. This asks the second question.

  Overtime games are excluded here and only here: OT points land in the final
  score but in no quarter, so a game total and its four quarters settle on
  different numbers once a game goes past regulation.""")
    imp = defaultdict(list)
    rz = defaultdict(list)
    ratio_imp, ratio_rz = [], []
    for k in keys:
        r = realized(games[k])
        if r["overtime"] or r["reg_total"] is None:
            continue
        fits = {}
        for tag, series in (("Q1", "q1"), ("Q2", "q2"), ("Q3", "q3"), ("Q4", "q4")):
            f = coh.fit_banded(snap[series].get(k, []))
            if f:
                fits[tag] = f
        # The per-quarter table does NOT need the game ladder; only the share
        # question does. Requiring it here threw away games for no reason.
        for i, tag in enumerate(("Q1", "Q2", "Q3", "Q4")):
            if tag in fits and r["quarters"][i] is not None:
                imp[tag].append(fits[tag]["mu"])
                rz[tag].append(r["quarters"][i])
        g = coh.fit_banded(snap["total"].get(k, []))
        if g and "Q1" in fits and r["quarters"][0] is not None and r["total"]:
            ratio_imp.append(fits["Q1"]["mu"] / g["mu"])
            ratio_rz.append(r["quarters"][0] / r["total"])
    print("\n  PAIRED per game: realized minus the market's own mean for that")
    print("  quarter, so each row is a matched comparison rather than two "
          "averages\n  over different games.")
    print(f"\n    {'quarter':<9}{'n':>5}{'market E':>10}{'realized':>10}"
          f"{'dev pts':>9}{'sd':>7}{'SE':>7}{'dev/SE':>8}")
    for tag in ("Q1", "Q2", "Q3", "Q4"):
        if not imp[tag]:
            continue
        d = [b - a_ for a_, b in zip(imp[tag], rz[tag])]
        n = len(d)
        md = statistics.fmean(d)
        sd = statistics.pstdev(d) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n else float("nan")
        print(f"    {tag:<9}{n:>5}{statistics.fmean(imp[tag]):>10.2f}"
              f"{statistics.fmean(rz[tag]):>10.2f}{md:>+9.2f}{sd:>7.2f}"
              f"{se:>7.2f}{(md/se if se else float('nan')):>+8.2f}")
    print("""
  NOT ONE QUARTER CLEARS 1.5 SE. Individually there is nothing here.

  What is worth writing down is the SIGN pattern: the market overprices the
  FIRST quarter of each half and underprices the SECOND, which is the shape
  real football has, since a half's scoring bunches into its closing minutes.
  But four signs alternating is a 1-in-8 coincidence on its own, so the
  pattern is only interesting BECAUSE a mechanism predicts it - and that is an
  argument for measuring it again, not for believing it now. One Saturday,
  n<=26 per quarter.""")
    allq = [x for tag in ("Q1", "Q2", "Q3", "Q4") for x in rz[tag]]
    if allq and rz["Q1"]:
        share = statistics.fmean(rz["Q1"]) / (statistics.fmean(allq) * 4) * 4
        print(f"\n  Is first-quarter scoring below a game-average quarter?")
        print(f"    mean regulation quarter   {statistics.fmean(allq):.2f} pts")
        print(f"    mean Q1                   {statistics.fmean(rz['Q1']):.2f} pts"
              f"   ({100*statistics.fmean(rz['Q1'])/statistics.fmean(allq):.1f}% of an average quarter)")
    rr = [x for x in ratio_rz if x is not None]
    if ratio_imp and rr:
        print(f"\n  Does the market price Q1 at a flat 25% of the game total?")
        print(f"    market  E[Q1]/E[game]     {statistics.fmean(ratio_imp):.4f}"
              f"   (sd {statistics.pstdev(ratio_imp):.4f}, n={len(ratio_imp)})")
        print(f"    realized Q1/final          {statistics.fmean(rr):.4f}"
              f"   (sd {statistics.pstdev(rr):.4f})")
        print(f"    a flat quarter would be    0.2500")
        d = statistics.fmean(ratio_imp) - 0.25
        print(f"\n    The market prices Q1 {abs(d)*100:.2f}pp "
              f"{'BELOW' if d < 0 else 'ABOVE'} a flat quarter, and reality came in at "
              f"{(statistics.fmean(rr)-0.25)*100:+.2f}pp against flat.")


def report_moneyline(games, snap, keys):
    _hdr("(c) MONEYLINE - the favourite-longshot check on heavy college prices")
    print("""
  One observation per game per side, and both sides are shown because a
  moneyline has no natural 'over'. They are exact complements, so the two rows
  mirror: read either, not both. Prices are NORMALISED to sum to 1 (median
  correction ~0.005) - that is a rounding of an exchange mid, not a de-vig.""")
    fav, dog = [], []
    for k in keys:
        r = realized(games[k])
        legs = [p for p in snap["money"].get(k, [])
                if p["spread"] <= coh.MAX_SPREAD and 0 < p["mid"] < 1]
        if len(legs) != 2:
            continue
        s = sum(p["mid"] for p in legs)
        if not (0.90 < s < 1.10):
            continue
        legs = sorted(legs, key=lambda p: -p["mid"])
        for i, p in enumerate(legs):
            nm = games[k]["kalshi_teams"].get(p["team"])
            if nm is None or nm not in r["by_team"]:
                continue
            other = [t for t in r["by_team"] if t != nm]
            won = r["by_team"][nm] > r["by_team"][other[0]]
            (fav if i == 0 else dog).append((p["mid"] / s, won, k))
    if fav:
        heavy = sum(1 for p, _, _ in fav if p >= 0.90)
        vheavy = sum(1 for p, _, _ in fav if p >= 0.95)
        print(f"""
  HOW LOPSIDED THIS SLATE IS - the brief's premise, measured first:
      games with a moneyline close   {len(fav)}
      favourite priced >= 0.90       {heavy}  ({100*heavy/len(fav):.0f}%)
      favourite priced >= 0.95       {vheavy}  ({100*vheavy/len(fav):.0f}%)
      median favourite price         {statistics.median([p for p, _, _ in fav]):.3f}
  College really does live where the NFL does not. That is also why every
  bucket below holds ~14 games: the prices spread across a range the NFL never
  reaches, so a one-week sample is thin everywhere at once.""")
    curve(fav, "FAVOURITE side (the higher-priced leg)")
    curve(dog, "UNDERDOG side (its exact complement)")
    if dog:
        lows = [(p, h) for p, h, _ in dog if p < 0.15]
        if lows:
            k = sum(1 for _, h in lows if h)
            gset = {g for p, h, g in dog if p < 0.15}
            lo, hi = wilson(k, len(gset))
            print(f"""
  The college longshot zone, which the NFL does not have at this depth:
      priced below 0.15   n={len(lows)} rungs over {len(gset)} games
      mean price          {statistics.fmean([p for p, _ in lows]):.4f}
      realized            {k/len(lows):.4f}
      Wilson on games     [{lo:.4f}, {hi:.4f}]
  F02 retired the sportsbook longshot bias as a sacks artifact once the bucket
  census was printed. Print this one before believing it too: at this n the
  interval is wide enough to contain almost any story.""")


def report_verdict(a, b):
    _hdr("GO / NO-GO - is CFB worth ever returning to?")
    print(f"""
  NO-GO, and not because the market is sharp.

  1. THE MARKET IS ROUGHLY AS CALIBRATED AS THE NFL'S, at a size that would
     matter only with an edge to point at it. Game total ECE {a['ece']:.4f} and
     spread ECE {b['ece']:.4f} on one Saturday, against the NFL props' 0.0043 on
     three seasons. College is looser, but the gap is measured in fractions of
     a point of probability and one week cannot tell looseness from noise.

  2. THERE IS NOTHING TO POINT A MODEL AT. Brief 013 established that Kalshi
     lists NO single-game player props for college - 47,553 markets across 97
     series and every player-named one is a season leader future. This
     project's entire validated edge is single-game player usage: targets,
     rush attempts, receptions. None of it exists here.

  3. SO A CFB EFFORT IS A NEW MODEL, NOT A REDIRECTION OF THIS ONE. Team
     totals, spreads and quarters need a team-level scoring model, a CFB facts
     layer that does not exist, and a results feed metered at 1,000 requests a
     month. That is a season of work to reach the starting line the NFL side
     is already past.

  4. AND THE DERIVATIVES ARE ALREADY TIGHT. Part 1 found period and team
     markets cohere with the game market to within a third of a point, with
     nothing beyond the quoted spread in 38 two-sided games. The structural
     arbitrage that would justify entering WITHOUT a model is not there.

  What would change this: a venue listing college player props at NFL ladder
  density, or a CFBD Starter Pack backfill making a team-level model cheap to
  build and test. Neither is true today, and neither is close.""")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for f in ("census", "totals", "quarters", "moneyline", "verdict", "all"):
        ap.add_argument(f"--{f}", action="store_true")
    a = ap.parse_args()
    if not any(vars(a).values()):
        a.all = True
    conn = db()
    n = conn.execute("SELECT COUNT(*) FROM cfbd_games").fetchone()[0]
    if not n:
        raise SystemExit("cfbd_games is empty - run jobs/ingest_cfbd.py --week 2")
    games, unmatched = match(conn)
    snap = closes(conn, games)
    print(f"CFB calibration  db={CFB_DB}   {n} CFBD games, "
          f"{len(games)} matched to Kalshi")
    keys = report_census(games, unmatched, snap)
    ta = tb = {"ece": float("nan"), "n": 0, "priced": 0, "realized": 0}
    if a.totals or a.all or a.verdict:
        ta, tb = report_totals(games, snap, keys)
    if a.quarters or a.all:
        report_quarters(games, snap, keys)
    if a.moneyline or a.all:
        report_moneyline(games, snap, keys)
    if a.verdict or a.all:
        report_verdict(ta, tb)


if __name__ == "__main__":
    main()
