"""The feature catalogue: every condition a rule can use, and the seasons it
can be used on.

SECTION 6.4 IS THE RULE THIS MODULE EXISTS FOR. Every feature is computed in
the producer, in Python, AS OF KICKOFF, and stored as a column on the universe
table (`lab.features`, `lab.universe`). Nothing here, and nothing in a browser,
computes a rolling feature at query time. H1 died of look-ahead; a "last 5
games" taken from a table that contains the current game repeats it exactly.

THE RANGE IS DERIVED, NOT TYPED. A feature declares the survey COLUMNS it is
computed from, and `analytics.metrics.derive_range` - the analytics registry's
own function, not a second copy - turns the column survey into
`season_from` / `season_to` / a note naming the binding column. That range is
then intersected with what the PRICES cover (a prop feature over 1999 is no use
when the lines start in 2023), measured from the universe itself.

Features that cannot be supported are listed anyway, with `availability:
"none"` and the reason, so a builder greys them out with an explanation rather
than the rule silently returning nothing.
"""
from dataclasses import dataclass, field

# Survey dataset / column per prop market. `weekly_stats` is nflverse
# `stats_player_week`; the tackles market is the PINNED three-column sum
# (jobs.settle_outcomes.STAT_COLUMN), so it requires all three.
STAT_REQUIRES = {
    "receptions": (("weekly_stats", "receptions"),),
    "receiving_yards": (("weekly_stats", "receiving_yards"),),
    "rush_attempts": (("weekly_stats", "carries"),),
    "tackles_assists": (("weekly_stats", "def_tackles_solo"),
                        ("weekly_stats", "def_tackles_with_assist"),
                        ("weekly_stats", "def_tackle_assists")),
    "sacks": (("weekly_stats", "def_sacks"),),
}

PROP = ("prop",)
GAMES = ("spread", "total", "moneyline")
ALL = PROP + GAMES


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    group: str
    dtype: str                    # number | text | bool
    bet_types: tuple
    # "stat" = the market's own stat columns (STAT_REQUIRES); a tuple = fixed
    # survey requirements; () = computed from the universe/prices only.
    requires: object = ()
    availability: str = "historical"   # historical | none
    note: str = ""
    extra: dict = field(default_factory=dict)


FEATURES = (
    # --- price and line: computed by the engine from the chosen books' close
    Feature("price.line", "Line", "price", "number", ALL),
    Feature("price.p_devig", "De-vigged probability of the side bet", "price",
            "number", ALL),
    Feature("price.implied", "Implied probability at the posted price", "price",
            "number", ALL),
    Feature("price.hold", "Book hold (overround - 1), median over the books",
            "price", "number", ALL),
    Feature("price.books_quoting", "Books quoting this line at the close",
            "price", "number", ALL),
    # --- player form, strictly prior games, zero-filled from snap counts
    Feature("player.position", "Position (as of his last prior game)", "player",
            "text", PROP, (("weekly_stats", "position"),)),
    Feature("player.games_prior", "Prior games in the history window", "player",
            "number", PROP, "stat"),
    Feature("player.mean_l3", "Mean of the stat, last 3 games", "player",
            "number", PROP, "stat"),
    Feature("player.mean_l5", "Mean of the stat, last 5 games", "player",
            "number", PROP, "stat"),
    Feature("player.mean_l10", "Mean of the stat, last 10 games", "player",
            "number", PROP, "stat"),
    Feature("player.line_minus_mean_l3", "Line minus the L3 mean", "player",
            "number", PROP, "stat"),
    Feature("player.line_minus_mean_l5", "Line minus the L5 mean", "player",
            "number", PROP, "stat"),
    Feature("player.line_minus_mean_l10", "Line minus the L10 mean", "player",
            "number", PROP, "stat"),
    Feature("player.cleared_l3", "Prior games over this line, of the last 3",
            "player", "number", PROP, "stat"),
    Feature("player.cleared_l5", "Prior games over this line, of the last 5",
            "player", "number", PROP, "stat"),
    Feature("player.cleared_l10", "Prior games over this line, of the last 10",
            "player", "number", PROP, "stat"),
    Feature("player.streak", "Consecutive prior games over (+) or under (-) "
            "this line", "player", "number", PROP, "stat"),
    Feature("player.snap_share_l3", "Snap share in the stat's phase, last 3",
            "player", "number", PROP, (("snap_counts", "offense_pct"),
                                       ("snap_counts", "defense_pct"))),
    Feature("player.target_share_l3", "Target share, last 3 games", "player",
            "number", PROP, (("weekly_stats", "target_share"),)),
    # --- matchup, this season's prior weeks only
    Feature("opp.allowed_trailing", "Opponent's mean of this stat allowed to the "
            "position, prior weeks this season (>= 3 games)", "matchup",
            "number", PROP, "stat"),
    Feature("opp.allowed_rank_trailing", "Opponent rank on that, 1 = allows the "
            "least", "matchup", "number", PROP, "stat"),
    # --- game context: the game's own row, known at kickoff
    Feature("game.home", "Home team (the player's or the side's)", "game",
            "bool", ALL),
    Feature("game.spread", "Team's point spread (negative = favoured)", "game",
            "number", ALL),
    Feature("game.total", "Game total", "game", "number", ALL),
    Feature("game.team_implied_total", "Team implied total", "game", "number",
            PROP + ("spread", "moneyline")),
    Feature("game.rest_days", "Days since the team's previous game", "game",
            "number", PROP + ("spread", "moneyline")),
    Feature("game.division", "Division game", "game", "bool", ALL),
    Feature("game.primetime", "Kickoff 19:00 ET or later", "game", "bool", ALL),
    Feature("game.dome", "Dome or closed roof", "game", "bool", ALL),
    Feature("game.grass", "Natural grass", "game", "bool", ALL),
    Feature("game.week", "Week of the season", "game", "number", ALL),
    # --- listed so the builder can grey them out WITH a reason
    Feature("model.p", "Model probability (walk-forward)", "model", "number",
            PROP, availability="none",
            note="walk-forward predictions are computed in memory by "
                 "research/walkforward.py and never persisted; the stored "
                 "predictions are the live model, in-sample on history"),
    Feature("model.gap", "Model minus market", "model", "number", PROP,
            availability="none", note="as model.p"),
    Feature("weather.temp_forecast", "Forecast kickoff temperature", "weather",
            "number", ALL, availability="none",
            note="no forecast weather is joined to the universe; observed "
                 "weather would be look-ahead for a pre-kickoff rule"),
    Feature("weather.wind_forecast", "Forecast kickoff wind", "weather",
            "number", ALL, availability="none", note="as weather.temp_forecast"),
    Feature("move.since_open", "Line change since open", "movement", "number",
            ALL, availability="none",
            note="2026 forward capture only; history has one close per claim "
                 "(partial 2023 opens are not in the universe)"),
    Feature("move.last_24h", "Line change in the last 24 hours", "movement",
            "number", ALL, availability="none", note="as move.since_open"),
)

BY_KEY = {f.key: f for f in FEATURES}


class _Req:
    """The slice of `analytics.metrics.Metric` that `derive_range` reads.

    derive_range touches `.key`, `.requires` and `.floor_season` /
    `.floor_reason` only. A full Metric would drag in validation that is about
    publishing a metric (block, basis, shares_denominator) and says nothing
    about a condition, so this carries exactly what the function consumes and
    `tests/test_lab_catalogue.py` pins that it is enough.
    """
    __slots__ = ("key", "requires", "floor_season", "floor_reason")

    def __init__(self, key, requires):
        self.key = key
        self.requires = tuple(tuple(r) for r in requires)
        self.floor_season = 1999
        self.floor_reason = ""


def requirements(feature, markets):
    if feature.requires == "stat":
        out = []
        for m in markets:
            out.extend(STAT_REQUIRES.get(m, ()))
        return tuple(dict.fromkeys(out))
    return tuple(feature.requires)


def _one(analytics_con, key, req, lo, hi, note, derive):
    """Season range for one requirement set, intersected with the prices."""
    if req:
        try:
            s_from, s_to, s_note = derive(analytics_con, _Req(key, req))
        except SystemExit as e:        # derive_range refuses by SystemExit
            return {"availability": "none", "season_from": None, "season_to": None,
                    "note": "range not derivable from the column survey: %s" % e}
        lo, hi = max(lo, s_from), min(hi, s_to)
        note = "%s; survey: %s" % (note, s_note)
    if lo > hi:
        return {"availability": "none", "season_from": None, "season_to": None,
                "note": "no season has both prices and the inputs (%s)" % note}
    return {"availability": "historical", "season_from": lo, "season_to": hi,
            "note": note}


def ranges(analytics_con, price_coverage, markets_by_bet_type, derive=None):
    """{key: feature dict with season_from, season_to, availability, note}.

    `price_coverage` is {bet_type: (first, last)} measured from the universe.
    `markets_by_bet_type` names which markets a stat-derived feature is ranged
    over (the range of a stat feature is the tightest over those markets).
    `derive` defaults to `analytics.metrics.derive_range` - injectable so the
    tests can drive it without a scanned analytics.db, not so a caller can
    substitute a second implementation.
    """
    if derive is None:
        from analytics.metrics import derive_range as derive
    out = {}
    for f in FEATURES:
        d = {"key": f.key, "label": f.label, "group": f.group, "dtype": f.dtype,
             "bet_types": list(f.bet_types), "availability": f.availability,
             "note": f.note, "season_from": None, "season_to": None}
        if f.availability == "none":
            out[f.key] = d
            continue
        covered = [price_coverage[b] for b in f.bet_types if b in price_coverage]
        if not covered:
            d.update(availability="none", note="no prices in the universe for %s"
                     % ", ".join(f.bet_types))
            out[f.key] = d
            continue
        lo = min(c[0] for c in covered)
        hi = max(c[1] for c in covered)
        note = "prices %d-%d" % (lo, hi)
        if f.requires == "stat":
            # PER MARKET. A stat feature over receptions has nothing to do with
            # the tackles columns, and ranging it over the union let one
            # market's refusal grey out every other market's feature.
            prop_markets = markets_by_bet_type.get("prop", ())
            per = {m: _one(analytics_con, f.key, STAT_REQUIRES.get(m, ()), lo, hi,
                           note, derive) for m in prop_markets}
            d["by_market"] = per
            ok = [v for v in per.values() if v["availability"] != "none"]
            if ok:
                d.update(season_from=min(v["season_from"] for v in ok),
                         season_to=max(v["season_to"] for v in ok),
                         note="per market: " + "; ".join(
                             "%s %s" % (m, ("%d-%d" % (v["season_from"], v["season_to"])
                                            if v["availability"] != "none" else "unavailable"))
                             for m, v in sorted(per.items())))
            else:
                d.update(availability="none", note="no market supports it")
        else:
            d.update(_one(analytics_con, f.key, tuple(f.requires), lo, hi, note, derive))
        out[f.key] = d
    return out
