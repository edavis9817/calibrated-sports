"""Unit f-05 - how far from the stadium each NFL weather reading was taken, and what the
airport proxy would have changed.

    python -m research.nfl_weather_offsets --season 2025

Reads `weather_at_kickoff` (sport='nfl') from the feeds store, after
`python -m jobs.ingest_feeds --nfl-weather --nfl-season 2025`. Part 3 makes Open-Meteo
archive calls (free, keyless) for the airport points and archives every response first.

Prints:
  1. coord_offset_km - the sourced point to the venue footprint, per current row
  2. grid_offset_km  - the sourced point to the cell that answered, by endpoint
  3. the proxy       - for every archive row with playing_conditions = 1, the same hour
     at the home team's nfldata airport: distance, whether the SAME grid cell answered,
     and the absolute difference in temperature and wind
  4. forecast horizon - kickoff_ts - fetched_ts on current forecast rows
"""
import argparse
import csv
import io
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl  # noqa: E402

from feeds import fetch, nfl_venues, openmeteo, sources  # noqa: E402
from jobs import ingest_feeds as J  # noqa: E402

AIRPORTS = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/airports.csv"
# A game this far from its home team's airport is not at the home stadium (London,
# Munich, Sao Paulo...): the proxy was never going to be used for it, so it is left out
# rather than inflating the difference.
HOME_RADIUS_KM = 80.0


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def summary(name, xs, unit):
    if not xs:
        print(f"   {name}: none")
        return
    print(f"   {name}: n={len(xs)} median {q(xs, .5):.2f} p90 {q(xs, .9):.2f} "
          f"max {max(xs):.2f} {unit}")


def latest_schedule(con):
    rel = con.execute("SELECT file_id FROM feeds_raw_files WHERE feed='nfl_schedule' "
                      "ORDER BY fetched_ts DESC LIMIT 1").fetchone()
    if rel is None:
        raise SystemExit("no nfl_schedule in the feeds archive - run the ingest first")
    return pl.read_parquet(io.BytesIO(fetch.read_archived(con, rel[0])))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    a = ap.parse_args(argv)
    con = J.connect()
    client = fetch.Client(con)

    rows = con.execute(
        "SELECT game_id, kind, kickoff_ts, observed_hour_ts, latitude, longitude, "
        "coord_ref, coord_offset_km, grid_offset_km, provider_latitude, provider_longitude, "
        "fetched_ts, playing_conditions, temperature_f, wind_speed_mph "
        "FROM weather_at_kickoff WHERE sport='nfl' AND valid_to_ts IS NULL").fetchall()
    if not rows:
        raise SystemExit("no current NFL weather rows - refusing to report on nothing")
    cols = ["game_id", "kind", "kickoff_ts", "hour_ts", "lat", "lon", "qid", "coord_off",
            "grid_off", "plat", "plon", "fetched_ts", "cond", "temp", "wind"]
    rows = [dict(zip(cols, r)) for r in rows]
    print(f"current NFL rows: {len(rows)}  "
          f"({sum(r['kind'] == 'archive' for r in rows)} archive, "
          f"{sum(r['kind'] == 'forecast' for r in rows)} forecast)")

    print("\n1. coord_offset_km (point to venue footprint)")
    summary("all rows", [r["coord_off"] for r in rows], "km")
    print(f"   rows with a null offset: {sum(r['coord_off'] is None for r in rows)}")
    outside = sorted({r["qid"] for r in rows if r["coord_off"]})
    print(f"   venues whose point is outside the footprint: {outside}")

    print("\n2. grid_offset_km (point to the cell that answered)")
    for kind in ("archive", "forecast"):
        summary(kind, [r["grid_off"] for r in rows if r["kind"] == kind], "km")

    print("\n4. forecast horizon (kickoff - fetched)")
    hz = [(r["kickoff_ts"] - r["fetched_ts"]) / 3600 for r in rows
          if r["kind"] == "forecast" and r["fetched_ts"] is not None]
    summary("hours", hz, "h")
    print(f"   of which after kickoff (negative): {sum(h < 0 for h in hz)}")

    # 3. the proxy
    body = client.get("nfl_airports", AIRPORTS)
    fetch.archive(con, "nfl_airports", None, AIRPORTS, body, suffix=".csv.gz")
    airports = {r["team"]: r for r in csv.DictReader(io.StringIO(body.decode()))}
    g = latest_schedule(con).filter(pl.col("season") == a.season)
    home = dict(g.select("game_id", "home_team").iter_rows())
    cands = []
    for r in rows:
        if r["kind"] != "archive" or r["cond"] != 1 or r["game_id"] not in home:
            continue
        ap_ = airports.get(home[r["game_id"]])
        if ap_ is None:
            continue
        alat, alon = float(ap_["latitude"]), float(ap_["longitude"])
        d = nfl_venues.haversine_km(r["lat"], r["lon"], alat, alon)
        if d > HOME_RADIUS_KM:
            continue
        cands.append((r, alat, alon, d, ap_["airport"]))
    by_date = defaultdict(list)
    for c in cands:
        by_date[openmeteo.day(c[0]["kickoff_ts"])].append(c)
    diffs, same_cell = [], 0
    per_team = defaultdict(list)
    for date, items in sorted(by_date.items()):
        for start in range(0, len(items), sources.OPEN_METEO_MAX_COORDS):
            batch = items[start:start + sources.OPEN_METEO_MAX_COORDS]
            params = openmeteo.build_params([(c[1], c[2]) for c in batch], date, "archive")
            body = client.get("research_airport_weather", sources.OPEN_METEO_ARCHIVE, params)
            fetch.archive(con, "research_airport_weather", f"{date}:archive",
                          sources.OPEN_METEO_ARCHIVE, body, kind="json")
            payload = json.loads(body)
            for i, (r, _la, _lo, d, code) in enumerate(batch):
                got = openmeteo.at_hour(payload, i, r["hour_ts"])
                if got is None or got["temperature_f"] is None or r["temp"] is None:
                    continue
                cell = (got["provider_latitude"], got["provider_longitude"]) == (r["plat"], r["plon"])
                same_cell += cell
                dt, dw = abs(got["temperature_f"] - r["temp"]), abs(got["wind_speed_mph"] - r["wind"])
                diffs.append((d, cell, dt, dw))
                per_team[code].append((d, cell, dt, dw))
    print(f"\n3. the proxy: {len(diffs)} outdoor archive games (playing_conditions=1) at the "
          f"home stadium, re-read at the home team's airport, same hour, same endpoint")
    if not diffs:
        raise SystemExit("no comparable games - refusing to print an empty comparison")
    print(f"   same grid cell answered for stadium and airport: {same_cell} of {len(diffs)}")
    summary("distance stadium-airport", [x[0] for x in diffs], "km")
    summary("|temperature difference|", [x[2] for x in diffs], "F")
    summary("|wind difference|", [x[3] for x in diffs], "mph")
    moved = [x for x in diffs if not x[1]]
    summary("|wind difference| where the cells differ", [x[3] for x in moved], "mph")
    print(f"   games where wind differs by >= 5 mph: {sum(x[3] >= 5 for x in diffs)}; "
          f">= 10 mph: {sum(x[3] >= 10 for x in diffs)}")
    print("   per airport (n, km, same cell, median |dwind| mph, max |dwind|):")
    for code, xs in sorted(per_team.items(), key=lambda kv: -q([x[3] for x in kv[1]], .5)):
        print(f"     {code:4} n={len(xs):2} {xs[0][0]:5.1f} km  same={sum(x[1] for x in xs):2}"
              f"  med {q([x[3] for x in xs], .5):5.2f}  max {max(x[3] for x in xs):5.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
