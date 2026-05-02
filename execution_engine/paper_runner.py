"""Live paper-trading runner with daily equity-curve artifact.

Two modes:

1. **IB mode** (PAPER_TRADING=true, IB Gateway running on port 7497):
   Connects to the IB paper account, polls NetLiquidation + open
   positions on a fixed cadence, and writes one JSONL row per snapshot
   to ``live/equity_curve.jsonl``. Real fills are routed via the
   existing execution stack when AUTO_EXECUTE=true.

2. **Shadow mode** (--shadow, no IB required):
   Runs the orchestrator against the most recent local OHLCV bars,
   simulates execution at the last close with the IBKR cost model,
   marks-to-market each cycle, and writes the same equity-curve
   artifact. Useful for producing a continuous curve while IB
   credentials are being provisioned.

Output artifacts:
  - live/equity_curve.jsonl     one row per polling cycle
  - live/trades.jsonl           one row per executed (or shadow) fill
  - live/positions.json         current open positions snapshot

These artifacts are designed to be easy to chart with the companion
``ayvid trade live-render`` command (matplotlib PNG) and to be the
single piece of YC evidence the partner will look at: a daily,
post-cost equity curve from a real (paper) deployment.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import signal
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.atomic_io import atomic_write_json
from shared.config import settings
from shared.logger import get_logger

log = get_logger("paper_runner")

LIVE_DIR = Path("live")
EQUITY_CURVE_PATH = LIVE_DIR / "equity_curve.jsonl"
TRADES_PATH = LIVE_DIR / "trades.jsonl"
POSITIONS_PATH = LIVE_DIR / "positions.json"
RUN_META_PATH = LIVE_DIR / "run_meta.json"
_GCS_BUCKET = (os.getenv("AYVID_LIVE_GCS_BUCKET") or "").strip()
_GCS_PREFIX = (os.getenv("AYVID_LIVE_GCS_PREFIX") or "live_state").strip("/")
_gcs_client: Any | None = None

_stop_event: asyncio.Event | None = None


@dataclass
class EquitySnapshot:
    timestamp: str
    mode: str
    equity: float
    cash: float
    position_value: float
    open_positions: int
    daily_pnl: float
    cumulative_pnl: float
    cumulative_return_pct: float
    cycle: int
    notes: str = ""


@dataclass
class ShadowState:
    initial_equity: float
    cash: float
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    cycle: int = 0
    cumulative_pnl: float = 0.0
    last_equity: float = 0.0
    # Track signal-book keys already consumed so one signal
    # produces exactly one fill across the runner's lifetime.
    consumed_signals: set[str] = field(default_factory=set)


def _ensure_dirs() -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")
    _sync_path_to_gcs(path)


def _blob_name(path: Path) -> str:
    # Keep a stable key layout under prefix, e.g.:
    #   live_state/live/equity_curve.jsonl
    rel = path.as_posix()
    return f"{_GCS_PREFIX}/{rel}" if _GCS_PREFIX else rel


def _sync_path_to_gcs(path: Path) -> None:
    if not _GCS_BUCKET:
        return
    if not path.exists():
        return
    global _gcs_client
    try:
        if _gcs_client is None:
            from google.cloud import storage

            _gcs_client = storage.Client()
        bucket = _gcs_client.bucket(_GCS_BUCKET)
        blob = bucket.blob(_blob_name(path))
        blob.upload_from_filename(str(path), content_type="application/json")
    except Exception as exc:
        # Never let storage sync break the trading loop.
        log.warning(f"gcs sync failed for {path}: {exc}")


import time as _time

DEPLOY_MARKER_PATH = LIVE_DIR / "deploy_marker.json"


async def _stall_watchdog(max_silence_seconds: int = 600) -> None:
    """Detects a "silent zombie" runner — process alive but not writing
    snapshots — and signals graceful shutdown so Docker's restart policy
    reboots the container with a fresh deploy marker.

    Behaviour: every 60s we ``stat`` ``live/equity_curve.jsonl``. If its
    size hasn't changed for ``max_silence_seconds`` we set
    ``_stop_event``. The main loops at lines 282–284 and the IB cleanup
    finally-block at 286–300 then run normally — this is **not**
    ``os._exit``: open IB brackets are cancelled before disconnect.

    Why a stall watchdog exists: the May 2 incident showed the deployed
    runner went 3 days without writing equity_curve.jsonl while still
    "Up" in Docker — health is structural, not behavioural. This makes
    it impossible: 10 minutes of no writes → restart → new deploy marker.
    """
    if _stop_event is None:
        return
    eq_path = LIVE_DIR / "equity_curve.jsonl"
    last_size = -1
    last_change_at = _time.monotonic()
    log.info(f"stall_watchdog_started max_silence_seconds={max_silence_seconds}")
    while not _stop_event.is_set():
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=60)
            return  # graceful shutdown initiated elsewhere — exit cleanly
        except TimeoutError:
            pass
        try:
            current_size = eq_path.stat().st_size if eq_path.exists() else 0
        except OSError as exc:
            log.warning(f"stall_watchdog_stat_failed: {exc!s}")
            continue
        if current_size != last_size:
            last_size = current_size
            last_change_at = _time.monotonic()
            continue
        silence = _time.monotonic() - last_change_at
        if silence > max_silence_seconds:
            log.error(
                f"stall_watchdog_triggered silence_seconds={int(silence)} "
                f"last_size={last_size} action=graceful_shutdown_signal"
            )
            # Set stop event — main loops exit, IB finally-block runs
            # (orphan-bracket cancellation), Docker restart policy reboots.
            _stop_event.set()
            return


def _write_deploy_marker() -> dict[str, Any]:
    """Persist the runner's identity to ``live/deploy_marker.json``.

    Read by ``GET /api/v1/system/runner-info`` so the CI smoke test
    can verify the VM container is on the new image. The image's
    Dockerfile bakes ``BUILD_SHA`` / ``BUILD_TIME`` as ENV vars at
    build time (see Dockerfile.execution), so this just snapshots
    them plus the live process metadata.

    Atomic write so a SIGKILL mid-write can't leave a half-truncated
    JSON file that would 500 the runner-info endpoint.
    """
    exec_mode = getattr(settings, "EXECUTION_MODE", None)
    marker = {
        "build_sha": os.environ.get("BUILD_SHA", "dev"),
        "build_time": os.environ.get("BUILD_TIME", "unknown"),
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "pid": os.getpid(),
        "execution_mode": (
            exec_mode.value if hasattr(exec_mode, "value") else str(exec_mode or "shadow")
        ),
        "auto_execute": bool(getattr(settings, "AUTO_EXECUTE", False)),
        "llm_orchestration_enabled": bool(
            getattr(settings, "LLM_ORCHESTRATION_ENABLED", False)
        ),
    }
    try:
        DEPLOY_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = DEPLOY_MARKER_PATH.with_suffix(".tmp")
        with tmp.open("w") as fh:
            json.dump(marker, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, DEPLOY_MARKER_PATH)
        log.info(
            f"deploy_marker_written sha={marker['build_sha']} "
            f"mode={marker['execution_mode']} pid={marker['pid']}"
        )
        _sync_path_to_gcs(DEPLOY_MARKER_PATH)
    except OSError as exc:
        # Non-fatal — we'd rather start the runner with no marker than
        # refuse to boot. The smoke test will surface the gap.
        log.warning(f"deploy_marker_write_failed: {exc!s}")
    return marker


def _write_meta(mode: str, initial_equity: float, interval_seconds: int) -> None:
    exec_mode = getattr(settings, "EXECUTION_MODE", None)
    meta = {
        "mode": mode,
        "execution_mode": exec_mode.value if hasattr(exec_mode, "value") else str(exec_mode or mode),
        "initial_equity": initial_equity,
        "interval_seconds": interval_seconds,
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "ib_host": settings.IB_HOST,
        "ib_port": settings.IB_PORT,
        "auto_execute": settings.AUTO_EXECUTE,
        "paper_trading": settings.PAPER_TRADING,
        "live_trading_confirmed": getattr(settings, "LIVE_TRADING_CONFIRMED", False),
        "llm_orchestration_enabled": getattr(settings, "LLM_ORCHESTRATION_ENABLED", False),
    }
    # Defensive write: an EACCES on live/ used to crash the runner at
    # boot which then Docker-restart-looped (May 3 incident: bind-mount
    # was root-owned but container ran as uid 1001). Now we degrade
    # gracefully — log a warning, surface the gap via /system/runner-info
    # missing-marker hint, but don't take down the trading loop just
    # because we can't snapshot run metadata.
    try:
        RUN_META_PATH.write_text(json.dumps(meta, indent=2))
        _sync_path_to_gcs(RUN_META_PATH)
    except OSError as exc:
        log.warning(f"_write_meta failed (non-fatal): {exc!s}")


# ────────────────────────────────────────────────────────────────
# IB MODE
# ────────────────────────────────────────────────────────────────


async def _snapshot_from_ib(ib: Any, cycle: int, last_equity: float) -> EquitySnapshot:
    """Pull NetLiquidation + cash + positions from the IB paper account.

    ``ib.accountValues()`` and ``ib.positions()`` are in-process cache
    accessors that ib_async populates from the ``reqAccountUpdates``
    subscription opened in ``connect()``. They are synchronous *and*
    cheap — no executor / no event-loop juggling needed.
    """
    account_values = ib.accountValues()
    positions = ib.positions()

    target_account = (settings.IB_ACCOUNT or "").strip()

    nlv = 0.0
    cash = 0.0
    for av in account_values:
        if target_account and av.account != target_account:
            continue
        if av.currency not in ("BASE", "USD"):
            continue
        if av.tag == "NetLiquidation":
            nlv = max(nlv, float(av.value))
        elif av.tag == "TotalCashValue":
            cash = max(cash, float(av.value))

    open_positions = [p for p in positions if p.position != 0]
    open_count = len(open_positions)
    position_value = max(nlv - cash, 0.0)

    daily_pnl = nlv - last_equity if last_equity > 0 else 0.0
    return EquitySnapshot(
        timestamp=dt.datetime.now(dt.UTC).isoformat(),
        mode="ib_paper",
        equity=round(nlv, 2),
        cash=round(cash, 2),
        position_value=round(position_value, 2),
        open_positions=open_count,
        daily_pnl=round(daily_pnl, 2),
        cumulative_pnl=0.0,
        cumulative_return_pct=0.0,
        cycle=cycle,
    )


async def _run_ib_mode(interval_seconds: int) -> None:
    from execution_engine.broker.connection import connect, disconnect

    ib = await connect()
    log.info(
        f"IB paper mode: polling every {interval_seconds}s, account={settings.IB_ACCOUNT or 'default'}"
    )

    cycle = 0
    initial_equity = 0.0
    last_equity = 0.0
    # Reconcile local open theses with IB-side positions every N cycles.
    # Catches divergence (failed close orders, silent IB rejections)
    # before it can produce a wrong-side trade on the next signal.
    RECONCILE_EVERY = 10
    try:
        while not _stop_event.is_set():
            cycle += 1
            if cycle % RECONCILE_EVERY == 1:
                try:
                    from execution_engine.broker.recovery import reconcile_local_with_ib
                    await reconcile_local_with_ib(ib)
                except Exception as exc:
                    log.warning(f"position reconciliation failed (non-fatal): {exc}")
            try:
                snap = await _snapshot_from_ib(ib, cycle, last_equity)
                if cycle == 1:
                    initial_equity = snap.equity
                    log.info(f"Initial NLV: ${initial_equity:,.2f}")
                snap.cumulative_pnl = round(snap.equity - initial_equity, 2)
                snap.cumulative_return_pct = round(
                    (snap.equity / initial_equity - 1) * 100 if initial_equity > 0 else 0.0, 4
                )
                _append_jsonl(EQUITY_CURVE_PATH, asdict(snap))
                log.info(
                    f"[{cycle:>4}] NLV=${snap.equity:,.2f} cash=${snap.cash:,.2f} "
                    f"open={snap.open_positions} daily=${snap.daily_pnl:+,.2f} "
                    f"cum={snap.cumulative_return_pct:+.3f}%"
                )
                last_equity = snap.equity
            except Exception as exc:
                log.warning(f"cycle {cycle} snapshot failed: {exc}")

            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
    finally:
        # Stop event was set (SIGTERM/SIGINT) OR an unexpected exit.
        # Either way, we MUST cancel open orders before letting IB drop
        # the session — leaving brackets live after the runner exits is
        # the worst-case contract violation.
        log.warning("IB mode shutting down — cancelling open orders before disconnect.")
        try:
            await asyncio.wait_for(disconnect(cancel_open_orders=True), timeout=4.0)
        except TimeoutError:
            log.error(
                "IB disconnect timed out after 4s. Open orders may persist; "
                "verify in IB Gateway and cancel manually if needed."
            )
        except Exception as exc:
            log.error(f"IB disconnect failed: {exc}")


# ────────────────────────────────────────────────────────────────
# SHADOW MODE (no IB connection required)
# ────────────────────────────────────────────────────────────────

_SIGNAL_BOOK_PATH = Path("data_store/signal_book.json")


# ────────────────────────────────────────────────────────────────
# Per-asset-class shadow execution
# ────────────────────────────────────────────────────────────────
#
# Each helper takes the signal dict + a mark price and returns a fill
# dict (or None to reject the signal). The dispatcher in
# ``_consume_signal_book`` handles dedupe, open-thesis registration,
# and book-keeping — these helpers ONLY decide qty/notional/cash
# impact for their asset class.
#
# Sizing precedence (per class):
#   1. ``sig['quantity']`` — emitted by ``compute_quantity_for_signal``
#      in continuous_worker. Authoritative when present and > 0.
#   2. Cash-based fallback via Kelly when the signal predates the
#      sizer (signal-book entries from older cycles).
# Cash semantics differ by class: equity / crypto debit full notional,
# futures / FX debit margin only, options handle premium credit/debit.

_INSTRUMENT_TO_ASSET_CLASS = {
    "EQUITY": "EQUITY",
    "ETF": "ETF",
    "FX": "FX",
    "COMMODITY": "COMMODITY",
    "COMMODITY_FUTURE": "COMMODITY",
    "RATE_FUTURE": "FUTURE",
    "INDEX_FUTURE": "FUTURE",
    "FUTURE": "FUTURE",
    "CRYPTO": "CRYPTO",
    "BOND": "BOND",
    "OPTION": "OPTION",
}

_SLIPPAGE_BPS = {
    "EQUITY": 0.0005,
    "ETF": 0.0005,
    "BOND": 0.0005,
    "FX": 0.0001,
    "CRYPTO": 0.0010,
    "COMMODITY": 0.0003,
    "FUTURE": 0.0002,
    "OPTION": 0.0050,
}


def _build_position_record(
    *,
    ticker: str,
    asset_class: str,
    side: str,
    qty: float,
    entry_price: float,
    notional: float,
    margin_used: float,
    sig: dict[str, Any],
    key: str,
    cycle: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the position record and trade row for any asset class.

    Returns ``(position_dict, trade_dict)``. Cash/state mutation is the
    caller's job — these are pure builders so per-class helpers stay
    short and parallel.
    """
    intent_id = f"shadow|{key}|{cycle}"
    signed_qty = qty if side == "BUY" else -qty
    position = {
        "asset_class": asset_class,
        "entry_price": round(entry_price, 6),
        "shares": signed_qty,
        "qty": signed_qty,
        "direction": "LONG" if side == "BUY" else "SHORT",
        "notional": round(notional, 2),
        "margin_used": round(margin_used, 2),
        "stop_loss_price": sig.get("stop_loss_price"),
        "take_profit_price": sig.get("take_profit_price"),
        "stop_loss_pct": sig.get("stop_loss_pct"),
        "take_profit_pct": sig.get("take_profit_pct"),
        "contract_multiplier": float(sig.get("contract_multiplier") or 1.0),
        "pip_value": float(sig.get("pip_value") or 0.0),
        "entered_at": dt.datetime.now(dt.UTC).isoformat(),
        "rationale": str(sig.get("rationale") or "signal_book entry"),
        "source": "signal_book",
        "signal_key": key,
        "intent_id": intent_id,
        "strategy": sig.get("strategy_name"),
        "strategy_name": sig.get("strategy_name"),
    }
    trade = {
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "mode": "shadow",
        "ticker": ticker,
        "asset_class": asset_class,
        "side": side,
        "shares": qty,
        "qty": qty,
        "fill_price": round(entry_price, 6),
        "notional": round(notional, 2),
        "margin_used": round(margin_used, 2),
        "rationale": str(sig.get("rationale") or "signal_book entry"),
        "strategy": sig.get("strategy_name"),
        "conviction": float(sig.get("conviction") or 0.0),
        "expected_r": float(sig.get("expected_r") or 0.0),
        "source": "signal_book",
        "signal_key": key,
        "cycle": cycle,
        "status": "FILLED",
    }
    return position, trade


def _sized_qty(sig: dict[str, Any], cash: float, mark: float, kelly_cap: float = 0.05) -> float:
    """Resolve the order quantity for a signal.

    Prefer the pre-sized ``quantity`` emitted by
    ``compute_quantity_for_signal`` in the worker; fall back to a
    Kelly-fraction × cash sizer for legacy signal-book entries that
    pre-date the sizer.
    """
    qty = float(sig.get("quantity") or 0.0)
    if qty > 0:
        return qty
    from execution_engine.math.kelly import (
        compute_position_size,
        kelly_fraction_single,
    )

    conv = float(sig.get("conviction") or 0.0)
    exp_r = max(float(sig.get("expected_r") or 1.0), 1.0)
    frac = min(kelly_fraction_single(conv, exp_r), kelly_cap)
    shares = compute_position_size(cash, frac, mark)
    return float(shares) if shares >= 1 else 0.0


def _execute_equity_or_etf(
    *, sig: dict, mark: float, side: str, asset_class: str,
    state: ShadowState, key: str, cycle: int,
) -> dict | None:
    qty = _sized_qty(sig, state.cash, mark)
    if qty < 1:
        return None
    slip_bps = _SLIPPAGE_BPS.get(asset_class, 0.0005)
    slip = (1.0 + slip_bps) if side == "BUY" else (1.0 - slip_bps)
    entry = mark * slip
    notional = entry * qty
    if side == "BUY" and notional > state.cash:
        return None
    if notional > state.cash * 0.4:
        return None
    state.cash += -notional if side == "BUY" else notional
    pos, trade = _build_position_record(
        ticker=str(sig.get("ticker", "")).upper(), asset_class=asset_class, side=side,
        qty=qty, entry_price=entry, notional=notional, margin_used=notional,
        sig=sig, key=key, cycle=cycle,
    )
    state.positions[trade["ticker"]] = pos
    return trade


def _execute_future(
    *, sig: dict, mark: float, side: str, state: ShadowState,
    key: str, cycle: int,
) -> dict | None:
    """Index futures / commodity futures.

    Margin is the cash impact, NOT notional — futures are leveraged
    instruments. We approximate maintenance margin at 5% of notional
    when the signal doesn't carry a margin_required field.
    """
    qty = _sized_qty(sig, state.cash, mark)
    if qty < 1:
        return None
    qty = float(int(qty))  # whole contracts only
    multiplier = float(sig.get("contract_multiplier") or 1.0)
    slip_bps = _SLIPPAGE_BPS["FUTURE"]
    slip = (1.0 + slip_bps) if side == "BUY" else (1.0 - slip_bps)
    entry = mark * slip
    notional = entry * qty * multiplier
    margin = float(sig.get("margin_required") or notional * 0.05)
    if margin > state.cash:
        log.info(f"shadow: insufficient margin for {sig.get('ticker')} (need {margin:.0f}, have {state.cash:.0f})")
        return None
    state.cash -= margin
    pos, trade = _build_position_record(
        ticker=str(sig.get("ticker", "")).upper(), asset_class="FUTURE", side=side,
        qty=qty, entry_price=entry, notional=notional, margin_used=margin,
        sig=sig, key=key, cycle=cycle,
    )
    state.positions[trade["ticker"]] = pos
    return trade


def _execute_fx(
    *, sig: dict, mark: float, side: str, state: ShadowState,
    key: str, cycle: int,
) -> dict | None:
    """FX pairs. Quantity is in standard lots (100k units)."""
    lots = _sized_qty(sig, state.cash, mark)
    if lots <= 0:
        return None
    notional = lots * 100_000.0 * mark
    margin = float(sig.get("margin_required") or notional * 0.02)
    if margin > state.cash:
        return None
    slip_bps = _SLIPPAGE_BPS["FX"]
    slip = (1.0 + slip_bps) if side == "BUY" else (1.0 - slip_bps)
    entry = mark * slip
    state.cash -= margin
    pos, trade = _build_position_record(
        ticker=str(sig.get("ticker", "")).upper(), asset_class="FX", side=side,
        qty=lots, entry_price=entry, notional=notional, margin_used=margin,
        sig=sig, key=key, cycle=cycle,
    )
    state.positions[trade["ticker"]] = pos
    return trade


def _execute_crypto(
    *, sig: dict, mark: float, side: str, state: ShadowState,
    key: str, cycle: int,
) -> dict | None:
    qty = _sized_qty(sig, state.cash, mark)
    if qty <= 0:
        return None
    slip_bps = _SLIPPAGE_BPS["CRYPTO"]
    slip = (1.0 + slip_bps) if side == "BUY" else (1.0 - slip_bps)
    entry = mark * slip
    notional = entry * qty
    fee = notional * 0.001  # 0.1% taker
    if side == "BUY" and (notional + fee) > state.cash:
        return None
    if (notional + fee) > state.cash * 0.4:
        return None
    state.cash += -(notional + fee) if side == "BUY" else (notional - fee)
    pos, trade = _build_position_record(
        ticker=str(sig.get("ticker", "")).upper(), asset_class="CRYPTO", side=side,
        qty=qty, entry_price=entry, notional=notional, margin_used=notional,
        sig=sig, key=key, cycle=cycle,
    )
    pos["fee_paid"] = round(fee, 4)
    trade["fee"] = round(fee, 4)
    state.positions[trade["ticker"]] = pos
    return trade


def _execute_option(
    *, sig: dict, mark: float, side: str, state: ShadowState,
    key: str, cycle: int,
) -> dict | None:
    """Options. Multiplier 100; premium drives cash impact."""
    qty = float(sig.get("quantity") or 0.0)
    if qty < 1:
        return None
    qty = float(int(qty))
    premium = float(sig.get("entry_price") or sig.get("entry_price_at_compute") or mark)
    multiplier = 100.0
    cost_basis = premium * qty * multiplier
    strategy = str(sig.get("strategy_name") or "").lower()
    is_credit = "iron_condor" in strategy or "credit" in strategy
    if is_credit:
        # Selling premium → cash credit; max-loss reserved as margin
        max_loss = float(sig.get("max_loss") or cost_basis * 2.0)
        state.cash += cost_basis  # credit received
        margin_used = max_loss
    else:
        # Buying option → debit
        if cost_basis > state.cash:
            return None
        state.cash -= cost_basis
        margin_used = cost_basis
    pos, trade = _build_position_record(
        ticker=str(sig.get("ticker", "")).upper(), asset_class="OPTION",
        side="SELL" if is_credit else "BUY", qty=qty, entry_price=premium,
        notional=cost_basis, margin_used=margin_used, sig=sig, key=key, cycle=cycle,
    )
    pos["option_type"] = sig.get("option_type")
    pos["strike"] = sig.get("strike")
    pos["expiry_date"] = sig.get("expiry_date")
    pos["leg_2_strike"] = sig.get("leg_2_strike")
    pos["is_credit"] = is_credit
    state.positions[f"{trade['ticker']}_OPT"] = pos
    return trade


def _check_sl_tp_exits(
    state: ShadowState,
    latest_close: dict[str, float],
    *,
    cycle: int,
) -> list[dict[str, Any]]:
    """Close any open position that breached its stored SL or TP.

    Runs every cycle for every asset class. The position record carries
    ``stop_loss_price`` / ``take_profit_price`` (set when the signal
    fills) and ``direction`` (LONG vs SHORT) so this check works for
    long shorts symmetrically. Options skip price-based SL/TP and rely
    on the readiness checker / external monitor (handled in
    `_execute_option`'s margin reservation).
    """
    if not state.positions:
        return []

    closed: list[dict[str, Any]] = []
    to_close: list[tuple[str, dict, float, str]] = []
    for sym, pos in state.positions.items():
        ac = str(pos.get("asset_class") or "EQUITY").upper()
        if ac == "OPTION":
            continue  # premium-based exits live elsewhere
        # FX symbols (e.g. EURUSD) are not always in latest_close;
        # without a fresh mark we cannot safely evaluate exit gates.
        mark = latest_close.get(sym.replace("_OPT", ""))
        if mark is None or mark <= 0:
            continue
        sl = pos.get("stop_loss_price")
        tp = pos.get("take_profit_price")
        direction = str(pos.get("direction") or "LONG").upper()
        reason: str | None = None
        if sl:
            if direction == "LONG" and mark <= float(sl):
                reason = "stop_loss_hit"
            elif direction == "SHORT" and mark >= float(sl):
                reason = "stop_loss_hit"
        if reason is None and tp:
            if direction == "LONG" and mark >= float(tp):
                reason = "take_profit_hit"
            elif direction == "SHORT" and mark <= float(tp):
                reason = "take_profit_hit"
        if reason:
            to_close.append((sym, pos, mark, reason))

    for sym, pos, mark, reason in to_close:
        ac = str(pos.get("asset_class") or "EQUITY").upper()
        qty = float(pos.get("qty", pos.get("shares", 0.0)) or 0.0)
        entry = float(pos.get("entry_price") or mark)
        multiplier = float(pos.get("contract_multiplier") or 1.0)
        margin_used = float(pos.get("margin_used") or 0.0)
        # P&L: signed-qty × (mark − entry) × multiplier. Equity uses
        # multiplier=1; futures use the contract multiplier embedded
        # at fill time.
        pnl = qty * (mark - entry) * multiplier
        # Cash impact:
        #   * Equity / crypto: reverse the entry — credit notional back
        #     (less the realised P&L sign convention is already in pnl).
        #   * Futures / FX: release margin, add P&L.
        #   * Options: handled separately above (excluded from this loop).
        if ac in ("EQUITY", "ETF", "BOND", "INDEX", "CRYPTO"):
            # Long close = sell |qty|@mark (credit). Short close = buy
            # |qty|@mark (debit). Realised P&L is implicit in the
            # entry-vs-mark difference, so we don't need to add `pnl`
            # separately — that would double-count.
            if qty > 0:
                state.cash += abs(qty) * mark
            else:
                state.cash -= abs(qty) * mark
        else:  # FUTURE / FX — release reserved margin, book P&L
            state.cash += margin_used + pnl
        closed.append({
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
            "mode": "shadow",
            "ticker": sym,
            "asset_class": ac,
            "side": "SELL" if qty > 0 else "BUY",
            "qty": abs(qty),
            "fill_price": round(mark, 6),
            "notional": round(abs(qty) * mark * multiplier, 2),
            "realized_pnl": round(pnl, 2),
            "reason": reason,
            "strategy": pos.get("strategy"),
            "source": "sl_tp_exit",
            "signal_key": pos.get("signal_key"),
            "intent_id": pos.get("intent_id"),
            "cycle": cycle,
            "status": "FILLED",
        })
        log.info(
            f"shadow exit ({ac}): {sym} {reason} @ {mark:.4f} "
            f"(entry={entry:.4f}, pnl={pnl:+,.2f})"
        )
        # Free the open thesis so a new signal can land next cycle.
        try:
            from execution_engine.signal_book import get_signal_book
            sb = get_signal_book()
            intent = pos.get("intent_id")
            if intent:
                sb.close_open_thesis(intent)
        except Exception as exc:  # pragma: no cover — defensive
            log.debug(f"close_open_thesis failed for {sym}: {exc}")
        del state.positions[sym]

    return closed


def _consume_signal_book(
    state: ShadowState,
    latest_close: dict[str, float],
    *,
    cycle: int,
) -> list[dict[str, Any]]:
    """Turn live signal-book entries into shadow fills.

    Routes by asset class to per-class executors. The router handles
    dedupe, readiness gating, HOLD short-circuit, open-thesis pinning,
    and signal-book registration. Per-class executors only decide
    qty/notional/cash semantics.
    """
    if not _SIGNAL_BOOK_PATH.exists():
        return []
    try:
        book = json.loads(_SIGNAL_BOOK_PATH.read_text())
    except json.JSONDecodeError:
        return []
    if not isinstance(book, dict) or not book:
        return []

    from execution_engine.signal_book import (
        compose_thesis_signature,
        get_signal_book,
    )

    sb = get_signal_book()
    sb_open_tickers = sb.open_thesis_tickers()

    fills: list[dict[str, Any]] = []
    for key, sig in book.items():
        if not isinstance(sig, dict):
            continue
        if key in state.consumed_signals:
            continue

        # Asset-class normalisation. Prefer the explicit asset_class
        # field (set by the worker for every new signal); fall back to
        # instrument_type for backward compat with older book entries.
        ac_raw = str(
            sig.get("asset_class")
            or sig.get("instrument_type")
            or "EQUITY"
        ).upper()
        asset_class = _INSTRUMENT_TO_ASSET_CLASS.get(ac_raw, ac_raw)
        if asset_class not in {"EQUITY", "ETF", "FX", "COMMODITY", "CRYPTO", "FUTURE", "BOND", "INDEX", "OPTION"}:
            continue

        ticker = str(sig.get("ticker") or "").upper()
        if not ticker:
            continue
        # Options key under "<TICKER>_OPT" so they don't collide with the
        # equity position; everything else collides as expected.
        position_key = f"{ticker}_OPT" if asset_class == "OPTION" else ticker
        if position_key in state.positions:
            continue
        if ticker in sb_open_tickers:
            log.info(
                f"shadow: refusing duplicate position on {ticker} — "
                f"open thesis already registered in signal_book"
            )
            continue

        readiness_status = str(sig.get("readiness_status", "WAITING")).upper()
        if readiness_status in ("EXPIRED", "CANCELLED"):
            continue
        if readiness_status == "WAITING":
            log.debug(
                f"shadow: filling {ticker} despite WAITING readiness "
                f"(checker may not have evaluated yet)"
            )

        direction = str(sig.get("direction", "LONG")).upper()
        if direction == "HOLD":
            continue
        side = "SELL" if direction == "SHORT" else "BUY"

        mark = (
            latest_close.get(ticker)
            or float(sig.get("entry_price") or 0.0)
            or float(sig.get("entry_price_at_compute") or 0.0)
        )
        if mark <= 0:
            continue

        try:
            if asset_class in ("EQUITY", "ETF", "BOND", "INDEX"):
                trade = _execute_equity_or_etf(
                    sig=sig, mark=mark, side=side, asset_class=asset_class,
                    state=state, key=key, cycle=cycle,
                )
            elif asset_class in ("FUTURE", "COMMODITY"):
                trade = _execute_future(
                    sig=sig, mark=mark, side=side, state=state, key=key, cycle=cycle,
                )
            elif asset_class == "FX":
                trade = _execute_fx(
                    sig=sig, mark=mark, side=side, state=state, key=key, cycle=cycle,
                )
            elif asset_class == "CRYPTO":
                trade = _execute_crypto(
                    sig=sig, mark=mark, side=side, state=state, key=key, cycle=cycle,
                )
            elif asset_class == "OPTION":
                trade = _execute_option(
                    sig=sig, mark=mark, side=side, state=state, key=key, cycle=cycle,
                )
            else:  # pragma: no cover — unreachable after the filter above
                trade = None
        except Exception as exc:
            log.warning(f"shadow: per-class executor failed for {ticker} ({asset_class}): {exc}")
            trade = None

        if trade is None:
            continue

        signature = compose_thesis_signature(sig.get("causal_intel"))
        sb.register_open_thesis(
            trade.get("intent_id") or f"shadow|{key}|{cycle}",
            ticker,
            thesis_signature=signature,
        )
        sb_open_tickers.add(ticker)
        state.consumed_signals.add(key)
        fills.append(trade)
        log.info(
            f"shadow fill ({asset_class}): {trade['side']} {trade['qty']} {ticker} "
            f"@ {trade['fill_price']:.4f} (notional={trade['notional']:.0f}, "
            f"margin={trade['margin_used']:.0f}, key={key})"
        )

    return fills


async def _shadow_cycle(state: ShadowState) -> EquitySnapshot:
    """Run one shadow trading cycle against most recent local data.

    Execution order:
      1. Consume live entries from ``data_store/signal_book.json`` —
         these are pre-computed LLM decisions; shadow mints a fill.
      2. Scan local bars with ``rule_based_orchestrate`` as a cold-start
         fallback when the signal book is empty.
      3. Mark open positions to the latest close and emit a snapshot.

    All fills land in ``live/trades.jsonl``; positions in
    ``live/positions.json``; equity snapshots in
    ``live/equity_curve.jsonl``.
    """
    from backtesting.engine import _load_local_bars
    from execution_engine.math.kelly import compute_position_size, kelly_fraction_single
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext,
        rule_based_orchestrate,
    )

    _DATA = Path("data_store/ohlcv")
    if not _DATA.exists():
        return EquitySnapshot(
            timestamp=dt.datetime.now(dt.UTC).isoformat(),
            mode="shadow",
            equity=state.cash,
            cash=state.cash,
            position_value=0.0,
            open_positions=0,
            daily_pnl=0.0,
            cumulative_pnl=0.0,
            cumulative_return_pct=0.0,
            cycle=state.cycle,
            notes="no_local_data — run `ayvid data ingest` first",
        )

    available = sorted(p.stem for p in _DATA.glob("*.jsonl") if p.stat().st_size > 0)[:30]
    if not available:
        return EquitySnapshot(
            timestamp=dt.datetime.now(dt.UTC).isoformat(),
            mode="shadow",
            equity=state.cash,
            cash=state.cash,
            position_value=0.0,
            open_positions=0,
            daily_pnl=0.0,
            cumulative_pnl=0.0,
            cumulative_return_pct=0.0,
            cycle=state.cycle,
            notes="no_local_data",
        )

    latest_close: dict[str, float] = {}
    mark_meta: dict[str, dict[str, Any]] = {}
    _INTRADAY_DIR = Path("data_store/intraday")
    now_utc = dt.datetime.now(dt.UTC)
    for sym in available:
        # Prefer intraday 5-min bars when available; they are typically
        # 5–15 minutes fresher than the EOD jsonl. Fall back to EOD
        # otherwise. Always record the bar's timestamp and source so the
        # UI can warn on staleness.
        intraday_path = _INTRADAY_DIR / f"{sym}_5m.jsonl"
        chosen_ts: str | None = None
        chosen_src: str | None = None
        chosen_price: float | None = None
        if intraday_path.exists() and intraday_path.stat().st_size > 0:
            try:
                with intraday_path.open() as fh:
                    last_line = ""
                    for line in fh:
                        if line.strip():
                            last_line = line
                if last_line:
                    rec = json.loads(last_line)
                    chosen_price = float(rec.get("close", 0.0)) or None
                    chosen_ts = rec.get("timestamp")
                    chosen_src = "ohlcv_intraday_5m"
            except (OSError, ValueError, json.JSONDecodeError):
                chosen_price = None
        if chosen_price is None:
            bars = _load_local_bars(sym)
            if not bars:
                continue
            chosen_price = bars[-1].close
            chosen_ts = getattr(bars[-1], "timestamp", None)
            chosen_ts = chosen_ts.isoformat() if hasattr(chosen_ts, "isoformat") else str(chosen_ts)
            chosen_src = "ohlcv_eod"
        latest_close[sym] = chosen_price
        # Compute age (best-effort). Cap at a sane bound so a malformed
        # timestamp does not produce an absurd age.
        age_seconds: float | None = None
        if chosen_ts:
            try:
                ts = dt.datetime.fromisoformat(chosen_ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.UTC)
                age_seconds = max(0.0, (now_utc - ts).total_seconds())
            except (TypeError, ValueError):
                age_seconds = None
        mark_meta[sym] = {
            "mark_price_timestamp": chosen_ts,
            "mark_price_source": chosen_src,
            "mark_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        }

    new_fills: list[dict[str, Any]] = []

    # 0) Close any position that breached its SL/TP since the last
    #    cycle. Doing this BEFORE consuming new signals frees up cash /
    #    margin and clears the open-thesis pin so a fresh signal in
    #    the same regime can replace the stopped trade in the same
    #    tick.
    exit_fills = _check_sl_tp_exits(state, latest_close, cycle=state.cycle)
    new_fills.extend(exit_fills)

    # 1) Consume pre-computed signal book. This is the "morning meeting"
    #    path — heavy LLM analysis already landed a trade decision; shadow
    #    mode mints a simulated fill so the live frontend (Orders Routed
    #    card) reflects signals even without an IB Gateway.
    book_fills = _consume_signal_book(
        state,
        latest_close,
        cycle=state.cycle,
    )
    new_fills.extend(book_fills)

    # 2) Fall back to fresh orchestrator scan on local bars — useful when
    #    the signal book is empty (cold start) or only covers symbols we
    #    already hold.
    from execution_engine.signal_book import get_signal_book
    sb_for_scan = get_signal_book()
    sb_open_for_scan = sb_for_scan.open_thesis_tickers()
    for sym in available[:10]:
        bars = _load_local_bars(sym)
        if len(bars) < 30:
            continue
        last_bar = bars[-1]
        if sym in state.positions or sym in sb_open_for_scan:
            continue

        closes = [b.close for b in bars[-30:]]
        avg = sum(closes) / len(closes)
        std = (sum((c - avg) ** 2 for c in closes) / len(closes)) ** 0.5 or 1.0
        z = (last_bar.close - avg) / std

        ctx = MarketContext(
            ticker=sym,
            current_price=last_bar.close,
            asset_class="EQUITY",
            z_score=z,
            rsi=50.0 if z > -2 else 25.0,
            atr=std,
            ret_5d=(last_bar.close / closes[-6] - 1) if len(closes) > 5 else 0.0,
            ret_20d=(last_bar.close / closes[-21] - 1) if len(closes) > 20 else 0.0,
            volatility_regime="NORMAL",
            trend_regime="CHOPPY",
            hurst=0.5,
            volume_ratio=1.2,
            trend_strength=0.0,
            daily_pnl_pct=0.0,
            open_positions_count=len(state.positions),
        )
        decision = rule_based_orchestrate(ctx)
        if decision.action != "EXECUTE":
            continue
        if decision.instrument_type not in ("EQUITY", "ETF", "FX", "COMMODITY_FUTURE", "CRYPTO", "RATE_FUTURE", "INDEX_FUTURE", "FUTURE", "BOND"):
            continue

        # Portfolio-level risk gate. Run BEFORE per-class sizing so we
        # can reject quickly if the portfolio is already at limits.
        from execution_engine.math.portfolio_risk import get_portfolio_risk_manager

        risk_mgr = get_portfolio_risk_manager()
        risk_mgr.update_portfolio_state(
            positions=state.positions,
            total_equity=state.cash + sum(
                p.get("shares", 0) * p.get("entry_price", 0)
                for p in state.positions.values()
            ),
            peak_equity=getattr(state, "peak_equity", state.cash),
        )
        proposed_notional = compute_position_size(state.cash, 0.05, last_bar.close) * last_bar.close
        risk_check = risk_mgr.check_risk(
            ticker=sym,
            asset_class=decision.instrument_type or "EQUITY",
            proposed_notional=proposed_notional,
        )
        if not risk_check.allowed:
            log.info(f"[RISK-GATE] {sym} blocked: {risk_check.reason}")
            continue

        # Build a synthetic signal-book row and route through the SAME
        # per-class executors that consume the LLM signal book. This
        # eliminates the previous gap where this fallback path debited
        # full notional for futures (instead of margin) and produced
        # positions without SL/TP fields the exit checker needs.
        from execution_engine.math.position_sizer import compute_quantity_for_signal

        kelly_frac = min(
            kelly_fraction_single(decision.conviction, max(decision.expected_r, 1.0)),
            0.05,
        )
        ac_norm = _INSTRUMENT_TO_ASSET_CLASS.get(
            (decision.instrument_type or "EQUITY").upper(),
            "EQUITY",
        )

        # SL/TP absolute prices, direction-aware (LONG below / SHORT above).
        direction_up = str(decision.direction).upper()
        sl_pct = float(decision.stop_loss_pct or 0.0) / 100.0
        tp_pct = float(decision.take_profit_pct or 0.0) / 100.0
        if direction_up == "LONG":
            sl_price = last_bar.close * (1.0 - sl_pct)
            tp_price = last_bar.close * (1.0 + tp_pct)
        elif direction_up == "SHORT":
            sl_price = last_bar.close * (1.0 + sl_pct)
            tp_price = last_bar.close * (1.0 - tp_pct)
        else:
            sl_price = last_bar.close
            tp_price = last_bar.close

        sized = compute_quantity_for_signal(
            ticker=sym,
            asset_class=ac_norm,
            instrument_type=decision.instrument_type,
            direction=decision.direction,
            entry_price=last_bar.close,
            stop_loss_price=sl_price,
            portfolio_value=state.cash,
            kelly_fraction=kelly_frac,
        )
        if sized.quantity <= 0:
            continue
        applied_qty = sized.quantity
        if risk_check.size_reduction < 1.0:
            applied_qty = max(1.0, applied_qty * risk_check.size_reduction)

        synthetic_sig: dict[str, Any] = {
            "ticker": sym,
            "asset_class": ac_norm,
            "instrument_type": decision.instrument_type,
            "direction": decision.direction,
            "quantity": applied_qty,
            "entry_price": last_bar.close,
            "entry_price_at_compute": last_bar.close,
            "stop_loss_price": sl_price,
            "take_profit_price": tp_price,
            "notional_value": sized.notional_value,
            "margin_required": sized.margin_required,
            "contract_multiplier": sized.contract_multiplier,
            "pip_value": sized.pip_value,
            "lot_size": sized.lot_size,
            "kelly_fraction": kelly_frac,
            "rationale": decision.rationale,
            "strategy_name": decision.strategy_name,
            "conviction": decision.conviction,
            "expected_r": decision.expected_r,
            "readiness_status": "READY",  # cold-start path bypasses checker
        }
        side = "SELL" if direction_up == "SHORT" else "BUY"
        scan_key = f"orch_scan|{sym}|{state.cycle}"

        if ac_norm in ("EQUITY", "ETF", "BOND", "INDEX"):
            trade = _execute_equity_or_etf(
                sig=synthetic_sig, mark=last_bar.close, side=side,
                asset_class=ac_norm, state=state, key=scan_key, cycle=state.cycle,
            )
        elif ac_norm in ("FUTURE", "COMMODITY"):
            trade = _execute_future(
                sig=synthetic_sig, mark=last_bar.close, side=side,
                state=state, key=scan_key, cycle=state.cycle,
            )
        elif ac_norm == "FX":
            trade = _execute_fx(
                sig=synthetic_sig, mark=last_bar.close, side=side,
                state=state, key=scan_key, cycle=state.cycle,
            )
        elif ac_norm == "CRYPTO":
            trade = _execute_crypto(
                sig=synthetic_sig, mark=last_bar.close, side=side,
                state=state, key=scan_key, cycle=state.cycle,
            )
        elif ac_norm == "OPTION":
            trade = _execute_option(
                sig=synthetic_sig, mark=last_bar.close, side=side,
                state=state, key=scan_key, cycle=state.cycle,
            )
        else:
            trade = None

        if trade is not None:
            trade["source"] = "orchestrator_scan"
            new_fills.append(trade)

    # 3) Mark every position (including freshly-opened ones) to the
    #    current close using SIGNED share counts, so shorts produce
    #    negative position value and correctly net against the cash
    #    credit we booked on entry. Running this AFTER both fill
    #    sources keeps equity accounting coherent regardless of mix.
    #
    #    Position-value semantics:
    #      EQUITY/ETF/CRYPTO/BOND : last × signed_qty
    #        (entry already debited cash, mark gives current value)
    #      FUTURE / FX            : margin_used + (last - entry) × signed_qty × multiplier
    #        (entry only locked margin; equity reflects unrealised P&L)
    position_value = 0.0
    for sym, pos in state.positions.items():
        ac = str(pos.get("asset_class") or "EQUITY").upper()
        signed_qty = float(pos.get("shares", pos.get("qty", 0.0)) or 0.0)
        # Look up under both raw and de-suffixed key for options.
        last = latest_close.get(sym) or latest_close.get(sym.replace("_OPT", "")) or float(pos.get("entry_price", 0.0))
        if ac in ("FUTURE", "COMMODITY", "FX"):
            entry_p = float(pos.get("entry_price", 0.0))
            mult = float(pos.get("contract_multiplier") or 1.0)
            margin_used = float(pos.get("margin_used") or 0.0)
            position_value += margin_used + (last - entry_p) * signed_qty * mult
        elif ac == "OPTION":
            # Premium-based; mark-to-market needs option chain data we
            # don't fetch in shadow mode. Carry at entry cost basis.
            position_value += float(pos.get("notional") or 0.0) * (1 if signed_qty > 0 else -1)
        else:
            position_value += last * signed_qty

    for fill in new_fills:
        _append_jsonl(TRADES_PATH, fill)

    # Persist positions as a list of enriched rows — this is the shape
    # the frontend Portfolio page renders directly. Keep the in-memory
    # dict (state.positions) as the source of truth for execution
    # logic; serialize a denormalized view for the UI.
    position_rows: list[dict[str, Any]] = []
    max_age = 0.0
    for sym, pos in state.positions.items():
        shares = float(pos.get("shares", 0))
        entry = float(pos.get("entry_price", 0.0))
        if sym in latest_close:
            mark = float(latest_close[sym])
            meta = mark_meta.get(sym, {})
        else:
            # No bar at all → fall back to entry price; flag the source.
            mark = entry
            meta = {
                "mark_price_timestamp": None,
                "mark_price_source": "fallback_entry_price",
                "mark_age_seconds": None,
            }
        mv = mark * shares
        upnl = (mark - entry) * shares
        if isinstance(meta.get("mark_age_seconds"), (int, float)):
            max_age = max(max_age, float(meta["mark_age_seconds"]))
        position_rows.append(
            {
                "ticker": sym,
                "asset_class": pos.get("asset_class"),
                "quantity": shares,
                "side": "LONG" if shares >= 0 else "SHORT",
                "avg_price": round(entry, 4),
                "avg_cost": round(entry, 4),
                "market_price": round(mark, 4),
                "mark_price_timestamp": meta.get("mark_price_timestamp"),
                "mark_price_source": meta.get("mark_price_source"),
                "mark_age_seconds": meta.get("mark_age_seconds"),
                "market_value": round(mv, 2),
                "unrealized_pnl": round(upnl, 2),
                "realized_pnl": 0.0,
                "stop_loss_price": pos.get("stop_loss_price"),
                "take_profit_price": pos.get("take_profit_price"),
                "stop_loss_pct": pos.get("stop_loss_pct"),
                "take_profit_pct": pos.get("take_profit_pct"),
                "contract_multiplier": pos.get("contract_multiplier"),
                "margin_used": pos.get("margin_used"),
                "notional": pos.get("notional"),
                "entered_at": pos.get("entered_at"),
                "strategy": pos.get("strategy"),
                "strategy_name": pos.get("strategy_name"),
                "source": pos.get("source"),
                "rationale": pos.get("rationale"),
                "signal_key": pos.get("signal_key"),
            }
        )

    atomic_write_json(
        POSITIONS_PATH,
        {
            "as_of": dt.datetime.now(dt.UTC).isoformat(),
            "cash": round(state.cash, 2),
            # Mode tells the UI whether marks are EOD-shadow, IB-delayed,
            # or live. Worst-case mark age across rows lets the UI render
            # a single header badge instead of one per row.
            "mode": "shadow",
            "max_mark_age_seconds": round(max_age, 1) if max_age else None,
            "positions": position_rows,
        },
        indent=2,
        default=str,
    )
    _sync_path_to_gcs(POSITIONS_PATH)

    equity = state.cash + position_value
    daily_pnl = equity - state.last_equity if state.last_equity > 0 else 0.0
    state.cumulative_pnl = equity - state.initial_equity
    state.last_equity = equity

    return EquitySnapshot(
        timestamp=dt.datetime.now(dt.UTC).isoformat(),
        mode="shadow",
        equity=round(equity, 2),
        cash=round(state.cash, 2),
        position_value=round(position_value, 2),
        open_positions=len(state.positions),
        daily_pnl=round(daily_pnl, 2),
        cumulative_pnl=round(state.cumulative_pnl, 2),
        cumulative_return_pct=round(
            (equity / state.initial_equity - 1) * 100 if state.initial_equity > 0 else 0.0, 4
        ),
        cycle=state.cycle,
    )


async def _run_shadow_mode(
    initial_equity: float,
    interval_seconds: int,
    state_holder: dict[str, ShadowState] | None = None,
) -> None:
    log.info(
        f"Shadow mode: ${initial_equity:,.0f} initial equity, "
        f"polling every {interval_seconds}s (no IB connection)"
    )
    state = ShadowState(
        initial_equity=initial_equity, cash=initial_equity, last_equity=initial_equity
    )
    if state_holder is not None:
        state_holder["state"] = state

    while not _stop_event.is_set():
        state.cycle += 1
        try:
            snap = await _shadow_cycle(state)
            _append_jsonl(EQUITY_CURVE_PATH, asdict(snap))
            log.info(
                f"[{state.cycle:>4}] equity=${snap.equity:,.2f} "
                f"cash=${snap.cash:,.2f} open={snap.open_positions} "
                f"daily=${snap.daily_pnl:+,.2f} cum={snap.cumulative_return_pct:+.3f}%"
                + (f" [{snap.notes}]" if snap.notes else "")
            )
        except Exception as exc:
            log.warning(f"shadow cycle {state.cycle} failed: {exc}")

        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


# ────────────────────────────────────────────────────────────────
# Public entrypoint
# ────────────────────────────────────────────────────────────────


async def run(
    shadow: bool = False,
    initial_equity: float = 1_000_000.0,
    interval_seconds: int = 60,
    with_pipeline: bool = False,
    pipeline_interval_minutes: int = 15,
) -> None:
    """Start the paper-trading runner. Blocks until SIGINT/SIGTERM.

    When ``with_pipeline=True``, the continuous ingestion pipeline
    (news → NER → graph → causal analysis → signal book) runs
    concurrently alongside the trading loop. This is the production
    configuration: signals are continuously refreshed from live data
    and the runner consumes them each cycle.

    Mode resolution priority:
        1. Explicit ``shadow=True`` (e.g. CLI ``--shadow``)
        2. ``settings.EXECUTION_MODE`` (env-driven, set in compute.tf)
        3. Default to ib_paper.
    """
    global _stop_event
    _stop_event = asyncio.Event()
    _ensure_dirs()
    # Identity stamp BEFORE anything else: makes "which image is running"
    # answerable from /api/v1/system/runner-info even if the rest of
    # init explodes. The CI smoke test depends on this firing first.
    _write_deploy_marker()

    # Explicit --shadow flag wins. Otherwise consult the env-driven
    # EXECUTION_MODE so a deploy that sets EXECUTION_MODE=shadow doesn't
    # silently start trying to talk to a non-existent IB Gateway.
    from shared.config import ExecutionMode

    env_mode = getattr(settings, "EXECUTION_MODE", ExecutionMode.IB_PAPER)
    if shadow:
        mode = "shadow"
    elif env_mode == ExecutionMode.SHADOW:
        mode = "shadow"
        shadow = True  # re-route through the shadow branch below
    elif env_mode == ExecutionMode.IB_LIVE:
        mode = "ib_live"
    else:
        mode = "ib_paper"
    _write_meta(mode, initial_equity, interval_seconds)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    log.info(f"Started paper runner ({mode}). Stop with Ctrl-C.")

    tasks: list[asyncio.Task] = []

    if with_pipeline:
        log.info(
            f"Starting continuous pipeline (interval={pipeline_interval_minutes}min) "
            f"alongside paper runner"
        )
        tasks.append(
            asyncio.create_task(
                _run_pipeline_loop(pipeline_interval_minutes),
                name="continuous_pipeline",
            )
        )

    # Shared mutable handle so the readiness checker can read live
    # shadow state without a global. Filled by ``_run_shadow_mode``.
    shadow_state_holder: dict[str, ShadowState] = {}

    if shadow:
        tasks.append(
            asyncio.create_task(
                _run_shadow_mode(
                    initial_equity, interval_seconds, shadow_state_holder
                ),
                name="shadow_runner",
            )
        )
    else:
        tasks.append(
            asyncio.create_task(
                _run_ib_mode(interval_seconds),
                name="ib_runner",
            )
        )

    # Trade-readiness checker — promotes WAITING → READY when the live
    # tape enters the entry zone for any cached signal. Runs alongside
    # the runner so the frontend Signals page reflects live readiness
    # without waiting for the next 60s runner cycle.
    tasks.append(
        asyncio.create_task(
            _run_readiness_loop(shadow=shadow, shadow_state_holder=shadow_state_holder),
            name="trade_readiness_checker",
        )
    )

    # Silent-zombie tripwire — see _stall_watchdog docstring. Setting
    # the timeout to 10× the runner's cycle interval gives a comfortable
    # buffer for slow OHLCV fetches without letting a true stall hide.
    tasks.append(
        asyncio.create_task(
            _stall_watchdog(max_silence_seconds=max(600, interval_seconds * 10)),
            name="stall_watchdog",
        )
    )

    await asyncio.gather(*tasks)
    log.info("Paper runner stopped.")


async def _run_readiness_loop(
    *,
    shadow: bool,
    shadow_state_holder: dict[str, ShadowState] | None = None,
) -> None:
    """Run the TradeReadinessChecker until the runner stops.

    The checker is plugged with closures over the runner's live state
    so the WAITING → READY gates reflect actual position holdings,
    cash, and risk in either shadow or IB mode.
    """
    from execution_engine.signal_book import get_signal_book
    from execution_engine.trade_readiness import TradeReadinessChecker

    book = get_signal_book()
    # Make sure the on-disk book is loaded — the worker writes through
    # save_to_disk; the runner may have started after a write.
    try:
        book.load_from_disk()
    except Exception as exc:
        log.warning(f"readiness loader: signal book load failed: {exc}")

    def _has_open_position(ticker: str) -> bool:
        # Shadow mode: consult the in-memory positions dict
        if shadow and shadow_state_holder is not None:
            state = shadow_state_holder.get("state")
            if state is not None and ticker in state.positions:
                return True
        # Always also check the persisted open-thesis set so that an
        # IB-side position survives restarts of the runner.
        try:
            return ticker in book.open_thesis_tickers()
        except Exception:
            return False

    def _portfolio_value() -> float:
        if shadow and shadow_state_holder is not None:
            state = shadow_state_holder.get("state")
            if state is not None:
                return float(state.last_equity or state.initial_equity or state.cash)
        # Fallback: read the latest run_meta initial_equity.
        try:
            if RUN_META_PATH.exists():
                meta = json.loads(RUN_META_PATH.read_text())
                return float(meta.get("initial_equity") or 0.0)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return 0.0

    def _portfolio_open_risk() -> float:
        # Sum risk across all open positions: |entry − SL| × quantity.
        # In shadow mode we don't track SL on the position record yet,
        # so approximate via 5% of notional per open name (conservative).
        if shadow and shadow_state_holder is not None:
            state = shadow_state_holder.get("state")
            if state is not None:
                total = 0.0
                for pos in state.positions.values():
                    qty = abs(float(pos.get("shares", 0) or 0))
                    entry = float(pos.get("entry_price", 0.0) or 0.0)
                    total += qty * entry * 0.05
                return total
        return 0.0

    def _portfolio_available_capital() -> float:
        if shadow and shadow_state_holder is not None:
            state = shadow_state_holder.get("state")
            if state is not None:
                return float(state.cash or 0.0)
        return 0.0

    checker = TradeReadinessChecker(
        signal_book=book,
        has_open_position=_has_open_position,
        portfolio_value=_portfolio_value,
        portfolio_open_risk=_portfolio_open_risk,
        portfolio_available_capital=_portfolio_available_capital,
    )

    log.info("readiness checker spawned alongside paper runner")
    while not (_stop_event is not None and _stop_event.is_set()):
        try:
            await checker.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            log.warning(f"readiness tick error: {exc}")
        try:
            if _stop_event is None:
                await asyncio.sleep(checker.check_interval_seconds)
            else:
                await asyncio.wait_for(
                    _stop_event.wait(),
                    timeout=checker.check_interval_seconds,
                )
        except TimeoutError:
            continue
    log.info("readiness checker stopped")


async def _run_pipeline_loop(interval_minutes: int) -> None:
    """Run the continuous ingestion pipeline alongside the paper runner."""
    from data_primitives.pipeline.continuous_worker import run_ingestion_cycle

    while not _stop_event.is_set():
        try:
            result = await run_ingestion_cycle(run_analysis=True)
            log.info(
                f"Pipeline cycle complete: news={result.get('news_fetched', 0)} "
                f"events={result.get('events_created', 0)} "
                f"signals={result.get('signals_generated', 0)} "
                f"precomputed={result.get('precomputed_signals', 0)}"
            )
        except Exception as exc:
            log.warning(f"Pipeline cycle failed: {exc}")

        try:
            await asyncio.wait_for(
                _stop_event.wait(), timeout=interval_minutes * 60
            )
        except TimeoutError:
            continue


def render_curve(out_path: Path = Path("live/equity_curve.png")) -> Path:
    """Render the equity curve JSONL as a PNG.

    Best-effort: requires matplotlib but degrades to writing a sparkline
    SVG if matplotlib is unavailable. Returns the artifact path.
    """
    if not EQUITY_CURVE_PATH.exists():
        raise FileNotFoundError(f"{EQUITY_CURVE_PATH} not found — run paper runner first")

    rows: list[dict[str, Any]] = []
    for line in EQUITY_CURVE_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        raise ValueError("equity_curve.jsonl is empty")

    import datetime as _dt

    parsed_ts: list[_dt.datetime] = []
    equity: list[float] = []
    for r in rows:
        try:
            parsed_ts.append(_dt.datetime.fromisoformat(r["timestamp"]))
            equity.append(float(r["equity"]))
        except Exception:
            continue
    if not parsed_ts:
        raise ValueError("no parseable timestamps in equity_curve.jsonl")

    initial_equity = equity[0]
    final_equity = equity[-1]
    total_return_pct = (final_equity / initial_equity - 1.0) * 100 if initial_equity else 0.0
    span = parsed_ts[-1] - parsed_ts[0]
    max_eq = max(equity)
    drawdown_pct = (final_equity / max_eq - 1.0) * 100 if max_eq else 0.0

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt

        plt.rcParams.update({"font.family": "DejaVu Sans"})
        fig, ax = plt.subplots(figsize=(12, 5.5))
        ax.plot(parsed_ts, equity, linewidth=1.6, color="#0a84ff")
        ax.fill_between(parsed_ts, equity, min(equity), alpha=0.10, color="#0a84ff")
        ax.axhline(initial_equity, color="#888", linewidth=0.6, linestyle="--", alpha=0.5)

        ax.set_title(
            f"AYVID — Live Paper Trading Equity Curve  ·  "
            f"{parsed_ts[0]:%Y-%m-%d} → {parsed_ts[-1]:%Y-%m-%d}  ·  "
            f"{len(equity)} snapshots",
            fontsize=12,
            weight="bold",
        )
        ax.set_xlabel("Time (UTC)")
        ax.set_ylabel("Net Liquidation ($)")
        ax.grid(True, alpha=0.25)

        # Smart axis formatting: by hour if same-day, by day otherwise
        if span.total_seconds() < 86400:
            ax.xaxis.set_major_locator(
                mdates.HourLocator(interval=max(1, int(span.total_seconds() // 3600 // 8)))
            )
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        elif span.days < 14:
            ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, span.days // 8)))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        else:
            ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, span.days // 8)))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        fig.autofmt_xdate(rotation=20, ha="right")

        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

        return_color = "#16a34a" if total_return_pct >= 0 else "#dc2626"
        stats_text = (
            f"Initial: ${initial_equity:,.0f}\n"
            f"Final:   ${final_equity:,.0f}\n"
            f"Return:  {total_return_pct:+.2f}%\n"
            f"DD:      {drawdown_pct:.2f}%"
        )
        ax.text(
            0.99,
            0.02,
            stats_text,
            transform=ax.transAxes,
            fontsize=9,
            family="monospace",
            verticalalignment="bottom",
            horizontalalignment="right",
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor="#f5f5f5",
                edgecolor=return_color,
                linewidth=1.2,
                alpha=0.9,
            ),
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(out_path, dpi=140, bbox_inches="tight")
        plt.close(fig)
    except ImportError:
        timestamps = [r["timestamp"] for r in rows]
        # Fallback: write SVG sparkline
        out_path = out_path.with_suffix(".svg")
        width = 800
        height = 220
        emin, emax = min(equity), max(equity)
        rng = max(emax - emin, 1.0)
        pts = " ".join(
            f"{i / max(len(equity) - 1, 1) * width:.1f},"
            f"{height - (e - emin) / rng * (height - 20) - 10:.1f}"
            for i, e in enumerate(equity)
        )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f'<polyline fill="none" stroke="#0a84ff" stroke-width="1.6" points="{pts}"/>'
            f'<text x="10" y="20" font-family="Helvetica" font-size="12" fill="#444">'
            f"AYVID Paper Equity Curve  |  ${emin:,.0f}–${emax:,.0f}</text></svg>"
        )
        out_path.write_text(svg)

    return out_path
