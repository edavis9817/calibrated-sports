# c-32 findings — does the model's disagreement with the open predict where the line closes

Script: `research/open_line_move.py`. Pre-registration: `docs/C32-open-line-move-preregistration.md`
(pushed at `c9b15cd`, before the script existed; addendum 1 at `bbcdb43`, written after a 50-draw
smoke run and before the real run). Run 2026-09-30 20:22Z, nflverse `nfl_games` version 2026-09-30,
2,000 game-block draws. Tests: `tests/test_open_line_move.py`.

**Scope, in the sentence, so it travels with the claim:** Kalshi `KXNFLGAME` / `KXNFLSPREAD` /
`KXNFLTOTAL`, NFL 2026 regular season **weeks 2 and 3 only, 32 games**. The open is the earliest
quote on disk, **~4.5-5 days before kickoff** (median lead 117.8h; min 43.7h). The model is
c-28's Elo margin and c-31's two-team total with **no wind** (NO_WIND arm). The close is the last
quote within 30 min of kickoff.

## Headline

| market | slope of (close − open) on (model − open) | r | MDE (slope) | verdict |
|---|---|---|---|---|
| moneyline (prob.) | **+0.104 [+0.039, +0.176]** | +0.449 [+0.186, +0.664] | 0.096 | line moved toward the model |
| spread (home margin, pts) | **+0.106 [+0.027, +0.178]** | +0.411 [+0.096, +0.652] | 0.107 | line moved toward the model |
| total (pts) | −0.026 [−0.174, +0.112] | −0.070 [−0.445, +0.299] | 0.203 | no detectable relationship |

**Read the size, not only the sign.**
- The line travels about **0.1 point per point of disagreement**.
- The model disagrees with the open by an sd of **3.4 points** on the spread. The line
  itself moves an sd of **0.87 points** (mean |move| 0.65).
- So the model sees about a tenth of what the market later does. It does not anticipate the
  close.

**The moneyline and the spread are one result, not two.** `p_home` and `mu_m` are monotone in
each other, and the two Kalshi series agree at the open (corr 0.999 after converting the
moneyline to a margin). Two confirmations of one relationship are one confirmation.

**Both intervals exclude zero at an estimate sitting on its own MDE** (0.104 vs 0.096;
0.106 vs 0.107). This is the smallest effect this sample can see, observed once.

## A constant that knows nothing about the teams gets half of it (post hoc, addendum 1)

Placebo: the model replaced by a constant.
- moneyline: the league home-win rate, 0.5630;
- spread: `sigma_m × PhiInv(0.5630)`, a home edge of about 2.1 points;
- total: c-28's league-window mean.

| market | placebo slope | placebo r |
|---|---|---|
| moneyline | +0.047 [+0.005, +0.099] | +0.351 [+0.040, +0.634] |
| spread | +0.051 [+0.000, +0.103] | +0.355 [+0.003, +0.657] |
| total | −0.078 [−0.170, +0.058] | −0.249 [−0.544, +0.164] |

- **A four-day-out Kalshi line moves toward a plain home-edge prior at about half the rate it
  moves toward the model.** Part of "toward the model" is therefore "toward the middle".
- **Whether the model adds anything beyond that was NOT tested.** The two slopes are on
  different x variables, so they cannot be differenced, and a joint regression was not
  registered. I did not add one after seeing the result.

## The shared-open artifact is not the explanation (post hoc, addendum 1)

`model − open` and `close − open` share the open with opposite signs. So noise in the open
alone produces a positive slope, of size `var(e)/var(x)`. `tests/test_open_line_move.py` shows
that trap firing on synthetic data.

It was measured with a second open snapshot, taken 2-3h after the first:

| market | sd(open B − open A) | slope that noise alone would produce | observed | split-snapshot slope |
|---|---|---|---|---|
| moneyline | 0.0050 | 0.001 | 0.104 | +0.106 [+0.043, +0.179] |
| spread | 0.12 pts | 0.001 | 0.106 | +0.114 [+0.037, +0.187] |
| total | 0.21 pts | 0.002 | −0.026 | −0.032 [−0.176, +0.103] |

- **Transient noise in the open is two orders of magnitude too small** to make the slope.
- **Noise that persists for days is not excluded by this check**, for example a stale ladder
  nobody re-quotes. A 2h gap between snapshots cannot see it.

## Selection control: bands of |model − open|

The ratio is (mean signed move toward the model) / (mean |move|):

| market | band 1 (small) | band 2 | band 3 (large) |
|---|---|---|---|
| moneyline | +0.80 | −0.22 | +1.00 |
| spread | +0.85 | +0.27 | +0.56 |
| total | +0.27 | −0.19 | −0.49 |

- **Large disagreements do not move more.** Mean |move| is about the same in bands 1 and 3,
  both for the spread (0.81 vs 0.83 pts) and for the moneyline.
- **The relationship is not monotone in the disagreement.** The share of movement going the
  model's way is highest where the model disagrees LEAST.
- A real, graded skill would predict the opposite. With about 11 games per band this is
  descriptive only.

The trap the brief named, that big-disagreement games move more for unrelated reasons, did
not appear: they do not move more. The non-monotone band pattern is a separate thing, and I
have no explanation for it.

## The honest version: did the move beat the cost of taking the side at the open

- **Pivot rung**: per game, the rung nearest 0.5 at the open (moneyline: the home market),
  taken on the model's side.
- **Cost**: the half-spread plus Kalshi's taker fee at 100 contracts.

| market | moved toward the model by more than cost | mean signed move | mean net of cost |
|---|---|---|---|
| moneyline | 9 of 32 (0.28, Wilson [0.16, 0.45]) | +1.05c [+0.22, +1.86] | **−0.96c [−1.80, −0.15]** |
| spread | 13 of 32 (0.41, Wilson [0.26, 0.58]) | +2.30c [+1.19, +3.38] | **+0.00c [−1.11, +1.07]** |
| total | 6 of 32 (0.19, Wilson [0.09, 0.35]) | −0.73c [−1.94, +0.58] | **−3.14c [−4.31, −1.91]** |

- The line moves toward the model's side by about as much as it costs to take that side: the
  spread nets to zero, and the moneyline nets slightly below it.

**A line moving toward a position is closing-line value, not profit.** Nothing here is
settled; the position still has to settle. c-28, c-30 and c-31 found that the same model loses
to the close at settlement. A positive slope says the model partly anticipates the market's
drift. It does not say that taking the model's side at the open pays, and the net-of-cost
column says that on this sample it does not.

## Totals

- **Primary, NO_WIND:** −0.026 [−0.174, +0.112]. The MDE is 0.203, so this is a null with
  less power than the spread's.
- **Sensitivity, FULL with RECORDED wind (look-ahead):** −0.000 [−0.148, +0.143].
- Recorded wind moves the model's total at the open by a mean +0.02 and an sd of 1.19 points.

c-31 found the two-team total's disagreement with the CLOSE is noise. Its disagreement with a
four-day-out price shows no relationship either.

## What this cannot say

- **Nothing about the true opening number.** Two things set the open on disk:
  - For week 3 it is the logger's discovery horizon (`KALSHI_CLOSE_HORIZON_DAYS` = 7, counted
    from each market's close_ts), which gives a lead of about 119.8h.
  - For week 2 it is the 14-day retention edge, which gives about 108.5h. That edge moves
    forward in real time, so **a re-run tomorrow will read later opens for week 2**, and
    around 10-02 onward week 2 leaves the store altogether.
- **Nothing about weeks 1 or 4.**
  - Week 1's live quotes were pruned. Its only early prices are candles, and it has no close.
  - Week 4 has not been played. Its opens are on disk now, and its closes arrive 10-01 to
    10-05.
  - Its opens will be pruned about 14 days after ingest (from about 10-10), unless held.
- **Nothing about 2023-2025**, where only closes were stored.
- **Nothing about sportsbooks.** Kalshi is the only 2026 venue with a history on disk; Odds
  API 2026 rows are event markers and props only.
- **Nothing about wind forecasts.** The primary total runs without wind.
- **Multiplicity.** There are three co-primary slopes and two of them are one relationship.
  The addendum adds 3 placebo slopes, 3 split slopes, 2 crossing-median slopes and 1 wind
  slope, plus the pivot measures. There is no correction.

## Invariants

- `market_log.db` and `analytics.db` were opened `mode=ro`.
- The script refuses to run unless c-28's moneyline Brier reproduces exactly (it did:
  0.2205815236861795).
- It refuses unless its as-of rebuild reproduces c-31's own factor states at kickoff on all
  32 games (it did).
- No c-28, c-30 or c-31 file was edited.
