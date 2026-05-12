"""MCP (Model Context Protocol) connectors for AYVID.

This package wraps the buy-side-relevant MCP servers from
anthropic/financial-services so they plug into AYVID's existing
data primitives:

- ``mt_newswires`` — real-time market news, consumed by the continuous
  ingestion worker for shock detection.
- ``aiera`` — earnings call transcripts, consumed by the equity-research
  earnings-analysis skill.
- ``factset`` / ``moodys`` / ``sp_global`` — fundamental, credit, and
  sector data; lightweight wrappers for the analyst / sector-overview
  paths.

Every wrapper fails closed: if its env-driven URL is not configured,
calls return ``None`` / ``[]`` instead of raising, so the worker keeps
running on the existing scraper-based fallback.
"""

from data_primitives.mcp.client import MCPClient, MCPError
from data_primitives.mcp.mt_newswires import fetch_mt_newswires_items

__all__ = ["MCPClient", "MCPError", "fetch_mt_newswires_items"]
