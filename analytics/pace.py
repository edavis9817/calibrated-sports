"""2. Neutral-situation pace: team plays per game with garbage time excluded.

    python -m analytics.pace --publish
    python -m analytics.pace --season 2025

THE QUESTION. "Plays per game" is mostly a measure of how the games went. A team
that trailed all year ran more plays, faster, because it was losing; a team that
led ran fewer and slower to bleed clock. Both numbers describe the scoreboard,
not the offence. Excluding garbage time is what makes the number about the team.

NEUTRAL IS DEFINED IN `analytics.spine` AND ONLY THERE - win probability in
[0.20, 0.80], more than 2:00 left in the half, regulation, a run or a pass, no
kneels or spikes. It uses `wp`, the game-state win probability, NOT `vegas_wp`:
the latter carries the closing spread, so a heavy favourite is scored as
comfortable before a snap is taken.

TWO METRICS, BECAUSE THEY ANSWER DIFFERENT QUESTIONS AND MOVE OPPOSITE WAYS.

    seconds_per_play   how fast the offence snaps it. The pace proper.
    plays_per_game     how much of it there is. Partly still a scoreboard
                       measure even after the exclusion - a team blown out
                       every week has fewer NEUTRAL plays by construction -
                       so it is published with that said rather than quietly.

SECONDS ARE MEASURED WITHIN A DRIVE. The gap across a drive boundary contains
the other team's possession, so it is not this team's pace; those plays are
counted but not timed, which is why `timed_plays` is stored beside `plays`
instead of being assumed equal.

`n` IS GAMES. Plays inside a game share a script and an opponent.
"""
import argparse
import sys

from analytics import metrics, paths
from analytics.intervals import block_bootstrap

MIN_GAMES = 5
SITUATION = "neutral"

REQUIRES = (("pbp", "wp"), ("pbp", "game_seconds_remaining"),
            ("pbp", "play_type"))

KINDS = {
    "seconds_per_play": (
        "Neutral-situation seconds per play",
        "seconds between snaps within a drive, neutral situations only"),
    "plays_per_game": (
        "Neutral-situation plays per game",
        "run and pass plays in neutral situations, per game; still partly a "
        "scoreboard measure - a team blown out weekly has fewer by construction"),
}


def metric_for(kind, per_season):
    label, unit = KINDS[kind]
    return metrics.Metric(
        key="pace.%s%s" % (kind, ".by_season" if per_season else ""),
        label=label + (", by season" if per_season else ", pooled"),
        unit=unit, subject_type="team", block="game", basis="pbp",
        availability="current",
        slice_kind="season" if per_season else "",
        requires=REQUIRES)


def _rows(con, season_from, season_to):
    return con.execute(
        "SELECT team, season, game_id, plays, seconds, timed_plays "
        "FROM f_team_game_pace WHERE situation=? AND season BETWEEN ? AND ? "
        "AND season_type='REG'", (SITUATION, season_from, season_to)).fetchall()


def _sec_per_play(rows):
    secs = sum(r[0] for r in rows if r[0] is not None)
    n = sum(r[1] for r in rows if r[0] is not None)
    return (secs / n) if n else None


def _plays_per_game(rows):
    return sum(r[0] for r in rows) / len(rows) if rows else None


def compute(con, kind, season_from, season_to, per_season, min_games=MIN_GAMES):
    blocks = {}
    for team, season, game, plays, seconds, timed in _rows(con, season_from, season_to):
        key = (team, str(season) if per_season else "")
        if kind == "seconds_per_play":
            row = (seconds, timed)
        else:
            row = (plays,)
        blocks.setdefault(key, {}).setdefault(game, []).append(row)
    stat = _sec_per_play if kind == "seconds_per_play" else _plays_per_game
    out = []
    for (team, slice_key), by_game in sorted(blocks.items()):
        if len(by_game) < min_games:
            continue
        e = block_bootstrap(by_game, stat)
        if e.est is not None:
            out.append((team, slice_key, e))
    return out


def publish(con, verbose=True):
    written = {}
    for kind in KINDS:
        for per_season in (False, True):
            m = metric_for(kind, per_season)
            lo, hi, note = metrics.derive_range(con, m)
            rows = compute(con, kind, lo, hi, per_season)
            written[m.key] = metrics.publish(con, m, rows, lo, hi)
            if verbose:
                print("  %-34s %d-%d  %d values" % (m.key, lo, hi, written[m.key]),
                      flush=True)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--season", type=int)
    a = ap.parse_args(argv)
    if a.publish:
        publish(paths.connect())
        return 0
    con = paths.connect(read_only=True)
    sl = str(a.season) if a.season else ""
    for kind in ("seconds_per_play", "plays_per_game"):
        key = "pace.%s%s" % (kind, ".by_season" if a.season else "")
        rows = con.execute(
            "SELECT subject_id, est, lo, hi, n, rows FROM f_metric_values "
            "WHERE metric=? AND slice=? ORDER BY est LIMIT 8",
            (key, sl)).fetchall()
        if not rows:
            raise SystemExit("nothing published for %s slice %r - publish first"
                             % (key, sl))
        print("\n%s%s - fastest / most, first" % (key, " " + sl if sl else ""))
        for s, est, lo, hi, n, r in rows:
            print("  %-5s %8.2f [%7.2f, %7.2f]  n=%d games, %d rows"
                  % (s, est, lo, hi, n, r or 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
