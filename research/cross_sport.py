"""c-38 - does the game model's advantage appear in both sports.

    LOGGER_DB=<market_log.db> python -m research.cross_sport \
        --json-out research/results/cross_sport.json --log-out research/results/cross_sport.log

PRE-REGISTRATION: docs/C38-cross-sport-preregistration.md, committed and pushed
at dbd3d29 BEFORE this script existed. This file implements it; it does not
extend it. Comments say only where the code carries a rule out.

Nothing is refitted that c-28 or c-39 did not fit: the NFL side is
`research.game_forecast` (c-28) and the college side `research.cfb_game_forecast`
(c-39), imported. Both published Part 1 results must reproduce to 4 dp before
any cross-sport figure is printed, or the run stops.

LOGGER_DB must name market_log.db (this clone's .env points it elsewhere on
purpose); it and cfb.db are opened mode=ro. Nothing is written except
--json-out and --log-out. Everything printed is an aggregate or an interval.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import cfb_game as C                         # noqa: E402
from models import game as G                             # noqa: E402
from research import ranking_calibration as rc           # noqa: E402

BASES = ("home", "record", "elo_nomov")
SEED_OWN = rc.SEED                 # 24: each sport's own interval is the published one
SEED_NFL, SEED_CFB = 3801, 3802    # between-sport draws: independent per sport
ALPHA = 0.05
BIN_EDGES = np.array([0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85])     # eight bins of max(p, 1-p)
N_BINS = len(BIN_EDGES) + 1
TOL = 0.00005 + 1e-9               # "to 4 dp"

PUBLISHED = {"nfl": {"home": -0.0255, "record": -0.0162, "elo_nomov": -0.0027},
             "cfb": {"home": -0.0615, "record": -0.0380, "elo_nomov": -0.0042}}
PUBLISHED_N = {"nfl": 6743, "cfb": 15508}
PUBLISHED_PRICE = {"nfl": (5281, 0.0092), "cfb": (3768, 0.0107)}
# the 2026 fits the transfer arms carry, as registered; the run stops if a walk disagrees
NFL_FIT = {"mov": {"k": 20.0, "hfa": 50.0, "regress": 0.5},
           "nomov": {"k": 40.0, "hfa": 50.0, "regress": 1.0 / 3.0}}
CFB_FIT = {"mov": {"k": 40.0, "hfa": 55.0, "regress": 0.4, "a": None, "cap": None,
                   "conf_w": 1.0, "entry": -300.0},
           "nomov": {"k": 80.0, "hfa": 55.0, "regress": 0.3, "a": None, "cap": None,
                     "conf_w": 1.0, "entry": -300.0}}
MDE_REGISTERED = {"home": 0.0065, "record": 0.0055, "elo_nomov": 0.0016}

PRICE_SENTENCE = (
    "The NFL comparator is the nflverse closing line, a sportsbook close of unrecorded provenance. "
    "The college comparator is CFBD's last value per provider, read after the game, with no capture "
    "time - not a close. Different books, different seasons (2006-2025 against 2021-2025), different "
    "base uncertainty; and if any college value is in-game it favours the price. The two losses are "
    "shown side by side and are not a measurement of which market is harder to beat.")


# =============================================================================
# the table: one row per game, sorted by game id (c-24's block order)
# =============================================================================

class Tab:
    """Squared errors per forecast column plus the cut fields, one row a game."""

    def __init__(self, rows, cols):
        rows = sorted(rows, key=lambda r: r["game"])
        if len({r["game"] for r in rows}) != len(rows):
            raise ValueError("a game appears twice")
        self.rows, self.cols, self.n = rows, tuple(cols), len(rows)
        self.y = np.array([r["y"] for r in rows], float)
        self.p = {c: np.array([r[c] for r in rows], float) for c in cols}
        self.E = np.column_stack([(self.p[c] - self.y) ** 2 for c in cols])
        f = np.maximum(self.p["model"], 1.0 - self.p["model"])
        self.f = f
        self.bin = np.searchsorted(BIN_EDGES, f, side="right")

    def subset(self, keep):
        return Tab([r for r in self.rows if keep(r)], self.cols)


def core(mean):
    """Every registered statistic that is a function of the column Brier means."""
    r = {}
    for b in BASES:
        r["d_" + b] = mean["model"] - mean[b]
        r["s_" + b] = 1.0 - mean["model"] / mean[b]
    r["c1"] = r["d_home"] - r["d_record"]
    r["c2"] = r["d_record"] - r["d_elo_nomov"]
    if "arm" in mean:
        for b in BASES:
            r["arm_vs_" + b] = mean["arm"] - mean[b]
        r["arm_vs_arm0"] = mean["arm"] - mean["arm0"]
    if "price" in mean:
        r["d_price"] = mean["model"] - mean["price"]
    return r


def _stats(tab, idx, bins):
    m = tab.E[idx].mean(axis=0)
    r = core(dict(zip(tab.cols, m)))
    if bins:
        bi = tab.bin[idx]
        cnt = np.bincount(bi, minlength=N_BINS).astype(float)
        r["share"] = cnt / len(idx)
        j = tab.cols.index("model")
        with np.errstate(invalid="ignore", divide="ignore"):
            for b in BASES:
                d = tab.E[idx, j] - tab.E[idx, tab.cols.index(b)]
                r["dbin_" + b] = np.bincount(bi, weights=d, minlength=N_BINS) / cnt
    return r


def boot(tab, seed, draws, bins=False):
    """(estimates, {name: array over draws}). The draw sequence is c-24's
    Pop.boot: rng.integers(0, G, G) per draw over games in sorted-id order."""
    est = _stats(tab, np.arange(tab.n), bins)
    rng = np.random.default_rng(seed)
    acc = {k: [] for k in est}
    for _ in range(draws):
        s = _stats(tab, rng.integers(0, tab.n, tab.n), bins)
        for k, v in s.items():
            acc[k].append(v)
    return est, {k: np.array(v) for k, v in acc.items()}


def interval(est, vals, games):
    vals = np.asarray(vals, float)
    vals = vals[~np.isnan(vals)]
    if games < rc.MIN_GAMES or len(vals) < 2:
        return {"est": est, "lo": None, "hi": None, "se": None, "games": games, "p": 1.0,
                "mde": None, "draws": int(len(vals))}
    se = float(vals.std())
    p = 1.0 if se <= 1e-12 else float(2.0 * norm.sf(abs(est) / se))  # z = est / bootstrap SE
    return {"est": float(est), "lo": float(np.percentile(vals, 2.5)),
            "hi": float(np.percentile(vals, 97.5)), "se": se, "games": games, "p": p,
            "mde": 2.8 * se, "draws": int(len(vals))}


def holm(ps):
    """Holm step-down adjusted p-values, in the input order."""
    ps = np.asarray(ps, float)
    order = np.argsort(ps, kind="stable")
    adj = np.empty(len(ps))
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj.tolist()


def matched(est_n, dr_n, est_c, dr_c, b):
    """College d_b at the NFL's bin shares, minus the NFL's d_b. A draw whose
    college resample has no game in a bin the NFL resample weights is dropped."""
    def one(w, dbin, d_nfl):
        need = w > 0
        if np.isnan(dbin[need]).any():
            return np.nan
        return float((w[need] * dbin[need]).sum() - d_nfl)
    e = one(est_n["share"], est_c["dbin_" + b], est_n["d_" + b])
    v = np.array([one(dr_n["share"][j], dr_c["dbin_" + b][j], dr_n["d_" + b][j])
                  for j in range(len(dr_n["d_" + b]))])
    return e, v


def verdict(signs, holm_ok):
    """The registered SUCCESS rule. `signs` and `holm_ok` are
    {sport: {test: 'below' / ... }} and {sport: {test: bool}} over d_home,
    d_record, d_elo_nomov, c1, c2. -> (success, [failures named])."""
    fails = []
    for sport in ("nfl", "cfb"):
        for t in ("d_home", "d_record", "d_elo_nomov", "c1", "c2"):
            if signs[sport][t] != "below" or not holm_ok[sport][t]:
                fails.append((sport, t, signs[sport][t], bool(holm_ok[sport][t])))
    return (not fails), fails


def one_sport_only(fails):
    """Tests that hold in one sport and fail in the other - the suspected artefacts."""
    bad = {}
    for sport, t, *_ in fails:
        bad.setdefault(t, set()).add(sport)
    return {t: ("cfb" if s == {"nfl"} else "nfl") for t, s in bad.items() if len(s) == 1}


# =============================================================================
# loading: c-28's NFL Part 1 and c-39's college Part 1, as their scripts build them
# =============================================================================

def _same(got, want):
    return all((got[k] is None and want[k] is None) or
               (got[k] is not None and want[k] is not None and abs(got[k] - want[k]) < 1e-9)
               for k in want)


def load_nfl(out):
    from jobs import season_model as S
    from research import game_forecast as F
    con = S.market_log_ro()
    games, _grp, versions = S.load(con)
    grid = S.grid()
    walk = F.Walk(games, S.game_losses(games, grid), mov=True)
    walk0 = F.Walk(games, F.nomov_losses(games, grid), mov=False)
    signs = F.record_signs(games)
    pop1 = [i for i, g in enumerate(games)
            if F.SCORE_FROM <= g["season"] <= F.SCORE_TO and F.scored(g)
            and g["home_score"] != g["away_score"]]
    years = list(range(F.SCORE_FROM, F.SCORE_TO + 1)) + [F.KALSHI_SEASON]
    consts = {y: F.baseline_constants(games, signs, y) for y in years}
    fit = {"mov": walk.params(F.KALSHI_SEASON).as_dict(), "nomov": walk0.params(F.KALSHI_SEASON).as_dict()}
    if not (_same(fit["mov"], NFL_FIT["mov"]) and _same(fit["nomov"], NFL_FIT["nomov"])):
        raise SystemExit("NFL 2026 fit %r is not the registered %r" % (fit, NFL_FIT))

    # arm R: college's 2026 constants walked over the NFL games (conference none)
    as_cfb = [{"game_id": g["game_id"], "season": g["season"], "start_ts": g["kickoff_ts"] or 0.0,
               "home": G.franchise(g["home"]), "away": G.franchise(g["away"]),
               "home_conf": None, "away_conf": None, "neutral": 0,
               "home_score": g["home_score"], "away_score": g["away_score"]} for g in games]
    arm = dict(C.run(as_cfb, C.CfbParams(**CFB_FIT["mov"]), mov=True)[0])
    arm0 = dict(C.run(as_cfb, C.CfbParams(**CFB_FIT["nomov"]), mov=False)[0])

    price = {}
    for i in pop1:
        g = games[i]
        if g["season"] < F.ML_FROM:
            continue
        raw = con.execute("SELECT home_moneyline, away_moneyline FROM nfl_games WHERE game_id = ? "
                          "AND data_version = ?", (g["game_id"], g["data_version"])).fetchone()
        if raw is None or raw[0] is None or raw[1] is None:
            continue
        ih, ia = F.american(raw[0]), F.american(raw[1])
        price[i] = ih / (ih + ia)
    con.close()

    rows = []
    for i in pop1:
        g = games[i]
        h, q = consts[g["season"]]
        s = signs[i]
        rows.append({"game": g["game_id"], "season": g["season"], "week": g["week"],
                     "regular": g["game_type"] == "REG", "neutral": False, "conference": None,
                     "y": 1.0 if g["home_score"] > g["away_score"] else 0.0,
                     "model": walk.p(i), "home": h,
                     "record": h if s == 0 else (q if s > 0 else 1.0 - q),
                     "elo_nomov": walk0.p(i), "arm": arm[i], "arm0": arm0[i],
                     "price": price.get(i)})
    out(f"NFL: games loaded {len(games):,}, Part 1 population {len(rows):,}; versions {versions}")
    return rows, {"versions": versions, "fit_2026": fit, "priced": len(price)}


def load_cfb(out):
    from research import cfb_game_forecast as R
    con = R.connect()
    games = R.load(con)
    signs = R.record_signs(con)
    done = [i for i, g in enumerate(games) if C.completed(g)]
    t0 = time.time()
    walk = R.Walk(games, R.grid(R.GRID_MOV), mov=True)
    walk0 = R.Walk(games, R.grid(R.GRID_PLAIN), mov=False)
    out(f"college: FBS-FBS games loaded {len(games):,}, completed {len(done):,}; grids {time.time() - t0:.0f}s")
    years = list(range(R.SCORE_FROM, R.CURRENT + 1))
    consts = {y: R.baseline_constants(games, signs, y) for y in years}
    fit = {"mov": walk.params(R.CURRENT).as_dict(), "nomov": walk0.params(R.CURRENT).as_dict()}
    if not (_same(fit["mov"], CFB_FIT["mov"]) and _same(fit["nomov"], CFB_FIT["nomov"])):
        raise SystemExit("college 2026 fit %r is not the registered %r" % (fit, CFB_FIT))

    # arm N (c-39's, unchanged) and arm N0: the NFL's 2026 constants walked over college
    arm = dict(C.run(games, C.CfbParams(a=G.MOV_A, cap=None, conf_w=0.0, entry=0.0, **NFL_FIT["mov"]),
                     mov=True, hfa_on_neutral=True)[0])
    arm0 = dict(C.run(games, C.CfbParams(a=G.MOV_A, cap=None, conf_w=0.0, entry=0.0, **NFL_FIT["nomov"]),
                      mov=False, hfa_on_neutral=True)[0])
    ml, _sp, census = R.cfbd_lines(con)
    oc, ocen = R.odds_api_closes(con)
    con.close()

    def row(i, price):
        g = games[i]
        h0, h1, q = consts[g["season"]]
        h = h1 if g["neutral"] else h0
        s = signs.get(g["game_id"], 0)
        return {"game": g["game_id"], "season": g["season"], "week": g["week"],
                "regular": g["season_type"] == "regular", "neutral": bool(g["neutral"]),
                "conference": bool(g["conference_game"]),
                "y": 1.0 if g["home_score"] > g["away_score"] else 0.0,
                "model": walk.p(i), "home": h,
                "record": h if s == 0 else (q if s > 0 else 1.0 - q),
                "elo_nomov": walk0.p(i), "arm": arm[i], "arm0": arm0[i], "price": price}

    rows = [row(i, ml.get(games[i]["game_id"]) if games[i]["season"] >= R.ML_FROM else None)
            for i in done if R.SCORE_FROM <= games[i]["season"] <= R.SCORE_TO]
    cur = [row(i, oc[games[i]["game_id"]][0]) for i in done
           if games[i]["season"] == R.CURRENT and games[i]["game_id"] in oc]
    return rows, cur, {"fit_2026": fit, "cfbd_census": census, "odds_api_census": ocen}


# =============================================================================
# main
# =============================================================================

COLS = ("model",) + BASES + ("arm", "arm0")

CUTS = [
    ("1 common window 2005-2025", lambda r: 2005 <= r["season"] <= 2025, lambda r: True),
    ("2 regular season only", lambda r: r["regular"], lambda r: r["regular"]),
    ("3 regular weeks 1-4", lambda r: r["regular"] and r["week"] <= 4, lambda r: r["regular"] and r["week"] <= 4),
    ("4 regular weeks 5+", lambda r: r["regular"] and r["week"] > 4, lambda r: r["regular"] and r["week"] > 4),
    ("5 college non-neutral only", lambda r: True, lambda r: not r["neutral"]),
    ("6 college conference games only", lambda r: True, lambda r: r["conference"]),
    ("7 2005-2014", lambda r: 2005 <= r["season"] <= 2014, lambda r: r["season"] <= 2014),
    ("8 2015-2025", lambda r: r["season"] >= 2015, lambda r: r["season"] >= 2015),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--log-out")
    ap.add_argument("--draws", type=int, default=rc.BOOT)
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    def show(label, r, d=4):
        out(f"   {label:<46} {rc.fmt(r, d)}  MDE {r['mde']:.4f}  -> {rc.sign(r)}" if r["mde"] is not None
            else f"   {label:<46} {rc.fmt(r, d)}  -> {rc.sign(r)}")

    result = {"preregistration": "docs/C38-cross-sport-preregistration.md @ dbd3d29", "draws": a.draws,
              "seeds": {"own": SEED_OWN, "nfl": SEED_NFL, "cfb": SEED_CFB}}
    nfl_rows, nfl_meta = load_nfl(out)
    cfb_rows, cfb_cur, cfb_meta = load_cfb(out)
    result["nfl"], result["cfb"] = nfl_meta, cfb_meta
    tabs = {"nfl": Tab(nfl_rows, COLS), "cfb": Tab(cfb_rows, COLS)}

    # ---------------------------------------------------------------- reproduction gate
    own = {s: boot(tabs[s], SEED_OWN, a.draws) for s in tabs}
    out("\n######## REPRODUCTION GATE - each sport's published Part 1, to 4 dp")
    for s in ("nfl", "cfb"):
        if tabs[s].n != PUBLISHED_N[s]:
            raise SystemExit(f"{s}: population {tabs[s].n:,} is not the published {PUBLISHED_N[s]:,} - stopping")
        for b in BASES:
            got = own[s][0]["d_" + b]
            ok = abs(got - PUBLISHED[s][b]) <= TOL
            out(f"   {s} vs {b:<10} dBrier {got:+.5f}  published {PUBLISHED[s][b]:+.4f}  {'ok' if ok else 'MISMATCH'}")
            if not ok:
                raise SystemExit(f"{s} vs {b}: {got:+.5f} does not reproduce {PUBLISHED[s][b]:+.4f} - stopping")

    # ---------------------------------------------------------------- PRIMARY
    out("\n######## PRIMARY - against settlement, each sport's own registered population, game blocks")
    prim, family = {}, []
    for s in ("nfl", "cfb"):
        est, dr = own[s]
        t = tabs[s]
        prim[s] = {"n": t.n, "realized": float(t.y.mean()),
                   "brier": {c: float(t.E[:, j].mean()) for j, c in enumerate(t.cols)},
                   "corp": {c: rc.corp(t.p[c], t.y) for c in ("model",) + BASES},
                   "auc": {c: rc.auc(t.p[c], t.y) for c in ("model",) + BASES}, "tests": {}, "skill": {}}
        out(f"\n== {s}: n {t.n:,}  realized home-win rate {t.y.mean():.4f}  mean favourite p {t.f.mean():.4f}")
        for c in ("model",) + BASES:
            cp = prim[s]["corp"][c]
            out(f"   {c:<10} BS {cp['bs']:.4f} = MCB {cp['mcb']:.4f} - DSC {cp['dsc']:.4f} + UNC {cp['unc']:.4f}"
                f"   DSC/UNC {cp['dsc'] / cp['unc']:.4f}   AUC {prim[s]['auc'][c]:.4f}")
        for name in ("d_home", "d_record", "d_elo_nomov", "c1", "c2"):
            r = interval(est[name], dr[name], t.n)
            prim[s]["tests"][name] = r
            family.append((s, name, r))
            show({"c1": "c1 = d_home - d_record", "c2": "c2 = d_record - d_elo_nomov"}.get(name, name), r)
        for b in BASES:
            r = interval(est["s_" + b], dr["s_" + b], t.n)
            prim[s]["skill"][b] = r
            show(f"skill 1 - BS(model)/BS({b})", r)

    ind = {"nfl": boot(tabs["nfl"], SEED_NFL, a.draws, bins=True),
           "cfb": boot(tabs["cfb"], SEED_CFB, a.draws, bins=True)}
    G2 = min(tabs["nfl"].n, tabs["cfb"].n)
    out("\n== between sports (college - NFL), independent draws per sport")
    between = {}
    for b in BASES:
        for key, lab in (("d_", "D"), ("s_", "Ds")):
            r = interval(ind["cfb"][0][key + b] - ind["nfl"][0][key + b],
                         ind["cfb"][1][key + b] - ind["nfl"][1][key + b], G2)
            between[f"{lab}_{b}"] = r
            family.append(("between", f"{lab}_{b}", r))
            show(f"{lab}_{b}" + (f"  (registered MDE {MDE_REGISTERED[b]:.4f})" if lab == "D" else ""), r)
    adj = holm([r["p"] for *_x, r in family])
    for (_s, _n, r), pa in zip(family, adj):
        r["p_holm"] = pa
        r["holm_significant"] = bool(pa < ALPHA)
    signs = {s: {n: rc.sign(prim[s]["tests"][n]) for n in prim[s]["tests"]} for s in ("nfl", "cfb")}
    hok = {s: {n: prim[s]["tests"][n]["holm_significant"] for n in prim[s]["tests"]} for s in ("nfl", "cfb")}
    success, fails = verdict(signs, hok)
    lone = one_sport_only(fails)
    result["primary"] = {"per_sport": prim, "between": between, "success": success,
                         "failures": [list(f) for f in fails], "one_sport_only": lone,
                         "holm_family_size": len(family),
                         "holm_significant": sum(1 for *_x, r in family if r["holm_significant"])}
    out(f"\n   Holm family of {len(family)}: {result['primary']['holm_significant']} significant at {ALPHA}")
    for s_, n_, r in family:
        out(f"      {s_:<8} {n_:<14} p {r['p']:.2e}  Holm {r['p_holm']:.2e}  {'*' if r['holm_significant'] else ' '}")
    out(f"\n   PRIMARY VERDICT: {'SUCCESS - same sign and same ordering in both sports' if success else 'NOT in both sports'}")
    for sport, t, sg, hk in fails:
        out(f"      fails: {sport} {t}: interval {sg}, Holm {'ok' if hk else 'not significant'}")
    for t, sport in lone.items():
        out(f"      {t} holds in {sport} only - the one-sport version is suspected of being a fitted artefact")

    # ---------------------------------------------------------------- M: matched on forecast strength
    out("\n######## M - matched on the model's favourite probability (college at the NFL's bin shares)")
    lo_edges = [0.5] + BIN_EDGES.tolist()
    mres = {"bins": [], "tests": {}}
    for k in range(N_BINS):
        row = {"bin": f"[{lo_edges[k]:.2f}, {(lo_edges[k + 1] if k + 1 < N_BINS else 1.0):.2f}{')' if k + 1 < N_BINS else ']'}"}
        for s in ("nfl", "cfb"):
            t = tabs[s]
            m = t.bin == k
            row[s] = {"n": int(m.sum()), "share": float(m.mean()),
                      "mean_f": float(t.f[m].mean()) if m.any() else None,
                      "d": {b: float(ind[s][0]["dbin_" + b][k]) if m.any() else None for b in BASES}}
        mres["bins"].append(row)
        out("   %-13s NFL n %5d share %.3f f %s | college n %5d share %.3f f %s | d_home %s / %s  d_record %s / %s  d_elo %s / %s" % (
            row["bin"], row["nfl"]["n"], row["nfl"]["share"],
            "%.3f" % row["nfl"]["mean_f"] if row["nfl"]["mean_f"] is not None else "  -  ",
            row["cfb"]["n"], row["cfb"]["share"],
            "%.3f" % row["cfb"]["mean_f"] if row["cfb"]["mean_f"] is not None else "  -  ",
            *["%+.4f" % row[s]["d"][b] if row[s]["d"][b] is not None else "   -   "
              for b in BASES for s in ("nfl", "cfb")]))
    mfam = []
    for b in BASES:
        e, v = matched(ind["nfl"][0], ind["nfl"][1], ind["cfb"][0], ind["cfb"][1], b)
        r = interval(e, v, G2)
        r["dropped_draws"] = int(np.isnan(v).sum())
        mres["tests"][b] = r
        mfam.append(r)
        show(f"Dm_{b} (matched college - NFL)", r)
        raw, sg = rc.sign(between[f"D_{b}"]), rc.sign(r)
        if raw in ("below", "above") and sg == "contains 0":
            read = "size difference accounted for by the wider spread of forecast strength, to within the MDE"
        elif sg == raw and sg in ("below", "above"):
            read = "not accounted for by the spread alone"
        else:
            read = f"neither registered reading (raw {raw}, matched {sg})"
        r["reading"] = read
        out(f"      reading: {read}")
    for r, pa in zip(mfam, holm([r["p"] for r in mfam])):
        r["p_holm"], r["holm_significant"] = pa, bool(pa < ALPHA)
    result["matched"] = mres

    # ---------------------------------------------------------------- T: transfer
    out("\n######## T - transfer: the other sport's 2026 constants carried across, nothing fitted here")
    tres, tfam = {}, []
    for s, label in (("cfb", "arm N: NFL constants over college"), ("nfl", "arm R: college constants over the NFL")):
        est, dr = own[s]
        t = tabs[s]
        j, j0 = t.cols.index("arm"), t.cols.index("arm0")
        tres[s] = {"label": label, "brier_arm": float(t.E[:, j].mean()), "brier_arm0": float(t.E[:, j0].mean()),
                   "tests": {}}
        out(f"\n== {label}: Brier carried MOV {tres[s]['brier_arm']:.4f}, carried plain {tres[s]['brier_arm0']:.4f}, "
            f"fitted model {prim[s]['brier']['model']:.4f}")
        for name in ("arm_vs_home", "arm_vs_record", "arm_vs_elo_nomov", "arm_vs_arm0"):
            r = interval(est[name], dr[name], t.n)
            tres[s]["tests"][name] = r
            tfam.append(r)
            show({"arm_vs_elo_nomov": "carried MOV - FITTED plain Elo (not like-for-like)",
                  "arm_vs_arm0": "carried MOV - carried plain Elo (like-for-like)"}.get(name, "carried MOV - " + name[7:]), r)
    for r, pa in zip(tfam, holm([r["p"] for r in tfam])):
        r["p_holm"], r["holm_significant"] = pa, bool(pa < ALPHA)
    for s in ("cfb", "nfl"):
        tt = tres[s]["tests"]
        ok = {n: rc.sign(tt[n]) == "below" and tt[n]["holm_significant"]
              for n in ("arm_vs_home", "arm_vs_record", "arm_vs_arm0")}
        if all(ok.values()):
            read = "the advantage is not a product of the per-sport fit"
        else:
            bad = [n for n in ("arm_vs_home", "arm_vs_record") if not ok[n]]
            read = ("suspected of being fitted: " + ", ".join(bad)) if bad else \
                "beats home and record carried; the margin-of-victory gain over a carried plain Elo is not shown"
        tres[s]["reading"] = read
        out(f"   reading, {tres[s]['label']}: {read}")
    result["transfer"] = tres

    # ---------------------------------------------------------------- cuts
    out("\n######## CUTS (not verdicts) - d_b per sport, D_b and Ds_b between sports")
    cres, cfam, cut_mismatch = {}, [], []
    for name, kn, kc in CUTS:
        tn, tc = tabs["nfl"].subset(kn), tabs["cfb"].subset(kc)
        o = {"nfl": boot(tn, SEED_OWN, a.draws), "cfb": boot(tc, SEED_OWN, a.draws)}
        i2 = {"nfl": boot(tn, SEED_NFL, a.draws), "cfb": boot(tc, SEED_CFB, a.draws)}
        cres[name] = {"n": {"nfl": tn.n, "cfb": tc.n}, "nfl": {}, "cfb": {}, "between": {}}
        out(f"\n-- cut {name}: NFL n {tn.n:,}, college n {tc.n:,}")
        for b in BASES:
            for s, t in (("nfl", tn), ("cfb", tc)):
                r = interval(o[s][0]["d_" + b], o[s][1]["d_" + b], t.n)
                cres[name][s][b] = r
                cfam.append((name, s, b, r))
            for key, lab in (("d_", "D"), ("s_", "Ds")):
                r = interval(i2["cfb"][0][key + b] - i2["nfl"][0][key + b],
                             i2["cfb"][1][key + b] - i2["nfl"][1][key + b], min(tn.n, tc.n))
                cres[name]["between"][f"{lab}_{b}"] = r
                cfam.append((name, "between", f"{lab}_{b}", r))
            out(f"   {b:<10} NFL {rc.fmt(cres[name]['nfl'][b])} | college {rc.fmt(cres[name]['cfb'][b])}")
            out(f"   {'':<10} D {rc.fmt(cres[name]['between']['D_' + b])} | Ds {rc.fmt(cres[name]['between']['Ds_' + b])}")
    for (_n, _s, _b, r), pa in zip(cfam, holm([r["p"] for *_x, r in cfam])):
        r["p_holm"], r["holm_significant"] = pa, bool(pa < ALPHA)
    for name, *_k in CUTS:
        for b in BASES:
            okn = rc.sign(cres[name]["nfl"][b]) == "below" and cres[name]["nfl"][b]["holm_significant"]
            okc = rc.sign(cres[name]["cfb"][b]) == "below" and cres[name]["cfb"][b]["holm_significant"]
            if okn != okc:
                cut_mismatch.append({"cut": name, "baseline": b, "holds_in": "nfl" if okn else "cfb"})
    result["cuts"] = {"results": cres, "holm_family_size": len(cfam),
                      "holm_significant": sum(1 for *_x, r in cfam if r["holm_significant"]),
                      "one_sport_only": cut_mismatch}
    out(f"\n   cuts Holm family of {len(cfam)}: {result['cuts']['holm_significant']} significant")
    out("   cuts where d_b is below zero (Holm) in one sport only: " +
        (json.dumps(cut_mismatch) if cut_mismatch else "none"))

    # ---------------------------------------------------------------- SECONDARY: price
    out("\n######## SECONDARY - against a price. NOT COMPARABLE across sports; no between-sport interval")
    out("   " + PRICE_SENTENCE)
    sec = {"sentence": PRICE_SENTENCE}

    def price_arm(label, rows, expect=None):
        rows = [r for r in rows if r["price"] is not None]
        if len(rows) < rc.MIN_GAMES:
            out(f"   {label}: {len(rows)} games - not scored")
            return {"n": len(rows), "not_scored": True}
        t = Tab(rows, ("model",) + BASES + ("price",))
        est, dr = boot(t, SEED_OWN, a.draws)
        r = interval(est["d_price"], dr["d_price"], t.n)
        bm, bk = float(t.E[:, 0].mean()), float(t.E[:, t.cols.index("price")].mean())
        cm, ck = rc.corp(t.p["model"], t.y), rc.corp(t.p["price"], t.y)
        res = {"n": t.n, "brier_model": bm, "brier_price": bk, "ratio": bm / bk, "dBrier": r,
               "dMCB": cm["mcb"] - ck["mcb"], "dDSC": cm["dsc"] - ck["dsc"], "unc": cm["unc"]}
        out(f"   {label}: n {t.n:,}  BS model {bm:.4f} price {bk:.4f} ratio {bm / bk:.4f}  UNC {cm['unc']:.4f}"
            f"  dMCB {res['dMCB']:+.4f} dDSC {res['dDSC']:+.4f}")
        show("      dBrier (model - price)", r)
        if expect is not None:
            ok = t.n == expect[0] and abs(r["est"] - expect[1]) <= TOL
            res["reproduces_published"] = bool(ok)
            out(f"      published: n {expect[0]:,}, dBrier {expect[1]:+.4f} -> {'reproduced' if ok else 'NOT reproduced'}")
            if not ok:
                raise SystemExit(f"{label}: does not reproduce the published figure - stopping")
        return res

    sec["nfl_close_2006_2025"] = price_arm("NFL nflverse moneyline close 2006-2025", nfl_rows, PUBLISHED_PRICE["nfl"])
    sec["cfb_cfbd_2021_2025"] = price_arm("college CFBD moneyline (last value, no capture time) 2021-2025",
                                          cfb_rows, PUBLISHED_PRICE["cfb"])
    sec["nfl_close_2021_2025"] = price_arm("NFL nflverse moneyline close 2021-2025",
                                           [r for r in nfl_rows if r["season"] >= 2021])
    sec["cfb_odds_api_2026"] = price_arm("college Odds API pre-kickoff h2h 2026 (timestamped, as on disk)", cfb_cur)
    result["secondary"] = sec

    # ---------------------------------------------------------------- counts
    n_int = (len(family) + 6 + len(mfam) + len(tfam) + len(cfam)
             + sum(1 for v in sec.values() if isinstance(v, dict) and "dBrier" in v))
    result["counts"] = {"registered_intervals": n_int,
                        "specifications": {"primary": 1, "matched": 1, "transfer_arms": 2,
                                           "cuts": len(CUTS), "price_arms": 4},
                        "holm_families": {"primary": len(family), "matched": len(mfam),
                                          "transfer": len(tfam), "cuts": len(cfam)}}
    out(f"\n   registered intervals {n_int} (primary {len(family)} + 6 skill levels, matched {len(mfam)}, "
        f"transfer {len(tfam)}, cuts {len(cfam)}, price 4 unpooled); specifications "
        f"{sum(result['counts']['specifications'].values())}")
    result["log"] = lines
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    if a.log_out:
        with open(a.log_out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
