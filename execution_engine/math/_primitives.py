"""Canonical mathematical primitives — single source of truth.

Every other module in the codebase that needs Hurst, RSI, ATR, log-returns,
annualised volatility, rolling z-score, Bayesian conviction aggregation,
or trend-strength MUST import from this module. Duplicate implementations
were the single largest source of numeric drift audited in Phase-2.

Design rules:

1. **Bit-exact** with the pre-Phase-2 canonical (`execution_engine.math.signals`
   and `execution_engine.math.regime`). Pin tests in `tests/test_primitives.py`
   enforce this with seeded NumPy inputs.
2. Pure-functional, NumPy-only. No I/O, no logging, no config imports.
3. All time constants come from `shared.constants`, all risk-free defaults
   from `shared.constants.DEFAULT_RISK_FREE_RATE`.
4. **Legacy variants** that produce numerically different values
   (`data_primitives.loaders.hopsworks_sync.compute_hurst_exponent`,
    `backtesting.engine._compute_hurst_fast`) are explicitly not consolidated
   here — unifying them would silently shift the YC-published screening and
   walk-forward numbers. They are flagged with `# LEGACY_VARIANT` comments
   at their definitions and are a Phase-3 item.
"""

from __future__ import annotations

import numpy as np

from shared.constants import TRADING_DAYS_PER_YEAR

# ── Return-space primitives ───────────────────────────────────


def log_returns(closes: np.ndarray) -> np.ndarray:
    """First-difference of log prices. The fundamental input to every
    volatility, Hurst, and return-based metric in the codebase.
    """
    return np.diff(np.log(closes))


def annualize_vol(returns_std: float) -> float:
    """Annualise a daily standard-deviation of log returns using the
    standard 252-trading-day convention from `shared.constants`.
    """
    return float(returns_std * np.sqrt(TRADING_DAYS_PER_YEAR))


def compute_realized_vol(returns: np.ndarray, window: int = 20) -> float:
    """Annualised realised volatility over the last `window` observations.

    Bit-exact with the previous canonical `regime.compute_realized_volatility`.
    """
    if len(returns) < window:
        return float(np.std(returns) * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(returns) > 1 else 0.2
    recent = returns[-window:]
    return float(np.std(recent, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def rolling_zscore(series: np.ndarray, lookback: int = 30) -> float:
    """Z-score of the latest point against its trailing-`lookback` window.

    Bit-exact with the inline z-score used in `signals.score_signal`:
        z = (last − mean) / std(ddof=1)
    Returns 0.0 when std is numerically zero.
    """
    if len(series) < lookback:
        return 0.0
    window = series[-lookback:]
    rolling_mean = float(np.mean(window))
    rolling_std = float(np.std(window, ddof=1))
    if rolling_std < 1e-10:
        return 0.0
    return float((window[-1] - rolling_mean) / rolling_std)


# ── Hurst exponent ────────────────────────────────────────────


def compute_hurst(series: np.ndarray, max_lag: int = 20) -> float:
    """Rescaled-range (R/S) Hurst exponent. Canonical signals.py variant.

    Bit-exact with the previous canonical `signals.compute_hurst_exponent`.

    H < 0.5 → mean-reverting (tradeable)
    H ≈ 0.5 → random walk (avoid)
    H > 0.5 → trending (momentum tradeable)

    NOTE: two other Hurst implementations exist in the repo and produce
    numerically different values by design (different lag grids, different
    chunking). They are NOT unified here to preserve the published
    screening universe — see `_primitives` module docstring.
    """
    n = len(series)
    if n < max_lag * 2:
        return 0.5

    lags = range(2, min(max_lag, n // 2))
    rs_values: list[float] = []
    lag_values: list[int] = []

    for lag in lags:
        rs_per_segment: list[float] = []
        num_segments = n // lag
        for i in range(num_segments):
            segment = series[i * lag : (i + 1) * lag]
            mean = np.mean(segment)
            deviate = np.cumsum(segment - mean)
            r = np.max(deviate) - np.min(deviate)
            s = np.std(segment, ddof=1) if np.std(segment, ddof=1) > 0 else 1e-10
            rs_per_segment.append(r / s)
        if rs_per_segment:
            rs_values.append(float(np.mean(rs_per_segment)))
            lag_values.append(lag)

    if len(rs_values) < 3:
        return 0.5

    log_lags = np.log(lag_values)
    log_rs = np.log(np.array(rs_values) + 1e-10)

    try:
        coeffs = np.polyfit(log_lags, log_rs, 1)
        return float(np.clip(coeffs[0], 0.0, 1.0))
    except (np.linalg.LinAlgError, ValueError):
        return 0.5


# ── Ornstein–Uhlenbeck ────────────────────────────────────────


def estimate_ou_parameters(
    prices: np.ndarray,
    dt_step: float = 1.0 / float(TRADING_DAYS_PER_YEAR),
) -> tuple[float, float, float]:
    """Estimate Ornstein-Uhlenbeck process parameters via MLE.

    dX_t = θ(μ - X_t)dt + σ dW_t

    Returns (theta, mu, sigma). Bit-exact with the previous canonical
    `signals.estimate_ou_parameters`.
    """
    log_prices = np.log(prices)
    n = len(log_prices)
    if n < 10:
        return 0.0, float(np.mean(log_prices)), 0.2

    x = log_prices[:-1]
    y = log_prices[1:]

    try:
        sx = np.sum(x)
        sy = np.sum(y)
        sxx = np.sum(x * x)
        sxy = np.sum(x * y)
        n_pts = len(x)

        denom = n_pts * sxx - sx * sx
        if abs(denom) < 1e-12:
            return 0.0, float(np.mean(log_prices)), 0.2

        a = (n_pts * sxy - sx * sy) / denom
        b = (sy - a * sx) / n_pts

        if a >= 1.0 or a <= 0.0:
            return 0.0, float(np.mean(log_prices)), 0.2

        theta = -np.log(a) / dt_step
        mu = b / (1.0 - a)
        residuals = y - (a * x + b)
        sigma_sq = np.var(residuals) * 2.0 * theta / (1.0 - a * a)
        sigma = float(np.sqrt(max(sigma_sq, 1e-10)))

        return float(max(theta, 0.0)), float(mu), sigma
    except (ValueError, RuntimeWarning):
        return 0.0, float(np.mean(log_prices)), 0.2


# ── Momentum / volatility oscillators ─────────────────────────


def compute_rsi(prices: np.ndarray, period: int = 14) -> float:
    """Relative Strength Index using Cutler's arithmetic moving average.

    Bit-exact with the previous canonical `signals.compute_rsi`.
    """
    if len(prices) < period + 1:
        return 50.0

    deltas = np.diff(prices[-(period + 1) :])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)

    if avg_loss < 1e-10:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> float:
    """Average True Range — volatility independent of direction.

    Bit-exact with the previous canonical `signals.compute_atr`.
    """
    if len(closes) < period + 1:
        return float(np.mean(highs - lows)) if len(highs) > 0 else 0.0

    tr_values: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_values.append(tr)

    return float(np.mean(tr_values[-period:]))


def compute_volume_profile(volumes: np.ndarray, lookback: int = 20) -> float:
    """Volume ratio: current volume vs. rolling average.

    Bit-exact with the previous canonical `signals.compute_volume_profile`.
    """
    if len(volumes) < lookback + 1:
        return 1.0
    recent = volumes[-1]
    avg = np.mean(volumes[-(lookback + 1) : -1])
    if avg < 1e-10:
        return 1.0
    return float(recent / avg)


def compute_trend_strength(
    prices: np.ndarray,
    short: int = 10,
    long: int = 30,
) -> float:
    """Directional trend strength, strictly bounded to [-1.0, +1.0].

    Bit-exact with the previous canonical `signals.compute_trend_strength`.
    """
    if len(prices) < long + 1:
        return 0.0

    short_ma = float(np.mean(prices[-short:]))
    long_ma = float(np.mean(prices[-long:]))
    std = float(np.std(prices[-long:], ddof=1))
    if std < 1e-10 or not np.isfinite(std):
        return 0.0

    trend = (short_ma - long_ma) / std
    if not np.isfinite(trend):
        return 0.0
    return float(np.clip(trend, -1.0, 1.0))


# ── Bayesian conviction ───────────────────────────────────────


def bayesian_conviction_update(
    prior: float,
    factor_scores: list[tuple[float, float]],
) -> float:
    """Bayesian conviction aggregation across independent signal factors.

    posterior_odds = prior_odds × ∏(likelihood_ratio_i ^ weight_i)

    Bit-exact with the previous canonical `signals.bayesian_conviction_update`.
    """
    if prior <= 0.0 or prior >= 1.0:
        prior = float(np.clip(prior, 0.01, 0.99))

    odds = prior / (1.0 - prior)

    for lr, weight in factor_scores:
        if lr <= 0:
            continue
        odds *= lr**weight

    posterior = odds / (1.0 + odds)
    return float(np.clip(posterior, 0.0, 1.0))
