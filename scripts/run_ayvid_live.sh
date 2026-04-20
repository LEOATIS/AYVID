#!/usr/bin/env bash
# run_ayvid_live.sh — single-command launcher for the 10-day paper-trading window.
#
# Starts both long-running processes (FastAPI backend + paper runner) under
# `caffeinate` so macOS can't sleep the stack, tees output to timestamped
# logs under live/, and prints a one-screen summary so you know the loop is
# alive before you walk away.
#
# Usage:
#   ./scripts/run_ayvid_live.sh                  # normal launch
#   ./scripts/run_ayvid_live.sh --stop           # graceful stop
#   ./scripts/run_ayvid_live.sh --status         # quick ps check
#
# Design notes:
#   * We do NOT use launchd here. Use launchd (load the plists from the same
#     folder) for truly unattended operation. This script is for the foreground
#     case where you want to watch the first few minutes of the loop.
#   * caffeinate -dis flags: -d prevent display sleep, -i prevent idle sleep,
#     -s prevent system sleep on AC power. Exits when this script exits.
#   * Backend MUST be started first so the continuous worker registers before
#     the paper runner tries to talk to it (not strictly required, but avoids
#     a startup race where the runner logs a misleading "no cycle yet" line).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LIVE_DIR="$REPO_ROOT/live"
PID_DIR="$LIVE_DIR/pids"
LOG_DIR="$LIVE_DIR/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
RUNNER_PID_FILE="$PID_DIR/runner.pid"
CAFFEINATE_PID_FILE="$PID_DIR/caffeinate.pid"

TS="$(date +%Y%m%d_%H%M%S)"
BACKEND_LOG="$LOG_DIR/backend_$TS.log"
RUNNER_LOG="$LOG_DIR/runner_$TS.log"

VENV_PY="$REPO_ROOT/venv/bin/python"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
is_alive() {
  [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null
}

kill_pidfile() {
  if is_alive "$1"; then
    pid=$(cat "$1")
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$1"
}

# ─────────────────────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
  echo "Stopping AYVID live stack..."
  kill_pidfile "$RUNNER_PID_FILE"
  kill_pidfile "$BACKEND_PID_FILE"
  kill_pidfile "$CAFFEINATE_PID_FILE"
  echo "Stopped."
  exit 0
fi

if [[ "${1:-}" == "--status" ]]; then
  for label in backend runner caffeinate; do
    f="$PID_DIR/$label.pid"
    if is_alive "$f"; then
      echo "  $label: alive  (pid $(cat "$f"))"
    else
      echo "  $label: dead"
    fi
  done
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight
# ─────────────────────────────────────────────────────────────────────────────
if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: venv Python not found at $VENV_PY"
  echo "  Run: python3 -m venv venv && source venv/bin/activate && pip install -e ."
  exit 1
fi

if is_alive "$BACKEND_PID_FILE" || is_alive "$RUNNER_PID_FILE"; then
  echo "ERROR: AYVID is already running. Stop first with:"
  echo "  $0 --stop"
  exit 1
fi

# Check TWS / IB Gateway is listening on the configured port (default 7497).
IB_PORT="$(grep -E '^IB_PORT=' .env 2>/dev/null | cut -d= -f2 || echo 7497)"
if ! lsof -nP -iTCP:"$IB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "WARNING: nothing is listening on IB port $IB_PORT."
  echo "  Make sure TWS / IB Gateway is running and API is enabled."
  echo "  See scripts/TWS_CONFIG.md."
  echo "  Continuing anyway — the runner will retry with exponential backoff."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Launch caffeinate guard
# ─────────────────────────────────────────────────────────────────────────────
# -d: no display sleep; -i: no idle sleep; -s: no system sleep on AC.
# This caffeinate process stays alive until we kill it; it does not wrap a
# child process, so it can't accidentally SIGKILL the stack.
caffeinate -dis &
CAFFEINATE_PID=$!
echo "$CAFFEINATE_PID" > "$CAFFEINATE_PID_FILE"

# ─────────────────────────────────────────────────────────────────────────────
# Launch backend
# ─────────────────────────────────────────────────────────────────────────────
echo "Starting FastAPI backend → $BACKEND_LOG"
(
  cd "$REPO_ROOT"
  exec "$VENV_PY" -m cli.app system serve --host 127.0.0.1 --port 8000
) >"$BACKEND_LOG" 2>&1 &
echo "$!" > "$BACKEND_PID_FILE"

# Wait for the health endpoint to come online (max 30s) before starting the
# paper runner. This prevents the runner from connecting to IB before the
# continuous worker is up, which would mean auto-executed orders don't fire.
for i in {1..60}; do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "  backend healthy ($((i / 2))s)"
    break
  fi
  sleep 0.5
done
if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "ERROR: backend did not come up within 30s. Tail the log:"
  echo "  tail -f $BACKEND_LOG"
  kill_pidfile "$BACKEND_PID_FILE"
  kill_pidfile "$CAFFEINATE_PID_FILE"
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Launch paper runner
# ─────────────────────────────────────────────────────────────────────────────
echo "Starting paper runner (IB mode) → $RUNNER_LOG"
(
  cd "$REPO_ROOT"
  exec "$VENV_PY" -m cli.app trade paper-start --equity 1000000 --interval 60
) >"$RUNNER_LOG" 2>&1 &
echo "$!" > "$RUNNER_PID_FILE"

sleep 2

# ─────────────────────────────────────────────────────────────────────────────
# Post-launch summary
# ─────────────────────────────────────────────────────────────────────────────
cat <<EOF

AYVID live stack is up.

  Backend     pid $(cat "$BACKEND_PID_FILE")   log: $BACKEND_LOG
  Runner      pid $(cat "$RUNNER_PID_FILE")   log: $RUNNER_LOG
  Caffeinate  pid $(cat "$CAFFEINATE_PID_FILE")

Live artifacts:
  live/equity_curve.jsonl   (one row per runner tick)
  live/trades.jsonl         (one row per IB paper fill)
  live/positions.json       (current snapshot)
  live/run_meta.json        (this run's config)

Next steps:
  1. Tail either log:          tail -f $BACKEND_LOG
  2. Snapshot health:          ./scripts/live_healthcheck.sh
  3. Stop the stack:           $0 --stop

Safe to close this terminal. caffeinate will keep the Mac awake
until you --stop or the pid dies. For unattended 10-day operation,
use the launchd plists in scripts/ayvid_*.plist instead.
EOF
