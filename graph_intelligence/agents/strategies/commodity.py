"""Commodity-futures orchestrator.

Extracted from ``strategy_orchestrator._commodity_orchestrate`` as
part of the H5 god-object decomposition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext,
        TradeDecision,
    )


def orchestrate(ctx: MarketContext) -> TradeDecision:
    """Rule-based orchestrator for commodity futures (Track 5).

    Three regimes:
      1. Supply shock  — supply_chain_shock with magnitude > 0.3.
      2. Momentum      — strong trend (ADX > 30, trend_strength > 0.5).
      3. Seasonal      — calendar-anchored historical edges.
    Falls through to SKIP otherwise.
    """
    from graph_intelligence.agents.strategy_orchestrator import (
        TradeDecision,
        _skip,
    )

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

    try:
        import datetime as _dt

        from shared.quant.seasonal_calendar import get_active_seasonal_edges

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
                    rationale=(
                        f"Commodity seasonal: {ctx.ticker} {edge.direction} "
                        f"(WR={edge.historical_win_rate}%)"
                    ),
                    expected_r=1.5,
                    min_r=1.0,
                )
    except Exception:
        pass

    return _skip(ctx, f"No commodity edge for {ctx.ticker}")
