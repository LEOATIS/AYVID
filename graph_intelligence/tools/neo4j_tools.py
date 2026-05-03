"""Neo4j Cypher query abstraction layer for supply chain topology operations.

All graph queries — schema init, ingestion, centrality, Systemic Importance Masking,
and N-th order path traversal — are centralized here.
"""

from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from shared.config import settings
from shared.exceptions import CausalPathNotFoundError, Neo4jConnectionError
from shared.logger import get_logger
from shared.types import CausalPath, CompanyNode, FacilityNode, SupplyEdge

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
    """Get or create the async Neo4j driver.

    Previous revisions attempted to pre-resolve the Aura hostname
    to IP addresses via ``socket.getaddrinfo`` and hand those to the
    driver via ``resolver=``. That was a workaround for Cursor's
    restricted-network sandbox where asyncio's DNS was blocked.
    When the backend runs in a real terminal (full network),
    returning IPs breaks Aura's routing-table negotiation — the
    driver spams ``Unable to retrieve routing information`` because
    it can't match the IPs against cluster member hostnames. We
    now let the driver use its default async resolver (which works
    outside the sandbox) and keep ``invalidate_driver`` +
    ``_is_transient_resolver_error`` as the belt-and-braces recovery
    hook for genuine DNS flaps.
    """
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


async def invalidate_driver() -> None:
    """Force-discard the cached driver without raising on close errors.

    Used as a self-heal hook when the long-lived driver singleton is
    suspected of holding stale state — e.g. the DNS resolver attached
    to the driver cached a NXDOMAIN during a network flap at boot and
    is now replaying that failure for every query even though the
    host resolves cleanly.

    Unlike ``close_driver`` this never raises — the goal is to get
    back to ``_driver is None`` so the next ``get_driver()`` call
    builds a fresh instance with a fresh resolver state.
    """
    global _driver
    if _driver is None:
        return
    try:
        await _driver.close()
    except Exception as exc:
        log.warning(f"neo4j driver close during invalidate failed (ignored): {exc}")
    finally:
        _driver = None


def _is_transient_resolver_error(exc: BaseException) -> bool:
    """True for errors that typically clear after recreating the driver.

    Covers the full transient-failure surface we've seen in practice:
      * DNS resolution failures (``EAI_NONAME`` etc.) — network flap
        during the driver's first hostname lookup.
      * Socket-level resets (``Connection reset by peer``, broken pipe,
        defunct connection) — usually a middlebox/proxy/keepalive
        dropping the idle bolt socket.
      * Routing-table refresh failures on Aura — the "Unable to
        retrieve routing information" cascade happens when the driver
        holds a stale router reference after a connection reset.
      * Pool / session exhaustion on long-running processes.
    Auth failures, syntax errors, and business-logic exceptions are
    NOT in this set — retrying those would just loop.
    """
    msg = str(exc).lower()
    transient_tokens = (
        "failed to dns resolve",
        "nodename nor servname",
        "temporary failure in name resolution",
        "name or service not known",
        "getaddrinfo failed",
        "connection refused",
        "connection reset",
        "defunct connection",
        "broken pipe",
        "unable to retrieve routing information",
        "failed to read from",
        "pool is closed",
    )
    if any(tok in msg for tok in transient_tokens):
        return True
    return isinstance(exc, (ConnectionError, OSError, TimeoutError))


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
    # Node uniqueness + lookup indexes
    "CREATE CONSTRAINT company_ticker IF NOT EXISTS FOR (c:Company) REQUIRE c.ticker IS UNIQUE",
    "CREATE CONSTRAINT facility_id IF NOT EXISTS FOR (f:Facility) REQUIRE f.facility_id IS UNIQUE",
    "CREATE INDEX product_name IF NOT EXISTS FOR (p:Product) ON (p.name)",
    "CREATE INDEX product_category IF NOT EXISTS FOR (p:Product) ON (p.category)",
    # Edge-property indexes — matter at scale (>50k edges) when traversal
    # filters on confidence / dependency. Without these, ALL(...) and
    # WHERE r.revenue_dependency_pct > X scans every relationship.
    "CREATE INDEX edge_revenue_dependency IF NOT EXISTS FOR ()-[r:SUPPLIES_TO]-() ON (r.revenue_dependency_pct)",
    "CREATE INDEX edge_confidence IF NOT EXISTS FOR ()-[r:SUPPLIES_TO]-() ON (r.confidence)",
    "CREATE INDEX edge_confidence_method IF NOT EXISTS FOR ()-[r:SUPPLIES_TO]-() ON (r.confidence_method)",
    "CREATE INDEX competitor_overlap IF NOT EXISTS FOR ()-[r:COMPETES_WITH]-() ON (r.market_overlap_pct)",
    # Geospatial index on facility location enables fast "facilities
    # within 50km of port X" queries when a regional shock fires.
    "CREATE POINT INDEX facility_location IF NOT EXISTS FOR (f:Facility) ON (f.location)",
]


async def initialize_schema() -> None:
    """Create constraints and indexes for the supply chain graph."""
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        for query in _SCHEMA_QUERIES:
            await session.run(query)
    log.info("Neo4j schema initialized")


async def auto_bootstrap_graph() -> dict[str, Any]:
    """Idempotent boot-time graph setup.

    Replaces two operator-run CLI commands (``ayvid graph init`` and
    ``ayvid graph seed``) so a fresh deploy never sits with an empty
    or schema-less Neo4j. Behaviour:

      1. Always re-runs ``initialize_schema()`` — every query is gated
         by ``IF NOT EXISTS`` so this is cheap-no-op when schema is
         already in place.
      2. Probes ``get_graph_stats()``. If the graph has zero
         ``SUPPLIES_TO`` edges we bulk-load the curated
         ``GLOBAL_SUPPLY_CHAIN`` seed; otherwise we leave the live
         graph alone.

    All failures are caught and reported in the return dict — the
    runner cannot afford a Neo4j outage to take down the trading loop.
    """
    out: dict[str, Any] = {
        "schema_initialized": False,
        "seed_skipped_reason": None,
        "edges_seeded": 0,
        "errors": [],
    }
    try:
        await initialize_schema()
        out["schema_initialized"] = True
    except Exception as exc:
        out["errors"].append(f"schema: {type(exc).__name__}: {exc}")
        log.warning(f"auto_bootstrap_graph schema init failed: {exc}")
        # Don't try to seed without schema — duplicate companies would
        # land on every subsequent boot.
        return out

    try:
        stats = await get_graph_stats()
    except Exception as exc:
        out["errors"].append(f"stats: {type(exc).__name__}: {exc}")
        out["seed_skipped_reason"] = "stats_probe_failed"
        log.warning(f"auto_bootstrap_graph stats probe failed: {exc}")
        return out

    if stats.get("edges", 0) > 0:
        out["seed_skipped_reason"] = (
            f"graph_already_populated ({stats['companies']} companies, "
            f"{stats['edges']} edges)"
        )
        log.info(
            f"auto_bootstrap_graph: skipping seed — graph already has "
            f"{stats['edges']} edges across {stats['companies']} companies"
        )
        return out

    try:
        from graph_intelligence.tools.seed_supply_chain import GLOBAL_SUPPLY_CHAIN

        edges = [
            SupplyEdge(
                source_ticker=src,
                target_ticker=tgt,
                revenue_dependency_pct=dep,
                criticality_score=crit,
            )
            for src, tgt, dep, crit in GLOBAL_SUPPLY_CHAIN
            if dep > 0
        ]
        seeded = await bulk_ingest_supply_chain(edges)
        out["edges_seeded"] = seeded
        log.info(f"auto_bootstrap_graph: seeded {seeded} supply-chain edges")
    except Exception as exc:
        out["errors"].append(f"seed: {type(exc).__name__}: {exc}")
        log.warning(f"auto_bootstrap_graph seed failed: {exc}")
    return out


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


async def upsert_product(
    name: str, category: str = "unknown", manufacturer: str | None = None
) -> None:
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
                name=name,
                category=category,
                manufacturer=manufacturer,
            )
        else:
            await session.run(
                """
                MERGE (p:Product {name: $name})
                SET p.category = $category, p.last_updated = datetime()
                """,
                name=name,
                category=category,
            )


async def upsert_competitor_edge(
    ticker_a: str,
    ticker_b: str,
    overlap: float = 0.0,
    *,
    sector: str | None = None,
    source: str = "manual",
) -> None:
    """Create or update a bidirectional COMPETES_WITH relationship.

    COMPETES_WITH is intrinsically symmetric — we MERGE both directions
    so a downstream MATCH (a)-[:COMPETES_WITH]-(b) traversal returns
    consistent results regardless of which side the edge was inserted
    from. ``source`` is stamped so the LLM proposer (when it eventually
    starts emitting these from 10-K competitor mentions) can be
    distinguished from operator-curated edges.
    """
    driver = await get_driver()
    overlap = float(max(0.0, min(overlap, 1.0)))
    async with driver.session(**_session_kwargs()) as session:
        await session.run(
            """
            MERGE (a:Company {ticker: $a})
            MERGE (b:Company {ticker: $b})
            MERGE (a)-[r1:COMPETES_WITH]->(b)
            ON CREATE SET r1.created_via = $source, r1.created_at = datetime()
            SET r1.market_overlap_pct = $overlap,
                r1.sector = COALESCE($sector, r1.sector),
                r1.last_updated = datetime()
            MERGE (b)-[r2:COMPETES_WITH]->(a)
            ON CREATE SET r2.created_via = $source, r2.created_at = datetime()
            SET r2.market_overlap_pct = $overlap,
                r2.sector = COALESCE($sector, r2.sector),
                r2.last_updated = datetime()
            """,
            a=ticker_a.upper(),
            b=ticker_b.upper(),
            overlap=overlap,
            sector=sector,
            source=source,
        )


async def bulk_upsert_competitor_edges(
    rows: list[tuple[str, str, float]],
    *,
    source: str = "manual",
    sector: str | None = None,
) -> int:
    """Batch version of upsert_competitor_edge."""
    if not rows:
        return 0
    driver = await get_driver()
    payload = [
        {"a": a.upper(), "b": b.upper(), "overlap": float(max(0.0, min(o, 1.0)))}
        for a, b, o in rows
    ]
    async with driver.session(**_session_kwargs()) as session:
        result = await session.run(
            """
            UNWIND $rows AS row
            MERGE (a:Company {ticker: row.a})
            MERGE (b:Company {ticker: row.b})
            MERGE (a)-[r1:COMPETES_WITH]->(b)
            ON CREATE SET r1.created_via = $source, r1.created_at = datetime()
            SET r1.market_overlap_pct = row.overlap,
                r1.sector = COALESCE($sector, r1.sector),
                r1.last_updated = datetime()
            MERGE (b)-[r2:COMPETES_WITH]->(a)
            ON CREATE SET r2.created_via = $source, r2.created_at = datetime()
            SET r2.market_overlap_pct = row.overlap,
                r2.sector = COALESCE($sector, r2.sector),
                r2.last_updated = datetime()
            RETURN count(*) AS upserted
            """,
            rows=payload,
            sector=sector,
            source=source,
        )
        rec = await result.single()
        return int(rec["upserted"]) if rec else 0


async def upsert_facility(node: FacilityNode) -> None:
    """Create or update a Facility node with geospatial point + ops link.

    Stores ``f.location = point({latitude, longitude})`` so the
    facility_location POINT index applies. When ``operator_ticker`` is
    set we additionally MERGE an OPERATES edge from the company to the
    facility — that gives the mapper a path from a regional / facility
    shock (port closure, fab fire) to the listed equity it impacts.
    """
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        await session.run(
            """
            MERGE (f:Facility {facility_id: $facility_id})
            SET f.name = COALESCE($name, f.name),
                f.facility_type = $facility_type,
                f.operational = $operational,
                f.capacity_units = $capacity_units,
                f.location = CASE
                    WHEN $latitude IS NOT NULL AND $longitude IS NOT NULL
                    THEN point({latitude: $latitude, longitude: $longitude})
                    ELSE f.location
                END,
                f.last_updated = datetime()
            """,
            facility_id=node.facility_id,
            name=node.name,
            facility_type=node.facility_type,
            operational=node.operational,
            capacity_units=node.capacity_units,
            latitude=node.latitude,
            longitude=node.longitude,
        )
        if node.operator_ticker:
            await session.run(
                """
                MERGE (c:Company {ticker: $ticker})
                MERGE (f:Facility {facility_id: $facility_id})
                MERGE (c)-[r:OPERATES]->(f)
                SET r.last_updated = datetime()
                """,
                ticker=node.operator_ticker.upper(),
                facility_id=node.facility_id,
            )


async def upsert_manufactures_edge(
    ticker: str,
    product_name: str,
    *,
    category: str = "unknown",
    capacity_share_pct: float | None = None,
) -> None:
    """Create or update a MANUFACTURES relationship from Company → Product.

    Used by the LLM proposer when a 10-K discloses a key product line
    (e.g. ``ASML manufactures EUV lithography systems``). Lets the
    mapper run product-level shock paths: a product shortage → all
    companies that MANUFACTURES it → their downstream SUPPLIES_TO.
    """
    driver = await get_driver()
    async with driver.session(**_session_kwargs()) as session:
        await session.run(
            """
            MERGE (p:Product {name: $name})
            SET p.category = $category, p.last_updated = datetime()
            WITH p
            MERGE (c:Company {ticker: $ticker})
            MERGE (c)-[r:MANUFACTURES]->(p)
            SET r.capacity_share_pct = $capacity_share_pct,
                r.last_updated = datetime()
            """,
            ticker=ticker.upper(),
            name=product_name,
            category=category,
            capacity_share_pct=capacity_share_pct,
        )


async def upsert_proposed_edge(
    src_ticker: str,
    dst_ticker: str,
    *,
    proposer_confidence: float = 0.2,
    revenue_dependency_pct: float = 0.10,
    criticality_label: str = "MEDIUM",
    source_filing: str = "",
    proposed_by: str = "",
) -> dict[str, Any]:
    """Upsert an LLM-proposed supply-chain edge with ``confidence-preserving``
    semantics.

    Contract
    ────────
    If the edge does NOT yet exist:
        MERGE it with ``r.confidence = proposer_confidence`` (default 0.2)
        and stamp ``r.confidence_method = "llm_10k_proposal"``.
        Returns ``{"created": True, "confidence_before": None,
                   "confidence_after": proposer_confidence}``.

    If the edge ALREADY exists:
        NEVER overwrite a higher confidence. We only record provenance
        (``r.last_proposed_at``, ``r.proposed_by``, ``r.source_filing``).
        Returns ``{"created": False,
                   "confidence_before": <existing>,
                   "confidence_after": <existing>}``.

    This is the critical invariant behind the proposer ↔ refinery loop:
    a 10-K mention of "Foxconn → Apple" cannot demote a refinery-validated
    edge back to the 0.2 proposal floor. Only CausalFusion writes
    validation confidence; this function only *adds* candidates.
    """
    driver = await get_driver()
    src = src_ticker.upper()
    dst = dst_ticker.upper()
    proposer_confidence = float(max(0.0, min(proposer_confidence, 1.0)))

    async with driver.session(**_session_kwargs()) as session:
        # Two-phase: ask Neo4j whether the edge exists BEFORE we MERGE, so
        # we can honestly report "created" vs "preserved". Cypher MERGE
        # has no per-row create/match flag exposed to clients.
        pre = await session.run(
            """
            OPTIONAL MATCH (a:Company {ticker: $src})-[r:SUPPLIES_TO]->(b:Company {ticker: $dst})
            RETURN r.confidence AS conf,
                   r.confidence_method AS method
            """,
            src=src,
            dst=dst,
        )
        pre_record = await pre.single()
        had_edge = pre_record is not None and pre_record["conf"] is not None
        confidence_before = float(pre_record["conf"]) if had_edge else None

        result = await session.run(
            """
            MERGE (a:Company {ticker: $src})
                ON CREATE SET a.last_updated = datetime()
            MERGE (b:Company {ticker: $dst})
                ON CREATE SET b.last_updated = datetime()
            MERGE (a)-[r:SUPPLIES_TO]->(b)
            ON CREATE SET
                r.confidence = $proposer_conf,
                r.confidence_method = 'llm_10k_proposal',
                r.confidence_updated_at = datetime(),
                r.revenue_dependency_pct = $dep,
                r.criticality_score = $crit,
                r.criticality_label = $crit_label,
                r.source_filing = $source_filing,
                r.proposed_by = $proposed_by,
                r.last_proposed_at = datetime(),
                r.created_via = 'llm_10k_proposal'
            ON MATCH SET
                r.last_proposed_at = datetime(),
                r.source_filing = COALESCE(r.source_filing, $source_filing),
                r.proposed_by = COALESCE(r.proposed_by, $proposed_by)
            RETURN r.confidence AS conf
            """,
            src=src,
            dst=dst,
            proposer_conf=proposer_confidence,
            dep=float(max(0.0, min(revenue_dependency_pct, 1.0))),
            crit=float(max(0.0, min(revenue_dependency_pct * 2, 1.0))),
            crit_label=criticality_label,
            source_filing=source_filing,
            proposed_by=proposed_by,
        )
        record = await result.single()
        if record is None:
            return {
                "created": False,
                "confidence_before": confidence_before,
                "confidence_after": None,
            }

        conf_after = float(record["conf"]) if record["conf"] is not None else None
        return {
            "created": not had_edge,
            "confidence_before": confidence_before,
            "confidence_after": conf_after,
        }


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
            # P2-27: the fallback Cypher used an unbounded
            # shortestPath match over every (a, b) Company pair — on
            # a graph of even modest size (a few thousand nodes) this
            # produces O(n²) paths and becomes an unintended DoS
            # vector for any caller. Bound the sampling: we compute
            # path-count betweenness only across a capped universe of
            # high-PageRank source nodes, and return at most
            # `max_results` rows. Approximations, not exact
            # betweenness, but adequate for ranking.
            log.warning(
                "GDS not available, using bounded path-count "
                "approximation (limits: 500 source nodes, 500 rows)"
            )
            result = await session.run(
                """
                MATCH (src:Company)
                WHERE coalesce(src.pagerank, 0.0) > 0
                WITH src
                ORDER BY coalesce(src.pagerank, 0.0) DESC
                LIMIT 500
                MATCH (c:Company)
                OPTIONAL MATCH path = shortestPath((src)-[:SUPPLIES_TO*..3]->(b:Company))
                WHERE src <> b AND c IN nodes(path) AND c <> src AND c <> b
                WITH c, count(path) AS path_count
                SET c.betweenness_score = toFloat(path_count)
                RETURN c.ticker AS ticker, toFloat(path_count) AS score
                ORDER BY score DESC
                LIMIT 500
                """
            )
            return [dict(r) async for r in result]


async def _resolve_pr_threshold(
    session,
    percentile: float = 0.85,
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
        log.info(f"PageRank cutoff at p{pagerank_percentile * 100:.0f}: {pr_cutoff}")

        result = await session.run(
            query,
            shock_ticker=shock_ticker,
            pr_threshold=pr_cutoff,
            min_risk=min_compound_risk,
            min_edge_confidence=float(min_edge_confidence),
        )

        paths: list[CausalPath] = []
        async for record in result:
            paths.append(
                CausalPath(
                    shocked_ticker=shock_ticker,
                    target_ticker=record["target_ticker"],
                    depth=record["depth"],
                    compound_risk=record["compound_risk"],
                    path_tickers=record["path_tickers"],
                )
            )

        if not paths:
            raise CausalPathNotFoundError(
                f"No valid causal paths from {shock_ticker} with "
                f"compound risk > {min_compound_risk}"
            )

        log.info(f"Found {len(paths)} hidden victims from [bold]{shock_ticker}[/bold] shock")
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
            rows=[{"src": s, "dst": d, "confidence": float(c)} for s, d, c in edge_updates],
            method=method,
        )
        record = await result.single()
        return int(record["updated"]) if record else 0


async def _run_graph_stats_once() -> dict[str, int]:
    """Single-attempt graph-stats query — caller decides retry policy."""
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


async def get_graph_stats() -> dict[str, int]:
    """Return high-level graph statistics with one-shot self-heal.

    If the first attempt fails with a transient resolver/connection
    error we invalidate the cached driver and retry exactly once.
    This lets the backend recover from a boot-time DNS flap without
    needing a process restart — the most common failure mode on a
    laptop that flips between WiFi networks / VPN states.
    """
    try:
        return await _run_graph_stats_once()
    except Exception as exc:
        if not _is_transient_resolver_error(exc):
            raise
        log.warning(
            f"neo4j stats probe failed ({type(exc).__name__}: {str(exc)[:120]}). "
            f"Invalidating driver and retrying once."
        )
        await invalidate_driver()
        return await _run_graph_stats_once()
