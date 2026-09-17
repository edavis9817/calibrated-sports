"""Odds API event -> `cfb_games` row. Production code; research imports it, never the reverse.

An event matches a game by the UNORDERED pair of team names and a kickoff within
+/-1 day: a late Hawaii kickoff is the next UTC day, a neutral site can swap home
and away, and our schedule carries TBD placeholder times (04:00Z) the Odds API
does not. Names come from `cfb_teams.display_name` ("Alabama Crimson Tide"), the
same school + mascot form the Odds API writes.

Normalisation is fold-to-ASCII, drop punctuation, lowercase - deterministic, never
fuzzy. A miss is reported, and a real alias goes in ALIASES with the Odds API
spelling on the left, only after the schedule shows the same kickoff and opponent.

KICKOFF AUTHORITY. For scheduling a capture the Odds API's `commence_time` is
authoritative, not `cfb_games.start_ts`: it is the time the books price against.
The join is for labelling a game, never for timing a request.
"""
import unicodedata
from collections import defaultdict

from cfb.oddsapi import iso_ts

DAY = 86400

# Each was a miss on the 2026-09-17 snapshot, checked against the schedule: same
# kickoff to the minute and the same opponent (cfb_games.game_id in the comment).
ALIASES: dict[str, str] = {
    "umass minutemen": "massachusetts minutemen",                         # 401866424
    "southeastern louisiana lions": "se louisiana lions",                 # 401868317
    "appalachian state mountaineers": "app state mountaineers",           # 401864574
    "nicholls state colonels": "nicholls colonels",                       # 401870764
    "sam houston state bearkats": "sam houston bearkats",                 # 401870764
    "southern mississippi golden eagles": "southern miss golden eagles",  # 401861963
}


def norm(s):
    if s is None:
        return None
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in s.replace("'", ""))
    s = " ".join(s.lower().split())
    return ALIASES.get(s, s)


def team_names(conn, season):
    """team_id -> (normalised display name, the season it came from). Falls back to the
    prior season for ids the current teams file misses (cfb.team_identity_gaps)."""
    out = {}
    for s in (season - 1, season):
        for tid, name in conn.execute("SELECT team_id, display_name FROM cfb_teams WHERE season=? "
                                      "AND valid_to_ts IS NULL", (s,)):
            if name:
                out[tid] = (norm(name), s)
    return out


def match(conn, events, season):
    """(games in window, [(event, game)], [missed event], [(event, [games])], fallback names)."""
    if not events:
        return [], [], [], [], 0
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
