# C29 — closing line value on the published leans: findings

Pre-registered at `docs/C29-clv-every-week-preregistration.md` (commit `6b2c802`,
pushed before `research/clv_record.py` existed). Script `research/clv_record.py`;
result `research/results/clv_record.json` (envelope `record.clv`, tier
`published`). Tests `tests/test_clv_record.py`. Run 2026-09-30 against
`market_log.db` (`mode=ro`) and the Board tree at
`D:/calibrated-sports/data/board_export`.

**Scope, in the sentence: 2026 NFL week 3 only, the Board's published leans on
receptions and rush attempts, priced at DraftKings / FanDuel / BetMGM, close =
each book's last Odds API capture strictly before kickoff (a median ~20 minutes
before it).** Week 4's leans are pending; weeks 1-2 have no published leans.

## Headline

    touch (vig-inclusive, same book, same line, same side)
        -0.08pp [-0.34, +0.24]   se 0.15   MDE 0.41pp   n 119 leans   11 games

**No measurable CLV; an effect smaller than 0.41pp could not have been seen.**
This is the expected result on a market this project has already shown to be
efficient, and it is a null with its power, not a failure.

    mid   (de-vigged, value no bet could take)   -0.07pp [-0.31, +0.23]   MDE 0.39
    held  (de-vigged close - price paid)          -3.31pp [-3.60, -2.98]
    touch, stale entries admitted                 -0.07pp [-0.32, +0.20]   n 175, 15 games

`held` is what a bet placed at entry and held is worth if the close is fair. It
is negative by roughly half the books' hold, which is what an unskilled bettor
pays. The touch headline does not make that number go away: CLV near zero means
the leans got the market's price, and the market's price carries the hold.

## Every lean accounted for

    published                                                        211
    scored                                                           119
    pending: game not yet kicked off                                  11   (week 4)
    void (refunded)                                                    6
    entry refused: every entry book stale (> 6 h)                     61
    no close at the lean's line (line moved or player pulled)          9
    no book on both ends                                               5
    not pre-kickoff / no read row / no event / pruned                  0

The partition is asserted by the script. **61 of the 200 week-3 leans have no
admissible entry price**: 48 were published at the 2026-09-26T19:19Z read and 13
at 2026-09-27T06:02Z on games whose latest Odds API capture was 5.7 to 47 hours
old at publication. They were published off prices the book had not been seen
quoting for up to two days. Admitting them moves the headline by 0.01pp.

The entry prices reproduce: for every admitted (lean, book), the price in the
Board's read file equals the store's quote at the capture it came from (0
mismatches), and the read-file row's lean equals the ledger's side (0
mismatches).

## The mechanical term

The scored leans are 94 unders and 25 overs (`mean_sgn` -0.580). Between entry and close the
de-vigged over probability drifted **+0.33pp** on average. Decomposed:

    mean(sgn x drift) -0.071 = cov(sgn, drift) +0.123 + mean(sgn) x mean(drift) -0.194

So the small negative mid CLV is entirely the directional lean times a
market-wide drift toward the over; the covariance - the part that would be
selection skill - is +0.12pp, well inside the MDE. Neither is a finding.

## Strata (40 intervals; none is a finding on its own)

- **side**: under -0.27pp [-0.49, -0.06] on 94 leans / 11 games; over +0.65pp
  [-0.06, +1.47] on 25 / 10. The under interval excludes zero, and it is the
  same drift as the mechanical term above seen from one side, not a second
  result.
- **books on both ends = 2**: -0.38pp [-0.70, -0.09], 43 leans / 11 games. The
  1- and 3-book cells both contain zero and sit either side of it. Read as noise.
- Counting: 20 cells x 2 arms = 40 intervals. Four readable ones exclude zero -
  the two cells above, each on touch and on mid, which are near-identical arms -
  so two independent cells, against ~2 expected by chance at alpha 0.05.
- **close lag <= 5 min**: +0.96pp on 13 leans / **2 games** - not read.
- **lead**: 111 of 119 scored leans sit at 6-24h, 8 at < 6h on one game. The
  24-48h and >= 48h cells are empty **because the stale rule removed them** -
  every long-lead lean was published off an old capture. So "enter earlier" and
  "enter on a stale rung" cannot be separated on week 3; they are the same 61
  leans.
- **entry depth** (benchmark books quoting the side at entry, 1 / 2 / 3): all
  three contain zero.
- market, band: all readable cells contain zero.

## What this cannot support

- **One week.** Every lean scored is week 3. The game-block bootstrap has 11
  games, enough to read, but a second week can move everything.
- **The close is ~20 minutes early.** For most 1pm games the forward schedule's
  last pre-kickoff capture is T-20m; a price move in the final 20 minutes is not
  in any figure here.
- **Not tradeability.** A book price is a quote, not a fill; a book can limit
  the account, and a stale capture could have been off the board at the read.
- **Not the Kalshi predictions.** `research/clv.py --weeks` shows `predictions`
  holds week 1 only, and under a-58's entry rule (live quote, <= 900 s) **0 of
  935** have a live entry quote and **0** have a live close left in the store:
  retention pruned them. S00's week-1 CLV cannot be re-derived from the store.
- **These figures will stop reproducing.** The week-3 Odds API rows are `live`
  and are pruned 14 days after ingestion (from ~2026-10-08). After that a
  re-run reports those leans under "quotes no longer in the store" rather than
  as stale or pulled - by design - and the committed result file is the record.
