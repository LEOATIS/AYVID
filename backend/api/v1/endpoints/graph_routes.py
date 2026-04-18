"""Graph intelligence API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.security import verify_api_key
from shared.logger import get_logger

log = get_logger("api.graph")

router = APIRouter(dependencies=[Depends(verify_api_key)])


class ShockRequest(BaseModel):
    shock_ticker: str
    max_depth: int = 4
    min_compound_risk: float = 0.15


class SupplyEdgeRequest(BaseModel):
    source_ticker: str
    target_ticker: str
    revenue_dependency_pct: float
    criticality_score: float


@router.get("/stats")
async def graph_stats():
    """Get supply chain graph statistics."""
    try:
        from graph_intelligence.tools.neo4j_tools import get_graph_stats
        stats = await get_graph_stats()
        return stats
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j unavailable: {exc}")


@router.post("/analyze-shock")
async def analyze_shock(req: ShockRequest):
    """Run the full causal arbitrage analysis for a supply chain shock."""
    try:
        from graph_intelligence.pipeline import run_causal_analysis
        signal = await run_causal_analysis(shock_ticker=req.shock_ticker)
        if signal:
            return signal.model_dump(mode="json")
        return {"status": "no_signal", "message": "No actionable trade found"}
    except Exception as exc:
        log.error(f"Shock analysis failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ingest-edge")
async def ingest_edge(req: SupplyEdgeRequest):
    """Ingest a single supply chain relationship."""
    try:
        from graph_intelligence.tools.neo4j_tools import upsert_supply_edge
        from shared.types import SupplyEdge
        edge = SupplyEdge(
            source_ticker=req.source_ticker,
            target_ticker=req.target_ticker,
            revenue_dependency_pct=req.revenue_dependency_pct,
            criticality_score=req.criticality_score,
        )
        await upsert_supply_edge(edge)
        return {"status": "ok", "edge": f"{req.source_ticker} -> {req.target_ticker}"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/find-victims/{shock_ticker}")
async def find_victims(shock_ticker: str, max_depth: int = 4):
    """Find hidden N-th order victims via Systemic Importance Masking."""
    try:
        from graph_intelligence.tools.neo4j_tools import find_hidden_victims
        paths = await find_hidden_victims(shock_ticker, max_depth=max_depth)
        return [p.model_dump(mode="json") for p in paths]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
