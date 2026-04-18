"""Multivariate Volatility Regulated Kelly Criterion position sizing.

Implements the continuous-finance derivation from the architecture doc:
  f* = (1/λ) · Σ⁻¹ · (μ - r_f)

Then applies Half-Kelly constraint for behavioral ruin protection.
"""
from __future__ import annotations

import numpy as np

from shared.config import settings
from shared.logger import get_logger

log = get_logger("math.kelly")

_RISK_FREE_RATE = 0.05


def kelly_fraction_single(
    win_probability: float,
    win_loss_ratio: float,
) -> float:
    """Classic Kelly Criterion for a single binary bet.

    f* = p - (1-p)/b
    where p = win probability, b = win/loss ratio
    """
    if win_loss_ratio <= 0:
        return 0.0
    q = 1.0 - win_probability
    f_star = win_probability - (q / win_loss_ratio)
    half_kelly = f_star * settings.KELLY_FRACTION
    return max(0.0, min(half_kelly, settings.MAX_POSITION_PCT / 100.0))


def multivariate_kelly(
    expected_returns: np.ndarray,
    covariance_matrix: np.ndarray,
    risk_free_rate: float = _RISK_FREE_RATE,
    volatility_penalty: float = 2.0,
) -> np.ndarray:
    """Multivariate Volatility Regulated Kelly allocation.

    f* = (1/λ) · Σ⁻¹ · (μ - r_f)

    Args:
        expected_returns: Vector of annualized expected returns (n,)
        covariance_matrix: Annualized covariance matrix (n, n)
        risk_free_rate: Risk-free rate for excess return calculation
        volatility_penalty: λ — penalizes idiosyncratic risk

    Returns:
        Allocation vector as fraction of portfolio, after Half-Kelly.
    """
    n = len(expected_returns)
    if n == 0:
        return np.array([])

    excess_returns = expected_returns - risk_free_rate

    try:
        cov_inv = np.linalg.inv(covariance_matrix)
    except np.linalg.LinAlgError:
        log.warning("Singular covariance matrix — using pseudo-inverse")
        cov_inv = np.linalg.pinv(covariance_matrix)

    raw_allocation = (1.0 / volatility_penalty) * cov_inv @ excess_returns

    half_kelly = raw_allocation * settings.KELLY_FRACTION

    max_per_position = settings.MAX_POSITION_PCT / 100.0
    clamped = np.clip(half_kelly, -max_per_position, max_per_position)

    total = np.sum(np.abs(clamped))
    if total > 1.0:
        clamped = clamped / total

    log.info(
        f"Kelly allocation: {dict(enumerate(clamped.tolist()))}, "
        f"total exposure: {np.sum(np.abs(clamped)):.2%}"
    )
    return clamped


def compute_position_size(
    portfolio_equity: float,
    allocation_fraction: float,
    current_price: float,
) -> int:
    """Convert a Kelly allocation fraction into a share count."""
    if current_price <= 0 or allocation_fraction == 0:
        return 0
    dollar_amount = portfolio_equity * abs(allocation_fraction)
    shares = int(dollar_amount / current_price)
    return max(shares, 0)


def optimal_allocation_for_signal(
    portfolio_equity: float,
    win_probability: float,
    expected_return: float,
    volatility: float,
    current_price: float,
) -> tuple[float, int]:
    """Convenience: compute Kelly fraction and share count for a single signal.

    Returns (allocation_fraction, share_count).
    """
    if volatility <= 0:
        volatility = 0.2

    win_loss_ratio = max(abs(expected_return / volatility), 0.1)
    frac = kelly_fraction_single(win_probability, win_loss_ratio)
    shares = compute_position_size(portfolio_equity, frac, current_price)

    log.info(
        f"Signal sizing: frac={frac:.4f}, shares={shares}, "
        f"notional=${shares * current_price:,.0f}"
    )
    return frac, shares


# ── F&O-Aware Kelly Sizing ───────────────────────────────────


def kelly_options(
    win_probability: float,
    expected_payoff: float,
    max_loss: float,
    premium_cost: float,
) -> float:
    """Kelly fraction for options trades where payoff is non-linear.

    For defined-risk strategies (spreads, condors), max_loss is known.
    Fraction is sized relative to the risk capital (premium or margin),
    not the notional.
    """
    if max_loss <= 0 or premium_cost <= 0:
        return 0.0

    expected_profit = expected_payoff - premium_cost
    if expected_profit <= 0:
        return 0.0

    rr = expected_profit / max_loss
    q = 1.0 - win_probability
    f_star = win_probability - (q / rr) if rr > 0 else 0.0
    half_kelly = f_star * settings.KELLY_FRACTION
    return max(0.0, min(half_kelly, settings.MAX_POSITION_PCT / 100.0))


def compute_contracts_size(
    portfolio_equity: float,
    allocation_fraction: float,
    premium_per_contract: float,
    multiplier: float = 100.0,
) -> int:
    """Convert a Kelly allocation into a contract count for options/futures.

    Unlike equity sizing (dollars / price = shares), derivatives use
    (dollars / (premium * multiplier)) = contracts.
    """
    if premium_per_contract <= 0 or allocation_fraction <= 0:
        return 0
    dollar_amount = portfolio_equity * allocation_fraction
    cost_per_contract = premium_per_contract * multiplier
    return max(int(dollar_amount / cost_per_contract), 0)


def kelly_futures(
    win_probability: float,
    avg_win_ticks: float,
    avg_loss_ticks: float,
    tick_value: float,
    margin_per_contract: float,
    portfolio_equity: float,
) -> int:
    """Kelly-optimal number of futures contracts.

    Sizes based on margin consumption rather than notional.
    """
    if avg_loss_ticks <= 0 or margin_per_contract <= 0:
        return 0

    rr = avg_win_ticks / avg_loss_ticks
    q = 1.0 - win_probability
    f_star = win_probability - (q / rr) if rr > 0 else 0.0
    half_kelly = f_star * settings.KELLY_FRACTION

    if half_kelly <= 0:
        return 0

    risk_capital = portfolio_equity * min(half_kelly, settings.MAX_POSITION_PCT / 100.0)
    return max(int(risk_capital / margin_per_contract), 0)
