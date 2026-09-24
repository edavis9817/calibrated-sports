"""F12 step 1: is there a line-movement dataset here at all? Answered from the stores.

Reads the scratch copy written by research.f12_extract (never the live stores) and
prints, per source, how many line observations exist per game or market, over
what span, at what cadence - as distributions, not means. Also checks the one
place a movement series is known to be overwritten: the nflverse games file,
archived once per DAY while upstream changes it several times a day.

    python -m research.f12_movement_inventory --extract D:/temp/f12/extract.db \
        --raw D:/calibrated-sports/data/raw --out ../_relay/reports/f-12-inventory.json

Every section asserts on the count it read and refuses at zero: an empty read and
"no movement data" are opposite answers that print the same.
"""
import argparse
import collections
import hashlib
import json
import os
import sqlite3

EXTRACT_TS = None   # set from the newest row in the extract, never the wall clock


def q(p, n):
    """Nearest-rank quantiles of a sorted list, as a dict."""
    if not n:
        return {}
    return {k: p[min(len(p) - 1, int(len(p) * f))] for k, f in
            (("p10", .1), ("p25", .25), ("p50", .5), ("p75", .75), ("p90", .9))} | {
        "min": p[0], "max": p[-1], "n": len(p)}


def dist(values):
    v = sorted(values)
    return q(v, len(v))


def need(n, what):
    if not n:
        raise SystemExit(f"{what}: read zero rows - refusing to report absence from an empty read")
    return n


LEAD_BUCKETS_H = ((0, "post-kickoff"), (1, "<1h"), (6, "1-6h"), (24, "6-24h"),
                  (72, "24-72h"), (168, "3-7d"), (1e9, ">=7d"))


def lead_bucket(h):
    if h <= 0:
        return "post-kickoff"
    for lim, name in LEAD_BUCKETS_H[1:]:
        if h < lim:
            return name
    return ">=7d"


def nflverse(c, raw):
    out = {}
    rows = c.execute("SELECT season, game_id, count(*), count(DISTINCT spread_line), "
                     "count(DISTINCT total_line) FROM nfl_games GROUP BY season, game_id").fetchall()
    need(len(rows), "nfl_games")
    by = collections.defaultdict(list)
    for s, g, nv, ns, nt in rows:
        by[s].append((nv, ns, nt))
    out["versions_per_game_by_season"] = {
        s: {"games": len(v), "versions": dist([x[0] for x in v]),
            "games_spread_changed": sum(x[1] > 1 for x in v),
            "games_total_changed": sum(x[2] > 1 for x in v)}
        for s, v in sorted(by.items()) if s >= 2021}
    # The intra-day overwrite: nflverse_versions records every content change; the
    # raw path is per DAY, so a same-day change replaces the file on disk.
    vr = c.execute("SELECT data_version, sha256, rel_path FROM nflverse_versions "
                   "WHERE dataset='games' ORDER BY ingested_ts").fetchall()
    need(len(vr), "nflverse_versions games")
    day = collections.defaultdict(list)
    for v, sha, rel in vr:
        day[v].append((sha, rel))
    recorded = on_disk = 0
    per_day = {}
    for v, l in sorted(day.items()):
        shas = list(dict.fromkeys(s for s, _ in l))
        path = os.path.join(raw, l[-1][1])
        disk = hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None
        recorded += len(shas)
        on_disk += disk in shas
        per_day[v] = {"distinct_contents": len(shas), "survives": int(disk in shas),
                      "rel_paths": sorted({r for _, r in l})}
    out["games_file_archive"] = {"distinct_contents_recorded": recorded,
                                 "contents_on_disk": on_disk,
                                 "contents_overwritten": recorded - on_disk, "per_day": per_day}
    return out


def oddsapi_live(c):
    ko = dict(c.execute("SELECT event_id, close_ts FROM markets WHERE venue='oddsapi'"))
    need(len(ko), "oddsapi markets (kickoffs)")
    out = {}
    for mt in ("game", "prop"):
        ev = collections.defaultdict(set)
        per = collections.defaultdict(set)
        n = 0
        for e, v, m, t in c.execute("SELECT event_id, venue, market_id, ts FROM oddsapi_quotes "
                                    "WHERE source='live' AND market_type=?", (mt,)):
            n += 1
            k = ko.get(e)
            if k is None or t >= k:
                continue
            ev[e].add(round(t))
            per[(v, m)].add(round(t))
        need(n, f"oddsapi live {mt}")
        kicked = [len(ts) for e, ts in ev.items() if ko[e] <= EXTRACT_TS]
        leads = collections.Counter(lead_bucket((ko[e] - t) / 3600) for e, ts in ev.items() for t in ts)
        out[mt] = {"rows": n, "events_with_prekick_quotes": len(ev),
                   "events_kicked_by_extract": len(kicked),
                   "prekick_snapshot_instants_per_kicked_event": dist(kicked),
                   "prekick_instants_per_book_market": dist(len(s) for s in per.values()),
                   "snapshot_instants_by_lead": dict(leads)}
    return out


def oddsapi_historical(c):
    ko = {g: k for g, k in c.execute("SELECT game_id, max(kickoff_ts) FROM nfl_games GROUP BY game_id")}
    out = {}
    for mt in ("spreads", "totals", "prop"):
        ev = collections.defaultdict(set)
        n = 0
        for e, t in c.execute("SELECT event_id, ts FROM oddsapi_quotes "
                              "WHERE source='oddsapi_historical' AND market_type=?", (mt,)):
            n += 1
            ev[e].add(t)
        need(n, f"oddsapi historical {mt}")
        pre = [sorted(t for t in ts if t < ko[e]) for e, ts in ev.items() if e in ko]
        leads = collections.Counter(lead_bucket((ko[e] - t) / 3600) for e, ts in ev.items()
                                    if e in ko for t in ts)
        out[mt] = {"rows": n, "events": len(ev),
                   "events_without_kickoff": sum(e not in ko for e in ev),
                   "prekick_snapshot_instants_per_event": dist(len(p) for p in pre),
                   "earliest_prekick_lead_h": dist(round((ko[e] - min(t for t in ts if t < ko[e])) / 3600, 1)
                                                   for e, ts in ev.items()
                                                   if e in ko and any(t < ko[e] for t in ts)),
                   "snapshot_instants_by_lead": dict(leads)}
    return out


def kalshi(c):
    agg = c.execute("SELECT market_id, source, n, n_ts, t0, t1 FROM kalshi_market_agg").fetchall()
    need(len(agg), "kalshi_market_agg")
    by = collections.defaultdict(list)
    for m, s, n, nts, t0, t1 in agg:
        by[(m.split("-")[0], s)].append((nts, (t1 - t0) / 3600))
    series = {f"{k[0]} [{k[1]}]": {"markets": len(v), "distinct_ts_per_market": dist(x for x, _ in v),
                                   "span_h": dist(round(y, 1) for _, y in v)}
              for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])) if len(v) >= 50}
    # cadence: gap between successive distinct quote instants, game-line series, live
    gaps = collections.defaultdict(list)
    last = {}
    n = 0
    for m, t in c.execute("SELECT market_id, ts FROM kalshi_game WHERE source='live' "
                          "ORDER BY market_id, ts"):
        n += 1
        if last.get(m) is not None and t > last[m]:
            gaps[m.split("-")[0]].append(round(t - last[m], 1))
        last[m] = t
    need(n, "kalshi_game live")
    return {"series": series,
            "gap_s_between_successive_quotes": {k: dist(v) for k, v in gaps.items()}}


def cfb(c):
    out = {}
    r = c.execute("SELECT count(*), sum(valid_from_ts < start_ts), "
                  "sum(spread_open IS NOT NULL AND spread IS NOT NULL), "
                  "sum(spread_open IS NOT NULL AND spread IS NOT NULL AND spread_open <> spread), "
                  "sum(total_open IS NOT NULL AND total IS NOT NULL), "
                  "sum(valid_to_ts IS NOT NULL) FROM cfb_game_lines").fetchone()
    need(r[0], "cfb_game_lines")
    out["cfb_game_lines"] = {"rows": r[0], "read_before_kickoff": r[1],
                             "rows_with_open_and_last_spread": r[2], "of_which_moved": r[3],
                             "rows_with_open_and_last_total": r[4],
                             "rows_superseded_valid_to_set": r[5],
                             "by_provider": [dict(zip(("provider", "rows", "open_and_last", "moved",
                                                       "first_season", "last_season"), x))
                                             for x in c.execute(
                                 "SELECT provider, count(*), sum(spread_open IS NOT NULL AND spread IS NOT NULL), "
                                 "sum(spread_open IS NOT NULL AND spread IS NOT NULL AND spread_open<>spread), "
                                 "min(season), max(season) FROM cfb_game_lines GROUP BY provider "
                                 "ORDER BY 2 DESC")]}
    snaps = c.execute("SELECT outcome, count(*), sum(cost), min(hour_ts), max(hour_ts) "
                      "FROM cfb_odds_snapshots GROUP BY outcome").fetchall()
    need(len(snaps), "cfb_odds_snapshots")
    f = c.execute("SELECT count(DISTINCT src_file_id), count(DISTINCT fetched_ts), "
                  "count(DISTINCT event_id) FROM cfb_odds_quotes").fetchone()
    pre = [n for (n,) in c.execute("SELECT count(DISTINCT fetched_ts) FROM cfb_odds_quotes "
                                   "WHERE fetched_ts < commence_ts GROUP BY event_id")]
    started = [n for (n,) in c.execute("SELECT count(DISTINCT fetched_ts) FROM cfb_odds_quotes "
                                       "WHERE fetched_ts < commence_ts AND commence_ts <= ? "
                                       "GROUP BY event_id", (EXTRACT_TS,))]
    out["odds_forward"] = {
        "snapshots_by_outcome": [dict(zip(("outcome", "n", "credits", "first_hour_ts", "last_hour_ts"), s))
                                 for s in snaps],
        "distinct_files_in_quotes": f[0], "distinct_fetched_ts_in_quotes": f[1], "events": f[2],
        "prekick_snapshots_per_event": dist(pre),
        "prekick_snapshots_per_started_event": dist(started)}
    return out


def main():
    global EXTRACT_TS
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{a.extract}?mode=ro", uri=True)
    EXTRACT_TS = c.execute("SELECT max(ts) FROM oddsapi_quotes WHERE source='live'").fetchone()[0]
    res = {"extract_ts": EXTRACT_TS, "nflverse": nflverse(c, a.raw),
           "oddsapi_live_2026": oddsapi_live(c), "oddsapi_historical": oddsapi_historical(c),
           "kalshi": kalshi(c), "cfb": cfb(c)}
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
