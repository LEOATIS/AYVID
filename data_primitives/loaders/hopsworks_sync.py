"""Hopsworks Feature Store sync for rolling volatility, covariance matrices,
Hurst exponent, and Ornstein-Uhlenbeck mean-reversion speed.

When Hopsworks is unavailable (no API key), computes features locally
using NumPy and caches them in-memory for the Kelly Criterion engine.
"""
from __future__ import annotations

import datetime as dt
import itertools
from typing import Any

import numpy as np

from shared.config import settings
from shared.logger import get_logger
from shared.types import OHLCVBar

log = get_logger("data.hopsworks")

_feature_cache: dict[str, dict[str, Any]] = {}
_covariance_cache: dict[str, dict[str, Any]] = {}

_VOLATILITY_WINDOWS: tuple[int, ...] = (5, 10, 30, 60)
_FEATURE_VECTOR_KEYS: tuple[str, ...] = (
    "rolling_volatility_5d",
    "rolling_volatility_10d",
    "rolling_volatility_30d",
    "rolling_volatility_60d",
    "hurst_exponent",
    "mean_reversion_speed",
    "expected_return_annualized",
)


# ── Volatility ───────────────────────────────────────────────


def compute_rolling_volatility(
    bars: list[OHLCVBar],
    window: int = 30,
) -> float:
    """Annualized rolling volatility from OHLCV bars."""
    if len(bars) < window + 1:
        return 0.0
    closes = np.array([b.close for b in bars[-(window + 1):]])
    log_returns = np.diff(np.log(closes))
    return float(np.std(log_returns, ddof=1)) * np.sqrt(252)


def compute_multi_window_volatility(
    bars: list[OHLCVBar],
) -> dict[str, float]:
    """Annualized volatility for windows 5, 10, 30, 60 days."""
    return {
        f"rolling_volatility_{w}d": compute_rolling_volatility(bars, window=w)
        for w in _VOLATILITY_WINDOWS
    }


# ── Expected Returns ─────────────────────────────────────────


def compute_expected_returns(
    bars: list[OHLCVBar],
    window: int = 30,
) -> float:
    """Annualized expected return from recent price data."""
    if len(bars) < 2:
        return 0.0
    recent = bars[-min(window, len(bars)):]
    total_return = recent[-1].close / recent[0].close - 1.0
    days = max((recent[-1].timestamp - recent[0].timestamp).days, 1)
    return total_return * (252 / days)


# ── Hurst Exponent (R/S Analysis) ────────────────────────────


def compute_hurst_exponent(
    bars: list[OHLCVBar],
    max_lag: int = 20,
) -> float:
    """Hurst exponent via rescaled-range (R/S) analysis on log-returns.

    H < 0.5 → mean-reverting, H ≈ 0.5 → random walk, H > 0.5 → trending.
    """
    if len(bars) < max_lag + 2:
        return 0.5

    closes = np.array([b.close for b in bars])
    log_returns = np.diff(np.log(closes))
    n = len(log_returns)

    lags: list[int] = []
    rs_values: list[float] = []

    for lag in range(2, min(max_lag + 1, n // 2 + 1)):
        num_chunks = n // lag
        if num_chunks < 1:
            continue

        rs_for_lag: list[float] = []
        for i in range(num_chunks):
            chunk = log_returns[i * lag : (i + 1) * lag]
            mean_chunk = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_chunk)
            r = float(np.max(deviations) - np.min(deviations))
            s = float(np.std(chunk, ddof=1))
            if s > 1e-12:
                rs_for_lag.append(r / s)

        if rs_for_lag:
            lags.append(lag)
            rs_values.append(np.mean(rs_for_lag))

    if len(lags) < 2:
        return 0.5

    log_lags = np.log(lags)
    log_rs = np.log(rs_values)
    coeffs = np.polyfit(log_lags, log_rs, 1)
    return float(np.clip(coeffs[0], 0.0, 1.0))


# ── Ornstein-Uhlenbeck Mean-Reversion Speed ─────────────────


def compute_ou_kappa(
    bars: list[OHLCVBar],
    window: int = 30,
) -> float:
    """OU process kappa (mean-reversion speed) via OLS on log-prices.

    kappa > 0 indicates mean-reverting; higher = faster reversion.
    """
    if len(bars) < window + 1:
        return 0.0

    closes = np.array([b.close for b in bars[-(window + 1):]])
    log_prices = np.log(closes)
    y = np.diff(log_prices)
    x = log_prices[:-1] - np.mean(log_prices[:-1])

    x_sq_sum = float(np.dot(x, x))
    if x_sq_sum < 1e-12:
        return 0.0

    beta = float(np.dot(x, y)) / x_sq_sum
    kappa = -beta * 252
    return float(max(kappa, 0.0))


# ── Covariance & Pairwise Features ──────────────────────────


def compute_covariance_matrix(
    multi_bars: dict[str, list[OHLCVBar]],
    window: int = 30,
) -> tuple[list[str], np.ndarray]:
    """Rolling covariance matrix across multiple assets (annualized)."""
    tickers = sorted(multi_bars.keys())
    if not tickers:
        return [], np.array([])

    min_len = min(len(multi_bars[t]) for t in tickers)
    effective_window = min(window, min_len - 1)
    if effective_window < 2:
        n = len(tickers)
        return tickers, np.eye(n) * 0.04

    returns_matrix = _build_returns_matrix(tickers, multi_bars, effective_window)
    cov = np.cov(returns_matrix) * 252
    return tickers, cov


def compute_pairwise_features(
    multi_bars: dict[str, list[OHLCVBar]],
    window: int = 30,
) -> list[dict[str, Any]]:
    """Correlation, covariance, and beta for all ticker pairs."""
    tickers, cov = compute_covariance_matrix(multi_bars, window=window)
    if len(tickers) < 2:
        return []

    results: list[dict[str, Any]] = []
    variances = np.diag(cov)

    for i, j in itertools.combinations(range(len(tickers)), 2):
        var_i = variances[i]
        var_j = variances[j]
        std_i = np.sqrt(var_i) if var_i > 0 else 1e-12
        std_j = np.sqrt(var_j) if var_j > 0 else 1e-12

        covariance = float(cov[i, j])
        correlation = covariance / (std_i * std_j)
        beta = covariance / var_j if var_j > 1e-12 else 0.0

        pair_key = f"{tickers[i]}_{tickers[j]}"
        record = {
            "pair_key": pair_key,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ticker_a": tickers[i],
            "ticker_b": tickers[j],
            "correlation_30d": float(np.clip(correlation, -1.0, 1.0)),
            "covariance_30d": covariance,
            "beta": float(beta),
        }
        results.append(record)
        _covariance_cache[pair_key] = record
        _covariance_cache[f"{tickers[j]}_{tickers[i]}"] = record

    return results


def _build_returns_matrix(
    tickers: list[str],
    multi_bars: dict[str, list[OHLCVBar]],
    effective_window: int,
) -> np.ndarray:
    returns_list: list[np.ndarray] = []
    for t in tickers:
        closes = np.array(
            [b.close for b in multi_bars[t][-(effective_window + 1):]]
        )
        returns_list.append(np.diff(np.log(closes)))

    min_ret_len = min(len(r) for r in returns_list)
    trimmed = [r[-min_ret_len:] for r in returns_list]
    return np.vstack(trimmed)


# ── Feature Retrieval ────────────────────────────────────────


def get_cached_features(symbol: str) -> dict[str, Any] | None:
    """Retrieve locally cached features for a symbol."""
    return _feature_cache.get(symbol)


def get_cached_covariance(ticker_a: str, ticker_b: str) -> dict[str, Any] | None:
    """Retrieve pairwise features for two tickers (order-agnostic)."""
    key = f"{ticker_a}_{ticker_b}"
    result = _covariance_cache.get(key)
    if result is not None:
        return result
    return _covariance_cache.get(f"{ticker_b}_{ticker_a}")


def get_feature_vector(symbol: str) -> np.ndarray:
    """Fixed-length feature vector for ML models.

    Returns [vol_5d, vol_10d, vol_30d, vol_60d, hurst, kappa, expected_return].
    All zeros if no cached features exist.
    """
    cached = _feature_cache.get(symbol)
    if cached is None:
        return np.zeros(len(_FEATURE_VECTOR_KEYS), dtype=np.float64)
    return np.array(
        [cached.get(k, 0.0) for k in _FEATURE_VECTOR_KEYS],
        dtype=np.float64,
    )


def get_all_cached_features() -> dict[str, dict[str, Any]]:
    """Return entire feature cache."""
    return dict(_feature_cache)


# ── Sync: Single Symbol ─────────────────────────────────────


async def sync_features(
    symbol: str,
    bars: list[OHLCVBar],
) -> dict[str, Any]:
    """Compute and cache all features for one symbol. Pushes to Hopsworks if configured."""
    vols = compute_multi_window_volatility(bars)
    expected_ret = compute_expected_returns(bars)
    hurst = compute_hurst_exponent(bars)
    kappa = compute_ou_kappa(bars)

    features: dict[str, Any] = {
        "symbol": symbol,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        **vols,
        "expected_return_annualized": expected_ret,
        "hurst_exponent": hurst,
        "mean_reversion_speed": kappa,
        "bar_count": len(bars),
    }

    _feature_cache[symbol] = features

    if settings.HOPSWORKS_API_KEY:
        await _push_volatility_to_hopsworks(symbol, features)
    else:
        log.debug(f"Features cached locally for {symbol} (no Hopsworks key)")

    return features


# ── Sync: All Tickers + Pairwise ─────────────────────────────


async def sync_all_features(
    multi_bars: dict[str, list[OHLCVBar]],
) -> dict[str, dict[str, Any]]:
    """Sync volatility features for every ticker AND pairwise covariance features."""
    results: dict[str, dict[str, Any]] = {}

    for symbol, bars in multi_bars.items():
        results[symbol] = await sync_features(symbol, bars)

    pairs = compute_pairwise_features(multi_bars)

    if settings.HOPSWORKS_API_KEY and pairs:
        await _push_covariance_to_hopsworks(pairs)

    if pairs:
        log.info(f"Computed {len(pairs)} pairwise features across {len(multi_bars)} tickers")

    return results


# ── Hopsworks Push ───────────────────────────────────────────


async def _push_volatility_to_hopsworks(
    symbol: str,
    features: dict[str, Any],
) -> None:
    try:
        import hopsworks
        import pandas as pd

        project = hopsworks.login(
            api_key_value=settings.HOPSWORKS_API_KEY,
            project=settings.HOPSWORKS_PROJECT,
        )
        fs = project.get_feature_store()
        fg = fs.get_or_create_feature_group(
            name="ayvid_volatility",
            version=2,
            primary_key=["symbol"],
            event_time="timestamp",
        )
        fg.insert(pd.DataFrame([features]))
        log.info(f"Pushed volatility features to Hopsworks for {symbol}")
    except Exception as exc:
        log.warning(f"Hopsworks volatility sync failed for {symbol}: {exc}")


async def _push_covariance_to_hopsworks(
    pairs: list[dict[str, Any]],
) -> None:
    try:
        import hopsworks
        import pandas as pd

        project = hopsworks.login(
            api_key_value=settings.HOPSWORKS_API_KEY,
            project=settings.HOPSWORKS_PROJECT,
        )
        fs = project.get_feature_store()
        fg = fs.get_or_create_feature_group(
            name="ayvid_covariance",
            version=1,
            primary_key=["pair_key"],
            event_time="timestamp",
        )
        fg.insert(pd.DataFrame(pairs))
        log.info(f"Pushed {len(pairs)} covariance pairs to Hopsworks")
    except Exception as exc:
        log.warning(f"Hopsworks covariance sync failed: {exc}")
