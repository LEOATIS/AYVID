"""Server-Sent Events streamer for live graph and portfolio updates.

Uses SSE (not WebSockets) for unidirectional server→client streaming,
as specified in the architecture doc for lighter concurrency footprint.

Implements broadcast semantics: every connected client receives every
event via per-client queues managed by a subscriber registry.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from shared.logger import get_logger

log = get_logger("api.sse")

router = APIRouter()

_subscribers: set[asyncio.Queue[dict]] = set()


async def publish_event(event_type: str, data: dict) -> None:
    """Broadcast an event to all connected SSE subscribers."""
    event = {"type": event_type, "data": data}
    dead: list[asyncio.Queue[dict]] = []
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
    for q in dead:
        _subscribers.discard(q)


async def _event_generator() -> AsyncGenerator[str, None]:
    """Generate SSE-formatted events for a single client connection."""
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
    _subscribers.add(q)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
            except asyncio.TimeoutError:
                yield f"event: heartbeat\ndata: {json.dumps({'status': 'alive'})}\n\n"
    finally:
        _subscribers.discard(q)


@router.get("/stream")
async def sse_stream():
    """SSE endpoint for live updates."""
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
