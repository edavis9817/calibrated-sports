# The Board's cadence: `run_board_tick.cmd`, every 5 minutes, from the production clone

Unit a-31, 2026-09-24. **The task is NOT registered.** Registering it is Ethan's, and it
must point at `code\prod\calibrated-sports`, never at a unit's working tree (see
`production-clone.md`: once a task runs from prod, merging to `origin/main` is deploying).

## What runs

    code\prod\calibrated-sports\run_board_tick.cmd
      -> .venv\Scripts\python.exe -m jobs.board_read --tick --upload --log

Every tick:

1. takes the single-instance lock beside the Board's tree (`<BOARD_EXPORT_DIR>.board.lock`).
   A tick that finds the previous one still running logs `previous tick still running -
   skipped` and exits 0. A full-slate read measured **164-225 s** on 2026-09-24, so an
   overlap with the next 5-minute tick is possible, and two writers on one ledger is the
   2026-09-11 shard incident again;
2. with `--upload` and R2 configured, checks the tree is intact: every key the Board's
   upload record (local, else its mirror at `_state/board_upload_state.json`) says was
   published must be on disk. If not it refuses and names the fix, `--restore`;
3. finds the weeks in play - the week of the next kickoff, plus any earlier week whose
   latest read still has a row that kicked off and is not settled;
4. reads a week only when `core.board.read_due` or `grade_due` says so;
5. uploads the Board's own tree (`export_web.upload(tree="board")`). **It deletes nothing.**

## The cadence it implements (config.py, `BOARD_*`)

| window | every | rule |
|---|---|---|
| before Tuesday 12:00 ET of the week | never | `read_due` is false before `window_open_ts` |
| Tuesday 12:00 ET to the last kickoff | 60 min | `BOARD_READ_EVERY_MIN` |
| within 2 h of ANY upcoming kickoff | 15 min | `BOARD_CLOSE_READ_EVERY_MIN`, `BOARD_CLOSE_WINDOW_MIN` |
| after a game kicks off, until every row settles | 60 min | `grade_due`, `BOARD_GRADE_EVERY_MIN` |

"Grading within an hour of final stats" holds **relative to the stats reaching the store**.
Settlement needs the game's `nfl_snap_counts` and `nfl_player_week` rows, which arrive with
the nflverse ingest in `CalibratedSports Weekly Refresh` (Tue/Wed/Thu 09:00). The Board
cannot grade faster than that ingest; it grades within the hour after it.

The tick fires every `BOARD_TICK_MIN` = 5 minutes, which bounds every cadence above: a
15-minute read lands 15-20 minutes after the previous one.

Before lines post, a due read finds no rows. It writes nothing, logs `ZERO rows`, and the
tick exits 0 - the page shows last week's graded board. That is not a failure.

## Settings the production `.env` needs

| name | value | note |
|---|---|---|
| `BOARD_EXPORT_DIR` | e.g. `D:\calibrated-sports\board` | **no default**; must not overlap `WEB_EXPORT_DIR` (refused) |
| `WEB_R2_BUCKET`, `WEB_R2_ACCESS_KEY_ID`, `WEB_R2_SECRET_ACCESS_KEY` | as for the weekly refresh | the Board ships to the site's bucket, keys `board/...` |
| `DB_PATH` / `LOGGER_DB` | the logger's `market_log.db` | opened `mode=ro` only |

Add `BOARD_EXPORT_DIR` to the prod clone's `.env` by name, the same way the allowlist copy
in `production-clone.md` does. Never enumerate `.env` keys to check it.

## Registering it (Ethan)

From an ordinary PowerShell, after `BOARD_EXPORT_DIR` is in the prod `.env`:

    $act = New-ScheduledTaskAction -Execute "C:\Users\Ethan Davis\code\prod\calibrated-sports\run_board_tick.cmd" `
             -WorkingDirectory "C:\Users\Ethan Davis\code\prod\calibrated-sports"
    $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(2) `
             -RepetitionInterval (New-TimeSpan -Minutes 5)
    $set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable
    Register-ScheduledTask -TaskName "CalibratedSports Board Tick" -Action $act -Trigger $trg -Settings $set

`-MultipleInstances IgnoreNew` is the scheduler's half of the single-instance rule; the lock
in step 1 is the half that also covers a tick started by hand.

**First run.** Do one by hand from the prod clone, with the upload, and read the log:

    .venv\Scripts\python.exe -m jobs.board_read --tick --upload
    .venv\Scripts\python.exe -m jobs.board_read --check --keys

## Reading the record

`<STORAGE_DIR>\logs\board_tick.log` gets, per tick: a `--- board_read <time>` header, one
JSON line per read, one JSON summary with `due`, `read`, `no_rows` and the upload result
(`uploaded`, `deleted` - always 0 - and `append_only`, the ledger checks it passed), then
`exit=N`. Task Scheduler's Last Run Result is the same exit code.

| exit | meaning |
|---|---|
| 0 | read and uploaded what was due; or nothing was due; or no lines yet; or skipped (another tick running) |
| 1 | refused: contract, source gate, lost tree, append-only violation, a missing setting (`BOARD_EXPORT_DIR`, `WEB_R2_BUCKET`), or an exception - the log has the message or traceback |
| 2 | a command-line usage error |

## What it publishes, and what is never removed

    board/nfl/{season}/wk{NN}/read-{iso}.json   kind board_read, one per read, never rewritten
    board/nfl/{season}/wk{NN}/index.json        kind board_index, rewritten every read
    board/nfl/ledger.parquet, ledger.csv         contract table board_ledger, append-only

**Nothing under `board/` is ever deleted by any uploader.** The web tree's uploader refuses
a declaration reaching `board/` and skips any `board/` file it finds; the Board's uploader
takes no declaration. So every read stays in R2 for good. Measured on 2026-09-24
(`--check --keys`): a real week-3 read of 211 rows is 416,746 bytes; the week-2 replay's
385-row reads are 789,492 and 799,257 bytes. Simulating this cadence over week 3's real
schedule gives **219 reads a week** (42 inside the two-hour windows), assuming stats land
~36 h after the last kickoff - so roughly **90-175 MB and ~220 keys a week**, all
permanent. See the a-31 report for the decision this leaves open.

## Recovering

- **Tree lost or partial** (new machine, disk): `python -m jobs.board_read --restore` pulls
  every `board/` key missing locally from the bucket. It uploads nothing and deletes nothing.
- **Upload refused `AppendOnlyError`**: the local ledger is not the bucket's plus rows at the
  end. Do not delete the bucket's copy. `--restore` into an empty directory and diff.

## Rolling back

Disable the task. Nothing else is needed: the Board writes only its own tree and `board/`
keys, and the web export neither reads nor deletes them.
