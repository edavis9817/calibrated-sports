"""c-28 - the game forecast the season model already had, scored.

    LOGGER_DB=<market_log.db> python -m research.game_forecast --json-out D:/temp/c28/result.json

PRE-REGISTRATION: docs/C28-game-forecast-preregistration.md, committed and
pushed at 45abdce BEFORE this script existed. This file implements it; it does
not extend it. Comments say only where the code carries a rule out.

The forecast is `models.game.GameForecast`; the rating walk and its per-season
fit are the season model's own (`models.season.run_elo`,
`jobs.season_model.game_losses` / `best_params`), imported, not copied. The
scoring statistics are c-24's (`research.ranking_calibration`), imported.

market_log.db is opened mode=ro (jobs.season_model.market_log_ro) and nothing
is written except --json-out. Everything printed is an aggregate or an
interval - no game is named as a pick.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import season_model as S                      # noqa: E402
from models import game as G                            # noqa: E402
from models import season as M                          # noqa: E402
from research import ranking_calibration as rc          # noqa: E402
from research.structural import ladder_key              # noqa: E402

FIT_FROM = S.FIT_FROM            # 2000
SCORE_FROM, SCORE_TO = 2001, 2025
ML_FROM = 2006                   # first season with nflverse moneylines
KALSHI_SEASON, KALSHI_WEEKS = 2026, (1, 2, 3)
TOTAL_WINDOW = 256
CLOSE_MAX_AGE = 1800             # seconds before kickoff
SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL")


# =============================================================================
# fitting: the season model's walk, with and without margin of victory
# =============================================================================

def nomov_losses(games, params_list):
    """`S.game_losses` for plain Elo: identical rule, multiplier fixed at 1."""
    out = {}
    for p in params_list:
        pre, _ = M.run_elo(games, p, mov=False)
        seasons = np.array([games[i]["season"] for i, _ in pre])
        ll = np.array([M.log_loss(q, 1.0 if games[i]["home_score"] > games[i]["away_score"]
                                  else (0.0 if games[i]["home_score"] < games[i]["away_score"] else 0.5))
                       for i, q in pre])
        out[p] = (seasons, ll)
    return out


class Walk:
    """Per-season params (fitted on FIT_FROM..T-1) and each game's as-of p."""

    def __init__(self, games, losses, mov):
        self.games, self.losses, self.mov = games, losses, mov
        self._pre = {}
        self.fits = {}

    def params(self, year):
        if year not in self.fits:
            p, ll, n = S.best_params(self.losses, year)
            self.fits[year] = {"params": p, "fit_log_loss": ll, "fit_games": n}
        return self.fits[year]["params"]

    def pre(self, params):
        if params not in self._pre:
            pre, _ = M.run_elo(self.games, params, mov=self.mov)
            self._pre[params] = dict(pre)
        return self._pre[params]

    def p(self, i):
        g = self.games[i]
        return self.pre(self.params(g["season"]))[i]


def scored(g):
    return g["home_score"] is not None and g["away_score"] is not None


def margin_sigmas(games, walk, years):
    """sigma_m per season: margin MLE on FIT_FROM..T-1 under season T's params."""
    out = {}
    for y in years:
        pre = walk.pre(walk.params(y))
        idx = [i for i in pre if FIT_FROM <= games[i]["season"] < y]
        p = np.array([pre[i] for i in idx])
        m = np.array([games[i]["home_score"] - games[i]["away_score"] for i in idx], float)
        out[y] = G.margin_sigma_mle(p, m)
    return out


def total_params(games):
    """{game index: (mu_t, sigma_t)} from the TOTAL_WINDOW completed games
    immediately before each game (all types), by (kickoff, game_id)."""
    order = sorted((i for i, g in enumerate(games) if g["kickoff_ts"] is not None),
                   key=lambda i: (games[i]["kickoff_ts"], games[i]["game_id"]))
    kicks, totals = [], []     # completed games, in kickoff order
    out = {}
    for i in order:
        g = games[i]
        n_before = bisect.bisect_left(kicks, g["kickoff_ts"])   # strictly earlier kickoffs
        if n_before >= TOTAL_WINDOW:
            a = np.array(totals[n_before - TOTAL_WINDOW:n_before], float)
            out[i] = (float(a.mean()), float(a.std(ddof=1)))
        if scored(g):
            kicks.append(g["kickoff_ts"])
            totals.append(g["home_score"] + g["away_score"])
    return out


def forecast(games, i, walk, sig, tot, as_of):
    g = games[i]
    mu_t, sd_t = tot[i]
    return G.GameForecast(game_id=g["game_id"], home=g["home"], away=g["away"], as_of=as_of,
                          p_home=walk.p(i), sigma_m=sig[g["season"]], mu_t=mu_t, sigma_t=sd_t)


# =============================================================================
# baselines
# =============================================================================

def record_signs(games):
    """{game index: +1 / -1 / 0} home record better / worse / equal, season to
    date before kickoff (ties half), all game types."""
    by = defaultdict(list)
    for i, g in enumerate(games):
        by[g["season"]].append(i)
    out = {}
    for s, idx in by.items():
        idx.sort(key=lambda i: (games[i]["kickoff_ts"] or 0.0, games[i]["game_id"]))
        w, n = defaultdict(float), defaultdict(int)
        pending = []
        last_k = None
        for i in idx:
            g = games[i]
            k = g["kickoff_ts"] or 0.0
            if last_k is not None and k > last_k:
                for j in pending:                 # results land after their kickoff instant
                    _apply(games[j], w, n)
                pending = []
            last_k = k
            ph = w[g["home"]] / n[g["home"]] if n[g["home"]] else None
            pa = w[g["away"]] / n[g["away"]] if n[g["away"]] else None
            if ph is None or pa is None or abs(ph - pa) < 1e-12:
                out[i] = 0
            else:
                out[i] = 1 if ph > pa else -1
            if scored(g):
                pending.append(i)
    return out


def _apply(g, w, n):
    d = g["home_score"] - g["away_score"]
    w[g["home"]] += 1.0 if d > 0 else (0.5 if d == 0 else 0.0)
    w[g["away"]] += 1.0 if d < 0 else (0.5 if d == 0 else 0.0)
    n[g["home"]] += 1
    n[g["away"]] += 1


def baseline_constants(games, signs, year):
    """(home rate, q) on decisive games in FIT_FROM..year-1."""
    home_w = home_n = rec_w = rec_n = 0
    for i, g in enumerate(games):
        if not (FIT_FROM <= g["season"] < year) or not scored(g):
            continue
        d = g["home_score"] - g["away_score"]
        if d == 0:
            continue
        home_n += 1
        home_w += d > 0
        s = signs[i]
        if s != 0:
            rec_n += 1
            rec_w += (d > 0) == (s > 0)
    return home_w / home_n, rec_w / rec_n


# =============================================================================
# scoring - c-24's statistics, one resample pass per comparison
# =============================================================================

def boot_many(pop, fns, draws=None, seed=None):
    """`rc.Pop.boot` for several statistics on the SAME resamples: the draw
    sequence, the percentile interval and the SE are Pop.boot's; only the loop
    is shared so each resample computes CORP once rather than once per figure."""
    draws = rc.BOOT if draws is None else draws
    seed = rc.SEED if seed is None else seed
    ests = fns(np.arange(pop.n))
    rng = np.random.default_rng(seed)
    vals = defaultdict(list)
    Gn = pop.games
    for _ in range(draws):
        pick = rng.integers(0, Gn, Gn)
        idx = np.concatenate([pop.gidx[j] for j in pick])
        for k, v in fns(idx).items():
            if v is not None:
                vals[k].append(v)
    out = {}
    for k, est in ests.items():
        v = np.array(vals[k])
        if Gn < 2 or est is None or not len(v):
            out[k] = {"est": est, "lo": None, "hi": None, "se": None, "games": Gn, "n": pop.n}
        else:
            out[k] = {"est": est, "lo": float(np.percentile(v, 2.5)),
                      "hi": float(np.percentile(v, 97.5)), "se": float(v.std()),
                      "games": Gn, "n": pop.n}
        out[k]["mde"] = 2.8 * out[k]["se"] if out[k]["se"] else None
    return out


def compare(name, rows, out, tests, within_line=False, draws=None):
    """model (m) against comparator (k) on binary y. Rows carry game, stat, line."""
    pop = rc.Pop(name, rows, "m", "k")
    m, k, y, s = pop.m, pop.k, pop.y, pop.s

    def fns(i):
        cm, ck = rc.corp(m[i], y[i]), rc.corp(k[i], y[i])
        am, ak = rc.auc(m[i], y[i]), rc.auc(k[i], y[i])
        r = {"dBrier": cm["bs"] - ck["bs"], "dMCB": cm["mcb"] - ck["mcb"],
             "dDSC": cm["dsc"] - ck["dsc"],
             "dAUC": None if am is None or ak is None else am - ak}
        if within_line:
            wm, wk = rc.wauc(m[i], y[i], s[i]), rc.wauc(k[i], y[i], s[i])
            r["d_wAUC"] = None if wm is None or wk is None else wm - wk
        return r
    t0 = time.time()
    d = boot_many(pop, fns, draws=draws)
    cm, ck = rc.corp(m, y), rc.corp(k, y)
    res = {"n": pop.n, "n_blocks": pop.games, "realized": float(y.mean()),
           "corp_model": cm, "corp_comparator": ck,
           "auc_model": rc.auc(m, y), "auc_comparator": rc.auc(k, y), "diffs": d}
    out(f"\n== {name}: n {pop.n:,}  n_blocks {pop.games}  realized {y.mean():.4f}  ({time.time() - t0:.0f}s)")
    out(f"   model      BS {cm['bs']:.4f} = MCB {cm['mcb']:.4f} - DSC {cm['dsc']:.4f} + UNC {cm['unc']:.4f}"
        f"   AUC {res['auc_model']:.4f}" if res["auc_model"] is not None else "")
    out(f"   comparator BS {ck['bs']:.4f} = MCB {ck['mcb']:.4f} - DSC {ck['dsc']:.4f} + UNC {ck['unc']:.4f}"
        f"   AUC {res['auc_comparator']:.4f}" if res["auc_comparator"] is not None else "")
    for nm, r in d.items():
        mde = f"  MDE {r['mde']:.4f}" if r["mde"] else ""
        out(f"   {nm:<7} (model - comparator) {rc.fmt(r)}{mde}  -> {rc.sign(r)}")
        tests.append((name, nm, r))
    return res


def mse_compare(name, y, pred_m, pred_k, blocks, out, tests, draws=None):
    """d(MSE) of a point prediction, model minus comparator, game blocks."""
    rows = [{"game": b, "stat": "x", "line": 0.0, "y": 0.0, "m": 0.0, "k": 0.0} for b in blocks]
    pop = rc.Pop(name, rows, "m", "k")
    em, ek = (y - pred_m) ** 2, (y - pred_k) ** 2
    r = pop.boot(lambda i: float(em[i].mean() - ek[i].mean()),
                 draws=rc.BOOT if draws is None else draws)
    r["mde"] = 2.8 * r["se"] if r["se"] else None
    res = {"n": len(y), "n_blocks": pop.games, "mse_model": float(em.mean()),
           "mse_comparator": float(ek.mean()), "mae_model": float(np.abs(y - pred_m).mean()),
           "mae_comparator": float(np.abs(y - pred_k).mean()), "dMSE": r}
    out(f"\n== {name}: n {len(y):,}  n_blocks {pop.games}")
    out(f"   MSE model {res['mse_model']:.2f}  comparator {res['mse_comparator']:.2f}   "
        f"MAE model {res['mae_model']:.2f}  comparator {res['mae_comparator']:.2f}")
    out(f"   dMSE (model - comparator) {rc.fmt(r, 2)}  MDE {r['mde']:.2f}  -> {rc.sign(r)}")
    tests.append((name, "dMSE", r))
    return res


# =============================================================================
# markets
# =============================================================================

def american(o):
    o = float(o)
    return -o / (-o + 100.0) if o < 0 else 100.0 / (o + 100.0)


def kalshi_closes(con, games_by_id):
    """[{series, market_id, game_id, team, line, bid, ask, mid, ts, kickoff}] for
    the registered weeks: last `source='live'` two-sided quote strictly before
    kickoff and no more than CLOSE_MAX_AGE before it. Also returns the census."""
    rows = con.execute(
        "SELECT m.market_id, m.line, o.event_id, o.entity_id, o.line "
        "FROM markets m JOIN market_outcome mo ON mo.venue = m.venue AND mo.market_id = m.market_id "
        "JOIN outcomes o ON o.outcome_id = mo.outcome_id "
        "WHERE m.venue = 'kalshi' AND (" + " OR ".join("m.market_id LIKE ?" for _ in SERIES) + ")",
        [s + "-%" for s in SERIES]).fetchall()
    census = defaultdict(lambda: defaultdict(int))
    out = []
    for mid, mline, gid, ent, oline in rows:
        series = mid.split("-")[0]
        g = games_by_id.get(gid)
        if g is None or g["season"] != KALSHI_SEASON or g["week"] not in KALSHI_WEEKS:
            census[series]["outside_weeks_or_unmatched"] += 1
            continue
        census[series]["mapped_in_weeks"] += 1
        k = g["kickoff_ts"]
        q = con.execute(
            "SELECT ts, best_bid, best_ask FROM quotes WHERE venue = 'kalshi' AND market_id = ? "
            "AND source = 'live' AND ts < ? AND ts >= ? AND best_bid IS NOT NULL "
            "AND best_ask IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (mid, k, k - CLOSE_MAX_AGE)).fetchone()
        if q is None:
            census[series]["no_two_sided_live_close"] += 1
            continue
        census[series]["closed"] += 1
        line = oline if oline is not None else mline
        out.append({"series": series, "market_id": mid, "game_id": gid, "team": ent,
                    "line": line, "bid": q[1], "ask": q[2], "mid": (q[1] + q[2]) / 2.0,
                    "ts": q[0], "kickoff": k})
    return out, {s: dict(v) for s, v in census.items()}


# =============================================================================
# main
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--draws", type=int, default=rc.BOOT)
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    tests = []
    result = {"preregistration": "docs/C28-game-forecast-preregistration.md @ 45abdce"}
    con = S.market_log_ro()
    games, _groupings, versions = S.load(con)
    result["versions"] = versions
    by_id = {g["game_id"]: g for g in games}
    out(f"games loaded {len(games):,}, scored {sum(scored(g) for g in games):,}; versions {versions}")

    t0 = time.time()
    grid = S.grid()
    walk = Walk(games, S.game_losses(games, grid), mov=True)
    walk0 = Walk(games, nomov_losses(games, grid), mov=False)
    out(f"grid fits (MOV and plain) {time.time() - t0:.0f}s")
    years = list(range(SCORE_FROM, SCORE_TO + 1)) + [KALSHI_SEASON]
    sig = margin_sigmas(games, walk, years)
    tot = total_params(games)
    signs = record_signs(games)
    result["fits"] = {y: {"mov": walk.params(y).as_dict(), "nomov": walk0.params(y).as_dict(),
                          "sigma_m": sig[y]} for y in years}
    out("sigma_m by season: " + ", ".join(f"{y}:{sig[y]:.1f}" for y in years))

    # ---------------------------------------------------------------- Part 1
    pop1 = [i for i, g in enumerate(games)
            if SCORE_FROM <= g["season"] <= SCORE_TO and scored(g)
            and g["home_score"] != g["away_score"]]
    consts = {y: baseline_constants(games, signs, y) for y in years}
    base = {"home": {}, "record": {}, "elo_nomov": {}}
    for i in pop1:
        g = games[i]
        h, q = consts[g["season"]]
        base["home"][i] = h
        s = signs[i]
        base["record"][i] = h if s == 0 else (q if s > 0 else 1.0 - q)
        base["elo_nomov"][i] = walk0.p(i)
    result["baseline_constants"] = {y: {"home_rate": consts[y][0], "q_better_record": consts[y][1]}
                                    for y in years}

    def rows_for(idx, comp, block="game"):
        rs = []
        for i in idx:
            g = games[i]
            b = g["game_id"] if block == "game" else f"{g['season']}-{g['week']:02d}"
            rs.append({"game": b, "stat": "ml", "line": 0.0,
                       "y": 1.0 if g["home_score"] > g["away_score"] else 0.0,
                       "m": walk.p(i), "k": comp(i)})
        return rs

    out(f"\n######## PART 1 - against settlement, {SCORE_FROM}-{SCORE_TO}, decisive games (PRIMARY)")
    p1 = {}
    for b in ("home", "record", "elo_nomov"):
        p1[b] = compare(f"P1 model vs {b}", rows_for(pop1, base[b].get), out, tests, draws=a.draws)
    verdict = {b: rc.sign(p1[b]["diffs"]["dBrier"]) for b in p1}
    success = all(v == "below" for v in verdict.values())
    result["part1"] = {"results": p1, "dBrier_sign": verdict, "success": success}
    out(f"\n   PRIMARY VERDICT: dBrier vs home {verdict['home']}, record {verdict['record']}, "
        f"elo_nomov {verdict['elo_nomov']} -> {'SUCCESS: beats all three baselines' if success else 'NOT all three'}")

    # sensitivity and cuts (not verdicts)
    sens, cuts = {}, {}
    out("\n-- sensitivity: season-week blocks")
    for b in ("home", "record", "elo_nomov"):
        sens[b] = compare(f"P1 model vs {b} [week blocks]", rows_for(pop1, base[b].get, "week"),
                          out, [], draws=a.draws)
    reg = [i for i in pop1 if games[i]["game_type"] == "REG"]
    early = [i for i in reg if games[i]["week"] <= 4]
    late = [i for i in reg if games[i]["week"] > 4]
    for cname, idx in (("REG only", reg), ("REG weeks 1-4", early), ("REG weeks 5+", late)):
        cuts[cname] = {}
        for b in ("home", "record", "elo_nomov"):
            cuts[cname][b] = compare(f"cut {cname}: model vs {b}", rows_for(idx, base[b].get),
                                     out, [], draws=a.draws)
    per_season = {}
    out("\n-- per season, dBrier model - baseline (game blocks)")
    for y in range(SCORE_FROM, SCORE_TO + 1):
        idx = [i for i in pop1 if games[i]["season"] == y]
        per_season[y] = {}
        row = []
        for b in ("home", "record", "elo_nomov"):
            pop = rc.Pop(str(y), rows_for(idx, base[b].get), "m", "k")
            r = pop.boot(lambda i, pop=pop: rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i]),
                         draws=min(a.draws, 1000))
            per_season[y][b] = r
            row.append(f"{b} {r['est']:+.4f} {rc.sign(r)}")
        out(f"   {y} n {len(idx)}: " + " | ".join(row))
    result["part1"]["sensitivity_week_blocks"] = sens
    result["part1"]["cuts"] = cuts
    result["part1"]["per_season"] = per_season
    counts = {b: {s: sum(1 for y in per_season if rc.sign(per_season[y][b]) == s)
                  for s in ("below", "contains 0", "above")} for b in base}
    result["part1"]["per_season_counts"] = counts
    out(f"   per-season interval counts: {counts}")

    # ---------------------------------------------------------------- Part 2a/2b Kalshi
    out(f"\n######## PART 2a/2b - Kalshi close, {KALSHI_SEASON} weeks {KALSHI_WEEKS}")
    kgames = [i for i, g in enumerate(games) if g["season"] == KALSHI_SEASON
              and g["week"] in KALSHI_WEEKS and g["game_type"] == "REG"]
    kscored = [i for i in kgames if scored(games[i])]
    out(f"   games in weeks {len(kgames)}, scored {len(kscored)}")
    fc = {games[i]["game_id"]: forecast(games, i, walk, sig, tot, "kickoff") for i in kscored}
    closes, census = kalshi_closes(con, by_id)
    result["kalshi_census"] = census
    out(f"   census: {json.dumps(census)}")
    # descriptive: the model on the 2026 games against settlement
    dec = [i for i in kscored if games[i]["home_score"] != games[i]["away_score"]]
    y26 = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in dec])
    p26 = np.array([fc[games[i]["game_id"]].p_home for i in dec])
    result["kalshi_2026_model_brier_settlement"] = {"n": len(dec), "brier": rc.brier(p26, y26)}
    out(f"   2026 wk1-3 model Brier vs settlement {rc.brier(p26, y26):.4f} on {len(dec)} decisive games")

    ml = defaultdict(dict)
    for c in closes:
        if c["series"] == "KXNFLGAME":
            ml[c["game_id"]][c["team"]] = c["mid"]
    ml_rows = []
    for gid, sides in ml.items():
        g = by_id[gid]
        if gid not in fc or g["home_score"] == g["away_score"]:
            continue
        h, aw = sides.get(g["home"]), sides.get(g["away"])
        if h is not None and aw is not None:
            k = h / (h + aw)
        elif h is not None:
            k = h
        elif aw is not None:
            k = 1.0 - aw
        else:
            continue
        ml_rows.append({"game": gid, "stat": "ml", "line": 0.0,
                        "y": 1.0 if g["home_score"] > g["away_score"] else 0.0,
                        "m": fc[gid].p_home, "k": k})
    p2 = {}
    if len(ml_rows) >= 2:
        p2["2a KXNFLGAME"] = compare("2a KXNFLGAME close", ml_rows, out, tests, draws=a.draws)
    else:
        out(f"   2a KXNFLGAME: {len(ml_rows)} games with a close - not scored")
        p2["2a KXNFLGAME"] = {"n": len(ml_rows), "not_scored": True}

    for series in ("KXNFLSPREAD", "KXNFLTOTAL"):
        rs = []
        keys = defaultdict(set)
        for c in closes:
            if c["series"] != series or c["game_id"] not in fc or c["line"] is None:
                continue
            L = float(c["line"])
            if abs(L - math.floor(L) - 0.5) > 1e-9:
                continue                                  # whole-number line: not priced
            g = by_id[c["game_id"]]
            f = fc[c["game_id"]]
            if series == "KXNFLSPREAD":
                team = c["team"]
                keys[ladder_key(c["market_id"])].add(team)
                side = "home" if team == g["home"] else ("away" if team == g["away"] else None)
                if side is None:
                    raise SystemExit(f"{c['market_id']}: team {team} not in {c['game_id']}")
                tm = (g["home_score"] - g["away_score"]) * (1 if side == "home" else -1)
                rs.append({"game": c["game_id"], "stat": f"spread:{side}", "line": L,
                           "y": 1.0 if tm > L else 0.0, "m": f.prob_team_by_over(team, L), "k": c["mid"]})
            else:
                tot_ = g["home_score"] + g["away_score"]
                rs.append({"game": c["game_id"], "stat": "total", "line": L,
                           "y": 1.0 if tot_ > L else 0.0, "m": f.prob_total_over(L), "k": c["mid"]})
        bad = {k: v for k, v in keys.items() if len(v) != 1}
        if bad:
            raise SystemExit(f"{series}: a ladder spans two teams - {list(bad.items())[:3]}")
        if len(rs) >= 2 and len({r['game'] for r in rs}) >= 2:
            p2[f"2b {series}"] = compare(f"2b {series} close (rungs)", rs, out, tests,
                                         within_line=True, draws=a.draws)
        else:
            p2[f"2b {series}"] = {"n": len(rs), "not_scored": True}
            out(f"   2b {series}: {len(rs)} rungs - not scored")
    result["part2_kalshi"] = p2

    # ---------------------------------------------------------------- Part 2c nflverse closes
    out(f"\n######## PART 2c - nflverse closing lines (registered, not in the brief)")
    mlr = []
    for i in pop1:
        g = by_id[games[i]["game_id"]]
        if g["season"] < ML_FROM:
            continue
        raw = con.execute("SELECT home_moneyline, away_moneyline FROM nfl_games WHERE game_id = ? "
                          "AND data_version = ?", (g["game_id"], g["data_version"])).fetchone()
        if raw is None or raw[0] is None or raw[1] is None:
            continue
        ih, ia = american(raw[0]), american(raw[1])
        mlr.append({"game": g["game_id"], "stat": "ml", "line": 0.0,
                    "y": 1.0 if g["home_score"] > g["away_score"] else 0.0,
                    "m": walk.p(i), "k": ih / (ih + ia)})
    p2c = {"moneyline": compare(f"2c nflverse moneyline close {ML_FROM}-{SCORE_TO}", mlr, out, tests,
                                draws=a.draws)}
    lines_ = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT g.game_id, g.spread_line, g.total_line FROM nfl_games g JOIN (SELECT game_id, "
        "MAX(data_version) dv FROM nfl_games GROUP BY game_id) v ON v.game_id = g.game_id "
        "AND v.dv = g.data_version")}
    sp_idx = [i for i in pop1 if lines_.get(games[i]["game_id"], (None,))[0] is not None]
    marg = np.array([games[i]["home_score"] - games[i]["away_score"] for i in sp_idx], float)
    mu = np.array([forecast(games, i, walk, sig, tot, "kickoff").margin_mean() for i in sp_idx])
    sl = np.array([lines_[games[i]["game_id"]][0] for i in sp_idx], float)
    p2c["spread"] = mse_compare(f"2c margin: model mu vs spread_line {SCORE_FROM}-{SCORE_TO}", marg, mu, sl,
                                [games[i]["game_id"] for i in sp_idx], out, tests, draws=a.draws)
    corr = float(np.corrcoef(mu, sl)[0, 1])
    p2c["spread"]["corr_mu_spread_line"] = corr
    out(f"   corr(model mu, spread_line) {corr:.3f}  (a sign check: spread_line is + when home is favoured)")
    t_idx = [i for i in pop1 if lines_.get(games[i]["game_id"], (None, None))[1] is not None and i in tot]
    tt = np.array([games[i]["home_score"] + games[i]["away_score"] for i in t_idx], float)
    mt = np.array([tot[i][0] for i in t_idx])
    tl = np.array([lines_[games[i]["game_id"]][1] for i in t_idx], float)
    p2c["total"] = mse_compare(f"2c total: model mu_t vs total_line {SCORE_FROM}-{SCORE_TO}", tt, mt, tl,
                               [games[i]["game_id"] for i in t_idx], out, tests, draws=a.draws)
    result["part2c_nflverse"] = p2c
    con.close()

    # ---------------------------------------------------------------- cost gate
    beats = [n for n, r in p2.items() if not r.get("not_scored") and rc.sign(r["diffs"]["dBrier"]) == "below"]
    result["cost"] = ("not run: no Kalshi dBrier interval lies below zero"
                      if not beats else f"REQUIRED for {beats} - fee_type from /series")
    out(f"\n   COST: {result['cost']}")

    n_ex = sum(1 for _n, _s, r in tests if rc.sign(r) in ("below", "above"))
    result["registered_intervals"] = {"count": len(tests), "exclude_zero": n_ex,
                                      "not_read": sum(1 for *_x, r in tests if rc.sign(r) == "not read")}
    out(f"   registered intervals: {len(tests)}, {n_ex} exclude zero; no multiplicity correction")
    result["log"] = lines
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=lambda o: o.as_dict() if hasattr(o, "as_dict") else str(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
