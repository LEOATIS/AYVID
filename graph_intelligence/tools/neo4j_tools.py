"""Neo4j Cypher query abstraction layer for supply chain topology operations.

All graph queries — schema init, ingestion, centrality, Systemic Importance Masking,
and N-th order path traversal — are centralized here.
"""
from __future__ import annotations

from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from shared.config import settings
from shared.exceptions import CausalPathNotFoundError, Neo4jConnectionError
from shared.logger import get_logger
from shared.types import CausalPath, CompanyNode, SupplyEdge

log = get_logger("graph.neo4j")

_driver: AsyncDriver | None = None


def _session_kwargs() -> dict:
    """Build kwargs for driver.session(), including database if configured."""
    kwargs = {}
    if settings.NEO4J_DATABASE:
        kwargs["database"] = settings.NEO4J_DATABASE
    return kwargs


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((Neo4jConnectionError, ConnectionError, OSError)),
    reraise=True,
)
async def get_driver() -> AsyncDriver:
    """Get or create the async Neo4j driver."""
    global _driver
    if _driver is None:
        if not settings.NEO4J_PASSWORD:
            raise Neo4jConnectionError("NEO4J_PASSWORD not configured")
        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _driver


async def close_driver() -> None:
    """Cleanly close the Neo4j driver."""
    global _driver
    if _driver:
        await _driver.close()
        _driver = None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((Neo4jConnectionError, ConnectionError, OSError)),
    reraise=True,
)
async def verify_connection() -> bool:
    """Test Neo4j connectivity."""
    try:
        driver = await get_driver()
        async with driver.session(**_session_kwargs()) as session:
            result = await session.run("RETURN 1 AS n")
            record = await result.single()
            return record is not None and record["n"] == 1
    except Exception as exc:
        log.error(f"Neo4j connection failed: {exc}")
        return False


# ── Schema Initialization ─────────────────────────────────────

_SCHEMA_QUERIES = [
    "CREATE CONSTRAINT company_ticker IF NOT EXISTS FOR (c:Company) REQUIRE c.ticker IS UNIQUE",
    "CREATE CONSTRAINT facility_id IF NOT EXISTS FOR (f:Facility) REQUIRE f.facility_id IS UNIQUE",
    "CREATE INDEX product_name IF NOT EXISTS FOR (p:Product) ON (p.name)",
    "CREATE INDEX product_category IF NOT EXISTS FOR (p:Product) ON (p.category)",
]


async def initialize_schema() -> None:
    """Create constraints and indexes for the supply chain graph."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        for query in _SCHEMA_QUERIES:
            await session.run(query)
    log.info("Neo4j schema initialized")


# ── Node Operations ───────────────────────────────────────────

async def upsert_company(node: CompanyNode) -> None:
    """Create or update a Company node."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        await session.run(
            """
            MERGE (c:Company {ticker: $ticker})
            SET c.name = $name,
                c.market_cap = $market_cap,
                c.sector = $sector,
                c.pagerank_score = $pagerank_score,
                c.betweenness_score = $betweenness_score,
                c.last_updated = datetime()
            """,
            ticker=node.ticker,
            name=node.name,
            market_cap=node.market_cap,
            sector=node.sector,
            pagerank_score=node.pagerank_score,
            betweenness_score=node.betweenness_score,
        )


async def upsert_supply_edge(edge: SupplyEdge) -> None:
    """Create or update a SUPPLIES_TO relationship."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        await session.run(
            """
            MERGE (supplier:Company {ticker: $source})
            MERGE (buyer:Company {ticker: $target})
            MERGE (supplier)-[r:SUPPLIES_TO]->(buyer)
            SET r.revenue_dependency_pct = $dependency,
                r.criticality_score = $criticality,
                r.last_updated = datetime()
            """,
            source=edge.source_ticker,
            target=edge.target_ticker,
            dependency=edge.revenue_dependency_pct,
            criticality=edge.criticality_score,
        )


async def upsert_product(name: str, category: str = "unknown", manufacturer: str | None = None) -> None:
    """Create or update a Product node."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        if manufacturer:
            await session.run(
                """
                MERGE (p:Product {name: $name})
                SET p.category = $category, p.last_updated = datetime()
                WITH p
                MERGE (c:Company {ticker: $manufacturer})
                MERGE (c)-[:MANUFACTURES]->(p)
                """,
                name=name, category=category, manufacturer=manufacturer,
            )
        else:
            await session.run(
                """
                MERGE (p:Product {name: $name})
                SET p.category = $category, p.last_updated = datetime()
                """,
                name=name, category=category,
            )


async def upsert_competitor_edge(ticker_a: str, ticker_b: str, overlap: float = 0.0) -> None:
    """Create or update a COMPETES_WITH relationship."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        await session.run(
            """
            MERGE (a:Company {ticker: $a})
            MERGE (b:Company {ticker: $b})
            MERGE (a)-[r:COMPETES_WITH]->(b)
            SET r.market_overlap_pct = $overlap, r.last_updated = datetime()
            """,
            a=ticker_a, b=ticker_b, overlap=overlap,
        )


async def bulk_ingest_supply_chain(
    edges: list[SupplyEdge],
) -> int:
    """Batch ingest supply chain relationships."""
    driver = await get_driver()
    payload = [
        {
            "source": e.source_ticker,
            "target": e.target_ticker,
            "dependency": e.revenue_dependency_pct,
            "criticality": e.criticality_score,
        }
        for e in edges
    ]
    async with driver.session(**_session_kwargs()) as session:
        await session.run(
            """
            UNWIND $payload AS row
            MERGE (supplier:Company {ticker: row.source})
            MERGE (buyer:Company {ticker: row.target})
            MERGE (supplier)-[rel:SUPPLIES_TO]->(buyer)
            SET rel.revenue_dependency_pct = row.dependency,
                rel.criticality_score = row.criticality
            """,
            payload=payload,
        )
    log.info(f"Ingested {len(payload)} supply chain edges")
    return len(payload)


# ── Centrality & Systemic Importance Masking ──────────────────

async def compute_pagerank() -> list[dict[str, Any]]:
    """Compute PageRank across the supply chain graph.

    Uses native Cypher projection when GDS is unavailable.
    """
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        try:
            result = await session.run(
                """
                CALL gds.pageRank.stream({
                    nodeProjection: 'Company',
                    relationshipProjection: {
                        SUPPLIES_TO: { orientation: 'NATURAL' }
                    }
                })
                YIELD nodeId, score
                WITH gds.util.asNode(nodeId) AS node, score
                SET node.pagerank_score = score
                RETURN node.ticker AS ticker, score
                ORDER BY score DESC
                """
            )
            return [dict(r) async for r in result]
        except Exception:
            log.warning("GDS not available, using degree-based approximation")
            result = await session.run(
                """
                MATCH (c:Company)
                OPTIONAL MATCH (c)<-[:SUPPLIES_TO]-(supplier)
                WITH c, count(supplier) AS in_degree
                OPTIONAL MATCH (c)-[:SUPPLIES_TO]->(buyer)
                WITH c, in_degree, count(buyer) AS out_degree
                WITH c, toFloat(in_degree + out_degree) AS degree
                SET c.pagerank_score = degree
                RETURN c.ticker AS ticker, degree AS score
                ORDER BY score DESC
                """
            )
            return [dict(r) async for r in result]


async def compute_betweenness() -> list[dict[str, Any]]:
    """Compute betweenness centrality for bottleneck detection."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        try:
            result = await session.run(
                """
                CALL gds.betweenness.stream({
                    nodeProjection: 'Company',
                    relationshipProjection: {
                        SUPPLIES_TO: { orientation: 'NATURAL' }
                    }
                })
                YIELD nodeId, score
                WITH gds.util.asNode(nodeId) AS node, score
                SET node.betweenness_score = score
                RETURN node.ticker AS ticker, score
                ORDER BY score DESC
                """
            )
            return [dict(r) async for r in result]
        except Exception:
            log.warning("GDS not available, using path-count approximation")
            result = await session.run(
                """
                MATCH (c:Company)
                OPTIONAL MATCH path = shortestPath((a:Company)-[:SUPPLIES_TO*..4]->(b:Company))
                WHERE a <> b AND c IN nodes(path) AND c <> a AND c <> b
                WITH c, count(path) AS path_count
                SET c.betweenness_score = toFloat(path_count)
                RETURN c.ticker AS ticker, toFloat(path_count) AS score
                ORDER BY score DESC
                """
            )
            return [dict(r) async for r in result]


async def _resolve_pr_threshold(
    session, percentile: float = 0.85,
) -> float:
    """Compute an absolute pagerank cutoff at the given percentile.

    Works with both normalized GDS PageRank (0-1) and raw degree-based
    approximations (integer counts) used when GDS is unavailable.
    """
    result = await session.run(
        """
        MATCH (c:Company)
        WHERE c.pagerank_score IS NOT NULL
        RETURN c.pagerank_score AS pr
        ORDER BY pr ASC
        """
    )
    scores = [r["pr"] async for r in result]
    if not scores:
        return 999.0
    idx = int(len(scores) * percentile)
    return scores[min(idx, len(scores) - 1)]


async def find_hidden_victims(
    shock_ticker: str,
    pagerank_percentile: float = 0.85,
    min_compound_risk: float = 0.005,
    max_depth: int = 4,
    min_edge_confidence: float = 0.0,
) -> list[CausalPath]:
    """Execute Systemic Importance Masking to find N-th order trade targets.

    Filters out ubiquitous high-PageRank nodes (above the given percentile)
    and isolates hidden victims with high compound revenue dependency.

    Phase-2 integration: ``min_edge_confidence`` additionally filters
    paths so that every edge in the traversal has a statistical
    ``confidence`` score above the supplied threshold. Confidence is
    populated by ``update_edge_confidence`` after the CausalFusion
    refinery run. Default 0.0 → no filtering (backwards compatible).
    """
    if not isinstance(max_depth, int) or max_depth < 1:
        max_depth = 4
    driver = await get_driver()
    # ALL() requires a confidence floor when min_edge_confidence > 0.
    # When == 0.0 the predicate degenerates to "True" and no edges are filtered.
    query = f"""
        MATCH path = (shocked:Company {{ticker: $shock_ticker}})-[:SUPPLIES_TO*1..{max_depth}]->(target:Company)
        WHERE target.ticker <> $shock_ticker
          AND (target.pagerank_score IS NULL OR target.pagerank_score < $pr_threshold)
          AND ALL(rel IN relationships(path) WHERE
              COALESCE(rel.confidence, 1.0) >= $min_edge_confidence)
        WITH target, path,
             length(path) AS depth,
             reduce(risk = 1.0, rel IN relationships(path) |
                risk * COALESCE(rel.revenue_dependency_pct, 0.1)) AS compound_risk,
             [n IN nodes(path) | n.ticker] AS path_tickers
        WHERE compound_risk > $min_risk
        RETURN target.ticker AS target_ticker,
               depth,
               compound_risk,
               path_tickers
        ORDER BY compound_risk DESC
        LIMIT 20
    """
    async with driver.session(**_session_kwargs()) as session:
        pr_cutoff = await _resolve_pr_threshold(session, pagerank_percentile)
        log.info(f"PageRank cutoff at p{pagerank_percentile*100:.0f}: {pr_cutoff}")

        result = await session.run(
            query,
            shock_ticker=shock_ticker,
            pr_threshold=pr_cutoff,
            min_risk=min_compound_risk,
            min_edge_confidence=float(min_edge_confidence),
        )

        paths: list[CausalPath] = []
        async for record in result:
            paths.append(CausalPath(
                shocked_ticker=shock_ticker,
                target_ticker=record["target_ticker"],
                depth=record["depth"],
                compound_risk=record["compound_risk"],
                path_tickers=record["path_tickers"],
            ))

        if not paths:
            raise CausalPathNotFoundError(
                f"No valid causal paths from {shock_ticker} with "
                f"compound risk > {min_compound_risk}"
            )

        log.info(
            f"Found {len(paths)} hidden victims from [bold]{shock_ticker}[/bold] shock"
        )
        return paths


async def update_edge_confidence(
    src_ticker: str,
    dst_ticker: str,
    confidence: float,
    *,
    p_value: float | None = None,
    method: str = "causal_fusion",
) -> bool:
    """Stamp a ``SUPPLIES_TO`` edge with a statistical confidence score.

    Written by the Phase-2 CausalFusion refinery after every iteration.
    The per-edge ``confidence`` property is read back by
    ``find_hidden_victims`` when ``min_edge_confidence > 0``.

    Returns True when a single edge was updated, False otherwise.
    """
    driver = await get_driver()
    confidence = float(max(0.0, min(confidence, 1.0)))
    async with driver.session(**_session_kwargs()) as session:
        result = await session.run(
            """
            MATCH (a:Company {ticker: $src})-[r:SUPPLIES_TO]->(b:Company {ticker: $dst})
            SET r.confidence = $confidence,
                r.confidence_method = $method,
                r.confidence_p_value = $p_value,
                r.confidence_updated_at = datetime()
            RETURN count(r) AS updated
            """,
            src=src_ticker,
            dst=dst_ticker,
            confidence=confidence,
            method=method,
            p_value=float(p_value) if p_value is not None else None,
        )
        record = await result.single()
        updated = int(record["updated"]) if record else 0
        return updated == 1


async def bulk_update_edge_confidences(
    edge_updates: list[tuple[str, str, float]],
    *,
    method: str = "causal_fusion",
) -> int:
    """Batch version of `update_edge_confidence`. Returns # edges updated.

    Useful when the refinery finishes and needs to persist N refined
    edges in one round-trip.
    """
    if not edge_updates:
        return 0
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        result = await session.run(
            """
            UNWIND $rows AS row
            MATCH (a:Company {ticker: row.src})-[r:SUPPLIES_TO]->(b:Company {ticker: row.dst})
            SET r.confidence = row.confidence,
                r.confidence_method = $method,
                r.confidence_updated_at = datetime()
            RETURN count(r) AS updated
            """,
            rows=[
                {"src": s, "dst": d, "confidence": float(c)}
                for s, d, c in edge_updates
            ],
            method=method,
        )
        record = await result.single()
        return int(record["updated"]) if record else 0


async def get_graph_stats() -> dict[str, int]:
    """Return high-level graph statistics."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        result = await session.run(
            """
            MATCH (c:Company) WITH count(c) AS companies
            MATCH ()-[r:SUPPLIES_TO]->() WITH companies, count(r) AS edges
            RETURN companies, edges
            """
        )
        record = await result.single()
        if record:
            return {"companies": record["companies"], "edges": record["edges"]}
        return {"companies": 0, "edges": 0}
