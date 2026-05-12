"""S&P Global / Capital IQ MCP wrapper — sector benchmarks + tear sheets.

Used by the ``equity-research/sector-overview`` skill. Stub-shaped: one
``fetch_tear_sheet`` entry point. Returns ``None`` when MCP_SP_GLOBAL_URL
is unset; the sector-overview skill falls back to the screener output.
"""

from __future__ import annotations

from typing import Any

from data_primitives.mcp.client import MCPClient
from shared.config import settings


def _build_client() -> MCPClient:
    url = getattr(settings, "MCP_SP_GLOBAL_URL", None)
    api_key = getattr(settings, "MCP_SP_GLOBAL_API_KEY", None)
    return MCPClient(url=url, api_key=api_key)


async def fetch_tear_sheet(ticker: str) -> dict[str, Any] | None:
    """Fetch a Capital IQ tear sheet for ``ticker``."""
    client = _build_client()
    if not client.configured:
        return None
    return await client.call_tool("tear_sheet.fetch", {"ticker": ticker})
