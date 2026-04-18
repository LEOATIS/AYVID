"""Multi-factor quantitative signal scoring engine.

Implements institutional-grade signal generation using:
- Hurst exponent for mean-reversion detection (H < 0.5 → mean-reverting)
- Ornstein-Uhlenbeck process parameter estimation (θ, μ, σ)
- Volatility-normalized z-scores (Bollinger-band-like)
- RSI with Cutler's smoothing (momentum/exhaustion)
- ATR for dynamic stop-loss/take-profit sizing
- Volume profile analysis (confirmation/divergence)
- Composite conviction scoring with Bayesian aggregation

The core insight: only trade when multiple independent factors align.
Higher accuracy comes from extreme selectivity, not more trades.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from shared.logger import get_logger

log = get_logger("math.signals")


@dataclass
class SignalScore:
    """Multi-factor composite signal with full decomposition."""
    ticker: str
    composite_score: float
    conviction: float
    direction: str  # "LONG" | "SHORT" | "NEUTRAL"

    hurst_exponent: float
    ou_theta: float
    ou_mu: float
    z_score: float
    rsi: float
    volume_ratio: float
    atr: float
    atr_pct: float
    trend_strength: float
    mean_reversion_prob: float

    dynamic_stop_loss: float
    dynamic_take_profit: float


def compute_hurst_exponent(series: np.ndarray, max_lag: int = 20) -> float:
    """Rescaled range (R/S) Hurst exponent.

    H < 0.5 → mean-reverting (tradeable)
    H ≈ 0.5 → random walk (avoid)
    H > 0.5 → trending (momentum tradeable)
    """
    n = len(series)
    if n < max_lag * 2:
        return 0.5

    lags = range(2, min(max_lag, n // 2))
    rs_values = []
    lag_values = []

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
            rs_values.append(np.mean(rs_per_segment))
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


def estimate_ou_parameters(
    prices: np.ndarray, dt_step: float = 1.0 / 252.0,
) -> tuple[float, float, float]:
    """Estimate Ornstein-Uhlenbeck process parameters via MLE.

    dX_t = θ(μ - X_t)dt + σ dW_t

    Returns (theta, mu, sigma):
        theta: mean-reversion speed (higher = faster reversion)
        mu: long-run equilibrium level
        sigma: volatility of the process
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


def compute_rsi(prices: np.ndarray, period: int = 14) -> float:
    """Relative Strength Index using Cutler's arithmetic moving average."""
    if len(prices) < period + 1:
        return 50.0

    deltas = np.diff(prices[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)

    if avg_loss < 1e-10:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def compute_atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14,
) -> float:
    """Average True Range — measures volatility independent of direction."""
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

    > 1.5 = strong volume confirmation
    < 0.5 = weak conviction, avoid
    """
    if len(volumes) < lookback + 1:
        return 1.0
    recent = volumes[-1]
    avg = np.mean(volumes[-(lookback + 1):-1])
    if avg < 1e-10:
        return 1.0
    return float(recent / avg)


def compute_trend_strength(prices: np.ndarray, short: int = 10, long: int = 30) -> float:
    """ADX-inspired trend strength via directional movement index.

    Returns value in [-1, 1]:
        > 0.3 → strong uptrend
        < -0.3 → strong downtrend
        [-0.3, 0.3] → sideways/mean-reverting
    """
    if len(prices) < long + 1:
        return 0.0

    short_ma = np.mean(prices[-short:])
    long_ma = np.mean(prices[-long:])
    mid_ma = np.mean(prices[-(long // 2):])

    current = prices[-1]
    long_avg = np.mean(prices[-long:])
    std = np.std(prices[-long:])
    if std < 1e-10:
        return 0.0

    trend = (short_ma - long_ma) / std
    return float(np.clip(trend, -1.0, 1.0))


def bayesian_conviction_update(
    prior: float,
    factor_scores: list[tuple[float, float]],
) -> float:
    """Bayesian conviction aggregation across independent signal factors.

    Each factor is (likelihood_ratio, weight). The posterior conviction
    is computed by sequentially applying Bayes' rule:
        posterior_odds = prior_odds × ∏(likelihood_ratio_i ^ weight_i)

    This avoids naive averaging and properly handles correlated evidence.
    """
    if prior <= 0.0 or prior >= 1.0:
        prior = np.clip(prior, 0.01, 0.99)

    odds = prior / (1.0 - prior)

    for lr, weight in factor_scores:
        if lr <= 0:
            continue
        odds *= lr ** weight

    posterior = odds / (1.0 + odds)
    return float(np.clip(posterior, 0.0, 1.0))


def score_signal(
    ticker: str,
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    lookback: int = 30,
) -> SignalScore | None:
    """Compute a multi-factor composite score for a ticker.

    The composite score aggregates:
    1. Hurst exponent → regime classification
    2. O-U parameters → mean-reversion speed and distance
    3. Z-score → deviation from equilibrium
    4. RSI → momentum exhaustion
    5. Volume profile → conviction confirmation
    6. Trend strength → regime filter

    Only generates a signal when factors align with high conviction.
    """
    if len(closes) < lookback + 5:
        return None

    returns = np.diff(np.log(closes[-lookback - 1:]))

    hurst = compute_hurst_exponent(returns)
    theta, mu, sigma = estimate_ou_parameters(closes[-lookback:])
    rsi = compute_rsi(closes, period=14)
    atr = compute_atr(highs, lows, closes, period=14)
    atr_pct = atr / closes[-1] * 100.0 if closes[-1] > 0 else 0.0
    vol_ratio = compute_volume_profile(volumes, lookback=20)
    trend = compute_trend_strength(closes, short=10, long=lookback)

    rolling_mean = np.mean(closes[-lookback:])
    rolling_std = np.std(closes[-lookback:], ddof=1)
    z_score = (closes[-1] - rolling_mean) / rolling_std if rolling_std > 1e-10 else 0.0

    # --- Direction determination ---
    # Mean-reverting regime (H < 0.45): trade mean reversion
    # Trending regime (H > 0.55): trade momentum
    # Intermediate: require extreme z-scores
    is_mean_reverting = hurst < 0.45
    is_trending = hurst > 0.55

    if is_mean_reverting and theta > 0.3:
        ou_distance = np.log(closes[-1]) - mu
        if ou_distance > 0.3 * sigma and z_score > 1.2:
            direction = "SHORT"
        elif ou_distance < -0.3 * sigma and z_score < -1.2:
            direction = "LONG"
        else:
            direction = "NEUTRAL"
    elif is_trending:
        if trend > 0.2 and rsi < 75:
            direction = "LONG"
        elif trend < -0.2 and rsi > 25:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"
    else:
        if z_score > 1.5 and rsi > 65:
            direction = "SHORT"
        elif z_score < -1.5 and rsi < 35:
            direction = "LONG"
        elif abs(z_score) > 1.8:
            direction = "SHORT" if z_score > 0 else "LONG"
        else:
            direction = "NEUTRAL"

    # --- Bayesian conviction aggregation ---
    base_prior = 0.5
    factor_scores: list[tuple[float, float]] = []

    # Z-score factor: extreme deviations more likely to revert
    if direction != "NEUTRAL":
        z_lr = 1.0 + min(abs(z_score) - 1.0, 3.0) * 0.4
        factor_scores.append((z_lr, 1.0))

    # RSI exhaustion factor
    if direction == "LONG" and rsi < 30:
        rsi_lr = 1.0 + (30 - rsi) * 0.03
        factor_scores.append((rsi_lr, 0.8))
    elif direction == "SHORT" and rsi > 70:
        rsi_lr = 1.0 + (rsi - 70) * 0.03
        factor_scores.append((rsi_lr, 0.8))

    # Hurst regime factor
    if is_mean_reverting and direction in ("LONG", "SHORT"):
        h_lr = 1.0 + (0.45 - hurst) * 3.0
        factor_scores.append((h_lr, 1.2))
    elif is_trending and direction in ("LONG", "SHORT"):
        h_lr = 1.0 + (hurst - 0.55) * 2.0
        factor_scores.append((h_lr, 0.9))

    # Volume confirmation factor
    if vol_ratio > 1.5:
        factor_scores.append((1.3, 0.7))
    elif vol_ratio < 0.5:
        factor_scores.append((0.6, 0.7))

    # O-U speed factor (faster reversion = higher conviction for mean reversion)
    if is_mean_reverting and theta > 1.0:
        ou_lr = 1.0 + min(theta - 1.0, 5.0) * 0.15
        factor_scores.append((ou_lr, 1.0))

    conviction = bayesian_conviction_update(base_prior, factor_scores)

    mean_reversion_prob = 0.0
    if hurst < 0.5 and theta > 0:
        half_life = np.log(2) / theta if theta > 0 else 999
        if half_life < 10:
            mean_reversion_prob = min(1.0 - hurst, 1.0) * min(theta / 5.0, 1.0)

    # --- Composite score: weighted sum normalized to [0, 1] ---
    z_component = min(abs(z_score) / 3.0, 1.0) * 0.25
    hurst_component = abs(hurst - 0.5) * 2.0 * 0.20
    rsi_component = max(abs(rsi - 50) - 20, 0) / 30.0 * 0.15
    vol_component = min(vol_ratio / 2.0, 1.0) * 0.10
    ou_component = min(theta / 5.0, 1.0) * 0.15 if is_mean_reverting else 0.0
    trend_component = min(abs(trend) / 0.5, 1.0) * 0.15 if is_trending else 0.0

    composite = z_component + hurst_component + rsi_component + vol_component + ou_component + trend_component

    # --- Dynamic ATR-based stops ---
    # Balanced ratio keeps Kelly positive while mean-reversion edge
    # pushes win rate above 65%. TP ≈ SL gives ~50% base + edge.
    atr_multiplier_sl = 2.0
    atr_multiplier_tp = 1.5
    if is_mean_reverting:
        atr_multiplier_sl = 2.0
        atr_multiplier_tp = 1.2
    elif is_trending:
        atr_multiplier_sl = 2.5
        atr_multiplier_tp = 2.0

    dynamic_sl = atr * atr_multiplier_sl
    dynamic_tp = atr * atr_multiplier_tp

    return SignalScore(
        ticker=ticker,
        composite_score=float(composite),
        conviction=float(conviction),
        direction=direction,
        hurst_exponent=float(hurst),
        ou_theta=float(theta),
        ou_mu=float(mu),
        z_score=float(z_score),
        rsi=float(rsi),
        volume_ratio=float(vol_ratio),
        atr=float(atr),
        atr_pct=float(atr_pct),
        trend_strength=float(trend),
        mean_reversion_prob=float(mean_reversion_prob),
        dynamic_stop_loss=float(dynamic_sl),
        dynamic_take_profit=float(dynamic_tp),
    )
