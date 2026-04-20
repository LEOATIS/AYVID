"""Bull/Bear adversarial researcher debate (Phase 3).

After the mapper proposes a directional thesis, the researcher debate
stress-tests it through two rounds of adversarial advocacy before the
analyst swarm fires. A Bull advocate and a Bear advocate build the
strongest possible case for each side, exchange two rounds of
rebuttals, and a Facilitator LLM synthesises the dominant side into
a ``ResearcherConsensus`` that is injected into the analyst-swarm
context.

This adds ~2 LLM rounds to the pipeline (≈ 3-5s at gpt-4o-mini pricing
for the advocates + one deeper synthesis call for the facilitator),
and reduces the largest single failure mode we have: the mapper's
directional bias propagating unchallenged into the analyst swarm
(e.g. "always bullish on shock beneficiaries").

Reference: Xiao et al., *TradingAgents: Multi-Agents LLM Financial
Trading Framework* (arXiv 2412.20138v7), §3.2 Researcher Team.
"""
from graph_intelligence.agents.researchers.schema import (
    BullArgument,
    BearArgument,
    ResearcherConsensus,
    DebateRound,
)
from graph_intelligence.agents.researchers.nodes import (
    researcher_debate_node,
    bull_advocate,
    bear_advocate,
    facilitator_judge,
)

__all__ = [
    "BullArgument",
    "BearArgument",
    "ResearcherConsensus",
    "DebateRound",
    "researcher_debate_node",
    "bull_advocate",
    "bear_advocate",
    "facilitator_judge",
]
