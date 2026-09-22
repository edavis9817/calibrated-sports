# F08 — drafting strengths as a published shape: the write-up of a null on two slices

Unit f-02, 2026-09-22. Depends on F07 (`docs/F07-drafting-strengths.md`), which found
that on roster4 and bust no team separates from shuffled labels (w_av separates and is
withheld; see below). **So this unit is the write-up of that
null, not a metric made to look publishable.** The file's shape could say otherwise.
Every verdict in it is computed, and each enum value is reachable; the tests drive
each one. On this data, though, it says that on the two PUBLISHED slices (roster4,
bust) nothing separates and nothing forecasts.

**Corrected by f-06.** This paragraph first ended "it says nothing separates", which
was a claim over all four slices the file defines and false of one: w_av separates
(p 0.003; withheld as confounded). And F07's "the walk-forward is null" was false of
snaps4, whose interval excludes zero on 4 targets. Neither withheld slice's figures
are in the file, so the file never stated either sentence - the prose did. The
verdicts below are per slice; see *Per-slice verdicts, including the withheld ones*.

Reproduce: `python -m analytics.predictor_export --write --out <dir>` from a 3.12
venv (~20 s; it re-measures from the mirror). Tests: `tests/test_predictor_export.py`.
**Built and validated locally. Not uploaded, not written to `WEB_EXPORT_DIR`, and
the contract is not edited.**

Scope, so it travels with the claim: **NFL draft classes 2002-2022, 32 franchises,
`draft_picks@2026-09-09`, `roster_weekly@2026-09-09`, `games@2026-09-22`.**

---

## What the file says

One key, `predictors/nfl/drafting.json`: 20 KB, 64 values, 4 slices.

| slice | status | separation | scoring record (walk-forward r) | bands | teams excluding a typical team / measured chance |
|---|---|---|---|---|---|
| roster4 | published | does_not_separate, p 0.72 | no_better_than_chance, −0.057 [−0.114, +0.016], 15 targets | null: separation_not_rejected | 1 / 2.4 |
| bust | published | does_not_separate, p 0.30 | no_better_than_chance, −0.007 [−0.106, +0.083], 15 targets | null: separation_not_rejected | 3 / 2.8 |
| snaps4 | withheld | null | null | null: withheld | null |
| w_av | withheld | null | null | null: withheld | null |

The figures are identical to F07's corrected run (f-06: the gsis recovery; before it,
roster4 p 0.68 / −0.056 and bust p 0.16 / +0.002, 2 of 32 excluding).
`sample.unresolved_rows` is now **153**, was 219. The measurement is the same code
at the same seed.

## Per-slice verdicts, including the withheld ones (f-06)

A withheld slice is `null` in the file, and a null is not a verdict. So what the file
does **not** say about the withheld slices is stated here, per slice:

| slice | separation | forecast (walk-forward) | what a page may say |
|---|---|---|---|
| roster4 | does_not_separate, p 0.72 | **no_better_than_chance**, 15 targets | no team separates; the record does not forecast the next class |
| bust | does_not_separate, p 0.30 | **no_better_than_chance**, 15 targets | same |
| snaps4 | does_not_separate, p 0.14 | **not_readable**: +0.139 [+0.016, +0.247], excludes 0 on **4** targets | nothing - withheld |
| w_av | **separates**, p 0.003 (confounded with winning, r 0.82) | none: not as-of | nothing - withheld |

**Decision taken (f-06, reversible): snaps4 stays withheld** - "not at all" rather
than "honestly". Two reasons, the first sufficient alone. (1) Its licence question is
open with Ethan (Sports-Reference terms unread; `NEEDS-ETHAN.md`, f-01/f-02), and a
record computed from PFR snap counts is PFR-derived like the values. (2) Published, it
would add one `not_readable` record and 32 per-team values beside two
`does_not_separate` slices - a page with more numbers and no more information.

**The schema already carries all three verdicts** the brief names:
`no_better_than_chance` and `not_readable` in `PredictorRecord.score.verdict` (plus
`forecasts` / `forecasts_inversely`), and `separates` in
`PredictorSeparation.verdict`. `not_readable` is the brief's "unreadable". The export
was never the failure: `record_verdict(0.02, 0.25, 4)` was pinned to `not_readable` in
f-02's tests with the comment "snaps4's shape". What failed was prose. So f-06 changes
no schema, and a new test builds snaps4 **as if published**, from its measured
figures, and asserts it validates with `not_readable` and an interval above zero - so
the day the licence clears the file cannot say "null" about it.

**What cannot ride along in the file, and why.** Track A's adopted contract (a-05,
unmerged at the time of writing) requires every measured field of a withheld slice,
`record` included, to be null. So the file cannot carry snaps4's `not_readable`
without publishing its figures, and "no interval, no verdict" says it should not try.
A page that writes a sentence over the published slices must scope it to them; it may
not generalise to "the drafting predictor".

## The shape, and why each part is there

- **The record sits beside the metric, in the same file, keyed to the same slice.**
  `slices[s].record.score` is the walk-forward forecast score. Each team's value sits
  in `values`, with the same `slice`. When a slice has never been scored, `score` is
  **null with a reason** (`not_as_of`, `too_few_targets`). It is never simply absent.
- **Every per-team value carries its interval and its sample.** `values` reuses the
  contract's `AnalyticValue`, so the interval and `n` are mandatory on both sides.
  `n` = 21 draft classes (the blocks), and `rows` = picks, which is not the sample.
- **Everything about a slice lives once, on the envelope.** Its null, separation
  test, measured chance baseline, record, confound check and bands are stored under
  `slices[s]`. None of them is repeated per row.
- **The null is on the envelope and is not zero by assumption.**
  `null_definition` states it in words (a typical team), and `null_value` is the
  number. The two coincide at 0 only because residuals are centred within each class.
- **A withheld slice is present.** It carries its label, unit, what it cannot see,
  its direction and its window, sets every measured field to null, and names
  `withheld_reasons`. A page can say why a number is missing, whereas an absent slice
  looks the same as one nobody built.
- **Bands are against each band's leader, never the row above.** Each band is
  `{leader, members}`, with members alphabetical. There is no band number and no rank,
  and the schema refuses an extra key. The leader is the **best** member *in the
  slice's direction*: for bust, that is the fewest busts. **Bands are null wherever
  separation does not reject.** When they are null, the proposal tells the site to list
  values as one unordered, alphabetical set.
- **`chance` carries the measured baseline.** It gives the number of teams whose
  interval excludes the null, next to the number that do so under shuffled labels
  (F07 defect 5). Without that baseline, a page presents about one false separation
  per slice as a finding.
- **`confound` is the correlation with team win share.** For w_av it was 0.82, which
  is why w_av is withheld as `confounded`. The two published slices sit at 0.15 and
  0.26, and both intervals contain 0.

## Decisions taken (reversible)

1. **A new kind, `predictor`, under a new top-level prefix `predictors/`**, rather than
   another `analytics.metric`. The existing metric kind has no place for a scoring
   record, a separation test or bands. The prefix is not `analytics/`, because
   `analytics.export.sync` deletes everything under `analytics/` that its own build
   did not produce. The kind is generic (`subject_type` player|team), so a future game
   model can use it.
2. **snaps4 and w_av are withheld, not published.** Both read
   Pro-Football-Reference columns whose terms nobody has read (F07), and w_av is also
   confounded. This follows f-01's recommendation.
3. **Bands are null when p ≥ 0.05, not rendered with a warning.** A page that is able
   to render them will.
4. **The local write goes to a scratch directory** (`D:/temp/f02/export`), not the
   module's default `config.storage_path("predictor_export")`. This leaves no footprint
   in the production store for a file nobody reads yet. The default exists for daylight.
5. **The writer never deletes.** One predictor, one key. A retiring predictor will need
   the F4 declaration before anything is deleted, locally or remotely.
6. **`drafting.bands()` gained `higher_is_better`.** F07's bust bands were led by the
   team with the *most* busts over the slot. That was invisible in F07, because those
   bands were marked unreadable, but a published shape has to get it right.

## What the data cannot support (unchanged from F07, restated for the page)

- Any per-team drafting ranking or band on roster4 or bust.
- "Team X drafts well" as a persistent skill: the scoring record shows no forecast
  skill on roster4 or bust. That is a statement about those two slices only; snaps4's
  record is not_readable, and w_av has none.
- Anything from snaps4 or w_av until their licensing is read, and never w_av labelled
  as drafting.
- A roster4 value is employment. It cannot see quality or health.

## The proposal

`docs/proposals/F08-predictor.defs.json` holds one kind, one key pattern and six
`$defs` (`PredictorFile`, `PredictorSlice`, `PredictorSample`, `PredictorSeparation`,
`PredictorRecord`, `PredictorBand`). None of them collides with the contract today,
and a test asserts that. The module validates against the vendored contract plus the
proposal, merged in memory. The same test file shows the merged schema refusing:

- a value without an interval,
- a published slice without its record,
- a score on 0 targets,
- an unknown withheld reason,
- a band carrying a number,
- a blank range note, and
- a key that implies another kind.

Filed to track A as F6 in `docs/track-a-requests.md`.
