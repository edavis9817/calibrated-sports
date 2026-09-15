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
import glob
import json
import math
import os
import sys
import zlib
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
# Population-aware: results/h2.jsonl for week 1, h2_wk2.jsonl for week 2.
REG_PATH = S.registry_path("h2")
CFB_REG_PATH = os.path.join(S.ROOT, "research", "sweep", "results", "h2_cfb.jsonl")


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


def load_games(c, season=S.SEASON, week=S.WEEK):
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
    S.open_population()                 # no-op for week 1; refuses week 2 until settled
    reg = S.Registry(REG_PATH)
    add = functools.partial(reg.add, role=S.ROLE, population=S.POPULATION)
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
                    add("H2 slope", name, None, unit="pp per unit I")
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
                add("H2 slope", name, res, unit="pp per unit I", scale=100.0)
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
                    add("H2 economic", name, None)
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
                add("H2 economic", name, res, scale=100.0,
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


# =============================================================================
# CFB replication (brief 022 phase 2 - holdout B)
# =============================================================================
#
# SOURCE. The NFL imbalance came from market_depth, which is the /orderbooks L2
# touch. CFB has no depth table, but the raw cfb_kalshi archive holds the same
# /orderbooks payloads, polled every ~60s per ticker. The /markets payloads also
# carry yes_bid_size_fp / yes_ask_size_fp, but a ticker appears there only every
# ~950s (discovery cadence), which cannot feed a 60s or 300s horizon. So I comes
# from /orderbooks touch levels, and the future mid from cfb_probe.db quotes
# as-of - the same split as NFL (depth for I, quotes for the mid).
#
# RECOVERY. 33 of 57 cfb_kalshi shards fail `gzip -t` (two probe processes
# appended to one file). archive_raw opens the file per record, so every record
# is its own gzip member, and zlib's gzip mode verifies each member's CRC32 and
# length. Scanning for member headers and keeping only members that decompress
# to EOF recovers exactly the bytes that were written intact - nothing is
# imputed; a torn member is counted and dropped.
#
# DUPLICATE POLLS. While two processes ran, each ticker was polled ~twice per
# minute. Imbalance instants are thinned to one per ticker per CFB_THIN seconds
# so those hours do not count double against single-process hours.

CFB_SERIES = ("KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFGAME", "KXNCAAFTEAMTOTAL")
CFB_THIN = 55.0
CFB_VENUE = "cfb_kalshi"
GZ_MAGIC = b"\x1f\x8b\x08"


def recover_members(data, stats, max_span=4):
    """Yield the decompressed bytes of every intact gzip member in `data`.

    A candidate member starts at a magic header and is tried against the next
    1..max_span headers as its end (magic bytes can occur inside compressed
    data). zlib raises or stops short on a torn member and checks the CRC32 +
    ISIZE trailer on a complete one, so only verified members are yielded."""
    mv = memoryview(data)
    heads, pos = [], 0
    while True:
        i = data.find(GZ_MAGIC, pos)
        if i < 0:
            break
        heads.append(i)
        pos = i + 1
    heads.append(len(data))
    stats["size"] += len(data)
    k = 0
    while k < len(heads) - 1:
        h, done = heads[k], False
        for span in range(1, max_span + 1):
            if k + span >= len(heads):
                break
            d = zlib.decompressobj(31)
            try:
                out = d.decompress(mv[h:heads[k + span]])
            except zlib.error:
                continue
            if d.eof:
                used = heads[k + span] - h - len(d.unused_data)
                stats["members"] += 1
                stats["bytes"] += used
                end = h + used
                while k < len(heads) - 1 and heads[k] < end:
                    k += 1
                done = True
                yield out
                break
        if not done:
            stats["failed_starts"] += 1
            k += 1


def book_touch(book):
    """(yes_bid, yes_bid_size, yes_ask, yes_ask_size) from one /orderbooks book.
    Both ladders are BIDS: the YES ask is 1 - best NO bid, and the size resting
    there is the NO bid's size."""
    ob = book.get("orderbook_fp") or {}
    yes, no = ob.get("yes_dollars") or [], ob.get("no_dollars") or []
    if not yes or not no:
        return None
    yb = max(yes, key=lambda lvl: float(lvl[0]))
    nb = max(no, key=lambda lvl: float(lvl[0]))
    return float(yb[0]), float(yb[1]), 1.0 - float(nb[0]), float(nb[1])


def extract_shard(path, series=CFB_SERIES):
    """One raw shard -> per-ticker (ts, I) arrays plus recovery statistics."""
    prefixes = tuple(s + "-" for s in series)
    stats = Counter()
    with open(path, "rb") as f:
        data = f.read()
    idx, tick, ts, imb = {}, array("i"), array("d"), array("f")
    for raw in recover_members(data, stats):
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_json_lines"] += 1
                continue
            if not rec.get("endpoint", "").startswith("orderbooks"):
                continue
            stats["orderbook_records"] += 1
            t = rec.get("ts")
            for b in (rec.get("payload") or {}).get("orderbooks") or []:
                tk = b.get("ticker") or ""
                if not tk.startswith(prefixes):
                    continue
                stats["books"] += 1
                touch = book_touch(b)
                I = None if touch is None else imbalance(touch[1], touch[3])
                if I is None:
                    stats["books_one_sided"] += 1
                    continue
                tick.append(idx.setdefault(tk, len(idx)))
                ts.append(t)
                imb.append(I)
    names = [None] * len(idx)
    for k, v in idx.items():
        names[v] = k
    rel = os.path.relpath(path, os.path.dirname(os.path.dirname(path))).replace(os.sep, "/")
    return rel, names, tick.tobytes(), ts.tobytes(), imb.tobytes(), dict(stats)


def thin(ts, I, min_gap=CFB_THIN):
    """Sort by time and keep one instant per `min_gap` seconds."""
    order = np.argsort(ts, kind="stable")
    ts, I = ts[order], I[order]
    keep, last = [], -np.inf
    for i, t in enumerate(ts):
        if t - last >= min_gap:
            keep.append(i)
            last = t
    keep = np.asarray(keep, dtype=np.int64)
    return ts[keep], I[keep]


def extract_all(raw_dir, workers=6):
    from concurrent.futures import ProcessPoolExecutor
    paths = sorted(glob.glob(os.path.join(raw_dir, "*", "*.jsonl.gz")))
    per_ticker = defaultdict(lambda: ([], []))
    shard_stats = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for rel, names, tick, ts, imb, st in ex.map(extract_shard, paths):
            shard_stats.append((rel, st))
            tick = np.frombuffer(tick, dtype=np.int32)
            ts = np.frombuffer(ts, dtype=float)
            imb = np.frombuffer(imb, dtype=np.float32)
            for j, name in enumerate(names):
                m = tick == j
                per_ticker[name][0].append(ts[m])
                per_ticker[name][1].append(imb[m])
    samples = {}
    for name, (tl, il) in per_ticker.items():
        samples[name] = thin(np.concatenate(tl), np.concatenate(il).astype(float))
    return samples, shard_stats


def save_cache(path, samples, shard_stats):
    names = sorted(samples)
    np.savez_compressed(
        path, names=np.array(names),
        lengths=np.array([len(samples[n][0]) for n in names]),
        ts=np.concatenate([samples[n][0] for n in names]) if names else np.array([]),
        I=np.concatenate([samples[n][1] for n in names]) if names else np.array([]),
        shard_stats=np.array(json.dumps(shard_stats)))


def load_cache(path):
    z = np.load(path, allow_pickle=False)
    samples, off = {}, 0
    for name, n in zip(z["names"], z["lengths"]):
        samples[str(name)] = (z["ts"][off:off + n], z["I"][off:off + n])
        off += n
    return samples, json.loads(str(z["shard_stats"]))


def cfb_games(c):
    """Kalshi CFB game key (26SEP10FAMUMIA) -> CFBD scheduled start_ts, through
    the matcher brief C01 validated (120 of 120)."""
    from research.cfb_calibration import match
    games, unmatched = match(c)
    return {k: g["start_ts"] for k, g in games.items()}, unmatched


def nfl_analogue(cfb_series):
    return {"KXNCAAFSPREAD": "KXNFLSPREAD", "KXNCAAFTOTAL": "KXNFLTOTAL",
            "KXNCAAFGAME": "KXNFLGAME"}.get(cfb_series)


def collect_cfb(c, samples, kicks):
    gidx = {g: i for i, g in enumerate(sorted(kicks))}
    cells, census = defaultdict(Cell), Counter()
    for tk in sorted(samples):
        s = tk.split("-")[0]
        gkey = tk.split("-")[1] if tk.count("-") >= 2 else None
        if gkey not in kicks:
            census["ticker: no matched CFBD game"] += 1
            continue
        kick = kicks[gkey]
        _maker, taker = series_multiplier(s)
        q = c.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue=? AND market_id=? "
                      "AND best_bid IS NOT NULL AND best_ask IS NOT NULL ORDER BY ts",
                      (CFB_VENUE, tk)).fetchall()
        if not q:
            census["ticker: no two-sided quote"] += 1
            continue
        qts = [r[0] for r in q]
        census["tickers"] += 1
        ts, I = samples[tk]
        for t, x in zip(ts.tolist(), I.tolist()):
            tr = tier(t, kick)
            if tr is None:
                census["instant: after the in-game window"] += 1
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
            cell.game.append(gidx[gkey])
            cell.I.append(x)
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


def cell_tests(cell, h, n_games):
    """(slope result, economic result, top-decile cut, gross, cost) - the same
    arithmetic as the NFL run."""
    I = np.frombuffer(cell.I, dtype=float)
    d = np.frombuffer(cell.d[h], dtype=float)
    g = np.frombuffer(cell.game, dtype=np.int32)
    cost = np.frombuffer(cell.cost, dtype=float)
    ok = ~np.isnan(d)
    slope = S.boot(slope_rows(game_suffstats(g[ok], I[ok], d[ok], n_games)), slope_stat)
    if slope:
        slope = dict(slope, n=int(ok.sum()))
    cut = float(np.quantile(np.abs(I), TOP_DECILE))
    m = (np.abs(I) >= cut) & ok
    if not m.any():
        return slope, None, cut, float("nan"), float("nan")
    signed = np.sign(I[m]) * d[m]
    econ = S.boot(mean_rows(g[m], signed - cost[m], n_games), mean_stat)
    if econ:
        econ = dict(econ, n=int(m.sum()))
    return slope, econ, cut, float(signed.mean()), float(cost[m].mean())


def _nfl_signs(path):
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r.get("estimable"):
                    sig = r["lo"] > 0 or r["hi"] < 0
                    out[(r["family"], r["name"])] = ("+" if r["est"] > 0 else "-") + ("*" if sig else "")
    return out


def run_cfb(cache=None, workers=6):
    c = S.cfb_ro()                       # refuses until the candidates doc is committed
    raw_dir = S.cfb_raw_dir()
    if cache and os.path.exists(cache):
        samples, shard_stats = load_cache(cache)
    else:
        samples, shard_stats = extract_all(raw_dir, workers)
        if cache:
            save_cache(cache, samples, shard_stats)
    kicks, unmatched = cfb_games(c)
    cells, census, n_games = collect_cfb(c, samples, kicks)
    nfl = _nfl_signs(os.path.join(S.ROOT, "research", "sweep", "results", "h2.jsonl"))

    if os.path.exists(CFB_REG_PATH):
        os.remove(CFB_REG_PATH)
    reg = S.Registry(CFB_REG_PATH)

    def add2(family, name, res, **kw):
        reg.add(family, name, res, role="replication", population="cfb", **kw)
        reg.add(f"{family} cfb population", name, res, role="search", population="cfb", **kw)

    print("=" * 100)
    print("H2 - ORDER BOOK IMBALANCE, CFB (holdout B: replication + population)")
    print("=" * 100)
    strict = sum(1 for _, st in shard_stats if st.get("failed_starts", 0) == 0)
    tot = Counter()
    for _, st in shard_stats:
        tot.update(st)
    print(f"  raw shards {len(shard_stats)}; with no torn member (gzip-readable) {strict} "
          f"({100 * strict / max(1, len(shard_stats)):.0f}%)")
    print(f"  member recovery: {tot['members']:,} intact members, {tot['failed_starts']:,} torn starts "
          f"dropped, {100 * tot['bytes'] / max(1, tot['size']):.2f}% of bytes recovered")
    print(f"  orderbook records {tot['orderbook_records']:,}; series books {tot['books']:,}; one-sided "
          f"{tot['books_one_sided']:,}; bad json lines {tot['bad_json_lines']}")
    per_hour = sorted((rel, st.get("failed_starts", 0), st.get("members", 0)) for rel, st in shard_stats)
    torn = [f"{rel}({f})" for rel, f, _m in per_hour if f]
    print(f"  shards with torn members (count): {', '.join(torn) if torn else 'none'}")
    print(f"  CFBD-matched game keys {len(kicks)}; unmatched moneylines {len(unmatched)}")
    for k, v in sorted(census.items()):
        print(f"  {k:<48}{v:>12,}")

    print("\n  RESOLUTION: same-quote-row share of the as-of lookup at t+h")
    for (s, tr), cell in sorted(cells.items()):
        parts = []
        for h in HORIZONS:
            d = np.frombuffer(cell.d[h], dtype=float)
            ok = ~np.isnan(d)
            same = np.frombuffer(cell.same[h], dtype=np.int8)[ok]
            parts.append(f"{h:>3}s same-row {100 * same.mean():5.1f}%" if ok.any() else f"{h}s -")
        print(f"    {s:<18} {tr:<3} " + " | ".join(parts))

    print("\n  SLOPE (pp per unit I) and ECONOMICS (top-decile signed move minus cost, pp)"
          "\n  NFL column = sign of the week-1 search estimate, * if its interval excluded zero.")
    for s in CFB_SERIES:
        for tr in TIERS:
            for h in HORIZONS:
                name = f"{s}|{tr}|{h}s"
                an = nfl_analogue(s)
                ns = nfl.get(("H2 slope", f"{an}|{tr}|{h}s"), "-") if an else "n/a"
                ne = nfl.get(("H2 economic", f"{an}|{tr}|{h}s"), "-") if an else "n/a"
                cell = cells.get((s, tr))
                if h == 10:
                    note = "not estimable: CFB poll cadence ~60s per ticker"
                    add2("H2 slope", name, None, unit="pp per unit I", note=note)
                    add2("H2 economic", name, None, note=note)
                    print(f"    {name:<28} NOT ESTIMABLE (cadence)          NFL slope {ns:<3} econ {ne}")
                    continue
                if cell is None or len(cell.I) == 0:
                    add2("H2 slope", name, None, unit="pp per unit I")
                    add2("H2 economic", name, None)
                    print(f"    {name:<28} n/a                               NFL slope {ns:<3} econ {ne}")
                    continue
                slope, econ, cut, gross, cost = cell_tests(cell, h, n_games)
                add2("H2 slope", name, slope, unit="pp per unit I", scale=100.0)
                add2("H2 economic", name, econ, scale=100.0,
                     note=f"|I| >= {cut:.3f}; gross {100 * gross:+.3f}pp cost {100 * cost:.3f}pp")
                print(f"    {name:<28} slope {fmt(slope)}   NFL {ns}")
                print(f"    {'':<28} econ  {fmt(econ)}  gross {100 * gross:+.3f} cost {100 * cost:.3f}   NFL {ne}")
    print(f"\n  registry: {CFB_REG_PATH}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cfb", action="store_true", help="holdout B: replicate on college football")
    ap.add_argument("--cache", help="npz of extracted CFB imbalance samples (read if present, else written)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.cfb:
        run_cfb(a.cache, a.workers)
    else:
        run()


if __name__ == "__main__":
    main()
