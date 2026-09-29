"""c-19 - the Kalshi ladder against the sportsbook close, pre-kickoff.

    python -m research.venue_spread --cache D:/temp/c19/extract.sqlite --extract
    python -m research.venue_spread --cache D:/temp/c19/extract.sqlite --json-out F

PRE-REGISTRATION: docs/C19-venue-spread-preregistration.md, committed at
ac605b7 BEFORE this script existed. This file implements it; it does not
extend it. Read that file for the rules; the comments here say only where the
code carries one out.

THE STORE IS NEVER WRITTEN. `market_log.db` is opened `mode=ro` everywhere,
including inside `venues.mapping.resolve_player`, whose `store.db()` is
replaced with a read-only connection before it is called, and whose
read-write `_conn` is replaced with one that raises.

THE CACHE (`--cache`) is a local scratch copy of the rows this study reads,
so the three-minute scan over `quotes` runs once. It holds Odds API prices, so
it stays on local disk, is never committed and never published
(`BET_LIST_RESTRICTION`). Everything this script PRINTS is an aggregate.
"""
import argparse
import bisect
import json
import math
import os
import random
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.fees import fee_per_contract, series_multiplier, series_of  # noqa: E402

SEASON = 2026
BOOK_KEYS = {"player_receptions": ("receptions", 0),
             "player_receptions_alternate": ("receptions", 1),
             "player_rush_attempts": ("rush_attempts", 0)}
KALSHI_SERIES = {"KXNFLREC": "receptions", "KXNFLRSHATT": "rush_attempts"}
BENCHMARK_BOOKS = ("draftkings", "fanduel", "betmgm")   # = backfill_oddsapi's
STALE = 600.0            # both venues; fixed by the pre-registration
MAX_LEAD = 80 * 3600     # a book row pairs with a kickoff at most 80h after it
CLOSE_LEAD = 900.0       # the close lies within 15 min of kickoff
ENTRY_LEAD = 180 * 60    # step 4 entry instant, kickoff - 180 min
THIN_GAMES, THIN_FITS = 10, 50
THRESH = 2.5             # pp, descriptive share
BOOT, SEED = 2000, 19
CLIP = 1e-4
TTK = ((24 * 3600, ">24h"), (6 * 3600, "6-24h"), (3600, "1-6h"), (0, "<1h"))


def ro_uri(path):
    return f"file:{path}?mode=ro"


def ro():
    return sqlite3.connect(ro_uri(config.DB_PATH), uri=True)


# =============================================================================
# extract - the only step that reads market_log.db's quotes table
# =============================================================================

def extract(cache):
    if os.path.exists(cache):
        raise SystemExit(f"{cache} exists; delete it by hand to re-extract")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    src = ro()
    out = sqlite3.connect(cache)
    out.executescript("""
        CREATE TABLE book (ts REAL, book TEXT, event_id TEXT, mkey TEXT, player TEXT,
                           line REAL, side TEXT, p REAL, source_ts REAL);
        CREATE TABLE kq (market_id TEXT, ts REAL, bid REAL, ask REAL);
        CREATE TABLE kout (market_id TEXT, outcome_id TEXT, gsis TEXT, stat TEXT,
                           line REAL, side TEXT, game_id TEXT, week INTEGER, push INTEGER);
        CREATE TABLE games (game_id TEXT PRIMARY KEY, week INTEGER, kickoff_ts REAL,
                            home_score REAL, away_score REAL, home TEXT, away TEXT);
        CREATE TABLE depth (market_id TEXT, ts REAL, side TEXT, touch_price REAL,
                            touch_size REAL);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    t0 = time.time()
    like = " OR ".join(f"market_id LIKE '%|{k}|%'" for k in BOOK_KEYS)
    n = 0
    for r in src.execute(f"""
            SELECT ts, venue, event_id, market_id, line, side, mid, source_ts
              FROM quotes
             WHERE source = 'live' AND venue LIKE 'oddsapi:%' AND market_type = 'prop'
               AND ({like})"""):
        ts, venue, eid, mid, line, side, p, sts = r
        _e, mkey, player, _s = mid.split("|")
        out.execute("INSERT INTO book VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts, venue.split(":", 1)[1], eid, mkey, player, line, side, p, sts))
        n += 1
    print(f"  book rows {n:,} ({time.time() - t0:.0f}s)")

    kout = src.execute("""
        SELECT mo.market_id, o.outcome_id, o.entity_id, o.stat, o.line, o.side, o.event_id,
               o.week, o.push_possible
          FROM market_outcome mo JOIN outcomes o USING (outcome_id)
         WHERE mo.venue = 'kalshi' AND o.season = ? AND o.stat IN ('receptions', 'rush_attempts')
    """, (SEASON,)).fetchall()
    out.executemany("INSERT INTO kout VALUES (?,?,?,?,?,?,?,?,?)", kout)
    print(f"  kalshi mapped outcomes {len(kout):,}")

    nq = 0
    for (mid, *_rest) in kout:
        # `source` is filtered in Python: with it in the WHERE clause the planner
        # picks ix_quotes_ingest (source=?) and walks 21M live rows per market.
        rows = [(ts, b, a) for ts, b, a, s in src.execute(
            """SELECT ts, best_bid, best_ask, source FROM quotes INDEXED BY ix_quotes_market_ts
                WHERE venue='kalshi' AND market_id=?""", (mid,)) if s == "live"]
        out.executemany("INSERT INTO kq VALUES (?,?,?,?)", [(mid, *r) for r in rows])
        nq += len(rows)
        d = src.execute("""SELECT ts, side, touch_price, touch_size FROM market_depth
                            WHERE venue='kalshi' AND market_id=?""", (mid,)).fetchall()
        out.executemany("INSERT INTO depth VALUES (?,?,?,?,?)", [(mid, *r) for r in d])
    print(f"  kalshi quotes {nq:,} ({time.time() - t0:.0f}s)")

    g = src.execute("""
        SELECT game_id, week, kickoff_ts, home_score, away_score, home_team, away_team
          FROM nfl_games n WHERE season = ? AND data_version = (
               SELECT MAX(data_version) FROM nfl_games m WHERE m.game_id = n.game_id)
    """, (SEASON,)).fetchall()
    out.executemany("INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?)", g)
    out.execute("INSERT INTO meta VALUES ('extracted_ts', ?)", (str(time.time()),))
    out.execute("CREATE INDEX ix_kq ON kq(market_id, ts)")
    out.commit()
    out.close()
    src.close()
    print(f"  extract done in {time.time() - t0:.0f}s -> {cache}")


# =============================================================================
# read-only player resolution
# =============================================================================

@contextmanager
def _ro_db():
    c = ro()
    try:
        yield c
    finally:
        c.close()


def _refuse(*_a, **_k):
    raise RuntimeError("c-19 must not open market_log.db read-write")


def resolver():
    import store
    store.db = _ro_db
    store._conn = _refuse
    from venues import mapping
    cache = {}

    def resolve(name):
        if name not in cache:
            try:
                cache[name] = (mapping.resolve_player(name, SEASON)[0], None)
            except mapping.Unresolved as e:
                cache[name] = (None, str(e)[:40])
        return cache[name]
    return resolve


# =============================================================================
# pure pieces
# =============================================================================

def devig(p_over, p_under):
    """backfill_oddsapi.devig_pair, without its import-time dependencies."""
    t = p_over + p_under
    if not (1.00 < t < 1.15):
        return None
    return p_over / t


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def block_boot(rows, stat_fn, key="game", draws=BOOT, seed=SEED):
    """Game block bootstrap of stat_fn(list_of_rows). -> (est, lo, hi, se, n_games)."""
    groups = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    ids = sorted(groups)
    est = stat_fn(rows)
    if len(ids) < 2 or est is None:
        return est, None, None, None, len(ids)
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        samp = []
        for _i in ids:
            samp.extend(groups[ids[rng.randrange(len(ids))]])
        v = stat_fn(samp)
        if v is not None:
            vals.append(v)
    vals.sort()
    se = statistics.pstdev(vals) if len(vals) > 1 else None
    return est, pct(vals, 0.025), pct(vals, 0.975), se, len(ids)


def mean(rows, f):
    xs = [f(r) for r in rows]
    return sum(xs) / len(xs) if xs else None


def slope(rows, fx, fy):
    xs = [fx(r) for r in rows]
    ys = [fy(r) for r in rows]
    if len(xs) < 3:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def fmt_iv(t, scale=1.0, d=2):
    est, lo, hi, _se, g = t
    if est is None:
        return "n/a"
    s = f"{est * scale:+.{d}f}"
    if lo is not None:
        s += f" [{lo * scale:+.{d}f}, {hi * scale:+.{d}f}]"
    s += f" ({g} games)"
    if g < 5:
        s += " NOT READ: <5 games"
    return s


# =============================================================================
# load + pair
# =============================================================================

def load(cache):
    c = sqlite3.connect(ro_uri(cache), uri=True)
    games = {r[0]: {"week": r[1], "kick": r[2], "hs": r[3], "as": r[4]}
             for r in c.execute("SELECT * FROM games")}
    kout = defaultdict(list)
    kmeta = {}
    for mid, oid, gsis, stat, line, side, game, week, push in c.execute("SELECT * FROM kout"):
        if side != "over" or series_of(mid) not in KALSHI_SERIES:
            continue
        kick = games.get(game, {}).get("kick")
        kmeta[mid] = {"oid": oid, "gsis": gsis, "stat": stat, "line": line, "game": game,
                      "week": week, "kick": kick, "push": push}
        kout[(gsis, stat, float(line))].append(mid)
    kq = defaultdict(lambda: ([], []))
    for mid, ts, bid, ask in c.execute("SELECT market_id, ts, bid, ask FROM kq ORDER BY market_id, ts"):
        kq[mid][0].append(ts)
        kq[mid][1].append((bid, ask))
    depth = defaultdict(list)
    for mid, ts, side, tp, tsz in c.execute("SELECT * FROM depth ORDER BY market_id, ts"):
        depth[mid].append((ts, side, tp, tsz))
    book = c.execute("SELECT * FROM book").fetchall()
    c.close()
    return games, kout, kmeta, kq, depth, book


def kalshi_at(kq, mid, t):
    """-> (status, mid, bid, ask, gap). Last quote at or before t."""
    ts, qs = kq.get(mid, ([], []))
    i = bisect.bisect_right(ts, t) - 1
    if i < 0:
        return "no kalshi quote before fetch", None, None, None, None
    bid, ask = qs[i]
    gap = t - ts[i]
    if bid is None or ask is None or not (0 < bid < ask < 1):
        return "kalshi one-sided", None, bid, ask, gap
    if gap > STALE:
        return "kalshi stale > 600s", None, bid, ask, gap
    return "ok", (bid + ask) / 2, bid, ask, gap


def book_consensus(book, census):
    """-> {(ts, event_id, player, stat, line): {p_bench, p_all, n_bench, n_all, ages}}"""
    by = defaultdict(dict)   # (ts, eid, book, player, stat, line) -> {side: (p, age, alt)}
    for ts, bk, eid, mkey, player, line, side, p, sts in book:
        stat, alt = BOOK_KEYS[mkey]
        if line is None or p is None:
            census["book row without line/price"] += 1
            continue
        sd = (side or "").lower()
        if sd not in ("over", "under"):
            continue
        k = (ts, eid, bk, player, stat, float(line))
        prev = by[k].get(sd)
        if prev is None or alt < prev[2]:          # main key preferred over alternate
            by[k][sd] = (p, (ts - sts) if sts else None, alt)
    per_line = defaultdict(list)
    ages = []
    for (ts, eid, bk, player, stat, line), sides in by.items():
        if "over" not in sides or "under" not in sides:
            census["book line one-sided (no under)"] += 1
            continue
        (po, ao, _), (pu, au, _) = sides["over"], sides["under"]
        if ao is None or au is None or ao > STALE or au > STALE:
            census["book quote stale > 600s or no source_ts"] += 1
            continue
        pv = devig(po, pu)
        if pv is None:
            census["book overround outside (1.00, 1.15)"] += 1
            continue
        census["book two-sided fresh pairs"] += 1
        ages.append(max(ao, au))
        per_line[(ts, eid, player, stat, line)].append((bk, pv))
    cons = {}
    for k, lst in per_line.items():
        bench = [p for b, p in lst if b in BENCHMARK_BOOKS]
        allp = [p for _b, p in lst]
        cons[k] = {"p_bench": statistics.median(bench) if bench else None,
                   "p_all": statistics.median(allp), "n_bench": len(bench), "n_all": len(allp)}
    return cons, ages


def pair(games, kout, kmeta, kq, book, resolve):
    census = Counter()
    cons, book_ages = book_consensus(book, census)
    matched = []
    ev_games = defaultdict(set)
    kgaps = []
    for (ts, eid, player, stat, line), c in sorted(cons.items()):
        if c["p_bench"] is None:
            census["line: no benchmark book (DK/FD/MGM)"] += 1
            continue
        census["line: benchmark consensus"] += 1
        gsis, why = resolve(player)
        if gsis is None:
            census["line: player unresolved"] += 1
            continue
        mids = kout.get((gsis, stat, line), [])
        cand = [m for m in mids if kmeta[m]["kick"] and 0 < kmeta[m]["kick"] - ts <= MAX_LEAD]
        if not cand:
            anyp = any(k[0] == gsis and k[1] == stat for k in kout)
            census["line: no Kalshi rung at this line" if anyp
                   else "line: player has no Kalshi ladder for this stat"] += 1
            continue
        mid = min(cand, key=lambda m: kmeta[m]["kick"])
        km = kmeta[mid]
        ev_games[eid].add(km["game"])
        st, kmid, bid, ask, gap = kalshi_at(kq, mid, ts)
        if gap is not None:
            kgaps.append(gap)
        if st != "ok":
            census["line: " + st] += 1
            continue
        census["line: MATCHED"] += 1
        matched.append({"ts": ts, "eid": eid, "mid": mid, "game": km["game"], "week": km["week"],
                        "gsis": gsis, "stat": stat, "line": line, "kick": km["kick"],
                        "lead": km["kick"] - ts, "k": kmid, "bid": bid, "ask": ask, "kgap": gap,
                        "b": c["p_bench"], "b_all": c["p_all"], "n_bench": c["n_bench"],
                        "n_all": c["n_all"]})
    multi = sum(1 for g in ev_games.values() if len(g) > 1)
    return matched, census, book_ages, kgaps, multi, cons


# =============================================================================
# steps
# =============================================================================

def step1(matched, census, book_ages, kgaps, multi, out):
    out("\n== STEP 1 - pairing ==")
    for k, v in census.items():
        out(f"  {k:<48} {v:>8,}")
    out(f"  Odds API events pairing to >1 nflverse game: {multi}  (must be 0)")
    out(f"  book age at fetch (s), fresh pairs:   p50 {pct(book_ages, .5):.0f}  p90 "
        f"{pct(book_ages, .9):.0f}  p99 {pct(book_ages, .99):.0f}  max {max(book_ages):.0f}")
    out(f"  Kalshi gap fetch - quote (s), every line reaching a rung: n {len(kgaps):,}  "
        f"p50 {pct(kgaps, .5):.0f}  p90 {pct(kgaps, .9):.0f}  p99 {pct(kgaps, .99):.0f}  "
        f"share > 600s {sum(g > STALE for g in kgaps) / len(kgaps):.3f}")
    mg = [r["kgap"] for r in matched]
    out(f"  Kalshi gap, matched: p50 {pct(mg, .5):.0f}  p90 {pct(mg, .9):.0f}  max {max(mg):.0f}")
    close = close_rows(matched)
    for stat in ("receptions", "rush_attempts", None):
        rs = [r for r in close if stat is None or r["stat"] == stat]
        out(f"  CLOSE {stat or 'all':<14} lines {len(rs):>5}  fits "
            f"{len({(r['gsis'], r['stat'], r['game']) for r in rs}):>4}  games "
            f"{len({r['game'] for r in rs}):>3}  weeks {sorted({r['week'] for r in rs})}")
    games = len({r["game"] for r in close})
    fits = len({(r["gsis"], r["stat"], r["game"]) for r in close})
    passed = multi == 0 and games >= THIN_GAMES and fits >= THIN_FITS
    out(f"  THIN RULE (>= {THIN_GAMES} games and >= {THIN_FITS} fits at the close, 0 multi-game "
        f"events): {'PASS' if passed else 'FAIL'}")
    return passed, {"games": games, "fits": fits, "lines": len(close), "multi": multi,
                    "census": dict(census)}


def close_rows(matched):
    """Per game, the last snapshot < kickoff, within 15 min of it."""
    last = {}
    for r in matched:
        g = r["game"]
        if r["lead"] <= CLOSE_LEAD and (g not in last or r["ts"] > last[g]):
            last[g] = r["ts"]
    return [r for r in matched if last.get(r["game"]) == r["ts"]]


def taker_edge(r, contracts):
    """Crossing toward the book, p_bench treated as fair. None when no side is priced
    through."""
    mt = series_multiplier(r["mid"])[1]          # (maker_M, taker_M)
    fair = r["b"]
    if fair > r["ask"]:
        price = r["ask"]
        return fair - price - fee_per_contract(price, contracts, "taker", mt)
    if fair < r["bid"]:
        price = 1 - r["bid"]
        return (1 - fair) - price - fee_per_contract(price, contracts, "taker", mt)
    return None


def touch_size(depth, r):
    best = None
    for ts, side, tp, tsz in depth.get(r["mid"], []):
        if abs(ts - r["ts"]) <= 60 and tsz is not None:
            want = "buy_yes" if r["b"] > r["k"] else "buy_no"
            if side == want and (best is None or abs(ts - r["ts"]) < best[0]):
                best = (abs(ts - r["ts"]), tsz)
    return None if best is None else best[1]


def step2(matched, depth, out):
    out("\n== STEP 2 - the spread at the close: 100 x (Kalshi mid - p_bench), pp ==")
    close = close_rows(matched)
    res = {}
    for stat in ("receptions", "rush_attempts"):
        rs = [dict(r, s=100 * (r["k"] - r["b"])) for r in close if r["stat"] == stat]
        if not rs:
            out(f"  {stat}: no lines")
            continue
        s = [r["s"] for r in rs]
        iv = block_boot(rs, lambda x: mean(x, lambda r: r["s"]))
        spread_k = [100 * (r["ask"] - r["bid"]) for r in rs]
        e10 = [taker_edge(r, 10) for r in rs]
        e100 = [taker_edge(r, 100) for r in rs]
        pos10 = [e for e in e10 if e is not None and e > 0]
        pos100 = [e for e in e100 if e is not None and e > 0]
        through = sum(e is not None for e in e10)
        tsz = [t for t in (touch_size(depth, r) for r in rs) if t is not None]
        out(f"  {stat}: lines {len(rs)}  games {len({r['game'] for r in rs})}")
        out(f"    p10 {pct(s, .1):+.2f}  p25 {pct(s, .25):+.2f}  p50 {pct(s, .5):+.2f}  "
            f"p75 {pct(s, .75):+.2f}  p90 {pct(s, .9):+.2f}")
        out(f"    mean {fmt_iv(iv)}")
        out(f"    |spread| > {THRESH}pp: {sum(abs(x) > THRESH for x in s) / len(s):.3f}   "
            f"Kalshi quoted spread median {pct(spread_k, .5):.1f}c")
        out(f"    book outside Kalshi's bid-ask (a side priced through): {through} of {len(rs)}")
        out(f"    taker edge > 0 after fee @10: {len(pos10)} ({len(pos10) / len(rs):.3f}), median "
            f"{(100 * statistics.median(pos10)) if pos10 else float('nan'):.2f}pp;  @100: "
            f"{len(pos100)} ({len(pos100) / len(rs):.3f})")
        out(f"    touch size on the side a taker would buy, depth within 60s: n {len(tsz)}  "
            f"median {pct(tsz, .5) if tsz else float('nan')}")
        res[stat] = {"lines": len(rs), "games": len({r["game"] for r in rs}),
                     "fits": len({(r["gsis"], r["game"]) for r in rs}),
                     "p10": pct(s, .1), "p25": pct(s, .25), "p50": pct(s, .5),
                     "p75": pct(s, .75), "p90": pct(s, .9),
                     "mean": iv[0], "lo": iv[1], "hi": iv[2], "n_games": iv[4],
                     "share_abs_gt_2p5": sum(abs(x) > THRESH for x in s) / len(s),
                     "kalshi_spread_c_p50": pct(spread_k, .5),
                     "priced_through": through,
                     "taker_pos_10": len(pos10), "taker_pos_100": len(pos100),
                     "taker_pos_10_median_pp": 100 * statistics.median(pos10) if pos10 else None,
                     "touch_size_n": len(tsz), "touch_size_p50": pct(tsz, .5) if tsz else None}
    out("  by time to kickoff, every matched snapshot (descriptive):")
    ttk = {}
    for stat in ("receptions", "rush_attempts"):
        for lo, name in TTK:
            hi = {0: 3600, 3600: 6 * 3600, 6 * 3600: 24 * 3600, 24 * 3600: 1e12}[lo]
            s = [100 * (r["k"] - r["b"]) for r in matched
                 if r["stat"] == stat and lo < r["lead"] <= hi]
            if s:
                out(f"    {stat:<14} {name:<6} n {len(s):>5}  p25 {pct(s, .25):+.2f}  p50 "
                    f"{pct(s, .5):+.2f}  p75 {pct(s, .75):+.2f}")
                ttk[f"{stat}|{name}"] = {"n": len(s), "p25": pct(s, .25), "p50": pct(s, .5),
                                          "p75": pct(s, .75)}
    res["ttk"] = ttk
    return res


def step3(matched, out):
    out("\n== STEP 3 - who moves first, consecutive book snapshots ==")
    seq = defaultdict(list)
    for r in matched:
        seq[(r["mid"])].append(r)
    pairs = []
    spacing = []
    for rs in seq.values():
        rs.sort(key=lambda r: r["ts"])
        for a, b in zip(rs, rs[1:]):
            gap = a["k"] - a["b"]
            pairs.append({"game": a["game"], "gap": gap, "db": b["b"] - a["b"],
                          "dk": b["k"] - a["k"]})
            spacing.append((b["ts"] - a["ts"]) / 60)
    if not pairs:
        out("  no consecutive pairs")
        return {}
    l1 = block_boot(pairs, lambda x: slope(x, lambda r: r["gap"], lambda r: r["db"]))
    l2 = block_boot(pairs, lambda x: slope(x, lambda r: -r["gap"], lambda r: r["dk"]))
    diff = block_boot(pairs, lambda x: (lambda a, b: None if a is None or b is None else a - b)(
        slope(x, lambda r: r["gap"], lambda r: r["db"]),
        slope(x, lambda r: -r["gap"], lambda r: r["dk"])))
    out(f"  pairs {len(pairs):,}; spacing between book snapshots (min): p10 "
        f"{pct(spacing, .1):.0f}  p50 {pct(spacing, .5):.0f}  p90 {pct(spacing, .9):.0f}")
    out(f"  L1 books toward Kalshi   {fmt_iv(l1, 1, 3)}")
    out(f"  L2 Kalshi toward books   {fmt_iv(l2, 1, 3)}")
    out(f"  L1 - L2                  {fmt_iv(diff, 1, 3)}")
    return {"pairs": len(pairs), "spacing_min_p10": pct(spacing, .1),
            "spacing_min_p50": pct(spacing, .5), "spacing_min_p90": pct(spacing, .9),
            "L1": l1[:3], "L2": l2[:3], "L1_minus_L2": diff[:3], "n_games": l1[4]}


# =============================================================================
# step 4 - the model against the exchange mid (C19-H1)
# =============================================================================

def step4(games, kmeta, kq, cons, resolve, workers, out):
    out("\n== STEP 4 - C19-H1: model vs Kalshi mid, 2026 weeks 2-3, entry kickoff - 180 min ==")
    from research import walkforward as wf
    from core import settlement
    con = ro()
    snaps = wf.snap_rows(con, SEASON)
    pw = wf.player_week_rows(con, SEASON)
    vals = {}
    for g, w, rec, car in con.execute(
            "SELECT gsis_id, week, receptions, carries FROM nfl_player_week w WHERE season=? "
            "AND season_type='REG' AND data_version=(SELECT MAX(data_version) FROM nfl_player_week v "
            "WHERE v.gsis_id=w.gsis_id AND v.season=w.season AND v.week=w.week "
            "AND v.season_type='REG')", (SEASON,)):
        vals[(g, w)] = {"receptions": rec, "rush_attempts": car}
    census = Counter()
    rows = []
    groups = defaultdict(list)
    pos_team = {}
    for mid, km in kmeta.items():
        if km["week"] not in (2, 3):
            continue
        census["outcomes weeks 2-3"] += 1
        g = games.get(km["game"])
        if not g or g["hs"] is None or g["kick"] is None:
            census["game without final score"] += 1
            continue
        key = (km["gsis"], km["week"])
        has = key in vals
        snap, steam, spos = snaps.get((km["gsis"], km["game"]), (None, None, None))
        res, actual, _void, _st = settlement.settle(
            vals[key][km["stat"]] if has else None, has, snap, km["stat"], km["line"], km["push"])
        if res not in (settlement.OVER, settlement.UNDER):
            census[f"settlement {res}"] += 1
            continue
        entry = g["kick"] - ENTRY_LEAD
        st, kmid, _b, _a, _gap = kalshi_at(kq, mid, entry)
        if st != "ok":
            census[f"entry: {st}"] += 1
            continue
        census["common set (settled, two-sided at entry)"] += 1
        pos_team.setdefault((km["gsis"], km["game"]), pw.get(key) or (spos, steam))
        rows.append({"oid": km["oid"], "mid": mid, "game": km["game"], "stat": km["stat"],
                     "gsis": km["gsis"], "line": km["line"], "y": 1.0 if res == settlement.OVER else 0.0,
                     "k": kmid, "entry": entry})
        groups[(km["gsis"], km["stat"], km["game"], g["kick"], km["week"])].append(
            (km["oid"], km["line"], km["push"]))
    # book at the T-180 snapshot: the snapshot per event whose lead is nearest 180 min
    bsnap = defaultdict(dict)
    for (ts, eid, player, stat, line), c in cons.items():
        if c["p_bench"] is None:
            continue
        bsnap[(player, stat, line)][ts] = c["p_bench"]

    cvals = {"default": wf.constants_for(SEASON, {}, lambda a, b: wf.refit_drift(con, a, b)).values}
    tasks = [(SEASON, g, s, gm, k, w, *pos_team.get((g, gm), (None, None)), lines, cvals)
             for (g, s, gm, k, w), lines in sorted(groups.items())]
    out(f"  census {dict(census)}")
    out(f"  fit tasks {len(tasks)}")
    t0 = time.time()
    if workers > 1:
        import multiprocessing as mp
        with mp.Pool(workers, initializer=wf._worker_init, initargs=(config.DB_PATH,)) as pool:
            results = list(pool.imap(wf._predict_task, tasks, chunksize=4))
    else:
        wf._worker_init(config.DB_PATH)
        results = [wf._predict_task(t) for t in tasks]
    out(f"  predicted in {time.time() - t0:.0f}s")
    preds, errs = {}, Counter()
    for res in results:
        if "err" in res:
            errs[res["err"]] += len(res["lines"])
            continue
        preds.update(res["probs"]["default"])
    out(f"  fit errors (outcomes): {dict(errs)}")
    scored = [dict(r, m=preds[r["oid"]]) for r in rows if r["oid"] in preds]

    # attach book p_bench at the snapshot nearest the entry, if within 30 min of it
    name_of = {}
    for (player, stat, line) in bsnap:
        gs, _w = resolve(player)
        if gs:
            name_of.setdefault((gs, stat, line), []).append((player, stat, line))
    for r in scored:
        best = None
        for k in name_of.get((r["gsis"], r["stat"], float(r["line"])), []):
            for ts, p in bsnap[k].items():
                if ts <= r["entry"] + 1800 and abs(ts - r["entry"]) <= 1800:
                    if best is None or abs(ts - r["entry"]) < best[0]:
                        best = (abs(ts - r["entry"]), p)
        r["b"] = None if best is None else best[1]

    def brier(p, y):
        return (p - y) ** 2

    def cl(p):
        return min(max(p, CLIP), 1 - CLIP)

    for r in scored:
        r["dmk"] = brier(r["m"], r["y"]) - brier(r["k"], r["y"])
    out(f"  scored n {len(scored)}  fits {len({(r['gsis'], r['stat'], r['game']) for r in scored})}"
        f"  games {len({r['game'] for r in scored})}")
    bm = mean(scored, lambda r: brier(r["m"], r["y"]))
    bk = mean(scored, lambda r: brier(r["k"], r["y"]))
    out(f"  Brier model {bm:.4f}   Kalshi mid {bk:.4f}   realized over rate "
        f"{mean(scored, lambda r: r['y']):.3f}")
    head = block_boot(scored, lambda x: mean(x, lambda r: r["dmk"]))
    mde = 2.8 * head[3] if head[3] else None
    out(f"  HEADLINE Brier(model) - Brier(Kalshi mid)  {fmt_iv(head, 1, 4)}   MDE {mde:.4f}")
    ll = mean(scored, lambda r: -(r["y"] * math.log(cl(r["m"])) + (1 - r["y"]) * math.log(1 - cl(r["m"])))
              + (r["y"] * math.log(cl(r["k"])) + (1 - r["y"]) * math.log(1 - cl(r["k"]))))
    out(f"  log loss model - Kalshi (descriptive, no interval): {ll:+.4f}")
    per = {}
    for stat in ("receptions", "rush_attempts"):
        rs = [r for r in scored if r["stat"] == stat]
        if rs:
            iv = block_boot(rs, lambda x: mean(x, lambda r: r["dmk"]))
            per[stat] = {"n": len(rs), "est": iv[0], "lo": iv[1], "hi": iv[2], "games": iv[4]}
            out(f"    {stat:<14} n {len(rs):>5}  {fmt_iv(iv, 1, 4)}")
    three = [r for r in scored if r["b"] is not None]
    for r in three:
        r["dmb"] = brier(r["m"], r["y"]) - brier(r["b"], r["y"])
        r["dkb"] = brier(r["k"], r["y"]) - brier(r["b"], r["y"])
    mb = block_boot(three, lambda x: mean(x, lambda r: r["dmb"]))
    kb = block_boot(three, lambda x: mean(x, lambda r: r["dkb"]))
    mk3 = block_boot(three, lambda x: mean(x, lambda r: r["dmk"]))
    out(f"  THREE-WAY common set n {len(three)} (book p_bench within 30 min of entry)")
    if three:
        out(f"    Brier model {mean(three, lambda r: brier(r['m'], r['y'])):.4f}  Kalshi "
            f"{mean(three, lambda r: brier(r['k'], r['y'])):.4f}  book "
            f"{mean(three, lambda r: brier(r['b'], r['y'])):.4f}")
    out(f"    model - book    {fmt_iv(mb, 1, 4)}")
    out(f"    Kalshi - book   {fmt_iv(kb, 1, 4)}")
    out(f"    model - Kalshi  {fmt_iv(mk3, 1, 4)}  (on the three-way rows, descriptive)")
    lo, hi = head[1], head[2]
    if head[4] < 5 or lo is None:
        verdict = "not read (<5 games)"
    elif hi < 0:
        verdict = "model better than the Kalshi mid on weeks 2-3 (a candidate, not an edge)"
    elif lo > 0:
        verdict = "model worse than the Kalshi mid"
    else:
        verdict = "model no better than the Kalshi mid"
    out(f"  DECISION (pre-registered rule): {verdict}")
    con.close()
    return {"census": dict(census), "fit_errors": dict(errs), "n": len(scored),
            "fits": len({(r['gsis'], r['stat'], r['game']) for r in scored}),
            "games": len({r['game'] for r in scored}),
            "brier_model": bm, "brier_kalshi": bk, "head": head[:3], "se": head[3], "mde": mde,
            "logloss_diff": ll, "per_stat": per, "three_n": len(three),
            "model_minus_book": mb[:3], "kalshi_minus_book": kb[:3],
            "model_minus_kalshi_three": mk3[:3],
            "three_brier": ({"model": mean(three, lambda r: brier(r['m'], r['y'])),
                             "kalshi": mean(three, lambda r: brier(r['k'], r['y'])),
                             "book": mean(three, lambda r: brier(r['b'], r['y']))} if three else None),
            "verdict": verdict}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", required=True, help="local scratch sqlite, never committed")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    if a.extract:
        extract(a.cache)
        return 0
    games, kout, kmeta, kq, depth, book = load(a.cache)
    print(f"loaded: book rows {len(book):,}  kalshi markets {len(kmeta):,}  games {len(games)}")
    if not book or not kmeta:
        raise SystemExit("empty cache - nothing to pair; refusing to report")
    resolve = resolver()
    matched, census, book_ages, kgaps, multi, cons = pair(games, kout, kmeta, kq, book, resolve)
    out = print
    passed, s1 = step1(matched, census, book_ages, kgaps, multi, out)
    result = {"step1": s1, "step1_pass": passed}
    if passed:
        result["step2"] = step2(matched, depth, out)
        result["step3"] = step3(matched, out)
    else:
        out("STEP 1 FAILED - steps 2 and 3 do not run (pre-registered)")
    if not a.skip_model:
        result["step4"] = step4(games, kmeta, kq, cons, resolve, a.workers, out)
    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(result, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
