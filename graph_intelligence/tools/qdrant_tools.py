"""Qdrant vector store operations for semantic event storage and retrieval.

Stores news events as embeddings for semantic search, while the chronological
timeline is maintained separately in the LangGraph state.
"""
from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models
from openai import AsyncOpenAI

from shared.config import settings
from shared.exceptions import QdrantConnectionError
from shared.logger import get_logger
from shared.types import CausalEvent

log = get_logger("graph.qdrant")

_client: AsyncQdrantClient | None = None
_openai: AsyncOpenAI | None = None
_EMBEDDING_DIM = 1536


async def get_client() -> AsyncQdrantClient:
    """Get or create the async Qdrant client."""
    global _client
    if _client is None:
        try:
            _client = AsyncQdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY or None,
                timeout=10.0,
            )
        except Exception as exc:
            raise QdrantConnectionError(f"Cannot connect to Qdrant: {exc}") from exc
    return _client


def _get_openai() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai


async def ensure_collection() -> None:
    """Create the events collection if it doesn't exist."""
    client = await get_client()
    collections = await client.get_collections()
    names = [c.name for c in collections.collections]

    if settings.QDRANT_COLLECTION not in names:
        await client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=models.VectorParams(
                size=_EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        log.info(f"Created Qdrant collection: {settings.QDRANT_COLLECTION}")


async def embed_text(text: str) -> list[float]:
    """Generate an embedding vector using OpenAI."""
    client = _get_openai()
    response = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


async def store_event(event: CausalEvent) -> str:
    """Store a causal event with its embedding in Qdrant."""
    client = await get_client()
    await ensure_collection()

    text = f"{event.source_node} {event.event_type.value}: {event.compressed_summary}"
    vector = await embed_text(text)
    point_id = str(uuid.uuid4())

    await client.upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=[
            models.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "timestamp": event.timestamp.isoformat(),
                    "source_node": event.source_node,
                    "event_type": event.event_type.value,
                    "summary": event.compressed_summary,
                    "confidence": event.confidence,
                    "sequence_id": event.sequence_id,
                },
            )
        ],
    )
    return point_id


async def search_similar_events(
    query: str,
    limit: int = 10,
    ticker_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Search for semantically similar events."""
    client = await get_client()
    vector = await embed_text(query)

    filter_condition = None
    if ticker_filter:
        filter_condition = models.Filter(
            must=[
                models.FieldCondition(
                    key="source_node",
                    match=models.MatchValue(value=ticker_filter),
                )
            ]
        )

    results = await client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=vector,
        limit=limit,
        query_filter=filter_condition,
    )

    return [
        {
            "id": str(r.id),
            "score": r.score,
            **r.payload,
        }
        for r in results.points
    ]


async def get_collection_stats() -> dict[str, Any]:
    """Return collection-level statistics."""
    try:
        client = await get_client()
        info = await client.get_collection(settings.QDRANT_COLLECTION)
        return {
            "points_count": info.points_count,
            "vectors_count": info.vectors_count,
            "status": info.status.value,
        }
    except Exception:
        return {"points_count": 0, "vectors_count": 0, "status": "unavailable"}


async def verify_connection() -> bool:
    """Test Qdrant connectivity."""
    try:
        client = await get_client()
        await client.get_collections()
        return True
    except Exception as exc:
        log.error(f"Qdrant connection failed: {exc}")
        return False
