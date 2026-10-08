"""c-43 - the interval between two rungs of one player's Kalshi ladder.

    python -m research.interval_mass --cache D:/temp/c35/rows.json --mde --json-out research/results/interval_mde.json
    python -m research.interval_mass --cache D:/temp/c35/rows.json --mde-in research/results/interval_mde.json \
        --json-out research/results/interval_mass.json

PRE-REGISTRATION: docs/C43-interval-preregistration.md, committed at dbf8b03
BEFORE this script existed. This file implements it.

The rungs come from the scratch cache written by c-35's committed extract
(`python -m research.ladder_edges --cache <scratch> --extract`). `market_log.db`
is NOT opened here. This is not a model unit: the cache's model column is
dropped on load and never read. `--mde` additionally drops the outcome columns
before anything is computed, so the MDE file is written without an outcome.

Everything printed is an aggregate or an interval. Nothing in the cost arm is
priced at a mid: the mid states the value being bought and nothing else.
"""
import argparse
import json
import os
import sys
import zlib
from collections import Counter, defaultdict

import numpy as np
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import ladder_edges as le  # noqa: E402

EXPECT_SHAPE = (725, 3987, 48)            # ladders, rungs, games - c-35's population
WIDTHS = {"receptions": (1, 2, 3, 4, 5, 6, 7), "rush_attempts": (3, 6)}
BIN_EDGES = (0.05, 0.10, 0.20, 0.30, 0.45, 0.60)
BIN_NAMES = ("<0.05", "0.05-0.10", "0.10-0.20", "0.20-0.30", "0.30-0.45", "0.45-0.60", ">=0.60")
BOOT, SEED = 2000, 43
SIMS = 2000
MIN_GAMES = 5
BH_Q = 0.10
HOLM_ALPHA = 0.05
SIZES = (100, 500)
CENTRAL = le.CENTRAL
MODEL_KEY = "m"                           # dropped on load; never read
OUTCOME_KEYS = ("x", "y")
TOL = 1e-9


# =============================================================================
# load
# =============================================================================

def load(cache, outcomes):
    """-> (rows, meta). The model column is always dropped. With outcomes=False
    the outcome columns are dropped too, before anything can be computed."""
    with open(cache) as f:
        data = json.load(f)
    rows = data["rows"]
    if not rows:
        raise SystemExit("empty cache - refusing to report")
    for r in rows:
        r.pop(MODEL_KEY, None)
        if not outcomes:
            for k in OUTCOME_KEYS:
                r.pop(k, None)
    types = {v[0] for k, v in data["fees"].items() if not k.endswith("error")}
    if types != {le.EXPECT_FEE_TYPE}:
        raise SystemExit(f"cache fee type {types} != {le.EXPECT_FEE_TYPE!r} - stop rule")
    return rows, {"census": data["census"], "fees": data["fees"],
                  "extracted_ts": data.get("extracted_ts")}


def build(rows):
    """-> ladders: rungs sorted by line, raw mids kept as quoted."""
    by = defaultdict(list)
    for r in rows:
        by[(r["gsis"], r["stat"], r["game"])].append(r)
    ladders = []
    for (gsis, stat, game), rs in sorted(by.items()):
        rs.sort(key=lambda r: r["line"])
        raw = [r["k"] for r in rs]
        c = min(range(len(rs)), key=lambda i: abs(raw[i] - 0.5))
        ladders.append({"gsis": gsis, "stat": stat, "game": game, "week": rs[0]["week"],
                        "rungs": rs, "lines": [r["line"] for r in rs], "raw": raw,
                        "x": rs[0].get("x"),
                        "central": c if CENTRAL[0] <= raw[c] <= CENTRAL[1] else None})
    return ladders


def check_shape(ladders, expect=None):
    expect = EXPECT_SHAPE if expect is None else expect
    got = (len(ladders), sum(len(l["rungs"]) for l in ladders), len({l["game"] for l in ladders}))
    if got != tuple(expect):
        raise SystemExit(f"cache shape {got} != {tuple(expect)} (ladders, rungs, games) - stop rule")
    return got


# =============================================================================
# intervals
# =============================================================================

def bin_of(p):
    return sum(1 for e in BIN_EDGES if p >= e)


def position(c, i, j):
    """Where the interval sits relative to the central rung index c."""
    if c is None:
        return "no central rung"
    if i < c < j:
        return "straddle"
    if i >= c + 2:
        return "upper wing"
    if j <= c - 2:
        return "lower wing"
    return "adjacent"


def hit(line_lo, line_hi, x):
    """1 if the settled count landed strictly between the two rungs."""
    return float(line_lo < x < line_hi)


def intervals(ladders):
    """Every pair of rungs i < j of every ladder. Outcome `h` is None when the
    rows were loaded without outcomes."""
    out = []
    for li, l in enumerate(ladders):
        rs = l["rungs"]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, b = rs[i], rs[j]
                p = a["k"] - b["k"]
                out.append({"lad": li, "game": l["game"], "stat": l["stat"], "i": i, "j": j,
                            "w": b["line"] - a["line"], "p": p, "bin": bin_of(p),
                            "pos": position(l["central"], i, j),
                            "h": None if l["x"] is None else hit(a["line"], b["line"], l["x"]),
                            "buy_touch": a["ask"] - b["bid"], "sell_touch": a["bid"] - b["ask"]})
    return out


def test_cells(ivs):
    """The registered verdict family and the pre-check family, as index lists.
    -> (family, precheck): ordered dicts name -> np.array of interval indices.
    Every registered cell is present, empty when no interval falls in it."""
    fam, pre = {}, {}
    for stat, ws in WIDTHS.items():
        for w in ws:
            sel = [n for n, v in enumerate(ivs) if v["stat"] == stat and v["w"] == w]
            for b, name in enumerate(BIN_NAMES):
                fam[f"{stat}|w={w}|{name}"] = np.array([n for n in sel if ivs[n]["bin"] == b], int)
            fam[f"{stat}|w={w}|all"] = np.array(sel, int)
    rec = [n for n, v in enumerate(ivs) if v["stat"] == "receptions"]
    for w in (2, 3, 4, 5, 6, 7):
        pre[f"S_{w}"] = np.array([n for n in rec if ivs[n]["pos"] == "straddle" and ivs[n]["w"] == w], int)
    pre["S_all"] = np.array([n for n in rec if ivs[n]["pos"] == "straddle"], int)
    pre["U"] = np.array([n for n in rec if ivs[n]["pos"] == "upper wing"], int)
    pre["Lo"] = np.array([n for n in rec if ivs[n]["pos"] == "lower wing"], int)
    return fam, pre


PRE_DIRECTION = {"S_2": -1, "S_3": -1, "S_4": -1, "S_5": -1, "S_6": -1, "S_7": -1,
                 "S_all": -1, "U": +1, "Lo": +1}


# =============================================================================
# the outcome-free MDE
# =============================================================================

def null_sd(ladders, ivs, cells, sims=None, seed=SEED):
    """Each ladder's count drawn from the ladder's own implied cells (PAV-repaired),
    independently across ladders; -> {name: sd of mean(hit) over the cell's intervals}.
    Reads lines and mids only."""
    sims = SIMS if sims is None else sims
    rng = np.random.default_rng(seed)
    J = np.zeros((len(ladders), sims), dtype=np.int16)
    for li, l in enumerate(ladders):
        q = np.clip(le.ladder_cells(l["lines"], le.pav_decreasing(l["raw"])), 0, None)
        J[li] = rng.choice(len(q), size=sims, p=q / q.sum())
    lad = np.array([v["lad"] for v in ivs])
    lo = np.array([v["i"] for v in ivs])[:, None]
    hi = np.array([v["j"] for v in ivs])[:, None]
    out = {}
    for name, idx in cells.items():
        if not len(idx):
            out[name] = None
            continue
        jj = J[lad[idx]]
        out[name] = float(((jj > lo[idx]) & (jj <= hi[idx])).mean(axis=0).std())
    return out


def mde_table(ladders, ivs, out=print):
    fam, pre = test_cells(ivs)
    res = {}
    for label, cells in (("family", fam), ("precheck", pre)):
        sd = null_sd(ladders, ivs, cells)
        res[label] = {}
        for name, idx in cells.items():
            games = len({ivs[n]["game"] for n in idx})
            lads = len({ivs[n]["lad"] for n in idx})
            res[label][name] = {"n": int(len(idx)), "ladders": lads, "games": games,
                                "implied": float(np.mean([ivs[n]["p"] for n in idx])) if len(idx) else None,
                                "null_se": sd[name], "mde": None if sd[name] is None else 2.8 * sd[name]}
    out("== PRE-RUN MDE (no outcome read): 2.8 x sd of the statistic under the ladder's own cells ==")
    for label in ("family", "precheck"):
        for name, e in res[label].items():
            if e["n"]:
                out(f"  {label:<8} {name:<34} n {e['n']:>5} ladders {e['ladders']:>4} games {e['games']:>3}"
                    f"  implied {e['implied']:.4f}  MDE {e['mde']:.4f}")
    ex = [e["mde"] for e in res["family"].values() if e["n"] and e["games"] >= MIN_GAMES]
    out(f"  family cells that exist: {sum(1 for e in res['family'].values() if e['n'])} of "
        f"{len(res['family'])}; on >= {MIN_GAMES} games {len(ex)}; MDE median {np.median(ex):.4f} "
        f"min {min(ex):.4f} max {max(ex):.4f}")
    return res


# =============================================================================
# intervals with a game-block interval
# =============================================================================

def boot_mean(values, games, name, draws=None):
    """Game-block bootstrap of mean(values). Seeded from the test's own name, so
    two cells read side by side do not share draws."""
    draws = BOOT if draws is None else draws
    values = np.asarray(values, float)
    n = len(values)
    gid = {g: k for k, g in enumerate(sorted(set(games)))}
    G = len(gid)
    r = {"est": float(values.mean()) if n else None, "lo": None, "hi": None, "se": None,
         "games": G, "n": n}
    if G < 2 or not n:
        return r
    s, c = np.zeros(G), np.zeros(G)
    for v, g in zip(values, games):
        s[gid[g]] += v
        c[gid[g]] += 1
    rng = np.random.default_rng(SEED + zlib.crc32(name.encode()))
    picks = rng.multinomial(G, np.full(G, 1.0 / G), size=draws)
    vals = (picks @ s) / (picks @ c)
    r.update(lo=float(np.percentile(vals, 2.5)), hi=float(np.percentile(vals, 97.5)),
             se=float(vals.std()))
    return r


def pval(r):
    """z = est / bootstrap SE. A zero-variance interval (to a tolerance: the sd of
    identical floats is ~1e-17, not 0), one on < 5 games, or an absent one is p = 1."""
    if r["se"] is None or r["se"] <= TOL or r["games"] < MIN_GAMES or r["est"] is None:
        return 1.0
    return float(2 * norm.sf(abs(r["est"] / r["se"])))


def holm(pvals, alpha=None):
    alpha = HOLM_ALPHA if alpha is None else alpha
    idx = sorted(range(len(pvals)), key=lambda i: pvals[i])
    keep = set()
    for rank, i in enumerate(idx):
        if pvals[i] > alpha / (len(pvals) - rank):
            break
        keep.add(i)
    return keep


def calibration_verdict(survivors):
    """survivors: [(|estimate|, pre-run MDE or None)] for the BH survivors.
    -> exactly one of three sentences."""
    if not survivors:
        return "calibrated at this resolution"
    if any(m is not None and abs(e) > m for e, m in survivors):
        return "mispriced"
    return "a deviation survives correction below its MDE"


def precheck_reading(r, direction):
    """direction: the sign stated in the pre-registration."""
    if r["lo"] is None or r["games"] < MIN_GAMES:
        return "not read"
    if (direction < 0 and r["hi"] < 0) or (direction > 0 and r["lo"] > 0):
        return "confirmed in direction"
    if (direction < 0 and r["lo"] > 0) or (direction > 0 and r["hi"] < 0):
        return "CONTRADICTED"
    return "not detected"


def fmt(r, scale=100.0, d=2):
    if r is None or r.get("est") is None:
        return "n/a"
    if r["lo"] is None:
        return f"{r['est'] * scale:+.{d}f} [no interval] (n {r['n']}, {r['games']} games)"
    star = "*" if (r["lo"] > 0 or r["hi"] < 0) else " "
    read = "" if r["games"] >= MIN_GAMES else "  NOT READ: <5 games"
    return (f"{r['est'] * scale:+.{d}f} [{r['lo'] * scale:+.{d}f}, {r['hi'] * scale:+.{d}f}]{star}"
            f" (n {r['n']}, {r['games']} games){read}")


# =============================================================================
# Answer 1 - coherence
# =============================================================================

def unimodal(masses):
    """True if the sequence never rises again after a strict fall."""
    fell = False
    for a, b in zip(masses, masses[1:]):
        if b < a - TOL:
            fell = True
        elif b > a + TOL and fell:
            return False
    return True


def coherence(ladders, ivs, out):
    out("\n== Answer 1 - coherence (counts) ==")
    res = {"by_width": {}, "by_bin": {}}
    crossed_fee = Counter()
    for v in ivs:
        if v["buy_touch"] < -TOL:                       # C3: ask_i < bid_j
            l = ladders[v["lad"]]
            a, b = l["rungs"][v["i"]], l["rungs"][v["j"]]
            da, db = a["depth"].get("buy_yes"), b["depth"].get("buy_no")
            ea = le.rung_exec(a["k"], da.get("100") if da else None, 100, a["mid"], "yes") if da else None
            eb = le.rung_exec(b["k"], db.get("100") if db else None, 100, b["mid"], "no") if db else None
            if ea is None or eb is None:
                crossed_fee["no depth at 100 on a leg"] += 1
            else:
                crossed_fee["survives the fee at 100" if ea["allin"] + eb["allin"] < 1 - TOL
                            else "does not survive the fee at 100"] += 1

    def row(sel):
        n = len(sel)
        if not n:
            return None
        band = np.array([v["buy_touch"] - v["sell_touch"] for v in sel])
        return {"n": n, "games": len({v["game"] for v in sel}),
                "C1_negative": sum(v["p"] < -TOL for v in sel),
                "C2_zero": sum(abs(v["p"]) <= TOL for v in sel),
                "C3_crossed_at_touch": sum(v["buy_touch"] < -TOL for v in sel),
                "C4_sell_touch_le_0": sum(v["sell_touch"] <= TOL for v in sel),
                "C4_band_c_p50": float(100 * np.median(band)),
                "C4_band_c_p90": float(100 * np.percentile(band, 90)),
                "implied_mean": float(np.mean([v["p"] for v in sel]))}

    for stat in WIDTHS:
        ws = sorted({v["w"] for v in ivs if v["stat"] == stat})
        for w in ws:
            e = row([v for v in ivs if v["stat"] == stat and v["w"] == w])
            res["by_width"][f"{stat}|w={w:g}"] = e
            out(f"  {stat:<13} w={w:<4g} n {e['n']:>5}  negative {e['C1_negative']:>3}  zero "
                f"{e['C2_zero']:>3}  crossed at touch {e['C3_crossed_at_touch']:>3}  sell-touch<=0 "
                f"{e['C4_sell_touch_le_0']:>4} ({e['C4_sell_touch_le_0'] / e['n']:.3f})  band p50 "
                f"{e['C4_band_c_p50']:.1f}c p90 {e['C4_band_c_p90']:.1f}c  mean implied {e['implied_mean']:.4f}")
        for b, name in enumerate(BIN_NAMES):
            e = row([v for v in ivs if v["stat"] == stat and v["bin"] == b])
            if e:
                res["by_bin"][f"{stat}|{name}"] = e
                out(f"  {stat:<13} mass {name:<9} n {e['n']:>5}  negative {e['C1_negative']:>3}  zero "
                    f"{e['C2_zero']:>3}  crossed {e['C3_crossed_at_touch']:>3}  sell-touch<=0 "
                    f"{e['C4_sell_touch_le_0']:>4} ({e['C4_sell_touch_le_0'] / e['n']:.3f})  band p50 "
                    f"{e['C4_band_c_p50']:.1f}c")
    tot = row(ivs)
    res["total"] = tot
    res["crossed_fee"] = dict(crossed_fee)
    lad_any = len({v["lad"] for v in ivs if v["p"] < -TOL})
    multi = [l for l in ladders if len(l["rungs"]) >= 2]
    res["ladders_with_2_rungs"] = len(multi)
    res["ladders_with_a_negative_interval"] = lad_any
    out(f"  TOTAL intervals {tot['n']} on {len(multi)} ladders with >= 2 rungs, {tot['games']} games: "
        f"negative {tot['C1_negative']} (on {lad_any} ladders), zero {tot['C2_zero']}, crossed at the touch "
        f"{tot['C3_crossed_at_touch']} {dict(crossed_fee)}, sell-touch <= 0 {tot['C4_sell_touch_le_0']} "
        f"({tot['C4_sell_touch_le_0'] / tot['n']:.3f})")
    rec = [l for l in multi if l["stat"] == "receptions" and len(l["rungs"]) >= 4]
    non = 0
    for l in rec:
        m = [l["raw"][i] - l["raw"][i + 1] for i in range(len(l["raw"]) - 1)
             if abs(l["lines"][i + 1] - l["lines"][i] - 1.0) < TOL]
        non += not unimodal(m)
    res["C5"] = {"receptions_ladders_4plus_rungs": len(rec), "not_unimodal": non}
    out(f"  C5 receptions ladders with >= 4 rungs: {len(rec)}; unit masses between rungs NOT unimodal: "
        f"{non} ({non / max(len(rec), 1):.3f})")
    return res


# =============================================================================
# Answer 3 - the second leg's cost (defined before Answer 2, which prints it)
# =============================================================================

def pair_cost(ladder, i, j, size, action):
    """Two legs at the executable VWAP plus the taker fee. action 'buy': YES on the
    lower rung, NO on the upper. 'sell': NO on the lower, YES on the upper.
    -> dict or None when a leg has no depth at the size."""
    key = str(size)
    a, b = ladder["rungs"][i], ladder["rungs"][j]
    sa, sb = ("yes", "no") if action == "buy" else ("no", "yes")
    legs = []
    for r, side in ((a, sa), (b, sb)):
        d = r["depth"].get("buy_yes" if side == "yes" else "buy_no")
        ex = le.rung_exec(r["k"], d.get(key) if d else None, size, r["mid"], side) if d else None
        if ex is None:
            return None
        legs.append(dict(ex, touch_size=d["size"] or 0))
    return {"cost": legs[0]["cost"] + legs[1]["cost"], "fee": legs[0]["fee"] + legs[1]["fee"],
            "capital": legs[0]["allin"] + legs[1]["allin"], "lower_leg": legs[0]["cost"],
            "upper_leg": legs[1]["cost"], "thin": min(legs[0]["touch_size"], legs[1]["touch_size"])}


def nearest_single(ladder, value, size):
    """The one rung-side with depth at the size whose own mid is nearest `value`.
    -> rung_exec dict plus side_mid, or None."""
    key, best = str(size), None
    for r in ladder["rungs"]:
        for side, dside in (("yes", "buy_yes"), ("no", "buy_no")):
            d = r["depth"].get(dside)
            ex = le.rung_exec(r["k"], d.get(key) if d else None, size, r["mid"], side) if d else None
            if ex is None:
                continue
            sm = r["k"] if side == "yes" else 1 - r["k"]
            if best is None or abs(sm - value) < abs(best["side_mid"] - value):
                best = dict(ex, side_mid=sm)
    return best


def break_even(value, cost):
    if not (1e-6 < value < 1 - 1e-6) or not (1e-6 < value + cost < 1 - 1e-6):
        return None
    return float(norm.ppf(value + cost) - norm.ppf(value))


def priced_pairs(ladders, ivs, size, action):
    rows = []
    for v in ivs:
        l = ladders[v["lad"]]
        pc = pair_cost(l, v["i"], v["j"], size, action)
        if pc is None:
            continue
        value = v["p"] if action == "buy" else 1 - v["p"]
        sg = nearest_single(l, value, size)
        pay = None if v["h"] is None else ((1 + v["h"]) if action == "buy" else (1 - v["h"]))
        rows.append(dict(pc, iv=v, value=value, single=sg["cost"], single_mid=sg["side_mid"],
                         be=break_even(value, pc["cost"]), single_be=sg["be"],
                         net=None if pay is None else pay - pc["capital"]))
    return rows


def cost_row(sel, name):
    c = np.array([r["cost"] for r in sel])
    s = np.array([r["single"] for r in sel])
    games = [r["iv"]["game"] for r in sel]
    be = [r["be"] for r in sel if r["be"] is not None]
    val = np.array([r["value"] for r in sel])
    return {"n": len(sel), "games": len(set(games)),
            "value_mean": float(val.mean()),
            "cost_c_mean": float(100 * c.mean()), "cost_c_p50": float(100 * np.median(c)),
            "fee_c_mean": float(100 * np.mean([r["fee"] for r in sel])),
            "lower_leg_c": float(100 * np.mean([r["lower_leg"] for r in sel])),
            "upper_leg_c": float(100 * np.mean([r["upper_leg"] for r in sel])),
            "single_c_mean": float(100 * s.mean()), "single_c_p50": float(100 * np.median(s)),
            "single_mid_gap_mean": float(np.mean([abs(r["single_mid"] - r["value"]) for r in sel])),
            "diff": boot_mean(c - s, games, name),
            "cost_share_of_value": float(c.sum() / val.sum()) if val.sum() > 0 else None,
            "be_p50": float(np.median(be)) if be else None, "be_undefined": len(sel) - len(be),
            "single_be_p50": float(np.median([r["single_be"] for r in sel])),
            "capital_mean": float(np.mean([r["capital"] for r in sel])),
            "thin_touch_p50": float(np.median([r["thin"] for r in sel]))}


def cost(ladders, ivs, out):
    out("\n== Answer 3 - what the second leg costs (executable VWAP + taker fee; no verdict) ==")
    res = {}
    for size in SIZES:
        for action in ("buy", "sell"):
            rows = priced_pairs(ladders, ivs, size, action)
            games = {r["iv"]["game"] for r in rows}
            key = f"{size}|{action}"
            res[key] = {"pairs": len(rows), "games": len(games), "no_depth": len(ivs) - len(rows),
                        "by_width": {}, "by_bin": {}}
            out(f"  -- {action.upper()} the interval, {size} contracts a leg: pairs with depth on both legs "
                f"{len(rows)} of {len(ivs)}, games {len(games)}")
            if len(games) < MIN_GAMES:
                out("     NOT READ: <5 games (stop rule)")
                res[key]["read"] = False
                continue
            res[key]["read"] = True

            def show(label, e):
                out(f"     {label:<26} n {e['n']:>4} g {e['games']:>2}  value {e['value_mean']:.3f}  two-leg "
                    f"{e['cost_c_mean']:.2f}c (p50 {e['cost_c_p50']:.2f}, fee {e['fee_c_mean']:.2f}; lower "
                    f"{e['lower_leg_c']:.2f} + upper {e['upper_leg_c']:.2f})  single {e['single_c_mean']:.2f}c"
                    f"  diff c {fmt(e['diff'])}")
                out(f"     {'':<26} share of value {100 * e['cost_share_of_value']:.1f}%  break-even probit "
                    f"p50 {e['be_p50'] if e['be_p50'] is None else round(e['be_p50'], 3)} vs single "
                    f"{e['single_be_p50']:.3f}  capital {e['capital_mean']:.3f}  thinner-leg touch p50 "
                    f"{e['thin_touch_p50']:.0f}  single's mid off by {e['single_mid_gap_mean']:.3f}")
            for stat, ws in WIDTHS.items():
                for w in ws:
                    sel = [r for r in rows if r["iv"]["stat"] == stat and r["iv"]["w"] == w]
                    if len(sel) >= 10:
                        e = cost_row(sel, f"cost|{key}|{stat}|{w}")
                        res[key]["by_width"][f"{stat}|w={w}"] = e
                        show(f"{stat} w={w}", e)
                for b, name in enumerate(BIN_NAMES):
                    sel = [r for r in rows if r["iv"]["stat"] == stat and r["iv"]["bin"] == b
                           and r["iv"]["w"] in ws]
                    if len(sel) >= 10:
                        e = cost_row(sel, f"cost|{key}|{stat}|bin{b}")
                        res[key]["by_bin"][f"{stat}|{name}"] = e
                        show(f"{stat} mass {name}", e)
            sel = [r for r in rows if r["iv"]["w"] in WIDTHS[r["iv"]["stat"]]]
            res[key]["all_tested_widths"] = cost_row(sel, f"cost|{key}|all")
            show("ALL tested widths", res[key]["all_tested_widths"])
    return res


def survivor_cost_line(ladders, ivs, idx, est, name, out):
    """The cost line printed beside any BH survivor: bought if realised > implied,
    sold otherwise, 100 contracts a leg."""
    action = "buy" if est > 0 else "sell"
    rows = priced_pairs(ladders, [ivs[n] for n in idx], 100, action)
    if not rows:
        out(f"    COST LINE {name}: no interval in the cell has depth on both legs")
        return {"action": action, "pairs": 0}
    net = boot_mean([r["net"] for r in rows], [r["iv"]["game"] for r in rows], f"net|{name}")
    e = {"action": action, "pairs": len(rows), "games": net["games"],
         "cost_c_mean": float(100 * np.mean([r["cost"] for r in rows])),
         "gap_pp": float(100 * abs(est)), "realised_net": net,
         "thin_touch_p50": float(np.median([r["thin"] for r in rows]))}
    out(f"    COST LINE {name}: {action} the interval, 100 a leg, {len(rows)} of {len(idx)} with depth; "
        f"two-leg cost {e['cost_c_mean']:.2f}c against a {e['gap_pp']:.2f}pp gap at the mid; thinner-leg "
        f"touch p50 {e['thin_touch_p50']:.0f}; realised net pp {fmt(net)}")
    return e


# =============================================================================
# Answer 2 - calibration, and the pre-check
# =============================================================================

def calibration(ladders, ivs, mde, out):
    out("\n== Answer 2 - calibration of the interval against settlement ==")
    fam, pre = test_cells(ivs)
    h = np.array([v["h"] for v in ivs], float)
    p = np.array([v["p"] for v in ivs], float)
    games = [v["game"] for v in ivs]
    res = {"cells": {}, "precheck": {}}

    def run(name, idx):
        r = boot_mean(h[idx] - p[idx], [games[n] for n in idx], name)
        r.update(implied=float(p[idx].mean()) if len(idx) else None,
                 realised=float(h[idx].mean()) if len(idx) else None,
                 ladders=len({ivs[n]["lad"] for n in idx}))
        return r

    names = list(fam)
    tests = [run(n, fam[n]) for n in names]
    exist = [k for k, r in enumerate(tests) if r["n"]]
    readable = [k for k in exist if tests[k]["games"] >= MIN_GAMES and tests[k]["se"]]
    ps = [pval(tests[k]) for k in exist]
    keep = {exist[k] for k in le.bh(ps, BH_Q)}
    keep_holm = {exist[k] for k in holm(ps)}
    out(f"  registered cells {len(names)}; exist {len(exist)}; on >= {MIN_GAMES} games with an interval "
        f"{len(readable)}; nominal p<0.05 {sum(pv < 0.05 for pv in ps)} (expected by chance "
        f"{0.05 * len(readable):.1f}); BH q={BH_Q} survivors {len(keep)}; Holm {HOLM_ALPHA} survivors "
        f"{len(keep_holm)}")
    for k in exist:
        n, r = names[k], tests[k]
        m = (mde.get(n) or {}).get("mde")
        r["mde_prerun"] = m
        r["mde_bootstrap"] = None if r["se"] is None else 2.8 * r["se"]
        r["p"] = pval(r)
        res["cells"][n] = r
        out(f"  {n:<34} implied {r['implied']:.4f} realised {r['realised']:.4f}  diff pp {fmt(r)}  "
            f"MDE pre-run {100 * m if m is not None else float('nan'):.2f} / bootstrap "
            f"{100 * r['mde_bootstrap'] if r['mde_bootstrap'] else float('nan'):.2f}"
            f"{'  BH' if k in keep else ''}{'  HOLM' if k in keep_holm else ''}")
    surv = [(names[k], tests[k]) for k in sorted(keep)]
    verdict = calibration_verdict([(r["est"], r["mde_prerun"]) for _n, r in surv])
    res["survivors"] = []
    for n, r in surv:
        e = {"name": n, "est": r["est"], "lo": r["lo"], "hi": r["hi"], "mde_prerun": r["mde_prerun"],
             "exceeds_mde": r["mde_prerun"] is not None and abs(r["est"]) > r["mde_prerun"],
             "sign": "realised above implied" if r["est"] > 0 else "realised below implied"}
        out(f"  SURVIVOR {n}: {fmt(r)}  pre-run MDE {100 * (r['mde_prerun'] or float('nan')):.2f}pp  "
            f"exceeds it: {e['exceeds_mde']}  ({e['sign']})")
        e["cost_line"] = survivor_cost_line(ladders, ivs, fam[n], r["est"], n, out)
        res["survivors"].append(e)
    mdes = [tests[k]["mde_prerun"] for k in readable if tests[k]["mde_prerun"] is not None]
    res.update(n_registered=len(names), n_exist=len(exist), n_readable=len(readable),
               n_nominal=int(sum(pv < 0.05 for pv in ps)), n_bh=len(keep), n_holm=len(keep_holm),
               verdict=verdict, mde_prerun_median=float(np.median(mdes)), mde_prerun_max=float(max(mdes)),
               mde_prerun_min=float(min(mdes)))
    out(f"  VERDICT, calibration (pre-registered rule): {verdict}  [pre-run MDE over the readable cells: "
        f"median {100 * res['mde_prerun_median']:.2f}pp, min {100 * res['mde_prerun_min']:.2f}, max "
        f"{100 * res['mde_prerun_max']:.2f}]")
    ratio = [tests[k]["mde_bootstrap"] / tests[k]["mde_prerun"] for k in readable if tests[k]["mde_prerun"]]
    res["bootstrap_over_prerun_mde_p50"] = float(np.median(ratio))
    out(f"  bootstrap SE over the pre-run (independent-ladder) SE: p50 {np.median(ratio):.2f}, "
        f"p90 {np.percentile(ratio, 90):.2f}")

    out("\n== The 2026 pre-check: stated direction against the result ==")
    pn = list(pre)
    pt = [run("pre|" + n, pre[n]) for n in pn]
    pk = le.bh([pval(r) for r in pt], BH_Q)
    for k, (n, r) in enumerate(zip(pn, pt)):
        d = PRE_DIRECTION[n]
        m = (mde.get("pre|" + n) or {}).get("mde")
        r.update(direction=d, reading=precheck_reading(r, d) if r["n"] else "no interval in the cell",
                 mde_prerun=m, bh=k in pk)
        res["precheck"][n] = r
        if r["n"]:
            out(f"  {n:<6} predicted {'< 0' if d < 0 else '> 0'}  implied {r['implied']:.4f} realised "
                f"{r['realised']:.4f}  diff pp {fmt(r)}  pre-run MDE "
                f"{100 * m if m is not None else float('nan'):.2f}  -> {r['reading']}{'  BH' if k in pk else ''}")
        else:
            out(f"  {n:<6} no interval in the cell")
    res["precheck_reading"] = {"straddle": res["precheck"]["S_all"]["reading"],
                               "upper_wing": res["precheck"]["U"]["reading"]}
    out(f"  READING (pre-registered): straddling intervals over-priced - {res['precheck_reading']['straddle']}; "
        f"upper-wing intervals under-priced - {res['precheck_reading']['upper_wing']}")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", required=True, help="c-35's extract cache; scratch, never committed")
    ap.add_argument("--mde", action="store_true", help="outcome-free MDE table only")
    ap.add_argument("--mde-in", help="the committed pre-run MDE file")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    out = print
    rows, meta = load(a.cache, outcomes=not a.mde)
    ladders = build(rows)
    shape = check_shape(ladders)
    out(f"ladders {shape[0]}; rungs {shape[1]}; games {shape[2]}; fees {meta['fees']}")
    ivs = intervals(ladders)
    out(f"intervals {len(ivs)} on {len({v['lad'] for v in ivs})} ladders; by position "
        f"{dict(Counter(v['pos'] for v in ivs))}")
    if a.mde:
        if any(k in r for r in rows for k in OUTCOME_KEYS + (MODEL_KEY,)):
            raise SystemExit("an outcome or model column survived the load - refusing")
        result = {"shape": shape, "sims": SIMS, "seed": SEED, "note": "no outcome read",
                  **mde_table(ladders, ivs, out)}
    else:
        if not a.mde_in:
            raise SystemExit("--mde-in is required for the outcome run: the MDE is written first")
        with open(a.mde_in) as f:
            mj = json.load(f)
        if tuple(mj["shape"]) != shape:
            raise SystemExit("the MDE file was computed on a different population")
        mde = dict(mj["family"])
        mde.update({"pre|" + k: v for k, v in mj["precheck"].items()})
        result = {"shape": shape, "intervals": len(ivs), "specifications_tried": 1,
                  "coherence": coherence(ladders, ivs, out),
                  "calibration": calibration(ladders, ivs, mde, out),
                  "cost": cost(ladders, ivs, out)}
    if a.json_out:
        with open(a.json_out, "w") as f:
            json.dump(result, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
