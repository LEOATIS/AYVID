"""Supervisor agent — Bayesian Update Operator over empirical priors.

The Supervisor is the last LLM before execution. It reads the
empirical priors produced by ``backtesting.calibrate`` and either
confirms or narrowly deviates from them, emitting the exact
`strategy_parameters` the orchestrator will consume at decision time.
"""

from graph_intelligence.agents.supervisor.consensus import (
    emit_trade_signal,
    should_continue,
)
from graph_intelligence.agents.supervisor.node import supervisor_node
from graph_intelligence.agents.supervisor.prompts import (
    SUPERVISOR_SYSTEM,
    SUPERVISOR_USER_TEMPLATE,
    render_priors_block,
)

__all__ = [
    "supervisor_node",
    "emit_trade_signal",
    "should_continue",
    "SUPERVISOR_SYSTEM",
    "SUPERVISOR_USER_TEMPLATE",
    "render_priors_block",
]
