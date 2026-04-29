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


def _write_meta(mode: str, initial_equity: float, interval_seconds: int) -> None:
    meta = {
        "mode": mode,
        "initial_equity": initial_equity,
        "interval_seconds": interval_seconds,
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "ib_host": settings.IB_HOST,
        "ib_port": settings.IB_PORT,
        "auto_execute": settings.AUTO_EXECUTE,
        "paper_trading": settings.PAPER_TRADING,
    }
    RUN_META_PATH.write_text(json.dumps(meta, indent=2))
    _sync_path_to_gcs(RUN_META_PATH)


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
    from execution_engine.broker.connection import connect

    ib = await connect()
    log.info(
        f"IB paper mode: polling every {interval_seconds}s, account={settings.IB_ACCOUNT or 'default'}"
    )

    cycle = 0
    initial_equity = 0.0
    last_equity = 0.0
    while not _stop_event.is_set():
        cycle += 1
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


# ────────────────────────────────────────────────────────────────
# SHADOW MODE (no IB connection required)
# ────────────────────────────────────────────────────────────────

_SIGNAL_BOOK_PATH = Path("data_store/signal_book.json")


def _consume_signal_book(
    state: ShadowState,
    latest_close: dict[str, float],
    *,
    cycle: int,
) -> list[dict[str, Any]]:
    """Turn live signal-book entries into shadow fills.

    Contract:
      * All asset classes are accepted (EQUITY, ETF, FX, COMMODITY,
        CRYPTO, FUTURE, BOND, INDEX_FUTURE). Crypto signals are
        tracked as signal-only since IBKR Paper doesn't support Paxos.
      * LONG → BUY, SHORT → SELL. We allow SHORT in shadow because the
        cost model already handles both sides.
      * Sizing uses the signal's conviction + expected_r with a single
        Kelly fraction, capped at 5% of cash.
      * Each (ticker, regime_fingerprint) key fires at most once per
        runner lifetime; the `consumed_signals` set is the dedupe guard.
    """
    if not _SIGNAL_BOOK_PATH.exists():
        return []
    try:
        book = json.loads(_SIGNAL_BOOK_PATH.read_text())
    except json.JSONDecodeError:
        return []
    if not isinstance(book, dict) or not book:
        return []

    from execution_engine.math.kelly import (
        compute_position_size,
        kelly_fraction_single,
    )

    fills: list[dict[str, Any]] = []
    for key, sig in book.items():
        if not isinstance(sig, dict):
            continue
        if key in state.consumed_signals:
            continue
        # Accept all asset classes — multi-asset routing
        inst_type = str(sig.get("instrument_type", "EQUITY")).upper()
        if inst_type not in ("EQUITY", "ETF", "FX", "COMMODITY", "CRYPTO", "FUTURE", "BOND", "INDEX_FUTURE"):
            continue
        ticker = str(sig.get("ticker") or "").upper()
        if not ticker or ticker in state.positions:
            continue

        direction = str(sig.get("direction", "LONG")).upper()
        side = "SELL" if direction == "SHORT" else "BUY"

        # Prefer the current mark so the fill is honest vs. "now". Fall
        # back to the signal's committed entry_price_at_compute if we
        # have no local bar for this symbol (e.g. universe drift).
        mark = latest_close.get(ticker) or float(sig.get("entry_price_at_compute") or 0.0)
        if mark <= 0:
            continue

        conv = float(sig.get("conviction", 0.0) or 0.0)
        exp_r = max(float(sig.get("expected_r", 1.0) or 1.0), 1.0)
        frac = min(kelly_fraction_single(conv, exp_r), 0.05)
        shares = compute_position_size(state.cash, frac, mark)
        if shares < 1 or shares * mark > state.cash * 0.4:
            continue

        # Asset-class-specific slippage
        _slip_map = {"FX": 0.0001, "CRYPTO": 0.0010, "COMMODITY": 0.0003, "FUTURE": 0.0002}
        slip_bps = _slip_map.get(inst_type, 0.0005)
        slip = (1 + slip_bps) if side == "BUY" else (1 - slip_bps)
        entry = mark * slip
        cost = entry * shares
        if side == "BUY" and cost > state.cash:
            continue

        # Cash semantics: BUY debits, SELL (short) credits proceeds into cash.
        # Mark-to-market in the cycle handler reconciles the final equity.
        state.cash += -cost if side == "BUY" else cost
        state.positions[ticker] = {
            "entry_price": round(entry, 4),
            "shares": shares if side == "BUY" else -shares,
            "entered_at": dt.datetime.now(dt.UTC).isoformat(),
            "rationale": str(sig.get("rationale") or "signal_book entry"),
            "source": "signal_book",
            "signal_key": key,
            "strategy": sig.get("strategy_name"),
        }
        state.consumed_signals.add(key)
        fills.append(
            {
                "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                "mode": "shadow",
                "ticker": ticker,
                "side": side,
                "shares": shares,
                "fill_price": round(entry, 4),
                "notional": round(entry * shares, 2),
                "rationale": str(sig.get("rationale") or "signal_book entry"),
                "strategy": sig.get("strategy_name"),
                "conviction": conv,
                "expected_r": exp_r,
                "source": "signal_book",
                "signal_key": key,
                "cycle": cycle,
                "status": "FILLED",
            }
        )
        log.info(
            f"shadow fill (signal_book): {side} {shares} {ticker} @ ${entry:.2f} "
            f"(conv={conv:.2f}, exp_r={exp_r:.1f}, key={key})"
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
    for sym in available:
        bars = _load_local_bars(sym)
        if not bars:
            continue
        latest_close[sym] = bars[-1].close

    new_fills: list[dict[str, Any]] = []

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
    for sym in available[:10]:
        bars = _load_local_bars(sym)
        if len(bars) < 30:
            continue
        last_bar = bars[-1]
        if sym in state.positions:
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

        frac = kelly_fraction_single(decision.conviction, max(decision.expected_r, 1.0))
        frac = min(frac, 0.05)
        shares = compute_position_size(state.cash, frac, last_bar.close)
        if shares < 1 or shares * last_bar.close > state.cash * 0.4:
            continue

        # Portfolio-level risk check
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
        risk_check = risk_mgr.check_risk(
            ticker=sym,
            asset_class=decision.instrument_type or "EQUITY",
            proposed_notional=shares * last_bar.close,
        )
        if not risk_check.allowed:
            log.info(f"[RISK-GATE] {sym} blocked: {risk_check.reason}")
            continue
        if risk_check.size_reduction < 1.0:
            shares = max(1, int(shares * risk_check.size_reduction))

        # Apply asset-class-specific slippage for shadow fill.
        _orch_slip = {"FX": 0.0001, "CRYPTO": 0.0010, "COMMODITY_FUTURE": 0.0003, "ETF": 0.0003}
        slip_bps = _orch_slip.get(decision.instrument_type, 0.0005)
        entry = last_bar.close * (1 + slip_bps)
        cost = entry * shares
        if cost > state.cash:
            continue

        state.cash -= cost
        state.positions[sym] = {
            "entry_price": round(entry, 4),
            "shares": shares,
            "entered_at": last_bar.timestamp.isoformat(),
            "rationale": decision.rationale,
            "source": "orchestrator_scan",
        }
        new_fills.append(
            {
                "timestamp": dt.datetime.now(dt.UTC).isoformat(),
                "mode": "shadow",
                "ticker": sym,
                "side": "BUY",
                "shares": shares,
                "fill_price": round(entry, 4),
                "notional": round(entry * shares, 2),
                "rationale": decision.rationale,
                "source": "orchestrator_scan",
                "cycle": state.cycle,
                "status": "FILLED",
            }
        )

    # 3) Mark every position (including freshly-opened ones) to the
    #    current close using SIGNED share counts, so shorts produce
    #    negative position value and correctly net against the cash
    #    credit we booked on entry. Running this AFTER both fill
    #    sources keeps equity accounting coherent regardless of mix.
    position_value = 0.0
    for sym, pos in state.positions.items():
        last = latest_close.get(sym, pos["entry_price"])
        position_value += last * pos["shares"]

    for fill in new_fills:
        _append_jsonl(TRADES_PATH, fill)

    # Persist positions as a list of enriched rows — this is the shape
    # the frontend Portfolio page renders directly. Keep the in-memory
    # dict (state.positions) as the source of truth for execution
    # logic; serialize a denormalized view for the UI.
    position_rows: list[dict[str, Any]] = []
    for sym, pos in state.positions.items():
        shares = float(pos.get("shares", 0))
        entry = float(pos.get("entry_price", 0.0))
        mark = float(latest_close.get(sym, entry))
        mv = mark * shares
        upnl = (mark - entry) * shares
        position_rows.append(
            {
                "ticker": sym,
                "quantity": shares,
                "side": "LONG" if shares >= 0 else "SHORT",
                "avg_price": round(entry, 4),
                "market_price": round(mark, 4),
                "market_value": round(mv, 2),
                "unrealized_pnl": round(upnl, 2),
                "realized_pnl": 0.0,
                "entered_at": pos.get("entered_at"),
                "strategy": pos.get("strategy"),
                "source": pos.get("source"),
                "rationale": pos.get("rationale"),
                "signal_key": pos.get("signal_key"),
            }
        )

    POSITIONS_PATH.write_text(
        json.dumps(
            {
                "as_of": dt.datetime.now(dt.UTC).isoformat(),
                "cash": round(state.cash, 2),
                "positions": position_rows,
            },
            indent=2,
            default=str,
        )
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


async def _run_shadow_mode(initial_equity: float, interval_seconds: int) -> None:
    log.info(
        f"Shadow mode: ${initial_equity:,.0f} initial equity, "
        f"polling every {interval_seconds}s (no IB connection)"
    )
    state = ShadowState(
        initial_equity=initial_equity, cash=initial_equity, last_equity=initial_equity
    )

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
    """
    global _stop_event
    _stop_event = asyncio.Event()
    _ensure_dirs()
    mode = "shadow" if shadow else "ib_paper"
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

    if shadow:
        tasks.append(
            asyncio.create_task(
                _run_shadow_mode(initial_equity, interval_seconds),
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

    await asyncio.gather(*tasks)
    log.info("Paper runner stopped.")


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
