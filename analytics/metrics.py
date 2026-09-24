"""The metric registry, and the one table every analytic publishes into.

TWO RULES LIVE HERE.

1. **A metric carries its usable range as data.** Not in a schema comment, not
   in a doc - on the row, so the page can print it where a reader sees it.
   `analytics.gate.metric_violations` refuses a metric envelope without
   `season_from`, `season_to`, `range_note` and `availability`.

2. **THE RANGE IS DERIVED FROM THE SURVEY, NOT TYPED.** A metric declares the
   COLUMNS it depends on; `derive_range` reads `f_pbp_columns` and returns the
   first season after which none of them is absent, thin or silently zero, plus
   a note naming the column that set the bound. A hand-written "2009" is a
   number that stops being true when upstream moves and nothing notices. This
   is the same rule the site already applies to comparative claims: computed,
   or it does not appear.

`availability` is `current` or `historical` and answers the only question a
reader actually has: can this serve a page about this week? A
participation-derived metric is structurally `historical` - the feed refreshes
only after the postseason - and that is not a defect, but it has to be said
rather than discovered when a live page renders empty.

THE OUTPUT IS ONE LONG TABLE. `f_metric_values` carries `est`, `lo`, `hi`, `n`,
`method` per (metric, subject, slice). One shape, so the gate covers every
analytic rather than each one re-implementing the discipline - and the gate
grew a long-format rule the day this table was designed, because a bare `est`
matched no estimate pattern and would have walked straight through it.
"""
import argparse
import re
import sys
import time
from dataclasses import dataclass, field

from analytics import gate, paths, survey

SCHEMA = """
CREATE TABLE IF NOT EXISTS f_metrics (
    metric       TEXT PRIMARY KEY,
    label        TEXT    NOT NULL,
    unit         TEXT    NOT NULL,   -- what the number is, in words
    subject_type TEXT    NOT NULL,   -- player | team | league
    block        TEXT    NOT NULL,   -- what the interval resamples: game | player
    basis        TEXT    NOT NULL,   -- pbp | participation
    slice_kind   TEXT    NOT NULL,   -- what `slice` means for this metric
    shares_denominator TEXT,         -- 'team' when two subjects divide one total
    season_from  INTEGER NOT NULL,
    season_to    INTEGER NOT NULL,
    range_note   TEXT    NOT NULL,
    availability TEXT    NOT NULL,   -- current | historical
    requires     TEXT    NOT NULL,   -- the columns the range was derived from
    computed_ts  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS f_metric_values (
    metric       TEXT    NOT NULL,
    subject_id   TEXT    NOT NULL,   -- gsis_id, team abbr, or '_league'
    slice        TEXT    NOT NULL,   -- '' for the headline, else the bucket
    season_from  INTEGER NOT NULL,
    season_to    INTEGER NOT NULL,
    est          REAL,
    lo           REAL    NOT NULL,
    hi           REAL    NOT NULL,
    n            INTEGER NOT NULL,   -- INDEPENDENT UNITS, not rows
    rows         INTEGER,            -- raw observations, when they exceed n
    method       TEXT    NOT NULL,
    PRIMARY KEY (metric, subject_id, slice, season_from, season_to)
);
CREATE INDEX IF NOT EXISTS ix_fmv_metric ON f_metric_values(metric, slice);
CREATE INDEX IF NOT EXISTS ix_fmv_subject ON f_metric_values(subject_id, metric);
"""

# A metric whose name or description is share-shaped must declare its
# denominator. Deliberately broad: a false positive costs one keyword argument,
# a false negative ships a comparison nobody flagged.
SHARE_SHAPED = re.compile(
    r"(^|[^a-z])share([^a-z]|$)|(^|_)pct(_|$)|percent|proportion|"
    r"fraction|(^|_)rate_of(_|$)", re.I)

VALUE_COLS = ("metric", "subject_id", "slice", "season_from", "season_to",
              "est", "lo", "hi", "n", "rows", "method")

# The live season. Read from the spine rather than hardcoded: a constant here
# would need editing every August, and the one that did not get edited is how
# `CURRENT_SEASON` problems start.
def live_season(con) -> int:
    row = con.execute("SELECT MAX(season) FROM f_spine_build").fetchone()
    if not row or row[0] is None:
        raise SystemExit("the spine is empty - run `python -m analytics.spine "
                         "--build` before publishing a metric")
    return int(row[0])


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    subject_type: str
    block: str
    basis: str
    availability: str
    # WHAT TWO SUBJECTS DIVIDE, when they divide anything. `team` for any
    # share-of-team figure - target share, carry share, snap share, and every
    # "% of team" a later analytic invents.
    #
    # It is a field rather than a note because the property is not about these
    # metrics, it is about the quantity: two teammates' shares are mechanically
    # opposed, so their intervals side by side are the comparison that the
    # bootstrap has to be independent for, and the page has to caveat. A rule
    # that lives only in prose is a rule the next analytic does not know about.
    # `own` means the denominator is the subject's own total - an air-yard bin
    # share divides that player's targets, so two players share nothing and the
    # side-by-side caveat does not apply. It is an explicit answer, because the
    # alternative is that every false positive of `SHARE_SHAPED` gets silenced
    # by a default and the real ones go with them.
    #
    # `SHARE_SHAPED` below refuses a share metric that leaves this unset.
    shares_denominator: str = None
    # What `slice` means for THIS metric - a script bucket, a down bucket, a
    # season, an air-yard bin. `f_metric_values` is one long table for every
    # analytic, so without this a reader cannot tell a slice of '2019' from a
    # slice of '3rd_short'.
    slice_kind: str = ""
    requires: tuple = ()          # ((dataset, column, condition), ...)
    floor_season: int = 1999      # a bound the data cannot express
    floor_reason: str = ""
    # WHAT THE RANGE CANNOT FIX, in words, appended to the derived note. The
    # bound itself stays derived; this is for a limitation of the MEASUREMENT
    # that holds in every season of it - a selection effect, say - and that a
    # page must print beside the range rather than leave in a docstring.
    range_caveat: str = ""

    def __post_init__(self):
        if self.availability not in gate.AVAILABILITY:
            raise ValueError("availability must be one of %s"
                             % (gate.AVAILABILITY,))
        if self.block not in ("game", "player", "team"):
            raise ValueError("block must name what the interval resamples")
        if self.shares_denominator not in (None, "team", "league", "own"):
            raise ValueError(
                "shares_denominator names what two subjects divide: 'team', "
                "'league', 'own' (the subject's own total - nothing is shared "
                "with another subject), or None for a metric that is not a "
                "share at all")
        text = "%s %s %s" % (self.key, self.label, self.unit)
        if SHARE_SHAPED.search(text) and self.shares_denominator is None:
            raise ValueError(
                "metric %r looks like a share but does not say what two "
                "subjects divide. Set shares_denominator to 'team' or "
                "'league' if they divide one total, or to 'own' if the "
                "denominator is the subject's own - two teammates' shares are "
                "mechanically opposed and a page reading their intervals side "
                "by side has to know which case it is. 'own' is an answer; "
                "silence is not." % self.key)


def derive_range(con, metric: Metric):
    """(season_from, season_to, note) from the measured coverage.

    For each required column, the first season at or after which the column is
    not absent, not thin and not in a silent-zero run - taking the MAXIMUM over
    the requirements, because a metric is only usable once every input is.

    The note names the binding column. When nothing binds, it says so; a metric
    whose range is the whole archive should say that in words rather than leave
    a reader to infer it from two numbers.
    """
    live = live_season(con)
    bounds, ends = [], []
    for req in metric.requires:
        dataset, column, condition = (req + ("",))[:3]
        name = "%s.%s%s" % (dataset, column, "|" + condition if condition else "")
        rows = survey.coverage(con, column, dataset, condition)
        if not rows:
            raise SystemExit(
                "metric %r requires %s, which the survey has never measured. "
                "Run `python -m analytics.survey --scan`; deriving a range "
                "from a missing measurement is how a hand-written range gets "
                "reintroduced." % (metric.key, name))
        bad = set()
        found = survey.anomalies(con, dataset=dataset,
                                 condition=condition).get(column)
        if found:
            bad |= set(found["absent"]) | set(found["thin"])
        for _c, _dt, runs, _ref, _sh, _n in survey.silent_zeros(
                con, dataset=dataset, condition=condition):
            if _c == column:
                bad |= {s for a, b in runs for s in range(a, b + 1)}
        seasons = sorted(s for s, *_rest in rows)
        if not seasons or set(seasons) <= bad:
            raise SystemExit("metric %r requires %s, which has no usable "
                             "season at all" % (metric.key, name))
        # THE FIRST SEASON FROM WHICH COVERAGE IS CONTINUOUS TO THE END. A
        # column with a hole in the middle bounds the metric at the hole, not
        # at its first good season - that is the 2003-2008 targets case, and
        # min(good seasons) there returns 1999 and is wrong.
        #
        # Walk the FULL season list backwards and stop at the first bad one.
        # The first version walked the GOOD list, which by construction never
        # contains a bad season, so it always ran to 1999 and every range came
        # back "the whole archive" - a checker that could only return one
        # answer, which is the falsifiability rule failing in miniature.
        start = seasons[0]
        for s in reversed(seasons):
            if s in bad:
                start = s + 1
                break
        if start > seasons[-1]:
            raise SystemExit("metric %r requires %s, whose last usable season "
                             "is before the archive ends" % (metric.key, name))
        bounds.append((start, name))
        # AND WHERE IT ENDS. The first version took `season_to` from the live
        # season for every metric, so the participation-derived on-field role
        # would have published as 2016-2026 while its input stops at 2025 - a
        # range that says a page can ask it about this week when it cannot.
        last = max(s for s in seasons if s not in bad)
        ends.append((last, name))
    if metric.floor_season > 1999:
        bounds.append((metric.floor_season, metric.floor_reason or "declared"))
    if not bounds:
        return 1999, live, "the whole archive, 1999-%d; no input binds it" % live
    start, _who = max(bounds)
    who = ", ".join(sorted(w for s, w in bounds if s == start))
    to, _w2 = min(ends) if ends else (live, "")
    to = min(to, live)
    ends_who = ", ".join(sorted(w for s, w in ends if s == to))

    parts = []
    if start > 1999:
        parts.append("starts %d: %s is not usable before it" % (start, who))
    if to < live:
        parts.append("ENDS %d: %s has no data after it, so this cannot answer "
                     "a question about the %d season" % (to, ends_who, live))
    if not parts:
        return 1999, live, ("the whole archive, 1999-%d; no input binds it"
                            % live)
    return start, to, "; ".join(parts)


def register(con, metric: Metric):
    """Write the metric's row, with its range derived. Returns the row."""
    con.executescript(SCHEMA)
    lo, hi, note = derive_range(con, metric)
    if metric.range_caveat:
        note = "%s. %s" % (note, metric.range_caveat)
    # AVAILABILITY IS CHECKED AGAINST THE DERIVED RANGE, not taken on trust. A
    # metric whose inputs stop before the live season cannot serve a page about
    # this week, whatever its author declared, and "current" is precisely the
    # claim a reader acts on.
    if hi < live_season(con) and metric.availability == "current":
        raise AssertionError(
            "metric %r declares availability 'current' but its derived range "
            "ends %d against a live season of %d: %s"
            % (metric.key, hi, live_season(con), note))
    row = {"metric": metric.key, "label": metric.label, "unit": metric.unit,
           "subject_type": metric.subject_type, "block": metric.block,
           "basis": metric.basis, "slice_kind": metric.slice_kind,
           "shares_denominator": metric.shares_denominator,
           "season_from": lo, "season_to": hi,
           "range_note": note, "availability": metric.availability,
           "requires": ";".join(
               "%s.%s%s" % ((r + ("",))[0], (r + ("",))[1],
                            "|" + (r + ("",))[2] if len(r) > 2 and r[2] else "")
               for r in metric.requires) or "-"}
    problems = gate.metric_violations(row, "$." + metric.key)
    if problems:
        raise AssertionError("metric %r does not carry its usable range: %s"
                             % (metric.key, problems))
    con.execute(
        "INSERT OR REPLACE INTO f_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(row[k] for k in ("metric", "label", "unit", "subject_type",
                               "block", "basis", "slice_kind",
                               "shares_denominator", "season_from",
                               "season_to", "range_note", "availability",
                               "requires"))
        + (int(time.time()),))
    con.commit()
    return row


def publish(con, metric: Metric, values, season_from=None, season_to=None):
    """Write a metric's values, gate-checked, replacing what it had before.

    `values` is an iterable of (subject_id, slice, Estimate). The gate runs on
    the ROW SHAPE once and on every payload - cheap, and it is the only thing
    standing between a point estimate and the table.
    """
    row = register(con, metric)
    lo = season_from if season_from is not None else row["season_from"]
    hi = season_to if season_to is not None else row["season_to"]
    # THE ENVELOPE MAY NOT CONTRADICT ITS OWN VALUES. `ngs_stability` published
    # a registry range of 1999-2026 around values stamped 2016-2026, because the
    # metric declared `requires=()` and `derive_range` duly answered "the whole
    # archive; no input binds it". Both halves were internally consistent and
    # the file said two different things - the same shape as a period row
    # labelled REG beside `label='Super Bowl'`. A caller narrowing the range
    # here is a caller whose Metric has not declared its floor.
    if (lo, hi) != (row["season_from"], row["season_to"]):
        raise AssertionError(
            "metric %r derives the range %d-%d but is publishing values as "
            "%d-%d. The envelope would contradict its own values. Declare the "
            "bound on the Metric - `requires=` where the survey can measure it, "
            "or `floor_season=` where it cannot - rather than passing it here."
            % (metric.key, row["season_from"], row["season_to"], lo, hi))
    problems = gate.column_violations("f_metric_values", VALUE_COLS)
    if problems:
        raise AssertionError("f_metric_values does not satisfy the gate: %s"
                             % problems)
    out = []
    for subject_id, slice_key, est in values:
        if est.n <= 0:
            continue                    # nothing measured is not a value
        out.append((metric.key, subject_id, slice_key or "", lo, hi,
                    est.est, est.lo, est.hi, est.n, est.rows, est.method))
    con.execute("DELETE FROM f_metric_values WHERE metric=?", (metric.key,))
    con.executemany("INSERT INTO f_metric_values VALUES (%s)"
                    % ",".join("?" * len(VALUE_COLS)), out)
    con.commit()
    return len(out)


def envelope(con, metric_key: str) -> dict:
    """A metric as a page would receive it: its range, then its values.

    The range is on the ENVELOPE, once, not repeated per row - repeating it is
    the duplication that lets two copies disagree. The gate's payload rule is
    scoped so a child object may not inherit the envelope's `n`, which is the
    part that must not be shared.
    """
    m = con.execute(
        "SELECT metric, label, unit, subject_type, block, basis, slice_kind, "
        "shares_denominator, season_from, season_to, range_note, "
        "availability, requires FROM f_metrics WHERE metric=?",
        (metric_key,)).fetchone()
    if not m:
        raise KeyError("no such metric: %r" % metric_key)
    keys = ("metric", "label", "unit", "subject_type", "block", "basis",
            "slice_kind", "shares_denominator", "season_from", "season_to",
            "range_note", "availability", "requires")
    env = dict(zip(keys, m))
    env["values"] = [
        {"subject_id": s, "slice": sl, "est": e, "lo": lo, "hi": hi,
         "n": n, "rows": rows, "method": meth}
        for s, sl, e, lo, hi, n, rows, meth in con.execute(
            "SELECT subject_id, slice, est, lo, hi, n, rows, method "
            "FROM f_metric_values WHERE metric=? ORDER BY subject_id, slice",
            (metric_key,))]
    return env


def check_published(con, metric_key: str) -> list:
    """Both gate rules against what is actually in the table."""
    return gate.published_violations(envelope(con, metric_key), "$." + metric_key)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="run both gate rules over every published metric")
    a = ap.parse_args(argv)
    con = paths.connect(read_only=True)
    rows = con.execute(
        "SELECT metric, label, season_from, season_to, availability, "
        "range_note FROM f_metrics ORDER BY metric").fetchall()
    if a.list or not (a.check):
        if not rows:
            raise SystemExit("no metrics published yet")
        for metric, label, lo, hi, av, note in rows:
            n = con.execute("SELECT COUNT(*) FROM f_metric_values WHERE metric=?",
                            (metric,)).fetchone()[0]
            print("%-34s %d-%d  %-10s %6d values" % (metric, lo, hi, av, n))
            print("%-34s %s" % ("", note))
    if a.check:
        if not rows:
            raise SystemExit("no metrics to check - an empty check is not a pass")
        bad = 0
        for (metric, *_r) in rows:
            v = check_published(con, metric)
            print("%-34s %s" % (metric, "ok" if not v else v))
            bad += len(v)
        print("%d metrics checked, %d violations" % (len(rows), bad))
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
