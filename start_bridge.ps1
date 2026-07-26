# Idempotent start for bridge.py. Safe to call repeatedly -- e.g. from
# Advanced Scene Switcher's "Run" action every time its condition re-checks --
# without spawning duplicate BLE connections.
#
# Windows-specific notes vs. start_bridge.sh (untested against real Windows
# hardware as of writing -- see README's Platform support section):
#   - No POSIX signals: stop_bridge.ps1 force-terminates the process rather
#     than requesting the graceful shutdown start_bridge.sh gets via SIGTERM.
#     bridge.py's cleanup (closing the BLE link, closing the WebSocket
#     server) is skipped, but nothing depends on it running -- the OS
#     reclaims both on process exit either way.
#   - Start-Process's stdout/stderr redirection overwrites its target file
#     each time rather than appending, unlike bash's `>>`. bridge.py logs
#     everything through Python's `logging` module, which defaults to
#     stderr, so bridge.log (stderr) has everything that matters;
#     bridge.out.log (stdout) is normally empty and mostly exists so the
#     redirect has somewhere to go.

$ErrorActionPreference = "Stop"

# Resolve our own directory rather than trusting the working directory,
# since OBS/Advanced Scene Switcher invokes this script from OBS's working
# directory, not this project's.
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir

# config.ps1 is entirely optional -- bridge.py auto-discovers the ring over
# BLE if $Address is unset, so most people never need this file. Only dot
# source it if it exists.
$Address = $null
$WsPort = 8765
$ConfigPath = Join-Path $Dir "config.ps1"
if (Test-Path $ConfigPath) {
    . $ConfigPath
}

$PidFile = Join-Path $Dir ".bridge.pid"
$LogFile = Join-Path $Dir "bridge.log"
$OutLogFile = Join-Path $Dir "bridge.out.log"
$PythonExe = Join-Path $Dir ".venv\Scripts\python.exe"

# Confirms a PID is actually still our bridge.py, not some unrelated
# process that happened to reuse the number after the original exited --
# Get-Process alone can't see the command line, so this goes through WMI.
# Match the full, repo-specific bridge.py path, not just the bare
# "bridge.py" substring, which any unrelated bridge.py elsewhere on the
# system could also satisfy (confirmed on macOS's bash equivalent by
# testing against a decoy bridge.py in another directory). Deliberately
# NOT also requiring $PythonExe in the command line: on macOS, `ps` shows
# a venv symlink's *resolved* target rather than the invoked symlink path,
# breaking an equivalent check there. Windows venvs copy python.exe rather
# than symlink it, so this specific failure mode may not apply here --
# but that's unverified against real Windows, so this keeps the same
# simpler, already-proven-sufficient check on both platforms rather than
# assume.
function Test-IsBridgeProcess([int]$ProcessId) {
    $wmiProc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $wmiProc) { return $false }
    $expectedScript = Join-Path $Dir "bridge.py"
    return $wmiProc.CommandLine -like "*$expectedScript*"
}

if (Test-Path $PidFile) {
    $existingPidText = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($existingPidText -and (Test-IsBridgeProcess ([int]$existingPidText))) {
        exit 0  # already running
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

# Each element must be a single whitespace-free token: Start-Process's
# -ArgumentList silently mis-splits any element that itself contains a
# space (confirmed by testing -- e.g. @("-c", "print('hi there')") arrives
# at the child process as separate "print('hi" and "there')" arguments).
# Not a concern for the fixed flags below or a BLE address/UUID (neither
# format contains spaces), but keep it in mind if you add your own args.
$ArgList = @("bridge.py", "--ws-port", $WsPort)
if ($Address) {
    $ArgList += @("--address", $Address)
}

$proc = Start-Process -FilePath $PythonExe -ArgumentList $ArgList `
    -WorkingDirectory $Dir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $OutLogFile -RedirectStandardError $LogFile

$proc.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
