"""Brief 023 Part 1 - the model against book prop CLOSES, walk-forward by season.

    python -m research.walkforward                 # all seasons, all variants
    python -m research.walkforward --workers 20

Follows docs/briefs/023-preregistration.md (committed fd0a57b), Part 1.

DECLARED HERE BEFORE ANY PREDICTION WAS COMPUTED
================================================

Step 0 - constant provenance (evidence: git show b08440e / 27b9419, the
comments in models/baseline.py, DECISIONS.md 2026-09-09/10, research/*.py):

  constant                value   class     evidence
  SHRINK_GAMES_DEFAULT    6       KEPT*     b08440e: "a full season is 17 games, so
                                            k=6 puts n=17 at 0.74" - arithmetic. *Later
                                            (27b9419) a k-grid was scored on 2023->24 and
                                            2024->25 and k was RETAINED on bias, so its
                                            survival is 2023-25-informed -> BRACKETED.
  SHRINK_GAMES_BY_STAT    {}      KEPT      empty; the same decision as above.
  MEAN_DRIFT_VAR          8.56/   REFIT     "measured, players with >=6 games in both
                          1.12              seasons" (27b9419) - but NO measurement code
                                            exists in the repo, and a grid of 2 season
                                            pairs x 2 opportunity rules x 2 min-games x
                                            4 screens x 2-3 position sets x 5 estimators
                                            reproduced neither n=101/8.56 nor n=215/1.12.
                                            The ORIGINAL procedure is unrecoverable, so
                                            it is refit with the DECLARED procedure below
                                            and BRACKETED at x0.5 / x2.
  DEFAULT_DRIFT_VAR       1.12    unused    receptions/rush_attempts have explicit keys.
  SHRINK_GAMES_VMR        12      UNKNOWN   b08440e judgment ("a 17-game variance estimate
                                            is still noisy"), typed 09-09 after the
                                            2023-25 research existed; no procedure ->
                                            BRACKETED.
  TEAM_CHANGE_KEEP        0.60    UNKNOWN   b08440e judgment, no data cited, no procedure
                                            -> BRACKETED.
  COACH_CHANGE_KEEP       0.70    UNKNOWN   b08440e judgment; its motivation (shotgun .79
                                            -> .12) is 2025 research -> BRACKETED.
  MIN_MEAN 0.05, MIN_VMR 1.05     floors    structural; not bracketed; the share of
                                            predictions where each floor binds is reported.
  features cap=4, min_games=4,    floors    structural; not bracketed.
  pos_prior n_players<3 fallback

  No UNKNOWN constant is left un-bracketed, so Part 1 proceeds; the verdict is
  read as robust only if every variant agrees.

Declared drift procedure (per window T, season pair (T-2, T-1)): REG player-weeks,
newest data_version, stat not null; players with >= 6 such games in BOTH seasons
and prior-season mean >= the research screen (receptions 2.0, carries 6.0);
D = max(0, mean over players of (m1-m0)^2 - v0/n0 - v1/n1)  (population variances).

Variants, one at a time from the default (13 per season):
  default    k=6, VMR=12, TEAM=0.60, COACH=0.70, drift=refit
  k          3, 12
  VMR        6, 24
  TEAM       0.40, 0.80, 1.00
  COACH      0.50, 0.85, 1.00
  drift      x0.5, x2

Features: as-of by the existing join (kickoff_ts < the game's nflverse kickoff),
prior_seasons=(T-1, T). Team and position come from the player's OWN nfl_player_week
row for the predicted game - the current crosswalk (`player_xwalk.last_team`, which
jobs/predict.py uses live) would be look-ahead for 2023-25.

Close: outcome_close.p_bench (median of multiplicatively de-vigged draftkings /
fanduel / betmgm at the last snapshot <= kickoff; lead <= 15 min). p_bench covers
only 43% / 48% of 2023 / 2024 receptions, so p_all (all books) is reported as a
SECONDARY close, counted.

Tests (intervals, 2,000-draw game block bootstrap, MDE = 2.8 x SE), per season:
  13  Brier(model) - Brier(p_bench), one per variant
   1  logloss(model) - logloss(p_bench), default
   2  Brier(model) - Brier(p_bench) per stat, default
   1  Brier(model) - Brier(p_all), default
   1  Brier(naive) - Brier(p_bench), default common set
  = 18 per season, 54 total.

ADDENDUM - CORRECTED SETTLEMENT ARM (added at the coordinator's request after a
smoke run and BEFORE any full-season score was computed; the pre-registered arm
above is unchanged and still settles through jobs.settle_outcomes):
  nflverse weekly stats carry NO row for a player who played and recorded no stat,
  and settle_outcomes treats a missing row as unsettled - which drops exactly the
  lost overs. Corrected arm: a missing/NULL player-week actual AND offensive snaps
  > 0 in nfl_snap_counts for that game (pfr_player_id -> gsis via player_xwalk)
  -> actual = 0, settled normally; missing AND no snap row or 0 offensive snaps ->
  void (excluded, as books void a DNP). Team/position for those fits come from the
  snap row. Added tests, per season: Brier(model) - Brier(p_bench) and
  Brier(model) - Brier(p_all) on the corrected set = 2 per season, 6 total -> 60.
  Reported beside it: outcomes moved unresolvable -> settled at 0, their realized
  over rate (0 by construction) against their mean p_bench, and their share of the
  close's p_bench mass against their share of outcomes.
"""
import argparse
import functools
import math
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core import settlement  # noqa: E402

SEASONS = (2023, 2024, 2025)
STATS = ("receptions", "rush_attempts")
STAT_COL = {"receptions": "receptions", "rush_attempts": "carries"}
RESEARCH_SCREEN = {"receptions": 2.0, "rush_attempts": 6.0}
DRIFT_MIN_GAMES = 6
DRIFT_KEYS = {"receptions": ("receptions", "targets", "receiving_yards"),
              "rush_attempts": ("rush_attempts", "rush_yards")}

DEFAULTS = {"SHRINK_GAMES_DEFAULT": 6.0, "SHRINK_GAMES_VMR": 12.0,
            "TEAM_CHANGE_KEEP": 0.60, "COACH_CHANGE_KEEP": 0.70}
VARIANTS = [("default", {})] + \
    [(f"k={v:g}", {"SHRINK_GAMES_DEFAULT": v}) for v in (3.0, 12.0)] + \
    [(f"VMR={v:g}", {"SHRINK_GAMES_VMR": v}) for v in (6.0, 24.0)] + \
    [(f"TEAM={v:.2f}", {"TEAM_CHANGE_KEEP": v}) for v in (0.40, 0.80, 1.00)] + \
    [(f"COACH={v:.2f}", {"COACH_CHANGE_KEEP": v}) for v in (0.50, 0.85, 1.00)] + \
    [(f"drift x{v:g}", {"_drift_scale": v}) for v in (0.5, 2.0)]
PATCHABLE = ("SHRINK_GAMES_DEFAULT", "SHRINK_GAMES_VMR", "TEAM_CHANGE_KEEP",
             "COACH_CHANGE_KEEP", "MEAN_DRIFT_VAR")
CLIP = 1e-4


class LeakError(AssertionError):
    """A constant used to predict season T was fitted on season T or later."""


# =============================================================================
# constants, walk-forward enforced
# =============================================================================

@dataclass
class Constants:
    target: int
    values: dict
    fit_seasons: dict = field(default_factory=dict)

    def check(self):
        for name, seas in self.fit_seasons.items():
            if seas and max(seas) >= self.target:
                raise LeakError(f"{name} fitted on {seas} used to predict {self.target}")
        return self


@contextmanager
def patched(values):
    """Set module constants on models.baseline in-process and ALWAYS restore."""
    from models import baseline
    bad = [k for k in values if k not in PATCHABLE]
    if bad:
        raise KeyError(f"{bad} are not patchable constants")
    saved = {k: getattr(baseline, k) for k in values}
    try:
        for k, v in values.items():
            setattr(baseline, k, dict(v) if isinstance(v, dict) else v)
        yield baseline
    finally:
        for k, v in saved.items():
            setattr(baseline, k, v)


def drift_from_panels(prev, cur, min_games=DRIFT_MIN_GAMES, screen=0.0):
    """prev/cur: {player: [values]} for two seasons -> (D, n) by the declared
    noise-corrected method of moments."""
    terms = []
    for g in set(prev) & set(cur):
        x, y = prev[g], cur[g]
        if len(x) < min_games or len(y) < min_games:
            continue
        m0, m1 = statistics.fmean(x), statistics.fmean(y)
        if m0 < screen:
            continue
        terms.append((m1 - m0) ** 2 - statistics.pvariance(x) / len(x)
                     - statistics.pvariance(y) / len(y))
    if not terms:
        return None, 0
    return max(0.0, statistics.fmean(terms)), len(terms)


def _season_panel(con, season, col):
    d = defaultdict(list)
    for g, v in con.execute(
            f"SELECT gsis_id, {col} FROM nfl_player_week w WHERE season=? AND season_type='REG' "
            "AND data_version=(SELECT MAX(data_version) FROM nfl_player_week v WHERE "
            "v.gsis_id=w.gsis_id AND v.season=w.season AND v.week=w.week AND v.season_type='REG')",
            (season,)):
        if v is not None:
            d[g].append(float(v))
    return d


def refit_drift(con, s0, s1):
    out, ns = {}, {}
    for stat, col in STAT_COL.items():
        D, n = drift_from_panels(_season_panel(con, s0, col), _season_panel(con, s1, col),
                                 screen=RESEARCH_SCREEN[stat])
        ns[stat] = n
        for key in DRIFT_KEYS[stat]:
            out[key] = D
    return out, ns


def constants_for(target, overrides, drift_fn):
    """The constants used to predict season `target` under one variant."""
    drift, _n = drift_fn(target - 2, target - 1)
    values = dict(DEFAULTS)
    scale = overrides.get("_drift_scale", 1.0)
    values["MEAN_DRIFT_VAR"] = {k: v * scale for k, v in drift.items()}
    values.update({k: v for k, v in overrides.items() if not k.startswith("_")})
    fit = {k: () for k in DEFAULTS}
    fit["MEAN_DRIFT_VAR"] = (target - 2, target - 1)
    return Constants(target, values, fit).check()


# =============================================================================
# scoring
# =============================================================================

def clip(p):
    return min(max(p, CLIP), 1 - CLIP)


def brier(p, y):
    return (p - y) ** 2


def logloss(p, y):
    p = clip(p)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def mde(res):
    return None if not res else 2.8 * res["se"]


# =============================================================================
# prediction (worker side)
# =============================================================================

_W = {}


def _worker_init(db_path):
    from models import features
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    _W["con"] = con
    # Feature queries do not depend on the constants, so every variant of one
    # fit reuses them. Cached per process; the functions are restored by
    # process exit.
    for name in ("player_prior", "role_rank", "positional_prior", "team_context"):
        fn = getattr(features, name)
        setattr(features, name, functools.lru_cache(maxsize=200_000)(fn))


def _predict_task(task):
    """task = (T, gsis, stat, game, kickoff, week, position, team,
               [(outcome_id, line, push)], {variant: Constants.values}).
    Position and team are resolved by the driver from the player's own row for
    that game (player-week, or the snap-count row in the corrected arm)."""
    from models import baseline
    T, gsis, stat, game, kickoff, week, position, team, lines, cvals = task
    con = _W["con"]
    if not team:
        return {"err": "no team for the predicted game", "lines": lines}
    out = {"probs": {}, "meta": {}}
    for vname, values in cvals.items():
        with patched(values):
            try:
                fit = baseline.fit_player_stat(con, gsis, stat, T, kickoff, position, team,
                                               prior_seasons=(T - 1, T))
            except Exception as e:  # noqa: BLE001
                return {"err": f"fit failed: {type(e).__name__}", "lines": lines}
        try:
            fit.provenance.assert_as_of(kickoff)
        except Exception as e:  # noqa: BLE001
            return {"err": f"PROVENANCE POSTDATES KICKOFF: {e}", "lines": lines}
        out["probs"][vname] = {oid: fit.dist.prob_over(float(line), push=bool(push))
                               for oid, line, push in lines}
        if vname == "default":
            p = fit.params
            out["meta"] = {"prior_games": fit.prior_games, "notes": fit.notes,
                           "min_vmr_binds": abs(p.get("var_mean_ratio", 9) - baseline.MIN_VMR) < 1e-9,
                           "min_mean_binds": abs(p.get("mean", 9) - baseline.MIN_MEAN) < 1e-9,
                           "team_change": "team " in fit.notes, "coach_change": "coach " in fit.notes}
    return out


# =============================================================================
# driver
# =============================================================================

def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


# `corrected_result` used to live here and judged EVERY stat on offensive snaps.
# It is gone: the rule is core/settlement.py, which picks the snap column from
# the stat. This copy was wrong for tackles and sacks - a defender's offensive
# snap count is 0 in every game he plays - and was dormant only because STATS
# here is ("receptions", "rush_attempts").


def snap_rows(con, T):
    """(gsis, game_id) -> (snap, team, position) at the newest data_version,
    where snap is (team, offense_snaps, defense_snaps) - the shape the shared
    settlement rule reads.

    BOTH PHASES, deliberately. This loader selected `offense_snaps` alone, which
    is what made the offence-only rule above impossible to fix in one line.
    """
    out = {}
    for gsis, game_id, team, pos, off, dfn in con.execute(
            """SELECT x.gsis_id, s.game_id, s.team, s.position, s.offense_snaps,
                      s.defense_snaps
                 FROM nfl_snap_counts s JOIN player_xwalk x ON x.pfr_id = s.pfr_player_id
                WHERE s.season = ? AND s.data_version = (
                      SELECT MAX(t.data_version) FROM nfl_snap_counts t
                       WHERE t.game_id = s.game_id AND t.pfr_player_id = s.pfr_player_id)""", (T,)):
        prev = out.get((gsis, game_id))
        if prev is None or (off or 0) > (prev[0][1] or 0):
            out[(gsis, game_id)] = ((team, off or 0, dfn or 0), team, pos)
    return out


def player_week_rows(con, T):
    return {(g, w): (p, t) for g, w, p, t in con.execute(
        "SELECT gsis_id, week, position, team FROM nfl_player_week w WHERE season=? AND season_type='REG' "
        "AND data_version=(SELECT MAX(data_version) FROM nfl_player_week v WHERE v.gsis_id=w.gsis_id "
        "AND v.season=w.season AND v.week=w.week AND v.season_type='REG')", (T,))}


def load_outcomes(con, T):
    return con.execute(
        """SELECT o.outcome_id, o.key, o.sport, o.season, o.week, o.entity_type, o.entity_id,
                  o.stat, o.line, o.side, o.push_possible, o.event_id,
                  oc.p_bench, oc.p_all, oc.lead_min, g.k
             FROM outcome_close oc JOIN outcomes o USING (outcome_id)
             JOIN (SELECT game_id, MAX(kickoff_ts) k FROM nfl_games GROUP BY game_id) g
               ON g.game_id = o.event_id
            WHERE o.season = ? AND o.side = 'over' AND o.stat IN ('receptions', 'rush_attempts')
              AND oc.lead_min <= 15""", (T,)).fetchall()


def naive_inputs(con, T):
    hist = defaultdict(list)
    for gsis, rec, tgt, car in con.execute(
            "SELECT gsis_id, receptions, targets, carries FROM nfl_player_week w WHERE season=? "
            "AND season_type='REG' AND data_version=(SELECT MAX(data_version) FROM nfl_player_week v "
            "WHERE v.gsis_id=w.gsis_id AND v.season=w.season AND v.week=w.week AND v.season_type='REG')",
            (T - 1,)):
        hist[(gsis, "receptions")].append((rec, tgt))
        hist[(gsis, "rush_attempts")].append((car, car))
    pooled = defaultdict(list)
    for (gsis, stat), games in hist.items():
        pooled[stat] += [s for s, o in games if (o or 0) >= 1]
    return hist, pooled


def run(workers=20, seasons=SEASONS, limit=None, out=print):
    from jobs.settle_outcomes import settle_one, UNSETTLED, OVER, PUSH
    from research.score import naive_prob
    from research.sweep import common as SW

    con = ro()
    tests = []
    t_all = time.time()
    out(__doc__.split("DECLARED HERE")[0].strip())
    out("\nprovenance + variants: see the module docstring (declared before this run)")
    for T in seasons:
        _hdr(out, f"SEASON {T} - constants fit on <= {T - 1}; prior_seasons=({T - 1}, {T})")
        drift, dn = refit_drift(con, T - 2, T - 1)
        out(f"  drift refit on ({T - 2}, {T - 1}): rush_attempts {drift['rush_attempts']:.3f} "
            f"(n={dn['rush_attempts']}), receptions {drift['receptions']:.3f} (n={dn['receptions']})"
            f"   [committed constants were 8.56 / 1.12]")
        cvals = {}
        for vname, ov in VARIANTS:
            c = constants_for(T, ov, lambda a, b: (drift, dn))
            cvals[vname] = c.values

        rows = load_outcomes(con, T)
        if limit:
            rows = rows[:limit]
        census, ccensus = Counter(), Counter()
        settled, settled_corr, groups = {}, {}, defaultdict(list)
        snaps = snap_rows(con, T)
        pwrows = player_week_rows(con, T)
        pos_team = {}
        for r in rows:
            (oid, key, sport, season, week, etype, gsis, stat, line, side, push, game,
             pb, pa, lead, kick) = r
            result, actual, _v, _void = settle_one(con, (oid, key, sport, season, week, etype, gsis,
                                                         stat, line, side, push))
            moved = False
            if result == UNSETTLED:
                census["unsettled (no player-week actual)"] += 1
                snap, steam, spos = snaps.get((gsis, game), (None, None, None))
                result, actual, _reason, _status = settlement.settle(
                    None, False, snap, stat, line, push)
                if result == settlement.VOID:
                    ccensus["void (no snap row or 0 offensive snaps)"] += 1
                    continue
                moved = True
                ccensus["moved: played, settled at 0"] += 1
                pos_team.setdefault((gsis, game), (spos, steam))
            elif result == PUSH:
                census["push"] += 1
                ccensus["push"] += 1
                continue
            else:
                census["settled"] += 1
                ccensus["settled as pre-registered"] += 1
            if result == PUSH:
                ccensus["push"] += 1
                continue
            rec = {"game": game, "stat": stat, "line": line, "gsis": gsis, "moved": moved,
                   "y": 1.0 if result == OVER else 0.0, "p_bench": pb, "p_all": pa}
            if not moved:
                settled[oid] = rec
                pw = pwrows.get((gsis, week))
                if pw:
                    pos_team.setdefault((gsis, game), pw)
                else:
                    _snap, steam, spos = snaps.get((gsis, game), (None, None, None))
                    pos_team.setdefault((gsis, game), (spos, steam))
            settled_corr[oid] = rec
            groups[(gsis, stat, game, kick, week)].append((oid, line, push))
        tasks = [(T, g, s, gm, k, w, *pos_team.get((g, gm), (None, None)), lines, cvals)
                 for (g, s, gm, k, w), lines in sorted(groups.items())]
        out(f"  outcomes {len(rows)}")
        out(f"  pre-registered settlement: {dict(census)}")
        out(f"  CORRECTED settlement:      {dict(ccensus)}")
        out(f"  fit tasks {len(tasks)}")
        t0 = time.time()
        results = []
        if workers > 1:
            import multiprocessing as mp
            # Group by kickoff so a worker's positional-prior cache sees the same
            # as_of repeatedly; imap preserves order, so the result is deterministic.
            tasks.sort(key=lambda t: (t[4], t[1], t[2]))
            with mp.Pool(workers, initializer=_worker_init, initargs=(config.DB_PATH,)) as pool:
                results = list(pool.imap(_predict_task, tasks, chunksize=8))
        else:
            _worker_init(config.DB_PATH)
            results = [_predict_task(t) for t in tasks]
        out(f"  predicted in {time.time() - t0:.0f}s")

        errs = Counter()
        meta = Counter()
        preds = defaultdict(dict)
        for res in results:
            if "err" in res:
                errs[res["err"]] += len(res["lines"])
                continue
            for vname, probs in res["probs"].items():
                for oid, p in probs.items():
                    preds[vname][oid] = p
            m = res["meta"]
            for k in ("min_vmr_binds", "min_mean_binds", "team_change", "coach_change"):
                meta[k] += bool(m.get(k))
            meta["fits"] += 1
        if any(e.startswith("PROVENANCE") for e in errs):
            raise LeakError(f"as-of violation in season {T}: {errs}")
        out(f"  prediction errors (excluded): {dict(errs)}")
        out(f"  fits {meta['fits']}: team change {meta['team_change']}, coach change {meta['coach_change']}, "
            f"MIN_VMR binds {meta['min_vmr_binds']}, MIN_MEAN binds {meta['min_mean_binds']}")

        hist, pooled = naive_inputs(con, T)
        base = []
        for oid, s in settled.items():
            if oid not in preds["default"]:
                continue
            pool = pooled[s["stat"]]
            nv, _src = naive_prob(hist.get((s["gsis"], s["stat"]), []),
                                  (sum(1 for x in pool if (x or 0) > s["line"]), len(pool)), s["line"])
            base.append(dict(s, oid=oid, naive=nv))
        bench = [r for r in base if r["p_bench"] is not None]
        allc = [r for r in base if r["p_all"] is not None]
        out(f"  common set vs p_bench: {len(bench)} outcomes, {len({r['game'] for r in bench})} games; "
            f"vs p_all: {len(allc)}")

        def diff(rows, f, a, b):
            return SW.boot(rows, lambda rs: statistics.fmean(f(r[a], r["y"]) - f(r[b], r["y"]) for r in rs)
                           if rs else None)

        for r in bench:
            for vname in cvals:
                r["m_" + vname] = preds[vname].get(r["oid"])
        for r in allc:
            r["m_default"] = preds["default"].get(r["oid"])

        levels = {name: statistics.fmean(f(r[k], r["y"]) for r in bench)
                  for name, (f, k) in {"Brier model": (brier, "m_default"), "Brier close": (brier, "p_bench"),
                                        "Brier naive": (brier, "naive"), "logloss model": (logloss, "m_default"),
                                        "logloss close": (logloss, "p_bench"),
                                        "logloss naive": (logloss, "naive")}.items()} if bench else {}
        out("  levels (p_bench common set): " + "  ".join(f"{k} {v:.4f}" for k, v in levels.items()))
        for vname in cvals:
            res = diff(bench, brier, "m_" + vname, "p_bench")
            tests.append((T, f"Brier(model[{vname}]) - Brier(p_bench)", res))
        tests.append((T, "logloss(model) - logloss(p_bench)", diff(bench, logloss, "m_default", "p_bench")))
        for st in STATS:
            sub = [r for r in bench if r["stat"] == st]
            tests.append((T, f"Brier(model) - Brier(p_bench) | {st}", diff(sub, brier, "m_default", "p_bench")))
        tests.append((T, "Brier(model) - Brier(p_all)  [secondary close]", diff(allc, brier, "m_default", "p_all")))
        tests.append((T, "Brier(naive) - Brier(p_bench)", diff(bench, brier, "naive", "p_bench")))

        # ---- corrected settlement arm -------------------------------------------
        corr = [dict(s, oid=oid, m_default=preds["default"][oid])
                for oid, s in settled_corr.items() if oid in preds["default"]]
        corr_b = [r for r in corr if r["p_bench"] is not None]
        corr_a = [r for r in corr if r["p_all"] is not None]
        mv_b = [r for r in corr_b if r["moved"]]
        mv_a = [r for r in corr_a if r["moved"]]
        out(f"  CORRECTED arm: {len(corr_b)} outcomes vs p_bench ({len(mv_b)} moved to settled-at-0), "
            f"{len(corr_a)} vs p_all ({len(mv_a)} moved)")
        if mv_a:
            out(f"    moved outcomes: realized over rate {statistics.fmean(r['y'] for r in mv_a):.3f} "
                f"(0 by construction); mean p_all {statistics.fmean(r['p_all'] for r in mv_a):.3f} "
                f"vs {statistics.fmean(r['p_all'] for r in corr_a if not r['moved']):.3f} on the rest")
        if mv_b:
            mass = sum(r["p_bench"] for r in mv_b) / sum(r["p_bench"] for r in corr_b)
            out(f"    moved outcomes: mean p_bench {statistics.fmean(r['p_bench'] for r in mv_b):.3f} vs "
                f"{statistics.fmean(r['p_bench'] for r in corr_b if not r['moved']):.3f} on the rest; "
                f"share of p_bench mass {100 * mass:.2f}% vs share of outcomes "
                f"{100 * len(mv_b) / len(corr_b):.2f}%; mean model p {statistics.fmean(r['m_default'] for r in mv_b):.3f}")
        tests.append((T, "CORRECTED Brier(model) - Brier(p_bench)", diff(corr_b, brier, "m_default", "p_bench")))
        tests.append((T, "CORRECTED Brier(model) - Brier(p_all)", diff(corr_a, brier, "m_default", "p_all")))
        for (s, name, res) in tests:
            if s == T:
                out(f"    {name:<52} {_iv(res)}")
    _hdr(out, "SUMMARY")
    out(f"  intervals computed {len(tests)} (declared 54 + 6 corrected-arm = 60); "
        f"estimable {sum(1 for *_, r in tests if r)}")
    for T in seasons:
        heads = [r for s, n, r in tests if s == T and n.startswith("Brier(model[")]
        signs = Counter(("model worse*" if r["lo"] > 0 else "model better*" if r["hi"] < 0 else "null")
                        for r in heads if r)
        out(f"  {T}: across 13 variants Brier(model)-Brier(p_bench): {dict(signs)}")
    out(f"  runtime {time.time() - t_all:.0f}s")
    return tests


def _iv(res):
    if not res:
        return "n/a"
    star = "*" if res["lo"] > 0 or res["hi"] < 0 else " "
    return (f"{res['est']:+.4f} [{res['lo']:+.4f}, {res['hi']:+.4f}]{star} p={res['p']:.1e} "
            f"MDE={mde(res):.4f} n={res['n']} games={res['games']}")


def _hdr(out, t):
    out("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--season", type=int, action="append")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    run(a.workers, tuple(a.season) if a.season else SEASONS, a.limit,
        out=lambda s: print(s, flush=True))


if __name__ == "__main__":
    main()
