import { useQuery } from "@tanstack/react-query";
import type { QueryObserverOptions } from "@tanstack/react-query";
import { dashboardFetch } from "./api";
import type { HealthData, P1Data, PositionsData, RadarData } from "./contracts";

export const LIVE_REFETCH_INTERVAL_MS = 10_000;

export function liveQueryOptions(enabled: boolean) {
  return {
    enabled,
    refetchInterval: enabled ? LIVE_REFETCH_INTERVAL_MS : false,
    retry: false,
    placeholderData: <T>(previous: T | undefined) => previous,
  } satisfies Pick<QueryObserverOptions, "enabled" | "refetchInterval" | "retry" | "placeholderData">;
}

export function useDashboardHealth(enabled: boolean) {
  return useQuery<HealthData>({
    queryKey: ["dashboard", "health"],
    queryFn: async () => (await dashboardFetch<HealthData>("/api/dashboard/health")).data,
    ...liveQueryOptions(enabled),
  });
}

export function useDashboardOpportunities(enabled: boolean) {
  return useQuery<P1Data>({
    queryKey: ["dashboard", "opportunities"],
    queryFn: async () =>
      (await dashboardFetch<P1Data>("/api/dashboard/opportunities")).data,
    ...liveQueryOptions(enabled),
  });
}

export function useDashboardPositions(enabled: boolean) {
  return useQuery<PositionsData>({
    queryKey: ["dashboard", "positions"],
    queryFn: async () => (await dashboardFetch<PositionsData>("/api/dashboard/positions")).data,
    ...liveQueryOptions(enabled),
  });
}

export const RADAR_PATH = "../data/boros_market_radar.json";

export function useRadar() {
  return useQuery<RadarData>({
    queryKey: ["static-radar"],
    queryFn: async () => {
      const response = await fetch(RADAR_PATH, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Radar data unavailable");
      return (await response.json()) as RadarData;
    },
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
}
