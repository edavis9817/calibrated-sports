"""The baseline model: prior usage, shrunk hard, emitted as a distribution.

This is a PLUMBING baseline, not a signal. It exists so that predictions,
pricing, the ledger and the evaluation harness have something real to carry
before any of them is trusted with a real model.

What it does and why, all of it from CLAUDE.md's settled findings:

- **Usage only.** Efficiency added ~0.000 OOS R² on top of prior usage, so
  there is nothing here but volume.
- **No player-specific overrides.** Residuals do not persist (r ~ 0.09), so
  there is no "this guy always beats his line" term. There never will be.
- **Shrink hard.** Week 1 has no current-season games at all, so a player's own
  history is a full year stale and the roster around it has moved. The weight
  on it is n/(n+k) and then penalised again for a team change and for a coach
  change.
- **Distributions, never scalars** (invariant #3). Counts are negative binomial
  - receptions var/mean sits near 1.7-2.3 measured, well above the 1.0 a Poisson
  would force - and yardage is zero-inflated gamma.
- **The constants in core/distributions.py are TEST REFERENCE VALUES**, measured
  on a screened population (prior expanding mean >= 2.0 receptions and so on).
  They are not priors and nothing here reads them. Every parameter below is
  fitted from the as-of data.
"""
import json
from dataclasses import dataclass

from core.distributions import NegativeBinomial, ZeroInflatedGamma
from models import features

# 0.3: shrink toward position AND role. 0.2 shrank to the bare position, which
# dragged every starter toward the backups sharing his listed position - a
# systematic -0.14 bias against the market on rush attempts. The 0.2 rows are
# still in the store: predictions are immutable, so a superseded model is
# superseded by a new version, never by deleting what it said.
# 0.4: the predictive variance now carries MEAN-ESTIMATE UNCERTAINTY, and the
# shrinkage constant is fitted out of sample instead of chosen. 0.3 treated the
# shrunk mean as if it were known, so a player with 4 prior games got the same
# near-Poisson shape as one with 15 - and every week-1 prediction ignored that
# a player's mean MOVES between seasons. Measured drift variance on rush
# attempts is 8.56 against a within-player variance of ~14; leaving it out
# understated the spread by 40% and pushed P(over) down on every line above the
# mean. That was the whole of the -16.4pp model-below-market gap on rushing.
#
# The version string is DERIVED, never typed: see model_version(). Two runs on
# different code both labelled "baseline-usage-0.2" is how the ledger got
# blended across 171/161 tickets that could not be evaluated together.
MODEL_FAMILY = "baseline-usage"
MODEL_SERIES = "0.4"

# Shrinkage, FITTED OUT OF SAMPLE per stat (research/shrinkage.py). The old
# k=6 was arithmetic - "a full season is 17 games, so 0.74 weight" - not error.
#
# Fitted on the problem that is actually posed: prior = all of season N-1,
# target = week 1 of season N, pooled over 2023->2024 and 2024->2025, scored on
# P(X > line) at the lines the books actually hang. An in-season walk-forward
# fit is fitted on the easy case, where the prior is days old and the roster
# has not moved, and it recommends a much smaller k than week 1 can support.
SHRINK_GAMES_DEFAULT = 6.0
# EMPTY, DELIBERATELY. The out-of-sample fit was run and it does not support
# changing k for anything.
#
# On the week-1 holdout (prior = all of season N-1, target = week 1 of N,
# pooled 2023->2024 and 2024->2025) lowering k improves RMSE by ~1% and makes
# BIAS AGAINST THE ACTUAL OUTCOME WORSE at the high end on every stat measured:
# rush attempts +5.2% -> +6.9%, receptions +0.1% -> +5.2%. Compression toward
# the group mean looked like the fault because it is large against the player's
# own prior mean (-3.7% to -9.3%), but the player's own mean is the optimistic
# number - it is regression to the mean, and it is correct.
#
# The -16.4pp model-below-market gap on rush attempts was never a mean problem.
# It was predictive_variance() ignoring that the mean itself is uncertain; see
# MEAN_DRIFT_VAR. Fixing the variance closed half of it with k untouched.
#
# Re-open this with a fit that beats k=6 on bias, not on RMSE.
SHRINK_GAMES_BY_STAT = {}
# Season-to-season variance of a player's own MEAN, measured over players with
# >=6 games in both seasons (research/shrinkage.py). This is not sampling
# noise - it is the mean genuinely moving: role changes, scheme changes, age.
# Median relative drift is ~29% on both stats; the absolute variance differs by
# an order of magnitude because rushing volume is larger and far more volatile.
MEAN_DRIFT_VAR = {
    "rush_attempts": 8.56,     # n=101, SD 2.92
    "rush_yards": 8.56,
    "receptions": 1.12,        # n=215, SD 1.06
    "targets": 1.12,
    "receiving_yards": 1.12,
}
DEFAULT_DRIFT_VAR = 1.12
SHRINK_GAMES = SHRINK_GAMES_DEFAULT
# Dispersion needs more evidence than a mean does: a 17-game variance estimate
# is still noisy, so it shrinks on a slower clock.
SHRINK_GAMES_VMR = 12.0
# Multiplicative penalties. A player who changed teams keeps less of their own
# usage history; a player whose team changed head coach keeps less again,
# because team scheme history is zeroed by a coaching change.
TEAM_CHANGE_KEEP = 0.60
COACH_CHANGE_KEEP = 0.70
# Below this the negative binomial is not meaningful and the market is not
# really quoting a distribution either.
MIN_MEAN = 0.05
MIN_VMR = 1.05


@dataclass
class Fit:
    gsis_id: str
    stat: str
    family: str
    params: dict
    dist: object
    prior_games: int
    shrink_weight: float
    provenance: features.Provenance
    notes: str = ""

    def params_json(self) -> str:
        return json.dumps(self.params, sort_keys=True, separators=(",", ":"))


def _fingerprint() -> str:
    """Hash of the files that define what this model predicts."""
    import hashlib
    import os
    h = hashlib.sha256()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("models/baseline.py", "models/features.py",
                "core/distributions.py"):
        path = os.path.join(here, rel)
        if os.path.exists(path):
            h.update(rel.encode())
            h.update(open(path, "rb").read())
    return h.hexdigest()[:12]


def model_version() -> str:
    """`baseline-usage-0.4+<fingerprint>`. DERIVED, never typed.

    Two runs on different code both labelled "baseline-usage-0.2" is how the
    ledger ended up with 171 tickets from one build and 161 from another,
    averaged together and impossible to evaluate. A declared version cannot
    drift from the code; a derived one cannot.
    """
    return f"{MODEL_FAMILY}-{MODEL_SERIES}+{_fingerprint()}"


# Kept as a module attribute so existing callers keep working, but it is
# computed, not written down.
MODEL_VERSION = model_version()


def shrink_games(stat: str) -> float:
    return SHRINK_GAMES_BY_STAT.get(stat, SHRINK_GAMES_DEFAULT)


def _shrink_weight(n: int, k: float) -> float:
    return n / (n + k) if n > 0 else 0.0


def predictive_variance(mean, vmr, player_var, n_games, w, stat):
    """Total variance of the NEXT observation, not of a known-mean draw.

    Three sources, and 0.3 carried only the first:

      1. within-player game-to-game variance          mean * vmr
      2. sampling error in the estimated mean         w^2 * player_var / n
      3. the mean genuinely MOVING between seasons    MEAN_DRIFT_VAR[stat]

    (2) is what makes a 4-game prior wider than a 15-game one - which 0.3 did
    not do at all, giving both the same near-Poisson shape. (3) is the larger
    term for week 1 and is not noise: a back's role changes, and measured
    season-over-season drift variance on rush attempts is 8.56 against a
    within-player variance near 14.

    Leaving both out does not make the model wrong in the middle - it makes it
    overconfident in the TAILS, which is exactly where the lines are.
    """
    within = max(mean * vmr, 1e-9)
    sampling = (w ** 2) * (player_var / n_games) if n_games > 0 else 0.0
    drift = MEAN_DRIFT_VAR.get(stat, DEFAULT_DRIFT_VAR)
    return within + sampling + drift


def fit_player_stat(con, gsis_id: str, stat: str, season: int, as_of_ts: float,
                    position: str = None, team: str = None,
                    prior_seasons=(2025,)) -> Fit:
    """Fit one player's distribution for one stat, as of a moment in time.

    ONE fit per (player, stat) - every line the venues quote is then a query
    against it (invariant #4). Fitting per line would let two thresholds on the
    same player disagree with each other, which is not a model, it is a table.
    """
    prior = features.player_prior(con, gsis_id, stat, as_of_ts, prior_seasons)
    pos = (position or "").upper()
    # Shrink toward position AND role. Position alone drags every starter down
    # toward the backups who share his listed position - on carries that is
    # worth about six attempts a game.
    role = features.role_rank(con, gsis_id, stat, as_of_ts, prior_seasons)
    pos_prior = features.positional_prior(con, pos, stat, as_of_ts,
                                          prior_seasons, role=role)
    if pos_prior["n_players"] < 3:
        # Too few comparables at this exact role; widen to the position.
        pos_prior = features.positional_prior(con, pos, stat, as_of_ts,
                                              prior_seasons)
    prov = features.Provenance().merge(prior.provenance).merge(
        pos_prior["provenance"])

    notes = []
    k = shrink_games(stat)
    w = _shrink_weight(prior.n_games, k)

    # Roster change: the player's history was accumulated somewhere else.
    if team and prior.last_team and prior.last_team != team:
        w *= TEAM_CHANGE_KEEP
        notes.append(f"team {prior.last_team}->{team}")

    # Coaching change zeroes team scheme history.
    if team:
        ctx = features.team_context(con, team, season, as_of_ts)
        prov.merge(ctx["provenance"])
        if ctx["coach_changed"]:
            w *= COACH_CHANGE_KEEP
            notes.append(f"coach {ctx['prev_coach']}->{ctx['coach']}")

    pos_mean = pos_prior["mean"]
    if pos_prior["n_players"] == 0:
        # No positional target at all. Fall back to the player's own history and
        # say so, rather than shrinking toward zero and inventing a confident
        # near-certain under.
        pos_mean = prior.mean
        notes.append("no positional prior")

    mean = max(w * prior.mean + (1.0 - w) * pos_mean, MIN_MEAN)

    if stat in features.COUNT_STATS:
        wv = _shrink_weight(prior.n_games, SHRINK_GAMES_VMR)
        player_vmr = prior.var / prior.mean if prior.mean > 0 else 0.0
        pos_vmr = (pos_prior["var"] / pos_mean) if pos_mean > 0 else MIN_VMR
        vmr = max(wv * player_vmr + (1.0 - wv) * pos_vmr, MIN_VMR)
        # The distribution is over the NEXT GAME, so it must carry the
        # uncertainty in the mean as well as the spread around it.
        total_var = predictive_variance(mean, vmr, prior.var, prior.n_games,
                                        w, stat)
        vmr_total = max(total_var / mean, MIN_VMR) if mean > 0 else MIN_VMR
        dist = NegativeBinomial(mean, vmr_total)
        params = {"mean": round(mean, 6), "var_mean_ratio": round(vmr_total, 6),
                  "vmr_within": round(vmr, 6),
                  "var_total": round(total_var, 6)}
        family = "negative_binomial"
    else:
        p_zero = min(max(w * prior.p_zero + (1.0 - w) * pos_prior["p_zero"],
                         0.0), 0.95)
        var = max(w * prior.var + (1.0 - w) * pos_prior["var"], mean)
        var = predictive_variance(mean, var / mean if mean > 0 else 1.0,
                                  prior.var, prior.n_games, w, stat)
        dist = ZeroInflatedGamma.from_overall_moments(mean, var, p_zero)
        params = {"mean": round(mean, 6), "variance": round(var, 6),
                  "p_zero": round(p_zero, 6)}
        family = "zero_inflated_gamma"

    notes.append(f"role{role}; k{k:g}")
    return Fit(gsis_id, stat, family, params, dist, prior.n_games, round(w, 4),
               prov, "; ".join(notes))


def rebuild(family: str, params: dict):
    """Reconstruct a distribution from a stored prediction row.

    The reason predictions store parameters rather than a probability: a stored
    scalar can answer exactly one question, and the one you want later is never
    the one you cached.
    """
    if family == "negative_binomial":
        return NegativeBinomial(params["mean"], params["var_mean_ratio"])
    if family == "zero_inflated_gamma":
        return ZeroInflatedGamma.from_overall_moments(
            params["mean"], params["variance"], params["p_zero"])
    raise ValueError(f"unknown family {family!r}")
