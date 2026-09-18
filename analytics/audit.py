"""Of the 87 published metrics, which ones let anyone actually say something?

    python -m analytics.audit
    python -m analytics.audit --by-metric
    python -m analytics.audit --survivors

THE QUESTION, AND WHY IT COMES AFTER THE GATE. `analytics.gate` refuses a number
without an interval and a sample count. That makes a metric publishable; it does
not make it worth publishing. Publishing all 87 undifferentiated is noise, and
the next question is which of them are distinguishable from saying nothing.

THE CRITERION IS TRACK B'S, DELIBERATELY: **the 95% interval excludes the null.**
Track B counted the prop record that way - 110 of 1,584 markets distinguishable
from even money, 1,474 saying nothing either way, 93.1% inconclusive. Two
pipelines answering the same question the same way is a stronger claim than
either alone, so the arithmetic here is identical and only the null differs.

AND THE NULL IS NOT THE SAME FOR EVERY METRIC, WHICH IS THE WHOLE DIFFICULTY.

  ZERO-NULL metrics are differences and correlations. "No effect" is exactly
  zero, so `lo > 0 or hi < 0` is the same test track B ran against 0.5, and
  these figures are DIRECTLY COMPARABLE to theirs.

  REFERENCE-NULL metrics are descriptive: a target share, an air-yard quantile,
  seconds per play. **These have no meaningful zero.** Every target-share
  interval excludes 0, so testing against 0 returns ~100% "distinguishable" and
  means nothing - a figure that would sit next to track B's 93.1% looking like a
  finding while being arithmetic. The honest analogue of "even money" here is
  the level at which the metric says nothing ABOUT THIS SUBJECT: the population
  reference. A player supports a statement when his interval excludes the
  median subject.

The two are reported separately and never merged into one headline. The
zero-null number is the one that pairs with track B's.

THE REFERENCE IS TREATED AS FIXED, and that is a real limitation stated rather
than buried: the population median carries its own uncertainty, which this
ignores, so the reference-null counts are somewhat generous. They are a ceiling
on how much separates, not an estimate of it.
"""
import argparse
import sys

from analytics import paths

# How to read each metric family's null. Explicit, and asserted complete: a new
# metric family that nobody classified must fail here rather than silently
# inherit a null that makes it look decisive.
NULL_KIND = {
    "script_elasticity": "mixed",     # slice "" is a difference; buckets are shares
    "usage_stability": "zero",        # correlations and a variance share
    "ngs_stability": "zero",          # correlations
    "role": "reference",              # shares of team
    "air_yards": "reference",         # bin shares, quantiles, a polarity share
    "pace": "reference",              # seconds per play, plays per game
}

# Within a mixed family, the slices whose null is zero.
ZERO_SLICES = {"script_elasticity": {""}}


def family(metric):
    return metric.split(".", 1)[0]


def null_kind(metric, slice_key):
    fam = family(metric)
    kind = NULL_KIND.get(fam)
    if kind is None:
        raise SystemExit(
            "metric family %r is not classified in analytics.audit.NULL_KIND. "
            "Classify it: a metric whose null nobody decided will be tested "
            "against zero and look decisive for free." % fam)
    if kind == "mixed":
        return "zero" if slice_key in ZERO_SLICES[fam] else "reference"
    return kind


def _rows(con):
    return con.execute(
        "SELECT v.metric, v.slice, v.est, v.lo, v.hi, v.n, m.subject_type, "
        "m.availability, m.label FROM f_metric_values v "
        "JOIN f_metrics m USING (metric) ORDER BY v.metric, v.slice").fetchall()


def audit(con):
    """(per_value, per_metric) - the split, by the criterion above."""
    rows = _rows(con)
    if not rows:
        raise SystemExit("no published metrics - an audit of nothing is not a "
                         "result. Run the analytics publishers first.")
    # the reference per (metric, slice): the median of subject estimates
    groups = {}
    for metric, sl, est, lo, hi, n, _st, _av, _lab in rows:
        if est is not None:
            groups.setdefault((metric, sl), []).append(est)
    reference = {}
    for key, ests in groups.items():
        xs = sorted(ests)
        mid = len(xs) // 2
        reference[key] = (xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2)

    per_value, per_metric = [], {}
    for metric, sl, est, lo, hi, n, subject_type, availability, label in rows:
        kind = null_kind(metric, sl)
        null = 0.0 if kind == "zero" else reference.get((metric, sl))
        decisive = (est is not None and null is not None
                    and (lo > null or hi < null))
        per_value.append((metric, sl, kind, decisive, n, subject_type))
        d = per_metric.setdefault(metric, {
            "label": label, "subject_type": subject_type,
            "availability": availability, "kind": kind,
            "values": 0, "decisive": 0, "min_n": None, "slices": set()})
        d["values"] += 1
        d["decisive"] += int(decisive)
        d["slices"].add(sl)
        d["min_n"] = n if d["min_n"] is None else min(d["min_n"], n)
        if d["kind"] != kind:
            d["kind"] = "mixed"
    return per_value, per_metric


def split(per_value, kind, subject_type=None):
    """(decisive, total) for a null kind, optionally within one subject type.

    THE SUBJECT SPLIT IS NOT COSMETIC. A league-level correlation is ONE value
    computed over hundreds of players and resolves almost by construction; a
    per-player elasticity is one value over that player's own games and mostly
    does not. Pooling them gives a number that is neither, and the per-player
    half is the one that answers track B's question - whether a claim can be
    made about THIS subject.
    """
    rows = [r for r in per_value
            if r[2] == kind and (subject_type is None or r[5] == subject_type)]
    return sum(1 for r in rows if r[3]), len(rows)


def survivors(per_metric, kind="zero"):
    """Metrics of `kind` where EVERY published value is decisive.

    For a league-level metric that is one value and the test is direct. The
    all-values rule is deliberate: a metric that separates on some subjects and
    not others does not "support a statement" as a metric - it supports one
    about those subjects, which is the reference-null table's business.
    """
    out = []
    for metric, d in per_metric.items():
        if d["kind"] != kind or not d["values"]:
            continue
        if d["decisive"] == d["values"]:
            out.append((metric, d))
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--by-metric", action="store_true")
    ap.add_argument("--survivors", action="store_true")
    a = ap.parse_args(argv)
    con = paths.connect(read_only=True)
    per_value, per_metric = audit(con)

    p_yes, p_tot = split(per_value, "zero", "player")
    l_yes, l_tot = split(per_value, "zero", "league")
    r_yes, r_tot = split(per_value, "reference")
    print("\n%d metrics, %d published values\n" % (len(per_metric), len(per_value)))
    print("CRITERION: the 95%% interval excludes the null - track B's test, so "
          "the first block is directly comparable to their 110 of 1,584.\n")
    print("  ZERO-NULL, PER PLAYER (game-script elasticity: a difference of two shares)")
    print("    %d of %d distinguishable from no game-script effect  (%.1f%%)"
          % (p_yes, p_tot, 100.0 * p_yes / max(p_tot, 1)))
    print("    %d say nothing either way                            (%.1f%% inconclusive)"
          % (p_tot - p_yes, 100.0 * (p_tot - p_yes) / max(p_tot, 1)))
    print("\n  ZERO-NULL, LEAGUE-WIDE (stability correlations: one value over all players)")
    print("    %d of %d distinguishable from zero                   (%.1f%%)"
          % (l_yes, l_tot, 100.0 * l_yes / max(l_tot, 1)))
    print("    a league figure pools hundreds of players, so it resolves almost by")
    print("    construction - a different question from the per-player one, not pooled with it.")
    print("\n  REFERENCE-NULL (descriptive; null is the median subject)")
    print("    %d of %d values distinguishable from a typical subject  (%.1f%%)"
          % (r_yes, r_tot, 100.0 * r_yes / max(r_tot, 1)))
    print("    %d say nothing either way                               (%.1f%% inconclusive)"
          % (r_tot - r_yes, 100.0 * (r_tot - r_yes) / max(r_tot, 1)))
    print("\n  These two are NOT added together. A descriptive metric has no "
          "meaningful zero,\n  so testing it against 0 would return ~100% and "
          "mean nothing.")

    if a.by_metric:
        print("\n%-44s %-10s %7s %7s %6s %s"
              % ("metric", "null", "values", "decisive", "min n", "share"))
        for metric in sorted(per_metric):
            d = per_metric[metric]
            print("%-44s %-10s %7d %8d %6d  %5.1f%%"
                  % (metric, d["kind"], d["values"], d["decisive"], d["min_n"],
                     100.0 * d["decisive"] / max(d["values"], 1)))
    if a.survivors:
        got = survivors(per_metric, "zero")
        print("\nZERO-NULL METRICS WHERE EVERY VALUE EXCLUDES THE NULL "
              "(%d of %d):\n" % (got and len(got) or 0,
                                 sum(1 for d in per_metric.values()
                                     if d["kind"] == "zero")))
        for metric, d in got:
            print("  %-44s n=%d  %s" % (metric, d["min_n"], d["availability"]))
        dead = [(m, d) for m, d in sorted(per_metric.items())
                if d["kind"] == "zero" and d["decisive"] == 0]
        print("\nZERO-NULL METRICS WHERE NOTHING EXCLUDES THE NULL (%d):\n"
              % len(dead))
        for metric, d in dead:
            print("  %-44s n=%d" % (metric, d["min_n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
