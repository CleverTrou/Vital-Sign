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

if [[ ! -f "$DIR/config.sh" ]]; then
  echo "config.sh not found -- copy config.sh.example to config.sh and fill in your device address." >&2
  exit 1
fi
# shellcheck source=config.sh
source "$DIR/config.sh"

PIDFILE="$DIR/.bridge.pid"
LOGFILE="$DIR/bridge.log"
WS_PORT="${WS_PORT:-8765}"

# kill -0 just probes whether the PID is alive; it doesn't verify the PID
# still belongs to *our* process (PIDs get reused). Good enough here since
# nothing else on a personal machine is likely to reuse this PID within a
# streaming session -- but worth knowing if you ever run this multi-user.
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  exit 0  # already running
fi
rm -f "$PIDFILE"

nohup "$DIR/.venv/bin/python3" "$DIR/bridge.py" --address "$ADDRESS" --ws-port "$WS_PORT" \
  >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
disown  # detach from this shell so the bridge outlives the ASS/OBS process tree that spawned it
