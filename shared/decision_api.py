"""Orchestrator surface as a stable Protocol.

The execution layer (``paper_runner``, ``broker``, the math glue
helpers) needs to call into the orchestrator to turn a market
context into a typed ``TradeIntent``. The concrete orchestrator
lives in ``graph_intelligence/agents/strategy_orchestrator.py``,
but importing that module at module-load time inside
``execution_engine`` re-creates the bidirectional cycle the audit
flagged.

This module is the contract: a callable Protocol matching the
orchestrator's public signature. Execution-layer code can:

  * type-annotate a parameter as ``OrchestrateFn`` and accept any
    impl that satisfies the Protocol;
  * keep a thin deferred-import inside the function body for the
    concrete factory (so the import graph at module load is acyclic);
  * write tests that pass a fake satisfying ``OrchestrateFn``
    without needing the real ``graph_intelligence`` stack.

Once every back-import call site is annotated against the Protocol,
the legacy ``ignore_imports`` block in ``.importlinter`` can be
removed.
"""

from __future__ import annotations

from typing import Any, Protocol


class OrchestrateFn(Protocol):
    """Callable shape of ``rule_based_orchestrate``.

    The concrete implementation accepts a ``MarketContext`` (defined
    in ``graph_intelligence.agents.strategy_orchestrator``) and returns
    a ``TradeDecision``-like dataclass with at least ``execute``,
    ``direction``, ``conviction``, ``expected_r``, ``rationale``
    fields. We intentionally keep the Protocol broad (``Any``) for
    both the input and output here — the concrete typed surface is
    in the orchestrator module — so this Protocol can be referenced
    from the leaf ``shared`` layer without dragging the orchestrator
    types into ``shared``.
    """

    def __call__(self, ctx: Any, /, *args: Any, **kwargs: Any) -> Any: ...


def get_orchestrate() -> OrchestrateFn:
    """Resolve the concrete ``rule_based_orchestrate`` lazily.

    Importing ``graph_intelligence`` at module-load time from
    ``execution_engine`` recreates the dependency cycle. Calling this
    helper inside a function body (only when the orchestrator is
    actually needed) keeps the module-load import graph acyclic
    while still giving callers a one-liner.
    """
    from graph_intelligence.agents.strategy_orchestrator import rule_based_orchestrate
    return rule_based_orchestrate
