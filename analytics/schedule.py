"""Strength of schedule, measured: what a defense does to OPPORTUNITY and to EFFICIENCY.

    python -m analytics.schedule --publish              # every season in range + rest of season
    python -m analytics.schedule --publish --season 2026
    python -m analytics.schedule --show DAL
    python -m analytics.schedule --rest KC

THE GENRE'S NUMBER. A strength-of-schedule table is almost always last season's
fantasy points allowed, ranked 1-32. That is a statement about last season's
OFFENSES as much as this season's defenses, it mixes "they are bad at stopping
receivers" with "their opponents threw more because they were ahead", and it
carries no interval - so a defense that has faced three teams is ranked with
the same confidence as one that has faced seventeen. This module replaces it
with two measurements, kept apart because the record says they behave
differently (CLAUDE.md: usage carries the signal, efficiency is noise, and
opponent defence added ~0.000 OOS R2 on top of prior usage).

EFFICIENCY - does the defense change what an opposing player does WITH his
opportunity? Per opposing player-game: production minus opportunity times the
player's OWN season rate from his OTHER games (leave-one-out). Summed over the
defense's games and divided by opportunity:

    schedule.efficiency.{wr,te}   receiving yards per target above the targeted
                                  player's own rate
    schedule.efficiency.rb        rushing yards per carry above the rusher's own

Conditioning on the player's own rate is the whole point. A defense that faced
three elite receivers allows a lot of yards and may be fine; points-allowed
charges it for its opponents.

OPPORTUNITY - does facing the defense change how much work the group gets at
all? Per opposing offense-game: the position group's targets (or carries) over
that offense's own average from its OTHER games. A defense that forces a game
script changes carry counts without being better at defending runs, which is
exactly why this is a separate number rather than folded into a rating:

    schedule.opportunity.{wr,te,rb}   group opportunity relative to the offense's
                                      own baseline; +0.10 is 10% more

Measured at the GROUP level rather than per player on purpose.
`stats_player_week` has NO ROW for a player who played and recorded nothing
(CLAUDE.md, nflverse), so a per-player opportunity residual would only ever see
the players who got some - survivorship that biases every defense upward. A
group total is unaffected: a missing row contributed zero either way.

THE INTERVAL. A Bayesian bootstrap over GAMES (Dirichlet weights, Rubin 1981):
every game in the season gets a weight, and on each draw BOTH the defense's own
games AND every baseline behind them are recomputed. Resampling only the
defense's games would treat each opposing player's baseline as known, which in
week 2 is a rate measured on ONE other game. Dirichlet rather than multinomial
because a multinomial draw leaves a defense with zero games about an eighth of
the time at n=2, and dropping those draws conditions the interval on something
the data did not do.

`n` IS THE DEFENSE'S GAMES. In week 2 that is 2, and the interval is as wide as
two games make it. That is the finding, not a defect - see the module's
research twin, `research/schedule_strength.py`, for how far an early-season
effect lands from the rest of the season, which is the only honest test of
whether any of this is worth reading.

INDEPENDENT DRAWS PER SUBJECT. Each defense's interval comes from its own draws,
seeded from its own id (CLAUDE.md, shared denominators: whatever a reader will
compare side by side must be drawn independently). The rest-of-season combination
is the other case: it is a SUM across opponents, so it must be computed inside
ONE set of draws or it loses the covariance between them, and those draws are
seeded from the offense it describes.

NO SHRINKAGE. `analytics/shrinkage.py` does not exist; the fitted `k` the brief
names lives in `research/shrinkage.py`, and it is a weight on a PLAYER's own
usage mean (`w = n/(n+k)`, fitted walk-forward on player usage). It does not
describe a defense's effect on other people's production and is not borrowed.
The published estimate is unshrunk and its interval is the whole statement.

NO RANK, NO NUMBERING, NO COMPOSITE. Nothing here orders defenses or rolls the
two measurements into one score. A page can sort a table; this module does not
publish an order, because an order is exactly what an interval spanning zero
cannot support.
"""
import argparse
import sys
import zlib

from analytics import metrics, paths
from analytics.intervals import Estimate

DRAWS = 2000
SEED = 20260924
CONF = 0.95

# group -> (opportunity column, production column, words)
GROUPS = {
    "wr": ("targets", "receiving_yards", "WR", "receiving yards per target"),
    "te": ("targets", "receiving_yards", "TE", "receiving yards per target"),
    "rb": ("carries", "rushing_yards", "RB", "rushing yards per carry"),
}

# A player-game enters the efficiency sum only if the player's OTHER games in
# the season carry at least this much opportunity. Below it the baseline is a
# rate on two or three looks, and a residual against it is noise wearing a
# defense's name. Applied once on the full sample so the population does not
# change between draws. It is a floor, not a fitted constant - and in week 2
# it is the binding reason a player-game drops out.
MIN_BASELINE = {"targets": 4, "carries": 8}

WEEKLY = "stats_player_week_{season}.parquet"


def _requires(group):
    opp, prod, _pg, _w = GROUPS[group]
    return (("weekly_stats", opp), ("weekly_stats", prod),
            ("weekly_stats", "position_group"), ("weekly_stats", "opponent_team"),
            ("weekly_stats", "game_id"))


def metric_for(measure, group, rest=False, live=None):
    opp, _prod, pg, words = GROUPS[group]
    if measure == "efficiency":
        unit = ("%s allowed to opposing %ss, above each targeted player's own "
                "rate from his other games that season (0 = what they do "
                "against everyone else)" % (words, pg))
        if group == "rb":
            unit = unit.replace("targeted player", "rusher")
    else:
        unit = ("opposing %s %s relative to that offense's own per-game average "
                "from its other games that season (+0.10 = 10%% more)"
                % (pg, opp))
    if rest:
        return metrics.Metric(
            key="schedule.rest_of_season.%s.%s" % (measure, group),
            label="Rest-of-season schedule, %s %s" % (pg, measure),
            unit=("the mean, over an offense's remaining games, of each "
                  "remaining opponent's measured effect: " + unit),
            subject_type="team", block="game", basis="weekly_stats",
            availability="current",
            slice_kind=("remaining_game: 'wWW_OPP' is one remaining game and "
                        "its opponent's effect; '' is the mean over all of them"),
            requires=_requires(group),
            # The envelope may not contradict its values: a rest-of-season
            # schedule exists only for the season in progress.
            floor_season=live,
            floor_reason="a remaining schedule exists only for the season in "
                         "progress")
    return metrics.Metric(
        key="schedule.%s.%s" % (measure, group),
        label="Opponent %s effect on %ss" % (measure, pg),
        unit=unit, subject_type="team", block="game", basis="weekly_stats",
        availability="current", slice_kind="season", requires=_requires(group))


# ---------------------------------------------------------------------------
# the facts, one season at a time
# ---------------------------------------------------------------------------

def load_season(season, max_week=None, min_week=None, path=None):
    """Regular-season player-weeks for one season, as a polars DataFrame."""
    import polars as pl
    if path is None:
        found = {s: p for s, p, _d in paths.seasonal_files(WEEKLY)}
        if season not in found:
            raise SystemExit("no %s in the mirror" % WEEKLY.format(season=season))
        path = found[season]
    df = (pl.read_parquet(path, columns=[
            "season", "week", "season_type", "game_id", "player_id", "team",
            "opponent_team", "position_group", "targets", "carries",
            "receiving_yards", "rushing_yards"])
          .filter(pl.col("season_type") == "REG")
          .filter(pl.col("game_id").is_not_null()
                  & pl.col("team").is_not_null()
                  & pl.col("opponent_team").is_not_null()))
    if max_week is not None:
        df = df.filter(pl.col("week") <= max_week)
    if min_week is not None:
        df = df.filter(pl.col("week") >= min_week)
    return df


class Season:
    """Matrices for one season and one group, ready to be reweighted.

    Games are the columns of every weight matrix. Everything the estimator
    needs is a weighted sum over games, so a draw is one matrix product.
    """

    def __init__(self, df, group):
        import numpy as np
        import polars as pl
        opp_col, prod_col, pg, _w = GROUPS[group]
        self.group = group
        games = sorted(df["game_id"].unique().to_list())
        gi = {g: i for i, g in enumerate(games)}
        self.games, self.N = games, len(games)
        teams = sorted(set(df["team"].unique().to_list())
                       | set(df["opponent_team"].unique().to_list()))
        self.teams = teams
        ti = {t: i for i, t in enumerate(teams)}

        # --- efficiency: opposing player-games with opportunity ------------
        g = (df.filter((pl.col("position_group") == pg)
                       & (pl.col(opp_col).fill_null(0) > 0))
             .select("player_id", "game_id", "opponent_team",
                     pl.col(opp_col).fill_null(0).cast(pl.Float64).alias("o"),
                     pl.col(prod_col).fill_null(0).cast(pl.Float64).alias("y")))
        players = sorted(g["player_id"].unique().to_list())
        pi = {p: i for i, p in enumerate(players)}
        P = len(players)
        Mo = np.zeros((self.N, max(P, 1)))
        My = np.zeros((self.N, max(P, 1)))
        rp, rg, rd, ro, ry = [], [], [], [], []
        for pid, gid, dteam, o, y in g.iter_rows():
            p, k = pi[pid], gi[gid]
            Mo[k, p] += o
            My[k, p] += y
            rp.append(p); rg.append(k); rd.append(ti[dteam]); ro.append(o); ry.append(y)
        self.Mo, self.My = Mo, My
        rp, rg, rd = np.array(rp, int), np.array(rg, int), np.array(rd, int)
        ro, ry = np.array(ro), np.array(ry)
        # The baseline floor, on the full sample, once.
        other = Mo.sum(axis=0)[rp] - ro if len(rp) else ro
        keep = other >= MIN_BASELINE[opp_col]
        self.e_p, self.e_g, self.e_d = rp[keep], rg[keep], rd[keep]
        self.e_o, self.e_y = ro[keep], ry[keep]
        self.e_dropped = int((~keep).sum())

        # --- opportunity: offense-group per team-game ----------------------
        tg = (df.group_by("game_id", "team", "opponent_team")
              .agg(pl.when(pl.col("position_group") == pg)
                   .then(pl.col(opp_col).fill_null(0)).otherwise(0)
                   .sum().cast(pl.Float64).alias("o")))
        T = len(teams)
        Mt = np.zeros((self.N, T))
        It = np.zeros((self.N, T))
        tt, tgm, td, to = [], [], [], []
        for gid, team, dteam, o in tg.iter_rows():
            k, t = gi[gid], ti[team]
            Mt[k, t] += o
            It[k, t] = 1.0
            tt.append(t); tgm.append(k); td.append(ti[dteam]); to.append(o)
        self.Mt, self.It = Mt, It
        self.t_t, self.t_g = np.array(tt, int), np.array(tgm, int)
        self.t_d, self.t_o = np.array(td, int), np.array(to)
        # An offense with no other game has no baseline. In week 1 that is
        # every offense, which is why nothing publishes off one week.
        games_played = It.sum(axis=0)
        self.t_keep = games_played[self.t_t] >= 2

    # -- the two estimators, for a (D x N) weight matrix -------------------

    def efficiency(self, W):
        """(D x T) yards per opportunity above baseline, per defense."""
        import numpy as np
        O = W @ self.Mo                      # D x P weighted opportunity
        Y = W @ self.My
        w = W[:, self.e_g]                   # D x R weight of each row's game
        base_o = O[:, self.e_p] - w * self.e_o
        base_y = Y[:, self.e_p] - w * self.e_y
        with np.errstate(invalid="ignore", divide="ignore"):
            rate = np.where(base_o > 0, base_y / base_o, np.nan)
        resid = self.e_y - self.e_o * rate   # D x R
        ok = ~np.isnan(resid)
        num = np.zeros((W.shape[0], len(self.teams)))
        den = np.zeros_like(num)
        wr = np.where(ok, w * resid, 0.0)
        wo = np.where(ok, w * self.e_o, 0.0)
        for d in np.unique(self.e_d):
            m = self.e_d == d
            num[:, d] = wr[:, m].sum(axis=1)
            den[:, d] = wo[:, m].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / den, np.nan)

    def opportunity(self, W):
        """(D x T) group opportunity over the offense's own baseline, minus 1."""
        import numpy as np
        S = W @ self.Mt                      # D x T weighted opportunity sum
        C = W @ self.It                      # D x T weighted games
        tk = self.t_keep
        t, g, d, o = self.t_t[tk], self.t_g[tk], self.t_d[tk], self.t_o[tk]
        w = W[:, g]
        with np.errstate(invalid="ignore", divide="ignore"):
            base = (S[:, t] - w * o) / (C[:, t] - w)
        num = np.zeros((W.shape[0], len(self.teams)))
        den = np.zeros_like(num)
        wo, wb = w * o, w * base
        for dd in np.unique(d):
            m = d == dd
            num[:, dd] = wo[:, m].sum(axis=1)
            den[:, dd] = wb[:, m].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / den - 1.0, np.nan)

    def games_faced(self, measure):
        """{defense index: (distinct games, rows)} that enter the estimate."""
        import numpy as np
        if measure == "efficiency":
            d, g = self.e_d, self.e_g
        else:
            d, g = self.t_d[self.t_keep], self.t_g[self.t_keep]
        out = {}
        for dd in np.unique(d):
            m = d == dd
            out[int(dd)] = (len(np.unique(g[m])), int(m.sum()))
        return out


def weights(N, draws, subject, seed=SEED):
    """(draws x N) Dirichlet(1,...,1) weights scaled to sum to N.

    Seeded from the subject with crc32, never `hash()` - str hashing is salted
    per process and the intervals would not reproduce (same rule as
    `analytics.intervals._counts_matrix`).
    """
    import numpy as np
    rng = np.random.default_rng([seed, N, zlib.crc32(str(subject).encode())])
    g = rng.standard_exponential((draws, N))
    return g / g.sum(axis=1, keepdims=True) * N


def scale(n):
    """How much wider than the raw bootstrap a small-n interval must be.

    A bootstrap over n games cannot see more spread than those n games show:
    at n=2 the Dirichlet interval is roughly the span between the two games,
    and two games that happen to agree give a tight interval around nothing.
    Measured on 2026 week 2 before this existed: LAC's WR efficiency read
    +6.1 [+5.5, +6.5] yards per target off two games.

    Two textbook corrections, both about n and neither fitted:
      - the Bayesian bootstrap variance of a mean is the plug-in variance over
        (n+1), where the unbiased one is over (n-1): sqrt((n+1)/(n-1));
      - a 95% interval on an SE estimated from n units uses t(n-1), not 1.96.
    At n=2 the product is 11.2; at n=17 it is 1.15. `research/schedule_strength.py`
    measures whether the corrected intervals are calibrated - that measurement,
    not this docstring, is what licenses using them.
    """
    import math
    from analytics.intervals import _T975, _Z
    if n < 2:
        return float("inf")
    t = _T975[n - 1] if n - 1 < len(_T975) else _Z[0.95]
    return t / _Z[0.95] * math.sqrt((n + 1) / (n - 1))


def _rescaled(point, reps, n, method):
    if method == "raw":
        return reps
    return point + (reps - point) * scale(n)


def _interval(point, reps, n, rows, method="t"):
    import numpy as np
    tag = "bayes%d%s" % (DRAWS, "t" if method == "t" else "")
    if point is not None and np.isnan(point):
        point = None        # nothing measured: "not measurable", never a NaN
    if n < 2:
        return Estimate(point if point is None else float(point),
                        float("-inf"), float("inf"), n, tag, rows)
    r = reps[~np.isnan(reps)]
    if point is None or np.isnan(point) or len(r) < len(reps) // 2:
        return Estimate(None if point is None or np.isnan(point) else float(point),
                        float("-inf"), float("inf"), n, tag, rows)
    p = float(point)
    r = _rescaled(p, r, n, method)
    lo, hi = np.quantile(r, [(1 - CONF) / 2, (1 + CONF) / 2])
    return Estimate(p, float(min(lo, p)), float(max(hi, p)), n, tag, rows)


def defense_effects(season_obj, measure, draws=DRAWS, tag="", method="t"):
    """{defense team: Estimate}, each from its OWN draws."""
    import numpy as np
    fn = getattr(season_obj, measure)
    point = fn(np.ones((1, season_obj.N)))[0]
    out = {}
    for d, (n, rows) in season_obj.games_faced(measure).items():
        team = season_obj.teams[d]
        W = weights(season_obj.N, draws, "%s|%s|%s|%s" % (
            measure, season_obj.group, team, tag))
        out[team] = _interval(point[d], fn(W)[:, d], n, rows, method)
    return out


# ---------------------------------------------------------------------------
# rest of season
# ---------------------------------------------------------------------------

def remaining_schedule(season):
    """{team: [(week, opponent)]} for regular-season games not yet played.

    "Not yet played" is `result` null on the newest `games.parquet`. A game
    that has been played but whose stats have not landed would be counted as
    remaining AND be missing from the effects, so the caller checks that every
    played week is in the stats.
    """
    import polars as pl
    found = paths.latest_asset("games.parquet")
    if not found:
        raise SystemExit("no games.parquet in the mirror")
    g = (pl.read_parquet(found[0])
         .filter((pl.col("season") == season) & (pl.col("game_type") == "REG")))
    left = g.filter(pl.col("result").is_null())
    out = {}
    for week, away, home in left.select("week", "away_team", "home_team").iter_rows():
        out.setdefault(away, []).append((int(week), home))
        out.setdefault(home, []).append((int(week), away))
    played = g.filter(pl.col("result").is_not_null())["game_id"].to_list()
    return {k: sorted(v) for k, v in out.items()}, set(played), found[1]


def rest_of_season(season_obj, measure, schedule, draws=DRAWS):
    """[(offense, slice, Estimate)]: each remaining game's opponent effect, and
    their mean, all out of ONE set of draws per offense so the mean carries the
    covariance between opponents (they share baselines)."""
    import numpy as np
    fn = getattr(season_obj, measure)
    faced = season_obj.games_faced(measure)
    ti = {t: i for i, t in enumerate(season_obj.teams)}
    point = fn(np.ones((1, season_obj.N)))[0]
    out = []
    for offense, games in sorted(schedule.items()):
        idx = [ti.get(opp) for _w, opp in games]
        if any(i is None for i in idx):
            raise AssertionError("%s's remaining opponents %s are not all in "
                                 "the season's stats" % (offense, games))
        W = weights(season_obj.N, draws, "rest|%s|%s|%s" % (
            measure, season_obj.group, offense))
        reps = fn(W)
        idx = np.array(idx)
        nm = [faced.get(int(i), (0, 0)) for i in idx]
        for (week, opp), i, (n, rows) in zip(games, idx, nm):
            out.append((offense, "w%02d_%s" % (week, opp),
                        _interval(point[i], reps[:, i], n, rows)))
        # EACH OPPONENT IS WIDENED BY ITS OWN n BEFORE THE MEAN, inside the same
        # draws - so the combination keeps the covariance between opponents and
        # carries every component's small-sample correction through. Widening
        # the mean afterwards would need one n for fifteen opponents, and there
        # is no such number.
        wide = np.column_stack([
            _rescaled(point[i], reps[:, i], faced.get(int(i), (0, 0))[0], "t")
            if faced.get(int(i), (0, 0))[0] >= 2 else reps[:, i] for i in idx])
        # The mean over remaining games. n is the distinct opponent-games it
        # was measured on - the games that actually inform it.
        pt = float(np.mean(point[idx]))     # NaN if any opponent is unmeasured
        comb = wide.mean(axis=1)
        n_all = sum(faced.get(int(i), (0, 0))[0] for i in np.unique(idx))
        rows_all = sum(faced.get(int(i), (0, 0))[1] for i in np.unique(idx))
        if any(faced.get(int(i), (0, 0))[0] < 2 for i in idx):
            e = Estimate(pt if not np.isnan(pt) else None, float("-inf"),
                         float("inf"), max(n_all, 1), "bayes%d" % draws, rows_all)
        else:
            # Already widened per opponent: the mean's own quantiles, raw.
            e = _interval(pt, comb, n_all, rows_all, "raw")
            e = Estimate(e.est, e.lo, e.hi, e.n, "bayes%dt" % draws, e.rows)
        out.append((offense, "", e))
    return out


def verdict(e):
    """The sentence a page can print, computed from the interval.

    Falsifiable by construction: all three answers are reachable, and
    `tests/test_analytics_schedule.py` drives it to each.
    """
    if e.est is None or e.lo == float("-inf"):
        return "not measurable yet"
    if e.lo > 0:
        return "measurably easier"
    if e.hi < 0:
        return "measurably harder"
    return "not measurably easier or harder"


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

def publish(con, seasons=None, verbose=True):
    import polars as pl
    live = metrics.live_season(con)
    written = {}
    files = {s: p for s, p, _d in paths.seasonal_files(WEEKLY)}
    for measure in ("efficiency", "opportunity"):
        for group in GROUPS:
            m = metric_for(measure, group)
            lo, hi, note = metrics.derive_range(con, m)
            want = [s for s in range(lo, hi + 1) if s in files
                    and (not seasons or s in seasons)]
            rows = []
            for s in want:
                so = Season(load_season(s, path=files[s]), group)
                for team, e in sorted(defense_effects(so, measure).items()):
                    rows.append((team, str(s), e))
            if seasons:
                # A partial run must not erase the seasons it did not compute:
                # `metrics.publish` replaces the metric wholesale.
                keep = [(sid, sl, Estimate(est, a, b, n, meth, r))
                        for sid, sl, est, a, b, n, r, meth in con.execute(
                            "SELECT subject_id, slice, est, lo, hi, n, rows, "
                            "method FROM f_metric_values WHERE metric=?",
                            (m.key,))
                        if int(sl) not in seasons]
                rows = keep + rows
            written[m.key] = metrics.publish(con, m, rows, lo, hi)
            if verbose:
                print("  %-36s %d-%d  %d values   [%s]"
                      % (m.key, lo, hi, written[m.key], note), flush=True)

    # Rest of season: the live season only.
    sched, played, pull = remaining_schedule(live)
    df = load_season(live, path=files[live])
    in_stats = set(df["game_id"].unique().to_list())
    missing = played - in_stats
    if missing:
        raise SystemExit(
            "%d played %d games have no stats rows yet (%s...). Their opponents "
            "would read as remaining-and-unmeasured; refusing rather than "
            "publishing a schedule that silently skips them."
            % (len(missing), live, sorted(missing)[:3]))
    for measure in ("efficiency", "opportunity"):
        for group in GROUPS:
            m = metric_for(measure, group, rest=True, live=live)
            lo, hi, note = metrics.derive_range(con, m)
            rows = rest_of_season(Season(df, group), measure, sched)
            written[m.key] = metrics.publish(con, m, rows, lo, hi)
            if verbose:
                from collections import Counter
                comb = [e for _t, sl, e in rows if sl == ""]
                # EVERY verdict, counted - "0 span zero" once hid that all 32
                # were unmeasurable, which is a different statement entirely.
                tally = Counter(verdict(e) for e in comb)
                print("  %-36s %d  %d values, %d offenses: %s (schedule pull %s)"
                      % (m.key, live, written[m.key], len(comb),
                         ", ".join("%d %s" % (v, k) for k, v in
                                   sorted(tally.items())), pull), flush=True)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--season", type=int, action="append")
    ap.add_argument("--show", help="a defense, e.g. DAL")
    ap.add_argument("--rest", help="an offense, e.g. KC")
    a = ap.parse_args(argv)
    if a.publish:
        publish(paths.connect(), seasons=set(a.season) if a.season else None)
        return 0
    con = paths.connect(read_only=True)
    if a.show:
        rows = con.execute(
            "SELECT metric, slice, est, lo, hi, n, rows FROM f_metric_values "
            "WHERE subject_id=? AND metric LIKE 'schedule.%' AND metric NOT "
            "LIKE 'schedule.rest_of_season.%' ORDER BY metric, slice DESC",
            (a.show,)).fetchall()
        if not rows:
            raise SystemExit("nothing published for %r" % a.show)
        for metric, sl, est, lo, hi, n, r in rows[:40]:
            print("%-30s %s %+.3f [%+.3f, %+.3f]  n=%d games, %s rows"
                  % (metric, sl, est, lo, hi, n, r))
        return 0
    if a.rest:
        rows = con.execute(
            "SELECT metric, slice, est, lo, hi, n, rows, method FROM "
            "f_metric_values WHERE subject_id=? AND metric LIKE "
            "'schedule.rest_of_season.%' ORDER BY metric, slice", (a.rest,)).fetchall()
        if not rows:
            raise SystemExit("nothing published for %r" % a.rest)
        for metric, sl, est, lo, hi, n, r, meth in rows:
            e = Estimate(est, lo, hi, n, meth, r)
            print("%-44s %-8s %+.3f [%+.3f, %+.3f]  n=%d  %s"
                  % (metric, sl or "ALL", est, lo, hi, n,
                     verdict(e) if not sl else ""))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
