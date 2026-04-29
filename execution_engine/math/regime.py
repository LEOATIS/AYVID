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

    # ── Volatility Risk Premium & grind-up gating (Tier-1 regime filter) ──
    # rv_short_annualized: 20-day realized vol (recent regime)
    # rv_long_annualized:  60-day realized vol (baseline regime)
    # vol_compression:     rv_short / rv_long — <0.85 means vol is falling
    # vrp:                 implied_vol − rv_short (annualized). Positive = IV
    #                      premium is rich (sell vol edge); negative = cheap
    # grind_up_flag:       low-vol bullish drift; long-vol strategies bleed here
    # long_vol_blocked:    convenience flag used by the orchestrator gate
    rv_short_annualized: float = 0.0
    rv_long_annualized: float = 0.0
    vol_compression: float = 1.0
    vrp: float = 0.0
    grind_up_flag: bool = False
    long_vol_blocked: bool = False


def compute_realized_volatility(returns: np.ndarray, window: int = 20) -> float:
    """Annualized realized volatility. Delegates to the canonical primitive."""
    from execution_engine.math._primitives import compute_realized_vol

    return compute_realized_vol(returns, window=window)


def compute_vrp(implied_vol: float | None, realized_vol: float) -> float:
    """Volatility Risk Premium = IV − RV (annualized).

    Positive → options are pricing in more vol than is being realized
    (premium is rich → sell-vol edge; buying puts/calls is statistically
    expensive).  Negative → options are cheap relative to realized.

    Returns 0.0 when IV is unavailable (neutral / do-not-act).
    """
    if implied_vol is None or not np.isfinite(implied_vol) or implied_vol <= 0:
        return 0.0
    if not np.isfinite(realized_vol) or realized_vol <= 0:
        return 0.0
    return float(implied_vol - realized_vol)


def compute_vol_windows(
    returns: np.ndarray,
    short: int = 20,
    long: int = 60,
) -> tuple[float, float, float]:
    """Realized-vol at two horizons + compression ratio.

    vol_compression = RV_short / RV_long
      < 0.85 → vol is currently compressed (calm after a storm)
      > 1.15 → vol is expanding (regime shift upward)

    We require both windows to have data; falls back gracefully.
    """
    if len(returns) < short + 2:
        rv = compute_realized_volatility(returns, short) if len(returns) > 1 else 0.2
        return rv, rv, 1.0
    rv_s = float(np.std(returns[-short:], ddof=1) * np.sqrt(252))
    if len(returns) < long + 2:
        return rv_s, rv_s, 1.0
    rv_l = float(np.std(returns[-long:], ddof=1) * np.sqrt(252))
    if rv_l < 1e-6:
        return rv_s, rv_l, 1.0
    return rv_s, rv_l, float(rv_s / rv_l)


def is_grind_up_regime(
    *,
    vol_percentile: float,
    vol_compression: float,
    ret_20d: float,
    adx: float,
    hurst: float,
    vrp: float,
) -> bool:
    """Detect the 'Low-Vol / Grind-Up' regime that kills long-vol strategies.

    The arithmetic: a grind-up market has all six of these simultaneously —
      1. Current RV is in the bottom quartile of its own history (vol_pct<25)
      2. RV is still compressing, not expanding (vol_compression<0.85)
      3. Net positive 20-day drift (ret_20d>0)
      4. No dominant directional trend (ADX<22 → below the 22 noise floor)
      5. Hurst sits near the random-walk boundary (0.48-0.60). Pure trending
         (H>0.60) markets want momentum; pure MR (H<0.45) wants MR options —
         the danger zone is the middle, where nothing obvious works.
      6. IV isn't already screaming (VRP < 0.04). If IV is rich, the market
         itself is warning us; we don't need the grind-up filter to block.
    """
    return (
        vol_percentile < 25.0
        and vol_compression < 0.85
        and ret_20d > 0.0
        and adx < 22.0
        and 0.48 <= hurst <= 0.60
        and vrp < 0.04
    )


def classify_volatility(
    returns: np.ndarray,
    lookback: int = 60,
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


def _wilder_running_sum(arr: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothed running sum (used for TR, +DM, -DM aggregates).

    Formula:
        S[period-1] = Σ arr[0..period-1]                 (seed)
        S[t]        = S[t-1] - S[t-1]/period + arr[t]    (recursion)

    This is what Welles Wilder originally defined in "New Concepts in
    Technical Trading Systems" (1978) for the raw TR/DM aggregates —
    it is NOT a mean, it is a smoothed rolling sum with ~1/period decay.
    """
    n = len(arr)
    out = np.zeros(n)
    if n < period:
        return out
    out[period - 1] = float(np.sum(arr[:period]))
    for j in range(period, n):
        out[j] = out[j - 1] - out[j - 1] / period + arr[j]
    return out


def _wilder_ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Wilder's exponential moving AVERAGE (used for final ADX smoothing).

    Formula (equivalent to EMA with α = 1/period):
        EMA[period-1] = mean(arr[0..period-1])
        EMA[t]        = (EMA[t-1] * (period - 1) + arr[t]) / period

    This is what the ADX line must use — smoothing an already-%-based
    series (DX ∈ [0,100]) so ADX stays strictly within [0,100]. The
    pre-fix implementation mistakenly used the running-SUM formula on
    DX, producing values of period × avg(DX) ≈ 140–700.
    """
    n = len(arr)
    out = np.zeros(n)
    if n < period:
        return out
    out[period - 1] = float(np.mean(arr[:period]))
    for j in range(period, n):
        out[j] = (out[j - 1] * (period - 1) + arr[j]) / period
    return out


def compute_adx(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> float:
    """Average Directional Index — quantifies trend strength, bounded [0, 100].

    ADX > 25 → trending
    ADX < 20 → ranging / mean-reverting

    Pipeline (strict Wilder, 1978):
        1. TR, +DM, -DM per bar
        2. Running-sum smoothing via _wilder_running_sum   → bounded-natural
        3. +DI, -DI  = 100 × (smoothed ±DM / smoothed TR)  → each ∈ [0, 100]
        4. DX        = 100 × |+DI − −DI| / (+DI + −DI)     → ∈ [0, 100]
        5. ADX       = _wilder_ema(DX, period)              → ∈ [0, 100]

    The returned value is defensively clipped to [0, 100] and NaN-
    protected so downstream regime logic never sees a pathological
    number.
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

        plus_dm[i] = high_diff if (high_diff > low_diff and high_diff > 0) else 0.0
        minus_dm[i] = low_diff if (low_diff > high_diff and low_diff > 0) else 0.0

        tr[i] = max(
            highs[i + 1] - lows[i + 1],
            abs(highs[i + 1] - closes[i]),
            abs(lows[i + 1] - closes[i]),
        )

    smoothed_tr = _wilder_running_sum(tr, period)
    smoothed_plus = _wilder_running_sum(plus_dm, period)
    smoothed_minus = _wilder_running_sum(minus_dm, period)

    valid = smoothed_tr > 1e-10
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(valid, smoothed_plus / smoothed_tr * 100.0, 0.0)
        minus_di = np.where(valid, smoothed_minus / smoothed_tr * 100.0, 0.0)

    di_sum = plus_di + minus_di
    di_diff = np.abs(plus_di - minus_di)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(di_sum > 1e-10, di_diff / di_sum * 100.0, 0.0)

    dx = np.nan_to_num(np.clip(dx, 0.0, 100.0), nan=0.0)

    if len(dx) < period:
        val = float(np.mean(dx)) if len(dx) > 0 else 20.0
        return float(np.clip(val, 0.0, 100.0))

    adx_series = _wilder_ema(dx, period)
    adx_last = float(adx_series[-1])
    if not np.isfinite(adx_last) or adx_last <= 0:
        adx_last = float(np.mean(dx[-period:]))
    return float(np.clip(adx_last, 0.0, 100.0))


def classify_trend(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    hurst: float = 0.5,
    period: int = 14,
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
    implied_vol: float | None = None,
) -> RegimeState:
    """Full regime classification combining volatility + trend + correlation.

    When `implied_vol` is provided (or proxied upstream), VRP and the
    grind-up filter are populated; otherwise they default to neutral.
    """
    returns = np.diff(np.log(closes)) if len(closes) > 1 else np.array([0.0])

    vol_regime, realized_vol, vol_pct = classify_volatility(returns)
    trend_regime, adx = classify_trend(highs, lows, closes, hurst)

    corr = 0.0
    if spy_returns is not None and len(spy_returns) >= len(returns) and len(returns) > 5:
        aligned_spy = spy_returns[-len(returns) :]
        corr_matrix = np.corrcoef(returns, aligned_spy)
        corr = float(corr_matrix[0, 1]) if not np.isnan(corr_matrix[0, 1]) else 0.0

    rv_short, rv_long, compression = compute_vol_windows(returns, short=20, long=60)
    vrp = compute_vrp(implied_vol, rv_short)
    ret_20d = 0.0
    if len(closes) >= 21:
        ret_20d = float(closes[-1] / closes[-21] - 1)
    grind_up = is_grind_up_regime(
        vol_percentile=vol_pct,
        vol_compression=compression,
        ret_20d=ret_20d,
        adx=adx,
        hurst=hurst,
        vrp=vrp,
    )

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
        rv_short_annualized=rv_short,
        rv_long_annualized=rv_long,
        vol_compression=compression,
        vrp=vrp,
        grind_up_flag=grind_up,
        long_vol_blocked=grind_up,
    )


def detect_vix_spike_reversal(
    vix_values: np.ndarray,
    spx_returns: np.ndarray | None = None,
    spike_threshold: float = 35.0,
) -> bool:
    """Detect VIX spike reversal setup (VIX > 35 and declining 2+ days).

    This is one of the highest-probability F&O setups: after VIX spikes
    above 35, it declines within 30 days ~78% of the time.

    Returns True when:
    - Current VIX > spike_threshold (35)
    - VIX has declined for at least 2 consecutive days
    - If spx_returns provided, SPX 2-day return > -1% (stabilising)
    """
    if len(vix_values) < 4:
        return False
    current = float(vix_values[-1])
    if current < spike_threshold:
        return False
    # Check VIX declining for 2+ consecutive days
    if not (vix_values[-1] < vix_values[-2] < vix_values[-3]):
        return False
    # Optional SPX stabilisation check
    if spx_returns is not None and len(spx_returns) >= 2:
        two_day_ret = float(np.sum(spx_returns[-2:]))
        if two_day_ret < -0.01:
            return False
    return True


def compute_iv_percentile(
    historical_iv: np.ndarray,
) -> float:
    """Compute IV Percentile rank (0-100).

    >65 = elevated (premium selling territory)
    <35 = suppressed (premium buying territory)
    """
    if len(historical_iv) < 10:
        return 50.0
    current = float(historical_iv[-1])
    rank = float(np.sum(historical_iv[:-1] < current)) / max(len(historical_iv) - 1, 1)
    return round(rank * 100.0, 1)


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
