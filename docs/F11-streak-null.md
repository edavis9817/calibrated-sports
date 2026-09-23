# F11 — how many "hot streaks" does pure chance produce?

Research only. Nothing exported, nothing published, no contract change.
Script: `research/f11_streak_null.py`. Construction tested against brute-force loops:
`tests/test_f11_streak_null.py`. Every figure below is printed by

    python -m research.f11_streak_null \
        --components D:/calibrated-sports/staging/a-11/nfl/components \
        --analytics D:/calibrated-sports/data/analytics.db --out <json> --reps 100

(seed 20260923; run 2026-09-23 on the 3.12 interpreter).

## Input

- **The a-11 components table** (staged, 28 season files, player-game grain). That
  staging was built by `build_components` at 982e705, and main has not changed that
  function since. **REG weeks only.**
  - 1999 is used only as lookback. 2026 (two weeks old) is excluded.
  - 199,475 REG rows, 3,939 players.
- **Home/away and opponent** come from `f_team_game_pace` in `analytics.db` (read-only),
  whose `game_id` names the home team last. 1 row is unmatched.
- **Eight stats:** receptions, targets, receiving yards, rush attempts, rushing yards,
  pass attempts, completions and passing yards.
  - Targets are *absent*, not zero, where the source did not collect them (2003–08).

## The line

Line = the player's median over their previous ≤17 REG games. It needs 4 prior games,
and the lookback crosses seasons.

- **Pushes are void.** `x == line` is not graded.
- **Eligibility floor:** the line must reach the screened-population floors in
  `CLAUDE.md`: receptions 2, targets 3, rec yds 25, rush att 6. For this unit's own
  choice, rush yds is 25, pass att 20, completions 12 and pass yds 150.

**The brief's premise ("true hit rate 50% by construction") does not hold for this
construction.** Measured, real, 2000–2025:

| selection | hit rate | push | n |
|---|---|---|---|
| no floor | 0.5312 | 0.705 | 413,786 |
| **floor (used)** | **0.4498** | 0.089 | 203,233 |
| line ≥ 2× floor | 0.4215 | 0.082 | 73,173 |

The iid null reproduces it (0.4451, band across reps [0.4436, 0.4465]), so the gap is a
property of the construction and not of real-data dynamics. Selecting on the line
selects lines that sit high against the player. The exact null the brief describes is
the **coin** world below.

## Boards

Each board is evaluated for a pair's next appearance, using graded games before it:

- **L7:** ≥6 of the last 7.
- **L5:** 5 of 5.
- **AWAY5:** 5 of the last 5 away games, with the next game away.
- **H2H1:** exactly one prior graded game against the next opponent, and it hit.

## Worlds

- **real:** the data as it is.
- **coin:** real schedule and push pattern, every graded outcome a fair coin. This is
  the exact null.
- **iid:** each player-season's values are redrawn with replacement from that same
  player-season, then the lines are rebuilt.
  - The world has no hot hand and no within-season order. Discreteness, ties and
    between-season level changes are the same as in real.
  - This is the null the real data is compared against.

Null bands are 2.5–97.5 percentiles over 100 replicates. Intervals on real are 95%
bootstraps clustered by player (1,000 draws).

## 1. Counts: pairs reaching each streak

**2025** (919 pairs scanned):

| board | real | share of pairs | coin null | iid null |
|---|---|---|---|---|
| 6 of 7 | 191 | 20.8% | 189 [170, 209] | 177 [158, 193] |
| 5 of 5 | 151 | 16.4% | 135 [118, 158] | 132 [115, 148] |
| 5 of 5 away | 68 | 7.4% | 70 [54, 84] | 70 [54, 83] |
| 1 of 1 vs opp | 585 | 63.7% | 564 [545, 586] | 557 [535, 577] |

**2000–2025** (5,164 pairs):

| board | real | share of pairs | coin null | iid null |
|---|---|---|---|---|
| 6 of 7 | 2,183 | 42.3% | 2,175 [2129, 2231] | 2,133 [2095, 2177] |
| 5 of 5 | 1,979 | 38.3% | 1,860 [1810, 1916] | 1,935 [1896, 1982] |
| 5 of 5 away | 1,131 | 21.9% | 1,129 [1088, 1172] | 1,197 [1156, 1237] |
| 1 of 1 vs opp | 3,767 | 72.9% | 3,827 [3793, 3859] | 3,777 [3743, 3813] |

The real counts sit within 14% of both nulls, and within 7% on the pooled seasons.

- The sign varies by board.
- Six of the 16 real-vs-null comparisons fall just outside a band, four above and two below:
  - 2025 5 of 5 vs iid;
  - 2025 1 of 1 vs iid;
  - pooled 6 of 7 vs iid;
  - pooled 5 of 5 vs coin;
  - pooled away vs iid (below);
  - pooled 1 of 1 vs coin (below).
- These are uncorrected comparisons, and they are not read as a finding.

**Streak-weeks** (a pair on the board, counted every week it stays there), 2000–2025:

| board | real | iid null |
|---|---|---|
| 6 of 7 | 14,249 | 16,071 [15662, 16515] |
| 5 of 5 | 8,220 | 9,351 [9009, 9665] |

Real streaks are *shorter* than the iid null's.

## 2. The 20-slot board, 2025

The 2025 slate offers a median of 547 eligible pairs a week (min 475).

| board | coin: weekly qualify rate | pairs needed (expected) | pairs needed (≥20 every week, 95%) | fewest coin qualifiers in any week |
|---|---|---|---|---|
| any of the four | 0.171 | 117 | 202 | 67 |
| 6 of 7 alone | 0.053 | 379 | 698 | 22 |
| 5 of 5 alone | 0.027 | 745 | 1,742 | 8 |

A 20-slot "any streak" board fills itself three times over every week from coin flips
alone.

## 3. Survivorship: the next game after qualifying

Displayed = what the board showed. Next = the hit rate at the pair's next graded
appearance.

**Coin null** (across 100 reps), 2000–2025:

| board | displayed | next |
|---|---|---|
| 6 of 7 | 0.876 | 0.4994 [0.4902, 0.5077] |
| 5 of 5 | 1.000 | 0.4995 [0.4874, 0.5144] |
| away 5 of 5 | 1.000 | 0.5011 [0.4850, 0.5218] |
| 1 of 1 | 1.000 | 0.5003 [0.4952, 0.5059] |

For 2025 alone, 6 of 7 goes to 0.4990 [0.4645, 0.5327]. The brief's prediction holds
exactly here: 50% within noise, against a displayed 88–100%.

**Real**, 2000–2025 (base rate 0.4498 [0.4464, 0.4530]):

| board | displayed | next | lift over base | real − iid |
|---|---|---|---|---|
| 6 of 7 | 0.884 | 0.5340 [0.5249, 0.5435] | +0.084 [+0.074, +0.094] | −0.035 [−0.046, −0.023] |
| 5 of 5 | 1.000 | 0.5691 [0.5565, 0.5813] | +0.119 [+0.106, +0.132] | −0.030 [−0.045, −0.014] |
| away 5 of 5 | 1.000 | 0.5168 [0.4953, 0.5370] | +0.067 [+0.047, +0.086] | −0.005 [−0.029, +0.021] |
| 1 of 1 vs opp | 1.000 | 0.4506 [0.4426, 0.4589] | +0.001 [−0.007, +0.008] | +0.016 [+0.006, +0.025] |

**Real, 2025 alone** (base 0.4183 [0.3981, 0.4372]):

| board | next | lift over base |
|---|---|---|
| 6 of 7 | 0.4982 [0.4528, 0.5374] | +0.080 [+0.033, +0.119] |
| 5 of 5 | 0.5437 [0.4686, 0.6041] | +0.126 [+0.057, +0.185] |
| away 5 of 5 | 0.4870 [0.3854, 0.5769] | +0.069 [−0.040, +0.156] |
| 1 of 1 vs opp | 0.4239 [0.3899, 0.4589] | +0.006 [−0.027, +0.040] |

**iid null**, 2000–2025 (across reps):

| board | next |
|---|---|
| 6 of 7 | 0.5672 [0.5593, 0.5745] |
| 5 of 5 | 0.6044 [0.5955, 0.6138] |
| away 5 of 5 | 0.5254 [0.5093, 0.5406] |
| 1 of 1 | 0.4380 [0.4335, 0.4439] |

The iid base rate is 0.4451.

Read it in three steps:

1. **The displayed rate overstates the next game by 32–56 points** on every board, in
   every world, on the pooled seasons. In 2025 alone the gap reaches 58.
2. **Against this line, a run-of-form streak does carry information:** +7 to +12
   points over base. But a world with no streakiness at all produces *more* of it. So
   what the streak detects is not a hot hand. It is a player whose level has moved
   while a trailing median has not caught up. **A sportsbook line is not a trailing
   median**, and nothing here measures a streak against one.
3. **"1 of 1 vs opponent" carries nothing** (lift +0.001 [−0.007, +0.008]) and is
   displayed at 100%.

## 4. Real data compared with the null

**On counts, the real data does not differ from chance in any way a board could use.**

**On persistence, it differs in the direction that hurts a streak board.** Real streaks
last less long and predict less than they do in a world with no hot hand
(6 of 7: −3.5 points, interval excludes zero).

Why real persistence falls below the iid null is not established. The likely cause is
that an iid redraw exposes a player's whole-season level from week 1, while real
changes of role arrive gradually and partly revert. That is inferred, not measured.

## Paragraph a page could carry

> A streak describes what already happened, not what happens next. We graded 26 NFL
> seasons of receiving, rushing and passing props against a simple line — each
> player's own median over their previous games. Props that had cleared it in 6 of
> their last 7 games cleared it next time 53% of the time, not the 88% the streak
> displays; props on a perfect 5-for-5 run cleared 57%, not 100%; and a prop that had
> cleared its only previous game against this opponent cleared 45% — exactly the rate
> of every other prop. The part of that 53% above average is not a hot hand: redrawing
> each player's games at random within the season, which removes any streakiness,
> produces streaks that do slightly better. A streak mostly detects a player whose
> role has changed before a line has caught up, and a sportsbook's line is not a
> trailing median. With roughly 550 player-stat props a week, fair coin flips alone
> put at least 67 streak badges on the board every week.

If this paragraph is ever rendered, its figures fall under the claims rule: they must
be computed at render time from exported data, not typed.

## Per stat (real, 2000–2025, 6 of 7)

| stat | base | next |
|---|---|---|
| receptions | 0.442 | 0.537 |
| targets | 0.443 | 0.554 |
| rec yds | 0.444 | 0.485 |
| rush att | 0.444 | 0.600 |
| rush yds | 0.438 | 0.531 |
| pass att | 0.485 | 0.520 |
| completions | 0.486 | 0.488 |
| pass yds | 0.475 | 0.493 |

The usage stats (targets, rush attempts) carry the most. That fits "usage carries the
signal; efficiency is noise" and the role-change reading. No intervals are computed
per stat.
