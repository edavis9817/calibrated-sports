<#
  Start/stop the Calibrated Sports logger. Three guards, one per failure
  already hit: venv interpreter by path (system python dies on import),
  append-redirect through cmd (PowerShell's redirect truncates), and a
  refusal to start when one is already running (duplicate loggers both
  ping healthchecks, which defeats the dead-man switch).

    .\start_logger.ps1            start
    .\start_logger.ps1 -Status    what's running + last lines
    .\start_logger.ps1 -Stop      stop and wait for exit
    .\start_logger.ps1 -Restart   stop, wait, start
    .\start_logger.ps1 -Ensure    start ONLY if it is not running; silent and
                                  exit 0 when it is. The watchdog task runs this
                                  every 2 minutes - it is the automatic restart.

  HOW "RUNNING" IS DECIDED (a-67). It used to be a scan of process command
  lines, and Win32_Process.CommandLine comes back EMPTY for a logger started in
  another session, so -Status printed NOT RUNNING beside a logger that was
  capturing - and a brief that said "restart it if status says down" would have
  restarted a healthy logger mid-slate. Three sources now, strongest first, and
  the answer always names which one it came from:

    1. the single-instance lock (core/single_instance.py). The kernel holds it
       for exactly as long as the logger lives, and it reads the same from
       every shell. This is the truth when it can be asked.
    2. the command-line scan, when the lock cannot be asked (no venv).
    3. the age of logger.log, when neither can: written in the last 3 minutes
       means running. A wedged logger reads as stopped here, which is the safe
       way round for a status line and why -Ensure never acts on this one.
#>
param([switch]$Stop, [switch]$Status, [switch]$Restart, [switch]$Force, [switch]$Ensure)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py   = Join-Path $Root ".venv\Scripts\python.exe"
$StaleAfter = 180

# Log lives beside the database, wherever LOGGER_DB points - so it follows
# the data across disks instead of reappearing on C:.
$dir = Join-Path $Root "data"
$envf = Join-Path $Root ".env"
if (Test-Path $envf) {
    $m = Select-String -Path $envf -Pattern '^\s*LOGGER_DB\s*=\s*(.+?)\s*$' | Select-Object -Last 1
    if ($m) { $dir = Split-Path -Parent ($m.Matches[0].Groups[1].Value -replace '/','\') }
}
$Log = Join-Path $dir "logger.log"

function Procs {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'run_logger\.py' }
}

# Ask the OS lock. $null when it cannot be asked at all (no venv, import
# failure) - which is a different answer from "not held" and is kept apart.
function LockState {
    if (-not (Test-Path $Py)) { return $null }
    Push-Location $Root
    try { $out = & $Py -m core.single_instance --held run_logger 2>$null; $rc = $LASTEXITCODE }
    catch { $out = $null; $rc = -1 }
    finally { Pop-Location }
    if ($rc -ne 0 -and $rc -ne 1) { return $null }
    try { $rec = ($out | Select-Object -Last 1) | ConvertFrom-Json } catch { return $null }
    if ($null -eq $rec -or $null -eq $rec.held) { return $null }
    return $rec
}

function LogAge {
    if (-not (Test-Path $Log)) { return $null }
    return [math]::Round(((Get-Date) - (Get-Item $Log).LastWriteTime).TotalSeconds)
}

# One answer, with where it came from. Certain = the lock answered.
function Liveness {
    $lock = LockState
    $p = @(Procs)
    if ($null -ne $lock) {
        $pids = @(); if ($lock.held -and $lock.pid) { $pids += [int]$lock.pid }
        return [pscustomobject]@{ Running = [bool]$lock.held; Certain = $true; How = "single-instance lock"
                                  Pids = $pids; Lock = $lock; Procs = $p }
    }
    if ($p.Count -gt 0) {
        return [pscustomobject]@{ Running = $true; Certain = $false; How = "process scan (lock could not be asked)"
                                  Pids = @($p | ForEach-Object { [int]$_.ProcessId }); Lock = $null; Procs = $p }
    }
    $age = LogAge
    $alive = ($null -ne $age -and $age -le $StaleAfter)
    return [pscustomobject]@{ Running = $alive; Certain = $false
                              How = "age of logger.log (lock could not be asked, no command line readable)"
                              Pids = @(); Lock = $null; Procs = $p }
}

function Show {
    $s = Liveness
    if ($s.Running) {
        Write-Host "logger: RUNNING  [by $($s.How)]" -ForegroundColor Green
        if ($s.Lock) { Write-Host ("  pid {0}  since {1}  {2}" -f $s.Lock.pid, $s.Lock.started_iso, $s.Lock.executable) }
        $s.Procs | ForEach-Object { Write-Host ("  pid {0}  {1}" -f $_.ProcessId, $_.CreationDate) }
        if ($s.Procs.Count -gt 2) { Write-Host "  WARNING: duplicate logger" -ForegroundColor Yellow }
    } else {
        Write-Host "logger: NOT RUNNING  [by $($s.How)]" -ForegroundColor Red
        if ($s.Lock -and $s.Lock.pid) { Write-Host ("  last holder: pid {0}, started {1}" -f $s.Lock.pid, $s.Lock.started_iso) }
        if (-not $s.Certain) { Write-Host "  UNCERTAIN: the lock could not be asked - do not restart on this line alone" -ForegroundColor Yellow }
    }
    Write-Host "log:    $Log"
    $age = LogAge
    if ($null -ne $age) {
        Write-Host "        last written ${age}s ago"
        if ($s.Running -and $age -gt $StaleAfter) { Write-Host "        WARNING: stale - alive but wedged?" -ForegroundColor Yellow }
        Write-Host ""; Get-Content $Log -Tail 6
    }
}

function Halt {
    $s = Liveness
    $targets = @($s.Pids) + @($s.Procs | ForEach-Object { [int]$_.ProcessId }) | Sort-Object -Unique
    if (-not $s.Running -and $targets.Count -eq 0) { Write-Host "nothing running."; return }
    $targets | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    for ($i=0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        $now = Liveness
        if (-not $now.Running -and @($now.Procs).Count -eq 0) { Write-Host "stopped."; return }
    }
    Write-Host "WARNING: still present after 10s (a logger in another session needs an elevated shell)" -ForegroundColor Yellow
}

function Launch {
    if (-not (Test-Path $Py)) { Write-Host "venv missing: $Py" -ForegroundColor Red; return $false }
    $a = '/c ""{0}" "run_logger.py" >> "{1}" 2>&1"' -f $Py, $Log
    Start-Process cmd -ArgumentList $a -WorkingDirectory $Root -WindowStyle Hidden
    return $true
}

function Go {
    if (-not $Force -and (Liveness).Running) {
        Write-Host "REFUSING - already running. Use -Restart." -ForegroundColor Yellow; Show; return
    }
    if (Launch) { Start-Sleep -Seconds 6; Write-Host ""; Show }
}

# The automatic restart. Acts ONLY on the lock: an uncertain "not running" is
# not grounds to start anything, and run_logger.py would refuse a duplicate
# anyway - but a refusal writes to logger.log, and a watchdog that touched the
# log every pass would make the log-age fallback above lie.
function EnsureRunning {
    $s = Liveness
    if ($s.Running) { exit 0 }
    $wlog = Join-Path (Join-Path $dir "logs") "logger_watchdog.log"
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $wlog)
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    if (-not $s.Certain) {
        Add-Content -Path $wlog -Encoding utf8 -Value "$stamp UNCERTAIN lock could not be asked from $Root; nothing started"
        exit 2
    }
    $age = LogAge
    Add-Content -Path $wlog -Encoding utf8 -Value "$stamp DOWN logger not running (log last written ${age}s ago, last holder pid $($s.Lock.pid)); starting from $Root"
    if (-not (Launch)) { Add-Content -Path $wlog -Encoding utf8 -Value "$stamp FAILED venv missing: $Py"; exit 1 }
    Start-Sleep -Seconds 15
    $after = Liveness
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    if ($after.Running) { Add-Content -Path $wlog -Encoding utf8 -Value "$stamp STARTED pid $($after.Lock.pid)"; exit 0 }
    Add-Content -Path $wlog -Encoding utf8 -Value "$stamp FAILED the logger did not take the lock within 15s - read the end of $Log"
    exit 1
}

if ($Ensure)  { EnsureRunning }
if ($Status)  { Show; exit }
if ($Stop)    { Halt; exit }
if ($Restart) { Halt; Start-Sleep -Seconds 1; Go; exit }
Go
