"""Async MCP-over-HTTP client.

Implements the minimum surface area of the Model Context Protocol
needed to call tools on a remote MCP server: ``tools/list`` and
``tools/call`` over JSON-RPC 2.0 / HTTP POST. SSE-streamed responses
are not required by AYVID's current use cases (news + transcript fetch
are request/response, not streaming).

The client deliberately fails closed: if ``url`` is ``None``, every
method short-circuits to ``None`` / ``[]`` instead of raising so
upstream callers (notably the continuous ingestion worker) can keep
running on their existing fallback paths.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

import httpx

from shared.logger import get_logger

log = get_logger("data_primitives.mcp")

# Per-call timeout. MCP requests are typically fast (~1-3s); a 15s
# ceiling means a stalled server can't block an ingestion cycle.
_DEFAULT_TIMEOUT_SEC: float = 15.0


class MCPError(RuntimeError):
    """Raised by ``MCPClient.call_tool_strict`` on protocol failure.

    The lenient ``call_tool`` returns ``None`` on the same conditions
    so the continuous worker's fetch_tasks gather pattern keeps
    running. Strict mode is for tests and explicit caller opt-in.
    """


class MCPClient:
    """Thin async client for one MCP server.

    Pass ``url=None`` for the "not configured" path; every method
    returns ``None`` / ``[]`` without an HTTP attempt. This keeps the
    ingestion worker call-site identical whether or not the operator
    has set the matching env var.
    """

    def __init__(
        self,
        url: str | None,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SEC,
        api_key: str | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._api_key = api_key
        self._id_counter = itertools.count(1)

    @property
    def configured(self) -> bool:
        return bool(self._url)

    async def list_tools(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        result = await self._rpc("tools/list", {})
        if result is None:
            return []
        return list(result.get("tools", []))

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Lenient call. Returns ``None`` on any error (logged at WARNING)."""
        if not self.configured:
            log.warning(
                "MCP client called but no URL configured for tool=%s; "
                "returning None (falling back to existing data path).",
                name,
            )
            return None
        return await self._rpc("tools/call", {"name": name, "arguments": arguments})

    async def call_tool_strict(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Strict call. Raises ``MCPError`` on failure. Used by tests."""
        if not self.configured:
            raise MCPError(f"No URL configured for MCP tool {name!r}")
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result is None:
            raise MCPError(f"MCP tool {name!r} returned no result")
        return result

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        assert self._url is not None  # gated by `configured` check at every call site
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": method,
            "params": params,
        }
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                resp = await asyncio.wait_for(
                    http.post(self._url, json=payload, headers=headers),
                    timeout=self._timeout,
                )
        except (httpx.HTTPError, TimeoutError) as exc:
            log.warning("MCP transport failed for %s %s: %s", self._url, method, exc)
            return None

        if resp.status_code >= 400:
            log.warning(
                "MCP server returned %d for %s %s",
                resp.status_code,
                self._url,
                method,
            )
            return None

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            log.warning("MCP server returned non-JSON for %s: %s", self._url, exc)
            return None

        if "error" in body:
            log.warning(
                "MCP server returned JSON-RPC error for %s %s: %s",
                self._url,
                method,
                body["error"],
            )
            return None

        return body.get("result")
