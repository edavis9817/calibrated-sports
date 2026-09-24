"""The team unit table: neutral pass rate, EPA per play, and the pass rush.

    python -m analytics.team_units --publish
    python -m analytics.team_units --season 2026

Per team per regular season, each with an interval and `n`, from the counts in
`f_team_game_units` (built by `analytics.spine`). `n` IS GAMES: plays inside a
game share a script, an opponent and a weather, so they are not the sample.

THREE THINGS THIS DOES NOT DO, AND WHY.

1. **No pbp "pressure rate".** The play-by-play has no pressure column. The
   nearest thing it carries is a sack or a QB hit, and measured against charted
   pressure (`was_pressure`, participation) that is a different quantity, not a
   noisy copy of it: 2024 league-wide, 3,080 sacks-or-hits against 6,561 charted
   pressures on the same 21,141 dropbacks. Labelled "pressure rate" it would be
   about half the real figure on every page it appeared on. So there are two
   metrics with two names: `pressure_rate` from participation, which is
   `historical` because the feed refreshes only after the postseason, and
   `sack_or_hit_rate` from the play-by-play, which is `current`. The second
   says in its own unit that it is not the first, with the ratio between them
   computed at publish time rather than typed here.

2. **No per-week values.** A team-week is one game, and one game is one block:
   there is no between-game spread to estimate, so the interval is unbounded,
   which the export drops. A within-game bootstrap over plays would give a finite
   interval by treating plays as independent - the rows-as-sample error this
   package exists to refuse.

3. **No shrinkage, and no false narrowness either.** A team two games into a
   season gets a two-game interval, published wide. `MIN_GAMES` is 2 because
   one game has no interval at all, not because two is enough. The interval is
   `intervals.ratio_t`, a cluster-robust t over games, NOT the percentile
   bootstrap the rest of the package uses: over two blocks the bootstrap can
   only span the two games, and `research/a25_small_n_coverage.py` measured it
   covering the full-season value 51-53% of the time at n=2 while calling
   itself 95%. The t interval covers 94-96% there, by being as wide as two
   games deserve. Brief 020's rule still applies on the page: an interval over
   fewer than five blocks is shown, not read.

NEUTRAL IS `analytics.spine`'s DEFINITION, stated in each metric's unit from
`spine.neutral_definition()` - generated from the constants that apply it, so a
page cannot print a rule the number was not computed under.

NO SHARED RANDOM DRAWS BETWEEN SUBJECTS, because there are no draws: the t
interval is analytic, so 32 teams side by side on one index page carry none of
the common-random-number correlation `analytics/crn_check.py` measured.
"""
import argparse
import sys
from dataclasses import dataclass

from analytics import metrics, paths, spine
from analytics.intervals import ratio_t

MIN_GAMES = 2
SEASON_TYPE = "REG"
RATE = (0.0, 1.0)


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    unit: str            # may carry {neutral} and {proxy}, filled at publish
    side: str            # offense | defense
    situation: str       # all | neutral
    num: str             # f_team_game_units column
    den: str
    requires: tuple
    basis: str = "pbp"
    availability: str = "current"
    shares_denominator: str = None
    bounds: tuple = None # the range the quantity can take; RATE for a rate


_PBP_NEUTRAL = (("pbp", "wp"), ("pbp", "half_seconds_remaining"),
                ("pbp", "play_type"))

SPECS = (
    Spec("team_units.neutral_pass_rate.early_downs.by_season",
         "Neutral early-down pass rate, by season",
         "dropbacks (passes, sacks and scrambles) per 1st- and 2nd-down play "
         "in neutral situations, regular season. Pass rate over every play is "
         "mostly a measure of how the games went - a team that trailed all "
         "year threw because it was losing - so garbage time is excluded and "
         "3rd and 4th down, where distance dictates the call, are left out. "
         "{neutral}",
         "offense", "neutral", "early_dropbacks", "early_plays",
         _PBP_NEUTRAL + (("pbp", "pass"), ("pbp", "down")),
         shares_denominator="own", bounds=RATE),
    Spec("team_units.epa_per_play.offense.by_season",
         "Offensive EPA per play, by season",
         "expected points added per run or pass, every down and score state, "
         "regular season; higher is better",
         "offense", "all", "epa_sum", "epa_plays",
         (("pbp", "epa"), ("pbp", "play_type"))),
    Spec("team_units.epa_per_play.defense.by_season",
         "Defensive EPA per play allowed, by season",
         "expected points added by opponents per run or pass, every down and "
         "score state, regular season; LOWER is better for the defence",
         "defense", "all", "epa_sum", "epa_plays",
         (("pbp", "epa"), ("pbp", "play_type"))),
    Spec("team_units.epa_per_play.offense_neutral.by_season",
         "Offensive EPA per play in neutral situations, by season",
         "expected points added per run or pass in neutral situations, "
         "regular season; higher is better. {neutral}",
         "offense", "neutral", "epa_sum", "epa_plays",
         _PBP_NEUTRAL + (("pbp", "epa"),)),
    Spec("team_units.epa_per_play.defense_neutral.by_season",
         "Defensive EPA per play allowed in neutral situations, by season",
         "expected points added by opponents per run or pass in neutral "
         "situations, regular season; LOWER is better for the defence. "
         "{neutral}",
         "defense", "neutral", "epa_sum", "epa_plays",
         _PBP_NEUTRAL + (("pbp", "epa"),)),
    Spec("team_units.pressure_rate.defense.by_season",
         "Pressure rate generated, by season",
         "charted pressures per opponent dropback, regular season, over the "
         "dropbacks the participation feed charts. From participation, which "
         "refreshes only after the postseason, so it cannot describe the "
         "current season",
         "defense", "all", "pressures", "charted_dropbacks",
         (("participation", "was_pressure"), ("pbp", "pass")),
         basis="participation", availability="historical",
         shares_denominator="own", bounds=RATE),
    Spec("team_units.pressure_rate.offense.by_season",
         "Pressure rate allowed, by season",
         "charted pressures per own dropback, regular season, over the "
         "dropbacks the participation feed charts. From participation, which "
         "refreshes only after the postseason, so it cannot describe the "
         "current season",
         "offense", "all", "pressures", "charted_dropbacks",
         (("participation", "was_pressure"), ("pbp", "pass")),
         basis="participation", availability="historical",
         shares_denominator="own", bounds=RATE),
    Spec("team_units.sack_or_hit_rate.defense.by_season",
         "Sack-or-hit rate generated, by season",
         "opponent dropbacks ending in a sack or a QB hit, per opponent "
         "dropback, regular season. NOT a pressure rate: {proxy}",
         "defense", "all", "sack_or_hit", "dropbacks",
         (("pbp", "pass"), ("pbp", "sack"), ("pbp", "qb_hit")),
         shares_denominator="own", bounds=RATE),
    Spec("team_units.sack_or_hit_rate.offense.by_season",
         "Sack-or-hit rate allowed, by season",
         "own dropbacks ending in a sack or a QB hit, per dropback, regular "
         "season. NOT a pressure rate: {proxy}",
         "offense", "all", "sack_or_hit", "dropbacks",
         (("pbp", "pass"), ("pbp", "sack"), ("pbp", "qb_hit")),
         shares_denominator="own", bounds=RATE),
)


def proxy_sentence(con) -> str:
    """How far sack-or-hit sits from charted pressure, measured on the store.

    Computed on every publish from the seasons where both exist, so the unit
    states the gap the data shows today rather than the one this module's
    author saw. Returns a sentence that can say the two agree, if they ever do.
    """
    row = con.execute(
        "SELECT MIN(season), MAX(season), SUM(sack_or_hit), SUM(pressures), "
        "SUM(dropbacks), SUM(charted_dropbacks) FROM f_team_game_units "
        "WHERE situation='all' AND season_type=? AND pressures IS NOT NULL",
        (SEASON_TYPE,)).fetchone()
    lo, hi, soh, pr, db, cdb = row
    if not pr or not db or not cdb:
        return ("no season carries both, so the gap between them cannot be "
                "measured here")
    a, b = soh / db, pr / cdb
    ratio = a / b
    if abs(ratio - 1) < 0.05:
        verdict = "about the same size as charted pressure"
    else:
        verdict = "%.2f times the charted pressure rate" % ratio
    return ("over %d-%d regular seasons the league's sack-or-hit rate is "
            "%.3f of dropbacks against a charted pressure rate of %.3f - %s. "
            "A QB hurried without being hit is a pressure and is not counted "
            "here" % (lo, hi, a, b, verdict))


def metric_for(spec: Spec, con) -> metrics.Metric:
    unit = spec.unit.format(neutral=spine.neutral_definition(),
                            proxy=proxy_sentence(con) if "{proxy}" in spec.unit
                            else "")
    return metrics.Metric(
        key=spec.key, label=spec.label, unit=unit, subject_type="team",
        block="game", basis=spec.basis, availability=spec.availability,
        shares_denominator=spec.shares_denominator, slice_kind="season",
        requires=spec.requires)


def _blocks(con, spec: Spec, season_from, season_to):
    """{(team, season): {game_id: (num, den)}} for one spec."""
    who = "team" if spec.side == "offense" else "opponent"
    out = {}
    for team, season, game, num, den in con.execute(
            "SELECT %s, season, game_id, %s, %s FROM f_team_game_units "
            "WHERE situation=? AND season_type=? AND season BETWEEN ? AND ? "
            "AND %s IS NOT NULL AND %s IS NOT NULL"
            % (who, spec.num, spec.den, spec.num, spec.den),
            (spec.situation, SEASON_TYPE, season_from, season_to)):
        if team is None or not den:
            continue
        out.setdefault((team, str(season)), {})[game] = (float(num), float(den))
    return out


def compute(con, spec: Spec, season_from, season_to, min_games=MIN_GAMES):
    """[(team, season, Estimate)], a ratio of sums with a t interval by game."""
    out = []
    for (team, season), games in sorted(_blocks(con, spec, season_from,
                                                season_to).items()):
        if len(games) < min_games:
            continue
        est = ratio_t(games, bounds=spec.bounds)
        if est.est is not None:
            out.append((team, season, est))
    return out


def publish(con, verbose=True, specs=SPECS):
    con.executescript(spine.SCHEMA)
    written = {}
    for spec in specs:
        m = metric_for(spec, con)
        lo, hi, _note = metrics.derive_range(con, m)
        rows = compute(con, spec, lo, hi)
        written[m.key] = metrics.publish(con, m, rows, lo, hi)
        if verbose:
            print("  %-54s %d-%d  %5d values" % (m.key, lo, hi, written[m.key]),
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
    season = str(a.season or metrics.live_season(con))
    for spec in SPECS:
        rows = con.execute(
            "SELECT subject_id, est, lo, hi, n, rows FROM f_metric_values "
            "WHERE metric=? AND slice=? ORDER BY est DESC", (spec.key, season)
        ).fetchall()
        print("\n%s %s - %d teams" % (spec.key, season, len(rows)))
        if not rows:
            print("  nothing published for this season")
            continue
        for s, est, lo, hi, n, r in rows[:4] + rows[-2:]:
            print("  %-4s %7.3f [%7.3f, %7.3f]  n=%d games, %d plays"
                  % (s, est, lo, hi, n, r or 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
