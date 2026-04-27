"""Trade Auditor agent — stress-tests proposed signals before execution.

Second node in the LangGraph DAG. Challenges the mapper's thesis
and either approves, adjusts confidence, or kills the trade.
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from graph_intelligence.agents.auditor.prompts import AUDITOR_REVIEW, AUDITOR_SYSTEM
from graph_intelligence.graph_state.state import CausalArbitrageState
from shared.logger import get_logger

log = get_logger("agent.auditor")


# ── Deterministic Tier-1 kill-switches (run before the LLM) ──────────
# The LLM is expensive and sometimes optimistic. The institutional
# pattern is: hard quantitative gates first, then LLM judgement on
# what survives. These thresholds mirror the orchestrator / debate
# constants so the whole pipeline speaks the same risk language.
from shared.config import settings as _settings

AUDITOR_MIN_CONFIDENCE: float = _settings.MIN_CONVICTION_AUDITOR
AUDITOR_MIN_CONFIDENCE_CAUSAL: float = _settings.MIN_CONVICTION_CAUSAL_OVERRIDE

_LONG_VOL_ALPHA_HORIZONS = {"1st_order", "first_order", "crisis"}


def _deterministic_auditor_kill(state: CausalArbitrageState) -> tuple[bool, str]:
    """Cheap pre-LLM kill checks. Returns (should_kill, reason)."""
    confidence = float(state.get("confidence", 0.0) or 0.0)
    rationale = (state.get("rationale") or "").lower()
    alpha_horizon = (state.get("alpha_horizon") or "").lower()

    # Hard floor: sub-threshold conviction cannot be saved by debate
    if confidence < AUDITOR_MIN_CONFIDENCE:
        return True, (
            f"confidence {confidence:.2f} < {AUDITOR_MIN_CONFIDENCE:.2f} — "
            f"sub-threshold for Tier-1 execution"
        )

    # Long-vol / crisis-alpha trades must clear the causal-override bar
    is_long_vol = any(
        kw in rationale
        for kw in (
            "long put",
            "naked put",
            "crisis put",
            "buying vol",
            "shock put",
            "decline put",
            "short equity",
        )
    )
    if is_long_vol and confidence < AUDITOR_MIN_CONFIDENCE_CAUSAL:
        return True, (
            f"long-vol thesis requires causal conviction ≥ "
            f"{AUDITOR_MIN_CONFIDENCE_CAUSAL:.2f}, got {confidence:.2f}"
        )

    return False, ""


def _extract_json(text: str) -> str:
    """Strip markdown code fences and extract raw JSON from LLM output."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _get_llm():
    from shared.llm_client import get_langchain_chat_model

    return get_langchain_chat_model(temperature=0.2, max_tokens=1000)


async def auditor_node(state: CausalArbitrageState) -> dict:
    """LangGraph node: Review and stress-test the proposed trade signal.

    Deterministic Tier-1 gates run *before* the LLM so obviously
    sub-noise trades are killed without burning tokens or risking the
    LLM being talked into a bad trade.
    """
    log.info(f"Auditor reviewing [bold]{state.get('target_ticker', '?')}[/bold] signal")

    should_kill, kill_reason = _deterministic_auditor_kill(state)
    if should_kill:
        log.warning(f"Auditor DETERMINISTIC KILL: {kill_reason}")
        return {
            "messages": [],
            "proposed_action": "HOLD",
            "confidence": 0.0,
            "risk_assessment": f"Deterministic kill: {kill_reason}",
            "iteration": state["iteration"] + 1,
        }

    # P1-L: surface the researcher debate consensus to the auditor.
    from graph_intelligence.agents.researchers.nodes import (
        render_researcher_consensus_block,
    )

    prompt = AUDITOR_REVIEW.format(
        shock_ticker=state["shock_ticker"],
        target_ticker=state.get("target_ticker", "UNKNOWN"),
        alpha_horizon=state.get("alpha_horizon", "unknown"),
        confidence=state.get("confidence", 0.0),
        rationale=state.get("rationale", "No rationale provided"),
        researcher_consensus_block=render_researcher_consensus_block(state),
        timeline=json.dumps(state["timeline"][:15], indent=2, default=str),
        graph_topology=state.get("graph_topology_json", "No topology"),
    )

    llm = _get_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(content=AUDITOR_SYSTEM),
            HumanMessage(content=prompt),
        ]
    )

    content = response.content
    try:
        cleaned = _extract_json(content)
        parsed = json.loads(cleaned)
        approved = parsed.get("approved", False)
        confidence_adj = parsed.get("confidence_adjustment", 0.0)
        risk_assessment = parsed.get("risk_assessment", content)
        action = parsed.get("proposed_action", "HOLD")
        concerns = parsed.get("concerns", [])
        kill_reasons = parsed.get("kill_reasons", [])
    except (json.JSONDecodeError, TypeError):
        approved = False
        confidence_adj = -0.1
        risk_assessment = content
        action = "HOLD"
        concerns = []
        kill_reasons = []

    new_confidence = max(0.0, min(1.0, state.get("confidence", 0.5) + confidence_adj))

    if kill_reasons:
        action = "HOLD"
        new_confidence = 0.0
        log.warning(f"Auditor KILLED trade: {kill_reasons}")
    elif approved:
        log.info(f"Auditor APPROVED: {action} at confidence {new_confidence:.2f}")
    else:
        action = "HOLD"
        log.info(f"Auditor REJECTED — concerns: {concerns}")

    return {
        "messages": [response],
        "proposed_action": action,
        "confidence": new_confidence,
        "risk_assessment": risk_assessment,
        "iteration": state["iteration"] + 1,
    }
