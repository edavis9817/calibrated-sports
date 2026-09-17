"""1. Game-script elasticity: per-player usage when trailing versus leading.

    python -m analytics.script --publish
    python -m analytics.script --show 00-0036355

THE QUESTION. A receiver's target count varies more with the scoreboard than
with anything about the receiver, and almost nobody splits it per player. A
back who gets 18 carries with a lead and 7 chasing is a different bet from one
who gets 13 either way, and their season averages are identical.

THE MEASURE. Per player and usage kind, the share of the team's plays that go
to him, computed separately inside each script bucket, and the ELASTICITY: his
usage share when the team trails by 7+ minus his share when it leads by 7+.

    positive   used MORE when trailing  (a passing-game piece)
    negative   used MORE when leading   (a closer)

A SHARE, NOT A COUNT, AND THAT IS THE WHOLE POINT. A trailing team runs more
plays, so raw counts rise for everyone when behind and the "finding" is that
losing teams throw. The share removes it: the denominator is his own team's
plays of that kind in that bucket, in that game.

THE INTERVAL IS A BLOCK BOOTSTRAP OVER GAMES. A player's plays inside one game
share a game plan, a script and an opponent; treating 40 targets across 8 games
as 40 independent draws makes the interval roughly sqrt(5) too narrow. `n` is
GAMES. A player who has never trailed by 7+ in 5 different games gets no
elasticity - see `MIN_GAMES`.

THE RANGE IS DERIVED, NOT TYPED. Carries and receptions run the whole archive;
the targets arm is bounded by the 2003-2008 hole in `receiver_player_id`, and
`analytics.metrics.derive_range` reads that from the survey rather than taking
a constant's word for it.
"""
import argparse
import sys

from analytics import intervals, metrics, paths
from analytics.intervals import histogram_bootstrap

# Fewer than this and the interval is not read. Brief 020: a block bootstrap
# over three identical outcomes returns a tight interval that "excludes zero",
# and 15 of that brief's 17 zero-excluding intervals were exactly that.
MIN_GAMES = 5

TRAILING, LEADING = "trailing_7plus", "leading_7plus"

KINDS = {
    # kind -> (role, the player's component, the team's denominator component)
    "targets": ("receiver", "is_target", "is_target"),
    "receptions": ("receiver", "is_reception", "is_reception"),
    "carries": ("rusher", "is_carry", "is_carry"),
}

# A requirement is (dataset, column, condition). THE CONDITION IS THE POINT.
# `receiver_player_id` over all plays reads 56% of reference in 2003-2008 and
# clears every threshold; over INCOMPLETIONS - the rows where a target is at
# risk of going unattributed - it reads 0.6% against 82.7% and the bound is
# unmissable. Receptions do not need it: the receiver is named on 100% of
# completions in all 28 seasons, so that arm runs the whole archive.
REQUIRES = {
    "targets": (("pbp", "receiver_player_id", "incomplete_pass"),
                ("pbp", "receiver_player_id", "pass_attempt")),
    "receptions": (("pbp", "receiver_player_id", "complete_pass"),),
    "carries": (("pbp", "rusher_player_id", "rush_attempt"),),
}


def metric_for(kind: str) -> metrics.Metric:
    return metrics.Metric(
        key="script_elasticity.%s" % kind,
        label="Game-script elasticity, %s" % kind,
        unit=("share of team %s when trailing by 7+, minus the same share "
              "when leading by 7+" % kind),
        subject_type="player", block="game", basis="pbp",
        availability="current", slice_kind="script_bucket",
        requires=REQUIRES[kind])


def _per_game_shares(con, kind, season_from, season_to):
    """{player: {game: (his_component, team_component)}} per script bucket.

    Two passes rather than a self-join: the team denominator is the same for
    every player on the team, and computing it per player would multiply the
    scan by the roster.
    """
    role, comp, team_comp = KINDS[kind]
    team = {}
    for game, tm, script, total in con.execute(
            "SELECT game_id, team, script, SUM(%s) FROM f_play_usage "
            "WHERE role=? AND season BETWEEN ? AND ? AND script IN (?,?) "
            "GROUP BY game_id, team, script" % team_comp,
            (role, season_from, season_to, TRAILING, LEADING)):
        team[(game, tm, script)] = total or 0
    out = {}
    for player, game, tm, script, got in con.execute(
            "SELECT player_id, game_id, team, script, SUM(%s) FROM f_play_usage "
            "WHERE role=? AND season BETWEEN ? AND ? AND script IN (?,?) "
            "GROUP BY player_id, game_id, team, script" % comp,
            (role, season_from, season_to, TRAILING, LEADING)):
        denom = team.get((game, tm, script), 0)
        if not denom:
            continue
        out.setdefault(player, {}).setdefault(script, {})[game] = (got or 0, denom)
    return out


def compute(con, kind, season_from, season_to, min_games=MIN_GAMES):
    """[(player, slice, Estimate)] - the two bucket shares and the elasticity.

    ALL THREE COME OUT OF ONE SET OF RESAMPLES. The elasticity is a statistic
    over the same summed vector as its two parts, not the difference of two
    separately bootstrapped means - brief 018: "quote the gap, bootstrapped as
    one quantity; the difference of two separately quoted means carries more
    confidence than the data supports."

    The vector per game is [trailing got, trailing denom, leading got, leading
    denom]. A game contributes to whichever buckets it contains, so a game
    played entirely level contributes zeros and drops out of both.
    """
    def elasticity(v):
        if not v[1] or not v[3]:
            return None
        return v[0] / v[1] - v[2] / v[3]

    def part(v, a, b):
        return (v[a] / v[b]) if v[b] else None

    out = []
    for player, by_script in _per_game_shares(
            con, kind, season_from, season_to).items():
        tr, ld = by_script.get(TRAILING, {}), by_script.get(LEADING, {})
        if len(tr) < min_games or len(ld) < min_games:
            continue
        vecs = {}
        for g, (got, denom) in tr.items():
            vecs.setdefault(g, [0.0, 0.0, 0.0, 0.0])[0] = got
            vecs[g][1] = denom
        for g, (got, denom) in ld.items():
            v = vecs.setdefault(g, [0.0, 0.0, 0.0, 0.0])
            v[2], v[3] = got, denom
        import numpy as np
        got = histogram_bootstrap(
            {g: np.array(v) for g, v in vecs.items()},
            {"": elasticity,
             TRAILING: lambda v: part(v, 0, 1),
             LEADING: lambda v: part(v, 2, 3)},
            rows_by_block={g: 1 for g in vecs}, subject=player)
        for slice_key, est in got.items():
            if est.est is not None:
                out.append((player, slice_key, est))
    return out


def publish(con, kinds=None, min_games=MIN_GAMES, verbose=True):
    written = {}
    for kind in (kinds or KINDS):
        m = metric_for(kind)
        lo, hi, note = metrics.derive_range(con, m)
        rows = compute(con, kind, lo, hi, min_games)
        written[kind] = metrics.publish(con, m, rows, lo, hi)
        if verbose:
            players = len({p for p, _s, _e in rows})
            print("  %-12s %d-%d  %d players, %d values   [%s]"
                  % (kind, lo, hi, players, written[kind], note), flush=True)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--kind", action="append", choices=sorted(KINDS))
    ap.add_argument("--show", help="a gsis_id")
    ap.add_argument("--top", type=int, default=0,
                    help="most and least script-elastic, by kind")
    a = ap.parse_args(argv)
    if a.publish:
        con = paths.connect()
        publish(con, a.kind)
        return 0
    con = paths.connect(read_only=True)
    if a.show:
        rows = con.execute(
            "SELECT metric, slice, est, lo, hi, n FROM f_metric_values "
            "WHERE subject_id=? AND metric LIKE 'script_elasticity.%' "
            "ORDER BY metric, slice", (a.show,)).fetchall()
        if not rows:
            raise SystemExit("nothing published for %r" % a.show)
        for metric, sl, est, lo, hi, n in rows:
            print("%-32s %-16s %+.4f [%+.4f, %+.4f]  n=%d games"
                  % (metric, sl or "ELASTICITY", est, lo, hi, n))
        return 0
    if a.top:
        for kind in sorted(KINDS):
            for direction, order in (("most trailing-leaning", "DESC"),
                                     ("most leading-leaning", "ASC")):
                rows = con.execute(
                    "SELECT subject_id, est, lo, hi, n FROM f_metric_values "
                    "WHERE metric=? AND slice='' AND n>=? "
                    "ORDER BY est %s LIMIT ?" % order,
                    ("script_elasticity.%s" % kind, MIN_GAMES, a.top)).fetchall()
                print("\n%s, %s" % (kind, direction))
                for s, est, lo, hi, n in rows:
                    print("  %-14s %+.4f [%+.4f, %+.4f]  n=%d" % (s, est, lo, hi, n))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
