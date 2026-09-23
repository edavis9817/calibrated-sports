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

  Exit codes - Task Scheduler shows them as Last Run Result:
    0  OK (or an -Update deferred because a job is running from the clone)
    1  DRIFT - read the log line; nothing was changed
    2  could not check (git fetch failed, not a git clone)

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
param([string]$Root = $PSScriptRoot, [switch]$Update, [string]$LogPath, [switch]$NoLogger, [switch]$ExpectLogger)

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

# --- 5. behind: report, or fast-forward -------------------------------------
if ($behind.Count) {
    if (-not $Update) {
        $problems.Add("behind origin/main by $($behind.Count) commit(s)")
    } elseif ($problems.Count) {
        $problems.Add("behind origin/main by $($behind.Count) commit(s); NOT fast-forwarded because of the above")
    } else {
        # A job importing modules while the tree moves under it can load two
        # revisions at once. The logger is exempt: it runs permanently and
        # picks new code up only on restart (the log says which it runs).
        $busy = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith(($Root + '\.venv\').ToLower()) -and
            -not ($_.CommandLine -and $_.CommandLine -match 'run_logger\.py')
        })
        if ($busy.Count) {
            $notes.Add("fast-forward DEFERRED: $($busy.Count) process(es) running from this clone (pid " +
                (($busy | ForEach-Object { $_.ProcessId }) -join ',') + ")")
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

if ($problems.Count) { Finish 1 "DRIFT" }
Finish 0 "OK"
