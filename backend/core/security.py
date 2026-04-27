"""Minimal security layer — API key validation for production use.

The auth posture has two modes:

* **Dev / test** (``settings.AYVID_REQUIRE_AUTH = False``): if no keys
  are registered, ``verify_api_key`` is a no-op (the existing
  behaviour test suites depend on). If a key IS registered, it must
  match.
* **Production** (``settings.AYVID_REQUIRE_AUTH = True``): the
  dev-mode degrade is removed entirely. A request without a valid
  ``X-API-Key`` header is rejected with 403 regardless of whether
  keys are registered.

Production deployments MUST set ``AYVID_REQUIRE_AUTH=True`` — the
default is False so local dev and pytest runs work without any key
registration. The Terraform Cloud Run module should export this
env var to the service.
"""

from __future__ import annotations

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from shared.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_VALID_KEYS: set[str] = set()


def register_api_key(key: str) -> None:
    """Register a valid API key."""
    _VALID_KEYS.add(key)


def _require_auth() -> bool:
    """Late-bind the flag so pytest can flip it via monkeypatch."""
    return bool(getattr(settings, "AYVID_REQUIRE_AUTH", False))


async def verify_api_key(
    api_key: str | None = Security(_api_key_header),
) -> str:
    """Dependency that validates the API key header.

    * ``AYVID_REQUIRE_AUTH=True``:
        – No keys registered → 403 (mis-configuration caught loudly).
        – Key present + matches → OK.
        – Otherwise → 403.
    * ``AYVID_REQUIRE_AUTH=False`` (dev / tests):
        – No keys registered → return ``"dev-mode"`` (legacy behaviour).
        – Key present + matches → OK.
        – Otherwise → 403.
    """
    strict = _require_auth()

    if not _VALID_KEYS:
        if strict:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "AYVID_REQUIRE_AUTH=True but no API keys registered — "
                    "refusing to accept requests"
                ),
            )
        return "dev-mode"

    if api_key and api_key in _VALID_KEYS:
        return api_key

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid or missing API key",
    )


async def verify_api_key_strict(
    api_key: str | None = Security(_api_key_header),
) -> str:
    """Same contract as ``verify_api_key`` but ALWAYS enforces.

    Use on routes that must never degrade to dev-mode pass-through,
    regardless of the global ``AYVID_REQUIRE_AUTH`` flag. This is
    intentionally separate from ``verify_api_key`` so high-sensitivity
    mutation endpoints (e.g. live-mode trade execution that bypasses
    paper) can opt in without flipping auth for the whole app.
    """
    if not _VALID_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No API keys registered — strict route unreachable",
        )
    if api_key and api_key in _VALID_KEYS:
        return api_key
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid or missing API key",
    )
