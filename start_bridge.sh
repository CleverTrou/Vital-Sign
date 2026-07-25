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

# kill -0 just probes whether the PID is alive; it doesn't verify the PID
# still belongs to *our* process (PIDs get reused). Good enough here since
# nothing else on a personal machine is likely to reuse this PID within a
# streaming session -- but worth knowing if you ever run this multi-user.
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
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
