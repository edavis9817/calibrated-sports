"""4. The air-yard DISTRIBUTION, not its mean.

    python -m analytics.airyards --publish
    python -m analytics.airyards --show 00-0036355

THE QUESTION. A 10-yard aDOT built from screens and go routes is a different
player from a flat 10, and the mean cannot tell them apart. Their catch rates,
their yardage variance and the shape of their prop distribution all differ, and
one of them is a completely different bet at the same posted line.

WHAT IS PUBLISHED.

  bins       the share of his targets in each depth band, with an interval each.
             THIS IS THE SHAPE, and it is the deliverable. Bands are behind the
             line of scrimmage, 0-4, 5-9, 10-14, 15-19, 20-29, 30+.
  quantiles  q25, q50, q75, q90 of the air-yard distribution.
  polarity   P(behind the line) + P(20 or deeper). The one-number summary of
             the thing the mean hides: a flat-10 player scores near zero, a
             screen-and-deep player scores high. Two players can match on aDOT
             and differ by 30 points of polarity.

WHY POLARITY AND NOT A KURTOSIS OR A DIP TEST. Both would be defensible and
neither is legible on a page. Polarity is a share of targets, so it carries a
Wilson-style interval honestly, and a reader can check it against the bins
printed right beside it. A statistic nobody can audit against the numbers next
to it is a statistic that gets quoted wrong.

RANGE. Air yards begin 2006. The RECEIVER arm is bounded at 2009, because a
target is not attributable on an incompletion for 2003-2008 and a shape built
only from completions is a shape of catches, not of targets - which is exactly
the bias the metric exists to expose. Both bounds are derived from the survey.

`n` IS GAMES, not targets. A player's targets within one game share a game plan.
"""
import argparse
import sys

from analytics import metrics, paths
from analytics.intervals import block_bootstrap

MIN_GAMES = 8
DEEP = 20

# (label, lo, hi) with hi exclusive; None is unbounded.
BINS = (("behind_los", None, 0), ("0_4", 0, 5), ("5_9", 5, 10),
        ("10_14", 10, 15), ("15_19", 15, 20), ("20_29", 20, 30),
        ("30_plus", 30, None))

QUANTILES = (25, 50, 75, 90)

ROLES = {
    "receiver": (("pbp", "air_yards", "pass_attempt"),
                 ("pbp", "receiver_player_id", "incomplete_pass")),
    "passer": (("pbp", "air_yards", "pass_attempt"),
               ("pbp", "passer_player_id", "pass_attempt")),
}


def metric_for(role, family):
    unit = {
        "bins": "share of targets in each air-yard band",
        "quantiles": "air yards at the named percentile of the target distribution",
        "polarity": ("share of targets behind the line of scrimmage plus share "
                     "20 yards or deeper - what an aDOT mean hides"),
    }[family]
    return metrics.Metric(
        key="air_yards.%s.%s" % (family, role),
        label="Air-yard %s, %s" % (family, role),
        unit=unit, subject_type="player", block="game", basis="pbp",
        availability="current",
        slice_kind={"bins": "air_yard_bin", "quantiles": "percentile",
                    "polarity": ""}[family],
        requires=ROLES[role])


# Air yards are INTEGERS in [-93, 78] - measured on the real archive, 0 of
# 712,224 rows fractional - so a game's targets compress losslessly into a
# fixed-width count vector and the whole bootstrap becomes a matrix product.
# See `analytics.intervals.histogram_bootstrap` for why that mattered.
AY_MIN, AY_MAX = -100, 100
AY_WIDTH = AY_MAX - AY_MIN + 1


def _hist_by_player(con, role, season_from, season_to):
    """{player: {game: histogram over integer air yards}}."""
    import numpy as np
    sql = ("SELECT player_id, game_id, air_yards FROM f_play_usage "
           "WHERE role=? AND season BETWEEN ? AND ? AND air_yards IS NOT NULL")
    if role == "receiver":
        sql += " AND is_target=1"
    out = {}
    for player, game, ay in con.execute(sql, (role, season_from, season_to)):
        i = int(round(ay)) - AY_MIN
        if not 0 <= i < AY_WIDTH:
            continue                    # outside the measured range; recorded
        games = out.setdefault(player, {})
        h = games.get(game)
        if h is None:
            h = games[game] = np.zeros(AY_WIDTH, dtype=np.int64)
        h[i] += 1
    return out


def _bin_slices():
    """{bin label: (start index, stop index)} into the histogram."""
    out = {}
    for label, lo, hi in BINS:
        a = 0 if lo is None else lo - AY_MIN
        b = AY_WIDTH if hi is None else hi - AY_MIN
        out[label] = (max(0, a), min(AY_WIDTH, b))
    return out


def _bin_of(ay):
    for label, lo, hi in BINS:
        if (lo is None or ay >= lo) and (hi is None or ay < hi):
            return label
    return BINS[-1][0]


def _quantile_from_hist(hist, q):
    """The q-th percentile of the values a histogram counts.

    Exact for integer data, which is what this is - no interpolation
    convention to disagree with `numpy.percentile`, because with a count
    vector the answer is simply the value at the crossing.
    """
    import numpy as np
    total = hist.sum()
    if total == 0:
        return None
    cut = q / 100.0 * total
    idx = int(np.searchsorted(np.cumsum(hist), cut, side="left"))
    return float(min(idx, AY_WIDTH - 1) + AY_MIN)


def _statistics():
    """{slice name: fn(histogram) -> float}. One dict, so every statistic a
    player gets is computed on the SAME resamples."""
    stats = {}
    for label, (a, b) in _bin_slices().items():
        stats["bin:" + label] = (
            lambda h, a=a, b=b: (h[a:b].sum() / h.sum()) if h.sum() else None)
    for q in QUANTILES:
        stats["q:q%d" % q] = lambda h, qq=q: _quantile_from_hist(h, qq)
    behind = (0, -AY_MIN)                       # air yards < 0
    deep = (DEEP - AY_MIN, AY_WIDTH)            # air yards >= DEEP
    stats["polarity:"] = (
        lambda h: ((h[behind[0]:behind[1]].sum() + h[deep[0]:deep[1]].sum())
                   / h.sum()) if h.sum() else None)
    return stats


def compute(con, role, season_from, season_to, min_games=MIN_GAMES, draws=2000):
    """{family: [(player, slice, Estimate)]}."""
    from analytics.intervals import histogram_bootstrap
    stats = _statistics()
    out = {"bins": [], "quantiles": [], "polarity": []}
    family_of = {"bin": "bins", "q": "quantiles", "polarity": "polarity"}
    for player, by_game in _hist_by_player(con, role, season_from, season_to).items():
        if len(by_game) < min_games:
            continue
        got = histogram_bootstrap(by_game, stats, draws=draws, subject=player)
        for key, est in got.items():
            if est.est is None:
                continue
            kind, _sep, slice_key = key.partition(":")
            out[family_of[kind]].append((player, slice_key, est))
    return out


def publish(con, roles=None, verbose=True):
    written = {}
    for role in (roles or ROLES):
        # The range is derived once per ROLE and applied to all three families:
        # they read the same columns, so three separate derivations would be
        # three chances for them to disagree about the same fact.
        lo, hi, note = metrics.derive_range(con, metric_for(role, "bins"))
        families = compute(con, role, lo, hi)
        for family, rows in families.items():
            m = metric_for(role, family)
            written[m.key] = metrics.publish(con, m, rows, lo, hi)
            if verbose:
                print("  %-34s %d-%d  %d values" % (m.key, lo, hi, written[m.key]),
                      flush=True)
        if verbose:
            print("   %s: %s" % (role, note), flush=True)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--role", action="append", choices=sorted(ROLES))
    ap.add_argument("--show", help="a gsis_id")
    ap.add_argument("--polar", type=int, default=0,
                    help="the N most and least polarised receivers")
    a = ap.parse_args(argv)
    if a.publish:
        publish(paths.connect(), a.role)
        return 0
    con = paths.connect(read_only=True)
    if a.show:
        rows = con.execute(
            "SELECT metric, slice, est, lo, hi, n FROM f_metric_values "
            "WHERE subject_id=? AND metric LIKE 'air_yards.%' "
            "ORDER BY metric, slice", (a.show,)).fetchall()
        if not rows:
            raise SystemExit("nothing published for %r" % a.show)
        for metric, sl, est, lo, hi, n in rows:
            print("%-32s %-12s %7.3f [%7.3f, %7.3f]  n=%d"
                  % (metric, sl or "-", est, lo, hi, n))
        return 0
    if a.polar:
        for direction, order in (("most polarised", "DESC"),
                                 ("flattest", "ASC")):
            rows = con.execute(
                "SELECT subject_id, est, lo, hi, n FROM f_metric_values "
                "WHERE metric='air_yards.polarity.receiver' "
                "ORDER BY est %s LIMIT ?" % order, (a.polar,)).fetchall()
            print("\n%s (share behind the line OR 20+ deep)" % direction)
            for s, est, lo, hi, n in rows:
                print("  %-14s %.3f [%.3f, %.3f]  n=%d games" % (s, est, lo, hi, n))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
