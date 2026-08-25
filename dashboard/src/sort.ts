import type { Classification, LiveOpportunity } from "./contracts";

export const PRIORITY_ORDER: Classification[] = [
  "EXCEPTIONAL",
  "HIGH_YIELD",
  "P99",
  "P95",
  "OTHER",
];

const PRIORITY_RANK: Record<Classification, number> = {
  EXCEPTIONAL: 0,
  HIGH_YIELD: 1,
  P99: 2,
  P95: 3,
  OTHER: 4,
};

export function classificationForOpportunity(item: LiveOpportunity): Classification {
  const copied = item.highYieldBand ?? item.historicalBand;
  return PRIORITY_ORDER.includes(copied as Classification)
    ? (copied as Classification)
    : "OTHER";
}

export function priorityRank(classification: string | null): number {
  return PRIORITY_RANK[classification as Classification] ?? PRIORITY_RANK.OTHER;
}

function signalApr(item: LiveOpportunity): number {
  const signal = item.sizes.find((size) => size.signalSize);
  return signal?.makerHedge.netFixedAprOnCapital ?? Number.NEGATIVE_INFINITY;
}

export function sortOpportunities(items: LiveOpportunity[]): LiveOpportunity[] {
  return [...items].sort((left, right) => {
    const rankDifference =
      priorityRank(classificationForOpportunity(left)) -
      priorityRank(classificationForOpportunity(right));
    if (rankDifference !== 0) return rankDifference;
    const aprDifference = signalApr(right) - signalApr(left);
    if (aprDifference !== 0) return aprDifference;
    const leftKey = `${left.asset}|${left.shortVenue}|${left.longVenue}|${left.maturity}|${left.identityKey}`;
    const rightKey = `${right.asset}|${right.shortVenue}|${right.longVenue}|${right.maturity}|${right.identityKey}`;
    return leftKey.localeCompare(rightKey);
  });
}
