"""What changed this week: per-player and per-team usage deltas, with their noise.

    python -m analytics.deltas --publish  --market-log <path>   # the live season
    python -m analytics.deltas --show 00-0036355 --market-log <path>
    python -m analytics.deltas --calibrate --market-log <path>  # held-out check

THE QUESTION. Which players' jobs moved this week, and which teams changed how
they play. A page can not compute that from one season file without loading
every player, so it is computed here, once per week of the live season.

SEVEN MEASURES, each a CHANGE with the levels it was computed from:

    snap_share            player  offensive snaps / team offensive snaps
    target_share          player  targets / team targets
    carry_share           player  carries / team carries
    redzone_look_share    player  red-zone targets+carries / team's
    role_touch_share      player  touches in a third-down bucket / team's
    pace_seconds_per_play team    neutral-situation seconds per snap
    target_concentration  team    Herfindahl index of target shares

ONE METRIC PER MEASURE PER WEEK: `deltas.<measure>.wNN`, so a page showing a
week fetches that week and nothing else. Each carries five parts in `slice`
(`bucket|part` for the role measure):

    now      the level this week
    prev     the level in the team's previous game of the same season
    base     the level over the player's (team's) preceding four appearances
    wow      now - prev       "did it move since last week"
    vs_base  now - base       "is this week outside his recent role"

Both comparisons, because they answer different questions: a back splitting
work 50/50 for a month and then 70/30 moved against his baseline, while a
receiver bouncing 15% / 30% / 15% / 30% moves every week against the last and
never against the baseline.

THE PRE-REGISTERED MINIMUM, fixed before any 2026 figure was computed:

  * the team ran at least 40 offensive snaps in the game - and, for `wow`, in
    the previous game too, since that denominator is half the delta;
  * the subject has four earlier appearances FOR THIS TEAM. Without them
    nothing is published for that week, not even `wow` - a player's second
    career game changing from his first is not a role change. The baseline may
    reach back into the previous season; in weeks 1-4 it must, or no player
    would have one. A player who changed teams starts again.

A BYE IS NOT A DROP TO ZERO. Games are indexed by the TEAM's schedule, so a
bye week is simply not a game. A player who did not dress has no row for that
game and gets no value - `prev` is only taken when he appeared in the team's
immediately previous game. Null is not zero. A player who dressed and played
only special teams IS a zero offensive snap share, because that is a job lost.

APPEARANCE is a row in `nfl_snap_counts`, whatever the phase - the only record
of who dressed (the play-by-play names only players who touched the ball, and
`stats_player_week` has no row for a player who played with no stat).

THE NOISE BAND. A single game's share is a small sample and a 4-target week
is not a role change, so every level and every delta carries its interval.

  * Shares: Wilson for a level, Newcombe's hybrid score interval for a
    difference, with the binomial variance INFLATED by a measured dispersion
    `phi`. Plays inside a game are not independent draws - a game plan puts a
    receiver on the field for a series, not a snap - so a pure binomial band
    is too narrow. `phi` is the within-season, game-to-game Pearson dispersion
    of the same share around each player's own season level, pooled over
    every completed season before the live one. It is conservative on
    purpose: a genuine mid-season role change inflates it, which widens the
    band rather than narrowing it.
  * Pace and concentration: a per-game value whose variance scales with the
    plays it was measured on, `c^2 / plays`, with `c^2` measured the same way.

`n` IS GAMES (1 for `now` and `prev`, 4 for `base`, 2 and 5 for the deltas);
`rows` is the denominator - the team's snaps, targets, carries or red-zone
looks - which is how both weeks' denominators are published beside the change.
The interval is built on the denominator; `n` says how many games it spans.

NO RANKING. Every qualifying value is published and the site bands them
(LEDGER 2026-09-19). There is no "largest" here; a list of the largest changes
is the most selection-prone shape on a sports site, and the interval beside
each value is what lets a page say which changes are distinguishable from a
quiet week.

SOURCES. Targets, carries and down buckets come from `analytics.spine`, like
`role`, `vacancy` and `usage_stability`. Red-zone looks come from
`jobs.ingest_nflverse.pbp_looks` - the one red-zone definition in the repo,
which reconciles to `stats_player_week` - because the spine carries no field
position. Snaps come from `nfl_snap_counts`, read `mode=ro` from the logger's
database.
"""
import argparse
import math
import sys

from analytics import metrics, paths
from analytics.intervals import Estimate, _Z

# -----------------------------------------------------------------------------
# PRE-REGISTERED. Fixed before any live-season figure was computed; changing
# either is a new registration, not a tweak.
MIN_TEAM_SNAPS = 40
BASELINE_APPEARANCES = 4
# -----------------------------------------------------------------------------

POSITIONS = {"WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "HB": "RB",
             "QB": "QB"}
ROLE_BUCKETS = ("3rd_short", "3rd_med", "3rd_long")
# nfl_snap_counts writes the franchise's code AT THE TIME; the spine writes
# today's. a-17 measured these three as the whole difference 2013-2025.
TEAM_ALIAS = {"STL": "LA", "SD": "LAC", "OAK": "LV"}
SNAP_FIRST_SEASON = 2013
# A player-season needs this many appearances to contribute to `phi`: fewer and
# the season level it is measured around is itself mostly noise.
DISPERSION_MIN_GAMES = 6
CONF = 0.95

PLAYER_MEASURES = ("snap_share", "target_share", "carry_share",
                   "redzone_look_share", "role_touch_share")
TEAM_MEASURES = ("pace_seconds_per_play", "target_concentration")

SPECS = {
    "snap_share": dict(
        label="Change in snap share",
        unit=("share of his team's offensive snaps, as a proportion; `wow` and "
              "`vs_base` are differences in that proportion"),
        basis="snap_counts", requires=()),
    "target_share": dict(
        label="Change in target share",
        unit=("share of his team's targets, as a proportion; `wow` and "
              "`vs_base` are differences in that proportion"),
        basis="pbp", requires=(("pbp", "receiver_player_id"),)),
    "carry_share": dict(
        label="Change in carry share",
        unit=("share of his team's carries, as a proportion; `wow` and "
              "`vs_base` are differences in that proportion"),
        basis="pbp", requires=(("pbp", "rusher_player_id"),)),
    "redzone_look_share": dict(
        label="Change in red-zone look share",
        unit=("share of his team's red-zone looks - targets plus carries at or "
              "inside the opponent's 20 - as a proportion; a team with no "
              "red-zone snaps in a game has no value for it, not zero"),
        basis="pbp", requires=(("pbp", "receiver_player_id"),
                               ("pbp", "rusher_player_id"),
                               ("pbp", "yardline_100"))),
    "role_touch_share": dict(
        label="Change in third-down touch share",
        unit=("share of his team's touches (carries plus targets) in a "
              "third-down bucket, as a proportion; the play-by-play names only "
              "the player who touched the ball, so this is who got it, not who "
              "was on the field"),
        basis="pbp", requires=(("pbp", "down"), ("pbp", "ydstogo"))),
    "pace_seconds_per_play": dict(
        label="Change in neutral-situation pace",
        unit=("seconds between snaps within a drive, neutral situations only "
              "(analytics.pace); negative `wow` is faster"),
        basis="pbp", requires=(("pbp", "wp"), ("pbp", "game_seconds_remaining"),
                               ("pbp", "play_type"))),
    "target_concentration": dict(
        label="Change in target concentration",
        unit=("Herfindahl index of the team's targets - the sum of squared "
              "per-player target proportions, 1.0 when one player gets every "
              "target; `base` is the target-weighted mean of four games"),
        basis="pbp", requires=(("pbp", "receiver_player_id"),)),
}


# ---------------------------------------------------------------------------
# intervals
# ---------------------------------------------------------------------------

def _z(phi):
    return _Z[CONF] * math.sqrt(max(phi, 1.0))


def wilson_limits(num, den, z):
    """(lo, hi) of the Wilson score interval at an arbitrary z.

    Scaling z by sqrt(phi) is exactly a Wilson interval on an effective sample
    of den / phi, which is what a quasi-binomial variance means.
    """
    p = num / den
    d = 1 + z * z / den
    c = p + z * z / (2 * den)
    half = z * math.sqrt(p * (1 - p) / den + z * z / (4 * den * den))
    return max(0.0, (c - half) / d), min(1.0, (c + half) / d)


def newcombe(n1, d1, n2, d2, z):
    """(diff, lo, hi) for p1 - p2, Newcombe's hybrid score (method 10, 1998).

    Built from the two Wilson intervals, so it inherits their behaviour at 0
    and 1 - which is where a share delta lives when a player is benched.
    """
    p1, p2 = n1 / d1, n2 / d2
    l1, u1 = wilson_limits(n1, d1, z)
    l2, u2 = wilson_limits(n2, d2, z)
    diff = p1 - p2
    lo = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return diff, min(lo, diff), max(hi, diff)


def _method(kind, disp):
    return "%s95/%s=%.3f" % (kind, "phi" if kind != "norm" else "c2", disp)


def share_level(num, den, games, phi):
    lo, hi = wilson_limits(num, den, _z(phi))
    p = num / den
    return Estimate(p, min(lo, p), max(hi, p), games, _method("wilson", phi),
                    int(round(den)))


def share_delta(a, b, phi):
    """a, b are (num, den, games). The change a - b and its band."""
    diff, lo, hi = newcombe(a[0], a[1], b[0], b[1], _z(phi))
    return Estimate(diff, lo, hi, a[2] + b[2], _method("newcombe", phi),
                    int(round(a[1] + b[1])))


def mean_level(value, weight, games, c2):
    """A per-play mean whose variance is c2 / weight."""
    half = _Z[CONF] * math.sqrt(c2 / weight)
    return Estimate(value, value - half, value + half, games,
                    _method("norm", c2), int(round(weight)))


def mean_delta(a, b, c2):
    """a, b are (value, weight, games)."""
    diff = a[0] - b[0]
    half = _Z[CONF] * math.sqrt(c2 / a[1] + c2 / b[1])
    return Estimate(diff, diff - half, diff + half, a[2] + b[2],
                    _method("norm", c2), int(round(a[1] + b[1])))


# ---------------------------------------------------------------------------
# the comparisons - pure, so the tests can drive them with a hand-built season
# ---------------------------------------------------------------------------

def comparisons(schedule, present, value, team_ok, baseline=None):
    """Yield (subject, game, now, prev, base) for every qualifying appearance.

    schedule  {team: [(season, week, game_id), ...]} in playing order, which
              may span seasons - the baseline is allowed to reach back
    present   {(game_id, team): iterable of subjects who appeared}
    value     fn(game_id, team, subject) -> observation, or None when the
              share is undefined (a team with no red-zone snaps)
    team_ok   fn(game_id, team) -> the team-game clears MIN_TEAM_SNAPS

    `prev` is the team's immediately previous game in the SAME season, taken
    only if the subject appeared in it and it cleared the minimum. `base` is a
    list of the subject's last BASELINE_APPEARANCES observations for this team,
    None when he has fewer. Nothing is yielded without a baseline: that is the
    pre-registered minimum.
    """
    baseline = BASELINE_APPEARANCES if baseline is None else baseline
    for team, games in schedule.items():
        history = {}                      # subject -> [observations], in order
        for i, (season, week, game) in enumerate(games):
            here = set(present.get((game, team), ()))
            ok = team_ok(game, team)
            obs = {}
            for subject in here:
                v = value(game, team, subject)
                if v is not None:
                    obs[subject] = v
            if ok:
                prev_game = games[i - 1] if i > 0 else None
                same_season = prev_game is not None and prev_game[0] == season
                for subject, now in obs.items():
                    past = history.get(subject, [])
                    if len(past) < baseline:
                        continue
                    prev = None
                    if (same_season and team_ok(prev_game[2], team)
                            and subject in present.get((prev_game[2], team), ())):
                        prev = value(prev_game[2], team, subject)
                    yield (subject, (season, week, game), now, prev,
                           past[-baseline:])
            for subject, v in obs.items():
                history.setdefault(subject, []).append(v)


def player_parts(now, prev, base, phi):
    """{part: Estimate} for one player-week. Observations are (num, den)."""
    n_b = sum(b[0] for b in base)
    d_b = sum(b[1] for b in base)
    out = {"now": share_level(now[0], now[1], 1, phi)}
    if d_b > 0:
        out["base"] = share_level(n_b, d_b, len(base), phi)
        out["vs_base"] = share_delta((now[0], now[1], 1),
                                     (n_b, d_b, len(base)), phi)
    if prev is not None:
        out["prev"] = share_level(prev[0], prev[1], 1, phi)
        out["wow"] = share_delta((now[0], now[1], 1), (prev[0], prev[1], 1), phi)
    return out


def team_parts(now, prev, base, c2):
    """{part: Estimate} for one team-week. Observations are (value, weight)."""
    w_b = sum(b[1] for b in base)
    v_b = sum(b[0] * b[1] for b in base) / w_b
    out = {"now": mean_level(now[0], now[1], 1, c2),
           "base": mean_level(v_b, w_b, len(base), c2),
           "vs_base": mean_delta((now[0], now[1], 1), (v_b, w_b, len(base)), c2)}
    if prev is not None:
        out["prev"] = mean_level(prev[0], prev[1], 1, c2)
        out["wow"] = mean_delta((now[0], now[1], 1), (prev[0], prev[1], 1), c2)
    return out


def dispersion(observations, kind):
    """The measured `phi` (shares) or `c2` (per-play means), and its df.

    observations  {(subject, season, team): [obs, ...]}. For shares an obs is
    (num, den); for means it is (value, weight). Each group contributes its
    game-to-game scatter around its OWN pooled level, so a genuinely different
    player or team never inflates it - only week-to-week movement does.
    """
    chi2, df, groups = 0.0, 0, 0
    for obs in observations.values():
        obs = [o for o in obs if o[1] > 0]
        if len(obs) < DISPERSION_MIN_GAMES:
            continue
        if kind == "share":
            p = sum(o[0] for o in obs) / sum(o[1] for o in obs)
            if not 0 < p < 1:
                continue
            chi2 += sum((o[0] - o[1] * p) ** 2 / (o[1] * p * (1 - p))
                        for o in obs)
        else:
            m = sum(o[0] * o[1] for o in obs) / sum(o[1] for o in obs)
            chi2 += sum(o[1] * (o[0] - m) ** 2 for o in obs)
        df += len(obs) - 1
        groups += 1
    if df == 0:
        raise SystemExit("no group has %d appearances - the dispersion cannot "
                         "be measured, and a band without it is a guess"
                         % DISPERSION_MIN_GAMES)
    return chi2 / df, df, groups


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def market_log_ro(path=None):
    import sqlite3
    if path is None:
        return paths.market_log_ro()
    return sqlite3.connect(paths._uri(path) + "?mode=ro", uri=True, timeout=5)


class Data:
    """Every per-game observation the seven metrics read, for a season span."""

    def __init__(self, acon, mcon, season_from, season_to):
        self.season_from, self.season_to = season_from, season_to
        self.counts = {}
        self._snaps(mcon)
        self._spine(acon)
        self._redzone()

    # -- snaps ---------------------------------------------------------------
    def _snaps(self, mcon):
        xwalk = dict(mcon.execute(
            "SELECT pfr_id, gsis_id FROM player_xwalk WHERE pfr_id IS NOT NULL"))
        # Latest data_version per (player, game): corrections are re-issued as
        # new versions.
        rows = mcon.execute(
            "SELECT s.season, s.week, s.game_id, s.team, s.pfr_player_id, "
            "s.position, s.offense_snaps, s.offense_pct FROM nfl_snap_counts s "
            "JOIN (SELECT pfr_player_id, game_id, MAX(data_version) AS v "
            "      FROM nfl_snap_counts WHERE season BETWEEN ? AND ? "
            "      GROUP BY pfr_player_id, game_id) l "
            "ON s.pfr_player_id = l.pfr_player_id AND s.game_id = l.game_id "
            "AND s.data_version = l.v",
            (self.season_from, self.season_to)).fetchall()
        if not rows:
            raise SystemExit("no nfl_snap_counts rows for %d-%d - nothing to "
                             "compare" % (self.season_from, self.season_to))
        games, top = {}, {}
        self.present, self.snaps, self.position = {}, {}, {}
        unmapped = set()
        for season, week, game, team, pfr, pos, snaps, pct in rows:
            team = TEAM_ALIAS.get(team, team)
            games.setdefault(team, set()).add((season, week, game))
            snaps = snaps or 0
            # TEAM SNAPS are the busiest player's snaps over his share. Every
            # team-game 2013-2026 but 23 has a player at exactly 100%, and the
            # busiest player's share carries the least rounding either way.
            if pct and snaps and snaps > top.get((game, team), (0, 1))[0]:
                top[(game, team)] = (snaps, pct)
            grp = POSITIONS.get((pos or "").split("/")[0].strip().upper())
            if grp is None:
                continue
            gsis = xwalk.get(pfr)
            if gsis is None:
                unmapped.add((pfr, season))
                continue
            self.present.setdefault((game, team), set()).add(gsis)
            self.snaps[(game, gsis)] = snaps
            self.position[gsis] = grp
        self.schedule = {t: sorted(g, key=lambda x: (x[0], x[1]))
                         for t, g in games.items()}
        self.team_snaps = {k: int(round(s / p)) for k, (s, p) in top.items()}
        self.counts["snap_rows"] = len(rows)
        self.counts["skill_unmapped_player_seasons"] = len(unmapped)
        self.counts["team_games"] = len(self.team_snaps)
        self.counts["team_games_below_min"] = sum(
            1 for v in self.team_snaps.values() if v < MIN_TEAM_SNAPS)

    def team_ok(self, game, team):
        return self.team_snaps.get((game, team), 0) >= MIN_TEAM_SNAPS

    # -- spine: targets, carries, down buckets, pace -------------------------
    def _spine(self, acon):
        self.usage, self.team_usage = {}, {}
        self.touch, self.team_touch = {}, {}
        self.targets_by_team = {}
        for game, team, pid, bucket, tgt, car in acon.execute(
                "SELECT game_id, team, player_id, down_bucket, SUM(is_target), "
                "SUM(is_carry) FROM f_play_usage WHERE season BETWEEN ? AND ? "
                "AND role IN ('rusher','receiver') "
                "GROUP BY game_id, team, player_id, down_bucket",
                (self.season_from, self.season_to)):
            tgt, car = tgt or 0, car or 0
            u = self.usage.setdefault((game, pid), [0, 0])
            u[0] += tgt
            u[1] += car
            t = self.team_usage.setdefault((game, team), [0, 0])
            t[0] += tgt
            t[1] += car
            if tgt:
                k = (game, team)
                self.targets_by_team.setdefault(k, {})
                self.targets_by_team[k][pid] = (
                    self.targets_by_team[k].get(pid, 0) + tgt)
            if bucket in ROLE_BUCKETS:
                self.touch[(game, pid, bucket)] = (
                    self.touch.get((game, pid, bucket), 0) + tgt + car)
                self.team_touch[(game, team, bucket)] = (
                    self.team_touch.get((game, team, bucket), 0) + tgt + car)
        self.pace = {}
        for game, team, seconds, timed in acon.execute(
                "SELECT game_id, team, seconds, timed_plays FROM f_team_game_pace "
                "WHERE situation='neutral' AND season BETWEEN ? AND ?",
                (self.season_from, self.season_to)):
            if seconds is not None and timed:
                self.pace[(game, team)] = (seconds / timed, timed)
        self.counts["spine_team_games"] = len(self.team_usage)

    # -- red zone: the a-15 definition, read from the mirror -----------------
    def _redzone(self):
        import polars as pl
        from jobs.ingest_nflverse import pbp_looks
        self.rz, self.team_rz = {}, {}
        cols = ["season", "week", "season_type", "game_id", "posteam",
                "play_type", "two_point_attempt", "receiver_player_id",
                "rusher_player_id", "yardline_100"]
        files = [f for f in paths.pbp_files()
                 if self.season_from <= f[0] <= self.season_to]
        if not files:
            raise SystemExit("no play-by-play in the mirror for %d-%d"
                             % (self.season_from, self.season_to))
        pulls = []
        for season, path, pull in files:
            looks = pbp_looks(pl.read_parquet(path, columns=cols))
            pulls.append("%d:%s" % (season, pull))
            for gsis, game, team, rzt, rzc in looks.select(
                    ["gsis_id", "game_id", "team", "rz_targets",
                     "rz_carries"]).iter_rows():
                n = (rzt or 0) + (rzc or 0)
                self.rz[(game, gsis)] = n
                self.team_rz[(game, team)] = self.team_rz.get((game, team), 0) + n
        self.counts["pbp_pulls"] = pulls

    # -- per-measure observation functions -----------------------------------
    def value_fn(self, measure, bucket=None):
        if measure == "snap_share":
            def f(game, team, s):
                d = self.team_snaps.get((game, team))
                return (self.snaps.get((game, s), 0), d) if d else None
        elif measure in ("target_share", "carry_share"):
            i = 0 if measure == "target_share" else 1
            def f(game, team, s):
                t = self.team_usage.get((game, team))
                if not t or not t[i]:
                    return None
                return (self.usage.get((game, s), (0, 0))[i], t[i])
        elif measure == "redzone_look_share":
            def f(game, team, s):
                d = self.team_rz.get((game, team), 0)
                return (self.rz.get((game, s), 0), d) if d else None
        elif measure == "role_touch_share":
            def f(game, team, s):
                d = self.team_touch.get((game, team, bucket), 0)
                return (self.touch.get((game, s, bucket), 0), d) if d else None
        elif measure == "pace_seconds_per_play":
            def f(game, team, s):
                return self.pace.get((game, team))
        elif measure == "target_concentration":
            def f(game, team, s):
                by = self.targets_by_team.get((game, team))
                if not by:
                    return None
                tot = sum(by.values())
                return (sum((v / tot) ** 2 for v in by.values()), tot)
        else:
            raise KeyError(measure)
        return f

    def team_present(self):
        """{(game, team): {team}} - a team 'appears' in each of its games."""
        return {(g, t): {t} for t, games in self.schedule.items()
                for _s, _w, g in games}


def _variants(measure):
    return ROLE_BUCKETS if measure == "role_touch_share" else (None,)


def measure_dispersion(data, measure, bucket, seasons):
    """`phi` or `c2` over the given seasons, within team-season groups."""
    f = data.value_fn(measure, bucket)
    team_level = measure in TEAM_MEASURES
    present = data.team_present() if team_level else data.present
    groups = {}
    for team, games in data.schedule.items():
        for season, _w, game in games:
            if season not in seasons or not data.team_ok(game, team):
                continue
            for s in present.get((game, team), ()):
                if not team_level and data.position.get(s) is None:
                    continue
                v = f(game, team, s)
                if v is not None:
                    groups.setdefault((s, season, team), []).append(v)
    return dispersion(groups, "mean" if team_level else "share")


def compute(data, measure, seasons_out, fit_seasons):
    """([(subject, slice, Estimate)], {bucket: (disp, df, groups)})."""
    team_level = measure in TEAM_MEASURES
    present = data.team_present() if team_level else data.present
    rows, disp = [], {}
    for bucket in _variants(measure):
        d, df, groups = measure_dispersion(data, measure, bucket, fit_seasons)
        disp[bucket] = (d, df, groups)
        f = data.value_fn(measure, bucket)
        for subject, (season, week, _g), now, prev, base in comparisons(
                data.schedule, present, f, data.team_ok):
            if season not in seasons_out:
                continue
            if team_level:
                parts = team_parts(now, prev, base, d)
            else:
                parts = player_parts(now, prev, base, d)
                # A subject at zero in every level has not changed job, he
                # has no job in this measure - a QB's target share.
                levels = [parts[k].est for k in ("now", "prev", "base")
                          if k in parts]
                if not any(levels):
                    continue
            prefix = "w%02d|" % week + ("%s|" % bucket if bucket else "")
            for part, est in parts.items():
                rows.append((subject, prefix + part, est))
    return rows, disp


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

def _note(measure, disp, fit_seasons):
    word = "c2" if measure in TEAM_MEASURES else "phi"
    d = "; ".join("%s%s = %.3f over %d %s" % (
        word, " (%s)" % b if b else "", v[0], v[2],
        "team-seasons" if measure in TEAM_MEASURES else "player-seasons")
        for b, v in disp.items())
    what = ("the binomial variance inflated by phi, the measured "
            "game-to-game dispersion of this share around each player's own "
            "season level" if word == "phi" else
            "a per-play variance c2 / plays, c2 measured as the game-to-game "
            "scatter around each team's own season level")
    return ("parts per week: now, prev (the team's previous game this season, "
            "only if he appeared), base (his previous %d appearances for this "
            "team, which in weeks 1-%d reach into the previous season), wow = "
            "now - prev, vs_base = now - base. Pre-registered minimum: the team "
            "ran >= %d offensive snaps in the game (and in the previous game "
            "for wow), and the subject has %d earlier appearances for this "
            "team; below it nothing is published. A bye or a missed game is "
            "skipped, never scored as zero. Intervals: %s, measured over "
            "%d-%d (%s). n is games; rows is the denominator. Unranked: bands "
            "are the site's." % (
                BASELINE_APPEARANCES, BASELINE_APPEARANCES, MIN_TEAM_SNAPS,
                BASELINE_APPEARANCES, what, min(fit_seasons), max(fit_seasons),
                d))


def metric_for(measure, live, week, note=""):
    """`week` is `wNN`. The key is `deltas.<measure>.wNN`."""
    spec = SPECS[measure]
    team_level = measure in TEAM_MEASURES
    return metrics.Metric(
        key="deltas.%s.%s" % (measure, week),
        label="%s, %d week %d" % (spec["label"], live, int(week[1:])),
        unit=spec["unit"],
        subject_type="team" if team_level else "player", block="game",
        basis=spec["basis"], availability="current",
        shares_denominator=(None if measure == "pace_seconds_per_play"
                            else "own" if measure == "target_concentration"
                            else "team"),
        slice_kind=("down_bucket|part" if measure == "role_touch_share"
                    else "part") + " - part is now / prev / base / wow / "
                                   "vs_base",
        requires=spec["requires"],
        # The board is the LIVE season's. Earlier seasons are computed for
        # validation (--calibrate) and read for baselines, never published.
        floor_season=live,
        floor_reason="a week-to-week board of the live season, where earlier "
                     "seasons are read only as baselines,",
        note=note)


def retire(acon, keep):
    """Delete this module's OWN derived rows for weeks no longer produced.

    One key per metric per week means last season's `w17` would otherwise sit
    in the registry - and in the export - beside this season's `w02`, with a
    range saying 2025. Only `deltas.*` is in reach, and everything deleted is
    re-derivable by running `--publish` on that season: these are beliefs about
    the facts, never the facts.
    """
    gone = [m for (m,) in acon.execute(
        "SELECT metric FROM f_metrics WHERE metric LIKE 'deltas.%'")
        if m not in keep]
    for m in gone:
        acon.execute("DELETE FROM f_metric_values WHERE metric=?", (m,))
        acon.execute("DELETE FROM f_metrics WHERE metric=?", (m,))
    acon.commit()
    return gone


def publish(acon, mcon, verbose=True, measures=None):
    """Every week of the live season, one metric per measure per week.

    PER WEEK, NOT PER SEASON, because the page this feeds reads one week. Two
    weeks of 2026 in one file per measure came to 2.18 MB across the seven,
    and 18 weeks would be ~20 MB - loading a season to show a week is exactly
    the cost this module exists to remove.
    """
    live = metrics.live_season(acon)
    fit = set(range(SNAP_FIRST_SEASON, live))
    data = Data(acon, mcon, SNAP_FIRST_SEASON, live)
    if verbose:
        print("  loaded %s" % data.counts, flush=True)
    written = {}
    for measure in (measures or PLAYER_MEASURES + TEAM_MEASURES):
        rows, disp = compute(data, measure, {live}, fit)
        by_week = {}
        for subject, sl, est in rows:
            week, rest = sl.split("|", 1)
            by_week.setdefault(week, []).append((subject, rest, est))
        note = _note(measure, disp, fit)
        for week, values in sorted(by_week.items()):
            m = metric_for(measure, live, week, note)
            written[m.key] = metrics.publish(acon, m, values)
            if verbose:
                print("  %-36s %6d values, %4d subjects  dispersion %s"
                      % (m.key, written[m.key], len({v[0] for v in values}),
                         ", ".join("%s%.3f" % ((b + " ") if b else "", v[0])
                                   for b, v in disp.items())), flush=True)
    if measures is None:
        gone = retire(acon, set(written))
        if verbose:
            print("  retired %d deltas.* metrics no longer produced%s"
                  % (len(gone), (": " + ", ".join(gone)) if gone else ""),
                  flush=True)
    return written, data.counts


def calibrate(acon, mcon, holdout):
    """The band's own check, on a season it was not fitted on.

    Fit the dispersion on 2013..holdout-1, compute every delta in `holdout`,
    and report how often the 95% band excludes zero. Real role changes exist,
    so a band calibrated to week-to-week noise alone would exclude zero on
    MORE than 5% of player-weeks. Measured on 2025 it excludes zero on 0.9-5.3%
    - the bands are conservative, because `phi` absorbs genuine role changes
    as noise. The phi=1 rates printed beside them are the other side: a pure
    binomial band excludes zero on 31% of snap-share weeks, which would make
    every third player a "mover".
    """
    fit = set(range(SNAP_FIRST_SEASON, holdout))
    data = Data(acon, mcon, SNAP_FIRST_SEASON, holdout)
    out = {}
    for measure in PLAYER_MEASURES + TEAM_MEASURES:
        rows, disp = compute(data, measure, {holdout}, fit)
        for part in ("wow", "vs_base"):
            ests = [e for _s, sl, e in rows if sl.endswith("|" + part)]
            if not ests:
                continue
            excl = sum(1 for e in ests if e.lo > 0 or e.hi < 0)
            out[(measure, part)] = (len(ests), excl / len(ests),
                                    {b: round(v[0], 3) for b, v in disp.items()})
    # AND THE SAME RATE WITH phi FORCED TO 1: the pure binomial band. If the
    # measured dispersion were doing nothing the two rates would match.
    raw = {}
    for measure in PLAYER_MEASURES:
        for bucket in _variants(measure):
            f = data.value_fn(measure, bucket)
            n = excl = 0
            for _s, (season, _w, _g), now, prev, base in comparisons(
                    data.schedule, data.present, f, data.team_ok):
                if season != holdout or prev is None:
                    continue
                if not (now[0] or prev[0]):
                    continue
                e = share_delta((now[0], now[1], 1), (prev[0], prev[1], 1), 1.0)
                n += 1
                excl += e.lo > 0 or e.hi < 0
            raw[(measure, bucket)] = (n, excl / n if n else None)
    return out, raw, data.counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--calibrate", type=int, nargs="?", const=-1,
                    help="held-out band check on a completed season "
                         "(default: the one before the live season)")
    ap.add_argument("--show", help="a gsis_id or team code")
    ap.add_argument("--market-log", help="path to market_log.db, opened mode=ro")
    a = ap.parse_args(argv)
    if a.publish:
        publish(paths.connect(), market_log_ro(a.market_log))
    if a.calibrate is not None:
        acon = paths.connect(read_only=True)
        season = (metrics.live_season(acon) - 1 if a.calibrate == -1
                  else a.calibrate)
        out, raw, counts = calibrate(acon, market_log_ro(a.market_log), season)
        print("held-out season %d; %s" % (season, counts))
        for (measure, part), (n, rate, disp) in sorted(out.items()):
            print("  %-24s %-8s n=%6d  band excludes 0: %.3f   dispersion %s"
                  % (measure, part, n, rate, disp))
        for (measure, bucket), (n, rate) in sorted(raw.items(),
                                                   key=lambda kv: str(kv[0])):
            print("  %-24s %-9s wow, phi=1 (pure binomial): n=%6d  excludes 0: %s"
                  % (measure, bucket or "", n,
                     "%.3f" % rate if rate is not None else "-"))
    if a.show:
        con = paths.connect(read_only=True)
        rows = con.execute(
            "SELECT metric, slice, est, lo, hi, n, rows, method "
            "FROM f_metric_values WHERE subject_id=? AND metric LIKE 'deltas.%' "
            "ORDER BY metric, slice", (a.show,)).fetchall()
        if not rows:
            raise SystemExit("nothing published for %r" % a.show)
        for metric, sl, est, lo, hi, n, r, meth in rows:
            print("%-38s %-18s %+.3f [%+.3f, %+.3f]  n=%d rows=%s  %s"
                  % (metric, sl, est, lo, hi, n, r, meth))
    if not (a.publish or a.show or a.calibrate is not None):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
