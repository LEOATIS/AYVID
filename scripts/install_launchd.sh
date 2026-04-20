#!/usr/bin/env bash
# install_launchd.sh — materialize the plists with the current repo path
# and load them via launchctl. Idempotent: safe to re-run.
#
# Usage:
#   ./scripts/install_launchd.sh           # install + load
#   ./scripts/install_launchd.sh --uninstall  # unload + remove

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
BACKEND_PLIST="$LA_DIR/com.ayvid.backend.plist"
RUNNER_PLIST="$LA_DIR/com.ayvid.runner.plist"
UID_DOMAIN="gui/$(id -u)"

mkdir -p "$LA_DIR"

if [[ "${1:-}" == "--uninstall" ]]; then
  echo "Uninstalling AYVID launch agents..."
  for label in com.ayvid.runner com.ayvid.backend com.ayvid.daily_digest; do
    launchctl bootout "$UID_DOMAIN/$label" 2>/dev/null || true
  done
  rm -f "$BACKEND_PLIST" "$RUNNER_PLIST" "$LA_DIR/com.ayvid.daily_digest.plist"
  echo "Done. Kill any stray processes with: ./scripts/run_ayvid_live.sh --stop"
  exit 0
fi

echo "Installing AYVID launch agents..."
echo "  Repo root: $REPO_ROOT"

mkdir -p "$REPO_ROOT/live/logs"

DIGEST_PLIST="$LA_DIR/com.ayvid.daily_digest.plist"

for name in ayvid_backend ayvid_runner ayvid_daily_digest; do
  src="$REPO_ROOT/scripts/${name}.plist"
  # Map ayvid_backend -> com.ayvid.backend, etc.
  case "$name" in
    ayvid_backend)       label=com.ayvid.backend ;;
    ayvid_runner)        label=com.ayvid.runner ;;
    ayvid_daily_digest)  label=com.ayvid.daily_digest ;;
  esac
  dst="$LA_DIR/${label}.plist"
  sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$src" > "$dst"
  echo "  wrote $dst"
done

echo "Loading agents..."
for label in com.ayvid.backend com.ayvid.runner com.ayvid.daily_digest; do
  launchctl bootout "$UID_DOMAIN/$label" 2>/dev/null || true
done
launchctl bootstrap "$UID_DOMAIN" "$BACKEND_PLIST"
launchctl bootstrap "$UID_DOMAIN" "$RUNNER_PLIST"
launchctl bootstrap "$UID_DOMAIN" "$DIGEST_PLIST"
for label in com.ayvid.backend com.ayvid.runner com.ayvid.daily_digest; do
  launchctl enable "$UID_DOMAIN/$label"
done

echo "Done. Both services are running and will auto-restart on crash."
echo ""
echo "Verify:"
echo "  launchctl list | grep ayvid"
echo "  ./scripts/live_healthcheck.sh"
echo ""
echo "Uninstall later with:"
echo "  $0 --uninstall"
