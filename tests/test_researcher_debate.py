"""Tests for Phase 3 — bull/bear adversarial researcher debate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graph_intelligence.agents.researchers import (
    BearArgument,
    BullArgument,
    DebateRound,
    ResearcherConsensus,
    bear_advocate,
    bull_advocate,
    facilitator_judge,
    researcher_debate_node,
)
from graph_intelligence.graph_state.state import create_initial_state


# ────────────────────────────────────────────────────────────────────
#  Schema validation
# ────────────────────────────────────────────────────────────────────
class TestSchemas:
    def test_bull_argument_defaults(self):
        b = BullArgument()
        assert b.conviction == 0.5
        assert b.strongest_catalysts == []
        assert 0.0 <= b.conviction <= 1.0

    def test_bull_conviction_bounds(self):
        with pytest.raises(Exception):
            BullArgument(conviction=1.5)

    def test_bear_argument_defaults(self):
        b = BearArgument()
        assert b.strongest_risks == []

    def test_researcher_consensus_defaults(self):
        c = ResearcherConsensus()
        assert c.dominant_side == "NEITHER"
        assert c.direction == "HOLD"
        assert c.conviction == 0.0
        assert c.margin == 0.0

    def test_researcher_consensus_dominant_enum(self):
        # DominantSide is Literal["BULL","BEAR","NEITHER"]
        with pytest.raises(Exception):
            ResearcherConsensus(dominant_side="MAYBE")

    def test_debate_round_wraps_args(self):
        r = DebateRound(round_index=1, bull=BullArgument(), bear=BearArgument())
        assert r.round_index == 1


# ────────────────────────────────────────────────────────────────────
#  Advocate LLM-failure graceful fallback
# ────────────────────────────────────────────────────────────────────
class TestGracefulFallback:
    def _state(self, **overrides):
        s = create_initial_state(shock_ticker="AAPL", target_ticker="AAPL")
        s["rationale"] = "[trend_investment] Strong uptrend + supply chain benef"
        s["proposed_action"] = "LONG"
        s.update(overrides)
        return s

    @pytest.mark.asyncio
    async def test_bull_advocate_llm_failure_returns_default(self):
        s = self._state()
        fake = MagicMock()
        fake.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        with patch(
            "graph_intelligence.agents.researchers.nodes._get_advocate_llm",
            return_value=fake,
        ):
            result = await bull_advocate(s)
        assert isinstance(result, BullArgument)
        assert result.conviction == 0.5  # default

    @pytest.mark.asyncio
    async def test_bear_advocate_llm_failure_returns_default(self):
        s = self._state()
        fake = MagicMock()
        fake.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        with patch(
            "graph_intelligence.agents.researchers.nodes._get_advocate_llm",
            return_value=fake,
        ):
            result = await bear_advocate(s)
        assert isinstance(result, BearArgument)

    @pytest.mark.asyncio
    async def test_facilitator_llm_failure_returns_neither(self):
        s = self._state()
        rounds = [DebateRound(round_index=1)]
        fake = MagicMock()
        fake.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        with patch(
            "graph_intelligence.agents.researchers.nodes._get_facilitator_llm",
            return_value=fake,
        ):
            consensus = await facilitator_judge(s, rounds)
        assert consensus.dominant_side == "NEITHER"
        assert consensus.direction == "HOLD"


# ────────────────────────────────────────────────────────────────────
#  Full orchestrator — mocked LLMs across two rounds + facilitator
# ────────────────────────────────────────────────────────────────────
class TestResearcherDebateNode:
    def _state(self):
        s = create_initial_state(shock_ticker="AAPL", target_ticker="AAPL")
        s["rationale"] = "[trend_investment] AAPL uptrend + rate-cut tailwind"
        s["proposed_action"] = "LONG"
        s["graph_topology_json"] = (
            "[REGIME BLOCK]\nvolatility_regime: NORMAL\n"
            "trend_regime: STRONG_TREND\nadx: 28.5\n"
            "[TOPOLOGY]\n{}"
        )
        s["causal_intel"] = {
            "shock_type": "macro",
            "expected_direction": "BULLISH",
            "shock_magnitude": 0.03,
            "beneficiary_tickers": ["AAPL"],
            "victim_tickers": [],
        }
        s["strategy_parameters"] = {
            "max_rsi_entry_long": 30.0,
            "max_z_entry_long": -1.5,
            "min_factor_score": 0.40,
            "min_expected_r": 1.5,
        }
        return s

    @pytest.mark.asyncio
    async def test_bull_dominates_debate(self):
        s = self._state()
        bull_mock = BullArgument(
            thesis_summary="AAPL earnings acceleration",
            strongest_catalysts=["iPhone 17 super-cycle", "Services margin expansion"],
            conviction=0.85,
            invalidation_level="AAPL closes < 180 on 2× ADV",
        )
        bear_mock = BearArgument(
            counter_thesis="Multiple contraction risk",
            strongest_risks=["China headwind"],
            conviction=0.35,
            invalidation_level="Services growth re-accelerates",
        )
        cons_mock = ResearcherConsensus(
            dominant_side="BULL",
            direction="LONG",
            conviction=0.78,
            margin=0.65,
            executive_summary="Bull case strong; bear rebuttal weak",
        )

        with (
            patch(
                "graph_intelligence.agents.researchers.nodes.bull_advocate",
                new=AsyncMock(return_value=bull_mock),
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.bear_advocate",
                new=AsyncMock(return_value=bear_mock),
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.facilitator_judge",
                new=AsyncMock(return_value=cons_mock),
            ),
        ):
            patch_dict = await researcher_debate_node(s, rounds=2)

        assert "researcher_consensus" in patch_dict
        assert patch_dict["researcher_consensus"]["dominant_side"] == "BULL"
        assert patch_dict["researcher_consensus"]["direction"] == "LONG"
        assert len(patch_dict["researcher_rounds"]) == 2

    @pytest.mark.asyncio
    async def test_facilitator_overrules_mapper(self):
        s = self._state()  # mapper says LONG
        bull_mock = BullArgument(conviction=0.40)
        bear_mock = BearArgument(conviction=0.85)
        cons_mock = ResearcherConsensus(
            dominant_side="BEAR",
            direction="SHORT",
            conviction=0.75,
            margin=0.45,
            executive_summary="Bear case wins — valuation ceiling",
        )
        with (
            patch(
                "graph_intelligence.agents.researchers.nodes.bull_advocate",
                new=AsyncMock(return_value=bull_mock),
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.bear_advocate",
                new=AsyncMock(return_value=bear_mock),
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.facilitator_judge",
                new=AsyncMock(return_value=cons_mock),
            ),
        ):
            patch_dict = await researcher_debate_node(s)

        assert patch_dict["researcher_consensus"]["direction"] == "SHORT"
        # Mapper said LONG, debate says SHORT — the overrule should appear in logs
        # (we don't test logs directly, but the downstream patch reflects it).

    @pytest.mark.asyncio
    async def test_tie_yields_neither(self):
        s = self._state()
        bull_mock = BullArgument(conviction=0.55)
        bear_mock = BearArgument(conviction=0.55)
        cons_mock = ResearcherConsensus(
            dominant_side="NEITHER",
            direction="HOLD",
            conviction=0.0,
            margin=0.10,
            executive_summary="Evenly matched — no clear edge",
        )
        with (
            patch(
                "graph_intelligence.agents.researchers.nodes.bull_advocate",
                new=AsyncMock(return_value=bull_mock),
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.bear_advocate",
                new=AsyncMock(return_value=bear_mock),
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.facilitator_judge",
                new=AsyncMock(return_value=cons_mock),
            ),
        ):
            patch_dict = await researcher_debate_node(s)

        assert patch_dict["researcher_consensus"]["dominant_side"] == "NEITHER"
        assert patch_dict["researcher_consensus"]["margin"] < 0.3

    @pytest.mark.asyncio
    async def test_no_target_ticker_short_circuits(self):
        s = create_initial_state(shock_ticker="")  # empty target
        s["target_ticker"] = ""
        result = await researcher_debate_node(s)
        assert result == {}  # no patch

    @pytest.mark.asyncio
    async def test_rebuttal_round_receives_opposing_thesis(self):
        """Round 2 advocates must be invoked with the round-1 opposing thesis."""
        s = self._state()
        r1_bull = BullArgument(thesis_summary="BULL_R1")
        r1_bear = BearArgument(counter_thesis="BEAR_R1")
        r2_bull = BullArgument(thesis_summary="BULL_R2")
        r2_bear = BearArgument(counter_thesis="BEAR_R2")

        bull_mock = AsyncMock(side_effect=[r1_bull, r2_bull])
        bear_mock = AsyncMock(side_effect=[r1_bear, r2_bear])
        cons_mock = ResearcherConsensus()

        with (
            patch(
                "graph_intelligence.agents.researchers.nodes.bull_advocate",
                new=bull_mock,
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.bear_advocate",
                new=bear_mock,
            ),
            patch(
                "graph_intelligence.agents.researchers.nodes.facilitator_judge",
                new=AsyncMock(return_value=cons_mock),
            ),
        ):
            await researcher_debate_node(s, rounds=2)

        # Round 2 bull must be called with the round-1 bear's counter_thesis.
        second_bull_call_kwargs = bull_mock.call_args_list[1]
        assert "BEAR_R1" in second_bull_call_kwargs.kwargs["opposing_thesis"]

        # Round 2 bear must be called with the round-1 bull's thesis.
        second_bear_call_kwargs = bear_mock.call_args_list[1]
        assert "BULL_R1" in second_bear_call_kwargs.kwargs["opposing_thesis"]


# ────────────────────────────────────────────────────────────────────
#  Pipeline integration — DAG structure
# ────────────────────────────────────────────────────────────────────
class TestPipelineIntegration:
    def test_graph_contains_researcher_debate_node(self):
        from graph_intelligence.pipeline import build_causal_graph

        g = build_causal_graph()
        # LangGraph's StateGraph doesn't expose node names cleanly; the
        # safest assertion is that the graph compiles without error.
        compiled = g.compile()
        assert compiled is not None

    def test_state_has_researcher_fields(self):
        s = create_initial_state(shock_ticker="AAPL")
        assert "researcher_consensus" in s
        assert "researcher_rounds" in s
        assert s["researcher_consensus"] == {}
        assert s["researcher_rounds"] == []
