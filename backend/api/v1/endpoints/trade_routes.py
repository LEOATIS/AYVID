"""Trade execution API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.security import verify_api_key
from shared.logger import get_logger

log = get_logger("api.trade")

router = APIRouter(dependencies=[Depends(verify_api_key)])


class TradeRequest(BaseModel):
    ticker: str
    action: str
    confidence: float = 0.7
    alpha_horizon: str = "3rd_order"


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
    """Manually execute a trade signal."""
    try:
        from backend.services.orchestrator import execute_manual_signal
        result = await execute_manual_signal(
            ticker=req.ticker,
            action=req.action,
            confidence=req.confidence,
            alpha_horizon=req.alpha_horizon,
        )
        return result
    except Exception as exc:
        log.error(f"Trade execution failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/queued")
async def get_queued():
    """Get orders queued during pre-market."""
    from execution_engine.broker.orders import get_queued_orders
    return [s.model_dump(mode="json") for s in get_queued_orders()]
