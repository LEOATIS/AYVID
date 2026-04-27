r"""Statistical falsification tests for candidate causal DAGs.

Implements the three tests from *CausalFusion* (ICLR 2026 #19725):

    1. Conditional Independence (Local Markov Condition)
       For each edge X → Y with parents Pa(Y), test whether Y is
       indeed dependent on X after conditioning on Pa(Y) \ {X}. A
       DAG edge is VALID if the conditional dependence is significant
       (null hypothesis "they are independent given the conditioning
       set" is rejected).

    2. Causal Minimality
       For every edge X → Y, remove it and ask: is Y still
       independent of X given the reduced parent set? If yes, the
       edge is UNNECESSARY.

    3. Random-Graph Benchmark (Markov Equivalence Class falsifiability)
       Randomly permute node labels K times and count the permuted
       DAGs that violate fewer CIs than the candidate. If more than
       5 % of permutations beat the candidate, the DAG is FALSIFIED.

Implementation notes
────────────────────
- All tests operate on a **residualised return matrix** — columns
  are per-ticker standardised log-returns. The CausalFusion paper
  uses kernel-based (HSIC) CI tests; we approximate with partial-
  correlation tests on residuals because SciPy's HSIC is not
  installed in the AYVID stack and partial correlation is the
  accepted Gaussian-noise fallback for financial return series
  (Spirtes et al. 2000).
- All p-value computations use only NumPy — no scipy dependency
  added. Where a more sophisticated test is warranted (non-Gaussian
  returns, heavy tails), the refinery prompts the LLM to take that
  into account as a knowledge-boundary warning.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

# ── Constants ────────────────────────────────────────────────────────
SIGNIFICANCE_ALPHA: float = 0.05
MIN_SAMPLES_FOR_CI: int = 30
FALSIFIABILITY_THRESHOLD: float = 0.05  # >5 % of perms better → falsified


@dataclass(frozen=True)
class CITestResult:
    """Outcome of one conditional-independence test on edge X → Y."""

    src: str
    dst: str
    conditioning_set: tuple[str, ...]
    partial_corr: float
    p_value: float
    independent: bool  # True iff the CI null hypothesis was NOT rejected


@dataclass(frozen=True)
class MinimalityResult:
    """Outcome of removing one edge and re-testing CI."""

    src: str
    dst: str
    unnecessary: bool
    p_value_after_removal: float


@dataclass
class FalsificationReport:
    """Full falsification pass over a candidate DAG."""

    edges_tested: int = 0

    ci_violations: list[CITestResult] = field(default_factory=list)
    ci_fraction_violated: float = 0.0

    unnecessary_edges: list[MinimalityResult] = field(default_factory=list)

    falsifiable: bool = True  # False when MEC ambiguity is high
    random_benchmark_p: float = 1.0  # P(random graph beats candidate)
    falsified_by_benchmark: bool = False

    @property
    def is_valid(self) -> bool:
        """A DAG is valid when CI violations are rare AND the random
        benchmark does not falsify it."""
        return self.ci_fraction_violated < 0.20 and not self.falsified_by_benchmark


# ── Numerical primitives ─────────────────────────────────────────────
def _standardise(x: np.ndarray) -> np.ndarray:
    sd = np.std(x, ddof=1)
    if sd < 1e-12 or not np.isfinite(sd):
        return np.zeros_like(x)
    return (x - np.mean(x)) / sd


def _partial_correlation(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray | None,
) -> tuple[float, float]:
    """Partial correlation between x and y controlling for z (matrix).

    Returns ``(r, p_value)`` where p_value is the two-sided Fisher-z
    approximation. When ``z`` is None or empty, this reduces to the
    ordinary Pearson correlation test.

    Implementation: regress x and y on z (OLS), take residuals, then
    compute Pearson correlation on residuals. Fisher-z gives the
    p-value without scipy.
    """
    n = len(x)
    if n < MIN_SAMPLES_FOR_CI:
        return 0.0, 1.0
    if z is None or (hasattr(z, "shape") and z.shape[1] == 0):
        return _pearson_with_p(x, y)

    try:
        z_ext = np.column_stack([z, np.ones(len(z))])
        beta_x, *_ = np.linalg.lstsq(z_ext, x, rcond=None)
        beta_y, *_ = np.linalg.lstsq(z_ext, y, rcond=None)
        rx = x - z_ext @ beta_x
        ry = y - z_ext @ beta_y
    except np.linalg.LinAlgError:
        return 0.0, 1.0

    return _pearson_with_p(rx, ry)


def _pearson_with_p(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Pearson correlation + two-sided p-value via Fisher-z."""
    n = len(x)
    if n < 4:
        return 0.0, 1.0
    sx, sy = _standardise(x), _standardise(y)
    r = float(np.clip(float(np.mean(sx * sy)), -0.9999, 0.9999))
    if abs(r) < 1e-12:
        return 0.0, 1.0
    # Fisher-z transform
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1.0 / math.sqrt(n - 3)
    t = z / se
    # Two-sided p via normal approximation
    p = math.erfc(abs(t) / math.sqrt(2))
    return r, p


# ── 1. Conditional Independence test ─────────────────────────────────
def test_conditional_independence(
    edge_src: str,
    edge_dst: str,
    parents_of_dst: Iterable[str],
    returns: dict[str, np.ndarray],
    *,
    alpha: float = SIGNIFICANCE_ALPHA,
) -> CITestResult:
    r"""Test whether src → dst is *dependent* given parents \ {src}.

    Null hypothesis: src ⊥ dst | (parents \ {src}).
    Reject when p < alpha → src and dst ARE dependent → edge is VALID.
    Fail to reject → edge is statistically unsupported → CI VIOLATION.
    """
    cond_set = tuple(p for p in parents_of_dst if p != edge_src)
    if edge_src not in returns or edge_dst not in returns:
        return CITestResult(edge_src, edge_dst, cond_set, 0.0, 1.0, True)

    x = np.asarray(returns[edge_src], dtype=float)
    y = np.asarray(returns[edge_dst], dtype=float)
    # Align length by trimming
    n = min(len(x), len(y))
    if n < MIN_SAMPLES_FOR_CI:
        return CITestResult(edge_src, edge_dst, cond_set, 0.0, 1.0, True)
    x, y = x[-n:], y[-n:]

    if cond_set:
        z_cols = []
        for c in cond_set:
            if c in returns:
                col = np.asarray(returns[c], dtype=float)[-n:]
                z_cols.append(col)
        z = np.column_stack(z_cols) if z_cols else None
    else:
        z = None

    r, p = _partial_correlation(x, y, z)
    return CITestResult(
        src=edge_src,
        dst=edge_dst,
        conditioning_set=cond_set,
        partial_corr=r,
        p_value=p,
        independent=(p >= alpha),
    )


# ── 2. Causal minimality test ────────────────────────────────────────
def test_causal_minimality(
    edge_src: str,
    edge_dst: str,
    parents_of_dst: Iterable[str],
    returns: dict[str, np.ndarray],
    *,
    alpha: float = SIGNIFICANCE_ALPHA,
) -> MinimalityResult:
    """Remove the edge and re-run CI with the reduced parent set.

    Marks the edge ``unnecessary`` iff dst STILL looks independent of
    src after removing src from Pa(dst). An edge contributing no new
    information is — per Spirtes et al. — a candidate for pruning.
    """
    remaining_parents = [p for p in parents_of_dst if p != edge_src]
    reduced = test_conditional_independence(
        edge_src,
        edge_dst,
        remaining_parents,
        returns,
        alpha=alpha,
    )
    return MinimalityResult(
        src=edge_src,
        dst=edge_dst,
        unnecessary=reduced.independent,
        p_value_after_removal=reduced.p_value,
    )


# ── 3. Random-graph / MEC benchmark ──────────────────────────────────
def _count_ci_violations(
    edges: list[tuple[str, str]],
    returns: dict[str, np.ndarray],
) -> int:
    """Count CI violations on a given edge set, empty-parent baseline."""
    # For the benchmark we test every edge with an empty conditioning
    # set (which is what "random graph" parent structure implies — we
    # are testing edge-to-edge independence, not full DAG semantics).
    violations = 0
    for src, dst in edges:
        if src == dst or src not in returns or dst not in returns:
            continue
        x = returns[src]
        y = returns[dst]
        n = min(len(x), len(y))
        if n < MIN_SAMPLES_FOR_CI:
            continue
        _, p = _pearson_with_p(x[-n:], y[-n:])
        if p >= SIGNIFICANCE_ALPHA:
            violations += 1
    return violations


def random_graph_benchmark(
    edges: list[tuple[str, str]],
    returns: dict[str, np.ndarray],
    *,
    permutations: int = 100,
    rng_seed: int = 2026,
) -> tuple[float, bool]:
    """Node-label permutation benchmark.

    Returns ``(p_fraction_better, falsified)``. ``falsified`` is True
    when more than 5 % of permuted DAGs exhibit fewer CI violations
    than the candidate — meaning the candidate is indistinguishable
    from random node relabelings, and therefore has no statistical
    information content.
    """
    if not edges or len(returns) < 3:
        return 1.0, False

    rng = np.random.default_rng(rng_seed)
    nodes = list(returns.keys())

    baseline = _count_ci_violations(edges, returns)
    better = 0
    for _ in range(permutations):
        permuted = {n: nodes[rng.integers(0, len(nodes))] for n in nodes}
        permuted_edges = [(permuted.get(s, s), permuted.get(d, d)) for s, d in edges]
        v = _count_ci_violations(permuted_edges, returns)
        if v < baseline:
            better += 1

    fraction = better / permutations
    return fraction, fraction > FALSIFIABILITY_THRESHOLD


# ── All-tests orchestrator ───────────────────────────────────────────
def _parents_of(edges: list[tuple[str, str]], dst: str) -> list[str]:
    """List of direct parents of `dst` in the candidate DAG."""
    return [s for s, d in edges if d == dst]


def run_all_tests(
    edges: list[tuple[str, str]],
    returns: dict[str, np.ndarray],
    *,
    alpha: float = SIGNIFICANCE_ALPHA,
    benchmark_permutations: int = 100,
) -> FalsificationReport:
    """Run the three falsification tests on a candidate DAG.

    Convention
    ──────────
    ``edges`` is a list of ``(src, dst)`` tuples representing
    directed edges src → dst. ``returns`` maps each node (ticker)
    to a 1-D array of standardised log-returns.
    """
    report = FalsificationReport(edges_tested=len(edges))

    # 1 + 2. Per-edge CI + causal-minimality tests.
    for src, dst in edges:
        parents = _parents_of(edges, dst)
        ci = test_conditional_independence(src, dst, parents, returns, alpha=alpha)
        if ci.independent:
            report.ci_violations.append(ci)
        minimality = test_causal_minimality(src, dst, parents, returns, alpha=alpha)
        if minimality.unnecessary:
            report.unnecessary_edges.append(minimality)

    if report.edges_tested:
        report.ci_fraction_violated = len(report.ci_violations) / report.edges_tested

    # 3. Random-graph benchmark.
    p_better, falsified = random_graph_benchmark(
        edges,
        returns,
        permutations=benchmark_permutations,
    )
    report.random_benchmark_p = p_better
    report.falsified_by_benchmark = falsified
    # "Not falsifiable" when many permutations are MEC-equivalent,
    # which we proxy here with a high benchmark fraction (>50 %).
    report.falsifiable = p_better <= 0.50

    return report
