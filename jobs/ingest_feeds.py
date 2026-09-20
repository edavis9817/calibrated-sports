"""Three feeds the site does not have: injuries, weather, news. Free, keyless, local.

    python -m jobs.ingest_feeds                      # status, 0 requests
    python -m jobs.ingest_feeds --injuries 2025      # nflverse official report
    python -m jobs.ingest_feeds --venues 2026        # CFB venue coordinates + dome flag
    python -m jobs.ingest_feeds --weather --days 3   # Open-Meteo at kickoff hour
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
from feeds import fetch, normalize, openmeteo, paths, rss, schema, sources  # noqa: E402

LIMITATIONS = [
    ("feeds.injuries_have_no_capture_time", "structural",
     "The official injury report carries no timestamp of its own",
     "nflverse's injuries file has 16 columns and none is a capture time: no "
     "date_modified, no scraped_at. The report itself is published Wednesday to Friday "
     "and amended, and the file is rewritten in place.",
     "The store versions every row by INGESTION time, so 'what was known at T' is a "
     "query and not a guess. A consumer must read it as of an instant; the current row "
     "set is 'what is known now', which is a different claim. If nflverse ever adds a "
     "capture column the parser REFUSES the file rather than keep guessing."),
    ("feeds.weather_is_hourly_not_at_kickoff", "precision",
     "Weather is the provider's hourly value at the kickoff HOUR",
     "Open-Meteo publishes hourly series. A kickoff at 13:07 is described by the 13:00 "
     "value; nothing is interpolated to the minute.",
     "Quote it as the hour's weather, never as 'the weather at kickoff'. "
     "`observed_hour_ts` is stored beside `kickoff_ts` so the gap is visible."),
    ("feeds.weather_needs_venue_coordinates", "coverage",
     "Weather exists only where the venue's own coordinates do",
     "CFB venue coordinates come from sportsdataverse `cfb_team_info` (venue_id, "
     "latitude, longitude, elevation, timezone, dome). NO feed this project trusts "
     "carries NFL stadium coordinates: nfldata's airports.csv is an AIRPORT, tens of "
     "kilometres from the stadium, and using it would be a proxy standing in for the "
     "thing.",
     "NFL weather is not ingested until a sourced coordinate feed exists. A dome game "
     "is skipped with a reason rather than given a number that cannot matter."),
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
    con.execute("BEGIN")
    ins, closed, same = versioning.apply(con, feed, season, file_id, ts, table, rows,
                                         label=label, part=part, schema_mod=schema)
    con.execute("INSERT INTO feeds_parse_log VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, label, feed, len(rows), ins, closed, same,
                 json.dumps(dropped or {}), "ok", None))
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
        out[season] = f"{outcome} +{ins} -{closed} ={same}"
        if verbose:
            print(f"  injuries {season}: {len(rows):,} rows  {out[season]}")
    return out


def injury_report_as_of(con, season, week, as_of_ts, season_type="REG"):
    """The report as the site would have had it at `as_of_ts` - the point of all this."""
    where, params = versioning.as_of_clause(as_of_ts)
    return con.execute(
        f"SELECT team, player_name, position, report_status, practice_status "
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


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--injuries", metavar="SEASONS", help="2025 | 2009-2025")
    ap.add_argument("--venues", metavar="SEASONS")
    ap.add_argument("--weather", action="store_true")
    ap.add_argument("--days", type=int, default=3, help="weather window either side of now")
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--injuries-report", metavar="SEASON:WEEK",
                    help="print the report AS OF --as-of (default: now)")
    ap.add_argument("--as-of", metavar="ISO8601")
    a = ap.parse_args(argv)

    paths.ensure_dirs()
    if not any((a.injuries, a.venues, a.weather, a.news, a.audit, a.injuries_report)):
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
                for team, name, pos, rep, prac in rows[:40]:
                    print(f"  {team:4} {name:26} {pos or '':4} {rep or '-':12} {prac or '-'}")
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
            if a.news:
                run_news(con)
            return 0
    except AlreadyRunning as e:
        print(f"REFUSING: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
