"""Brief 022 Part 3 - the open scan, NFL WEEK 1 ONLY (axes 1-5).

    python -m research.sweep.scan          # all five axes -> results/scan.jsonl

Rules: docs/briefs/022-preregistration.md (committed ae3895b). Every interval
computed here is registered, role `search`, population `nfl_wk1`. Nothing in
this module may read CFB or a week-2 row: every Kalshi market and every trade
print passes `common.assert_search_set`, and the sportsbook consensus is built
only for nflverse 2026 week-1 games.

Implementation choices, stated because they are choices:
  * Intervals use a game block bootstrap over per-game SUFFICIENT STATISTICS
    (sums), with the same seed and draw order as `common.boot`, so estimates and
    intervals are identical to it (tested) at a cost that does not scale with
    rows. p-values are the pre-registered z = est / bootstrap SE.
  * Price-path grids are capped at GRID_CAP evenly spaced instants per
    market x tier x horizon.
  * Key-number pricing re-centres the historical margin distribution by a single
    shift delta (0.1-point steps, |P - p_cons| minimised, ties -> smallest
    |delta|). A fractional shift moves the key numbers with it; this arm
    carries interpolation error that 020's exact-match arm did not.
"""
import argparse
import bisect
import glob
import math
import os
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402
from core.fees import fee_per_contract, series_multiplier  # noqa: E402
from research.sweep import common as S  # noqa: E402

REG_PATH = os.path.join(S.ROOT, "research", "sweep", "results", "scan.jsonl")
TRADES_DB = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "trades_m01.db")
SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLGAME")
PROPS = ("KXNFLREC", "KXNFLRSHATT")
MAX_AGE = 660.0
DEPTH_WINDOW = 60.0
LIVE_WINDOW = 4 * 3600
HORIZONS = (60, 600, 3600)
GRID_CAP = 150
TICKET = 10
THRESH_PP = 2.5
DIST_BUCKETS = ((0, 3, "0-3"), (3, 7, "3-7"), (7, 10, "7-10"), (10, 1e9, "10+"))


# =============================================================================
# statistics on sufficient statistics (identical to common.boot, tested)
# =============================================================================

def _boot_groups(groups, est_fn, n_rows, n=S.BOOT, seed=S.SEED):
    keys = list(groups)
    if len(keys) < 2:
        return None
    width = len(next(iter(groups.values())))
    total = [sum(groups[k][i] for k in keys) for i in range(width)]
    est = est_fn(total)
    if est is None:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(n):
        acc = [0.0] * width
        for _k in keys:
            g = groups[keys[rng.randrange(len(keys))]]
            for i in range(width):
                acc[i] += g[i]
        v = est_fn(acc)
        if v is not None:
            draws.append(v)
    if len(draws) < 20:
        return None
    draws.sort()
    se = statistics.pstdev(draws)
    p = S.norm_p(est / se) if se > 0 else (0.0 if est != 0 else 1.0)
    return {"est": est, "lo": draws[int(0.025 * len(draws))],
            "hi": draws[int(0.975 * len(draws)) - 1], "se": se, "p": p,
            "n": n_rows, "games": len(keys)}


def mean_boot(rows, field, block="game"):
    groups, n = {}, 0
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        g = groups.setdefault(r[block], [0.0, 0.0])
        g[0] += 1
        g[1] += v
        n += 1
    return _boot_groups(groups, lambda s: s[1] / s[0] if s[0] else None, n)


def slope_boot(rows, xf, yf, block="game"):
    groups, n = {}, 0
    for r in rows:
        x, y = r.get(xf), r.get(yf)
        if x is None or y is None:
            continue
        g = groups.setdefault(r[block], [0.0] * 5)
        g[0] += 1
        g[1] += x
        g[2] += y
        g[3] += x * x
        g[4] += x * y
        n += 1

    def est(s):
        if s[0] < 3:
            return None
        den = s[3] - s[1] * s[1] / s[0]
        return None if den <= 1e-12 else (s[4] - s[1] * s[2] / s[0]) / den
    return _boot_groups(groups, est, n)


# =============================================================================
# pure pieces
# =============================================================================

class Quotes:
    """One market's live quote series with as-of lookup."""

    def __init__(self, rows):
        self.ts = [r[0] for r in rows]
        self.bid = [r[1] for r in rows]
        self.ask = [r[2] for r in rows]

    def __len__(self):
        return len(self.ts)

    def asof(self, t, strict=False, max_age=MAX_AGE):
        i = (bisect.bisect_left if strict else bisect.bisect_right)(self.ts, t) - 1
        if i < 0 or (max_age is not None and t - self.ts[i] > max_age):
            return None
        b, a = self.bid[i], self.ask[i]
        if b is None or a is None:
            return None
        return self.ts[i], b, a

    def last_two_sided_before(self, t):
        i = bisect.bisect_left(self.ts, t) - 1
        while i >= 0:
            if self.bid[i] is not None and self.ask[i] is not None:
                return self.ts[i], self.bid[i], self.ask[i]
            i -= 1
        return None


def mid(q):
    return None if q is None else (q[1] + q[2]) / 2


def fee_pp(price, contracts=TICKET, market_id="KXNFLGAME"):
    if price is None or not (0 < price < 1):
        return None
    _m, taker = series_multiplier(market_id)
    return 100 * fee_per_contract(price, contracts, "taker", taker)


def c1_violation(game_ask, spread_bid):
    """P(win) >= P(win by over 1.5). Buy GAME yes at ask, buy SPREAD-1.5 NO at
    1 - bid: payoff >= 1 in every state, so an arbitrage iff ask < bid."""
    return game_ask < spread_bid


def c2_violation(bid_a, bid_b):
    """P(A by over x) + P(B by over x) <= 1. Buy both NOs at 1 - bid: at most
    one YES can happen, so payoff >= 1; an arbitrage iff bid_a + bid_b > 1."""
    return bid_a + bid_b > 1


def fav_margins(hist, line_mag, window=1.0):
    """Favourite's margin in historical games whose |spread_line| is within
    `window` of `line_mag`. nflverse spread_line > 0 = home favoured, result =
    home margin (verified by correlation at load)."""
    out = [(res if sl > 0 else -res) for sl, res in hist
           if sl != 0 and abs(abs(sl) - line_mag) <= window]
    return sorted(out)


def p_greater(sorted_v, x, delta):
    """P(V + delta > x)."""
    if not sorted_v:
        return None
    return 1 - bisect.bisect_right(sorted_v, x - delta) / len(sorted_v)


def p_less(sorted_v, x, delta):
    """P(V + delta < x)."""
    if not sorted_v:
        return None
    return bisect.bisect_left(sorted_v, x - delta) / len(sorted_v)


def fit_shift(sorted_v, line, p_target, lo=-7.0, hi=7.0, step=0.1):
    best = None
    k = int(round((hi - lo) / step))
    for i in range(k + 1):
        d = round(lo + i * step, 10)
        err = abs(p_greater(sorted_v, line, d) - p_target)
        if best is None or err < best[0] - 1e-12 or (abs(err - best[0]) <= 1e-12 and abs(d) < abs(best[1])):
            best = (err, d)
    return best[1]


def dist_bucket(d):
    for a, b, name in DIST_BUCKETS:
        if a <= d < b:
            return name
    return None


def slot_of(kick_ts):
    k = datetime.fromtimestamp(kick_ts, S.ET)
    if k.weekday() != 6:
        return "wed/thu/mon prime"
    if k.hour < 16:
        return "sun 1pm"
    if k.hour < 20:
        return "sun late"
    return "snf"


def grid_times(w0, w1, h, cap=GRID_CAP):
    """Instants t with t-h >= w0 and t+h <= w1, step h, thinned evenly to cap."""
    start, end = w0 + h, w1 - h
    if end < start:
        return []
    n = int((end - start) // h) + 1
    if n <= cap:
        return [start + i * h for i in range(n)]
    step = n / cap
    return [start + int(i * step) * h for i in range(cap)]


def top_decile(rows, key):
    rs = sorted(rows, key=lambda r: -abs(r[key]))
    return rs[:max(1, math.ceil(0.1 * len(rs)))] if rs else []


def sign(x):
    return (x > 0) - (x < 0)


# =============================================================================
# loading (week-1 fenced)
# =============================================================================

def team_of_subject(subject):
    from venues.mapping import team_abbr
    return team_abbr(re.sub(r"\s+wins by.*$", "", subject or ""))


def load_games(c):
    from research.consensus import load_games as lg
    return lg(c, 2026, 1)


def event_game_map(c, games):
    sql, params = S.week1_filter_sql("event_id")
    teams = defaultdict(set)
    for eid, subj in c.execute(
            f"SELECT DISTINCT event_id, subject FROM markets WHERE venue='kalshi' AND "
            f"(market_id LIKE 'KXNFLSPREAD-%' OR market_id LIKE 'KXNFLGAME-%') AND {sql}", params):
        t = team_of_subject(subj)
        if t:
            teams[eid.split("-", 1)[1]].add(t)
    out = {}
    for suffix, ts_ in teams.items():
        day = datetime.strptime(suffix[:7], "%y%b%d").date()
        for gid, g in games.items():
            if {g["home"], g["away"]} == ts_ and abs(datetime.fromtimestamp(g["kick"], S.ET).date() - day) <= timedelta(days=1):
                out[suffix] = gid
    return out


def load_markets(c, games):
    evmap = event_game_map(c, games)
    sql, params = S.week1_filter_sql("market_id")
    out, unmapped = {}, 0
    for mid_, eid, subj, line, title in c.execute(
            f"SELECT market_id, event_id, subject, line, title FROM markets "
            f"WHERE venue='kalshi' AND {sql}", params):
        s = mid_.split("-")[0]
        if s not in SERIES:
            continue
        S.assert_search_set(mid_, 0)
        gid = evmap.get(eid.split("-", 1)[1])
        if gid is None:
            unmapped += 1
            continue
        team = team_of_subject(subj) if s in ("KXNFLSPREAD", "KXNFLGAME") else None
        out[mid_] = {"market": mid_, "series": s, "game": gid, "subject": subj,
                     "line": line, "title": title, "team": team}
    return out, unmapped


_QCACHE = {}


def quotes(c, market_id):
    if market_id not in _QCACHE:
        S.assert_search_set(market_id, 0)
        _QCACHE[market_id] = Quotes(c.execute(
            "SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' "
            "AND market_id=? AND source='live' ORDER BY ts", (market_id,)).fetchall())
    return _QCACHE[market_id]


def depth_at(c, market_id, side, t):
    r = c.execute("SELECT ts, touch_price, touch_size FROM market_depth WHERE venue='kalshi' "
                  "AND market_id=? AND side=? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                  (market_id, side, t)).fetchone()
    return r if r and t - r[0] <= DEPTH_WINDOW else None


def depth_ok(c, market_id, side, t, price, contracts=TICKET):
    d = depth_at(c, market_id, side, t)
    return bool(d and d[1] is not None and abs(d[1] - price) <= 0.005 and (d[2] or 0) >= contracts)


def team_outcome(m, g):
    hs, as_ = g["hs"], g["as"]
    if hs is None or as_ is None:
        return None
    if m["series"] == "KXNFLTOTAL":
        return 1.0 if hs + as_ > m["line"] else 0.0
    margin = hs - as_ if m["team"] == g["home"] else as_ - hs
    if m["series"] == "KXNFLGAME":
        return 1.0 if margin > 0 else 0.0
    return 1.0 if margin > m["line"] else 0.0


def prop_outcomes(c, markets):
    """market -> (y, player's team) via market_outcome -> outcomes -> settle_one."""
    from jobs.settle_outcomes import OVER, UNDER, settle_one
    from research.score import rung
    out, census = {}, Counter()
    for mid_, m in markets.items():
        if m["series"] not in PROPS:
            continue
        row = c.execute(
            "SELECT o.outcome_id, o.key, o.sport, o.season, o.week, o.entity_type, o.entity_id, "
            "o.stat, o.line, o.side, o.push_possible FROM market_outcome mo JOIN outcomes o "
            "USING (outcome_id) WHERE mo.venue='kalshi' AND mo.market_id=?", (mid_,)).fetchone()
        if row is None:
            census["prop: no outcome mapping"] += 1
            continue
        if row[9] != "over" or rung(mid_) is None or rung(mid_) - 0.5 != row[8]:
            census["prop: orientation check failed"] += 1
            continue
        result = settle_one(c, row)[0]
        if result not in (OVER, UNDER):
            census["prop: unsettled"] += 1
            continue
        t = c.execute("SELECT team FROM nfl_player_week WHERE gsis_id=? AND season=2026 AND week=1 "
                      "ORDER BY data_version DESC LIMIT 1", (row[6],)).fetchone()
        out[mid_] = (1.0 if result == OVER else 0.0, t[0] if t else None)
        census["prop: settled"] += 1
    return out, census


def load_history():
    import polars as pl
    f = sorted(glob.glob(os.path.join(config.RAW_DIR, "nflverse", "*", "games.parquet")))[-1]
    g = pl.read_parquet(f).filter((pl.col("season") <= 2025) & pl.col("result").is_not_null())
    sp = g.filter(pl.col("spread_line").is_not_null())
    corr = sp.select(pl.corr("result", "spread_line")).item()
    spread = list(sp.select("spread_line", "result").iter_rows())
    tot = g.filter(pl.col("total_line").is_not_null() & pl.col("total").is_not_null())
    totals = list(tot.select("total_line", "total").iter_rows())
    return spread, totals, corr, f


# =============================================================================
# axes
# =============================================================================

def axis1(c, reg, games, markets, out):
    out.append("\n" + "=" * 78 + "\nAXIS 1 - CROSS-MARKET CONSTRAINTS\n" + "=" * 78)
    for pref in ("KXNFLRECYDS", "KXNFLPASSYDS", "KXNFLTEAMTOTAL", "KXMVE"):
        n = c.execute("SELECT COUNT(*) FROM markets WHERE venue='kalshi' AND market_id >= ? AND market_id < ?",
                      (pref + "-", pref + ".")).fetchone()[0]
        out.append(f"  {pref:<16} markets on disk: {n}  -> {'NOT TESTABLE, 0 tests' if n == 0 else 'present'}")
    out.append("  No team passing-total series of any name is in the markets table (checked by series census).")
    t = c.execute("SELECT title FROM markets WHERE venue='kalshi' AND market_id LIKE 'KXNFLWINSWEEK%' LIMIT 1").fetchone()
    out.append(f"  KXNFLWINSWEEK is '{t[0] if t else '-'}' - a cumulative season claim, NOT the same claim as "
               "KXNFLGAME. Skipped, 0 tests.")
    out.append("  Lowest KXNFLSPREAD rung present: 1.5 for every team (no 0.5 rung). C1 tests P(win) >= "
               "P(win by over 1.5); the gap is P(win by exactly 1), so a positive deviation is EXPECTED.")

    by_game = defaultdict(list)
    for m in markets.values():
        by_game[m["game"]].append(m)
    c1, c2 = [], []
    for gid, ms in by_game.items():
        g = games[gid]
        gm = {m["team"]: m for m in ms if m["series"] == "KXNFLGAME"}
        sp = {m["team"]: m for m in ms if m["series"] == "KXNFLSPREAD" and m["line"] == 1.5}
        teams = [t for t in (g["home"], g["away"]) if t in gm and t in sp]
        firsts = [quotes(c, m["market"]).ts[0] for m in list(gm.values()) + list(sp.values())
                  if len(quotes(c, m["market"]))]
        if not firsts:
            continue
        pre = [g["kick"] - 3600 * k for k in range(1, 24 * 14)
               if g["kick"] - 3600 * k >= max(firsts)]
        ing = [g["kick"] + 300 * k for k in range(0, LIVE_WINDOW // 300 + 1)]
        for phase, grid in (("pre", pre), ("in", ing)):
            for t in grid:
                for team in teams:
                    qg, qs = quotes(c, gm[team]["market"]).asof(t), quotes(c, sp[team]["market"]).asof(t)
                    if qg is None or qs is None:
                        continue
                    viol = c1_violation(qg[2], qs[1])
                    net = None
                    if viol:
                        ok = (depth_ok(c, gm[team]["market"], "buy_yes", t, qg[2])
                              and depth_ok(c, sp[team]["market"], "buy_no", t, 1 - qs[1]))
                        if ok:
                            net = 100 * (qs[1] - qg[2]) - fee_pp(qg[2]) - fee_pp(1 - qs[1], market_id="KXNFLSPREAD")
                    c1.append({"game": gid, "phase": phase, "dev": 100 * (mid(qg) - mid(qs)),
                               "viol": 1.0 if viol else 0.0, "net": net})
                if len(teams) == 2:
                    qa = quotes(c, sp[teams[0]]["market"]).asof(t)
                    qb = quotes(c, sp[teams[1]]["market"]).asof(t)
                    if qa is None or qb is None:
                        continue
                    viol = c2_violation(qa[1], qb[1])
                    net = None
                    if viol:
                        ok = (depth_ok(c, sp[teams[0]]["market"], "buy_no", t, 1 - qa[1])
                              and depth_ok(c, sp[teams[1]]["market"], "buy_no", t, 1 - qb[1]))
                        if ok:
                            net = (100 * (qa[1] + qb[1] - 1) - fee_pp(1 - qa[1], market_id="KXNFLSPREAD")
                                   - fee_pp(1 - qb[1], market_id="KXNFLSPREAD"))
                    c2.append({"game": gid, "phase": phase, "dev": 100 * (mid(qa) + mid(qb) - 1),
                               "viol": 1.0 if viol else 0.0, "net": net})
    for name, rows, expect in (("C1 GAME - SPREAD1.5", c1, "expected > 0 (= P(win by exactly 1))"),
                               ("C2 SPREAD1.5 A + B - 1", c2, "expected < 0 (= -P(|margin| <= 1))")):
        out.append(f"\n  {name}   {expect}")
        for phase in ("pre", "in"):
            rs = [r for r in rows if r["phase"] == phase]
            for metric, label in (("dev", "signed deviation at mid"), ("viol", "violation rate beyond bands"),
                                  ("net", "executable net on violations (10 ct, depth-verified)")):
                res = mean_boot(rs, metric)
                scale = 100.0 if metric == "viol" else 1.0
                rec = reg.add("axis1_constraints", f"{name} | {phase} | {metric}", res, scale=scale,
                              unit="pp" if metric != "viol" else "% of instants")
                out.append(fmt(f"{phase:<3} {label}", rec, n_extra=f"obs {len(rs)}, violations "
                               f"{int(sum(r['viol'] for r in rs))}, depth-verified {sum(1 for r in rs if r['net'] is not None)}"))


def axis2(c, reg, games, markets, out):
    from research import consensus as CONS
    out.append("\n" + "=" * 78 + "\nAXIS 2 - KEY-NUMBER LADDER PRICING (pre-kickoff)\n" + "=" * 78)
    spread_hist, total_hist, corr, src = load_history()
    out.append(f"  history {os.path.basename(src)}: {len(spread_hist)} games with spread, corr(result, spread_line) "
               f"{corr:+.3f} (positive = spread_line is the HOME favourite's line, as CLAUDE.md says)")
    out.append("  INTERPOLATION CAVEAT: every price here comes from a conditional historical distribution "
               "shifted to match ONE consensus line; the exact-match arm (020) had no such model error.")
    assert corr > 0.3, "spread_line sign convention not as expected"
    snaps, _lag = CONS.load_snapshots(games)
    by_game = defaultdict(list)
    for m in markets.values():
        if m["series"] in ("KXNFLSPREAD", "KXNFLTOTAL"):
            by_game[m["game"]].append(m)
    rows, fits = [], Counter()
    for s in snaps:
        g = games[s["game"]]
        T = s["T"]
        if T >= g["kick"]:
            continue
        S.assert_search_set("KXNFLSPREAD-" + datetime.fromtimestamp(g["kick"], S.ET).strftime("%y%b%d").upper(), T)
        cons, _short = CONS.consensus(s["pairs"], "multiplicative")
        for kind, series in (("spread", "KXNFLSPREAD"), ("total", "KXNFLTOTAL")):
            keys = [(k, v) for k, v in cons.items() if k[0] == kind]
            if not keys:
                continue
            (main, (p_main, nb)) = sorted(keys, key=lambda kv: (-kv[1][1], kv[0][2]))[0]
            L = main[2]
            if kind == "spread":
                sample = fav_margins(spread_hist, L)
            else:
                sample = sorted(tot for tl, tot in total_hist if abs(tl - L) <= 1.0)
            if len(sample) < 30:
                fits["fit skipped: < 30 historical games"] += 1
                continue
            delta = fit_shift(sample, L, p_main)
            fits[f"{kind} fits"] += 1
            for m in by_game.get(s["game"], []):
                if m["series"] != series or m["line"] is None:
                    continue
                q = quotes(c, m["market"]).asof(T)
                if q is None:
                    continue
                x = m["line"]
                if kind == "total":
                    implied, dist = p_greater(sample, x, delta), abs(x - L)
                elif m["team"] == main[1]:
                    implied, dist = p_greater(sample, x, delta), abs(x - L)
                else:
                    implied, dist = p_less(sample, -x, delta), x + L
                gap = 100 * (mid(q) - implied)
                y = team_outcome(m, g)
                side = "no" if gap > 0 else "yes"
                price = (1 - q[1]) if side == "no" else q[2]
                pnl = clv = None
                if abs(gap) > THRESH_PP and 0 < price < 1:
                    fee = fee_pp(price, market_id=m["market"])
                    if y is not None:
                        pay = y if side == "yes" else 1 - y
                        pnl = 100 * (pay - price) - fee
                    cq = quotes(c, m["market"]).last_two_sided_before(g["kick"])
                    if cq:
                        cm = mid(cq)
                        clv = 100 * ((cm if side == "yes" else 1 - cm) - price)
                rows.append({"game": s["game"], "series": series, "bucket": dist_bucket(dist),
                             "gap": gap, "pnl": pnl, "clv": clv, "open": abs(gap) > THRESH_PP})
    out.append(f"  consensus snapshots used: {dict(fits)};  rung observations {len(rows)}")
    for series in ("KXNFLSPREAD", "KXNFLTOTAL"):
        out.append(f"\n  [{series}]")
        for _a, _b, b in DIST_BUCKETS:
            rs = [r for r in rows if r["series"] == series and r["bucket"] == b]
            rec = reg.add("axis2_keynumber", f"{series} | dist {b} | mean signed gap", mean_boot(rs, "gap"))
            share = 100 * sum(r["open"] for r in rs) / len(rs) if rs else float("nan")
            out.append(fmt(f"dist {b:<5} mean gap (Kalshi - implied)", rec, n_extra=f"|gap|>2.5pp {share:.1f}%"))
        opened = [r for r in rows if r["series"] == series and r["open"]]
        for metric in ("pnl", "clv"):
            rec = reg.add("axis2_keynumber", f"{series} | executable |gap|>2.5 | {metric}", mean_boot(opened, metric))
            out.append(fmt(f"executable {metric.upper()} per contract", rec, n_extra=f"triggers {len(opened)}"))


def axis3(c, reg, games, markets, out):
    out.append("\n" + "=" * 78 + "\nAXIS 3 - PRICE PATH (momentum / reversal)\n" + "=" * 78)
    out.append(f"  grid: instants t with as-of mids at t-h, t, t+h (age <= {MAX_AGE:.0f}s, two-sided), "
               f"thinned evenly to <= {GRID_CAP} per market x tier x h")
    cells = defaultdict(list)
    for m in markets.values():
        g = games[m["game"]]
        q = quotes(c, m["market"])
        if len(q) < 3:
            continue
        windows = (("pre", q.ts[0], g["kick"]), ("in", g["kick"], min(g["kick"] + LIVE_WINDOW, q.ts[-1] + MAX_AGE)))
        for tier, w0, w1 in windows:
            for h in HORIZONS:
                for t in grid_times(w0, w1, h):
                    a, b, n_ = q.asof(t - h), q.asof(t), q.asof(t + h)
                    if a is None or b is None or n_ is None:
                        continue
                    mb = mid(b)
                    fee = fee_pp(mb, market_id=m["market"])
                    if fee is None:
                        continue
                    cells[(m["series"], tier, h)].append(
                        (m["game"], 100 * (mb - mid(a)), 100 * (mid(n_) - mb), 50 * (b[2] - b[1]) + fee))
    for series in SERIES:
        for tier in ("pre", "in"):
            for h in HORIZONS:
                rs = [{"game": g_, "x": x, "y": y, "cost": cost} for g_, x, y, cost in cells[(series, tier, h)]]
                rec = reg.add("axis3_pricepath", f"{series} | {tier} | h={h}s | slope", slope_boot(rs, "x", "y"),
                              unit="slope")
                d = sign(rec.get("est", 0) or 0)
                top = top_decile(rs, "x")
                for r in top:
                    r["net"] = d * sign(r["x"]) * r["y"] - r["cost"]
                rec2 = reg.add("axis3_pricepath", f"{series} | {tier} | h={h}s | top-decile net of cost",
                               mean_boot(top, "net"))
                out.append(fmt(f"{series:<12} {tier:<3} h={h:>4}s slope", rec, unit=""))
                out.append(fmt(f"{'':<12} {'':<3}         top-decile net", rec2,
                               n_extra=f"direction {'momentum' if d > 0 else 'reversal' if d < 0 else 'none'}"))


def axis4(c, reg, games, markets, out):
    out.append("\n" + "=" * 78 + "\nAXIS 4 - TIME AND CALENDAR (residual = outcome - last pre-kick mid)\n" + "=" * 78)
    props, census = prop_outcomes(c, markets)
    out.append(f"  prop settlement census: {dict(census)}")
    close_mid = {}
    rows = []
    for m in markets.values():
        g = games[m["game"]]
        cq = quotes(c, m["market"]).last_two_sided_before(g["kick"])
        if cq is None:
            continue
        cm = mid(cq)
        close_mid[m["market"]] = cm
        if m["series"] in PROPS:
            if m["market"] not in props:
                continue
            y, team = props[m["market"]]
            fam = "props"
        else:
            y, team, fam = team_outcome(m, g), m["team"], "team"
            if y is None:
                continue
        rows.append({"game": m["game"], "family": fam, "series": m["series"], "res": 100 * (y - cm),
                     "slot": slot_of(g["kick"]),
                     "prime": "primetime" if datetime.fromtimestamp(g["kick"], S.ET).hour >= 20 else "afternoon",
                     "ha": None if team is None else ("home" if team == g["home"] else "away" if team == g["away"] else None),
                     "team": team, "market": m["market"]})
    # favourite: the team's own GAME closing mid > 0.5
    game_close = {(m["game"], m["team"]): close_mid.get(m["market"]) for m in markets.values()
                  if m["series"] == "KXNFLGAME"}
    for r in rows:
        r["fav"] = None
        if r["family"] == "team" and r["series"] != "KXNFLTOTAL":
            gc = game_close.get((r["game"], r["team"]))
            r["fav"] = None if gc is None else ("favourite" if gc > 0.5 else "underdog")
    axes = (("slot", ("wed/thu/mon prime", "sun 1pm", "sun late", "snf")), ("prime", ("primetime", "afternoon")),
            ("ha", ("home", "away")))
    for fam in ("props", "team"):
        out.append(f"\n  [{fam}]")
        fr = [r for r in rows if r["family"] == fam]
        for field, levels in axes + ((("fav", ("favourite", "underdog")),) if fam == "team" else ()):
            for lv in levels:
                rs = [r for r in fr if r[field] == lv]
                rec = reg.add("axis4_calendar", f"{fam} | {field}={lv} | mean residual", mean_boot(rs, "res"))
                out.append(fmt(f"{field}={lv:<18}", rec))


def axis5(c, reg, games, markets, out):
    import sqlite3
    out.append("\n" + "=" * 78 + "\nAXIS 5 - LIQUIDITY EVENTS (first print >= series p99 size)\n" + "=" * 78)
    t = sqlite3.connect(f"file:{TRADES_DB}?mode=ro", uri=True)
    events, fence = [], Counter()
    for series in SERIES:
        prints = defaultdict(list)
        for mid_, ts, size, side in t.execute(
                "SELECT market_id, ts, size, taker_side FROM market_trades WHERE venue='kalshi' "
                "AND market_id >= ? AND market_id < ? ORDER BY market_id, ts", (series + "-", series + ".")):
            if not S.in_search_set(mid_, ts):
                fence["excluded: not a week-1 ticker"] += 1
                continue
            S.assert_search_set(mid_, ts)
            fence["week-1 prints"] += 1
            prints[mid_].append((ts, size or 0.0, side))
        sizes = sorted(s for ps in prints.values() for _, s, _ in ps)
        if not sizes:
            continue
        p99 = sizes[int(0.99 * (len(sizes) - 1))]
        for mid_, ps in prints.items():
            if mid_ not in markets:
                continue
            first = next(((ts, s, sd) for ts, s, sd in ps if s >= p99 and sd in ("yes", "no")), None)
            if first:
                events.append((series, mid_, first, p99))
        out.append(f"  {series:<12} week-1 prints {len(sizes):>8,}  p99 size {p99:,.0f}  markets with an event "
                   f"{sum(1 for e in events if e[0] == series)}")
    out.append(f"  fence: {dict(fence)}  (GAME/SPREAD tapes were completed only inside entry->kickoff; "
               "'first' is first in the retained tape)")
    for series in SERIES:
        for h in HORIZONS:
            rs = []
            for s, mid_, (ts, size, side), _p in events:
                if s != series:
                    continue
                q = quotes(c, mid_)
                a, b = q.asof(ts - 60), q.asof(ts + h)
                if a is None or b is None:
                    continue
                dirn = 1 if side == "yes" else -1
                cont = 100 * (mid(b) - mid(a)) * dirn
                fee = fee_pp(mid(a), market_id=mid_)
                rs.append({"game": markets[mid_]["game"], "cont": cont,
                           "net": None if fee is None else cont - (50 * (a[2] - a[1]) + fee)})
            rec = reg.add("axis5_liquidity", f"{series} | h={h}s | continuation", mean_boot(rs, "cont"))
            rec2 = reg.add("axis5_liquidity", f"{series} | h={h}s | continuation net of cost", mean_boot(rs, "net"))
            out.append(fmt(f"{series:<12} h={h:>4}s continuation", rec))
            out.append(fmt(f"{'':<12}         net of half-spread+fee", rec2))


# =============================================================================
# report
# =============================================================================

def fmt(label, rec, unit="pp", n_extra=""):
    if not rec.get("estimable"):
        return f"    {label:<48} n/a (not estimable){('  ' + n_extra) if n_extra else ''}"
    star = "*" if rec["excludes_zero"] else " "
    read = "" if rec["readable"] else "  <- too few games to read"
    return (f"    {label:<48} {rec['est']:+8.3f}{unit} [{rec['lo']:+8.3f}, {rec['hi']:+8.3f}]{star} "
            f"p={rec['p']:.2g} n={rec['n']} games={rec['games']}{read}"
            + (f"  | {n_extra}" if n_extra else ""))


def candidates(records):
    return [r for r in records if r["role"] == "search" and r.get("estimable") and r.get("excludes_zero")
            and r.get("readable")]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--axes", default="1,2,3,4,5")
    a = ap.parse_args()
    if os.path.exists(REG_PATH):
        os.remove(REG_PATH)
    reg = S.Registry(REG_PATH)
    c = S.live_ro()
    games = load_games(c)
    markets, unmapped = load_markets(c, games)
    out = [f"BRIEF 022 PART 3 SCAN - NFL week 1 only. games {len(games)}, week-1 Kalshi markets "
           f"{len(markets)} ({dict(Counter(m['series'] for m in markets.values()))}), unmapped {unmapped}. "
           "No CFB and no week-2 row is read by this module."]
    axes = {"1": axis1, "2": axis2, "3": axis3, "4": axis4, "5": axis5}
    for k in a.axes.split(","):
        axes[k](c, reg, games, markets, out)
        print("\n".join(out), flush=True)
        out.clear()
    recs = S.load_registries([REG_PATH])
    fam = Counter(r["family"] for r in recs)
    cand = candidates(recs)
    print("\n" + "=" * 78 + "\nREGISTRY\n" + "=" * 78)
    print(f"  {REG_PATH}: {len(recs)} search intervals registered, estimable "
          f"{sum(1 for r in recs if r['estimable'])}; by family {dict(fam)}")
    print(f"  nominal p<0.05: {sum(1 for r in recs if r['estimable'] and r['p'] < 0.05)}  "
          f"(expected by chance ~{0.05 * sum(1 for r in recs if r['estimable']):.1f})")
    print(f"  CANDIDATES (interval excludes zero, >= {S.MIN_GAMES_TO_READ} games): {len(cand)}")
    for r in sorted(cand, key=lambda r: r["p"]):
        print(f"    {r['family']:<18} {r['name']:<58} {r['est']:+.3f} [{r['lo']:+.3f}, {r['hi']:+.3f}] "
              f"p={r['p']:.2g} n={r['n']} games={r['games']}")


if __name__ == "__main__":
    main()
