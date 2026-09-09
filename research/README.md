# Original research scripts — the missing provenance

These are the exact scripts that produced every number quoted as "settled by
evidence". They were run in a sandbox and delivered as downloads, never
committed, which is why the repo could not reproduce them. Commit them to
`research/` so the reference is auditable.

| script | produced |
|---|---|
| `markets.py` | the market forecastability ranking |
| `decomp.py` | usage vs game-script vs opponent decomposition; same-game correlations |
| `externals.py` | situational factors, player residual persistence, distribution moments |
| `continuity.py` | coach/QB continuity split of team tendency stability |
| `ngs_transfer.py` | NGS trait retention across team changes |
| `stickiness.py` | pooled team-season year-over-year stability |

## The screen that was never written down

`markets.py` and `externals.py` both filter on the player's PRIOR expanding
mean, keeping only players with an established role:

```
receptions (WR/TE)      prior_mean >= 2.0
targets (WR/TE)         prior_mean >= 3.0
receiving_yards         prior_mean >= 25.0
carries (RB)            prior_mean >= 6.0
rushing_yards (RB)      prior_mean >= 25.0
receptions (RB)         prior_mean >= 1.5
pass attempts (QB)      prior_mean >= 15.0
completions (QB)        prior_mean >= 10.0
passing_yards (QB)      prior_mean >= 120.0
```
plus `>= 4` prior games in season.

This is the entire source of the +0.17 to +0.28 gap against an unscreened
reconstruction. Restricting to established-role players cuts cross-sectional
variance, which lowers R². The unscreened number is inflated by trivially
predictable near-zero-usage players.

Neither is wrong; they answer different questions. The screened one asks "among
players you would actually bet, how forecastable is this?" — which is the
decision-relevant framing, and why the screen was there. It should have been
documented rather than left in a filter argument.

Data source: the now-dead `player_stats` release, seasons 2016-2024, train
2016-22 / test 2023-24. The A/B against `stats_player` showed the source made
no material difference (every market within 0.012).
