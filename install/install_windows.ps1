<#
    fable-5-hunter -- Windows Autostart Installer
    ==============================================
    Registers a Scheduled Task that starts the hunter at every login,
    runs it hidden in the background (pythonw) and restarts it on crash.
    Survives system reboots. No administrator rights required for AtLogOn.

    Usage (PowerShell, no admin needed):
        powershell -ExecutionPolicy Bypass -File install\install_windows.ps1
    Uninstall:
        powershell -ExecutionPolicy Bypass -File install\install_windows.ps1 -Uninstall
#>
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'Fable5Hunter'

# Robust path resolution: prefer $PSScriptRoot (= install/), otherwise derive
# from the invocation path. ScriptDir is the project root (one level up).
$Here = $PSScriptRoot
if (-not $Here) { $Here = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ScriptDir = Split-Path -Parent $Here
$Script    = Join-Path $ScriptDir 'fable_hunter.py'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Task '$TaskName' removed."
    } else {
        Write-Host "Task '$TaskName' was not registered."
    }
    return
}

# Prefer pythonw (no console window); fall back to python
$PyW = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $PyW) { $PyW = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $PyW) { throw "Python not found in PATH." }
if (-not (Test-Path $Script)) { throw "Script not found: $Script" }

Write-Host "Python : $PyW"
Write-Host "Script : $Script"

$action   = New-ScheduledTaskAction -Execute $PyW `
                -Argument "`"$Script`" run" -WorkingDirectory $ScriptDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -RestartCount 999 `
                -RestartInterval (New-TimeSpan -Minutes 2) `
                -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings `
    -Description 'Watches for Claude Fable 5 availability and notifies when back.' `
    -Force | Out-Null

Write-Host "Task '$TaskName' registered (starts at login)."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2
$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host "Task state: $state"
if ($state -eq 'Running') {
    Write-Host "OK — hunter is running. Check status: python `"$Script`" status"
} else {
    Write-Warning "Task is not running yet. Inspect: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
}
