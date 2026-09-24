"""f-15: an INDEPENDENT re-derivation of a-17's vacancy measurement, plus the
audits the brief asks for. Read-only against every store.

    python -m research.f15_vacancy_verify --out <json>

WRITTEN FROM THE a-17 BRIEF'S DEFINITIONS, NOT FROM a-17'S CODE, and on a
different source where one exists, so an agreement means something:

    targets / carries   nfl_player_week (the stats release), NOT f_play_usage
    team totals         sum of nfl_player_week over the team-week, NOT the PBP
    snap share          nfl_snap_counts.offense_pct (no second source exists)
    absence             nfl_snap_counts: no row for the player in the team's
                        game, the team having a game that week

Pre-registered constants copied from the brief (15% targets / 40% snaps / last
4 appearances). Every other choice below is mine and is either the same as
a-17's documented one (so the comparison is like-for-like) or is varied on
purpose and labelled as a sensitivity.

WHAT IS MEASURED, per (measure, group, slot):
    absorption = sum over events of (slot's share in the absence game minus
                 his own last-4 baseline) / sum over events of vacated share
    placebo    = the same on games where the role player DID play
    net        = absorption - placebo, bootstrapped as one quantity

INTERVALS: three block structures on the same statistic, 2000 draws each -
team-season (a-17's), absent player-season (the brief's), and event (iid, the
wrong one, to size how much the blocks matter).
"""
import argparse
import glob
import json
import sqlite3
import sys
import zlib
from collections import Counter, defaultdict

import numpy as np

ROLE_T, ROLE_S, K = 0.15, 0.40, 4          # the brief's, pre-registered
FIRST, LAST = 2013, 2025                    # snap counts start 2013; 2026 is partial
SLOTS = 3
DRAWS = 2000
GROUPS = {"WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "HB": "RB"}
ALIAS = {"STL": "LA", "SD": "LAC", "OAK": "LV"}
MIRROR = "D:/calibrated-sports/data/raw/nflverse"
MARKET_LOG = "D:/calibrated-sports/data/market_log.db"


def ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)


def latest(asset):
    got = sorted(glob.glob("%s/*/%s" % (MIRROR, asset)))
    return got[-1] if got else None


# ---------------------------------------------------------------------------
def load(m, last_season):
    xw = dict(m.execute("SELECT pfr_id, gsis_id FROM player_xwalk "
                        "WHERE pfr_id IS NOT NULL"))
    snaps = m.execute(
        "SELECT s.season, s.week, s.game_id, s.team, s.pfr_player_id, s.position,"
        " s.offense_pct, s.offense_snaps, s.defense_snaps, s.st_snaps"
        " FROM nfl_snap_counts s JOIN (SELECT pfr_player_id p, game_id g,"
        " MAX(data_version) v FROM nfl_snap_counts WHERE season BETWEEN ? AND ?"
        " GROUP BY 1,2) l ON s.pfr_player_id=l.p AND s.game_id=l.g"
        " AND s.data_version=l.v", (FIRST, last_season)).fetchall()
    pw = m.execute(
        "SELECT w.gsis_id, w.season, w.week, w.team, w.targets, w.carries"
        " FROM nfl_player_week w JOIN (SELECT gsis_id g, season s, week k,"
        " MAX(data_version) v FROM nfl_player_week WHERE season BETWEEN ? AND ?"
        " GROUP BY 1,2,3) l ON w.gsis_id=l.g AND w.season=l.s AND w.week=l.k"
        " AND w.data_version=l.v", (FIRST, last_season)).fetchall()
    stat = {}
    team_tot = defaultdict(lambda: [0, 0])
    for g, s, w, t, tg, ca in pw:
        stat[(g, s, w)] = (tg or 0, ca or 0, t)
        team_tot[(s, w, t)][0] += tg or 0
        team_tot[(s, w, t)][1] += ca or 0
    games = m.execute("SELECT game_id, season, week, home_team, away_team,"
                      " home_score, away_score FROM nfl_games"
                      " WHERE season BETWEEN ? AND ?", (FIRST, last_season)).fetchall()
    gmeta = {}
    for gid, s, w, h, a, hs, as_ in games:
        gmeta[gid] = (h, a, hs, as_)
    return xw, snaps, stat, dict(team_tot), gmeta


def load_status():
    """(season, week, gsis) -> roster status for the week."""
    import polars as pl
    out = {}
    for y in range(FIRST, LAST + 1):
        f = latest("roster_weekly_%d.parquet" % y)
        if not f:
            continue
        d = pl.read_parquet(f, columns=["season", "week", "gsis_id", "status"])
        for s, w, g, st in d.iter_rows():
            if g:
                out[(s, w, g)] = st
    return out


def load_depth():
    """(season, week, gsis) -> best offensive depth_team. 2013-2024 only:
    from 2025 the release changes format (a dated snapshot, no week)."""
    import polars as pl
    out = {}
    for y in range(FIRST, 2025):
        f = latest("depth_charts_%d.parquet" % y)
        if not f:
            continue
        # A FULLBACK IS LISTED AT depth 1 OF ITS OWN SLOT. Ranked with the
        # backs on the raw label it outranks the RB2 who inherits the work,
        # so a fullback-only listing is pushed behind every RB listing.
        d = (pl.read_parquet(f, columns=["season", "week", "gsis_id",
                                         "depth_team", "formation", "position"])
             .filter((pl.col("formation") == "Offense")
                     & pl.col("position").is_in(["WR", "TE", "RB", "FB", "HB"]))
             .with_columns(pl.col("depth_team").cast(pl.Int32, strict=False))
             .with_columns(pl.when(pl.col("position") == "FB")
                           .then(pl.col("depth_team") + 10)
                           .otherwise(pl.col("depth_team")).alias("depth_team")))
        for s, w, g, dt in (d.group_by(["season", "week", "gsis_id"])
                            .agg(pl.col("depth_team").min()).iter_rows()):
            if g is not None and dt is not None:
                out[(s, w, g)] = dt
    return out


# ---------------------------------------------------------------------------
def build(xw, snaps, stat, team_tot, gmeta, status, depth):
    counts = Counter()
    # a team-game is (season, week, team). One game per team-week.
    at = defaultdict(dict)          # (s, team, week) -> {pfr: rec}
    anyrow = set()                  # (pfr, s, w) any snap row anywhere
    anyrow_team = {}
    tg_games = defaultdict(set)
    gid_of = {}
    for s, w, gid, team, pfr, pos, pct, osn, dsn, stn in snaps:
        team = ALIAS.get(team, team)
        anyrow.add((pfr, s, w))
        anyrow_team[(pfr, s, w)] = team
        tg_games[(s, team)].add(w)
        gid_of[(s, team, w)] = gid
        grp = GROUPS.get((pos or "").split("/")[0].strip().upper())
        g = xw.get(pfr)
        tg, ca, _t = stat.get((g, s, w), (0, 0, None)) if g else (0, 0, None)
        tot = team_tot.get((s, w, team))
        rec = dict(pfr=pfr, gsis=g, grp=grp, snap=pct or 0.0, tg=tg, ca=ca,
                   tshare=(tg / tot[0]) if tot and tot[0] else None,
                   cshare=(ca / tot[1]) if tot and tot[1] else None)
        at[(s, team, w)][pfr] = rec
    # a team-game whose roster rows are truncated manufactures absences.
    sizes = Counter(len(v) for v in at.values())
    counts["team-games in snap counts"] = len(at)
    counts["team-games with < 30 snap rows (suspect truncation)"] = \
        sum(c for n, c in sizes.items() if n < 30)

    def share(r, meas):
        return {"targets": r["tshare"], "snaps": r["snap"],
                "carries": r["cshare"]}[meas]

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    absences, placebos = [], []
    for (s, team), weeks in tg_games.items():
        weeks = sorted(weeks)
        hist = defaultdict(list)
        for i, w in enumerate(weeks):
            here = at[(s, team, w)]
            prev = at[(s, team, weeks[i - 1])] if i else {}
            cands, plac = [], []
            for pfr, past in hist.items():
                if len(past) < K:
                    continue
                last = past[-K:]
                bt = mean(r["tshare"] for r in last) or 0.0
                bs = mean(r["snap"] for r in last) or 0.0
                if not (bt >= ROLE_T or bs >= ROLE_S):
                    continue
                if pfr not in prev:
                    continue
                grp = Counter(r["grp"] for r in last).most_common(1)[0][0]
                if grp is None:
                    continue                    # not a skill-position role
                ev = dict(s=s, team=team, w=w, i=i, pfr=pfr, grp=grp,
                          gsis=last[-1]["gsis"], bt=bt, bs=bs, last=last,
                          prev_snap=prev[pfr]["snap"])
                if pfr in here:
                    plac.append(ev)
                    continue
                counts["role player with no row in the team-game"] += 1
                if (pfr, s, w) in anyrow:
                    counts["  has a row for another team that week: dropped"] += 1
                    continue
                cands.append(ev)
            if len(cands) > 1:
                counts["  team-game with 2+ absences: dropped"] += len(cands)
            elif cands:
                absences.append(cands[0])
            if not cands:
                placebos.extend(plac)
            for pfr, rec in here.items():
                hist[pfr].append(rec)

    # teammates
    for e in absences + placebos:
      s, team, w, i = e["s"], e["team"], e["w"], e["i"]
      weeks = sorted(tg_games[(s, team)])
      here = at[(s, team, w)]
      # "m": baselines over the last K appearances before the absence game.
      # "m_lag": the SAME, skipping the immediately previous team game - where
      # a player hurt mid-game has already had part of his work absorbed, so
      # both his vacated share and his teammates' baselines are contaminated.
      for mkey, upto in (("m", weeks[:i]), ("m_lag", weeks[:max(i - 1, 0)])):
        prior = defaultdict(list)
        for pw in upto:
            for pfr, rec in at[(s, team, pw)].items():
                prior[pfr].append(rec)
        vlast = prior.get(e["pfr"], [])[-K:]
        e[mkey] = {}
        if len(vlast) < 1:
            continue
        for meas in ("targets", "snaps", "carries"):
            vac = mean(share(r, meas) for r in vlast)
            if not vac:
                continue
            mates = []
            bad = False
            for pfr, rec in here.items():
                if pfr == e["pfr"] or rec["grp"] is None:
                    continue
                now = share(rec, meas)
                if now is None:
                    bad = True
                    break
                last = prior.get(pfr, [])[-K:]
                base = mean(share(r, meas) for r in last) or 0.0
                bsnap = mean(r["snap"] for r in last) or 0.0
                dep = depth.get((s, w, rec["gsis"]), 9)
                mates.append((rec["grp"], base, bsnap, pfr, now - base, dep))
            if bad:
                continue
            same = [x for x in mates if x[0] == e["grp"]]
            own = sorted(same, key=lambda x: (-x[1], -x[2], x[3]))
            lab = sorted(same, key=lambda x: (x[5], -x[1], -x[2], x[3]))
            e[mkey][meas] = dict(
                vac=vac,
                own=[own[k][4] if k < len(own) else 0.0 for k in range(SLOTS)],
                lab=[lab[k][4] if k < len(lab) else 0.0 for k in range(SLOTS)],
                group=sum(x[4] for x in same),
                other=sum(x[4] for x in mates if x[0] != e["grp"]))
      # context for the splits
      gid = gid_of.get((s, team, w))
      h, a, hs, as_ = gmeta.get(gid, (None, None, None, None))
      e["margin"] = (abs(hs - as_) if hs is not None and as_ is not None
                     else None)
      e["status"] = status.get((s, w, e["gsis"]))
      e["exit_prev"] = e["prev_snap"] < 0.5 * e["bs"] if e["bs"] else False
      # QB change: the snap leader at QB differs from the previous game's
      qb_now = max(((r["snap"], p) for p, r in here.items()
                    if _qb(r, snaps_pos)), default=(0, None))[1]
      pw_ = weeks[i - 1] if i else None
      qb_prev = (max(((r["snap"], p) for p, r in at[(s, team, pw_)].items()
                      if _qb(r, snaps_pos)), default=(0, None))[1]
                 if pw_ is not None else None)
      e["qb_change"] = qb_now != qb_prev
      # the "returning" check on a second source: a stat row in the game
      st = stat.get((e["gsis"], s, w)) if e.get("gsis") else None
      e["stat_row_same_week"] = st is not None
      e["stat_row_touch"] = bool(st and (st[0] or st[1]))
      e.pop("last")
    return absences, placebos, counts


snaps_pos = {}


def _qb(rec, _):
    return snaps_pos.get(rec["pfr"]) == "QB"


# ---------------------------------------------------------------------------
def vectors(evs, meas, grp, rank="own", mkey="m"):
    """[(event, vector)] vector = [vac, slot1..3, group, other]."""
    out = []
    for e in evs:
        if e["grp"] != grp or meas not in e[mkey]:
            continue
        x = e[mkey][meas]
        out.append((e, np.array([x["vac"]] + x[rank] + [x["group"], x["other"]])))
    return out


BLOCKS = {
    "team_season": lambda e: (e["s"], e["team"]),
    "player_season": lambda e: (e["s"], e["pfr"]),
    "event": lambda e: (e["s"], e["team"], e["w"], e["pfr"]),
}


def ratio_ci(ab, pl=None, block="team_season", seed_key=""):
    """Ratio-of-sums per column (cols 1.. over col 0). With `pl`, the net
    absorption minus placebo, resampling the ABSENCE blocks jointly."""
    key = BLOCKS[block]
    A = defaultdict(lambda: np.zeros(6))
    for e, v in ab:
        A[key(e)] += v
    P = defaultdict(lambda: np.zeros(6))
    if pl is not None:
        # placebo matched to the absence population's TEAM-SEASONS, keyed to
        # the absence blocks so both halves move together
        tsab = defaultdict(set)
        for e, _v in ab:
            tsab[(e["s"], e["team"])].add(key(e))
        for e, v in pl:
            ks = tsab.get((e["s"], e["team"]))
            if not ks:
                continue
            # split a team-season's placebo evenly across its absence blocks
            for k in ks:
                P[k] += v / len(ks)
    keys = sorted(A)
    Am = np.array([A[k] for k in keys])
    Pm = np.array([P[k] for k in keys]) if pl is not None else None

    def stat(w):
        a = (w[:, None] * Am).sum(0)
        r = a[1:] / a[0]
        if Pm is not None:
            p = (w[:, None] * Pm).sum(0)
            r = r - p[1:] / p[0] if p[0] else r * np.nan
        return r
    n = len(keys)
    pt = stat(np.ones(n))
    rng = np.random.default_rng(zlib.crc32(("%s|%s" % (seed_key, block)).encode()))
    W = rng.multinomial(n, np.full(n, 1 / n), size=DRAWS).astype(float)
    bs = np.array([stat(w) for w in W])
    lo = np.nanpercentile(bs, 2.5, axis=0)
    hi = np.nanpercentile(bs, 97.5, axis=0)
    return pt, lo, hi, n, len(ab)


NAMES = ["slot1", "slot2", "slot3", "group", "other"]


def contrast(evA, evB, meas, grp, mkey="m", seed_key=""):
    """Raw absorption of split A minus split B, per column, as ONE quantity:
    team-season blocks resampled jointly, each block carrying both halves.
    Never the difference of two separately quoted intervals (brief 018)."""
    A = defaultdict(lambda: np.zeros(12))
    for half, evs in ((0, evA), (6, evB)):
        for e, v in vectors(evs, meas, grp, "own", mkey):
            A[(e["s"], e["team"])][half:half + 6] += v
    keys = sorted(A)
    M = np.array([A[k] for k in keys])
    if len(keys) < 5 or M[:, 0].sum() == 0 or M[:, 6].sum() == 0:
        return None

    def stat(w):
        a = (w[:, None] * M).sum(0)
        return a[1:6] / a[0] - a[7:12] / a[6]
    n = len(keys)
    pt = stat(np.ones(n))
    rng = np.random.default_rng(zlib.crc32(seed_key.encode()))
    W = rng.multinomial(n, np.full(n, 1 / n), size=DRAWS).astype(float)
    bs = np.array([stat(w) for w in W])
    lo, hi = np.nanpercentile(bs, 2.5, 0), np.nanpercentile(bs, 97.5, 0)
    return {nm: [round(float(pt[j]), 4), round(float(lo[j]), 4),
                 round(float(hi[j]), 4)] for j, nm in enumerate(NAMES)} |         {"n_blocks": n, "events_A": len(vectors(evA, meas, grp, "own", mkey)),
         "events_B": len(vectors(evB, meas, grp, "own", mkey))}


def table(ab_all, pl_all, meas, grp, rank="own", block="team_season",
          net=True, mkey="m"):
    ab = vectors(ab_all, meas, grp, rank, mkey)
    pl = vectors(pl_all, meas, grp, rank, mkey) if net else None
    if len(ab) < 5:
        return None
    raw = ratio_ci(ab, None, block, "%s|%s|%s|raw" % (meas, grp, rank))
    res = {"n_blocks": raw[3], "events": raw[4],
           "vacated_mean": float(np.mean([v[0] for _e, v in ab]))}
    for j, nm in enumerate(NAMES):
        res[nm] = [round(float(raw[0][j]), 4), round(float(raw[1][j]), 4),
                   round(float(raw[2][j]), 4)]
    if net:
        nt = ratio_ci(ab, pl, block, "%s|%s|%s|net" % (meas, grp, rank))
        for j, nm in enumerate(NAMES):
            res[nm + "|net"] = [round(float(nt[0][j]), 4),
                                round(float(nt[1][j]), 4),
                                round(float(nt[2][j]), 4)]
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    m = ro(MARKET_LOG)
    for pfr, pos in m.execute("SELECT pfr_player_id, position FROM nfl_snap_counts"
                              " WHERE season >= ?", (FIRST,)):
        if pos == "QB":
            snaps_pos[pfr] = "QB"
    xw, snaps, stat, team_tot, gmeta = load(m, LAST)
    status = load_status()
    depth = load_depth()
    ab, pl, counts = build(xw, snaps, stat, team_tot, gmeta, status, depth)
    counts["absence events kept"] = len(ab)
    counts["placebo events"] = len(pl)
    out = {"counts": dict(counts), "cells": {}}
    P = lambda *x: print(*x, flush=True)
    for k, v in counts.items():
        P("  %-58s %7d" % (k, v))

    # returning weeks, on a SECOND source
    out["returning"] = {
        "absences": len(ab),
        "with_stat_row_same_week": sum(e["stat_row_same_week"] for e in ab),
        "with_target_or_carry_same_week": sum(e["stat_row_touch"] for e in ab)}
    P("returning check:", out["returning"])

    specs = [("targets", g) for g in ("WR", "TE", "RB")] + \
            [("snaps", g) for g in ("WR", "TE", "RB")] + [("carries", "RB")]
    subsets = {
        "all": lambda e: True,
        "no_blowout21": lambda e: e["margin"] is not None and e["margin"] < 21,
        "no_blowout14": lambda e: e["margin"] is not None and e["margin"] < 14,
        "no_qb_change": lambda e: not e["qb_change"],
        "clean(no21,noQB)": lambda e: (e["margin"] is not None and e["margin"] < 21
                                       and not e["qb_change"]),
        "status_RES": lambda e: e["status"] == "RES",
        "status_INA_ACT": lambda e: e["status"] in ("INA", "ACT"),
        "status_other": lambda e: e["status"] not in ("RES", "INA", "ACT"),
        "exit_prev_game": lambda e: e["exit_prev"],
        "no_exit_prev_game": lambda e: not e["exit_prev"],
        "depth_era_2013_2024": lambda e: e["s"] <= 2024,
        # the lagged-baseline variants: key names end in _lag
        "all_lag": lambda e: True,
        "exit_prev_game_lag": lambda e: e["exit_prev"],
        "no_exit_prev_game_lag": lambda e: not e["exit_prev"],
    }
    out["split_sizes"] = {k: sum(1 for e in ab if f(e)) for k, f in subsets.items()}
    out["status_counts"] = dict(Counter(str(e["status"]) for e in ab))
    P("split sizes", out["split_sizes"])
    P("status", out["status_counts"])
    for meas, grp in specs:
        for sub, f in subsets.items():
            abs_ = [e for e in ab if f(e)]
            # placebos are not split by absence attributes (they have none);
            # for the blowout/QB subsets the same filter applies to them.
            pls = [e for e in pl if f(e)] if sub.startswith(("no_", "clean")) \
                else pl
            ranks = ("own", "lab") if sub in ("all", "depth_era_2013_2024") \
                else ("own",)
            for rank in ranks:
                blocks = ("team_season", "player_season", "event") \
                    if sub == "all" and rank == "own" else ("team_season",)
                for b in blocks:
                    r = table(abs_, pls, meas, grp, rank, b,
                              mkey="m_lag" if sub.endswith("_lag") else "m")
                    key = "%s|%s|%s|%s|%s" % (meas, grp, sub, rank, b)
                    out["cells"][key] = r
                    if r:
                        P("%-58s n=%4d ev=%5d vac=%.3f s1 %s  s1net %s  grp %s"
                          % (key, r["n_blocks"], r["events"], r["vacated_mean"],
                             r["slot1"], r["slot1|net"], r["group"]))
    # THE SELECTION CONTRASTS, each bootstrapped as one quantity
    out["contrasts"] = {}
    pairs = {
        "RES_minus_notRES": (lambda e: e["status"] == "RES",
                             lambda e: e["status"] != "RES", "m"),
        "exit_minus_noexit_lag": (lambda e: e["exit_prev"],
                                  lambda e: not e["exit_prev"], "m_lag"),
        "exit_minus_noexit": (lambda e: e["exit_prev"],
                              lambda e: not e["exit_prev"], "m"),
        "blowout21_minus_rest": (
            lambda e: e["margin"] is not None and e["margin"] >= 21,
            lambda e: e["margin"] is not None and e["margin"] < 21, "m"),
        "qbchange_minus_rest": (lambda e: e["qb_change"],
                                lambda e: not e["qb_change"], "m"),
    }
    for meas, grp in specs:
        for name, (fa, fb, mk) in pairs.items():
            r = contrast([e for e in ab if fa(e)], [e for e in ab if fb(e)],
                         meas, grp, mk, "%s|%s|%s" % (meas, grp, name))
            key = "%s|%s|%s" % (meas, grp, name)
            out["contrasts"][key] = r
            if r:
                P("%-40s nA=%4d nB=%4d  slot1 %s  group %s"
                  % (key, r["events_A"], r["events_B"], r["slot1"], r["group"]))
    # RANKING: own-share slots minus depth-chart-label slots on the SAME
    # events (2013-2024, where a weekly depth chart exists), one quantity.
    out["rank_contrast"] = {}
    for meas, grp in specs:
        evs = [e for e in ab if e["s"] <= 2024]
        A = defaultdict(lambda: np.zeros(8))
        for e, v in vectors(evs, meas, grp, "own"):
            A[(e["s"], e["team"])][0:4] += v[0:4]
        for e, v in vectors(evs, meas, grp, "lab"):
            A[(e["s"], e["team"])][4:8] += v[0:4]
        keys = sorted(A)
        M = np.array([A[k] for k in keys])
        n = len(keys)
        f = lambda w: ((w[:, None] * M).sum(0)[1:4] / (w[:, None] * M).sum(0)[0]
                       - (w[:, None] * M).sum(0)[5:8] / (w[:, None] * M).sum(0)[4])
        pt = f(np.ones(n))
        rng = np.random.default_rng(zlib.crc32(("rank|%s|%s" % (meas, grp)).encode()))
        W = rng.multinomial(n, np.full(n, 1 / n), size=DRAWS).astype(float)
        bs = np.array([f(w) for w in W])
        lo, hi = np.percentile(bs, 2.5, 0), np.percentile(bs, 97.5, 0)
        r = {"slot%d" % (j + 1): [round(float(pt[j]), 4), round(float(lo[j]), 4),
                                  round(float(hi[j]), 4)] for j in range(3)}
        r["n_blocks"] = n
        # how often the two rankings name a different slot-1 player at all
        out["rank_contrast"]["%s|%s" % (meas, grp)] = r
        P("rank own-lab %-12s n=%d slot1 %s slot2 %s" % (meas + "|" + grp, n,
                                                        r["slot1"], r["slot2"]))
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    if not ab:
        raise SystemExit("zero absences - a result of nothing is a failure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
