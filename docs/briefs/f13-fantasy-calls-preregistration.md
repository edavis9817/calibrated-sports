# F13 — how the fantasy board's calls are graded: pre-registration

Committed on 2026-09-24, **before any call exists.** At registration no buy-low or
sell-high board has been rendered (b-26 is pending), the residual it will read is still
being built (a-18 is running), and no call ledger exists. Nothing in this document was
computed from data, and no outcome has been joined to any band. The commit that adds
this file is the registration timestamp.

The rule's constants live in `research/f13_call_grading.py`. A grading run imports them
rather than re-typing them, and `tests/test_f13_call_grading.py` asserts the module and
this document agree. It is registered as **R18** in `docs/hypotheses.json`, verdict
`open`.

**Why this exists.** A site that publishes a call has the option to stop mentioning it
later. The defence is a scoring rule that already exists when the first call is made.
The clause the rest of this document serves is section 9: **a failed band is reported on
the page and is not removed.**

## 1. What counts as a call

- **A call** is one player's membership in the `sell_high` or the `buy_low` band, as the
  board published it for one (season, REG week).
  - The call is made after that week's games are final.
  - It is made before the first kickoff of the player's next game.
- **Only the graded scoring system is scored.** That is the `ppr` preset, pinned by
  value in `PRESET_WEIGHTS`.
  - A reader's edited weights are never scored. We cannot grade a board we did not
    publish, and a reader who re-weights has built their own board.
  - The page must say the record is for PPR.
  - The weights are pinned by value, not by name. If the export's `ppr` preset ever
    changes, that is a new call series (section 9); the old series keeps the old weights.
- **Membership is what the board showed**, including the band edges it showed.
  - The edges are not recomputed at grading time.
  - They are read from the call ledger (section 10), which records them at call time.
- **The middle is not a call.** Players between the edges are the board's
  "no claim". They are counted and shown, and they are not graded.
- **Every call counts.** That includes every week a player stays in a band, and every
  band on every week the board was published. There is no selection of "featured" calls.

## 2. A call is gradable only if it could have been made at the time

The band must be computed from an **as-of** residual:

- The line `production ~ opportunity` is fitted on REG weeks 1..t of that season only.
- It is applied to that player's weeks 1..t.

A residual taken from a **full-season** fit knows weeks t+1 onward, so calls built from
it are not forward calls.

**If the board's residual is full-season, its calls are `not_testable`, and the page says
so.** They are not graded as though they were forward calls, and the board is not
published as a call board. This is a condition on a-18/b-26, not a choice left to the
grader. It is filed to track A and track B in the f-13 report. f-14 checks which kind of
fit a-18 uses.

## 3. What counts as the outcome

**The outcome is the player's residual in the horizon, in PPR points per game played.**
It is priced against the line frozen at call time: the week-t as-of fit, applied to his
later opportunity.

The claim under test is *"the residual does not persist"*, and what that claim predicts
is the later residual. So the residual is the outcome, and these are not:

- **Not points.** Points are opportunity times efficiency, and opportunity *does* persist.
  A sell-high player with a large role keeps scoring. Grading on points would let a
  persistent role pass or fail a claim about efficiency.
- **Not the change in points.** Regression toward the mean after selecting on a noisy
  residual happens even when efficiency *does* persist. With r = 0.5, half of the
  residual still reverts. A "points went down after sell-high" test would pass under both
  hypotheses, so it tests neither.
- **Not opportunity.** The board makes no claim about opportunity.

**Why the line is frozen at call time.** Refitting at grading time would let later weeks
move the line the call was measured against. With the line frozen, the only thing that
arrives after the call is the player's own later production and opportunity.

**Null is not zero.** A later game where an opportunity column is null drops out of the
numerator and the denominator together. A game the player did not play is not a game
with residual 0.

## 4. The horizon

Both horizons are reported, **separately**, and both were chosen here:

| horizon | outcome |
|---|---|
| `next` | the player's next REG game in the same season |
| `ros` | every remaining REG game he plays in the same season, averaged per game |

- **A call that cannot be graded is counted, never dropped.** This covers a week-18 call,
  an injury, or a release: the player records no game in the horizon.
  - The count of ungradable calls is published per band and horizon.
  - Suppose the ungradable share differs between the two bands by more than 10 points
    (`UNGRADABLE_GAP_FLAG`). Then the page states it beside the verdict: availability
    correlates with performance, so this is a survivorship flag, not a footnote.
- **The postseason is excluded.** No horizon crosses into another season.

## 5. The estimand and the verdicts

- **The carried fraction.** Take the calls in a band. Divide the mean per-game residual in
  the horizon by the mean per-game residual at the call.
  - The per-game residual at the call is the season-to-date sum divided by games played
    to date.
  - For the **contrast**, the numerator is sell-high minus buy-low in the horizon, and the
    denominator is sell-high minus buy-low at the call.
  - Under no persistence the carried fraction is 0. If efficiency is fully persistent, it
    is 1.
- **The excess carried fraction** is the real carried fraction minus the median carried
  fraction in the null world (section 6). Some mechanics can make a board show carry with
  no persistence at all: selection, the as-of refit, integer outcomes, ties, and
  league-wide drift in efficiency across a season. The null world measures them and they
  are subtracted, so they are not argued away.
- **The threshold is `TAU = 0.25`.** The claim is that no more than a quarter of the
  residual carries.
  - The record the board rests on says r ≈ 0.09, which a-18 is re-deriving. τ is about
    2.8× that.
  - If the record is right, the board passes comfortably. If the record is off by enough
    to matter, the board fails.
  - A board whose players keep a quarter or more of their efficiency is not a board whose
    calls are "unlikely to repeat".
  - τ is a judgement, and it is fixed here, before any data.
- **Three verdicts, each reachable.** `tests/test_f13_call_grading.py` drives
  `verdict()` to each one.

| verdict | rule |
|---|---|
| `supported` | the **simultaneous** upper bound on the excess carried fraction is below τ |
| `failed` | the **unadjusted** 95% lower bound is above τ |
| `inconclusive` | neither; or fewer than 30 distinct players in either band that season |

- **Uncertainty comes from a player-clustered bootstrap** with 2,000 draws.
  - The whole carried fraction is resampled as one quantity.
  - It is never assembled from two separately published intervals, following the
    brief-018 rule on the selection gap.
  - Clustering by player handles the overlap between a player's consecutive calls and his
    overlapping `ros` windows. It does not handle correlation across players within a
    game, and that limitation is stated with every figure.

## 6. The comparison: which null

**The brief proposed the F11 null:** redraw each player-season's values with
replacement from his own values. That null does not describe a world with no persistent
efficiency. Resampling a player's own season keeps his season-level efficiency, which is
exactly the thing the board claims does not persist. It removes order within the season,
and nothing else. F11 used it correctly, because a hot hand is an ordering claim. Here it
would compare the board against a world in which efficiency persists for the whole
season, and the board would look worse than it is for the wrong reason.

So there are two nulls, and each has one job.

- **Primary: the pooled redraw.** This is the world with no persistent efficiency.
  - Each player-week keeps its real opportunity.
  - Its residual is redrawn with replacement from the pooled residuals of player-weeks in
    the same (season, position group, stat).
  - The pool is also restricted to the same opportunity quintile, so the spread of
    residuals at each level of volume is preserved.
  - Production is rebuilt as `line(opportunity) + redrawn residual`, and the whole board
    is rebuilt with the identical rules: as-of refit, edge rule, eligibility and PPR
    weights.
  - Its carried fraction centres the excess (section 5).
  - It uses `NULL_REPS = 1000` replicates. The empirical p floors at about 0.001, which is
    adequate for six tests. The sweep's floor problem was hundreds.
- **Secondary, descriptive only: the own-season redraw (F11's iid).** It answers a
  different question. Does real carry exceed a world where each player's season-long
  efficiency is fixed and only its order is shuffled? It is reported beside the primary
  and carries no verdict.

## 7. What is graded, and when

- **Primary family, two tests:** the sell-high minus buy-low contrast, at `next` and at
  `ros`. The contrast is primary because league-wide drift in efficiency affects both
  bands alike and cancels in it.
- **Secondary family, four tests:** each band alone, at each horizon. Each band gets its
  own verdict, because a board can work on one side and not the other, and the page must
  be able to say so.
- **Per season, never pooled for a verdict.** A pooled figure may be shown, labelled
  descriptive.
- **The verdict is computed once per season,** after the weekly refresh that settles REG
  week 18.
  - During the season the page may show the running figures and intervals, marked as
    interim, **with no verdict word**.
  - A verdict available every week is optional stopping. The board would get eighteen
    chances to be called `supported`, and would be called it the first week it was.
- **Live against retrospective.**
  - The **live series** starts with the first week b-26 publishes calls. It is the
    holdout, and only it carries a verdict on the page.
  - A retrospective board over past seasons may be computed under exactly these rules. It
    is labelled **retrospective, not a holdout**, because r ≈ 0.09 was measured on those
    same seasons.
- **There are no retroactive calls.** The live series has no week earlier than the first
  week the board was actually published.

## 8. Multiple comparisons, and how the board could be flattered

There are six tests per season (2 primary plus 4 secondary), plus positions, stats and
seasons that could each be sliced. How each is handled is fixed here, before anyone can
exploit it.

- **The correction is asymmetric, and both halves go against the board.**
  - A `supported` verdict must clear a **Bonferroni-widened** interval. That is 97.5% for
    the 2 primary tests and 98.75% for the 4 secondary tests (`simultaneous_level`).
  - A `failed` verdict needs only the **plain 95%** interval.
  - The claim under test is a null, so the usual correction would make the board *harder
    to fail*. A correction must never be allowed to help the board.
  - The cost: under a perfectly true claim, some test fails by chance more often than 5%
    per season. We accept that. A chance failure is published with its interval, and the
    reader can see it for what it is. A convenient correction would be invisible.
- **Stats and positions are descriptive, and carry no verdict.** These are the per-stat
  decomposition (receiving yards, receptions, rushing yards, TDs) and each position
  group.
  - No page says "the board works for receiving yards" or "for running backs".
  - Slicing six tests by four stats and three positions gives 72 cells. A cell that looks
    good in a family that size is the expected output of chance.
- **Individual players are never the evidence.** A board is wide. Take a band of 20 calls
  with any per-call "hit" rate near a half: some players will look vindicated by
  construction. P(≥6 of 7 | p = 0.5) = 8/128 ≈ 1 in 16. So:
  - the page shows every call or none;
  - no "we called it" callout for a single player, in either direction.

## 9. What gets published when it fails

**A failed band is reported on the page and is not removed.** This includes a failed
contrast and a failed season, and it holds even when a later season is `supported`.

- **The record is permanent and dated.**
  - Every season's verdict stays visible, next to its figures, its interval, n (calls and
    distinct players), the ungradable counts and the null's centre.
  - A `failed` season is not reworded, collapsed behind a toggle, or moved below the fold
    relative to a `supported` one.
- **The board is not quietly retired after a failure.** Retiring it is allowed, as a dated
  entry on the same record that says why. The record of its calls stays.
- **The rule changes only by a dated amendment,** appended to this file.
  - An amendment applies to calls made **after** its date.
  - Every earlier call is graded under the rule it was made under, and that series stays
    on the page.
  - This covers band edges, eligibility, the preset, the fit and τ. Changing any of them
    starts a new series. It never regrades an old one.
- **"No calls graded yet" is itself shown, dated from the first published board.** An
  empty scoreboard dated from day one is honest. A scoreboard added later, after good
  results, is not.
- **Wording follows the verdict mechanically** (CLAUDE.md, *Claims*):
  - `supported` → "carried less than a quarter";
  - `failed` → "carried more than a quarter";
  - `inconclusive` → the figures and nothing else.

## 10. The call ledger (a requirement, not built here)

Grading needs an **append-only, timestamped** record of each call, written when the call
is made. Invariant 6 applies: this is a belief store. Each row carries:

- the season, the week, `player_id` (gsis) and the position group;
- the band;
- the residual per game at the call, with its interval and games played;
- the band edges and the edge rule;
- the preset and its weights by value;
- the as-of fit's identity: the weeks it used and a version of the code;
- the timestamp at which the row was written.

**The rules for the ledger:**

- **The ledger is never regenerated from the current board.** A regenerated ledger is the
  board as it would have been, not the board as it was.
- **Rows are never edited.** A later correction to a *fact* changes only the outcome
  side, at grading time.

`research/f13_call_grading.py` refuses to grade (exit 2) until a ledger exists. **Whoever
builds b-26's publishing path owns the ledger.** Until it exists, b-26 renders the board
as descriptive only. Its brief already says so.

## 11. What this cannot see

- **This is not a test of whether the calls make money in a league.** Trade prices are not
  observed. The page claims that the residual does not repeat, and nothing about what
  other managers will pay.
- **Opportunity is taken as given.** A sell-high player whose role shrinks is graded only
  on his efficiency. His lost points are real, and they are outside the claim.
- **One season is thin.** The 2026 live series starts mid-season, whenever b-26 first
  publishes, so `inconclusive` is a likely first verdict. It will be published as such.
- **Clustering by player does not absorb game-level correlation** between teammates in the
  same band. The intervals are somewhat narrower than the truth for that reason.
