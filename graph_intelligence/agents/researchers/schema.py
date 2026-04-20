"""Pydantic schemas for the bull/bear researcher debate.

Every LLM call in Phase 3 uses ``with_structured_output`` against
one of these schemas so the downstream analyst swarm and auditor
receive typed, bounded inputs rather than free-form LLM prose.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


DominantSide = Literal["BULL", "BEAR", "NEITHER"]


class BullArgument(BaseModel):
    """The Bull advocate's case for entering LONG on the mapper thesis."""

    thesis_summary: str = Field(
        default="",
        description="One-sentence restatement of the bullish thesis.",
    )
    strongest_catalysts: list[str] = Field(
        default_factory=list,
        description="Up to 5 concrete, cite-able reasons the trade "
                    "will work. Each item ≤ 140 characters.",
    )
    bear_case_rebuttal: str = Field(
        default="",
        description="Directly address the strongest argument against this "
                    "position. Concede valid points; refute invalid ones.",
    )
    conviction: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Self-reported strength of this argument "
                    "(BEFORE facilitator adjudication).",
    )
    invalidation_level: str = Field(
        default="",
        description="The single observation that would invalidate this "
                    "thesis (e.g. 'SPY closes below 4200 with volume').",
    )


class BearArgument(BaseModel):
    """The Bear advocate's case for rejecting / reversing the mapper thesis."""

    counter_thesis: str = Field(
        default="",
        description="One-sentence statement of the bearish counter-thesis.",
    )
    strongest_risks: list[str] = Field(
        default_factory=list,
        description="Up to 5 concrete risks / reasons the trade will fail. "
                    "Each item ≤ 140 characters.",
    )
    bull_case_rebuttal: str = Field(
        default="",
        description="Directly address the strongest argument in favour of "
                    "the position. Concede valid points; refute invalid ones.",
    )
    conviction: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Self-reported strength of this argument.",
    )
    invalidation_level: str = Field(
        default="",
        description="The single observation that would invalidate this "
                    "counter-thesis.",
    )


class DebateRound(BaseModel):
    """Container for a single round of bull-vs-bear exchange."""

    round_index: int = Field(default=0, ge=0)
    bull: BullArgument = Field(default_factory=BullArgument)
    bear: BearArgument = Field(default_factory=BearArgument)


class ResearcherConsensus(BaseModel):
    """Facilitator verdict after n rounds of debate.

    This is the object the analyst swarm + auditor actually read. It
    abstracts away the multi-round rebuttal chain into a single
    structured consensus.
    """

    dominant_side: DominantSide = "NEITHER"
    direction: Literal["LONG", "SHORT", "HOLD"] = "HOLD"
    conviction: float = Field(default=0.0, ge=0.0, le=1.0)

    # Crisp executive summary for the analyst swarm / auditor prompt.
    executive_summary: str = Field(
        default="",
        description="Max 2 sentences summarising who won and why.",
    )

    # The surviving strongest arguments on each side AFTER rebuttals.
    bull_takeaways: list[str] = Field(default_factory=list)
    bear_takeaways: list[str] = Field(default_factory=list)

    # Facilitator's honest assessment of how close the debate was.
    # Values near 0 mean near-tie (should lower downstream conviction);
    # values near 1 mean one side clearly dominated.
    margin: float = Field(default=0.0, ge=0.0, le=1.0)

    facilitator_rationale: str = Field(default="")
