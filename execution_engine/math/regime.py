"""Market regime detection via Hidden Markov Model-inspired state classification.

Classifies markets into discrete regimes using observable features:
- Realized volatility quantiles → LOW_VOL / NORMAL / HIGH_VOL / CRISIS
- Trend persistence (ADX-like) → TRENDING / MEAN_REVERTING / CHOPPY
- Cross-asset correlation breakdown → CORRELATED / DECORRELATED
- Composite regime → determines which strategy class to deploy

The regime filter is the single most important component for accuracy:
a mean-reversion strategy in a trending regime loses money, and vice versa.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from shared.logger import get_logger

log = get_logger("math.regime")


class VolatilityRegime(str, Enum):
    LOW_VOL = "LOW_VOL"
    NORMAL = "NORMAL"
    HIGH_VOL = "HIGH_VOL"
    CRISIS = "CRISIS"


class TrendRegime(str, Enum):
    STRONG_TREND = "STRONG_TREND"
    WEAK_TREND = "WEAK_TREND"
    MEAN_REVERTING = "MEAN_REVERTING"
    CHOPPY = "CHOPPY"


class CompositeRegime(str, Enum):
    TRENDING_LOW_VOL = "TRENDING_LOW_VOL"
    TRENDING_HIGH_VOL = "TRENDING_HIGH_VOL"
    MEAN_REVERTING = "MEAN_REVERTING"
    CRISIS = "CRISIS"
    CHOPPY = "CHOPPY"


@dataclass
class RegimeState:
    """Complete regime classification for a market or instrument."""
    volatility_regime: VolatilityRegime
    trend_regime: TrendRegime
    composite_regime: CompositeRegime

    realized_vol_annualized: float
    vol_percentile: float
    adx_value: float
    correlation_to_spy: float

    tradeable: bool
    preferred_strategy: str
    regime_confidence: float
    risk_scaling_factor: float


def compute_realized_volatility(returns: np.ndarray, window: int = 20) -> float:
    """Annualized realized volatility (Parkinson-like estimator)."""
    if len(returns) < window:
        return float(np.std(returns) * np.sqrt(252)) if len(returns) > 1 else 0.2
    recent = returns[-window:]
    return float(np.std(recent, ddof=1) * np.sqrt(252))


def classify_volatility(
    returns: np.ndarray, lookback: int = 60,
) -> tuple[VolatilityRegime, float, float]:
    """Classify volatility regime using percentile ranking.

    Uses the full lookback history to compute where current vol sits
    in the historical distribution. This is more robust than fixed thresholds.
    """
    if len(returns) < lookback:
        vol = compute_realized_volatility(returns)
        return VolatilityRegime.NORMAL, vol, 50.0

    rolling_vols: list[float] = []
    window = 20
    for i in range(window, len(returns)):
        rv = np.std(returns[i - window : i], ddof=1) * np.sqrt(252)
        rolling_vols.append(rv)

    if not rolling_vols:
        return VolatilityRegime.NORMAL, 0.2, 50.0

    current_vol = rolling_vols[-1]
    percentile = float(np.mean(np.array(rolling_vols) <= current_vol) * 100)

    if percentile > 95:
        regime = VolatilityRegime.CRISIS
    elif percentile > 75:
        regime = VolatilityRegime.HIGH_VOL
    elif percentile < 25:
        regime = VolatilityRegime.LOW_VOL
    else:
        regime = VolatilityRegime.NORMAL

    return regime, float(current_vol), percentile


def compute_adx(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14,
) -> float:
    """Average Directional Index — quantifies trend strength.

    ADX > 25 → trending
    ADX < 20 → ranging/mean-reverting
    """
    n = len(closes)
    if n < period + 2:
        return 20.0

    plus_dm = np.zeros(n - 1)
    minus_dm = np.zeros(n - 1)
    tr = np.zeros(n - 1)

    for i in range(n - 1):
        high_diff = highs[i + 1] - highs[i]
        low_diff = lows[i] - lows[i + 1]

        plus_dm[i] = high_diff if (high_diff > low_diff and high_diff > 0) else 0
        minus_dm[i] = low_diff if (low_diff > high_diff and low_diff > 0) else 0

        tr[i] = max(
            highs[i + 1] - lows[i + 1],
            abs(highs[i + 1] - closes[i]),
            abs(lows[i + 1] - closes[i]),
        )

    def wilder_smooth(arr: np.ndarray, per: int) -> np.ndarray:
        result = np.zeros(len(arr))
        result[per - 1] = np.sum(arr[:per])
        for j in range(per, len(arr)):
            result[j] = result[j - 1] - result[j - 1] / per + arr[j]
        return result

    smoothed_tr = wilder_smooth(tr, period)
    smoothed_plus = wilder_smooth(plus_dm, period)
    smoothed_minus = wilder_smooth(minus_dm, period)

    valid = smoothed_tr > 1e-10
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(valid, smoothed_plus / smoothed_tr * 100, 0)
        minus_di = np.where(valid, smoothed_minus / smoothed_tr * 100, 0)

    di_sum = plus_di + minus_di
    di_diff = np.abs(plus_di - minus_di)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(di_sum > 0, di_diff / di_sum * 100, 0)

    if len(dx) < period:
        return float(np.mean(dx)) if len(dx) > 0 else 20.0

    adx = wilder_smooth(dx, period)
    return float(adx[-1]) if adx[-1] > 0 else float(np.mean(dx[-period:]))


def classify_trend(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    hurst: float = 0.5, period: int = 14,
) -> tuple[TrendRegime, float]:
    """Classify trend regime using ADX + Hurst exponent.

    Combining two independent measures is more robust than either alone.
    """
    adx = compute_adx(highs, lows, closes, period)

    if adx > 30 and hurst > 0.55:
        regime = TrendRegime.STRONG_TREND
    elif adx > 20 and hurst > 0.5:
        regime = TrendRegime.WEAK_TREND
    elif hurst < 0.45 and adx < 25:
        regime = TrendRegime.MEAN_REVERTING
    else:
        regime = TrendRegime.CHOPPY

    return regime, adx


def classify_regime(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    hurst: float = 0.5,
    spy_returns: np.ndarray | None = None,
) -> RegimeState:
    """Full regime classification combining volatility + trend + correlation."""
    returns = np.diff(np.log(closes)) if len(closes) > 1 else np.array([0.0])

    vol_regime, realized_vol, vol_pct = classify_volatility(returns)
    trend_regime, adx = classify_trend(highs, lows, closes, hurst)

    corr = 0.0
    if spy_returns is not None and len(spy_returns) >= len(returns) and len(returns) > 5:
        aligned_spy = spy_returns[-len(returns):]
        corr_matrix = np.corrcoef(returns, aligned_spy)
        corr = float(corr_matrix[0, 1]) if not np.isnan(corr_matrix[0, 1]) else 0.0

    if vol_regime == VolatilityRegime.CRISIS:
        composite = CompositeRegime.CRISIS
    elif trend_regime in (TrendRegime.STRONG_TREND, TrendRegime.WEAK_TREND):
        if vol_regime in (VolatilityRegime.HIGH_VOL, VolatilityRegime.CRISIS):
            composite = CompositeRegime.TRENDING_HIGH_VOL
        else:
            composite = CompositeRegime.TRENDING_LOW_VOL
    elif trend_regime == TrendRegime.MEAN_REVERTING:
        composite = CompositeRegime.MEAN_REVERTING
    else:
        composite = CompositeRegime.CHOPPY

    strategy_map = {
        CompositeRegime.MEAN_REVERTING: "mean_reversion",
        CompositeRegime.TRENDING_LOW_VOL: "momentum",
        CompositeRegime.TRENDING_HIGH_VOL: "momentum_reduced",
        CompositeRegime.CRISIS: "defensive",
        CompositeRegime.CHOPPY: "avoid",
    }

    risk_scale_map = {
        CompositeRegime.MEAN_REVERTING: 1.0,
        CompositeRegime.TRENDING_LOW_VOL: 0.8,
        CompositeRegime.TRENDING_HIGH_VOL: 0.5,
        CompositeRegime.CRISIS: 0.2,
        CompositeRegime.CHOPPY: 0.3,
    }

    tradeable = composite not in (CompositeRegime.CHOPPY, CompositeRegime.CRISIS)

    confidence_components = [
        min(abs(adx - 22) / 10, 1.0),
        min(abs(vol_pct - 50) / 30, 1.0),
        min(abs(hurst - 0.5) * 4, 1.0),
    ]
    regime_conf = float(np.mean(confidence_components))

    return RegimeState(
        volatility_regime=vol_regime,
        trend_regime=trend_regime,
        composite_regime=composite,
        realized_vol_annualized=realized_vol,
        vol_percentile=vol_pct,
        adx_value=adx,
        correlation_to_spy=corr,
        tradeable=tradeable,
        preferred_strategy=strategy_map[composite],
        regime_confidence=regime_conf,
        risk_scaling_factor=risk_scale_map[composite],
    )


def compute_regime_fingerprint(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    hurst: float = 0.5,
    vix: float = 20.0,
) -> str:
    """Compute a regime fingerprint string for signal book cache keying.

    When this fingerprint changes, any cached signals for this instrument
    are invalidated because the market regime has shifted.
    """
    returns = np.diff(np.log(closes)) if len(closes) > 1 else np.array([0.0])
    vol_regime, _, _ = classify_volatility(returns)
    trend_regime, _ = classify_trend(highs, lows, closes, hurst)

    if vix > 35:
        vix_bucket = "EXTREME"
    elif vix > 25:
        vix_bucket = "HIGH"
    elif vix > 15:
        vix_bucket = "NORMAL"
    else:
        vix_bucket = "LOW"

    return f"{vol_regime.value}|{trend_regime.value}|VIX_{vix_bucket}"
