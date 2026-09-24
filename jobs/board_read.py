"""The Board read job (audit 5.5, units a-26 and a-31). One read -> one static JSON file.

    python -m jobs.board_read --season 2026 --week 3 --dest D:/scratch/board
    python -m jobs.board_read --season 2026 --week 2 --dest ... --at 2026-09-20T15:00:00Z
    python -m jobs.board_read --season 2026 --week 3 --dest ... --due     # cadence only
    python -m jobs.board_read --tick [--upload]      # the scheduled entry point (a-31)
    python -m jobs.board_read --check --keys --dest ...   # validate a tree, list its keys
    python -m jobs.board_read --restore              # pull board/ back from the bucket

Writes, under `--dest` (or BOARD_EXPORT_DIR for --tick; there is NO default -
config has no defaults and a job that can publish must be told where):

    board/nfl/{season}/wk{week}/read-{iso}.json    kind board_read
    board/nfl/{season}/wk{week}/index.json         kind board_index
    board/nfl/ledger.parquet  + ledger.csv          contract table board_ledger, append-only

THE CONTRACT (a-31). Both JSON kinds are in web/contract/v2/contract.schema.json
and go through `export_web.sync_keys` with NO owned prefix: validated against the
contract, gated on their source declarations (the registry scans this module as
a producer - jobs.source_registry.SIDE_PRODUCERS), and never deleted by absence.
The ledger's columns and types are asserted against `x-contract.tables` before
every write. Every estimate a row carries has an interval and an integer sample,
or the row carries null in its place - never a record with n = 0.

READ-ONLY ON THE STORE. Every query opens `market_log.db` with `mode=ro`; the
Board's own state is the files it wrote last time, so a read needs no table and
can never write a fact. `venues.mapping.resolve_player` is NOT used for that
reason - it opens `store.db()` read-write - and its alias-then-team rule is
repeated here over a read-only connection.

THE CADENCE (a-31). `--tick` is what a scheduled task runs every
config.BOARD_TICK_MIN minutes, from the production clone. It finds the week or
weeks in play, asks `core.board.read_due` / `grade_due`, reads only when one says
so, and with `--upload` ships the Board's own tree (export_web.upload(tree="board")).
Registering the task is Ethan's - see docs/runbooks/board-cadence.md.

What each row is built from, per audit 5.3:
  books        Odds API forward capture (source 'live'), benchmark books only,
               each book's LATEST snapshot at or before the read. A book that
               stopped listing the player at its latest snapshot is not counted.
  main line    core.board.main_line at this read.
  model        models.baseline fit as of the read, BOARD_MODEL_STATS only.
  band         research/results/board_bands.json (research/board_bands.py).
  last10/rates nfl_player_week before this game's kickoff, against TODAY'S line.
  streak       core.board.streak, next-game rate from F11 (research/results/
               f11_next_rates.json).
  kalshi_mid   the mapped Kalshi over market's latest mid, never de-vigged.
  news         [] - no news source is ingested. Not faked.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sqlite3
import statistics
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from core import board as B
from core import outcomes as O
from jobs import export_web as E
from jobs import source_registry as R
from venues.mapping import norm_name, team_abbr

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANDS_PATH = os.path.join(REPO, "research", "results", "board_bands.json")
F11_PATH = os.path.join(REPO, "research", "results", "f11_next_rates.json")
SLUGS_PATH = os.path.join(REPO, "web", "slugs", "nfl.json")
F11_STAT = {"receptions": "rec", "rush_attempts": "rush_att", "receiving_yards": "rec_yds"}
STAT_COLUMN = {"receptions": "receptions", "rush_attempts": "carries",
               "receiving_yards": "receiving_yards"}
ET = ZoneInfo("America/New_York")


def ro(path=None):
    return sqlite3.connect(f"file:{path or config.DB_PATH}?mode=ro", uri=True)


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


# ------------------------------------------------------------------ schedule

def week_games(con, season, week):
    return {g: {"game_id": g, "kickoff_ts": k, "home": h, "away": a, "gameday": d}
            for g, k, h, a, d in con.execute(
                "SELECT game_id, MAX(kickoff_ts), home_team, away_team, gameday FROM nfl_games "
                "WHERE season=? AND week=? GROUP BY game_id", (season, week))}


def window_open_ts(games):
    """Tuesday 12:00 ET before the week's first kickoff."""
    first = min(g["kickoff_ts"] for g in games.values())
    d = datetime.fromtimestamp(first, ET)
    tue = (d - timedelta(days=(d.weekday() - 1) % 7)).replace(hour=12, minute=0, second=0,
                                                               microsecond=0)
    return tue.timestamp()


def oddsapi_events(con, games):
    """Odds API event id -> nflverse game, matched on the exact team pair and
    the kickoff date +/- 1 day (the same rule venues.mapping.game_for uses)."""
    by_pair = {frozenset((g["home"], g["away"])): g for g in games.values()}
    out = {}
    lo = min(g["kickoff_ts"] for g in games.values()) - 3 * 86400
    hi = max(g["kickoff_ts"] for g in games.values()) + 3 * 86400
    for eid, title, close_ts in con.execute(
            "SELECT event_id, title, close_ts FROM markets WHERE venue='oddsapi' "
            "AND market_type='game' AND close_ts BETWEEN ? AND ?", (lo, hi)):
        if not title or " @ " not in title:
            continue
        away, home = title.split(" @ ", 1)
        pair = frozenset((team_abbr(away), team_abbr(home)))
        g = by_pair.get(pair)
        if g and abs((close_ts or 0) - g["kickoff_ts"]) <= 86400 * 1.5:
            out[eid] = g
    return out


# ------------------------------------------------------------------ quotes

def load_quotes(con, event_ids, read_ts, books):
    """{(eid, mkey, player): {book: {ts: {line: {over, under, read_at}}}}},
    plus {(eid, book): [snapshot ts...]} - every Odds API prop row at or before
    the read."""
    claims = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    snaps = defaultdict(set)
    for book in books:
        venue = f"oddsapi:{book}"
        for eid in event_ids:
            for mid, line, side, price, ts, sts in con.execute(
                    "SELECT market_id, line, side, last, ts, source_ts FROM quotes "
                    "WHERE venue=? AND event_id=? AND ts<=? AND market_type='prop' "
                    "AND source='live'", (venue, eid, read_ts)):
                parts = (mid or "").split("|")
                if len(parts) != 4 or parts[1] not in config.BOARD_MARKETS or line is None:
                    continue
                snaps[(eid, book)].add(ts)
                sd = (side or "").lower()
                if sd not in ("over", "under"):
                    continue
                cell = claims[(eid, parts[1], parts[2])][book][ts].setdefault(float(line), {})
                cell[sd] = price
                cell["read_at"] = B.iso(sts or ts)
    return claims, snaps


def ladder_at(claim_books, snaps, eid, upto_ts=None):
    """The ladder at a read: each book's quotes at ITS latest snapshot. A book
    whose latest event snapshot does not list this claim contributes nothing -
    it has pulled the player."""
    ladder = defaultdict(dict)
    for book, by_ts in claim_books.items():
        ev_ts = [t for t in snaps.get((eid, book), ()) if upto_ts is None or t <= upto_ts]
        if not ev_ts:
            continue
        latest = max(ev_ts)
        if latest not in by_ts:
            continue
        for line, q in by_ts[latest].items():
            ladder[line][book] = q
    return dict(ladder)


def line_path(claim_books, snaps, eid, line, read_ts):
    ts_all = sorted({t for b in claim_books for t in snaps.get((eid, b), ()) if t <= read_ts})
    out = []
    for t in ts_all:
        lad = ladder_at(claim_books, snaps, eid, upto_ts=t)
        p, n = B.market_prob(lad, line)
        if p is not None and (not out or out[-1]["p_over"] != round(p, 4)):
            out.append({"t": B.iso(t), "p_over": round(p, 4), "books": n})
    return out


# ------------------------------------------------------------------ identity

def resolve_player(con, name, season, teams):
    """Read-only alias resolution: unique alias, else narrowed to the two teams
    in the game, else active last season. Returns (gsis, position, team) or
    None - an ambiguous name is dropped and COUNTED, never guessed."""
    alias = norm_name(name)
    hits = con.execute(
        "SELECT DISTINCT a.gsis_id, x.position, x.last_team, x.last_season FROM player_alias a "
        "LEFT JOIN player_xwalk x USING (gsis_id) WHERE a.alias=?", (alias,)).fetchall()
    if len({h[0] for h in hits}) > 1:
        ids = sorted({h[0] for h in hits})
        on = {r[0] for r in con.execute(
            f"SELECT DISTINCT gsis_id FROM nfl_player_week WHERE gsis_id IN "
            f"({','.join('?' * len(ids))}) AND season BETWEEN ? AND ? AND team IN (?,?)",
            (*ids, season - 1, season, *teams))}
        hits = [h for h in hits if h[0] in on] or [h for h in hits if (h[3] or 0) >= season - 1]
    if len({h[0] for h in hits}) != 1:
        return None
    return hits[0][0], hits[0][1], hits[0][2]


def current_team(con, gsis, season, teams, fallback):
    r = con.execute("SELECT team FROM nfl_player_week WHERE gsis_id=? AND season=? "
                    "ORDER BY week DESC LIMIT 1", (gsis, season)).fetchone()
    t = r[0] if r else fallback
    return t if t in teams else fallback


# ------------------------------------------------------------------ history

def history(con, gsis, stat, before_ts):
    """The player's REG games before a kickoff, oldest first:
    [{game_id, value, away, opp}]."""
    col = STAT_COLUMN[stat]
    rows = con.execute(
        f"""SELECT w.season, w.week, w.team, w.{col}, g.game_id, g.home_team, g.away_team, g.k
              FROM nfl_player_week w
              JOIN (SELECT game_id, season, week, home_team, away_team, MAX(kickoff_ts) k
                      FROM nfl_games GROUP BY game_id) g
                ON g.season = w.season AND g.week = w.week
               AND (g.home_team = w.team OR g.away_team = w.team)
             WHERE w.gsis_id = ? AND w.season_type = 'REG' AND g.k < ?
               AND w.data_version = (SELECT MAX(v.data_version) FROM nfl_player_week v
                     WHERE v.gsis_id = w.gsis_id AND v.season = w.season AND v.week = w.week
                       AND v.season_type = 'REG')
             ORDER BY g.k""", (gsis, before_ts)).fetchall()
    out = []
    for season, week, team, val, gid, home, away, _k in rows:
        out.append({"game_id": gid, "season": season, "value": val,
                    "away": team == away, "opp": home if team == away else away})
    return out


def rates(hist, line, season):
    def kn(games):
        g = [x for x in games if x["value"] is not None and x["value"] != line]
        return {"k": sum(1 for x in g if x["value"] > line), "n": len(g)}
    this = [h for h in hist if h["season"] == season]
    from core.stats import wilson
    out = {}
    for name, games in (("last10", hist[-10:]), ("season", this), ("career", hist)):
        r = kn(games)
        # No graded game is no record: null, never {k: 0, n: 0} - the contract's
        # BoardRate requires n >= 1 and an interval (a-31, the AnalyticValue rule).
        if not r["n"]:
            out[name] = None
            continue
        lo, hi = wilson(r["k"], r["n"])
        out[name] = dict(r, ci=[round(lo, 4), round(hi, 4)])
    # "career" above is against TODAY's line and says so by its key;
    # "career_posted" (below, `posted_record`) is against each game's OWN line.
    return out


def posted_record(con, gsis, stat, before_ts):
    """The player's record against the line that was POSTED for each past game:
    per game, the over outcome whose closing market P(over) is nearest 0.5 (the
    Board's main-line rule, applied at the close), settled by the stored
    settlement. Sportsbook closes exist for 2023-2025 (outcome_close); the Board's
    own frozen rows extend this forward once there are settled weeks.

    Market at the close is p_bench (DK/FD/MGM) where any rung of that game has
    it, else p_all - counted in `basis` so a reader can see which."""
    rows = con.execute(
        """SELECT o.event_id, o.line, oc.p_bench, oc.p_all, oc.kickoff_ts,
                  (SELECT s.result FROM outcome_settlement s WHERE s.outcome_id = o.outcome_id
                    ORDER BY s.data_version DESC LIMIT 1)
             FROM outcomes o JOIN outcome_close oc USING (outcome_id)
            WHERE o.entity_id = ? AND o.stat = ? AND o.side = 'over'
              AND oc.kickoff_ts < ?""", (gsis, stat, before_ts)).fetchall()
    games = defaultdict(list)
    for gid, line, pb, pa, _k, res in rows:
        games[gid].append((line, pb, pa, res))
    k = n = 0
    basis = {"p_bench": 0, "p_all": 0}         # both keys always: a closed shape
    for gid, rungs in games.items():
        use = "p_bench" if any(r[1] is not None for r in rungs) else "p_all"
        cands = [r for r in rungs if (r[1] if use == "p_bench" else r[2]) is not None]
        if not cands:
            continue
        line, pb, pa, res = min(cands, key=lambda r: (abs((r[1] if use == "p_bench" else r[2]) - 0.5), r[0]))
        if res not in ("over", "under"):
            continue                       # push, void or unsettled: not graded
        n += 1
        k += res == "over"
        basis[use] += 1
    if not n:
        return None
    from core.stats import wilson
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "ci": [round(lo, 4), round(hi, 4)], "basis": dict(basis),
            "seasons": "2023-2025 sportsbook closes"}


# ------------------------------------------------------------------ model, band, streak

@contextmanager
def feature_cache():
    """Memoise the feature queries for ONE read, as research/walkforward does per
    worker. Every fit in a read shares one as_of, so the positional and role
    priors repeat; without this a Sunday slate is ~300 fits at ~3s each. The
    originals are restored on exit, so nothing outlives the read."""
    from models import features
    names = ("player_prior", "role_rank", "positional_prior", "team_context")
    saved = {n: getattr(features, n) for n in names}
    try:
        for n, fn in saved.items():
            setattr(features, n, functools.lru_cache(maxsize=100_000)(fn))
        yield
    finally:
        for n, fn in saved.items():
            setattr(features, n, fn)


def model_prob(con, gsis, stat, season, read_ts, pos, team, line, cache):
    if stat not in config.BOARD_MODEL_STATS:
        return None
    from models import baseline
    key = (gsis, stat)
    if key not in cache:
        try:
            fit = baseline.fit_player_stat(con, gsis, stat, season, read_ts, pos, team,
                                           prior_seasons=(season - 1, season))
            fit.provenance.assert_as_of(read_ts)
            cache[key] = fit
        except Exception as e:  # noqa: BLE001 - counted by the caller
            cache[key] = e
    fit = cache[key]
    if isinstance(fit, Exception):
        return None
    return fit.dist.prob_over(float(line), push=O.is_push_possible(line, O.Stat(stat)))


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def band_payload(bands, market, gap, counts=None):
    """The walk-forward record for leans this size, or None when there is none.

    a-26 returned an object with n = 0 and null rates in that case. The contract
    (a-31) forbids it: an estimate carries an interval and a sample or it is not
    published, so a lean with no walk-forward record carries `band: null` and is
    COUNTED here, rather than rendering as a band with nothing in it."""
    b = B.band_for(gap)
    if b is None:
        return None
    lo, hi = b
    key = f"{market}|{lo:g}-{hi:g}" if hi is not None else f"{market}|{lo:g}+"
    s = (bands or {}).get("bands", {}).get(key)
    if not s or not s.get("n") or s.get("ci") is None or s.get("roi_ci") is None:
        if counts is not None:
            counts["lean with no walk-forward band"] += 1
        return None
    return {"lo_pp": lo, "hi_pp": hi, "n": s["n"], "games": s["games"], "cleared": s["cleared"],
            "ci": s["ci"], "ci_method": s["ci_method"], "roi": s["roi"], "roi_ci": s["roi_ci"],
            "roi_ci_method": s["roi_ci_method"], "source": "research/board_bands.py"}


def streak_payload(f11, stat, hist, line, away, opp, counts=None):
    """A fired streak with F11's measured next-game rate, or None.

    A streak whose next-game rate is not on file is NOT shown (a-31): "5 of 5"
    without the rate that corrects it is the hot-hand display F11 measured as
    overstating the next game by 32-56 points. Counted, never silent."""
    s = B.streak(hist, line, away, opp)
    if s is None:
        return None
    nxt = ((f11 or {}).get("next_rates") or {}).get(s["rule"])
    if not nxt or not nxt.get("n"):
        if counts is not None:
            counts["streak with no F11 next-game rate"] += 1
        return None
    return {"rule": s["rule"], "k": s["k"], "n": s["n"], "display_rate": s["display_rate"],
            "next_rate": nxt["next_rate"], "next_ci": [nxt["lo"], nxt["hi"]],
            "next_n": nxt["n"],
            "source": "F11", "line_basis": "posted line (F11 measured a constructed median line)"}


def kalshi_mid(con, season, week, gsis, stat, line, read_ts):
    """Kalshi's over market on the SAME claim, latest mid at or before the read."""
    r = con.execute(
        """SELECT mo.market_id FROM outcomes o JOIN market_outcome mo USING (outcome_id)
            WHERE o.entity_id=? AND o.season=? AND o.week=? AND o.stat=? AND o.line=?
              AND o.side IN ('over','yes') AND mo.venue='kalshi' LIMIT 1""",
        (gsis, season, week, stat, float(line))).fetchone()
    if not r:
        return None
    q = con.execute("SELECT mid FROM quotes WHERE venue='kalshi' AND market_id=? AND ts<=? "
                    "ORDER BY ts DESC LIMIT 1", (r[0], read_ts)).fetchone()
    return None if not q or q[0] is None else round(q[0], 4)


# ------------------------------------------------------------------ settlement

def settle(con, season, week, game_id, gsis, stat, line):
    """(result, actual, void_reason) through the ONE settlement rule - the
    zero-row fix included (played with no stat row settles at 0)."""
    from jobs.settle_outcomes import settle_one
    from core import settlement
    # Settle only once THIS game has data. settle_one's coverage test is per
    # WEEK, so on a Sunday with Thursday's snaps already in, a Sunday player
    # with no row yet reads as "no snap row, plays elsewhere -> void inactive"
    # - a void written over data that has not arrived.
    game = con.execute("SELECT 1 FROM nfl_snap_counts WHERE season=? AND week=? AND game_id=? "
                       "LIMIT 1", (season, week, game_id)).fetchone()
    if game is None:
        return settlement.UNSETTLED, None, None
    push = O.is_push_possible(line, O.Stat(stat))
    res, actual, _v, reason = settle_one(
        con, (None, None, "nfl", season, week, "player", gsis, stat, float(line), "over", push))
    return res, actual, reason


# ------------------------------------------------------------------ one read

def fresh_rows(con, season, week, read_ts, games, counts):
    events = oddsapi_events(con, games)
    books = config.BOARD_BENCH_BOOKS
    claims, snaps = load_quotes(con, list(events), read_ts, books)
    bands, f11 = load_json(BANDS_PATH), load_json(F11_PATH)
    slugs = load_json(SLUGS_PATH) or {}
    cache, rows = {}, []
    for (eid, mkey, name), claim_books in sorted(claims.items()):
        g = events[eid]
        if g["kickoff_ts"] <= read_ts:
            continue
        market = config.BOARD_MARKETS[mkey]
        ladder = ladder_at(claim_books, snaps, eid)
        line = B.main_line(ladder)
        if line is None:
            counts["no two-way benchmark quote"] += 1
            continue
        who = resolve_player(con, name, season, (g["home"], g["away"]))
        if who is None:
            counts["player unresolved or ambiguous"] += 1
            continue
        gsis, pos, last_team = who
        team = current_team(con, gsis, season, (g["home"], g["away"]), last_team)
        if team not in (g["home"], g["away"]):
            counts["player not on either team"] += 1
            continue
        opp = g["away"] if team == g["home"] else g["home"]
        p_mkt, n_books = B.market_prob(ladder, line)
        p_model = model_prob(con, gsis, market, season, read_ts, pos, team, line, cache)
        if market in config.BOARD_MODEL_STATS and p_model is None:
            counts["model fit failed"] += 1
        gap = B.gap_pp(p_model, p_mkt)
        T = B.lean_threshold_at(B.iso(read_ts))
        side = B.lean(gap, T)
        hist = history(con, gsis, market, g["kickoff_ts"])
        book_rows = []
        lean_prices = []
        for book in books:
            q = ladder.get(line, {}).get(book)
            if not q:
                continue
            book_rows.append({"book": book, "over": q.get("over"), "under": q.get("under"),
                              "read_at": q.get("read_at"),
                              "p_over_devig": (round(B.devig_mult(q.get("over"), q.get("under")), 4)
                                               if B.devig_mult(q.get("over"), q.get("under")) else None),
                              "hold": (round(B.hold(q.get("over"), q.get("under")), 4)
                                       if B.hold(q.get("over"), q.get("under")) is not None else None)})
            if side and q.get(side) is not None:
                lean_prices.append(q[side])
        rows.append({
            "row_id": B.row_id(season, week, g["away"], g["home"], gsis, market, line),
            "claim_id": B.claim_id(g["game_id"], gsis, market),
            "season": season, "week": week,
            "gsis_id": gsis, "slug": slugs.get(gsis), "name": name, "pos": pos, "team": team, "opp": opp,
            "game_id": g["game_id"], "kickoff": B.iso(g["kickoff_ts"]), "kickoff_ts": g["kickoff_ts"],
            "market": market, "line": line, "is_main": True,
            "ladder": sorted(ladder),
            "mkt_p_over": round(p_mkt, 4), "mkt_books": n_books, "mkt_method": "median_devig_mult",
            "longshot": B.is_longshot(p_mkt),
            "books": book_rows,
            "kalshi_mid": kalshi_mid(con, season, week, gsis, market, line, read_ts),
            "model_p_over": None if p_model is None else round(p_model, 4),
            "gap_pp": gap, "lean": side,
            "lean_price": statistics.median(lean_prices) if lean_prices else None,
            "band": band_payload(bands, market, gap, counts) if side else None,
            "last10": [{"game_id": h["game_id"], "value": h["value"],
                        "cleared": None if h["value"] is None or h["value"] == line
                        else h["value"] > line} for h in hist[-10:]],
            "rates": dict(rates(hist, line, season),
                          career_posted=posted_record(con, gsis, market, g["kickoff_ts"])),
            "streak": streak_payload(f11, market, hist, line, team == g["away"], opp, counts),
            "line_path": line_path(claim_books, snaps, eid, line, read_ts),
            "news": [],
            "status": B.UPCOMING, "result": None, "lean_result": None, "void_reason": None,
            "pulled_at": None,
        })
        counts["rows"] += 1
    return rows


def week_dir(dest, season, week):
    return os.path.join(dest, "board", "nfl", str(season), f"wk{int(week):02d}")


def ledger_path(dest):
    return os.path.join(dest, "board", "nfl", "ledger.parquet")


def read_ledger(dest):
    p = ledger_path(dest)
    if not os.path.exists(p):
        return []
    import polars as pl
    return pl.read_parquet(p).to_dicts()


LEDGER_KIND = "board_ledger"
_POLARS_TYPES = {"string": "Utf8", "int64": "Int64", "float64": "Float64"}


def ledger_schema():
    """The ledger's polars schema, READ OFF THE CONTRACT (x-contract.tables) and
    checked against core.board's column order - so the job, the contract and the
    reader cannot drift. Raises if they disagree."""
    import polars as pl
    entry = E.TABLES.get(LEDGER_KIND)
    if entry is None:
        raise E.ContractError(f"the contract has no {LEDGER_KIND!r} table")
    cols = entry["columns"]
    if tuple(cols) != tuple(B.LEDGER_COLUMNS):
        raise E.ContractError(f"ledger columns drifted from the contract: job {B.LEDGER_COLUMNS}, "
                              f"contract {tuple(cols)}")
    if tuple(cols.values()) != tuple(B.LEDGER_DTYPES[c] for c in B.LEDGER_COLUMNS):
        raise E.ContractError("ledger column types drifted from the contract")
    return {c: getattr(pl, _POLARS_TYPES[t]) for c, t in cols.items()}


def _atomic(path, write):
    tmp = path + ".tmp"
    write(tmp)
    os.replace(tmp, path)


def write_ledger(dest, old, new_events):
    """Append `new_events`. The whole file is rewritten (parquet cannot append),
    so the rewrite is CHECKED to be the old rows plus rows at the end, and it is
    atomic: a crash leaves the previous ledger, never half of a new one. No new
    events and a ledger on disk -> nothing is written, so the bytes (and the
    upload record) do not move on a read that changed nothing."""
    import polars as pl
    if not new_events and os.path.exists(ledger_path(dest)):
        return 0
    rows = old + new_events
    B.assert_append_only(old, rows)
    schema = ledger_schema()
    df = pl.DataFrame([{c: r.get(c) for c in B.LEDGER_COLUMNS} for r in rows], schema=schema,
                      orient="row") if rows else pl.DataFrame(schema=schema)
    os.makedirs(os.path.dirname(ledger_path(dest)), exist_ok=True)
    _atomic(ledger_path(dest), df.write_parquet)
    _atomic(ledger_path(dest).replace(".parquet", ".csv"), df.write_csv)
    return len(new_events)


def previous_rows(dest, season, week):
    idx = load_json(os.path.join(week_dir(dest, season, week), "index.json"))
    if not idx or not idx.get("latest"):
        return [], idx
    doc = load_json(os.path.join(week_dir(dest, season, week), read_name(idx["latest"])))
    return doc["rows"], idx


def read_name(read_iso):
    return f"read-{read_iso.replace(':', '')}.json"


def read_key(season, week, read_iso):
    return f"board/nfl/{season}/wk{int(week):02d}/{read_name(read_iso)}"


def index_key(season, week):
    return f"board/nfl/{season}/wk{int(week):02d}/index.json"


def verdict():
    """R15, the walk-forward record the verdict strip quotes. The 2025 season is
    the latest; the other seasons are in brief 023 Part 1."""
    return {"id": "R15", "season": 2025, "brier_gap": 0.0195, "lo": 0.0144, "hi": 0.0253,
            "games": 284, "source": "research/walkforward.py (brief 023 Part 1, restated 2026-09-17)"}


def refuse_publish_tree(dest):
    """Refuse WEB_EXPORT_DIR (and anything inside it) as a destination.

    a-26 refused it because the Board had no contract kind. a-31 added the kinds
    and KEPT the refusal, for a different reason: the Board has its OWN tree
    (BOARD_EXPORT_DIR) and its own upload record. It publishes every 15-60
    minutes; the site export publishes three times a week. One tree would put two
    uploaders on one record, each overwriting the other's copy, and the web
    uploader skips `board/` anyway (export_web.BOARD_PREFIX)."""
    web = getattr(config, "WEB_EXPORT_DIR", None) or os.getenv("WEB_EXPORT_DIR")
    if not web:
        return
    d, w = os.path.normcase(os.path.abspath(dest)), os.path.normcase(os.path.abspath(web))
    if d == w or d.startswith(w + os.sep) or w.startswith(d + os.sep):
        raise SystemExit(f"--dest {dest} overlaps WEB_EXPORT_DIR; the Board publishes from its "
                         "own tree (BOARD_EXPORT_DIR) with its own upload record - refusing")


class NoRows(SystemExit):
    """A due read found nothing to put on the board (lines have not posted). Not
    a failure of the job: nothing is written, and `--tick` reports it and exits 0."""


def run(season, week, dest, read_ts=None, db=None, log=print):
    refuse_publish_tree(dest)
    read_ts = read_ts or time.time()
    read_iso = B.iso(read_ts)
    con = ro(db)
    # The registry's runtime check: SQLite reports every table this read touches,
    # and sync_keys refuses the write if any of them has no declared source.
    reads = R.watch(con, "nfl")
    counts = defaultdict(int)
    try:
        games = week_games(con, season, week)
        if not games:
            raise SystemExit(f"no nflverse schedule for {season} week {week}")
        prev, idx = previous_rows(dest, season, week)
        if idx and idx.get("latest") and idx["latest"] >= read_iso:
            raise SystemExit(f"read {read_iso} is not after the latest read {idx['latest']}")
        with feature_cache():
            fresh = fresh_rows(con, season, week, read_ts, games, counts)
        rows = B.merge_read(prev, fresh, read_ts, read_iso)
        rows = B.apply_grades(rows, lambda r: settle(con, season, week, r["game_id"],
                                                     r["gsis_id"], r["market"], r["line"]))
        from models import baseline
        mv = baseline.model_version()
        T = B.lean_threshold_at(read_iso)
        old = read_ledger(dest)
        pulled = {r["claim_id"]: r["pulled_at"] for r in rows
                  if r.get("void_reason") == B.VOID_MARKET_PULLED}
        new_ev = B.ledger_events(
            old, rows, read_iso,
            lambda ev: (settle(con, ev["season"], ev["week"], ev["game_id"], ev["gsis_id"],
                               ev["market"], ev["line"])
                        if parse_iso(read_iso) >= ev["kickoff_ts"] else None),
            pulled, mv, T)
    finally:
        con.close()
    if not rows:
        # Before lines post there is nothing to read, and an empty read file
        # would be indistinguishable from a broken one. Refuse BEFORE any write;
        # the page's "lines post from Tuesday" state reads last week's board.
        raise NoRows(f"board read produced ZERO rows for {season} wk{week} at {read_iso} "
                     f"({dict(counts) or 'no Odds API prop quotes'}) - nothing written")
    states = B.lean_states(old + new_ev, read_ts)
    # Every published lean of the week on this read, as one row, in its ledger
    # state - refused BEFORE any write (a-34: a moved line used to drop it).
    on_board = B.leans_on_board(old + new_ev, rows, season, week, read_ts)
    generated_at = E.iso()
    doc = {**E.envelope("board_read", generated_at, "nfl"),
           "read_at": read_iso, "season": season, "week": week, "rows": rows}
    reads_listed = sorted(set((idx or {}).get("reads", [])) | {read_iso})
    week_leans = {e["lean_id"] for e in old + new_ev
                  if e["event"] == "published" and e["season"] == season and e["week"] == week}
    index = {**E.envelope("board_index", generated_at, "nfl"),
             "season": season, "week": week, "reads": reads_listed, "latest": read_iso,
             "lean_threshold_pp": T,
             "lean_threshold_log": [list(x) for x in config.BOARD_LEAN_THRESHOLD_LOG],
             "model_version": mv, "verdict": verdict(),
             "leans": {s: sum(1 for l in week_leans if states[l] == s)
                       for s in (B.S_GRADED, B.S_UPCOMING, B.S_LIVE, B.S_VOID)},
             "definitions": {
                 "line": "the main line AT THIS READ: the listed threshold whose median de-vigged "
                         "over probability across DraftKings, FanDuel and BetMGM is closest to 0.5. "
                         "is_main is a property of the read, not of the market; it is false only on "
                         "a row kept for a published lean whose line the main line moved off.",
                 "gap_pp": "model minus market, probability points, signed",
                 "lean": "over at gap >= +T, under at gap <= -T",
                 "longshot": "market P(over) outside [0.15, 0.85]; multiplicative de-vig is biased "
                             "there - to revisit with Shin or power de-vig once settled data exists",
                 "void": "market_pulled | inactive | no_snap - a voided lean stays on the ledger",
                 "published_lean": "a lean is published at the first read that carries it and graded "
                                   "on the line it was published at, forever. It never leaves the "
                                   "board: if the main line moves off it, or the current read stops "
                                   "leaning that way at it, the row stays, frozen as priced at "
                                   "priced_at, flagged line_moved_after_publication or "
                                   "lean_changed_after_publication. So every published lean is on "
                                   "every later read, as exactly one row."}}
    wanted = {read_key(season, week, read_iso): doc, index_key(season, week): index}
    # THE GATE, BEFORE ANY WRITE: contract and source declarations for both
    # files. The ledger is written only once both pass, and the JSON only after
    # the ledger, so a crash between them leaves a ledger AHEAD of the index (the
    # next read re-derives the same rows and appends nothing twice) and never an
    # index naming leans the ledger does not hold.
    E.validate_contract(wanted)
    approved = R.require_declared(wanted)
    ledger_new = write_ledger(dest, old, new_ev)
    # No owned prefix: sync_keys deletes nothing here, whatever is on disk.
    written, deleted = E.sync_keys(dest, wanted, [])
    assert deleted == 0
    status = defaultdict(int)
    for r in rows:
        status[r["status"]] += 1
    summary = {"read_at": read_iso, "rows": len(rows), "status": dict(status),
               "fresh": dict(counts), "ledger_new": ledger_new, "written": written,
               "leans": index["leans"], "leans_on_board": on_board, "tables_read": sorted(reads),
               "sources": {f"{s}/{k}": list(v) for (s, k), v in sorted(approved.items())}}
    log(json.dumps(summary))
    return summary


# ------------------------------------------------------------------ the cadence

def _in_play(row, now_ts):
    """Kicked off and not settled. A row still UPCOMING in the last read whose
    game has since started is live now; the read that says so is a grading read."""
    return row["status"] == B.LIVE or (row["status"] == B.UPCOMING and row["kickoff_ts"] <= now_ts)


def weeks_in_play(con, season, now_ts, dest):
    """The weeks a tick must consider: the week of the NEXT kickoff (it is reading
    toward its games) plus any earlier week of the season whose latest read still
    has a LIVE row (it is grading). Returns [(week, games, prev_rows, idx)]."""
    rows = con.execute("SELECT week, MAX(kickoff_ts), MIN(kickoff_ts) FROM nfl_games "
                       "WHERE season=? AND game_type='REG' GROUP BY week ORDER BY week",
                       (season,)).fetchall()
    out = []
    upcoming = next((w for w, last, _first in rows if last > now_ts), None)
    for w, _last, _first in rows:
        if upcoming is not None and w > upcoming:
            break
        prev, idx = previous_rows(dest, season, w)
        grading = any(_in_play(r, now_ts) for r in prev)
        if w == upcoming or grading:
            out.append((w, week_games(con, season, w), prev, idx))
    return out


def due_reads(season, now_ts, dest, db=None):
    """[(week, reason)] of reads due now. Pure over the store and the tree."""
    con = ro(db)
    try:
        weeks = weeks_in_play(con, season, now_ts, dest)
    finally:
        con.close()
    out = []
    for w, games, prev, idx in weeks:
        last = parse_iso(idx["latest"]) if idx and idx.get("latest") else None
        kicks = [g["kickoff_ts"] for g in games.values()]
        live = [r for r in prev if _in_play(r, now_ts)]
        if B.read_due(now_ts, last, kicks, window_open_ts(games)):
            out.append((w, "read"))
        elif B.grade_due(now_ts, last, live):
            out.append((w, "grade"))
    return out


def tree_intact(dest, client, bucket, log=print):
    """-> the statement it approved; raises otherwise. Every key the Board's upload
    record (local, else the bucket's mirror) says was published must be on disk.
    A tree that lost its ledger would start a fresh one and a fresh index, and
    the uploader's append-only check would then refuse every tick - loud, but a
    dead Board. Refusing HERE names the fix (`--restore`) before any read."""
    state, source = E.load_upload_state(dest, client, bucket, log, state_key=E.BOARD_STATE_KEY)
    local = E.local_keys(dest, tables=True)
    missing = sorted(k for k in state if k not in local)
    if missing:
        raise SystemExit(f"the Board's tree at {dest} is missing {len(missing)} key(s) its upload "
                         f"record ({source}) says are published, e.g. {missing[0]} - run "
                         "`python -m jobs.board_read --restore` before reading again")
    return f"tree intact: {len(state)} published key(s) all on disk (record: {source})"


def restore(dest, client, bucket, log=print):
    """Download every `board/` key in the bucket that is missing locally. Writes
    only into the local tree; never uploads, never deletes."""
    keys = E._bucket_keys(client, bucket)
    if keys is None:
        raise SystemExit("could not list the bucket - nothing restored")
    board = sorted(k for k in keys if k.startswith(E.BOARD_PREFIX))
    local = E.local_keys(dest, tables=True)
    got = 0
    for k in board:
        if k in local:
            continue
        body = client.get_object(Bucket=bucket, Key=k)["Body"].read()
        path = E.local_path(dest, k)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        def put(tmp, b=body):
            with open(tmp, "wb") as f:
                f.write(b)
        _atomic(path, put)
        got += 1
    log(f"restored {got} of {len(board)} board key(s) into {dest}")
    return got


def board_lock(dest):
    """ONE WRITER PER BOARD TREE. A full-slate read measured 164-225 s on
    2026-09-24; the tick fires every 5 minutes, so a slow read and the next tick
    can overlap, and two processes rewriting one ledger is the 2026-09-11 shard
    incident again. The lock sits BESIDE the tree (never inside it: everything in
    the tree is a candidate key) and is keyed on the tree, not the checkout."""
    from core.single_instance import InstanceLock
    d = os.path.abspath(dest)
    return InstanceLock(os.path.join(os.path.dirname(d), os.path.basename(d) + ".board.lock"))


def tick(season, dest, upload=False, now_ts=None, db=None, client=None, log=print):
    """What the scheduled task runs. Reads the weeks that are due, then uploads.

    Exit semantics: a due read with no rows yet (lines not posted), and a tick
    that finds the previous one still running, are NOT failures - logged, exit 0.
    Anything else that refuses (the contract, the source gate, a lost tree, an
    append-only violation) raises and the task records a failure."""
    from core.single_instance import AlreadyRunning
    try:
        with board_lock(dest):
            return _tick(season, dest, upload, now_ts, db, client, log)
    except AlreadyRunning as e:
        log(f"previous tick still running - skipped: {e}")
        return {"skipped": "already running"}


def _tick(season, dest, upload, now_ts, db, client, log):
    now_ts = time.time() if now_ts is None else now_ts
    refuse_publish_tree(dest)
    out = {"at": B.iso(now_ts), "due": [], "read": [], "no_rows": [], "upload": None}
    bucket = None
    if upload:
        if not (config.WEB_R2_ACCESS_KEY_ID and config.WEB_R2_SECRET_ACCESS_KEY):
            log("R2 upload not configured - reading locally only")
            upload = False
        else:
            bucket = E.require_setting("WEB_R2_BUCKET")
            client = client or E.r2_client()
            out["tree"] = tree_intact(dest, client, bucket, log)
    due = due_reads(season, now_ts, dest, db)
    out["due"] = [f"wk{w:02d}:{why}" for w, why in due]
    for w, _why in due:
        try:
            s = run(season, w, dest, now_ts, db=db, log=log)
            out["read"].append({"week": w, "rows": s["rows"], "ledger_new": s["ledger_new"]})
        except NoRows as e:
            log(str(e))
            out["no_rows"].append(w)
    if upload:
        out["upload"] = E.upload(dest=dest, client=client, log=log, tree="board")
    log(json.dumps(out, default=str))
    return out


def check_tree(dest, show_keys=False, log=print):
    """Validate every Board file in a tree against the contract and the source
    gate WITHOUT writing, and (with `show_keys`) list every key with its bytes.
    -> {"keys", "bytes", "json", "tables"}. Refuses a tree with nothing in it:
    a check over zero files is not a check."""
    local = E.local_keys(dest, tables=True)
    board = {k: p for k, p in local.items() if k.startswith(E.BOARD_PREFIX)}
    if not board:
        raise SystemExit(f"no board/ keys under {dest} - nothing to check")
    js = {k: load_json(p) for k, p in board.items() if k.endswith(".json")}
    E.validate_contract(js)
    R.require_declared(js)
    tables = sorted(k for k in board if not k.endswith(".json"))
    if os.path.exists(ledger_path(dest)):
        import polars as pl
        pq = pl.read_parquet(ledger_path(dest))
        schema = ledger_schema()
        if dict(pq.schema) != schema:
            raise E.ContractError(f"ledger on disk does not match the contract: {dict(pq.schema)}")
        csv = pl.read_csv(ledger_path(dest).replace(".parquet", ".csv"), infer_schema_length=0)
        if csv.height != pq.height:
            raise E.ContractError(f"ledger.csv has {csv.height} rows, ledger.parquet {pq.height}")
        led = pq.to_dicts()
        B.lean_states(led, time.time())
        # a-34: each week's LATEST read carries every lean the ledger had published
        # for it by then, in the state the ledger then gave it. Events after that
        # read (another week's read may grade this week's lean) are not yet on it.
        for k in sorted(js):
            idx = js[k]
            if idx.get("kind") != "board_index":
                continue
            read = js.get(read_key(idx["season"], idx["week"], idx["latest"]))
            if read is None:
                raise E.ContractError(f"{k} names latest {idx['latest']}, which is not in the tree")
            asof = [e for e in led if e["event_at"] <= idx["latest"]]
            log(B.leans_on_board(asof, read["rows"], idx["season"], idx["week"],
                                 parse_iso(idx["latest"])))
    total = 0
    for k in sorted(board):
        size = os.path.getsize(board[k])
        total += size
        if show_keys:
            log(f"  {k:<64} {size} bytes")
    log(f"{len(board)} keys ({len(js)} JSON, {len(tables)} table files), {total} bytes, "
        f"all validated against {os.path.relpath(E.CONTRACT_PATH, E.ROOT)}")
    return {"keys": len(board), "bytes": total, "json": len(js), "tables": len(tables)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--dest", help="the Board's tree; --tick/--restore default to BOARD_EXPORT_DIR")
    ap.add_argument("--at", help="read time, ISO Z (replay); default now")
    ap.add_argument("--due", action="store_true", help="print whether a read is due, and exit")
    ap.add_argument("--tick", action="store_true",
                    help="the scheduled entry point: read every week that is due now")
    ap.add_argument("--upload", action="store_true",
                    help="with --tick: upload the Board's tree (never deletes)")
    ap.add_argument("--check", action="store_true",
                    help="validate the tree against the contract and the source gate; writes nothing")
    ap.add_argument("--keys", action="store_true", help="with --check: list every key and its bytes")
    ap.add_argument("--restore", action="store_true",
                    help="download board/ keys missing from the local tree; uploads nothing")
    ap.add_argument("--log", action="store_true",
                    help="append output, any traceback and the exit code to "
                         "<STORAGE_DIR>/logs/board_tick.log (the scheduled task's record)")
    a = ap.parse_args(argv)
    if a.log:
        return _logged(lambda: _main(a, ap), config.storage_path("logs", "board_tick.log"))
    return _main(a, ap)


def _logged(fn, path):
    """Run `fn` with stdout and stderr appended to `path`, and record the exit code
    and any traceback there - a scheduled task's console is seen by nobody."""
    import contextlib
    import traceback
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f, contextlib.redirect_stdout(f), \
            contextlib.redirect_stderr(f):
        print(f"--- board_read {B.iso(time.time())}")
        try:
            rc = fn()
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            if e.code not in (None, 0):
                print(f"refused: {e.code}")
        except BaseException:  # noqa: BLE001 - recorded, then re-signalled by the exit code
            traceback.print_exc()
            rc = 1
        print(f"exit={rc}")
    return rc


def _main(a, ap):
    now = parse_iso(a.at) if a.at else time.time()
    dest = a.dest
    if a.tick or a.restore:
        dest = dest or E.require_setting("BOARD_EXPORT_DIR")
    if not dest:
        ap.error("--dest is required (no default: a job that can publish must be told where)")
    if a.check:
        check_tree(dest, show_keys=a.keys)
        return 0
    if a.restore:
        with board_lock(dest):
            restore(dest, E.r2_client(), E.require_setting("WEB_R2_BUCKET"))
        return 0
    if a.tick:
        tick(a.season or current_season(now), dest, upload=a.upload, now_ts=now)
        return 0
    if a.season is None or a.week is None:
        ap.error("--season and --week are required for a single read")
    if a.due:
        con = ro()
        games = week_games(con, a.season, a.week)
        con.close()
        prev, idx = previous_rows(dest, a.season, a.week)
        last = parse_iso(idx["latest"]) if idx and idx.get("latest") else None
        due = B.read_due(now, last, [g["kickoff_ts"] for g in games.values()], window_open_ts(games))
        gdue = B.grade_due(now, last, [r for r in prev if r["status"] == B.LIVE])
        print(json.dumps({"read_due": due, "grade_due": gdue, "window_open": B.iso(window_open_ts(games))}))
        return 0
    try:
        with board_lock(dest):
            run(a.season, a.week, dest, now)
    except NoRows as e:
        print(str(e))
        return 3
    return 0


def current_season(now_ts):
    """The NFL season a date falls in: September-December is that year's, January
    to August the previous year's (the postseason and the offseason)."""
    d = datetime.fromtimestamp(now_ts, ET)
    return d.year if d.month >= 9 else d.year - 1


if __name__ == "__main__":
    raise SystemExit(main())
