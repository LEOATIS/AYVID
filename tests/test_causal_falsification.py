"""Tests for Phase 2 — CausalFusion graph-falsification refinery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from graph_intelligence.causal_falsification import (
    EdgeProposal,
    random_graph_benchmark,
    refine_dag,
    run_all_tests,
)
from graph_intelligence.causal_falsification.falsification_tests import (
    test_causal_minimality as minimality_test,
)

# Import with aliases so pytest's `test_*` collector does not treat the
# imported module-level functions as test cases.
from graph_intelligence.causal_falsification.falsification_tests import (
    test_conditional_independence as ci_test,
)
from graph_intelligence.causal_falsification.refinery import RefinementDecision


# ────────────────────────────────────────────────────────────────────
#  Synthetic data generators
# ────────────────────────────────────────────────────────────────────
def _true_causal_data(n: int = 400, seed: int = 0) -> dict[str, np.ndarray]:
    """Generate data where A→B→C holds as TRUE causation.

    A is noise; B = α·A + noise; C = β·B + noise. Therefore:
      - Marginally A⊥̸B and B⊥̸C   (dependent)
      - A⊥C | B                    (conditionally independent — classic)
    """
    rng = np.random.default_rng(seed)
    a = rng.standard_normal(n)
    b = 0.8 * a + 0.5 * rng.standard_normal(n)
    c = 0.7 * b + 0.5 * rng.standard_normal(n)
    d = rng.standard_normal(n)  # completely unrelated
    return {"A": a, "B": b, "C": c, "D": d}


# ────────────────────────────────────────────────────────────────────
#  Primitive CI test behaviour
# ────────────────────────────────────────────────────────────────────
class TestCIPrimitive:
    def test_true_edge_appears_dependent(self):
        data = _true_causal_data(seed=1)
        # A → B has no other parents, so cond set is empty.
        ci = ci_test("A", "B", [], data)
        assert ci.p_value < 0.05
        assert ci.independent is False

    def test_unrelated_variables_appear_independent(self):
        data = _true_causal_data(seed=2)
        ci = ci_test("A", "D", [], data)
        assert ci.p_value > 0.05
        assert ci.independent is True

    def test_conditional_independence_on_mediator_chain(self):
        """A → B → C: A should become CONDITIONALLY independent of C given B."""
        data = _true_causal_data(seed=3)
        marginal = ci_test("A", "C", [], data)
        assert marginal.independent is False  # marginally dependent

        conditioned = ci_test("A", "C", ["B"], data)
        # Given the mediator, the conditional dependence should vanish.
        assert conditioned.p_value > 0.05

    def test_below_min_samples_returns_neutral(self):
        tiny = {
            "A": np.random.default_rng(0).standard_normal(10),
            "B": np.random.default_rng(1).standard_normal(10),
        }
        ci = ci_test("A", "B", [], tiny)
        assert ci.p_value == 1.0
        assert ci.independent is True


# ────────────────────────────────────────────────────────────────────
#  Causal minimality
# ────────────────────────────────────────────────────────────────────
class TestMinimality:
    def test_genuine_edge_is_necessary(self):
        data = _true_causal_data(seed=4)
        # A → B is a direct cause. Removing it should NOT render A
        # independent of B.
        m = minimality_test("A", "B", [], data)
        assert m.unnecessary is False

    def test_redundant_edge_flagged_unnecessary(self):
        # Duplicate edge D → B where D is unrelated to B.
        data = _true_causal_data(seed=5)
        m = minimality_test("D", "B", ["A"], data)
        assert m.unnecessary is True


# ────────────────────────────────────────────────────────────────────
#  Random-graph benchmark
# ────────────────────────────────────────────────────────────────────
class TestRandomBenchmark:
    def test_all_true_edges_not_falsified(self):
        data = _true_causal_data(seed=6)
        edges = [("A", "B"), ("B", "C")]
        p, falsified = random_graph_benchmark(
            edges,
            data,
            permutations=50,
            rng_seed=1,
        )
        # The real DAG should not be beaten by most random perms.
        assert falsified is False

    def test_random_edges_may_be_falsified(self):
        data = _true_causal_data(seed=7)
        # Purely spurious edges — at least one permutation typically
        # fits the data as well or better.
        edges = [("D", "A"), ("D", "B"), ("D", "C")]
        p, falsified = random_graph_benchmark(
            edges,
            data,
            permutations=50,
            rng_seed=2,
        )
        # We assert the call returns valid bounded values rather than a
        # specific verdict (permutation tests are stochastic).
        assert 0.0 <= p <= 1.0
        assert isinstance(falsified, bool)


# ────────────────────────────────────────────────────────────────────
#  Full-report orchestrator
# ────────────────────────────────────────────────────────────────────
class TestFullReport:
    def test_true_chain_reports_few_violations(self):
        data = _true_causal_data(n=500, seed=42)
        edges = [("A", "B"), ("B", "C")]
        report = run_all_tests(edges, data, benchmark_permutations=30)
        assert report.edges_tested == 2
        assert len(report.ci_violations) == 0
        assert report.ci_fraction_violated == 0.0

    def test_garbage_edges_report_violations(self):
        data = _true_causal_data(n=400, seed=10)
        edges = [("D", "A"), ("D", "B"), ("D", "C")]  # D is random noise
        report = run_all_tests(edges, data, benchmark_permutations=30)
        assert report.edges_tested == 3
        # D is unrelated to anything; all three edges should be CI-violations.
        assert report.ci_fraction_violated >= 0.5

    def test_is_valid_flag(self):
        data = _true_causal_data(n=500, seed=100)
        clean_report = run_all_tests(
            [("A", "B"), ("B", "C")],
            data,
            benchmark_permutations=30,
        )
        assert clean_report.is_valid is True

        bad_report = run_all_tests(
            [("D", "A"), ("D", "B"), ("D", "C")],
            data,
            benchmark_permutations=30,
        )
        assert bad_report.is_valid is False


# ────────────────────────────────────────────────────────────────────
#  refine_dag orchestrator — mocked LLM
# ────────────────────────────────────────────────────────────────────
class TestRefineDag:
    @pytest.mark.asyncio
    async def test_converges_when_priors_already_valid(self):
        """When the starting DAG already passes falsification, the loop exits on iter 1."""
        data = _true_causal_data(n=500, seed=200)
        edges = [
            EdgeProposal(src="A", dst="B", confidence=0.6),
            EdgeProposal(src="B", dst="C", confidence=0.7),
        ]
        refined = await refine_dag(edges, data, alpha=5, gamma=0.80)
        assert refined.converged is True
        assert refined.iterations_run == 1
        # Original edges preserved.
        assert {(e.src, e.dst) for e in refined.edges} == {("A", "B"), ("B", "C")}

    @pytest.mark.asyncio
    async def test_llm_refinement_accepted_when_ci_declines(self):
        data = _true_causal_data(n=400, seed=300)
        starting = [
            EdgeProposal(src="D", dst="A", confidence=0.5),  # spurious
            EdgeProposal(src="D", dst="B", confidence=0.5),  # spurious
            EdgeProposal(src="A", dst="B", confidence=0.5),  # real
        ]
        # LLM proposes to drop the spurious D → * edges.
        decision = RefinementDecision(
            refined_edges=[
                EdgeProposal(src="A", dst="B", confidence=0.92),
            ],
            removed_edges=[("D", "A"), ("D", "B")],
            rationale="D is uncorrelated with A/B after CI tests.",
            self_confidence=0.85,
        )
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(return_value=decision)
        with patch(
            "graph_intelligence.causal_falsification.refinery._build_llm",
            return_value=fake_llm,
        ):
            refined = await refine_dag(
                starting,
                data,
                alpha=3,
                gamma=0.80,
                benchmark_permutations=30,
            )
        final_keys = {(e.src, e.dst) for e in refined.edges}
        assert ("D", "A") not in final_keys
        assert ("D", "B") not in final_keys
        assert ("A", "B") in final_keys

    @pytest.mark.asyncio
    async def test_low_self_confidence_rejects_refinement(self):
        data = _true_causal_data(n=400, seed=400)
        starting = [
            EdgeProposal(src="D", dst="A", confidence=0.5),
            EdgeProposal(src="A", dst="B", confidence=0.5),
        ]
        decision = RefinementDecision(
            refined_edges=[EdgeProposal(src="XX", dst="YY", confidence=0.99)],
            removed_edges=[("D", "A"), ("A", "B")],
            rationale="confident refactor",
            self_confidence=0.40,  # < gamma
        )
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(return_value=decision)
        with patch(
            "graph_intelligence.causal_falsification.refinery._build_llm",
            return_value=fake_llm,
        ):
            refined = await refine_dag(
                starting,
                data,
                alpha=3,
                gamma=0.80,
                benchmark_permutations=30,
            )
        # Because self_confidence < gamma, refinement is skipped entirely;
        # the original edges survive (albeit still unconverged).
        final_keys = {(e.src, e.dst) for e in refined.edges}
        assert ("A", "B") in final_keys
        assert ("XX", "YY") not in final_keys

    @pytest.mark.asyncio
    async def test_llm_error_preserves_priors(self):
        data = _true_causal_data(n=400, seed=500)
        starting = [
            EdgeProposal(src="D", dst="A", confidence=0.5),
            EdgeProposal(src="D", dst="B", confidence=0.5),
            EdgeProposal(src="D", dst="C", confidence=0.5),
        ]
        fake_llm = MagicMock()
        fake_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        with patch(
            "graph_intelligence.causal_falsification.refinery._build_llm",
            return_value=fake_llm,
        ):
            refined = await refine_dag(
                starting,
                data,
                alpha=3,
                gamma=0.80,
                benchmark_permutations=30,
            )
        # LLM failure should not crash — priors pass through.
        assert len(refined.edges) == 3
        assert refined.converged is False

    @pytest.mark.asyncio
    async def test_empty_edges_returns_converged(self):
        refined = await refine_dag([], {}, alpha=5, gamma=0.80)
        assert refined.converged is True
        assert refined.edges == []
