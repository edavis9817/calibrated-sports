"""Which nflverse columns reproduce the sportsbook "Tackles + Assists" line?

    python -m research.tackle_definition

Three tackle columns exist in stats_player_week and they are not
interchangeable. The spread between plausible combinations is several tackles
per player-game - Bobby Okereke in 2025 week 1 is 7, 16 or 25 depending on
which you pick - so a wrong choice does not look wrong, it just prices every
tackle prop off by a third.

Verified against ESPN official box scores, three games, three players - the
third chosen specifically because `def_tackles_with_assist` is non-zero there.
Two reference cases were NOT enough: both had with_assist = 0, which leaves
"solo + assists" and "solo + assists + with_assist" indistinguishable, and the
first is wrong.
"""
import sqlite3

import config

# (season, week, player, ESPN "TACKLES" total, ESPN "SOLO", box score URL)
REFERENCE = [
    (2025, 1, "Roquan Smith", 10, 8,
     "https://www.espn.com/nfl/boxscore/_/gameId/401772918"),
    (2025, 1, "Bobby Okereke", 16, 7,
     "https://www.espn.com/nfl/boxscore/_/gameId/401772827"),
    # The discriminating case: with_assist = 3, so the two surviving candidates
    # differ by 3 tackles here and only one of them matches.
    (2025, 3, "Tremaine Edmunds", 15, 6,
     "https://www.espn.com/nfl/boxscore/_/gameId/401772844"),
]

# Combined-tackle candidates, i.e. the sportsbook "Tackles + Assists" number.
CANDIDATES = {
    "solo only": lambda r: r["solo"],
    "solo+ast": lambda r: r["solo"] + r["assists"],
    "solo+w_ast": lambda r: r["solo"] + r["with_assist"],
    "solo+w_ast+ast": lambda r: r["solo"] + r["with_assist"] + r["assists"],
}

# The box score's own SOLO column is itself a sum, which is the trap: a player
# credited with a tackle that someone assisted on still shows as solo.
SOLO_CANDIDATES = {
    "solo": lambda r: r["solo"],
    "solo+w_ast": lambda r: r["solo"] + r["with_assist"],
}


def fetch(c, season, week, name):
    row = c.execute(
        "SELECT def_tackles_solo, def_tackles_with_assist, def_tackle_assists "
        "FROM nfl_player_week WHERE season=? AND week=? AND player_name=? "
        "AND season_type='REG'", (season, week, name)).fetchone()
    if row is None:
        raise SystemExit(f"{name} {season} wk{week} not in nfl_player_week")
    return {"solo": row[0] or 0, "with_assist": row[1] or 0, "assists": row[2] or 0}


def main():
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print(f"{'player':<16} {'solo':>5} {'w/ast':>6} {'assists':>8} | "
          + " ".join(f"{k:>28}" for k in CANDIDATES) + " | ESPN")
    verdict = {k: True for k in CANDIDATES}
    for season, week, name, total, solo_ref, _url in REFERENCE:
        r = fetch(c, season, week, name)
        cells = []
        for k, fn in CANDIDATES.items():
            got = fn(r)
            ok = got == total
            verdict[k] &= ok
            cells.append(f"{got:>26.0f} {'OK' if ok else '  '}")
        print(f"{name:<16} {r['solo']:>5.0f} {r['with_assist']:>6.0f} "
              f"{r['assists']:>8.0f} | " + " ".join(cells) + f" | {total}")

    winners = [k for k, v in verdict.items() if v]
    print()
    print("reproduces box-score TACKLES in every reference game:", winners or "NONE")
    if winners != ["solo+w_ast+ast"]:
        raise SystemExit(f"expected exactly ['solo+w_ast+ast'], got {winners}")

    # and separately pin the SOLO column, which is what makes the above work
    solo_ok = {k: all(fn(fetch(c, s_, w, n)) == sr
                      for s_, w, n, _t, sr, _u in REFERENCE)
               for k, fn in SOLO_CANDIDATES.items()}
    solo_win = [k for k, v in solo_ok.items() if v]
    print("reproduces box-score SOLO   in every reference game:", solo_win or "NONE")
    if solo_win != ["solo+w_ast"]:
        raise SystemExit(f"expected exactly ['solo+w_ast'], got {solo_win}")

    print()
    print("=> box-score SOLO   = def_tackles_solo + def_tackles_with_assist")
    print("=> TACKLES+ASSISTS  = def_tackles_solo + def_tackles_with_assist"
          " + def_tackle_assists")
    print("   `def_tackles_with_assist` is a tackle this player made that")
    print("   someone else assisted on - it counts as SOLO on the box score,")
    print("   and dropping it undercounts ~6% of defensive player-games by up")
    print("   to 3 tackles. Two reference cases cannot see this; both had 0.")


if __name__ == "__main__":
    main()
