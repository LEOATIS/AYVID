"""Response schemas for the research API (OpenAPI documentation + validation).

Every model allows extra keys: the schema documents and type-checks the fields
clients rely on, but a newly added field is never silently stripped from a
response. Request bodies live in ``shared.research_types``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.research_types import ScreenerFilter


class Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorOut(Out):
    detail: str | list[dict[str, Any]] = Field(description="Human-readable reason, or validation errors")


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorOut, "description": "Malformed workspace / user headers"},
    403: {"model": ErrorOut, "description": "Resource belongs to another workspace, or API key rejected"},
    404: {"model": ErrorOut, "description": "Resource not found"},
    409: {"model": ErrorOut, "description": "Conflicts with an existing record"},
    422: {"model": ErrorOut, "description": "Invalid input, or values outside a computable range"},
    429: {"model": ErrorOut, "description": "Per-workspace rate limit exceeded (see Retry-After)"},
}


# ── shared building blocks ─────────────────────────────────────────────
class Freshness(Out):
    is_stale: bool
    age_days: int | None = None
    as_of: str | None = None


class InstrumentOut(Out):
    instrument_id: str = Field(examples=["US:NASDAQ:NVDA"])
    symbol: str
    local_symbol: str | None = None
    name: str
    exchange: str
    currency: str
    country: str
    sector: str | None = None
    industry: str | None = None
    cik: str | None = None
    data_tier: str = Field(description="LIVE_EDGAR_AND_LOCAL_OHLCV | LOCAL_OHLCV_ONLY | FIXTURE | REFERENCE_ONLY")
    first_bar: str | None = None
    last_bar: str | None = None


class SourceDocumentOut(Out):
    source_id: str
    label: str | None = Field(default=None, description="Citation label within one answer, e.g. S1")
    provider: str = Field(description="SEC_EDGAR | LOCAL_OHLCV | SUPPLY_CHAIN_GRAPH | USER_UPLOAD | FIXTURE_FILING")
    title: str
    publisher: str | None = None
    canonical_url: str | None = None
    published_at: str | None = None
    retrieved_at: str
    doc_type: str
    instrument_id: str | None = None
    accession_no: str | None = None
    locator: str | None = None
    excerpt: str | None = None
    content_hash: str | None = None
    access_status: Literal["AVAILABLE", "RESTRICTED", "UNAVAILABLE"]
    is_fixture: bool


class ObservationOut(Out):
    observation_id: str
    instrument_id: str
    metric_code: str
    metric_label: str
    value: str = Field(description="Decimal-safe string")
    currency: str | None = None
    scale: Literal["UNITS", "MILLIONS", "BILLIONS", "PERCENT", "RATIO"]
    fiscal_period: str = Field(examples=["FY2025", "Q2-FY2026", "Q2-FY2026-YTD"])
    period_start: str | None = None
    period_end: str | None = None
    published_at: str | None = None
    as_of: str
    provider: str
    source_id: str | None = None
    basis: str
    status: Literal["REPORTED", "ESTIMATED", "DERIVED", "STALE"]
    formula: str | None = None
    inputs: Any = None


class EdgeOut(Out):
    edge_id: str
    src_instrument_id: str | None = None
    dst_instrument_id: str | None = None
    src_symbol: str
    dst_symbol: str
    relationship_type: str
    revenue_dependency_pct: float | None = None
    criticality_score: float | None = None
    source_id: str | None = None
    evidence_status: Literal["EXTRACTED", "INFERRED", "HYPOTHESIS"]
    uncertainty_note: str | None = None


class EdgeDetailOut(EdgeOut):
    source: SourceDocumentOut | None = None


class StatusOut(Out):
    status: str
    code: str | None = None


# ── meta / resolve ─────────────────────────────────────────────────────
class LlmMeta(Out):
    provider: str
    model: str | None = None
    configured: list[str]


class DataMeta(Out):
    instruments: int
    latest_bar: str | None = None
    stale: Freshness
    live_edgar_enabled: bool
    mode_label: str


class MetaOut(Out):
    workspace_id: str
    user_id: str
    llm: LlmMeta
    data: DataMeta
    safety: dict[str, bool]


class ResolveOut(Out):
    query: str
    status: Literal["RESOLVED", "AMBIGUOUS", "UNSUPPORTED"]
    candidates: list[InstrumentOut]
    note: str | None = None


# ── research artifact ──────────────────────────────────────────────────
Epistemic = Literal["REPORTED", "CALCULATED", "MANAGEMENT", "INTERPRETATION", "HYPOTHESIS"]


class AmbiguityOut(Out):
    token: str
    reason: str
    candidates: list[InstrumentOut]


class ResolutionOut(Out):
    resolved: list[InstrumentOut]
    ambiguous: list[AmbiguityOut]
    unsupported: list[str]
    notes: list[str]


class ClaimOut(Out):
    claim_id: str
    text: str
    epistemic: Epistemic
    refs: list[str] = Field(description="Citation labels (S#) or calculation labels (CALC-#)")


class SummaryOut(Out):
    headline: str
    claims: list[ClaimOut]
    llm_rewrite: str | None = None


class DataRowOut(Out):
    instrument_id: str
    symbol: str
    metric_code: str
    metric_label: str
    fiscal_period: str
    value: str
    display: str
    currency: str | None = None
    scale: str
    basis: str
    status: str
    as_of: str
    is_stale: bool
    age_days: int | None = None
    ref: str | None = None
    source_label: str | None = None
    observation_id: str
    epistemic: Epistemic


class ExposureOut(Out):
    edge_id: str
    order: int
    direction: Literal["UPSTREAM", "DOWNSTREAM"]
    path: list[str]
    from_: str = Field(alias="from")
    to: str
    from_instrument_id: str | None = None
    to_instrument_id: str | None = None
    relationship_type: str
    mechanism: str
    revenue_dependency_pct: float | None = None
    criticality_score: float | None = None
    evidence_status: str
    uncertainty_note: str | None = None
    source_label: str | None = None
    exposure_score: float

    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)


class UncertaintyOut(Out):
    kind: str
    text: str


class MonitorOut(Out):
    metric_code: str
    instrument_id: str
    operator: Literal["<", ">", "DROP_PCT", "EVENT_OCCURS"]
    threshold_value: float | None = None
    description: str


class AuditOut(Out):
    verified: int
    dropped: int
    dropped_claims: list[str]


class ClaimLinkOut(Out):
    claim_id: str
    ref_id: str
    ref_kind: Literal["SOURCE", "CALC"]
    verified: bool


class StepOut(Out):
    key: str
    label: str
    status: Literal["done", "degraded", "blocked"]
    detail: str
    ms: int


class BannerOut(Out):
    kind: str = Field(description="STALE | DEGRADED | AMBIGUOUS | UNSUPPORTED")
    message: str


class CalculationOut(Out):
    label: str
    label_text: str
    formula: str
    inputs: Any
    result: str
    unit: str
    observation_id: str
    source_label: str | None = None
    instrument_id: str
    as_of: str


class TelemetryOut(Out):
    provider: str
    model: str | None = None
    llm_used: bool
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    note: str | None = None
    total_ms: int | None = None
    retrieval: dict[str, Any] | None = None
    sanitizer: dict[str, Any] | None = None


class RefusalOut(Out):
    code: str
    message: str


class ArtifactOut(Out):
    session_id: str
    query: str
    created_at: str
    status: Literal["COMPLETE", "REFUSED", "NEEDS_DISAMBIGUATION"]
    intent: str
    title: str
    data_mode: Literal["LIVE", "HYBRID", "FIXTURE"]
    resolution: ResolutionOut
    refusal: RefusalOut | None = None
    summary: SummaryOut | None = None
    data_table: list[DataRowOut]
    exposures: list[ExposureOut]
    bull: list[ClaimOut]
    bear: list[ClaimOut]
    uncertainty: list[UncertaintyOut]
    monitor: list[MonitorOut]
    audit: AuditOut
    claim_links: list[ClaimLinkOut]
    steps: list[StepOut]
    banners: list[BannerOut]
    citations: dict[str, SourceDocumentOut]
    calculations: dict[str, CalculationOut]
    telemetry: TelemetryOut


class SessionSummaryOut(Out):
    session_id: str
    query: str
    title: str | None = None
    intent: str
    status: str
    data_mode: str
    created_at: str


# ── companies ──────────────────────────────────────────────────────────
class FundamentalsStatusOut(Out):
    status: Literal["OK", "FAILED", "UNSUPPORTED", "NOT_FETCHED"]
    source: str
    fetched_at: str | None = None
    attempted_at: str | None = None
    filings: int | None = None
    observations: int | None = None
    latest_filing: str | None = None
    cached: bool | None = None
    error: str | None = None
    reason: str | None = None
    previous: dict[str, Any] | None = None


class ExposureSides(Out):
    upstream: list[EdgeOut]
    downstream: list[EdgeOut]


class ThesisRefOut(Out):
    thesis_id: str
    title: str
    health: Literal["HEALTHY", "TRIGGERED", "UNKNOWN"]
    updated_at: str | None = None


class CompanyOut(Out):
    instrument: InstrumentOut
    freshness: Freshness
    observations: list[ObservationOut]
    sources: list[SourceDocumentOut]
    exposures: ExposureSides
    theses: list[ThesisRefOut]
    fundamentals: FundamentalsStatusOut


class BarOut(Out):
    t: str
    close: float
    volume: float | None = None


class PricesOut(Out):
    instrument_id: str
    currency: str
    source_id: str
    freshness: Freshness
    cleaning: dict[str, int] = Field(description="non_finite / duplicates / conflicting_duplicates rows removed")
    bars: list[BarOut]


# ── theses ─────────────────────────────────────────────────────────────
ConditionStatus = Literal["NOT_TRIGGERED", "TRIGGERED", "UNKNOWN_MISSING_DATA"]


class ConditionOut(Out):
    condition_id: str
    thesis_id: str
    metric_code: str
    instrument_id: str
    operator: str
    threshold_value: float | None = None
    description: str
    current_status: ConditionStatus
    observed_value: str | None = None
    last_evaluated_at: str | None = None
    evaluation_explanation: str | None = None


class EvidenceLinkOut(Out):
    evidence_id: str
    thesis_id: str
    source_id: str
    stance: Literal["SUPPORTING", "CONTRADICTING"]
    note: str | None = None
    added_at: str
    source_title: str | None = None
    provider: str | None = None
    doc_type: str | None = None
    is_fixture: bool | int | None = None


class RevisionSummaryOut(Out):
    revision_id: str
    revision_no: int
    created_at: str
    author: str
    change_note: str


class RevisionOut(RevisionSummaryOut):
    thesis_id: str
    snapshot: dict[str, Any] = Field(description="Full thesis state at this revision (append-only)")


class ThesisOut(Out):
    thesis_id: str
    workspace_id: str
    user_id: str
    title: str
    instrument_ids: list[str]
    created_at: str
    updated_at: str
    last_reviewed_at: str | None = None
    horizon: str | None = None
    conviction_label: str
    summary: str | None = None
    assumptions: list[str]
    catalysts: list[str]
    open_questions: list[str]
    linked_session_ids: list[str]
    supporting_evidence: list[EvidenceLinkOut]
    contradicting_evidence: list[EvidenceLinkOut]
    conditions: list[ConditionOut]
    revisions: list[RevisionSummaryOut]
    health: Literal["HEALTHY", "TRIGGERED", "UNKNOWN"] | None = None


class ThesisListItemOut(Out):
    thesis_id: str
    title: str
    instrument_ids: list[str]
    updated_at: str
    conviction_label: str
    health: Literal["HEALTHY", "TRIGGERED", "UNKNOWN"]
    condition_statuses: list[ConditionStatus]


class ConditionResultOut(Out):
    condition_id: str
    description: str
    status: ConditionStatus
    previous_status: ConditionStatus
    observed_value: str | None = None
    stale: bool
    explanation: str
    source_id: str | None = None


class NewEvidenceOut(Out):
    source_id: str
    title: str
    provider: str
    doc_type: str | None = None
    published_at: str | None = None


class EvaluationOut(Out):
    thesis_id: str
    evaluated_at: str
    health: Literal["HEALTHY", "TRIGGERED", "UNKNOWN"]
    results: list[ConditionResultOut]
    changed: list[ConditionResultOut]
    new_evidence: list[NewEvidenceOut]
    counts: dict[str, int]
    proposed_revision_note: str


# ── screener ───────────────────────────────────────────────────────────
class ScreenPlanOut(Out):
    query: str
    filters: list[ScreenerFilter]
    unparsed: list[str]


class ScreenMatchOut(Out):
    instrument_id: str
    symbol: str
    name: str
    industry: str | None = None
    country: str
    last_bar: str | None = None
    is_stale: bool
    metrics: dict[str, str | None]


class UniverseOut(Out):
    size: int
    description: str
    date_range: dict[str, str | None]
    stale_instruments: int


class MissingDataOut(Out):
    rule: str
    excluded_count: int
    excluded: list[dict[str, Any]]


class ScreenerOut(Out):
    plan: ScreenPlanOut
    filters: list[ScreenerFilter]
    matches: list[ScreenMatchOut]
    universe: UniverseOut
    missing_data: MissingDataOut


# ── watchlists / notifications ─────────────────────────────────────────
class WatchItemOut(Out):
    instrument_id: str
    added_at: str
    symbol: str | None = None
    name: str | None = None
    last_bar: str | None = None
    data_tier: str | None = None
    freshness: Freshness
    metrics: dict[str, str | None]
    theses: list[ThesisRefOut]


class WatchlistOut(Out):
    watchlist_id: str
    workspace_id: str
    user_id: str
    name: str
    created_at: str
    items: list[WatchItemOut]


class WatchlistCreatedOut(Out):
    watchlist_id: str


class CheckResultOut(Out):
    thesis_id: str
    health: str
    counts: dict[str, int]
    changed: int


class RunChecksOut(Out):
    evaluated: int
    results: list[CheckResultOut]


class NotificationOut(Out):
    notification_id: str
    workspace_id: str
    created_at: str
    kind: str
    title: str
    body: str | None = None
    ref_type: str | None = None
    ref_id: str | None = None
    is_read: int


# ── valuation ──────────────────────────────────────────────────────────
class AssumedInput(Out):
    value: float | int | None
    label: Literal["ASSUMED"]


class DcfRowOut(Out):
    year: int
    revenue: str
    fcf: str
    pv: str


class DcfResultOut(Out):
    rows: list[DcfRowOut]
    pv_explicit: str
    pv_terminal: str
    enterprise_value: str
    equity_value: str
    value_per_share: str | None = None
    terminal_share_pct: str | None = None
    formula: str


class SensitivityRowOut(Out):
    discount_rate_pct: float
    values: list[str | None]


class SensitivityOut(Out):
    discount_rates: list[float]
    growth_rates: list[float]
    grid: list[SensitivityRowOut]


class DcfOut(Out):
    inputs: dict[str, AssumedInput]
    result: DcfResultOut
    sensitivity: SensitivityOut
