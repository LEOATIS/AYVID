"""In-memory agent activity event bus for SSE streaming.

Tracks real-time agent activity (Strategist, Mapper, Auditor, Supervisor)
and exposes it as a simple async generator for the SSE endpoint.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class AgentEvent:
    """A single agent activity event."""

    agent: str  # Strategist, Mapper, Auditor, Supervisor, Executor, KillSwitch
    action: str  # e.g. "analyzing", "debating", "executing", "blocking"
    detail: str  # human-readable detail
    timestamp: float = field(default_factory=time.time)
    asset_class: str = ""  # EQUITY, FX, COMMODITY, CRYPTO, ETF, BOND
    ticker: str = ""

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "action": self.action,
            "detail": self.detail,
            "timestamp": self.timestamp,
            "asset_class": self.asset_class,
            "ticker": self.ticker,
        }


class AgentEventBus:
    """Thread-safe event bus with bounded history and async subscribers."""

    def __init__(self, max_history: int = 200) -> None:
        self._history: deque[AgentEvent] = deque(maxlen=max_history)
        self._subscribers: list[asyncio.Queue[AgentEvent]] = []

    def emit(self, event: AgentEvent) -> None:
        """Publish an event to all subscribers and history."""
        self._history.append(event)
        dead: list[asyncio.Queue[AgentEvent]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def recent(self, n: int = 20) -> list[AgentEvent]:
        """Return the last N events."""
        items = list(self._history)
        return items[-n:]

    async def subscribe(self) -> AsyncIterator[AgentEvent]:
        """Async generator that yields events as they arrive."""
        q: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=50)
        self._subscribers.append(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)


# Module-level singleton
_bus: AgentEventBus | None = None


def get_event_bus() -> AgentEventBus:
    global _bus
    if _bus is None:
        _bus = AgentEventBus()
    return _bus
