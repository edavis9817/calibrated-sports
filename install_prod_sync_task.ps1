<#
  Register the scheduled task that keeps a production clone on origin/main.
  Unit a-69. The runbook is docs/runbooks/production-clone.md.

    .\install_prod_sync_task.ps1 -Root C:\...\code\prod\calibrated-sports -ExpectLogger
    .\install_prod_sync_task.ps1 -Root C:\...\code\prod\cs-cfb -NoLogger -TaskName "CalibratedSports Prod Sync (cfb)"
    .\install_prod_sync_task.ps1 -Root <clone> -WhatIf      print, register nothing

  Needs an elevated shell (an S4U task cannot be registered without one). Run it
  from any checkout: nothing it installs depends on where it was run from.

  THE TASK DOES NOT RUN THE CLONE'S OWN COPY OF prod_sync.ps1. It runs
  origin/main's, read out of the object store after the fetch. The reason is a
  deadlock that already happened once in miniature: the script that decides
  whether the clone may fast-forward lives in the clone, so a defect in that
  decision can never be repaired by merging the repair - the old copy refuses
  the fast-forward that would deliver the new one. The first prod_sync.ps1
  deferred for ever behind the Live snapshot loop, and a fixed copy on
  origin/main would have sat there unread. Reading the script from origin/main
  means merging a fix to the sync IS deploying it, the same as everything else.

  What it installs, both OUTSIDE every clone (a production clone's tree must
  stay clean, and neither file is the repository's to version):

    <parent of Root>\prod_sync_task.ps1     the bootstrap the task runs
    <parent of Root>\.prod_sync\            origin/main's prod_sync.ps1, rewritten every run

  The bootstrap is twenty lines and does one thing. If it ever has to change,
  run this installer again.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$TaskName = "CalibratedSports Prod Sync",
    [switch]$ExpectLogger, [switch]$NoLogger,
    [int]$Minute = 20,              # past every hour; off the hour, where the refresh and the prune start
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

if ($Remove) {
    if ($PSCmdlet.ShouldProcess($TaskName, "Unregister-ScheduledTask")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    return
}

$Root = (Resolve-Path $Root).Path.TrimEnd('\')
if (-not (Test-Path (Join-Path $Root ".git"))) { throw "$Root is not a git clone" }
$Home_ = Split-Path -Parent $Root
$Bootstrap = Join-Path $Home_ "prod_sync_task.ps1"

$body = @'
# Written by install_prod_sync_task.ps1 (calibrated-sports, unit a-69). Do not edit: re-run the installer.
# Runs origin/main's prod_sync.ps1 against a production clone - never the clone's own copy, which a
# defect in the sync could leave permanently stale. See the installer's header.
param([Parameter(Mandatory = $true)][string]$Root, [switch]$ExpectLogger, [switch]$NoLogger)
$ErrorActionPreference = 'Continue'
$work = Join-Path $PSScriptRoot ".prod_sync"
$null = New-Item -ItemType Directory -Force -Path $work
$script = Join-Path $work ((Split-Path -Leaf $Root) + ".prod_sync.ps1")
& git.exe -C $Root fetch --quiet origin 2>$null
# cmd's redirect, so the bytes are origin/main's; PowerShell's own would re-encode them.
cmd.exe /c "git.exe -C `"$Root`" show origin/main:prod_sync.ps1 > `"$script`" 2>nul"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $script) -or (Get-Item $script).Length -eq 0) {
    $script = Join-Path $Root "prod_sync.ps1"       # a check with the old script beats no check
}
$flags = @("-Update")
if ($ExpectLogger) { $flags += "-ExpectLogger" }
if ($NoLogger) { $flags += "-NoLogger" }
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script -Root $Root @flags
exit $LASTEXITCODE
'@

$flag = if ($ExpectLogger) { " -ExpectLogger" } elseif ($NoLogger) { " -NoLogger" } else { "" }
$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Bootstrap`" -Root `"$Root`"$flag"

Write-Host "task      $TaskName"
Write-Host "runs      powershell.exe $arguments"
Write-Host "every     hour at :$('{0:d2}' -f $Minute), S4U as $env:USERDOMAIN\$env:USERNAME"

if ($PSCmdlet.ShouldProcess($Bootstrap, "write the bootstrap")) {
    Set-Content -Path $Bootstrap -Value $body -Encoding ascii
}
if ($PSCmdlet.ShouldProcess($TaskName, "Register-ScheduledTask")) {
    $start = (Get-Date).Date.AddHours((Get-Date).Hour).AddMinutes($Minute)
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $Home_
    $trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
    $null = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Force -Description ("a-69: keeps $Root on origin/main. Runs origin/main's prod_sync.ps1 -Update " +
        "hourly. Last Run Result 0 = in sync (or a short deferral), 1 = DRIFT, 2 = could not check. " +
        "Log: <STORAGE_DIR>\logs\prod_sync.log")
    Write-Host "registered. First run $start; run it now with: Start-ScheduledTask -TaskName `"$TaskName`""
}
