import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { LIVE_REFETCH_INTERVAL_MS, liveQueryOptions, useDashboardHealth } from "./hooks";

describe("live query contract", () => {
  it("uses ten-second polling and disables requests in static mode", () => {
    expect(LIVE_REFETCH_INTERVAL_MS).toBe(10_000);
    expect(liveQueryOptions(true).enabled).toBe(true);
    expect(liveQueryOptions(true).refetchInterval).toBe(10_000);
    expect(liveQueryOptions(false).enabled).toBe(false);
    expect(liveQueryOptions(false).refetchInterval).toBe(false);
  });

  it("does not fetch live APIs when disabled", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true, data: {}, meta: { ts: 1 } }), { status: 200 }),
    );
    function Probe() {
      useDashboardHealth(false);
      return null;
    }
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <Probe />
      </QueryClientProvider>,
    );
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
