"""Black-Scholes-Merton options pricing engine.

Provides European call/put pricing, analytical Greeks (delta, gamma, theta,
vega, rho), Newton-Raphson implied-volatility solver, and the two multi-leg
helpers used by the backtest and orchestrator (iron condor, straddle).

All math is vectorised with NumPy — no external pricing libraries required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.stats import norm

OptionType = Literal["CALL", "PUT"]


@dataclass(frozen=True, slots=True)
class Greeks:
    """Full first-order risk sensitivities for a single option."""

    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


# ── Core Black-Scholes-Merton ────────────────────────────────


def _d1d2(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> tuple[float, float]:
    """Core BSM d1 / d2 helper.

    P2-26: the type hints previously admitted ``np.ndarray`` but the
    body unconditionally ``float(...)``'s every input, so array
    arguments silently collapsed to a single scalar (taking the
    first element) or raised. Narrowing the hints to ``float`` makes
    the contract match the implementation; a future vectorised
    version would need a separate function that stays array-native.
    """
    sqrt_T = np.sqrt(max(float(T), 1e-12))
    sig_f = float(sigma)
    d1 = (np.log(float(S) / float(K)) + (r + 0.5 * sig_f**2) * float(T)) / (sig_f * sqrt_T)
    d2 = d1 - sig_f * sqrt_T
    return d1, d2


def black_scholes(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "CALL",
) -> float:
    """European option price under BSM assumptions.

    Args:
        S: Underlying spot price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate (annualised, continuous)
        sigma: Volatility (annualised)
        option_type: "CALL" or "PUT"
    """
    if T <= 0:
        return max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)

    d1, d2 = _d1d2(S, K, T, r, sigma)
    if option_type == "CALL":
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return float(np.maximum(price, 0.0))


# ── Greeks ────────────────────────────────────────────────────


def bs_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType = "CALL",
) -> Greeks:
    """Analytical first-order Greeks."""
    if T <= 0:
        intrinsic_long = 1.0 if S > K else 0.0
        d = intrinsic_long if option_type == "CALL" else intrinsic_long - 1.0
        return Greeks(delta=d, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = _d1d2(S, K, T, r, sigma)
    d1, d2 = float(d1), float(d2)
    sqrt_T = np.sqrt(T)
    pdf_d1 = float(norm.pdf(d1))
    disc = np.exp(-r * T)

    gamma = pdf_d1 / (S * sigma * sqrt_T)
    vega = S * pdf_d1 * sqrt_T / 100.0  # per 1% vol move

    if option_type == "CALL":
        delta = float(norm.cdf(d1))
        theta = (-S * pdf_d1 * sigma / (2 * sqrt_T) - r * K * disc * float(norm.cdf(d2))) / 365.0
        rho = K * T * disc * float(norm.cdf(d2)) / 100.0
    else:
        delta = float(norm.cdf(d1)) - 1.0
        theta = (-S * pdf_d1 * sigma / (2 * sqrt_T) + r * K * disc * float(norm.cdf(-d2))) / 365.0
        rho = -K * T * disc * float(norm.cdf(-d2)) / 100.0

    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho)


# ── Implied Volatility ───────────────────────────────────────


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType = "CALL",
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Newton-Raphson IV solver.

    Returns annualised implied volatility. Falls back to bisection
    if Newton diverges.
    """
    if T <= 0 or market_price <= 0:
        return 0.0

    intrinsic = max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)
    if market_price < intrinsic - 1e-8:
        return 0.0

    sigma = 0.25  # initial guess

    for _ in range(max_iter):
        price = black_scholes(S, K, T, r, sigma, option_type)
        diff = price - market_price

        if abs(diff) < tol:
            return sigma

        d1, _ = _d1d2(S, K, T, r, sigma)
        vega_raw = S * float(norm.pdf(float(d1))) * np.sqrt(T)
        if vega_raw < 1e-12:
            break

        sigma -= diff / vega_raw
        sigma = max(sigma, 1e-6)

    return _bisection_iv(market_price, S, K, T, r, option_type, tol)


def _bisection_iv(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType,
    tol: float,
) -> float:
    lo, hi = 0.001, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        price = black_scholes(S, K, T, r, mid, option_type)
        if abs(price - market_price) < tol:
            return mid
        if price > market_price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ── Multi-leg strategy helpers ────────────────────────────────


def iron_condor_price(
    S: float,
    T: float,
    r: float,
    sigma: float,
    put_long_K: float,
    put_short_K: float,
    call_short_K: float,
    call_long_K: float,
) -> dict[str, float]:
    """Price an iron condor and return net credit + max loss."""
    p_long = black_scholes(S, put_long_K, T, r, sigma, "PUT")
    p_short = black_scholes(S, put_short_K, T, r, sigma, "PUT")
    c_short = black_scholes(S, call_short_K, T, r, sigma, "CALL")
    c_long = black_scholes(S, call_long_K, T, r, sigma, "CALL")

    net_credit = (p_short - p_long) + (c_short - c_long)
    put_width = put_short_K - put_long_K
    call_width = call_long_K - call_short_K
    max_loss = max(put_width, call_width) - net_credit

    return {
        "net_credit": net_credit,
        "max_loss": max_loss,
        "max_profit": net_credit,
        "breakeven_low": put_short_K - net_credit,
        "breakeven_high": call_short_K + net_credit,
        "risk_reward": net_credit / max(max_loss, 0.01),
    }


def straddle_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> dict[str, float]:
    """Price a long straddle (buy call + buy put at same strike)."""
    call_p = black_scholes(S, K, T, r, sigma, "CALL")
    put_p = black_scholes(S, K, T, r, sigma, "PUT")
    total = call_p + put_p
    return {
        "total_cost": total,
        "breakeven_low": K - total,
        "breakeven_high": K + total,
        "call_price": call_p,
        "put_price": put_p,
    }
