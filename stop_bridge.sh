#!/usr/bin/env bash
# Stops the bridge.py process started by start_bridge.sh. Safe to call
# repeatedly, or when nothing is running.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/.bridge.pid"

[[ -f "$PIDFILE" ]] || exit 0
PID="$(cat "$PIDFILE")"

# Confirm this PID still belongs to our bridge.py before touching it --
# PIDs get reused, and killing whatever now holds this number without
# checking would terminate an unrelated process. Match the full,
# repo-specific bridge.py path, not just the bare "bridge.py" substring
# (which any unrelated bridge.py elsewhere on the system could also
# satisfy). Deliberately NOT also requiring "$DIR/.venv/bin/python3" in
# the command: macOS's `ps` shows the venv symlink's *resolved* target
# (the real Python.framework binary), not the invoked symlink path, so
# that half of the check could never match -- confirmed by testing.
CMD="$(ps -p "$PID" -o command= -ww 2>/dev/null || true)"
if [[ -z "$CMD" || "$CMD" != *"$DIR/bridge.py"* ]]; then
  # Not our process -- either already gone, or the PID was reused by
  # something else. Nothing of ours to stop; safe to clear the file.
  rm -f "$PIDFILE"
  exit 0
fi

kill "$PID" 2>/dev/null || true          # SIGTERM: let bridge.py close the BLE link and WebSocket server cleanly
for _ in $(seq 1 20); do                 # give it up to ~4s to exit on its own
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.2
done
kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null || true  # still alive -- force it

if kill -0 "$PID" 2>/dev/null; then
  # Still alive even after -9 (exceedingly rare -- e.g. an uninterruptible
  # syscall) -- leave the PID file in place so start_bridge.sh doesn't spawn
  # a second instance while this one still holds the BLE connection and
  # WebSocket port.
  echo "Failed to stop bridge process $PID" >&2
  exit 1
fi

rm -f "$PIDFILE"
