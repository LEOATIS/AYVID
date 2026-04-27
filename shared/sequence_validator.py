from __future__ import annotations

import threading
from collections.abc import Coroutine
from typing import Any

from shared.exceptions import SequenceValidationError
from shared.logger import get_logger

log = get_logger("sequence")

_SOURCES = frozenset({"neo4j", "questdb", "qdrant", "pipeline"})


class SequenceRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequences: dict[str, int] = {src: 0 for src in _SOURCES}

    def register_write(self, source: str, sequence_id: int) -> None:
        with self._lock:
            prev = self._sequences.get(source, 0)
            if sequence_id > prev:
                self._sequences[source] = sequence_id
                log.debug("seq/%s: %d -> %d", source, prev, sequence_id)

    def get_sequence(self, source: str) -> int:
        with self._lock:
            return self._sequences.get(source, 0)

    def get_all(self) -> dict[str, int]:
        with self._lock:
            return dict(self._sequences)

    def check_sync(self, tolerance: int = 10) -> tuple[bool, dict[str, int]]:
        with self._lock:
            seqs = dict(self._sequences)
        if not seqs:
            return True, seqs
        hi = max(seqs.values())
        lo = min(seqs.values())
        return (hi - lo) <= tolerance, seqs

    def validate_for_trade(self, required_seq: int, tolerance: int = 10) -> None:
        with self._lock:
            seqs = dict(self._sequences)
        for source, current in seqs.items():
            if required_seq - current > tolerance:
                log.warning(
                    "pre-trade desync: %s at seq %d, required %d (tol=%d)",
                    source,
                    current,
                    required_seq,
                    tolerance,
                )
                raise SequenceValidationError(source, required_seq, current)


class SequenceMiddleware:
    def __init__(self, registry: SequenceRegistry | None = None) -> None:
        self._registry = registry or sequence_registry

    async def wrap_neo4j_write(self, coro: Coroutine[Any, Any, Any], sequence_id: int) -> Any:
        return await self._wrap("neo4j", coro, sequence_id)

    async def wrap_questdb_write(self, coro: Coroutine[Any, Any, Any], sequence_id: int) -> Any:
        return await self._wrap("questdb", coro, sequence_id)

    async def wrap_qdrant_write(self, coro: Coroutine[Any, Any, Any], sequence_id: int) -> Any:
        return await self._wrap("qdrant", coro, sequence_id)

    async def _wrap(self, source: str, coro: Coroutine[Any, Any, Any], sequence_id: int) -> Any:
        try:
            result = await coro
        except Exception:
            log.exception("write failed for %s at seq %d", source, sequence_id)
            raise
        self._registry.register_write(source, sequence_id)
        return result


sequence_registry = SequenceRegistry()


async def enforce_pre_trade_sync(signal_sequence_id: int, tolerance: int = 10) -> bool:
    sequence_registry.validate_for_trade(signal_sequence_id, tolerance)
    return True
