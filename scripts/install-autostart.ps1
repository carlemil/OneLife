# Register the "OneLife Autostart" scheduled task (idempotent — re-run to update).
#
# Runs scripts\start-onelife.ps1 at sign-in, so Docker Desktop and the OneLife
# stack come back on their own after a Windows reboot. Run this once, elevated:
#   powershell -ExecutionPolicy Bypass -File D:\source\OneLife\scripts\install-autostart.ps1
#
# NOTE: Docker Desktop needs a logged-in user session. If the machine reboots
# unattended (e.g. Windows Update at night) the site stays down until someone
# signs in — enable automatic sign-in as well if that matters.

$ErrorActionPreference = 'Stop'

$taskName = 'OneLife Autostart'
$script   = Join-Path $PSScriptRoot 'start-onelife.ps1'
$user     = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""

# 15s of slack so the shell/session is settled before Docker Desktop launches.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT15S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'Starts Docker Desktop and brings the OneLife stack up (prod overlay) after sign-in.' `
    -Force | Out-Null

Write-Output "Registered scheduled task '$taskName' for $user."

# Companion task: automatic sign-in leaves the desktop unlocked, so lock it again
# straight away (only when the sign-in was in fact automatic — see the script).
$lockName    = 'OneLife Lock After Autologon'
$lockScript  = Join-Path $PSScriptRoot 'lock-after-autologon.ps1'
$lockAction  = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$lockScript`""
$lockTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$lockTrigger.Delay = 'PT20S'
$lockSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $lockName -Action $lockAction -Trigger $lockTrigger `
    -Settings $lockSettings -Principal $principal `
    -Description 'Locks the workstation after an automatic sign-in (Docker Desktop needs the session, not an open desktop).' `
    -Force | Out-Null

Write-Output "Registered scheduled task '$lockName' for $user."
