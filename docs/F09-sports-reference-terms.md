# F09 — Sports-Reference's terms and the drafting columns: not silent, not a prohibition

Unit f-07, 2026-09-22. Track F. Code: `analytics/pfr_terms.py` (quotes, provenance and
a classification for each column); `analytics/predictor_export.py` (the file now carries
the credit). Tests: `tests/test_pfr_terms.py`, `tests/test_predictor_export.py`.

**Built and validated locally. Nothing is uploaded or published, and nothing is written to
`WEB_EXPORT_DIR`.**

## The question, and its answer

The posture settled on 22 September says: publish where a source's terms are **silent**,
and refuse where they **prohibit**. The brief asked which of the two applies to
Sports-Reference.

**Neither.** Sports-Reference's Terms of Use (last updated 2023-05-19, read 2026-09-22 at
https://www.sports-reference.com/termsofuse.html) are not silent. Section 5 speaks to
republishing directly and **permits** it, on conditions:

> Our guiding principles are that (1) sharing, using, modifying, repackaging, or
> publishing data found on individual SRL webpages is welcomed, whether for commercial or
> non-commercial purposes, but (2) any such sharing, use, modification, repackaging, or
> publication should explicitly credit SRL as the source of the data to the maximum extent
> possible and (3) any such sharing, use, modification, repackaging, or publication must
> not violate any express restrictions set forth in this Section 5, especially the
> restrictions set forth in subparts 5(i) and 5(j) below.

> we encourage the sharing and reuse of data and statistics our users find on our Site as a
> general matter, but our business obviously would be harmed if a bad apple were to abuse
> that privilege to create a competing statistical database or to copy a materially
> significant portion of our data.

So the settled posture is not what decides this case. The **express permission** decides
it, together with its three conditions. Each condition is checked below.

## The operative lines that restrict

> [you may not] use any material or Content from the Site, including without limitation
> any statistics or data, (i) to create any database, archive, or other data store that
> competes with or constitutes a material substitute for the services or data stores
> offered on the Site or by the Site's Data Providers or (ii) to provide any service that
> competes with or constitutes a material substitute for [them] — **5(i)**

> [you may not] copy or use any material or Content from the Site ... for purposes of
> training, fine-tuning, prompting, or instructing artificial intelligence models or
> technologies in any manner, including without limitation for purposes of (i) generating
> answers, text, scores, statistics, notes, graphics, images, or any other output; or (ii)
> supporting machine learning methods used to predict, classify, label, or score inputs
> into the models — **5(j)**

> without our express written permission, use any automated means to access or use the
> Site, including scripts, bots, scrapers, data miners, or similar software, in a manner
> that adversely impacts site performance or access

And the one line that points the other way, from the "SR and Data Use" page
(https://www.sports-reference.com/data_use.html), which glosses the terms:

> This means that you should not create websites or tools based on data you scrape from
> Sports Reference or any of our sites or use our data to train generative artificial
> intelligence models without our permission.

The same page also says:

> For some of our datasets, our licenses completely preclude any redistribution of the data.

> As an aside, copyright law is clear that facts cannot be copyrighted, so you are free to
> reuse facts found on this site in accordance with copyright laws.

## Provenance: we never read from Sports-Reference

| dataset | where our copy comes from | what nflverse says |
|---|---|---|
| `draft_picks` | nflverse release `draft_picks` | "Draft picks dating back to 1980, courtesy of Pro Football Reference" (release notes); nflreadr `load_draft_picks`: "Loads every draft pick since 1980 courtesy of PFR" |
| `snap_counts` | nflverse release `snap_counts` | nflreadr `load_snap_counts`: "game level snap counts stats provided by Pro Football Reference" |

The nflverse-data repository is licensed CC-BY-4.0 (GitHub license API, `LICENSE.md`).
That licence covers only what nflverse holds rights in, and nflverse states no permission
from Sports-Reference. **nflverse's position is not PFR's.** So the analysis applies
Sports-Reference's terms to these columns as if we had read the data off the site ourselves.

There is a weaker argument available: a party that never used the site may not be bound by
its terms at all. **Nothing here relies on it.**

## Per column

The brief named four columns, and they do not share one answer. The code reads more PFR
columns than those four, so all of them are listed. `analytics/pfr_terms.COLUMNS` is the
machine form of this table, and a test walks `analytics/drafting.py` by AST. It fails on
any PFR column the code reads that this table does not classify. The test is shown firing
on a planted `dr_av` read and a planted `st_snaps` read.

| column | source | used as | what the terms require |
|---|---|---|---|
| `w_av` | draft_picks (SR's own Approximate Value) | **published**, as a team aggregate in the `w_av` slice | credit; no per-pick values; not an AI input |
| `dr_av` | draft_picks | **not read by any outcome** | nothing to decide |
| `games` | draft_picks | read for one printed diagnostic count, **never published** | nothing |
| `offense_snaps`, `defense_snaps` | snap_counts | **published**, summed into `snaps4` per team | credit; no per-pick values; not an AI input |
| `season`, `pick`, `team`, `category` | draft_picks (the draft order; a public fact PFR records) | **published**, as the spine of every slice | credit |
| `pfr_player_id`, `pfr_player_name` | draft_picks | join and check only | nothing |
| `season`, `game_type`, `pfr_player_id` | snap_counts | join and filter only | nothing |

**Something the brief did not list: roster4 and bust are PFR-sourced too.** Their draft
order (who was picked where, by whom) comes from nflverse's copy of PFR's draft table. F07
called roster4 and bust "public record" with "no licensing question". The facts are public;
our copy of them is PFR's. So the credit applies to **every** slice, not only to `w_av` and
`snaps4`, and the file's `attribution.applies_to` lists all four.

## The three conditions, checked

1. **Credit.** The file carries a top-level `attribution` block with a statement, a source,
   a URL, the terms URL and the date the terms were read. Its `applies_to` covers every
   published slice. A file without the block is refused, and so is one whose statement is
   empty. Both refusals are tested.
2. **5(i), no substitute data store.** The file publishes at most 32 team aggregates per
   slice and no per-pick row. A test asserts that every `values.subject` is a franchise,
   and that the file contains no gsis id and no PFR id. Both patterns are shown matching a
   real id before their silence is trusted.
3. **5(j), no AI or ML training.** The scoring record is a walk-forward Pearson r between a
   team's closed-class mean and its next class. No model is trained on anything. **This is
   a reading of 5(j), not a measurement**, and it is recorded in `pfr_terms.JUDGEMENTS`.

## Judgements, not measurements. Reverse any of them in daylight.

- **The data-use page's "should not create websites or tools based on data you scrape" is
  read as a gloss on 5(i) and the automated-access clause.** It is not read as a
  prohibition that overrides section 5's express permission. Three reasons: it introduces
  itself as explaining the quoted excerpt, it sits beside the "facts cannot be copyrighted"
  line, and we do not scrape. It is still the line that most nearly reads as a prohibition,
  and a reader who takes it literally would withhold. **If Sports-Reference is asked and
  says no, every slice comes down, not just two.** The draft order under roster4 and bust
  is PFR's too.
- **5(j) and any future learned model.** A model fitted on AV or on PFR snap counts is a
  different case, and nothing here clears it. The prediction models ruled in scope on
  09-20 should check this before they train on `nfl_snap_counts`, which is PFR-provided.
  That check belongs to track A and is filed there.
- **"Some licenses completely preclude any redistribution"** names no dataset. PFR's pages
  were not checked for a licensed-in marker on draft tables or snap counts, because
  pro-football-reference.com returns HTTP 403 to this machine's client. The terms and
  data-use pages are on sports-reference.com, and both were read in full.

## The result, per outcome (published under these terms)

Classes 2002-2022 (snaps4 2013-2022, w_av 2002-2021). Pulls `draft_picks@2026-09-09`,
`roster_weekly@2026-09-09`, `snap_counts` (see `sources`), `games@2026-09-22`.
2,000 permutations.

| slice | reading | separation | record | confound (win share r) |
|---|---|---|---|---|
| roster4 | `does_not_separate` | p 0.720 | no better than chance, −0.057 [−0.114, +0.016], 15 | +0.18 [−0.18, +0.50] |
| bust | `does_not_separate` | p 0.298 | no better than chance, −0.007 [−0.106, +0.083], 15 | +0.27 [−0.09, +0.56] |
| snaps4 | `not_readable` | p 0.141 | not readable, +0.139 [+0.016, +0.247], 4 | +0.17 [−0.19, +0.49] |
| w_av | `separates_confounded` | **p 0.003** | none (no as-of version) | **+0.82 [+0.65, +0.91]** |

The four outcomes get **three words, not four**. roster4 and bust carry the same verdict.
The brief's "four different verdicts" overcounts them.

**w_av separates, and a reader will take it for the finding.** So its sentence in the file
is generated to carry the confound and the missing forecast in the same sentence:

> Career Approximate Value over the slot: teams separate from shuffled labels (p 0.003),
> but not on drafting - team values correlate with regular-season win share at r +0.82
> [+0.65, +0.91], and Approximate Value is allocated from team points, so a winning team's
> picks accrue it for the winning; and there is no forecast to score: the measure is a
> total as of the pull date, with no version a forecast could have seen.

Its `bands` are null with `bands_reason: confounded`. A band order there would rank teams
by winning and label it drafting. `separates_confounded` requires **both** a named
mechanism and a confound interval that excludes zero. A correlation with winning alone is
not enough, since real drafting skill would produce one. The test suite drives the same
pipeline to `separates` with bands, to show that answer is reachable.

## The open editorial question, left open

Whether the site shows per-team values when nothing separates is Ethan's decision.
`build(..., values_when_not_separating=)` takes `show` or `hide` **and has no default**.
`--write` refuses without it, and `--check` validates both. The file records the choice in
`values_policy`. Under `hide`, every slice keeps its reading, statement, separation, record
and confound, and drops only its 32 values, with `values_reason` saying why. Switching is a
re-export, not a rebuild. Both files validate.

My recommendation is `show`, for this reason. What this predictor has to say is that
nothing separates. Thirty-two alphabetical, overlapping intervals under a sentence that
says so let a reader check that claim. Under `hide`, the reader has to trust a sentence.
The case against: any list of 32 team numbers gets read as a ranking, however it is sorted.
If Ethan weighs that more heavily, `hide` costs one flag.
