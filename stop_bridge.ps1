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
$PythonExe = Join-Path $Dir ".venv\Scripts\python.exe"

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
# see the command line, so this goes through WMI. See the matching
# Test-IsBridgeProcess comment in start_bridge.ps1 for why this checks
# both ExecutablePath and a bare CommandLine substring, rather than
# matching CommandLine against the absolute bridge.py path (which never
# matches -- the script is launched with a relative argument) or the bare
# substring alone (too loose on its own).
$wmiProc = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction SilentlyContinue
$isOurs = $wmiProc -and $wmiProc.ExecutablePath -and $wmiProc.ExecutablePath -ieq $PythonExe -and $wmiProc.CommandLine -like "*bridge.py*"

if (-not $isOurs) {
    # Not our process -- either already gone, or the PID was reused by
    # something else. Nothing of ours to stop; safe to clear the file.
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    exit 0
}

Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 20; $i++) {
    if (-not (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 200
}

if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {
    # Still alive after Force + ~4s -- leave the PID file in place so
    # start_bridge.ps1 doesn't spawn a second instance while this one
    # still holds the BLE connection and WebSocket port.
    Write-Error "Failed to stop bridge process $targetPid"
    exit 1
}

Remove-Item $PidFile -ErrorAction SilentlyContinue
