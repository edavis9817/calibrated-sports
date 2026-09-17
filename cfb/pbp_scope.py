"""The play-by-play scope boundary as code: 2022 is when FCS entered the feed.

`cfbfastR_cfb_pbp` goes from 887 games in 2021 to 1,459 in 2022 and 1,657 in 2025.
None of that is better coverage. FBS-vs-FBS is FLAT at 770-807 games for twelve
seasons; the jump is FCS games entering the feed - fcs/fcs is 0 in 2021 and 519 in
2022 - plus a handful of fcs/ii. Measured 2026-09-17, `docs/C02-cfb-pbp-survey.md`,
reproduced by `python -m research.cfb_pbp_survey --divisions`.

So ANY league-wide quantity pooled across 2021 -> 2022 moves by roughly 60% for a
reason that has nothing to do with football. A footnote about that is forgotten; this
raises instead.

    check([2019, ..., 2025])                          -> ScopeError
    check(seasons, division_scope=FBS_VS_FBS)         -> fine, and says so
    check(seasons, declared="FCS enters in 2022; ...") -> fine, and repeats it

`check` returns the scope statement it approved, so the caller has one line to print,
store beside a figure, or put on a page. It is deliberately not a boolean: a guard
whose result can be dropped on the floor is a guard that will be.

THE RULE THIS SITS BESIDE: a text-parsed name column is never a key. In this same
feed `rusher_player_name` went 0.37 -> 0.21 -> 0.000 of plays across 2024-2026 while
`rush_player_id` held flat - a column being retired live. `key_column` refuses the
name columns by name.
"""
import re

SCOPE_CHANGE_SEASON = 2022
FBS_VS_FBS = "fbs_vs_fbs"
ALL_DIVISIONS = "all_divisions"

# Measured 2026-09-17 (cfbfastR_cfb_pbp joined to cfb_games). The error message
# quotes these; `research/cfb_pbp_survey.py --divisions` writes the same numbers to
# `cfb_measurements` as `pbp.games_by_division`, so the premise is query-derived.
MEASURED = {2019: {"fbs/fbs": 773, "fbs/fcs": 114, "fcs/fcs": 0},
            2021: {"fbs/fbs": 770, "fbs/fcs": 116, "fcs/fcs": 0},
            2022: {"fbs/fbs": 776, "fbs/fcs": 120, "fcs/fcs": 519},
            2025: {"fbs/fbs": 807, "fbs/fcs": 126, "fcs/fcs": 669}}

# Parsed from the play text and being retired upstream. Never a key, never a join.
NAME_COLUMNS = ("rusher_player_name", "passer_player_name", "receiver_player_name",
                "sack_player_name", "sack_players", "kickoff_returner_player_name",
                "punt_returner_player_name", "fumble_player_name",
                "fumble_forced_player_name", "fumble_recovered_player_name",
                "interception_player_name", "pass_breakup_player_name",
                "punter_player_name", "kickoff_player_name", "fg_kicker_player_name")
_NAME_SHAPED = re.compile(r"_player_name$|^.*_players$|_player_name\d+$")


class ScopeError(Exception):
    pass


def spans_change(seasons) -> bool:
    s = sorted(set(int(x) for x in seasons))
    return bool(s) and s[0] < SCOPE_CHANGE_SEASON <= s[-1]


def sql_filter(alias="g") -> str:
    """The FBS-vs-FBS restriction, against a joined `cfb_games` row."""
    return f"{alias}.home_division = 'fbs' AND {alias}.away_division = 'fbs'"


def check(seasons, division_scope=None, declared=None) -> str:
    """Approve a season range and return the scope statement. Raises ScopeError when
    a range crosses 2022 with neither an FBS-vs-FBS restriction nor a declaration."""
    s = sorted(set(int(x) for x in seasons))
    if not s:
        raise ScopeError("no seasons given; a scope over nothing is not a scope")
    span = f"{s[0]}-{s[-1]}" if len(s) > 1 else str(s[0])
    if division_scope == FBS_VS_FBS:
        return f"{span}, FBS vs FBS only (770-807 games a season, flat across 2022)"
    if not spans_change(s):
        side = "before" if s[-1] < SCOPE_CHANGE_SEASON else "from"
        return f"{span}, one side of the {SCOPE_CHANGE_SEASON} scope change ({side} it)"
    if isinstance(declared, str) and declared.strip():
        return f"{span}, ALL DIVISIONS, scope change declared: {declared.strip()}"
    raise ScopeError(
        f"seasons {span} pool across the {SCOPE_CHANGE_SEASON} scope change. FCS entered "
        f"cfbfastR_cfb_pbp that season - fcs/fcs games {MEASURED[2021]['fcs/fcs']} in 2021 "
        f"and {MEASURED[2022]['fcs/fcs']} in 2022 - while FBS vs FBS stayed flat "
        f"({MEASURED[2021]['fbs/fbs']} -> {MEASURED[2022]['fbs/fbs']}). Pass "
        f"division_scope=FBS_VS_FBS, or declared='<why pooling is right here>'.")


def key_column(name: str) -> str:
    """Return `name` if it may be used as a key; raise if it is a parsed name column."""
    if name in NAME_COLUMNS or _NAME_SHAPED.search(name or ""):
        raise ScopeError(
            f"{name!r} is parsed from the play text and is never a key: "
            f"rusher_player_name ran 0.37 of plays in 2024, 0.21 in 2025 and 0.000 in "
            f"2026 while rush_player_id held flat. Key on the id column.")
    return name
