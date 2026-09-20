"""Three feeds the site does not have: injuries, weather, news.

Its own store (`<STORAGE_DIR>/feeds.db`) and its own raw archive
(`<STORAGE_DIR>/feeds/raw`). Touches no NFL code path, opens `market_log.db` never,
and publishes nothing.

The disciplines are the ones the CFB ingest already pays for, and the versioning is
literally `cfb.versioning` rather than a second copy of it:

  * raw first, manifested, content-hash deduplicated;
  * every row carries `sport`;
  * every row is versioned by INGESTION time, which is what makes an injury snapshot
    answerable as "what was known then" rather than "what turned out to be true";
  * a source that cannot be resolved is refused and counted, never guessed.
"""
