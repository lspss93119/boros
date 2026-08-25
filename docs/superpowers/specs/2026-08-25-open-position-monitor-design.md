# Boros Open Position Monitor Design

Date: 2026-08-25

## Purpose and boundary

P2 adds a separate, read-only monitor for already-open Boros strategies. It
answers whether a known strategy is new, remains hedged, is approaching
maturity, or has disappeared from the CrossEx strategy inventory. It does not
recommend roll/close actions, execute trades, or change the existing P1
opportunity monitor.

The primary source of truth is `GET /api/strategy/:address`. The auxiliary
source is `GET /api/positions`; auxiliary failure is diagnostic only and must
not suppress a valid primary lifecycle cycle. The only CrossEx routes P2 may
call are these two GET routes.

## Configuration and runtime

- `BOROS_ADDRESS` supplies the normal EVM address.
- CLI `--address` takes precedence and never edits `.env`.
- The command is `python3 -m boros_research.cli positions`.
- `--once` performs one cycle; the default interval is 60 seconds.
- Production state is `data/position_monitor.sqlite3`.
- `--dry-run` uses `:memory:` state and no Telegram sender, so it cannot mutate
  either P2 production state or P1 `data/live_monitor.sqlite3`.

The address is validated as exactly `0x` followed by 40 hexadecimal
characters before being inserted into a URL path. CrossEx token resolution,
base URL resolution, request seam, and `x-arb-token` behavior are reused from
the existing client. No secret is printed.

## Normalized data model

`strategyId` is the durable identity. Immutable models preserve the server
snapshot needed for current monitoring and later research: base, maturity,
legs, hedge, `hedgeChecks` (`borosMatchRatio`, `perpMatchRatio`,
`borosVsPerpRatio`, `fullyHedged`), capital and split, realized PnL/APR,
spread, locked APR on capital, expected PnL to maturity, seconds to maturity,
notional mismatch, attribution (`source`, `confidence`, `pinned`,
`unclaimed`), and warnings. Leg models retain kind, venue, base, side,
notional, collateral/notional token/market ID, APR fields, prices, cash-flow,
MTM, trade PnL, fees, net, timestamps, maturity, symbol, share, and warnings.
`/positions` preserves server-computed exposure groups at minimum.

The `{ok,data,meta}` envelope and critical fields are validated strictly.
Malformed responses, non-finite critical numbers, duplicate `strategyId`, and
invalid identities fail closed. Response ordering is never meaningful.

## Lifecycle state and observation rules

P2 uses a dedicated SQLite database and `PositionStateStore`, keyed only by
`strategyId`. It stores lifecycle, first/last seen times, active state,
successful absence count, observed and notified hedge state, delivery flags for
NEW/disappearance/14d/7d/3d/1d, lifecycle number, and the last known snapshot
JSON. Observed hedge state is deliberately separate from notified hedge state
so a failed delivery cannot consume a transition.

On a successful primary cycle, present strategies update last seen/snapshot and
reset absence evidence. Missing active strategies increment consecutive
successful absence evidence; exactly the third absence creates a disappearance
event. A confirmed disappearance becomes inactive only after its event is
delivered. Reappearance before the third absence resets the counter. A
confirmed disappeared `strategyId` reappears as a new active lifecycle and
creates NEW again; already-delivered maturity milestones remain delivered.

A primary failure, malformed response, or invalid response is `UNKNOWN`: it
resets/breaks absence evidence, leaves active/last-seen/snapshot unchanged,
does not infer hedge transitions, and does not advance maturity. A successful
empty strategy list is real inventory and may advance absence evidence.

## Alert families and delivery

Only these event kinds exist:

1. `NEW_STRATEGY`
2. `HEDGE_WARNING` / `HEDGE_RECOVERED`
3. `MATURITY_14D`, `MATURITY_7D`, `MATURITY_3D`, `MATURITY_1D`
4. `STRATEGY_DISAPPEARED`

NEW is emitted on the first successful observation of each lifecycle. If its
initial `fullyHedged` is false, NEW includes that status and no separate hedge
warning is emitted in that cycle; after NEW delivery, false is the notified
baseline. Thereafter only CrossEx `hedgeChecks.fullyHedged` drives hedge
transitions: `true -> false` warns and `false -> true` recovers. No custom
ratio or dollar mismatch threshold is introduced.

Maturity thresholds use actual `secondsToMaturity`, are future-only, and are
inclusive at 14/7/3/1 days. At each cycle the smallest not-yet-delivered
threshold that is satisfied is selected. Delivering a threshold also marks
all larger thresholds delivered. Thus first seeing a strategy at 6.5 days
only emits 7d, and first seeing it at 0.8 days only emits 1d; expired
strategies emit no reminder.

Observation updates never consume notification state. Telegram event delivery
commits only after a successful send. A failed event remains retryable while
its condition remains. If a failed hedge warning is followed by recovery, the
stale warning is discarded. Each event is isolated so one delivery failure
does not stop the daemon.

## Telegram presentation

P2 formatting is separate from P1 opportunity formatting and never dumps raw
CrossEx warning arrays. NEW shows base/direction when derivable, maturity/DTE,
locked spread, locked APR on capital, capital, expected PnL to maturity,
hedge, and attribution confidence. Hedge events show base/maturity, old to new
hedge state, the three server ratios, and notional mismatch. Maturity events
show the threshold, base/maturity/current DTE, locked spread/APR, expected PnL,
and hedge. Disappearance shows base/maturity, compact strategy ID, last seen,
and last known hedge. No roll or close recommendation appears.

## Orchestration and preservation

`PositionMonitor` fetches strategy and positions concurrently. A valid
strategy cycle runs the lifecycle even when positions is unavailable. A
strategy failure calls `observe_unknown` and produces no lifecycle event.
`run_once` and `run_forever` expose cycle summaries, delivery diagnostics, and
snapshot rendering while surviving ordinary API failures.

P2 never changes P1 historical P95/P99 or High-Yield HY20/HY30 behavior,
Market Radar, historical data, or `data/live_monitor.sqlite3`. The official
CrossEx repository remains external and untouched.
