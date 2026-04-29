"use client";
import useSWR from "swr";
import {
  getJSON,
  api,
  type EquityResponse,
  type RunnerStatus,
  type Signal,
  type BacktestSummary,
  type ShockScenario,
  type SystemStatus,
} from "./api";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const fetcher = (path: string) => getJSON<any>(path);

const REFRESH = {
  fast: 5_000,
  medium: 15_000,
  slow: 60_000,
};

export const useEquity = (limit = 500) =>
  useSWR<EquityResponse>(`/live/equity?limit=${limit}`, fetcher, { refreshInterval: REFRESH.medium });

export const useRunner = () =>
  useSWR<RunnerStatus>(`/live/runner-status`, fetcher, { refreshInterval: REFRESH.fast });

export const useSignals = () =>
  useSWR<{ signals: Signal[]; count: number }>(`/live/signals`, fetcher, { refreshInterval: REFRESH.medium });

export const usePositions = () =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useSWR<{ positions: any[]; count: number; as_of: string | null }>(`/live/positions`, fetcher, { refreshInterval: REFRESH.medium });

export const useTrades = (limit = 50) =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useSWR<{ trades: any[]; count: number }>(`/live/trades?limit=${limit}`, fetcher, { refreshInterval: REFRESH.medium });

export const useBacktests = () =>
  useSWR<{ reports: BacktestSummary[]; count: number }>(`/live/backtests`, fetcher, { refreshInterval: REFRESH.slow });

export const useScenarios = () =>
  useSWR<{ scenarios: ShockScenario[]; count: number }>(`/live/scenarios`, fetcher, { refreshInterval: REFRESH.slow });

export const useSystemStatus = () =>
  useSWR<SystemStatus>(`/live/system-status`, fetcher, { refreshInterval: REFRESH.medium });

export { api };
