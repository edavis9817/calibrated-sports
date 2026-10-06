"""c-34 - does known-before-kickoff information raise the prop model's resolution?

    set LOGGER_DB=D:/calibrated-sports/data/market_log.db   (opened mode=ro only)
    python -m research.known_before_kickoff --audit --out-dir D:/temp/c34      # Step 0 only
    python -m research.known_before_kickoff --ledger D:/temp/c24/wf_ledger.csv \
        --p2 D:/temp/c27/p2_rows.json --out-dir D:/temp/c34

PRE-REGISTRATION: docs/C34-known-before-kickoff-preregistration.md, committed at
32c1795 (addendum 0 at 774c798) BEFORE this script existed. This file implements
it; it does not extend it. The scoring is research.ranking_calibration and the
panel is research.decomposed_usage's, both IMPORTED - never copied.

Everything PRINTED is an aggregate or an interval (BET_LIST_RESTRICTION). The
per-outcome rows go to --out-dir, which is scratch and never committed.
"""
import argparse
import bisect
import calendar
import glob
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

import numpy as np
from scipy.stats import nbinom

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from research import decomposed_usage as du  # noqa: E402  (c-27's panel and P1 loader)
from research import ranking_calibration as rc  # noqa: E402  (the c-24 scoring code)

BOOT, SEED = 2000, 34
FIRST_TRAIN = 2013                    # snap counts start 2013
PREV_W = 0.5                          # weight on season T-1 games (c-27's declared weight)
SHRINK_K = 6.0
MIN_ASOF = 4
SCREEN = {"receptions": 2.0, "rush_attempts": 6.0}
STAT_COL = {"receptions": "rec", "rush_attempts": "car"}
VOL = {"receptions": "tgt", "rush_attempts": "car"}
FAMILIES = {"receptions": ("WR", "TE", "RB"), "rush_attempts": ("RB",)}
SKILL = ("WR", "TE", "RB")
MIN_FEATURE_ROWS = 200
RIDGE = 1e-6
M_CLIP = (0.5, 2.0)
VMR_FLOOR = 1.05
WINDOW_DAYS = 8
P2_LEAD = 180 * 60                    # P2 is priced at kickoff - 180 min
ROSTER_GAMES = 4
MIN_ROWS, MIN_WEEKS = 3000, 20        # the stop rule

ARM1 = ("own_Q", "own_DO", "own_P", "own_LP", "own_DNP", "own_listed_full",
        "above_absent", "above_Q", "below_absent", "below_Q")
ARM2 = ("rank2", "rank3p", "unlisted", "promoted", "demoted",
        "chart_above_usage", "chart_below_usage")
ARM3 = ("vac_same_new", "vac_same_old", "vac_other", "qb1_absent")
ARMS = {"arm1": ARM1, "arm2": ARM2, "arm3": ARM3}
INJ_LAST_STAMPED = 2024               # 2025 has no upstream stamp: excluded
DT_FIRST = 2025                       # depth charts carry `dt` from 2025


class LeakError(AssertionError):
    """A training season at or after the test season."""


# =============================================================================
# the as-of rules - pure
# =============================================================================

def injury_row_provable(asof, kickoff):
    """Seasons with an upstream stamp: provable iff it sits inside
    (kickoff - 8 days, kickoff). -> (bool, reason)."""
    if asof is None:
        return False, "no stamp"
    if asof >= kickoff:
        return False, "stamped at or after kickoff"
    if asof <= kickoff - WINDOW_DAYS * 86400:
        return False, "stamped more than 8 days before kickoff"
    return True, "ok"


def team_report_usable(rows, kickoff):
    """rows: [(gsis, status, practice, asof)]. Usable iff >=1 row and EVERY row
    provable. -> (bool, reason)."""
    if not rows:
        return False, "no rows for the team-week"
    for _g, _s, _p, asof in rows:
        ok, why = injury_row_provable(asof, kickoff)
        if not ok:
            return False, why
    return True, "ok"


def version_in_force(versions, cut):
    """versions: [(valid_from, valid_to, payload)]. The one with
    valid_from <= cut < valid_to (valid_to None = open)."""
    for vf, vt, payload in versions:
        if vf <= cut and (vt is None or cut < vt):
            return payload
    return None


def last_snapshot_before(dts, cut, kickoff):
    """dts ascending. Index of the last dt STRICTLY before cut and within 8 days
    of kickoff, else None."""
    i = bisect.bisect_left(dts, cut) - 1
    if i < 0 or dts[i] <= kickoff - WINDOW_DAYS * 86400:
        return None
    return i


def bucket(rank):
    return None if rank is None else min(int(rank), 3)


def is_absent(status):
    return status in ("Out", "Doubtful")


# =============================================================================
# sources, read-only
# =============================================================================

def feeds_ro():
    path = config.storage_path("feeds.db")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_injuries():
    """-> stamped {(season, week, team): [(gsis, status, practice, asof)]} for
    seasons with an upstream stamp era (<= 2025, REG), and versioned
    {(season, week, team): {gsis: [(valid_from, valid_to, (status, practice))]}}
    plus the capture times, for 2026."""
    con = feeds_ro()
    stamped, versioned = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for s, w, t, pid, st, pr, asof, vf, vt in con.execute(
            "SELECT season, week, team, player_id, report_status, practice_status, "
            "upstream_asof_ts, valid_from_ts, valid_to_ts FROM injury_reports "
            "WHERE sport = 'nfl' AND season_type = 'REG'"):
        if s <= 2025:
            if vt is None:
                stamped[(s, w, t)].append((pid, st, pr, asof))
        else:
            versioned[(s, w, t)][pid].append((vf, vt, (st, pr)))
    captures = sorted(r[0] for r in con.execute(
        "SELECT fetched_ts FROM feeds_raw_files WHERE feed = 'injuries' AND scope = '2026'"))
    con.close()
    return dict(stamped), {k: dict(v) for k, v in versioned.items()}, captures


def _iso(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


def depth_root():
    return os.path.join(os.path.dirname(config.DB_PATH), "raw", "nflverse")


def load_depth_labelled(root, seasons):
    """2001-2024 format. -> {(season, team): [(week, {gsis: (family, tier)})]} ascending."""
    import polars as pl
    out = defaultdict(list)
    for s in seasons:
        fs = sorted(glob.glob(os.path.join(root, "*", f"depth_charts_{s}.parquet")))
        if not fs:
            continue
        d = pl.read_parquet(fs[-1]).filter(
            (pl.col("formation") == "Offense") & (pl.col("game_type") == "REG")
            & pl.col("week").is_not_null() & pl.col("gsis_id").is_not_null()
            & pl.col("position").is_in(["WR", "TE", "RB", "QB"])
            & (pl.col("depth_position").str.strip_chars() != "FB"))
        d = d.with_columns(pl.col("depth_team").cast(pl.Int32, strict=False).alias("tier")).drop_nulls("tier")
        d = d.group_by("club_code", "week", "gsis_id", "position").agg(pl.col("tier").min())
        charts = defaultdict(dict)
        for team, week, gs, fam, tier in d.iter_rows():
            cur = charts[(team, week)].get(gs)
            if cur is None or tier < cur[1]:
                charts[(team, week)][gs] = (fam, tier)
        for (team, week), ch in sorted(charts.items()):
            out[(s, team)].append((week, ch))
    return dict(out)


def _dt_frame(path):
    import polars as pl
    d = pl.read_parquet(path).filter(
        (pl.col("pos_grp") == "3WR 1TE") & pl.col("gsis_id").is_not_null()
        & pl.col("pos_abb").is_in(["WR", "TE", "RB", "QB"]))
    # depth rank = ordinal WITHIN the slot (addendum 0.4)
    d = d.with_columns(pl.col("pos_rank").rank("dense").over("dt", "team", "pos_abb", "pos_slot")
                       .cast(pl.Int32).alias("tier"))
    return d.group_by("dt", "team", "gsis_id", "pos_abb").agg(pl.col("tier").min())


def load_depth_dt(root, seasons):
    """2025+ format, newest archived copy. -> {(season, team): ([dt_ts...], [chart...])}."""
    out = {}
    for s in seasons:
        fs = sorted(glob.glob(os.path.join(root, "*", f"depth_charts_{s}.parquet")))
        if not fs:
            continue
        charts = defaultdict(dict)
        for dt, team, gs, fam, tier in _dt_frame(fs[-1]).iter_rows():
            cur = charts[(team, _iso(dt))].get(gs)
            if cur is None or tier < cur[1]:
                charts[(team, _iso(dt))][gs] = (fam, tier)
        by = defaultdict(list)
        for (team, ts), ch in sorted(charts.items(), key=lambda x: (x[0][0], x[0][1])):
            by[team].append((ts, ch))
        for team, v in by.items():
            out[(s, team)] = ([x[0] for x in v], [x[1] for x in v])
    return out


def audit_depth_copies(root, season, out):
    """Every archived copy of the season's file: a past dt's rows must be
    identical in the newest copy, and no dt may postdate its copy's day."""
    import polars as pl
    fs = sorted(glob.glob(os.path.join(root, "*", f"depth_charts_{season}.parquet")))
    if len(fs) < 2:
        out(f"   depth {season}: {len(fs)} archived copy - snapshot stability NOT checkable; dt is upstream's claim")
        return {"copies": len(fs), "checked": 0, "changed": None, "future": None}

    def sig(path):
        d = pl.read_parquet(path)
        chart = sorted(c for c in d.columns if c != "gsis_id")     # the chart itself, keyed on espn_id
        h = d.with_columns(pl.struct(chart).hash(seed=34).alias("h"),
                           pl.struct(sorted(d.columns)).hash(seed=34).alias("hid"))              .group_by("dt").agg(pl.col("h").sum(), pl.col("hid").sum(), pl.len())
        return {dt: (hh, hid, n) for dt, hh, hid, n in h.iter_rows()}
    ref = sig(fs[-1])
    checked = changed = future = missing = relabelled = 0
    for f in fs[:-1]:
        day = os.path.basename(os.path.dirname(f))
        day_end = calendar.timegm(time.strptime(day, "%Y-%m-%d")) + 2 * 86400
        for dt, v in sig(f).items():
            checked += 1
            if _iso(dt) > day_end:
                future += 1
            if dt not in ref:
                missing += 1
            elif (ref[dt][0], ref[dt][2]) != (v[0], v[2]):
                changed += 1
            elif ref[dt][1] != v[1]:
                relabelled += 1
    out(f"   depth {season}: {len(fs)} archived copies, {checked:,} (copy, dt) snapshots re-read against the "
        f"newest copy: {changed} with a changed chart (team, position, slot, rank, espn_id), {relabelled} with "
        f"only gsis_id rewritten, {missing} dropped, {future} dated after their copy")
    return {"copies": len(fs), "checked": checked, "changed": changed, "gsis_rewritten": relabelled,
            "missing": missing, "future": future}


# =============================================================================
# as-of context
# =============================================================================

class Context:
    """As-of usage, rosters, reports and charts for every (team, game)."""

    def __init__(self, panel, stamped, versioned, lab, dts):
        self.p, self.stamped, self.versioned, self.lab, self.dts = panel, stamped, versioned, lab, dts
        self.kicks = {gs: [x[0] for x in v] for gs, v in panel.player.items()}
        self.tgames = {t: [(k, g) for k, _s, g, _o, _a in v] for t, v in panel.team.items()}
        self.tkicks = {t: [x[0] for x in v] for t, v in self.tgames.items()}
        self.members = defaultdict(list)
        for (gs, g), r in panel.pg.items():
            self.members[(g, r["team"])].append(gs)
        self._asof, self._ctx = {}, {}

    def asof(self, gs, kick, season):
        """Weighted as-of sums over played games in (season-1, season) before kick."""
        key = (gs, kick, season)
        hit = self._asof.get(key)
        if hit is not None:
            return hit
        a = {"n": 0, "w": 0.0, "tgt": 0.0, "rec": 0.0, "car": 0.0, "att": 0.0,
             "ttgt": 0.0, "tcar": 0.0, "last_team": None, "last_game": None}
        lst = self.p.player.get(gs, ())
        i = bisect.bisect_left(self.kicks.get(gs, ()), kick)
        for j in range(i - 1, -1, -1):
            _k, s, g, r = lst[j]
            if s < season - 1:
                break
            if a["last_team"] is None:
                a["last_team"], a["last_game"] = r["team"], g
            w = 1.0 if s == season else PREV_W
            a["n"] += 1
            a["w"] += w
            for c in ("tgt", "rec", "car", "att"):
                a[c] += w * r[c]
            t = self.p.tg[(g, r["team"])]
            a["ttgt"] += w * t["tgt"]
            a["tcar"] += w * t["car"]
        self._asof[key] = a
        return a

    def prev_game(self, team, kick):
        i = bisect.bisect_left(self.tkicks.get(team, ()), kick)
        return self.tgames[team][i - 1] if i > 0 else None

    # ---- reports -----------------------------------------------------------
    def report(self, season, week, team, kick, cut):
        """-> (usable, reason, {gsis: (status, practice)})."""
        if season <= INJ_LAST_STAMPED:
            rows = self.stamped.get((season, week, team), [])
            ok, why = team_report_usable(rows, kick)
            return ok, why, ({g: (s, p) for g, s, p, _a in rows} if ok else {})
        if season == 2025:
            return False, "2025: no upstream stamp, ingested 2026-09-20", {}
        vers = self.versioned.get((season, week, team), {})
        rep = {}
        for gs, vv in vers.items():
            got = version_in_force(vv, cut)
            if got is not None:
                rep[gs] = got
        if not rep:
            return False, "no capture of the team-week at or before the cut", {}
        return True, "ok", rep

    # ---- charts ------------------------------------------------------------
    def chart(self, season, week, team, kick, cut, labelled_ok):
        """-> (kind, chart or None). kind: 'dt', 'label' (lagged), or a reason."""
        if season >= DT_FIRST:
            got = self.dts.get((season, team))
            if not got:
                return "no dt file", None
            i = last_snapshot_before(got[0], cut, kick)
            return ("dt", got[1][i]) if i is not None else ("no dt snapshot inside 8 days before the cut", None)
        if not labelled_ok:
            return "weekly label, no timestamp", None
        prior = [c for w, c in self.lab.get((season, team), ()) if w < week]
        return ("label", prior[-1]) if prior else ("no earlier labelled week", None)

    # ---- the team-game ------------------------------------------------------
    def team_ctx(self, game, team, cut_lead):
        key = (game, team, cut_lead)
        hit = self._ctx.get(key)
        if hit is not None:
            return hit
        season, week, kick = self.p.games[game][:3]
        cut = kick - cut_lead
        usable, why, rep = self.report(season, week, team, kick, cut)
        # roster: last played game was for this team, inside its last 4 games
        i = bisect.bisect_left(self.tkicks.get(team, ()), kick)
        recent = self.tgames.get(team, [])[max(0, i - ROSTER_GAMES):i]
        last_g = recent[-1][1] if recent else None
        cand = set()
        for _k, g in recent:
            cand.update(self.members.get((g, team), ()))
        roster = {}
        for gs in cand:
            a = self.asof(gs, kick, season)
            if a["n"] and a["last_team"] == team:
                roster[gs] = a
        for gs in rep:
            if gs not in roster:
                a = self.asof(gs, kick, season)
                if a["n"]:
                    roster[gs] = a
        fam = {}
        for gs in roster:
            lst = self.p.player[gs]
            j = bisect.bisect_left(self.kicks[gs], kick) - 1
            fam[gs] = lst[j][3]["pos"]
        played_last = set(self.members.get((last_g, team), ())) if last_g else set()
        ranks = {}
        for stat, vol in VOL.items():
            by = defaultdict(list)
            for gs, a in roster.items():
                by[fam[gs]].append((-(a[vol] / a["w"]), gs))
            rk = {}
            for f, v in by.items():
                v.sort()
                for n, (_m, gs) in enumerate(v):
                    rk[gs] = (n + 1, [x[1] for x in v])
            ranks[stat] = rk
        qb1 = None
        qbs = [(-(a["att"] / a["w"]), gs) for gs, a in roster.items() if fam[gs] == "QB" and a["att"] > 0]
        if qbs:
            qb1 = min(qbs)[1]
        ctx = {"season": season, "week": week, "kick": kick, "cut": cut, "usable": usable, "why": why,
               "rep": rep, "roster": roster, "fam": fam, "ranks": ranks, "qb1": qb1,
               "played_last": played_last, "prev": self.prev_game(team, kick)}
        self._ctx[key] = ctx
        return ctx

    # ---- one player-game ----------------------------------------------------
    def features(self, gs, game, team, pos, stat, cut_lead=0, labelled_ok=False):
        """-> dict: per-arm usable flags, features, family, usage rank, as-of."""
        c = self.team_ctx(game, team, cut_lead)
        a = self.asof(gs, c["kick"], c["season"])
        vol = VOL[stat]
        x = {}
        rk = c["ranks"][stat].get(gs)
        urank = rk[0] if rk else None
        out = {"inj_ok": c["usable"], "inj_why": c["why"], "asof": a, "pos": pos, "urank": urank}
        modelled = pos in FAMILIES[stat]
        out["modelled"] = modelled
        # ---------------- arms 1 and 3 ----------------
        if c["usable"] and modelled:
            st, pr = c["rep"].get(gs, (None, None))
            pr = pr or ""
            if st == "Questionable":
                x["own_Q"] = 1.0
            elif st in ("Doubtful", "Out"):
                x["own_DO"] = 1.0
            elif st == "Probable":
                x["own_P"] = 1.0
            if pr.startswith("Limited"):
                x["own_LP"] = 1.0
            elif pr.startswith("Did Not"):
                x["own_DNP"] = 1.0
            elif gs in c["rep"] and st is None and pr.startswith("Full"):
                x["own_listed_full"] = 1.0
            if rk:
                order = rk[1]
                i = rk[0] - 1
                for nm, j in (("above", i - 1), ("below", i + 1)):
                    if 0 <= j < len(order):
                        s2 = c["rep"].get(order[j], (None, None))[0]
                        if is_absent(s2):
                            x[f"{nm}_absent"] = 1.0
                        elif s2 == "Questionable":
                            x[f"{nm}_Q"] = 1.0
            tot = "ttgt" if vol == "tgt" else "tcar"
            for g2, (s2, _p2) in c["rep"].items():
                if g2 == gs or not is_absent(s2):
                    continue
                a2 = c["roster"].get(g2)
                if not a2:
                    continue
                if g2 == c["qb1"]:
                    x["qb1_absent"] = 1.0
                f2 = c["fam"].get(g2)
                if f2 not in SKILL or a2[tot] <= 0:
                    continue
                share = a2[vol] / a2[tot]
                if f2 == pos:
                    nm = "vac_same_new" if g2 in c["played_last"] else "vac_same_old"
                else:
                    nm = "vac_other"
                x[nm] = x.get(nm, 0.0) + share
        # ---------------- arm 2 ----------------
        kind, ch = self.chart(c["season"], c["week"], team, c["kick"], c["cut"], labelled_ok)
        out["dep_kind"] = kind
        out["dep_ok"] = ch is not None
        if ch is not None and modelled:
            mine = ch.get(gs)
            tier = mine[1] if mine and mine[0] == pos else None
            if tier is None:
                x["unlisted"] = 1.0
            elif tier == 2:
                x["rank2"] = 1.0
            elif tier >= 3:
                x["rank3p"] = 1.0
            if c["prev"] is not None and tier is not None:
                pk, pg_ = c["prev"]
                ps, pw = self.p.games[pg_][:2]
                _k2, pch = self.chart(ps, pw, team, pk, pk - cut_lead, labelled_ok)
                if pch is not None and pch.get(gs) and pch[gs][0] == pos:
                    if tier < pch[gs][1]:
                        x["promoted"] = 1.0
                    elif tier > pch[gs][1]:
                        x["demoted"] = 1.0
            if tier is not None and urank is not None:
                if bucket(tier) < bucket(urank):
                    x["chart_above_usage"] = 1.0
                elif bucket(tier) > bucket(urank):
                    x["chart_below_usage"] = 1.0
        out["x"] = x
        return out


# =============================================================================
# the stat-level fit - pure
# =============================================================================

def poisson_fit(X, y, offset, ridge=RIDGE, iters=50):
    """Poisson regression, log link, offset; column 0 of X is the intercept."""
    n, k = X.shape
    beta = np.zeros(k)
    beta[0] = math.log(max(y.sum(), 1e-9) / np.exp(offset).sum())
    for _ in range(iters):
        mu = np.exp(np.clip(offset + X @ beta, -30, 30))
        g = X.T @ (y - mu) - ridge * beta
        H = (X * mu[:, None]).T @ X + ridge * np.eye(k)
        step = np.linalg.solve(H, g)
        beta = beta + step
        if np.abs(step).max() < 1e-9:
            break
    mu = np.exp(np.clip(offset + X @ beta, -30, 30))
    H = (X * mu[:, None]).T @ X + ridge * np.eye(k)
    se = np.sqrt(np.diag(np.linalg.inv(H)))
    return beta, se


def fit_arm(train, names, test_season):
    """train: [(season, y, E0, x-dict)]. -> {'beta': {name: b}, 'table': [...], 'dropped': [...]}.
    Refuses if any training season >= test_season."""
    if any(s >= test_season for s, *_ in train):
        raise LeakError(f"training season >= test season {test_season}")
    counts = Counter()
    for _s, _y, _e, x in train:
        for nm in names:
            if x.get(nm):
                counts[nm] += 1
    keep = [nm for nm in names if counts[nm] >= MIN_FEATURE_ROWS]
    dropped = [(nm, counts[nm]) for nm in names if counts[nm] < MIN_FEATURE_ROWS]
    X = np.zeros((len(train), 1 + len(keep)))
    X[:, 0] = 1.0
    y = np.empty(len(train))
    off = np.empty(len(train))
    for i, (_s, yy, e0, x) in enumerate(train):
        y[i], off[i] = yy, math.log(e0)
        for j, nm in enumerate(keep):
            X[i, 1 + j] = x.get(nm, 0.0)
    beta, se = poisson_fit(X, y, off)
    return {"beta": {nm: float(beta[1 + j]) for j, nm in enumerate(keep)},
            "intercept": float(beta[0]), "n": len(train), "dropped": dropped,
            "table": [(nm, float(beta[1 + j]), float(se[1 + j]), counts[nm]) for j, nm in enumerate(keep)]}


def multiplier(beta, x):
    """exp(beta . x), intercept excluded, clipped. No non-zero feature -> exactly 1.0."""
    z = sum(b * x.get(nm, 0.0) for nm, b in beta.items())
    if z == 0.0:
        return 1.0
    return float(min(max(math.exp(z), M_CLIP[0]), M_CLIP[1]))


def nb_over(mu, v, line):
    """P(NB(mean mu, var v*mu) > line)."""
    return nbinom.sf(np.floor(line), mu / (v - 1.0), 1.0 / v)


def implied_mu(p, v, line, iters=70):
    """mu with P(NB(mu, v) > line) = p, by bisection in log mu (monotone)."""
    p = np.clip(p, 1e-9, 1 - 1e-9)
    lo, hi = np.full(len(p), math.log(1e-4)), np.full(len(p), math.log(500.0))
    for _ in range(iters):
        mid = (lo + hi) / 2
        up = nb_over(np.exp(mid), v, line) < p
        lo = np.where(up, mid, lo)
        hi = np.where(up, hi, mid)
    return np.exp((lo + hi) / 2)


def apply_multiplier(p, m, v, line):
    """The baseline probability with its implied mean scaled by m. m == 1 rows
    are returned bit for bit."""
    p, m, line = np.asarray(p, float), np.asarray(m, float), np.asarray(line, float)
    out = p.copy()
    mv = m != 1.0
    if mv.any():
        mu = implied_mu(p[mv], v, line[mv])
        out[mv] = np.clip(nb_over(mu * m[mv], v, line[mv]), rc.CLIP, 1 - rc.CLIP)
    return out


# =============================================================================
# building the rows
# =============================================================================

def role_priors(panel, ctx, seasons, stat):
    """(family, usage bucket) -> training mean of the stat per played game."""
    tot = defaultdict(lambda: [0.0, 0])
    for (gs, g), r in panel.pg.items():
        s = panel.games[g][0]
        if s not in seasons or r["pos"] not in FAMILIES[stat]:
            continue
        c = ctx.team_ctx(g, r["team"], 0)
        rk = c["ranks"][stat].get(gs)
        b = bucket(rk[0]) if rk else 3
        t = tot[(r["pos"], b)]
        t[0] += r[STAT_COL[stat]]
        t[1] += 1
    return {k: v[0] / v[1] for k, v in tot.items() if v[1]}


def e0_of(a, stat, prior):
    return (a[STAT_COL[stat]] + SHRINK_K * prior) / (a["w"] + SHRINK_K)


def stat_rows(panel, ctx, stat, seasons, priors, labelled_ok):
    """Screened played player-games of `seasons` -> [(season, game, gs, y, E0, feat)]."""
    rows = []
    for (gs, g), r in panel.pg.items():
        s = panel.games[g][0]
        if s not in seasons or r["pos"] not in FAMILIES[stat]:
            continue
        f = ctx.features(gs, g, r["team"], r["pos"], stat, 0, labelled_ok)
        a = f["asof"]
        if a["n"] < MIN_ASOF:
            continue
        prior = priors.get((r["pos"], bucket(f["urank"]) or 3))
        if prior is None:
            continue
        e0 = e0_of(a, stat, prior)
        if e0 < SCREEN[stat]:
            continue
        rows.append((s, g, gs, float(r[STAT_COL[stat]]), e0, f))
    return rows


def training_vmr(rows):
    """Mean within player-season variance / mean, floor 1.05."""
    by = defaultdict(list)
    for s, _g, gs, y, _e, _f in rows:
        by[(gs, s)].append(y)
    v = [np.var(x) / np.mean(x) for x in by.values() if len(x) >= 4 and np.mean(x) > 0]
    return max(float(np.mean(v)), VMR_FLOOR)


# =============================================================================
# scoring - c-24's functions, imported
# =============================================================================

def score_pair(name, rows, key, block, tests, out, draws, want=("dDSC", "dAUC")):
    """rows carry `key` (baseline + arm), m (baseline), k (market)."""
    pop = rc.Pop(f"{name} [{block}-block]", rows, key, "m")
    d, m, y, s = pop.m, pop.k, pop.y, pop.s
    k = np.array([r["k"] for r in rows], float)
    boot = lambda f: pop.boot(f, draws=draws, seed=SEED)  # noqa: E731
    stats = {
        "dDSC": lambda: boot(du._diff(du.dsc, d, m, y)),
        "dAUC": lambda: boot(du._diff(rc.auc, d, m, y)),
        "d_wAUC": lambda: boot(du._diff(rc.wauc, d, m, y, s)),
        "dMCB": lambda: boot(du._diff(du.mcb, d, m, y)),
        "dBrier": lambda: boot(du._diff(rc.brier, d, m, y)),
        "dDSC_vs_market": lambda: boot(du._diff(du.dsc, d, k, y)),
        "dAUC_vs_market": lambda: boot(du._diff(rc.auc, d, k, y)),
    }
    res = {"n": pop.n, "blocks": pop.games, "moved": int((d != m).sum())}
    for nm in want:
        r = stats[nm]()
        r["mde"] = 2.8 * r["se"] if r.get("se") else None
        res[nm] = r
        tests.append((name, nm, r))
        out(f"   {name:<34} {nm:<15} {rc.fmt(r, 5)}" + (f"  MDE {r['mde']:.5f}" if r["mde"] else ""))
    return res


def levels(rows, key):
    y = np.array([r["y"] for r in rows], float)
    o = {}
    for lab, kk in (("info", key), ("baseline", "m"), ("market", "k")):
        p = np.array([r[kk] for r in rows], float)
        c = rc.corp(p, y)
        o[lab] = {"brier": c["bs"], "mcb": c["mcb"], "dsc": c["dsc"], "auc": rc.auc(p, y)}
    return o


def verdict(r):
    if r["lo"] is None:
        return "not read"
    return "resolution rises" if r["lo"] > 0 else "resolution falls" if r["hi"] < 0 \
        else "no rise in resolution detected"


# =============================================================================
# main
# =============================================================================

def build(out):
    con = du.ro()
    t0 = time.time()
    panel = du.Panel(con)
    con.close()                       # the store is not held while anything is fitted
    out(f"panel: {len(panel.pg):,} player-games, {len(panel.games):,} REG games "
        f"({time.time() - t0:.0f}s, store closed)")
    stamped, versioned, captures = load_injuries()
    root = depth_root()
    lab = load_depth_labelled(root, range(FIRST_TRAIN, DT_FIRST))
    dts = load_depth_dt(root, (2025, 2026))
    out(f"injuries: {sum(len(v) for v in stamped.values()):,} stamped-era rows, "
        f"{sum(len(v) for v in versioned.values()):,} versioned 2026 player-weeks, {len(captures)} captures; "
        f"depth: {len(lab)} labelled team-seasons, {len(dts)} dt team-seasons")
    return panel, Context(panel, stamped, versioned, lab, dts), captures, root


def audit(panel, ctx, root, out):
    """Step 0: team-games usable per season and source, with reasons."""
    out("\n== STEP 0 - the as-of audit (REG team-games; cut = kickoff)")
    res = {"injuries": {}, "depth": {}}
    by = defaultdict(lambda: {"tg": 0, "inj": 0, "dep": 0, "why_inj": Counter(), "why_dep": Counter(),
                              "lead": []})
    for g, (s, w, k, h, a) in panel.games.items():
        if s < 2009 or k is None:
            continue
        for team in (h, a):
            b = by[s]
            b["tg"] += 1
            ok, why, rep = ctx.report(s, w, team, k, k - (P2_LEAD if s == 2026 else 0))
            b["inj"] += ok
            b["why_inj"][why] += 1
            if ok and s <= INJ_LAST_STAMPED:
                b["lead"].extend((k - x[3]) / 3600 for x in ctx.stamped[(s, w, team)])
            kind, ch = ctx.chart(s, w, team, k, k - (P2_LEAD if s == 2026 else 0), False)
            b["dep"] += ch is not None
            b["why_dep"][kind] += 1
    out(f"   {'season':<7}{'team-games':>11}{'inj usable':>11}{'depth usable':>13}   reasons unusable (injuries | depth)")
    for s in sorted(by):
        b = by[s]
        wi = "; ".join(f"{k} {v}" for k, v in b["why_inj"].most_common() if k != "ok")
        wd = "; ".join(f"{k} {v}" for k, v in b["why_dep"].most_common() if k != "dt")
        lead = f" median stamp lead {np.median(b['lead']):.0f}h" if b["lead"] else ""
        out(f"   {s:<7}{b['tg']:>11}{b['inj']:>11}{b['dep']:>13}   {wi or '-'}{lead} | {wd or '-'}")
        res["injuries"][s] = {"team_games": b["tg"], "usable": b["inj"], "why": dict(b["why_inj"]),
                              "median_lead_h": float(np.median(b["lead"])) if b["lead"] else None}
        res["depth"][s] = {"team_games": b["tg"], "usable": b["dep"], "why": dict(b["why_dep"])}
    res["copies"] = {s: audit_depth_copies(root, s, out) for s in (2025, 2026)}
    return res


def attach(rows, panel, ctx, cut_lead, out, name):
    """Features onto population rows. -> census."""
    cen = Counter()
    for r in rows:
        r["f"] = {}
        if r["game"] not in panel.games:
            cen["not a REG game (postseason)"] += 1
            continue
        pgr = panel.pg.get((r["gsis"], r["game"]))
        if pgr is None:
            cen["no panel row for the player-game"] += 1
            continue
        r["f"] = ctx.features(r["gsis"], r["game"], pgr["team"], pgr["pos"], r["stat"], cut_lead, False)
        r["f_lab"] = ctx.features(r["gsis"], r["game"], pgr["team"], pgr["pos"], r["stat"], cut_lead, True) \
            if r["season"] < DT_FIRST else None
        cen["in the panel"] += 1
        if not r["f"]["modelled"]:
            cen["position not modelled (m = 1)"] += 1
    out(f"   {name}: " + "; ".join(f"{k} {v:,}" for k, v in cen.most_common()))
    return dict(cen)


def survivors(rows, out, name):
    res = {}
    for s in sorted({r["season"] for r in rows}):
        rr = [r for r in rows if r["season"] == s]
        inj = sum(1 for r in rr if r["f"] and r["f"]["inj_ok"])
        dep = sum(1 for r in rr if r["f"] and r["f"]["dep_ok"])
        res[s] = {"rows": len(rr), "injury": inj, "depth": dep}
        out(f"   {name} {s}: rows {len(rr):,}  injury-usable {inj:,}  depth-usable (strict) {dep:,}")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default="D:/temp/c24/wf_ledger.csv")
    ap.add_argument("--p2", default="D:/temp/c27/p2_rows.json")
    ap.add_argument("--out-dir", default="D:/temp/c34")
    ap.add_argument("--audit", action="store_true", help="Step 0 only")
    ap.add_argument("--draws", type=int, default=BOOT)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    log = open(os.path.join(a.out_dir, "audit.log" if a.audit else "run.log"), "w", encoding="utf-8")

    def out(s=""):
        print(s, flush=True)
        log.write(s + "\n")
        log.flush()

    result = {"draws": a.draws, "seed": SEED}
    panel, ctx, captures, root = build(out)
    result["audit"] = audit(panel, ctx, root, out)

    # ---- populations ---------------------------------------------------------
    p1 = du.load_p1_rows(a.ledger)
    out(f"\n== populations")
    repro = {}
    for T, (n_exp, d_exp) in rc.P1_EXPECT.items():
        rr = [r for r in p1 if r["season"] == T]
        y = np.array([r["y"] for r in rr])
        d = rc.brier(np.array([r["m"] for r in rr]), y) - rc.brier(np.array([r["k"] for r in rr]), y)
        ok = len(rr) == n_exp and round(d, 4) == d_exp
        repro[T] = {"n": len(rr), "diff": d, "ok": ok}
        out(f"   P1 {T}: n {len(rr):,} (c-24 {n_exp:,})  Brier(model) - Brier(close) {d:+.4f} (c-24 {d_exp:+.4f})  "
            f"{'reproduces' if ok else 'DOES NOT REPRODUCE'}")
    if not all(v["ok"] for v in repro.values()):
        raise SystemExit("P1 does not reproduce c-24 - refusing")
    result["p1_reproduction"] = repro
    p2 = json.load(open(a.p2, encoding="utf-8"))["rows"]
    y2 = np.array([r["y"] for r in p2])
    d2 = rc.brier(np.array([r["m"] for r in p2]), y2) - rc.brier(np.array([r["k"] for r in p2]), y2)
    out(f"   P2: n {len(p2):,} (c-24 {rc.P2_EXPECT[0]:,})  Brier diff {d2:+.4f} (c-24 {rc.P2_EXPECT[1]:+.4f})")
    if len(p2) != rc.P2_EXPECT[0] or round(d2, 4) != rc.P2_EXPECT[1]:
        raise SystemExit("P2 does not reproduce c-24 - refusing")
    result["census"] = {"P1": attach(p1, panel, ctx, 0, out, "P1"),
                        "P2": attach(p2, panel, ctx, P2_LEAD, out, "P2")}
    result["survivors"] = {"P1": survivors(p1, out, "P1"), "P2": survivors(p2, out, "P2")}
    lead = []
    for r in p2:
        if r["f"] and r["f"]["inj_ok"]:
            cut = r["kick"] - P2_LEAD
            prior = [c for c in captures if c <= cut]
            if prior:
                lead.append((r["kick"] - prior[-1]) / 3600)
    if lead:
        out(f"   P2 injury captures: newest capture at or before the cut leads kickoff by a median "
            f"{np.median(lead):.0f}h (min {min(lead):.0f}h, max {max(lead):.0f}h)")
        result["p2_capture_lead_h"] = {"median": float(np.median(lead)), "min": float(min(lead)),
                                       "max": float(max(lead))}

    # ---- the stop rule -------------------------------------------------------
    def arm_rows(arm, pop):
        if arm == "arm2":
            return [r for r in pop if r["f"] and r["f"]["dep_ok"]]
        return [r for r in pop if r["f"] and r["f"]["inj_ok"]]
    tested = {}
    out("\n== stop rule (>= 3,000 surviving P1 rows over >= 20 season-weeks)")
    for arm in ARMS:
        rr = arm_rows(arm, p1)
        weeks = len({(r["season"], r["week"]) for r in rr})
        tested[arm] = len(rr) >= MIN_ROWS and weeks >= MIN_WEEKS
        out(f"   {arm}: {len(rr):,} rows over {weeks} weeks, seasons {sorted({r['season'] for r in rr})} -> "
            f"{'TESTED' if tested[arm] else 'NOT TESTED'}")
        result.setdefault("stop_rule", {})[arm] = {"rows": len(rr), "weeks": weeks, "tested": tested[arm]}
    if a.audit or not any(tested.values()):
        _dump(a.out_dir, result, "audit.json" if a.audit else "result.json")
        out("\nStep 0 only." if a.audit else "\nNo arm clears the stop rule - the audit is the finding.")
        return

    # ---- stat-level rows, per stat -------------------------------------------
    out("\n== Step 1 - stat-level fits (seasons before T only)")
    fits, vmr = {}, {}
    train_cache = {}
    result["fits"], result["mechanism"] = {}, {}
    for stat in VOL:
        for T in (2023, 2024, 2025, 2026):
            seasons = tuple(range(FIRST_TRAIN, T))
            priors = role_priors(panel, ctx, set(seasons), stat)
            rows = stat_rows(panel, ctx, stat, set(seasons), priors, True)
            train_cache[(stat, T)] = (rows, priors)
            vmr[(stat, T)] = training_vmr(rows)
            for arm, names in ARMS.items():
                if arm == "arm2":
                    tr = [(s, y, e, f["x"]) for s, _g, _gs, y, e, f in rows if f["dep_ok"]]
                else:
                    tr = [(s, y, e, f["x"]) for s, _g, _gs, y, e, f in rows if f["inj_ok"]]
                if len(tr) < 1000:
                    continue
                fits[(arm, stat, T)] = fit_arm(tr, names, T)
            both = [(s, y, e, f["x"]) for s, _g, _gs, y, e, f in rows if f["inj_ok"]]
            if len(both) >= 1000:
                fits[("arm13", stat, T)] = fit_arm(both, ARM1 + ARM3, T)
            alls = [(s, y, e, f["x"]) for s, _g, _gs, y, e, f in rows if f["inj_ok"] and f["dep_ok"]]
            if len(alls) >= 1000:
                fits[("arm123", stat, T)] = fit_arm(alls, ARM1 + ARM2 + ARM3, T)
        for (arm, st, T), ft in sorted(fits.items()):
            if st != stat or T not in (2024, 2025, 2026) or (arm in ("arm1", "arm3") and T == 2025):
                continue
            if arm in ("arm13", "arm123") or (T == 2026 and arm != "arm2") or (T == 2024 and arm == "arm2"):
                continue
            out(f"   {arm} {stat} T={T}: n {ft['n']:,}, v {vmr[(stat, T)]:.2f}; "
                + ", ".join(f"{nm} {b:+.3f} (se {se:.3f}, n {c:,})" for nm, b, se, c in ft["table"])
                + (f"; dropped {ft['dropped']}" if ft["dropped"] else ""))
    result["fits"] = {f"{k[0]}|{k[1]}|{k[2]}": {"n": v["n"], "table": v["table"], "dropped": v["dropped"],
                                                "vmr": vmr[(k[1], k[2])]} for k, v in fits.items()}

    # ---- descriptive: the stat-level mechanism, out of sample -----------------
    out("\n== descriptive - actual / E0 on the TEST seasons 2023-24 by newly vacated same-family share")
    for stat in VOL:
        tot = defaultdict(lambda: [0.0, 0.0, 0])
        for T in (2023, 2024):
            _rows, _pri = train_cache[(stat, T + 1)]
            for s, _g, _gs, y, e, f in _rows:
                if s != T or not f["inj_ok"]:
                    continue
                v = f["x"].get("vac_same_new", 0.0)
                b = "0" if v == 0 else "(0, .10]" if v <= .10 else "(.10, .20]" if v <= .20 else "> .20"
                tot[b][0] += y
                tot[b][1] += e
                tot[b][2] += 1
        row = {b: {"n": t[2], "ratio": t[0] / t[1]} for b, t in tot.items()}
        result["mechanism"][stat] = row
        out(f"   {stat}: " + "; ".join(f"{b} n {row[b]['n']:,} ratio {row[b]['ratio']:.3f}"
                                         for b in ("0", "(0, .10]", "(.10, .20]", "> .20") if b in row))

    # ---- applying it ----------------------------------------------------------
    def predict(pop, arm, key, vscale=1.0, lab=False):
        n_moved = 0
        for stat in VOL:
            for T in sorted({r["season"] for r in pop}):
                rr = [r for r in pop if r["stat"] == stat and r["season"] == T]
                ft = fits.get((arm, stat, T))
                if not rr:
                    continue
                m = np.ones(len(rr))
                if ft:
                    for i, r in enumerate(rr):
                        f = (r.get("f_lab") if lab else r["f"]) or {}
                        if f and f.get("modelled"):
                            m[i] = multiplier(ft["beta"], f["x"])
                v = max((vmr.get((stat, T)) or 2.0) * vscale, VMR_FLOOR)
                p = apply_multiplier([r["m"] for r in rr], m, v, [r["line"] for r in rr])
                for r, pp in zip(rr, p):
                    r[key] = float(pp)
                n_moved += int((m != 1.0).sum())
        return n_moved

    tests = []
    result["arms"] = {}
    names = {"arm1": "arm 1 injury status", "arm2": "arm 2 depth rank", "arm3": "arm 3 teammate absence"}
    for arm in ARMS:
        if not tested[arm]:
            out(f"\n== {names[arm]}: NOT TESTED (stop rule)")
            continue
        rows = arm_rows(arm, p1)
        moved = predict(rows, arm, arm)
        wk = du.week_rows(rows)
        res = {"n": len(rows), "moved": moved, "share_moved": moved / len(rows), "levels": levels(rows, arm)}
        lv = res["levels"]
        out(f"\n== {names[arm]} - P1 {sorted({r['season'] for r in rows})}: n {len(rows):,}, "
            f"{moved:,} rows carry any information ({moved / len(rows):.1%})")
        for lab in ("info", "baseline", "market"):
            out(f"   {lab:<9} Brier {lv[lab]['brier']:.4f}  MCB {lv[lab]['mcb']:.4f}  DSC {lv[lab]['dsc']:.5f}  "
                f"AUC {lv[lab]['auc']:.4f}")
        res["primary"] = score_pair(f"{arm} P1 PRIMARY", wk, arm, "week", tests, out, a.draws,
                                    ("dDSC", "dAUC", "d_wAUC", "dMCB", "dBrier", "dDSC_vs_market", "dAUC_vs_market"))
        pr = res["primary"]["dDSC"]
        res["verdict"] = verdict(pr)
        gap = lv["baseline"]["auc"] - lv["market"]["auc"]
        da = res["primary"]["dAUC"]
        res["gap"] = {"baseline_minus_market_auc": gap, "narrows": bool(da["lo"] is not None and da["lo"] > 0),
                      "share_closed": (da["est"] / -gap) if gap else None}
        failed = (res["primary"]["dMCB"]["est"] < 0) and not (pr["lo"] is not None and pr["lo"] > 0)
        res["calibration_only"] = bool(failed)
        out(f"   VERDICT: {res['verdict']}"
            + ("  - improved calibration and not resolution: FAILED this unit" if failed else ""))
        out(f"   ordering gap to the close on these rows {gap:+.4f}; dAUC {da['est']:+.5f} "
            f"-> {'NARROWS' if res['gap']['narrows'] else 'does not narrow'} "
            f"({(da['est'] / -gap):+.1%} of the gap)")
        for stat in VOL:
            sub = [r for r in wk if r["stat"] == stat]
            if sub:
                res[stat] = score_pair(f"{arm} P1 {stat}", sub, arm, "week", tests, out, a.draws, ("dDSC",))
        if arm != "arm2":
            for T in (2023, 2024):
                sub = [r for r in wk if r["season"] == T]
                if sub:
                    res[str(T)] = score_pair(f"{arm} P1 {T}", sub, arm, "week", tests, out, a.draws, ("dDSC",))
        for sc in (0.5, 2.0):
            predict(rows, arm, f"{arm}_v{sc}", vscale=sc)
            res[f"v x{sc}"] = score_pair(f"{arm} P1 v x{sc}", du.week_rows(rows), f"{arm}_v{sc}", "week",
                                         tests, out, a.draws, ("dDSC",))
        result["arms"][arm] = res

    if tested["arm1"] and tested["arm3"]:
        rows = arm_rows("arm1", p1)
        moved = predict(rows, "arm13", "arm13")
        out(f"\n== arms 1 + 3 together - P1 2023-24: {moved:,} rows moved")
        result["arms"]["arm13"] = score_pair("arm13 P1", du.week_rows(rows), "arm13", "week", tests, out,
                                             a.draws, ("dDSC", "dAUC"))

    out("\n== P2 (Kalshi mid, 2026 weeks 2-3), game-block")
    result["p2"] = {}
    for arm in ("arm1", "arm2", "arm3", "arm123"):
        rows = [r for r in p2 if r["f"] and (r["f"]["dep_ok"] if arm == "arm2" else
                                             r["f"]["inj_ok"] if arm != "arm123" else
                                             r["f"]["inj_ok"] and r["f"]["dep_ok"])]
        if len({r["game"] for r in rows}) < rc.MIN_GAMES:
            out(f"   {arm}: {len(rows)} usable rows - not scored")
            continue
        moved = predict(rows, arm, arm)
        out(f"   {arm}: n {len(rows):,} over {len({r['game'] for r in rows})} games, {moved:,} moved; "
            f"weeks {dict(Counter(r['week'] for r in rows))}")
        result["p2"][arm] = score_pair(f"{arm} P2", rows, arm, "game", tests, out, a.draws, ("dDSC", "dAUC"))
        result["p2"][arm]["moved"] = moved

    # ---- descriptive, NOT read for the verdict --------------------------------
    out("\n== descriptive, NOT read for the verdict - arm 2 on P1 2023-24, lagged-LABEL charts as the test feature")
    lab_rows = [r for r in p1 if r["season"] < DT_FIRST and r.get("f_lab") and r["f_lab"]["dep_ok"]]
    if lab_rows:
        moved = predict(lab_rows, "arm2", "arm2lab", lab=True)
        dump = []
        result["arm2_label_descriptive"] = score_pair("arm2 label P1 2023-24", du.week_rows(lab_rows), "arm2lab",
                                                      "week", dump, out, a.draws, ("dDSC", "dAUC"))
        result["arm2_label_descriptive"]["moved"] = moved

    n_ex = sum(1 for _p, _l, r in tests if r["lo"] is not None and (r["lo"] > 0 or r["hi"] < 0))
    out(f"\n== {len(tests)} registered intervals computed, {n_ex} exclude zero")
    result["tests"] = [{"pop": p, "stat": lbl, **{k: r.get(k) for k in ("est", "lo", "hi", "se", "mde", "n", "games")}}
                       for p, lbl, r in tests]
    _dump(a.out_dir, result, "result.json")
    import csv
    with open(os.path.join(a.out_dir, "predictions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pop", "season", "week", "game", "gsis", "stat", "line", "y", "baseline", "market",
                    "arm1", "arm2", "arm3", "inj_ok", "dep_ok"])
        for nm, pop in (("P1", p1), ("P2", p2)):
            for r in pop:
                ff = r["f"] or {}
                w.writerow([nm, r["season"], r["week"], r["game"], r["gsis"], r["stat"], r["line"], r["y"],
                            r["m"], r["k"], r.get("arm1", ""), r.get("arm2", ""), r.get("arm3", ""),
                            int(bool(ff.get("inj_ok"))), int(bool(ff.get("dep_ok")))])


def _dump(out_dir, result, name):
    def clean(o):
        if isinstance(o, dict):
            return {str(k): clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.bool_):
            return bool(o)
        return o
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(clean(result), f, indent=1)


if __name__ == "__main__":
    main()
