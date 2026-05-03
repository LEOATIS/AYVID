"""Tests for the public ``/api/v1/system/*`` endpoints.

These endpoints sit on ``public_router`` (no ``verify_api_key``) so the
CI smoke test in ``deploy-gcp.yml`` can poll them without a credential.
That is a deliberate, reviewed exception to the project's "all live
data is gated" rule (see ``backend/api/v1/endpoints/live_routes.py``
docstring) — so the test file's job is twofold:

  1. The endpoints respond at all, with the expected shape.
  2. They NEVER leak strategy-bearing fields (equity, P&L, positions,
     mode, signals). If a future edit accidentally widens the
     whitelist, these tests fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Fields a competitor could use to reconstruct strategy state — the
# whole reason ``/live/*`` is gated. None of them belong in any public
# system endpoint, period.
FORBIDDEN_FIELDS = frozenset(
    {
        "equity",
        "cash",
        "position_value",
        "open_positions",
        "positions",
        "daily_pnl",
        "cumulative_pnl",
        "cumulative_return_pct",
        "total_return_pct",
        "drawdown",
        "drawdown_from_peak_pct",
        "peak_equity",
        "initial_equity",
        "final_equity",
        "mode",
        "signals",
        "snapshots",
        "summary",
    }
)


def _assert_no_forbidden_fields(payload: dict) -> None:
    leaked = set(payload.keys()) & FORBIDDEN_FIELDS
    assert not leaked, (
        f"Public endpoint leaked strategy fields: {sorted(leaked)}. "
        f"These belong behind verify_api_key on /api/v1/live/*."
    )


@pytest.fixture
def app_client():
    from backend.core.main import app

    return TestClient(app)


@pytest.fixture
def isolated_live_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``system_routes.LIVE_DIR`` at a fresh tmp dir so each test
    controls exactly what ``equity_curve.jsonl`` looks like."""
    from backend.api.v1.endpoints import system_routes

    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(system_routes, "LIVE_DIR", live)
    return live


class TestSnapshotInfo:
    def test_returns_only_whitelisted_fields_when_file_present(
        self, app_client: TestClient, isolated_live_dir: Path
    ):
        eq = isolated_live_dir / "equity_curve.jsonl"
        # Realistic row with every forbidden field — the endpoint must
        # extract only the timestamp.
        rows = [
            {
                "timestamp": "2026-05-03T11:00:00+00:00",
                "mode": "shadow",
                "equity": 1000000.0,
                "cash": 500000.0,
                "position_value": 500000.0,
                "open_positions": 7,
                "daily_pnl": 1234.5,
                "cumulative_pnl": 9876.5,
                "cumulative_return_pct": 0.987,
                "cycle": 42,
            },
            {
                "timestamp": "2026-05-03T12:00:00+00:00",
                "mode": "shadow",
                "equity": 1001000.0,
                "cash": 500000.0,
                "position_value": 501000.0,
                "open_positions": 7,
                "daily_pnl": 1000.0,
                "cumulative_pnl": 10876.5,
                "cumulative_return_pct": 1.087,
                "cycle": 43,
            },
        ]
        eq.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        r = app_client.get("/api/v1/system/snapshot-info")
        assert r.status_code == 200
        body = r.json()

        assert body["last_timestamp"] == "2026-05-03T12:00:00+00:00"
        assert body["snapshots_count"] == 2
        assert isinstance(body["checked_at"], str)
        _assert_no_forbidden_fields(body)
        # Defense in depth — serialise and grep for forbidden numeric
        # values that might sneak in via a future field rename.
        as_text = json.dumps(body)
        for forbidden in ("equity", "cash", "pnl", "drawdown", "mode"):
            assert forbidden not in as_text.lower(), (
                f"Public response contains forbidden token {forbidden!r}"
            )

    def test_missing_file_returns_null_with_note(
        self, app_client: TestClient, isolated_live_dir: Path
    ):
        # No equity_curve.jsonl written.
        r = app_client.get("/api/v1/system/snapshot-info")
        assert r.status_code == 200
        body = r.json()
        assert body["last_timestamp"] is None
        assert body["snapshots_count"] == 0
        assert "note" in body
        _assert_no_forbidden_fields(body)

    def test_empty_file_returns_null_with_note(
        self, app_client: TestClient, isolated_live_dir: Path
    ):
        (isolated_live_dir / "equity_curve.jsonl").write_text("")
        r = app_client.get("/api/v1/system/snapshot-info")
        assert r.status_code == 200
        body = r.json()
        assert body["last_timestamp"] is None
        assert body["snapshots_count"] == 0
        assert "note" in body
        _assert_no_forbidden_fields(body)

    def test_malformed_last_line_returns_null_with_note(
        self, app_client: TestClient, isolated_live_dir: Path
    ):
        eq = isolated_live_dir / "equity_curve.jsonl"
        eq.write_text(
            json.dumps({"timestamp": "2026-05-03T11:00:00+00:00", "cycle": 1})
            + "\n"
            + "{not valid json\n"
        )
        r = app_client.get("/api/v1/system/snapshot-info")
        assert r.status_code == 200
        body = r.json()
        assert body["last_timestamp"] is None
        # Both lines counted (count is line-based, not parse-based) so
        # operators can still see the runner is writing.
        assert body["snapshots_count"] == 2
        assert "note" in body
        _assert_no_forbidden_fields(body)

    def test_endpoint_requires_no_api_key(
        self, app_client: TestClient, isolated_live_dir: Path
    ):
        """Sibling check: the path is on ``public_router``, so a request
        with no ``X-API-Key`` header must NOT 403. (This is the actual
        bug commit 8302e04 / this change exists to prevent.)"""
        r = app_client.get("/api/v1/system/snapshot-info")
        assert r.status_code != 403, (
            "snapshot-info regressed onto an authed router; CI smoke test "
            "will start failing with 403 again."
        )


class TestPublicRouterMembership:
    """Belt-and-suspenders: confirm the routes we expect to be public
    are actually mounted on ``public_router`` (no
    ``verify_api_key`` dependency), and confirm we did NOT
    accidentally publish anything else."""

    def test_public_router_paths_are_exactly_the_expected_set(self):
        from backend.api.v1.endpoints.system_routes import public_router

        paths = {route.path for route in public_router.routes}
        assert paths == {"/build", "/runner-info", "/snapshot-info"}, (
            f"public_router membership changed: {sorted(paths)}. "
            f"Adding a public endpoint requires a security review — "
            f"update this assertion deliberately, not reflexively."
        )

    def test_live_router_still_requires_api_key(self):
        """Sanity: the existing /live/* routes must remain gated. If
        someone moves /live/equity to ``public_router`` to "fix" CI,
        this test catches it."""
        from backend.api.v1.endpoints.live_routes import router as live_router

        # Router-level dependency (set on the APIRouter itself).
        dep_callables = [d.dependency for d in live_router.dependencies]
        from backend.core.security import verify_api_key

        assert verify_api_key in dep_callables, (
            "live_router lost its verify_api_key dependency — every "
            "/api/v1/live/* route is now public, leaking strategy data."
        )
