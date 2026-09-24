"""The Board read job (audit 5.5, unit a-26). One read -> one static JSON file.

    python -m jobs.board_read --season 2026 --week 3 --dest D:/scratch/board
    python -m jobs.board_read --season 2026 --week 2 --dest ... --at 2026-09-20T15:00:00Z
    python -m jobs.board_read --season 2026 --week 3 --dest ... --due     # cadence only

Writes, under `--dest` (there is NO default - config has no defaults and a job
that can publish must be told where):

    board/nfl/{season}/wk{week}/read-{iso}.json    rows[] for this read
    board/nfl/{season}/wk{week}/index.json         reads[], latest, T, model, verdict
    board/nfl/ledger.parquet  + ledger.csv          append-only lean events

READ-ONLY ON THE STORE. Every query opens `market_log.db` with `mode=ro`; the
Board's own state is the files it wrote last time, so a read needs no table and
can never write a fact. `venues.mapping.resolve_player` is NOT used for that
reason - it opens `store.db()` read-write - and its alias-then-team rule is
repeated here over a read-only connection.

NOT WIRED INTO THE LOGGER YET, and does not upload. The cadence is
`core.board.read_due` / `grade_due` (pure, tested); `--due` prints what they say.
Wiring it into run_logger is a restart of a production process and is left to
a unit that is allowed to deploy.

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
        lo, hi = wilson(r["k"], r["n"])
        out[name] = dict(r, ci=[round(lo, 4), round(hi, 4)] if r["n"] else None)
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
    basis = defaultdict(int)
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


def band_payload(bands, market, gap):
    b = B.band_for(gap)
    if b is None:
        return None
    lo, hi = b
    key = f"{market}|{lo:g}-{hi:g}" if hi is not None else f"{market}|{lo:g}+"
    s = (bands or {}).get("bands", {}).get(key)
    base = {"lo_pp": lo, "hi_pp": hi, "source": "research/board_bands.py"}
    if not s:
        return dict(base, n=0, cleared=None, ci=None, roi=None, roi_ci=None,
                    note="no walk-forward record for this market and band")
    return dict(base, n=s["n"], cleared=s["cleared"], ci=s["ci"], roi=s["roi"],
                roi_ci=s["roi_ci"], roi_ci_method=s.get("roi_ci_method"))


def streak_payload(f11, stat, hist, line, away, opp):
    s = B.streak(hist, line, away, opp)
    if s is None:
        return None
    nxt = ((f11 or {}).get("next_rates") or {}).get(s["rule"])
    return {"rule": s["rule"], "k": s["k"], "n": s["n"], "display_rate": s["display_rate"],
            "next_rate": nxt["next_rate"] if nxt else None,
            "next_ci": [nxt["lo"], nxt["hi"]] if nxt else None,
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
            "band": band_payload(bands, market, gap) if side else None,
            "last10": [{"game_id": h["game_id"], "value": h["value"],
                        "cleared": None if h["value"] is None or h["value"] == line
                        else h["value"] > line} for h in hist[-10:]],
            "rates": dict(rates(hist, line, season),
                          career_posted=posted_record(con, gsis, market, g["kickoff_ts"])),
            "streak": streak_payload(f11, market, hist, line, team == g["away"], opp),
            "line_path": line_path(claim_books, snaps, eid, line, read_ts),
            "news": [],
            "status": B.UPCOMING, "result": None, "lean_result": None, "void_reason": None,
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


def write_ledger(dest, old, new_events):
    import polars as pl
    rows = old + new_events
    B.assert_append_only(old, rows)
    schema = {c: pl.Utf8 for c in B.LEDGER_COLUMNS}
    for c in ("season", "week", "mkt_books"):
        schema[c] = pl.Int64
    for c in ("line", "kickoff_ts", "mkt_p_over", "model_p_over", "gap_pp", "price",
              "lean_threshold_pp", "actual"):
        schema[c] = pl.Float64
    df = pl.DataFrame([{c: r.get(c) for c in B.LEDGER_COLUMNS} for r in rows], schema=schema,
                      orient="row") if rows else pl.DataFrame(schema=schema)
    os.makedirs(os.path.dirname(ledger_path(dest)), exist_ok=True)
    df.write_parquet(ledger_path(dest))
    df.write_csv(ledger_path(dest).replace(".parquet", ".csv"))


def previous_rows(dest, season, week):
    idx = load_json(os.path.join(week_dir(dest, season, week), "index.json"))
    if not idx or not idx.get("latest"):
        return [], idx
    doc = load_json(os.path.join(week_dir(dest, season, week), read_name(idx["latest"])))
    return doc["rows"], idx


def read_name(read_iso):
    return f"read-{read_iso.replace(':', '')}.json"


def verdict():
    """R15, the walk-forward record the verdict strip quotes. The 2025 season is
    the latest; the other seasons are in brief 023 Part 1."""
    return {"id": "R15", "season": 2025, "brier_gap": 0.0195, "lo": 0.0144, "hi": 0.0253,
            "games": 284, "source": "research/walkforward.py (brief 023 Part 1, restated 2026-09-17)"}


def refuse_publish_tree(dest):
    """Refuse WEB_EXPORT_DIR (and anything inside it) as a destination.

    The uploader ships every *.json under that tree on the next weekly
    `--upload-only`, validated against nothing: the Board has no contract kind
    yet (`board_index` / `board_read` are not in contract.schema.json, and a new
    kind must also be declared in the source registry - f-17 C1). And it would
    ship the reads WITHOUT the ledger, because the uploader skips non-JSON. So
    until both land, the Board writes only to a scratch tree it is pointed at.
    Lift this in the same commit that adds the contract kinds."""
    web = getattr(config, "WEB_EXPORT_DIR", None) or os.getenv("WEB_EXPORT_DIR")
    if not web:
        return
    d, w = os.path.normcase(os.path.abspath(dest)), os.path.normcase(os.path.abspath(web))
    if d == w or d.startswith(w + os.sep):
        raise SystemExit(f"--dest {dest} is inside WEB_EXPORT_DIR; the Board has no contract kind "
                         "yet and the uploader would publish it unvalidated - refusing")


def run(season, week, dest, read_ts=None, db=None, log=print):
    refuse_publish_tree(dest)
    read_ts = read_ts or time.time()
    read_iso = B.iso(read_ts)
    con = ro(db)
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
        raise SystemExit(f"board read produced ZERO rows for {season} wk{week} at {read_iso} "
                         f"({dict(counts) or 'no Odds API prop quotes'}) - nothing written")
    states = B.lean_states(old + new_ev, read_ts)
    wd = week_dir(dest, season, week)
    os.makedirs(wd, exist_ok=True)
    doc = {"read_at": read_iso, "sport": "nfl", "season": season, "week": week, "rows": rows}
    with open(os.path.join(wd, read_name(read_iso)), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    write_ledger(dest, old, new_ev)
    reads = sorted(set((idx or {}).get("reads", [])) | {read_iso})
    week_leans = {e["lean_id"] for e in old + new_ev
                  if e["event"] == "published" and e["season"] == season and e["week"] == week}
    index = {"sport": "nfl", "season": season, "week": week, "reads": reads, "latest": read_iso,
             "lean_threshold_pp": T, "lean_threshold_log": [list(x) for x in config.BOARD_LEAN_THRESHOLD_LOG],
             "model_version": mv, "verdict": verdict(),
             "leans": {s: sum(1 for l in week_leans if states[l] == s)
                       for s in (B.S_GRADED, B.S_UPCOMING, B.S_LIVE, B.S_VOID)},
             "definitions": {
                 "line": "the main line AT THIS READ: the listed threshold whose median de-vigged "
                         "over probability across DraftKings, FanDuel and BetMGM is closest to 0.5. "
                         "is_main is a property of the read, not of the market.",
                 "gap_pp": "model minus market, probability points, signed",
                 "lean": "over at gap >= +T, under at gap <= -T",
                 "longshot": "market P(over) outside [0.15, 0.85]; multiplicative de-vig is biased "
                             "there - to revisit with Shin or power de-vig once settled data exists",
                 "void": "market_pulled | inactive | no_snap - a voided lean stays on the ledger"}}
    with open(os.path.join(wd, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1)
    status = defaultdict(int)
    for r in rows:
        status[r["status"]] += 1
    summary = {"read_at": read_iso, "rows": len(rows), "status": dict(status),
               "fresh": dict(counts), "ledger_new": len(new_ev),
               "leans": index["leans"]}
    log(json.dumps(summary))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--at", help="read time, ISO Z (replay); default now")
    ap.add_argument("--due", action="store_true", help="print whether a read is due, and exit")
    a = ap.parse_args(argv)
    now = parse_iso(a.at) if a.at else time.time()
    if a.due:
        con = ro()
        games = week_games(con, a.season, a.week)
        con.close()
        prev, idx = previous_rows(a.dest, a.season, a.week)
        last = parse_iso(idx["latest"]) if idx and idx.get("latest") else None
        due = B.read_due(now, last, [g["kickoff_ts"] for g in games.values()], window_open_ts(games))
        gdue = B.grade_due(now, last, [r for r in prev if r["status"] == B.LIVE])
        print(json.dumps({"read_due": due, "grade_due": gdue, "window_open": B.iso(window_open_ts(games))}))
        return 0
    run(a.season, a.week, a.dest, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
