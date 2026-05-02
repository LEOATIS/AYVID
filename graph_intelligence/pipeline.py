"""LangGraph DAG assembly — the complete causal arbitrage reasoning pipeline.

Wires: prepare_topology → compress_timeline → strategist → mapper →
       analyst_swarm → auditor → [execute|retry|end]

The chronological compression node distills raw news into a time-ordered
fact stream before any LLM reasoning begins, solving the "Lost in the
Middle" problem from standard RAG.

The analyst swarm deploys 4 specialist agents in parallel (fundamentals,
technicals, macro, supply chain) and aggregates their opinions via a
Bayesian debate protocol before the auditor makes the final call.

Each node is wrapped with latency instrumentation so bottlenecks are
visible via `ayvid system latency`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from graph_intelligence.agents.auditor.node import auditor_node
from graph_intelligence.agents.mapper.node import mapper_node
from graph_intelligence.agents.researchers import researcher_debate_node
from graph_intelligence.agents.strategist.node import strategist_node
from graph_intelligence.agents.supervisor.consensus import (
    emit_trade_signal,
    should_continue,
)
from graph_intelligence.agents.supervisor.node import supervisor_node
from graph_intelligence.graph_state.state import (
    CausalArbitrageState,
    create_initial_state,
)
from graph_intelligence.tools.neo4j_tools import find_hidden_victims
from shared.logger import get_logger
from shared.types import CausalEvent, TradeSignal

log = get_logger("pipeline")

# ── Per-node latency tracking ────────────────────────────────────────
_node_latencies: dict[str, float] = {}
_pipeline_total_ms: float = 0.0


def _timed_node(name: str, fn: Callable) -> Callable:
    """Wrap a pipeline node function with latency measurement."""

    async def _wrapper(state: CausalArbitrageState) -> Any:
        t0 = time.perf_counter()
        result = await fn(state)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _node_latencies[name] = round(elapsed_ms, 1)
        log.debug(f"Node [{name}] completed in {elapsed_ms:.0f}ms")
        return result

    _wrapper.__name__ = fn.__name__
    return _wrapper


def get_pipeline_latency() -> dict:
    """Return the per-node latency breakdown from the last pipeline run.

    Used by `ayvid system latency` CLI command.
    """
    total = sum(_node_latencies.values())
    return {
        "nodes": dict(_node_latencies),
        "total_ms": round(total, 1),
        "total_s": round(total / 1000, 1),
    }


async def _prepare_topology_node(state: CausalArbitrageState) -> dict:
    """Pre-processing: query Neo4j for causal paths AND Qdrant for similar
    historical events before LLM reasoning begins.
    """
    import asyncio

    shock = state["shock_ticker"]
    topology_json = ""
    causal_dicts: list[dict] = []

    neo4j_task = _query_neo4j_topology(shock)
    qdrant_task = _retrieve_similar_events(shock)
    neo4j_result, qdrant_result = await asyncio.gather(
        neo4j_task,
        qdrant_task,
        return_exceptions=True,
    )

    if isinstance(neo4j_result, Exception):
        log.warning(f"Topology query failed: {neo4j_result}")
        topology_json = f"Neo4j query failed: {neo4j_result}"
    else:
        causal_dicts, topology_json = neo4j_result

    rag_context = ""
    if isinstance(qdrant_result, Exception):
        log.debug(f"Qdrant RAG retrieval failed: {qdrant_result}")
    elif qdrant_result:
        rag_context = _format_rag_results(qdrant_result)
        log.info(f"RAG retrieved {len(qdrant_result)} similar historical events")

    combined_topology = topology_json
    if rag_context:
        combined_topology += f"\n\n=== Similar Historical Events (RAG) ===\n{rag_context}"

    return {
        "causal_paths": causal_dicts,
        "graph_topology_json": combined_topology,
        "iteration": state["iteration"] + 1,
    }


async def _query_neo4j_topology(shock: str) -> tuple[list[dict], str]:
    paths = await find_hidden_victims(shock)
    causal_dicts = [p.model_dump(mode="json") for p in paths]
    return causal_dicts, json.dumps(causal_dicts[:5], indent=2)


async def _retrieve_similar_events(shock: str) -> list[dict]:
    from graph_intelligence.tools.qdrant_tools import search_similar_events

    query = f"{shock} supply chain disruption impact"
    return await search_similar_events(query, limit=8, ticker_filter=None)


def _format_rag_results(events: list[dict]) -> str:
    lines: list[str] = []
    for e in events:
        score = e.get("score", 0)
        ts = e.get("timestamp", "?")
        node = e.get("source_node", "?")
        etype = e.get("event_type", "?")
        summary = e.get("summary", "?")
        lines.append(f"  [{ts}] {node} ({etype}, sim={score:.2f}): {summary}")
    return "\n".join(lines)


async def _compress_timeline_node(state: CausalArbitrageState) -> dict:
    """Chronological Semantic Compression — distills raw events into a dense
    time-ordered fact stream. Replaces raw RAG retrieval with structured
    chronological distillation to prevent the 'Lost in the Middle' effect.
    """
    from graph_intelligence.compression.chronological import (
        build_compressed_timeline,
        format_timeline_for_llm,
    )

    raw_timeline = state.get("timeline", [])
    events = [CausalEvent(**e) if isinstance(e, dict) else e for e in raw_timeline]

    compressed = await build_compressed_timeline(events)

    formatted = format_timeline_for_llm(
        compressed,
        ticker_filter=state.get("shock_ticker"),
        max_facts=80,
    )

    compressed_dicts = compressed[:100]

    return {
        "timeline": compressed_dicts,
        "quant_data": state.get("quant_data", "")
        + f"\n\n=== Compressed Timeline ===\n{formatted}",
    }


async def _enrich_quant_data_node(state: CausalArbitrageState) -> dict:
    """Compute and inject real quantitative metrics into state before analysts run.

    Populates: Hurst exponent, O-U parameters, RSI, ATR, regime classification,
    volume profile, z-score, trend strength — the data analysts reference in prompts.
    """
    target = state.get("target_ticker") or state.get("shock_ticker", "")
    existing_quant = state.get("quant_data", "")

    quant_block = await _compute_quant_block(target)
    if not quant_block:
        quant_block = "(No OHLCV data available for quantitative analysis)"

    return {"quant_data": f"{existing_quant}\n\n=== Quantitative Signals ===\n{quant_block}"}


async def _compute_quant_block(ticker: str) -> str:
    """Build the quant data string from live/cached market data."""
    try:
        import numpy as np

        from data_primitives.extractors.openbb_client import fetch_ohlcv
        from data_primitives.loaders.hopsworks_sync import get_cached_features
        from shared.quant.regime import classify_regime
        from shared.quant.signals import score_signal

        bars = await fetch_ohlcv(ticker, days=90)
        if len(bars) < 30:
            return ""

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        volumes = np.array([b.volume for b in bars], dtype=float)

        signal = score_signal(ticker, closes, highs, lows, volumes)
        regime = classify_regime(
            closes, highs, lows, volumes, hurst=signal.hurst_exponent if signal else 0.5
        )

        features = get_cached_features(ticker)
        hurst = (
            features.get("hurst_exponent", signal.hurst_exponent if signal else 0.5)
            if features
            else (signal.hurst_exponent if signal else 0.5)
        )

        lines = [
            f"Ticker: {ticker}",
            f"Price: ${closes[-1]:.2f}",
            f"Hurst Exponent: {hurst:.3f} ({'trending' if hurst > 0.55 else 'mean-reverting' if hurst < 0.45 else 'random walk'})",
        ]

        if signal:
            lines.extend(
                [
                    f"O-U Theta (reversion speed): {signal.ou_theta:.3f}",
                    f"O-U Mu (equilibrium): ${np.exp(signal.ou_mu):.2f}",
                    f"Z-Score: {signal.z_score:+.2f}",
                    f"RSI(14): {signal.rsi:.1f}",
                    f"ATR(14): ${signal.atr:.2f} ({signal.atr_pct:.1f}% of price)",
                    f"Volume Ratio: {signal.volume_ratio:.2f}x average",
                    f"Trend Strength: {signal.trend_strength:+.2f}",
                    f"Composite Score: {signal.composite_score:.3f}",
                    f"Conviction: {signal.conviction:.2f}",
                    f"Signal Direction: {signal.direction}",
                    f"Dynamic SL: ${signal.dynamic_stop_loss:.2f} | TP: ${signal.dynamic_take_profit:.2f}",
                ]
            )

        lines.extend(
            [
                "\nRegime Classification:",
                f"  Volatility: {regime.volatility_regime.value} (RV={regime.realized_vol_annualized:.1%}, pctile={regime.vol_percentile:.0f})",
                f"  Trend: {regime.trend_regime.value} (ADX={regime.adx_value:.1f})",
                f"  Composite: {regime.composite_regime.value}",
                f"  Preferred Strategy: {regime.preferred_strategy}",
                f"  Risk Scaling: {regime.risk_scaling_factor:.1f}x",
                f"  Tradeable: {'YES' if regime.tradeable else 'NO'}",
            ]
        )

        if features:
            for key in (
                "rolling_volatility_5d",
                "rolling_volatility_10d",
                "rolling_volatility_30d",
                "rolling_volatility_60d",
            ):
                val = features.get(key)
                if val:
                    lines.append(f"  {key}: {val:.1%}")

        return "\n".join(lines)

    except Exception as exc:
        log.debug(f"Quant data computation failed for {ticker}: {exc}")
        return ""


async def _analyst_swarm_node(state: CausalArbitrageState) -> dict:
    """Deploy the 4-specialist analyst swarm and run the debate protocol.

    Each analyst independently evaluates the mapper's proposed trade.
    The debate protocol aggregates opinions via Bayesian conviction.
    Only supermajority consensus allows the trade to proceed.
    """
    from graph_intelligence.agents.analyst.node import run_analyst_swarm
    from graph_intelligence.debate import run_debate

    quant_data = state.get("quant_data", "")
    opinions = await run_analyst_swarm(state, quant_data=quant_data)

    debate = run_debate(opinions)

    updated: dict = {
        "analyst_opinions": opinions,
        "debate_result": {
            "consensus_direction": debate.consensus_direction,
            "conviction": debate.bayesian_conviction,
            "votes": {
                "long": debate.votes_long,
                "short": debate.votes_short,
                "hold": debate.votes_hold,
            },
            "has_consensus": debate.has_consensus,
            "supermajority": debate.supermajority,
            "should_trade": debate.should_trade,
            "summary": debate.debate_summary,
        },
    }

    if debate.should_trade:
        updated["proposed_action"] = debate.consensus_direction
        updated["confidence"] = debate.bayesian_conviction
        log.info(
            f"Debate passed: {debate.consensus_direction} @ "
            f"{debate.bayesian_conviction:.2f} conviction"
        )
    else:
        updated["confidence"] = debate.bayesian_conviction * 0.5
        log.info(f"Debate failed: no consensus ({debate.debate_summary})")

    return updated


def build_causal_graph() -> StateGraph:
    """Construct the LangGraph DAG for N-th Order Causal Arbitrage.

    Flow (Phase-3 unified):
        prepare_topology → compress_timeline → strategist → mapper →
        researcher_debate → enrich_quant_data → analyst_swarm →
        auditor → supervisor → [execute|retry|end]

    The ``researcher_debate`` node runs the 2-round bull-vs-bear debate
    and injects a ``ResearcherConsensus`` into state BEFORE the analyst
    swarm fires, so the analysts evaluate a thesis that has already
    been adversarially stress-tested.

    The ``supervisor`` node (wired in Phase-3, Gap A2) runs AFTER the
    auditor has approved a proposed action. It:
      * Reads calibration priors for the (strategy × regime) cell.
      * Hard-blocks empirically unprofitable cells.
      * Emits `strategy_parameters` consumed by the downstream
        orchestrator's `get_param(...)` dynamic-threshold lookup.
      * Optionally refines `risk_multiplier`.
    Without this node, LangGraph's `strategy_parameters` were always
    empty and `get_param` always resolved to hard-coded defaults.

    Every node is wrapped with latency instrumentation.
    """
    graph = StateGraph(CausalArbitrageState)

    graph.add_node("prepare_topology", _timed_node("prepare_topology", _prepare_topology_node))
    graph.add_node("compress_timeline", _timed_node("compress_timeline", _compress_timeline_node))
    graph.add_node("strategist", _timed_node("strategist", strategist_node))
    graph.add_node("mapper", _timed_node("mapper", mapper_node))
    graph.add_node("researcher_debate", _timed_node("researcher_debate", researcher_debate_node))
    graph.add_node("enrich_quant_data", _timed_node("enrich_quant_data", _enrich_quant_data_node))
    graph.add_node("analyst_swarm", _timed_node("analyst_swarm", _analyst_swarm_node))
    graph.add_node("auditor", _timed_node("auditor", auditor_node))
    graph.add_node("supervisor", _timed_node("supervisor", supervisor_node))

    graph.set_entry_point("prepare_topology")
    graph.add_edge("prepare_topology", "compress_timeline")
    graph.add_edge("compress_timeline", "strategist")
    graph.add_edge("strategist", "mapper")
    graph.add_edge("mapper", "researcher_debate")
    graph.add_edge("researcher_debate", "enrich_quant_data")
    graph.add_edge("enrich_quant_data", "analyst_swarm")
    graph.add_edge("analyst_swarm", "auditor")
    graph.add_edge("auditor", "supervisor")

    graph.add_conditional_edges(
        "supervisor",
        should_continue,
        {
            "execute": END,
            "retry": "mapper",
            "end": END,
        },
    )

    return graph


_compiled_graph = None


def get_compiled_graph():
    """Return the compiled LangGraph (singleton)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_causal_graph().compile()
    return _compiled_graph


async def run_causal_analysis(
    shock_ticker: str,
    timeline: list[CausalEvent] | None = None,
    sequence_id: int = 0,
    backtest_mode: bool = False,
    analysis_date: str | None = None,
) -> TradeSignal | None:
    """Execute the full causal arbitrage pipeline for a given shock.

    When backtest_mode=True, applies Divergence Decoding to strip
    lookahead bias from LLM reasoning before emitting the signal.
    """
    global _pipeline_total_ms
    log.info(f"Starting causal analysis for shock: [bold]{shock_ticker}[/bold]")

    initial_state = create_initial_state(
        shock_ticker=shock_ticker,
        timeline=timeline,
        sequence_id=sequence_id,
    )

    _node_latencies.clear()
    t0 = time.perf_counter()
    graph = get_compiled_graph()
    final_state = await graph.ainvoke(initial_state)
    _pipeline_total_ms = (time.perf_counter() - t0) * 1000

    signal = emit_trade_signal(final_state)

    if signal and backtest_mode:
        try:
            from graph_intelligence.compression.chronological import format_timeline_for_llm
            from graph_intelligence.compression.divergence_decoding import divergence_decode

            compressed_text = format_timeline_for_llm(
                final_state.get("timeline", []),
                ticker_filter=shock_ticker,
            )
            topology_text = final_state.get("graph_topology_json", "")

            dd_result = await divergence_decode(
                ticker=signal.ticker,
                analysis_date=analysis_date or signal.timestamp.strftime("%Y-%m-%d"),
                compressed_timeline=compressed_text,
                causal_paths=topology_text,
            )

            kl_div = dd_result.get("kl_divergence", 0.0)
            # Always record the KL on the intent for downstream inspection.
            signal.alpha_score.kl_divergence_to_naive = float(kl_div)
            if dd_result.get("bias_detected"):
                log.warning(
                    f"Lookahead bias detected for {signal.ticker} "
                    f"(KL={kl_div:.3f}) — halving conviction"
                )
                signal.alpha_score.unified_conviction *= 0.5
                signal.rationale += f" [DD: bias detected, KL={kl_div:.3f}]"

        except Exception as exc:
            log.debug(f"Divergence decoding skipped: {exc}")

    if signal:
        log.info(
            f"Pipeline complete: [bold]{signal.direction.value} {signal.ticker}[/bold] "
            f"conv={signal.alpha_score.unified_conviction:.2f}"
        )
    else:
        log.info("Pipeline complete: no actionable signal")

    return signal
