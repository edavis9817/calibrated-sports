"""c-39 - the game model, pointed at college, scored.

    python -m research.cfb_game_forecast --json-out research/results/cfb_game_forecast.json \
        --nfl-constants research/results/c39_nfl_constants.json

PRE-REGISTRATION: docs/C39-cfb-game-model-preregistration.md, committed and
pushed at 1787e1f BEFORE this script or models/cfb_game.py existed. This file
implements it; it does not extend it. Comments say only where the code carries
a rule out.

The forecast object is `models.game.GameForecast`; the walk is
`models.cfb_game`. The scoring statistics are c-24's
(`research.ranking_calibration`) and the comparison loop is c-28's
(`research.game_forecast.compare`), both imported, not copied.

cfb.db is opened mode=ro; market_log.db is not opened at all. Nothing is
written except --json-out and --log-out. Everything printed is an aggregate or
an interval - no game is named as a pick.
"""
from __future__ import annotations

import argparse
import datetime
import itertools
import json
import os
import sqlite3
import statistics
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import paths                                    # noqa: E402
from cfb import probe_promote                            # noqa: E402
from cfb.oddsapi_join import match, norm, team_names     # noqa: E402
from models import cfb_game as C                         # noqa: E402
from models import game as G                             # noqa: E402
from research import game_forecast as F                  # noqa: E402
from research import ranking_calibration as rc           # noqa: E402

FIRST_SEASON = 2001              # burn-in
FIT_FROM = 2002
SCORE_FROM, SCORE_TO = 2005, 2025
CURRENT = 2026
ML_FROM, SPREAD_FROM = 2021, 2013
TOTAL_WINDOW = 256
SIGMA_GRID = np.round(np.arange(10.0, 30.0001, 0.1), 1)
OVERROUND = (1.00, 1.15)
ODDS_MAX_AGE = 6 * 3600.0
BREAK_EVEN = 110.0 / 210.0       # -110
EDGE_POINTS = (0.0, 3.0, 7.0)
WEEK = 7 * 86400.0
WEEK_BREAK = 5.5 * 86400.0       # Tuesday 12:00Z, the CFB week boundary
POOLED = "FCS"

GRID_MOV = {"k": [20.0, 30.0, 40.0, 50.0, 60.0, 80.0, 100.0, 130.0, 160.0],
            "hfa": [0.0, 25.0, 40.0, 55.0, 70.0, 85.0, 100.0, 130.0],
            "regress": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8],
            "a": [1.0, 2.2, 4.0, None],
            "cap": [14.0, 21.0, 28.0, 35.0, 45.0, None],
            "conf_w": [0.0, 0.5, 1.0],
            "entry": [0.0, -100.0, -200.0, -300.0, -400.0]}
GRID_PLAIN = dict(GRID_MOV, k=[20.0, 30.0, 40.0, 50.0, 60.0, 80.0, 100.0, 130.0, 160.0, 200.0],
                  a=[None], cap=[None])
NAMES = ("k", "hfa", "regress", "a", "cap", "conf_w", "entry")


def grid(spec):
    return [C.CfbParams(*v) for v in itertools.product(*(spec[n] for n in NAMES))]


# =============================================================================
# loading
# =============================================================================

def connect():
    uri = "file:" + os.path.abspath(paths.db_path()).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def load(con, pooled_fcs=False):
    """FBS-FBS games (the primary rule). With `pooled_fcs` (S1) an FBS game
    against a non-FBS side is kept, that side becoming the one team POOLED, and
    carries fit = False."""
    out = []
    for r in con.execute(
            "SELECT game_id, season, week, season_type, start_ts, neutral_site, conference_game, "
            "home_id, away_id, home_division, away_division, home_conference, away_conference, "
            "home_points, away_points, completed FROM cfb_games WHERE valid_to_ts IS NULL "
            "AND season BETWEEN ? AND ?", (FIRST_SEASON, CURRENT)):
        hf, af = r[9] == "fbs", r[10] == "fbs"
        if not (hf and af) and not (pooled_fcs and (hf or af)):
            continue
        done = bool(r[15]) and r[13] is not None and r[14] is not None
        out.append({"game_id": str(r[0]), "season": r[1], "week": r[2], "season_type": r[3],
                    "start_ts": r[4], "neutral": int(bool(r[5])), "conference_game": r[6],
                    "home": str(r[7]) if hf else POOLED, "away": str(r[8]) if af else POOLED,
                    "home_conf": r[11] if hf else POOLED, "away_conf": r[12] if af else POOLED,
                    "home_score": r[13] if done else None, "away_score": r[14] if done else None,
                    "fit": hf and af})
    if len(out) < 10000:
        raise SystemExit("loaded %d games - refusing" % len(out))
    return out


def record_signs(con):
    """{game_id: +1 / -1 / 0} by c-28's rule, over EVERY completed game a team
    played that season, any opponent."""
    allg = []
    for r in con.execute(
            "SELECT game_id, season, start_ts, home_id, away_id, home_points, away_points, completed "
            "FROM cfb_games WHERE valid_to_ts IS NULL AND season BETWEEN ? AND ?",
            (FIRST_SEASON, CURRENT)):
        done = bool(r[7]) and r[5] is not None and r[6] is not None and r[5] != r[6]
        allg.append({"game_id": str(r[0]), "season": r[1], "kickoff_ts": r[2], "home": str(r[3]),
                     "away": str(r[4]), "home_score": r[5] if done else None,
                     "away_score": r[6] if done else None})
    signs = F.record_signs(allg)
    return {allg[i]["game_id"]: s for i, s in signs.items()}


# =============================================================================
# the walk-forward
# =============================================================================

class Walk:
    """Per-season params (fitted on FIT_FROM..T-1) and each game's as-of p."""

    def __init__(self, games, plist, mov):
        self.games, self.plist, self.mov = games, plist, mov
        self.seasons, self.sums, self.counts = C.grid_fit(games, plist, mov=mov)
        self._pre, self.fits = {}, {}

    def params(self, year):
        if year not in self.fits:
            p, ll, n = C.best_params(self.plist, self.seasons, self.sums, self.counts, FIT_FROM, year)
            self.fits[year] = {"params": p, "fit_log_loss": ll, "fit_games": n}
            # the vectorised walk must BE the scalar walk on the point it chose
            pre = self.pre(p)
            v = [C.log_loss(q, 1.0 if self.games[i]["home_score"] > self.games[i]["away_score"] else 0.0)
                 for i, q in pre.items()
                 if FIT_FROM <= self.games[i]["season"] < year and self.games[i]["fit"]]
            if len(v) != n or abs(float(np.mean(v)) - ll) > 1e-9:
                raise SystemExit("grid and scalar walks disagree for %d: %r vs %r (n %d vs %d)"
                                 % (year, float(np.mean(v)), ll, len(v), n))
        return self.fits[year]["params"]

    def pre(self, params):
        if params not in self._pre:
            pre, _r, _s = C.run(self.games, params, mov=self.mov)
            self._pre[params] = dict(pre)
        return self._pre[params]

    def p(self, i):
        return self.pre(self.params(self.games[i]["season"]))[i]


def edge_hits(walk, spec, years):
    """Every (constant, season) whose fitted value is the first or last grid value."""
    out = defaultdict(list)
    for y in years:
        d = walk.params(y).as_dict()
        for n in NAMES:
            vals = spec[n]
            if len(vals) > 1 and d[n] in (vals[0], vals[-1]):
                out["%s=%s (%s)" % (n, d[n], "first" if d[n] == vals[0] else "last")].append(y)
    return dict(out)


def margin_sigmas(games, walk, years):
    out = {}
    for y in years:
        pre = walk.pre(walk.params(y))
        idx = [i for i in pre if FIT_FROM <= games[i]["season"] < y and games[i]["fit"]]
        p = np.array([pre[i] for i in idx])
        m = np.array([games[i]["home_score"] - games[i]["away_score"] for i in idx], float)
        out[y] = G.margin_sigma_mle(p, m, grid=SIGMA_GRID)
    return out


def total_params(games):
    """{index: (mu_t, sigma_t)} from the TOTAL_WINDOW completed games before it.
    Carried so the object is complete; not scored (pre-registration)."""
    order = sorted(range(len(games)), key=lambda i: (games[i]["start_ts"], games[i]["game_id"]))
    hist, out = [], {}
    pending = []
    last = None
    for i in order:
        g = games[i]
        if last is not None and g["start_ts"] > last:
            hist.extend(pending)
            pending = []
        last = g["start_ts"]
        if len(hist) >= TOTAL_WINDOW:
            a = np.array(hist[-TOTAL_WINDOW:], float)
            out[i] = (float(a.mean()), float(a.std(ddof=1)))
        if C.completed(g) and g["fit"]:
            pending.append(g["home_score"] + g["away_score"])
    return out


def baseline_constants(games, signs, year):
    """(home rate non-neutral, home rate neutral, q) on FIT_FROM..year-1."""
    hw = [0, 0]
    hn = [0, 0]
    rw = rn = 0
    for g in games:
        if not (FIT_FROM <= g["season"] < year) or not g["fit"] or not C.completed(g):
            continue
        d = g["home_score"] - g["away_score"]
        hn[g["neutral"]] += 1
        hw[g["neutral"]] += d > 0
        s = signs.get(g["game_id"], 0)
        if s != 0:
            rn += 1
            rw += (d > 0) == (s > 0)
    return hw[0] / hn[0], hw[1] / hn[1], rw / rn


def week_block(g):
    return "%d-%d" % (g["season"], int((g["start_ts"] - WEEK_BREAK) // WEEK))


def brier_diff(name, rows, out, draws):
    """dBrier only (cuts and sensitivities, which are not verdicts)."""
    if len({r["game"] for r in rows}) < 2:
        out(f"   {name:<44} n {len(rows)} - not scored")
        return {"n": len(rows), "not_scored": True}
    pop = rc.Pop(name, rows, "m", "k")
    r = pop.boot(lambda i: rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i]), draws=draws)
    r["brier_model"], r["brier_comparator"] = rc.brier(pop.m, pop.y), rc.brier(pop.k, pop.y)
    out(f"   {name:<44} n {pop.n:>6,} blocks {pop.games:>6,}  BS {r['brier_model']:.4f} vs "
        f"{r['brier_comparator']:.4f}  dBrier {rc.fmt(r)} -> {rc.sign(r)}")
    return r


# =============================================================================
# prices on disk
# =============================================================================

def devig(home_ml, away_ml):
    """Normalised home probability, or None outside the overround band."""
    if not home_ml or not away_ml:
        return None
    ih, ia = F.american(home_ml), F.american(away_ml)
    return ih / (ih + ia) if OVERROUND[0] <= ih + ia <= OVERROUND[1] else None


def cfbd_lines(con):
    """({game_id: median de-vigged home p}, {game_id: median spread}, census)."""
    ml, sp = defaultdict(list), defaultdict(list)
    census = {"ml_rows": 0, "ml_outside_overround": 0, "spread_rows": 0}
    for gid, prov, spread, hm, am in con.execute(
            "SELECT game_id, provider, spread, home_moneyline, away_moneyline FROM cfb_game_lines "
            "WHERE valid_to_ts IS NULL"):
        if spread is not None:
            sp[str(gid)].append(float(spread))
            census["spread_rows"] += 1
        if hm is not None and am is not None:
            census["ml_rows"] += 1
            k = devig(hm, am)
            if k is None:
                census["ml_outside_overround"] += 1
            else:
                ml[str(gid)].append(k)
    return ({g: statistics.median(v) for g, v in ml.items()},
            {g: statistics.median(v) for g, v in sp.items()}, census)


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def odds_api_closes(con):
    """({game_id: (median de-vigged home p, books, median age s)}, census): per
    book the last h2h pair fetched strictly before commence and <= 6h before it."""
    events = {e: (ct, h, a) for e, ct, h, a in con.execute(
        "SELECT event_id, commence_ts, home_team, away_team FROM cfb_odds_events")}
    last = {}
    for eid, ct, book, name, price, fts in con.execute(
            "SELECT event_id, commence_ts, bookmaker, outcome_name, price, fetched_ts FROM cfb_odds_quotes "
            "WHERE market_key = 'h2h' AND fetched_ts < commence_ts AND fetched_ts >= commence_ts - ?",
            (ODDS_MAX_AGE,)):
        k = (eid, book)
        if k not in last or fts > last[k][0]:
            last[k] = (fts, ct, {})
        if fts == last[k][0]:
            last[k][2][name] = price
    ev_list = [{"id": e, "commence_time": _iso(ct), "home_team": h, "away_team": a}
               for e, (ct, h, a) in events.items()]
    _g, matched, missed, ambiguous, _fb = match(con, ev_list, CURRENT)
    names = team_names(con, CURRENT)
    census = {"events": len(events), "matched": len(matched), "missed": len(missed),
              "ambiguous": len(ambiguous), "outside_overround": 0, "no_fresh_pair": 0}
    out = {}
    for e, g in matched:
        ct, h, a = events[e["id"]]
        same_home = names.get(g[4], (None,))[0] == norm(h)
        ps, ages = [], []
        for (eid, _book), (fts, qct, pr) in last.items():
            if eid != e["id"] or h not in pr or a not in pr:
                continue
            k = devig(pr[h], pr[a])
            if k is None:
                census["outside_overround"] += 1
                continue
            ps.append(k if same_home else 1.0 - k)
            ages.append(qct - fts)
        if not ps:
            census["no_fresh_pair"] += 1
            continue
        out[str(g[0])] = (statistics.median(ps), len(ps), statistics.median(ages))
    return out, census


def kalshi_closes(con):
    """({game_id: home p}, census) from the probe's two-sided moneyline closes."""
    games = {str(r[0]): r[1:] for r in con.execute(
        "SELECT game_id, home_team, away_team, home_abbreviation, away_abbreviation FROM cfb_games "
        "WHERE valid_to_ts IS NULL AND season = ?", (CURRENT,))}
    sides = defaultdict(dict)
    census = {"closes": 0, "side_unmatched": 0, "no_game": 0}
    for mid, subject, gid, m in con.execute(
            "SELECT c.market_id, m.subject, c.game_id, c.mid FROM cfb_exchange_closes c "
            "JOIN cfb_exchange_markets m ON m.venue = c.venue AND m.market_id = c.market_id "
            "AND m.valid_to_ts IS NULL WHERE c.valid_to_ts IS NULL AND c.venue = 'cfb_kalshi' "
            "AND m.market_type = 'moneyline' AND c.book_state = 'two_sided'"):
        census["closes"] += 1
        g = games.get(str(gid))
        if g is None:
            census["no_game"] += 1
            continue
        s, tail = probe_promote.norm(subject), mid.rsplit("-", 1)[-1]
        is_h = s == probe_promote.norm(g[0]) or tail == g[2]
        is_a = s == probe_promote.norm(g[1]) or tail == g[3]
        if is_h == is_a:
            census["side_unmatched"] += 1
            continue
        sides[str(gid)]["home" if is_h else "away"] = m
    out = {}
    for gid, d in sides.items():
        h, a = d.get("home"), d.get("away")
        out[gid] = h / (h + a) if h is not None and a is not None else (h if h is not None else 1.0 - a)
    census["games"] = len(out)
    return out, census


# =============================================================================
# main
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--log-out")
    ap.add_argument("--nfl-constants", required=True,
                    help="research/c39_nfl_constants.py's output: c-28's 2026 NFL fit")
    ap.add_argument("--draws", type=int, default=rc.BOOT)
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    with open(a.nfl_constants, encoding="utf-8") as f:
        nfl = json.load(f)
    for key in ("mov", "nomov", "sigma_m"):
        if key not in nfl:
            raise SystemExit("--nfl-constants has no %r" % key)

    tests = []
    result = {"preregistration": "docs/C39-cfb-game-model-preregistration.md @ 1787e1f"}
    con = connect()
    games = load(con)
    signs = record_signs(con)
    done = [i for i, g in enumerate(games) if C.completed(g)]
    out(f"FBS-FBS games loaded {len(games):,}, completed {len(done):,}, "
        f"equal-score rows dropped {sum(1 for g in games if g['home_score'] is not None and g['home_score'] == g['away_score'])}")
    result["loaded"] = {"games": len(games), "completed": len(done)}

    t0 = time.time()
    gm, gp = grid(GRID_MOV), grid(GRID_PLAIN)
    walk = Walk(games, gm, mov=True)
    out(f"MOV grid {len(gm):,} points {time.time() - t0:.0f}s; "
        f"points dropped for a non-positive multiplier denominator {int(np.isnan(walk.sums[0]).sum()):,}")
    t0 = time.time()
    walk0 = Walk(games, gp, mov=False)
    out(f"plain grid {len(gp):,} points {time.time() - t0:.0f}s")
    years = list(range(SCORE_FROM, CURRENT + 1))
    sig = margin_sigmas(games, walk, years)
    tot = total_params(games)
    result["fits"] = {y: {"mov": walk.params(y).as_dict(), "nomov": walk0.params(y).as_dict(),
                          "sigma_m": sig[y], "fit_games": walk.fits[y]["fit_games"],
                          "fit_log_loss_mov": walk.fits[y]["fit_log_loss"],
                          "fit_log_loss_nomov": walk0.fits[y]["fit_log_loss"]} for y in years}
    out("\n-- fitted constants by season (MOV | plain) and sigma_m")
    for y in years:
        m_, n_ = walk.params(y), walk0.params(y)
        out(f"   {y}  k {m_.k:>5.0f} hfa {m_.hfa:>4.0f} reg {m_.regress:.2f} a {str(m_.a):>4} cap {str(m_.cap):>4} "
            f"conf_w {m_.conf_w:.1f} entry {m_.entry:>5.0f} | k {n_.k:>5.0f} hfa {n_.hfa:>4.0f} reg {n_.regress:.2f} "
            f"conf_w {n_.conf_w:.1f} entry {n_.entry:>5.0f} | sigma_m {sig[y]:.1f}")
    result["grid_edges"] = {"mov": edge_hits(walk, GRID_MOV, years), "nomov": edge_hits(walk0, GRID_PLAIN, years)}
    out(f"   grid edges hit, MOV: {json.dumps(result['grid_edges']['mov'])}")
    out(f"   grid edges hit, plain: {json.dumps(result['grid_edges']['nomov'])}")

    # ------------------------------------------------------------ constants table
    cur = walk.params(CURRENT).as_dict()
    rng_ = {n: sorted({str(walk.params(y).as_dict()[n]) for y in years[:-1]}) for n in NAMES}
    table = [("k", nfl["mov"]["k"], cur["k"]), ("hfa (Elo points)", nfl["mov"]["hfa"], cur["hfa"]),
             ("preseason regression", nfl["mov"]["regress"], cur["regress"]),
             ("multiplier damping a", G.MOV_A, cur["a"]), ("blowout cap (points)", None, cur["cap"]),
             ("conference weight in the target", "not in the model", cur["conf_w"]),
             ("entry offset", 0.0, cur["entry"]),
             ("home advantage on a neutral site", "applied (no flag)", 0.0),
             ("sigma_m (points)", nfl["sigma_m"], sig[CURRENT]),
             ("plain Elo k", nfl["nomov"]["k"], walk0.params(CURRENT).k),
             ("plain Elo hfa", nfl["nomov"]["hfa"], walk0.params(CURRENT).hfa),
             ("plain Elo regression", nfl["nomov"]["regress"], walk0.params(CURRENT).regress)]
    out(f"\n-- constants, NFL (c-28's {CURRENT} fit) beside college ({CURRENT} fit; range over {SCORE_FROM}-{SCORE_TO} fits)")
    for name, nv, cv in table:
        key = {"k": "k", "hfa (Elo points)": "hfa", "preseason regression": "regress",
               "multiplier damping a": "a", "blowout cap (points)": "cap",
               "conference weight in the target": "conf_w", "entry offset": "entry"}.get(name)
        out(f"   {name:<34} NFL {str(nv):<18} college {str(cv):<8} {('range ' + ', '.join(rng_[key])) if key else ''}")
    result["constants_table"] = [{"constant": n, "nfl": nv, "college": cv} for n, nv, cv in table]
    result["constants_range"] = rng_

    # ---------------------------------------------------------------- Part 1
    pop1 = [i for i in done if SCORE_FROM <= games[i]["season"] <= SCORE_TO]
    consts = {y: baseline_constants(games, signs, y) for y in years}
    result["baseline_constants"] = {y: {"home_rate": c[0], "home_rate_neutral": c[1],
                                        "q_better_record": c[2]} for y, c in consts.items()}

    def base(b, i):
        g = games[i]
        h0, h1, q = consts[g["season"]]
        h = h1 if g["neutral"] else h0
        if b == "home":
            return h
        if b == "record":
            s = signs.get(g["game_id"], 0)
            return h if s == 0 else (q if s > 0 else 1.0 - q)
        return walk0.p(i)

    def rows_for(idx, comp, block="game", model=None):
        model = walk.p if model is None else model
        return [{"game": games[i]["game_id"] if block == "game" else week_block(games[i]),
                 "stat": "ml", "line": 0.0,
                 "y": 1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0,
                 "m": model(i), "k": comp(i)} for i in idx]

    out(f"\n######## PART 1 - against settlement, {SCORE_FROM}-{SCORE_TO}, FBS-FBS decisive games (PRIMARY)")
    p1 = {}
    for b in ("home", "record", "elo_nomov"):
        p1[b] = F.compare(f"P1 model vs {b}", rows_for(pop1, lambda i, b=b: base(b, i)), out, tests,
                          draws=a.draws)
    verdict = {b: rc.sign(p1[b]["diffs"]["dBrier"]) for b in p1}
    success = all(v == "below" for v in verdict.values())
    result["part1"] = {"results": p1, "dBrier_sign": verdict, "success": success}
    out(f"\n   PRIMARY VERDICT: dBrier vs home {verdict['home']}, record {verdict['record']}, "
        f"elo_nomov {verdict['elo_nomov']} -> {'SUCCESS: beats all three baselines' if success else 'NOT all three'}")

    out("\n-- not verdicts: season-week blocks, cuts (dBrier, model - baseline)")
    cuts = {"week blocks": pop1,
            "regular weeks 1-4": [i for i in pop1 if games[i]["season_type"] == "regular" and games[i]["week"] <= 4],
            "regular weeks 5+": [i for i in pop1 if games[i]["season_type"] == "regular" and games[i]["week"] > 4],
            "postseason": [i for i in pop1 if games[i]["season_type"] == "postseason"],
            "conference games": [i for i in pop1 if games[i]["conference_game"]],
            "non-conference games": [i for i in pop1 if not games[i]["conference_game"]],
            "neutral site": [i for i in pop1 if games[i]["neutral"]]}
    result["part1"]["cuts"] = {}
    for cname, idx in cuts.items():
        result["part1"]["cuts"][cname] = {}
        for b in ("home", "record", "elo_nomov"):
            result["part1"]["cuts"][cname][b] = brier_diff(
                f"{cname}: vs {b}", rows_for(idx, lambda i, b=b: base(b, i),
                                             "week" if cname == "week blocks" else "game"), out, a.draws)
    per_season = {}
    for y in range(SCORE_FROM, SCORE_TO + 1):
        idx = [i for i in pop1 if games[i]["season"] == y]
        per_season[y] = {}
        for b in ("home", "record", "elo_nomov"):
            pop = rc.Pop(str(y), rows_for(idx, lambda i, b=b: base(b, i)), "m", "k")
            per_season[y][b] = pop.boot(
                lambda i, pop=pop: rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i]),
                draws=min(a.draws, 1000))
        out(f"   {y} n {len(idx)}: " + " | ".join(
            f"{b} {per_season[y][b]['est']:+.4f} {rc.sign(per_season[y][b])}" for b in per_season[y]))
    counts = {b: {s: sum(1 for y in per_season if rc.sign(per_season[y][b]) == s)
                  for s in ("below", "contains 0", "above")} for b in ("home", "record", "elo_nomov")}
    result["part1"]["per_season"] = per_season
    result["part1"]["per_season_counts"] = counts
    out(f"   per-season interval counts: {counts}")

    # sensitivity S1: one pooled FCS pseudo-team
    out("\n-- sensitivity S1: FBS-FCS games walked against one pooled FCS team (model = S1, comparator = primary)")
    t0 = time.time()
    pooled = load(con, pooled_fcs=True)
    walk_s1 = Walk(pooled, gm, mov=True)
    by_gid = {g["game_id"]: j for j, g in enumerate(pooled)}
    s1 = F.compare("S1 pooled-FCS walk vs primary",
                   rows_for(pop1, walk.p, model=lambda i: walk_s1.p(by_gid[games[i]["game_id"]])),
                   out, [], draws=a.draws)
    result["part1"]["S1"] = {"result": s1, "games_walked": sum(C.completed(g) for g in pooled),
                             "fit_2026": walk_s1.params(CURRENT).as_dict(), "seconds": time.time() - t0}

    # arm N: the NFL constants carried across unchanged
    pn = C.CfbParams(k=nfl["mov"]["k"], hfa=nfl["mov"]["hfa"], regress=nfl["mov"]["regress"],
                     a=G.MOV_A, cap=None, conf_w=0.0, entry=0.0)
    pre_n = dict(C.run(games, pn, mov=True, hfa_on_neutral=True)[0])
    out(f"\n-- arm N: the NFL constants carried across unchanged {pn.as_dict()} (model = college fit, comparator = arm N)")
    arm_n = F.compare("college fit vs arm N", rows_for(pop1, pre_n.get), out, [], draws=a.draws)
    result["part1"]["arm_N"] = {"params": pn.as_dict(), "result": arm_n}

    # 2026, descriptive
    cur_idx = [i for i in done if games[i]["season"] == CURRENT]
    y26 = np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in cur_idx])
    desc = {"n": len(cur_idx), "model": rc.brier(np.array([walk.p(i) for i in cur_idx]), y26)}
    for b in ("home", "record", "elo_nomov"):
        desc[b] = rc.brier(np.array([base(b, i) for i in cur_idx]), y26)
    result["current_season_descriptive"] = desc
    out(f"\n-- {CURRENT} to date, descriptive (no interval): n {desc['n']}  Brier model {desc['model']:.4f}  "
        f"home {desc['home']:.4f}  record {desc['record']:.4f}  elo_nomov {desc['elo_nomov']:.4f}")

    # ---------------------------------------------------------------- Part 2
    out("\n######## PART 2 - against a price, where one is on disk")
    ml, sp, census = cfbd_lines(con)
    result["cfbd_census"] = census
    out(f"   CFBD census: {json.dumps(census)}")
    p2 = {}

    idx = [i for i in pop1 if games[i]["season"] >= ML_FROM and games[i]["game_id"] in ml]
    out(f"\n   2a LABEL: the provider's last value, read after the game, no capture time - not a timestamped close")
    p2["2a"] = F.compare(f"2a CFBD moneyline {ML_FROM}-{SCORE_TO} (median over providers)",
                         rows_for(idx, lambda i: ml[games[i]["game_id"]]), out, tests, draws=a.draws)
    p2["2a"]["eligible_games"] = sum(1 for i in pop1 if games[i]["season"] >= ML_FROM)
    p2["2a"]["brier_ratio_model_over_price"] = p2["2a"]["corp_model"]["bs"] / p2["2a"]["corp_comparator"]["bs"]
    p2["2a"]["vs_baselines_same_games"] = {}
    for b in ("home", "record", "elo_nomov"):
        p2["2a"]["vs_baselines_same_games"][b] = brier_diff(
            f"2a price vs {b} (same games)", rows_for(idx, lambda i, b=b: base(b, i),
                                                      model=lambda i: ml[games[i]["game_id"]]), out, a.draws)
    out(f"   2a: {len(idx):,} of {p2['2a']['eligible_games']:,} eligible games carry a moneyline; "
        f"Brier(model)/Brier(price) {p2['2a']['brier_ratio_model_over_price']:.4f}")
    if nfl.get("c28_moneyline_gap"):
        g_ = nfl["c28_moneyline_gap"]
        out(f"   beside it, c-28's NFL gap to the nflverse moneyline close: dBrier {g_['dBrier']:+.4f}, "
            f"Brier ratio {g_.get('brier_ratio')}. A smaller college gap is not by itself evidence of a "
            f"softer market: base uncertainty differs and this college price is not a timestamped close.")

    idx = [i for i in pop1 if games[i]["season"] >= SPREAD_FROM and games[i]["game_id"] in sp]
    fc = {i: G.GameForecast(game_id=games[i]["game_id"], home=games[i]["home"], away=games[i]["away"],
                            as_of="kickoff", p_home=walk.p(i), sigma_m=sig[games[i]["season"]],
                            mu_t=tot.get(i, (0.0, 1.0))[0], sigma_t=tot.get(i, (0.0, 1.0))[1]) for i in idx}
    marg = np.array([games[i]["home_score"] - games[i]["away_score"] for i in idx], float)
    line = np.array([-sp[games[i]["game_id"]] for i in idx])          # expected home margin
    mu = np.array([fc[i].margin_mean() for i in idx])
    corr = float(np.corrcoef(line, marg)[0, 1])
    out(f"\n   2b sign check: corr(-spread, home margin) {corr:+.3f} on {len(idx):,} games")
    if not corr > 0:
        raise SystemExit("2b: -spread does not correlate positively with the home margin - convention wrong")
    p2["2b"] = {"corr_line_margin": corr, "corr_mu_line": float(np.corrcoef(mu, line)[0, 1])}
    p2["2b"]["mse"] = F.mse_compare(f"2b margin: model mu vs CFBD spread {SPREAD_FROM}-{SCORE_TO}", marg, mu,
                                    line, [games[i]["game_id"] for i in idx], out, tests, draws=a.draws)
    cover = [{"game": games[i]["game_id"], "stat": "cover", "line": 0.0,
              "y": 1.0 if marg[j] > line[j] else 0.0, "m": fc[i].prob_margin_over(line[j]), "k": 0.5}
             for j, i in enumerate(idx) if marg[j] != line[j]]
    p2["2b"]["pushes"] = len(idx) - len(cover)
    p2["2b"]["cover"] = F.compare("2b cover: model P(margin > line) vs 0.5", cover, out, tests, draws=a.draws)
    p2["2b"]["hit_rate"] = {}
    for t in EDGE_POINTS:
        rs = []
        for j, i in enumerate(idx):
            e = mu[j] - line[j]
            if marg[j] == line[j] or e == 0 or abs(e) < t:
                continue
            rs.append({"game": games[i]["game_id"], "stat": "x", "line": 0.0, "m": 0.0, "k": 0.0,
                       "y": 1.0 if (marg[j] > line[j]) == (e > 0) else 0.0})
        pop = rc.Pop(f"hit{t}", rs, "m", "k")
        r = pop.boot(lambda ii, pop=pop: float(pop.y[ii].mean()), draws=a.draws)
        r["beats_coin"] = bool(r["lo"] is not None and r["lo"] > 0.5)
        r["beats_break_even"] = bool(r["lo"] is not None and r["lo"] > BREAK_EVEN)
        p2["2b"]["hit_rate"][str(t)] = r
        tests.append((f"2b hit rate |edge| >= {t}", "hit - 0.5", dict(r, est=r["est"] - 0.5,
                                                                      lo=r["lo"] - 0.5, hi=r["hi"] - 0.5)))
        out(f"   2b hit rate, model's side, |mu - line| >= {t:.0f}: n {pop.n:,}  {r['est']:.4f} "
            f"[{r['lo']:.4f}, {r['hi']:.4f}]  vs 0.5: {'above' if r['beats_coin'] else ('below' if r['hi'] < 0.5 else 'contains')}"
            f"   vs break-even {BREAK_EVEN:.4f}: {'ABOVE' if r['beats_break_even'] else 'not above'}")

    oc, ocen = odds_api_closes(con)
    out(f"\n   2c Odds API census: {json.dumps(ocen)}")
    idx = [i for i in cur_idx if games[i]["game_id"] in oc]
    p2["2c"] = {"census": ocen, "games": len(idx)}
    if len(idx) >= 2:
        p2["2c"]["median_age_s"] = statistics.median(oc[games[i]["game_id"]][2] for i in idx)
        p2["2c"]["median_books"] = statistics.median(oc[games[i]["game_id"]][1] for i in idx)
        out(f"   2c: {len(idx)} completed FBS-FBS {CURRENT} games with a timestamped pre-kickoff h2h; "
            f"median quote age {p2['2c']['median_age_s']:.0f}s, median books {p2['2c']['median_books']}")
        p2["2c"]["result"] = F.compare(f"2c Odds API pre-kickoff h2h {CURRENT} (timestamped)",
                                       rows_for(idx, lambda i: oc[games[i]["game_id"]][0]), out, tests,
                                       draws=a.draws)
    else:
        out(f"   2c: {len(idx)} games - not scored")

    kc, kcen = kalshi_closes(con)
    out(f"\n   2d Kalshi probe census: {json.dumps(kcen)}")
    idx = [i for i in cur_idx if games[i]["game_id"] in kc]
    p2["2d"] = {"census": kcen, "games": len(idx)}
    if len(idx) >= 2:
        p2["2d"]["result"] = F.compare("2d Kalshi probe moneyline close, one weekend",
                                       rows_for(idx, lambda i: kc[games[i]["game_id"]]), out, tests,
                                       draws=a.draws)
    else:
        out(f"   2d: {len(idx)} games - not scored")
    result["part2"] = p2
    con.close()

    # ---------------------------------------------------------------- the reading, by the registered rule
    beats = [n for n in ("2a", "2c") if p2[n].get("result", p2[n]).get("diffs")
             and rc.sign(p2[n].get("result", p2[n])["diffs"]["dBrier"]) == "below"]
    beats += [f"2b hit rate >= {t}" for t, r in p2["2b"]["hit_rate"].items() if r["beats_break_even"]]
    a2 = rc.sign(p2["2a"]["diffs"]["dBrier"])
    any_coin = any(r["beats_coin"] for r in p2["2b"]["hit_rate"].values())
    if beats:
        reading = "the competition was the problem; college is softer - COST ANALYSIS REQUIRED: %s" % beats
    elif a2 == "above" and not any_coin:
        reading = "the method is the limit"
    else:
        reading = "neither (2a dBrier %s; a 2b hit-rate interval above 0.5: %s)" % (a2, any_coin)
    result["reading"] = reading
    result["cost"] = "REQUIRED and not in this unit" if beats else "not run: the model beats no price"
    out(f"\n   READING (registered rule): {reading}")
    out(f"   COST: {result['cost']}")

    n_ex = sum(1 for _n, _s, r in tests if rc.sign(r) in ("below", "above"))
    result["registered_intervals"] = {"count": len(tests), "exclude_zero": n_ex,
                                      "not_read": sum(1 for *_x, r in tests if rc.sign(r) == "not read")}
    out(f"   registered intervals: {len(tests)}, {n_ex} exclude zero; no multiplicity correction")
    result["log"] = lines
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=lambda o: o.as_dict() if hasattr(o, "as_dict") else str(o))
    if a.log_out:
        with open(a.log_out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
