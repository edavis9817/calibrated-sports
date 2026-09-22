"""MLB stats - games, team-games, player-games, player-team-seasons. Stats only.

Its own store (`<STORAGE_DIR>/mlb.db`) and its own raw archive (`<STORAGE_DIR>/mlb/raw`).
Touches no NFL code path, opens `market_log.db` never, and publishes nothing.

NO ODDS, NO PROPS, NO MARKETS, NO CREDITS. Odds for this sport are a later, deliberate
decision (c-03). Nothing in this package can build a URL to a price source: `mlb.sources`
names the one host it fetches from and refuses the rest by name.

THE SOURCE IS RETROSHEET, AND THE TERMS ARE WHY. Its notice (archived verbatim beside
the data as `notice.txt`) reads: "Recipients of Retrosheet data are free to make any
desired use of the information, including (but not limited to) selling it, giving it
away, or producing a commercial product based upon the data", with one requirement - a
prominent attribution statement, carried in `mlb.sources.ATTRIBUTION`. MLB's own Stats API
is the obvious feed and is refused: MLBAM's notice reads "Only individual, non-commercial,
non-bulk use of the Materials is permitted" - and an ingest is bulk by definition.

The disciplines are the CFB/feeds ones, reused rather than copied: raw first and
content-hashed (`feeds.fetch.archive`), every row versioned by INGESTION time
(`cfb.versioning.apply`), `sport` on every row, and a column that upstream leaves blank
stored as NULL - unknown - never as zero.
"""
