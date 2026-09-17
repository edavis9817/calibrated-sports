"""College football facts: ingest and normalisation (W07 track C).

CFB ships STATS, USAGE AND PER-GAME RESULTS - NOT AGGREGATE HIT RATES. There
is no public appearance signal for a college player - no snap counts, no
participation, and ESPN's `did_not_play` is False on every row - so a game
where a player recorded nothing is indistinguishable from a game the player did
not dress for. A posted line, the actual stat and cleared/missed for ONE game
may be shown; a rate across games, settlement and voiding may not. See
`cfb.limitations` and `cfb.guards`, which the test suite enforces.

Nothing in this package imports or edits an NFL code path.
"""
