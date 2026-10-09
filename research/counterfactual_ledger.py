"""c-40 - the counterfactual ledger: which single model input would have flipped a lean.

    python -m research.counterfactual_ledger --ledger <snapshot of board/nfl/ledger.parquet>
        --reads-dir <snapshot of board/nfl> --facts-db <scratch copy of the facts tables>
        --results-dir research/results

PRE-REGISTRATION: docs/C40-counterfactual-preregistration.md, committed at 8ce15b1
BEFORE this script existed. This file implements it and does not extend it: the
five inputs, the flip target, the proportional distance, T1 / T2, the eight
slices and the 80-test Holm family are that document's.

The model is NOT edited and NOT refitted. Each lean's fit is rebuilt by
`models.baseline.fit_player_stat` as of the lean's own `read_at`; the same
feature calls are then made once more so the fit's intermediate numbers
(`own_mean`, `group_mean`, `weight`) can be named, and `replica()` is asserted
equal to the model's own output on EVERY row before any of them is moved. A
lean enters the analysis only if the rebuilt P(over) is the ledgered one.

`--facts-db` is a scratch copy of nfl_player_week / nfl_games / player_xwalk,
opened mode=ro. The live store is never opened by this script.
"""
import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
from scipy import stats as sps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import record as R  # noqa: E402
from core.distributions import NegativeBinomial  # noqa: E402
from models import baseline, features  # noqa: E402

INPUTS = ("own_mean", "group_mean", "weight", "dispersion", "line")   # closed (prereg 3)
K = len(INPUTS)
FACTOR = 20.0                 # continuous inputs: x0.05 .. x20
STEP = math.log(FACTOR) / 600
WEIGHT_FLOOR = 1e-6           # (0, 1] scanned down to here
LINE_STEPS = 30
GATE_TOL = 1e-4               # rebuilt P(over) against the ledgered one
GATE_MIN_SHARE = 0.80
CAPS = (("10%", math.log(1.10)), ("25%", math.log(1.25)), ("50%", math.log(1.5)),
        ("100%", math.log(2.0)))
BOOT, SEED = 2000, 40
MIN_BLOCKS = 5
ALPHA = 0.05
MDE_MULT = 2.80               # 80% power, two-sided 5%
ZERO_SE = 1e-12               # a bootstrap sd below this is zero variance
BANDS = (("4-6", 4.0, 6.0), ("6-8", 6.0, 8.0), ("8+", 8.0, None))


class GateError(AssertionError):
    """The replica of the fit's arithmetic is not the model's arithmetic."""


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


# =============================================================================
# the fit, with its intermediate numbers named
# =============================================================================

def components(con, gsis, stat, season, as_of_ts, pos, team):
    """The numbers `fit_player_stat` computes on the way, from the SAME calls in
    the same order. -> dict. Verified against the fit by `check_replica`."""
    seasons = (season - 1, season)
    prior = features.player_prior(con, gsis, stat, as_of_ts, seasons)
    pos = (pos or "").upper()
    role = features.role_rank(con, gsis, stat, as_of_ts, seasons)
    pp = features.positional_prior(con, pos, stat, as_of_ts, seasons, role=role)
    if pp["n_players"] < 3:
        pp = features.positional_prior(con, pos, stat, as_of_ts, seasons)
    w = baseline._shrink_weight(prior.n_games, baseline.shrink_games(stat))
    team_changed = bool(team and prior.last_team and prior.last_team != team)
    if team_changed:
        w *= baseline.TEAM_CHANGE_KEEP
    coach_changed = False
    if team:
        coach_changed = bool(features.team_context(con, team, season, as_of_ts)["coach_changed"])
        if coach_changed:
            w *= baseline.COACH_CHANGE_KEEP
    return {"stat": stat, "n": prior.n_games, "own_mean": prior.mean, "own_var": prior.var,
            "group_mean": pp["mean"], "group_var": pp["var"], "no_group": pp["n_players"] == 0,
            "weight": w, "role": role, "team_changed": team_changed,
            "coach_changed": coach_changed}


def replica(c, own_mean=None, group_mean=None, weight=None, var_scale=1.0):
    """(mean, var_mean_ratio, total_var) of the negative binomial, with at most one
    named input replaced. The arithmetic is `fit_player_stat`'s count branch;
    the variance is the model's own `predictive_variance`, imported.
    -> None where the perturbation is outside what the model allows."""
    pm = c["own_mean"] if own_mean is None else own_mean
    gm = c["group_mean"] if group_mean is None else group_mean
    w = c["weight"] if weight is None else weight
    if c["no_group"]:
        gm = pm                      # the model falls back to the player's own mean
    mean = max(w * pm + (1.0 - w) * gm, baseline.MIN_MEAN)
    wv = baseline._shrink_weight(c["n"], baseline.SHRINK_GAMES_VMR)
    player_vmr = c["own_var"] / pm if pm > 0 else 0.0
    pos_vmr = (c["group_var"] / gm) if gm > 0 else baseline.MIN_VMR
    vmr = max(wv * player_vmr + (1.0 - wv) * pos_vmr, baseline.MIN_VMR)
    total = baseline.predictive_variance(mean, vmr, c["own_var"], c["n"], w, c["stat"])
    if var_scale != 1.0:
        total *= var_scale
        if total / mean < baseline.MIN_VMR:
            return None              # prereg 3: never below MIN_VMR * mean
    vmr_total = max(total / mean, baseline.MIN_VMR) if mean > 0 else baseline.MIN_VMR
    return mean, vmr_total, total


def p_over(c, line, **kw):
    m = replica(c, **kw)
    if m is None:
        return None
    return NegativeBinomial(m[0], m[1]).prob_over(float(line), push=False)


def check_replica(c, fit, line):
    """Raise unless the replica IS the model on this row - mean and dispersion to
    the 6 dp the fit records, the weight to its 4 dp, P(over) exactly."""
    mean, vmr_total, _ = replica(c)
    bad = []
    if abs(round(mean, 6) - fit.params["mean"]) > 1e-9:
        bad.append(f"mean {mean} vs {fit.params['mean']}")
    if abs(round(vmr_total, 6) - fit.params["var_mean_ratio"]) > 1e-9:
        bad.append(f"vmr {vmr_total} vs {fit.params['var_mean_ratio']}")
    if abs(round(c["weight"], 4) - fit.shrink_weight) > 1e-9:
        bad.append(f"weight {c['weight']} vs {fit.shrink_weight}")
    p_fit = fit.dist.prob_over(float(line), push=False)
    if abs(p_over(c, line) - p_fit) > 1e-12:
        bad.append(f"p {p_over(c, line)} vs {p_fit}")
    if bad:
        raise GateError(f"replica is not the model for {fit.gsis_id} {fit.stat}: " + "; ".join(bad))
    return p_fit


# =============================================================================
# the minimum single-input perturbation
# =============================================================================

def reached(p, side, mkt, T, target):
    """Has the perturbed model reached the target? `flip`: it would publish the
    opposite side. `withdraw`: it would publish no lean."""
    if p is None:
        return False
    gap = 100.0 * (p - mkt)
    if target == "flip":
        return gap <= -T if side == "over" else gap >= T
    return abs(gap) < T


def _scan(f, up_max, down_max):
    """Smallest u > 0 with f(+u) or f(-u) true, scanning outward on a STEP grid
    and bisecting the first crossing. f takes a signed log-ratio. -> (u, sign)
    or None. `up_max` / `down_max` bound each direction (log units)."""
    best = None
    for sign, umax in ((1, up_max), (-1, down_max)):
        if umax <= 0:
            continue
        n = int(math.ceil(umax / STEP))
        prev = 0.0
        for j in range(1, n + 1):
            u = min(j * STEP, umax)
            if best is not None and prev >= best[0]:
                break
            if f(sign * u):
                lo, hi = prev, u
                for _ in range(40):
                    mid = 0.5 * (lo + hi)
                    if f(sign * mid):
                        hi = mid
                    else:
                        lo = mid
                if best is None or hi < best[0]:
                    best = (hi, sign)
                break
            prev = u
    return best


def min_perturbations(c, line, side, mkt, T, target):
    """{input: {"value", "distance", ...} or None} for one lean and one target."""
    out = {}
    lf = math.log(FACTOR)

    def ok(**kw):
        return reached(p_over(c, line, **kw), side, mkt, T, target)

    if reached(p_over(c, line), side, mkt, T, target):          # already there (withdraw only)
        return {i: {"value": _base(c, line, i), "distance": 0.0} for i in INPUTS}

    x = c["own_mean"]
    r = _scan(lambda u: ok(own_mean=x * math.exp(u)), lf, lf) if x > 0 and c["weight"] > 0 else None
    out["own_mean"] = None if r is None else {"value": x * math.exp(r[1] * r[0]), "distance": r[0]}

    x = c["group_mean"]
    r = (_scan(lambda u: ok(group_mean=x * math.exp(u)), lf, lf)
         if x > 0 and not c["no_group"] else None)
    out["group_mean"] = None if r is None else {"value": x * math.exp(r[1] * r[0]), "distance": r[0]}

    x = c["weight"]
    r = (_scan(lambda u: ok(weight=min(x * math.exp(u), 1.0)), -math.log(x),
               math.log(x / WEIGHT_FLOOR)) if x > 0 else None)
    out["weight"] = None if r is None else {"value": min(x * math.exp(r[1] * r[0]), 1.0),
                                            "distance": r[0]}

    total = replica(c)[2]
    r = _scan(lambda u: ok(var_scale=math.exp(u)), lf, lf)
    out["dispersion"] = None if r is None else {"value": total * math.exp(r[1] * r[0]),
                                                "distance": r[0]}

    best = None
    for sign in (1, -1):
        for j in range(1, LINE_STEPS + 1):
            new = line + sign * j
            if new < 0.5:
                break
            if reached(p_over(c, new), side, mkt, T, target):
                d = abs(math.log(new / line))
                if best is None or d < best["distance"]:
                    best = {"value": new, "distance": d, "steps": sign * j}
                break
    out["line"] = best
    return out


def _base(c, line, name):
    if name == "line":
        return line
    if name == "dispersion":
        return replica(c)[2]
    return c[name]


def flipping_input(pert):
    """The input with the smallest distance; ties to the earlier of INPUTS."""
    cand = [(pert[i]["distance"], n, i) for n, i in enumerate(INPUTS) if pert[i] is not None]
    return min(cand)[2] if cand else None


# =============================================================================
# shares, tests, correction
# =============================================================================

def band_of(gap_pp):
    g = abs(gap_pp)
    for name, lo, hi in BANDS:
        if g >= lo and (hi is None or g < hi):
            return name
    return None


def shares(rows):
    have = [r for r in rows if r["flipping_input"] is not None]
    n = len(have)
    cnt = Counter(r["flipping_input"] for r in have)
    return n, {i: (cnt[i] / n if n else float("nan")) for i in INPUTS}, cnt


def slice_tests(name, rows, rng):
    """T1 and T2 for every input on one slice, game-block bootstrap."""
    missed = [r for r in rows if r["result"] == "missed"]
    cleared = [r for r in rows if r["result"] == "cleared"]
    n_m, s_m, cnt_m = shares(missed)
    n_c, s_c, cnt_c = shares(cleared)
    games = sorted({r["game_id"] for r in rows})
    gi = {g: n for n, g in enumerate(games)}
    cm = np.zeros((len(games), K))
    cc = np.zeros((len(games), K))
    for r in rows:
        if r["flipping_input"] is None:
            continue
        (cm if r["result"] == "missed" else cc)[gi[r["game_id"]], INPUTS.index(r["flipping_input"])] += 1
    draws = rng.integers(0, len(games), size=(BOOT, len(games))) if games else np.zeros((0, 0), int)
    bm = cm[draws].sum(axis=1)                       # (BOOT, K)
    bc = cc[draws].sum(axis=1)
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
                # A share that is 0 in every draw has a bootstrap sd of ~1e-17, not
                # 0.0: the mean of 2,000 identical floats is not exactly that float.
                # The first run tested `se == 0`, so those tests entered the family
                # at p = 0 instead of the registered p = 1 (findings, section 6).
                flat = se < ZERO_SE
                p = 1.0 if flat else float(2 * sps.norm.sf(abs(est) / se))
                t.update(lo=lo, hi=hi, se=0.0 if flat else se, p=p,
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


def holm(tests):
    order = sorted(range(len(tests)), key=lambda j: tests[j]["p"])
    m, running = len(tests), 0.0
    for rank, j in enumerate(order):
        running = max(running, min(1.0, (m - rank) * tests[j]["p"]))
        tests[j]["p_holm"] = running
    return tests


def verdict(tests):
    """The registered rule, read on the all-rows slice. Each branch is reachable."""
    allr = [t for t in tests if t["slice"] == "all"]
    t1 = {t["input"] for t in allr if t["test"] == "T1" and t["estimate"] > 0 and t["p_holm"] < ALPHA}
    t2 = {t["input"] for t in allr if t["test"] == "T2" and t["estimate"] > 0 and t["p_holm"] < ALPHA}
    both = sorted(t1 & t2, key=INPUTS.index)
    if both:
        return {"verdict": "concentrated_and_about_the_misses", "inputs": both}
    if t1:
        return {"verdict": "concentrated_and_structural", "inputs": sorted(t1, key=INPUTS.index)}
    return {"verdict": "spread_evenly", "inputs": []}


def ceiling(missed):
    n = len(missed)
    dmin = [min((r["flip"][i]["distance"] for i in INPUTS if r["flip"][i] is not None),
                default=None) for r in missed]
    out = {"n_missed": n,
           "no_single_input_flip": sum(1 for d in dmin if d is None),
           "caps": []}
    for label, cap in CAPS:
        k = sum(1 for d in dmin if d is not None and d <= cap)
        out["caps"].append({"cap": label, "log_distance": cap, "n_reached": k,
                            "n_not_reached": n - k, "share_reached": k / n if n else None})
    out["per_input"] = {}
    for i in INPUTS:
        ds = [r["flip"][i]["distance"] for r in missed if r["flip"][i] is not None]
        out["per_input"][i] = {"n_can_flip": len(ds), "n_cannot": n - len(ds),
                               "median_log_distance": float(np.median(ds)) if ds else None,
                               "median_proportional_change": (float(math.exp(np.median(ds)) - 1)
                                                              if ds else None)}
    return out


# =============================================================================
# I/O
# =============================================================================

def read_index(reads_dir):
    """{read_at: {(gsis, market, line): (pos, team)}} from every Board read file."""
    idx = {}
    for root, _, files in os.walk(reads_dir):
        for fn in files:
            if not (fn.startswith("read-") and fn.endswith(".json")):
                continue
            with open(os.path.join(root, fn), encoding="utf-8") as fh:
                d = json.load(fh)
            m = idx.setdefault(d["read_at"], {})
            for r in d["rows"]:
                m[(r["gsis_id"], r["market"], float(r["line"]))] = (r["pos"], r["team"])
    return idx


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cached_features():
    import functools
    for n in ("player_prior", "role_rank", "positional_prior", "team_context"):
        setattr(features, n, functools.lru_cache(maxsize=200_000)(getattr(features, n)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", required=True, help="snapshot of board/nfl/ledger.parquet")
    ap.add_argument("--reads-dir", required=True, help="snapshot of the Board's read files")
    ap.add_argument("--facts-db", required=True, help="scratch copy of the facts tables")
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--now", type=float, default=None, help="ledger 'now' (default: wall clock)")
    ap.add_argument("--from-rows", default=None,
                    help="a directory holding a previous run's two output files: skip the fits "
                         "and recompute the shares and tests from its per-row file")
    a = ap.parse_args(argv)

    import polars as pl
    t0 = time.time()
    ledger = pl.read_parquet(a.ledger).to_dicts()
    led_hash = sha256(a.ledger)
    body = R.build_published(ledger, a.now if a.now is not None else time.time())
    leans = body["leans"]
    graded = [x for x in leans if x["result"] in ("cleared", "missed")]
    thr = {e["lean_id"]: float(e["lean_threshold_pp"]) for e in ledger if e["event"] == "published"}
    print(f"ledger {a.ledger} sha256 {led_hash}")
    print(f"events {len(ledger)}  published {body['n_published']}  graded {body['n_graded']}  "
          f"void {body['n_void']}  ungraded {body['n_ungraded']}  "
          f"cleared {body['record']['cleared']}  missed {body['record']['missed']}  "
          f"push {body['record']['push']}")
    if not graded:
        raise SystemExit("no graded lean in the ledger - nothing to do, and that is not a result")
    if baseline.model_version() not in {x["model_version"] for x in graded}:
        print(f"NOTE: this tree's model is {baseline.model_version()}, the ledger's "
              f"{sorted({x['model_version'] for x in graded})} - the gate below decides")

    if a.from_rows:
        with open(os.path.join(a.from_rows, "counterfactual_ledger_rows.json"), encoding="utf-8") as fh:
            prev_rows = json.load(fh)
        with open(os.path.join(a.from_rows, "counterfactual_ledger.json"), encoding="utf-8") as fh:
            prev = json.load(fh)
        if prev_rows["ledger_sha256"] != led_hash or prev["ledger_sha256"] != led_hash:
            raise SystemExit("--from-rows was built from a different ledger; refusing")
        rows, failed = prev_rows["rows"], prev["gate"]["failed"]
        gate = Counter(prev["gate"]["reasons"])
        gate["pass"] = len(rows)
        if {r["lean_id"] for r in rows} | {f["lean_id"] for f in failed} != {x["lean_id"] for x in graded}:
            raise SystemExit("--from-rows does not hold exactly this ledger's graded leans")
        print(f"rows read from {a.from_rows}: {len(rows)} (no fit rebuilt in this run)")
        graded_iter = []
    else:
        graded_iter = graded
        rows, gate, failed = [], Counter(), []
    idx = read_index(a.reads_dir) if not a.from_rows else {}
    print(f"read files indexed: {len(idx)}")
    con = sqlite3.connect(f"file:{a.facts_db}?mode=ro", uri=True)
    cached_features()

    for n, x in enumerate(graded_iter):
        key = (x["gsis_id"], x["market"], float(x["line"]))
        who = (idx.get(x["read_at"]) or {}).get(key)
        if who is None:
            gate["no row in the lean's read file"] += 1
            failed.append({"lean_id": x["lean_id"], "why": "no row in the lean's read file"})
            continue
        pos, team = who
        as_of = parse_iso(x["read_at"])
        fit = baseline.fit_player_stat(con, x["gsis_id"], x["market"], x["season"], as_of, pos,
                                       team, prior_seasons=(x["season"] - 1, x["season"]))
        fit.provenance.assert_as_of(as_of)
        c = components(con, x["gsis_id"], x["market"], x["season"], as_of, pos, team)
        p = check_replica(c, fit, x["line"])                    # raises: a bug, not a row to drop
        if abs(p - x["model_p_over"]) > GATE_TOL:
            gate["rebuilt P(over) is not the ledgered one"] += 1
            failed.append({"lean_id": x["lean_id"], "why": "rebuilt P(over) is not the ledgered one",
                           "rebuilt": round(p, 4), "ledgered": x["model_p_over"]})
            continue
        gate["pass"] += 1
        T, mkt = thr[x["lean_id"]], x["mkt_p_over"]
        flip = min_perturbations(c, x["line"], x["side"], mkt, T, "flip")
        wd = min_perturbations(c, x["line"], x["side"], mkt, T, "withdraw")
        rows.append({
            "lean_id": x["lean_id"], "season": x["season"], "week": x["week"],
            "game_id": x["game_id"], "gsis_id": x["gsis_id"], "pos": pos, "team": team,
            "market": x["market"], "line": x["line"], "side": x["side"],
            "read_at": x["read_at"], "result": x["result"], "actual": x["actual"],
            "model_p_over": x["model_p_over"], "mkt_p_over": mkt, "gap_pp": x["gap_pp"],
            "band": band_of(x["gap_pp"]), "threshold_pp": T,
            "as_of": {"own_mean": c["own_mean"], "group_mean": c["group_mean"],
                      "weight": c["weight"], "dispersion": replica(c)[2], "line": x["line"],
                      "prior_games": c["n"], "role": c["role"],
                      "team_changed": c["team_changed"], "coach_changed": c["coach_changed"]},
            "flip": flip, "flipping_input": flipping_input(flip),
            "withdraw": wd, "withdraw_input": flipping_input(wd)})
        if (n + 1) % 100 == 0:
            print(f"  {n + 1}/{len(graded)} leans  {time.time() - t0:.0f}s", flush=True)
    con.close()

    n_pass = gate["pass"]
    print(f"reconstruction gate: {n_pass} of {len(graded)} graded leans pass; {dict(gate)}")
    if n_pass != len(rows) or n_pass + len(failed) != len(graded):
        raise SystemExit("gate counts do not partition the graded leans")
    partial = n_pass < GATE_MIN_SHARE * len(graded)

    rng = np.random.default_rng(SEED)
    slices = [("all", rows)]
    slices += [(f"market={m}", [r for r in rows if r["market"] == m])
               for m in ("receptions", "rush_attempts")]
    slices += [(f"side={s}", [r for r in rows if r["side"] == s]) for s in ("over", "under")]
    slices += [(f"band={b}", [r for r in rows if r["band"] == b]) for b, _, _ in BANDS]
    if sum(len(s) for n_, s in slices if n_.startswith("band=")) != len(rows):
        raise SystemExit("gap bands do not partition the rows")
    summaries, tests = [], []
    for name, rs in slices:
        s, t = slice_tests(name, rs, rng)
        summaries.append(s)
        tests += t
    if len(tests) != 80:
        raise SystemExit(f"{len(tests)} tests built; the registered family is 80")
    holm(tests)
    v = verdict(tests) if not partial else {"verdict": "partial_no_verdict", "inputs": []}

    missed = [r for r in rows if r["result"] == "missed"]
    cleared = [r for r in rows if r["result"] == "cleared"]
    cnt = [summaries[0]["missed"]["count"][i] for i in INPUTS]
    chi = (sps.chisquare(cnt) if sum(cnt) else None)
    line_steps = Counter(r["flip"]["line"]["steps"] for r in missed
                         if r["flipping_input"] == "line")
    out = {
        "unit": "c-40", "preregistration": "docs/C40-counterfactual-preregistration.md",
        "ledger_sha256": led_hash,
        "population": {"events": len(ledger), "published": body["n_published"],
                       "graded": body["n_graded"], "void": body["n_void"],
                       "ungraded": body["n_ungraded"], "cleared": body["record"]["cleared"],
                       "missed": body["record"]["missed"], "push": body["record"]["push"],
                       "weeks_graded": body["weeks_graded"],
                       "model_versions": sorted({x["model_version"] for x in graded})},
        "gate": {"graded": len(graded), "pass": n_pass, "share": n_pass / len(graded),
                 "tolerance": GATE_TOL, "reasons": {k: v_ for k, v_ in gate.items() if k != "pass"},
                 "failed": failed, "partial": partial},
        "inputs": list(INPUTS), "null_share": 1.0 / K,
        "verdict": v,
        "slices": summaries, "tests": tests,
        "omnibus_reference_only": (None if chi is None else
                                   {"chi2": float(chi.statistic), "p_unblocked": float(chi.pvalue),
                                    "note": "rows of one game are not independent; not used"}),
        "ceiling_flip": ceiling(missed),
        "ceiling_flip_cleared": ceiling(cleared),
        "descriptive": {
            "withdraw_input_missed": dict(Counter(r["withdraw_input"] for r in missed)),
            "withdraw_input_cleared": dict(Counter(r["withdraw_input"] for r in cleared)),
            "line_steps_where_line_is_flipping_input_missed": {str(k): v_ for k, v_ in
                                                               sorted(line_steps.items())},
            "missed_by_market_side": {f"{m}|{s}": n_ for (m, s), n_ in sorted(Counter(
                (r["market"], r["side"]) for r in missed).items())},
        },
        "bootstrap": {"draws": BOOT, "seed": SEED, "block": "game"},
    }
    os.makedirs(a.results_dir, exist_ok=True)
    with open(os.path.join(a.results_dir, "counterfactual_ledger.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    with open(os.path.join(a.results_dir, "counterfactual_ledger_rows.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"unit": "c-40", "ledger_sha256": led_hash, "n": len(rows),
                   "distance": "smallest |ln(x'/x)| on one input that puts the model on the "
                               "opposite side; null where the input cannot reach it",
                   "line_note": "a line counterfactual holds the market probability at its "
                                "ledgered value: the same price hung at a different number",
                   "rows": rows}, fh, indent=1)

    # ---- the printed report: aggregates only
    print(f"\nverdict: {v}")
    for s in summaries:
        print(f"\n[{s['slice']}] rows {s['n_rows']} games {s['n_games']}  "
              f"missed {s['missed']['n']} (with a flip {s['missed']['n_with_flip']})  "
              f"cleared {s['cleared']['n']} (with a flip {s['cleared']['n_with_flip']})")
        for i in INPUTS:
            t1 = next(t for t in tests if (t["slice"], t["test"], t["input"]) == (s["slice"], "T1", i))
            t2 = next(t for t in tests if (t["slice"], t["test"], t["input"]) == (s["slice"], "T2", i))

            def f(t):
                if t["lo"] is None:
                    return f"{t['estimate']:+.3f} [n/a] p_holm 1"
                if t["ratio_to_mde"] is None:
                    return f"{t['estimate']:+.3f} [zero variance] p 1 holm {t['p_holm']:.2g}"
                return (f"{t['estimate']:+.3f} [{t['lo']:+.3f}, {t['hi']:+.3f}] "
                        f"p {t['p']:.2g} holm {t['p_holm']:.2g} est/MDE "
                        f"{t['ratio_to_mde']:.2f}")
            print(f"  {i:11s} missed {s['missed']['share'][i]:.3f}  cleared "
                  f"{s['cleared']['share'][i]:.3f}   T1 {f(t1)}   T2 {f(t2)}")
    c_ = out["ceiling_flip"]
    print(f"\nceiling, missed rows ({c_['n_missed']}): no single-input flip at the full range: "
          f"{c_['no_single_input_flip']}")
    for cap in c_["caps"]:
        print(f"  within a {cap['cap']} change: reached {cap['n_reached']}, not reached "
              f"{cap['n_not_reached']} ({cap['share_reached']:.3f})")
    for i in INPUTS:
        pi = c_["per_input"][i]
        print(f"  {i:11s} can flip {pi['n_can_flip']}, cannot {pi['n_cannot']}, median change "
              f"{pi['median_proportional_change']}")
    print(f"omnibus (reference only): {out['omnibus_reference_only']}")
    print(f"descriptive: {json.dumps(out['descriptive'])}")
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
