"""Unit a-66 - what the NFL weather capture holds after the backfill, and whether its wind
is the wind c-31 measured.

    python -m research.nfl_weather_coverage
    python -m research.nfl_weather_coverage --out research/results/a66_nfl_weather_coverage.json

Reads the feeds store (mode=ro) and the newest archived nflverse games.parquet. Writes one
JSON file and no store.

  1. COVERAGE per season: games, rows of each kind, fixed-roof games (skipped by design),
     and games with no row and why (the refusal counts the ingest recorded).
  2. FORECASTS: how many current forecast rows were taken BEFORE their kickoff, and the
     lead time. A row of kind `forecast` taken after kickoff is not a forecast, and is
     counted apart.
  3. THE TWO WINDS. c-31's -0.24 points per mph was measured on nflverse's recorded
     `wind`. The reanalysis here is a model's 10-metre wind for a grid cell. Before either
     is used where the other was fitted, they are compared on the outdoor games both
     describe: means, correlation, and the least-squares line of one on the other.
"""
import argparse
import json
import math
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl  # noqa: E402

import config  # noqa: E402
from feeds import paths as feed_paths  # noqa: E402

DEFAULT_OUT = "research/results/a66_nfl_weather_coverage.json"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def latest_games():
    root = config.storage_path("raw", "nflverse")
    for day in sorted((d for d in os.listdir(root) if _DAY.match(d)), reverse=True):
        p = os.path.join(root, day, "games.parquet")
        if os.path.exists(p):
            return pl.read_parquet(p), day
    raise SystemExit(f"no games.parquet under {root}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    games, version = latest_games()
    db = feed_paths.db_path().replace("\\", "/")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    rows = con.execute(
        "SELECT game_id, kind, kickoff_ts, fetched_ts, wind_speed_mph, temperature_f, "
        "playing_conditions, coord_offset_km, grid_offset_km FROM weather_at_kickoff "
        "WHERE sport = 'nfl' AND valid_to_ts IS NULL").fetchall()
    versions = con.execute("SELECT COUNT(*) FROM weather_at_kickoff WHERE sport = 'nfl'"
                           ).fetchone()[0]
    measures = {(k, s): (v, d) for k, s, v, d in con.execute(
        "SELECT key, scope, value, detail FROM feeds_measurements WHERE key LIKE 'nfl_weather.%'")}
    con.close()
    if not rows:
        raise SystemExit("weather_at_kickoff holds no NFL row - nothing to report")
    kind_of = {}
    for gid, kind, *_ in rows:
        kind_of.setdefault(gid, set()).add(kind)
    season_of = dict(zip(games["game_id"].to_list(), games["season"].to_list()))
    played = dict(zip(games["game_id"].to_list(),
                      [s is not None for s in games["home_score"].to_list()]))
    per = {}
    for s in sorted(set(season_of.values())):
        ids = [g for g, x in season_of.items() if x == s]
        refused = measures.get(("nfl_weather.refused", f"season {s}"), (None, None))
        domed = measures.get(("nfl_weather.domed", f"season {s}"), (None, None))[0]
        beyond = measures.get(("nfl_weather.beyond_forecast_horizon", f"season {s}"),
                              (None, None))[0]
        per[s] = {
            "games": len(ids), "played": sum(1 for g in ids if played[g]),
            "archive": sum(1 for g in ids if "archive" in kind_of.get(g, ())),
            "forecast": sum(1 for g in ids if "forecast" in kind_of.get(g, ())),
            "any_row": sum(1 for g in ids if g in kind_of),
            "fixed_roof_skipped": None if domed is None else int(domed),
            "refused": None if refused[1] is None else json.loads(refused[1]),
            "beyond_forecast_horizon": None if beyond is None else int(beyond),
        }
    fc = [(k - f) / 3600.0 for _g, kind, k, f, *_ in rows if kind == "forecast" and f is not None]
    pre = sorted(x for x in fc if x > 0)
    forecasts = {"current_rows": len(fc), "taken_before_kickoff": len(pre),
                 "taken_after_kickoff": len(fc) - len(pre),
                 "lead_hours_min": round(pre[0], 1) if pre else None,
                 "lead_hours_median": round(pre[len(pre) // 2], 1) if pre else None,
                 "lead_hours_max": round(pre[-1], 1) if pre else None}
    # the two winds, on outdoor games both describe
    nfl_wind = {r["game_id"]: (r["wind"], r["roof"]) for r in games.select(
        "game_id", "wind", "roof").iter_rows(named=True)}
    pairs = []
    for gid, kind, _k, _f, wind, _t, cond, _co, _go in rows:
        w, roof = nfl_wind.get(gid, (None, None))
        if (kind == "archive" and cond == 1 and wind is not None and w is not None
                and not (isinstance(w, float) and math.isnan(w)) and roof in ("outdoors", "open")):
            pairs.append((float(w), float(wind), season_of[gid]))
    n = len(pairs)
    winds = None
    if n >= 30:
        mx = sum(p[0] for p in pairs) / n
        my = sum(p[1] for p in pairs) / n
        sxx = sum((p[0] - mx) ** 2 for p in pairs)
        syy = sum((p[1] - my) ** 2 for p in pairs)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
        winds = {"games": n, "seasons": [min(p[2] for p in pairs), max(p[2] for p in pairs)],
                 "nflverse_recorded_mean_mph": round(mx, 2),
                 "reanalysis_mean_mph": round(my, 2),
                 "correlation": round(sxy / math.sqrt(sxx * syy), 4),
                 "reanalysis_on_recorded": {"slope": round(sxy / sxx, 4),
                                            "intercept": round(my - sxy / sxx * mx, 3)},
                 "recorded_on_reanalysis": {"slope": round(sxy / syy, 4),
                                            "intercept": round(mx - sxy / syy * my, 3)},
                 "mean_abs_difference_mph": round(sum(abs(p[0] - p[1]) for p in pairs) / n, 2)}
    out = {"games_version": version, "current_rows": len(rows), "all_versions": versions,
           "by_kind": {k: sum(1 for r in rows if r[1] == k) for k in ("archive", "forecast")},
           "seasons_with_a_row": [min(s for s, v in per.items() if v["any_row"]),
                                  max(s for s, v in per.items() if v["any_row"])],
           "per_season": per, "forecasts": forecasts, "winds": winds,
           "coord_offset_km_max": max(r[7] for r in rows if r[7] is not None),
           "grid_offset_km_median": sorted(r[8] for r in rows if r[8] is not None)[
               len([r for r in rows if r[8] is not None]) // 2]}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"NFL weather: {len(rows):,} current rows ({out['by_kind']}), {versions:,} versions, "
          f"games.parquet {version}")
    print("season games played archive forecast  fixed-roof  refused")
    for s, v in per.items():
        print(f"{s}  {v['games']:>4} {v['played']:>6} {v['archive']:>7} {v['forecast']:>8}  "
              f"{str(v['fixed_roof_skipped']):>10}  {v['refused']}"
              + (f"  beyond horizon {v['beyond_forecast_horizon']}"
                 if v["beyond_forecast_horizon"] else ""))
    tot = {k: sum(v[k] for v in per.values()) for k in ("games", "played", "archive", "forecast")}
    print(f"all   {tot['games']:>4} {tot['played']:>6} {tot['archive']:>7} {tot['forecast']:>8}")
    print(f"forecasts: {forecasts}")
    print(f"winds: {winds}")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
