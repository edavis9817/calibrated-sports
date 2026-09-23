"""Unit c-01 (track C): the sport-coverage feed. Local files only - NOTHING is published.

    python -m jobs.export_coverage --dry-run             # read, validate, print, write nothing
    python -m jobs.export_coverage --out D:/path/to/dir  # write <dir>/coverage.json

0 requests, 0 credits, no network. Every store is opened `mode=ro`.

WHAT IT SAYS. Per sport the site declares: whether we hold stats, odds and context, the
seasons each covers, and the per-table counts that justify it, each stamped with when it
was read. One file for every sport, so `/nhl` and the home page cannot disagree about
hockey - both read this.

COUNTS ARE READ, NEVER TYPED. Every number below comes out of a query against the table it
describes. What IS declared here is vocabulary - which sports the site carries, which
class a table belongs to, what one row of it means - never a quantity.

NOTHING IS NULL, NOT ZERO. A table holding no rows for a sport contributes nothing; a class
with no contributing table is `null`. `stores` lists every store read and every sport value
found in it, which is what makes a null a measured absence rather than a place nobody
looked. A store that is MISSING refuses the run: a file that is not there says nothing
about whether the data exists.

UNITS, NOT ROWS. nflverse tables keep one row per `data_version`, so `nfl_games` holds
11,084 rows for 7,548 games (measured 2026-09-22). `units` counts distinct things at the
table's grain and is the figure to quote; `rows` is published beside it so the difference
is visible rather than silently resolved.

EVERY TABLE IS CLASSIFIED. Each store's tables are either read (REGISTRY) or excluded with
a reason (IGNORED). A table in neither refuses the run, so an `mlb_player_game` added next
month cannot exist outside the count while this file keeps reporting MLB as null.

THE CONTRACT. `coverage` is a new kind and the contract belongs to track A. Until track A
adopts it, output is validated against `docs/proposals/coverage.defs.json` merged over the
contract's own `$defs`; once the contract carries the kind, the contract is used and the
proposal must be deleted (the test suite fails while both exist).

NOT PUBLISHED, DELIBERATELY. There is no upload path, and `--out` refuses the web export
directory: `weekly_refresh` uploads whatever sits there, so writing into it IS publishing.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                          # noqa: E402
from jobs.export_web import CONTRACT, envelope, iso, write_if_changed  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROPOSAL_PATH = os.path.join(ROOT, "docs", "proposals", "coverage.defs.json")
KIND = "coverage"
KEY = "coverage.json"

# The sports the site declares, in its rail order: calibratedsports-web
# config/sports/index.ts `SPORTS`. A declaration of vocabulary, not a count - and
# tests/test_export_coverage.py compares it with the site's file when that repo is
# checked out beside this one.
SPORTS = ("nfl", "cfb", "nba", "mlb", "nhl")

# The three classes a holding can be. See SportCoverage in the proposal.
CLASSES = ("stats", "odds", "context")

# WHAT WE HAVE DECIDED about each sport, which the stores cannot say (unit c-09). A null
# holding reads the same whether we chose not to work a sport or have simply not got to
# it, and those are different facts: `held` is a decision with a date and a reason,
# `not_attempted` is the absence of one. Declared, like SPORTS - a decision is not a count,
# so nothing here is read from a table, and nothing here states a figure.
#
#   carried        we work this sport; its holdings say how far that has got
#   held           decided against for now - `since` and `reason` are required, and
#                  `revisit` says what would reopen it (never a promise that it will)
#   not_attempted  no decision either way; nothing has been tried - so it may hold nothing
#
# Scope decision 2026-09-22 (_relay/LEDGER.md): the site works on the sports in season.
STATES = ("carried", "held", "not_attempted")
HELD_2026_09_22 = dict(
    state="held", since="2026-09-22",
    reason="Scope decision: effort goes to the sports in season (NFL, CFB, MLB). No ingest, "
           "survey or spend for this sport until the hold is lifted.",
    revisit="A decision by the owner, not a date. Nothing here says when, or whether.")
SPORT_STATUS = {
    "nfl": dict(state="carried", since=None, reason=None, revisit=None),
    "cfb": dict(state="carried", since=None, reason=None, revisit=None),
    "nba": HELD_2026_09_22,
    "mlb": dict(state="carried", since=None, reason=None, revisit=None),
    "nhl": HELD_2026_09_22,
}

# A store not named here is not read at all, so its tables are outside the "every table
# is classified" guard - that guard walks these files and nothing else. mlb.db was added in
# the same commit that created it (c-03); a later store must be added the same way.
STORES = ("market_log.db", "cfb.db", "feeds.db", "mlb.db")


def store_path(name):
    return config.storage_path(name)


def season_of(col):
    """A football season from an event timestamp: the calendar year from March, else the
    year before (a January playoff game belongs to the season that started in September).
    Used only where a table carries no season of its own, and labelled `event_time`."""
    return (f"(CAST(strftime('%Y', {col}, 'unixepoch') AS INTEGER) - "
            f"(CAST(strftime('%m', {col}, 'unixepoch') AS INTEGER) < 3))")


# ---------------------------------------------------------------------------
# the registry: what is read, and what one unit of it is
# ---------------------------------------------------------------------------
#
# Every query returns rows of
#   (sport, source, season, provider, units, rows, first_ts, last_ts, ingested_ts, prune_key)
# grouped finely enough that `units` and `rows` are summable across groups. `provider`
# is a book or venue where the table records one per row, else NULL. `prune_key` is the
# quotes `source` value retention is keyed on, else NULL (nothing else is pruned by age).

CUR = "valid_to_ts IS NULL"      # the current version of a versioned row (cfb, feeds, mlb)


def _versioned(table, source, units_expr, season="season", span=None, provider=None,
               where=None):
    """`source` is a SQL expression: a quoted literal, or a column read from the row."""
    sea = season if season != "event_time" else season_of(span)
    prov = provider or "NULL"
    first, last = (f"MIN({span})", f"MAX({span})") if span else ("NULL", "NULL")
    cond = CUR + (f" AND {where}" if where else "")
    return (f"SELECT sport, {source} AS src, {sea} AS s, {prov} AS p, "
            f"COUNT(DISTINCT {units_expr}), COUNT(*), {first}, {last}, MAX(valid_from_ts), NULL "
            f"FROM {table} WHERE {cond} GROUP BY sport, src, s, p")


REGISTRY = [
    # ---- NFL facts (market_log.db). nflverse keeps a row per data_version, so units are
    # distinct keys WITHOUT data_version.
    dict(store="market_log.db", table="nfl_player_week", cls="stats", grain="player-week",
         basis="column",
         sql="SELECT sport, source, season, NULL, "
             "COUNT(DISTINCT gsis_id || '|' || week || '|' || season_type), COUNT(*), "
             "NULL, NULL, MAX(ingested_ts), NULL FROM nfl_player_week "
             "GROUP BY sport, source, season"),
    dict(store="market_log.db", table="nfl_games", cls="stats",
         grain="scheduled game (includes games not yet played)", basis="column",
         sql="SELECT sport, source, season, NULL, COUNT(DISTINCT game_id), "
             "COUNT(*), MIN(kickoff_ts), MAX(kickoff_ts), MAX(ingested_ts), NULL "
             "FROM nfl_games GROUP BY sport, source, season"),
    dict(store="market_log.db", table="nfl_snap_counts", cls="stats", grain="player-game snap count",
         basis="column",
         sql="SELECT sport, source, season, NULL, "
             "COUNT(DISTINCT pfr_player_id || '|' || game_id), COUNT(*), NULL, NULL, "
             "MAX(ingested_ts), NULL FROM nfl_snap_counts GROUP BY sport, source, season"),
    # ---- NFL prices
    dict(store="market_log.db", table="nfl_games", cls="odds",
         grain="game spread/total on the schedule (the close once played; posted ahead for "
               "games not yet played)", basis="column",
         sql="SELECT sport, source || ' schedule lines', season, NULL, "
             "COUNT(DISTINCT game_id), COUNT(*), MIN(kickoff_ts), MAX(kickoff_ts), "
             "MAX(ingested_ts), NULL FROM nfl_games "
             "WHERE spread_line IS NOT NULL OR total_line IS NOT NULL "
             "GROUP BY sport, source, season"),
    # ONE scan of the largest table in the project (~28M rows, ~65s). Split by venue
    # family and capture path, because a live 14-day window and a purchased backfill are
    # different holdings; the book is the provider.
    dict(store="market_log.db", table="quotes", cls="odds", grain="price quote",
         basis="event_time",
         sql="SELECT sport, "
             "CASE WHEN instr(venue, ':') > 0 THEN substr(venue, 1, instr(venue, ':') - 1) "
             "ELSE venue END || ' ' || COALESCE(source, 'live'), "
             f"{season_of('ts')} AS s, venue, COUNT(*), COUNT(*), MIN(ts), MAX(ts), "
             "MAX(ingest_ts), COALESCE(source, 'live') FROM quotes "
             "GROUP BY sport, venue, source, s"),
    dict(store="market_log.db", table="outcome_close", cls="odds",
         grain="player-prop close (de-vigged book median)", basis="column",
         sql="SELECT o.sport, 'oddsapi closes', o.season, NULL, COUNT(*), COUNT(*), "
             "MIN(c.kickoff_ts), MAX(c.kickoff_ts), MAX(c.built_ts), NULL "
             "FROM outcome_close c JOIN outcomes o USING (outcome_id) "
             "GROUP BY o.sport, o.season"),
    dict(store="market_log.db", table="market_depth", cls="odds",
         grain="order-book depth snapshot", basis="event_time",
         sql="SELECT m.sport, d.venue || ' depth', " + season_of("d.ts") + " AS s, d.venue, "
             "COUNT(*), COUNT(*), MIN(d.ts), MAX(d.ts), NULL, NULL FROM market_depth d "
             "JOIN markets m ON m.venue = d.venue AND m.market_id = d.market_id "
             "GROUP BY m.sport, d.venue, s"),
    dict(store="market_log.db", table="market_trades", cls="odds", grain="trade print",
         basis="event_time",
         sql="SELECT m.sport, t.venue || ' trades', " + season_of("t.ts") + " AS s, t.venue, "
             "COUNT(DISTINCT t.trade_id), COUNT(*), MIN(t.ts), MAX(t.ts), MAX(t.ingest_ts), NULL "
             "FROM market_trades t JOIN markets m ON m.venue = t.venue AND m.market_id = t.market_id "
             "GROUP BY m.sport, t.venue, s"),

    # ---- CFB facts (cfb.db). Versioned per row; current rows only.
    dict(store="cfb.db", table="cfb_games", cls="stats",
         grain="scheduled game (includes games not yet played)", basis="column",
         sql=_versioned("cfb_games", "'sportsdataverse cfb_schedules'", "game_id",
                        span="start_ts")),
    dict(store="cfb.db", table="cfb_player_game_box", cls="stats", grain="player-game box score",
         basis="column",
         sql=_versioned("cfb_player_game_box", "'sportsdataverse espn_cfb_player_box'",
                        "game_id || '|' || athlete_id")),
    dict(store="cfb.db", table="cfb_player_game_usage", cls="stats", grain="player-game usage",
         basis="column",
         sql=_versioned("cfb_player_game_usage", "'sportsdataverse espn_cfb_adv_player_usage'",
                        "game_id || '|' || athlete_id")),
    dict(store="cfb.db", table="cfb_game_rosters", cls="stats", grain="player-game roster entry",
         basis="column",
         sql=_versioned("cfb_game_rosters", "'sportsdataverse espn_cfb_game_rosters'",
                        "game_id || '|' || team_id || '|' || athlete_id")),
    dict(store="cfb.db", table="cfb_rosters", cls="stats", grain="player-season roster entry",
         basis="column",
         sql=_versioned("cfb_rosters", "'sportsdataverse espn_cfb_rosters'",
                        "team_id || '|' || athlete_id")),
    dict(store="cfb.db", table="cfb_teams", cls="stats", grain="team-season", basis="column",
         sql=_versioned("cfb_teams", "'sportsdataverse espn_cfb_teams'", "team_id")),
    dict(store="cfb.db", table="cfb_rankings", cls="stats", grain="poll-week ranking",
         basis="column",
         sql=_versioned("cfb_rankings", "'cfbd rankings'",
                        "season_type || '|' || week || '|' || poll || '|' || team_id")),
    dict(store="cfb.db", table="cfb_cfbd_games", cls="stats", grain="game (with line scores)",
         basis="column",
         sql=_versioned("cfb_cfbd_games", "'cfbd games'", "game_id", span="start_ts")),
    # ---- CFB prices
    dict(store="cfb.db", table="cfb_game_lines", cls="odds",
         grain="game line per provider (no capture time)", basis="column",
         sql=_versioned("cfb_game_lines", "'cfbd lines'", "game_id || '|' || provider",
                        span="start_ts", provider="provider")),
    dict(store="cfb.db", table="cfb_odds_quotes", cls="odds", grain="price quote",
         basis="event_time",
         sql="SELECT sport, 'oddsapi forward', " + season_of("commence_ts") + " AS s, "
             "bookmaker, COUNT(*), COUNT(*), MIN(commence_ts), MAX(commence_ts), "
             "MAX(fetched_ts), NULL FROM cfb_odds_quotes GROUP BY sport, s, bookmaker"),
    dict(store="cfb.db", table="cfb_exchange_closes", cls="odds",
         grain="exchange close (last quote before kickoff)", basis="event_time",
         sql=_versioned("cfb_exchange_closes", "'exchange closes, ' || capture",
                        "venue || '|' || market_id", season="event_time", span="kickoff_ts",
                        provider="venue")),

    # ---- MLB facts (mlb.db, c-03). Retrosheet, versioned per row; current rows only,
    # best-estimate lines only (`stattype = 'value'`).
    # `span_kind="date"`: Retrosheet records a game's local DATE (YYYYMMDD) and no instant,
    # so the span is published as `event_dates`, never as an invented time of day (c-11).
    dict(store="mlb.db", table="mlb_games", cls="stats", grain="game", basis="column",
         span_kind="date",
         sql=_versioned("mlb_games", "'retrosheet'", "game_id", span="date")),
    dict(store="mlb.db", table="mlb_team_games", cls="stats", grain="team-game line",
         basis="column",
         span_kind="date",
         sql=_versioned("mlb_team_games", "'retrosheet'", "game_id || '|' || team",
                        span="date", where="stattype = 'value'")),
    dict(store="mlb.db", table="mlb_batting", cls="stats", grain="player-game batting line",
         basis="column",
         span_kind="date",
         sql=_versioned("mlb_batting", "'retrosheet'",
                        "game_id || '|' || player_id || '|' || team",
                        span="date", where="stattype = 'value'")),
    dict(store="mlb.db", table="mlb_pitching", cls="stats", grain="player-game pitching line",
         basis="column",
         span_kind="date",
         sql=_versioned("mlb_pitching", "'retrosheet'",
                        "game_id || '|' || player_id || '|' || team",
                        span="date", where="stattype = 'value'")),
    dict(store="mlb.db", table="mlb_player_teams", cls="stats",
         grain="player-team-season appearance line", basis="column",
         sql=_versioned("mlb_player_teams", "'retrosheet'", "player_id || '|' || team")),

    # ---- context (feeds.db)
    dict(store="feeds.db", table="injury_reports", cls="context",
         grain="player-week injury report entry", basis="column",
         sql=_versioned("injury_reports", "'nflverse injuries'",
                        "season_type || '|' || week || '|' || team || '|' || player_id")),
    dict(store="feeds.db", table="venues", cls="context", grain="venue-season", basis="column",
         sql=_versioned("venues", "'venues (' || src_dataset || ')'", "venue_id")),
    dict(store="feeds.db", table="weather_at_kickoff", cls="context",
         grain="kickoff-hour weather value", basis="event_time",
         sql=_versioned("weather_at_kickoff", "provider || ' ' || kind",
                        "game_id || '|' || provider || '|' || kind", season="event_time",
                        span="kickoff_ts", provider="provider")),
    dict(store="feeds.db", table="news_items", cls="context", grain="headline", basis="event_time",
         sql=_versioned("news_items", "'rss headlines'", "feed || '|' || guid",
                        season="event_time", span="published_ts", provider="feed")),
]

# Tables deliberately not counted, each with its reason. A table in a store that is in
# neither list refuses the run.
IGNORED = {
    "market_log.db": {
        "ledger_reprice": "derived: repriced paper ledger",
        "market_liquidity": "derived from quotes, which are counted",
        "market_outcome": "mapping between markets and claims, not data",
        "market_trades_fetch": "control: trade-tape fetch progress",
        "markets": "catalogue of instruments; their prices are counted in quotes",
        "model_version_equivalence": "model identity bookkeeping",
        "nfl_teams": "team identity reference, not play",
        "nflverse_versions": "control: release versions",
        "oddsapi_progress": "control: backfill progress",
        "outcome_benchmark": "derived from quotes, which are counted",
        "outcome_settlement": "derived from nfl_player_week, which is counted",
        "outcomes": "the claims themselves (join keys), not prices",
        "paper_ledger": "decisions, not facts",
        "player_alias": "identity crosswalk",
        "player_headshot": "identity: image urls",
        "player_xwalk": "identity crosswalk",
        "poll_log": "control: poll timing",
        "predictions": "beliefs, not facts (invariant 6)",
        "quote_retention_hold": "control: retention holds",
        "raw_shards": "control: raw archive manifest",
        "source_health": "control: job health",
    },
    "cfb.db": {
        "cfb_exchange_markets": "catalogue of instruments; their prices are counted in cfb_exchange_closes",
        "cfb_fetch_checks": "control",
        "cfb_http_log": "control",
        "cfb_limitations": "prose about the data, not data",
        "cfb_measurements": "derived measurements",
        "cfb_odds_event_markets": "catalogue: which markets an event lists, no prices",
        "cfb_odds_events": "catalogue of events; their prices are counted in cfb_odds_quotes",
        "cfb_odds_snapshots": "control: capture schedule",
        "cfb_parse_log": "control",
        "cfb_player_xwalk": "identity crosswalk",
        "cfb_raw_files": "control: raw archive manifest",
        "cfb_runs": "control",
        "cfbd_requests": "control: metered request ledger",
        "f_pbp_columns": "survey metadata about play-by-play files; the plays are not held",
        "f_pbp_files": "survey metadata about play-by-play files; the plays are not held",
        "f_survey_meta": "survey metadata",
        "oddsapi_requests": "control: credit ledger",
    },
    "feeds.db": {
        "feeds_http_log": "control",
        "feeds_limitations": "prose about the data, not data",
        "feeds_measurements": "derived measurements",
        "feeds_parse_log": "control",
        "feeds_preserved_nulls": "control: values held through upstream silence (c-02)",
        "feeds_raw_files": "control: raw archive manifest",
        "feeds_runs": "control",
    },
    "mlb.db": {
        "mlb_http_log": "control",
        "mlb_limitations": "prose about the data, not data",
        "mlb_measurements": "derived measurements",
        "mlb_parse_log": "control",
        "mlb_raw_files": "control: raw archive manifest",
    },
}


class CoverageError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def connect_ro(path):
    if not os.path.isfile(path):
        raise CoverageError(
            f"{path}: store is missing. A missing file says nothing about whether the data "
            f"exists, so it cannot be reported as nothing - refusing.")
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def tables_of(con):
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name")]


def classify(store, tables):
    """(registered, ignored, unclassified) for one store's tables."""
    reg = {e["table"] for e in REGISTRY if e["store"] == store}
    ign = set(IGNORED.get(store, {}))
    both = reg & ign
    if both:
        raise CoverageError(f"{store}: {sorted(both)} are both counted and ignored")
    have = set(tables)
    return have & reg, have & ign, sorted(have - reg - ign)


def sport_values(con, table):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    if "sport" not in cols:
        return set()
    return {r[0] for r in con.execute(f'SELECT DISTINCT sport FROM "{table}"')}


def read_stores(log=print):
    """Run every registered query. Returns (groups, stores): groups is a list of
    (entry, row, counted_at_ts); stores is the CoverageStore list."""
    groups, stores = [], []
    for store in STORES:
        con = connect_ro(store_path(store))
        try:
            tables = tables_of(con)
            reg, ign, unclassified = classify(store, tables)
            if unclassified:
                raise CoverageError(
                    f"{store}: {len(unclassified)} table(s) neither counted nor ignored: "
                    f"{unclassified}. Add each to REGISTRY or to IGNORED with a reason - an "
                    f"unclassified table is data this feed would silently not report.")
            seen = set()
            for entry in [e for e in REGISTRY if e["store"] == store]:
                if entry["table"] not in reg:
                    raise CoverageError(f"{store}: registered table {entry['table']} does not exist")
                t0 = time.time()
                rows = con.execute(entry["sql"]).fetchall()
                counted = time.time()
                log(f"  {store}:{entry['table']} [{entry['cls']}] {len(rows)} group(s) "
                    f"in {counted - t0:.1f}s")
                for r in rows:
                    groups.append((entry, r, counted))
                    seen.add(r[0])
            # sport values in ignored tables too: a sport that exists only there is still
            # a sport the stores know about, and must be one the site declares.
            for t in sorted(ign):
                seen |= sport_values(con, t)
            stores.append({"store": store, "read_at": iso(time.time()),
                           "tables": len(tables), "classified": len(reg) + len(ign),
                           "sports_seen": sorted(s for s in seen if s is not None)})
        finally:
            con.close()
    return groups, stores


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------

def event_date(value, where):
    """A Retrosheet YYYYMMDD date as YYYY-MM-DD, refusing anything else. The span of a
    date-grained table is compared as text, so a malformed value would silently win a
    MIN or a MAX - refuse it rather than publish it as the cutoff."""
    from datetime import date
    s = str(value)
    try:
        if len(s) != 8 or not s.isdigit():
            raise ValueError(s)
        return date(int(s[:4]), int(s[4:6]), int(s[6:])).isoformat()
    except ValueError:
        raise CoverageError(f"{where}: event date {value!r} is not YYYYMMDD; the feed will "
                            f"not publish it as a cutoff") from None


def _aggregate(groups, prune_sources):
    """Fold query groups into CoverageCount dicts keyed by (sport, class)."""
    acc = {}
    for entry, r, counted in groups:
        sport, source, season, provider, units, rows, first, last, ingested, prune_key = r
        if not units or not rows:
            continue
        if season is None:
            raise CoverageError(f"{entry['store']}:{entry['table']}: a group has no season "
                                f"({source}); the feed cannot place it")
        k = (sport, entry["cls"], entry["store"], entry["table"], source)
        a = acc.setdefault(k, {"entry": entry, "units": 0, "rows": 0, "providers": set(),
                               "seasons": set(), "first": None, "last": None,
                               "ingested": None, "rolling": False, "counted": 0.0})
        a["units"] += units
        a["rows"] += rows
        if provider is not None:
            a["providers"].add(provider)
        a["seasons"].add(int(season))
        if first is not None:
            a["first"] = first if a["first"] is None else min(a["first"], first)
            a["last"] = last if a["last"] is None else max(a["last"], last)
        if ingested is not None:
            a["ingested"] = ingested if a["ingested"] is None else max(a["ingested"], ingested)
        if prune_key is not None and prune_key in prune_sources:
            a["rolling"] = True
        a["counted"] = max(a["counted"], counted)
    out = defaultdict(list)
    for (sport, cls, store, table, source), a in sorted(acc.items(), key=lambda kv: kv[0]):
        e = a["entry"]
        dated = e.get("span_kind", "instant") == "date"
        if dated and a["first"] is not None:
            where = f"{store}:{table}"
            dates = {"first": event_date(a["first"], where), "last": event_date(a["last"], where)}
        else:
            dates = None
        out[(sport, cls)].append({
            "store": store, "table": table, "source": source, "grain": e["grain"],
            "units": a["units"], "rows": a["rows"],
            "providers": len(a["providers"]) or None,
            "seasons": sorted(a["seasons"]), "season_basis": e["basis"],
            "span": ({"first": iso(a["first"]), "last": iso(a["last"])}
                     if a["first"] is not None and not dated else None),
            "event_dates": dates,
            "ingested_through": iso(a["ingested"]) if a["ingested"] is not None else None,
            "retention": "rolling" if a["rolling"] else "kept",
            "counted_at": iso(a["counted"]),
        })
    return out


def check_status(status, sports):
    """Refuse a status table that cannot be right, whatever the stores say: every declared
    sport has exactly one status, a `held` sport names when and why, and a sport nobody has
    decided about carries no decision's fields."""
    missing, extra = sorted(set(sports) - set(status)), sorted(set(status) - set(sports))
    if missing or extra:
        raise CoverageError(f"sport status: undeclared {missing}, not a site sport {extra}")
    for s in sports:
        st = status[s]
        if set(st) != {"state", "since", "reason", "revisit"} or st["state"] not in STATES:
            raise CoverageError(f"{s}: status {st!r} is not one of {STATES} with since/reason/revisit")
        if st["state"] == "held" and not (st["since"] and st["reason"]):
            raise CoverageError(f"{s}: `held` is a decision - it needs `since` and `reason`")
        if st["state"] == "not_attempted" and (st["since"] or st["reason"]):
            raise CoverageError(f"{s}: `not_attempted` means no decision was taken, so it "
                                f"cannot carry one's date or reason")


def check_status_against_holdings(per_sport, status):
    """The one contradiction the stores CAN expose: a sport declared not attempted that
    holds data was attempted. `held` may hold data (a hold stops work, it does not delete
    it), and `carried` may hold nothing yet."""
    for rec in per_sport:
        if status[rec["sport"]]["state"] == "not_attempted" and any(rec[c] for c in CLASSES):
            raise CoverageError(f"{rec['sport']}: declared not_attempted, but the stores hold "
                                f"{[c for c in CLASSES if rec[c]]} for it. Something was "
                                f"attempted; declare what was decided.")


def schema_admits_status(contract=CONTRACT):
    """Whether the SportCoverage the output is validated against has a `status` property.
    Until it does the file cannot carry one (every object is closed); the declarations are
    still checked on every run, and the diff is filed to track A."""
    if contract_has_kind(contract):
        defs = contract["$defs"]
    else:
        defs = load_proposal()["$defs"]
    return "status" in defs["SportCoverage"]["properties"]


def build(groups, stores, generated_at, sports=SPORTS, prune_sources=None, status=None,
          emit_status=None):
    prune_sources = (tuple(config.QUOTES_PRUNE_SOURCES) if prune_sources is None
                     else tuple(prune_sources))
    status = SPORT_STATUS if status is None else status
    emit_status = schema_admits_status() if emit_status is None else emit_status
    check_status(status, sports)
    seen = set().union(*(set(s["sports_seen"]) for s in stores)) if stores else set()
    stray = sorted(seen - set(sports))
    if stray:
        raise CoverageError(
            f"the stores hold sport value(s) {stray} that the site does not declare "
            f"({list(sports)}). Either the site gains the sport or the data is mislabelled; "
            f"this feed will not guess which.")
    counts = _aggregate(groups, prune_sources)
    per_sport = []
    for sport in sports:
        rec = {"sport": sport}
        for cls in CLASSES:
            srcs = counts.get((sport, cls), [])
            rec[cls] = ({"seasons": sorted(set().union(*(set(s["seasons"]) for s in srcs))),
                         "sources": srcs} if srcs else None)
        if emit_status:
            rec["status"] = dict(status[sport])
        per_sport.append(rec)
    check_status_against_holdings(per_sport, status)
    return {**envelope(KIND, generated_at, None), "sports": per_sport, "stores": stores}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def load_proposal():
    with open(PROPOSAL_PATH, encoding="utf-8") as f:
        return json.load(f)


def contract_has_kind(contract=CONTRACT):
    return KIND in contract["x-contract"]["kinds"]


def validator(contract=CONTRACT):
    from jsonschema import Draft202012Validator
    if contract_has_kind(contract):
        name = contract["x-contract"]["kinds"][KIND]
        defs = contract["$defs"]
    else:
        prop = load_proposal()
        clash = set(prop["$defs"]) & set(contract["$defs"])
        if clash:
            raise CoverageError(f"proposal $defs collide with the contract's: {sorted(clash)}")
        name = prop["x-contract-additions"]["kinds"][KIND]
        defs = {**contract["$defs"], **prop["$defs"]}
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": defs})


def validate(obj, contract=CONTRACT):
    """Returns the statement it checked against; raises on any violation."""
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
              for e in validator(contract).iter_errors(obj)]
    # the schema cannot say "seasons is the union of its sources" - check it here
    for s in obj.get("sports", []):
        for cls in CLASSES:
            h = s.get(cls)
            if h and sorted(set().union(*(set(x["seasons"]) for x in h["sources"]))) != h["seasons"]:
                errors.append(f"{s['sport']}/{cls}: seasons is not the union of its sources")
            for x in (h or {}).get("sources", []):
                if x["units"] > x["rows"]:
                    errors.append(f"{s['sport']}/{cls}/{x['table']}: units exceed rows")
                if x.get("span") is not None and x.get("event_dates") is not None:
                    errors.append(f"{s['sport']}/{cls}/{x['table']}: span and event_dates "
                                  f"are both set; a table has an instant or a date, not both")
                d = x.get("event_dates")
                if d and d["first"] > d["last"]:
                    errors.append(f"{s['sport']}/{cls}/{x['table']}: event_dates run backwards")
    if errors:
        raise CoverageError(f"{len(errors)} violation(s):\n  " + "\n  ".join(errors[:12]))
    basis = "the contract" if contract_has_kind(contract) else \
        "docs/proposals/coverage.defs.json (NOT YET IN THE CONTRACT)"
    return f"coverage.json valid against {basis}"


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

def refuse_published_dir(out):
    """Writing into the web export directory is publishing: weekly_refresh uploads it."""
    target = os.path.normcase(os.path.realpath(out))
    for name, p in (("WEB_EXPORT_DIR", os.getenv("WEB_EXPORT_DIR")),
                    ("storage web_export", config.storage_path("web_export"))):
        if p and os.path.normcase(os.path.realpath(p)) == target:
            raise CoverageError(f"--out {out} is {name}, which the uploader publishes. "
                                f"This feed is not published; name another directory.")


def summary_lines(obj, status=None):
    status = SPORT_STATUS if status is None else status
    lines = []
    for s in obj["sports"]:
        st = status[s["sport"]]
        parts = [st["state"] + (f" since {st['since']}" if st["since"] else "")]
        for cls in CLASSES:
            h = s[cls]
            if h is None:
                parts.append(f"{cls}: none")
            else:
                se = h["seasons"]
                dates = [x["event_dates"]["last"] for x in h["sources"] if x.get("event_dates")]
                through = f", events through {max(dates)}" if dates else ""
                parts.append(f"{cls}: {len(h['sources'])} source(s), seasons {se[0]}-{se[-1]} "
                             f"({len(se)} distinct){through}")
        lines.append(f"  {s['sport']:4s} " + " | ".join(parts))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", help="directory to write coverage.json into (never the web export dir)")
    g.add_argument("--dry-run", action="store_true", help="read and validate, write nothing")
    args = ap.parse_args(argv)
    if args.out:
        refuse_published_dir(args.out)
    t0 = time.time()
    print(f"reading {len(STORES)} stores under {config.STORAGE_DIR} (read-only)")
    groups, stores = read_stores()
    obj = build(groups, stores, iso(time.time()))
    print(validate(obj))
    if not schema_admits_status():
        print("  sport status: checked, NOT CARRIED - the schema has no `status` yet "
              "(docs/proposals/coverage-status.patch.json, filed to track A as A-C11)")
    for line in summary_lines(obj):
        print(line)
    if args.dry_run:
        print(f"dry run: nothing written ({time.time() - t0:.0f}s)")
        return 0
    os.makedirs(args.out, exist_ok=True)
    changed = write_if_changed(os.path.join(args.out, KEY), obj)
    print(f"{os.path.join(args.out, KEY)}: {'written' if changed else 'unchanged'} "
          f"({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
