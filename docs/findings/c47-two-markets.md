# c-47 — c-41's frame on receptions and rush attempts

Pre-registration `docs/C47-two-markets-preregistration.md` (`5374561`, before the script
existed; addendum 1 with the measured power at `d8a227e`, before any settlement was
joined). Script `research/residual_two_markets.py`. **One registered run**, 2026-10-08:
`research/results/residual_two_markets.log` and `.json` (aggregates only); pre-run power
in `residual_two_markets_power.log` / `.json`. 84 registered tests in one Holm family
across both markets, 98 specifications in all. Zero credits; `market_log.db` opened
`mode=ro`. No line-blind model was built. Receiving yards was not run.

**Scope, in the sentence:** NFL receptions and rush attempts, over side, regular season
2023-2025, the de-vigged DraftKings / FanDuel / BetMGM price at each book's own line
about 14 minutes before kickoff, c-41's six linear candidates. Sizes are in over
probability per standard deviation of a candidate.

## The verdict, in three states

**Nothing was detected in either market: 0 of 12 candidate-market cells.** No registered
test survives the correction (0 of 84 at Holm p < 0.05), and no candidate's walk-forward
Brier gain exceeds its MDE.

**That is not twelve nulls of the same strength, and in rush attempts "not detected" says
almost nothing.** What each cell supports, by the registered rule:

| candidate | receptions | rush attempts |
|---|---|---|
| `form_gap` | excluded at 0.033; **0.020 not ruled out** | **UNRESOLVED at every size** |
| `last_game` | excluded at 0.033; **0.020 not ruled out** | excluded at 0.045 only |
| `book_gap` | excluded at 0.033; **0.020 not ruled out** | excluded at 0.045 only |
| `line_pos` | excluded at 0.020 | excluded at 0.033; **0.020 not ruled out** |
| `team_total` | excluded at 0.033; **0.020 not ruled out** | excluded at 0.045 only |
| `log_line` | excluded at 0.033; **0.020 not ruled out** | excluded at 0.045 only |

- **An effect of 2 points per sd is ruled out in one cell of twelve** - receptions
  `line_pos`. In the other eleven it is **not ruled out**, and addendum 1 said so before
  the run for all eleven: their pre-run power at 0.020 was 0.62-0.72 in receptions and
  0.09-0.78 in rush attempts, under the 0.80 the rule requires.
- "Excluded at d" means: the pooled 2023-2025 linear coefficient's Bonferroni interval
  (84 tests) lies inside (-d, +d), the pre-run power of that test at d was at least 0.80,
  and the same holds on the realized SE. It is a statement about a pooled linear
  coefficient and nothing wider.
- **Rush attempts, `form_gap` (the player's own prior mean against the line), is
  unresolved by name.** +0.0201 [+0.0033, +0.0368], SE 0.0085, Holm p 1.00; Bonferroni
  interval [-0.0092, +0.0494], which reaches past the largest pre-stated size. Same sign
  in all three seasons (+0.013, +0.024, +0.023) and unchanged without quarterbacks
  (+0.0201, descriptive). Its walk-forward Brier gain is 0.00042 against an MDE of
  0.00083. The detection rule would have fired on a true effect of this size about 1% of
  the time (pre-run: 0.01 at 0.020, 0.07 at 0.033), so the run could neither confirm nor
  exclude it. It is the same position c-41's `team_total` was left in.
- **Receptions, `book_gap` (all books minus the bench price), sits inside the region the
  run cannot resolve below 0.033.** +0.0140 [+0.0037, +0.0246], Holm p 0.64; Bonferroni
  interval [-0.0041, +0.0320]. Positive in 2023 and 2024, negative in 2025 (-0.0023).
  Excluded at 0.033, not at 0.020.

## Populations

| | receptions | rush attempts |
|---|---|---|
| bench rungs / player-games / games | 10,527 / 9,450 / 813 | 4,581 / 3,654 / 806 |
| dropped: not regular season / position / void / tie | 640 / 32 / 9 / 0 | 258 / 0 / 5 / 0 |
| positions (rungs) | WR 5,562, TE 2,428, RB 2,537 | RB 3,134, QB 1,408, WR 26, TE 13 |
| over rate against mean price | 0.4704 / 0.4943 | 0.4722 / 0.4996 |
| price inside [0.45, 0.55] | 40.1% | 76.9% |
| Brier of the close (0.2500 for a constant 0.5) | 0.2445 | 0.2477 |

## Pooled coefficients on the bench rungs

Over probability per sd of the candidate, game-block bootstrap. Candidates in the
registered order; the order carries no claim.

**Receptions**

| candidate | b | 95% interval | Bonferroni interval | SE | Holm p | same sign 2023/24/25 |
|---|---|---|---|---|---|---|
| `form_gap` | +0.0059 | [-0.0035, +0.0150] | [-0.0102, +0.0220] | 0.0047 | 1.00 | yes |
| `last_game` | -0.0043 | [-0.0144, +0.0056] | [-0.0216, +0.0130] | 0.0050 | 1.00 | no |
| `book_gap` | +0.0140 | [+0.0037, +0.0246] | [-0.0041, +0.0320] | 0.0053 | 0.64 | no |
| `line_pos` | -0.0042 | [-0.0119, +0.0037] | [-0.0177, +0.0093] | 0.0039 | 1.00 | no |
| `team_total` | +0.0019 | [-0.0088, +0.0122] | [-0.0168, +0.0206] | 0.0054 | 1.00 | no |
| `log_line` | -0.0060 | [-0.0156, +0.0041] | [-0.0231, +0.0111] | 0.0050 | 1.00 | no |

**Rush attempts**

| candidate | b | 95% interval | Bonferroni interval | SE | Holm p | same sign 2023/24/25 |
|---|---|---|---|---|---|---|
| `form_gap` | +0.0201 | [+0.0033, +0.0368] | [-0.0092, +0.0494] | 0.0085 | 1.00 | yes |
| `last_game` | -0.0047 | [-0.0231, +0.0136] | [-0.0366, +0.0271] | 0.0093 | 1.00 | yes |
| `book_gap` | +0.0025 | [-0.0133, +0.0178] | [-0.0247, +0.0298] | 0.0079 | 1.00 | no |
| `line_pos` | -0.0134 | [-0.0236, -0.0029] | [-0.0315, +0.0047] | 0.0053 | 0.88 | yes |
| `team_total` | +0.0062 | [-0.0114, +0.0238] | [-0.0248, +0.0372] | 0.0090 | 1.00 | no |
| `log_line` | -0.0057 | [-0.0210, +0.0093] | [-0.0322, +0.0207] | 0.0077 | 1.00 | yes |

## Walk-forward Brier, arm minus close, 2024 + 2025

Slope fitted on earlier seasons only. No registered arm's gain exceeds its MDE in either
market.

| arm | receptions (7,275 rungs) | MDE | rush attempts (3,323 rungs) | MDE |
|---|---|---|---|---|
| `form_gap` | -0.00003 [-0.00015, +0.00009] | 0.00017 | -0.00042 [-0.00102, +0.00015] | 0.00083 |
| `last_game` | +0.00014 [-0.00004, +0.00031] | 0.00025 | +0.00000 [-0.00019, +0.00019] | 0.00027 |
| `book_gap` | -0.00008 [-0.00040, +0.00023] | 0.00045 | +0.00003 [-0.00001, +0.00007] | 0.00006 |
| `line_pos` | -0.00002 [-0.00013, +0.00009] | 0.00016 | -0.00010 [-0.00050, +0.00032] | 0.00058 |
| `team_total` | +0.00002 [-0.00006, +0.00010] | 0.00011 | +0.00025 [-0.00032, +0.00084] | 0.00083 |
| `log_line` | +0.00032 [+0.00003, +0.00064] | 0.00043 | -0.00000 [-0.00020, +0.00019] | 0.00028 |
| `close + a` (descriptive) | -0.00058 [-0.00086, -0.00029] | 0.00040 | -0.00086 [-0.00155, -0.00020] | 0.00096 |
| `close + joint` (descriptive) | +0.00037 [-0.00011, +0.00087] | 0.00069 | -0.00021 [-0.00117, +0.00079] | 0.00139 |

## Power, as written before the run

From `--power`: outcomes simulated on the real rows with no settlement joined, one
uniform draw per player-game, a season-stable linear effect planted in one candidate, 600
replications a cell. An **upper bound** on real power (player-games in a game simulated
independent, effect exactly linear and stable, price taken as true).

| | pooled coefficient at the Bonferroni bound: 0.020 / 0.033 / 0.045 | DETECTED rule: 0.020 / 0.033 / 0.045 |
|---|---|---|
| receptions, five candidates | 0.62-0.72 / 1.00 / 1.00 | 0.06-0.10 / 0.33-0.49 / 0.69-0.84 |
| receptions, `line_pos` | 0.98 / 1.00 / 1.00 | 0.28 / 0.84 / 1.00 |
| rush attempts, five candidates | 0.09-0.20 / 0.57-0.78 / 0.94-0.99 | 0.01 / 0.07-0.11 / 0.18-0.31 |
| rush attempts, `line_pos` | 0.78 / 1.00 / 1.00 | 0.12 / 0.50 / 0.86 |

The detection rule's false-positive rate at zero effect was 0 of 600 in every cell.
Realized pooled SEs (0.0039-0.0054 receptions, 0.0053-0.0093 rush attempts) are within
8% of the simulated ones, so the power table describes the run that happened.

## What is and is not in this

- **The unit's "no" is a "not detected", and its strength differs by market.** In
  receptions the detection rule reaches 80% at 0.045 for three of the six candidates; in
  rush attempts it is at most 31% at 0.045 for five of the six. What carries weight here
  is the exclusion column, which rests on the coefficient interval, not on the rule
  failing to fire.
- **11 of 84 registered tests have unadjusted p < 0.05** against 4.2 expected by chance;
  none survives Holm. The tests share rows (a candidate's pooled, per-season, `RM` and
  Brier tests), so 11 is not a count of findings.
- **The only arm that beats the close out of sample beyond its MDE is the constant, in
  receptions** (-0.00058 against an MDE of 0.00040; descriptive, outside the family): the
  over bias, already published. In rush attempts the constant's gain (0.00086) is below
  its MDE (0.00096). Over rate against mean price is 2.4 points low in receptions and 2.7
  in rush attempts on these rows; no candidate was allowed to take credit for it.
- **`line_pos` on the one-rung-per-player-game population** (`RM`, a consistency check
  that cannot rescue a candidate): receptions -0.0139 [-0.0246, -0.0030], Holm p 0.96.
  Recorded, not read.
- **`+10` was kept verbatim from c-41**, so on count lines `form_gap`, `last_game` and
  `line_pos` are nearer a level difference than a ratio. They are c-41's candidates by
  name and formula, not by meaning.
- **Rush attempts includes quarterbacks** (31% of its rungs), a decision registered
  before any outcome. Without them the six pooled coefficients keep their signs except
  `log_line` (-0.0057 with, +0.0085 without; both intervals span zero).

## What this does not establish

- Not that either close is efficient. Twelve linear terms, two markets, one instant.
- Not that an effect of 2 points per sd is absent - in eleven of twelve cells it is
  explicitly not ruled out. Not that an effect of 3.3 points is absent in rush attempts,
  for five of six candidates.
- Nothing about rush-attempts `form_gap` in either direction.
- No ranking of candidates or of markets, and no comparison with receiving yards: no
  between-market difference was tested.
- Nothing about receiving yards (c-41), rushing yards, 2026, the open, an exchange, or a
  price other than a US bench close about 14 minutes before kickoff.
- Nothing about tradeability. No vig, limit or execution is modelled.
- Nothing non-linear, interacted, or off the list of six.
- `team_total`'s input is nflverse's untimestamped closing total and spread, as in c-41.
- The power figures are simulated upper bounds; "excluded" also requires the realized-SE
  condition, which does not depend on the simulation.

## For whoever picks this up

Two cells are left open by name, and they need different things. Rush-attempts `form_gap`
needs **more player-games**, not a re-run: 3,654 cannot separate 0.020 from zero, and a
season held 1,080-1,486 bench player-games in 2023-2025. Receptions `book_gap` needs the same test on an **independent
sample** (2026) - its 2025 season already reads the other way.
