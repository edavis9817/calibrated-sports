# W07 — parallel tracks and operating rules

How Calibrated Sports development runs from here. Four tracks, three shared files, one owner each.

---

## Why it has been slow

Parallelism is the smaller half of the answer. The larger half:

1. **Stops for decisions that had obvious defaults.** Roughly eight halts in one day, several on calls
   where the agent had already stated a recommendation and was waiting only for assent.
2. **Round trips through chat.** Report → paste → brief → paste. Two human copy-pastes per iteration,
   and much of the brief restated rules already settled.
3. **Rules living in chat rather than in the repo.** Re-derived every round instead of applied.
4. **Briefs living only in chat.** `brief-W03` was cited as a repo path and did not exist there, costing
   a full round.
5. **One agent, one thread, everything serial** — including work with no dependency between the parts.

Fixes 1–4 are free and land before any parallel track starts. Fix 5 is this document.

---

## The autonomy rule

**Decide unless it is irreversible.**

Proceed on any reversible call. Record what was chosen and why in the report under *decisions taken*.
Stop only for:

- Anything irreversible — deleting data, rewriting pushed history, dropping a column with rows in it
- Anything that publishes outward — a public repo commit containing new kinds of content, a deploy that
  changes a published figure
- Anything that changes a number already published on the site or in the research record
- A conflict between an instruction and an earlier decision of Ethan's (quote both, do not pick)
- A finding that invalidates the task itself

Everything else: choose the option you would have recommended, build it, report it. A decision reported
after the fact costs one paragraph. A decision asked before the fact costs a full round trip.

---

## The four tracks

### A — Producer / NFL data  (`calibrated-sports`)

§3 in order: settlement fix + schedule join → components table → custom scoring → dirty set →
prop-history export. Then 021 re-scoring.

**Owns `contract.schema.json`.** No other track edits it.

### B — Consumer / web  (`calibratedsports-web`)

Pages, charts, the design pass against `site-design-direction.md`. Consumes the vendored contract as it
stands; regenerates `lib/schema.generated.ts` from it.

**Never edits the contract.** If a page needs a field that does not exist, it files a request to track A
and builds the rest.

### C — CFB ingest  (`calibrated-sports`, second clone)

Ingest, normalize, export. New files only: `jobs/ingest_cfb.py`, CFB normalizers, CFB tables, a CFB
sport manifest.

**Scope is ingest and export only. No pages.** CFB pages wait for the NFL design pass, or you build
the same under-designed pages twice and redo both.

**Does not edit shared NFL code paths, and does not edit the contract.** A new sport adds a manifest;
it should not require a schema change. If it does, that is a finding worth reporting — it means the
contract is not as sport-agnostic as it claims, and that is better learned now than at sport five.

### F — Analytics  (`calibrated-sports`, third clone)

Play-by-play processing into league analytics. nflverse PBP 1999–2026 is archived on disk and has
never been read; it is the largest untapped asset in the project.

**Own database: `D:\calibrated-sports\data\analytics.db`.** Never writes to `market_log.db` — read it
only with `mode=ro`. Same reasoning as CFB: bulk writes must never contend with the live logger.

**Scope is processing and export only. No pages.** Does not edit `jobs/export_web.py`,
`contract.schema.json`, or any shared NFL code path. New files only.

**The structural rule, enforced as a failing test:** no analytic is published without an interval and
a sample count. Every stats site publishes point estimates; publishing the uncertainty is what makes
these ours. A table or export carrying a rate without its `n` and its interval fails the gate, the
same way CFB's guard fails on a hit-rate-shaped table.

First pass is PBP only. Next Gen Stats is free and static but is the most widely discussed advanced
data there is; it lands after PBP, once we know what PBP is missing.

### E — Design  (Claude Design, not a repo)

Ethan iterating on look and composition in a separate Claude Design conversation. Fully parallel — it
touches no code and cannot conflict.

Three constraints, all of them learned the expensive way:

- **Mock every artboard with the real magnitudes**, not invented density. 3,970 players, 7,292 games,
  8 seasons, 18 stat columns, 11 markets, 83 ladder rungs, prop history at 11-of-16. A design approved
  against 1,284 markets is a design that ships empty.
- **Target what §3 will fill**, not what just shipped: prop performance, stats leaderboards, the
  fantasy scoring editor, sport home. Redesigning built pages means building them twice.
- **Carry the register assignment** from `site-design-direction.md`. Dense reference or editorial, per
  page, chosen before any composition.

Output lands in `design/` **in the web repo**, committed — not in the public repo, where it is
currently gitignored and therefore untracked and unbacked. Track B reads it there.

### D — Ops

At-boot task, Task Scheduler operational log, retention hold, source-health monitoring. Mostly Ethan's
elevated shell. Not an agent track, but it blocks the public record, so it goes first.

---

## Shared files and their owners

| File | Owner | Everyone else |
|---|---|---|
| `contract.schema.json` | Track A | Proposes; never edits |
| `lib/schema.generated.ts` | Track B | Never touches |
| `CLAUDE.md` (both repos) | Whoever adds a rule | Append only, never rewrite |
| R2 bucket | All | Disjoint sport prefixes; confirm before first CFB write |

A and C both live in `calibrated-sports`. **Use a second clone at a short path** — `C:\cs-cfb` — not a
git worktree; worktrees already hit the Windows path limit on this repo. Both push to the same remote,
commit often, pull before starting. Conflicts only arise if they touch the same file, which the scope
rules above prevent by construction.

---

## What stays serial

- Contract changes. One at a time, track A, and track B regenerates after.
- Deploys. Data leads code, always.
- The design pass. It waits for §3, per `site-design-direction.md`.

---

## CFB — the honest risk, and why it is still the right test

It is the hardest of the five sports: ~130 FBS teams, heavy roster churn, thinner data quality than
nflverse, and no equivalent of the snap-count spine.

That is also the argument for it. A contract that survives CFB survives NBA, MLB and NHL trivially;
one validated only against NFL proves almost nothing about being multi-sport. Doing the hard one second
means the abstractions get tested while there are two consumers rather than five.

Expect the sport manifest to need fields NFL never exercised — divisions and conferences, a variable
game count, no bye structure, roster churn between seasons. Each of those is a finding about the
contract, not a CFB problem.

---

## Reporting, all tracks

**Every report opens with its track letter, its repo and its working path**, on one line, before
anything else:

```
TRACK A · calibrated-sports · C:\Users\Ethan Davis\code\calibrated-sports
```

Three tracks reporting into one conversation is unreadable without it, and a finding attributed to the
wrong repo sends the next instruction to the wrong place.

**Every item inside a report that touches another track is labelled with that track**, so it can be
routed rather than re-diagnosed. An item that belongs to another track is reported, not fixed.

Every report ends with: what was as described and what was not; decisions taken and why; what the real
data cannot support, flagged rather than faked; what changed that was not asked for; what belongs to
another track; and anything genuinely irreversible awaiting Ethan.

No report asks a question without also stating the answer it would have chosen.