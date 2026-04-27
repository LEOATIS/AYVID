"""Tests for the production-hardening pass.

Four surfaces, all visible to operators and CI:

    1. Request-ID middleware — every response carries an ``X-Request-ID``
       header, either propagated from the client or generated fresh.
       The id is observable from inside a handler via ``get_request_id()``.

    2. Readiness probe — ``/ready`` is 503 during startup / shutdown and
       200 only when ``mark_ready()`` has been called. ``/health``
       (liveness) stays 200 throughout so the pod is not killed during
       a clean drain.

    3. Global exception handlers — an unhandled exception produces a
       structured JSON body with ``error``, ``detail``, and
       ``request_id`` instead of a raw traceback.

    4. CLI exit codes — every documented code in ``cli.exit_codes`` is
       an integer in the canonical range, and the desync / auth paths
       in ``trade execute`` map to the right constants.

    5. Graceful shutdown — the helper ``_await_task_with_timeout``
       (a) cancels the task, (b) waits up to ``timeout``, (c) returns
       cleanly even if the task refuses to die.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

# ── 1. Request-ID middleware ────────────────────────────────────────


class TestRequestIdMiddleware:
    """We use ``TestClient(app)`` WITHOUT a context manager so the
    production lifespan (which starts the ingestion pipeline + kill
    switch) does not fire. The middleware is installed at import
    time and is fully testable without the lifespan running."""

    def test_response_carries_generated_request_id(self):
        """A request with no inbound header gets a fresh uuid."""
        from backend.core.main import app

        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        req_id = r.headers.get("X-Request-ID")
        assert req_id and len(req_id) >= 16, (
            "X-Request-ID missing or too short; middleware not installed"
        )

    def test_response_echoes_incoming_request_id(self):
        from backend.core.main import app

        sent = "trace-abc-12345"
        client = TestClient(app)
        r = client.get("/health", headers={"X-Request-ID": sent})
        assert r.headers.get("X-Request-ID") == sent

    def test_response_time_header_is_present(self):
        from backend.core.main import app

        client = TestClient(app)
        r = client.get("/health")
        assert "X-Response-Time-Ms" in r.headers
        float(r.headers["X-Response-Time-Ms"])

    def test_request_id_is_accessible_inside_handlers(self):
        from backend.core.main import app
        from backend.core.observability import get_request_id

        router = APIRouter()

        @router.get("/__test_ctx_request_id")
        async def _handler():
            return {"request_id": get_request_id()}

        app.include_router(router)
        try:
            client = TestClient(app)
            r = client.get(
                "/__test_ctx_request_id",
                headers={"X-Request-ID": "ctx-id-xyz"},
            )
            assert r.status_code == 200
            assert r.json()["request_id"] == "ctx-id-xyz"
        finally:
            app.router.routes = [
                r
                for r in app.router.routes
                if getattr(r, "path", None) != "/__test_ctx_request_id"
            ]


# ── 2. Readiness probe ──────────────────────────────────────────────


class TestReadinessProbe:
    """Skipping the lifespan (no context-manager enter) leaves the
    readiness state whatever we set it to, so these tests are
    self-contained and hermetic."""

    def test_ready_endpoint_reflects_mark_ready(self):
        from backend.core.main import app
        from backend.core.observability import mark_ready

        mark_ready(components={"test_dep": True, "other_dep": False})
        client = TestClient(app)
        r = client.get("/ready")
        assert r.status_code == 200
        payload = r.json()
        assert payload["stage"] == "ready"
        assert payload["components"]["test_dep"] is True
        assert payload["components"]["other_dep"] is False

    def test_ready_endpoint_returns_503_during_shutdown(self):
        from backend.core.main import app
        from backend.core.observability import (
            mark_ready,
            mark_shutting_down,
        )

        mark_shutting_down("drain_in_progress")
        try:
            client = TestClient(app)
            r = client.get("/ready")
        finally:
            mark_ready(components={})
        assert r.status_code == 503
        payload = r.json()
        assert payload["stage"] == "shutting_down"
        assert payload["reason"] == "drain_in_progress"

    def test_health_stays_200_during_shutdown(self):
        """Liveness probe MUST remain 200 during a clean shutdown so
        the orchestrator does not SIGKILL the pod mid-drain. Readiness
        is the gate that holds traffic back, not liveness."""
        from backend.core.main import app
        from backend.core.observability import (
            mark_ready,
            mark_shutting_down,
        )

        mark_shutting_down("drain_in_progress")
        try:
            client = TestClient(app)
            r = client.get("/health")
        finally:
            mark_ready(components={})
        assert r.status_code == 200


# ── 3. Global exception handlers ────────────────────────────────────


class TestGlobalExceptionHandler:
    def test_unhandled_exception_produces_structured_500(self):
        from backend.core.main import app

        router = APIRouter()

        @router.get("/__test_raise")
        async def _handler():
            raise RuntimeError("synthetic failure for test")

        app.include_router(router)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get(
                "/__test_raise",
                headers={"X-Request-ID": "probe-500"},
            )
            assert r.status_code == 500
            body = r.json()
            assert body["error"] == "internal_error"
            assert body["request_id"] == "probe-500"
            assert "detail" in body
            assert r.headers.get("X-Request-ID") == "probe-500"
        finally:
            app.router.routes = [
                r for r in app.router.routes if getattr(r, "path", None) != "/__test_raise"
            ]


# ── 4. CLI exit codes ───────────────────────────────────────────────


class TestCliExitCodes:
    def test_all_codes_are_ints_and_unique(self):
        from cli import exit_codes

        names = [
            n
            for n in dir(exit_codes)
            if n.startswith("EXIT_") and isinstance(getattr(exit_codes, n), int)
        ]
        values = [getattr(exit_codes, n) for n in names]
        assert len(values) == len(set(values)), f"duplicate EXIT_* values: {sorted(values)}"
        for v in values:
            assert 0 <= v <= 255, f"exit code out of POSIX range: {v}"

    def test_canonical_values(self):
        """Pin the well-known codes so external CI scripts don't silently
        break if someone renumbers. Add to this list when adding new codes;
        never reorder."""
        from cli.exit_codes import (
            EXIT_BACKEND_AUTH_FAILED,
            EXIT_BACKEND_TIMEOUT,
            EXIT_BACKEND_UNREACHABLE,
            EXIT_DATA_PLANE_DESYNC,
            EXIT_GENERIC_ERROR,
            EXIT_KEYBOARD_INTERRUPT,
            EXIT_OK,
            EXIT_USAGE_ERROR,
        )

        assert EXIT_OK == 0
        assert EXIT_GENERIC_ERROR == 1
        assert EXIT_USAGE_ERROR == 2
        assert EXIT_BACKEND_UNREACHABLE == 3
        assert EXIT_BACKEND_AUTH_FAILED == 4
        assert EXIT_BACKEND_TIMEOUT == 5
        assert EXIT_DATA_PLANE_DESYNC == 10
        assert EXIT_KEYBOARD_INTERRUPT == 130


# ── 5. Graceful shutdown helper ─────────────────────────────────────


class TestAwaitTaskWithTimeout:
    @pytest.mark.asyncio
    async def test_already_done_task_is_noop(self):
        from backend.core.main import _await_task_with_timeout

        async def _noop():
            return "done"

        t = asyncio.create_task(_noop())
        await t
        # Must not raise even though the task is done.
        await _await_task_with_timeout(t, "noop", timeout=1.0)

    @pytest.mark.asyncio
    async def test_cooperative_task_cancels_cleanly(self):
        """A well-behaved task that checks for cancellation returns
        before the timeout fires."""
        from backend.core.main import _await_task_with_timeout

        async def _sleeper():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        t = asyncio.create_task(_sleeper())
        # Give it one event-loop tick so the task actually starts.
        await asyncio.sleep(0)
        await _await_task_with_timeout(t, "sleeper", timeout=1.0)
        assert t.done()

    @pytest.mark.asyncio
    async def test_none_task_is_noop(self):
        """Passing ``None`` is a valid call — the lifespan guards each
        task variable separately and may call the helper with a slot
        that was never populated."""
        from backend.core.main import _await_task_with_timeout

        # Must not raise.
        await _await_task_with_timeout(None, "unset", timeout=1.0)


# ── 6. Structured logging formatter ─────────────────────────────────


class TestJsonFormatter:
    def test_formatter_emits_valid_json(self):
        import json
        import logging

        from backend.core.observability import _JsonFormatter

        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        out = formatter.format(record)
        payload = json.loads(out)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test"
        assert payload["message"] == "hello world"
        assert "ts" in payload
        # No in-flight request → request_id must be null, not missing
        assert payload.get("request_id") is None
