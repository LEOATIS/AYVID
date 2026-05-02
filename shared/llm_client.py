"""Single-source-of-truth LLM dispatch layer.

Every module in the codebase that calls OpenAI or Anthropic must go
through this layer. It owns:

1. The per-provider async client singletons (one `AsyncOpenAI`, one
   `AsyncAnthropic`) — previously 8 scattered `_openai_client` /
   `_anthropic_client` globals.
2. Provider dispatch via `settings.LLM_PROVIDER`.
3. The JSON-mode contract: callers pass a system prompt and a user
   prompt and get back a JSON string. The module handles Anthropic's
   "respond JSON only" suffix + OpenAI's `response_format={...}` knob.
4. Embeddings for Qdrant.
5. A thin `chat(...)` entry for agents that need non-JSON free-form
   reasoning (pattern agent vision, auditor rationale, etc.).

Design rules:

- This module must never import from `graph_intelligence`, `backtesting`,
  `execution_engine`, or `backend`. It's inside `shared/` — the leaf
  layer of the dependency graph.
- Clients are lazily constructed on first use so importing this module
  does NOT require API keys to exist.
- Every call takes `temperature=0.0` by default so LLM outputs are
  deterministic-ish across re-runs. Callers may override.
"""

from __future__ import annotations

import asyncio
from typing import Any

from shared.config import settings
from shared.logger import get_logger

log = get_logger("shared.llm")


# Per-call wall-clock timeout. A stalled stream from either provider
# would otherwise block an entire agent chain forever; the LangGraph
# pipeline has no global timeout so this single-call ceiling is what
# bounds the worst case. 30s is comfortably above p99 normal latency
# (~3–6s) and well below the orchestrator's per-decision budget.
DEFAULT_LLM_TIMEOUT_SEC: float = 30.0


_openai_client: Any = None
_anthropic_client: Any = None


# ── Client acquisition ───────────────────────────────────────


def _get_openai_client() -> Any:
    """Return the module-global AsyncOpenAI singleton, lazily constructed."""
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI

        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


def _get_anthropic_client() -> Any:
    """Return the module-global AsyncAnthropic singleton, lazily constructed."""
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic

        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


def _use_anthropic() -> bool:
    """Dispatch policy: Anthropic only when configured AND API key is present."""
    return settings.LLM_PROVIDER == "anthropic" and bool(settings.ANTHROPIC_API_KEY)


# ── JSON-mode completion (the workhorse) ─────────────────────


_JSON_ONLY_SUFFIX = "\n\nYou MUST respond with valid JSON only. No markdown, no explanation."


async def json_call(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    model: str | None = None,
    timeout: float | None = None,
) -> str:
    """Run a JSON-mode completion against the configured provider.

    Returns the raw JSON string (no post-parsing). Callers that need a
    dict should wrap with `_parse_llm_json` helpers at their use-site —
    those are free to raise or fall back on malformed output.

    Every call is wrapped in ``asyncio.wait_for(timeout=...)`` so a
    stalled provider cannot block the orchestrator; on timeout we
    raise ``asyncio.TimeoutError`` and let the caller fall back.
    """
    deadline = timeout if timeout is not None else DEFAULT_LLM_TIMEOUT_SEC
    if _use_anthropic():
        client = _get_anthropic_client()
        response = await asyncio.wait_for(
            client.messages.create(
                model=model or settings.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                system=system + _JSON_ONLY_SUFFIX,
                messages=[{"role": "user", "content": user}],
                temperature=temperature,
            ),
            timeout=deadline,
        )
        return response.content[0].text if response.content else "{}"

    client = _get_openai_client()
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=model or settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        ),
        timeout=deadline,
    )
    return response.choices[0].message.content or "{}"


# ── Free-form chat (non-JSON) ────────────────────────────────


async def chat(
    system: str,
    user: str,
    *,
    max_tokens: int = 2000,
    temperature: float = 0.0,
    model: str | None = None,
    timeout: float | None = None,
) -> str:
    """Run a free-form (non-JSON) completion. Used by agents that need
    natural-language rationale (auditor, pattern-agent vision, etc.).
    """
    deadline = timeout if timeout is not None else DEFAULT_LLM_TIMEOUT_SEC
    if _use_anthropic():
        client = _get_anthropic_client()
        response = await asyncio.wait_for(
            client.messages.create(
                model=model or settings.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=temperature,
            ),
            timeout=deadline,
        )
        return response.content[0].text if response.content else ""

    client = _get_openai_client()
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=model or settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        ),
        timeout=deadline,
    )
    return response.choices[0].message.content or ""


# ── Embeddings ───────────────────────────────────────────────


async def embed(text: str, *, model: str | None = None) -> list[float]:
    """Generate an embedding vector via OpenAI (the provider we always
    use for embeddings, regardless of `LLM_PROVIDER`)."""
    client = _get_openai_client()
    response = await client.embeddings.create(
        model=model or settings.OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


# ── Provider-direct escape hatches ───────────────────────────


def get_openai_client() -> Any:
    """Public accessor for the OpenAI singleton. Use ONLY when you need a
    provider feature not exposed by `json_call` / `chat` / `embed` (logprobs,
    multi-turn memory, vision inputs, etc.)."""
    return _get_openai_client()


def get_anthropic_client() -> Any:
    """Public accessor for the Anthropic singleton. Same contract as above."""
    return _get_anthropic_client()


# ── LangChain factory (for agent nodes) ──────────────────────


def get_langchain_chat_model(
    *,
    temperature: float = 0.1,
    max_tokens: int = 1500,
):
    """Return a configured LangChain chat model for the active provider.

    LangGraph agent nodes use LangChain wrappers (`ChatOpenAI` /
    `ChatAnthropic`) rather than the raw SDK because they need
    streaming + tool-calling semantics. This factory centralises the
    provider-dispatch logic that was duplicated across every node.
    """
    if _use_anthropic():
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )


__all__ = [
    "json_call",
    "chat",
    "embed",
    "get_openai_client",
    "get_anthropic_client",
    "get_langchain_chat_model",
]
