"""Vision-LLM PatternAgent node + analyst-swarm opinion adapter.

Two public entry points:

    classify_pattern(ohlcv, ticker, timeframe) -> PatternClassification
        Pure classification call — renders the chart, asks the vision
        model, returns a typed result. Used directly for diagnostic
        CLI tooling or research scripts.

    pattern_analyst_opinion(state, quant_data) -> dict
        Wraps ``classify_pattern`` and converts the result into the
        ``{analyst, direction, confidence, thesis}`` shape the analyst
        swarm expects, so the PatternAgent slots into the existing
        debate/JSD aggregation as a 5th specialist.

Caching
───────
Vision calls are the most expensive LLM calls in the pipeline. We
cache results on a fingerprint of the rendered chart bytes — two
calls for the same ticker within the same session that would produce
identical pixel bytes share the same classification.
"""
from __future__ import annotations

import base64
import time
from typing import Any

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from pydantic import ValidationError

from shared.config import settings
from shared.logger import get_logger
from graph_intelligence.graph_state.state import CausalArbitrageState
from graph_intelligence.agents.pattern.chart_renderer import (
    OHLCVSeries, render_candlestick_chart, chart_fingerprint,
)
from graph_intelligence.agents.pattern.prompts import (
    PATTERN_SYSTEM, PATTERN_USER_TEMPLATE,
)
from graph_intelligence.agents.pattern.schema import (
    PATTERN_BIAS, PatternClass, PatternClassification,
)

log = get_logger("agent.pattern")

# ── Process-local cache: fingerprint → PatternClassification ────────
_CACHE_MAX = 256
_pattern_cache: dict[str, PatternClassification] = {}


def _cache_put(key: str, value: PatternClassification) -> None:
    if len(_pattern_cache) >= _CACHE_MAX:
        # Cheap LRU-ish eviction: drop first inserted item.
        _pattern_cache.pop(next(iter(_pattern_cache)))
    _pattern_cache[key] = value


def _build_image_message(png_bytes: bytes, text: str) -> HumanMessage:
    """Provider-aware vision message.

    Anthropic expects ``{"type":"image","source":{"type":"base64",...}}``;
    OpenAI expects ``{"type":"image_url","image_url":{"url":"data:..."}}``.
    """
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        return HumanMessage(content=[
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            },
            {"type": "text", "text": text},
        ])
    return HumanMessage(content=[
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        },
    ])


def _get_vision_llm():
    """Vision-capable LLM bound to the ``PatternClassification`` schema."""
    if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=0.05,
            max_tokens=900,
        ).with_structured_output(PatternClassification)
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.05,
        max_tokens=900,
    ).with_structured_output(PatternClassification)


# ── Public API ────────────────────────────────────────────────────────
async def classify_pattern(
    ohlcv: OHLCVSeries,
    ticker: str,
    timeframe: str = "D1",
    lookback: int = 50,
) -> PatternClassification:
    """Classify one instrument against the 16-pattern library.

    Safe to call in any async context. Always returns a valid
    ``PatternClassification`` — failures degrade to
    ``PatternClass.NONE`` with confidence 0, never raise.
    """
    try:
        png = render_candlestick_chart(ohlcv, lookback=lookback,
                                       title=f"{ticker} • {timeframe}")
    except Exception as exc:
        log.warning(f"[Pattern] render failed for {ticker}: {exc}")
        return PatternClassification()

    fp = chart_fingerprint(png)
    cache_key = f"{ticker}:{timeframe}:{fp}"
    if cache_key in _pattern_cache:
        return _pattern_cache[cache_key]

    try:
        llm = _get_vision_llm()
    except Exception as exc:
        log.warning(f"[Pattern] vision LLM init failed ({exc}); NONE")
        return PatternClassification()

    n = len(ohlcv.closes)
    start = max(0, n - lookback)
    price_low = float(np.min(ohlcv.lows[start:]))
    price_high = float(np.max(ohlcv.highs[start:]))

    text = PATTERN_USER_TEMPLATE.format(
        ticker=ticker, timeframe=timeframe,
        price_low=price_low, price_high=price_high,
    )
    messages = [SystemMessage(content=PATTERN_SYSTEM), _build_image_message(png, text)]

    t0 = time.perf_counter()
    try:
        result: PatternClassification = await llm.ainvoke(messages)
    except (ValidationError, ValueError, TypeError) as exc:
        log.warning(f"[Pattern] validation failed for {ticker}: {exc}")
        return PatternClassification()
    except Exception as exc:
        log.warning(f"[Pattern] LLM error for {ticker}: {exc}")
        return PatternClassification()
    latency_ms = (time.perf_counter() - t0) * 1000

    if not isinstance(result, PatternClassification):
        return PatternClassification()

    # Consistency check: if the LLM labels a directional pattern but
    # emits NEUTRAL/opposite direction, normalise back to the canonical
    # bias. This prevents hallucinated contradictions from polluting
    # the analyst swarm.
    canonical = PATTERN_BIAS.get(result.pattern, "NEUTRAL")
    if canonical != "NEUTRAL" and result.implied_direction != canonical:
        log.debug(
            f"[Pattern] {ticker}: LLM returned {result.pattern.value} with "
            f"direction={result.implied_direction}; snapping to canonical "
            f"bias {canonical}"
        )
        result = result.model_copy(update={"implied_direction": canonical})

    log.info(
        f"[Pattern] {ticker} ({timeframe}) → {result.pattern.value} "
        f"dir={result.implied_direction} conf={result.confidence:.2f} "
        f"latency={latency_ms:.0f}ms"
    )
    _cache_put(cache_key, result)
    return result


async def pattern_analyst_opinion(
    state: CausalArbitrageState,
    quant_data: str = "",
    lookback: int = 50,
) -> dict[str, Any]:
    """Adapt a PatternAgent classification into an analyst-swarm opinion.

    Opinion shape (matches the 4 existing specialists):
        {
          "analyst": "Pattern",
          "direction": "LONG" | "SHORT" | "HOLD",
          "confidence": float,
          "thesis":    <rationale>,
          "full_analysis": {pattern, confidence, invalidation_level},
          "latency_ms": ...
        }

    When OHLCV cannot be fetched for the target ticker, returns a HOLD
    opinion with confidence 0 — that keeps the swarm at the full 5-
    specialist size but contributes no signal.
    """
    ticker = state.get("target_ticker") or state.get("shock_ticker") or ""
    if not ticker:
        return {
            "analyst": "Pattern", "direction": "HOLD", "confidence": 0.0,
            "thesis": "no target ticker", "full_analysis": {}, "latency_ms": 0.0,
        }

    ohlcv = await _fetch_ohlcv_for_pattern(ticker)
    if ohlcv is None:
        return {
            "analyst": "Pattern", "direction": "HOLD", "confidence": 0.0,
            "thesis": "OHLCV unavailable", "full_analysis": {}, "latency_ms": 0.0,
        }

    t0 = time.perf_counter()
    classification = await classify_pattern(ohlcv, ticker, lookback=lookback)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Map implied_direction → swarm direction. A PatternClass of NONE
    # with confidence > 0 becomes HOLD; directional patterns become
    # LONG / SHORT per their implied bias.
    if classification.pattern == PatternClass.NONE:
        direction = "HOLD"
    elif classification.implied_direction == "NEUTRAL":
        direction = "HOLD"
    else:
        direction = classification.implied_direction

    thesis = classification.rationale or (
        f"{classification.pattern.value} pattern detected "
        f"(invalidation @ {classification.invalidation_level:.2f})"
    )

    return {
        "analyst": "Pattern",
        "direction": direction,
        "confidence": float(classification.confidence),
        "thesis": thesis[:240],
        "full_analysis": {
            "pattern": classification.pattern.value,
            "implied_direction": classification.implied_direction,
            "invalidation_level": classification.invalidation_level,
        },
        "latency_ms": round(elapsed_ms, 1),
    }


async def _fetch_ohlcv_for_pattern(
    ticker: str, days: int = 90,
) -> OHLCVSeries | None:
    """Fetch OHLCV for the PatternAgent, gracefully degrading on failure."""
    try:
        from data_primitives.extractors.openbb_client import fetch_ohlcv
        bars = await fetch_ohlcv(ticker, days=days)
        if not bars or len(bars) < 30:
            return None
        return OHLCVSeries(
            dates=[b.timestamp for b in bars],
            opens=np.array([b.open for b in bars], dtype=float),
            highs=np.array([b.high for b in bars], dtype=float),
            lows=np.array([b.low for b in bars], dtype=float),
            closes=np.array([b.close for b in bars], dtype=float),
            volumes=np.array([b.volume for b in bars], dtype=float),
        )
    except Exception as exc:
        log.debug(f"[Pattern] OHLCV fetch failed for {ticker}: {exc}")
        return None
