"""Risk metrics computation — VaR, drawdown tracking, and Sharpe ratio.

These metrics feed the kill switch memory-mapped file at 10Hz.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from shared.constants import DEFAULT_RISK_FREE_RATE, TRADING_DAYS_PER_YEAR
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
    """Compute portfolio-level VaR using historical simulation.

    **P1-X alignment fix.** Prior implementation folded each additional
    position into the running portfolio series with
    ``portfolio_returns[-min_len:] + weighted[-min_len:]`` — that is
    element-wise addition of two tail windows of possibly different
    underlying dates. Two positions whose histories happened to span
    different calendar periods would add unrelated returns together,
    inflating or deflating the composite tail in unpredictable ways.

    New approach: collect every position's weighted return series,
    align them all to the shortest common tail length, and sum along
    the position axis to get a single portfolio-return vector whose
    i-th element is the sum of position contributions at the same
    relative time index.
    """
    if not positions or not price_history:
        return 0.0

    series: list[np.ndarray] = []

    for pos in positions:
        bars = price_history.get(pos.ticker, [])
        if len(bars) < 10:
            continue

        closes = np.array([b.close for b in bars[-31:]])
        if len(closes) < 2:
            continue

        returns = np.diff(np.log(closes))
        weight = (
            (pos.quantity * pos.current_price) / portfolio_equity if portfolio_equity > 0 else 0.0
        )
        side_sign = 1.0 if pos.side.value == "LONG" else -1.0
        series.append(returns * weight * side_sign)

    if not series:
        return 0.0

    # Align by taking the shortest tail across all positions. Every
    # element of the resulting portfolio vector then corresponds to
    # the same number of trading days back for every position.
    min_len = min(len(s) for s in series)
    if min_len == 0:
        return 0.0
    aligned = np.vstack([s[-min_len:] for s in series])
    portfolio_returns = aligned.sum(axis=0)

    return compute_var(portfolio_returns, confidence)


def update_drawdown(state: PortfolioState) -> PortfolioState:
    """Update the peak equity and drawdown tracking."""
    if state.total_equity > state.peak_equity:
        state.peak_equity = state.total_equity

    if state.peak_equity > 0:
        state.drawdown_pct = ((state.peak_equity - state.total_equity) / state.peak_equity) * 100.0
    else:
        state.drawdown_pct = 0.0

    state.max_drawdown_pct = max(state.max_drawdown_pct, state.drawdown_pct)
    return state


def compute_sharpe_ratio(
    daily_returns: np.ndarray,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> float:
    """Compute annualized Sharpe ratio."""
    if len(daily_returns) < 10:
        return 0.0

    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = daily_returns - daily_rf
    mean_excess = np.mean(excess)
    std = np.std(excess, ddof=1)

    if std == 0:
        return 0.0

    return float(mean_excess / std * np.sqrt(TRADING_DAYS_PER_YEAR))


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
        timestamp=dt.datetime.now(dt.UTC),
        value_at_risk_95=var_95,
        value_at_risk_99=var_99,
        current_drawdown_pct=portfolio.drawdown_pct,
        max_drawdown_pct=portfolio.max_drawdown_pct,
        portfolio_volatility=var_95 * np.sqrt(TRADING_DAYS_PER_YEAR) if var_95 else 0.0,
        kill_switch_armed=True,
    )
