# Unified Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a localhost read-only unified dashboard whose Python snapshots are the only source of financial and lifecycle truth, while preserving static GitHub Pages research.

**Architecture:** Extend P1/P2 cycle results with structured, already-computed views and an exception-isolated observer seam. A finite, atomically written snapshot contract feeds a stdlib localhost server; a separate React/Vite package presents local live data or static Pages-safe research data without threshold calculations or mutation controls.

**Tech Stack:** Python 3.11+ standard library plus existing package, `sqlite3`/JSON snapshots, React 18, TypeScript, Vite, TanStack React Query, Vitest, Testing Library, npm, and CSS variables/component CSS.

**Spec:** `docs/superpowers/specs/2026-08-25-unified-dashboard-design.md`

## Global Constraints

- Python = truth; React = presentation; snapshots = contract; dashboard server = read-only bridge.
- Only `GET /api/dashboard/health`, `GET /api/dashboard/opportunities`, and `GET /api/dashboard/positions` are dashboard API routes.
- Local mode is enabled only for hostname `localhost` or `127.0.0.1`; all other hosts are static mode.
- No POST/PUT/PATCH/DELETE application feature, trading, close, roll, order, account, wallet, credential, or CrossEx mutation endpoint.
- P1/P2 state machines, historical/high-yield semantics, Market Radar calculations, and existing research pages are preserved.
- Dry-run must not publish production dashboard snapshots or mutate either production SQLite database.
- Python JSON must be finite, schema-versioned, and atomically published; malformed snapshots fail closed.
- Vite uses `base: "./"`, builds to `site/dashboard`, and does not proxy CrossEx or localhost APIs.

---

### Task 1: Structured monitor views and observer seams

**Files:**
- Modify: `boros_research/monitor.py`
- Modify: `boros_research/position_monitor.py`
- Test: `tests/test_monitor.py`
- Test: `tests/test_position_monitor.py`

**Interfaces:**
- `LiveOpportunityView(identity, percentile_90d, historical_band, high_yield_band, sizes)`
- `MonitorCycleResult.current_opportunities: tuple[LiveOpportunityView, ...] = ()`
- `PositionCycleResult.strategy_snapshots: tuple[StrategySnapshot, ...] = ()`
- `PositionCycleResult.positions_snapshot: PositionsSnapshot | None = None`
- Optional observer `Callable[[Result | None, str | None], None]`

- [ ] **Step 1: Write RED tests for copied classifications and complete current identities.** Build fake P1 records whose decisions are P95/P99/HIGH_YIELD/EXCEPTIONAL and assert the view copies bands, includes non-alerting identities, retains all five size rows, and marks only `$10,000` as `signalSize` at serialization time.
- [ ] **Step 2: Run `python3 -m pytest tests/test_monitor.py tests/test_position_monitor.py -q` and verify the new tests fail because the additive fields and observer are absent.**
- [ ] **Step 3: Refactor each `run_once` into `_run_once_impl` plus a wrapper.** Invoke the optional observer after success or exception; swallow observer exceptions and preserve the original result/exception and delivery/state behavior.
- [ ] **Step 4: Populate P1 views from `_IdentityEvaluation` booleans only.** Do not add threshold arithmetic; use P99/P95 and EXCEPTIONAL/HIGH_YIELD priority exactly as specified. Populate P2 structured source fields from the values already returned by the two client calls.
- [ ] **Step 5: Run the focused tests and all existing P1/P2 tests.** `python3 -m pytest tests/test_monitor.py tests/test_position_monitor.py tests/test_high_yield.py tests/test_alert_state.py tests/test_position_state.py -q`.
- [ ] **Step 6: Commit.** `git add boros_research/monitor.py boros_research/position_monitor.py tests/test_monitor.py tests/test_position_monitor.py && git commit -m "feat: expose dashboard monitor views"`.

### Task 2: Snapshot contract and atomic publishers

**Files:**
- Create: `boros_research/dashboard_snapshot.py`
- Create: `tests/test_dashboard_snapshot.py`

**Interfaces:**
- `SCHEMA_VERSION = 1`
- `P1_DASHBOARD_SNAPSHOT = Path("data/dashboard/p1_latest.json")`
- `P2_DASHBOARD_SNAPSHOT = Path("data/dashboard/p2_latest.json")`
- `write_snapshot(path, snapshot) -> None`
- `read_snapshot(path, expected_kind) -> dict | None`
- `build_p1_snapshot(result, previous, cycle_timestamp, poll_interval_seconds) -> dict`
- `build_p2_snapshot(result, previous, cycle_timestamp, poll_interval_seconds) -> dict`

- [ ] **Step 1: Write RED tests for schema, finite JSON, recursive non-finite rejection, P1/P2 source statuses, last-good rules, retained data on error, and exact P1/P2 payload fields.** Include P1 degraded benchmark/CrossEx cases, P2 auxiliary-only failure, primary unknown, and unexpected error retention.
- [ ] **Step 2: Run `python3 -m pytest tests/test_dashboard_snapshot.py -q` and verify RED.**
- [ ] **Step 3: Implement finite validation and atomic same-directory writes.** Serialize with `allow_nan=False`, flush/fsync, replace atomically, remove only temporary files on failure, and keep the prior valid file intact. The reader must reject incompatible schema/kind and recursively non-finite values.
- [ ] **Step 4: Implement snapshot builders as pure serializers.** Copy bands from `LiveOpportunityView`; never compare thresholds. Preserve unavailable sizes and prior compatible data without manufacturing empty inventories.
- [ ] **Step 5: Run snapshot tests plus `python3 -m compileall -q boros_research`.**
- [ ] **Step 6: Commit.** `git add boros_research/dashboard_snapshot.py tests/test_dashboard_snapshot.py && git commit -m "feat: publish dashboard snapshots"`.

### Task 3: CLI observer wiring and dry-run isolation

**Files:**
- Modify: `boros_research/cli.py`
- Modify: `.gitignore`
- Test: `tests/test_position_cli.py`
- Test: `tests/test_env.py`
- Test: `tests/test_dashboard_snapshot.py`

**Interfaces:**
- `_p1_dashboard_observer(snapshot_dir, interval) -> Callable`
- `_p2_dashboard_observer(snapshot_dir, interval) -> Callable`
- `dashboard` parser: `--port`, `--site-dir`, `--snapshot-dir`

- [ ] **Step 1: Write RED tests for monitor/positions observer paths, interval, previous snapshot retention, and dry-run sentinel files/SQLite files remaining byte-identical.**
- [ ] **Step 2: Run the focused CLI tests and verify RED.**
- [ ] **Step 3: Wire observers only in non-dry-run commands.** Use `int(time.time())`, command interval, and separate P1/P2 snapshot files; catch publisher failures through the monitor seam without affecting Telegram/lifecycle behavior.
- [ ] **Step 4: Add dashboard runtime data/temp/build ignores without ignoring tracked site data.**
- [ ] **Step 5: Run CLI/environment/P1/P2 tests and verify dry-run sentinels.**
- [ ] **Step 6: Commit.** `git add boros_research/cli.py .gitignore tests/test_position_cli.py tests/test_env.py tests/test_dashboard_snapshot.py && git commit -m "feat: wire live dashboard snapshots"`.

### Task 4: Read-only dashboard server

**Files:**
- Create: `boros_research/dashboard_server.py`
- Create: `tests/test_dashboard_server.py`
- Modify: `boros_research/cli.py`

**Interfaces:**
- `DASHBOARD_HOST = "127.0.0.1"`
- `DEFAULT_DASHBOARD_PORT = 8765`
- `DashboardHTTPServer(site_dir, snapshot_dir, port=8765)`
- `freshness(age_seconds, interval_seconds) -> "fresh" | "stale" | "offline"`
- `python3 -m boros_research.cli dashboard`

- [ ] **Step 1: Write RED server tests for exact freshness boundaries, success/error envelopes, missing/malformed snapshots as 503, health component states, static redirects/files, path traversal, Host/Origin policy, security headers, and 405 mutation methods.**
- [ ] **Step 2: Run `python3 -m pytest tests/test_dashboard_server.py -q` and verify RED.**
- [ ] **Step 3: Implement a stdlib `ThreadingHTTPServer` bound to `127.0.0.1`.** Route only the three approved GET APIs and static files; decode paths, resolve under site root, reject traversal, avoid filesystem paths/secrets in errors, and set DENY/CSP headers.
- [ ] **Step 4: Add CLI dashboard command with port/site/snapshot options and no host option.**
- [ ] **Step 5: Run server tests and existing CLI tests.**
- [ ] **Step 6: Commit.** `git add boros_research/dashboard_server.py boros_research/cli.py tests/test_dashboard_server.py && git commit -m "feat: serve read-only dashboard api"`.

### Task 5: React/Vite dashboard package and frontend contract tests

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/package-lock.json`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/vite.config.ts`
- Create: `dashboard/index.html`
- Create: `dashboard/src/*`
- Create: `dashboard/src/**/*.test.tsx`

**Interfaces:**
- `isLocalMode(hostname: string) -> boolean`
- `dashboardFetch<T>(path) -> Promise<ApiEnvelope<T>>`
- React Query hooks with `refetchInterval: 10_000` for health/opportunities/positions
- `priorityRank(classification)` with no numeric threshold literals

- [ ] **Step 1: Create the minimal npm package/config and write RED Vitest tests.** Cover hostname mode, disabled live fetch, API envelope errors, polling interval, priority order, no threshold arithmetic in sort logic, `$10k` signal labeling, capacity context, position fields, no mutation controls, freshness states, refetch retention, and static Radar loading.
- [ ] **Step 2: Run `npm test --prefix dashboard` and verify RED for missing source modules.**
- [ ] **Step 3: Implement typed API models, mode detection, hooks, deterministic opportunity sorting, and static Radar loader.** Keep API data unchanged and preserve prior rows on background errors.
- [ ] **Step 4: Run `npm run typecheck --prefix dashboard` and `npm test --prefix dashboard`; fix only implementation failures.
- [ ] **Step 5: Commit the package foundation and contract implementation.** `git add dashboard && git commit -m "feat: scaffold unified dashboard frontend"`.

### Task 6: Unified five-area UI and existing-site links

**Files:**
- Modify: `dashboard/src/*`
- Create: `dashboard/src/styles.css` and component CSS as needed
- Create: `dashboard/src/**/*.test.tsx`
- Modify: `site/index.html`
- Modify: `site/radar.html`
- Modify: `site/arbitrage.html`
- Modify: `README.md`

- [ ] **Step 1: Write RED component tests for Summary, Live Opportunities, Open Positions compact/expanded, Radar summary, System Health, static-mode copy, and absence of Trade/Close/Roll/Execute controls.**
- [ ] **Step 2: Run the component tests and verify RED.**
- [ ] **Step 3: Implement the responsive dashboard using CSS variables/component CSS.** Show copied classifications, P1 $10k signal vs larger capacity context, P2 approved fields, freshness/cycle/last-good distinctions, static Radar, and links to existing pages.
- [ ] **Step 4: Build and run frontend tests.** `npm run typecheck --prefix dashboard && npm test --prefix dashboard && npm run build --prefix dashboard`.
- [ ] **Step 5: Add only minimal Dashboard navigation links to existing pages and document local/static usage in README.**
- [ ] **Step 6: Commit.** `git add dashboard site/index.html site/radar.html site/arbitrage.html README.md && git commit -m "feat: build unified control center"`.

### Task 7: CI, Pages build, runtime smoke, and exact-HEAD verification

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `.github/workflows/deploy-pages.yml`
- Modify only if a verification regression requires a TDD fix.

- [ ] **Step 1: Extend Test workflow with Node 20, `npm ci`, frontend typecheck/test/build after Python tests and site-data build.**
- [ ] **Step 2: Extend Pages workflow with Node 20 frontend build, `touch site/.nojekyll`, and upload of the complete `site/` tree.**
- [ ] **Step 3: Run local Python/frontend verification, compileall, modified-file Ruff, focused mypy, and `git diff --check`.**
- [ ] **Step 4: Run read-only smoke in temporary directories: dashboard health/opportunities/positions, static dashboard load, Radar JSON, evil Host/Origin rejection, mutation 405, and dry-run snapshot/SQLite invariants.
- [ ] **Step 5: Run `python3 -m pytest -q`; require more than 283 passed and all P1/P2/Radar tests green.**
- [ ] **Step 6: Review `git status --short`, `git diff --check`, and `git diff 772f7ff6a65d2e577c82a6f93cbf40e4e99de227...HEAD`; commit workflow/docs fixes if needed.**
- [ ] **Step 7: Push `origin feature/boros-arbitrage-research` and verify the GitHub Actions run has the exact final `head_sha` and conclusion `success`.**
