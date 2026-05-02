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

from shared.logger import get_logger

log = get_logger("options_pricing")

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
    import math

    S_f, K_f, T_f, sig_f = float(S), float(K), max(float(T), 1e-12), float(sigma)
    # Guard: non-finite or non-positive inputs → return neutral (0, 0)
    if not (math.isfinite(S_f) and math.isfinite(K_f) and math.isfinite(sig_f)):
        return 0.0, 0.0
    if S_f <= 0 or K_f <= 0:
        return 0.0, 0.0
    # Volatility clamp: prevent degenerate 0-vol or extreme vol
    sig_f = max(sig_f, 1e-6)
    sig_f = min(sig_f, 10.0)  # 1000% vol cap

    sqrt_T = np.sqrt(T_f)
    d1 = (np.log(S_f / K_f) + (r + 0.5 * sig_f**2) * T_f) / (sig_f * sqrt_T)
    d2 = d1 - sig_f * sqrt_T

    # Final NaN guard
    if not (math.isfinite(d1) and math.isfinite(d2)):
        return 0.0, 0.0
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
    import math

    S_f, K_f = float(S), float(K)
    if not (math.isfinite(S_f) and math.isfinite(K_f)):
        return 0.0

    if T <= 0:
        return max(S_f - K_f, 0.0) if option_type == "CALL" else max(K_f - S_f, 0.0)

    sigma_f = max(float(sigma), 1e-6)
    d1, d2 = _d1d2(S_f, K_f, T, r, sigma_f)
    if option_type == "CALL":
        price = S_f * norm.cdf(d1) - K_f * np.exp(-r * T) * norm.cdf(d2)
    else:
        price = K_f * np.exp(-r * T) * norm.cdf(-d2) - S_f * norm.cdf(-d1)
    result = float(np.maximum(price, 0.0))
    return result if math.isfinite(result) else 0.0


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
    *,
    strict: bool = False,
) -> float:
    """Newton-Raphson IV solver.

    Returns annualised implied volatility. Falls back to bisection
    if Newton diverges.

    Degenerate inputs (T <= 0, market_price <= 0, or arbitrage-violating
    price below intrinsic) return ``0.0`` by default to preserve the
    legacy contract. Callers that need to distinguish "IV genuinely
    solved to 0" from "solver could not run" should pass ``strict=True``
    to receive ``math.nan`` for the latter — Greeks computed off NaN
    propagate the failure instead of silently underestimating risk.
    """
    import math

    bad_input = T <= 0 or market_price <= 0
    if bad_input:
        if strict:
            log.warning(
                f"implied_volatility: degenerate input "
                f"(T={T}, market_price={market_price}); returning NaN"
            )
            return float("nan")
        return 0.0

    intrinsic = max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)
    if market_price < intrinsic - 1e-8:
        if strict:
            log.warning(
                f"implied_volatility: market_price {market_price:.4f} below "
                f"intrinsic {intrinsic:.4f} — arbitrage violation; returning NaN"
            )
            return float("nan")
        return 0.0
    _ = math  # keep linter quiet when strict path is unused

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


def select_condor_strikes(
    spot: float,
    sigma: float,
    T: float,
    short_delta: float = 0.20,
    wing_delta: float = 0.10,
) -> tuple[float, float, float, float]:
    """Select iron condor strikes from delta targets.

    Returns (short_call_K, long_call_K, short_put_K, long_put_K).
    Uses the BSM inverse-delta approximation: K ≈ S * exp(z * sigma * sqrt(T))
    where z is the standard normal quantile for the target delta.
    """
    from scipy.stats import norm as _norm

    sqrt_T = np.sqrt(max(T, 1e-12))
    sig = max(sigma, 1e-6)

    # Call side: short at delta=0.20 (OTM), long at delta=0.10 (further OTM)
    z_short_call = _norm.ppf(1.0 - short_delta)  # ~0.84
    z_long_call = _norm.ppf(1.0 - wing_delta)  # ~1.28
    short_call_K = round(spot * np.exp(z_short_call * sig * sqrt_T), 2)
    long_call_K = round(spot * np.exp(z_long_call * sig * sqrt_T), 2)

    # Put side: short at delta=-0.20 (OTM), long at delta=-0.10 (further OTM)
    z_short_put = _norm.ppf(short_delta)  # ~-0.84
    z_long_put = _norm.ppf(wing_delta)  # ~-1.28
    short_put_K = round(spot * np.exp(z_short_put * sig * sqrt_T), 2)
    long_put_K = round(spot * np.exp(z_long_put * sig * sqrt_T), 2)

    return short_call_K, long_call_K, short_put_K, long_put_K


def select_credit_spread_strikes(
    spot: float,
    sigma: float,
    T: float,
    direction: str = "BULLISH",
    short_delta: float = 0.30,
    wing_delta: float = 0.15,
) -> tuple[float, float]:
    """Select credit spread strikes (short_strike, long_strike).

    BULLISH → sell put spread (sell higher put, buy lower put).
    BEARISH → sell call spread (sell lower call, buy higher call).
    """
    from scipy.stats import norm as _norm

    sqrt_T = np.sqrt(max(T, 1e-12))
    sig = max(sigma, 1e-6)

    if direction == "BULLISH":
        z_short = _norm.ppf(short_delta)
        z_long = _norm.ppf(wing_delta)
        short_strike = round(spot * np.exp(z_short * sig * sqrt_T), 2)
        long_strike = round(spot * np.exp(z_long * sig * sqrt_T), 2)
    else:
        z_short = _norm.ppf(1.0 - short_delta)
        z_long = _norm.ppf(1.0 - wing_delta)
        short_strike = round(spot * np.exp(z_short * sig * sqrt_T), 2)
        long_strike = round(spot * np.exp(z_long * sig * sqrt_T), 2)

    return short_strike, long_strike


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
