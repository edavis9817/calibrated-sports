"""Settle outcomes against nflverse facts.

    python -m jobs.settle_outcomes                  # settle everything settleable
    python -m jobs.settle_outcomes --season 2025 --week 1
    python -m jobs.settle_outcomes --spot-check     # 5 hand-verified 2025 rows

Settlement is a FACT and lands on the facts side of the store (invariant #6).
Nothing here reads or writes a price, a model output or a position; those live
in the beliefs store and are joined to this by outcome_id.

A settlement is versioned by the `data_version` of the player-week it was
computed from, so a stat correction produces a SECOND settlement row rather
than editing the first. That matters more than it looks: settling a prop is the
moment a backtest's P&L becomes real, and a silently restated settlement is a
silently restated track record.
"""
import argparse
import sqlite3
import time

import config
import store
from core.outcomes import Stat

OVER, UNDER, PUSH, UNSETTLED = "over", "under", "push", "unsettled"

# Canonical stat -> the nfl_player_week expression that measures it.
STAT_COLUMN = {
    Stat.RECEPTIONS.value: "receptions",
    Stat.TARGETS.value: "targets",
    Stat.RUSH_ATTEMPTS.value: "carries",
    Stat.RUSH_YARDS.value: "rushing_yards",
    Stat.RECEIVING_YARDS.value: "receiving_yards",
    Stat.PASSING_YARDS.value: "passing_yards",
    Stat.PASS_ATTEMPTS.value: "attempts",
    Stat.COMPLETIONS.value: "completions",
    # "Anytime TD" is any touchdown the player scored, which is two columns.
    Stat.ANYTIME_TD.value: "COALESCE(receiving_tds,0) + COALESCE(rushing_tds,0)",
    # Pinned against ESPN box scores (research/tackle_definition.py): the
    # sportsbook number is all three columns, and `def_tackles_with_assist` -
    # a tackle this player made that someone else assisted on - scores as SOLO
    # officially. Dropping it undercounts ~6% of defensive player-games by up
    # to 3 tackles.
    Stat.TACKLES_ASSISTS.value: ("COALESCE(def_tackles_solo,0) + "
                                 "COALESCE(def_tackles_with_assist,0) + "
                                 "COALESCE(def_tackle_assists,0)"),
    Stat.SACKS.value: "COALESCE(def_sacks,0)",
}


def resolve(actual, line, push_possible) -> str:
    """The pure decision. Kept separate from any I/O so it can be tested and
    read at a glance - this is the function that decides whether a bet won."""
    if actual is None or line is None:
        return UNSETTLED
    if actual > line:
        return OVER
    if actual < line:
        return UNDER
    # Exactly on the line. Only meaningful when the line is an integer on a
    # discrete stat; a half-point line can never land here.
    return PUSH if push_possible else OVER


def actual_for(con, gsis_id, season, week, stat, as_of=None):
    """(value, data_version) for one player-week at its newest version."""
    col = STAT_COLUMN.get(stat)
    if col is None:
        return None, None
    q = f"""SELECT {col}, data_version FROM nfl_player_week
             WHERE gsis_id=? AND season=? AND week=? AND season_type='REG'
               {'AND data_version <= ?' if as_of else ''}
             ORDER BY data_version DESC LIMIT 1"""
    args = [gsis_id, season, week] + ([as_of] if as_of else [])
    row = con.execute(q, args).fetchone()
    return (row[0], row[1]) if row else (None, None)


def settle_one(con, outcome_row, as_of=None):
    """outcome row -> (result, actual, data_version). Never writes."""
    (_oid, _key, _sport, season, week, entity_type, entity_id, stat, line,
     _side, push_possible) = outcome_row
    if entity_type != "player":
        return UNSETTLED, None, None       # team/game settlement is brief 004
    if week is None:
        return UNSETTLED, None, None       # season-long claims settle in Feb
    actual, version = actual_for(con, entity_id, season, week, stat, as_of)
    if actual is None:
        return UNSETTLED, None, version
    return resolve(float(actual), line, bool(push_possible)), float(actual), version


def run(season=None, week=None, as_of=None, limit=None) -> dict:
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    q = ("SELECT outcome_id, key, sport, season, week, entity_type, entity_id, "
         "stat, line, side, push_possible FROM outcomes WHERE entity_type='player'")
    args = []
    if season:
        q += " AND season=?"
        args.append(season)
    if week:
        q += " AND week=?"
        args.append(week)
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = con.execute(q, args).fetchall()

    counts = {OVER: 0, UNDER: 0, PUSH: 0, UNSETTLED: 0}
    batch = []
    for r in rows:
        result, actual, version = settle_one(con, r, as_of)
        counts[result] += 1
        if result != UNSETTLED:
            batch.append((r[0], result, actual, version, "nflverse"))
        if len(batch) >= 5000:
            store.record_settlements(batch)
            batch = []
    store.record_settlements(batch)
    con.close()
    settled = sum(v for k, v in counts.items() if k != UNSETTLED)
    store.record_health("settlement", True,
                        f"{settled} settled of {len(rows)} player outcomes "
                        f"({counts[OVER]} over, {counts[UNDER]} under, "
                        f"{counts[PUSH]} push)", watermark=time.time())
    return counts


# --- the hand-verified spot check -------------------------------------------
# Five 2025 player-weeks whose box scores are public. Each line is chosen to
# exercise a different branch: comfortably over, comfortably under, and the two
# that sit exactly on an integer line, which is where push handling either works
# or silently costs you the entire probability mass at the line.
SPOT_CHECK = [
    # (player, season, week, stat, line, expected result, verified actual)
    # Actuals verified by hand against ESPN box scores / game logs, not taken
    # from the same table the settler reads.
    ("Amon-Ra St. Brown", 2025, 1, "receptions", 5.5, UNDER, 4.0),
    ("Christian McCaffrey", 2025, 1, "rush_attempts", 30.5, UNDER, 22.0),
    ("Puka Nacua", 2025, 1, "receiving_yards", 99.5, OVER, 130.0),
    # Integer lines. These are the rows that matter: a half-point line can never
    # push, an integer line on a discrete stat can, and scoring a push as a win
    # is how a backtest quietly inflates its own hit rate.
    ("Bijan Robinson", 2025, 2, "rush_attempts", 18.0, OVER, 22.0),
    ("Khalil Shakir", 2025, 1, "receptions", 6.0, PUSH, 6.0),
]


def spot_check():
    from venues.mapping import resolve_player
    from core.outcomes import Side, Stat as S, player_prop, is_push_possible

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print(f"{'player':<22} {'wk':>3} {'stat':<16} {'line':>6} {'actual':>7} "
          f"{'push?':>6} {'result':>9}")
    bad = 0
    for name, season, week, stat, line, expected, verified in SPOT_CHECK:
        gsis, _how, _c = resolve_player(name, season)
        o = player_prop(season, week, gsis, S(stat), line, Side.OVER)
        store.upsert_outcome(o)
        row = (o.outcome_id, o.key, "nfl", season, week, "player", gsis, stat,
               line, "over", int(is_push_possible(line, S(stat))))
        result, actual, version = settle_one(con, row)
        flag = ""
        if expected and result != expected:
            flag, bad = f"  <-- expected {expected}", bad + 1
        if verified is not None and actual is not None and float(actual) != verified:
            flag, bad = f"  <-- box score says {verified:g}", bad + 1
        print(f"{name:<22} {week:>3} {stat:<16} {line:>6g} "
              f"{actual if actual is not None else '-':>7} "
              f"{str(bool(row[10])):>6} {result:>9}{flag}")
        if result != UNSETTLED:
            store.record_settlement(o.outcome_id, result, actual, version, "nflverse")
    con.close()
    print()
    print("Verify by hand against the box scores. The two integer lines are the"
          " ones that matter:")
    print("a push must NOT be scored as a win, and a half-point line must never"
          " push.")
    return bad


def reasons(season=None, week=None):
    """Why each unsettled outcome is unsettled.

    A settlement job that reports "0 settled" and stops is indistinguishable
    from a broken one. The categories below separate "the game has not been
    played" from "the player did not record a stat" from "we cannot settle this
    kind of claim yet", and only the last is work.
    """
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    now = time.time()
    q = """
        SELECT o.outcome_id, o.entity_type, o.week, o.stat, o.entity_id,
               o.season, g.kickoff_ts, g.home_score,
               (SELECT 1 FROM nfl_player_week pw
                 WHERE pw.gsis_id = o.entity_id AND pw.season = o.season
                   AND pw.week = o.week AND pw.season_type = 'REG' LIMIT 1),
               s.result
          FROM outcomes o
          JOIN market_outcome mo USING (outcome_id)
          LEFT JOIN (SELECT game_id, MAX(kickoff_ts) kickoff_ts,
                            MAX(home_score) home_score
                       FROM nfl_games GROUP BY game_id) g
            ON g.game_id = o.event_id
          LEFT JOIN outcome_settlement s ON s.outcome_id = o.outcome_id
         WHERE mo.outcome_id IS NOT NULL
    """
    args = []
    if season:
        q += " AND o.season = ?"
        args.append(season)
    if week:
        q += " AND o.week = ?"
        args.append(week)
    q += " GROUP BY o.outcome_id"

    counts = {}
    for (_oid, etype, wk, stat, _eid, _season, kickoff, score, has_pw,
         result) in con.execute(q, args):
        if result in (OVER, UNDER, PUSH):
            key = f"settled: {result}"
        elif etype != "player":
            key = f"unsettled: {etype} outcome (team/game settlement is later work)"
        elif wk is None:
            key = "unsettled: season-long claim, settles after the postseason"
        elif stat not in STAT_COLUMN:
            key = f"unsettled: no fact column for stat {stat!r}"
        elif kickoff is None:
            key = "unsettled: outcome has no game attached"
        elif kickoff > now:
            key = "unsettled: game has not kicked off"
        elif score is None:
            key = "unsettled: game in progress or final not published"
        elif not has_pw:
            key = "unsettled: no player-week row (inactive, or stats not posted)"
        else:
            key = "unsettled: player-week exists but stat is null"
        counts[key] = counts.get(key, 0) + 1
    con.close()

    total = sum(counts.values()) or 1
    print(f"{'outcome':<62} {'n':>7} {'share':>7}")
    for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{k:<62} {v:>7,} {v/total:>7.1%}")
    print(f"{'TOTAL':<62} {total:>7,}")
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--as-of", dest="as_of")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--spot-check", action="store_true")
    ap.add_argument("--reasons", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.spot_check:
        raise SystemExit(1 if spot_check() else 0)
    if args.reasons:
        reasons(args.season, args.week)
        return
    c = run(args.season, args.week, args.as_of, args.limit)
    print(f"over={c[OVER]} under={c[UNDER]} push={c[PUSH]} "
          f"unsettled={c[UNSETTLED]}")


if __name__ == "__main__":
    main()
