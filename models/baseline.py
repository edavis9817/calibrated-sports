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
MODEL_VERSION = "baseline-usage-0.3"

# Shrinkage. A full season is 17 games, so k=6 puts a player with a complete
# 2025 (n=17) at 0.74 weight on their own history and a 4-game sample at 0.40.
SHRINK_GAMES = 6.0
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


def _shrink_weight(n: int, k: float) -> float:
    return n / (n + k) if n > 0 else 0.0


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
    w = _shrink_weight(prior.n_games, SHRINK_GAMES)

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
        dist = NegativeBinomial(mean, vmr)
        params = {"mean": round(mean, 6), "var_mean_ratio": round(vmr, 6)}
        family = "negative_binomial"
    else:
        p_zero = min(max(w * prior.p_zero + (1.0 - w) * pos_prior["p_zero"],
                         0.0), 0.95)
        var = max(w * prior.var + (1.0 - w) * pos_prior["var"], mean)
        dist = ZeroInflatedGamma.from_overall_moments(mean, var, p_zero)
        params = {"mean": round(mean, 6), "variance": round(var, 6),
                  "p_zero": round(p_zero, 6)}
        family = "zero_inflated_gamma"

    notes.append(f"role{role}")
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
