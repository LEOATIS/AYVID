"""Read-only endpoints exposing the R&D-roadmap artefacts.

Phase-2 (causal refinery) and the empirical-priors calibration
harness land their output on disk (`calibration/priors.json`,
`calibration/refined_dag.json`). These endpoints let the Next.js
console surface that data without re-implementing the file-reading
logic on the frontend.

P1-15: the priors file IS the fund's institutional memory —
strategy-by-regime Bayesian priors are competitive IP. The router is
now guarded by ``verify_api_key``; in dev (no keys registered,
``AYVID_REQUIRE_AUTH=False``) the dependency passes through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.core.security import verify_api_key
from shared.logger import get_logger

log = get_logger("api.calibration")
router = APIRouter(dependencies=[Depends(verify_api_key)])

PROJECT_ROOT = Path(__file__).resolve().parents[4]
CALIBRATION_DIR = PROJECT_ROOT / "calibration"
PRIORS_PATH = CALIBRATION_DIR / "priors.json"
REFINED_DAG_PATH = CALIBRATION_DIR / "refined_dag.json"
EDGE_PROPOSALS_PATH = CALIBRATION_DIR / "edge_proposals.jsonl"


@router.get("/priors")
async def priors_file() -> dict[str, Any]:
    """Full ``calibration/priors.json`` — strategy × regime × optimal params.

    Consumed by the console's Priors viewer tile. Returns the raw
    JSON plus a summary counting active vs blocked cells per strategy.
    """
    if not PRIORS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="priors.json not found — run `ayvid data calibrate --mode quick`",
        )
    try:
        payload = json.loads(PRIORS_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"priors.json malformed: {exc}")

    priors = payload.get("priors", {}) if isinstance(payload, dict) else {}
    summary: dict[str, dict[str, int]] = {}
    for strategy, cells in priors.items():
        active = sum(
            1 for c in cells.values() if not (isinstance(c, dict) and c.get("blocked_by_regime"))
        )
        blocked = sum(
            1 for c in cells.values() if isinstance(c, dict) and c.get("blocked_by_regime")
        )
        summary[strategy] = {"active": active, "blocked": blocked, "total": active + blocked}

    return {
        "generated_at": payload.get("_generated_at"),
        "min_samples": payload.get("_min_samples"),
        "schema_version": payload.get("_schema_version"),
        "summary": summary,
        "priors": priors,
    }


@router.get("/priors/{strategy}/{regime}")
async def priors_cell(strategy: str, regime: str) -> dict[str, Any]:
    """Single (strategy, regime) priors cell — for drill-down UI.

    Returns 404 when the cell does not exist (i.e. calibration has
    never seen a trade in this bucket), which the frontend can
    render as "no historical prior, using defaults".
    """
    doc = await priors_file()
    cells = doc.get("priors", {}).get(strategy, {})
    cell = cells.get(regime)
    if cell is None:
        raise HTTPException(
            status_code=404,
            detail=f"No prior for {strategy} × {regime}",
        )
    return {"strategy": strategy, "regime": regime, "cell": cell}


@router.get("/refined-dag")
async def refined_dag() -> dict[str, Any]:
    """Last ``ayvid graph refine`` snapshot — edges + LLM rationale trail.

    404 when the refinery has never run (authoritative confidence
    values still live in Neo4j; this artefact is purely for audit).
    """
    if not REFINED_DAG_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="refined_dag.json not found — run `ayvid graph refine`",
        )
    try:
        return json.loads(REFINED_DAG_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"refined_dag.json malformed: {exc}")


@router.get("/edge-proposals")
async def edge_proposals(last_n: int = 100) -> dict[str, Any]:
    """Tail of ``calibration/edge_proposals.jsonl`` for the console UI.

    Every row records one LLM-proposed supply-chain edge: where the
    mention came from (ticker whose 10-K was parsed), the raw entity
    string, which resolution method produced the ticker, confidence
    before/after, and the outcome (created / preserved_existing /
    dry_run / skipped).

    This is the artefact that proves the graph is no longer hand-
    seeded — every edge ingested through the proposer path lands here
    with its provenance.
    """
    if not EDGE_PROPOSALS_PATH.exists():
        return {
            "count": 0,
            "rows": [],
            "message": (
                "No proposals yet — run `ayvid graph propose <ticker>` or "
                "`ayvid graph propose-all` to mine SEC filings."
            ),
        }
    try:
        lines = EDGE_PROPOSALS_PATH.read_text().splitlines()
    except OSError as exc:
        raise HTTPException(500, f"edge_proposals.jsonl unreadable: {exc}")

    rows: list[dict[str, Any]] = []
    for line in lines[-last_n:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    outcomes: dict[str, int] = {}
    for r in rows:
        outcomes[r.get("outcome", "unknown")] = outcomes.get(r.get("outcome", "unknown"), 0) + 1

    return {
        "count": len(rows),
        "total_audit_lines": len(lines),
        "outcomes": outcomes,
        "rows": rows,
    }


@router.get("/roadmap-status")
async def roadmap_status() -> dict[str, Any]:
    """High-level roadmap feature-flag status for the console UI.

    Returns a boolean per Phase indicating whether its artefacts are
    present on disk — useful for the console landing page to show
    which upgrades are live.
    """
    return {
        "phase_1_adversarial_defense": {
            "live": True,
            "module": "data_primitives.security",
            "description": "Unicode + hidden-HTML sanitisation on every news ingestion path",
        },
        "phase_2_causal_falsification": {
            "live": REFINED_DAG_PATH.exists(),
            "module": "graph_intelligence.causal_falsification",
            "artefact": str(REFINED_DAG_PATH.relative_to(PROJECT_ROOT))
            if REFINED_DAG_PATH.exists()
            else None,
            "description": "Weekly LLM↔falsification loop refines Neo4j edge confidence",
        },
        "phase_2b_edge_proposer": {
            "live": EDGE_PROPOSALS_PATH.exists(),
            "module": "data_primitives.extractors.edge_proposer",
            "artefact": str(EDGE_PROPOSALS_PATH.relative_to(PROJECT_ROOT))
            if EDGE_PROPOSALS_PATH.exists()
            else None,
            "description": (
                "GPT-4o / Claude extracts supplier→customer edges from SEC "
                "10-K / 10-Q filings, resolves names to canonical tickers, "
                "and ingests into Neo4j at confidence=0.2 until the Phase-2 "
                "refinery validates them against observational returns."
            ),
        },
        "phase_3_researcher_debate": {
            "live": True,
            "module": "graph_intelligence.agents.researchers",
            "description": "2-round Bull/Bear adversarial debate between mapper and analyst swarm",
        },
        "phase_4_epistemic_coherence": {
            "live": True,
            "module": "graph_intelligence.debate",
            "description": "Mean pairwise JS divergence filter on the analyst swarm",
        },
        "phase_5_pattern_agent": {
            "live": True,
            "module": "graph_intelligence.agents.pattern",
            "description": "Multimodal 5th specialist classifying candlestick patterns",
        },
        "empirical_priors": {
            "live": PRIORS_PATH.exists(),
            "module": "backtesting.calibrate",
            "artefact": str(PRIORS_PATH.relative_to(PROJECT_ROOT))
            if PRIORS_PATH.exists()
            else None,
            "description": "Supervisor LLM reads calibration/priors.json as Bayesian prior",
        },
    }
