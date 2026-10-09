"""The college game forecast: `models.game`, pointed at FBS (c-39).

The object and the win-probability link are `models.game`'s, imported - there
is one `GameForecast` and one `win_prob`. What is college's own is the WALK,
because four things differ (docs/C39-cfb-game-model-preregistration.md):

  order     (season, start_ts, game_id). A postseason row carries week = 1, so
            the NFL walk's (season, week) order would put every bowl first.
  neutral   home advantage is 0 on a neutral site.
  blowouts  the margin multiplier takes a fitted cap and a fitted damping `a`:
                ln(min(|margin|, cap) + 1) * a / (0.001 * elo_diff_winner + a)
            `a` 2.2 with no cap IS `models.game.elo_delta`.
  turnover  the preseason regression goes toward
                conf_w * conference_mean + (1 - conf_w) * MEAN
            and a team first seen after the first season enters at MEAN + entry.

No constant here is carried from the NFL. `run` is the scalar walk; `grid_fit`
is the same walk over a whole parameter grid at once and returns per-season
log-loss sums, which is all the walk-forward fit reads.

A grid point whose multiplier denominator reaches <= 0 has no defined update.
Whether that excludes it from a fit is decided AS OF the season being fitted
(c-49, docs/C49-grid-fit-asof-preregistration.md): `grid_fit` records the
season each point first went bad and `best_params` refuses a point for season
T only if that season is before T. One mask over the whole walk let a game of
2026 remove a point from 2005's choice (f-32).

Which games enter is the CALLER's rule (FBS against FBS only, in c-39): this
module walks the list it is given. A game dict carries game_id, season,
start_ts, home, away, home_conf, away_conf, neutral, home_score, away_score,
and optionally `fit` (False keeps a game out of the log-loss sums while it
still updates ratings - sensitivity S1's FBS-FCS games).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from models.game import MEAN, win_prob

P_CLIP = 1e-9          # models.season.log_loss's clip
NEVER = 9999           # first_bad of a grid point whose denominator never reached <= 0


@dataclass(frozen=True)
class CfbParams:
    k: float
    hfa: float
    regress: float
    a: float | None = 2.2          # None: no rating-gap damping (factor 1)
    cap: float | None = None       # None: no blowout cap
    conf_w: float = 0.0
    entry: float = 0.0

    def as_dict(self):
        return {"k": self.k, "hfa": self.hfa, "regress": self.regress, "a": self.a,
                "cap": self.cap, "conf_w": self.conf_w, "entry": self.entry}


def completed(g) -> bool:
    """Both scores and unequal: college has no ties, an equal score is a non-game."""
    return (g["home_score"] is not None and g["away_score"] is not None
            and g["home_score"] != g["away_score"])


def order(games):
    return sorted((i for i, g in enumerate(games) if completed(g)),
                  key=lambda i: (games[i]["season"], games[i]["start_ts"], games[i]["game_id"]))


def conferences(games):
    """{(season, team): conference} from the season's own schedule rows, played
    or not - membership is known before week 1. The commonest label wins."""
    seen: dict = {}
    for g in games:
        for t, c in ((g["home"], g["home_conf"]), (g["away"], g["away_conf"])):
            d = seen.setdefault((g["season"], t), {})
            d[c] = d.get(c, 0) + 1
    return {k: sorted(d.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0] for k, d in seen.items()}


def multiplier(margin, wdiff, params: CfbParams) -> float:
    m = abs(margin) if params.cap is None else min(abs(margin), params.cap)
    if params.a is None:
        return math.log(m + 1.0)
    den = 0.001 * wdiff + params.a
    if den <= 0:
        raise ValueError("multiplier denominator %.3f <= 0 (a %.1f, winner diff %.0f)"
                         % (den, params.a, wdiff))
    return math.log(m + 1.0) * params.a / den


def _conf_means(r, conf, season):
    tot: dict = {}
    for t, v in r.items():
        c = conf.get((season, t))
        if c is not None:
            s = tot.setdefault(c, [0.0, 0])
            s[0] += v
            s[1] += 1
    return {c: s[0] / s[1] for c, s in tot.items()}


def run(games, params: CfbParams, mov: bool = True, hfa_on_neutral: bool = False,
        snapshot_seasons=(), undefined: list | None = None):
    """Walk every completed game in (season, start_ts, game_id) order.

    Returns (pre, ratings, snaps): pre is [(index, p_home)] computed BEFORE the
    game updated anything; ratings the final {team: rating}; snaps
    {season: {team: rating}} taken after that season's preseason regression.
    `hfa_on_neutral` exists only for arm N (the NFL walk has no neutral site).

    A game whose multiplier denominator is <= 0 raises - unless `undefined` is
    a list, in which case the game takes `grid_fit`'s substitution (denominator
    1.0) and its (index, season) is appended. A point chosen as of season T can
    go bad in a later game, and that game must not take T's forecasts with it.
    """
    conf = conferences(games)
    r: dict = {}
    pre, snaps = [], {}
    first = season = None
    for i in order(games):
        g = games[i]
        s = g["season"]
        if s != season:
            if season is not None:
                cm = _conf_means(r, conf, s)
                for t in list(r):
                    c = conf.get((s, t))
                    target = MEAN if c is None else (params.conf_w * cm[c]
                                                     + (1.0 - params.conf_w) * MEAN)
                    r[t] = target + (1.0 - params.regress) * (r[t] - target)
            else:
                first = s
            season = s
            if s in snapshot_seasons:
                snaps[s] = dict(r)
        start = MEAN if s == first else MEAN + params.entry
        h, a = g["home"], g["away"]
        rh, ra = r.setdefault(h, start), r.setdefault(a, start)
        neutral = 0 if hfa_on_neutral else int(bool(g["neutral"]))
        diff = rh - ra + params.hfa * (1 - neutral)
        p = float(win_prob(diff))
        pre.append((i, p))
        margin = g["home_score"] - g["away_score"]
        result = 1.0 if margin > 0 else 0.0
        wdiff = diff if margin > 0 else -diff
        if (mov and undefined is not None and params.a is not None
                and 0.001 * wdiff + params.a <= 0):
            undefined.append((i, s))
            m = abs(margin) if params.cap is None else min(abs(margin), params.cap)
            mult = math.log(m + 1.0) * params.a
        else:
            mult = multiplier(margin, wdiff, params) if mov else 1.0
        delta = params.k * mult * (result - p)
        r[h] = rh + delta
        r[a] = ra - delta
    return pre, r, snaps


def log_loss(p, y):
    p = min(max(p, P_CLIP), 1 - P_CLIP)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def grid_fit(games, params_list, mov: bool = True):
    """`run` over every grid point at once.

    Returns (seasons, sums, counts, first_bad): sums[s, j] is grid point j's
    summed log loss over the `fit` games of seasons[s]; counts[s] the games;
    first_bad[j] the season of the first game in which point j's multiplier
    denominator reached <= 0, or NEVER. Nothing is masked here: from that game
    on the point walks with the denominator replaced by 1.0, and `best_params`
    decides, as of the season it fits, whether the point may be chosen.
    """
    P = len(params_list)
    k = np.array([p.k for p in params_list])
    hfa = np.array([p.hfa for p in params_list])
    reg = np.array([p.regress for p in params_list])
    a_none = np.array([p.a is None for p in params_list])
    a = np.array([1.0 if p.a is None else p.a for p in params_list])
    cap = np.array([np.inf if p.cap is None else p.cap for p in params_list])
    cw = np.array([p.conf_w for p in params_list])
    entry = np.array([p.entry for p in params_list])
    caps = np.unique(cap)
    cap_idx = np.searchsorted(caps, cap)
    conf = conferences(games)
    ordr = order(games)
    seasons = sorted({games[i]["season"] for i in ordr})
    sidx = {s: j for j, s in enumerate(seasons)}
    sums = np.zeros((len(seasons), P))
    counts = np.zeros(len(seasons), dtype=int)
    first_bad = np.full(P, NEVER, dtype=np.int32)
    r: dict = {}
    first = season = None
    c400 = math.log(10.0) / 400.0
    for i in ordr:
        g = games[i]
        s = g["season"]
        if s != season:
            if season is not None:
                tot: dict = {}
                for t, v in r.items():
                    c = conf.get((s, t))
                    if c is not None:
                        e = tot.setdefault(c, [np.zeros(P), 0])
                        e[0] = e[0] + v
                        e[1] += 1
                for t in list(r):
                    c = conf.get((s, t))
                    if c is None:
                        target = MEAN
                    else:
                        target = cw * (tot[c][0] / tot[c][1]) + (1.0 - cw) * MEAN
                    r[t] = target + (1.0 - reg) * (r[t] - target)
            else:
                first = s
            season = s
        h, aw = g["home"], g["away"]
        for t in (h, aw):
            if t not in r:
                r[t] = np.full(P, MEAN) if s == first else MEAN + entry
        rh, ra = r[h], r[aw]
        diff = rh - ra + (0.0 if g["neutral"] else hfa)
        p = 1.0 / (1.0 + np.exp(-diff * c400))
        margin = g["home_score"] - g["away_score"]
        win = margin > 0
        if g.get("fit", True):
            q = np.clip(p if win else 1.0 - p, P_CLIP, 1 - P_CLIP)
            sums[sidx[s]] -= np.log(q)
            counts[sidx[s]] += 1
        if mov:
            logm = np.log(np.minimum(abs(margin), caps) + 1.0)[cap_idx]
            den = 0.001 * (diff if win else -diff) + a
            first_bad[(~a_none) & (den <= 0) & (first_bad == NEVER)] = s
            mult = np.where(a_none, logm, logm * a / np.where(den <= 0, 1.0, den))
        else:
            mult = 1.0
        delta = k * mult * ((1.0 if win else 0.0) - p)
        r[h] = rh + delta
        r[aw] = ra - delta
    return seasons, sums, counts, first_bad


def best_params(params_list, seasons, sums, counts, first_bad, fit_from, year):
    """The first grid point with the lowest mean log loss on fit_from..year-1
    (jobs.season_model.best_params's rule), among the points that had not gone
    bad in any game of a season before `year`. -> (params, mean log loss, games)."""
    m = np.array([fit_from <= s < year for s in seasons])
    n = int(counts[m].sum())
    if not n:
        raise ValueError("no fit games in %d..%d" % (fit_from, year - 1))
    mean = sums[m].sum(axis=0) / n
    mean = np.where(np.isnan(mean) | (np.asarray(first_bad) < year), np.inf, mean)
    j = int(np.argmin(mean))
    if not np.isfinite(mean[j]):
        raise ValueError("no grid point is defined through %d" % (year - 1))
    return params_list[j], float(mean[j]), n
