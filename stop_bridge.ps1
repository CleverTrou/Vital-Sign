# Stops the bridge.py process started by start_bridge.ps1. Safe to call
# repeatedly, or when nothing is running.
#
# There's no Windows equivalent of sending SIGTERM and letting bridge.py
# shut down gracefully (see the note in start_bridge.ps1) -- this just
# force-terminates the process. That's fine here: nothing bridge.py does
# on shutdown is required for correctness on the next start.

$ErrorActionPreference = "Stop"

$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Dir ".bridge.pid"

if (-not (Test-Path $PidFile)) {
    exit 0
}

$targetPidText = Get-Content $PidFile -ErrorAction SilentlyContinue
Remove-Item $PidFile -ErrorAction SilentlyContinue

if (-not $targetPidText) {
    exit 0
}

$proc = Get-Process -Id ([int]$targetPidText) -ErrorAction SilentlyContinue
if ($proc) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}
