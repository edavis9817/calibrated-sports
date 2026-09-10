"""As-of feature extraction. Invariant #5 lives here.

Every feature is keyed `(entity_id, as_of_ts)` and may read nothing timestamped
after `as_of_ts`. The guard is pushed into SQL rather than applied afterwards,
because a filter you have to remember to write is a filter you will one day
forget: `kickoff_ts < as_of_ts` is in the join, so a leaking query returns no
rows instead of returning good-looking ones.

A leak here does not raise. It produces a model that looks excellent in backtest
and is worthless live, and it can sit undetected for a season. Hence `Provenance`
- every extraction reports the newest timestamp it actually touched, and the
caller asserts that against its own as_of before anything is written.
"""
import sqlite3
from dataclasses import dataclass, field

# Canonical stat -> the nfl_player_week column that measures it.
STAT_COLUMN = {
    "receptions": "receptions",
    "targets": "targets",
    "rush_attempts": "carries",
    "rush_yards": "rushing_yards",
    "receiving_yards": "receiving_yards",
    "passing_yards": "passing_yards",
    "pass_attempts": "attempts",
    "completions": "completions",
}

COUNT_STATS = {"receptions", "targets", "rush_attempts", "pass_attempts",
               "completions"}


class LeakError(AssertionError):
    """A feature read something dated after as_of_ts."""


@dataclass
class Provenance:
    """What was read, and the newest timestamp among it."""
    sources: list = field(default_factory=list)      # (name, max_ts, n_rows)

    def add(self, name, max_ts, n_rows=0):
        self.sources.append((name, max_ts, n_rows))

    def merge(self, other):
        self.sources.extend(other.sources)
        return self

    @property
    def newest(self):
        seen = [ts for _, ts, _ in self.sources if ts is not None]
        return max(seen) if seen else None

    def assert_as_of(self, as_of_ts):
        """Raise unless everything read predates as_of_ts. Called before write."""
        bad = [(n, ts) for n, ts, _ in self.sources
               if ts is not None and ts >= as_of_ts]
        if bad:
            raise LeakError(
                f"feature(s) dated at or after as_of_ts={as_of_ts:.0f}: "
                + ", ".join(f"{n}@{ts:.0f} (+{(ts - as_of_ts) / 3600:.1f}h)"
                            for n, ts in bad))
        return True


@dataclass
class PriorUsage:
    gsis_id: str
    stat: str
    n_games: int
    mean: float
    var: float
    p_zero: float
    last_team: str
    provenance: Provenance


# The as-of join, written once. nfl_player_week has no kickoff of its own, so
# the game it belongs to supplies the timestamp; a player-week is "known" only
# once its game has been played.
_ASOF_JOIN = """
    FROM nfl_player_week pw
    JOIN (SELECT season, week, home_team, away_team, MAX(kickoff_ts) kickoff_ts
            FROM nfl_games GROUP BY game_id) g
      ON g.season = pw.season AND g.week = pw.week
     AND (g.home_team = pw.team OR g.away_team = pw.team)
    JOIN (SELECT gsis_id, season, week, season_type, MAX(data_version) dv
            FROM nfl_player_week GROUP BY gsis_id, season, week, season_type) v
      ON v.gsis_id = pw.gsis_id AND v.season = pw.season AND v.week = pw.week
     AND v.season_type = pw.season_type AND v.dv = pw.data_version
   WHERE pw.season_type = 'REG' AND g.kickoff_ts < :as_of
"""


def player_prior(con: sqlite3.Connection, gsis_id: str, stat: str,
                 as_of_ts: float, seasons=(2025,)) -> PriorUsage:
    """One player's prior usage for a stat, using only games already played."""
    col = STAT_COLUMN[stat]
    seasons = tuple(seasons)
    yrs = ",".join(str(int(x)) for x in seasons)
    q = (f"SELECT COUNT(*), AVG(pw.{col}), AVG(pw.{col} * pw.{col}), "
         f"       AVG(CASE WHEN COALESCE(pw.{col},0) = 0 THEN 1.0 ELSE 0.0 END), "
         f"       MAX(g.kickoff_ts) "
         + _ASOF_JOIN +
         f"   AND pw.gsis_id = :gid AND pw.season IN ({yrs}) "
         f"   AND pw.{col} IS NOT NULL")
    n, mean, mean_sq, p_zero, max_ts = con.execute(
        q, {"as_of": as_of_ts, "gid": gsis_id}).fetchone()

    # The team he FINISHED on, chronologically. Deliberately not MAX(team):
    # that is alphabetical, and 87 players changed teams during 2025 alone, so
    # the roster-change penalty would fire on the wrong ones in both directions.
    last_q = ("SELECT pw.team " + _ASOF_JOIN +
              f"   AND pw.gsis_id = :gid AND pw.season IN ({yrs}) "
              f" ORDER BY pw.season DESC, pw.week DESC LIMIT 1")
    row = con.execute(last_q, {"as_of": as_of_ts, "gid": gsis_id}).fetchone()
    team = row[0] if row else None

    prov = Provenance()
    prov.add(f"nfl_player_week:{gsis_id}:{stat}", max_ts, n or 0)
    var = max((mean_sq or 0.0) - (mean or 0.0) ** 2, 0.0) if n else 0.0
    return PriorUsage(gsis_id, stat, n or 0, mean or 0.0, var,
                      p_zero or 0.0, team, prov)


def _ranked_cte(col: str, seasons) -> str:
    """Per-player prior-season aggregates, ranked within (team, position).

    Written as a CTE because SQLite will not let a window alias be used in
    HAVING - the rank has to be computed in one scope and filtered in the next.
    """
    yrs = ",".join(str(int(x)) for x in seasons)
    return f"""
        WITH pg AS (
            SELECT pw.gsis_id, pw.team, pw.position,
                   COUNT(*) n,
                   AVG(pw.{col}) m,
                   AVG(pw.{col} * pw.{col}) msq,
                   AVG(CASE WHEN COALESCE(pw.{col},0)=0 THEN 1.0 ELSE 0.0 END) z,
                   SUM(COALESCE(pw.{col},0)) tot,
                   MAX(g.kickoff_ts) mx
            {_ASOF_JOIN}
              AND pw.season IN ({yrs})
              AND pw.{col} IS NOT NULL
            GROUP BY pw.gsis_id, pw.team, pw.position
        ),
        ranked AS (
            SELECT pg.*, RANK() OVER (PARTITION BY team, position
                                      ORDER BY tot DESC) rnk
              FROM pg
        )
    """


_SNAP_ROLE_SQL = """
WITH share AS (
  SELECT x.gsis_id AS gsis_id, s.team AS team, s.position AS position,
         AVG(s.offense_pct) AS pct
    FROM nfl_snap_counts s
    JOIN player_xwalk x ON x.pfr_id = s.pfr_player_id
    JOIN nfl_games g    ON g.game_id = s.game_id
   WHERE s.season IN (%s) AND s.offense_pct IS NOT NULL
     AND g.kickoff_ts < :as_of
   GROUP BY x.gsis_id, s.team, s.position
),
ranked AS (
  SELECT gsis_id, team, position, pct,
         ROW_NUMBER() OVER (PARTITION BY team, position
                            ORDER BY pct DESC) AS rnk
    FROM share
)
SELECT MIN(rnk, :cap) FROM ranked WHERE gsis_id = :gid ORDER BY pct DESC LIMIT 1
"""


def snap_role(con: sqlite3.Connection, gsis_id: str, as_of_ts: float,
              seasons=(2025,), cap: int = 4):
    """Role from SNAP SHARE, ranked within (team, position). None if unknown.

    Independent of the stat being predicted, which the volume-rank version is
    not: ranking a player by the very quantity the model is about to forecast
    makes his shrinkage target a function of his own outcome, so he is pulled
    toward players who already looked like him. That quietly undoes the
    shrinkage it is supposed to inform, and it does so hardest at the high end
    where the lines are.

    Snap share is what "bell cow / committee / rotational" actually means, and
    it is measured before the ball is snapped rather than after.
    """
    q = _SNAP_ROLE_SQL % ",".join(str(int(x)) for x in seasons)
    row = con.execute(q, {"as_of": as_of_ts, "gid": gsis_id,
                          "cap": cap}).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def role_rank(con: sqlite3.Connection, gsis_id: str, stat: str,
              as_of_ts: float, seasons=(2025,), cap: int = 4) -> int:
    """Where the player sat in his own team's pecking order for this stat.

    Position alone is the wrong shrinkage target, and it fails in a specific
    one-directional way: an RB1 and a third-string back are both "RB", so
    shrinking toward the all-RB mean drags every starter down and lifts every
    backup. On carries that was worth about six attempts a game here - larger
    than any edge being hunted.

    Rank comes from the prior season's volume within (team, position). For a
    player who changed teams it is stale, since his new depth is genuinely
    unknown pre-season without a normalized depth chart, but the team-change
    penalty already cuts his weight and a stale role beats no role at all.
    """
    q = _ranked_cte(STAT_COLUMN[stat], seasons) +         " SELECT MIN(rnk, :cap) FROM ranked WHERE gsis_id = :gid ORDER BY n DESC LIMIT 1"
    row = con.execute(q, {"as_of": as_of_ts, "gid": gsis_id,
                          "cap": cap}).fetchone()
    return int(row[0]) if row and row[0] is not None else cap


def positional_prior(con: sqlite3.Connection, position: str, stat: str,
                     as_of_ts: float, seasons=(2025,), min_games: int = 4,
                     role: int = None, cap: int = 4) -> dict:
    """Mean and dispersion for players at this position, optionally at this role.

    The shrinkage target. Deliberately computed from the same as-of window as
    the player's own history - a target built from data the player's own prior
    could not see would leak through the back door.
    """
    q = _ranked_cte(STAT_COLUMN[stat], seasons) + """
        SELECT AVG(m), AVG(MAX(msq - m * m, 0)), AVG(z), COUNT(*), MAX(mx)
          FROM ranked
         WHERE position = :pos AND n >= :ming
    """
    args = {"as_of": as_of_ts, "pos": (position or "").upper(),
            "ming": min_games, "cap": cap}
    if role is not None:
        q += " AND MIN(rnk, :cap) = :role"
        args["role"] = role
    mean, var, p_zero, n_players, max_ts = con.execute(q, args).fetchone()
    prov = Provenance()
    prov.add(f"positional:{position}:r{role}:{stat}", max_ts, n_players or 0)
    return {"mean": mean or 0.0, "var": var or 0.0, "p_zero": p_zero or 0.0,
            "n_players": n_players or 0, "provenance": prov}


def team_context(con: sqlite3.Connection, team: str, season: int,
                 as_of_ts: float) -> dict:
    """Roster and scheme context for a team going into `season`.

    Coaching change zeroes team scheme history (CLAUDE.md: shotgun .79 -> .12).
    LIMITATION, stated rather than hidden: nflverse publishes head coach, not
    the playcaller. A head-coach change is a proxy - it misses an OC change
    under a retained head coach, and overstates the case when a new head coach
    keeps the incumbent coordinator. Carrying "the incoming playcaller's prior"
    properly needs a coordinator source this project does not ingest, so what
    happens here is the conservative half: shrink harder, claim nothing.
    """
    row = con.execute(
        """SELECT MAX(CASE WHEN season = :s - 1 THEN coach END),
                  MAX(CASE WHEN season = :s     THEN coach END),
                  MAX(CASE WHEN season = :s - 1 THEN kickoff_ts END)
             FROM (SELECT season, home_team team, home_coach coach, kickoff_ts
                     FROM nfl_games WHERE week = 1
                   UNION ALL
                   SELECT season, away_team, away_coach, kickoff_ts
                     FROM nfl_games WHERE week = 1)
            WHERE team = :t AND season IN (:s - 1, :s)""",
        {"s": season, "t": team}).fetchone()
    prev_coach, coach, prev_ts = row if row else (None, None, None)
    prov = Provenance()
    # The schedule and its coach assignments are published months ahead, so the
    # NEXT season's coach is legitimately known now. Only the prior season's
    # game timestamp is a fact that had to happen.
    prov.add(f"coach:{team}", prev_ts, 1)
    return {"team": team, "coach": coach, "prev_coach": prev_coach,
            "coach_changed": bool(prev_coach and coach and prev_coach != coach),
            "provenance": prov}
