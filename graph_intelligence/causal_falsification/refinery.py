"""Iterative LLM ↔ falsification loop for DAG refinement.

Contract
────────
    refine_dag(edges, returns, *, alpha=5, gamma=0.80) -> RefinedDAG

Each iteration:
  1. Run full falsification pass over the current edge list.
  2. Render the report as prompt context.
  3. Ask the LLM (acting as a domain-specialist causal-inference
     researcher) for a refined edge list + confidence.
  4. If ``self_confidence ≥ gamma`` AND ``ci_fraction_violated``
     declined vs. previous iteration → accept refinement.
     Otherwise rollback iteration, increment counter, retry.

Stops after ``alpha`` iterations or when a stable high-confidence
DAG is reached.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from graph_intelligence.causal_falsification.falsification_tests import (
    FalsificationReport,
    run_all_tests,
)
from shared.logger import get_logger

log = get_logger("causal.refinery")


# ── Pydantic schemas ─────────────────────────────────────────────────
class EdgeProposal(BaseModel):
    src: str
    dst: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""
    # Multi-asset: track what kind of instrument each node is
    src_asset_class: str = "EQUITY"  # EQUITY|FX|COMMODITY|CRYPTO|ETF|BOND
    dst_asset_class: str = "EQUITY"


class RefinementDecision(BaseModel):
    """LLM output per iteration."""

    refined_edges: list[EdgeProposal] = Field(default_factory=list)
    removed_edges: list[tuple[str, str]] = Field(default_factory=list)
    rationale: str = ""
    self_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


@dataclass
class RefinedDAG:
    """Final output of the refinery loop."""

    edges: list[EdgeProposal] = field(default_factory=list)
    iterations_run: int = 0
    last_report: FalsificationReport | None = None
    converged: bool = False
    rationale_trail: list[str] = field(default_factory=list)


# ── LLM prompts ──────────────────────────────────────────────────────
_REFINERY_SYSTEM = """\
You are a senior causal-inference data scientist specialising in
financial supply-chain graphs. Your job is to iteratively refine a
candidate DAG based on three sources of evidence:

  1. Domain knowledge — supply-chain relationships, regulatory and
     contractual links, production-input dependencies.
  2. Conditional-independence tests (partial correlation, Gaussian
     residuals) on historical returns — reported below as p-values
     at α = 0.05.
  3. Causal-minimality tests — edges flagged ``unnecessary`` are
     candidates for pruning because their removal does not reduce
     the implied conditional independences in the data.

Critical caveats (apply ALL of these when reasoning):

  • Falsification is ONE-SIDED — a test can reject a DAG but cannot
    confirm it.
  • Markov Equivalence Class: many DAGs imply identical CIs. You
    cannot resolve edge direction from observational tests alone.
  • Unobserved confounders may validate an incorrect DAG.
  • Return data is NOT Gaussian (heavy tails); treat partial-
    correlation p-values as approximate, not exact.
  • An edge may exist for non-statistical reasons (contractual,
    regulatory, strategic) — do NOT prune every statistically
    silent edge; use domain judgement.

Output STRICT JSON matching the ``RefinementDecision`` schema. Your
``self_confidence`` reflects how well the refined DAG aligns with
the combined evidence — NOT how certain you are that the final
graph is ground truth.
"""


def _render_report(report: FalsificationReport) -> str:
    """Compact human/LLM-readable rendering of the falsification report."""
    parts = [
        f"edges_tested: {report.edges_tested}",
        f"ci_violations: {len(report.ci_violations)} (fraction {report.ci_fraction_violated:.2%})",
        f"unnecessary_edges: {len(report.unnecessary_edges)}",
        f"random_benchmark_p: {report.random_benchmark_p:.3f}",
        f"falsifiable: {report.falsifiable}",
        f"falsified_by_benchmark: {report.falsified_by_benchmark}",
    ]
    # Show a sample of the problematic edges so the LLM has data to reason on.
    if report.ci_violations:
        parts.append("\nSample CI violations (edges with p >= 0.05):")
        for ci in report.ci_violations[:8]:
            parts.append(
                f"  {ci.src} → {ci.dst} | cond={list(ci.conditioning_set)} "
                f"| r={ci.partial_corr:+.2f} p={ci.p_value:.3f}"
            )
    if report.unnecessary_edges:
        parts.append("\nSample unnecessary edges:")
        for m in report.unnecessary_edges[:8]:
            parts.append(f"  {m.src} → {m.dst} (p after removal = {m.p_value_after_removal:.3f})")
    return "\n".join(parts)


def _build_llm():
    """LLM bound to RefinementDecision. Uses the deep-reasoning tier."""
    from shared.llm_client import get_langchain_chat_model

    return get_langchain_chat_model(
        temperature=0.05,
        max_tokens=2000,
    ).with_structured_output(RefinementDecision)


# ── Loop orchestrator ────────────────────────────────────────────────
def _edges_to_tuples(proposals: Iterable[EdgeProposal]) -> list[tuple[str, str]]:
    return [(e.src, e.dst) for e in proposals]


def _apply_decision(
    current: list[EdgeProposal],
    decision: RefinementDecision,
) -> list[EdgeProposal]:
    """Merge an LLM decision into the current edge list.

    - Removed edges are dropped.
    - Refined edges REPLACE any existing entry with the same (src, dst)
      key (so confidence can be updated), or are appended new.
    """
    removed_set = {(s, d) for s, d in decision.removed_edges}
    by_key = {(e.src, e.dst): e for e in current if (e.src, e.dst) not in removed_set}
    for e in decision.refined_edges:
        by_key[(e.src, e.dst)] = e
    return list(by_key.values())


async def refine_dag(
    initial_edges: list[EdgeProposal],
    returns: dict[str, np.ndarray],
    *,
    alpha: int = 5,
    gamma: float = 0.80,
    benchmark_permutations: int = 100,
) -> RefinedDAG:
    """Run the refinery loop for up to ``alpha`` iterations.

    Early-exit conditions:
      • ``is_valid`` AND ``self_confidence ≥ gamma`` → converged.
      • No change in edge list across consecutive iterations → converged.
    """
    if not initial_edges:
        return RefinedDAG(edges=[], iterations_run=0, converged=True)

    current_edges = list(initial_edges)
    rationale_trail: list[str] = []
    last_report: FalsificationReport | None = None

    try:
        llm = _build_llm()
    except Exception as exc:
        log.warning(f"refinery: LLM init failed ({exc}); returning priors untouched")
        return RefinedDAG(
            edges=current_edges,
            iterations_run=0,
            last_report=None,
            converged=False,
            rationale_trail=[f"LLM unavailable: {exc}"],
        )

    for iteration in range(1, alpha + 1):
        report = run_all_tests(
            _edges_to_tuples(current_edges),
            returns,
            benchmark_permutations=benchmark_permutations,
        )
        last_report = report
        log.info(
            f"refinery iter {iteration}/{alpha}: "
            f"ci_violated={report.ci_fraction_violated:.2%}, "
            f"unnecessary={len(report.unnecessary_edges)}, "
            f"falsified={report.falsified_by_benchmark}"
        )

        # Short-circuit: already valid.
        if report.is_valid:
            rationale_trail.append(f"iter {iteration}: DAG passes falsification; stopping early.")
            return RefinedDAG(
                edges=current_edges,
                iterations_run=iteration,
                last_report=report,
                converged=True,
                rationale_trail=rationale_trail,
            )

        # Ask LLM to propose refinements.
        prompt = (
            f"Current edge count: {len(current_edges)}\n"
            f"Current edges (first 40):\n"
            + "\n".join(
                f"  {e.src} → {e.dst} (conf={e.confidence:.2f})" for e in current_edges[:40]
            )
            + f"\n\nFalsification report:\n{_render_report(report)}\n\n"
            "Propose refinements: edges to keep with updated confidence, "
            "edges to remove, and a rationale. Output JSON."
        )

        try:
            decision: RefinementDecision = await llm.ainvoke(
                [
                    SystemMessage(content=_REFINERY_SYSTEM),
                    HumanMessage(content=prompt),
                ]
            )
        except (ValidationError, ValueError, TypeError) as exc:
            log.warning(f"refinery iter {iteration}: LLM validation failed ({exc})")
            rationale_trail.append(f"iter {iteration}: validation failure")
            break
        except Exception as exc:
            log.warning(f"refinery iter {iteration}: LLM error ({exc})")
            rationale_trail.append(f"iter {iteration}: LLM error ({exc})")
            break

        if not isinstance(decision, RefinementDecision):
            rationale_trail.append(f"iter {iteration}: non-Decision return")
            break

        rationale_trail.append(
            f"iter {iteration} (conf={decision.self_confidence:.2f}): {decision.rationale[:200]}"
        )

        if decision.self_confidence < gamma:
            log.info(
                f"refinery iter {iteration}: self_confidence "
                f"{decision.self_confidence:.2f} < gamma {gamma}; skipping apply"
            )
            continue

        proposed_edges = _apply_decision(current_edges, decision)
        if not proposed_edges:
            log.warning("refinery: LLM returned empty edge set; rolling back")
            continue

        # Measure improvement.
        tentative_report = run_all_tests(
            _edges_to_tuples(proposed_edges),
            returns,
            benchmark_permutations=benchmark_permutations,
        )
        if tentative_report.ci_fraction_violated <= report.ci_fraction_violated:
            current_edges = proposed_edges
            last_report = tentative_report
            if tentative_report.is_valid:
                rationale_trail.append(
                    f"iter {iteration}: refinement accepted AND valid; stopping."
                )
                return RefinedDAG(
                    edges=current_edges,
                    iterations_run=iteration,
                    last_report=tentative_report,
                    converged=True,
                    rationale_trail=rationale_trail,
                )
        else:
            log.info(
                f"refinery iter {iteration}: refinement worsened CI "
                f"({report.ci_fraction_violated:.2%} → "
                f"{tentative_report.ci_fraction_violated:.2%}); rolling back"
            )

    return RefinedDAG(
        edges=current_edges,
        iterations_run=alpha,
        last_report=last_report,
        converged=False,
        rationale_trail=rationale_trail,
    )
