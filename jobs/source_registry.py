"""THE SOURCE REGISTRY: what every exported kind reads, derived rather than written (a-22, a-30).

    python -m jobs.source_registry            # print the check statement and the union
    python -m jobs.source_registry --json     # print the nfl sources file the export would write
    python -m jobs.source_registry --sport cfb --json

WHY THIS EXISTS. `/nfl/sources` was hand-written prose, and on 2026-09-24 it was
wrong about what was connected in three places (audit S-04). Prose drifts because
nothing checks it against what the code reads. This module is what gets checked.

WHY IT WAS REBUILT (a-30). a-22 derived part of each kind's sources from a scan
of export_web's SQL and typed the rest. f-19 attacked it with an independently
measured read-set and found the typed half wrong in three places (cfb/manifest,
mlb/manifest, player_index missing Kalshi) and four ways past the scan: lower-case
SQL, an f-string table name, SQL living in store.py, and a mapped table read by a
new kind. A hand-written declaration is the same failure as the hand-written page
it replaced, one level down. So now:

  SPORT_TABLE_SOURCES  store table -> source ids, per sport (each sport is its own
                       store). Hand-written, and the ONE place a human says where
                       rows come from.
  LOADERS              producer function -> the kinds its SQL reads reach. Every
                       function in a producer whose SQL reads a table MUST be here:
                       a new one fails the registry until it is attributed.
  what each function READS is DERIVED, by AST, from the function's own SQL plus
                       the SQL of every function it calls in another module
                       (store.pfr_gsis, research.implied.fit_*, research.score.load),
                       plus the module-level SQL constants those name. The scan is
                       case-insensitive, and a table name it cannot read - an
                       f-string hole where a table goes, a FROM that ends its
                       literal - is a GAP, and a gap refuses the registry. It never
                       passes silently.
  KIND_INPUTS          a kind built from another kind's output (player_index's
                       has_market, the manifest's market counts) inherits that
                       kind's sources. Derived transitively, not retyped.
  KIND_EXTRA           reads that are not SQL (committed result files) or happen
                       in producers the gate does not run (analytics, predictor,
                       coverage, live.prices). Still hand-written, and said so.
  PAGE_READS           a source a PAGE reads at request time, through no file.

THREE CHECKS, AND WHERE EACH RUNS.

  static    `check_registry()` / `require_declared()`: every SQL function
            attributed, every table mapped, no gaps, every id registered and
            serving the sport it is declared for, every source read by something.
  runtime   `watch(con, sport)` puts a sqlite authorizer on the producer's
            connection. SQLite itself reports every table a statement reads -
            whatever its case, however the string was built, wherever the Python
            that built it lives - and `require_declared` refuses the write if any
            of them is unmapped. The static scan catches code that has not run;
            the authorizer catches SQL the scan cannot parse.
  behaviour tests/test_source_registry.py empties each table the fixture export
            reads and asserts every kind whose output moved declares that
            table's sources: the attribution in LOADERS and KIND_INPUTS is
            checked against what the code DOES, not what it says.

THE GATE. `jobs.export_web.sync_keys` - the one choke point every exported file
passes through, used by export_web, export_cfb_web and export_mlb_web - calls
`require_declared(wanted)`, keyed on each file's own `kind` AND `sport`.

WHAT THE GATE DOES NOT COVER, stated so it is not assumed:
  - Kinds produced OUTSIDE sync_keys (analytics, predictor, coverage,
    live.prices) are declared in KIND_EXTRA from reading their producers, not
    enforced there. f-19 lists them; a-30 brought MLB under the gate and no other.
  - Reads through a connection nobody watched (research modules open their own)
    are covered by the static scan only.
  - What a page reads is the site's to enforce, against `kinds` in the file.

"NOT CONNECTED YET" lives here too (audit S-02): what the site cannot show because
the producer does not export it, each with the STATE it is in.
"""
import argparse
import ast
import contextlib
import json
import os
import re
import sqlite3
import sys

import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYERS = ("FACTS", "PRICES", "BELIEFS", "CONTEXT", "HEADLINES")
SPORTS = ("nfl", "cfb", "mlb")


def _sportless_kinds():
    # Read off the contract document rather than retyped: a sportless kind (the
    # coverage file counts every store) may read sources of any sport.
    with open(os.path.join(ROOT, "web", "contract", "v2", "contract.schema.json"),
              encoding="utf-8") as f:
        return frozenset(json.load(f)["x-contract"]["sportless_kinds"])


SPORTLESS = _sportless_kinds()


class SourceRegistryError(AssertionError):
    """A kind is written whose sources are undeclared, or a declared id is unregistered."""
def _tiers():
    # From the logger's own settings, never retyped: a cadence written here
    # would be the next false row on the Sources page.
    return (f"polled every {config.POLL_HOT}s in the {config.HOT_WINDOW_MIN // 60}h before "
            f"kickoff, every {config.POLL_LIVE}s in-game, every {config.POLL_GAME}s within "
            f"{config.COLD_WINDOW_HOURS:g}h of kickoff, every {config.POLL_COLD}s otherwise")


def _retention():
    return (f"live quotes are kept {config.QUOTES_RETENTION_DAYS:g} days, measured on when "
            f"they were ingested, and held for as long as the market is on the site")


_PAIRING = ("pairing a snap-count player id with a gsis id where no crosswalk row "
            "does (pfr_alias), which every snap figure reads through")


# `last_read`: how "last read" is measured, per source.
#   ("health", names)       max(last_ok_ts) over those source_health rows
#   ("quotes", pairs)       newest ingest_ts in `quotes` for (source, venue LIKE)
#   ("max", (table, col))   max(col) over a table in this store
#   ("elsewhere", store)    held in another store this export does not open
#   ("runtime", reader)     read at request time by `reader`; no producer reads it
#   ("committed", path)     a result file committed to the repo; no store measures it
SOURCES = {
    # --- nflverse: one id per release the export reads, because "schedule" and
    # "player stats" are separate files that go stale separately.
    "nflverse.stats": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Weekly player stats (the stats_player release), every season it carries",
        used_for=("Every player's season and career lines, the components table, team splits, "
                  "and the actual each prop settles against"),
        last_read=("health", ("nflverse:weekly_stats",))),
    "nflverse.schedule": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="The schedule: every game's date, kickoff, teams, final score, and the "
                 "spread and total the release carries",
        used_for=("The fixture source. Team schedules, standings and season paths, the current "
                  "week, and the spread and total shown beside each game"),
        last_read=("health", ("nflverse:games",))),
    "nflverse.snap_counts": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Offensive and defensive snap counts per player-game, from 2013",
        used_for=("Snap share, and telling a player who played and recorded nothing (settles "
                  "at 0) from one who did not play (void)"),
        last_read=("health", ("nflverse:snap_counts",))),
    "nflverse.players": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Player identity: gsis id, name, position and the ids other feeds use",
        used_for="The crosswalk every join keys on, names, positions and slugs",
        last_read=("health", ("nflverse:players",))),
    "nflverse.rosters": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Weekly rosters: team, jersey number and headshot URL per player-week",
        used_for=("Headshots and jersey numbers, and archived-release evidence for "
                  + _PAIRING),
        last_read=("health", ("nflverse:weekly_rosters",))),
    "nflverse.teams": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Team names, colours, conference and division",
        used_for="Team identity colours and the division and conference groupings",
        last_read=("health", ("nflverse:teams",))),
    "nflverse.pbp": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Play-by-play",
        used_for=("Air yards and red-zone looks where that stage is on, the analytics metrics, "
                  "and the settlement-lag study in the register"),
        last_read=("health", ("nflverse:pbp",))),
    "nflverse.participation": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Participation: routes, coverage and pressure, refreshed after the postseason",
        used_for=("Analytics metrics whose basis is participation, and on-field evidence "
                  "for " + _PAIRING),
        last_read=("health", ("nflverse:participation",))),
    "nflverse.ngs": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides="Next Gen Stats passing, receiving and rushing",
        used_for="Analytics metrics built on Next Gen Stats",
        last_read=("health", ("nflverse:ngs_passing", "nflverse:ngs_receiving",
                              "nflverse:ngs_rushing"))),
    "nflverse.draft_picks": dict(
        name="nflverse", sports=("nfl",), layer="FACTS",
        provides=("Draft picks, including Pro-Football-Reference's career value columns, which "
                  "are credited to Sports-Reference where shown"),
        used_for=("The drafting-strengths predictor, and draft-slot evidence for "
                  + _PAIRING),
        last_read=("health", ("nflverse:draft_picks",))),
    "nflverse.injuries": dict(
        name="nflverse", sports=("nfl",), layer="CONTEXT",
        provides="The weekly injury and practice report, versioned by when it was ingested",
        used_for="Counted on the coverage file only. No page shows a report yet",
        last_read=("elsewhere", "feeds.db")),

    # --- prices
    "kalshi.ladders": dict(
        name="Kalshi", sports=("nfl", "cfb"), layer="PRICES",
        provides="Event ladders for receptions and rush attempts, per rung, bid and ask",
        used_for=("The current week's market-implied fantasy distributions, read at the mid of "
                  "each rung with no de-vig (an exchange spread is not a bookmaker's margin), "
                  "the 2026 lines in each player's prop history, and the exchange mid shown "
                  "beside each Board row"),
        last_read=("quotes", (("live", "kalshi"),))),
    "kalshi.price_history": dict(
        name="Kalshi", sports=("nfl",), layer="PRICES",
        # A callable, resolved when the file is built: an f-string here would
        # freeze the cadence at import, so a changed setting would change what
        # the logger does and not what Sources says.
        provides=lambda: f"Every quote on every rung, as it was logged: {_tiers()}. {_retention()}",
        used_for=("Each priced player's price path: the rung nearest even money, change-points "
                  "only (a step is kept, a repeated price is not), capped, with the number "
                  "of points dropped published beside it"),
        last_read=("quotes", (("live", "kalshi"),))),
    "kalshi.trades": dict(
        name="Kalshi", sports=("nfl",), layer="PRICES",
        provides="The public trade tape per market",
        used_for="The maker and spread-capture studies in the register",
        last_read=("max", ("market_trades", "ingest_ts"))),
    "polymarket": dict(
        name="Polymarket", sports=("nfl", "cfb"), layer="PRICES",
        provides="Top of book on player and game markets, 2026",
        used_for="2026 lines in each player's prop history, beside Kalshi's",
        last_read=("quotes", (("live", "polymarket"),))),
    "oddsapi": dict(
        name="The Odds API", sports=("nfl", "cfb"), layer="PRICES",
        provides=("US sportsbook prices: closing lines on five player-prop markets - "
                  "receptions, receiving yards, rush attempts and tackles and assists for "
                  "2023-2025, sacks for 2024-2025 - and the featured game markets; and in 2026, "
                  "forward capture on a schedule keyed to kickoff"),
        used_for=("Every player's 2023-2025 prop history (posted line and result), the market "
                  "calibration study and the Method page's over-bias finding (median de-vigged "
                  "close across DraftKings, FanDuel and BetMGM), and the register's walk-forward "
                  "against the book close; and the Board's market price at every read, the "
                  "median de-vigged DraftKings, FanDuel and BetMGM quote from forward capture; and "
                  "the Lab library's precomputed preset results, as aggregates and de-vigged "
                  "probabilities only - no book price leaves the server"),
        last_read=("quotes", (("live", "oddsapi%"), ("oddsapi_historical", "oddsapi%")))),

    # --- beliefs: ours, and listed because a page shows it
    "calibrated.model": dict(
        name="Calibrated Sports", sports=("nfl",), layer="BELIEFS",
        provides="The baseline usage model's predictions, immutable and timestamped",
        used_for=("Scored against the market in the register and on the Method page, and the "
                  "Board's model probability at each read (fit at the read, frozen in that read's "
                  "file and the lean ledger) - never shown as a pick"),
        last_read=("health", ("predictions",))),

    # The walk-forward is ours too, and a different thing from the predictions
    # table: the model REFIT on seasons <= T-1 and scored per outcome against the
    # 2023-2025 sportsbook close. The Board quotes it twice - the verdict strip
    # (R15) and every lean's "leans this size" band. It reaches the Board as a
    # committed result file, so nothing in a store measures when it was last read.
    "calibrated.walkforward": dict(
        name="Calibrated Sports", sports=("nfl",), layer="BELIEFS",
        provides=("The walk-forward ledger: the model refit on seasons before each one it "
                  "forecasts, every outcome scored against the de-vigged 2023-2025 sportsbook "
                  "close"),
        used_for=("The Board's verdict strip and each lean's walk-forward record for leans "
                  "that size (research/board_bands.py), never a forecast of the lean"),
        last_read=("committed", "research/results/board_bands.json")),

    # --- held in other stores; registered because `coverage` counts them
    "sportsdataverse.cfb": dict(
        name="sportsdataverse", sports=("cfb",), layer="FACTS",
        provides="College schedule and results, teams, rosters, box scores and usage",
        used_for=("Every college team page - schedule, results, splits and roster - the "
                  "college manifest, and coverage counts"),
        last_read=("elsewhere", "cfb.db")),
    "cfbd": dict(
        name="CollegeFootballData", sports=("cfb",), layer="FACTS",
        provides="College games with line scores, and book lines with no capture time",
        used_for=("The spread and total beside each game on a college team's schedule, "
                  "labelled as a book line with no capture time"),
        last_read=("elsewhere", "cfb.db")),
    "retrosheet": dict(
        name="Retrosheet", sports=("mlb",), layer="FACTS",
        provides="MLB game, batting and pitching lines, by season",
        used_for=("MLB player, team and manifest files (a local probe export, not "
                  "published) and coverage counts"),
        last_read=("elsewhere", "mlb.db")),
    "openmeteo": dict(
        name="Open-Meteo", sports=("cfb",), layer="CONTEXT",
        provides="Weather at kickoff, by venue coordinates",
        used_for="Coverage counts",
        last_read=("elsewhere", "feeds.db")),
    # Read by a PAGE, at request time, through no exported file (a-30; f-19 found it
    # unregistered). a-23's Live snapshot job moves this read into a producer; when
    # its kind lands in the contract, the completeness check refuses the export until
    # that kind is declared here, and the page read below should then be removed.
    "espn.scoreboard": dict(
        name="ESPN", sports=("nfl",), layer="FACTS",
        provides="The public scoreboard: game state, clock and score",
        used_for="The Live page's scores and game clock",
        last_read=("runtime", "the site's Live page")),
    "rss.headlines": dict(
        name="Outlet RSS feeds", sports=("nfl", "cfb"), layer="HEADLINES",
        provides="Headline, time and link from each outlet's own feed",
        used_for="Coverage counts. News reads the outlets itself, at request time",
        last_read=("elsewhere", "feeds.db")),
}


# =============================================================================
# tables -> sources, per sport (each sport is its own store)
# =============================================================================
#
# The mapping is for the table's ROWS. A reader that filters to one venue says
# so in NARROW, per (function, table), and may only drop sources, never add one.
SPORT_TABLE_SOURCES = {
    "nfl": {
        "nfl_player_week": ("nflverse.stats",),
        "nflverse_versions": ("nflverse.stats",),
        "nfl_games": ("nflverse.schedule",),
        "nfl_snap_counts": ("nflverse.snap_counts",),
        "player_xwalk": ("nflverse.players",),
        "player_alias": ("nflverse.players",),
        # PFR id -> gsis pairings derived by jobs/build_pfr_alias.py from archived
        # players / rosters / draft releases (archive_release), draft slots
        # (draft_slot) and participation matched against snap rows (participation).
        # Read through store.pfr_gsis, which a-22's scan could not see (f-19).
        "pfr_alias": ("nflverse.players", "nflverse.rosters", "nflverse.draft_picks",
                      "nflverse.participation", "nflverse.snap_counts"),
        "player_headshot": ("nflverse.rosters",),
        "nfl_roster_week": ("nflverse.rosters",),
        "nfl_teams": ("nflverse.teams",),
        "nfl_pbp_looks": ("nflverse.pbp",),
        # A venue-independent claim: 2023-2025 rows were created from Odds API
        # books, 2026 rows from Kalshi and Polymarket markets (measured 2026-09-24
        # by joining settled player outcomes to market_outcome.venue).
        "outcomes": ("oddsapi", "kalshi.ladders", "polymarket"),
        # The de-vigged sportsbook close per outcome (backfill, 2023-2025). Read by
        # the Board's posted-line record; a-26 added the read, a-31 mapped it.
        "outcome_close": ("oddsapi",),
        # The actual, and whether a player with no stat row played.
        "outcome_settlement": ("nflverse.stats", "nflverse.snap_counts"),
        "market_outcome": ("kalshi.ladders", "polymarket", "oddsapi"),
        "markets": ("kalshi.ladders", "polymarket", "oddsapi"),
        "quotes": ("kalshi.ladders", "kalshi.price_history", "polymarket", "oddsapi"),
        "predictions": ("calibrated.model",),
        "model_version_equivalence": ("calibrated.model",),
    },
    "cfb": {
        "cfb_teams": ("sportsdataverse.cfb",),
        "cfb_games": ("sportsdataverse.cfb",),
        "cfb_rosters": ("sportsdataverse.cfb",),
        "cfb_player_game_box": ("sportsdataverse.cfb",),
        "cfb_player_game_usage": ("sportsdataverse.cfb",),
        "cfb_game_lines": ("cfbd",),
    },
    "mlb": {
        "mlb_batting": ("retrosheet",),
        "mlb_pitching": ("retrosheet",),
        "mlb_player_teams": ("retrosheet",),
        "mlb_team_games": ("retrosheet",),
    },
}
TABLE_SOURCES = SPORT_TABLE_SOURCES["nfl"]

# The producer module whose functions are scanned, per sport.
PRODUCERS = {"nfl": "jobs.export_web", "cfb": "jobs.export_cfb_web", "mlb": "jobs.export_mlb_web"}

# Producers that write kinds through `sync_keys` from their OWN module - scanned
# exactly like the export module, their functions attributed in LOADERS under the
# qualified name `module.function` (a bare name would collide: two modules may
# both have a `history`). a-31: the Board read job.
SIDE_PRODUCERS = {"nfl": ("jobs.board_read", "lab.universe"), "cfb": (), "mlb": ()}

# Modules the scan does not follow into, each with why. The registry's own reads
# (last_read over source_health and quotes) describe sources; they carry no row of
# any source into a file, and `sources` declares that with ().
NOT_FOLLOWED = {"jobs.source_registry": "the sources kind's own metadata reads"}

# producer function -> the kinds its SQL's rows reach. EVERY function whose SQL
# (its own, or a called module's) reads a table must be here, and nothing that
# reads none: both directions are refused. `()` says "reads, and writes no kind".
_PLAYER_KINDS = ("player_summary", "player_season", "player_index")
LOADERS = {
    "nfl": {
        "load_games": ("market", *_PLAYER_KINDS, "team", "components", "sport_manifest"),
        "load_line_history": ("sport_manifest",),             # fixtures stage only (a-37)
        "load_player_weeks": ("market", *_PLAYER_KINDS, "team", "components", "sport_manifest"),
        "load_xwalk": ("market", *_PLAYER_KINDS, "team", "sport_manifest"),
        "load_snaps": (*_PLAYER_KINDS, "team", "sport_manifest"),
        "snap_weeks": (*_PLAYER_KINDS, "components"),
        "load_phase_snaps": (*_PLAYER_KINDS, "team"),         # extended profile only
        "load_jerseys": (*_PLAYER_KINDS, "team"),             # extended profile only
        "load_looks": (*_PLAYER_KINDS, "components"),         # air_rz stage only
        "load_headshots": ("player_summary", "player_index"),
        "load_prop_history": ("player_summary",),
        # a-24's main line rides the summary's prop_history. Unattributed when a-24
        # and a-30 were built apart; the merged tree refused to export (a-33).
        "load_main_lines": ("player_summary",),
        "_book_closes": ("player_summary",),
        "_exchange_close": ("player_summary",),
        "load_team_snaps": ("components",),
        "build_market": ("market",),
        "build_price_path": ("market",),
        "load_team_colors": ("sport_manifest",),
        "load_team_groupings": ("sport_manifest",),
        "export": ("sport_manifest",),                        # nflverse_versions
        # research.score.load: predictions scored against Kalshi, settled. Only the
        # calibration file carries it; hypotheses and execution read committed files.
        "build_research": ("research.calibration",),
        # a-31: the Board read job (SIDE_PRODUCERS). Every read it makes lands in
        # the read file; the index inherits it through KIND_INPUTS.
        **{f"jobs.board_read.{fn}": ("board_read",) for fn in (
            "week_games", "oddsapi_events", "load_quotes", "resolve_player", "current_team",
            "history", "posted_record", "model_prob", "kalshi_mid", "settle")},
        # --tick's choice of WHICH week to read; no row of it reaches a file.
        "jobs.board_read.weeks_in_play": (),
        # a-32: the Lab library. `lab.universe` builds the table every preset runs
        # over (jobs/lab_publish.py writes the files from it); every table it reads
        # reaches the preset files, and the index inherits them through KIND_INPUTS.
        **{f"lab.universe.{fn}": ("lab_preset",) for fn in (
            "load_games", "load_divisions", "load_history", "load_props", "load_game_quotes")},
        # the settlement-fix gate: it refuses a pre-fix store and its counts stay
        # in the universe's meta; no row of it reaches a published file.
        "lab.universe.settlement_evidence": (),
    },
    "cfb": {
        "teams": ("team", "sport_manifest"),
        "colors": ("sport_manifest",),
        "abbr_map": ("team",),
        "schedule_rows": ("team",),
        "game_lines": ("team",),
        "team_splits": ("team",),
        "team_points": ("team",),
        "roster_rows": ("team",),
        "build": ("sport_manifest",),
    },
    "mlb": {
        "build": ("player_season", "player_summary", "player_index", "team", "sport_manifest"),
        "measure": (),                                        # --measure prints; writes nothing
    },
}

# (function, table) -> the sources that function's rows of that table carry.
NARROW = {
    "nfl": {
        # build_market joins outcomes to market_outcome ON venue = 'kalshi'.
        ("build_market", "outcomes"): ("kalshi.ladders",),
        ("build_market", "market_outcome"): ("kalshi.ladders",),
        ("build_market", "quotes"): ("kalshi.ladders", "kalshi.price_history"),
        ("build_price_path", "quotes"): ("kalshi.price_history",),
        # research.score.load: market_outcome venue='kalshi', markets venue='kalshi',
        # research.clv.quote_at venue='kalshi'.
        ("build_research", "outcomes"): ("kalshi.ladders",),
        ("build_research", "market_outcome"): ("kalshi.ladders",),
        ("build_research", "markets"): ("kalshi.ladders",),
        ("build_research", "quotes"): ("kalshi.ladders", "kalshi.price_history"),
        # load_prop_history reads every venue's outcomes and their settlement.
        # a-24/a-33: the main line's book close is outcome_close (Odds API backfill
        # only), and its exchange close is Kalshi's or Polymarket's last quote.
        ("_book_closes", "outcomes"): ("oddsapi",),
        # Kept from the hand fix: the price_history backfill also writes
        # quotes at venue = 'kalshi', and _exchange_close filters on venue
        # alone, so a backfilled row can be the close it selects.
        ("_exchange_close", "quotes"): ("kalshi.ladders", "kalshi.price_history", "polymarket"),
        ("load_main_lines", "market_outcome"): ("kalshi.ladders", "polymarket"),
        # a-31, the Board: book prices are Odds API rows only (venue 'oddsapi:*');
        # the exchange mid is Kalshi's only; the posted-line record joins
        # outcomes to outcome_close, which exists only for backfilled book closes.
        ("jobs.board_read.oddsapi_events", "markets"): ("oddsapi",),
        ("jobs.board_read.load_quotes", "quotes"): ("oddsapi",),
        ("jobs.board_read.kalshi_mid", "outcomes"): ("kalshi.ladders",),
        ("jobs.board_read.kalshi_mid", "market_outcome"): ("kalshi.ladders",),
        ("jobs.board_read.kalshi_mid", "quotes"): ("kalshi.ladders",),
        ("jobs.board_read.posted_record", "outcomes"): ("oddsapi",),
        # a-32: every price in the Lab universe is an Odds API historical close
        # (quotes.source = 'oddsapi_historical'); outcomes are read whole but only
        # those joined to an Odds API close become rows.
        ("lab.universe.load_props", "outcomes"): ("oddsapi",),
        ("lab.universe.load_props", "market_outcome"): ("oddsapi",),
        ("lab.universe.load_props", "quotes"): ("oddsapi",),
        ("lab.universe.load_game_quotes", "quotes"): ("oddsapi",),
    },
    "cfb": {},
    "mlb": {},
}

# kind -> kinds whose OUTPUT it is built from. The sources are inherited.
KIND_INPUTS = {
    "nfl": {
        # has_market (index) and market.key (summary) exist iff a market file does.
        "player_index": ("market",),
        "player_summary": ("market",),
        # counts.market, counts.rungs and teams[].season.markets come off the market
        # files; counts.players and unresolved_ids off the index.
        "sport_manifest": ("market", "player_index"),
        # a-31: the index names the reads, counts their leans and carries the
        # verdict - built from the read, so it reads what the read reads.
        "board_index": ("board_read",),
        # a-32: the Lab index lists the presets and their verdicts.
        "lab_index": ("lab_preset",),
    },
    "cfb": {},
    "mlb": {},
}

# Reads that are not SQL in a scanned producer, declared by hand. The half of the
# registry that is only as good as the reading behind it.
KIND_EXTRA = {
    "nfl": {
        # research/sweep/results/h3.jsonl, the book lifecycle study (brief 022)
        "research.execution": ("kalshi.ladders", "kalshi.price_history"),
        # a-36: research/results/market_calibration.json, committed output of
        # research/calibration.py - de-vigged Odds API closes, settled on nflverse
        # stat rows, with snap counts settling a played-with-no-row over at 0.
        "research.market_calibration": ("oddsapi", "nflverse.stats", "nflverse.snap_counts"),
        # docs/hypotheses.json: every register row's script, R01-R17
        "research.hypotheses": ("oddsapi", "kalshi.ladders", "kalshi.price_history",
                                "kalshi.trades", "nflverse.stats", "nflverse.schedule",
                                "nflverse.snap_counts", "nflverse.pbp", "calibrated.model"),
        # produced outside sync_keys - declared from reading their producers
        "analytics.index": ("nflverse.pbp", "nflverse.participation", "nflverse.ngs"),
        "analytics.metric": ("nflverse.pbp", "nflverse.participation", "nflverse.ngs"),
        "live.prices": ("kalshi.ladders",),
        # a-23's Live snapshot (jobs/live_snapshot.py, written straight to R2 under
        # live/): the schedule skeleton from the store, the scoreboard overlay, the
        # exchange's game-winner quotes and the injury report held in feeds.db.
        # Declared at integration (a-33): a-23 and a-30 were built apart and the
        # merged tree refused every export on the undeclared kind. PAGE_READS below
        # stays until the site's Live page reads the snapshot instead (track B).
        "live.snapshot": ("nflverse.schedule", "espn.scoreboard", "kalshi.ladders",
                          "nflverse.injuries"),
        "predictor": ("nflverse.draft_picks",),
        "coverage": ("nflverse.stats", "nflverse.schedule", "nflverse.snap_counts",
                     "kalshi.ladders", "kalshi.price_history", "kalshi.trades", "polymarket",
                     "oddsapi", "nflverse.injuries", "sportsdataverse.cfb", "cfbd",
                     "retrosheet", "openmeteo", "rss.headlines"),
        # a-31: the Board's committed result files - research/results/board_bands.json
        # (walk-forward bands) and the hard-coded R15 verdict - and the model it fits
        # at every read. F11's next-game rates are nflverse stats, already derived.
        "board_read": ("calibrated.walkforward", "calibrated.model"),
        # read nothing upstream: a static list, and this registry itself
        "sports": (),
        "sources": (),
    },
    # The college index is an empty list: no appearance signal, so no player pages.
    "cfb": {"player_index": ()},
    "mlb": {},
}

# source -> pages that read it AT REQUEST TIME, through no exported file.
PAGE_READS = {
    "nfl": {
        # The Worker read the scoreboard and the exchange per request until a-23.
        # a-23 is now merged and the Live page renders live/{sport}/snapshot.json
        # alone (lib/liveSnapshot.ts), so these two rows are STALE - but retiring
        # them needs a `last_read` mode for "read on a schedule by a job that
        # records its own read time", which espn.scoreboard's ("runtime", ...)
        # is not. Held as a deferred issue rather than half-done here.
        "espn.scoreboard": ("page:live",),
        "kalshi.ladders": ("page:live",),
    },
    "cfb": {},
    "mlb": {},
}


# =============================================================================
# the scan: what each producer function reads, by AST
# =============================================================================

_HOLE = "\x00"
_SQL_WORD = re.compile(r"\bselect\b", re.I)
_TABLE_REF = re.compile(r"\b(?:from|join)\b\s*(\S*)", re.I)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PARSED = {}


def _module_file(mod):
    p = os.path.join(ROOT, *mod.split(".")) + ".py"
    return p if os.path.exists(p) else None


def _parse(mod):
    path = _module_file(mod)
    if path not in _PARSED:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        funcs = {n.name: n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        consts = {n.targets[0].id: n.value for n in tree.body
                  if isinstance(n, ast.Assign) and len(n.targets) == 1
                  and isinstance(n.targets[0], ast.Name)}
        _PARSED[path] = (tree, funcs, consts)
    return _PARSED[path]


def _import_map(nodes):
    """alias -> module, and bare name -> (module, function), for repo modules only."""
    mods, names = {}, {}
    for n in nodes:
        if isinstance(n, ast.Import):
            for a in n.names:
                if _module_file(a.name):
                    mods[a.asname or a.name] = a.name
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                sub = f"{n.module}.{a.name}"
                if _module_file(sub):
                    mods[a.asname or a.name] = sub
                elif _module_file(n.module):
                    names[a.asname or a.name] = (n.module, a.name)
    return mods, names


def _render(node):
    """A string literal as text; an f-string with every hole marked."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str)
                       else _HOLE for v in node.values)
    return None


def _strings(node):
    """Every string in `node` except docstrings (they quote SQL to explain it)."""
    skip = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(n, ast.JoinedStr):
            skip |= {id(v) for v in n.values}     # read once, as the whole f-string
    for n in ast.walk(node):
        if id(n) not in skip:
            text = _render(n)
            if text is not None:
                yield text, getattr(n, "lineno", 0)


def sql_tables(text):
    """-> (tables, gaps) for one string. Case-insensitive. A table position the scan
    cannot read is a GAP, never a skip: an interpolated name, or a FROM/JOIN that
    ends the literal (the table is in an expression the scan cannot see)."""
    tables, gaps = set(), []
    if not _SQL_WORD.search(text):
        return tables, gaps
    for m in _TABLE_REF.finditer(text):
        tok = m.group(1)
        if tok.startswith("("):
            continue                        # a subquery; its own FROM is matched too
        if not tok:
            gaps.append("FROM/JOIN ends the string: the table is outside the literal")
        elif tok.startswith(_HOLE):
            gaps.append("the table name is interpolated")
        elif not _IDENT.match(tok):
            gaps.append(f"unreadable table token {tok!r}")
        else:
            name = _IDENT.match(tok).group(0).lower()
            if not name.startswith("sqlite_"):
                tables.add(name)
    return tables, gaps


_CTE_DEF = re.compile(r"(?:\bwith\b(?:\s+recursive)?|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(",
                      re.I)


def cte_names(text):
    """Names a string DEFINES as common table expressions (`WITH pg AS (`,
    `, ranked AS (`). `FROM ranked` then reads the CTE, not a table - and the
    definition and the read may sit in different strings of one closure
    (models.features builds the CTE in one function and selects from it in
    another), so the subtraction happens over the whole closure, in scan()."""
    return {m.group(1).lower() for m in _CTE_DEF.finditer(text)} if _SQL_WORD.search(text) else set()


def _mapped_tables():
    return {t for tables in SPORT_TABLE_SOURCES.values() for t in tables}


def _closure(mod, fname, follow_local, seen, ctes=None):
    """Tables and gaps for one function, following calls into other repo modules
    (and, once outside the producer, within them)."""
    if (mod, fname) in seen or mod in NOT_FOLLOWED:
        return set(), []
    seen.add((mod, fname))
    tree, funcs, consts = _parse(mod)
    fn = funcs.get(fname)
    if fn is None:
        return set(), []
    mods, names = _import_map(tree.body)
    lm, ln = _import_map([n for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))])
    mods, names = {**mods, **lm}, {**names, **ln}
    tables, gaps = set(), []

    def take(node, where):
        for text, line in _strings(node):
            t, g = sql_tables(text)
            tables.update(t)
            gaps.extend(f"{where}:{line}: {x}" for x in g)
            if ctes is not None:
                ctes.update(cte_names(text))

    take(fn, f"{mod}.{fname}")
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in consts:
            take(consts[n.id], f"{mod}.{n.id}")
        if isinstance(n, ast.Call):
            f, target = n.func, None
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in mods:
                target = (mods[f.value.id], f.attr)
            elif isinstance(f, ast.Name) and f.id in names:
                target = names[f.id]
            elif isinstance(f, ast.Name) and follow_local and f.id in funcs:
                target = (mod, f.id)
            if target and _module_file(target[0]):
                t, g = _closure(target[0], target[1], True, seen, ctes)
                tables |= t
                gaps += g
    return tables, gaps


def scan(mod):
    """-> {function: (tables, gaps)} for every top-level function in `mod` that reads.
    Same-module calls are NOT folded in: each such function is attributed itself."""
    _, funcs, _ = _parse(mod)
    out = {}
    for name in funcs:
        ctes = set()
        tables, gaps = _closure(mod, name, False, set(), ctes)
        # A CTE name is not a table read. But a CTE that SHADOWS a mapped table
        # name is kept as a read: dropping it would let a name collision hide a
        # real table, and a false read is refused loudly while a hidden one is not.
        tables = tables - (ctes - _mapped_tables())
        if tables or gaps:
            out[name] = (tables, gaps)
    return out


# =============================================================================
# derivation
# =============================================================================

def derive(sport):
    """-> ({kind: source ids}, [problems]) for one sport. Never raises: a problem is
    carried to check_registry and require_declared, which refuse by name."""
    problems = []
    tables_map = SPORT_TABLE_SOURCES.get(sport, {})
    loaders = LOADERS.get(sport, {})
    narrow = NARROW.get(sport, {})
    found = {}
    for mod in (PRODUCERS[sport],) + tuple(SIDE_PRODUCERS.get(sport, ())):
        try:
            scanned = scan(mod)
        except (OSError, SyntaxError, KeyError, TypeError) as e:
            return {}, [f"{sport}: producer scan failed ({mod}): {type(e).__name__}: {e}"]
        for fn, v in scanned.items():
            found[fn if mod == PRODUCERS[sport] else f"{mod}.{fn}"] = v

    def qual(fn):
        return fn if "." in fn else f"{PRODUCERS[sport]}.{fn}"
    out = {}
    for fn in sorted(set(found) - set(loaders)):
        problems.append(f"{sport}: {qual(fn)} reads {sorted(found[fn][0])} "
                        f"and is not attributed to any kind in LOADERS")
    for fn in sorted(set(loaders) - set(found)):
        problems.append(f"{sport}: LOADERS names {fn}, which reads no table (stale entry)")
    for fn, (tables, gaps) in sorted(found.items()):
        problems += [f"{sport}: SQL the scan cannot read, {g}" for g in gaps]
        for t in sorted(tables):
            if t not in tables_map:
                problems.append(f"{sport}: {qual(fn)} reads table {t!r}, "
                                f"which has no entry in SPORT_TABLE_SOURCES[{sport!r}]")
                continue
            ids = narrow.get((fn, t), tables_map[t])
            if not set(ids) <= set(tables_map[t]):
                problems.append(f"{sport}: {fn} narrows {t} to {ids}, not a subset of "
                                f"{tables_map[t]}")
            for k in loaders.get(fn, ()):
                out.setdefault(k, set()).update(ids)
    for fn, t in narrow:
        if t not in found.get(fn, (set(), []))[0]:
            problems.append(f"{sport}: NARROW names ({fn}, {t}), which that function "
                            f"does not read (stale entry)")
    for kind, ids in KIND_EXTRA.get(sport, {}).items():
        out.setdefault(kind, set()).update(ids)
    inputs = KIND_INPUTS.get(sport, {})
    for _ in range(len(inputs) + 1):          # transitive, to a fixed point
        for kind, srcs in inputs.items():
            for name in srcs:
                if name not in out:
                    problems.append(f"{sport}: {kind} is built from {name}, which is "
                                    f"not declared")
                    continue
                out.setdefault(kind, set()).update(out[name])
    order = list(SOURCES)
    for kind, ids in out.items():
        for i in ids:
            if i not in SOURCES:
                problems.append(f"{sport}: kind {kind}: {i!r} is not a registered source")
            elif sport not in SOURCES[i]["sports"] and kind not in SPORTLESS:
                problems.append(f"{sport}: kind {kind} reads {i!r}, which does not name "
                                f"{sport} among the sports it serves")
    # Unregistered ids sort last rather than crashing the import (f-19's C2): the
    # refusal has to be the named one, reachable from a real edit.
    rank = {s: n for n, s in enumerate(order)}
    decl = {k: tuple(sorted(v, key=lambda i: (rank.get(i, len(order)), i)))
            for k, v in sorted(out.items())}
    return decl, sorted(set(problems))


def _derive_all():
    decl, probs = {}, {}
    for sport in SPORTS:
        decl[sport], probs[sport] = derive(sport)
    return decl, probs


DECLARED, PROBLEMS = _derive_all()
KIND_SOURCES = DECLARED["nfl"]


def refresh():
    """Re-derive after a test edits a table. -> the nfl declaration."""
    global DECLARED, PROBLEMS, KIND_SOURCES
    _PARSED.clear()
    DECLARED, PROBLEMS = _derive_all()
    KIND_SOURCES = DECLARED["nfl"]
    return KIND_SOURCES


# =============================================================================
# the runtime ledger: what SQLite says the producer actually read
# =============================================================================

_READS = {}      # sport -> {table} read on the connection watched for that sport
_QUIET = [0]


def _recorder(reads):
    def authorizer(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_READ and arg1 and not _QUIET[0]:
            name = arg1.lower()
            if not name.startswith("sqlite_"):
                reads.add(name)
        return sqlite3.SQLITE_OK
    return authorizer


def watch(con, sport):
    """Record every table read on `con`, as SQLite reports it. Starts a fresh ledger
    for `sport`: one per producer run. -> the set, so a caller can look."""
    reads = set()
    _READS[sport] = reads
    con.set_authorizer(_recorder(reads))
    return reads


@contextlib.contextmanager
def watch_process(con, sport):
    """`watch(con)`, AND every sqlite3 connection this process opens until exit
    (a-35). f-21 planted an unmapped read on a SECOND connection with its SQL
    built by `" ".join(...)`: the static scan cannot read the table name and the
    authorizer on `con` never sees the query, so the gate passed and the file was
    written. Inside this block `sqlite3.connect` hands back connections carrying
    the same recorder, so a read on any of them lands in the same ledger.

    What it still cannot see, stated rather than implied: a connection opened
    BEFORE the block, `from sqlite3 import connect` bound at import time, a
    `sqlite3.Connection(...)` built directly, and another process. The original
    is restored on exit, including on a raise."""
    reads = watch(con, sport)
    real = sqlite3.connect

    def connect(*a, **k):
        c = real(*a, **k)
        c.set_authorizer(_recorder(reads))
        return c
    sqlite3.connect = connect
    try:
        yield reads
    finally:
        sqlite3.connect = real


@contextlib.contextmanager
def quiet():
    """The registry's own last_read queries describe sources; they carry no row."""
    _QUIET[0] += 1
    try:
        yield
    finally:
        _QUIET[0] -= 1


def runtime_problems(sport):
    mapped = SPORT_TABLE_SOURCES.get(sport, {})
    return [f"{sport}: the export read table {t!r} (reported by SQLite), which has no "
            f"entry in SPORT_TABLE_SOURCES[{sport!r}]"
            for t in sorted(_READS.get(sport, set()) - set(mapped))]


# =============================================================================
# checks
# =============================================================================

def check_registry(contract_kinds=None):
    """-> the statement it approved. Raises SourceRegistryError otherwise.

    Every sport's derivation is clean; every declared id is registered and serves
    the sport; every source has a known layer and at least one reader. With
    `contract_kinds`, nfl declares exactly the contract's kinds, and no other
    sport declares a kind the contract does not have.
    """
    problems = [p for sport in SPORTS for p in PROBLEMS[sport]]
    for sid, s in SOURCES.items():
        if s["layer"] not in LAYERS:
            problems.append(f"{sid}: layer {s['layer']!r} is not one of {LAYERS}")
        if not s["sports"]:
            problems.append(f"{sid}: names no sport")
    for sport, tables in SPORT_TABLE_SOURCES.items():
        for t, ids in tables.items():
            for i in ids:
                if i not in SOURCES:
                    problems.append(f"{sport}: table {t}: {i!r} is not a registered source")
    for sport, pages in PAGE_READS.items():
        for i in pages:
            if i not in SOURCES:
                problems.append(f"{sport}: page read {i!r} is not a registered source")
    read = {i for sport in SPORTS for ids in DECLARED[sport].values() for i in ids}
    read |= {i for pages in PAGE_READS.values() for i in pages}
    for sid in SOURCES:
        if sid not in read:
            # The inverse lie: a row on Sources for a feed nothing draws from.
            problems.append(f"{sid}: registered but no kind reads it")
    if contract_kinds is not None:
        missing = sorted(set(contract_kinds) - set(DECLARED["nfl"]))
        if missing:
            problems.append(f"contract kinds with no source declaration: {missing}")
        for sport in SPORTS:
            extra = sorted(set(DECLARED[sport]) - set(contract_kinds))
            if extra:
                problems.append(f"{sport}: declared kinds the contract does not have: {extra}")
    if problems:
        raise SourceRegistryError("source registry refused:\n  " + "\n  ".join(problems))
    kinds = sum(len(DECLARED[s]) for s in SPORTS)
    tables = sum(len(SPORT_TABLE_SOURCES[s]) for s in SPORTS)
    fns = sum(len(LOADERS[s]) for s in SPORTS)
    return (f"{len(SOURCES)} sources registered, {kinds} (sport, kind) pairs declared "
            f"({len(DECLARED['nfl'])} nfl), {tables} tables mapped, {fns} reading functions "
            f"attributed with 0 gaps, every source read by at least one kind or page")


def require_declared(files):
    """The export gate. `files` is {key: obj} as sync_keys holds it (kind and sport
    read off each file), or an iterable of nfl kinds.

    Refuses when: a (sport, kind) has no declaration; a declared id is unregistered
    or does not serve that sport; the sport's producer has an unattributed or
    unreadable SQL read; or SQLite reported a read of an unmapped table on the
    watched connection. -> {(sport, kind): ids}, the statement it approved."""
    if isinstance(files, dict):
        # A sportless kind (sports.json carries no sport) is declared under nfl,
        # whose producer is the one that writes it.
        pairs = sorted({("nfl" if o.get("kind") in SPORTLESS else o.get("sport"), o.get("kind"))
                        for o in files.values()}, key=lambda p: (str(p[0]), str(p[1])))
    else:
        pairs = sorted({("nfl", k) for k in files})
    problems, approved = [], {}
    for sport in sorted({s for s, _ in pairs}, key=str):
        if sport not in DECLARED:
            problems.append(f"sport {sport!r} has no declarations at all")
            continue
        problems += PROBLEMS[sport] + runtime_problems(sport)
    for sport, kind in pairs:
        decl = DECLARED.get(sport, {})
        if kind not in decl:
            problems.append(f"{sport}/{kind}: no source declaration")
            continue
        for i in decl[kind]:
            if i not in SOURCES:
                problems.append(f"{sport}/{kind}: {i!r} is not in the registry")
            elif sport not in SOURCES[i]["sports"] and kind not in SPORTLESS:
                problems.append(f"{sport}/{kind}: {i!r} does not serve {sport}")
        approved[(sport, kind)] = decl[kind]
    if problems:
        raise SourceRegistryError(
            "refusing to export - a file whose sources are not named cannot be listed on "
            "the Sources page. Fix jobs/source_registry.py:\n  "
            + "\n  ".join(sorted(set(problems))))
    return approved
# =============================================================================
# not connected yet
# =============================================================================
#
# What a page cannot show because the PRODUCER does not export it. `state` is
# one of four, and they are different facts:
#   not_ingested            no feed is read at all
#   ingested_not_exported   the data is in a store; no export carries it
#   not_built               the thing that would produce it does not exist
#   declined                decided against, with where the decision lives
# `evidence` names where to check; it carries no figure, so it cannot go stale.
NOT_CONNECTED_STATES = ("not_ingested", "ingested_not_exported", "not_built", "declined")
NOT_CONNECTED = [
    dict(what="Injury report", where="Live, player and team pages",
         state="ingested_not_exported",
         needs="an export of the weekly report, stamped with when it was read",
         evidence="feeds.db injury_reports, versioned by ingestion time (valid_from_ts)"),
    dict(what="Week-by-week prop results", where="Player, prop history",
         state="ingested_not_exported",
         needs="one row per settled line in the player file; today it carries season-and-line "
               "aggregates only",
         evidence="market_log.db outcome_settlement joined to outcomes"),
    dict(what="Pace, and neutral pass rate, EPA per play and pressure rate", where="Teams, team",
         state="ingested_not_exported",
         needs="per-team play-by-play summaries in the team file",
         evidence="nflverse play-by-play is ingested (source_health nflverse:pbp); "
                  "analytics/pace.py computes pace; no team file carries either"),
    dict(what="Prop moves since kickoff", where="Live",
         state="ingested_not_exported",
         needs="a pre-kickoff price kept per prop market in an export; market files cover "
               "unplayed games only",
         evidence="market_log.db quotes (Kalshi, retained per the price history row)"),
    dict(what="Wind", where="Live, team pages",
         state="not_ingested",
         needs="NFL stadium coordinates from a trusted source; weather is read for college "
               "venues only",
         evidence="feeds.db weather_at_kickoff holds sport = cfb rows only"),
    dict(what="Live exchange prices", where="Live",
         state="declined",
         needs="nothing: publishing Kalshi prices to the site on a cycle was withdrawn",
         evidence="_relay/LEDGER.md, 2026-09-22: Kalshi prices on Live dropped (a-09 withdrawn)"),
    dict(what="Pieces written here", where="News",
         state="not_built",
         needs="the generator that writes from the settled record",
         evidence="no producer job writes a news piece"),
]


def check_not_connected():
    bad = [n["what"] for n in NOT_CONNECTED if n["state"] not in NOT_CONNECTED_STATES]
    if bad:
        raise SourceRegistryError(f"not-connected rows with an unknown state: {bad}")
    return f"{len(NOT_CONNECTED)} not-connected rows, states {sorted({n['state'] for n in NOT_CONNECTED})}"


# =============================================================================
# last read
# =============================================================================

def _iso(ts):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def last_read(con, spec):
    """-> (unix ts or None, basis). A None always carries the reason it is None.

    Runs under `quiet()`: these reads describe a source, carry no row into a file,
    and must not land in the watched connection's ledger as an unmapped table."""
    with quiet():
        return _last_read(con, spec)


def _last_read(con, spec):
    how, arg = spec
    if how == "elsewhere":
        return None, f"held in {arg}, which this export does not open"
    if how == "committed":
        return None, (f"a committed result file ({arg}), regenerated by its research script; "
                      "no store records when it was last read")
    if how == "runtime":
        return None, f"read at request time by {arg}; no producer reads it, so nothing measures it"
    if how == "health":
        marks = ",".join("?" * len(arg))
        try:
            ts = con.execute(f"SELECT MAX(last_ok_ts) FROM source_health WHERE source IN ({marks})",
                             arg).fetchone()[0]
        except Exception as e:  # noqa: BLE001 - a missing table is an answer, not a crash
            return None, f"source_health unreadable: {type(e).__name__}"
        return ts, ("last successful read, source_health " + ", ".join(arg)
                    if ts is not None else "no successful read recorded in source_health "
                    + ", ".join(arg))
    if how == "max":
        table, col = arg
        try:
            ts = con.execute(f"SELECT MAX({col}) FROM {table}").fetchone()[0]
        except Exception as e:  # noqa: BLE001
            return None, f"{table} unreadable: {type(e).__name__}"
        return ts, (f"newest {table}.{col}" if ts is not None else f"{table} is empty")
    if how == "quotes":
        best = None
        for source, venue in arg:
            try:
                # Newest-first on (source, ingest_ts): stops at the first matching
                # venue rather than scanning the table.
                r = con.execute(
                    "SELECT ingest_ts FROM quotes INDEXED BY ix_quotes_ingest "
                    "WHERE source = ? AND venue LIKE ? ORDER BY ingest_ts DESC LIMIT 1",
                    (source, venue)).fetchone()
            except Exception:  # noqa: BLE001 - no such index (a scratch store): plain read
                try:
                    r = con.execute(
                        "SELECT MAX(ingest_ts) FROM quotes WHERE source = ? AND venue LIKE ?",
                        (source, venue)).fetchone()
                except Exception as e:  # noqa: BLE001
                    return None, f"quotes unreadable: {type(e).__name__}"
            if r and r[0] is not None and (best is None or r[0] > best):
                best = r[0]
        return best, ("newest quote ingested" if best is not None
                      else "no quote ingested from this venue")
    raise SourceRegistryError(f"unknown last_read spec {how!r}")


# =============================================================================
# the file
# =============================================================================


# =============================================================================
# the file
# =============================================================================

def build_sources(sport, generated_at, con, envelope):
    """The `{sport}/sources.json` file: every source this sport's files read.

    `kinds` is this sport's own declaration - never another sport's filtered by
    `sports`, which is how a-22 listed cfb/manifest as reading only Kalshi and
    mlb/manifest as reading nothing (f-19). `envelope` is export_web's, passed in
    so this module does not import the exporter (which imports it)."""
    # A sportless kind (coverage) reads every sport's stores; a sport's file lists
    # only the part of it that serves this sport, as a-22 did.
    decl = {k: tuple(i for i in ids if k not in SPORTLESS or sport in SOURCES[i]["sports"])
            for k, ids in DECLARED[sport].items()}
    read_by = {}
    for k, ids in sorted(decl.items()):
        for i in ids:
            read_by.setdefault(i, []).append(k)
    for i, pages in PAGE_READS.get(sport, {}).items():
        read_by.setdefault(i, []).extend(pages)
    rows = []
    for sid, s in SOURCES.items():
        if sid not in read_by:
            continue
        ts, basis = last_read(con, s["last_read"])
        provides = s["provides"]() if callable(s["provides"]) else s["provides"]
        rows.append({"source_id": sid, "name": s["name"], "layer": s["layer"],
                     "provides": provides, "used_for": s["used_for"],
                     "read_by": sorted(set(read_by[sid])),
                     "last_read": _iso(ts) if ts is not None else None,
                     "last_read_basis": basis})
    return {**envelope("sources", generated_at, sport),
            "sources": rows,
            "kinds": {k: list(v) for k, v in sorted(decl.items())},
            "not_connected": [dict(n) for n in NOT_CONNECTED]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print the sources file")
    ap.add_argument("--sport", default="nfl", choices=SPORTS)
    a = ap.parse_args(argv)
    from jobs import export_web as E
    print(check_registry(E.CONTRACT["x-contract"]["kinds"]))
    print(check_not_connected())
    if a.json:
        # Only the nfl store is opened: every other sport's last_read is "elsewhere".
        con = E.ro() if a.sport == "nfl" else sqlite3.connect(":memory:")
        try:
            print(json.dumps(build_sources(a.sport, E.iso(), con, E.envelope), indent=1))
        finally:
            con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
