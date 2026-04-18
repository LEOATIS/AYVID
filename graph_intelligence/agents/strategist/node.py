"""Strategist agent — synthesizes macro regime and cross-asset context.

Runs before the mapper to provide institutional-grade macro awareness.
The strategist's output enriches the state so the mapper can factor in
macro conditions when proposing causal trade targets.
"""
from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

from shared.config import settings
from shared.logger import get_logger
from graph_intelligence.graph_state.state import CausalArbitrageState
from graph_intelligence.agents.strategist.prompts import STRATEGIST_SYSTEM, STRATEGIST_ANALYZE

log = get_logger("agent.strategist")


def _extract_json(text: str) -> str:
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
    if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=0.1,
            max_tokens=1200,
        )
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.1,
        max_tokens=1200,
    )


async def _fetch_macro_context() -> str:
    """Gather real-time macro signals for the strategist."""
    context_parts: list[str] = []

    try:
        from data_primitives.extractors.openbb_client import fetch_sector_performance
        sectors = await fetch_sector_performance()
        if sectors:
            sector_str = ", ".join(
                f"{s.get('sector', '?')}: {s.get('change_1d', 0):.2f}%"
                for s in sectors[:10]
            )
            context_parts.append(f"Sector Performance: {sector_str}")
    except Exception as exc:
        log.warning(f"Sector performance fetch failed: {exc}")

    try:
        from data_primitives.extractors.openbb_client import fetch_treasury_rates
        rates = await fetch_treasury_rates()
        if rates:
            latest = rates[-1] if rates else {}
            context_parts.append(f"Treasury Rates: {json.dumps(latest, default=str)[:500]}")
    except Exception as exc:
        log.warning(f"Treasury rates fetch failed: {exc}")

    try:
        from data_primitives.extractors.openbb_client import fetch_index_historical
        vix = await fetch_index_historical("^VIX", days=5)
        if vix:
            context_parts.append(f"VIX: {vix[-1].close:.2f}")
    except Exception as exc:
        log.warning(f"VIX fetch failed: {exc}")

    return "\n".join(context_parts) if context_parts else "No macro data available"


async def strategist_node(state: CausalArbitrageState) -> dict:
    """LangGraph node: Synthesize macro regime context before causal mapping."""
    log.info(f"Strategist analyzing macro context for [bold]{state['shock_ticker']}[/bold]")

    macro_context = await _fetch_macro_context()
    timeline_str = json.dumps(state["timeline"][:15], indent=2, default=str)

    prompt = STRATEGIST_ANALYZE.format(
        shock_ticker=state["shock_ticker"],
        timeline=timeline_str,
        macro_context=macro_context,
    )

    llm = _get_llm()
    response = await llm.ainvoke([
        SystemMessage(content=STRATEGIST_SYSTEM),
        HumanMessage(content=prompt),
    ])

    content = response.content
    try:
        cleaned = _extract_json(content)
        parsed = json.loads(cleaned)
        regime = parsed.get("macro_regime", "TRANSITIONAL")
        risk_multiplier = float(parsed.get("risk_multiplier", 1.0))
        shock_context = parsed.get("shock_context", "")

        macro_summary = (
            f"Regime: {regime} | "
            f"Risk multiplier: {risk_multiplier:.1f}x | "
            f"Context: {shock_context[:200]}"
        )
        log.info(f"Strategist: {regime} regime, risk_multiplier={risk_multiplier:.1f}x")
    except (json.JSONDecodeError, TypeError):
        macro_summary = content[:500]
        risk_multiplier = 1.0
        log.warning("Strategist output parsing failed, using raw content")

    enriched_topology = state.get("graph_topology_json", "")
    if enriched_topology:
        enriched_topology = f"[MACRO CONTEXT]\n{macro_summary}\n\n[TOPOLOGY]\n{enriched_topology}"
    else:
        enriched_topology = f"[MACRO CONTEXT]\n{macro_summary}"

    return {
        "messages": [response],
        "graph_topology_json": enriched_topology,
    }
