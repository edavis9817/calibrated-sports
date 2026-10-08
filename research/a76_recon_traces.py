"""a-76: two loose threads from a-71's team-row / player-row reconciliation.

    python -m research.a76_recon_traces --team-raw <raw dir> --player-raw <raw dir> \
        [--a64-file <stats_team_week_2026.parquet a-64 downloaded>]

Reads parquet files only.

  1. fumbles   a-64 reported CLE 2026 week 3 fumbles lost "1 against 0" and left
               fumbles off the matchup page; a-71 read 1 against 1 and could not
               reproduce it. Both definitions are computed here for the six
               PIT/CLE team-games a-64 checked: the team row's
               fumbles_lost_total against (a) the player rows' fumbles_lost_total
               and (b) the player rows' three TYPED columns (sack + rushing +
               receiving fumbles lost). a-64's comparison code is not on disk, so
               which one it ran is an inference from which one returns 1 and 0.
  2. def_sacks a-71 found team def_sacks differs from the player-row sum on 99
               of 14,659 team-games and did not look for the cause. Tested here:
               stats_player_week carries rows with NO player (null name; id null
               or '0') that pool unidentified credits under one team label.
"""
import argparse
import glob
import os
import sys

import polars as pl

TYPED = ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")


def newest(raw, stem, season):
    hits = sorted(glob.glob(os.path.join(raw, "nflverse", "*", f"{stem}_{season}.parquet")))
    return hits[-1] if hits else None


def fumbles(team_raw, player_raw, a64_file, seasons):
    print("== 1. fumbles lost: team row against two different player-row sums")
    tot = [0, 0, 0]
    for s in seasons:
        tf, pf = newest(team_raw, "stats_team_week", s), newest(player_raw, "stats_player_week", s)
        if not tf or not pf:
            continue
        if s == 2026 and a64_file:
            tf = a64_file
        t, p = pl.read_parquet(tf), pl.read_parquet(pf)
        ps = p.group_by(["season", "week", "team"]).agg(
            pl.col("fumbles_lost_total").fill_null(0).sum().alias("p_total"),
            pl.sum_horizontal([pl.col(c).fill_null(0) for c in TYPED]).sum().alias("p_typed"))
        j = t.select("season", "week", "team", pl.col("fumbles_lost_total").fill_null(0).alias("t_total"),
                     pl.sum_horizontal([pl.col(c).fill_null(0) for c in TYPED]).alias("t_typed")).join(
            ps, on=["season", "week", "team"], how="left").fill_null(0)
        tot[0] += j.height
        tot[1] += j.filter(pl.col("t_total") != pl.col("p_total")).height
        tot[2] += j.filter(pl.col("t_total") != pl.col("p_typed")).height
        if s == 2026:
            print(f"  team file: {tf}")
            print(f"  player file: {pf}")
            print("  a-64's six team-games (team total | team typed sum | players total | players typed sum):")
            six = j.filter(pl.col("team").is_in(["PIT", "CLE"]) & (pl.col("week") <= 3)).sort(["team", "week"])
            if six.height != 6:
                raise SystemExit(f"expected a-64's six PIT/CLE team-games, found {six.height}")
            for r in six.iter_rows(named=True):
                flag = "  <- 1 against 0" if (r["t_total"], r["p_typed"]) == (1, 0) else ""
                print(f"    {r['team']} wk{r['week']}: {r['t_total']} | {r['t_typed']} | {r['p_total']} | {r['p_typed']}{flag}")
            who = p.filter((pl.col("team") == "CLE") & (pl.col("week") == 3)
                           & (pl.col("fumbles_lost_total").fill_null(0) > 0))
            for r in who.iter_rows(named=True):
                print(f"    CLE wk3 lost fumble is on {r['player_display_name']} ({r['position']}): "
                      f"fumbles_lost_total {r['fumbles_lost_total']}, typed columns "
                      f"{[r[c] for c in TYPED]}")
    if not tot[0]:
        raise SystemExit("no team-game was compared - check the raw dirs")
    print(f"  all seasons {seasons[0]}-{seasons[-1]}: {tot[0]:,} team-games; team total != players total on "
          f"{tot[1]:,}; team total != players typed sum on {tot[2]:,}")


def sacks(team_raw, player_raw, seasons):
    print("\n== 2. def_sacks: team row against the sum of player rows")
    frames, nameless_rows, nameless_sack_rows, ids = [], 0, 0, {}
    for s in seasons:
        tf, pf = newest(team_raw, "stats_team_week", s), newest(player_raw, "stats_player_week", s)
        if not tf or not pf:
            continue
        t, p = pl.read_parquet(tf), pl.read_parquet(pf)
        anon = pl.col("player_display_name").is_null()
        u = p.filter(anon)
        nameless_rows += u.height
        nameless_sack_rows += u.filter(pl.col("def_sacks").fill_null(0) > 0).height
        for i in u["player_id"].to_list():
            ids[i] = ids.get(i, 0) + 1
        ps = p.group_by(["season", "week", "team"]).agg(
            pl.col("def_sacks").cast(pl.Float64).fill_null(0).sum().alias("p_all"),
            pl.col("def_sacks").cast(pl.Float64).fill_null(0).filter(~anon).sum().alias("p_named"),
            pl.col("def_sacks").cast(pl.Float64).fill_null(0).filter(anon).sum().alias("p_anon"))
        opp = t.select("season", "week", pl.col("team").alias("opponent_team"),
                       pl.col("sacks_suffered").alias("opp_suffered"))
        frames.append(t.select("season", "week", "team", "opponent_team",
                               pl.col("def_sacks").cast(pl.Float64).fill_null(0).alias("t"))
                      .join(ps, on=["season", "week", "team"], how="left").fill_null(0)
                      .join(opp, on=["season", "week", "opponent_team"], how="left"))
    a = pl.concat(frames).with_columns((pl.col("t") - pl.col("p_all")).alias("d"))
    d = a.filter(pl.col("d").abs() > 1e-9)
    if not a.height:
        raise SystemExit("no team-game was compared - check the raw dirs")
    wk = d.group_by(["season", "week"]).agg(pl.col("d").sum().alias("net"))
    over, under = d.filter(pl.col("d") < 0), d.filter(pl.col("d") > 0)
    print(f"  {a.height:,} team-games; team != sum of player rows on {d.height} "
          f"({over.height} where the players sum is higher, {under.height} where it is lower)")
    print(f"  by season: {dict(sorted(d.group_by('season').len().iter_rows()))}")
    print(f"  season-weeks holding a difference: {wk.height}; netting to exactly zero inside the week: "
          f"{wk.filter(pl.col('net').abs() < 1e-9).height}")
    print(f"  the team row equals the OPPONENT's sacks_suffered on {d.filter(pl.col('t') == pl.col('opp_suffered')).height} "
          f"of the {d.height} (all team-games: {a.filter(pl.col('t') == pl.col('opp_suffered')).height:,} of {a.height:,})")
    print(f"  nameless player rows (player_display_name null): {nameless_rows:,}, ids {dict(sorted(ids.items(), key=lambda x: -x[1]))}; "
          f"of them carrying def_sacks > 0: {nameless_sack_rows}")
    print(f"  over-credited team-games whose excess is exactly the nameless row's sacks: "
          f"{over.filter((pl.col('p_anon') > 0) & ((pl.col('t') - pl.col('p_named')).abs() < 1e-9)).height} of {over.height}")
    print(f"  over-credited team-games with a nameless sack row at all: {over.filter(pl.col('p_anon') > 0).height} of {over.height}")
    named = a.filter((pl.col("t") - pl.col("p_named")).abs() > 1e-9)
    print(f"  dropping the nameless rows instead: team != sum of NAMED player rows on {named.height} team-games "
          f"(the sacks nobody is named for; they are in no named player's row)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--team-raw", required=True)
    ap.add_argument("--player-raw", required=True)
    ap.add_argument("--a64-file")
    ap.add_argument("--first", type=int, default=1999)
    ap.add_argument("--last", type=int, default=2026)
    a = ap.parse_args()
    seasons = list(range(a.first, a.last + 1))
    fumbles(a.team_raw, a.player_raw, a.a64_file, seasons)
    sacks(a.team_raw, a.player_raw, seasons)


if __name__ == "__main__":
    main()
