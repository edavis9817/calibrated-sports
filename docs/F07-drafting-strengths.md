# F07 — drafting strengths: on roster4 and bust, no team separates from shuffled labels

Unit f-01, 2026-09-22; corrected by f-06 the same day (below). Reproduce with `python -m analytics.drafting` (from a 3.12
environment; ~17 s). Tests: `tests/test_drafting.py`. **Nothing exported, nothing
published.** The export shape is f-02.

Scope, so it travels with the claim: **NFL draft classes 2002-2022, all 32
franchises, the nflverse mirror as pulled 2026-09-09 (draft_picks) and 2026-09-09
to 09-21 (roster_weekly, snap_counts); four outcomes defined below; a pick-slot
model; no forecast of any single pick.**

---

## Correction (f-06, 2026-09-22) — read this before the answer

Track A re-derived this null independently (a-06, `docs/A09-verify-f07.md` on
`a-06-verify-f01-drafting`) and it **holds** on roster4 and bust: separation and
walk-forward agree in sign and significance under a different method, and the
roster4 walk-forward matches at −0.057 against −0.056. Two things did not survive,
and both are corrected below rather than footnoted:

1. **The first summary said "the as-of walk-forward is null." That was true of
   roster4 and bust and FALSE of snaps4**, whose interval excludes zero in both
   methods: +0.139 [+0.016, +0.247] on 4 targets here, +0.155 [+0.011, +0.266] on 6
   classes in A's. On four to six blocks that is **not readable** — below the
   five-block floor — which is neither a null nor an edge. The table's own snaps4 cell
   said "not read"; the sentence above it claimed more than the table did. The same
   applies to the 2002-2010 era cut, whose walk-forward excludes zero *below* on 3
   targets. Verdicts are now per outcome and computed (`drafting.forecast_verdict`,
   printed in a SCOPE block by `python -m analytics.drafting`), and there is no
   verdict over all outcomes anywhere in the code.
2. **The id join scored 59 rostered picks as busts, not the 9 the docstring said.**
   See defect 3. Fixed by recovering `gsis_id` from the roster feed's own draft slot;
   **the null survives and is stronger on bust** (p 0.16 → 0.30). All figures below
   are the corrected run.

## The answer

**Per outcome — there is no single verdict over all four:**

- **roster4 and bust** (the two outcomes that measure drafting and can be forecast
  on enough classes): the 32 teams cannot be told apart from teams given random
  picks of the same slots, **and** a team's closed draft record does not forecast
  its next class — `no_better_than_chance`, 15 targets each.
- **snaps4**: does not separate (p 0.14). Its walk-forward is **not readable**: the
  interval excludes zero, positive, on 4 targets. This says nothing either way about
  whether playing-time value over the slot persists by franchise, and it must not be
  quoted as a null.
- **w_av**: separates (p 0.003), but it cannot be read as drafting (below), and it
  has no as-of forecast at all.

| outcome | classes | picks | separation p | signal share | walk-forward r [95%] | forecast verdict | teams excluding a typical team | chance count, measured |
|---|---|---|---|---|---|---|---|---|
| **roster4** (primary) | 2002-2022 | 5,371 | **0.72** | 0.00 | **−0.057 [−0.114, +0.016]**, 15 targets | no_better_than_chance | 1 | 2.4 (p95 5) |
| **bust** | 2002-2022 | 5,371 | **0.30** | 0.11 | **−0.007 [−0.106, +0.083]**, 15 targets | no_better_than_chance | 3 | 2.8 (p95 5) |
| **snaps4** | 2013-2022 | 2,558 | **0.14** | 0.22 | +0.139 [+0.016, +0.247], **4 targets** | **not_readable** (excludes 0) | 4 | 3.1 (p95 6) |
| **w_av** | 2002-2021 | 5,109 | 0.003 | 0.47 | none possible (not as-of) | none | 3 | 2.5 (p95 5) |

As first published (before the id fix): roster4 p 0.68, walk-forward −0.056
[−0.116, +0.021]; bust p 0.16, signal share 0.20, walk-forward +0.002 [−0.083,
+0.088], 2 teams excluding. snaps4 and w_av do not read the gsis join and did not move.

- **Separation p** is the share of 2,000 label permutations (team labels shuffled
  among each class's picks) whose between-team spread is at least the observed
  one. **Signal share** is `1 − E[var_null]/var_obs`: the fraction of the spread
  you see that is not noise. On roster4 it is zero: the observed spread (SD 0.0199)
  is *smaller* than the mean shuffled spread (0.0216).
- **Walk-forward** predicts a team's class-t residual from its record over classes
  ≤ t−4, the classes whose four-year horizon had closed by draft day t. On roster4
  and bust neither interval excludes zero, over 15 targets each (roster4's point
  estimate is slightly *negative*). **snaps4's interval does exclude zero**, on 4
  targets, below the five-block floor: `not_readable`, not null.
- **Teams excluding a typical team** is the count whose own 95% class-block
  interval excludes 0, set against the same count **measured under shuffled labels**.
  On every outcome the observed count is inside the chance p95. It is below the chance
  *mean* only on roster4 (1 against 2.4); bust (3 / 2.8), snaps4 (4 / 3.1) and w_av
  (3 / 2.5) sit above the mean. The first version said "at or below chance" on every
  outcome, which was already false of snaps4 and w_av against the mean (f-06).

### The one outcome that separates is the one that cannot tell drafting from winning

Career Approximate Value separates teams clearly (p = 0.003, split-half r = 0.47,
Spearman-Brown 0.64). It also correlates **r = 0.82 [0.65, 0.91]** with the team's
regular-season win share, 2002-2025. The three clean outcomes run r = 0.18 (roster4),
0.27 (bust) and 0.17 (snaps4), and each of those intervals contains zero.

That correlation does not say which way the effect runs, which is the problem. AV is
*allocated* from team points, so a pick on a good team collects more of it for the
same play. And drafting well also wins games. AV cannot separate those two
explanations, and nothing on disk can. Whatever the w_av spread measures, it cannot
be labelled "drafting strength". It also cannot back a forecast: it is a career total
as of the pull date, censored by class age, so no as-of version exists.

**So the finding is not "draft value is too noisy to measure".** The pick-slot curve
is strong, and a team that holds the top picks gets far more raw value (the test
`test_draft_capital_is_not_credited_as_skill` pins this). The finding is
**narrower: on roster4 and bust, value *over the slot* neither separates franchises
nor persists by franchise.** On snaps4 persistence is unreadable (4 targets), not
absent.

### Era and position

- **Era**, roster4: 2002-2010 p = 0.14; 2011-2022 (rookie wage scale) p = 0.79 with
  signal share 0. Neither era separates. Walk-forward within an era has 3 and 6
  targets. The 6-target one contains zero (`no_better_than_chance`). **The 3-target
  one excludes zero, below**: −0.076 [−0.138, −0.023]. It is under the five-block
  floor, so it is `not_readable` — the same status as snaps4, in the other direction.
- **Position**, roster4, with the slot curve refitted within each group: none of nine
  groups separates (p from 0.10 to 0.91, and **zero survive BH at q = 0.10**). The
  best groups are DB (p 0.10) and LB (p 0.14). **ST cannot be read at all**: the
  minimum is one pick per team over 21 years, which is why its per-team exclusion
  count (9) is noise. QB (min 5 per team) and TE (min 4) are close to the same
  state. "A team that drafts kickers well and tackles badly" is a comparison this
  data cannot make at the team level.

---

## What "value realised" means, and what each measure cannot see

| outcome | definition | cannot see |
|---|---|---|
| roster4 | share of regular-season weeks, first 4 seasons, on **any** team's roster (53 or reserve lists) | quality, health |
| bust | roster4 = 0 | anything above zero |
| snaps4 | offense + defense snaps, first 4 seasons, class-relative | special teams; pre-2013 |
| w_av | PFR weighted career AV, class-relative, null → 0 | the team-vs-player split (above) |

A fixed four-year horizon is the reason roster4 is primary. A 2004 pick and a 2019
pick are measured over the same span, and no class is censored. A pick's value to
**any** team is used, not to the drafting team, because the question is selection.
Retention is a different question (`own_weeks` is computed and not analysed).

**The null is a typical team, not zero.** Each outcome becomes a residual against
the league's pick-slot expectation (a kernel smoother in log pick, pooled across
classes) and is centred within its class. A team's number is value over the slot
per pick, and 0 is "drafts exactly as the league does with the same picks". Draft
capital is not credited as skill.

**Survivorship is scored, not dropped.** A pick with no roster week in four years
scores 0 and counts as a bust. That is 5.4% of picks, 2002-2022 (6.5% before
the f-06 id fix, which moved 59 rostered picks out of the bust count).

---

## Data defects found on the way, all measured

1. **`roster_weekly.status` before 2016 is a SEASON STAMP, not a weekly status.**
   Before 2016, 2-9% of player-seasons carry more than one status. From 2020 the
   figure is 63-72%. Stephen Hill (NYJ, 2012 round 2; 23 PFR games, all in 2012-13, career
   over by 2013) is `RES` in every week of 2012 and of 2013. The first version of this unit counted
   `ACT` weeks, and it produced exactly the wrong kind of result: **a "2002-2010
   separation" at p = 0.01 (200 permutations) that vanished (p = 0.13) once the measure became roster
   presence.** The feed was reading teams' IR habits as drafting skill. Presence is
   weekly in every era; active status is not. Pinned by
   `test_roster_status_before_2016_is_a_season_stamp`. **Anyone reading
   `roster_weekly.status` as a weekly fact before 2016 has the same defect.**
2. **The roster feed spells teams three ways**: PFR (`GNB`), nflverse (`GB`) and GSIS
   (`ARZ`, `BLT`, `CLV`, `HST`, `SL`). The first `own_weeks` compared nflverse codes
   against PFR codes and would have scored 0 own-team weeks for every team whose
   codes differ. A test caught it before any figure used it. `to_franchise` now
   refuses any code it cannot place.
3. **219 picks carry no `gsis_id` in `draft_picks`**, and neither
   `players.parquet` nor the roster feed's own `pfr_id` (2-15% populated before 2010)
   resolves them. **CORRECTED (f-06).** This first said "9 of them played ... a
   misclassification of 9 in 5,371". That counted id-less picks with PFR *games*, but
   roster4 is roster *presence*: a pick on injured reserve or inactive all season has
   0 games and is still on a roster. The roster feed carries each player's own draft
   slot (`entry_year`, `draft_number`, `draft_club`), and that is a key, not a name:
   `drafting.recover_gsis` matches on the slot and accepts only when the club is the
   pick's franchise, the folded last name is a word of the PFR name, the slot has one
   candidate id and the id belongs to no other pick. Measured on the mirror:
   **67 candidates, 66 accepted, 1 refused** (2007 #159, PHI's C.J. Gaddis, where the
   feed puts Jared Gaither, a BAL supplemental pick, on the same number), 152 with no
   candidate. **59 of the 66 had roster weeks in their first four seasons and had
   been scored as busts**; the 9 with games are among them. Track A's independent
   recovery found the same 66 (a-06). The null survives: roster4 p 0.68 → 0.72, bust
   p 0.16 → 0.30, both walk-forwards still cover zero. 153 picks still have no id and
   score 0; that count is `sample.unresolved_rows` in the export. 24 picks with an id
   show PFR games but no roster week in four seasons (33 before the fix), not
   individually checked; the likely causes are a debut after year four, or games
   before a mid-season waiver.
4. **Practice squad appears in the feed only from 2006**, and the player-season
   population jumps from about 1,950 to about 3,100 in 2016-17. DEV is excluded for
   both reasons.
5. **The nominal chance count (0.05 × 32 = 1.6) understates chance.** Measured under
   shuffled labels it is 2.3-3.1. A percentile block bootstrap over 10-21 classes
   runs narrow. **A per-row "interval excludes the null" criterion needs a measured
   chance baseline beside it, or a page will present about one false separation per
   metric as a finding.** This bears directly on F04's criterion.

## Bands: computed, and not to be rendered here

Bands against the leader split roster4, bust and snaps4 into two bands each. Yet the
separation test cannot tell any of those three from shuffled labels. The leader is a
**selected maximum** of 32 noisy numbers, and each contrast against it is one of 31
unadjusted tests, so a band boundary can appear where no separation exists. The
module marks `bands_readable` only where the separation test rejects. For these
three outcomes, a band boundary would be exactly the claim this unit refutes.
**Recommendation for f-02: render bands only where the separation test rejects**;
otherwise render the single statement that no team separates - scoped to the slice
it is computed on, never to the predictor.

---

## Licensing — the brief's "no licensing question" is not established

**Superseded by f-07 (2026-09-22): the terms have now been read. See
`docs/F09-sports-reference-terms.md`.** They are neither silent nor a prohibition.
Section 5 *welcomes* republishing data "whether for commercial or non-commercial
purposes", on condition of explicit credit to Sports-Reference, and subject to 5(i)
(no substitute data store) and 5(j) (no AI/ML training). The drafting file meets all
three, and snaps4 and w_av are now published. **One correction to the paragraph below:**
roster4 and bust are not free of the question. Their draft order is PFR's table via
nflverse, so the credit covers every slice. The original text is kept as it was written.

`draft_picks` is nflverse's copy of Pro-Football-Reference's draft table.
`w_av`, `dr_av`, `car_av`, `pfr_player_id`, `games` and `seasons_started` are PFR's
columns, and `snap_counts` is PFR-sourced too. **This is the same shape as F06's OTC
question**: an upstream site's data redistributed under nflverse's CC-BY, with no
stated permission. I did not fetch Sports-Reference's terms tonight, so this is
*inferred from provenance*, not read from the terms.

The primary outcome is built so that the question is small. roster4 and bust use
NFL-sourced `roster_weekly` plus the draft order (season, pick, team, player id),
which is public record. **If f-02 shows anything, show roster4/bust, not AV or
snaps.**

## What the real data cannot support

- Any per-team drafting ranking or band on these outcomes.
- Any claim that a front office "drafts well" *as a skill* on roster4 or bust: their
  walk-forwards are `no_better_than_chance` on 15 targets each.
- **Any claim either way about persistence on snaps4**: its walk-forward excludes
  zero on 4 targets and is `not_readable`. Not "null", not "an edge".
- Any single sentence about "the walk-forward" that does not name its outcome.
- Any per-position team comparison, and ST/QB/TE in particular.
- Any AV-based claim labelled as drafting: AV is confounded with winning at r = 0.82.
- A snaps-based forecast: 4 walk-forward targets, below the five-block floor.
