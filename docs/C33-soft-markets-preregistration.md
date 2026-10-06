# C33 — where is the book's price weakest, by the book's own behaviour. Pre-registration

This file is committed and pushed **before** `research/soft_markets.py` exists and before any
hold, dispersion or movement figure is computed. The script implements what is written here
and does not extend it. Nothing in this file is edited after the first run. A correction goes
at the bottom, dated, with its reason.

Unit c-33 (track C), 2026-10-05. Branch `c-33-soft-markets`, cut from
`origin/c-32-open-line-move` (`56a9967`), the previous unit's tip.

## The question

No forecast is involved. **Per Odds API market key, how confident and how contested is the
sportsbook price**, measured three ways (hold, disagreement between books, line movement), and
— on a separate axis — how forecastable is the stat the market settles on. The deliverable is a
ranked shortlist of where to look next. It claims no edge: soft pricing is necessary for one
and never sufficient, and a market can be soft because it is unpredictable.

## Credit budget: 0

**This unit spends no Odds API credits.** The brief asks for a low-cadence paid survey; the
run's stop conditions say "do not spend money or API credits", and the stop condition wins.
Everything below is computed from rows already in `market_log.db` (opened `mode=ro`). The
markets the API documents and we do not hold are catalogued from the public docs page (a web
page, not an API call) and are reported as **not measured**. A paid probe is costed in the
findings as a recommendation for Ethan, not run.

## What was done before this file

I counted what exists: keys, books, rows, seasons, read counts, which columns are populated.
I computed no overround, no dispersion and no movement.

- **Panel L (live, 2026).** `quotes` rows with `venue LIKE 'oddsapi:%'`, `source='live'`.
  One book examined for shape (draftkings): 145,718 rows, 09-24 00:15 to 10-06 00:15 UTC, 47
  events, 21-114 reads per event, `source_ts` (the book's `last_update`) on every row,
  `prob_devig` on none, `mid` = raw implied probability. Keys present: 13 standard-or-alternate
  props (`player_receptions`, `player_reception_yds`, `player_rush_attempts`,
  `player_rush_yds`, `player_pass_yds`, `player_pass_attempts`, `player_pass_tds`,
  `player_tackles_assists`, `player_anytime_td`, and four `_alternate` ladders), three
  first-half keys (`spreads_h1`, `totals_h1`, `team_totals_h1`) and `h2h`/`spreads`/`totals`.
  **These rows are `source='live'`, prunable at 14 days on `ingest_ts`, and no
  `quote_retention_hold` row covers any `oddsapi` venue (0 rows).** The window is whatever
  the logger has not yet pruned; the script records its run time and the realised window.
- **Panel H (historical closes, 2023-2025).** `source='oddsapi_historical'`, 907,421 rows.
  Props are ONE snapshot per event (the close): `player_receptions` 852 events,
  `player_reception_yds` 853, `player_rush_attempts` 846, `player_tackles_assists` 813,
  `player_sacks` 561 (2024-25 only; quarter lines 0.25/0.75). `h2h`/`spreads`/`totals` carry
  a median of 10-11 snapshots per event over 855 events.
- **The docs list 33 standard NFL/NCAAF/CFL player-prop keys and 26 alternates**
  (the-odds-api.com betting-markets page, read 2026-10-05). We hold 9 of the 33 live in 2026
  and one more (`player_sacks`) historically. 23 standard keys are held nowhere.

## Panels are never merged

Panel L is ~2 weeks of one season on 8 two-sided prop keys; panel H is three seasons of
closes on 5. They are ranked separately. The keys in both (receptions, reception yards, rush
attempts, tackles+assists) are reported side by side as the bridge, and that is all.

## Definitions

**Kickoff.** Panel L: the `close_ts` of the `oddsapi` `game:<event_id>` market row (the
API's `commence_time`). Panel H: `nfl_games.kickoff_ts` by `event_id`. Only rows with
`ts < kickoff` are used. Only games that have kicked off by run time are used.

**Pair.** One (book, event, key, subject, line, read `ts`) with exactly one Over and one
Under (for `spreads`/`h2h`: the two teams). `o`, `u` = the two raw implied probabilities
(`mid`).

    hold = o + u - 1
    p    = o / (o + u)            multiplicative de-vig; P(over)

A pair is valid when `0 < hold <= 0.25` (pointsbetus quotes both sides at ~0.999; that and
any other broken pair is dropped and counted). Where a book hangs several two-sided lines on
one subject at one read, its **main line** is the one whose `p` is nearest 0.5.

**Close.** Panel L: per (book, event, key, subject), the last read before kickoff, required
to be within 120 minutes of it. Panel H: the stored snapshot.

**Freshness (panel L only).** A close pair is fresh when `ts - source_ts <= 900` s. Primary
figures use fresh pairs; the unfiltered figure and the share dropped are printed beside them.
Panel H has no `source_ts` and no filter — stated, not worked around.

**Belief index.** Books hang different lines on the same player, so a price difference and a
line difference have to be put in one unit:

    b = p + kappa_key * ln(line)          kappa_key = -dP(over)/d ln(line) > 0

`kappa_key` is estimated per key and per panel as minus the median, over same-instant
cross-book main-line pairs on DIFFERENT lines, of `(p_i - p_j) / (ln L_i - ln L_j)`. It needs
>= 30 such pairs and must come out positive; otherwise the key has no combined measure and
its same-line figure is used and flagged. `kappa` is a crude single slope per key. Every
combined figure is therefore printed with its two components (same-line price difference;
share on a different line) so the components can be read without it. Not applied to
`h2h` (no line) or to `spreads` (signed lines; same-line figures only).

### Proxy 1 — hold

Per key: for each game, the mean `hold` over its close main-line pairs, one book at a time;
then the mean over games. **Primary: `BOARD_BENCH_BOOKS` (draftkings, fanduel, betmgm), each
book's across-game mean averaged with equal weight** — book mix differs by key, and a pooled
mean would rank keys by which books list them. Secondary: all books pooled; each bench book
alone. Sensitivity: pairs with `p` in [0.40, 0.60] only, because overround is not constant
across the price range and TD-shaped lines sit off-centre.

### Proxy 2 — disagreement between books

Per claim (event, key, subject) at the close, over every valid (fresh, panel L) book
main-line pair:

    same-line     mean |p_i - p_j| over book pairs on the same line
    line split    share of book pairs on different lines
    combined      mean |b_i - b_j| over all book pairs        <- the ranked figure

Mean absolute pairwise difference, not max - min: `outcome_close.dispersion` is a range and
grows with the number of books. A claim needs >= 2 books. Game value = mean over its claims;
key value = mean over games. All books primary (a three-book dispersion is too thin);
bench-only secondary. The median number of books per claim is printed per key, because a key
two books quote cannot show the disagreement of a key eight quote.

### Proxy 3 — line movement (panel L; panel H for spreads/totals/h2h only)

Per (book, event, key, subject): **open** = the last read at least 24 h before kickoff (and
no more than 72 h); **close** as above. The window is fixed at T-24h so that keys the books
post earlier do not show more movement merely for having had longer.

    move          |b_close - b_open|                          <- the ranked figure
    line changed  share whose main line differs
    same-line     |p_close - p_open| where the line did not change

Game value = mean over its (book, subject) rows; key value = mean over games. Secondary:
earliest read on disk -> close, with the median realised lead printed per key. Historical
props have one snapshot and **no movement can be computed for 2023-2025 props**.

### What is ranked, and what is only listed

Ranked (panel L): the 8 two-sided standard prop keys. Reference rows, never in the prop
ranking: `spreads_h1`, `totals_h1`, `team_totals_h1`, `h2h`, `spreads`, `totals`.
**`player_anytime_td` is one-sided** — no Under, not a partition — so hold is undefined and
no de-vig applies; its raw-price same-line dispersion and movement are printed as labelled
descriptive figures and it is not ranked. `_alternate` ladders are one-sided too: catalogue
only. `player_sacks` (panel H) is ranked in panel H with its quarter lines flagged.

A key needs >= 10 games to be ranked. An interval on < 5 games is not read.

### Direction, and the composite

    hold           higher = softer
    disagreement   higher = softer
    movement       LOWER  = softer     (the brief's reading: nobody is pushing on it)

The movement direction is the contestable one — a line that does not move may equally be one
the opener got right, and a line that moves a lot is one where information is still arriving
late. So two composites are reported and neither is preferred after the fact:

    composite A = mean rank over hold, disagreement, movement(low = soft)
    composite B = mean rank over hold, disagreement

The Spearman correlations among the three proxies across keys are printed (n = 8:
descriptive, no interval is read into it).

### Intervals

2,000 game-block bootstrap draws, seed 33. Games are resampled once per draw and every key is
recomputed from the same draw, so each key's value interval and its **rank interval** come
from the same resamples. 95% percentile intervals. Per CLAUDE.md, two keys' intervals side by
side are not a test of their difference; the rank interval is the statement about order.

## The second axis — is the stat forecastable at all

Separate from softness and never folded into the composite. From `nfl_player_week`, REG
2023-2025, per stat: the as-of predictor is the player's mean over his previous 8 games
played (strictly earlier season-week, crossing seasons), requiring >= 4 of them; the
population is player-games whose predictor is at or above the 10th percentile of the main
lines the books hung on that key (panel L closes; panel H for sacks), so it resembles the
players who get a line.

    R2        squared Pearson correlation of predictor and actual       <- the axis
    Spearman  rank correlation
    CV        sd(actual - predictor) / mean(actual)

Player-block bootstrap, 1,000 draws, seed 33. Stats: receptions, receiving yards, carries,
rushing yards, pass attempts, passing yards, passing TDs (QBs), tackles+assists (the
three-column sum), sacks, and anytime TD as a 0/1 outcome (rushing + receiving TDs > 0).

R2 against a trailing mean is mostly "who the player is", which the book also knows; it
measures whether the stat is forecastable, **not** whether anyone can out-forecast the book.
It is also range-dependent: a stat every starter does about equally (QB attempts) scores low
without being noisy. Both stated in the findings.

## The shortlist rule

Within panel L's 8 ranked keys: "soft" = top half on the composite; "forecastable" = top half
on R2. The 2x2 is printed under composite A and under composite B. A key is shortlisted only
if it is soft AND forecastable under **both** composites; a key that is so under one is
listed as conditional. Panel H gets the same table over its 5 keys (composite B only: it has
no movement).

## What this cannot establish, written before the run

- Soft is not mispriced. High hold can be the book correctly charging for variance.
- Panel L is about two weeks of one season. Ranks, not constants.
- 23 of the 33 documented standard prop keys are held nowhere and are not measured.
- The books here include no Pinnacle; "disagreement" is among retail and offshore books.
- No hypothesis test is run. This is a descriptive ranking with intervals; nothing is
  "significant".

## Outputs

`research/soft_markets.py` (reads `LOGGER_DB` `mode=ro`, writes only `--json-out` and an
extract cache under a scratch path), `tests/test_soft_markets.py`,
`docs/findings/soft-markets.md`. Nothing is published and no site file is touched.
