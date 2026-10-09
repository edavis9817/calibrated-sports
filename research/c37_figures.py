"""c-48 - a committed computation of three figures c-37 published post hoc.

    set LOGGER_DB=D:/calibrated-sports/data/market_log.db   (opened mode=ro only)
    python -m research.c37_figures
    python -m research.c37_figures --scratch D:/temp/c37/predictions.csv

PRE-REGISTRATION: docs/C48-c37-figures-preregistration.md, committed at bea5737
BEFORE this script existed. This file implements it; it does not extend it.

EVERY FIGURE HERE IS ABOUT ONE MARKET: NFL receiving yards, over side, regular
season 2023-2025, the de-vigged DK / FD / MGM bench close at the book's own line.
The panel is rebuilt through research.yards_markets (c-37, e8ff867), IMPORTED and
never copied; no model is fitted that c-37 did not fit. This is a reproduction,
not a test: there is no null and no family.

Everything PRINTED or written is an aggregate or an interval (BET_LIST_RESTRICTION).
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import decomposed_usage as du  # noqa: E402  (the c-27 frame)
from research import ranking_calibration as rc  # noqa: E402  (platt_fit)
from research import yards_markets as ym  # noqa: E402  (c-37's model and rung loader)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREREG = "bea5737"
MARKET = "NFL receiving yards, over side, REG 2023-2025, de-vigged DK/FD/MGM bench close"
STAT = "receiving_yards"
BAND = (0.45, 0.55)
BOOT, SEED = 2000, 48
TOL = 0.0005                           # "rounds to the digits c-37 published"

# what c-37 published (report c-37.json, docs/findings/yards-markets.md)
PUBLISHED = {"share": 0.995, "sd": 0.010, "mean_model": 0.410, "over_rate": 0.479, "slope": 0.037}
C37_COUNTS = {"RB": 18666, "RB_player_games": 10016, "RB_games": 814,
              "RB_n_bench": {"1": 12432, "2": 4998, "3": 1236},
              "scored": {"2023": 20114, "2024": 22508, "2025": 11111}}
# the argument each figure carries, and the bar it survives at (pre-registered)
F1_SURVIVES_AT = 0.90
F3_SURVIVES_BELOW = 0.5

# the same quantity in other markets, READ from the unit that measured it
OTHER = (("receiving_yards", "c-41", "research/results/residual_given_line.json", ("descriptive", "RB")),
         ("receptions", "c-47", "research/results/residual_two_markets.json", ("descriptive", "receptions")),
         ("rush_attempts", "c-47", "research/results/residual_two_markets.json", ("descriptive", "rush_attempts")))


# =============================================================================
# the panel - c-37's own functions, in c-37's own order
# =============================================================================

def rebuild(con, out):
    """-> (RB rows, census, counts). The receiving-yards half of
    yards_markets.main(), without its bootstraps."""
    t0 = time.time()
    panel = du.Panel(con)
    fc = du.Forecaster(panel, None)
    out(f"panel: {len(panel.pg):,} player-games  ({time.time() - t0:.0f}s)")
    targets = ym.TEST_SEASONS
    rows = ym.build_rows(panel, fc, STAT, range(ym.FIRST_TRAIN, max(targets) + 1))
    out(f"frame: {len(rows):,} {STAT} rows {ym.FIRST_TRAIN}-{max(targets)}  ({time.time() - t0:.0f}s)")
    census, scored, rungs_all = Counter(), {}, []
    for T in targets:
        tab = ym.fit_tables([r for r in rows if r["season"] < T], T)
        sh = ym.fit_shapes([r for r in rows if T - ym.SHAPE_TRAIN_SPAN <= r["season"] < T], tab, T)
        frame = [r for r in rows if r["season"] == T]
        arms = ym.arms_for(frame, tab, sh)
        rungs = ym.load_rungs(con, panel, T, census)
        idx = {(r["gsis"], r["game"]): i for i, r in enumerate(frame)}
        keep = [r for r in rungs if (r["gsis"], r["game"]) in idx]
        census["settled but not in the frame (dropped)"] += len(rungs) - len(keep)
        ii = np.array([idx[(r["gsis"], r["game"])] for r in keep])
        line = np.array([r["line"] for r in keep])
        push = np.array([r["push"] for r in keep])
        prior = ym.naive_prior(panel, T)
        for arm in ym.ARMS:
            A = arms[arm]
            p = ym.prob_over({"fam": A["fam"], "pi0": A["pi0"][ii], "mup": A["mup"][ii],
                              "disp": A["disp"][ii]}, line, push)
            for r, v in zip(keep, p):
                r[arm] = float(v)
        for r in keep:
            r["half"] = 0.5
            r["prior"] = float(prior(r["gsis"], r["line"]))
            if (frame[idx[(r["gsis"], r["game"])]]["y"] > r["line"]) != bool(r["y"]):
                raise SystemExit("panel actual disagrees with settle_one - refusing")
        scored[str(T)] = len(keep)
        rungs_all += keep
        out(f"   T={T}: shapes on {sh['fit_seasons']}, {len(keep):,} rungs scored  ({time.time() - t0:.0f}s)")
    rb = [r for r in rungs_all if r["p_bench"] is not None]
    if not rb:
        raise SystemExit("RB is EMPTY - refusing to report a figure on no rows")
    nb = Counter(str(r["n_bench"]) for r in rb)
    counts = {"scored": scored, "scored_total": len(rungs_all), "RB": len(rb),
              "RB_player_games": len({(r["gsis"], r["game"]) for r in rb}),
              "RB_games": len({r["game"] for r in rb}), "RB_n_bench": dict(sorted(nb.items())),
              "RB_by_season": {str(T): sum(1 for r in rb if r["season"] == T) for T in targets},
              "integer_lines_RB": int(sum(1 for r in rb if float(r["line"]).is_integer()))}
    return rb, dict(census), counts


# =============================================================================
# the figures - pure
# =============================================================================

def concentration(p):
    """F1: share of prices inside BAND (bounds inclusive) and the population sd."""
    p = np.asarray(p, float)
    return float(np.mean((p >= BAND[0]) & (p <= BAND[1]))), float(p.std())


def platt_slope(m, y):
    """F3: (intercept, slope) of the outcome on logit(model), in sample."""
    _f, (a, b) = rc.platt_fit(np.asarray(m, float), np.asarray(y, float))
    return float(a), float(b)


def figures(rb, arm="Y"):
    p = np.array([r["p_bench"] for r in rb], float)
    m = np.array([r[arm] for r in rb], float)
    y = np.array([r["y"] for r in rb], float)
    share, sd = concentration(p)
    a, b = platt_slope(m, y)
    return {"share": share, "sd": sd, "mean_model": float(m.mean()), "over_rate": float(y.mean()),
            "gap": float((m - y).mean()), "intercept": a, "slope": b, "n": len(rb)}


def boot(rb, arm="Y", draws=None, seed=None):
    """Game-block bootstrap of the six registered quantities, one set of draws."""
    draws = BOOT if draws is None else draws
    seed = SEED if seed is None else seed
    p = np.array([r["p_bench"] for r in rb], float)
    m = np.array([r[arm] for r in rb], float)
    y = np.array([r["y"] for r in rb], float)
    by = {}
    for i, r in enumerate(rb):
        by.setdefault(r["game"], []).append(i)
    gidx = [np.array(v) for _g, v in sorted(by.items())]
    G = len(gidx)
    names = ("share", "sd", "mean_model", "over_rate", "gap", "slope")
    if G < 2:
        return {k: {"lo": None, "hi": None, "se": None, "games": G} for k in names}
    rng = np.random.default_rng(seed)
    vals = np.empty((draws, len(names)))
    for d in range(draws):
        i = np.concatenate([gidx[j] for j in rng.integers(0, G, G)])
        sh, sd = concentration(p[i])
        vals[d] = (sh, sd, m[i].mean(), y[i].mean(), (m[i] - y[i]).mean(), platt_slope(m[i], y[i])[1])
    return {k: {"lo": float(np.percentile(vals[:, j], 2.5)), "hi": float(np.percentile(vals[:, j], 97.5)),
                "se": float(vals[:, j].std()), "games": G} for j, k in enumerate(names)}


def status(x, published, tol=None):
    tol = TOL if tol is None else tol
    return "REPRODUCED" if abs(x - published) < tol else "MOVED"


def verdicts(fig, iv):
    """The registered reading of each figure. Returns the statement, not a boolean."""
    rows = {k: {"value": fig[k], "published": PUBLISHED[k], "diff": fig[k] - PUBLISHED[k],
                "status": status(fig[k], PUBLISHED[k])} for k in PUBLISHED}
    both = lambda *ks: "REPRODUCED" if all(rows[k]["status"] == "REPRODUCED" for k in ks) else "MOVED"  # noqa: E731
    gap_hi, slope_hi = iv["gap"]["hi"], iv["slope"]["hi"]
    return {"rows": rows,
            "F1": {"status": both("share", "sd"),
                   "argument": "the close has too little price variation for a resolution statistic",
                   "argument_survives": bool(fig["share"] >= F1_SURVIVES_AT), "bar": f"share >= {F1_SURVIVES_AT}"},
            "F2": {"status": both("mean_model", "over_rate"),
                   "argument": "the model sits below the realised over rate at the book's line",
                   "argument_survives": bool(gap_hi is not None and gap_hi < 0),
                   "bar": "95% interval of mean(model - outcome) wholly below 0"},
            "F3": {"status": both("slope"),
                   "argument": "the model's disagreement with the line carries almost no information",
                   "argument_survives": bool(slope_hi is not None and slope_hi < F3_SURVIVES_BELOW),
                   "bar": f"95% interval of the Platt slope wholly below {F3_SURVIVES_BELOW}"}}


def population_check(counts):
    diffs = {}
    for k in ("RB", "RB_player_games", "RB_games", "RB_n_bench", "scored"):
        if counts[k] != C37_COUNTS[k]:
            diffs[k] = {"c37": C37_COUNTS[k], "now": counts[k]}
    return {"same_as_c37": not diffs, "differences": diffs}


def other_markets(root=None):
    """The same share in other markets, read by key from the unit that measured
    it. Refuses on a missing file or key: an absent comparison must not print."""
    root = REPO if root is None else root
    out = []
    for market, unit, rel, path in OTHER:
        node = json.load(open(os.path.join(root, rel)))
        for k in path:
            if k not in node:
                raise SystemExit(f"{rel} has no {'/'.join(path)} - refusing to print a market comparison")
            node = node[k]
        for k in ("share_p_in_45_55", "sd_p", "n"):
            if k not in node:
                raise SystemExit(f"{rel}:{'/'.join(path)} has no {k} - refusing")
        out.append({"market": market, "measured_by": unit, "file": rel, "share": node["share_p_in_45_55"],
                    "sd": node["sd_p"], "n": node["n"]})
    return out


def compare_scratch(rb, path):
    """Regenerated RB against c-37's scratch predictions, row for row."""
    if not path:
        return {"state": "not requested"}
    if not os.path.exists(path):
        return {"state": "ABSENT", "path": path}
    key = lambda s, g, gs, ln: (int(s), str(g), str(gs), round(float(ln), 4))  # noqa: E731
    old, total = {}, 0
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            total += 1
            if r["p_bench"] not in ("", "None"):
                old[key(r["season"], r["game"], r["gsis"], r["line"])] = r
    new = {key(r["season"], r["game"], r["gsis"], r["line"]): r for r in rb}
    if len(new) != len(rb):
        raise SystemExit("(season, game, gsis, line) is not unique on the regenerated RB - refusing to compare")
    shared = sorted(set(old) & set(new))
    res = {"state": "COMPARED", "path": path, "scratch_rows": total, "scratch_bench_rows": len(old),
           "regenerated_bench_rows": len(new), "shared": len(shared),
           "only_scratch": len(set(old) - set(new)), "only_regenerated": len(set(new) - set(old)),
           "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(path)))}
    for col in ("Y", "S0", "S1", "p_bench", "y"):
        res[f"max_abs_diff_{col}"] = (float(max(abs(float(old[k][col]) - float(new[k][col])) for k in shared))
                                      if shared else None)
    res["identical_keys"] = bool(shared and not res["only_scratch"] and not res["only_regenerated"])
    return res


# =============================================================================
# main
# =============================================================================

def _commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance only, never a reason to fail the run
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", default=None, help="c-37's predictions.csv, for a row-for-row comparison")
    ap.add_argument("--results", default=os.path.join(REPO, "research", "results", "c37_figures.json"))
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)
    t0 = time.time()
    import config
    out(f"c-48 / c37_figures  prereg {PREREG}  commit {_commit()}  store {config.DB_PATH} (mode=ro)")
    out(f"MARKET: {MARKET}")
    others = other_markets()                      # refuse BEFORE the slow part, not after it
    con = du.ro()
    rb, census, counts = rebuild(con, out)
    con.close()

    out("\nPOPULATION (every count; compare these on a re-run)")
    for k, v in sorted(census.items()):
        out(f"  {k:<58} {v:>8,}")
    out(f"  scored by season {counts['scored']}  total {counts['scored_total']:,}")
    out(f"  RB {counts['RB']:,} rungs / {counts['RB_player_games']:,} player-games / {counts['RB_games']} games"
        f"   by season {counts['RB_by_season']}   by n_bench {counts['RB_n_bench']}"
        f"   integer lines {counts['integer_lines_RB']}")
    pc = population_check(counts)
    out(f"  SAME POPULATION AS c-37 (18,666 / 10,016 / 814, n_bench and scored counts): "
        f"{'YES' if pc['same_as_c37'] else 'NO - MOVED: ' + json.dumps(pc['differences'])}")

    fig = figures(rb)
    iv = boot(rb)
    v = verdicts(fig, iv)
    out(f"\nTHE THREE FIGURES - {MARKET}")
    out(f"  game-block bootstrap, {BOOT} draws, seed {SEED}, {iv['share']['games']} games; "
        f"reproduced = within {TOL} of the published digits")
    lab = {"share": "F1 share of p_bench in [0.45, 0.55]", "sd": "F1 sd of p_bench",
           "mean_model": "F2 mean model P(over), arm Y", "over_rate": "F2 realised over rate",
           "slope": "F3 Platt slope, arm Y"}
    for k in ("share", "sd", "mean_model", "over_rate", "slope"):
        r = v["rows"][k]
        out(f"  {lab[k]:<38} {r['value']:.6f} [{iv[k]['lo']:.6f}, {iv[k]['hi']:.6f}]   "
            f"published {r['published']:.3f}   diff {r['diff']:+.6f}   {r['status']}")
    out(f"  {'   mean(model - outcome)':<38} {fig['gap']:+.6f} [{iv['gap']['lo']:+.6f}, {iv['gap']['hi']:+.6f}]")
    out(f"  {'   Platt intercept':<38} {fig['intercept']:+.6f}")
    for f in ("F1", "F2", "F3"):
        out(f"  {f}: {v[f]['status']}.  '{v[f]['argument']}' "
            f"{'SURVIVES' if v[f]['argument_survives'] else 'DOES NOT SURVIVE'} ({v[f]['bar']})")

    out("\nDESCRIPTIVE (declared, not figures of record)")
    desc = {"arms": {}, "by_season": {}, "by_n_bench": {}}
    y = np.array([r["y"] for r in rb], float)
    for arm in ("S0", "S1", "Y", "Y-lognormal", "Y-weibull", "half", "prior", "p_bench"):
        m = np.array([r[arm] for r in rb], float)
        desc["arms"][arm] = {"mean": float(m.mean())}
        if arm in ym.ARMS:
            desc["arms"][arm]["platt_slope"] = platt_slope(m, y)[1]
        out(f"  mean {arm:<12} {m.mean():.4f}" + (f"   Platt slope {desc['arms'][arm]['platt_slope']:+.4f}"
                                                 if arm in ym.ARMS else ""))
    for name, keyf in (("by_season", lambda r: str(r["season"])), ("by_n_bench", lambda r: str(r["n_bench"]))):
        for g in sorted({keyf(r) for r in rb}):
            sub = [r for r in rb if keyf(r) == g]
            sh, sd = concentration([r["p_bench"] for r in sub])
            desc[name][g] = {"n": len(sub), "share": sh, "sd": sd}
            out(f"  F1 {name[3:]:<8} {g:<5} n {len(sub):>6,}   share {sh:.4f}   sd {sd:.4f}")

    out("\nTHE SAME SHARE IN OTHER MARKETS - read from the unit that measured it, NOT recomputed here.")
    out("F1 is a fact about receiving yards. It does not carry to the rows below.")
    out(f"  {'receiving_yards':<16} this run   share {fig['share']:.4f}   sd {fig['sd']:.4f}   n {fig['n']:,}")
    for o in others:
        out(f"  {o['market']:<16} {o['measured_by']:<9}  share {o['share']:.4f}   sd {o['sd']:.4f}   n {o['n']:,}"
            f"   ({o['file']})")

    sc = compare_scratch(rb, a.scratch)
    out(f"\nSCRATCH COMPARISON: {json.dumps(sc)}")
    out(f"\nruntime {time.time() - t0:.0f}s")
    result = {"unit": "c-48", "preregistration": PREREG, "commit": _commit(), "market": MARKET,
              "store": str(config.DB_PATH), "published_by_c37": PUBLISHED, "tolerance": TOL,
              "census": census, "counts": counts, "population_check": pc, "figures": fig, "intervals": iv,
              "verdicts": v, "descriptive": desc, "other_markets": others, "scratch": sc,
              "boot": BOOT, "seed": SEED, "band": list(BAND)}
    with open(a.results, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    with open(os.path.splitext(a.results)[0] + ".log", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
