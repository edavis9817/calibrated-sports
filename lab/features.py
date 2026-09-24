"""As-of features. Pure functions: history in, one row's features out.

THE RULE (AUDIT 6.4): a feature for a bet in (season, week) reads only games
STRICTLY BEFORE that week. Not "before kickoff" by timestamp - by ordinal
(season * 100 + week), because a Thursday game and a Monday game share a week
and neither may see the other's result. `tests/test_lab_asof.py` shuffles and
rewrites every history row at or after the target week and asserts that no
feature moves; that is the guard, and it is the H1 lesson made mechanical.

What is deliberately NOT taken from the target week itself, even where it would
be knowable before kickoff:

- the player's TEAM and POSITION come from his last PRIOR game, and a team that
  does not match either side of the target game (a trade, a signing) yields
  None rather than a guess. Reading the week-w stat row for his team would make
  the as-of guard unable to tell a harmless read from a harmful one.

History is zero-filled from snap counts in the STAT'S phase, via
`core.settlement.snaps_played` - the same rule settlement uses - because
nflverse carries no row for a player who played and recorded nothing, and a
trailing mean that skips those games is biased upward by exactly the games the
settlement fix restored.
"""
import bisect
import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
WINDOWS = (3, 5, 10)
OPP_MIN_GAMES = 3


def ordinal(season, week):
    return int(season) * 100 + int(week)


# =============================================================================
# player form
# =============================================================================

class PlayerIndex:
    """gsis -> games in order. Each game: dict with ordinal, team, opponent,
    position, values {market: v}, snap_off, snap_def, target_share."""

    def __init__(self, history):
        self._by = {}
        for g in history:
            g = dict(g)
            g["ordinal"] = ordinal(g["season"], g["week"])
            self._by.setdefault(g["gsis"], []).append(g)
        for games in self._by.values():
            games.sort(key=lambda x: x["ordinal"])
        self._keys = {k: [g["ordinal"] for g in v] for k, v in self._by.items()}

    def prior(self, gsis, season, week):
        """Games strictly before (season, week), oldest first."""
        games = self._by.get(gsis)
        if not games:
            return []
        i = bisect.bisect_left(self._keys[gsis], ordinal(season, week))
        return games[:i]


def _phase_share(g, market):
    from core.settlement import DEFENSIVE_STATS
    return g.get("snap_def") if market in DEFENSIVE_STATS else g.get("snap_off")


def player_features(index, gsis, season, week, market, line, game_teams=None):
    """Form features for one (player, week, market, line). Every value is None
    when the history cannot support it - fewer than N prior games is None, not
    a mean over fewer, because "L5" over two games is a different feature."""
    prior = index.prior(gsis, season, week)
    vals = [g["values"].get(market) for g in prior]
    vals = [v for v in vals if v is not None]
    out = {"player.games_prior": len(vals)}
    last = prior[-1] if prior else None
    out["player.position"] = last.get("position") if last else None
    team = last.get("team") if last else None
    if game_teams is not None and team not in game_teams:
        team = None
    out["player.team"] = team
    for n in WINDOWS:
        tail = vals[-n:]
        ok = len(tail) == n
        m = sum(tail) / n if ok else None
        out["player.mean_l%d" % n] = m
        out["player.line_minus_mean_l%d" % n] = (line - m) if ok and line is not None else None
        out["player.cleared_l%d" % n] = (sum(1 for v in tail if v > line)
                                         if ok and line is not None else None)
    streak = 0
    if line is not None:
        for v in reversed(vals):
            if v > line and streak >= 0:
                streak += 1
            elif v < line and streak <= 0:
                streak -= 1
            else:
                break
    out["player.streak"] = streak if vals and line is not None else None
    shares = [_phase_share(g, market) for g in prior[-3:]]
    out["player.snap_share_l3"] = (sum(shares) / 3
                                   if len(shares) == 3 and None not in shares else None)
    ts = [g.get("target_share") for g in prior[-3:]]
    out["player.target_share_l3"] = (sum(ts) / 3 if len(ts) == 3
                                     and all(t is not None and t == t for t in ts)
                                     else None)
    return out


# =============================================================================
# matchup: what a defence has allowed to a position, this season, before now
# =============================================================================

class OpponentIndex:
    def __init__(self, history):
        # (season, defence, position, market) -> {week: summed value that game}
        acc = {}
        for g in history:
            opp, pos = g.get("opponent"), g.get("position")
            if not opp or not pos:
                continue
            for market, v in g["values"].items():
                if v is None:
                    continue
                k = (g["season"], opp, pos, market)
                wk = acc.setdefault(k, {})
                wk[g["week"]] = wk.get(g["week"], 0.0) + v
        self._acc = {k: sorted(v.items()) for k, v in acc.items()}
        self._by_group = {}
        for (season, opp, pos, market) in self._acc:
            self._by_group.setdefault((season, pos, market), []).append(opp)
        self._rank_cache = {}

    def allowed(self, season, week, defence, position, market):
        rows = self._acc.get((season, defence, position, market))
        if not rows:
            return None
        prior = [v for w, v in rows if w < week]
        return sum(prior) / len(prior) if len(prior) >= OPP_MIN_GAMES else None

    def rank(self, season, week, defence, position, market):
        """1 = allows the least, among defences with >= OPP_MIN_GAMES prior
        games at this as-of. None when the defence itself has too few."""
        key = (season, week, position, market)
        table = self._rank_cache.get(key)
        if table is None:
            scored = []
            for d in self._by_group.get((season, position, market), ()):
                a = self.allowed(season, week, d, position, market)
                if a is not None:
                    scored.append((a, d))
            scored.sort()
            table = {d: i + 1 for i, (_a, d) in enumerate(scored)}
            self._rank_cache[key] = table
        return table.get(defence)


# =============================================================================
# game context
# =============================================================================

def game_team_features(games, divisions=None):
    """{(game_id, team): features}. `games` are dicts with game_id, season,
    week, kickoff_ts, home_team, away_team, spread_line (home-relative,
    POSITIVE = home favoured - checked by correlation in brief 008, not
    assumed), total_line, roof, surface. Rest days read only the team's
    previous game."""
    divisions = divisions or {}
    by_team = {}
    for g in games:
        for t in (g["home_team"], g["away_team"]):
            by_team.setdefault(t, []).append(g)
    prev = {}
    for t, gs in by_team.items():
        gs = sorted(gs, key=lambda x: (x["kickoff_ts"] or 0))
        for i, g in enumerate(gs):
            prev[(g["game_id"], t)] = gs[i - 1] if i else None
    out = {}
    for g in games:
        k = g.get("kickoff_ts")
        local = (dt.datetime.fromtimestamp(k, ET) if k is not None else None)
        dh, da = divisions.get(g["home_team"]), divisions.get(g["away_team"])
        common = {
            "game.total": g.get("total_line"),
            "game.division": (dh == da) if dh and da else None,
            "game.primetime": (local.hour >= 19) if local else None,
            "game.dome": ((g.get("roof") or "").lower() in ("dome", "closed")
                          if g.get("roof") else None),
            "game.grass": ((g.get("surface") or "").lower() == "grass"
                           if g.get("surface") else None),
            "game.week": g.get("week"),
        }
        sl = g.get("spread_line")
        for team, is_home in ((g["home_team"], True), (g["away_team"], False)):
            point = None if sl is None else (-sl if is_home else sl)
            p = prev.get((g["game_id"], team))
            rest = None
            if p is not None and p.get("kickoff_ts") and k:
                rest = round((k - p["kickoff_ts"]) / 86400.0)
            tot = g.get("total_line")
            out[(g["game_id"], team)] = dict(
                common, **{
                    "game.home": is_home,
                    "game.spread": point,
                    "game.team_implied_total": (None if point is None or tot is None
                                                else (tot - point) / 2.0),
                    "game.rest_days": rest,
                })
    return out


def game_level_features(game_row, home_features):
    """Features for a bet on the GAME (a total): the home side's context, with
    team-specific fields removed because no team is being bet."""
    f = dict(home_features)
    for k in ("game.home", "game.team_implied_total", "game.rest_days"):
        f[k] = None
    return f
