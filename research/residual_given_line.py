"""c-41 - what predicts the residual given the line.

    set LOGGER_DB=D:/calibrated-sports/data/market_log.db   (opened mode=ro only)
    python -m research.residual_given_line --dry                      # counts only
    python -m research.residual_given_line --out-dir D:/temp/c41      # the registered run

PRE-REGISTRATION: docs/C41-residual-preregistration.md, committed at de5a5a7
BEFORE this script existed. This file implements it and its addendum; it does
not extend them.

The forecast is the book's own de-vigged probability at its own line
(`outcome_close.p_bench`). The residual is `y - p`. Six candidates, closed in
the registration, are tested as linear predictors of it. NO line-blind model is
built here: nothing in this file turns a player's history into a probability.

Everything PRINTED is an aggregate or an interval. Per-rung rows go to
--out-dir, which is scratch and never committed.
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SEASONS = (2023, 2024, 2025)
TEST_SEASONS = (2024, 2025)
STAT = "receiving_yards"
POSITIONS = ("WR", "TE", "RB")
CANDIDATES = ("form_gap", "last_game", "book_gap", "line_pos", "team_total", "log_line")
PREV_W = 0.5              # weight on season S-1 in the player's prior-game mean
MIN_PRIOR = 2             # prior games C1 needs
FLOOR = 10.0              # the "+10" in C1, C2, C4
WINSOR = 3.0
CLIP = (0.01, 0.99)
BOOT, SEED = 2000, 41
SE_TOL = 1e-12
MIN_GAMES = 5
N_REGISTERED = 42         # 30 coefficient + 12 Brier, one Holm family
Z_BONF = 3.24             # 0.05 / 42, two-sided
C37 = {"rungs": 18666, "player_games": 10016, "games": 814}   # c-37's RB population
SE_EXPECTED = 0.0050      # registered pooled coefficient SE; > 2x is under-powered


class LeakError(AssertionError):
    """A feature or a fit reached at or past the instant it is used to predict."""


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=120)


# ------------------------------------------------------------------ loading

def load_games(con):
    """game_id -> dict, at the newest data_version. REG only is decided by the caller."""
    games = {}
    for gid, dv, season, week, gt, k, home, away, spread, total in con.execute(
            "SELECT game_id, data_version, season, week, game_type, kickoff_ts, home_team, "
            "away_team, spread_line, total_line FROM nfl_games WHERE season BETWEEN ? AND ? "
            "ORDER BY data_version", (SEASONS[0] - 1, SEASONS[-1])):
        games[gid] = {"season": season, "week": week, "type": gt, "k": k, "home": home,
                      "away": away, "spread": spread, "total": total}
    return games


def load_history(con, games):
    """gsis -> list of (kickoff, season, yards, team, position), played REG games,
    newest data_version per player-week, sorted by kickoff."""
    by_week = {}
    for g in games.values():
        if g["type"] == "REG" and g["k"] is not None:
            by_week[(g["season"], g["week"], g["home"])] = g["k"]
            by_week[(g["season"], g["week"], g["away"])] = g["k"]
    rows, miss = {}, 0
    for gsis, season, week, dv, pos, team, yds in con.execute(
            "SELECT gsis_id, season, week, data_version, position, team, receiving_yards "
            "FROM nfl_player_week WHERE season_type='REG' AND season BETWEEN ? AND ? "
            "ORDER BY data_version", (SEASONS[0] - 1, SEASONS[-1])):
        rows[(gsis, season, week)] = (pos, team, yds)
    hist = defaultdict(list)
    for (gsis, season, week), (pos, team, yds) in rows.items():
        k = by_week.get((season, week, team))
        if k is None:
            miss += 1
            continue
        hist[gsis].append((k, season, float(yds or 0), team, pos))
    for v in hist.values():
        v.sort()
    return hist, miss


def load_rungs(con):
    """Every over rung of the stat at the close, all books. The bench subset is RB."""
    return con.execute(
        """SELECT o.outcome_id, o.key, o.sport, o.season, o.week, o.entity_type, o.entity_id,
                  o.stat, o.line, o.side, o.push_possible, o.event_id,
                  oc.p_bench, oc.p_all, oc.n_bench, oc.n_all
             FROM outcome_close oc JOIN outcomes o USING (outcome_id)
            WHERE o.stat = ? AND o.side = 'over' AND oc.lead_min <= 15
              AND o.season BETWEEN ? AND ?""", (STAT, SEASONS[0], SEASONS[-1])).fetchall()


def load_roster(con):
    out = {}
    for gsis, season, week, team in con.execute(
            "SELECT gsis_id, season, week, team FROM nfl_roster_week WHERE season BETWEEN ? AND ? "
            "ORDER BY data_version", (SEASONS[0], SEASONS[-1])):
        out[(gsis, season, week)] = team
    return out


# ------------------------------------------------------------------ features

def prior_games(hist, gsis, season, kickoff):
    """The player's played games strictly before `kickoff`, seasons S-1 and S."""
    return [h for h in hist.get(gsis, ()) if h[0] < kickoff and h[1] in (season - 1, season)]


def features(rung, game, prior, lbar, roster_team):
    """The six candidates for one rung. None = missing. Pure; no I/O."""
    line, p_bench, p_all = rung["line"], rung["p_bench"], rung["p_all"]
    for h in prior:                                   # every feature row is as-of
        if game["k"] is None or h[0] >= game["k"]:
            raise LeakError(f"prior game at {h[0]} is not before kickoff {game['k']}")
    f = dict.fromkeys(CANDIDATES)
    if len(prior) >= MIN_PRIOR:
        w = np.array([PREV_W if h[1] < game["season"] else 1.0 for h in prior])
        y = np.array([h[2] for h in prior])
        f["form_gap"] = float((np.sum(w * y) / np.sum(w) - line) / (line + FLOOR))
    this = [h for h in prior if h[1] == game["season"]]
    if this:
        f["last_game"] = (this[-1][2] - line) / (line + FLOOR)
    if p_all is not None and p_bench is not None:
        f["book_gap"] = p_all - p_bench
    if lbar is not None:
        f["line_pos"] = (line - lbar) / (lbar + FLOOR)
    team = prior[-1][3] if prior and prior[-1][3] in (game["home"], game["away"]) else None
    if team is None and roster_team in (game["home"], game["away"]):
        team = roster_team
    if team is not None and game["total"] is not None and game["spread"] is not None:
        sign = 1.0 if team == game["home"] else -1.0   # spread_line > 0: home favoured
        f["team_total"] = game["total"] / 2.0 + sign * game["spread"] / 2.0
    if line > 0:
        f["log_line"] = math.log(line)
    return f


def standardise(x, mu, sd):
    """Standardise on (mu, sd), winsorise at +/-3, missing (nan) -> 0."""
    z = (x - mu) / sd if sd > SE_TOL else np.zeros_like(x)
    z = np.clip(z, -WINSOR, WINSOR)
    return np.where(np.isnan(x), 0.0, z)


def moments(x):
    ok = ~np.isnan(x)
    if ok.sum() < 2:
        return 0.0, 0.0
    return float(x[ok].mean()), float(x[ok].std())


# ------------------------------------------------------------------ statistics

def norm_p(z):
    return math.erfc(abs(z) / math.sqrt(2.0))


def block_weights(game_idx, n_games, rng):
    """BOOT x n_games multinomial counts: a game-block bootstrap."""
    return rng.multinomial(n_games, np.full(n_games, 1.0 / n_games), size=BOOT).astype(float)


def game_sums(game_idx, n_games, cols):
    out = np.zeros((n_games, len(cols)))
    for j, c in enumerate(cols):
        out[:, j] = np.bincount(game_idx, weights=c, minlength=n_games)
    return out


def slope_from_sums(s):
    """OLS slope of r on z with an intercept, from (n, Sz, Sr, Szr, Szz) sums."""
    n, sz, sr, szr, szz = (s[..., i] for i in range(5))
    var = szz - sz * sz / n
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.abs(var) > SE_TOL, (szr - sz * sr / n) / var, np.nan)


def coef_test(z, r, game_idx, n_games, W):
    """Slope, bootstrap SE, unadjusted interval, p. z is already standardised."""
    s = game_sums(game_idx, n_games, [np.ones_like(z), z, r, z * r, z * z])
    est = float(slope_from_sums(s.sum(axis=0)))
    draws = slope_from_sums(W @ s)
    return _summ(est, draws, n_games, len(z))


def brier_test(q, p, y, game_idx, n_games, W):
    """Brier(q) - Brier(p) as ONE contrast over shared game blocks."""
    d = (q - y) ** 2 - (p - y) ** 2
    s = game_sums(game_idx, n_games, [np.ones_like(d), d])
    est = float(s[:, 1].sum() / s[:, 0].sum())
    ws = W @ s
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = ws[:, 1] / ws[:, 0]
    return _summ(est, draws, n_games, len(d))


def _summ(est, draws, n_games, n):
    draws = draws[np.isfinite(draws)]
    se = float(draws.std()) if len(draws) > 1 else 0.0
    if n_games < MIN_GAMES or se < SE_TOL or not math.isfinite(est):
        p = 1.0                       # too few blocks, or a zero-variance bootstrap
    else:
        p = norm_p(est / se)
    lo, hi = (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))) \
        if len(draws) else (float("nan"), float("nan"))
    return {"est": est, "se": se, "lo": lo, "hi": hi, "p": p, "mde": 2.8 * se,
            "bonf_lo": est - Z_BONF * se, "bonf_hi": est + Z_BONF * se,
            "n": int(n), "games": int(n_games)}


def holm(pvals):
    """Holm step-down adjusted p-values, in the input order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


def ols(Z, r):
    """Coefficients of r on [1, Z]. Returns (intercept, betas)."""
    X = np.column_stack([np.ones(len(r)), Z])
    beta, *_ = np.linalg.lstsq(X, r, rcond=None)
    return float(beta[0]), beta[1:]


def walk_forward(rows, cols, add_intercept=False, slopes=True):
    """Out-of-sample forecast q for the TEST_SEASONS rows. `cols` = candidate names.

    Fit r = a + b.z on seasons < T, z standardised on those training rows. The
    intercept is fitted and, unless `add_intercept`, NOT added: a candidate takes
    no credit for the known over bias."""
    season = rows["season"]
    q = np.full(len(season), np.nan)
    fits = {}
    for T in TEST_SEASONS:
        tr, te = season < T, season == T
        if not tr.any() or not te.any():
            continue
        if (season[tr] >= T).any():
            raise LeakError(f"training rows reach season {T}")
        Ztr = np.zeros((int(tr.sum()), len(cols)))
        Zte = np.zeros((int(te.sum()), len(cols)))
        for j, c in enumerate(cols):
            mu, sd = moments(rows[c][tr])
            Ztr[:, j] = standardise(rows[c][tr], mu, sd)
            Zte[:, j] = standardise(rows[c][te], mu, sd)
        a, b = ols(Ztr, rows["r"][tr]) if cols else (float(rows["r"][tr].mean()), np.zeros(0))
        adj = (Zte @ b if (slopes and cols) else 0.0) + (a if add_intercept else 0.0)
        q[te] = np.clip(rows["p"][te] + adj, *CLIP)
        fits[T] = {"a": a, "b": [float(v) for v in b], "n_train": int(tr.sum())}
    return q, fits


# ------------------------------------------------------------------ assembly

def build(con, out, dry=False):
    """The RB rows as column arrays, plus the population counts."""
    games = load_games(con)
    hist, hist_miss = load_history(con, games)
    roster = load_roster(con)
    raw = load_rungs(con)
    counts = Counter()
    counts["rungs_all_books"] = len(raw)
    counts["history_rows_without_game"] = hist_miss
    xpos = dict(con.execute("SELECT gsis_id, position FROM player_xwalk"))

    lsum = defaultdict(lambda: [0.0, 0.0])          # (player, game) -> [sum n*line, sum n]
    for r in raw:
        if r[13] is not None and r[15]:
            a = lsum[(r[6], r[11])]
            a[0] += r[15] * r[8]
            a[1] += r[15]

    if not dry:
        from jobs.settle_outcomes import load_snap_index, settle_one
        snaps, players, weeks = load_snap_index(con)

    recs = []
    for r in raw:
        (oid, key, sport, season, week, etype, gsis, stat, line, side, push,
         event, p_bench, p_all, n_bench, n_all) = r
        if p_bench is None:
            continue
        counts["bench_rungs"] += 1
        g = games.get(event)
        if g is None or g["k"] is None:
            counts["drop_no_game"] += 1
            continue
        if g["type"] != "REG":
            counts["drop_not_regular_season"] += 1
            continue
        prior = prior_games(hist, gsis, season, g["k"])
        pos = (prior[-1][4] if prior else None) or xpos.get(gsis)
        if pos not in POSITIONS:
            counts["drop_position"] += 1
            continue
        y = actual = None
        if not dry:
            res, actual, _dv, _why = settle_one(con, r[:11], snaps=snaps,
                                                coverage=(players, weeks))
            if res not in ("over", "under") or actual is None:
                counts[f"drop_{res}"] += 1
                continue
            if actual == line:                       # addendum 1: a tie is a push
                counts["drop_push_on_line"] += 1
                continue
            y = 1.0 if res == "over" else 0.0
        ls = lsum.get((gsis, event))
        lbar = ls[0] / ls[1] if ls and ls[1] > 0 else None
        rung = {"line": line, "p_bench": p_bench, "p_all": p_all}
        f = features(rung, g, prior, lbar, roster.get((gsis, season, week)))
        recs.append({"oid": oid, "gsis": gsis, "event": event, "season": season,
                     "line": line, "p": p_bench, "y": y, "n_bench": n_bench or 0,
                     "n_all": n_all or 0, **f})
    counts["rb_rungs"] = len(recs)
    counts["rb_player_games"] = len({(d["gsis"], d["event"]) for d in recs})
    counts["rb_games"] = len({d["event"] for d in recs})
    return recs, counts


def columns(recs):
    """List of dicts -> dict of arrays, with a dense game index."""
    ev = sorted({d["event"] for d in recs})
    eidx = {e: i for i, e in enumerate(ev)}
    rows = {"season": np.array([d["season"] for d in recs]),
            "p": np.array([d["p"] for d in recs], dtype=float),
            "game": np.array([eidx[d["event"]] for d in recs]),
            "n_games": len(ev)}
    if recs and recs[0]["y"] is not None:
        rows["y"] = np.array([d["y"] for d in recs], dtype=float)
        rows["r"] = rows["y"] - rows["p"]
    for c in CANDIDATES:
        rows[c] = np.array([np.nan if d[c] is None else d[c] for d in recs], dtype=float)
    return rows


def modal(recs):
    """RM: one rung per player-game - largest n_bench, then n_all, then lowest line."""
    best = {}
    for d in recs:
        k = (d["gsis"], d["event"])
        rank = (-d["n_bench"], -d["n_all"], d["line"])
        if k not in best or rank < best[k][0]:
            best[k] = (rank, d)
    return [v[1] for v in best.values()]


def subset(rows, mask):
    out = {k: (v[mask] if isinstance(v, np.ndarray) else v) for k, v in rows.items()}
    ids, inv = np.unique(out["game"], return_inverse=True)
    out["game"], out["n_games"] = inv, len(ids)
    return out


def analyse(recs, out):
    """Every registered and descriptive test. Returns (tests, extras)."""
    rng = np.random.default_rng(SEED)
    tests, extras = [], {}
    pops = {"RB": columns(recs), "RM": columns(modal(recs))}

    # --- coefficients: RB pooled, RB per season, RM pooled
    cuts = [("RB", "pooled", pops["RB"]), ("RM", "pooled", pops["RM"])]
    cuts[1:1] = [("RB", str(s), subset(pops["RB"], pops["RB"]["season"] == s)) for s in SEASONS]
    for pop, cut, rows in cuts:
        W = block_weights(rows["game"], rows["n_games"], rng)
        for c in CANDIDATES:
            mu, sd = moments(rows[c])
            z = standardise(rows[c], mu, sd)
            t = coef_test(z, rows["r"], rows["game"], rows["n_games"], W)
            t.update(kind="coef", pop=pop, cut=cut, cand=c, registered=True,
                     missing=int(np.isnan(rows[c]).sum()), raw_mean=mu, raw_sd=sd)
            tests.append(t)

    # --- walk-forward Brier, test seasons pooled
    for pop in ("RB", "RM"):
        rows = pops[pop]
        te = np.isin(rows["season"], TEST_SEASONS)
        trows = subset(rows, te)
        W = block_weights(trows["game"], trows["n_games"], rng)
        arms = [(c, [c], False, True, True) for c in CANDIDATES]
        arms += [("close+a", [], True, False, False), ("close+joint", list(CANDIDATES),
                                                       False, True, False)]
        for name, cols, add_a, slopes, registered in arms:
            q, fits = walk_forward(rows, cols, add_intercept=add_a, slopes=slopes)
            t = brier_test(q[te], trows["p"], trows["y"], trows["game"], trows["n_games"], W)
            t.update(kind="brier", pop=pop, cut="2024+2025", cand=name, registered=registered,
                     fits=fits, brier_close=float(np.mean((trows["p"] - trows["y"]) ** 2)),
                     brier_arm=float(np.mean((q[te] - trows["y"]) ** 2)))
            tests.append(t)

    reg = [t for t in tests if t["registered"]]
    if len(reg) != N_REGISTERED:
        raise AssertionError(f"{len(reg)} registered tests, the registration says {N_REGISTERED}")
    for t, a in zip(reg, holm([t["p"] for t in reg])):
        t["p_holm"] = a

    rb = pops["RB"]
    extras["descriptive"] = {
        "RB": {"n": len(rb["y"]), "over_rate": float(rb["y"].mean()),
               "mean_p": float(rb["p"].mean()), "sd_p": float(rb["p"].std()),
               "share_p_in_45_55": float(np.mean((rb["p"] >= 0.45) & (rb["p"] <= 0.55))),
               "brier_close": float(np.mean(rb["r"] ** 2)),
               "brier_half": float(np.mean((0.5 - rb["y"]) ** 2))},
        "RM": {"n": len(pops["RM"]["y"]), "over_rate": float(pops["RM"]["y"].mean()),
               "mean_p": float(pops["RM"]["p"].mean())}}
    return tests, extras


def verdict(tests):
    """The registered rule, per candidate. Both conditions on RB, or no."""
    out = {}
    for c in CANDIDATES:
        def get(kind, pop, cut):
            return next(t for t in tests if t["kind"] == kind and t["pop"] == pop
                        and t["cut"] == cut and t["cand"] == c)
        pooled = get("coef", "RB", "pooled")
        seas = [get("coef", "RB", str(s))["est"] for s in SEASONS]
        same_sign = all(v > 0 for v in seas) or all(v < 0 for v in seas)
        cond1 = bool(pooled["p_holm"] < 0.05 and same_sign)
        b = get("brier", "RB", "2024+2025")
        cond2 = bool(b["est"] < 0 and b["hi"] < 0 and -b["est"] > b["mde"])
        out[c] = {"coef_excludes_zero_after_holm": bool(pooled["p_holm"] < 0.05),
                  "same_sign_all_seasons": bool(same_sign), "condition_1": cond1,
                  "condition_2_brier_beyond_mde": cond2, "passes": cond1 and cond2,
                  "under_powered": bool(pooled["se"] > 2 * SE_EXPECTED)}
    return out


def report(tests, extras, counts, verd, out):
    out("\nPOPULATION")
    for k in sorted(counts):
        out(f"  {k:32s} {counts[k]:>8,}")
    out(f"  c-37 reported: rungs {C37['rungs']:,}, player-games {C37['player_games']:,}, "
        f"games {C37['games']:,}")
    gap = abs(counts["rb_rungs"] - C37["rungs"]) / C37["rungs"]
    out(f"  rung count differs from c-37 by {gap:.2%} -> "
        f"{'SAME population (within 2%)' if gap <= 0.02 else 'NOT the same population'}")
    d = extras["descriptive"]["RB"]
    out(f"\nRB: n {d['n']:,}  over rate {d['over_rate']:.4f}  mean p {d['mean_p']:.4f} "
        f"(sd {d['sd_p']:.4f}, {d['share_p_in_45_55']:.1%} in [0.45, 0.55])  "
        f"Brier close {d['brier_close']:.4f}  Brier 0.5 {d['brier_half']:.4f}")
    out("\nCOEFFICIENTS (probability per sd of the candidate; game-block bootstrap)")
    out(f"  {'pop':3s} {'cut':7s} {'candidate':11s} {'b':>8s}  {'95% interval':>20s}  "
        f"{'SE':>7s} {'MDE':>7s} {'p':>8s} {'Holm p':>8s}  {'missing':>7s} {'n':>6s}")
    for t in (t for t in tests if t["kind"] == "coef"):
        out(f"  {t['pop']:3s} {t['cut']:7s} {t['cand']:11s} {t['est']:+8.4f}  "
            f"[{t['lo']:+.4f}, {t['hi']:+.4f}]  {t['se']:7.4f} {t['mde']:7.4f} "
            f"{t['p']:8.4f} {t['p_holm']:8.4f}  {t['missing']:7,} {t['n']:6,}")
    out("\nWALK-FORWARD BRIER, arm minus close, test seasons 2024 + 2025")
    out(f"  {'pop':3s} {'arm':12s} {'diff':>9s}  {'95% interval':>22s}  {'SE':>8s} {'MDE':>8s} "
        f"{'p':>8s} {'Holm p':>8s}  {'n':>6s}")
    for t in (t for t in tests if t["kind"] == "brier"):
        hp = f"{t['p_holm']:8.4f}" if t["registered"] else "   descr"
        out(f"  {t['pop']:3s} {t['cand']:12s} {t['est']:+9.5f}  [{t['lo']:+.5f}, {t['hi']:+.5f}]  "
            f"{t['se']:8.5f} {t['mde']:8.5f} {t['p']:8.4f} {hp}  {t['n']:6,}")
    out("\nVERDICT (registered rule: both conditions on RB, or no)")
    for c, v in verd.items():
        out(f"  {c:11s} coef after Holm: {str(v['coef_excludes_zero_after_holm']):5s}  "
            f"same sign: {str(v['same_sign_all_seasons']):5s}  Brier beyond MDE: "
            f"{str(v['condition_2_brier_beyond_mde']):5s}  under-powered: "
            f"{str(v['under_powered']):5s} -> {'PASSES' if v['passes'] else 'no'}")
    n_pass = sum(v["passes"] for v in verd.values())
    reg = [t for t in tests if t["registered"]]
    out(f"\n  candidates passing: {n_pass} of {len(verd)}")
    out(f"  registered tests {len(reg)}; unadjusted p < 0.05: "
        f"{sum(t['p'] < 0.05 for t in reg)} (expected by chance {0.05 * len(reg):.1f}); "
        f"Holm p < 0.05: {sum(t['p_holm'] < 0.05 for t in reg)}")
    out(f"  specifications tried: {len(tests)}")


def dry_report(recs, counts, out):
    out("DRY RUN - counts and missing rates only; no settlement, no residual")
    for k in sorted(counts):
        out(f"  {k:32s} {counts[k]:>8,}")
    rows = columns(recs)
    for c in CANDIDATES:
        x = rows[c]
        out(f"  {c:11s} missing {int(np.isnan(x).sum()):6,} of {len(x):,}   "
            f"mean {np.nanmean(x):+.4f}  sd {np.nanstd(x):.4f}")
    out(f"  RM player-games {len(modal(recs)):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("LOGGER_DB"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--results", default=None, help="write <path>.json (aggregates only)")
    a = ap.parse_args()
    if not a.db or not os.path.exists(a.db):
        raise SystemExit("no database: set LOGGER_DB or pass --db (opened mode=ro)")

    def out(s=""):
        print(s, flush=True)

    t0 = time.time()
    con = ro(a.db)
    try:
        recs, counts = build(con, out, dry=a.dry)
    finally:
        con.close()
    if not recs:
        raise SystemExit("no rows: the population query returned nothing")
    if a.dry:
        dry_report(recs, counts, out)
        return
    tests, extras = analyse(recs, out)
    verd = verdict(tests)
    report(tests, extras, counts, verd, out)
    out(f"\n  elapsed {time.time() - t0:.0f}s")
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        with open(os.path.join(a.out_dir, "rows.json"), "w") as fh:
            json.dump(recs, fh)
    if a.results:
        with open(a.results, "w") as fh:
            json.dump({"unit": "c-41", "preregistration": "docs/C41-residual-preregistration.md",
                       "counts": dict(counts), "c37": C37, "descriptive": extras["descriptive"],
                       "tests": tests, "verdict": verd, "specifications": len(tests),
                       "boot": BOOT, "seed": SEED}, fh, indent=1)


if __name__ == "__main__":
    main()
