/**
 * AYVID API client — typed thin wrapper around the FastAPI backend.
 *
 * Production-grade contract:
 *   • Deadlines (AbortController) so no fetch can hang a tab.
 *   • Retry-with-backoff on network errors and 5xx ONLY. 4xx errors
 *     are deterministic ("auth", "not found", "desync") and must
 *     surface immediately — retrying them burns cycles without hope.
 *   • Typed error classes (`ApiAuthError`, `ApiNotFoundError`,
 *     `ApiDesyncError`, `ApiServerError`) so UI code can `instanceof`
 *     instead of inspecting strings.
 *   • Request-ID echo so a support ticket can copy-paste one string
 *     that ties directly to the backend log trail. The backend's
 *     observability layer stamps `X-Request-ID` on every response.
 */

// Resolve the API base at build time. In local dev (`next dev`) we leave
// it at `/api/v1` so Next's same-origin proxying / rewrites keep working.
// In production the Vercel-hosted frontend lives at `ayvid.ai` and the
// FastAPI backend at `api.ayvid.ai`, so `NEXT_PUBLIC_API_BASE` is set in
// `frontend/.env.production` (and again in Vercel project env) to the
// fully-qualified URL. Strip a trailing slash to keep concatenation
// predictable: callers always pass paths starting with `/`.
const _RAW_BASE = (process.env.NEXT_PUBLIC_API_BASE || "/api/v1").trim();
const BASE = _RAW_BASE.endsWith("/") ? _RAW_BASE.slice(0, -1) : _RAW_BASE;
const API_KEY = (process.env.NEXT_PUBLIC_API_KEY || "").trim();

// Conservative defaults. Callers that need different values can
// override per-request via `init` (AbortSignal) or via the
// `fetchJSON` options object.
const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_BASE_BACKOFF_MS = 300;

/** Base class — every AYVID API error derives from this. */
export class ApiError extends Error {
  public readonly requestId: string | null;
  constructor(
    public status: number,
    message: string,
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.requestId = requestId;
  }
}

/** 401 / 403 — caller sent wrong or missing credentials. */
export class ApiAuthError extends ApiError {
  constructor(status: number, message: string, requestId: string | null) {
    super(status, message, requestId);
    this.name = "ApiAuthError";
  }
}

/** 404 — resource does not exist. Never retried. */
export class ApiNotFoundError extends ApiError {
  constructor(message: string, requestId: string | null) {
    super(404, message, requestId);
    this.name = "ApiNotFoundError";
  }
}

/** 409 — data-plane desync / business-rule rejection. */
export class ApiDesyncError extends ApiError {
  constructor(message: string, requestId: string | null) {
    super(409, message, requestId);
    this.name = "ApiDesyncError";
  }
}

/** 5xx — server-side fault. Retriable (and retried). */
export class ApiServerError extends ApiError {
  constructor(status: number, message: string, requestId: string | null) {
    super(status, message, requestId);
    this.name = "ApiServerError";
  }
}

export interface FetchOptions extends RequestInit {
  /** Hard deadline in milliseconds. Default `DEFAULT_TIMEOUT_MS`. */
  timeoutMs?: number;
  /** Max total attempts. `1` disables retry. Default `DEFAULT_MAX_ATTEMPTS`. */
  maxAttempts?: number;
  /** Base backoff in ms. Each retry waits `base × 2^n`. Default 300. */
  baseBackoffMs?: number;
}

function _sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function _mapHttpError(
  status: number,
  statusText: string,
  body: string,
  requestId: string | null,
): ApiError {
  const detail = body.slice(0, 400);
  if (status === 401 || status === 403) {
    return new ApiAuthError(status, `${status} ${statusText}: ${detail}`, requestId);
  }
  if (status === 404) {
    return new ApiNotFoundError(`${statusText}: ${detail}`, requestId);
  }
  if (status === 409) {
    return new ApiDesyncError(`${statusText}: ${detail}`, requestId);
  }
  if (status >= 500) {
    return new ApiServerError(status, `${status} ${statusText}: ${detail}`, requestId);
  }
  return new ApiError(status, `${status} ${statusText}: ${detail}`, requestId);
}

async function _fetchOnce(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // Respect an upstream AbortSignal if the caller passed one; otherwise
    // attach our own so the timeout fires.
    const signal = init.signal ?? controller.signal;
    return await fetch(url, { ...init, signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function getJSON<T>(
  path: string,
  init?: FetchOptions,
): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const timeoutMs = init?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const maxAttempts = Math.max(1, init?.maxAttempts ?? DEFAULT_MAX_ATTEMPTS);
  const baseBackoffMs = init?.baseBackoffMs ?? DEFAULT_BASE_BACKOFF_MS;

  let lastErr: unknown = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (init?.headers) {
        const merged = new Headers(init.headers);
        merged.forEach((v, k) => {
          headers[k] = v;
        });
      }
      if (API_KEY) {
        headers["X-API-Key"] = API_KEY;
      }

      const res = await _fetchOnce(
        url,
        {
          cache: "no-store",
          ...init,
          headers,
        },
        timeoutMs,
      );
      const requestId = res.headers.get("X-Request-ID");
      if (res.ok) {
        return (await res.json()) as T;
      }

      let body = "";
      try {
        body = await res.text();
      } catch {
        // swallow — body may have been consumed or malformed
      }
      const err = _mapHttpError(res.status, res.statusText, body, requestId);

      // Retry 5xx; surface 4xx immediately.
      if (err instanceof ApiServerError && attempt < maxAttempts) {
        lastErr = err;
        await _sleep(baseBackoffMs * 2 ** (attempt - 1));
        continue;
      }
      throw err;
    } catch (exc) {
      // Network-level failure (TypeError from fetch, AbortError from
      // timeout). Retriable same as 5xx.
      if (exc instanceof ApiError) {
        throw exc; // already an HTTP error — do not re-wrap
      }
      lastErr = exc;
      if (attempt < maxAttempts) {
        await _sleep(baseBackoffMs * 2 ** (attempt - 1));
        continue;
      }
      const msg = exc instanceof Error ? exc.message : String(exc);
      throw new ApiError(0, `network error after ${attempt} attempts: ${msg}`, null);
    }
  }
  // Unreachable — the loop above always throws or returns. Kept for the
  // type-checker.
  throw (lastErr ?? new ApiError(0, "api client: unknown fault", null));
}

/**
 * Mutating requests share `getJSON`'s deadline + typed-error contract but
 * default to ONE attempt: a retried POST could create a duplicate thesis.
 */
function _sendJSON<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
  init?: FetchOptions,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  return getJSON<T>(path, {
    maxAttempts: 1,
    ...init,
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const postJSON = <T>(path: string, body?: unknown, init?: FetchOptions) =>
  _sendJSON<T>("POST", path, body, init);
export const putJSON = <T>(path: string, body?: unknown, init?: FetchOptions) =>
  _sendJSON<T>("PUT", path, body, init);
export const patchJSON = <T>(path: string, body?: unknown, init?: FetchOptions) =>
  _sendJSON<T>("PATCH", path, body, init);
export const deleteJSON = <T>(path: string, init?: FetchOptions) =>
  _sendJSON<T>("DELETE", path, undefined, init);

/* ─── Types ─────────────────────────────────────────────────────────── */

export interface EquitySnapshot {
  timestamp: string;
  mode: string;
  equity: number;
  cash: number;
  position_value: number;
  open_positions: number;
  daily_pnl: number;
  cumulative_pnl: number;
  cumulative_return_pct: number;
  cycle: number;
  notes: string;
}

export interface EquitySummary {
  snapshots_count: number;
  initial_equity: number;
  final_equity: number;
  peak_equity: number;
  total_return_pct: number;
  drawdown_from_peak_pct: number;
  first_timestamp: string;
  last_timestamp: string;
  mode: string;
  open_positions: number;
  cycle: number;
}

export interface EquityResponse {
  snapshots: EquitySnapshot[];
  summary: EquitySummary | null;
  meta: RunMeta | null;
}

export interface RunMeta {
  mode: string;
  initial_equity: number;
  interval_seconds: number;
  started_at: string;
  ib_host: string;
  ib_port: number;
  auto_execute: boolean;
  paper_trading: boolean;
}

export interface RunnerStatus {
  is_alive: boolean;
  mode: string | null;
  ib_host: string | null;
  ib_port: number | null;
  auto_execute: boolean | null;
  paper_trading: boolean;
  interval_seconds: number;
  started_at: string | null;
  uptime_seconds: number | null;
  last_snapshot_at: string | null;
  last_snapshot_age_seconds: number | null;
  snapshots_total: number;
}

export interface BacktestSummary {
  name: string;
  filename: string;
  size_bytes: number;
  kind: "walkforward" | "shock_driven" | "single_window" | "unknown";
  generated_at?: string | null;
  span?: string;
  windows?: number;
  scenarios?: number;
  avg_return_pct?: number | null;
  median_return_pct?: number | null;
  compounded_return_pct?: number | null;
  cagr_pct?: number | null;
  pct_positive?: number | null;
  median_sharpe?: number | null;
  pooled_trades?: number | null;
  total_return_pct?: number | null;
  sharpe_ratio?: number | null;
  max_drawdown_pct?: number | null;
  total_trades?: number | null;
  // Side-by-side comparison metrics surfaced by _summarize_backtest
  // for both walkforward and shock_driven shapes.
  pooled_win_rate?: number | null;
  avg_sharpe?: number | null;
  worst_drawdown_pct?: number | null;
  avg_cost_drag_pct?: number | null;
}

export type ReadinessStatus = "WAITING" | "READY" | "EXPIRED" | "CANCELLED";
export type SignalDirection = "LONG" | "SHORT" | "HOLD";
export type SignalAssetClass =
  | "EQUITY"
  | "INDEX"
  | "FX"
  | "FUTURE"
  | "CRYPTO"
  | "ETF"
  | "OPTION"
  | "COMMODITY"
  | "BOND";

export interface Signal {
  // Identity
  ticker: string;
  asset_class: SignalAssetClass | string;
  instrument_type: string;
  strategy_name: string;
  direction: SignalDirection | string;

  // Pricing
  entry_price: number | null;
  current_price: number | null;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  stop_loss_pct: number | null;
  take_profit_pct: number | null;
  risk_reward_ratio: number | null;
  distance_to_entry_pct: number | null;
  entry_zone_low: number | null;
  entry_zone_high: number | null;
  entry_price_at_compute: number | null;

  // Sizing
  quantity: number | null;
  notional_value: number | null;
  risk_amount: number | null;
  kelly_fraction: number | null;
  lot_size: number | null;
  contract_multiplier: number | null;
  pip_value: number | null;
  margin_required: number | null;

  // Options
  strike: number | null;
  expiry_date: string | null;
  option_type: "CALL" | "PUT" | null;
  leg_2_strike: number | null;
  leg_2_expiry: string | null;
  second_leg: Record<string, unknown> | null;

  // Quality
  conviction: number;
  factor_score: number | null;
  expected_r: number | null;
  min_r: number | null;
  rationale: string | null;

  // Readiness
  readiness_status: ReadinessStatus | string;
  readiness_reason: string | null;
  readiness_checked_at: string | null;

  // Timestamps + metadata
  computed_at: string;
  expires_at: string | null;
  ttl_minutes: number | null;
  regime_fingerprint: string | null;
  thesis_signature: string | null;
  tier3_shock: boolean | null;
  price_at_compute: number | null;
}

export interface SignalsResponse {
  signals: Signal[];
  count: number;
  ready_count: number;
  waiting_count: number;
  expired_count: number;
  cancelled_count: number;
  total: number;
  snapshot_at: string | null;
}

/** A single row from the immortal signal_history.jsonl ledger. */
export interface SignalHistoryRecord extends Partial<Signal> {
  event: string;            // CREATED, EXPIRED, STATUS_READY, BACKFILLED_CREATED, …
  logged_at: string;        // ISO timestamp of the event itself
  key: string;              // <ticker>|<regime_fingerprint>
}

export interface SignalsHistoryResponse {
  records: SignalHistoryRecord[];
  total: number;
  by_status: Record<string, number>;
  by_asset_class: Record<string, number>;
  by_event: Record<string, number>;
  by_ticker: Record<string, number>;
  earliest: string | null;
  latest: string | null;
  snapshot_at: string;
}

export interface SignalReadinessEvent {
  type: "snapshot" | "readiness_change" | "heartbeat";
  ticker?: string;
  asset_class?: string;
  direction?: SignalDirection | string;
  readiness_status?: ReadinessStatus | string;
  readiness_reason?: string | null;
  current_price?: number | null;
  entry_price?: number | null;
  stop_loss_price?: number | null;
  take_profit_price?: number | null;
  quantity?: number | null;
  distance_to_entry_pct?: number | null;
  conviction?: number;
  strategy_name?: string;
}

export interface ShockScenario {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  description: string;
  tickers: string[];
  indices: string[];
  shock_type?: string;
  expected_direction?: string;
  beneficiary_tickers?: string[];
  victim_tickers?: string[];
  index_bias?: Record<string, string>;
  causal_chain?: string;
}

export interface SystemStatus {
  api: string;
  paper_runner: RunnerStatus;
  neo4j: {
    ok: boolean;
    stats: Record<string, number> | null;
    error: string | null;
    uri: string | null;
  };
  llm: {
    ok: boolean;
    provider: string | null;
    model: string | null;
    has_key: boolean;
  };
  latest_backtest: BacktestSummary | null;
  config: {
    llm_provider: string | null;
    llm_model: string | null;
    ib_host: string | null;
    ib_port: number | null;
    auto_execute: boolean;
  };
}

export interface PipelineStatus {
  orchestration_mode: "llm_swarm" | "rule_based";
  llm_orchestration_enabled: boolean;
  llm_signal_timeout_seconds: number;
  llm_max_concurrent_signals: number;
  last_pipeline_run: string | null;
  signals_generated_last_cycle: number;
  execution_mode: "shadow" | "ib_paper" | "ib_live";
  auto_execute: boolean;
  live_confirmed: boolean;
  max_single_order_notional: number;
  asset_classes_enabled: string[];
  open_positions: number;
  signals_total: number;
  signals_waiting: number;
  signals_ready: number;
  signals_expired: number;
  signals_cancelled: number;
  trades_today: number;
  snapshot_at: string;
}

/* ─── Endpoints ─────────────────────────────────────────────────────── */

export const api = {
  equity: (limit = 500) => getJSON<EquityResponse>(`/live/equity?limit=${limit}`),
  runner: () => getJSON<RunnerStatus>("/live/runner-status"),
  trades: (limit = 200) => getJSON<{ trades: unknown[]; count: number }>(`/live/trades?limit=${limit}`),
  positions: () => getJSON<{ positions: unknown[]; count: number; as_of: string | null }>("/live/positions"),
  signals: () => getJSON<SignalsResponse>("/live/signals"),
  signalsHistory: (params: {
    start?: string;
    end?: string;
    ticker?: string;
    asset_class?: string;
    status?: string;
    event?: string;
    limit?: number;
  } = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    }
    const qs = q.toString();
    return getJSON<SignalsHistoryResponse>(`/live/signals/history${qs ? `?${qs}` : ""}`);
  },
  scenarios: () => getJSON<{ scenarios: ShockScenario[]; count: number }>("/live/scenarios"),
  backtests: () => getJSON<{ reports: BacktestSummary[]; count: number }>("/live/backtests"),
  backtest: (name: string) => getJSON<Record<string, unknown>>(`/live/backtests/${encodeURIComponent(name)}`),
  systemStatus: () => getJSON<SystemStatus>("/live/system-status"),
  pipeline: () => getJSON<PipelineStatus>("/system/pipeline"),
};
