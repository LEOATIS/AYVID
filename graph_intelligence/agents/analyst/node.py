"""Specialized deep-dive analyst agents — the core of the agent swarm.

Unlike single-pass GPT calls, these agents:
1. Receive domain-specific system prompts encoding real expertise
2. Get quantitative data (not just news) — Hurst, ATR, regime, graph metrics
3. Iterate with follow-up questions when initial analysis is ambiguous
4. Produce structured opinions that feed into the multi-agent debate

Latency optimization: uses streaming with early direction extraction so the
debate can begin as soon as direction+confidence are known, without waiting
for the full thesis text.
"""

from __future__ import annotations

import json
import re
import time

from langchain_core.messages import HumanMessage, SystemMessage

from graph_intelligence.agents.analyst.prompts import (
    ANALYST_DEEP_DIVE,
    FUNDAMENTALS_SYSTEM,
    MACRO_SYSTEM,
    SUPPLY_CHAIN_SYSTEM,
    TECHNICALS_SYSTEM,
)
from graph_intelligence.graph_state.state import CausalArbitrageState
from shared.logger import get_logger

log = get_logger("agent.analyst")

_swarm_latency_ms: float = 0.0


def get_swarm_latency_ms() -> float:
    return _swarm_latency_ms


def _extract_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Bracket-counting extraction instead of greedy regex.
    # The regex r"\{.*\}" with DOTALL causes catastrophic backtracking
    # on malformed LLM output with nested braces.
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


def _try_early_extract(partial_text: str) -> dict | None:
    """Attempt to extract direction and confidence from a partial LLM stream.

    Returns a dict with at least 'direction' and 'confidence' if found,
    even before the full response is complete. This lets the debate
    protocol start aggregating as soon as the critical fields appear.
    """
    direction_match = re.search(r'"direction"\s*:\s*"(LONG|SHORT|HOLD)"', partial_text)
    confidence_match = re.search(r'"confidence"\s*:\s*(0\.\d+|1\.0|0|1)', partial_text)

    if direction_match and confidence_match:
        return {
            "direction": direction_match.group(1),
            "confidence": float(confidence_match.group(1)),
        }
    return None


def _get_llm(temperature: float = 0.1):
    from shared.llm_client import get_langchain_chat_model

    return get_langchain_chat_model(temperature=temperature, max_tokens=1500)


async def _run_specialist_streaming(
    system_prompt: str,
    state: CausalArbitrageState,
    quant_data: str,
    analyst_name: str,
) -> dict:
    """Run a specialist with streaming — extract direction as soon as it appears.

    Instead of waiting for the full LLM response (~5-10s), we stream tokens
    and extract direction+confidence as soon as they appear in the JSON.
    For the debate protocol, direction+confidence is all that matters;
    the thesis text is logged but doesn't affect the trading decision.
    """
    # P1-L: inject the researcher debate consensus so each specialist
    # analyst sees the bull/bear facilitator verdict as an additional
    # input. Previously the consensus was computed and dropped — the
    # swarm ran blind to the debate.
    from graph_intelligence.agents.researchers.nodes import (
        render_researcher_consensus_block,
    )

    prompt = ANALYST_DEEP_DIVE.format(
        shock_ticker=state["shock_ticker"],
        target_ticker=state.get("target_ticker", "UNKNOWN"),
        direction=state.get("proposed_action", "UNKNOWN"),
        researcher_consensus_block=render_researcher_consensus_block(state),
        quant_data=quant_data,
        timeline=json.dumps(state["timeline"][:10], indent=2, default=str),
        topology=state.get("graph_topology_json", "No topology")[:2000],
    )

    llm = _get_llm(temperature=0.15)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ]

    t0 = time.perf_counter()
    chunks: list[str] = []
    early_result: dict | None = None

    try:
        async for chunk in llm.astream(messages):
            token = chunk.content if hasattr(chunk, "content") else str(chunk)
            chunks.append(token)

            if early_result is None:
                partial = "".join(chunks)
                early_result = _try_early_extract(partial)
                if early_result:
                    early_ms = (time.perf_counter() - t0) * 1000
                    log.info(
                        f"  {analyst_name}: early extract {early_result['direction']} "
                        f"@ {early_result['confidence']:.2f} in {early_ms:.0f}ms"
                    )
    except Exception:
        pass

    full_content = "".join(chunks)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    try:
        cleaned = _extract_json(full_content)
        parsed = json.loads(cleaned)
        thesis = parsed.get("thesis", full_content[:300])
        confidence = float(parsed.get("confidence", 0.5))
        direction = parsed.get("direction", "HOLD")
        log.info(
            f"  {analyst_name}: {direction} @ {confidence:.2f} "
            f"({elapsed_ms:.0f}ms) — {thesis[:80]}..."
        )
        return {
            "analyst": analyst_name,
            "thesis": thesis,
            "confidence": confidence,
            "direction": direction,
            "full_analysis": parsed,
            "raw_content": full_content,
            "latency_ms": round(elapsed_ms, 1),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        if early_result:
            return {
                "analyst": analyst_name,
                "thesis": full_content[:300],
                "confidence": early_result["confidence"],
                "direction": early_result["direction"],
                "full_analysis": {},
                "raw_content": full_content,
                "latency_ms": round(elapsed_ms, 1),
            }
        log.warning(f"  {analyst_name}: parse failed ({elapsed_ms:.0f}ms)")
        return {
            "analyst": analyst_name,
            "thesis": full_content[:300],
            "confidence": 0.3,
            "direction": "HOLD",
            "full_analysis": {},
            "raw_content": full_content,
            "latency_ms": round(elapsed_ms, 1),
        }


async def _run_specialist(
    system_prompt: str,
    state: CausalArbitrageState,
    quant_data: str,
    analyst_name: str,
) -> dict:
    """Run a single specialist — uses streaming for lower latency."""
    return await _run_specialist_streaming(system_prompt, state, quant_data, analyst_name)


async def run_analyst_swarm(
    state: CausalArbitrageState,
    quant_data: str = "",
) -> list[dict]:
    """Deploy all four specialist analysts in parallel on the same signal.

    Each analyst independently evaluates the proposed trade from their
    domain-specific perspective. Uses streaming for early direction extraction.
    The debate protocol then aggregates their opinions to produce a final
    conviction score.
    """
    import asyncio

    global _swarm_latency_ms

    log.info(
        f"Deploying analyst swarm on {state.get('target_ticker', '?')} "
        f"(4 specialists in parallel, streaming)"
    )

    t0 = time.perf_counter()

    analysts = [
        (FUNDAMENTALS_SYSTEM, "Fundamentals"),
        (TECHNICALS_SYSTEM, "Technicals"),
        (MACRO_SYSTEM, "Macro"),
        (SUPPLY_CHAIN_SYSTEM, "Supply Chain"),
    ]

    # Phase 5: the PatternAgent fires as a 5th specialist, in parallel
    # with the 4 text-based analysts. It reads chart geometry via a
    # vision LLM and contributes a direction/confidence opinion the
    # debate aggregator treats as orthogonal evidence.
    from graph_intelligence.agents.pattern import pattern_analyst_opinion

    tasks = [
        _run_specialist(sys_prompt, state, quant_data, name) for sys_prompt, name in analysts
    ] + [pattern_analyst_opinion(state, quant_data)]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    _swarm_latency_ms = (time.perf_counter() - t0) * 1000

    opinions: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning(f"Analyst failed: {r}")
            continue
        opinions.append(r)

    log.info(f"Analyst swarm complete: {len(opinions)} opinions in {_swarm_latency_ms:.0f}ms")
    return opinions
