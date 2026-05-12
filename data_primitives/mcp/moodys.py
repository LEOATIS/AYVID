"""Moody's MCP wrapper — credit ratings.

Used by the ETFs & bonds asset class for rating-aware sizing. Stub
returns ``None`` when MCP_MOODYS_URL is unset; sizing falls back to
the BBB-uniform assumption.
"""

from __future__ import annotations

from typing import Any

from data_primitives.mcp.client import MCPClient
from shared.config import settings


def _build_client() -> MCPClient:
    url = getattr(settings, "MCP_MOODYS_URL", None)
    api_key = getattr(settings, "MCP_MOODYS_API_KEY", None)
    return MCPClient(url=url, api_key=api_key)


async def fetch_rating(issuer_or_ticker: str) -> dict[str, Any] | None:
    """Fetch the latest credit rating for an issuer or ETF underlying."""
    client = _build_client()
    if not client.configured:
        return None
    return await client.call_tool("ratings.latest", {"identifier": issuer_or_ticker})
