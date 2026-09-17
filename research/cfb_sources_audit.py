"""W07 track C - reproduces every figure in the CFB source report (2026-09-16).

    python -m research.cfb_sources_audit

Reads the CFB store and raw archive where the ingest has the file, and
downloads the rest to `<STORAGE_DIR>/cfb/cache/audit/` - never into `cfb/raw`,
because nothing downloaded here is ingested. Free GitHub downloads only: no
CFBD request, no Odds API credit.

Prints:
  1. espn_cfb_betting fabricated lines - rows with odds_source 'default' per
     season, and the constant values they carry
  2. CFBD athlete ids == ESPN athlete ids - 2023 rosters from both
  3. coverage per season - games, box-score games, usage games
  4. the appearance gap - did_not_play True rows, team-games with a starting
     lineup, per season
  5. the 2026 passing layout split, and the TD/INT order check
  6. unattributed usage targets per season
  7. nflverse players.espn_id -> CFB athlete ids for one rookie class
"""
import glob
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import polars as pl

from cfb import paths

SDV = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
CFBD_ROSTER_2023 = ("https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/"
                    "main/rosters/parquet/cfb_rosters_2023.parquet")


def cached(url, name):
    d = paths.root("cache", "audit")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    if not os.path.exists(p):
        r = httpx.get(url, follow_redirects=True, timeout=180)
        r.raise_for_status()
        with open(p + ".part", "wb") as f:
            f.write(r.content)
        os.replace(p + ".part", p)
        time.sleep(0.5)
    return p


def newest(dataset_tag, stem):
    files = sorted(glob.glob(os.path.join(paths.raw_root(), "sportsdataverse", dataset_tag,
                                          stem, "*.parquet")))
    return files[-1] if files else None


def raw_or_download(tag, asset):
    return newest(tag, asset.rsplit(".", 1)[0]) or cached(f"{SDV}/{tag}/{asset}", f"{tag}__{asset}")


def betting():
    print("\n1. espn_cfb_betting - odds_source='default' rows (NOT ingested)")
    print(f"   {'season':<7}{'games':>6}{'default':>8}  default spread/total values")
    for y in range(2004, 2027):
        df = pl.read_parquet(cached(f"{SDV}/espn_cfb_betting/betting_{y}.parquet",
                                    f"betting_{y}.parquet"))
        dflt = df.filter(pl.col("odds_source") == "default")
        vals = sorted(set(zip(dflt["game_spread"].to_list(), dflt["over_under"].to_list())))
        print(f"   {y:<7}{df.height:>6}{dflt.height:>8}  {vals[:3]}")


def id_equality():
    print("\n2. CFBD athlete ids vs ESPN athlete ids, 2023 rosters")
    cfbd = pl.read_parquet(cached(CFBD_ROSTER_2023, "cfbd_rosters_2023.parquet"))
    espn = pl.read_parquet(raw_or_download("espn_cfb_rosters", "cfb_rosters_2023.parquet"))
    a = cfbd.select(pl.col("athlete_id").cast(pl.Int64, strict=False).alias("id"),
                    pl.col("last_name").str.to_lowercase().alias("ln"))
    b = espn.select(pl.col("athlete_id").cast(pl.Int64).alias("id"),
                    pl.col("last_name").str.to_lowercase().alias("ln2")).unique("id")
    j = a.join(b, on="id")
    print(f"   CFBD ids {a.height:,}; present in ESPN {j.height:,}; "
          f"same last name {j.filter(pl.col('ln') == pl.col('ln2')).height:,}")


def store_measurements():
    conn = sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)
    m = {}
    for key, season, value, detail in conn.execute(
            "SELECT key, season, value, detail FROM cfb_measurements"):
        m[(key, season)] = (value, detail)
    seasons = sorted({s for _k, s in m if s})
    g = lambda k, s: m.get((k, s), (None, None))[0]

    print("\n3. coverage per season (from the store)")
    print(f"   {'season':<7}{'games':>7}{'fbs-inv':>8}{'box games':>10}{'usage games':>12}")
    for s in seasons:
        print(f"   {s:<7}{g('games.rows', s) or 0:>7.0f}{g('games.fbs_involved', s) or 0:>8.0f}"
              f"{g('player_box.games', s) or 0:>10.0f}{g('player_usage.games', s) or 0:>12.0f}")

    print("\n4. the appearance gap (game rosters)")
    print(f"   {'season':<7}{'rows':>9}{'dnp True':>9}{'team-games':>11}{'full lineup':>12}{'no starter':>11}")
    for s in seasons:
        if g("game_rosters.rows", s) is None:
            continue
        print(f"   {s:<7}{g('game_rosters.rows', s):>9.0f}{g('game_rosters.did_not_play_true_rows', s):>9.0f}"
              f"{g('game_rosters.team_games', s):>11.0f}"
              f"{g('game_rosters.team_games_full_starting_lineup', s):>12.0f}"
              f"{g('game_rosters.team_games_no_starters', s):>11.0f}")

    print("\n6. usage targets attributed to no player")
    for s in seasons:
        v = m.get(("player_usage.unattributed_targets", s))
        if v:
            print(f"   {s}: {v[0]:.0f} {v[1]}")
    conn.close()


def passing_layout():
    print("\n5. 2026 passing rows: named vs positional (stat_1..stat_5) layout")
    b = pl.read_parquet(raw_or_download("espn_cfb_player_box", "player_box_2026.parquet"))
    p = b.filter(pl.col("category") == "passing")
    named = p.filter(pl.col("passingYards").is_not_null())
    pos = p.filter(pl.col("stat_1").is_not_null())
    f = lambda d, c: d[c].cast(pl.Float64, strict=False).mean()
    print(f"   passing rows {p.height}; named {named.height}; positional {pos.height}")
    chk = pos.with_columns(att=pl.col("stat_1").str.split("/").list.get(1).cast(pl.Float64),
                           yds=pl.col("stat_2").cast(pl.Float64),
                           avg=pl.col("stat_3").cast(pl.Float64)).filter(pl.col("att") > 0)
    ok = int((((chk["yds"] / chk["att"]) - chk["avg"]).abs() < 0.06).sum())
    print(f"   AVG == YDS/ATT on {ok} of {chk.height} positional rows")
    print(f"   named TD {f(named, 'passingTouchdowns'):.2f} INT {f(named, 'interceptions'):.2f}; "
          f"positional stat_4 {f(pos, 'stat_4'):.2f} stat_5 {f(pos, 'stat_5'):.2f}")


def xwalk():
    print("\n7. nflverse players.espn_id -> ESPN CFB athlete ids (rookie season 2024)")
    conn = sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)
    x = pl.DataFrame(conn.execute(
        "SELECT espn_athlete_id, nfl_last_name FROM cfb_player_xwalk "
        "WHERE valid_to_ts IS NULL AND rookie_season=2024").fetchall(),
        schema=["id", "ln"], orient="row")
    r = pl.DataFrame(conn.execute(
        "SELECT DISTINCT athlete_id, last_name FROM cfb_rosters WHERE valid_to_ts IS NULL "
        "AND season=2023").fetchall(), schema=["id", "ln2"], orient="row").unique("id")
    j = x.join(r, on="id")
    same = j.filter(pl.col("ln").str.to_lowercase() == pl.col("ln2").str.to_lowercase()).height
    print(f"   2024 rookies with an espn_id {x.height}; on a 2023 CFB roster {j.height}; "
          f"same last name {same}")
    conn.close()


if __name__ == "__main__":
    betting()
    id_equality()
    store_measurements()
    passing_layout()
    xwalk()
