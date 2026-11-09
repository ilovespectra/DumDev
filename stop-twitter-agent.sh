#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for service in agent monitor; do
  PID_FILE="$SCRIPT_DIR/logs/$service.pid"
  [ -f "$PID_FILE" ] || continue
  PID="$(cat "$PID_FILE")"
  case "$PID" in ''|*[!0-9]*) echo "Invalid PID file: $PID_FILE" >&2; exit 1;; esac
  if [ "$service" = agent ]; then
    SCRIPT="$SCRIPT_DIR/twitter_agent.py"
  else
    SCRIPT="$SCRIPT_DIR/monitor_server.py"
  fi
  COMMAND="$(ps -p "$PID" -o command= || true)"
  STATE="$(ps -p "$PID" -o state= || true)"
  if [[ "$STATE" == *Z* ]]; then
    rm -f "$PID_FILE"
    continue
  fi
  VERIFIED=false
  case "$COMMAND" in
    *"$SCRIPT"*|*"$SCRIPT_DIR/app.py"*) VERIFIED=true ;;
    *)
      if [ "$service" = monitor ] && [[ "$COMMAND" == *"monitor_server.py"* || "$COMMAND" == *" app.py"* ]]; then
        PROCESS_CWD="$(lsof -a -p "$PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
        [ "$PROCESS_CWD" = "$SCRIPT_DIR" ] && VERIFIED=true
      fi
      ;;
  esac
  if [ "$VERIFIED" = true ]; then
    kill "$PID"
    for _ in {1..50}; do
      kill -0 "$PID" 2>/dev/null || break
      STATE="$(ps -p "$PID" -o state= || true)"
      [[ "$STATE" == *Z* ]] && break
      sleep 0.1
    done
    STATE="$(ps -p "$PID" -o state= || true)"
    if kill -0 "$PID" 2>/dev/null && [[ "$STATE" != *Z* ]]; then
      echo "Process $PID has not exited; refusing to start a competing instance." >&2
      exit 1
    fi
    echo "Stopped $service ($PID)."
  elif [ -z "$COMMAND" ]; then
    :
  else
    echo "PID $PID belongs to another command; refusing to kill it." >&2
    exit 1
  fi
  rm -f "$PID_FILE"
done
