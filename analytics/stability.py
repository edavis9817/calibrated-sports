"""5. Usage stability: which usage metrics are predictive and which are noise.

    python -m analytics.stability --publish
    python -m analytics.stability --show

A MEASUREMENT, NOT A FEATURE. This is the one of the five that speaks to the
research record: CLAUDE.md already holds "usage carries the signal, efficiency
is noise" and "player residuals don't persist (r ~ 0.09)". This puts a number
and an interval on the first half of that, per usage metric, so a page can say
which of them a reader should believe week to week.

TWO NUMBERS, AND REPORTING ONLY ONE OF THEM IS THE TRAP.

  between   the share of total variance that sits BETWEEN player-seasons rather
            than within them. High means the metric separates players at all.
  within    the lag-1 autocorrelation of week-to-week DEVIATIONS from a
            player's own season mean. High means this week's surprise says
            something about next week's.

A naive pooled correlation of (week w, week w+1) answers neither. It reads
around +0.8 for every usage metric, because good players are high every week,
and a reader takes that as "targets are 80% predictable week to week" when what
it says is "some receivers are better than others". The deviations are
demeaned per player-season for exactly that reason.

`n` IS PLAYERS. A player contributes many weeks and many seasons and they are
not independent of each other; the block is the player.

HOW IT IS COMPUTED AT THIS SIZE. Both statistics are functions of SUMS, so each
player compresses to a fixed vector of sufficient statistics and the bootstrap
is a matrix product - `analytics.intervals.histogram_bootstrap`, which is not
only for histograms. Resampling ~200,000 raw pairs 2,000 times is not feasible
in this language; resampling 4,000 six-wide vectors is instant, and it is the
same estimator.
"""
import argparse
import sys

from analytics import metrics, paths

MIN_WEEKS = 6           # within one season, to have deviations worth measuring
MIN_PLAYERS = 30

# kind -> (role, the player's component, the team denominator or None)
KINDS = {
    "targets": ("receiver", "is_target", None),
    "receptions": ("receiver", "is_reception", None),
    "carries": ("rusher", "is_carry", None),
    "target_share": ("receiver", "is_target", "is_target"),
    "reception_share": ("receiver", "is_reception", "is_reception"),
    "carry_share": ("rusher", "is_carry", "is_carry"),
}

REQUIRES = {
    "targets": (("pbp", "receiver_player_id", "incomplete_pass"),),
    "receptions": (("pbp", "receiver_player_id", "complete_pass"),),
    "carries": (("pbp", "rusher_player_id", "rush_attempt"),),
}
REQUIRES["target_share"] = REQUIRES["targets"]
REQUIRES["reception_share"] = REQUIRES["receptions"]
REQUIRES["carry_share"] = REQUIRES["carries"]

FAMILIES = {
    "naive_lag1": ("Naive week-to-week correlation, NOT demeaned",
                   "pooled lag-1 correlation of raw weekly values across all "
                   "players; high because good players are high every week, "
                   "which is why it is published beside the demeaned figure "
                   "rather than instead of it"),
    "within_lag1": ("Week-to-week persistence of usage deviations",
                    "lag-1 autocorrelation of a player's weekly deviation from "
                    "his own season mean; 0 means this week's surprise says "
                    "nothing about next week's"),
    "between": ("Share of usage variance that is between players",
                "share of total variance sitting between player-seasons rather "
                "than within them; high means the metric separates players"),
}


def metric_for(family, kind):
    label, unit = FAMILIES[family]
    return metrics.Metric(
        key="usage_stability.%s.%s" % (family, kind),
        label="%s: %s" % (label, kind),
        unit=unit, subject_type="league", block="player", basis="pbp",
        availability="current", slice_kind="", requires=REQUIRES[kind])


def weekly(con, kind, season_from, season_to):
    """{player: {(season, week): value}} over regular-season weeks."""
    role, comp, denom = KINDS[kind]
    team_total = {}
    if denom:
        for season, week, team, total in con.execute(
                "SELECT season, week, team, SUM(%s) FROM f_play_usage "
                "WHERE role=? AND season BETWEEN ? AND ? AND season_type='REG' "
                "GROUP BY season, week, team" % denom,
                (role, season_from, season_to)):
            team_total[(season, week, team)] = total or 0
    out = {}
    for player, season, week, team, got in con.execute(
            "SELECT player_id, season, week, team, SUM(%s) FROM f_play_usage "
            "WHERE role=? AND season BETWEEN ? AND ? AND season_type='REG' "
            "GROUP BY player_id, season, week, team" % comp,
            (role, season_from, season_to)):
        if denom:
            d = team_total.get((season, week, team), 0)
            if not d:
                continue
            value = (got or 0) / d
        else:
            value = float(got or 0)
        out.setdefault(player, {})[(season, week)] = value
    return out


def _sufficient(by_player, min_weeks=MIN_WEEKS):
    """{player: vector} of additive sufficient statistics, plus raw counts.

    The vector is
        [pairs, Sx, Sy, Sxy, Sxx, Syy, obs, S1, S2, B]
    where the first six are the lag-1 pair sums over DEMEANED weekly values and
    the last four are the one-way variance decomposition over player-seasons:
    `obs` weeks, `S1` = sum of values, `S2` = sum of squares, `B` = sum over
    seasons of n_season * mean_season^2. Every element is a plain sum, which is
    what makes the bootstrap a matrix product.
    """
    import numpy as np
    vec, rows = {}, {}
    for player, weeks in by_player.items():
        by_season = {}
        for (season, week), value in weeks.items():
            by_season.setdefault(season, {})[week] = value
        v = np.zeros(16)
        n_obs = 0
        for season, wk in by_season.items():
            if len(wk) < min_weeks:
                continue
            values = [wk[w] for w in sorted(wk)]
            mean = sum(values) / len(values)
            dev = [x - mean for x in values]
            # Consecutive CALENDAR weeks only. A bye or a missed game leaves a
            # gap, and pairing across it measures persistence over two weeks
            # while calling it one.
            ordered = sorted(wk)
            for a, b in zip(ordered, ordered[1:]):
                if b != a + 1:
                    continue
                x, y = wk[a] - mean, wk[b] - mean
                v[0] += 1
                v[1] += x
                v[2] += y
                v[3] += x * y
                v[4] += x * x
                v[5] += y * y
                # the SAME pairs, undemeaned. Published beside the demeaned
                # figure because the gap between them is the finding: the
                # naive number is what "his target share is trending" rests
                # on, and it is almost entirely a between-player fact.
                p, q = wk[a], wk[b]
                v[10] += 1
                v[11] += p
                v[12] += q
                v[13] += p * q
                v[14] += p * p
                v[15] += q * q
            n = len(values)
            v[6] += n
            v[7] += sum(values)
            v[8] += sum(x * x for x in values)
            v[9] += n * mean * mean
            n_obs += n
        if v[0] >= 1 and n_obs > 0:
            vec[player] = v
            rows[player] = n_obs
    return vec, rows


def _pearson_at(v, k):
    n, sx, sy, sxy, sxx, syy = v[k], v[k+1], v[k+2], v[k+3], v[k+4], v[k+5]
    if n < 2:
        return None
    cov = sxy - sx * sy / n
    vx = sxx - sx * sx / n
    vy = syy - sy * sy / n
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def _pearson(v):
    return _pearson_at(v, 0)


def _naive(v):
    return _pearson_at(v, 10)


def _between(v):
    """Share of total variance that is between player-seasons."""
    n, s1, s2, b = v[6], v[7], v[8], v[9]
    if n < 2:
        return None
    total = s2 - s1 * s1 / n
    between = b - s1 * s1 / n
    if total <= 0:
        return None
    return max(0.0, min(1.0, between / total))


def compute(con, kind, season_from, season_to, draws=2000):
    """{family: Estimate} over the league."""
    from analytics.intervals import histogram_bootstrap
    vec, rows = _sufficient(weekly(con, kind, season_from, season_to))
    if len(vec) < MIN_PLAYERS:
        raise SystemExit("only %d players qualify for %s - refusing to publish "
                         "a league figure on that" % (len(vec), kind))
    return histogram_bootstrap(vec, {"within_lag1": _pearson,
                                     "naive_lag1": _naive,
                                     "between": _between},
                               draws=draws, rows_by_block=rows, subject=kind)


def publish(con, kinds=None, verbose=True):
    written = {}
    for kind in (kinds or KINDS):
        lo, hi, note = metrics.derive_range(con, metric_for("within_lag1", kind))
        got = compute(con, kind, lo, hi)
        for family, est in got.items():
            m = metric_for(family, kind)
            written[m.key] = metrics.publish(con, m, [("_league", "", est)], lo, hi)
            if verbose:
                print("  %-40s %d-%d  %+.4f [%+.4f, %+.4f]  n=%d players, "
                      "%d player-weeks"
                      % (m.key, lo, hi, est.est, est.lo, est.hi, est.n, est.rows),
                      flush=True)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--kind", action="append", choices=sorted(KINDS))
    a = ap.parse_args(argv)
    if a.publish:
        publish(paths.connect(), a.kind)
        return 0
    con = paths.connect(read_only=True)
    rows = con.execute(
        "SELECT v.metric, m.season_from, m.season_to, v.est, v.lo, v.hi, v.n, "
        "v.rows FROM f_metric_values v JOIN f_metrics m USING (metric) "
        "WHERE v.metric LIKE 'usage_stability.%' ORDER BY v.metric").fetchall()
    if not rows:
        raise SystemExit("nothing published - run --publish")
    print("%-40s %-10s %8s %20s %8s" % ("metric", "range", "est", "95%", "players"))
    for metric, lo, hi, est, a_, b_, n, r in rows:
        print("%-40s %d-%d %8.4f  [%+.4f, %+.4f] %8d"
              % (metric, lo, hi, est, a_, b_, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
