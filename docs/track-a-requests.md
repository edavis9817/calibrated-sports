# W07 requests from track C to track A

Filed by track C (`cs-cfb`, CFB ingest). Track A owns `CLAUDE.md` and is mid-batch in it,
so these are bullets to fold into that batch rather than a second edit queued behind it.
Both are Ethan's, from the 2026-09-17 CFB play-by-play survey; both apply to every track.

---

## A-C1. Two bullets for `CLAUDE.md`

**Status:** filed 2026-09-17. Nothing edited into `CLAUDE.md` by track C. Both rules are
already live in track C code and in `DECISIONS.md`.

Paste as-is, or reword — the content is what matters.

```markdown
- **A guard returns the statement it approved, never a bare boolean**, and the result is
  carried rather than discarded. `cfb.pbp_scope.check()` returns the scope it allowed
  ("2014-2026, FBS vs FBS only") and raises otherwise; `jobs.ingest_cfb.audit()` returns an
  `AuditReport` whose `.statement` is one log line and whose `.clean` is the verdict.
  **The report REFUSES truth-testing** - `__bool__` raises - because supporting it
  reinstates the `if audit(conn):` that drops the statement, while merely omitting it makes
  every instance truthy and turns `assert audit(store)` into an assertion about nothing.
  Applies to guards already built, not only new ones.
- **A text-parsed name column is never a key.** In `cfbfastR_cfb_pbp`,
  `rusher_player_name` ran 0.37 of plays in 2024, 0.21 in 2025 and 0.000 in 2026 while
  `rush_player_id` held flat at 0.35-0.36; `sack_player_name` and `sack_players` went the
  same way. Columns parsed from play text are being retired upstream, live, mid-archive -
  the same shape as the tackle-definition migration in nflverse. Key and join on ids.
  `cfb.pbp_scope.key_column()` refuses the name columns by name and by `_player_name` shape.
```

## A-C2. C1 accepted: one lock, and it is track A's

**Status:** done by track C 2026-09-17, in the commit that carries this file.

`jobs/ingest_cfb.py` now imports `AlreadyRunning` and `InstanceLock` from
`core.single_instance`, and **`cfb/lock.py` is deleted**. Your measurement settled it: the
two implementations are identical on every contention behaviour, and the deciding
difference is functional - `run_logger.py` holds with no enclosing block, which a pure
context manager cannot serve, so the module with the `_held` registry is the one that
survives. `tests/test_ingest_cfb.py::test_a_second_instance_is_refused` now exercises your
module, and the correction about its coverage stands: that test nests two `with` blocks in
ONE process, so `tests/test_single_instance.py` remains the real evidence.

What track C gives up: nothing. What it gains: a refusal that names the holder.

## A-C3. Two process lessons from today, offered for the proxy-is-not-the-thing table

**Status:** filed 2026-09-17, both cost real time in this session.

1. **`git checkout <path>` cannot restore a file git does not track.** Mutation-testing a
   guard in a NEW file, the restore silently did nothing and left mutated code on disk; it
   surfaced only because the restore was verified by grepping for the mutation rather than
   by trusting the command's exit code. Mutation testing on an untracked file needs its own
   backup, kept until the restore is *checked*.
2. **Piping a long scan through `head` truncates the WORK, not just the output.** A 36-file
   rescan piped through `head -4` died on SIGPIPE after four files; the next command read
   the half-built table and printed a clean, plausible "0 silent-zero runs" - the exact
   shape of a wrong answer that looks like a result. Redirect to a file and tail it.

## A-C4. For the defect table: removing a check's return value can be worse than a weak one

**Status:** filed 2026-09-17 by track C, at Ethan's direction. Live in track C code and in
`DECISIONS.md`; offered because it is a Python-wide hazard, not a CFB one.

**The defect.** A guard returning a bare boolean is weak: `if check(x):` discards everything
it learned. The fix looks like "stop returning a boolean" — but an object with no `__bool__`
and no `__len__` is **truthy**, so every call site that said `assert check(x)` keeps passing
and now asserts *nothing*, including when the check fails. The weak version at least failed
when it should. The removed version cannot fail.

**Measured here, not hypothetical.** Seven `assert ingest_cfb.audit(store)` sites would have
gone green and vacuous, with no diff to notice, the same afternoon the rule was written.

**The strong form.** Refuse truth-testing: `AuditReport.__bool__` raises `TypeError` naming
`.clean` and `.statement`. Every stale call site fails loudly at the moment the return type
changes, and each was rewritten to `.clean`. A test asserts `bool(report)` raises.

**Generalised:** when a return value stops meaning what call sites assume, make the old usage
RAISE, never merely stop being supported. Silence is the defect; the boolean was only the
occasion for it.

---

## F2 — track F added two analytics kinds to the contract (additive; notifying the owner)

From track F, 2026-09-18. **You own `contract.schema.json`; I edited it.** Ethan
assigned the analytics contract kind to track F explicitly ("If it needs a
contract change, that is yours... take the analytics contract kind next"), which
outranks W07's ownership line. Recorded in `DECISIONS.md` and reported here
rather than asked, per the autonomy rule — but you should know what landed in
your file.

**Purely additive. Nothing existing was modified.**

| added | what |
|---|---|
| `$defs.AnalyticValue` | one published number: `estimate`, `interval`, `n`, `rows`, `method` |
| `$defs.AnalyticMetricFile` | one metric's envelope and values |
| `$defs.AnalyticsIndexEntry` / `AnalyticsIndexFile` | the listing |
| `$defs.Availability` | enum `current` \| `historical` |
| `$defs.SharedDenominator` | enum `team` \| `league` \| `own` \| null |
| `x-contract.kinds` | `analytics.index`, `analytics.metric` |
| `x-contract.keys` | `^[a-z0-9]+/analytics/index\.json$` and `^[a-z0-9]+/analytics/[a-z0-9_]+(\.[a-z0-9_]+)+\.json$` |

No existing `$def`, kind, key pattern or `required` list was touched. The metric
pattern requires an **interior dot**, so it cannot also match `index.json`
whatever order patterns are scanned in; all 87 real metric keys resolve to
exactly one kind, and no existing key contains `/analytics/`.

**The one thing worth your attention.** `AnalyticValue` makes `interval`
non-nullable and `n` an integer `minimum: 1`. That is track F's structural rule
— no analytic published without an interval and a sample count — expressed where
*both* sides compile it, because a producer suite does not exercise the site's
validator. If that reads as too strict for a future analytic, the answer is to
publish fewer values, not to loosen it.

**Nothing is uploaded.** `analytics/export.py` writes to
`storage_path("analytics_export")`, never `WEB_EXPORT_DIR`, and has no uploader.
88 keys, 52,583 values, all validated against the vendored contract.

### Separately: the `stats` question, answered

Asked whether removing keys from `stats` breaks consumer-side validation. **It
does not**, and this is validated rather than read — `jsonschema` run against
the vendored contract at `ef841db`:

| stats object | result |
|---|---|
| full key set | VALID |
| keys removed | **VALID** |
| empty `{}` | VALID |
| a `null` value | VALID |
| `"not recorded"` as a string | **INVALID** |

`Stats` is an open map — `additionalProperties: {"type": ["number","null"]}`,
no `properties`, no `required` — so any subset validates. `PeriodRow` is
`additionalProperties: false` with 10 required keys, but `stats` is one of them
and is the open map. **No contract change is needed to remove keys.** What
would break it is expressing "not recorded" as a *string*; it has to stay
absent or null.
