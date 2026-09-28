"""Which settled-prop population does a Lab universe support? (a-42, section 3)

    python -m research.lab_prop_population --universe D:/temp/a38/universe
    python -m research.lab_prop_population --universe ... --preset served_fade_every_over.json

The Replay page and the Home artboard printed "43,209 settled props - 2023-2025"
while the Lab reports `price_coverage = {prop: [2023, 2025]}`. That value is a
[from, to] RANGE, not a list of seasons. This prints, from the universe itself:

  * price_coverage as the universe states it;
  * distinct settled OVER claims per season, all books and the dk/fd/mgm
    benchmark trio (the population `research/market_calibration.json` scores);
  * optionally, which seasons a published preset file actually carries figures
    for (a preset holds out `holdout.season`, so its by_season can be shorter
    than the universe's coverage).

Refuses an empty result: a count of zero is a failed read, not a finding.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import polars as pl

BENCH = ("draftkings", "fanduel", "betmgm")


def measure(universe_dir):
    with open(os.path.join(universe_dir, "universe.meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    df = pl.read_parquet(os.path.join(universe_dir, "universe.parquet"))
    over = df.filter((pl.col("bet_type") == "prop") & (pl.col("side") == "over")
                     & (pl.col("outcome") != "void"))
    if over.height == 0:
        raise SystemExit("no settled over-side prop rows in %s - refusing" % universe_dir)

    def per_season(frame):
        return {r["season"]: {"claims": r["claims"], "games": r["games"]}
                for r in frame.group_by("season").agg(
                    pl.col("claim").n_unique().alias("claims"),
                    pl.col("game_id").n_unique().alias("games")).sort("season").to_dicts()}

    bench = over.filter(pl.col("book").is_in(BENCH))
    return {"universe_built": meta.get("built"),
            "price_coverage": meta.get("price_coverage"),
            "all_books": {"claims": over.select(pl.col("claim").n_unique()).item(),
                          "by_season": per_season(over)},
            "benchmark_trio": {"books": list(BENCH),
                               "claims": bench.select(pl.col("claim").n_unique()).item(),
                               "games": bench.select(pl.col("game_id").n_unique()).item(),
                               "by_season": per_season(bench)}}


def preset_seasons(path):
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    return {"key": p["key"], "strategy_seasons": [p["strategy"]["seasons"]["from"],
                                                  p["strategy"]["seasons"]["to"]],
            "holdout": p.get("holdout"),
            "by_season": [(s["key"], s["bets"]) for s in p["by_season"]],
            "bets": p["summary"]["bets"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--universe", required=True)
    ap.add_argument("--preset", action="append", default=[])
    a = ap.parse_args(argv)
    out = measure(a.universe)
    out["presets"] = [preset_seasons(p) for p in a.preset]
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
