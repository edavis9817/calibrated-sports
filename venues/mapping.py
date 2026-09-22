"""Shared mapping helpers: names to gsis_id, venue teams to abbreviations,
dates to (season, week).

Venue-specific parsing stays in each adapter (invariant #1). What lives here is
the part every adapter needs and that must behave identically across them - if
Kalshi and Polymarket normalized a name differently, the same player would
produce two outcome_ids and the join key would be worthless.

IDENTITY IS RESOLVED HERE, AT INGEST. No analysis query ever sees a display
name, and there is no fuzzy matching anywhere: a name either resolves to exactly
one player or it is recorded unresolved. Fuzzy matching in analysis code is how
you silently price the wrong player's prop.
"""
import re
import time
import unicodedata

import store

# --- name normalization ------------------------------------------------------

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def norm_name(name: str) -> str:
    """Fold a display name to a comparison key.

    Venues write "A.J. Brown", ESPN writes "AJ Brown", someone writes
    "Wan'Dale Robinson" with a curly apostrophe. Strip accents, punctuation and
    generational suffixes; keep the rest.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ")
    s = _SUFFIX.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    # Collapse runs of single letters. nflverse writes "A.J. Brown", which
    # loses its periods and becomes "a j brown"; the sportsbook writes
    # "AJ Brown" -> "aj brown". Without this they are different keys, and it
    # was the single largest name failure in the Odds API pilot - 66 outcomes
    # on one player. Only RUNS of two or more collapse, so a lone middle
    # initial ("Robert L Jones") is left alone.
    parts, out = s.split(), []
    i = 0
    while i < len(parts):
        j = i
        while j < len(parts) and len(parts[j]) == 1:
            j += 1
        if j - i >= 2:
            out.append("".join(parts[i:j]))
            i = j
        else:
            out.append(parts[i])
            i += 1
    return " ".join(out)


# --- team abbreviations ------------------------------------------------------
# Kalshi writes city names ("New England", "Los Angeles R"), Polymarket writes
# nicknames in slugs ("lions", "49ers") and lowercase abbreviations. nflverse is
# canonical and everything folds to it.

TEAM_ALIASES = {}


def _team(abbr, *names):
    TEAM_ALIASES[norm_name(abbr)] = abbr
    for n in names:
        TEAM_ALIASES[norm_name(n)] = abbr


_team("ARI", "Arizona", "Cardinals", "Arizona Cardinals")
_team("ATL", "Atlanta", "Falcons", "Atlanta Falcons")
_team("BAL", "Baltimore", "Ravens", "Baltimore Ravens")
_team("BUF", "Buffalo", "Bills", "Buffalo Bills")
_team("CAR", "Carolina", "Panthers", "Carolina Panthers")
_team("CHI", "Chicago", "Bears", "Chicago Bears")
_team("CIN", "Cincinnati", "Bengals", "Cincinnati Bengals")
_team("CLE", "Cleveland", "Browns", "Cleveland Browns")
_team("DAL", "Dallas", "Cowboys", "Dallas Cowboys")
_team("DEN", "Denver", "Broncos", "Denver Broncos")
_team("DET", "Detroit", "Lions", "Detroit Lions")
_team("GB", "Green Bay", "Packers", "Green Bay Packers", "GNB")
_team("HOU", "Houston", "Texans", "Houston Texans")
_team("IND", "Indianapolis", "Colts", "Indianapolis Colts")
_team("JAX", "Jacksonville", "Jaguars", "Jacksonville Jaguars", "JAC")
_team("KC", "Kansas City", "Chiefs", "Kansas City Chiefs", "KAN")
_team("LA", "Los Angeles R", "Rams", "Los Angeles Rams", "LAR")
_team("LAC", "Los Angeles C", "Chargers", "Los Angeles Chargers")
_team("LV", "Las Vegas", "Raiders", "Las Vegas Raiders", "LVR", "OAK")
_team("MIA", "Miami", "Dolphins", "Miami Dolphins")
_team("MIN", "Minnesota", "Vikings", "Minnesota Vikings")
_team("NE", "New England", "Patriots", "New England Patriots", "NWE")
_team("NO", "New Orleans", "Saints", "New Orleans Saints", "NOR")
_team("NYG", "New York G", "Giants", "New York Giants")
_team("NYJ", "New York J", "Jets", "New York Jets")
_team("PHI", "Philadelphia", "Eagles", "Philadelphia Eagles")
_team("PIT", "Pittsburgh", "Steelers", "Pittsburgh Steelers")
_team("SEA", "Seattle", "Seahawks", "Seattle Seahawks")
_team("SF", "San Francisco", "49ers", "San Francisco 49ers", "SFO", "niners")
_team("TB", "Tampa Bay", "Buccaneers", "Tampa Bay Buccaneers", "TAM", "bucs")
_team("TEN", "Tennessee", "Titans", "Tennessee Titans")
_team("WAS", "Washington", "Commanders", "Washington Commanders", "WSH")


def team_abbr(name: str):
    return TEAM_ALIASES.get(norm_name(name))


# --- the crosswalk -----------------------------------------------------------

def build_crosswalk(data: bytes, version: str = None):
    """Normalize the nflverse players release into player_xwalk + player_alias.

    Aliases come from every name form nflverse publishes. An alias pointing at
    two players is KEPT: resolution is what refuses to guess, not ingestion, so
    the ambiguity stays visible rather than being silently decided by whichever
    row was inserted last.
    """
    import io as _io

    import polars as pl

    df = pl.read_parquet(_io.BytesIO(data))
    now = time.time()
    have = set(df.columns)
    xw_cols = ("gsis_id", "display_name", "first_name", "last_name", "position",
               "last_team", "last_season", "status", "pfr_id", "espn_id",
               "sleeper_id", "yahoo_id", "pff_id", "ingested_ts")

    def sid(v):
        return str(v) if v is not None else None

    rows, aliases = [], []
    for r in df.iter_rows(named=True):
        gid = r.get("gsis_id")
        if not gid:
            continue                       # no gsis_id, no join key, no row
        last_season = r.get("last_season")
        rows.append((gid, r.get("display_name"), r.get("first_name"),
                     r.get("last_name"), r.get("position"), r.get("latest_team"),
                     last_season, r.get("status"), r.get("pfr_id"),
                     sid(r.get("espn_id")), None, sid(r.get("yahoo_id")),
                     r.get("pff_id"), now))

        forms = {"display": r.get("display_name")}
        for key, col in (("football", "football_name"), ("short", "short_name")):
            if col in have:
                forms[key] = r.get(col)
        if r.get("first_name") and r.get("last_name"):
            forms["first_last"] = f"{r['first_name']} {r['last_name']}"
        if r.get("common_first_name") and r.get("last_name"):
            forms["common_last"] = f"{r['common_first_name']} {r['last_name']}"

        seen = set()
        for src, form in forms.items():
            a = norm_name(form)
            if a and a not in seen:
                seen.add(a)
                aliases.append((a, gid, src, last_season))

    # The external ids are PRESERVED when the release omits them. nflverse
    # drops a populated id column between releases - measured 2026-09-19: the
    # 09-19 file omitted pfr_id for 75 players and espn_id for 38 that 09-17
    # carried - and a whole-row write nulls every one of them. `pfr_id IS NULL`
    # then matches nothing in the snap-count join, so those players are dropped
    # from settlement and three research scripts SILENTLY, as a shortfall rather
    # than a visible gap. A restated id still wins; only silence is refused.
    #
    # sleeper_id and yahoo_id are deliberately NOT preserved: sleeper_id is
    # hardcoded None on line ~148 and neither is populated in the store (0.0%),
    # so preserving them would protect nothing and imply a guarantee we do not
    # have.
    store.upsert_preserving("player_xwalk", xw_cols, rows,
                            ("gsis_id",), ("pfr_id", "espn_id", "pff_id"))
    # player_alias has the INVERSE exposure and is left alone on purpose: rows
    # are never deleted, so a retired alias accumulates rather than vanishing.
    # That is a staleness problem, not a data-loss one, and fixing it is a
    # deletion decision that belongs in its own unit.
    store.replace_rows("player_alias",
                       ("alias", "gsis_id", "source", "last_season"),
                       aliases)
    return len(rows), len(aliases)


# --- resolution --------------------------------------------------------------

# Sportsbooks disambiguate same-name players inline, in two shapes seen in the
# 2023-2025 historical feed: a parenthesised team ("Chris Jones (KC)") and a
# trailing position code ("Akayleb Evans CB"). Both are FACTS the feed is
# handing over, so they are parsed out and used rather than stripped and lost.
_TEAM_SUFFIX = re.compile(r"\s*\(([A-Za-z]{2,3})\)\s*$")
_POS_SUFFIX = re.compile(
    r"\s+(CB|S|SS|FS|SAF|OLB|ILB|MLB|LB|DE|DT|NT|EDGE|QB|RB|WR|TE|K|P)$")


def parse_book_name(raw: str):
    """('Chris Jones (KC)') -> ('Chris Jones', 'KC', None).

    Returns (name, team_hint, position_hint). The hints are the whole point:
    a book that writes "(KC)" is telling you which Chris Jones it means, and
    throwing that away turns a resolvable name into an ambiguous one.
    """
    name = (raw or "").strip()
    team = pos = None
    m = _TEAM_SUFFIX.search(name)
    if m:
        team = team_abbr(m.group(1))
        name = _TEAM_SUFFIX.sub("", name).strip()
    m = _POS_SUFFIX.search(name)
    if m:
        pos = m.group(1).upper()
        name = _POS_SUFFIX.sub("", name).strip()
    return name, team, pos


class Unresolved(Exception):
    """A name did not resolve to exactly one player. Never guessed."""


def resolve_player(name: str, season: int = None, position: str = None,
                   teams=None):
    """name -> (gsis_id, method, confidence). Raises Unresolved otherwise.

    Ambiguity is broken only by facts - active-in-season, then position - never
    by similarity. If two players still match, that is an honest ambiguity and
    the market goes onto the unmapped list with a reason attached.
    """
    alias = norm_name(name)
    if not alias:
        raise Unresolved("empty name")
    with store.db() as c:
        hits = c.execute(
            "SELECT a.gsis_id, a.source, x.position, x.last_season "
            "FROM player_alias a LEFT JOIN player_xwalk x USING (gsis_id) "
            "WHERE a.alias = ?", (alias,)).fetchall()
    if not hits:
        raise Unresolved(f"no player matches {name!r}")
    if len(hits) == 1:
        return hits[0][0], f"alias:{hits[0][1]}", 1.0

    cands = hits
    # The strongest fact available, when the caller has it: a prop belongs to
    # one game, so the player must be on one of two rosters. This separates
    # same-name pairs that nothing else can - Byron Murphy the MIN corner from
    # Byron Murphy II the SEA tackle, both active in 2024.
    if teams and season is not None:
        ids = sorted({h[0] for h in cands})
        with store.db() as c:
            on_team = {r[0] for r in c.execute(
                f"SELECT DISTINCT gsis_id FROM nfl_player_week "
                f"WHERE gsis_id IN ({','.join('?' * len(ids))}) AND season = ? "
                f"AND team IN ({','.join('?' * len(teams))})",
                (*ids, season, *teams))}
        narrowed = [h for h in cands if h[0] in on_team]
        if len({h[0] for h in narrowed}) == 1:
            return narrowed[0][0], f"alias:{narrowed[0][1]}+team", 1.0
        cands = narrowed or cands
    if season is not None:
        active = [h for h in cands if h[3] is not None and h[3] >= season - 1]
        if len(active) == 1:
            return active[0][0], f"alias:{active[0][1]}+season", 0.95
        cands = active or cands
    if position:
        bypos = [h for h in cands if (h[2] or "").upper() == position.upper()]
        if len(bypos) == 1:
            return bypos[0][0], f"alias:{bypos[0][1]}+position", 0.9
        cands = bypos or cands
    if len({h[0] for h in cands}) > 1 and season is not None:
        # Two players share a name. Break it on a FACT - who actually recorded a
        # snap - not on similarity. "DeVonta Smith" the WR has 18 player-weeks;
        # "Devonta Smith" the practice-squad CB has none, and a venue listing a
        # receptions prop can only mean the former.
        ids = sorted({h[0] for h in cands})
        with store.db() as c:
            played = dict(c.execute(
                f"SELECT gsis_id, COUNT(*) FROM nfl_player_week "
                f"WHERE gsis_id IN ({','.join('?' * len(ids))}) "
                f"  AND season BETWEEN ? AND ? GROUP BY gsis_id",
                (*ids, season - 1, season)).fetchall())
        active = [h for h in cands if played.get(h[0], 0) > 0]
        if len({h[0] for h in active}) == 1:
            return active[0][0], f"alias:{active[0][1]}+played", 0.95
        cands = active or cands
    if len({h[0] for h in cands}) > 1:
        # Last fact available: roster status. ACT is on a 53; DEV is not.
        with store.db() as c:
            ids = sorted({h[0] for h in cands})
            stat = dict(c.execute(
                f"SELECT gsis_id, status FROM player_xwalk "
                f"WHERE gsis_id IN ({','.join('?' * len(ids))})", ids).fetchall())
        act = [h for h in cands if (stat.get(h[0]) or "").upper() == "ACT"]
        if len({h[0] for h in act}) == 1:
            return act[0][0], f"alias:{act[0][1]}+status", 0.9
    if len({h[0] for h in cands}) == 1:
        return cands[0][0], f"alias:{cands[0][1]}", 0.9
    raise Unresolved(f"{name!r} is ambiguous: {len({h[0] for h in cands})} players "
                     f"({', '.join(sorted({h[0] for h in cands})[:4])})")


# --- schedule lookup ---------------------------------------------------------

def game_for(team_a: str, team_b: str, gameday: str, tolerance_days: int = 1):
    """(season, week, game_id) for a matchup on or about a date.

    The team pair is matched EXACTLY. The date is matched within a day, because
    the venues disagree about which calendar day a night game belongs to:
    nflverse and Kalshi both call the 2026 opener 2026-09-09 (Eastern), while
    Polymarket's slug says 2026-09-10 (UTC, since kickoff is 00:20Z). Keyed on
    the venue's own date, that one game produces two different weeks - or, as it
    did here, simply fails to resolve on one venue.

    This is not fuzzy matching. An exact pair of teams plays at most once in any
    three-day window, so the pair plus a +/-1 day window is still a unique key.
    """
    from datetime import datetime, timedelta
    a, b = team_abbr(team_a), team_abbr(team_b)
    if not a or not b:
        raise Unresolved(f"unknown team(s): {team_a!r} / {team_b!r}")
    try:
        base = datetime.strptime(gameday, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise Unresolved(f"unparseable date {gameday!r}")
    days = [(base + timedelta(days=d)).strftime("%Y-%m-%d")
            for d in range(-tolerance_days, tolerance_days + 1)]
    with store.db() as c:
        rows = c.execute(
            f"SELECT season, week, game_id, gameday FROM nfl_games "
            f"WHERE gameday IN ({','.join('?' * len(days))}) "
            f"  AND ((home_team=? AND away_team=?) OR (home_team=? AND away_team=?)) "
            f"GROUP BY game_id ORDER BY gameday", (*days, a, b, b, a)).fetchall()
    if not rows:
        raise Unresolved(f"no scheduled game {a} vs {b} near {gameday}")
    if len({r[2] for r in rows}) > 1:
        raise Unresolved(f"{a} vs {b} near {gameday} matched "
                         f"{len(rows)} games: {[r[2] for r in rows]}")
    return rows[0][:3]


# --- shared stat vocabulary --------------------------------------------------
# One table, used by every adapter. Two venues that spelled "receiving yards"
# into different Stat members would produce two outcome_ids for one claim,
# which is precisely the failure this whole brief exists to prevent.

from core.outcomes import Stat        # noqa: E402  (kept next to its use)

STAT_WORDS = {
    "receptions": Stat.RECEPTIONS,
    "reception": Stat.RECEPTIONS,
    "targets": Stat.TARGETS,
    "rushing attempts": Stat.RUSH_ATTEMPTS,
    "rush attempts": Stat.RUSH_ATTEMPTS,
    "carries": Stat.RUSH_ATTEMPTS,
    "rushing yards": Stat.RUSH_YARDS,
    "rush yards": Stat.RUSH_YARDS,
    "receiving yards": Stat.RECEIVING_YARDS,
    "passing yards": Stat.PASSING_YARDS,
    "pass yards": Stat.PASSING_YARDS,
    "passing attempts": Stat.PASS_ATTEMPTS,
    "pass attempts": Stat.PASS_ATTEMPTS,
    "completions": Stat.COMPLETIONS,
    "touchdown": Stat.ANYTIME_TD,
    "touchdowns": Stat.ANYTIME_TD,
    "anytime touchdown": Stat.ANYTIME_TD,
}

# nflverse abbreviation -> the other spellings a venue might concatenate into a
# ticker. Kalshi writes LA for the Rams in some series and LAR in others.
ABBR_FORMS = {"LA": ("LA", "LAR"), "LAC": ("LAC",), "JAX": ("JAX", "JAC"),
              "LV": ("LV", "LVR"), "GB": ("GB", "GNB"), "KC": ("KC", "KAN"),
              "NE": ("NE", "NWE"), "NO": ("NO", "NOR"), "SF": ("SF", "SFO"),
              "TB": ("TB", "TAM"), "WAS": ("WAS", "WSH")}


def stat_from_words(text: str):
    """Longest-match a stat name out of free text, or None."""
    t = norm_name(text)
    for phrase in sorted(STAT_WORDS, key=len, reverse=True):
        if phrase in t:
            return STAT_WORDS[phrase]
    return None


def game_by_concat_teams(blob: str, gameday: str, tolerance_days: int = 1):
    """Resolve 'NESEA' + a date to a game, without guessing where to split.

    Kalshi concatenates the two team codes with no separator, and the split is
    genuinely ambiguous from the string alone (NE|SEA and NES|EA are both
    readable). So do not split it: ask the schedule which games happened near
    that date and check which one's two codes concatenate to this blob.
    """
    from datetime import datetime, timedelta
    blob = (blob or "").upper()
    try:
        base = datetime.strptime(gameday, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise Unresolved(f"unparseable date {gameday!r}")
    days = [(base + timedelta(days=d)).strftime("%Y-%m-%d")
            for d in range(-tolerance_days, tolerance_days + 1)]
    with store.db() as c:
        games = c.execute(
            f"SELECT season, week, game_id, away_team, home_team FROM nfl_games "
            f"WHERE gameday IN ({','.join('?' * len(days))}) GROUP BY game_id",
            days).fetchall()
    hits = []
    for season, week, gid, away, home in games:
        for x in ABBR_FORMS.get(away, (away,)):
            for y in ABBR_FORMS.get(home, (home,)):
                if blob in (x + y, y + x):
                    hits.append((season, week, gid))
    hits = list(dict.fromkeys(hits))
    if not hits:
        raise Unresolved(f"no game near {gameday} matches team blob {blob!r}")
    if len(hits) > 1:
        raise Unresolved(f"team blob {blob!r} near {gameday} is ambiguous: {hits}")
    return hits[0]
