# Boros Unified Dashboard Design

Date: 2026-08-25

## Purpose and boundary

P5 adds one unified local control center for the existing read-only research
companion. Python remains the source of truth for CrossEx economics,
historical classifications, High-Yield decisions, P2 lifecycle state, source
health, and Radar data. React only presents snapshots and server responses; it
must never calculate a second financial or alerting model.

The dashboard server is a localhost-only read-only bridge. It publishes the
latest Python monitor snapshots through GET APIs and serves the static React
bundle. It never calls CrossEx mutation routes, Telegram, account, wallet,
order, execution, roll, or close operations.

## Modes

Local mode is enabled only when `window.location.hostname` is exactly
`localhost` or `127.0.0.1`. It polls the dashboard health, opportunities, and
positions APIs every 10 seconds. GitHub Pages and every other hostname use
static mode: the live opportunity, open-position, and live system-health areas
explicitly say `Local monitor not connected`; the static Market Radar and
existing research links remain usable. Pages never attempts to fetch
localhost.

The command is:

```text
python3 -m boros_research.cli dashboard
```

It binds only to `127.0.0.1`, defaults to port 8765, and accepts `--port`,
`--site-dir`, and `--snapshot-dir`. There is no host override.

## Python monitor view contracts

P1 exposes an additive `LiveOpportunityView` for every current identity with a
$10k primary, not only identities that sent Telegram. It copies the already
computed historical and High-Yield decisions:

- historical: P99, then P95, otherwise absent;
- High-Yield: EXCEPTIONAL, then HIGH_YIELD, otherwise absent.

No dashboard code reruns P95/P99/HY20/HY30 threshold logic. Each view retains
identity, percentile, copied bands, and all five existing `LiveSizeRecord`
rows. `$10k` is the only signal size; larger rows are capacity context.

P2 extends its cycle result additively with normalized strategy snapshots and
the normalized auxiliary positions snapshot. Snapshot publication never calls
or changes `PositionStateStore` decisions.

Both monitors have optional best-effort cycle observers. The observer receives
`(result, None)` after a successful cycle or `(None, exception_type)` when the
cycle raises. Observer exceptions are swallowed and cannot affect alert
delivery, lifecycle state, or the original exception.

## Snapshot contract

P1 and P2 publish separate files:

```text
data/dashboard/p1_latest.json
data/dashboard/p2_latest.json
```

Every file has:

```json
{
  "schemaVersion": 1,
  "kind": "p1",
  "cycleTimestamp": 0,
  "pollIntervalSeconds": 60,
  "sourceStatus": "ok",
  "lastGoodTimestamp": null,
  "data": {},
  "diagnostics": {}
}
```

JSON is finite and written atomically through a same-directory temporary file,
flush/fsync, and `os.replace`. A failed serialization or write removes only
the temporary file and leaves the previous valid snapshot intact. Readers
reject malformed envelopes, incompatible schema/kind, and recursively
non-finite values.

P1 reports CrossEx `ok`/`degraded`, benchmark `ok`/`stale`/`unavailable`, and
overall `ok` only when both are ok. Benchmark freshness is seven days. A
degraded/error cycle keeps the previous compatible data and last-good time;
unexpected errors advance the cycle timestamp but never fabricate an empty
inventory.

P2 reports `ok` when primary strategy and auxiliary positions both succeed,
`degraded` when only auxiliary positions fail, and `unknown` when primary
strategy fails. Primary success advances last-good even if auxiliary data is
unavailable. Primary unknown/error retains the previous compatible position
data and last-good time.

## Read-only dashboard server

Allowed API routes are exactly:

```text
GET /api/dashboard/health
GET /api/dashboard/opportunities
GET /api/dashboard/positions
```

Successful responses use `{ok:true,data,meta:{ts}}`. Missing, malformed, or
incompatible snapshots return a 503 error envelope rather than empty data.
Freshness uses the exact cycle interval:

- fresh: age `<= 2.5 * interval`;
- stale: age `> 2.5 * interval` and `<= 10 * interval`;
- offline: age `> 10 * interval` or absent.

The server accepts only localhost Host/Origin values, sets
`X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`,
decodes URL paths before traversal checks, and serves only resolved files
under the configured `site` root. POST/PUT/PATCH/DELETE return 405. Errors
never expose filesystem paths, credentials, or secrets.

## React dashboard

The separate `dashboard/` package uses React 18, TypeScript, Vite, TanStack
React Query, Vitest, Testing Library, CSS variables, and component CSS; it does
not use Tailwind. Vite builds with `base: "./"` into `site/dashboard`.

The five areas are Summary KPIs, Live Opportunities, Open Positions, Market
Radar summary, and System Health/Research links. Opportunity priority is the
copied classification order EXCEPTIONAL > HIGH YIELD > P99 > P95 > OTHER,
then $10k maker net APR and deterministic asset/direction. React performs no
numeric threshold comparisons. Position cards expose approved read-only
fields and contain no Trade, Close, Roll, or Execute controls.

Background refetch errors retain the last valid rows while showing degraded or
stale state. Static Radar loads the existing JSON once and links to the
existing APR, Radar, and Arbitrage Research pages.

## Preservation

P5 is additive. Historical P95/P99, High-Yield HY20/HY30, P1 delivery/rearm,
P2 lifecycle/delivery, Market Radar calculations, existing site pages, and the
official external CrossEx repository remain unchanged in meaning.
