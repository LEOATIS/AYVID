"use client";
import useSWR from "swr";
import {
  getJSON,
  api,
  type EquityResponse,
  type RunnerStatus,
  type SignalsResponse,
  type BacktestSummary,
  type ShockScenario,
  type SystemStatus,
  type PipelineStatus,
} from "./api";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const fetcher = (path: string) => getJSON<any>(path);

const REFRESH = {
  realtime: 3_000,
  fast: 5_000,
  medium: 15_000,
  slow: 60_000,
};

export const useEquity = (limit = 500) =>
  useSWR<EquityResponse>(`/live/equity?limit=${limit}`, fetcher, { refreshInterval: REFRESH.medium });

export const useRunner = () =>
  useSWR<RunnerStatus>(`/live/runner-status`, fetcher, { refreshInterval: REFRESH.fast });

export const useSignals = () =>
  useSWR<SignalsResponse>(`/live/signals`, fetcher, {
    refreshInterval: REFRESH.realtime,
    dedupingInterval: 2_000,
    revalidateOnFocus: true,
  });

export const usePositions = () =>
  useSWR<{
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    positions: any[];
    count: number;
    as_of: string | null;
    cash?: number | null;
    mode?: string | null;
    max_mark_age_seconds?: number | null;
  }>(`/live/positions`, fetcher, { refreshInterval: REFRESH.medium });

export const useTrades = (limit = 50) =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useSWR<{ trades: any[]; count: number }>(`/live/trades?limit=${limit}`, fetcher, { refreshInterval: REFRESH.medium });

export const useBacktests = () =>
  useSWR<{ reports: BacktestSummary[]; count: number }>(`/live/backtests`, fetcher, { refreshInterval: REFRESH.slow });

export const useScenarios = () =>
  useSWR<{ scenarios: ShockScenario[]; count: number }>(`/live/scenarios`, fetcher, { refreshInterval: REFRESH.slow });

export const useSystemStatus = () =>
  useSWR<SystemStatus>(`/live/system-status`, fetcher, { refreshInterval: REFRESH.medium });

export const usePipelineStatus = () =>
  useSWR<PipelineStatus>(`/system/pipeline`, fetcher, {
    refreshInterval: REFRESH.medium,
    revalidateOnFocus: true,
  });

export { api };
