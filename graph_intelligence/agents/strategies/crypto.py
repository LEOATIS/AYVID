"""Crypto orchestrator.

Extracted from ``strategy_orchestrator._crypto_orchestrate`` as part
of the H5 god-object decomposition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext,
        TradeDecision,
    )


def orchestrate(ctx: MarketContext) -> TradeDecision:
    """Rule-based orchestrator for crypto assets (Track 6).

    Two regimes:
      1. Momentum       — strong trend with ADX > 25 and 20-day move > 10%.
      2. Supply chain   — graph-intel-driven shock expression.
    """
    from graph_intelligence.agents.strategy_orchestrator import (
        TradeDecision,
        _skip,
    )

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
