import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { HealthData, P1Data, PositionsData, RadarData } from "./contracts";

const state = vi.hoisted(() => ({
  health: {} as { data: HealthData; isError: boolean },
  opportunities: {} as { data: P1Data; isError: boolean },
  positions: {} as { data: PositionsData; isError: boolean },
  radar: {} as { data: RadarData; isError: boolean },
}));

vi.mock("./hooks", () => ({
  useDashboardHealth: () => state.health,
  useDashboardOpportunities: () => state.opportunities,
  useDashboardPositions: () => state.positions,
  useRadar: () => state.radar,
}));

import { App } from "./App";

const opportunity = {
  identityKey: "eth-key",
  asset: "ETH",
  maturity: "2026-09-25",
  shortVenue: "HYPERLIQUID",
  longVenue: "OKX",
  tokenId: 1,
  percentile90d: 98.7,
  historicalBand: "P99",
  highYieldBand: "EXCEPTIONAL",
  sizes: [
    { notionalUsd: 10000, signalSize: true, makerHedge: { available: true, netFixedAprOnCapital: 0.34, secondsToMaturity: 2678400 }, immediate: { available: true, netFixedAprOnCapital: 0.27 } },
    { notionalUsd: 25000, signalSize: false, makerHedge: { available: true, netFixedAprOnCapital: 0.31 }, immediate: { available: true, netFixedAprOnCapital: 0.25 } },
    { notionalUsd: 50000, signalSize: false, makerHedge: { available: true, netFixedAprOnCapital: 0.27 }, immediate: { available: true, netFixedAprOnCapital: 0.22 } },
    { notionalUsd: 100000, signalSize: false, makerHedge: { available: false }, immediate: { available: false } },
    { notionalUsd: 200000, signalSize: false, makerHedge: { available: false }, immediate: { available: false } },
  ],
};

const strategy = {
  strategyId: "strategy-1",
  base: "ETH",
  maturity: 1_800_000_000,
  secondsToMaturity: 1_000_000,
  legs: [
    { kind: "boros", venue: "Boros", base: "ETH", side: "short", notionalUsd: 500, netUsd: 1, tradePnlUsd: 2, feesUsd: 0.1, symbol: "ETH PT", warnings: [] },
    { kind: "boros", venue: "Boros", base: "ETH", side: "long", notionalUsd: 500, netUsd: 1, tradePnlUsd: 2, feesUsd: 0.1, symbol: "ETH YT", warnings: [] },
    { kind: "perp", venue: "Hyperliquid", base: "ETH", side: "short", notionalUsd: 500, netUsd: 1, tradePnlUsd: 2, feesUsd: 0.1, symbol: "ETH-PERP", warnings: [] },
    { kind: "perp", venue: "OKX", base: "ETH", side: "long", notionalUsd: 500, netUsd: 1, tradePnlUsd: 2, feesUsd: 0.1, symbol: "ETH-USD-SWAP", warnings: [] },
  ],
  hedge: {},
  hedgeChecks: { borosMatchRatio: 1, perpMatchRatio: 0.99, borosVsPerpRatio: 0.98, fullyHedged: true },
  capitalUsd: 1000,
  capitalSplit: { boros: 400, perp: 600 },
  realizedPnlUsd: 12,
  realizedApr: 0.03,
  spread: 0.1,
  lockedAprOnCapital: 0.2,
  expectedPnlToMaturityUsd: 100,
  notionalMismatchUsd: 2,
  attribution: { source: "cross-ex", confidence: 0.9, pinned: true, unclaimed: false },
  warnings: ["diagnostic"],
};

function setup(options?: { opportunityError?: boolean; opportunityFreshness?: "fresh" | "stale" | "offline" }) {
  state.health = {
    data: {
      status: "ok",
      server: "ok",
      components: {
        p1: { status: "ok", freshness: options?.opportunityFreshness ?? "fresh", cycleTimestamp: 2000, lastGoodTimestamp: 1900 },
        p2: { status: "ok", freshness: "fresh", cycleTimestamp: 2000, lastGoodTimestamp: 1900 },
      },
    },
    isError: false,
  };
  state.opportunities = {
    data: {
      counts: { opportunities: 1, mapped: 1, benchmarkable: 1, p95: 0, p99: 1, highYield: 0, exceptional: 1 },
      currentOpportunities: [opportunity],
      sourceStatus: options?.opportunityFreshness === "stale" ? "degraded" : "ok",
      freshness: options?.opportunityFreshness ?? "fresh",
      cycleTimestamp: 2000,
      lastGoodTimestamp: 1900,
      diagnostics: { benchmarkAgeSeconds: 60 },
    },
    isError: options?.opportunityError ?? false,
  };
  state.positions = {
    data: {
      strategies: [strategy],
      positions: { exposureGroups: [], asOfTimestamp: 2000, warnings: [] },
      sourceStatus: "ok",
      freshness: "fresh",
      cycleTimestamp: 2000,
      lastGoodTimestamp: 1900,
    },
    isError: false,
  };
  state.radar = {
    data: { rows: [{ id: 1 }], notionals: [10000, 25000, 50000], windowDays: 90 },
    isError: false,
  };
}

describe("dashboard UI acceptance", () => {
  it("renders signal versus capacity context and expanded four-leg position details", () => {
    setup();
    render(<App hostname="localhost" />);
    expect(screen.getByText("$10k = signal; larger = capacity context")).toBeInTheDocument();
    expect(screen.getByText(/\$100,000 capacity: — unavailable/)).toBeInTheDocument();
    expect(screen.getAllByText("Fully hedged").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Show details" }));
    expect(screen.getByText(/ETH PT/)).toBeInTheDocument();
    expect(screen.getByText(/ETH-USD-SWAP/)).toBeInTheDocument();
    expect(screen.getByText(/Hedge ratios/)).toBeInTheDocument();
    expect(screen.getByText(/Attribution/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /trade|close|roll|execute/i })).toBeNull();
  });

  it("retains prior rows and marks background refresh failure", () => {
    setup({ opportunityError: true, opportunityFreshness: "stale" });
    render(<App hostname="localhost" />);
    expect(screen.getAllByText("ETH").length).toBeGreaterThan(0);
    expect(screen.getByText("Background refresh failed; showing last valid rows.")).toBeInTheDocument();
    expect(screen.getByText("degraded / stale")).toHaveClass("status-degraded", "freshness-stale");
  });

  it("keeps static Radar usable without live monitor data", () => {
    setup();
    render(<App hostname="boros.pages.dev" />);
    expect(screen.getByText(/radar rows/)).toBeInTheDocument();
    expect(screen.getByText("Approved notionals: 10000, 25000, 50000")).toBeInTheDocument();
    expect(screen.getAllByText("Local monitor not connected")).toHaveLength(3);
  });
});
