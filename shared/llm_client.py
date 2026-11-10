"""Single-source-of-truth LLM dispatch layer.

Every module in the codebase that calls Gemini, OpenAI or Anthropic must go
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
import time
from collections import deque
from dataclasses import asdict, dataclass
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

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
PROVIDERS: tuple[str, ...] = ("gemini", "anthropic", "openai")
DETERMINISTIC = "deterministic"
_TELEMETRY_RING_SIZE = 200


class LLMUnavailableError(RuntimeError):
    """No provider key is configured — callers must use deterministic paths."""


@dataclass(frozen=True)
class LLMCallMeta:
    """Telemetry for one completion — surfaced in the research Evidence Drawer."""

    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    ok: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Recent-call ring buffer for /system status views. Per-run telemetry is
# returned directly by ``json_call_with_meta`` so concurrent runs never mix.
_recent_calls: deque[LLMCallMeta] = deque(maxlen=_TELEMETRY_RING_SIZE)


def recent_llm_calls() -> list[dict[str, Any]]:
    return [m.to_dict() for m in _recent_calls]


def _provider_key(provider: str) -> str | None:
    return {
        "gemini": settings.GEMINI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }.get(provider)


def active_provider() -> str:
    """Selected provider if its key exists, else the first configured one,
    else ``"deterministic"`` (no LLM reachable)."""
    selected = (settings.LLM_PROVIDER or "").strip().lower()
    if selected in PROVIDERS and _provider_key(selected):
        return selected
    for candidate in PROVIDERS:
        if _provider_key(candidate):
            return candidate
    return DETERMINISTIC


def active_model(provider: str | None = None) -> str | None:
    provider = provider or active_provider()
    return {
        "gemini": settings.GEMINI_MODEL,
        "anthropic": settings.ANTHROPIC_MODEL,
        "openai": settings.OPENAI_MODEL,
    }.get(provider)


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


async def _gemini_generate(
    system: str, user: str, *, model: str, max_tokens: int, temperature: float, json_mode: bool
) -> tuple[str, int | None, int | None]:
    import httpx

    config: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_mode:
        config["responseMimeType"] = "application/json"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": config,
    }
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(
            f"{GEMINI_API_BASE}/models/{model}:generateContent",
            headers={"x-goog-api-key": settings.GEMINI_API_KEY or ""},
            json=body,
        )
    resp.raise_for_status()
    data = resp.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    usage = data.get("usageMetadata", {})
    return text, usage.get("promptTokenCount"), usage.get("candidatesTokenCount")


async def _dispatch(
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
    model: str | None,
    json_mode: bool,
) -> tuple[str, int | None, int | None, str, str]:
    provider = active_provider()
    if provider == DETERMINISTIC:
        raise LLMUnavailableError("no LLM provider key configured")
    chosen = model or active_model(provider) or ""

    if provider == "gemini":
        text, tin, tout = await _gemini_generate(
            system, user, model=chosen, max_tokens=max_tokens,
            temperature=temperature, json_mode=json_mode,
        )
        return text or ("{}" if json_mode else ""), tin, tout, provider, chosen

    if provider == "anthropic":
        client = _get_anthropic_client()
        response = await client.messages.create(
            model=chosen,
            max_tokens=max_tokens,
            system=system + (_JSON_ONLY_SUFFIX if json_mode else ""),
            messages=[{"role": "user", "content": user}],
            temperature=temperature,
        )
        text = response.content[0].text if response.content else ("{}" if json_mode else "")
        usage = getattr(response, "usage", None)
        return (
            text,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            provider,
            chosen,
        )

    client = _get_openai_client()
    kwargs: dict[str, Any] = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = await client.chat.completions.create(**kwargs)
    text = response.choices[0].message.content or ("{}" if json_mode else "")
    usage = getattr(response, "usage", None)
    return (
        text,
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        provider,
        chosen,
    )


async def _call_with_meta(
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
    model: str | None,
    timeout: float | None,
    json_mode: bool,
) -> tuple[str, LLMCallMeta]:
    deadline = timeout if timeout is not None else DEFAULT_LLM_TIMEOUT_SEC
    started = time.perf_counter()
    provider = active_provider()
    try:
        text, tin, tout, provider, chosen = await asyncio.wait_for(
            _dispatch(
                system, user, max_tokens=max_tokens, temperature=temperature,
                model=model, json_mode=json_mode,
            ),
            timeout=deadline,
        )
    except Exception as exc:
        meta = LLMCallMeta(
            provider=provider,
            model=model or active_model(provider) or "",
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=None,
            output_tokens=None,
            ok=False,
            error=type(exc).__name__,
        )
        _recent_calls.append(meta)
        log.warning("llm_call_failed %s", meta.to_dict())
        raise
    meta = LLMCallMeta(
        provider=provider,
        model=chosen,
        latency_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=tin,
        output_tokens=tout,
        ok=True,
    )
    _recent_calls.append(meta)
    log.info("llm_call %s", meta.to_dict())
    return text, meta


async def json_call_with_meta(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    model: str | None = None,
    timeout: float | None = None,
) -> tuple[str, LLMCallMeta]:
    """``json_call`` plus provider / model / latency / token telemetry."""
    return await _call_with_meta(
        system, user, max_tokens=max_tokens, temperature=temperature,
        model=model, timeout=timeout, json_mode=True,
    )


async def json_call(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    model: str | None = None,
    timeout: float | None = None,
) -> str:
    """Run a JSON-mode completion against the active provider.

    Returns the raw JSON string (no post-parsing). Every call is bounded by
    ``asyncio.wait_for`` so a stalled provider cannot block the caller; on
    timeout we raise ``asyncio.TimeoutError``. Raises ``LLMUnavailableError``
    when no provider key is configured.
    """
    text, _ = await json_call_with_meta(
        system, user, max_tokens=max_tokens, temperature=temperature,
        model=model, timeout=timeout,
    )
    return text


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
    text, _ = await _call_with_meta(
        system, user, max_tokens=max_tokens, temperature=temperature,
        model=model, timeout=timeout, json_mode=False,
    )
    return text


# ── Embeddings ───────────────────────────────────────────────


async def embed(text: str, *, model: str | None = None) -> list[float]:
    """Generate an embedding vector. OpenAI when its key exists (keeps the
    existing Qdrant collection's dimensionality); Gemini when it is the only
    configured provider."""
    if not settings.OPENAI_API_KEY and settings.GEMINI_API_KEY:
        import httpx

        gem_model = model or settings.GEMINI_EMBEDDING_MODEL
        async with httpx.AsyncClient(timeout=DEFAULT_LLM_TIMEOUT_SEC) as http:
            resp = await http.post(
                f"{GEMINI_API_BASE}/models/{gem_model}:embedContent",
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                json={"content": {"parts": [{"text": text}]}},
            )
        resp.raise_for_status()
        return list(resp.json()["embedding"]["values"])
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
    "LLMCallMeta",
    "LLMUnavailableError",
    "active_model",
    "active_provider",
    "json_call_with_meta",
    "recent_llm_calls",
    "json_call",
    "chat",
    "embed",
    "get_openai_client",
    "get_anthropic_client",
    "get_langchain_chat_model",
]
