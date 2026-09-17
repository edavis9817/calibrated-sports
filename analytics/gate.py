"""The publication gate: an estimate without its interval and its n is refused.

This is the structural rule of track F, as code. It is deliberately the same
shape as `cfb.guards`: a pure function over a schema spec, plus a second one
over an export payload, plus a test that drives the checker into every banned
shape - a guard that has never been seen to fail is not a guard.

WHAT COUNTS AS AN ESTIMATE. A number computed by summarising OTHER numbers: a
rate, a share, a mean, a per-game figure, a correlation, a slope, a quantile.
Not a raw count (`plays`, `games`, `targets`), not an identifier, not a
threshold that was posted rather than estimated (`line`, `ydstogo`).

WHAT AN ESTIMATE MUST CARRY. For a column named `S`:

    S_lo, S_hi   the interval, whatever its construction
    S_n or n     the sample count the interval was built on

`n` alone is accepted because the common case is a table whose every estimate
shares one sample - a player-week row summarising the same set of plays. A
table mixing samples must name them per estimate, and `MIXED_N` says so.

THE SECOND RULE: A METRIC CARRIES ITS USABLE RANGE AS DATA.

Every metric in this package has a different one - game-script elasticity runs
1999-2026 on carries and 2009-2026 on targets, air-yard shape starts 2006 by
passer and 2009 by receiver, on-field role has no current season at all. A range
that lives only in a schema comment or in a doc is a range the reader never
sees, and a metric that quietly changes definition by season is worse than one
that says it cannot answer. So `metric_violations` refuses a published metric
payload that does not carry `season_from`, `season_to`, `range_note` and
`availability` - and `availability` must say `current` or `historical`, because
"can this serve a page about this week" is the question a reader is actually
asking.

WHY THE SAMPLE COUNT IS SEPARATE FROM THE INTERVAL. An interval hides its own
width behind a construction choice; `n` does not. Brief 021's "effective sample
is 136 fits, not 935" and brief 022's "an interval on <5 games enters BH at
p = 1" are both facts about n that no interval reports on its own.
"""
import re

# Suffixes that are PART of an estimate's apparatus, never an estimate.
_APPARATUS = re.compile(r"_(lo|hi|n|se|sd|ci|method|draws|eff_n)$", re.I)

# A number summarised across other numbers. Anything matching must carry the
# triple. Ordered loosely by how often each shape shows up in this project.
ESTIMATE = re.compile(
    r"(^|_)(rate|share|pct|percent|prob|probability|freq|frequency|density)(_|$)|"
    r"(^|_)(mean|avg|average|median|mode|var|variance|dispersion|vmr)(_|$)|"
    r"(^|_)(per_game|per_play|per_snap|per_target|per_carry|per_drive|per_week)(_|$)|"
    r"(^|_)(corr|correlation|autocorr|autocorrelation|acf|r2|rsq|slope|coef|"
    r"coefficient|elasticity|beta|delta|lift|edge|skew|kurtosis|entropy|gini)(_|$)|"
    r"(^|_)(adot|aypa|ypc|ypa|ypt|ypr|epa|wpa|cpoe|success|xpass|ece|brier)(_|$)|"
    r"(^|_)(q\d+|p\d+|pctile|percentile|quantile|decile)(_|$)|"
    r"_(rate|share|pct|mean|median|index|ratio|score|elasticity)$|"
    r"^(rate|share|pct|mean|median|index|ratio|elasticity)$",
    re.I)

# Names that look like an estimate but are a posted or measured scalar, not one
# summarised from a sample. Each is here because it exists in real nflverse or
# market data under a name the regex above would otherwise claim.
NOT_AN_ESTIMATE = {
    # posted by a venue or a league, not computed by us
    "line", "line_index", "total_line", "spread_line", "strike", "price",
    # play-level nflverse columns carried through verbatim, not summarised
    "epa", "wpa", "cpoe", "success", "xpass", "pass_oe", "wp", "def_wp",
    "air_epa", "yac_epa", "qb_epa",
}

MIXED_N = ("estimate has no sample count; a table whose estimates share one "
           "sample may name it `n`, otherwise name it `{col}_n`")


# A LONG-FORMAT row names its estimate `est` and its bounds `lo`/`hi`, with no
# stem to prefix. Without this rule the whole gate is sidesteppable by pivoting:
# `est` matches no estimate pattern, so a one-row-per-metric table carrying a
# bare `est` and nothing else would have passed. Found while writing
# `f_metric_values`, which is exactly that shape.
LONG_EST = ("est", "estimate", "value", "point_estimate")


def _stem_ok(col, present):
    """Does `col` have its interval and its sample count in `present`?"""
    missing = []
    if col in LONG_EST:
        if "lo" not in present or "hi" not in present:
            missing.append("interval (lo, hi)")
        if "n" not in present:
            missing.append("estimate has no sample count (n)")
        return missing
    if f"{col}_lo" not in present or f"{col}_hi" not in present:
        missing.append("interval ({col}_lo, {col}_hi)".format(col=col))
    if f"{col}_n" not in present and "n" not in present:
        missing.append(MIXED_N.format(col=col))
    return missing


def is_estimate(col: str) -> bool:
    c = col.lower()
    if c in LONG_EST:
        return True
    if c in NOT_AN_ESTIMATE or _APPARATUS.search(c):
        return False
    return bool(ESTIMATE.search(c))


def column_violations(table: str, columns) -> list:
    """[(table, column, reason)] for every estimate missing its apparatus."""
    present = {str(c).lower() for c in columns}
    out = []
    for col in columns:
        c = str(col).lower()
        if not is_estimate(c):
            continue
        for reason in _stem_ok(c, present):
            out.append((table, col, reason))
    return out


def schema_violations(tables) -> list:
    """Check a `{table: (key, [(col, type), ...])}` spec, as `cfb.schema` uses.

    Also accepts `{table: [col, ...]}` for a plain column list.
    """
    out = []
    for table, spec in tables.items():
        if isinstance(spec, tuple) and len(spec) == 2:
            _key, cols = spec
            names = [c for c, _t in cols]
        else:
            names = [c if isinstance(c, str) else c[0] for c in spec]
        out.extend(column_violations(table, names))
    return out


def payload_violations(obj, path="$") -> list:
    """Check an export payload - nested dicts and lists of records.

    An exported object is checked as its own scope: `{"rate": 0.41, "n": 900,
    "rate_lo": .., "rate_hi": ..}` passes, and a sibling object without them
    fails even if a PARENT carried an `n`. Inheriting `n` down the tree is how
    a figure ends up quoting a sample it was not computed on.
    """
    out = []
    if isinstance(obj, dict):
        scalars = {k for k, v in obj.items()
                   if v is None or isinstance(v, (int, float, str, bool))}
        out.extend((path, c, r) for _t, c, r in column_violations(path, scalars))
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                out.extend(payload_violations(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                out.extend(payload_violations(v, f"{path}[{i}]"))
    return out


# ---------------------------------------------------------------------------
# the second rule: a metric carries its usable range
# ---------------------------------------------------------------------------

# What a published metric must say about itself, beyond its numbers.
RANGE_KEYS = ("season_from", "season_to", "range_note", "availability")

# `availability` answers one question: can this metric serve a page about the
# CURRENT season? `historical` is not a defect - participation-derived metrics
# are structurally historical, because the feed refreshes only after the
# postseason - but it has to be said out loud rather than discovered when a
# live page renders an empty panel.
AVAILABILITY = ("current", "historical")


def metric_violations(payload, path="$") -> list:
    """[(path, key, reason)] for a metric payload missing its usable range.

    Applied to the METRIC envelope, not to every row inside it: the range is a
    property of the metric, and repeating it per row is the duplication that
    lets two copies disagree.
    """
    out = []
    if not isinstance(payload, dict):
        return [(path, None, "a metric payload must be an object")]
    for key in RANGE_KEYS:
        if payload.get(key) is None:
            out.append((path, key, "metric does not carry its usable range "
                                   "(season_from, season_to, range_note, "
                                   "availability)"))
    av = payload.get("availability")
    if av is not None and av not in AVAILABILITY:
        out.append((path, "availability",
                    "availability must be one of %s, not %r"
                    % (", ".join(AVAILABILITY), av)))
    lo, hi = payload.get("season_from"), payload.get("season_to")
    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
        out.append((path, "season_from",
                    "season_from %d is after season_to %d" % (lo, hi)))
    note = payload.get("range_note")
    if isinstance(note, str) and not note.strip():
        out.append((path, "range_note",
                    "range_note is blank; a metric with nothing to say about "
                    "its range says so in words, it does not say nothing"))
    return out


def published_violations(payload, path="$") -> list:
    """Both rules at once: the interval-and-n gate AND the usable range.

    This is what an export calls. Calling only one of the two is how a metric
    ships with perfect intervals over a range nobody stated.
    """
    return metric_violations(payload, path) + payload_violations(payload, path)
