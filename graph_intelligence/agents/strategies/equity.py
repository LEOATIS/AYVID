"""Equity orchestrator (H5 extraction).

Long-equity decision logic — multi-factor scoring + regime gate +
sizing. Body is byte-identical to the legacy
``_equity_orchestrate_core`` in ``strategy_orchestrator.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext,
        TradeDecision,
    )


def orchestrate(ctx: MarketContext) -> TradeDecision:
    """Equity-only investment decisions. No options on stocks.

    Equities are treated as medium/long-term investments:
    - Deep value: buy when severely oversold with strong factor confirmation
    - Trend following: ride confirmed uptrends
    - Supply chain: position on AYVID's proprietary shock intelligence
    """

    from graph_intelligence.agents.strategy_orchestrator import (
        MIN_FACTOR_SCORE,
        TradeDecision,
        _dynamic_conviction,
        _multi_factor_score,
        _settings,
        _skip,
        get_param,
        is_strategy_regime_blocked,
    )

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
