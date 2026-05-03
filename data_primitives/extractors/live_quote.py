"""Real-time last-price feed for the readiness checker.

The default ``_default_get_price`` in ``execution_engine.trade_readiness``
reads the freshest close from local OHLCV/intraday jsonl files. Those
files only refresh during ingestion cycles, so signals booked weeks
ago show stale prices forever — the May 3 console showed
``CURRENT $63,121.64`` for BTC while live BTC was $78K.

This module is the live alternative. It uses ``yfinance`` (already a
project dependency, no API key) to pull a near-real-time
``fast_info.last_price`` for any ticker, with asset-class-aware Yahoo
symbol mapping and a small TTL cache so 7 readiness ticks in a 30-second
window only hit the wire once per ticker.

Falls back to ``None`` on any failure — the caller is expected to chain
to the historical-close path so a yfinance 429 / DNS blip / missing
symbol never breaks the readiness loop.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("ayvid.live_quote")

# 30s — short enough to feel "live" on the dashboard, long enough that
# 7 simultaneous lookups in a checker tick coalesce into 1 wire call
# per ticker. The 5-second SSE refresh on the frontend therefore reads
# from cache 5 of every 6 ticks, which is the right balance for a free
# upstream that rate-limits aggressively.
_QUOTE_CACHE_TTL_S = 30.0

# (symbol, epoch_seconds, price) — write-through cache, no eviction
# bound (worst case ~hundreds of tickers per process is fine).
_quote_cache: dict[str, tuple[float, float]] = {}

# Futures suffix map: AYVID stores the root symbol (GC, NQ, …) so the
# Yahoo "=F" suffix has to be re-attached at fetch time. The mapping
# already lives in ``openbb_client.fetch_commodity_historical``; we
# duplicate the few that are actually traded so we don't import a
# heavy module at quote time.
_FUTURES_TO_YAHOO: dict[str, str] = {
    "GC": "GC=F",
    "SI": "SI=F",
    "CL": "CL=F",
    "BZ": "BZ=F",
    "NG": "NG=F",
    "HG": "HG=F",
    "PL": "PL=F",
    "PA": "PA=F",
    "ZC": "ZC=F",
    "ZS": "ZS=F",
    "ZW": "ZW=F",
    "CT": "CT=F",
    "KC": "KC=F",
    "SB": "SB=F",
    "CC": "CC=F",
    "ES": "ES=F",
    "NQ": "NQ=F",
    "YM": "YM=F",
    "RTY": "RTY=F",
    "ZB": "ZB=F",
    "ZN": "ZN=F",
    "ZF": "ZF=F",
    "ZT": "ZT=F",
}


def to_yahoo_symbol(ticker: str, asset_class: str) -> str:
    """Map AYVID's (ticker, asset_class) tuple onto the Yahoo Finance
    symbol convention used by yfinance.

    EQUITY/ETF/INDEX → ticker passed through (Yahoo's default form).
    CRYPTO           → ``BTC`` becomes ``BTC-USD`` (Yahoo crypto pair).
    FX               → ``USDJPY`` becomes ``USDJPY=X`` (Yahoo fx pair).
    FUTURE/COMMODITY → ``GC`` becomes ``GC=F`` via the explicit map,
                       falling back to ``{ticker}=F`` for symbols we
                       don't have an explicit row for.

    Already-Yahoo-shaped tickers (``BTC-USD``, ``USDJPY=X``, ``GC=F``)
    are left alone — idempotency is important because the caller may
    pass either shape depending on whether the pipeline normalised
    upstream.
    """
    t = (ticker or "").strip().upper()
    ac = (asset_class or "").strip().upper()
    if not t:
        return t
    if ac == "CRYPTO":
        return t if "-USD" in t else f"{t}-USD"
    if ac == "FX":
        return t if "=" in t else f"{t}=X"
    if ac in {"FUTURE", "COMMODITY"}:
        if "=" in t:
            return t
        return _FUTURES_TO_YAHOO.get(t, f"{t}=F")
    return t


def _yf_lookup(symbol: str) -> float | None:
    """Synchronous yfinance call. Runs on a worker thread so it never
    blocks the asyncio loop. Wrapped in a broad except — yfinance
    surfaces dozens of distinct exception classes (HTTPError, KeyError
    on missing ``last_price``, JSONDecodeError on a captcha page) and
    we treat all of them identically: ``None`` so the caller falls
    back to the historical close."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        # ``fast_info`` is the cheap path — a single request to the
        # quoteSummary endpoint, vs ``info`` which does a dozen.
        price = float(ticker.fast_info.last_price)
    except Exception as exc:
        log.debug(f"yf_lookup_failed symbol={symbol}: {exc}")
        return None
    return price if price > 0 else None


async def fetch_live_quote(ticker: str, asset_class: str) -> float | None:
    """Return the freshest last-price for a (ticker, asset_class).

    The signature matches ``trade_readiness.PriceFn`` exactly so this
    can be passed straight into ``TradeReadinessChecker(get_price=…)``.

    Returns ``None`` on any failure. Never raises.
    """
    sym = to_yahoo_symbol(ticker, asset_class)
    if not sym:
        return None
    now = time.time()
    cached = _quote_cache.get(sym)
    if cached and now - cached[0] < _QUOTE_CACHE_TTL_S:
        return cached[1]

    price = await asyncio.to_thread(_yf_lookup, sym)
    if price is None or price <= 0:
        # Defence in depth — _yf_lookup already filters non-positive
        # quotes, but a zero leaking into the readiness checker would
        # mark every signal WAITING with reason="price_unavailable",
        # then flip on the next non-zero quote, churning SignalBook
        # persistence.
        return None
    _quote_cache[sym] = (now, price)
    return price


def _reset_cache_for_tests() -> None:
    """Test hook only — clears the in-process cache so unit tests don't
    leak quote state across cases."""
    _quote_cache.clear()
