"""FactSet MCP wrapper — multi-asset fundamentals + consensus + IV.

Stub-shaped: one canonical ``fetch_consensus`` entry point that the
analyst and earnings-preview skills route through. Full FactSet MCP
surface is much larger; expand as needs arise.
"""

from __future__ import annotations

from typing import Any

from data_primitives.mcp.client import MCPClient
from shared.config import settings


def _build_client() -> MCPClient:
    url = getattr(settings, "MCP_FACTSET_URL", None)
    api_key = getattr(settings, "MCP_FACTSET_API_KEY", None)
    return MCPClient(url=url, api_key=api_key)


async def fetch_consensus(
    ticker: str,
    *,
    metrics: list[str] | None = None,
) -> dict[str, Any] | None:
    """Fetch consensus estimates for ``ticker``.

    ``metrics`` defaults to the standard EPS + revenue + EBIT margin
    triad when omitted. Returns ``None`` if FactSet MCP not configured.
    """
    client = _build_client()
    if not client.configured:
        return None

    arguments: dict[str, Any] = {"ticker": ticker}
    arguments["metrics"] = metrics or ["eps", "revenue", "ebit_margin"]
    return await client.call_tool("consensus.fetch", arguments)
