# c-41 — what predicts the residual given the line

Pre-registration `docs/C41-residual-preregistration.md` (`de5a5a7`, before the script
existed; addendum 1 at `5c91bcc`, before any residual was formed). Script
`research/residual_given_line.py`. **One registered run**, 2026-10-08:
`research/results/residual_given_line.log` and `.json` (aggregates only). 42 registered
tests in one Holm family, 46 specifications in all. Zero credits; `market_log.db` opened
`mode=ro`. No line-blind model was built.

**Scope, in the sentence:** NFL receiving yards, over side, regular season 2023-2025, the
de-vigged DraftKings / FanDuel / BetMGM price at each book's own line about 14 minutes
before kickoff, six named linear candidates.

## The answer is no

**None of the six candidates predicts the residual of the receiving-yards bench close by
the registered rule. 0 of 6 pass.** No pooled coefficient survives the correction, and
no candidate's walk-forward Brier improvement exceeds its MDE.

The population is c-37's exactly: 18,666 rungs, 10,016 player-games, 814 games (c-37
reported the same three figures). Over rate 0.4793 against a mean price of 0.5005; 99.5%
of prices inside [0.45, 0.55]; Brier of the close 0.2498 against 0.2500 for a constant.

Pooled coefficient on the bench rungs, in over probability per standard deviation of the
candidate, game-block bootstrap:

| candidate | b | 95% interval | SE | p | Holm p | same sign 2023/24/25 |
|---|---|---|---|---|---|---|
| `form_gap` (own prior mean vs line) | +0.0009 | [-0.0090, +0.0114] | 0.0052 | 0.86 | 1.00 | no |
| `last_game` (last game vs line) | -0.0058 | [-0.0173, +0.0059] | 0.0060 | 0.33 | 1.00 | no |
| `book_gap` (all books minus bench) | +0.0076 | [-0.0007, +0.0155] | 0.0041 | 0.064 | 1.00 | no |
| `line_pos` (bench line vs all-book mean line) | -0.0134 | [-0.0232, -0.0043] | 0.0047 | 0.0043 | 0.15 | no |
| `team_total` (team implied points) | +0.0148 | [+0.0048, +0.0242] | 0.0050 | 0.0033 | 0.12 | yes |
| `log_line` | -0.0007 | [-0.0114, +0.0090] | 0.0052 | 0.89 | 1.00 | no |

Walk-forward Brier, arm minus close, 2024 + 2025 (13,405 rungs), slope fitted on earlier
seasons only:

| arm | difference | 95% interval | MDE | reading |
|---|---|---|---|---|
| `form_gap` | +0.00038 | [+0.00014, +0.00063] | 0.00035 | **worse** than the close |
| `last_game` | +0.00008 | [-0.00011, +0.00026] | 0.00027 | nothing |
| `book_gap` | -0.00001 | [-0.00005, +0.00004] | 0.00007 | nothing |
| `line_pos` | -0.00007 | [-0.00021, +0.00006] | 0.00020 | nothing |
| `team_total` | -0.00014 | [-0.00024, -0.00004] | 0.00014 | on its MDE (ratio 0.996) - does not clear |
| `log_line` | +0.00014 | [+0.00002, +0.00026] | 0.00017 | nothing |
| `close + a` (intercept only, descriptive) | -0.00047 | [-0.00074, -0.00019] | 0.00040 | the known over bias |
| `close + joint` (all six, descriptive) | +0.00046 | [+0.00001, +0.00090] | 0.00064 | worse |

## What is and is not in that

- **Power.** Realized pooled coefficient SEs are 0.0041-0.0060 against the 0.0050
  registered, so no test is under-powered by the registered rule. The largest pooled
  estimate is 0.0148; the registration said beforehand that an effect under 0.020 could
  not survive the correction and one under 0.033 could not pass the Brier bar. What this
  rules out is an effect of about 2 points of over probability per standard deviation;
  it does not rule out one of 1.
- **The registered Brier MDE arithmetic assumed the applied slope equals the true one.**
  The walk-forward slopes were much smaller than the pooled ones (`team_total`: +0.0019
  fitted on 2023, +0.0052 on 2023-2024, against +0.0148 pooled), so the realized Brier
  MDEs (0.00007 to 0.00035) are far below the 0.0011 written down. The registered rule
  reads the realized MDE and is applied as written; the 0.0011 was a description of where
  the bar binds for a stable effect, and no effect here was stable.
- **`team_total` is the nearest thing to a signal and it fails three ways.** Its pooled
  interval excludes zero unadjusted and not after Holm (0.12; Bonferroni interval
  [-0.0015, +0.0311]). Its Brier improvement is 0.000140 against an MDE of 0.000141 - an
  estimate sitting on its MDE, which the rule was written to refuse. And its input is the
  one declared soft before the run: nflverse's closing total and spread carry no
  timestamp. Its size, about 1.5pp per 3.7 points of implied team total, is inside a book
  prop's half-overround.
- **The two Holm survivors are single-season cuts** (2025 `line_pos` -0.0242, Holm p
  0.0001; 2025 `team_total` +0.0317, Holm p 0.0057). The registration says a cut cannot
  rescue a candidate and nothing found in a cut is promoted. `line_pos` has the opposite
  sign in 2023 (+0.0062). They are recorded, not read.
- **13 of 42 registered tests have unadjusted p < 0.05** against 2.1 expected by chance.
  The tests are not independent - a candidate's pooled, per-season, `RM` and Brier tests
  share rows - so 13 is not a count of findings; most of them are the 2025 season
  differing from 2023, which is a statement about one season.
- **The player's own history is what makes the forecast worse.** Adding `form_gap` with a
  slope fitted on earlier seasons is worse than the close, interval above zero; its
  fitted slope was +0.0138 on 2023 and -0.0052 on 2023-2024. The joint arm is worse too.
  That is the same fact c-37 found from the other side, now with the line given.
- **The only arm that improves on the close out of sample is the constant**: the over
  bias already published for this market (over rate 0.479 against 0.50). It was kept out
  of the candidates so none could take credit for it. It is about 2.1 points, against a
  typical book prop half-overround of 2.3, and no cost is modelled here.

## What this does not establish

- Not that the close is efficient. Six linear nulls on one market at one instant.
- Nothing about receptions, rush attempts or rushing yards; nothing about 2026, the open,
  an exchange, or any book outside the three bench books' own price.
- Nothing about non-linear or interacted effects, or about any variable not on the list
  (injury status, teammate absence, weather and opponent were not candidates).
- Nothing about tradeability in either direction.
- The bench price is one book on most rungs (c-37: 12,432 of 18,666).

## For whoever picks this up

The candidates that touch another market (`team_total`, `line_pos`, `book_gap`) carry the
only non-zero unadjusted estimates; the three that describe the player or the line alone
are at zero. If a second pre-registration is written, the one test with a reason behind
it is `team_total` re-derived from the **timestamped** Odds API game rungs at the same
snapshot, on the count markets as an independent sample - not a re-run on these rows.
