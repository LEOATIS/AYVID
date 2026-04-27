"""Unit tests for the unified AlphaScorer.

Covers: direction determination, factor breakdown traceability,
unified conviction composition, causal-shock override, and the
trade-gate contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from execution_engine.math import alpha_scorer
from shared.config import settings
from shared.types import TradeAction


@dataclass
class _FakeCtx:
    """Minimal MarketContext replica for unit testing the scorer."""

    ticker: str = "TEST"
    z_score: float = 0.0
    rsi: float = 50.0
    hurst: float = 0.5
    trend_strength: float = 0.0
    trend_regime: str = "CHOPPY"
    volume_ratio: float = 1.0
    ret_5d: float = 0.0
    ou_theta: float = 0.0
    iv_rv_spread: float | None = None
    market_breadth: float = 0.5
    vix_level: float = 20.0
    supply_chain_shock: bool = False
    shock_bias: str = ""
    causal_conviction: float = 0.0
    risk_multiplier: float = 1.0


# ── Direction determination ───────────────────────────────────


def test_direction_hold_when_neutral():
    ctx = _FakeCtx()
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.HOLD
    assert alpha.unified_conviction == 0.0, "HOLD must force zero conviction"


def test_direction_short_on_extreme_overbought():
    ctx = _FakeCtx(z_score=2.0, rsi=72.0, hurst=0.5, trend_strength=0.0)
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.SHORT


def test_direction_long_on_extreme_oversold():
    ctx = _FakeCtx(z_score=-2.0, rsi=28.0, hurst=0.5)
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.LONG


def test_direction_long_on_trending_up():
    ctx = _FakeCtx(hurst=0.62, trend_strength=0.4, rsi=55.0)
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.LONG


def test_direction_short_on_trending_down():
    ctx = _FakeCtx(hurst=0.62, trend_strength=-0.4, rsi=45.0)
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.SHORT


# ── Causal shock override ─────────────────────────────────────


def test_causal_shock_bullish_overrides_weak_technicals():
    """Gap A6 fix: causal shock bias MUST set direction, not default to LONG via researchers."""
    ctx = _FakeCtx(
        # Weak technicals that would otherwise HOLD
        z_score=0.2,
        rsi=52.0,
        hurst=0.5,
        trend_strength=0.05,
        # But the causal graph is screaming BULLISH
        supply_chain_shock=True,
        shock_bias="BULLISH",
        causal_conviction=0.85,
    )
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.LONG
    assert alpha.factor_breakdown["causal"] > 1.5, (
        "Causal factor should contribute a likelihood ratio > 1.5 when "
        "conviction is 0.85 and direction aligns"
    )


def test_causal_shock_bearish_overrides_default_long():
    ctx = _FakeCtx(
        z_score=0.0,
        rsi=50.0,
        supply_chain_shock=True,
        shock_bias="BEARISH",
        causal_conviction=0.75,
    )
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.SHORT, (
        "BEARISH causal bias must flip direction to SHORT — this is the "
        "A6 bug fix. Previously mapper had no direction field so "
        "researchers defaulted to LONG regardless of shock bias."
    )


# ── Factor breakdown traceability ─────────────────────────────


def test_factor_breakdown_lists_all_factors():
    """Every factor must appear in the breakdown for auditability."""
    ctx = _FakeCtx(z_score=2.1, rsi=72.0)
    alpha = alpha_scorer.score(ctx)
    expected_factors = {
        "zscore",
        "rsi",
        "hurst",
        "volume",
        "momentum",
        "ou",
        "vrp",
        "macro",
        "causal",
    }
    assert set(alpha.factor_breakdown.keys()) == expected_factors


def test_factor_breakdown_zscore_likelihood_scales_with_magnitude():
    small_z = _FakeCtx(z_score=1.2, rsi=70.0)
    large_z = _FakeCtx(z_score=3.0, rsi=70.0)
    s_small = alpha_scorer.score(small_z)
    s_large = alpha_scorer.score(large_z)
    assert s_large.factor_breakdown["zscore"] > s_small.factor_breakdown["zscore"]


# ── Unified conviction composition ────────────────────────────


def test_unified_conviction_is_factor_times_bayes_times_risk_mult():
    ctx = _FakeCtx(
        z_score=-2.0,
        rsi=28.0,
        hurst=0.4,
        ou_theta=1.5,
        trend_regime="MEAN_REVERTING",
        volume_ratio=1.8,
        ret_5d=-0.03,
        risk_multiplier=0.75,
    )
    alpha = alpha_scorer.score(ctx)
    expected = alpha.factor_score * alpha.bayesian_conviction * 0.75
    assert abs(alpha.unified_conviction - expected) < 1e-9


def test_risk_multiplier_clamped_to_unit_interval():
    ctx_neg = _FakeCtx(z_score=-2.0, rsi=28.0, risk_multiplier=-0.5)
    ctx_big = _FakeCtx(z_score=-2.0, rsi=28.0, risk_multiplier=5.0)
    a_neg = alpha_scorer.score(ctx_neg)
    a_big = alpha_scorer.score(ctx_big)
    assert a_neg.unified_conviction == 0.0
    # Big multiplier should be clamped to <=1.0 → unified cannot exceed factor*bayes
    assert a_big.unified_conviction <= a_big.factor_score * a_big.bayesian_conviction + 1e-9


# ── Trade gate contract ───────────────────────────────────────


def test_trade_gate_rejects_hold():
    ctx = _FakeCtx()
    alpha = alpha_scorer.score(ctx)
    assert not alpha_scorer.passes_trade_gate(alpha)


def test_trade_gate_rejects_low_conviction():
    """Even with a direction, low unified_conviction must fail the gate."""
    ctx = _FakeCtx(z_score=-1.6, rsi=32.0, risk_multiplier=0.3)
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction != TradeAction.HOLD
    assert not alpha_scorer.passes_trade_gate(alpha), (
        f"Unified={alpha.unified_conviction:.3f} should fail the "
        f"MIN_CONVICTION_TRADE={settings.MIN_CONVICTION_TRADE} gate"
    )


def test_trade_gate_accepts_high_conviction_aligned_setup():
    """Multi-factor aligned setup with strong causal shock should pass."""
    ctx = _FakeCtx(
        z_score=-2.5,
        rsi=24.0,
        hurst=0.38,
        trend_regime="MEAN_REVERTING",
        ou_theta=2.0,
        volume_ratio=1.9,
        ret_5d=-0.05,
        market_breadth=0.35,
        vix_level=26.0,
        supply_chain_shock=True,
        shock_bias="BULLISH",
        causal_conviction=0.92,
    )
    alpha = alpha_scorer.score(ctx)
    assert alpha.direction == TradeAction.LONG
    assert alpha_scorer.passes_trade_gate(alpha), (
        f"High-conviction setup failed gate: "
        f"unified={alpha.unified_conviction:.3f} factor={alpha.factor_score:.2f} "
        f"bayes={alpha.bayesian_conviction:.2f} R={alpha.expected_r:.2f}"
    )


# ── Determinism ───────────────────────────────────────────────


def test_scorer_is_deterministic():
    ctx = _FakeCtx(z_score=-1.8, rsi=30.0, hurst=0.4, ou_theta=1.2, trend_regime="MEAN_REVERTING")
    alpha_a = alpha_scorer.score(ctx)
    alpha_b = alpha_scorer.score(ctx)
    assert alpha_a.model_dump() == alpha_b.model_dump(), (
        "Same context must always produce the same AlphaScore — no RNG, no hidden state."
    )
