"""Core data contracts for the AYVID architecture.

Every Pydantic model that crosses module boundaries lives here.
NO business logic. NO imports from outer layers.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ── Enums ────────────────────────────────────────────────────

class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FX = "FX"
    CRYPTO = "CRYPTO"
    ETF = "ETF"
    BOND = "BOND"
    COMMODITY = "COMMODITY"


class Exchange(str, Enum):
    # North America
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"
    AMEX = "AMEX"
    TSX = "TSX"
    CME = "CME"
    CBOE = "CBOE"
    NYMEX = "NYMEX"
    ICE = "ICE"
    # Europe
    LSE = "LSE"
    XETRA = "XETRA"
    EURONEXT = "EURONEXT"
    SIX = "SIX"
    BME = "BME"
    BORSA_ITALIANA = "BORSA_ITALIANA"
    OMX = "OMX"
    # Asia-Pacific
    TSE = "TSE"
    HKEX = "HKEX"
    SSE = "SSE"
    SZSE = "SZSE"
    KRX = "KRX"
    BSE = "BSE"
    NSE = "NSE"
    ASX = "ASX"
    SGX = "SGX"
    # Other
    JSE = "JSE"
    B3 = "B3"
    TADAWUL = "TADAWUL"
    SMART = "SMART"


class EventType(str, Enum):
    SHOCK = "SHOCK"
    RECOVERY = "RECOVERY"
    EARNINGS = "EARNINGS"
    MACRO = "MACRO"
    GEOPOLITICAL = "GEOPOLITICAL"
    REGULATORY = "REGULATORY"
    COMMODITY_SHOCK = "COMMODITY_SHOCK"
    FX_SHOCK = "FX_SHOCK"


class TradeAction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    TWAP = "TWAP"
    VWAP = "VWAP"


class AlphaHorizon(str, Enum):
    FIRST_ORDER = "1st_order"
    SECOND_ORDER = "2nd_order"
    THIRD_ORDER = "3rd_order"
    FOURTH_ORDER = "4th_order"


class SignalStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    EXECUTING = "EXECUTING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# ── Global Instrument Universe ───────────────────────────────

class Instrument(BaseModel):
    """A single tradeable instrument in the global universe."""
    symbol: str
    name: str | None = None
    asset_class: AssetClass = AssetClass.EQUITY
    exchange: Exchange = Exchange.SMART
    currency: str = "USD"
    country: str = "US"
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    is_active: bool = True

    @property
    def full_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange.value}"


class FuturesContract(BaseModel):
    symbol: str
    underlying: str
    exchange: Exchange
    expiry: dt.date
    multiplier: float = 1.0
    currency: str = "USD"
    asset_class: AssetClass = AssetClass.FUTURE


class OptionsContract(BaseModel):
    symbol: str
    underlying: str
    exchange: Exchange
    expiry: dt.date
    strike: float
    right: Literal["CALL", "PUT"]
    multiplier: float = 100.0
    currency: str = "USD"
    asset_class: AssetClass = AssetClass.OPTION


class OptionsChainSnapshot(BaseModel):
    """Single row from an options chain."""
    underlying: str
    expiry: dt.date
    strike: float
    right: Literal["CALL", "PUT"]
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0


class FuturesQuote(BaseModel):
    """Snapshot of a futures contract."""
    symbol: str
    underlying: str
    exchange: str = "CME"
    expiry: dt.date
    multiplier: float = 1.0
    tick_size: float = 0.01
    last_price: float = 0.0
    volume: int = 0
    open_interest: int = 0
    currency: str = "USD"


class ForexPair(BaseModel):
    symbol: str
    base_currency: str
    quote_currency: str
    asset_class: AssetClass = AssetClass.FX


class CryptoAsset(BaseModel):
    symbol: str
    name: str | None = None
    market_cap: float | None = None
    asset_class: AssetClass = AssetClass.CRYPTO


class MarketIndex(BaseModel):
    symbol: str
    name: str
    country: str
    exchange: Exchange
    components: list[str] = Field(default_factory=list)
    asset_class: AssetClass = AssetClass.INDEX


class UniverseSnapshot(BaseModel):
    """Point-in-time snapshot of the full investment universe."""
    timestamp: dt.datetime = Field(default_factory=_utcnow)
    equities: int = 0
    indices: int = 0
    futures: int = 0
    options: int = 0
    fx_pairs: int = 0
    crypto: int = 0
    etfs: int = 0
    total: int = 0
    exchanges: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)


# ── Supply Chain Graph ───────────────────────────────────────

class CompanyNode(BaseModel):
    ticker: str
    name: str | None = None
    market_cap: float | None = None
    sector: str | None = None
    pagerank_score: float = 0.0
    betweenness_score: float = 0.0


class FacilityNode(BaseModel):
    facility_id: str
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    operational: bool = True


class ProductNode(BaseModel):
    """Product node in the supply chain graph."""
    sku: str
    name: str | None = None
    category: str = "unknown"
    manufacturer_ticker: str | None = None


class CompetitorEdge(BaseModel):
    """Competitive relationship between two companies."""
    ticker_a: str
    ticker_b: str
    market_overlap_pct: float = Field(ge=0.0, le=1.0)
    sector: str | None = None


class SupplyEdge(BaseModel):
    source_ticker: str
    target_ticker: str
    revenue_dependency_pct: float = Field(ge=0.0, le=1.0)
    criticality_score: float = Field(ge=0.0, le=1.0)


class CausalPath(BaseModel):
    shocked_ticker: str
    target_ticker: str
    depth: int
    compound_risk: float
    path_tickers: list[str] = Field(default_factory=list)


# ── Time-Series & Market Data ────────────────────────────────

class OHLCVBar(BaseModel):
    timestamp: dt.datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class NewsItem(BaseModel):
    timestamp: dt.datetime
    title: str
    source: str
    symbols: list[str] = Field(default_factory=list)
    text: str | None = None
    url: str | None = None
    sentiment: float | None = None


# ── Causal Intelligence ──────────────────────────────────────

class CausalEvent(BaseModel):
    """Single event in the chronological timeline fed to the reasoning LLM."""
    timestamp: dt.datetime
    source_node: str
    event_type: EventType
    compressed_summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    sequence_id: int | None = None


class AgenticState(BaseModel):
    """LangGraph state object passed between reasoning nodes."""
    target_ticker: str
    current_date: str
    timeline: list[CausalEvent] = Field(default_factory=list)
    graph_topology: str | None = None
    proposed_action: TradeAction | None = None
    rationale: str | None = None
    confidence: float = 0.0
    alpha_horizon: AlphaHorizon | None = None
    risk_assessment: str | None = None


# ── Trade Signals & Execution ────────────────────────────────

class TradeSignal(BaseModel):
    signal_id: str
    timestamp: dt.datetime = Field(default_factory=_utcnow)
    ticker: str
    action: TradeAction
    alpha_horizon: AlphaHorizon
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    compound_risk: float | None = None
    causal_path: list[str] = Field(default_factory=list)
    status: SignalStatus = SignalStatus.PENDING


class BracketOrder(BaseModel):
    """IB bracket order: entry + take-profit + stop-loss."""
    parent_order_id: int | None = None
    ticker: str
    action: Literal["BUY", "SELL"]
    quantity: int
    order_type: OrderType
    limit_price: float | None = None
    take_profit_price: float
    stop_loss_price: float


class Position(BaseModel):
    ticker: str
    side: TradeAction
    quantity: int
    avg_entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    kelly_allocation: float = 0.0
    opened_at: dt.datetime = Field(default_factory=_utcnow)


# ── Portfolio & Risk ─────────────────────────────────────────

class PortfolioState(BaseModel):
    timestamp: dt.datetime = Field(default_factory=_utcnow)
    positions: list[Position] = Field(default_factory=list)
    cash: float = 1_000_000.0
    total_equity: float = 1_000_000.0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    peak_equity: float = 1_000_000.0


class RiskSnapshot(BaseModel):
    timestamp: dt.datetime = Field(default_factory=_utcnow)
    value_at_risk_95: float = 0.0
    value_at_risk_99: float = 0.0
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float | None = None
    portfolio_volatility: float = 0.0
    kill_switch_armed: bool = True


# ── LLM Interaction ──────────────────────────────────────────

class LLMResponse(BaseModel):
    provider: str
    model: str
    content: str
    tokens_used: int = 0
    latency_ms: float = 0.0


class CompressedDaySummary(BaseModel):
    """Chronological Semantic Compression output for a single day."""
    date: dt.date
    ticker: str
    facts: list[str] = Field(default_factory=list)
    sentiment_shift: float = 0.0
    key_entities: list[str] = Field(default_factory=list)
    raw_event_count: int = 0
