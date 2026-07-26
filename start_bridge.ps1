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
#
# Checks two WMI properties together rather than one path match, because
# neither alone is both safe and correct:
#   - CommandLine preserves arguments exactly as passed to Start-Process
#     below, which is the *relative* "bridge.py" (see the -ArgumentList
#     comment) -- so matching CommandLine against the *absolute* bridge.py
#     path (an earlier version of this check) can never match a real,
#     legitimately-running instance at all. Matching CommandLine against
#     just the bare "bridge.py" substring, on its own, is too loose --
#     any unrelated bridge.py elsewhere on the system would also satisfy
#     it (confirmed on macOS's bash equivalent via a decoy bridge.py in
#     another directory).
#   - ExecutablePath is the resolved, absolute path to the binary that was
#     actually launched (unlike CommandLine's arguments, this isn't
#     preserved "as typed") -- reliable here since Windows venvs copy
#     python.exe rather than symlink it (macOS's bash equivalent can't use
#     the analogous check for exactly that reason: `ps` there shows a venv
#     symlink's *resolved* target, not the invoked path).
# Together: only a python.exe from *this* venv, invoked with "bridge.py"
# somewhere in its arguments, counts -- specific enough without needing to
# risk passing an absolute, possibly space-containing path as a
# Start-Process argument.
function Test-IsBridgeProcess([int]$ProcessId) {
    $wmiProc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $wmiProc) { return $false }
    return $wmiProc.ExecutablePath -and $wmiProc.ExecutablePath -ieq $PythonExe -and $wmiProc.CommandLine -like "*bridge.py*"
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
