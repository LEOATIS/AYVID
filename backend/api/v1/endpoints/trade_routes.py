"""Trade execution API endpoints.

Hosts the ``POST /execute`` (and alias ``POST /manual``) endpoint that
is now the single authoritative entry point for operator-initiated
trades (P0-B). The CLI ``ayvid trade execute`` delegates here over HTTP
rather than calling ``execute_manual_signal`` in-process, which keeps
the graph-freshness gate, the sequence-sync gate, and the mmap
kill-switch writer all on the same lifespan.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.core.security import verify_api_key
from shared.exceptions import GraphStaleError, SequenceValidationError
from shared.logger import get_logger

log = get_logger("api.trade")

router = APIRouter(dependencies=[Depends(verify_api_key)])


class TradeRequest(BaseModel):
    """Operator-initiated trade intent.

    Mirrors the legacy ``TradeRequest`` schema for backward compat with
    existing external callers (dashboards, scripts). Field semantics:

    * ``ticker``: required, the instrument symbol
    * ``action``: ``"LONG"`` | ``"SHORT"`` | ``"HOLD"``
    * ``confidence``: operator-declared conviction in [0, 1]
    * ``alpha_horizon``: one of ``"1st_order"``, ``"2nd_order"``,
      ``"3rd_order"``, ``"4th_order"`` — drives order-type routing
    """

    ticker: str
    action: str
    confidence: float = Field(0.7, ge=0.0, le=1.0)
    alpha_horizon: str = "3rd_order"


async def _run_manual_execute(req: TradeRequest) -> dict:
    """Shared body for the POST /execute and POST /manual endpoints.

    Keeps the two routes bit-for-bit equivalent so older clients using
    ``/execute`` and new clients (CLI) using ``/manual`` get identical
    behaviour and identical error surfaces.
    """
    try:
        from backend.services.orchestrator import execute_manual_signal

        result = await execute_manual_signal(
            ticker=req.ticker,
            action=req.action,
            confidence=req.confidence,
            alpha_horizon=req.alpha_horizon,
        )
        return result
    except (GraphStaleError, SequenceValidationError) as exc:
        # Data-plane desync is an expected, caller-visible failure —
        # surface it as 409 Conflict so the CLI can differentiate it
        # from a 500 internal error and print a clean operator msg.
        log.warning(f"Trade rejected — data desync: {exc}")
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        # Invalid action / horizon enum → client error
        log.warning(f"Trade rejected — bad input: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        log.error(f"Trade execution failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/portfolio")
async def get_portfolio():
    """Get current portfolio state."""
    from backend.services.orchestrator import get_portfolio_state

    portfolio = get_portfolio_state()
    return portfolio.model_dump(mode="json")


@router.get("/signals")
async def get_signals():
    """Get recent trade signals."""
    from backend.services.orchestrator import get_recent_signals

    signals = get_recent_signals()
    return [s.model_dump(mode="json") for s in signals]


@router.get("/risk")
async def get_risk():
    """Get current risk snapshot."""
    from backend.services.orchestrator import get_risk_snapshot

    snapshot = get_risk_snapshot()
    return snapshot.model_dump(mode="json")


@router.post("/execute")
async def execute_trade(req: TradeRequest):
    """Manually execute a trade signal (legacy route)."""
    return await _run_manual_execute(req)


@router.post("/manual")
async def execute_manual_trade(req: TradeRequest):
    """Operator-initiated trade — enforces the same graph-freshness and
    sequence-sync gates as autopilot trades.

    P0-B: the CLI used to call ``execute_manual_signal`` in-process with
    ``sequence_id=0``, which short-circuited the freshness check and
    ran outside the FastAPI lifespan (i.e. without the kill-switch mmap
    writer alive). Routing manual trades through this endpoint keeps
    exactly one execution path, one lifespan, one sequence registry.
    Authentication is enforced via ``verify_api_key`` on the router.
    """
    return await _run_manual_execute(req)


@router.get("/queued")
async def get_queued():
    """Get orders queued during pre-market."""
    from execution_engine.broker.orders import get_queued_orders

    return [s.model_dump(mode="json") for s in get_queued_orders()]
