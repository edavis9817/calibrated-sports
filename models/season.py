"""The season model, phase 1: chance to win the division (a-41).

A rating, a game-level distribution, a Monte Carlo over the real remaining
schedule, and the NFL's division tiebreakers - and nothing cleverer, because a
simple model that can be scored is worth more here than a good one that cannot.

THE RATING IS MARGIN-OF-VICTORY ELO. Chosen over an opponent-adjusted points
model for three reasons, all about scoring rather than accuracy:
  * it is sequential by construction - the rating before a game is a function
    of earlier games only - so a walk-forward needs no refitting machinery to
    stay as-of, and a leak would have to be written on purpose;
  * it has three constants (K, home advantage, preseason regression), few
    enough to refit per season on EARLIER seasons only by a plain grid search;
  * it reads scores alone. Closing spreads exist on the schedule, but a model
    that reads the market is a different claim (and brief 023's lesson is that
    this project's own forecasts lose to the market); phase 1 answers what the
    RESULTS say.
The margin multiplier is FiveThirtyEight's published one,
ln(|margin| + 1) x 2.2 / (0.001 x elo_diff_winner + 2.2), which damps the
autocorrelation of blowouts by favourites. Every team regresses toward 1500 by
`regress` at each season start. Expansion (HOU 2002) enters at 1500.

THE GAME DISTRIBUTION is Bernoulli: P(home wins) = 1 / (1 + 10^(-(d + hfa)/400))
with d the rating difference. Ratings are HELD FIXED inside a simulation (they
are not updated by simulated results), which understates uncertainty in a long
remaining schedule; ties are never simulated (15 REG ties in 6,967 games since
1999); every regular-season game is treated as having a home side, including
neutral-site international games. All three are disclosed on the file.

TIEBREAKERS, division format, in the NFL's order:
  1 head-to-head, 2 division record, 3 common games, 4 conference record,
  5 strength of victory, 6 strength of schedule
are IMPLEMENTED. Every step after 6 (ranks in points scored and allowed, net
points in common games, net points, net touchdowns) is NOT implemented, and a
tie that survives step 6 is resolved by a uniform draw among the survivors -
simulated games carry no points to rank on. When a step eliminates a club from
a 3- or 4-club tie the procedure restarts at step 1 with the survivors, as the
rule book specifies for both the 2-club and 3-club formats.
Ties in actual results count half a win, as the league counts them.

THE VERDICT RULE, fixed before the walk-forward was run (a-41):
  Score = multi-category Brier per division per state, sum over the four teams
  of (p - won)^2, range 0-2. States are "after week k" for k = 0 (preseason)
  through the week before the last. Two baselines, both from the standings:
    standings_leader   - the current leader(s) on win percentage share 1.0
                         equally; everyone else 0. The standings carried
                         forward, literally.
    standings_coinflip - the same simulator and the same tiebreakers with every
                         remaining game a coin flip. The standings carried
                         forward with honest uncertainty; the model's only
                         advantage over it is the rating.
  model - baseline is bootstrapped over division-seasons (the block: one
  division's forecasts from week to week are one race, not seventeen). The
  model BEATS the standings only if the 95% interval of model - baseline lies
  below zero against BOTH baselines. If it covers zero against either, the
  file says "no better than the standings" and the Teams page should print the
  naive baseline. No constant is tuned after this rule is written.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MEAN = 1500.0
MOV_A = 2.2

# A franchise keeps one rating across a move. The division map is keyed on the
# published abbreviation and holds both halves (STL and LA are both NFC West).
FRANCHISE = {"STL": "LA", "SD": "LAC", "OAK": "LV"}

IMPLEMENTED_TIEBREAKERS = [
    "head_to_head", "division_record", "common_games", "conference_record",
    "strength_of_victory", "strength_of_schedule",
]
NOT_IMPLEMENTED_TIEBREAKERS = [
    "conference_points_rank", "all_games_points_rank",
    "net_points_common_games", "net_points_all_games",
    "net_touchdowns_all_games",
]
RESIDUAL_TIEBREAK = "uniform draw among clubs still tied after strength of schedule"


def franchise(team: str) -> str:
    return FRANCHISE.get(team, team)


@dataclass(frozen=True)
class EloParams:
    k: float
    hfa: float
    regress: float

    def as_dict(self):
        return {"k": self.k, "hfa": self.hfa, "regress": self.regress}


def win_prob(diff):
    """P(home wins) given home rating minus away rating PLUS home advantage."""
    return 1.0 / (1.0 + 10.0 ** (-np.asarray(diff, dtype=float) / 400.0))


def run_elo(games, params: EloParams, snapshot_at=None):
    """Walk every scored game in (season, week, kickoff) order.

    `games` is a list of dicts with game_id, season, week, kickoff_ts, home,
    away, home_score, away_score (scores None for unplayed games, which are
    skipped). Ordering is by the SCHEDULED week, so a snapshot "after week k"
    holds exactly the games the standings after week k hold, including a game
    postponed past its week. Returns (pre, snaps):
      pre   - [(index into `games`, p_home)] for every scored game, computed
              BEFORE that game updated anything;
      snaps - {(season, k): {franchise: rating}} for every requested key: the
              ratings after every game of that season with week <= k, after
              the season-start regression (k = 0 is the preseason).
    """
    want = sorted(set(snapshot_at or ()))
    r: dict[str, float] = {}
    pre, snaps = [], {}
    order = sorted((i for i, g in enumerate(games)
                    if g["home_score"] is not None and g["away_score"] is not None),
                   key=lambda i: (games[i]["season"], games[i]["week"],
                                  games[i]["kickoff_ts"] or 0.0, games[i]["game_id"]))
    season = None

    def regress():
        for t in list(r):
            r[t] = MEAN + (1.0 - params.regress) * (r[t] - MEAN)

    def take(before):
        # every wanted snapshot that sorts before `before` = (season, week)
        while want and want[0] < before:
            snaps[want.pop(0)] = dict(r)

    for i in order:
        g = games[i]
        s, w = g["season"], g["week"]
        if s != season:
            take((s, -1))
            regress()
            season = s
        take((s, w))
        h, a = franchise(g["home"]), franchise(g["away"])
        rh, ra = r.setdefault(h, MEAN), r.setdefault(a, MEAN)
        diff = rh - ra + params.hfa
        p = float(win_prob(diff))
        pre.append((i, p))
        margin = g["home_score"] - g["away_score"]
        result = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
        if margin == 0:
            mult = 1.0
        else:
            wdiff = diff if margin > 0 else -diff
            mult = math.log(abs(margin) + 1.0) * MOV_A / (0.001 * wdiff + MOV_A)
        delta = params.k * mult * (result - p)
        r[h] = rh + delta
        r[a] = ra - delta
    # snapshots past the last scored game: same season -> as it stands; a
    # later season -> regressed once, as its preseason would be
    for key in want:
        if key[0] == season:
            snaps[key] = dict(r)
        else:
            snaps[key] = {t: MEAN + (1.0 - params.regress) * (v - MEAN)
                          for t, v in r.items()}
    return pre, snaps


def log_loss(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


# ---------------------------------------------------------------- the season

@dataclass
class Season:
    """One regular season's schedule and division structure.

    teams      - published abbreviations appearing in this season's REG games
    division   - {team: "AFC East"}
    conference - {team: "AFC"}
    games      - REG games as (home, away, week, result) with result 1/0/0.5 or
                 None when unplayed at the state being simulated
    """
    teams: list
    division: dict
    conference: dict
    games: list

    def __post_init__(self):
        self.idx = {t: i for i, t in enumerate(self.teams)}
        self.T = len(self.teams)
        self.G = len(self.games)
        self.home = np.array([self.idx[g[0]] for g in self.games], dtype=int)
        self.away = np.array([self.idx[g[1]] for g in self.games], dtype=int)
        self.div_game = np.array([self.division[g[0]] == self.division[g[1]]
                                  for g in self.games])
        self.conf_game = np.array([self.conference[g[0]] == self.conference[g[1]]
                                   for g in self.games])
        H = np.zeros((self.G, self.T))
        A = np.zeros((self.G, self.T))
        H[np.arange(self.G), self.home] = 1
        A[np.arange(self.G), self.away] = 1
        self.H, self.A = H, A
        self.games_played_by = (H + A).sum(axis=0)
        self.divisions = {}
        for t in self.teams:
            self.divisions.setdefault(self.division[t], []).append(t)
        for d in self.divisions:
            self.divisions[d].sort()
        self.opponents = {t: set() for t in self.teams}
        for g in self.games:
            self.opponents[g[0]].add(g[1])
            self.opponents[g[1]].add(g[0])


def simulate(season: Season, p_home, n: int, rng: np.random.Generator):
    """(n x G) home-result matrix: fixed results where played, draws elsewhere.

    `p_home` is a length-G array of P(home wins) for every game; it is read
    only where the result is None.
    """
    fixed = np.array([np.nan if g[3] is None else g[3] for g in season.games])
    out = np.empty((n, season.G))
    played = ~np.isnan(fixed)
    out[:, played] = fixed[played]
    open_ = ~played
    if open_.any():
        u = rng.random((n, int(open_.sum())))
        out[:, open_] = (u < np.asarray(p_home)[open_]).astype(float)
    return out


def _pct(w, g):
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(g > 0, w / np.where(g > 0, g, 1), 0.0)


class Tables:
    """Every per-sim quantity a division tiebreak reads, computed once."""

    def __init__(self, season: Season, R):
        self.s = season
        self.R = R                                   # n x G, home result
        L = 1.0 - R
        self.W = R @ season.H + L @ season.A         # n x T wins (ties half)
        self.g = season.games_played_by              # T
        self.pct = self.W / np.maximum(self.g, 1)
        dm = season.div_game.astype(float)
        cm = season.conf_game.astype(float)
        self.divW = (R * dm) @ season.H + (L * dm) @ season.A
        self.divG = (season.H + season.A).T @ dm
        self.confW = (R * cm) @ season.H + (L * cm) @ season.A
        self.confG = (season.H + season.A).T @ cm
        # strength of victory / schedule: combined record of opponents
        Wh = self.W[:, season.home]                  # n x G, home team's wins
        Wa = self.W[:, season.away]
        gh = self.g[season.home]
        ga = self.g[season.away]
        # beaten opponents: home beat away with weight R (ties half), etc.
        sov_w = (R * Wa) @ season.H + (L * Wh) @ season.A
        sov_g = (R * ga) @ season.H + (L * gh) @ season.A
        self.sov = _pct(sov_w, sov_g)
        sos_w = Wa @ season.H + Wh @ season.A
        sos_g = (np.ones_like(R) * ga) @ season.H + (np.ones_like(R) * gh) @ season.A
        self.sos = _pct(sos_w, sos_g)
        self._h2h = {}
        self._common = {}

    def among(self, subset):
        """Per-sim win pct of each club in `subset` in games among the subset."""
        key = tuple(subset)
        if key not in self._h2h:
            s = self.s
            ids = [s.idx[t] for t in subset]
            sel = np.isin(s.home, ids) & np.isin(s.away, ids)
            self._h2h[key] = self._record_in(sel, ids)
        return self._h2h[key]

    def common(self, subset):
        """Per-sim win pct of each club in games against common opponents."""
        key = tuple(subset)
        if key not in self._common:
            s = self.s
            ids = [s.idx[t] for t in subset]
            com = set.intersection(*(s.opponents[t] for t in subset)) - set(subset)
            cids = [s.idx[t] for t in com]
            sel = ((np.isin(s.home, ids) & np.isin(s.away, cids))
                   | (np.isin(s.away, ids) & np.isin(s.home, cids)))
            self._common[key] = self._record_in(sel, ids)
        return self._common[key]

    def _record_in(self, sel, ids):
        s = self.s
        R = self.R[:, sel]
        H = s.H[sel][:, ids]
        A = s.A[sel][:, ids]
        w = R @ H + (1.0 - R) @ A
        g = (H + A).sum(axis=0)
        return _pct(w, g)


def _step_values(tab: Tables, subset, step, rows):
    ids = [tab.s.idx[t] for t in subset]
    if step == "head_to_head":
        return tab.among(subset)[rows]
    if step == "division_record":
        return _pct(tab.divW[rows][:, ids], tab.divG[ids])
    if step == "common_games":
        return tab.common(subset)[rows]
    if step == "conference_record":
        return _pct(tab.confW[rows][:, ids], tab.confG[ids])
    if step == "strength_of_victory":
        return tab.sov[rows][:, ids]
    if step == "strength_of_schedule":
        return tab.sos[rows][:, ids]
    raise ValueError(step)


def resolve(tab: Tables, subset, rows, rng, stats=None, step0=0):
    """Winner (team abbreviation) of a division tie for each sim in `rows`.

    `subset` is the tied clubs (sorted), `rows` an index array of sims in which
    exactly these clubs are tied. Vectorised over sims: at each step the clubs
    at the maximum survive; sims whose tie SHRANK restart at step 1 with the
    survivors, sims whose tie did not shrink move to the next step.
    `stats`, if given, counts the step that decided each sim.
    """
    out = np.empty(len(rows), dtype=object)
    if len(subset) == 1:
        out[:] = subset[0]
        return out
    if len(rows) == 0:
        return out
    active = np.arange(len(rows))
    step = step0
    while len(active) and step < len(IMPLEMENTED_TIEBREAKERS):
        name = IMPLEMENTED_TIEBREAKERS[step]
        v = _step_values(tab, list(subset), name, rows[active])
        best = np.isclose(v, v.max(axis=1, keepdims=True), rtol=0, atol=1e-9)
        n_best = best.sum(axis=1)
        shrunk = n_best < len(subset)
        if shrunk.any():
            # group by the surviving set and recurse from step 1
            codes = best[shrunk] @ (1 << np.arange(len(subset)))
            idx_shrunk = active[shrunk]
            for code in np.unique(codes):
                members = [subset[j] for j in range(len(subset)) if code >> j & 1]
                here = idx_shrunk[codes == code]
                if stats is not None and len(members) == 1:
                    stats[name] = stats.get(name, 0) + len(here)
                out[here] = resolve(tab, members, rows[here], rng, stats)
        active = active[~shrunk]
        step += 1
    if len(active):
        if stats is not None:
            stats["residual_draw"] = stats.get("residual_draw", 0) + len(active)
        out[active] = np.array(subset, dtype=object)[
            rng.integers(0, len(subset), size=len(active))]
    return out


def division_winners(tab: Tables, rng, stats=None):
    """{division: array of winning team per sim}."""
    s = tab.s
    res = {}
    n = tab.R.shape[0]
    for d, members in s.divisions.items():
        ids = [s.idx[t] for t in members]
        pct = tab.pct[:, ids]
        top = np.isclose(pct, pct.max(axis=1, keepdims=True), rtol=0, atol=1e-9)
        codes = top @ (1 << np.arange(len(members)))
        win = np.empty(n, dtype=object)
        for code in np.unique(codes):
            tied = [members[j] for j in range(len(members)) if code >> j & 1]
            rows = np.nonzero(codes == code)[0]
            if len(tied) > 1 and stats is not None:
                stats["tied_at_top"] = stats.get("tied_at_top", 0) + len(rows)
            win[rows] = resolve(tab, tied, rows, rng, stats)
        res[d] = win
    return res


def division_probs(season: Season, p_home, n, rng, stats=None):
    """{division: {team: probability}} from `n` simulations."""
    R = simulate(season, p_home, n, rng)
    tab = Tables(season, R)
    wins = division_winners(tab, rng, stats)
    out = {}
    for d, members in season.divisions.items():
        w = wins[d]
        out[d] = {t: float(np.mean(w == t)) for t in members}
    return out


def leader_probs(season: Season):
    """The standings carried forward, literally: co-leaders on win pct share 1."""
    fixed = np.array([[np.nan if g[3] is None else g[3] for g in season.games]])
    played = ~np.isnan(fixed[0])
    R = np.where(played, fixed, 0.0)
    W = R @ season.H + np.where(played, 1.0 - R, 0.0) @ season.A
    G = played.astype(float) @ (season.H + season.A)
    pct = _pct(W[0], G)
    out = {}
    for d, members in season.divisions.items():
        v = np.array([pct[season.idx[t]] for t in members])
        top = np.isclose(v, v.max(), rtol=0, atol=1e-9)
        out[d] = {t: (1.0 / top.sum() if top[j] else 0.0)
                  for j, t in enumerate(members)}
    return out


def standings(season: Season):
    """{team: (wins, losses, ties)} over played games."""
    rec = {t: [0, 0, 0] for t in season.teams}
    for h, a, _w, res in season.games:
        if res is None:
            continue
        if res == 1.0:
            rec[h][0] += 1
            rec[a][1] += 1
        elif res == 0.0:
            rec[a][0] += 1
            rec[h][1] += 1
        else:
            rec[h][2] += 1
            rec[a][2] += 1
    return {t: tuple(v) for t, v in rec.items()}


def brier(probs: dict, winner: str) -> float:
    return sum((p - (1.0 if t == winner else 0.0)) ** 2 for t, p in probs.items())
