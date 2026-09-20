# F06 — contracts: the data is good, the licence is the blocker

TRACK F · cs-analytics · `C:\Users\Ethan Davis\code\cs-analytics`

    python -m analytics.contracts_survey
    python -m analytics.contracts_survey --upstream

Survey, not a build. Measured 2026-09-19 against the archived pull and against
today's upstream copy.

---

## Verdict first

**The table exists, is richer than expected, and covers 2015 onward completely.
It cannot answer dead money, void years, restructure economics or guarantee
structure. And OverTheCap's terms, read rather than assumed, prohibit exactly
what serving it on a public site would be.**

The licence is the binding constraint, not the data. That is the opposite of
what I expected going in, and it means the gap analysis below is mostly
academic unless someone obtains written consent.

---

## 1. Does nflreadr carry it?

Yes. `contracts/historical_contracts.parquet` — **not** `contracts.parquet`,
which 404s. The nflreadr docs state it "Loads player contracts from
OverTheCap.com".

| | |
|---|---|
| Rows | **52,751** archived (52,862 upstream today) |
| Columns | **26**, two of them nested |
| Players | **12,883** by `otc_id`, 11,076 by `gsis_id` |
| Active contracts | 2,469 |

Two nested columns carry most of the value:

- **`season_history`** — per year: `base_salary`, `prorated_bonus`,
  `option_bonus`, `roster_bonus`, `guaranteed_salary`, **`cap_number`**,
  `cap_percent`, `cash_paid`, `workout_bonus`, `per_game_roster_bonus`,
  `other_bonus`.
- **`contract_history`** — per contract: `team`, `contract_type`, `status`,
  `year_signed`, `yrs`, `total`, `apy`, `guarantees`, `amount_earned`,
  `percent_earned`, `effective_apy`.

So **cap hits per year and cash paid per year are present**, which is more than
the plan assumed. Draft capital is present too (`draft_year`, `draft_round`,
`draft_overall`, `draft_team`).

### History depth

| signing decade | rows |
|---|---|
| **unknown (`year_signed` = 0)** | **1,106** |
| 1980s | 14 |
| 1990s | 166 |
| 2000s | 1,372 |
| 2010s | 18,929 |
| 2020s | 31,164 |

Nominally 1980–2026. Practically it is a 2010s-onward table, and see §2 for what
that means at the join.

### Refresh cadence

The registry classifies `contracts` as **OFFSEASON** tier — "DOES NOT REFRESH
IN-SEASON". **That is wrong, measured.** The upstream asset's `last-modified` is
**today, 2026-09-19 11:53 GMT**, and the content hash differs from the archived
2026-09-09 copy: **+111 rows, active contracts 2,469 → 2,455 over ten days**, in
season.

The content check matters and is not ceremony — CLAUDE.md already records that
upstream re-uploads make `updated_at` meaningless on its own, so a moved
`last-modified` proves nothing until the bytes are compared.

**I cannot state a cadence.** One interval is not a cadence, and the archive
holds exactly one pull. What is established is that it *does* change in season,
which is enough to say the tier is misclassified. *Routed to track A* — the tier
drives whether `ingest_nflverse --tier live` picks it up, and today it does not.

---

## 2. The join, which is the part that decides everything

`gsis_id` is present on **93.3%** of rows. That headline is misleading and the
era split is the real answer.

**Spine (players in `f_play_usage`) that have a contract row:**

| last seen | with contract | without | covered |
|---|---:|---:|---:|
| 1995–1999 | 0 | 138 | **0%** |
| 2000–2004 | 9 | 609 | **1%** |
| 2005–2009 | 30 | 583 | **5%** |
| 2010–2014 | 405 | 293 | 58% |
| 2015–2019 | 707 | 7 | **99%** |
| 2020–2024 | 726 | 17 | **98%** |
| 2025–2026 | 653 | 0 | **100%** |

**This is a 2015+ table.** A flat "2,530 of 4,177 spine players join" (60.6%)
hides a cliff as sharp as any in `F01`. Any contracts feature is a 2015-onward
feature, or it is silently empty for the players it cannot cover — the same
defect shape as the 2003–08 targets.

### Identity, since track A flagged it as not incidental

| | |
|---|---|
| Players with **no `gsis_id` at all** | **1,807 of 12,883 (14%)** |
| `gsis_id` values **not in `players.parquet`** | **1,661 of 11,076 (15%)** |
| Active contracts with a `gsis_id` | 2,447 of 2,469 (**99.1%**) |

The second row is the one for track A: **15% of the contracts table's own
`gsis_id` values do not resolve in nflverse's own crosswalk.** These are not
players missing an id — they are ids that point at nobody. That is the
fragility track A found, quantified from a second direction.

The active-player picture is much better (99.1%), so a *current-roster* feature
joins cleanly. A *historical* one does not.

---

## 3. Two traps, both already-tabled classes

### A silent double, inside a nested column

`season_history` contains a row whose `year` is the **string `"Total"`**:

| | |
|---|---|
| `"Total"` rows | **47,286** |
| real-year rows | 265,915 |
| null-year rows | 5,465 |
| players where Total == sum of years | **8,834 of 8,955 (98.6%)** |

Explode `season_history` and sum `cap_number` without excluding it and **every
cap figure doubles.** Same class as the NGS week-0 total, one level less visible
because it lives inside a list-of-struct rather than as its own row. The
reconciliation — does the part sum to the whole, or *is* the whole sitting among
the parts — is what catches it, exactly as it did for NGS.

`year` is also a **String**, not an integer, and mixes years, `"Total"` and
nulls. Any numeric use has to cast, and casting without filtering turns
`"Total"` into a null rather than an error.

### A silent zero

`year_signed` is **0 on 1,106 rows** rather than null. `year_signed >= 2010`
drops them silently; a decade grouping files them under 1980. The survey reports
them as their own bucket for that reason.

---

## 4. What it cannot answer

| needed | in the table |
|---|---|
| **dead money** | **ABSENT** — no field, at any level |
| **void years** | **ABSENT** |
| **restructures** | **ABSENT** as economics — see below |
| incentives / escalators | **ABSENT** |
| guarantee *structure* (injury vs full, vesting) | **ABSENT** — only totals |
| cap hit per year | `cap_number` ✓ |
| cash paid per year | `cash_paid` ✓ |
| guarantee totals | `guaranteed`, `guarantees`, `guaranteed_salary` ✓ |
| draft capital | `draft_round`, `draft_overall`, `draft_team`, `draft_year` ✓ |

**Restructures are half-present and the half that exists is the wrong half.**
`contract_history.status` carries `Renegotiated` (43,867) and `Extended`
(2,343), so you can tell **that** a contract was renegotiated. There is no field
for what it did to the cap — no converted-salary amount, no new proration, no
resulting dead-money change. The event is recorded; the economics are not.

**Dead money is the sharpest absence.** It is not derivable from what is here:
computing it needs remaining prorated bonus by year plus guarantee vesting, and
`prorated_bonus` is present per year while the *unamortised remainder at
release* is not, and the guarantee split that determines what accelerates is
not either.

### Which of the six features each gap blocks

**I cannot answer this as asked, and will not invent it.** The stats plan is not
in this repo — `grep` for salary, cap hit, dead money, draft capital across all
markdown and Python returns nothing, and there is no document naming six
features. Naming them from inference is exactly the hand-written-claim failure
one level up.

What I can say, and what the mapping will be once the six are named:

| capability | supportable? |
|---|---|
| Salary / APY / contract value, any era 2015+ | **yes** |
| Cap hit and cash paid per year, 2015+ | **yes** |
| Draft capital | **yes**, and it needs no contracts join at all — `draft_picks` already covers it |
| Cap-space or roster-construction context | **partial** — per-player cap numbers exist, team totals would have to be summed and the "Total" trap makes that a live hazard |
| Anything dead-money | **no** |
| Anything about restructure economics, void years, or guarantee structure | **no** |
| Any of the above before 2015 | **no**, on join coverage alone |

Give me the six and I will complete this table in one pass; the measurements are
all here.

---

## 5. Licensing — and this is the blocker

**Reported from the terms, fetched 2026-09-19, not from what seems likely.**

### What nflverse says

The `nflverse-data` repository is licensed **CC-BY-4.0**. The nflreadr
documentation for `load_contracts()` states it "Loads player contracts from
OverTheCap.com" and directs data issues to `github.com/nflverse/rotc`. **Neither
the data repository, the function documentation, nor the `rotc` scraper
repository states any permission, consent or agreement with OverTheCap.**

### What OverTheCap's terms say

From `overthecap.com/terms-and-conditions`, quoted:

> "OTC owns this data, content, graphics, forms and any material on this website
> as well as the way the information is arranged and presented"

> "You are permitted to download and/or print one copy of the materials of this
> website for personal use provided you include a complete copy of the entire
> page."

> "Any other use, including for any commercial purposes, is strictly prohibited
> without our express prior written consent."

> "You may not create derivative works, nor distribute, sell, or publish in full
> any content from OTC."

> "Any systematic retrieval, by manual, automated or other means, of data or
> other content from the Services whether to create or compile any database,
> collection, compilation, directly or otherwise is prohibited without our
> express written consent."

> "The use of data scraping software to capture any content for commercial use
> is strictly prohibited"

and a prohibition on any process to copy the data onto another site that
"mirrors the presentation of data provided on OTC".

### The reading

**This is not unclear.** The terms prohibit systematic automated retrieval to
compile a database, derivative works, redistribution, and publication — which
describes the pipeline that produces this file, and describes serving it on
calibratedsports.com more directly still.

**What *is* unclear is one thing only: whether nflverse holds express written
consent from OTC.** A CC-BY-4.0 grant is only valid from someone with the right
to grant it, and no nflverse repository states such consent exists. I found no
evidence either way; the absence is not proof of absence, and it is also not
something to build on.

Two distinctions worth keeping separate, because they are being conflated:

1. **nflverse redistributing to analysts** and **serving it on a public
   website** are different acts under these terms, and the second is the one the
   terms address most directly ("mirrors the presentation of data").
2. **The CC-BY licence on the repository** covers nflverse's own arrangement and
   code. It does not, on its face, transfer rights nflverse may not hold in the
   underlying data.

### Recommendation

**Do not build contracts features for public display against this source.** The
options, in the order I would take them:

- **Ask OTC.** They sell an API and have an "Advertise With OTC" page, so there
  is a commercial channel and a licensed feed is likely purchasable. One email
  settles the only genuinely unclear point.
- **Use it privately.** Nothing here prevents contracts data informing
  `edge/` — the same rule the project already applies to PFF data, which
  CLAUDE.md says "never leaves `edge/`" for precisely this reason. That
  precedent covers this case exactly and was set before the question arose.
- **Do not** rely on "nflverse publishes it, so it must be fine." That is the
  reasoning the project has a rule against: a claim nobody checked, which
  happened to have a second argument next to it.

This is the same finding shape as `F03`: the data is real, it is good, and it
does not support the thing it was wanted for. Better now than after six
features were designed against it.
