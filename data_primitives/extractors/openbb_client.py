"""OpenBB SDK client for fetching market data, news, filings, and macro indicators.

This is the foundational raw data fabric — everything flows from here.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import pandas as pd
from openbb import obb
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from shared.config import settings
from shared.exceptions import OpenBBFetchError
from shared.logger import get_logger
from shared.types import FuturesQuote, NewsItem, OHLCVBar, OptionsChainSnapshot

log = get_logger("data.openbb")

# ── Credential injection ─────────────────────────────────────

_creds_injected = False


def _ensure_credentials() -> None:
    """Lazily inject API keys into OpenBB right before the first call.

    OpenBB's internal init can overwrite credentials set at module load time,
    so we defer injection and re-apply before each provider call.
    """
    global _creds_injected
    if _creds_injected:
        return
    _cred_map = {
        "fmp_api_key": settings.OPENBB_FMP_API_KEY,
        "fred_api_key": settings.OPENBB_FRED_API_KEY,
        "openbb_pat": settings.OPENBB_PAT,
        "benzinga_api_key": settings.OPENBB_BENZINGA_API_KEY,
        "tiingo_token": settings.OPENBB_TIINGO_API_KEY,
        "intrinio_api_key": settings.OPENBB_INTRINIO_API_KEY,
    }
    for attr, val in _cred_map.items():
        if val:
            setattr(obb.user.credentials, attr, val)
    _creds_injected = True


async def _obb_call(func, **kwargs):
    """Wrapper that ensures credentials are injected before every OpenBB call."""
    _ensure_credentials()
    return await asyncio.to_thread(func, **kwargs)


# ── Price Data ────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((OpenBBFetchError, ConnectionError, TimeoutError)),
    reraise=True,
)
async def fetch_ohlcv(
    symbol: str,
    days: int = 90,
    provider: str = "yfinance",
    interval: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[OHLCVBar]:
    """Fetch historical OHLCV bars for a symbol.

    Args:
        days: Rolling lookback window from today. Ignored when start_date
              is provided.
        interval: Bar interval — "1m", "5m", "15m", "30m", "1h", "1d", "1wk".
                  None defaults to daily. Intraday intervals limited to ~60 days
                  of history on most providers.
        start_date: Explicit start date (ISO format "2021-03-20"). When set,
                    ``days`` is ignored and the explicit range is used instead.
        end_date: Explicit end date (ISO format "2021-04-15"). Defaults to
                  today when start_date is set but end_date is omitted.
    """
    try:
        if start_date:
            start = start_date
        else:
            start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        kwargs: dict[str, Any] = {
            "symbol": symbol,
            "provider": provider,
            "start_date": start,
        }
        if end_date:
            kwargs["end_date"] = end_date
        if interval:
            kwargs["interval"] = interval

        result = await _obb_call(
            obb.equity.price.historical,
            **kwargs,
        )
        df = result.to_df().reset_index()
        bars: list[OHLCVBar] = []
        for _, row in df.iterrows():
            bars.append(OHLCVBar(
                timestamp=pd.Timestamp(row.get("date", row.name)).to_pydatetime(),
                symbol=symbol,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
            ))
        tf_label = f" ({interval})" if interval else ""
        log.info(f"Fetched {len(bars)} OHLCV bars{tf_label} for [bold]{symbol}[/bold]")
        return bars
    except Exception as exc:
        raise OpenBBFetchError(f"OHLCV fetch failed for {symbol}: {exc}") from exc


async def fetch_intraday(
    symbol: str,
    interval: str = "5m",
    days: int = 5,
) -> list[OHLCVBar]:
    """Fetch intraday OHLCV bars via yfinance.

    Supported intervals: "1m" (7 days max), "5m", "15m", "30m", "1h" (60 days max).
    """
    max_days = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "1h": 730}
    days = min(days, max_days.get(interval, 60))
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = await asyncio.to_thread(
            ticker.history, period=f"{days}d", interval=interval,
        )
        if df.empty:
            return []
        bars: list[OHLCVBar] = []
        for ts, row in df.iterrows():
            bars.append(OHLCVBar(
                timestamp=pd.Timestamp(ts).to_pydatetime(),
                symbol=symbol,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
            ))
        log.info(f"Fetched {len(bars)} intraday {interval} bars for {symbol}")
        return bars
    except Exception as exc:
        log.warning(f"Intraday fetch failed for {symbol}: {exc}")
        return []


async def fetch_intraday_batch(
    symbols: list[str],
    interval: str = "5m",
    days: int = 5,
) -> dict[str, list[OHLCVBar]]:
    """Fetch intraday bars for multiple symbols with controlled concurrency."""
    semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def _one(sym: str) -> tuple[str, list[OHLCVBar]]:
        async with semaphore:
            try:
                return sym, await fetch_intraday(sym, interval=interval, days=days)
            except Exception as exc:
                log.warning(f"Intraday batch: {sym} failed: {exc}")
                return sym, []

    results = await asyncio.gather(*[_one(s) for s in symbols])
    output: dict[str, list[OHLCVBar]] = {}
    for sym, bars in results:
        if bars:
            output[sym] = bars
    log.info(f"Intraday batch: {len(output)}/{len(symbols)} succeeded ({interval}, {days}d)")
    return output


async def fetch_index_options_data(
    index_symbol: str,
    etf_proxy: str | None = None,
) -> dict[str, Any]:
    """Fetch options data for a global index via its ETF proxy.

    Most indices don't have directly tradeable options data via yfinance,
    so we use ETF proxies (SPY for SPX, QQQ for NDX, EWG for DAX, etc.).
    """
    from graph_intelligence.tools.dynamic_universe import GLOBAL_INDEX_DERIVATIVES

    idx_info = GLOBAL_INDEX_DERIVATIVES.get(index_symbol, {})
    proxy = etf_proxy or idx_info.get("etf", index_symbol)

    chain = await fetch_options_chain(proxy)
    ohlcv = await fetch_ohlcv(idx_info.get("yahoo", proxy), days=60)

    return {
        "index": index_symbol,
        "etf_proxy": proxy,
        "options_count": len(chain),
        "price_bars": len(ohlcv),
        "chain": chain,
        "ohlcv": ohlcv,
        "contract_specs": idx_info,
    }


async def fetch_current_price(symbol: str) -> float:
    """Fetch the latest closing price for a symbol."""
    bars = await fetch_ohlcv(symbol, days=5)
    if not bars:
        raise OpenBBFetchError(f"No price data for {symbol}")
    return bars[-1].close


# ── Corporate News ────────────────────────────────────────────

async def fetch_company_news(
    symbol: str,
    limit: int = 20,
    provider: str = "yfinance",
) -> list[NewsItem]:
    """Fetch recent news articles for a company."""
    try:
        result = await _obb_call(
            obb.news.company,
            symbol=symbol,
            provider=provider,
            limit=limit,
        )
        df = result.to_df().reset_index()
        items: list[NewsItem] = []
        for _, row in df.iterrows():
            items.append(NewsItem(
                timestamp=pd.Timestamp(row.get("date", row.get("published", dt.datetime.now(dt.timezone.utc)))).to_pydatetime(),
                title=str(row.get("title", "")),
                source=str(row.get("source", provider)),
                symbols=[symbol],
                text=str(row.get("text", row.get("description", ""))),
                url=str(row.get("url", row.get("link", ""))),
            ))
        log.info(f"Fetched {len(items)} news items for [bold]{symbol}[/bold]")
        return items
    except Exception as exc:
        raise OpenBBFetchError(f"News fetch failed for {symbol}: {exc}") from exc


# ── SEC Filings ───────────────────────────────────────────────

async def fetch_sec_filings(
    symbol: str,
    limit: int = 10,
    provider: str = "fmp",
) -> list[dict[str, Any]]:
    """Fetch recent SEC filings (10-K, 8-K, etc.)."""
    try:
        result = await _obb_call(
            obb.equity.fundamental.filings,
            symbol=symbol,
            provider=provider,
            limit=limit,
        )
        records = result.to_df().to_dict("records")
        log.info(f"Fetched {len(records)} SEC filings for [bold]{symbol}[/bold]")
        return records
    except Exception as exc:
        raise OpenBBFetchError(f"SEC filings fetch failed for {symbol}: {exc}") from exc


# ── Fundamental Ratios ────────────────────────────────────────

async def fetch_financial_ratios(
    symbol: str,
    provider: str = "fmp",
) -> dict[str, Any]:
    """Fetch key financial ratios for a company."""
    try:
        result = await _obb_call(
            obb.equity.fundamental.ratios,
            symbol=symbol,
            provider=provider,
            limit=4,
        )
        df = result.to_df()
        if df.empty:
            return {}
        return df.iloc[0].to_dict()
    except Exception as exc:
        log.warning(f"Ratios fetch failed for {symbol}: {exc}")
        return {}


# ── Macro Calendar ────────────────────────────────────────────

async def fetch_economic_calendar(
    days_ahead: int = 30,
) -> list[dict[str, Any]]:
    """Fetch upcoming macroeconomic events (tries FMP, FRED, then earnings as proxy)."""
    today = dt.date.today().isoformat()
    end = (dt.date.today() + dt.timedelta(days=days_ahead)).isoformat()

    for provider in ("fmp", "fred"):
        try:
            result = await _obb_call(
                obb.economy.calendar,
                provider=provider,
                start_date=today,
                end_date=end,
            )
            records = result.to_df().to_dict("records")
            if records:
                log.info(f"Fetched {len(records)} macro events via {provider}")
                return records
        except Exception:
            continue

    # Fallback: build a proxy calendar from earnings + FOMC data
    events: list[dict[str, Any]] = []
    try:
        earnings = await fetch_earnings_calendar(days_ahead=days_ahead)
        for e in earnings[:30]:
            events.append({
                "date": e.get("report_date", ""),
                "event": f"{e.get('symbol', '?')} Earnings",
                "country": "US",
                "category": "earnings",
            })
    except Exception:
        pass

    if events:
        log.info(f"Built proxy calendar with {len(events)} events (earnings)")
    else:
        log.warning("Macro calendar fetch failed from all providers")
    return events


# ── Supply Chain / Company Search ─────────────────────────────

async def search_companies(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search for companies by name or ticker."""
    try:
        result = await _obb_call(
            obb.equity.search,
            query=query,
            limit=limit,
        )
        return result.to_df().to_dict("records")
    except Exception as exc:
        log.warning(f"Company search failed for '{query}': {exc}")
        return []


# ── Multi-Symbol Batch ────────────────────────────────────────

_BATCH_CONCURRENCY = 5


def _cleanup_yfinance_cache() -> None:
    """Close stale yfinance/sqlite3 connections to prevent fd leaks."""
    import gc
    import sqlite3
    gc.collect()
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.close()
            except Exception:
                pass


async def fetch_multi_ohlcv(
    symbols: list[str],
    days: int = 90,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, list[OHLCVBar]]:
    """Fetch OHLCV data for multiple symbols with controlled concurrency."""
    semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def _fetch_one(sym: str) -> tuple[str, list[OHLCVBar] | Exception]:
        async with semaphore:
            try:
                bars = await fetch_ohlcv(
                    sym, days, start_date=start_date, end_date=end_date,
                )
                return sym, bars
            except Exception as exc:
                return sym, exc

    tasks = [_fetch_one(sym) for sym in symbols]
    results = await asyncio.gather(*tasks)

    output: dict[str, list[OHLCVBar]] = {}
    failed = 0
    for sym, res in results:
        if isinstance(res, Exception):
            failed += 1
            continue
        output[sym] = res

    log.info(f"Batch fetch: {len(output)}/{len(symbols)} succeeded, {failed} failed")
    await asyncio.to_thread(_cleanup_yfinance_cache)
    return output


# ── Global Index Data ─────────────────────────────────────────

def _df_to_bars(df: pd.DataFrame, symbol: str) -> list[OHLCVBar]:
    """Convert a price DataFrame to OHLCVBar list, dropping rows with NaN close."""
    import math

    df = df.reset_index()
    bars: list[OHLCVBar] = []
    for _, row in df.iterrows():
        close_val = float(row.get("close", 0))
        if math.isnan(close_val):
            continue
        bars.append(OHLCVBar(
            timestamp=pd.Timestamp(row.get("date", row.name)).to_pydatetime(),
            symbol=symbol,
            open=float(row.get("open", 0)),
            high=float(row.get("high", 0)),
            low=float(row.get("low", 0)),
            close=close_val,
            volume=int(row.get("volume", 0)),
        ))
    return bars


async def fetch_index_historical(
    symbol: str,
    days: int = 90,
    provider: str = "yfinance",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[OHLCVBar]:
    """Fetch historical data for a market index (^GSPC, ^FTSE, ^N225 etc.).

    Falls back to obb.equity.price.historical when the index endpoint
    returns empty (some symbols like ^SSMI only work via equity).
    """
    start = start_date or (dt.date.today() - dt.timedelta(days=days)).isoformat()
    kwargs: dict[str, Any] = {
        "symbol": symbol,
        "provider": provider,
        "start_date": start,
    }
    if end_date:
        kwargs["end_date"] = end_date

    for endpoint in (obb.index.price.historical, obb.equity.price.historical):
        try:
            result = await _obb_call(endpoint, **kwargs)
            df = result.to_df()
            if df.empty:
                continue
            bars = _df_to_bars(df, symbol)
            log.info(f"Fetched {len(bars)} index bars for [bold]{symbol}[/bold]")
            return bars
        except Exception:
            continue

    log.warning(f"Index fetch failed for {symbol}: no data from any endpoint")
    return []


# ── Forex Data ────────────────────────────────────────────────

async def fetch_fx_historical(
    pair: str,
    days: int = 90,
    provider: str = "yfinance",
) -> list[OHLCVBar]:
    """Fetch historical FX rates. pair format: 'EURUSD' → yahoo 'EURUSD=X'."""
    try:
        start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        result = await _obb_call(
            obb.currency.price.historical,
            symbol=pair,
            provider=provider,
            start_date=start,
        )
        bars = _df_to_bars(result.to_df(), pair)
        log.info(f"Fetched {len(bars)} FX bars for [bold]{pair}[/bold]")
        return bars
    except Exception as exc:
        log.warning(f"FX fetch failed for {pair}: {exc}")
        return []


# ── Crypto Data ───────────────────────────────────────────────

async def fetch_crypto_historical(
    symbol: str,
    days: int = 90,
    provider: str = "yfinance",
) -> list[OHLCVBar]:
    """Fetch historical crypto price data."""
    yahoo_sym = f"{symbol}-USD"
    try:
        start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        result = await _obb_call(
            obb.crypto.price.historical,
            symbol=yahoo_sym,
            provider=provider,
            start_date=start,
        )
        bars = _df_to_bars(result.to_df(), symbol)
        log.info(f"Fetched {len(bars)} crypto bars for [bold]{symbol}[/bold]")
        return bars
    except Exception as exc:
        log.warning(f"Crypto fetch failed for {symbol}: {exc}")
        return []


# ── ETF Data ──────────────────────────────────────────────────

async def fetch_etf_info(symbol: str, provider: str = "yfinance") -> dict[str, Any]:
    """Fetch ETF metadata and holdings."""
    try:
        result = await _obb_call(
            obb.etf.info,
            symbol=symbol,
            provider=provider,
        )
        df = result.to_df()
        if df.empty:
            return {}
        return df.iloc[0].to_dict()
    except Exception as exc:
        log.warning(f"ETF info failed for {symbol}: {exc}")
        return {}


# ── Commodity / Futures Data ──────────────────────────────────

async def fetch_commodity_historical(
    symbol: str,
    days: int = 90,
) -> list[OHLCVBar]:
    """Fetch commodity futures historical data via yfinance convention.

    Maps common futures symbols to yfinance tickers (CL=F, GC=F, etc.).
    """
    yahoo_map = {
        "CL": "CL=F", "BZ": "BZ=F", "NG": "NG=F",
        "GC": "GC=F", "SI": "SI=F", "HG": "HG=F",
        "PL": "PL=F", "PA": "PA=F",
        "ZC": "ZC=F", "ZS": "ZS=F", "ZW": "ZW=F",
        "CT": "CT=F", "KC": "KC=F", "SB": "SB=F", "CC": "CC=F",
        "ES": "ES=F", "NQ": "NQ=F", "YM": "YM=F", "RTY": "RTY=F",
        "ZB": "ZB=F", "ZN": "ZN=F", "ZF": "ZF=F", "ZT": "ZT=F",
    }
    yahoo_sym = yahoo_map.get(symbol, f"{symbol}=F")
    return await fetch_ohlcv(yahoo_sym, days=days)


# ── World News (Multi-source) ────────────────────────────────

_WORLD_NEWS_PROVIDERS = ["benzinga", "tiingo", "intrinio", "fmp"]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    reraise=True,
)
async def fetch_world_news(limit: int = 50) -> list[NewsItem]:
    """Fetch global financial news across all markets.

    Tries providers in priority order until one succeeds.
    """
    for provider in _WORLD_NEWS_PROVIDERS:
        try:
            result = await _obb_call(
                obb.news.world,
                provider=provider,
                limit=limit,
            )
            df = result.to_df().reset_index()
            items: list[NewsItem] = []
            for _, row in df.iterrows():
                symbols_raw = row.get("symbols", row.get("tickers", ""))
                if isinstance(symbols_raw, str):
                    symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]
                elif isinstance(symbols_raw, list):
                    symbols = symbols_raw
                else:
                    symbols = []

                items.append(NewsItem(
                    timestamp=pd.Timestamp(
                        row.get("date", row.get("published", dt.datetime.now(dt.timezone.utc)))
                    ).to_pydatetime(),
                    title=str(row.get("title", "")),
                    source=str(row.get("source", provider)),
                    symbols=symbols,
                    text=str(row.get("text", row.get("description", ""))),
                    url=str(row.get("url", row.get("link", ""))),
                ))
            log.info(f"Fetched {len(items)} global news items (provider={provider})")
            return items
        except Exception as exc:
            log.warning(f"World news provider {provider} failed: {exc}")
            continue
    log.warning("All world news providers exhausted — returning empty")
    return []


# ── Sector Performance ────────────────────────────────────────

_SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLV": "Healthcare",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLC": "Communication Services",
}


async def fetch_sector_performance() -> list[dict[str, Any]]:
    """Fetch US sector performance via SPDR sector ETFs."""
    try:
        import math

        results: list[dict[str, Any]] = []
        for etf, sector_name in _SECTOR_ETFS.items():
            bars = await fetch_ohlcv(etf, days=30)
            valid = [b for b in bars if not math.isnan(b.close)]
            if valid and len(valid) >= 2:
                chg_1d = (valid[-1].close / valid[-2].close - 1) * 100
                chg_period = (valid[-1].close / valid[0].close - 1) * 100
                results.append({
                    "sector": sector_name,
                    "etf": etf,
                    "change_1d": round(chg_1d, 2),
                    "change_30d": round(chg_period, 2),
                    "last": round(valid[-1].close, 2),
                })
        results.sort(key=lambda r: r.get("change_1d", 0), reverse=True)
        return results
    except Exception as exc:
        log.warning(f"Sector performance fetch failed: {exc}")
        return []


# ── Earnings Calendar ─────────────────────────────────────────

async def fetch_earnings_calendar(days_ahead: int = 7) -> list[dict[str, Any]]:
    """Fetch upcoming earnings announcements."""
    try:
        today = dt.date.today().isoformat()
        end = (dt.date.today() + dt.timedelta(days=days_ahead)).isoformat()
        result = await _obb_call(
            obb.equity.calendar.earnings,
            provider="fmp",
            start_date=today,
            end_date=end,
        )
        records = result.to_df().to_dict("records")
        log.info(f"Fetched {len(records)} upcoming earnings")
        return records
    except Exception as exc:
        log.warning(f"Earnings calendar failed: {exc}")
        return []


# ── IPO Calendar ──────────────────────────────────────────────

async def fetch_ipo_calendar() -> list[dict[str, Any]]:
    """Fetch upcoming and recent IPOs."""
    try:
        result = await _obb_call(
            obb.equity.calendar.ipo,
            provider="fmp",
        )
        return result.to_df().to_dict("records")
    except Exception as exc:
        log.warning(f"IPO calendar failed: {exc}")
        return []


# ── Treasury / Rates ──────────────────────────────────────────

async def fetch_treasury_rates() -> list[dict[str, Any]]:
    """Fetch current US Treasury yield curve."""
    try:
        result = await _obb_call(
            obb.fixedincome.rate.treasury,
            provider="fmp",
        )
        return result.to_df().to_dict("records")
    except Exception as exc:
        log.warning(f"Treasury rates fetch failed: {exc}")
        return []


# ── Options & Derivatives ────────────────────────────────────


async def fetch_options_chain(
    symbol: str,
    provider: str = "intrinio",
) -> list[OptionsChainSnapshot]:
    """Fetch the full options chain for a symbol.

    Tries OpenBB derivatives first, falls back to yfinance if unavailable.
    """
    snapshots: list[OptionsChainSnapshot] = []

    try:
        result = await _obb_call(
            obb.derivatives.options.chains,
            symbol=symbol,
            provider=provider,
        )
        df = result.to_df()
        for _, row in df.iterrows():
            snapshots.append(OptionsChainSnapshot(
                underlying=symbol,
                expiry=pd.Timestamp(row.get("expiration", row.get("expiry", dt.date.today()))).date(),
                strike=float(row.get("strike", 0)),
                right="CALL" if str(row.get("option_type", "call")).upper().startswith("C") else "PUT",
                bid=float(row.get("bid", 0)),
                ask=float(row.get("ask", 0)),
                last=float(row.get("last_price", row.get("close", 0))),
                volume=int(row.get("volume", 0)),
                open_interest=int(row.get("open_interest", 0)),
                implied_volatility=float(row.get("implied_volatility", 0)),
                delta=float(row.get("delta", 0)),
                gamma=float(row.get("gamma", 0)),
                theta=float(row.get("theta", 0)),
                vega=float(row.get("vega", 0)),
            ))
        log.info(f"Fetched {len(snapshots)} options for {symbol}")
    except Exception as exc:
        log.warning(f"Options chain fetch failed for {symbol}: {exc}")
        snapshots = await _fetch_options_yfinance(symbol)

    return snapshots


async def _fetch_options_yfinance(symbol: str) -> list[OptionsChainSnapshot]:
    """Fallback: fetch options via yfinance when OpenBB derivatives unavailable."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        expiries = await asyncio.to_thread(lambda: ticker.options)
        if not expiries:
            return []

        snapshots: list[OptionsChainSnapshot] = []
        for exp_str in expiries[:4]:
            chain = await asyncio.to_thread(ticker.option_chain, exp_str)
            exp_date = dt.datetime.strptime(exp_str, "%Y-%m-%d").date()
            for _, row in chain.calls.iterrows():
                snapshots.append(OptionsChainSnapshot(
                    underlying=symbol, expiry=exp_date, strike=float(row["strike"]),
                    right="CALL", bid=float(row.get("bid", 0)), ask=float(row.get("ask", 0)),
                    last=float(row.get("lastPrice", 0)), volume=int(row.get("volume", 0) or 0),
                    open_interest=int(row.get("openInterest", 0) or 0),
                    implied_volatility=float(row.get("impliedVolatility", 0) or 0),
                ))
            for _, row in chain.puts.iterrows():
                snapshots.append(OptionsChainSnapshot(
                    underlying=symbol, expiry=exp_date, strike=float(row["strike"]),
                    right="PUT", bid=float(row.get("bid", 0)), ask=float(row.get("ask", 0)),
                    last=float(row.get("lastPrice", 0)), volume=int(row.get("volume", 0) or 0),
                    open_interest=int(row.get("openInterest", 0) or 0),
                    implied_volatility=float(row.get("impliedVolatility", 0) or 0),
                ))
        log.info(f"Fetched {len(snapshots)} options for {symbol} via yfinance")
        return snapshots
    except Exception as exc:
        log.warning(f"yfinance options fallback failed for {symbol}: {exc}")
        return []


async def fetch_futures_chain(symbol: str) -> list[FuturesQuote]:
    """Fetch active futures contracts for a commodity/index symbol."""
    FUTURES_MAP: dict[str, tuple[str, str, float]] = {
        "ES": ("ES=F", "CME", 50.0),
        "NQ": ("NQ=F", "CME", 20.0),
        "YM": ("YM=F", "CBOT", 5.0),
        "RTY": ("RTY=F", "CME", 50.0),
        "CL": ("CL=F", "NYMEX", 1000.0),
        "GC": ("GC=F", "COMEX", 100.0),
        "SI": ("SI=F", "COMEX", 5000.0),
        "NG": ("NG=F", "NYMEX", 10000.0),
        "ZB": ("ZB=F", "CBOT", 1000.0),
        "ZN": ("ZN=F", "CBOT", 1000.0),
        "ZC": ("ZC=F", "CBOT", 50.0),
        "ZS": ("ZS=F", "CBOT", 50.0),
        "ZW": ("ZW=F", "CBOT", 50.0),
        "HG": ("HG=F", "COMEX", 25000.0),
        "6E": ("EURUSD=X", "CME", 125000.0),
        "6J": ("JPY=X", "CME", 12500000.0),
    }

    yahoo_sym, exchange, multiplier = FUTURES_MAP.get(symbol, (f"{symbol}=F", "CME", 1.0))
    quotes: list[FuturesQuote] = []
    try:
        bars = await fetch_ohlcv(yahoo_sym, days=5)
        if bars:
            last_bar = bars[-1]
            today = dt.date.today()
            month = today.month
            year = today.year
            front_expiry = dt.date(year, month, 21)
            if front_expiry <= today:
                month += 1
                if month > 12:
                    month, year = 1, year + 1
                front_expiry = dt.date(year, month, 21)

            quotes.append(FuturesQuote(
                symbol=symbol, underlying=symbol, exchange=exchange,
                expiry=front_expiry, multiplier=multiplier,
                last_price=last_bar.close, volume=last_bar.volume,
                currency="USD",
            ))
        log.info(f"Fetched futures quote for {symbol}")
    except Exception as exc:
        log.warning(f"Futures chain fetch failed for {symbol}: {exc}")

    return quotes


async def fetch_vix_term_structure() -> list[dict[str, Any]]:
    """Fetch VIX futures term structure for volatility regime detection."""
    vix_contracts = ["^VIX", "^VIX3M"]
    results: list[dict[str, Any]] = []
    try:
        for sym in vix_contracts:
            bars = await fetch_ohlcv(sym, days=30)
            if bars:
                results.append({
                    "symbol": sym,
                    "last": bars[-1].close,
                    "high_30d": max(b.high for b in bars),
                    "low_30d": min(b.low for b in bars),
                    "avg_30d": sum(b.close for b in bars) / len(bars),
                    "bars": len(bars),
                })
        if len(results) >= 2:
            spot = results[0]["last"]
            three_m = results[1]["last"]
            results.append({
                "contango": three_m > spot,
                "term_spread": three_m - spot,
                "term_spread_pct": (three_m / spot - 1) * 100 if spot > 0 else 0,
            })
    except Exception as exc:
        log.warning(f"VIX term structure fetch failed: {exc}")
    return results
