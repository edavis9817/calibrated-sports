"""c-35 - the edges inside one player's own Kalshi ladder.

    LOGGER_DB=<market_log.db> python -m research.ladder_edges --cache D:/temp/c35/rows.json --extract
    python -m research.ladder_edges --cache D:/temp/c35/rows.json --json-out D:/temp/c35/result.json

PRE-REGISTRATION: docs/C35-ladder-edges-preregistration.md, committed at
f1db87c BEFORE this script existed. This file implements it; it does not
extend it. The comments here say only where the code carries a rule out.

`market_log.db` is opened `mode=ro` only (here, and by research.walkforward's
workers). `--extract` is the only step that reads it; it writes a scratch JSON
cache that is never committed. Everything PRINTED is an aggregate or an
interval. Nothing is priced at a mid in Q3: the mid states the view and nothing
else.
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import norm, rankdata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.fees import fee_per_contract, series_multiplier, series_of  # noqa: E402

SEASON = 2026
WEEKS = (2, 3, 4)
REF_SEASONS = (2023, 2024, 2025)
KALSHI_SERIES = {"KXNFLREC": "receptions", "KXNFLRSHATT": "rush_attempts"}
ENTRY_LEAD = 180 * 60
STALE = 600.0
DEPTH_LOOKBACK = 600.0
BOOT, SEED = 2000, 35
MIN_GAMES = 5
MIN_RUNGS = 3
CLIP = 0.01
CENTRAL = (0.25, 0.75)
REF_BAND = (0.40, 0.60)
REF_MIN_N = 30
OFFSETS = (-3, -2, -1, 0, 1, 2, 3)
SIZES = (100, 500)
VIEWS = (0.10, 0.25, 0.50)
PRIMARY_VIEW = 0.25
RULES = (("central", 0.50), ("near 0.30", 0.30), ("near 0.70", 0.70),
         ("near 0.15", 0.15), ("near 0.85", 0.85))
GROUPS = ("receptions-all", "receptions-WR", "receptions-TE", "receptions-RB",
          "rush_attempts-all")
BH_Q = 0.10
C24_P2 = (2513, 0.0121)
EXPECT_FEE_TYPE = "quadratic"       # REC and RSHATT: taker M 1, maker M 0


def ro(path=None):
    return sqlite3.connect(f"file:{path or config.DB_PATH}?mode=ro", uri=True)


# =============================================================================
# pure pieces
# =============================================================================

def pav_decreasing(s):
    """Non-increasing isotonic fit of a survival curve, equal weights."""
    vals, wts = [], []
    for v in s:
        vals.append(float(v))
        wts.append(1.0)
        while len(vals) > 1 and vals[-2] < vals[-1]:
            w = wts[-2] + wts[-1]
            v2 = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / w
            vals[-2:] = [v2]
            wts[-2:] = [w]
    out = []
    for v, w in zip(vals, wts):
        out.extend([v] * int(w))
    return out


def ladder_cells(lines, surv):
    """Rungs as boundaries. -> masses q_0..q_n: below the lowest rung, between
    consecutive rungs, above the highest. Sums to 1."""
    q = [1.0 - surv[0]]
    q += [surv[i] - surv[i + 1] for i in range(len(surv) - 1)]
    q.append(surv[-1])
    return q


def cell_index(lines, x):
    """Which of ladder_cells' cells the realised count fell in."""
    return sum(1 for L in lines if x > L)


def mid_pit(q, j):
    return sum(q[:j]) + q[j] / 2.0


def pit_var(q):
    """Var of the mid-PIT under the ladder itself: (1 - sum q^3) / 12."""
    return (1.0 - sum(v ** 3 for v in q)) / 12.0


def unit_cells(lines, surv):
    """{x: implied P(X = x)} wherever both bounding rungs are quoted."""
    s = dict(zip(lines, surv))
    s[-0.5] = 1.0
    out = {}
    for L in list(s):
        if (L + 1.0) in s:
            out[int(round(L + 0.5))] = s[L] - s[L + 1.0]
    return out


def probit(p):
    return float(norm.ppf(min(max(p, CLIP), 1 - CLIP)))


def fit_shift(k, m):
    """The single probit shift of the market ladder that best reproduces the
    model's, least squares in probability space. -> (c, ss_resid)."""
    pk = norm.ppf(np.clip(k, CLIP, 1 - CLIP))
    m = np.clip(m, CLIP, 1 - CLIP)

    def f(c):
        return float(((norm.cdf(pk + c) - m) ** 2).sum())
    r = minimize_scalar(f, bounds=(-6, 6), method="bounded", options={"xatol": 1e-6})
    return float(r.x), float(r.fun)


def fit_shift_scale(k, m):
    pk = norm.ppf(np.clip(k, CLIP, 1 - CLIP))
    m = np.clip(m, CLIP, 1 - CLIP)

    def f(ab):
        return float(((norm.cdf(ab[0] + ab[1] * pk) - m) ** 2).sum())
    c0, s0 = fit_shift(k, m)
    r = minimize(f, [c0, 1.0], method="Nelder-Mead",
                 options={"xatol": 1e-6, "fatol": 1e-12, "maxiter": 2000})
    return float(r.x[0]), float(r.x[1]), float(min(r.fun, s0))


def taker_cost(price, contracts, market_id):
    """-> (fee per contract, all-in price) at the executable price."""
    mt = series_multiplier(market_id)[1]
    fee = fee_per_contract(price, contracts, "taker", mt)
    return fee, price + fee


def rung_exec(k, price, contracts, market_id, side):
    """One rung, one side, one size, at the executable VWAP `price` of that
    side. -> dict(cost, be) or None when the all-in price is not in (0, 1).
    `be` is the break-even |view| in probit units; `cost` is all-in minus the
    side's own mid."""
    if price is None or not (0 < price < 1):
        return None
    fee, allin = taker_cost(price, contracts, market_id)
    if not (0 < allin < 1):
        return None
    side_mid = k if side == "yes" else 1 - k
    be = float(norm.ppf(allin) - norm.ppf(min(max(side_mid, 1e-6), 1 - 1e-6)))
    return {"price": price, "fee": fee, "allin": allin, "cost": allin - side_mid, "be": be}


def view_ev(k, allin, delta, side):
    """Net EV per contract of holding the view `delta` (probit, signed by the
    side: yes = up) through this rung at the all-in executable price."""
    z = float(norm.ppf(min(max(k, 1e-6), 1 - 1e-6)))
    fair_yes = float(norm.cdf(z + (delta if side == "yes" else -delta)))
    fair = fair_yes if side == "yes" else 1 - fair_yes
    return fair - allin


def wauc(p, y, strata):
    from research import ranking_calibration as rc      # c-24's code, as registered
    return rc.wauc(p, y, strata)


def spearman(a, b):
    ra, rb = rankdata(a), rankdata(b)
    if len(a) < 3 or ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def bh(pvals, q=BH_Q):
    """-> set of surviving indices."""
    idx = sorted(range(len(pvals)), key=lambda i: pvals[i])
    n, keep = len(pvals), -1
    for rank, i in enumerate(idx, 1):
        if pvals[i] <= q * rank / n:
            keep = rank
    return set(idx[:keep]) if keep > 0 else set()


class Blocks:
    """Game-block bootstrap over row indices. fn(idx) -> float or None."""

    def __init__(self, block_keys):
        groups = defaultdict(list)
        for i, g in enumerate(block_keys):
            groups[g].append(i)
        self.gidx = [np.array(v) for _g, v in sorted(groups.items())]
        self.n, self.G = len(block_keys), len(self.gidx)

    def boot(self, fn, draws=BOOT, seed=SEED):
        est = fn(np.arange(self.n)) if self.n else None
        out = {"est": est, "lo": None, "hi": None, "se": None, "games": self.G, "n": self.n}
        if self.G < 2 or est is None:
            return out
        rng = np.random.default_rng(seed)
        vals = []
        for _ in range(draws):
            pick = rng.integers(0, self.G, self.G)
            v = fn(np.concatenate([self.gidx[j] for j in pick]))
            if v is not None:
                vals.append(v)
        if len(vals) < draws // 2:
            return out
        vals = np.array(vals)
        out.update(lo=float(np.percentile(vals, 2.5)), hi=float(np.percentile(vals, 97.5)),
                   se=float(vals.std()))
        return out


def pval(r):
    """z = est / bootstrap SE; a zero-variance or <5-game interval is p = 1."""
    if r["se"] is None or r["se"] <= 0 or r["games"] < MIN_GAMES or r["est"] is None:
        return 1.0
    return float(2 * norm.sf(abs(r["est"] / r["se"])))


def fmt(r, d=4, scale=1.0):
    if r is None or r.get("est") is None:
        return "n/a"
    if r["lo"] is None:
        return f"{r['est'] * scale:+.{d}f} [no interval] (n {r['n']}, {r['games']} games)"
    star = "*" if (r["lo"] > 0 or r["hi"] < 0) else " "
    read = "" if r["games"] >= MIN_GAMES else "  NOT READ: <5 games"
    return (f"{r['est'] * scale:+.{d}f} [{r['lo'] * scale:+.{d}f}, {r['hi'] * scale:+.{d}f}]{star}"
            f" MDE {2.8 * r['se'] * scale:.{d}f} (n {r['n']}, {r['games']} games){read}")


def groups_of(stat, pos):
    out = [f"{stat}-all"]
    if stat == "receptions" and pos in ("WR", "TE", "RB"):
        out.append(f"receptions-{pos}")
    return out


# =============================================================================
# extract - the only step that reads market_log.db
# =============================================================================

def fee_authority(out):
    """The /series fee_type for the two series: board_019 snapshot, and live
    if the free endpoint answers. -> dict; raises on a mismatch (Q3 stop rule)."""
    seen = {}
    snap = os.path.join(os.path.dirname(config.DB_PATH), "board_019.db")
    if os.path.exists(snap):
        c = ro(snap)
        for t, ft, fm in c.execute("SELECT ticker, fee_type, fee_multiplier FROM series "
                                   "WHERE ticker IN ('KXNFLREC','KXNFLRSHATT')"):
            seen[f"snapshot:{t}"] = [ft, fm]
        c.close()
    try:
        import httpx
        for s in KALSHI_SERIES:
            r = httpx.get(f"https://api.elections.kalshi.com/trade-api/v2/series/{s}", timeout=15,
                          headers={"User-Agent": "calibratedsports-research/1.0 python-httpx"})
            j = r.json().get("series", {})
            seen[f"live:{s}"] = [j.get("fee_type"), j.get("fee_multiplier")]
    except Exception as e:  # noqa: BLE001 - the live read is optional
        seen["live:error"] = [type(e).__name__, None]
    out(f"  /series fee authority: {seen}")
    types = {v[0] for k, v in seen.items() if not k.endswith("error")}
    if not types:
        raise SystemExit("no /series fee_type could be read - Q3 stop rule")
    if types != {EXPECT_FEE_TYPE}:
        raise SystemExit(f"/series fee_type {types} != {EXPECT_FEE_TYPE!r} - Q3 stop rule")
    for s in KALSHI_SERIES:
        mk, tk = series_multiplier(s)
        if (float(mk), float(tk)) != (0.0, 1.0):
            raise SystemExit(f"core.fees disagrees with /series for {s}")
    return seen


def extract(cache, workers, out=print):
    if os.path.exists(cache):
        raise SystemExit(f"{cache} exists; delete it by hand to re-extract")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    from core import settlement
    from research import walkforward as wf
    fees = fee_authority(out)
    t0 = time.time()
    con = ro()
    games = {r[0]: {"week": r[1], "kick": r[2], "hs": r[3]} for r in con.execute(
        """SELECT game_id, week, kickoff_ts, home_score FROM nfl_games n WHERE season=? AND
           data_version=(SELECT MAX(data_version) FROM nfl_games m WHERE m.game_id=n.game_id)""",
        (SEASON,))}
    kout = con.execute(
        f"""SELECT mo.market_id, o.outcome_id, o.entity_id, o.stat, o.line, o.event_id, o.week,
                   o.push_possible
              FROM market_outcome mo JOIN outcomes o USING (outcome_id)
             WHERE mo.venue='kalshi' AND o.season=? AND o.side='over'
               AND o.stat IN ('receptions','rush_attempts')
               AND o.week IN ({','.join(map(str, WEEKS))})""", (SEASON,)).fetchall()
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
    rows, tasks_g, pos_team = [], defaultdict(list), {}
    for mid, oid, gsis, stat, line, game, week, push in kout:
        if series_of(mid) not in KALSHI_SERIES:
            continue
        census[f"rungs mapped wk{week}"] += 1
        g = games.get(game)
        if not g or g["hs"] is None or g["kick"] is None:
            census["game without final score"] += 1
            continue
        key = (gsis, week)
        has = key in vals
        snap, steam, spos = snaps.get((gsis, game), (None, None, None))
        res, actual, _void, _st = settlement.settle(
            vals[key][stat] if has else None, has, snap, stat, line, push)
        if res not in (settlement.OVER, settlement.UNDER):
            census[f"settlement {res}"] += 1
            continue
        entry = g["kick"] - ENTRY_LEAD
        q = None
        for ts, bid, ask, src in con.execute(
                """SELECT ts, best_bid, best_ask, source FROM quotes INDEXED BY ix_quotes_market_ts
                    WHERE venue='kalshi' AND market_id=? AND ts<=? ORDER BY ts DESC LIMIT 8""",
                (mid, entry)):
            if src == "live":
                q = (ts, bid, ask)
                break
        if q is None:
            census["entry: no kalshi quote before entry"] += 1
            continue
        ts, bid, ask = q
        if bid is None or ask is None or not (0 < bid < ask < 1):
            census["entry: kalshi one-sided"] += 1
            continue
        if entry - ts > STALE:
            census["entry: kalshi stale > 600s"] += 1
            continue
        census["rungs usable at entry"] += 1
        depth = {}
        for dts, side, tp, tsz, v100, v500, v1000, tot in con.execute(
                """SELECT ts, side, touch_price, touch_size, vwap_100, vwap_500, vwap_1000, total_size
                     FROM market_depth WHERE venue='kalshi' AND market_id=? AND ts<=? AND ts>=?
                    ORDER BY ts DESC""", (mid, entry, entry - DEPTH_LOOKBACK)):
            if side not in depth:
                depth[side] = {"age": entry - dts, "touch": tp, "size": tsz, "100": v100,
                               "500": v500, "1000": v1000, "total": tot}
        if depth:
            census["rungs with a depth snapshot <= 600s before entry"] += 1
        p, t = pw.get(key) or (spos, steam)
        pos_team.setdefault((gsis, game), (p, t))
        rows.append({"mid": mid, "oid": oid, "gsis": gsis, "stat": stat, "line": float(line),
                     "game": game, "week": week, "pos": pos_team[(gsis, game)][0],
                     "y": 1.0 if res == settlement.OVER else 0.0, "x": actual,
                     "bid": bid, "ask": ask, "k": (bid + ask) / 2, "depth": depth})
        tasks_g[(gsis, stat, game, g["kick"], week)].append((oid, float(line), push))
    out(f"  kalshi rows {len(rows):,} ({time.time() - t0:.0f}s)")

    # Q1-C reference: settled book closes near the money, 2023-2025
    ref = []
    for T in REF_SEASONS:
        pwT = wf.player_week_rows(con, T)
        best = {}
        for gsis, week, stat, line, pb, actual in con.execute(
                """SELECT o.entity_id, o.week, o.stat, o.line, oc.p_bench, s.actual
                     FROM outcome_close oc JOIN outcomes o USING (outcome_id)
                     JOIN outcome_settlement s ON s.outcome_id = o.outcome_id
                    WHERE o.season=? AND o.side='over' AND o.stat IN ('receptions','rush_attempts')
                      AND oc.p_bench BETWEEN ? AND ? AND s.result IN ('over','under')
                      AND s.actual IS NOT NULL
                      AND s.data_version=(SELECT MAX(data_version) FROM outcome_settlement z
                                           WHERE z.outcome_id=s.outcome_id)""",
                (T, REF_BAND[0], REF_BAND[1])):
            k = (gsis, week, stat)
            if k not in best or abs(pb - 0.5) < abs(best[k][1] - 0.5):
                best[k] = (line, pb, actual)
        for (gsis, week, stat), (line, pb, actual) in best.items():
            pos = (pwT.get((gsis, week)) or (None, None))[0]
            ref.append([T, stat, pos, float(line), pb, actual])
    held = time.time() - t0
    con.close()
    out(f"  reference player-games {len(ref):,}; store held {held:.0f}s; census {dict(census)}")

    cvals = {"default": wf.constants_for(SEASON, {}, lambda a, b: _drift(wf, a, b)).values}
    tasks = [(SEASON, g, s, gm, k, w, *pos_team.get((g, gm), (None, None)), lines, cvals)
             for (g, s, gm, k, w), lines in sorted(tasks_g.items())]
    out(f"  fit tasks {len(tasks)}")
    t1 = time.time()
    if workers > 1:
        import multiprocessing as mp
        with mp.Pool(workers, initializer=wf._worker_init, initargs=(config.DB_PATH,)) as pool:
            results = list(pool.imap(wf._predict_task, tasks, chunksize=4))
    else:
        wf._worker_init(config.DB_PATH)
        results = [wf._predict_task(t) for t in tasks]
    preds, errs = {}, Counter()
    for res in results:
        if "err" in res:
            errs[res["err"]] += len(res["lines"])
            continue
        preds.update(res["probs"]["default"])
    out(f"  predicted in {time.time() - t1:.0f}s; fit errors (rungs) {dict(errs)}")
    for r in rows:
        r["m"] = preds.get(r["oid"])
    with open(cache, "w") as f:
        json.dump({"rows": rows, "ref": ref, "census": dict(census), "fit_errors": dict(errs),
                   "fees": fees, "extracted_ts": time.time(), "store_held_s": held}, f)
    out(f"  wrote {cache}")


def _drift(wf, a, b):
    con = ro()
    try:
        return wf.refit_drift(con, a, b)
    finally:
        con.close()


# =============================================================================
# ladders
# =============================================================================

def build_ladders(rows):
    """-> list of ladder dicts, rungs sorted by line, survival made monotone."""
    by = defaultdict(list)
    for r in rows:
        by[(r["gsis"], r["stat"], r["game"])].append(r)
    ladders = []
    for (gsis, stat, game), rs in sorted(by.items()):
        rs.sort(key=lambda r: r["line"])
        lines = [r["line"] for r in rs]
        raw = [r["k"] for r in rs]
        surv = pav_decreasing(raw)
        adj = sum(abs(a - b) for a, b in zip(raw, surv))
        c = min(range(len(rs)), key=lambda i: abs(raw[i] - 0.5))
        ladders.append({"gsis": gsis, "stat": stat, "game": game, "week": rs[0]["week"],
                        "pos": rs[0]["pos"], "x": rs[0]["x"], "rungs": rs, "lines": lines,
                        "raw": raw, "surv": surv, "pav_adj": adj,
                        "central": c if CENTRAL[0] <= raw[c] <= CENTRAL[1] else None})
    return ladders


def mean_diff(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return lambda idx: float((a[idx] - b[idx]).mean()) if len(idx) else None


# =============================================================================
# Q1
# =============================================================================

def q1(ladders, ref, out):
    out("\n== Q1 - the shape between the rungs ==")
    L = [l for l in ladders if len(l["rungs"]) >= MIN_RUNGS]
    touched = [l for l in L if l["pav_adj"] > 1e-12]
    out(f"  ladders >= {MIN_RUNGS} rungs: {len(L)} of {len(ladders)}; games "
        f"{len({l['game'] for l in L})}; isotonic step touched {len(touched)} "
        f"(mean |adj| summed over the ladder {np.mean([l['pav_adj'] for l in touched]) if touched else 0:.4f})")
    out(f"  rungs per ladder: median {np.median([len(l['rungs']) for l in L]):.0f}, "
        f"with a central rung in {CENTRAL}: {sum(l['central'] is not None for l in L)}")
    tests, res = [], {"n_ladders": len(L), "pav_touched": len(touched), "A": {}, "B": {}, "C": {}}
    # reference table: (stat, pos, line) -> Counter of actual
    reft = defaultdict(Counter)
    for _T, stat, pos, line, _pb, actual in ref:
        reft[(stat, pos, line)][int(round(actual))] += 1
        reft[(stat, None, line)][int(round(actual))] += 1
    for grp in GROUPS:
        gl = [l for l in L if grp in groups_of(l["stat"], l["pos"])]
        if not gl:
            continue
        # ---- A: unit cells by offset, and the two tails
        cells = defaultdict(list)       # name -> [(game, hit, q)]
        for l in gl:
            x = int(round(l["x"]))
            cells["below lowest rung"].append((l["game"], float(x < l["lines"][0]), 1 - l["surv"][0]))
            cells["above highest rung"].append((l["game"], float(x > l["lines"][-1]), l["surv"][-1]))
            if l["central"] is None:
                continue
            base = math.ceil(l["lines"][l["central"]])
            for xx, q in unit_cells(l["lines"], l["surv"]).items():
                d = xx - base
                if d in OFFSETS:
                    cells[f"d={d:+d}"].append((l["game"], float(x == xx), q))
        res["A"][grp] = {}
        for name in ["below lowest rung"] + [f"d={d:+d}" for d in OFFSETS] + ["above highest rung"]:
            c = cells.get(name, [])
            if not c:
                continue
            hit = np.array([v[1] for v in c])
            q = np.array([v[2] for v in c])
            r = Blocks([v[0] for v in c]).boot(mean_diff(hit, q))
            r.update(implied=float(q.mean()), realised=float(hit.mean()))
            res["A"][grp][name] = r
            tests.append((f"A|{grp}|{name}", r))
        # ---- B: level and dispersion of the mid-PIT
        t, v = [], []
        for l in gl:
            q = ladder_cells(l["lines"], l["surv"])
            j = cell_index(l["lines"], l["x"])
            tt = mid_pit(q, j)
            t.append(tt - 0.5)
            v.append(12 * (tt - 0.5) ** 2 - 12 * pit_var(q))
            l["t"] = tt
        b = Blocks([l["game"] for l in gl])
        M = b.boot(mean_diff(t, np.zeros(len(t))))
        D = b.boot(mean_diff(v, np.zeros(len(v))))
        res["B"][grp] = {"level_M": M, "dispersion_D": D}
        tests += [(f"B|{grp}|level M", M), (f"B|{grp}|dispersion D", D)]
        # ---- C: the realised reference, descriptive
        ctab = defaultdict(lambda: [0, 0.0, 0.0, 0.0])   # d -> [n, implied, ref, realised]
        dropped = used = 0
        kc, pb = [], []
        for l in gl:
            c = l["central"]
            if c is None or not (REF_BAND[0] <= l["raw"][c] <= REF_BAND[1]):
                continue
            key = (l["stat"], l["pos"] if grp.endswith(("WR", "TE", "RB")) else None, l["lines"][c])
            hist = reft.get(key)
            n = sum(hist.values()) if hist else 0
            if n < REF_MIN_N:
                dropped += 1
                continue
            used += 1
            kc.append(l["raw"][c])
            base = math.ceil(l["lines"][c])
            x = int(round(l["x"]))
            for xx, q in unit_cells(l["lines"], l["surv"]).items():
                d = xx - base
                if d in OFFSETS:
                    e = ctab[d]
                    e[0] += 1
                    e[1] += q
                    e[2] += hist[xx] / n
                    e[3] += float(x == xx)
        res["C"][grp] = {"ladders_used": used, "dropped_ref_n_lt_30": dropped,
                         "kalshi_central_mid_mean": float(np.mean(kc)) if kc else None,
                         "cells": {f"d={d:+d}": {"n": e[0], "implied": e[1] / e[0], "ref_2023_25": e[2] / e[0],
                                                 "realised_2026": e[3] / e[0]}
                                   for d, e in sorted(ctab.items()) if e[0]}}
    # ---- print, BH
    ps = [pval(r) for _n, r in tests]
    keep = bh(ps)
    out(f"  registered intervals {len(tests)} (expected 55 if every cell exists); BH q={BH_Q} "
        f"survivors {len(keep)}")
    for grp in GROUPS:
        if grp not in res["A"]:
            continue
        out(f"  -- {grp}")
        out(f"     B level M      {fmt(res['B'][grp]['level_M'])}")
        out(f"     B dispersion D {fmt(res['B'][grp]['dispersion_D'])}   (>0 ladder too narrow)")
        for name, r in res["A"][grp].items():
            i = [n for n, _ in tests].index(f"A|{grp}|{name}")
            out(f"     A {name:<19} implied {r['implied']:.4f} realised {r['realised']:.4f}  diff "
                f"{fmt(r)}{'  BH' if i in keep else ''}")
        c = res["C"][grp]
        out(f"     C reference (descriptive): ladders {c['ladders_used']}, dropped n<30 "
            f"{c['dropped_ref_n_lt_30']}, mean central mid {c['kalshi_central_mid_mean']}")
        for d, e in c["cells"].items():
            out(f"       {d}  n {e['n']:>4}  kalshi implied {e['implied']:.4f}  books-era realised "
                f"{e['ref_2023_25']:.4f}  2026 realised {e['realised_2026']:.4f}")
    surv = [(tests[i][0], tests[i][1]) for i in sorted(keep)]
    shape = [n for n, _ in surv if "level M" not in n]
    res["bh_survivors"] = [{"name": n, "est": r["est"], "lo": r["lo"], "hi": r["hi"]} for n, r in surv]
    ranked = sorted(tests, key=lambda t: -abs(t[1]["est"] / t[1]["se"]) if t[1]["se"] else 0)
    res["ranked_by_absz"] = [{"name": n, "z": r["est"] / r["se"], "est": r["est"],
                              "mde": 2.8 * r["se"]} for n, r in ranked[:10] if r["se"]]
    out("  largest |z| (registered family):")
    for e in res["ranked_by_absz"][:8]:
        out(f"    {e['name']:<44} z {e['z']:+.2f}  est {e['est']:+.4f}  MDE {e['mde']:.4f}")
    res["verdict"] = ("the ladder's implied shape departs from what happened: " + "; ".join(shape)
                      if shape else "no detectable departure of the implied shape")
    res["n_tests"] = len(tests)
    out(f"  VERDICT Q1 (pre-registered rule): {res['verdict']}")
    return res


# =============================================================================
# Q2
# =============================================================================

def q2(ladders, out):
    out("\n== Q2 - is the whole ladder shifted, and is that better than one rung ==")
    res = {}
    rows_all = [r for l in ladders for r in l["rungs"] if r.get("m") is not None]
    w23 = [r for r in rows_all if r["week"] in (2, 3)]
    if w23:
        d = np.mean([(r["m"] - r["y"]) ** 2 - (r["k"] - r["y"]) ** 2 for r in w23])
        res["reproduction"] = {"n_wk23": len(w23), "brier_diff_wk23": float(d), "c24_p2": C24_P2}
        out(f"  reproduction guard, weeks 2-3: n {len(w23)} (c-24 P2 {C24_P2[0]}), Brier(model) - "
            f"Brier(Kalshi mid) {d:+.4f} (c-24 {C24_P2[1]:+.4f})")
    L = []
    for l in ladders:
        rs = [r for r in l["rungs"] if r.get("m") is not None]
        if len(rs) < MIN_RUNGS:
            continue
        k = np.array([r["k"] for r in rs])
        m = np.array([r["m"] for r in rs])
        c, ss1 = fit_shift(k, m)
        _a, b, ss2 = fit_shift_scale(k, m)
        ss0 = float(((np.clip(m, CLIP, 1 - CLIP) - np.clip(k, CLIP, 1 - CLIP)) ** 2).sum())
        loo = []
        for i in range(len(rs)):
            keep = np.arange(len(rs)) != i
            loo.append(fit_shift(k[keep], m[keep])[0])
        ci = min(range(len(rs)), key=lambda i: abs(rs[i]["k"] - 0.5))
        L.append({"l": l, "rs": rs, "c": c, "b": b, "ss0": ss0, "ss1": ss1, "ss2": ss2, "loo": loo,
                  "z": [probit(r["m"]) - probit(r["k"]) for r in rs],
                  "zc": probit(rs[ci]["m"]) - probit(rs[ci]["k"]),
                  "has_central": CENTRAL[0] <= rs[ci]["k"] <= CENTRAL[1]})
    out(f"  ladders with >= {MIN_RUNGS} modelled rungs: {len(L)}; games "
        f"{len({x['l']['game'] for x in L})}; rungs {sum(len(x['rs']) for x in L)}")
    cs = np.array([x["c"] for x in L])
    out(f"  fitted ladder shift c (probit; >0 = model above market): p10 {np.percentile(cs, 10):+.2f} "
        f"p50 {np.percentile(cs, 50):+.2f} p90 {np.percentile(cs, 90):+.2f}  mean {cs.mean():+.3f}; "
        f"fitted scale b: p50 {np.median([x['b'] for x in L]):.2f}")
    # decomposition
    b = Blocks([x["l"]["game"] for x in L])
    s0 = np.array([x["ss0"] for x in L])
    s1 = np.array([x["ss1"] for x in L])
    s2 = np.array([x["ss2"] for x in L])
    level = b.boot(lambda i: float(1 - s1[i].sum() / s0[i].sum()))
    scale = b.boot(lambda i: float((s1[i].sum() - s2[i].sum()) / s0[i].sum()))
    resid = b.boot(lambda i: float(s2[i].sum() / s0[i].sum()))
    rms = math.sqrt(s0.sum() / sum(len(x["rs"]) for x in L))
    out(f"  rung-level RMS disagreement |m - k|: {rms:.4f}")
    out(f"  level share  {fmt(level, 3)}")
    out(f"  scale share  {fmt(scale, 3)}   (further reduction from adding a scale)")
    out(f"  residual     {fmt(resid, 3)}   (shape neither a shift nor a scale explains)")
    per = np.array([1 - x["ss1"] / x["ss0"] for x in L if x["ss0"] > 1e-9])
    out(f"  per-ladder level share: p25 {np.percentile(per, 25):.2f} p50 {np.percentile(per, 50):.2f} "
        f"p75 {np.percentile(per, 75):.2f}")
    if level["lo"] is not None and level["lo"] > 0.5:
        dv = "mostly level"
    elif level["hi"] is not None and level["hi"] < 0.5:
        dv = "mostly not level"
    else:
        dv = "undetermined"
    out(f"  DECOMPOSITION READ: {dv}")
    res.update(n_ladders=len(L), level_share=level, scale_share=scale, residual_share=resid,
               rms_gap=rms, decomposition=dv, per_ladder_level_share_p50=float(np.median(per)))
    # ordering
    tests = []
    res["ordering"] = {}
    for scope in ("all", "receptions", "rush_attempts"):
        LL = [x for x in L if scope == "all" or x["l"]["stat"] == scope]
        if not LL:
            continue
        y = np.array([r["y"] for x in LL for r in x["rs"]])
        z = np.array([v for x in LL for v in x["z"]])
        c = np.array([x["c"] for x in LL for _ in x["rs"]])
        cl = np.array([v for x in LL for v in x["loo"]])
        stat_id = np.array([0 if x["l"]["stat"] == "receptions" else 1 for x in LL for _ in x["rs"]])
        strata = stat_id * 10 + np.array([min(int(10 * r["k"]), 9) for x in LL for r in x["rs"]])
        game = [x["l"]["game"] for x in LL for _ in x["rs"]]
        pg = [(x["l"]["game"], x["l"]["gsis"], x["l"]["stat"]) for x in LL for _ in x["rs"]]
        bg = Blocks(game)

        def W(s):
            return lambda i: (lambda v: None if v is None else v - 0.5)(wauc(s[i], y[i], strata[i]))

        def dW(s1_, s2_):
            def f(i):
                a, b_ = wauc(s1_[i], y[i], strata[i]), wauc(s2_[i], y[i], strata[i])
                return None if a is None or b_ is None else a - b_
            return f
        dA = bg.boot(dW(c, z))
        out(f"  -- {scope}: rungs {len(y)}, ladders {len(LL)}")
        out(f"     MDE of dA (printed first, 2.8 x bootstrap SE): "
            f"{2.8 * dA['se'] if dA['se'] else float('nan'):.4f}")
        aR, aL = bg.boot(W(z)), bg.boot(W(c))
        dLoo = bg.boot(dW(cl, z))
        aLoo = bg.boot(W(cl))
        dA_pg = Blocks(pg).boot(dW(c, z))
        out(f"     A_R - 0.5 (rung's own gap)        {fmt(aR)}")
        out(f"     A_L - 0.5 (ladder shift)          {fmt(aL)}")
        out(f"     dA = A_L - A_R   PRIMARY          {fmt(dA)}")
        out(f"     dA, player-game blocks            {fmt(dA_pg)}")
        out(f"     A_LOO - 0.5 (rest of the ladder)  {fmt(aLoo)}")
        out(f"     LOO dA = A_LOO - A_R              {fmt(dLoo)}")
        # one observation per ladder
        LC = [x for x in LL if x["has_central"] and "t" in x["l"]]
        t = np.array([x["l"]["t"] for x in LC])
        cc = np.array([x["c"] for x in LC])
        zc = np.array([x["zc"] for x in LC])
        bl = Blocks([x["l"]["game"] for x in LC])
        rL = bl.boot(lambda i: spearman(cc[i], t[i]))
        rR = bl.boot(lambda i: spearman(zc[i], t[i]))
        dR = bl.boot(lambda i: (lambda a, b_: None if a is None or b_ is None else a - b_)(
            spearman(cc[i], t[i]), spearman(zc[i], t[i])))
        out(f"     per ladder (n {len(LC)}): Spearman(c, PIT) {fmt(rL, 3)}")
        out(f"                           Spearman(z_central, PIT) {fmt(rR, 3)}")
        out(f"                           difference {fmt(dR, 3)}")
        if dA["games"] < MIN_GAMES or dA["lo"] is None:
            v = "not read (<5 games)"
        elif not (aR["lo"] > 0 or aL["lo"] > 0):
            v = "a comparison of two nulls: neither level of disagreement orders the outcome"
        elif dA["lo"] > 0:
            v = "ladder-level disagreement orders outcomes better than the rung's own gap"
        elif dA["hi"] < 0:
            v = "the rung's own gap orders outcomes better than the ladder-level disagreement"
        else:
            v = "no detectable difference between ladder-level and rung-level disagreement"
        out(f"     VERDICT Q2 {scope} (pre-registered rule): {v}")
        res["ordering"][scope] = {"A_R": aR, "A_L": aL, "dA": dA, "dA_player_game_blocks": dA_pg,
                                  "A_LOO": aLoo, "dA_LOO": dLoo, "spearman_c": rL,
                                  "spearman_zc": rR, "spearman_diff": dR, "verdict": v,
                                  "rungs": len(y), "ladders": len(LL)}
        tests += [(f"{scope}|{n}", r) for n, r in (("dA", dA), ("A_R", aR), ("A_L", aL),
                                                   ("dA_LOO", dLoo), ("rho_c", rL), ("rho_zc", rR),
                                                   ("rho_diff", dR))]
    keep = bh([pval(r) for _n, r in tests])
    res["n_tests"] = len(tests)
    res["bh_survivors"] = [{"name": tests[i][0], "est": tests[i][1]["est"]} for i in sorted(keep)]
    out(f"  registered intervals {len(tests)}; BH q={BH_Q} survivors: "
        f"{[(s['name'], round(s['est'], 4)) for s in res['bh_survivors']]}")
    return res


# =============================================================================
# Q3
# =============================================================================

def q3(ladders, out):
    out("\n== Q3 - the cheapest rung to express a view (executable VWAP + taker fee) ==")
    res = {}
    all_r = [r for l in ladders for r in l["rungs"]]
    wd = [r for r in all_r if r["depth"]]
    out(f"  rungs usable at entry {len(all_r)}; with a depth snapshot {len(wd)} "
        f"({ {w: sum(1 for r in wd if r['week'] == w) for w in WEEKS} } by week); "
        f"without {len(all_r) - len(wd)} - given no cost")
    ages = [d["age"] for r in wd for d in r["depth"].values()]
    if ages:
        out(f"  depth age at entry (s): p50 {np.percentile(ages, 50):.0f} p90 {np.percentile(ages, 90):.0f}")
        dis = [abs(r["depth"]["buy_yes"]["touch"] - r["ask"]) for r in wd
               if "buy_yes" in r["depth"] and r["depth"]["buy_yes"]["touch"] is not None]
        out(f"  depth touch vs quoted ask, |diff| <= 1c: {np.mean([d <= 0.0101 for d in dis]):.3f} "
            f"of {len(dis)} (the depth row is up to 600s older than nothing; the quote up to 600s)")
    res["coverage"] = {"rungs": len(all_r), "with_depth": len(wd)}
    tests = []
    for size in SIZES:
        key = str(size)
        res[key] = {}
        for side, dside in (("yes", "buy_yes"), ("no", "buy_no")):
            elig = []
            for l in ladders:
                rr = []
                for r in l["rungs"]:
                    d = r["depth"].get(dside)
                    ex = rung_exec(r["k"], d.get(key) if d else None, size, r["mid"], side) if d else None
                    if ex:
                        rr.append(dict(ex, k=r["k"], y=r["y"], touch_size=d["size"], mid_id=r["mid"]))
                if len(rr) >= MIN_RUNGS:
                    elig.append({"game": l["game"], "stat": l["stat"], "rungs": rr})
            games = sorted({e["game"] for e in elig})
            out(f"  -- size {size}, buy {side.upper()}: ladders with >= {MIN_RUNGS} eligible rungs "
                f"{len(elig)}, games {len(games)}, eligible rungs {sum(len(e['rungs']) for e in elig)}")
            if len(games) < MIN_GAMES:
                out("     NOT READ: <5 games (stop rule)")
                res[key][side] = {"ladders": len(elig), "games": len(games), "read": False}
                continue
            # cost by the rung's own (side) price bucket - every eligible rung
            flat = [(e["game"], r) for e in elig for r in e["rungs"]]
            bk = {}
            for lo in (0.0, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9):
                hi = {0.0: 0.1, 0.1: 0.2, 0.2: 0.35, 0.35: 0.5, 0.5: 0.65, 0.65: 0.8, 0.8: 0.9, 0.9: 1.0}[lo]
                sel = [(g, r) for g, r in flat
                       if lo <= (r["k"] if side == "yes" else 1 - r["k"]) < hi]
                if len(sel) < 10:
                    continue
                cost = np.array([r["cost"] for _g, r in sel])
                be = np.array([r["be"] for _g, r in sel])
                fee = np.array([r["fee"] for _g, r in sel])
                real = np.array([(r["y"] if side == "yes" else 1 - r["y"]) - r["allin"] for _g, r in sel])
                rb = Blocks([g for g, _r in sel]).boot(mean_diff(real, np.zeros(len(real))))
                bk[f"{lo:.2f}-{hi:.2f}"] = {
                    "n": len(sel), "cost_c_mean": float(100 * cost.mean()),
                    "cost_c_p50": float(100 * np.median(cost)), "fee_c_mean": float(100 * fee.mean()),
                    "be_p50": float(np.median(be)), "be_mean": float(be.mean()),
                    "touch_size_p50": float(np.median([r["touch_size"] or 0 for _g, r in sel])),
                    "realised_no_view": rb}
                out(f"     side price {lo:.2f}-{hi:.2f}: n {len(sel):>4}  all-in cost over mid mean "
                    f"{100 * cost.mean():.2f}c p50 {100 * np.median(cost):.2f}c (fee {100 * fee.mean():.2f}c)"
                    f"  break-even view p50 {np.median(be):.3f} mean {be.mean():.3f}  touch p50 "
                    f"{bk[f'{lo:.2f}-{hi:.2f}']['touch_size_p50']:.0f}")
                out(f"         realised, no view (pp): {fmt(rb, 2, 100)}")
            # fixed rules
            rule = {}
            for name, tau in RULES:
                tgt = tau if side == "yes" else 1 - tau       # rule is on the rung's YES mid
                pick = [min(e["rungs"], key=lambda r: abs(r["k"] - tau)) for e in elig]
                rule[name] = pick
                _ = tgt
            g_of = [e["game"] for e in elig]
            rtab = {}
            for name, pick in rule.items():
                be = np.array([r["be"] for r in pick])
                ev = {v: np.array([view_ev(r["k"], r["allin"], v, side) for r in pick]) for v in VIEWS}
                same = np.mean([a is b for a, b in zip(pick, rule["central"])])
                rtab[name] = {"mid_mean": float(np.mean([r["k"] for r in pick])),
                              "be_p50": float(np.median(be)), "be_mean": float(be.mean()),
                              "cost_c_mean": float(100 * np.mean([r["cost"] for r in pick])),
                              "ev_pp": {str(v): float(100 * ev[v].mean()) for v in VIEWS},
                              "ev_per_dollar": {str(v): float(np.mean(ev[v] / np.array(
                                  [r["allin"] for r in pick]))) for v in VIEWS},
                              "same_rung_as_central": float(same)}
                out(f"     rule {name:<10} mean YES mid {rtab[name]['mid_mean']:.3f}  cost "
                    f"{rtab[name]['cost_c_mean']:.2f}c  break-even view p50 {rtab[name]['be_p50']:.3f}  "
                    f"EV pp at view " + "  ".join(f"{v}: {rtab[name]['ev_pp'][str(v)]:+.2f}" for v in VIEWS)
                    + f"  (same rung as central {same:.2f})")
            # cross-fitted worth of the choice
            half = {g: i % 2 for i, g in enumerate(games)}
            evp = {name: np.array([view_ev(r["k"], r["allin"], PRIMARY_VIEW, side) for r in pick])
                   for name, pick in rule.items()}
            h = np.array([half[g] for g in g_of])
            diff = np.zeros(len(elig))
            chosen = {}
            for a in (0, 1):
                best = max(evp, key=lambda n: evp[n][h == a].mean() if (h == a).any() else -9)
                chosen[a] = best
                diff[h == 1 - a] = (evp[best] - evp["central"])[h == 1 - a]
            cf = Blocks(g_of).boot(mean_diff(diff, np.zeros(len(diff))))
            out(f"     CROSS-FITTED worth of the choice at view {PRIMARY_VIEW} (pp/contract): "
                f"{fmt(cf, 2, 100)}   rule chosen on each half: {chosen}")
            tests.append((f"{size}|{side}", cf))
            # descriptive best-of-n
            best_minus = defaultdict(list)
            where = Counter()
            for e in elig:
                ev = [view_ev(r["k"], r["allin"], PRIMARY_VIEW, side) for r in e["rungs"]]
                cen = min(range(len(ev)), key=lambda i: abs(e["rungs"][i]["k"] - 0.5))
                bi = int(np.argmax(ev))
                best_minus["central"].append(ev[bi] - ev[cen])
                best_minus["mean"].append(ev[bi] - float(np.mean(ev)))
                best_minus["worst"].append(ev[bi] - min(ev))
                kb = e["rungs"][bi]["k"]
                where["below 0.35" if kb < 0.35 else "0.35-0.65" if kb < 0.65 else "above 0.65"] += 1
            desc = {k: float(100 * np.mean(v)) for k, v in best_minus.items()}
            out(f"     descriptive (>= 0 by construction, no null): best rung minus central "
                f"{desc['central']:.2f}pp, minus the mean rung {desc['mean']:.2f}pp, minus the worst "
                f"{desc['worst']:.2f}pp; best rung's YES mid: {dict(where)}")
            res[key][side] = {"ladders": len(elig), "games": len(games), "read": True, "buckets": bk,
                              "rules": rtab, "crossfit": cf, "chosen": {str(k): v for k, v in chosen.items()},
                              "best_minus_pp": desc, "best_rung_where": dict(where)}
    keep = bh([pval(r) for _n, r in tests]) if tests else set()
    res["n_tests"] = len(tests)
    res["bh_survivors"] = [tests[i][0] for i in sorted(keep)]
    for n, r in tests:
        v = ("not read" if r["games"] < MIN_GAMES or r["lo"] is None else
             "the choice of rung is worth this much" if r["lo"] > 0 else
             "the fixed-rule choice is WORSE than the central rung" if r["hi"] < 0 else
             "not shown to be worth anything")
        out(f"  VERDICT Q3 size|side {n} (pre-registered rule): {v}")
        res.setdefault("verdicts", {})[n] = v
    out(f"  registered intervals {len(tests)}; BH q={BH_Q} survivors {res['bh_survivors']}")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", required=True, help="scratch JSON, never committed")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json-out")
    a = ap.parse_args()
    if a.extract:
        extract(a.cache, a.workers)
        return 0
    with open(a.cache) as f:
        data = json.load(f)
    rows = data["rows"]
    if not rows:
        raise SystemExit("empty cache - refusing to report")
    out = print
    out(f"loaded rungs {len(rows):,}; census {data['census']}; fit errors {data['fit_errors']}")
    out(f"fees {data['fees']}")
    ladders = build_ladders(rows)
    games = {l["game"] for l in ladders}
    out(f"ladders {len(ladders)}; games {len(games)}; by week "
        f"{ {w: sum(1 for l in ladders if l['week'] == w) for w in WEEKS} }")
    result = {"census": data["census"], "fit_errors": data["fit_errors"], "fees": data["fees"],
              "ladders": len(ladders), "games": len(games)}
    if len(games) < 10 or len(ladders) < 100:
        out("STOP RULE: fewer than 10 games or 100 ladders - Q1 and Q2 are not read")
    else:
        result["q1"] = q1(ladders, data["ref"], out)
        result["q2"] = q2(ladders, out)
    result["q3"] = q3(ladders, out)
    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(result, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
