"""TradeReadinessChecker — promotes WAITING signals to READY when every
gate is green.

The ingestion pipeline writes ``PrecomputedSignal`` entries to the
SignalBook the moment a decision is made. Those signals start in
``WAITING`` state — the orchestrator says "buy NVDA at $117 if it
trades there". The checker's job is to look at the live tape every
``CHECK_INTERVAL_SECONDS`` and answer one question per signal:

    Is it tradable RIGHT NOW?

A signal is READY when ALL of the following hold:

    1. Signal is not expired (computed_at + ttl > now)
    2. A live mark is available for the ticker
    3. Current price sits inside the entry zone
    4. The regime fingerprint matches (when a regime detector is wired)
    5. No open position already exists for this ticker
    6. Adding this position does not breach portfolio risk limits
    7. Sufficient capital / margin to enter

The 7th gate is intentionally permissive in shadow mode — we allow
sizing reduction rather than refusing the trade. The IB-mode wiring
plugs in a stricter ``portfolio_available_capital`` callable.

Plug-points (all callables, all optional):

    get_price             (ticker, asset_class) → float | None
    get_regime_fingerprint(ticker)              → str
    has_open_position     (ticker)              → bool
    portfolio_value       ()                    → float
    portfolio_open_risk   ()                    → float
    portfolio_available_capital ()              → float

When a callable is missing the corresponding gate is skipped. The
fallback ``get_price`` reads the latest close from the same on-disk
intraday/EOD bars the paper runner uses, so the readiness checker is
useful even with zero bespoke wiring.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.logger import get_logger

log = get_logger("trade_readiness")


# ── Tunables ─────────────────────────────────────────────────────


DEFAULT_CHECK_INTERVAL_SECONDS = 5.0
DEFAULT_ENTRY_ZONE_PCT = 0.005          # ±0.5% around entry
DEFAULT_MAX_PORTFOLIO_RISK_PCT = 0.06   # 6% of NLV at open risk

_INTRADAY_DIR = Path("data_store/intraday")
_OHLCV_DIR = Path("data_store/ohlcv")


# ── Public result type ───────────────────────────────────────────


@dataclass
class ReadinessResult:
    ticker: str
    status: str                  # WAITING | READY | EXPIRED | CANCELLED
    reason: str
    current_price: float = 0.0
    distance_to_entry_pct: float = 0.0
    entry_price: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    quantity: float = 0.0
    notional_value: float = 0.0
    risk_amount: float = 0.0
    risk_reward_ratio: float = 0.0
    checked_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


# ── Default callables ────────────────────────────────────────────


def _default_get_price(ticker: str, _asset_class: str) -> float | None:
    """Read the freshest close from local OHLCV/intraday bars.

    This is the same fallback chain ``paper_runner._shadow_cycle`` uses
    so the readiness checker stays in sync with the runner's view of
    the tape without any extra wiring.
    """
    intraday_path = _INTRADAY_DIR / f"{ticker}_5m.jsonl"
    if intraday_path.exists() and intraday_path.stat().st_size > 0:
        try:
            with intraday_path.open() as fh:
                last_line = ""
                for line in fh:
                    if line.strip():
                        last_line = line
            if last_line:
                rec = json.loads(last_line)
                close = float(rec.get("close", 0.0))
                if close > 0:
                    return close
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    eod_path = _OHLCV_DIR / f"{ticker}.jsonl"
    if eod_path.exists() and eod_path.stat().st_size > 0:
        try:
            with eod_path.open() as fh:
                last_line = ""
                for line in fh:
                    if line.strip():
                        last_line = line
            if last_line:
                rec = json.loads(last_line)
                close = float(rec.get("close", 0.0))
                if close > 0:
                    return close
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    return None


# ── Helper ───────────────────────────────────────────────────────


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it's awaitable; otherwise return it as-is."""
    if inspect.isawaitable(value):
        return await value
    return value


# ── The checker ──────────────────────────────────────────────────


PriceFn = Callable[[str, str], "float | None | Awaitable[float | None]"]
RegimeFn = Callable[[str], "str | Awaitable[str]"]
HasPositionFn = Callable[[str], "bool | Awaitable[bool]"]
ScalarFn = Callable[[], "float | Awaitable[float]"]


class TradeReadinessChecker:
    """Continuous WAITING → READY promoter for the SignalBook."""

    def __init__(
        self,
        *,
        signal_book: Any,
        get_price: PriceFn = _default_get_price,
        get_regime_fingerprint: RegimeFn | None = None,
        has_open_position: HasPositionFn | None = None,
        portfolio_value: ScalarFn | None = None,
        portfolio_open_risk: ScalarFn | None = None,
        portfolio_available_capital: ScalarFn | None = None,
        check_interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
        entry_zone_pct: float = DEFAULT_ENTRY_ZONE_PCT,
        max_portfolio_risk_pct: float = DEFAULT_MAX_PORTFOLIO_RISK_PCT,
        persist_after_each_cycle: bool = True,
    ) -> None:
        self.signal_book = signal_book
        self.get_price = get_price
        self.get_regime_fingerprint = get_regime_fingerprint
        self.has_open_position = has_open_position
        self.portfolio_value = portfolio_value
        self.portfolio_open_risk = portfolio_open_risk
        self.portfolio_available_capital = portfolio_available_capital
        self.check_interval_seconds = float(check_interval_seconds)
        self.entry_zone_pct = float(entry_zone_pct)
        self.max_portfolio_risk_pct = float(max_portfolio_risk_pct)
        self.persist_after_each_cycle = persist_after_each_cycle
        self._stop_event: asyncio.Event | None = None
        self._cycles_completed = 0

    def request_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        """Main loop. Cancellable via ``stop_event`` or asyncio.cancel()."""
        self._stop_event = stop_event or asyncio.Event()
        log.info(
            f"trade_readiness_checker_started "
            f"interval={self.check_interval_seconds}s "
            f"zone_pct={self.entry_zone_pct:.4f}"
        )
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(f"readiness_checker_tick_error: {exc}")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.check_interval_seconds,
                )
            except TimeoutError:
                continue
        log.info("trade_readiness_checker_stopped")

    async def tick(self) -> list[ReadinessResult]:
        """One readiness sweep over the SignalBook. Returns the per-signal results."""
        # Sweep stale entries first so they don't get re-evaluated.
        self.signal_book.mark_all_expired()

        candidates: dict[str, Any] = {}
        with self.signal_book._lock:
            for k, sig in self.signal_book._signals.items():
                if sig.readiness_status in ("WAITING", "READY"):
                    candidates[k] = sig

        results: list[ReadinessResult] = []
        any_change = False
        for key, sig in candidates.items():
            try:
                result = await self._evaluate(sig)
            except Exception as exc:
                log.warning(f"readiness_evaluate_failed ticker={sig.ticker}: {exc}")
                continue
            results.append(result)
            if result.status != sig.readiness_status or (
                result.current_price > 0
                and abs(result.current_price - (sig.current_price or 0.0)) > 1e-9
            ):
                any_change = True
                self.signal_book.update_readiness(
                    key,
                    status=result.status,
                    current_price=result.current_price,
                    distance_to_entry_pct=result.distance_to_entry_pct,
                    readiness_checked_at=result.checked_at,
                    readiness_reason=result.reason,
                )
                if result.status == "READY":
                    log.info(
                        f"signal_ready ticker={sig.ticker} "
                        f"strategy={sig.strategy_name} "
                        f"entry={sig.entry_price:.4f} current={result.current_price:.4f}"
                    )
                elif result.status == "CANCELLED":
                    log.info(
                        f"signal_cancelled ticker={sig.ticker} "
                        f"reason={result.reason}"
                    )

        self._cycles_completed += 1
        if any_change and self.persist_after_each_cycle:
            try:
                self.signal_book.save_to_disk()
            except Exception as exc:  # pragma: no cover
                log.warning(f"readiness_persist_failed: {exc}")
        return results

    # ── Per-signal evaluation ────────────────────────────────────

    async def _evaluate(self, sig: Any) -> ReadinessResult:
        now = dt.datetime.now(dt.UTC)

        common = dict(
            ticker=sig.ticker,
            entry_price=sig.entry_price or sig.entry_price_at_compute,
            stop_loss_price=sig.stop_loss_price,
            take_profit_price=sig.take_profit_price,
            quantity=sig.quantity,
            notional_value=sig.notional_value,
            risk_amount=sig.risk_amount,
            risk_reward_ratio=sig.risk_reward_ratio,
            checked_at=now,
        )

        # ── Gate 1: TTL ────────────────────────────────────────
        if sig.is_expired:
            return ReadinessResult(
                status="EXPIRED",
                reason=f"ttl_elapsed at {sig.expires_at}",
                **common,
            )

        # HOLD signals are signal-only — never tradable, never READY.
        # Surface them in WAITING so the UI shows the analysis.
        if str(sig.direction).upper() == "HOLD":
            return ReadinessResult(
                status="WAITING",
                reason="hold_signal_no_execution",
                **common,
            )

        # ── Gate 2: live mark ──────────────────────────────────
        try:
            current_price = await _maybe_await(
                self.get_price(sig.ticker, sig.asset_class)
            )
        except Exception as exc:
            current_price = None
            log.debug(f"price_lookup_failed ticker={sig.ticker}: {exc}")
        if current_price is None or current_price <= 0:
            return ReadinessResult(
                status="WAITING",
                reason="price_unavailable",
                **common,
            )
        common["current_price"] = float(current_price)

        entry = common["entry_price"]
        distance_pct = (
            (current_price - entry) / entry if entry > 0 else 0.0
        )
        common["distance_to_entry_pct"] = distance_pct

        # ── Gate 3: entry zone ─────────────────────────────────
        zone_low = sig.entry_zone_low or (entry * (1.0 - self.entry_zone_pct))
        zone_high = sig.entry_zone_high or (entry * (1.0 + self.entry_zone_pct))
        if not (zone_low <= current_price <= zone_high):
            side = "above" if current_price > entry else "below"
            return ReadinessResult(
                status="WAITING",
                reason=(
                    f"outside_entry_zone "
                    f"({distance_pct * 100:+.2f}% {side} entry)"
                ),
                **common,
            )

        # ── Gate 4: regime ─────────────────────────────────────
        if self.get_regime_fingerprint is not None and sig.regime_fingerprint:
            try:
                live_fp = await _maybe_await(
                    self.get_regime_fingerprint(sig.ticker)
                )
            except Exception as exc:
                live_fp = None
                log.debug(f"regime_lookup_failed ticker={sig.ticker}: {exc}")
            if live_fp and live_fp != sig.regime_fingerprint:
                return ReadinessResult(
                    status="CANCELLED",
                    reason=(
                        f"regime_changed: was {sig.regime_fingerprint}, "
                        f"now {live_fp}"
                    ),
                    **common,
                )

        # ── Gate 5: no duplicate position ──────────────────────
        if self.has_open_position is not None:
            try:
                already_open = await _maybe_await(
                    self.has_open_position(sig.ticker)
                )
            except Exception as exc:
                already_open = False
                log.debug(f"position_lookup_failed ticker={sig.ticker}: {exc}")
            if already_open:
                return ReadinessResult(
                    status="WAITING",
                    reason="position_already_open",
                    **common,
                )

        # ── Gate 6: portfolio risk ─────────────────────────────
        if (
            self.portfolio_value is not None
            and self.portfolio_open_risk is not None
        ):
            try:
                pv = float(await _maybe_await(self.portfolio_value()))
                open_risk = float(await _maybe_await(self.portfolio_open_risk()))
                new_risk = float(sig.risk_amount or 0.0)
                max_risk = pv * self.max_portfolio_risk_pct
                if pv > 0 and (open_risk + new_risk) > max_risk:
                    return ReadinessResult(
                        status="WAITING",
                        reason=(
                            f"portfolio_risk_limit: "
                            f"open=${open_risk:,.0f} + new=${new_risk:,.0f} "
                            f"> cap=${max_risk:,.0f}"
                        ),
                        **common,
                    )
            except Exception as exc:
                log.debug(f"portfolio_risk_check_failed ticker={sig.ticker}: {exc}")

        # ── Gate 7: capital / margin ───────────────────────────
        if self.portfolio_available_capital is not None:
            try:
                avail = float(
                    await _maybe_await(self.portfolio_available_capital())
                )
                required = float(
                    sig.margin_required
                    if sig.margin_required is not None
                    else (sig.notional_value or 0.0)
                )
                if avail > 0 and required > avail:
                    return ReadinessResult(
                        status="WAITING",
                        reason=(
                            f"insufficient_capital: "
                            f"need ${required:,.0f}, have ${avail:,.0f}"
                        ),
                        **common,
                    )
            except Exception as exc:
                log.debug(f"capital_check_failed ticker={sig.ticker}: {exc}")

        # ── ALL GATES PASSED → READY ───────────────────────────
        return ReadinessResult(
            status="READY",
            reason="all_gates_passed",
            **common,
        )

    # ── Diagnostics ──────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        return {
            "cycles_completed": self._cycles_completed,
            "interval_seconds": self.check_interval_seconds,
            "entry_zone_pct": self.entry_zone_pct,
            "max_portfolio_risk_pct": self.max_portfolio_risk_pct,
        }
