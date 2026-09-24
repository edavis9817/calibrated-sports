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

FOUR METRICS PER STAT.

    opportunity_residual.<stat>              player  slice season|group
        the summed residual, interval from resampling his games
    opportunity_residual.fit.<stat>          league  slice season|group
        R^2 of each fit, interval from resampling players. ALSO summarised in
        the range_note of every metric of the stat, because a residual whose
        fit explains 10% of the variance means something different from one
        whose fit explains 80%, and a reader must see which before reading it
    opportunity_residual.bands.<stat>        league  slice season|group|qNN
        band edges: percentiles of the summed residual across the published
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

WHAT THIS IS NOT THE SAME AS. CLAUDE.md's "player residuals don't persist
(r ~ 0.09)" comes from `research/externals.py`, and ITS residual is actual minus
the player's OWN prior expanding mean - a usage surprise, not an efficiency. It
is a different quantity measured on a different population (2016-2024, >=8
games, receptions / receiving yards / carries). This module does not re-derive
that number; it measures the claim the site makes with it.

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
- a player's summed residual resamples HIS GAMES (n = games). It is conditional
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


def _spine_rows(con, stat, lo, hi):
    """[(player, season, week, y, x_tuple)] plus the count of dropped weeks."""
    if stat in ("receiving_yards", "receptions"):
        sql = ("SELECT player_id, season, week, SUM(is_target), SUM(is_reception), "
               "SUM(CASE WHEN is_reception=1 THEN yards END), "
               "SUM(CASE WHEN is_reception=1 AND yards IS NULL THEN 1 ELSE 0 END), "
               "SUM(air_yards), "
               "SUM(CASE WHEN is_target=1 AND air_yards IS NULL THEN 1 ELSE 0 END) "
               "FROM f_play_usage WHERE role='receiver' AND season_type='REG' "
               "AND season BETWEEN ? AND ? GROUP BY player_id, season, week")
    else:
        sql = ("SELECT player_id, season, week, SUM(is_carry), NULL, SUM(yards), "
               "SUM(CASE WHEN yards IS NULL THEN 1 ELSE 0 END), NULL, 0 "
               "FROM f_play_usage WHERE role='rusher' AND season_type='REG' "
               "AND season BETWEEN ? AND ? GROUP BY player_id, season, week")
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
        elif stat == "receptions":
            if rec is None:
                dropped += 1
                continue
            out.append((pid, season, week, float(rec), (float(opp),)))
        else:
            if yds_null or yds is None:
                dropped += 1
                continue
            out.append((pid, season, week, float(yds), (float(opp),)))
    return out, dropped


def _weekly_rows(facts, stat, lo, hi):
    """Same shape, from nflverse weekly stats at the newest data_version."""
    opp, prod = {"receiving_tds": ("targets", "receiving_tds"),
                 "rushing_tds": ("carries", "rushing_tds")}[stat]
    sql = ("SELECT w.gsis_id, w.season, w.week, w.%s, w.%s FROM nfl_player_week w "
           "JOIN (SELECT gsis_id, season, week, season_type, "
           "MAX(data_version) dv FROM nfl_player_week WHERE season_type='REG' "
           "AND season BETWEEN ? AND ? GROUP BY gsis_id, season, week, season_type) m "
           "ON w.gsis_id=m.gsis_id AND w.season=m.season AND w.week=m.week "
           "AND w.season_type=m.season_type AND w.data_version=m.dv"
           % (prod, opp))
    out, dropped = [], 0
    for gsis, season, week, y, x in facts.execute(sql, (lo, hi)):
        if x is None or y is None:
            # Null is never zero: the week leaves both sides of the fit. It
            # is counted only when the other side carried something - a
            # defender's row, null on both, is not a dropped observation.
            if (x or 0) or (y or 0):
                dropped += 1
            continue
        if not x:
            continue
        out.append((gsis, season, week, float(y), (float(x),)))
    return out, dropped


def rows_for(con, facts, stat, lo, hi):
    source = PAIRINGS[stat][0]
    if source == "spine":
        return _spine_rows(con, stat, lo, hi)
    return _weekly_rows(facts, stat, lo, hi)


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


def fit(rows, groups):
    """{(season, group): fit} over player-weeks, and the rows that fitted.

    `fit` is {"beta", "r2", "by_player": {pid: moment vector}, "weeks"}. The
    per-player moment vectors are kept because the R^2 interval resamples
    players and needs them.
    """
    import numpy as np
    cells = {}
    for pid, season, week, y, xs in rows:
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


def player_values(stat, res):
    """[(pid, slice, Estimate)]: summed residual, games resampled."""
    from analytics.intervals import histogram_bootstrap
    import numpy as np
    out = []
    for (season, g, pid), weeks in sorted(res.items()):
        if len(weeks) < MIN_GAMES:
            continue
        blocks = {w: np.array([r]) for w, _f, r in weeks}
        est = histogram_bootstrap(blocks, {"sum": lambda v: float(v[0])},
                                  draws=DRAWS, rows_by_block={w: 1 for w in blocks},
                                  subject="%s|%s|%s" % (stat, pid, season))["sum"]
        out.append((pid, "%d|%s" % (season, g), est))
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


def pairs(res, design):
    """{group: {pid: summed pair vector}}, per-game fitted and residual."""
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
                if not nxt or len(weeks) < MIN_GAMES or len(nxt) < MIN_GAMES:
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


def fit_note(stat, fits):
    """The fit quality, in words, for the envelope. Computed, never typed."""
    if not fits:
        return "no fit reached %d players" % MIN_PLAYERS
    parts = []
    for g in PAIRINGS[stat][3]:
        r2 = sorted(f["r2"] for (s, gg), f in fits.items() if gg == g)
        if r2:
            parts.append("%s median %.3f (%.3f-%.3f, %d seasons)"
                         % (g, statistics.median(r2), r2[0], r2[-1], len(r2)))
    return ("fit %s ~ %s, OLS per season x position group over player-weeks; "
            "R^2 %s; %s" % (PAIRINGS[stat][1], _opp_words(stat),
                            "; ".join(parts), POSITION_NOTE))


def metric_for(stat, family, note=""):
    prod = PAIRINGS[stat][1]
    opp = _opp_words(stat)
    spec = {
        "player": ("opportunity_residual.%s" % stat,
                   "Production beyond opportunity: %s" % prod,
                   "%s over the season minus what his %s predicted, summed "
                   "over his games; the interval resamples his games and is "
                   "conditional on the fitted line" % (prod, opp),
                   "player", "game", "season|position_group"),
        "fit": ("opportunity_residual.fit.%s" % stat,
                "How much of %s volume explains" % prod,
                "R^2 of %s on %s across one season's player-weeks in one "
                "position group; the interval resamples players" % (prod, opp),
                "league", "player", "season|position_group"),
        "bands": ("opportunity_residual.bands.%s" % stat,
                  "Band edges for production beyond opportunity: %s" % prod,
                  "the named quantile (q10 = tenth centile) of the summed %s "
                  "residual across the "
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
    }[family]
    key, label, unit, subject_type, block, slice_kind = spec
    return metrics.Metric(
        key=key, label=label, unit=unit, subject_type=subject_type,
        block=block, basis="pbp" if PAIRINGS[stat][0] == "spine" else "weekly_stats",
        availability="current", slice_kind=slice_kind,
        shares_denominator=None, requires=REQUIRES[stat], note=note)


def compute(con, facts, stat, verbose=True):
    lo, hi, _note = metrics.derive_range(con, metric_for(stat, "player"))
    groups = positions(facts)
    rows, dropped = rows_for(con, facts, stat, lo, hi)
    groups_needed = set(PAIRINGS[stat][3])
    no_group = sum(1 for r in rows if groups.get(r[0]) not in groups_needed)
    rows = [r for r in rows if groups.get(r[0]) in groups_needed]
    fits = fit(rows, groups)
    res = residuals(fits)
    pv = player_values(stat, res)
    got = {"player": pv, "fit": fit_values(stat, fits),
           "bands": band_values(stat, pv),
           "persistence": persistence_values(stat, res)}
    if verbose:
        # Printed every run, zero included.
        print("%s %d-%d: %d player-weeks fitted in %d fits; dropped %d with a "
              "null input, %d outside the position groups"
              % (stat, lo, hi, sum(f["rows"] for f in fits.values()),
                 len(fits), dropped, no_group), flush=True)
    return lo, hi, fits, got


def publish(con, facts, stats=None, verbose=True):
    written = {}
    for stat in (stats or PAIRINGS):
        lo, hi, fits, got = compute(con, facts, stat, verbose)
        note = fit_note(stat, fits)
        for family, values in got.items():
            if not values:
                raise SystemExit("%s produced no %s values - an empty metric "
                                 "is a failure, not a publish" % (stat, family))
            m = metric_for(stat, family, note)
            written[m.key] = metrics.publish(con, m, values, lo, hi)
            if verbose:
                print("  %-52s %d-%d  %6d values" % (m.key, lo, hi,
                                                      written[m.key]), flush=True)
    return written


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
    a = ap.parse_args(argv)
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
