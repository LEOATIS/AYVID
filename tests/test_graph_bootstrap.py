"""Tests for ``auto_bootstrap_graph`` — the runner's boot-time hook
that replaces the operator-run ``ayvid graph init`` + ``ayvid graph
seed`` pair so production never sits with a schema-less Neo4j.

Idempotency is the contract that matters: running on a populated
graph must NOT duplicate edges, and a partial Neo4j outage during
bootstrap must NOT take down the trading loop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestAutoBootstrapGraph:
    @pytest.mark.asyncio
    async def test_empty_graph_seeds_supply_chain(self):
        from graph_intelligence.tools import neo4j_tools

        with patch.object(
            neo4j_tools, "initialize_schema", new=AsyncMock(return_value=None)
        ) as mock_schema, patch.object(
            neo4j_tools,
            "get_graph_stats",
            new=AsyncMock(return_value={"companies": 0, "edges": 0}),
        ) as mock_stats, patch.object(
            neo4j_tools,
            "bulk_ingest_supply_chain",
            new=AsyncMock(return_value=109),
        ) as mock_seed:
            result = await neo4j_tools.auto_bootstrap_graph()

        assert result["schema_initialized"] is True
        assert result["edges_seeded"] == 109
        assert result["seed_skipped_reason"] is None
        assert result["errors"] == []
        mock_schema.assert_awaited_once()
        mock_stats.assert_awaited_once()
        mock_seed.assert_awaited_once()
        # Verify the seed payload was the curated GLOBAL_SUPPLY_CHAIN —
        # not a hardcoded placeholder.
        seeded_edges = mock_seed.call_args.args[0]
        from graph_intelligence.tools.seed_supply_chain import GLOBAL_SUPPLY_CHAIN

        # Filter matches the bootstrap helper: dep > 0
        expected = [t for t in GLOBAL_SUPPLY_CHAIN if t[2] > 0]
        assert len(seeded_edges) == len(expected)

    @pytest.mark.asyncio
    async def test_populated_graph_skips_seed(self):
        """Running on a graph that already has edges must NOT re-seed —
        otherwise every container restart would create duplicate edges
        (constraints prevent duplicate Companies, but SUPPLIES_TO has
        no uniqueness guard) and the graph degenerates over time."""
        from graph_intelligence.tools import neo4j_tools

        with patch.object(
            neo4j_tools, "initialize_schema", new=AsyncMock(return_value=None)
        ), patch.object(
            neo4j_tools,
            "get_graph_stats",
            new=AsyncMock(return_value={"companies": 855, "edges": 109}),
        ), patch.object(
            neo4j_tools,
            "bulk_ingest_supply_chain",
            new=AsyncMock(return_value=0),
        ) as mock_seed:
            result = await neo4j_tools.auto_bootstrap_graph()

        assert result["schema_initialized"] is True
        assert result["edges_seeded"] == 0
        assert "graph_already_populated" in (result["seed_skipped_reason"] or "")
        mock_seed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_schema_failure_blocks_seed(self):
        """If schema init fails we must NOT seed — without uniqueness
        constraints the seed would create duplicate Company nodes that
        the rest of the pipeline merges incorrectly."""
        from graph_intelligence.tools import neo4j_tools

        with patch.object(
            neo4j_tools,
            "initialize_schema",
            new=AsyncMock(side_effect=RuntimeError("neo4j unreachable")),
        ), patch.object(
            neo4j_tools,
            "get_graph_stats",
            new=AsyncMock(return_value={"companies": 0, "edges": 0}),
        ) as mock_stats, patch.object(
            neo4j_tools,
            "bulk_ingest_supply_chain",
            new=AsyncMock(return_value=0),
        ) as mock_seed:
            result = await neo4j_tools.auto_bootstrap_graph()

        assert result["schema_initialized"] is False
        assert result["edges_seeded"] == 0
        assert any("schema:" in err for err in result["errors"])
        mock_stats.assert_not_awaited()
        mock_seed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_seed_failure_does_not_raise(self):
        """A failed seed (e.g. mid-way Neo4j hiccup) must surface in the
        return dict but never raise — the runner's startup path
        explicitly cannot tolerate a graph-bootstrap exception
        propagating, or the trading loop never starts."""
        from graph_intelligence.tools import neo4j_tools

        with patch.object(
            neo4j_tools, "initialize_schema", new=AsyncMock(return_value=None)
        ), patch.object(
            neo4j_tools,
            "get_graph_stats",
            new=AsyncMock(return_value={"companies": 0, "edges": 0}),
        ), patch.object(
            neo4j_tools,
            "bulk_ingest_supply_chain",
            new=AsyncMock(side_effect=ConnectionError("aura timeout")),
        ):
            result = await neo4j_tools.auto_bootstrap_graph()

        assert result["schema_initialized"] is True
        assert result["edges_seeded"] == 0
        assert any("seed:" in err for err in result["errors"])
