"""Normalisers for CFBD JSON payloads. Same contract as `cfb.normalize`: rows in
schema order, every drop counted, measurements of the payload."""
import json
import re

from cfb import schema
from cfb.normalize import Normalized, _iso_ts


def _int(x):
    try:
        return None if x is None else int(x)
    except (TypeError, ValueError):
        return None


def _float(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _bool(x):
    return None if x is None else int(bool(x))


def _drop_duplicates(rows, table, dropped):
    cols = schema.columns(table)
    kidx = [cols.index(k) for k in schema.keys(table)]
    seen = {}
    for r in rows:
        k = tuple(r[i] for i in kidx)
        seen[k] = seen.get(k, 0) + 1
    dup = {k for k, n in seen.items() if n > 1}
    if dup:
        dropped["duplicate_key_rows"] = sum(seen[k] for k in dup)
    return [r for r in rows if tuple(r[i] for i in kidx) not in dup]


def cfbd_games(payload, season):
    t = "cfb_cfbd_games"
    dropped = {}
    rows = []
    for g in payload or []:
        if not isinstance(g, dict) or g.get("id") is None:
            dropped["no_game_id"] = dropped.get("no_game_id", 0) + 1
            continue
        ls = lambda v: json.dumps(v) if v is not None else None
        rows.append((
            _int(g.get("id")), _int(g.get("season")), _int(g.get("week")), g.get("seasonType"),
            _iso_ts(g.get("startDate")), _bool(g.get("completed")), _bool(g.get("neutralSite")),
            _bool(g.get("conferenceGame")),
            _int(g.get("homeId")), g.get("homeTeam"), g.get("homeClassification"),
            g.get("homeConference"), _int(g.get("homePoints")), ls(g.get("homeLineScores")),
            _int(g.get("awayId")), g.get("awayTeam"), g.get("awayClassification"),
            g.get("awayConference"), _int(g.get("awayPoints")), ls(g.get("awayLineScores")),
        ))
    rows = _drop_duplicates(rows, t, dropped)
    m = [("cfbd_games.rows", len(rows), None),
         ("cfbd_games.completed", sum(1 for r in rows if r[5]), None)]
    return Normalized(t, rows, dropped, m)


def provider_fold(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# fold key -> canonical name. Every provider seen 2013-2026 is listed, so a NEW
# name is visible as unmapped rather than silently becoming its own book.
PROVIDER_CANONICAL = {
    "bovada": "Bovada",
    "caesars": "Caesars",
    "caesarspennsylvania": "Caesars (Pennsylvania)",
    "caesarssportsbookcolorado": "Caesars Sportsbook (Colorado)",
    "consensus": "consensus",
    "draftkings": "DraftKings",
    "espnbet": "ESPN Bet",
    "numberfire": "numberfire",
    "sugarhouse": "SugarHouse",
    "teamrankings": "teamrankings",
    "williamhillnewjersey": "William Hill (New Jersey)",
}


class ProviderSplit(ValueError):
    """One file spells an UNMAPPED provider two ways. Refused so the name is
    added to PROVIDER_CANONICAL rather than guessed."""


def canonical_provider(raw: str) -> str:
    return PROVIDER_CANONICAL.get(provider_fold(raw), raw.strip())


_FORMATTED = re.compile(r"^(?P<team>.+?)\s+(?P<value>[-+]?\d+(?:\.\d+)?)$")


def spread_side(line, home, away):
    """Which side is `spread` quoted from? Read `formattedSpread` ("Alabama -7")
    and compare with the number. Returns 'home', 'away', or None when the text
    does not say (a pick'em, an unmatched name, a missing field)."""
    spread, text = _float(line.get("spread")), line.get("formattedSpread")
    if spread is None or not text:
        return None
    m = _FORMATTED.match(text.strip())
    if not m:
        return None
    team, value = m.group("team").strip(), float(m.group("value"))
    if value == 0:
        return None
    if team == home:
        return "home" if abs(spread - value) < 1e-9 else ("away" if abs(spread + value) < 1e-9 else None)
    if team == away:
        return "home" if abs(spread + value) < 1e-9 else ("away" if abs(spread - value) < 1e-9 else None)
    return None


VALUE_FIELDS = ("spread", "spreadOpen", "overUnder", "overUnderOpen", "homeMoneyline",
                "awayMoneyline")


def cfbd_lines(payload, season):
    """PROVIDER NAMES ARE NORMALISED, AND ONE BOOK CAN ARRIVE AS TWO FEEDS.

    CFBD writes DraftKings as both "DraftKings" and "Draft Kings" (from late
    2025). They are not duplicates: on the 176 games carrying both through
    2026 week 2, "Draft Kings" never has an opening value or a moneyline, and
    its spread differs on 30 - two snapshots of one book. A provider-level
    aggregate must see one DraftKings per game, so when both feeds quote a game
    the row whose raw name IS the canonical name is kept, the other is counted
    in `dropped` as `superseded_feed_rows`, and every value they disagreed on
    is measured. `provider_raw` says which feed a kept row came from.
    """
    t = "cfb_game_lines"
    dropped = {}
    rows, providers, sides = [], {}, {"home": 0, "away": 0, None: 0}
    games = with_lines = postseason = 0
    unmapped, collisions, disagreements = {}, 0, {}
    spellings = {}
    for g in payload or []:
        if not isinstance(g, dict) or g.get("id") is None:
            dropped["no_game_id"] = dropped.get("no_game_id", 0) + 1
            continue
        games += 1
        postseason += g.get("seasonType") == "postseason"
        lines = g.get("lines") or []
        with_lines += bool(lines)
        chosen = {}
        for ln in lines:
            raw = ln.get("provider")
            if not raw:
                dropped["no_provider"] = dropped.get("no_provider", 0) + 1
                continue
            fold = provider_fold(raw)
            spellings.setdefault(fold, set()).add(raw)
            if fold not in PROVIDER_CANONICAL:
                unmapped[raw] = unmapped.get(raw, 0) + 1
            prov = canonical_provider(raw)
            if prov in chosen:
                collisions += 1
                other = chosen[prov]
                for f in VALUE_FIELDS:
                    a, b = _float(other.get(f)), _float(ln.get(f))
                    if a is not None and b is not None and a != b:
                        disagreements[f] = disagreements.get(f, 0) + 1
                if ln.get("provider") == prov and other.get("provider") != prov:
                    chosen[prov] = ln
                dropped["superseded_feed_rows"] = dropped.get("superseded_feed_rows", 0) + 1
                continue
            chosen[prov] = ln
        for prov, ln in chosen.items():
            providers[prov] = providers.get(prov, 0) + 1
            sides[spread_side(ln, g.get("homeTeam"), g.get("awayTeam"))] += 1
            rows.append((
                _int(g.get("id")), _int(g.get("season")), _int(g.get("week")), g.get("seasonType"),
                _iso_ts(g.get("startDate")),
                _int(g.get("homeTeamId")), g.get("homeTeam"),
                _int(g.get("awayTeamId")), g.get("awayTeam"),
                prov, _float(ln.get("spread")), _float(ln.get("spreadOpen")),
                _float(ln.get("overUnder")), _float(ln.get("overUnderOpen")),
                _float(ln.get("homeMoneyline")), _float(ln.get("awayMoneyline")),
                ln.get("provider"),
            ))
    split = {k: sorted(v) for k, v in spellings.items()
             if len(v) > 1 and k not in PROVIDER_CANONICAL}
    if split:
        raise ProviderSplit(f"unmapped provider spelled more than one way: {split} - "
                            f"add it to cfb.cfbd_normalize.PROVIDER_CANONICAL")
    rows = _drop_duplicates(rows, t, dropped)
    m = [("cfbd_lines.games", games, None),
         ("cfbd_lines.games_with_lines", with_lines, None),
         ("cfbd_lines.postseason_games", postseason, None),
         ("cfbd_lines.rows", len(rows), None),
         ("cfbd_lines.providers", len(providers), json.dumps(dict(sorted(providers.items())))),
         ("cfbd_lines.spread_quoted_home_side", sides["home"], None),
         ("cfbd_lines.spread_quoted_away_side", sides["away"], None),
         ("cfbd_lines.spread_side_unchecked", sides[None], "pick'em, missing or unmatched"),
         # Opening values and moneylines are sparse (79% / 80% absent 2013-2025).
         ("cfbd_lines.rows_with_spread_open", sum(1 for r in rows if r[11] is not None), None),
         ("cfbd_lines.rows_with_total_open", sum(1 for r in rows if r[13] is not None), None),
         ("cfbd_lines.rows_with_moneyline", sum(1 for r in rows if r[14] is not None), None),
         ("cfbd_lines.provider_feed_collisions", collisions,
          json.dumps(dict(sorted(disagreements.items()))) if disagreements else None),
         ("cfbd_lines.unmapped_providers", len(unmapped),
          json.dumps(dict(sorted(unmapped.items()))) if unmapped else None)]
    return Normalized(t, rows, dropped, m)


NORMALIZERS = {"cfbd_games": cfbd_games, "cfbd_lines": cfbd_lines}
TABLES = {"cfbd_games": "cfb_cfbd_games", "cfbd_lines": "cfb_game_lines"}
