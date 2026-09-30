"""c-32 - does the model's disagreement with the open predict where the line closes.

    LOGGER_DB=<market_log.db> python -m research.open_line_move --json-out D:/temp/c32/result.json

PRE-REGISTRATION: docs/C32-open-line-move-preregistration.md, committed and
pushed at c9b15cd BEFORE this script existed. This file implements it; it does
not extend it. Comments say only where the code carries a rule out.

The model is c-28's margin (`research.game_forecast`) and c-31's total
(`research.game_total`, NO_WIND arm primary), rebuilt from their own functions
and walk-forward fits, imported, not copied, and not edited. The c-31 total's
factor states are rebuilt AS OF each game's information cut.

market_log.db and analytics.db are opened mode=ro; nothing is written except
--json-out. Printed output is aggregates and intervals only - no game is named.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from itertools import groupby

import numpy as np
from scipy.stats import norm, spearmanr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fees                                   # noqa: E402
from jobs import season_model as S                      # noqa: E402
from models import game as G                            # noqa: E402
from models import game_total as GT                     # noqa: E402
from research import game_forecast as GF                # noqa: E402
from research import game_total as GTR                  # noqa: E402
from research import ranking_calibration as rc          # noqa: E402

SEASON = 2026
WEEKS = (2, 3)
SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL")
MARKET = {"KXNFLGAME": "moneyline", "KXNFLSPREAD": "spread", "KXNFLTOTAL": "total"}
FINISH = 5 * 3600            # a game is over 5h after its kickoff
MIN_LEAD = 24 * 3600
OPEN_WINDOW = 3600
SNAP_B = (7200, 10800)       # addendum 1: the second open snapshot, [t0 + 2h, t0 + 3h)
CLOSE_MAX_AGE = GF.CLOSE_MAX_AGE     # 1800
MAX_SPREAD = 0.10
MID_LO, MID_HI = 0.10, 0.90
CONTRACTS = 100
C28_P1_BRIER = GTR.C28_P1_BRIER


# =============================================================================
# pure pieces (tested in tests/test_open_line_move.py)
# =============================================================================

def info_cut(games, i):
    """max(previous kickoff of home, of away) + FINISH, over games of the same season."""
    g = games[i]
    teams = {G.franchise(g["home"]), G.franchise(g["away"])}
    prev = [h["kickoff_ts"] for h in games
            if h["season"] == g["season"] and h["kickoff_ts"] is not None
            and h["kickoff_ts"] < g["kickoff_ts"]
            and {G.franchise(h["home"]), G.franchise(h["away"])} & teams]
    return (max(prev) + FINISH) if prev else None


def eligible(q, line):
    """A quote usable for an implied number: half-point line, tight, not a tail."""
    bid, ask = q["bid"], q["ask"]
    if line is None or abs(line - math.floor(line) - 0.5) > 1e-9:
        return False
    mid = (bid + ask) / 2.0
    return ask - bid <= MAX_SPREAD + 1e-12 and MID_LO <= mid <= MID_HI


def implied_mean(points, sigma):
    """points: [(x, P(Y > x))]. p(1-p)-weighted mean of x + sigma * PhiInv(p). <2 points -> None."""
    if len(points) < 2:
        return None
    w = np.array([p * (1 - p) for _x, p in points])
    mu = np.array([x + sigma * norm.ppf(p) for x, p in points])
    return float((w * mu).sum() / w.sum())


def crossing_median(points):
    """Linear interpolation of P(Y > x) at 0.5; None unless the points bracket 0.5."""
    pts = sorted(points)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if (p0 - 0.5) * (p1 - 0.5) <= 0 and p0 != p1:
            return float(x0 + (p0 - 0.5) * (x1 - x0) / (p0 - p1))
    return None


def spread_point(team, home, away, line, mid):
    """A spread rung as (x, P(home margin > x))."""
    if team == home:
        return (line, mid)
    if team == away:
        return (-line, 1.0 - mid)
    raise ValueError(f"team {team} is neither {home} nor {away}")


def ml_home(sides, home, away):
    """c-28's moneyline rule: h/(h+a), else the one side."""
    h, a = sides.get(home), sides.get(away)
    if h is not None and a is not None:
        return h / (h + a)
    if h is not None:
        return h
    if a is not None:
        return 1.0 - a
    return None


def slope_r(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None, None
    b = float(np.polyfit(x, y, 1)[0])
    return b, float(np.corrcoef(x, y)[0, 1])


def boot(fn, n, draws, seed=rc.SEED):
    """Rows are games: resample rows. fn(idx) -> float|None."""
    est = fn(np.arange(n))
    rng = np.random.default_rng(seed)
    v = [fn(rng.integers(0, n, n)) for _ in range(draws)]
    v = np.array([x for x in v if x is not None])
    if est is None or n < 2 or not len(v):
        return {"est": est, "lo": None, "hi": None, "se": None, "games": n, "n": n, "mde": None}
    se = float(v.std())
    return {"est": est, "lo": float(np.percentile(v, 2.5)), "hi": float(np.percentile(v, 97.5)),
            "se": se, "games": n, "n": n, "mde": 2.8 * se}


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def pivot_trade(open_q, close_q, model_p, fee_fn):
    """(side, signed move, cost) for one rung. YES if the model's probability exceeds the open mid."""
    om = (open_q["bid"] + open_q["ask"]) / 2.0
    cm = (close_q["bid"] + close_q["ask"]) / 2.0
    if model_p > om:
        return "yes", cm - om, (open_q["ask"] - om) + fee_fn(open_q["ask"])
    return "no", om - cm, (om - open_q["bid"]) + fee_fn(1.0 - open_q["bid"])


def bands(mmo, com, k=3):
    """Terciles of |model_minus_open|: n, mean |move|, mean signed move toward the model, ratio."""
    mmo, com = np.asarray(mmo, float), np.asarray(com, float)
    order = np.argsort(np.abs(mmo), kind="stable")
    out = []
    for j, idx in enumerate(np.array_split(order, k)):
        if not len(idx):
            continue
        uns = float(np.abs(com[idx]).mean())
        sgn = float((np.sign(mmo[idx]) * com[idx]).mean())
        out.append({"band": j + 1, "n": int(len(idx)),
                    "abs_mmo_range": [float(np.abs(mmo[idx]).min()), float(np.abs(mmo[idx]).max())],
                    "mean_abs_move": uns, "mean_signed_toward_model": sgn,
                    "ratio": sgn / uns if uns > 0 else None})
    return out


# =============================================================================
# quotes
# =============================================================================

def mapped_markets(con, by_id):
    rows = con.execute(
        "SELECT m.market_id, m.line, o.event_id, o.entity_id, o.line "
        "FROM markets m JOIN market_outcome mo ON mo.venue = m.venue AND mo.market_id = m.market_id "
        "JOIN outcomes o ON o.outcome_id = mo.outcome_id "
        "WHERE m.venue = 'kalshi' AND (" + " OR ".join("m.market_id LIKE ?" for _ in SERIES) + ")",
        [s + "-%" for s in SERIES]).fetchall()
    out = defaultdict(list)          # (game_id, series) -> [market dict]
    for mid, mline, gid, ent, oline in rows:
        g = by_id.get(gid)
        if g is None or g["season"] != SEASON or g["week"] not in WEEKS or g["game_type"] != "REG":
            continue
        out[(gid, mid.split("-")[0])].append({"market_id": mid, "team": ent,
                                              "line": oline if oline is not None else mline})
    return out


def _q(row):
    return None if row is None else {"ts": row[0], "bid": row[1], "ask": row[2]}


def first_after(con, mid, t, before):
    return _q(con.execute(
        "SELECT ts, best_bid, best_ask FROM quotes WHERE venue = 'kalshi' AND market_id = ? "
        "AND source = 'live' AND ts >= ? AND ts < ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
        "ORDER BY ts LIMIT 1", (mid, t, before)).fetchone())


def last_before(con, mid, k):
    return _q(con.execute(
        "SELECT ts, best_bid, best_ask FROM quotes WHERE venue = 'kalshi' AND market_id = ? "
        "AND source = 'live' AND ts < ? AND ts >= ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1", (mid, k, k - CLOSE_MAX_AGE)).fetchone())


# =============================================================================
# the c-31 total, as of a cut
# =============================================================================

def states_asof(games, pace, cuts, finish=FINISH):
    """{(i, side): state} from team-games with kickoff + finish STRICTLY before cuts[i].
    finish=0 with cut=kickoff is c-31's own rule (strictly earlier kickoffs)."""
    order = sorted((j for j, g in enumerate(games) if g["kickoff_ts"] is not None and g["home_score"] is not None),
                   key=lambda j: (games[j]["kickoff_ts"], games[j]["game_id"]))
    book = GT.FactorBook()
    states = {}
    p = 0
    for i in sorted(cuts, key=lambda i: cuts[i]):
        while p < len(order) and games[order[p]]["kickoff_ts"] + finish < cuts[i]:
            g = games[order[p]]
            h, a = G.franchise(g["home"]), G.franchise(g["away"])
            for t, o, pts in ((h, a, g["home_score"]), (a, h, g["away_score"])):
                pl = pace.get((g["game_id"], t))
                if pl is not None and pl > 0:
                    book.add(GT.TeamGame(g["game_id"], g["season"], g["kickoff_ts"], t, o, pl, float(pts)))
            p += 1
        g = games[i]
        h, a = G.franchise(g["home"]), G.franchise(g["away"])
        for side, t, o in (("home", h, a), ("away", a, h)):
            st = book.raw_state(t, o, g["season"])
            if st is not None:
                states[(i, side)] = st
    return states


def build_model(con, games, by_id, out):
    """c-28 and c-31 as fitted for 2026. -> dict of the pieces the scoring needs."""
    t0 = time.time()
    grid = S.grid()
    walk = GF.Walk(games, S.game_losses(games, grid), mov=True)
    years = list(range(GTR.SCORE_FROM, GTR.SCORE_TO + 1)) + [SEASON]
    sig = GF.margin_sigmas(games, walk, years)
    tot = GF.total_params(games)
    pop1 = [i for i, g in enumerate(games) if GTR.SCORE_FROM <= g["season"] <= GTR.SCORE_TO
            and GF.scored(g) and g["home_score"] != g["away_score"]]
    b = rc.brier(np.array([walk.p(i) for i in pop1]),
                 np.array([1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0 for i in pop1]))
    out(f"c-28 moneyline Part-1 Brier {b!r} (recorded {C28_P1_BRIER!r})  [{time.time() - t0:.0f}s]")
    if b != C28_P1_BRIER:
        raise SystemExit("c-28's moneyline moved - refusing to score")

    pace, _drives, _unm = GTR.load_pace(by_id)
    versions = S.load(con)[2]
    wx_raw, wpath = GTR.load_weather(con, versions["nfl_games"])
    states = GTR.factor_states(games, pace)
    params, _n = GTR.fit_factor_params(games, pace, states, years)
    # c-31's training features, rebuilt by c-31's rule (its main() loop, same functions)
    feat = {}
    for i, g in enumerate(games):
        if (i, "home") not in states or (i, "away") not in states:
            continue
        if not (GTR.FIT_FROM <= g["season"] <= GTR.SCORE_TO or g["season"] == SEASON):
            continue
        if not GF.scored(g) or i not in tot:
            continue
        T_par = g["season"] if g["season"] >= GTR.SCORE_FROM else GTR.SCORE_FROM
        kp, rp, kq, rq = params[T_par]
        th = GT.team_expectation(states[(i, "home")], kp, rp, kq, rq)
        ta = GT.team_expectation(states[(i, "away")], kp, rp, kq, rq)
        Lp, Lq = states[(i, "home")]["L"]
        if g["season"] >= GTR.SCORE_FROM:
            fc = GF.forecast(games, i, walk, sig, tot, "kickoff")
        else:
            mu_t, sd_t = tot[i]
            fc = G.GameForecast(game_id=g["game_id"], home=g["home"], away=g["away"], as_of="kickoff",
                                p_home=walk.pre(walk.params(GTR.SCORE_FROM))[i], sigma_m=sig[GTR.SCORE_FROM],
                                mu_t=mu_t, sigma_t=sd_t)
        feat[i] = {"raw": th[0] * th[1] + ta[0] * ta[1],
                   "raw_plays": th[0] * Lq + ta[0] * Lq, "raw_ppp": Lp * th[1] + Lp * ta[1],
                   "eam": GT.expected_abs_normal(fc.mu_m, fc.sigma_m),
                   "wx": GTR.weather_x(wx_raw.get(g["game_id"]))}
    stacks = GTR.fit_stacks(feat, games, [y for y in years if y > GTR.FIT_FROM])
    out(f"model built [{time.time() - t0:.0f}s]; 2026 factor params {params[SEASON]}; sigma_m {sig[SEASON]:.3f}; "
        f"NO_WIND s_e {stacks['NO_WIND'][SEASON][2]:.3f}; FULL s_e {stacks['FULL'][SEASON][2]:.3f}")
    return {"walk": walk, "sig": sig, "tot": tot, "pace": pace, "wx": wx_raw, "params": params,
            "stacks": stacks, "states_kickoff": states, "feat_kickoff": feat, "weather_file": wpath}


def total_forecast(mdl, games, i, st_h, st_a, arm):
    g = games[i]
    kp, rp, kq, rq = mdl["params"][g["season"]]
    th = GT.team_expectation(st_h, kp, rp, kq, rq)
    ta = GT.team_expectation(st_a, kp, rp, kq, rq)
    fc = GF.forecast(games, i, mdl["walk"], mdl["sig"], mdl["tot"], "open")
    f = {"raw": th[0] * th[1] + ta[0] * ta[1], "raw_plays": None, "raw_ppp": None,
         "eam": GT.expected_abs_normal(fc.mu_m, fc.sigma_m), "wx": GTR.weather_x(mdl["wx"].get(g["game_id"]))}
    beta, gamma, s_e = mdl["stacks"][arm][g["season"]]
    mu = float(np.dot(beta, GTR.design(arm, f["raw"], f["eam"], f["wx"])))
    return GT.GameTotalForecast(base=fc, mu=mu, gamma=gamma, s_e=s_e)


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

    run_ts = time.time()
    result = {"preregistration": "docs/C32-open-line-move-preregistration.md @ c9b15cd", "run_ts": run_ts}
    con = S.market_log_ro()
    games, _grp, versions = S.load(con)
    by_id = {g["game_id"]: g for g in games}
    result["versions"] = versions
    out(f"run {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime(run_ts))}; games {len(games):,}; versions {versions}")

    mdl = build_model(con, games, by_id, out)
    result["weather_file"] = mdl["weather_file"]
    result["fits_2026"] = {"sigma_m": mdl["sig"][SEASON], "factor_params": mdl["params"][SEASON],
                           **{f"stack_{arm}": {"beta": mdl["stacks"][arm][SEASON][0].tolist(),
                                               "gamma": mdl["stacks"][arm][SEASON][1],
                                               "s_e": mdl["stacks"][arm][SEASON][2]}
                              for arm in ("NO_WIND", "FULL")}}

    target = [i for i, g in enumerate(games) if g["season"] == SEASON and g["week"] in WEEKS
              and g["game_type"] == "REG"]
    cuts = {}
    for i in target:
        c = info_cut(games, i)
        g = games[i]
        teams = {G.franchise(g["home"]), G.franchise(g["away"])}
        clash = [h["game_id"] for h in games if h["season"] == SEASON and h["game_id"] != g["game_id"]
                 and h["kickoff_ts"] is not None and c - FINISH < h["kickoff_ts"] < g["kickoff_ts"]
                 and {G.franchise(h["home"]), G.franchise(h["away"])} & teams]
        if clash:
            raise SystemExit(f"{g['game_id']}: a game of one of its teams inside (cut, kickoff): {clash}")
        cuts[i] = c
    out(f"target games {len(target)} (weeks {WEEKS}); every cut asserted clear of both teams' games")

    # the c-31 total as of the cut; identity check: a cut AT kickoff reproduces c-31's own states
    st_open = states_asof(games, mdl["pace"], cuts)
    st_kick = states_asof(games, mdl["pace"], {i: games[i]["kickoff_ts"] for i in target}, finish=0)
    same = all(st_kick.get((i, s)) == mdl["states_kickoff"].get((i, s)) for i in target for s in ("home", "away"))
    out(f"identity: as-of-kickoff states rebuilt here == c-31's factor_states on all {len(target)} games: {same}")
    if not same:
        raise SystemExit("the as-of rebuild does not reproduce c-31's states at kickoff")
    result["identity_states_at_kickoff"] = same

    sig_m = mdl["sig"][SEASON]
    sig_t = mdl["stacks"]["NO_WIND"][SEASON][2]
    mk = mapped_markets(con, by_id)
    fee_cache = {}
    dec = [g for g in games if GTR.FIT_FROM <= g["season"] <= GTR.SCORE_TO and GF.scored(g)
           and g["home_score"] != g["away_score"]]
    home_rate = sum(g["home_score"] > g["away_score"] for g in dec) / len(dec)
    result["placebo_home_rate"] = {"rate": home_rate, "games": len(dec)}
    out(f"placebo home-win rate {home_rate:.4f} over {len(dec):,} decisive games {GTR.FIT_FROM}-{GTR.SCORE_TO}")

    per = {m: [] for m in MARKET.values()}
    census = {m: defaultdict(int) for m in MARKET.values()}
    leads = {m: [] for m in MARKET.values()}
    wind_shift = []
    for i in sorted(target, key=lambda i: games[i]["kickoff_ts"]):
        g = games[i]
        gid, k = g["game_id"], g["kickoff_ts"]
        fc = GF.forecast(games, i, mdl["walk"], mdl["sig"], mdl["tot"], "open")
        tf = total_forecast(mdl, games, i, st_open[(i, "home")], st_open[(i, "away")], "NO_WIND")
        tf_full = total_forecast(mdl, games, i, st_open[(i, "home")], st_open[(i, "away")], "FULL")
        wind_shift.append(tf_full.mean() - tf.mean())
        for series, mname in MARKET.items():
            ms = mk.get((gid, series), [])
            census[mname]["games"] += 1
            if not ms:
                census[mname]["no_markets"] += 1
                continue
            firsts = [first_after(con, m["market_id"], cuts[i], k) for m in ms]
            firsts = [q for q in firsts if q is not None]
            if not firsts:
                census[mname]["no_quote_after_cut"] += 1
                continue
            t0 = min(q["ts"] for q in firsts)
            lead = k - t0
            if lead < MIN_LEAD:
                census[mname]["lead_below_24h"] += 1
                continue
            rungs, snap_b = [], {}
            for m in ms:
                oq = first_after(con, m["market_id"], t0, min(t0 + OPEN_WINDOW, k))
                cq = last_before(con, m["market_id"], k)
                rungs.append((m, oq, cq))
                snap_b[m["market_id"]] = first_after(con, m["market_id"], t0 + SNAP_B[0],
                                                     min(t0 + SNAP_B[1], k))
            if mname == "moneyline":
                so = {m["team"]: (oq["bid"] + oq["ask"]) / 2 for m, oq, _c in rungs if oq}
                sc = {m["team"]: (cq["bid"] + cq["ask"]) / 2 for m, _o, cq in rungs if cq}
                sb = {m["team"]: (snap_b[m["market_id"]]["bid"] + snap_b[m["market_id"]]["ask"]) / 2
                      for m, _o, _c in rungs if snap_b[m["market_id"]]}
                o_n, c_n = ml_home(so, g["home"], g["away"]), ml_home(sc, g["home"], g["away"])
                o_b = ml_home(sb, g["home"], g["away"])
                o_med = c_med = None
                model_n = fc.p_home
                placebo = home_rate
                piv = [(m, oq, cq) for m, oq, cq in rungs if m["team"] == g["home"] and oq]
                piv_p = {id(r[0]): fc.p_home for r in piv}
            else:
                po, pc, pb = [], [], []
                piv_cand = []
                for m, oq, cq in rungs:
                    L = float(m["line"]) if m["line"] is not None else None
                    for q, acc in ((oq, po), (cq, pc), (snap_b[m["market_id"]], pb)):
                        if q is None or not eligible(q, L):
                            continue
                        mid = (q["bid"] + q["ask"]) / 2
                        acc.append(spread_point(m["team"], g["home"], g["away"], L, mid)
                                   if mname == "spread" else (L, mid))
                    if oq is not None and eligible(oq, L):
                        piv_cand.append((abs((oq["bid"] + oq["ask"]) / 2 - 0.5), m["market_id"], m, oq, cq))
                sg = sig_m if mname == "spread" else sig_t
                o_n, c_n = implied_mean(po, sg), implied_mean(pc, sg)
                o_b = implied_mean(pb, sg)
                o_med, c_med = crossing_median(po), crossing_median(pc)
                model_n = fc.margin_mean() if mname == "spread" else tf.mean()
                placebo = sig_m * float(norm.ppf(home_rate)) if mname == "spread" else mdl["tot"][i][0]
                piv = [min(piv_cand)[2:]] if piv_cand else []
                piv_p = {}
                for m, _o, _c in piv:
                    L = float(m["line"])
                    piv_p[id(m)] = (fc.prob_team_by_over(m["team"], L) if mname == "spread"
                                    else tf.prob_total_over(L))
            if o_n is None or c_n is None:
                census[mname]["no_implied_number"] += 1
                continue
            census[mname]["scored"] += 1
            leads[mname].append(lead / 3600)
            row = {"game_id": gid, "week": g["week"], "lead_h": lead / 3600, "open_ts": t0,
                   "model": model_n, "open": o_n, "close": c_n,
                   "model_minus_open": model_n - o_n, "close_minus_open": c_n - o_n,
                   "open_median": o_med, "close_median": c_med,
                   "open_b": o_b, "placebo": placebo}
            if mname == "total":
                row["model_full_wind"] = tf_full.mean()
            if piv and piv[0][2] is not None:
                m, oq, cq = piv[0]
                key = m["market_id"]
                mult = fees.series_multiplier(key)[1]      # (maker, taker): the taker M

                def fee_fn(p, mult=mult):
                    ck = (round(p, 6), mult)
                    if ck not in fee_cache:
                        fee_cache[ck] = fees.fee_per_contract(p, CONTRACTS, "taker", mult)
                    return fee_cache[ck]
                side, mv, cost = pivot_trade(oq, cq, piv_p[id(m)], fee_fn)
                row["pivot"] = {"market_id": key, "side": side, "signed_move": mv, "cost": cost,
                                "beat_cost": mv > cost}
            else:
                census[mname]["pivot_without_close"] += 1
            per[mname].append(row)
    con.close()
    result["census"] = {m: dict(v) for m, v in census.items()}
    out(f"census: {json.dumps(result['census'])}")
    ws = np.array(wind_shift)
    out(f"FULL (recorded wind) minus NO_WIND total mean at the open: mean {ws.mean():+.2f}, sd {ws.std():.2f}")

    # ---------------------------------------------------------------- measures
    res = {}
    for mname, rows in per.items():
        out(f"\n######## {mname.upper()}  (n = {len(rows)} games)")
        r = {"n": len(rows)}
        if len(rows) < 3:
            out("   fewer than 3 games - not scored")
            res[mname] = r
            continue
        L = np.array(leads[mname])
        r["lead_h"] = {"min": float(L.min()), "p10": float(np.percentile(L, 10)), "p50": float(np.median(L)),
                       "p90": float(np.percentile(L, 90)), "max": float(L.max())}
        out("   open lead (h): " + ", ".join(f"{kk} {v:.1f}" for kk, v in r["lead_h"].items()))
        x = np.array([q["model_minus_open"] for q in rows])
        y = np.array([q["close_minus_open"] for q in rows])
        r["sd_model_minus_open"], r["sd_close_minus_open"] = float(x.std(ddof=1)), float(y.std(ddof=1))
        r["mean_abs_close_minus_open"] = float(np.abs(y).mean())
        out(f"   sd model-open {r['sd_model_minus_open']:.4f}; sd close-open {r['sd_close_minus_open']:.4f}; "
            f"mean |close-open| {r['mean_abs_close_minus_open']:.4f}")
        bs = boot(lambda ix: slope_r(x[ix], y[ix])[0], len(x), a.draws)
        br = boot(lambda ix: slope_r(x[ix], y[ix])[1], len(x), a.draws)
        out(f"   MDE (2.8 x bootstrap SE), read before the estimate: slope {bs['mde']:.3f}, r {br['mde']:.3f}")
        r["slope"], r["r"] = bs, br
        out(f"   SLOPE close-open on model-open {rc.fmt(bs, 3)} -> {rc.sign(bs)}")
        out(f"   r                              {rc.fmt(br, 3)} -> {rc.sign(br)}")
        sp = spearmanr(x, y)
        r["spearman"] = {"rho": float(sp.statistic), "p": float(sp.pvalue)}
        out(f"   Spearman rho {sp.statistic:+.3f} (p {sp.pvalue:.3f})")
        r["bands"] = bands(x, y)
        for bd in r["bands"]:
            ratio = "n/a" if bd["ratio"] is None else f"{bd['ratio']:+.2f}"
            out(f"   band {bd['band']} n {bd['n']:>2} |model-open| {bd['abs_mmo_range'][0]:.3f}-"
                f"{bd['abs_mmo_range'][1]:.3f}: mean |move| {bd['mean_abs_move']:.4f}, "
                f"signed toward model {bd['mean_signed_toward_model']:+.4f}, ratio {ratio}")
        if mname != "moneyline":
            med = [(q["model"] - q["open_median"], q["close_median"] - q["open_median"]) for q in rows
                   if q["open_median"] is not None and q["close_median"] is not None]
            if len(med) >= 3:
                mx, my = np.array([m_[0] for m_ in med]), np.array([m_[1] for m_ in med])
                sm = boot(lambda ix: slope_r(mx[ix], my[ix])[0], len(mx), a.draws)
                r["sensitivity_crossing_median_slope"] = sm
                out(f"   sensitivity, crossing-median number: slope {rc.fmt(sm, 3)} (n {len(mx)})")
        if mname == "total":
            fx = np.array([q["model_full_wind"] - q["open"] for q in rows])
            sf = boot(lambda ix: slope_r(fx[ix], y[ix])[0], len(fx), a.draws)
            r["sensitivity_FULL_recorded_wind_slope"] = sf
            out(f"   sensitivity, FULL with RECORDED wind (look-ahead): slope {rc.fmt(sf, 3)}")
        # ---- addendum 1 (post hoc): the shared-open artifact
        ad = {}
        rb = [q for q in rows if q["open_b"] is not None]
        ad["n_with_snapshot_b"] = len(rb)
        if len(rb) >= 3:
            d = np.array([q["open_b"] - q["open"] for q in rb])
            var_e = float(d.var(ddof=1)) / 2.0
            ad["sd_openB_minus_openA"] = float(d.std(ddof=1))
            ad["var_e"] = var_e
            ad["artifact_slope_predicted"] = var_e / float(x.var(ddof=1))
            xa = np.array([q["model_minus_open"] for q in rb])
            yb = np.array([q["close"] - q["open_b"] for q in rb])
            ad["split_slope"] = boot(lambda ix: slope_r(xa[ix], yb[ix])[0], len(xa), a.draws)
            out(f"   [add.1] snapshot B on {len(rb)} games: sd(oB-oA) {ad['sd_openB_minus_openA']:.4f}; "
                f"artifact slope var(e)/var(x) {ad['artifact_slope_predicted']:.3f} vs observed {bs['est']:.3f}")
            out(f"   [add.1] split-snapshot slope (x on A, y from B) {rc.fmt(ad['split_slope'], 3)} "
                f"-> {rc.sign(ad['split_slope'])}")
        px = np.array([q["placebo"] - q["open"] for q in rows])
        ad["placebo_slope"] = boot(lambda ix: slope_r(px[ix], y[ix])[0], len(px), a.draws)
        ad["placebo_r"] = boot(lambda ix: slope_r(px[ix], y[ix])[1], len(px), a.draws)
        out(f"   [add.1] placebo constant: slope {rc.fmt(ad['placebo_slope'], 3)} -> {rc.sign(ad['placebo_slope'])}; "
            f"r {rc.fmt(ad['placebo_r'], 3)}")
        r["addendum1"] = ad
        pv = [q["pivot"] for q in rows if "pivot" in q]
        if pv:
            kb = sum(p_["beat_cost"] for p_ in pv)
            lo, hi = wilson(kb, len(pv))
            net = np.array([p_["signed_move"] - p_["cost"] for p_ in pv])
            mv = np.array([p_["signed_move"] for p_ in pv])
            bn = boot(lambda ix: float(net[ix].mean()), len(net), a.draws)
            bm = boot(lambda ix: float(mv[ix].mean()), len(mv), a.draws)
            r["pivot"] = {"n": len(pv), "beat_cost": kb, "share": kb / len(pv), "wilson": [lo, hi],
                          "mean_signed_move": bm, "mean_net_of_cost": bn,
                          "mean_cost": float(np.mean([p_["cost"] for p_ in pv])),
                          "yes_share": sum(p_["side"] == "yes" for p_ in pv) / len(pv)}
            out(f"   pivot rung: moved toward the model by more than the cost in {kb} of {len(pv)} games "
                f"({kb / len(pv):.2f}, Wilson [{lo:.2f}, {hi:.2f}]); mean cost {r['pivot']['mean_cost']:.4f}")
            out(f"   pivot mean signed move {rc.fmt(bm)}; net of cost {rc.fmt(bn)} -> {rc.sign(bn)}")
        s = rc.sign(bs)
        r["verdict"] = {"above": "the line moved toward the model", "below": "the line moved away from the model",
                        "contains 0": "no detectable relationship",
                        "not read": "not read"}[s]
        out(f"   VERDICT: {r['verdict']} (slope {s})")
        res[mname] = r
    result["markets"] = res
    result["per_game"] = per
    result["log"] = lines
    os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
