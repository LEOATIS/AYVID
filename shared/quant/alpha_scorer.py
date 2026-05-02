"""Unified conviction scorer — single source of truth.

Pre-Phase-3 the codebase had FIVE independent "how confident am I?"
computations, each with different formulas, thresholds, and semantics:

    1. `_multi_factor_score`              (orchestrator, checklist)
    2. `signals.score_signal` composite   (Bayesian factor aggregation)
    3. `graph_intelligence.debate`        (analyst-vote JSD + Bayesian)
    4. `_dynamic_conviction`              (orchestrator helper)
    5. researcher facilitator margin      (bull/bear debate)

They disagreed on direction, magnitude, and floor, which meant the same
`MarketContext` could produce a "LONG 0.72" via one path and a "SKIP"
via another. `AlphaScorer.score(ctx)` consolidates them into a
deterministic, inspectable `AlphaScore` with full factor traceability.

Contract:

    score(ctx) -> AlphaScore

    * direction = f(regime, z-score, RSI, shock bias, causal intel)
    * factor_score = fraction of independent confirmations (was
      `_multi_factor_score`)
    * bayesian_conviction = product of likelihood ratios on those
      same factors (was `signals.bayesian_conviction_update` applied
      inside `score_signal`)
    * unified_conviction = factor_score * bayesian_conviction
                                        * risk_multiplier
    * expected_r = f(ATR, stop-loss method, direction)

`debate.py`'s analyst-vote aggregation is retained as a parallel,
audit-only signal: the LLM debate outcome is stored in
`TradeIntent.audit_trail["debate_result"]`, but the *trade gate* is
the deterministic `unified_conviction` produced here.
"""

from __future__ import annotations

from typing import Any

from shared.config import settings
from shared.logger import get_logger
from shared.quant._primitives import bayesian_conviction_update
from shared.types import AlphaScore, TradeAction

log = get_logger("math.alpha_scorer")


# ── Factor checklist ──────────────────────────────────────────
#
# Each factor returns (confirmed: bool, likelihood_ratio: float).
# `confirmed` feeds the frequentist factor-score count; `likelihood_ratio`
# feeds the Bayesian posterior. Both are derived from the same MarketContext
# so they cannot disagree on what evidence they saw.


def _factor_zscore(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """Extreme z-score in the trade direction."""
    z = ctx.z_score
    if abs(z) < 1.0:
        return False, 1.0
    lr = 1.0 + min(abs(z) - 1.0, 3.0) * 0.4
    if direction == TradeAction.LONG and z < -1.0:
        return True, lr
    if direction == TradeAction.SHORT and z > 1.0:
        return True, lr
    # z-score points the wrong way for the proposed direction
    return False, max(1.0 / lr, 0.3)


def _factor_rsi(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """RSI exhaustion in the trade direction."""
    rsi = ctx.rsi
    if direction == TradeAction.LONG and rsi < 35.0:
        return True, 1.0 + max(35.0 - rsi, 0.0) * 0.04
    if direction == TradeAction.SHORT and rsi > 65.0:
        return True, 1.0 + max(rsi - 65.0, 0.0) * 0.04
    return False, 1.0


def _factor_hurst(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """Regime-Hurst alignment.

    * Trending regime + H > 0.55 → trend trade confirmed.
    * Mean-reverting regime + H < 0.45 → MR trade confirmed.
    * Anything else → no confirmation (likelihood_ratio = 1.0).
    """
    hurst = ctx.hurst
    trend = ctx.trend_regime
    if trend in ("STRONG_TREND", "WEAK_TREND") and hurst > 0.55:
        return True, 1.0 + (hurst - 0.55) * 2.0
    if trend == "MEAN_REVERTING" and hurst < 0.45:
        return True, 1.0 + (0.45 - hurst) * 3.0
    return False, 1.0


def _factor_volume(ctx: Any, _direction: TradeAction) -> tuple[bool, float]:
    """Volume confirmation or divergence."""
    v = ctx.volume_ratio
    if v > 1.5:
        return True, 1.3
    if v < 0.5:
        return False, 0.6
    return False, 1.0


def _factor_momentum(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """5-day momentum aligned with direction."""
    r5 = ctx.ret_5d
    if abs(r5) < 0.02:
        return False, 1.0
    aligned = (direction == TradeAction.LONG and r5 > 0) or (
        direction == TradeAction.SHORT and r5 < 0
    )
    if aligned:
        return True, 1.0 + min(abs(r5) * 5.0, 1.0)
    return False, max(1.0 - abs(r5) * 3.0, 0.4)


def _factor_ou(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """OU mean-reversion speed — only counts for mean-reverting trades."""
    theta = ctx.ou_theta
    if theta <= 0.5:
        return False, 1.0
    if ctx.trend_regime != "MEAN_REVERTING":
        return False, 1.0
    return True, 1.0 + min(theta - 0.5, 4.5) * 0.15


def _factor_vrp(ctx: Any, _direction: TradeAction) -> tuple[bool, float]:
    """IV-RV dislocation — either direction counts."""
    spread = ctx.iv_rv_spread
    if spread is None or abs(spread) < 0.04:
        return False, 1.0
    return True, 1.0 + min(abs(spread), 0.15) * 3.0


def _factor_macro(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """Macro context: market breadth + VIX alignment."""
    breadth = ctx.market_breadth
    vix = ctx.vix_level
    trend_dir = ctx.trend_strength

    aligned = False
    if direction == TradeAction.LONG and breadth > 0.6 and trend_dir > 0:
        aligned = True
    elif direction == TradeAction.SHORT and breadth < 0.4 and trend_dir < 0:
        aligned = True

    vix_aligned = False
    if vix > settings.VIX_ELEVATED_THRESHOLD and ctx.trend_regime == "MEAN_REVERTING":
        vix_aligned = True
    elif vix < settings.VIX_CALM_THRESHOLD and ctx.trend_regime in ("STRONG_TREND", "WEAK_TREND"):
        vix_aligned = True

    confirmed = aligned or vix_aligned
    return confirmed, 1.2 if confirmed else 1.0


def _factor_causal(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """Causal-graph shock bias alignment.

    This factor is ONLY active when LangGraph surfaced a shock with a
    directional bias. When it fires with high causal_conviction, it
    carries a very high likelihood ratio — the whole point of the
    causal-arbitrage thesis is that supply-chain-shocked downstream
    targets beat naive technical signals.
    """
    if not ctx.supply_chain_shock or not ctx.shock_bias:
        return False, 1.0
    conv = max(0.0, min(ctx.causal_conviction, 1.0))
    bias = ctx.shock_bias.upper()
    aligned = (direction == TradeAction.LONG and bias == "BULLISH") or (
        direction == TradeAction.SHORT and bias == "BEARISH"
    )
    if aligned:
        return True, 1.0 + conv * 2.0
    # Shock points the wrong way for the proposed direction → strong bearish signal
    return False, max(1.0 - conv, 0.2)


def _factor_uoa(ctx: Any, direction: TradeAction) -> tuple[bool, float]:
    """Unusual options activity alignment with trade direction."""
    uoa = getattr(ctx, "uoa_signal", None)
    if uoa is None or not getattr(uoa, "is_unusual", False):
        return False, 1.0
    uoa_dir = getattr(uoa, "direction", "NEUTRAL").upper()
    uoa_conf = max(0.0, min(getattr(uoa, "confidence", 0.0), 1.0))
    aligned = (direction == TradeAction.LONG and uoa_dir == "BULLISH") or (
        direction == TradeAction.SHORT and uoa_dir == "BEARISH"
    )
    if aligned:
        return True, 1.0 + uoa_conf * 1.5  # max LR boost of 2.5
    return False, 1.0


_FACTORS = {
    "zscore": _factor_zscore,
    "rsi": _factor_rsi,
    "hurst": _factor_hurst,
    "volume": _factor_volume,
    "momentum": _factor_momentum,
    "ou": _factor_ou,
    "vrp": _factor_vrp,
    "macro": _factor_macro,
    "causal": _factor_causal,
    "uoa": _factor_uoa,
}


# ── Direction determination ───────────────────────────────────


def _determine_direction(ctx: Any) -> TradeAction:
    """Derive the trade direction from context.

    Priority:
        1. Causal shock bias (highest trust — it's why we built the graph)
        2. Regime + extreme z-score / RSI
        3. Trend strength (fallback for trending regimes)

    Returns HOLD when no signal exceeds the minimum activation bar.
    """
    # 1. Causal shock — trust it if present
    if ctx.supply_chain_shock and ctx.shock_bias:
        bias = ctx.shock_bias.upper()
        if bias == "BULLISH":
            return TradeAction.LONG
        if bias == "BEARISH":
            return TradeAction.SHORT

    # 2. Regime-driven direction
    hurst = ctx.hurst
    z = ctx.z_score
    rsi = ctx.rsi
    trend = ctx.trend_strength

    is_mean_reverting = hurst < 0.45
    is_trending = hurst > 0.55

    if is_mean_reverting and ctx.ou_theta > 0.3:
        if z > 1.2 and rsi > 60:
            return TradeAction.SHORT
        if z < -1.2 and rsi < 40:
            return TradeAction.LONG

    if is_trending:
        if trend > 0.2 and rsi < 75:
            return TradeAction.LONG
        if trend < -0.2 and rsi > 25:
            return TradeAction.SHORT

    # 3. Extreme-z fallback (random-walk regime)
    if z > 1.5 and rsi > 65:
        return TradeAction.SHORT
    if z < -1.5 and rsi < 35:
        return TradeAction.LONG
    if abs(z) > 1.8:
        return TradeAction.SHORT if z > 0 else TradeAction.LONG

    return TradeAction.HOLD


# ── Expected R (reward / risk) ────────────────────────────────


def _expected_r(ctx: Any, direction: TradeAction) -> float:
    """Estimate best-case reward-to-risk ratio for this context.

    Strategy-agnostic estimate of what R the orchestrator can achieve
    if it chooses the right instrument + stop method for this setup.
    The downstream orchestrator is free to refine — this just has to
    be realistic enough that a genuinely asymmetric setup doesn't fail
    the `MIN_R_GLOBAL` gate purely on scorer pessimism.

    Empirical anchors (informed by the 2018-2025 shock-driven backtest):
        * Trend-following on confirmed Hurst + direction : ~2.2R
        * Mean-reversion on confirmed Hurst + extreme z   : ~1.7R
        * Causal-shock override at high conviction        : ~2.6R
        * Choppy / random-walk regime                     : ~1.1R

    All values are pre-cost; the orchestrator applies the EV hurdle
    downstream to kill sub-noise trades.
    """
    if direction == TradeAction.HOLD:
        return 0.0

    # Causal shock with high conviction is the fattest tail in the
    # strategy universe — this is literally the fund's raison d'être.
    if ctx.supply_chain_shock and ctx.causal_conviction >= 0.70:
        aligned = (direction == TradeAction.LONG and ctx.shock_bias.upper() == "BULLISH") or (
            direction == TradeAction.SHORT and ctx.shock_bias.upper() == "BEARISH"
        )
        if aligned:
            return 2.6

    # Regime-confirmed trade.
    if ctx.trend_regime in ("STRONG_TREND", "WEAK_TREND") and ctx.hurst > 0.55:
        return 2.2
    if ctx.trend_regime == "MEAN_REVERTING" and ctx.hurst < 0.45:
        return 1.7

    # Random-walk / choppy regime: barely worth the spread. The MIN_R
    # gate will typically kill these.
    return 1.1


# ── Public API ────────────────────────────────────────────────


def score(ctx: Any) -> AlphaScore:
    """Compute the unified AlphaScore for a MarketContext.

    Deterministic — the same context always produces the same score.
    Fully inspectable via `alpha_score.factor_breakdown`.
    """
    direction = _determine_direction(ctx)

    confirmations: list[bool] = []
    likelihood_ratios: list[tuple[float, float]] = []
    factor_breakdown: dict[str, float] = {}
    active_factor_count = 0

    for name, fn in _FACTORS.items():
        confirmed, lr = fn(ctx, direction)
        confirmations.append(confirmed)
        likelihood_ratios.append((lr, 1.0))
        factor_breakdown[name] = round(lr, 4)
        # UOA factor only counts toward the denominator when UOA data
        # is actually available. Without data it's a no-op, not a penalty.
        if name == "uoa":
            uoa = getattr(ctx, "uoa_signal", None)
            if uoa is not None and getattr(uoa, "is_unusual", False):
                active_factor_count += 1
        else:
            active_factor_count += 1

    factor_score = sum(1 for c in confirmations if c) / max(active_factor_count, 1)
    bayesian = bayesian_conviction_update(0.5, likelihood_ratios)

    # Unified conviction scales down by Strategist / Supervisor risk
    # multiplier. A multiplier of 1.0 is neutral; values < 1.0 damp
    # conviction (e.g., during high-vol blow-up protection).
    risk_mult = max(0.0, min(getattr(ctx, "risk_multiplier", 1.0), 1.0))
    unified = factor_score * bayesian * risk_mult

    # Direction HOLD → force unified to zero so the gate cannot pass.
    if direction == TradeAction.HOLD:
        unified = 0.0

    expected_r = _expected_r(ctx, direction)

    rationale = (
        f"direction={direction.value} "
        f"factor={factor_score:.2f} bayes={bayesian:.2f} "
        f"risk_mult={risk_mult:.2f} unified={unified:.2f} "
        f"R={expected_r:.2f}"
    )

    return AlphaScore(
        ticker=ctx.ticker,
        direction=direction,
        factor_score=factor_score,
        bayesian_conviction=bayesian,
        unified_conviction=unified,
        expected_r=expected_r,
        min_r=settings.MIN_R_GLOBAL,
        kl_divergence_to_naive=0.0,
        factor_breakdown=factor_breakdown,
        rationale=rationale,
    )


def _resolve_factor_floor(strategy_type: str | None = None) -> float:
    """Resolve the correct MIN_FACTOR_SCORE floor for the strategy type."""
    if strategy_type == "premium":
        return settings.MIN_FACTOR_SCORE_PREMIUM
    if strategy_type == "shock_driven":
        return settings.MIN_FACTOR_SCORE_SHOCK
    if strategy_type == "directional":
        return settings.MIN_FACTOR_SCORE_DIRECTIONAL
    return settings.MIN_FACTOR_SCORE


def passes_trade_gate(alpha: AlphaScore, strategy_type: str | None = None) -> bool:
    """Single-line gate check used by the orchestrator before emitting an
    EXECUTE intent. Aligns every downstream gate against the ONE conviction
    floor (`MIN_CONVICTION_TRADE`) instead of the previous ladder of
    incompatible numbers.

    strategy_type: "shock_driven", "premium", "directional", or None (uses global default).
    """
    if alpha.direction == TradeAction.HOLD:
        return False
    if alpha.unified_conviction < settings.MIN_CONVICTION_TRADE:
        return False
    factor_floor = _resolve_factor_floor(strategy_type)
    if alpha.factor_score < factor_floor:
        return False
    if alpha.expected_r < alpha.min_r:
        return False
    return True


__all__ = [
    "score",
    "passes_trade_gate",
    "_resolve_factor_floor",
]
