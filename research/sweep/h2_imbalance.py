"""Brief 022 H2 - does order-book imbalance at the YES touch predict the next
mid move, and is the move bigger than the cost of crossing? NFL week 1 only.

    python -m research.sweep.h2_imbalance

Pre-registered in docs/briefs/022-preregistration.md (ae3895b), section H2.

    I = (bid_size - ask_size) / (bid_size + ask_size)     at the YES touch
    yes bid size = market_depth buy_no touch_size   (a resting YES bid is a NO offer)
    yes ask size = market_depth buy_yes touch_size  (both from the same capture ts)

Future mid change at t+10s / t+60s / t+300s from the as-of live quote (both
sides, age <= 660s), minus the as-of mid at t. Tier: pre-kickoff (t < kickoff)
or in-game (kickoff <= t <= kickoff + 4h); later instants drop.

RESOLUTION FLOOR. Quotes are written on change plus a 5-minute heartbeat, and
pre-kickoff a market is polled every 15-60s. A 10s-ahead as-of lookup therefore
usually returns the SAME quote row as at t, which reads as "no move" whether or
not the price moved between polls. The share of same-row lookups is printed per
cell - read the 10s and 60s pre-kickoff slopes against it.

Tests (search): slope of dmid on I per series x tier x horizon (30), and the
mean over the top decile of |I| of (sign(I) x dmid - (half-spread + taker fee
at 100 contracts)) per series x tier x horizon (30). Game block bootstrap on
per-game sufficient statistics, so a resample never touches a raw row.
"""
import bisect
import functools
import math
import os
import sys
from array import array
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.fees import fee_per_contract, series_multiplier  # noqa: E402
from research.sweep import common as S  # noqa: E402
from venues.mapping import team_abbr  # noqa: E402

SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLGAME")
TIERS = ("pre", "in")
HORIZONS = (10, 60, 300)
MAX_AGE = 660.0
LIVE_WINDOW = 4 * 3600
TOP_DECILE = 0.90
REG_PATH = os.path.join(S.ROOT, "research", "sweep", "results", "h2.jsonl")


# =============================================================================
# pure pieces (tested)
# =============================================================================

def imbalance(bid_size, ask_size):
    """None unless BOTH sides rest size: a one-sided book has no mid to move."""
    if bid_size is None or ask_size is None or bid_size <= 0 or ask_size <= 0:
        return None
    return (bid_size - ask_size) / (bid_size + ask_size)


def tier(t, kick, live_window=LIVE_WINDOW):
    if t < kick:
        return "pre"
    if t <= kick + live_window:
        return "in"
    return None


def asof_index(qts, t, max_age=MAX_AGE):
    i = bisect.bisect_right(qts, t) - 1
    return i if i >= 0 and t - qts[i] <= max_age else None


def load_games(c, season=2026, week=1):
    """game_id -> (kickoff_ts, home, away) at the latest data_version."""
    g = {}
    for gid, _dv, k, home, away in c.execute(
            "SELECT game_id, data_version, kickoff_ts, home_team, away_team FROM nfl_games "
            "WHERE season=? AND week=? ORDER BY game_id, data_version", (season, week)):
        g[gid] = (k, home, away)
    return g


def event_game(event_id, games, abbr=team_abbr):
    """KXNFLSPREAD-26SEP13BUFHOU -> 2026_01_BUF_HOU. The team codes are
    concatenated with no separator, so every split is tried and only one that
    resolves BOTH halves to the game's exact team pair is accepted."""
    suffix = event_id.split("-", 1)[1] if "-" in event_id else event_id
    try:
        day = datetime.strptime(suffix[:7], "%y%b%d").date()
    except ValueError:
        return None
    teams = suffix[7:]
    hits = set()
    for gid, (k, home, away) in games.items():
        if abs((datetime.fromtimestamp(k, S.ET).date() - day).days) > 1:
            continue
        for i in range(2, len(teams) - 1):
            x, y = abbr(teams[:i]), abbr(teams[i:])
            if x and y and {x, y} == {home, away}:
                hits.add(gid)
    return hits.pop() if len(hits) == 1 else None


def week1_markets(c, series):
    sql, params = S.week1_filter_sql()
    out = []
    for mid, eid in c.execute(
            f"SELECT market_id, event_id FROM markets WHERE venue='kalshi' "
            f"AND market_id >= ? AND market_id < ? AND {sql}", [series + "-", series + "."] + params):
        S.assert_search_set(mid, 0.0)          # dated ticker: raises on any week-2 date
        out.append((mid, eid))
    return out


def game_suffstats(game, x, y, n_games):
    """Per-game n, sum x, sum y, sum xx, sum xy - enough to rebuild an OLS slope
    over any resample of games."""
    g = np.asarray(game)
    return (np.bincount(g, minlength=n_games),
            np.bincount(g, weights=x, minlength=n_games),
            np.bincount(g, weights=y, minlength=n_games),
            np.bincount(g, weights=x * x, minlength=n_games),
            np.bincount(g, weights=x * y, minlength=n_games))


def slope_rows(stats):
    n, sx, sy, sxx, sxy = stats
    # Python scalars, not numpy: the registry JSON-encodes what comes back.
    return [{"game": i, "n": int(n[i]), "sx": float(sx[i]), "sy": float(sy[i]),
             "sxx": float(sxx[i]), "sxy": float(sxy[i])}
            for i in range(len(n)) if n[i] > 0]


def slope_stat(rows):
    N = sum(r["n"] for r in rows)
    sx = sum(r["sx"] for r in rows)
    sy = sum(r["sy"] for r in rows)
    sxx = sum(r["sxx"] for r in rows)
    sxy = sum(r["sxy"] for r in rows)
    den = N * sxx - sx * sx
    if N < 3 or den <= 0:
        return None
    return (N * sxy - sx * sy) / den


def mean_rows(game, v, n_games):
    g = np.asarray(game)
    n = np.bincount(g, minlength=n_games)
    s = np.bincount(g, weights=v, minlength=n_games)
    return [{"game": i, "n": int(n[i]), "s": float(s[i])} for i in range(n_games) if n[i] > 0]


def mean_stat(rows):
    n = sum(r["n"] for r in rows)
    return None if n == 0 else sum(r["s"] for r in rows) / n


@functools.lru_cache(maxsize=None)
def crossing_fee(mid, taker_m):
    return fee_per_contract(mid, 100, "taker", taker_m)


# =============================================================================
# collection
# =============================================================================

class Cell:
    def __init__(self):
        self.game = array("i")
        self.I = array("d")
        self.cost = array("d")
        self.d = {h: array("d") for h in HORIZONS}
        self.same = {h: array("b") for h in HORIZONS}


def collect(c, games):
    gidx = {gid: i for i, gid in enumerate(sorted(games))}
    cells = defaultdict(Cell)
    census = Counter()
    for s in SERIES:
        _maker, taker = series_multiplier(s)
        for mid, eid in week1_markets(c, s):
            gid = event_game(eid, games)
            if gid is None:
                census["market: no mapped game"] += 1
                continue
            kick = games[gid][0]
            q = c.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' "
                          "AND market_id=? AND source='live' AND best_bid IS NOT NULL "
                          "AND best_ask IS NOT NULL ORDER BY ts", (mid,)).fetchall()
            if not q:
                census["market: no two-sided quote"] += 1
                continue
            qts = [r[0] for r in q]
            books = defaultdict(dict)
            for ts, side, size in c.execute(
                    "SELECT ts, side, touch_size FROM market_depth WHERE venue='kalshi' "
                    "AND market_id=?", (mid,)):
                books[ts][side] = size
            census["markets"] += 1
            for t in sorted(books):
                tr = tier(t, kick)
                if tr is None:
                    census["instant: after the in-game window"] += 1
                    continue
                I = imbalance(books[t].get("buy_no"), books[t].get("buy_yes"))
                if I is None:
                    census[f"instant: one-sided or empty book ({tr})"] += 1
                    continue
                i0 = asof_index(qts, t)
                if i0 is None:
                    census[f"instant: no fresh quote at t ({tr})"] += 1
                    continue
                bid, ask = q[i0][1], q[i0][2]
                m0 = (bid + ask) / 2
                if ask < bid or not (0 < m0 < 1):
                    census["instant: crossed book or mid at 0/1"] += 1
                    continue
                cell = cells[(s, tr)]
                cell.game.append(gidx[gid])
                cell.I.append(I)
                cell.cost.append((ask - bid) / 2 + crossing_fee(round(m0, 4), taker))
                for h in HORIZONS:
                    i1 = asof_index(qts, t + h)
                    if i1 is None:
                        cell.d[h].append(math.nan)
                        cell.same[h].append(0)
                    else:
                        cell.d[h].append((q[i1][1] + q[i1][2]) / 2 - m0)
                        cell.same[h].append(1 if i1 == i0 else 0)
                census[f"instant kept ({tr})"] += 1
    return cells, census, len(gidx)


# =============================================================================
# report
# =============================================================================

def fmt(res, scale=100.0):
    if not res:
        return "n/a"
    star = "*" if res["lo"] > 0 or res["hi"] < 0 else " "
    read = "" if res["games"] >= S.MIN_GAMES_TO_READ else " (too few games)"
    return (f"{scale * res['est']:+8.3f} [{scale * res['lo']:+8.3f}, {scale * res['hi']:+8.3f}]{star} "
            f"p={res['p']:.2g} n={res['n']:,} g={res['games']}{read}")


def run():
    if os.path.exists(REG_PATH):
        os.remove(REG_PATH)
    reg = S.Registry(REG_PATH)
    c = S.live_ro()
    games = load_games(c)
    cells, census, n_games = collect(c, games)

    print("=" * 100)
    print("H2 - ORDER BOOK IMBALANCE, NFL WEEK 1 (search set only; week 2 and CFB not read)")
    print("=" * 100)
    for k, v in sorted(census.items()):
        print(f"  {k:<48}{v:>10,}")

    print("\n  RESOLUTION FLOOR: share of as-of lookups at t+h that return the SAME quote row"
          "\n  as at t (reads as zero move), and share of kept dmid exactly 0.")
    for (s, tr), cell in sorted(cells.items()):
        parts = []
        for h in HORIZONS:
            d = np.frombuffer(cell.d[h], dtype=float)
            ok = ~np.isnan(d)
            same = np.frombuffer(cell.same[h], dtype=np.int8)[ok]
            parts.append(f"{h:>3}s same-row {100 * same.mean():5.1f}% zero {100 * (d[ok] == 0).mean():5.1f}%")
        print(f"    {s:<12} {tr:<3} " + " | ".join(parts))

    print("\n  SLOPE of dmid on I (pp of mid per unit of I; + = the move follows the imbalance)")
    slopes = {}
    for s in SERIES:
        for tr in TIERS:
            cell = cells.get((s, tr))
            for h in HORIZONS:
                name = f"{s}|{tr}|{h}s"
                if cell is None or len(cell.I) == 0:
                    reg.add("H2 slope", name, None, unit="pp per unit I")
                    print(f"    {name:<24} n/a")
                    continue
                I = np.frombuffer(cell.I, dtype=float)
                d = np.frombuffer(cell.d[h], dtype=float)
                g = np.frombuffer(cell.game, dtype=np.int32)
                ok = ~np.isnan(d)
                rows = slope_rows(game_suffstats(g[ok], I[ok], d[ok], n_games))
                res = S.boot(rows, slope_stat)
                if res:
                    res = dict(res, n=int(ok.sum()))
                slopes[name] = res
                reg.add("H2 slope", name, res, unit="pp per unit I", scale=100.0)
                print(f"    {name:<24} {fmt(res)}")

    print("\n  ECONOMICS - top decile of |I| per series x tier:"
          "\n  mean of sign(I) x dmid, mean crossing cost (half-spread + taker fee @100), and"
          "\n  the registered test: signed move MINUS cost (pp). > 0 would pay.")
    any_pays = []
    for s in SERIES:
        for tr in TIERS:
            cell = cells.get((s, tr))
            for h in HORIZONS:
                name = f"{s}|{tr}|{h}s"
                if cell is None or len(cell.I) == 0:
                    reg.add("H2 economic", name, None)
                    print(f"    {name:<24} n/a")
                    continue
                I = np.frombuffer(cell.I, dtype=float)
                d = np.frombuffer(cell.d[h], dtype=float)
                cost = np.frombuffer(cell.cost, dtype=float)
                g = np.frombuffer(cell.game, dtype=np.int32)
                cut = np.quantile(np.abs(I), TOP_DECILE)
                m = (np.abs(I) >= cut) & ~np.isnan(d)
                signed = np.sign(I[m]) * d[m]
                net = signed - cost[m]
                res = S.boot(mean_rows(g[m], net, n_games), mean_stat)
                if res:
                    res = dict(res, n=int(m.sum()))
                reg.add("H2 economic", name, res, scale=100.0,
                        note=f"|I| >= {cut:.3f}; gross {100 * signed.mean():+.3f}pp cost {100 * cost[m].mean():.3f}pp")
                if res and res["lo"] > 0:
                    any_pays.append(name)
                print(f"    {name:<24} |I|>={cut:.2f} gross {100 * signed.mean():+7.3f}  cost {100 * cost[m].mean():6.3f}  "
                      f"net {fmt(res)}")
    print("\n  VERDICT: " + ("the net interval excludes zero ABOVE zero in: " + ", ".join(any_pays)
                             if any_pays else
                             "in no series x tier x horizon does the predicted move exceed half-spread + fee."))
    print(f"\n  registry: {REG_PATH}")
    return slopes


if __name__ == "__main__":
    run()
