"""The scope boundary as code: what a CFB table may and may not look like.

CFB supports statistics and usage. PER-GAME display is permitted - a posted
line, the actual statistic and whether it cleared or missed for that one game.
AGGREGATE rates across games are not, and neither is settlement: nothing records
whether a college player dressed, so a game with no stat row cannot be graded,
voided or counted. See `cfb.limitations` (`cfb.stats_and_usage_only`).

`scope_violations(schema.TABLES)` must return []. The test suite asserts it,
and asserts that the checker itself catches each banned shape - a guard that
has never been seen to fail is not a guard.
"""
import re

# A number summarised ACROSS games. Banned anywhere.
AGGREGATE = re.compile(
    r"hit_?rate|clear(ed)?_?rate|miss(ed)?_?rate|over_?rate|under_?rate|cover_?rate|"
    r"win_?rate|_pct$|^pct_|percent|streak|record|"
    r"(^|_)l\d+(_|$)|last_?\d+|"
    r"(^|_)n_(cleared|missed|hits|overs|unders|games_(cleared|missed))|"
    r"(cleared|missed|hits|overs|unders)_(count|total|n)$|games_(cleared|missed|over|under)",
    re.I)

# A grade for ONE game. Allowed only in a table keyed on game_id.
PER_GAME_GRADE = re.compile(r"cleared|missed|graded|went_over|went_under|beat_line", re.I)

# Settlement and appearance. Banned anywhere: grading a void or an appearance
# needs exactly the signal CFB does not have.
SETTLEMENT = re.compile(r"settlement|void|(^|_)push(_|$)|did_not_play|dnp|(^|_)played|appear|"
                        r"inactive", re.I)


def scope_violations(tables):
    """[(table, column, reason)] for every column that crosses the boundary."""
    out = []
    for table, (key, cols) in tables.items():
        names = [table] + [c for c, _t in cols]
        for name in names:
            where = (table, None if name == table else name)
            if AGGREGATE.search(name):
                out.append(where + ("aggregate rate across games",))
            if SETTLEMENT.search(name):
                out.append(where + ("settlement or appearance",))
            if PER_GAME_GRADE.search(name) and "game_id" not in key:
                out.append(where + ("a grade in a table not keyed on game_id",))
    return out
