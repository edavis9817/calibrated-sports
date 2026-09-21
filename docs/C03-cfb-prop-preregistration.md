# C03 — CFB player props: the pre-registered question, and why the gate is not the price

Written 2026-09-21, track C. **Nothing is bought.** This exists so that when someone
proposes the spend, the question it would answer is already written down and the
condition that must change first is named.

Reproduce every figure: `python -m research.cfb_p1_markets` (P1, already bought) and
the query in §2 against `cfb.db`.

---

## 1. The spend does not fit, and that is the SECOND reason

**Confirmed, and the brief's estimate was conservative in one direction and generous
in another.** Billing is `10 x markets returned x regions` per EVENT (historical event
odds), so a slate costs `10 x 5 x events` for the 5-market usage version.

| | events | 5 markets | all 26 prop keys |
|---|---:|---:|---:|
| one measured slate (2026 week 4) | 74 | **3,700** | 19,240 |
| rest of the 2026 regular season (11 weeks, measured) | **628** | **31,400** | 163,280 |

The brief's ~44,000 assumed a 74-event slate every week. The measured remainder is 628
FBS-involving games across weeks 4–15, an average of 57 a slate, so the real figure is
**31,400**. Against **22,166** spendable above the 20,000 reserve (pool 42,166 at the
last ledger row, 2026-09-21), the cheap option still **does not fit** — it is 1.4x the
budget, not 1.8x. The conclusion is unchanged and the arithmetic is now measured rather
than extrapolated.

Two notes that keep this honest if it is ever re-costed:
- The pool is **shared with the live NFL logger**, which has first claim, and Track A's
  NFL credit reconciliation is still open. 22,166 is what is spendable today by
  everyone, not a CFB allocation.
- `10 x markets RETURNED` means every figure here is an UPPER bound: a slate where a
  book lists three of the five keys bills three.

## 2. THE GATE IS THE APPEARANCE GAP, NOT THE PRICE

**Even at zero cost, a CFB prop record cannot yield an honest hit rate today.**

No public source records whether a college player dressed. Measured and recorded as
`cfb.stats_and_usage_only` in the store, from `research/cfb_sources_audit.py`:

- `did_not_play` is **False on every row, 2004–2026** — an always-False column reads as
  "everyone played".
- Starter flags exist only from 2025, and only **802 of 1,890** team-games in 2025 carry
  a full lineup (**106 of 156** so far in 2026, **0** in 2004–2024).
- CFBD publishes no snap or participation field; ESPN's box score lists only players who
  recorded a stat; PFF sells snaps.

So a player with no stat row is **"did not dress" OR "played and recorded nothing"**, and
nothing on disk separates them. A hit rate over props therefore inherits a bias whose
DIRECTION is a choice made by the analyst and not by the data: counting a missing row as
zero skews to the under, dropping it skews to the over. That is the same defect the NFL
side hit with `stats_player_week` missing rows — except the NFL has `nfl_snap_counts`
(2013+) to resolve it, and CFB has nothing.

**Therefore: buying prop prices buys the PRICE side of a record whose OUTCOME side
cannot be settled.** The spend is not premature because it is expensive. It is premature
because the thing it would build cannot be graded.

## 3. The pre-registered question

> **Q.** Over one CFB season of book-listed player props (receptions, rush attempts,
> pass attempts, reception yards, rush yards), is the closing consensus calibrated —
> and does a usage model fit on `cfb_player_game_usage` beat it on Brier score?
>
> **Decision rule, declared before any purchase:**
> - Primary: `Brier(model) - Brier(close)`, block-bootstrapped over GAMES (not props —
>   a player's rungs are one claim), 95% interval. The model wins only if the interval
>   excludes zero in its favour.
> - The NFL answer to this same question is +0.0195 to +0.0237 against the close in
>   every season, walk-forward (brief 023). **A CFB result that merely reproduces the
>   NFL's loss is a null, not a finding**, and is the expected outcome.
> - Secondary, and reported whether or not the primary is estimable: market calibration
>   (ECE, Wilson intervals on distinct games), which needs no model and is publishable
>   as a fact.

**The gate that must open first, stated as a test rather than a wish:**

> **G.** A source exists that says, for a college player and a game, whether he
> DRESSED — independent of whether he recorded a stat. It must cover the seasons
> being graded, not only the current one, and it must be checkable: `did_not_play`
> that varies, a snap count, a participation feed, or an official inactives list with a
> timestamp.

Until G is satisfied, §3's primary question is not answerable at any price, and the
honest scope of any CFB prop purchase is the SECONDARY question only — market
calibration, which grades against the final stat line and needs no appearance signal
**for the players who recorded one**, while still being silent about everyone who did
not.

## 4. What would change the recommendation

- **G opens** (a participation or inactives feed appears, free or paid) — then the
  primary question becomes answerable and the 31,400 is worth costing against a
  specific season rather than "the rest of this one".
- **The pool grows or NFL's claim shrinks** — this changes the price problem and NOT
  the gate, so on its own it is not sufficient.
- **A cheaper shape** — the forward bulk capture already running (3 credits per kickoff
  hour, game lines only) proves that a live-captured, timestamped record costs two
  orders of magnitude less than a historical prop backfill. If CFB props are ever worth
  a record, capture them FORWARD at the close rather than buying the past.

## 5. Recommendation

**Do not spend.** Not this season, and not when the pool recovers — until G opens, the
purchase buys half a record. The pre-registration above is what makes the next proposal
cheap to evaluate: if someone arrives with a participation source, the question, the
decision rule and the number are already written.

This is a recommendation, not a decision: the spend is Ethan's call.
