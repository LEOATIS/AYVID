"""Typed schema for the chart-pattern classifier."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PatternClass(str, Enum):
    """The 16 canonical patterns the PatternAgent classifies against.

    Taken from QuantAgent (arXiv 2509.09995v3, §C.2).
    """

    DOUBLE_BOTTOM = "DOUBLE_BOTTOM"
    DOUBLE_TOP = "DOUBLE_TOP"
    INVERSE_HEAD_SHOULDERS = "INVERSE_HEAD_SHOULDERS"
    HEAD_SHOULDERS = "HEAD_SHOULDERS"
    ROUNDED_BOTTOM = "ROUNDED_BOTTOM"
    ROUNDED_TOP = "ROUNDED_TOP"
    ASCENDING_TRIANGLE = "ASCENDING_TRIANGLE"
    DESCENDING_TRIANGLE = "DESCENDING_TRIANGLE"
    SYMMETRICAL_TRIANGLE = "SYMMETRICAL_TRIANGLE"
    EXPANDING_TRIANGLE = "EXPANDING_TRIANGLE"
    FALLING_WEDGE = "FALLING_WEDGE"
    RISING_WEDGE = "RISING_WEDGE"
    BULLISH_FLAG = "BULLISH_FLAG"
    BEARISH_FLAG = "BEARISH_FLAG"
    RECTANGLE = "RECTANGLE"
    V_REVERSAL = "V_REVERSAL"
    NONE = "NONE"


# Default directional bias of each pattern family when detected clean.
# The LLM can override this in its `implied_direction` output, but we
# expose it for callers that want a heuristic fallback.
PATTERN_BIAS: dict[PatternClass, Literal["LONG", "SHORT", "NEUTRAL"]] = {
    PatternClass.DOUBLE_BOTTOM: "LONG",
    PatternClass.INVERSE_HEAD_SHOULDERS: "LONG",
    PatternClass.ROUNDED_BOTTOM: "LONG",
    PatternClass.ASCENDING_TRIANGLE: "LONG",
    PatternClass.FALLING_WEDGE: "LONG",
    PatternClass.BULLISH_FLAG: "LONG",
    PatternClass.DOUBLE_TOP: "SHORT",
    PatternClass.HEAD_SHOULDERS: "SHORT",
    PatternClass.ROUNDED_TOP: "SHORT",
    PatternClass.DESCENDING_TRIANGLE: "SHORT",
    PatternClass.RISING_WEDGE: "SHORT",
    PatternClass.BEARISH_FLAG: "SHORT",
    PatternClass.SYMMETRICAL_TRIANGLE: "NEUTRAL",
    PatternClass.EXPANDING_TRIANGLE: "NEUTRAL",
    PatternClass.RECTANGLE: "NEUTRAL",
    PatternClass.V_REVERSAL: "NEUTRAL",
    PatternClass.NONE: "NEUTRAL",
}


class PatternClassification(BaseModel):
    """Vision-LLM output — a single typed chart-pattern classification.

    Every field is bounded and Pydantic-validated so hallucinated or
    out-of-range values are rejected at the SDK boundary.
    """

    pattern: PatternClass = Field(
        default=PatternClass.NONE,
        description="Canonical chart pattern from the 16-class library.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Classifier confidence in the pattern label.",
    )
    implied_direction: Literal["LONG", "SHORT", "NEUTRAL"] = Field(
        default="NEUTRAL",
        description="Expected directional bias IF the pattern completes.",
    )
    invalidation_level: float = Field(
        default=0.0,
        description="Price level beyond which the pattern is broken and "
        "the thesis is invalidated (absolute price, not percent).",
    )
    rationale: str = Field(
        default="",
        description="1-3 sentence justification referencing specific "
        "chart features (swing highs/lows, trendlines, etc.).",
    )
