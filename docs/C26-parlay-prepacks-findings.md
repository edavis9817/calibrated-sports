# C26 - parlay pre-packs: findings (Step 0 hard exit)

Pre-registration: `docs/C26-parlay-prepacks-preregistration.md` (commit a23a76b, pushed
before the script existed). Script: `research/prepack_census.py`. Aggregates:
`research/prepack_census.json`. Run 2026-09-29/30 ET.

## Verdict: the hard exit fired. There is nothing to price.

| where | pre-pack rows |
|---|---|
| `market_log.db` `markets` (ticker range `KXNFLPREPACK*`, and any PREPACK/SGP/MVE/COMBO/parlay spelling) | **0** |
| `quotes` / `market_depth` / `market_trades` in that range | **0 / 0 / 0** |
| `market_outcome` rows with reason `parlay: ...` | **0** - the mapper branch has never fired |
| local Kalshi raw archive, 172 shards, 2026-09-23 -> 09-30 (150,574 lines at the final run, 0 torn members) | pre-pack mentions only in the 7 `/series` payloads; **0 pre-pack market tickers** in any markets or orderbooks response |
| `board_019.db` (019's catalogue, 2026-09-14) | 11 pre-pack/MVE/COMBO series listed; **every one fetched 0 events, 0 markets** |
| live `/markets?series_ticker=...&status=open`, all 11 series, 2026-09-30 | **0 open** |

The pre-registered bar was >= 50 packs quoted two-sided over >= 8 games. The store holds
zero packs over zero games.

## Why: Kalshi has listed no NFL pre-packs this season

The brief's premise was "the pre-pack prices are on disk and only their semantics are
missing". It is false, and the cause is not our discovery code:

- `/events?series_ticker=` lists pre-pack events from the **2025-26 season only**:
  `KXNFLPREPACKSGP` 92 events (61 Dec 2025, 27 Jan 2026, 2 Feb 2026), `KXNFLPREPACK2ML`
  22, `KXNFLPREPACK3ML` 16, `KXNFLPREPACK` 17 (Oct-Nov 2025), `KXNFLPREPACK1HFT` 3,
  `KXNFLCOMBO` 6 (five of them Sep 2025). **None is dated in the 2026-27 season.**
- `/markets?series_ticker=KXNFLPREPACKSGP` (every status, paged) still returns 36 markets
  over 2 events, all `closed`, closing 2025-12-08/09, **all with volume 0**. The other
  series return no markets at all - older events' markets are no longer served by that
  endpoint. So what is visible of last season's packs never traded.
- The allowlist keeps the series and the fetch returns nothing because there is nothing
  open. `venues/kalshi.py`'s `parlay` branch is correct and unreached.

## What the pre-packs ARE, when they exist - and it changes the question

The SGP pre-packs are **game-line combos**, not player-prop parlays. Their events carry
`competition_scope` "Combo", "Spread and Total", "Moneyline and Total"; sample titles are
"Spread / Total Points Combo" and "56 wins & Over 41.5 points". The multi-game packs
(`2ML`, `3ML`) are moneyline combos across games. Only `KXNFLCOMBO` carried player legs
("Los Angeles C Wins, Over 46.5 points scored, and Ladd McConkey TD"), on 5 events in Sep
2025. So the `config.py` comment that the pre-packs are "where the QB<->WR1 correlation
of 0.42 gets mispriced" does not describe what Kalshi listed: the correlation a spread +
total combo carries is margin-with-total, not QB-with-receiver.

## Fees, as read from `/series` (live, 2026-09-30)

All 11 series: `fee_type = quadratic`, `fee_multiplier = 1`. Taker M = 1, **maker free**.
Matches the archived `/series` payload and 019's snapshot.

## For c-23 and Ethan's question

This **neither reopens nor closes** the multiples question. It stays where c-23 left it:
"stopped, joint not measured". c-26 adds one fact: the same-exchange route around the
missing joint model is not available this season, because the venue lists no packs.
It is not evidence either way about whether multiples beat their price.

## What would reopen it

1. Kalshi lists 2026-27 pre-packs (last season's first SGP event was Dec 2025, so they
   may return mid-season). The logger's allowlist already keeps them; nothing to build
   to START capture. What is missing is the leg parser and depth snapshots, which are
   only worth building once packs are listed.
2. A historical version off last season's candles cannot meet the pre-registered depth
   condition (depth is unrecoverable after the fact) and the visible packs never traded,
   so it is not recommended.
