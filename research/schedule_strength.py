"""Does an early-season defense effect say anything about the rest of the season?

    python -m research.schedule_strength                 # 2009-2025, k = 2, 4, 8
    python -m research.schedule_strength --seasons 2019-2025 --k 2 4

THE QUESTION a rest-of-season schedule depends on, and the one no strength-of-
schedule table checks. `analytics.schedule` measures each defense's effect on
opposing WR / TE / RB efficiency and opportunity with an interval. A reader who
uses it in week k is betting that the effect measured over weeks 1..k is the
effect the defense will have over weeks k+1..18. Two things can make that bet
bad, and this script measures both:

1. **Persistence.** The correlation, across defenses, between the effect over
   weeks 1..k and the effect over weeks k+1..18 - two halves computed on
   DISJOINT games with DISJOINT baselines, so a shared sample cannot manufacture
   agreement. Alongside it, the genre's own predictor: the defense's effect over
   the WHOLE previous season against the same weeks k+1..18.

2. **Calibration of the interval.** If the effect is stable within a season and
   the interval is honest, z = (early - rest) / sqrt(se_early^2 + se_rest^2)
   has SD 1 and |z| > 1.96 about 5% of the time. SD above 1 means the published
   interval understates how far the rest of the season lands from the estimate
   - which is the quantity a reader consumes, whichever of "the interval is too
   narrow" and "the effect drifts" is the cause. This script cannot separate the
   two and does not claim to. se = (hi - lo) / 3.92.

   Measured for BOTH interval constructions in `analytics.schedule`: `raw`
   (Dirichlet percentile) and `t` (the same draws widened by `schedule.scale(n)`).

Intervals on the pooled correlations and z-SDs are block bootstraps over
SEASONS: 32 defenses in one season share a league, a rules environment and a
set of offenses, so the defense-season is not the independent unit.
"""
import argparse
import math
import sys
import time

from analytics import schedule as S
from analytics.intervals import block_bootstrap

DRAWS = 1000        # only an SE is needed here; the published figures use 2000
KS = (2, 4, 8)
LAST_WEEK = 18


def _se(e):
    if e.lo == float("-inf") or e.hi == float("inf"):
        return None
    return (e.hi - e.lo) / (2 * 1.959963984540054)


def _pearson(pairs):
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if n < 3:
        return None
    ma = sum(a for a, _ in pairs) / n
    mb = sum(b for _, b in pairs) / n
    sab = sum((a - ma) * (b - mb) for a, b in pairs)
    saa = sum((a - ma) ** 2 for a, _ in pairs)
    sbb = sum((b - mb) ** 2 for _, b in pairs)
    return sab / math.sqrt(saa * sbb) if saa and sbb else None


def _sd(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None
    return math.sqrt(sum(x * x for x in xs) / len(xs))   # mean-zero under H0


def _tail(xs):
    xs = [x for x in xs if x is not None]
    return (sum(1 for x in xs if abs(x) > 1.959963984540054) / len(xs)) if xs else None


def effects(df, group, measure, tag, method):
    so = S.Season(df, group)
    return S.defense_effects(so, measure, draws=DRAWS, tag=tag, method=method)


def run(seasons, ks, groups, measures):
    """{(group, measure, k): {season: [record]}} - one record per defense."""
    out = {}
    for season in seasons:
        t0 = time.time()
        full = S.load_season(season)
        prior = S.load_season(season - 1) if season - 1 >= 1999 else None
        for group in groups:
            for measure in measures:
                pri = (effects(prior, group, measure, "prior", "t")
                       if prior is not None else {})
                for k in ks:
                    early_df = full.filter(full["week"] <= k)
                    rest_df = full.filter(full["week"] > k)
                    by_method = {}
                    for method in ("raw", "t"):
                        by_method[method] = (
                            effects(early_df, group, measure, "early%d" % k, method),
                            effects(rest_df, group, measure, "rest%d" % k, method))
                    recs = []
                    for team, e_t in by_method["t"][0].items():
                        r_t = by_method["t"][1].get(team)
                        e_r = by_method["raw"][0].get(team)
                        r_r = by_method["raw"][1].get(team)
                        if r_t is None or e_t.est is None or r_t.est is None:
                            continue
                        rec = {"team": team, "n_early": e_t.n, "n_rest": r_t.n,
                               "early": e_t.est, "rest": r_t.est,
                               "prior": pri[team].est if team in pri else None}
                        for method, a, b in (("raw", e_r, r_r), ("t", e_t, r_t)):
                            sa, sb = _se(a), _se(b)
                            rec["z_" + method] = (
                                (a.est - b.est) / math.sqrt(sa * sa + sb * sb)
                                if sa is not None and sb is not None and (sa or sb)
                                else None)
                        recs.append(rec)
                    out.setdefault((group, measure, k), {})[season] = recs
        print("  %d done in %.0fs" % (season, time.time() - t0), flush=True)
    return out


def summarise(results):
    rows = []
    for (group, measure, k), by_season in sorted(results.items()):
        blocks = {s: v for s, v in by_season.items() if v}
        n_def = sum(len(v) for v in blocks.values())

        def stat(fn):
            return block_bootstrap(blocks, fn, draws=1000, seed=20260924)

        r_early = stat(lambda rs: _pearson([(r["early"], r["rest"]) for r in rs]))
        r_prior = stat(lambda rs: _pearson([(r["prior"], r["rest"]) for r in rs]))
        z_raw = stat(lambda rs: _sd([r["z_raw"] for r in rs]))
        z_t = stat(lambda rs: _sd([r["z_t"] for r in rs]))
        tail_raw = _tail([r["z_raw"] for v in blocks.values() for r in v])
        tail_t = _tail([r["z_t"] for v in blocks.values() for r in v])
        rows.append((group, measure, k, len(blocks), n_def, r_early, r_prior,
                     z_raw, z_t, tail_raw, tail_t))
    return rows


def _fmt(e, spec="%+.3f"):
    if e is None or e.est is None:
        return "n/a"
    return (spec + " [" + spec + ", " + spec + "]") % (e.est, e.lo, e.hi)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seasons", default="2009-2025")
    ap.add_argument("--k", type=int, nargs="+", default=list(KS))
    ap.add_argument("--group", action="append", choices=sorted(S.GROUPS))
    ap.add_argument("--measure", action="append",
                    choices=("efficiency", "opportunity"))
    a = ap.parse_args(argv)
    lo, _, hi = a.seasons.partition("-")
    seasons = list(range(int(lo), int(hi or lo) + 1))
    results = run(seasons, a.k, a.group or sorted(S.GROUPS),
                  a.measure or ["efficiency", "opportunity"])
    rows = summarise(results)
    if not rows:
        raise SystemExit("no results - an empty study is not a null")
    print("\nseasons %s; draws %d; intervals over seasons (block = season)"
          % (a.seasons, DRAWS))
    print("%-3s %-12s %2s %4s %5s  %-26s %-26s %-24s %-24s %6s %6s"
          % ("grp", "measure", "k", "szn", "defs", "r(early, rest)",
             "r(prior season, rest)", "SD(z) raw", "SD(z) t", "|z|>2 raw", "t"))
    for (group, measure, k, ns, nd, re, rp, zr, zt, tr, tt) in rows:
        print("%-3s %-12s %2d %4d %5d  %-26s %-26s %-24s %-24s %6.3f %6.3f"
              % (group, measure, k, ns, nd, _fmt(re), _fmt(rp),
                 _fmt(zr, "%.2f"), _fmt(zt, "%.2f"), tr or float("nan"),
                 tt or float("nan")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
