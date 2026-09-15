"""Brief 020 - does Kalshi's team-market price lag the sportsbook consensus by
more than it costs to cross?

    python -m research.consensus            # everything, week 1 2026
    python -m research.consensus --week 1

PRE-REGISTRATION - committed before any number below was computed.

  Markets. KXNFLSPREAD and KXNFLTOTAL against Odds API `spreads` / `totals`.
  MONEYLINE IS EXCLUDED: a US book voids a moneyline on a tie, so its de-vigged
  price is P(win | no tie), while KXNFLGAME has no tie leg and its tie rule is
  not in our data. The two are different claims by an unknown ~0.3pp, which is
  the same order as the effect under test.

  Matching. Exact lines only. A book spread "TEAM -L" is the claim "TEAM wins
  by over L", which is Kalshi's `KXNFLSPREAD` rung for TEAM at line L; a book
  total "Over L" is Kalshi's `KXNFLTOTAL` rung at L. Integer book lines have no
  half-integer Kalshi counterpart and drop. Nothing is interpolated. Teams
  resolve through `venues.mapping.team_abbr`, games through nflverse.

  Consensus. Per book, per snapshot, both sides at the same point, American
  odds -> implied -> de-vigged by MULTIPLICATIVE and by SHIN, separately. The
  consensus is the unweighted mean across books, and a line quoted by fewer
  than MIN_BOOKS=2 books drops. Source is the raw `odds_bulk` archive, because
  only the raw payload carries each book's `last_update`.

  Kalshi price. The last live quote at or before the snapshot's fetch time,
  both sides present, no older than MAX_STALE=660s. gap = Kalshi mid -
  consensus, in probability points.

  Phases. PRE-KICKOFF (fetch < nflverse kickoff) is the primary sample.
  IN-GAME (kickoff <= fetch <= kickoff + 4h) is run and reported separately.

  Threshold. 2.5pp, derived from costs in the brief, not fitted. The curve at
  2.0 / 3.0 / 4.0 is reported AS A CURVE.

  Executable test. Every observation with |gap| > 2.5pp crosses toward the
  consensus: gap > 0 buys NO, gap < 0 buys YES. Held to settlement (nflverse
  final score), one taker fee at the order size, series multiplier from
  `core.fees`. Arms:
    touch   the as-of quote's touch, fee charged at 10 contracts
    10      depth touch if it holds 10, else vwap_100 (an upper bound on the
            cost of 10 - flagged in the census)
    100     depth vwap_100        500   depth vwap_500
  Depth must be within 60s of the fetch and show the same touch as the quote
  (+-0.5c), or the observation is unexecutable at that size and counted. CLV is
  the side's closing mid (last quote strictly before kickoff) minus the entry
  price, gross of fee; it is undefined in-game.

  Persistence. (a) At the NEXT consensus snapshot of the same market, is the
  gap still beyond threshold with the same sign? (b) Holding the consensus at
  its snapshot value, how long until the Kalshi mid comes back within
  threshold - censored at the next snapshot or at the phase boundary. (b)
  assumes the books did not move between snapshots; (a) does not.

  Lead-lag, pre-kickoff, consecutive snapshots k -> k+1 of one market:
    L1  slope of (c[k+1] - c[k]) on gap[k]     > 0: books move toward Kalshi
    L2  slope of (m[k+1] - m[k]) on -gap[k]    > 0: Kalshi moves toward books
  Both are biased UPWARD by measurement noise in their own snapshot-k term
  (noise in c[k] sits in both gap[k] and -c[k]), so each slope is compared
  with the other, not with zero. Cross-correlations of the two change series
  at lags -1/0/+1 are descriptive.

  Intervals: block bootstrap over GAMES. Hypothesis count (intervals):
    mean gap, both methods                                    2
    L1, L2, both methods                                      4
    pre-kickoff PnL + CLV x 4 arms x 2 methods               16
    in-game PnL x 4 arms x 2 methods                          8
    curve: touch PnL pre-kickoff, multiplicative, 2.0/3.0/4.0 3
                                                        total 33
"""
import argparse
import bisect
import glob
import gzip
import json
import math
import os
import random
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.fees import fee_per_contract, series_multiplier  # noqa: E402
from research.implied import shin_devig  # noqa: E402
from venues.mapping import team_abbr  # noqa: E402

ET = ZoneInfo("America/New_York")

THRESHOLD = 0.025
CURVE = (0.020, 0.025, 0.030, 0.040)
MIN_BOOKS = 2
MAX_STALE = 660.0
DEPTH_WINDOW = 60.0
SAME_TOUCH = 0.005
LIVE_WINDOW = 4 * 3600
ARMS = ("touch", 10, 100, 500)
METHODS = ("multiplicative", "shin")
SERIES = ("KXNFLSPREAD", "KXNFLTOTAL")
BOOT = 2000
SEED = 20
TTK_BUCKETS = ((72 * 3600, ">72h"), (24 * 3600, "24-72h"), (6 * 3600, "6-24h"),
               (3600, "1-6h"), (0, "0-1h"))


# =============================================================================
# pure pieces
# =============================================================================

def american_to_prob(odds):
    o = float(odds)
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def devig(p_yes, p_no, method):
    if method == "multiplicative":
        s = p_yes + p_no
        return p_yes / s if s > 0 else None
    if method == "shin":
        return shin_devig(p_yes, p_no)
    raise ValueError(method)


def book_pairs(event, abbr=team_abbr):
    """One bookmaker-snapshot payload -> [(key, p_yes_raw, p_no_raw, book, last_update)].

    key is ("spread", TEAM, L) for "TEAM wins by over L" - the FAVOURITE's side
    of the book pair - or ("total", None, L) for "over L". A pick'em (point 0)
    has no Kalshi rung and is skipped.
    """
    out = []
    for b in event.get("bookmakers") or []:
        for m in b.get("markets") or []:
            oc = m.get("outcomes") or []
            if len(oc) != 2 or any(o.get("point") is None for o in oc):
                continue
            lu = _iso(m.get("last_update") or b.get("last_update"))
            if m.get("key") == "totals":
                over = [o for o in oc if o.get("name") == "Over"]
                under = [o for o in oc if o.get("name") == "Under"]
                if len(over) != 1 or len(under) != 1 or over[0]["point"] != under[0]["point"]:
                    continue
                out.append((("total", None, float(over[0]["point"])),
                            american_to_prob(over[0]["price"]),
                            american_to_prob(under[0]["price"]), b.get("key"), lu))
            elif m.get("key") == "spreads":
                a, d = sorted(oc, key=lambda o: o["point"])
                if a["point"] != -d["point"] or a["point"] == 0:
                    continue
                team = abbr(a.get("name") or "")
                if not team:
                    continue
                out.append((("spread", team, float(-a["point"])),
                            american_to_prob(a["price"]),
                            american_to_prob(d["price"]), b.get("key"), lu))
    return out


def consensus(pairs, method, min_books=MIN_BOOKS):
    """{key: (mean de-vigged P(yes), n_books)} for keys with >= min_books books.
    Also returns the keys that fell short, so the drop is counted not hidden."""
    by = defaultdict(dict)
    for key, py, pn, book, _lu in pairs:
        p = devig(py, pn, method)
        if p is not None:
            by[key][book] = p
    ok, short = {}, []
    for key, books in by.items():
        if len(books) >= min_books:
            ok[key] = (statistics.fmean(books.values()), len(books))
        else:
            short.append(key)
    return ok, short


def kalshi_key(market_id, subject, line):
    if line is None:
        return None
    if market_id.startswith("KXNFLTOTAL-"):
        return ("total", None, float(line))
    if market_id.startswith("KXNFLSPREAD-"):
        team = team_abbr(re.sub(r"\s+wins by.*$", "", subject or ""))
        return ("spread", team, float(line)) if team else None
    return None


def settle(key, home, away, home_score, away_score):
    if home_score is None or away_score is None:
        return None
    kind, team, line = key
    if kind == "total":
        return 1.0 if home_score + away_score > line else 0.0
    margin = home_score - away_score if team == home else away_score - home_score
    return 1.0 if margin > line else 0.0


def side_for(gap):
    """Cross TOWARD the consensus: Kalshi rich (gap > 0) buys NO."""
    return "no" if gap > 0 else "yes"


def touch_price(side, bid, ask):
    return ask if side == "yes" else 1.0 - bid


def ttk_bucket(secs):
    if secs <= 0:
        return "in-game"
    for floor, name in TTK_BUCKETS:
        if secs > floor:
            return name
    return "0-1h"


def walk_close(quotes_after, c, thr, horizon, max_stale=MAX_STALE, start=None):
    """Kalshi-side persistence with the consensus held at `c`.

    `quotes_after` is [(ts, bid, ask)] strictly after `start`, ascending.
    Returns (seconds, reason): reason is "closed", "censored" (reached the
    horizon still open) or "went stale" (no quote for max_stale)."""
    last = start
    for ts, bid, ask in quotes_after:
        if ts > horizon:
            break
        if last is not None and ts - last > max_stale:
            return last - start, "went stale"
        last = ts
        if bid is None or ask is None:
            continue
        if abs((bid + ask) / 2 - c) <= thr:
            return ts - start, "closed"
    return horizon - start, "censored"


def ols_slope(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    if vx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx


def corr(xs, ys):
    if len(xs) < 3:
        return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (vx * vy) if vx and vy else float("nan")


def boot(rows, stat, block="game", n=BOOT, seed=SEED):
    """Block bootstrap of ANY statistic over games. `stat(rows) -> float|None`."""
    by = defaultdict(list)
    for r in rows:
        by[r[block]].append(r)
    keys = list(by)
    point = stat(rows)
    if point is None or len(keys) < 2:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(n):
        sample = [r for _ in keys for r in by[keys[rng.randrange(len(keys))]]]
        v = stat(sample)
        if v is not None:
            draws.append(v)
    draws.sort()
    return {"est": point, "lo": draws[int(0.025 * len(draws))],
            "hi": draws[int(0.975 * len(draws))], "n": len(rows), "games": len(keys)}


def mean_of(field):
    def f(rows):
        v = [r[field] for r in rows if r.get(field) is not None]
        return statistics.fmean(v) if v else None
    return f


# =============================================================================
# loading
# =============================================================================

def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def load_games(c, season, week):
    g = {}
    for gid, dv, kick, home, away, hs, as_ in c.execute(
            "SELECT game_id, data_version, kickoff_ts, home_team, away_team, "
            "home_score, away_score FROM nfl_games WHERE season=? AND week=? "
            "ORDER BY game_id, data_version", (season, week)):
        g[gid] = {"game": gid, "kick": kick, "home": home, "away": away,
                  "hs": hs, "as": as_}
    return g


def load_snapshots(games, raw_dir=None):
    """Raw odds_bulk -> [{T, game, pairs}] plus book-lag seconds."""
    raw_dir = raw_dir or os.path.join(config.RAW_DIR, "oddsapi")
    by_pair = {(v["away"], v["home"]): k for k, v in games.items()}
    snaps, lag, seen = [], [], set()
    for fn in sorted(glob.glob(os.path.join(raw_dir, "*", "*.jsonl.gz"))):
        with gzip.open(fn, "rt", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("endpoint", "").split(":")[0] != "odds_bulk":
                    continue
                for ev in rec.get("payload") or []:
                    gid = by_pair.get((team_abbr(ev.get("away_team") or ""),
                                       team_abbr(ev.get("home_team") or "")))
                    if gid is None:
                        continue
                    ct = _iso(ev.get("commence_time"))
                    if ct is None or abs(ct - games[gid]["kick"]) > 36 * 3600:
                        continue
                    k = (gid, round(rec["ts"]))
                    if k in seen:
                        continue
                    seen.add(k)
                    pairs = book_pairs(ev)
                    lag += [rec["ts"] - p[4] for p in pairs if p[4]]
                    snaps.append({"T": rec["ts"], "game": gid, "pairs": pairs})
    snaps.sort(key=lambda s: s["T"])
    return snaps, lag


def load_kalshi(c, games):
    """Kalshi SPREAD/TOTAL markets per game, with their live quote series."""
    ev_game = {}
    for eid, in c.execute(
            "SELECT DISTINCT event_id FROM markets WHERE venue='kalshi' "
            "AND market_id >= 'KXNFLSPREAD-' AND market_id < 'KXNFLSPREAD.'"):
        suffix = eid.split("-", 1)[1]
        teams = {team_abbr(re.sub(r"\s+wins by.*$", "", s or "")) for (s,) in c.execute(
            "SELECT DISTINCT subject FROM markets WHERE venue='kalshi' AND event_id=?", (eid,))}
        try:
            day = datetime.strptime(suffix[:7], "%y%b%d").date()
        except ValueError:
            continue
        for gid, g in games.items():
            kday = datetime.fromtimestamp(g["kick"], ET).date()
            if {g["home"], g["away"]} == teams and abs(kday - day) <= timedelta(days=1):
                ev_game[suffix] = gid
    markets = defaultdict(list)
    for mid, eid, subject, line in c.execute(
            "SELECT market_id, event_id, subject, line FROM markets WHERE venue='kalshi' "
            "AND ((market_id >= 'KXNFLSPREAD-' AND market_id < 'KXNFLSPREAD.') "
            "  OR (market_id >= 'KXNFLTOTAL-' AND market_id < 'KXNFLTOTAL.'))"):
        gid = ev_game.get(eid.split("-", 1)[1])
        key = kalshi_key(mid, subject, line)
        if gid is None or key is None:
            continue
        q = c.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' "
                      "AND market_id=? AND source='live' ORDER BY ts", (mid,)).fetchall()
        markets[gid].append({"market": mid, "key": key, "q": q,
                             "qts": [r[0] for r in q]})
    return markets


def asof(mk, T, strict=False):
    i = (bisect.bisect_left if strict else bisect.bisect_right)(mk["qts"], T) - 1
    return mk["q"][i] if i >= 0 else None


def depth(c, market_id, side, T):
    r = c.execute(
        "SELECT ts, touch_price, touch_size, vwap_100, vwap_500 FROM market_depth "
        "WHERE venue='kalshi' AND market_id=? AND side=? AND ts <= ? "
        "ORDER BY ts DESC LIMIT 1", (market_id, "buy_" + side, T)).fetchone()
    return r if r and T - r[0] <= DEPTH_WINDOW else None


# =============================================================================
# building observations
# =============================================================================

def build(c, games, snaps, kalshi, method):
    obs, drops = [], Counter()
    book_keys, matched_keys, kalshi_seen = set(), set(), set()
    for s in snaps:
        g = games[s["game"]]
        T = s["T"]
        if T < g["kick"]:
            phase = "pre"
        elif T <= g["kick"] + LIVE_WINDOW:
            phase = "in"
        else:
            drops["snapshot after the in-game window"] += 1
            continue
        cons, short = consensus(s["pairs"], method)
        drops["book line with < 2 books (snapshot-lines)"] += len(short)
        for key in cons:
            book_keys.add((s["game"], key))
        for mk in kalshi.get(s["game"], []):
            if mk["key"] not in cons:
                continue
            matched_keys.add((s["game"], mk["key"]))
            kalshi_seen.add(mk["market"])
            q = asof(mk, T)
            if q is None or T - q[0] > MAX_STALE:
                drops["kalshi quote missing or stale at the fetch"] += 1
                continue
            if q[1] is None or q[2] is None:
                drops["kalshi book one-sided at the fetch"] += 1
                continue
            cval, nb = cons[mk["key"]]
            m = (q[1] + q[2]) / 2
            obs.append({"game": s["game"], "market": mk["market"], "key": mk["key"],
                        "T": T, "phase": phase, "ttk": g["kick"] - T, "c": cval,
                        "books": nb, "m": m, "bid": q[1], "ask": q[2],
                        "age": T - q[0], "gap": m - cval, "mk": mk})
    all_kalshi = {mk["market"] for v in kalshi.values() for mk in v}
    census = {
        "kalshi markets in matched games": len(all_kalshi),
        "kalshi markets never matched to an exact book line": len(all_kalshi - kalshi_seen),
        "book (game, line) keys": len(book_keys),
        "book keys with no kalshi rung": len(book_keys - matched_keys),
        "  of which integer lines": sum(1 for _, k in book_keys - matched_keys
                                        if float(k[2]).is_integer()),
    }
    return obs, drops, census


def next_obs_index(obs):
    """market -> its observations in time order, per phase."""
    seq = defaultdict(list)
    for o in obs:
        seq[(o["market"], o["phase"])].append(o)
    for v in seq.values():
        v.sort(key=lambda o: o["T"])
    return seq


def executions(c, games, obs, thr, arms=ARMS):
    rows = []
    for o in obs:
        if abs(o["gap"]) <= thr:
            continue
        g = games[o["game"]]
        side = side_for(o["gap"])
        payoff_yes = settle(o["key"], g["home"], g["away"], g["hs"], g["as"])
        payoff = None if payoff_yes is None else (payoff_yes if side == "yes" else 1 - payoff_yes)
        close = asof(o["mk"], g["kick"], strict=True) if o["phase"] == "pre" else None
        close_side = None
        if close and close[1] is not None and close[2] is not None:
            cm = (close[1] + close[2]) / 2
            close_side = cm if side == "yes" else 1 - cm
        _mm, taker_m = series_multiplier(o["market"])
        quote_touch = touch_price(side, o["bid"], o["ask"])
        d = depth(c, o["market"], side, o["T"]) if any(a != "touch" for a in arms) else None
        for arm in arms:
            r = {"game": o["game"], "market": o["market"], "phase": o["phase"],
                 "arm": arm, "gap": o["gap"], "note": ""}
            if arm == "touch":
                price, size = quote_touch, 10
            else:
                size = arm
                if d is None:
                    r["note"] = "no depth within 60s"
                    rows.append(r)
                    continue
                if d[1] is None or abs(d[1] - quote_touch) > SAME_TOUCH:
                    r["note"] = "depth shows a different book"
                    rows.append(r)
                    continue
                if arm == 10:
                    if d[2] is not None and d[2] >= 10:
                        price = d[1]
                    else:
                        price, r["note"] = d[3], "10 priced at vwap_100 (upper bound)"
                else:
                    price = d[3] if arm == 100 else d[4]
                if price is None:
                    r["note"] = f"book cannot fill {arm}"
                    rows.append(r)
                    continue
            if not (0 < price < 1):
                r["note"] = "price outside (0,1)"
                rows.append(r)
                continue
            fee = fee_per_contract(price, size, "taker", taker_m)
            r["price"] = price
            r["pnl"] = None if payoff is None else payoff - price - fee
            r["clv"] = None if close_side is None else close_side - price
            rows.append(r)
    return rows


# =============================================================================
# reports
# =============================================================================

def _hdr(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")


MIN_GAMES_TO_READ = 5


def readable(res):
    """An interval resampled over fewer than MIN_GAMES_TO_READ games is not
    read, whatever it excludes. Added AFTER the first run (labelling only - no
    number changes): the pre-kickoff arms rested on 2-3 games that all won, and
    a bootstrap over three identical wins returns a tight interval that
    'excludes zero' and means nothing."""
    return bool(res) and res["games"] >= MIN_GAMES_TO_READ


def fmt_iv(res, scale=100.0, unit="pp"):
    if not res:
        return "n/a (fewer than 2 games)"
    star = "*" if res["lo"] > 0 or res["hi"] < 0 else " "
    tail = "" if readable(res) else f"  <- {res['games']} games: too few to read"
    return (f"{scale * res['est']:+6.2f}{unit} [{scale * res['lo']:+6.2f}, "
            f"{scale * res['hi']:+6.2f}]{star} n={res['n']} games={res['games']}{tail}")


def dist_line(vals):
    if not vals:
        return "n=0"
    v = [100 * x for x in vals]
    return (f"n={len(v):>6}  p10 {q(v, .1):+5.2f}  p25 {q(v, .25):+5.2f}  median "
            f"{statistics.median(v):+5.2f}  p75 {q(v, .75):+5.2f}  p90 {q(v, .9):+5.2f}  "
            f"|gap|>2.5pp {100 * sum(abs(x) > 2.5 for x in v) / len(v):5.1f}%")


def book_lag_by_phase(snaps, games):
    """Book staleness split by phase, plus how far books quoting ONE line at
    ONE fetch disagree with each other. Added after the first run: in-game,
    books on the same line differed by up to ~35pp, which is one book live and
    another stale - a clock, not a price."""
    lag, spread = defaultdict(list), defaultdict(list)
    for s in snaps:
        k = games[s["game"]]["kick"]
        ph = "pre" if s["T"] < k else "in" if s["T"] <= k + LIVE_WINDOW else None
        if ph is None:
            continue
        lag[ph] += [s["T"] - p[4] for p in s["pairs"] if p[4]]
        by = defaultdict(list)
        for key, py, pn, _b, _lu in s["pairs"]:
            by[key].append(devig(py, pn, "multiplicative"))
        spread[ph] += [max(v) - min(v) for v in by.values() if len(v) >= 2]
    out = []
    for ph, label in (("pre", "pre-kickoff"), ("in", "in-game")):
        L, S = lag[ph], [100 * x for x in spread[ph]]
        if L:
            out.append(f"      {label:<12} book lag median {statistics.median(L):.0f}s p90 {q(L, .9):.0f}s"
                       f"  | max-min across books on one line: median {statistics.median(S):.1f}pp"
                       f" p90 {q(S, .9):.1f}pp  n={len(S)}")
    return "\n".join(out)


def report(season, week):
    c = ro()
    games = load_games(c, season, week)
    snaps, lag = load_snapshots(games)
    kalshi = load_kalshi(c, games)
    tests = []

    _hdr("ITEM 1 - MATCHING (exact lines only)")
    print(f"  games {len(games)}  |  consensus snapshots {len(snaps)}  |  "
          f"kalshi SPREAD/TOTAL markets {sum(len(v) for v in kalshi.values())} "
          f"in {len(kalshi)} games")
    print("  MONEYLINE EXCLUDED - a book voids it on a tie, KXNFLGAME has no tie leg.")
    per_method = {}
    for method in METHODS:
        obs, drops, census = build(c, games, snaps, kalshi, method)
        per_method[method] = obs
        print(f"\n  [{method}]")
        for k, v in census.items():
            print(f"    {k:<55}{v:>7}")
        for k, v in drops.items():
            print(f"    dropped: {k:<46}{v:>7}")
        print(f"    observations kept (market x snapshot)                  {len(obs):>7}"
              f"   pre {sum(o['phase'] == 'pre' for o in obs)}  in-game "
              f"{sum(o['phase'] == 'in' for o in obs)}")
    obs = per_method["multiplicative"]
    ages = [o["age"] for o in obs]
    lag.sort()
    spacing = []
    for v in next_obs_index(obs).values():
        spacing += [b["T"] - a["T"] for a, b in zip(v, v[1:])]
    print(f"""
  ALIGNMENT. Two clocks, both reported:
    kalshi as-of quote age at the fetch   median {statistics.median(ages):.0f}s  p90 {q(ages, .9):.0f}s
      (an UPPER bound - quotes are written on change plus a 5-min heartbeat,
       so an unchanged book reads older than it is)
    fetch minus each book's last_update   median {statistics.median(lag):.0f}s  p90 {q(lag, .9):.0f}s  max {lag[-1]:.0f}s
{book_lag_by_phase(snaps, games)}
    spacing between consensus snapshots   median {statistics.median(spacing) / 60:.1f} min  p10 {q(spacing, .1) / 60:.1f}  p90 {q(spacing, .9) / 60:.1f}
  No lead-lag below the snapshot spacing is resolvable, whatever the clocks say.""")

    _hdr("ITEM 2 - DE-VIG: multiplicative vs Shin on the same book pairs")
    mult = {(o["market"], o["T"]): o["c"] for o in per_method["multiplicative"]}
    diffs = [100 * (o["c"] - mult[(o["market"], o["T"])]) for o in per_method["shin"]
             if (o["market"], o["T"]) in mult]
    print(f"  shin - multiplicative, pp: median {statistics.median(diffs):+.3f}  "
          f"p1 {q(diffs, .01):+.3f}  p99 {q(diffs, .99):+.3f}  max |.| "
          f"{max(abs(x) for x in diffs):.3f}  n={len(diffs)}")
    books = Counter(o["books"] for o in obs)
    print(f"  books per consensus line: {dict(sorted(books.items()))}  (no Pinnacle - us region)")

    _hdr("ITEM 3 - THE GAP  (Kalshi mid - de-vigged consensus)")
    for method in METHODS:
        ob = per_method[method]
        print(f"\n  [{method}]")
        print(f"    all          {dist_line([o['gap'] for o in ob])}")
        for b in [n for _, n in TTK_BUCKETS] + ["in-game"]:
            print(f"    {b:<12} {dist_line([o['gap'] for o in ob if ttk_bucket(o['ttk']) == b])}")
        res = boot(ob, mean_of("gap"))
        tests.append((f"mean gap [{method}]", res))
        print(f"    mean gap (sign: + = Kalshi above books)  {fmt_iv(res)}")
        print("    CURVE - share beyond threshold (a curve, not a result): " + "  ".join(
            f"{100 * t:.1f}pp {100 * sum(abs(o['gap']) > t for o in ob) / len(ob):.1f}%"
            for t in CURVE))

    print("\n  PERSISTENCE at 2.5pp")
    for method in METHODS:
        ob = per_method[method]
        seq = next_obs_index(ob)
        for phase in ("pre", "in"):
            same = flip = inside = last = 0
            gaps_dt = []
            durations, reasons = [], Counter()
            for (mid, ph), v in seq.items():
                if ph != phase:
                    continue
                for i, o in enumerate(v):
                    if abs(o["gap"]) <= THRESHOLD:
                        continue
                    nxt = v[i + 1] if i + 1 < len(v) else None
                    if nxt is None:
                        last += 1
                    else:
                        gaps_dt.append(nxt["T"] - o["T"])
                        if abs(nxt["gap"]) > THRESHOLD and (nxt["gap"] > 0) == (o["gap"] > 0):
                            same += 1
                        elif abs(nxt["gap"]) > THRESHOLD:
                            flip += 1
                        else:
                            inside += 1
                    g = games[o["game"]]
                    horizon = nxt["T"] if nxt else (g["kick"] if phase == "pre"
                                                    else g["kick"] + LIVE_WINDOW)
                    mk = o["mk"]
                    j = bisect.bisect_right(mk["qts"], o["T"])
                    d, why = walk_close(mk["q"][j:], o["c"], THRESHOLD, horizon, start=o["T"])
                    durations.append(d)
                    reasons[why] += 1
            n_open = same + flip + inside + last
            print(f"\n    [{method} / {'pre-kickoff' if phase == 'pre' else 'in-game'}] "
                  f"open observations {n_open}")
            if not n_open:
                continue
            k = same + flip + inside
            if k:
                print(f"      (a) at the next snapshot (median {statistics.median(gaps_dt) / 60:.1f} min later): "
                      f"still open same sign {same} ({100 * same / k:.0f}%), flipped {flip}, "
                      f"back inside {inside} ({100 * inside / k:.0f}%);  {last} had no next snapshot")
            b = Counter()
            for d in durations:
                b["<1 min" if d < 60 else "1-5 min" if d < 300 else "5-30 min" if d < 1800
                  else "30 min-3h" if d < 10800 else ">3h"] += 1
            print(f"      (b) kalshi-side, consensus held: median {statistics.median(durations) / 60:.1f} min  "
                  f"p90 {q(durations, .9) / 60:.1f} min  | " + "  ".join(
                      f"{kk} {b[kk]}" for kk in ("<1 min", "1-5 min", "5-30 min", "30 min-3h", ">3h")))
            print(f"          ended by: {dict(reasons)}")

    print("\n  DEPTH at the touch while the gap is open (side toward the consensus)")
    for phase in ("pre", "in"):
        opened = [o for o in obs if abs(o["gap"]) > THRESHOLD and o["phase"] == phase]
        got, sizes, fill = 0, [], Counter()
        for o in opened:
            side = side_for(o["gap"])
            d = depth(c, o["market"], side, o["T"])
            if d is None or d[1] is None or abs(d[1] - touch_price(side, o["bid"], o["ask"])) > SAME_TOUCH:
                continue
            got += 1
            sizes.append(d[2] or 0)
            fill["10 at touch"] += (d[2] or 0) >= 10
            fill["100"] += d[3] is not None
            fill["500"] += d[4] is not None
        print(f"    [{phase}] open {len(opened)}, depth verified {got}"
              + (f": touch size median {statistics.median(sizes):.0f}, p10 {q(sizes, .1):.0f}; "
                 f"fillable 10 at touch {fill['10 at touch']}, 100 {fill['100']}, 500 {fill['500']}"
                 if got else ""))

    _hdr("ITEM 3 - LEAD-LAG (pre-kickoff, consecutive snapshots of one market)")
    print("  L1 > 0: books move toward Kalshi (Kalshi leads).  L2 > 0: Kalshi moves"
          "\n  toward books (Kalshi lags). Both carry the same upward noise bias, so"
          "\n  compare L1 with L2.")
    for method in METHODS:
        pairs = []
        seq = next_obs_index(per_method[method])
        chains = defaultdict(list)
        for (mid, ph), v in seq.items():
            if ph != "pre":
                continue
            for a, b in zip(v, v[1:]):
                pairs.append({"game": a["game"], "gap": a["gap"], "dc": b["c"] - a["c"],
                              "dm": b["m"] - a["m"]})
                chains[mid].append((b["m"] - a["m"], b["c"] - a["c"]))
        l1 = boot(pairs, lambda rs: ols_slope([r["gap"] for r in rs], [r["dc"] for r in rs]))
        l2 = boot(pairs, lambda rs: ols_slope([-r["gap"] for r in rs], [r["dm"] for r in rs]))
        tests += [(f"L1 [{method}]", l1), (f"L2 [{method}]", l2)]
        print(f"\n  [{method}]  pairs {len(pairs)}")
        print(f"    L1 books toward Kalshi   {fmt_iv(l1, 1, '')}")
        print(f"    L2 Kalshi toward books   {fmt_iv(l2, 1, '')}")
        for lagk in (-1, 0, 1):
            xs, ys = [], []
            for ch in chains.values():
                for i in range(len(ch)):
                    j = i + lagk
                    if 0 <= j < len(ch):
                        xs.append(ch[i][0])
                        ys.append(ch[j][1])
            label = {-1: "books change BEFORE kalshi's", 0: "same interval",
                     1: "books change AFTER kalshi's"}[lagk]
            print(f"    corr(d kalshi[k], d books[k{lagk:+d}])  {corr(xs, ys):+.3f}  n={len(xs)}  ({label})")

    _hdr("ITEM 4 - THE EXECUTABLE TEST at 2.5pp (cross toward the consensus, hold)")
    for method in METHODS:
        rows = executions(c, games, per_method[method], THRESHOLD)
        for phase, label in (("pre", "PRE-KICKOFF (primary)"), ("in", "IN-GAME (separate)")):
            print(f"\n  [{method}] {label}")
            for arm in ARMS:
                rs = [r for r in rows if r["phase"] == phase and r["arm"] == arm]
                ok = [r for r in rs if "price" in r]
                notes = Counter(r["note"] for r in rs if r["note"])
                pnl = boot([r for r in ok if r["pnl"] is not None], mean_of("pnl"))
                tests.append((f"PnL {phase} {arm} [{method}]", pnl))
                line = (f"    {str(arm):>5}  triggers {len(rs):>4}  priced {len(ok):>4}  "
                        f"markets {len({r['market'] for r in ok}):>3}  games {len({r['game'] for r in ok}):>2}")
                print(line)
                print(f"           PnL/contract  {fmt_iv(pnl)}")
                if phase == "pre":
                    clv = boot([r for r in ok if r["clv"] is not None], mean_of("clv"))
                    tests.append((f"CLV pre {arm} [{method}]", clv))
                    print(f"           CLV to close  {fmt_iv(clv)}")
                if notes:
                    print("           " + "; ".join(f"{k}: {v}" for k, v in notes.items()))
    print("\n  CURVE - touch PnL, pre-kickoff, multiplicative (a curve, not a result)")
    for t in CURVE:
        rows = [r for r in executions(c, games, per_method["multiplicative"], t, arms=("touch",))
                if r["phase"] == "pre" and r.get("pnl") is not None]
        res = boot(rows, mean_of("pnl"))
        if t != THRESHOLD:
            tests.append((f"curve PnL pre touch {t}", res))
        print(f"    {100 * t:.1f}pp  {fmt_iv(res)}")

    _hdr("HYPOTHESIS COUNT")
    ran = sum(1 for _, r in tests if r)
    excl = [(n, r) for n, r in tests if r and (r["lo"] > 0 or r["hi"] < 0)]
    print(f"  pre-registered intervals {len(tests)} (33 planned); estimable {ran}; "
          f"excluding zero {len(excl)}, of which on >= {MIN_GAMES_TO_READ} games "
          f"{sum(1 for _, r in excl if readable(r))}")
    for name, r in tests:
        if not r:
            print(f"    not estimable: {name}")
    for name, r in excl:
        print(f"    excludes zero: {name:<34} games={r['games']}"
              + ("" if readable(r) else "  (too few to read)"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    a = ap.parse_args()
    report(a.season, a.week)


if __name__ == "__main__":
    main()
