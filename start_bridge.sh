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
# alone only proves *something* is alive at that PID.
is_bridge_process() {
  ps -p "$1" -o command= -ww 2>/dev/null | grep -q "bridge\.py"
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
