"""Portfolio-level risk manager — enforces cross-asset exposure limits,
drawdown circuit breakers, and concentration guards.

This is the single source of truth for "can I add this position to
the portfolio?" Every live path (orchestrator, paper_runner, broker)
must consult `get_portfolio_risk_manager().allow_new_position()` before
sizing. The backtesting engine uses the same logic via `check_risk()`.

Thread-safe via threading.RLock — same concurrency model as SignalBook.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from shared.config import settings
from shared.logger import get_logger

log = get_logger("portfolio_risk")


# ── Per-asset-class exposure limits (fraction of portfolio equity) ─────
# These are hard caps — the portfolio CANNOT exceed these allocations
# per asset class, regardless of signal quality. Configurable via env.
DEFAULT_ASSET_CLASS_LIMITS: dict[str, float] = {
    "EQUITY": 0.50,           # max 50% in equities
    "INDEX": 0.30,            # max 30% in index F&O
    "FX": 0.15,               # max 15% in FX
    "COMMODITY": 0.15,        # max 15% in commodity futures
    "CRYPTO": 0.10,           # max 10% in crypto
    "ETF": 0.25,              # max 25% in ETFs
    "BOND": 0.15,             # max 15% in bond/rate futures
    "FUTURE": 0.30,           # max 30% in generic futures
    "OPTION": 0.20,           # max 20% in options
}

# Maximum total portfolio exposure (sum of all positions / equity).
# >1.0 allows leverage; 1.0 = no leverage.
MAX_GROSS_EXPOSURE = 1.5

# Maximum number of concurrent positions
MAX_CONCURRENT_POSITIONS = 30

# Per-asset-class max concurrent positions
MAX_POSITIONS_PER_CLASS: dict[str, int] = {
    "EQUITY": 15,
    "INDEX": 8,
    "FX": 8,
    "COMMODITY": 6,
    "CRYPTO": 6,
    "ETF": 10,
    "BOND": 4,
    "FUTURE": 8,
    "OPTION": 8,
}


@dataclass
class ExposureSnapshot:
    """Point-in-time portfolio exposure breakdown."""
    total_equity: float = 1_000_000.0
    peak_equity: float = 1_000_000.0
    gross_exposure: float = 0.0
    gross_exposure_pct: float = 0.0
    per_class_exposure: dict[str, float] = field(default_factory=dict)
    per_class_exposure_pct: dict[str, float] = field(default_factory=dict)
    per_class_positions: dict[str, int] = field(default_factory=dict)
    total_positions: int = 0
    current_drawdown_pct: float = 0.0
    daily_pnl_pct: float = 0.0


@dataclass
class RiskCheckResult:
    """Result of a pre-trade risk check."""
    allowed: bool
    reason: str = ""
    size_reduction: float = 1.0  # 1.0 = full size, 0.5 = half, etc.

    def __bool__(self) -> bool:
        return self.allowed


class PortfolioRiskManager:
    """Enforces portfolio-level risk constraints across all asset classes.

    Checks (in order):
    1. Portfolio drawdown circuit breaker (halt ALL new trades)
    2. Daily PnL stress halt (halt new trades for the day)
    3. Max gross exposure (leverage limit)
    4. Per-asset-class exposure cap
    5. Per-asset-class position count cap
    6. Total position count cap
    7. Concentration guard (single-name max)
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._positions: dict[str, dict[str, Any]] = {}
        self._total_equity: float = 1_000_000.0
        self._peak_equity: float = 1_000_000.0
        self._daily_pnl_pct: float = 0.0
        self._drawdown_halted: bool = False

        # Load limits from settings or defaults
        self._max_drawdown_halt = getattr(settings, "MAX_DRAWDOWN_PCT", 3.0)
        self._daily_pnl_halt = getattr(settings, "DAILY_PNL_HALT_PCT", -2.0)
        self._max_var_pct = getattr(settings, "MAX_VAR_PCT", 5.0)
        self._asset_class_limits = dict(DEFAULT_ASSET_CLASS_LIMITS)
        self._max_gross_exposure = MAX_GROSS_EXPOSURE

    def update_portfolio_state(
        self,
        positions: dict[str, dict[str, Any]] | list[Any],
        total_equity: float,
        peak_equity: float | None = None,
        daily_pnl_pct: float = 0.0,
    ) -> None:
        """Update the risk manager with current portfolio state."""
        with self._lock:
            if isinstance(positions, list):
                self._positions = {}
                for p in positions:
                    key = p.ticker if hasattr(p, "ticker") else p.get("ticker", "")
                    self._positions[key] = {
                        "ticker": key,
                        "asset_class": (
                            p.asset_class.value
                            if hasattr(p, "asset_class") and hasattr(p.asset_class, "value")
                            else str(getattr(p, "asset_class", p.get("asset_class", "EQUITY")))
                        ),
                        "notional": abs(
                            getattr(p, "quantity", p.get("quantity", 0))
                            * getattr(p, "current_price", p.get("current_price", p.get("entry_price", 0)))
                        ),
                    }
            else:
                self._positions = dict(positions)

            self._total_equity = max(total_equity, 1.0)
            if peak_equity is not None:
                self._peak_equity = max(peak_equity, self._total_equity)
            self._daily_pnl_pct = daily_pnl_pct

    def get_exposure_snapshot(self) -> ExposureSnapshot:
        """Build a point-in-time exposure breakdown."""
        with self._lock:
            per_class: dict[str, float] = {}
            per_class_count: dict[str, int] = {}
            gross = 0.0

            for pos in self._positions.values():
                ac = pos.get("asset_class", "EQUITY")
                notional = pos.get("notional", 0.0)
                per_class[ac] = per_class.get(ac, 0.0) + notional
                per_class_count[ac] = per_class_count.get(ac, 0) + 1
                gross += notional

            eq = self._total_equity
            dd = ((self._peak_equity - eq) / self._peak_equity * 100) if self._peak_equity > 0 else 0.0

            return ExposureSnapshot(
                total_equity=eq,
                peak_equity=self._peak_equity,
                gross_exposure=gross,
                gross_exposure_pct=gross / eq if eq > 0 else 0.0,
                per_class_exposure=per_class,
                per_class_exposure_pct={k: v / eq for k, v in per_class.items()} if eq > 0 else {},
                per_class_positions=per_class_count,
                total_positions=len(self._positions),
                current_drawdown_pct=dd,
                daily_pnl_pct=self._daily_pnl_pct,
            )

    def check_risk(
        self,
        ticker: str,
        asset_class: str,
        proposed_notional: float,
    ) -> RiskCheckResult:
        """Pre-trade risk check. Returns whether the trade is allowed.

        This is the single gate every trading path must call before sizing.
        """
        with self._lock:
            eq = self._total_equity
            snap = self.get_exposure_snapshot()

            # 1. Portfolio drawdown circuit breaker
            if snap.current_drawdown_pct >= self._max_drawdown_halt:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Portfolio drawdown {snap.current_drawdown_pct:.1f}% >= "
                           f"{self._max_drawdown_halt}% halt threshold",
                )

            # 2. Daily PnL stress halt
            if self._daily_pnl_pct <= self._daily_pnl_halt:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Daily PnL {self._daily_pnl_pct:+.1f}% <= "
                           f"{self._daily_pnl_halt}% halt threshold",
                )

            # 3. Max gross exposure (leverage limit)
            new_gross_pct = (snap.gross_exposure + proposed_notional) / eq if eq > 0 else 999
            if new_gross_pct > self._max_gross_exposure:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Gross exposure {new_gross_pct:.0%} would exceed "
                           f"{self._max_gross_exposure:.0%} limit",
                )

            # 4. Per-asset-class exposure cap
            ac_key = _normalize_asset_class(asset_class)
            ac_limit = self._asset_class_limits.get(ac_key, 0.30)
            current_ac_exposure = snap.per_class_exposure_pct.get(ac_key, 0.0)
            new_ac_exposure = current_ac_exposure + (proposed_notional / eq if eq > 0 else 0)
            if new_ac_exposure > ac_limit:
                # Allow with size reduction if close to limit
                remaining = max(0, ac_limit - current_ac_exposure)
                if remaining > 0.01:
                    reduction = remaining / (proposed_notional / eq) if proposed_notional > 0 else 0
                    return RiskCheckResult(
                        allowed=True,
                        reason=f"{ac_key} exposure near limit ({current_ac_exposure:.0%}/{ac_limit:.0%}), "
                               f"size reduced to {reduction:.0%}",
                        size_reduction=min(reduction, 1.0),
                    )
                return RiskCheckResult(
                    allowed=False,
                    reason=f"{ac_key} exposure {new_ac_exposure:.0%} would exceed "
                           f"{ac_limit:.0%} cap (current: {current_ac_exposure:.0%})",
                )

            # 5. Per-asset-class position count cap
            ac_count = snap.per_class_positions.get(ac_key, 0)
            max_count = MAX_POSITIONS_PER_CLASS.get(ac_key, 10)
            if ac_count >= max_count:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"{ac_key} has {ac_count} positions (max {max_count})",
                )

            # 6. Total position count cap
            if snap.total_positions >= MAX_CONCURRENT_POSITIONS:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Portfolio has {snap.total_positions} positions "
                           f"(max {MAX_CONCURRENT_POSITIONS})",
                )

            # 7. Single-name concentration guard (max 5% per name)
            max_single_name = getattr(settings, "MAX_POSITION_PCT", 5.0) / 100.0
            if proposed_notional / eq > max_single_name:
                reduction = (max_single_name * eq) / proposed_notional if proposed_notional > 0 else 0
                return RiskCheckResult(
                    allowed=True,
                    reason=f"Single-name cap: {ticker} sized down from "
                           f"{proposed_notional/eq:.1%} to {max_single_name:.1%}",
                    size_reduction=min(reduction, 1.0),
                )

            return RiskCheckResult(allowed=True)

    def register_position(
        self,
        ticker: str,
        asset_class: str,
        notional: float,
    ) -> None:
        """Register a newly opened position."""
        with self._lock:
            self._positions[ticker] = {
                "ticker": ticker,
                "asset_class": _normalize_asset_class(asset_class),
                "notional": abs(notional),
            }

    def remove_position(self, ticker: str) -> None:
        """Remove a closed position."""
        with self._lock:
            self._positions.pop(ticker, None)

    def get_stats(self) -> dict[str, Any]:
        """Return risk manager statistics for monitoring."""
        snap = self.get_exposure_snapshot()
        return {
            "total_positions": snap.total_positions,
            "gross_exposure_pct": round(snap.gross_exposure_pct * 100, 1),
            "per_class_exposure_pct": {
                k: round(v * 100, 1) for k, v in snap.per_class_exposure_pct.items()
            },
            "per_class_positions": snap.per_class_positions,
            "current_drawdown_pct": round(snap.current_drawdown_pct, 2),
            "daily_pnl_pct": round(self._daily_pnl_pct, 2),
            "drawdown_halted": snap.current_drawdown_pct >= self._max_drawdown_halt,
        }


def _normalize_asset_class(ac: str) -> str:
    """Normalize asset class strings to canonical keys."""
    ac = ac.upper().strip()
    mapping = {
        "COMMODITY_FUTURE": "COMMODITY",
        "RATE_FUTURE": "BOND",
        "INDEX_FUTURE": "INDEX",
        "INDEX_CALL": "OPTION",
        "INDEX_PUT": "OPTION",
        "INDEX_SPREAD": "OPTION",
        "CRYPTO_SPOT": "CRYPTO",
        "FX_PAIR": "FX",
    }
    return mapping.get(ac, ac)


# ── Module-level singleton ────────────────────────────────────────
_risk_manager: PortfolioRiskManager | None = None


def get_portfolio_risk_manager() -> PortfolioRiskManager:
    """Return the global portfolio risk manager singleton."""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = PortfolioRiskManager()
    return _risk_manager
