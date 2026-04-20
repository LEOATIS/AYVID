# IBKR Trader Workstation — Hardening Checklist

This is a one-time setup to make TWS survive a 10-day unattended paper-trading
window. Do this today (before launching the live stack), and never touch it
again for the duration.

All paths below assume TWS desktop app (your current setup), not IB Gateway.

---

## 1. Enable the API

**TWS → File → Global Configuration → API → Settings**

- [x] **Enable ActiveX and Socket Clients** ✓
- [x] **Socket port** = `7497` (matches `IB_PORT` in `.env`)
- [ ] **Read-Only API** = **OFF**
  *This is the #1 footgun. If Read-Only is ON, your data loop still works
  (which is why today's system-health shows green), but every order will
  be silently rejected. Turn it off.*
- [x] **Master API client ID** = blank (leave unset)
- [x] **Trusted IP Addresses** → Add `127.0.0.1`
- [x] **Bypass Order Precautions for API Orders** = ON
  *(Otherwise every auto-exec fires a popup that blocks the order until
  you click through it. In paper that's just annoying; we still want it
  off.)*
- [ ] **Download open orders on connection** = ON

Click **OK**, then **restart TWS** to take effect.

## 2. Daily auto-restart (critical for 10-day runs)

IBKR **forces** a daily logout around 23:45 ET on paper sessions.
Without auto-restart configured, TWS will sit at the login screen for the
rest of the night and your orders will silently fail starting the next
session. Non-negotiable to fix.

**TWS → File → Global Configuration → Lock and Exit**

- [ ] **Set Auto restart** = ON
- [ ] **Auto restart time** = `03:00` (any time 00:00–06:00 ET while you sleep)
- [ ] **Auto Logoff** → set to a time at least 15 minutes **after** Auto Restart
  *This lets the restart happen first so there's no login gap.*

**TWS → File → Global Configuration → Lock and Exit → Auto Logoff**

- [ ] **Never require TWS to be logged out** = ON (if available for paper)

## 3. Store paper credentials for unattended re-login

- [ ] On the login screen, check **Save Password** (paper accounts only!)
- [ ] Do NOT check "Use SSL" on a daily cycle setting — stick with the default

> For truly unattended operation you'd install [IBC](https://github.com/IbcAlpha/IBC)
> to handle re-login across restarts. For this 10-day window, the stored
> password + auto-restart combo is sufficient on macOS.

## 4. Disable the "Precautionary Settings" that kill orders

**TWS → File → Global Configuration → Presets → All Products**

- [ ] **Size Limit** → set to something like `10000` shares (well above any
  Kelly-sized order you'll place with $1M equity)
- [ ] **Total Value Limit** → `$500,000` per order
  *These are paranoia limits TWS enforces even on the API. If your Kelly
  sizing produces an order bigger than this, TWS rejects it silently.*

## 5. Confirm port 7497 is actually listening

After you hit OK in step 1 and restart TWS, from a terminal:

```bash
lsof -nP -iTCP:7497 -sTCP:LISTEN
```

You should see a `java` (TWS) process bound to `127.0.0.1:7497`. If nothing
comes back, the API isn't actually enabled — go back to step 1.

## 6. Memory sanity

TWS leaks memory over a multi-day run. Combined with the auto-restart above
this is fine, but if you want extra belt-and-suspenders on a 16 GB MBP:

- [ ] **TWS → File → Global Configuration → Display → Ticker Row** → reduce
  the "Maximum rows" to 50 (from default 1000)
- [ ] Close any chart windows you opened for testing — they each keep a
  historical subscription alive

## 7. Power / network

- [ ] Mac is **plugged into AC power**
- [ ] Laptop lid can be closed in **clamshell mode** (external monitor, or
  just clamshell on wall power — BUT only if `caffeinate` or the sleep_guard
  script is active; otherwise macOS sleeps when the lid closes even plugged in)
- [ ] Wifi is on a network that won't silently drop you every few hours
  (corporate VPNs are often the culprit — test without the VPN if possible)
- [ ] Consider hardline Ethernet with a USB-C dongle if reliability matters

## 8. Final verification

After all the above, run:

```bash
./scripts/live_healthcheck.sh
```

You want every row green. Then you're ready to launch:

```bash
./scripts/run_ayvid_live.sh           # foreground, for initial observation
# or
./scripts/install_launchd.sh          # unattended, auto-restart on crash
sudo ./scripts/sleep_guard.sh --enable
```

And on Apr 30:

```bash
sudo ./scripts/sleep_guard.sh --disable
./scripts/install_launchd.sh --uninstall
python scripts/daily_digest.py --all  # regenerate any missed digests
```

---

## Common failure modes during a 10-day run

| Symptom | Root cause | Fix |
|---|---|---|
| Orders logged as FILLED in AYVID but no fills in TWS | `PAPER_TRADING=true` in `.env` | Set to `false`, restart backend |
| Orders rejected silently | Read-Only API is ON (step 1), or over size limit (step 4) | Fix in TWS Global Config |
| Equity curve stops updating at ~midnight ET daily | TWS auto-logout, Auto Restart not configured | Step 2 |
| `clientId 326` error on reconnect | Stale clientId from previous crash | AYVID auto-bumps the ID (connection.py:45-55); wait 1 minute |
| Mac sleeps despite `caffeinate` running | caffeinate child process was killed (terminal closed etc.) | Use `sleep_guard.sh --enable` + launchd plists instead |
| Curve goes flat for hours in the middle of the day | IB market-data subscription lapsed, or no signals | Check `live/logs/backend_*.log` for "No active subscription" |
