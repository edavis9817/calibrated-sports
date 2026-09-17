"""College football facts: ingest and normalisation (W07 track C).

CFB ships STATS AND USAGE, NOT HIT RATES. There is no public appearance signal
for a college player - no snap counts, no participation, and ESPN's
`did_not_play` is False on every row - so a game where a player recorded
nothing is indistinguishable from a game the player did not play. Hit rates, prop
history and settlement all need that distinction and cannot be built on this
data. See `cfb.limitations`, which records it as a fact, and
`tests/test_ingest_cfb.py`, which keeps settlement-shaped tables out of the
schema.

Nothing in this package imports or edits an NFL code path.
"""
