"""THE aggregation rule for MLB, implemented once. Every total anywhere - the store's
`season_totals`, the export's season, career and team-split totals - folds through
`add` below. There is no second copy.

Why this file exists (c-07). c-03 shipped the rule twice: a SQL `CASE WHEN COUNT(col) =
COUNT(*) THEN SUM(col) END` in `jobs.ingest_mlb.season_totals`, and a Python fold in
`jobs.export_mlb_web`. They agreed on 90,116 of 90,116 cells (f-03), and nothing kept
them agreeing: an edit to one would have published totals that the store's own reader
disagreed with, and no test compares the two. The fix is to delete one, not to test that
two stay equal. The SQL half was deleted; `tests/test_ingest_mlb.py` fails if a
`SUM(` reappears in either module.

THE RULE, in two parts:

1. NULL IS UNKNOWN AND IT IS CONTAGIOUS. A total over a run of games is NULL unless every
   game in it is known. SQL `SUM` skips NULL, so a SUM over a column that is blank in one
   game reads as a confident, smaller number - the silent-zero class wearing a different
   hat. Once a total is NULL it stays NULL.
2. A TIEBREAKER IS REGULAR SEASON. Retrosheet files "Game 163" as gametype `playoff`; MLB
   counts it in regular-season statistics (Holliday 2007: 214 H / 135 RBI on `regular`
   alone, the official 216 / 137 with the 2007-10-01 tiebreaker; c-03). 7 such games
   1999-2025.
"""

REGULAR_SEASON = ("regular", "playoff")

_UNSET = object()


def add(acc, value):
    """One step of the fold: NULL in either operand makes the result NULL."""
    if acc is None or value is None:
        return None
    return acc + value


def total(values):
    """The fold over a sequence. An EMPTY sequence totals to 0, which is what a SQL
    GROUP BY never produces (it emits no group) - callers only total groups that exist."""
    acc = 0
    for v in values:
        acc = add(acc, v)
    return acc


def fold_into(acc: dict, stats: dict):
    """Accumulate a stats dict into `acc` in place, key by key, under `add`."""
    for k, v in stats.items():
        acc[k] = add(acc.get(k, 0), v)
    return acc


def season_type(gametype: str) -> str:
    """The bucket a game's line counts toward: a tiebreaker counts as `regular`."""
    return "regular" if gametype in REGULAR_SEASON else gametype
