"""Dynamic liquidity tier classifier — replaces any hardcoded "S&P 500"
membership list. Purely data-driven from local OHLCV.

A ticker is classified as LIQUID when it clears both of:

* 30-day median dollar-volume ≥ ``MIN_MEDIAN_DOLLAR_VOL``
  (default ~$50M/day — comfortably above the IB pre-market book depth
  needed for a midpoint LIMIT to fill inside a 2-minute window).
* 30-day median spread ≤ ``MAX_MEDIAN_SPREAD_BPS``
  (proxied from high-low range on 1-day bars — the only microstructure
  signal available without a live quote feed).

Anything below either threshold is ILLIQUID — the existing 09:45 ET
delay path still applies.

The thresholds are env-tunable and the classification is memoised for a
short TTL so the hot path doesn't re-read every OHLCV file on every
submission.
"""

from __future__ import annotations

import os
import threading
import time
from enum import Enum
from typing import Any

from shared.logger import get_logger

log = get_logger("broker.liquidity")


class LiquidityTier(str, Enum):
    LIQUID = "LIQUID"  # midpoint LIMIT at pre-market OK
    ILLIQUID = "ILLIQUID"  # delay to 09:45 ET
    UNKNOWN = "UNKNOWN"  # not enough data → treat as illiquid (safer)


# ── Configuration (env-tunable, no hardcoded ticker list) ────

MIN_MEDIAN_DOLLAR_VOL = float(os.environ.get("AYVID_LIQUID_MIN_DOLLAR_VOL", 50_000_000))
MAX_MEDIAN_SPREAD_BPS = float(
    os.environ.get("AYVID_LIQUID_MAX_SPREAD_BPS", 15.0)  # 15 bps ≈ 0.15%
)
WINDOW_BARS = int(os.environ.get("AYVID_LIQUID_WINDOW_BARS", 30))
CACHE_TTL_SEC = int(os.environ.get("AYVID_LIQUID_TTL_SEC", 900))  # 15 min


# ── Cache (P1-D: protected by threading.Lock) ────────────────

_cache: dict[str, tuple[LiquidityTier, dict[str, Any], float]] = {}
_cache_lock = threading.Lock()


def _now_monotonic() -> float:
    return time.monotonic()


def _cache_get(ticker: str) -> tuple[LiquidityTier, dict[str, Any]] | None:
    with _cache_lock:
        hit = _cache.get(ticker)
        if not hit:
            return None
        tier, meta, ts = hit
        if _now_monotonic() - ts > CACHE_TTL_SEC:
            return None
        return tier, meta


def _cache_put(ticker: str, tier: LiquidityTier, meta: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[ticker] = (tier, meta, _now_monotonic())


# ── Classifier ───────────────────────────────────────────────


def classify(ticker: str) -> tuple[LiquidityTier, dict[str, Any]]:
    """Return the liquidity tier for a ticker plus a small meta dict.

    No network calls. Pure local-data classification.
    """
    hit = _cache_get(ticker)
    if hit:
        return hit

    try:
        from backtesting.engine import _load_local_bars
    except Exception as exc:
        log.debug(f"liquidity: backtest engine unavailable ({exc})")
        meta = {"reason": "engine_unavailable"}
        _cache_put(ticker, LiquidityTier.UNKNOWN, meta)
        return LiquidityTier.UNKNOWN, meta

    bars = _load_local_bars(ticker)
    if len(bars) < 10:
        meta = {"reason": "insufficient_history", "bars": len(bars)}
        _cache_put(ticker, LiquidityTier.UNKNOWN, meta)
        return LiquidityTier.UNKNOWN, meta

    window = bars[-WINDOW_BARS:]

    # Dollar-volume median
    dvs = sorted(b.close * b.volume for b in window if b.volume > 0 and b.close > 0)
    if not dvs:
        meta = {"reason": "no_volume", "bars": len(window)}
        _cache_put(ticker, LiquidityTier.UNKNOWN, meta)
        return LiquidityTier.UNKNOWN, meta
    median_dv = dvs[len(dvs) // 2]

    # High-low spread proxy (bps)
    spreads = []
    for b in window:
        if b.close > 0 and b.high >= b.low:
            spreads.append(1e4 * (b.high - b.low) / b.close)
    spreads.sort()
    median_spread_bps = spreads[len(spreads) // 2] if spreads else float("inf")

    dv_ok = median_dv >= MIN_MEDIAN_DOLLAR_VOL
    spread_ok = median_spread_bps <= MAX_MEDIAN_SPREAD_BPS

    meta = {
        "median_dollar_volume": median_dv,
        "median_spread_bps": round(median_spread_bps, 2),
        "window_bars": len(window),
        "thresholds": {
            "min_dollar_vol": MIN_MEDIAN_DOLLAR_VOL,
            "max_spread_bps": MAX_MEDIAN_SPREAD_BPS,
        },
        "dv_ok": dv_ok,
        "spread_ok": spread_ok,
    }

    tier = LiquidityTier.LIQUID if (dv_ok and spread_ok) else LiquidityTier.ILLIQUID
    _cache_put(ticker, tier, meta)
    return tier, meta


def invalidate_cache(ticker: str | None = None) -> None:
    """Drop cached classifications. Call after ingesting new OHLCV."""
    with _cache_lock:
        if ticker is None:
            _cache.clear()
        else:
            _cache.pop(ticker, None)


# ── Pre-market spread midpoint ───────────────────────────────


async def compute_pre_market_midpoint(ticker: str) -> float | None:
    """Best-effort pre-market spread midpoint from OpenBB, when available.

    Returns ``None`` if no pre-market quote is reachable; caller should
    fall back to the delay path. We deliberately do NOT synthesise a
    midpoint from the last RTH close — that gets torched by overnight
    gaps and is how the original "pre-market trap" bug happened.
    """
    try:
        from data_primitives.extractors.openbb_client import fetch_quote
    except Exception:
        return None

    try:
        quote = await fetch_quote(ticker)
    except Exception as exc:
        log.debug(f"liquidity: pre-market quote fetch failed for {ticker}: {exc}")
        return None

    if not quote:
        return None
    bid = float(quote.get("bid") or 0.0)
    ask = float(quote.get("ask") or 0.0)
    if bid > 0 and ask > 0 and ask > bid:
        return round((bid + ask) / 2.0, 2)

    # Some vendors expose pre-market as "pre_market" child dict
    pm = quote.get("pre_market") or quote.get("extended_hours") or {}
    if isinstance(pm, dict):
        pbid = float(pm.get("bid") or 0.0)
        pask = float(pm.get("ask") or 0.0)
        if pbid > 0 and pask > 0 and pask > pbid:
            return round((pbid + pask) / 2.0, 2)

    return None
