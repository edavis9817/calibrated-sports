# Known holes in the Kalshi depth series (2026)

**Read this before conditioning any result on depth.** `market_depth` is not a complete
record of the weeks it spans. A study that joins to it silently drops the markets below,
and in two weeks that is 93% of the player props. Nothing in the table says so: a market
with no depth row is simply absent from the join.

Measured 2026-10-08 16:33Z by `python -m research.mapping_lead --lines` against
`market_log.db`, read-only (unit a-73; first measured by a-68). Venue: Kalshi. Season:
2026, weeks 1 to 5. "Depth" means at least one `market_depth` row for the market; it says
nothing about how many snapshots, or about raw L2 books (see the last section).

## The holes

Share of listed Kalshi markets with at least one `market_depth` row, by week:

| week | props (REC + RSHATT) | moneyline | spread | total |
|---|---|---|---|---|
| 1 | 1,049 of 1,722 (**60.9%**) | 30 of 32 | 379 of 438 (86.5%) | 285 of 330 (86.4%) |
| 2 | 107 of 1,482 (**7.2%**) | 32 of 32 | 415 of 443 (93.7%) | 304 of 324 (93.8%) |
| 3 | 1,297 of 1,502 (86.4%) | 32 of 32 | **0 of 436 (0%)** | 304 of 319 (95.3%) |
| 4 | 119 of 1,589 (**7.5%**) | 32 of 32 | 406 of 430 (94.4%) | 306 of 324 (94.4%) |

Week 5 is in progress and is not in this table.

1. **Weeks 2 and 4, player props: 7% covered. Not recoverable.** 107 of 1,482 and 119 of
   1,589 prop markets have any depth row, against 1,297 of 1,502 in week 3.
2. **Week 1, player props: 61% covered.** The same mechanism, less severe: 1,076 of 1,722
   had an outcome before their own kickoff.
3. **Week 3, spreads: 0 of 436.** No Kalshi spread market dated 09-24 to 09-28 has a depth
   row; spread depth stops at 2026-09-22 04:14Z and resumes 2026-10-01 00:15Z. **The cause
   is not established.** What is measured: every week-3 spread outcome was created at
   2026-09-29 18:10Z, after the week was played, which is consistent with those markets
   having no outcome during the week. That is one observation, not a diagnosis.

Game lines are otherwise covered in every week, so holes 1 and 2 are a prop problem and
not a logger outage.

## Why holes 1 and 2 exist

Depth is captured only for markets that have a `market_outcome` row with an outcome
(`jobs/capture_depth.py`). Mapping ran only inside the weekly refresh, at 13:00Z on
Tuesday, Wednesday and Thursday. Kalshi lists the bulk of a week's props at about 17:00Z
on Thursday and goes on listing through Sunday. So most of a slate had no outcome until
the following Tuesday, after every game on it had been played, and was never on the depth
allowlist while its book existed.

Outcomes that existed before their market's own kickoff:

| week | listed | in time | share |
|---|---|---|---|
| 1 | 1,722 | 1,076 | 62.5% |
| 2 | 1,482 | 95 | 6.4% |
| 3 | 1,502 | 1,281 | 85.3% |
| 4 | 1,589 | 87 | 5.5% |

Week 3 is high only because the mapping job was run by hand on 09-24, 09-26 and 09-28.
The depth share tracks the in-time share almost exactly in every week, which is the check
that the mechanism is the right one.

**It cannot be backfilled.** Kalshi candlesticks carry price and volume and no book, and a
book that was not captured while it existed is gone. The quotes for these markets do
survive (a-68 measured a quote for every week 2 to 5 prop market); only depth is lost.

## What this does to a result

- **Any depth-conditioned figure over 2026 weeks 1 to 4 is a figure about week 3, plus the
  part of weeks 1, 2 and 4 that was mapped early.** The early-mapped part is not a random
  sample: in weeks 2 and 4 it is the markets listed before the week's last scheduled
  mapping run (Thursday 13:00Z), not the bulk listed after it. Treat it as a different
  population, not as a thinner one.
- **A per-week count will not show it** unless it is taken against `markets`. Counting the
  weeks present in a join to `market_depth` returns all four.
- **State the denominator.** A depth-conditioned result should print how many of the
  listed markets it kept, per week, next to the estimate.

Code that reads `market_depth` and is therefore exposed (by grep, 2026-10-08):
`research/clv.py`, `research/maker.py`, `research/consensus.py`,
`research/cross_venue_lead.py`, `research/longshot.py`, `research/structural.py`,
`research/inactives.py`, `research/sweep/h1_settlement.py`, `h2_imbalance.py`,
`h3_lifecycle.py`, `scan.py`, `jobs/reprice_ledger.py`, `jobs/audit_fees.py`,
`jobs/export_coverage.py`. Results already published from week 1 (briefs S00 to 022) used
a week that was itself 61% covered on props; none of them is re-derived here.

## How "in time" is measured, and where that stops being valid

The time a market was first mapped is read from **`outcomes.created_ts`**, not from
`market_outcome.mapped_ts`. `mapped_ts` is rewritten by every full mapping pass, so after
any Tuesday it holds that Tuesday. `created_ts` is written once and survives a re-upsert
(pinned by `tests/test_first_mapped.py`).

It is the market's own first-mapped time only where the market's venue is the one that
creates the outcome. That is true of Kalshi player props (Polymarket and the books link to
player outcomes and never create them; 0 of 6,463 Kalshi prop markets share an outcome
with another Kalshi prop market). It is **not** true of a game line, a Polymarket market or
a book line, whose outcome another venue may have created first, and it breaks if outcomes
are ever re-keyed, as the week-3 spreads show. The in-time column above is therefore
printed for props only.

## What is fixed, and what is not

- `jobs.map_markets.run_pending` maps what has no row, on the logger's own 600 s timer
  (a-68). **It has run in production since 2026-10-08 16:35:20Z** (logger build
  `edb5db1b5167`); every week before that minute is exposed to the mechanism above, and
  week 5 was mapped in time only because the pass was also run by hand at 16:23Z that day.
  Whether it keeps a whole slate above 95% in time is not yet observed for a full week:
  read `python -m research.mapping_lead --weeks 5` after Monday night.
- `analytics.staleness.check_mapped_rate` now measures the rate **before each market's own
  kickoff**, for the week being priced and the week just played, and `check_unexamined`
  counts markets the mapper has never looked at (a-73). Neither helps unless the gate is
  run; nothing schedules it.
- Week 4's quotes for the unmapped props carry no retention hold and begin pruning
  2026-10-15 (a-68). That is a second, separate loss and it is still avoidable.

## Not covered here

- **Raw L2 books** (`raw_shards`, `kalshi_depth` / `polymarket_depth`) were not archived at
  all from 2026-09-15 to 2026-10-06, for a different reason (the depth allowlist read week
  1 all season; a-67). That hole covers weeks 2 to 4 entirely and is recorded in CLAUDE.md.
- **Polymarket depth** was zero over the same span for the same reason.
- Coverage **within** a market (how many snapshots, how early the first one) is not
  measured here. "Has a depth row" is the weakest form of coverage.
