"""CausalFusion graph-falsification refinery (Phase 2).

Iterative LLM ↔ statistical-test loop that refines the supply-chain
causal DAG against observational price-return data. Every edge in
Neo4j receives a statistically-validated ``confidence`` score, and
the mapper consumes edges with ``min_edge_confidence`` filtering.

Reference: *CausalFusion: Integrating LLMs and Graph Falsification
for Causal Discovery* (ICLR 2026 submission #19725).

Runs offline (weekly cron / ``ayvid graph refine``) — never in the
hot decision path.
"""

from graph_intelligence.causal_falsification.falsification_tests import (
    FalsificationReport,
    random_graph_benchmark,
    run_all_tests,
    test_causal_minimality,
    test_conditional_independence,
)
from graph_intelligence.causal_falsification.refinery import (
    EdgeProposal,
    RefinedDAG,
    refine_dag,
)

__all__ = [
    "FalsificationReport",
    "test_conditional_independence",
    "test_causal_minimality",
    "random_graph_benchmark",
    "run_all_tests",
    "EdgeProposal",
    "RefinedDAG",
    "refine_dag",
]
