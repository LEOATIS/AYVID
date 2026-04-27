"""Tests for Batch-1 risk + sizing upgrades.

Covers:
  * Batch-1/A — priors.json blocking of directionally-mismatched
    shock strategies (supervisor HARD-HOLDs before any LLM call).
  * Batch-1/B — ATR-scaled dynamic bracket stop/TP with fallback to
    fixed-pct when bars are absent.
  * Batch-1/D — conviction-scaled Kelly sizing: a 0.62 signal gets
    materially less notional than a 0.95 signal even when both
    clear the supervisor floor.

These are the acceptance gates for the three regime/risk/sizing fixes
we just landed. Each maps to one concrete behaviour that must hold
forever — a regression here means a previously-verified improvement
has quietly reverted.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest

_PRIORS_PATH = Path(__file__).resolve().parents[1] / "calibration" / "priors.json"


# ── Batch-1/A: priors.json regime gates + supervisor HARD-HOLD ──────


class TestPriorsBlocksDirectionalMismatch:
    def _priors(self) -> dict:
        return json.loads(_PRIORS_PATH.read_text())["priors"]

    def test_idx_bear_spread_blocked_in_strong_uptrend_normal_vol(self):
        """The 2026-Q1 backtest's worst bucket. Must be blocked."""
        cell = self._priors()["idx_bear_spread"]["STRONG_TREND_UP__NORMAL"]
        assert cell.get("blocked_by_regime") is True, (
            "idx_bear_spread × STRONG_TREND_UP__NORMAL must be blocked — "
            "Batch-1/A regression: this is the cell that generated "
            "5-of-6 losing strategy variants in the 2026-Q1 report"
        )

    def test_idx_bear_spread_blocked_in_strong_uptrend_low_vol(self):
        cell = self._priors()["idx_bear_spread"]["STRONG_TREND_UP__LOW_VOL"]
        assert cell.get("blocked_by_regime") is True

    def test_idx_bear_spread_blocked_in_weak_uptrend(self):
        p = self._priors()["idx_bear_spread"]
        assert p["WEAK_TREND_UP__NORMAL"].get("blocked_by_regime") is True
        assert p["WEAK_TREND_UP__LOW_VOL"].get("blocked_by_regime") is True

    def test_idx_decline_put_blocked_in_uptrends(self):
        """Paying premium for puts in an uptrend at low IV is the
        single most negative-EV trade the system can make."""
        p = self._priors()["idx_decline_put"]
        assert p["STRONG_TREND_UP__LOW_VOL"].get("blocked_by_regime") is True
        assert p["STRONG_TREND_UP__NORMAL"].get("blocked_by_regime") is True
        assert p["WEAK_TREND_UP__LOW_VOL"].get("blocked_by_regime") is True

    def test_crisis_index_put_blocked_outside_crisis_regimes(self):
        """VIX-LOW + STRONG_TREND_UP is the exact opposite of a crisis
        setup. Crisis-put premium-paying there is negative EV by
        construction."""
        p = self._priors()["crisis_index_put"]
        assert p["STRONG_TREND_UP__LOW_VOL"].get("blocked_by_regime") is True
        assert p["STRONG_TREND_UP__NORMAL"].get("blocked_by_regime") is True


class TestSupervisorHardHoldsOnBlockedCell:
    """Integration: the supervisor_node MUST short-circuit to HOLD
    before the LLM call whenever the inferred (strategy, regime) cell
    is marked blocked_by_regime."""

    @pytest.mark.asyncio
    async def test_blocked_cell_returns_hold_without_llm_call(
        self,
        monkeypatch,
    ):
        from graph_intelligence.agents.supervisor import node as sup_node

        # Force the regime classifier to return a blocked cell key.
        async def fake_regime(ticker):
            return ("(mock regime)", "STRONG_TREND_UP__NORMAL")

        monkeypatch.setattr(sup_node, "_compute_regime_for_target", fake_regime)

        # If the supervisor ever invokes the LLM, this raises — so we
        # prove the block fires BEFORE any LLM call.
        def explode(*args, **kwargs):
            raise AssertionError("supervisor reached the LLM call — block gate failed")

        monkeypatch.setattr(sup_node, "_get_structured_llm", explode)

        state = {
            "target_ticker": "^GSPC",
            "proposed_action": "SHORT",
            "confidence": 0.75,
            "rationale": "[idx_bear_spread] bear spread on index per mapper thesis",
            "shock_ticker": "^GSPC",
            "timeline": [],
            "graph_topology_json": "",
            "causal_intel": {},
            "iteration": 1,
        }

        patch_dict = await sup_node.supervisor_node(state)

        assert patch_dict.get("proposed_action") == "HOLD"
        assert patch_dict.get("confidence") == 0.0
        assert patch_dict.get("strategy_parameters") == {}
        assert (
            "priors" in (patch_dict.get("supervisor_rationale") or "").lower()
            or "block" in (patch_dict.get("supervisor_rationale") or "").lower()
            or "unprofitable" in (patch_dict.get("supervisor_rationale") or "").lower()
        )


# ── Batch-1/B: ATR-scaled dynamic bracket ────────────────────────────


def _make_bars(*, count: int = 30, base: float = 100.0, atr_pct: float = 0.02):
    """Fabricate an OHLCVBar list with a controlled ATR.

    Builds bars whose each-day true-range is ~atr_pct * base, so the
    ATR(14) of the tail equals roughly ``atr_pct * base``.
    """
    from datetime import datetime, timedelta

    from shared.types import OHLCVBar

    tr_dollars = atr_pct * base
    bars = []
    for i in range(count):
        close = base + 0.001 * i
        high = close + tr_dollars / 2
        low = close - tr_dollars / 2
        bars.append(
            OHLCVBar(
                timestamp=datetime.now(UTC) - timedelta(days=count - i),
                symbol="TEST",
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1_000_000,
            )
        )
    return bars


def _make_signal(*, horizon: str = "3rd_order", tier3: bool = False, direction: str = "LONG"):
    from shared.types import (
        AlphaHorizon,
        AlphaScore,
        TradeAction,
        TradeIntent,
    )

    d = TradeAction.LONG if direction == "LONG" else TradeAction.SHORT
    return TradeIntent(
        intent_id="b1-bracket",
        execute=True,
        ticker="TEST",
        direction=d,
        alpha_horizon=AlphaHorizon(horizon),
        alpha_score=AlphaScore(
            ticker="TEST",
            direction=d,
            factor_score=0.7,
            bayesian_conviction=0.7,
            unified_conviction=0.7,
            expected_r=2.0,
            min_r=1.5,
        ),
        tier3_shock=tier3,
        rationale="bracket test",
    )


class TestAtrScaledBracket:
    def test_atr_scaled_stop_widens_with_volatility(self):
        """Higher ATR → wider stop distance. Same target R → wider TP too."""
        from execution_engine.broker.orders import compute_bracket_levels

        low_vol_bars = _make_bars(atr_pct=0.01)  # 1% daily range
        high_vol_bars = _make_bars(atr_pct=0.04)  # 4% daily range
        signal = _make_signal(direction="LONG")
        entry = 100.0

        sl_low, tp_low, meta_low = compute_bracket_levels(signal, entry, low_vol_bars)
        sl_hi, tp_hi, meta_hi = compute_bracket_levels(signal, entry, high_vol_bars)

        assert meta_low["mode"] == "atr_scaled"
        assert meta_hi["mode"] == "atr_scaled"

        assert meta_hi["stop_pct"] > meta_low["stop_pct"], (
            "higher-vol stop MUST widen — Batch-1/B regression"
        )
        # Target R is preserved; TP distance also widens.
        assert meta_hi["tp_pct"] > meta_low["tp_pct"]
        assert abs(meta_hi["target_r"] - meta_low["target_r"]) < 1e-9

    def test_target_r_ratio_is_preserved(self):
        """tp_pct / stop_pct MUST equal the configured target R."""
        from execution_engine.broker.orders import compute_bracket_levels
        from shared.config import settings

        bars = _make_bars(atr_pct=0.02)

        long_horizon_signal = _make_signal(horizon="3rd_order")
        _, _, meta = compute_bracket_levels(long_horizon_signal, 100.0, bars)
        assert abs(meta["target_r"] - settings.BRACKET_TARGET_R_LONG_HORIZON) < 1e-9
        assert abs(meta["tp_pct"] - meta["stop_pct"] * meta["target_r"]) < 1e-9

        tier3_signal = _make_signal(horizon="3rd_order", tier3=True)
        _, _, meta_t3 = compute_bracket_levels(tier3_signal, 100.0, bars)
        assert abs(meta_t3["target_r"] - settings.BRACKET_TARGET_R_TIER3_SHOCK) < 1e-9

    def test_min_stop_pct_floor_applied(self):
        """An abnormally-tight ATR must NOT produce sub-MIN_STOP_PCT stops."""
        from execution_engine.broker.orders import compute_bracket_levels
        from shared.config import settings

        tight_bars = _make_bars(atr_pct=0.001)  # 10 bps noise
        signal = _make_signal()
        _, _, meta = compute_bracket_levels(signal, 100.0, tight_bars)
        assert meta["stop_pct"] >= float(settings.MIN_STOP_PCT) - 1e-12

    def test_max_stop_pct_cap_applied(self):
        """A cataclysmic ATR must NOT produce >MAX_STOP_PCT stops."""
        from execution_engine.broker.orders import compute_bracket_levels
        from shared.config import settings

        wild_bars = _make_bars(atr_pct=0.20)  # 20% daily range
        signal = _make_signal()
        _, _, meta = compute_bracket_levels(signal, 100.0, wild_bars)
        assert meta["stop_pct"] <= float(settings.MAX_STOP_PCT) + 1e-12

    def test_fallback_when_bars_missing(self):
        """No bars → fall back to legacy fixed-pct bracket."""
        from execution_engine.broker.orders import compute_bracket_levels
        from shared.config import settings

        signal = _make_signal(horizon="3rd_order")  # long horizon
        sl, tp, meta = compute_bracket_levels(signal, 100.0, bars=None)

        assert meta["mode"] == "fallback_fixed_pct"
        assert abs(meta["stop_pct"] - settings.BRACKET_STOP_LOSS_PCT) < 1e-12
        assert abs(meta["tp_pct"] - settings.BRACKET_TAKE_PROFIT_PCT_LONG_HORIZON) < 1e-12

    def test_short_direction_flips_stop_above_entry(self):
        """SHORT bracket: stop is ABOVE entry, TP is BELOW entry."""
        from execution_engine.broker.orders import compute_bracket_levels

        bars = _make_bars(atr_pct=0.02)
        long_sig = _make_signal(direction="LONG")
        short_sig = _make_signal(direction="SHORT")
        entry = 100.0

        sl_l, tp_l, _ = compute_bracket_levels(long_sig, entry, bars)
        sl_s, tp_s, _ = compute_bracket_levels(short_sig, entry, bars)

        assert sl_l < entry < tp_l, f"LONG bracket inverted: sl={sl_l} entry={entry} tp={tp_l}"
        assert tp_s < entry < sl_s, f"SHORT bracket inverted: sl={sl_s} entry={entry} tp={tp_s}"


# ── Batch-1/D: conviction-scaled sizing ─────────────────────────────


class TestConvictionScaledSizing:
    def test_below_floor_conviction_zeros_out_size(self):
        """A signal below the supervisor floor must carry zero size."""
        from execution_engine.math.kelly import _conviction_size_multiplier
        from shared.config import settings

        below = float(settings.SUPERVISOR_MIN_CONFIDENCE) - 0.01
        assert _conviction_size_multiplier(below) == 0.0

    def test_at_floor_conviction_is_zero(self):
        from execution_engine.math.kelly import _conviction_size_multiplier
        from shared.config import settings

        assert _conviction_size_multiplier(float(settings.SUPERVISOR_MIN_CONFIDENCE)) == 0.0

    def test_at_one_conviction_is_full_size(self):
        from execution_engine.math.kelly import _conviction_size_multiplier

        assert _conviction_size_multiplier(1.0) == 1.0

    def test_intermediate_conviction_scales_linearly(self):
        """Midpoint between floor and 1.0 → multiplier ≈ 0.5."""
        from execution_engine.math.kelly import _conviction_size_multiplier
        from shared.config import settings

        floor = float(settings.SUPERVISOR_MIN_CONFIDENCE)
        mid = (floor + 1.0) / 2.0
        assert abs(_conviction_size_multiplier(mid) - 0.5) < 1e-9

    def test_marginal_signal_gets_much_less_size_than_high_conviction(self):
        """The business-level invariant: a 0.62 signal size < 25% of a 0.95 signal size."""
        from execution_engine.math.kelly import _conviction_size_multiplier

        assert _conviction_size_multiplier(0.62) < 0.25 * _conviction_size_multiplier(0.95)

    def test_nan_inf_conviction_safe(self):
        """Sanitation: non-finite inputs (NaN, ±inf) MUST fall to zero.

        Non-finite values are never a legitimate conviction; treating
        them as "max edge" would let a miscalibrated upstream silently
        push the sizing multiplier to 1.0. We treat them as "no
        information" and return 0.
        """
        import math

        from execution_engine.math.kelly import _conviction_size_multiplier

        assert _conviction_size_multiplier(math.nan) == 0.0
        assert _conviction_size_multiplier(math.inf) == 0.0
        assert _conviction_size_multiplier(-math.inf) == 0.0

    def test_optimal_allocation_applies_conviction_multiplier(self):
        """End-to-end: optimal_allocation_for_signal with low conviction
        must produce strictly smaller Kelly fraction than with high
        conviction, other inputs equal.

        The input combo below yields a positive Kelly fraction so the
        multiplier's effect is observable in the share count (both
        sides of a zero-fraction comparison would trivially pass).
        """
        from execution_engine.math.kelly import optimal_allocation_for_signal

        # win_loss_ratio = 0.12/0.15 = 0.8; p=0.68 →
        # f* = 0.68 - 0.32/0.8 = 0.28 → half-kelly = 0.14
        # → capped at MAX_POSITION_PCT, leaves headroom for conviction scaling
        low = optimal_allocation_for_signal(
            portfolio_equity=1_000_000,
            win_probability=0.68,
            expected_return=0.12,
            volatility=0.15,
            current_price=50.0,
            enforce_ev_hurdle=False,
            conviction=0.62,
        )
        high = optimal_allocation_for_signal(
            portfolio_equity=1_000_000,
            win_probability=0.68,
            expected_return=0.12,
            volatility=0.15,
            current_price=50.0,
            enforce_ev_hurdle=False,
            conviction=0.95,
        )
        assert high[1] > 0, (
            f"test setup bug: Kelly should be positive "
            f"(high allocation={high[0]}, shares={high[1]})"
        )
        # Fraction AND share count must be strictly smaller for low conviction.
        assert low[0] < high[0], f"low conviction fraction {low[0]} must be < high {high[0]}"
        assert low[1] < high[1], (
            f"conviction scaling did not reduce share count "
            f"(low={low[1]}, high={high[1]}) — Batch-1/D regression"
        )

    def test_optimal_allocation_without_conviction_kwarg_is_backward_compat(self):
        """Callers that don't pass conviction get legacy unscaled behaviour."""
        from execution_engine.math.kelly import optimal_allocation_for_signal

        legacy = optimal_allocation_for_signal(
            portfolio_equity=1_000_000,
            win_probability=0.68,
            expected_return=0.12,
            volatility=0.15,
            current_price=50.0,
            enforce_ev_hurdle=False,
        )
        explicit_full = optimal_allocation_for_signal(
            portfolio_equity=1_000_000,
            win_probability=0.68,
            expected_return=0.12,
            volatility=0.15,
            current_price=50.0,
            enforce_ev_hurdle=False,
            conviction=1.0,
        )
        # conviction=1.0 multiplier == 1.0, so it MUST match legacy.
        assert legacy == explicit_full
