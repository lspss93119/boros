export type Classification = "EXCEPTIONAL" | "HIGH_YIELD" | "P99" | "P95" | "OTHER";

export interface Economics {
  available: boolean;
  grossSpreadApr?: number | null;
  execSpreadApr?: number | null;
  borosImpactApr?: number | null;
  makerLeg?: string | null;
  costsTotalUsd?: number | null;
  capitalUsd?: number | null;
  netFixedApr?: number | null;
  netFixedAprOnCapital?: number | null;
  effectiveLeverage?: number | null;
  estProfitUsd?: number | null;
  secondsToMaturity?: number | null;
}

export interface LiveSize {
  notionalUsd: number;
  signalSize: boolean;
  makerHedge: Economics;
  immediate: Economics;
}

export interface LiveOpportunity {
  identityKey: string;
  asset: string;
  maturity: string;
  shortVenue: string;
  longVenue: string;
  tokenId: number;
  percentile90d: number | null;
  historicalBand: string | null;
  highYieldBand: string | null;
  sizes: LiveSize[];
}

export interface P1Data {
  counts: Record<string, number>;
  currentOpportunities: LiveOpportunity[];
  sourceStatus?: string;
  freshness?: Freshness;
  cycleTimestamp?: number;
  lastGoodTimestamp?: number | null;
  pollIntervalSeconds?: number;
  diagnostics?: Record<string, unknown>;
}

export interface PositionLeg {
  kind: string;
  venue: string;
  base: string;
  side: string;
  notionalUsd: number | null;
  netUsd: number | null;
  tradePnlUsd: number | null;
  feesUsd: number | null;
  symbol: string | null;
  warnings: string[];
  [key: string]: unknown;
}

export interface StrategySnapshot {
  strategyId: string;
  base: string;
  maturity: number;
  secondsToMaturity: number;
  legs: PositionLeg[];
  hedge: unknown;
  hedgeChecks: {
    borosMatchRatio: number | null;
    perpMatchRatio: number | null;
    borosVsPerpRatio: number | null;
    fullyHedged: boolean;
  };
  capitalUsd: number | null;
  capitalSplit: unknown;
  realizedPnlUsd: number | null;
  realizedApr: number | null;
  spread: number | null;
  lockedAprOnCapital: number | null;
  expectedPnlToMaturityUsd: number | null;
  notionalMismatchUsd: number | null;
  attribution: {
    source: string | null;
    confidence: number | null;
    pinned: boolean | null;
    unclaimed: boolean | null;
  };
  warnings: string[];
  hasWarnings?: boolean;
}

export interface PositionsData {
  strategies: StrategySnapshot[];
  positions: {
    exposureGroups: unknown[];
    asOfTimestamp: number;
    warnings: string[];
    hasWarnings?: boolean;
  } | null;
  sourceStatus?: string;
  freshness?: Freshness;
  cycleTimestamp?: number;
  lastGoodTimestamp?: number | null;
  pollIntervalSeconds?: number;
  diagnostics?: Record<string, unknown>;
}

export interface HealthComponent {
  status: string;
  freshness: Freshness;
  cycleTimestamp?: number;
  lastGoodTimestamp?: number | null;
  pollIntervalSeconds?: number;
}

export interface HealthData {
  status: string;
  server: string;
  components: { p1: HealthComponent; p2: HealthComponent };
}

export interface RadarData {
  generatedAt?: string;
  historicalMaxTimestamp?: number;
  notionals?: number[];
  rows?: unknown[];
  windowDays?: number;
  [key: string]: unknown;
}

export type Freshness = "fresh" | "stale" | "offline";

export interface ApiEnvelope<T> {
  ok: true;
  data: T;
  meta: { ts: number };
}

export interface ApiErrorEnvelope {
  ok: false;
  error: { code: string; message: string };
  meta: { ts: number };
}
