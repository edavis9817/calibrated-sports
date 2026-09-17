"""The settlement rule. ONE copy, called by everything that settles.

THE DEFECT THIS REPLACES. The rule existed TWICE and the two copies disagreed on
3,272 outcomes, every one of them defensive:

    research/bookvbook.corrected_value     judged on DEFENSIVE snaps for defensive
                                           stats - correct
    research/walkforward.corrected_result  judged EVERY stat on OFFENSIVE snaps

walkforward's copy is wrong for tackles and sacks. A linebacker's
`offense_snaps` is 0 in every game he ever plays, so "offense_snaps > 0" voids
exactly the outcomes where he played and recorded nothing - his realized zeros.
It never fired only because `walkforward.STATS` is ("receptions",
"rush_attempts"). That is why the bug was DORMANT, not why it was SAFE: an
offense-only branch in `settle_one` would have voided 3,263 real defensive
zeros, every one in the direction that flatters the model, and no test in the
suite would have caught it.

Lifting the correct copy into a third place would have made three copies. There
is one, here, and the callers import it.

WHY A MISSING ROW IS NOT ONE THING. nflverse weekly stats carry NO ROW for a
player who played and recorded nothing - verified on the raw parquet: Calvin
Ridley, De'Zhaun Stribling, Elijah Arroyo and Odell Beckham Jr. played 11-32
offensive snaps in 2026 week 1 and are absent from the file itself, not just
from the table. So a missing row is neither "inactive" nor "unresolvable"; it is
one of five things, and only the snap counts separate them.

THE PARTITION, measured 2026-09-16 over the 16,945 unsettled outcomes on one
deduped base (MAX(data_version) per player/season/week, as `load_snaps` uses):

    A    snaps > 0 in the stat's own phase      13,182   actual = 0, the over loses
    B    snaps = 0                               3,315   void, did_not_play
    C1a  no snap row, player has snap rows
         elsewhere                                 309   void, inactive
    C1b  no snap row, pfr_id appears nowhere        44   UNSETTLED
    C2   the week has no snap data at all           95   UNSETTLED

    13,182 + 3,315 + 309 + 44 + 95 = 16,945, exactly.

C1b AND C2 STAY UNSETTLED ON PURPOSE. There the absence is OUR JOIN FAILING,
not the player sitting, and a settlement is a claim about the player. The 44 are
a known mapper defect - a 2026-09-10 backfill attributed a Cleveland receiver's
outcomes to a 1981 linebacker - which is a row to repair, not a result to
record. Writing "void" over a join failure would launder a data defect into a
fact.

THE PHASE IS THE WHOLE POINT. Pick the snap column from the STAT, never from the
player: a defender has 0 offensive snaps in every game, and an offensive player
has 0 defensive snaps in almost all of them.
"""

# Settlement results. `void` is new (2026-09-16): a prop the venue returns
# rather than grades. It is NOT a push - a push is a tied bet at a settled
# number, a void is no number at all - and every consumer that filters
# `result IN ('over','under')` excludes both correctly.
OVER, UNDER, PUSH, UNSETTLED, VOID = "over", "under", "push", "unsettled", "void"

# Why a void was voided. Extensible; these are the two the evidence supports.
DID_NOT_PLAY = "did_not_play"      # case B: a snap row exists and reads zero
INACTIVE = "inactive"              # case C1a: no snap row, but he plays elsewhere

# Stats recorded on defence. Everything else is judged on offensive snaps.
DEFENSIVE_STATS = {"tackles_assists", "sacks"}


def snaps_played(snap, stat):
    """Snaps in the phase THIS STAT is recorded in, or None if there is no row.

    `snap` is (team, offense_snaps, defense_snaps). The phase comes from the
    stat because it cannot come from the player: a linebacker's offensive snap
    count is 0 in every game he plays, so judging his tackles on it voids
    precisely his zeros.
    """
    if snap is None:
        return None
    return (snap[2] if stat in DEFENSIVE_STATS else snap[1]) or 0


def settled_value(pw_value, has_pw_row, snap, stat,
                  player_has_snaps=None, week_has_snaps=None):
    """The rule. -> (value, status, void_reason).

    `value`        the realized stat, or None when this outcome cannot settle
    `status`       a census string - why this row landed where it did
    `void_reason`  DID_NOT_PLAY | INACTIVE, or None (None + value None =
                   genuinely unsettled, not void)

    `player_has_snaps` / `week_has_snaps` separate C1a from C1b and C2 and are
    the difference between "he sat" and "our join failed". Left as None they are
    UNKNOWN, and an unknown absence is treated as did-not-play - which is the
    behaviour the research scripts had before this module existed. Anything that
    WRITES a settlement must pass them, because a void written over a join
    failure is a fabricated fact.
    """
    if has_pw_row:
        return pw_value, ("value" if pw_value is not None else "null stat"), None

    played = snaps_played(snap, stat)
    if played is not None:
        if played > 0:
            return 0.0, "played, no stat row -> 0", None
        return None, "snaps = 0 -> void", DID_NOT_PLAY

    # No snap row for this player-week. Three different absences.
    if week_has_snaps is False:
        return None, "no snap data for the week -> unsettled", None
    if player_has_snaps is False:
        return None, "player has no snap row anywhere -> unsettled", None
    if player_has_snaps is True:
        return None, "no snap row, plays elsewhere -> void", INACTIVE
    return None, "no snap row, coverage unknown -> void", DID_NOT_PLAY


def resolve(actual, line, push_possible) -> str:
    """The pure decision: does the over win? Kept free of I/O so it can be read
    at a glance - this is the function that decides whether a bet won."""
    if actual is None or line is None:
        return UNSETTLED
    if actual > line:
        return OVER
    if actual < line:
        return UNDER
    # Exactly on the line. Only reachable when the line is an integer on a
    # discrete stat; a half-point line can never land here.
    return PUSH if push_possible else OVER


def settle(pw_value, has_pw_row, snap, stat, line, push_possible,
           player_has_snaps=None, week_has_snaps=None):
    """The whole rule, end to end. -> (result, actual, void_reason, status).

    This is what a settler wants: the four cases collapsed into a result it can
    store. `void` carries its reason; `unsettled` never does.
    """
    value, status, reason = settled_value(
        pw_value, has_pw_row, snap, stat,
        player_has_snaps=player_has_snaps, week_has_snaps=week_has_snaps)
    if reason is not None:
        return VOID, None, reason, status
    if value is None:
        return UNSETTLED, None, None, status
    return resolve(float(value), line, bool(push_possible)), float(value), None, status
