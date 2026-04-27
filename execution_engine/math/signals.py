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

NOTE: All low-level math primitives (Hurst, RSI, ATR, OU, volume profile,
trend strength, Bayesian update) live in ``execution_engine.math._primitives``.
This module re-exports them so the existing public API (``compute_rsi``,
``compute_atr``, ``compute_hurst_exponent``, …) continues to work, and then
layers the higher-level ``score_signal`` composite on top.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from execution_engine.math._primitives import (
    bayesian_conviction_update,
    compute_atr,
    compute_rsi,
    compute_trend_strength,
    compute_volume_profile,
    estimate_ou_parameters,
)
from execution_engine.math._primitives import (
    compute_hurst as _compute_hurst_primitive,
)
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
    """Public alias retained for backward compatibility.

    Delegates to the canonical `_primitives.compute_hurst`.
    """
    return _compute_hurst_primitive(series, max_lag=max_lag)


# `estimate_ou_parameters`, `compute_rsi`, `compute_atr`,
# `compute_volume_profile`, `compute_trend_strength`, and
# `bayesian_conviction_update` are imported verbatim above. They are the
# public API of this module and are now single-sourced from `_primitives`.


__all__ = [
    "SignalScore",
    "score_signal",
    "compute_hurst_exponent",
    "estimate_ou_parameters",
    "compute_rsi",
    "compute_atr",
    "compute_volume_profile",
    "compute_trend_strength",
    "bayesian_conviction_update",
]


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

    returns = np.diff(np.log(closes[-lookback - 1 :]))

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

    composite = (
        z_component
        + hurst_component
        + rsi_component
        + vol_component
        + ou_component
        + trend_component
    )

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
