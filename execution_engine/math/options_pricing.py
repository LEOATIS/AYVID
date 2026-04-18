"""Black-Scholes-Merton options pricing engine with full Greeks suite.

Provides European call/put pricing, analytical Greeks (delta, gamma, theta,
vega, rho), Newton-Raphson implied-volatility solver, and IV surface
construction from market chain data.

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


@dataclass(frozen=True, slots=True)
class OptionPrice:
    """Complete pricing result."""
    price: float
    greeks: Greeks
    intrinsic: float
    time_value: float


@dataclass
class IVPoint:
    """Single point on the implied-volatility surface."""
    strike: float
    expiry_years: float
    iv: float
    option_type: OptionType


@dataclass
class IVSurface:
    """Discretised implied-volatility surface."""
    strikes: np.ndarray
    expiries: np.ndarray
    iv_grid: np.ndarray          # shape (len(expiries), len(strikes))
    underlying_price: float
    risk_free_rate: float
    timestamp: str | None = None

    def iv_at(self, strike: float, expiry: float) -> float:
        """Bilinear interpolation on the surface."""
        from scipy.interpolate import RegularGridInterpolator
        interp = RegularGridInterpolator(
            (self.expiries, self.strikes), self.iv_grid,
            bounds_error=False, fill_value=None,
        )
        return float(interp([[expiry, strike]])[0])

    @property
    def atm_term_structure(self) -> np.ndarray:
        """ATM IV for each expiry (nearest strike to spot)."""
        atm_idx = int(np.argmin(np.abs(self.strikes - self.underlying_price)))
        return self.iv_grid[:, atm_idx]

    @property
    def skew(self) -> np.ndarray:
        """25-delta put minus 25-delta call IV per expiry (simplified as
        lowest-strike IV minus highest-strike IV)."""
        return self.iv_grid[:, 0] - self.iv_grid[:, -1]


# ── Core Black-Scholes-Merton ────────────────────────────────


def _d1d2(
    S: float | np.ndarray,
    K: float | np.ndarray,
    T: float | np.ndarray,
    r: float,
    sigma: float | np.ndarray,
) -> tuple[float, float]:
    S_f = float(S)
    K_f = float(K)
    T_f = float(T)
    sig_f = float(sigma)
    sqrt_T = np.sqrt(max(T_f, 1e-12))
    d1 = (np.log(S_f / K_f) + (r + 0.5 * sig_f**2) * T_f) / (sig_f * sqrt_T)
    d2 = d1 - sig_f * sqrt_T
    return d1, d2


def black_scholes(
    S: float, K: float, T: float, r: float, sigma: float,
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


def _d1d2_vec(
    S: np.ndarray, K: np.ndarray, T: np.ndarray,
    r: float, sigma: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised d1/d2 for arrays."""
    sqrt_T = np.sqrt(np.maximum(T, 1e-12))
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def black_scholes_vec(
    S: np.ndarray, K: np.ndarray, T: np.ndarray,
    r: float, sigma: np.ndarray, option_type: OptionType = "CALL",
) -> np.ndarray:
    """Vectorised BS for arrays of strikes / expiries / vols."""
    d1, d2 = _d1d2_vec(S, K, T, r, sigma)
    if option_type == "CALL":
        return np.maximum(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2), 0)
    return np.maximum(K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1), 0)


# ── Greeks ────────────────────────────────────────────────────


def bs_greeks(
    S: float, K: float, T: float, r: float, sigma: float,
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
        theta = (
            -S * pdf_d1 * sigma / (2 * sqrt_T)
            - r * K * disc * float(norm.cdf(d2))
        ) / 365.0
        rho = K * T * disc * float(norm.cdf(d2)) / 100.0
    else:
        delta = float(norm.cdf(d1)) - 1.0
        theta = (
            -S * pdf_d1 * sigma / (2 * sqrt_T)
            + r * K * disc * float(norm.cdf(-d2))
        ) / 365.0
        rho = -K * T * disc * float(norm.cdf(-d2)) / 100.0

    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, rho=rho)


def price_option(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: OptionType = "CALL",
) -> OptionPrice:
    """Full pricing result with price, Greeks, intrinsic and time value."""
    price = black_scholes(S, K, T, r, sigma, option_type)
    greeks = bs_greeks(S, K, T, r, sigma, option_type)
    intrinsic = max(S - K, 0.0) if option_type == "CALL" else max(K - S, 0.0)
    return OptionPrice(
        price=price, greeks=greeks,
        intrinsic=intrinsic, time_value=price - intrinsic,
    )


# ── Implied Volatility ───────────────────────────────────────


def implied_volatility(
    market_price: float,
    S: float, K: float, T: float, r: float,
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
    S: float, K: float, T: float, r: float,
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


# ── Put-Call Parity ───────────────────────────────────────────


def put_call_parity_check(
    call_price: float, put_price: float,
    S: float, K: float, T: float, r: float,
    tolerance: float = 0.02,
) -> tuple[bool, float]:
    """Verify put-call parity: C - P = S - K*exp(-rT).

    Returns (is_valid, mispricing_amount).
    """
    theoretical_diff = S - K * np.exp(-r * T)
    actual_diff = call_price - put_price
    mispricing = actual_diff - theoretical_diff
    return abs(mispricing) <= tolerance * S, float(mispricing)


# ── IV Surface Construction ───────────────────────────────────


def build_iv_surface(
    chain_data: list[dict],
    underlying_price: float,
    risk_free_rate: float = 0.05,
) -> IVSurface:
    """Build an IV surface from options chain snapshots.

    Each dict in chain_data should contain:
        strike, expiry_years, market_price, option_type ("CALL"/"PUT")
    """
    points: list[IVPoint] = []
    for row in chain_data:
        strike = row["strike"]
        T = row["expiry_years"]
        mkt_price = row["market_price"]
        opt_type: OptionType = row.get("option_type", "CALL")

        iv = implied_volatility(mkt_price, underlying_price, strike, T, risk_free_rate, opt_type)
        if 0.01 < iv < 3.0:
            points.append(IVPoint(strike=strike, expiry_years=T, iv=iv, option_type=opt_type))

    if not points:
        return IVSurface(
            strikes=np.array([underlying_price]),
            expiries=np.array([0.083]),
            iv_grid=np.array([[0.25]]),
            underlying_price=underlying_price,
            risk_free_rate=risk_free_rate,
        )

    strikes = np.unique(sorted(p.strike for p in points))
    expiries = np.unique(sorted(p.expiry_years for p in points))

    iv_grid = np.full((len(expiries), len(strikes)), np.nan)
    for p in points:
        ei = int(np.searchsorted(expiries, p.expiry_years))
        si = int(np.searchsorted(strikes, p.strike))
        if ei < len(expiries) and si < len(strikes):
            existing = iv_grid[ei, si]
            iv_grid[ei, si] = p.iv if np.isnan(existing) else (existing + p.iv) / 2

    for i in range(iv_grid.shape[0]):
        row = iv_grid[i]
        valid = ~np.isnan(row)
        if valid.sum() >= 2:
            iv_grid[i] = np.interp(np.arange(len(row)), np.where(valid)[0], row[valid])
        elif valid.sum() == 1:
            iv_grid[i] = row[valid][0]
        else:
            iv_grid[i] = 0.25

    return IVSurface(
        strikes=strikes, expiries=expiries, iv_grid=iv_grid,
        underlying_price=underlying_price, risk_free_rate=risk_free_rate,
    )


# ── Multi-leg strategy helpers ────────────────────────────────


def iron_condor_price(
    S: float, T: float, r: float, sigma: float,
    put_long_K: float, put_short_K: float,
    call_short_K: float, call_long_K: float,
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
    S: float, K: float, T: float, r: float, sigma: float,
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
