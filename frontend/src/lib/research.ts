/**
 * Research workspace client — types mirror backend/services/research_*.
 */
import useSWR from "swr";
import { deleteJSON, getJSON, patchJSON, postJSON } from "./api";

export type Epistemic = "REPORTED" | "CALCULATED" | "MANAGEMENT" | "INTERPRETATION" | "HYPOTHESIS";
export type ConditionStatus = "NOT_TRIGGERED" | "TRIGGERED" | "UNKNOWN_MISSING_DATA";
export type Health = "HEALTHY" | "TRIGGERED" | "UNKNOWN";

export interface SourceDoc {
  source_id: string;
  label?: string;
  provider: string;
  title: string;
  publisher: string | null;
  canonical_url: string | null;
  published_at: string | null;
  retrieved_at: string;
  doc_type: string;
  instrument_id: string | null;
  accession_no: string | null;
  locator: string | null;
  excerpt: string | null;
  content_hash: string | null;
  access_status: "AVAILABLE" | "RESTRICTED" | "UNAVAILABLE";
  is_fixture: boolean;
}

export interface CalcInput {
  name: string;
  metric_code?: string;
  value: string;
  currency?: string | null;
  scale?: string;
  fiscal_period?: string;
  source_id?: string | null;
  as_of?: string | null;
}

export interface Calculation {
  label: string;
  label_text: string;
  formula: string;
  inputs: CalcInput[] | Record<string, unknown>;
  result: string;
  unit: string;
  observation_id: string;
  source_label: string | null;
  instrument_id: string;
  as_of: string;
}

export interface Claim {
  claim_id: string;
  text: string;
  epistemic: Epistemic;
  refs: string[];
}

export interface DataRow {
  instrument_id: string;
  symbol: string;
  metric_code: string;
  metric_label: string;
  fiscal_period: string;
  value: string;
  display: string;
  currency: string | null;
  scale: string;
  basis: string;
  status: string;
  as_of: string;
  is_stale: boolean;
  age_days: number | null;
  ref: string | null;
  source_label: string | null;
  observation_id: string;
  epistemic: Epistemic;
}

export interface Exposure {
  edge_id: string;
  order: number;
  direction: "UPSTREAM" | "DOWNSTREAM";
  path: string[];
  from: string;
  to: string;
  from_instrument_id: string | null;
  to_instrument_id: string | null;
  relationship_type: string;
  mechanism: string;
  revenue_dependency_pct: number | null;
  criticality_score: number | null;
  evidence_status: "EXTRACTED" | "INFERRED" | "HYPOTHESIS";
  uncertainty_note: string | null;
  source_label: string | null;
  exposure_score: number;
}

export interface Instrument {
  instrument_id: string;
  symbol: string;
  local_symbol: string | null;
  name: string;
  exchange: string;
  currency: string;
  country: string;
  sector: string | null;
  industry: string | null;
  data_tier: string;
  last_bar: string | null;
  cik?: string | null;
  first_bar?: string | null;
}

export interface MonitorSuggestion {
  metric_code: string;
  instrument_id: string;
  operator: "<" | ">" | "DROP_PCT" | "EVENT_OCCURS";
  threshold_value: number | null;
  description: string;
}

export interface Banner {
  kind: "STALE" | "DEGRADED" | "AMBIGUOUS" | "UNSUPPORTED" | "FIXTURE";
  message: string;
}

export interface Step {
  key: string;
  label: string;
  status: "done" | "degraded" | "blocked";
  detail: string;
  ms: number;
}

export interface Telemetry {
  provider: string;
  model: string | null;
  llm_used: boolean;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  note?: string;
  total_ms?: number;
  retrieval?: {
    source_documents: number;
    live_edgar_documents: number;
    degraded_providers: { provider: string; instrument: string; error: string }[];
  };
}

export interface Artifact {
  session_id: string;
  query: string;
  created_at: string;
  status: "COMPLETE" | "REFUSED" | "NEEDS_DISAMBIGUATION";
  intent: string;
  title: string;
  data_mode: "LIVE" | "HYBRID" | "FIXTURE";
  resolution: {
    resolved: Instrument[];
    ambiguous: { token: string; reason: string; candidates: Instrument[] }[];
    unsupported: string[];
    notes: string[];
  };
  refusal: { code: string; message: string } | null;
  summary: { headline: string; claims: Claim[]; llm_rewrite: string | null } | null;
  data_table: DataRow[];
  exposures: Exposure[];
  bull: Claim[];
  bear: Claim[];
  uncertainty: { kind: string; text: string }[];
  monitor: MonitorSuggestion[];
  audit: { verified: number; dropped: number; dropped_claims: string[] };
  steps: Step[];
  banners: Banner[];
  citations: Record<string, SourceDoc>;
  calculations: Record<string, Calculation>;
  telemetry: Telemetry;
}

export interface SessionSummary {
  session_id: string;
  query: string;
  title: string | null;
  intent: string;
  status: string;
  data_mode: string;
  created_at: string;
}

export interface Condition {
  condition_id: string;
  metric_code: string;
  instrument_id: string;
  operator: string;
  threshold_value: number | null;
  description: string;
  current_status: ConditionStatus;
  observed_value: string | null;
  last_evaluated_at: string | null;
  evaluation_explanation: string | null;
}

export interface EvidenceLink {
  evidence_id: string;
  source_id: string;
  stance: "SUPPORTING" | "CONTRADICTING";
  note: string;
  added_at: string;
  source_title: string | null;
  provider: string | null;
  doc_type: string | null;
  is_fixture: number | null;
}

export interface Thesis {
  thesis_id: string;
  title: string;
  instrument_ids: string[];
  created_at: string;
  updated_at: string;
  last_reviewed_at: string | null;
  horizon: string | null;
  conviction_label: string;
  summary: string;
  assumptions: string[];
  catalysts: string[];
  open_questions: string[];
  linked_session_ids: string[];
  supporting_evidence: EvidenceLink[];
  contradicting_evidence: EvidenceLink[];
  conditions: Condition[];
  revisions: { revision_id: string; revision_no: number; created_at: string; author: string; change_note: string }[];
  health?: Health;
}

export interface ThesisListItem {
  thesis_id: string;
  title: string;
  instrument_ids: string[];
  updated_at: string;
  conviction_label: string;
  health: Health;
  condition_statuses: ConditionStatus[];
}

export interface Evaluation {
  thesis_id: string;
  evaluated_at: string;
  health: Health;
  results: {
    condition_id: string;
    description: string;
    status: ConditionStatus;
    previous_status: ConditionStatus;
    observed_value: string | null;
    stale: boolean;
    explanation: string;
  }[];
  changed: { condition_id: string }[];
  new_evidence: { source_id: string; title: string; provider: string }[];
  counts: Record<ConditionStatus, number>;
  proposed_revision_note: string;
}

export interface Meta {
  workspace_id: string;
  llm: { provider: string; model: string | null; configured: string[] };
  data: { instruments: number; latest_bar: string; stale: { is_stale: boolean; age_days: number }; mode_label: string };
  safety: Record<string, boolean>;
}

const swrOpts = { revalidateOnFocus: false, shouldRetryOnError: false };

export const researchApi = {
  query: (query: string, opts: { simulate?: string | null; instrumentIds?: string[] } = {}) =>
    postJSON<Artifact>(
      `/research/query${opts.simulate ? `?simulate_provider_failure=${encodeURIComponent(opts.simulate)}` : ""}`,
      { query, instrument_ids: opts.instrumentIds?.length ? opts.instrumentIds : undefined },
      { timeoutMs: 60_000 },
    ),
  session: (id: string) => getJSON<Artifact>(`/research/sessions/${id}`, { maxAttempts: 1 }),
  source: (id: string) => getJSON<SourceDoc>(`/research/sources/${encodeURIComponent(id)}`),
  edge: (id: string) => getJSON<Record<string, unknown>>(`/research/edges/${encodeURIComponent(id)}`),
  thesisFromResearch: (session_id: string) => postJSON<Thesis>("/theses/from-research", { session_id }),
  attach: (thesisId: string, session_id: string) => postJSON<Thesis>(`/theses/${thesisId}/attach`, { session_id }),
  thesis: (id: string) => getJSON<Thesis>(`/theses/${id}`, { maxAttempts: 1 }),
  createThesis: (body: unknown) => postJSON<Thesis>("/theses", body),
  updateThesis: (id: string, body: unknown) => patchJSON<Thesis>(`/theses/${id}`, body),
  evaluate: (id: string) => postJSON<Evaluation>(`/theses/${id}/evaluate`),
  approveRevision: (id: string, change_note: string) => postJSON<Thesis>(`/theses/${id}/revisions`, { change_note }),
  addCondition: (id: string, body: unknown) => postJSON<Thesis>(`/theses/${id}/conditions`, body),
  removeCondition: (id: string, cid: string) => deleteJSON<Thesis>(`/theses/${id}/conditions/${cid}`),
  addEvidence: (id: string, body: unknown) => postJSON<Thesis>(`/theses/${id}/evidence`, body),
  removeEvidence: (id: string, eid: string) => deleteJSON<Thesis>(`/theses/${id}/evidence/${eid}`),
  recordEvent: (code: string, description: string) => postJSON("/research/events", { code, description }),
  screen: (query: string, filters?: unknown) => postJSON<ScreenResult>("/screener/query", { query, filters }),
  runChecks: () => postJSON<{ evaluated: number }>("/watchlists/run-checks"),
  addWatchItem: (wid: string, instrument_id: string) => postJSON(`/watchlists/${wid}/items`, { instrument_id }),
  removeWatchItem: (wid: string, iid: string) => deleteJSON(`/watchlists/${wid}/items/${encodeURIComponent(iid)}`),
  createWatchlist: (name: string, instrument_ids: string[]) => postJSON<{ watchlist_id: string }>("/watchlists", { name, instrument_ids }),
  dcf: (body: unknown) => postJSON<DCFResult>("/valuation/dcf", body),
  refreshFundamentals: (iid: string) =>
    postJSON<FundamentalsStatus>(`/companies/${encodeURIComponent(iid)}/refresh-fundamentals`, undefined, { timeoutMs: 45_000 }),
};

export interface ScreenFilter {
  field: string;
  op: string;
  value: number | string | string[];
}

export interface ScreenResult {
  plan: { query: string; filters: ScreenFilter[]; unparsed: string[] };
  matches: {
    instrument_id: string;
    symbol: string;
    name: string;
    industry: string | null;
    country: string;
    last_bar: string;
    is_stale: boolean;
    metrics: Record<string, string | null>;
  }[];
  universe: { size: number; description: string; date_range: { first_bar: string; last_bar: string }; stale_instruments: number };
  missing_data: { rule: string; excluded_count: number; excluded: { symbol: string; missing: string[] }[] };
}

export interface DCFResult {
  result: {
    rows: { year: number; revenue: string; fcf: string; pv: string }[];
    pv_explicit: string;
    pv_terminal: string;
    enterprise_value: string;
    equity_value: string;
    value_per_share: string | null;
    terminal_share_pct: string | null;
    formula: string;
  };
  sensitivity: { discount_rates: number[]; growth_rates: number[]; grid: { discount_rate_pct: number; values: (string | null)[] }[] };
}

export interface CompanyPayload {
  instrument: Instrument;
  freshness: { is_stale: boolean; age_days: number | null; as_of: string | null };
  observations: {
    observation_id: string;
    metric_code: string;
    metric_label: string;
    value: string;
    currency: string | null;
    scale: string;
    fiscal_period: string;
    period_end: string;
    provider: string;
    source_id: string | null;
    basis: string;
    status: string;
    formula: string | null;
    inputs: unknown;
    as_of: string;
  }[];
  sources: SourceDoc[];
  exposures: { upstream: EdgeRow[]; downstream: EdgeRow[] };
  theses: { thesis_id: string; title: string; health: Health; updated_at: string }[];
  fundamentals: FundamentalsStatus;
}

export interface FundamentalsStatus {
  status: "OK" | "FAILED" | "UNSUPPORTED" | "NOT_FETCHED";
  source: string;
  fetched_at?: string;
  filings?: number;
  observations?: number;
  latest_filing?: string;
  cached?: boolean;
  error?: string;
  reason?: string;
  previous?: FundamentalsStatus | null;
}

export interface EdgeRow {
  edge_id: string;
  src_symbol: string;
  dst_symbol: string;
  src_instrument_id: string | null;
  dst_instrument_id: string | null;
  relationship_type: string;
  revenue_dependency_pct: number | null;
  criticality_score: number | null;
  evidence_status: string;
  source_id: string | null;
  uncertainty_note: string | null;
}

export interface Watchlist {
  watchlist_id: string;
  name: string;
  items: {
    instrument_id: string;
    symbol: string;
    name: string;
    last_bar: string | null;
    freshness: { is_stale: boolean; as_of: string | null };
    metrics: Record<string, string | null>;
    theses: { thesis_id: string; title: string; health: Health }[];
  }[];
}

export const useMeta = () => useSWR<Meta>("/research/meta", (p: string) => getJSON<Meta>(p), swrOpts);
export const useSessions = () =>
  useSWR<SessionSummary[]>("/research/sessions?limit=12", (p: string) => getJSON<SessionSummary[]>(p), swrOpts);
export const useTheses = () =>
  useSWR<ThesisListItem[]>("/theses", (p: string) => getJSON<ThesisListItem[]>(p), swrOpts);
export const useCompanies = () => useSWR<Instrument[]>("/companies", (p: string) => getJSON<Instrument[]>(p), swrOpts);
export const useCompany = (id: string) =>
  useSWR<CompanyPayload>(`/companies/${encodeURIComponent(id)}`, (p: string) => getJSON<CompanyPayload>(p, { maxAttempts: 1, timeoutMs: 45_000 }), swrOpts);
export const usePrices = (id: string) =>
  useSWR<{ bars: { t: string; close: number }[]; currency: string; source_id: string; freshness: { is_stale: boolean; as_of: string } }>(
    `/companies/${encodeURIComponent(id)}/prices`,
    (p: string) => getJSON(p, { maxAttempts: 1 }),
    swrOpts,
  );
export const useWatchlists = () => useSWR<Watchlist[]>("/watchlists", (p: string) => getJSON<Watchlist[]>(p), swrOpts);

export const HEALTH_LABEL: Record<Health, string> = { HEALTHY: "Healthy", TRIGGERED: "Triggered", UNKNOWN: "Unknown" };
export const STATUS_LABEL: Record<ConditionStatus, string> = {
  NOT_TRIGGERED: "Not triggered",
  TRIGGERED: "Triggered",
  UNKNOWN_MISSING_DATA: "UNKNOWN — Missing data",
};

export const fmtValue = (value: string | null | undefined, scale: string, currency?: string | null): string => {
  if (value === null || value === undefined) return "—";
  const v = Number(value);
  if (!Number.isFinite(v)) return value;
  if (scale === "PERCENT") return `${v.toFixed(1)}%`;
  if (scale === "RATIO") return v.toFixed(2);
  if (scale === "MILLIONS") {
    const p = ({ USD: "$", EUR: "€", TWD: "NT$", INR: "₹" } as Record<string, string>)[currency ?? ""] ?? "";
    return Math.abs(v) >= 1000 ? `${p}${(v / 1000).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}B` : `${p}${v.toLocaleString("en-US")}M`;
  }
  return `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${currency ? ` ${currency}` : ""}`;
};
