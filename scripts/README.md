# scripts/ — AYVID operational tooling

Everything in this folder exists to support the **10-day live paper-trading
window (Apr 20 → Apr 30, 2026)** that ships with the Y Combinator S26
application. After YC, most of these scripts continue to serve as the
operational surface for any live deployment, but the defaults
(`--equity 1000000`, `--interval 60`, macOS-only paths) are currently
tuned for that window.

## File map

| File | Purpose |
|---|---|
| `run_ayvid_live.sh` | Foreground launcher for backend + paper runner under `caffeinate`. Use for day-1 observation. |
| `install_launchd.sh` | Install `com.ayvid.*` launch agents so the stack auto-restarts on crash and survives reboots. Use for unattended days 2–10. |
| `ayvid_backend.plist` | launchd manifest for the FastAPI backend (`cli.app system serve`). |
| `ayvid_runner.plist` | launchd manifest for the paper runner (`cli.app trade paper-start`). |
| `ayvid_daily_digest.plist` | launchd manifest for the daily 23:55 digest job. |
| `live_healthcheck.sh` | 10-second status snapshot across API, runner, TWS, disk, sleep policy, AUTO_EXECUTE flag. Run every morning. |
| `sleep_guard.sh` | `pmset` wrapper — disables system sleep for the window, restores it cleanly. Requires sudo for `--enable/--disable`. |
| `daily_digest.py` | Rolls up each day's fills + equity snapshots into `live/daily/YYYY-MM-DD.md` with rendered curve PNG. |
| `TWS_CONFIG.md` | Click-path for hardening IB Trader Workstation (API enable, daily auto-restart, size limits, stored creds). **Do this first.** |
| `demo.py` | Older demo workflow — unrelated to the live-trading window. |

## Day-0 setup (do once, ~30 min)

### 1. Lock TWS settings
Open `TWS_CONFIG.md` and follow every checkbox. Most important:
- Disable **Read-Only API** (otherwise orders silently fail)
- Enable **Auto Restart** with credentials stored (otherwise the IBKR
  nightly logout kills the loop)

### 2. Sanity-check the `.env`
```bash
grep -E '^(PAPER_TRADING|AUTO_EXECUTE|IB_ACCOUNT|IB_PORT)=' .env
```
Must show:
```
PAPER_TRADING=false
AUTO_EXECUTE=true
IB_ACCOUNT=DUQ243141            # or whatever your IB paper account is
IB_PORT=7497
```

### 3. First-run in foreground
```bash
./scripts/run_ayvid_live.sh
# Watch the logs, confirm "ORDER ROUTING: IB Paper (... account=DUQ243141)"
# and at least one equity row appears within 2 minutes.
./scripts/live_healthcheck.sh
# If everything is green:
./scripts/run_ayvid_live.sh --stop
```

### 4. Install unattended
```bash
./scripts/install_launchd.sh
sudo ./scripts/sleep_guard.sh --enable
./scripts/live_healthcheck.sh       # should still be green
```

From this point the laptop can be closed (clamshell, plugged in). The
stack is now:
- FastAPI + paper runner both under launchd with auto-restart
- Daily digest at 23:55 PT writing to `live/daily/`
- Sleep disabled system-wide

## Daily ritual (days 2–10, ~2 min/day)

```bash
./scripts/live_healthcheck.sh                # confirm all green
cat live/daily/$(date -v-1d +%Y-%m-%d).md    # read yesterday's digest
tail -5 live/run_log.txt                     # at-a-glance streak
```

If anything is red/warn in healthcheck, the runbook in `TWS_CONFIG.md`
under "Common failure modes" has the fix. 90% of the time the answer is
`launchctl kickstart -k gui/$(id -u)/com.ayvid.runner`.

## Day-10 shutdown (Apr 30, ~30 min)

```bash
# 1. Generate any missed digests
venv/bin/python scripts/daily_digest.py --all

# 2. Final curve render
venv/bin/python -m cli.app trade paper-render --out live/equity_curve_final.png

# 3. Freeze the live/ artifacts into document/yc/
cp live/equity_curve.jsonl document/yc/live_run_equity_curve.jsonl
cp live/trades.jsonl       document/yc/live_run_trades.jsonl
cp live/run_log.txt        document/yc/live_run_log.txt
cp live/equity_curve_final.png document/yc/live_run_equity_curve.png

# 4. Write LIVE_PAPER_REPORT.md (1-pager) — template in document/yc/ (TBD)

# 5. Tear down the infra
./scripts/install_launchd.sh --uninstall
sudo ./scripts/sleep_guard.sh --disable
./scripts/run_ayvid_live.sh --stop      # no-op if launchd already removed them
```

## Debug cheatsheet

| Question | Command |
|---|---|
| Is the backend healthy? | `curl -s localhost:8000/health \| jq` |
| Is the runner still ticking? | `tail -1 live/equity_curve.jsonl \| jq` |
| What's the last IB error? | `grep "IB Error" live/logs/backend_*.log \| tail -5` |
| Auto-exec enabled on this process? | `curl -s localhost:8000/api/v1/live/system-status \| jq .config.auto_execute` |
| Pipeline cycle history | `curl -s localhost:8000/pipeline/status \| jq` |
| Any cycles silently failed? | `grep "Auto-execute failed" live/logs/backend_*.log` |
| launchd still happy? | `launchctl list \| grep ayvid` |
| Force-restart the runner | `launchctl kickstart -k gui/$(id -u)/com.ayvid.runner` |
| Which pid is using port 8000? | `lsof -nP -iTCP:8000` |
| Is TWS accepting connections? | `nc -z 127.0.0.1 7497 && echo open` |

## Design notes

- **Why launchd instead of systemd/supervisor**: macOS-native, no extra
  install, survives reboot, logs to filesystem. For cloud/Linux we'd swap
  for systemd units.
- **Why a separate plist per service**: independent restart cycles — an
  IB hang on the runner shouldn't reboot the backend (which is also
  serving the operations console), and vice versa.
- **Why `caffeinate -dis` AND `pmset disablesleep`**: belt-and-suspenders.
  `caffeinate` dies with its parent terminal; `pmset disablesleep` is
  sticky across terminals and reboots. Both together = no sleep, ever,
  for 10 days.
- **Why `ThrottleInterval=30`**: prevents a crash-loop from pegging CPU
  on day-3 if some edge case puts the runner in a bad state.
- **Why daily digest runs at 23:55 local**: writes happen before the
  IBKR nightly logout (~23:45–00:00 ET depending on your TZ), and before
  midnight UTC rolls the `date` bucket. Simplest correct behavior.
