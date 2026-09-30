# C26 - the parlay pre-packs, priced against their own legs: pre-registration

Written 2026-09-29, before `research/prepack_census.py` or any pricing script exists.
Track C, branch `c-26-parlay-prepacks` off `origin/main` (the unit names its base).

## The question

Ethan: *"low risk multiples are far more successful because they are more likely to
happen than the markets suggest since they have to price in other stuff."*

c-23 could not test it: no joint model exists. The brief's route around that: Kalshi
allowlists `KXNFLPREPACKSGP*` (config.py), so the pack prices should be on disk with
only their semantics missing, and a pack can be compared with the product of its own
legs on the same exchange at the same instant, with no book vig on either side.

## What has ALREADY been read, before this file was written

Stated so the order is honest. Step 0 is read-only and was run ad hoc against
`market_log.db` (`mode=ro`) before this commit:

- `markets`: **0 rows** with `market_id` in `[KXNFLPREPACK, KXNFLPREPACL)`, and 0 rows
  matching `%PREPACK%`, `%SGP%`, `%MVE%`, `%PARLAY%` (upper-cased), a `parlay` title, or
  `market_type='parlay'`. Kalshi series present in `markets`: REC, SPREAD, TOTAL,
  RSHATT, WINSWEEK, WINS, GAME, the two CHAMP series, WINSTREAK, eight divisions.
- `quotes`, `market_depth`, `market_trades` over the same ticker range: **0 / 0 / 0**.
- `market_outcome`: no `parlay: SGP pre-pack` reason exists. The mapper line in
  `venues/kalshi.py` has never fired, because no parlay row ever reached it.
- `board_019.db` (brief 019's `/series` snapshot, 2026-09-14 23:08 ET): the series
  exist - `KXNFLPREPACKSGP`, `KXNFLPREPACKSGPSPREAD`, `KXNFLPREPACK`, `KXNFLPREPACK2ML`,
  `KXNFLPREPACK3ML`, `KXNFLPREPACK1Q1H`, `KXNFLPREPACK1HFT`, `KXMVENFLSINGLEGAME`,
  `KXMVENFLMULTIGAME`, `KXMVENFLMULTIGAMEEXTENDED`, all titled "MVE NFL ...", all
  `fee_type=quadratic`, `fee_multiplier=1.0` - and **every one returned 0 events and
  0 markets** when fetched by `series_ticker`.

No pre-pack PRICE has been seen by anyone in this project, because none exists on disk.
So the verdict rule below is fixed before any number it would grade has been seen, which
is the point of writing it even though Step 0's hard exit is expected to fire.

## Step 0 - the census, and the hard exit

`research/prepack_census.py` (read-only) will re-derive every count above, and add:

1. a member-safe scan of the local Kalshi raw archive (every gzip member decoded
   separately, per the 2026-09-15 recovery rule) counting lines mentioning `PREPACK` or
   `MVE` by endpoint - which separates "discovery never asked" from "asked and got
   nothing" from "got markets and dropped them";
2. the pre-pack series' `fee_type` / `fee_multiplier` from the most recent archived
   `/series` payload - the `/series` endpoint is the fee authority, and reading its
   archived response spends nothing;
3. a live, unauthenticated, credit-free probe (a handful of public GETs, responses kept
   under the scratch output directory) of `GET /markets?series_ticker=<pre-pack series>`
   and of Kalshi's multivariate-collection endpoints, to establish WHERE pre-pack markets
   are listed if not under their series. This is the same public API the logger polls
   continuously; it costs no credits and no money.

**Hard exit.** Proceed to Step 1 only if pre-packs are quoted two-sided on at least
**8 distinct games** and at least **50 distinct pack markets**. Otherwise stop, report
the census, and specify what capture would be needed.

## Step 1 - parse gate (for when data exists)

- Every leg parsed from ticker AND title; each leg located as a separately quoted
  market on the same exchange, asserted to be the same claim (same game, same player by
  id, same stat, same threshold, same side). A name is never a key.
- A pack with any unlocated leg is excluded and COUNTED with its reason.
- Proceed to pricing only if **>= 90% of packs** resolve every leg. Below that, the
  parse census is the result.

## Step 2-3 - the measurement

At a shared time bin (as `jobs/cross_venue.py`), legs no staler than `MAX_STALE`,
strict arm = pack and every leg inside `SAME_BATCH`; loose arm reported beside it.

    premium_mid   = pack_mid - prod(leg_mid)                  descriptive only
    gap_cheap     = prod(leg_bid) - pack_ask                  pack below independence, at the touch
    gap_rich      = pack_bid - prod(leg_ask)                  pack above independence, at the touch

Fees: taker, `ceil_to_cent(M x 0.07 x C x P x (1-P))` at C = 100 on the pack leg, M as
READ from `/series` and printed. Depth: `market_depth` `touch_size` and `vwap_100` on
the pack side taken and on every leg.

**Important, and it constrains what the premium can mean.** `prod(leg prices)` is the
price of the claim IF THE LEGS WERE INDEPENDENT. It is not a replicating price: a
parlay cannot be built statically from its single legs. So a pack above the product is
what correct pricing of positively correlated legs (QB yards with WR1 yards, rho ~0.42)
LOOKS LIKE, and a premium of either sign measures the correlation the venue IMPLIES,
not a mispricing. Whether the implied correlation is wrong is only answered by
settlement, which is test C below.

## Pre-registered verdict rule

**P (the venue measurement).** "Kalshi prices pre-packs away from independence" if, in
the strict arm on the touch, mean `gap_cheap` (or `gap_rich`) net of the pack taker fee
is **>= 1.0pp** with a **game-block bootstrap 95% interval excluding zero**, over
**>= 8 distinct games**. An interval over fewer than 5 games is not read, whatever it
excludes.

**C (the question Ethan asked).** "Multiples happen more often than priced" if the
realized settle rate of packs minus the pack ASK, net of the taker fee at C = 100, is
**>= 1.0pp** with a game-block bootstrap 95% interval excluding zero over **>= 8
games**. Reported on the YES side only (one side per slice).

**Tradeable, not merely present.** Either P or C is called tradeable only if, on the
instances that pass, the pack side taken carries **touch_size >= 10** and a non-null
`vwap_100`, the gap recomputed at `vwap_100` is still > 0 on at least half of them, and
every leg used in the product had touch_size >= 10 on both sides (so the benchmark is
not built from a one-contract quote - brief 019's thinner-leg median was 1).

**What it means for Ethan's question, both directions.**
- C passes: on Kalshi pre-packs, multiples settled YES more often than their executable
  price implied, by at least 1pp net of fee - evidence FOR the claim on this venue,
  and an edge only if the depth condition also passes.
- C fails with an interval containing zero: this venue's pre-packs are priced at
  least as well as we can measure - no evidence for the claim on this venue, which is
  not evidence against it at US sportsbooks, where c-23's 6-10pp vig headwind applies.

P alone answers neither direction of Ethan's question: a positive premium is
information about the venue, not an edge.

## Invariants

`market_log.db` opened `mode=ro` only. Nothing published to the site. No credits spent.
