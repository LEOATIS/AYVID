"use client";
import useSWR from "swr";
import { api } from "./api";

const fetcher = async (url: string) => {
  const res = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
};

const REFRESH = {
  fast: 5_000,
  medium: 15_000,
  slow: 60_000,
};

export const useEquity = (limit = 500) =>
  useSWR(`/api/v1/live/equity?limit=${limit}`, fetcher, { refreshInterval: REFRESH.medium });

export const useRunner = () =>
  useSWR(`/api/v1/live/runner-status`, fetcher, { refreshInterval: REFRESH.fast });

export const useSignals = () =>
  useSWR(`/api/v1/live/signals`, fetcher, { refreshInterval: REFRESH.medium });

export const usePositions = () =>
  useSWR(`/api/v1/live/positions`, fetcher, { refreshInterval: REFRESH.medium });

export const useTrades = (limit = 50) =>
  useSWR(`/api/v1/live/trades?limit=${limit}`, fetcher, { refreshInterval: REFRESH.medium });

export const useBacktests = () =>
  useSWR(`/api/v1/live/backtests`, fetcher, { refreshInterval: REFRESH.slow });

export const useScenarios = () =>
  useSWR(`/api/v1/live/scenarios`, fetcher, { refreshInterval: REFRESH.slow });

export const useSystemStatus = () =>
  useSWR(`/api/v1/live/system-status`, fetcher, { refreshInterval: REFRESH.medium });

export { api };
