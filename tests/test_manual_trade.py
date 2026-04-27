"""Tests for the P0-B manual-trade routing fix.

Three layers need to hold together for P0-B to close:
  1. ``_allocate_manual_sequence_id`` returns a seq that makes the
     autopilot sync gates meaningful (max of registered source seqs).
  2. ``POST /api/v1/trade/manual`` routes through ``execute_manual_signal``
     which in turn pipes the allocated seq into ``execute_signal`` so
     ``validate_graph_freshness`` + ``enforce_pre_trade_sync`` are
     actually exercised.
  3. The CLI ``ayvid trade execute`` no longer calls the orchestrator
     in-process — it POSTs to the backend endpoint via httpx.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

_TEST_API_KEY = "test-manual-key"

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def fresh_registry():
    """Reset the shared sequence registry between tests so the allocator
    is not contaminated by state from a previous run."""
    from shared.sequence_validator import sequence_registry

    with sequence_registry._lock:
        prev = dict(sequence_registry._sequences)
        sequence_registry._sequences = {k: 0 for k in sequence_registry._sequences}
    yield sequence_registry
    with sequence_registry._lock:
        sequence_registry._sequences = prev


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """Keep manual-route tests isolated from global auth mutations.

    Other tests may register API keys in-process, which would make
    unauthenticated requests return 403 even in dev-mode. These tests
    exercise routing/sequence behavior, so force the legacy dev auth
    baseline and clear registered keys for each test.
    """
    from backend.core import security as sec
    from shared.config import settings

    original_require_auth = getattr(settings, "AYVID_REQUIRE_AUTH", False)
    original_keys = set(sec._VALID_KEYS)
    try:
        object.__setattr__(settings, "AYVID_REQUIRE_AUTH", False)
        sec._VALID_KEYS.clear()
        sec.register_api_key(_TEST_API_KEY)
        yield
    finally:
        sec._VALID_KEYS.clear()
        sec._VALID_KEYS.update(original_keys)
        object.__setattr__(settings, "AYVID_REQUIRE_AUTH", original_require_auth)


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": _TEST_API_KEY}


# ── Layer 1: the allocator ───────────────────────────────────────────


class TestAllocateManualSequenceId:
    def test_empty_registry_returns_zero(self, fresh_registry):
        """Cold start — no ingestion yet. The allocator returns 0 and
        the legacy short-circuit in ``validate_graph_freshness`` lets
        the trade through. This is an intentional UX concession for
        fresh operators firing up the system for the first time."""
        from backend.services.orchestrator import _allocate_manual_sequence_id

        assert _allocate_manual_sequence_id() == 0

    def test_returns_max_of_registered_source_seqs(self, fresh_registry):
        """Once ingestion has started, the allocator tracks the fastest
        source. The sync check then compares every other source against
        this max — slow sources behind by > tolerance raise."""
        from backend.services.orchestrator import _allocate_manual_sequence_id

        fresh_registry.register_write("neo4j", 42)
        fresh_registry.register_write("questdb", 7)
        fresh_registry.register_write("qdrant", 50)

        assert _allocate_manual_sequence_id() == 50


# ── Layer 2: the /manual endpoint ────────────────────────────────────


def _build_client():
    """Construct a TestClient against the live app.

    We mount the router as-is — no auth set up — so the `dev-mode`
    branch of ``verify_api_key`` short-circuits and we're testing the
    trade-routing contract, not the auth layer.
    """
    from backend.core.main import app

    return TestClient(app)


class TestManualEndpoint:
    def test_manual_endpoint_returns_200_on_happy_path(
        self,
        fresh_registry,
    ):
        """The happy path: registry empty (seq allocator returns 0 →
        legacy bypass), execute_signal completes successfully, endpoint
        mirrors the signal dict back as JSON."""
        import backend.services.orchestrator as orch
        from shared.types import SignalStatus

        async def fake_execute_signal(signal, sequence_id=0):
            signal.status = SignalStatus.FILLED
            return signal

        with patch.object(
            orch,
            "execute_signal",
            side_effect=fake_execute_signal,
        ) as mock_exec:
            with _build_client() as client:
                response = client.post(
                    "/api/v1/trade/manual",
                    json={"ticker": "AAPL", "action": "LONG", "confidence": 0.8},
                    headers=_auth_headers(),
                )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ticker"] == "AAPL"
        assert body["direction"] == "LONG"
        assert body["status"] == "FILLED"
        assert body["intent_id"].startswith("manual_")

        # Verify the endpoint DID pipe a sequence_id into execute_signal
        # (empty registry → seq=0, but the kwarg must be present).
        mock_exec.assert_awaited_once()
        _, kwargs = mock_exec.call_args
        assert "sequence_id" in kwargs, (
            "execute_manual_signal dropped the allocated sequence_id — "
            "P0-B regression: the endpoint is running without the "
            "freshness gate"
        )

    def test_manual_endpoint_passes_allocated_seq_through(
        self,
        fresh_registry,
    ):
        """With a populated registry, the allocator returns max(seqs)
        and the endpoint must forward that to execute_signal so the
        sync gate is actually checked."""
        import backend.services.orchestrator as orch
        from shared.types import SignalStatus

        fresh_registry.register_write("neo4j", 99)
        fresh_registry.register_write("questdb", 42)

        async def fake_execute_signal(signal, sequence_id=0):
            signal.status = SignalStatus.FILLED
            return signal

        with patch.object(
            orch,
            "execute_signal",
            side_effect=fake_execute_signal,
        ) as mock_exec:
            with _build_client() as client:
                response = client.post(
                    "/api/v1/trade/manual",
                    json={"ticker": "NVDA", "action": "SHORT", "confidence": 0.75},
                    headers=_auth_headers(),
                )

        assert response.status_code == 200
        _, kwargs = mock_exec.call_args
        assert kwargs["sequence_id"] == 99, (
            f"allocator forwarded sequence_id={kwargs['sequence_id']}; "
            "expected 99 (max of registered sources)"
        )

    def test_manual_endpoint_returns_409_on_sequence_desync(
        self,
        fresh_registry,
    ):
        """When the registry is diverged (slow source behind > tolerance),
        ``enforce_pre_trade_sync`` raises. The endpoint should surface
        that as HTTP 409 Conflict, not 500 Internal Error — the CLI
        relies on this to print a specific operator message."""
        import backend.services.orchestrator as orch
        from shared.exceptions import SequenceValidationError

        async def failing_execute(signal, sequence_id=0):
            raise SequenceValidationError("questdb", 200, 50)

        with patch.object(orch, "execute_signal", side_effect=failing_execute):
            with _build_client() as client:
                response = client.post(
                    "/api/v1/trade/manual",
                    json={"ticker": "MSFT", "action": "LONG"},
                    headers=_auth_headers(),
                )

        assert response.status_code == 409, response.text
        assert (
            "questdb" in response.json()["detail"].lower()
            or "sequence" in response.json()["detail"].lower()
        )

    def test_manual_endpoint_returns_422_on_bad_action(self, fresh_registry):
        """Invalid action string → ValueError inside TradeAction enum →
        endpoint returns 422 Unprocessable Entity."""
        with _build_client() as client:
            response = client.post(
                "/api/v1/trade/manual",
                json={"ticker": "AAPL", "action": "WIGGLE"},
                headers=_auth_headers(),
            )
        assert response.status_code == 422

    def test_strict_auth_rejects_unauthenticated(self, fresh_registry):
        """P1-15: when AYVID_REQUIRE_AUTH is True and no API keys are
        registered, every request must be rejected with 403 — the
        dev-mode degrade is disabled. Verifies that the auth tighten
        is not merely documentary.
        """
        from shared.config import settings

        original = getattr(settings, "AYVID_REQUIRE_AUTH", False)
        try:
            object.__setattr__(settings, "AYVID_REQUIRE_AUTH", True)
            with _build_client() as client:
                response = client.post(
                    "/api/v1/trade/manual",
                    json={"ticker": "AAPL", "action": "LONG"},
                )
            assert response.status_code == 403, (
                f"strict auth must reject unauthenticated request, "
                f"got {response.status_code}: {response.text}"
            )
        finally:
            object.__setattr__(settings, "AYVID_REQUIRE_AUTH", original)

    def test_legacy_execute_route_is_equivalent(self, fresh_registry):
        """Backward compatibility: the old /execute route must behave
        identically to /manual so existing integrations keep working."""
        import backend.services.orchestrator as orch
        from shared.types import SignalStatus

        async def fake_execute_signal(signal, sequence_id=0):
            signal.status = SignalStatus.FILLED
            return signal

        with patch.object(orch, "execute_signal", side_effect=fake_execute_signal):
            with _build_client() as client:
                legacy = client.post(
                    "/api/v1/trade/execute",
                    json={"ticker": "TSLA", "action": "LONG"},
                    headers=_auth_headers(),
                )
                new = client.post(
                    "/api/v1/trade/manual",
                    json={"ticker": "TSLA", "action": "LONG"},
                    headers=_auth_headers(),
                )

        assert legacy.status_code == 200
        assert new.status_code == 200
        # intent_id / timestamp will differ, but the core contract matches
        assert legacy.json()["ticker"] == new.json()["ticker"]
        assert legacy.json()["direction"] == new.json()["direction"]


# ── Layer 3: CLI → backend round-trip ────────────────────────────────


class TestCliRoutesThroughBackend:
    def test_cli_execute_posts_to_manual_endpoint(self, fresh_registry):
        """The P0-B acceptance: `ayvid trade execute` must submit to the
        backend over HTTP rather than calling the in-process helper.
        We intercept httpx.Client.post and verify the exact URL + body.
        """
        from typer.testing import CliRunner

        from cli.commands.trade import trade_app

        captured: dict = {}

        class _FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "ticker": "AAPL",
                    "direction": "LONG",
                    "status": "FILLED",
                    "intent_id": "manual_deadbeef",
                }

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def post(self, url, *, headers=None, json=None):
                captured["url"] = url
                captured["headers"] = headers or {}
                captured["json"] = json
                return _FakeResponse()

        runner = CliRunner()
        with patch.object(httpx, "Client", _FakeClient):
            result = runner.invoke(
                trade_app,
                [
                    "execute",
                    "AAPL",
                    "LONG",
                    "--confidence",
                    "0.8",
                    "--backend-url",
                    "http://127.0.0.1:8001",
                ],
            )

        assert result.exit_code == 0, result.output

        assert captured["url"].endswith("/api/v1/trade/manual"), (
            f"CLI posted to {captured['url']} — must target /manual "
            "so the backend's freshness gate fires"
        )
        assert captured["json"] == {
            "ticker": "AAPL",
            "action": "LONG",
            "confidence": 0.8,
            "alpha_horizon": "3rd_order",
        }

    def test_cli_execute_surfaces_connection_error_as_nonzero_exit(self, fresh_registry):
        """If the backend isn't running the CLI must exit non-zero with
        a helpful message, not silently swallow the failure."""
        from typer.testing import CliRunner

        from cli.commands.trade import trade_app

        class _RaisingClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def post(self, url, *, headers=None, json=None):
                raise httpx.ConnectError("connection refused")

        runner = CliRunner()
        with patch.object(httpx, "Client", _RaisingClient):
            result = runner.invoke(
                trade_app,
                ["execute", "AAPL", "LONG", "--backend-url", "http://127.0.0.1:8001"],
            )

        assert result.exit_code != 0
        assert "Cannot reach backend" in result.output

    def test_cli_execute_surfaces_409_as_desync_message(
        self,
        fresh_registry,
    ):
        """HTTP 409 from the backend is the specific 'data desync'
        contract — the CLI must render it distinctly (not as a generic
        500) and exit with the desync code 5."""
        from typer.testing import CliRunner

        from cli.commands.trade import trade_app

        class _DesyncResponse:
            status_code = 409
            text = '{"detail": "neo4j at seq 40, required 200"}'

            def json(self):
                return {"detail": "neo4j at seq 40, required 200"}

        class _DesyncClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def post(self, url, *, headers=None, json=None):
                return _DesyncResponse()

        runner = CliRunner()
        with patch.object(httpx, "Client", _DesyncClient):
            result = runner.invoke(
                trade_app,
                ["execute", "AAPL", "LONG", "--backend-url", "http://127.0.0.1:8001"],
            )

        from cli.exit_codes import EXIT_DATA_PLANE_DESYNC

        assert result.exit_code == EXIT_DATA_PLANE_DESYNC
        assert "desync" in result.output.lower()
