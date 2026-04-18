"""Risk metrics computation — VaR, drawdown tracking, and Sharpe ratio.

These metrics feed the kill switch memory-mapped file at 10Hz.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from shared.logger import get_logger
from shared.types import OHLCVBar, PortfolioState, Position, RiskSnapshot

log = get_logger("math.risk")


def compute_var(
    returns: np.ndarray,
    confidence: float = 0.95,
    method: str = "historical",
) -> float:
    """Compute Value at Risk.

    Args:
        returns: Array of historical daily returns.
        confidence: Confidence level (0.95 or 0.99).
        method: "historical" or "parametric".
    """
    if len(returns) < 5:
        return 0.0

    if method == "parametric":
        from scipy import stats
        mu = np.mean(returns)
        sigma = np.std(returns, ddof=1)
        z = stats.norm.ppf(1 - confidence)
        return float(-(mu + z * sigma))
    else:
        percentile = (1 - confidence) * 100
        return float(-np.percentile(returns, percentile))


def compute_portfolio_var(
    positions: list[Position],
    price_history: dict[str, list[OHLCVBar]],
    portfolio_equity: float,
    confidence: float = 0.95,
) -> float:
    """Compute portfolio-level VaR using historical simulation."""
    if not positions or not price_history:
        return 0.0

    portfolio_returns: np.ndarray | None = None

    for pos in positions:
        bars = price_history.get(pos.ticker, [])
        if len(bars) < 10:
            continue

        closes = np.array([b.close for b in bars[-31:]])
        if len(closes) < 2:
            continue

        returns = np.diff(np.log(closes))
        weight = (pos.quantity * pos.current_price) / portfolio_equity if portfolio_equity > 0 else 0
        weighted = returns * weight * (1 if pos.side.value == "LONG" else -1)

        if portfolio_returns is None:
            portfolio_returns = weighted
        else:
            min_len = min(len(portfolio_returns), len(weighted))
            portfolio_returns = portfolio_returns[-min_len:] + weighted[-min_len:]

    if portfolio_returns is None:
        return 0.0

    return compute_var(portfolio_returns, confidence)


def update_drawdown(state: PortfolioState) -> PortfolioState:
    """Update the peak equity and drawdown tracking."""
    if state.total_equity > state.peak_equity:
        state.peak_equity = state.total_equity

    if state.peak_equity > 0:
        state.drawdown_pct = (
            (state.peak_equity - state.total_equity) / state.peak_equity
        ) * 100.0
    else:
        state.drawdown_pct = 0.0

    state.max_drawdown_pct = max(state.max_drawdown_pct, state.drawdown_pct)
    return state


def compute_sharpe_ratio(
    daily_returns: np.ndarray,
    risk_free_rate: float = 0.05,
) -> float:
    """Compute annualized Sharpe ratio."""
    if len(daily_returns) < 10:
        return 0.0

    daily_rf = risk_free_rate / 252
    excess = daily_returns - daily_rf
    mean_excess = np.mean(excess)
    std = np.std(excess, ddof=1)

    if std == 0:
        return 0.0

    return float(mean_excess / std * np.sqrt(252))


def build_risk_snapshot(
    portfolio: PortfolioState,
    price_history: dict[str, list[OHLCVBar]] | None = None,
) -> RiskSnapshot:
    """Build a complete risk snapshot for the kill switch and dashboard."""
    var_95 = 0.0
    var_99 = 0.0

    if price_history and portfolio.positions:
        var_95 = compute_portfolio_var(
            portfolio.positions, price_history, portfolio.total_equity, 0.95
        )
        var_99 = compute_portfolio_var(
            portfolio.positions, price_history, portfolio.total_equity, 0.99
        )

    return RiskSnapshot(
        timestamp=dt.datetime.now(dt.timezone.utc),
        value_at_risk_95=var_95,
        value_at_risk_99=var_99,
        current_drawdown_pct=portfolio.drawdown_pct,
        max_drawdown_pct=portfolio.max_drawdown_pct,
        portfolio_volatility=var_95 * np.sqrt(252) if var_95 else 0.0,
        kill_switch_armed=True,
    )


# ── Greeks-Aware Risk (F&O) ─────────────────────────────────


@dataclass
class PortfolioGreeks:
    """Aggregate Greeks across all option/futures positions."""
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    net_rho: float = 0.0
    delta_dollars: float = 0.0
    gamma_dollars: float = 0.0


@dataclass
class OptionsPosition:
    """Single options position for Greeks aggregation."""
    underlying: str
    strike: float
    expiry_years: float
    right: str  # "CALL" or "PUT"
    quantity: int
    multiplier: float = 100.0
    underlying_price: float = 0.0
    implied_vol: float = 0.25


def aggregate_portfolio_greeks(
    options_positions: list[OptionsPosition],
    risk_free_rate: float = 0.05,
) -> PortfolioGreeks:
    """Aggregate Greeks across a portfolio of options positions."""
    from execution_engine.math.options_pricing import bs_greeks

    totals = PortfolioGreeks()

    for pos in options_positions:
        g = bs_greeks(
            pos.underlying_price, pos.strike, pos.expiry_years,
            risk_free_rate, pos.implied_vol, pos.right,
        )
        sign = pos.quantity * pos.multiplier
        totals.net_delta += g.delta * sign
        totals.net_gamma += g.gamma * sign
        totals.net_theta += g.theta * sign
        totals.net_vega += g.vega * sign
        totals.net_rho += g.rho * sign

    totals.delta_dollars = totals.net_delta * (
        options_positions[0].underlying_price if options_positions else 0
    )
    totals.gamma_dollars = totals.net_gamma * (
        options_positions[0].underlying_price if options_positions else 0
    ) ** 2 / 100.0

    return totals


def scenario_var(
    options_positions: list[OptionsPosition],
    spot_shocks: np.ndarray | None = None,
    vol_shocks: np.ndarray | None = None,
    risk_free_rate: float = 0.05,
    confidence: float = 0.95,
) -> float:
    """Scenario-based VaR for options portfolios.

    Reprices the portfolio under a grid of spot and vol shocks,
    then takes the percentile loss.
    """
    from execution_engine.math.options_pricing import black_scholes

    if not options_positions:
        return 0.0

    if spot_shocks is None:
        spot_shocks = np.linspace(-0.15, 0.15, 31)
    if vol_shocks is None:
        vol_shocks = np.linspace(-0.10, 0.10, 11)

    base_value = 0.0
    for pos in options_positions:
        px = black_scholes(
            pos.underlying_price, pos.strike, pos.expiry_years,
            risk_free_rate, pos.implied_vol, pos.right,
        )
        base_value += px * pos.quantity * pos.multiplier

    pnl_scenarios: list[float] = []
    for ds in spot_shocks:
        for dv in vol_shocks:
            scenario_value = 0.0
            for pos in options_positions:
                S_new = pos.underlying_price * (1 + ds)
                vol_new = max(pos.implied_vol + dv, 0.01)
                px = black_scholes(
                    S_new, pos.strike, pos.expiry_years,
                    risk_free_rate, vol_new, pos.right,
                )
                scenario_value += px * pos.quantity * pos.multiplier
            pnl_scenarios.append(scenario_value - base_value)

    pnl_arr = np.array(pnl_scenarios)
    return float(-np.percentile(pnl_arr, (1 - confidence) * 100))


def compute_margin_requirement(
    options_positions: list[OptionsPosition],
    margin_type: str = "reg_t",
) -> float:
    """Estimate margin requirement for options positions.

    Uses simplified Reg T rules: max of (premium + 20% underlying - OTM amount)
    for short options, or premium paid for long options.
    """
    total_margin = 0.0
    for pos in options_positions:
        notional = pos.underlying_price * pos.multiplier
        if pos.quantity > 0:
            from execution_engine.math.options_pricing import black_scholes
            premium = black_scholes(
                pos.underlying_price, pos.strike, pos.expiry_years,
                0.05, pos.implied_vol, pos.right,
            )
            total_margin += premium * abs(pos.quantity) * pos.multiplier
        else:
            otm = 0.0
            if pos.right == "CALL":
                otm = max(pos.strike - pos.underlying_price, 0)
            else:
                otm = max(pos.underlying_price - pos.strike, 0)

            from execution_engine.math.options_pricing import black_scholes
            premium = black_scholes(
                pos.underlying_price, pos.strike, pos.expiry_years,
                0.05, pos.implied_vol, pos.right,
            )
            naked_margin = (premium * pos.multiplier) + (0.20 * notional) - (otm * pos.multiplier)
            min_margin = (premium * pos.multiplier) + (0.10 * notional)
            total_margin += max(naked_margin, min_margin) * abs(pos.quantity)

    return total_margin
