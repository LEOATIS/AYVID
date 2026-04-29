"""Chronological Semantic Compression — the architecture doc's solution to
the "Lost in the Middle" problem and the RAG chronological destruction issue.

Traditional RAG retrieves chunks by semantic proximity, destroying the timeline.
Stuffing everything into a long context causes the model to ignore the middle.

This module implements a rolling compression pipeline:
1. Time-window daily news into discrete 24-hour batches
2. Distill each batch into a dense chronological summary (hard facts only)
3. Structure summaries as a rigid chronological timeline
4. Feed the compressed timeline into LangGraph state for causal reasoning

The compression ratio is typically 20:1 — a 90-day history of millions of
tokens compresses into ~5K tokens while preserving every causal link.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections import defaultdict
from typing import Any

from shared.logger import get_logger
from shared.types import CausalEvent, NewsItem

log = get_logger("compression.chrono")


async def _llm_json_call(system: str, user: str) -> str:
    """Dispatch JSON-mode LLM call via the shared llm_client layer.

    Local wrapper retained only to preserve the `"facts": []` fallback
    string (callers expect an array-shaped JSON, not `{}`).
    """
    from shared.llm_client import json_call

    try:
        return await json_call(system, user, max_tokens=1000)
    except Exception as exc:
        log.warning(f"LLM json_call failed: {exc}")
        return '{"facts": []}'


_COMPRESSION_SYSTEM = """\
You are a financial intelligence compressor for a supply chain hedge fund.

Your task: compress a batch of financial news from a single day into a dense
chronological summary that preserves ONLY hard facts needed for causal reasoning.

Rules:
- Extract ONLY: dates, company names, tickers, quantities, percentages, facility names, product names
- Preserve chronological order within the day
- Strip ALL journalistic narrative, opinion, speculation, filler
- Keep sentiment shifts if backed by data (e.g., "revenue down 15%", not "analysts worried")
- Output must be a JSON array of fact objects
- Each fact: {"time": "HH:MM or 'morning'/'afternoon'", "entity": "TICKER or name", "fact": "compressed statement", "type": "SHOCK|RECOVERY|EARNINGS|MACRO|REGULATORY"}
- Maximum 15 facts per day — only the most causally significant"""

_COMPRESSION_USER = """\
Compress these {count} news items from {date} into a chronological fact summary:

{news_block}

Return JSON array of facts. Maximum 15 facts. Only hard data, no narrative."""


async def compress_daily_batch(
    date: dt.date,
    news_items: list[NewsItem],
) -> list[dict[str, Any]]:
    """Compress a single day's news into a dense fact timeline."""
    if not news_items:
        return []

    from data_primitives.security.text_sanitizer import sanitize_news_text

    news_block = "\n---\n".join(
        f"[{item.timestamp.strftime('%H:%M')}] {sanitize_news_text(item.title or '').clean}"
        + (f"\n{sanitize_news_text(item.text[:500]).clean}" if item.text else "")
        for item in news_items[:50]
    )

    try:
        raw = await _llm_json_call(
            _COMPRESSION_SYSTEM,
            _COMPRESSION_USER.format(
                count=len(news_items),
                date=date.isoformat(),
                news_block=news_block[:8000],
            ),
        )
        parsed = json.loads(raw)
        facts = parsed if isinstance(parsed, list) else parsed.get("facts", [])

        for fact in facts:
            fact["date"] = date.isoformat()

        log.debug(f"Compressed {len(news_items)} items from {date} → {len(facts)} facts")
        return facts
    except Exception as exc:
        log.warning(f"Daily compression failed for {date}: {exc}")
        return [
            {
                "date": date.isoformat(),
                "time": "all-day",
                "entity": "UNKNOWN",
                "fact": f"{len(news_items)} news items (compression failed)",
                "type": "MACRO",
            }
        ]


async def build_compressed_timeline(
    events: list[CausalEvent],
    news_items: list[NewsItem] | None = None,
    lookback_days: int = 90,
) -> list[dict[str, Any]]:
    """Build a full compressed chronological timeline from events and news.

    This is the main entry point. It:
    1. Groups events/news by day
    2. Compresses each day in parallel
    3. Returns a strictly chronological timeline of facts
    """
    by_date: dict[dt.date, list[NewsItem]] = defaultdict(list)

    if news_items:
        for item in news_items:
            by_date[item.timestamp.date()].append(item)

    if not by_date and events:
        for event in events:
            synthetic = NewsItem(
                timestamp=event.timestamp,
                title=event.compressed_summary,
                source="internal",
                symbols=[event.source_node],
            )
            by_date[event.timestamp.date()].append(synthetic)

    cutoff = dt.date.today() - dt.timedelta(days=lookback_days)
    filtered_dates = sorted(d for d in by_date if d >= cutoff)

    tasks = [compress_daily_batch(date, by_date[date]) for date in filtered_dates]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    timeline: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            log.warning(f"Daily batch compression failed: {result}")
            continue
        timeline.extend(result)

    timeline.sort(key=lambda f: (f.get("date", ""), f.get("time", "")))

    log.info(
        f"Compressed {sum(len(by_date[d]) for d in filtered_dates)} items "
        f"across {len(filtered_dates)} days → {len(timeline)} facts"
    )
    return timeline


def format_timeline_for_llm(
    timeline: list[dict[str, Any]],
    ticker_filter: str | None = None,
    max_facts: int = 100,
) -> str:
    """Format a compressed timeline into a string for LLM consumption.

    Produces a strict chronological narrative that preserves causal ordering.
    """
    if ticker_filter:
        timeline = [
            f
            for f in timeline
            if ticker_filter.upper() in (f.get("entity", "")).upper()
            or ticker_filter.upper() in str(f.get("fact", "")).upper()
        ]

    timeline = timeline[-max_facts:]

    if not timeline:
        return "No relevant events in the compressed timeline."

    lines: list[str] = []
    current_date = ""
    for fact in timeline:
        date = fact.get("date", "unknown")
        if date != current_date:
            current_date = date
            lines.append(f"\n=== {date} ===")
        time = fact.get("time", "")
        entity = fact.get("entity", "")
        text = fact.get("fact", "")
        event_type = fact.get("type", "")
        lines.append(f"  [{time}] {entity} ({event_type}): {text}")

    return "\n".join(lines)
