"""Aiera MCP wrapper — earnings call transcripts.

Used by the ``equity-research/earnings-analysis`` and
``equity-research/earnings-preview`` skills. When ``MCP_AIERA_URL`` is
unset, ``fetch_transcript`` returns ``None`` and callers fall back to
scraped transcript paths.
"""

from __future__ import annotations

from typing import Any

from data_primitives.mcp.client import MCPClient
from shared.config import settings


def _build_client() -> MCPClient:
    url = getattr(settings, "MCP_AIERA_URL", None)
    api_key = getattr(settings, "MCP_AIERA_API_KEY", None)
    return MCPClient(url=url, api_key=api_key)


async def fetch_transcript(
    ticker: str,
    *,
    reporting_period: str | None = None,
) -> dict[str, Any] | None:
    """Fetch an earnings transcript for ``ticker``.

    Returns the raw MCP result dict (transcript text + speaker turns +
    timestamps) or ``None`` if MCP is not configured / call failed.
    Callers should treat ``None`` as "transcript not available via
    Aiera; try scraper".
    """
    client = _build_client()
    if not client.configured:
        return None

    arguments: dict[str, Any] = {"ticker": ticker}
    if reporting_period:
        arguments["reporting_period"] = reporting_period

    return await client.call_tool("transcripts.fetch", arguments)
