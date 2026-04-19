/**
 * AYVID API client — typed thin wrapper around the FastAPI backend.
 * All paths go through the Next rewrite proxy at `/api/...` so calls
 * work in both dev (port 3000 → 8000) and standalone deploy.
 */

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const res = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let body = "";
    try {
      body = await res.text();
    } catch {}
    throw new ApiError(res.status, `${res.status} ${res.statusText} ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

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
}

export interface Signal {
  ticker: string;
  direction: "LONG" | "SHORT" | string;
  instrument_type: string;
  conviction: number;
  strategy_name: string;
  strike?: number | null;
  expiry_date?: string;
  stop_loss_pct?: number;
  take_profit_pct?: number;
  rationale?: string;
  computed_at: string;
  ttl_minutes?: number;
  expected_r?: number;
  min_r?: number;
  entry_price_at_compute?: number;
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

/* ─── Endpoints ─────────────────────────────────────────────────────── */

export const api = {
  equity: (limit = 500) => getJSON<EquityResponse>(`/live/equity?limit=${limit}`),
  runner: () => getJSON<RunnerStatus>("/live/runner-status"),
  trades: (limit = 200) => getJSON<{ trades: unknown[]; count: number }>(`/live/trades?limit=${limit}`),
  positions: () => getJSON<{ positions: unknown[]; count: number; as_of: string | null }>("/live/positions"),
  signals: () => getJSON<{ signals: Signal[]; count: number }>("/live/signals"),
  scenarios: () => getJSON<{ scenarios: ShockScenario[]; count: number }>("/live/scenarios"),
  backtests: () => getJSON<{ reports: BacktestSummary[]; count: number }>("/live/backtests"),
  backtest: (name: string) => getJSON<Record<string, unknown>>(`/live/backtests/${encodeURIComponent(name)}`),
  systemStatus: () => getJSON<SystemStatus>("/live/system-status"),
};
