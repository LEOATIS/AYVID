"""FastAPI application — the nervous system that routes requests.

No heavy compute here. Endpoints hand off to services immediately.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import settings
from shared.logger import get_logger

log = get_logger("backend.main")

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline_task, _risk_writer_task
    log.info("AYVID Backend starting...")
    _register_api_keys()
    _log_trade_routing_banner()

    from data_primitives.pipeline.continuous_worker import start_continuous_pipeline
    _pipeline_task = asyncio.create_task(
        start_continuous_pipeline(interval_minutes=15)
    )
    log.info("Continuous ingestion pipeline started (15min interval)")

    try:
        from execution_engine.kill_switch.memory_map import (
            initialize_mmap,
            risk_writer_loop,
        )
        from backend.services.orchestrator import get_risk_snapshot

        initialize_mmap()
        _risk_writer_task = asyncio.create_task(
            risk_writer_loop(get_snapshot=get_risk_snapshot, interval=0.1)
        )
        log.info("Kill switch mmap writer started (10Hz, with kill switch enforcement)")
    except Exception as exc:
        log.warning(f"Kill switch init skipped: {exc}")

    yield

    log.info("AYVID Backend shutting down...")
    from data_primitives.pipeline.continuous_worker import stop_continuous_pipeline
    stop_continuous_pipeline()
    if _pipeline_task:
        _pipeline_task.cancel()
    if _risk_writer_task:
        try:
            from execution_engine.kill_switch.memory_map import stop_writer, cleanup
            stop_writer()
        except Exception as exc:
            log.warning(f"Kill switch stop_writer failed: {exc}")
        _risk_writer_task.cancel()
        try:
            cleanup()
        except Exception as exc:
            log.warning(f"Kill switch cleanup failed: {exc}")
    from graph_intelligence.tools.neo4j_tools import close_driver
    await close_driver()


app = FastAPI(
    title="AYVID API",
    description="Asymmetric Yield Via Intelligent Decisions — API Layer",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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


from backend.api.v1.endpoints.graph_routes import router as graph_router
from backend.api.v1.endpoints.trade_routes import router as trade_router
from backend.api.v1.endpoints.live_routes import router as live_router
from backend.api.sse.streamer import router as sse_router

app.include_router(graph_router, prefix="/api/v1/graph", tags=["graph"])
app.include_router(trade_router, prefix="/api/v1/trade", tags=["trade"])
app.include_router(live_router, prefix="/api/v1/live", tags=["live"])
app.include_router(sse_router, prefix="/api/v1/sse", tags=["sse"])
