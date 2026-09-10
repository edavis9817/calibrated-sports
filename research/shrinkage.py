"""How much of a player's own usage should the model keep?

    python -m research.shrinkage --compression      # the acceptance table
    python -m research.shrinkage --fit              # fit k out of sample
    python -m research.shrinkage --all

THE QUESTION. The model predicts `w * own_mean + (1-w) * group_mean` with
`w = n/(n+k)`. k was set by feel - "a full season is 17 games, so k=6 puts a
complete season at 0.74" - which is a statement about arithmetic, not about
error. This fits it against held-out weeks instead.

THE DESIGN. Walk forward through 2025. At week W a player's prior is weeks
1..W-1 and the target is week W itself, which the prior cannot see. Sweep k,
score every (player, stat, week) with n >= MIN_PRIOR_GAMES, and take the k that
minimises held-out error. Nothing here reads week W to build week W's prior;
that is the whole point and it is asserted in the tests.

WHY COMPRESSION IS THE TEST THAT MATTERS. A pooled average hides both
directions at once. Shrinking toward a group mean lifts low-usage players and
drags high-usage ones down, and an aggregate over both reports the difference
of two errors - "12% low overall" was that difference, not the size of either.
Bucketing by the player's OWN prior mean separates them.

ROLE COMES FROM SNAPS, NOT FROM THE STAT. Ranking a player by the volume of
the very stat being predicted makes the shrinkage target a function of his own
outcome: he is shrunk toward players who already looked like him, which quietly
undoes the shrinkage it is supposed to inform. Snap share is an independent
measure of the same thing.
"""
import argparse
import math
import sqlite3
import statistics
from collections import defaultdict

import config

# The stats the venues actually quote, and the columns behind them.
STATS = {
    "receptions": "receptions",
    "targets": "targets",
    "rush_attempts": "carries",
    "receiving_yards": "receiving_yards",
    "rush_yards": "rushing_yards",
    "pass_attempts": "attempts",
    "passing_yards": "passing_yards",
    "completions": "completions",
}
COUNT_STATS = {"receptions", "targets", "rush_attempts", "pass_attempts",
               "completions"}

# WHO THE MODEL IS FOR. Derived from what the books actually quote, not
# chosen: every offensive player-week includes offensive linemen with zero
# receptions forever, and fitting on that population makes the terciles all
# zero and the whole exercise a study of linemen. Positions below are the ones
# carrying more than a handful of quoted props across 2023-2025.
ELIGIBLE = {
    "receptions": {"WR", "TE", "RB", "FB"},
    "targets": {"WR", "TE", "RB", "FB"},
    "receiving_yards": {"WR", "TE", "RB", "FB"},
    "rush_attempts": {"RB", "QB", "WR", "FB"},
    "rush_yards": {"RB", "QB", "WR", "FB"},
    "pass_attempts": {"QB"},
    "passing_yards": {"QB"},
    "completions": {"QB"},
}
# And a usage floor at the LOWEST LINE THE BOOKS HANG. Below it there is no
# market to be right or wrong about, and including those player-weeks buries
# the players who are quoted under a pile of players who are not.
MIN_OWN_MEAN = {
    "receptions": 0.5, "targets": 0.5, "receiving_yards": 0.5,
    "rush_attempts": 0.5, "rush_yards": 0.5,
    "pass_attempts": 5.0, "passing_yards": 50.0, "completions": 3.0,
}

# A prior of one game is not a prior. Below this the fit is measuring noise.
MIN_PRIOR_GAMES = 3
# Role buckets by snap share, which is what "bell cow / committee / rotational"
# actually means. Rank within (team, position) rather than an absolute share,
# because a share is not comparable across positions.
ROLE_CAP = 4
# Players a group prior needs before it is a prior rather than an anecdote.
MIN_GROUP = 3

K_GRID = [0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 60]


def _ro():
    return sqlite3.connect("file:%s?mode=ro" % config.DB_PATH, uri=True)


# --------------------------------------------------------------------------
# the panel
# --------------------------------------------------------------------------

_PANEL_SQL = """
SELECT w.gsis_id, w.week, w.position, w.team,
       %s
  FROM nfl_player_week w
 WHERE w.season = ? AND w.season_type = 'REG'
"""

_SNAP_SQL = """
SELECT x.gsis_id, s.week, s.team, s.position, s.offense_pct
  FROM nfl_snap_counts s
  JOIN player_xwalk x ON x.pfr_id = s.pfr_player_id
 WHERE s.season = ? AND s.offense_pct IS NOT NULL
"""


def load_panel(season=2025):
    """One row per (player, week) with every stat, plus snap share.

    Loaded once and swept in memory. Re-querying per k per week would be a few
    hundred thousand round trips to answer a question about sixteen constants.
    """
    con = _ro()
    cols = ", ".join(f"w.{c}" for c in STATS.values())
    rows = con.execute(_PANEL_SQL % cols, (season,)).fetchall()
    panel = defaultdict(dict)          # gsis -> week -> {stat: value}
    meta = {}                          # gsis -> (position, team)
    for r in rows:
        gsis, week, pos, team = r[0], r[1], r[2], r[3]
        if gsis is None or week is None:
            continue
        vals = {s: (r[4 + i] if r[4 + i] is not None else 0.0)
                for i, s in enumerate(STATS)}
        panel[gsis][week] = vals
        meta.setdefault(gsis, ((pos or "").upper(), team))
    snaps = defaultdict(dict)          # gsis -> week -> offense_pct
    for gsis, week, team, pos, pct in con.execute(_SNAP_SQL, (season,)):
        if gsis is not None and week is not None:
            snaps[gsis][week] = float(pct)
    con.close()
    return panel, meta, snaps


def snap_roles(panel, meta, snaps, upto_week):
    """{gsis: role} from snap share over weeks < upto_week.

    Rank within (team, position): 1 is the highest snap share on that team at
    that position, capped at ROLE_CAP. An absolute share is not comparable
    across positions - 60% is a bell cow at RB and a rotational piece at WR.
    """
    by_group = defaultdict(list)
    for gsis, (pos, team) in meta.items():
        pcts = [v for w, v in snaps.get(gsis, {}).items() if w < upto_week]
        if not pcts:
            continue
        by_group[(team, pos)].append((statistics.fmean(pcts), gsis))
    roles = {}
    for group, members in by_group.items():
        members.sort(reverse=True)
        for i, (_pct, gsis) in enumerate(members, start=1):
            roles[gsis] = min(i, ROLE_CAP)
    return roles


def _prior(panel_player, stat, upto_week):
    """(n, mean) over weeks strictly before upto_week. The as-of guard."""
    vals = [v[stat] for w, v in panel_player.items() if w < upto_week]
    if not vals:
        return 0, 0.0
    return len(vals), statistics.fmean(vals)


def observations(panel, meta, snaps, stat, weeks=range(2, 19)):
    """Every held-out (own_mean, n, group_mean, actual) for one stat.

    `group_mean` is the average of the OTHER players' own prior means in the
    same (position, role) bucket - leave-one-out, so a player is never part of
    the target he is shrunk toward. Including him makes the target chase him,
    which flatters shrinkage at exactly the high-usage end where it hurts most.
    """
    out = []
    for week in weeks:
        roles = snap_roles(panel, meta, snaps, week)
        eligible = ELIGIBLE.get(stat, set())
        floor = MIN_OWN_MEAN.get(stat, 0.0)
        priors = {}
        for gsis, weeks_played in panel.items():
            pos, _team = meta[gsis]
            if pos not in eligible:
                continue
            n, mean = _prior(weeks_played, stat, week)
            if n >= MIN_PRIOR_GAMES and mean >= floor:
                priors[gsis] = (n, mean)
        groups = defaultdict(list)
        for gsis, (n, mean) in priors.items():
            pos, _team = meta[gsis]
            role = roles.get(gsis)
            if role is None:
                continue
            groups[(pos, role)].append((gsis, mean))
        for (pos, role), members in groups.items():
            if len(members) < MIN_GROUP + 1:
                continue
            total = sum(m for _g, m in members)
            for gsis, own in members:
                actual = panel[gsis].get(week)
                if actual is None:
                    continue          # did not play; nothing to score against
                loo = (total - own) / (len(members) - 1)
                out.append((gsis, week, priors[gsis][0], own, loo,
                            actual[stat]))
    return out


# --------------------------------------------------------------------------
# fitting k out of sample
# --------------------------------------------------------------------------

def score_k(obs, k):
    """Mean squared held-out error at this k, and the mean signed bias."""
    if not obs:
        return None
    se = bias = 0.0
    for _g, _w, n, own, group, actual in obs:
        wt = n / (n + k)
        pred = wt * own + (1.0 - wt) * group
        se += (pred - actual) ** 2
        bias += pred - actual
    return {"k": k, "rmse": math.sqrt(se / len(obs)), "bias": bias / len(obs),
            "n": len(obs)}


def fit_k(obs, grid=K_GRID):
    scored = [score_k(obs, k) for k in grid]
    scored = [s for s in scored if s]
    best = min(scored, key=lambda s: s["rmse"])
    # k=0 is "keep all of your own history"; a huge k is "keep none of it".
    # Reporting both ends makes the shape of the curve visible rather than
    # just its argmin, which is what tells you whether the fit is meaningful.
    return best, scored


# --------------------------------------------------------------------------
# the compression test - the acceptance criterion
# --------------------------------------------------------------------------

def compression(obs, k, buckets=3):
    """Model mean vs the player's own mean, bucketed by that own mean.

    THE test. Shrinkage lifts the low bucket and drags the high one, and an
    aggregate over both reports the difference of two errors rather than the
    size of either - which is how "12% low overall" got mistaken for a single
    directional bias.

    `actual` is the held-out week, so the third column says whether the model's
    compression was WRONG or whether the player's own mean was the optimistic
    number. Both matter and they are not the same claim.
    """
    if not obs:
        return []
    owns = sorted(o[3] for o in obs)
    cuts = [owns[int(len(owns) * i / buckets)] for i in range(1, buckets)]
    if len(set(cuts)) != len(cuts):
        # Degenerate terciles mean the population is mostly one value - which
        # is what a missing usage screen looks like, and it silently reports
        # every player in one bucket instead of failing.
        raise ValueError(f"tercile cuts are not distinct ({cuts}); the "
                         f"population is not screened to quoted players")

    def which(v):
        for i, c in enumerate(cuts):
            if v < c:
                return i
        return buckets - 1

    rows = [[] for _ in range(buckets)]
    for g, wk, n, own, group, actual in obs:
        wt = n / (n + k)
        rows[which(own)].append((wt * own + (1.0 - wt) * group, own, actual))
    names = ["low", "mid", "high"] if buckets == 3 else [
        f"q{i+1}" for i in range(buckets)]
    out = []
    for i, v in enumerate(rows):
        if not v:
            continue
        model = statistics.fmean(x[0] for x in v)
        own = statistics.fmean(x[1] for x in v)
        actual = statistics.fmean(x[2] for x in v)
        out.append({"bucket": names[i], "n": len(v), "model": model,
                    "own": own, "actual": actual,
                    "vs_own": (model - own) / own if own else 0.0,
                    "vs_actual": (model - actual) / actual if actual else 0.0})
    return out


def _print_compression(title, table):
    print("\n%s" % title)
    print("  %-7s%8s%10s%10s%10s%10s%10s"
          % ("bucket", "n", "model", "own mean", "actual", "vs own", "vs act"))
    for r in table:
        print("  %-7s%8s%10.3f%10.3f%10.3f%9.1f%%%9.1f%%"
              % (r["bucket"], format(r["n"], ","), r["model"], r["own"],
                 r["actual"], 100 * r["vs_own"], 100 * r["vs_actual"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--compression", action="store_true")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--current-k", type=float, default=6.0)
    args = ap.parse_args()

    print("loading %d panel..." % args.season, flush=True)
    panel, meta, snaps = load_panel(args.season)
    print("  %s players, %s with snap share"
          % (format(len(panel), ","), format(len(snaps), ",")))

    fitted = {}
    for stat in STATS:
        obs = observations(panel, meta, snaps, stat)
        if len(obs) < 500:
            print(f"\n{stat}: only {len(obs)} held-out observations, skipping")
            continue
        best, scored = fit_k(obs, K_GRID)
        fitted[stat] = best
        print("\n" + "=" * 72)
        print(f"{stat.upper()}   {len(obs):,} held-out player-weeks "
              f"({args.season} weeks 2-18, walk-forward)")
        print("=" * 72)

        if args.fit or args.all:
            print("\n  held-out error by shrinkage constant:")
            print("    %-7s%10s%10s" % ("k", "rmse", "bias"))
            for s in scored:
                mark = "  <- best" if s["k"] == best["k"] else ""
                cur = "  (current)" if s["k"] == args.current_k else ""
                print("    %-7g%10.4f%+10.4f%s%s"
                      % (s["k"], s["rmse"], s["bias"], mark, cur))
            cur = score_k(obs, args.current_k)
            print(f"\n  fitted k = {best['k']:g} (rmse {best['rmse']:.4f}), "
                  f"current k = {args.current_k:g} (rmse {cur['rmse']:.4f}), "
                  f"{100*(cur['rmse']-best['rmse'])/best['rmse']:+.2f}% worse")

        if args.compression or args.all:
            _print_compression(f"  COMPRESSION at current k={args.current_k:g}",
                               compression(obs, args.current_k))
            _print_compression(f"  COMPRESSION at fitted  k={best['k']:g}",
                               compression(obs, best["k"]))

    print("\n" + "=" * 72)
    print("FITTED SHRINKAGE CONSTANTS")
    print("=" * 72)
    print("  %-18s%8s%10s%10s" % ("stat", "k", "rmse", "n"))
    for stat, b in fitted.items():
        print("  %-18s%8g%10.4f%10s"
              % (stat, b["k"], b["rmse"], format(b["n"], ",")))
    return fitted


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# the objective that matches the use
# --------------------------------------------------------------------------

def quoted_lines(stat):
    """The lines the books actually hang for this stat, with their weights.

    Fitting on RMSE of the mean optimises a quantity nobody trades. What is
    traded is P(X > line) at the handful of thresholds books hang, and a model
    can have better RMSE and worse P(X > 5.5) - the two objectives disagreed on
    every receiving stat here.
    """
    con = _ro()
    rows = con.execute(
        "SELECT line, COUNT(*) FROM outcomes WHERE entity_type='player' "
        "AND stat = ? AND line IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 40",
        (stat,)).fetchall()
    con.close()
    return rows


def empirical_vmr(obs):
    """Variance/mean over the SCREENED held-out actuals. The dispersion k does
    not control.

    Over the whole panel this was 11.4 for rush attempts, because the panel is
    mostly receivers and linemen with zero carries every week - a variance
    driven by players the model never prices. On the screened population it is
    the dispersion of the players who are actually quoted.
    """
    vals = [o[5] for o in obs]
    if not vals:
        return 1.05
    m = statistics.fmean(vals)
    if m <= 0:
        return 1.05
    return max(statistics.pvariance(vals) / m, 1.05)


def score_k_prob(obs, k, lines, vmr, count_stat=True):
    """Brier and log loss of P(X > line), averaged over the quoted lines.

    Each held-out player-week is scored at every line the books hang, weighted
    by how often they hang it. That is the loss the ledger actually pays.
    """
    from core.distributions import NegativeBinomial
    if not obs or not lines:
        return None
    total_w = sum(c for _l, c in lines)
    brier = ll = 0.0
    n = 0
    cache = {}
    for _g, _w, ng, own, group, actual in obs:
        wt = ng / (ng + k)
        mean = max(wt * own + (1.0 - wt) * group, 0.05)
        key = round(mean, 3)
        dist = cache.get(key)
        if dist is None:
            dist = cache[key] = NegativeBinomial(key, vmr)
        for line, cnt in lines:
            p = min(max(dist.prob_over(line), 1e-6), 1 - 1e-6)
            hit = 1.0 if actual > line else 0.0
            w = cnt / total_w
            brier += w * (p - hit) ** 2
            ll -= w * (math.log(p) if hit else math.log(1 - p))
        n += 1
    return {"k": k, "brier": brier / n, "log_loss": ll / n, "n": n}


def fit_k_prob(obs, stat, panel=None, grid=K_GRID):
    lines = quoted_lines(stat)
    vmr = empirical_vmr(obs)
    scored = [s for s in (score_k_prob(obs, k, lines, vmr) for k in grid) if s]
    if not scored:
        return None, [], lines, vmr
    return min(scored, key=lambda s: s["brier"]), scored, lines, vmr


# --------------------------------------------------------------------------
# the week-1 problem, which is the one Sunday actually poses
# --------------------------------------------------------------------------

def week1_observations(prior_season, target_season, stat):
    """Prior = ALL of `prior_season`; target = week 1 of `target_season`.

    THE FIT THAT MATCHES SUNDAY. Everything above walks forward inside one
    season, where a prior is days old and the roster around it has not moved.
    Week 1 is a different question: the prior is a full year stale, the player
    may have changed teams, the coordinator may have changed, and there is no
    current-season data at all. A k fitted in-season is fitted on the easy case
    and will keep too much of a stale prior.
    """
    prior_panel, prior_meta, prior_snaps = load_panel(prior_season)
    tgt_panel, _tgt_meta, _s = load_panel(target_season)
    roles = snap_roles(prior_panel, prior_meta, prior_snaps, upto_week=99)
    eligible = ELIGIBLE.get(stat, set())
    floor = MIN_OWN_MEAN.get(stat, 0.0)

    priors = {}
    for gsis, weeks in prior_panel.items():
        pos, _team = prior_meta[gsis]
        if pos not in eligible:
            continue
        n, mean = _prior(weeks, stat, upto_week=99)
        if n >= MIN_PRIOR_GAMES and mean >= floor:
            priors[gsis] = (n, mean)

    groups = defaultdict(list)
    for gsis, (n, mean) in priors.items():
        pos, _team = prior_meta[gsis]
        role = roles.get(gsis)
        if role is not None:
            groups[(pos, role)].append((gsis, mean))

    out = []
    for _key, members in groups.items():
        if len(members) < MIN_GROUP + 1:
            continue
        total = sum(m for _g, m in members)
        for gsis, own in members:
            wk1 = tgt_panel.get(gsis, {}).get(1)
            if wk1 is None:
                continue        # not on a week 1 roster; nothing to score
            loo = (total - own) / (len(members) - 1)
            out.append((gsis, 1, priors[gsis][0], own, loo, wk1[stat]))
    return out
