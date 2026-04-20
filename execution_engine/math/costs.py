"""Dynamic cost-drag model + Expected-Value (EV) hurdle.

Tier-1 institutions model slippage before every trade and refuse
statistical noise. A trade is only valid when its expected PnL clears
a multiple of its round-trip execution cost — otherwise it is alpha
being handed to market-makers as rebate.

This module centralises the arithmetic.  It reuses the already-
calibrated cost tables in `backtesting.cost_model` so production and
backtest share a single source of truth for commission + slippage.

The Institutional EV Hurdle:
    expected_pnl_usd  >=  EV_HURDLE_MULTIPLE * round_trip_cost_usd
"""
from __future__ import annotations

from dataclasses import dataclass

from backtesting.cost_model import (
    equity_costs,
    future_costs,
    option_costs,
    spread_costs,
)
from shared.logger import get_logger

log = get_logger("math.costs")


# ── Institutional EV hurdle ─────────────────────────────────────────
# 4.0 is the Tier-1 proprietary-firm default. The reasoning:
#   - round-trip cost on a liquid US equity is ≈ 10-15 bps
#   - for the trade to be *mathematically profitable* after an edge
#     haircut (we never realise full theoretical edge), expected PnL
#     must clear several multiples of the cost, not just ≥ cost.
#   - 4× is the empirically stable multiplier where the signal-to-noise
#     is high enough that cost drag stops dominating the distribution.
EV_HURDLE_MULTIPLE = 4.0


@dataclass(frozen=True)
class EVReport:
    """Transparent breakdown of the EV gate decision."""

    expected_pnl_usd: float
    round_trip_cost_usd: float
    hurdle_usd: float
    ratio: float  # expected_pnl / round_trip_cost
    passes: bool
    reason: str


def _safe(x: float) -> float:
    """Protect against NaN / inf arithmetic polluting the gate."""
    try:
        if x != x:  # NaN
            return 0.0
        if x in (float("inf"), float("-inf")):
            return 0.0
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ── Round-trip cost estimators ──────────────────────────────────────
def estimate_equity_cost(
    *, entry_price: float, shares: int,
) -> float:
    """Round-trip commission + slippage for an equity position."""
    if shares <= 0 or entry_price <= 0:
        return 0.0
    tc = equity_costs(
        entry_price=entry_price,
        exit_price=entry_price,  # same-side estimate for gating
        shares=shares,
    )
    return _safe(tc.total)


def estimate_future_cost(
    *, underlying: str, price: float, lots: int,
    multiplier: float, usd_rate: float,
) -> float:
    if lots <= 0 or price <= 0:
        return 0.0
    tc = future_costs(
        underlying=underlying,
        entry_price=price,
        exit_price=price,
        lots=lots,
        multiplier=multiplier,
        usd_rate=usd_rate,
    )
    return _safe(tc.total)


def estimate_option_cost(
    *, underlying: str, premium: float, lots: int,
    multiplier: float, usd_rate: float,
) -> float:
    if lots <= 0 or premium <= 0:
        return 0.0
    tc = option_costs(
        underlying=underlying,
        entry_premium=premium,
        exit_premium=premium,
        lots=lots,
        multiplier=multiplier,
        usd_rate=usd_rate,
    )
    return _safe(tc.total)


def estimate_spread_cost(
    *, underlying: str, net_debit: float, lots: int,
    multiplier: float, usd_rate: float,
) -> float:
    if lots <= 0 or net_debit <= 0:
        return 0.0
    tc = spread_costs(
        underlying=underlying,
        net_debit=net_debit,
        exit_value=net_debit,
        lots=lots,
        multiplier=multiplier,
        usd_rate=usd_rate,
    )
    return _safe(tc.total)


# ── EV gate ─────────────────────────────────────────────────────────
def ev_hurdle_passes(
    expected_pnl_usd: float,
    round_trip_cost_usd: float,
    *,
    multiple: float = EV_HURDLE_MULTIPLE,
    label: str = "",
) -> EVReport:
    """Check that expected PnL clears `multiple` × round-trip cost.

    If cost is 0 or negligible (e.g. paper mode, missing spec), we fall
    back to requiring positive expected PnL — we never *help* noise
    through the gate.
    """
    pnl = _safe(expected_pnl_usd)
    cost = max(_safe(round_trip_cost_usd), 0.0)
    hurdle = cost * max(multiple, 1.0)
    ratio = (pnl / cost) if cost > 1e-6 else (float("inf") if pnl > 0 else 0.0)

    if cost <= 1e-6:
        passes = pnl > 0.0
        reason = (
            f"{label or 'EV'}: cost≈0, fallback to PnL>0 → "
            f"{'PASS' if passes else 'FAIL'}"
        )
    else:
        passes = pnl >= hurdle
        reason = (
            f"{label or 'EV'}: E[pnl]=${pnl:,.0f} vs "
            f"{multiple:.1f}×cost=${hurdle:,.0f} "
            f"(ratio={ratio:.2f}) → {'PASS' if passes else 'FAIL'}"
        )

    return EVReport(
        expected_pnl_usd=pnl,
        round_trip_cost_usd=cost,
        hurdle_usd=hurdle,
        ratio=ratio,
        passes=passes,
        reason=reason,
    )


# ── Composite: single call that picks the right cost model ──────────
def evaluate_trade_ev(
    *,
    asset_class: str,
    expected_return_pct: float,
    price: float,
    notional_shares: int = 0,
    lots: int = 0,
    multiplier: float = 1.0,
    usd_rate: float = 1.0,
    underlying: str = "",
    premium: float = 0.0,
    net_debit: float = 0.0,
    stop_distance: float = 0.0,
    multiple: float = EV_HURDLE_MULTIPLE,
    label: str = "",
) -> EVReport:
    """One-shot EV evaluation per asset class.

    `expected_return_pct` is the *fractional* edge we think the trade
    captures (e.g. 0.03 = 3%). It is multiplied against the natural
    notional of each instrument:
        EQUITY   → shares × price × expected_return_pct
        FUTURE   → stop_distance × multiplier × lots × usd_rate
                   (already-priced R-multiple world)
        OPTION   → premium × multiplier × lots × usd_rate × expected_return_pct
        SPREAD   → net_debit × multiplier × lots × usd_rate × expected_return_pct
    """
    ac = (asset_class or "").upper()

    if ac == "EQUITY":
        expected_pnl = notional_shares * price * expected_return_pct
        cost = estimate_equity_cost(entry_price=price, shares=notional_shares)

    elif ac in ("FUTURE", "INDEX_FUTURE"):
        # Futures already denominate edge in points via stop_distance, so
        # expected_return_pct acts as the R-multiple (= reward / risk).
        expected_pnl = (
            abs(stop_distance) * multiplier * lots * usd_rate
            * max(expected_return_pct, 0.0)
        )
        cost = estimate_future_cost(
            underlying=underlying, price=price, lots=lots,
            multiplier=multiplier, usd_rate=usd_rate,
        )

    elif ac in ("INDEX_CALL", "INDEX_PUT", "OPTION"):
        expected_pnl = (
            premium * multiplier * lots * usd_rate
            * max(expected_return_pct, 0.0)
        )
        cost = estimate_option_cost(
            underlying=underlying, premium=premium, lots=lots,
            multiplier=multiplier, usd_rate=usd_rate,
        )

    elif ac in ("INDEX_SPREAD", "SPREAD"):
        expected_pnl = (
            net_debit * multiplier * lots * usd_rate
            * max(expected_return_pct, 0.0)
        )
        cost = estimate_spread_cost(
            underlying=underlying, net_debit=net_debit, lots=lots,
            multiplier=multiplier, usd_rate=usd_rate,
        )

    else:
        log.warning(f"evaluate_trade_ev: unknown asset_class '{asset_class}'")
        return EVReport(
            expected_pnl_usd=0.0, round_trip_cost_usd=0.0, hurdle_usd=0.0,
            ratio=0.0, passes=False,
            reason=f"Unknown asset class '{asset_class}' — refusing trade",
        )

    return ev_hurdle_passes(
        expected_pnl_usd=expected_pnl,
        round_trip_cost_usd=cost,
        multiple=multiple,
        label=label or ac,
    )
