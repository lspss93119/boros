import { describe, expect, it } from "vitest";
import { PRIORITY_ORDER, sortOpportunities } from "./sort";
import type { LiveOpportunity } from "./contracts";

function opportunity(
  classification: string,
  asset: string,
  apr: number,
): LiveOpportunity {
  return {
    identityKey: `${asset}-key`,
    asset,
    maturity: "2026-09-25",
    shortVenue: "HYPERLIQUID",
    longVenue: "OKX",
    tokenId: 1,
    percentile90d: null,
    historicalBand: null,
    highYieldBand: classification === "OTHER" ? null : classification,
    sizes: [
      {
        notionalUsd: 10000,
        signalSize: true,
        makerHedge: { available: true, netFixedAprOnCapital: apr },
        immediate: { available: true, netFixedAprOnCapital: apr - 0.01 },
      },
    ],
  };
}

describe("opportunity priority", () => {
  it("uses copied classification order, APR tie-break, and deterministic asset order", () => {
    expect(PRIORITY_ORDER).toEqual(["EXCEPTIONAL", "HIGH_YIELD", "P99", "P95", "OTHER"]);
    const sorted = sortOpportunities([
      opportunity("P95", "ZETA", 0.5),
      opportunity("EXCEPTIONAL", "ETH", 0.2),
      opportunity("HIGH_YIELD", "BTC", 0.3),
      opportunity("EXCEPTIONAL", "BTC", 0.4),
      opportunity("P99", "SOL", 0.9),
    ]);
    expect(sorted.map((item) => item.asset)).toEqual(["BTC", "ETH", "BTC", "SOL", "ZETA"]);
  });
});
