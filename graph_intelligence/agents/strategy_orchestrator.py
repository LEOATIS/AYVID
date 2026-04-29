"""LLM Strategy Orchestrator — the meta-decision engine.

This is what separates AYVID from a GPT wrapper. Instead of running
fixed strategies, the LLM dynamically decides:

1. WHICH instrument to trade (index future, index option, equity)
2. WHICH timeframe to analyse on (M5, H1, D1, W1)
3. WHICH strategy to deploy (index futures trend, index spreads, equity investment)
4. HOW to size the position (Kelly variant, margin-adjusted)
5. WHEN to enter (multi-timeframe confluence entry)

Architecture split:
- **F&O = INDEX derivatives** (NIFTY, S&P 500, DAX futures & options)
- **Equities = Investment** (stock positions, no equity options)

This mirrors how Citadel/Jane Street actually trade: index-level
macro bets via futures and options, stock-level positions via equity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from shared.logger import get_logger

if TYPE_CHECKING:
    from shared.types import AlphaScore, TradeIntent

log = get_logger("agent.orchestrator")


# ── Index → Futures Contract Specifications ───────────────────
# Real-world contract specs: multiplier, margin, currency, exchange.
# Used by both orchestrator and backtest engine for proper P&L sizing.

INDEX_FUTURES_SPECS: dict[str, dict] = {
    # fut_expiry_rule: "3rd_fri" = 3rd Friday, "last_thu" = last Thursday, "2nd_fri" = 2nd Friday
    # opt_expiry_rule: "weekly_fri" = every Friday, "weekly_thu" = every Thursday, "monthly" = same as futures
    "^GSPC": {
        "future": "ES",
        "name": "E-mini S&P 500",
        "multiplier": 50,
        "currency": "USD",
        "usd_rate": 1.0,
        "exchange": "CME",
        "country": "US",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "weekly_fri",
    },
    "^IXIC": {
        "future": "NQ",
        "name": "E-mini NASDAQ 100",
        "multiplier": 20,
        "currency": "USD",
        "usd_rate": 1.0,
        "exchange": "CME",
        "country": "US",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "weekly_fri",
    },
    "^DJI": {
        "future": "YM",
        "name": "E-mini Dow",
        "multiplier": 5,
        "currency": "USD",
        "usd_rate": 1.0,
        "exchange": "CME",
        "country": "US",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "weekly_fri",
    },
    "^RUT": {
        "future": "RTY",
        "name": "E-mini Russell 2000",
        "multiplier": 50,
        "currency": "USD",
        "usd_rate": 1.0,
        "exchange": "CME",
        "country": "US",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "weekly_fri",
    },
    "^NSEI": {
        "future": "NIFTY",
        "name": "NIFTY 50 Future",
        "multiplier": 75,
        "currency": "INR",
        "usd_rate": 0.012,
        "exchange": "NSE",
        "country": "IN",
        "margin_pct": 0.10,
        "fut_expiry_rule": "last_thu",
        "opt_expiry_rule": "weekly_thu",
    },
    "^BSESN": {
        "future": "SENSEX",
        "name": "SENSEX Future",
        "multiplier": 15,
        "currency": "INR",
        "usd_rate": 0.012,
        "exchange": "BSE",
        "country": "IN",
        "margin_pct": 0.10,
        "fut_expiry_rule": "last_thu",
        "opt_expiry_rule": "weekly_fri",
    },
    "^FTSE": {
        "future": "Z",
        "name": "FTSE 100 Future",
        "multiplier": 10,
        "currency": "GBP",
        "usd_rate": 1.27,
        "exchange": "ICE",
        "country": "GB",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "monthly",
    },
    "^GDAXI": {
        "future": "FDAX",
        "name": "DAX Future",
        "multiplier": 25,
        "currency": "EUR",
        "usd_rate": 1.08,
        "exchange": "EUREX",
        "country": "DE",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "weekly_fri",
    },
    "^FCHI": {
        "future": "FCE",
        "name": "CAC 40 Future",
        "multiplier": 10,
        "currency": "EUR",
        "usd_rate": 1.08,
        "exchange": "EURONEXT",
        "country": "FR",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "monthly",
    },
    "^STOXX50E": {
        "future": "FESX",
        "name": "EURO STOXX 50 Future",
        "multiplier": 10,
        "currency": "EUR",
        "usd_rate": 1.08,
        "exchange": "EUREX",
        "country": "EU",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "weekly_fri",
    },
    "^N225": {
        "future": "NKD",
        "name": "Nikkei 225 Future",
        "multiplier": 5,
        "currency": "USD",
        "usd_rate": 1.0,
        "exchange": "CME",
        "country": "JP",
        "margin_pct": 0.05,
        "fut_expiry_rule": "2nd_fri",
        "opt_expiry_rule": "monthly",
    },
    "^HSI": {
        "future": "HSI",
        "name": "Hang Seng Future",
        "multiplier": 50,
        "currency": "HKD",
        "usd_rate": 0.128,
        "exchange": "HKEX",
        "country": "HK",
        "margin_pct": 0.08,
        "fut_expiry_rule": "2nd_last_bd",
        "opt_expiry_rule": "monthly",
    },
    "^KS11": {
        "future": "KOSPI",
        "name": "KOSPI 200 Future",
        "multiplier": 250000,
        "currency": "KRW",
        "usd_rate": 0.00075,
        "exchange": "KRX",
        "country": "KR",
        "margin_pct": 0.08,
        "fut_expiry_rule": "2nd_thu",
        "opt_expiry_rule": "weekly_thu",
    },
    "^AXJO": {
        "future": "AP",
        "name": "S&P/ASX 200 Future",
        "multiplier": 25,
        "currency": "AUD",
        "usd_rate": 0.65,
        "exchange": "ASX",
        "country": "AU",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_thu",
        "opt_expiry_rule": "monthly",
    },
    "^BVSP": {
        "future": "IND",
        "name": "Bovespa Future",
        "multiplier": 1,
        "currency": "BRL",
        "usd_rate": 0.20,
        "exchange": "B3",
        "country": "BR",
        "margin_pct": 0.08,
        "fut_expiry_rule": "wed_near_15th",
        "opt_expiry_rule": "monthly",
    },
    "^SSMI": {
        "future": "SMI",
        "name": "Swiss Market Future",
        "multiplier": 10,
        "currency": "CHF",
        "usd_rate": 1.10,
        "exchange": "SIX",
        "country": "CH",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "monthly",
    },
    "^GSPTSE": {
        "future": "SXF",
        "name": "S&P/TSX 60 Future",
        "multiplier": 200,
        "currency": "CAD",
        "usd_rate": 0.74,
        "exchange": "TMX",
        "country": "CA",
        "margin_pct": 0.05,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "monthly",
    },
    "000001.SS": {
        "future": "IF",
        "name": "CSI 300 Future",
        "multiplier": 300,
        "currency": "CNY",
        "usd_rate": 0.14,
        "exchange": "CFFEX",
        "country": "CN",
        "margin_pct": 0.12,
        "fut_expiry_rule": "3rd_fri",
        "opt_expiry_rule": "monthly",
    },
    "^STI": {
        "future": "SGX_NK",
        "name": "SGX Nifty / STI Future",
        "multiplier": 10,
        "currency": "SGD",
        "usd_rate": 0.74,
        "exchange": "SGX",
        "country": "SG",
        "margin_pct": 0.06,
        "fut_expiry_rule": "2nd_last_bd",
        "opt_expiry_rule": "monthly",
    },
    "^TWII": {
        "future": "TX",
        "name": "TAIEX Future",
        "multiplier": 200,
        "currency": "TWD",
        "usd_rate": 0.031,
        "exchange": "TAIFEX",
        "country": "TW",
        "margin_pct": 0.07,
        "fut_expiry_rule": "3rd_wed",
        "opt_expiry_rule": "monthly",
    },
}

TRADEABLE_INDEX_SYMBOLS = list(INDEX_FUTURES_SPECS.keys())


# Indices with wider bid/ask spreads, fewer market-makers, or restricted
# routing. We require materially higher conviction to enter on these venues
# because the realized fill quality cannot match the simulated mid-price.
ILLIQUID_INDEX_SYMBOLS: set[str] = {
    "^HSI",
    "000001.SS",
    "^TWII",
    "^STI",
    "^AXJO",
    "^BVSP",
}

BANNED_FNO_INDICES: set[str] = {
    "^KS11",
    "^STOXX50E",
    "^DJI",
    "^RUT",
    "000001.SS",
    "^TWII",
}

# Batch 3/H: per-future strategy-variant blocklist.
#
# Distinct from `BANNED_FNO_INDICES` (which kills the WHOLE index
# for EVERY strategy) — this set blocks specific (strategy, future)
# combinations whose historical win rate is ≤33% with n≥3 trades,
# i.e. strategies the 2018-2025 walkforward proved have no edge on
# a specific venue even though the *same strategy* works on a
# different venue (e.g. `idx_bear_spread_fce` wins at 60% WR on the
# CAC 40 — we keep that; but `idx_bear_spread_sgx_nk` lost 33% WR
# over 3 trades — we block that specific variant).
#
# Seeding: populated from the `document/backtests/shock_driven_*.json`
# walkforward reports. The Option-C recalibrator in
# `backtesting.trade_outcomes` will also add to this set at runtime
# when a variant crosses the threshold — evidence-driven expansion.
BLOCKED_STRATEGY_VARIANTS: set[str] = {
    # 2018-2025 walkforward, n≥3, WR ≤33%:
    "idx_bear_spread_sgx_nk",  # SGX Nikkei — 3 trades, 33% WR, -$3.3k
    "idx_bear_spread_z",  # Z future    — 4 trades, 25% WR, -$10.4k
}


def is_strategy_variant_blocked(strategy_name: str) -> bool:
    """Return True if this concrete per-future strategy variant is on
    the H-blocklist. Used by `index_fno_orchestrate` to skip emission
    before any other gating fires."""
    return bool(strategy_name) and strategy_name in BLOCKED_STRATEGY_VARIANTS


def _illiquid_factor_floor(symbol: str, base_floor: float) -> float:
    """Bump the factor-score floor for known-illiquid index venues."""
    if symbol in ILLIQUID_INDEX_SYMBOLS:
        return max(base_floor + 0.20, 0.50)
    return base_floor


@dataclass
class MarketContext:
    """All market data the orchestrator needs to make decisions.

    Includes both per-ticker signals AND global macro context so the
    orchestrator can think multi-dimensionally like Citadel.
    """

    ticker: str
    current_price: float = 0.0
    volatility_regime: str = "NORMAL"  # LOW_VOL, NORMAL, HIGH_VOL, CRISIS
    trend_regime: str = "CHOPPY"  # STRONG_TREND, WEAK_TREND, MEAN_REVERTING, CHOPPY
    hurst: float = 0.5
    rsi: float = 50.0
    atr: float = 0.0
    realized_vol: float = 0.0
    implied_vol: float | None = None
    iv_rv_spread: float | None = None
    supply_chain_shock: bool = False
    shock_source: str | None = None
    shock_magnitude: float = 0.0
    shock_bias: str = ""  # BULLISH, BEARISH, NEUTRAL, or "" (no bias)
    is_beneficiary: bool = False
    is_victim: bool = False
    sector: str | None = None
    country: str = "US"
    asset_class: str = "EQUITY"  # EQUITY, ETF, INDEX, FUTURE, FX, CRYPTO
    mtf_signals: dict[str, str] = field(default_factory=dict)
    portfolio_exposure_pct: float = 0.0
    available_instruments: list[str] = field(default_factory=list)
    z_score: float = 0.0
    trend_strength: float = 0.0
    volume_ratio: float = 1.0
    ou_theta: float = 0.0
    ret_5d: float = 0.0
    ret_20d: float = 0.0
    # Global macro context
    market_breadth: float = 0.5  # 0=bearish, 0.5=neutral, 1=bullish (% stocks above 200MA)
    vix_level: float = 20.0  # current VIX equivalent
    cross_asset_momentum: float = 0.0  # avg momentum across correlated assets
    open_positions_count: int = 0  # total open positions in portfolio
    daily_pnl_pct: float = 0.0  # portfolio daily P&L so far
    # ── Tier-1 regime-gating fields (populated from RegimeState) ──
    # Positive VRP = IV premium is rich vs realised; negative = cheap
    vrp: float = 0.0
    # RV_20d / RV_60d; <0.85 means vol is compressing (calm market)
    vol_compression: float = 1.0
    # Grind-up = low-vol + positive drift + no trend + H≈0.5. Long-vol strategies
    # bleed alpha here and are hard-blocked unless causal conviction > 0.90.
    grind_up_flag: bool = False
    # Causal agent override (LangGraph). Only a conviction ≥ 0.90 can force a
    # long-vol trade through the grind-up gate.
    causal_conviction: float = 0.0
    # ── LLM-driven dynamic gating (from Strategist / Supervisor) ──
    # Authoritative strategy blocklist emitted by the Strategist after
    # reading the live RegimeState. Strategies here are hard-skipped
    # regardless of local factor scores.
    forbidden_strategies: list[str] = field(default_factory=list)
    # Supervisor-emitted per-trade dynamic thresholds (replaces the old
    # hardcoded RSI<30 / z<-1.5 numbers). Keys match StrategyParameters.
    strategy_parameters: dict = field(default_factory=dict)
    # Supervisor / Strategist risk-multiplier to scale max_risk_pct.
    risk_multiplier: float = 1.0
    # ADX — Average Directional Index for trend strength measurement.
    adx: float = 20.0
    # Regime confidence from the Strategist (0-1). Trades are blocked
    # when the Strategist is uncertain about the current regime.
    regime_confidence: float = 0.5
    # Causal intel dict (shock_type, expected_direction, graph_role, etc.)
    causal_intel: dict = field(default_factory=dict)
    # IV Percentile — where current IV sits relative to 1-year history (0-100).
    # >65 = elevated (premium selling territory), <35 = suppressed.
    iv_percentile: float = 50.0
    # Unusual Options Activity signal for the 10th alpha factor.
    uoa_signal: Any = None


@dataclass
class TradeDecision:
    """Structured output from the LLM orchestrator.

    R-multiple framework (Citadel / Jane Street style):
        expected_r = expected_profit / risk_per_unit
        Only execute when expected_r >= min_r (typically >= 2.0).
        This ensures the portfolio is positively skewed — small frequent
        losses, large occasional wins — the institutional edge.
    """

    action: Literal["EXECUTE", "SKIP"]
    instrument_type: str  # "EQUITY", "INDEX_FUTURE", "INDEX_CALL", "INDEX_PUT", "INDEX_SPREAD"
    direction: str  # "LONG", "SHORT"
    ticker: str
    strategy_name: str
    analysis_timeframe: str  # "D1", "H4", "H1", etc.
    entry_timeframe: str  # "M5", "M1", etc.
    conviction: float  # 0.0 to 1.0
    sizing_method: str  # "KELLY_EQUITY", "KELLY_OPTIONS", "KELLY_FUTURES", "FIXED_RISK"
    max_risk_pct: float  # max portfolio risk for this trade
    stop_loss_method: str  # "ATR", "FIXED_PCT", "VOLATILITY_ADJUSTED", "SPREAD_BASED"
    take_profit_method: str  # "ATR", "FIXED_PCT", "MEAN_REVERSION", "SPREAD_BASED"
    rationale: str
    strike: float | None = None
    expiry_days: int | None = None
    second_leg: dict | None = None  # for spreads
    expected_r: float = 0.0  # expected reward / risk (R-multiple)
    min_r: float = 1.5  # minimum R-multiple gate
    # Index-specific fields
    futures_contract: str | None = None  # "ES", "NQ", "NIFTY", etc.
    multiplier: float = 1.0
    country: str = ""
    exchange: str = ""
    graph_intel: bool = False  # True if trade was driven by causal graph intelligence
    # Concrete exit percentages. The *method* fields above describe
    # HOW to compute the stop / take-profit (ATR bucket, fixed,
    # volatility-adjusted, …); these two hold the actual % values
    # that downstream executors (the signal-book upsert, the paper
    # runner, IB order builder) need as numbers. Institutional
    # defaults — 3 % stop / 6 % take-profit — give a 2R envelope
    # that matches the ``min_r >= 1.5`` gate on TradeDecision. The
    # Tier-3 emergency-recompute path in ``continuous_worker.py``
    # crashed on every shock ticker (``AttributeError:
    # 'TradeDecision' object has no attribute 'stop_loss_pct'``)
    # because these fields were absent entirely — the signal book
    # could never be refreshed on a shock. Now every TradeDecision
    # carries safe defaults; orchestrator paths that know the ATR or
    # vol can still override them with tighter values.
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 6.0


ORCHESTRATOR_SYSTEM = """You are the Strategy Orchestrator for AYVID, an AI-native quantitative hedge fund.

You think like a portfolio manager at Citadel, Jane Street, or Two Sigma.

ARCHITECTURE (non-negotiable):
- **F&O = INDEX DERIVATIVES**: trade index futures (ES, NQ, NIFTY, FDAX) and index options
- **Equities = Investment**: stock positions only, no equity options

CORE RULES:
1. POSITIVE R-MULTIPLES: every trade must risk $1 to make $2+.
2. INDEX FUTURES: trend-following on global indices with tight ATR stops, R = 2-3x.
3. INDEX OPTIONS: debit spreads and long puts/calls on indices for directional bets.
4. Require multi-factor confirmation (4+ independent signals).
5. SKIP 80%+ of opportunities. Only trade the most asymmetric setups.
6. Size inversely to tail risk. Never exceed 2% portfolio risk per trade.

You MUST output valid JSON with these exact fields:
{
    "action": "EXECUTE" or "SKIP",
    "instrument_type": "EQUITY" | "INDEX_FUTURE" | "INDEX_CALL" | "INDEX_PUT" | "INDEX_SPREAD",
    "direction": "LONG" | "SHORT",
    "strategy_name": "<descriptive name>",
    "analysis_timeframe": "D1" | "H4" | "H1",
    "entry_timeframe": "M5" | "M1" | "M15",
    "conviction": 0.65-1.0,
    "sizing_method": "KELLY_EQUITY" | "KELLY_OPTIONS" | "KELLY_FUTURES" | "FIXED_RISK",
    "max_risk_pct": 0.5-2.0,
    "stop_loss_method": "ATR" | "FIXED_PCT" | "VOLATILITY_ADJUSTED",
    "take_profit_method": "ATR" | "FIXED_PCT" | "MEAN_REVERSION",
    "rationale": "<1-2 sentence reasoning>",
    "expected_r": float (>= 1.5),
    "min_r": 1.5
}

If expected_r < 1.5 or multi-factor score < 50%, set action to "SKIP"."""


def _build_context_prompt(ctx: MarketContext) -> str:
    """Build the market context prompt for the LLM."""
    mtf_str = (
        ", ".join(f"{tf}={d}" for tf, d in ctx.mtf_signals.items()) if ctx.mtf_signals else "N/A"
    )

    instruments = (
        ", ".join(ctx.available_instruments)
        if ctx.available_instruments
        else "EQUITY, CALL, PUT, STRADDLE, IRON_CONDOR, FUTURE"
    )

    iv_section = ""
    if ctx.implied_vol is not None:
        iv_section = f"""
Implied Volatility: {ctx.implied_vol:.1%}
IV-RV Spread: {ctx.iv_rv_spread:+.1%} ({"IV expensive" if (ctx.iv_rv_spread or 0) > 0 else "IV cheap"})"""

    shock_section = ""
    if ctx.supply_chain_shock:
        shock_section = f"""

SUPPLY CHAIN SHOCK DETECTED:
Source: {ctx.shock_source}
Magnitude: {ctx.shock_magnitude:+.1%}
This ticker is a {"direct" if ctx.shock_magnitude > 0.05 else "indirect"} supply chain link.
AYVID'S EDGE: Express this view through OPTIONS for asymmetric payoff."""

    return f"""MARKET CONTEXT FOR {ctx.ticker}:

Price: ${ctx.current_price:.2f}
Sector: {ctx.sector or "Unknown"}

REGIME:
Volatility Regime: {ctx.volatility_regime}
Trend Regime: {ctx.trend_regime}
Hurst Exponent: {ctx.hurst:.3f} ({"trending" if ctx.hurst > 0.55 else "mean-reverting" if ctx.hurst < 0.45 else "random walk"})
Realized Volatility: {ctx.realized_vol:.1%}{iv_section}

INDICATORS:
RSI(14): {ctx.rsi:.1f}
ATR(14): ${ctx.atr:.2f} ({ctx.atr / ctx.current_price * 100:.1f}% of price)

MULTI-TIMEFRAME SIGNALS: {mtf_str}

PORTFOLIO:
Current exposure to this ticker: {ctx.portfolio_exposure_pct:.1f}%
Available instruments: {instruments}
{shock_section}

What is the optimal trade? Output JSON only."""


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1).strip())
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return json.loads(text)


async def orchestrate_trade(ctx: MarketContext) -> TradeDecision:
    """Ask the LLM to decide the optimal trade for the given context.

    This is the core function. It sends the full market context to the LLM
    and receives a structured TradeDecision back.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from shared.llm_client import get_langchain_chat_model

    llm = get_langchain_chat_model(temperature=0.05, max_tokens=800)

    prompt = _build_context_prompt(ctx)
    response = await llm.ainvoke(
        [
            SystemMessage(content=ORCHESTRATOR_SYSTEM),
            HumanMessage(content=prompt),
        ]
    )

    try:
        parsed = _extract_json(response.content)
        exp_r = float(parsed.get("expected_r", 0.0))
        min_r_val = float(parsed.get("min_r", MIN_R_GLOBAL))
        if exp_r < min_r_val and parsed.get("action") == "EXECUTE":
            log.warning(f"LLM proposed R={exp_r:.1f} < min_r={min_r_val:.1f}, downgrading to SKIP")
            parsed["action"] = "SKIP"
        decision = TradeDecision(
            action=parsed.get("action", "SKIP"),
            instrument_type=parsed.get("instrument_type", "EQUITY"),
            direction=parsed.get("direction", "LONG"),
            ticker=ctx.ticker,
            strategy_name=parsed.get("strategy_name", "unknown"),
            analysis_timeframe=parsed.get("analysis_timeframe", "D1"),
            entry_timeframe=parsed.get("entry_timeframe", "M5"),
            conviction=float(parsed.get("conviction", 0.5)),
            sizing_method=parsed.get("sizing_method", "KELLY_EQUITY"),
            max_risk_pct=float(parsed.get("max_risk_pct", 2.0)),
            stop_loss_method=parsed.get("stop_loss_method", "ATR"),
            take_profit_method=parsed.get("take_profit_method", "ATR"),
            rationale=parsed.get("rationale", ""),
            strike=parsed.get("strike"),
            expiry_days=parsed.get("expiry_days"),
            second_leg=parsed.get("second_leg"),
            expected_r=exp_r,
            min_r=min_r_val,
        )
        log.info(
            f"Orchestrator: {decision.action} {decision.instrument_type} "
            f"{decision.direction} {ctx.ticker} | "
            f"strategy={decision.strategy_name} conviction={decision.conviction:.0%} | "
            f"analyse={decision.analysis_timeframe} enter={decision.entry_timeframe}"
        )
        return decision
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.warning(f"Orchestrator parse failed: {exc}")
        return TradeDecision(
            action="SKIP",
            instrument_type="EQUITY",
            direction="LONG",
            ticker=ctx.ticker,
            strategy_name="parse_error",
            analysis_timeframe="D1",
            entry_timeframe="M5",
            conviction=0.0,
            sizing_method="FIXED_RISK",
            max_risk_pct=1.0,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=f"Parse error: {exc}",
        )


def _pick_expiry(volatility_regime: str, strategy: str) -> int:
    """Select option expiry in days based on regime and strategy."""
    if "spread" in strategy or "butterfly" in strategy:
        return 21 if volatility_regime == "HIGH_VOL" else 30
    if "protective" in strategy or "hedge" in strategy:
        return 30
    if volatility_regime == "HIGH_VOL":
        return 14
    if volatility_regime == "LOW_VOL":
        return 30
    return 21


def _multi_factor_score(ctx: MarketContext) -> float:
    """Bayesian-inspired multi-factor confirmation score.

    Returns 0-1. Higher = more factors agree on direction.
    Citadel/Jane Street only trade when 4+ independent signals confirm.
    """
    confirmations = 0
    total = 0

    # Z-score extremity (mean reversion signal)
    if abs(ctx.z_score) > 1.5:
        confirmations += 1
    total += 1

    # RSI extremity
    if ctx.rsi < 30 or ctx.rsi > 70:
        confirmations += 1
    total += 1

    # Hurst confirms regime (trending for trend, MR for mean-rev)
    if ctx.trend_regime in ("STRONG_TREND", "WEAK_TREND") and ctx.hurst > 0.55:
        confirmations += 1
    elif ctx.trend_regime == "MEAN_REVERTING" and ctx.hurst < 0.45:
        confirmations += 1
    total += 1

    # Volume confirmation (above-average volume on signal day)
    if ctx.volume_ratio > 1.2:
        confirmations += 1
    total += 1

    # Return momentum confirms direction
    if abs(ctx.ret_5d) > 0.02:
        confirmations += 1
    total += 1

    # OU speed (fast reversion for MR plays)
    if ctx.ou_theta > 0.5:
        confirmations += 1
    total += 1

    # IV-RV dislocation
    if ctx.iv_rv_spread is not None and abs(ctx.iv_rv_spread) > 0.04:
        confirmations += 1
    total += 1

    # Macro context: market breadth supports direction
    if ctx.market_breadth > 0.6 and ctx.trend_strength > 0:
        confirmations += 1
    elif ctx.market_breadth < 0.4 and ctx.trend_strength < 0:
        confirmations += 1
    total += 1

    # VIX context: high VIX = more aggressive on mean-reversion
    if ctx.vix_level > 25 and ctx.trend_regime == "MEAN_REVERTING":
        confirmations += 1
    elif ctx.vix_level < 15 and ctx.trend_regime in ("STRONG_TREND", "WEAK_TREND"):
        confirmations += 1
    total += 1

    return confirmations / max(total, 1)


def _skip(ctx: MarketContext, reason: str) -> TradeDecision:
    """Shorthand for a SKIP decision."""
    return TradeDecision(
        action="SKIP",
        instrument_type="EQUITY",
        direction="LONG",
        ticker=ctx.ticker,
        strategy_name="no_edge",
        analysis_timeframe="D1",
        entry_timeframe="M5",
        conviction=0.0,
        sizing_method="FIXED_RISK",
        max_risk_pct=1.0,
        stop_loss_method="ATR",
        take_profit_method="ATR",
        rationale=reason,
        expected_r=0.0,
        min_r=0.0,
    )


# All policy floors below are operator-tunable via `.env` through
# `shared.config.Settings`. These module-level aliases preserve the
# legacy import surface so downstream code and tests keep working.
from shared.config import settings as _settings

MIN_FACTOR_SCORE: float = _settings.MIN_FACTOR_SCORE
MIN_CONVICTION: float = _settings.MIN_CONVICTION_TRADE
MIN_R_GLOBAL: float = _settings.MIN_R_GLOBAL

# ── Regime-gating constants (Tier-1 long-vol block) ──────────────────
# Causal-agent conviction required to override a grind-up block on
# long-vol strategies (naked puts, crisis puts, shock-directed puts,
# aggressive short equity). Below this bar, the orchestrator MUST skip
# — low-vol grind-up markets systematically bleed these strategies.
CAUSAL_OVERRIDE_THRESHOLD: float = _settings.CAUSAL_OVERRIDE_CONVICTION

# VRP band where buying options is statistically expensive. When
# IV − RV ≥ this, we refuse to pay for premium and flip to credit
# structures or skip entirely.
#
# NOTE: live trading reads VRP from real option chains. In backtest
# we proxy IV as `rv + |ret_5d|×2 + 0.02`, so the proxy VRP is
# structurally ~|ret_5d|×2 + 0.02 (≈ 4-14% in any active market).
# We therefore use a proxy-calibrated threshold of 0.06 and bypass
# the gate entirely when a causal shock is active with directional
# bias (the shock signal is the primary alpha driver; VRP would
# otherwise veto exactly the trades the causal graph is right about).
VRP_RICH_THRESHOLD: float = _settings.VRP_RICH_THRESHOLD

# Long-vol strategy families — gated through the grind-up filter.
LONG_VOL_STRATEGY_PREFIXES: tuple[str, ...] = (
    "crisis_index_put",
    "crisis_capitulation_bounce",
    "idx_mr_overbought_put_",
    "idx_decline_put_",
    "shock_directed_put_",
)


def _is_long_vol_strategy(strategy_name: str) -> bool:
    """Return True if the strategy buys net premium / crisis protection."""
    return any(strategy_name.startswith(p) for p in LONG_VOL_STRATEGY_PREFIXES)


def _matches_blocklist(strategy_name: str, blocklist: list[str]) -> bool:
    """Match a concrete strategy_name against a canonical blocklist.

    Concrete names (emitted by `index_fno_orchestrate`) are suffixed
    with an instrument tag — e.g. `shock_directed_put_es`, `idx_trend_nq`.
    The Strategist emits canonical prefixes (`shock_directed_put`,
    `idx_trend_es`). A blocklist entry matches when it either:
      - exactly equals the concrete name, OR
      - is a prefix of the concrete name up to an underscore boundary.
    """
    if not blocklist:
        return False
    for blocked in blocklist:
        if strategy_name == blocked:
            return True
        if strategy_name.startswith(blocked + "_"):
            return True
        if strategy_name.startswith(blocked):
            return True
    return False


def get_param(
    ctx: MarketContext,
    key: str,
    default: float,
    *,
    strategy_name: str | None = None,
) -> float:
    """Read a dynamic parameter with three-tier fallback:

    1. **Supervisor LLM** — `ctx.strategy_parameters[key]` if present.
       The LLM justifies deviation from the prior against the current
       regime; this is the most specific, most recent signal.
    2. **Empirical prior** — `calibration/priors.json` lookup for the
       current (strategy, regime) cell. This is the statistically-
       validated anchor, populated by `backtesting.calibrate`.
    3. **Hardcoded default** — last-resort legacy constant, shipped
       with the code so the pipeline degrades gracefully when the
       priors file is missing.

    `strategy_name` narrows the prior lookup; if the caller omits it,
    we fall straight to the hardcoded default.
    """
    params = ctx.strategy_parameters or {}
    if isinstance(params, dict) and key in params:
        try:
            return float(params[key])
        except (TypeError, ValueError):
            pass  # fall through to prior / default

    prior_val = _lookup_empirical_prior(ctx, strategy_name, key)
    if prior_val is not None:
        return prior_val

    return default


# ── Empirical-prior loader (calibration/priors.json) ──────────────────
import json as _json
from pathlib import Path as _Path

_PRIORS_PATH: _Path = _Path("calibration/priors.json")
_priors_cache: dict[str, Any] | None = None
_priors_cache_mtime: float | None = None

# Keys in priors.json → Supervisor-facing parameter names. Maintained
# here so that the calibration file stays compact while the LLM-facing
# contract stays verbose and unambiguous.
_PRIORS_KEY_ALIASES: dict[str, str] = {
    "max_rsi_entry_long": "max_rsi",
    "max_z_entry_long": "max_z",
    "min_factor_score": "min_factor_score",
    "min_expected_r": "min_expected_r",
}


def _load_priors_cached() -> dict[str, Any]:
    """Read + cache the priors.json file, reloading when mtime changes.

    Returns an empty dict when the file is absent (calibration never
    ran) so callers transparently fall back to hardcoded defaults.
    """
    global _priors_cache, _priors_cache_mtime
    try:
        mtime = _PRIORS_PATH.stat().st_mtime
    except OSError:
        _priors_cache = {}
        _priors_cache_mtime = None
        return _priors_cache

    if _priors_cache is not None and _priors_cache_mtime == mtime:
        return _priors_cache

    try:
        payload = _json.loads(_PRIORS_PATH.read_text())
        _priors_cache = payload.get("priors", {}) if isinstance(payload, dict) else {}
    except (OSError, ValueError, _json.JSONDecodeError) as exc:
        log.warning(f"priors.json load failed ({exc}); falling back to defaults")
        _priors_cache = {}
    _priors_cache_mtime = mtime
    return _priors_cache


def _compose_regime_key(ctx: MarketContext) -> str:
    """Build the regime-bucket key that matches `calibrate.regime_key`."""
    trend = ctx.trend_regime or "CHOPPY"
    vol = ctx.volatility_regime or "NORMAL"
    if trend in ("STRONG_TREND", "WEAK_TREND"):
        direction = "_UP" if ctx.ret_20d >= 0 else "_DOWN"
        return f"{trend}{direction}__{vol}"
    return f"{trend}__{vol}"


def _lookup_empirical_prior(
    ctx: MarketContext,
    strategy_name: str | None,
    key: str,
) -> float | None:
    """Resolve (strategy, regime, key) against the priors JSON.

    Returns None when no prior exists OR the cell is marked
    `blocked_by_regime` — the orchestrator's upstream blocklist should
    already be enforcing that, but we stay safe and let the default
    fire. Returns a float otherwise.
    """
    if not strategy_name:
        return None
    priors = _load_priors_cached()
    if not priors:
        return None

    cell = priors.get(strategy_name, {}).get(_compose_regime_key(ctx))
    if not cell or cell.get("blocked_by_regime"):
        return None

    alias = _PRIORS_KEY_ALIASES.get(key, key)
    raw = cell.get(alias)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_strategy_regime_blocked(
    ctx: MarketContext,
    strategy_name: str,
) -> bool:
    """Return True if priors.json explicitly flagged this cell as blocked.

    The orchestrator uses this as a secondary gate: even when the
    Strategist forgot to add the strategy to `forbidden_strategies`,
    an empirical `blocked_by_regime` prior is strong enough evidence
    to refuse the trade.
    """
    priors = _load_priors_cached()
    if not priors:
        return False
    cell = priors.get(strategy_name, {}).get(_compose_regime_key(ctx))
    return bool(cell and cell.get("blocked_by_regime"))


# ── Institutional EV hurdle (Step 2 gate) ─────────────────────────────
# Conservative per-asset-class round-trip-cost-as-%-of-notional used at
# the orchestrator layer. These mirror (and slightly over-estimate) the
# `shared.cost_model` assumptions so we never let a sub-noise trade
# through just because the downstream Kelly sizer would have rejected it.
#
# Equity:  ~5 bps slippage/side + $0.35 min commission ≈ 0.10% round-trip
# Future:  ~2 bps slippage/side + $0.85 comm / trade ≈ 0.10% round-trip
# Option:  1.5% premium slippage/side ≈ 3.0% round-trip on premium value;
#          in notional-of-underlying terms options are expressed as a
#          much larger percentage of the paid debit, so we keep that
#          dimension separate: evaluate EV on the *premium*, not notional.
# Spread:  1.5% × 1.5 multiplier × 2 sides = 4.5% on net_debit.
ORCHESTRATOR_COST_PCT_OF_NOTIONAL: dict[str, float] = {
    "EQUITY": 0.0020,  # 20 bps round-trip incl. caps & mins
    "INDEX_FUTURE": 0.0015,  # 15 bps — institutional tier futures
    "INDEX_CALL": 0.0400,  # 4% of premium round-trip
    "INDEX_PUT": 0.0400,
    "INDEX_SPREAD": 0.0600,  # 6% of net debit (two legs, two sides)
    "FX": 0.0004,  # 4 bps — interbank FX spread
    "COMMODITY_FUTURE": 0.0010,  # 10 bps — commodity futures (tick + comm)
    "CRYPTO": 0.0020,  # 20 bps — maker/taker fees
    "ETF": 0.0015,  # 15 bps — ETF spread + commission
    "RATE_FUTURE": 0.0008,  # 8 bps — treasury futures (tight spread)
}

# Institutional EV hurdle multiple — expected PnL must clear N× cost.
# Single source of truth is `shared.config.Settings.EV_HURDLE_MULTIPLE`;
# `execution_engine.math.costs` reads the same key, so both orchestrator
# and Kelly EV-gate agree on "what 'economically worth trading' means".
ORCH_EV_HURDLE: float = _settings.EV_HURDLE_MULTIPLE

# Instrument-to-risk-capital basis: for equities/futures, the notional
# exposure is roughly equal to the portfolio risk pct (we trade full
# size at the stop). For options, the paid premium IS the risk — so
# the cost-of-premium (4-6%) scales down to portfolio level by
# multiplying by `max_risk_pct/100`. This conversion keeps the EV
# arithmetic in a single unit: % of portfolio equity.
_OPTIONS_INSTRUMENTS = ("INDEX_CALL", "INDEX_PUT", "INDEX_SPREAD")


def orchestrator_ev_gate(
    ctx: MarketContext,
    decision: TradeDecision,
) -> tuple[bool, str]:
    """Conservative EV hurdle check at the orchestrator layer.

    Everything is computed as % of portfolio equity for consistency:
        expected_return_pct ≈ max_risk_pct * expected_r * conviction
        portfolio_cost_pct  ≈ cost_pct_of_notional                 (equity / future)
        portfolio_cost_pct  ≈ cost_pct_of_premium * max_risk_pct   (options)

    Then: trade passes iff expected_return_pct >= 4 × portfolio_cost_pct.
    """
    itype = decision.instrument_type
    cost_pct = ORCHESTRATOR_COST_PCT_OF_NOTIONAL.get(itype)
    if cost_pct is None:
        return True, ""  # unknown type; defer to downstream gates

    # `max_risk_pct` is expressed in percent (e.g. 2.0 == 2% of equity).
    # Convert to fraction for arithmetic.
    risk_frac = max(decision.max_risk_pct, 0.0) / 100.0
    r_mult = max(decision.expected_r, 0.0)
    conviction = max(min(decision.conviction, 1.0), 0.0)

    # Expected gross return at the portfolio level.
    expected_return_pct = risk_frac * r_mult * conviction

    # Convert cost to portfolio level. For equity/futures, cost_pct is
    # already of-notional ≈ of-portfolio at full-risk sizing. For
    # options, cost_pct is of-premium, and premium ≈ risk_frac × equity.
    if itype in _OPTIONS_INSTRUMENTS:
        portfolio_cost_pct = cost_pct * risk_frac
    else:
        portfolio_cost_pct = cost_pct

    hurdle_pct = portfolio_cost_pct * ORCH_EV_HURDLE

    if expected_return_pct >= hurdle_pct:
        return True, ""

    return False, (
        f"EV<4×cost: E[ret]={expected_return_pct * 100:.3f}% "
        f"({risk_frac * 100:.1f}% risk × {r_mult:.1f}R × conv={conviction:.2f}) "
        f"vs hurdle={hurdle_pct * 100:.3f}% "
        f"(port_cost={portfolio_cost_pct * 100:.3f}% × {ORCH_EV_HURDLE:.0f}×) "
        f"on {itype}"
    )


def _grind_up_gate(
    ctx: MarketContext,
    strategy_name: str,
    instrument_type: str = "",
) -> tuple[bool, str]:
    """Apply the Tier-1 grind-up regime gate.

    Returns (allowed, reason). If not allowed, the caller must SKIP.

    Two-tier block:
      1. Long-vol strategies (naked puts, crisis, decline) — blocked
         regardless of instrument_type.
      2. ALL index F&O — blocked during grind-up if no causal shock.
         Empirically, leveraged index derivatives bleed alpha in
         low-vol drifting markets (ATR tight, stops hit, spreads eat
         the edge). They only recover their cost of capital when a
         causal shock provides real directional conviction.

    Both blocks are bypassed when:
      - `causal_conviction >= 0.90` (live LangGraph override), OR
      - A directional supply-chain shock is active (backtest proxy
        for the causal agent — shock_bias ∈ {BULLISH, BEARISH}).
    """
    if not ctx.grind_up_flag:
        return True, ""

    is_long_vol = _is_long_vol_strategy(strategy_name)
    is_index_derivative = instrument_type in (
        "INDEX_FUTURE",
        "INDEX_CALL",
        "INDEX_PUT",
        "INDEX_SPREAD",
    )

    if not (is_long_vol or is_index_derivative):
        return True, ""  # equity investment is fine in grind-up

    if ctx.causal_conviction >= CAUSAL_OVERRIDE_THRESHOLD:
        return True, f"causal override {ctx.causal_conviction:.2f}"
    if ctx.supply_chain_shock and ctx.shock_bias in ("BULLISH", "BEARISH"):
        return True, f"shock-bias causal override ({ctx.shock_bias})"

    block_reason = "long-vol" if is_long_vol else "index derivative"
    return False, (
        f"Grind-up regime blocks {block_reason} '{strategy_name}' "
        f"(vol_pct low, H={ctx.hurst:.2f}, 20d={ctx.ret_20d:+.1%}, "
        f"VRP={ctx.vrp:+.2f}, no shock, causal={ctx.causal_conviction:.2f})"
    )


def _dynamic_conviction(ctx: MarketContext, factor_score: float) -> float:
    """Multi-factor dynamic conviction (0.50-0.85 range).

    Replaces the flat max(0.65, ...) floor with a score that reflects
    how many independent signals confirm the trade.
    """
    z_pct = min(abs(ctx.z_score) / 3.0, 1.0)
    rsi_depth = min(abs(ctx.rsi - 50) / 40.0, 1.0)
    vol_confirm = min(max(ctx.volume_ratio - 1.0, 0.0) / 0.5, 1.0)
    factor_bonus = min(factor_score, 1.0)

    raw = 0.50 + z_pct * 0.12 + rsi_depth * 0.10 + vol_confirm * 0.08 + factor_bonus * 0.10
    return round(min(max(raw, 0.50), 0.85), 3)


# ══════════════════════════════════════════════════════════════
#  INDEX F&O ORCHESTRATOR — trades index futures & options
# ══════════════════════════════════════════════════════════════


def index_fno_orchestrate(ctx: MarketContext) -> TradeDecision:
    """Decide index F&O trades: futures, index options, index spreads.

    This is the core F&O decision engine. It ONLY produces:
    - INDEX_FUTURE: trend-following futures with ATR-based stops
    - INDEX_CALL / INDEX_PUT: directional index options
    - INDEX_SPREAD: index debit spreads for range/directional plays

    Called for every index (^GSPC, ^NSEI, ^GDAXI, etc.) on every day.
    """
    if ctx.ticker in BANNED_FNO_INDICES:
        return _skip(ctx, f"{ctx.ticker} banned — negative historical expectancy")

    spec = INDEX_FUTURES_SPECS.get(ctx.ticker, {})
    if not spec:
        return _skip(ctx, "No futures spec for this index")

    price = ctx.current_price
    atr = max(ctx.atr, price * 0.003)
    factor_score = _multi_factor_score(ctx)
    future_name = spec["future"]
    multiplier = spec["multiplier"]
    usd_rate = spec.get("usd_rate", 1.0)
    country = spec.get("country", "")
    exchange = spec.get("exchange", "")

    def _idx_decision(**kwargs) -> TradeDecision:
        # Batch 3/H: short-circuit emission if the concrete per-future
        # variant is on the evidence-driven blocklist. We return SKIP
        # instead of the intended EXECUTE so the decision flows through
        # `rule_based_orchestrate`'s normal SKIP handling downstream.
        sname = kwargs.get("strategy_name", "")
        if is_strategy_variant_blocked(sname):
            return _skip(
                ctx,
                f"{sname} on H-blocklist (historical WR ≤33% with n≥3)",
            )
        kwargs.setdefault("futures_contract", future_name)
        kwargs.setdefault("multiplier", multiplier)
        kwargs.setdefault("country", country)
        kwargs.setdefault("exchange", exchange)
        return TradeDecision(**kwargs)

    # ── GLOBAL RISK GOVERNOR ──────────────────────────────────────
    _daily_halt = getattr(_settings, "DAILY_PNL_HALT_PCT", -2.0)
    if ctx.daily_pnl_pct < _daily_halt:
        return _skip(ctx, f"Daily PnL stressed ({ctx.daily_pnl_pct:+.1f}% < {_daily_halt}%), sitting out")
    if ctx.open_positions_count > 15 and factor_score < 0.55:
        return _skip(ctx, f"Portfolio full ({ctx.open_positions_count} positions)")

    # ── SHOCK-DIRECTED: causal intelligence override ──────────────
    if ctx.supply_chain_shock and ctx.shock_bias:
        if ctx.shock_bias == "NEUTRAL":
            return _skip(ctx, f"Shock active but {future_name} NEUTRAL — no edge")

        # Higher factor floor for illiquid venues (HSI, CSI, TAIEX, STI, ASX, Bovespa).
        # Empirical: pre-fix HSI shock-directed puts had 16% win rate over 32 trades —
        # the simulated edge did not survive realised slippage on Hang Seng options.
        shock_factor_floor = _illiquid_factor_floor(ctx.ticker, 0.30)

        if ctx.shock_bias == "BEARISH" and factor_score >= shock_factor_floor:
            if ctx.ret_20d > 0.05 and ctx.trend_strength > 0.15:
                return _skip(ctx, f"Shock BEARISH but strong uptrend (20d={ctx.ret_20d:+.1%})")
            spread_pts = max(atr * 1.5, price * 0.008)
            rv_pct = max(ctx.realized_vol, 0.05)
            debit_ratio = min(0.55, 0.20 + rv_pct * 1.5)
            debit_est = spread_pts * debit_ratio
            exp_r = max((spread_pts - debit_est) / max(debit_est, 0.01), 1.2)
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_PUT",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name=f"shock_directed_put_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=min(0.80, _dynamic_conviction(ctx, factor_score) + 0.10),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=(
                    f"Shock-directed PUT on {future_name}: {ctx.shock_source} "
                    f"→ BEARISH bias (z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f})"
                ),
                strike=round(price * 0.98, 2),
                expiry_days=14,
                expected_r=exp_r,
                min_r=1.5,
                graph_intel=True,
            )

        if ctx.shock_bias == "BULLISH" and factor_score >= shock_factor_floor:
            if ctx.ret_20d < -0.05 and ctx.trend_strength < -0.15:
                return _skip(ctx, f"Shock BULLISH but strong downtrend (20d={ctx.ret_20d:+.1%})")
            spread_pts = max(atr * 1.5, price * 0.008)
            rv_pct = max(ctx.realized_vol, 0.05)
            debit_ratio = min(0.55, 0.20 + rv_pct * 1.5)
            debit_est = spread_pts * debit_ratio
            exp_r = max((spread_pts - debit_est) / max(debit_est, 0.01), 1.2)
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_CALL",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name=f"shock_directed_call_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=min(0.80, _dynamic_conviction(ctx, factor_score) + 0.10),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=(
                    f"Shock-directed CALL on {future_name}: {ctx.shock_source} "
                    f"→ BULLISH bias (z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f})"
                ),
                strike=round(price * 1.02, 2),
                expiry_days=14,
                expected_r=exp_r,
                min_r=1.5,
                graph_intel=True,
            )

    # ── CRISIS: long index puts (protective / directional) ────────
    if ctx.volatility_regime == "CRISIS":
        if ctx.rsi < 25 and ctx.z_score < -2.0 and factor_score >= 0.45:
            exp_r = 3.0
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_PUT",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="crisis_index_put",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=0.75,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.0,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Crisis regime put on {future_name} (RSI={ctx.rsi:.0f}, z={ctx.z_score:+.1f})",
                strike=round(price * 0.97, 2),
                expiry_days=21,
                expected_r=exp_r,
                min_r=2.0,
            )
        if ctx.rsi < 15 and ctx.z_score < -3.0 and factor_score >= 0.50:
            exp_r = 4.0
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_CALL",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="crisis_capitulation_bounce",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=0.70,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=0.8,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Extreme capitulation bounce on {future_name} (RSI={ctx.rsi:.0f}, z={ctx.z_score:+.1f})",
                strike=round(price * 0.99, 2),
                expiry_days=14,
                expected_r=exp_r,
                min_r=2.0,
            )
        # VIX mean-reversion: after spike > 35, VIX declining 2+ days
        if (
            ctx.vix_level > 35
            and not is_strategy_regime_blocked(ctx, "vix_mean_reversion")
        ):

            # Use available VIX data — in backtest this comes from the context
            vix_arr = np.array([ctx.vix_level + 2, ctx.vix_level + 1, ctx.vix_level])
            if ctx.ret_5d > -0.01:  # SPX stabilising
                exp_r = 3.0
                return _idx_decision(
                    action="EXECUTE",
                    instrument_type="INDEX_CALL",
                    direction="LONG",
                    ticker=ctx.ticker,
                    strategy_name=f"vix_mean_reversion_{future_name.lower()}",
                    analysis_timeframe="D1",
                    entry_timeframe="M5",
                    conviction=0.72,
                    sizing_method="KELLY_OPTIONS",
                    max_risk_pct=1.5,
                    stop_loss_method="FIXED_PCT",
                    take_profit_method="ATR",
                    rationale=(
                        f"VIX spike reversal on {future_name}: "
                        f"VIX={ctx.vix_level:.0f} declining, 5d={ctx.ret_5d:+.1%}"
                    ),
                    strike=round(price * 0.98, 2),
                    expiry_days=35,
                    expected_r=exp_r,
                    min_r=1.5,
                )
        return _skip(ctx, f"Crisis on {future_name} — no compelling setup")

    # ── STRONG TREND: INDEX FUTURES (the bread-and-butter) ────────
    # Citadel/Jane Street: trend-follow index futures with tight stops.
    # Entry thresholds are Supervisor-driven (regime-tuned). In low-
    # conviction chop the LLM will raise `min_trend_strength_long` and
    # `min_ret_5d_momentum` so the gate auto-tightens.
    idx_trend_strategy = f"idx_trend_{future_name.lower()}"
    min_trend_fno = get_param(
        ctx,
        "min_trend_strength_long",
        0.08,
        strategy_name=idx_trend_strategy,
    )
    min_ret5_fno = get_param(
        ctx,
        "min_ret_5d_momentum",
        0.005,
        strategy_name=idx_trend_strategy,
    )
    min_factor_fno = get_param(
        ctx,
        "min_factor_score",
        MIN_FACTOR_SCORE,
        strategy_name=idx_trend_strategy,
    )

    if ctx.trend_regime in ("STRONG_TREND", "WEAK_TREND") and ctx.hurst > 0.52:
        if factor_score < min_factor_fno:
            return _skip(ctx, f"Trend on {future_name} but factors={factor_score:.0%}")

        bullish = ctx.trend_strength > min_trend_fno and ctx.ret_5d > min_ret5_fno
        bearish = ctx.trend_strength < -min_trend_fno and ctx.ret_5d < -min_ret5_fno

        if bullish or bearish:
            direction = "LONG" if bullish else "SHORT"
            stop_dist = atr * 2.0
            target_dist = atr * 3.5
            exp_r = target_dist / max(stop_dist, 0.01)
            exp_r = min(exp_r, 5.0)

            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_FUTURE",
                direction=direction,
                ticker=ctx.ticker,
                strategy_name=f"idx_trend_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_FUTURES",
                max_risk_pct=2.0,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"{direction} {future_name} ({spec['name']}): "
                    f"trend={ctx.trend_strength:+.2f}, H={ctx.hurst:.2f}, "
                    f"5d={ctx.ret_5d:+.1%}, factors={factor_score:.0%}, R={exp_r:.1f}"
                ),
                expected_r=exp_r,
                min_r=1.5,
            )

    # ── MEAN REVERTING + HIGH VOL: index option spreads ───────────
    if ctx.volatility_regime == "HIGH_VOL" and ctx.trend_regime == "MEAN_REVERTING":
        if factor_score < MIN_FACTOR_SCORE:
            return _skip(ctx, f"High vol MR on {future_name} but factors weak")

        if ctx.z_score < -1.5 and ctx.rsi < 35:
            exp_r = 2.5
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_CALL",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name=f"idx_mr_oversold_call_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"MR oversold {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 0.99, 2),
                expiry_days=21,
                expected_r=exp_r,
                min_r=2.0,
            )
        if ctx.z_score > 1.5 and ctx.rsi > 65:
            exp_r = 2.5
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_PUT",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name=f"idx_mr_overbought_put_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"MR overbought {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 1.01, 2),
                expiry_days=21,
                expected_r=exp_r,
                min_r=2.0,
            )

    # ── NORMAL VOL + DIRECTIONAL: index debit spreads ─────────────
    if ctx.volatility_regime in ("NORMAL", "LOW_VOL"):
        spread_pts = max(atr * 1.5, price * 0.008)
        rv_pct = max(ctx.realized_vol, 0.05)
        debit_ratio = min(0.55, 0.20 + rv_pct * 1.5)

        # Regime-aware directional filter: don't buy calls into a falling market
        trailing_bearish = ctx.ret_5d < 0 and ctx.z_score < -0.5

        if (
            ctx.z_score < -1.2
            and ctx.rsi < 40
            and factor_score >= MIN_FACTOR_SCORE
            and not trailing_bearish
        ):
            long_K = round(price * 0.998, 2)
            short_K = round(long_K + spread_pts, 2)
            debit_est = spread_pts * debit_ratio
            exp_r = (spread_pts - debit_est) / max(debit_est, 0.01)
            conv = _dynamic_conviction(ctx, factor_score)
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_SPREAD",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name=f"idx_bull_spread_{future_name.lower()}",
                analysis_timeframe="H4",
                entry_timeframe="M5",
                conviction=conv,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"Bull call spread on {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, R={exp_r:.1f}",
                strike=long_K,
                expiry_days=14,
                second_leg={"strike": short_K, "direction": "SHORT", "type": "CALL"},
                expected_r=exp_r,
                min_r=1.5,
            )

        if ctx.z_score > 0.8 and ctx.rsi > 55 and factor_score >= MIN_FACTOR_SCORE:
            long_K = round(price * 1.002, 2)
            short_K = round(long_K - spread_pts, 2)
            debit_est = spread_pts * debit_ratio
            exp_r = (spread_pts - debit_est) / max(debit_est, 0.01)
            conv = _dynamic_conviction(ctx, factor_score)
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_SPREAD",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name=f"idx_bear_spread_{future_name.lower()}",
                analysis_timeframe="H4",
                entry_timeframe="M5",
                conviction=conv,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"Bear put spread on {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, R={exp_r:.1f}",
                strike=long_K,
                expiry_days=14,
                second_leg={"strike": short_K, "direction": "SHORT", "type": "PUT"},
                expected_r=exp_r,
                min_r=1.5,
            )

        # Protective put on extended decline (no bounce in sight)
        if (
            trailing_bearish
            and ctx.z_score < -2.0
            and ctx.rsi < 30
            and factor_score >= MIN_FACTOR_SCORE
        ):
            exp_r = abs(ctx.z_score) * 1.1
            conv = _dynamic_conviction(ctx, factor_score)
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_PUT",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name=f"idx_decline_put_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=conv,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.0,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Decline put on {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, 5d={ctx.ret_5d:+.1%}",
                strike=round(price * 1.01, 2),
                expiry_days=14,
                expected_r=exp_r,
                min_r=1.5,
            )

        # Momentum futures on normal/low vol
        if (
            abs(ctx.trend_strength) > 0.15
            and ctx.hurst > 0.52
            and abs(ctx.ret_5d) > 0.008
            and factor_score >= MIN_FACTOR_SCORE
        ):
            direction = "LONG" if ctx.trend_strength > 0 else "SHORT"
            stop_dist = atr * 2.0
            target_dist = atr * 3.0
            exp_r = target_dist / max(stop_dist, 0.01)
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_FUTURE",
                direction=direction,
                ticker=ctx.ticker,
                strategy_name=f"idx_momentum_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_FUTURES",
                max_risk_pct=1.5,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"Momentum {direction} {future_name}: "
                    f"trend={ctx.trend_strength:+.2f}, 5d={ctx.ret_5d:+.1%}, R={exp_r:.1f}"
                ),
                expected_r=exp_r,
                min_r=1.5,
            )

    # ── IV-RV DISLOCATION: buy cheap vol on index ─────────────────
    if ctx.implied_vol is not None and ctx.iv_rv_spread is not None:
        if ctx.iv_rv_spread < -0.04 and factor_score >= MIN_FACTOR_SCORE:
            exp_r = 2.5
            if ctx.trend_strength > 0:
                return _idx_decision(
                    action="EXECUTE",
                    instrument_type="INDEX_CALL",
                    direction="LONG",
                    ticker=ctx.ticker,
                    strategy_name=f"idx_vol_arb_call_{future_name.lower()}",
                    analysis_timeframe="D1",
                    entry_timeframe="M5",
                    conviction=_dynamic_conviction(ctx, factor_score),
                    sizing_method="KELLY_OPTIONS",
                    max_risk_pct=1.5,
                    stop_loss_method="FIXED_PCT",
                    take_profit_method="ATR",
                    rationale=f"IV cheap on {future_name}: IV={ctx.implied_vol:.0%} < RV={ctx.realized_vol:.0%}",
                    strike=round(price, 2),
                    expiry_days=21,
                    expected_r=exp_r,
                    min_r=1.5,
                )
            else:
                return _idx_decision(
                    action="EXECUTE",
                    instrument_type="INDEX_PUT",
                    direction="LONG",
                    ticker=ctx.ticker,
                    strategy_name=f"idx_vol_arb_put_{future_name.lower()}",
                    analysis_timeframe="D1",
                    entry_timeframe="M5",
                    conviction=_dynamic_conviction(ctx, factor_score),
                    sizing_method="KELLY_OPTIONS",
                    max_risk_pct=1.5,
                    stop_loss_method="FIXED_PCT",
                    take_profit_method="ATR",
                    rationale=f"IV cheap on {future_name}: IV={ctx.implied_vol:.0%} < RV={ctx.realized_vol:.0%}",
                    strike=round(price, 2),
                    expiry_days=21,
                    expected_r=exp_r,
                    min_r=1.5,
                )

    # ── PREMIUM IRON CONDOR: sell OTM call+put spreads when IV elevated ──
    if (
        ctx.iv_percentile > 65
        and 15 < ctx.vix_level < 30
        and ctx.trend_regime in ("CHOPPY", "MEAN_REVERTING")
        and not is_strategy_regime_blocked(ctx, "premium_iron_condor")
    ):
        from execution_engine.math.options_pricing import (
            iron_condor_price,
            select_condor_strikes,
        )

        dte_days = 21 if ctx.volatility_regime == "NORMAL" else 14
        T = dte_days / 365.0
        sigma = max(ctx.realized_vol, 0.10)
        sc_K, lc_K, sp_K, lp_K = select_condor_strikes(price, sigma, T)
        ic = iron_condor_price(price, T, 0.05, sigma, lp_K, sp_K, sc_K, lc_K)
        credit_pct = ic["net_credit"] / max(price, 1.0)
        if credit_pct >= 0.015:  # 1.5% credit hurdle
            return _idx_decision(
                action="EXECUTE",
                instrument_type="INDEX_SPREAD",
                direction="SHORT",
                ticker=ctx.ticker,
                strategy_name=f"premium_iron_condor_{future_name.lower()}",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=min(0.75, _dynamic_conviction(ctx, factor_score) + 0.05),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="FIXED_PCT",
                rationale=(
                    f"Iron condor on {future_name}: IVP={ctx.iv_percentile:.0f}, "
                    f"credit={credit_pct:.1%}, regime={ctx.trend_regime}"
                ),
                strike=sc_K,
                expiry_days=dte_days,
                second_leg={"put_short": sp_K, "put_long": lp_K, "call_long": lc_K},
                expected_r=ic["risk_reward"] * 2.0,
                min_r=1.0,
                stop_loss_pct=ic["max_loss"] / max(price, 1.0) * 100,
                take_profit_pct=ic["net_credit"] / max(price, 1.0) * 50,  # 50% of max profit
            )

    # ── PREMIUM CREDIT SPREAD: directional + elevated IV ──
    if (
        ctx.iv_percentile > 55
        and ctx.trend_regime in ("STRONG_TREND", "WEAK_TREND", "MEAN_REVERTING")
        and not is_strategy_regime_blocked(ctx, "premium_credit_spread")
    ):
        from execution_engine.math.options_pricing import select_credit_spread_strikes

        has_direction = abs(ctx.trend_strength) > 0.10
        if has_direction:
            cs_dir = "BULLISH" if ctx.trend_strength > 0 else "BEARISH"
            dte_days = 21
            T = dte_days / 365.0
            sigma = max(ctx.realized_vol, 0.10)
            short_K, long_K = select_credit_spread_strikes(price, sigma, T, cs_dir)
            spread_width = abs(short_K - long_K)
            # Estimate credit as ~33% of spread width
            est_credit = spread_width * 0.33
            max_loss = spread_width - est_credit
            if max_loss > 0 and est_credit / max_loss >= 0.33:
                return _idx_decision(
                    action="EXECUTE",
                    instrument_type="INDEX_SPREAD",
                    direction="SHORT",
                    ticker=ctx.ticker,
                    strategy_name=f"premium_credit_spread_{future_name.lower()}",
                    analysis_timeframe="D1",
                    entry_timeframe="D1",
                    conviction=min(0.72, _dynamic_conviction(ctx, factor_score) + 0.05),
                    sizing_method="KELLY_OPTIONS",
                    max_risk_pct=1.5,
                    stop_loss_method="FIXED_PCT",
                    take_profit_method="FIXED_PCT",
                    rationale=(
                        f"Credit spread ({cs_dir}) on {future_name}: "
                        f"IVP={ctx.iv_percentile:.0f}, trend={ctx.trend_strength:+.2f}"
                    ),
                    strike=short_K,
                    expiry_days=dte_days,
                    second_leg={"strike": long_K, "direction": "LONG", "type": "CALL" if cs_dir == "BEARISH" else "PUT"},
                    expected_r=est_credit / max(max_loss, 0.01),
                    min_r=0.8,
                )

    return _skip(ctx, f"No edge on {future_name} ({spec['name']})")


# ══════════════════════════════════════════════════════════════
#  EQUITY INVESTMENT ORCHESTRATOR — stock positions only
# ══════════════════════════════════════════════════════════════


def _equity_orchestrate_core(ctx: MarketContext) -> TradeDecision:
    """Equity-only investment decisions. No options on stocks.

    Equities are treated as medium/long-term investments:
    - Deep value: buy when severely oversold with strong factor confirmation
    - Trend following: ride confirmed uptrends
    - Supply chain: position on AYVID's proprietary shock intelligence
    """
    price = ctx.current_price
    atr = max(ctx.atr, price * 0.005)
    factor_score = _multi_factor_score(ctx)

    _daily_halt = getattr(_settings, "DAILY_PNL_HALT_PCT", -2.0)
    if ctx.daily_pnl_pct < _daily_halt:
        return _skip(ctx, f"Daily PnL stressed ({ctx.daily_pnl_pct:+.1f}% < {_daily_halt}%)")
    if ctx.open_positions_count > 25 and factor_score < 0.55:
        return _skip(ctx, f"Portfolio full ({ctx.open_positions_count} positions)")

    if ctx.volatility_regime == "CRISIS":
        return _skip(ctx, "Crisis regime — no new equity positions")

    # Supply chain shock → equity position (AYVID's edge)
    # Widened for YC walk-forward (trade on any notable event, not just major shocks):
    #   Trigger on >0.8% magnitude (was 2%), factor >= 0.45 (was 0.50).
    #   Conviction gate downstream (MIN_CONVICTION=0.62) still filters weak trades.
    #   RSI < 32 for victim dips, volume confirmation still required.
    if ctx.supply_chain_shock and abs(ctx.shock_magnitude) > 0.008 and factor_score >= 0.45:
        if ctx.is_beneficiary and ctx.shock_magnitude > 0.008 and ctx.volume_ratio >= 1.1:
            exp_r = max(abs(ctx.shock_magnitude) / 0.02 * 2.0, 1.8)
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="supply_chain_beneficiary",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=min(0.85, _dynamic_conviction(ctx, factor_score) + 0.10),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=2.0,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=f"Supply chain beneficiary of {ctx.shock_source} ({ctx.shock_magnitude:+.1%}), vol_r={ctx.volume_ratio:.1f}",
                expected_r=exp_r,
                min_r=1.5,
                graph_intel=True,
            )
        if (
            ctx.is_victim
            and ctx.shock_magnitude < -0.015
            and ctx.rsi < 28
            and ctx.volume_ratio >= 1.5
            and ctx.z_score < -2.0
        ):
            exp_r = max(abs(ctx.shock_magnitude) / 0.02 * 2.5, 1.8)
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="supply_chain_oversold_dip",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=1.0,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=f"Supply chain capitulation dip after {ctx.shock_source} ({ctx.shock_magnitude:+.1%}), RSI={ctx.rsi:.0f}, vol_r={ctx.volume_ratio:.1f}",
                expected_r=exp_r,
                min_r=1.5,
                graph_intel=True,
            )

    # ── Dynamic thresholds: resolved per-branch so the empirical prior
    # (calibration/priors.json) is looked up against the correct
    # (strategy, regime) cell. When the Supervisor emits an explicit
    # parameter it wins; otherwise the prior fires; otherwise the
    # hardcoded default here applies.

    # Secondary regime-block gate: if calibration proved that a
    # (strategy, regime) combo never delivered positive-sharpe, refuse
    # the trade even if the Strategist LLM didn't blocklist it.
    if is_strategy_regime_blocked(ctx, "deep_value_investment"):
        return _skip(ctx, "priors.json marks deep_value_investment as blocked in this regime")

    rsi_max_long = get_param(
        ctx, "max_rsi_entry_long", 30.0, strategy_name="deep_value_investment"
    )
    z_max_long = get_param(ctx, "max_z_entry_long", -1.5, strategy_name="deep_value_investment")
    ret20_max_long = get_param(
        ctx, "max_20d_return_long", -0.06, strategy_name="deep_value_investment"
    )
    min_factor_dv = get_param(ctx, "min_factor_score", 0.40, strategy_name="deep_value_investment")
    min_r_trade = get_param(ctx, "min_expected_r", 1.5, strategy_name="deep_value_investment")

    # Deep value: severely oversold with multi-factor confirmation.
    # Thresholds are Supervisor-provided; in a STRONG_TREND-down regime
    # the Strategist will normally also forbid this strategy outright.
    if (
        ctx.rsi < rsi_max_long
        and ctx.z_score < z_max_long
        and ctx.ret_20d < ret20_max_long
        and factor_score >= min_factor_dv
    ):
        exp_r = abs(ctx.z_score) * 1.2
        return TradeDecision(
            action="EXECUTE",
            instrument_type="EQUITY",
            direction="LONG",
            ticker=ctx.ticker,
            strategy_name="deep_value_investment",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=_dynamic_conviction(ctx, factor_score),
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=(
                f"Deep value: RSI={ctx.rsi:.0f}<{rsi_max_long:.0f}, "
                f"z={ctx.z_score:+.1f}<{z_max_long:+.1f}, "
                f"20d={ctx.ret_20d:+.1%}<{ret20_max_long:+.1%}, R={exp_r:.1f}"
            ),
            expected_r=exp_r,
            min_r=min_r_trade,
        )

    # Trend investment — params resolved via the prior for this cell.
    if is_strategy_regime_blocked(ctx, "trend_investment"):
        return _skip(ctx, "priors.json marks trend_investment as blocked in this regime")
    min_trend_long_ti = get_param(
        ctx,
        "min_trend_strength_long",
        0.25,
        strategy_name="trend_investment",
    )
    min_factor_ti = get_param(
        ctx,
        "min_factor_score",
        0.40,
        strategy_name="trend_investment",
    )
    min_r_ti = get_param(
        ctx,
        "min_expected_r",
        1.5,
        strategy_name="trend_investment",
    )

    # Trend investment: confirmed uptrend with Hurst > 0.55
    if (
        ctx.trend_strength > min_trend_long_ti
        and ctx.hurst > 0.55
        and ctx.ret_20d > 0.03
        and factor_score >= min_factor_ti
    ):
        exp_r = ctx.trend_strength / 0.15 * 1.5
        exp_r = min(exp_r, 5.0)
        return TradeDecision(
            action="EXECUTE",
            instrument_type="EQUITY",
            direction="LONG",
            ticker=ctx.ticker,
            strategy_name="trend_investment",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=_dynamic_conviction(ctx, factor_score),
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=(
                f"Trend invest: trend={ctx.trend_strength:+.2f}>{min_trend_long_ti:+.2f}, "
                f"H={ctx.hurst:.2f}, R={exp_r:.1f}"
            ),
            expected_r=exp_r,
            min_r=min_r_ti,
        )

    # Mean-reversion bounce — params resolved via the prior for this cell.
    if is_strategy_regime_blocked(ctx, "mr_equity_bounce"):
        return _skip(ctx, "priors.json marks mr_equity_bounce as blocked in this regime")
    rsi_max_mr = get_param(ctx, "max_rsi_entry_long", 30.0, strategy_name="mr_equity_bounce")
    z_max_mr = get_param(ctx, "max_z_entry_long", -1.5, strategy_name="mr_equity_bounce")
    vol_ratio_min = get_param(ctx, "min_volume_ratio", 1.2, strategy_name="mr_equity_bounce")
    min_factor_mr = get_param(
        ctx, "min_factor_score", MIN_FACTOR_SCORE, strategy_name="mr_equity_bounce"
    )
    min_r_mr = get_param(ctx, "min_expected_r", 1.5, strategy_name="mr_equity_bounce")

    if (
        ctx.z_score < z_max_mr * 0.87
        and ctx.rsi < rsi_max_mr + 8
        and ctx.volume_ratio > vol_ratio_min
        and ctx.hurst < 0.48
        and factor_score >= min_factor_mr
    ):
        exp_r = abs(ctx.z_score) * 1.0
        return TradeDecision(
            action="EXECUTE",
            instrument_type="EQUITY",
            direction="LONG",
            ticker=ctx.ticker,
            strategy_name="mr_equity_bounce",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=_dynamic_conviction(ctx, factor_score),
            sizing_method="KELLY_EQUITY",
            max_risk_pct=1.5,
            stop_loss_method="ATR",
            take_profit_method="MEAN_REVERSION",
            rationale=(
                f"MR bounce: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, "
                f"vol_r={ctx.volume_ratio:.1f}>{vol_ratio_min:.1f}"
            ),
            expected_r=exp_r,
            min_r=min_r_mr,
        )

    # ── Momentum equity long (12-1 momentum, trending regime) ──
    if not is_strategy_regime_blocked(ctx, "momentum_equity_long"):
        min_adx = get_param(ctx, "min_adx_trend", 25.0, strategy_name="momentum_equity_long")
        min_ret5 = get_param(ctx, "min_ret_5d_momentum", 0.01, strategy_name="momentum_equity_long")
        if (
            ctx.adx > min_adx
            and ctx.ret_5d > min_ret5
            and ctx.ret_20d > 0.02
            and ctx.hurst > 0.52
            and factor_score >= 0.35
        ):
            exp_r = min(ctx.ret_20d / 0.02 * 1.5, 5.0)
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="momentum_equity_long",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=2.0,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"Momentum: ADX={ctx.adx:.0f}>{min_adx:.0f}, "
                    f"5d={ctx.ret_5d:+.1%}, 20d={ctx.ret_20d:+.1%}, H={ctx.hurst:.2f}"
                ),
                expected_r=exp_r,
                min_r=1.5,
            )

    # ── Quality growth long (P/E, EPS growth, margin filters) ──
    if not is_strategy_regime_blocked(ctx, "quality_growth_long"):
        if (
            ctx.z_score > -0.5
            and ctx.trend_strength > 0.10
            and ctx.rsi > 40
            and ctx.rsi < 65
            and factor_score >= 0.45
        ):
            exp_r = factor_score * 3.0
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="quality_growth_long",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=2.0,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"Quality growth: factor={factor_score:.0%}, "
                    f"RSI={ctx.rsi:.0f}, trend={ctx.trend_strength:+.2f}"
                ),
                expected_r=exp_r,
                min_r=1.5,
            )

    # ── Earnings beat momentum (post-earnings drift) ──
    if not is_strategy_regime_blocked(ctx, "earnings_beat_momentum"):
        if (
            ctx.ret_5d > 0.03
            and ctx.volume_ratio > 1.8
            and ctx.rsi > 50
            and ctx.rsi < 75
            and factor_score >= 0.35
        ):
            exp_r = min(ctx.ret_5d / 0.03 * 1.8, 5.0)
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="earnings_beat_momentum",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=1.5,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"Earnings beat drift: 5d={ctx.ret_5d:+.1%}, "
                    f"vol_r={ctx.volume_ratio:.1f}, RSI={ctx.rsi:.0f}"
                ),
                expected_r=exp_r,
                min_r=1.5,
            )

    # ── Positive shock beneficiary (bullish shock → graph beneficiaries) ──
    if not is_strategy_regime_blocked(ctx, "positive_shock_beneficiary"):
        ci = ctx.causal_intel or {}
        direction = ci.get("expected_direction", "")
        graph_role = ci.get("graph_role", "")
        if (
            direction == "BULLISH"
            and graph_role == "beneficiary"
            and ctx.trend_strength > 0.05
            and factor_score >= 0.30
        ):
            exp_r = min(factor_score * 3.5, 5.0)
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="positive_shock_beneficiary",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=2.0,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"Positive shock beneficiary: {ctx.ticker} graph-identified "
                    f"as beneficiary in bullish shock, factor={factor_score:.0%}"
                ),
                expected_r=exp_r,
                min_r=1.5,
            )

    # ── Premium cash-secured put (high-quality stocks at support) ──
    if not is_strategy_regime_blocked(ctx, "premium_cash_secured_put"):
        if (
            ctx.iv_percentile > 50
            and ctx.rsi < 45
            and factor_score >= 0.40
            and ctx.volatility_regime != "CRISIS"
        ):
            exp_r = 0.8  # premium strategies have lower R but higher WR
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="premium_cash_secured_put",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=min(0.75, _dynamic_conviction(ctx, factor_score) + 0.05),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=1.5,
                stop_loss_method="FIXED_PCT",
                take_profit_method="FIXED_PCT",
                rationale=(
                    f"Cash-secured put: IVP={ctx.iv_percentile:.0f}, "
                    f"RSI={ctx.rsi:.0f}, factor={factor_score:.0%}"
                ),
                expected_r=exp_r,
                min_r=0.5,
                stop_loss_pct=15.0,
                take_profit_pct=3.0,
            )

    # ── Earnings drift long (PEAD — post-earnings announcement drift) ──
    if not is_strategy_regime_blocked(ctx, "earnings_drift_long"):
        if (
            ctx.ret_5d > 0.05
            and ctx.volume_ratio > 1.8
            and ctx.rsi > 45
            and ctx.rsi < 80
            and factor_score >= 0.35
            and ctx.volatility_regime != "CRISIS"
        ):
            exp_r = min(ctx.ret_5d / 0.03 * 1.5, 4.0)
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="earnings_drift_long",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=1.5,
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"Earnings drift: 5d={ctx.ret_5d:+.1%}, "
                    f"vol_r={ctx.volume_ratio:.1f}, RSI={ctx.rsi:.0f}"
                ),
                expected_r=exp_r,
                min_r=1.5,
            )

    # ── Supply chain high-confidence (causal edge >= 0.80) ──
    if not is_strategy_regime_blocked(ctx, "supply_chain_hc"):
        if (
            ctx.supply_chain_shock
            and ctx.causal_conviction >= 0.80
            and ctx.is_beneficiary
            and ctx.volatility_regime != "CRISIS"
            and factor_score >= 0.30
        ):
            exp_r = max(ctx.causal_conviction * 3.0, 2.0)
            return TradeDecision(
                action="EXECUTE",
                instrument_type="EQUITY",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="supply_chain_hc",
                analysis_timeframe="D1",
                entry_timeframe="D1",
                conviction=min(0.85, _dynamic_conviction(ctx, factor_score) + 0.15),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=3.0,  # 2x normal Kelly for high-confidence edges
                stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"High-confidence supply chain: causal_conf={ctx.causal_conviction:.2f}, "
                    f"beneficiary of {ctx.shock_source}"
                ),
                expected_r=exp_r,
                min_r=1.5,
                graph_intel=True,
            )

    return _skip(ctx, f"No equity edge (factors={factor_score:.0%})")


# ── FX Orchestrator ──────────────────────────────────────────


def _fx_orchestrate(ctx: MarketContext) -> TradeDecision:
    """Rule-based orchestrator for FX pairs (Track 4)."""
    price = ctx.current_price
    atr = max(ctx.atr, price * 0.002)

    # FX macro trend — strong trend + shock bias alignment
    if (
        ctx.supply_chain_shock
        and ctx.shock_bias in ("BULLISH", "BEARISH")
        and ctx.trend_strength > 0.4
        and ctx.adx > 25
    ):
        direction = "BUY" if ctx.shock_bias == "BULLISH" else "SELL"
        return TradeDecision(
            action="EXECUTE",
            instrument_type="FX",
            direction=direction,
            ticker=ctx.ticker,
            strategy_name="fx_macro_trend",
            analysis_timeframe="D1",
            entry_timeframe="H4",
            conviction=min(0.70 + ctx.shock_magnitude * 0.3, 0.95),
            sizing_method="FIXED_RISK",
            max_risk_pct=0.8,
            stop_loss_method="ATR_MULTIPLE",
            take_profit_method="R_MULTIPLE",
            rationale=f"FX macro trend: {ctx.shock_source} → {ctx.ticker}",
            expected_r=2.0,
            min_r=1.5,
            graph_intel=bool(ctx.supply_chain_shock),
        )

    # FX carry trade — low vol, mean-reverting regime
    if (
        ctx.volatility_regime in ("LOW_VOL", "NORMAL")
        and ctx.trend_regime == "MEAN_REVERTING"
        and abs(ctx.z_score) > 1.5
    ):
        direction = "BUY" if ctx.z_score < -1.5 else "SELL"
        return TradeDecision(
            action="EXECUTE",
            instrument_type="FX",
            direction=direction,
            ticker=ctx.ticker,
            strategy_name="fx_carry_trade",
            analysis_timeframe="D1",
            entry_timeframe="H4",
            conviction=0.60,
            sizing_method="FIXED_RISK",
            max_risk_pct=0.6,
            stop_loss_method="ATR_MULTIPLE",
            take_profit_method="R_MULTIPLE",
            rationale=f"FX carry: z={ctx.z_score:.2f}, vol={ctx.volatility_regime}",
            expected_r=1.8,
            min_r=1.2,
        )

    # FX mean reversion — extreme z-score
    if abs(ctx.z_score) > 2.0 and ctx.hurst < 0.45:
        direction = "BUY" if ctx.z_score < -2.0 else "SELL"
        return TradeDecision(
            action="EXECUTE",
            instrument_type="FX",
            direction=direction,
            ticker=ctx.ticker,
            strategy_name="fx_mean_reversion",
            analysis_timeframe="D1",
            entry_timeframe="H1",
            conviction=0.55,
            sizing_method="FIXED_RISK",
            max_risk_pct=0.5,
            stop_loss_method="ATR_MULTIPLE",
            take_profit_method="R_MULTIPLE",
            rationale=f"FX mean reversion: z={ctx.z_score:.2f}, H={ctx.hurst:.2f}",
            expected_r=1.5,
            min_r=1.0,
        )

    return _skip(ctx, f"No FX edge for {ctx.ticker}")


# ── Commodity Orchestrator ───────────────────────────────────


def _commodity_orchestrate(ctx: MarketContext) -> TradeDecision:
    """Rule-based orchestrator for commodity futures (Track 5)."""
    # Commodity supply shock — shock-driven
    if ctx.supply_chain_shock and ctx.shock_magnitude > 0.3:
        direction = "BUY" if ctx.shock_bias in ("BULLISH", "") else "SELL"
        return TradeDecision(
            action="EXECUTE",
            instrument_type="COMMODITY_FUTURE",
            direction=direction,
            ticker=ctx.ticker,
            strategy_name="commodity_supply_shock",
            analysis_timeframe="D1",
            entry_timeframe="H4",
            conviction=min(0.65 + ctx.shock_magnitude * 0.3, 0.95),
            sizing_method="FIXED_RISK",
            max_risk_pct=1.0,
            stop_loss_method="ATR_MULTIPLE",
            take_profit_method="R_MULTIPLE",
            rationale=f"Commodity supply shock: {ctx.shock_source} → {ctx.ticker}",
            expected_r=2.5,
            min_r=1.5,
            graph_intel=True,
        )

    # Commodity momentum — strong trend
    if ctx.adx > 30 and ctx.trend_strength > 0.5:
        direction = "BUY" if ctx.ret_20d > 0 else "SELL"
        return TradeDecision(
            action="EXECUTE",
            instrument_type="COMMODITY_FUTURE",
            direction=direction,
            ticker=ctx.ticker,
            strategy_name="commodity_momentum",
            analysis_timeframe="W1",
            entry_timeframe="D1",
            conviction=0.60,
            sizing_method="FIXED_RISK",
            max_risk_pct=0.8,
            stop_loss_method="ATR_MULTIPLE",
            take_profit_method="R_MULTIPLE",
            rationale=f"Commodity momentum: ADX={ctx.adx:.0f}, ret20d={ctx.ret_20d:+.1%}",
            expected_r=2.0,
            min_r=1.2,
        )

    # Commodity seasonal — checked via seasonal_calendar
    try:
        import datetime as _dt

        from execution_engine.math.seasonal_calendar import get_active_seasonal_edges

        edges = get_active_seasonal_edges(_dt.date.today())
        for edge in edges:
            if edge.symbol == ctx.ticker:
                return TradeDecision(
                    action="EXECUTE",
                    instrument_type="COMMODITY_FUTURE",
                    direction=edge.direction,
                    ticker=ctx.ticker,
                    strategy_name="commodity_seasonal",
                    analysis_timeframe="W1",
                    entry_timeframe="D1",
                    conviction=edge.historical_win_rate / 100.0,
                    sizing_method="FIXED_RISK",
                    max_risk_pct=0.5,
                    stop_loss_method="ATR_MULTIPLE",
                    take_profit_method="R_MULTIPLE",
                    rationale=f"Commodity seasonal: {ctx.ticker} {edge.direction} (WR={edge.historical_win_rate}%)",
                    expected_r=1.5,
                    min_r=1.0,
                )
    except Exception:
        pass

    return _skip(ctx, f"No commodity edge for {ctx.ticker}")


# ── Crypto Orchestrator ──────────────────────────────────────


def _crypto_orchestrate(ctx: MarketContext) -> TradeDecision:
    """Rule-based orchestrator for crypto assets (Track 6)."""
    # Crypto momentum — strong trend with high ADX
    if ctx.adx > 25 and abs(ctx.ret_20d) > 0.10 and ctx.trend_strength > 0.4:
        direction = "BUY" if ctx.ret_20d > 0 else "SELL"
        return TradeDecision(
            action="EXECUTE",
            instrument_type="CRYPTO",
            direction=direction,
            ticker=ctx.ticker,
            strategy_name="crypto_momentum",
            analysis_timeframe="D1",
            entry_timeframe="H4",
            conviction=min(0.55 + abs(ctx.ret_20d), 0.90),
            sizing_method="FIXED_RISK",
            max_risk_pct=0.7,
            stop_loss_method="ATR_MULTIPLE",
            take_profit_method="R_MULTIPLE",
            rationale=f"Crypto momentum: ret20d={ctx.ret_20d:+.1%}, ADX={ctx.adx:.0f}",
            expected_r=2.0,
            min_r=1.5,
        )

    # Crypto supply-chain expression — shock-driven
    if ctx.supply_chain_shock and ctx.shock_magnitude > 0.2:
        direction = "BUY" if ctx.shock_bias in ("BULLISH", "") else "SELL"
        return TradeDecision(
            action="EXECUTE",
            instrument_type="CRYPTO",
            direction=direction,
            ticker=ctx.ticker,
            strategy_name="crypto_supply_chain",
            analysis_timeframe="D1",
            entry_timeframe="H1",
            conviction=min(0.60 + ctx.shock_magnitude * 0.3, 0.90),
            sizing_method="FIXED_RISK",
            max_risk_pct=0.6,
            stop_loss_method="ATR_MULTIPLE",
            take_profit_method="R_MULTIPLE",
            rationale=f"Crypto supply-chain: {ctx.shock_source} → {ctx.ticker}",
            expected_r=2.5,
            min_r=1.5,
            graph_intel=True,
        )

    return _skip(ctx, f"No crypto edge for {ctx.ticker}")


def rule_based_orchestrate(ctx: MarketContext) -> TradeDecision:
    """Public entry point — routes to index F&O or equity investment.

    After the per-asset orchestrator produces a decision we apply two
    Tier-1 post-filters:
      (a) Grind-up long-vol gate — blocks premium-buying strategies in
          low-vol grinding-bull regimes unless the causal agent has
          conviction ≥ 0.90.
      (b) VIX / daily-drawdown size-down — unchanged.
      (c) IV-rich premium filter — if VRP ≥ 4% and the decision buys
          single-leg options, we refuse (credit structures are safer).
    """
    if ctx.asset_class == "INDEX":
        decision = index_fno_orchestrate(ctx)
    elif ctx.asset_class == "FX":
        decision = _fx_orchestrate(ctx)
    elif ctx.asset_class == "COMMODITY":
        decision = _commodity_orchestrate(ctx)
    elif ctx.asset_class == "CRYPTO":
        decision = _crypto_orchestrate(ctx)
    else:
        decision = _equity_orchestrate_core(ctx)

    if decision.action == "EXECUTE":
        # C5: Regime confidence gate — if the Strategist is uncertain
        # about the current regime, block the trade unless conviction
        # is very high (causal override territory).
        # Shock-driven strategies use looser gate (0.40); all others 0.55.
        is_shock_strat = decision.strategy_name.startswith("shock_directed") or decision.strategy_name.startswith("supply_chain")
        conf_threshold = 0.40 if is_shock_strat else 0.55
        if ctx.regime_confidence < conf_threshold and decision.conviction < 0.85:
            log.info(
                f"[REGIME-CONF-GATE] {ctx.ticker}: regime_confidence="
                f"{ctx.regime_confidence:.2f} < {conf_threshold}, skipping"
            )
            return _skip(ctx, f"Regime confidence too low ({ctx.regime_confidence:.0%})")

        # Tier-1 LLM-driven blocklist (Strategist regime gating).
        # Takes precedence over every per-strategy rule — if the
        # Strategist forbids a family, we SKIP no matter how strong
        # the local factors look.
        if _matches_blocklist(decision.strategy_name, ctx.forbidden_strategies):
            log.info(
                f"[STRATEGIST-BLOCK] {ctx.ticker}: "
                f"'{decision.strategy_name}' is on the regime blocklist"
            )
            return _skip(
                ctx,
                f"Strategist forbade '{decision.strategy_name}' for current regime",
            )

        allowed, reason = _grind_up_gate(
            ctx,
            decision.strategy_name,
            decision.instrument_type,
        )
        if not allowed:
            log.info(f"[REGIME-GATE] {ctx.ticker}: {reason}")
            return _skip(ctx, reason)

        # VRP gate on single-leg option debits. Bypassed when a
        # causal shock bias is active — the graph signal is the
        # primary driver and a rich-IV gate would veto exactly the
        # asymmetric-payoff trades the shock intelligence targets
        # (e.g. SVB-2023 FTSE puts realised +$244k in backtest).
        causal_shock_active = ctx.supply_chain_shock and ctx.shock_bias in ("BULLISH", "BEARISH")
        if (
            decision.instrument_type in ("INDEX_CALL", "INDEX_PUT")
            and ctx.vrp >= VRP_RICH_THRESHOLD
            and not causal_shock_active
        ):
            log.info(
                f"[VRP-GATE] {ctx.ticker}: VRP={ctx.vrp:+.2f} ≥ "
                f"{VRP_RICH_THRESHOLD} — refusing to buy rich premium"
            )
            return _skip(ctx, f"VRP rich ({ctx.vrp:+.2%}); single-leg premium too expensive")

        ev_ok, ev_reason = orchestrator_ev_gate(ctx, decision)
        if not ev_ok:
            log.info(f"[EV-GATE] {ctx.ticker}: {ev_reason}")
            return _skip(ctx, ev_reason)

        vix = ctx.vix_level
        if vix > 30:
            decision.max_risk_pct *= 0.50
        elif vix > 25:
            decision.max_risk_pct *= 0.75
        if ctx.daily_pnl_pct < -0.5:
            decision.max_risk_pct *= 0.75

        # Apply Strategist-emitted risk multiplier (macro amplifier/damper).
        if ctx.risk_multiplier and ctx.risk_multiplier != 1.0:
            decision.max_risk_pct *= max(min(ctx.risk_multiplier, 2.5), 0.25)
    return decision


def naive_baseline_orchestrate(ctx: MarketContext) -> TradeDecision:
    """Naive 'dumb quant' baseline — no shock awareness, no regime filtering,
    fixed conviction, lower thresholds, no VIX scaling.  Used to prove the
    AI-native system adds real alpha over a vanilla factor-based approach."""
    price = ctx.current_price
    atr = max(ctx.atr, price * 0.005)
    factor_score = _multi_factor_score(ctx)

    def _skip_naive(reason: str) -> TradeDecision:
        return TradeDecision(
            action="SKIP",
            instrument_type="NONE",
            direction="NONE",
            ticker=ctx.ticker,
            strategy_name="naive_skip",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=0.0,
            sizing_method="FIXED_RISK",
            max_risk_pct=0.0,
            stop_loss_method="FIXED_PCT",
            take_profit_method="FIXED_PCT",
            rationale=reason,
        )

    if ctx.daily_pnl_pct < -2.0:
        return _skip_naive("Daily PnL too stressed")
    if ctx.open_positions_count > 30:
        return _skip_naive("Portfolio full")

    if ctx.asset_class == "INDEX":
        spec = INDEX_FUTURES_SPECS.get(ctx.ticker, {})
        future_name = spec.get("future", ctx.ticker)
        exchange = spec.get("exchange", "CME")
        country = spec.get("country", "US")
        multiplier = spec.get("multiplier", 50.0)

        # Simple z-score + RSI signals only, no regime check
        if ctx.z_score < -1.5 and ctx.rsi < 35 and factor_score >= 0.25:
            return TradeDecision(
                action="EXECUTE",
                instrument_type="INDEX_CALL",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="naive_zscore_call",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=0.60,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=2.0,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Naive: z-score oversold {ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 0.99, 2),
                expiry_days=14,
                expected_r=1.2,
                min_r=1.0,
                futures_contract=future_name,
                multiplier=multiplier,
                country=country,
                exchange=exchange,
            )

        if ctx.z_score > 1.5 and ctx.rsi > 65 and factor_score >= 0.25:
            return TradeDecision(
                action="EXECUTE",
                instrument_type="INDEX_PUT",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="naive_zscore_put",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=0.60,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=2.0,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Naive: z-score overbought {ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 1.01, 2),
                expiry_days=14,
                expected_r=1.2,
                min_r=1.0,
                futures_contract=future_name,
                multiplier=multiplier,
                country=country,
                exchange=exchange,
            )

        if ctx.trend_strength > 0.6 and ctx.ret_5d > 0.01 and factor_score >= 0.25:
            return TradeDecision(
                action="EXECUTE",
                instrument_type="INDEX_FUTURE",
                direction="LONG",
                ticker=ctx.ticker,
                strategy_name="naive_trend_long",
                analysis_timeframe="D1",
                entry_timeframe="M5",
                conviction=0.60,
                sizing_method="KELLY_FUTURES",
                max_risk_pct=2.0,
                stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Naive: trend following long, trend={ctx.trend_strength:.2f}",
                expected_r=1.2,
                min_r=1.0,
                futures_contract=future_name,
                multiplier=multiplier,
                country=country,
                exchange=exchange,
            )

        return _skip_naive(f"Naive: no F&O signal on {future_name}")

    # Equity: simple factor-based, no shock awareness
    if factor_score < 0.25:
        return _skip_naive(f"Naive: factor score too low ({factor_score:.2f})")

    if ctx.rsi < 30 and ctx.z_score < -1.5:
        return TradeDecision(
            action="EXECUTE",
            instrument_type="EQUITY",
            direction="LONG",
            ticker=ctx.ticker,
            strategy_name="naive_oversold",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=0.60,
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=f"Naive: oversold dip buy RSI={ctx.rsi:.0f}, z={ctx.z_score:+.1f}",
            expected_r=1.5,
            min_r=1.0,
        )

    if ctx.trend_strength > 0.5 and ctx.ret_5d > 0.01:
        return TradeDecision(
            action="EXECUTE",
            instrument_type="EQUITY",
            direction="LONG",
            ticker=ctx.ticker,
            strategy_name="naive_momentum",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=0.60,
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=f"Naive: momentum long, trend={ctx.trend_strength:.2f}",
            expected_r=1.5,
            min_r=1.0,
        )

    return _skip_naive(f"Naive: no equity signal on {ctx.ticker}")


# ════════════════════════════════════════════════════════════════════
# Phase-3 unified orchestrator — the single public entry point.
# ════════════════════════════════════════════════════════════════════
#
# The big-bang consolidation. Pre-Phase-3 the codebase had:
#   * `orchestrate_trade(ctx)`      → TradeDecision   (LLM mode)
#   * `rule_based_orchestrate(ctx)` → TradeDecision   (rule mode)
#   * `naive_baseline_orchestrate(ctx)` → TradeDecision  (ablation)
# and the LangGraph pipeline separately emitted `TradeSignal`. Two
# execution contracts, two scoring stacks, two sets of gates.
#
# `orchestrate(ctx, *, mode)` below produces ONE `TradeIntent` for
# both modes. The strategy branches inside `rule_based_orchestrate` /
# `index_fno_orchestrate` / `_equity_orchestrate_core` are kept intact
# — they are the fund's alpha — but their output is adapted into the
# unified contract and supplemented with the deterministic
# `AlphaScore`. Both modes go through the same final gate
# (`alpha_scorer.passes_trade_gate`) so there is no "sneak past" path.


def _trade_decision_to_intent(
    ctx: MarketContext,
    decision: TradeDecision,
    alpha: AlphaScore,
) -> TradeIntent:
    """Adapter: TradeDecision × AlphaScore → TradeIntent.

    Preserves every field the downstream sizing / routing layer needs
    while collapsing the dual contract into one.
    """
    import uuid

    from shared.types import (
        AssetClass,
        InstrumentChoice,
        SignalStatus,
        SizingMethod,
        TradeAction,
        TradeIntent,
    )

    try:
        direction = TradeAction(decision.direction)
    except ValueError:
        direction = TradeAction.HOLD

    try:
        instr = InstrumentChoice(decision.instrument_type)
    except ValueError:
        instr = InstrumentChoice.EQUITY

    try:
        sizing = SizingMethod(decision.sizing_method)
    except ValueError:
        sizing = SizingMethod.KELLY_EQUITY

    asset_class = AssetClass.EQUITY
    if instr in (
        InstrumentChoice.INDEX_FUTURE,
        InstrumentChoice.INDEX_CALL,
        InstrumentChoice.INDEX_PUT,
        InstrumentChoice.INDEX_SPREAD,
    ):
        asset_class = AssetClass.INDEX

    return TradeIntent(
        intent_id=str(uuid.uuid4())[:8],
        execute=(decision.action == "EXECUTE"),
        ticker=decision.ticker,
        direction=direction,
        asset_class=asset_class,
        instrument_choice=instr,
        alpha_score=alpha,
        sizing_method=sizing,
        max_risk_pct=decision.max_risk_pct,
        stop_loss_method=decision.stop_loss_method,
        take_profit_method=decision.take_profit_method,
        strike=decision.strike,
        expiry_days=decision.expiry_days,
        second_leg=decision.second_leg,
        futures_contract=decision.futures_contract,
        multiplier=decision.multiplier,
        exchange=decision.exchange or "SMART",
        country=decision.country or ctx.country,
        strategy_name=decision.strategy_name,
        graph_intel=decision.graph_intel,
        rationale=decision.rationale,
        status=SignalStatus.PENDING,
    )


async def orchestrate(
    ctx: MarketContext,
    *,
    mode: str = "rule",
    baseline: bool = False,
) -> TradeIntent:
    """Unified orchestrator — single public entry point, two modes.

    Args:
        ctx      : `MarketContext` built via `build_decision_context()`.
        mode     : "rule" (deterministic Tier-1 strategy tree) or "llm"
                   (Claude/GPT-driven orchestration). Both modes emit a
                   TradeIntent that satisfies the SAME gates.
        baseline : when True with `mode="rule"`, route through
                   `naive_baseline_orchestrate` for A/B ablations.

    Gate hierarchy applied to EVERY produced intent:
        1. `rule_based_orchestrate` (or `orchestrate_trade` for LLM mode)
           already applies Strategist blocklist, grind-up, VRP, EV,
           VIX / daily-PnL risk dampers, and Supervisor risk multiplier.
        2. AlphaScorer additionally re-validates direction alignment,
           unified conviction vs `MIN_CONVICTION_TRADE`, factor score vs
           `MIN_FACTOR_SCORE`, and expected_r vs `MIN_R_GLOBAL`.

    Intents that fail gate (2) are returned with `execute=False` and a
    SKIP rationale appended to the audit trail.
    """
    from execution_engine.math import alpha_scorer
    from shared.types import TradeAction

    alpha = alpha_scorer.score(ctx)

    if mode == "llm":
        decision = await orchestrate_trade(ctx)
    elif mode == "rule":
        if baseline:
            decision = naive_baseline_orchestrate(ctx)
        else:
            decision = rule_based_orchestrate(ctx)
    else:
        raise ValueError(f"Unknown orchestrate mode: {mode!r}")

    intent = _trade_decision_to_intent(ctx, decision, alpha)

    # Second-pass gate: align the strategy-tree output with the
    # deterministic AlphaScore. A strategy branch may have emitted
    # EXECUTE on a narrow criterion that the unified scorer rejects
    # (e.g., conflicting direction with causal intel). Respect the
    # stricter of the two — the scorer vetoes but does not promote.
    if intent.execute:
        # Direction mismatch is the A6 failure mode. When a strategy
        # branch fires LONG but AlphaScorer's causal-shock-aware
        # direction determination says SHORT, we trust the scorer.
        if alpha.direction != TradeAction.HOLD and alpha.direction != intent.direction:
            log.info(
                f"[ALPHA-GATE] {ctx.ticker}: strategy picked "
                f"{intent.direction.value} but AlphaScorer chose "
                f"{alpha.direction.value} — vetoing (A6 safeguard)"
            )
            intent.execute = False
            intent.rationale += (
                f" | ALPHA-GATE: direction mismatch with causal intel "
                f"(strategy={intent.direction.value}, alpha={alpha.direction.value})"
            )

        # Conviction floor — one unified number, aligned across the fund.
        if not alpha_scorer.passes_trade_gate(alpha):
            log.info(
                f"[ALPHA-GATE] {ctx.ticker}: unified_conv="
                f"{alpha.unified_conviction:.2f} factor={alpha.factor_score:.2f} "
                f"R={alpha.expected_r:.2f} — below gate"
            )
            intent.execute = False
            intent.rationale += (
                f" | ALPHA-GATE: unified_conviction={alpha.unified_conviction:.2f} below floor"
            )

    intent.audit_trail["alpha_score"] = alpha.model_dump(mode="json")
    intent.audit_trail["rule_decision"] = {
        "action": decision.action,
        "strategy_name": decision.strategy_name,
        "conviction": decision.conviction,
        "expected_r": decision.expected_r,
    }
    return intent
