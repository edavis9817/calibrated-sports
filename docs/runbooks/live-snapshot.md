# Runbook: the Live snapshot job (unit a-23)

`jobs/live_snapshot.py` reads the ESPN scoreboard and Kalshi's `KXNFLGAME`
markets on a schedule and PUTs `live/nfl/snapshot.json` to the site bucket.
The contract is `live.snapshot`; `docs/web-schema.md` describes the file.

## Run it by hand

    .venv\Scripts\python.exe -m jobs.live_snapshot --once --no-upload --out D:\temp\snap.json
    .venv\Scripts\python.exe -m jobs.live_snapshot --once          # also PUTs to R2
    .venv\Scripts\python.exe -m jobs.live_snapshot --loop --log    # the scheduled process

`--once` exits 1 if the file has neither a slate nor last week's finals, 2 if
the upload failed. `--loop` refuses to start a second instance (OS lock
`<STORAGE_DIR>/locks/live_snapshot.lock`) and exits 3.

The job imports no numpy, so the default `.venv` interpreter runs it. Its
injury refresh runs `jobs.ingest_feeds`, which imports polars: if that ever
crashes on the default interpreter, the refresh fails on its own (a
`refresh_failed` event on the healthcheck log) and the snapshot keeps going.

## Scheduling it (not done by a-23 — Ethan's step)

Production runs from `code\prod\calibrated-sports`, which tracks `origin/main`.
This job is on branch `a-23-live-snapshot` until merged, so it is NOT scheduled.
After the merge, from an ordinary (non-elevated) PowerShell:

    $root = "$env:USERPROFILE\code\prod\calibrated-sports"
    $a = New-ScheduledTaskAction -Execute "$root\run_live_snapshot.cmd" -WorkingDirectory $root
    $t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "CalibratedSports Live Snapshot (logon)" -Action $a -Trigger $t -Settings $s

Same shape and same accepted debt as the logger's logon task: the PC must be on
and logged in.

## Health

- The logger's `HEALTHCHECK_URL` receives **only `/log` events** from this job
  (one per source and reason per 15 min). They do not change that check's
  state - a success ping from here would keep the logger's dead-man green while
  the logger was dead.
- Set `LIVE_HEALTHCHECK_URL` to a dedicated healthchecks.io check (Period 15 min
  on game days is too tight for idle hours; use Period 1 h, Grace 20 min) to get
  a dead-man for THIS job: success after each upload, `/fail` when one fails.
- The file itself: `stale_after` in the past means the job has stopped.
- Log: `<STORAGE_DIR>/live/live_snapshot.log`; the injury refresh's own output
  is `<STORAGE_DIR>/live/injuries_refresh.log`.

## Switches (all in `config.py`, all env-overridable)

`LIVE_SNAPSHOT_PRICES=0` drops the exchange quotes (the 2026-09-23 ledger
dropped Kalshi prices from Live; the 2026-09-24 audit asks for them back - the
default follows the newer instruction). Cadences: `LIVE_SNAPSHOT_EVERY_LIVE`
(30), `_GAMEDAY` (900), `_IDLE` (3600). Backoff: `LIVE_SNAPSHOT_BACKOFF_BASE`
(30), `_MAX` (3600).

## Write budget

One PUT per cycle. A Sunday with ~11 h of games in their live window is
~1,300 PUTs at 30 s plus ~50 at 15 min; a quiet weekday 24. Well inside R2's
free Class A allowance. Reads: one scoreboard GET and one exchange GET per
cycle - the same count whether one visitor or ten thousand load the page.
