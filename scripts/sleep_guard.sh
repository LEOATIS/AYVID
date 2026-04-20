#!/usr/bin/env bash
# sleep_guard.sh — toggle macOS system sleep for the 10-day live-trading window.
#
# This is the nuclear option beyond `caffeinate`. caffeinate only suppresses
# sleep *while the caffeinate process is running*; if launchd restarts
# something or you close the terminal, sleep can re-engage. `pmset
# disablesleep 1` makes the policy sticky across reboots and daemons.
#
# Usage:
#   sudo ./scripts/sleep_guard.sh --enable   # disable sleep until --disable is called
#   sudo ./scripts/sleep_guard.sh --disable  # restore default power policy
#   ./scripts/sleep_guard.sh --status        # no sudo; read-only
#
# NOTE: Requires sudo for --enable / --disable (pmset -a modifies a
# system-wide preference).

set -euo pipefail

ACTION="${1:-}"

case "$ACTION" in
  --enable)
    if [[ $EUID -ne 0 ]]; then
      echo "ERROR: --enable requires sudo."
      echo "  sudo $0 --enable"
      exit 1
    fi
    echo "Disabling macOS sleep (system-wide)..."
    pmset -a sleep 0
    pmset -a disksleep 0
    pmset -a disablesleep 1
    pmset -a displaysleep 5   # allow display to sleep, system must not
    echo "Done. Current power settings:"
    pmset -g | grep -E '(sleep|disablesleep|displaysleep|disksleep)' | sed 's/^/  /'
    echo ""
    echo "Remember to revert on Apr 30 with:"
    echo "  sudo $0 --disable"
    ;;
  --disable)
    if [[ $EUID -ne 0 ]]; then
      echo "ERROR: --disable requires sudo."
      echo "  sudo $0 --disable"
      exit 1
    fi
    echo "Restoring default power policy..."
    pmset -a disablesleep 0
    pmset -a sleep 10
    pmset -a disksleep 10
    pmset -a displaysleep 5
    echo "Done. Laptop will sleep normally now."
    pmset -g | grep -E '(sleep|disablesleep|displaysleep|disksleep)' | sed 's/^/  /'
    ;;
  --status|"")
    pmset -g | grep -E '(sleep|disablesleep|displaysleep|disksleep|lidwake|womp)'
    echo ""
    if pmset -g | grep -qE '^ sleep\s+0\b' || pmset -g | grep -qE 'disablesleep\s+1'; then
      echo "state: sleep is DISABLED (live-trading mode)"
    else
      echo "state: sleep is ENABLED (default macOS policy)"
    fi
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo "Usage: $0 --enable | --disable | --status"
    exit 1
    ;;
esac
