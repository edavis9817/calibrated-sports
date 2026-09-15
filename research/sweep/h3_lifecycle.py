"""Brief 022 H3 - the spread and depth lifecycle, NFL week 1. An execution map,
not an edge.

    python -m research.sweep.h3_lifecycle

Pre-registered in docs/briefs/022-preregistration.md (ae3895b), section H3.

TIME-WEIGHTED, NOT ROW-WEIGHTED. Quotes are written on change plus a 5-minute
heartbeat, so a mean over rows over-weights exactly the volatile minutes. Every
market's as-of spread is sampled on a fixed grid - 60s before kickoff, 10s
in-game - and each sample carries its step as its weight. A sample with no quote
in the last 660s (or a one-sided book) is dropped.

Dated series (REC, RSHATT, SPREAD, TOTAL, GAME): blocks are GAMES. Undated
futures (season wins, weekly wins, conference champion pooled AFC+NFC, division
pooled over all eight) are read only before UNDATED_CUTOFF_TS and their blocks
are ET CALENDAR DAYS - there is no game to block on. A futures Sunday is ONE
calendar day (09-13), so its contrast rests on one block however many rows it has.

Touch size (median of min(yes bid size, yes ask size)) comes from market_depth
capture instants, unweighted, on a log histogram accurate to 0.05 dex (~12%).

Tests (search), mean spread contrasts per series: each time-to-kickoff bucket
minus 1-6h (dated only); 00-08 ET minus 09-16; 17-23 minus 09-16; Sunday minus
Mon-Fri (Saturday is in neither side). Medians are descriptive.
"""
import functools
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from research.sweep import common as S  # noqa: E402
from research.sweep.h2_imbalance import event_game, load_games, week1_markets, LIVE_WINDOW  # noqa: E402

DATED = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLGAME")
FUTURES = {
    "WINS": ("KXNFLWINS",),
    "WINSWEEK": ("KXNFLWINSWEEK",),
    "CHAMP": ("KXNFLAFCCHAMP", "KXNFLNFCCHAMP"),
    "DIVISION": tuple(f"KXNFL{c}{d}" for c in ("AFC", "NFC") for d in ("EAST", "NORTH", "SOUTH", "WEST")),
}
BUCKETS = (">72h", "24-72h", "6-24h", "1-6h", "0-1h", "in-game")
REF_BUCKET = BUCKETS.index("1-6h")
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HOUR_GROUPS = {"00-08": range(0, 9), "09-16": range(9, 17), "17-23": range(17, 24)}
MAX_AGE = 660.0
PRE_STEP, IN_STEP = 60, 10
NC = 101                      # spread in whole cents 0..100
EDT = 4 * 3600                # every date here (Sep 9-15) is EDT; asserted in tests
SIZE_BINS = np.arange(0.0, 9.0001, 0.05)
# Population-aware: results/h3.jsonl for week 1, h3_wk2.jsonl for week 2.
REG_PATH = S.registry_path("h3")
CFB_REG_PATH = os.path.join(S.ROOT, "research", "sweep", "results", "h3_cfb.jsonl")


# =============================================================================
# pure pieces (tested)
# =============================================================================

def sample_grid(qts, bids, asks, kick=None, end_cap=None, max_age=MAX_AGE,
                pre_step=PRE_STEP, in_step=IN_STEP, live_window=LIVE_WINDOW):
    """-> (grid_ts, weight_s, spread, mid) for valid samples. Without a kickoff
    the whole span is sampled at pre_step."""
    qts = np.asarray(qts, dtype=float)
    bids = np.asarray(bids, dtype=float)
    asks = np.asarray(asks, dtype=float)
    if len(qts) == 0:
        return (np.array([]),) * 4
    start, end = qts[0], qts[-1] + max_age
    if end_cap is not None:
        end = min(end, end_cap)
    if kick is None:
        grid = np.arange(start, end, pre_step, dtype=float)
        w = np.full(len(grid), float(pre_step))
    else:
        end = min(end, kick + live_window)
        pre = np.arange(start, min(kick, end), pre_step, dtype=float)
        ing = np.arange(max(kick, start), end, in_step, dtype=float)
        grid = np.concatenate([pre, ing])
        w = np.concatenate([np.full(len(pre), float(pre_step)), np.full(len(ing), float(in_step))])
    if len(grid) == 0:
        return (np.array([]),) * 4
    idx = np.searchsorted(qts, grid, side="right") - 1
    ok = idx >= 0
    ok[ok] &= (grid[ok] - qts[idx[ok]]) <= max_age
    b, a = np.full(len(grid), np.nan), np.full(len(grid), np.nan)
    b[ok], a[ok] = bids[idx[ok]], asks[idx[ok]]
    ok &= ~np.isnan(b) & ~np.isnan(a) & (a >= b)
    return grid[ok], w[ok], (a - b)[ok], ((a + b) / 2)[ok]


def ttk_bucket(grid, kick):
    secs = kick - np.asarray(grid, dtype=float)
    b = np.full(len(secs), 5, dtype=np.int64)
    b[secs > 0] = 4
    b[secs > 3600] = 3
    b[secs > 6 * 3600] = 2
    b[secs > 24 * 3600] = 1
    b[secs > 72 * 3600] = 0
    return b


def hour_dow_et(grid):
    local = np.asarray(grid, dtype=float) - EDT
    hour = (np.floor(local / 3600) % 24).astype(np.int64)
    dow = ((np.floor(local / 86400).astype(np.int64) + 3) % 7)      # 1970-01-01 was a Thursday
    day = np.floor(local / 86400).astype(np.int64)
    return hour, dow, day


def weighted_median_from_hist(h):
    tot = h.sum()
    if tot <= 0:
        return None
    return int(np.searchsorted(np.cumsum(h), tot / 2.0))


def hist_boot_median(block_hist, n=S.BOOT, seed=S.SEED):
    """block_hist: (blocks, bins). Median of the pooled histogram and a
    percentile interval from resampling blocks."""
    live = block_hist[block_hist.sum(axis=1) > 0]
    k = len(live)
    if k == 0:
        return None
    est = weighted_median_from_hist(live.sum(axis=0))
    if k < 2:
        return None
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, k, size=(n, k))
    draws = []
    for row in pick:
        m = weighted_median_from_hist(live[row].sum(axis=0))
        if m is not None:
            draws.append(m)
    draws = np.sort(np.array(draws, dtype=float))
    return {"est": float(est), "lo": float(draws[int(0.025 * len(draws))]),
            "hi": float(draws[int(0.975 * len(draws)) - 1]), "se": float(draws.std()),
            "p": None, "n": int(live.sum()), "games": k}


def contrast_rows(block_hist, a_vals, b_vals):
    """Per-block weighted spread sums for two groups of a dimension."""
    cents = np.arange(block_hist.shape[-1], dtype=float)
    rows = []
    for blk in range(block_hist.shape[1]):
        ha = block_hist[list(a_vals), blk].sum(axis=0)
        hb = block_hist[list(b_vals), blk].sum(axis=0)
        if ha.sum() == 0 and hb.sum() == 0:
            continue
        rows.append({"game": blk, "sa": float(ha @ cents), "wa": float(ha.sum()),
                     "sb": float(hb @ cents), "wb": float(hb.sum())})
    return rows


def contrast_stat(rows):
    wa = sum(r["wa"] for r in rows)
    wb = sum(r["wb"] for r in rows)
    if wa == 0 or wb == 0:
        return None
    return sum(r["sa"] for r in rows) / wa - sum(r["sb"] for r in rows) / wb


def contrast_tests(name, acc):
    """The pre-registered contrasts, as (series, label, array, group A, group B).
    ONE definition, so the CFB replication records carry exactly the labels the
    week-1 candidates name."""
    tests = []
    if acc.ttk is not None:
        for i, lab in enumerate(BUCKETS):
            if i == REF_BUCKET:
                continue
            tests.append((name, f"ttk {lab} - 1-6h", acc.ttk, (i,), (REF_BUCKET,)))
    tests.append((name, "hour 00-08 - 09-16", acc.hour, tuple(HOUR_GROUPS["00-08"]), tuple(HOUR_GROUPS["09-16"])))
    tests.append((name, "hour 17-23 - 09-16", acc.hour, tuple(HOUR_GROUPS["17-23"]), tuple(HOUR_GROUPS["09-16"])))
    tests.append((name, "Sunday - Mon-Fri", acc.dow, (6,), (0, 1, 2, 3, 4)))
    return tests


# =============================================================================
# collection
# =============================================================================

class SeriesAcc:
    def __init__(self, n_blocks, dated):
        self.n_blocks = n_blocks
        self.ttk = np.zeros((len(BUCKETS), n_blocks, NC)) if dated else None
        self.hour = np.zeros((24, n_blocks, NC))
        self.dow = np.zeros((7, n_blocks, NC))
        self.size = defaultdict(lambda: np.zeros(len(SIZE_BINS)))
        self.vol_n = 0
        self.vol_s = 0.0
        self.vol_ss = 0.0
        self.markets = 0
        self.samples = 0

    def add(self, dim, values, block, cents, w):
        arr = getattr(self, dim)
        flat = (values * self.n_blocks + block) * NC + cents
        arr += np.bincount(flat, weights=w, minlength=arr.size).reshape(arr.shape)


def _vol(acc, qts, bids, asks, kick, end_cap):
    g, w, spread, mid = sample_grid(qts, bids, asks, kick=None, end_cap=(
        min(end_cap, kick + LIVE_WINDOW) if kick is not None and end_cap is not None
        else kick + LIVE_WINDOW if kick is not None else end_cap))
    if len(g) < 61:
        return
    # 60-minute changes on the 60s grid, only between samples exactly 60 grid
    # steps apart. Matched on INTEGER step index, never on float timestamps.
    k = np.rint((g - g[0]) / 60.0).astype(np.int64)
    j = np.searchsorted(k, k + 60)
    j = np.minimum(j, len(k) - 1)
    ok = k[j] == k + 60
    d = (mid[j[ok]] - mid[ok]) * 100
    acc.vol_n += len(d)
    acc.vol_s += float(d.sum())
    acc.vol_ss += float((d * d).sum())


def collect_dated(c, games):
    gidx = {gid: i for i, gid in enumerate(sorted(games))}
    accs, census = {}, Counter()
    for s in DATED:
        acc = accs[s] = SeriesAcc(len(gidx), dated=True)
        for mid, eid in week1_markets(c, s):
            gid = event_game(eid, games)
            if gid is None:
                census[f"{s}: market with no mapped game"] += 1
                continue
            kick = games[gid][0]
            q = c.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' "
                          "AND market_id=? AND source='live' AND best_bid IS NOT NULL "
                          "AND best_ask IS NOT NULL ORDER BY ts", (mid,)).fetchall()
            if not q:
                census[f"{s}: market with no two-sided quote"] += 1
                continue
            qts, bids, asks = zip(*q)
            g, w, spread, _mid = sample_grid(qts, bids, asks, kick=kick)
            if len(g) == 0:
                continue
            acc.markets += 1
            acc.samples += len(g)
            cents = np.clip(np.rint(spread * 100).astype(np.int64), 0, NC - 1)
            blk = np.full(len(g), gidx[gid], dtype=np.int64)
            hour, dow, _day = hour_dow_et(g)
            acc.add("ttk", ttk_bucket(g, kick), blk, cents, w)
            acc.add("hour", hour, blk, cents, w)
            acc.add("dow", dow, blk, cents, w)
            _vol(acc, qts, bids, asks, kick, None)
            books = defaultdict(dict)
            for ts, side, size in c.execute("SELECT ts, side, touch_size FROM market_depth "
                                            "WHERE venue='kalshi' AND market_id=?", (mid,)):
                books[ts][side] = size
            for ts, b in books.items():
                y, n = b.get("buy_yes"), b.get("buy_no")
                if not y or not n or y <= 0 or n <= 0 or ts > kick + LIVE_WINDOW:
                    continue
                bucket = int(ttk_bucket([ts], kick)[0])
                b_ix = int(np.log10(min(y, n)) / 0.05) if min(y, n) >= 1 else 0
                acc.size[bucket][min(len(SIZE_BINS) - 1, b_ix)] += 1
    return accs, census


def collect_futures(c):
    accs, census = {}, Counter()
    day0 = None
    lo, hi = S.undated_window()          # week 1: (-inf, cutoff); week 2: its own week
    for name, prefixes in FUTURES.items():
        rows_by_market = []
        for p in prefixes:
            for mid, in c.execute("SELECT DISTINCT market_id FROM markets WHERE venue='kalshi' "
                                  "AND market_id >= ? AND market_id < ?", (p + "-", p + ".")):
                q = c.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' "
                              "AND market_id=? AND source='live' AND ts >= ? AND ts < ? "
                              "AND best_bid IS NOT NULL AND best_ask IS NOT NULL ORDER BY ts",
                              (mid, max(lo, -1e18), hi)).fetchall()
                if q:
                    S.assert_search_set(mid, q[-1][0])
                    rows_by_market.append((mid, q))
        if not rows_by_market:
            census[f"{name}: no quotes before cutoff"] += 1
            continue
        days = sorted({int(d) for _m, q in rows_by_market
                       for d in hour_dow_et([q[0][0], q[-1][0]])[2]})
        day0 = min(days)
        n_blocks = int(hour_dow_et([hi])[2][0]) - day0 + 1
        acc = accs[name] = SeriesAcc(n_blocks, dated=False)
        for mid, q in rows_by_market:
            qts, bids, asks = zip(*q)
            g, w, spread, _mid = sample_grid(qts, bids, asks, end_cap=hi)
            if len(g) == 0:
                continue
            acc.markets += 1
            acc.samples += len(g)
            cents = np.clip(np.rint(spread * 100).astype(np.int64), 0, NC - 1)
            hour, dow, day = hour_dow_et(g)
            blk = day - day0
            acc.add("hour", hour, blk, cents, w)
            acc.add("dow", dow, blk, cents, w)
            _vol(acc, qts, bids, asks, None, hi)
    return accs, census


# =============================================================================
# report
# =============================================================================

def fmt(res):
    if not res:
        return "n/a"
    star = "*" if res["lo"] > 0 or res["hi"] < 0 else " "
    read = "" if res["games"] >= S.MIN_GAMES_TO_READ else " (too few blocks)"
    return (f"{res['est']:+6.2f}c [{res['lo']:+6.2f}, {res['hi']:+6.2f}]{star} "
            f"p={res['p']:.2g} blocks={res['games']}{read}")


def run():
    if os.path.exists(REG_PATH):
        os.remove(REG_PATH)
    S.open_population()                 # no-op for week 1; refuses week 2 until settled
    reg = S.Registry(REG_PATH)
    add = functools.partial(reg.add, population=S.POPULATION)
    c = S.live_ro()
    games = load_games(c)
    dated, census_d = collect_dated(c, games)
    futures, census_f = collect_futures(c)
    allacc = {**dated, **futures}

    print("=" * 100)
    print("H3 - SPREAD AND DEPTH LIFECYCLE, NFL WEEK 1 (search set only)")
    print("=" * 100)
    print("  time-weighted as-of spread on a fixed grid (60s pre-kickoff, 10s in-game, 60s futures);"
          "\n  dated series block on GAMES, futures on ET CALENDAR DAYS; futures read before 2026-09-15 02:00 ET.")
    for name, acc in allacc.items():
        print(f"  {name:<12} markets {acc.markets:>5}  grid samples {acc.samples:>10,}  blocks {acc.n_blocks}")
    for k, v in sorted({**census_d, **census_f}.items()):
        print(f"  census: {k:<50}{v:>6}")

    print("\n  MEDIAN SPREAD (cents), descriptive, [block bootstrap interval]")
    for name, acc in allacc.items():
        dims = (("ttk", BUCKETS), ("dow", DOW), ("hour", [f"{h:02d}" for h in range(24)]))
        for dim, labels in dims:
            arr = getattr(acc, dim)
            if arr is None:
                continue
            parts = []
            for i, lab in enumerate(labels):
                res = hist_boot_median(arr[i])
                add("H3 median spread", f"{name}|{dim}|{lab}", res, role="descriptive", unit="c",
                        note="time-weighted; blocks=" + ("games" if acc.ttk is not None else "calendar days"))
                parts.append(f"{lab} {res['est']:.0f} [{res['lo']:.0f},{res['hi']:.0f}]" if res else f"{lab} -")
            print(f"    {name:<12} {dim:<4} " + "  ".join(parts))

    print("\n  MEDIAN TOUCH SIZE (contracts, min of the two sides; depth capture instants, unweighted)")
    for name, acc in dated.items():
        parts = []
        for i, lab in enumerate(BUCKETS):
            h = acc.size.get(i)
            if h is None or h.sum() == 0:
                parts.append(f"{lab} -")
                continue
            m = weighted_median_from_hist(h)
            parts.append(f"{lab} {10 ** SIZE_BINS[m]:,.0f} (n={int(h.sum()):,})")
        print(f"    {name:<12} " + "  ".join(parts))

    print("\n  CONTRASTS - mean spread difference in cents (search tests; + = wider than the reference)")
    tests = []
    for name, acc in allacc.items():
        tests += contrast_tests(name, acc)
    for name, label, arr, a, b in tests:
        res = S.boot(contrast_rows(arr, a, b), contrast_stat)
        block = "games" if name in dated else "calendar days"
        note = f"blocks={block}" + ("; futures Sunday is ONE calendar day" if name in futures and "Sunday" in label else "")
        add("H3 contrast", f"{name}|{label}", res, role=S.ROLE, unit="c", note=note)
        print(f"    {name:<12} {label:<22} {fmt(res)}{'   [' + note + ']' if 'ONE' in note else ''}")

    print("\n  RANKING - median spread / sd of 60-minute mid changes (descriptive; higher = wider"
          "\n  relative to how much the price actually moves = the market-maker's screen)")
    ranking = []
    for name, acc in allacc.items():
        arr = acc.ttk if acc.ttk is not None else acc.hour
        med = weighted_median_from_hist(arr.sum(axis=(0, 1)))
        if acc.vol_n > 1 and med is not None:
            mean = acc.vol_s / acc.vol_n
            sd = (acc.vol_ss / acc.vol_n - mean * mean) ** 0.5
            ranking.append((med / sd if sd > 0 else float("inf"), name, med, sd, acc.vol_n))
    for ratio, name, med, sd, n in sorted(ranking, reverse=True):
        print(f"    {name:<12} median spread {med:>3}c  sd(60-min dmid) {sd:6.2f}pp  ratio {ratio:7.2f}  (n={n:,})")
    print(f"\n  registry: {REG_PATH}")


# =============================================================================
# CFB replication (brief 022 phase 2 - holdout B)
# =============================================================================
# Quotes from cfb_probe.db, written live by the probe and untouched by the raw
# shard corruption. Two probe processes wrote duplicate rows for part of the
# window; a time grid is indifferent to duplicates, which is one more reason the
# grid is the right estimator. Blocks are CFB games (CFBD-matched). The capture
# ran Thu-Sat ET, so "Sunday - Mon-Fri" is registered not estimable.

CFB_SERIES = ("KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFGAME", "KXNCAAFTEAMTOTAL")


def collect_cfb(c, kicks):
    from research.sweep.h2_imbalance import CFB_VENUE
    gidx = {g: i for i, g in enumerate(sorted(kicks))}
    accs, census = {}, Counter()
    for s in CFB_SERIES:
        acc = accs[s] = SeriesAcc(len(gidx), dated=True)
        for mid, in c.execute("SELECT DISTINCT market_id FROM markets WHERE venue=? "
                              "AND market_id >= ? AND market_id < ?", (CFB_VENUE, s + "-", s + ".")):
            gkey = mid.split("-")[1] if mid.count("-") >= 2 else None
            if gkey not in kicks:
                census[f"{s}: market with no matched CFBD game"] += 1
                continue
            kick = kicks[gkey]
            q = c.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue=? AND market_id=? "
                          "AND best_bid IS NOT NULL AND best_ask IS NOT NULL ORDER BY ts",
                          (CFB_VENUE, mid)).fetchall()
            if not q:
                census[f"{s}: market with no two-sided quote"] += 1
                continue
            qts, bids, asks = zip(*q)
            g, w, spread, _mid = sample_grid(qts, bids, asks, kick=kick)
            if len(g) == 0:
                continue
            acc.markets += 1
            acc.samples += len(g)
            cents = np.clip(np.rint(spread * 100).astype(np.int64), 0, NC - 1)
            blk = np.full(len(g), gidx[gkey], dtype=np.int64)
            hour, dow, _day = hour_dow_et(g)
            acc.add("ttk", ttk_bucket(g, kick), blk, cents, w)
            acc.add("hour", hour, blk, cents, w)
            acc.add("dow", dow, blk, cents, w)
            _vol(acc, qts, bids, asks, kick, None)
    return accs, census


def run_cfb():
    from research.sweep.h2_imbalance import cfb_games, nfl_analogue, _nfl_signs
    c = S.cfb_ro()                       # refuses until the candidates doc is committed
    kicks, unmatched = cfb_games(c)
    accs, census = collect_cfb(c, kicks)
    nfl = _nfl_signs(os.path.join(S.ROOT, "research", "sweep", "results", "h3.jsonl"))
    if os.path.exists(CFB_REG_PATH):
        os.remove(CFB_REG_PATH)
    reg = S.Registry(CFB_REG_PATH)

    print("=" * 100)
    print("H3 - SPREAD LIFECYCLE, CFB (holdout B: replication + population)")
    print("=" * 100)
    print(f"  CFBD-matched game keys {len(kicks)}; unmatched moneylines {len(unmatched)}")
    for name, acc in accs.items():
        live = int((acc.hour.sum(axis=(0, 2)) > 0).sum())
        print(f"  {name:<18} markets {acc.markets:>5}  grid samples {acc.samples:>10,}  games with samples {live}")
    for k, v in sorted(census.items()):
        print(f"  census: {k:<56}{v:>6}")

    print("\n  MEDIAN SPREAD (cents), descriptive")
    for name, acc in accs.items():
        for dim, labels in (("ttk", BUCKETS), ("dow", DOW), ("hour", [f"{h:02d}" for h in range(24)])):
            arr = getattr(acc, dim)
            parts = []
            for i, lab in enumerate(labels):
                res = hist_boot_median(arr[i])
                reg.add("H3 median spread cfb", f"{name}|{dim}|{lab}", res, role="descriptive",
                        unit="c", population="cfb", note="time-weighted; blocks=CFB games")
                if res:
                    parts.append(f"{lab} {res['est']:.0f} [{res['lo']:.0f},{res['hi']:.0f}]")
            print(f"    {name:<18} {dim:<4} " + ("  ".join(parts) or "-"))

    print("\n  CONTRASTS - mean spread difference in cents (+ = wider than the reference)"
          "\n  NFL column = sign of the week-1 search estimate on the analogous series, * if it excluded zero.")
    for name, acc in accs.items():
        an = nfl_analogue(name)
        for _n, label, arr, a, b in contrast_tests(name, acc):
            if label == "Sunday - Mon-Fri":
                res, note = None, "not estimable: the CFB capture ran Thu-Sat ET"
            else:
                res, note = S.boot(contrast_rows(arr, a, b), contrast_stat), "blocks=CFB games"
            reg.add("H3 contrast", f"{name}|{label}", res, role="replication", unit="c",
                    population="cfb", note=note)
            reg.add("H3 contrast cfb population", f"{name}|{label}", res, role="search", unit="c",
                    population="cfb", note=note)
            nsign = nfl.get(("H3 contrast", f"{an}|{label}"), "-") if an else "n/a"
            print(f"    {name:<18} {label:<22} {fmt(res) if res else 'NOT ESTIMABLE (' + note + ')':<62} NFL {nsign}")

    print("\n  RANKING - median spread / sd of 60-minute mid changes (descriptive)")
    ranking = []
    for name, acc in accs.items():
        med = weighted_median_from_hist(acc.ttk.sum(axis=(0, 1)))
        if acc.vol_n > 1 and med is not None:
            mean = acc.vol_s / acc.vol_n
            sd = (acc.vol_ss / acc.vol_n - mean * mean) ** 0.5
            ranking.append((med / sd if sd > 0 else float("inf"), name, med, sd, acc.vol_n))
    for ratio, name, med, sd, n in sorted(ranking, reverse=True):
        print(f"    {name:<18} median spread {med:>3}c  sd(60-min dmid) {sd:6.2f}pp  ratio {ratio:7.2f}  (n={n:,})")
    print(f"\n  registry: {CFB_REG_PATH}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cfb", action="store_true", help="holdout B: replicate on college football")
    a = ap.parse_args()
    run_cfb() if a.cfb else run()


if __name__ == "__main__":
    main()
