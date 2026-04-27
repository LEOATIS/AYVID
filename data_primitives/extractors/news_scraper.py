"""News extraction pipeline — fetches raw headlines and routes them into
the canonical NER/compression pipeline.

Public surface:

* `scrape_news_for_tickers(tickers)` — fetch + merge OpenBB headlines.
* `news_to_causal_events(news_items)` — asynchronously convert raw
  `NewsItem`s into `CausalEvent`s. Prefers the LLM-based NER pipeline
  (`ner_pipeline.process_news_batch`) and falls back to the keyword
  heuristic only when the LLM path is unreachable (no API key, rate
  limited, provider outage).

The keyword-heuristic classifier is retained as a **fallback only** —
it gives a fixed 0.5 confidence which is deliberately below the
production `BACKTEST_MIN_CONVICTION` floor, so fallback events cannot
cross the live trade gate on their own.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Sequence

from data_primitives.extractors.openbb_client import fetch_company_news
from shared.logger import get_logger
from shared.types import CausalEvent, EventType, NewsItem

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


# ── Smart dispatch: LLM-first, heuristic fallback ─────────────


async def news_to_causal_events(
    news_items: Sequence[NewsItem],
    *,
    force_heuristic: bool = False,
) -> list[CausalEvent]:
    """Convert raw news into `CausalEvent`s using the canonical LLM pipeline.

    Behaviour:
        1. If ``force_heuristic`` is True, or no LLM API key is configured,
           route through the keyword-heuristic fallback (conf=0.5, below
           the live-trade floor).
        2. Otherwise delegate to `ner_pipeline.process_news_batch` which
           performs LLM NER + event classification + per-event confidence.
        3. If the LLM call raises, catch, log, and fall back to the
           heuristic path so the pipeline never crashes on a provider outage.
    """
    items = list(news_items)
    if not items:
        return []

    if force_heuristic:
        return _heuristic_news_to_causal_events(items)

    from shared.config import settings

    if not (settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY):
        log.debug("No LLM API key — using heuristic event classification")
        return _heuristic_news_to_causal_events(items)

    try:
        from data_primitives.extractors.ner_pipeline import process_news_batch

        return await process_news_batch(items)
    except Exception as exc:
        log.warning(
            f"LLM NER failed ({exc}); falling back to heuristic classifier. "
            f"Downstream gates will see conf=0.5 events."
        )
        return _heuristic_news_to_causal_events(items)


# ── LEGACY / FALLBACK: keyword heuristic ──────────────────────
#
# Retained as a last-resort path when the LLM is unavailable. Fixed
# confidence 0.5 is below `settings.BACKTEST_MIN_CONVICTION` (0.55)
# and `settings.MIN_CONVICTION_TRADE` (0.62) by design — heuristic
# events alone can never cross the production trade gate.


def _heuristic_classify_event_type(title: str, text: str | None) -> EventType:
    """Keyword-based event type classification. Fallback path only."""
    combined = f"{title} {text or ''}".lower()
    shock_keywords = [
        "fire",
        "explosion",
        "shortage",
        "sanctions",
        "tariff",
        "strike",
        "recall",
        "shutdown",
        "layoff",
        "breach",
        "crash",
        "war",
        "flood",
        "earthquake",
        "pandemic",
        "ban",
        "embargo",
    ]
    recovery_keywords = [
        "recovery",
        "reopen",
        "resume",
        "resolve",
        "deal",
        "agreement",
        "expansion",
        "growth",
        "upgrade",
    ]
    earnings_keywords = [
        "earnings",
        "revenue",
        "profit",
        "loss",
        "guidance",
        "forecast",
        "quarterly",
        "annual",
        "EPS",
        "beat",
        "miss",
    ]
    macro_keywords = [
        "fed",
        "interest rate",
        "inflation",
        "GDP",
        "unemployment",
        "CPI",
        "PPI",
        "FOMC",
        "treasury",
        "yield",
        "central bank",
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


def _heuristic_news_to_causal_events(
    news_items: Sequence[NewsItem],
) -> list[CausalEvent]:
    """Keyword-heuristic fallback conversion. Fixed conf=0.5."""
    events: list[CausalEvent] = []
    for item in news_items:
        event_type = _heuristic_classify_event_type(item.title, item.text)
        for symbol in item.symbols:
            events.append(
                CausalEvent(
                    timestamp=item.timestamp,
                    source_node=symbol,
                    event_type=event_type,
                    compressed_summary=item.title,
                    confidence=0.5,
                    sequence_id=_next_seq(),
                )
            )
    return events


# Legacy aliases retained only for existing test imports. New callers
# MUST use the smart-dispatch `news_to_causal_events` above.
classify_event_type = _heuristic_classify_event_type
