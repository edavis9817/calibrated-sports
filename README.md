# Market data logger

Captures NFL market prices from Kalshi and Polymarket into SQLite plus a raw
JSON archive. This is the time-critical piece of the project: Kalshi split live
and historical data tiers on 2026-02-19, so granular book history is **not
backfillable**. Whatever you don't log this week is gone.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install httpx

python probe.py        # FIRST. verifies endpoints, dumps real payload shapes
python run_logger.py   # then this, and leave it running
```

`probe.py` matters more than it looks. **Every field name in `venues/*.py` was
written from API documentation, not from an observed response** — the machine
that wrote this couldn't reach either venue. The probe writes real payloads to
`data/probe/` and prints their keys so you can fix mismatches in one place, in
two minutes, rather than at 1pm on Sunday.

Likely first-run fixes:
- `KALSHI_BASE` if the host moved (probe will 404)
- `venues/kalshi.py::_top` — ladder format under `yes`/`no`
- `venues/polymarket.py::_best` — whether book levels are dicts with `price`
- the NFL filters in both `list_markets` once you see real ticker/slug grammar

## Layout

| file | role |
|---|---|
| `config.py` | env-overridable settings; no secrets in git |
| `store.py` | SQLite schema + gzipped raw archive |
| `venues/base.py` | HTTP client, rate limiting, backoff, normalized types |
| `venues/kalshi.py` | Kalshi adapter (batched orderbooks, cents → probability) |
| `venues/polymarket.py` | Polymarket adapter (gamma discovery + CLOB book) |
| `probe.py` | one-shot endpoint/shape verification |
| `run_logger.py` | the long-lived polling loop |

## Design decisions worth knowing

**Raw first, always.** Every response is archived verbatim before parsing.
Parsers have bugs and venues change shapes mid-season; the raw archive is the
only thing that lets you re-derive history after you fix a parser.

**Append only.** Quote rows are never updated. Storage is cheap; a corrupted
time series is not.

**SQLite on purpose.** Zero infra, one file, comfortably handles an NFL season
at these cadences. Move to Postgres/Timescale when you add a second sport or
need concurrent writers — not before.

**Not serverless.** Cron minimums and cold starts make 15-second pre-kickoff
polling impossible. Run under systemd or a Fly/Render worker with
restart-always.

**Batched orderbooks.** Kalshi takes up to 100 tickers per call. One-at-a-time
would blow the rate limit before covering a Sunday slate.

**Blind backoff.** Kalshi sends no `Retry-After` on a 429, so the client stays
conservatively under the limit and backs off exponentially with jitter.

**`poll_log` table.** Every cycle records success/failure. Coverage gaps become
visible instead of silent — check it before trusting any analysis:

```sql
SELECT venue, date(ts,'unixepoch') d, COUNT(*) polls,
       SUM(ok) ok, SUM(n_quotes) quotes
FROM poll_log GROUP BY 1,2 ORDER BY 2 DESC;
```

## Adding a sportsbook feed

Implement `list_markets()` and `fetch_quotes()` from `venues/base.py::VenueClient`
and register it in `run_logger.py`. Nothing else changes — the whole point of the
normalized quote row. **Log Pinnacle if your provider carries it**; closing-line
value should be measured against the sharp close, not against the book you bet at.

## First analysis, once you have a few days of data

Cross-venue dispersion on the same event, fee-adjusted. That single query decides
whether Week 2 real money is a market-structure play or a model play:

```sql
-- pair up quotes captured within 60s of each other across venues,
-- then compare mids on markets you've mapped to the same outcome
```

You'll need an event-mapping table first (Kalshi tickers ↔ Polymarket slugs ↔
book event ids). That mapping is unglamorous, entirely manual for ~16 games a
week, and it is the actual prerequisite for every cross-venue comparison.
Budget an hour for it and don't skip it.
