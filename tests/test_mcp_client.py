"""Tests for the MCP client + the MT Newswires wrapper.

Two responsibilities:

1. The client fails closed when no URL is configured.
2. When a URL IS set, the JSON-RPC envelope is well-formed (mocked
   transport — no live HTTP).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data_primitives.mcp import client as mcp_client
from data_primitives.mcp import mt_newswires


@pytest.mark.asyncio
async def test_client_not_configured_returns_none() -> None:
    c = mcp_client.MCPClient(url=None)
    assert c.configured is False
    assert await c.list_tools() == []
    assert await c.call_tool("any.tool", {}) is None


@pytest.mark.asyncio
async def test_client_not_configured_strict_raises() -> None:
    c = mcp_client.MCPClient(url=None)
    with pytest.raises(mcp_client.MCPError):
        await c.call_tool_strict("any.tool", {})


@pytest.mark.asyncio
async def test_mt_newswires_wrapper_returns_empty_when_unset() -> None:
    # The autouse conftest sets up env without MCP_MT_NEWSWIRES_URL,
    # so the wrapper should short-circuit to [] without any HTTP.
    items = await mt_newswires.fetch_mt_newswires_items(limit=10)
    assert items == []


def _mock_async_client_response(json_payload: dict[str, Any], status_code: int = 200):
    """Build a context manager that yields a mock httpx AsyncClient.

    The mocked client's .post() returns a response with json() returning
    ``json_payload`` and status_code attribute set.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_payload)

    async_client = MagicMock()
    async_client.post = AsyncMock(return_value=resp)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=async_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, async_client


@pytest.mark.asyncio
async def test_call_tool_builds_jsonrpc_envelope() -> None:
    cm, async_client = _mock_async_client_response(
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    )

    with patch.object(mcp_client.httpx, "AsyncClient", return_value=cm):
        c = mcp_client.MCPClient(url="https://example.invalid/mcp", api_key="ak-test")
        result = await c.call_tool("news.recent", {"limit": 5})

    assert result == {"ok": True}

    async_client.post.assert_awaited_once()
    call_args = async_client.post.call_args
    posted_url = call_args.args[0]
    posted_payload = call_args.kwargs["json"]
    posted_headers = call_args.kwargs["headers"]

    assert posted_url == "https://example.invalid/mcp"
    assert posted_payload["jsonrpc"] == "2.0"
    assert posted_payload["method"] == "tools/call"
    assert posted_payload["params"]["name"] == "news.recent"
    assert posted_payload["params"]["arguments"] == {"limit": 5}
    assert posted_headers["authorization"] == "Bearer ak-test"


@pytest.mark.asyncio
async def test_call_tool_returns_none_on_jsonrpc_error() -> None:
    cm, _ = _mock_async_client_response(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "method not found"}}
    )

    with patch.object(mcp_client.httpx, "AsyncClient", return_value=cm):
        c = mcp_client.MCPClient(url="https://example.invalid/mcp")
        result = await c.call_tool("missing.tool", {})

    assert result is None


@pytest.mark.asyncio
async def test_call_tool_returns_none_on_http_error_status() -> None:
    cm, _ = _mock_async_client_response({}, status_code=502)

    with patch.object(mcp_client.httpx, "AsyncClient", return_value=cm):
        c = mcp_client.MCPClient(url="https://example.invalid/mcp")
        result = await c.call_tool("any.tool", {})

    assert result is None
