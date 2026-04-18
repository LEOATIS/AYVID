"""Fault injection framework for chaos/resilience testing.

Context managers that simulate external service failures by patching
the production call sites. Each injector raises the same exception
type the system would encounter in a real outage.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

from shared.exceptions import (
    IBKRDisconnectError,
    LLMProviderError,
    LLMRateLimitError,
    Neo4jConnectionError,
    OpenBBFetchError,
)


@contextmanager
def simulate_llm_outage(
    *,
    error: type[Exception] = LLMProviderError,
    message: str = "Simulated LLM outage",
) -> Generator[None, None, None]:
    """Patch LLM ``astream`` and ``ainvoke`` to raise on every call."""
    async def _fail(*args: Any, **kwargs: Any) -> None:
        raise error(message)

    targets = [
        "graph_intelligence.agents.analyst.node._run_specialist_streaming",
        "graph_intelligence.agents.strategy_orchestrator.orchestrate_trade",
    ]
    patches = [patch(t, side_effect=_fail) for t in targets]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


@contextmanager
def simulate_llm_rate_limit(
    *,
    message: str = "Rate limit exceeded (simulated)",
) -> Generator[None, None, None]:
    """Patch LLM calls to raise ``LLMRateLimitError``."""
    async def _fail(*args: Any, **kwargs: Any) -> None:
        raise LLMRateLimitError(message)

    targets = [
        "graph_intelligence.agents.analyst.node._run_specialist_streaming",
        "graph_intelligence.agents.strategy_orchestrator.orchestrate_trade",
    ]
    patches = [patch(t, side_effect=_fail) for t in targets]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


@contextmanager
def simulate_ib_disconnect(
    *,
    message: str = "Simulated IB disconnect",
) -> Generator[None, None, None]:
    """Patch ``get_ib()`` to raise ``IBKRDisconnectError``."""
    async def _fail(*args: Any, **kwargs: Any) -> None:
        raise IBKRDisconnectError(message)

    with patch(
        "execution_engine.broker.connection.get_ib",
        side_effect=_fail,
    ):
        yield


@contextmanager
def simulate_openbb_timeout(
    *,
    timeout_seconds: float = 0.0,
) -> Generator[None, None, None]:
    """Patch ``fetch_ohlcv`` to raise ``asyncio.TimeoutError``."""
    async def _fail(*args: Any, **kwargs: Any) -> None:
        if timeout_seconds > 0:
            await asyncio.sleep(timeout_seconds)
        raise asyncio.TimeoutError("Simulated OpenBB timeout")

    with patch(
        "data_primitives.extractors.openbb_client.fetch_ohlcv",
        side_effect=_fail,
    ), patch(
        "data_primitives.extractors.openbb_client.fetch_multi_ohlcv",
        side_effect=_fail,
    ), patch(
        "data_primitives.extractors.openbb_client.fetch_world_news",
        side_effect=_fail,
    ):
        yield


@contextmanager
def simulate_neo4j_down(
    *,
    message: str = "Simulated Neo4j outage",
) -> Generator[None, None, None]:
    """Patch ``get_driver()`` to raise ``Neo4jConnectionError``."""
    async def _fail(*args: Any, **kwargs: Any) -> None:
        raise Neo4jConnectionError(message)

    def _fail_sync(*args: Any, **kwargs: Any) -> None:
        raise Neo4jConnectionError(message)

    with patch(
        "graph_intelligence.tools.neo4j_tools.get_driver",
        side_effect=_fail,
    ), patch(
        "graph_intelligence.tools.neo4j_tools.verify_connection",
        side_effect=_fail,
    ):
        yield


@contextmanager
def simulate_all_services_down() -> Generator[None, None, None]:
    """Nuclear option: all external services fail simultaneously."""
    with (
        simulate_llm_outage(),
        simulate_ib_disconnect(),
        simulate_openbb_timeout(),
        simulate_neo4j_down(),
    ):
        yield
