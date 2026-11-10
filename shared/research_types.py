"""Typed contracts for the AYVID research workspace.

Leaf-layer module (``shared``): no imports from backend / graph_intelligence.
These models validate every research-API boundary; storage rows are plain
dicts produced by ``backend.services.research_store``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

DataTier = Literal["LIVE_EDGAR_AND_LOCAL_OHLCV", "LOCAL_OHLCV_ONLY", "FIXTURE", "REFERENCE_ONLY"]
Provider = Literal["SEC_EDGAR", "LOCAL_OHLCV", "SUPPLY_CHAIN_GRAPH", "USER_UPLOAD", "FIXTURE_FILING"]
DocType = Literal[
    "10-K", "10-Q", "8-K", "20-F", "6-K", "EARNINGS_RELEASE", "PRICE_SERIES", "GRAPH_EDGE_AUDIT"
]
AccessStatus = Literal["AVAILABLE", "RESTRICTED", "UNAVAILABLE"]
Scale = Literal["UNITS", "MILLIONS", "BILLIONS", "PERCENT", "RATIO"]
Basis = Literal["GAAP", "NON_GAAP", "IFRS", "DETERMINISTIC_CALC", "REPORTED_MARKET"]
ObservationStatus = Literal["REPORTED", "ESTIMATED", "DERIVED", "STALE"]
RelationshipType = Literal[
    "SUPPLIER", "CUSTOMER", "COMPETITOR", "GEOGRAPHIC_CHOKEPOINT", "COMMODITY_INPUT"
]
EvidenceStatus = Literal["EXTRACTED", "INFERRED", "HYPOTHESIS"]
Intent = Literal[
    "COMPANY_RESEARCH",
    "FINANCIAL_COMPARISON",
    "EARNINGS_ANALYSIS",
    "EVENT_IMPACT",
    "VALUATION_SCENARIO",
    "UNSUPPORTED_QUERY",
]
Epistemic = Literal["REPORTED", "CALCULATED", "MANAGEMENT", "INTERPRETATION", "HYPOTHESIS"]
DataMode = Literal["LIVE", "HYBRID", "FIXTURE"]
Conviction = Literal["HIGH_EVIDENCE", "MODERATE_EVIDENCE", "EXPLORATORY"]
ConditionOperator = Literal["<", ">", "DROP_PCT", "EVENT_OCCURS"]
ConditionStatus = Literal["NOT_TRIGGERED", "TRIGGERED", "UNKNOWN_MISSING_DATA"]
EvidenceStance = Literal["SUPPORTING", "CONTRADICTING"]

MAX_QUERY_CHARS = 1000
MAX_TEXT_CHARS = 4000


class ResearchQuery(BaseModel):
    query: str = Field(min_length=3, max_length=MAX_QUERY_CHARS)
    simulate_provider_failure: Literal["edgar", "llm", "all"] | None = None
    instrument_ids: list[str] | None = Field(default=None, max_length=10)


class ThesisConditionIn(BaseModel):
    metric_code: str = Field(min_length=1, max_length=64)
    instrument_id: str = Field(min_length=3, max_length=64)
    operator: ConditionOperator
    threshold_value: float | None = None
    description: str = Field(min_length=1, max_length=500)

    @field_validator("threshold_value")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and (v != v or v in (float("inf"), float("-inf"))):
            raise ValueError("threshold must be finite")
        return v


class ThesisEvidenceIn(BaseModel):
    source_id: str = Field(min_length=1, max_length=128)
    stance: EvidenceStance
    note: str = Field(default="", max_length=MAX_TEXT_CHARS)


class ThesisCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    instrument_ids: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(default="", max_length=MAX_TEXT_CHARS)
    horizon: str = Field(default="12-24 months", max_length=64)
    conviction_label: Conviction = "EXPLORATORY"
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    catalysts: list[str] = Field(default_factory=list, max_length=50)
    open_questions: list[str] = Field(default_factory=list, max_length=50)
    evidence: list[ThesisEvidenceIn] = Field(default_factory=list, max_length=200)
    conditions: list[ThesisConditionIn] = Field(default_factory=list, max_length=50)
    session_id: str | None = None


class ThesisUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    summary: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    horizon: str | None = Field(default=None, max_length=64)
    conviction_label: Conviction | None = None
    assumptions: list[str] | None = Field(default=None, max_length=50)
    catalysts: list[str] | None = Field(default=None, max_length=50)
    open_questions: list[str] | None = Field(default=None, max_length=50)
    change_note: str = Field(default="Edited", max_length=500)


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instrument_ids: list[str] = Field(default_factory=list, max_length=200)


class WatchlistItemIn(BaseModel):
    instrument_id: str = Field(min_length=3, max_length=64)


class ScreenerFilter(BaseModel):
    field: str = Field(min_length=1, max_length=64)
    op: Literal[">", "<", ">=", "<=", "==", "in", "linked_to"]
    value: float | str | list[str]


class ScreenerQuery(BaseModel):
    query: str = Field(default="", max_length=MAX_QUERY_CHARS)
    filters: list[ScreenerFilter] | None = Field(default=None, max_length=20)


class DCFInput(BaseModel):
    base_revenue: float = Field(gt=0)
    revenue_growth_pct: float = Field(ge=-50, le=100)
    operating_margin_pct: float = Field(ge=-50, le=90)
    tax_rate_pct: float = Field(default=15, ge=0, le=60)
    reinvestment_pct_of_nopat: float = Field(default=30, ge=0, le=100)
    discount_rate_pct: float = Field(ge=3, le=25)
    terminal_growth_pct: float = Field(default=3, ge=-2, le=6)
    years: int = Field(default=5, ge=1, le=15)
    shares_outstanding: float | None = Field(default=None, gt=0)
    net_cash: float = 0.0
