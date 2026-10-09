"""c-44 - does a total model built the same way beat the same baselines in both sports.

    LOGGER_DB=<market_log.db> python -m research.cross_sport_total --mde-only \
        --json-out research/results/cross_sport_total_mde.json --log-out research/results/cross_sport_total_mde.log
    LOGGER_DB=<market_log.db> python -m research.cross_sport_total \
        --json-out research/results/cross_sport_total.json --log-out research/results/cross_sport_total.log

PRE-REGISTRATION: docs/C44-cross-sport-total-preregistration.md, committed and
pushed at c92f98e BEFORE this script existed. This file implements it; it does
not extend it. Comments say only where the code carries a rule out.

One function, `build`, makes the model and both baselines for either sport from
(game, season, start, home, away, points) and nothing else. Nothing under
models/ is added or edited.

LOGGER_DB must name market_log.db (this clone's .env points it elsewhere on
purpose); it and cfb.db are opened mode=ro. Nothing is written except
--json-out and --log-out. Everything printed is an aggregate or an interval.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import statistics
import sys

import numpy as np
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import ranking_calibration as rc           # noqa: E402
from research.cross_sport import holm, interval          # noqa: E402

FIRST_SEASON, FIT_FROM = 2001, 2002
SCORE_FROM, SCORE_TO = 2005, 2025
LAG = 6 * 3600.0                   # a game enters state 6h after its start
K_GRID = (1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0)
R_GRID = (0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0)
GRID = list(itertools.product(K_GRID, R_GRID))
BIN_EDGES = np.array([-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0])   # eight bins of e = B_pair - B_league
N_BINS = len(BIN_EDGES) + 1
SEED_OWN = rc.SEED                 # 24
SEED_NFL, SEED_CFB = 4401, 4402    # between-sport draws: independent per sport
ALPHA = 0.05
PRICE_FROM, PRICE_COVERAGE = 2013, 0.90
COLS = ("mse_model", "mse_league", "mse_pair", "br_model", "br_league", "br_pair")
PART1 = ("d_league", "d_pair", "c")
BASES = ("league", "pair")

PRICE_SENTENCE = (
    "The NFL comparator is the nflverse total_line, a sportsbook close of unrecorded provenance. The "
    "college comparator is the CFBD total, median over providers - NOT A TIMESTAMPED CLOSE: CFBD's last "
    "value per provider, read after the game, with no capture time. f-30's check (2026 weeks 3-5) was on "
    "the moneyline and c-15's (2026 week 3) on the spread; neither covers a total or a backfill season, "
    "so nothing on disk says when a college total was captured. The two figures are shown side by side, "
    "carry no between-sport interval, and enter no verdict.")


# =============================================================================
# the model and the two baselines - one function for both sports
# =============================================================================

def build(games):
    """`games`: completed population games, dicts with game, season, ts, home,
    away, hs, as_ (+ week, regular). -> (F, y): forecast-side arrays for the
    scored seasons, and their totals. Everything in F for a game is a function
    of games that started at least LAG before it."""
    order = sorted(range(len(games)), key=lambda i: (games[i]["ts"], games[i]["game"]))
    lg = {}                         # season -> [n, sum of totals]
    tm = {}                         # (team, season) -> [n, points for, points against]
    pending = collections.deque()
    idx, BL, BP, N, SUM, PREV = [], [], [], [], [], []

    def flush(t):
        while pending and games[pending[0]]["ts"] <= t - LAG:
            g = games[pending.popleft()]
            s = lg.setdefault(g["season"], [0, 0.0])
            s[0] += 1
            s[1] += g["hs"] + g["as_"]
            for team, pf, pa in ((g["home"], g["hs"], g["as_"]), (g["away"], g["as_"], g["hs"])):
                v = tm.setdefault((team, g["season"]), [0, 0.0, 0.0])
                v[0] += 1
                v[1] += pf
                v[2] += pa

    for i in order:
        g = games[i]
        T = g["season"]
        flush(g["ts"])
        a, b = lg.get(T - 1, (0, 0.0)), lg.get(T, (0, 0.0))
        if a[0] + b[0] > 0:
            league = (a[1] + b[1]) / (a[0] + b[0])
            own = []
            for team in (g["home"], g["away"]):
                p, c = tm.get((team, T - 1), (0, 0.0, 0.0)), tm.get((team, T), (0, 0.0, 0.0))
                own.append((p[1] + p[2] + c[1] + c[2]) / (p[0] + c[0]) if p[0] + c[0] else league)
            n4, s4, p4 = [], [], []
            for team, col in ((g["home"], 1), (g["away"], 2), (g["away"], 1), (g["home"], 2)):
                p, c = tm.get((team, T - 1), (0, 0.0, 0.0)), tm.get((team, T), (0, 0.0, 0.0))
                n4.append(c[0])
                s4.append(c[col])
                p4.append(p[col] / p[0] if p[0] else np.nan)
            idx.append(i)
            BL.append(league)
            BP.append(0.5 * (own[0] + own[1]))
            N.append(n4)
            SUM.append(s4)
            PREV.append(p4)
        pending.append(i)

    idx = np.array(idx)
    BL, BP = np.array(BL), np.array(BP)
    N, SUM, PREV = np.array(N, float), np.array(SUM), np.array(PREV)
    season = np.array([games[i]["season"] for i in idx])
    y = np.array([games[i]["hs"] + games[i]["as_"] for i in idx], float)
    L = BL / 2.0

    def raw(k, r):
        prior = np.where(np.isnan(PREV), L[:, None], L[:, None] + r * (np.nan_to_num(PREV) - L[:, None]))
        return ((SUM + k * prior) / (N + k)).sum(axis=1) - 2.0 * L

    RAW = np.stack([raw(k, r) for k, r in GRID])
    mu = np.full(len(idx), np.nan)
    s_model, s_pair, q, sig = (np.full(len(idx), np.nan) for _ in range(4))
    fits = {}
    for T in range(SCORE_FROM, SCORE_TO + 1):
        tr = (season >= FIT_FROM) & (season < T)
        te = season == T
        if not tr.any() or not te.any():
            raise SystemExit("season %d has %d training and %d scored games - stopping" % (T, tr.sum(), te.sum()))
        j = int(np.argmin(((RAW[:, tr] - y[tr]) ** 2).mean(axis=1)))
        X = np.column_stack([np.ones(tr.sum()), RAW[j, tr]])
        beta, *_ = np.linalg.lstsq(X, y[tr], rcond=None)
        mu[te] = beta[0] + beta[1] * RAW[j, te]
        s_model[te] = np.sqrt(((y[tr] - X @ beta) ** 2).mean())
        s_pair[te] = np.sqrt(((y[tr] - BP[tr]) ** 2).mean())
        q[te] = (y[tr] > BL[tr]).mean()
        sig[te] = y[tr].std()
        fits[T] = {"k": GRID[j][0], "r": GRID[j][1], "a": float(beta[0]), "b": float(beta[1]),
                   "train_games": int(tr.sum())}
    keep = (season >= SCORE_FROM) & (season <= SCORE_TO)
    want = sum(1 for g in games if SCORE_FROM <= g["season"] <= SCORE_TO)
    if keep.sum() != want:
        raise SystemExit("%d of %d scored-season games have a league baseline - stopping" % (keep.sum(), want))
    gi = idx[keep]
    F = {"game": np.array([games[i]["game"] for i in gi]), "season": season[keep],
         "week": np.array([games[i]["week"] for i in gi]),
         "regular": np.array([bool(games[i]["regular"]) for i in gi]),
         "mu": mu[keep], "league": BL[keep], "pair": BP[keep], "e": BP[keep] - BL[keep],
         "s_model": s_model[keep], "s_pair": s_pair[keep], "q": q[keep], "sigma": sig[keep],
         "fits": fits}
    F["bin"] = np.searchsorted(BIN_EDGES, F["e"], side="right")
    F["p_model"] = 1.0 - norm.cdf((F["league"] - F["mu"]) / F["s_model"])
    F["p_pair"] = 1.0 - norm.cdf((F["league"] - F["pair"]) / F["s_pair"])
    return F, y[keep]


def edge_hits(fits):
    out = collections.defaultdict(list)
    for T, f in sorted(fits.items()):
        for name, grid in (("k", K_GRID), ("r", R_GRID)):
            if f[name] in (grid[0], grid[-1]):
                out["%s=%s (%s)" % (name, f[name], "first" if f[name] == grid[0] else "last")].append(T)
    return dict(out)


# =============================================================================
# the outcome-blind MDE: forecasts and the training sd of totals, no forecast error
# =============================================================================

def formula_se(F, mask=None):
    """SE of each loss difference from SE(d) ~ 2 sigma rms(m - b) / sqrt(n).
    Takes no outcome: it cannot see which forecast is nearer the result."""
    m = np.ones(len(F["mu"]), bool) if mask is None else mask
    n = int(m.sum())
    var = float((F["sigma"][m] ** 2).mean())
    pairs = {"d_league": F["mu"] - F["league"], "d_pair": F["mu"] - F["pair"], "c": F["pair"] - F["league"]}
    out = {"n": n, "var_total": var, "share": np.bincount(F["bin"][m], minlength=N_BINS) / max(n, 1)}
    for name, delta in pairs.items():
        w = 4.0 * F["sigma"][m] ** 2 * delta[m] ** 2
        out[name] = float(np.sqrt(w.mean() / n)) if n else None
        cnt = np.bincount(F["bin"][m], minlength=N_BINS).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[name + "_bin"] = np.sqrt(np.bincount(F["bin"][m], weights=w, minlength=N_BINS) / cnt) / np.sqrt(cnt)
    for b in BASES:
        out["s_" + b] = out["d_" + b] / var if n else None
    out["dB_league"] = float(np.sqrt(((F["p_model"][m] - F["q"][m]) ** 2).mean() / n)) if n else None
    out["dB_pair"] = float(np.sqrt(((F["p_model"][m] - F["p_pair"][m]) ** 2).mean() / n)) if n else None
    return out


def reweighted_se(se, name, w):
    need = w > 0
    v = se[name + "_bin"][need]
    return None if np.isnan(v).any() else float(np.sqrt(((w[need] * v) ** 2).sum()))


def mde_table(sen, sec):
    """{test: MDE} for the primary family, R and B, from two formula_se dicts."""
    def comb(a, b):
        return None if a is None or b is None else 2.8 * float(np.hypot(a, b))
    t = {}
    for s, se in (("nfl", sen), ("cfb", sec)):
        for name in PART1:
            t[f"{s} {name}"] = 2.8 * se[name]
    for name in PART1:
        r = reweighted_se(sec, name, sen["share"])
        t[f"cfb@nfl {name}"] = None if r is None else 2.8 * r
        r = reweighted_se(sen, name, sec["share"])
        t[f"R nfl@cfb {name}"] = None if r is None else 2.8 * r
    for b in BASES:
        t[f"D_{b}"] = comb(sen["d_" + b], sec["d_" + b])
        t[f"Ds_{b}"] = comb(sen["s_" + b], sec["s_" + b])
        rc_, rn_ = reweighted_se(sec, "d_" + b, sen["share"]), reweighted_se(sen, "d_" + b, sec["share"])
        t[f"Dm_{b}"] = comb(rc_, sen["d_" + b])
        t[f"Dms_{b}"] = comb(None if rc_ is None else rc_ / sec["var_total"], sen["s_" + b])
        t[f"R D_{b}"] = comb(rn_, sec["d_" + b])
        t[f"R Ds_{b}"] = comb(None if rn_ is None else rn_ / sen["var_total"], sec["s_" + b])
        for s, se in (("nfl", sen), ("cfb", sec)):
            t[f"B {s} dB_{b}"] = 2.8 * se["dB_" + b]
        t[f"B D_{b}"] = comb(sen["dB_" + b], sec["dB_" + b])
    return t


# =============================================================================
# losses, resampling, statistics
# =============================================================================

class Tab:
    """Per-game losses and the environment bin, one row a game, sorted by game id."""

    def __init__(self, F, y, mask=None, extra=None):
        m = np.ones(len(y), bool) if mask is None else mask
        o = np.flatnonzero(m)
        o = o[np.argsort(F["game"][o], kind="stable")]
        if len(set(F["game"][o].tolist())) != len(o):
            raise ValueError("a game appears twice")
        z = (y[o] > F["league"][o]).astype(float)
        cols = [(F["mu"][o] - y[o]) ** 2, (F["league"][o] - y[o]) ** 2, (F["pair"][o] - y[o]) ** 2,
                (F["p_model"][o] - z) ** 2, (F["q"][o] - z) ** 2, (F["p_pair"][o] - z) ** 2]
        if extra is not None:
            cols.append((extra[o] - y[o]) ** 2)
        self.L = np.column_stack(cols)
        self.bin, self.e, self.n = F["bin"][o], F["e"][o], len(o)


def sums(tab, i):
    cnt = np.bincount(tab.bin[i], minlength=N_BINS).astype(float)
    S = np.column_stack([np.bincount(tab.bin[i], weights=tab.L[i, c], minlength=N_BINS)
                         for c in range(tab.L.shape[1])])
    return cnt, S


def boot(tab, seed, draws):
    """((cnt, S) on the sample, (cnt, S) stacked over draws). The draw sequence
    is c-24's: rng.integers(0, n, n) per draw over games in sorted-id order."""
    rng = np.random.default_rng(seed)
    cs, ss = [], []
    for _ in range(draws):
        c, s = sums(tab, rng.integers(0, tab.n, tab.n))
        cs.append(c)
        ss.append(s)
    return sums(tab, np.arange(tab.n)), (np.array(cs), np.array(ss))


def means(cnt, S, w=None):
    """Column means: overall, or with bin shares `w` (nan where a weighted bin is empty)."""
    if w is None:
        return S.sum(axis=-2) / cnt.sum(axis=-1)[..., None]
    need = w > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        bm = S / cnt[..., None]
    M = np.where(need[..., None], w[..., None] * np.nan_to_num(bm), 0.0).sum(axis=-2)
    bad = (need & (cnt == 0)).any(axis=-1)
    return np.where(np.asarray(bad)[..., None], np.nan, M)


def derive(M):
    d = {"d_league": M[..., 0] - M[..., 1], "d_pair": M[..., 0] - M[..., 2]}
    d["c"] = d["d_league"] - d["d_pair"]
    d["s_league"] = 1.0 - M[..., 0] / M[..., 1]
    d["s_pair"] = 1.0 - M[..., 0] / M[..., 2]
    d["dB_league"] = M[..., 3] - M[..., 4]
    d["dB_pair"] = M[..., 3] - M[..., 5]
    if M.shape[-1] > 6:
        d["d_price"] = M[..., 0] - M[..., 6]
    return d


def stat(bt, w=None):
    """(estimates, draws) of every derived statistic; `w` = (shares on the sample, shares per draw)."""
    (c0, s0), (cs, ss) = bt
    if w is None:
        return derive(means(c0, s0)), derive(means(cs, ss))
    return derive(means(c0, s0, w[0])), derive(means(cs, ss, w[1]))


def shares(bt):
    (c0, _s0), (cs, _ss) = bt
    return c0 / c0.sum(), cs / cs.sum(axis=-1, keepdims=True)


def ok(r):
    return rc.sign(r) == "below" and bool(r.get("holm_significant"))


def verdict(held):
    """The registered Part 1. `held` = {arm: {test: bool}} for arms nfl, cfb,
    cfb_at_nfl over PART1. -> (success, fails, spread_product, one_sport_only)."""
    fails = [(arm, t) for arm in ("nfl", "cfb", "cfb_at_nfl") for t in PART1 if not held[arm][t]]
    spread = [t for t in PART1 if held["cfb"][t] and not held["cfb_at_nfl"][t]]
    lone = {t: ("nfl" if held["nfl"][t] else "cfb") for t in PART1 if held["nfl"][t] != held["cfb"][t]}
    return (not fails), fails, spread, lone


def reading(raw, rw):
    """The registered Part 2 reading from the two signs (skill scale)."""
    if raw in ("below", "above") and rw == "contains 0":
        return "the size difference is accounted for by the spread of environments, to within the MDE"
    if rw in ("below", "above") and rw == raw:
        return "not accounted for by the spread alone"
    return "neither registered reading (raw %s, re-weighted %s)" % (raw, rw)


# =============================================================================
# loading
# =============================================================================

def load_nfl(out):
    from jobs import season_model as S
    from models import game as G
    con = S.market_log_ro()
    games, _grp, versions = S.load(con)
    line = {r[0]: r[1] for r in con.execute(
        "SELECT g.game_id, g.total_line FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv "
        "FROM nfl_games GROUP BY game_id) v ON v.game_id = g.game_id AND v.dv = g.data_version")}
    con.close()
    rows = []
    for g in games:
        if not (FIRST_SEASON <= g["season"] <= SCORE_TO) or g["home_score"] is None or g["away_score"] is None:
            continue
        if g["kickoff_ts"] is None:
            raise SystemExit("NFL game with a score and no kickoff time - stopping")
        rows.append({"game": g["game_id"], "season": g["season"], "week": g["week"],
                     "regular": g["game_type"] == "REG", "ts": float(g["kickoff_ts"]),
                     "home": G.franchise(g["home"]), "away": G.franchise(g["away"]),
                     "hs": g["home_score"], "as_": g["away_score"]})
    out(f"NFL: scored games {FIRST_SEASON}-{SCORE_TO} loaded {len(rows):,}; versions {versions}")
    return rows, {g: float(v) for g, v in line.items() if v is not None}, {"versions": versions}


def load_cfb(out):
    from models import cfb_game as C
    from research import cfb_game_forecast as R
    con = R.connect()
    games = R.load(con)
    tot = collections.defaultdict(list)
    for gid, t in con.execute("SELECT game_id, total FROM cfb_game_lines WHERE valid_to_ts IS NULL "
                              "AND total IS NOT NULL"):
        tot[str(gid)].append(float(t))
    con.close()
    rows = []
    for g in games:
        if not (FIRST_SEASON <= g["season"] <= SCORE_TO) or not C.completed(g):
            continue
        if g["start_ts"] is None:
            raise SystemExit("college game with a score and no start time - stopping")
        rows.append({"game": g["game_id"], "season": g["season"], "week": g["week"],
                     "regular": g["season_type"] == "regular", "ts": float(g["start_ts"]),
                     "home": g["home"], "away": g["away"], "hs": g["home_score"], "as_": g["away_score"]})
    out(f"college: completed FBS-FBS games {FIRST_SEASON}-{SCORE_TO} loaded {len(rows):,}")
    return rows, {g: statistics.median(v) for g, v in tot.items()}, {}


CUTS = [("1 regular season only", lambda F: F["regular"]),
        ("2 regular weeks 1-4", lambda F: F["regular"] & (F["week"] <= 4)),
        ("3 regular weeks 5+", lambda F: F["regular"] & (F["week"] > 4)),
        ("4 2005-2014", lambda F: F["season"] <= 2014),
        ("5 2015-2025", lambda F: F["season"] >= 2015)]


# =============================================================================
# main
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--log-out")
    ap.add_argument("--draws", type=int, default=rc.BOOT)
    ap.add_argument("--mde-only", action="store_true",
                    help="print the outcome-blind MDE of every test and stop; no loss is computed")
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    def finish(result):
        result["log"] = lines
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
        if a.log_out:
            with open(a.log_out, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        return 0

    result = {"preregistration": "docs/C44-cross-sport-total-preregistration.md @ c92f98e", "draws": a.draws,
              "seeds": {"own": SEED_OWN, "nfl": SEED_NFL, "cfb": SEED_CFB}}
    nfl_games, nfl_line, nfl_meta = load_nfl(out)
    cfb_games, cfb_line, _ = load_cfb(out)
    built = {"nfl": build(nfl_games), "cfb": build(cfb_games)}
    FS = {s: built[s][0] for s in built}
    result["nfl"] = dict(nfl_meta, fits=FS["nfl"]["fits"], edge_hits=edge_hits(FS["nfl"]["fits"]))
    result["cfb"] = {"fits": FS["cfb"]["fits"], "edge_hits": edge_hits(FS["cfb"]["fits"])}
    for s in ("nfl", "cfb"):
        f = FS[s]["fits"]
        out(f"{s}: scored {SCORE_FROM}-{SCORE_TO} n {len(FS[s]['mu']):,}; (k, r) chosen per season: "
            + ", ".join(f"{T}:({f[T]['k']:g},{f[T]['r']:g})" for T in sorted(f)))
        out(f"   grid-edge choices: {json.dumps(result[s]['edge_hits']) if result[s]['edge_hits'] else 'none'}")
        out("   environment bins (share of games): " + " ".join(
            "%.3f" % v for v in np.bincount(FS[s]["bin"], minlength=N_BINS) / len(FS[s]["bin"])))

    # ---------------------------------------------------------------- outcome-blind MDE
    out("\n######## MDE of every test from SE(d) ~ 2 sigma rms(m - b) / sqrt(n) - no forecast error is read")
    se = {s: formula_se(FS[s]) for s in FS}
    mde = mde_table(se["nfl"], se["cfb"])
    for k, v in mde.items():
        out(f"   {k:<22} MDE {'n/a (an empty bin)' if v is None else '%.4f' % v}")
    cut_mde = {}
    for name, keep in CUTS:
        cn, cc = formula_se(FS["nfl"], keep(FS["nfl"])), formula_se(FS["cfb"], keep(FS["cfb"]))
        cut_mde[name] = {f"{s} d_{b}": 2.8 * c_["d_" + b] for s, c_ in (("nfl", cn), ("cfb", cc)) for b in BASES}
        for b in BASES:
            cut_mde[name][f"D_{b}"] = 2.8 * float(np.hypot(cn["d_" + b], cc["d_" + b]))
            cut_mde[name][f"Ds_{b}"] = 2.8 * float(np.hypot(cn["s_" + b], cc["s_" + b]))
        out(f"   cut {name}: " + "  ".join(f"{k} {v:.4f}" for k, v in cut_mde[name].items()))
    result["mde_formula"] = {"tests": mde, "cuts": cut_mde,
                             "n": {s: se[s]["n"] for s in se}, "var_total": {s: se[s]["var_total"] for s in se}}
    if a.mde_only:
        out("\n   --mde-only: stopping before any loss is computed")
        return finish(result)

    # ---------------------------------------------------------------- PRIMARY
    tabs = {s: Tab(*built[s]) for s in built}
    own = {s: boot(tabs[s], SEED_OWN, a.draws) for s in tabs}
    ind = {"nfl": boot(tabs["nfl"], SEED_NFL, a.draws), "cfb": boot(tabs["cfb"], SEED_CFB, a.draws)}
    G2 = min(tabs["nfl"].n, tabs["cfb"].n)

    def show(label, r, key=None, d=4):
        m = mde.get(key) if key else None
        out(f"   {label:<40} {rc.fmt(r, d)}  MDE run {r['mde']:.4f}"
            + (f" / registered {m:.4f}" if m is not None else "") + f"  -> {rc.sign(r)}")

    out("\n######## PRIMARY - squared error of the total against settlement, game blocks")
    prim, family = {}, []
    for s in ("nfl", "cfb"):
        t = tabs[s]
        est, dr = stat(own[s])
        M = means(*own[s][0])
        prim[s] = {"n": t.n, "mse": dict(zip(COLS[:3], M[:3].tolist())),
                   "brier": dict(zip(COLS[3:], M[3:6].tolist())),
                   "mean_e": float(t.e.mean()), "sd_e": float(t.e.std()), "tests": {}, "skill": {}}
        out(f"\n== {s}: n {t.n:,}  MSE model {M[0]:.2f}  league {M[1]:.2f}  pair {M[2]:.2f}   "
            f"sd of e {t.e.std():.2f} points")
        for name in PART1:
            r = interval(est[name], dr[name], t.n)
            prim[s]["tests"][name] = r
            family.append((s, name, r))
            show({"c": "c = d_league - d_pair"}.get(name, name), r, f"{s} {name}", d=3)
        for b in BASES:
            r = interval(est["s_" + b], dr["s_" + b], t.n)
            prim[s]["skill"][b] = r
            show(f"skill 1 - MSE(model)/MSE({b}) (level)", r)

    wn, wc = shares(ind["nfl"]), shares(ind["cfb"])
    e_n, d_n = stat(ind["nfl"])
    e_c, d_c = stat(ind["cfb"])
    e_cm, d_cm = stat(ind["cfb"], wn)            # college at the NFL's bin shares
    e_nm, d_nm = stat(ind["nfl"], wc)            # R: the NFL at college's bin shares
    out("\n== college at the NFL's bin shares of e")
    prim["cfb_at_nfl"] = {"tests": {}, "dropped_draws": int(np.isnan(d_cm["d_league"]).sum())}
    for name in PART1:
        r = interval(e_cm[name], d_cm[name], G2)
        prim["cfb_at_nfl"]["tests"][name] = r
        family.append(("cfb@nfl", name, r))
        show(name, r, f"cfb@nfl {name}", d=3)
    out(f"   draws dropped for an empty college bin: {prim['cfb_at_nfl']['dropped_draws']}")

    out("\n== between sports (college - NFL), independent draws per sport: raw, then re-weighted")
    between = {}
    for b in BASES:
        for key, lab, mlab, d in (("d_", "D", "Dm", 3), ("s_", "Ds", "Dms", 4)):
            r = interval(e_c[key + b] - e_n[key + b], d_c[key + b] - d_n[key + b], G2)
            between[f"{lab}_{b}"] = r
            family.append(("between", f"{lab}_{b}", r))
            show(f"{lab}_{b} (raw)", r, f"{lab}_{b}", d=d)
            r = interval(e_cm[key + b] - e_n[key + b], d_cm[key + b] - d_n[key + b], G2)
            between[f"{mlab}_{b}"] = r
            family.append(("between", f"{mlab}_{b}", r))
            show(f"{mlab}_{b} (college at NFL shares - NFL)", r, f"{mlab}_{b}", d=d)
    for (_s, _n, r), pa in zip(family, holm([r["p"] for *_x, r in family])):
        r["p_holm"], r["holm_significant"] = pa, bool(pa < ALPHA)
    held = {arm: {t: ok(prim[arm]["tests"][t]) for t in PART1} for arm in ("nfl", "cfb", "cfb_at_nfl")}
    success, fails, spread, lone = verdict(held)
    reads = {b: reading(rc.sign(between[f"Ds_{b}"]), rc.sign(between[f"Dms_{b}"])) for b in BASES}
    result["primary"] = {"per_arm": prim, "between": between, "success": success,
                         "failures": [list(f) for f in fails], "spread_product": spread,
                         "one_sport_only": lone, "part2_reading": reads, "holm_family_size": len(family),
                         "holm_significant": sum(1 for *_x, r in family if r["holm_significant"])}
    out(f"\n   Holm family of {len(family)}: {result['primary']['holm_significant']} significant at {ALPHA}")
    for s_, n_, r in family:
        out(f"      {s_:<8} {n_:<12} p {r['p']:.2e}  Holm {r['p_holm']:.2e}  {'*' if r['holm_significant'] else ' '}")
    out("\n   PART 1: " + ("SUCCESS - model beats pair beats league in the NFL, in college, and in college "
                           "at the NFL's distribution of e" if success else "FAILS - it does not transfer"))
    for arm, t in fails:
        out(f"      fails: {arm} {t}: interval {rc.sign(prim[arm]['tests'][t])}, Holm "
            f"{'ok' if prim[arm]['tests'][t]['holm_significant'] else 'not significant'}")
    for t in spread:
        out(f"      {t}: holds raw in college and fails re-weighted - the college ordering is a product of "
            "the wider spread of game environments")
    for t, s_ in lone.items():
        out(f"      {t} holds in {s_} only - suspected of being a fitted artefact")
    for b in BASES:
        out(f"   PART 2, against {b} (skill scale): {reads[b]}")

    out("\n== per bin of e: n, mean e, d_league / d_pair (points^2), NFL | college")
    lo = [-np.inf] + BIN_EDGES.tolist()
    bins = []
    for k in range(N_BINS):
        row = {"bin": "[%s, %s)" % (lo[k], BIN_EDGES[k] if k + 1 < N_BINS else "inf")}
        for s in ("nfl", "cfb"):
            t = tabs[s]
            m = t.bin == k
            row[s] = {"n": int(m.sum()), "share": float(m.mean()),
                      "mean_e": float(t.e[m].mean()) if m.any() else None,
                      "d_league": float((t.L[m, 0] - t.L[m, 1]).mean()) if m.any() else None,
                      "d_pair": float((t.L[m, 0] - t.L[m, 2]).mean()) if m.any() else None}
        bins.append(row)
        out("   %-14s " % row["bin"] + " | ".join(
            "n %5d share %.3f e %s d_league %s d_pair %s" % (
                row[s]["n"], row[s]["share"],
                *("%+7.2f" % row[s][x] if row[s][x] is not None else "   -   " for x in ("mean_e", "d_league", "d_pair")))
            for s in ("nfl", "cfb")))
    result["bins"] = bins

    # ---------------------------------------------------------------- R: reverse re-weighting
    out("\n######## R (secondary) - the NFL at college's bin shares of e")
    rfam, rres = [], {"dropped_draws": int(np.isnan(d_nm["d_league"]).sum())}
    for name in PART1:
        r = interval(e_nm[name], d_nm[name], G2)
        rres[name] = r
        rfam.append(r)
        show(f"NFL at college shares: {name}", r, f"R nfl@cfb {name}", d=3)
    for b in BASES:
        for key, lab, d in (("d_", "D", 3), ("s_", "Ds", 4)):
            r = interval(e_c[key + b] - e_nm[key + b], d_c[key + b] - d_nm[key + b], G2)
            rres[f"{lab}_{b}"] = r
            rfam.append(r)
            show(f"college - NFL at college shares: {lab}_{b}", r, f"R {lab}_{b}", d=d)
    out(f"   draws dropped for an empty NFL bin: {rres['dropped_draws']}")
    for r, pa in zip(rfam, holm([r["p"] for r in rfam])):
        r["p_holm"], r["holm_significant"] = pa, bool(pa < ALPHA)
    result["reverse"] = rres

    # ---------------------------------------------------------------- B: common unit
    out("\n######## B (secondary) - Brier of 'the total is above the league-and-season mean'")
    bfam, bres = [], {"nfl": {}, "cfb": {}, "between": {}}
    for s in ("nfl", "cfb"):
        est, dr = stat(own[s])
        out(f"   {s}: Brier model {prim[s]['brier']['br_model']:.4f} league {prim[s]['brier']['br_league']:.4f} "
            f"pair {prim[s]['brier']['br_pair']:.4f}")
        for b in BASES:
            r = interval(est["dB_" + b], dr["dB_" + b], tabs[s].n)
            bres[s][b] = r
            bfam.append(r)
            show(f"{s} dB_{b}", r, f"B {s} dB_{b}")
    for b in BASES:
        r = interval(e_c["dB_" + b] - e_n["dB_" + b], d_c["dB_" + b] - d_n["dB_" + b], G2)
        bres["between"]["D_" + b] = r
        bfam.append(r)
        show(f"college - NFL dB_{b} (raw)", r, f"B D_{b}")
        r = interval(e_cm["dB_" + b] - e_n["dB_" + b], d_cm["dB_" + b] - d_n["dB_" + b], G2)
        bres["between"]["Dm_" + b] = r
        bfam.append(r)
        show(f"college at NFL shares - NFL dB_{b}", r)
    for r, pa in zip(bfam, holm([r["p"] for r in bfam])):
        r["p_holm"], r["holm_significant"] = pa, bool(pa < ALPHA)
    result["brier"] = bres

    # ---------------------------------------------------------------- cuts
    out("\n######## CUTS (not verdicts) - d_b per sport, raw D_b and Ds_b")
    cres, cfam, mismatch = {}, [], []
    for name, keep in CUTS:
        tn = Tab(*built["nfl"], mask=keep(FS["nfl"]))
        tc = Tab(*built["cfb"], mask=keep(FS["cfb"]))
        o = {"nfl": stat(boot(tn, SEED_OWN, a.draws)), "cfb": stat(boot(tc, SEED_OWN, a.draws))}
        (en, dn), (ec, dc) = stat(boot(tn, SEED_NFL, a.draws)), stat(boot(tc, SEED_CFB, a.draws))
        cres[name] = {"n": {"nfl": tn.n, "cfb": tc.n}, "nfl": {}, "cfb": {}, "between": {}}
        out(f"\n-- cut {name}: NFL n {tn.n:,}, college n {tc.n:,}")
        for b in BASES:
            for s, t in (("nfl", tn), ("cfb", tc)):
                r = interval(o[s][0]["d_" + b], o[s][1]["d_" + b], t.n)
                cres[name][s][b] = r
                cfam.append(r)
            for key, lab in (("d_", "D"), ("s_", "Ds")):
                r = interval(ec[key + b] - en[key + b], dc[key + b] - dn[key + b], min(tn.n, tc.n))
                cres[name]["between"][f"{lab}_{b}"] = r
                cfam.append(r)
            out(f"   {b:<7} NFL {rc.fmt(cres[name]['nfl'][b], 3)} | college {rc.fmt(cres[name]['cfb'][b], 3)}")
            out(f"   {'':<7} D {rc.fmt(cres[name]['between']['D_' + b], 3)} | Ds {rc.fmt(cres[name]['between']['Ds_' + b])}")
    for r, pa in zip(cfam, holm([r["p"] for r in cfam])):
        r["p_holm"], r["holm_significant"] = pa, bool(pa < ALPHA)
    for name, _k in CUTS:
        for b in BASES:
            hn, hc = ok(cres[name]["nfl"][b]), ok(cres[name]["cfb"][b])
            if hn != hc:
                mismatch.append({"cut": name, "baseline": b, "holds_in": "nfl" if hn else "cfb"})
    result["cuts"] = {"results": cres, "holm_family_size": len(cfam),
                      "holm_significant": sum(1 for r in cfam if r["holm_significant"]),
                      "one_sport_only": mismatch}
    out(f"\n   cuts Holm family of {len(cfam)}: {result['cuts']['holm_significant']} significant")
    out("   cuts where d_b is below zero (Holm) in one sport only: " + (json.dumps(mismatch) if mismatch else "none"))

    # ---------------------------------------------------------------- the price: separate, outside the verdict
    out("\n######## PRICE - separate, outside the success condition. College: NOT A TIMESTAMPED CLOSE")
    out("   " + PRICE_SENTENCE)
    sec = {"sentence": PRICE_SENTENCE, "coverage": {}}
    Fc, Fn = FS["cfb"], FS["nfl"]
    pc = np.array([cfb_line.get(g, np.nan) for g in Fc["game"]])
    pn = np.array([nfl_line.get(g, np.nan) for g in Fn["game"]])
    seasons = []
    for T in range(PRICE_FROM, SCORE_TO + 1):
        m = Fc["season"] == T
        cov = float((~np.isnan(pc[m])).mean())
        sec["coverage"][T] = cov
        if cov >= PRICE_COVERAGE:
            seasons.append(T)
    out("   college share of games carrying a CFBD total, by season: "
        + " ".join(f"{T}:{v:.3f}" for T, v in sec["coverage"].items()))
    out(f"   seasons at or above {PRICE_COVERAGE:.2f}, applied to BOTH sports: {seasons or 'none - arm not run'}")
    sec["seasons"] = seasons
    n_price = 0
    if seasons:
        for s, F, p, label in (("nfl", Fn, pn, "NFL nflverse total_line (close, provenance unrecorded)"),
                               ("cfb", Fc, pc, "college CFBD total (not a timestamped close)")):
            m = np.isin(F["season"], seasons) & ~np.isnan(p)
            t = Tab(F, built[s][1], mask=m, extra=p)
            est, dr = stat(boot(t, SEED_OWN, a.draws))
            r = interval(est["d_price"], dr["d_price"], t.n)
            M = means(*sums(t, np.arange(t.n)))
            sec[s] = {"n": t.n, "of": int(np.isin(F["season"], seasons).sum()), "mse_model": float(M[0]),
                      "mse_price": float(M[6]), "ratio": float(M[0] / M[6]), "dMSE": r}
            n_price += 1
            out(f"   {label}: n {t.n:,} of {sec[s]['of']:,}  MSE model {M[0]:.2f} price {M[6]:.2f} ratio {M[0] / M[6]:.4f}")
            out(f"      dMSE (model - price) {rc.fmt(r, 3)} -> {rc.sign(r)}")
    result["price"] = sec

    n_int = len(family) + len(rfam) + len(bfam) + len(cfam) + n_price
    result["counts"] = {"registered_intervals": n_int,
                        "families": {"primary": len(family), "reverse": len(rfam), "brier": len(bfam),
                                     "cuts": len(cfam), "price": n_price},
                        "specifications": 4 + len(CUTS), "grid_points": len(GRID)}
    out(f"\n   registered intervals {n_int} (primary {len(family)}, R {len(rfam)}, B {len(bfam)}, cuts {len(cfam)}, "
        f"price {n_price}); specifications {4 + len(CUTS)}; grid points {len(GRID)} per sport per season")
    return finish(result)


if __name__ == "__main__":
    sys.exit(main())
