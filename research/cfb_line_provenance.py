"""c-15: what the published CFB spread actually is - which provider, what quantity, read when.

    python -m research.cfb_line_provenance              # cfb.db, read-only, 0 requests
    python -m research.cfb_line_provenance --json out.json

Three questions c-14 left open, each answered from the store and the exporter's own
selection, never from the line row alone:

1. PROVIDER. `jobs.export_cfb_web.game_lines` is imported and run, so the provider
   counted here is the one the export publishes, not a re-implementation of it.
2. MAGNITUDE. Is the published number a quantity a book quoted? Three checks: the
   half-point grid (a book quotes on it; an average need not), `consensus` against the
   median of the books CFBD lists beside it, and - for 2026, where both exist - the
   CFBD line against the Odds API books captured BEFORE kickoff by `--odds-forward`.
3. CAPTURE TIME. CFBD carries no timestamp on a line, so the only knowable instant is
   when WE read it: `cfb_raw_files.fetched_ts` of the file the current row came from,
   against the game's `start_ts`.

Sign: CFBD `spread` and the Odds API home-outcome `point` are both negative when the
home team is favoured (c-14), so they compare without transformation.
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import paths  # noqa: E402
from cfb.cfbd_normalize import LINE_KINDS, PROVIDER_KIND  # noqa: E402
from cfb.oddsapi_join import match, norm, team_names  # noqa: E402
from jobs.export_cfb_web import STAT_ERA_FROM, game_lines  # noqa: E402

H = 3600.0


def connect():
    return sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))]


def on_half_grid(x):
    return x is not None and abs(x * 2 - round(x * 2)) < 1e-9


# -----------------------------------------------------------------------------
# 1. provider
# -----------------------------------------------------------------------------

def provider_rule(conn):
    """Which provider the export publishes, per game, and what the rule silently does."""
    chosen = game_lines(conn)                       # the exporter's own selection
    rows = defaultdict(list)
    for gid, prov, spread, total, season in conn.execute(
            "SELECT game_id, provider, spread, total, season FROM cfb_game_lines "
            "WHERE valid_to_ts IS NULL"):
        rows[gid].append((prov, spread, total, season))
    exported = {g for (g,) in conn.execute(
        "SELECT game_id FROM cfb_games WHERE valid_to_ts IS NULL AND season>=?", (STAT_ERA_FROM,))}

    by_prov, by_season = Counter(), defaultdict(Counter)
    spread_null_other_has, total_null_other_has = 0, 0
    spread_null_eligible_has, total_null_eligible_has = 0, 0   # must stay 0 (c-16)
    books_per_game = Counter()
    for gid in exported & set(chosen):
        cands = rows[gid]
        sp, tot = chosen[gid]
        # which provider produced the SPREAD: consensus first, then name order, among
        # line-eligible providers with a spread (c-16: third-party sites excluded, and
        # spread and total each chosen on their own - the total may be another row's)
        order = sorted((r for r in cands if PROVIDER_KIND.get(r[0]) in LINE_KINDS),
                       key=lambda r: (r[0] != "consensus", r[0]))
        with_sp = [r for r in order if r[1] is not None]
        with_tot = [r for r in order if r[2] is not None]
        assert sp == (with_sp[0][1] if with_sp else None), gid     # rule reproduced exactly
        assert tot == (with_tot[0][2] if with_tot else None), gid
        prov, season = (with_sp or order)[0][0], order[0][3]
        by_prov[prov] += 1
        by_season[season]["consensus" if prov == "consensus" else "fallback"] += 1
        books_per_game[len(cands)] += 1
        spread_null_other_has += sp is None and any(r[1] is not None for r in cands)
        total_null_other_has += tot is None and any(r[2] is not None for r in cands)
        spread_null_eligible_has += sp is None and bool(with_sp)
        total_null_eligible_has += tot is None and bool(with_tot)
    return {
        "games_exported_with_a_line": sum(by_prov.values()),
        "games_exported_total": len(exported),
        "chosen_provider": dict(by_prov.most_common()),
        "by_season": {s: dict(c) for s, c in sorted(by_season.items())},
        "providers_per_game": dict(sorted(books_per_game.items())),
        "spread_null_but_another_provider_has_one": spread_null_other_has,
        "total_null_but_another_provider_has_one": total_null_other_has,
        # "another provider" above includes third-party sites; these count only the
        # providers a line may come from, and the per-field rule makes them zero.
        "spread_null_but_a_line_provider_has_one": spread_null_eligible_has,
        "total_null_but_a_line_provider_has_one": total_null_eligible_has,
    }


# -----------------------------------------------------------------------------
# 2. magnitude
# -----------------------------------------------------------------------------

def grid(conn):
    out = {}
    for prov, n, on in conn.execute(
            "SELECT provider, COUNT(spread), SUM(CASE WHEN ABS(spread*2 - ROUND(spread*2)) < 1e-9 "
            "THEN 1 ELSE 0 END) FROM cfb_game_lines WHERE valid_to_ts IS NULL AND spread IS NOT NULL "
            "GROUP BY provider ORDER BY 2 DESC"):
        out[prov] = {"n": n, "on_half_point_grid": on, "share": round(on / n, 4)}
    return out


def consensus_vs_books(conn):
    """|consensus - median(other providers)| on games with consensus and >= 2 books."""
    per = defaultdict(dict)
    for gid, prov, sp in conn.execute(
            "SELECT game_id, provider, spread FROM cfb_game_lines WHERE valid_to_ts IS NULL "
            "AND spread IS NOT NULL"):
        per[gid][prov] = sp
    diffs_med, diffs_mean = [], []
    for gid, d in per.items():
        if "consensus" not in d:
            continue
        books = [v for p, v in d.items() if p not in ("consensus", "numberfire", "teamrankings")]
        if len(books) < 2:
            continue
        diffs_med.append(d["consensus"] - statistics.median(books))
        diffs_mean.append(d["consensus"] - statistics.fmean(books))
    ab = [abs(x) for x in diffs_med]
    return {
        "n_games": len(diffs_med),
        "equal_to_book_median": sum(1 for x in ab if x < 1e-9),
        "within_half_point_of_median": sum(1 for x in ab if x <= 0.5 + 1e-9),
        "abs_diff_median_p50_p90_max": [q(ab, .5), q(ab, .9), max(ab) if ab else None],
        "equal_to_book_mean": sum(1 for x in diffs_mean if abs(x) < 1e-9),
    }


def pre_kick_books(conn, season=2026):
    """game_id -> {book: home point} from the LAST Odds API snapshot fetched before the
    event's commence time. Only a snapshot strictly before kickoff is a pre-game price."""
    events = {}
    for eid, ct, home, away in conn.execute(
            "SELECT event_id, commence_ts, home_team, away_team FROM cfb_odds_events"):
        events[eid] = (ct, home, away)
    best = {}
    for eid, ct, book, name, point, fts in conn.execute(
            "SELECT event_id, commence_ts, bookmaker, outcome_name, point, fetched_ts "
            "FROM cfb_odds_quotes WHERE market_key='spreads' AND fetched_ts < commence_ts"):
        if eid not in events or name != events[eid][1] or point is None:
            continue
        k = (eid, book)
        if k not in best or fts > best[k][1]:
            best[k] = (point, fts)
    ev_list = [{"id": e, "commence_time": _iso(ct), "home_team": h, "away_team": a}
               for e, (ct, h, a) in events.items()]
    _, matched, _, _, _ = match(conn, ev_list, season)
    names = team_names(conn, season)
    out = {}
    for e, g in matched:
        books = {b: v for (eid, b), v in best.items() if eid == e["id"]}
        if not books:
            continue
        # A neutral site can swap home/away between feeds. Decide it with the SAME name
        # table and aliases the join used - a second, alias-blind comparison flipped the
        # sign on UL Monroe v SE Louisiana and manufactured a 19.5-point disagreement.
        same_home = names.get(g[4], (None,))[0] == norm(e["home_team"])
        out[g[0]] = {"books": books, "same_home": same_home, "commence_ts": events[e["id"]][0]}
    return out


def _iso(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def which_instant(conn, book="draftkings", cfbd_book="DraftKings", season=2026):
    """The discriminating form of the magnitude check. Agreement with the LAST pre-kick
    quote says nothing on a game whose line never moved, so restrict to games where the
    book's line MOVED inside our capture and ask which instant CFBD's value matches: the
    first quote we saw, the last before kickoff, or the first after it (a live line)."""
    events = {}
    for eid, ct, home in conn.execute("SELECT event_id, commence_ts, home_team FROM cfb_odds_events"):
        events[eid] = (ct, home)
    series = defaultdict(list)
    for eid, ct, name, point, fts in conn.execute(
            "SELECT event_id, commence_ts, outcome_name, point, fetched_ts FROM cfb_odds_quotes "
            "WHERE market_key='spreads' AND bookmaker=?", (book,)):
        if eid in events and name == events[eid][1] and point is not None:
            series[eid].append((fts, ct, point))
    full = {e: (ct, h, a) for e, ct, h, a in conn.execute(
        "SELECT event_id, commence_ts, home_team, away_team FROM cfb_odds_events")}
    ev_list = [{"id": e, "commence_time": _iso(ct), "home_team": h, "away_team": a}
               for e, (ct, h, a) in full.items()]
    _, matched, _, _, _ = match(conn, ev_list, season)
    names = team_names(conn, season)
    cf = {g: sp for g, sp in conn.execute(
        "SELECT game_id, spread FROM cfb_game_lines WHERE valid_to_ts IS NULL AND provider=? "
        "AND season=?", (cfbd_book, season)) if sp is not None}
    tally = Counter()
    for e, g in matched:
        if g[0] not in cf or e["id"] not in series:
            continue
        sign = 1 if names.get(g[4], (None,))[0] == norm(e["home_team"]) else -1
        pts = sorted(series[e["id"]])
        pre = [sign * p for f, c, p in pts if f < c]
        post = [sign * p for f, c, p in pts if f >= c]
        if len(pre) < 2 or pre[0] == pre[-1]:
            tally["line_did_not_move_pre_kick"] += 1
            continue
        v = cf[g[0]]
        tally["moved"] += 1
        tally["cfbd_eq_last_pre_kick"] += v == pre[-1]
        tally["cfbd_eq_first_seen"] += v == pre[0]
        tally["cfbd_eq_neither"] += v not in (pre[0], pre[-1])
        if post:
            tally["with_post_kick_quote"] += 1
            tally["post_kick_differs_from_last_pre"] += post[-1] != pre[-1]
            tally["cfbd_eq_last_post_kick_and_not_last_pre"] += (v == post[-1] and v != pre[-1])
    return dict(tally)


def versus_odds_api(conn):
    chosen = game_lines(conn)
    prov_of = {}
    for gid, prov, sp in conn.execute(
            "SELECT game_id, provider, spread FROM cfb_game_lines WHERE valid_to_ts IS NULL "
            "AND season=2026"):
        prov_of.setdefault(gid, {})[prov] = sp
    g_info = {gid: (h, a, hp, ap, w) for gid, h, a, hp, ap, w in conn.execute(
        "SELECT g.game_id, t1.display_name, t2.display_name, g.home_points, g.away_points, g.week "
        "FROM cfb_games g LEFT JOIN (SELECT team_id, display_name FROM cfb_teams WHERE "
        "valid_to_ts IS NULL GROUP BY team_id) t1 ON t1.team_id=g.home_id LEFT JOIN "
        "(SELECT team_id, display_name FROM cfb_teams WHERE valid_to_ts IS NULL GROUP BY team_id) "
        "t2 ON t2.team_id=g.away_id WHERE g.valid_to_ts IS NULL AND g.season=2026")}
    pk = pre_kick_books(conn)
    diffs, dk_diffs, sample, flipped = [], [], [], 0
    for gid, rec in pk.items():
        if gid not in chosen or chosen[gid][0] is None or gid not in g_info:
            continue
        home, away, hp, ap, wk = g_info[gid]
        sign = 1
        if not rec["same_home"]:
            sign, flipped = -1, flipped + 1     # the Odds API lists the other side as home
        pts = [sign * v[0] for v in rec["books"].values()]
        med = statistics.median(pts)
        d = chosen[gid][0] - med
        diffs.append(d)
        cf_dk = prov_of.get(gid, {}).get("DraftKings")
        if cf_dk is not None and "draftkings" in rec["books"]:
            dk_diffs.append(cf_dk - sign * rec["books"]["draftkings"][0])
        sample.append({"game_id": gid, "week": wk, "home": home, "away": away,
                       "score": f"{hp}-{ap}", "published_home_spread": chosen[gid][0],
                       "odds_api_median_pre_kick": med, "n_books": len(pts),
                       "lead_min": round(min(rec["commence_ts"] - v[1]
                                             for v in rec["books"].values()) / 60, 1)})
    ab = [abs(x) for x in diffs]
    dab = [abs(x) for x in dk_diffs]
    sample.sort(key=lambda r: -abs(r["published_home_spread"] - r["odds_api_median_pre_kick"]))
    return {
        "n_games": len(diffs), "odds_api_home_swapped": flipped,
        "abs_diff_vs_odds_median": {"exact": sum(x < 1e-9 for x in ab),
                                    "le_0.5": sum(x <= .5 + 1e-9 for x in ab),
                                    "le_1.0": sum(x <= 1 + 1e-9 for x in ab),
                                    "gt_3.0": sum(x > 3 + 1e-9 for x in ab),
                                    "p50": q(ab, .5), "p90": q(ab, .9),
                                    "max": max(ab) if ab else None},
        "signed_diff_mean": round(statistics.fmean(diffs), 3) if diffs else None,
        "cfbd_draftkings_vs_odds_api_draftkings": {
            "n": len(dab), "exact": sum(x < 1e-9 for x in dab),
            "le_0.5": sum(x <= .5 + 1e-9 for x in dab), "p90": q(dab, .9),
            "max": max(dab) if dab else None},
        "largest_disagreements": sample[:8],
        "sample_first_rows": sorted(sample, key=lambda r: r["game_id"])[:6],
    }


# -----------------------------------------------------------------------------
# 3. capture time
# -----------------------------------------------------------------------------

def capture_time(conn):
    by_part = {}
    rows = conn.execute(
        "SELECT l.src_season, l.src_part, l.start_ts, f.fetched_ts FROM cfb_game_lines l "
        "JOIN cfb_raw_files f ON f.file_id=l.src_file_id WHERE l.valid_to_ts IS NULL "
        "AND l.start_ts IS NOT NULL").fetchall()
    groups = defaultdict(list)
    for season, part, start, fetched in rows:
        key = f"{season}:{part}" if season >= 2026 else "2013-2025 backfill (both)"
        groups[key].append((fetched - start) / H)
    for k, lags in sorted(groups.items()):
        by_part[k] = {"rows": len(lags), "fetched_before_kickoff": sum(1 for x in lags if x < 0),
                      "lag_hours_min": round(min(lags), 1), "lag_hours_median": round(q(lags, .5), 1),
                      "lag_hours_max": round(max(lags), 1)}
    fetches = [dict(season=s, part=p, fetched_utc=_iso(t)) for s, p, t in conn.execute(
        "SELECT season, asset, fetched_ts FROM cfb_raw_files WHERE dataset='cfbd_lines' "
        "ORDER BY fetched_ts")]
    weeks_2026 = [dict(week=w, first_kick=_iso(a), last_kick=_iso(b)) for w, a, b in conn.execute(
        "SELECT week, MIN(start_ts), MAX(start_ts) FROM cfb_games WHERE valid_to_ts IS NULL AND "
        "season=2026 AND season_type='regular' AND week<=4 GROUP BY week ORDER BY week")]
    have = {p for (p,) in conn.execute(
        "SELECT DISTINCT src_part FROM cfb_game_lines WHERE valid_to_ts IS NULL AND season=2026")}
    moved = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN spread != spread_open THEN 1 ELSE 0 END) FROM cfb_game_lines "
        "WHERE valid_to_ts IS NULL AND spread IS NOT NULL AND spread_open IS NOT NULL").fetchone()
    return {"by_scope": by_part, "cfbd_line_fetches": fetches, "weeks_2026": weeks_2026,
            "parts_held_2026": sorted(have),
            "rows_with_open": moved[0], "rows_where_spread_differs_from_open": moved[1]}


def forward_capture(conn):
    """What the Odds API forward capture already holds: snapshots and their lead to kickoff."""
    snaps = conn.execute(
        "SELECT outcome, COUNT(*), SUM(COALESCE(cost,0)), MIN(fired_ts), MAX(fired_ts) "
        "FROM cfb_odds_snapshots GROUP BY outcome").fetchall()
    leads = [(e - f) / 60 for e, f in conn.execute(
        "SELECT earliest_commence_ts, fired_ts FROM cfb_odds_snapshots WHERE outcome='captured'")]
    return {"snapshots": [dict(outcome=o, n=n, credits=c, first=_iso(a), last=_iso(b))
                          for o, n, c, a, b in snaps],
            "lead_to_hour_first_kick_min": [round(min(leads), 1), round(q(leads, .5), 1),
                                            round(max(leads), 1)] if leads else None}


def historical_close_cost(conn, first=2020, last=2025):
    """What a timestamped close for past seasons would cost from the Odds API historical
    bulk endpoint: 10 x markets x regions per call (CLAUDE.md, measured from the docs
    2026-09-17), one call per distinct UTC kickoff hour, as the forward capture does.
    Kickoff hours are counted over games that CARRY a CFBD line, from `cfb_games.start_ts`,
    which has TBD placeholders - so this is an estimate of calls, not a quote."""
    out = {}
    for season in range(first, last + 1):
        hours = {int(ts // 3600) for (ts,) in conn.execute(
            "SELECT DISTINCT g.start_ts FROM cfb_games g JOIN cfb_game_lines l ON "
            "l.game_id=g.game_id AND l.valid_to_ts IS NULL WHERE g.valid_to_ts IS NULL AND "
            "g.season=? AND g.start_ts IS NOT NULL", (season,))}
        out[season] = {"kickoff_hours": len(hours), "credits_spreads_only": len(hours) * 10,
                       "credits_h2h_spreads_totals": len(hours) * 30}
    out["total"] = {k: sum(v[k] for v in out.values()) for k in
                    ("kickoff_hours", "credits_spreads_only", "credits_h2h_spreads_totals")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    conn = connect()
    res = {"db": str(paths.db_path()),
           "provider": provider_rule(conn),
           "grid": grid(conn),
           "consensus_vs_books": consensus_vs_books(conn),
           "versus_odds_api_2026": versus_odds_api(conn),
           "which_instant_draftkings_2026": which_instant(conn),
           "which_instant_bovada_2026": which_instant(conn, "bovada", "Bovada"),
           "capture_time": capture_time(conn),
           "forward_capture": forward_capture(conn),
           "historical_close_cost": historical_close_cost(conn)}
    if res["provider"]["games_exported_with_a_line"] == 0:
        raise SystemExit("no exported game carries a line - wrong store?")
    text = json.dumps(res, indent=1, default=str)
    print(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
