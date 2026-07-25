#!/usr/bin/env bash
# Stops the bridge.py process started by start_bridge.sh. Safe to call
# repeatedly, or when nothing is running.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/.bridge.pid"

[[ -f "$PIDFILE" ]] || exit 0
PID="$(cat "$PIDFILE")"

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null || true          # SIGTERM: let bridge.py close the BLE link and WebSocket server cleanly
  for _ in $(seq 1 20); do                 # give it up to ~4s to exit on its own
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.2
  done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null || true  # still alive -- force it
fi

rm -f "$PIDFILE"
