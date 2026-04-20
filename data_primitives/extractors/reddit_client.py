"""Reddit sentiment scraper — searches public JSON API across finance subreddits.

Provides retail-sentiment signal as a complement to institutional data sources.
No authentication required for read-only JSON endpoints.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from difflib import SequenceMatcher

import httpx

from shared.logger import get_logger
from shared.types import NewsItem

log = get_logger("data.reddit")

_SUBREDDITS = ["wallstreetbets", "stocks", "investing", "options", "SecurityAnalysis"]
_USER_AGENT = "AYVID/1.0 (research bot)"
_BASE_URL = "https://www.reddit.com"


async def _search_subreddit(
    client: httpx.AsyncClient,
    subreddit: str,
    ticker: str,
    limit: int,
) -> list[NewsItem]:
    url = f"{_BASE_URL}/r/{subreddit}/search.json"
    params = {"q": ticker, "sort": "new", "limit": limit, "t": "week", "restrict_sr": "on"}
    try:
        resp = await client.get(url, params=params)
        if resp.status_code == 429:
            log.warning(f"Reddit rate-limited on r/{subreddit} for {ticker}")
            return []
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning(f"Reddit fetch failed for r/{subreddit}/{ticker}: {exc}")
        return []

    items: list[NewsItem] = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        created_utc = post.get("created_utc")
        title = post.get("title")
        if not created_utc or not title:
            continue
        items.append(NewsItem(
            timestamp=dt.datetime.fromtimestamp(created_utc, tz=dt.timezone.utc),
            title=title,
            source=f"reddit/{subreddit}",
            symbols=[ticker.upper()],
            text=post.get("selftext") or None,
            url=f"https://reddit.com{post.get('permalink', '')}",
            sentiment=None,
        ))
    return items


def _deduplicate_by_title(items: list[NewsItem], threshold: float = 0.85) -> list[NewsItem]:
    seen_titles: list[str] = []
    unique: list[NewsItem] = []
    for item in items:
        is_dup = any(
            SequenceMatcher(None, item.title.lower(), t).ratio() >= threshold
            for t in seen_titles
        )
        if not is_dup:
            seen_titles.append(item.title.lower())
            unique.append(item)
    return unique


async def fetch_reddit_sentiment(ticker: str, limit: int = 25) -> list[NewsItem]:
    """Search all finance subreddits for posts mentioning *ticker*.

    Returns deduplicated NewsItems sorted by timestamp (oldest first).
    Sentiment is left as ``None`` here and filled in downstream by
    ``data_primitives.transformers.sentiment.score_news_items`` (OpenAI,
    [-1, +1], 0.0 neutral).
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
    ) as client:
        tasks: list[asyncio.Task[list[NewsItem]]] = []
        for idx, sub in enumerate(_SUBREDDITS):
            if idx > 0:
                await asyncio.sleep(1)
            tasks.append(asyncio.create_task(_search_subreddit(client, sub, ticker, limit)))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[NewsItem] = []
    for result in results:
        if isinstance(result, Exception):
            log.warning(f"Reddit subreddit task failed: {result}")
            continue
        all_items.extend(result)

    deduped = _deduplicate_by_title(all_items)
    deduped.sort(key=lambda n: n.timestamp)
    log.info(f"Reddit: fetched {len(deduped)} unique posts for {ticker}")
    return deduped


async def fetch_reddit_batch(tickers: list[str], limit_per: int = 10) -> list[NewsItem]:
    """Fetch Reddit posts for multiple tickers in parallel, merged and sorted."""
    tasks = [fetch_reddit_sentiment(t, limit=limit_per) for t in tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: list[NewsItem] = []
    for ticker, result in zip(tickers, results):
        if isinstance(result, Exception):
            log.warning(f"Reddit batch failed for {ticker}: {result}")
            continue
        merged.extend(result)

    merged.sort(key=lambda n: n.timestamp)
    return merged
