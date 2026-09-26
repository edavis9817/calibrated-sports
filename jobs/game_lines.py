"""Where a game line came from and when it was read - one definition for every producer.

WHY (unit a-37, audit N-03). `/nfl` and `/nfl/live` printed different spreads and totals
for the same games, and both said "from the schedule". Both were right for their own
read: nflverse overwrites the lines in its games file in place, the store keeps one
`nfl_games` row per `data_version`, and the manifest export and the live snapshot read
the store at different times. A reader saw two numbers for one fact and no way to tell
which was newer.

So every game line either producer publishes carries the same three fields, computed
here and nowhere else:

    line_source     which source the line is, as its source-registry id: today only
                    `nflverse.schedule`
    line_read_at    when this store WROTE the schedule row the line comes from - the read
                    of the source file that carried this value. A later read that found
                    the file unchanged writes nothing, so this can be older than the most
                    recent check; it is never later than the truth.
    line_previous   the most recent EARLIER line that differed from this one, with the
                    time it was last read - so a page can say "moved from -4.5" - or null
                    when every stored version carried this line.

WHAT "PREVIOUS" CAN AND CANNOT SEE. `data_version` is a pull DATE, so the store holds at
most one version per game per day: two moves inside one day are one move here, and the
intermediate value is gone (the day's last read overwrote it). A version with NO line
(nflverse blanks lines for weeks that are not yet posted - measured 2026-09-26 on every
2026 week-4 game, null from 09-13 to 09-21) is not a line and is skipped, so a previous
line can be a week or more older than the current one; its own `read_at` says so.

The SQL lives in each caller, not here: the source registry attributes every table read
to the producer function that runs it (`jobs/source_registry.py`), and this module is
pure so both callers can share it without either one importing the other.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

# The source's id in jobs/source_registry.py, so the site labels it from the Sources
# file (`$defs.LineSource`). The contract cannot enumerate it: it names no sport. The
# scoreboard's own odds on the Live card are a DIFFERENT source and stay in their own
# field (`scoreboard_odds`), never in this one.
LINE_SOURCE = "nflverse.schedule"

# What each caller selects from nfl_games, in this order.
HISTORY_COLUMNS = ("game_id", "data_version", "spread_line", "total_line", "ingested_ts")


def iso(ts):
    """Contract `Timestamp`, floored to the second: never later than the truth."""
    if ts is None:
        return None
    return datetime.fromtimestamp(math.floor(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(v):
    return None if v is None else float(v)


def provenance(rows) -> dict:
    """{game_id: {"line_source", "line_read_at", "line_previous"}} from every stored
    version of each game. `rows` are tuples or dicts in HISTORY_COLUMNS order.

    The CURRENT line is the newest `data_version` - the same row both producers already
    select - so the provenance always describes the value printed beside it."""
    by_game = {}
    for r in rows:
        r = dict(zip(HISTORY_COLUMNS, r)) if not isinstance(r, dict) else r
        by_game.setdefault(r["game_id"], []).append(r)
    out = {}
    for gid, versions in by_game.items():
        versions.sort(key=lambda v: (v["data_version"], v["ingested_ts"] or 0), reverse=True)
        cur = versions[0]
        now_line = (_f(cur["spread_line"]), _f(cur["total_line"]))
        previous = None
        for v in versions[1:]:
            line = (_f(v["spread_line"]), _f(v["total_line"]))
            if line == (None, None) or line == now_line:
                continue
            previous = {"spread": line[0], "total": line[1], "read_at": iso(v["ingested_ts"])}
            break
        out[gid] = {"line_source": LINE_SOURCE, "line_read_at": iso(cur["ingested_ts"]),
                    "line_previous": previous}
    return out
