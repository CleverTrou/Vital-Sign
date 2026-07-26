#!/usr/bin/env bash
# Idempotent start for bridge.py. Safe to call repeatedly -- e.g. from
# Advanced Scene Switcher's "Run" action every time its condition re-checks --
# without spawning duplicate BLE connections.
set -euo pipefail

# Resolve our own directory rather than trusting $PWD, since OBS/Advanced
# Scene Switcher invokes this script from OBS's working directory, not
# this project's.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# config.sh is entirely optional -- bridge.py auto-discovers the ring over
# BLE if ADDRESS is unset, so most people never need this file. Only
# source it if it exists.
ADDRESS=""
WS_PORT="8765"
if [[ -f "$DIR/config.sh" ]]; then
  # shellcheck source=config.sh
  source "$DIR/config.sh"
fi

PIDFILE="$DIR/.bridge.pid"
LOGFILE="$DIR/bridge.log"

# Confirms a PID is actually still our bridge.py, not an unrelated process
# that happened to reuse the number after the original exited -- kill -0
# alone only proves *something* is alive at that PID. Match the full,
# repo-specific bridge.py path, not just the bare "bridge.py" substring,
# which any unrelated bridge.py elsewhere on the system could also satisfy
# (confirmed by testing against a decoy bridge.py in another directory).
# Deliberately NOT also requiring "$DIR/.venv/bin/python3" in the command:
# macOS's `ps` shows the venv symlink's *resolved* target (the real
# Python.framework binary), not the invoked symlink path, so that half of
# the check could never match -- confirmed by testing.
is_bridge_process() {
  local cmd
  cmd="$(ps -p "$1" -o command= -ww 2>/dev/null)" || return 1
  [[ -n "$cmd" && "$cmd" == *"$DIR/bridge.py"* ]]
}

if [[ -f "$PIDFILE" ]] && is_bridge_process "$(cat "$PIDFILE")"; then
  exit 0  # already running
fi
rm -f "$PIDFILE"

ARGS=(--ws-port "$WS_PORT")
if [[ -n "$ADDRESS" ]]; then
  ARGS+=(--address "$ADDRESS")
fi

nohup "$DIR/.venv/bin/python3" "$DIR/bridge.py" "${ARGS[@]}" \
  >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
disown  # detach from this shell so the bridge outlives the ASS/OBS process tree that spawned it
