"""c-42 - the counterfactual ledger, re-measured in units of each input's own as-of uncertainty.

    python -m research.counterfactual_uncertainty --rows research/results/counterfactual_ledger_rows.json
        --facts-db <c-40's scratch copy of the facts tables> --cache-dir <scratch>
        --build-cache --shard 0/3          # the slow part: rebuild each lean's components
    python -m research.counterfactual_uncertainty --rows ... --cache-dir <scratch>
        --results-dir research/results     # distances, shares, tests - no store opened

PRE-REGISTRATION: docs/C42-uncertainty-scale-preregistration.md, committed at b69b376
BEFORE this script existed. This file implements it and does not extend it: three
inputs with an as-of standard error (own_mean, group_mean, dispersion), `weight`
and `line` excluded by name, the distance |x' - x| / SE, T1 against 1/3, T2 missed
against cleared, c-40's eight slices, 48 tests in one Holm family, ONE specification.

ONLY THE RULER CHANGES. The population is c-40's 477 rows; the model's arithmetic,
the flip target, the allowed ranges and the scan are imported from
`research.counterfactual_ledger`; `models/` is not edited. Every lean's rebuilt
components are asserted equal to the values c-40 recorded before anything is
measured, and each input's nearest-in-log-ratio flip is asserted equal to c-40's.

`--facts-db` is opened mode=ro. The live store is never opened by this script.
"""
import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter

import numpy as np
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import baseline, features  # noqa: E402
from research import counterfactual_ledger as CL  # noqa: E402

INPUTS = ("own_mean", "group_mean", "dispersion")     # the three with an as-of SE (prereg 3)
EXCLUDED = {"weight": "no fit yields a standard error for it: n is exact and the constants "
                      "are judgment calls",
            "line": "a posted line is exact; one listed line across the benchmark books on "
                    "409 of 477 leans"}
K = len(INPUTS)
N_ROWS = 477
LEDGER_SHA = "760b93be22fb1464df139457b011a67687ba1c70a013ba06f8e55a90d225306d"
MATCH_TOL = 1e-9              # rebuilt components against c-40's recorded as_of values
FLIP_TOL = 1e-6               # nearest-in-log flip against c-40's recorded distance
SE_BOOT = 1000                # draws for the dispersion's standard error
BOOT, SEED = 2000, 42
MIN_BLOCKS = 5
ALPHA = 0.05
MDE_MULT = 2.80
ZERO_SE = 1e-12
AT_MDE = 1.25
SE_CAPS = (1.0, 2.0, 3.0, 5.0)
SLICE_NAMES = ("all", "market=receptions", "market=rush_attempts", "side=over", "side=under",
               "band=4-6", "band=6-8", "band=8+")
REGISTERED_MDE = {"T1": {"share 1/3": 0.136, "share 0.5 (worst)": 0.144, "share 0.8": 0.115,
                         "share 0.9": 0.086},
                  "T2": {"worst case": 0.128}}


class FrozenError(AssertionError):
    """The population or a rebuilt fit is not c-40's."""


# =============================================================================
# the slow part: components, the player's games, the group's rows
# =============================================================================

def player_games(con, gsis, stat, as_of_ts, seasons):
    """The per-game values `features.player_prior` averaged - same join, same filter."""
    col = features.STAT_COLUMN[stat]
    yrs = ",".join(str(int(x)) for x in seasons)
    q = (f"SELECT pw.{col} " + features._ASOF_JOIN +
         f" AND pw.gsis_id = :gid AND pw.season IN ({yrs}) AND pw.{col} IS NOT NULL")
    return [float(r[0]) for r in con.execute(q, {"as_of": as_of_ts, "gid": gsis})]


def group_rows(con, pos, stat, as_of_ts, seasons, role, cap=4, min_games=4):
    """The (mean, within-variance) rows `features.positional_prior` averaged.
    `role=None` is the widened, position-only group."""
    q = features._ranked_cte(features.STAT_COLUMN[stat], seasons) + """
        SELECT m, MAX(msq - m * m, 0) FROM ranked WHERE position = :pos AND n >= :ming"""
    args = {"as_of": as_of_ts, "pos": (pos or "").upper(), "ming": min_games, "cap": cap}
    if role is not None:
        q += " AND MIN(rnk, :cap) = :role"
        args["role"] = role
    return [(float(m), float(v)) for m, v in con.execute(q, args)]


def build_cache(a):
    with open(a.rows, encoding="utf-8") as fh:
        rows = json.load(fh)["rows"]
    i, m = (int(x) for x in a.shard.split("/"))
    con = sqlite3.connect(f"file:{a.facts_db}?mode=ro", uri=True)
    CL.cached_features()
    gcache, out, t0 = {}, {}, time.time()
    mine = [r for n, r in enumerate(rows) if n % m == i]
    for n, r in enumerate(mine):
        as_of = CL.parse_iso(r["read_at"])
        seasons = (r["season"] - 1, r["season"])
        c = CL.components(con, r["gsis_id"], r["market"], r["season"], as_of, r["pos"], r["team"])
        pos = (r["pos"] or "").upper()
        widened = features.positional_prior(con, pos, r["market"], as_of, seasons,
                                            role=c["role"])["n_players"] < 3
        gkey = (pos, r["market"], as_of, None if widened else c["role"])
        if gkey not in gcache:
            gcache[gkey] = group_rows(con, pos, r["market"], as_of, seasons, gkey[3])
        out[r["lean_id"]] = {"c": c, "games": player_games(con, r["gsis_id"], r["market"],
                                                           as_of, seasons),
                             "group": gcache[gkey], "widened": widened}
        if (n + 1) % 25 == 0:
            print(f"  shard {a.shard}: {n + 1}/{len(mine)}  {time.time() - t0:.0f}s", flush=True)
    con.close()
    os.makedirs(a.cache_dir, exist_ok=True)
    path = os.path.join(a.cache_dir, f"components-{i}of{m}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print(f"shard {a.shard}: wrote {len(out)} leans to {path} in {time.time() - t0:.0f}s")
    return 0


def load_cache(cache_dir):
    out = {}
    for fn in sorted(os.listdir(cache_dir)):
        if fn.startswith("components-") and fn.endswith(".json"):
            with open(os.path.join(cache_dir, fn), encoding="utf-8") as fh:
                out.update(json.load(fh))
    return out


# =============================================================================
# the frozen-population gate
# =============================================================================

def assert_frozen(row, entry):
    """Raise unless the rebuilt fit IS the one c-40 analysed, and the extra rows
    this unit reads are the rows the fit's own numbers were built from."""
    c, games, grp = entry["c"], entry["games"], entry["group"]
    rec, bad = row["as_of"], []
    for name, got in (("own_mean", c["own_mean"]), ("group_mean", c["group_mean"]),
                      ("weight", c["weight"]), ("dispersion", CL.replica(c)[2])):
        if abs(got - rec[name]) > MATCH_TOL:
            bad.append(f"{name} {got} vs recorded {rec[name]}")
    if c["n"] != rec["prior_games"] or len(games) != c["n"]:
        bad.append(f"games {len(games)} / n {c['n']} vs recorded {rec['prior_games']}")
    if games:
        g = np.asarray(games)
        if abs(g.mean() - c["own_mean"]) > MATCH_TOL or abs(g.var() - c["own_var"]) > 1e-7:
            bad.append("the player's games do not reproduce the fit's own mean and variance")
    if c["no_group"]:
        if grp:
            bad.append("the model had no group but rows were read")
    else:
        m = np.asarray([x[0] for x in grp])
        v = np.asarray([x[1] for x in grp])
        if (not len(m) or abs(m.mean() - c["group_mean"]) > MATCH_TOL
                or abs(v.mean() - c["group_var"]) > 1e-7):
            bad.append("the group's rows do not reproduce the fit's group mean and variance")
    if bad:
        raise FrozenError(f"{row['lean_id']}: " + "; ".join(bad))


# =============================================================================
# the standard errors (prereg 3)
# =============================================================================

def se_own_mean(games):
    """sqrt(s^2 / n), s^2 the sample variance. None when n < 2 or s^2 == 0."""
    n = len(games)
    if n < 2:
        return None
    s2 = float(np.var(games, ddof=1))
    return math.sqrt(s2 / n) if s2 > 0 else None


def se_group_mean(c, grp):
    """sqrt(v / N) over the per-player means the model averaged."""
    if c["no_group"] or len(grp) < 2:
        return None
    v = float(np.var([x[0] for x in grp], ddof=1))
    return math.sqrt(v / len(grp)) if v > 0 else None


def lean_seed(lean_id):
    return int.from_bytes(hashlib.sha256(lean_id.encode()).digest()[:8], "big")


def se_dispersion(c, games, grp, lean_id, draws=SE_BOOT):
    """Bootstrap sd of the model's total predictive variance: the player's games
    and (independently) the group's rows resampled, weight / n / constants held."""
    n = len(games)
    if n < 2:
        return None
    rng = np.random.default_rng(lean_seed(lean_id))
    g = np.asarray(games, dtype=float)
    gm = np.asarray([x[0] for x in grp], dtype=float)
    gv = np.asarray([x[1] for x in grp], dtype=float)
    gi = rng.integers(0, n, size=(draws, n))
    pi = rng.integers(0, len(gm), size=(draws, len(gm))) if len(gm) and not c["no_group"] else None
    tot = np.empty(draws)
    for d in range(draws):
        x = g[gi[d]]
        cc = dict(c, own_mean=float(x.mean()), own_var=float(x.var()))
        if pi is not None:
            cc["group_mean"] = float(gm[pi[d]].mean())
            cc["group_var"] = float(gv[pi[d]].mean())
        tot[d] = CL.replica(cc)[2]
    sd = float(np.std(tot, ddof=1))
    return sd if sd > ZERO_SE else None


# =============================================================================
# the distance in standard errors (prereg 4)
# =============================================================================

def nearest_in_se(x, se, crossings):
    """`crossings`: [(log distance, sign)] per direction that reaches the flip.
    -> {"value", "distance" (|x' - x| / se), "log_distance", "direction"} for the
    direction nearest IN STANDARD ERRORS, or None."""
    best = None
    for u, sign in crossings:
        value = x * math.exp(sign * u)
        d = abs(value - x) / se
        if best is None or d < best["distance"]:
            best = {"value": value, "distance": d, "log_distance": u,
                    "direction": "up" if sign > 0 else "down"}
    return best


def se_perturbations(c, line, side, mkt, T, ses):
    """{input: nearest-in-SE flip or None}, plus the per-input smallest LOG
    distance (the number c-40 recorded, for the only-the-ruler-changed check)."""
    lf = math.log(CL.FACTOR)

    def ok(**kw):
        return CL.reached(CL.p_over(c, line, **kw), side, mkt, T, "flip")

    def both(f):
        got = [CL._scan(f, lf, 0.0), CL._scan(f, 0.0, lf)]
        return [g for g in got if g is not None]

    cross = {}
    x = c["own_mean"]
    cross["own_mean"] = (x, both(lambda u: ok(own_mean=x * math.exp(u)))
                         if x > 0 and c["weight"] > 0 else [])
    g = c["group_mean"]
    cross["group_mean"] = (g, both(lambda u: ok(group_mean=g * math.exp(u)))
                           if g > 0 and not c["no_group"] else [])
    total = CL.replica(c)[2]
    cross["dispersion"] = (total, both(lambda u: ok(var_scale=math.exp(u))))
    out, log_min = {}, {}
    for i in INPUTS:
        base, cr = cross[i]
        log_min[i] = min((u for u, _ in cr), default=None)
        out[i] = nearest_in_se(base, ses[i], cr) if ses[i] is not None and cr else None
    return out, log_min


def flipping(dist_by_input):
    """The input with the smallest distance; ties to the earlier of INPUTS."""
    cand = [(d, n, i) for n, i in enumerate(INPUTS)
            if (d := dist_by_input.get(i)) is not None]
    return min(cand)[2] if cand else None


# =============================================================================
# shares, tests, verdict
# =============================================================================

def shares(rows, key):
    have = [r for r in rows if r[key] is not None]
    cnt = Counter(r[key] for r in have)
    n = len(have)
    return n, {i: (cnt[i] / n if n else float("nan")) for i in INPUTS}, cnt


def slice_tests(name, rows, rng, key="flip_se"):
    """T1 (share - 1/K) and T2 (missed - cleared) per input, game-block bootstrap.
    c-40's `slice_tests` with the input set and the row key as parameters."""
    missed = [r for r in rows if r["result"] == "missed"]
    cleared = [r for r in rows if r["result"] == "cleared"]
    n_m, s_m, cnt_m = shares(missed, key)
    n_c, s_c, cnt_c = shares(cleared, key)
    games = sorted({r["game_id"] for r in rows})
    gi = {g: n for n, g in enumerate(games)}
    cm, cc = np.zeros((len(games), K)), np.zeros((len(games), K))
    for r in rows:
        if r[key] is None:
            continue
        (cm if r["result"] == "missed" else cc)[gi[r["game_id"]], INPUTS.index(r[key])] += 1
    draws = rng.integers(0, len(games), size=(BOOT, len(games))) if games else np.zeros((0, 0), int)
    bm, bc = cm[draws].sum(axis=1), cc[draws].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        sm = bm / bm.sum(axis=1, keepdims=True)
        sc = bc / bc.sum(axis=1, keepdims=True)
    tests = []
    for k, i in enumerate(INPUTS):
        for kind, est, boot in (("T1", s_m[i] - 1.0 / K, sm[:, k] - 1.0 / K),
                                ("T2", s_m[i] - s_c[i], sm[:, k] - sc[:, k])):
            b = boot[np.isfinite(boot)]
            t = {"slice": name, "test": kind, "input": i, "estimate": est,
                 "n_missed": n_m, "n_cleared": n_c, "n_games": len(games),
                 "draws_used": int(len(b))}
            if len(games) < MIN_BLOCKS or len(b) < BOOT // 2 or not math.isfinite(est):
                t.update(lo=None, hi=None, se=None, p=1.0, mde=None, ratio_to_mde=None,
                         note="fewer than 5 games or an empty arm: p = 1")
            else:
                se = float(np.std(b, ddof=1))
                lo, hi = (float(v) for v in np.percentile(b, [2.5, 97.5]))
                flat = se < ZERO_SE
                t.update(lo=lo, hi=hi, se=0.0 if flat else se,
                         p=1.0 if flat else float(2 * sps.norm.sf(abs(est) / se)),
                         mde=None if flat else MDE_MULT * se,
                         ratio_to_mde=None if flat else abs(est) / (MDE_MULT * se),
                         note="zero-variance bootstrap: p = 1" if flat else None)
            tests.append(t)
    summary = {"slice": name, "n_rows": len(rows), "n_games": len(games),
               "missed": {"n": len(missed), "n_with_flip": n_m, "share": s_m,
                          "count": {i: cnt_m[i] for i in INPUTS}},
               "cleared": {"n": len(cleared), "n_with_flip": n_c, "share": s_c,
                           "count": {i: cnt_c[i] for i in INPUTS}}}
    return summary, tests


def first_input(share):
    """Largest share; ties to the earlier of INPUTS. None if no share is finite."""
    cand = [(-share[i], n, i) for n, i in enumerate(INPUTS) if math.isfinite(share[i])]
    return min(cand)[2] if cand else None


def verdict(summary_all, tests, c40_first="own_mean"):
    """The registered rule (prereg 6), read on the all-rows slice. Three branches,
    each reachable (tests/test_counterfactual_uncertainty.py)."""
    first = first_input(summary_all["missed"]["share"])
    allr = {(t["test"], t["input"]): t for t in tests if t["slice"] == "all"}
    t1, t2 = allr[("T1", first)], allr[("T2", first)]
    above = (t1["estimate"] > 0 and t1["p_holm"] < ALPHA and t1["lo"] is not None and t1["lo"] > 0)
    if first != c40_first:
        v, words = "scale_dependent", "c-40's 66.1% was scale-dependent"
    elif not above:
        v = "same_first_input_share_not_above_null"
        words = (f"{first} ranks first on both scales but its share is not distinguishable "
                 "from 1/3 on the uncertainty scale; the answer is no")
    else:
        v, words = "same_ranking", f"{first} ranks first on both scales and above the 1/3 null"
    t2_zero = t2["lo"] is None or (t2["lo"] <= 0 <= t2["hi"])
    about = sorted((t["input"] for t in tests if t["slice"] == "all" and t["test"] == "T2"
                    and t["estimate"] > 0 and t["p_holm"] < ALPHA), key=INPUTS.index)
    return {"verdict": v, "words": words, "first_input": first, "c40_first_input": c40_first,
            "share_missed": summary_all["missed"]["share"][first],
            "share_cleared": summary_all["cleared"]["share"][first],
            "t1": t1, "t2": t2, "t2_interval_contains_zero": t2_zero,
            "cleared_statement": ("a ranking that is identical on misses and clears is not a "
                                  "diagnostic of a miss, on either scale" if t2_zero else
                                  "the first input's share differs between missed and cleared"),
            "inputs_over_represented_on_misses": about}


def reading(t):
    """The registered wording of one test against its own MDE (prereg 5)."""
    if t.get("ratio_to_mde") is None:
        return "no variance"
    sig = t["p_holm"] < ALPHA
    if sig and t["ratio_to_mde"] < AT_MDE:
        return "at its MDE"
    if sig:
        return "detected"
    return "not detected"


def ceiling(missed, key="se"):
    n = len(missed)
    dmin = [min((r[key][i]["distance"] for i in INPUTS if r[key][i] is not None), default=None)
            for r in missed]
    out = {"n_missed": n, "no_single_input_flip": sum(1 for d in dmin if d is None),
           "median_nearest_se": float(np.median([d for d in dmin if d is not None])) if n else None,
           "caps": [{"cap_se": cap, "n_reached": (k := sum(1 for d in dmin if d is not None and d <= cap)),
                     "share_reached": k / n if n else None} for cap in SE_CAPS],
           "per_input": {}}
    for i in INPUTS:
        ds = [r[key][i]["distance"] for r in missed if r[key][i] is not None]
        out["per_input"][i] = {"n_can_flip": len(ds), "n_cannot_or_no_se": n - len(ds),
                               "median_se_distance": float(np.median(ds)) if ds else None}
    return out


# =============================================================================
# main
# =============================================================================

def analyse(rows, cache):
    """Per-row SEs, SE distances and both scales' three-input flipping input."""
    out, no_se = [], {i: Counter() for i in INPUTS}
    for r in rows:
        e = cache.get(r["lean_id"])
        if e is None:
            raise FrozenError(f"{r['lean_id']}: no rebuilt components in the cache")
        assert_frozen(r, e)
        c, games, grp = e["c"], e["games"], e["group"]
        ses = {"own_mean": se_own_mean(games), "group_mean": se_group_mean(c, grp),
               "dispersion": se_dispersion(c, games, grp, r["lean_id"])}
        thr = float(r["threshold_pp"])
        pert, log_min = se_perturbations(c, r["line"], r["side"], r["mkt_p_over"], thr, ses)
        for i in INPUTS:                       # only the ruler changed: c-40's own number
            rec = r["flip"][i]
            if (rec is None) != (log_min[i] is None) or (
                    rec is not None and abs(rec["distance"] - log_min[i]) > FLIP_TOL):
                raise FrozenError(f"{r['lean_id']} {i}: nearest log distance {log_min[i]} is not "
                                  f"c-40's {rec and rec['distance']}")
            if ses[i] is None:
                why = ("no group" if i == "group_mean" and c["no_group"] else
                       "fewer than 2 prior games" if i != "group_mean" and len(games) < 2 else
                       "fewer than 2 group rows or zero variance" if i == "group_mean" else
                       "zero variance")
                no_se[i][why] += 1
        prop3 = {i: (r["flip"][i]["distance"] if r["flip"][i] is not None else None) for i in INPUTS}
        w = c["weight"]
        pred = (None if ses["own_mean"] is None or ses["group_mean"] is None
                else bool(w * ses["own_mean"] > (1.0 - w) * ses["group_mean"]))
        out.append({
            "lean_id": r["lean_id"], "game_id": r["game_id"], "gsis_id": r["gsis_id"],
            "market": r["market"], "line": r["line"], "side": r["side"], "result": r["result"],
            "band": r["band"], "gap_pp": r["gap_pp"], "prior_games": c["n"],
            "group_rows": len(grp), "group_widened": e["widened"], "weight": w,
            "as_of": {"own_mean": c["own_mean"], "group_mean": c["group_mean"],
                      "dispersion": CL.replica(c)[2]},
            "standard_error": ses,
            "relative_se": {i: (ses[i] / out_base if ses[i] is not None and out_base > 0 else None)
                            for i, out_base in (("own_mean", c["own_mean"]),
                                                ("group_mean", c["group_mean"]),
                                                ("dispersion", CL.replica(c)[2]))},
            "se": pert, "flip_se": flipping({i: pert[i] and pert[i]["distance"] for i in INPUTS}),
            "proportional": prop3, "flip_prop3": flipping(prop3),
            "flip_prop5": r["flipping_input"],
            "own_nearer_than_group_predicted": pred})
    return out, no_se


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", required=True, help="c-40's counterfactual_ledger_rows.json")
    ap.add_argument("--facts-db", help="c-40's scratch copy of the facts tables (--build-cache)")
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--build-cache", action="store_true")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--results-dir")
    a = ap.parse_args(argv)
    if a.build_cache:
        return build_cache(a)

    t0 = time.time()
    with open(a.rows, encoding="utf-8") as fh:
        src = json.load(fh)
    rows = src["rows"]
    if len(rows) != N_ROWS or src["ledger_sha256"] != LEDGER_SHA:
        raise FrozenError(f"{len(rows)} rows / ledger {src['ledger_sha256'][:12]}: not c-40's "
                          f"{N_ROWS} rows of {LEDGER_SHA[:12]}")
    print(f"population: {len(rows)} rows of ledger {LEDGER_SHA[:12]}... (c-40's, frozen)")
    print(f"model in this tree: {baseline.model_version()}")
    cache = load_cache(a.cache_dir)
    print(f"components cache: {len(cache)} leans")
    out, no_se = analyse(rows, cache)
    print(f"frozen gate: {len(out)} of {N_ROWS} rebuilt fits equal c-40's recorded inputs to "
          f"{MATCH_TOL:g}, and every input's nearest log-ratio flip equals c-40's to {FLIP_TOL:g}")
    if len(out) != N_ROWS:
        raise FrozenError("not every row was analysed")

    rng = np.random.default_rng(SEED)
    sl = {"all": out}
    for m in ("receptions", "rush_attempts"):
        sl[f"market={m}"] = [r for r in out if r["market"] == m]
    for s in ("over", "under"):
        sl[f"side={s}"] = [r for r in out if r["side"] == s]
    for b in ("4-6", "6-8", "8+"):
        sl[f"band={b}"] = [r for r in out if r["band"] == b]
    if tuple(sl) != SLICE_NAMES or sum(len(sl[n]) for n in SLICE_NAMES[5:]) != N_ROWS:
        raise SystemExit("slices are not c-40's eight, or the bands do not partition the rows")
    summaries, tests = [], []
    for name in SLICE_NAMES:
        s, t = slice_tests(name, sl[name], rng)
        summaries.append(s)
        tests += t
    if len(tests) != 48:
        raise SystemExit(f"{len(tests)} tests built; the registered family is 48")
    CL.holm(tests)
    for t in tests:
        t["reading"] = reading(t)
    v = verdict(summaries[0], tests)

    missed = [r for r in out if r["result"] == "missed"]
    cleared = [r for r in out if r["result"] == "cleared"]

    def share_of(rs, key):
        n, s, cnt = shares(rs, key)
        return {"n_with_flip": n, "share": s, "count": {i: cnt[i] for i in INPUTS},
                "order": sorted(INPUTS, key=lambda i: (-cnt[i], INPUTS.index(i)))}

    prop5 = {res: dict(Counter(r["flip_prop5"] for r in rs))
             for res, rs in (("missed", missed), ("cleared", cleared))}
    both = [r for r in out if r["flip_se"] is not None and r["flip_prop3"] is not None]
    agree = sum(1 for r in both if r["flip_se"] == r["flip_prop3"])
    pred = [r["own_nearer_than_group_predicted"] for r in out
            if r["own_nearer_than_group_predicted"] is not None]
    rel = {i: [r["relative_se"][i] for r in out if r["relative_se"][i] is not None] for i in INPUTS}
    desc = {
        "uncertainty_scale": {"missed": share_of(missed, "flip_se"),
                              "cleared": share_of(cleared, "flip_se")},
        "proportional_scale_same_three_inputs": {"missed": share_of(missed, "flip_prop3"),
                                                 "cleared": share_of(cleared, "flip_prop3")},
        "proportional_scale_c40_five_inputs_counts": prop5,
        "row_agreement_three_inputs": {"n": len(both), "same": agree,
                                       "share": agree / len(both) if both else None,
                                       "transitions": {f"{a_}->{b_}": n for (a_, b_), n in sorted(
                                           Counter((r["flip_prop3"], r["flip_se"]) for r in both).items())}},
        "prediction_own_nearer_than_group": {"n": len(pred), "holds": int(sum(pred)),
                                             "share": sum(pred) / len(pred) if pred else None},
        "no_standard_error": {i: dict(no_se[i]) for i in INPUTS},
        "relative_se_median": {i: float(np.median(rel[i])) if rel[i] else None for i in INPUTS},
        "direction_differs_from_log_nearest": {i: sum(
            1 for r in out if r["se"][i] is not None
            and abs(r["se"][i]["log_distance"] - r["proportional"][i]) > FLIP_TOL) for i in INPUTS},
    }
    res = {"unit": "c-42", "preregistration": "docs/C42-uncertainty-scale-preregistration.md",
           "base": "c-40 rows, research/results/counterfactual_ledger_rows.json",
           "ledger_sha256": LEDGER_SHA, "n_rows": N_ROWS, "n_missed": len(missed),
           "n_cleared": len(cleared), "inputs": list(INPUTS), "excluded_by_name": EXCLUDED,
           "null_share": 1.0 / K, "specifications_tried": 1, "family_size": len(tests),
           "registered_mde": REGISTERED_MDE, "verdict": v, "slices": summaries, "tests": tests,
           "ceiling_missed": ceiling(missed), "ceiling_cleared": ceiling(cleared),
           "descriptive": desc,
           "bootstrap": {"draws": BOOT, "seed": SEED, "block": "game",
                         "dispersion_se_draws": SE_BOOT}}
    if a.results_dir:
        os.makedirs(a.results_dir, exist_ok=True)
        with open(os.path.join(a.results_dir, "counterfactual_uncertainty.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)
        with open(os.path.join(a.results_dir, "counterfactual_uncertainty_rows.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"unit": "c-42", "ledger_sha256": LEDGER_SHA, "n": len(out),
                       "distance": "smallest |x' - x| / SE on one input that puts the model on "
                                   "the opposite side; SE is the input's own as-of standard "
                                   "error; null where it has none or cannot reach the flip",
                       "excluded_by_name": EXCLUDED, "rows": out}, fh, indent=1)

    # ---- the printed report: aggregates only
    print(f"\nverdict: {v['verdict']} - {v['words']}")
    print(f"  first input {v['first_input']}: missed {v['share_missed']:.3f}  cleared "
          f"{v['share_cleared']:.3f}")
    print(f"  cleared comparison: {v['cleared_statement']}")

    def f(t):
        if t["lo"] is None:
            return f"{t['estimate']:+.3f} [n/a] holm 1"
        if t["ratio_to_mde"] is None:
            return f"{t['estimate']:+.3f} [zero variance] holm {t['p_holm']:.2g}"
        return (f"{t['estimate']:+.3f} [{t['lo']:+.3f}, {t['hi']:+.3f}] p {t['p']:.2g} holm "
                f"{t['p_holm']:.2g} MDE {t['mde']:.3f} est/MDE {t['ratio_to_mde']:.2f} "
                f"({t['reading']})")
    for s in summaries:
        print(f"\n[{s['slice']}] rows {s['n_rows']} games {s['n_games']}  missed "
              f"{s['missed']['n']} (with a flip {s['missed']['n_with_flip']})  cleared "
              f"{s['cleared']['n']} (with a flip {s['cleared']['n_with_flip']})")
        for i in INPUTS:
            t1 = next(t for t in tests if (t["slice"], t["test"], t["input"]) == (s["slice"], "T1", i))
            t2 = next(t for t in tests if (t["slice"], t["test"], t["input"]) == (s["slice"], "T2", i))
            print(f"  {i:11s} missed {s['missed']['share'][i]:.3f}  cleared "
                  f"{s['cleared']['share'][i]:.3f}   T1 {f(t1)}   T2 {f(t2)}")
    print(f"\nHolm survivors at {ALPHA}: {sum(1 for t in tests if t['p_holm'] < ALPHA)} of "
          f"{len(tests)}; T2 with unadjusted p < 0.05: "
          f"{sum(1 for t in tests if t['test'] == 'T2' and t['p'] < 0.05)}")
    print(f"registered MDE: {json.dumps(REGISTERED_MDE)}")
    print(f"descriptive: {json.dumps(desc, indent=1)}")
    for label, c_ in (("missed", res["ceiling_missed"]), ("cleared", res["ceiling_cleared"])):
        print(f"ceiling in SEs, {label} ({c_['n_missed']}): no single-input flip "
              f"{c_['no_single_input_flip']}, median nearest {c_['median_nearest_se']}")
        for cap in c_["caps"]:
            print(f"  within {cap['cap_se']:g} SE: {cap['n_reached']} ({cap['share_reached']:.3f})")
        for i in INPUTS:
            print(f"  {i:11s} {c_['per_input'][i]}")
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
