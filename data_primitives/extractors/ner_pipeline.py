"""Named Entity Recognition pipeline — extracts structured entities from news
and maps them into the Neo4j supply chain graph.

Supports both OpenAI and Anthropic backends (selected via LLM_PROVIDER config).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
from typing import Any

from shared.config import settings
from shared.exceptions import LLMProviderError
from shared.logger import get_logger
from shared.types import CausalEvent, EventType, NewsItem

log = get_logger("data.ner")

_openai_client = None
_anthropic_client = None
_sequence_counter = itertools.count(start=1)
_DEFAULT_LLM_CONCURRENCY = 25


async def _llm_json_call(system: str, user: str) -> str:
    """Dispatch a JSON-mode LLM call to the configured provider."""
    if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
        return await _anthropic_call(system, user)
    return await _openai_call(system, user)


async def _openai_call(system: str, user: str) -> str:
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await _openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or "{}"


async def _anthropic_call(system: str, user: str) -> str:
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic
        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await _anthropic_client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=1500,
        system=system + "\n\nYou MUST respond with valid JSON only. No markdown, no explanation.",
        messages=[{"role": "user", "content": user}],
        temperature=0.0,
    )
    return response.content[0].text if response.content else "{}"


def _next_seq() -> int:
    return next(_sequence_counter)


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON output from LLMs."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _parse_llm_json(raw: str) -> Any:
    """Safely parse JSON from LLM output, handling common formatting issues."""
    try:
        return json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        log.warning("Failed to parse LLM JSON output, returning empty result")
        return None


# ── Entity Extraction ─────────────────────────────────────────

_NER_SYSTEM_PROMPT = """\
You are a financial Named Entity Recognition system. Extract all relevant entities \
from the provided text. Focus on entities that matter for supply chain and market analysis.
Always respond with valid JSON matching the requested schema exactly."""

_NER_USER_TEMPLATE = """\
Extract all named entities from this financial news text:

"{text}"

Return a JSON object:
{{
  "entities": [
    {{
      "entity": "entity name",
      "type": "COMPANY|PRODUCT|FACILITY|PERSON|LOCATION",
      "ticker": "stock ticker if company, null otherwise",
      "confidence": float between 0.0 and 1.0
    }}
  ]
}}

Rules:
- For companies, always try to resolve the stock ticker (e.g., Apple -> AAPL)
- Include subsidiaries as separate COMPANY entities
- Facilities include factories, warehouses, ports, data centers
- Products include specific product lines, components, raw materials
- Be precise with confidence scores: 0.9+ for explicit mentions, 0.5-0.8 for inferred"""


async def extract_entities(text: str) -> list[dict[str, Any]]:
    """Extract structured entities from text using the configured LLM provider."""
    try:
        raw = await _llm_json_call(
            _NER_SYSTEM_PROMPT,
            _NER_USER_TEMPLATE.format(text=text),
        )
        parsed = _parse_llm_json(raw)
        if parsed is None:
            return []
        entities = parsed.get("entities", [])
        log.debug(f"Extracted {len(entities)} entities from text")
        return entities
    except Exception as exc:
        raise LLMProviderError(f"Entity extraction failed: {exc}") from exc


# ── Event Classification ──────────────────────────────────────

_CLASSIFY_SYSTEM_PROMPT = """\
You are a financial event classifier for a supply chain intelligence system.
Classify the event type and assess confidence.
Always respond with valid JSON matching the requested schema exactly."""

_CLASSIFY_USER_TEMPLATE = """\
Classify this financial news event:

Title: {title}
Text: {text}

Return a JSON object:
{{
  "event_type": one of ["SHOCK", "RECOVERY", "EARNINGS", "MACRO", "GEOPOLITICAL", "REGULATORY", "COMMODITY_SHOCK", "FX_SHOCK"],
  "confidence": float between 0.0 and 1.0,
  "rationale": "brief explanation of classification"
}}

Classification guide:
- SHOCK: Supply disruptions, factory fires, shortages, strikes, recalls, natural disasters
- RECOVERY: Reopenings, deal resolutions, capacity expansions, growth signals
- EARNINGS: Quarterly/annual results, EPS, revenue reports, guidance changes
- MACRO: Central bank actions, interest rates, inflation, GDP, employment data
- GEOPOLITICAL: Sanctions, tariffs, trade wars, political instability, military conflicts
- REGULATORY: New regulations, compliance changes, antitrust actions, policy shifts
- COMMODITY_SHOCK: Raw material price spikes, supply squeezes, inventory disruptions
- FX_SHOCK: Currency crises, major exchange rate moves, capital controls"""

_VALID_EVENT_TYPES = {e.value for e in EventType}


async def classify_event_with_llm(title: str, text: str) -> tuple[EventType, float]:
    """Classify event type using the configured LLM provider."""
    combined_text = text or title
    try:
        raw = await _llm_json_call(
            _CLASSIFY_SYSTEM_PROMPT,
            _CLASSIFY_USER_TEMPLATE.format(title=title, text=combined_text[:2000]),
        )
        parsed = _parse_llm_json(raw)
        if parsed is None:
            return EventType.SHOCK, 0.3

        event_type_str = parsed.get("event_type", "SHOCK")
        if event_type_str not in _VALID_EVENT_TYPES:
            event_type_str = "SHOCK"

        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        return EventType(event_type_str), confidence
    except Exception as exc:
        log.warning(f"Event classification failed, falling back to SHOCK: {exc}")
        return EventType.SHOCK, 0.3


# ── Graph Mapping ─────────────────────────────────────────────

async def map_entities_to_graph(entities: list[dict[str, Any]]) -> int:
    """Upsert extracted entities into the Neo4j supply chain graph.

    Companies become Company nodes; products become Product nodes.
    Returns count of nodes upserted.
    """
    from graph_intelligence.tools.neo4j_tools import get_driver, _session_kwargs
    from shared.types import CompanyNode

    companies = [e for e in entities if e.get("type") == "COMPANY" and e.get("ticker")]
    products = [e for e in entities if e.get("type") == "PRODUCT"]

    if not companies and not products:
        return 0

    driver = await get_driver()
    upserted = 0

    async with driver.session(**_session_kwargs()) as session:
        for company in companies:
            node = CompanyNode(
                ticker=company["ticker"],
                name=company.get("entity"),
            )
            await session.run(
                """
                MERGE (c:Company {ticker: $ticker})
                SET c.name = COALESCE($name, c.name),
                    c.last_updated = datetime()
                """,
                ticker=node.ticker,
                name=node.name,
            )
            upserted += 1

        for product in products:
            await session.run(
                """
                MERGE (p:Product {name: $name})
                SET p.category = $category,
                    p.last_updated = datetime()
                """,
                name=product["entity"],
                category=product.get("category", "unknown"),
            )
            upserted += 1

    log.info(f"Upserted {upserted} entities to Neo4j ({len(companies)} companies, {len(products)} products)")
    return upserted


# ── Full News Batch Pipeline ──────────────────────────────────

async def _process_single_item(
    item: NewsItem,
    semaphore: asyncio.Semaphore,
) -> list[CausalEvent]:
    """Process a single news item: extract entities, classify, map to graph."""
    async with semaphore:
        text = f"{item.title}. {item.text or ''}"

        entities, (event_type, confidence) = await asyncio.gather(
            extract_entities(text),
            classify_event_with_llm(item.title, item.text or ""),
        )

        upserted = await map_entities_to_graph(entities)

        if upserted > 0:
            try:
                from backend.services.orchestrator import update_graph_sequence
                update_graph_sequence(_next_seq())
            except Exception as exc:
                log.debug(f"Graph sequence update skipped: {exc}")

        symbols = item.symbols or [
            e["ticker"] for e in entities
            if e.get("type") == "COMPANY" and e.get("ticker")
        ]
        if not symbols:
            symbols = ["UNKNOWN"]

        events: list[CausalEvent] = []
        for symbol in symbols:
            events.append(CausalEvent(
                timestamp=item.timestamp,
                source_node=symbol,
                event_type=event_type,
                compressed_summary=item.title,
                confidence=confidence,
                sequence_id=_next_seq(),
            ))
        return events


async def process_news_batch(
    news_items: list[NewsItem],
    concurrency: int | None = None,
) -> list[CausalEvent]:
    """Full NER pipeline: extract entities, classify events, map to graph,
    and produce CausalEvent objects with monotonic sequence IDs.

    Args:
        news_items: News items to process.
        concurrency: Max concurrent LLM calls. Defaults to _DEFAULT_LLM_CONCURRENCY (10).
            For large batches (200+), consider raising to 15-20.

    Uses a semaphore to respect LLM API rate limits.
    """
    if not news_items:
        return []

    effective_concurrency = concurrency or _DEFAULT_LLM_CONCURRENCY
    semaphore = asyncio.Semaphore(effective_concurrency)

    log.info(
        f"NER processing {len(news_items)} items "
        f"(concurrency={effective_concurrency})"
    )

    tasks = [_process_single_item(item, semaphore) for item in news_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_events: list[CausalEvent] = []
    failed = 0
    for result in results:
        if isinstance(result, Exception):
            log.warning(f"News item processing failed: {result}")
            failed += 1
            continue
        all_events.extend(result)

    all_events.sort(key=lambda e: (e.timestamp, e.sequence_id or 0))
    log.info(
        f"NER pipeline processed {len(news_items)} items → "
        f"{len(all_events)} events ({failed} failures)"
    )
    return all_events
