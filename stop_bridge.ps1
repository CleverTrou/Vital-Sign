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
if (-not $targetPidText) {
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    exit 0
}
$targetPid = [int]$targetPidText

# Confirm this PID still belongs to our bridge.py before touching it --
# PIDs get reused, and killing whatever now holds this number without
# checking would terminate an unrelated process. Get-Process alone can't
# see the command line, so this goes through WMI.
$wmiProc = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction SilentlyContinue
if ($wmiProc -and $wmiProc.CommandLine -like "*bridge.py*") {
    Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
    # Give it a moment to actually exit before we remove the PID file below --
    # otherwise a start_bridge.ps1 racing in right after this would have no
    # way to tell the (still-dying) old process apart from "not running".
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 200
    }
}

Remove-Item $PidFile -ErrorAction SilentlyContinue
