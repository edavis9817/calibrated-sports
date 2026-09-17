"""Market-implied fantasy distributions: build the object, then score it.

    python -m research.implied --marginals    # arm A / arm B implied CDFs
    python -m research.implied --copula       # within-player dependence
    python -m research.implied --score        # calibration against realized
    python -m research.implied --h2h          # the KXNFLFFH2H consistency check
    python -m research.implied --all

THE OBJECT. A threshold ladder is a survival function. P(X>=2), P(X>=3), ...
read off a Kalshi ladder, or P(X>L) read off a sportsbook over/under at several
lines, is the market's own view of the whole distribution rather than of one
threshold. Couple several of those with a dependence structure and you can
simulate fantasy points that nobody quotes directly.

WHAT IS MARKET-DERIVED AND WHAT IS NOT. Three components, and they are not
equally trustworthy. Every output labels them:

  receptions, receiving yards   MARKET      a real de-vigged ladder
  rushing yards                 DERIVED     rush-attempt ladder x fitted YPC;
                                            no yardage ladder exists free
  touchdowns                    ANCHORED    scaled to an expected-scorer count
                                            fitted from team totals, because
                                            anytime-TD prices are one-sided and
                                            cannot be normalised as a partition

DENSITY IS THE VARIABLE UNDER TEST. A distribution fitted to two survival
points is not the same object as one fitted to eight, and averaging them hides
exactly the question. `n_points` rides on every fit and stratifies every result.
"""
import argparse
import functools
import math
import sqlite3
import statistics
from collections import defaultdict

import config

# Every fit below reads the whole store and none of them change within a run.
# Memoised because --all calls each of them from three different places, and a
# 93-second marginal build repeated per arm per scoring is most of the runtime.
cache = functools.lru_cache(maxsize=None)

# --- fantasy scoring ---------------------------------------------------------
# Three scorings, kept separate rather than parameterised into one number: PPR
# and standard disagree most exactly where the reception ladder is densest, so
# collapsing them would hide the component this engine is best at.
SCORINGS = {
    "ppr": {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "td": 6.0},
    "half_ppr": {"rec": 0.5, "rec_yd": 0.1, "rush_yd": 0.1, "td": 6.0},
    "standard": {"rec": 0.0, "rec_yd": 0.1, "rush_yd": 0.1, "td": 6.0},
}

COUNT_STATS = {"receptions", "rush_attempts"}


def _ro():
    return sqlite3.connect("file:%s?mode=ro" % config.DB_PATH, uri=True)


# =============================================================================
# PART 2 - implied marginals
# =============================================================================

def shin_devig(p_over, p_under, tol=1e-10):
    """Shin (1993). Returns the over probability with the margin removed.

    NOT multiplicative. The calibration study measured the market's longshot
    end pricing 0.1375 and realizing 0.0674 - multiplicative de-vig assumes the
    margin is spread proportionally, which is exactly wrong there because it
    leaves the longshot too high. Shin models the margin as compensation for
    informed traders, which loads more of it onto the longshot and corrects the
    end of the book where anytime-TD and ceiling thresholds live.

    Solves for z, the implied proportion of informed money:
        pi_i = [sqrt(z^2 + 4(1-z) p_i^2 / S) - z] / (2(1-z)),  S = sum p_i
    and finds the z that makes the pi sum to 1.
    """
    s = p_over + p_under
    if s <= 0:
        return None
    if abs(s - 1.0) < 1e-9:
        return p_over

    def pi(p, z):
        if z >= 1 - 1e-12:
            return p / s
        return (math.sqrt(z * z + 4 * (1 - z) * p * p / s) - z) / (2 * (1 - z))

    lo, hi = 0.0, 0.999
    for _ in range(200):
        z = (lo + hi) / 2
        total = pi(p_over, z) + pi(p_under, z)
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = z
        else:
            hi = z
    return min(max(pi(p_over, z), 1e-6), 1 - 1e-6)


def power_devig(p_over, p_under, tol=1e-10):
    """Power method: find k with p_over^k + p_under^k = 1. Kept for comparison.

    Like Shin it is non-proportional, but it pulls the whole book toward the
    centre rather than specifically discounting the longshot.
    """
    s = p_over + p_under
    if s <= 0:
        return None
    if abs(s - 1.0) < 1e-9:
        return p_over
    lo, hi = 0.2, 5.0
    for _ in range(200):
        k = (lo + hi) / 2
        total = p_over ** k + p_under ** k
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = k
        else:
            hi = k
    return min(max(p_over ** k, 1e-6), 1 - 1e-6)


def isotonic_decreasing(xs, ys):
    """Pool-adjacent-violators, forced non-increasing in x.

    A survival function cannot rise as the threshold rises. Pooled book quotes
    violate that routinely - two books disagreeing by 3c at adjacent lines is
    enough - and an un-monotone survival curve integrates to a negative density
    somewhere, which then shows up as a nonsense simulated draw rather than as
    an error.
    """
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    v = [ys[i] for i in order]
    w = [1.0] * len(v)
    i = 0
    while i < len(v) - 1:
        if v[i] < v[i + 1] - 1e-12:
            tot = w[i] + w[i + 1]
            v[i] = (v[i] * w[i] + v[i + 1] * w[i + 1]) / tot
            w[i] = tot
            del v[i + 1], w[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    out, k = [], 0
    for run_v, run_w in zip(v, w):
        for _ in range(int(round(run_w))):
            out.append(run_v)
            k += 1
    res = [0.0] * len(xs)
    for pos, idx in enumerate(order):
        res[idx] = out[pos] if pos < len(out) else out[-1]
    return res


# --- parametric fit to the survival points -----------------------------------
# A ladder gives S(L) at a handful of L. To simulate you need the whole
# distribution, so the ladder constrains a two-parameter family and the family
# supplies interpolation and both tails. `n_points` records how much of the
# shape the market actually pinned down and how much the family assumed.

def _nb_survival(mean, vmr, line):
    """P(X > line) for a negative binomial with this mean and var/mean.

    Written out rather than routed through core.distributions: the grid search
    below evaluates this a few million times, and constructing a distribution
    object per call made a 23,000-marginal fit take longer than the session.
    Same parameterisation - r = mu/(vmr-1), p = 1/vmr - checked against it in
    the tests.
    """
    mean = max(mean, 1e-9)
    vmr = max(vmr, 1.0001)
    r = mean / (vmr - 1.0)
    pp = 1.0 / vmr
    kmax = int(math.floor(line))
    if kmax < 0:
        return 1.0
    # cdf by forward recursion on the pmf; no factorials, no per-call objects
    term = math.exp(r * math.log(pp))          # pmf(0)
    cdf = term
    for k in range(1, kmax + 1):
        term *= (r + k - 1) / k * (1.0 - pp)
        cdf += term
        if cdf >= 1.0:
            return 0.0
    return max(0.0, 1.0 - cdf)


def _gamma_survival(mean, cv, line):
    """P(X > line) for a gamma with this mean and coefficient of variation.

    Yardage, not counts: continuous, right-skewed, zero mass handled by the
    caller's zero-inflation term.
    """
    if line <= 0:
        return 1.0
    k = 1.0 / max(cv * cv, 1e-4)
    theta = max(mean, 1e-6) / k
    return 1.0 - _gamma_cdf(line / theta, k)


def _gamma_cdf(x, k, iters=300):
    """Regularised lower incomplete gamma P(k, x), series + continued fraction."""
    if x <= 0:
        return 0.0
    gln = math.lgamma(k)
    if x < k + 1:
        ap, s, d = k, 1.0 / k, 1.0 / k
        for _ in range(iters):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-12:
                break
        return s * math.exp(-x + k * math.log(x) - gln)
    b, c = x + 1 - k, 1e30
    d = 1.0 / b
    h = d
    for i in range(1, iters):
        an = -i * (i - k)
        b += 2
        d = an * d + b
        if abs(d) < 1e-30:
            d = 1e-30
        c = b + an / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        if abs(d * c - 1.0) < 1e-12:
            break
    return 1.0 - math.exp(-x + k * math.log(x) - gln) * h


def fit_marginal(stat, points):
    """Fit a two-parameter marginal to [(line, survival), ...].

    Returns a dict carrying the fit AND how well constrained it was. A single
    survival point determines a mean and nothing else, so the shape parameter
    falls back to a population value and `assumed_shape` says so - which is the
    difference the density stratification is there to expose.
    """
    pts = [(l, s) for l, s in points if l is not None and s is not None]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = isotonic_decreasing(xs, [p[1] for p in pts])
    pts = sorted(zip(xs, ys))
    n = len(pts)
    count = stat in COUNT_STATS
    # Population fallbacks, measured in research/shrinkage.py on the screened
    # quoted population rather than guessed.
    shape0 = 1.6 if count else 0.85           # var/mean for counts, CV for yards
    surv = _nb_survival if count else _gamma_survival

    lines_ = [l for l, _ in pts]
    obs = [s for _, s in pts]

    def sse(mean, shape):
        t = 0.0
        for l, s in zip(lines_, obs):
            d = surv(mean, shape, l) - s
            t += d * d
        return t

    # Coarse grid then local refine. Two parameters and at most ~18 points, so
    # a search is cheaper and more robust than a gradient on a step function.
    lo_m = max(0.05, pts[0][0] * 0.15)
    hi_m = max(pts[-1][0] * 3.0 + 2.0, 1.0)
    best = None
    shapes = [shape0] if n < 2 else (
        [1.05, 1.2, 1.4, 1.6, 1.9, 2.3, 2.8, 3.5] if count
        else [0.45, 0.6, 0.75, 0.9, 1.05, 1.25, 1.5])
    for _round in range(3):
        step = (hi_m - lo_m) / 16.0
        for i in range(17):
            m = lo_m + i * step
            for sh in shapes:
                e = sse(m, sh)
                if best is None or e < best[0]:
                    best = (e, m, sh)
        lo_m, hi_m = max(0.05, best[1] - step * 2), best[1] + step * 2
    err, mean, shape = best
    return {
        "stat": stat, "family": "negative_binomial" if count else "gamma",
        "mean": mean, "shape": shape, "n_points": n,
        "lines": [l for l, _ in pts], "survival": [s for _, s in pts],
        "rmse": math.sqrt(err / n),
        # The honesty flag the brief asks for: did the market pin the shape, or
        # did the family supply it?
        "assumed_shape": n < 2,
        "interpolated": n < 3,
    }


def _inverse_table(fit, n=512):
    """Precompute the inverse CDF once per fitted marginal.

    The draw loop runs ~14 million times over a full validation. Recomputing a
    CDF from scratch inside it - which is what calling prob_at_most(k) per k
    does, at O(k^2) per draw - turned the run into hours. Build the ladder of
    quantiles once, then every draw is a lookup.
    """
    if fit["family"] == "negative_binomial":
        mean, vmr = max(fit["mean"], 1e-9), max(fit["shape"], 1.0001)
        r = mean / (vmr - 1.0)
        pp = 1.0 / vmr
        cdf, term, acc = [], math.exp(r * math.log(pp)), 0.0
        for k in range(0, 80):
            if k:
                term *= (r + k - 1) / k * (1.0 - pp)
            acc += term
            cdf.append(min(acc, 1.0))
            if acc > 0.999999:
                break
        return ("count", cdf)
    # gamma: a grid of survival values, inverted by interpolation
    hi = max(fit["mean"] * 14.0, 50.0)
    xs = [hi * i / n for i in range(n + 1)]
    sv = [_gamma_survival(fit["mean"], fit["shape"], x) for x in xs]
    return ("cont", (xs, sv))


def sample_marginal(fit, u, table=None):
    """Inverse-CDF draw from a fitted marginal at uniform u."""
    if fit is None:
        return 0.0
    kind, tab = table if table is not None else _inverse_table(fit)
    if kind == "count":
        for k, c in enumerate(tab):
            if c >= u:
                return float(k)
        return float(len(tab))
    xs, sv = tab
    target = 1.0 - u
    lo, hi = 0, len(xs) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if sv[mid] > target:
            lo = mid + 1
        else:
            hi = mid
    return xs[lo]


# --- ladder extraction -------------------------------------------------------
# Two arms, and they need different de-vig treatment because they are different
# kinds of venue. Kalshi is an EXCHANGE: the yes book has a bid-ask spread and
# no bookmaker margin, so the mid IS the probability and Shin has nothing to
# remove. A sportsbook quotes both sides with a margin baked in, so the pair at
# each line gets de-vigged. Applying Shin to an exchange mid would invent a
# correction for a margin that is not there.

ARM_A_SQL = """
SELECT o.entity_id, o.event_id, o.stat, o.line, q.best_bid, q.best_ask, q.mid
  FROM outcomes o
  JOIN market_outcome mo ON mo.outcome_id = o.outcome_id AND mo.venue = 'kalshi'
  JOIN quotes q ON q.venue = 'kalshi' AND q.market_id = mo.market_id
 WHERE o.entity_type = 'player' AND o.line IS NOT NULL
   AND q.ts = (SELECT MAX(ts) FROM quotes q2
                WHERE q2.venue='kalshi' AND q2.market_id=mo.market_id
                  AND q2.best_bid IS NOT NULL AND q2.best_ask IS NOT NULL)
   AND q.best_bid IS NOT NULL AND q.best_ask IS NOT NULL
"""

ARM_B_SQL = """
SELECT o.entity_id, o.event_id, o.stat, o.line, o.side, c.p_all, c.n_all,
       c.dispersion
  FROM outcome_settlement s
  JOIN outcomes o      ON o.outcome_id = s.outcome_id
  JOIN outcome_close c ON c.outcome_id = s.outcome_id
 WHERE o.entity_type = 'player' AND o.line IS NOT NULL AND c.p_all IS NOT NULL
   -- `outcome_settlement` is joined here purely as "this outcome resolved" -
   -- the realized values come from realized_index(), not from this row. Once
   -- voids exist, an unfiltered join would build ladder rungs out of outcomes
   -- that never resolved and move the published quantile coverage.
   AND s.result IN ('over', 'under')
"""


def ladders_arm_a(con):
    """Kalshi native ladders. Survival = mid of the yes book, no de-vig."""
    lad = defaultdict(list)
    spread = defaultdict(list)
    for gsis, event, stat, line, bid, ask, mid in con.execute(ARM_A_SQL):
        p = mid if mid is not None else (bid + ask) / 2.0
        lad[(gsis, event, stat)].append((line, min(max(p, 1e-4), 1 - 1e-4)))
        spread[(gsis, event, stat)].append(ask - bid)
    return {k: {"points": v, "spread": statistics.fmean(spread[k]),
                "arm": "A", "venue": "kalshi"}
            for k, v in lad.items()}


def ladders_arm_b(con, devig=shin_devig):
    """Sportsbook ladders, pooled across books, Shin-de-vigged at each line.

    The pair at one line comes from the consensus close on each side, so the
    de-vig is applied to the consensus rather than book by book. Book-by-book
    would be better and is not available: outcome_close already collapsed the
    books to a median, and re-deriving per book means re-reading 500k quotes.
    Recorded here rather than glossed - it makes the arm B de-vig a consensus
    de-vig, which is slightly conservative at the tails.
    """
    sides = defaultdict(dict)
    meta = defaultdict(lambda: {"n_books": [], "dispersion": []})
    for gsis, event, stat, line, side, p, nb, disp in con.execute(ARM_B_SQL):
        sides[(gsis, event, stat, line)][side] = p
        m = meta[(gsis, event, stat)]
        m["n_books"].append(nb or 0)
        if disp is not None:
            m["dispersion"].append(disp)

    lad = defaultdict(list)
    for (gsis, event, stat, line), sd in sides.items():
        over, under = sd.get("over"), sd.get("under")
        if over is None:
            continue
        p = devig(over, under) if under is not None else over
        if p is None:
            continue
        lad[(gsis, event, stat)].append((line, min(max(p, 1e-4), 1 - 1e-4)))
    out = {}
    for k, v in lad.items():
        m = meta[k]
        out[k] = {
            "points": v, "arm": "B", "venue": "sportsbook",
            "n_books": statistics.fmean(m["n_books"]) if m["n_books"] else 0,
            "spread": statistics.fmean(m["dispersion"]) if m["dispersion"] else None,
        }
    return out


@cache
def build_marginals(arm="B", devig=shin_devig):
    con = _ro()
    raw = ladders_arm_a(con) if arm == "A" else ladders_arm_b(con, devig)
    con.close()
    out = {}
    for key, d in raw.items():
        fit = fit_marginal(key[2], d["points"])
        if fit:
            fit.update({k: v for k, v in d.items() if k != "points"})
            out[key] = fit
    return out


# =============================================================================
# PART 3 - coupling
# =============================================================================
# Marginals alone would treat a 10-catch game and a 130-yard game as
# independent, which is wrong in the direction that matters most: the fantasy
# ceiling is exactly the corner where both are high at once. Independent
# marginals understate the upper tail and would make the engine look
# well-calibrated in the middle and badly overconfident about a boom game.
#
# CROSS-PLAYER correlation (QB<->WR1 ~0.42) is deliberately out of scope: it
# does not affect a single player's distribution. It will be needed for
# head-to-head and lineup questions, which is exactly what PART 5 touches - the
# H2H check below compares two INDEPENDENT player distributions, and that
# independence is the largest known error in that comparison.

CORR_SQL = """
SELECT position, receptions, receiving_yards, carries, rushing_yards,
       COALESCE(receiving_tds,0) + COALESCE(rushing_tds,0) AS tds
  FROM nfl_player_week
 WHERE season BETWEEN 2015 AND 2025 AND season_type = 'REG'
   AND position IN ('WR','TE','RB','QB','FB')
"""

CORR_FIELDS = ["receptions", "receiving_yards", "carries", "rushing_yards", "tds"]


def _spearman(a, b):
    """Rank correlation. Rank, not Pearson, because a Gaussian copula is fitted
    on ranks and because a 200-yard game would otherwise drag the estimate."""
    n = len(a)
    if n < 30:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    ra, rb = rank(a), rank(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db) if da and db else None


@cache
def fit_copula(min_active=1):
    """Within-player rank correlations per position, over 2015-2025.

    Restricted to player-weeks where the player was actually involved: a bench
    week of all zeros is a tie at the bottom of every rank and inflates every
    correlation toward 1 without saying anything about a game that happened.
    """
    con = _ro()
    rows = con.execute(CORR_SQL).fetchall()
    con.close()
    by_pos = defaultdict(lambda: defaultdict(list))
    for pos, rec, ryd, car, rud, td in rows:
        vals = [rec or 0, ryd or 0, car or 0, rud or 0, td or 0]
        if sum(1 for v in vals[:4] if v > 0) < min_active:
            continue
        for f, v in zip(CORR_FIELDS, vals):
            by_pos[pos][f].append(float(v))
    out = {}
    for pos, cols in by_pos.items():
        n = len(cols[CORR_FIELDS[0]])
        if n < 200:
            continue
        m = {}
        for i, a in enumerate(CORR_FIELDS):
            for b in CORR_FIELDS[i + 1:]:
                r = _spearman(cols[a], cols[b])
                if r is not None:
                    m[(a, b)] = r
        out[pos] = {"n": n, "rho": m}
    return out


def gaussian_copula_draw(rho, rng, k=2):
    """Two correlated uniforms from a Gaussian copula with this rank rho.

    Pearson correlation of the latent normals, converted from Spearman by
    rho_p = 2 sin(pi rho_s / 6) - the standard relation for a Gaussian copula.
    Skipping the conversion applies the rank correlation as if it were linear,
    which understates the coupling slightly and always in the same direction.
    """
    rp = 2.0 * math.sin(math.pi * max(min(rho, 0.999), -0.999) / 6.0)
    z1 = rng.gauss(0, 1)
    z2 = rp * z1 + math.sqrt(max(1 - rp * rp, 0.0)) * rng.gauss(0, 1)
    return _norm_cdf(z1), _norm_cdf(z2)


def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# =============================================================================
# TOUCHDOWNS - anchored, not market-derived
# =============================================================================
# Anytime-TD prices are one-sided: every quote is a "Yes" and there is no "No",
# so the outcomes are neither exhaustive nor mutually exclusive and no de-vig
# method that normalises a partition applies. Rather than pretend otherwise,
# the TD component is ANCHORED to a count fitted from data we already hold:
#
#   1. expected team TDs from the closing team total (total_line, spread_line)
#   2. expected DISTINCT scorers given N team TDs, from realized player-weeks
#   3. a player's share of that expectation from his own prior usage
#
# This is the weakest component in the engine and is labelled as such wherever
# it appears. It is a model of TDs, not a market view of them.

ANCHOR_SQL = """
SELECT g.total_line, g.spread_line, g.home_score, g.away_score
  FROM (SELECT game_id, MAX(total_line) total_line, MAX(spread_line) spread_line,
               MAX(home_score) home_score, MAX(away_score) away_score
          FROM nfl_games
         WHERE season BETWEEN 2015 AND 2025 AND game_type='REG'
         GROUP BY game_id) g
 WHERE g.total_line IS NOT NULL AND g.home_score IS NOT NULL
"""

SCORERS_SQL = """
SELECT w.season, w.week, w.team,
       SUM(COALESCE(w.receiving_tds,0) + COALESCE(w.rushing_tds,0)) AS tds,
       SUM(CASE WHEN COALESCE(w.receiving_tds,0)+COALESCE(w.rushing_tds,0) > 0
                THEN 1 ELSE 0 END) AS scorers
  FROM nfl_player_week w
 WHERE w.season BETWEEN 2015 AND 2025 AND w.season_type='REG'
 GROUP BY w.season, w.week, w.team
"""


@cache
def fit_td_anchor():
    """Two small regressions, both reported so the anchor is inspectable.

    team_tds ~ a + b * team_implied_points, where the team's implied points are
    (total +/- spread)/2 - the standard decomposition of a game total into two
    team totals. Then E[distinct scorers | team TDs], which is sublinear
    because one player often scores twice.
    """
    con = _ro()
    xs, ys = [], []
    for total, spread, hs, aws in con.execute(ANCHOR_SQL):
        if total is None or spread is None:
            continue
        # SIGN CHECKED EMPIRICALLY, not assumed. nflverse spread_line is
        # POSITIVE when the home team is favoured, so home implied points are
        # (T + S)/2. The other convention correlates -0.150 with actual points
        # against +0.391 for this one - a sign error here would have made the
        # anchor predict fewer TDs for better offences and nothing would have
        # raised an error.
        for implied, pts in ((total + spread) / 2.0, hs), ((total - spread) / 2.0, aws):
            if pts is None:
                continue
            xs.append(implied)
            # TDs are not directly in nfl_games; points/7 is a poor proxy, so
            # the scorer table below supplies the real counts and this
            # regression only fixes the points->TD slope.
            ys.append(pts)
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    b = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
         / max(sum((x - mx) ** 2 for x in xs), 1e-9))
    a = my - b * mx

    tds_by_team, scorers = [], defaultdict(list)
    for _s, _w, _t, td, sc in con.execute(SCORERS_SQL):
        if td is None:
            continue
        tds_by_team.append(td)
        scorers[int(td)].append(sc)
    con.close()

    # E[scorers | tds], the sublinearity that stops the anchor double-counting
    # a two-TD game as two players.
    curve = {k: statistics.fmean(v) for k, v in sorted(scorers.items()) if len(v) >= 30}
    # points -> TDs, from realized team scoring
    pts_per_td = statistics.fmean(ys) / max(statistics.fmean(tds_by_team), 1e-9)
    return {
        "n_team_games": n, "intercept": a, "slope": b,
        "mean_team_points": my, "mean_team_tds": statistics.fmean(tds_by_team),
        "points_per_td": pts_per_td,
        "scorers_given_tds": curve,
        "component": "ANCHORED",
    }


def expected_team_tds(anchor, total_line, spread_line, is_home):
    implied = ((total_line + spread_line) / 2.0 if is_home
               else (total_line - spread_line) / 2.0)
    pts = anchor["intercept"] + anchor["slope"] * implied
    return max(pts, 0.0) / anchor["points_per_td"]


# =============================================================================
# PART 4 - simulate and score
# =============================================================================

REALIZED_SQL = """
SELECT w.gsis_id, g.game_id, w.position,
       COALESCE(w.receptions,0), COALESCE(w.receiving_yards,0),
       COALESCE(w.carries,0), COALESCE(w.rushing_yards,0),
       COALESCE(w.receiving_tds,0) + COALESCE(w.rushing_tds,0),
       gg.total_line, gg.spread_line, (w.team = gg.home_team) AS is_home
  FROM nfl_player_week w
  JOIN (SELECT game_id, MAX(season) season, MAX(week) week, MAX(total_line) total_line,
               MAX(spread_line) spread_line, MAX(home_team) home_team
          FROM nfl_games GROUP BY game_id) gg ON gg.season=w.season AND gg.week=w.week
  JOIN (SELECT game_id, MAX(season) s, MAX(week) wk FROM nfl_games GROUP BY game_id) g
       ON g.game_id = gg.game_id
 WHERE w.season BETWEEN 2023 AND 2025 AND w.season_type='REG'
"""


@cache
def realized_index():
    """(gsis, game_id) -> realized components, position and the game's market
    total. One pass; the simulation joins against it."""
    con = _ro()
    idx = {}
    for (gsis, gid, pos, rec, ryd, car, rud, td, tot, spr, home) in con.execute(REALIZED_SQL):
        idx[(gsis, gid)] = {
            "pos": (pos or "").upper(), "rec": rec, "rec_yd": ryd,
            "car": car, "rush_yd": rud, "td": td,
            "total": tot, "spread": spr, "home": bool(home),
        }
    con.close()
    return idx


@cache
def fit_ypc():
    """Yards per carry and yards per reception, by position, 2015-2025.

    Used only where a yardage LADDER does not exist. Rushing yards are DERIVED
    this way and carry the extra variance of the YPC draw on top of the attempt
    ladder's own uncertainty, which is worse than a direct ladder and is the
    honest cost of not buying one.
    """
    con = _ro()
    rows = con.execute(
        """SELECT position, carries, rushing_yards FROM nfl_player_week
            WHERE season BETWEEN 2015 AND 2025 AND season_type='REG'
              AND carries >= 3 AND rushing_yards IS NOT NULL""").fetchall()
    con.close()
    per = defaultdict(list)
    for pos, car, yd in rows:
        if car and car > 0:
            per[(pos or "").upper()].append(yd / car)
    out = {}
    for pos, v in per.items():
        if len(v) < 200:
            continue
        out[pos] = {"mean": statistics.fmean(v), "sd": statistics.pstdev(v), "n": len(v)}
    league = [x for v in per.values() for x in v]
    out["_ALL"] = {"mean": statistics.fmean(league), "sd": statistics.pstdev(league),
                   "n": len(league)}
    return out


@cache
def fit_ypr():
    """Yards per reception by position. The receiving twin of fit_ypc().

    Needed because Kalshi lists a receptions ladder and no yardage ladder, so a
    fantasy total built from arm A alone has no yards in it at all. Deriving
    yards from the catch ladder is the same trade the revised brief accepts for
    rushing: worse than a ladder, free, and the extra variance is explicit.
    """
    con = _ro()
    rows = con.execute(
        """SELECT position, receptions, receiving_yards FROM nfl_player_week
            WHERE season BETWEEN 2015 AND 2025 AND season_type='REG'
              AND receptions >= 2 AND receiving_yards IS NOT NULL""").fetchall()
    con.close()
    per = defaultdict(list)
    for pos, rec, yd in rows:
        if rec and rec > 0:
            per[(pos or "").upper()].append(yd / rec)
    out = {}
    for pos, v in per.items():
        if len(v) >= 200:
            out[pos] = {"mean": statistics.fmean(v), "sd": statistics.pstdev(v),
                        "n": len(v)}
    league = [x for v in per.values() for x in v]
    out["_ALL"] = {"mean": statistics.fmean(league), "sd": statistics.pstdev(league),
                   "n": len(league)}
    return out


def simulate_player_game(fits, real, anchor, ypc, rho, rng, n=4000, scoring="ppr",
                         ypr=None):
    """One player-game -> n simulated fantasy point totals.

    Receptions and receiving yards are coupled through a Gaussian copula on the
    fitted rank correlation for the position. Rushing yards, where an attempt
    ladder exists, are attempts x a YPC draw. Touchdowns are ANCHORED.
    """
    w = SCORINGS[scoring]
    rec_fit = fits.get("receptions")
    ryd_fit = fits.get("receiving_yards")
    car_fit = fits.get("rush_attempts")
    rec_tab = _inverse_table(rec_fit) if rec_fit else None
    ryd_tab = _inverse_table(ryd_fit) if ryd_fit else None
    car_tab = _inverse_table(car_fit) if car_fit else None
    pos = real["pos"]
    y = ypc.get(pos, ypc["_ALL"])

    # anchored TD rate: the player's own scoring rate, scaled by how many TDs
    # this game's market total implies for his team relative to a league mean.
    p_td = 0.0
    if anchor and real.get("total") is not None and real.get("spread") is not None:
        exp_tds = expected_team_tds(anchor, real["total"], real["spread"], real["home"])
        scale = exp_tds / max(anchor["mean_team_tds"], 1e-6)
        base = fits.get("_td_rate", 0.0)
        p_td = min(base * scale, 0.95)

    out = []
    for _ in range(n):
        u1, u2 = gaussian_copula_draw(rho, rng)
        rec = sample_marginal(rec_fit, u1, rec_tab) if rec_fit else 0.0
        if ryd_fit:
            ryd = sample_marginal(ryd_fit, u2, ryd_tab)
        elif rec_fit and ypr is not None:
            # DERIVED, not market: no yardage ladder exists on this venue. The
            # copula draw u2 still drives it, so the catch/yard dependence is
            # preserved rather than thrown away by using a fresh uniform.
            yr = ypr.get(pos, ypr["_ALL"])
            per_catch = yr["mean"] + yr["sd"] * (u2 - 0.5) * 2.0 / math.sqrt(
                max(rec, 1))
            ryd = max(rec * per_catch, 0.0)
        else:
            ryd = 0.0
        rush_yd = 0.0
        if car_fit:
            car = sample_marginal(car_fit, rng.random(), car_tab)
            if car > 0:
                per = rng.gauss(y["mean"], y["sd"] / math.sqrt(max(car, 1)))
                rush_yd = max(car * per, 0.0)
        tds = 0
        if p_td > 0:
            if rng.random() < p_td:
                tds = 2 if rng.random() < 0.18 else 1
        out.append(w["rec"] * rec + w["rec_yd"] * ryd
                   + w["rush_yd"] * rush_yd + w["td"] * tds)
    out.sort()
    return out


def realized_points(real, scoring="ppr"):
    w = SCORINGS[scoring]
    return (w["rec"] * real["rec"] + w["rec_yd"] * real["rec_yd"]
            + w["rush_yd"] * real["rush_yd"] + w["td"] * real["td"])


def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    i = min(int(q * (len(sorted_vals) - 1)), len(sorted_vals) - 1)
    return sorted_vals[i]


TAILS = [0.50, 0.75, 0.90, 0.95]


def score_arm(arm="B", scoring="ppr", n_sims=2000, seed=7, limit=None):
    """The validation. Predicted vs realized, with the tails kept separate.

    Two views, because they answer different questions:

      QUANTILE COVERAGE  for each player-game take the predicted q50/q75/q90/q95
                         and count how often the realized total exceeded it. A
                         calibrated distribution exceeds its own q90 exactly 10%
                         of the time. This is where a ceiling failure shows.
      CALIBRATION CURVE  bucket predicted P(FP >= threshold) and compare to the
                         realized frequency in each bucket.
    """
    import random
    rng = random.Random(seed)
    marg = build_marginals(arm)
    real = realized_index()
    anchor = fit_td_anchor()
    ypc, ypr = fit_ypc(), fit_ypr()
    cop = fit_copula()

    # per (player, game): collect the stats we have a marginal for
    by_pg = defaultdict(dict)
    for (gsis, gid, stat), f in marg.items():
        by_pg[(gsis, gid)][stat] = f

    # player TD rate from his own realized history, for the anchor
    td_rate = defaultdict(list)
    for (gsis, gid), r in real.items():
        td_rate[gsis].append(1.0 if r["td"] > 0 else 0.0)
    td_rate = {k: statistics.fmean(v) for k, v in td_rate.items() if len(v) >= 4}

    rows = []
    keys = [k for k in by_pg if k in real]
    if limit:
        keys = keys[:limit]
    for key in keys:
        fits = dict(by_pg[key])
        r = real[key]
        if "receptions" not in fits and "receiving_yards" not in fits:
            continue
        fits["_td_rate"] = td_rate.get(key[0], 0.0)
        pos = r["pos"]
        rho = cop.get(pos, {}).get("rho", {}).get(("receptions", "receiving_yards"), 0.75)
        sims = simulate_player_game(fits, r, anchor, ypc, rho, rng, n_sims, scoring,
                                    ypr=ypr)
        actual = realized_points(r, scoring)
        npts = max((f["n_points"] for k2, f in fits.items()
                    if isinstance(f, dict) and "n_points" in f), default=0)
        rows.append({
            "key": key, "pos": pos, "actual": actual, "sims": sims,
            "n_points": npts,
            "spread": max((f.get("spread") or 0) for k2, f in fits.items()
                          if isinstance(f, dict)) if fits else 0,
            # dispersion is 0 when only ONE book quoted, which would otherwise
            # pile every single-book fit into the "tight" stratum and make
            # tight books look badly calibrated for the wrong reason.
            "n_books": max((f.get("n_books") or 0) for k2, f in fits.items()
                           if isinstance(f, dict)) if fits else 0,
            "has_rush": "rush_attempts" in fits,
        })
    return rows, {"anchor": anchor, "copula": cop, "ypc": ypc}


def quantile_coverage(rows, group=None):
    """Realized exceedance of each predicted quantile. Calibrated = 1 - q."""
    buckets = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        g = "all" if group is None else group(r)
        for q in TAILS:
            thr = quantile(r["sims"], q)
            b = buckets[g][q]
            b[1] += 1
            if r["actual"] > thr:
                b[0] += 1
    out = {}
    for g, qs in buckets.items():
        out[g] = {q: {"n": v[1], "realized": v[0] / v[1] if v[1] else 0.0,
                      "expected": 1 - q}
                  for q, v in sorted(qs.items())}
    return out


def calibration(rows, thresholds=(5, 10, 15, 20, 25, 30)):
    """Predicted P(FP >= t) against realized frequency, bucketed."""
    bins = defaultdict(lambda: [0.0, 0, 0])   # sum_p, hits, n
    for r in rows:
        n = len(r["sims"])
        for t in thresholds:
            p = sum(1 for s in r["sims"] if s >= t) / n
            b = bins[round(min(int(p * 10), 9) / 10, 1)]
            b[0] += p
            b[1] += 1 if r["actual"] >= t else 0
            b[2] += 1
    return {k: {"predicted": v[0] / v[2], "realized": v[1] / v[2], "n": v[2]}
            for k, v in sorted(bins.items()) if v[2] >= 30}


def brier_logloss(rows, thresholds=(5, 10, 15, 20, 25, 30)):
    bs = ll = 0.0
    n = 0
    for r in rows:
        m = len(r["sims"])
        for t in thresholds:
            p = min(max(sum(1 for s in r["sims"] if s >= t) / m, 1e-6), 1 - 1e-6)
            hit = 1.0 if r["actual"] >= t else 0.0
            bs += (p - hit) ** 2
            ll -= math.log(p if hit else 1 - p)
            n += 1
    return {"brier": bs / n, "log_loss": ll / n, "n": n}


# =============================================================================
# PART 5 - the consistency check
# =============================================================================
# The brief asks for KXNFLFFH2H. That series is not currently listed on Kalshi
# and has zero open markets. KXNFLFFPTS is, and it is a STRICTLY BETTER version
# of the same test: it quotes P(fantasy points > X) for one player directly, so
# the derivative and its components sit on the same exchange, for the same game,
# at the same moment - and unlike an H2H it does not also depend on
# cross-player correlation, which is explicitly out of scope for this brief.
#
# A derivative priced independently of its components is where structural edge
# lives. Nothing here trades anything.

def load_ffpts(path="ffpts_markets.json"):
    import json
    import os
    p = path if os.path.isabs(path) else config.storage_path(path)
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for m in raw:
        strike = m.get("floor_strike")
        title = m.get("title") or ""
        if strike is None or ":" not in title:
            continue
        out.append({
            "ticker": m["ticker"],
            "player": title.split(":")[0].strip(),
            "event": m.get("event_ticker") or "-".join(m["ticker"].split("-")[:2]),
            "threshold": float(strike),
            "yes_bid": _f(m.get("yes_bid_dollars")),
            "yes_ask": _f(m.get("yes_ask_dollars")),
        })
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def h2h_consistency(scoring="ppr", n_sims=6000, seed=11):
    """Derived P(FP > X) from the component ladders vs the quoted FFPTS price.

    Components are arm A only: this must be the same venue as the derivative or
    the comparison measures cross-venue disagreement instead of internal
    consistency.
    """
    import random
    from venues.mapping import norm_name
    rng = random.Random(seed)

    quoted = load_ffpts()
    marg = build_marginals("A")
    anchor = fit_td_anchor()
    ypc, ypr = fit_ypc(), fit_ypr()
    cop = fit_copula()

    con = _ro()
    name_of = {g: n for g, n in con.execute(
        "SELECT gsis_id, display_name FROM player_xwalk WHERE display_name IS NOT NULL")}
    pos_of = {g: (p or "").upper() for g, p in con.execute(
        "SELECT gsis_id, position FROM player_xwalk")}
    # The upcoming slate's market totals, for the anchored TD term. Without
    # these every derived total is missing its touchdowns, which on a
    # fantasy-points threshold is most of the distribution.
    games = con.execute(
        """SELECT MAX(total_line), MAX(spread_line), MAX(home_team), MAX(away_team)
             FROM nfl_games WHERE season=2026 AND week=1 GROUP BY game_id""").fetchall()
    con.close()
    league_total = statistics.fmean([g[0] for g in games if g[0] is not None])         if games else 44.0
    td_rate = {}
    con2 = _ro()
    for g, n_g, n_td in con2.execute(
            """SELECT gsis_id, COUNT(*),
                      SUM(CASE WHEN COALESCE(receiving_tds,0)+COALESCE(rushing_tds,0)>0
                               THEN 1 ELSE 0 END)
                 FROM nfl_player_week WHERE season BETWEEN 2023 AND 2025
                  AND season_type='REG' GROUP BY gsis_id HAVING COUNT(*) >= 8"""):
        td_rate[g] = (n_td or 0) / n_g
    con2.close()
    by_norm = defaultdict(list)
    for g, n in name_of.items():
        by_norm[norm_name(n)].append(g)

    comp = defaultdict(dict)
    for (gsis, event, stat), f in marg.items():
        comp[gsis][stat] = f

    def covered(pos, comps):
        """Can these components reach a fantasy-points threshold at all?

        A QB's fantasy points are passing yards and passing TDs, and Kalshi
        lists no passing ladder - so a QB derived from a rush-attempt ladder
        tops out near 4 points against an 18-point line. The derived value is
        0.000 by CONSTRUCTION, and comparing it to a market price measures the
        missing component rather than a disagreement. An RB without a rushing
        ladder has the same hole. Excluded and counted, never reported.
        """
        if not pos:
            return False, "position unknown"
        if pos == "QB":
            return False, "QB: no passing component on this venue"
        if pos == "RB" and "rush_attempts" not in comps:
            return False, "RB without a rushing ladder"
        if pos in ("WR", "TE", "FB") and "receptions" not in comps:
            return False, "receiver without a catch ladder"
        return True, ""

    out, unmatched = [], 0
    excluded = defaultdict(int)
    for q in quoted:
        gs = by_norm.get(norm_name(q["player"]), [])
        hit = next((g for g in gs if g in comp), None)
        if hit is None:
            unmatched += 1
            continue
        fits = dict(comp[hit])
        fits["_td_rate"] = td_rate.get(hit, 0.0)
        pos = pos_of.get(hit, "")
        ok, why = covered(pos, [k for k in fits if not k.startswith("_")])
        if not ok:
            excluded[why] += 1
            continue
        # A league-average game rather than this game's number: the FFPTS
        # tickers do not carry a game_id that joins cleanly to nfl_games, and a
        # wrong join would be worse than a stated average. Recorded as a
        # limitation of the check, not of the engine.
        stub = {"pos": pos, "rec": 0, "rec_yd": 0, "car": 0, "rush_yd": 0,
                "td": 0, "total": league_total, "spread": 0.0, "home": True}
        rho = cop.get(pos, {}).get("rho", {}).get(("receptions", "receiving_yards"), 0.75)
        sims = simulate_player_game(fits, stub, anchor, ypc, rho, rng, n_sims,
                                    scoring, ypr=ypr)
        derived = sum(1 for s in sims if s > q["threshold"]) / len(sims)
        mid = None
        if q["yes_bid"] is not None and q["yes_ask"] is not None:
            mid = (q["yes_bid"] + q["yes_ask"]) / 2.0
        out.append({**q, "gsis": hit, "pos": pos, "derived": derived,
                    "market": mid,
                    "diff": (derived - mid) if mid is not None else None,
                    "components": sorted(k for k in fits if not k.startswith("_"))})
    return out, {"quoted": len(quoted), "unmatched": unmatched,
                 "excluded": dict(excluded)}


# =============================================================================
# reporting
# =============================================================================

def _hdr(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def report_marginals():
    _hdr("PART 2 - IMPLIED MARGINALS: ladder density is the variable under test")
    for arm in ("A", "B"):
        m = build_marginals(arm)
        per = defaultdict(list)
        for (_g, _e, st), f in m.items():
            per[st].append(f)
        label = ("A - Kalshi native ladders (exchange mid; a spread is not a "
                 "margin, so no de-vig applies)" if arm == "A"
                 else "B - sportsbook consensus, Shin de-vigged at each line")
        print(f"\n  ARM {label}")
        print(f"    {'stat':<18}{'fits':>7}{'med pts':>9}{'max':>5}"
              f"{'>=3 pts':>9}{'shape assumed':>15}{'fit rmse':>10}")
        for st, v in sorted(per.items(), key=lambda kv: -len(kv[1])):
            npts = [f["n_points"] for f in v]
            print(f"    {st:<18}{len(v):>7,}{statistics.median(npts):>9.0f}"
                  f"{max(npts):>5}"
                  f"{100 * sum(1 for x in npts if x >= 3) / len(v):>8.0f}%"
                  f"{100 * sum(1 for f in v if f['assumed_shape']) / len(v):>14.0f}%"
                  f"{statistics.median([f['rmse'] for f in v]):>10.4f}")
    print("\n  De-vig on the longshot pair the calibration study flagged")
    print("  (0.10-0.15 bucket: priced 0.1375, realized 0.0674):")
    po, pu = 0.1375, 0.8875
    print(f"    multiplicative {po/(po+pu):.4f}   power {power_devig(po,pu):.4f}"
          f"   shin {shin_devig(po,pu):.4f}   realized 0.0674")
    print("    Shin moves the right way and nowhere near far enough. The longshot")
    print("    bias is a MARKET bias, not a vig artifact - no de-vig method")
    print("    closes a 7-point gap.")


def report_copula():
    _hdr("PART 3 - COUPLING: within-player rank correlation, 2015-2025")
    cop = fit_copula()
    pairs = [("receptions", "receiving_yards"), ("carries", "rushing_yards"),
             ("receptions", "tds"), ("rushing_yards", "tds")]
    print(f"\n    {'pos':<6}{'n':>8}  " + "".join(
        f"{a[:4]+'~'+b[:4]:>12}" for a, b in pairs))
    for pos in ("WR", "TE", "RB", "QB", "FB"):
        d = cop.get(pos)
        if not d:
            continue
        r = d["rho"]
        cells = []
        for a, b in pairs:
            v = r.get((a, b), r.get((b, a)))
            cells.append(f"{v:+.3f}" if v is not None else "-")
        print(f"    {pos:<6}{d['n']:>8,}  " + "".join(f"{c:>12}" for c in cells))
    print("\n    Cross-player correlation (QB<->WR1 ~0.42) is OUT OF SCOPE and does")
    print("    not affect a single player's distribution. It is the largest known")
    print("    error in PART 5, which compares two players.")


def report_score(arm="B", scoring="ppr", n_sims=2000, limit=None):
    rows, meta = score_arm(arm, scoring, n_sims=n_sims, limit=limit)
    _hdr(f"PART 4 - VALIDATION, arm {arm}, {scoring.upper()}, "
         f"{len(rows):,} settled player-games")
    bl = brier_logloss(rows)
    print(f"\n  overall   brier {bl['brier']:.4f}   log loss {bl['log_loss']:.4f}"
          f"   ({bl['n']:,} threshold-observations)")

    print("\n  QUANTILE COVERAGE - how often the realized total beat the model's")
    print("  own quantile. A calibrated distribution exceeds its q90 10% of the time.")
    print(f"    {'quantile':<10}{'expected':>10}{'realized':>10}{'n':>8}{'verdict':>17}")
    for q, v in quantile_coverage(rows)["all"].items():
        d = v["realized"] - v["expected"]
        verdict = ("calibrated" if abs(d) < 0.02 else
                   "OVERCONFIDENT" if d > 0 else "underconfident")
        print(f"    q{int(q*100):<9}{v['expected']:>10.2f}{v['realized']:>10.3f}"
              f"{v['n']:>8,}{verdict:>17}")

    print("\n  BY POSITION")
    print(f"    {'pos':<6}{'n':>7}" + "".join(f"{'q'+str(int(q*100)):>9}" for q in TAILS))
    qcp = quantile_coverage(rows, group=lambda r: r["pos"])
    for pos, qs in sorted(qcp.items(), key=lambda kv: -list(kv[1].values())[0]["n"]):
        n = list(qs.values())[0]["n"]
        if n < 100:
            continue
        print(f"    {pos:<6}{n:>7,}" + "".join(f"{qs[q]['realized']:>9.3f}" for q in TAILS))

    print("\n  BY LADDER DENSITY - the question the two arms exist to answer")
    print(f"    {'points':<14}{'n':>7}" + "".join(f"{'q'+str(int(q*100)):>9}" for q in TAILS))
    dens = quantile_coverage(rows, group=lambda r: (
        "1-2 (sparse)" if r["n_points"] <= 2 else
        "3-4 (medium)" if r["n_points"] <= 4 else "5+ (dense)"))
    for k in ("1-2 (sparse)", "3-4 (medium)", "5+ (dense)"):
        if k not in dens:
            continue
        qs = dens[k]
        n = list(qs.values())[0]["n"]
        print(f"    {k:<14}{n:>7,}" + "".join(f"{qs[q]['realized']:>9.3f}" for q in TAILS))

    print("\n  BY BOOK DISAGREEMENT - a noisy book should give a noisy distribution")
    spr = quantile_coverage(rows, group=lambda r: (
        "1 book (n/a)" if r["n_books"] < 2 else
        "tight  <0.02" if (r["spread"] or 0) < 0.02 else
        "medium <0.05" if (r["spread"] or 0) < 0.05 else "wide  >=0.05"))
    print(f"    {'dispersion':<14}{'n':>7}" + "".join(f"{'q'+str(int(q*100)):>9}" for q in TAILS))
    for k in ("1 book (n/a)", "tight  <0.02", "medium <0.05", "wide  >=0.05"):
        if k not in spr:
            continue
        qs = spr[k]
        n = list(qs.values())[0]["n"]
        print(f"    {k:<14}{n:>7,}" + "".join(f"{qs[q]['realized']:>9.3f}" for q in TAILS))

    print("\n  CALIBRATION CURVE - predicted P(FP >= t) vs realized, pooled over t")
    print(f"    {'bucket':<10}{'predicted':>11}{'realized':>10}{'n':>9}")
    for b, v in calibration(rows).items():
        print(f"    {b:<10.1f}{v['predicted']:>11.4f}{v['realized']:>10.4f}{v['n']:>9,}")
    return rows, meta


def report_h2h():
    _hdr("PART 5 - CONSISTENCY: derived P(FP > X) vs Kalshi's own FFPTS price")
    out, meta = h2h_consistency()
    print("\n  KXNFLFFH2H has zero open markets. KXNFLFFPTS does, and it is a")
    print("  better test: same exchange, same game, and it needs no cross-player")
    print("  term - which this brief scopes out.")
    print(f"\n  quoted {meta['quoted']}, unmatched to any component ladder "
          f"{meta['unmatched']}")
    for why, n in sorted(meta.get("excluded", {}).items(), key=lambda kv: -kv[1]):
        print(f"    excluded {n:>3}  {why}")
    print(f"  comparable: {len(out)}")
    live = [o for o in out if o["market"] is not None
            and o["yes_ask"] is not None and o["yes_bid"] is not None
            and (o["yes_ask"] - o["yes_bid"]) <= 0.15]
    print(f"  with a tradeable book (spread <= 15c): {len(live)}")
    if not live:
        print("  nothing comparable")
        return out
    diffs = sorted(o["diff"] for o in live)
    print("\n  disagreement (derived - market), probability points")
    print(f"    mean {statistics.fmean(diffs):+.3f}   median {statistics.median(diffs):+.3f}"
          f"   sd {statistics.pstdev(diffs):.3f}")
    print(f"    p10 {diffs[len(diffs)//10]:+.3f}   p90 {diffs[9*len(diffs)//10]:+.3f}")
    print(f"\n  {'player':<22}{'thr':>6}{'derived':>9}{'market':>8}{'diff':>8}  components")
    for o in sorted(live, key=lambda x: -abs(x["diff"]))[:12]:
        print(f"    {o['player'][:20]:<22}{o['threshold']:>6.1f}{o['derived']:>9.3f}"
              f"{o['market']:>8.3f}{o['diff']:>+8.3f}  {','.join(o['components'])}")
    print("\n  Components are receptions and rush attempts only - Kalshi lists no")
    print("  yardage ladder - so the derived number leans on DERIVED yardage and")
    print("  an ANCHORED TD term. Read the spread of disagreements, not a row.")
    print("  Nothing is traded.")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--marginals", action="store_true")
    ap.add_argument("--copula", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--h2h", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--arm", default="B")
    ap.add_argument("--scoring", default="ppr", choices=sorted(SCORINGS))
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.marginals or a.all:
        report_marginals()
    if a.copula or a.all:
        report_copula()
    if a.score or a.all:
        report_score(a.arm, a.scoring, a.sims, a.limit)
    if a.h2h or a.all:
        report_h2h()


if __name__ == "__main__":
    main()
