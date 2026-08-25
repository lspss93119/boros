import { useMemo, useState } from "react";
import { isLocalMode } from "./mode";
import {
  useDashboardHealth,
  useDashboardOpportunities,
  useDashboardPositions,
  useRadar,
} from "./hooks";
import { classificationForOpportunity, sortOpportunities } from "./sort";
import type { Economics, LiveOpportunity, StrategySnapshot } from "./contracts";
import "./styles.css";

export interface AppProps {
  hostname?: string;
}

function percent(value: number | null | undefined): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function money(value: number | null | undefined): string {
  return value == null
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
}

function dte(seconds: number | null | undefined): string {
  return seconds == null ? "—" : `${(seconds / 86400).toFixed(1)}d`;
}

function statusText(sourceStatus: string | undefined, freshness: string | undefined): string {
  return [sourceStatus ?? "offline", freshness ?? "offline"].join(" / ");
}

function signalEconomics(opportunity: LiveOpportunity): Economics | undefined {
  return opportunity.sizes.find((size) => size.signalSize)?.makerHedge;
}

function referenceEconomics(opportunity: LiveOpportunity): Economics | undefined {
  return opportunity.sizes.find((size) => size.signalSize)?.immediate;
}

function LiveState({ text }: { text: string }) {
  return <p className="state-copy">{text}</p>;
}

function Opportunities({
  local,
  data,
  isError,
}: {
  local: boolean;
  data: ReturnType<typeof useDashboardOpportunities>["data"];
  isError: boolean;
}) {
  if (!local) return <LiveState text="Local monitor not connected" />;
  const opportunities = sortOpportunities(data?.currentOpportunities ?? []);
  if (isError && opportunities.length === 0) return <LiveState text="Live opportunities unavailable" />;
  if (opportunities.length === 0) return <LiveState text="No current opportunities" />;
  return (
    <div className="table-stack">
      {opportunities.slice(0, 20).map((opportunity) => {
        const signal = signalEconomics(opportunity);
        const reference = referenceEconomics(opportunity);
        return (
          <article className="data-row" key={opportunity.identityKey}>
            <div className="row-main">
              <div>
                <strong>{opportunity.asset}</strong>
                <span className="muted"> {opportunity.shortVenue} → {opportunity.longVenue}</span>
              </div>
              <span className={`badge badge-${classificationForOpportunity(opportunity).toLowerCase()}`}>
                {classificationForOpportunity(opportunity)}
              </span>
            </div>
            <div className="metric-grid">
              <span>Maturity <b>{opportunity.maturity}</b></span>
              <span>DTE <b>{dte(signal?.secondsToMaturity)}</b></span>
              <span>90D <b>{opportunity.percentile90d == null ? "—" : `P${opportunity.percentile90d.toFixed(1)}`}</b></span>
              <span>$10k Limit + Hedge <b>{percent(signal?.netFixedAprOnCapital)}</b></span>
              <span>$10k immediate reference <b>{percent(reference?.netFixedAprOnCapital)}</b></span>
            </div>
            <details>
              <summary>Capacity context <span className="muted">$10k = signal; larger = capacity context</span></summary>
              <div className="capacity-grid">
                {opportunity.sizes.map((size) => (
                  <span key={size.notionalUsd}>
                    ${size.notionalUsd.toLocaleString()} {size.signalSize ? "signal" : "capacity"}: {size.makerHedge.available ? percent(size.makerHedge.netFixedAprOnCapital) : "— unavailable"}
                  </span>
                ))}
              </div>
            </details>
            {isError && <span className="stale-note">Background refresh failed; showing last valid rows.</span>}
          </article>
        );
      })}
    </div>
  );
}

function Positions({
  local,
  data,
  isError,
}: {
  local: boolean;
  data: ReturnType<typeof useDashboardPositions>["data"];
  isError: boolean;
}) {
  if (!local) return <LiveState text="Local monitor not connected" />;
  const strategies = data?.strategies ?? [];
  if (isError && strategies.length === 0) return <LiveState text="Open positions unavailable" />;
  if (strategies.length === 0) return <LiveState text="No open strategies" />;
  return (
    <div className="table-stack">
      {strategies.map((strategy) => <PositionRow key={strategy.strategyId} strategy={strategy} />)}
    </div>
  );
}

function PositionRow({ strategy }: { strategy: StrategySnapshot }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <article className="data-row">
      <div className="row-main">
        <div><strong>{strategy.base}</strong><span className="muted"> {strategy.strategyId}</span></div>
        <span className={strategy.hedgeChecks.fullyHedged ? "healthy" : "warning"}>{strategy.hedgeChecks.fullyHedged ? "Fully hedged" : "Hedge warning"}</span>
      </div>
      <div className="metric-grid">
        <span>Maturity <b>{new Date(strategy.maturity * 1000).toISOString().slice(0, 10)}</b></span>
        <span>DTE <b>{dte(strategy.secondsToMaturity)}</b></span>
        <span>Locked spread <b>{percent(strategy.spread)}</b></span>
        <span>Locked APR <b>{percent(strategy.lockedAprOnCapital)}</b></span>
        <span>Expected PnL <b>{money(strategy.expectedPnlToMaturityUsd)}</b></span>
        <span>Capital <b>{money(strategy.capitalUsd)}</b></span>
        <span>Mismatch <b>{money(strategy.notionalMismatchUsd)}</b></span>
      </div>
      <button className="disclosure" type="button" onClick={() => setExpanded((value) => !value)}>
        {expanded ? "Hide details" : "Show details"}
      </button>
      {expanded && (
        <div className="detail-panel">
          <div><b>Hedge ratios</b> Boros {percent(strategy.hedgeChecks.borosMatchRatio)} · Perp {percent(strategy.hedgeChecks.perpMatchRatio)} · Boros/Perp {percent(strategy.hedgeChecks.borosVsPerpRatio)}</div>
          <div><b>Realized</b> {money(strategy.realizedPnlUsd)} / {percent(strategy.realizedApr)}</div>
          <div><b>Attribution</b> {strategy.attribution.source ?? "—"} · confidence {percent(strategy.attribution.confidence)}</div>
          <div><b>Capital split</b> {JSON.stringify(strategy.capitalSplit ?? {})}</div>
          <div><b>Legs</b> {strategy.legs.length}</div>
          {strategy.warnings.length > 0 && <div className="warning">Diagnostics present: {strategy.warnings.length}</div>}
        </div>
      )}
    </article>
  );
}

export function App({ hostname = window.location.hostname }: AppProps) {
  const local = isLocalMode(hostname);
  const health = useDashboardHealth(local);
  const opportunities = useDashboardOpportunities(local);
  const positions = useDashboardPositions(local);
  const radar = useRadar();
  const radarSummary = useMemo(() => ({
    rows: radar.data?.rows?.length ?? 0,
    notionals: radar.data?.notionals ?? [],
    windowDays: radar.data?.windowDays,
  }), [radar.data]);

  return (
    <main className="dashboard-shell">
      <header className="hero">
        <div><p className="eyebrow">BOROS RESEARCH COMPANION</p><h1>Unified Dashboard</h1><p className="muted">Python snapshots are the source of truth. This interface is read-only.</p></div>
        <span className={local ? "mode-pill live" : "mode-pill static"}>{local ? "LOCAL LIVE" : "STATIC RESEARCH"}</span>
      </header>

      <section className="kpi-grid" aria-label="Summary KPIs">
        <div className="kpi"><span>High Yield</span><strong>{opportunities.data?.counts.highYield ?? "—"}</strong></div>
        <div className="kpi"><span>Exceptional</span><strong>{opportunities.data?.counts.exceptional ?? "—"}</strong></div>
        <div className="kpi"><span>Historical P95/P99</span><strong>{opportunities.data ? `${opportunities.data.counts.p95 ?? 0}/${opportunities.data.counts.p99 ?? 0}` : "—"}</strong></div>
        <div className="kpi"><span>Open strategies</span><strong>{positions.data?.strategies.length ?? "—"}</strong></div>
        <div className="kpi"><span>Fully hedged</span><strong>{positions.data ? positions.data.strategies.filter((item) => item.hedgeChecks.fullyHedged).length : "—"}</strong></div>
      </section>

      <section className="panel" aria-labelledby="live-opportunities-heading">
        <div className="section-heading"><div><p className="eyebrow">P1</p><h2 id="live-opportunities-heading">Live Opportunities</h2></div><span className="status-text">{statusText(opportunities.data?.sourceStatus, opportunities.data?.freshness)}</span></div>
        <Opportunities local={local} data={opportunities.data} isError={opportunities.isError} />
      </section>

      <section className="panel" aria-labelledby="open-positions-heading">
        <div className="section-heading"><div><p className="eyebrow">P2</p><h2 id="open-positions-heading">Open Positions</h2></div><span className="status-text">{statusText(positions.data?.sourceStatus, positions.data?.freshness)}</span></div>
        <Positions local={local} data={positions.data} isError={positions.isError} />
      </section>

      <div className="two-column">
        <section className="panel" aria-labelledby="radar-heading">
          <div className="section-heading"><div><p className="eyebrow">RESEARCH</p><h2 id="radar-heading">Market Radar</h2></div><a href="../radar.html">Open Radar →</a></div>
          {radar.isError ? <LiveState text="Radar data unavailable" /> : <div className="radar-summary"><strong>{radarSummary.rows}</strong><span>radar rows · {radarSummary.windowDays ?? "—"} day window</span><small>Approved notionals: {radarSummary.notionals.join(", ") || "—"}</small></div>}
        </section>

        <section className="panel" aria-labelledby="health-heading">
          <div className="section-heading"><div><p className="eyebrow">OPERATIONS</p><h2 id="health-heading">System Health</h2></div><span className="status-text">{local ? (health.data?.status ?? "offline") : "offline"}</span></div>
          {!local ? <LiveState text="Local monitor not connected" /> : <div className="health-list"><span>Process freshness <b>{health.data?.server ?? "—"}</b></span><span>P1 cycle <b>{health.data?.components.p1.cycleTimestamp ?? "—"}</b></span><span>P1 last good <b>{health.data?.components.p1.lastGoodTimestamp ?? "—"}</b></span><span>P2 primary / auxiliary <b>{health.data ? `${health.data.components.p2.status} / ${health.data.components.p2.freshness}` : "—"}</b></span></div>}
        </section>
      </div>

      <footer className="research-links"><span>Research</span><a href="../index.html">Historical APR</a><a href="../radar.html">Market Radar</a><a href="../arbitrage.html">Arbitrage Research</a></footer>
    </main>
  );
}
