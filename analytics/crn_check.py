"""Does sharing bootstrap draws across subjects corrupt a side-by-side reading?

    python -m analytics.crn_check
    python -m analytics.crn_check --pairs 60 --reps 200

THE QUESTION, ASKED BY ETHAN 2026-09-17. `histogram_bootstrap` cached its
resampling matrix by block count, so every subject with the same number of
games was resampled with the SAME draws - common random numbers. The comment
said that was safe because nothing published is a contrast between two
subjects. That is wrong about the product: the site's whole idiom is two
players' intervals side by side, and a reader comparing them IS performing an
informal contrast.

WHAT CRN DOES AND DOES NOT DO. It changes no interval's centre and, in
expectation, no interval's width - with infinite draws it has no effect at all.
What it changes is the CORRELATION of the Monte Carlo error between subjects,
and therefore the variance of the gap a reader eyeballs:

    var(A.lo - B.hi) = var(A.lo) + var(B.hi) - 2 cov(A.lo, B.hi)

Positive covariance shrinks that and makes the naive overlap call MORE stable -
conservative. Negative covariance inflates it and makes two intervals separate
more often than they should - anti-conservative.

WHY NEGATIVE COVARIANCE IS THE CASE TO WORRY ABOUT HERE, not a hypothetical.
Two receivers on one team share a denominator: team targets. Their per-game
shares are mechanically negatively related, because they sum to at most one. If
the same resample over-weights a game, it pushes one player's share up and the
other's down. Under CRN those two errors are then negatively correlated, the
gap between their interval endpoints is noisier than it looks, and a reader
sees separation that is not there.

That coupling needs the shared matrix's column j to mean the same GAME for both
players. It does: both players' block dictionaries are built from the same
`GROUP BY ... game_id` ordering, so two teammates who played the same games get
the same game order.

THREE ARMS, MEASURED AGAINST A HIGH-DRAW REFERENCE:

    cached        one matrix per block count - what shipped
    permuted      the same matrix, columns permuted per subject. The counts are
                  exchangeable, so this preserves each subject's distribution
                  exactly while scrambling which game a column refers to.
    independent   a fresh matrix per subject

Two populations: TEAMMATE pairs, which share a denominator, and CONTROL pairs
from different teams in different seasons, which do not.

The answer belongs on the page, not in a comment.
"""
import argparse
import sys

import numpy as np

from analytics import paths

DRAWS = 2000
REFERENCE_DRAWS = 120000
MIN_GAMES = 8


def _counts(n, draws, seed):
    rng = np.random.default_rng(seed)
    return rng.multinomial(n, np.full(n, 1.0 / n), size=draws).astype(float)


def _interval(mat, counts, conf=0.95):
    """(lo, hi) of the share statistic under a given resampling matrix."""
    rep = counts @ mat
    with np.errstate(invalid="ignore", divide="ignore"):
        vals = np.where(rep[:, 1] > 0, rep[:, 0] / np.maximum(rep[:, 1], 1e-9),
                        np.nan)
    vals = np.sort(vals[~np.isnan(vals)])
    if len(vals) == 0:
        return None
    lo = vals[int((1 - conf) / 2 * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 + conf) / 2 * len(vals)))]
    return float(lo), float(hi)


def _subject_matrix(blocks):
    return np.vstack([np.array([float(a), float(b)]) for a, b in blocks])


def load_pairs(con, season, n_pairs, min_games=MIN_GAMES):
    """(teammate_pairs, control_pairs) as [(name_a, mat_a, name_b, mat_b)].

    A teammate pair shares a team, a season and a game list, so the shared
    matrix's columns line up game for game. A control pair does not.
    """
    team_tot = {}
    for game, team, tot in con.execute(
            "SELECT game_id, team, SUM(is_target) FROM f_play_usage "
            "WHERE role='receiver' AND season=? GROUP BY game_id, team",
            (season,)):
        team_tot[(game, team)] = tot or 0
    per = {}
    for player, game, team, got in con.execute(
            "SELECT player_id, game_id, team, SUM(is_target) FROM f_play_usage "
            "WHERE role='receiver' AND season=? "
            "GROUP BY player_id, game_id, team", (season,)):
        d = team_tot.get((game, team), 0)
        if d:
            per.setdefault((player, team), {})[game] = (got or 0, d)
    per = {k: v for k, v in per.items() if len(v) >= min_games}

    by_team = {}
    for (player, team), games in per.items():
        by_team.setdefault(team, []).append((player, games))

    mates, controls = [], []
    for team, players in sorted(by_team.items()):
        for i in range(len(players)):
            for j in range(i + 1, len(players)):
                a, ga = players[i]
                b, gb = players[j]
                # Same games, in the same order, is what makes the columns
                # line up - which is the condition being tested.
                if list(ga) != list(gb):
                    continue
                mates.append((a, _subject_matrix(ga.values()),
                              b, _subject_matrix(gb.values())))
    flat = sorted(per.items(), key=lambda kv: str(kv[0]))
    for i in range(0, len(flat) - 1, 2):
        (pa, ta), ga = flat[i]
        (pb, tb), gb = flat[i + 1]
        if ta == tb or len(ga) != len(gb):
            continue
        controls.append((pa, _subject_matrix(ga.values()),
                         pb, _subject_matrix(gb.values())))
    rng = np.random.default_rng(11)
    for pool in (mates, controls):
        rng.shuffle(pool)
    return mates[:n_pairs], controls[:n_pairs]


def per_game_share_corr(pairs):
    """Mean correlation of the two subjects' per-game shares, across pairs.

    The mechanism, measured rather than asserted. Teammates share a denominator
    so this should be negative; controls should be near zero.
    """
    out = []
    for _na, ma, _nb, mb in pairs:
        sa = ma[:, 0] / np.maximum(ma[:, 1], 1e-9)
        sb = mb[:, 0] / np.maximum(mb[:, 1], 1e-9)
        if sa.std() > 0 and sb.std() > 0:
            out.append(float(np.corrcoef(sa, sb)[0, 1]))
    return float(np.mean(out)) if out else float("nan")


def measure(pairs, reps, draws=DRAWS, base_seed=5000):
    """{arm: dict} of the quantities that decide conservative vs anti-.

    THE DECIDING NUMBER IS A VARIANCE RATIO, not a false-positive rate. The
    first version of this counted how often an arm declared separation among
    truly-overlapping pairs; the base rate was 0.003 on 13 pairs and nothing
    was distinguishable from anything. What a reader's eye actually responds to
    is the GAP between the near endpoints, and its Monte Carlo variance is
    estimable to three figures from the same run:

        D = A.lo - B.hi     (A the higher subject)

        var(D) = var(A.lo) + var(B.hi) - 2 cov(A.lo, B.hi)

    So `var_ratio` = var(D) under the arm, over var(D) under independent draws.
    Below 1 means the shared draws make the comparison LESS noisy than a reader
    would get from independent bootstraps - conservative, fewer spurious
    separations. Above 1 means more noise in the comparison than the intervals
    themselves advertise - anti-conservative, and the caching has to go.

    `endpoint_var_ratio` is the control on the fix: an arm that changed a
    SINGLE subject's interval would be wrong for a different reason, so each
    arm's own endpoint variance is reported against independent too. It must
    be ~1 everywhere.
    """
    arms = ("cached", "permuted", "independent")
    D = {a: [] for a in arms}
    E = {a: [] for a in arms}

    for idx, (na, ma, nb, mb) in enumerate(pairs):
        n = len(ma)
        # orient the pair so A is the higher of the two point estimates
        pa = ma[:, 0].sum() / max(ma[:, 1].sum(), 1e-9)
        pb = mb[:, 0].sum() / max(mb[:, 1].sum(), 1e-9)
        if pb > pa:
            ma, mb = mb, ma
        d = {a: [] for a in arms}
        e = {a: [] for a in arms}
        for r in range(reps):
            shared = _counts(n, draws, base_seed + r * 131 + n)
            for arm in arms:
                if arm == "cached":
                    ca = cb = shared
                elif arm == "permuted":
                    pr = np.random.default_rng(base_seed + r * 131 + n + idx)
                    ca = shared[:, pr.permutation(n)]
                    cb = shared[:, pr.permutation(n)]
                else:
                    ca = _counts(n, draws, base_seed + r * 977 + n + 7717)
                    cb = _counts(n, draws, base_seed + r * 977 + n + 31337)
                xa, xb = _interval(ma, ca), _interval(mb, cb)
                if xa is None or xb is None:
                    continue
                d[arm].append(xa[0] - xb[1])    # the gap a reader eyeballs
                e[arm].append(xa[0])            # one subject's own endpoint
        for arm in arms:
            if len(d[arm]) > 5:
                D[arm].append(float(np.var(d[arm])))
                E[arm].append(float(np.var(e[arm])))

    base_d = float(np.mean(D["independent"])) if D["independent"] else float("nan")
    base_e = float(np.mean(E["independent"])) if E["independent"] else float("nan")
    out = {}
    for arm in arms:
        vd = float(np.mean(D[arm])) if D[arm] else float("nan")
        ve = float(np.mean(E[arm])) if E[arm] else float("nan")
        out[arm] = {"gap_var": vd, "var_ratio": vd / base_d if base_d else float("nan"),
                    "endpoint_var_ratio": ve / base_e if base_e else float("nan"),
                    "pairs": len(D[arm])}
    return out


def synthetic_pairs(rho, n_games=17, n_pairs=40, seed=7):
    """Two subjects sharing an EXACT denominator, with per-game shares
    correlated at `rho`.

    The real-data arm answers "what is the effect on this slate"; this answers
    "what is the effect as a function of the thing that causes it", which is
    the part that generalises. Teammates on one team measure about -0.2, but
    two backs splitting one workload can be far more negative, and the page
    should not quote a number that only holds at one point on the curve.
    """
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_pairs):
        z1 = rng.normal(size=n_games)
        z2 = rho * z1 + np.sqrt(max(0.0, 1 - rho * rho)) * rng.normal(size=n_games)
        denom = rng.integers(25, 40, size=n_games).astype(float)
        sa = np.clip(0.22 + 0.06 * z1, 0.02, 0.6)
        sb = np.clip(0.20 + 0.06 * z2, 0.02, 0.6)
        ma = np.column_stack([np.round(sa * denom), denom])
        mb = np.column_stack([np.round(sb * denom), denom])
        out.append(("a", ma, "b", mb))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--pairs", type=int, default=40)
    ap.add_argument("--reps", type=int, default=120)
    ap.add_argument("--synthetic", action="store_true",
                    help="sweep the effect against the correlation that causes "
                         "it, which is the part that generalises")
    a = ap.parse_args(argv)
    if a.synthetic:
        print("synthetic pairs sharing an exact denominator, %d reps, %d draws"
              % (a.reps, DRAWS))
        print("  %6s  %11s  %11s  %14s"
              % ("rho", "cached", "permuted", "endpoint ratio"))
        for rho in (-0.9, -0.6, -0.3, 0.0, 0.3, 0.6, 0.9):
            got = measure(synthetic_pairs(rho, n_pairs=a.pairs), a.reps)
            print("  %+6.1f  %11.3f  %11.3f  %14.3f"
                  % (rho, got["cached"]["var_ratio"],
                     got["permuted"]["var_ratio"],
                     got["cached"]["endpoint_var_ratio"]))
        return 0
    con = paths.connect(read_only=True)
    mates, controls = load_pairs(con, a.season, a.pairs)
    if not mates:
        raise SystemExit("no teammate pairs found for %d - an empty check is "
                         "not a pass" % a.season)
    print("season %d: %d teammate pairs, %d control pairs, %d reps, %d draws"
          % (a.season, len(mates), len(controls), a.reps, DRAWS))
    for label, pairs in (("TEAMMATES (shared denominator)", mates),
                         ("CONTROL (different teams)", controls)):
        if not pairs:
            print("\n%s: none available" % label)
            continue
        got = measure(pairs, a.reps)
        corr = per_game_share_corr(pairs)
        print("\n%s" % label)
        print("  mean per-game share correlation within a pair: %+.3f" % corr)
        print("  %-13s %10s  %11s  %14s  %s"
              % ("arm", "gap var", "VAR RATIO", "endpoint ratio", "pairs"))
        for arm in ("cached", "permuted", "independent"):
            g = got[arm]
            print("  %-13s %10.3e  %11.3f  %14.3f  %5d"
                  % (arm, g["gap_var"], g["var_ratio"],
                     g["endpoint_var_ratio"], g["pairs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
