"""Multi-Timeframe Analysis Engine.

Institutional quant funds analyse on higher timeframes (H1, D1) and enter
on lower timeframes (M1, M5) for precision timing. This module:

1. Resamples raw OHLCV bars into any standard timeframe
2. Computes signals on each timeframe independently
3. Combines multi-timeframe confluence for high-conviction entries
4. Determines optimal entry timeframe based on volatility regime

Timeframe hierarchy:
    W1 > D1 > H4 > H1 > M30 > M15 > M5 > M3 > M1

Higher timeframes define direction/bias.
Lower timeframes refine entry timing.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from shared.logger import get_logger
from shared.types import OHLCVBar

log = get_logger("math.mtf")


class Timeframe(str, Enum):
    M1 = "M1"
    M3 = "M3"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"

    @property
    def minutes(self) -> int:
        return {
            "M1": 1, "M3": 3, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "D1": 1440, "W1": 10080,
        }[self.value]

    @property
    def is_intraday(self) -> bool:
        return self.minutes < 1440


ANALYSIS_TIMEFRAMES = [Timeframe.D1, Timeframe.H4, Timeframe.H1]
ENTRY_TIMEFRAMES = [Timeframe.M5, Timeframe.M3, Timeframe.M1]


@dataclass(frozen=True, slots=True)
class TimeframeSignal:
    """Signal generated on a single timeframe."""
    timeframe: Timeframe
    direction: str  # "LONG", "SHORT", "NEUTRAL"
    strength: float  # 0.0 to 1.0
    indicators: dict[str, float] = field(default_factory=dict)


@dataclass
class MTFSignal:
    """Multi-timeframe confluent signal."""
    ticker: str
    analysis_tf: Timeframe
    entry_tf: Timeframe
    direction: str
    confluence_score: float  # 0.0 to 1.0, higher = more TFs agree
    signals: list[TimeframeSignal] = field(default_factory=list)
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    rationale: str = ""


# ── Bar Resampling ───────────────────────────────────────────


def resample_bars(
    bars: list[OHLCVBar],
    target_tf: Timeframe,
) -> list[OHLCVBar]:
    """Resample OHLCV bars to a target timeframe.

    For daily data being downsampled to intraday: returns the daily bars as-is
    (intraday precision requires intraday source data).

    For daily data being upsampled to weekly: groups by ISO week.
    """
    if not bars:
        return []

    source_interval = _estimate_bar_interval(bars)

    if target_tf.minutes < source_interval:
        return bars

    if target_tf == Timeframe.D1 and source_interval <= 1440:
        return _resample_to_daily(bars)
    elif target_tf == Timeframe.W1:
        return _resample_to_weekly(bars)
    elif target_tf.minutes > source_interval:
        return _resample_generic(bars, target_tf.minutes)

    return bars


def _estimate_bar_interval(bars: list[OHLCVBar]) -> int:
    """Estimate the bar interval in minutes from timestamps."""
    if len(bars) < 2:
        return 1440
    deltas = [
        (bars[i + 1].timestamp - bars[i].timestamp).total_seconds() / 60
        for i in range(min(10, len(bars) - 1))
    ]
    avg = np.median(deltas) if deltas else 1440
    if avg > 600:
        return 1440
    return int(round(avg))


def _resample_to_daily(bars: list[OHLCVBar]) -> list[OHLCVBar]:
    """Group intraday bars into daily bars."""
    by_date: dict[dt.date, list[OHLCVBar]] = {}
    for bar in bars:
        d = bar.timestamp.date()
        by_date.setdefault(d, []).append(bar)

    daily: list[OHLCVBar] = []
    for date in sorted(by_date):
        day_bars = by_date[date]
        daily.append(OHLCVBar(
            timestamp=dt.datetime.combine(date, dt.time()),
            symbol=day_bars[0].symbol,
            open=day_bars[0].open,
            high=max(b.high for b in day_bars),
            low=min(b.low for b in day_bars),
            close=day_bars[-1].close,
            volume=sum(b.volume for b in day_bars),
        ))
    return daily


def _resample_to_weekly(bars: list[OHLCVBar]) -> list[OHLCVBar]:
    """Group bars into weekly bars (ISO week)."""
    by_week: dict[tuple[int, int], list[OHLCVBar]] = {}
    for bar in bars:
        iso = bar.timestamp.isocalendar()
        key = (iso[0], iso[1])
        by_week.setdefault(key, []).append(bar)

    weekly: list[OHLCVBar] = []
    for key in sorted(by_week):
        week_bars = by_week[key]
        weekly.append(OHLCVBar(
            timestamp=week_bars[0].timestamp,
            symbol=week_bars[0].symbol,
            open=week_bars[0].open,
            high=max(b.high for b in week_bars),
            low=min(b.low for b in week_bars),
            close=week_bars[-1].close,
            volume=sum(b.volume for b in week_bars),
        ))
    return weekly


def _resample_generic(bars: list[OHLCVBar], target_minutes: int) -> list[OHLCVBar]:
    """Generic resampler for arbitrary timeframe multiples."""
    if not bars:
        return []

    resampled: list[OHLCVBar] = []
    bucket: list[OHLCVBar] = [bars[0]]
    bucket_start = bars[0].timestamp

    for bar in bars[1:]:
        elapsed = (bar.timestamp - bucket_start).total_seconds() / 60
        if elapsed >= target_minutes:
            resampled.append(OHLCVBar(
                timestamp=bucket_start,
                symbol=bucket[0].symbol,
                open=bucket[0].open,
                high=max(b.high for b in bucket),
                low=min(b.low for b in bucket),
                close=bucket[-1].close,
                volume=sum(b.volume for b in bucket),
            ))
            bucket = [bar]
            bucket_start = bar.timestamp
        else:
            bucket.append(bar)

    if bucket:
        resampled.append(OHLCVBar(
            timestamp=bucket_start,
            symbol=bucket[0].symbol,
            open=bucket[0].open,
            high=max(b.high for b in bucket),
            low=min(b.low for b in bucket),
            close=bucket[-1].close,
            volume=sum(b.volume for b in bucket),
        ))

    return resampled


# ── Single-Timeframe Signal Analysis ─────────────────────────


def analyse_timeframe(
    bars: list[OHLCVBar],
    timeframe: Timeframe,
) -> TimeframeSignal:
    """Compute directional signal on a single timeframe.

    Uses institutional-grade indicators:
    - EMA crossover (8/21/55) for trend direction
    - RSI(14) for momentum
    - ATR(14)-normalised z-score for mean reversion detection
    - Volume profile for confirmation
    - Hurst exponent for regime
    """
    resampled = resample_bars(bars, timeframe)
    if len(resampled) < 25:
        return TimeframeSignal(timeframe=timeframe, direction="NEUTRAL", strength=0.0)

    closes = np.array([b.close for b in resampled])
    highs = np.array([b.high for b in resampled])
    lows = np.array([b.low for b in resampled])
    volumes = np.array([b.volume for b in resampled], dtype=float)

    ema_8 = _ema(closes, 8)
    ema_21 = _ema(closes, 21)
    ema_55 = _ema(closes, min(55, len(closes) - 1))

    rsi = _rsi(closes, 14)

    atr = _atr(highs, lows, closes, 14)
    sma_20 = float(np.mean(closes[-20:]))
    z_score = (closes[-1] - sma_20) / max(atr, 1e-10)

    vol_sma = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else float(np.mean(volumes))
    vol_ratio = float(volumes[-1] / max(vol_sma, 1)) if vol_sma > 0 else 1.0

    long_score = 0.0
    short_score = 0.0

    if ema_8 > ema_21 > ema_55:
        long_score += 0.30
    elif ema_8 > ema_21:
        long_score += 0.15
    if ema_8 < ema_21 < ema_55:
        short_score += 0.30
    elif ema_8 < ema_21:
        short_score += 0.15

    if rsi < 30:
        long_score += 0.25
    elif rsi < 40:
        long_score += 0.10
    if rsi > 70:
        short_score += 0.25
    elif rsi > 60:
        short_score += 0.10

    if z_score < -2.0:
        long_score += 0.20
    elif z_score < -1.0:
        long_score += 0.10
    if z_score > 2.0:
        short_score += 0.20
    elif z_score > 1.0:
        short_score += 0.10

    if vol_ratio > 1.5:
        long_score += 0.10
        short_score += 0.10

    ret_5 = float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
    if ret_5 > 0.02:
        long_score += 0.15
    elif ret_5 < -0.02:
        short_score += 0.15

    if long_score > short_score and long_score > 0.25:
        direction = "LONG"
        strength = min(long_score, 1.0)
    elif short_score > long_score and short_score > 0.25:
        direction = "SHORT"
        strength = min(short_score, 1.0)
    else:
        direction = "NEUTRAL"
        strength = 0.0

    indicators = {
        "ema_8": ema_8, "ema_21": ema_21, "ema_55": ema_55,
        "rsi": rsi, "atr": atr, "z_score": z_score,
        "vol_ratio": vol_ratio, "ret_5": ret_5,
    }

    return TimeframeSignal(
        timeframe=timeframe, direction=direction,
        strength=strength, indicators=indicators,
    )


# ── Multi-Timeframe Confluence ───────────────────────────────


def compute_mtf_signal(
    bars: list[OHLCVBar],
    ticker: str,
    analysis_tfs: list[Timeframe] | None = None,
    entry_tfs: list[Timeframe] | None = None,
) -> MTFSignal | None:
    """Compute a multi-timeframe confluent signal.

    1. Analyse each higher timeframe for direction
    2. Check confluence (do they agree?)
    3. Use lowest timeframe for entry timing
    4. Compute entry/SL/TP from the entry timeframe
    """
    if analysis_tfs is None:
        analysis_tfs = ANALYSIS_TIMEFRAMES
    if entry_tfs is None:
        entry_tfs = ENTRY_TIMEFRAMES

    signals: list[TimeframeSignal] = []
    for tf in analysis_tfs:
        sig = analyse_timeframe(bars, tf)
        signals.append(sig)

    directional = [s for s in signals if s.direction != "NEUTRAL"]
    if not directional:
        return None

    long_count = sum(1 for s in directional if s.direction == "LONG")
    short_count = sum(1 for s in directional if s.direction == "SHORT")
    total_active = len(directional)

    if long_count > short_count:
        consensus_dir = "LONG"
        agreement = long_count / max(total_active, 1)
    elif short_count > long_count:
        consensus_dir = "SHORT"
        agreement = short_count / max(total_active, 1)
    else:
        return None

    if agreement < 0.60:
        return None

    weighted_strength = sum(
        s.strength * (1.5 if s.direction == consensus_dir else 0.3)
        for s in directional
    ) / max(total_active, 1)

    confluence = agreement * min(weighted_strength, 1.0)

    entry_sig = None
    for tf in entry_tfs:
        sig = analyse_timeframe(bars, tf)
        if sig.direction == consensus_dir:
            entry_sig = sig
            break
    if entry_sig is None:
        entry_sig = signals[-1]

    closes = np.array([b.close for b in bars])
    current_price = float(closes[-1])
    atr = entry_sig.indicators.get("atr", current_price * 0.02)

    if consensus_dir == "LONG":
        sl = current_price - 2.0 * atr
        tp = current_price + 3.0 * atr
    else:
        sl = current_price + 2.0 * atr
        tp = current_price - 3.0 * atr

    tf_summary = ", ".join(f"{s.timeframe.value}={s.direction[0]}({s.strength:.0%})" for s in signals)
    rationale = f"MTF confluence={confluence:.0%} [{tf_summary}]"

    return MTFSignal(
        ticker=ticker,
        analysis_tf=analysis_tfs[0],
        entry_tf=entry_sig.timeframe,
        direction=consensus_dir,
        confluence_score=confluence,
        signals=signals,
        entry_price=current_price,
        stop_loss=sl,
        take_profit=tp,
        rationale=rationale,
    )


def select_optimal_timeframes(
    bars: list[OHLCVBar],
    volatility_regime: str = "NORMAL",
) -> tuple[list[Timeframe], list[Timeframe]]:
    """Dynamically select analysis and entry timeframes based on conditions.

    High-volatility → use longer analysis TFs, faster entry TFs.
    Low-volatility → use shorter analysis TFs, standard entry TFs.
    """
    if volatility_regime == "CRISIS" or volatility_regime == "HIGH_VOL":
        return [Timeframe.W1, Timeframe.D1, Timeframe.H4], [Timeframe.M5, Timeframe.M3]
    elif volatility_regime == "LOW_VOL":
        return [Timeframe.H4, Timeframe.H1], [Timeframe.M15, Timeframe.M5]
    else:
        return [Timeframe.D1, Timeframe.H4, Timeframe.H1], [Timeframe.M5, Timeframe.M3, Timeframe.M1]


# ── Helpers ──────────────────────────────────────────────────


def _ema(data: np.ndarray, period: int) -> float:
    if len(data) < period:
        return float(data[-1]) if len(data) > 0 else 0.0
    multiplier = 2.0 / (period + 1)
    ema_val = float(data[0])
    for val in data[1:]:
        ema_val = (float(val) - ema_val) * multiplier + ema_val
    return ema_val


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return float(np.mean(highs - lows)) if len(highs) > 0 else 0.0
    tr = np.maximum(
        highs[-period:] - lows[-period:],
        np.maximum(
            np.abs(highs[-period:] - closes[-period - 1:-1]),
            np.abs(lows[-period:] - closes[-period - 1:-1]),
        ),
    )
    return float(np.mean(tr))
