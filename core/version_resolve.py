"""BRIEF 017 ITEMS 1 AND 3 - resolving a model version, and refusing to guess.

`models.baseline.MODEL_VERSION` is derived from a hash of the model's source.
That is the right design - it is what stopped two different builds both calling
themselves "baseline-usage-0.2" - but it means an unrelated edit to a hashed
file renames the model, and every consumer asking for "the current version"
then finds nothing.

Two mechanisms, and the second exists because the first is not enough:

  EQUIVALENCE. `model_version_equivalence` records that two fingerprints
  predict identically, with machine-checked evidence (see
  `core/model_equivalence.py`). Resolution follows those links transitively and
  in BOTH directions, because equivalence is symmetric.

  REFUSAL. If resolution cannot find predictions - not under the requested
  version, not under anything proven equivalent to it - it RAISES. It does not
  fall back and it does not print.

The refusal is the point. This project has now shipped four separate bugs whose
symptom was a plausible-looking empty or degenerate result: C01's gate sweep
printing four identical rows, F01's Part 5 simulating one component, S01's
one-crossing arm reproducing mid-to-mid exactly, and 016's series audit
returning zero predictions. Every one of them looked like an answer. The series
audit was caught only because "zero rows" is obviously wrong in a way that
"0.61pp" is not - and a printed warning inside a twelve-minute run is a warning
nobody reads. An override exists, but it has to be typed: `--model-version`.
"""
import sqlite3

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_version_equivalence (
    old_version TEXT NOT NULL,
    new_version TEXT NOT NULL,
    reason      TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    created_ts  REAL NOT NULL,
    PRIMARY KEY (old_version, new_version)
);
"""


class ModelVersionError(Exception):
    """No predictions under the requested version, or anything equal to it."""


def ensure_schema(con=None):
    own = con is None
    con = con or sqlite3.connect(config.DB_PATH, timeout=60)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        if own:
            con.close()


def _ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def record(old_version, new_version, reason, evidence):
    """Write one equivalence. Callers must have run the evidence assertion."""
    import time
    ensure_schema()
    con = sqlite3.connect(config.DB_PATH, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    try:
        con.execute(
            "INSERT INTO model_version_equivalence VALUES (?,?,?,?,?) "
            "ON CONFLICT(old_version, new_version) DO UPDATE SET "
            "reason=excluded.reason, evidence=excluded.evidence, "
            "created_ts=excluded.created_ts",
            (old_version, new_version, reason, evidence, time.time()))
        con.commit()
    finally:
        con.close()


def equivalent(version, con=None) -> set:
    """Transitive closure of `version` under equivalence, including itself.

    Undirected: a row `old ≡ new` means asking for either should find the
    other. Recorded direction is provenance, not semantics.
    """
    own = con is None
    con = con or _ro()
    try:
        try:
            rows = con.execute("SELECT old_version, new_version "
                               "FROM model_version_equivalence").fetchall()
        except sqlite3.OperationalError:
            return {version}            # table not created yet
    finally:
        if own:
            con.close()
    adj = {}
    for a, b in rows:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, stack = {version}, [version]
    while stack:
        v = stack.pop()
        for w in adj.get(v, ()):
            if w not in seen:
                seen.add(w)
                stack.append(w)
    return seen


def counts(season, week, con=None):
    """{model_version: n predictions} for a season/week, newest first."""
    own = con is None
    con = con or _ro()
    try:
        rows = con.execute(
            "SELECT p.model_version, COUNT(*), MAX(p.created_ts) "
            "FROM predictions p JOIN outcomes o USING (outcome_id) "
            "WHERE o.season=? AND o.week=? GROUP BY 1 ORDER BY 3 DESC",
            (season, week)).fetchall()
    finally:
        if own:
            con.close()
    return [(v, n) for v, n, _ in rows]


def resolve(season: int, week: int, want: str = None,
            override: str = None) -> tuple:
    """(version, note). Raises ModelVersionError rather than guessing.

    `override` is the explicit `--model-version` escape hatch and is used as
    given, including when it has no predictions - if a caller insists on a
    version, the emptiness is theirs to explain.
    """
    have = dict(counts(season, week))
    if override:
        return override, f"model version forced to {override} by --model-version"
    if not have:
        raise ModelVersionError(
            f"no predictions at all for season {season} week {week}")
    if want is None:
        v = list(have)[0]
        return v, ""
    if have.get(want):
        return want, ""
    for alt in equivalent(want):
        if have.get(alt):
            return alt, (f"{want} has no predictions; using {alt}, which is "
                         f"RECORDED EQUIVALENT to it ({have[alt]:,} rows)")
    raise ModelVersionError(
        f"no predictions under {want}, and nothing recorded equivalent to it.\n"
        f"  versions that DO have predictions for season {season} week {week}:\n    "
        + "\n    ".join(f"{v}  ({n:,} rows)" for v, n in have.items())
        + "\n  This is a refusal, not a fallback: silently analysing a "
          "different\n  model's predictions is how an empty result gets "
          "reported as a finding.\n  Pass --model-version to choose one "
          "explicitly, or record an\n  equivalence with jobs/model_equivalence.py.")
