"""What each metric lets the page say, generated from its own interval.

    python -m analytics.claims              # one claim per metric
    python -m analytics.claims --player 00-0036355
    python -m analytics.claims --counts

NOT A DOCUMENT OF SENTENCES. `CLAUDE.md`: no comparative claim on this site is
hand-written, and the A10 extension covers EXPORTED PROSE and not only rendered
text - five captions written from memory were wrong in both directions. Writing
87 sentences by hand would be that defect with a larger surface, so the
vocabulary is data, the verdict comes from the interval, and the sentence is
assembled. `docs/F05-analytics-page-copy.md` shows what this emits; it is not
the source of it.

THE ONE RULE THE WORDING MUST NOT BREAK, from track B's `rateVerdict`: an
interval covering the null says **"cannot be distinguished from X"**, never "is
the same as X". Absence of evidence is not evidence of absence, and the copy is
where that conversion happens silently. `BANNED` is the mechanical form of it
and `tests/test_analytics_claims.py` drives every generated sentence past it.

FOUR VERDICTS, ALL REACHABLE. A verdict enum with an unreachable value is
decoration - the falsifiability rule the research register already carries.
`insufficient` currently fires on 0 of 52,583 values because the smallest
published `n` is 8; it is still reachable, tested, and counted out loud, because
a count that reads 0 today is what makes the day it reads 12 visible.
"""
import argparse
import sys

from analytics import audit, paths

# Brief 020: an interval over fewer than five blocks is not read, whatever it
# excludes - a block bootstrap over three identical outcomes returns a tight
# interval that "excludes zero".
MIN_BLOCKS = 5

ABOVE, BELOW, INDISTINCT, INSUFFICIENT = (
    "above", "below", "indistinguishable", "insufficient")

# Phrasings that convert "we cannot tell" into "there is nothing there". The
# list is the rule; the test drives every sentence this module can emit past it.
BANNED = (
    "is the same as", "identical to", "no different", "no difference",
    "has no effect", "unaffected", "shows no", "proves", "confirms there is no",
    "exactly zero", "is zero", "none at all",
)


def verdict(lo, hi, null, n):
    """The verdict, from the interval and the sample. Nothing else."""
    if n is None or n < MIN_BLOCKS:
        return INSUFFICIENT
    if lo > null:
        return ABOVE
    if hi < null:
        return BELOW
    return INDISTINCT


# --------------------------------------------------------------------------
# the vocabulary, as data
# --------------------------------------------------------------------------
# quantity : what the number is, in the site's words
# above/below : what it means for the interval to clear the null on each side
# null_name : what the page calls the null - never "zero" for a descriptive
#             metric, because "cannot be distinguished from zero target share"
#             is true and absurd
# fmt : how the magnitude reads
VOCAB = {
    "script_elasticity": {
        "quantity": "share of his team's {kind} when trailing by 7+, minus the "
                    "same share when leading by 7+",
        "short": "game-script elasticity",
        "above": "used more when his team is trailing",
        "below": "used more when his team is leading",
        "null_name": "no game-script effect",
        "fmt": "pp", "unit": "games",
    },
    # The stability families measure DIFFERENT THINGS and cannot share
    # wording. `within_lag1` is persistence of a player's own weekly swings;
    # `between` is how much of the variance separates players at all. Calling
    # the second one "persistence" would be a hand-written claim that happened
    # to be assembled.
    "usage_stability": {"short": "usage stability", "fmt": "r",
                        "unit": "players", "by_family": True},
    "ngs_stability": {"short": "Next Gen Stats stability", "fmt": "r",
                      "unit": "players", "by_family": True},
    "role": {
        "quantity": "share of his team's plays in this down-and-distance bucket",
        "short": "down-and-distance role",
        "above": "used more than a typical player here",
        "below": "used less than a typical player here",
        "null_name": "a typical player", "fmt": "pp", "unit": "games",
    },
    "air_yards": {
        "quantity": "air-yard shape",
        "short": "air-yard distribution",
        "above": "deeper or more polarised than a typical receiver",
        "below": "shallower or flatter than a typical receiver",
        "null_name": "a typical receiver", "fmt": "auto", "unit": "games",
    },
    "pace": {
        "quantity": "neutral-situation pace",
        "short": "pace",
        "above": "slower or higher-volume than a typical team",
        "below": "faster or lower-volume than a typical team",
        "null_name": "a typical team", "fmt": "auto", "unit": "games",
    },
}


# family -> (quantity, above, below, null_name) for the stability metrics.
STABILITY = {
    "within_lag1": (
        "week-to-week persistence of a player's own swings in {kind}",
        "this week's swing carries into next week",
        "this week's swing reverts by next week",
        "zero week-to-week persistence"),
    "naive_lag1": (
        "raw week-to-week correlation of {kind}, not adjusted for who the "
        "player is",
        "positive, but see the adjusted figure beside it",
        "negative, before adjusting for who the player is",
        "zero raw correlation"),
    "between": (
        "share of the variance in {kind} that separates players rather than "
        "weeks",
        "the metric separates players",
        "the metric does not separate players",
        "no separation between players"),
}


def _fmt(value, how):
    if value is None:
        return "-"
    if how == "pp":
        return "%+.1f points of share" % (100.0 * value)
    if how == "r":
        return "%+.3f" % value
    return "%.2f" % value


def _interval(lo, hi, how):
    if how == "pp":
        return "%.1f to %.1f" % (100.0 * lo, 100.0 * hi)
    if how == "r":
        return "%+.3f to %+.3f" % (lo, hi)
    return "%.2f to %.2f" % (lo, hi)


def claim(metric, slice_key, est, lo, hi, n, null, label=None):
    """{verdict, sentence, ...} for one published value.

    The sentence is assembled from `VOCAB` and the verdict; no branch of it
    contains a hand-written comparison.
    """
    fam = metric.split(".", 1)[0]
    v = dict(VOCAB[fam])
    kind = metric.rsplit(".", 1)[-1]
    if v.get("by_family"):
        family_word = metric.split(".")[1]
        q, up, down, null_name = STABILITY[family_word]
        v.update(quantity=q, above=up, below=down, null_name=null_name)
    got = verdict(lo, hi, null, n)
    quantity = v["quantity"].format(kind=kind.replace("_", " "))
    span = _interval(lo, hi, v["fmt"])
    tail = "95%% interval %s, n=%d %s" % (span, n or 0, v["unit"])

    if got == INSUFFICIENT:
        sentence = ("Not reported: %d %s is too few to read an interval from."
                    % (n or 0, v["unit"]))
    elif got == INDISTINCT:
        # THE RULE. Absence of evidence, stated as absence of evidence.
        sentence = ("%s cannot be distinguished from %s (%s; %s)."
                    % (quantity[0].upper() + quantity[1:], v["null_name"],
                       _fmt(est, v["fmt"]), tail))
    else:
        sentence = ("%s - %s: %s (%s)."
                    % (quantity[0].upper() + quantity[1:], v[got],
                       _fmt(est, v["fmt"]), tail))
    return {"metric": metric, "slice": slice_key, "verdict": got,
            "sentence": sentence, "quantity": quantity, "short": v["short"],
            "estimate": est, "interval": [lo, hi], "n": n, "null": null}


def for_metric(con, metric):
    """One claim describing the METRIC, plus its value counts.

    A league metric has one value and the claim is that value's. A per-subject
    metric has thousands, so the metric-level claim is how many of its subjects
    support a statement - which is F04's question, worded.
    """
    rows = con.execute(
        "SELECT v.slice, v.est, v.lo, v.hi, v.n, m.subject_type, m.label, "
        "m.availability, m.season_from, m.season_to FROM f_metric_values v "
        "JOIN f_metrics m USING (metric) WHERE v.metric=?", (metric,)).fetchall()
    if not rows:
        raise KeyError(metric)
    subject_type = rows[0][5]
    label, availability = rows[0][6], rows[0][7]
    lo_season, hi_season = rows[0][8], rows[0][9]
    fam = metric.split(".", 1)[0]
    refs = _references(rows, metric)
    claims = []
    for sl, est, lo, hi, n, _st, _lab, _av, _s0, _s1 in rows:
        kind = audit.null_kind(metric, sl)
        null = 0.0 if kind == "zero" else refs.get(sl)
        if null is None:
            continue
        claims.append(claim(metric, sl, est, lo, hi, n, null, label))
    counts = {}
    for c in claims:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    decisive = counts.get(ABOVE, 0) + counts.get(BELOW, 0)
    if subject_type == "league" and len(claims) == 1:
        headline = claims[0]["sentence"]
    else:
        headline = _population_sentence(decisive, len(claims), subject_type,
                                        VOCAB[fam]["short"])
    return {"metric": metric, "label": label, "availability": availability,
            "seasons": (lo_season, hi_season), "subject_type": subject_type,
            "headline": headline, "counts": counts, "values": len(claims),
            "decisive": decisive, "claims": claims}


def _references(rows, metric):
    """Median estimate per slice - the null for a descriptive metric."""
    groups = {}
    for sl, est, _lo, _hi, _n, _st, _lab, _av, _s0, _s1 in rows:
        if est is not None:
            groups.setdefault(sl, []).append(est)
    out = {}
    for sl, xs in groups.items():
        xs.sort()
        mid = len(xs) // 2
        out[sl] = xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2
    return out


def _population_sentence(decisive, total, subject_type, short):
    """How many subjects this metric supports a statement about.

    Assembled, not written: the numbers come from the intervals and the wording
    has one branch. "says nothing either way" is the honest half and it is the
    larger half for most of these.
    """
    noun = {"player": "players", "team": "teams"}.get(subject_type, "subjects")
    if total == 0:
        return "No published values."
    if decisive == 0:
        return ("%s: no %s of %d can be distinguished from the typical one."
                % (short.capitalize(), noun[:-1], total))
    return ("%s: %d of %d %s distinguishable from the typical one; the other "
            "%d say nothing either way." % (short.capitalize(), decisive, total,
                                            noun, total - decisive))


def all_metrics(con):
    return [for_metric(con, m) for (m,) in con.execute(
        "SELECT metric FROM f_metrics ORDER BY metric")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--counts", action="store_true")
    ap.add_argument("--player")
    ap.add_argument("--metric")
    a = ap.parse_args(argv)
    con = paths.connect(read_only=True)
    if a.player:
        for (metric,) in con.execute("SELECT DISTINCT metric FROM f_metric_values "
                                     "WHERE subject_id=? ORDER BY metric", (a.player,)):
            got = for_metric(con, metric)
            for c in got["claims"]:
                pass
        rows = con.execute(
            "SELECT v.metric, v.slice, v.est, v.lo, v.hi, v.n FROM f_metric_values v "
            "WHERE v.subject_id=? ORDER BY v.metric, v.slice", (a.player,)).fetchall()
        for metric, sl, est, lo, hi, n in rows:
            kind = audit.null_kind(metric, sl)
            if kind != "zero":
                continue
            c = claim(metric, sl, est, lo, hi, n, 0.0)
            print("  %-40s %s" % (metric, c["sentence"]))
        return 0
    metrics = [for_metric(con, a.metric)] if a.metric else all_metrics(con)
    tally = {}
    for got in metrics:
        if not a.counts:
            print("%s  [%d-%d, %s]" % (got["metric"], got["seasons"][0],
                                       got["seasons"][1], got["availability"]))
            print("    %s\n" % got["headline"])
        for v, k in got["counts"].items():
            tally[v] = tally.get(v, 0) + k
    print("verdicts over %d metrics and %d values: %s"
          % (len(metrics), sum(tally.values()),
             ", ".join("%s %d" % kv for kv in sorted(tally.items()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
