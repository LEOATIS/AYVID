"""FX pair orchestrator.

Extracted from ``strategy_orchestrator._fx_orchestrate`` as part of
the H5 god-object decomposition. Logic is byte-identical to the
original; only the location moved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext,
        TradeDecision,
    )


def orchestrate(ctx: MarketContext) -> TradeDecision:
    """Rule-based orchestrator for FX pairs (Track 4).

    Three regimes:
      1. Macro trend  — strong directional shock + supply-chain bias.
      2. Carry trade  — low-vol mean-reverting environment.
      3. Mean reversion — extreme z-score in anti-persistent regime.
    Falls through to SKIP when no regime triggers.
    """
    # Imports done inside the function to avoid a circular import:
    # ``strategy_orchestrator`` imports this module to wire the
    # dispatcher, and we need its data classes / `_skip` helper here.
    from graph_intelligence.agents.strategy_orchestrator import (
        TradeDecision,
        _skip,
    )

    price = ctx.current_price
    _atr = max(ctx.atr, price * 0.002)  # noqa: F841 — kept for parity with legacy

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
