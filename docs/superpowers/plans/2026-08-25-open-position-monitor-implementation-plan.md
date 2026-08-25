# Open Position Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable, read-only P2 monitor for CrossEx strategies with independent lifecycle, hedge, maturity, and disappearance notifications.

**Architecture:** Keep P2 in four focused modules: immutable API models, a dedicated SQLite lifecycle store, P2-only Telegram formatting, and a `PositionMonitor` orchestrator. Extend `CrossExClient` only with strict GET methods for `/api/strategy/:address` and `/api/positions`; add the `positions` CLI without sharing P1 state or changing P1 request semantics.

**Tech Stack:** Python 3.11+, standard-library `urllib`, immutable dataclasses, SQLite via `sqlite3`, `python-dotenv`, and pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-open-position-monitor-design.md`

## Global Constraints

- CrossEx access is read-only and limited to `GET /api/strategy/:address` and `GET /api/positions`.
- `strategyId` is the durable position identity; response ordering is never used for matching.
- Primary `/strategy` failure is UNKNOWN, never an empty inventory; successful `strategies=[]` is a real empty inventory.
- Production P2 state is `data/position_monitor.sqlite3`; dry-run uses `:memory:` and cannot touch P1 state.
- Only NEW, hedge transition, 14/7/3/1-day maturity, and three-successful-absence disappearance events are allowed.
- Notification state is consumed only after successful Telegram delivery.
- Preserve all P1 historical, High-Yield, capacity ladder, Radar, and `data/live_monitor.sqlite3` semantics.
- No new dependency is required; no credentials or raw warning arrays may be printed.

---

### Task 1: API models and read-only client methods

**Files:**
- Create: `boros_research/position_models.py`
- Modify: `boros_research/crossex_client.py`
- Modify: `tests/test_crossex_client.py`
- Test: `tests/test_position_models.py`

**Interfaces:**
- `CrossExClient.fetch_strategy(address: str) -> tuple[StrategySnapshot, ...]`
- `CrossExClient.fetch_positions() -> PositionsSnapshot`
- `normalize_strategy_response(value: Any) -> tuple[StrategySnapshot, ...]`
- `normalize_positions_response(value: Any) -> PositionsSnapshot`

- [ ] **Step 1: Write RED tests for address validation, envelopes, models, and routes.** Add fixtures covering a valid strategy, duplicate `strategyId`, malformed/non-finite critical fields, preserved leg fields, valid exposure groups, path injection, both GET URLs, and unchanged P1 opportunity query behavior.
- [ ] **Step 2: Run the focused tests.** Run `python3 -m pytest tests/test_position_models.py tests/test_crossex_client.py -q`; verify collection/assertion failures identify missing P2 model/client APIs.
- [ ] **Step 3: Implement immutable models and strict normalizers.** Preserve every approved strategy/leg/hedge/attribution field, reject invalid critical values and duplicate identities, and retain server exposure groups without relying on ordering.
- [ ] **Step 4: Add only the two GET client methods.** Validate `0x` plus 40 hex characters before URL construction, reuse token/request seams, call only the approved routes, and normalize `{ok,data,meta}` without adding query parameters.
- [ ] **Step 5: Run focused tests and preserve P1 tests.** Run `python3 -m pytest tests/test_position_models.py tests/test_crossex_client.py -q` and `python3 -m pytest tests/test_high_yield.py tests/test_monitor.py -q`.
- [ ] **Step 6: Commit.** `git add boros_research/position_models.py boros_research/crossex_client.py tests/test_position_models.py tests/test_crossex_client.py && git commit -m "feat: add read-only position API models"`.

### Task 2: Dedicated SQLite lifecycle store

**Files:**
- Create: `boros_research/position_state.py`
- Test: `tests/test_position_state.py`

**Interfaces:**
- `PositionStateStore(path: str | Path, poll_interval_seconds: int = 60)`
- `observe_success(strategies: Sequence[StrategySnapshot], timestamp: int) -> tuple[PositionEvent, ...]`
- `observe_unknown(timestamp: int) -> None`
- `pending_events(strategy_id: str) -> tuple[PositionEvent, ...]`
- `commit_event(event: PositionEvent, timestamp: int) -> None`
- `snapshot(strategy_id: str) -> PositionLifecycle | None`

- [ ] **Step 1: Write RED lifecycle tests.** Cover first NEW, failed NEW retry, initial unhedged NEW-only, true/false hedge transitions, persistent-false no spam, recovery, inclusive maturity thresholds and startup suppression, failed maturity retry, one/two/three absences, UNKNOWN reset, reappearance reset, failed disappearance retry, confirmed reappearance lifecycle, and preserved maturity milestones.
- [ ] **Step 2: Run `python3 -m pytest tests/test_position_state.py -q` and verify RED.** Failures must be due to missing store/event behavior rather than test setup.
- [ ] **Step 3: Implement a separate SQLite schema and migrations.** Store strategy identity, lifecycle, observation timestamps, absence evidence, observed/notified hedge state, per-event delivery flags, and last snapshot JSON. Add idempotent migrations; never use `data/live_monitor.sqlite3`.
- [ ] **Step 4: Implement observation and event commit semantics.** Successful primary observations update evidence; UNKNOWN changes only absence evidence; pending events are condition-aware; commits consume only the delivered event and preserve retryability.
- [ ] **Step 5: Run state tests, reopen tests, and compile the module.** `python3 -m pytest tests/test_position_state.py -q` and `python3 -m compileall -q boros_research/position_state.py`.
- [ ] **Step 6: Commit.** `git add boros_research/position_state.py tests/test_position_state.py && git commit -m "feat: persist position lifecycle state"`.

### Task 3: P2 Telegram and snapshot formatting

**Files:**
- Create: `boros_research/position_telegram.py`
- Test: `tests/test_position_telegram.py`

**Interfaces:**
- `format_position_event(event: PositionEvent, snapshot: StrategySnapshot | None) -> str`
- `render_position_snapshot(snapshot: StrategySnapshot) -> str`
- `render_position_delivery(event: PositionDeliveryEvent) -> str`

- [ ] **Step 1: Write RED formatting tests.** Assert NEW, hedge warning/recovery, each maturity threshold, disappearance, snapshot output, no roll/close recommendation, and no raw CrossEx warning block.
- [ ] **Step 2: Run `python3 -m pytest tests/test_position_telegram.py -q` and verify RED.**
- [ ] **Step 3: Implement separate P2 formatting.** Render approved fields, compact strategy ID, DTE, hedge ratios, attribution confidence, and unavailable values as `—`; do not import or reuse P1 opportunity message formatting.
- [ ] **Step 4: Run formatter tests and refactor only after GREEN.** `python3 -m pytest tests/test_position_telegram.py -q`.
- [ ] **Step 5: Commit.** `git add boros_research/position_telegram.py tests/test_position_telegram.py && git commit -m "feat: format position monitor alerts"`.

### Task 4: PositionMonitor orchestration

**Files:**
- Create: `boros_research/position_monitor.py`
- Test: `tests/test_position_monitor.py`

**Interfaces:**
- `PositionMonitor(client, state, telegram_send, address, poll_interval_seconds=60)`
- `PositionMonitor.run_once(dry_run=False) -> PositionCycleResult`
- `PositionMonitor.run_forever(dry_run=False) -> None`
- `render_position_cycle_summary(result: PositionCycleResult) -> str`

- [ ] **Step 1: Write RED orchestration tests.** Cover concurrent strategy/positions calls, positions failure not suppressing strategy, strategy failure as UNKNOWN, successful empty inventory advancing absence, independent event delivery, dry-run zero sends, and diagnostic/snapshot output.
- [ ] **Step 2: Run `python3 -m pytest tests/test_position_monitor.py -q` and verify RED.**
- [ ] **Step 3: Implement concurrent read-only fetches and lifecycle evaluation.** Process primary success through `observe_success`, primary failure through `observe_unknown`, keep auxiliary failure diagnostic-only, and make each Telegram event failure isolated.
- [ ] **Step 4: Implement send-success commits.** Render and send pending current-condition events; commit only successful events; skip stale hedge transitions when observed state has already recovered; keep daemon alive on ordinary failures.
- [ ] **Step 5: Run focused P2 integration tests plus P1 preservation tests.** `python3 -m pytest tests/test_position_monitor.py tests/test_position_state.py tests/test_position_telegram.py tests/test_high_yield.py tests/test_monitor.py -q`.
- [ ] **Step 6: Commit.** `git add boros_research/position_monitor.py tests/test_position_monitor.py && git commit -m "feat: monitor open strategy health"`.

### Task 5: `positions` CLI and environment resolution

**Files:**
- Modify: `boros_research/cli.py`
- Modify: `.env.example` if needed for `BOROS_ADDRESS`
- Test: `tests/test_position_cli.py`

**Interfaces:**
- `positions` parser options: `--address`, `--dry-run`, `--once`, `--base-url`, `--token-file`, `--state-path`, `--interval`.
- Address precedence: `args.address or os.environ.get("BOROS_ADDRESS")`; missing address raises a clear nonzero CLI error.

- [ ] **Step 1: Write RED CLI tests.** Cover `.env` address, `--address` override, no `.env` write, missing address, default state/interval, and dry-run construction.
- [ ] **Step 2: Run `python3 -m pytest tests/test_position_cli.py tests/test_env.py -q` and verify RED.**
- [ ] **Step 3: Add the command without changing monitor command behavior.** Reuse existing environment/token/Telegram helpers, choose `:memory:` only for dry-run, default `data/position_monitor.sqlite3`, and print cycle/delivery/snapshot diagnostics without secrets.
- [ ] **Step 4: Run CLI tests and P1 command tests.** `python3 -m pytest tests/test_position_cli.py tests/test_env.py tests/test_monitor.py tests/test_high_yield.py -q`.
- [ ] **Step 5: Commit.** `git add boros_research/cli.py tests/test_position_cli.py .env.example && git commit -m "feat: add positions monitoring CLI"` (omit `.env.example` when unchanged).

### Task 6: Lifecycle and integration regression coverage

**Files:**
- Modify: `tests/test_position_models.py`
- Modify: `tests/test_position_state.py`
- Modify: `tests/test_position_telegram.py`
- Modify: `tests/test_position_monitor.py`
- Modify: `tests/test_crossex_client.py`

- [ ] **Step 1: Add any uncovered RED regressions from the approved checklist.** Explicitly cover no mutation endpoint names/calls, exact route/method assertions, P1 state-path isolation, and production P2 state reopening.
- [ ] **Step 2: Run the smallest failing tests and record the expected failures.** Use `python3 -m pytest <focused node> -q` before each fix.
- [ ] **Step 3: Apply minimal fixes only in P2 files, preserving P1 behavior.** Do not weaken existing assertions.
- [ ] **Step 4: Run all focused P2 and P1 tests.** `python3 -m pytest tests/test_position_models.py tests/test_position_state.py tests/test_position_telegram.py tests/test_position_monitor.py tests/test_position_cli.py tests/test_crossex_client.py tests/test_high_yield.py tests/test_alert_state.py tests/test_monitor.py tests/test_radar.py tests/test_radar_economics.py -q`.
- [ ] **Step 5: Commit.** `git add tests boros_research && git commit -m "test: cover position monitor lifecycle"`.

### Task 7: Read-only runtime smoke, full regression, and exact-HEAD CI

**Files:**
- Modify only if a verification failure requires a TDD fix.

- [ ] **Step 1: Run module and modified-file verification.** Run `python3 -m compileall -q boros_research`; run Ruff on every modified/new Python file; run focused mypy using repository configuration; run `git diff --check`.
- [ ] **Step 2: Run full regression.** Run `python3 -m pytest -q`; require a count greater than the pre-P2 baseline of 239 and preserve all P1 tests.
- [ ] **Step 3: Run read-only runtime smoke when configured.** If local CrossEx and `BOROS_ADDRESS` exist, run `python3 -m boros_research.cli positions --dry-run --once`; verify both GET routes, zero Telegram sends, snapshots when present, unchanged `data/position_monitor.sqlite3` and `data/live_monitor.sqlite3`, and no mutation route.
- [ ] **Step 4: Review and commit any verification fix with TDD.** Re-run focused RED/GREEN, full regression, and diff checks.
- [ ] **Step 5: Push and verify exact CI.** Run `git status --short`, `git diff --check`, `git push origin feature/boros-arbitrage-research`; inspect GitHub Actions until the final run’s `head_sha` equals local HEAD and conclusion is success.
