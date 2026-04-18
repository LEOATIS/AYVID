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
from typing import Any, Literal

from shared.config import settings
from shared.logger import get_logger

log = get_logger("agent.orchestrator")


# ── Index → Futures Contract Specifications ───────────────────
# Real-world contract specs: multiplier, margin, currency, exchange.
# Used by both orchestrator and backtest engine for proper P&L sizing.

INDEX_FUTURES_SPECS: dict[str, dict] = {
    # fut_expiry_rule: "3rd_fri" = 3rd Friday, "last_thu" = last Thursday, "2nd_fri" = 2nd Friday
    # opt_expiry_rule: "weekly_fri" = every Friday, "weekly_thu" = every Thursday, "monthly" = same as futures
    "^GSPC":    {"future": "ES",     "name": "E-mini S&P 500",       "multiplier": 50,  "currency": "USD", "usd_rate": 1.0,    "exchange": "CME",     "country": "US", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "weekly_fri"},
    "^IXIC":    {"future": "NQ",     "name": "E-mini NASDAQ 100",    "multiplier": 20,  "currency": "USD", "usd_rate": 1.0,    "exchange": "CME",     "country": "US", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "weekly_fri"},
    "^DJI":     {"future": "YM",     "name": "E-mini Dow",           "multiplier": 5,   "currency": "USD", "usd_rate": 1.0,    "exchange": "CME",     "country": "US", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "weekly_fri"},
    "^RUT":     {"future": "RTY",    "name": "E-mini Russell 2000",  "multiplier": 50,  "currency": "USD", "usd_rate": 1.0,    "exchange": "CME",     "country": "US", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "weekly_fri"},
    "^NSEI":    {"future": "NIFTY",  "name": "NIFTY 50 Future",      "multiplier": 75,  "currency": "INR", "usd_rate": 0.012,  "exchange": "NSE",     "country": "IN", "margin_pct": 0.10, "fut_expiry_rule": "last_thu", "opt_expiry_rule": "weekly_thu"},
    "^BSESN":   {"future": "SENSEX", "name": "SENSEX Future",        "multiplier": 15,  "currency": "INR", "usd_rate": 0.012,  "exchange": "BSE",     "country": "IN", "margin_pct": 0.10, "fut_expiry_rule": "last_thu", "opt_expiry_rule": "weekly_fri"},
    "^FTSE":    {"future": "Z",      "name": "FTSE 100 Future",      "multiplier": 10,  "currency": "GBP", "usd_rate": 1.27,   "exchange": "ICE",     "country": "GB", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "monthly"},
    "^GDAXI":   {"future": "FDAX",   "name": "DAX Future",           "multiplier": 25,  "currency": "EUR", "usd_rate": 1.08,   "exchange": "EUREX",   "country": "DE", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "weekly_fri"},
    "^FCHI":    {"future": "FCE",    "name": "CAC 40 Future",        "multiplier": 10,  "currency": "EUR", "usd_rate": 1.08,   "exchange": "EURONEXT","country": "FR", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "monthly"},
    "^STOXX50E":{"future": "FESX",   "name": "EURO STOXX 50 Future", "multiplier": 10,  "currency": "EUR", "usd_rate": 1.08,   "exchange": "EUREX",   "country": "EU", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "weekly_fri"},
    "^N225":    {"future": "NKD",    "name": "Nikkei 225 Future",    "multiplier": 5,   "currency": "USD", "usd_rate": 1.0,    "exchange": "CME",     "country": "JP", "margin_pct": 0.05, "fut_expiry_rule": "2nd_fri",  "opt_expiry_rule": "monthly"},
    "^HSI":     {"future": "HSI",    "name": "Hang Seng Future",     "multiplier": 50,  "currency": "HKD", "usd_rate": 0.128,  "exchange": "HKEX",    "country": "HK", "margin_pct": 0.08, "fut_expiry_rule": "2nd_last_bd", "opt_expiry_rule": "monthly"},
    "^KS11":    {"future": "KOSPI",  "name": "KOSPI 200 Future",     "multiplier": 250000, "currency": "KRW", "usd_rate": 0.00075, "exchange": "KRX", "country": "KR", "margin_pct": 0.08, "fut_expiry_rule": "2nd_thu",  "opt_expiry_rule": "weekly_thu"},
    "^AXJO":    {"future": "AP",     "name": "S&P/ASX 200 Future",   "multiplier": 25,  "currency": "AUD", "usd_rate": 0.65,   "exchange": "ASX",     "country": "AU", "margin_pct": 0.05, "fut_expiry_rule": "3rd_thu",  "opt_expiry_rule": "monthly"},
    "^BVSP":    {"future": "IND",    "name": "Bovespa Future",       "multiplier": 1,   "currency": "BRL", "usd_rate": 0.20,   "exchange": "B3",      "country": "BR", "margin_pct": 0.08, "fut_expiry_rule": "wed_near_15th", "opt_expiry_rule": "monthly"},
    "^SSMI":    {"future": "SMI",    "name": "Swiss Market Future",  "multiplier": 10,  "currency": "CHF", "usd_rate": 1.10,   "exchange": "SIX",     "country": "CH", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "monthly"},
    "^GSPTSE":  {"future": "SXF",    "name": "S&P/TSX 60 Future",   "multiplier": 200, "currency": "CAD", "usd_rate": 0.74,   "exchange": "TMX",     "country": "CA", "margin_pct": 0.05, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "monthly"},
    "000001.SS":{"future": "IF",     "name": "CSI 300 Future",       "multiplier": 300, "currency": "CNY", "usd_rate": 0.14,   "exchange": "CFFEX",   "country": "CN", "margin_pct": 0.12, "fut_expiry_rule": "3rd_fri",  "opt_expiry_rule": "monthly"},
    "^STI":     {"future": "SGX_NK", "name": "SGX Nifty / STI Future","multiplier": 10, "currency": "SGD", "usd_rate": 0.74,   "exchange": "SGX",     "country": "SG", "margin_pct": 0.06, "fut_expiry_rule": "2nd_last_bd", "opt_expiry_rule": "monthly"},
    "^TWII":    {"future": "TX",     "name": "TAIEX Future",         "multiplier": 200, "currency": "TWD", "usd_rate": 0.031,  "exchange": "TAIFEX",  "country": "TW", "margin_pct": 0.07, "fut_expiry_rule": "3rd_wed",  "opt_expiry_rule": "monthly"},
}

TRADEABLE_INDEX_SYMBOLS = list(INDEX_FUTURES_SPECS.keys())


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
    market_breadth: float = 0.5   # 0=bearish, 0.5=neutral, 1=bullish (% stocks above 200MA)
    vix_level: float = 20.0       # current VIX equivalent
    cross_asset_momentum: float = 0.0  # avg momentum across correlated assets
    open_positions_count: int = 0  # total open positions in portfolio
    daily_pnl_pct: float = 0.0    # portfolio daily P&L so far


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
    mtf_str = ", ".join(f"{tf}={d}" for tf, d in ctx.mtf_signals.items()) if ctx.mtf_signals else "N/A"

    instruments = ", ".join(ctx.available_instruments) if ctx.available_instruments else "EQUITY, CALL, PUT, STRADDLE, IRON_CONDOR, FUTURE"

    iv_section = ""
    if ctx.implied_vol is not None:
        iv_section = f"""
Implied Volatility: {ctx.implied_vol:.1%}
IV-RV Spread: {ctx.iv_rv_spread:+.1%} ({'IV expensive' if (ctx.iv_rv_spread or 0) > 0 else 'IV cheap'})"""

    shock_section = ""
    if ctx.supply_chain_shock:
        shock_section = f"""

SUPPLY CHAIN SHOCK DETECTED:
Source: {ctx.shock_source}
Magnitude: {ctx.shock_magnitude:+.1%}
This ticker is a {'direct' if ctx.shock_magnitude > 0.05 else 'indirect'} supply chain link.
AYVID'S EDGE: Express this view through OPTIONS for asymmetric payoff."""

    return f"""MARKET CONTEXT FOR {ctx.ticker}:

Price: ${ctx.current_price:.2f}
Sector: {ctx.sector or 'Unknown'}

REGIME:
Volatility Regime: {ctx.volatility_regime}
Trend Regime: {ctx.trend_regime}
Hurst Exponent: {ctx.hurst:.3f} ({'trending' if ctx.hurst > 0.55 else 'mean-reverting' if ctx.hurst < 0.45 else 'random walk'})
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
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic

    if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        llm = ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=0.05,
            max_tokens=800,
        )
    else:
        llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            api_key=settings.OPENAI_API_KEY,
            temperature=0.05,
            max_tokens=800,
        )

    prompt = _build_context_prompt(ctx)
    response = await llm.ainvoke([
        SystemMessage(content=ORCHESTRATOR_SYSTEM),
        HumanMessage(content=prompt),
    ])

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
            action="SKIP", instrument_type="EQUITY", direction="LONG",
            ticker=ctx.ticker, strategy_name="parse_error",
            analysis_timeframe="D1", entry_timeframe="M5",
            conviction=0.0, sizing_method="FIXED_RISK",
            max_risk_pct=1.0, stop_loss_method="ATR",
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
        action="SKIP", instrument_type="EQUITY", direction="LONG",
        ticker=ctx.ticker, strategy_name="no_edge",
        analysis_timeframe="D1", entry_timeframe="M5",
        conviction=0.0, sizing_method="FIXED_RISK",
        max_risk_pct=1.0, stop_loss_method="ATR",
        take_profit_method="ATR", rationale=reason,
        expected_r=0.0, min_r=0.0,
    )


MIN_FACTOR_SCORE = 0.40
MIN_CONVICTION = 0.62
MIN_R_GLOBAL = 1.5


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
        kwargs.setdefault("futures_contract", future_name)
        kwargs.setdefault("multiplier", multiplier)
        kwargs.setdefault("country", country)
        kwargs.setdefault("exchange", exchange)
        return TradeDecision(**kwargs)

    # ── GLOBAL RISK GOVERNOR ──────────────────────────────────────
    if ctx.daily_pnl_pct < -1.0:
        return _skip(ctx, f"Daily PnL stressed ({ctx.daily_pnl_pct:+.1f}%), sitting out")
    if ctx.open_positions_count > 15 and factor_score < 0.55:
        return _skip(ctx, f"Portfolio full ({ctx.open_positions_count} positions)")

    # ── SHOCK-DIRECTED: causal intelligence override ──────────────
    if ctx.supply_chain_shock and ctx.shock_bias:
        if ctx.shock_bias == "NEUTRAL":
            return _skip(ctx, f"Shock active but {future_name} NEUTRAL — no edge")

        if ctx.shock_bias == "BEARISH" and factor_score >= 0.30:
            spread_pts = max(atr * 1.5, price * 0.008)
            rv_pct = max(ctx.realized_vol, 0.05)
            debit_ratio = min(0.55, 0.20 + rv_pct * 1.5)
            debit_est = spread_pts * debit_ratio
            exp_r = max((spread_pts - debit_est) / max(debit_est, 0.01), 1.2)
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_PUT", direction="LONG",
                ticker=ctx.ticker, strategy_name=f"shock_directed_put_{future_name.lower()}",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=min(0.80, _dynamic_conviction(ctx, factor_score) + 0.10),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=(
                    f"Shock-directed PUT on {future_name}: {ctx.shock_source} "
                    f"→ BEARISH bias (z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f})"
                ),
                strike=round(price * 0.98, 2), expiry_days=14,
                expected_r=exp_r, min_r=1.0,
                graph_intel=True,
            )

        if ctx.shock_bias == "BULLISH" and factor_score >= 0.30:
            spread_pts = max(atr * 1.5, price * 0.008)
            rv_pct = max(ctx.realized_vol, 0.05)
            debit_ratio = min(0.55, 0.20 + rv_pct * 1.5)
            debit_est = spread_pts * debit_ratio
            exp_r = max((spread_pts - debit_est) / max(debit_est, 0.01), 1.2)
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_CALL", direction="LONG",
                ticker=ctx.ticker, strategy_name=f"shock_directed_call_{future_name.lower()}",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=min(0.80, _dynamic_conviction(ctx, factor_score) + 0.10),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=(
                    f"Shock-directed CALL on {future_name}: {ctx.shock_source} "
                    f"→ BULLISH bias (z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f})"
                ),
                strike=round(price * 1.02, 2), expiry_days=14,
                expected_r=exp_r, min_r=1.0,
                graph_intel=True,
            )

    # ── CRISIS: long index puts (protective / directional) ────────
    if ctx.volatility_regime == "CRISIS":
        if ctx.rsi < 25 and ctx.z_score < -2.0 and factor_score >= 0.45:
            exp_r = 3.0
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_PUT", direction="LONG",
                ticker=ctx.ticker, strategy_name="crisis_index_put",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=0.75, sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.0, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Crisis regime put on {future_name} (RSI={ctx.rsi:.0f}, z={ctx.z_score:+.1f})",
                strike=round(price * 0.97, 2), expiry_days=21,
                expected_r=exp_r, min_r=2.0,
            )
        if ctx.rsi < 15 and ctx.z_score < -3.0 and factor_score >= 0.50:
            exp_r = 4.0
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_CALL", direction="LONG",
                ticker=ctx.ticker, strategy_name="crisis_capitulation_bounce",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=0.70, sizing_method="KELLY_OPTIONS",
                max_risk_pct=0.8, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Extreme capitulation bounce on {future_name} (RSI={ctx.rsi:.0f}, z={ctx.z_score:+.1f})",
                strike=round(price * 0.99, 2), expiry_days=14,
                expected_r=exp_r, min_r=2.0,
            )
        return _skip(ctx, f"Crisis on {future_name} — no compelling setup")

    # ── STRONG TREND: INDEX FUTURES (the bread-and-butter) ────────
    # Citadel/Jane Street: trend-follow index futures with tight stops
    if ctx.trend_regime in ("STRONG_TREND", "WEAK_TREND") and ctx.hurst > 0.52:
        if factor_score < MIN_FACTOR_SCORE:
            return _skip(ctx, f"Trend on {future_name} but factors={factor_score:.0%}")

        bullish = ctx.trend_strength > 0.08 and ctx.ret_5d > 0.005
        bearish = ctx.trend_strength < -0.08 and ctx.ret_5d < -0.005

        if bullish or bearish:
            direction = "LONG" if bullish else "SHORT"
            stop_dist = atr * 2.0
            target_dist = atr * 3.5
            exp_r = target_dist / max(stop_dist, 0.01)
            exp_r = min(exp_r, 5.0)

            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_FUTURE", direction=direction,
                ticker=ctx.ticker, strategy_name=f"idx_trend_{future_name.lower()}",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_FUTURES",
                max_risk_pct=2.0, stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"{direction} {future_name} ({spec['name']}): "
                    f"trend={ctx.trend_strength:+.2f}, H={ctx.hurst:.2f}, "
                    f"5d={ctx.ret_5d:+.1%}, factors={factor_score:.0%}, R={exp_r:.1f}"
                ),
                expected_r=exp_r, min_r=1.5,
            )

    # ── MEAN REVERTING + HIGH VOL: index option spreads ───────────
    if ctx.volatility_regime == "HIGH_VOL" and ctx.trend_regime == "MEAN_REVERTING":
        if factor_score < MIN_FACTOR_SCORE:
            return _skip(ctx, f"High vol MR on {future_name} but factors weak")

        if ctx.z_score < -1.5 and ctx.rsi < 35:
            exp_r = 2.5
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_CALL", direction="LONG",
                ticker=ctx.ticker, strategy_name=f"idx_mr_oversold_call_{future_name.lower()}",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"MR oversold {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 0.99, 2), expiry_days=21,
                expected_r=exp_r, min_r=2.0,
            )
        if ctx.z_score > 1.5 and ctx.rsi > 65:
            exp_r = 2.5
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_PUT", direction="LONG",
                ticker=ctx.ticker, strategy_name=f"idx_mr_overbought_put_{future_name.lower()}",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"MR overbought {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 1.01, 2), expiry_days=21,
                expected_r=exp_r, min_r=2.0,
            )

    # ── NORMAL VOL + DIRECTIONAL: index debit spreads ─────────────
    if ctx.volatility_regime in ("NORMAL", "LOW_VOL"):
        spread_pts = max(atr * 1.5, price * 0.008)
        rv_pct = max(ctx.realized_vol, 0.05)
        debit_ratio = min(0.55, 0.20 + rv_pct * 1.5)

        # Regime-aware directional filter: don't buy calls into a falling market
        trailing_bearish = ctx.ret_5d < 0 and ctx.z_score < -0.5

        if (ctx.z_score < -1.2 and ctx.rsi < 40
                and factor_score >= MIN_FACTOR_SCORE and not trailing_bearish):
            long_K = round(price * 0.998, 2)
            short_K = round(long_K + spread_pts, 2)
            debit_est = spread_pts * debit_ratio
            exp_r = (spread_pts - debit_est) / max(debit_est, 0.01)
            conv = _dynamic_conviction(ctx, factor_score)
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_SPREAD", direction="LONG",
                ticker=ctx.ticker, strategy_name=f"idx_bull_spread_{future_name.lower()}",
                analysis_timeframe="H4", entry_timeframe="M5",
                conviction=conv,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"Bull call spread on {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, R={exp_r:.1f}",
                strike=long_K, expiry_days=14,
                second_leg={"strike": short_K, "direction": "SHORT", "type": "CALL"},
                expected_r=exp_r, min_r=1.5,
            )

        if ctx.z_score > 0.8 and ctx.rsi > 55 and factor_score >= MIN_FACTOR_SCORE:
            long_K = round(price * 1.002, 2)
            short_K = round(long_K - spread_pts, 2)
            debit_est = spread_pts * debit_ratio
            exp_r = (spread_pts - debit_est) / max(debit_est, 0.01)
            conv = _dynamic_conviction(ctx, factor_score)
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_SPREAD", direction="LONG",
                ticker=ctx.ticker, strategy_name=f"idx_bear_spread_{future_name.lower()}",
                analysis_timeframe="H4", entry_timeframe="M5",
                conviction=conv,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                take_profit_method="MEAN_REVERSION",
                rationale=f"Bear put spread on {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, R={exp_r:.1f}",
                strike=long_K, expiry_days=14,
                second_leg={"strike": short_K, "direction": "SHORT", "type": "PUT"},
                expected_r=exp_r, min_r=1.5,
            )

        # Protective put on extended decline (no bounce in sight)
        if (trailing_bearish and ctx.z_score < -2.0 and ctx.rsi < 30
                and factor_score >= MIN_FACTOR_SCORE):
            exp_r = abs(ctx.z_score) * 1.1
            conv = _dynamic_conviction(ctx, factor_score)
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_PUT", direction="LONG",
                ticker=ctx.ticker, strategy_name=f"idx_decline_put_{future_name.lower()}",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=conv,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=1.0, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Decline put on {future_name}: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, 5d={ctx.ret_5d:+.1%}",
                strike=round(price * 1.01, 2), expiry_days=14,
                expected_r=exp_r, min_r=1.5,
            )

        # Momentum futures on normal/low vol
        if (abs(ctx.trend_strength) > 0.15 and ctx.hurst > 0.52
                and abs(ctx.ret_5d) > 0.008 and factor_score >= MIN_FACTOR_SCORE):
            direction = "LONG" if ctx.trend_strength > 0 else "SHORT"
            stop_dist = atr * 2.0
            target_dist = atr * 3.0
            exp_r = target_dist / max(stop_dist, 0.01)
            return _idx_decision(
                action="EXECUTE", instrument_type="INDEX_FUTURE", direction=direction,
                ticker=ctx.ticker, strategy_name=f"idx_momentum_{future_name.lower()}",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_FUTURES",
                max_risk_pct=1.5, stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=(
                    f"Momentum {direction} {future_name}: "
                    f"trend={ctx.trend_strength:+.2f}, 5d={ctx.ret_5d:+.1%}, R={exp_r:.1f}"
                ),
                expected_r=exp_r, min_r=1.5,
            )

    # ── IV-RV DISLOCATION: buy cheap vol on index ─────────────────
    if ctx.implied_vol is not None and ctx.iv_rv_spread is not None:
        if ctx.iv_rv_spread < -0.04 and factor_score >= MIN_FACTOR_SCORE:
            exp_r = 2.5
            if ctx.trend_strength > 0:
                return _idx_decision(
                    action="EXECUTE", instrument_type="INDEX_CALL", direction="LONG",
                    ticker=ctx.ticker, strategy_name=f"idx_vol_arb_call_{future_name.lower()}",
                    analysis_timeframe="D1", entry_timeframe="M5",
                    conviction=_dynamic_conviction(ctx, factor_score),
                    sizing_method="KELLY_OPTIONS",
                    max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                    take_profit_method="ATR",
                    rationale=f"IV cheap on {future_name}: IV={ctx.implied_vol:.0%} < RV={ctx.realized_vol:.0%}",
                    strike=round(price, 2), expiry_days=21,
                    expected_r=exp_r, min_r=1.5,
                )
            else:
                return _idx_decision(
                    action="EXECUTE", instrument_type="INDEX_PUT", direction="LONG",
                    ticker=ctx.ticker, strategy_name=f"idx_vol_arb_put_{future_name.lower()}",
                    analysis_timeframe="D1", entry_timeframe="M5",
                    conviction=_dynamic_conviction(ctx, factor_score),
                    sizing_method="KELLY_OPTIONS",
                    max_risk_pct=1.5, stop_loss_method="FIXED_PCT",
                    take_profit_method="ATR",
                    rationale=f"IV cheap on {future_name}: IV={ctx.implied_vol:.0%} < RV={ctx.realized_vol:.0%}",
                    strike=round(price, 2), expiry_days=21,
                    expected_r=exp_r, min_r=1.5,
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

    if ctx.daily_pnl_pct < -1.0:
        return _skip(ctx, f"Daily PnL stressed ({ctx.daily_pnl_pct:+.1f}%)")
    if ctx.open_positions_count > 25 and factor_score < 0.55:
        return _skip(ctx, f"Portfolio full ({ctx.open_positions_count} positions)")

    if ctx.volatility_regime == "CRISIS":
        return _skip(ctx, "Crisis regime — no new equity positions")

    # Supply chain shock → equity position (AYVID's edge)
    if ctx.supply_chain_shock and abs(ctx.shock_magnitude) > 0.01 and factor_score >= 0.30:
        if ctx.is_beneficiary and ctx.shock_magnitude > 0.01:
            exp_r = max(abs(ctx.shock_magnitude) / 0.02 * 2.0, 1.8)
            return TradeDecision(
                action="EXECUTE", instrument_type="EQUITY", direction="LONG",
                ticker=ctx.ticker, strategy_name="supply_chain_beneficiary",
                analysis_timeframe="D1", entry_timeframe="D1",
                conviction=min(0.85, _dynamic_conviction(ctx, factor_score) + 0.10),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=2.0, stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=f"Supply chain beneficiary of {ctx.shock_source} ({ctx.shock_magnitude:+.1%})",
                expected_r=exp_r, min_r=1.2,
                graph_intel=True,
            )
        if ctx.is_victim and ctx.shock_magnitude < -0.01 and ctx.rsi < 40:
            exp_r = max(abs(ctx.shock_magnitude) / 0.02 * 2.5, 1.8)
            return TradeDecision(
                action="EXECUTE", instrument_type="EQUITY", direction="LONG",
                ticker=ctx.ticker, strategy_name="supply_chain_oversold_dip",
                analysis_timeframe="D1", entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=1.5, stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=f"Supply chain dip buy after {ctx.shock_source} shock ({ctx.shock_magnitude:+.1%}), RSI={ctx.rsi:.0f}",
                expected_r=exp_r, min_r=1.2,
                graph_intel=True,
            )
        if ctx.shock_magnitude > 0.01 and not ctx.is_victim:
            exp_r = max(abs(ctx.shock_magnitude) / 0.03 * 2.0, 1.5)
            return TradeDecision(
                action="EXECUTE", instrument_type="EQUITY", direction="LONG",
                ticker=ctx.ticker, strategy_name="supply_chain_beneficiary",
                analysis_timeframe="D1", entry_timeframe="D1",
                conviction=_dynamic_conviction(ctx, factor_score),
                sizing_method="KELLY_EQUITY",
                max_risk_pct=2.0, stop_loss_method="ATR",
                take_profit_method="ATR",
                rationale=f"Supply chain beneficiary of {ctx.shock_source} ({ctx.shock_magnitude:+.1%})",
                expected_r=exp_r, min_r=1.2,
                graph_intel=True,
            )

    # Deep value: severely oversold with multi-factor confirmation
    if (ctx.rsi < 30 and ctx.z_score < -1.5 and ctx.ret_20d < -0.06
            and factor_score >= 0.40):
        exp_r = abs(ctx.z_score) * 1.2
        return TradeDecision(
            action="EXECUTE", instrument_type="EQUITY", direction="LONG",
            ticker=ctx.ticker, strategy_name="deep_value_investment",
            analysis_timeframe="D1", entry_timeframe="D1",
            conviction=_dynamic_conviction(ctx, factor_score),
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0, stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=f"Deep value: RSI={ctx.rsi:.0f}, z={ctx.z_score:+.1f}, 20d={ctx.ret_20d:+.1%}, R={exp_r:.1f}",
            expected_r=exp_r, min_r=1.5,
        )

    # Trend investment: confirmed uptrend with Hurst > 0.55
    if (ctx.trend_strength > 0.25 and ctx.hurst > 0.55 and ctx.ret_20d > 0.03
            and factor_score >= 0.40):
        exp_r = ctx.trend_strength / 0.15 * 1.5
        exp_r = min(exp_r, 5.0)
        return TradeDecision(
            action="EXECUTE", instrument_type="EQUITY", direction="LONG",
            ticker=ctx.ticker, strategy_name="trend_investment",
            analysis_timeframe="D1", entry_timeframe="D1",
            conviction=_dynamic_conviction(ctx, factor_score),
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0, stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=f"Trend invest: trend={ctx.trend_strength:+.2f}, H={ctx.hurst:.2f}, R={exp_r:.1f}",
            expected_r=exp_r, min_r=1.5,
        )

    # Mean-reversion bounce with volume confirmation
    if (ctx.z_score < -1.3 and ctx.rsi < 38 and ctx.volume_ratio > 1.2
            and ctx.hurst < 0.48 and factor_score >= MIN_FACTOR_SCORE):
        exp_r = abs(ctx.z_score) * 1.0
        return TradeDecision(
            action="EXECUTE", instrument_type="EQUITY", direction="LONG",
            ticker=ctx.ticker, strategy_name="mr_equity_bounce",
            analysis_timeframe="D1", entry_timeframe="D1",
            conviction=_dynamic_conviction(ctx, factor_score),
            sizing_method="KELLY_EQUITY",
            max_risk_pct=1.5, stop_loss_method="ATR",
            take_profit_method="MEAN_REVERSION",
            rationale=f"MR bounce: z={ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}, vol_r={ctx.volume_ratio:.1f}",
            expected_r=exp_r, min_r=1.5,
        )

    return _skip(ctx, f"No equity edge (factors={factor_score:.0%})")


def rule_based_orchestrate(ctx: MarketContext) -> TradeDecision:
    """Public entry point — routes to index F&O or equity investment."""
    if ctx.asset_class == "INDEX":
        decision = index_fno_orchestrate(ctx)
    else:
        decision = _equity_orchestrate_core(ctx)

    if decision.action == "EXECUTE":
        vix = ctx.vix_level
        if vix > 30:
            decision.max_risk_pct *= 0.50
        elif vix > 25:
            decision.max_risk_pct *= 0.75
        if ctx.daily_pnl_pct < -0.5:
            decision.max_risk_pct *= 0.75
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
            action="SKIP", instrument_type="NONE", direction="NONE",
            ticker=ctx.ticker, strategy_name="naive_skip",
            analysis_timeframe="D1", entry_timeframe="D1",
            conviction=0.0, sizing_method="FIXED_RISK",
            max_risk_pct=0.0, stop_loss_method="FIXED_PCT",
            take_profit_method="FIXED_PCT", rationale=reason,
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
                action="EXECUTE", instrument_type="INDEX_CALL", direction="LONG",
                ticker=ctx.ticker, strategy_name="naive_zscore_call",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=0.60,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=2.0, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Naive: z-score oversold {ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 0.99, 2), expiry_days=14,
                expected_r=1.2, min_r=1.0,
                futures_contract=future_name, multiplier=multiplier,
                country=country, exchange=exchange,
            )

        if ctx.z_score > 1.5 and ctx.rsi > 65 and factor_score >= 0.25:
            return TradeDecision(
                action="EXECUTE", instrument_type="INDEX_PUT", direction="LONG",
                ticker=ctx.ticker, strategy_name="naive_zscore_put",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=0.60,
                sizing_method="KELLY_OPTIONS",
                max_risk_pct=2.0, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Naive: z-score overbought {ctx.z_score:+.1f}, RSI={ctx.rsi:.0f}",
                strike=round(price * 1.01, 2), expiry_days=14,
                expected_r=1.2, min_r=1.0,
                futures_contract=future_name, multiplier=multiplier,
                country=country, exchange=exchange,
            )

        if ctx.trend_strength > 0.6 and ctx.ret_5d > 0.01 and factor_score >= 0.25:
            return TradeDecision(
                action="EXECUTE", instrument_type="INDEX_FUTURE", direction="LONG",
                ticker=ctx.ticker, strategy_name="naive_trend_long",
                analysis_timeframe="D1", entry_timeframe="M5",
                conviction=0.60,
                sizing_method="KELLY_FUTURES",
                max_risk_pct=2.0, stop_loss_method="FIXED_PCT",
                take_profit_method="ATR",
                rationale=f"Naive: trend following long, trend={ctx.trend_strength:.2f}",
                expected_r=1.2, min_r=1.0,
                futures_contract=future_name, multiplier=multiplier,
                country=country, exchange=exchange,
            )

        return _skip_naive(f"Naive: no F&O signal on {future_name}")

    # Equity: simple factor-based, no shock awareness
    if factor_score < 0.25:
        return _skip_naive(f"Naive: factor score too low ({factor_score:.2f})")

    if ctx.rsi < 30 and ctx.z_score < -1.5:
        return TradeDecision(
            action="EXECUTE", instrument_type="EQUITY", direction="LONG",
            ticker=ctx.ticker, strategy_name="naive_oversold",
            analysis_timeframe="D1", entry_timeframe="D1",
            conviction=0.60,
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0, stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=f"Naive: oversold dip buy RSI={ctx.rsi:.0f}, z={ctx.z_score:+.1f}",
            expected_r=1.5, min_r=1.0,
        )

    if ctx.trend_strength > 0.5 and ctx.ret_5d > 0.01:
        return TradeDecision(
            action="EXECUTE", instrument_type="EQUITY", direction="LONG",
            ticker=ctx.ticker, strategy_name="naive_momentum",
            analysis_timeframe="D1", entry_timeframe="D1",
            conviction=0.60,
            sizing_method="KELLY_EQUITY",
            max_risk_pct=2.0, stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale=f"Naive: momentum long, trend={ctx.trend_strength:.2f}",
            expected_r=1.5, min_r=1.0,
        )

    return _skip_naive(f"Naive: no equity signal on {ctx.ticker}")
