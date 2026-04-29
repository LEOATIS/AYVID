"""Dynamic supply-chain critical node discovery via Neo4j graph centrality.

Replaces the hardcoded 34-ticker SUPPLY_CHAIN_CRITICAL list in
universe_builder.py with a live centrality analysis that discovers
which nodes, when disrupted, propagate shocks furthest through the
supply-chain graph.

Three-source discovery pipeline:
  1. Neo4j betweenness centrality (primary — graph topology)
  2. SEC EDGAR supplier mention extraction (secondary — filing text)
  3. Cached fallback (tertiary — last-known list when graph is down)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.logger import get_logger

log = get_logger("graph.critical_nodes")

_CACHE_PATH = Path("data_store/critical_nodes_cache.json")


@dataclass
class CriticalNode:
    """A single dynamically-discovered critical supply-chain node."""

    ticker: str
    centrality: float = 0.0
    source: str = "graph_centrality"
    company_name: str = ""
    sector: str = ""
    is_single_source: bool = False
    asset_class: str = "EQUITY"


class CriticalNodeFinder:
    """Discovers supply-chain critical tickers dynamically from the Neo4j graph.

    Critical = high betweenness centrality (nodes that connect many others).
    These nodes, when disrupted, propagate shocks furthest through the graph.
    """

    def __init__(self, neo4j_client: Any = None):
        self._neo4j = neo4j_client

    async def _get_neo4j_client(self) -> Any:
        if self._neo4j is not None:
            return self._neo4j
        try:
            from graph_intelligence.tools.neo4j_tools import get_client

            self._neo4j = await get_client()
            return self._neo4j
        except Exception:
            return None

    async def discover_critical_nodes(
        self,
        min_centrality_percentile: float = 0.75,
        min_confidence: float = 0.60,
        max_nodes: int = 60,
        enrich_from_filings: bool = False,
        filing_tickers: list[str] | None = None,
    ) -> list[CriticalNode]:
        """Run Neo4j GDS betweenness centrality algorithm.

        Returns top nodes ranked by shock-propagation reach.
        Falls back to degree-centrality estimation if GDS is unavailable.

        If *enrich_from_filings* is True, a secondary enrichment step calls
        ``extract_supplier_mentions`` using the tickers already discovered
        (or *filing_tickers* if provided) and merges the results.
        """
        client = await self._get_neo4j_client()
        if client is None:
            log.warning("neo4j_unavailable — using cached critical nodes")
            return await self._load_cached()

        results: list[CriticalNode] = []

        try:
            # Try GDS betweenness centrality first
            results = await self._run_gds_centrality(client, min_confidence, max_nodes)
        except Exception as exc:
            log.debug(f"GDS centrality failed ({exc}), falling back to degree query")

        if not results:
            try:
                # Fallback: simple degree-based centrality (no GDS required)
                results = await self._run_degree_centrality(client, min_confidence, max_nodes)
            except Exception as exc:
                log.warning(f"degree centrality also failed: {exc}")

        if not results:
            results = await self._load_cached()

        # --- Secondary enrichment: SEC EDGAR supplier mentions ---
        if enrich_from_filings:
            seed_tickers = filing_tickers or [n.ticker for n in results]
            if seed_tickers:
                try:
                    edgar_nodes = await self.extract_supplier_mentions(seed_tickers)
                    existing_tickers = {n.ticker for n in results}
                    for node in edgar_nodes:
                        if node.ticker not in existing_tickers:
                            results.append(node)
                            existing_tickers.add(node.ticker)
                    log.info(
                        f"enrichment added {len(edgar_nodes)} nodes from SEC filings"
                    )
                except Exception as exc:
                    log.warning(f"SEC filing enrichment failed: {exc}")

        if results:
            await self._cache_nodes(results)

        return results

    async def _run_gds_centrality(
        self, client: Any, min_confidence: float, max_nodes: int
    ) -> list[CriticalNode]:
        """Run GDS betweenness centrality (requires Neo4j Graph Data Science plugin)."""
        query = """
        CALL gds.betweenness.stream('supply-chain-graph', {
            relationshipWeightProperty: 'confidence'
        })
        YIELD nodeId, score
        WHERE score > 0
        MATCH (n) WHERE id(n) = nodeId AND n.confidence >= $min_confidence
        RETURN n.ticker AS ticker, n.company_name AS name,
               n.sector AS sector, score AS centrality_score
        ORDER BY centrality_score DESC
        LIMIT $max_nodes
        """
        records = await client.run(
            query, min_confidence=min_confidence, max_nodes=max_nodes
        )
        return [
            CriticalNode(
                ticker=r["ticker"],
                centrality=r["centrality_score"],
                company_name=r.get("name", ""),
                sector=r.get("sector", ""),
                source="gds_betweenness",
            )
            for r in records
            if r.get("ticker")
        ]

    async def _run_degree_centrality(
        self, client: Any, min_confidence: float, max_nodes: int
    ) -> list[CriticalNode]:
        """Simple degree-based centrality — counts incoming + outgoing edges."""
        query = """
        MATCH (n:Company)
        WHERE n.ticker IS NOT NULL
        OPTIONAL MATCH (n)-[r]-()
        WHERE r.confidence >= $min_confidence OR r.confidence IS NULL
        WITH n, count(r) AS degree
        WHERE degree > 0
        RETURN n.ticker AS ticker, n.company_name AS name,
               n.sector AS sector, degree AS centrality_score
        ORDER BY centrality_score DESC
        LIMIT $max_nodes
        """
        records = await client.run(
            query, min_confidence=min_confidence, max_nodes=max_nodes
        )
        return [
            CriticalNode(
                ticker=r["ticker"],
                centrality=float(r["centrality_score"]),
                company_name=r.get("name", ""),
                sector=r.get("sector", ""),
                source="degree_centrality",
            )
            for r in records
            if r.get("ticker")
        ]

    async def extract_supplier_mentions(
        self, tickers: list[str], max_filings: int = 5
    ) -> list[CriticalNode]:
        """Extract supplier/customer mentions from SEC 10-K filings.

        For each ticker, fetches EDGAR filing data via the full_edgar_pipeline
        and extracts mentioned suppliers/customers as CriticalNode objects.
        """
        from data_primitives.extractors.sec_edgar import full_edgar_pipeline

        nodes: list[CriticalNode] = []
        seen_tickers: set[str] = set()

        for ticker in tickers[:max_filings]:
            try:
                log.info(f"fetching EDGAR filings for {ticker}")
                filings = await full_edgar_pipeline(ticker)
                if not filings:
                    log.debug(f"no filings returned for {ticker}")
                    continue

                for filing in filings:
                    # Extract supplier/customer mentions from filing analysis
                    suppliers = filing.get("suppliers", [])
                    customers = filing.get("customers", [])
                    mentioned = filing.get("supply_chain_mentions", [])

                    all_mentions = suppliers + customers + mentioned
                    for mention in all_mentions:
                        # mentions can be dicts with a ticker key or plain strings
                        if isinstance(mention, dict):
                            mentioned_ticker = mention.get("ticker", "")
                            company_name = mention.get("company_name", mention.get("name", ""))
                            sector = mention.get("sector", "")
                        else:
                            mentioned_ticker = str(mention).upper().strip()
                            company_name = ""
                            sector = ""

                        if not mentioned_ticker or mentioned_ticker in seen_tickers:
                            continue

                        seen_tickers.add(mentioned_ticker)
                        nodes.append(
                            CriticalNode(
                                ticker=mentioned_ticker,
                                centrality=0.0,
                                source="sec_edgar_filing",
                                company_name=company_name,
                                sector=sector,
                                is_single_source=True,
                                asset_class="EQUITY",
                            )
                        )

            except Exception as exc:
                log.warning(f"EDGAR extraction failed for {ticker}: {exc}")
                continue

        log.info(f"extracted {len(nodes)} supplier/customer mentions from SEC filings")
        return nodes

    async def _cache_nodes(self, nodes: list[CriticalNode]) -> None:
        """Persist discovered nodes for offline fallback."""
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "ticker": n.ticker,
                "centrality": n.centrality,
                "source": n.source,
                "company_name": n.company_name,
                "sector": n.sector,
            }
            for n in nodes
        ]
        _CACHE_PATH.write_text(json.dumps(data, indent=2))
        log.info(f"cached {len(data)} critical nodes to {_CACHE_PATH}")

    async def _load_cached(self) -> list[CriticalNode]:
        """Load last-known critical nodes from disk cache."""
        if not _CACHE_PATH.exists():
            log.info("no critical node cache — returning empty list")
            return []
        try:
            data = json.loads(_CACHE_PATH.read_text())
            nodes = [
                CriticalNode(
                    ticker=d["ticker"],
                    centrality=d.get("centrality", 0.0),
                    source="cache",
                    company_name=d.get("company_name", ""),
                    sector=d.get("sector", ""),
                )
                for d in data
            ]
            log.info(f"loaded {len(nodes)} critical nodes from cache")
            return nodes
        except Exception as exc:
            log.warning(f"failed to load critical node cache: {exc}")
            return []


# Module-level singleton
_finder: CriticalNodeFinder | None = None


def get_finder() -> CriticalNodeFinder:
    global _finder
    if _finder is None:
        _finder = CriticalNodeFinder()
    return _finder


async def discover_critical_nodes(
    max_nodes: int = 60,
    min_confidence: float = 0.60,
    enrich_from_filings: bool = False,
    filing_tickers: list[str] | None = None,
) -> list[CriticalNode]:
    """Public API: discover supply-chain critical nodes dynamically."""
    return await get_finder().discover_critical_nodes(
        max_nodes=max_nodes,
        min_confidence=min_confidence,
        enrich_from_filings=enrich_from_filings,
        filing_tickers=filing_tickers,
    )
