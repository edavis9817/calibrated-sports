<#
  Keep a PRODUCTION clone on origin/main, and fail visibly when it is not.
  Unit a-10. The runbook is docs/runbooks/production-clone.md.

  A production clone is a checkout that scheduled tasks run from and that no
  unit ever works in. It carries branch `main` and nothing else, its tree is
  clean, and `main` equals `origin/main`. Anything else is DRIFT.

    .\prod_sync.ps1                    check only: never changes anything
    .\prod_sync.ps1 -Update            fast-forward to origin/main if, and only
                                       if, every other check passes
    .\prod_sync.ps1 -Root <clone>      operate on another clone (default: the
                                       clone this script lives in)
    -ExpectLogger                      the running logger must be THIS clone's;
                                       anything else is DRIFT (the NFL clone,
                                       once the logger tasks point at it)
    -NoLogger                          skip the logger check (the CFB clone: it
                                       shares the store folder, runs no logger)
    -WaitSec <n>                       how long an -Update waits for a short job
                                       running from the clone to finish before it
                                       defers (default 120; 0 does not wait)
    -MaxDeferHours <h>                 how long an -Update may go on deferring
                                       before that is itself DRIFT (default 6)

  Exit codes - Task Scheduler shows them as Last Run Result:
    0  OK (or an -Update deferred, for less than -MaxDeferHours, because a
       short job is running from the clone)
    1  DRIFT - read the log line. The one thing a DRIFT run may have changed is
       a completed fast-forward, and the line says so when it did
    2  could not check (git fetch failed, not a git clone)

  Two kinds of process run from a production clone and they are treated
  differently (unit a-69). A PERMANENT LOOP - the logger, the Live snapshot
  loop: $Permanent below - never exits, so waiting for it is waiting for ever.
  The first version deferred behind Live Snapshot on every run, exit 0, and the
  clone sat 12 commits behind with nothing red anywhere. A loop has loaded its
  code and picks new code up only when restarted, so it does not hold the
  fast-forward back; the log line names each one still running code from before
  main last moved. A SHORT JOB (the Board tick, a refresh) imports modules as it
  goes and can load two revisions if the tree moves under it, so the
  fast-forward waits -WaitSec for it and then defers - and a deferral that has
  lasted -MaxDeferHours is DRIFT, exit 1, because a deferral that can last for
  ever is a sync that does not exist. A new permanent loop nobody added to
  $Permanent therefore turns the task red within -MaxDeferHours, naming its pid.

  What it deliberately never does: reset, stash, checkout, delete, push. A
  drifted production clone holds something a human has to look at - most often
  a slug-registry commit that jobs.weekly_refresh made and nobody pushed - and
  every one of those verbs is a way of destroying it quietly.

  Every run appends one line to <STORAGE_DIR>\logs\prod_sync.log, where
  STORAGE_DIR is the folder LOGGER_DB names in the clone's .env (the same rule
  start_logger.ps1 uses). If PROD_SYNC_HEALTHCHECK_URL is set in that .env the
  script pings it on OK and pings <url>/fail on DRIFT, so drift reaches a
  human under three weeks of neglect rather than waiting in a log.
#>
param([string]$Root = $PSScriptRoot, [switch]$Update, [string]$LogPath, [switch]$NoLogger, [switch]$ExpectLogger,
      [int]$WaitSec = 120, [double]$MaxDeferHours = 6)

# Command lines of the loops that run from a production clone and never exit,
# matched against Win32_Process.CommandLine. Adding a loop here is a statement
# that it tolerates the tree moving under it. Leaving one out is the safe
# error: it turns the sync red after -MaxDeferHours instead of stopping it
# silently.
$Permanent = @('run_logger\.py', 'jobs\.live_snapshot\b.*--loop')

$ErrorActionPreference = 'Continue'
$Root = (Resolve-Path $Root).Path.TrimEnd('\')

function Invoke-Git {
    $out = & git.exe -C $Root @args 2>&1 | ForEach-Object { "$_" }
    $script:GitRc = $LASTEXITCODE
    return $out
}

function EnvValue([string]$name) {
    $envf = Join-Path $Root ".env"
    if (-not (Test-Path $envf)) { return $null }
    $m = Select-String -Path $envf -Pattern ('^\s*' + $name + '\s*=\s*(.+?)\s*$') | Select-Object -Last 1
    if ($m) { return $m.Matches[0].Groups[1].Value.Trim('"').Trim("'") }
    return $null
}

if (-not $LogPath) {
    $db = EnvValue 'LOGGER_DB'
    $dir = if ($db) { Split-Path -Parent ($db -replace '/', '\') } else { Join-Path $Root "data" }
    $LogPath = Join-Path (Join-Path $dir "logs") "prod_sync.log"
}
$null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath)
# When the current run of deferrals began. One file per clone: two clones share a log folder.
$DeferPath = Join-Path (Split-Path -Parent $LogPath) ("prod_sync." + (Split-Path -Leaf $Root) + ".deferred_since")

$problems = New-Object System.Collections.Generic.List[string]
$notes    = New-Object System.Collections.Generic.List[string]

function Finish([int]$code, [string]$verdict) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $head = (Invoke-Git rev-parse --short HEAD | Select-Object -First 1)
    $line = "$stamp $verdict $Root @ $head"
    if ($problems.Count) { $line += " | DRIFT: " + ($problems -join '; ') }
    if ($notes.Count)    { $line += " | " + ($notes -join '; ') }
    Add-Content -Path $LogPath -Value $line -Encoding utf8
    Write-Host $line
    $hc = EnvValue 'PROD_SYNC_HEALTHCHECK_URL'
    if ($hc) {
        $url = if ($code -eq 0) { $hc } else { $hc.TrimEnd('/') + "/fail" }
        try { $null = Invoke-WebRequest -UseBasicParsing -Method Post -Body $line -Uri $url -TimeoutSec 10 }
        catch { Add-Content -Path $LogPath -Value "$stamp WARN healthcheck ping failed: $($_.Exception.Message)" -Encoding utf8 }
    }
    exit $code
}

# --- is this a clone at all -----------------------------------------------
$null = Invoke-Git rev-parse --git-dir
if ($GitRc -ne 0) { $problems.Add("not a git clone"); Finish 2 "ERROR" }

$fetchOut = Invoke-Git fetch --quiet origin
if ($GitRc -ne 0) { $problems.Add("git fetch failed: " + ($fetchOut -join ' ')); Finish 2 "ERROR" }

# --- 1. on main, and main is the only local branch -------------------------
$branch = (Invoke-Git symbolic-ref --quiet --short HEAD | Select-Object -First 1)
if ($GitRc -ne 0) { $problems.Add("detached HEAD") }
elseif ($branch -ne 'main') { $problems.Add("on branch '$branch', not main") }

$others = @(Invoke-Git for-each-ref --format='%(refname:short)' refs/heads | Where-Object { $_ -and $_ -ne 'main' })
if ($others.Count) { $problems.Add("local branches besides main: " + ($others -join ', ')) }

# --- 2. clean tree (untracked counts; ignored files such as .env do not) ---
$dirty = @(Invoke-Git status --porcelain | Where-Object { $_ })
if ($dirty.Count) {
    $shown = ($dirty | Select-Object -First 8 | ForEach-Object { $_.Trim() }) -join ', '
    $problems.Add("working tree not clean ($($dirty.Count)): $shown")
}

# --- 3. nothing on main that origin/main does not have ---------------------
$ahead = @(Invoke-Git rev-list origin/main..HEAD | Where-Object { $_ })
if ($ahead.Count) {
    $files = @(Invoke-Git diff --name-only origin/main...HEAD | Where-Object { $_ })
    $slugOnly = $files.Count -and -not ($files | Where-Object { $_ -notlike 'web/slugs/*' })
    if ($slugOnly) {
        $problems.Add("$($ahead.Count) local commit(s) touching only web/slugs - a slug-registry append by " +
            "jobs.weekly_refresh that is not on origin/main. Carry it to origin (see the runbook); never reset it away")
    } else {
        $problems.Add("$($ahead.Count) local commit(s) not on origin/main, touching: " +
            (($files | Select-Object -First 8) -join ', '))
    }
}

$behind = @(Invoke-Git rev-list HEAD..origin/main | Where-Object { $_ })

# --- 4. the running logger, where this clone runs one ----------------------
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not $NoLogger -and (Test-Path (Join-Path $Root "run_logger.py")) -and (Test-Path $py)) {
    Push-Location $Root
    $probe = & $py -c "import json; from core import single_instance as s; h = s.holder(s.LOGGER) or {}; print(json.dumps({'exe': h.get('executable'), 'pid': h.get('pid'), 'started': h.get('started_iso')}))" 2>$null
    Pop-Location
    try { $h = ($probe | Select-Object -Last 1) | ConvertFrom-Json } catch { $h = $null }
    if ($h -and $h.exe) {
        $mine = $h.exe.ToLower().StartsWith(($Root + '\').ToLower())
        if ($mine) { $notes.Add("logger pid $($h.pid) runs from this clone since $($h.started)") }
        elseif ($ExpectLogger) { $problems.Add("logger pid $($h.pid) runs from $($h.exe), not this clone") }
        else       { $notes.Add("logger pid $($h.pid) runs from $($h.exe), NOT this clone") }
    } elseif ($ExpectLogger) {
        $problems.Add("no logger lock holder found - is the logger running?")
    }
}

# --- processes running from this clone --------------------------------------
function ClonePids {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith(($Root + '\.venv\').ToLower()) })
}
function IsPermanent($proc) {
    foreach ($pat in $Permanent) { if ($proc.CommandLine -and $proc.CommandLine -match $pat) { return $true } }
    return $false
}
# A loop started before main last moved here is running the code from before
# that move, and will until something restarts it. Said on every run, so the
# age of a running build is in the log rather than in someone's memory.
function PermanentNotes {
    # %gd under --date=unix is the time of the reflog ENTRY - when HEAD moved in
    # this clone. %ct would be the commit's own time, which says nothing about
    # when it arrived here.
    $moved = (Invoke-Git reflog -1 --date=unix --format=%gd HEAD | Select-Object -First 1)
    if (-not ("$moved" -match '\{(\d+)\}')) { return }
    $movedAt = [DateTimeOffset]::FromUnixTimeSeconds([long]$Matches[1]).UtcDateTime
    foreach ($p in @(ClonePids | Where-Object { IsPermanent $_ })) {
        $started = $p.CreationDate.ToUniversalTime()
        if ($started -lt $movedAt) {
            $name = if ($p.CommandLine -match 'run_logger') { 'logger' } else { 'loop' }
            $notes.Add("$name pid $($p.ProcessId) started " + $started.ToString("yyyy-MM-ddTHH:mm:ssZ") +
                ", BEFORE main last moved here (" + $movedAt.ToString("yyyy-MM-ddTHH:mm:ssZ") +
                "): it runs the older code until restarted")
        }
    }
}

# --- 5. behind: report, or fast-forward -------------------------------------
if ($behind.Count) {
    if (-not $Update) {
        $problems.Add("behind origin/main by $($behind.Count) commit(s)")
    } elseif ($problems.Count) {
        $problems.Add("behind origin/main by $($behind.Count) commit(s); NOT fast-forwarded because of the above")
    } else {
        # A short job importing modules while the tree moves under it can load
        # two revisions at once, so wait for it. A permanent loop is not waited
        # for - see the header.
        $deadline = (Get-Date).AddSeconds($WaitSec)
        $waited = $false
        while ($true) {
            $busy = @(ClonePids | Where-Object { -not (IsPermanent $_) })
            if (-not $busy.Count -or (Get-Date) -ge $deadline) { break }
            $waited = $true
            Start-Sleep -Seconds 2
        }
        if ($waited -and -not $busy.Count) { $notes.Add("waited for a job running from this clone to finish") }
        if ($busy.Count) {
            $now = (Get-Date).ToUniversalTime()
            $since = $now
            if (Test-Path $DeferPath) {
                try {
                    $since = [datetime]::Parse((Get-Content $DeferPath -TotalCount 1), [cultureinfo]::InvariantCulture,
                        [System.Globalization.DateTimeStyles]::AdjustToUniversal)
                } catch { $since = $now }
            } else {
                Set-Content -Path $DeferPath -Value $now.ToString("yyyy-MM-ddTHH:mm:ssZ") -Encoding ascii
            }
            $hours = ($now - $since).TotalHours
            $sinceText = $since.ToString("yyyy-MM-ddTHH:mm:ssZ")
            $who = "$($busy.Count) process(es) running from this clone (pid " +
                (($busy | ForEach-Object { $_.ProcessId }) -join ',') + ")"
            if ($hours -ge $MaxDeferHours) {
                $problems.Add("behind origin/main by $($behind.Count) commit(s) and the fast-forward has been deferred since " +
                    "$sinceText ($([math]::Round($hours, 1)) h, limit $MaxDeferHours h): $who. A loop that never exits " +
                    "belongs in Permanent in prod_sync.ps1, or must be stopped")
                PermanentNotes
                Finish 1 "DRIFT"
            }
            $notes.Add("fast-forward DEFERRED since ${sinceText}: $who")
            PermanentNotes
            Finish 0 "DEFERRED"
        }
        $old = (Invoke-Git rev-parse HEAD | Select-Object -First 1)
        $ffOut = Invoke-Git merge --ff-only --quiet origin/main
        if ($GitRc -ne 0) { $problems.Add("fast-forward failed: " + ($ffOut -join ' ')); Finish 1 "DRIFT" }
        $notes.Add("fast-forwarded $($behind.Count) commit(s) from $($old.Substring(0,7))")
        $req = @(Invoke-Git diff --name-only $old HEAD -- requirements.txt | Where-Object { $_ })
        if ($req.Count -and (Test-Path $py)) {
            $pipOut = & $py -m pip install --quiet -r (Join-Path $Root "requirements.txt") 2>&1 | ForEach-Object { "$_" }
            $pipRc = $LASTEXITCODE
            if ($pipRc -ne 0) { $problems.Add("requirements.txt changed and pip install failed ($pipRc): " + (($pipOut | Select-Object -Last 2) -join ' ')) }
            else { $notes.Add("requirements.txt changed; pip install ok") }
        }
        $after = @(Invoke-Git rev-list HEAD..origin/main | Where-Object { $_ })
        if ($after.Count) { $problems.Add("still behind origin/main after fast-forward") }
    }
}

# Not behind (or just fast-forwarded): whatever deferral was running has ended.
if (-not @(Invoke-Git rev-list HEAD..origin/main | Where-Object { $_ }).Count -and (Test-Path $DeferPath)) {
    Remove-Item -Path $DeferPath -Force -ErrorAction SilentlyContinue
}
PermanentNotes
if ($problems.Count) { Finish 1 "DRIFT" }
Finish 0 "OK"
