#!/usr/bin/env bash
# Keeps an SSH reverse tunnel open from this computer to the one running
# OBS, so overlay.html there can keep using ws://localhost:PORT and still
# reach this bridge. The bridge stays here because Bluetooth only reaches a
# few metres; OBS can be anywhere SSH can reach (a cloud gaming PC, a second
# streaming PC).
#
# start_bridge.sh starts this when config.sh sets TUNNEL_HOST, and
# stop_bridge.sh stops it. By hand:  ./tunnel.sh HOST PORT [LOGFILE]
#
# Nothing listens on the network here or there: ssh -R makes the OTHER
# computer's localhost:PORT lead back to this one's, inside the SSH
# connection, which is why bridge.py can stay bound to localhost.
#
# Written for bash 3.2, which is what macOS runs this with.
set -uo pipefail

HOST="${1:-}"
PORT="${2:-}"
LOGFILE="${3:-/dev/stderr}"

# Overridable only so tests don't have to wait real minutes.
RETRY_MIN="${VITALSIGN_TUNNEL_RETRY_MIN:-5}"   # first retry delay, seconds
RETRY_MAX="${VITALSIGN_TUNNEL_RETRY_MAX:-60}"  # backoff cap
UP_AFTER="${VITALSIGN_TUNNEL_UP_AFTER:-15}"    # alive this long = connected

# Same shape as bridge.py's log lines, so the control app's log panel shows
# these and colors the warnings.
log() {
  printf '%s,000 %s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2" >> "$LOGFILE"
}

if [[ -z "$HOST" || "$HOST" == -* ]]; then
  # A leading '-' would be read as an ssh option, not a host.
  log ERROR "Tunnel: TUNNEL_HOST must be an ssh destination like user@host, got '$HOST'"
  exit 2
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
  log ERROR "Tunnel: port must be a number, got '$PORT'"
  exit 2
fi

SSH_PID=""
SLEEP_PID=""
ERRFILE="$(mktemp "${TMPDIR:-/tmp}/vitalsign-tunnel.XXXXXX")"

stop() {
  local pid
  for pid in $SSH_PID $SLEEP_PID; do
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
  done
  rm -f "$ERRFILE"
  log INFO "Tunnel to $HOST closed"
  exit 0
}
trap stop TERM INT HUP

# Interruptible sleep: bash runs a trap only between commands, so a plain
# `sleep 60` would hold off stop_bridge.sh for up to a minute.
pause() {
  sleep "$1" &
  SLEEP_PID=$!
  wait "$SLEEP_PID"
  SLEEP_PID=""
}

delay="$RETRY_MIN"
last_error=""
log INFO "Tunnel: connecting to $HOST so OBS there can reach ws://localhost:$PORT"
while true; do
  started=$SECONDS
  # BatchMode: never stop to ask for a password or passphrase; nobody is
  #   there to answer when OBS or the control app started this.
  # accept-new: remember a host the first time, refuse one whose key
  #   changed. A rebuilt cloud PC gets a new key; see the README.
  # ExitOnForwardFailure: exit rather than sit connected with no tunnel,
  #   e.g. when the port is still held by the previous connection.
  # ServerAlive*: notice within ~45s that the other end went away.
  ssh -N \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -o ConnectTimeout=10 \
    -R "$PORT:localhost:$PORT" \
    -- "$HOST" 2> "$ERRFILE" &
  SSH_PID=$!

  # ssh -N prints nothing once it's up, so "still running after
  # UP_AFTER seconds" is the signal: longer than ConnectTimeout, and
  # ExitOnForwardFailure makes a refused forward exit well before that.
  announced=""
  while kill -0 "$SSH_PID" 2>/dev/null; do
    if [[ -z "$announced" ]] && (( SECONDS - started >= UP_AFTER )); then
      log INFO "Tunnel open: OBS on $HOST can reach the overlay at ws://localhost:$PORT"
      announced=1
      last_error=""
      delay="$RETRY_MIN"
    fi
    pause 1
  done
  wait "$SSH_PID" 2>/dev/null
  status=$?
  SSH_PID=""

  # One line for the log: drop ssh's "Permanently added" notice and the
  # rows of @ around its changed-host-key warning, keep the words.
  error="$(grep -v -e '^Warning: Permanently added' -e '^@*$' "$ERRFILE" | tr '\n' ' ' | sed 's/ *$//')"
  [[ -n "$error" ]] || error="ssh exited with status $status"
  # Log each new failure once, not every retry: a cloud PC that's switched
  # off would otherwise fill the log with the same timeout all evening.
  if [[ -n "$announced" ]]; then
    log WARNING "Tunnel to $HOST dropped ($error); reconnecting"
  elif [[ "$error" != "$last_error" ]]; then
    log WARNING "Tunnel to $HOST failed: $error. Retrying until it connects."
  fi
  last_error="$error"

  pause "$delay"
  delay=$(( delay * 2 ))
  (( delay > RETRY_MAX )) && delay="$RETRY_MAX"
done
