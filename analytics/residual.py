"""6/7. The opportunity residual: production minus what the volume bought.

    python -m analytics.residual --publish
    python -m analytics.residual --publish --stat receptions
    python -m analytics.residual --show

THE SITE'S CENTRAL CLAIM, AS A METRIC. The research record says "usage carries
the signal, efficiency is noise". `analytics.stability` measures the first half
of that. This module measures the second half, and publishes the two on one
axis so a page can put them side by side.

THE FIT. Per season, per position group, cross-sectional over player-weeks:

    production ~ opportunity            (OLS with an intercept)

one fit per production stat, with the pairing declared in `PAIRINGS` below - one
table a reader can check. The FITTED value is what the player's volume bought;
the RESIDUAL (actual - fitted) is what he did with it. Summed per player-season,
a large positive residual means he produced more than his volume bought. That is
the efficiency the record says does not persist.

SIX METRICS PER STAT.

    opportunity_residual.<stat>              player  slice season|group
        the summed residual, interval from resampling his games. It SCALES
        WITH GAMES PLAYED (f-14: |sum| against games r 0.38-0.40), so nothing
        bands or ranks on it; the envelope carries the measured correlation
    opportunity_residual.per_game.<stat>     player  slice season|group
        the same residual per game played, from the same resamples. This is
        what bands are cut on and what a page should rank or band on (a-21)
    opportunity_residual.asof.<stat>         player  slice season|group|wNN
        the per-game residual through REG week NN from a line fitted on weeks
        1..NN of that season ONLY - the value a call made after week NN could
        have read (f-13 section 2). SEPARATE from the full-season metrics,
        which stay the published descriptive ones and know later weeks. f-14
        measured the full-season look-ahead as small (residual corr 0.997+,
        lag-1 persistence moved <= 0.036), which is why the descriptive
        metric is kept; small is not zero, which is why grading needs this
    opportunity_residual.fit.<stat>          league  slice season|group
        R^2 of each fit, interval from resampling players. ALSO summarised in
        the range_note of every metric of the stat, because a residual whose
        fit explains 10% of the variance means something different from one
        whose fit explains 80%, and a reader must see which before reading it
    opportunity_residual.bands.<stat>        league  slice season|group|qNN
        band edges: percentiles of the PER-GAME residual across the published
        player-seasons. The page renders BANDS ("residual below -X"), so this
        publishes the edges and the values, never an ordering (LEDGER
        2026-09-19: bands, not ranked lists)
    opportunity_residual.persistence.<stat>  league  slice group|design|quantity
        THE POINT. Correlation of the per-game RESIDUAL between two samples of
        the same player, beside the same correlation for the per-game FITTED
        value (the opportunity, in the stat's own units), and their GAP - all
        three from ONE set of resamples, so the gap is bootstrapped as one
        quantity rather than differenced from two separately quoted intervals
        (brief 018). Two designs:
            lag1_season  season T against season T+1
            split_half   odd weeks against even weeks, same season
        The split-half r is NOT Spearman-Brown corrected: it is the reliability
        of half a season, stated as such.
        BOTH DESIGNS ARE CONDITIONAL ON SURVIVAL: a pair needs the player to
        stay (>= MIN_GAMES in both seasons). f-14 measured lag-1 r rising with
        the requirement, and the envelope carries r at 1 / 5 / 12 games and
        the leavers' mean residual, computed each run (`survivorship`).

WHAT THIS IS NOT THE SAME AS. CLAUDE.md's "player residuals don't persist
(r ~ 0.09)" comes from `research/externals.py`, and ITS residual is actual minus
the player's OWN prior expanding mean - a usage surprise, not an efficiency. It
is a different quantity measured on a different population (2016-2024, >=8
games, receptions / receiving yards / carries). f-14 re-derived it (0.055 /
0.070 / -0.045, intervals spanning zero) and re-derived THIS metric (lag-1 r
0.09 to 0.49, 25 of 26 intervals excluding zero). So "efficiency is noise,
r ~ 0.09" is FALSE for this metric and must not be quoted for it. What this
metric supports is RELATIVE: efficiency persists less than opportunity, which is
the `gap` quantity of the persistence metric - published with its own interval
so a page cites it rather than typing a sentence.

KNEELS ARE NOT CARRIES. `kneel_plays` removes every REG `qb_kneel` from both
sides of the rushing fits (a-21, from f-14). See its docstring.

NULL IS NEVER ZERO. A player-week missing any input of its fit - a completion
with no recorded yards, a target with no air yards, a carry with no yards - drops
out of the fit, the residual AND the persistence pairs together, and the count
dropped is printed every run. One exception, which is a definition rather than
a fill: an incomplete pass gains zero receiving yards. nflverse writes
`receiving_yards` NULL on an incompletion because the column is the yardage of a
catch; summing a player-week's receiving yards over its completions is the
stat's own definition, and a completion whose yardage is NULL still drops.

POSITION GROUP IS NOT AS-OF. It comes from `nfl_player_week.position`, which is
the player's CURRENT nflverse position stamped on every season: 0 of 3,831
skill-position players carry more than one position across 28 seasons
(measured 2026-09-24). A converted receiver's early seasons fit with his current
group. The range_note says so on the envelope.

RED-ZONE LOOKS ARE NOT PAIRED. The brief names them as an opportunity for TDs.
They live in `nfl_pbp_looks`, which a-15 stages and the live store does not yet
carry. TDs are paired with raw targets / carries instead; add the red-zone arm
when the table exists rather than approximating it.

INTERVALS. Every value carries its interval and n (independent units):
- a player's summed, per-game and as-of residual resample HIS GAMES (n =
  games). It is conditional
  on the fitted line - fit uncertainty is not propagated, and with hundreds of
  players per fit it is small beside a player's game-to-game noise. The
  metric's `unit` says so, where a page will read it.
- R^2, band edges and persistence resample PLAYERS (n = players). A player's
  weeks share a player and his seasons share a career.
Every subject gets independent draws (seeded from its own key), because these
are exactly the numbers a reader will put side by side.
"""
import argparse
import math
import re
import statistics
import sys
import zlib

from analytics import metrics, paths

MIN_GAMES = 5            # per player-season, to publish a residual or pair it
MIN_HALF = 3             # games in each half for the split-half design
MIN_PLAYERS = 30         # per fit, and per persistence cell
DRAWS = 2000
QUANTILES = (10, 25, 50, 75, 90)
SEED = 20260924

GROUP = {"WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "HB": "RB", "QB": "QB"}

# THE PAIRING TABLE. One row per production stat. `source` is where both sides
# of the fit are read from - never mixed within one fit, so a definitional
# difference between two feeds cannot masquerade as a residual.
#
#   stat              source        production          opportunity            groups
PAIRINGS = {
    "receiving_yards": ("spine",   "receiving yards",  ("targets", "air_yards"), ("WR", "TE", "RB")),
    "receptions":      ("spine",   "receptions",       ("targets",),             ("WR", "TE", "RB")),
    "rushing_yards":   ("spine",   "rushing yards",    ("carries",),             ("RB", "QB")),
    "receiving_tds":   ("weekly",  "receiving TDs",    ("targets",),             ("WR", "TE", "RB")),
    "rushing_tds":     ("weekly",  "rushing TDs",      ("carries",),             ("RB", "QB")),
}

# What each pairing's range is derived from. Position comes from weekly stats
# for every stat, so it is required everywhere.
_POS = ("weekly_stats", "position", "")
REQUIRES = {
    "receiving_yards": (("pbp", "receiver_player_id", "incomplete_pass"),
                        ("pbp", "receiver_player_id", "complete_pass"),
                        ("pbp", "air_yards", "pass_attempt"), _POS),
    "receptions": (("pbp", "receiver_player_id", "incomplete_pass"),
                   ("pbp", "receiver_player_id", "complete_pass"), _POS),
    "rushing_yards": (("pbp", "rusher_player_id", "rush_attempt"), _POS),
    "receiving_tds": (("weekly_stats", "targets", ""),
                      ("weekly_stats", "receiving_tds", ""), _POS),
    "rushing_tds": (("weekly_stats", "carries", ""),
                    ("weekly_stats", "rushing_tds", ""), _POS),
}

POSITION_NOTE = ("position group is the player's CURRENT nflverse position "
                 "applied to every season, not as-of")


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def positions(facts):
    """{gsis_id: group} from the newest data_version of each player-week.

    One group per player, because the source carries one position per player
    (see the module docstring). Asserted rather than assumed: if nflverse ever
    starts stamping positions as-of, this raises and the grouping gets
    redesigned instead of silently taking whichever row came last.
    """
    out = {}
    for gsis, pos in facts.execute(
            "SELECT DISTINCT gsis_id, position FROM nfl_player_week "
            "WHERE position IS NOT NULL"):
        g = GROUP.get(pos)
        if g is None:
            continue
        if out.get(gsis, g) != g:
            raise AssertionError(
                "player %s carries two position groups - the source is now "
                "as-of, and grouping by one position per player is wrong" % gsis)
        out[gsis] = g
    return out


def kneel_plays():
    """{(game_id, play_id): (rusher_id, season, week)} for every REG qb_kneel.

    A KNEEL IS NOT A CARRY FOR THIS METRIC (a-21, from f-14). nflverse sets
    `rush_attempt = 1` on every kneel and the weekly `carries` counts it -
    measured 2024: 405 of 405 kneels are rush attempts, and 240 of 246 QB
    weeks with a kneel have weekly carries equal to the play-by-play rush
    attempts INCLUDING the kneels. A kneel buys about -1 yard by design and
    follows the team winning, so left in, it drags the QB rushing fit (f-14:
    QB R^2 median 0.476 -> 0.616 without them) and manufactures persistence
    (QB lag-1 efficiency r 0.488 -> 0.405): the same QBs kneel every year.

    Read from the newest pull of each season's play-by-play. Refuses on zero
    kneels (the mirror was not read) and on a kneel that scored, which cannot
    happen and would mean `qb_kneel` no longer means a kneel.
    """
    import polars as pl
    out, seasons = {}, 0
    for season, path, _day in paths.pbp_files():
        d = pl.read_parquet(path, columns=["game_id", "play_id", "season_type",
                                           "week", "rusher_player_id",
                                           "qb_kneel", "rush_touchdown"])
        k = d.filter((pl.col("qb_kneel") == 1) & (pl.col("season_type") == "REG"))
        seasons += 1
        scored = k.filter(pl.col("rush_touchdown") == 1).height
        if scored:
            raise AssertionError("%d %d kneels scored a touchdown - qb_kneel "
                                 "no longer means a kneel" % (season, scored))
        for gid, play, week, rusher in k.select(
                "game_id", "play_id", "week", "rusher_player_id").rows():
            out[(gid, int(play))] = (rusher, int(season), int(week))
    if not out:
        raise SystemExit("found no kneels in %d play-by-play seasons - the raw "
                         "mirror was not read" % seasons)
    return out


def _rusher_rows(con, lo, hi, kneels):
    """Rushing yards per player-week from play rows, kneels removed from BOTH
    sides: the carry leaves the opportunity and its yards leave the
    production. Aggregated here rather than in SQL so the exclusion is keyed on
    the play, the one thing both feeds agree on."""
    kneels = kneels or {}
    agg, excluded = {}, 0
    for pid, season, week, gid, play, carry, yds in con.execute(
            "SELECT player_id, season, week, game_id, play_id, is_carry, yards "
            "FROM f_play_usage WHERE role='rusher' AND season_type='REG' "
            "AND season BETWEEN ? AND ?", (lo, hi)):
        if (gid, play) in kneels:
            excluded += 1
            continue
        a = agg.setdefault((pid, season, week), [0, 0.0, 0])
        a[0] += carry or 0
        if yds is None:
            a[2] += 1
        else:
            a[1] += yds
    out, dropped = [], 0
    for (pid, season, week), (opp, yds, yds_null) in sorted(agg.items()):
        if not opp:
            continue                    # not in the population: no volume at all
        if yds_null:
            dropped += 1
            continue
        out.append((pid, season, week, float(yds), (float(opp),)))
    return out, dropped, excluded


def _spine_rows(con, stat, lo, hi, kneels=None):
    """[(player, season, week, y, x_tuple)] plus the count of dropped weeks."""
    if stat == "rushing_yards":
        out, dropped, _excluded = _rusher_rows(con, lo, hi, kneels)
        return out, dropped
    if stat in ("receiving_yards", "receptions"):
        sql = ("SELECT player_id, season, week, SUM(is_target), SUM(is_reception), "
               "SUM(CASE WHEN is_reception=1 THEN yards END), "
               "SUM(CASE WHEN is_reception=1 AND yards IS NULL THEN 1 ELSE 0 END), "
               "SUM(air_yards), "
               "SUM(CASE WHEN is_target=1 AND air_yards IS NULL THEN 1 ELSE 0 END) "
               "FROM f_play_usage WHERE role='receiver' AND season_type='REG' "
               "AND season BETWEEN ? AND ? GROUP BY player_id, season, week")
    else:
        raise ValueError("no spine reader for %r" % stat)
    out, dropped = [], 0
    for pid, season, week, opp, rec, yds, yds_null, ay, ay_null in con.execute(
            sql, (lo, hi)):
        if not opp:
            continue                    # not in the population: no volume at all
        if stat == "receiving_yards":
            if yds_null or ay_null or ay is None:
                dropped += 1
                continue
            # Zero completions: receiving yards are zero by definition, not
            # missing - see the module docstring.
            out.append((pid, season, week, float(yds or 0.0), (float(opp), float(ay))))
        else:
            if rec is None:
                dropped += 1
                continue
            out.append((pid, season, week, float(rec), (float(opp),)))
    return out, dropped


def kneels_by_week(kneels):
    """{(rusher_id, season, week): kneel count}."""
    out = {}
    for rusher, season, week in (kneels or {}).values():
        if rusher is not None:
            out[(rusher, season, week)] = out.get((rusher, season, week), 0) + 1
    return out


def _weekly_rows(facts, stat, lo, hi, kneels=None):
    """Same shape, from nflverse weekly stats at the newest data_version.

    Rushing TDs: the weekly `carries` includes kneels, so the week's kneel
    count (from the play-by-play) comes off the opportunity side. It is the
    one place a fit reads two feeds, and it is a subtraction of a count of
    flagged plays, not a mix of two definitions of a carry: a kneel cannot
    score (asserted in `kneel_plays`), so the production side needs nothing.
    A week where the kneels exceed the weekly carries is two feeds disagreeing
    and drops, counted, rather than going negative."""
    opp, prod = {"receiving_tds": ("targets", "receiving_tds"),
                 "rushing_tds": ("carries", "rushing_tds")}[stat]
    sql = ("SELECT w.gsis_id, w.season, w.week, w.%s, w.%s FROM nfl_player_week w "
           "JOIN (SELECT gsis_id, season, week, season_type, "
           "MAX(data_version) dv FROM nfl_player_week WHERE season_type='REG' "
           "AND season BETWEEN ? AND ? GROUP BY gsis_id, season, week, season_type) m "
           "ON w.gsis_id=m.gsis_id AND w.season=m.season AND w.week=m.week "
           "AND w.season_type=m.season_type AND w.data_version=m.dv"
           % (prod, opp))
    kn = kneels_by_week(kneels) if stat == "rushing_tds" else {}
    out, dropped = [], 0
    for gsis, season, week, y, x in facts.execute(sql, (lo, hi)):
        if x is None or y is None:
            # Null is never zero: the week leaves both sides of the fit. It
            # is counted only when the other side carried something - a
            # defender's row, null on both, is not a dropped observation.
            if (x or 0) or (y or 0):
                dropped += 1
            continue
        k = kn.get((gsis, season, week), 0)
        if k > x:
            dropped += 1
            continue
        x = x - k
        if not x:
            continue
        out.append((gsis, season, week, float(y), (float(x),)))
    return out, dropped


RUSHING = ("rushing_yards", "rushing_tds")


def rows_for(con, facts, stat, lo, hi, kneels=None):
    """Rows for one pairing. `kneels` (from `kneel_plays`) is REQUIRED for a
    rushing stat - a caller that forgets it would silently fit kneels as
    carries, which is the defect a-21 fixed."""
    if stat in RUSHING and kneels is None:
        raise ValueError("%s needs the kneel plays: pass kneel_plays()" % stat)
    source = PAIRINGS[stat][0]
    if source == "spine":
        return _spine_rows(con, stat, lo, hi, kneels)
    return _weekly_rows(facts, stat, lo, hi, kneels)


# ---------------------------------------------------------------------------
# the fit
# ---------------------------------------------------------------------------

def _moments(y, xs):
    """Flattened Z'Z for Z = [1, x..., y]. Additive, so it bootstraps."""
    import numpy as np
    z = np.array([1.0] + list(xs) + [y])
    return np.outer(z, z).ravel()


def _solve(v, k):
    """(beta, r2) from a flattened moment matrix with k regressors."""
    import numpy as np
    m = np.asarray(v, dtype=float).reshape(k + 2, k + 2)
    xtx, xty, yty, n = m[:k + 1, :k + 1], m[:k + 1, k + 1], m[k + 1, k + 1], m[0, 0]
    if n < k + 2:
        return None, None
    try:
        beta = np.linalg.solve(xtx, xty)
    except np.linalg.LinAlgError:
        return None, None
    sse = yty - beta @ xty
    sst = yty - (m[0, k + 1] ** 2) / n
    if sst <= 0:
        return beta, None
    return beta, max(0.0, min(1.0, 1.0 - sse / sst))


def fit(rows, groups, through_week=None):
    """{(season, group): fit} over player-weeks, and the rows that fitted.

    `fit` is {"beta", "r2", "by_player": {pid: moment vector}, "weeks"}. The
    per-player moment vectors are kept because the R^2 interval resamples
    players and needs them.

    `through_week=t` fits on REG weeks 1..t only - the AS-OF line a call made
    after week t could have known. None is the full season, which is the
    published descriptive metric and knows the rest of the season.
    """
    import numpy as np
    cells = {}
    for pid, season, week, y, xs in rows:
        if through_week is not None and week > through_week:
            continue
        g = groups.get(pid)
        if g is None:
            continue
        c = cells.setdefault((season, g), {})
        c.setdefault(pid, []).append((week, y, xs))
    fits = {}
    for key, by_player in cells.items():
        if len(by_player) < MIN_PLAYERS:
            continue
        k = len(next(iter(by_player.values()))[0][2])
        vecs = {pid: sum(_moments(y, xs) for _w, y, xs in wk)
                for pid, wk in by_player.items()}
        total = sum(vecs.values())
        beta, r2 = _solve(total, k)
        if beta is None or r2 is None:
            continue
        fits[key] = {"beta": beta, "r2": r2, "k": k, "moments": vecs,
                     "weeks": by_player,
                     "rows": sum(len(w) for w in by_player.values())}
    return fits


def residuals(fits):
    """{(season, group, pid): [(week, fitted, residual)]}."""
    import numpy as np
    out = {}
    for (season, g), f in fits.items():
        beta = f["beta"]
        for pid, weeks in f["weeks"].items():
            lst = []
            for week, y, xs in weeks:
                yhat = float(beta @ np.array([1.0] + list(xs)))
                lst.append((week, yhat, y - yhat))
            out[(season, g, pid)] = lst
    return out


# ---------------------------------------------------------------------------
# estimates
# ---------------------------------------------------------------------------

def _rng(*key):
    import numpy as np
    return np.random.default_rng(
        [SEED, zlib.crc32("|".join(str(k) for k in key).encode("utf-8"))])


def _game_bootstrap(weeks, subject):
    """{'sum', 'per_game'} Estimates for one player's residuals, his games
    resampled - both from ONE set of resamples."""
    from analytics.intervals import histogram_bootstrap
    import numpy as np
    blocks = {w: np.array([r, 1.0]) for w, _f, r in weeks}
    return histogram_bootstrap(
        blocks, {"sum": lambda v: float(v[0]),
                 "per_game": lambda v: float(v[0] / v[1]) if v[1] else None},
        draws=DRAWS, rows_by_block={w: 1 for w in blocks}, subject=subject)


def player_values(stat, res, per_game=False):
    """[(pid, slice, Estimate)]: summed residual (or per game), games resampled.

    The SUM scales with games played - f-14 measured |sum| against games at
    r 0.38-0.40 - so it conflates availability with efficiency. Anything that
    bands or ranks reads the per-game value instead.

    The subject string is the one a-18 used for the sum, so the summed values
    reproduce a-18's draws; the per-game value comes from the same draws.
    """
    out = []
    for (season, g, pid), weeks in sorted(res.items()):
        if len(weeks) < MIN_GAMES:
            continue
        got = _game_bootstrap(weeks, "%s|%s|%s" % (stat, pid, season))
        out.append((pid, "%d|%s" % (season, g),
                    got["per_game" if per_game else "sum"]))
    return out


ASOF_MIN_GAMES = 3       # games through week t, for an as-of value to exist


def asof_values(stat, rows, groups):
    """[(pid, 'season|group|wNN', Estimate)]: the AS-OF per-game residual.

    For every REG week t of every season: the line is fitted on weeks 1..t of
    that season only (`fit(..., through_week=t)`), and applied to the player's
    own weeks 1..t. Nothing after week t enters, so a call made after week t's
    games could have read this value. The line is refitted each week, so a
    player who did not play week t still gets a (slightly) different value at
    t than at t-1 - the line moved, and that is what a caller at t would see.

    Per game, not summed, because a call compares players with different
    games played. Games resampled, >= ASOF_MIN_GAMES games through t.
    """
    by_season = {}
    for r in rows:
        by_season.setdefault(r[1], []).append(r)
    out = []
    for season in sorted(by_season):
        srows = by_season[season]
        for t in sorted({r[2] for r in srows}):
            res = residuals(fit(srows, groups, through_week=t))
            for (s, g, pid), weeks in sorted(res.items()):
                if len(weeks) < ASOF_MIN_GAMES:
                    continue
                est = _game_bootstrap(
                    weeks, "%s|asof|%s|%s|%d" % (stat, pid, s, t))["per_game"]
                out.append((pid, "%d|%s|w%02d" % (s, g, t), est))
    return out


def fit_values(stat, fits):
    """[('_league', slice, Estimate)]: R^2 per fit, players resampled."""
    from analytics.intervals import histogram_bootstrap
    out = []
    for (season, g), f in sorted(fits.items()):
        k = f["k"]
        got = histogram_bootstrap(
            f["moments"], {"r2": lambda v, k=k: _solve(v, k)[1]}, draws=DRAWS,
            rows_by_block={p: len(w) for p, w in f["weeks"].items()},
            subject="%s|fit|%d|%s" % (stat, season, g))["r2"]
        out.append(("_league", "%d|%s" % (season, g), got))
    return out


def band_values(stat, player_vals):
    """[('_league', slice, Estimate)]: quantiles of the summed residual."""
    import numpy as np
    from analytics.intervals import Estimate
    cells = {}
    for pid, sl, est in player_vals:
        cells.setdefault(sl, []).append(est.est)
    out = []
    for sl, vals in sorted(cells.items()):
        n = len(vals)
        if n < MIN_PLAYERS:
            continue
        a = np.array(vals)
        point = np.percentile(a, QUANTILES)
        idx = _rng(stat, "bands", sl).integers(0, n, size=(DRAWS, n))
        boot = np.percentile(a[idx], QUANTILES, axis=1)       # (q, draws)
        for i, q in enumerate(QUANTILES):
            lo, hi = np.percentile(boot[i], [2.5, 97.5])
            p = float(point[i])
            out.append(("_league", "%s|q%02d" % (sl, q),
                        Estimate(p, min(float(lo), p), max(float(hi), p), n,
                                 "boot%d-players" % DRAWS, n)))
    return out


def _pearson_at(v, k):
    n, sx, sy, sxy, sxx, syy = (v[k + i] for i in range(6))
    if n < 3:
        return None
    vx, vy = sxx - sx * sx / n, syy - sy * sy / n
    if vx <= 0 or vy <= 0:
        return None
    return (sxy - sx * sy / n) / math.sqrt(vx * vy)


def _pair_vec(a_opp, b_opp, a_res, b_res):
    import numpy as np
    return np.array([1, a_opp, b_opp, a_opp * b_opp, a_opp ** 2, b_opp ** 2,
                     1, a_res, b_res, a_res * b_res, a_res ** 2, b_res ** 2],
                    dtype=float)


def pairs(res, design, min_games=MIN_GAMES):
    """{group: {pid: summed pair vector}}, per-game fitted and residual.

    `min_games` is the lag-1 requirement in BOTH seasons. It is not neutral:
    f-14 measured lag-1 r rising with it (WR receiving yards 0.209 at 1 game,
    0.297 at 12) because leavers carry lower residuals. `survivorship` puts
    that on the envelope."""
    import numpy as np
    by_pg = {}
    for (season, g, pid), weeks in res.items():
        by_pg.setdefault((g, pid), {})[season] = weeks
    out = {}
    for (g, pid), seasons in by_pg.items():
        vec = None
        for season, weeks in seasons.items():
            if design == "lag1_season":
                nxt = seasons.get(season + 1)
                if not nxt or len(weeks) < min_games or len(nxt) < min_games:
                    continue
                a, b = weeks, nxt
            else:
                a = [w for w in weeks if w[0] % 2 == 1]
                b = [w for w in weeks if w[0] % 2 == 0]
                if len(a) < MIN_HALF or len(b) < MIN_HALF:
                    continue
            v = _pair_vec(sum(f for _w, f, _r in a) / len(a),
                          sum(f for _w, f, _r in b) / len(b),
                          sum(r for _w, _f, r in a) / len(a),
                          sum(r for _w, _f, r in b) / len(b))
            vec = v if vec is None else vec + v
        if vec is not None:
            out.setdefault(g, {})[pid] = vec
    return out


def persistence_values(stat, res):
    """[('_league', slice, Estimate)] - opportunity, residual and their gap,
    from ONE set of resamples per (group, design)."""
    from analytics.intervals import histogram_bootstrap
    out = []
    for design in ("lag1_season", "split_half"):
        for g, vecs in sorted(pairs(res, design).items()):
            if len(vecs) < MIN_PLAYERS:
                continue

            def gap(v):
                a, b = _pearson_at(v, 0), _pearson_at(v, 6)
                return None if a is None or b is None else a - b
            got = histogram_bootstrap(
                vecs, {"opportunity": lambda v: _pearson_at(v, 0),
                       "residual": lambda v: _pearson_at(v, 6),
                       "gap": gap},
                draws=DRAWS, rows_by_block={p: int(v[0]) for p, v in vecs.items()},
                subject="%s|persistence|%s|%s" % (stat, design, g))
            for quantity in ("opportunity", "residual", "gap"):
                out.append(("_league", "%s|%s|%s" % (g, design, quantity),
                            got[quantity]))
    return out


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

def _opp_words(stat):
    return " + ".join(PAIRINGS[stat][2])


FAMILIES = ("player", "per_game", "fit", "bands", "persistence", "asof")
SURVIVORSHIP_GAMES = (1, MIN_GAMES, 12)


def kneel_note(stat, excluded):
    """What the kneel exclusion removed, for a rushing stat. Computed."""
    if stat not in RUSHING:
        return ""
    by = ", ".join("%s %d" % (g, excluded.get(g, 0)) for g in PAIRINGS[stat][3])
    return ("QB kneel-downs (nflverse qb_kneel) are excluded from both sides of "
            "the fit - %d kneel plays removed (%s by position group)"
            % (sum(excluded.values()), by))


def fit_note(stat, fits, r2_by_fit=None):
    """The fit quality, in words, for the envelope. Computed, never typed.

    `r2_by_fit` is {(season, group): r2} from the values actually PUBLISHED
    as `opportunity_residual.fit.<stat>`; when given it is what the medians
    are taken over, so the envelope cannot drift from its own fit values
    (a-18's scratch envelope did, in 9 of 13 groups, from an earlier version
    that took the upper-middle element). `check_notes` re-verifies it from the
    store after every publish.
    """
    if not fits:
        return "no fit reached %d players" % MIN_PLAYERS
    r2s = r2_by_fit if r2_by_fit is not None else {
        k: f["r2"] for k, f in fits.items()}
    parts = []
    for g in PAIRINGS[stat][3]:
        r2 = sorted(v for (s, gg), v in r2s.items() if gg == g)
        if r2:
            parts.append("%s median %.3f (%.3f-%.3f, %d seasons)"
                         % (g, statistics.median(r2), r2[0], r2[-1], len(r2)))
    return ("fit %s ~ %s, OLS per season x position group over player-weeks; "
            "R^2 %s; %s" % (PAIRINGS[stat][1], _opp_words(stat),
                            "; ".join(parts), POSITION_NOTE))


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def games_note(values):
    """How far a player value sorts by games played: Pearson r of |estimate|
    against n (his games), pooled over the published player-seasons."""
    vals = [(abs(e.est), e.n) for _p, _s, e in values if e.est is not None]
    r = _pearson([a for a, _n in vals], [float(n) for _a, n in vals])
    return "|value| against games played r %s over %d player-seasons" % (
        "n/a" if r is None else "%.2f" % r, len(vals))


def survivorship(res):
    """{group: {"r": {min_games: r}, "leave": (left, qualifying),
    "mean": (leavers' mean per-game residual, returners')}} - POINT figures
    for the envelope, never values.

    A player-season qualifies with >= MIN_GAMES games; it LEAVES when he has
    no season T+1 with >= MIN_GAMES. Only seasons whose T+1 has at least one
    qualifying player-season are counted, so an in-progress season (2026 at
    week 2: nobody has 5 games) does not read as everybody leaving.
    """
    out = {}
    for m in SURVIVORSHIP_GAMES:
        for g, vecs in pairs(res, "lag1_season", min_games=m).items():
            if len(vecs) >= MIN_PLAYERS:
                out.setdefault(g, {"r": {}})["r"][m] = _pearson_at(
                    sum(vecs.values()), 6)
    qual = {k: v for k, v in res.items() if len(v) >= MIN_GAMES}
    live = {s for s, _g, _p in qual}
    for (s, g, pid), weeks in qual.items():
        if s + 1 not in live:
            continue
        d = out.setdefault(g, {"r": {}})
        d.setdefault("_left", []).append(
            ((s + 1, g, pid) not in qual,
             sum(r for _w, _f, r in weeks) / len(weeks)))
    for g, d in out.items():
        lst = d.pop("_left", [])
        left = [v for gone, v in lst if gone]
        stay = [v for gone, v in lst if not gone]
        d["leave"] = (len(left), len(lst))
        d["mean"] = (sum(left) / len(left) if left else None,
                     sum(stay) / len(stay) if stay else None)
    return out


def survivorship_note(stat, surv):
    """The games requirement and what it does to lag-1 r, in words."""
    parts = []
    for g in PAIRINGS[stat][3]:
        d = surv.get(g)
        if not d:
            continue
        rs = ", ".join("%d: %s" % (m, "n/a" if d["r"].get(m) is None
                                   else "%.3f" % d["r"][m])
                       for m in SURVIVORSHIP_GAMES)
        left, qual = d.get("leave", (0, 0))
        ml, ms = d.get("mean", (None, None))
        parts.append("%s lag-1 efficiency r by games required {%s}; %d of %d "
                     "qualifying player-seasons do not return, mean per-game "
                     "residual %s leaving against %s returning"
                     % (g, rs, left, qual,
                        "n/a" if ml is None else "%+.3f" % ml,
                        "n/a" if ms is None else "%+.3f" % ms))
    return ("SURVIVORSHIP: a lag-1 pair needs >= %d games in both seasons, a "
            "split-half pair >= %d in each half, so both designs are "
            "conditional on the player staying in the league; %s"
            % (MIN_GAMES, MIN_HALF, "; ".join(parts)))


def metric_for(stat, family, note=""):
    prod = PAIRINGS[stat][1]
    opp = _opp_words(stat)
    spec = {
        "player": ("opportunity_residual.%s" % stat,
                   "Production beyond opportunity: %s" % prod,
                   "%s over the season minus what his %s predicted, SUMMED "
                   "over his games, so it grows with games played - band or "
                   "rank on opportunity_residual.per_game.%s instead; the "
                   "interval resamples his games and is conditional on the "
                   "fitted line" % (prod, opp, stat),
                   "player", "game", "season|position_group"),
        "per_game": ("opportunity_residual.per_game.%s" % stat,
                     "Production beyond opportunity per game: %s" % prod,
                     "%s minus what his %s predicted, per game played over the "
                     "season (the summed residual divided by his games); the "
                     "interval resamples his games and is conditional on the "
                     "fitted line" % (prod, opp),
                     "player", "game", "season|position_group"),
        "fit": ("opportunity_residual.fit.%s" % stat,
                "How much of %s volume explains" % prod,
                "R^2 of %s on %s across one season's player-weeks in one "
                "position group; the interval resamples players" % (prod, opp),
                "league", "player", "season|position_group"),
        "bands": ("opportunity_residual.bands.%s" % stat,
                  "Band edges for production beyond opportunity per game: %s"
                  % prod,
                  "the named quantile (q10 = tenth centile) of the PER-GAME %s "
                  "residual (opportunity_residual.per_game) across the "
                  "player-seasons published for that season and group; edges "
                  "for bands, not ranks. The interval resamples players"
                  % prod, "league", "player", "season|position_group|quantile"),
        "persistence": ("opportunity_residual.persistence.%s" % stat,
                        "Does it persist: %s volume against efficiency" % prod,
                        "Pearson r of a player's per-game value between two "
                        "samples of himself - season T and T+1, or odd and "
                        "even weeks - for the volume-predicted %s "
                        "(opportunity), the residual (efficiency), and "
                        "opportunity minus efficiency (gap), all three from "
                        "one set of resamples of players" % prod,
                        "league", "player", "position_group|design|quantity"),
        "asof": ("opportunity_residual.asof.%s" % stat,
                 "As-of production beyond opportunity per game: %s" % prod,
                 "AS-OF: %s minus what his %s predicted, per game, over REG "
                 "weeks 1..t of the season, where the line is fitted on weeks "
                 "1..t only - nothing after week t enters, so a call made "
                 "after week t could have read it. Slice wNN is t. The "
                 "interval resamples his games through t and is conditional "
                 "on that line. opportunity_residual.per_game is the "
                 "full-season DESCRIPTIVE value and knows later weeks"
                 % (prod, opp),
                 "player", "game", "season|position_group|through_week"),
    }[family]
    key, label, unit, subject_type, block, slice_kind = spec
    return metrics.Metric(
        key=key, label=label, unit=unit, subject_type=subject_type,
        block=block, basis="pbp" if PAIRINGS[stat][0] == "spine" else "weekly_stats",
        availability="current", slice_kind=slice_kind,
        shares_denominator=None, requires=REQUIRES[stat], note=note)


def compute(con, facts, stat, verbose=True, kneels=None, asof=True):
    lo, hi, _note = metrics.derive_range(con, metric_for(stat, "player"))
    groups = positions(facts)
    if stat in RUSHING and kneels is None:
        kneels = kneel_plays()
    rows, dropped = rows_for(con, facts, stat, lo, hi, kneels)
    groups_needed = set(PAIRINGS[stat][3])
    no_group = sum(1 for r in rows if groups.get(r[0]) not in groups_needed)
    rows = [r for r in rows if groups.get(r[0]) in groups_needed]
    # Kneels excluded, per group - the rusher of each kneel play in range.
    excluded = {}
    if stat in RUSHING:
        for rusher, season, _w in kneels.values():
            g = groups.get(rusher)
            if lo <= season <= hi and g in groups_needed:
                excluded[g] = excluded.get(g, 0) + 1
    fits = fit(rows, groups)
    res = residuals(fits)
    pv = player_values(stat, res)
    pg = player_values(stat, res, per_game=True)
    got = {"player": pv, "per_game": pg, "fit": fit_values(stat, fits),
           "bands": band_values(stat, pg),
           "persistence": persistence_values(stat, res)}
    if asof:
        got["asof"] = asof_values(stat, rows, groups)
    extra = {"excluded": excluded, "survivorship": survivorship(res)}
    if verbose:
        # Printed every run, zero included.
        print("%s %d-%d: %d player-weeks fitted in %d fits; dropped %d with a "
              "null input, %d outside the position groups; kneel plays "
              "excluded %s"
              % (stat, lo, hi, sum(f["rows"] for f in fits.values()),
                 len(fits), dropped, no_group,
                 excluded if stat in RUSHING else "n/a"), flush=True)
    return lo, hi, fits, got, extra


def notes(stat, fits, got, extra):
    """{family: note} - every note computed from what is being published."""
    r2 = {}
    for _s, sl, e in got["fit"]:
        season, g = sl.split("|")
        r2[(int(season), g)] = e.est
    base = fit_note(stat, fits, r2)
    kn = kneel_note(stat, extra["excluded"])
    if kn:
        base = "%s; %s" % (base, kn)
    out = {
        "player": "%s; SUMMED, so it scales with games: %s (per game: %s)"
                  % (base, games_note(got["player"]),
                     games_note(got["per_game"])),
        "per_game": "%s; per game: %s" % (base, games_note(got["per_game"])),
        "fit": base,
        "bands": "%s; edges of the per-game residual" % base,
        "persistence": "%s; %s" % (base, survivorship_note(
            stat, extra["survivorship"])),
    }
    if "asof" in got:
        seasons = sorted({sl.split("|")[0] for _p, sl, _e in got["asof"]})
        out["asof"] = ("AS-OF, refitted each REG week t on weeks 1..t of that "
                       "season; a value needs >= %d games through t; %d values "
                       "over %d seasons; %s%s"
                       % (ASOF_MIN_GAMES, len(got["asof"]), len(seasons),
                          POSITION_NOTE, "; " + kn if kn else ""))
    return out


def publish(con, facts, stats=None, verbose=True, asof=True):
    written = {}
    kneels = None
    for stat in (stats or PAIRINGS):
        if stat in RUSHING and kneels is None:
            kneels = kneel_plays()
            if verbose:
                print("kneel plays read from the play-by-play: %d" % len(kneels),
                      flush=True)
        lo, hi, fits, got, extra = compute(con, facts, stat, verbose,
                                           kneels=kneels, asof=asof)
        note = notes(stat, fits, got, extra)
        for family, values in got.items():
            if not values:
                raise SystemExit("%s produced no %s values - an empty metric "
                                 "is a failure, not a publish" % (stat, family))
            m = metric_for(stat, family, note[family])
            written[m.key] = metrics.publish(con, m, values, lo, hi)
            if verbose:
                print("  %-52s %d-%d  %6d values" % (m.key, lo, hi,
                                                      written[m.key]), flush=True)
    bad = check_notes(con)
    if bad:
        raise AssertionError("published range_note disagrees with the published "
                             "fit values: %s" % bad)
    return written


_MEDIAN = re.compile(r"\b(WR|TE|RB|QB) median ([0-9.]+)")


def check_notes(con):
    """[(metric, group, note median, values median)] where an envelope's R^2
    median disagrees with its own published fit values at the note's
    precision. Empty is clean. Reads the STORE, so it catches a stale envelope
    whatever code wrote it (f-14 found 9 of 13 groups stale in a-18's scratch
    store)."""
    bad = []
    for metric, note in con.execute(
            "SELECT metric, range_note FROM f_metrics "
            "WHERE metric LIKE 'opportunity_residual.%'"):
        stat = metric.rsplit(".", 1)[-1]
        vals = {}
        for sl, est in con.execute(
                "SELECT slice, est FROM f_metric_values WHERE metric=?",
                ("opportunity_residual.fit.%s" % stat,)):
            vals.setdefault(sl.split("|")[1], []).append(est)
        for g, got in _MEDIAN.findall(note):
            want = "%.3f" % statistics.median(vals[g]) if vals.get(g) else None
            if got != want:
                bad.append((metric, g, got, want))
    return bad


def show(con):
    rows = con.execute(
        "SELECT metric, slice, est, lo, hi, n, rows FROM f_metric_values "
        "WHERE metric LIKE 'opportunity_residual.persistence.%' "
        "ORDER BY metric, slice").fetchall()
    if not rows:
        raise SystemExit("nothing published - run --publish")
    for metric, sl, est, a, b, n, r in rows:
        print("%-46s %-34s %+.3f [%+.3f, %+.3f] n=%d players, %d pairs"
              % (metric.split(".")[-1], sl, est, a, b, n, r or 0))
    for metric, note in con.execute(
            "SELECT metric, range_note FROM f_metrics WHERE metric LIKE "
            "'opportunity_residual.fit.%' ORDER BY metric"):
        print("%s\n    %s" % (metric, note))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--stat", action="append", choices=sorted(PAIRINGS))
    ap.add_argument("--check-notes", action="store_true",
                    help="exit 1 if any envelope's R^2 medians disagree with "
                         "its own published fit values")
    a = ap.parse_args(argv)
    if a.check_notes:
        con = paths.connect(read_only=True)
        n = con.execute("SELECT COUNT(*) FROM f_metrics WHERE metric LIKE "
                        "'opportunity_residual.%'").fetchone()[0]
        if not n:
            raise SystemExit("no opportunity_residual metrics to check - an "
                             "empty check is not a pass")
        bad = check_notes(con)
        for b in bad:
            print("STALE %s %s: note %s, values %s" % b)
        print("%d metrics checked, %d stale medians" % (n, len(bad)))
        return 1 if bad else 0
    if a.publish:
        facts = paths.market_log_ro()
        try:
            publish(paths.connect(), facts, a.stat)
        finally:
            facts.close()
        return 0
    return show(paths.connect(read_only=True))


if __name__ == "__main__":
    sys.exit(main())
