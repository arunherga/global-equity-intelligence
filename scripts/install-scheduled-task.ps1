<#
.SYNOPSIS
    Register two Windows scheduled tasks that fire the intelligence workflow.

.DESCRIPTION
    Replaces GitHub's `schedule` trigger, which has never delivered for this
    repository. Two tasks are created, at 11:48 and 18:44 local time.

    -StartWhenAvailable is set, so a slot missed because the machine was
    asleep or switched off runs as soon as it is next available. That is
    strictly better than GitHub cron, which simply drops a missed tick.

    Run this once, from PowerShell. It does not need administrator rights:
    the tasks are registered for the current user only.

.EXAMPLE
    .\install-scheduled-task.ps1
    .\install-scheduled-task.ps1 -Remove
#>

[CmdletBinding()]
param(
    [string]   $MiddayTime  = '11:48',
    [string]   $EveningTime = '18:44',
    [string]   $TaskFolder  = 'GlobalEquityIntelligence',
    [switch]   $Remove
)

$ErrorActionPreference = 'Stop'

$triggerScript = Join-Path $PSScriptRoot 'trigger-workflow.ps1'
if (-not (Test-Path $triggerScript)) {
    throw "trigger-workflow.ps1 not found next to this script ($PSScriptRoot)"
}

$tasks = @(
    @{ Name = 'midday';  Time = $MiddayTime  },
    @{ Name = 'evening'; Time = $EveningTime }
)

foreach ($task in $tasks) {
    $taskPath = "\$TaskFolder\"
    $taskName = "gei-$($task.Name)"

    $existing = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false
        Write-Host "removed existing task $taskPath$taskName"
    }
    if ($Remove) { continue }

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $triggerScript)

    $trigger = New-ScheduledTaskTrigger -Daily -At $task.Time

    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath `
        -Action $action -Trigger $trigger -Settings $settings `
        -Description "Fire the global-equity-intelligence workflow ($($task.Name) slot)" | Out-Null

    Write-Host "registered $taskPath$taskName at $($task.Time) daily"
}

if ($Remove) {
    Write-Host 'all tasks removed.'
} else {
    Write-Host ''
    Write-Host 'Check them with:  Get-ScheduledTask -TaskPath "\GlobalEquityIntelligence\"'
    Write-Host 'Run one now with: Start-ScheduledTask -TaskName gei-midday -TaskPath "\GlobalEquityIntelligence\"'
    Write-Host 'Log:              $env:LOCALAPPDATA\global-equity-intelligence\trigger.log'
}
