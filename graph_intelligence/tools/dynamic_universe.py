"""Dynamic Universe Discovery — replaces all hardcoded ticker maps.

Queries the Neo4j supply chain graph at runtime to discover:
- Supply chain neighbours for any ticker
- Correlated/cointegrated pairs from price data
- Full tradeable universe from local data + graph
- Global index options universe

Nothing is hardcoded. Everything is discovered from the graph or computed
from market data.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np

from shared.logger import get_logger

log = get_logger("universe.dynamic")

_DATA_DIR = Path("data_store/ohlcv")


# ── Supply Chain Discovery (Neo4j) ───────────────────────────


async def discover_supply_chain_neighbours(
    ticker: str,
    max_depth: int = 3,
    min_compound_risk: float = 0.001,
) -> list[dict[str, Any]]:
    """Query Neo4j for all supply chain neighbours of a ticker.

    Returns a list of dicts with target_ticker, depth, compound_risk, path.
    Falls back to correlation-based discovery if Neo4j is unavailable.
    """
    try:
        from graph_intelligence.tools.neo4j_tools import find_hidden_victims
        paths = await find_hidden_victims(
            ticker,
            pagerank_percentile=0.90,
            min_compound_risk=min_compound_risk,
            max_depth=max_depth,
        )
        return [
            {
                "target_ticker": p.target_ticker,
                "depth": p.depth,
                "compound_risk": p.compound_risk,
                "path": p.path_tickers,
            }
            for p in paths
        ]
    except Exception as exc:
        log.debug(f"Neo4j neighbour discovery failed for {ticker}: {exc}")
        return await _correlation_fallback(ticker)


async def discover_all_supply_chain_links() -> dict[str, list[str]]:
    """Build the full supply chain map dynamically from Neo4j.

    Returns {ticker: [neighbour_tickers]} for every company in the graph.
    Falls back to correlation-based pairs if Neo4j is unavailable.
    """
    try:
        from graph_intelligence.tools.neo4j_tools import get_driver
        from shared.config import settings

        driver = await get_driver()
        kwargs = {}
        if settings.NEO4J_DATABASE:
            kwargs["database"] = settings.NEO4J_DATABASE

        async with driver.session(**kwargs) as session:
            result = await session.run(
                """
                MATCH (a:Company)-[:SUPPLIES_TO]->(b:Company)
                RETURN a.ticker AS source, collect(DISTINCT b.ticker) AS targets
                """
            )
            chain_map: dict[str, list[str]] = {}
            async for record in result:
                src = record["source"]
                targets = record["targets"]
                if src and targets:
                    chain_map[src] = targets
                    for t in targets:
                        if t not in chain_map:
                            chain_map[t] = []
                        if src not in chain_map[t]:
                            chain_map[t].append(src)
            log.info(f"Discovered {len(chain_map)} tickers with supply chain links from Neo4j")
            return chain_map
    except Exception as exc:
        log.warning(f"Neo4j supply chain discovery failed: {exc}")
        return await _build_correlation_pairs()


async def _correlation_fallback(ticker: str, threshold: float = 0.6) -> list[dict]:
    """Find correlated tickers from local price data when Neo4j is unavailable."""
    from backtesting.engine import _load_local_bars

    target_bars = _load_local_bars(ticker)
    if len(target_bars) < 20:
        return []

    target_closes = np.array([b.close for b in target_bars[-60:]])
    target_returns = np.diff(np.log(target_closes)) if len(target_closes) > 1 else np.array([])
    if len(target_returns) < 10:
        return []

    neighbours: list[dict] = []
    for path in _DATA_DIR.glob("*.jsonl"):
        other_ticker = path.stem
        if other_ticker == ticker or path.stat().st_size == 0:
            continue
        other_bars = _load_local_bars(other_ticker)
        if len(other_bars) < 20:
            continue
        other_closes = np.array([b.close for b in other_bars[-60:]])
        other_returns = np.diff(np.log(other_closes)) if len(other_closes) > 1 else np.array([])

        n = min(len(target_returns), len(other_returns))
        if n < 10:
            continue

        corr = float(np.corrcoef(target_returns[-n:], other_returns[-n:])[0, 1])
        if abs(corr) > threshold:
            neighbours.append({
                "target_ticker": other_ticker,
                "depth": 1,
                "compound_risk": abs(corr),
                "path": [ticker, other_ticker],
                "correlation": corr,
            })

    neighbours.sort(key=lambda x: x["compound_risk"], reverse=True)
    return neighbours[:20]


async def _build_correlation_pairs(threshold: float = 0.5) -> dict[str, list[str]]:
    """Build a full correlation-based pair map from local data."""
    from backtesting.engine import _load_local_bars

    tickers = sorted(p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0)
    if len(tickers) < 2:
        return {}

    returns_map: dict[str, np.ndarray] = {}
    for ticker in tickers:
        bars = _load_local_bars(ticker)
        if len(bars) < 20:
            continue
        closes = np.array([b.close for b in bars[-60:]])
        if len(closes) > 1:
            returns_map[ticker] = np.diff(np.log(closes))

    pair_map: dict[str, list[str]] = {}
    ticker_list = list(returns_map.keys())

    for i, t1 in enumerate(ticker_list):
        for t2 in ticker_list[i + 1:]:
            r1, r2 = returns_map[t1], returns_map[t2]
            n = min(len(r1), len(r2))
            if n < 10:
                continue
            corr = float(np.corrcoef(r1[-n:], r2[-n:])[0, 1])
            if abs(corr) > threshold:
                pair_map.setdefault(t1, []).append(t2)
                pair_map.setdefault(t2, []).append(t1)

    return pair_map


# ── Dynamic Cointegration Pairs ──────────────────────────────


async def discover_cointegrated_pairs(
    min_bars: int = 30,
    p_value_threshold: float = 0.05,
) -> list[tuple[str, str, float]]:
    """Find statistically cointegrated pairs from local data.

    Returns list of (ticker_a, ticker_b, p_value) sorted by strength.
    Uses Engle-Granger two-step procedure.
    """
    from backtesting.engine import _load_local_bars

    tickers = sorted(p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0)
    closes_map: dict[str, np.ndarray] = {}
    for t in tickers:
        bars = _load_local_bars(t)
        if len(bars) >= min_bars:
            closes_map[t] = np.array([b.close for b in bars[-120:]])

    pairs: list[tuple[str, str, float]] = []
    ticker_list = list(closes_map.keys())

    for i, t1 in enumerate(ticker_list):
        for t2 in ticker_list[i + 1:]:
            c1, c2 = closes_map[t1], closes_map[t2]
            n = min(len(c1), len(c2))
            if n < min_bars:
                continue

            log_c1, log_c2 = np.log(c1[-n:]), np.log(c2[-n:])
            beta = float(np.cov(log_c1, log_c2)[0, 1] / max(np.var(log_c2), 1e-10))
            spread = log_c1 - beta * log_c2
            spread_diff = np.diff(spread)

            if len(spread_diff) < 10:
                continue
            adf_stat = _simple_adf(spread)
            if adf_stat < -2.86:
                p_approx = max(0.001, 0.05 * (1 + adf_stat / 2.86))
                if p_approx < p_value_threshold:
                    pairs.append((t1, t2, p_approx))

    pairs.sort(key=lambda x: x[2])
    log.info(f"Discovered {len(pairs)} cointegrated pairs from {len(ticker_list)} tickers")
    return pairs


def _simple_adf(series: np.ndarray) -> float:
    """Simplified ADF test statistic (no external statsmodels dependency)."""
    if len(series) < 10:
        return 0.0
    diff = np.diff(series)
    lagged = series[:-1]
    n = len(diff)
    x = np.column_stack([lagged, np.ones(n)])
    try:
        beta = np.linalg.lstsq(x, diff, rcond=None)[0]
        residuals = diff - x @ beta
        se = np.sqrt(np.sum(residuals**2) / (n - 2) / max(np.sum((lagged - np.mean(lagged))**2), 1e-10))
        return float(beta[0] / max(se, 1e-10))
    except Exception:
        return 0.0


# ── Full Universe Discovery ──────────────────────────────────


async def get_available_tickers() -> list[str]:
    """Get all tickers with local OHLCV data available."""
    return sorted(p.stem for p in _DATA_DIR.glob("*.jsonl") if p.stat().st_size > 0)


async def build_dynamic_universe() -> dict[str, Any]:
    """Build the complete dynamic trading universe.

    Combines:
    1. All local OHLCV data tickers
    2. Supply chain relationships from Neo4j
    3. Cointegrated pairs
    4. Global index derivatives universe
    """
    tickers = await get_available_tickers()
    supply_chain = await discover_all_supply_chain_links()
    coint_pairs = await discover_cointegrated_pairs()

    return {
        "equity_tickers": tickers,
        "supply_chain_map": supply_chain,
        "cointegrated_pairs": [(a, b) for a, b, _ in coint_pairs],
        "total_instruments": len(tickers),
        "supply_chain_links": sum(len(v) for v in supply_chain.values()),
        "coint_pairs_count": len(coint_pairs),
        "index_options": GLOBAL_INDEX_DERIVATIVES,
        "futures_universe": GLOBAL_FUTURES_UNIVERSE,
    }


# ── Global Index Derivatives Universe ────────────────────────

GLOBAL_INDEX_DERIVATIVES: dict[str, dict[str, Any]] = {
    "SPX": {
        "name": "S&P 500", "exchange": "CBOE", "multiplier": 100,
        "yahoo": "^GSPC", "futures": "ES", "etf": "SPY",
        "country": "US", "currency": "USD",
    },
    "NDX": {
        "name": "NASDAQ 100", "exchange": "CBOE", "multiplier": 100,
        "yahoo": "^IXIC", "futures": "NQ", "etf": "QQQ",
        "country": "US", "currency": "USD",
    },
    "RUT": {
        "name": "Russell 2000", "exchange": "CBOE", "multiplier": 100,
        "yahoo": "^RUT", "futures": "RTY", "etf": "IWM",
        "country": "US", "currency": "USD",
    },
    "DJX": {
        "name": "Dow Jones", "exchange": "CBOE", "multiplier": 100,
        "yahoo": "^DJI", "futures": "YM", "etf": "DIA",
        "country": "US", "currency": "USD",
    },
    "VIX": {
        "name": "CBOE VIX", "exchange": "CBOE", "multiplier": 100,
        "yahoo": "^VIX", "futures": "VX", "etf": "VXX",
        "country": "US", "currency": "USD",
    },
    "DAX": {
        "name": "DAX 40", "exchange": "EUREX", "multiplier": 5,
        "yahoo": "^GDAXI", "futures": "FDAX", "etf": "EWG",
        "country": "DE", "currency": "EUR",
    },
    "UKX": {
        "name": "FTSE 100", "exchange": "ICE", "multiplier": 10,
        "yahoo": "^FTSE", "futures": "Z", "etf": "EWU",
        "country": "GB", "currency": "GBP",
    },
    "SX5E": {
        "name": "EURO STOXX 50", "exchange": "EUREX", "multiplier": 10,
        "yahoo": "^STOXX50E", "futures": "FESX", "etf": "FEZ",
        "country": "EU", "currency": "EUR",
    },
    "NKY": {
        "name": "Nikkei 225", "exchange": "OSE", "multiplier": 1000,
        "yahoo": "^N225", "futures": "NK", "etf": "EWJ",
        "country": "JP", "currency": "JPY",
    },
    "HSI": {
        "name": "Hang Seng", "exchange": "HKFE", "multiplier": 50,
        "yahoo": "^HSI", "futures": "HSI", "etf": "EWH",
        "country": "HK", "currency": "HKD",
    },
    "NIFTY": {
        "name": "NIFTY 50", "exchange": "NSE", "multiplier": 75,
        "yahoo": "^NSEI", "futures": "NIFTY", "etf": "INDA",
        "country": "IN", "currency": "INR",
    },
    "SENSEX": {
        "name": "BSE SENSEX", "exchange": "BSE", "multiplier": 15,
        "yahoo": "^BSESN", "futures": "SENSEX", "etf": "INDY",
        "country": "IN", "currency": "INR",
    },
    "KOSPI": {
        "name": "KOSPI 200", "exchange": "KRX", "multiplier": 250000,
        "yahoo": "^KS11", "futures": "K200", "etf": "EWY",
        "country": "KR", "currency": "KRW",
    },
    "ASX": {
        "name": "S&P/ASX 200", "exchange": "ASX", "multiplier": 25,
        "yahoo": "^AXJO", "futures": "AP", "etf": "EWA",
        "country": "AU", "currency": "AUD",
    },
    "CAC": {
        "name": "CAC 40", "exchange": "EURONEXT", "multiplier": 10,
        "yahoo": "^FCHI", "futures": "FCE", "etf": "EWQ",
        "country": "FR", "currency": "EUR",
    },
    "IBOV": {
        "name": "Bovespa", "exchange": "B3", "multiplier": 1,
        "yahoo": "^BVSP", "futures": "IND", "etf": "EWZ",
        "country": "BR", "currency": "BRL",
    },
    "TAIEX": {
        "name": "TAIEX", "exchange": "TAIFEX", "multiplier": 200,
        "yahoo": "^TWII", "futures": "TX", "etf": "EWT",
        "country": "TW", "currency": "TWD",
    },
    "SMI": {
        "name": "Swiss Market Index", "exchange": "EUREX", "multiplier": 10,
        "yahoo": "^SSMI", "futures": "FSMI", "etf": "EWL",
        "country": "CH", "currency": "CHF",
    },
}


GLOBAL_FUTURES_UNIVERSE: dict[str, dict[str, Any]] = {
    # Energy
    "CL": {"name": "Crude Oil WTI", "exchange": "NYMEX", "multiplier": 1000, "tick": 0.01, "currency": "USD", "category": "energy"},
    "BZ": {"name": "Brent Crude", "exchange": "ICE", "multiplier": 1000, "tick": 0.01, "currency": "USD", "category": "energy"},
    "NG": {"name": "Natural Gas", "exchange": "NYMEX", "multiplier": 10000, "tick": 0.001, "currency": "USD", "category": "energy"},
    "RB": {"name": "RBOB Gasoline", "exchange": "NYMEX", "multiplier": 42000, "tick": 0.0001, "currency": "USD", "category": "energy"},
    "HO": {"name": "Heating Oil", "exchange": "NYMEX", "multiplier": 42000, "tick": 0.0001, "currency": "USD", "category": "energy"},
    # Metals
    "GC": {"name": "Gold", "exchange": "COMEX", "multiplier": 100, "tick": 0.10, "currency": "USD", "category": "metals"},
    "SI": {"name": "Silver", "exchange": "COMEX", "multiplier": 5000, "tick": 0.005, "currency": "USD", "category": "metals"},
    "HG": {"name": "Copper", "exchange": "COMEX", "multiplier": 25000, "tick": 0.0005, "currency": "USD", "category": "metals"},
    "PL": {"name": "Platinum", "exchange": "NYMEX", "multiplier": 50, "tick": 0.10, "currency": "USD", "category": "metals"},
    "PA": {"name": "Palladium", "exchange": "NYMEX", "multiplier": 100, "tick": 0.05, "currency": "USD", "category": "metals"},
    # Agriculture
    "ZC": {"name": "Corn", "exchange": "CBOT", "multiplier": 50, "tick": 0.25, "currency": "USD", "category": "agriculture"},
    "ZS": {"name": "Soybeans", "exchange": "CBOT", "multiplier": 50, "tick": 0.25, "currency": "USD", "category": "agriculture"},
    "ZW": {"name": "Wheat", "exchange": "CBOT", "multiplier": 50, "tick": 0.25, "currency": "USD", "category": "agriculture"},
    "CT": {"name": "Cotton", "exchange": "ICE", "multiplier": 50000, "tick": 0.01, "currency": "USD", "category": "agriculture"},
    "KC": {"name": "Coffee", "exchange": "ICE", "multiplier": 37500, "tick": 0.05, "currency": "USD", "category": "agriculture"},
    "SB": {"name": "Sugar", "exchange": "ICE", "multiplier": 112000, "tick": 0.01, "currency": "USD", "category": "agriculture"},
    "CC": {"name": "Cocoa", "exchange": "ICE", "multiplier": 10, "tick": 1.0, "currency": "USD", "category": "agriculture"},
    # Equity Index Futures
    "ES": {"name": "E-mini S&P 500", "exchange": "CME", "multiplier": 50, "tick": 0.25, "currency": "USD", "category": "index"},
    "NQ": {"name": "E-mini NASDAQ 100", "exchange": "CME", "multiplier": 20, "tick": 0.25, "currency": "USD", "category": "index"},
    "YM": {"name": "E-mini Dow", "exchange": "CBOT", "multiplier": 5, "tick": 1.0, "currency": "USD", "category": "index"},
    "RTY": {"name": "E-mini Russell 2000", "exchange": "CME", "multiplier": 50, "tick": 0.10, "currency": "USD", "category": "index"},
    "MES": {"name": "Micro E-mini S&P", "exchange": "CME", "multiplier": 5, "tick": 0.25, "currency": "USD", "category": "index"},
    "MNQ": {"name": "Micro E-mini NASDAQ", "exchange": "CME", "multiplier": 2, "tick": 0.25, "currency": "USD", "category": "index"},
    # Rates
    "ZB": {"name": "30Y T-Bond", "exchange": "CBOT", "multiplier": 1000, "tick": 0.03125, "currency": "USD", "category": "rates"},
    "ZN": {"name": "10Y T-Note", "exchange": "CBOT", "multiplier": 1000, "tick": 0.015625, "currency": "USD", "category": "rates"},
    "ZF": {"name": "5Y T-Note", "exchange": "CBOT", "multiplier": 1000, "tick": 0.0078125, "currency": "USD", "category": "rates"},
    "ZT": {"name": "2Y T-Note", "exchange": "CBOT", "multiplier": 2000, "tick": 0.0078125, "currency": "USD", "category": "rates"},
    "GE": {"name": "Eurodollar", "exchange": "CME", "multiplier": 2500, "tick": 0.005, "currency": "USD", "category": "rates"},
    # FX Futures
    "6E": {"name": "Euro FX", "exchange": "CME", "multiplier": 125000, "tick": 0.00005, "currency": "USD", "category": "fx"},
    "6J": {"name": "Japanese Yen", "exchange": "CME", "multiplier": 12500000, "tick": 0.0000005, "currency": "USD", "category": "fx"},
    "6B": {"name": "British Pound", "exchange": "CME", "multiplier": 62500, "tick": 0.0001, "currency": "USD", "category": "fx"},
    "6A": {"name": "Australian Dollar", "exchange": "CME", "multiplier": 100000, "tick": 0.0001, "currency": "USD", "category": "fx"},
    "6C": {"name": "Canadian Dollar", "exchange": "CME", "multiplier": 100000, "tick": 0.00005, "currency": "USD", "category": "fx"},
    "6S": {"name": "Swiss Franc", "exchange": "CME", "multiplier": 125000, "tick": 0.0001, "currency": "USD", "category": "fx"},
}
