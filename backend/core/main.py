"""FastAPI application — the nervous system that routes requests.

No heavy compute here. Endpoints hand off to services immediately.

Production posture (senior-engineer conventions, all enforced in code):

* **Lifespan** runs ingestion + kill switch inside an ``asyncio.Task``
  set and cancels them on shutdown with a bounded wait + log so a stuck
  coroutine can never wedge a pod restart.
* **Observability** (``backend.core.observability``) is installed before
  any route — request-id middleware, structured-JSON logging toggle,
  ``/ready`` readiness probe, global exception handlers.
* **Graceful shutdown** awaits in-flight background tasks with a
  ``SHUTDOWN_TIMEOUT_SECONDS`` deadline; any that miss the deadline are
  logged loudly and the process still exits cleanly so the orchestrator
  can schedule a new pod.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.observability import (
    install_observability,
    mark_ready,
    mark_shutting_down,
)
from shared.config import settings
from shared.logger import get_logger

log = get_logger("backend.main")

# Bounded lifespan-shutdown wait. 10 s is the SIGTERM grace window
# most container runtimes give us (GKE default 30 s, Cloud Run 10 s
# pre-terminate). We await hard deps for SHUTDOWN_TIMEOUT_SECONDS and
# then let the process exit regardless — better to take a clean shutdown
# with one torn task than an SIGKILL-9 with all of them.
SHUTDOWN_TIMEOUT_SECONDS: float = 8.0

_pipeline_task: asyncio.Task | None = None
_risk_writer_task: asyncio.Task | None = None


def _register_api_keys() -> None:
    """Register configured API keys for request authentication."""
    from backend.core.security import register_api_key

    api_key = getattr(settings, "API_KEY", None)
    if api_key:
        register_api_key(api_key)
        log.info("API key authentication enabled")
    else:
        log.info("No API_KEY configured — running in dev mode (no auth)")


def _log_trade_routing_banner() -> None:
    """Print exactly where the next order will go. Read this first on every restart."""
    acct = (settings.IB_ACCOUNT or "").strip() or "default"
    is_paper_acct = acct.upper().startswith("DU")
    if settings.PAPER_TRADING:
        log.info(
            f"[bold yellow]ORDER ROUTING:[/bold yellow] stub-only "
            f"(PAPER_TRADING=true). Orders will NOT reach IB. "
            f"auto_execute={settings.AUTO_EXECUTE}"
        )
    elif is_paper_acct:
        log.info(
            f"[bold green]ORDER ROUTING:[/bold green] IB Paper "
            f"({settings.IB_HOST}:{settings.IB_PORT}, account={acct}). "
            f"Zero real-money risk. auto_execute={settings.AUTO_EXECUTE}"
        )
    else:
        log.warning(
            f"[bold red]ORDER ROUTING:[/bold red] IB LIVE "
            f"({settings.IB_HOST}:{settings.IB_PORT}, account={acct}). "
            f"auto_execute={settings.AUTO_EXECUTE} "
            f"— real money will move."
        )


async def _await_task_with_timeout(
    task: asyncio.Task | None,
    name: str,
    timeout: float,
) -> None:
    """Cancel ``task`` and wait for it, bounded by ``timeout`` seconds.

    Any of these outcomes is acceptable at shutdown time:
      * task completes (clean)
      * task raises CancelledError (expected)
      * task raises something else (logged, swallowed — shutdown
        must not re-raise)

    What is NOT acceptable: a task that refuses to finish within
    ``timeout``. That's logged at ``error`` so the incident is
    visible in Cloud Logging, then we return — the event loop moves
    on and the process can exit.
    """
    if task is None or task.done():
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except TimeoutError:
        log.error(
            f"shutdown: task '{name}' did not finish within {timeout:.1f}s "
            f"after cancellation; proceeding anyway"
        )
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.warning(f"shutdown: task '{name}' raised on cancel: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline_task, _risk_writer_task
    log.info("AYVID Backend starting...")
    _register_api_keys()
    _log_trade_routing_banner()

    components: dict[str, bool] = {"pipeline": False, "kill_switch": False}

    if settings.CONTINUOUS_PIPELINE_ENABLED:
        from data_primitives.pipeline.continuous_worker import start_continuous_pipeline

        try:
            _pipeline_task = asyncio.create_task(
                start_continuous_pipeline(interval_minutes=15),
                name="continuous_pipeline",
            )
            components["pipeline"] = True
            log.info("Continuous ingestion pipeline started (15min interval)")
        except Exception as exc:
            log.error(f"pipeline failed to start: {exc}")
    else:
        log.info("Continuous ingestion pipeline disabled (CONTINUOUS_PIPELINE_ENABLED=false)")

    try:
        from backend.services.orchestrator import (
            init_kill_switch,
            kill_switch_writer_loop,
        )

        await init_kill_switch()
        _risk_writer_task = asyncio.create_task(
            kill_switch_writer_loop(interval=0.1),
            name="kill_switch_writer",
        )
        components["kill_switch"] = True
        log.info("Kill switch armed: seqlock mmap + C++ watchdog + 10Hz writer loop")
    except Exception as exc:
        log.warning(f"Kill switch init skipped: {exc}")

    # Flip readiness to "ready" regardless of component state — /ready
    # exposes the per-component health so the orchestrator can gate on
    # it explicitly. An opt-in `AYVID_STRICT_READINESS=true` would
    # short-circuit to 503 when any component is down; keeping this
    # permissive by default matches how operators use the dashboard.
    mark_ready(components=components)

    yield

    # ── Teardown ────────────────────────────────────────────────────
    log.info("AYVID Backend shutting down...")
    mark_shutting_down("lifespan teardown")
    from data_primitives.pipeline.continuous_worker import stop_continuous_pipeline

    try:
        stop_continuous_pipeline()
    except Exception as exc:
        log.warning(f"stop_continuous_pipeline raised: {exc}")

    await _await_task_with_timeout(
        _pipeline_task,
        "continuous_pipeline",
        SHUTDOWN_TIMEOUT_SECONDS,
    )
    await _await_task_with_timeout(
        _risk_writer_task,
        "kill_switch_writer",
        SHUTDOWN_TIMEOUT_SECONDS,
    )

    try:
        from backend.services.orchestrator import stop_kill_switch

        await asyncio.wait_for(stop_kill_switch(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        log.error(
            f"stop_kill_switch exceeded {SHUTDOWN_TIMEOUT_SECONDS:.1f}s — "
            f"watchdog subprocess will orphan on pod death; operator must "
            f"prune stale /tmp/ayvid_risk.mmap manually"
        )
    except Exception as exc:
        log.warning(f"Kill switch shutdown failed: {exc}")

    try:
        from graph_intelligence.tools.neo4j_tools import close_driver

        await asyncio.wait_for(close_driver(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        log.error("neo4j driver close exceeded shutdown budget")
    except Exception as exc:
        log.warning(f"neo4j driver close raised: {exc}")


app = FastAPI(
    title="AYVID API",
    description="Asymmetric Yield Via Intelligent Decisions — API Layer",
    version="0.1.0",
    lifespan=lifespan,
)

# Observability wiring — request-id middleware + /ready probe +
# JSON-log formatter (when AYVID_LOG_FORMAT=json) + global exception
# handlers. MUST precede app.include_router(...) calls below so every
# request (including 404s) goes through the middleware stack.
install_observability(app)


def _parse_cors_origins(raw: str) -> list[str]:
    """Parse the comma-separated ``AYVID_ALLOWED_ORIGINS`` setting.

    Returns ``["*"]`` when the setting is blank or literally ``*`` to
    preserve the existing dev default; otherwise splits on commas and
    trims each entry.
    """
    value = (raw or "").strip()
    if not value or value == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


_cors_origins = _parse_cors_origins(getattr(settings, "AYVID_ALLOWED_ORIGINS", "*"))
_cors_credentials = bool(getattr(settings, "AYVID_CORS_ALLOW_CREDENTIALS", False))

# CORS safety: the previous config was `allow_origins=["*"]` combined
# with `allow_credentials=True`, which the CORS spec rejects anyway
# (browsers won't send cookies to a wildcard origin) AND it advertised
# a less-secure posture to scanning tools. Production deployments
# should set ``AYVID_ALLOWED_ORIGINS`` to a comma-separated list of
# explicit origins and optionally flip credentials on — tests and
# local dev retain the permissive `*` default so nothing breaks.
if _cors_origins == ["*"] and _cors_credentials:
    log.warning(
        "AYVID_ALLOWED_ORIGINS=* with credentials is not a valid CORS "
        "combination; disabling credentials for this run"
    )
    _cors_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "paper_trading": settings.PAPER_TRADING,
        "llm_provider": settings.LLM_PROVIDER,
    }


@app.get("/pipeline/status")
async def pipeline_status():
    from data_primitives.pipeline.continuous_worker import get_pipeline_status

    return get_pipeline_status()


from backend.api.sse.streamer import router as sse_router
from backend.api.v1.endpoints.calibration_routes import router as calibration_router
from backend.api.v1.endpoints.graph_routes import router as graph_router
from backend.api.v1.endpoints.live_routes import router as live_router
from backend.api.v1.endpoints.trade_routes import router as trade_router

app.include_router(graph_router, prefix="/api/v1/graph", tags=["graph"])
app.include_router(trade_router, prefix="/api/v1/trade", tags=["trade"])
app.include_router(live_router, prefix="/api/v1/live", tags=["live"])
app.include_router(calibration_router, prefix="/api/v1/calibration", tags=["calibration"])
app.include_router(sse_router, prefix="/api/v1/sse", tags=["sse"])
