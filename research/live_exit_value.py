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
        CREATE TABLE depth (market_id TEXT, ts REAL, side TEXT, touch_price REAL, touch_size REAL);
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
        d = src.execute("""SELECT ts, side, touch_price, touch_size FROM market_depth
                            WHERE venue='kalshi' AND market_id=? AND ts BETWEEN ? AND ?""",
                        (mid, lo, hi)).fetchall()
        out.executemany("INSERT INTO depth VALUES (?,?,?,?,?)", [(mid, *r) for r in d])
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
    for mid, ts, side, tp, tsz in c.execute("SELECT * FROM depth ORDER BY market_id, ts"):
        depth[mid].append((ts, side, tp, tsz))
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
