"""Pydantic schema for the Supply Chain Mapper agent output.

Phase-3 (P1-J): every LangGraph agent MUST emit a structured-output
payload rather than free-form JSON that downstream callers parse with
``json.loads``. The mapper was the last hold-out — it used
``json.loads`` on the raw LLM message body and silently defaulted
fields when parsing failed, letting malformed / hallucinated
compound_risk values flow straight into the conviction calc.

This schema binds the mapper's LLM call via ``with_structured_output``
so the SDK itself rejects outputs that do not fit the contract:

* ``target_ticker`` : required — the downstream victim / beneficiary
* ``alpha_horizon`` : one of ``1st_order``–``4th_order``
* ``direction``     : ``LONG`` / ``SHORT`` / ``HOLD``
* ``compound_risk`` : bounded into ``[0, 1]`` (no more inflated values
  silently driving downstream conviction to 1.0)
* ``causal_chain``  : at most 8 hops end-to-end
* ``reasoning``     : short prose
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AlphaHorizonLiteral = Literal[
    "1st_order",
    "2nd_order",
    "3rd_order",
    "4th_order",
]

DirectionLiteral = Literal["LONG", "SHORT", "HOLD"]


class MapperOutput(BaseModel):
    """Canonical Supply-Chain Mapper agent response.

    Used as the ``with_structured_output`` schema in
    ``graph_intelligence/agents/mapper/node.py``.
    """

    target_ticker: str = Field(
        default="",
        description="The ticker the mapper proposes to trade. Empty "
        "string signals the mapper could not identify a "
        "high-conviction downstream victim/beneficiary.",
    )
    alpha_horizon: AlphaHorizonLiteral = Field(
        default="3rd_order",
        description="How many causal hops downstream from the shock.",
    )
    direction: DirectionLiteral = Field(
        default="HOLD",
        description="Direction for the proposed trade. HOLD when the "
        "mapper cannot classify (orchestrator then skips).",
    )
    compound_risk: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Aggregate causal-dependency score across the "
        "full chain. Bounded to [0, 1] so downstream "
        "conviction scaling cannot be inflated by a "
        "miscalibrated LLM.",
    )
    causal_chain: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="The ordered list of tickers from shock source "
        "to target. At most 8 hops — anything longer is "
        "noise.",
    )
    reasoning: str = Field(
        default="",
        description="Short prose explaining the mispricing thesis.",
    )
