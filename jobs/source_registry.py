"""THE SOURCE REGISTRY: what every exported kind reads, generated rather than written (a-22).

    python -m jobs.source_registry            # print the check statement and the union
    python -m jobs.source_registry --json     # print the sources file the export would write

WHY THIS EXISTS. `/nfl/sources` was hand-written prose, and on 2026-09-24 it was
wrong about what was connected in three places (audit S-04): it omitted The Odds
API, which every player's prop history and the register's R15 read; it said
"price history: not retained" beside a market block drawing a price path from
retained quotes; and it said "schedule feed: not connected" beside team pages
showing every date, opponent, spread and total. Prose drifts because nothing
checks it against what the code reads. This module is what gets checked.

THREE TABLES, AND WHAT CHECKS EACH ONE.

  SOURCES        source_id -> what it is, what it provides, how it is used, layer.
  KIND_SOURCES   contract kind -> the source_ids a file of that kind reads.
                 EVERY kind in the contract must appear, including kinds other
                 tracks produce; a kind that reads nothing says so with ().
  TABLE_SOURCES  store table -> source_ids, for the tables jobs/export_web.py
                 reads by SQL. `KIND_TABLES` says which of export_web's kinds
                 read which table, and the export kinds' sources are DERIVED
                 from it rather than typed a second time.

THE GATE. `require_declared(kinds)` raises unless every kind is declared and every
id it names is registered. `jobs.export_web.sync_keys` calls it on every file it
writes, so an export of an undeclared kind fails before anything reaches disk -
the same choke point the contract check uses. `tests/test_source_registry.py`
closes the other two gaps: every kind in the contract is declared (a new kind
fails until its sources are named), and every table named in export_web's SQL is
mapped (a new loader reading a new table fails until it is attributed).

WHAT THE GATE DOES NOT COVER, stated so it is not assumed:
  - Tables read INSIDE research modules the export imports (`research.implied`'s
    fits, `research.score.load`) are not scanned: those modules also hold arms
    the export never calls (arm B reads `outcome_close`), so a module-wide scan
    would force a false declaration. Their reads are declared by hand in
    `KIND_EXTRA`, and that half is only as good as the reading behind it.
  - Kinds produced OUTSIDE export_web (analytics, predictor, coverage,
    live.prices) are declared from reading their producers, not enforced there.
  - The page half - "a page may not read a kind whose sources are not listed" -
    belongs to the site, which can enforce it against `kinds` in this file.

"NOT CONNECTED YET" lives here too (audit S-02): what the site cannot show
because the producer does not export it, each with the STATE it is in - never
ingested, ingested but not exported, not built, or declined - because those are
four different reasons and "needs X" collapses them into one.

Sport scope: every source names the sports it serves, and `{sport}/sources.json`
lists the sources for that sport. Registered ids for other sports (the CFB and
MLB stores `coverage` counts) are here so the gate can see them.
"""
import argparse
import json
import sys

import config

LAYERS = ("FACTS", "PRICES", "BELIEFS", "CONTEXT", "HEADLINES")


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


# `last_read`: how "last read" is measured, per source.
#   ("health", names)       max(last_ok_ts) over those source_health rows
#   ("quotes", pairs)       newest ingest_ts in `quotes` for (source, venue LIKE)
#   ("max", (table, col))   max(col) over a table in this store
#   ("elsewhere", store)    held in another store this export does not open
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
        used_for="Headshots and jersey numbers",
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
        used_for="Analytics metrics whose basis is participation",
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
        used_for="The drafting-strengths predictor",
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
                  "and the 2026 lines in each player's prop history"),
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
                  "against the book close"),
        last_read=("quotes", (("live", "oddsapi%"), ("oddsapi_historical", "oddsapi%")))),

    # --- beliefs: ours, and listed because a page shows it
    "calibrated.model": dict(
        name="Calibrated Sports", sports=("nfl",), layer="BELIEFS",
        provides="The baseline usage model's predictions, immutable and timestamped",
        used_for="Scored against the market in the register and on the Method page, never shown "
                 "as a pick",
        last_read=("health", ("predictions",))),

    # --- held in other stores; registered because `coverage` counts them
    "sportsdataverse.cfb": dict(
        name="sportsdataverse", sports=("cfb",), layer="FACTS",
        provides="College play-by-play, box scores, usage and rosters",
        used_for="College stats and coverage counts",
        last_read=("elsewhere", "cfb.db")),
    "cfbd": dict(
        name="CollegeFootballData", sports=("cfb",), layer="FACTS",
        provides="College games with line scores, and book lines with no capture time",
        used_for="College results and historical lines, labelled as book consensus",
        last_read=("elsewhere", "cfb.db")),
    "retrosheet": dict(
        name="Retrosheet", sports=("mlb",), layer="FACTS",
        provides="MLB game, batting and pitching lines, by season",
        used_for="MLB coverage counts",
        last_read=("elsewhere", "mlb.db")),
    "openmeteo": dict(
        name="Open-Meteo", sports=("cfb",), layer="CONTEXT",
        provides="Weather at kickoff, by venue coordinates",
        used_for="Coverage counts",
        last_read=("elsewhere", "feeds.db")),
    "rss.headlines": dict(
        name="Outlet RSS feeds", sports=("nfl", "cfb"), layer="HEADLINES",
        provides="Headline, time and link from each outlet's own feed",
        used_for="Coverage counts. News reads the outlets itself, at request time",
        last_read=("elsewhere", "feeds.db")),
}

# Tables jobs/export_web.py reads BY SQL -> the sources their rows come from.
# The mapping is for THIS reader: `quotes` holds every venue, but the export
# filters to venue = 'kalshi', so it maps to Kalshi alone.
TABLE_SOURCES = {
    "nfl_player_week": ("nflverse.stats",),
    "nflverse_versions": ("nflverse.stats",),
    "nfl_games": ("nflverse.schedule",),
    "nfl_snap_counts": ("nflverse.snap_counts",),
    "player_xwalk": ("nflverse.players",),
    "player_alias": ("nflverse.players",),
    "player_headshot": ("nflverse.rosters",),
    "nfl_roster_week": ("nflverse.rosters",),
    "nfl_teams": ("nflverse.teams",),
    "nfl_pbp_looks": ("nflverse.pbp",),
    # A venue-independent claim: 2023-2025 rows were created from Odds API
    # books, 2026 rows from Kalshi and Polymarket markets (measured 2026-09-24
    # by joining settled player outcomes to market_outcome.venue).
    "outcomes": ("oddsapi", "kalshi.ladders", "polymarket"),
    # The actual, and whether a player with no stat row played.
    "outcome_settlement": ("nflverse.stats", "nflverse.snap_counts"),
    "market_outcome": ("kalshi.ladders",),
    "quotes": ("kalshi.ladders", "kalshi.price_history"),
}

# Which of export_web's kinds read which table. Taken from the loaders each part
# calls in `export()`; staged loaders (roster jerseys, play-by-play looks) are
# included because a staged run reads them.
_PLAYER = ("nfl_games", "nfl_player_week", "player_xwalk", "player_alias", "nfl_snap_counts",
           "nfl_roster_week", "nfl_pbp_looks")
KIND_TABLES = {
    # build_market joins outcomes to market_outcome ON venue = 'kalshi', so the
    # market file reads Kalshi's outcomes only - not the book or Polymarket rows
    # the same table holds. A (table, ids) pair narrows a table for one reader.
    "market": ("nfl_games", "nfl_player_week", "player_xwalk",
               ("outcomes", ("kalshi.ladders",)), "market_outcome", "quotes"),
    "player_summary": _PLAYER + ("player_headshot", "outcomes", "outcome_settlement"),
    "player_season": _PLAYER,
    "player_index": _PLAYER + ("player_headshot",),
    "team": ("nfl_games", "nfl_player_week", "nfl_snap_counts", "player_xwalk", "nfl_roster_week"),
    "components": ("nfl_games", "nfl_player_week", "nfl_snap_counts", "nfl_pbp_looks"),
    "sport_manifest": ("nfl_games", "nfl_player_week", "nfl_teams", "nflverse_versions",
                       "player_xwalk", "nfl_snap_counts"),
}

# Reads that are not SQL in export_web.py, declared by hand (see the module
# docstring for why these are not scanned).
KIND_EXTRA = {
    # counts.market, counts.rungs and teams[].season.markets come off the market files
    "sport_manifest": ("kalshi.ladders",),
    # research.score.load: predictions, Kalshi quotes at entry and close, settlement
    "research.calibration": ("calibrated.model", "kalshi.ladders", "kalshi.price_history",
                             "nflverse.stats", "nflverse.schedule"),
    # research/sweep/results/h3.jsonl, the book lifecycle study (brief 022)
    "research.execution": ("kalshi.ladders", "kalshi.price_history"),
    # docs/hypotheses.json: every register row's script, R01-R17
    "research.hypotheses": ("oddsapi", "kalshi.ladders", "kalshi.price_history", "kalshi.trades",
                            "nflverse.stats", "nflverse.schedule", "nflverse.snap_counts",
                            "nflverse.pbp", "calibrated.model"),
    # produced outside export_web - declared from reading their producers
    "analytics.index": ("nflverse.pbp", "nflverse.participation", "nflverse.ngs"),
    "analytics.metric": ("nflverse.pbp", "nflverse.participation", "nflverse.ngs"),
    "live.prices": ("kalshi.ladders",),
    "predictor": ("nflverse.draft_picks",),
    "coverage": ("nflverse.stats", "nflverse.schedule", "nflverse.snap_counts", "kalshi.ladders",
                 "kalshi.price_history", "kalshi.trades", "polymarket", "oddsapi",
                 "nflverse.injuries", "sportsdataverse.cfb", "cfbd", "retrosheet", "openmeteo",
                 "rss.headlines"),
    # read nothing upstream: a static list, and this registry itself
    "sports": (),
    "sources": (),
}


def _derive():
    out = {}
    for kind, tables in KIND_TABLES.items():
        ids = []
        for t in tables:
            ids.extend(t[1] if isinstance(t, tuple) else TABLE_SOURCES[t])
        out[kind] = ids
    for kind, ids in KIND_EXTRA.items():
        out.setdefault(kind, []).extend(ids)
    ordered = list(SOURCES)
    return {k: tuple(sorted(set(v), key=ordered.index)) for k, v in out.items()}


KIND_SOURCES = _derive()


def check_registry(contract_kinds=None):
    """-> the statement it approved. Raises SourceRegistryError otherwise.

    Every declared id is registered; every source has a known layer and at
    least one reader; every table a kind names is mapped. With `contract_kinds`
    it also refuses any contract kind left undeclared, or declared and absent.
    """
    problems = []
    for sid, s in SOURCES.items():
        if s["layer"] not in LAYERS:
            problems.append(f"{sid}: layer {s['layer']!r} is not one of {LAYERS}")
        if not s["sports"]:
            problems.append(f"{sid}: names no sport")
    for t, ids in TABLE_SOURCES.items():
        for i in ids:
            if i not in SOURCES:
                problems.append(f"table {t}: {i!r} is not a registered source")
    for kind, tables in KIND_TABLES.items():
        for t in tables:
            name, narrowed = t if isinstance(t, tuple) else (t, None)
            if name not in TABLE_SOURCES:
                problems.append(f"kind {kind}: table {name!r} has no entry in TABLE_SOURCES")
            elif narrowed is not None and not set(narrowed) <= set(TABLE_SOURCES[name]):
                # Narrowing may only drop sources, never add one the table does not hold.
                problems.append(f"kind {kind}: {name} narrowed to {narrowed}, not a subset "
                                f"of {TABLE_SOURCES[name]}")
    for kind, ids in KIND_EXTRA.items():
        for i in ids:
            if i not in SOURCES:
                problems.append(f"kind {kind}: {i!r} is not a registered source")
    read = {i for ids in KIND_SOURCES.values() for i in ids}
    for sid in SOURCES:
        if sid not in read:
            # A registered source nothing reads is the inverse lie: it would put
            # a row on Sources for a feed no page draws from.
            problems.append(f"{sid}: registered but no kind reads it")
    if contract_kinds is not None:
        missing = sorted(set(contract_kinds) - set(KIND_SOURCES))
        extra = sorted(set(KIND_SOURCES) - set(contract_kinds))
        if missing:
            problems.append(f"contract kinds with no source declaration: {missing}")
        if extra:
            problems.append(f"declared kinds the contract does not have: {extra}")
    if problems:
        raise SourceRegistryError("source registry refused:\n  " + "\n  ".join(problems))
    return (f"{len(SOURCES)} sources registered, {len(KIND_SOURCES)} kinds declared, "
            f"{len(TABLE_SOURCES)} tables mapped, every source read by at least one kind")


def require_declared(kinds):
    """The export gate: refuse a kind whose sources are undeclared or unregistered.

    Returns the ids the kinds read, so a caller holds the statement rather than
    a boolean. Called from `jobs.export_web.sync_keys` on every file it writes.
    """
    kinds = sorted(set(kinds))
    undeclared = [k for k in kinds if k not in KIND_SOURCES]
    unregistered = sorted({i for k in kinds if k in KIND_SOURCES
                           for i in KIND_SOURCES[k] if i not in SOURCES})
    if undeclared or unregistered:
        raise SourceRegistryError(
            f"refusing to export: kinds with no source declaration {undeclared}, "
            f"declared ids not in the registry {unregistered}. Add them to "
            f"jobs/source_registry.py - a file whose sources are not named cannot be "
            f"listed on the Sources page.")
    return {k: KIND_SOURCES[k] for k in kinds}


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
    """-> (unix ts or None, basis). A None always carries the reason it is None."""
    how, arg = spec
    if how == "elsewhere":
        return None, f"held in {arg}, which this export does not open"
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

def build_sources(sport, generated_at, con, envelope):
    """The `{sport}/sources.json` file: every source this sport's files read.

    `envelope` is export_web's, passed in so this module does not import the
    exporter (which imports it)."""
    kinds = {k: [i for i in ids if sport in SOURCES[i]["sports"]]
             for k, ids in sorted(KIND_SOURCES.items())}
    read_by = {}
    for k, ids in kinds.items():
        for i in ids:
            read_by.setdefault(i, []).append(k)
    rows = []
    for sid, s in SOURCES.items():
        if sport not in s["sports"] or sid not in read_by:
            continue
        ts, basis = last_read(con, s["last_read"])
        provides = s["provides"]() if callable(s["provides"]) else s["provides"]
        rows.append({"source_id": sid, "name": s["name"], "layer": s["layer"],
                     "provides": provides, "used_for": s["used_for"],
                     "read_by": sorted(read_by[sid]),
                     "last_read": _iso(ts) if ts is not None else None,
                     "last_read_basis": basis})
    return {**envelope("sources", generated_at, sport),
            "sources": rows,
            "kinds": {k: v for k, v in kinds.items()},
            "not_connected": [dict(n) for n in NOT_CONNECTED]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print the nfl sources file")
    a = ap.parse_args(argv)
    from jobs import export_web as E
    print(check_registry(E.CONTRACT["x-contract"]["kinds"]))
    print(check_not_connected())
    if a.json:
        con = E.ro()
        try:
            print(json.dumps(build_sources(E.SPORT, E.iso(), con, E.envelope), indent=1))
        finally:
            con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
