"""c-21 - is the live exchange price a fair exit?

    python -m research.live_exit_value --cache D:/temp/c21/extract.sqlite --extract
    python -m research.live_exit_value --cache D:/temp/c21/extract.sqlite --density
    python -m research.live_exit_value --cache D:/temp/c21/extract.sqlite --run --json-out F

Scope of every figure: Kalshi `KXNFLREC` and `KXNFLRSHATT` (the YES leg is the
over), NFL 2026 regular season, IN-GAME, games whose quotes are still in the
store. Kalshi is the only venue: Polymarket NFL markets close at kickoff, so
there is no Polymarket in-game price to exit at.

WHAT IS READ, AND HOW
- `market_log.db`, `mode=ro`, by `--extract` only: live Kalshi quotes and depth
  for the mapped REC/RSHATT markets from kickoff - 240 min to kickoff + 330 min,
  the market->outcome map, nfl_games, and the settlement inputs. Everything is
  copied into a SCRATCH store (`--cache`); the analysis reads only that.
- Game state comes from the archived nflverse play_by_play_2026.parquet
  (`--pbp`, newest by default). `time_of_day` is the wall clock of the SNAP.

LICENCE: everything printed or written by `--json-out` is an aggregate or an
interval. No per-market row and no per-position path leaves this process
(`BET_LIST_RESTRICTION`).
"""
import argparse
import glob
import json
import math
import os
import random
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.fees import fee_per_contract, series_of  # noqa: E402

SEASON = 2026
SERIES = {"KXNFLREC": "receptions", "KXNFLRSHATT": "rush_attempts"}
PRE = 240 * 60             # extract from kickoff - 240 min
POST = 330 * 60            # to kickoff + 330 min (live tier is 240; the rest shows the fall-off)
ENTRY_LEAD = 10 * 60       # pre-game entry: kickoff - 10 min (c-19's K)
STALE = 120.0              # a quote older than this at a checkpoint is not a price
CHECKPOINTS = ("end_q1", "halftime", "end_q3")
TICKET = 100
PBUCKETS = ((0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0001))
BOOT, SEED = 2000, 21


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def iso_ts(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


# =============================================================================
# --extract: the only step that reads market_log.db
# =============================================================================

def extract(cache, db=None):
    db = db or config.DB_PATH
    if os.path.exists(cache):
        raise SystemExit(f"{cache} exists; delete it by hand to re-extract")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    src = ro(db)
    out = sqlite3.connect(cache)
    out.executescript("""
        CREATE TABLE kq (market_id TEXT, ts REAL, bid REAL, ask REAL);
        CREATE TABLE kout (market_id TEXT, outcome_id TEXT, gsis TEXT, stat TEXT,
                           line REAL, side TEXT, game_id TEXT, week INTEGER, push INTEGER);
        CREATE TABLE games (game_id TEXT PRIMARY KEY, week INTEGER, kickoff_ts REAL,
                            home_score REAL, away_score REAL);
        CREATE TABLE depth (market_id TEXT, ts REAL, side TEXT, touch_price REAL, touch_size REAL,
                            vwap_100 REAL);
        CREATE TABLE pw (gsis TEXT, week INTEGER, receptions REAL, carries REAL);
        CREATE TABLE snaps (gsis TEXT, game_id TEXT, team TEXT, off REAL, dfn REAL);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    t0 = time.time()
    g = src.execute("""
        SELECT game_id, week, kickoff_ts, home_score, away_score
          FROM nfl_games n WHERE season = ? AND data_version = (
               SELECT MAX(data_version) FROM nfl_games m WHERE m.game_id = n.game_id)
    """, (SEASON,)).fetchall()
    out.executemany("INSERT OR REPLACE INTO games VALUES (?,?,?,?,?)", g)
    kick = {r[0]: r[2] for r in g}
    kout = src.execute("""
        SELECT mo.market_id, o.outcome_id, o.entity_id, o.stat, o.line, o.side, o.event_id,
               o.week, o.push_possible
          FROM market_outcome mo JOIN outcomes o USING (outcome_id)
         WHERE mo.venue = 'kalshi' AND o.season = ? AND o.stat IN ('receptions', 'rush_attempts')
    """, (SEASON,)).fetchall()
    out.executemany("INSERT INTO kout VALUES (?,?,?,?,?,?,?,?,?)", kout)
    nq = nd = 0
    for (mid, _o, _g, _s, _l, _sd, game, _w, _p) in kout:
        k = kick.get(game)
        if k is None:
            continue
        lo, hi = k - PRE, k + POST
        # `source` filtered in Python, as c-19 found: in the WHERE clause the
        # planner picks ix_quotes_ingest and walks every live row per market.
        rows = [(ts, b, a) for ts, b, a, s in src.execute(
            """SELECT ts, best_bid, best_ask, source FROM quotes INDEXED BY ix_quotes_market_ts
                WHERE venue='kalshi' AND market_id=? AND ts BETWEEN ? AND ?""", (mid, lo, hi))
            if s == "live"]
        out.executemany("INSERT INTO kq VALUES (?,?,?,?)", [(mid, *r) for r in rows])
        nq += len(rows)
        d = src.execute("""SELECT ts, side, touch_price, touch_size, vwap_100 FROM market_depth
                            WHERE venue='kalshi' AND market_id=? AND ts BETWEEN ? AND ?""",
                        (mid, lo, hi)).fetchall()
        out.executemany("INSERT INTO depth VALUES (?,?,?,?,?,?)", [(mid, *r) for r in d])
        nd += len(d)
    out.executemany("INSERT INTO pw VALUES (?,?,?,?)", src.execute(
        "SELECT gsis_id, week, receptions, carries FROM nfl_player_week w WHERE season=? "
        "AND season_type='REG' AND data_version=(SELECT MAX(data_version) FROM nfl_player_week v "
        "WHERE v.gsis_id=w.gsis_id AND v.season=w.season AND v.week=w.week "
        "AND v.season_type='REG')", (SEASON,)).fetchall())
    from research import walkforward as wf
    snaps = wf.snap_rows(src, SEASON)
    out.executemany("INSERT INTO snaps VALUES (?,?,?,?,?)",
                    [(gs, gm, s[0], s[1], s[2]) for (gs, gm), (s, _t, _p) in snaps.items()])
    out.execute("INSERT INTO meta VALUES ('extracted_ts', ?)", (str(time.time()),))
    out.execute("INSERT INTO meta VALUES ('db', ?)", (str(db),))
    out.execute("CREATE INDEX ix_kq ON kq(market_id, ts)")
    out.execute("CREATE INDEX ix_depth ON depth(market_id, ts)")
    out.commit()
    out.close()
    src.close()
    print(f"extract: {len(kout):,} mapped outcomes, {nq:,} quotes, {nd:,} depth rows "
          f"in {time.time() - t0:.0f}s -> {cache}")
    if nq == 0:
        raise SystemExit("extract read ZERO quotes - refusing to call that a result")


# =============================================================================
# loading
# =============================================================================

def load(cache):
    c = ro(cache)
    games = {r[0]: {"week": r[1], "kick": r[2], "hs": r[3], "as": r[4]}
             for r in c.execute("SELECT * FROM games")}
    mk = {}
    for mid, oid, gsis, stat, line, side, game, week, push in c.execute("SELECT * FROM kout"):
        if side != "over" or series_of(mid) not in SERIES:
            continue
        mk[mid] = {"oid": oid, "gsis": gsis, "stat": stat, "line": line, "game": game,
                   "week": week, "push": push, "kick": games.get(game, {}).get("kick")}
    kq = defaultdict(list)
    for mid, ts, b, a in c.execute("SELECT market_id, ts, bid, ask FROM kq ORDER BY market_id, ts"):
        kq[mid].append((ts, b, a))
    depth = defaultdict(list)
    for mid, ts, side, tp, tsz, v100 in c.execute("SELECT * FROM depth ORDER BY market_id, ts"):
        depth[mid].append((ts, side, tp, tsz, v100))
    pw = {(g, w): {"receptions": r, "rush_attempts": ca}
          for g, w, r, ca in c.execute("SELECT * FROM pw")}
    snaps = {(gs, gm): (t, o, d) for gs, gm, t, o, d in c.execute("SELECT * FROM snaps")}
    meta = dict(c.execute("SELECT * FROM meta"))
    c.close()
    return games, mk, kq, depth, pw, snaps, meta


def latest_pbp(raw_dir=None):
    fs = sorted(glob.glob(os.path.join(raw_dir or config.RAW_DIR, "nflverse", "*",
                                       f"play_by_play_{SEASON}.parquet")))
    if not fs:
        raise SystemExit("no play_by_play parquet in the raw archive")
    return fs[-1]


def game_clock(pbp_path):
    """game_id -> {checkpoint: wall ts, 'end': ts}, and (game, gsis) -> sorted event ts
    for receptions and rush attempts. Checkpoints are the midpoint between the last
    snap of one quarter and the first snap of the next: the break, not a play."""
    import polars as pl
    df = pl.read_parquet(pbp_path, columns=[
        "game_id", "week", "qtr", "time_of_day", "receiver_player_id", "rusher_player_id",
        "complete_pass", "rush_attempt", "two_point_attempt", "play_type"])
    df = df.filter(pl.col("time_of_day").is_not_null())
    clocks, events = {}, defaultdict(list)
    for (gid,), sub in df.group_by(["game_id"]):
        ts_q = defaultdict(list)
        for r in sub.iter_rows(named=True):
            t = iso_ts(r["time_of_day"])
            q = int(r["qtr"]) if r["qtr"] is not None else None
            if q is not None:
                ts_q[q].append(t)
            if r["two_point_attempt"] == 1:
                continue
            if r["complete_pass"] == 1 and r["receiver_player_id"]:
                events[(gid, r["receiver_player_id"], "receptions")].append(t)
            if r["rush_attempt"] == 1 and r["rusher_player_id"]:
                events[(gid, r["rusher_player_id"], "rush_attempts")].append(t)
        ck = {}
        for name, a, b in (("end_q1", 1, 2), ("halftime", 2, 3), ("end_q3", 3, 4)):
            if ts_q.get(a) and ts_q.get(b):
                ck[name] = (max(ts_q[a]) + min(ts_q[b])) / 2.0
        ck["end"] = max(max(v) for v in ts_q.values()) if ts_q else None
        ck["start"] = min(min(v) for v in ts_q.values()) if ts_q else None
        clocks[gid] = ck
    for k in events:
        events[k].sort()
    return clocks, events


# =============================================================================
# --density: is there an in-game sample at all?
# =============================================================================

def quarter_of(t, ck):
    if ck.get("start") is None or t < ck["start"]:
        return "pre"
    for name, q in (("end_q1", "Q1"), ("halftime", "Q2"), ("end_q3", "Q3")):
        if name not in ck or t < ck[name]:
            return q
    return "Q4" if ck.get("end") is not None and t <= ck["end"] else "post"


def density(cache, pbp, out=print):
    games, mk, kq, depth, pw, snaps, meta = load(cache)
    clocks, _ev = game_clock(pbp)
    out(f"== DENSITY - Kalshi REC/RSHATT, quotes per market per game, by phase ==")
    out(f"cache extracted {datetime.fromtimestamp(float(meta['extracted_ts'])).isoformat()} ; pbp {pbp}")
    per = defaultdict(lambda: defaultdict(list))   # (week) -> phase -> [quotes per market]
    gaps = defaultdict(list)
    games_seen = defaultdict(set)
    res = {}
    for mid, m in mk.items():
        ck = clocks.get(m["game"])
        if not ck or m["kick"] is None:
            continue
        cnt = Counter()
        rows = kq.get(mid, [])
        prev = None
        for ts, b, a in rows:
            ph = quarter_of(ts, ck)
            cnt[ph] += 1
            if ph in ("Q1", "Q2", "Q3", "Q4") and prev is not None:
                gaps[ph].append(ts - prev)
            prev = ts
        for ph in ("pre", "Q1", "Q2", "Q3", "Q4", "post"):
            per[m["week"]][ph].append(cnt[ph])
        if any(cnt[p] for p in ("Q1", "Q2", "Q3", "Q4")):
            games_seen[m["week"]].add(m["game"])
    for w in sorted(per):
        row = {}
        n = len(per[w]["Q1"])
        s = f"  week {w}: {n:,} markets, {len(games_seen[w])} games with any in-game quote |"
        for ph in ("pre", "Q1", "Q2", "Q3", "Q4", "post"):
            xs = sorted(per[w][ph])
            med = xs[len(xs) // 2] if xs else 0
            zero = sum(1 for x in xs if x == 0) / len(xs) if xs else float("nan")
            row[ph] = {"median_quotes": med, "share_zero": round(zero, 4)}
            s += f" {ph} med {med} zero {zero:.0%} |"
        res[f"week{w}"] = {"markets": n, "games_in_game": len(games_seen[w]), "phases": row}
        out(s)
    gq = {}
    for ph in ("Q1", "Q2", "Q3", "Q4"):
        xs = sorted(gaps[ph])
        if xs:
            gq[ph] = {"p50": round(xs[len(xs) // 2], 1), "p90": round(xs[int(0.9 * len(xs))], 1),
                      "p99": round(xs[int(0.99 * len(xs))], 1)}
            out(f"  inter-quote gap {ph}: p50 {gq[ph]['p50']}s p90 {gq[ph]['p90']}s p99 {gq[ph]['p99']}s")
    res["gaps"] = gq
    return res


# =============================================================================
# --run: the three questions
# =============================================================================

HEARTBEAT_FRESH = 310.0     # QUOTE_HEARTBEAT_SEC 300 + one 10 s live poll
LIVE_GAP_MAX = 60.0         # game-level rule: median in-game inter-row gap
DEPTH_WINDOW = 60.0


def quote_at(rows, t):
    """Newest row at or before t -> (status, bid, ask, age)."""
    lo, hi = 0, len(rows)
    while lo < hi:
        m = (lo + hi) // 2
        if rows[m][0] <= t:
            lo = m + 1
        else:
            hi = m
    if lo == 0:
        return "none", None, None, None
    ts, b, a = rows[lo - 1]
    age = t - ts
    if age > HEARTBEAT_FRESH:
        return "stale", b, a, age
    if b is None or a is None or not (b > 0) or not (a < 1):
        return "one_sided", b, a, age
    return "ok", b, a, age


def depth_at(drows, t, side):
    """Nearest depth snapshot within DEPTH_WINDOW -> (touch_size, vwap_100) or None.
    vwap_100 is NULL in the store when the book cannot fill 100 contracts."""
    best = None
    for ts, sd, _tp, tsz, v100 in drows:
        if sd != side or abs(ts - t) > DEPTH_WINDOW:
            continue
        if best is None or abs(ts - t) < abs(best[0] - t):
            best = (ts, tsz, v100)
    return None if best is None else best[1:]


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = q * (len(xs) - 1)
    lo = int(math.floor(i))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def boot(rows, fn, draws=BOOT, seed=SEED):
    """Game block bootstrap -> {est, lo, hi, se, n, games}."""
    by = defaultdict(list)
    for r in rows:
        by[r["game"]].append(r)
    games = sorted(by)
    est = fn(rows) if rows else None
    if len(games) < 2 or est is None:
        return {"est": est, "lo": None, "hi": None, "se": None, "n": len(rows), "games": len(games)}
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        sample = []
        for _g in range(len(games)):
            sample.extend(by[games[rng.randrange(len(games))]])
        v = fn(sample)
        if v is not None:
            vals.append(v)
    mv = sum(vals) / len(vals)
    se = (sum((v - mv) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 if len(vals) > 1 else None
    return {"est": est, "lo": pct(vals, 0.025), "hi": pct(vals, 0.975), "se": se,
            "n": len(rows), "games": len(games)}


def mean_of(f):
    def g(rows):
        return sum(f(r) for r in rows) / len(rows) if rows else None
    return g


def slope_of(fx, fy):
    def g(rows):
        if len(rows) < 3:
            return None
        xs = [fx(r) for r in rows]
        ys = [fy(r) for r in rows]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            return None
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return g


def var(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def var_ratio(fh, fe):
    def g(rows):
        vh = var([fh(r) for r in rows])
        ve = var([fe(r) for r in rows])
        if not vh:
            return None
        return 1.0 - ve / vh
    return g


def bucket(p):
    for lo, hi in PBUCKETS:
        if lo <= p < hi:
            return f"{lo:.1f}-{min(hi, 1.0):.1f}"
    return None


def fmt(t, scale=100.0, d=2):
    if t is None or t.get("est") is None:
        return "n/a"
    s = f"{t['est'] * scale:+.{d}f}"
    if t.get("lo") is not None:
        s += f" [{t['lo'] * scale:+.{d}f}, {t['hi'] * scale:+.{d}f}]"
    return s + f" (n={t['n']:,}, games={t['games']})"


def live_games_rule(games, mk, kq, clocks):
    """Pre-registered game-level rule: median in-game inter-row gap <= LIVE_GAP_MAX."""
    gaps_by_game = defaultdict(list)
    for mid, m in mk.items():
        ck = clocks.get(m["game"])
        if not ck or ck.get("start") is None:
            continue
        prev = None
        for ts, _b, _a in kq.get(mid, []):
            if ck["start"] <= ts <= ck["end"]:
                if prev is not None:
                    gaps_by_game[m["game"]].append(ts - prev)
                prev = ts
    live, excluded = set(), Counter()
    for g, gaps in gaps_by_game.items():
        med = pct(gaps, 0.5)
        if med is not None and med <= LIVE_GAP_MAX:
            live.add(g)
        else:
            excluded[f"week {games[g]['week']}"] += 1
    return live, excluded


def positions(games, mk, kq, depth, pw, snaps, clocks, events, live, census, recon):
    """-> (pos, cost_rows). pos: one row per (market, checkpoint) with a fresh two-sided
    quote; cost_rows: every fresh quote incl. one-sided, incl. the pre-game instant."""
    from core import settlement
    pos, cost_rows = [], []
    for mid, m in mk.items():
        if m["game"] not in live:
            continue
        census["markets in live games"] += 1
        g = games[m["game"]]
        key = (m["gsis"], m["week"])
        has = key in pw
        res_, actual, _void, _st = settlement.settle(
            pw[key][m["stat"]] if has else None, has, snaps.get((m["gsis"], m["game"])),
            m["stat"], m["line"], m["push"])
        if res_ not in (settlement.OVER, settlement.UNDER):
            census[f"settlement {res_}"] += 1
            continue
        y = 1.0 if res_ == settlement.OVER else 0.0
        ck = clocks[m["game"]]
        ev = events.get((m["game"], m["gsis"], m["stat"]), [])
        recon["pbp final == settled actual" if len(ev) == actual else "pbp final != settled actual"] += 1
        k = math.floor(m["line"]) + 1
        rows = kq.get(mid, [])
        pre_t = g["kick"] - ENTRY_LEAD
        st0, b0, a0, _ = quote_at(rows, pre_t)
        mid_pre = (b0 + a0) / 2 if st0 == "ok" else None
        census["settled"] += 1
        for name in ("pre",) + CHECKPOINTS:
            t = pre_t if name == "pre" else ck.get(name)
            if t is None:
                census[f"{name}: no checkpoint in pbp"] += 1
                continue
            st, b, a, _age = quote_at(rows, t)
            census[f"{name}: {st}"] += 1
            c = sum(1 for e in ev if e < t) if name != "pre" else 0
            if st == "one_sided":
                census[f"{name}: one_sided, " + ("bid only" if b and b > 0 else
                                                 "ask only" if a is not None and a < 1 else "neither")] += 1
            if name != "pre" and c >= k:
                census[f"{name}: DECIDED (pbp c >= k) -> {st}"] += 1
            base = {"game": m["game"], "stat": m["stat"], "ck": name, "y": y,
                    "decided": c >= k, "r": k - c, "st": st}
            if st in ("ok", "one_sided"):
                base.update({"bid": b, "ask": a})
                cost_rows.append(base)
            if st != "ok":
                continue
            base.update({"mid": (b + a) / 2, "hs": (a - b) / 2,
                         "fee_y": fee_per_contract(b, TICKET),       # over holder sells YES at the bid
                         "fee_n": fee_per_contract(1 - a, TICKET),   # under holder sells NO at 1 - ask
                         "mid_pre": mid_pre, "e": mid_pre,
                         # a YES sale hits the resting YES bids; market_depth's buy_no touch is
                         # 1 - yes_bid (checked against quotes: 1,571 of 1,790 rows exact), so
                         # its size is the YES-bid size
                         "dy": depth_at(depth.get(mid, []), t, "buy_no"),
                         "dn": depth_at(depth.get(mid, []), t, "buy_yes")})
            dy, dn = base["dy"], base["dn"]
            base["tsz_y"] = dy[0] if dy else None
            base["tsz_n"] = dn[0] if dn else None
            # executable exit of TICKET contracts, per contract, measured from mid:
            # selling YES = buying NO, so the YES seller receives 1 - vwap(buy_no);
            # selling NO = buying YES, so the NO seller receives 1 - vwap(buy_yes).
            base["exec_y"] = ("nodepth" if dy is None else "unfillable" if dy[1] is None
                              else base["mid"] - (1 - dy[1]))
            base["exec_n"] = ("nodepth" if dn is None else "unfillable" if dn[1] is None
                              else dn[1] - base["mid"])
            if name != "pre":
                pos.append(base)
            else:
                cost_rows[-1].update(base)
    return pos, cost_rows


def run(cache, pbp, out=print):
    games, mk, kq, depth, pw, snaps, meta = load(cache)
    clocks, events = game_clock(pbp)
    res = {"scope": "Kalshi KXNFLREC/KXNFLRSHATT YES (= over), NFL 2026 REG, in-game; "
                    "Kalshi only (Polymarket NFL closes at kickoff)",
           "cache_extracted_ts": float(meta["extracted_ts"]),
           "pbp_snapshot": os.path.basename(os.path.dirname(pbp))}
    census, recon = Counter(), Counter()

    live, excluded = live_games_rule(games, mk, kq, clocks)
    res["population"] = {"games_live_tier": len(live),
                         "games_live_by_week": dict(Counter(f"week {games[g]['week']}" for g in live)),
                         "games_excluded_not_live_tier": dict(excluded)}
    out(f"\n== POPULATION == live-tier games {len(live)} "
        f"{res['population']['games_live_by_week']}; excluded {dict(excluded)}")

    pos, cost_rows = positions(games, mk, kq, depth, pw, snaps, clocks, events, live, census, recon)
    res["census"] = dict(census)
    res["pbp_reconciliation"] = dict(recon)
    out(f"census {dict(sorted(census.items()))}")
    out(f"pbp reconciliation {dict(recon)}")
    if not pos:
        raise SystemExit("no in-game position with a fresh two-sided quote - refusing to report a result")

    res["q1_calibration"] = q1_calibration(pos, out)
    res["q2_exit_cost"] = q2_cost(cost_rows, census, out)
    res["q3_exit_vs_hold"] = q3_exit_hold(pos, out)
    return res


def q1_calibration(pos, out):
    out("\n== Q1 - LIVE CALIBRATION (undecided rungs; y - mid in pp) ==")
    und = [r for r in pos if not r["decided"]]
    und_pre = [r for r in und if r["mid_pre"] is not None]
    fx, fy = (lambda r: r["mid"] - r["mid_pre"]), (lambda r: r["y"] - r["mid"])
    q1 = {"H1a_overreaction_slope": boot(und_pre, slope_of(fx, fy))}
    out(f"H1a slope of (y - mid_t) on (mid_t - mid_pre), pooled: {fmt(q1['H1a_overreaction_slope'], 1.0, 3)}")
    for by in ("receptions", "rush_attempts"):
        q1[f"slope_{by}"] = boot([r for r in und_pre if r["stat"] == by], slope_of(fx, fy))
        out(f"   descriptive, {by}: {fmt(q1[f'slope_{by}'], 1.0, 3)}")
    for name in CHECKPOINTS:
        q1[f"slope_{name}"] = boot([r for r in und_pre if r["ck"] == name], slope_of(fx, fy))
        out(f"   descriptive, {name}: {fmt(q1[f'slope_{name}'], 1.0, 3)}")
    q1["H1b_bias_pooled"] = boot(und, mean_of(fy))
    out(f"H1b mean(y - mid), pooled: {fmt(q1['H1b_bias_pooled'])}")
    for name in CHECKPOINTS:
        q1[f"H1b_bias_{name}"] = boot([r for r in und if r["ck"] == name], mean_of(fy))
        out(f"H1b mean(y - mid), {name}: {fmt(q1[f'H1b_bias_{name}'])}")
    # post-hoc: a mid over a wide book near 0 or 1 is pulled toward 0.5, so a "bias" that
    # lives only on wide books is the mid's arithmetic, not the market's belief
    for name in CHECKPOINTS + ("pooled",):
        for lab, f in (("tight (half-spread <= 1c)", lambda r: r["hs"] <= 0.01 + 1e-9),
                       ("wide (half-spread > 1c)", lambda r: r["hs"] > 0.01 + 1e-9)):
            sub = [r for r in und if (name == "pooled" or r["ck"] == name) and f(r)]
            k_ = f"bias_by_spread::{name}::{lab}"
            q1[k_] = boot(sub, mean_of(fy))
            out(f"   descriptive, post-hoc, {name:9s} {lab}: y - mid {fmt(q1[k_])}")
            # the same rungs scored at the price a taker would actually pay on each side
            q1[k_ + "::y-ask"] = boot(sub, mean_of(lambda r: r["y"] - r["ask"]))
            q1[k_ + "::y-bid"] = boot(sub, mean_of(lambda r: r["y"] - r["bid"]))
            out(f"        y - ask {fmt(q1[k_ + '::y-ask'])}; y - bid {fmt(q1[k_ + '::y-bid'])}")
    moves = [fx(r) for r in und_pre]
    t1, t2 = pct(moves, 1 / 3), pct(moves, 2 / 3)
    q1["move_tercile_cuts_pp"] = [t1 * 100, t2 * 100]
    for lab, f in (("move down (bottom third)", lambda x: x <= t1),
                   ("move middle third", lambda x: t1 < x <= t2),
                   ("move up (top third)", lambda x: x > t2)):
        sub = [r for r in und_pre if f(fx(r))]
        q1[f"by_move::{lab}"] = boot(sub, mean_of(fy))
        q1[f"by_move::{lab}"]["mean_move"] = sum(fx(r) for r in sub) / len(sub) if sub else None
        out(f"   descriptive, {lab} (mean move {q1[f'by_move::{lab}']['mean_move'] * 100:+.1f}pp): "
            f"y - mid {fmt(q1[f'by_move::{lab}'])}")
    cal = {}
    out("   calibration by checkpoint x price bucket (priced / realised; y - mid [game boot]):")
    for name in CHECKPOINTS:
        for lo, hi in PBUCKETS:
            sub = [r for r in und if r["ck"] == name and lo <= r["mid"] < hi]
            if not sub:
                continue
            b = boot(sub, mean_of(fy))
            pr = sum(r["mid"] for r in sub) / len(sub)
            rl = sum(r["y"] for r in sub) / len(sub)
            cal[f"{name}::{bucket(lo)}"] = {"priced": pr, "realised": rl, "diff": b}
            out(f"     {name:9s} {bucket(lo)}: {pr:.3f} / {rl:.3f}  {fmt(b)}")
    q1["calibration_table"] = cal
    can = {}
    out("   canonical case - receptions, by remaining needed r = k - c:")
    for name in CHECKPOINTS:
        for lab, f in (("r=1", lambda v: v == 1), ("r=2", lambda v: v == 2), ("r>=3", lambda v: v >= 3)):
            sub = [r for r in und if r["ck"] == name and r["stat"] == "receptions" and f(r["r"])]
            if not sub:
                continue
            b = boot(sub, mean_of(fy))
            can[f"{name}::{lab}"] = {"priced": sum(r["mid"] for r in sub) / len(sub),
                                     "realised": sum(r["y"] for r in sub) / len(sub), "diff": b}
            out(f"     {name:9s} {lab:5s}: priced {can[f'{name}::{lab}']['priced']:.3f} "
                f"realised {can[f'{name}::{lab}']['realised']:.3f}  {fmt(b)}")
    q1["canonical_receptions_by_remaining"] = can
    dec = [r for r in pos if r["decided"]]
    q1["decided"] = {"n": len(dec), "games": len({r["game"] for r in dec}),
                     "realised_over_rate": (sum(r["y"] for r in dec) / len(dec)) if dec else None,
                     "median_mid": pct([r["mid"] for r in dec], 0.5),
                     "median_bid": pct([r["bid"] for r in dec], 0.5)}
    out(f"   decided rungs (pbp c >= k), descriptive: {q1['decided']}")
    se_b, se_s = q1["H1b_bias_pooled"]["se"], q1["H1a_overreaction_slope"]["se"]
    q1["H1b_mde_80"] = 2.8 * se_b if se_b else None
    q1["H1a_mde_80"] = 2.8 * se_s if se_s else None
    out(f"   MDE at 80% power (2.8 x bootstrap SE): bias {q1['H1b_mde_80'] * 100:.2f}pp, "
        f"slope {q1['H1a_mde_80']:.3f}")
    return q1


def q2_cost(cost_rows, census, out):
    out("\n== Q2 - COST OF EXITING (per contract; 100-contract taker order, over holder) ==")
    q2 = {}
    for name in ("pre",) + CHECKPOINTS:
        two = [r for r in cost_rows if r["ck"] == name and r["st"] == "ok"]
        tot = sum(census[f"{name}: {s}"] for s in ("ok", "one_sided", "stale", "none"))
        if not two:
            continue
        hs = [r["hs"] for r in two]
        cost = [r["hs"] + r["fee_y"] for r in two]
        tsz = [r["tsz_y"] for r in two if r.get("tsz_y") is not None]
        tszn = [r["tsz_n"] for r in two if r.get("tsz_n") is not None]
        q2[name] = {"markets_settled": tot, "fresh_two_sided": len(two),
                    "one_sided_share": census[f"{name}: one_sided"] / tot,
                    "stale_or_none_share": (census[f"{name}: stale"] + census[f"{name}: none"]) / tot,
                    "half_spread_p50": pct(hs, 0.5), "half_spread_p90": pct(hs, 0.9),
                    "fee_mean": sum(r["fee_y"] for r in two) / len(two),
                    "cost_mean": boot(two, mean_of(lambda r: r["hs"] + r["fee_y"])),
                    "cost_p50": pct(cost, 0.5), "cost_p90": pct(cost, 0.9),
                    "touch_size_yes_bid_p50": pct(tsz, 0.5), "touch_size_n": len(tsz),
                    "touch_size_no_side_p50": pct(tszn, 0.5),
                    "touch_yes_bid_below_100_share": (sum(1 for x in tsz if x < TICKET) / len(tsz)) if tsz else None}
        out(f"{name:9s}: fresh two-sided {len(two):,}/{tot:,}; one-sided {q2[name]['one_sided_share']:.1%}, "
            f"stale/none {q2[name]['stale_or_none_share']:.1%}; half-spread p50 {pct(hs, .5) * 100:.1f}c "
            f"p90 {pct(hs, .9) * 100:.1f}c; fee mean {q2[name]['fee_mean'] * 100:.2f}c; "
            f"cost {fmt(q2[name]['cost_mean'])}; YES-bid touch p50 {q2[name]['touch_size_yes_bid_p50']} "
            f"(n={len(tsz)}, <{TICKET} contracts {q2[name]['touch_yes_bid_below_100_share'] or 0:.1%}); "
            f"NO-side touch p50 {q2[name]['touch_size_no_side_p50']}")
        for side, key, fk in (("over holder (sell YES)", "exec_y", "fee_y"),
                              ("under holder (sell NO)", "exec_n", "fee_n")):
            st = Counter("fill" if isinstance(r.get(key), float) else r.get(key) for r in two)
            fill = [r for r in two if isinstance(r.get(key), float)]
            b = boot(fill, mean_of(lambda r, key=key, fk=fk: r[key] + r[fk]))
            q2[name][f"exec_{TICKET}::{side}"] = {"status": dict(st), "cost_mean": b,
                                                   "cost_p50": pct([r[key] + r[fk] for r in fill], 0.5)}
            out(f"           executable {TICKET}-contract exit, {side}: {dict(st)}; cost (vs mid, + fee) "
                f"{fmt(b)}, p50 {(q2[name][f'exec_{TICKET}::{side}']['cost_p50'] or 0) * 100:.1f}c")
        byb = {}
        for lo, hi in PBUCKETS:
            sub = [r for r in two if lo <= r["mid"] < hi]
            if sub:
                byb[bucket(lo)] = {"n": len(sub), "half_spread_p50": pct([r["hs"] for r in sub], 0.5),
                                   "cost_mean": sum(r["hs"] + r["fee_y"] for r in sub) / len(sub)}
        q2[name]["by_price_bucket"] = byb
        out("           by bucket: " + "; ".join(
            f"{k} n={v['n']} hs p50 {v['half_spread_p50'] * 100:.1f}c cost {v['cost_mean'] * 100:.1f}c"
            for k, v in byb.items()))
    return q2


LEGS = {
    # (P&L hold, P&L exit, miscalibration term, cost term); hold - exit == miscal + cost exactly
    "over": (lambda r: r["y"] - r["e"], lambda r: r["bid"] - r["fee_y"] - r["e"],
             lambda r: r["y"] - r["mid"], lambda r: r["hs"] + r["fee_y"]),
    "under": (lambda r: (1 - r["y"]) - (1 - r["e"]), lambda r: (1 - r["ask"]) - r["fee_n"] - (1 - r["e"]),
              lambda r: r["mid"] - r["y"], lambda r: r["hs"] + r["fee_n"]),
}


def q3_exit_hold(pos, out):
    out("\n== Q3 - EXIT AGAINST HOLD (entry: Kalshi mid at kickoff - 10 min) ==")
    q3 = {}
    ent = [r for r in pos if r["e"] is not None]
    for scope, rows in (("all", ent), ("undecided", [r for r in ent if not r["decided"]])):
        for name in CHECKPOINTS:
            sub = [r for r in rows if r["ck"] == name]
            for side, (fh, fe, fmis, fcost) in LEGS.items():
                if not sub:
                    continue
                k_ = f"{scope}::{name}::{side}"
                q3[k_] = {"ev_cost": boot(sub, mean_of(lambda r: fh(r) - fe(r))),
                          "miscalibration_term": boot(sub, mean_of(fmis)),
                          "cost_term": boot(sub, mean_of(fcost)),
                          "var_hold": var([fh(r) for r in sub]), "var_exit": var([fe(r) for r in sub]),
                          "variance_reduction": boot(sub, var_ratio(fh, fe))}
                out(f"{scope:9s} {name:9s} {side:5s}: EV cost {fmt(q3[k_]['ev_cost'])} = miscal "
                    f"{fmt(q3[k_]['miscalibration_term'])} + cost {fmt(q3[k_]['cost_term'])}; var reduction "
                    f"{fmt(q3[k_]['variance_reduction'], 100.0, 1)}% "
                    f"(var hold {q3[k_]['var_hold']:.4f}, exit {q3[k_]['var_exit']:.4f})")
    out("   descriptive, post-hoc: TAKER ENTRY at the checkpoint, held to settlement (net of fee):")
    und = [r for r in pos if not r["decided"]]
    for name in CHECKPOINTS:
        sub = [r for r in und if r["ck"] == name]
        for side, f in (("buy YES at ask", lambda r: r["y"] - r["ask"] - fee_per_contract(r["ask"], TICKET)),
                        ("buy NO at 1 - bid", lambda r: (1 - r["y"]) - (1 - r["bid"]) - fee_per_contract(1 - r["bid"], TICKET))):
            k_ = f"taker_entry::{name}::{side}"
            q3[k_] = boot(sub, mean_of(f))
            out(f"     {name:9s} {side:18s}: {fmt(q3[k_])}")
    return q3


# =============================================================================
# main
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--db", required=False, default=None,
                    help="market_log.db; opened mode=ro (config.DB_PATH if omitted)")
    ap.add_argument("--pbp", default=None)
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--density", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    if a.extract:
        extract(a.cache, a.db)
    pbp = a.pbp or latest_pbp()
    result = {}
    if a.density or a.run:
        result["density"] = density(a.cache, pbp)
    if a.run:
        result.update(run(a.cache, pbp))
    if a.json_out and result:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
