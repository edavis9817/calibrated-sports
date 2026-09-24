"""The universe table: every bettable claim with its close, its settlement and
its as-of features, one row per (claim, side, book).

    python -m lab.universe --build                 # read the store, write parquet
    python -m lab.universe --build --db PATH       # another store (read-only)
    python -m lab.universe --check                 # the prerequisite counts (6.9)

READ-ONLY. The store is opened `mode=ro` and nothing here writes to it; the
table lands under `<STORAGE_DIR>/lab/`. `lab.run` never touches this module:
it is handed the table.

WHERE EACH PIECE COMES FROM

  props   The Odds API historical close (`quotes.source = 'oddsapi_historical'`),
          per book, at the last snapshot at or before kickoff - the rule
          research/calibration.build uses, so the consensus here is the one the
          register's calibration figures were computed on. 2023-2025.
          Settlement is `outcome_settlement`, i.e. jobs/settle_outcomes and
          core/settlement - READ, never re-derived, so a prop settles here
          exactly as it settles everywhere else.
  games   The Odds API featured close per book (2023-2025), and the nflverse
          games line (1999-2025) as a separate source the engine falls back to
          for seasons the chosen books do not cover. Settled from the final
          score by the pure rule `game_outcome` below.

THE PREREQUISITES (AUDIT 6.9) ARE CHECKED HERE AND CARRIED IN THE META:

  1. the fixed settlement - `settlement_evidence()` counts, per market, settled
     outcomes with NO stat row that week. Those exist only because the
     2026-09-17 fix settles played-with-no-stat-row at 0; on unfixed data the
     count is zero, and `build` REFUSES rather than publish an over rate that
     is inflated by exactly the dropped zeros.
  2. tackles - the history's tackle column is `jobs.settle_outcomes.STAT_COLUMN`,
     imported, the three-column pinned definition.
  3. the nflverse line provider - `line_provider_check()` compares nflverse's
     spread and total with each book's close where both exist.
  4. books - kept per book; the strategy chooses, the default is the benchmark.
  5. model predictions - not in the universe at all (see strategy.check).
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.settlement import OVER, PUSH, UNDER, VOID, snaps_played  # noqa: E402
from lab import catalogue, features  # noqa: E402
from lab.strategy import PROP_MARKETS  # noqa: E402

SOURCE = "oddsapi_historical"
LAST_REG_WEEK = 18            # a-29: postseason props are settled wrongly
HISTORY_FROM = 2020           # L10 across seasons needs games before 2023
GAME_MARKET = {"spreads": "spread", "totals": "total", "h2h": "moneyline"}


def out_dir():
    return config.storage_path("lab")


def connect_ro(path=None):
    path = path or config.DB_PATH
    return sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True)


def american_to_prob(a):
    a = float(a)
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def side_outcome(result, side):
    """Market result -> this side's outcome. `result` is the settlement's, and
    it is market-level: identical on the over and the under row."""
    if result == VOID:
        return "void"
    if result == PUSH:
        return "push"
    if result not in (OVER, UNDER):
        return None
    return "cleared" if result == side else "missed"


def game_outcome(kind, line, team_margin=None, points=None):
    """Cleared / missed / push for one side of a game market.

    spread     the team's point: cleared iff margin + point > 0
    over/under the total line against combined points
    moneyline  the team wins; a tie is a push (books refund, 020)
    """
    if kind == "spread":
        v = team_margin + line
    elif kind == "over":
        v = points - line
    elif kind == "under":
        v = line - points
    elif kind == "moneyline":
        v = team_margin
    else:
        raise ValueError(kind)
    return "cleared" if v > 0 else ("missed" if v < 0 else "push")


# =============================================================================
# loading (read-only)
# =============================================================================

_PROP_CLOSE = """
WITH oq AS (
  SELECT mo.outcome_id AS oid, q.ts AS ts, substr(q.venue, 9) AS book,
         q.prob_devig AS p, q.last AS american
    FROM quotes q
    JOIN market_outcome mo ON mo.venue = q.venue AND mo.market_id = q.market_id
   WHERE q.source = ? AND q.prob_devig IS NOT NULL AND mo.outcome_id IS NOT NULL
),
kick AS (
  SELECT o.outcome_id AS oid, g.kickoff_ts AS k
    FROM outcomes o JOIN nfl_games g ON g.game_id = o.event_id
   WHERE o.entity_type = 'player'
),
last AS (
  SELECT oq.oid AS oid, MAX(oq.ts) AS ts
    FROM oq JOIN kick ON kick.oid = oq.oid
   WHERE oq.ts <= kick.k
   GROUP BY oq.oid
)
SELECT DISTINCT oq.oid, oq.ts, oq.book, oq.p, oq.american, kick.k
  FROM oq
  JOIN last ON last.oid = oq.oid AND last.ts = oq.ts
  JOIN kick ON kick.oid = oq.oid
"""


def load_games(con):
    cols = ("game_id, season, week, game_type, kickoff_ts, home_team, away_team, "
            "home_score, away_score, spread_line, total_line, home_moneyline, "
            "away_moneyline, over_odds, under_odds, home_spread_odds, "
            "away_spread_odds, roof, surface")
    games = {}
    for r in con.execute("SELECT %s, data_version FROM nfl_games ORDER BY data_version"
                         % cols):
        d = dict(zip([c.strip() for c in cols.split(",")], r))
        games[d["game_id"]] = d            # newest data_version wins
    return games


def load_divisions(con):
    """{team: division}. Empty on a store that predates `nfl_teams` (the
    pre-fix baseline does), which leaves `game.division` None - unknown, not
    False."""
    out = {}
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'nfl_teams'").fetchone():
        return out
    for abbr, div in con.execute(
            "SELECT team_abbr, team_division FROM nfl_teams ORDER BY data_version"):
        out[abbr] = div
    return out


def load_history(con, season_from):
    """Player-games for the as-of features, zero-filled from snap counts.

    The stat expressions ARE `jobs.settle_outcomes.STAT_COLUMN` - including the
    pinned three-column tackles sum - so a trailing tackles mean measures the
    same quantity the market settles on.
    """
    from jobs.settle_outcomes import STAT_COLUMN
    import store
    exprs = ", ".join("%s AS m_%s" % (STAT_COLUMN[m], m) for m in PROP_MARKETS)
    rows = {}
    for r in con.execute(
            "SELECT gsis_id, season, week, position, team, opponent, target_share, "
            "%s, data_version FROM nfl_player_week WHERE season_type = 'REG' "
            "AND season >= ? ORDER BY data_version" % exprs, (season_from,)):
        gsis, season, week, pos, team, opp, ts = r[:7]
        vals = dict(zip(PROP_MARKETS, r[7:7 + len(PROP_MARKETS)]))
        rows[(gsis, season, week)] = {"gsis": gsis, "season": season, "week": week,
                                      "position": pos, "team": team, "opponent": opp,
                                      "target_share": ts, "values": vals}
    snaps = {}
    pj = store.pfr_gsis(con)
    for gsis, season, week, team, pos, off, dfn, opct, dpct, _dv in con.execute(
            "SELECT x.gsis_id, s.season, s.week, s.team, s.position, s.offense_snaps, "
            "s.defense_snaps, s.offense_pct, s.defense_pct, s.data_version "
            "FROM nfl_snap_counts s %s WHERE s.season >= ? ORDER BY s.data_version"
            % pj.on(), (season_from,)):
        snaps[(gsis, season, week)] = (team, off or 0, dfn or 0, opct, dpct, pos)
    games_by_team_week = {}
    for g in load_games(con).values():
        if g["game_type"] == "REG":
            games_by_team_week[(g["season"], g["week"], g["home_team"])] = g["away_team"]
            games_by_team_week[(g["season"], g["week"], g["away_team"])] = g["home_team"]
    zero_filled = 0
    for key, sn in snaps.items():
        team, off, dfn, opct, dpct, pos = sn
        row = rows.get(key)
        if row is None:
            vals = {}
            for m in PROP_MARKETS:
                played = snaps_played((team, off, dfn), m)
                vals[m] = 0.0 if played and played > 0 else None
            if all(v is None for v in vals.values()):
                continue
            zero_filled += 1
            row = rows[key] = {"gsis": key[0], "season": key[1], "week": key[2],
                               "position": pos, "team": team,
                               "opponent": games_by_team_week.get((key[1], key[2], team)),
                               "target_share": None, "values": vals}
        row["snap_off"], row["snap_def"] = opct, dpct
    return list(rows.values()), zero_filled


def load_props(con):
    outcomes = {}
    q = ("SELECT outcome_id, season, week, entity_id, stat, line, side, event_id "
         "FROM outcomes WHERE entity_type = 'player' AND stat IN (%s)"
         % ",".join("?" * len(PROP_MARKETS)))
    for r in con.execute(q, PROP_MARKETS):
        outcomes[r[0]] = r
    settled, dup = {}, 0
    for oid, result, actual in con.execute(
            "SELECT outcome_id, result, actual FROM outcome_settlement "
            "ORDER BY data_version"):
        if oid in settled:
            dup += 1
        settled[oid] = (result, actual)     # newest data_version wins
    quotes = con.execute(_PROP_CLOSE, (SOURCE,)).fetchall()
    return outcomes, settled, quotes, dup


def load_game_quotes(con, games):
    """Per-book Odds API featured close for each game, at the game's last
    snapshot at or before kickoff."""
    by_game = {}
    for gid, ts, venue, mtype, side, line, american, p, mid in con.execute(
            "SELECT event_id, ts, venue, market_type, side, line, last, prob_devig, "
            "market_id FROM quotes WHERE source = ? AND market_type IN "
            "('spreads','totals','h2h')", (SOURCE,)):
        g = games.get(gid)
        if g is None or g["kickoff_ts"] is None or ts > g["kickoff_ts"]:
            continue
        by_game.setdefault(gid, []).append((ts, venue[8:], mtype, side, line,
                                            american, p, mid))
    out = {}
    for gid, qs in by_game.items():
        last = max(q[0] for q in qs)
        out[gid] = [q for q in qs if q[0] == last]
    return out


# =============================================================================
# assembly (pure given the loaded pieces)
# =============================================================================

def _hold(rows):
    """book_hold per (claim, book): sum of both sides' implied minus 1."""
    acc = {}
    for r in rows:
        if r["american"] is None:
            continue
        acc.setdefault((r["claim"], r["book"]), []).append(american_to_prob(r["american"]))
    for r in rows:
        v = acc.get((r["claim"], r["book"]))
        r["book_hold"] = (sum(v) - 1.0) if v and len(v) == 2 else None
    return rows


def assemble_props(outcomes, settled, quotes, history, games, divisions):
    pidx = features.PlayerIndex(history)
    oidx = features.OpponentIndex(history)
    gfeat = features.game_team_features(list(games.values()), divisions)
    census = {"quotes": len(quotes), "no_outcome": 0, "postseason": 0,
              "unsettled": 0, "rows": 0}
    cache = {}
    rows = []
    for oid, ts, book, p, american, kick in quotes:
        o = outcomes.get(oid)
        if o is None:
            census["no_outcome"] += 1
            continue
        _oid, season, week, gsis, stat, line, side, gid = o
        if week is None or week > LAST_REG_WEEK:
            census["postseason"] += 1
            continue
        st = settled.get(oid)
        outcome = side_outcome(st[0], side) if st else None
        if outcome is None:
            census["unsettled"] += 1
            continue
        g = games.get(gid) or {}
        teams = (g.get("home_team"), g.get("away_team"))
        fk = (gsis, season, week, stat, line)
        f = cache.get(fk)
        if f is None:
            f = features.player_features(pidx, gsis, season, week, stat, line, teams)
            team = f.pop("player.team")
            opp = None
            if team:
                opp = teams[1] if team == teams[0] else teams[0]
                f.update(gfeat.get((gid, team), {}))
                pos = f.get("player.position")
                if pos:
                    f["opp.allowed_trailing"] = oidx.allowed(season, week, opp, pos, stat)
                    f["opp.allowed_rank_trailing"] = oidx.rank(season, week, opp, pos, stat)
            f["_team"], f["_opp"] = team, opp
            cache[fk] = f
        row = {"bet_type": "prop", "market": stat, "season": season, "week": week,
               "season_type": "REG", "game_id": gid, "kickoff_ts": kick,
               "subject": gsis, "event_subject": gsis,
               "claim": "%s|%s|%s|%s|%g" % (season, week, gsis, stat, line),
               "line": line, "side": side, "team": f["_team"], "opp": f["_opp"],
               "book": book, "american": american, "p_devig": p,
               "source": "oddsapi_close", "outcome": outcome,
               "actual": st[1] if st else None}
        row.update({k: v for k, v in f.items() if not k.startswith("_")})
        rows.append(row)
    census["rows"] = len(rows)
    return _hold(rows), census


def _game_common(g, gfeat, team):
    f = dict(gfeat.get((g["game_id"], team), {}))
    return f


def assemble_games(games, game_quotes, divisions, team_abbr):
    gfeat = features.game_team_features(list(games.values()), divisions)
    rows, census = [], {"games": 0, "nflverse_rows": 0, "odds_rows": 0,
                        "unmatched_team": 0}
    for g in games.values():
        if g["home_score"] is None or g["away_score"] is None or g["kickoff_ts"] is None:
            continue
        census["games"] += 1
        st = "REG" if g["game_type"] == "REG" else "POST"
        margin = {g["home_team"]: g["home_score"] - g["away_score"],
                  g["away_team"]: g["away_score"] - g["home_score"]}
        pts = g["home_score"] + g["away_score"]
        base = {"season": g["season"], "week": g["week"], "season_type": st,
                "game_id": g["game_id"], "kickoff_ts": g["kickoff_ts"],
                "actual": None}

        def team_row(market, team, line, book, american, p, source):
            home = team == g["home_team"]
            opp = g["away_team"] if home else g["home_team"]
            kind = "spread" if market == "spread" else "moneyline"
            r = dict(base, bet_type=market, market=market, subject=team,
                     event_subject=team, claim="%s|%s|%s" % (g["game_id"], market,
                                                             "na" if line is None else "%g" % line),
                     line=line, side="home" if home else "away", team=team, opp=opp,
                     book=book, american=american, p_devig=p, source=source,
                     outcome=game_outcome(kind, line or 0.0, team_margin=margin[team]),
                     actual=margin[team])
            r.update(_game_common(g, gfeat, team))
            return r

        def total_row(side, line, book, american, p, source):
            r = dict(base, bet_type="total", market="total", subject="game",
                     event_subject=g["game_id"], claim="%s|total|%g" % (g["game_id"], line),
                     line=line, side=side, team=None, opp=None, book=book,
                     american=american, p_devig=p, source=source,
                     outcome=game_outcome(side, line, points=pts), actual=pts)
            r.update(features.game_level_features(g, gfeat.get((g["game_id"], g["home_team"]), {})))
            return r

        # nflverse line, one per game. Juice from its odds columns where present.
        sl, tl = g["spread_line"], g["total_line"]
        if sl is not None:
            ho, ao = g["home_spread_odds"], g["away_spread_odds"]
            p_h = _devig_pair(ho, ao)
            rows.append(team_row("spread", g["home_team"], -sl, "nflverse", ho, p_h, "nflverse_line"))
            rows.append(team_row("spread", g["away_team"], sl, "nflverse", ao,
                                 None if p_h is None else 1 - p_h, "nflverse_line"))
            census["nflverse_rows"] += 2
        if tl is not None:
            oo, uo = g["over_odds"], g["under_odds"]
            p_o = _devig_pair(oo, uo)
            rows.append(total_row("over", tl, "nflverse", oo, p_o, "nflverse_line"))
            rows.append(total_row("under", tl, "nflverse", uo,
                                  None if p_o is None else 1 - p_o, "nflverse_line"))
            census["nflverse_rows"] += 2
        hm, am = g["home_moneyline"], g["away_moneyline"]
        if hm is not None and am is not None:
            p_h = _devig_pair(hm, am)
            rows.append(team_row("moneyline", g["home_team"], None, "nflverse", hm, p_h, "nflverse_line"))
            rows.append(team_row("moneyline", g["away_team"], None, "nflverse", am, 1 - p_h, "nflverse_line"))
            census["nflverse_rows"] += 2

        for _ts, book, mtype, side, line, american, p, mid in game_quotes.get(g["game_id"], ()):
            market = GAME_MARKET[mtype]
            if market == "total":
                if side not in ("over", "under") or line is None:
                    continue
                rows.append(total_row(side, line, book, american, p, "oddsapi_close"))
            else:
                parts = (mid or "").split("|")
                team = team_abbr(parts[2]) if len(parts) > 2 else None
                if team not in margin:
                    census["unmatched_team"] += 1
                    continue
                rows.append(team_row(market, team, line if market == "spread" else None,
                                     book, american, p, "oddsapi_close"))
            census["odds_rows"] += 1
    return _hold(rows), census


def _devig_pair(a, b):
    """Multiplicative de-vig of a two-way pair; 0.5 when either side is missing
    (the engine then pays the assumed juice and says so in provenance)."""
    if a is None or b is None:
        return 0.5
    pa, pb = american_to_prob(a), american_to_prob(b)
    return pa / (pa + pb)


# =============================================================================
# prerequisite checks
# =============================================================================

def settlement_evidence(con):
    """Per market: settled outcomes, zero actuals, and settled outcomes whose
    player has NO REG stat row that week. The last count exists only on fixed
    data - before 2026-09-17 those outcomes were left unsettled - so it is the
    discriminating signal, and zero actuals alone are not (a receiver with a
    target and no catch has a row reading 0)."""
    cols = [c[1] for c in con.execute("PRAGMA table_info(outcome_settlement)")]
    out = {}
    for stat, n, zeros, norow in con.execute(
            "SELECT o.stat, COUNT(*), SUM(s.actual = 0), "
            "SUM(NOT EXISTS (SELECT 1 FROM nfl_player_week w WHERE w.gsis_id = o.entity_id "
            "  AND w.season = o.season AND w.week = o.week AND w.season_type = 'REG')) "
            "FROM outcome_settlement s JOIN outcomes o USING (outcome_id) "
            "WHERE o.entity_type = 'player' AND o.side = 'over' AND o.week <= ? "
            "AND s.result IN ('over','under') AND o.stat IN (%s) GROUP BY o.stat"
            % ",".join("?" * len(PROP_MARKETS)), (LAST_REG_WEEK,) + PROP_MARKETS):
        out[stat] = {"settled_over_rows": n, "zero_actual": zeros or 0,
                     "settled_with_no_stat_row": norow or 0}
    return {"by_market": out, "void_reason_column": "void_reason" in cols}


class UnfixedSettlement(RuntimeError):
    pass


def require_fixed(evidence):
    """Refuse a universe built on the pre-fix settlement. Returns the statement."""
    bm = evidence["by_market"]
    rec = bm.get("receptions", {})
    if not rec or rec.get("settled_with_no_stat_row", 0) == 0:
        raise UnfixedSettlement(
            "no receptions outcome is settled without a stat row - this store "
            "predates the 2026-09-17 settlement fix, so every played-with-no-"
            "stat-row zero (a miss on every over) is missing and any over "
            "strategy would read better than it was. Re-run "
            "jobs.settle_outcomes before building.")
    return ("settlement fixed: %s" % ", ".join(
        "%s %d settled with no stat row" % (m, v["settled_with_no_stat_row"])
        for m, v in sorted(bm.items())))


def line_provider_check(games, game_quotes):
    """Where nflverse's line and a book's close both exist, how often do they
    agree? The c-15 question asked of the NFL: is the published number a close,
    and whose? Only 2023-2025 have book closes to compare against, so earlier
    seasons stay unverified - and the engine labels them so."""
    by = {}
    for gid, qs in game_quotes.items():
        g = games.get(gid)
        if not g or g["spread_line"] is None:
            continue
        home = g["home_team"]
        for _ts, book, mtype, side, line, _am, _p, mid in qs:
            if mtype == "spreads":
                parts = (mid or "").split("|")
                if len(parts) < 3 or line is None:
                    continue
                from venues.mapping import team_abbr
                if team_abbr(parts[2]) != home:
                    continue
                d = by.setdefault((g["season"], "spread", book), [0, 0, []])
                d[0] += 1
                d[1] += int(abs(-line - g["spread_line"]) < 1e-9)
                d[2].append(abs(-line - g["spread_line"]))
            elif mtype == "totals" and side == "over" and line is not None \
                    and g["total_line"] is not None:
                d = by.setdefault((g["season"], "total", book), [0, 0, []])
                d[0] += 1
                d[1] += int(abs(line - g["total_line"]) < 1e-9)
                d[2].append(abs(line - g["total_line"]))
    out = {}
    for (season, kind, book), (n, eq, diffs) in sorted(by.items()):
        out.setdefault(str(season), {}).setdefault(kind, {})[book] = {
            "n": n, "exact": round(eq / n, 4), "mean_abs_diff": round(statistics.fmean(diffs), 3)}
    seasons = sorted({g["season"] for g in games.values() if g["spread_line"] is not None})
    return {"compared": out,
            "unverified_seasons": [s for s in seasons if str(s) not in out]}


# =============================================================================
# build
# =============================================================================

def build(db=None, dest=None, analytics_con=None, verbose=True, allow_unfixed=False):
    """Build and write the table. `allow_unfixed` exists for ONE purpose:
    reproducing a figure the register recorded BEFORE the settlement fix, on
    the preserved pre-fix store. Such a universe is stamped
    `settlement_fixed: false` and every result run on it says so and is not
    publishable (lab.engine)."""
    import polars as pl
    from venues.mapping import team_abbr
    t0 = time.time()
    con = connect_ro(db)
    try:
        evidence = settlement_evidence(con)
        try:
            fixed, is_fixed = require_fixed(evidence), True
        except UnfixedSettlement as e:
            if not allow_unfixed:
                raise
            fixed, is_fixed = "UNFIXED SETTLEMENT (reproduction only): %s" % e, False
        if verbose:
            print("  " + fixed, flush=True)
        games = load_games(con)
        divisions = load_divisions(con)
        history, zero_filled = load_history(con, HISTORY_FROM)
        outcomes, settled, quotes, dup = load_props(con)
        if verbose:
            print("  %d prop close quotes, %d history player-games (%d zero-filled)"
                  % (len(quotes), len(history), zero_filled), flush=True)
        gq = load_game_quotes(con, games)
    finally:
        con.close()
    prop_rows, prop_census = assemble_props(outcomes, settled, quotes, history,
                                            games, divisions)
    game_rows, game_census = assemble_games(games, gq, divisions, team_abbr)
    rows = prop_rows + game_rows
    df = pl.DataFrame(rows, infer_schema_length=None)

    coverage = {}
    for bt in ("prop", "spread", "total", "moneyline"):
        s = df.filter(pl.col("bet_type") == bt).get_column("season")
        if len(s):
            coverage[bt] = (int(s.min()), int(s.max()))
    markets = {bt: sorted(set(df.filter(pl.col("bet_type") == bt)
                              .get_column("market").to_list())) for bt in coverage}
    if analytics_con is None:
        try:
            from analytics import paths
            analytics_con = paths.connect(read_only=True)
        except Exception as e:           # no analytics.db on this machine
            analytics_con = None
            if verbose:
                print("  analytics.db unavailable (%s): survey ranges cannot be derived" % e)
    if analytics_con is not None:
        feats = catalogue.ranges(analytics_con, coverage, markets)
    else:
        feats = catalogue.ranges(None, coverage, markets, derive=_no_survey)
    complete = [s for s in sorted({g["season"] for g in games.values()})
                if all(g["home_score"] is not None for g in games.values()
                       if g["season"] == s and g["game_type"] == "REG")]
    meta = {
        "schema": "lab.universe/1",
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_db": str(db or config.DB_PATH),
        "rows": len(df), "price_coverage": coverage, "markets": markets,
        "latest_complete_season": complete[-1] if complete else None,
        "features": feats, "settlement": evidence, "settlement_statement": fixed,
        "settlement_fixed": is_fixed,
        "settlement_duplicate_versions": dup,
        "prop_census": prop_census, "game_census": game_census,
        "history_zero_filled": zero_filled,
        "line_provider": line_provider_check(games, gq),
        "note": "props: Odds API close 2023-2025, REG weeks 1-18; games: Odds API "
                "close 2023-2025 plus nflverse lines 1999-2025 (not a close)",
    }
    dest = dest or out_dir()
    os.makedirs(dest, exist_ok=True)
    df.write_parquet(os.path.join(dest, "universe.parquet"))
    with open(os.path.join(dest, "universe.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, default=str)
    if verbose:
        print("  wrote %d rows to %s in %.0fs" % (len(df), dest, time.time() - t0))
    return {"rows": df, "meta": meta}


def _no_survey(_con, metric):
    raise SystemExit("analytics.db (the column survey) is not available")


def load(dest=None):
    import polars as pl
    dest = dest or out_dir()
    with open(os.path.join(dest, "universe.meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    meta["price_coverage"] = {k: tuple(v) for k, v in meta["price_coverage"].items()}
    return {"rows": pl.read_parquet(os.path.join(dest, "universe.parquet")), "meta": meta}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--db")
    ap.add_argument("--out")
    ap.add_argument("--allow-unfixed", action="store_true",
                    help="reproduction on the pre-fix store only; results are "
                         "stamped unpublishable")
    a = ap.parse_args(argv)
    if a.check:
        con = connect_ro(a.db)
        ev = settlement_evidence(con)
        con.close()
        print(json.dumps(ev, indent=1))
        print(require_fixed(ev))
        return 0
    if a.build:
        u = build(a.db, a.out, allow_unfixed=a.allow_unfixed)
        print(json.dumps({k: u["meta"][k] for k in ("rows", "price_coverage",
                          "prop_census", "game_census", "settlement_statement")},
                         indent=1, default=str))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
