"""Unified `build_decision_context()` — single context builder.

Pre-Phase-3 this logic was duplicated (and divergent) in two places:

  * `backtesting.engine._build_ctx`       — historical simulation
  * `backend.services.orchestrator._build_market_context`  — live trading

The two produced subtly different `MarketContext` objects. Specifically,
the LIVE path was missing `vrp`, `vol_compression`, `grind_up_flag`,
`causal_conviction`, `forbidden_strategies`, `strategy_parameters`, and
`risk_multiplier` — which meant LangGraph regime-gating was silently
ignored in live trading (audit finding A1).

This module is the single canonical builder. Both backtest and live now
call `build_decision_context()`. It accepts optional `shock_context`
(from the backtest's `SHOCK_SCENARIOS` catalog) and optional
`final_state` (the LangGraph pipeline's output dict) so LangGraph's
strategist blocklist, supervisor parameters, and causal intel propagate
into the orchestrator's gates — no more cosmetic LangGraph output.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from execution_engine.math._primitives import (
    compute_atr,
    compute_rsi,
    compute_trend_strength,
    compute_volume_profile,
    rolling_zscore,
)
from execution_engine.math._primitives import (
    compute_hurst as _compute_hurst_primitive,
)
from execution_engine.math.regime import (
    classify_trend,
    classify_volatility,
    compute_vol_windows,
    compute_vrp,
    is_grind_up_regime,
)
from shared.logger import get_logger
from shared.types import OHLCVBar

log = get_logger("math.decision_context")


def _closes_highs_lows_volumes(
    bars: list[OHLCVBar],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    closes = np.array([b.close for b in bars])
    highs = np.array([b.high for b in bars])
    lows = np.array([b.low for b in bars])
    volumes = np.array([b.volume for b in bars], dtype=float)
    return closes, highs, lows, volumes


def build_decision_context(
    ticker: str,
    bars: list[OHLCVBar],
    *,
    asset_class: str = "EQUITY",
    sector: str | None = None,
    country: str = "US",
    shock_context: dict[str, Any] | None = None,
    final_state: dict[str, Any] | None = None,
    portfolio_exposure_pct: float = 0.0,
    open_positions_count: int = 0,
    daily_pnl_pct: float = 0.0,
    market_breadth: float = 0.5,
    vix_level: float = 20.0,
):
    """Construct a `MarketContext` from OHLCV + optional LangGraph enrichment.

    Args:
        ticker              : instrument symbol.
        bars                : OHLCV history (needs at least 30 rows).
        asset_class         : "EQUITY" | "INDEX" | "FUTURE" | ... (string for
                              downstream orchestrator compatibility).
        sector, country     : passed through to the context.
        shock_context       : optional dict from `SHOCK_SCENARIOS` (backtest) or
                              NER shock detection. Keys consumed:
                                shocked_tickers, beneficiary_tickers,
                                victim_tickers, source, magnitude, index_bias.
        final_state         : optional LangGraph output dict. Keys consumed:
                                forbidden_strategies, strategy_parameters,
                                risk_multiplier, causal_intel, confidence.
        portfolio_exposure_pct, open_positions_count, daily_pnl_pct,
        market_breadth, vix_level : macro/portfolio overlay. Default to
                              flat-neutral values so backtests that don't
                              track these fields still produce a valid ctx.

    Returns `MarketContext` or `None` when the bars window is too short.
    """
    if len(bars) < 30:
        return None

    # Local import to avoid circular dependency — MarketContext lives in the
    # agent layer (business logic), this module is pure math.
    from graph_intelligence.agents.strategy_orchestrator import (
        INDEX_FUTURES_SPECS,
        MarketContext,
    )

    closes, highs, lows, volumes = _closes_highs_lows_volumes(bars)

    price = float(closes[-1])
    log_returns = np.diff(np.log(closes[-31:])) if len(closes) > 31 else np.diff(np.log(closes))

    # ── Technicals (canonical primitives only) ────────────────
    hurst = _compute_hurst_primitive(log_returns)
    rsi = compute_rsi(closes, 14)
    atr_val = compute_atr(highs, lows, closes, 14)
    trend = compute_trend_strength(closes, short=10, long=30)
    vol_ratio = compute_volume_profile(volumes, 20)

    # ── Regime classification ─────────────────────────────────
    vol_regime, rv, vol_pct = classify_volatility(log_returns)
    trend_regime, adx = classify_trend(highs, lows, closes, hurst)

    # Z-score on a 20-bar close window (matches legacy _build_ctx exactly
    # — see tests/test_primitives.py parity pins).
    z_score = rolling_zscore(closes, lookback=20)

    ret_5d = float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
    ret_20d = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0

    # ── IV proxy + VRP + grind-up regime flag ────────────────
    iv_proxy = rv + abs(ret_5d) * 2.0 + 0.02
    iv_rv_spread = iv_proxy - rv if rv > 0 else None

    rv_short, rv_long, vol_compression = compute_vol_windows(
        log_returns,
        short=20,
        long=60,
    )
    vrp_val = compute_vrp(iv_proxy, rv_short) if iv_rv_spread is not None else 0.0
    grind_up = is_grind_up_regime(
        vol_percentile=vol_pct,
        vol_compression=vol_compression,
        ret_20d=ret_20d,
        adx=adx,
        hurst=hurst,
        vrp=vrp_val,
    )

    # ── Shock overlay (backtest scenarios or NER-detected shocks) ──
    shock_active = False
    shock_source: str | None = None
    shock_magnitude = 0.0
    shock_bias = ""
    is_beneficiary = False
    is_victim = False
    if shock_context:
        shock_active = True
        shock_source = shock_context.get("source")
        shocked_tickers = set(shock_context.get("shocked_tickers", []))
        beneficiary_tickers = set(shock_context.get("beneficiary_tickers", []))
        victim_tickers = set(shock_context.get("victim_tickers", []))
        is_beneficiary = ticker in beneficiary_tickers
        is_victim = ticker in victim_tickers
        is_shock_ticker = ticker in shocked_tickers
        if is_shock_ticker and len(closes) >= 6:
            window = min(5, len(closes) - 1)
            shock_magnitude = float(closes[-1] / closes[-1 - window] - 1)
        else:
            shock_magnitude = float(shock_context.get("magnitude", 0.0))
        if asset_class == "INDEX":
            shock_bias = shock_context.get("index_bias", {}).get(ticker, "")
        elif is_beneficiary:
            shock_bias = "BULLISH"
        elif is_victim:
            shock_bias = "BEARISH"

    # ── LangGraph output propagation (A1 fix) ─────────────────
    forbidden_strategies: list[str] = []
    strategy_parameters: dict = {}
    risk_multiplier = 1.0
    causal_conviction = 0.0
    if final_state:
        forbidden_strategies = list(final_state.get("forbidden_strategies", []) or [])
        strategy_parameters = dict(final_state.get("strategy_parameters", {}) or {})
        risk_multiplier = float(final_state.get("risk_multiplier", 1.0))
        causal_conviction = float(final_state.get("confidence", 0.0))
        causal_intel = final_state.get("causal_intel", {}) or {}
        # When the LangGraph pipeline itself surfaced the shock, its
        # intel overrides any static shock_context passed in.
        if causal_intel:
            shock_active = True
            shock_source = causal_intel.get("source", shock_source)
            if asset_class == "INDEX":
                shock_bias = causal_intel.get("index_bias", {}).get(ticker, shock_bias)
            elif ticker in set(causal_intel.get("beneficiary_tickers", []) or []):
                shock_bias = shock_bias or "BULLISH"
                is_beneficiary = True
            elif ticker in set(causal_intel.get("victim_tickers", []) or []):
                shock_bias = shock_bias or "BEARISH"
                is_victim = True

    spec = INDEX_FUTURES_SPECS.get(ticker, {}) if asset_class == "INDEX" else {}

    return MarketContext(
        ticker=ticker,
        current_price=price,
        volatility_regime=vol_regime.value,
        trend_regime=trend_regime.value,
        hurst=hurst,
        rsi=rsi,
        atr=atr_val,
        realized_vol=rv,
        implied_vol=iv_proxy if iv_rv_spread is not None else None,
        iv_rv_spread=iv_rv_spread,
        supply_chain_shock=shock_active,
        shock_source=shock_source,
        shock_magnitude=shock_magnitude,
        shock_bias=shock_bias,
        is_beneficiary=is_beneficiary,
        is_victim=is_victim,
        sector=sector,
        country=spec.get("country", country),
        asset_class=asset_class,
        z_score=z_score,
        trend_strength=trend,
        volume_ratio=vol_ratio,
        ret_5d=ret_5d,
        ret_20d=ret_20d,
        market_breadth=market_breadth,
        vix_level=vix_level,
        open_positions_count=open_positions_count,
        daily_pnl_pct=daily_pnl_pct,
        portfolio_exposure_pct=portfolio_exposure_pct,
        vrp=vrp_val,
        vol_compression=vol_compression,
        grind_up_flag=grind_up,
        causal_conviction=causal_conviction,
        forbidden_strategies=forbidden_strategies,
        strategy_parameters=strategy_parameters,
        risk_multiplier=risk_multiplier,
    )


__all__ = ["build_decision_context"]
