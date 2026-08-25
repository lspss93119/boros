import { describe, expect, it } from "vitest";
import { LIVE_REFETCH_INTERVAL_MS, liveQueryOptions } from "./hooks";

describe("live query contract", () => {
  it("uses ten-second polling and disables requests in static mode", () => {
    expect(LIVE_REFETCH_INTERVAL_MS).toBe(10_000);
    expect(liveQueryOptions(true).enabled).toBe(true);
    expect(liveQueryOptions(true).refetchInterval).toBe(10_000);
    expect(liveQueryOptions(false).enabled).toBe(false);
    expect(liveQueryOptions(false).refetchInterval).toBe(false);
  });
});
