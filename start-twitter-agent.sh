#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
FOREGROUND=false
if [ "${1:-}" = "--foreground" ]; then
  FOREGROUND=true
  shift
fi
[ -x "$PYTHON" ] || { echo "Missing .venv/bin/python; install requirements.txt in the project virtualenv first." >&2; exit 1; }
BROWSER="${TWITTER_AGENT_BROWSER:-brave}"
case "$BROWSER" in
  brave|chrome) ;;
  *) echo "TWITTER_AGENT_BROWSER must be brave or chrome." >&2; exit 1 ;;
esac
PROFILE="$SCRIPT_DIR/browser-profile-$BROWSER"
MODE="playwright"
EXTRA_ARGS=()
if [ "${1:-}" = "--arc" ] || [ "${1:-}" = "--arc-replies" ] || [ "${1:-}" = "--arc-campaign" ]; then
  MODE="arc"
  if [ "${1:-}" = "--arc-replies" ]; then
    EXTRA_ARGS+=(--discover-reply)
  elif [ "${1:-}" = "--arc-campaign" ]; then
    EXTRA_ARGS+=(--campaign --supervise)
  fi
  if [ ! -x "$SCRIPT_DIR/takeover-helper" ] || [ "$SCRIPT_DIR/takeover.swift" -nt "$SCRIPT_DIR/takeover-helper" ]; then
    swiftc "$SCRIPT_DIR/takeover.swift" -o "$SCRIPT_DIR/takeover-helper"
  fi
fi
mkdir -p "$LOG_DIR"
bash "$SCRIPT_DIR/stop-twitter-agent.sh"

AGENT_PID=""
MONITOR_PID=""
cleanup() {
  for service in agent monitor; do
    if [ "$service" = agent ]; then
      PID="$AGENT_PID"
    else
      PID="$MONITOR_PID"
    fi
    [ -n "$PID" ] || continue
    if kill -0 "$PID" 2>/dev/null; then
      kill "$PID" 2>/dev/null || true
    fi
    wait "$PID" 2>/dev/null || true
    PID_FILE="$LOG_DIR/$service.pid"
    if [ -f "$PID_FILE" ] && [ "$(cat "$PID_FILE")" = "$PID" ]; then
      rm -f "$PID_FILE"
    fi
  done
}
if [ "$FOREGROUND" = true ]; then
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
fi
if [ "${1:-}" = "--login" ]; then
  exec "$PYTHON" -u "$SCRIPT_DIR/twitter_agent.py" --login \
    --browser "$BROWSER" --user-data-dir "$PROFILE"
fi

"$PYTHON" -u "$SCRIPT_DIR/monitor_server.py" > "$LOG_DIR/monitor-server.out" 2>&1 &
MONITOR_PID="$!"
echo "$MONITOR_PID" > "$LOG_DIR/monitor.pid"
"$PYTHON" -u "$SCRIPT_DIR/twitter_agent.py" \
  --repo "${TWITTER_AGENT_REPO:-$SCRIPT_DIR/../CruiseControl}" \
  --post-mode "$MODE" \
  --browser "$BROWSER" --user-data-dir "$PROFILE" \
  --status-file "$LOG_DIR/status.json" \
  --settings-file "$LOG_DIR/settings.json" \
  "${EXTRA_ARGS[@]}" \
  --loop > "$LOG_DIR/twitter-agent.out" 2>&1 &
AGENT_PID="$!"
echo "$AGENT_PID" > "$LOG_DIR/agent.pid"
for _ in {1..50}; do
  if ! kill -0 "$MONITOR_PID" 2>/dev/null || ! kill -0 "$AGENT_PID" 2>/dev/null; then
    echo "Startup failed. See logs/monitor-server.out and logs/twitter-agent.out." >&2
    tail -n 20 "$LOG_DIR/monitor-server.out" "$LOG_DIR/twitter-agent.out" >&2
    cleanup
    exit 1
  fi
  if curl --fail --silent --max-time 1 http://127.0.0.1:8765/control > /dev/null; then
    break
  fi
  sleep 0.1
done
curl --fail --silent --max-time 2 http://127.0.0.1:8765/control > /dev/null || {
  echo "Dashboard did not become ready at http://127.0.0.1:8765." >&2
  cleanup
  exit 1
}
echo "Dashboard: http://127.0.0.1:8765"
if [ "$MODE" = arc ]; then
  echo "Automatic posts/replies enabled. Arc announces control before acting; Escape stops control."
fi
if [ "$FOREGROUND" = true ]; then
  if ! open "http://127.0.0.1:8765/"; then
    echo "Could not open the browser; open the dashboard URL above manually." >&2
  fi
  echo "Agent running. Ctrl+C stops both services. Logs: $LOG_DIR/twitter-agent.out"
  while kill -0 "$AGENT_PID" 2>/dev/null; do
    if ! kill -0 "$MONITOR_PID" 2>/dev/null; then
      echo "Dashboard exited unexpectedly; stopping the agent." >&2
      exit 1
    fi
    sleep 1
  done
  if wait "$AGENT_PID"; then
    exit 0
  else
    CODE="$?"
    echo "Agent stopped with exit code $CODE. Recent worker output:" >&2
    tail -n 20 "$LOG_DIR/twitter-agent.out" >&2
    exit "$CODE"
  fi
else
  echo "Background startup initiated; only a verified X post link proves publication."
fi
