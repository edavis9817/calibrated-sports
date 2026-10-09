"""c-47 - c-41's residual-given-the-line frame on receptions and rush attempts.

    set LOGGER_DB=D:/calibrated-sports/data/market_log.db   (opened mode=ro only)
    python -m research.residual_two_markets --dry                       # counts only
    python -m research.residual_two_markets --power --power-out P.json  # simulated outcomes
    python -m research.residual_two_markets --power-in P.json --results R.json   # the run

PRE-REGISTRATION: docs/C47-two-markets-preregistration.md, committed at 5374561
BEFORE this script existed. This file implements it; it does not extend it.

The frame is c-41's (`research/residual_given_line.py`), whose pure pieces are
imported rather than copied: the six candidates, the standardisation, the
walk-forward, Holm. What is new here is the market parameter, one Holm family
over both markets, the outcome-free power simulation, and a verdict with three
states - DETECTED, EXCLUDED at a pre-stated size, UNRESOLVED.

Receiving yards is NOT run here. Everything PRINTED is an aggregate or an
interval; per-rung rows go to --out-dir, which is scratch and never committed.
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import residual_given_line as c41  # noqa: E402

SEASONS = c41.SEASONS
TEST_SEASONS = c41.TEST_SEASONS
CANDIDATES = c41.CANDIDATES
# market -> (nfl_player_week column, positions kept). Order is the reporting order.
MARKETS = {
    "receptions": {"col": "receptions", "positions": ("WR", "TE", "RB")},
    "rush_attempts": {"col": "carries", "positions": ("WR", "TE", "RB", "QB")},
}
BOOT, SEED = 2000, 47
SE_TOL = 1e-12
MIN_GAMES = 5
N_PER_MARKET = 42          # 30 coefficient + 12 Brier
N_REGISTERED = 84          # one Holm family over both markets
N_SPECS = 98               # 84 + 8 descriptive arms + 6 no-QB coefficients
Z_BONF = 3.4337749867680993    # Phi^-1(1 - 0.025 / 84)
Z_80 = 0.8416212335729143
SIZES = (0.020, 0.033, 0.045)  # the pre-stated effect sizes, per sd of a candidate
POWER_FLOOR = 0.80
POWER_REPS = 600


# ------------------------------------------------------------------ loading

def load_history(con, games, col):
    """gsis -> [(kickoff, season, value, team, position)], played REG games."""
    if col not in {m["col"] for m in MARKETS.values()}:
        raise ValueError(f"not a registered stat column: {col}")
    by_week = {}
    for g in games.values():
        if g["type"] == "REG" and g["k"] is not None:
            by_week[(g["season"], g["week"], g["home"])] = g["k"]
            by_week[(g["season"], g["week"], g["away"])] = g["k"]
    rows, miss = {}, 0
    for gsis, season, week, _dv, pos, team, val in con.execute(
            f"SELECT gsis_id, season, week, data_version, position, team, {col} "
            "FROM nfl_player_week WHERE season_type='REG' AND season BETWEEN ? AND ? "
            "ORDER BY data_version", (SEASONS[0] - 1, SEASONS[-1])):
        rows[(gsis, season, week)] = (pos, team, val)
    hist = defaultdict(list)
    for (gsis, season, week), (pos, team, val) in rows.items():
        k = by_week.get((season, week, team))
        if k is None:
            miss += 1
            continue
        hist[gsis].append((k, season, float(val or 0), team, pos))
    for v in hist.values():
        v.sort()
    return hist, miss


def load_rungs(con, stat):
    return con.execute(
        """SELECT o.outcome_id, o.key, o.sport, o.season, o.week, o.entity_type, o.entity_id,
                  o.stat, o.line, o.side, o.push_possible, o.event_id,
                  oc.p_bench, oc.p_all, oc.n_bench, oc.n_all
             FROM outcome_close oc JOIN outcomes o USING (outcome_id)
            WHERE o.stat = ? AND o.side = 'over' AND oc.lead_min <= 15
              AND o.season BETWEEN ? AND ?""", (stat, SEASONS[0], SEASONS[-1])).fetchall()


def build(con, market, shared, dry=False):
    """One market's RB rows as a list of dicts, plus the population counts.

    `shared` carries what both markets read once: games, roster, xwalk positions
    and (when not dry) the settlement snap index."""
    spec = MARKETS[market]
    games, roster, xpos = shared["games"], shared["roster"], shared["xpos"]
    hist, hist_miss = load_history(con, games, spec["col"])
    raw = load_rungs(con, market)
    counts = Counter()
    counts["rungs_all_books"] = len(raw)
    counts["history_rows_without_game"] = hist_miss

    lsum = defaultdict(lambda: [0.0, 0.0])          # (player, game) -> [sum n*line, sum n]
    for r in raw:
        if r[13] is not None and r[15]:
            a = lsum[(r[6], r[11])]
            a[0] += r[15] * r[8]
            a[1] += r[15]

    if not dry:
        from jobs.settle_outcomes import settle_one

    recs = []
    for r in raw:
        (oid, _key, _sport, season, week, _etype, gsis, _stat, line, _side, _push,
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
        prior = c41.prior_games(hist, gsis, season, g["k"])
        pos = (prior[-1][4] if prior else None) or xpos.get(gsis)
        if pos not in spec["positions"]:
            counts["drop_position"] += 1
            continue
        y = None
        if not dry:
            res, actual, _dv, _why = settle_one(con, r[:11], snaps=shared["snaps"],
                                                coverage=shared["coverage"])
            if res not in ("over", "under") or actual is None:
                counts[f"drop_{res}"] += 1
                continue
            if actual == line:                       # c-41 addendum 1: a tie is a push
                counts["drop_push_on_line"] += 1
                continue
            y = 1.0 if res == "over" else 0.0
        ls = lsum.get((gsis, event))
        lbar = ls[0] / ls[1] if ls and ls[1] > 0 else None
        rung = {"line": line, "p_bench": p_bench, "p_all": p_all}
        f = c41.features(rung, g, prior, lbar, roster.get((gsis, season, week)))
        recs.append({"oid": oid, "gsis": gsis, "event": event, "season": season, "pos": pos,
                     "line": line, "p": p_bench, "y": y, "n_bench": n_bench or 0,
                     "n_all": n_all or 0, **f})
    counts["drop_push_on_line"] += 0
    counts["rb_rungs"] = len(recs)
    counts["rb_player_games"] = len({(d["gsis"], d["event"]) for d in recs})
    counts["rb_games"] = len({d["event"] for d in recs})
    for pos in spec["positions"]:
        counts[f"rb_rungs_{pos}"] = sum(d["pos"] == pos for d in recs)
    return recs, counts


def load_shared(con, dry=False):
    shared = {"games": c41.load_games(con), "roster": c41.load_roster(con),
              "xpos": dict(con.execute("SELECT gsis_id, position FROM player_xwalk"))}
    if not dry:
        from jobs.settle_outcomes import load_snap_index
        snaps, players, weeks = load_snap_index(con)
        shared["snaps"], shared["coverage"] = snaps, (players, weeks)
    return shared


# ------------------------------------------------------------------ statistics

def _summ(est, draws, n_games, n):
    draws = draws[np.isfinite(draws)]
    se = float(draws.std()) if len(draws) > 1 else 0.0
    if n_games < MIN_GAMES or se < SE_TOL or not math.isfinite(est):
        p = 1.0                       # too few blocks, or a zero-variance bootstrap
    else:
        p = c41.norm_p(est / se)
    lo, hi = (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))) \
        if len(draws) else (float("nan"), float("nan"))
    return {"est": est, "se": se, "lo": lo, "hi": hi, "p": p, "mde": 2.8 * se,
            "mde_bound_80": (Z_BONF + Z_80) * se,
            "bonf_lo": est - Z_BONF * se, "bonf_hi": est + Z_BONF * se,
            "n": int(n), "games": int(n_games)}


def coef_test(z, r, game_idx, n_games, W):
    s = c41.game_sums(game_idx, n_games, [np.ones_like(z), z, r, z * r, z * z])
    est = float(c41.slope_from_sums(s.sum(axis=0)))
    return _summ(est, c41.slope_from_sums(W @ s), n_games, len(z))


def brier_test(q, p, y, game_idx, n_games, W):
    """Brier(q) - Brier(p) as ONE contrast over shared game blocks."""
    d = (q - y) ** 2 - (p - y) ** 2
    s = c41.game_sums(game_idx, n_games, [np.ones_like(d), d])
    est = float(s[:, 1].sum() / s[:, 0].sum())
    ws = W @ s
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = ws[:, 1] / ws[:, 0]
    return _summ(est, draws, n_games, len(d))


def _cut(rows, src, rng):
    """A subset of a population with its own dense game index and bootstrap weights."""
    out = {k: (v[src] if isinstance(v, np.ndarray) else v) for k, v in rows.items()}
    ids, inv = np.unique(out["game"], return_inverse=True)
    out["game"], out["n_games"], out["src"] = inv, len(ids), src
    out["W"] = c41.block_weights(inv, len(ids), rng)
    out["z"] = {}
    for c in CANDIDATES:
        mu, sd = c41.moments(out[c])
        out["z"][c] = (c41.standardise(out[c], mu, sd), mu, sd)
    return out


def prepare(recs, seed):
    """Everything about a market that does not depend on the outcome.

    Returns the RB and RM populations as arrays, each registered cut with its
    bootstrap weights and standardised candidates, and the player-game index the
    power simulation draws on. Outcomes are attached afterwards by `set_y`."""
    rng = np.random.default_rng(seed)
    for i, d in enumerate(recs):
        d["i"] = i
    rm_recs = c41.modal(recs)
    rb = c41.columns(recs)
    rm = c41.columns(rm_recs)
    rm["rb_index"] = np.array([d["i"] for d in rm_recs])
    pgs = {}
    rb["pg"] = np.array([pgs.setdefault((d["gsis"], d["event"]), len(pgs)) for d in recs])
    rb["n_pg"] = len(pgs)
    rb["is_qb"] = np.array([d["pos"] == "QB" for d in recs])
    all_rb, all_rm = np.arange(len(recs)), np.arange(len(rm_recs))
    prep = {"RB": rb, "RM": rm, "cuts": []}
    prep["cuts"].append(("RB", "pooled", _cut(rb, all_rb, rng)))
    for s in SEASONS:
        prep["cuts"].append(("RB", str(s), _cut(rb, np.flatnonzero(rb["season"] == s), rng)))
    prep["cuts"].append(("RM", "pooled", _cut(rm, all_rm, rng)))
    prep["test"] = {}
    for pop, rows in (("RB", rb), ("RM", rm)):
        prep["test"][pop] = _cut(rows, np.flatnonzero(np.isin(rows["season"], TEST_SEASONS)), rng)
    if rb["is_qb"].any():
        prep["no_qb"] = _cut(rb, np.flatnonzero(~rb["is_qb"]), rng)
    return prep


def set_y(prep, y_rb):
    rb, rm = prep["RB"], prep["RM"]
    rb["y"] = np.asarray(y_rb, dtype=float)
    rb["r"] = rb["y"] - rb["p"]
    rm["y"] = rb["y"][rm["rb_index"]]
    rm["r"] = rm["y"] - rm["p"]


def analyse_market(prep, cands=None, descriptive=True):
    """The registered tests of one market for `cands`, plus the descriptive arms."""
    cands = CANDIDATES if cands is None else cands
    tests = []
    for pop, cut, rows in prep["cuts"]:
        r = prep[pop]["r"][rows["src"]]
        for c in cands:
            z, mu, sd = rows["z"][c]
            t = coef_test(z, r, rows["game"], rows["n_games"], rows["W"])
            t.update(kind="coef", pop=pop, cut=cut, cand=c, registered=True,
                     missing=int(np.isnan(rows[c]).sum()), raw_mean=mu, raw_sd=sd)
            tests.append(t)
    for pop in ("RB", "RM"):
        rows, tr = prep[pop], prep["test"][pop]
        te, y, p = tr["src"], prep[pop]["y"][tr["src"]], tr["p"]
        arms = [(c, [c], False, True, True) for c in cands]
        if descriptive:
            arms += [("close+a", [], True, False, False),
                     ("close+joint", list(CANDIDATES), False, True, False)]
        for name, cols, add_a, slopes, registered in arms:
            q, fits = c41.walk_forward(rows, cols, add_intercept=add_a, slopes=slopes)
            t = brier_test(q[te], p, y, tr["game"], tr["n_games"], tr["W"])
            t.update(kind="brier", pop=pop, cut="2024+2025", cand=name, registered=registered,
                     fits=fits, brier_close=float(np.mean((p - y) ** 2)),
                     brier_arm=float(np.mean((q[te] - y) ** 2)))
            tests.append(t)
    if descriptive and "no_qb" in prep:
        rows = prep["no_qb"]
        r = prep["RB"]["r"][rows["src"]]
        for c in cands:
            z, mu, sd = rows["z"][c]
            t = coef_test(z, r, rows["game"], rows["n_games"], rows["W"])
            t.update(kind="coef", pop="RB", cut="no_qb", cand=c, registered=False,
                     missing=int(np.isnan(rows[c]).sum()), raw_mean=mu, raw_sd=sd)
            tests.append(t)
    return tests


def find(tests, market, kind, pop, cut, cand):
    return next(t for t in tests if t.get("market", market) == market and t["kind"] == kind
                and t["pop"] == pop and t["cut"] == cut and t["cand"] == cand)


def detected_parts(pooled, season_ests, brier, p_adj):
    """c-41's pass rule. `p_adj` is the family-adjusted p of the pooled coefficient."""
    same_sign = all(v > 0 for v in season_ests) or all(v < 0 for v in season_ests)
    cond1 = bool(p_adj < 0.05 and same_sign)
    cond2 = bool(brier["est"] < 0 and brier["hi"] < 0 and -brier["est"] > brier["mde"])
    return same_sign, cond1, cond2


def excluded_at(pooled, power_at):
    """The smallest pre-stated size this pooled coefficient excludes, or None.

    Three conditions, all registered: the Bonferroni interval inside (-d, +d);
    the pre-run power at d at or above 0.80; and the same on the realized SE."""
    out = {}
    for d in SIZES:
        inside = bool(pooled["bonf_lo"] > -d and pooled["bonf_hi"] < d)
        pre = power_at.get(d)
        powered_pre = bool(pre is not None and pre >= POWER_FLOOR)
        powered_real = bool(d >= (Z_BONF + Z_80) * pooled["se"])
        out[d] = {"interval_inside": inside, "power_pre_run": pre, "powered_pre_run": powered_pre,
                  "powered_on_realized_se": powered_real,
                  "excluded": inside and powered_pre and powered_real}
    smallest = next((d for d in SIZES if out[d]["excluded"]), None)
    return smallest, out


def verdict(tests, power):
    """Three states per market and candidate, read on RB only."""
    out = {}
    for m in MARKETS:
        out[m] = {}
        for c in CANDIDATES:
            pooled = find(tests, m, "coef", "RB", "pooled", c)
            seas = [find(tests, m, "coef", "RB", str(s), c)["est"] for s in SEASONS]
            b = find(tests, m, "brier", "RB", "2024+2025", c)
            same_sign, cond1, cond2 = detected_parts(pooled, seas, b, pooled["p_holm"])
            power_at = {d: power[m][c][f"{d:.3f}"]["coef_pooled_bound"] for d in SIZES}
            smallest, per_size = excluded_at(pooled, power_at)
            if cond1 and cond2:
                state = "detected"
            elif smallest is not None:
                state = f"excluded at {smallest:.3f}"
            else:
                state = "unresolved"
            out[m][c] = {
                "state": state, "detected": bool(cond1 and cond2),
                "coef_excludes_zero_after_holm": bool(pooled["p_holm"] < 0.05),
                "same_sign_all_seasons": bool(same_sign), "condition_1": cond1,
                "condition_2_brier_beyond_mde": cond2,
                "smallest_size_excluded": smallest,
                "unresolved_at": [d for d in SIZES if not per_size[d]["excluded"]],
                "may_say_rules_out_0020": bool(per_size[SIZES[0]]["excluded"]),
                "per_size": {f"{d:.3f}": v for d, v in per_size.items()},
                "detect_power_pre_run": {f"{d:.3f}": power[m][c][f"{d:.3f}"]["detected"]
                                         for d in SIZES}}
    return out


# ------------------------------------------------------------------ power

def simulate_y(prep, cand, d, rng):
    """Outcome-free outcomes: one uniform per player-game, a rung clears when
    U < clip(p + d z). Rungs of one player-game share the draw, as they share
    one real result."""
    rb = prep["RB"]
    z = prep["cuts"][0][2]["z"][cand][0] if cand else 0.0
    q = np.clip(rb["p"] + d * z, *c41.CLIP)
    return (rng.random(rb["n_pg"])[rb["pg"]] < q).astype(float)


def power_market(prep, rng, reps, out):
    """For each candidate slot and size: the rate at which each of its registered
    tests fires, and the rate at which the DETECTED rule fires. Size 0 is the
    false-positive rate. The Bonferroni bound stands in for Holm."""
    res = {c: {} for c in CANDIDATES}
    keys = [("coef", pop, cut) for pop, cut, _ in prep["cuts"]] + \
           [("brier", "RB", "2024+2025"), ("brier", "RM", "2024+2025")]
    for d in (0.0,) + SIZES:
        slots = [None] if d == 0.0 else list(CANDIDATES)
        for slot in slots:
            cands = CANDIDATES if slot is None else (slot,)
            acc = {c: defaultdict(list) for c in cands}
            for _ in range(reps):
                set_y(prep, simulate_y(prep, slot, d, rng))
                tests = analyse_market(prep, cands=cands, descriptive=False)
                for c in cands:
                    mine = {(t["kind"], t["pop"], t["cut"]): t for t in tests if t["cand"] == c}
                    for k in keys:
                        t, tag = mine[k], "|".join(k)
                        acc[c][tag + "|se"].append(t["se"])
                        if k[0] == "coef":
                            acc[c][tag + "|unadj"].append(t["p"] < 0.05)
                            acc[c][tag + "|bound"].append(t["p"] * N_REGISTERED < 0.05)
                        else:
                            acc[c][tag + "|unadj"].append(
                                t["est"] < 0 and t["hi"] < 0 and -t["est"] > t["mde"])
                            acc[c][tag + "|bound"].append(
                                t["est"] < 0 and t["p"] * N_REGISTERED < 0.05)
                    pooled = mine[("coef", "RB", "pooled")]
                    seas = [mine[("coef", "RB", str(s))]["est"] for s in SEASONS]
                    _, c1, c2 = detected_parts(pooled, seas, mine[("brier", "RB", "2024+2025")],
                                               min(1.0, pooled["p"] * N_REGISTERED))
                    acc[c]["detected"].append(c1 and c2)
            for c in cands:
                row = {"reps": reps, "detected": float(np.mean(acc[c]["detected"])),
                       "coef_pooled_bound": float(np.mean(acc[c]["coef|RB|pooled|bound"])),
                       "tests": {}}
                for k in keys:
                    tag = "|".join(k)
                    se = float(np.mean(acc[c][tag + "|se"]))
                    row["tests"][tag] = {"se": se, "mde": 2.8 * se,
                                         "mde_bound_80": (Z_BONF + Z_80) * se,
                                         "rate_unadjusted": float(np.mean(acc[c][tag + "|unadj"])),
                                         "rate_bound": float(np.mean(acc[c][tag + "|bound"]))}
                res[c][f"{d:.3f}"] = row
            out(f"    d={d:.3f} slot={slot or 'all (null)'} done")
    return res


def power_report(power, counts, out):
    out("\nPOWER - simulated outcomes on the real rows, NO settlement joined")
    out("  rate at which a test fires when a season-stable linear effect of size d per sd")
    out("  is planted in that candidate. 'bound' = p x 84 < 0.05. Upper bound on real power.")
    for m in MARKETS:
        c = counts[m]
        out(f"\n  {m}: {c['rb_rungs']:,} rungs, {c['rb_player_games']:,} player-games, "
            f"{c['rb_games']:,} games (before settlement)")
        out(f"  {'candidate':11s} {'SE pooled':>9s} {'MDE bound':>9s} | pooled coef at the bound "
            f"{'0.020':>6s} {'0.033':>6s} {'0.045':>6s} | DETECTED rule {'0':>5s} {'0.020':>6s} "
            f"{'0.033':>6s} {'0.045':>6s}")
        for cand in CANDIDATES:
            p = power[m][cand]
            t0 = p["0.000"]["tests"]["coef|RB|pooled"]
            out(f"  {cand:11s} {t0['se']:9.4f} {t0['mde_bound_80']:9.4f} |"
                f"{'':26s}{p['0.020']['coef_pooled_bound']:6.2f} {p['0.033']['coef_pooled_bound']:6.2f} "
                f"{p['0.045']['coef_pooled_bound']:6.2f} |{'':14s}{p['0.000']['detected']:5.2f} "
                f"{p['0.020']['detected']:6.2f} {p['0.033']['detected']:6.2f} "
                f"{p['0.045']['detected']:6.2f}")
    out("\n  EVERY TEST (rate unadjusted / at the bound; Brier 'unadjusted' = the registered "
        "condition 2)")
    out(f"  {'market':13s} {'candidate':11s} {'test':22s} {'SE':>8s} {'MDE':>8s}  "
        f"{'0.020':>11s} {'0.033':>11s} {'0.045':>11s}")
    for m in MARKETS:
        for cand in CANDIDATES:
            for tag in power[m][cand]["0.000"]["tests"]:
                t0 = power[m][cand]["0.000"]["tests"][tag]
                cells = []
                for d in SIZES:
                    t = power[m][cand][f"{d:.3f}"]["tests"][tag]
                    cells.append(f"{t['rate_unadjusted']:.2f} / {t['rate_bound']:.2f}")
                out(f"  {m:13s} {cand:11s} {tag:22s} {t0['se']:8.5f} {t0['mde']:8.5f}  "
                    + " ".join(f"{x:>11s}" for x in cells))


# ------------------------------------------------------------------ reporting

def report(tests, extras, counts, verd, out):
    for m in MARKETS:
        out(f"\n================ {m} ================")
        out("POPULATION")
        for k in sorted(counts[m]):
            out(f"  {k:32s} {counts[m][k]:>8,}")
        d = extras[m]
        out(f"\nRB: n {d['n']:,}  over rate {d['over_rate']:.4f}  mean p {d['mean_p']:.4f} "
            f"(sd {d['sd_p']:.4f}, {d['share_p_in_45_55']:.1%} in [0.45, 0.55])  "
            f"Brier close {d['brier_close']:.4f}  Brier 0.5 {d['brier_half']:.4f}")
        out("\nCOEFFICIENTS (probability per sd of the candidate; game-block bootstrap)")
        out(f"  {'pop':3s} {'cut':7s} {'candidate':11s} {'b':>8s}  {'95% interval':>20s}  "
            f"{'Bonferroni (84)':>20s}  {'SE':>7s} {'MDE':>7s} {'p':>8s} {'Holm p':>8s}  "
            f"{'missing':>7s} {'n':>6s}")
        for t in (t for t in tests if t["market"] == m and t["kind"] == "coef"):
            hp = f"{t['p_holm']:8.4f}" if t["registered"] else "   descr"
            out(f"  {t['pop']:3s} {t['cut']:7s} {t['cand']:11s} {t['est']:+8.4f}  "
                f"[{t['lo']:+.4f}, {t['hi']:+.4f}]  [{t['bonf_lo']:+.4f}, {t['bonf_hi']:+.4f}]  "
                f"{t['se']:7.4f} {t['mde']:7.4f} {t['p']:8.4f} {hp}  {t['missing']:7,} {t['n']:6,}")
        out("\nWALK-FORWARD BRIER, arm minus close, test seasons 2024 + 2025")
        out(f"  {'pop':3s} {'arm':12s} {'diff':>9s}  {'95% interval':>22s}  {'SE':>8s} {'MDE':>8s} "
            f"{'p':>8s} {'Holm p':>8s}  {'n':>6s}")
        for t in (t for t in tests if t["market"] == m and t["kind"] == "brier"):
            hp = f"{t['p_holm']:8.4f}" if t["registered"] else "   descr"
            out(f"  {t['pop']:3s} {t['cand']:12s} {t['est']:+9.5f}  [{t['lo']:+.5f}, {t['hi']:+.5f}]  "
                f"{t['se']:8.5f} {t['mde']:8.5f} {t['p']:8.4f} {hp}  {t['n']:6,}")
        out("\nVERDICT (three states, RB only; sizes are per sd of the candidate)")
        for c in CANDIDATES:
            v = verd[m][c]
            pw = v["per_size"]
            out(f"  {c:11s} coef after Holm: {str(v['coef_excludes_zero_after_holm']):5s} "
                f"same sign: {str(v['same_sign_all_seasons']):5s} Brier beyond MDE: "
                f"{str(v['condition_2_brier_beyond_mde']):5s} | pre-run power "
                + " ".join(f"{d}:{pw[d]['power_pre_run']:.2f}" for d in pw)
                + f" -> {v['state'].upper()}"
                + ("" if v["may_say_rules_out_0020"] else "  (0.020 NOT ruled out)"))
    reg = [t for t in tests if t["registered"]]
    out(f"\nregistered tests {len(reg)}; unadjusted p < 0.05: {sum(t['p'] < 0.05 for t in reg)} "
        f"(expected by chance {0.05 * len(reg):.1f}); Holm p < 0.05: "
        f"{sum(t['p_holm'] < 0.05 for t in reg)}")
    out(f"specifications tried: {len(tests)}")


def dry_report(recs, counts, market, out):
    out(f"\nDRY RUN {market} - counts and missing rates only; no settlement, no residual")
    for k in sorted(counts):
        out(f"  {k:32s} {counts[k]:>8,}")
    rows = c41.columns(recs)
    for c in CANDIDATES:
        x = rows[c]
        out(f"  {c:11s} missing {int(np.isnan(x).sum()):6,} of {len(x):,}   "
            f"mean {np.nanmean(x):+.4f}  sd {np.nanstd(x):.4f}")
    out(f"  RM player-games {len(c41.modal(recs)):,}")


def descriptive(prep):
    rb = prep["RB"]
    return {"n": len(rb["y"]), "over_rate": float(rb["y"].mean()),
            "mean_p": float(rb["p"].mean()), "sd_p": float(rb["p"].std()),
            "share_p_in_45_55": float(np.mean((rb["p"] >= 0.45) & (rb["p"] <= 0.55))),
            "brier_close": float(np.mean(rb["r"] ** 2)),
            "brier_half": float(np.mean((0.5 - rb["y"]) ** 2)),
            "rm_n": len(prep["RM"]["y"]), "rm_over_rate": float(prep["RM"]["y"].mean())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("LOGGER_DB"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--power", action="store_true", help="simulated outcomes; no settlement")
    ap.add_argument("--power-out", default=None)
    ap.add_argument("--power-in", default=None, help="the pre-run power JSON the verdict reads")
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--results", default=None, help="write <path> (aggregates only)")
    a = ap.parse_args()
    if not a.db or not os.path.exists(a.db):
        raise SystemExit("no database: set LOGGER_DB or pass --db (opened mode=ro)")
    outcome_free = a.dry or a.power
    if not outcome_free and not (a.power_in and os.path.exists(a.power_in)):
        raise SystemExit("the registered run needs --power-in: the power written BEFORE the run")

    def out(s=""):
        print(s, flush=True)

    t0 = time.time()
    con = c41.ro(a.db)
    try:
        shared = load_shared(con, dry=outcome_free)
        built = {m: build(con, m, shared, dry=outcome_free) for m in MARKETS}
    finally:
        con.close()                                   # the store is held only while loading
    for m, (recs, _) in built.items():
        if not recs:
            raise SystemExit(f"no rows for {m}: the population query returned nothing")
    counts = {m: dict(built[m][1]) for m in MARKETS}

    if a.dry:
        for m in MARKETS:
            dry_report(built[m][0], built[m][1], m, out)
        return

    if a.power:
        reps = POWER_REPS if a.reps is None else a.reps
        power = {}
        for i, m in enumerate(MARKETS):
            out(f"  power: {m}, {reps} replications per cell")
            prep = prepare(built[m][0], [SEED, 100 + i])
            power[m] = power_market(prep, np.random.default_rng([SEED, 200 + i]), reps, out)
        power_report(power, counts, out)
        out(f"\n  elapsed {time.time() - t0:.0f}s")
        if a.power_out:
            with open(a.power_out, "w") as fh:
                json.dump({"unit": "c-47", "what": "pre-run power, simulated outcomes, no settlement",
                           "reps": reps, "sizes": SIZES, "z_bonf": Z_BONF, "family": N_REGISTERED,
                           "counts_before_settlement": counts, "power": power}, fh, indent=1)
        return

    with open(a.power_in) as fh:
        power = json.load(fh)["power"]
    tests, extras = [], {}
    for i, m in enumerate(MARKETS):
        recs = built[m][0]
        prep = prepare(recs, [SEED, i])
        set_y(prep, [d["y"] for d in recs])
        mt = analyse_market(prep)
        for t in mt:
            t["market"] = m
        if sum(t["registered"] for t in mt) != N_PER_MARKET:
            raise AssertionError(f"{m}: registered tests are not {N_PER_MARKET}")
        tests += mt
        extras[m] = descriptive(prep)
    reg = [t for t in tests if t["registered"]]
    if len(reg) != N_REGISTERED:
        raise AssertionError(f"{len(reg)} registered tests, the registration says {N_REGISTERED}")
    if len(tests) != N_SPECS:
        raise AssertionError(f"{len(tests)} specifications, the registration says {N_SPECS}")
    for t, adj in zip(reg, c41.holm([t["p"] for t in reg])):
        t["p_holm"] = adj
    verd = verdict(tests, power)
    report(tests, extras, counts, verd, out)
    out(f"\n  elapsed {time.time() - t0:.0f}s")
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        for m in MARKETS:
            with open(os.path.join(a.out_dir, f"rows_{m}.json"), "w") as fh:
                json.dump(built[m][0], fh)
    if a.results:
        with open(a.results, "w") as fh:
            json.dump({"unit": "c-47", "preregistration": "docs/C47-two-markets-preregistration.md",
                       "markets": list(MARKETS), "counts": counts, "descriptive": extras,
                       "tests": tests, "verdict": verd, "specifications": len(tests),
                       "registered": len(reg), "boot": BOOT, "seed": SEED, "sizes": SIZES,
                       "z_bonf": Z_BONF}, fh, indent=1)


if __name__ == "__main__":
    main()
