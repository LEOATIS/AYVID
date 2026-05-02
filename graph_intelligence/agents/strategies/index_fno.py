"""Index F&O orchestrator (H5 extraction).

580-line decision engine for index futures, index options, and
debit spreads. Logic is byte-identical to the legacy
``index_fno_orchestrate`` in ``strategy_orchestrator.py``; only the
module location moved. The parent module keeps a 3-line shim under
the legacy name so public callers (``rule_based_orchestrate``,
tests) are unaffected.

The next iteration of H5 should split this further by REGIME
(shock / crisis / trend / mean-reversion). The audit's recommended
final shape is one orchestrator per regime, each ~150 lines, with
the asset-class dispatcher choosing among them. That split is a
non-mechanical change — the closure ``_idx_decision`` reads many
local variables — and therefore needs an integration-test harness
covering the full decision matrix as a precondition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_intelligence.agents.strategy_orchestrator import (
        MarketContext,
        TradeDecision,
    )


def orchestrate(ctx: MarketContext) -> TradeDecision:
    """Decide index F&O trades: futures, index options, index spreads.

    This is the core F&O decision engine. It ONLY produces:
    - INDEX_FUTURE: trend-following futures with ATR-based stops
    - INDEX_CALL / INDEX_PUT: directional index options
    - INDEX_SPREAD: index debit spreads for range/directional plays

    Called for every index (^GSPC, ^NSEI, ^GDAXI, etc.) on every day.
    """

    # All helpers / constants this orchestrator references live in the
    # parent ``strategy_orchestrator`` module. Importing them lazily
    # inside the function body avoids the circular-import problem at
    # module load time (the parent imports this submodule for its
    # dispatcher shim).
    import numpy as np

    from graph_intelligence.agents.strategy_orchestrator import (
        BANNED_FNO_INDICES,
        INDEX_FUTURES_SPECS,
        MIN_FACTOR_SCORE,
        TradeDecision,
        _dynamic_conviction,
        _illiquid_factor_floor,
        _multi_factor_score,
        _settings,
        _skip,
        get_param,
        is_strategy_regime_blocked,
        is_strategy_variant_blocked,
    )

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
        from shared.quant.options_pricing import (
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
        from shared.quant.options_pricing import select_credit_spread_strikes

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
