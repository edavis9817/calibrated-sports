"""Three feeds the site does not have: injuries, weather, news. Free, keyless, local.

    python -m jobs.ingest_feeds                      # status, 0 requests
    python -m jobs.ingest_feeds --injuries 2025      # nflverse official report
    python -m jobs.ingest_feeds --venues 2026        # CFB venue coordinates + dome flag
    python -m jobs.ingest_feeds --weather --days 3   # Open-Meteo at kickoff hour
    python -m jobs.ingest_feeds --nfl-weather --days 6           # NFL, around now
    python -m jobs.ingest_feeds --nfl-weather --nfl-season 2025  # NFL, one season
    python -m jobs.ingest_feeds --news               # RSS: headline, source, time, link
    python -m jobs.ingest_feeds --parse              # replay the archive, 0 requests
    python -m jobs.ingest_feeds --audit              # manifest vs disk
    python -m jobs.ingest_feeds --as-of 2026-09-14T13:00Z --injuries-report 2026:2

NO API CREDITS. nflverse and sportsdataverse release assets, public RSS documents, and
Open-Meteo (no key, no account). Nothing here touches the Odds API or CFBD.

WHAT WAS KNOWN, NOT WHAT TURNED OUT. Every fact table is versioned by INGESTION time
through `cfb.versioning.apply` - the same implementation the CFB ingest uses, given this
package's schema, rather than a second copy - so `--as-of` answers "the injury report as
the site would have had it on Sunday morning". The nflverse file carries no capture time
of its own, so that ingestion stamp is the only as-of that exists.

PUBLISHES NOTHING. There is no upload path and no export here; the contract has no kind
for any of these three, which is filed to track A rather than invented locally.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import versioning                                   # noqa: E402
from core.single_instance import AlreadyRunning, InstanceLock  # noqa: E402
from feeds import (fetch, nfl_venues, normalize, openmeteo, paths, rss,  # noqa: E402
                   schema, sources)

LIMITATIONS = [
    ("feeds.injuries_have_no_capture_time", "structural",
     "The official injury report carries no timestamp of its own",
     "nflverse's injuries file carries `date_modified` for 2009-2024 and REMOVED it "
     "from 2025: the 2025 and 2026 files have no capture time. On 2023 and 2024 it sits "
     "a median 47h before the team's kickoff with 75-80% of rows on a Friday ET, so "
     "where it exists it dates the final report. The report is published Wednesday to "
     "Friday and amended, the file is rewritten in place, and it holds ONE "
     "practice_status per player-week, not the Wednesday/Thursday/Friday sequence.",
     "The store versions every row by INGESTION time, so 'what was known at T' is a "
     "query and not a guess. From 2025 that stamp is the only date a row has, and it "
     "dates a report only if the capture ran BEFORE kickoff; a backfilled season is "
     "dated after its own games. A value upstream goes silent on is held rather than "
     "unsaid, and every case is logged in feeds_preserved_nulls. If nflverse ever adds "
     "a capture column the parser REFUSES the file rather than keep guessing."),
    ("feeds.weather_is_hourly_not_at_kickoff", "precision",
     "Weather is the provider's hourly value at the kickoff HOUR",
     "Open-Meteo publishes hourly series. A kickoff at 13:07 is described by the 13:00 "
     "value; nothing is interpolated to the minute.",
     "Quote it as the hour's weather, never as 'the weather at kickoff'. "
     "`observed_hour_ts` is stored beside `kickoff_ts` so the gap is visible."),
    ("feeds.weather_needs_venue_coordinates", "coverage",
     "Weather exists only where the venue's own coordinates do",
     "CFB venue coordinates come from sportsdataverse `cfb_team_info` (venue_id, "
     "latitude, longitude, elevation, timezone, dome); their distance from the venue has "
     "NEVER been measured, so `coord_offset_km` is NULL on every CFB row. NFL coordinates "
     "are Wikidata P625 per venue (feeds/nfl_stadiums.csv maps each nflverse stadium id "
     "AND name to an item), each checked against the OpenStreetMap footprint of the same "
     "item; a venue with no measured offset is not used. nfldata's airports.csv is an "
     "AIRPORT, a median 16 km from the stadium, and is never used as a venue.",
     "Every NFL row carries coord_offset_km (point to venue, 0.0 = inside its footprint) "
     "and every row grid_offset_km (point to the provider cell that answered). A dome "
     "game is skipped with a reason rather than given a number that cannot matter."),
    ("feeds.weather_is_a_model_not_an_observation", "precision",
     "Neither endpoint is a measurement taken at the stadium",
     "`archive` is Open-Meteo's reanalysis and `forecast` is a numerical weather model; "
     "both answer for a GRID CELL, whose centre the response reports and the row stores "
     "as provider_latitude/longitude. The two endpoints answer from different cells for "
     "one stadium.",
     "Say 'modelled weather for the stadium's grid cell', never 'observed at kickoff'. "
     "A forecast row keeps fetched_ts, so its horizon is kickoff_ts - fetched_ts."),
    ("feeds.nfl_roof_is_two_facts", "structural",
     "A roof is the venue's structure AND the game's state, and the feed mislabels both",
     "nflverse's per-game `roof` labels the 2026 Melbourne, Munich and Paris games 'dome' "
     "though none has a roof over the pitch - and labelled the same Allianz Arena "
     "'outdoors' in 2022 and 2024 under a different stadium id. Retractable roofs are '' "
     "until the game is played.",
     "playing_conditions is 1 only when the venue's structure and the game's label agree "
     "the pitch is open, 0 only when both say it is covered, NULL otherwise. A reading "
     "whose playing_conditions is not 1 is outdoor weather, not the game's conditions, "
     "and must not be presented as the second."),
    ("feeds.news_is_headline_only", "structural",
     "News is headline, source, timestamp and link - never text",
     "The parser does not read description, summary or content:encoded, and "
     "`news_items` has no column that could hold them.",
     "The only words this project publishes about someone else's reporting are the "
     "headline they wrote and a link back to them. Anything else we say is generated "
     "from our own data."),
]


def connect(db_path=None):
    db = db_path or paths.db_path()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(schema.ddl())
    for table, col, typ in schema.added_columns(con):
        # Additive only: a store created before a column existed gains it as NULL.
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    bad = schema.news_column_violations()
    if bad:
        raise RuntimeError(f"news_items must not carry article text: {bad}")
    for lid, sev, title, statement, consequence in LIMITATIONS:
        con.execute("INSERT OR REPLACE INTO feeds_limitations VALUES (?,?,?,?,?,?)",
                    (lid, sev, title, statement, consequence, time.time()))
    con.commit()
    return con


def measure(con, key, scope, value, detail=None):
    con.execute("INSERT INTO feeds_measurements (key, scope, value, detail, measured_ts) "
                "VALUES (?,?,?,?,?) ON CONFLICT(key, scope) DO UPDATE SET value=excluded.value, "
                "detail=excluded.detail, measured_ts=excluded.measured_ts",
                (key, str(scope), value, detail, time.time()))
    con.commit()


def _apply(con, feed, season, file_id, rows, table, label, dropped=None, part=None):
    """One versioned write, through the CFB implementation with this schema.

    `season` is the integer scope (a season) and `part` the string one (a weather date
    and endpoint); a feed with neither passes both None and is scoped by its own name."""
    ts = time.time()
    preserved = []
    con.execute("BEGIN")
    ins, closed, same = versioning.apply(con, feed, season, file_id, ts, table, rows,
                                         label=label, part=part, schema_mod=schema,
                                         preserve=schema.PRESERVE.get(table, ()),
                                         dated_by=schema.DATED_BY.get(table, ()),
                                         preserved=preserved)
    con.executemany(
        "INSERT INTO feeds_preserved_nulls VALUES (?,?,?,?,?,?,?,?,?)",
        [(ts, feed, season, file_id, table, json.dumps(list(k), default=str), col,
          None if held is None else str(held), outcome)
         for k, col, held, outcome in preserved])
    detail = None
    if preserved:
        counts = {}
        for _k, col, _h, outcome in preserved:
            counts[f"{col}:{outcome}"] = counts.get(f"{col}:{outcome}", 0) + 1
        detail = "silent upstream: " + json.dumps(counts, sort_keys=True)
    con.execute("INSERT INTO feeds_parse_log VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, label, feed, len(rows), ins, closed, same,
                 json.dumps(dropped or {}), "ok", detail))
    con.commit()
    return ins, closed, same


# ---------------------------------------------------------------------------
# injuries
# ---------------------------------------------------------------------------

def run_injuries(con, seasons, client=None, verbose=True):
    client = client or fetch.Client(con)
    rel = sources.release("injuries")
    out = {}
    for season in seasons:
        body = client.release_asset(rel.feed, rel.repo, rel.tag, rel.asset_name(season))
        if body is None:
            out[season] = "absent"
            continue
        file_id, outcome = fetch.archive(con, rel.feed, season, rel.tag, body, suffix=".parquet.gz")
        rows, dropped = normalize.injuries(body, season)
        ins, closed, same = _apply(con, rel.feed, season, file_id, rows,
                                   rel.table, f"injuries {season}", dropped)
        measure(con, "injuries.rows", season, len(rows), json.dumps(dropped) or None)
        kept = con.execute(
            "SELECT COUNT(*) FROM feeds_preserved_nulls WHERE feed=? AND src_file_id=? "
            "AND outcome='kept'", (rel.feed, file_id)).fetchone()[0]
        out[season] = f"{outcome} +{ins} -{closed} ={same}" + (
            f"  held-through-silence {kept}" if kept else "")
        if verbose:
            print(f"  injuries {season}: {len(rows):,} rows  {out[season]}")
    return out


def injury_report_as_of(con, season, week, as_of_ts, season_type="REG"):
    """The report as the site would have had it at `as_of_ts` - the point of all this.

    Each row carries its DATE, because a designation without one is not usable:
    `upstream_asof_ts` where nflverse published it (2009-2024; on 2023 and 2024 a median
    47h before that team's kickoff, 75-80% of rows on a Friday ET - the final report),
    and `valid_from_ts`, when THIS store first held this version - the only date there
    is from 2025. The second bounds when it was known, not when it was filed, and for a
    season backfilled after the fact it is later than the game."""
    where, params = versioning.as_of_clause(as_of_ts)
    return con.execute(
        f"SELECT team, player_name, position, report_status, practice_status, "
        f"upstream_asof_ts, valid_from_ts "
        f"FROM injury_reports WHERE sport='nfl' AND season=? AND week=? AND season_type=? "
        f"AND {where} ORDER BY team, player_name", (season, week, season_type, *params)
    ).fetchall()


# ---------------------------------------------------------------------------
# venues and weather
# ---------------------------------------------------------------------------

def run_venues(con, seasons, client=None, verbose=True):
    client = client or fetch.Client(con)
    rel = sources.release("cfb_venues")
    out = {}
    for season in seasons:
        body = client.release_asset(rel.feed, rel.repo, rel.tag, rel.asset_name(season))
        if body is None:
            out[season] = "absent"
            continue
        file_id, outcome = fetch.archive(con, rel.feed, season, rel.tag, body, suffix=".parquet.gz")
        rows, dropped = normalize.venues(body, season)
        ins, closed, same = _apply(con, rel.feed, season, file_id, rows, rel.table,
                                   f"venues {season}", dropped)
        domes = sum(1 for r in rows if r[11])
        measure(con, "venues.rows", season, len(rows), json.dumps(dropped) or None)
        measure(con, "venues.domes", season, domes)
        out[season] = f"{outcome} +{ins} -{closed} ={same}"
        if verbose:
            print(f"  venues {season}: {len(rows):,} venues ({domes} domed)  {out[season]}")
    return out


def venue_for_games(con, cfb_db, days, now=None):
    """[(game_id, kickoff_ts, venue row)] for CFB games inside the window whose venue has
    coordinates. A game with no venue row is REPORTED, never given a nearby guess."""
    now = time.time() if now is None else now
    lo, hi = now - days * 86400, now + days * 86400
    venues = {}
    for r in con.execute("SELECT venue_id, name, latitude, longitude, dome, season FROM venues "
                         "WHERE sport='cfb' AND valid_to_ts IS NULL ORDER BY season"):
        venues[r[0]] = {"venue_id": r[0], "name": r[1], "latitude": r[2], "longitude": r[3],
                        "dome": r[4]}
    games = cfb_db.execute(
        "SELECT game_id, start_ts, venue_id, venue FROM cfb_games WHERE valid_to_ts IS NULL "
        "AND start_ts BETWEEN ? AND ? AND start_time_tbd = 0 ORDER BY start_ts", (lo, hi)
    ).fetchall()
    matched, missing, domed = [], [], []
    for gid, ts, vid, vname in games:
        v = venues.get(vid)
        if v is None:
            missing.append((gid, vid, vname))
        elif v["dome"]:
            domed.append((gid, v["name"]))
        else:
            matched.append((str(gid), ts, v))
    return matched, missing, domed


def run_weather(con, cfb_db, days=3, client=None, now=None, verbose=True):
    """One Open-Meteo call per (date, batch of venues). 0 credits, no key."""
    client = client or fetch.Client(con)
    now = time.time() if now is None else now
    matched, missing, domed = venue_for_games(con, cfb_db, days, now)
    if verbose:
        print(f"  games in window: {len(matched) + len(missing) + len(domed)}  "
              f"with coordinates {len(matched)}  domed (skipped) {len(domed)}  "
              f"no venue row {len(missing)}")
    measure(con, "weather.games_without_venue", "window", len(missing),
            json.dumps([m[0] for m in missing[:20]]))
    measure(con, "weather.games_domed", "window", len(domed))

    by_day = {}
    for gid, ts, v in matched:
        by_day.setdefault((openmeteo.day(ts), openmeteo.endpoint_for(ts, now)[0]), []).append(
            (gid, ts, v))
    written = 0
    for (date, kind), items in sorted(by_day.items()):
        url = (sources.OPEN_METEO_ARCHIVE if kind == "archive" else sources.OPEN_METEO_FORECAST)
        # ONE versioned write per (date, kind), not one per batch. The version scope is
        # (feed, part), so applying each batch separately closed the previous batch's
        # rows as "no longer carried" - visible as +50 -50 on the second call and a
        # store holding only the last batch.
        rows, last_file = [], None
        for start in range(0, len(items), sources.OPEN_METEO_MAX_COORDS):
            batch = items[start:start + sources.OPEN_METEO_MAX_COORDS]
            points = [(v["latitude"], v["longitude"]) for _g, _t, v in batch]
            params = openmeteo.build_params(points, date, kind)
            body = client.get("weather", url, params)
            last_file, _outcome = fetch.archive(con, "weather", f"{date}:{kind}", url, body,
                                                kind="json")
            payload = json.loads(body)
            for i, (gid, ts, v) in enumerate(batch):
                measured = openmeteo.at_hour(payload, i, openmeteo.floor_hour(ts))
                if measured is None:
                    continue
                rows.append(openmeteo.row("cfb", gid, kind, v, ts, measured))
        if rows:
            ins, closed, same = _apply(con, "weather", None, last_file, rows,
                                       "weather_at_kickoff", f"weather {date} {kind}",
                                       part=f"{date}:{kind}")
            written += ins
            if verbose:
                print(f"  weather {date} {kind}: {len(rows)} games in "
                      f"{-(-len(items) // sources.OPEN_METEO_MAX_COORDS)} call(s)  "
                      f"+{ins} -{closed} ={same}")
    return {"games": len(matched), "rows_written": written, "domed": len(domed),
            "no_venue": len(missing)}


def nfl_games(con, client):
    """The nflverse schedule, fetched and archived verbatim, as a polars frame."""
    import io

    import polars as pl
    rel = sources.release("nfl_schedule")
    body = client.release_asset(rel.feed, rel.repo, rel.tag, rel.asset_name(None))
    if body is None:
        raise fetch.FetchError("nflverse schedules/games.parquet is not listed")
    fetch.archive(con, rel.feed, None, rel.tag, body, suffix=".parquet.gz")
    return pl.read_parquet(io.BytesIO(body))


def nfl_games_for_weather(games, *, days=None, season=None, now=None,
                          crosswalk=None, points=None):
    """(matched, refused, domed) for the NFL games in scope.

    SCOPE IS WHOLE UTC DATES. A weather version's scope is (feed, "date:kind"), so a
    window that cut a date in half would close the other half's rows as "no longer
    carried" on the next run. `days` therefore takes every game on every UTC date the
    window touches, never a timestamp range.

    matched: [(game_id, kickoff_ts, venue, game_roof, playing_conditions)]
    refused: [(game_id, reason)] - the (stadium_id, name) pair is not in the crosswalk,
             the venue has no coordinate or no measured offset, or there is no kickoff
             time. Never a nearby guess.
    domed:   [(game_id, venue name)] - a fixed roof both sources say is shut: skipped.
    """
    now = time.time() if now is None else now
    crosswalk = nfl_venues.load_crosswalk() if crosswalk is None else crosswalk
    points = nfl_venues.load_points() if points is None else points
    if season is None and days is None:
        raise ValueError("a season or a window: weather over everything is not a scope")
    lo_day = openmeteo.day(now - (days or 0) * 86400)
    hi_day = openmeteo.day(now + (days or 0) * 86400)
    matched, refused, domed = [], [], []
    for r in games.iter_rows(named=True):
        ts = nfl_venues.kickoff_ts(r.get("gameday"), r.get("gametime"))
        if season is not None:
            if r["season"] != season:
                continue
        elif ts is None or not lo_day <= openmeteo.day(ts) <= hi_day:
            continue
        if ts is None:
            refused.append((r["game_id"], "no_kickoff_time"))
            continue
        venue, why = nfl_venues.resolve(r.get("stadium_id"), r.get("stadium"),
                                        crosswalk, points)
        if venue is None:
            refused.append((r["game_id"], why))
            continue
        cond = nfl_venues.playing_conditions(venue["roof_type"], r.get("roof"))
        if venue["roof_type"] == "fixed" and cond == 0:
            domed.append((r["game_id"], venue["name"]))
            continue
        matched.append((r["game_id"], ts, venue, r.get("roof") or None, cond))
    return matched, refused, domed


def run_nfl_weather(con, games, *, days=None, season=None, client=None, now=None,
                    verbose=True):
    """Open-Meteo at the kickoff hour for NFL games, at each venue's own sourced point.

    Written under its own feed, `weather_nfl`: the version scope is (feed, part), so
    sharing CFB's `weather` feed would let an NFL run close every CFB row of that date.
    """
    client = client or fetch.Client(con)
    now = time.time() if now is None else now
    matched, refused, domed = nfl_games_for_weather(games, days=days, season=season, now=now)
    reasons = {}
    for _g, why in refused:
        reasons[why] = reasons.get(why, 0) + 1
    unknown = sum(1 for m in matched if m[4] is None)
    if verbose:
        print(f"  nfl games in scope: {len(matched) + len(refused) + len(domed)}  "
              f"fetchable {len(matched)} (playing conditions unknown {unknown})  "
              f"domed (skipped) {len(domed)}  refused {reasons or 0}")
    scope = f"season {season}" if season is not None else f"window {days}d"
    measure(con, "nfl_weather.refused", scope, len(refused), json.dumps(reasons))
    measure(con, "nfl_weather.domed", scope, len(domed))
    measure(con, "nfl_weather.conditions_unknown", scope, unknown)

    by_day = {}
    for item in matched:
        key = (openmeteo.day(item[1]), openmeteo.endpoint_for(item[1], now)[0])
        by_day.setdefault(key, []).append(item)
    written, missing_hour = 0, 0
    for (date, kind), items in sorted(by_day.items()):
        url = sources.OPEN_METEO_ARCHIVE if kind == "archive" else sources.OPEN_METEO_FORECAST
        # One versioned write per (date, kind), never per batch - see run_weather.
        rows, last_file = [], None
        for start in range(0, len(items), sources.OPEN_METEO_MAX_COORDS):
            batch = items[start:start + sources.OPEN_METEO_MAX_COORDS]
            params = openmeteo.build_params(
                [(v["latitude"], v["longitude"]) for _g, _t, v, _r, _c in batch], date, kind)
            fetched = time.time()
            body = client.get("weather_nfl", url, params)
            last_file, _o = fetch.archive(con, "weather_nfl", f"{date}:{kind}", url, body,
                                          fetched_ts=fetched, kind="json")
            payload = json.loads(body)
            for i, (gid, ts, v, roof, cond) in enumerate(batch):
                got = openmeteo.at_hour(payload, i, openmeteo.floor_hour(ts))
                if got is None:
                    missing_hour += 1
                    continue
                rows.append(openmeteo.row(
                    "nfl", gid, kind, v, ts, got, fetched_ts=fetched,
                    coord_source=nfl_venues.COORD_SOURCE, coord_ref=v["qid"],
                    coord_offset_km=v["offset_km"], roof_type=v["roof_type"],
                    game_roof=roof, playing_conditions=cond))
        if rows:
            ins, closed, same = _apply(con, "weather_nfl", None, last_file, rows,
                                       "weather_at_kickoff", f"weather_nfl {date} {kind}",
                                       part=f"{date}:{kind}")
            written += ins
            if verbose:
                print(f"  weather_nfl {date} {kind}: {len(rows)} games  "
                      f"+{ins} -{closed} ={same}")
    measure(con, "nfl_weather.hour_not_published", scope, missing_hour)
    return {"games": len(matched), "rows_written": written, "domed": len(domed),
            "refused": reasons, "conditions_unknown": unknown,
            "hour_not_published": missing_hour}


def nfl_weather_readout(con):
    """Current NFL rows with both offsets and the forecast horizon, for a reader."""
    return con.execute(
        "SELECT game_id, kind, kickoff_ts, coord_ref, coord_offset_km, grid_offset_km, "
        "CASE WHEN fetched_ts IS NULL THEN NULL ELSE (kickoff_ts - fetched_ts) / 3600.0 END, "
        "roof_type, game_roof, playing_conditions, temperature_f, wind_speed_mph "
        "FROM weather_at_kickoff WHERE sport='nfl' AND valid_to_ts IS NULL "
        "ORDER BY kickoff_ts").fetchall()


# ---------------------------------------------------------------------------
# news
# ---------------------------------------------------------------------------

def run_news(con, feeds=None, client=None, verbose=True):
    client = client or fetch.Client(con)
    out = {}
    for name in (feeds or sources.RSS_FEEDS):
        sport, url = sources.RSS_FEEDS[name]
        try:
            body = client.get(name, url)
        except fetch.FetchError as e:
            out[name] = f"failed: {e}"
            if verbose:
                print(f"  news {name}: {out[name]}")
            continue
        file_id, outcome = fetch.archive(con, name, None, url, body, suffix=".xml.gz")
        items = rss.parse(body, name)
        rows = rss.rows(items, sport, name)
        ins, closed, same = _apply(con, name, None, file_id, rows, "news_items", f"news {name}")
        undated = sum(1 for i in items if i["published_ts"] is None)
        measure(con, "news.items", name, len(rows),
                json.dumps({"undated": undated}) if undated else None)
        out[name] = f"{outcome} {len(rows)} items +{ins} -{closed} ={same}"
        if verbose:
            print(f"  news {name}: {out[name]}"
                  + (f"  ({undated} with no timestamp)" if undated else ""))
    return out


# ---------------------------------------------------------------------------
# audit, status, CLI
# ---------------------------------------------------------------------------

def audit(con):
    root = paths.raw_root()
    on_disk = set()
    for dirpath, _d, files in os.walk(root):
        for f in files:
            on_disk.add(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    manifest = {r[0] for r in con.execute("SELECT rel_path FROM feeds_raw_files")}
    unreadable = []
    import gzip
    for rel in sorted(manifest & on_disk):
        try:
            with gzip.open(os.path.join(root, *rel.split("/")), "rb") as f:
                f.read(1 << 16)
        except Exception as e:
            unreadable.append((rel, f"{type(e).__name__}: {e}"))
    statement = (f"feeds raw audit: {len(on_disk)} on disk, {len(manifest)} manifested, "
                 f"unregistered {len(on_disk - manifest)}, missing {len(manifest - on_disk)}, "
                 f"unreadable {len(unreadable)}")
    print(f"  {statement}")
    for rel, why in unreadable[:10]:
        print(f"    UNREADABLE {rel}: {why}")
    return {"statement": statement, "clean": not (on_disk - manifest) and not (manifest - on_disk)
            and not unreadable, "unreadable": unreadable}


def status(con):
    print(f"feeds store  db={paths.db_path()}")
    for table in schema.TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE valid_to_ts IS NULL").fetchone()[0]
        allv = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:20} current {n:>8,}  all versions {allv:>8,}")
    files, nbytes = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM feeds_raw_files").fetchone()
    print(f"  raw files {files}  {nbytes / 1e6:.1f} MB (uncompressed)")
    print("  limitations:")
    for lid, title in con.execute("SELECT id, title FROM feeds_limitations ORDER BY id"):
        print(f"    {lid:44} {title}")


def parse_seasons(spec):
    out = set()
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--injuries", metavar="SEASONS", help="2025 | 2009-2025")
    ap.add_argument("--venues", metavar="SEASONS")
    ap.add_argument("--weather", action="store_true")
    ap.add_argument("--days", type=int, default=3, help="weather window either side of now")
    ap.add_argument("--nfl-weather", action="store_true",
                    help="NFL weather at sourced stadium coordinates (--days or --nfl-season)")
    ap.add_argument("--nfl-season", type=int, metavar="YEAR")
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--injuries-report", metavar="SEASON:WEEK",
                    help="print the report AS OF --as-of (default: now)")
    ap.add_argument("--as-of", metavar="ISO8601")
    a = ap.parse_args(argv)

    paths.ensure_dirs()
    if not any((a.injuries, a.venues, a.weather, a.nfl_weather, a.news, a.audit,
                a.injuries_report)):
        status(connect())
        return 0
    try:
        with InstanceLock(paths.lock_path()):
            con = connect()
            if a.audit:
                return 0 if audit(con)["clean"] else 1
            if a.injuries_report:
                season, week = (int(x) for x in a.injuries_report.split(":"))
                ts = _iso(a.as_of) if a.as_of else time.time()
                rows = injury_report_as_of(con, season, week, ts)
                when = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
                print(f"injury report {season} week {week}, as the store had it at {when}: "
                      f"{len(rows)} rows")
                for team, name, pos, rep, prac, up_ts, from_ts in rows[:40]:
                    dated = (f"filed {_utc(up_ts)}" if up_ts is not None
                             else f"held since {_utc(from_ts)}")
                    print(f"  {team:4} {name:26} {pos or '':4} {rep or '-':12} "
                          f"{(prac or '-').strip():36} {dated}")
                return 0
            if a.injuries:
                run_injuries(con, parse_seasons(a.injuries))
            if a.venues:
                run_venues(con, parse_seasons(a.venues))
            if a.weather:
                from cfb import paths as cfb_paths
                cfb = sqlite3.connect(f"file:{cfb_paths.db_path()}?mode=ro", uri=True)
                try:
                    print(f"weather: {run_weather(con, cfb, a.days)}")
                finally:
                    cfb.close()
            if a.nfl_weather:
                client = fetch.Client(con)
                out = run_nfl_weather(con, nfl_games(con, client), client=client,
                                      days=None if a.nfl_season else a.days,
                                      season=a.nfl_season)
                print(f"nfl weather: {out}")
            if a.news:
                run_news(con)
            return 0
    except AlreadyRunning as e:
        print(f"REFUSING: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
