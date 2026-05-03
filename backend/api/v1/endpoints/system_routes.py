"""System-level diagnostics — lets the console verify the pipeline is
actually running, in which mode, and across which asset classes.

Mounted at ``/api/v1/system``. Read-only, gated by ``verify_api_key``
the same way the live routes are.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from backend.core.security import verify_api_key
from shared.config import ExecutionMode, settings
from shared.logger import get_logger

log = get_logger("api.system")

router = APIRouter(dependencies=[Depends(verify_api_key)])

# Unauthenticated sibling for endpoints that expose ONLY build identity
# (commit SHA, container start time, runner deploy marker). The CI smoke
# test in deploy-gcp.yml has no API key — gating these would 403 every
# deploy verification (we hit exactly that on the May 3 deploy of
# 5c87819). Build SHA is the inverse of secret: its purpose is to be
# publicly verifiable so users can confirm what's running.
public_router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[4]
LIVE_DIR = PROJECT_ROOT / "live"
SIGNAL_BOOK = PROJECT_ROOT / "data_store" / "signal_book.json"

# Captured at module import so /system/build can report uptime instead
# of just "I started at this Unix epoch second".
_PROCESS_STARTED_AT = dt.datetime.now(dt.UTC)


def _count_trades_today() -> int:
    path = LIVE_DIR / "trades.jsonl"
    if not path.exists():
        return 0
    today = dt.datetime.now(dt.UTC).date().isoformat()
    count = 0
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = str(rec.get("timestamp") or rec.get("ts") or "")
                if ts.startswith(today):
                    count += 1
    except OSError:
        return 0
    return count


def _signal_book_counts() -> dict[str, int]:
    """Count signals by readiness status + open position count."""
    if not SIGNAL_BOOK.exists():
        return {"waiting": 0, "ready": 0, "expired": 0, "cancelled": 0, "total": 0}
    try:
        book = json.loads(SIGNAL_BOOK.read_text())
    except (OSError, json.JSONDecodeError):
        return {"waiting": 0, "ready": 0, "expired": 0, "cancelled": 0, "total": 0}
    if not isinstance(book, dict):
        return {"waiting": 0, "ready": 0, "expired": 0, "cancelled": 0, "total": 0}
    counts = {"waiting": 0, "ready": 0, "expired": 0, "cancelled": 0, "total": 0}
    classes: set[str] = set()
    for sig in book.values():
        if not isinstance(sig, dict):
            continue
        counts["total"] += 1
        status = str(sig.get("readiness_status", "WAITING")).upper()
        if status == "READY":
            counts["ready"] += 1
        elif status == "EXPIRED":
            counts["expired"] += 1
        elif status == "CANCELLED":
            counts["cancelled"] += 1
        else:
            counts["waiting"] += 1
        ac = str(sig.get("asset_class") or sig.get("instrument_type") or "EQUITY").upper()
        classes.add(ac)
    counts["__classes"] = sorted(classes)  # type: ignore[assignment]
    return counts


def _open_position_count() -> int:
    path = LIVE_DIR / "positions.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, dict):
        positions = data.get("positions")
        if isinstance(positions, list):
            return len(positions)
    return 0


@router.get("/pipeline")
async def get_pipeline_status() -> dict[str, Any]:
    """Snapshot of what the AYVID pipeline is doing right now.

    Combines:
      * Worker telemetry (last cycle time, signal count, orchestration mode)
      * Settings (execution mode, AUTO_EXECUTE, LIVE_TRADING_CONFIRMED)
      * Signal-book aggregates (counts by status, asset classes seen)
      * Live artefacts (open positions, trades today)

    All fields are best-effort — the endpoint never fails just because
    one source file is missing on a fresh checkout.
    """
    try:
        from data_primitives.pipeline.continuous_worker import (
            get_pipeline_telemetry,
        )
        worker_state = get_pipeline_telemetry()
    except Exception:  # pragma: no cover — defensive
        worker_state = {
            "last_pipeline_run": None,
            "signals_generated_last_cycle": 0,
            "orchestration_mode": "rule_based",
        }

    counts = _signal_book_counts()
    book_classes = counts.pop("__classes", []) if "__classes" in counts else []  # type: ignore[arg-type]

    # Asset classes the worker actively scans every 15-min cycle. The
    # signal-book snapshot might be empty on a cold start, so we union
    # what the book actually contains with the configured universe so
    # the console shows "FX is live" before the first FX signal lands.
    configured_classes = ["EQUITY", "INDEX", "FX", "FUTURE", "COMMODITY", "CRYPTO", "ETF"]
    classes = sorted(set(book_classes) | set(configured_classes))

    mode = getattr(settings, "EXECUTION_MODE", ExecutionMode.SHADOW)
    mode_value = mode.value if hasattr(mode, "value") else str(mode)

    return {
        # Signal generation
        "orchestration_mode": worker_state.get("orchestration_mode"),
        "llm_orchestration_enabled": bool(settings.LLM_ORCHESTRATION_ENABLED),
        "llm_signal_timeout_seconds": int(settings.LLM_SIGNAL_TIMEOUT_SECONDS),
        "llm_max_concurrent_signals": int(settings.LLM_MAX_CONCURRENT_SIGNALS),
        "last_pipeline_run": worker_state.get("last_pipeline_run"),
        "signals_generated_last_cycle": int(
            worker_state.get("signals_generated_last_cycle") or 0
        ),
        # Execution
        "execution_mode": mode_value,
        "auto_execute": bool(getattr(settings, "AUTO_EXECUTE", False)),
        "live_confirmed": bool(getattr(settings, "LIVE_TRADING_CONFIRMED", False)),
        "max_single_order_notional": float(
            getattr(settings, "MAX_SINGLE_ORDER_NOTIONAL", 0.0)
        ),
        # Asset classes seen in the current signal book
        "asset_classes_enabled": classes,
        # Counts
        "open_positions": _open_position_count(),
        "signals_total": counts.get("total", 0),
        "signals_waiting": counts.get("waiting", 0),
        "signals_ready": counts.get("ready", 0),
        "signals_expired": counts.get("expired", 0),
        "signals_cancelled": counts.get("cancelled", 0),
        "trades_today": _count_trades_today(),
        # Snapshot timestamp so the UI can show "as of XX seconds ago"
        "snapshot_at": dt.datetime.now(dt.UTC).isoformat(),
    }


@public_router.get("/build")
async def build_info() -> dict[str, Any]:
    """Identity of the currently-running backend container.

    The CI smoke test asserts ``build_sha`` matches ``GITHUB_SHA``
    after every deploy — that's the single tripwire that catches
    "Cloud Run rolled but VM didn't" / "we shipped but it didn't
    take" / "this revision is from yesterday" drift. The companion
    ``/runner-info`` endpoint reports the paper runner's SHA, which
    can briefly differ during a rolling deploy.
    """
    started = _PROCESS_STARTED_AT
    return {
        "build_sha": os.environ.get("BUILD_SHA", "dev"),
        "build_time": os.environ.get("BUILD_TIME", "unknown"),
        "started_at": started.isoformat(),
        "uptime_sec": (dt.datetime.now(dt.UTC) - started).total_seconds(),
    }


@public_router.get("/runner-info")
async def runner_info() -> dict[str, Any]:
    """Identity of the paper runner that's writing live/* files.

    Backed by ``live/deploy_marker.json`` which the runner writes at
    the very top of ``run()`` (before any other init).

    GCS-aware: Cloud Run and the runner are on different machines, so
    the marker the runner writes to its VM disk isn't visible to the
    Cloud Run backend's local filesystem. The runner mirrors it via
    ``_sync_path_to_gcs`` to ``gs://${AYVID_LIVE_GCS_BUCKET}/${AYVID_LIVE_GCS_PREFIX}/live/deploy_marker.json``;
    we delegate the local-then-GCS read to the same ``_read_text_any``
    helper that powers ``/api/v1/live/equity`` and friends so the
    bucket / prefix / client cache are shared.
    """
    # Import-time dependency on live_routes is fine here: it's already
    # imported by main.py for include_router, so no extra cost.
    from backend.api.v1.endpoints.live_routes import _read_text_any

    marker_path = LIVE_DIR / "deploy_marker.json"
    text = _read_text_any(marker_path)
    if text is None:
        return {
            "build_sha": "unknown",
            "started_at": None,
            "note": (
                "deploy_marker.json not present locally and not in GCS — "
                "runner has not booted yet OR is on an image without "
                "_write_deploy_marker"
            ),
        }
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return {"build_sha": "error", "error": str(exc)}


@public_router.get("/snapshot-info")
async def snapshot_info() -> dict[str, Any]:
    """Liveness heartbeat for the paper runner — public on purpose.

    The CI smoke test in ``deploy-gcp.yml`` polls this after every
    deploy to assert the runner is still writing fresh
    ``equity_curve.jsonl`` rows. Gating it behind ``verify_api_key``
    forces CI to consume a production credential, which we
    deliberately avoided when we made ``/system/build`` and
    ``/system/runner-info`` public (commit 8302e04).

    Whitelist (everything we return):
      * ``last_timestamp``    — ISO timestamp of the most recent snapshot,
                                or ``null`` if no snapshot exists yet.
      * ``snapshots_count``   — count of non-empty lines in the file.
                                Monotonic cycle counter; reveals roughly
                                how long the runner has been up. No
                                strategy signal.
      * ``checked_at``        — server time at read, for "X seconds ago"
                                math without a clock-sync round-trip.
      * ``note``              — present only on degraded reads (file
                                missing, empty, or last line corrupt).

    Forbidden (NEVER returned, even derived):
      * equity / cash / position_value / daily_pnl / cumulative_pnl
      * cumulative_return_pct / drawdown
      * mode / open_positions / positions list / signals
      * anything else from the row beyond the timestamp.

    GCS-aware via the same ``_read_text_any`` helper ``/runner-info``
    uses, so reads work whether the runner is co-located or on a
    different VM that mirrors to ``${AYVID_LIVE_GCS_BUCKET}``.
    """
    from backend.api.v1.endpoints.live_routes import _read_text_any

    eq_path = LIVE_DIR / "equity_curve.jsonl"
    text = _read_text_any(eq_path)
    now = dt.datetime.now(dt.UTC).isoformat()

    if not text:
        return {
            "last_timestamp": None,
            "snapshots_count": 0,
            "checked_at": now,
            "note": (
                "equity_curve.jsonl not present locally and not in GCS — "
                "runner has not written its first snapshot yet"
            ),
        }

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {
            "last_timestamp": None,
            "snapshots_count": 0,
            "checked_at": now,
            "note": "equity_curve.jsonl is empty",
        }

    try:
        last = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {
            "last_timestamp": None,
            "snapshots_count": len(lines),
            "checked_at": now,
            "note": f"last row is not valid JSON: {exc}",
        }

    ts = last.get("timestamp") if isinstance(last, dict) else None
    return {
        "last_timestamp": ts if isinstance(ts, str) else None,
        "snapshots_count": len(lines),
        "checked_at": now,
    }
