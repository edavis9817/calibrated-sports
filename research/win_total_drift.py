"""c-36 - what a season win total does between now and January.

    python -m research.win_total_drift --db D:/calibrated-sports/data/market_log.db \
        --raw D:/calibrated-sports/data/research_raw/c36 \
        --published D:/calibrated-sports/data/web_export/season/nfl \
        --c25-quotes D:/temp/c36/c25_quotes_at_T.json \
        --json-out research/results/win_total_drift.json

PRE-REGISTRATION: docs/C36-win-total-drift-preregistration.md, pushed at
3dc5735 BEFORE this script existed and before any fetched candle was read.
This file implements it. Anything added afterwards prints as POST HOC.

Scope: Kalshi KXNFLWINS-27{TEAM}-{k}, 32 teams, NFL 2026, the market's open
(2026-04-20) to 2026-10-08; four played weeks. Nothing here has settled and
nothing here scores a forecast against truth - every test is about whether a
PRICE CHANGE is predictable.

Reads: market_log.db (mode=ro; nfl_games, nfl_teams and the live quotes used
to validate the candles), the raw Kalshi candle / order-book archive written
by research/c36_fetch.py, the published projection (constants and the
reproduction check) and c-25's preserved quote rows (validation only).
Writes: the results JSON named on the command line and nothing else.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import glob
import gzip
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fees                                      # noqa: E402
from jobs import season_model as J                          # noqa: E402
from models import season as M                              # noqa: E402

YEAR = 2026
N_SIMS = 20000
N_BOOT = 2000
MAX_SPREAD = 0.15
HOUR = 3600.0
STATES = (0, 1, 2, 3, 4)
CONTRACTS = 100
SIZES = (100, 500, 1000)
CARRY_RATES = (0.04, 0.0)
KALSHI_TO_NFLVERSE = {"JAC": "JAX", "LAR": "LA"}
RE_WINS = re.compile(r"^KXNFLWINS-27([A-Z]+)-(\d+)$")


def utc(y, m, d, h=0):
    return dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp()


S_OF = {0: utc(2026, 9, 8, 16), 1: utc(2026, 9, 15, 16), 2: utc(2026, 9, 22, 16),
        3: utc(2026, 9, 29, 16), 4: utc(2026, 10, 6, 16)}
SEASON_START = utc(2026, 9, 7)
LOG = []


def out(s=""):
    print(s)
    LOG.append(s)


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


# =============================================================================
# pure helpers (tested)
# =============================================================================

def usable(bid, ask, max_spread=None):
    """A candle or book quote that carries a price. An empty book quotes
    0.01/0.99 and its mid is a meaningless 0.5."""
    cap = MAX_SPREAD if max_spread is None else max_spread
    return bid is not None and ask is not None and 0 < bid < ask < 1 and ask - bid <= cap + 1e-9


def pav_decreasing(vals):
    """Pool-adjacent-violators to a NON-INCREASING sequence (equal weights)."""
    blocks = []
    for v in vals:
        blocks.append([float(v), 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            v2, n2 = blocks.pop()
            v1, n1 = blocks.pop()
            blocks.append([(v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2])
    res = []
    for v, n in blocks:
        res.extend([v] * n)
    return res


def crossing(pts):
    """[(k, P(W >= k))] -> the win count at which the PAV-monotone survival
    curve crosses 0.5, linearly interpolated. None when it never does."""
    pts = sorted(pts)
    if len(pts) < 2:
        return None
    ks = [k for k, _ in pts]
    ps = pav_decreasing([p for _, p in pts])
    for i in range(len(ks) - 1):
        p0, p1 = ps[i], ps[i + 1]
        if p0 >= 0.5 > p1:
            return ks[i] + (p0 - 0.5) / (p0 - p1) * (ks[i + 1] - ks[i])
    return None


def decided(cur_whole, remaining, k):
    if cur_whole >= k:
        return 1.0
    if cur_whole + remaining < k:
        return 0.0
    return None


def ols(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def boot_ols(rows, ycol, xcols, fe, block, n_boot=None, seed=36):
    """OLS of ycol on xcols plus one dummy per level of `fe`, with a block
    bootstrap over `block`. -> {x: {est, se, lo, hi, z, mde, n, n_blocks}}.
    A draw in which a regressor is collinear is skipped and counted."""
    nb = N_BOOT if n_boot is None else n_boot
    rows = [r for r in rows if all(r.get(c) is not None for c in [ycol] + list(xcols))]
    if len(rows) < len(xcols) + 3:
        return {x: {"est": None, "n": len(rows)} for x in xcols}
    levels = sorted({r[fe] for r in rows})

    def design(rs):
        X = np.array([[r[c] for c in xcols] + [1.0 if r[fe] == lv else 0.0 for lv in levels]
                      for r in rs], float)
        return np.array([r[ycol] for r in rs], float), X

    y, X = design(rows)
    est = ols(y, X)[:len(xcols)]
    by = defaultdict(list)
    for r in rows:
        by[r[block]].append(r)
    keys = sorted(by)
    rng = np.random.default_rng(seed)
    draws, skipped = [], 0
    for _ in range(nb):
        pick = rng.integers(0, len(keys), len(keys))
        rs = [r for j in pick for r in by[keys[j]]]
        yb, Xb = design(rs)
        if np.linalg.matrix_rank(Xb) < Xb.shape[1]:
            skipped += 1
            continue
        draws.append(ols(yb, Xb)[:len(xcols)])
    draws = np.array(draws)
    res = {}
    for i, x in enumerate(xcols):
        if len(draws) < 100:
            res[x] = {"est": float(est[i]), "n": len(rows), "n_blocks": len(keys), "se": None}
            continue
        se = float(draws[:, i].std(ddof=1))
        res[x] = {"est": float(est[i]), "se": se,
                  "lo": float(np.percentile(draws[:, i], 2.5)),
                  "hi": float(np.percentile(draws[:, i], 97.5)),
                  "z": float(est[i] / se) if se > 0 else None, "mde": 2.8 * se,
                  "n": len(rows), "n_blocks": len(keys), "skipped_draws": skipped}
    return res


def boot_stat(rows, fn, block, n_boot=None, seed=361):
    nb = N_BOOT if n_boot is None else n_boot
    by = defaultdict(list)
    for r in rows:
        by[r[block]].append(r)
    keys = sorted(by)
    est = fn(rows)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(nb):
        pick = rng.integers(0, len(keys), len(keys))
        v = fn([r for j in pick for r in by[keys[j]]])
        if v is not None and np.isfinite(v):
            vals.append(v)
    if est is None or len(vals) < 100:
        return {"est": est, "n": len(rows), "n_blocks": len(keys)}
    return {"est": float(est), "se": float(np.std(vals, ddof=1)),
            "lo": float(np.percentile(vals, 2.5)), "hi": float(np.percentile(vals, 97.5)),
            "n": len(rows), "n_blocks": len(keys)}


def fmt(d, nd=3):
    if not d or d.get("est") is None:
        return "n/a (n %s)" % (d or {}).get("n")
    if d.get("se") is None and d.get("lo") is None:
        return f"{d['est']:+.{nd}f} [no interval] n {d['n']}"
    s = f"{d['est']:+.{nd}f} [{d['lo']:+.{nd}f}, {d['hi']:+.{nd}f}]"
    if d.get("z") is not None:
        s += f" z {d['z']:+.2f} MDE {d['mde']:.{nd}f}"
    return s + f" n {d['n']} blocks {d['n_blocks']}"


def bh(pvals, q=0.10):
    """Benjamini-Hochberg: {name: p} -> set of surviving names."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    cut = 0
    for i, (_n, p) in enumerate(items, 1):
        if p <= q * i / m:
            cut = i
    return {n for n, _p in items[:cut]}


def p_from_z(z):
    from math import erf, sqrt
    if z is None:
        return 1.0
    return float(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))))


def book_vwap(levels_opposite_bids, size):
    """Cost per contract of BUYING `size` by lifting the opposite side's bids.
    `levels_opposite_bids` is [(bid_price, contracts)] of the OTHER side (a YES
    buy lifts NO bids: each NO bid at q is a YES offer at 1 - q). -> (vwap,
    filled). vwap is None when the book cannot fill the size."""
    need = float(size)
    cost = 0.0
    for q, n in sorted(levels_opposite_bids, key=lambda x: -x[0]):
        take = min(need, n)
        cost += take * (1.0 - q)
        need -= take
        if need <= 1e-9:
            return cost / size, float(size)
    return None, float(size) - need


# =============================================================================
# raw archive
# =============================================================================

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_candles(raw):
    """{ticker: {"h": {end_ts: row}, "d": {end_ts: row}}}; row = (bid, ask,
    last, volume, oi)."""
    files = sorted(glob.glob(os.path.join(raw, "candles", "*.json.gz")))
    if len(files) < 500:
        raise SystemExit(f"REFUSED: {len(files)} candle files in {raw} - expected ~544")
    res = {}
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            bodies = json.load(fh)
        t = bodies[0]["request"]["ticker"]
        d = {"h": {}, "d": {}}
        for b in bodies:
            per = b["request"]["period_interval"]
            for c in json.loads(b["body"]).get("candlesticks") or []:
                row = (_f((c.get("yes_bid") or {}).get("close_dollars")),
                       _f((c.get("yes_ask") or {}).get("close_dollars")),
                       _f((c.get("price") or {}).get("close_dollars")),
                       _f(c.get("volume_fp")), _f(c.get("open_interest_fp")))
                d["h" if per == 60 else "d"][float(c["end_period_ts"])] = row
        res[t] = d
    n_h = sum(len(v["h"]) for v in res.values())
    n_d = sum(len(v["d"]) for v in res.values())
    if n_h == 0 or n_d == 0:
        raise SystemExit(f"REFUSED: parsed {n_h} hourly and {n_d} daily candles - key names wrong?")
    return res, n_h, n_d


class Tape:
    """The candle archive as a quote source."""

    def __init__(self, candles):
        self.c = candles
        self.fallback = 0
        self.miss = 0
        self.max_age = 0.0
        self.keys = {kd: {t: sorted(v[kd]) for t, v in candles.items()} for kd in ("h", "d")}
        self.by_team = defaultdict(list)
        for t in candles:
            m = RE_WINS.match(t)
            if m:
                self.by_team[KALSHI_TO_NFLVERSE.get(m.group(1), m.group(1))].append(
                    (int(m.group(2)), t))
        for v in self.by_team.values():
            v.sort()

    def quote(self, ticker, T, kind="h"):
        """(bid, ask) of the LATEST candle ending at or before T (addendum 1:
        Kalshi emits no candle for an hour in which nothing changed, so the
        last one stands until the next). None before the rung's first candle."""
        ks = self.keys[kind][ticker]
        i = bisect.bisect_right(ks, float(T)) - 1
        if i < 0:
            self.miss += 1
            return None
        if ks[i] != float(T):
            self.fallback += 1
            self.max_age = max(self.max_age, float(T) - ks[i])
        r = self.c[ticker][kind][ks[i]]
        return r[0], r[1]

    def ladder(self, team, T, wins_at, kind="h"):
        """[(k, p)] of usable mids plus decided rungs for `team` at T.
        wins_at(team, T) -> (current whole wins, games remaining)."""
        cur, rem = wins_at(team, T)
        pts = []
        for k, t in self.by_team[team]:
            dv = decided(cur, rem, k)
            if dv is not None:
                pts.append((k, dv))
                continue
            q = self.quote(t, T, kind)
            if q and usable(*q):
                pts.append((k, (q[0] + q[1]) / 2))
        return pts

    def number(self, team, T, wins_at, kind="h"):
        return crossing(self.ladder(team, T, wins_at, kind))


# =============================================================================
# main
# =============================================================================

def ro(path):
    return sqlite3.connect("file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro",
                           uri=True, timeout=5)


def git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--published", required=True)
    ap.add_argument("--c25-quotes", required=True)
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args(argv)
    res = {"unit": "c-36", "preregistration": "docs/C36-win-total-drift-preregistration.md",
           "prereg_commit": "3dc5735", "script_commit": git_head()}

    # ------------------------------------------------------------ archive
    candles, n_h, n_d = load_candles(a.raw)
    tape = Tape(candles)
    out(f"== archive: {len(candles)} rung tickers, {n_h:,} hourly and {n_d:,} daily candles; "
        f"{len(tape.by_team)} teams")
    if len(tape.by_team) != 32:
        raise SystemExit(f"REFUSED: {len(tape.by_team)} teams parsed, expected 32")
    hs = [ts for v in candles.values() for ts in v["h"]]
    ds = [ts for v in candles.values() for ts in v["d"]]
    out(f"  hourly span {iso(min(hs))} -> {iso(max(hs))}; daily span {iso(min(ds))} -> {iso(max(ds))}")
    res["archive"] = {"tickers": len(candles), "hourly": n_h, "daily": n_d,
                      "hourly_span": [min(hs), max(hs)], "daily_span": [min(ds), max(ds)]}

    # ------------------------------------------------------------ schedule
    con = ro(a.db)
    games, groupings, versions = J.load(con)
    reg = [g for g in games if g["season"] == YEAR and g["game_type"] == "REG"]
    for k in (1, 2, 3, 4):
        wk = [g for g in reg if g["week"] == k]
        nx = [g for g in reg if g["week"] == k + 1]
        assert len(wk) == 16 and all(g["home_score"] is not None for g in wk), f"week {k}"
        assert max(g["kickoff_ts"] for g in wk) + 5 * HOUR <= S_OF[k], f"S_{k} too early"
        assert min(g["kickoff_ts"] for g in nx) >= S_OF[k] + 24 * HOUR + 8 * HOUR, f"S'_{k} late"
    assert min(g["kickoff_ts"] for g in reg) >= S_OF[0] + 24 * HOUR + 8 * HOUR
    out("  instants verified: every week-k game kicked >= 5h before S_k; no week-(k+1) game "
        "within 8h after S'_k")
    team_games = defaultdict(list)
    for g in reg:
        for t, home in ((g["home"], True), (g["away"], False)):
            r = None
            if g["home_score"] is not None:
                d = g["home_score"] - g["away_score"]
                hr = 1.0 if d > 0 else (0.0 if d < 0 else 0.5)
                r = hr if home else 1.0 - hr
            team_games[t].append({"week": g["week"], "kick": g["kickoff_ts"], "res": r,
                                  "game_id": g["game_id"]})

    def wins_at(team, T):
        """Whole wins and games remaining as known at T (a game is known 4.5h
        after kickoff)."""
        done = [g for g in team_games[team] if g["res"] is not None
                and g["kick"] + 4.5 * HOUR <= T]
        return sum(1 for g in done if g["res"] == 1.0), len(team_games[team]) - len(done)

    # ------------------------------------------------------------ validation
    out("\n== candle validation (the stop rule: >= 90% of rungs agree to 1c at S_3 and S_4)")
    val = {}
    for k in (3, 4):
        T = S_OF[k]
        agree = n = 0
        dm = []
        for t in candles:
            r = con.execute("SELECT best_bid, best_ask FROM quotes WHERE venue='kalshi' AND "
                            "market_id=? AND side='yes' AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
                            (t, T, T - 660)).fetchone()
            q = tape.quote(t, T)
            if not r or q is None or r[0] is None or r[1] is None or q[0] is None or q[1] is None:
                continue
            n += 1
            ok = abs(r[0] - q[0]) <= 0.0101 and abs(r[1] - q[1]) <= 0.0101
            agree += ok
            dm.append(abs((r[0] + r[1]) / 2 - (q[0] + q[1]) / 2))
        val[f"S_{k}"] = {"n": n, "agree_1c": agree / n if n else None,
                         "median_abs_mid_diff": float(np.median(dm)) if dm else None,
                         "p95_abs_mid_diff": float(np.percentile(dm, 95)) if dm else None}
        out(f"  S_{k} {iso(T)} vs live quotes: {n} rungs compared, {agree} agree to 1c on bid and "
            f"ask ({agree / max(n, 1):.1%}); |mid diff| median {np.median(dm):.4f} p95 "
            f"{np.percentile(dm, 95):.4f}")
        if n == 0 or agree / n < 0.90:
            raise SystemExit(f"STOP (pre-registered): candle validation failed at S_{k}")
    c25 = json.load(open(a.c25_quotes))
    for state, lab in ((1, "S'_1"), (2, "S'_2")):
        T = S_OF[state] + 24 * HOUR
        agree = n = 0
        dm = []
        for r in c25["rows"]:
            if r["state"] != state or r["market_id"] not in candles or not r["quote"]:
                continue
            assert abs(r["T"] - T) < 1, "c-25 instant is not this unit's S'"
            q = tape.quote(r["market_id"], T)
            b, k_ = r["quote"].get("bid"), r["quote"].get("ask")
            if q is None or b is None or k_ is None or q[0] is None or q[1] is None:
                continue
            n += 1
            agree += abs(b - q[0]) <= 0.0101 and abs(k_ - q[1]) <= 0.0101
            dm.append(abs((b + k_) / 2 - (q[0] + q[1]) / 2))
        val[lab] = {"n": n, "agree_1c": agree / n if n else None,
                    "median_abs_mid_diff": float(np.median(dm)) if dm else None}
        out(f"  {lab} {iso(T)} vs c-25's preserved rows: {n} compared, {agree} agree to 1c "
            f"({agree / max(n, 1):.1%}); |mid diff| median {np.median(dm) if dm else float('nan'):.4f}")
    res["validation"] = val

    # ------------------------------------------------------------ model
    out("\n== model: a-41/a-55 season model at states 0..4")
    published = json.load(open(os.path.join(a.published, "projection.json")))
    losses = J.game_losses(games, J.grid())
    params, _, _ = J.best_params(losses, YEAR)
    pc = published["method"]["constants"]
    assert (params.k, params.hfa, params.regress) == (pc["k"], pc["hfa"], pc["regress"]), \
        f"refit params {params} differ from published {pc}"
    sigma = published["method"]["sigma"]["value"]
    _, snaps = M.run_elo(games, params, snapshot_at=[(YEAR, k) for k in STATES])
    model_ladder, model_num, model_mean = {}, {}, {}
    for k in STATES:
        s = J.season_at(games, groupings, YEAR, k)
        tag = J.rng_for("proj-current", YEAR) if k == 4 else J.rng_for("proj-current", YEAR, k)
        draws = M.rating_draws(s, snaps[(YEAR, k)], sigma, N_SIMS, tag)
        R = M.simulate(s, M.p_home_draws(s, draws, params.hfa), N_SIMS, tag)
        W = (R == 1.0).astype(float) @ s.H + (R == 0.0).astype(float) @ s.A   # WHOLE wins
        Wh = M.final_wins(s, R)
        for t in s.teams:
            j = s.idx[t]
            model_ladder[(t, k)] = [(r, float(np.mean(W[:, j] >= r))) for r in range(1, 18)]
            model_num[(t, k)] = crossing(model_ladder[(t, k)])
            model_mean[(t, k)] = float(Wh[:, j].mean())
        if k == 4:
            pub = {x["team"]: x["projection"]["mean"] for x in published["teams"]}
            assert all(x["games_played"] == 4 for x in published["teams"]), \
                "published projection is not the after-week-4 state"
            worst = max(abs(model_mean[(t, 4)] - pub[t]) for t in s.teams)
            out(f"  state 4 against the published projection ({published['generated_at']}): "
                f"max |mean wins - published| {worst:.4f}")
            if worst > 0.051:
                raise SystemExit("STOP (pre-registered): state 4 does not reproduce the published projection")
            res["reproduction_state4_max_abs"] = worst
    teams = sorted(tape.by_team)
    out(f"  params {params.as_dict()}, sigma {sigma}, {N_SIMS} sims per state")

    # ------------------------------------------------------------ market numbers
    m_S = {(t, k): tape.number(t, S_OF[k], wins_at) for t in teams for k in STATES}
    m_Sp = {(t, k): tape.number(t, S_OF[k] + 24 * HOUR, wins_at) for t in teams for k in STATES}
    cover = {k: sum(m_S[(t, k)] is not None for t in teams) for k in STATES}
    out(f"\n== market number m (0.5 crossing of the usable ladder): teams with a number at "
        f"S_0..S_4 {cover}; at S'_0..S'_4 "
        f"{ {k: sum(m_Sp[(t, k)] is not None for t in teams) for k in STATES} }")
    if min(cover.values()) < 24:
        raise SystemExit("STOP (pre-registered): fewer than 24 teams with a market number at some S_k")
    res["coverage_S"] = cover
    rungs_used = [len([1 for _k, p in tape.ladder(t, S_OF[k], wins_at) if 0 < p < 1])
                  for t in teams for k in STATES]
    out(f"  usable undecided rungs per team-instant: median {np.median(rungs_used):.0f}, "
        f"min {min(rungs_used)}, max {max(rungs_used)}")

    # team-week rows
    rows = []
    for t in teams:
        for w in (1, 2, 3, 4):
            g = next(x for x in team_games[t] if x["week"] == w)
            k = w - 1
            K = g["kick"]
            r = {"team": t, "week": w, "game": g["game_id"], "win": g["res"],
                 "m0": m_S[(t, k)], "m0p": m_Sp[(t, k)], "m1": m_S[(t, w)],
                 "f0": model_num[(t, k)], "f1": model_num[(t, w)],
                 "pre": tape.number(t, K - HOUR, wins_at),
                 "posta": tape.number(t, K + 5 * HOUR, wins_at),
                 "postb": tape.number(t, K + 17 * HOUR, wins_at)}

            def d(x, y):
                return None if r[x] is None or r[y] is None else r[x] - r[y]
            r["move_w"] = d("m1", "m0")              # S_k -> S_{k+1}
            r["move_p"] = d("m1", "m0p")             # S'_k -> S_{k+1}   (T3-P)
            r["drift_pre"] = d("pre", "m0p")         # S'_k -> PRE       (T3-D)
            r["jump"] = d("posta", "pre")
            r["drift_b"] = d("m1", "postb")          # POSTb -> S_w      (T2a)
            r["drift_a"] = d("m1", "posta")          # artifact-prone
            r["gap"] = d("f0", "m0")
            r["dmodel"] = d("f1", "f0")
            r["lvl"] = None if r["m0"] is None else r["m0"] - 8.5
            r["win_lvl"] = None if r["lvl"] is None else r["win"] * r["lvl"]
            rows.append(r)
    out(f"  team-weeks {len(rows)}; with weekly move {sum(r['move_w'] is not None for r in rows)}, "
        f"with jump {sum(r['jump'] is not None for r in rows)}, with gap "
        f"{sum(r['gap'] is not None for r in rows)}; candle reads carried forward {tape.fallback} (oldest "
        f"{tape.max_age / HOUR:.0f}h), reads before a rung's first candle {tape.miss}")
    res["team_weeks"] = rows

    # ------------------------------------------------------------ Q1
    out("\n== Q1 how much a win total moves, and when (descriptive)")
    q1 = {}
    for w in (1, 2, 3, 4):
        v = np.array([r["move_w"] for r in rows if r["week"] == w and r["move_w"] is not None])
        q1[f"week{w}"] = {"n": len(v), "mean_abs": float(np.mean(np.abs(v))),
                          "median_abs": float(np.median(np.abs(v))),
                          "p90_abs": float(np.percentile(np.abs(v), 90)),
                          "max_abs": float(np.max(np.abs(v)))}
        out(f"  week {w}: number moved |d| mean {np.mean(np.abs(v)):.2f} median "
            f"{np.median(np.abs(v)):.2f} p90 {np.percentile(np.abs(v), 90):.2f} max "
            f"{np.max(np.abs(v)):.2f} wins (n {len(v)})")
    for lab, sel in (("after a win", 1.0), ("after a loss", 0.0)):
        v = [r for r in rows if r["win"] == sel and r["move_w"] is not None]
        b = boot_stat(v, lambda rs: float(np.mean([x["move_w"] for x in rs])) if rs else None, "team")
        q1[lab] = b
        out(f"  {lab}: mean weekly change of the number {fmt(b, 2)} wins")
    by_team = {t: [r["move_w"] for r in rows if r["team"] == t and r["move_w"] is not None]
               for t in teams}
    tot = sorted(((float(np.sum(np.abs(v))), t) for t, v in by_team.items() if v), reverse=True)
    out("  most-moved teams (sum |weekly d| over 4 weeks): "
        + ", ".join(f"{t} {x:.2f}" for x, t in tot[:6]) + "; least: "
        + ", ".join(f"{t} {x:.2f}" for x, t in tot[-6:]))
    q1["by_team_sum_abs"] = {t: x for x, t in tot}
    # rung level
    rung = []
    for t in teams:
        for k in (0, 1, 2, 3):
            if m_S[(t, k)] is None:
                continue
            for r_k, tk in tape.by_team[t]:
                c0, rem0 = wins_at(t, S_OF[k])
                c1, rem1 = wins_at(t, S_OF[k + 1])
                if decided(c0, rem0, r_k) is not None or decided(c1, rem1, r_k) is not None:
                    continue
                q0, q1_ = tape.quote(tk, S_OF[k]), tape.quote(tk, S_OF[k + 1])
                if not (q0 and q1_ and usable(*q0) and usable(*q1_)):
                    continue
                rung.append({"team": t, "week": k + 1, "dist": abs(r_k - m_S[(t, k)]),
                             "d": (q1_[0] + q1_[1]) / 2 - (q0[0] + q0[1]) / 2,
                             "spread": q0[1] - q0[0]})
    q1["rung"] = {}
    for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 99)):
        v = [x for x in rung if lo <= x["dist"] < hi]
        if not v:
            continue
        ad = np.abs([x["d"] for x in v])
        q1["rung"][f"{lo}-{hi}"] = {"n": len(v), "mean_abs_pp": float(ad.mean() * 100),
                                    "median_abs_pp": float(np.median(ad) * 100),
                                    "p90_abs_pp": float(np.percentile(ad, 90) * 100),
                                    "median_spread": float(np.median([x["spread"] for x in v]))}
        out(f"  rungs {lo}-{hi if hi < 99 else '+'} wins from the number: weekly |d mid| mean "
            f"{ad.mean() * 100:.1f}pp median {np.median(ad) * 100:.1f}pp p90 "
            f"{np.percentile(ad, 90) * 100:.1f}pp; median spread "
            f"{np.median([x['spread'] for x in v]) * 100:.0f}c (n {len(v)})")
    # when
    ww = [r for r in rows if r["jump"] is not None and r["move_w"] is not None
          and r["pre"] is not None and r["posta"] is not None]

    def share(rs):
        j = sum(x["jump"] ** 2 for x in rs)
        o = sum((x["pre"] - x["m0"]) ** 2 + (x["m1"] - x["posta"]) ** 2 for x in rs)
        return j / (j + o) if j + o > 0 else None
    sh = boot_stat(ww, share, "team")
    q1["game_window_share"] = sh
    out(f"  WHEN: share of the week's squared movement inside the team's own 6h game window "
        f"(of 168h): {fmt(sh)}; mean |jump| {np.mean([abs(x['jump']) for x in ww]):.2f} wins, "
        f"mean |rest of week| "
        f"{np.mean([abs(x['pre'] - x['m0']) + abs(x['m1'] - x['posta']) for x in ww]):.2f}")
    # preseason against in season, daily, market number from daily closes
    day_ends = sorted({ts for v in candles.values() for ts in v["d"]})
    pre_d, in_d = [], []
    for t in teams:
        prev = None
        for ts in day_ends:
            x = tape.number(t, ts, wins_at, kind="d")
            if x is not None and prev is not None:
                pre_d.append(abs(x - prev))
            prev = x
        prev = None
        ts = SEASON_START + 28 * HOUR          # 04:00 UTC ends, the daily candle's boundary
        while ts <= max(hs):
            x = tape.number(t, ts, wins_at)
            if x is not None and prev is not None:
                in_d.append(abs(x - prev))
            prev = x
            ts += 24 * HOUR
    q1["daily_abs_change"] = {"preseason_mean": float(np.mean(pre_d)), "preseason_n": len(pre_d),
                              "in_season_mean": float(np.mean(in_d)), "in_season_n": len(in_d),
                              "preseason_median": float(np.median(pre_d)),
                              "in_season_median": float(np.median(in_d))}
    out(f"  daily |change| of the number: preseason (04-21..09-07) mean {np.mean(pre_d):.3f} "
        f"median {np.median(pre_d):.3f} wins (n {len(pre_d)}); in season mean {np.mean(in_d):.3f} "
        f"median {np.median(in_d):.3f} (n {len(in_d)})")
    res["Q1"] = q1

    # ------------------------------------------------------------ Q2
    out("\n== Q2 does the market move more than it should")
    q2 = {}
    t2a = boot_ols(rows, "drift_b", ["jump"], "week", "team", seed=362)["jump"]
    t2a_art = boot_ols(rows, "drift_a", ["jump"], "week", "team", seed=362)["jump"]
    t2a_game = boot_ols(rows, "drift_b", ["jump"], "week", "game", seed=362)["jump"]
    q2["T2a"] = t2a
    q2["T2a_same_instant_artifact_prone"] = t2a_art
    q2["T2a_game_blocks"] = t2a_game
    out(f"  T2a (registered) drift(POSTb->S_w) on jump(PRE->POSTa): {fmt(t2a)}")
    out(f"      game blocks: {fmt(t2a_game)}")
    out(f"      same-instant (drift from POSTa; artifact-prone): {fmt(t2a_art)}")
    jr = [r for r in rows if r["jump"] is not None]
    for lab, sel in (("win", 1.0), ("loss", 0.0)):
        v = [r["jump"] for r in jr if r["win"] == sel]
        dr = [r["drift_b"] for r in jr if r["win"] == sel and r["drift_b"] is not None]
        out(f"      jump after a {lab}: mean {np.mean(v):+.2f} wins (n {len(v)}); drift after "
            f"mean {np.mean(dr):+.3f}")
        q2[f"jump_{lab}_mean"] = float(np.mean(v))
        q2[f"drift_{lab}_mean"] = float(np.mean(dr))
    t2b = boot_ols(rows, "move_w", ["dmodel"], "week", "team", seed=363)["dmodel"]
    both = [r for r in rows if r["move_w"] is not None and r["dmodel"] is not None]
    ratio = boot_stat(both, lambda rs: float(np.sqrt(np.mean([x["move_w"] ** 2 for x in rs])
                                                     / np.mean([x["dmodel"] ** 2 for x in rs])))
                      if rs else None, "team")
    q2["T2b_slope_dmarket_on_dmodel"] = t2b
    q2["T2b_rms_ratio"] = ratio
    out(f"  T2b (descriptive) d-market on d-model, weekly: slope {fmt(t2b)}; RMS ratio "
        f"market/model {fmt(ratio)}")
    for lab, sel in (("win", 1.0), ("loss", 0.0)):
        v = [r for r in both if r["win"] == sel]
        out(f"      after a {lab}: market {np.mean([x['move_w'] for x in v]):+.2f}, model "
            f"{np.mean([x['dmodel'] for x in v]):+.2f} wins (n {len(v)})")
        q2[f"T2b_{lab}"] = {"market": float(np.mean([x["move_w"] for x in v])),
                            "model": float(np.mean([x["dmodel"] for x in v])), "n": len(v)}
    q2["dispersion"] = {}
    for k in STATES:
        pr = [{"team": t, "x": m_S[(t, k)], "y": model_num[(t, k)]} for t in teams
              if m_S[(t, k)] is not None and model_num[(t, k)] is not None]
        sl = boot_stat(pr, lambda rs: float(np.polyfit([z["x"] for z in rs], [z["y"] for z in rs], 1)[0])
                       if len({z["x"] for z in rs}) > 2 else None, "team")
        sdm, sdf = float(np.std([z["x"] for z in pr])), float(np.std([z["y"] for z in pr]))
        q2["dispersion"][k] = {"teams": len(pr), "sd_market": sdm, "sd_model": sdf,
                               "slope_model_on_market": sl}
        out(f"  dispersion at S_{k}: SD across teams market {sdm:.2f} / model {sdf:.2f} wins; "
            f"slope model-on-market {fmt(sl)}")
    res["Q2"] = q2

    # ------------------------------------------------------------ Q3
    out("\n== Q3 does the model's gap at week k predict the move from k to k+1")
    q3 = {}
    X = ["gap", "win", "lvl", "win_lvl"]
    t3p = boot_ols(rows, "move_p", X, "week", "team", seed=364)
    t3p_game = boot_ols(rows, "move_p", X, "week", "game", seed=364)
    t3d = boot_ols(rows, "drift_pre", ["gap", "lvl"], "week", "team", seed=365)
    q3["T3P"] = t3p
    q3["T3P_game_blocks"] = t3p_game
    q3["T3D"] = t3d
    out(f"  T3-P (registered, PRIMARY) beta on gap: {fmt(t3p['gap'])}")
    out(f"       game blocks: {fmt(t3p_game['gap'])}")
    out(f"       controls: win {fmt(t3p['win'])}; level {fmt(t3p['lvl'])}; win x level "
        f"{fmt(t3p['win_lvl'])}")
    out(f"  T3-D (registered) pre-game drift, beta_D on gap: {fmt(t3d['gap'])}; level "
        f"{fmt(t3d['lvl'])}")
    nolvl = boot_ols(rows, "move_p", ["gap", "win"], "week", "team", seed=366)
    same = boot_ols(rows, "move_w", X, "week", "team", seed=367)
    q3["no_level_control"] = nolvl
    q3["move_from_S_same_instant"] = same
    out(f"  beside them: without the level control {fmt(nolvl['gap'])}; move started at S_k "
        f"(shares the quote with the gap) {fmt(same['gap'])}")
    lowo = {}
    for w in (1, 2, 3, 4):
        b = boot_ols([r for r in rows if r["week"] != w], "move_p", X, "week", "team",
                     n_boot=200, seed=368)["gap"]
        lowo[w] = b.get("est")
    q3["leave_one_week_out"] = lowo
    out("  leave-one-week-out beta: " + ", ".join(f"without wk{w} {v:+.3f}" for w, v in lowo.items()))
    for w in (1, 2, 3, 4):
        b = boot_ols([r for r in rows if r["week"] == w], "move_p", X, "week", "team",
                     n_boot=200, seed=369)["gap"]
        q3[f"week{w}_only"] = b
        out(f"      week {w} alone: {fmt(b)}")
    gp = [(r["gap"], next((x["gap"] for x in rows if x["team"] == r["team"]
                           and x["week"] == r["week"] + 1), None)) for r in rows]
    gp = [(a_, b_) for a_, b_ in gp if a_ is not None and b_ is not None]
    q3["gap_persistence_r"] = float(np.corrcoef([x for x, _ in gp], [y for _, y in gp])[0, 1])
    gaps = np.array([r["gap"] for r in rows if r["gap"] is not None])
    out(f"  the gap: SD {gaps.std():.2f} wins, mean {gaps.mean():+.2f}; week-to-week persistence "
        f"r {q3['gap_persistence_r']:.2f} (n {len(gp)})")
    pv = {"T2a": p_from_z(t2a.get("z")), "T3-P": p_from_z(t3p["gap"].get("z")),
          "T3-D": p_from_z(t3d["gap"].get("z"))}
    surv = bh(pv)
    q3["bh"] = {"p": pv, "survivors_q10": sorted(surv)}
    out(f"  BH q=0.10 over the 3 registered tests: p {{{', '.join(f'{k}: {v:.3f}' for k, v in pv.items())}}} "
        f"-> survivors {sorted(surv) or 'none'}")

    # tradeable table (registered as a table, printed whatever the sign so a
    # reader sees the cost of acting on the gap)
    trades = []
    for r in rows:
        if r["gap"] is None or r["gap"] == 0:
            continue
        k = r["week"] - 1
        T0, T1 = S_OF[k] + 24 * HOUR, S_OF[r["week"]]
        c0, rem0 = wins_at(r["team"], T0)
        c1, rem1 = wins_at(r["team"], T1)
        best = None
        for r_k, tk in tape.by_team[r["team"]]:
            if decided(c0, rem0, r_k) is not None or decided(c1, rem1, r_k) is not None:
                continue
            q0, q1_ = tape.quote(tk, T0), tape.quote(tk, T1)
            if not (q0 and q1_ and usable(*q0) and usable(*q1_)):
                continue
            c = abs((q0[0] + q0[1]) / 2 - 0.5)
            if best is None or c < best[0]:
                best = (c, q0, q1_, tk)
        if best is None:
            continue
        _c, q0, q1_, tk = best
        if r["gap"] > 0:      # the model has the team higher: buy YES at the ask, sell at the bid
            buy, sell = q0[1], q1_[0]
        else:                 # buy NO at 1 - bid, sell NO at 1 - ask
            buy, sell = 1 - q0[0], 1 - q1_[1]
        fee = fees.fee_per_contract(round(buy, 4), CONTRACTS, "taker", 1) + \
            fees.fee_per_contract(round(sell, 4), CONTRACTS, "taker", 1)
        mid_move = ((q1_[0] + q1_[1]) / 2 - (q0[0] + q0[1]) / 2) * (1 if r["gap"] > 0 else -1)
        trades.append({"team": r["team"], "week": r["week"], "ticker": tk, "pnl": sell - buy - fee,
                       "gross_mid": mid_move, "fee": fee, "absgap": abs(r["gap"])})
    if trades:
        net = boot_stat(trades, lambda rs: float(np.mean([x["pnl"] for x in rs])) if rs else None, "team")
        gross = boot_stat(trades, lambda rs: float(np.mean([x["gross_mid"] for x in rs])) if rs else None, "team")
        big = [x for x in trades if x["absgap"] >= np.median([y["absgap"] for y in trades])]
        netb = boot_stat(big, lambda rs: float(np.mean([x["pnl"] for x in rs])) if rs else None, "team")
        q3["round_trip"] = {"net_per_contract": net, "gross_mid_to_mid": gross,
                            "net_top_half_by_gap": netb,
                            "mean_fee_two_legs": float(np.mean([x["fee"] for x in trades]))}
        out(f"  one-week round trip on the model's side of the central rung (candle touch, taker "
            f"both legs, {CONTRACTS} contracts): mid-to-mid {fmt(gross, 4)}; net {fmt(net, 4)}; "
            f"larger-gap half net {fmt(netb, 4)}; mean fee both legs "
            f"{np.mean([x['fee'] for x in trades]) * 100:.2f}c")
    res["Q3"] = q3

    # ------------------------------------------------------------ Q4
    out("\n== Q4 what it costs to hold one")
    q4 = {}
    sraw = json.load(open(os.path.join(a.raw, "series_KXNFLWINS.json")))
    sb = json.loads(sraw["body"]).get("series", {})
    mk_m, tk_m = fees.series_multiplier("KXNFLWINS")
    out(f"  /series/KXNFLWINS ({iso(sraw['fetched_ts'])}): fee_type {sb.get('fee_type')} "
        f"multiplier {sb.get('fee_multiplier')}; core.fees maker M {mk_m} taker M {tk_m}")
    if sb.get("fee_type") != "quadratic" or float(sb.get("fee_multiplier") or 0) != 1.0 \
            or float(mk_m) != 0 or float(tk_m) != 1:
        raise SystemExit("STOP (pre-registered): /series fee does not match core.fees")
    q4["fee"] = {"fee_type": sb.get("fee_type"), "fee_multiplier": sb.get("fee_multiplier"),
                 "fetched_ts": sraw["fetched_ts"]}
    books = []
    bfiles = sorted(glob.glob(os.path.join(a.raw, "books", "*.json")))
    if len(bfiles) < 500:
        raise SystemExit(f"REFUSED: {len(bfiles)} book files")
    bts = []
    close_ts = None
    for p in bfiles:
        b = json.load(open(p))
        ob = json.loads(b["orderbook"]).get("orderbook_fp") or {}
        mkt = json.loads(b["market"])["market"]
        bts.append(b["fetched_ts"])
        close_ts = dt.datetime.fromisoformat(mkt["close_time"].replace("Z", "+00:00")).timestamp()
        yes = [(float(x), float(n)) for x, n in (ob.get("yes_dollars") or [])]
        no = [(float(x), float(n)) for x, n in (ob.get("no_dollars") or [])]
        m_ = RE_WINS.match(b["ticker"])
        t = KALSHI_TO_NFLVERSE.get(m_.group(1), m_.group(1))
        cur, rem = wins_at(t, b["fetched_ts"])
        row = {"ticker": b["ticker"], "team": t, "k": int(m_.group(2)),
               "decided": decided(cur, rem, int(m_.group(2))) is not None,
               "oi": _f(mkt.get("open_interest_fp")), "volume": _f(mkt.get("volume_fp")),
               "two_sided": bool(yes and no)}
        if yes and no:
            bid = max(x for x, _ in yes)
            ask = 1 - max(x for x, _ in no)
            row.update(bid=bid, ask=ask, mid=(bid + ask) / 2, spread=ask - bid,
                       touch_yes=sum(n for x, n in no if abs(x - max(z for z, _ in no)) < 1e-9),
                       touch_no=sum(n for x, n in yes if abs(x - bid) < 1e-9))
            for sz in SIZES:
                vy, _ = book_vwap(no, sz)
                vn, _ = book_vwap(yes, sz)
                for side, v, ref in (("yes", vy, row["mid"]), ("no", vn, 1 - row["mid"])):
                    if v is None or not (0 < v < 1):
                        row[f"cost_{side}_{sz}"] = None
                        continue
                    fee = fees.fee_per_contract(round(v, 4), sz, "taker", 1)
                    row[f"cost_{side}_{sz}"] = v - ref + fee
                    row[f"fee_{side}_{sz}"] = fee
        books.append(row)
    out(f"  books: {len(books)} rungs, fetched {iso(min(bts))} -> {iso(max(bts))} UTC (one "
        f"overnight snapshot); two-sided {sum(b['two_sided'] for b in books)}, decided "
        f"{sum(b['decided'] for b in books)}")
    live = [b for b in books if b["two_sided"] and not b["decided"] and usable(b["bid"], b["ask"])]
    wide = [b for b in books if b["two_sided"] and not b["decided"] and not usable(b["bid"], b["ask"])]
    out(f"  undecided two-sided with spread <= 15c: {len(live)}; wider or degenerate: {len(wide)}; "
        f"undecided with a one-sided or empty book: "
        f"{sum(1 for b in books if not b['decided'] and not b['two_sided'])}")
    q4["bands"] = {}
    for lo, hi in ((0, .10), (.10, .25), (.25, .75), (.75, .90), (.90, 1.0)):
        v = [b for b in live if lo <= b["mid"] < hi]
        if not v:
            continue
        d = {"n": len(v), "spread_median": float(np.median([b["spread"] for b in v])),
             "touch_yes_median": float(np.median([b["touch_yes"] for b in v])),
             "touch_no_median": float(np.median([b["touch_no"] for b in v])),
             "oi_median": float(np.median([b["oi"] or 0 for b in v]))}
        line = (f"  mid {lo:.2f}-{hi:.2f} (n {len(v)}): spread median {d['spread_median'] * 100:.0f}c, "
                f"touch YES/NO {d['touch_yes_median']:.0f}/{d['touch_no_median']:.0f}, OI median "
                f"{d['oi_median']:,.0f};  all-in cost over mid (c)")
        for sz in SIZES:
            for side in ("yes", "no"):
                c = [b[f"cost_{side}_{sz}"] for b in v if b.get(f"cost_{side}_{sz}") is not None]
                d[f"cost_{side}_{sz}"] = float(np.median(c)) if c else None
                d[f"fillable_{side}_{sz}"] = len(c) / len(v)
            line += (f" | {sz}: YES {(d[f'cost_yes_{sz}'] or float('nan')) * 100:.1f} "
                     f"({d[f'fillable_yes_{sz}']:.0%}) NO {(d[f'cost_no_{sz}'] or float('nan')) * 100:.1f} "
                     f"({d[f'fillable_no_{sz}']:.0%})")
        q4["bands"][f"{lo}-{hi}"] = d
        out(line)
    # candle spread by week and distance; volume
    q4["candle_spread"] = {}
    for k in STATES:
        sp_near, sp_far = [], []
        for t in teams:
            if m_S[(t, k)] is None:
                continue
            c0, rem0 = wins_at(t, S_OF[k])
            for r_k, tk in tape.by_team[t]:
                if decided(c0, rem0, r_k) is not None:
                    continue
                q = tape.quote(tk, S_OF[k])
                if q and q[0] and q[1] and 0 < q[0] < q[1] < 1:
                    (sp_near if abs(r_k - m_S[(t, k)]) < 2 else sp_far).append(q[1] - q[0])
        q4["candle_spread"][k] = {"near_median": float(np.median(sp_near)), "near_n": len(sp_near),
                                  "far_median": float(np.median(sp_far)), "far_n": len(sp_far)}
        out(f"  candle spread at S_{k}: within 2 wins of the number median "
            f"{np.median(sp_near) * 100:.0f}c (n {len(sp_near)}); further out "
            f"{np.median(sp_far) * 100:.0f}c (n {len(sp_far)}) - two-sided rungs, no spread cap")
    last_h = max(hs)
    vol7 = []
    for t, d in candles.items():
        v = sum((r[3] or 0) for ts, r in d["h"].items() if ts > last_h - 7 * 24 * HOUR)
        vol7.append(v)
    q4["volume_7d_per_rung"] = {"median": float(np.median(vol7)), "p90": float(np.percentile(vol7, 90)),
                                "max": float(np.max(vol7)), "zero_share": float(np.mean(np.array(vol7) == 0))}
    out(f"  contracts traded per rung over the last 7 days: median {np.median(vol7):,.0f}, p90 "
        f"{np.percentile(vol7, 90):,.0f}, max {np.max(vol7):,.0f}; rungs with none "
        f"{np.mean(np.array(vol7) == 0):.0%}")
    days = (close_ts - max(bts)) / 86400.0
    q4["carry"] = {"days_to_close": days, "close": iso(close_ts), "rows": []}
    out(f"  CARRY: {days:.0f} days from the snapshot to the series close ({iso(close_ts)} UTC)")
    for p in (0.2, 0.5, 0.8):
        fee1 = fees.fee_per_contract(p, CONTRACTS, "taker", 1)
        for r in CARRY_RATES:
            carry = p * r * days / 365.0
            q4["carry"]["rows"].append({"price": p, "rate": r, "carry": carry, "fee_one_leg": fee1})
            out(f"    price {p:.2f}, alternative {r:.0%}/yr (ASSUMED, not measured): carry "
                f"{carry * 100:.2f}c per contract; taker fee one leg {fee1 * 100:.2f}c, two legs "
                f"{2 * fee1 * 100:.2f}c")
    res["Q4"] = q4
    res["books"] = books
    res["model_numbers"] = {f"{t}_{k}": v for (t, k), v in model_num.items()}
    res["market_numbers_S"] = {f"{t}_{k}": v for (t, k), v in m_S.items()}
    res["log"] = LOG
    os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
    json.dump(res, open(a.json_out, "w"), indent=1, default=str)
    out(f"\nwrote {a.json_out}")


if __name__ == "__main__":
    main()
