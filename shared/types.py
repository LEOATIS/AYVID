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
    return dt.datetime.now(dt.UTC)


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
    # PASSIVE_LIMIT = post-only / maker-rebate-eligible. Routes as an
    # IBKR RELATIVE (REL) order pegged to the near touch, adaptive
    # patient. Trails the NBB (buy) / NBO (sell) for a fixed window
    # and cancels if unfilled — NEVER crosses the spread.
    PASSIVE_LIMIT = "PASSIVE_LIMIT"
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
    """Physical facility (fab, refinery, warehouse, port).

    The PDF spec calls for a Neo4j ``point`` geospatial property and a
    boolean operational status. ``location`` is set on ingest by composing
    ``latitude``/``longitude`` into ``point({latitude, longitude})`` in
    Cypher; the model carries the raw scalars so we can serialise.
    """

    facility_id: str
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    operational: bool = True
    facility_type: str = "unknown"  # fab | refinery | warehouse | port | mine | datacenter
    capacity_units: float | None = None
    operator_ticker: str | None = None


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


class InstrumentChoice(str, Enum):
    """Concrete instrument the orchestrator decides to trade.

    Separate from `AssetClass` because a single underlying asset class
    maps to multiple instrument families (equity vs option on equity,
    future vs option on future, spread vs single-leg).
    """

    EQUITY = "EQUITY"
    INDEX_FUTURE = "INDEX_FUTURE"
    INDEX_CALL = "INDEX_CALL"
    INDEX_PUT = "INDEX_PUT"
    INDEX_SPREAD = "INDEX_SPREAD"


class SizingMethod(str, Enum):
    """Which Kelly variant the sizing layer should use for this intent."""

    KELLY_EQUITY = "KELLY_EQUITY"
    KELLY_OPTIONS = "KELLY_OPTIONS"
    KELLY_FUTURES = "KELLY_FUTURES"
    FIXED_RISK = "FIXED_RISK"


class AlphaScore(BaseModel):
    """Unified scoring block — the single source of truth for conviction.

    Consolidates the 5 previously-fragmented conviction stacks:
        * `_multi_factor_score`  (independent-factor confirmation count)
        * `signals.score_signal` composite + Bayesian update
        * `debate.py` analyst-vote / Bayesian aggregation
        * `_dynamic_conviction` orchestrator helper
        * researcher facilitator margin

    Every field below traces deterministically from `MarketContext` — no
    hidden state, no opaque LLM scoring. `AlphaScorer.score(ctx)` is the
    unique producer.

    The composite `unified_conviction` is the only number the post-scorer
    gates should compare against `settings.MIN_CONVICTION_TRADE`.
    """

    ticker: str
    direction: TradeAction
    factor_score: float = Field(ge=0.0, le=1.0)
    bayesian_conviction: float = Field(ge=0.0, le=1.0)
    unified_conviction: float = Field(ge=0.0, le=1.0)
    expected_r: float = 0.0
    min_r: float = 1.5
    kl_divergence_to_naive: float = 0.0
    factor_breakdown: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""


class TradeIntent(BaseModel):
    """Single canonical execution contract for AYVID.

    Replaces the previous dual surface (`TradeDecision` emitted by the
    rule-based / LLM orchestrator, `TradeSignal` emitted by the LangGraph
    causal pipeline). Every downstream layer — Kelly sizing, IB routing,
    logging, dashboard, live/equity API — consumes this one type.

    Semantics:
        * `execute`   : True → sizing + routing; False → logged only.
        * `direction` : LONG/SHORT/HOLD (the trade side).
        * `alpha_score` : the unified AlphaScore that justified the intent.
        * `audit_trail` : analyst opinions, debate outcome, divergence
          decoding KL, and any other per-intent diagnostic payload.
    """

    intent_id: str
    timestamp: dt.datetime = Field(default_factory=_utcnow)

    # ── Core decision ────────────────────────────────────────
    execute: bool = False
    ticker: str
    direction: TradeAction = TradeAction.HOLD
    asset_class: AssetClass = AssetClass.EQUITY
    instrument_choice: InstrumentChoice = InstrumentChoice.EQUITY
    alpha_horizon: AlphaHorizon = AlphaHorizon.FIRST_ORDER

    # ── Unified scoring block ───────────────────────────────
    alpha_score: AlphaScore

    # ── Sizing ──────────────────────────────────────────────
    sizing_method: SizingMethod = SizingMethod.KELLY_EQUITY
    max_risk_pct: float = 1.0

    # ── Exits ───────────────────────────────────────────────
    stop_loss_method: str = "ATR"
    take_profit_method: str = "ATR"

    # ── Derivative details ──────────────────────────────────
    strike: float | None = None
    expiry_days: int | None = None
    second_leg: dict | None = None
    futures_contract: str | None = None
    multiplier: float = 1.0
    # IB contract specifier fields — used by `submit_signal` to build
    # the right ib_async contract for non-equity intents. `contract_strike`
    # is distinct from `strike` because strike is the strategy-level
    # choice while contract_strike is the concrete strike written onto
    # the IB Option contract (they can differ for synthetic structures).
    contract_expiry: str | None = None  # YYYYMM or YYYYMMDD
    contract_strike: float | None = None
    contract_right: Literal["CALL", "PUT"] | None = None
    contract_multiplier: str | None = None

    # ── Routing ────────────────────────────────────────────
    exchange: str = "SMART"
    currency: str = "USD"
    country: str = ""

    # ── Pipeline metadata ───────────────────────────────────
    strategy_name: str = ""
    tier3_shock: bool = False
    graph_intel: bool = False
    rationale: str = ""
    causal_path: list[str] = Field(default_factory=list)
    compound_risk: float | None = None
    audit_trail: dict = Field(default_factory=dict)

    # ── Execution status ───────────────────────────────────
    status: SignalStatus = SignalStatus.PENDING

    # Read-only convenience accessors that mirror legacy `TradeSignal`
    # field names so existing callers that only READ these values keep
    # working. Callers that MUTATE conviction must update
    # `intent.alpha_score.unified_conviction` directly — conviction is
    # derived from the unified AlphaScore, not a free-standing field.
    @property
    def confidence(self) -> float:
        """Read-only alias for `alpha_score.unified_conviction`."""
        return self.alpha_score.unified_conviction

    @property
    def signal_id(self) -> str:
        """Read-only alias for `intent_id`."""
        return self.intent_id

    @property
    def action(self) -> TradeAction:
        """Read-only alias for `direction`."""
        return self.direction


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


# ── Legacy type alias ────────────────────────────────────────
# Phase-3 consolidated `TradeSignal` → `TradeIntent`. The alias is kept
# exclusively for in-tree imports that haven't been touched yet (chaos
# tests, order-routing tests, signal-book helpers). New code MUST use
# `TradeIntent` directly. The alias will be removed when all call sites
# have been migrated.
TradeSignal = TradeIntent
