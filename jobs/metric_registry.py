"""One number per metric, enforced (unit a-36, audit N-02).

    python -m jobs.metric_registry --dest <WEB_EXPORT_DIR>    # check served files on disk

THE BUG THIS EXISTS FOR. On 2026-09-26 the site printed its headline finding two
ways one click apart: the register (R18) said -2.43pp on 44,198 after the
settlement fix, while `/` and `/nfl/analytics` still said -1.40pp on 43,209 from a
hand-copied 2026-09-10 file. Method printed two calibration errors for one set -
one cited from DECISIONS.md, one computed. Every one of those numbers was
correct about SOMETHING; what failed is that nothing tied a figure to one place.

THE REGISTRY. `METRICS` maps a metric id to the ONE served file and JSON path
that owns it (`source`), plus every OTHER served location known to carry the same
number (`copies`). The sport manifest exports it (`metrics`), so the site renders
a registered figure by reading the owner rather than by typing it.

THE GATE. `check(files)` resolves every registered location and refuses when a
copy disagrees with its owner at the metric's published precision. Two escapes,
both loud:
  * a copy may carry `declared` - a written reason it is KNOWN to disagree. A
    declared copy that AGREES also fails: the declaration is stale, and a stale
    declaration is how a real disagreement later hides behind an old excuse.
  * nothing else. A location that does not resolve is a failure, never a skip -
    a gate that cannot find a value has checked nothing (CLAUDE.md, "a guard
    asserts only over the shapes it walks").

PATH GRAMMAR (the site implements the same, b-47). Dot-separated keys; `[N]` is
an array index; `[key=value]` selects the ONE array element whose `key` equals
`value` (compared as a string), and zero or two matches is an error. Examples:
`over_bias.interval_pp`, `hypotheses[id=R18].estimate`,
`by_stat[stat=sacks].estimate_pp`.

The result is a `GateReport`, which REFUSES truth-testing (CLAUDE.md, "a guard
returns the statement it approved"): read `.clean` and `.statement`.
"""
import argparse
import json
import os
import re
import sys

MARKET = "research/market_calibration.json"
SCORE = "research/calibration.json"
REGISTER = "research/hypotheses.json"
MANIFEST = "nfl/manifest.json"
SEASON = "season/nfl/division.json"
PROJECTION = "season/nfl/projection.json"
# a-63: the game forecast and its settlement record (jobs.game_export).
GAME_RECORD = "game/nfl/record.json"
GAME_FORECAST = "game/nfl/forecast.json"
# a-64: the spread's and the total's scoring records (jobs.game_matchup).
GAME_RECORD_SPREAD = "game/nfl/record_spread.json"
GAME_RECORD_TOTAL = "game/nfl/record_total.json"
# a-55: the projection's per-team rows are keyed by team, so the registry names
# the 32 published abbreviations (nflverse's, the site's team keys). A relocation
# or a renamed code fails the season gate loudly rather than dropping a team.
NFL_TEAMS = ("ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
             "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO",
             "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS")

# R10 was restated 2026-09-17 on the n=706 common set, when 229 week-1 Kalshi
# books were one-sided at the prediction instant. The Kalshi candle backfill
# (quotes.source 'backfill:kalshi_candles', ingested after 09-17) now supplies a
# two-sided quote at or before entry for all 935, so score.py's common set is 935
# and its figures moved. Which quote is the right entry price is a methodology
# decision nobody has taken; until it is, the two are published as what they are
# and this reason travels with them. Measured by a-36 on 2026-09-26.
R10_DECLARED = ("R10 is the 2026-09-17 restatement on n=706 (229 books one-sided at entry); "
                "research/calibration.json now scores n=935 because the Kalshi candle backfill "
                "supplies a two-sided entry quote for every prediction. Unresolved: which entry "
                "quote is correct (a-36).")


def _m(id, label, unit, decimals, source, copies=(), nullable=False):
    # `nullable`: null is a legitimate published state (a share with an empty
    # denominator), not a lost value. Everywhere else a null owner fails.
    return {"id": id, "label": label, "unit": unit, "decimals": decimals,
            **({"nullable": True} if nullable else {}),
            "source": {"file": source[0], "path": source[1]},
            "copies": [dict(file=c[0], path=c[1], **({"declared": c[2]} if len(c) > 2 else {}))
                       for c in copies]}


ANALYTICS_INDEX = "analytics/nfl/index.json"

# a-51. The opportunity residual's published families. `asof` is NOT here: the
# a-33 publish decision ships without the five as-of keys (9.6-15.0 MB each, no
# page reads them; `analytics.residual --no-asof`), and that decision is Ethan's
# to reverse, not this registry's.
RESIDUAL_FAMILIES = ("player", "per_game", "fit", "bands", "persistence")


def residual_metric_ids():
    """The published opportunity_residual metric ids, read off the producer's own
    `metric_for` rather than retyped - one definition of each id (a-51)."""
    from analytics.residual import PAIRINGS, metric_for
    return [metric_for(stat, fam).key for fam in RESIDUAL_FAMILIES for stat in PAIRINGS]


def analytics_file(metric_id):
    """Where analytics.export serves a metric - its own key rule, not retyped."""
    from analytics.export import PREFIX
    return "%s/%s.json" % (PREFIX, metric_id)


def _residual_rows():
    # One row per published figure a page prints beside the metric: the season
    # range. The OWNER is the metric's own file; the index entry that tells a page
    # the same range before it fetches the file is a COPY. The two are written by
    # one function today (analytics.export.build), and that is a fact about the
    # code, not a property of the files - the ngs_stability range disagreement
    # (CLAUDE.md, Claims) was two internally consistent halves of one publish.
    rows = []
    for mid in residual_metric_ids():
        for end, word in (("season_from", "First"), ("season_to", "Last")):
            rows.append(_m(f"{mid}.{end}", f"{word} season of {mid}", "season", 0,
                           (analytics_file(mid), end),
                           [(ANALYTICS_INDEX, f"metrics[metric={mid}].{end}")]))
    return rows


STATS = ("receiving_yards", "receptions", "tackles_assists", "rush_attempts", "sacks")
SEASONS = (2023, 2024, 2025)

METRICS = [
    # --- the closing sportsbook market, 2023-2025 (R18) --------------------
    _m("market.over_bias.estimate_pp", "Over-side pricing gap at the close", "pp", 2,
       (MARKET, "over_bias.estimate_pp"), [(REGISTER, "hypotheses[id=R18].estimate")]),
    _m("market.over_bias.interval_pp", "Over-side pricing gap, game-block 95% interval", "pp", 2,
       (MARKET, "over_bias.interval_pp"), [(REGISTER, "hypotheses[id=R18].interval")]),
    _m("market.over_bias.n", "Settled over-side props", "outcomes", 0,
       (MARKET, "population.n"), [(REGISTER, "hypotheses[id=R18].n")]),
    _m("market.over_bias.games", "Games", "games", 0,
       (MARKET, "population.games"), [(REGISTER, "hypotheses[id=R18].games")]),
    _m("market.over_bias.priced", "Priced over rate at the close", "probability", 4,
       (MARKET, "over_bias.priced")),
    _m("market.over_bias.realized", "Realized over rate", "probability", 4,
       (MARKET, "over_bias.realized")),
    _m("market.over_bias.boot_se_pp", "Game-block bootstrap standard error", "pp", 2,
       (MARKET, "over_bias.boot_se_pp")),
    _m("market.over_bias.z_null", "z under the null-variance se (outcomes treated as "
       "independent; prefer the interval)", "z", 1, (MARKET, "over_bias.z_null")),
    _m("market.close.ece", "Calibration error of the close, over side", "ECE", 4,
       (MARKET, "score.ece")),
    _m("market.close.brier", "Brier score of the close, over side", "Brier", 4,
       (MARKET, "score.brier")),
    _m("market.close.log_loss", "Log loss of the close, over side", "log loss", 4,
       (MARKET, "score.log_loss")),
    *[_m(f"market.over_bias.by_stat.{s}.estimate_pp", f"Over-side pricing gap, {s}", "pp", 2,
         (MARKET, f"by_stat[stat={s}].estimate_pp")) for s in STATS],
    *[_m(f"market.over_bias.by_season.{y}.estimate_pp", f"Over-side pricing gap, {y}", "pp", 2,
         (MARKET, f"by_season[season={y}].estimate_pp")) for y in SEASONS],
    # --- the model against Kalshi, 2026 week 1 (R10) ------------------------
    _m("model.brier_minus_market", "Brier(model) - Brier(Kalshi mid), week 1", "Brier", 4,
       (SCORE, "brier.model_minus_market.estimate"),
       [(REGISTER, "hypotheses[id=R10].estimate", R10_DECLARED)]),
    _m("model.brier_minus_market.interval", "Brier(model) - Brier(Kalshi mid), 95% interval",
       "Brier", 4, (SCORE, "brier.model_minus_market.interval"),
       [(REGISTER, "hypotheses[id=R10].interval", R10_DECLARED)]),
    _m("model.brier_minus_market.n", "Predictions scored on the common set", "predictions", 0,
       (SCORE, "n"), [(REGISTER, "hypotheses[id=R10].n", R10_DECLARED)]),
    _m("model.brier_minus_market.games", "Games in the common set", "games", 0,
       (SCORE, "games"), [(REGISTER, "hypotheses[id=R10].games")]),
    _m("model.brier_minus_naive", "Brier(model) - Brier(naive prior), week 1", "Brier", 4,
       (SCORE, "brier.model_minus_naive.estimate")),
    _m("model.brier_minus_naive.interval", "Brier(model) - Brier(naive prior), 95% interval",
       "Brier", 4, (SCORE, "brier.model_minus_naive.interval")),
    _m("model.brier", "Brier score, model, week 1", "Brier", 4, (SCORE, "brier.model")),
    _m("kalshi.brier", "Brier score, Kalshi mid, week 1", "Brier", 4, (SCORE, "brier.market")),
    _m("naive.brier", "Brier score, naive prior, week 1", "Brier", 4, (SCORE, "brier.naive")),
    _m("model.ece", "Calibration error, model, week 1", "ECE", 4,
       (SCORE, "ece.model"), [(SCORE, "series[name=model].ece")]),
    _m("kalshi.ece", "Calibration error, Kalshi mid, week 1", "ECE", 4,
       (SCORE, "ece.market"), [(SCORE, "series[name=market].ece")]),
    # --- walk-forward against the sportsbook close (R15) --------------------
    # Owned by the register until research/walkforward.py publishes a file; only
    # 2025 is a field today (2023 and 2024 live in R15's prose).
    _m("model.walkforward.brier_minus_close.2025",
       "Walk-forward Brier(model) - Brier(close), 2025", "Brier", 4,
       (REGISTER, "hypotheses[id=R15].estimate")),
    _m("model.walkforward.brier_minus_close.2025.interval",
       "Walk-forward Brier(model) - Brier(close), 2025, 95% interval", "Brier", 4,
       (REGISTER, "hypotheses[id=R15].interval")),
    _m("model.walkforward.brier_minus_close.2025.n", "Walk-forward outcomes, 2025", "outcomes", 0,
       (REGISTER, "hypotheses[id=R15].n")),
    # --- denominators (a-46, DECISIONS-2026-09-28 §P) ------------------------
    # Every player and game count at a named tier. The legacy unlabelled
    # `counts` fields are registered as COPIES of the tier they always were, so
    # the gate refuses the day "3,988" and the archive tier stop being one number.
    _m("coverage.players.archive", "Players with offensive usage, whole archive", "players", 0,
       (MANIFEST, "denominators.archive.players"), [(MANIFEST, "counts.players")]),
    _m("coverage.players.season", "Players with offensive usage, current season", "players", 0,
       (MANIFEST, "denominators.season.players")),
    _m("coverage.players.week.played", "Players with offensive usage in the current period's "
       "final games", "players", 0, (MANIFEST, "denominators.week.players.played")),
    _m("coverage.players.week.expected", "Players expected in the current period's open games",
       "players", 0, (MANIFEST, "denominators.week.players.expected")),
    _m("coverage.players.week.priced", "Players with a posted market, current period", "players",
       0, (MANIFEST, "denominators.week.players.priced"), [(MANIFEST, "counts.market")]),
    _m("coverage.players.week.priced_expected", "Priced players among those expected",
       "players", 0, (MANIFEST, "denominators.week.players.priced_expected")),
    _m("coverage.players.week.share_priced", "Share of expected players with a posted market",
       "proportion", 4, (MANIFEST, "denominators.week.share_priced"), nullable=True),
    _m("coverage.games.archive", "Games with a final score, whole archive", "games", 0,
       (MANIFEST, "denominators.archive.games.final"), [(MANIFEST, "counts.games")]),
    _m("coverage.games.season", "Games with a final score, current season", "games", 0,
       (MANIFEST, "denominators.season.games.final")),
    _m("coverage.games.week.final", "Games with a final score, current period", "games", 0,
       (MANIFEST, "denominators.week.games.final")),
    _m("coverage.games.week.open", "Games not yet kicked off, current period", "games", 0,
       (MANIFEST, "denominators.week.games.open")),
    # --- chance to win the division (a-41 built it, a-42 publishes it) -------
    # The walk-forward RECORD, not the 32 current probabilities: those are the
    # file's subject and change weekly; these are the figures a page quotes about
    # whether to believe it. Numbers only - the site's resolver reads a number or
    # an interval, so `record.verdict` and `display.show` are not registered.
    # The first calibration bin is registered on purpose: a-41's overconfidence
    # at the tails (0-10% forecast 1.9%, realised 3.2%) is the figure the Teams
    # page is instructed to print beside the bars.
    _m("season.division.record.division_seasons", "Division races scored walk-forward",
       "races", 0, (SEASON, "record.division_seasons")),
    _m("season.division.record.brier.model", "Brier score, division model, walk-forward",
       "Brier", 4, (SEASON, "record.brier.model")),
    _m("season.division.record.brier.standings_coin_flip",
       "Brier score, standings with coin-flip games, walk-forward", "Brier", 4,
       (SEASON, "record.brier.standings_coin_flip")),
    _m("season.division.record.brier.standings_leader",
       "Brier score, standings leader carried forward, walk-forward", "Brier", 4,
       (SEASON, "record.brier.standings_leader")),
    _m("season.division.record.vs_coin_flip", "Brier(model) - Brier(coin-flip standings)",
       "Brier", 4, (SEASON, "record.vs.standings_coin_flip.estimate")),
    _m("season.division.record.vs_coin_flip.interval",
       "Brier(model) - Brier(coin-flip standings), division-race block 95% interval",
       "Brier", 4, (SEASON, "record.vs.standings_coin_flip.interval")),
    _m("season.division.record.vs_leader", "Brier(model) - Brier(standings leader)",
       "Brier", 4, (SEASON, "record.vs.standings_leader.estimate")),
    _m("season.division.record.vs_leader.interval",
       "Brier(model) - Brier(standings leader), division-race block 95% interval",
       "Brier", 4, (SEASON, "record.vs.standings_leader.interval")),
    _m("season.division.calibration.low_tail.forecast",
       "Average forecast in the lowest probability bin", "probability", 4,
       (SEASON, "record.calibration[0].forecast")),
    _m("season.division.calibration.low_tail.realised",
       "Realised rate in the lowest probability bin", "probability", 4,
       (SEASON, "record.calibration[0].realised")),
    _m("season.division.as_of.games_played", "Regular-season games the forecast has seen",
       "games", 0, (SEASON, "as_of.games_played"),
       # a-55: the projection is built in the same run from the same games; a
       # file that disagrees saw a different season
       [(PROJECTION, "as_of.games_played")]),
    # --- the projected final record (a-55) ----------------------------------
    # Unlike the division file, the per-team headline IS registered: it is the
    # figure the team page prints beside the dashed line, one per team, and the
    # brief asked for every published figure. The distribution, path and
    # per-game rows are the file's body and are not.
    _m("season.projection.record.team_seasons", "Team-seasons scored walk-forward, projection",
       "team-seasons", 0, (PROJECTION, "record.team_seasons")),
    _m("season.projection.record.n_forecasts", "Projections scored walk-forward", "forecasts", 0,
       (PROJECTION, "record.n_forecasts")),
    *[_m(f"season.projection.record.rmse.{b}", f"RMS error of final wins, {w}, walk-forward",
         "wins", 2, (PROJECTION, f"record.rmse.{b}"))
      for b, w in (("model", "season model"), ("pace", "pace line"),
                   ("standings_coin_flip", "coin-flip standings"))],
    *[_m(f"season.projection.record.vs_{b}{sfx}",
         f"MSE(model) - MSE({w}), final wins{lab}", "wins^2", 2,
         (PROJECTION, f"record.vs.{b}.{fld}"))
      for b, w in (("pace", "pace line"), ("standings_coin_flip", "coin-flip standings"))
      for sfx, fld, lab in (("", "estimate", ""), (".interval", "interval",
                                                    ", season-block 95% interval"))],
    *[_m(f"season.projection.record.coverage{l}.{f}",
         f"Central {l}% interval, {w}, walk-forward", "share", 3,
         (PROJECTION, f"record.coverage.{l}.{f}"))
      for l in ("80", "95") for f, w in (("realised", "real final records inside"),
                                          ("mass", "simulations inside"))],
    _m("season.projection.sigma", "Rating uncertainty drawn per simulation", "rating points", 0,
       (PROJECTION, "method.sigma.value")),
    *[_m(f"season.projection.{t}.{f}", f"{t}: {w}", u, d,
         (PROJECTION, f"teams[team={t}].{path}"))
      for t in NFL_TEAMS
      for f, w, u, d, path in (
          ("mean", "projected final wins", "wins", 2, "projection.mean"),
          ("interval80", "projected final wins, central 80% interval", "wins", 1,
           "projection.interval80"),
          ("schedule_effect", "wins the remaining schedule adds against average opponents",
           "wins", 2, "remaining.schedule_effect"),
          ("difficulty_rank", "remaining schedule difficulty rank", "rank", 0,
           "remaining.difficulty_rank"))],
    # --- the opportunity residual (a-18 built it, a-21 fixed it, a-51 publishes it)
    *_residual_rows(),
    # --- the game forecast's settlement record (a-63) ---------------------------
    # The record's headline figures only. `market_comparison` is DELIBERATELY NOT
    # registered: this list is exported into the sport manifest, and a registered
    # figure is one a page is invited to render by resolving its path - the
    # opposite of the 2026-09-30 decision that it is stored and not displayed.
    # The forecast's `season_stage` repeats one stage's figures; which stage
    # depends on the week, so a static copy path cannot name it and
    # `jobs.game_export.stage_agrees` checks that pair instead.
    _m("game.record.games", "Games with a winner scored walk-forward, game forecast", "games",
       0, (GAME_RECORD, "population.games")),
    _m("game.record.brier.model", "Brier score of the game forecast, walk-forward", "Brier", 4,
       (GAME_RECORD, "model.brier")),
    *[_m(f"game.record.vs_{b}{sfx}", f"Brier(model) - Brier({w}), game forecast{lab}", "Brier",
         4, (GAME_RECORD, f"baselines[id={b}].d_brier.{fld}"))
      for b, w in (("home", "home team"), ("record", "better record"), ("elo_nomov", "plain Elo"))
      for sfx, fld, lab in (("", "estimate", ""),
                            (".interval", "interval", ", game-block 95% interval"))],
    *[_m(f"game.record.{st}.vs_elo_nomov{sfx}",
         f"Brier(model) - Brier(plain Elo), {w}{lab}", "Brier", 4,
         (GAME_RECORD, f"by_stage.{st}.vs.elo_nomov.d_brier.{fld}"))
      for st, w in (("weeks_1_4", "regular-season weeks 1-4"),
                    ("weeks_5_plus", "regular-season weeks 5+"))
      for sfx, fld, lab in (("", "estimate", ""),
                            (".interval", "interval", ", game-block 95% interval"))],
    # --- the spread's and the total's records (a-64) ---------------------------
    # Registered, `vs_close` included: a-64 asks each model number to travel with
    # the fact that it loses to the close. The matchup files copy these figures and
    # jobs.game_export.matchup_agrees refuses a copy that differs.
    _m("game.record_spread.games", "Games scored against the closing spread, spread model",
       "games", 0, (GAME_RECORD_SPREAD, "population.games")),
    _m("game.record_total.games", "Games scored against settlement, total model", "games", 0,
       (GAME_RECORD_TOTAL, "population.games")),
    *[_m(f"game.record_{mk}.vs_close{sfx}", f"Brier(model) - Brier(closing price), {w}{lab}",
         "Brier", 4, (f, f"vs_close.d_brier.{fld}"))
      for mk, w, f in (("spread", "spread model", GAME_RECORD_SPREAD),
                       ("total", "total model", GAME_RECORD_TOTAL))
      for sfx, fld, lab in (("", "estimate", ""),
                            (".interval", "interval", ", game-block 95% interval"))],
    *[_m(f"game.record_total.vs_{b}{sfx}", f"Brier(model) - Brier({w}), total model{lab}",
         "Brier", 4, (GAME_RECORD_TOTAL, f"against_baselines[id={b}].d_brier.{fld}"))
      for b, w in (("league", "league average total"), ("season_avg", "season averages"))
      for sfx, fld, lab in (("", "estimate", ""),
                            (".interval", "interval", ", game-block 95% interval"))],
]


def _files_of(m):
    return {m["source"]["file"]} | {c["file"] for c in m["copies"]}


# The gates. The research gate runs before the first write; the manifest
# gate runs on the built manifest before it is written; the season gate runs in
# `jobs.season_export` on the built file before it is written (a-42) - a
# different producer, so a different gate. A metric whose locations span two
# groups belongs to none and is refused at import - it could never be checked
# by any gate.
RESEARCH_FILES = frozenset({MARKET, SCORE, REGISTER})
RESEARCH_METRICS = [m for m in METRICS if _files_of(m) <= RESEARCH_FILES]
MANIFEST_METRICS = [m for m in METRICS if _files_of(m) <= {MANIFEST}]
SEASON_METRICS = [m for m in METRICS if _files_of(m) <= {SEASON, PROJECTION}]
# a-63: the fifth gate, run by `jobs.game_export` on the built files before any write.
GAME_METRICS = [m for m in METRICS if _files_of(m) <= {GAME_RECORD, GAME_FORECAST,
                                                       GAME_RECORD_SPREAD, GAME_RECORD_TOTAL}]
# a-51: the fourth gate, `analytics.export.gate`, on the built analytics tree
# before `--dest web` writes it (and in jobs.publish_preflight).
ANALYTICS_METRICS = [m for m in METRICS if all(f.startswith("analytics/") for f in _files_of(m))]
_ungated = [m["id"] for m in METRICS if m not in RESEARCH_METRICS
            and m not in MANIFEST_METRICS and m not in SEASON_METRICS
            and m not in ANALYTICS_METRICS and m not in GAME_METRICS]
if _ungated:
    raise ImportError(f"metrics no gate can check (files span both groups): {_ungated}")

FILES = sorted({m["source"]["file"] for m in METRICS}
               | {c["file"] for m in METRICS for c in m["copies"]})


class Unresolved(LookupError):
    pass


_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]|\[([^=\]]+)=([^\]]*)\]")


def parse(path):
    toks, pos = [], 0
    while pos < len(path):
        if path[pos] == ".":
            pos += 1
            continue
        m = _TOKEN.match(path, pos)
        if not m:
            raise ValueError(f"bad metric path {path!r} at {pos}")
        if m.group(1) is not None:
            toks.append(("key", m.group(1)))
        elif m.group(2) is not None:
            toks.append(("index", int(m.group(2))))
        else:
            toks.append(("select", m.group(3), m.group(4)))
        pos = m.end()
    return toks


def resolve(obj, path):
    cur = obj
    for t in parse(path):
        if t[0] == "key":
            if not isinstance(cur, dict) or t[1] not in cur:
                raise Unresolved(f"{path}: no key {t[1]!r}")
            cur = cur[t[1]]
        elif t[0] == "index":
            if not isinstance(cur, list) or t[1] >= len(cur):
                raise Unresolved(f"{path}: no index {t[1]}")
            cur = cur[t[1]]
        else:
            if not isinstance(cur, list):
                raise Unresolved(f"{path}: [{t[1]}={t[2]}] on a non-array")
            hits = [e for e in cur if isinstance(e, dict) and str(e.get(t[1])) == t[2]]
            if len(hits) != 1:
                raise Unresolved(f"{path}: [{t[1]}={t[2]}] matched {len(hits)}, need exactly 1")
            cur = hits[0]
    return cur


def _norm(v, decimals):
    """Value at the published precision. A list compares elementwise."""
    if isinstance(v, list):
        return [_norm(x, decimals) for x in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        r = round(float(v), decimals)
        return 0.0 if r == 0 else r        # -0.0 and 0.0 are one number
    return v


class GateReport:
    """What the metric gate approved. Refuses truth-testing: read .clean / .statement."""

    def __init__(self, checked, problems, declared, n_metrics=None):
        self.checked, self.problems, self.declared = checked, problems, declared
        self.clean = not problems
        n_metrics = len(METRICS) if n_metrics is None else n_metrics
        self.statement = (
            f"metric gate: {n_metrics} metrics, {checked} locations resolved, "
            f"{len(declared)} declared disagreement(s), "
            + ("0 undeclared disagreements" if self.clean
               else f"{len(problems)} PROBLEM(S):\n  " + "\n  ".join(problems)))

    def __bool__(self):
        raise TypeError("GateReport is not a boolean - read .clean and .statement")


def check(files, metrics=None):
    """`files` is {key: parsed object}. -> GateReport. Never raises on a
    disagreement; `require()` does."""
    metrics = METRICS if metrics is None else metrics
    problems, declared, checked = [], [], 0

    def get(file, path):
        if file not in files:
            raise Unresolved(f"{file} was not produced")
        return resolve(files[file], path)

    for m in metrics:
        d = m["decimals"]
        try:
            owner = _norm(get(m["source"]["file"], m["source"]["path"]), d)
            checked += 1
        except (Unresolved, ValueError) as e:
            problems.append(f"{m['id']}: owner {m['source']['file']}:{e}")
            continue
        if owner is None and not m.get("nullable"):
            problems.append(f"{m['id']}: owner {m['source']['file']}:{m['source']['path']} is null")
        for c in m["copies"]:
            where = f"{c['file']}:{c['path']}"
            try:
                copy = _norm(get(c["file"], c["path"]), d)
                checked += 1
            except (Unresolved, ValueError) as e:
                problems.append(f"{m['id']}: copy {c['file']}:{e}")
                continue
            if c.get("declared"):
                if copy == owner:
                    problems.append(f"{m['id']}: {where} is DECLARED to disagree but agrees "
                                    f"({copy!r}) - remove the stale declaration")
                else:
                    declared.append(f"{m['id']}: {where} {copy!r} vs owner {owner!r}")
            elif copy != owner:
                problems.append(f"{m['id']}: {where} = {copy!r} but "
                                f"{m['source']['file']}:{m['source']['path']} = {owner!r}")
    return GateReport(checked, problems, declared, len(metrics))


class MetricDisagreement(RuntimeError):
    pass


def require(files, metrics=None):
    """The export gate. -> the GateReport it approved, or raises."""
    rep = check(files, metrics)
    if not rep.clean:
        raise MetricDisagreement("refusing to export - " + rep.statement)
    return rep


def manifest_block():
    """What the sport manifest publishes. A copy of METRICS, so no caller can
    mutate the registry through the manifest it built."""
    return json.loads(json.dumps(METRICS))


def load_files(dest):
    out = {}
    for key in FILES:
        p = os.path.join(dest, *key.split("/"))
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                out[key] = json.load(f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", required=True, help="an export tree (WEB_EXPORT_DIR)")
    a = ap.parse_args(argv)
    rep = check(load_files(a.dest))
    print(rep.statement)
    for d in rep.declared:
        print("  declared: " + d)
    return 0 if rep.clean else 1


if __name__ == "__main__":
    sys.exit(main())
