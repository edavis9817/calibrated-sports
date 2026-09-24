"""How often a team-season interval built on n games covers the full-season value.

    python -m research.a25_small_n_coverage

WHY. `analytics.team_units` publishes a team two games into a season. The brief
expected those intervals to be wide; a percentile block bootstrap over two
blocks can only span the two games, so it is as narrow as the two games happen
to agree. This measures it instead of arguing it.

DESIGN. Each complete team-season (2023-2025 regular season, 17 games) is a
finite population and its full-season ratio of sums is the target. Draw n of
its games without replacement, build the interval two ways, and count how often
it contains the target:

    boot   the percentile block bootstrap `team_units.compute` used first
    t      a cluster-robust ratio interval: est +/- t(n-1) * se, where
           se^2 = n/(n-1) * sum((num_g - est*den_g)^2) / (sum den_g)^2

Sampling without replacement from 17 makes the target a finite-population
value, so both methods are slightly conservative at larger n. That bias is the
same for both and is small at n <= 5, where the question lives.
"""
import random
import sys

import numpy as np

from analytics import paths, team_units
from analytics.intervals import histogram_bootstrap, _T975

SEASONS = (2023, 2025)
NS = (2, 3, 4, 5, 8, 12)
REPS = 25
KEYS = ("team_units.neutral_pass_rate.early_downs.by_season",
        "team_units.epa_per_play.offense.by_season",
        "team_units.sack_or_hit_rate.defense.by_season")


def t_interval(games):
    num = np.array([g[0] for g in games])
    den = np.array([g[1] for g in games])
    n = len(games)
    est = num.sum() / den.sum()
    resid = num - est * den
    se = np.sqrt(n / (n - 1) * (resid ** 2).sum()) / den.sum()
    t = _T975[n - 1] if n - 1 < len(_T975) else 1.96
    return est, est - t * se, est + t * se


def boot_interval(games, subject):
    vecs = {i: np.array(g) for i, g in enumerate(games)}
    e = histogram_bootstrap(vecs, {"r": lambda v: v[0] / v[1] if v[1] else None},
                            draws=1000, subject=subject)["r"]
    return e.est, e.lo, e.hi


def main():
    con = paths.connect(read_only=True)
    specs = {s.key: s for s in team_units.SPECS}
    rng = random.Random(20260924)
    print("coverage of the full-season value by a 95%% interval on n games, "
          "%d-%d REG, %d draws per team-season" % (SEASONS + (REPS,)))
    for key in KEYS:
        blocks = team_units._blocks(con, specs[key], *SEASONS)
        full = {k: list(v.values()) for k, v in blocks.items() if len(v) >= 16}
        if len(full) < 90:
            raise SystemExit("%s: only %d complete team-seasons - expected ~96"
                             % (key, len(full)))
        print("\n%s  (%d team-seasons)" % (key, len(full)))
        print("  %3s  %8s %8s   %10s %10s" % ("n", "boot", "t", "boot width",
                                              "t width"))
        for n in NS:
            hit_b = hit_t = tot = 0
            wb, wt = [], []
            for (team, season), games in sorted(full.items()):
                truth = sum(g[0] for g in games) / sum(g[1] for g in games)
                for r in range(REPS):
                    pick = rng.sample(games, n)
                    _e, lo, hi = boot_interval(pick, "%s|%s|%d" % (team, season, r))
                    _e2, lo2, hi2 = t_interval(pick)
                    hit_b += lo <= truth <= hi
                    hit_t += lo2 <= truth <= hi2
                    wb.append(hi - lo)
                    wt.append(hi2 - lo2)
                    tot += 1
            print("  %3d  %8.3f %8.3f   %10.3f %10.3f"
                  % (n, hit_b / tot, hit_t / tot, float(np.median(wb)),
                     float(np.median(wt))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
