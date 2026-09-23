# F10 — independent check of a-11's components table

Track F, unit f-08, 2026-09-22, unattended. Verification only: nothing was built,
exported or published. Every figure below is printed by

    python -m research.f10_verify_components --db D:/calibrated-sports/data/market_log.db \
        --staged D:/calibrated-sports/staging/a-11/nfl/components \
        --pbp D:/calibrated-sports/data/analytics.db --export D:/calibrated-sports/data/web_export

against `market_log.db` opened `mode=ro`, the 28 staged files a-11 wrote
(`generated_at 2026-09-23T01:28:30Z`), and the local export tree.

## Verdict in one paragraph

**The values agree. The row count does not, and the reason is a real defect upstream of
a-11.** Every one of the 18 stat columns matches the source on all 209,283 staged rows,
`team_targets` equals the source team total on all 209,283, and `team_snaps` is exactly what
a-11 says it is. But the source supports **209,736** rows, not 209,283: **453 played weeks
are missing**, every one a snap-only row (offensive snaps, no stat row) for a team under its
pre-relocation code — OAK 258, SD 125, STL 70, seasons 2013-2019. Not one snap-only row under
those three codes reached the table. The same 453 are missing from the published player season
files (447 absent, 6 in player-seasons with no file at all), so a-11's "0 differing values
against the local export tree" is true, and could not have caught this: it compared the
table against the thing it was projected from.

## Method, and what was actually independent

The exporter was not opened until everything in §1-§4 was measured. Honest about which parts
were predicted and which were fitted:

- **Row set: predicted.** Stat rows plus snap-only rows with `offense_snaps > 0` from 2013 is
  the settlement rule already written in CLAUDE.md. That rule predicted 209,736.
- **Population: FITTED.** I did not know the players-index rule. Candidates tried against the
  staged player list: any offensive stat (4,176, 189 too many — receptions with zero targets in
  2003-08), any target/carry/attempt (4,008, 21 too many), career thresholds (wrong both ways).
  What matches, 3,987 of 3,987 with a symmetric difference of 0: **at least one REGULAR-season
  target, carry or pass attempt**, minus the one documented nameless id. The 21 were all
  players whose only touch was in a playoff game. Because this rule was fitted to the output,
  it is a description of the population, not a check of it.
- **Column mapping: guessed from names, then confirmed by matching.** Also not independent in
  the strict sense; a 0-mismatch result over 18 columns does make a wrong mapping implausible.
- **Denominators: checked against the source and against a second source (PBP)**, never
  against the numerators — see §3.

## 1. The row count

| | rows |
|---|---:|
| stat rows (`nfl_player_week`, latest `data_version`) for the 3,987 players | 194,048 |
| snap-only rows, `offense_snaps > 0`, 2013+ | 15,688 |
| **expected** | **209,736** |
| **staged** | **209,283** |
| expected, not staged | 453 |
| staged, not expected | 0 |

209,283 is therefore 194,048 stat rows + 15,235 snap-only rows, 1999-2026 weeks 1-2, REG and
POST. The table is a strict subset of what the source supports.

**The 453.** 76 players, 113 player-seasons, 2013 (100) to 2019 (33). Example: Khalif Barnes,
OAK 2013 week 4 v WAS, 66 offensive snaps at 100% — present in `nfl_snap_counts`, absent from
the table and from `nfl/players/00-0023487/2013.json`. He has 26 such weeks, 2013-2015.

**Cause** (read after measuring): `jobs/export_web.load_games` folds home/away through
`FRANCHISE` (`OAK→LV`, `SD→LAC`, `STL→LA`), and `played_zero_periods` looks the game up with
the snap table's own `team`, which is NOT folded. `gidx.get((season, week, "OAK"))` misses,
and the row is dropped by the "a week with no joinable game" refusal. Stat rows are unaffected
because `nfl_player_week` already carries the current codes. The line is on `origin/main`
(`jobs/export_web.py:1029`), so this predates a-11 — a-11 inherited it by projecting.

**What it costs.** These are real played weeks, so for those 76 players every per-game rate
over 2013-2019 (e.g. PPR/G = sum / row count) is computed over too few games and runs high,
and any season snap total or share `sum(snaps)/sum(team_snaps)` drops the weeks. Mostly
linemen, tight ends and fullbacks, since those are the players who play without a stat.

## 2. Values

Every column, every staged row, against the source (`nfl_player_week` at latest
`data_version`; snaps from `nfl_snap_counts` via `player_xwalk.pfr_id`):

    mismatches: 0 in each of snaps, snap_share, targets, target_share, rec, rec_yds, rec_td,
    rush_att, rush_yds, rush_td, pass_att, pass_cmp, pass_yds, pass_td, int, fum_lost, two_pt,
    ret_td  (209,283 rows)

once 2003-08 `targets`/`target_share` are taken as null (the documented read-boundary null for
the silent-zero seasons; the source holds zeros). Snap-only rows carry 0 in every stat
column. Pre-2013 `snaps` are null on every row, never 0.

Samples chosen where it is easy to be wrong:

- **Traded mid-season.** 534 player-seasons carry more than one team. Christian McCaffrey
  2022: weeks 1-6 CAR, week 7 SF (23 of 79 snaps, 2 of 46 team targets, days after the trade),
  then SF. `team_targets` follows the ROW's team every week — covered by the full check in §3,
  not just this sample.
- **Zero snaps.** 19,331 rows carry `snaps = 0`; all are stat rows (a kicker's or returner's
  week with a snap-count row showing no offensive snaps). A player with neither a stat row nor
  an offensive snap gets no row, per the settlement rule. 57 rows in 2013+ have `snaps` null:
  a stat row whose player has no snap row.
- **Byes.** No staged row falls in a week its team had no game, except Steve Bono 1999 week 9,
  whose source row has no team — already listed in the manifest's `unresolved_ids`.

## 3. The denominators, against the source rather than the numerators

**`team_targets`: correct by the definition it states, and that definition is nflverse's.**
It equals the sum of `targets` over EVERY stat row of the row's team-week (not just the 3,987
in scope) on 209,283 of 209,283 rows. Against a second source — receiver targets counted from
play-by-play in track F's `f_play_usage` — 10,038 of 11,358 team-games (88.4%) agree exactly;
1,171 differ by -1, 120 by -2, 11 by -3 (PBP counts more), and 16 differ by more than +3, all
in 2001-02, where the PBP is missing receiver ids. The small negative gap is consistent
with targets to players with no stat row (unidentified receivers), which a stat-row sum cannot
see. So a share is "of targets to identified players", about 0.17 targets per team-game
lighter than PBP. That is inferred, not measured: I did not trace the extra PBP targets.

**`team_snaps`: exactly as a-11 says, including its known weakness.** It equals the maximum
`offense_snaps` of the row's own snap-table team on 112,732 of 112,732 rows. In 23 of 7,188
team-games nobody played every snap, and there the max undercounts the team total implied by
`offense_snaps / offense_pct` by 1-4 snaps (373 rows; worst PHI 2022 week 8, 53 against 57).
a-11 measured the same 23 and disclosed them, so this confirms it rather than finding something
new. It does refute the docstring's premise ("someone is on the field for every snap"): in those
23 games nobody was.

**Keying `team_snaps` on the snap table's own team saved it from a source defect.** In four
Super Bowls the snap table has the teams SWAPPED for every player: 2014 NE-SEA (21 of 21 rows),
2015 CAR-DEN (25/25), 2018 NE-LA (19/19), 2020 KC-TB (24/24). Tom Brady's 74 SB XLIX snaps are
labelled SEA. No other game in 3,594 has a single mismatch. Because a-11 pairs each row with
its own snap record's team, `team_snaps` stays right. But **snap-only rows take their `team`
from the snap table, so 19 rows in those four games carry the opponent's team** — Tony Moeaki,
Will Tukuafu and Cooper Helfet are listed as NE in Super Bowl XLIX, and their `team_targets` is
New England's. Their shares are 0 either way. Any page that GROUPS rows by `team`, though,
mixes the two rosters in those games.

## 4. The grain claim — over-scoped

a-11 (`DECISIONS.md`, 2026-09-22) says *"scoring presets carry per-period threshold bonuses,
which cannot be applied to a season sum"*. **No published preset carries a bonus:** `ppr`,
`half` and `standard` all have `bonuses: []`. Each is a linear weighting, so a season sum
scores exactly the same as the sum of per-period scores under every preset that exists today.

What IS true: the contract's `ScoringPreset.bonuses` is `{stat, at, points}`, a per-period
threshold, and it is the same code path custom scoring will use (site-architecture §3.1). So
the per-period grain is required for custom or future presets, and for a per-period Fantasy
leaderboard, and the decision stands. Only its stated reason is wrong about today's presets.
Recommended wording: "the contract allows per-period threshold bonuses and custom scoring
shares the preset path, so the stored grain must be per period; the three current presets are
linear."

## What this does not establish

- **Nothing about R2.** Compared against the staged files and the LOCAL export tree only.
- **The population rule is fitted, not independently derived** (see Method). If the players
  index rule is itself wrong, this check inherits that.
- **The 453 are counted from `offense_snaps > 0`.** Whether each is a genuine appearance
  rests on PFR's snap counts, the same source the settlement rule trusts.
- **2026 is weeks 1-2** in the staged files (940 rows). Every 2026 figure goes stale with the
  next nflverse week; nothing refreshes the staged tree.
- **The PBP comparison uses track F's own PBP table**, which is independent of nflverse's
  weekly stats but not of nflverse. The cause of the -1 gap is inferred.
- **Downstream reach not traced.** `core/settlement.py` and the team pages may use the same
  unfolded snap-team join. Settlement of 2023+ props cannot be affected by the relocation
  half (the last old code is OAK 2019). I did not check the team pages or the Super Bowl
  swap's effect elsewhere.

## Recommendations (for track A, who owns the exporter)

1. Fold the snap table's `team` through `FRANCHISE` in `snap_weeks` or `played_zero_periods`.
   Expected effect: +453 rows (209,736), +447 periods in existing season files, 6 new season
   files. That is a published-figure change to player pages, so it needs a snapshot before
   the export.
2. For snap-only rows, take `team` from the game rather than the snap label where the two
   disagree. Or detect the swapped Super Bowls: every player's label disagrees with the
   weekly-stats team.
3. Optionally, estimate `team_snaps` from the highest-`offense_pct` player's implied total
   instead of the max, where no player reached 100%.
4. Correct the grain rationale in `DECISIONS.md` as in §4.
