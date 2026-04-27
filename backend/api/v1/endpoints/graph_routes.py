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


class ProposeEdgesRequest(BaseModel):
    ticker: str
    dry_run: bool = False
    proposer_confidence: float = 0.2


class ProposeEdgesAllRequest(BaseModel):
    limit: int | None = 20
    concurrency: int = 3
    dry_run: bool = False
    proposer_confidence: float = 0.2


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


@router.post("/propose-edges")
async def propose_edges_for_ticker(req: ProposeEdgesRequest):
    """Discover new supply-chain edges from SEC filings for one ticker.

    Runs the full 10-K → LLM extraction → entity resolution → low-
    confidence Neo4j upsert pipeline. Edges land below the 0.6
    ``find_hidden_victims`` traversal cutoff until ``ayvid graph refine``
    validates them against observational returns.
    """
    try:
        from data_primitives.extractors.edge_proposer import (
            ingest_proposed_edges,
            propose_edges_from_10k,
        )

        result = await propose_edges_from_10k(
            req.ticker.upper(),
            proposer_confidence=req.proposer_confidence,
        )
        ingestion = await ingest_proposed_edges(
            result.proposals,
            dry_run=req.dry_run,
        )
        return {
            "ticker": result.ticker,
            "filings_analyzed": result.filings_analyzed,
            "candidates_raw": result.candidates_raw,
            "candidates_resolved": result.candidates_resolved,
            "candidates_rejected": result.candidates_rejected,
            "proposals": [p.model_dump() for p in result.proposals],
            "rejections": result.rejections,
            "ingestion": ingestion,
        }
    except Exception as exc:
        log.error(f"Edge proposer failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/propose-edges/batch")
async def propose_edges_batch(req: ProposeEdgesAllRequest):
    """Cron-safe batch proposer across the local-data ticker universe."""
    try:
        from data_primitives.extractors.edge_proposer import propose_for_universe

        summary = await propose_for_universe(
            limit=req.limit,
            concurrency=req.concurrency,
            proposer_confidence=req.proposer_confidence,
            dry_run=req.dry_run,
        )
        return summary
    except Exception as exc:
        log.error(f"Batch edge proposer failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/edge-proposals/recent")
async def edge_proposals_recent(last_n: int = 50):
    """Tail of ``calibration/edge_proposals.jsonl`` — provenance for every
    edge that the LLM proposer has added or touched.
    """
    try:
        from data_primitives.extractors.edge_proposer import read_audit_trail

        rows = read_audit_trail(last_n=last_n)
        return {"count": len(rows), "rows": rows}
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
