"""Three denominators, never divided across tiers (unit a-46, DECISIONS-2026-09-28 §P).

THE BUG THIS EXISTS FOR. The site printed "150 of 3,988 have a whole distribution
this week · 3.8% PRICED". 3,988 is every player with offensive usage across 28
seasons; 150 is one week. The ratio reads as "this site prices 3.8% of the
league" and is wrong in the direction that makes the site look worse. "7,309
games logged" beside a week-3 figure is the same defect.

THE FIX. Every count a page can print is published at a named TIER, carrying
the span it covers, so a page cannot print one without saying which it is:

    archive   every player / game in the archive. A depth-of-history claim.
    season    the current season.
    week      the current period. THE ONLY TIER THAT MAY BE DIVIDED, and the one
              percentage it supports (`share_priced`) is computed here, never by
              a page.

WHY THE WEEK DENOMINATOR IS "EXPECTED", NOT "PLAYED". A posted market is built
only for a game that has not kicked off (export_web.build_market), so a priced
player has, by construction, NOT played in the current period yet: "players who
played this week, and how many of them are priced" is 0 of N on every run. The
priced count and its denominator must describe the SAME games, so the week tier
divides over the period's OPEN games (not yet kicked off):

    expected         players who recorded participation for their team in that
                     team's most recent final game, for every team with an open
                     game this period - who is expected to play in the games
                     that can carry a market
    priced           players with a posted market this period
    priced_expected  priced AND expected; the numerator of share_priced, so the
                     share is a proportion of one set and can never exceed 1
    played           players who recorded participation in the period's FINAL
                     games so far. Descriptive; it is a different set of games
                     from `priced` and nothing here divides by it.

SPORT-AGNOSTIC. Which rows count as "participation" is the sport's call, not this
module's: `Participation` is the sport config, and the NFL's (offensive usage: a
target, carry or pass attempt) lives beside the exporter that owns it. The same
object drives `export_web.player_scope`, so the archive tier and the player index
cannot come to disagree about what a player is.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Participation:
    """What makes a row count as a player taking part - the sport config."""
    noun: str                  # "offensive usage", "a published stat"
    definition: str            # the sentence a page may quote verbatim
    columns: tuple             # any of these > 0 counts
    scope_season_types: tuple  # season types the archive and season tiers admit
    id_col: str = "player_id"
    period_col: str = "period"
    team_col: str = "team"

    def used(self, row):
        # ANY non-zero column, not a positive sum (c-18). The two agree on counts,
        # which cannot go negative; they part on yardage, which can, and the
        # all-positions config carries return yards. A sum also lets one column
        # cancel another. `!= 0` is also exactly the rule a-14's extended scope
        # used (`any(r.get(c))`), so moving that scope onto this object changes
        # no player's membership.
        return any(_num(row.get(c)) != 0 for c in self.columns)


def _num(v):
    return 0 if v is None else v


def _order(g):
    return (g["season"], g["period"] if g["period"] is not None else -1)


def compute(cfg, rows, games, current, archive_ids, priced_ids, now_ts, team_of=None):
    """-> the `denominators` block.

    rows         player-period rows (dicts) carrying cfg.id_col, `season`,
                 cfg.period_col, `season_type`, cfg.team_col and cfg.columns
    games        iterable of {season, period, final: bool, kickoff_ts, teams: (a, b)}
    current      {"season", "period": {"index", "label", "key"}}
    archive_ids  the players the archive publishes (the player index)
    priced_ids   players with a posted market in the current period
    team_of      row -> team as `games` spells it (franchise folding); default the column
    """
    team_of = team_of or (lambda r: r.get(cfg.team_col))
    season, index = current["season"], current["period"]["index"]
    archive_ids, priced_ids = set(archive_ids), set(priced_ids)
    games = list(games)
    rows = [r for r in rows if cfg.used(r)]

    # ---- archive ------------------------------------------------------------
    scoped = [r for r in rows if r["season_type"] in cfg.scope_season_types
              and r[cfg.id_col] in archive_ids]
    a_seasons = sorted({r["season"] for r in scoped})
    final = [g for g in games if g["final"]]
    g_seasons = sorted({g["season"] for g in final})

    # ---- season -------------------------------------------------------------
    # The archive's own players, restricted to this season: a player the export
    # does not publish (no resolvable name) is in neither tier, so the season
    # count is a subset of the archive count by construction. Refusing instead
    # would stop the weekly export over one nameless rookie.
    season_ids = {r[cfg.id_col] for r in scoped if r["season"] == season}
    this_season = [g for g in games if g["season"] == season]

    # ---- week ---------------------------------------------------------------
    period = [g for g in this_season if g["period"] == index]
    open_ = [g for g in period if not g["final"] and g["kickoff_ts"] is not None
             and g["kickoff_ts"] > now_ts]
    wk_final = [g for g in period if g["final"]]
    played_ids = {r[cfg.id_col] for r in rows
                  if r["season"] == season and r[cfg.period_col] == index}
    open_teams = {t for g in open_ for t in g["teams"]}
    last = {}
    for g in final:
        for t in g["teams"]:
            if t in open_teams and (t not in last or _order(g) > last[t]):
                last[t] = _order(g)
    expected = {r[cfg.id_col] for r in rows
                if team_of(r) in last
                and (r["season"], r[cfg.period_col]) == last[team_of(r)]}
    priced_expected = priced_ids & expected

    return {
        "participation": {"noun": cfg.noun, "definition": cfg.definition},
        "archive": {
            "divisible": False,
            "span": {"season_from": a_seasons[0] if a_seasons else None,
                     "season_to": a_seasons[-1] if a_seasons else None,
                     "seasons": len(a_seasons)},
            "players": len(archive_ids),
            "games": {"final": len(final),
                      "season_from": g_seasons[0] if g_seasons else None,
                      "season_to": g_seasons[-1] if g_seasons else None},
        },
        "season": {
            "divisible": False,
            "span": {"season": season},
            "players": len(season_ids),
            "games": {"final": sum(1 for g in this_season if g["final"]),
                      "scheduled": len(this_season)},
        },
        "week": {
            "divisible": True,
            "span": {"season": season, "period": dict(current["period"])},
            "games": {"scheduled": len(period), "final": len(wk_final), "open": len(open_),
                      "awaiting_final": len(period) - len(wk_final) - len(open_)},
            "players": {"played": len(played_ids), "expected": len(expected),
                        "priced": len(priced_ids), "priced_expected": len(priced_expected)},
            "share_priced": (round(len(priced_expected) / len(expected), 4) if expected else None),
        },
    }
