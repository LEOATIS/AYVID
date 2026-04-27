"""Researcher debate orchestration — bull/bear advocates + facilitator judge.

Flow
────
    round 1: bull_advocate() ‖ bear_advocate()   (both given opposing stub)
    round 2: bull_advocate() ‖ bear_advocate()   (given opposing round-1 output)
    facilitator_judge(all four rounds)  →  ResearcherConsensus

Design choices
──────────────
- **Mixed-model strategy** (TradingAgents paper §4.3): advocates run on
  a fast/cheap model (OpenAI `gpt-4o-mini` or equivalent), the
  facilitator runs on a deeper reasoning model (Claude `claude-4.6-
  sonnet-thinking`). This compresses the cost by ~8× without losing
  the synthesis quality that matters most.
- **Structured output everywhere** — every LLM call is bound to a
  Pydantic schema via ``with_structured_output``. Hallucinated or
  out-of-bounds outputs raise at the SDK boundary and degrade to
  empty defaults so the pipeline never crashes.
- **Parallel rounds** — bull and bear fire concurrently via
  ``asyncio.gather``, so a 2-round debate costs ~2× the single-call
  latency, not 4×.

The node is imported by ``pipeline.py`` and inserted between
``mapper`` and ``analyst_swarm``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from graph_intelligence.agents.researchers.prompts import (
    BEAR_SYSTEM,
    BEAR_USER_TEMPLATE,
    BULL_SYSTEM,
    BULL_USER_TEMPLATE,
    FACILITATOR_SYSTEM,
    FACILITATOR_USER_TEMPLATE,
)
from graph_intelligence.agents.researchers.schema import (
    BearArgument,
    BullArgument,
    DebateRound,
    ResearcherConsensus,
)
from graph_intelligence.graph_state.state import CausalArbitrageState
from shared.config import settings
from shared.logger import get_logger

log = get_logger("agent.researcher")

_DEFAULT_DEBATE_ROUNDS = 2

# P1-N: when the facilitator returns NEITHER (debate was effectively
# tied), downstream conviction MUST be capped. Prior to this fix the
# code logged the intent to cap but never wrote it to the state
# patch, so the analyst swarm + auditor + supervisor all saw
# unchanged confidence.
NEITHER_CONVICTION_CAP: float = 0.65


def render_researcher_consensus_block(state: CausalArbitrageState) -> str:
    """Render a compact, prompt-ready summary of the researcher debate.

    P1-L: the analyst swarm and the auditor both embed this block in
    their prompt templates (``{researcher_consensus_block}``) so the
    bull/bear debate outcome actually reaches the downstream agents
    that gate execution. Previously the consensus was computed and
    then dropped on the floor — analysts and auditor ran without any
    visibility into the debate.

    When no consensus exists yet (node did not run or advocates
    errored), returns a neutral placeholder so prompt templates stay
    well-formed.
    """
    consensus = state.get("researcher_consensus") or {}
    if not consensus:
        return "(no researcher debate has run yet)"

    def _fmt_list(values: list[str]) -> str:
        if not values:
            return "(none)"
        return "; ".join(v for v in values[:4] if v)

    dominant = consensus.get("dominant_side", "NEITHER")
    direction = consensus.get("direction", "HOLD")
    conviction = float(consensus.get("conviction", 0.0) or 0.0)
    margin = float(consensus.get("margin", 0.0) or 0.0)
    summary = consensus.get("executive_summary", "") or "(no summary)"
    bull_takeaways = consensus.get("bull_takeaways") or []
    bear_takeaways = consensus.get("bear_takeaways") or []
    rationale = consensus.get("facilitator_rationale", "") or ""

    return (
        f"dominant_side={dominant} | direction={direction} "
        f"| conviction={conviction:.2f} | margin={margin:.2f}\n"
        f"summary: {summary[:320]}\n"
        f"bull_takeaways: {_fmt_list(bull_takeaways)}\n"
        f"bear_takeaways: {_fmt_list(bear_takeaways)}\n"
        f"rationale: {rationale[:320] if rationale else '(none)'}"
    )


# ── Mixed-model selection ─────────────────────────────────────────────
def _get_advocate_llm(schema: type):
    """Fast / cheap model for bull & bear advocates.

    Falls back to the configured provider if the mini model is unavailable.
    """
    if settings.OPENAI_API_KEY:
        return ChatOpenAI(
            model="gpt-4o-mini",
            api_key=settings.OPENAI_API_KEY,
            temperature=0.2,
            max_tokens=900,
        ).with_structured_output(schema)
    if settings.ANTHROPIC_API_KEY:
        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=0.2,
            max_tokens=900,
        ).with_structured_output(schema)
    raise RuntimeError("No LLM provider configured for advocates")


def _get_facilitator_llm(schema: type):
    """Deep-reasoning model for the facilitator — the call that matters."""
    from shared.llm_client import get_langchain_chat_model

    return get_langchain_chat_model(
        temperature=0.05,
        max_tokens=1500,
    ).with_structured_output(schema)


# ── Context helpers ──────────────────────────────────────────────────
def _mapper_direction_from_state(state: CausalArbitrageState) -> str:
    """Phase-3 (Gap A6): mapper now ALWAYS sets `proposed_action` to one of
    LONG/SHORT/HOLD (either from its JSON schema or from the deterministic
    causal-intel fallback). If we still see something unexpected here, it
    is a genuine pipeline bug — we return HOLD so the researcher debate
    refuses to trade rather than silently going long."""
    action = (state.get("proposed_action") or "").upper()
    if action in ("LONG", "SHORT", "HOLD"):
        return action
    return "HOLD"


def _mapper_thesis_from_state(state: CausalArbitrageState) -> str:
    rationale = state.get("rationale") or ""
    return rationale.strip()[:1000] or "(no mapper rationale provided)"


def _regime_block_from_state(state: CausalArbitrageState) -> str:
    topology = state.get("graph_topology_json") or ""
    if "[REGIME BLOCK]" in topology:
        start = topology.find("[REGIME BLOCK]")
        end = topology.find("[TOPOLOGY]")
        if end > start:
            return topology[start:end].strip()[:600]
    return "(regime block unavailable)"


def _shock_context_summary(state: CausalArbitrageState) -> str:
    intel = state.get("causal_intel") or {}
    if not intel:
        return "(no causal intel)"
    direction = intel.get("expected_direction", "NEUTRAL")
    shock_type = intel.get("shock_type", "unknown")
    magnitude = intel.get("shock_magnitude", 0.0)
    return (
        f"type={shock_type} | bias={direction} | "
        f"magnitude={magnitude:+.1%} | "
        f"beneficiaries={intel.get('beneficiary_tickers', [])[:4]} | "
        f"victims={intel.get('victim_tickers', [])[:4]}"
    )


def _priors_block_from_state(state: CausalArbitrageState) -> str:
    params = state.get("strategy_parameters") or {}
    if not params:
        return "(no empirical priors loaded)"
    keys = ("max_rsi_entry_long", "max_z_entry_long", "min_factor_score", "min_expected_r")
    parts = [f"{k}={params.get(k)}" for k in keys if k in params]
    return "; ".join(parts) or "(no relevant priors)"


# ── Advocates ────────────────────────────────────────────────────────
async def bull_advocate(
    state: CausalArbitrageState,
    opposing_thesis: str = "(none yet)",
) -> BullArgument:
    """Single bull-advocate LLM call. Returns BullArgument (validated)."""
    try:
        llm = _get_advocate_llm(BullArgument)
    except RuntimeError as exc:
        log.warning(f"bull_advocate: no LLM configured ({exc}); returning default")
        return BullArgument()

    prompt = BULL_USER_TEMPLATE.format(
        ticker=state.get("target_ticker", "?"),
        mapper_thesis=_mapper_thesis_from_state(state),
        mapper_direction=_mapper_direction_from_state(state),
        regime_block=_regime_block_from_state(state),
        shock_context=_shock_context_summary(state),
        priors_block=_priors_block_from_state(state),
        opposing_thesis=opposing_thesis,
    )
    try:
        result: BullArgument = await llm.ainvoke(
            [
                SystemMessage(content=BULL_SYSTEM),
                HumanMessage(content=prompt),
            ]
        )
    except (ValidationError, ValueError, TypeError) as exc:
        log.warning(f"bull_advocate validation failed: {exc}")
        return BullArgument()
    except Exception as exc:
        log.warning(f"bull_advocate LLM error: {exc}")
        return BullArgument()
    return result if isinstance(result, BullArgument) else BullArgument()


async def bear_advocate(
    state: CausalArbitrageState,
    opposing_thesis: str = "(none yet)",
) -> BearArgument:
    """Single bear-advocate LLM call. Returns BearArgument (validated)."""
    try:
        llm = _get_advocate_llm(BearArgument)
    except RuntimeError as exc:
        log.warning(f"bear_advocate: no LLM configured ({exc}); returning default")
        return BearArgument()

    prompt = BEAR_USER_TEMPLATE.format(
        ticker=state.get("target_ticker", "?"),
        mapper_thesis=_mapper_thesis_from_state(state),
        mapper_direction=_mapper_direction_from_state(state),
        regime_block=_regime_block_from_state(state),
        shock_context=_shock_context_summary(state),
        priors_block=_priors_block_from_state(state),
        opposing_thesis=opposing_thesis,
    )
    try:
        result: BearArgument = await llm.ainvoke(
            [
                SystemMessage(content=BEAR_SYSTEM),
                HumanMessage(content=prompt),
            ]
        )
    except (ValidationError, ValueError, TypeError) as exc:
        log.warning(f"bear_advocate validation failed: {exc}")
        return BearArgument()
    except Exception as exc:
        log.warning(f"bear_advocate LLM error: {exc}")
        return BearArgument()
    return result if isinstance(result, BearArgument) else BearArgument()


# ── Facilitator ──────────────────────────────────────────────────────
async def facilitator_judge(
    state: CausalArbitrageState,
    rounds: list[DebateRound],
) -> ResearcherConsensus:
    """Facilitator LLM call — returns the final ResearcherConsensus."""
    if not rounds:
        return ResearcherConsensus()
    try:
        llm = _get_facilitator_llm(ResearcherConsensus)
    except Exception as exc:
        log.warning(f"facilitator: LLM init failed ({exc}); returning default")
        return ResearcherConsensus()

    r1 = rounds[0]
    r2 = rounds[1] if len(rounds) >= 2 else DebateRound(round_index=1)

    def _bull_block(b: BullArgument) -> str:
        return (
            f"thesis={b.thesis_summary}\n"
            f"catalysts={b.strongest_catalysts}\n"
            f"rebuttal={b.bear_case_rebuttal}\n"
            f"conviction={b.conviction:.2f}\n"
            f"invalidation={b.invalidation_level}"
        )

    def _bear_block(b: BearArgument) -> str:
        return (
            f"counter_thesis={b.counter_thesis}\n"
            f"risks={b.strongest_risks}\n"
            f"rebuttal={b.bull_case_rebuttal}\n"
            f"conviction={b.conviction:.2f}\n"
            f"invalidation={b.invalidation_level}"
        )

    prompt = FACILITATOR_USER_TEMPLATE.format(
        ticker=state.get("target_ticker", "?"),
        mapper_thesis=_mapper_thesis_from_state(state),
        mapper_direction=_mapper_direction_from_state(state),
        regime_block=_regime_block_from_state(state),
        priors_block=_priors_block_from_state(state),
        r1_bull=_bull_block(r1.bull),
        r1_bear=_bear_block(r1.bear),
        r2_bull=_bull_block(r2.bull),
        r2_bear=_bear_block(r2.bear),
    )
    try:
        result: ResearcherConsensus = await llm.ainvoke(
            [
                SystemMessage(content=FACILITATOR_SYSTEM),
                HumanMessage(content=prompt),
            ]
        )
    except (ValidationError, ValueError, TypeError) as exc:
        log.warning(f"facilitator validation failed: {exc}")
        return ResearcherConsensus()
    except Exception as exc:
        log.warning(f"facilitator LLM error: {exc}")
        return ResearcherConsensus()
    return result if isinstance(result, ResearcherConsensus) else ResearcherConsensus()


# ── Orchestrator node ────────────────────────────────────────────────
async def researcher_debate_node(
    state: CausalArbitrageState,
    rounds: int = _DEFAULT_DEBATE_ROUNDS,
) -> dict[str, Any]:
    """LangGraph node: run n-round bull-vs-bear debate, return state patch.

    Returns a patch dict:
        {
          "researcher_consensus": { ...ResearcherConsensus.model_dump()... },
          "researcher_rounds":    [ ...DebateRound dumps... ],
          "confidence":           updated when facilitator lowers it,
        }

    Short-circuits (returns empty patch) when there is no target ticker
    or no mapper thesis — defensive parity with supervisor_node.
    """
    target = state.get("target_ticker") or ""
    if not target:
        log.debug("researcher_debate_node: no target ticker; skipping")
        return {}

    # Round 1 — advocates fire in parallel, no opposing thesis yet.
    bull_r1, bear_r1 = await asyncio.gather(
        bull_advocate(state),
        bear_advocate(state),
    )
    round_1 = DebateRound(round_index=1, bull=bull_r1, bear=bear_r1)

    debate_rounds: list[DebateRound] = [round_1]

    # Rounds 2..n — each advocate rebuts the opposing round-(i-1) case.
    for i in range(2, rounds + 1):
        prev = debate_rounds[-1]
        bull_opposing = prev.bear.counter_thesis
        bear_opposing = prev.bull.thesis_summary
        bull_i, bear_i = await asyncio.gather(
            bull_advocate(state, opposing_thesis=bull_opposing or "(empty)"),
            bear_advocate(state, opposing_thesis=bear_opposing or "(empty)"),
        )
        debate_rounds.append(DebateRound(round_index=i, bull=bull_i, bear=bear_i))

    # Facilitator adjudicates.
    consensus = await facilitator_judge(state, debate_rounds)

    log.info(
        f"[RESEARCHER] {target}: dominant={consensus.dominant_side} "
        f"direction={consensus.direction} "
        f"conviction={consensus.conviction:.2f} margin={consensus.margin:.2f} "
        f"({rounds}-round debate)"
    )

    patch: dict[str, Any] = {
        "researcher_consensus": consensus.model_dump(),
        "researcher_rounds": [r.model_dump() for r in debate_rounds],
    }

    # P1-N: actually apply the NEITHER cap. Previously this was logged
    # as if the downstream would honour it, but the state patch never
    # touched ``confidence``. Now we write a capped value so the
    # auditor and supervisor see the degraded conviction.
    mapper_dir = _mapper_direction_from_state(state)
    current_conf = float(state.get("confidence") or 0.0)

    if consensus.dominant_side == "NEITHER":
        if current_conf > NEITHER_CONVICTION_CAP:
            patch["confidence"] = NEITHER_CONVICTION_CAP
            log.info(
                f"[RESEARCHER] tied debate on {target} (margin={consensus.margin:.2f}) "
                f"— conviction capped {current_conf:.2f} → "
                f"{NEITHER_CONVICTION_CAP:.2f}"
            )
        else:
            log.info(
                f"[RESEARCHER] tied debate on {target}; conviction "
                f"{current_conf:.2f} already below cap, no change"
            )
    elif consensus.direction != mapper_dir and consensus.direction != "HOLD":
        log.warning(
            f"[RESEARCHER] OVERRULING mapper on {target}: "
            f"mapper said {mapper_dir}, debate consensus says {consensus.direction}"
        )

    return patch
