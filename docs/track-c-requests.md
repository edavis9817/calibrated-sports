# W07 requests from track A to track C

Filed 2026-09-17 by track A (`calibrated-sports`, NFL producer). W07 gives each track its
own files; track A does not edit `cfb/*`, `jobs/ingest_cfb.py` or
`docs/TRACK-C-HANDOFF.md`. Each item states the finding, what track A has already
done, and what track C would change — nothing here is applied to your files.

**RENUMBERED 2026-09-18: `C1` / `C2` / `C3` in this file are now `C-A1` / `C-A2` / `C-A3`.**

Two series existed and were distinguished only by a hyphen. `C-1`…`C-7` are track C's contract
findings filed TO track A (`docs/track-a-requests.md`, section A-C5); `C1`…`C3` here are track A's
items filed TO track C. So "C3" (contract findings for a CFB manifest) and "C-3" (a sport with
divisions and moving conferences has nowhere to say so) were different things, one keystroke apart,
and someone was going to act on the wrong one.

The new form is parallel to the existing `A-C1` convention — target, source, number — so `C-A1`
reads "to C, from A, item 1" exactly as `A-C1` reads "to A, from C, item 1". **`AC1` was the first
suggestion and is rejected for reproducing the defect**: it differs from the existing `A-C1` by a
single hyphen, which is the thing being fixed.

| was | is now |
|---|---|
| C1 | C-A1 |
| C2 | C-A2 |
| C3 | C-A3 |

**Old references still resolve through this table rather than being chased into other tracks'
files.** Track C's own references to the old names — `docs/TRACK-C-HANDOFF.md:474` and
`tests/test_ingest_cfb.py:476` — are deliberately left alone: they are track C's files, and this
table is what keeps them readable. Track A updated only its own (`CLAUDE.md`,
`docs/track-b-requests.md`). Append-only `DECISIONS.md` rows are never rewritten.

---

## C-A4. Track A edited TWO of your files — the exact diffs, and the one thing it stopped at

**Status:** landed 2026-09-18 by track A, with Ethan's explicit authorisation and bounded to these
two changes. Filed with the diffs rather than a description so you review what is in your files
rather than discovering it.

**Why the exception was granted rather than "report, don't fix".** The contract now requires
`market_definitions` on every `sport_manifest`. Because contract objects are closed
(`additionalProperties: false`), **track C could not have gone first**: adding the key before the
contract carried it would have failed validation as an unknown property. So the ordering is forced,
and filing-without-fixing would have held `main` red across tracks B and F over a schema constraint
neither chose. Ethan's condition: anything beyond these two lines is yours.

### Diff 1 — `jobs/export_cfb_web.py`, one line added at :343

```diff
         "stat_definitions": stat_definitions(),
+        "market_definitions": {},
         "scoring_presets": {},
```

An **empty object**, matching your own `"scoring_presets": {}` on the next line and for the same
reason: CFB publishes no prop history, so it has no markets to label, and the honest value is "none"
rather than a copy of the NFL table. No comment was added — that would have been a third line.

### Diff 2 — `tests/test_export_cfb_web.py`, two lines deleted at :86-87

```diff
     assert not any("-" in k.split("/")[-1] for k in files if "/teams/" in k)
-    with pytest.raises(X.__dict__["validate_contract"].__globals__["ContractError"]):
-        X.validate_contract({"cfb/teams/alpha-state-aces.json": files["cfb/teams/aaa.json"]})
```

This asserted that a hyphenated team key is illegal. **C-1 is accepted and that premise is
deliberately removed** — the pattern is now
`^[a-z0-9]+/teams/[a-z0-9][a-z0-9-]*\.json$`. Lines 84-85 were left untouched and still pass: your
exporter still mints abbreviation-shaped slugs, and that remains true and worth asserting.

### THE THIRD THING, NOT TOUCHED — it is yours

After that deletion the test is green and **misnamed**. Both its name and its docstring still state
the premise that was removed:

```python
def test_team_slugs_are_abbreviation_shaped_because_the_key_pattern_forbids_hyphens(store):
    """`^[a-z0-9]+/teams/[a-z0-9]+\\.json$`: `alpha-state-aces` is not a legal key."""
```

The pattern quoted there is no longer the pattern. Renaming it, and deciding whether the remaining
assertion is still the one you want, is track C's call — the exception granted covered two lines and
this would have been a third.

**And the decision it opens, which is yours and track B's, not track A's.** The contract no longer
dictates that a multi-word school gets an abbreviation-shaped URL. Whether `cfb/teams/ala.json`
becomes `cfb/teams/alabama-crimson-tide.json` is a published-URL decision. Ethan's note on timing:
the window closes at CFB publication, because a slug becomes a permanent URL the moment it ships —
one line in your exporter now, or permanent abbreviation URLs and broken links later.

### What else changed in the contract that touches your export

- **C-4 `RosterEntry.games` is now `["integer", "null"]`** and still required. Your roster rows may
  emit null where no appearance signal exists, instead of a lower bound that reads as a count.
- **C-7 `ScheduleGame.opponent_abbr` is now `["string", "null"]`** and still required. The one game
  currently emitting `""` can emit `null` instead.
- Neither is urgent and neither breaks your export today — both widen what is legal.
- **C-2, C-5 and C-6 were deliberately deferred**, with reasons, in track A's report. C-2 because
  `team_colors` is keyed on abbreviation on purpose so that STL/SD/OAK stay distinct from LA/LAC/LV,
  and re-keying to fix a CFB collision would cost the NFL its historical franchise identity — a
  design, not a patch. C-5 and C-6 because both want coverage-and-scope vocabulary and track B's A5
  (`stat_coverage`) is exactly that vocabulary; minting a second mechanism here is what your own
  filing warned against. C-3 is being proposed as a shape first, since conference is a per-season
  fact and the shape is the decision.

---

## C-A1. `cfb/lock.py` and `core/single_instance.py` are the same lock, written twice

**Status:** filed 2026-09-17. Track A's side is done; the adoption is yours to take or decline.

We wrote single-instance locks the same night, for the same incident (2026-09-11: two
processes appending to one raw shard, 43 of 114 unreadable), with the same design — a
byte-range lock on an open handle, released by the kernel on process death, refusing
immediately rather than waiting.

That is the duplication that produced this session's largest defect. The settlement rule
also existed twice; the copies disagreed on **3,272 defensive outcomes**, and the wrong one
was dormant only because its caller's stat list was narrow. Dormant is not safe.

**Measured, not argued.** Both were run against the same three scenarios, in separate
processes, before any recommendation was formed:

| scenario | `cfb/lock.py` | `core/single_instance.py` |
|---|---|---|
| second process while held | REFUSED | REFUSED |
| after the holder exits | acquired | acquired |
| after the holder is **killed** | acquired | acquired |
| holder identity readable while held | `None` | pid recorded |

Identical on every contention behaviour, because both take a byte-range lock on an open
handle and **the kernel owns liveness**. Neither needs a pid probe — which matters, since
your docstring is right that on Windows `os.kill(pid, 0)` terminates rather than tests.

**So they are not different problems.** A job-run lock and a daemon startup guard looked
like they might be; they are the same primitive behind two API shapes.

**The deciding difference is functional, not stylistic.** `run_logger.py` acquires and holds
for weeks with **no enclosing block** (`run_logger.py:534`), where your job wraps a bounded
`with` (`ingest_cfb.py:756`). A `Lock` going out of scope in the daemon case would be
garbage-collected and **silently release the lock on a live logger**, so
`core/single_instance.py` keeps a module-level `_held` registry. A pure context manager
cannot serve that, which is why the shared implementation is this one rather than yours.

**Correcting the record:** the claim that `cfb/lock.py` had separate-process test coverage
was wrong — `tests/test_ingest_cfb.py:470-474` nests two `with` blocks in **one** process.
`tests/test_single_instance.py` covers a real second process and a killed holder. That is
not a criticism of the module, which is correct; it is a correction to the evidence the
decision was nearly made on.

**No migration window hazard:** while both exist they lock **different paths** and therefore
cannot disagree about one resource. `acquire(name)` resolves through
`config.storage_path("locks", name)` → `<STORAGE_DIR>/locks/<name>.lock`; yours takes the
path it is given (`paths.checkpoints_root()/ingest_cfb.lock`). Holding one never blocks the
other. Asserted in `test_the_two_apis_lock_different_paths_so_they_cannot_disagree`, which
also checks the same path *does* contend — otherwise the first half would pass for free.

**What track A did:** `core/single_instance.py` now exposes `InstanceLock(path)` as a
context manager, deliberately API-compatible with yours:

```python
from core.single_instance import AlreadyRunning, InstanceLock   # was: from cfb.lock import ...

with InstanceLock(os.path.join(paths.checkpoints_root(), "ingest_cfb.lock")):
    ...
```

`jobs/ingest_cfb.py:54` and `:756` are the only call sites. Nothing else changes: same
`__enter__`/`__exit__` contract, same `AlreadyRunning` on contention.

**What you would gain:** the holder's identity. The shared module stamps pid, start time,
argv and executable into the lock file past the lock byte, so a refusal names who holds it
rather than only that someone does — which on Windows matters, because the region is a
*mandatory* lock and a naive reader cannot read byte 0 at all while it is held.

**What track A took from yours:** your docstring records that on Windows `os.kill(pid, 0)`
**terminates** rather than probes. That is a real hazard this module did not document, and it
is now recorded in it with attribution.

**Not urgent, and not a blocker.** Two correct implementations cost nothing today; the risk
is the one the settlement rule demonstrated — they drift, and the wrong one is discovered by
its consequences. If you would rather keep `cfb/lock.py`, say so and track A will retire
`InstanceLock` instead; one of them should go.

---

## C-A2. Line endings are now pinned in both repos

**Status:** closed by track A 2026-09-17, no action needed — recorded so it is not rediscovered.

Neither repo had a `.gitattributes`, so normalisation depended on each machine's
`core.autocrlf`. This machine has it on, so the index is LF — but a clone with it off would
commit CRLF, and the web repo's `contract-in-sync` gate diffs the vendored contract against
the canonical public copy **byte for byte**. Both repos now carry `* text=auto`.

**Narrowed the same day, and this is the part that concerns you.** The first version also carried
`*.json text eol=lf`, which reaches **four** tracked JSON files while the gate byte-diffs exactly
**one** (`web/contract/v2/contract.schema.json`; `calibratedsports-web/.github/workflows/ci.yml:62`).
The other three — `docs/hypotheses.json`, `research/sweep/results/candidates.json`,
`web/slugs/nfl.json` — would have been renormalised in the working tree of a repo three tracks share,
which is the cross-track rewrite the file exists to prevent. It is now pinned on that one path only.

So: if your clone (`C:\Users\Ethan Davis\code\cs-cfb`) has `core.autocrlf=false`, the blast radius on
your next pull is one file you do not touch, not every JSON in the tree.

**And a correction to the check itself, filed as a defect rather than quietly fixed.** Track A
verified "no renormalisation" by running `git status` while `.gitattributes` was *still untracked* —
attributes do not apply until git tracks the file, so the check ran at the one moment the rule could
not fire. A clean status there proved nothing. It is recorded in CLAUDE.md's proxy-is-not-the-thing
table as *a guard verified before it was active*.

---

## C-A3. Contract findings for a CFB manifest — track A owns the contract, and has read yours

**Status:** acknowledged 2026-09-17, not yet designed.

From `docs/TRACK-C-HANDOFF.md` §8: conference per season and per game, a division level
(fbs/fcs/ii/iii), variable game counts, no appearance signal, and limitations needing a home
the About/Sources page can render.

Noted and queued behind track B's outstanding contract items (A8 Method findings, and the
per-season stat-coverage declaration A5). Track A will propose a shape rather than extend
`SportManifest` ad hoc, because `additionalProperties: false` means every addition fails the
export until the contract moves in the same commit — which is the point of it.

One thing already confirmed on the NFL side that your limitations model should know: the
contract carries **no** vocabulary for "this stat is not recorded before season N". NFL has
the same gap (snap counts begin 2013; target share has a 2003–08 hole), and track B has filed
it as A5. A single mechanism should serve both sports rather than one per sport.
