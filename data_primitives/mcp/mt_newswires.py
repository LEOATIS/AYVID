"""MT Newswires MCP wrapper — real-time market news feed.

Used by ``data_primitives/pipeline/continuous_worker.py`` as an optional
preferred source for shock detection. When ``MCP_MT_NEWSWIRES_URL`` is
unset (default), this module returns ``[]`` and the worker continues
on its existing scraper-based path. When set, the items returned
flow into the same NER pipeline as scraper output, so the downstream
contract is unchanged.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from data_primitives.mcp.client import MCPClient
from shared.config import settings
from shared.logger import get_logger
from shared.types import NewsItem

log = get_logger("data_primitives.mcp.mt_newswires")


def _build_client() -> MCPClient:
    url = getattr(settings, "MCP_MT_NEWSWIRES_URL", None)
    api_key = getattr(settings, "MCP_MT_NEWSWIRES_API_KEY", None)
    return MCPClient(url=url, api_key=api_key)


def _parse_timestamp(raw: Any) -> dt.datetime:
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=dt.UTC)
    if isinstance(raw, (int, float)):
        return dt.datetime.fromtimestamp(float(raw), tz=dt.UTC)
    if isinstance(raw, str):
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
        except ValueError:
            pass
    return dt.datetime.now(tz=dt.UTC)


def _to_news_item(raw: dict[str, Any]) -> NewsItem | None:
    title = raw.get("title") or raw.get("headline")
    if not title:
        return None
    return NewsItem(
        timestamp=_parse_timestamp(raw.get("published") or raw.get("timestamp")),
        title=str(title),
        source="mt_newswires",
        symbols=list(raw.get("symbols") or raw.get("tickers") or []),
        text=raw.get("body") or raw.get("text"),
        url=raw.get("url"),
        sentiment=raw.get("sentiment"),
    )


async def fetch_mt_newswires_items(
    *,
    tickers: list[str] | None = None,
    limit: int = 50,
) -> list[NewsItem]:
    """Fetch recent news items via the MT Newswires MCP server.

    Returns ``[]`` when the MCP URL is not configured, the call fails,
    or the response cannot be parsed. The continuous worker treats this
    as "this source returned nothing" and proceeds with the other
    feeds, matching its existing failure-tolerant behaviour.
    """
    client = _build_client()
    if not client.configured:
        return []

    arguments: dict[str, Any] = {"limit": limit}
    if tickers:
        arguments["tickers"] = list(tickers)

    result = await client.call_tool("news.recent", arguments)
    if result is None:
        return []

    raw_items = result.get("items") or result.get("news") or []
    if not isinstance(raw_items, list):
        log.warning("MT Newswires MCP returned non-list items payload")
        return []

    items: list[NewsItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = _to_news_item(raw)
        if item is not None:
            items.append(item)
    return items
