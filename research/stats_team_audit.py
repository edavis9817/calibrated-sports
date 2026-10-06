"""Unit a-66 - what nflverse stats_team_week holds, season by season, before anything reads it.

    python -m research.stats_team_audit                      # the store's own archive
    python -m research.stats_team_audit --team-root D:/x/raw/nflverse --out results.json

Three questions, each one a way this file could be read wrong:

  1. THE SILENT-ZERO CLASS. A column present, populated and ZERO (or all NULL / NaN) for
     a run of seasons. Per stored column per season: rows, nulls, NaNs, zeros and the
     sum. NaN is counted apart from NULL - polars counts a NaN as present.
     "Effectively zero" is a season sum under SILENT_FRACTION of the column's median
     non-zero season, never exactly zero: targets taught that an exact test walks past
     a season holding 3.
  2. DOES THE TEAM ROW EQUAL THE SUM OF ITS PLAYER ROWS. Per team-game, for every
     season, against stats_player_week from the same archive. a-64 checked six
     team-games; this is the league.
  3. WHAT `passing_epa` AND `rushing_epa` ARE SUMS OVER, so a per-play figure has a
     denominator that is measured rather than assumed. Candidate definitions are summed
     from play-by-play and compared per team-game.

Reads parquet files only; opens no database and writes one JSON file.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars as pl  # noqa: E402

import config  # noqa: E402
import store  # noqa: E402

TEAM = "stats_team_week_{season}.parquet"
PLAYER = "stats_player_week_{season}.parquet"
PBP = "play_by_play_{season}.parquet"
SILENT_FRACTION = 0.02
DEFAULT_OUT = "research/results/a66_stats_team_audit.json"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# team column -> the player column summed to compare with it
RECONCILE = (("passing_yards", "passing_yards"), ("rushing_yards", "rushing_yards"),
             ("passing_interceptions", "passing_interceptions"), ("attempts", "attempts"),
             ("carries", "carries"), ("completions", "completions"),
             ("sacks_suffered", "sacks_suffered"), ("passing_tds", "passing_tds"),
             ("rushing_tds", "rushing_tds"), ("fumbles_lost_total", "fumbles_lost_total"),
             ("def_interceptions", "def_interceptions"), ("def_sacks", "def_sacks"))
EPA_SEASONS = (2010, 2025, 2026)


def latest(root, asset):
    """(path, pull date) of the newest pull under `root` carrying `asset`, or None."""
    if not os.path.isdir(root):
        return None
    for day in sorted((d for d in os.listdir(root) if _DAY.match(d)), reverse=True):
        p = os.path.join(root, day, asset)
        if os.path.exists(p):
            return p, day
    return None


def seasons_in(root, pattern):
    out = {}
    for s in range(1999, 2100):
        hit = latest(root, pattern.format(season=s))
        if hit:
            out[s] = hit
    return out


def sweep(team_files):
    """{column: {season: {rows, null, nan, zero, sum}}} plus the silent runs."""
    cells = {c: {} for c in store.TEAM_WEEK_COLS}
    absent = {}
    for season, (path, _day) in sorted(team_files.items()):
        df = pl.read_parquet(path).filter(pl.col("team").is_not_null())
        for c in store.TEAM_WEEK_COLS:
            if c not in df.columns:
                absent.setdefault(c, []).append(season)
                continue
            col = df[c].cast(pl.Float64)
            nan = int(col.is_nan().sum() or 0)
            null = int(col.is_null().sum())
            ok = col.filter(col.is_not_null() & ~col.is_nan())
            cells[c][season] = {"rows": df.height, "null": null, "nan": nan,
                                "zero": int((ok == 0).sum()), "sum": float(ok.sum() or 0.0)}
    silent = {}
    for c, by in cells.items():
        sums = sorted(abs(v["sum"]) for v in by.values() if abs(v["sum"]) > 0)
        if not sums:
            silent[c] = {"seasons": sorted(by), "why": "never non-zero"}
            continue
        median = sums[len(sums) // 2]
        bad = []
        for season, v in sorted(by.items()):
            informative = v["rows"] - v["null"] - v["nan"]
            if informative == 0 or abs(v["sum"]) < SILENT_FRACTION * median:
                # EPA sums are signed and can cancel near zero: there the test is
                # whether the VALUES are zero, not whether they net out.
                if informative and v["zero"] < 0.9 * informative:
                    continue
                bad.append(season)
        if bad:
            silent[c] = {"seasons": bad, "first_collected": max(bad) + 1
                         if bad == list(range(min(bad), max(bad) + 1)) and min(bad) == min(by)
                         else None,
                         "median_season_sum": median}
    return cells, absent, silent


def reconcile(team_files, player_files):
    out = {t: {"team_games": 0, "equal": 0, "unequal": 0, "no_player_rows": 0,
               "by_season_unequal": {}} for t, _p in RECONCILE}
    for season in sorted(set(team_files) & set(player_files)):
        tdf = pl.read_parquet(team_files[season][0]).filter(pl.col("team").is_not_null())
        pdf = pl.read_parquet(player_files[season][0])
        keys = ["season", "week", "season_type", "team"]
        for tcol, pcol in RECONCILE:
            if tcol not in tdf.columns or pcol not in pdf.columns:
                continue
            ps = pdf.group_by(keys).agg(pl.col(pcol).cast(pl.Float64).fill_nan(None).sum()
                                        .alias("p"))
            j = tdf.select(*keys, pl.col(tcol).cast(pl.Float64).alias("t")).join(
                ps, on=keys, how="left")
            n = j.height
            nop = j.filter(pl.col("p").is_null()).height
            eq = j.filter(pl.col("p").is_not_null()
                          & ((pl.col("t").fill_null(0) - pl.col("p")).abs() < 1e-6)).height
            o = out[tcol]
            o["team_games"] += n
            o["equal"] += eq
            o["no_player_rows"] += nop
            o["unequal"] += n - eq - nop
            if n - eq - nop:
                o["by_season_unequal"][season] = n - eq - nop
    return out


def epa_definition(team_files, pbp_files):
    """Which play set reproduces passing_epa / rushing_epa per team-game."""
    out = {}
    for season in EPA_SEASONS:
        if season not in team_files or season not in pbp_files:
            continue
        cols = ["game_id", "posteam", "play_type", "epa", "qb_epa", "pass_attempt", "sack",
                "rush_attempt", "qb_dropback", "qb_scramble", "passer_player_id",
                "rusher_player_id", "two_point_attempt", "pass"]
        pb = pl.read_parquet(pbp_files[season][0], columns=cols).filter(
            pl.col("posteam").is_not_null() & pl.col("epa").is_not_null())
        tdf = pl.read_parquet(team_files[season][0]).filter(pl.col("team").is_not_null())
        named_pass = pl.col("passer_player_id").is_not_null()
        named_rush = pl.col("rusher_player_id").is_not_null()
        cands = {
            "passing_epa": {
                "qb_epa on play_type pass with a named passer (attempts and sacks)":
                    (pl.col("play_type") == "pass") & named_pass,
                "qb_epa on pass attempts only (no sacks)":
                    (pl.col("play_type") == "pass") & named_pass & (pl.col("sack") != 1),
                "qb_epa on every qb_dropback": pl.col("qb_dropback") == 1,
            },
            "rushing_epa": {
                "epa on play_type run or qb_kneel with a named rusher":
                    pl.col("play_type").is_in(["run", "qb_kneel"]) & named_rush,
                "epa on play_type run with a named rusher":
                    (pl.col("play_type") == "run") & named_rush,
                "epa on every rush_attempt": pl.col("rush_attempt") == 1,
            },
        }
        res = {}
        for col, defs in cands.items():
            val = "qb_epa" if col == "passing_epa" else "epa"
            res[col] = {}
            for label, flt in defs.items():
                agg = pb.filter(flt).group_by(["game_id", "posteam"]).agg(
                    pl.col(val).sum().alias("x"), pl.len().alias("plays"))
                j = tdf.select("game_id", "team", pl.col(col).alias("t")).join(
                    agg, left_on=["game_id", "team"], right_on=["game_id", "posteam"], how="left")
                d = (j["t"] - j["x"].fill_null(0.0)).abs()
                res[col][label] = {"team_games": j.height,
                                   "within_0.01": int((d < 0.01).sum()),
                                   "max_abs_diff": round(float(d.max()), 4)}
        out[season] = res
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    live = config.storage_path("raw", "nflverse")
    ap.add_argument("--team-root", default=live, help="archive holding stats_team_week files")
    ap.add_argument("--player-root", default=live,
                    help="archive holding stats_player_week and play_by_play files")
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    team = seasons_in(a.team_root, TEAM)
    if not team:
        raise SystemExit(f"no stats_team_week file under {a.team_root} - nothing audited")
    player = seasons_in(a.player_root, PLAYER)
    pbp = seasons_in(a.player_root, PBP)
    keyless = {}
    rows = {}
    for s, (p, _d) in sorted(team.items()):
        df = pl.read_parquet(p)
        k = df.filter(pl.col("team").is_null()).height
        rows[s] = df.height - k
        if k:
            keyless[s] = k
        if int(df["week"].min()) < 1:
            raise SystemExit(f"{s}: a week below 1 - a season-total row would double every sum")
    cells, absent, silent = sweep(team)
    rec = reconcile(team, player)
    epa = epa_definition(team, pbp)
    out = {"seasons": [min(team), max(team)], "files": len(team),
           "pull_dates": sorted({d for _p, d in team.values()}),
           "rows": sum(rows.values()), "rows_by_season": rows, "keyless_rows": keyless,
           "columns": list(store.TEAM_WEEK_COLS), "absent_columns": absent,
           "silent_fraction": SILENT_FRACTION, "silent": silent,
           "reconcile_seasons": sorted(set(team) & set(player)), "reconcile": rec,
           "epa_definition": epa, "cells": cells}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"stats_team_week: {len(team)} files, seasons {min(team)}-{max(team)}, "
          f"{out['rows']:,} team-games, keyless rows {keyless or 0}")
    print(f"\n1. silent columns (season sum under {SILENT_FRACTION:.0%} of the column's median, "
          f"or nothing but NULL/NaN):")
    for c, v in sorted(silent.items()):
        print(f"   {c:28s} {v['seasons'][0]}-{v['seasons'][-1]} ({len(v['seasons'])} seasons)"
              f"  first collected {v.get('first_collected')}")
    nan = {c: sum(v["nan"] for v in by.values()) for c, by in cells.items()}
    print(f"   NaN cells by column: { {c: n for c, n in nan.items() if n} or 0}")
    print(f"   columns absent from a file: {absent or 0}")
    print(f"\n2. team row vs sum of player rows, {len(out['reconcile_seasons'])} seasons:")
    for t, v in rec.items():
        print(f"   {t:24s} team-games {v['team_games']:>6,}  equal {v['equal']:>6,}  "
              f"unequal {v['unequal']:>5,}  no player rows {v['no_player_rows']}")
    print("\n3. what the EPA columns sum over:")
    for season, res in epa.items():
        for col, defs in res.items():
            for label, v in defs.items():
                print(f"   {season} {col:12s} {v['within_0.01']:>4}/{v['team_games']:<4} "
                      f"max diff {v['max_abs_diff']:>8}  {label}")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
