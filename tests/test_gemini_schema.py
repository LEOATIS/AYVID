"""Gemini controlled generation: the research synthesis schema reaches the API (no network)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from backend.services.research_orchestrator import SYNTHESIS_SCHEMA
from shared import llm_client
from shared.config import settings


def test_gemini_request_carries_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...
        async def __aenter__(self) -> FakeClient: return self
        async def __aexit__(self, *_: Any) -> None: ...
        async def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
            sent.update(url=url, body=json)
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "candidates": [{"content": {"parts": [{"text": '{"synthesis": "ok [S1]"}'}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    text, tin, tout = asyncio.run(llm_client._gemini_generate(
        "sys", "user", model="gemini-2.5-flash", max_tokens=50, temperature=0.0, json_mode=True,
        schema=SYNTHESIS_SCHEMA))

    cfg = sent["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["responseSchema"] == SYNTHESIS_SCHEMA
    assert sent["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert (text, tin, tout) == ('{"synthesis": "ok [S1]"}', 5, 3)
