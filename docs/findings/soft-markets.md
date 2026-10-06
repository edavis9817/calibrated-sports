# c-33 — where the sportsbook's price is weakest, by its own behaviour

Unit c-33, track C. Script `research/soft_markets.py`, tests `tests/test_soft_markets.py`,
pre-registration `docs/C33-soft-markets-preregistration.md` (pushed at `a112411` before the
script existed; addendum 1 pushed at `76af522` after one smoke run and before the run reported
here). Run 2026-10-06 04:00 UTC from an extract of `market_log.db` taken 03:56 UTC, `mode=ro`.

**Credits spent: 0.** No HTTP request was made; the script imports no client (asserted in the
tests). Everything is computed from rows the logger had already bought.

**This is not an edge and names none.** No forecast is involved anywhere. Every figure is about
US sportsbooks as returned by The Odds API `us` region (no Pinnacle), NFL only.

## Scope of each number

| panel | what | when | games |
|---|---|---|---|
| **L** | live Odds API reads, 8 two-sided prop keys + 6 game-line keys | 2026 weeks 3-4 only (rows 09-24 to 10-06 UTC) | 32 |
| **H** | purchased closes, 5 prop keys + h2h/spreads/totals | 2023-2025, one close per prop | 855 |

The panels are never pooled. Panel L is two weeks of one season: its ranks are ranks, not
constants.

## 1. The catalogue — the brief's "two of fifteen" is two of 33

The Odds API documents **33 standard NFL player-prop keys and 26 alternates**
(betting-markets page, read 2026-10-05). What is on disk:

| key | panel | books | claim-lines/game | two-sided | held from |
|---|---|---|---|---|---|
| player_receptions | L, H | 7 / 11 | 16 / 24 | yes | 2023-09 (closes); live 2026-09-24 |
| player_reception_yds | L, H | 7 / 11 | 97 / 71 | yes | same |
| player_rush_attempts | L, H | 5 / 9 | 6 / 6 | yes | same |
| player_tackles_assists | L, H | 3 / 9 | 11 / 12 | yes | same |
| player_sacks | H | 4 | 14 | yes | 2024-09 (closes only) |
| player_rush_yds | L | 7 | 51 | yes | live 2026-09-24 |
| player_pass_yds | L | 7 | 48 | yes | live 2026-09-24 |
| player_pass_attempts | L | 5 | 4 | yes | live 2026-09-24 |
| player_pass_tds | L | 7 | 2 | yes | live 2026-09-24 |
| player_anytime_td | L | 8 | 34 | **no** | live 2026-09-24 |
| 4 `_alternate` ladders (receptions, reception/rush/pass yds) | L | 7-8 | 100-430 | no | live 2026-09-24 |
| spreads_h1, totals_h1, team_totals_h1 | L | 8 / 8 / 3 | 3-4 | yes | live 2026-09-24 |

"Held from 2026-09-24" is the retention edge, not the start of capture: these rows are
`source='live'` and prune at 14 days. `markets.first_seen` for the same keys is 2026-09-17.

- **The model prices 2 of the 10 standard keys we hold, and 2 of the 33 that exist.**
- **The production logger already captures 9 standard prop keys**, not the three in
  `config.py`'s default: both `.env` files set `ODDS_PROP_MARKETS` to 16 keys. That is why this
  survey needed no credits.
- **23 standard keys are held nowhere and are NOT measured here**: pass completions, pass
  interceptions, longest completion/rush/reception, rush TDs, reception TDs, rush+reception
  yards, pass+rush yards, pass+rush+reception yards/TDs, kicking points, field goals, PATs,
  solo tackles, assists, defensive interceptions, first/last TD, `player_tds_over`,
  `player_tds`, `player_pass_yds_q1`, and (live) sacks.

## 2. Props, as a class, are softer than game lines on all three proxies

This is the one result every proxy agrees on, in both panels where both exist.

| | prop keys | game lines (h2h / spreads / totals) |
|---|---|---|
| hold, bench books, pp — L | 6.24 to 6.71 | 4.17 / 4.45 / 4.59 |
| hold, bench books, pp — H | 5.85 to 6.71 | 4.25 / 4.68 / 4.77 |
| disagreement between books, pp — L | 1.12 to 2.81 | 0.77 / 0.68 / 0.63 |
| disagreement between books, pp — H | 1.22 to 2.31 (sacks not comparable) | 0.82 / 0.63 / 0.53 |
| movement, last 24h, pp — L | 1.48 to 2.76 | 1.37 / 1.11 / 0.84 |

Books charge ~2pp more to quote a prop, disagree with each other about 2-4x as much, and
move the number more in the last day. First-half lines sit between (hold 4.5-5.6pp).

## 3. Within props, the three proxies do NOT agree on an order

Panel L, 32 games, 95% game-block bootstrap intervals, rank 1 = softest, rank interval in
brackets. Movement is ranked LOW = soft, as the brief framed it.

| key | hold, bench (pp) | hold, DK alone | disagreement (pp) | movement T-24h→close (pp) |
|---|---|---|---|---|
| receptions | 6.71 [6.70, 6.72] r1 [1-1] | 5.99 r4 | 1.73 [1.52, 1.98] r4 [3-6] | 2.31 [1.77, 3.00] r6 [5-7] |
| reception_yds | 6.25 [6.25, 6.26] r6 [6-7] | 5.66 r6 | 2.57 [2.17, 3.10] r2 [1-2] | 2.51 [2.01, 3.17] r7 [5-8] |
| rush_attempts | 6.54 [6.50, 6.59] r2 [2-3] | 6.04 r2 | 1.55 [1.42, 1.68] r6 [4-6] | 1.56 [1.31, 1.89] r4 [1-4] |
| rush_yds | 6.24 [6.24, 6.25] r8 [7-8] | 5.66 r8 | 2.81 [2.15, 3.60] r1 [1-2] | 2.22 [1.83, 2.68] r5 [5-7] |
| pass_yds | 6.25 [6.24, 6.26] r7 [6-8] | 5.66 r7 | 1.58 [1.43, 1.73] r5 [4-6] | 1.48 [1.22, 1.82] r1 [1-4] |
| pass_attempts | 6.51 [6.47, 6.54] r3 [2-4] | 6.03 r3 | 1.12 [0.93, 1.30] r8 [7-8] | 1.54 [1.25, 1.85] r3 [1-4] |
| pass_tds | 6.50 [6.47, 6.53] r4 [3-4] | 5.91 r5 | 1.85 [1.57, 2.18] r3 [3-5] | 1.50 [1.29, 1.72] r2 [1-4] |
| tackles_assists (2 books; thin) | 6.42 [6.41, 6.43] r5 [5-5] | 6.42 r1 | 1.14 [0.84, 1.46] r7 [7-8] | 2.76 [2.15, 3.43] r8 [5-8], 14 games |

Spearman across the 8 keys (descriptive, n = 8): hold vs disagreement **-0.48**, hold vs
movement +0.05, disagreement vs movement +0.10. **There is no common "softness" among these
keys for a composite to find.**

What each proxy is actually measuring:

- **Hold is a price template, not a judgement about the market.** The intervals are ±0.01pp
  because each book applies one overround to a whole family: DraftKings 5.66 on every yards key
  and ~6.0 on every count key, FanDuel 6.10 on yards, BetMGM ~7.0 on everything. The whole
  range across prop keys is **0.47pp**. The brief's picture — "4% on one market, 9% on
  another" — does not exist in this data: no prop key is below 5.6 or above 7.2 at any bench
  book. The order also depends on the book: tackles+assists is DraftKings' highest-hold key
  and mid-table in the bench mean, because FanDuel and BetMGM do not quote it.
- **Disagreement is highest on the yards keys** (rush 2.81, receiving 2.57), then pass TDs
  (1.85) and receptions (1.73); pass attempts and tackles are tightest (~1.1). Panel H agrees
  on the one comparison it can make: reception yards 2.31 [2.22, 2.40] above rush attempts
  1.85 and receptions 1.80, tackles 1.22.
- **Movement is highest on the same skill-position keys** (2.2-2.5pp) and lowest on the three
  quarterback keys (~1.5pp). Read as the brief asked (low = nobody pushing), the QB keys are
  "soft"; read the other way, they are simply settled early. This proxy cannot tell those apart
  and I do not think it should carry weight.

### The correction that produced the disagreement column (addendum 1)

The registered way of converting a line difference into probability was biased to zero on
yards markets, and it would have reversed the table. Books express a different opinion on a
yards prop by **moving the line and leaving the price near even** — 57-81% of book pairs sit
on different lines on the three yards keys, against 4-28% on the count keys — so a slope
estimated across books reads ~0 there. Measured:

| key | registered cross-book slope | one book's own ladder slope [IQR] | empirical check |
|---|---|---|---|
| receptions | 0.587 | 0.608 [0.49, 0.71] | 0.55 |
| reception_yds | 0.019 | 0.469 [0.38, 0.56] | 0.40 |
| rush_yds | 0.044 | 0.573 [0.44, 0.71] | 0.43 |
| pass_yds | negative | 1.313 [1.20, 1.39] | 1.09 |

On receptions, where both exist and the line cannot be shaded, the two agree (0.59 vs 0.61),
which is the check that the ladder slope is the right quantity. Under the registered slope,
reception-yards disagreement read 0.68pp (rank 7) and rush yards 0.73 (rank 6); corrected,
2.57 (rank 2) and 2.81 (rank 1). Both versions are printed by the script. An independent
plain-Python recomputation of reception yards reproduced hold (three books), same-line,
line-split and combined disagreement to four decimals.

## 4. The second axis — is the stat forecastable at all

Squared correlation between a player's as-of trailing-8-game mean and what he then did, REG
2023-2025, players at or above the 10th percentile of the lines books hang. Player-block
bootstrap. CV = sd of (actual - trailing mean) over the mean actual: the size of the miss
relative to the level.

| key | R² | CV | n |
|---|---|---|---|
| rush_yds | 0.344 [0.290, 0.389] | 0.83 | 4,888 |
| rush_attempts | 0.310 [0.252, 0.362] | 0.49 | 2,750 |
| reception_yds | 0.302 [0.266, 0.333] | 0.88 | 10,808 |
| receptions | 0.271 [0.233, 0.306] | 0.66 | 8,897 |
| tackles_assists | 0.158 [0.124, 0.187] | 0.50 | 7,663 |
| pass_tds | 0.082 [0.054, 0.111] | 0.85 | 1,636 |
| pass_yds | 0.058 [0.033, 0.085] | 0.36 | 1,240 |
| anytime_td (0/1) | 0.043 [0.031, 0.056] | 1.70 | 9,938 |
| sacks | 0.040 [0.025, 0.054] | 1.75 | 6,824 |
| pass_attempts | 0.028 [0.009, 0.056] | 0.31 | 1,084 |

- Two tiers and a floor: skill-position volume and yards (0.27-0.34), tackles (0.16),
  everything else (< 0.09).
- **R² does not put attempts above yards** — rush yards 0.344 against carries 0.310, receiving
  yards 0.302 against receptions 0.271, intervals overlapping. That is because R² here is
  mostly "who the player is", and a player's yards level is as persistent as his volume. The
  brief's point (c-27: efficiency is noise) shows in the **CV** instead: a yards forecast
  misses by 0.83-0.88 of the level, a volume forecast by 0.49-0.66.
- The quarterback keys score low for the opposite reason: every listed starter throws about
  as often, so there is nothing between players to explain (CV 0.31-0.36 — they are the most
  predictable in level and the least in rank). A low R² there does not mean noisy.
- TD and sack outcomes are noise on both measures.

## 5. The shortlist

**By the registered rule** (soft = top half of the composite, forecastable = top half of R²,
required under both composite A and composite B):

| panel | shortlisted | conditional |
|---|---|---|
| L (2026 wk 3-4) | receptions, rush_attempts | reception_yds (B only) |
| H (2023-25, composite B only) | rush_attempts, reception_yds | — |

**That rule returns the two markets we already price, and it should not be read as a result.**
The composite adds ranks of three proxies that are uncorrelated or opposed, so what decides it
is hold — a 0.3-0.5pp template difference between "count" and "yards" price sheets. Under the
DraftKings-only sensitivity (the one book that quotes all 8) five keys tie at a mean rank of
4.00. The data cannot separate the prop keys by softness, and I am reporting that rather than
the tidy list.

**What I would look at next, and why** (a judgement, labelled as one):

1. **Receiving yards and rushing yards.** They are where the books disagree with each other
   most (2.6-2.8pp, about 4x a game total, and reception yards holds that position on 850 games
   of 2023-25), they sit in the top forecastability tier, they are quoted by 7 books with deep
   alternate ladders, and reception yards is already on the Board with no model column. The
   cost is that a yards forecast inherits efficiency noise (CV 0.83-0.88), so a model must add
   a lot to be worth anything.
2. **Not the quarterback keys** (pass yards, attempts, TDs): two claims a game, nothing
   between players to forecast, and the least movement.
3. **Not TDs or sacks**: unforecastable on both measures; anytime TD is one-sided, so it has
   no hold and no de-vig (raw same-claim dispersion 1.39pp [1.34, 1.43], 8 books, ~33 players
   a game — listed, not ranked).
4. **Not tackles+assists on this evidence**: two books in 2026, movement on 14 games.

Book disagreement is already known not to be a business by itself: brief 023 measured prop
arbitrage between these same books at ~1pp and not scalable. Disagreement says where the books
are least sure, which is where a forecast has the most room — nothing more.

## What this cannot establish

- **Soft is not mispriced.** Higher hold can be the book correctly charging for variance, and
  higher disagreement can be noise around a correct consensus. The model loses to the close on
  receptions and rush attempts in every season (brief 023); nothing here changes that.
- Panel L is 32 games in weeks 3-4 of 2026. Early-season, and two weeks.
- No Pinnacle or other sharp book: this is disagreement among retail and offshore books.
- The line-to-probability slope is one number per key, held fixed across bootstrap draws, so
  the disagreement and movement intervals omit its uncertainty (ladder IQRs above). Rush
  attempts, pass attempts and pass TDs have no ladder and use the cross-book slope; receptions
  is the only measured check that this is unbiased on a count market. Tackles has no slope at
  all (22 pairs) and uses same-line figures.
- Panel H's yards slope is borrowed from 2026 ladders; 2023-25 has none on disk.
- Sacks disagreement is not read: no two books quote the same line format on the same player.
- First-half team totals use a cross-book slope of 0.036, which is the same defect addendum 1
  fixes for props; that reference row's combined disagreement is unreliable.
- 23 of 33 documented standard prop keys are unmeasured.

## Decisions taken unattended

- **Spent nothing**, against a brief that asked for a paid survey: the stop condition forbids
  it and nine keys were already on disk. The paid step is costed below for Ethan.
- **Addendum 1** replaced the registered slope after one smoke run. It is disclosed with the
  registered figures printed beside the corrected ones.
- Kept the registered shortlist rule's output in the report and said why I do not trust it,
  rather than replacing the rule after seeing what it returned.

## For Ethan

- **A paid catalogue probe, not run.** `/events/{id}/markets` is 1 credit per event and lists
  which keys each book currently has: ~16 credits for one slate tells us which of the 23
  unmeasured keys are actually quoted, and by how many books. A single pre-kickoff snapshot of
  those 23 keys is then at most 23 x 16 = 368 credits (billing follows markets returned, so
  less). Recommended: the 16-credit listing first; buy the snapshot only for keys that at
  least three books quote two-sided.
- **The panel-L rows are being pruned.** They are `source='live'` with no retention hold;
  week 3's reads start leaving the store around 10-08. If this survey is to be re-run on more
  weeks, track A needs to hold `oddsapi:*` prop quotes (a write to `market_log.db`, not this
  track's). The extract this run used (780,236 live rows) is kept at
  `_relay/archive/reports/c-33-extract/`.
