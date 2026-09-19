# Requests filed TO track F

Per the convention in `DECISIONS.md` (2026-09-17, Ethan, project-wide): one file per TARGET track,
`docs/track-<target>-requests.md`, with sections by SOURCE track. This file is everything filed TO
track F. Track F's filings to track A live in `docs/track-a-requests.md` (F2, F3).

---

## F-A1. F02 IS UNBLOCKED — and the one mechanism still to settle is who declares `analytics/`

**From track A, 2026-09-19.** You filed F3 as BLOCKING: `upload()` deleted from R2 by absence, so
analytics keys living in the shared export dir would be deleted by any weekly run where your export
had not run first. **That is fixed and pushed** (`91c86d9`, and the contract batch after it).

### What changed in the shared uploader

`upload()` now takes a DECLARATION — the prefixes the run that produced the tree actually rebuilt —
and deletes only inside them:

```python
absent = sorted(set(state) - set(local))
if refreshed is None:
    removed, withheld, declared = [], absent, None      # nobody said: delete NOTHING
else:
    removed  = [k for k in absent if any(k.startswith(p) for p in refreshed)]
    withheld = [k for k in absent if k not in set(removed)]
```

Exactly as you and Ethan specified: **missing or empty declaration deletes nothing.** `None`
("nobody said") and `[]` ("declared, owns nothing") both withhold and are reported apart, because
`removed_withheld` is benign in the first case and a signal in the second. The declaration is
PASSED, never persisted — a stale copy on disk would authorise deletions for a run that never
happened, which is the same defect wearing a fresh coat.

It crosses the process boundary as one line of stdout: `main()` prints `REFRESHED <prefixes>` last,
`jobs.export_web.parse_refreshed(text)` reads it, and both live in one file so they cannot drift. A
test drives the real producer into the real consumer over real stdout with no fake in between.

### The two things track F has to do, and why each is shaped this way

**1. The keys must land under `WEB_EXPORT_DIR/analytics/`.** `upload()` walks `local_keys(dest)` over
`WEB_EXPORT_DIR` and nothing else, so keys in `storage_path("analytics_export")` have no path to R2 —
your own F3 established this. The good news is that your `sync(out, root=None, dry_run=False)`
**already takes a root**, so this is a value at the call site, not a change to the module.

**Do NOT change `export_dir()`, and do not reference `WEB_EXPORT_DIR` inside `analytics/export.py`.**
Your own guard forbids it — `test_the_exporter_writes_nowhere_near_the_site_export_dir` parses the
module's AST and asserts the symbol appears nowhere in it — and that guard is correct and should
stay. `export_dir()` remains `storage_path("analytics_export")` and remains never equal to
`WEB_EXPORT_DIR`, so the test keeps passing unchanged.

The site root therefore has to arrive from OUTSIDE the module: a `--root` (or `--publish-to`)
argument on your job's entry point, defaulting to `export_dir()`. The job that publishes passes the
site root; every other invocation writes to your own directory exactly as today.

**2. Something must declare `analytics/`, and it can only be you.** Track A's five `sync_keys` call
sites declare `nfl/market/`, `nfl/players/`, `nfl/teams/` and `research/` — and they must never
declare `analytics/`. That is asserted categorically in `tests/test_prefix_ownership.py`: no
producer-owned prefix may contain or be contained by `analytics/`, extracted by AST with zero
unresolvable call sites. It stays.

**And the reason is not bookkeeping.** A declaration is a statement that THIS RUN REBUILT THIS
PREFIX. Track A's export cannot truthfully say that about `analytics/` — it has no analytics
builder. A run that declares a prefix it did not rebuild hands `upload()` an empty wanted set over an
owned prefix, which is delete-by-absence reintroduced one layer up, in the mechanism built to remove
it.

So: **your export declares `analytics/`, by printing the same sentinel.**

```python
from jobs.export_web import REFRESHED_SENTINEL   # one constant, not a second copy
...
print(REFRESHED_SENTINEL + " " + OWNED_PREFIX)   # last line, after any summary
```

### What track A will build, so the two halves meet

`jobs/weekly_refresh.py` today captures ONE export step's stdout, parses the sentinel and passes
`--refreshed` to the single `--upload-only` step. It will learn to run your export as a step and
**union the declarations across steps** before passing them on one command line — one uploader, one
`.upload_state.json`, each producer declaring only what it actually rebuilt.

Track A is not building this until you confirm the shape, because it has to match your job's real
entry point and flags. Tell us the module and argument you settle on and it lands in the same unit
as the `weekly_refresh` change.

### What this does NOT require

- No second uploader, and no second state file. Ethan's ruling stands: two writers to one bucket
  with two `.upload_state.json` files is a split-brain nothing reconciles.
- No `sync_keys` call owning `analytics/` on track A's side — that was your F2 request, and the
  inverse is what actually protects you. See `tests/test_prefix_ownership.py`.
- No contract change. `analytics.index` and `analytics.metric` and their key patterns are already in
  `contract.schema.json` and are validated by the producer's own suite.

### One caveat, stated because it has not been exercised

The scoped deletion has never deleted anything in production. Both runs since it landed reported
`removed 0, removed_withheld 0`, because no key disappeared — so the live evidence covers the
withhold path only. The tests are what cover the delete path, not those runs. The first time your
export drops a metric will be the first real exercise of it; `removed` and `removed_withheld` are in
the upload's JSON summary and `weekly_refresh` logs both, worded differently for the two readings.
