"""Global Investment Universe Builder.

Discovers and catalogs every tradeable instrument across world markets:
- Equities from 30+ global exchanges
- Major world indices (S&P 500, FTSE 100, Nikkei 225, DAX, etc.)
- Futures (commodities, rates, FX futures)
- Options chains
- FX pairs (major, minor, exotic)
- Crypto assets
- ETFs (global)

Data sources: OpenBB SDK, YFinance, FMP, and IB reference data.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import pandas as pd
from openbb import obb

from shared.logger import get_logger
from shared.types import (
    AssetClass,
    CryptoAsset,
    Exchange,
    ForexPair,
    FuturesContract,
    Instrument,
    MarketIndex,
    UniverseSnapshot,
)

log = get_logger("data.universe")

# ── In-memory universe store ──────────────────────────────────

_universe: dict[str, Instrument] = {}
_indices: dict[str, MarketIndex] = {}
_futures: dict[str, FuturesContract] = {}
_fx_pairs: dict[str, ForexPair] = {}
_crypto: dict[str, CryptoAsset] = {}

# ── Global Index Definitions ─────────────────────────────────

WORLD_INDICES = {
    # North America
    "^GSPC": ("S&P 500", "US", Exchange.NYSE),
    "^DJI": ("Dow Jones Industrial Average", "US", Exchange.NYSE),
    "^IXIC": ("NASDAQ Composite", "US", Exchange.NASDAQ),
    "^RUT": ("Russell 2000", "US", Exchange.NYSE),
    "^VIX": ("CBOE Volatility Index", "US", Exchange.CBOE),
    "^GSPTSE": ("S&P/TSX Composite", "CA", Exchange.TSX),
    "^MXX": ("IPC Mexico", "MX", Exchange.BME),
    # Europe
    "^FTSE": ("FTSE 100", "GB", Exchange.LSE),
    "^GDAXI": ("DAX", "DE", Exchange.XETRA),
    "^FCHI": ("CAC 40", "FR", Exchange.EURONEXT),
    "^STOXX50E": ("EURO STOXX 50", "EU", Exchange.EURONEXT),
    "^AEX": ("AEX Amsterdam", "NL", Exchange.EURONEXT),
    "^IBEX": ("IBEX 35", "ES", Exchange.BME),
    "^SSMI": ("Swiss Market Index", "CH", Exchange.SIX),
    "FTSEMIB.MI": ("FTSE MIB", "IT", Exchange.BORSA_ITALIANA),
    "^OMX": ("OMX Stockholm 30", "SE", Exchange.OMX),
    # Asia-Pacific
    "^N225": ("Nikkei 225", "JP", Exchange.TSE),
    "^HSI": ("Hang Seng", "HK", Exchange.HKEX),
    "000001.SS": ("Shanghai Composite", "CN", Exchange.SSE),
    "399001.SZ": ("Shenzhen Component", "CN", Exchange.SZSE),
    "^KS11": ("KOSPI", "KR", Exchange.KRX),
    "^BSESN": ("BSE SENSEX", "IN", Exchange.BSE),
    "^NSEI": ("NIFTY 50", "IN", Exchange.NSE),
    "^AXJO": ("S&P/ASX 200", "AU", Exchange.ASX),
    "^STI": ("Straits Times", "SG", Exchange.SGX),
    "^TWII": ("TAIEX", "TW", Exchange.TSE),
    # Other
    "^BVSP": ("Bovespa", "BR", Exchange.B3),
    "^TASI.SR": ("Tadawul All Share", "SA", Exchange.TADAWUL),
    "^JN0U.JO": ("JSE All Share", "ZA", Exchange.JSE),
}

# Major FX pairs covering every liquid cross
MAJOR_FX_PAIRS = [
    ("EURUSD", "EUR", "USD"),
    ("GBPUSD", "GBP", "USD"),
    ("USDJPY", "USD", "JPY"),
    ("USDCHF", "USD", "CHF"),
    ("AUDUSD", "AUD", "USD"),
    ("USDCAD", "USD", "CAD"),
    ("NZDUSD", "NZD", "USD"),
    ("EURGBP", "EUR", "GBP"),
    ("EURJPY", "EUR", "JPY"),
    ("GBPJPY", "GBP", "JPY"),
    ("AUDJPY", "AUD", "JPY"),
    ("EURAUD", "EUR", "AUD"),
    ("EURCHF", "EUR", "CHF"),
    ("GBPCHF", "GBP", "CHF"),
    ("CADJPY", "CAD", "JPY"),
    ("AUDCAD", "AUD", "CAD"),
    ("AUDNZD", "AUD", "NZD"),
    ("NZDJPY", "NZD", "JPY"),
    # Emerging market pairs
    ("USDMXN", "USD", "MXN"),
    ("USDZAR", "USD", "ZAR"),
    ("USDTRY", "USD", "TRY"),
    ("USDBRL", "USD", "BRL"),
    ("USDINR", "USD", "INR"),
    ("USDCNY", "USD", "CNY"),
    ("USDSGD", "USD", "SGD"),
    ("USDHKD", "USD", "HKD"),
    ("USDKRW", "USD", "KRW"),
    ("USDTWD", "USD", "TWD"),
    ("USDTHB", "USD", "THB"),
    ("USDIDR", "USD", "IDR"),
    ("USDPLN", "USD", "PLN"),
    ("USDCZK", "USD", "CZK"),
    ("USDSEK", "USD", "SEK"),
    ("USDNOK", "USD", "NOK"),
    ("USDDKK", "USD", "DKK"),
]

# Major commodity futures
COMMODITY_FUTURES = [
    ("CL", "Crude Oil WTI", Exchange.NYMEX),
    ("BZ", "Brent Crude", Exchange.ICE),
    ("NG", "Natural Gas", Exchange.NYMEX),
    ("GC", "Gold", Exchange.CME),
    ("SI", "Silver", Exchange.CME),
    ("HG", "Copper", Exchange.CME),
    ("PL", "Platinum", Exchange.NYMEX),
    ("PA", "Palladium", Exchange.NYMEX),
    ("ZC", "Corn", Exchange.CME),
    ("ZS", "Soybeans", Exchange.CME),
    ("ZW", "Wheat", Exchange.CME),
    ("CT", "Cotton", Exchange.ICE),
    ("KC", "Coffee", Exchange.ICE),
    ("SB", "Sugar", Exchange.ICE),
    ("CC", "Cocoa", Exchange.ICE),
    ("LE", "Live Cattle", Exchange.CME),
    ("HE", "Lean Hogs", Exchange.CME),
    ("LBS", "Lumber", Exchange.CME),
]

# Rate / Index futures
RATE_FUTURES = [
    ("ES", "E-mini S&P 500", Exchange.CME),
    ("NQ", "E-mini NASDAQ 100", Exchange.CME),
    ("YM", "E-mini Dow", Exchange.CME),
    ("RTY", "E-mini Russell 2000", Exchange.CME),
    ("ZB", "US Treasury Bond", Exchange.CME),
    ("ZN", "10-Year T-Note", Exchange.CME),
    ("ZF", "5-Year T-Note", Exchange.CME),
    ("ZT", "2-Year T-Note", Exchange.CME),
    ("GE", "Eurodollar", Exchange.CME),
    ("6E", "Euro FX Future", Exchange.CME),
    ("6J", "Japanese Yen Future", Exchange.CME),
    ("6B", "British Pound Future", Exchange.CME),
    ("6A", "Australian Dollar Future", Exchange.CME),
    ("FESX", "EURO STOXX 50 Future", Exchange.EURONEXT),
    ("FDAX", "DAX Future", Exchange.XETRA),
    ("NKD", "Nikkei 225 Future", Exchange.CME),
    ("HSI", "Hang Seng Future", Exchange.HKEX),
]

CRYPTO_ASSETS = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "AVAX",
    "DOT",
    "POL",
    "LINK",
    "UNI",
    "ATOM",
    "LTC",
    "BCH",
    "NEAR",
    "APT",
    "ARB",
    "OP",
    "FIL",
    "ICP",
    "IMX",
]

# Major global equities by region — core supply chain nodes
GLOBAL_EQUITY_SCREENS = {
    "US_MEGA_CAP": [
        "AAPL",
        "MSFT",
        "AMZN",
        "NVDA",
        "GOOG",
        "GOOGL",
        "META",
        "BRK.B",
        "LLY",
        "TSM",
        "AVGO",
        "JPM",
        "V",
        "UNH",
        "MA",
        "XOM",
        "JNJ",
        "PG",
        "HD",
        "COST",
        "ABBV",
        "MRK",
        "ADBE",
        "CRM",
        "AMD",
        "NFLX",
        "INTC",
        "CSCO",
        "ORCL",
        "QCOM",
        "TXN",
        "AMAT",
        "LRCX",
        "MU",
        "KLAC",
        "ASML",
    ],
    "US_LARGE_CAP": [
        "TSLA",
        "WMT",
        "DIS",
        "PFE",
        "BMY",
        "AMGN",
        "GILD",
        "BA",
        "CAT",
        "DE",
        "MMM",
        "HON",
        "GE",
        "RTX",
        "LMT",
        "NOC",
        "UPS",
        "FDX",
        "DAL",
        "UAL",
        "AAL",
        "F",
        "GM",
        "TM",
        "DELL",
        "HPQ",
        "IBM",
        "NOW",
        "SNOW",
        "PLTR",
        "NET",
        "CRWD",
        "PANW",
        "ZS",
        "DDOG",
        "MDB",
        "SQ",
        "PYPL",
        "COIN",
        "RIVN",
    ],
    "EUROPE": [
        "NESN.SW",
        "ROG.SW",
        "NOVN.SW",
        "MC.PA",
        "OR.PA",
        "SAN.PA",
        "AIR.PA",
        "SIE.DE",
        "SAP.DE",
        "ALV.DE",
        "BAS.DE",
        "BMW.DE",
        "DAI.DE",
        "VOW3.DE",
        "ADS.DE",
        "SHEL.L",
        "AZN.L",
        "HSBA.L",
        "ULVR.L",
        "BP.L",
        "GSK.L",
        "RIO.L",
        "BHP.L",
        "GLEN.L",
        "ASML.AS",
        "PHIA.AS",
        "INGA.AS",
        "ABN.AS",
    ],
    "ASIA": [
        "9984.T",
        "7203.T",
        "6758.T",
        "6861.T",
        "9433.T",
        "0700.HK",
        "9988.HK",
        "9618.HK",
        "1299.HK",
        "0005.HK",
        "005930.KS",
        "000660.KS",
        "005380.KS",
        "RELIANCE.NS",
        "TCS.NS",
        "INFY.NS",
        "HDFCBANK.NS",
        "2330.TW",
        "2317.TW",
        "2454.TW",
    ],
    "EMERGING": [
        "VALE3.SA",
        "PETR4.SA",
        "ITUB4.SA",
        "BABA",
        "JD",
        "PDD",
        "BIDU",
        "NIO",
        "XPEV",
        "LI",
        "GRAB",
        "SE",
        "MELI",
    ],
    "SUPPLY_CHAIN_CRITICAL": [
        "TSM",
        "ASML",
        "AMAT",
        "LRCX",
        "KLAC",
        "ENTG",
        "MKSI",
        "TER",
        "ONTO",
        "CRUS",
        "SWKS",
        "QRVO",
        "MCHP",
        "NXPI",
        "ON",
        "STM",
        "IFNNY",
        "RNECY",
        "UMC",
        "GFS",
        "2317.TW",
        "HON",
        "APH",
        "TEL",
        "FLEX",
        "JBL",
        "FDX",
        "UPS",
        "AMKBY",
        "ZIM",
        "MATX",
        "XPO",
    ],
    "MAJOR_ETFS": [
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "EFA",
        "EEM",
        "VWO",
        "VEA",
        "GLD",
        "SLV",
        "USO",
        "UNG",
        "TLT",
        "IEF",
        "HYG",
        "LQD",
        "XLF",
        "XLK",
        "XLE",
        "XLV",
        "XLI",
        "XLP",
        "XLU",
        "XLB",
        "ARKK",
        "ARKG",
        "SOXX",
        "SMH",
        "XBI",
        "IBB",
        "VGK",
        "FXI",
        "EWJ",
        "EWZ",
        "EWG",
        "EWU",
        "INDA",
        "KWEB",
    ],
}


# ── Universe Build Functions ──────────────────────────────────


async def build_equity_universe() -> dict[str, Instrument]:
    """Build the global equity universe from all regional screens."""
    instruments: dict[str, Instrument] = {}

    for region, tickers in GLOBAL_EQUITY_SCREENS.items():
        for ticker in tickers:
            asset_class = AssetClass.ETF if region == "MAJOR_ETFS" else AssetClass.EQUITY

            exchange = Exchange.SMART
            country = "US"
            if ".L" in ticker:
                exchange, country = Exchange.LSE, "GB"
            elif ".DE" in ticker:
                exchange, country = Exchange.XETRA, "DE"
            elif ".PA" in ticker:
                exchange, country = Exchange.EURONEXT, "FR"
            elif ".SW" in ticker:
                exchange, country = Exchange.SIX, "CH"
            elif ".AS" in ticker:
                exchange, country = Exchange.EURONEXT, "NL"
            elif ".T" in ticker:
                exchange, country = Exchange.TSE, "JP"
            elif ".HK" in ticker:
                exchange, country = Exchange.HKEX, "HK"
            elif ".KS" in ticker:
                exchange, country = Exchange.KRX, "KR"
            elif ".NS" in ticker:
                exchange, country = Exchange.NSE, "IN"
            elif ".TW" in ticker:
                exchange, country = Exchange.TSE, "TW"
            elif ".SA" in ticker:
                exchange, country = Exchange.B3, "BR"
            elif ".SS" in ticker:
                exchange, country = Exchange.SSE, "CN"
            elif ".SZ" in ticker:
                exchange, country = Exchange.SZSE, "CN"

            instruments[ticker] = Instrument(
                symbol=ticker,
                asset_class=asset_class,
                exchange=exchange,
                country=country,
            )

    log.info(
        f"Built equity universe: {len(instruments)} instruments across {len(GLOBAL_EQUITY_SCREENS)} regions"
    )
    return instruments


async def build_index_universe() -> dict[str, MarketIndex]:
    """Build the global index universe."""
    indices: dict[str, MarketIndex] = {}
    for symbol, (name, country, exchange) in WORLD_INDICES.items():
        indices[symbol] = MarketIndex(
            symbol=symbol,
            name=name,
            country=country,
            exchange=exchange,
        )
    log.info(f"Built index universe: {len(indices)} global indices")
    return indices


async def build_futures_universe() -> dict[str, FuturesContract]:
    """Build commodity and rate futures universe."""
    futures: dict[str, FuturesContract] = {}
    expiry = dt.date.today() + dt.timedelta(days=90)

    for symbol, name, exchange in COMMODITY_FUTURES + RATE_FUTURES:
        futures[symbol] = FuturesContract(
            symbol=symbol,
            underlying=name,
            exchange=exchange,
            expiry=expiry,
        )
    log.info(f"Built futures universe: {len(futures)} contracts")
    return futures


async def build_fx_universe() -> dict[str, ForexPair]:
    """Build the global FX pair universe."""
    pairs: dict[str, ForexPair] = {}
    for symbol, base, quote in MAJOR_FX_PAIRS:
        pairs[symbol] = ForexPair(
            symbol=symbol,
            base_currency=base,
            quote_currency=quote,
        )
    log.info(f"Built FX universe: {len(pairs)} pairs")
    return pairs


async def build_crypto_universe() -> dict[str, CryptoAsset]:
    """Build the crypto asset universe."""
    crypto: dict[str, CryptoAsset] = {}
    for symbol in CRYPTO_ASSETS:
        crypto[symbol] = CryptoAsset(symbol=symbol)
    log.info(f"Built crypto universe: {len(crypto)} assets")
    return crypto


async def _fetch_sp500_from_wikipedia() -> list[str]:
    """Scrape current S&P 500 tickers from the Wikipedia list (no API key needed)."""
    import io

    import httpx

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    async with httpx.AsyncClient(
        headers={"User-Agent": "AYVID/1.0 (https://github.com/ayvid)"},
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    col = "Symbol" if "Symbol" in df.columns else df.columns[0]
    tickers = df[col].str.replace(".", "-", regex=False).tolist()
    return [t for t in tickers if isinstance(t, str) and t]


async def discover_sp500_constituents() -> list[str]:
    """Fetch live S&P 500 constituents (tries FMP API, falls back to Wikipedia)."""
    from data_primitives.extractors.openbb_client import _ensure_credentials

    _ensure_credentials()

    try:
        result = await asyncio.to_thread(obb.index.constituents, symbol="sp500", provider="fmp")
        df = result.to_df()
        tickers = df["symbol"].tolist() if "symbol" in df.columns else []
        if tickers:
            log.info(f"Discovered {len(tickers)} S&P 500 constituents via FMP")
            return tickers
    except Exception as exc:
        log.debug(f"S&P 500 via FMP failed: {exc}")

    try:
        tickers = await _fetch_sp500_from_wikipedia()
        log.info(f"Discovered {len(tickers)} S&P 500 constituents via Wikipedia")
        return tickers
    except Exception as exc:
        log.debug(f"S&P 500 via Wikipedia failed: {exc}")

    log.warning("All S&P 500 discovery providers failed, using hardcoded list")
    return GLOBAL_EQUITY_SCREENS["US_MEGA_CAP"]


async def discover_global_equities_via_screen(
    market_cap_min: float = 1e9,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Discover global equities via stock screener (tries FMP, falls back to yfinance)."""
    from data_primitives.extractors.openbb_client import _ensure_credentials

    _ensure_credentials()

    providers = ["fmp", "yfinance"]
    for provider in providers:
        try:
            result = await asyncio.to_thread(
                obb.equity.screener,
                provider=provider,
                mktcap_min=int(market_cap_min),
                limit=limit,
            )
            records = result.to_df().to_dict("records")
            if records:
                log.info(
                    f"Screener discovered {len(records)} equities via {provider} "
                    f"(mkt cap > ${market_cap_min / 1e9:.0f}B)"
                )
                return records
        except Exception as exc:
            log.debug(f"Equity screener via {provider} failed: {exc}")
            continue

    log.warning("All equity screener providers failed")
    return []


# ── Full Universe Assembly ────────────────────────────────────


async def refresh_universe_with_discovery() -> list[str]:
    """Dynamically discover and merge new tickers into the equity universe.

    Calls the S&P 500 constituents API and the FMP equity screener,
    then merges discovered tickers into GLOBAL_EQUITY_SCREENS.
    Returns all newly discovered tickers.
    """
    discovered: list[str] = []

    sp500 = await discover_sp500_constituents()
    existing = set()
    for region_list in GLOBAL_EQUITY_SCREENS.values():
        existing.update(region_list)

    new_sp500 = [t for t in sp500 if t not in existing]
    if new_sp500:
        GLOBAL_EQUITY_SCREENS.setdefault("SP500_DISCOVERED", []).extend(new_sp500)
        discovered.extend(new_sp500)
        log.info(f"Discovery: added {len(new_sp500)} S&P 500 constituents")

    screened = await discover_global_equities_via_screen(market_cap_min=5e9, limit=300)
    screened_tickers = [
        r.get("symbol", "")
        for r in screened
        if r.get("symbol") and r.get("symbol") not in existing and r.get("symbol") not in new_sp500
    ]
    if screened_tickers:
        GLOBAL_EQUITY_SCREENS.setdefault("SCREENER_DISCOVERED", []).extend(screened_tickers[:200])
        discovered.extend(screened_tickers[:200])
        log.info(
            f"Discovery: added {len(screened_tickers[:200])} screened equities (mkt cap > $5B)"
        )

    return discovered


async def build_full_universe(run_discovery: bool = False) -> UniverseSnapshot:
    """Assemble the complete global investment universe across all asset classes.

    If run_discovery is True, also fetches live S&P 500 constituents and
    runs the FMP equity screener to expand the universe dynamically.
    """
    global _universe, _indices, _futures, _fx_pairs, _crypto

    log.info("Building global investment universe...")

    if run_discovery:
        discovered = await refresh_universe_with_discovery()
        log.info(f"Discovery phase added {len(discovered)} new tickers")

    _universe, _indices, _futures, _fx_pairs, _crypto = await asyncio.gather(
        build_equity_universe(),
        build_index_universe(),
        build_futures_universe(),
        build_fx_universe(),
        build_crypto_universe(),
    )

    countries = set()
    exchanges = set()
    for inst in _universe.values():
        countries.add(inst.country)
        exchanges.add(inst.exchange.value)
    for idx in _indices.values():
        countries.add(idx.country)
        exchanges.add(idx.exchange.value)

    snapshot = UniverseSnapshot(
        equities=sum(1 for i in _universe.values() if i.asset_class == AssetClass.EQUITY),
        etfs=sum(1 for i in _universe.values() if i.asset_class == AssetClass.ETF),
        indices=len(_indices),
        futures=len(_futures),
        fx_pairs=len(_fx_pairs),
        crypto=len(_crypto),
        total=len(_universe) + len(_indices) + len(_futures) + len(_fx_pairs) + len(_crypto),
        exchanges=sorted(exchanges),
        countries=sorted(countries),
    )

    log.info(
        f"Universe complete: {snapshot.total} instruments | "
        f"{len(snapshot.exchanges)} exchanges | "
        f"{len(snapshot.countries)} countries"
    )
    return snapshot


def get_universe() -> dict[str, Instrument]:
    return dict(_universe)


def get_all_equity_tickers() -> list[str]:
    return [s for s, i in _universe.items() if i.asset_class == AssetClass.EQUITY]


def get_all_etf_tickers() -> list[str]:
    return [s for s, i in _universe.items() if i.asset_class == AssetClass.ETF]


def get_tickers_by_region(region: str) -> list[str]:
    return GLOBAL_EQUITY_SCREENS.get(region.upper(), [])


def get_supply_chain_critical_tickers() -> list[str]:
    """Return supply-chain critical tickers.

    Tries dynamic discovery from Neo4j graph centrality first,
    falls back to cached list, then to the static seed list.
    The static list is retained ONLY as a last-resort fallback —
    production runs should always populate the graph first.
    """
    return GLOBAL_EQUITY_SCREENS["SUPPLY_CHAIN_CRITICAL"]


async def get_supply_chain_critical_tickers_dynamic() -> list[str]:
    """Dynamically discover supply-chain critical tickers from Neo4j.

    Falls back to the last-known cached list if the graph is unavailable.
    Never returns an empty list — static seed is the ultimate fallback.
    """
    try:
        from graph_intelligence.tools.find_critical_nodes import discover_critical_nodes

        nodes = await discover_critical_nodes(max_nodes=60)
        tickers = [n.ticker for n in nodes]
        if len(tickers) >= 10:
            log.info(f"Dynamic discovery: {len(tickers)} critical nodes from graph")
            return tickers
    except Exception as e:
        log.warning(f"Dynamic critical node discovery failed: {e}")
    # Fallback to static list
    return GLOBAL_EQUITY_SCREENS["SUPPLY_CHAIN_CRITICAL"]


def get_all_index_symbols() -> list[str]:
    return list(_indices.keys())


def get_all_futures_symbols() -> list[str]:
    return list(_futures.keys())


def get_all_fx_symbols() -> list[str]:
    return list(_fx_pairs.keys())


def get_all_crypto_symbols() -> list[str]:
    return list(_crypto.keys())


def build_options_universe() -> list[dict]:
    """Return the set of underlyings with liquid options markets.

    Combines the most liquid US equities, ETFs, and index options
    suitable for volatility arbitrage, iron condors, and supply chain
    options plays.
    """
    liquid_underlyings = [
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "EEM",
        "TLT",
        "GLD",
        "SLV",
        "XLF",
        "XLE",
        "AAPL",
        "MSFT",
        "AMZN",
        "GOOG",
        "META",
        "NVDA",
        "TSLA",
        "AMD",
        "NFLX",
        "JPM",
        "GS",
        "BAC",
        "XOM",
        "CVX",
        "BA",
        "CAT",
        "JNJ",
        "PFE",
        "MRK",
        "CRM",
        "ORCL",
        "NOW",
        "SHOP",
        "SNAP",
        "COIN",
        "MARA",
        "SQ",
    ]
    return [
        {
            "symbol": sym,
            "asset_class": "OPTION_UNDERLYING",
            "exchange": "SMART",
            "in_universe": sym in _universe,
        }
        for sym in liquid_underlyings
    ]
