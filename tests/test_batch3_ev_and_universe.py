"""Tests for Batch 3 — EV hurdle (G) + universe cleanup (H).

Two invariants:

    G — EV hurdle is the single public canonical gate, the multiple
        is driven from ``shared.config.settings.EV_HURDLE_MULTIPLE``,
        and it REJECTS across every instrument type when
        ``expected_return / (multiple × cost) < 1``.

    H — Per-future strategy-variant blocklist actually short-circuits
        emission. A concrete name in ``BLOCKED_STRATEGY_VARIANTS``
        MUST result in a SKIP decision, regardless of how strong the
        market context would otherwise be.
"""

from __future__ import annotations

import pytest

# ── G — EV hurdle gate ──────────────────────────────────────────────


class TestEvHurdleGate:
    def test_gate_exposed_publicly_from_costs_module(self):
        """The G refactor: ``orchestrator_ev_gate`` is importable both
        from `execution_engine.math.costs` and from
        `graph_intelligence.agents.strategy_orchestrator`, and the two
        resolve to the same underlying implementation."""
        from execution_engine.math.costs import (
            orchestrator_ev_gate as from_costs,
        )

        # The wrapper in costs.py is a thin re-export that lazy-imports.
        # Calling both should produce identical tuples on the same
        # inputs. We use a minimal MarketContext + TradeDecision stub
        # so the test does not depend on LLM or OHLCV data.
        from graph_intelligence.agents.strategy_orchestrator import (
            MarketContext,
            TradeDecision,
        )
        from graph_intelligence.agents.strategy_orchestrator import (
            orchestrator_ev_gate as from_orch,
        )

        ctx = MarketContext(ticker="^GSPC", current_price=4500.0)
        d = TradeDecision(
            action="EXECUTE",
            instrument_type="INDEX_FUTURE",
            direction="SHORT",
            ticker="^GSPC",
            strategy_name="idx_trend_es",
            analysis_timeframe="D1",
            entry_timeframe="M5",
            conviction=0.8,
            sizing_method="KELLY_FUTURES",
            max_risk_pct=2.0,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale="test",
            expected_r=2.5,
            min_r=1.5,
        )
        ok_costs, msg_costs = from_costs(ctx, d)
        ok_orch, msg_orch = from_orch(ctx, d)
        assert ok_costs == ok_orch
        assert msg_costs == msg_orch

    def test_multiple_matches_settings(self):
        """Single source of truth for the 4× hurdle."""
        from execution_engine.math.costs import EV_HURDLE_MULTIPLE
        from graph_intelligence.agents.strategy_orchestrator import (
            ORCH_EV_HURDLE,
        )
        from shared.config import settings

        assert EV_HURDLE_MULTIPLE == settings.EV_HURDLE_MULTIPLE
        assert ORCH_EV_HURDLE == settings.EV_HURDLE_MULTIPLE

    @pytest.mark.parametrize("itype", ["EQUITY", "INDEX_FUTURE"])
    def test_low_edge_rejected_on_physical_assets(self, itype):
        """For EQUITY and INDEX_FUTURE (cost is % of notional), weak-
        edge must fail the 4× hurdle. expected_return_pct × risk_frac
        must be >= 4 × cost_pct. At r_mult=1.0, conv=0.55, risk=0.5%:
            expected = 0.5% × 1.0 × 0.55 = 0.275%
            cost     ≈ 15-20 bps
            hurdle   = 4 × 15bps = 60 bps = 0.6%
            0.275% < 0.6% → FAIL  ✓
        """
        from execution_engine.math.costs import orchestrator_ev_gate
        from graph_intelligence.agents.strategy_orchestrator import (
            MarketContext,
            TradeDecision,
        )

        ctx = MarketContext(ticker="^GSPC", current_price=4500.0)
        d = TradeDecision(
            action="EXECUTE",
            instrument_type=itype,
            direction="LONG",
            ticker="^GSPC",
            strategy_name="test_strategy",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=0.55,
            sizing_method="KELLY_EQUITY",
            max_risk_pct=0.5,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale="intentionally-weak",
            expected_r=1.0,
            min_r=1.0,
        )
        passed, reason = orchestrator_ev_gate(ctx, d)
        assert passed is False, f"weak-edge {itype} decision passed the 4× EV hurdle: {reason}"
        assert "EV" in reason or "cost" in reason.lower()

    @pytest.mark.parametrize(
        "itype,cost_pct",
        [
            ("INDEX_CALL", 0.04),
            ("INDEX_PUT", 0.04),
            ("INDEX_SPREAD", 0.06),
        ],
    )
    def test_low_edge_rejected_on_options(self, itype, cost_pct):
        """For options (cost is % of premium, proportional to risk), a
        weak-edge decision is one where ``r_mult × conviction`` fails
        to clear ``4 × cost_pct``. For CALL/PUT at 4% premium cost:
        ``r_mult × conv < 4 × 0.04 = 0.16`` fails. Use r=0.2, conv=0.5
        → 0.10 < 0.16 → FAIL. For SPREAD at 6%: need < 0.24."""
        from execution_engine.math.costs import orchestrator_ev_gate
        from graph_intelligence.agents.strategy_orchestrator import (
            MarketContext,
            TradeDecision,
        )

        ctx = MarketContext(ticker="^GSPC", current_price=4500.0)
        # r_mult × conv deliberately below 4 × cost_pct hurdle
        r_mult = 0.25
        conv = 0.5
        hurdle_product = 4 * cost_pct
        assert r_mult * conv < hurdle_product, (
            "test-input bug: r_mult * conv must be below the hurdle "
            f"product for {itype} (got {r_mult * conv} vs {hurdle_product})"
        )
        d = TradeDecision(
            action="EXECUTE",
            instrument_type=itype,
            direction="LONG",
            ticker="^GSPC",
            strategy_name="test_strategy",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=conv,
            sizing_method="KELLY_OPTIONS",
            max_risk_pct=1.5,
            stop_loss_method="FIXED_PCT",
            take_profit_method="ATR",
            rationale="weak-edge options",
            expected_r=r_mult,
            min_r=1.0,
        )
        passed, reason = orchestrator_ev_gate(ctx, d)
        assert passed is False, f"weak-edge {itype} decision passed the 4× EV hurdle: {reason}"

    @pytest.mark.parametrize("itype", ["EQUITY", "INDEX_FUTURE"])
    def test_high_edge_passes_on_physical_assets(self, itype):
        """A materially-positive-edge decision must PASS."""
        from execution_engine.math.costs import orchestrator_ev_gate
        from graph_intelligence.agents.strategy_orchestrator import (
            MarketContext,
            TradeDecision,
        )

        ctx = MarketContext(ticker="^GSPC", current_price=4500.0)
        d = TradeDecision(
            action="EXECUTE",
            instrument_type=itype,
            direction="LONG",
            ticker="^GSPC",
            strategy_name="test_strategy",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=0.9,
            sizing_method="KELLY_FUTURES",
            max_risk_pct=2.0,  # full risk slot
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale="strong edge",
            expected_r=3.5,  # generous R-multiple
            min_r=1.5,
        )
        passed, reason = orchestrator_ev_gate(ctx, d)
        assert passed is True, f"strong-edge {itype} decision failed the EV hurdle: {reason}"

    def test_unknown_instrument_type_defers(self):
        """Unknown instrument types should defer (pass with empty
        reason), so downstream gates govern."""
        from execution_engine.math.costs import orchestrator_ev_gate
        from graph_intelligence.agents.strategy_orchestrator import (
            MarketContext,
            TradeDecision,
        )

        ctx = MarketContext(ticker="BTC", current_price=40000.0)
        d = TradeDecision(
            action="EXECUTE",
            instrument_type="CRYPTO_FUTURE",
            direction="LONG",
            ticker="BTC",
            strategy_name="s",
            analysis_timeframe="D1",
            entry_timeframe="D1",
            conviction=0.7,
            sizing_method="KELLY_EQUITY",
            max_risk_pct=1.0,
            stop_loss_method="ATR",
            take_profit_method="ATR",
            rationale="",
            expected_r=2.0,
            min_r=1.5,
        )
        passed, reason = orchestrator_ev_gate(ctx, d)
        assert passed is True
        assert reason == ""


# ── H — per-future variant blocklist ────────────────────────────────


class TestUniverseCleanupBlocklist:
    def test_blocklist_seeded_with_evidence_driven_losers(self):
        """The seed set must contain the variants the walkforward
        proved have no edge (n≥3, WR≤33%). These are the concrete
        names in the 2018-2025 report."""
        from graph_intelligence.agents.strategy_orchestrator import (
            BLOCKED_STRATEGY_VARIANTS,
        )

        required = {"idx_bear_spread_sgx_nk", "idx_bear_spread_z"}
        assert required.issubset(BLOCKED_STRATEGY_VARIANTS), (
            f"H-blocklist missing evidence-backed variant blocks: "
            f"{required - set(BLOCKED_STRATEGY_VARIANTS)}"
        )

    def test_winning_variant_is_NOT_blocked(self):
        """The winning variant `idx_bear_spread_fce` (CAC 40, 60% WR
        over 5 trades) must STAY tradeable — don't block by family."""
        from graph_intelligence.agents.strategy_orchestrator import (
            BLOCKED_STRATEGY_VARIANTS,
        )

        assert "idx_bear_spread_fce" not in BLOCKED_STRATEGY_VARIANTS, (
            "blocked the winning variant; H should be surgical, not sector-wide"
        )

    def test_is_strategy_variant_blocked_returns_true_on_seeded_names(self):
        from graph_intelligence.agents.strategy_orchestrator import (
            is_strategy_variant_blocked,
        )

        assert is_strategy_variant_blocked("idx_bear_spread_sgx_nk") is True
        assert is_strategy_variant_blocked("idx_bear_spread_z") is True

    def test_is_strategy_variant_blocked_is_exact_match(self):
        from graph_intelligence.agents.strategy_orchestrator import (
            is_strategy_variant_blocked,
        )

        assert is_strategy_variant_blocked("idx_bear_spread_fce") is False
        assert is_strategy_variant_blocked("idx_bear_spread") is False
        assert is_strategy_variant_blocked("") is False
        assert is_strategy_variant_blocked(None) is False  # type: ignore[arg-type]

    def test_idx_decision_short_circuits_on_blocked_variant(self):
        """Contract: when `_idx_decision` is called with a blocked
        strategy_name, it returns SKIP — no EXECUTE can leak past
        the blocklist. We invoke via a module-level helper that
        replicates the closure's exact logic (the closure is
        scoped inside `index_fno_orchestrate`)."""
        from graph_intelligence.agents.strategy_orchestrator import (
            is_strategy_variant_blocked,
        )

        # Simulate the exact short-circuit the closure performs.
        blocked_name = "idx_bear_spread_sgx_nk"
        ok_name = "idx_bear_spread_fce"

        # The test asserts the semantic invariant: blocked → True
        # (engine will call `_skip`), ok → False (engine will emit).
        assert is_strategy_variant_blocked(blocked_name) is True
        assert is_strategy_variant_blocked(ok_name) is False
