# Runbook: `jobs.migrate_nflverse_versions`

Collapses the duplicate NULL-season rows in `nflverse_versions` and adds the
unique index `ux_nflv_key`. Why it exists: `jobs/migrate_nflverse_versions.py`'s
docstring, and DECISIONS.md rows for units a-03 and a-07.

**Run with someone watching. It writes `market_log.db`, which only Ethan runs
read-write.**

---

> ## ⛔ HARD STOP: read this before `--apply`
>
> The index makes the OLD writer (every commit before a-03, `b1b8891`) **raise
> `IntegrityError` on the second same-day pull of an all-season file**, and the
> old `ingest_one` raises it *after* `archive_file` has overwritten that day's
> file. The ledger's sha then no longer matches the disk. The index is
> permanent. The check that guards it is only a snapshot of one moment.
>
> `--apply` refuses unless it can see that every writer is on the fix: each
> scheduled writer task's checkout, the running logger's loaded revision, and
> no ingest job running. **It cannot see these, so they are on you:**
>
> 1. **Any python process that imports `store` from an old checkout**, such as
>    a notebook, a REPL or a shell running `python -m jobs.ingest_nflverse`.
>    The dry run counts unclassifiable python processes but cannot inspect them.
>    Close anything you are not sure of.
> 2. **The future.** After the migration, checking out ANY branch cut before
>    a-03 into `C:\Users\Ethan Davis\code\calibrated-sports` arms the failure.
>    It fires at the next logger restart (logon task) or the next weekly
>    refresh (09:00 Tue/Wed/Thu). Unattended track units check out branches in
>    that tree, and on 2026-09-22 it held `a-06-verify-f01-drafting`, which is
>    pre-fix. **Do not migrate until production runs from a tree that nothing
>    else checks out**, or until every live branch contains a-03.
> 3. **Other clones.** `cs-cfb` and `cs-analytics` are clones of the same
>    repository. None of their scheduled tasks writes `nflverse_versions`. A
>    manual ingest from one of them, with `DB_PATH` pointed at the live store,
>    runs whatever code that clone holds.
> 4. **The gap between check and write.** The check runs seconds before
>    `BEGIN IMMEDIATE`. A writer that starts in between is not seen.

---

## Don't use any figure from a document. Re-count.

The old writer adds rows at every same-day all-season pull, so every published
count is stale when you read it. a-03 counted 71 surplus rows and c-04 counted
77 six hours later. The dry run prints the current figures, and those are the
only ones to act on:

    null_season_rows / null_season_keys / null_season_dup_keys / null_season_surplus
    seasoned_rows / seasoned_surplus      <- expect seasoned_surplus 0
    surplus                               <- must equal null_season_surplus
    kept_sha_mismatch                     <- must be [] (with --raw-dir)

If `seasoned_surplus` is not 0, or `kept_sha_mismatch` is not empty, stop.
The migration's premise does not hold on that store.

## Order

All commands run from the production checkout,
`C:\Users\Ethan Davis\code\calibrated-sports`, with its `.venv`. The migration
imports only the standard library (sqlite3, json, ast, subprocess), so the
interpreter's numpy is irrelevant here. Note that this `.venv` is Python
3.14.5, measured 2026-09-22 from `pyvenv.cfg`, and not the 3.12 that CLAUDE.md
recommends for the export.

1. **Put production on the fix.** In the production tree:
   `git fetch origin` then `git checkout main` then `git merge --ff-only origin/main`.
   Confirm that `store.py` contains `NFLV_WRITER_REV = 2`.
2. **Restart the logger** with `.\start_logger.ps1 -Restart`, then
   `.\start_logger.ps1 -Status`. The new process writes
   `build <fp> pid <pid> nflv_writer 2` into `source_health.logger_start`.
3. **Don't run this near 09:00 on Tue, Wed or Thu** (the Weekly Refresh task),
   and make sure no `jobs.ingest_nflverse` is running.
4. **Snapshot the table.** It is small, and this read is read-only:

       .venv\Scripts\python.exe -c "import sqlite3,json; c=sqlite3.connect('file:D:/calibrated-sports/data/market_log.db?mode=ro',uri=True); json.dump(c.execute('SELECT rowid,* FROM nflverse_versions').fetchall(), open('D:/temp/nflverse_versions_before.json','w'))"

5. **Dry run.**

       .venv\Scripts\python.exe -m jobs.migrate_nflverse_versions --db D:/calibrated-sports/data/market_log.db --raw-dir D:/calibrated-sports/data/raw

   The last block must say `writers: every writer is on the fixed upsert`.
   If it says `REFUSE`, each `x` line names what to fix. Fix it and go back to
   step 5. Don't work around it.
6. **Apply.** Run the same command with `--apply`. Exit 3 means it refused and
   nothing was written.
7. **Verify.** Run the dry run again. Expect `surplus 0`,
   `null_season_dup_keys 0` and `index_present_before True`.
8. **Follow-up code change (not part of the run):** add `ux_nflv_key` to
   `store.SCHEMA`, so a fresh store gets it too. Do this only after the live
   store is migrated, because `init_db` on an unmigrated store would fail on
   the duplicates.

## "Read-only" (`mode=ro`) is narrower than it sounds

Measured by `research/wal_readonly_probe.py` (unit a-07) on a scratch WAL
database. The same effect was observed on the live store: `-wal` and `-shm`
were absent before one `mode=ro` read and present after it.

- **Guaranteed:** the connection cannot write, and the main database file's
  bytes are unchanged.
- **Not guaranteed: no files touched.** On a WAL database, a `mode=ro` open
  **creates `-wal` and `-shm`** if they are absent, and **leaves them** after
  close, because a read-only connection cannot checkpoint. The next read-write
  connection absorbs them. This is harmless, but it is a filesystem write.
- **Not guaranteed: no effect on the writer.** An open read transaction
  **pins the WAL**. A writer's `wal_checkpoint(TRUNCATE)` returns busy, and the
  `-wal` file grows for as long as the reader holds its snapshot. Keep
  read-only sessions against the live store short.
- **`immutable=1` is not the fix.** It touches no files, but it ignores the
  WAL, so it reads a stale database and misses committed rows. Use it only on
  a file nothing is writing.
