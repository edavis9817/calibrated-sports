# W07 requests from track A to track C

Filed 2026-09-17 by track A (`calibrated-sports`, NFL producer). W07 gives each track its
own files; track A does not edit `cfb/*`, `jobs/ingest_cfb.py` or
`docs/TRACK-C-HANDOFF.md`. Each item states the finding, what track A has already
done, and what track C would change — nothing here is applied to your files.

---

## C1. `cfb/lock.py` and `core/single_instance.py` are the same lock, written twice

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

## C2. Line endings are now pinned in both repos

**Status:** closed by track A 2026-09-17, no action needed — recorded so it is not rediscovered.

Neither repo had a `.gitattributes`, so normalisation depended on each machine's
`core.autocrlf`. This machine has it on, so the index is LF — but a clone with it off would
commit CRLF, and the web repo's `contract-in-sync` gate diffs the vendored contract against
the canonical public copy **byte for byte**. Both repos now carry `* text=auto` and
`*.json text eol=lf`.

If your clone (`C:\Users\Ethan Davis\code\cs-cfb`) has `core.autocrlf=false`, check `git
status` after your next pull: git may want to renormalise files on first touch.

---

## C3. Contract findings for a CFB manifest — track A owns the contract, and has read yours

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
