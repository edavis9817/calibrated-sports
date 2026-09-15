"""Brief 023 Part 3 - the inactives reaction, NFL week 1 2026. A PROXY.

    python -m research.inactives

Pre-registered at fd0a57b (docs/briefs/023-preregistration.md, Part 3).

NOTHING ON DISK TIMESTAMPS AN INACTIVE. nflverse injuries (archived verbatim
under raw/nflverse_023) carry report and practice status per week and no time
column at all. So the announcement time is INFERRED from the market: t0 is the
first instant, after the team's kickoff - 120 min, at which the player's own
highest-priced Kalshi rung falls below 0.10, or at which his markets stop
quoting. Because t0 is defined BY the direct line, the direct reaction speed is
not measurable here - only its shape.

Interpretation stated, not hidden: the pre-registration gives no upper bound on
the t0 search. An inactive is announced before kickoff, so the primary search
ends AT kickoff; an in-game collapse is a stat event, not an inactive, and is
printed as context only.
"""
import bisect
import glob
import io
import os
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from research.sweep import common as S  # noqa: E402
from venues.mapping import team_abbr  # noqa: E402

ET = ZoneInfo("America/New_York")
WEEK1 = re.compile(r"-26SEP(09|10|13|14)")
SEARCH_FROM = 120 * 60          # kickoff - 120 min
COLLAPSE = 0.10
MAX_STALE = 660.0
PRE = 300.0                     # t0 - 5 min
HORIZONS = (60, 300, 900, 1800)
LEVEL_TOL = 0.01
DEPTH_WINDOW = 60.0


# =============================================================================
# pure pieces
# =============================================================================

def classify(offense_snaps, has_snap_row, has_player_week):
    """Pre-registered inactive rule, plus the facts that qualify it."""
    zero_snaps = has_snap_row and (offense_snaps or 0) == 0
    inactive = zero_snaps or not has_player_week
    played = has_snap_row and (offense_snaps or 0) > 0
    reasons = []
    if zero_snaps:
        reasons.append("zero offensive snaps")
    if not has_snap_row:
        reasons.append("no snap-count row")
    if not has_player_week:
        reasons.append("no player-week row")
    return {"inactive_by_rule": inactive, "played": played, "reasons": reasons}


def team_from_ticker(market_id, teams):
    """`KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-2` -> 'NYG', given the game's two
    nflverse abbreviations. Kalshi codes differ (JAC, LAR), so each prefix is
    resolved through the alias table and must be one of the game's teams."""
    seg = market_id.split("-")[2]
    for n in (3, 2):
        t = team_abbr(seg[:n])
        if t in teams:
            return t
    return None


def asof(series, t):
    """Last (ts, bid, ask) at or before t, or None."""
    ts = [r[0] for r in series]
    i = bisect.bisect_right(ts, t) - 1
    return series[i] if i >= 0 else None


def two_sided_mid(series, t, max_stale=MAX_STALE):
    q = asof(series, t)
    if not q or t - q[0] > max_stale or q[1] is None or q[2] is None:
        return None
    return (q[1] + q[2]) / 2, q[2] - q[1]


def find_t0(rungs, start, end):
    """rungs: {market_id: [(ts, bid, ask)]} ascending. Returns (t0, how) or
    (None, reason). `how` is 'collapse' or 'stopped quoting'."""
    last = max((s[-1][0] for s in rungs.values() if s), default=None)
    if last is None or last < start - MAX_STALE:
        return None, "never had a live market near kickoff"
    stop_t = last + MAX_STALE
    instants = sorted({r[0] for s in rungs.values() for r in s if start <= r[0] <= end})
    for t in instants:
        mids = [m[0] for m in (two_sided_mid(s, t) for s in rungs.values()) if m]
        if mids and max(mids) < COLLAPSE:
            if stop_t < t and start <= stop_t <= end:
                return stop_t, "stopped quoting"
            return t, "collapse"
    if start <= stop_t <= end:
        return stop_t, "stopped quoting"
    return None, "no collapse and still quoting through the window"


def time_to_level(series, t0, target, horizon, tol=LEVEL_TOL):
    """Seconds after t0 until the as-of two-sided mid first sits within tol of
    target, searched over quote instants up to t0+horizon."""
    for ts, b, a in series:
        if ts < t0 or ts > t0 + horizon or b is None or a is None:
            continue
        if abs((b + a) / 2 - target) <= tol:
            return ts - t0
    return None


# =============================================================================
# loading
# =============================================================================

def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def load_injuries():
    import polars as pl
    files = sorted(glob.glob(os.path.join(config.RAW_DIR, "nflverse_023", "*", "injuries_2026.parquet")))
    if not files:
        return {}, None
    df = pl.read_parquet(files[-1]).filter(pl.col("week") == 1)
    out = {r["gsis_id"]: r for r in df.iter_rows(named=True) if r["gsis_id"]}
    return out, list(df.columns)


def load(con):
    games = {}
    for gid, k, h, a in con.execute(
            "SELECT game_id, MAX(kickoff_ts), home_team, away_team FROM nfl_games "
            "WHERE season=2026 AND week=1 GROUP BY game_id"):
        games[gid] = {"game": gid, "kick": k, "teams": {h, a}}
    by_pair = {frozenset(g["teams"]): g for g in games.values()}
    players = defaultdict(lambda: {"markets": []})
    for mid, gsis, stat in con.execute(
            "SELECT mo.market_id, o.entity_id, o.stat FROM market_outcome mo JOIN outcomes o USING(outcome_id) "
            "WHERE mo.venue='kalshi' AND o.entity_type='player' "
            "AND (mo.market_id LIKE 'KXNFLREC-%' OR mo.market_id LIKE 'KXNFLRSHATT-%')"):
        if not WEEK1.search(mid):
            continue
        players[gsis]["markets"].append((mid, stat))
    # game and team per player come from the tickers, below
    xw ={g: (n, pos, pfr) for g, n, pos, pfr in
          con.execute("SELECT gsis_id, display_name, position, pfr_id FROM player_xwalk")}
    snaps = {pfr: off for pfr, off in con.execute(
        "SELECT pfr_player_id, MAX(offense_snaps) FROM nfl_snap_counts WHERE season=2026 AND week=1 "
        "GROUP BY pfr_player_id")}
    pweek = {g for (g,) in con.execute(
        "SELECT gsis_id FROM nfl_player_week WHERE season=2026 AND week=1 AND season_type='REG'")}
    for gsis, p in players.items():
        name, pos, pfr = xw.get(gsis, ("?", "?", None))
        p.update(gsis=gsis, name=name, pos=pos)
        p.update(classify(snaps.get(pfr), pfr in snaps, gsis in pweek))
        game = team = None
        for mid, _ in p["markets"]:
            suffix = mid.split("-")[1][7:]
            for pair, g in by_pair.items():
                codes = [c for c in pair]
                t = team_from_ticker(mid, pair)
                if t and all(any(team_abbr(suffix[i:i + n]) == c for i in range(len(suffix)) for n in (2, 3))
                             for c in codes):
                    game, team = g, t
                    break
            if game:
                break
        p["game"], p["team"] = game, team
    return games, dict(players)


def series(con, market_id, lo, hi):
    return con.execute(
        "SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' AND market_id=? "
        "AND source='live' AND ts BETWEEN ? AND ? ORDER BY ts", (market_id, lo, hi)).fetchall()


def last_quote(con, market_id):
    return con.execute("SELECT MAX(ts) FROM quotes WHERE venue='kalshi' AND market_id=? AND source='live'",
                       (market_id,)).fetchone()[0]


def depth_size(con, market_id, side, t):
    r = con.execute(
        "SELECT ts, touch_size FROM market_depth WHERE venue='kalshi' AND market_id=? AND side=? "
        "AND ts <= ? ORDER BY ts DESC LIMIT 1", (market_id, side, t)).fetchone()
    return r[1] if r and t - r[0] <= DEPTH_WINDOW else None


# =============================================================================
# measures
# =============================================================================

def second_order(con, game, team, t0, exclude, players, label):
    rows = []
    for gsis, p in players.items():
        if gsis in exclude or p.get("team") != team or p.get("game") is not game:
            continue
        for mid, stat in p["markets"]:
            s = series(con, mid, t0 - PRE - MAX_STALE, t0 + max(HORIZONS) + 60)
            base = two_sided_mid(s, t0 - PRE)
            if not base:
                continue
            r = {"game": game["game"], "arm": label, "market": mid, "player": p["name"], "pos": p["pos"],
                 "stat": stat, "mid0": base[0], "spread0": base[1]}
            end = two_sided_mid(s, t0 + max(HORIZONS))
            for h in HORIZONS:
                m = two_sided_mid(s, t0 + h)
                r[f"move_{h}"] = None if not m else m[0] - base[0]
                r[f"abs_{h}"] = None if not m else abs(m[0] - base[0])
            r["t_to_level"] = None if not end else time_to_level(s, t0, end[0], max(HORIZONS))
            moved = r.get("move_300") or r.get("move_1800") or 0.0
            side = "buy_yes" if moved >= 0 else "buy_no"
            r["touch"] = {h: depth_size(con, mid, side, t0 + h) for h in (0, 60, 300)}
            r["n_quote_rows"] = sum(1 for x in s if t0 - PRE <= x[0] <= t0 + max(HORIZONS))
            rows.append(r)
    return rows


def _hdr(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def fmt(t):
    return datetime.fromtimestamp(t, ET).strftime("%a %m-%d %H:%M:%S") if t else "-"


def main():
    con = ro()
    games, players = load(con)
    inj, inj_cols = load_injuries()

    _hdr("3a - IS THERE AN ANNOUNCEMENT TIMESTAMP?")
    print(f"  nflverse injuries_2026 columns: {inj_cols}")
    has_time = bool(inj_cols) and any(c in inj_cols for c in ("date_modified", "report_time", "timestamp", "last_update"))
    print(f"  any time column: {has_time}. The dataset is one row per player x week with a practice")
    print("  status and a game-status designation (Out / Doubtful / Questionable) - it is the")
    print("  Wed-Fri injury report, not the game-day inactive list, and carries no clock.")
    print("  Nothing else on disk (snap counts, player-week stats, rosters, participation) times")
    print("  an inactive either: all are post-game facts. VERDICT: no usable announcement time.")

    _hdr("PROXY INACTIVE SET (pre-registered rule, week-1 mapped Kalshi REC/RSHATT players)")
    print(f"  players with a mapped week-1 KXNFLREC/KXNFLRSHATT market: {len(players)}")
    print("  (limitation: only MAPPED markets are visible - brief 022 counted 673 unmapped week-1")
    print("   prop markets, so an inactive whose markets are unmapped cannot enter the set)")
    cands = [p for p in players.values() if p["inactive_by_rule"]]
    print(f"  inactive by rule: {len(cands)}")
    events = []
    for p in sorted(cands, key=lambda p: p["name"]):
        g = p["game"]
        rep = inj.get(p["gsis"], {})
        last = max((last_quote(con, m) or 0) for m, _ in p["markets"])
        print(f"   {p['name']:<22} {p['pos']:<3} {p['team'] or '?':<4} reasons={p['reasons']} played={p['played']} "
              f"injury report: {rep.get('report_status') or '-'} / {rep.get('practice_status') or '-'}")
        if not g:
            print("      no game mapped")
            continue
        rungs = {m: series(con, m, g["kick"] - SEARCH_FROM - MAX_STALE, g["kick"]) for m, _ in p["markets"]}
        t0, how = find_t0(rungs, g["kick"] - SEARCH_FROM, g["kick"])
        print(f"      kickoff {fmt(g['kick'])}  last quote {fmt(last)}  t0: {fmt(t0) if t0 else '-'} ({how})")
        if not t0:
            full = {m: series(con, m, g["kick"] - SEARCH_FROM - MAX_STALE, g["kick"] + 4 * 3600) for m, _ in p["markets"]}
            t_in, how_in = find_t0(full, g["kick"], g["kick"] + 4 * 3600)
            if t_in:
                print(f"      context only (not an inactive): in-game {how_in} at {fmt(t_in)}")
        else:
            events.append((p, t0, how, rungs))
    known_out = [p["name"] for p in cands if (inj.get(p["gsis"], {}).get("report_status") == "Out")]
    print(f"  surprise vs known OUT: injury report available locally (nflverse_023); OUT before the game: {known_out or 'none'}")

    _hdr("DIRECT SHAPE (speed NOT measurable: t0 is defined by this line)")
    if not events:
        print("  no proxy event in any team's [kickoff-120, kickoff] window - nothing to shape")
    for p, t0, how, rungs in events:
        for m, s in sorted(rungs.items()):
            vals = [two_sided_mid(s, t0 + d) for d in (-PRE, 0, PRE)]
            print(f"   {m:<42} " + "  ".join(
                f"{lab} {'-' if not v else f'{v[0]:.3f}/{v[1]:.2f}'}" for lab, v in zip(("t0-5m", "t0", "t0+5m"), vals)))

    _hdr("SECOND-ORDER (teammates) vs PLACEBO (same game, players who played)")
    treat, placebo = [], []
    leads = [p["game"]["kick"] - t0 for p, t0, _, _ in events]
    lead = statistics.median(leads) if leads else None
    for p, t0, how, _ in events:
        treat += second_order(con, p["game"], p["team"], t0, {p["gsis"]}, players, "treatment")
        other = next(iter(p["game"]["teams"] - {p["team"]}))
        placebo += second_order(con, p["game"], other, p["game"]["kick"] - lead, set(), players, "placebo")
    print(f"  treatment rungs {len(treat)} over {len({r['game'] for r in treat})} games; "
          f"placebo rungs {len(placebo)} (t0 = kickoff - median proxy lead {lead if lead is None else round(lead/60,1)} min)")
    print("  effective resolution: hot tier polls every 15s but a quote row is written only on a")
    print("  change or a 5-min heartbeat, so an unchanged mid reads as the last write; depth every 60s.")
    tests = []
    for label, rows in (("treatment", treat), ("placebo", placebo)):
        for h in HORIZONS:
            for kind in ("abs", "move"):
                res = S.boot(rows, S.mean_of(f"{kind}_{h}")) if rows else None
                tests.append((label, h, kind, res))
                print(f"   {label:<9} h={h:>4}s mean {'|move|' if kind == 'abs' else 'signed move'}: "
                      + ("not estimable (0 rows)" if not res else
                         f"{100*res['est']:+.2f}pp [{100*res['lo']:+.2f}, {100*res['hi']:+.2f}] games={res['games']}"))
    for r in treat:
        print(f"    {r['player']:<20} {r['market']:<44} mid0 {r['mid0']:.3f} spr {r['spread0']:.2f} "
              + " ".join(f"{h}s {'-' if r[f'move_{h}'] is None else f'{100*r[f'move_{h}']:+.1f}'}" for h in HORIZONS)
              + f" t_to_level {r['t_to_level']} touch {r['touch']}")

    _hdr("BAR (3c): second-order move > 3.6pp, taking > 60s, >= 10 contracts at the touch")
    hits = [r for r in treat if r.get("move_1800") is not None and r["move_1800"] > 0.036
            and (r["t_to_level"] or 0) > 60 and any((v or 0) >= 10 for v in r["touch"].values())]
    print(f"  qualifying teammate rungs: {len(hits)} of {len(treat)}; games under the blocks: "
          f"{len({r['game'] for r in treat})}. " + ("NOTHING CLEARS THE BAR - and with zero proxy events this is "
                                                    "not a test at all." if not hits else "anecdote, not a test."))
    print(f"  tests registered: {len(tests)} (estimable {sum(1 for t in tests if t[3])})")

    _hdr("CONTEXT, NOT PRE-REGISTERED: markets pulled BEFORE the window")
    for p in cands:
        g = p["game"]
        if not g:
            continue
        last = max((last_quote(con, m) or 0) for m, _ in p["markets"])
        if last and last < g["kick"] - SEARCH_FROM:
            tp = last + MAX_STALE
            ctx = second_order(con, g, p["team"], tp, {p["gsis"]}, players, "context")
            print(f"  {p['name']} ({p['team']}): markets stopped quoting {fmt(last)}, "
                  f"{(g['kick'] - last)/3600:.1f}h before kickoff; resolution then was the COLD tier (600s)")
            print(f"    teammate rungs two-sided at pull-5min: {len(ctx)}")
            for r in ctx:
                print(f"    {r['player']:<20} {r['market']:<44} mid0 {r['mid0']:.3f} "
                      + " ".join(f"{h}s {'-' if r[f'move_{h}'] is None else f'{100*r[f'move_{h}']:+.1f}'}"
                                 for h in HORIZONS) + f"  quote rows {r['n_quote_rows']}")


if __name__ == "__main__":
    main()
