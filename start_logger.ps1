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
#>
param([switch]$Stop, [switch]$Status, [switch]$Restart, [switch]$Force)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py   = Join-Path $Root ".venv\Scripts\python.exe"

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

function Show {
    $p = @(Procs)
    if ($p.Count -eq 0) { Write-Host "logger: NOT RUNNING" -ForegroundColor Red }
    else {
        $c = "Green"; if ($p.Count -gt 2) { $c = "Yellow" }
        Write-Host "logger: RUNNING ($($p.Count) processes)" -ForegroundColor $c
        $p | ForEach-Object { Write-Host ("  pid {0}  {1}" -f $_.ProcessId, $_.CreationDate) }
        if ($p.Count -gt 2) { Write-Host "  WARNING: duplicate logger" -ForegroundColor Yellow }
    }
    Write-Host "log:    $Log"
    if (Test-Path $Log) {
        $age = [math]::Round(((Get-Date) - (Get-Item $Log).LastWriteTime).TotalSeconds)
        Write-Host "        last written ${age}s ago"
        if ($age -gt 180) { Write-Host "        WARNING: stale - alive but wedged?" -ForegroundColor Yellow }
        Write-Host ""; Get-Content $Log -Tail 6
    }
}

function Halt {
    $p = @(Procs)
    if ($p.Count -eq 0) { Write-Host "nothing running."; return }
    $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i=0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 250
        if (@(Procs).Count -eq 0) { Write-Host "stopped."; return }
    }
    Write-Host "WARNING: still present after 5s" -ForegroundColor Yellow
}

function Go {
    if (-not $Force -and @(Procs).Count -gt 0) {
        Write-Host "REFUSING - already running. Use -Restart." -ForegroundColor Yellow; Show; return
    }
    if (-not (Test-Path $Py)) { Write-Host "venv missing: $Py" -ForegroundColor Red; return }
    $a = '/c ""{0}" "run_logger.py" >> "{1}" 2>&1"' -f $Py, $Log
    Start-Process cmd -ArgumentList $a -WorkingDirectory $Root -WindowStyle Hidden
    Start-Sleep -Seconds 6; Write-Host ""; Show
}

if ($Status)  { Show; exit }
if ($Stop)    { Halt; exit }
if ($Restart) { Halt; Start-Sleep -Seconds 1; Go; exit }
Go