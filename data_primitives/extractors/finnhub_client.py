"""Finnhub client — news, sentiment, insider trades, recommendations, and earnings.

Uses the free-tier Finnhub API (60 calls/min). All functions degrade gracefully
when the API key is missing or the endpoint errors out.
"""

from __future__ import annotations

import datetime as dt

import httpx

from shared.config import settings
from shared.logger import get_logger
from shared.types import NewsItem

log = get_logger("data.finnhub")

_BASE_URL = "https://finnhub.io/api/v1"
_USER_AGENT = "AYVID/1.0 (research bot)"


def _api_key() -> str | None:
    return settings.FINNHUB_API_KEY


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> dict | list | None:
    key = _api_key()
    if not key:
        log.debug("FINNHUB_API_KEY not set — skipping request")
        return None
    params["token"] = key
    try:
        resp = await client.get(f"{_BASE_URL}{path}", params=params)
        if resp.status_code == 429:
            log.warning("Finnhub rate-limited")
            return None
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning(f"Finnhub request failed ({path}): {exc}")
        return None


async def fetch_finnhub_news(ticker: str, days_back: int = 7) -> list[NewsItem]:
    """Fetch company news from Finnhub for the past *days_back* days."""
    today = dt.date.today()
    from_date = today - dt.timedelta(days=days_back)
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(15.0),
    ) as client:
        data = await _get(
            client,
            "/company-news",
            {
                "symbol": ticker.upper(),
                "from": from_date.isoformat(),
                "to": today.isoformat(),
            },
        )

    if not isinstance(data, list):
        return []

    items: list[NewsItem] = []
    for article in data:
        ts_epoch = article.get("datetime")
        headline = article.get("headline")
        if not ts_epoch or not headline:
            continue
        items.append(
            NewsItem(
                timestamp=dt.datetime.fromtimestamp(ts_epoch, tz=dt.UTC),
                title=headline,
                source="finnhub",
                symbols=[ticker.upper()],
                text=article.get("summary") or None,
                url=article.get("url"),
                sentiment=None,
            )
        )

    items.sort(key=lambda n: n.timestamp)
    log.info(f"Finnhub: {len(items)} news articles for {ticker}")
    return items


async def fetch_finnhub_sentiment(ticker: str) -> dict:
    """Fetch Finnhub news-sentiment aggregate (buzz, bullish/bearish pct, etc.)."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(15.0),
    ) as client:
        data = await _get(client, "/news-sentiment", {"symbol": ticker.upper()})
    if not isinstance(data, dict):
        return {}
    return data


async def fetch_finnhub_insider_transactions(ticker: str) -> list[dict]:
    """Fetch insider transaction filings for *ticker*."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(15.0),
    ) as client:
        data = await _get(client, "/stock/insider-transactions", {"symbol": ticker.upper()})
    if not isinstance(data, dict):
        return []
    return data.get("data", [])


async def fetch_finnhub_recommendation_trends(ticker: str) -> list[dict]:
    """Fetch analyst recommendation trends (buy/hold/sell counts by period)."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(15.0),
    ) as client:
        data = await _get(client, "/stock/recommendation", {"symbol": ticker.upper()})
    if not isinstance(data, list):
        return []
    return data


async def fetch_finnhub_earnings_surprises(ticker: str) -> list[dict]:
    """Fetch historical earnings surprise data for *ticker*."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(15.0),
    ) as client:
        data = await _get(client, "/stock/earnings", {"symbol": ticker.upper()})
    if not isinstance(data, list):
        return []
    return data
