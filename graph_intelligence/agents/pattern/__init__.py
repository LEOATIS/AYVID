"""PatternAgent — multimodal chart-pattern classifier (Phase 5).

Adds a 5th specialist to the analyst swarm that reasons about chart
GEOMETRY rather than just indicator numbers. Renders a candlestick
chart of the target instrument's recent price action and asks a
vision LLM to classify it against a canonical 16-pattern library
(double bottom, descending triangle, head-and-shoulders, etc.).

Reference: Xiong et al., *QuantAgent: Price-Driven Multi-Agent LLMs
for High-Frequency Trading* (arXiv 2509.09995v3), §3.1.1 PatternAgent.

The indicator/trend/risk agents from QuantAgent already exist in
AYVID as `signals.py`, `regime.py`, and the orchestrator EV gate —
this module fills the last remaining gap.
"""
from graph_intelligence.agents.pattern.schema import (
    PatternClass, PatternClassification,
)
from graph_intelligence.agents.pattern.node import (
    pattern_analyst_opinion,
    classify_pattern,
)
from graph_intelligence.agents.pattern.chart_renderer import (
    render_candlestick_chart,
    chart_fingerprint,
)

__all__ = [
    "PatternClass",
    "PatternClassification",
    "pattern_analyst_opinion",
    "classify_pattern",
    "render_candlestick_chart",
    "chart_fingerprint",
]
