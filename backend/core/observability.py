"""Production observability surface for the FastAPI backend.

Three responsibilities, one module:

  1. **Request IDs.** Every incoming HTTP request is tagged with a
     ``request_id`` (from the ``X-Request-ID`` header if present,
     otherwise generated) and the ID is echoed back on the response.
     Downstream log lines emitted during the request carry the same
     ID via a ``ContextVar``, so operators can grep a single request's
     full trajectory across the log file.

  2. **Structured logging toggle.** When ``AYVID_LOG_FORMAT=json`` is
     set, a JSON line is emitted per log record, with ``request_id``,
     ``logger``, ``level``, ``message``, and ``timestamp`` fields.
     This is the format Cloud Logging / Datadog / Loki expect. The
     default (unset) stays on the existing Rich-console format so
     local development is unaffected.

  3. **Readiness vs liveness.** The existing ``/health`` endpoint is
     a liveness probe (process alive, event loop serving requests).
     The new ``/ready`` endpoint this module registers is a
     readiness probe: it reports whether the lifespan stages finished
     (pipeline task started, kill-switch mmap bridge armed, etc.).
     Kubernetes / Cloud Run should use ``/health`` as
     ``livenessProbe`` and ``/ready`` as ``readinessProbe``.

This module is deliberately framework-specific (FastAPI + standard
``logging``) because production observability is a system-level
concern. We do NOT introduce OpenTelemetry here — that would pull in
300 MB of protobuf deps for a surface that benefits zero from them
at our current scale. When we add a second service, we revisit.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Header the load balancer / sidecar is expected to forward.
REQUEST_ID_HEADER: str = "X-Request-ID"

# Context-local request id, readable by `get_request_id()` from any
# code path — handlers, background tasks spawned from handlers,
# LLM clients. ``None`` outside an HTTP request.
_current_request_id: ContextVar[str | None] = ContextVar(
    "_current_request_id",
    default=None,
)


def get_request_id() -> str | None:
    """Return the request id for the current async context, if any."""
    return _current_request_id.get()


# ── Middleware ──────────────────────────────────────────────────────


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Tag every request with a stable id + echo it on the response.

    We prefer the incoming ``X-Request-ID`` header (when a load
    balancer has already assigned one) so a single user action stays
    grep-able across hops. Fall back to a fresh uuid4 hex otherwise.

    Also records the request duration as an ``X-Response-Time-Ms``
    header — cheap built-in performance signal for the dashboard.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        req_id = incoming if incoming else uuid.uuid4().hex
        token = _current_request_id.set(req_id)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            _current_request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = req_id
        response.headers["X-Response-Time-Ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        return response


# ── Structured JSON logging (opt-in via AYVID_LOG_FORMAT=json) ──────


class _JsonFormatter(logging.Formatter):
    """Emit one JSON line per log record, safe for Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _current_request_id.get(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Allow extra kwargs to surface — e.g.
        # ``log.info("x", extra={"trade_id": ...})``.
        for k, v in record.__dict__.items():
            if k in (
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            ):
                continue
            if k.startswith("_"):
                continue
            try:
                json.dumps(v)  # drop values we can't serialise
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, default=str)


def configure_structured_logging_if_enabled() -> bool:
    """If ``AYVID_LOG_FORMAT=json``, swap the root handler's formatter
    for the JSON formatter. Returns True when applied. Safe to call
    multiple times — idempotent.
    """
    fmt = (os.environ.get("AYVID_LOG_FORMAT") or "").strip().lower()
    if fmt != "json":
        return False
    formatter = _JsonFormatter()
    root = logging.getLogger()
    for h in list(root.handlers):
        h.setFormatter(formatter)
    return True


# ── Readiness probe ─────────────────────────────────────────────────


@dataclass
class ReadinessState:
    """Tri-state readiness signal the lifespan hook updates.

    ``starting``  — process is still in the lifespan ``yield``-preamble.
    ``ready``     — pipeline + kill switch + other hard deps are up.
    ``shutting_down`` — lifespan has entered its ``yield``-postamble;
                        new requests should be drained, not served.
    """

    stage: str = "starting"
    # Per-dependency healthy/unhealthy map, populated by lifespan.
    components: dict[str, bool] | None = None
    # Reason string attached when stage != "ready"; human-readable.
    reason: str | None = None


# Module-level singleton, mutated by the lifespan hook and read by /ready.
readiness: ReadinessState = ReadinessState()


def mark_ready(components: dict[str, bool] | None = None) -> None:
    """Called from the lifespan after all hard deps are up."""
    readiness.stage = "ready"
    readiness.components = dict(components or {})
    readiness.reason = None


def mark_shutting_down(reason: str | None = None) -> None:
    readiness.stage = "shutting_down"
    readiness.reason = reason or "lifespan teardown"


def _readiness_router() -> APIRouter:
    """Router that exposes ``/ready`` only. `/health` stays in main.py
    as the liveness probe."""
    router = APIRouter(tags=["observability"])

    @router.get("/ready")
    async def ready() -> JSONResponse:
        body: dict[str, object] = {
            "stage": readiness.stage,
            "components": readiness.components or {},
            "reason": readiness.reason,
        }
        # 200 only in the "ready" stage; 503 otherwise so orchestrator
        # health checks correctly hold traffic back during startup /
        # shutdown. Liveness probe (/health) stays 200 throughout so
        # the pod is not killed during a clean shutdown.
        status = 200 if readiness.stage == "ready" else 503
        return JSONResponse(body, status_code=status)

    return router


# ── Global exception handlers ───────────────────────────────────────


def install_exception_handlers(app: FastAPI) -> None:
    """Install consistent JSON error responses with request-id echo.

    Without these, an unhandled exception produces a stack trace on
    stderr and an opaque 500 to the caller. In production we want:

      * A structured JSON body (``error``, ``request_id``, ``detail``).
      * The ``X-Request-ID`` header on the error response so a user
        support ticket can include one string that ties back to the
        log trail.
      * Best-effort logging of the exception with that same id.
    """
    log = logging.getLogger("backend.errors")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        req_id = get_request_id() or request.headers.get(REQUEST_ID_HEADER, "")
        log.exception(
            f"unhandled exception on {request.method} {request.url.path}",
            extra={"request_id": req_id},
        )
        body = {
            "error": "internal_error",
            "detail": "An unexpected error occurred. Reference the request_id when escalating.",
            "request_id": req_id,
        }
        resp = JSONResponse(body, status_code=500)
        if req_id:
            resp.headers[REQUEST_ID_HEADER] = req_id
        return resp


# ── Public install helper ───────────────────────────────────────────


def install_observability(app: FastAPI) -> None:
    """One-shot wiring: call this from `backend.core.main`.

    Adds request-id middleware, the readiness router, global exception
    handlers, and (if enabled) the structured JSON logging formatter.
    Order matters: middleware attaches BEFORE any app.include_router
    calls in ``main.py`` so every request — including 404s — goes
    through it.
    """
    configure_structured_logging_if_enabled()
    app.add_middleware(RequestIdMiddleware)
    app.include_router(_readiness_router())
    install_exception_handlers(app)


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIdMiddleware",
    "ReadinessState",
    "readiness",
    "mark_ready",
    "mark_shutting_down",
    "get_request_id",
    "install_observability",
    "configure_structured_logging_if_enabled",
]
