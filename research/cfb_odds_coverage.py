"""W07 track C phase 3 step 2: which CFB games the Odds API lists, and what a pull costs.

    python -m research.cfb_odds_coverage            # newest archived /events snapshot
    python -m research.cfb_odds_coverage --file 168 # a specific cfb_raw_files.file_id

0 requests. Reads the archived `/sports/americanfootball_ncaaf/events` response
(fetched by `python -m jobs.ingest_cfb --odds-free`, verified 0 credits) and
`cfb.db` read-only.

JOIN. An event is matched to a `cfb_games` row by the UNORDERED pair of team
names and a kickoff within +/-1 day (a late Hawaii kickoff is the next UTC day;
a neutral site can swap home and away). Names come from `cfb_teams.display_name`
("Alabama Crimson Tide"), the same school + mascot form the Odds API writes.
Normalisation is fold-to-ASCII, drop punctuation, lowercase - deterministic, no
fuzzy matching. Anything that misses is LISTED, and a real alias goes in
`ALIASES` with the event's spelling on the left.

WHAT THIS CANNOT SAY. `/events` lists upcoming events only and carries no
markets, so it measures GAME coverage for this week, not which prop keys any
book hangs. That needs `/events/{id}/markets` at 1 credit per event (P1), which
is not run here.

COSTS are the docs' formulas applied to the counts measured here (fetched
2026-09-17 into `cfb/cache/oddsapi_docs/`); every prop figure is an UPPER bound
because historical event odds bill markets RETURNED, not requested.
"""
import argparse
import gzip
import json
import os
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import paths

DAY = 86400
# event spelling (normalised) -> cfb_teams display_name (normalised). Each was a
# miss on the 2026-09-17 snapshot, checked against the schedule: same kickoff to the
# minute and the same opponent (game ids in the comments).
ALIASES: dict[str, str] = {
    "umass minutemen": "massachusetts minutemen",                     # 401866424
    "southeastern louisiana lions": "se louisiana lions",             # 401868317
    "appalachian state mountaineers": "app state mountaineers",       # 401864574
    "nicholls state colonels": "nicholls colonels",                   # 401870764
    "sam houston state bearkats": "sam houston bearkats",             # 401870764
    "southern mississippi golden eagles": "southern miss golden eagles",  # 401861963
}

NFL_SETTING_MARKETS = 16      # main clone ODDS_PROP_MARKETS, see TRACK-C-HANDOFF.md §10
USAGE_PROPS = 5               # receptions, rush attempts, pass attempts, reception yds, rush yds
ALL_PROP_KEYS = 34            # docs: "NFL, NCAAF, CFL Player Props"
GAME_MARKETS = 3              # h2h, spreads, totals


def norm(s):
    if s is None:
        return None
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s.replace("'", ""))
    s = " ".join(s.lower().split())
    return ALIASES.get(s, s)


def iso_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def utc(ts, fmt="%Y-%m-%d %H:%MZ"):
    return datetime.fromtimestamp(ts, timezone.utc).strftime(fmt)


def load_events(conn, file_id=None):
    q = ("SELECT file_id, rel_path, fetched_ts FROM cfb_raw_files WHERE dataset='oddsapi_events' "
         + ("AND file_id=?" if file_id else "ORDER BY fetched_ts DESC LIMIT 1"))
    row = conn.execute(q, (file_id,) if file_id else ()).fetchone()
    if row is None:
        raise SystemExit("no archived oddsapi_events snapshot; run jobs.ingest_cfb --odds-free")
    with gzip.open(os.path.join(paths.raw_root(), *row[1].split("/"))) as f:
        return row, json.loads(f.read())


def team_names(conn, season):
    """team_id -> (normalised display name, which season it came from). Falls back to
    the prior season for ids the current teams file misses (cfb.team_identity_gaps)."""
    out = {}
    for s in (season - 1, season):
        for tid, name in conn.execute("SELECT team_id, display_name FROM cfb_teams WHERE season=? "
                                      "AND valid_to_ts IS NULL", (s,)):
            if name:
                out[tid] = (norm(name), s)
    return out


def match(conn, events, season):
    names = team_names(conn, season)
    lo = min(iso_ts(e["commence_time"]) for e in events) - DAY
    hi = max(iso_ts(e["commence_time"]) for e in events) + DAY
    games = conn.execute(
        "SELECT game_id, week, season_type, start_ts, home_id, away_id, home_team, away_team, "
        "home_division, away_division FROM cfb_games WHERE valid_to_ts IS NULL AND season=? "
        "AND start_ts BETWEEN ? AND ?", (season, lo, hi)).fetchall()
    by_pair = defaultdict(list)
    fallback = 0
    for g in games:
        h, a = names.get(g[4]), names.get(g[5])
        if h and a:
            by_pair[frozenset((h[0], a[0]))].append(g)
            fallback += (h[1] != season) + (a[1] != season)
    matched, missed, ambiguous = [], [], []
    for e in events:
        t = iso_ts(e["commence_time"])
        cands = [g for g in by_pair.get(frozenset((norm(e["home_team"]), norm(e["away_team"]))), [])
                 if abs(g[3] - t) <= DAY]
        if len(cands) == 1:
            matched.append((e, cands[0]))
        elif cands:
            ambiguous.append((e, cands))
        else:
            missed.append(e)
    return games, matched, missed, ambiguous, fallback


def div_pair(g):
    return "/".join(sorted(x or "?" for x in (g[8], g[9])))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=int, help="cfb_raw_files.file_id of an oddsapi_events snapshot")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--spendable", type=int, default=None,
                    help="credits above reserve (default: newest oddsapi_requests row minus "
                         "ODDS_RESERVE)")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)
    (fid, rel, fetched_ts), events = load_events(conn, a.file)
    print(f"snapshot file {fid}  fetched {utc(fetched_ts)}  {rel}")
    print(f"events listed: {len(events)}")
    for day, n in sorted(Counter(e["commence_time"][:10] for e in events).items()):
        print(f"  {day}  {n}")

    games, matched, missed, ambiguous, fallback = match(conn, events, a.season)
    print(f"\nJOIN to cfb_games (unordered display names, kickoff +/-1 day)")
    print(f"  matched {len(matched)} of {len(events)}   missed {len(missed)}   "
          f"ambiguous {len(ambiguous)}   team names from prior season: {fallback}")
    for e in missed:
        print(f"    MISS  {e['commence_time']}  {e['away_team']} @ {e['home_team']}")
    for e, c in ambiguous:
        print(f"    AMBIG {e['commence_time']}  {e['away_team']} @ {e['home_team']}  -> {[g[0] for g in c]}")
    dt = sorted(abs(iso_ts(e["commence_time"]) - g[3]) / 60 for e, g in matched)
    if dt:
        print(f"  kickoff |event - schedule| minutes: median {dt[len(dt) // 2]:.0f}  max {dt[-1]:.0f}  "
              f"exact {sum(x == 0 for x in dt)}")

    # coverage per week: listed vs scheduled, by division pair
    weeks = sorted({(g[2], g[1]) for _e, g in matched})
    listed_ids = {g[0] for _e, g in matched}
    last_kick = max(iso_ts(e["commence_time"]) for e in events)
    for st, wk in weeks:
        full = conn.execute("SELECT home_division, away_division, game_id, start_ts, away_team, "
                            "home_team FROM cfb_games WHERE valid_to_ts IS NULL AND season=? AND "
                            "season_type=? AND week=?", (a.season, st, wk)).fetchall()
        tot = Counter("/".join(sorted(x or "?" for x in r[:2])) for r in full)
        lst = Counter(div_pair(g) for _e, g in matched if (g[2], g[1]) == (st, wk))
        partial = max(r[3] for r in full) > last_kick
        print(f"\n{a.season} {st} week {wk}: listed {sum(lst.values())} of {len(full)} scheduled"
              + ("   PAST THE LISTING HORIZON (latest listed kickoff "
                 f"{utc(last_kick)}) - partial by the API's window, not a coverage figure"
                 if partial else ""))
        for k in sorted(tot, key=lambda k: -tot[k]):
            print(f"  {k:<10} listed {lst.get(k, 0):>3} of {tot[k]:>3}")
        if not partial:
            for r in full:
                if "fbs" in (r[0], r[1]) and r[2] not in listed_ids:
                    print(f"    FBS game not listed: {utc(r[3])}  {r[4]} @ {r[5]}  ({r[2]})")

    # the upcoming week: kickoff slots drive bulk-snapshot cost
    if not weeks:
        return 0
    st, wk = weeks[0]
    cur = [(e, g) for e, g in matched if (g[2], g[1]) == (st, wk)]
    n = len(cur)
    slots5 = {int(iso_ts(e["commence_time"]) // 300) for e, _g in cur}
    hours = {int(iso_ts(e["commence_time"]) // 3600) for e, _g in cur}
    print(f"\nCOSTS for {a.season} {st} week {wk} as listed now: N = {n} events, "
          f"{len(slots5)} distinct 5-minute kickoff slots, {len(hours)} kickoff hours")
    if a.spendable is None:
        rem = conn.execute("SELECT remaining FROM oddsapi_requests WHERE remaining IS NOT NULL "
                           "ORDER BY ts DESC LIMIT 1").fetchone()
        res = os.getenv("ODDS_RESERVE")
        spend = rem[0] - int(res) if rem and res and res.isdigit() else None
    else:
        spend = a.spendable
    rows = [
        ("P1  /events/{id}/markets, every listed event, once", n, "exact"),
        ("fwd one bulk live snapshot h2h,spreads,totals x us", GAME_MARKETS, "exact"),
        ("fwd one bulk live snapshot per kickoff hour", GAME_MARKETS * len(hours), "exact"),
        ("A   historical bulk game lines, one per 5-min slot", 10 * GAME_MARKETS * len(slots5), "exact"),
        ("B   A + 5 usage props x N + one listing per slot",
         10 * GAME_MARKETS * len(slots5) + 10 * USAGE_PROPS * n + len(slots5), "<="),
        ("C   16 NFL-setting markets x N + one listing per slot",
         10 * NFL_SETTING_MARKETS * n + len(slots5), "<="),
        ("kill all 34 prop keys x N", 10 * ALL_PROP_KEYS * n, "<="),
    ]
    print(f"  spendable above reserve: {spend}  (shared with the NFL logger)")
    print(f"  {'item':<56} {'credits':>9}  {'':<5} share")
    for label, cr, kind in rows:
        share = f"{100 * cr / spend:.1f}%" if spend else "-"
        print(f"  {label:<56} {cr:>9,}  {kind:<5} {share}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
