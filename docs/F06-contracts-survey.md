# F06 — contracts: the data is good, the licence is the blocker

TRACK F · cs-analytics · `C:\Users\Ethan Davis\code\cs-analytics`

    python -m analytics.contracts_survey
    python -m analytics.contracts_survey --upstream

Survey, not a build. Measured 2026-09-19, **corrected and extended
2026-09-21** with the six planned features now named.

> ### Correction, 2026-09-21 — three counts in the first version were inflated 6.19x
>
> **The table is one row per (player, CONTRACT), and every row carries that
> player's ENTIRE nested history, byte-identical.** The player with 38 contract
> rows carries the same 8-year `season_history` 38 times. The first version of
> this survey exploded from the flat frame, so three figures below were
> multiplied by each player's own row count:
>
> | first published | correct |
> |---|---|
> | 47,286 `"Total"` rows | **8,955** |
> | 265,915 real-year rows | **42,970** |
> | 98.6% Total-equals-sum | **99.88%** (8,944 of 8,955) |
>
> Nothing else moved: shape, depth, identity, the join-by-era table, the gap
> list and the licensing all count rows or distinct values rather than
> explosions, and were right.
>
> **This is a third double-count nested inside the one the survey was written
> to catch, and it is the worse of the two.** The `"Total"` row multiplies by a
> constant 2; this multiplies by a per-player variable — median 2, max 38 — so
> no sanity check on magnitude catches it consistently, and **a ratio of two
> figures computed the same wrong way comes out right**, which is why the 98.6%
> agreement looked fine. Combined, a naive explode-and-sum reports
> **$1,054,673m of cap against a true $112,992m — 9.33x**.
>
> `contracts_survey.per_player()` is the fix; every nested accessor goes
> through it, and a test asserts by AST that any function exploding a nested
> column calls it first.

---

## Verdict first

**Of the six planned features, two are supportable, one in a reduced form, and
three are not. And OverTheCap's terms, read rather than assumed, prohibit
exactly what serving any of them on a public site would be.**

| # | feature | verdict |
|---|---|---|
| 1 | salary-related | **yes**, 2015+ |
| 2 | drafting strengths | **yes** — and it needs no contracts data at all |
| 3 | contract ROI | **reduced** — cost yes, attribution to a signing no |
| 4 | cap-space analysis | **no** — team totals miss the cap by a median 18% |
| 5 | contention window | **no** — dead money, void years, guarantee structure all absent |
| 6 | championship team archetype | **no** — 0–5% coverage in the eras it compares |

The table is richer than expected and covers 2015 onward completely. **The
licence is the binding constraint, not the data** — which is the opposite of
what I expected going in, and it means even the two green rows are blocked for
public display unless someone obtains written consent.

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

### A per-player duplication of both nested columns

Measured 2026-09-21, and it is the trap that caught this survey's own first
version — see the correction at the top.

| | naive explode | correct | inflation |
|---|---|---|---|
| `season_history` rows | 318,666 | 55,853 | **5.71x** |
| `contract_history` rows | 462,915 | 54,762 | **8.45x** |
| total `cap_number` incl. `"Total"` | $1,054,673m | $112,992m | **9.33x** |

Rows per player: min 1, **median 2, max 38**. Deduplicating is safe because the
copies are not different slices of one history — they are the same history
repeated, verified byte-identical on the busiest player's 38 rows.

### A silent zero

`year_signed` is **0 on 1,106 rows** rather than null. `year_signed >= 2010`
drops them silently; a decade grouping files them under 1980. The survey reports
them as their own bucket for that reason.

---

### One poison value, found and negligible

`cap_number` carries **2147.483647** — `INT32_MAX` divided by a million — on
**3 rows, one player** (Sheldon Brown, 2009). Not null, not zero, and it reads
as a $2.147 billion cap hit. Reported at its true size rather than dressed up:
three rows of 42,970 is a curiosity, not a finding. It is named because the
*class* is the dangerous one — a sentinel that survives every null and zero
check — and because a filter on plausible cap values is one line.

---

## 3a. What DOES reconcile, which matters for the mapping

**Forward cap treatment is complete and internally consistent.** `cap_number`
equals the sum of its seven components — `base_salary`, `prorated_bonus`,
`option_bonus`, `roster_bonus`, `workout_bonus`, `per_game_roster_bonus`,
`other_bonus` — on **100% of 265,915 season rows within $1,000, and 97.9% to
the dollar**, median absolute difference 1.1e-16.

So the **cap treatment of signing bonuses is present going forward**: the
annual proration is `prorated_bonus` and it adds up. What is absent is the
*backward* half — the unamortised remainder at release and the acceleration
rule — which is dead money, and §4 says why it is not derivable.

Note there is **no `signing_bonus` field**: the per-year proration exists, the
bonus itself does not, so the amount and its amortisation length have to be
inferred from a constant proration across years.

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

The six, as named in the brief. **Two are supportable, one is supportable in a
reduced form, three are not.**

| # | feature | verdict | what decides it |
|---|---|---|---|
| 1 | **salary-related** | **YES**, 2015+ | `value`, `apy`, `guaranteed`, `cap_number`, `cash_paid` all present and reconciling |
| 2 | **drafting strengths** | **YES**, and it needs no contracts join | `draft_round` / `draft_overall` / `draft_team` are here, and `draft_picks` already carries them |
| 3 | **contract ROI** | **REDUCED** — cost yes, attribution no | cost per season is exact; **a season cannot be attributed to a contract** |
| 4 | **cap-space analysis** | **NO** | team totals miss the cap by a median 18%, and the gap *is* dead money |
| 5 | **contention window** | **NO** | needs forward commitments net of dead money and void years; both absent |
| 6 | **championship team archetype** | **NO** | needs cap *allocation* by unit across eras; pre-2015 coverage is 0–5% |

#### 1. Salary-related — supportable, 2015 onward

Everything a salary view needs is present and checks out: contract `value`,
`apy`, `guaranteed`, and per-season `cap_number` / `cash_paid` that reconcile to
their components on 100% of rows within $1,000 (§3a).

The binding constraint is **era, not fields**: 0% of pre-2000 spine players
join, 5% for 2005–09, 99% from 2015. A salary page is a 2015+ page or it is
silently empty for the players it cannot cover.

#### 2. Drafting strengths — supportable, and cheapest of the six

`draft_year`, `draft_round`, `draft_overall` and `draft_team` are on every row.
**This one does not need the contracts table at all** — `draft_picks` already
carries draft capital, with no licensing question attached (§5). If only one of
the six is built, this is the one that costs nothing and risks nothing.

What contracts would *add* is the second-contract outcome — did the pick earn an
extension — and that half inherits the 2015 cliff and the licence.

#### 3. Contract ROI — reduced, and the reason is structural

Cost is exact. **Attribution is not**, and this was not in the original gap
list because it is not a missing column — it is a missing *key*:

- `season_history` carries `year` and `team` and **no contract identifier**.
- `contract_history` carries `year_signed` and `yrs` and **no identifier
  either**.

So a season's cap number cannot be attributed to the contract that produced it,
except by inferring from year ranges — and that inference breaks exactly where
ROI is interesting: a player re-signed, extended or traded mid-window has
overlapping contracts and no way to say which season belongs to which.
`Renegotiated` appears on 43,867 contract rows, so the ambiguous case is the
common one.

**What survives:** ROI as *player*-level cost against player-level production —
"this player cost $X across these seasons and produced Y" — which is a real
feature. **What does not:** ROI attributed to a *signing decision*, which is
what the phrase usually means.

#### 4. Cap-space analysis — not supportable, and measured

Summing 2025 `cap_number` by team gives all 32 teams and the totals are wrong:

| | |
|---|---|
| actual 2025 cap | **~$279.2m** per team |
| median team total from this table | **$229.3m** |
| range | **$157.1m (Jets) to $270.9m (Bears)** |

A **median 18% shortfall, varying 40 points across teams.** The missing
component is exactly what §4 says is absent — dead money, plus practice-squad
and IR treatment and the top-51 rule. Cap space is a residual, so a feature
computing it from these totals would report a team's remaining room wrong by an
amount that is itself the interesting number.

This is the sharpest no of the six: the data looks complete — 32 teams, every
player — and the totals are individually correct. Only the *sum* is wrong, and
only against a figure this table does not contain.

#### 5. Contention window — not supportable

A contention window is forward commitment: what is owed in each of the next
three or four years, and how much of it is escapable. That needs three things
the table does not have.

- **Dead money** — what releasing a player actually costs. Absent, and not
  derivable: it needs the unamortised proration at release and the guarantee
  split that decides what accelerates.
- **Void years** — years that exist only to spread proration. There is no field.
  They are *sometimes* visible as a year with `base_salary` 0 and
  `prorated_bonus` > 0, but that shape occurs on only **136 season rows across
  19 players**, which is far too few to be the real population — so the signal
  exists and is not reliable enough to build on.
- **Guarantee structure** — `guarantees` and `guaranteed_salary` are totals. No
  split of full versus injury versus skill, and no vesting dates, so "how much
  of year three is already locked" is unanswerable.

#### 6. Championship team archetype — not supportable

This one fails on era before it fails on fields. An archetype claim compares cap
allocation by unit across champions, which means the 2000s and 1990s — and
coverage there is **0% to 5%** of spine players. The seasons with the teams
worth comparing are the seasons the table does not have.

Even restricted to 2015+, it would need allocation by unit net of dead money,
which is (4) again.

### Two things the mapping needs that are not in this table

Worth stating because they will otherwise be discovered during a build:

- **Position grouping.** `position` here is OTC's vocabulary (`IDL`, `ED`,
  `RG`), not nflverse's. Any by-unit rollup needs a mapping, and it is not
  written yet.
- **Team identity.** `team` is a nickname string — `Bears`, not `CHI` — with 36
  distinct values including one empty. A team join needs a name map.

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
