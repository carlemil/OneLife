# Lock the workstation right after an automatic sign-in.
#
# Automatic sign-in exists only so Docker Desktop gets a user session after an
# unattended reboot — the desktop itself should not sit there unlocked. This runs
# at logon and locks immediately, but ONLY when the sign-in looks automatic
# (i.e. it happened within a few minutes of boot). A deliberate sign-in later in
# the day is left alone, so you don't get locked out of a session you just opened.
#
# Installed as the scheduled task "OneLife Lock After Autologon".

$ErrorActionPreference = 'Stop'

$log = Join-Path $env:LOCALAPPDATA 'OneLife\autostart.log'
$uptime = (Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime

function Write-Log($msg) {
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 's'), $msg)
}

if ($uptime.TotalMinutes -lt 5) {
    Write-Log ("Locking workstation ({0:n1} min after boot -> automatic sign-in)." -f $uptime.TotalMinutes)
    rundll32.exe user32.dll,LockWorkStation
} else {
    Write-Log ("Not locking: signed in {0:n1} min after boot -> looks deliberate." -f $uptime.TotalMinutes)
}
