# Production clones: scheduled jobs run from `origin/main`, never from a unit's branch

Unit a-10, 2026-09-22. Raised by a-07 and a-08; a-07's migration refused on it.

## The defect

Every scheduled task launched from a working tree that units also branch in. The code
production ran was whichever branch a unit last checked out there: a-07 found
`calibrated-sports` on the pre-fix branch `a-06-verify-f01-drafting` while three writer tasks
launched from it. That is true of everything those tasks run, not only the migration.

**Two production clones fix it.** Units keep `code\calibrated-sports`, `code\cs-cfb` and
`code\cs-analytics` and their worktrees. No scheduled task runs from those after cutover.

    C:\Users\Ethan Davis\code\prod\calibrated-sports   NFL: logger, weekly refresh
    C:\Users\Ethan Davis\code\prod\cs-cfb              CFB: odds forward, weekly CFB

**Why two, not one.** It is one repository, but the two sets of jobs need different `.env`s.
`cs-cfb\.env` points `LOGGER_DB` at `_track_c_unused.db` on purpose, so CFB jobs never open
`market_log.db`. One merged `.env` would point the CFB jobs at the live logger database. Both
clones share `STORAGE_DIR` (`D:\calibrated-sports\data`, the parent of `LOGGER_DB`), so
`cfb.db`, the locks and the logs are where they were.

## Inventory: every task that runs this project's code (read from Task Scheduler, 2026-09-22)

| Task | Trigger | Runs | From, today |
|---|---|---|---|
| CalibratedSports CFB Odds Forward | every 5 min | `cs-cfb\.venv\Scripts\pythonw.exe -m jobs.ingest_cfb --odds-forward --log` | `code\cs-cfb` (workdir) |
| CalibratedSports CFB Weekly | Tue 09:00 | `cs-cfb\run_weekly_cfb.cmd` | `code\cs-cfb` (the .cmd `cd`s there) |
| CalibratedSports Logger (boot) | at boot, S4U | `powershell -File code\calibrated-sports\start_logger.ps1` | `code\calibrated-sports` |
| CalibratedSports Logger (logon) | at logon | same script | `code\calibrated-sports` |
| CalibratedSports Weekly Refresh | Tue/Wed/Thu 09:00 | `calibrated-sports\.venv\Scripts\python.exe -m jobs.weekly_refresh` | `code\calibrated-sports` |
| CalibratedSports CFB Odds P1 | once, 2026-09-19 09:00 (spent) | `cs-cfb\run_cfb_job.cmd --odds-p1 --lock-wait 900` | `code\cs-cfb`; no next run |
| KillCfbProbe | once, 2026-09-13 08:00 (spent) | `D:\calibrated-sports\kill_cfb_probe.ps1` | outside every repo |
| CalibratedSports Relay Resume | hourly | `code\_relay\run\start-night.ps1 -Quiet` | the relay: it starts units, it is not production |

The last three stay where they are. P1 and KillCfbProbe are one-shot and have no next run, so
re-pointing them changes nothing.

Current task XML, the rollback baseline, is in `code\prod\task-backup-2026-09-22\`.

## How the clones were built (reproducible)

    cd C:\Users\Ethan Davis\code\prod
    git clone https://github.com/edavis9817/calibrated-sports.git calibrated-sports
    git clone https://github.com/edavis9817/calibrated-sports.git cs-cfb
    git -C <clone> remote set-url --push origin DISABLED-production-clone-never-pushes
    py -3.12 -m venv <clone>\.venv
    <clone>\.venv\Scripts\python.exe -m pip install -r <clone>\.venv\constraints-from-dev-venv.txt
    <clone>\.venv\Scripts\python.exe -m pip install -r <clone>\requirements.txt

- **Fetch over HTTPS, push disabled.** A production clone has no reason ever to push. The one
  commit it makes (below) is carried by a person, on purpose.
- **Python 3.12, which is what CI tests.** The NFL tasks ran a 3.14.5 venv until now. It
  completed the 09-22 refresh, so it is not the segfaulting system interpreter CLAUDE.md
  describes, but "passed CI" only means something about 3.12.
- **Same package versions as the venv each clone replaces.** `constraints-from-dev-venv.txt`
  is a `pip freeze` of `code\calibrated-sports\.venv` or `code\cs-cfb\.venv`. The new freeze was
  diffed against it and is identical (34 and 31 packages). Only the interpreter changed.
- **`.env` is copied by an explicit allowlist of names**, each line verbatim. The NFL clone's
  `.env` carries **no Kalshi key and no `KALSHI_PRIVATE_KEY_PATH`**. No scheduled job reads one;
  `probe_history.py` is the only caller of `config.kalshi_private_key()`. If a key is ever
  needed, put the PEM in a file outside every repo and set `KALSHI_PRIVATE_KEY_PATH` to that
  path. **Never put the PEM body in `.env`.** Any tool that lists a `.env`'s keys prints it:
  `cut -d= -f1` did, and so does `dotenv_values(...).keys()`, because each unquoted base64 line
  parses as a key.

## Keeping them current: `prod_sync.ps1`

A production clone carries branch `main` and nothing else, its tree is clean, and
`main == origin/main`. `prod_sync.ps1` checks exactly that and says so in one line.

    <clone>\prod_sync.ps1                  check only, changes nothing
    <clone>\prod_sync.ps1 -Update          fast-forward to origin/main, only if every other check passes
    -ExpectLogger   (NFL, after cutover)   the running logger must be THIS clone's, else DRIFT
    -NoLogger       (CFB)                  skip the logger check

| exit | meaning |
|---|---|
| 0 | OK, or DEFERRED: a job is running from the clone, so the fast-forward waits for the next run |
| 1 | DRIFT: on a branch, another local branch exists, dirty tree, local commits, still behind, or pip failed |
| 2 | could not check (fetch failed, not a clone) |

- **It never resets, stashes, checks out, deletes or pushes.** A drifted production clone
  holds something a person must look at, and each of those verbs destroys it quietly.
- **Visible failure.** Task Scheduler's Last Run Result is the exit code. Every run appends one
  line to `D:\calibrated-sports\data\logs\prod_sync.log`. If `PROD_SYNC_HEALTHCHECK_URL` is set in
  the clone's `.env`, the script pings it on OK and `<url>/fail` on DRIFT or ERROR, which alerts
  by email under neglect. That URL is not set yet: creating the check is Ethan's.
- **`requirements.txt` changes are followed.** When a fast-forward moves `requirements.txt`, it
  runs `pip install -r` into the clone's venv. A failed install is DRIFT.
- **The logger picks up new code only on restart.** The sync does not restart it. The log line
  names the pid and clone of the running logger (read from the single-instance lock's stamp),
  and `logger.log` prints `build <fingerprint>` at every start.

Guarded by `tests/test_prod_sync.py`. It checks every verdict on its own input (a bare origin
and a clone under `tmp_path`), and it runs only where Windows PowerShell exists.

### The one commit production makes: the slug registry

`jobs.weekly_refresh` step 3b commits `web/slugs/` **in the clone it runs from** whenever the
export appends a slug (last on 2026-09-22, `8986d8d`). In a production clone that makes `main`
one commit ahead of origin. The sync reports it by name ("touching only web/slugs") and will not
fast-forward over it. To carry it to origin:

    cd C:\Users\Ethan Davis\code\prod\calibrated-sports
    git log --stat origin/main..main                  # confirm: web/slugs/ only
    git fetch origin
    git rebase origin/main                            # the registry is append-only; a conflict means two appends
    git push git@github.com:edavis9817/calibrated-sports.git main
    .\prod_sync.ps1                                   # OK again

The push names the URL explicitly because the clone's push URL is disabled. Pushing a slug-only
commit straight to `main` is what happened to `8986d8d` from the old arrangement.

## Cutover (Ethan's to run; nothing below has been done)

Do it outside Tue/Wed/Thu 09:00 (weekly refresh) and outside any CFB kickoff hour. The forward
tick buys a snapshot within 40 minutes of a first kickoff; `jobs.ingest_cfb --odds-week` lists
them.

**0. Land the code and bring both clones up to it.**

    # after the a-10 branch is merged to origin/main:
    & "C:\Users\Ethan Davis\code\prod\calibrated-sports\prod_sync.ps1" -Update
    & "C:\Users\Ethan Davis\code\prod\cs-cfb\prod_sync.ps1" -Update -NoLogger
    # both must print OK

**1. Re-point the four live tasks.** `Set-ScheduledTask -Action` replaces only the action and
keeps triggers and principal. The boot task is S4U and was registered from an elevated shell, so
changing it probably needs one too.

    $P = "C:\Users\Ethan Davis\code\prod"
    Set-ScheduledTask -TaskName "CalibratedSports CFB Odds Forward" -Action (New-ScheduledTaskAction `
        -Execute "$P\cs-cfb\.venv\Scripts\pythonw.exe" -Argument "-m jobs.ingest_cfb --odds-forward --log" `
        -WorkingDirectory "$P\cs-cfb")
    Set-ScheduledTask -TaskName "CalibratedSports CFB Weekly" -Action (New-ScheduledTaskAction `
        -Execute "$P\cs-cfb\run_weekly_cfb.cmd")
    Set-ScheduledTask -TaskName "CalibratedSports Weekly Refresh" -Action (New-ScheduledTaskAction `
        -Execute "$P\calibrated-sports\.venv\Scripts\python.exe" -Argument "-m jobs.weekly_refresh" `
        -WorkingDirectory "$P\calibrated-sports")
    $logger = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -NonInteractive ' +
        '-ExecutionPolicy Bypass -WindowStyle Hidden -File "' + "$P\calibrated-sports\start_logger.ps1" + '"') `
        -WorkingDirectory "$P\calibrated-sports"
    Set-ScheduledTask -TaskName "CalibratedSports Logger (logon)" -Action $logger
    Set-ScheduledTask -TaskName "CalibratedSports Logger (boot)"  -Action $logger   # elevated shell

**Note on CFB Weekly.** The committed `run_weekly_cfb.cmd` differs from the copy in
`code\cs-cfb`, which has uncommitted edits: it redirects to `cfb\logs\weekly_cfb.log` and drops
`--log`. The task has been running that uncommitted copy. The production clone runs the
committed one, which writes through `--log` to `cfb\logs\ingest_cfb.log`. If the uncommitted
edit is wanted, commit it (track C) before cutover.

**2. Move the running logger.** A task change does not restart it, and **`start_logger.ps1
-Restart` from an ordinary shell will not stop it.** The live logger runs in session 0 (started
by the S4U boot task), and its command line cannot be read from an interactive session, so the
script's process scan reports `NOT RUNNING` while it is capturing. A start from the new clone
would then hit the single-instance lock and exit, leaving the old logger running. Either:

    # (a) simplest: reboot. The boot task now starts the logger from the production clone.
    # (b) from an ELEVATED shell: stop the pid the lock names, then start from the new clone.
    & "$P\calibrated-sports\.venv\Scripts\python.exe" -c "from core import single_instance as s; print(s.holder(s.LOGGER))"
    Stop-Process -Id <pid from above>
    & "$P\calibrated-sports\start_logger.ps1"

Verify, in this order. A single green line proves nothing:

    & "$P\calibrated-sports\prod_sync.ps1" -ExpectLogger   # OK, "logger pid N runs from this clone"
    Get-Content D:\calibrated-sports\data\logger.log -Tail 40 | Select-String "build "
    # and the healthchecks.io dead-man stays green past 20 minutes

**3. Schedule the sync.** It runs hourly at :45, so it misses the 09:00 refresh and the :00
tick of every hourly job.

    foreach ($c in @(@{n="calibrated-sports"; a="-Update -ExpectLogger"}, @{n="cs-cfb"; a="-Update -NoLogger"})) {
        $act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -NonInteractive ' +
            '-ExecutionPolicy Bypass -WindowStyle Hidden -File "' + "$P\$($c.n)\prod_sync.ps1" + '" ' + $c.a)
        $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(45) -RepetitionInterval (New-TimeSpan -Hours 1) `
            -RepetitionDuration (New-TimeSpan -Days 3650)   # the P3650D the 5-minute Odds Forward task already uses
        Register-ScheduledTask -TaskName "CalibratedSports Prod Sync ($($c.n))" -Action $act -Trigger $trg
    }

**Rollback.** Re-import the saved definitions:

    Register-ScheduledTask -TaskName "<name>" -Xml (Get-Content "$P\task-backup-2026-09-22\<file>.xml" -Raw) -Force

## What the old clones can and cannot be fixed to do

- **The PEM in `code\calibrated-sports\.env` can be removed without breaking any running job.**
  Nothing that runs on a schedule reads it. It also does not work as stored: the block has no
  `-----END` line, so `config.kalshi_private_key()` returns `None` today and every process runs
  unauthenticated already. `load_dotenv` reads at process start, so editing the file cannot
  affect the running logger. Removing it is a deletion of a secret, and **the key should be
  rotated first**, since it has now been printed into transcripts three times.
- `code\cs-cfb\.env` and `code\cs-analytics\.env` carry no key.
