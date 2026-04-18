"""Text cleaning and chronological semantic compression pipeline.

Implements the Chronological Semantic Compression strategy from the architecture doc:
time-window daily news into 24h batches, compress via LLM, preserve timeline integrity.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Sequence

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from shared.config import settings
from shared.logger import get_logger
from shared.types import CompressedDaySummary, NewsItem

log = get_logger("data.text")

_COMPRESSION_SYSTEM_PROMPT = """You are a financial intelligence analyst performing chronological semantic compression.
Given a batch of news articles from a single day about a specific company/ticker, produce a DENSE factual summary.

Rules:
- Extract ONLY hard facts: dates, numbers, named entities, sentiment shifts
- Strip all journalistic narrative, opinion, and filler
- Preserve exact chronological order of events within the day
- Output as a JSON object with fields: facts (list of strings), sentiment_shift (float -1 to 1), key_entities (list of strings)
- Be extremely concise — each fact should be one sentence max"""


async def _compress_with_openai(ticker: str, date: dt.date, texts: list[str]) -> dict:
    """Compress a day's news using OpenAI."""
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    joined = "\n---\n".join(texts)
    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _COMPRESSION_SYSTEM_PROMPT},
            {"role": "user", "content": f"Ticker: {ticker}\nDate: {date.isoformat()}\n\nArticles:\n{joined}"},
        ],
        temperature=0.1,
        max_tokens=500,
    )
    import json
    return json.loads(response.choices[0].message.content or "{}")


async def _compress_with_anthropic(ticker: str, date: dt.date, texts: list[str]) -> dict:
    """Compress a day's news using Claude."""
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    joined = "\n---\n".join(texts)
    response = await client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=500,
        system=_COMPRESSION_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Ticker: {ticker}\nDate: {date.isoformat()}\n\nArticles:\n{joined}"},
        ],
    )
    import json
    raw = response.content[0].text if response.content else "{}"
    return json.loads(raw)


async def compress_daily_news(
    ticker: str,
    date: dt.date,
    texts: list[str],
) -> CompressedDaySummary:
    """Compress a single day's news into a dense chronological summary."""
    if not texts:
        return CompressedDaySummary(date=date, ticker=ticker)

    try:
        if settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY:
            data = await _compress_with_anthropic(ticker, date, texts)
        else:
            data = await _compress_with_openai(ticker, date, texts)

        return CompressedDaySummary(
            date=date,
            ticker=ticker,
            facts=data.get("facts", []),
            sentiment_shift=float(data.get("sentiment_shift", 0.0)),
            key_entities=data.get("key_entities", []),
            raw_event_count=len(texts),
        )
    except Exception as exc:
        log.warning(f"LLM compression failed for {ticker}/{date}: {exc}")
        return CompressedDaySummary(
            date=date,
            ticker=ticker,
            facts=[t[:200] for t in texts[:3]],
            raw_event_count=len(texts),
        )


async def build_chronological_timeline(
    news_items: Sequence[NewsItem],
    ticker: str,
) -> list[CompressedDaySummary]:
    """Group news by day, compress each batch, return ordered timeline."""
    daily_buckets: dict[dt.date, list[str]] = defaultdict(list)
    for item in news_items:
        if ticker in item.symbols:
            text = item.text or item.title
            daily_buckets[item.timestamp.date()].append(text)

    timeline: list[CompressedDaySummary] = []
    for date in sorted(daily_buckets.keys()):
        summary = await compress_daily_news(ticker, date, daily_buckets[date])
        timeline.append(summary)

    log.info(f"Built {len(timeline)}-day compressed timeline for {ticker}")
    return timeline


def clean_text(raw: str) -> str:
    """Basic text cleaning for pre-LLM processing."""
    import re
    text = re.sub(r"<[^>]+>", "", raw)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\x20-\x7E\n]", "", text)
    return text.strip()
