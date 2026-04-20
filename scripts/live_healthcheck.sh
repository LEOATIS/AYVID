#!/usr/bin/env bash
# live_healthcheck.sh — 10-second health snapshot for the paper-trading run.
#
# Checks every subsystem the live loop depends on and prints a Rich-style
# table. No changes to state. Run this once every morning during the 10-day
# window, and any time you suspect something went sideways.
#
# Exit codes:
#   0  — everything green
#   1  — at least one critical service is down
#   2  — warnings only (e.g. stale tick but API alive)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Colors (only if stdout is a tty)
if [[ -t 1 ]]; then
  G="\033[32m"; R="\033[31m"; Y="\033[33m"; B="\033[1m"; D="\033[2m"; X="\033[0m"
else
  G=""; R=""; Y=""; B=""; D=""; X=""
fi

status_ok=0
status_warn=0

row() {
  local label="$1" state="$2" detail="${3:-}"
  case "$state" in
    ok)    printf "  ${G}● ok${X}    %-22s ${D}%s${X}\n"      "$label" "$detail" ;;
    warn)  printf "  ${Y}● warn${X}  %-22s ${D}%s${X}\n"    "$label" "$detail" ; status_warn=1 ;;
    fail)  printf "  ${R}● fail${X}  %-22s ${D}%s${X}\n"    "$label" "$detail" ; status_ok=1 ;;
  esac
}

echo
printf "${B}AYVID — Live Paper-Trading Healthcheck${X}   ${D}$(date '+%Y-%m-%d %H:%M:%S %Z')${X}\n"
echo

# ─────────────────────────────────────────────────────────────────────────────
# 1. FastAPI backend
# ─────────────────────────────────────────────────────────────────────────────
if curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  health="$(curl -s --max-time 3 http://127.0.0.1:8000/health)"
  paper="$(echo "$health" | grep -oE '"paper_trading":[^,}]+' | cut -d: -f2)"
  row "FastAPI backend" ok ":8000 · paper_trading=$paper"
else
  row "FastAPI backend" fail ":8000 not responding"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. Paper runner — equity curve freshness
# ─────────────────────────────────────────────────────────────────────────────
curve="$REPO_ROOT/live/equity_curve.jsonl"
if [[ -f "$curve" ]] && [[ -s "$curve" ]]; then
  last_line="$(tail -1 "$curve")"
  last_ts="$(echo "$last_line" | grep -oE '"timestamp":[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')"
  last_equity="$(echo "$last_line" | grep -oE '"equity":[[:space:]]*[0-9.]+' | head -1 | awk -F: '{print $2}' | tr -d ' ')"
  last_mode="$(echo "$last_line" | grep -oE '"mode":[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"([^"]+)"$/\1/')"
  # Age in seconds (convert ISO -> epoch)
  last_epoch="$(python3 -c "import datetime as d; import sys; print(int(d.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$last_ts" 2>/dev/null || echo 0)"
  now_epoch="$(date +%s)"
  age=$(( now_epoch - last_epoch ))
  if (( age < 180 )); then
    row "Paper runner tick" ok "${age}s ago · mode=$last_mode · NLV=\$$(printf '%.0f' "$last_equity" 2>/dev/null || echo "$last_equity")"
  elif (( age < 600 )); then
    row "Paper runner tick" warn "${age}s ago — tick is slow, mode=$last_mode"
  else
    row "Paper runner tick" fail "stale: last tick ${age}s ago"
  fi
else
  row "Paper runner tick" fail "live/equity_curve.jsonl missing or empty"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. IB Gateway / TWS — port listening
# ─────────────────────────────────────────────────────────────────────────────
IB_PORT="$(grep -E '^IB_PORT=' .env 2>/dev/null | cut -d= -f2 || echo 7497)"
if lsof -nP -iTCP:"$IB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  row "IB Gateway/TWS" ok ":$IB_PORT LISTENING"
else
  row "IB Gateway/TWS" fail "nothing listening on :$IB_PORT — API orders will fail"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. Today's trade activity
# ─────────────────────────────────────────────────────────────────────────────
trades="$REPO_ROOT/live/trades.jsonl"
if [[ -f "$trades" ]]; then
  today="$(date -u +%Y-%m-%d)"
  today_count=$(grep -c "\"timestamp\":[[:space:]]*\"$today" "$trades" 2>/dev/null || echo 0)
  total_count="$(wc -l < "$trades" | tr -d ' ')"
  row "Trades today" ok "$today_count today · $total_count total in live/trades.jsonl"
else
  row "Trades today" warn "live/trades.jsonl not yet created (expected before first fill)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. AUTO_EXECUTE flag
# ─────────────────────────────────────────────────────────────────────────────
ae="$(grep -E '^AUTO_EXECUTE=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
pt="$(grep -E '^PAPER_TRADING=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
ib_acct="$(grep -E '^IB_ACCOUNT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
if [[ "$ae" == "true" ]] && [[ "$pt" == "false" ]]; then
  row "Routing config" ok "AUTO_EXECUTE=true · PAPER_TRADING=false · IB_ACCOUNT=$ib_acct"
elif [[ "$ae" == "true" ]] && [[ "$pt" == "true" ]]; then
  row "Routing config" warn "AUTO_EXECUTE=true but PAPER_TRADING=true (orders stub, never hit IB)"
else
  row "Routing config" warn "AUTO_EXECUTE=$ae · PAPER_TRADING=$pt (auto-execute disabled)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. Process liveness (from pid files)
# ─────────────────────────────────────────────────────────────────────────────
for label in backend runner caffeinate; do
  f="$REPO_ROOT/live/pids/$label.pid"
  if [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null; then
    row "pid: $label" ok "$(cat "$f")"
  else
    # Only fail if we have a pid file; if not, assume launchd/dev-run
    if [[ -f "$f" ]]; then
      row "pid: $label" fail "pid in file is dead"
    else
      row "pid: $label" warn "no pid file (launchd-managed?)"
    fi
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# 7. Disk space on live/ volume
# ─────────────────────────────────────────────────────────────────────────────
avail_pct="$(df -P "$REPO_ROOT/live" | awk 'NR==2 {gsub("%","",$5); print 100-$5}')"
if (( avail_pct > 20 )); then
  row "Disk headroom" ok "${avail_pct}% free on live/ volume"
elif (( avail_pct > 5 )); then
  row "Disk headroom" warn "${avail_pct}% free — consider freeing space"
else
  row "Disk headroom" fail "${avail_pct}% free — dangerously low"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 8. pmset — system sleep disabled?
# ─────────────────────────────────────────────────────────────────────────────
if command -v pmset >/dev/null 2>&1; then
  sleep_val="$(pmset -g | grep -E '^ sleep' | awk '{print $2}' || echo unknown)"
  disablesleep="$(pmset -g | grep -E 'disablesleep' | awk '{print $2}' || echo 0)"
  if [[ "$sleep_val" == "0" ]] || [[ "$disablesleep" == "1" ]]; then
    row "macOS sleep" ok "disabled (sleep=$sleep_val)"
  else
    row "macOS sleep" warn "sleep enabled (sleep=$sleep_val) — run scripts/sleep_guard.sh --enable"
  fi
fi

echo
if (( status_ok > 0 )); then
  printf "${R}${B}CRITICAL:${X} at least one service is down. See above.\n\n"
  exit 1
elif (( status_warn > 0 )); then
  printf "${Y}${B}warnings${X}: stack is up but some issues. See above.\n\n"
  exit 2
else
  printf "${G}${B}all green.${X}\n\n"
  exit 0
fi
