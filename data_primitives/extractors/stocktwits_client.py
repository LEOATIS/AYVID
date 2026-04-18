"""StockTwits social sentiment — public API, no auth required.

Fetches message streams for tickers and aggregates bull/bear sentiment
ratios as an alternative signal source.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import httpx

from shared.logger import get_logger
from shared.types import NewsItem

log = get_logger("data.stocktwits")

_BASE_URL = "https://api.stocktwits.com/api/2"
_HEADERS = {"User-Agent": "AYVID/1.0"}


async def fetch_stocktwits_sentiment(
    ticker: str,
    limit: int = 30,
) -> tuple[list[NewsItem], dict[str, Any]]:
    """Fetch recent StockTwits messages and compute aggregate sentiment.

    Returns (posts_as_NewsItems, aggregate_sentiment_dict).
    """
    url = f"{_BASE_URL}/streams/symbol/{ticker.upper()}.json"
    items: list[NewsItem] = []
    agg: dict[str, Any] = {"bullish_count": 0, "bearish_count": 0, "total": 0, "bull_ratio": 0.0}

    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.debug(f"StockTwits {ticker}: HTTP {resp.status_code}")
                return items, agg

            data = resp.json()
            messages = data.get("messages", [])[:limit]

            for msg in messages:
                body = msg.get("body", "")
                created = msg.get("created_at", "")
                sentiment_obj = msg.get("entities", {}).get("sentiment", {})
                basic = sentiment_obj.get("basic") if sentiment_obj else None

                if basic == "Bullish":
                    score = 1.0
                    agg["bullish_count"] += 1
                elif basic == "Bearish":
                    score = -1.0
                    agg["bearish_count"] += 1
                else:
                    score = 0.0

                try:
                    ts = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    ts = dt.datetime.now(dt.UTC)

                items.append(NewsItem(
                    timestamp=ts,
                    title=body[:200],
                    source="stocktwits",
                    symbols=[ticker.upper()],
                    text=body,
                    sentiment=score,
                ))

            agg["total"] = len(messages)
            if agg["total"] > 0:
                agg["bull_ratio"] = agg["bullish_count"] / agg["total"]

    except Exception as exc:
        log.debug(f"StockTwits fetch failed for {ticker}: {exc}")

    return items, agg


async def fetch_stocktwits_batch(
    tickers: list[str],
) -> dict[str, dict[str, Any]]:
    """Fetch aggregate sentiment for multiple tickers in parallel."""
    async def _single(ticker: str) -> tuple[str, dict[str, Any]]:
        _, agg = await fetch_stocktwits_sentiment(ticker, limit=30)
        return ticker, agg

    tasks = [_single(t) for t in tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: dict[str, dict[str, Any]] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        ticker, agg = r
        out[ticker] = agg

    return out
