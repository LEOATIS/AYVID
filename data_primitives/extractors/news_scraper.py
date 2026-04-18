"""News extraction pipeline — feeds unstructured text into the NER/compression pipeline.

Aggregates news from OpenBB and assigns monotonic sequence IDs for
event-sourcing validation against the Neo4j graph state.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import itertools
from typing import Sequence

from shared.logger import get_logger
from shared.types import CausalEvent, EventType, NewsItem
from data_primitives.extractors.openbb_client import fetch_company_news

log = get_logger("data.news")

_sequence_counter = itertools.count(start=1)


def _next_seq() -> int:
    return next(_sequence_counter)


async def scrape_news_for_tickers(
    tickers: list[str],
    limit_per_ticker: int = 15,
) -> list[NewsItem]:
    """Fetch and merge news across a watchlist, sorted by timestamp."""
    tasks = [fetch_company_news(sym, limit=limit_per_ticker) for sym in tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_news: list[NewsItem] = []
    for ticker, result in zip(tickers, results):
        if isinstance(result, Exception):
            log.warning(f"News scrape failed for {ticker}: {result}")
            continue
        all_news.extend(result)

    all_news.sort(key=lambda n: n.timestamp)
    log.info(f"Scraped {len(all_news)} total news items across {len(tickers)} tickers")
    return all_news


def classify_event_type(title: str, text: str | None) -> EventType:
    """Heuristic event type classification before LLM-based NER."""
    combined = f"{title} {text or ''}".lower()
    shock_keywords = [
        "fire", "explosion", "shortage", "sanctions", "tariff", "strike",
        "recall", "shutdown", "layoff", "breach", "crash", "war", "flood",
        "earthquake", "pandemic", "ban", "embargo",
    ]
    recovery_keywords = [
        "recovery", "reopen", "resume", "resolve", "deal", "agreement",
        "expansion", "growth", "upgrade",
    ]
    earnings_keywords = [
        "earnings", "revenue", "profit", "loss", "guidance", "forecast",
        "quarterly", "annual", "EPS", "beat", "miss",
    ]
    macro_keywords = [
        "fed", "interest rate", "inflation", "GDP", "unemployment", "CPI",
        "PPI", "FOMC", "treasury", "yield", "central bank",
    ]

    if any(kw in combined for kw in shock_keywords):
        return EventType.SHOCK
    if any(kw in combined for kw in recovery_keywords):
        return EventType.RECOVERY
    if any(kw in combined for kw in earnings_keywords):
        return EventType.EARNINGS
    if any(kw in combined for kw in macro_keywords):
        return EventType.MACRO
    return EventType.SHOCK


def news_to_causal_events(
    news_items: Sequence[NewsItem],
) -> list[CausalEvent]:
    """Convert raw news into sequenced CausalEvent objects for the timeline."""
    events: list[CausalEvent] = []
    for item in news_items:
        event_type = classify_event_type(item.title, item.text)
        for symbol in item.symbols:
            events.append(CausalEvent(
                timestamp=item.timestamp,
                source_node=symbol,
                event_type=event_type,
                compressed_summary=item.title,
                confidence=0.5,
                sequence_id=_next_seq(),
            ))
    return events
