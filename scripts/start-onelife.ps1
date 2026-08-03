# Bring OneLife online after a Windows sign-in.
#
# Docker Desktop only runs inside a user session, so the compose `restart:
# unless-stopped` policies are useless until the engine itself is up. This script
# starts Docker Desktop, waits for the engine to answer, then brings the stack up
# with the production overlay (see docker-compose.prod.yml). It is idempotent —
# already-running containers are left alone.
#
# Installed as the scheduled task "OneLife Autostart" (see install-autostart.ps1).
# Run it by hand any time to bring the site back:
#   powershell -ExecutionPolicy Bypass -File D:\source\OneLife\scripts\start-onelife.ps1

$ErrorActionPreference = 'Stop'

$repo      = Split-Path -Parent $PSScriptRoot
$dockerExe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$logDir    = Join-Path $env:LOCALAPPDATA 'OneLife'
$log       = Join-Path $logDir 'autostart.log'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 's'), $msg
    Add-Content -Path $log -Value $line
    Write-Output $line
}

# Keep the log from growing without bound across reboots.
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) {
    Move-Item $log "$log.old" -Force
}

Write-Log "=== OneLife autostart ==="

# 1. Docker Desktop -----------------------------------------------------------
if (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue) {
    Write-Log 'Docker Desktop already running.'
} elseif (Test-Path $dockerExe) {
    Write-Log 'Starting Docker Desktop...'
    Start-Process -FilePath $dockerExe
} else {
    Write-Log "ERROR: Docker Desktop not found at $dockerExe"
    exit 1
}

# 2. Wait for the engine ------------------------------------------------------
# A cold boot with the WSL2 backend can take a couple of minutes before the
# daemon accepts connections.
$deadline = (Get-Date).AddMinutes(10)
$ready = $false
while ((Get-Date) -lt $deadline) {
    docker info 2>$null 1>$null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $ready) {
    Write-Log 'ERROR: Docker engine did not become ready within 10 minutes.'
    exit 1
}
Write-Log 'Docker engine is ready.'

# 3. The stack ----------------------------------------------------------------
# The prod overlay is mandatory here: without it Caddy/duckdns never start and
# the api is built for localhost-only CORS, so remote access breaks.
Set-Location $repo
# 'Continue' while a native exe runs: compose writes its progress to stderr, and
# under 'Stop' PowerShell turns those lines into terminating NativeCommandErrors.
$ErrorActionPreference = 'Continue'
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d 2>&1 |
    ForEach-Object { Write-Log "$_" }
$composeExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'

if ($composeExit -ne 0) {
    Write-Log "ERROR: docker compose up exited $composeExit"
    exit $composeExit
}

# 4. Health check -------------------------------------------------------------
# Caddy fronts the api on :8082; the Mac mini proxies the public domain here.
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8082/api/health' -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { Write-Log "OneLife is live: $($r.Content)"; exit 0 }
    } catch { }
    Start-Sleep -Seconds 5
}
Write-Log 'WARNING: containers started but /api/health did not answer on :8082.'
exit 1
