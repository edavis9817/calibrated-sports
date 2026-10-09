"""c-46 - the 19 leans c-40 dropped, and the within-game permutation null.

    python -m research.counterfactual_population --ledger <c-40's ledger snapshot>
        --reads-dir <snapshot of board/nfl> --facts-db <scratch copy of the facts tables>
        --c40-dir research/results --results-dir research/results --fits-cache <scratch json>

PRE-REGISTRATION: docs/C46-population-and-null-preregistration.md, committed at 0bc9d74
BEFORE this script existed. This file implements it and does not extend it: the one
recovery rule R1, the two populations, the missed-minus-cleared statistic, the
10,000-draw within-game permutation, the 80-test Holm family, the pre-stated MDEs
and the two-outcome verdict are that document's.

c-40's ranking is not re-implemented. The 477 rows are read from c-40's committed
file; a lean entering here is ranked by c-40's own functions, imported. The model is
not edited or refitted. `--facts-db` is opened mode=ro; the live store is never opened.
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import record as R  # noqa: E402
from models import baseline  # noqa: E402
from research import counterfactual_ledger as CL  # noqa: E402

LEDGER_SHA256 = "760b93be22fb1464df139457b011a67687ba1c70a013ba06f8e55a90d225306d"
INPUTS = CL.INPUTS
K = len(INPUTS)
PERM, BOOT, SEED = 10_000, 2000, 46
ALPHA = 0.05
MDE_MULT = 2.80
ZERO_SD = 1e-12
MIN_BLOCKS = 5
POPS = ("P477", "P496")
R1_TABLES = ("nfl_player_week", "nfl_games")
MDE_DRIFT = 1.5               # realised / pre-stated outside [1/1.5, 1.5] is reported


# =============================================================================
# R1: the facts as they had been ingested when the lean was read
# =============================================================================

def set_r1(con, read_ts):
    """Shadow the two versioned facts tables with TEMP views holding only rows
    ingested at or before `read_ts`. An unqualified name resolves to temp first,
    so the model's own SQL reads the restricted facts without being touched."""
    for t in R1_TABLES:
        con.execute(f"DROP VIEW IF EXISTS temp.{t}")
        con.execute(f"CREATE TEMP VIEW {t} AS SELECT * FROM main.{t} "
                    f"WHERE ingested_ts <= {float(read_ts)!r}")


def rebuild(con, x, pos, team):
    """(components, model P(over) at the lean's line) from the model's own fit."""
    as_of = CL.parse_iso(x["read_at"])
    fit = baseline.fit_player_stat(con, x["gsis_id"], x["market"], x["season"], as_of, pos,
                                   team, prior_seasons=(x["season"] - 1, x["season"]))
    fit.provenance.assert_as_of(as_of)
    c = CL.components(con, x["gsis_id"], x["market"], x["season"], as_of, pos, team)
    return c, CL.check_replica(c, fit, x["line"])       # raises: a bug, not a row to drop


def not_yet_ingested(con, read_ts):
    """Player-weeks the model's as-of join admits at `read_ts` (their game has
    kicked off) whose first ingestion is later than `read_ts`. `con` must be a
    connection WITHOUT the R1 views."""
    return con.execute(
        "SELECT COUNT(*) FROM (SELECT pw.gsis_id, pw.season, pw.week, MIN(pw.ingested_ts) first "
        "  FROM main.nfl_player_week pw "
        "  JOIN (SELECT season, week, home_team, away_team, MAX(kickoff_ts) kickoff_ts "
        "          FROM main.nfl_games GROUP BY game_id) g "
        "    ON g.season = pw.season AND g.week = pw.week "
        "   AND (g.home_team = pw.team OR g.away_team = pw.team) "
        " WHERE pw.season_type = 'REG' AND g.kickoff_ts < :t "
        " GROUP BY pw.gsis_id, pw.season, pw.week) WHERE first > :t", {"t": read_ts}).fetchone()[0]


def lean_row(x, pos, team, c, T, fit_basis):
    """One lean in c-40's row shape, ranked by c-40's own functions."""
    mkt = x["mkt_p_over"]
    flip = CL.min_perturbations(c, x["line"], x["side"], mkt, T, "flip")
    wd = CL.min_perturbations(c, x["line"], x["side"], mkt, T, "withdraw")
    return {"lean_id": x["lean_id"], "season": x["season"], "week": x["week"],
            "game_id": x["game_id"], "gsis_id": x["gsis_id"], "pos": pos, "team": team,
            "market": x["market"], "line": x["line"], "side": x["side"],
            "read_at": x["read_at"], "result": x["result"], "actual": x["actual"],
            "model_p_over": x["model_p_over"], "mkt_p_over": mkt, "gap_pp": x["gap_pp"],
            "band": CL.band_of(x["gap_pp"]), "threshold_pp": T, "fit_basis": fit_basis,
            "as_of": {"own_mean": c["own_mean"], "group_mean": c["group_mean"],
                      "weight": c["weight"], "dispersion": CL.replica(c)[2], "line": x["line"],
                      "prior_games": c["n"], "role": c["role"],
                      "team_changed": c["team_changed"], "coach_changed": c["coach_changed"]},
            "flip": flip, "flipping_input": CL.flipping_input(flip),
            "withdraw": wd, "withdraw_input": CL.flipping_input(wd)}


def refit_all(a, graded, thr, failed_ids, c40_rows):
    """R1 on every graded lean: recovery for the 19, control for the 477."""
    idx = CL.read_index(a.reads_dir)
    uri = f"file:{a.facts_db}?mode=ro"
    today = sqlite3.connect(uri, uri=True)
    r1 = sqlite3.connect(uri, uri=True)
    CL.cached_features()
    by_id = {r["lean_id"]: r for r in c40_rows}
    fits, new_rows, t0 = [], [], time.time()
    exposure = {}
    for n, x in enumerate(sorted(graded, key=lambda g: g["read_at"])):
        pos, team = idx[x["read_at"]][(x["gsis_id"], x["market"], float(x["line"]))]
        ts = CL.parse_iso(x["read_at"])
        if x["read_at"] not in exposure:
            exposure[x["read_at"]] = not_yet_ingested(today, ts)
        set_r1(r1, ts)
        c1, p1 = rebuild(r1, x, pos, team)
        rec = {"lean_id": x["lean_id"], "in_477": x["lean_id"] not in failed_ids,
               "week": x["week"], "market": x["market"], "side": x["side"],
               "result": x["result"], "game_id": x["game_id"], "read_at": x["read_at"],
               "ledgered": x["model_p_over"], "r1": p1,
               "r1_within_tol": abs(p1 - x["model_p_over"]) <= CL.GATE_TOL,
               "player_weeks_kicked_off_not_ingested": exposure[x["read_at"]]}
        if rec["in_477"]:
            o = by_id[x["lean_id"]]["as_of"]
            rec["r1_inputs_differ_from_c40"] = bool(
                abs(c1["own_mean"] - o["own_mean"]) > 1e-9
                or abs(c1["group_mean"] - o["group_mean"]) > 1e-9
                or abs(c1["weight"] - o["weight"]) > 1e-9)
        else:
            c0, p0 = rebuild(today, x, pos, team)
            rec["todays_facts"] = p0
            T = thr[x["lean_id"]]
            if rec["r1_within_tol"]:
                new_rows.append(lean_row(x, pos, team, c1, T, "r1_recovered"))
            else:
                new_rows.append(lean_row(x, pos, team, c0, T, "not_the_published_fit"))
        fits.append(rec)
        if (n + 1) % 50 == 0:
            print(f"  {n + 1}/{len(graded)} leans  {time.time() - t0:.0f}s", flush=True)
    today.close()
    r1.close()
    return fits, new_rows


# =============================================================================
# the test: missed minus cleared, against the within-game label permutation
# =============================================================================

def _arrays(rows):
    have = [r for r in rows if r["flipping_input"] is not None]
    games = sorted({r["game_id"] for r in have})
    gi = {g: n for n, g in enumerate(games)}
    g = np.array([gi[r["game_id"]] for r in have], dtype=int)
    miss = np.array([r["result"] == "missed" for r in have], dtype=bool)
    f = np.zeros((len(have), K))
    for n, r in enumerate(have):
        f[n, INPUTS.index(r["flipping_input"])] = 1.0
    return g, miss, f, len(games)


def contrast(miss_counts, total_counts, n_miss, n_all):
    """share among missed minus share among cleared, per input; nan on an empty arm."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return miss_counts / n_miss - (total_counts - miss_counts) / (n_all - n_miss)


def permuted_missed_counts(g, miss, f, n_games, rng, draws=None):
    """(draws, K) missed-row counts per input with the labels shuffled WITHIN game:
    every game keeps its number of misses, every row keeps its flipping input."""
    draws = PERM if draws is None else draws
    out = np.zeros((draws, K))
    for j in range(n_games):
        sel = np.flatnonzero(g == j)
        lab = np.tile(miss[sel].astype(float), (draws, 1))
        out += rng.permuted(lab, axis=1) @ f[sel]
    return out


def wilson(k, n, z=1.959963984540054):
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return mid - half, mid + half


def slice_tests(pop, name, rows, seed, mde_pre, draws=None, boot=None):
    """Five missed-minus-cleared tests on one slice of one population."""
    draws = PERM if draws is None else draws
    boot = BOOT if boot is None else boot
    g, miss, f, n_games = _arrays(rows)
    n_all, n_miss = len(miss), int(miss.sum())
    total = f.sum(axis=0)
    obs_m = f[miss].sum(axis=0) if n_all else np.zeros(K)
    est = contrast(obs_m, total, n_miss, n_all) if n_all else np.full(K, np.nan)
    usable = n_games >= MIN_BLOCKS and 0 < n_miss < n_all
    tests, pct = [], None
    if usable:
        # two streams, so the bootstrap picks do not depend on how many numbers
        # the permutation consumed (it scales with the row count)
        ss_perm, ss_boot = np.random.SeedSequence(seed).spawn(2)
        rng = np.random.default_rng(ss_perm)
        pm = permuted_missed_counts(g, miss, f, n_games, rng, draws)
        null = contrast(pm, total, n_miss, n_all)                       # (draws, K)
        cm = np.zeros((n_games, K))
        ca = np.zeros((n_games, K))
        np.add.at(cm, g[miss], f[miss])
        np.add.at(ca, g, f)
        pick = np.random.default_rng(ss_boot).integers(0, n_games, size=(boot, n_games))
        bm, ba = cm[pick].sum(axis=1), ca[pick].sum(axis=1)
        nm, na = bm.sum(axis=1, keepdims=True), ba.sum(axis=1, keepdims=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            bs = bm / nm - (ba - bm) / (na - nm)
        k0 = INPUTS.index("own_mean")
        below = int((pm[:, k0] < obs_m[k0] - 1e-9).sum())
        ties = int((np.abs(pm[:, k0] - obs_m[k0]) <= 1e-9).sum())
        q = (below + 0.5 * ties) / draws
        lo, hi = wilson(below + 0.5 * ties, draws)
        pct = {"input": "own_mean", "observed_missed_share": float(obs_m[k0] / n_miss),
               "percentile": q, "wilson95": [lo, hi], "draws": draws,
               # not registered: the same position counting ties as below, which is
               # the reading a discrete share invites and the likelier source of a
               # "62nd percentile" on the 477 (findings, section 5)
               "draws_below": below, "draws_tied": ties,
               "share_at_or_below": (below + ties) / draws,
               "null_mean_missed_share": float(pm[:, k0].mean() / n_miss)}
    for k, i in enumerate(INPUTS):
        t = {"population": pop, "slice": name, "input": i,
             "estimate": None if not math.isfinite(est[k]) else float(est[k]),
             "n_missed": n_miss, "n_cleared": n_all - n_miss, "n_games": n_games,
             "missed_share": float(obs_m[k] / n_miss) if n_miss else None,
             "cleared_share": (float((total[k] - obs_m[k]) / (n_all - n_miss))
                               if n_all > n_miss else None),
             "mde_pre": mde_pre.get((name, i))}
        if not usable:
            t.update(p=1.0, lo=None, hi=None, mde_realised=None, mde_used=None,
                     note="fewer than 5 games or an empty arm: p = 1")
        else:
            sd = float(np.std(null[:, k], ddof=1))
            b = bs[:, k][np.isfinite(bs[:, k])]
            lo, hi = (float(v) for v in np.percentile(b, [2.5, 97.5]))
            if sd < ZERO_SD:
                t.update(p=1.0, lo=lo, hi=hi, mde_realised=None, mde_used=None,
                         note="zero-variance permutation: p = 1")
            else:
                ge = int((np.abs(null[:, k]) >= abs(est[k]) - 1e-12).sum())
                real = MDE_MULT * sd
                pre = t["mde_pre"]
                t.update(p=(1 + ge) / (draws + 1), lo=lo, hi=hi, mde_realised=real,
                         mde_used=None if pre is None else max(pre, real),
                         mde_drift=None if pre is None else real / pre, note=None)
        tests.append(t)
    return tests, pct


def holm(tests):
    order = sorted(range(len(tests)), key=lambda j: tests[j]["p"])
    m, running = len(tests), 0.0
    for rank, j in enumerate(order):
        running = max(running, min(1.0, (m - rank) * tests[j]["p"]))
        tests[j]["p_holm"] = running
    return tests


def is_diagnostic(t):
    """The registered three-part bar, on one test."""
    return bool(t["estimate"] is not None and t["lo"] is not None and t["mde_used"] is not None
                and t["p_holm"] < ALPHA and (t["lo"] > 0 or t["hi"] < 0)
                and abs(t["estimate"]) > t["mde_used"])


def verdict(tests, population):
    """Read on the all-rows slice of the verdict population. Both branches reachable."""
    allr = [t for t in tests if t["population"] == population and t["slice"] == "all"]
    hit = [t["input"] for t in allr if is_diagnostic(t)]
    if hit:
        return {"verdict": "diagnostic", "population": population, "inputs": hit}
    return {"verdict": "not diagnostic, at this power", "population": population, "inputs": [],
            "mde": {t["input"]: t["mde_used"] for t in allr}}


def own_mean_bound(rows477, n_missed_out, n_cleared_out):
    """Range of the own-mean contrast over every assignment of flipping inputs to the
    excluded leans, computed without their fits."""
    have = [r for r in rows477 if r["flipping_input"] is not None]
    m = [r for r in have if r["result"] == "missed"]
    c = [r for r in have if r["result"] == "cleared"]
    km = sum(r["flipping_input"] == "own_mean" for r in m)
    kc = sum(r["flipping_input"] == "own_mean" for r in c)
    lo = km / (len(m) + n_missed_out) - (kc + n_cleared_out) / (len(c) + n_cleared_out)
    hi = (km + n_missed_out) / (len(m) + n_missed_out) - kc / (len(c) + n_cleared_out)
    return lo, hi


def slices_of(rows):
    out = [("all", rows)]
    out += [(f"market={m}", [r for r in rows if r["market"] == m])
            for m in ("receptions", "rush_attempts")]
    out += [(f"side={s}", [r for r in rows if r["side"] == s]) for s in ("over", "under")]
    out += [(f"band={b}", [r for r in rows if r["band"] == b]) for b, _, _ in CL.BANDS]
    return out


def analyse(pops, mde_pre):
    tests, pcts = [], {}
    for pi, pop in enumerate(POPS):
        for si, (name, rs) in enumerate(slices_of(pops[pop])):
            t, pct = slice_tests(pop, name, rs, [SEED, pi, si], mde_pre)
            tests += t
            if name == "all":
                pcts[pop] = pct
    if len(tests) != 80:
        raise SystemExit(f"{len(tests)} tests built; the registered family is 80")
    return holm(tests), pcts


# =============================================================================
# I/O
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", required=True, help="c-40's snapshot of board/nfl/ledger.parquet")
    ap.add_argument("--reads-dir", required=True)
    ap.add_argument("--facts-db", required=True, help="scratch copy of the facts tables")
    ap.add_argument("--c40-dir", required=True, help="directory holding c-40's two result files")
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--now", type=float, required=True, help="the ledger 'now' used to grade")
    ap.add_argument("--fits-cache", required=True,
                    help="scratch json for the R1 re-fits; reused when it holds this ledger")
    a = ap.parse_args(argv)

    import polars as pl
    t0 = time.time()
    led_hash = CL.sha256(a.ledger)
    print(f"ledger {a.ledger} sha256 {led_hash}")
    if led_hash != LEDGER_SHA256:
        raise SystemExit("this is not the registered ledger snapshot; refusing")
    ledger = pl.read_parquet(a.ledger).to_dicts()
    body = R.build_published(ledger, a.now)
    graded = [x for x in body["leans"] if x["result"] in ("cleared", "missed")]
    thr = {e["lean_id"]: float(e["lean_threshold_pp"]) for e in ledger if e["event"] == "published"}
    with open(os.path.join(a.c40_dir, "counterfactual_ledger_rows.json"), encoding="utf-8") as fh:
        c40_rows = json.load(fh)
    with open(os.path.join(a.c40_dir, "counterfactual_ledger.json"), encoding="utf-8") as fh:
        c40 = json.load(fh)
    if c40_rows["ledger_sha256"] != led_hash or c40["ledger_sha256"] != led_hash:
        raise SystemExit("c-40's result files were built from a different ledger; refusing")
    rows477 = c40_rows["rows"]
    failed_ids = {f["lean_id"] for f in c40["gate"]["failed"]}
    ids = {x["lean_id"] for x in graded}
    if len(graded) != 496 or len(rows477) != 477 or len(failed_ids) != 19 \
            or {r["lean_id"] for r in rows477} | failed_ids != ids:
        raise SystemExit(f"population is not 496 = 477 + 19: graded {len(graded)}, "
                         f"rows {len(rows477)}, failed {len(failed_ids)}")
    res = {x["lean_id"]: x["result"] for x in graded}
    if any(res[r["lean_id"]] != r["result"] for r in rows477):
        raise SystemExit("a c-40 row's result is not the ledger's under --now; refusing")
    print(f"graded {len(graded)} = {len(rows477)} analysed by c-40 + {len(failed_ids)} excluded")

    cache = None
    if os.path.exists(a.fits_cache):
        with open(a.fits_cache, encoding="utf-8") as fh:
            cache = json.load(fh)
        if cache.get("ledger_sha256") != led_hash or len(cache["fits"]) != len(graded):
            cache = None
    if cache is None:
        fits, new_rows = refit_all(a, graded, thr, failed_ids, rows477)
        with open(a.fits_cache, "w", encoding="utf-8") as fh:
            json.dump({"ledger_sha256": led_hash, "fits": fits, "new_rows": new_rows}, fh)
        print(f"R1 re-fits done in {time.time() - t0:.0f}s")
    else:
        fits, new_rows = cache["fits"], cache["new_rows"]
        print(f"R1 re-fits read from {a.fits_cache} (no fit rebuilt in this run)")
    if {r["lean_id"] for r in new_rows} != failed_ids or len(fits) != 496:
        raise SystemExit("the re-fits do not cover exactly the 19 and the 496")

    nineteen = [f for f in fits if not f["in_477"]]
    control = [f for f in fits if f["in_477"]]
    n_rec = sum(f["r1_within_tol"] for f in nineteen)
    n_ctl = sum(f["r1_within_tol"] for f in control)
    all_recovered = n_rec == len(nineteen)
    verdict_pop = "P496" if all_recovered else "P477"
    print(f"R1 recovers {n_rec} of {len(nineteen)} excluded leans; control: {n_ctl} of "
          f"{len(control)} of c-40's rows still rebuild under R1; verdict population {verdict_pop}")

    mde_pre = {(t["slice"], t["input"]): t["mde"] for t in c40["tests"] if t["test"] == "T2"}
    pops = {"P477": rows477, "P496": rows477 + new_rows}
    tests, pcts = analyse(pops, mde_pre)
    v = verdict(tests, verdict_pop)
    n_out_m = sum(f["result"] == "missed" for f in nineteen)
    lo, hi = own_mean_bound(rows477, n_out_m, len(nineteen) - n_out_m)

    def tab(fs, key):
        return {str(k): n for k, n in sorted(Counter(key(f) for f in fs).items())}
    exposed = lambda f: f["player_weeks_kicked_off_not_ingested"] > 0     # noqa: E731
    out = {
        "unit": "c-46", "preregistration": "docs/C46-population-and-null-preregistration.md",
        "ledger_sha256": led_hash, "now": a.now,
        "population": {"graded": len(graded), "c40_analysed": len(rows477),
                       "c40_excluded": len(nineteen)},
        "the_19": {
            "by_week": tab(nineteen, lambda f: f["week"]),
            "by_market": tab(nineteen, lambda f: f["market"]),
            "by_side": tab(nineteen, lambda f: f["side"]),
            "by_result": tab(nineteen, lambda f: f["result"]),
            "by_game": tab(nineteen, lambda f: f["game_id"]),
            "c40_reason": c40["gate"]["reasons"],
            "rows": [dict(f, flipping_input=next(r["flipping_input"] for r in new_rows
                                                 if r["lean_id"] == f["lean_id"]),
                          fit_basis=next(r["fit_basis"] for r in new_rows
                                         if r["lean_id"] == f["lean_id"]))
                     for f in nineteen]},
        "r1": {"rule": "facts restricted to rows with ingested_ts <= read_at "
                       "(nfl_player_week, nfl_games)",
               "tolerance": CL.GATE_TOL, "recovered": n_rec, "of": len(nineteen),
               "all_recovered": all_recovered,
               "control_477": {"still_within_tol": n_ctl, "of": len(control),
                               "inputs_differ_from_c40": sum(f["r1_inputs_differ_from_c40"]
                                                             for f in control),
                               "failed": [f for f in control if not f["r1_within_tol"]]},
               "exposure": {
                   "note": "exposed = the lean was read while at least one player-week whose "
                           "game had kicked off was not yet ingested",
                   "the_19_exposed": sum(exposed(f) for f in nineteen),
                   "the_477_exposed": sum(exposed(f) for f in control),
                   "the_477_exposed_by_week": tab([f for f in control if exposed(f)],
                                                  lambda f: f["week"]),
                   "exposed_by_result": tab([f for f in fits if exposed(f)],
                                            lambda f: f["result"]),
                   "unexposed_by_result": tab([f for f in fits if not exposed(f)],
                                              lambda f: f["result"])}},
        "verdict_population": verdict_pop, "verdict": v,
        "permutation": {"draws": PERM, "seed": SEED, "within": "game",
                        "p": "(1 + #{|d*| >= |d|}) / (draws + 1)"},
        "bootstrap": {"draws": BOOT, "seed": SEED, "block": "game"},
        "family": {"tests": len(tests), "correction": "holm", "alpha": ALPHA,
                   "survivors": sum(t["p_holm"] < ALPHA for t in tests),
                   "p_below_0.05_unadjusted": sum(t["p"] < 0.05 for t in tests),
                   "min_p": min(t["p"] for t in tests)},
        "own_mean_percentile": pcts,
        "own_mean_bound_over_the_19": {"lo": lo, "hi": hi},
        "tests": tests,
    }
    os.makedirs(a.results_dir, exist_ok=True)
    with open(os.path.join(a.results_dir, "counterfactual_population.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    with open(os.path.join(a.results_dir, "counterfactual_population_rows.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"unit": "c-46", "ledger_sha256": led_hash, "n": len(new_rows),
                   "note": "the 19 leans c-40 excluded, in c-40's row shape; fit_basis says "
                           "which facts the fit was rebuilt on",
                   "rows": new_rows}, fh, indent=1)

    # ---- the printed report: aggregates only
    t19 = out["the_19"]
    print(f"\nthe 19: week {t19['by_week']}  market {t19['by_market']}  side {t19['by_side']}  "
          f"result {t19['by_result']}\n        game {t19['by_game']}")
    e = out["r1"]["exposure"]
    print(f"exposed at read: {e['the_19_exposed']} of 19, {e['the_477_exposed']} of 477 "
          f"(by week {e['the_477_exposed_by_week']}); 477 rows whose R1 inputs differ from "
          f"c-40's: {out['r1']['control_477']['inputs_differ_from_c40']}")
    print(f"exposed by result {e['exposed_by_result']}  unexposed {e['unexposed_by_result']}")
    print(f"flipping input of the 19: "
          f"{dict(Counter((r['result'], r['flipping_input']) for r in new_rows))}")
    for pop in POPS:
        q = pcts[pop]
        print(f"\n[{pop}] own_mean missed share {q['observed_missed_share']:.4f} sits at the "
              f"{100 * q['percentile']:.1f}th percentile of its permutation null "
              f"[{100 * q['wilson95'][0]:.1f}, {100 * q['wilson95'][1]:.1f}] "
              f"(null mean {q['null_mean_missed_share']:.4f}; at or below: "
              f"{100 * q['share_at_or_below']:.1f}%)")
        for t in tests:
            if t["population"] != pop:
                continue
            if t["lo"] is None or t["mde_realised"] is None:
                body_ = f"{t['note']}"
            else:
                pre = "none" if t["mde_pre"] is None else f"{t['mde_pre']:.3f}"
                body_ = (f"{t['estimate']:+.4f} [{t['lo']:+.3f}, {t['hi']:+.3f}] perm p "
                         f"{t['p']:.3f} holm {t['p_holm']:.2g}  MDE pre {pre} realised "
                         f"{t['mde_realised']:.3f}")
            print(f"  {t['slice']:22s} {t['input']:11s} n {t['n_missed']}/{t['n_cleared']} "
                  f"g {t['n_games']:2d}  {body_}")
    drift = [t for t in tests if t.get("mde_drift") and
             not (1 / MDE_DRIFT <= t["mde_drift"] <= MDE_DRIFT)]
    print(f"\nrealised MDE outside [1/1.5, 1.5] of the pre-stated one: {len(drift)} tests "
          f"{[(t['population'], t['slice'], t['input'], round(t['mde_drift'], 2)) for t in drift]}")
    print(f"family: {out['family']}")
    print(f"own-mean contrast over every assignment of the 19: [{lo:+.4f}, {hi:+.4f}]")
    print(f"verdict: {v}")
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
