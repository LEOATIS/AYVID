"""Minimal security layer — API key validation for production use."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_VALID_KEYS: set[str] = set()


def register_api_key(key: str) -> None:
    """Register a valid API key."""
    _VALID_KEYS.add(key)


async def verify_api_key(
    api_key: str | None = Security(_api_key_header),
) -> str:
    """Dependency that validates the API key header.

    Returns early (no-op) if no keys are registered (dev mode).
    """
    if not _VALID_KEYS:
        return "dev-mode"

    if api_key and api_key in _VALID_KEYS:
        return api_key

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid or missing API key",
    )
