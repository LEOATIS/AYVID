"""Strategist agent — synthesizes macro regime, cross-asset context, AND
emits the regime-driven strategy blocklist.

Runs before the mapper. Its `forbidden_strategies` output is surfaced
into the state object so the orchestrator / supervisor can strictly
respect it further down the DAG (Tier-1 regime-gated execution).
"""

from __future__ import annotations

import json
import re

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from graph_intelligence.agents.strategist.prompts import (
    STRATEGIST_ANALYZE,
    STRATEGIST_SYSTEM,
)
from graph_intelligence.agents.strategist.schema import (
    StrategistOutput,
)
from graph_intelligence.graph_state.state import CausalArbitrageState
from shared.logger import get_logger

log = get_logger("agent.strategist")


def _extract_json(text: str) -> str:
    """Strip markdown code fences and return the first balanced JSON object."""
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

    return get_langchain_chat_model(temperature=0.1, max_tokens=1500)


async def _fetch_macro_context() -> str:
    """Gather real-time macro signals for the strategist."""
    context_parts: list[str] = []

    try:
        from data_primitives.extractors.openbb_client import fetch_sector_performance

        sectors = await fetch_sector_performance()
        if sectors:
            sector_str = ", ".join(
                f"{s.get('sector', '?')}: {s.get('change_1d', 0):.2f}%" for s in sectors[:10]
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


async def _compute_regime_block(ticker: str) -> str:
    """Compute and render the live RegimeState block for the prompt.

    Falls back to an empty annotation if OHLCV can't be fetched; the
    LLM then skips regime-based blocking.
    """
    try:
        from data_primitives.extractors.openbb_client import fetch_ohlcv
        from shared.quant.regime import classify_regime
        from shared.quant.signals import compute_hurst_exponent

        bars = await fetch_ohlcv(ticker, days=90)
        if not bars or len(bars) < 30:
            return "(RegimeState unavailable — insufficient OHLCV)"

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        volumes = np.array([b.volume for b in bars], dtype=float)

        log_rets = np.diff(np.log(closes))
        hurst = compute_hurst_exponent(log_rets)
        iv_proxy = (
            float(np.std(log_rets[-20:], ddof=1) * np.sqrt(252))
            + abs(float(closes[-1] / closes[-6] - 1)) * 2.0
            + 0.02
        )

        rs = classify_regime(
            closes,
            highs,
            lows,
            volumes,
            hurst=hurst,
            implied_vol=iv_proxy,
        )
        ret_20d = float(closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0

        return (
            f"volatility_regime: {rs.volatility_regime.value}\n"
            f"trend_regime: {rs.trend_regime.value}\n"
            f"composite_regime: {rs.composite_regime.value}\n"
            f"adx: {rs.adx_value:.1f}\n"
            f"hurst: {hurst:.3f}\n"
            f"realized_vol: {rs.realized_vol_annualized:.1%}\n"
            f"vol_percentile: {rs.vol_percentile:.0f}\n"
            f"rv_short (20d): {rs.rv_short_annualized:.1%}\n"
            f"rv_long  (60d): {rs.rv_long_annualized:.1%}\n"
            f"vol_compression: {rs.vol_compression:.2f}\n"
            f"vrp: {rs.vrp:+.3f}\n"
            f"grind_up_flag: {rs.grind_up_flag}\n"
            f"ret_20d: {ret_20d:+.2%}"
        )
    except Exception as exc:
        log.debug(f"regime_block fetch failed for {ticker}: {exc}")
        return "(RegimeState unavailable)"


async def strategist_node(state: CausalArbitrageState) -> dict:
    """LangGraph node: macro context + regime-driven strategy gating."""
    shock = state.get("shock_ticker", "") or ""
    log.info(f"Strategist analysing macro context for [bold]{shock}[/bold]")

    macro_context = await _fetch_macro_context()
    regime_block = await _compute_regime_block(shock)
    timeline_str = json.dumps(state.get("timeline", [])[:15], indent=2, default=str)

    prompt = STRATEGIST_ANALYZE.format(
        shock_ticker=shock,
        timeline=timeline_str,
        macro_context=macro_context,
        regime_block=regime_block,
    )

    llm = _get_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(content=STRATEGIST_SYSTEM),
            HumanMessage(content=prompt),
        ]
    )

    content = response.content
    parsed: StrategistOutput
    try:
        raw = json.loads(_extract_json(content))
        parsed = StrategistOutput.model_validate(raw)
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        log.warning(f"Strategist output parse failed: {exc}; falling back to defaults")
        parsed = StrategistOutput()

    if parsed.forbidden_strategies:
        log.info(
            f"Strategist FORBIDS {len(parsed.forbidden_strategies)} strategies: "
            f"{parsed.forbidden_strategies}"
        )
    if parsed.preferred_strategies:
        log.info(
            f"Strategist PREFERS {len(parsed.preferred_strategies)} strategies: "
            f"{parsed.preferred_strategies}"
        )

    macro_summary = (
        f"Regime: {parsed.macro_regime} | "
        f"Risk multiplier: {parsed.risk_multiplier:.1f}x | "
        f"Confidence: {parsed.regime_confidence:.2f} | "
        f"Context: {parsed.shock_context[:180]}"
    )
    log.info(
        f"Strategist: {parsed.macro_regime} regime, "
        f"risk_multiplier={parsed.risk_multiplier:.1f}x, "
        f"forbidden={len(parsed.forbidden_strategies)}"
    )

    enriched_topology = state.get("graph_topology_json", "")
    enriched_topology = (
        f"[MACRO CONTEXT]\n{macro_summary}\n"
        f"[REGIME BLOCK]\n{regime_block}\n\n"
        f"[TOPOLOGY]\n{enriched_topology}"
    )

    return {
        "messages": [response],
        "graph_topology_json": enriched_topology,
        "forbidden_strategies": parsed.forbidden_strategies,
        "preferred_strategies": parsed.preferred_strategies,
        "risk_multiplier": parsed.risk_multiplier,
        "macro_regime": parsed.macro_regime,
        "gating_rationale": parsed.gating_rationale,
    }
