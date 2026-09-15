# Inactives capture: forward spec for Sunday 2026-09-20

**Spec only. Nothing here is built or scheduled.** It is written because brief
023 Part 3a found no announcement timestamp anywhere on disk.
- nflverse injuries (archived under `raw/nflverse_023`) carry a Wed–Fri report
  status and a practice status per player-week, and no time column.
- Snap counts, player-week stats, rosters and participation are all post-game
  facts.

## What the logger already captures in the T-120 → T-60 window
With no changes, for every game on the slate:
- **Kalshi quotes** for `KXNFLREC` and `KXNFLRSHATT`:
  - hot tier, polled every 15s from kickoff-240 min;
  - a row is written on a change or a 5-minute heartbeat, so a quiet market's
    effective resolution is its last change;
  - raw `/markets` payloads are archived, carrying `status`, `updated_time`,
    `yes_bid_size_fp` and `yes_ask_size_fp`.
- **Depth** (`market_depth`) every 60s: touch price, touch size and VWAP at
  100/500/1000/5000 contracts.
- **Book props** via the Odds API snapshot ladder (`ODDS_SNAPSHOTS_MIN` includes
  90, 45, 20 and 5 min before kickoff). These are prices at scheduled instants
  only, but they give a coarse book-side second-order check: T-90 before the
  list lands, T-45 after it.
- **Kalshi's own reaction.** Brief 023's week-1 proxy found Brock Bowers's
  markets stopped quoting on the Saturday, a day before kickoff. Kalshi can pull
  a player's markets on the Friday/Saturday report, well before the game-day
  list. A Sunday run must record that pull time as well as the inactive-list
  time.

## The missing piece: a timestamped inactive source

| source | what it timestamps | automation risk | cadence | cost |
|---|---|---|---|---|
| ESPN public site API, game `summary` (`site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=<id>`) and team injuries | Nothing itself. **Our fetch time** of the first payload in which a player appears as inactive/out is an upper bound on the announcement, at the polling resolution | Unofficial and unkeyed; shape can change without notice; rate limits undocumented | every 60s per game from kickoff-120 to kickoff-45 | free; ~75 requests per game, ~975 for a 13-game Sunday |
| NFL game-day inactive list (NFL Communications / nfl.com game center) | The official list, released ~90 min before kickoff | No documented public API; scraping a page or a PDF, highest breakage risk | every 60s in the same window | free |
| Team and beat-reporter social posts | Wall-clock post time | Not automatable within the stack; manual | - | - |
| nflverse `injuries` | No time column (confirmed) | - | weekly | free |

**Recommendation for Sunday.** Poll the ESPN summary for each game every 60s
from kickoff-120 to kickoff-45:
- **Archive every payload raw first**, under its own venue directory, e.g.
  `espn_inactives`. It must not be a directory the logger writes, because two
  writers corrupt shards.
- **Derive `first_seen_inactive_ts`** per player by diffing consecutive payloads.
- Treat the result as an upper bound with 60s resolution, and say so wherever
  it is used.
- In the same poll, record the Kalshi market-status change time per player from
  the raw `/markets` payloads the logger already archives. That catches
  pre-game pulls like the Bowers one.

## Prerequisite that must be fixed first
- **Only mapped markets can enter the analysis.** Brief 022 counted 673 week-1
  prop markets with no `market_outcome` mapping, and the week-1 proxy saw 132
  mapped REC/RSHATT players.
- An inactive or a teammate whose markets are unmapped is invisible. Map before
  Sunday, or the second-order set is whatever happened to map.

## Measurements a Sunday run supports
For each player first seen inactive at `t_a`, and for his markets' pull time
`t_p` if earlier:
1. **Direct.** His own ladder:
   - mid and spread at `t_a` - 5 min;
   - the mid path at the effective resolution;
   - time from `t_a` to the collapse below 0.10, or to stopped quoting.

   This is now measurable, because `t_a` comes from a source independent of the
   market.
2. **Second-order.** Same team, same position group (WR/TE on `KXNFLREC`, RB on
   `KXNFLREC` and `KXNFLRSHATT`), every rung two-sided at `t_a` - 5 min:
   - move at +1, +5, +15 and +30 min;
   - time to within 1c of the +30 min level;
   - touch size on the moving side from `market_depth` at `t_a`, +1 and +5 min.
3. **Placebo.** Same measures for the opposing team's players at the same
   clock time.
4. **Book side (coarse).** The same teammates' Odds API prop lines at T-90
   versus T-45.

## The bar (023 §3c) — what the check needs
A second-order rung qualifies only if ALL hold:
- **Move size:** the move exceeds **3.6pp** (props, held to settlement).
- **Timing:** it takes **more than 60 seconds** from `t_a` to reach its new
  level, measured on the effective resolution. `t_a`'s own 60s uncertainty
  counts against it, not for it.
- **Depth:** the touch on the moving side holds **at least 10 contracts** in
  the transition.
- **Placebo:** the move clears the placebo's move at the same horizon.

One Sunday is anecdote, a handful of events over a few games. Any positive result
is a candidate for a pre-registered multi-week collection, not a finding.

## Why Kalshi REC/RSHATT is the only second-order surface
- **The logger's Kalshi allowlist tracks `KXNFLREC` and `KXNFLRSHATT` among player
  props.** It does not track `KXNFLRECYDS` (receiving yards), which is where
  "WR1 out raises WR2" would move most.
- **Book props are snapshots, not a stream.** Adding `KXNFLRECYDS` to the
  allowlist is the one change that would widen the surface. It is a logger
  change, so it is not part of this spec.
