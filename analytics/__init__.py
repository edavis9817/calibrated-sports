"""Track F - play-by-play analytics.

Scope: processing and export only. No pages, no edits to `jobs/export_web.py`,
`contract.schema.json` or any shared NFL code path. Everything here is new
files under `analytics/` plus its own database.

THE STRUCTURAL RULE, and the reason this package exists as its own thing:

    No analytic is published without an interval and a sample count.

Every stats site publishes point estimates. Publishing the uncertainty is the
differentiator, so it is enforced rather than remembered - `analytics.gate`
fails any table or payload carrying a rate without its `n` and its interval,
the same shape as `cfb.guards` failing on a hit-rate-shaped table.

The database is `<STORAGE_DIR>/analytics.db`, resolved through
`config.storage_path` and never hardcoded. `market_log.db` is the live logger's
and is opened `mode=ro` only, through `analytics.paths.market_log_ro()`: a bulk
write holding SQLite's WAL writer lock makes the logger drop quotes.
"""
