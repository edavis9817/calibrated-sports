"""F11: how many "hot streaks" does pure chance produce, and does a streak mean anything?

A streak board ("hit in 6 of last 7", "5 of 5 away", "1 of 1 vs DAL") is measured
here against lines we construct ourselves, because we hold no historical prop lines:

    line(player, stat, game t) = median of that player's previous LOOKBACK (17)
                                 regular-season values of the stat, needing
                                 MIN_PRIOR (4); the lookback crosses seasons.
    graded        x_t != line        (x_t > line is a hit, x_t < line a miss)
    push          x_t == line        (void, as a book voids a push; not graded)
    eligible      line >= FLOOR[stat] (a prop somebody would actually hang)

Three boards, each evaluated for a pair's NEXT appearance using only graded games
before it:

    L7     >= 6 hits in the last 7 graded games              (displays 6/7 or 7/7)
    L5     5 hits in the last 5 graded games                 (displays 100%)
    AWAY5  5 hits in the last 5 graded AWAY games, next game away (100%)
    H2H1   exactly one prior graded game vs the next opponent, and it hit (1 of 1)

and three worlds, run through the IDENTICAL construction:

    real    the components table as it is
    coin    real schedule, real push pattern, every graded outcome replaced by a
            fair coin - the textbook null the brief describes
    iid     each player-season's values redrawn WITH replacement from that same
            player-season's values, then lines rebuilt - no hot hand, no within-
            season trend, same discreteness and same ties as real. This is the
            null the real data is compared against.

Reads the staged a-11 components files (read-only). Writes one JSON of results
and prints every figure docs/F11-streak-null.md quotes. Nothing is exported.

    python -m research.f11_streak_null \
        --components D:/calibrated-sports/staging/a-11/nfl/components \
        --analytics D:/calibrated-sports/data/analytics.db \
        --out ../_relay/reports/f-11-streak-null.json
"""
import argparse
import glob
import json
import os
import sqlite3
import sys

import numpy as np

LOOKBACK = 17
MIN_PRIOR = 4
# Eligibility: the trailing median must reach the floor. rec/targets/rush_att/
# rec_yds are the screened-population floors in CLAUDE.md; rush_yds mirrors
# rec_yds; the QB floors are this unit's choice.
FLOOR = {"rec": 2.0, "targets": 3.0, "rec_yds": 25.0, "rush_att": 6.0,
         "rush_yds": 25.0, "pass_att": 20.0, "pass_cmp": 12.0, "pass_yds": 150.0}
STATS = tuple(FLOOR)
BOARDS = ("L7", "L5", "AWAY5", "H2H1")
DISPLAY = {"L5": 1.0, "AWAY5": 1.0, "H2H1": 1.0}  # L7 is per-row: 6/7 or 7/7
FIRST_SEASON, LAST_SEASON = 1999, 2025   # 2026 is two weeks old and is excluded
PRIMARY = 2025
SLOTS = 20


# ---------------------------------------------------------------------------- load
def load(components_dir, analytics_db):
    """-> dict of flat arrays, one entry per (player, stat) appearance, sorted by
    (pair, season, week). Also returns join census."""
    ctx = sqlite3.connect(f"file:{analytics_db}?mode=ro", uri=True)
    sched = {}
    for season, week, gid, team, opp in ctx.execute(
            "SELECT DISTINCT season, week, game_id, team, opponent FROM f_team_game_pace "
            "WHERE season_type = 'REG'"):
        sched[(season, week, team)] = (opp, gid.split("_")[3] != team)  # away if not home
    ctx.close()

    rows = []   # (gsis, season, week, team, {stat: value})
    census = {"rows": 0, "sched_unmatched": 0}
    files = sorted(glob.glob(os.path.join(components_dir, "*.json")))
    if len(files) != 28:
        raise SystemExit(f"expected 28 season files, found {len(files)}")
    for f in files:
        d = json.load(open(f))
        season = d["season"]
        if not FIRST_SEASON <= season <= LAST_SEASON:
            continue
        col = {c: i for i, c in enumerate(d["columns"])}
        for r in d["rows"]:
            if r["season_type"] != "REG":
                continue
            census["rows"] += 1
            key = (season, r["index"], r["team"])
            if key not in sched:
                census["sched_unmatched"] += 1
            rows.append((d["players"][r["player"]]["id"], season, r["index"], r["team"],
                         {s: r["values"][col[s]] for s in STATS}))
    if census["rows"] < 150_000:
        raise SystemExit(f"only {census['rows']} REG rows read - the shape is wrong")

    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    out = {k: [] for k in ("pair", "player", "stat", "season", "week", "x", "opp", "away")}
    pair_ids, player_ids = {}, {}
    for si, s in enumerate(STATS):
        for g, season, week, team, v in rows:
            x = v[s]
            if x is None or (isinstance(x, float) and x != x):
                continue   # not collected (targets 2003-2008): absent, never zero
            opp, away = sched.get((season, week, team), (None, None))
            out["pair"].append(pair_ids.setdefault((g, s), len(pair_ids)))
            out["player"].append(player_ids.setdefault(g, len(player_ids)))
            out["stat"].append(si)
            out["season"].append(season)
            out["week"].append(week)
            out["x"].append(float(x))
            out["opp"].append(opp or "")
            out["away"].append(-1 if away is None else int(away))
    a = {k: np.array(v) for k, v in out.items()}
    order = np.lexsort((a["week"], a["season"], a["pair"]))
    a = {k: v[order] for k, v in a.items()}
    census["appearances"] = int(len(a["x"]))
    census["pairs"] = len(pair_ids)
    census["players"] = len(player_ids)
    return a, census


# ------------------------------------------------------------------ construction
def lines(pair, x):
    """Trailing median of the previous <= LOOKBACK values in the same pair, NaN
    where fewer than MIN_PRIOR exist. Vectorised over lags."""
    n = len(x)
    lagged = np.full((n, LOOKBACK), np.nan)
    for k in range(1, LOOKBACK + 1):
        same = np.zeros(n, bool)
        same[k:] = pair[k:] == pair[:-k]
        lagged[k:, k - 1] = np.where(same[k:], x[:-k], np.nan)
    have = np.sum(~np.isnan(lagged), axis=1)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(lagged, axis=1)
    med[have < MIN_PRIOR] = np.nan
    return med


def grade(a, x):
    """-> (eligible, graded, hit) boolean arrays for values x under the construction."""
    line = lines(a["pair"], x)
    floor = np.array([FLOOR[s] for s in STATS])[a["stat"]]
    eligible = ~np.isnan(line) & (line >= floor)
    graded = eligible & (x != line)
    hit = graded & (x > line)
    return eligible, graded, hit


def trailing(group, mask, hit, k):
    """For each row: (#graded-in-mask before this row within `group`, #hits among
    the last k of them). `group` must be sorted with rows in time order inside it."""
    g = mask.astype(np.int64)
    h = (mask & hit).astype(np.int64)
    cg = np.concatenate([[0], np.cumsum(g)])
    ch = np.concatenate([[0], np.cumsum(h)])
    n = len(group)
    start = np.zeros(n, np.int64)
    new = np.ones(n, bool)
    new[1:] = group[1:] != group[:-1]
    idx = np.where(new)[0]
    start = idx[np.searchsorted(idx, np.arange(n), side="right") - 1]
    before = cg[np.arange(n)] - cg[start]          # graded rows strictly before
    # position (in the cumulative graded sequence) of the k-th most recent graded row
    pos_now = cg[np.arange(n)]
    target = pos_now - k
    # row index where cumulative graded count first reaches target+1 -> use ch at the
    # boundary: hits among the last k = ch at now - ch at the row where cg == target
    ok = before >= k
    # index into rows: first row r with cg[r] == target (cg is the prefix before row r)
    boundary = np.searchsorted(cg, np.maximum(target, 0), side="left")
    lastk = np.where(ok, ch[np.arange(n)] - ch[np.minimum(boundary, n)], 0)
    return before, lastk


def boards(a, graded, hit):
    """-> {board: qualifies bool array}, {board: displayed rate array}. A row
    qualifies when the pair is on the slate there (eligible is checked by the
    caller) and its history before the row meets the board's rule."""
    pair = a["pair"]
    q, disp = {}, {}
    n7, h7 = trailing(pair, graded, hit, 7)
    q["L7"] = (n7 >= 7) & (h7 >= 6)
    disp["L7"] = h7 / 7.0
    n5, h5 = trailing(pair, graded, hit, 5)
    q["L5"] = (n5 >= 5) & (h5 == 5)
    disp["L5"] = np.ones(len(pair))
    away = a["away"] == 1
    na, ha = trailing(pair, graded & away, hit, 5)
    q["AWAY5"] = away & (na >= 5) & (ha == 5)
    disp["AWAY5"] = np.ones(len(pair))
    # H2H: group by (pair, opponent), time order kept inside the group
    has_opp = a["opp"] != ""
    order = np.lexsort((a["week"], a["season"], a["opp"], pair))
    grp = pair[order].astype(np.int64) * 64 + np.unique(a["opp"], return_inverse=True)[1][order]
    nb, hb = trailing(grp, graded[order], hit[order], 1)
    n_before = np.zeros(len(pair), np.int64)
    n_before[order] = nb
    # all prior meetings, not the last one: exactly one prior graded meeting
    tot = np.zeros(len(pair), np.int64)
    gg = graded[order].astype(np.int64)
    cg = np.concatenate([[0], np.cumsum(gg)])
    new = np.ones(len(grp), bool)
    new[1:] = grp[1:] != grp[:-1]
    idx = np.where(new)[0]
    start = idx[np.searchsorted(idx, np.arange(len(grp)), side="right") - 1]
    tot[order] = cg[np.arange(len(grp))] - cg[start]
    last_hit = np.zeros(len(pair), np.int64)
    last_hit[order] = hb
    q["H2H1"] = has_opp & (tot == 1) & (last_hit == 1)
    disp["H2H1"] = np.ones(len(pair))
    return q, disp


# ---------------------------------------------------------------------- worlds
def world_real(a, rng):
    e, g, h = grade(a, a["x"])
    return e, g, h


def world_coin(a, rng, base=None):
    e, g, h = base if base is not None else grade(a, a["x"])
    return e, g, g & (rng.random(len(g)) < 0.5)


def world_iid(a, rng):
    """Redraw each player-season's values with replacement from that player-season."""
    key = a["pair"].astype(np.int64) * 10_000 + a["season"]
    # rows are sorted by pair then season, so each key is one contiguous block
    new = np.ones(len(key), bool)
    new[1:] = key[1:] != key[:-1]
    starts = np.where(new)[0]
    ends = np.append(starts[1:], len(key))
    size = (ends - starts)[np.cumsum(new) - 1]
    first = starts[np.cumsum(new) - 1]
    pick = first + (rng.random(len(key)) * size).astype(np.int64)
    return grade(a, a["x"][pick])


# --------------------------------------------------------------------- measures
def measure(a, e, g, h, seasons):
    """Counts and next-appearance outcomes for one world, restricted to seasons."""
    q, disp = boards(a, g, h)
    in_s = np.isin(a["season"], seasons)
    slate = e & in_s
    out = {"slate_pair_games": int(slate.sum()),
           "pairs_scanned": int(len(np.unique(a["pair"][slate]))),
           "graded": int((g & in_s).sum()),
           "base_hit_rate": float(h[g & in_s].sum() / max(1, (g & in_s).sum())),
           "push_rate": float(((e & ~g) & in_s).sum() / max(1, slate.sum()))}
    for b in BOARDS:
        qq = q[b] & slate
        nxt = qq & g
        out[b] = {"events": int(qq.sum()),
                  "pairs_reaching": int(len(np.unique(a["pair"][qq]))),
                  "next_graded": int(nxt.sum()),
                  "next_hits": int((nxt & h).sum()),
                  "next_rate": float((nxt & h).sum() / max(1, nxt.sum())),
                  "displayed_mean": float(disp[b][nxt].mean()) if nxt.any() else None}
    anyq = np.zeros(len(e), bool)
    for b in BOARDS:
        anyq |= q[b]
    return out, q, anyq


def weekly(a, e, q, anyq, season):
    """Per week of `season`: candidates on the slate and qualifiers per board."""
    rows = []
    s = a["season"] == season
    for w in sorted(set(a["week"][s].tolist())):
        m = s & (a["week"] == w) & e
        rows.append({"week": w, "candidates": int(m.sum()),
                     **{b: int((q[b] & m).sum()) for b in BOARDS},
                     "any": int((anyq & m).sum())})
    return rows


def boot_rate(player, num, den, rng, draws=1000):
    """Player-clustered bootstrap of sum(num)/sum(den)."""
    ids, inv = np.unique(player, return_inverse=True)
    N = np.bincount(inv, weights=num, minlength=len(ids))
    D = np.bincount(inv, weights=den, minlength=len(ids))
    est = N.sum() / D.sum()
    k = len(ids)
    samp = rng.integers(0, k, size=(draws, k))
    r = N[samp].sum(1) / np.maximum(D[samp].sum(1), 1)
    return est, float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5)), int(k)


def boot_contrast(player, n1, d1, n2, d2, rng, draws=1000):
    """Player-clustered bootstrap of rate1 - rate2 over the SAME resampled players."""
    ids, inv = np.unique(player, return_inverse=True)
    f = lambda w: np.bincount(inv, weights=w, minlength=len(ids))
    N1, D1, N2, D2 = f(n1), f(d1), f(n2), f(d2)
    est = N1.sum() / D1.sum() - N2.sum() / D2.sum()
    samp = rng.integers(0, len(ids), size=(draws, len(ids)))
    r = (N1[samp].sum(1) / np.maximum(D1[samp].sum(1), 1)
         - N2[samp].sum(1) / np.maximum(D2[samp].sum(1), 1))
    return est, float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def slots_needed(p_weeks, slots=SLOTS, conf=0.95):
    """Smallest N with P(Binomial(N, p) >= slots) >= conf in EVERY week given."""
    from math import comb, log, exp
    p = min(p for p in p_weeks if p > 0)

    def tail(N):   # P(X >= slots)
        s = 0.0
        for k in range(slots):
            s += exp(log(comb(N, k)) + k * log(p) + (N - k) * log(1 - p))
        return 1 - s
    N = slots
    while tail(N) < conf:
        N += max(1, N // 50)
    while N > slots and tail(N - 1) >= conf:
        N -= 1
    return N, p


# ------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--components", required=True)
    ap.add_argument("--analytics", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260923)
    args = ap.parse_args(argv)
    rng = np.random.default_rng(args.seed)

    a, census = load(args.components, args.analytics)
    print("census", census)
    all_seasons = list(range(2000, LAST_SEASON + 1))   # 1999 is lookback only
    scopes = {"2025": [PRIMARY], "2000-2025": all_seasons}
    res = {"config": {"lookback": LOOKBACK, "min_prior": MIN_PRIOR, "floor": FLOOR,
                      "reps": args.reps, "seed": args.seed}, "census": census}

    base = world_real(a, rng)
    e, g, h = base
    for name, ss in scopes.items():
        m, q, anyq = measure(a, e, g, h, ss)
        res.setdefault("real", {})[name] = m
    _, q_real, any_real = measure(a, e, g, h, all_seasons)

    # coin: one draw for the headline, reps for the spread
    coin_reps = {n: [] for n in scopes}
    iid_reps = {n: [] for n in scopes}
    keep = {}
    for r in range(args.reps):
        ce, cg, ch = world_coin(a, rng, base)
        ie, ig, ih = world_iid(a, rng)
        for name, ss in scopes.items():
            coin_reps[name].append(measure(a, ce, cg, ch, ss)[0])
            iid_reps[name].append(measure(a, ie, ig, ih, ss)[0])
        if r == 0:
            keep = {"coin": (ce, cg, ch), "iid": (ie, ig, ih)}
    res["coin_reps"] = coin_reps
    res["iid_reps"] = iid_reps

    # ---------------------------------------------------------- intervals
    player = a["player"]
    res["intervals"] = {}
    in_all = np.isin(a["season"], all_seasons)
    in_25 = a["season"] == PRIMARY
    for world, (we, wg, wh) in {"real": base, **keep}.items():
        qw, dw = boards(a, wg, wh)
        for sname, ins in (("2025", in_25), ("2000-2025", in_all)):
            for b in BOARDS:
                nxt = qw[b] & we & wg & ins
                if nxt.sum() == 0:
                    continue
                est, lo, hi, k = boot_rate(player[nxt], wh[nxt].astype(float),
                                           np.ones(nxt.sum()), rng)
                res["intervals"][f"{world}|{sname}|{b}"] = {
                    "next_rate": est, "lo": lo, "hi": hi, "n": int(nxt.sum()), "players": k,
                    "displayed": float(dw[b][nxt].mean())}
            # base rate, same clustering
            gm = wg & ins
            est, lo, hi, k = boot_rate(player[gm], wh[gm].astype(float), np.ones(gm.sum()), rng)
            res["intervals"][f"{world}|{sname}|base"] = {"next_rate": est, "lo": lo, "hi": hi,
                                                        "n": int(gm.sum()), "players": k}

    # real minus iid contrast on the same resampled players (next rate on qualifiers,
    # and next rate minus base rate - the survivorship lift - within each world)
    ie, ig, ih = keep["iid"]
    qi, _ = boards(a, ig, ih)
    res["contrasts"] = {}
    for b in BOARDS:
        for sname, ins in (("2025", in_25), ("2000-2025", in_all)):
            nr = q_real[b] & e & g & ins
            ni = qi[b] & ie & ig & ins
            est, lo, hi = boot_contrast(player, (nr & h).astype(float), nr.astype(float),
                                        (ni & ih).astype(float), ni.astype(float), rng)
            res["contrasts"][f"real-iid|{sname}|{b}"] = {"est": est, "lo": lo, "hi": hi}
            # lift over own base, real world
            gm = g & ins
            est, lo, hi = boot_contrast(player, (nr & h).astype(float), nr.astype(float),
                                        (gm & h).astype(float), gm.astype(float), rng)
            res["contrasts"][f"real lift over base|{sname}|{b}"] = {"est": est, "lo": lo, "hi": hi}

    # ------------------------------------------------------------- weekly board fill
    res["weekly"] = {}
    for world, (we, wg, wh) in {"real": base, **keep}.items():
        qw, _ = boards(a, wg, wh)
        anyw = np.zeros(len(we), bool)
        for b in BOARDS:
            anyw |= qw[b]
        wk = weekly(a, we, qw, anyw, PRIMARY)
        res["weekly"][world] = wk
    res["slots"] = {}
    for world in ("real", "coin", "iid"):
        wk = res["weekly"][world]
        for b in BOARDS + ("any",):
            ps = [r[b] / r["candidates"] for r in wk if r["candidates"]]
            N, pmin = slots_needed(ps)
            mean_p = sum(r[b] for r in wk) / sum(r["candidates"] for r in wk)
            res["slots"][f"{world}|{b}"] = {"p_mean": mean_p, "p_min_week": pmin,
                                           "n_expected": SLOTS / mean_p if mean_p else None,
                                           "n_every_week_95": N,
                                           "candidates_min": min(r["candidates"] for r in wk),
                                           "candidates_median": float(np.median(
                                               [r["candidates"] for r in wk])),
                                           "qualifiers_min": min(r[b] for r in wk)}

    # per stat, real, pooled: base rate and L7 next rate
    res["per_stat"] = {}
    for si, s in enumerate(STATS):
        m = a["stat"] == si
        gm = g & in_all & m
        nr = q_real["L7"] & e & g & in_all & m
        res["per_stat"][s] = {"base": float(h[gm].mean()) if gm.any() else None,
                              "graded": int(gm.sum()),
                              "L7_next": float(h[nr].mean()) if nr.any() else None,
                              "L7_n": int(nr.sum())}

    # why the construction's base rate is not 50%: the same grading with the floor
    # removed, and restricted to lines well clear of it
    line = lines(a["pair"], a["x"])
    have = ~np.isnan(line) & in_all
    floor = np.array([FLOOR[s] for s in STATS])[a["stat"]]
    diag = {}
    for name, m in (("no_floor", have), ("floor", have & (line >= floor)),
                    ("line >= 2x floor", have & (line >= 2 * floor)),
                    ("floor, same-season lookback full (week >= 6)",
                     have & (line >= floor) & (a["week"] >= 6))):
        gm = m & (a["x"] != line)
        diag[name] = {"graded": int(gm.sum()),
                      "hit_rate": float((a["x"][gm] > line[gm]).mean()),
                      "push_rate": float(1 - gm.sum() / max(1, m.sum()))}
    res["construction"] = diag

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(res, open(args.out, "w"), indent=1)
    report(res)


def pct(v):
    return f"{100 * v:.1f}%"


def report(res):
    for name in ("2025", "2000-2025"):
        r = res["real"][name]
        print(f"\n== {name}: slate pair-games {r['slate_pair_games']:,}, pairs scanned "
              f"{r['pairs_scanned']:,}, graded {r['graded']:,}, push {pct(r['push_rate'])}")
        for b in BOARDS:
            cr = [x[b]["pairs_reaching"] for x in res["coin_reps"][name]]
            ir = [x[b]["pairs_reaching"] for x in res["iid_reps"][name]]
            ce = [x[b]["events"] for x in res["coin_reps"][name]]
            ie = [x[b]["events"] for x in res["iid_reps"][name]]
            real_p = r[b]["pairs_reaching"]
            print(f"  {b:6s} pairs reaching: real {real_p:6,} ({pct(real_p / r['pairs_scanned'])})"
                  f" | coin {np.mean(cr):8.1f} [{np.percentile(cr, 2.5):.0f}, {np.percentile(cr, 97.5):.0f}]"
                  f" | iid {np.mean(ir):8.1f} [{np.percentile(ir, 2.5):.0f}, {np.percentile(ir, 97.5):.0f}]"
                  f"   events real {r[b]['events']:,} coin {np.mean(ce):.0f} iid {np.mean(ie):.0f}"
                  f" [{np.percentile(ie, 2.5):.0f}, {np.percentile(ie, 97.5):.0f}]")
    print("\n== next-appearance hit rate, spread ACROSS null reps (2.5-97.5 pct), vs real")
    for name in ("2025", "2000-2025"):
        for w in ("coin_reps", "iid_reps"):
            base = [x["base_hit_rate"] for x in res[w][name]]
            print(f"  {w:9s} {name:9s} base {np.mean(base):.4f} "
                  f"[{np.percentile(base, 2.5):.4f}, {np.percentile(base, 97.5):.4f}]"
                  f"  real {res['real'][name]['base_hit_rate']:.4f}")
            for b in BOARDS:
                v = [x[b]["next_rate"] for x in res[w][name]]
                real = res["real"][name][b]["next_rate"]
                share = float(np.mean(np.array(v) >= real))
                print(f"    {b:6s} null {np.mean(v):.4f} [{np.percentile(v, 2.5):.4f}, "
                      f"{np.percentile(v, 97.5):.4f}]  real {real:.4f}  share(null>=real) {share:.2f}")
    print("\n== construction base rate (real, 2000-2025)")
    for k, v in res["construction"].items():
        print(f"  {k:46s} hit {v['hit_rate']:.4f} push {v['push_rate']:.3f} n={v['graded']:,}")
    print("\n== next-appearance hit rate among qualifiers (player-clustered 95%)")
    for k, v in res["intervals"].items():
        print(f"  {k:28s} {v['next_rate']:.4f} [{v['lo']:.4f}, {v['hi']:.4f}] n={v['n']:,}"
              + (f" displayed {v['displayed']:.3f}" if "displayed" in v else ""))
    print("\n== contrasts")
    for k, v in res["contrasts"].items():
        print(f"  {k:36s} {v['est']:+.4f} [{v['lo']:+.4f}, {v['hi']:+.4f}]")
    print("\n== 20-slot board, 2025 weekly")
    for k, v in res["slots"].items():
        print(f"  {k:12s} p_mean {v['p_mean']:.4f} p_min {v['p_min_week']:.4f} "
              f"N_expected {v['n_expected']:.0f} N_every_week_95 {v['n_every_week_95']} "
              f"cand min/med {v['candidates_min']}/{v['candidates_median']:.0f} "
              f"qual_min {v['qualifiers_min']}")
    print("\n== per stat (real, 2000-2025)")
    for s, v in res["per_stat"].items():
        print(f"  {s:9s} base {v['base']:.4f} (n={v['graded']:,})  L7 next "
              f"{(v['L7_next'] or 0):.4f} (n={v['L7_n']:,})")


if __name__ == "__main__":
    sys.exit(main())
