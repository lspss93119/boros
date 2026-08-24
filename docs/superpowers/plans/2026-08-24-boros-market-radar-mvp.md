# Boros Market Radar MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small historical Market Radar that immediately shows, for each asset and notional, the normal-best venue direction, burst-best venue direction, common high/low fixed-rate venues, and economically derived DTE floor, then deep-links into the existing Historical Arbitrage Explorer.

**Architecture:** Keep the existing Historical Arbitrage Explorer intact as the drill-down tool. Add a separate read-only Radar aggregation path backed by the existing DuckDB and a small current CrossEx cost/capital proxy, emit a compact `site/data/boros_market_radar.json`, and render it in a new static `site/radar.html`. Historical rows are never mutated; DTE filtering applies only to Radar aggregation.

**Tech Stack:** Python 3.11+, DuckDB, existing `boros_research.crossex_client.CrossExClient`, pytest, static HTML/CSS/vanilla JavaScript, Node syntax/DOM regression checks.

**Spec:** `docs/superpowers/specs/2026-08-24-boros-market-radar-mvp-design.md`

## Global Constraints

- Repository: `lspss93119/boros`
- Branch: `feature/boros-arbitrage-research`
- Starting HEAD when this plan was written: `1010aff3103815733d33050cf6e34b8e3e34ff4f`
- Never modify, patch, fork, or write into `Arbitrage with CrossEx`.
- CrossEx access is GET-only through the existing local `/api/opportunities` adapter; no wallet, account, order, or agent mutation.
- Phase 1, Phase 2, and Phase 3 semantics must remain unchanged.
- Do not delete or rewrite short-DTE historical observations in DuckDB.
- DTE exclusion is Radar-only.
- Eligible extreme spreads are retained; no statistical outlier trimming.
- Radar primary window is 90 days anchored to the historical dataset maximum timestamp, not wall-clock time.
- Main Radar notionals are exactly `$10k`, `$25k`, `$50k`; default UI notional is `$10k`.
- Minimum viability targets are exactly: estimated net profit `>= $50` AND estimated holding-period return on model capital `>= 1%`.
- Current CrossEx costs/capital are a proxy applied to historical spreads and must be labelled as a proxy, never as historical realized cost/P&L.
- Normal-best direction ranks by 90D median executable spread.
- Burst-best direction ranks by 90D P95 executable spread.
- Spread statistics use fully executable observations only.
- Minimum 90D direction sample count: `50`.
- High/low venue comparison groups require at least two valid distinct venues and never treat missing data as zero.
- Default UI language is Traditional Chinese; `?lang=en` explicitly opts into English.
- Existing `site/data/boros_arbitrage_site_data.json` must not be enlarged with Radar payload fields.
- Generated Radar payload target should remain comfortably small; enforce `< 1 MiB`.
- No composite opportunity score in MVP.

---

## File Structure

### Create

- `docs/superpowers/specs/2026-08-24-boros-market-radar-mvp-design.md` — approved design spec copied verbatim from the approved artifact.
- `docs/superpowers/plans/2026-08-24-boros-market-radar-mvp.md` — this implementation plan.
- `boros_research/radar_economics.py` — pure current-model proxy validation and DTE viability math.
- `boros_research/radar.py` — Radar-only DuckDB queries and aggregation.
- `build_market_radar_data.py` — production read-only builder that fetches CrossEx proxy data and writes compact Radar JSON.
- `site/radar.html` — standalone Radar page.
- `site/radar.js` — Radar rendering, language switching, notional switching, Explorer deep-links.
- `site/radar.css` — Radar-scoped layout only.
- `site/data/boros_market_radar.json` — generated compact production payload.
- `tests/test_radar_economics.py` — pure viability/proxy tests.
- `tests/test_radar.py` — DuckDB aggregation semantics.
- `tests/test_build_market_radar_data.py` — builder/read-only/proxy/output tests.
- `tests/test_radar_site.py` — static UI/DOM/deep-link/language tests.

### Modify

- `site/arbitrage.js` — initialize Explorer filters from validated URL query parameters.
- `site/arbitrage.html` — add Market Radar navigation link only.
- `site/index.html` — add Market Radar navigation link only.
- `tests/test_arbitrage_site.py` — Explorer deep-link regression only.
- `README.md` — add concise Radar build/open instructions only after implementation is verified.

### Must remain analytically unchanged

- `build_arbitrage_site_data.py`
- `site/data/boros_arbitrage_site_data.json`
- `boros_research/benchmark.py`
- `boros_research/live_benchmark.py`
- `boros_research/monitor.py`
- `boros_research/alert_state.py`
- `boros_research/execution.py`
- `boros_research/crossex_client.py` unless a test exposes a strictly read-only parsing bug; do not extend its responsibilities for Radar.
- all trading/order/account code outside this repository.

---

### Task 1: Commit the approved design/plan and lock the baseline

**Files:**
- Create: `docs/superpowers/specs/2026-08-24-boros-market-radar-mvp-design.md`
- Create: `docs/superpowers/plans/2026-08-24-boros-market-radar-mvp.md`

**Interfaces:**
- Consumes: approved spec artifact and this plan.
- Produces: repo-local immutable design references used by every later task.

- [ ] **Step 1: Verify branch, HEAD, and clean worktree**

Run:

```bash
git branch --show-current
git rev-parse HEAD
git status --short
```

Expected:

```text
feature/boros-arbitrage-research
1010aff3103815733d33050cf6e34b8e3e34ff4f
```

`git status --short` should be empty. If HEAD has advanced only because these approved docs were already committed, continue after verifying the diff. If unrelated code has changed, stop and report the divergence before implementing.

- [ ] **Step 2: Save the approved design spec verbatim**

Write the approved Market Radar MVP design to:

```text
docs/superpowers/specs/2026-08-24-boros-market-radar-mvp-design.md
```

Do not merge it into the older broad Boros research design.

- [ ] **Step 3: Save this implementation plan verbatim**

Write this plan to:

```text
docs/superpowers/plans/2026-08-24-boros-market-radar-mvp.md
```

- [ ] **Step 4: Run the existing baseline**

Run:

```bash
python3 -m pytest -q
git diff --check
node --check site/app.js
node --check site/arbitrage.js
```

Expected: current suite passes (179 tests at plan creation), diff check passes, both JS syntax checks pass.

- [ ] **Step 5: Commit docs only**

```bash
git add docs/superpowers/specs/2026-08-24-boros-market-radar-mvp-design.md \
        docs/superpowers/plans/2026-08-24-boros-market-radar-mvp.md
git commit -m "docs: specify market radar mvp"
```

---

### Task 2: Implement pure proxy economics and DTE viability math

**Files:**
- Create: `boros_research/radar_economics.py`
- Create: `tests/test_radar_economics.py`

**Interfaces:**
- Consumes: `CrossExResponse`, `CrossExPair` from `boros_research.crossex_client`.
- Produces:
  - `ProxyEconomics`
  - `ViabilityCutoff`
  - `collect_asset_proxy(response: CrossExResponse, asset: str) -> ProxyEconomics | None`
  - `required_dte_days(...) -> int | None`
  - `derive_viability_cutoff(...) -> ViabilityCutoff | None`

Use these exact public shapes:

```python
@dataclass(frozen=True)
class ProxyEconomics:
    cost_usd: float
    capital_usd: float
    as_of_timestamp: int
    pair_count: int

@dataclass(frozen=True)
class ViabilityCutoff:
    cutoff_days: int
    reference_p95_spread_apr: float
    gross_profit_at_cutoff_usd: float
    estimated_net_profit_at_cutoff_usd: float
    holding_return_at_cutoff: float
    proxy: ProxyEconomics
```

- [ ] **Step 1: Write failing tests for conservative CrossEx proxy collection**

Create tests that build an in-memory `CrossExResponse` with multiple valid/invalid pairs and assert:

```python
def test_collect_asset_proxy_uses_conservative_valid_envelope():
    proxy = collect_asset_proxy(response, "HYPE")
    assert proxy.cost_usd == 24.0       # max valid totalUsd
    assert proxy.capital_usd == 1800.0  # max valid capitalUsd
    assert proxy.pair_count == 2
```

Also assert these pairs are excluded:

```python
pair.reasons != ()
costs.total_usd is None
costs.total_usd < 0
capital_usd is None
capital_usd <= 0
```

If no valid pair remains, expect `None`.

- [ ] **Step 2: Run the focused tests and confirm RED**

```bash
python3 -m pytest tests/test_radar_economics.py -q
```

Expected: FAIL because `boros_research.radar_economics` does not exist.

- [ ] **Step 3: Implement `collect_asset_proxy`**

Implementation rules:

```python
valid pair =
    pair.base.upper() == asset.upper()
    and not pair.reasons
    and pair.costs.total_usd is finite and >= 0
    and pair.capital_usd is finite and > 0
```

Return:

```python
ProxyEconomics(
    cost_usd=max(valid total costs),
    capital_usd=max(valid capital values),
    as_of_timestamp=response.as_of_timestamp,
    pair_count=len(valid_pairs),
)
```

The max cost and max capital may come from different valid pairs intentionally; this is a conservative asset/notional envelope.

- [ ] **Step 4: Write failing tests for the exact DTE formula**

Use:

```python
gross_profit = notional_usd * spread_apr * dte_days / 365
estimated_net_profit = gross_profit - proxy.cost_usd
holding_return = estimated_net_profit / proxy.capital_usd
```

Required days:

```python
profit_days = 365 * (proxy.cost_usd + min_net_profit_usd) / (notional_usd * spread_apr)
return_days = 365 * (proxy.cost_usd + min_holding_return * proxy.capital_usd) / (notional_usd * spread_apr)
cutoff = ceil(max(profit_days, return_days))
```

Test with explicit numbers:

```python
proxy = ProxyEconomics(cost_usd=20, capital_usd=2000, as_of_timestamp=1, pair_count=1)
assert required_dte_days(
    notional_usd=10_000,
    spread_apr=0.08,
    proxy=proxy,
    min_net_profit_usd=50,
    min_holding_return=0.01,
) == 32
```

Reason: the $50 profit condition requires `ceil(365*70/800) = 32`.

Also test:

```python
spread_apr <= 0 -> None
notional_usd <= 0 -> ValueError
non-finite inputs -> ValueError
capital <= 0 -> ValueError
```

- [ ] **Step 5: Implement `required_dte_days` and `derive_viability_cutoff`**

`derive_viability_cutoff` must accept a precomputed positive `reference_p95_spread_apr`. It must not compute percentiles itself.

```python
def derive_viability_cutoff(
    *,
    notional_usd: float,
    reference_p95_spread_apr: float,
    proxy: ProxyEconomics,
    min_net_profit_usd: float = 50.0,
    min_holding_return: float = 0.01,
) -> ViabilityCutoff | None:
```

After finding `cutoff_days`, calculate the three economics fields exactly at that integer cutoff.

- [ ] **Step 6: Verify GREEN**

```bash
python3 -m pytest tests/test_radar_economics.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add boros_research/radar_economics.py tests/test_radar_economics.py
git commit -m "feat: add radar viability economics"
```

---

### Task 3: Build Radar aggregation on read-only DuckDB

**Files:**
- Create: `boros_research/radar.py`
- Create: `tests/test_radar.py`

**Interfaces:**
- Consumes:
  - `ProxyEconomics`, `ViabilityCutoff`, `derive_viability_cutoff`
  - DuckDB database containing existing `markets` and `executable_opportunities`
- Produces:
  - `build_radar_payload(connection, proxies_by_notional, *, generated_at=None) -> dict[str, Any]`
  - no database mutations outside temporary views/tables in the read-only session.

Define constants:

```python
RADAR_NOTIONALS = (10_000, 25_000, 50_000)
RADAR_WINDOW_DAYS = 90
MIN_DIRECTION_SAMPLES = 50
MIN_VENUE_COMPARISONS = 50
MIN_NET_PROFIT_USD = 50.0
MIN_HOLDING_RETURN = 0.01
PROXY_MODEL = "current-crossex-conservative-envelope-v1"
```

`proxies_by_notional` type:

```python
dict[int, dict[str, ProxyEconomics]]
```

- [ ] **Step 1: Write a minimal DuckDB fixture builder in the test**

Create temporary tables containing only fields Radar needs:

```sql
CREATE TABLE executable_opportunities (
    timestamp BIGINT,
    asset VARCHAR,
    maturity DATE,
    dte_days INTEGER,
    token_id BIGINT,
    short_market_id BIGINT,
    short_venue VARCHAR,
    long_market_id BIGINT,
    long_venue VARCHAR,
    notional_usd DOUBLE,
    short_bid_vwap_apr DOUBLE,
    long_ask_vwap_apr DOUBLE,
    executable_spread_apr DOUBLE,
    fully_executable BOOLEAN,
    invalid_reason VARCHAR
);
```

Create `markets` identity rows matching the existing production identity requirements.

- [ ] **Step 2: Write RED tests for the reference spread and per-notional DTE cutoff**

The DTE reference distribution must be calculated before any DTE filter:

1. Anchor time = `MAX(timestamp)` among `$10k/$25k/$50k`.
2. Keep only last 90 days, fully executable, non-null positive spread.
3. For each:

```text
asset + token + maturity + timestamp + notional
```

take the maximum executable spread across directions.
4. For each:

```text
asset + notional
```

calculate DuckDB `quantile_cont(best_spread, 0.95)`.

Feed that P95 into `derive_viability_cutoff`.

Test that the same asset can produce different cutoffs for `$10k` and `$50k`.

- [ ] **Step 3: Implement production-safe identity views**

Follow the existing `build_arbitrage_site_data.py` identity rules:

```sql
market_identity
valid_opportunities
```

Do not change the original builder. Recreate equivalent TEMP VIEW semantics inside `radar.py`.

- [ ] **Step 4: Implement reference P95 and cutoff construction**

Create a temporary cutoff relation with exact fields:

```text
asset
notional_usd
cutoff_days
reference_p95_spread_apr
proxy_cost_usd
proxy_capital_usd
proxy_as_of_timestamp
proxy_pair_count
```

Assets without a valid current proxy remain known to the payload but receive:

```json
{
  "available": false,
  "unavailableReason": "proxy-unavailable"
}
```

Do not invent a gross-only substitute for the approved `$50 + 1%` viability rule.

- [ ] **Step 5: Write RED tests proving short DTE is Radar-only and spikes survive**

Fixture:

```text
eligible DTE rows: 30d spreads 0.02, 0.021, ... plus one 0.15 spike
ineligible DTE row: 2d spread 0.50
```

Assertions:

- the 2-day row is excluded from Radar direction statistics once cutoff > 2;
- the 15% eligible spike remains in the source sample and raises P95/max as mathematically expected;
- the underlying DuckDB row count is unchanged before/after `build_radar_payload`.

- [ ] **Step 6: Implement direction statistics**

For every eligible asset/notional/directed venue pair in the 90D window:

```sql
COUNT(*) AS sample_count,
MEDIAN(executable_spread_apr) AS median_spread_apr,
QUANTILE_CONT(executable_spread_apr, 0.95) AS p95_spread_apr
```

Filters:

```text
fully_executable = true
executable_spread_apr is finite/non-null
dte_days >= asset/notional cutoff
timestamp >= anchor - 90d
both venues are in current CrossEx-compatible venue universe
```

Require `sample_count >= 50`.

Ranking:

```text
normal best: median_spread_apr DESC, then short_venue, long_venue
burst best:  p95_spread_apr DESC, then short_venue, long_venue
```

Lexical tie-break is only for deterministic payload generation after equal numeric metrics; expose the equal metric, do not create a composite score.

- [ ] **Step 7: Add deterministic drill-down maturity selection**

For each selected normal/burst direction choose:

1. nearest maturity on or after the historical anchor date that has an eligible fully executable 90D observation for the selected notional;
2. if none, latest maturity before the anchor date with an eligible observation.

Export as `drilldownMaturity`.

- [ ] **Step 8: Write RED tests for fair high/low venue comparison**

Build duplicated pair rows where one venue quote appears against multiple counterparties.

Required comparison key:

```text
asset + token_id + maturity + timestamp + notional
```

Short side:
- deduplicate per venue;
- if one venue has conflicting short market/rate values in the same comparison group, exclude that venue from that group;
- require at least two valid venues;
- highest `short_bid_vwap_apr` wins;
- exact top-rate tie => no winner.

Long side:
- same rules;
- lowest `long_ask_vwap_apr` wins;
- exact bottom-rate tie => no winner.

Assert duplicate pair rows do not give a venue extra wins.

- [ ] **Step 9: Implement venue winner aggregation**

For each `asset + notional`, count high-side and low-side group wins.

Require at least `MIN_VENUE_COMPARISONS = 50` valid comparison groups for that side. If not, export venue as `null`.

If two venues tie for the highest total win count, export `null` rather than making an arbitrary claim.

- [ ] **Step 10: Implement compact payload schema**

Exact top-level shape:

```json
{
  "schemaVersion": 1,
  "generatedAt": "...",
  "historicalMaxTimestamp": 1787529600,
  "windowDays": 90,
  "notionals": [10000, 25000, 50000],
  "viability": {
    "minNetProfitUsd": 50.0,
    "minHoldingReturn": 0.01,
    "proxyModel": "current-crossex-conservative-envelope-v1"
  },
  "rows": []
}
```

Each row:

```json
{
  "asset": "HYPE",
  "notionalUsd": 10000,
  "available": true,
  "unavailableReason": null,
  "dteCutoffDays": 9,
  "referenceP95SpreadApr": 0.097,
  "proxy": {
    "costUsd": 20.0,
    "capitalUsd": 1800.0,
    "asOfTimestamp": 1787,
    "pairCount": 3
  },
  "normal": {
    "shortVenue": "HYPERLIQUID",
    "longVenue": "BYBIT",
    "medianSpreadApr": 0.048,
    "sampleCount": 4821,
    "drilldownMaturity": "2026-09-25"
  },
  "burst": {
    "shortVenue": "HYPERLIQUID",
    "longVenue": "BYBIT",
    "p95SpreadApr": 0.097,
    "sampleCount": 4821,
    "drilldownMaturity": "2026-09-25"
  },
  "highVenue": "HYPERLIQUID",
  "lowVenue": "BYBIT",
  "highVenueComparisonCount": 1000,
  "lowVenueComparisonCount": 1000
}
```

Use `null` for unavailable submetrics; never zero-fill missing values.

- [ ] **Step 11: Verify aggregation tests**

```bash
python3 -m pytest tests/test_radar.py tests/test_radar_economics.py -q
```

Expected: all pass.

- [ ] **Step 12: Commit**

```bash
git add boros_research/radar.py tests/test_radar.py
git commit -m "feat: aggregate historical market radar"
```

---

### Task 4: Add the production Radar builder and small generated payload

**Files:**
- Create: `build_market_radar_data.py`
- Create: `tests/test_build_market_radar_data.py`
- Generate: `site/data/boros_market_radar.json`

**Interfaces:**
- Consumes:
  - existing DuckDB, opened `read_only=True`
  - existing `CrossExClient.fetch(notional_usd)` for exactly `10000`, `25000`, `50000`
  - `build_radar_payload`
- Produces: compact deterministic JSON for a fixed DB + fixed CrossEx proxy responses.

CLI:

```bash
python3 build_market_radar_data.py \
  --database data/boros.duckdb \
  --output site/data/boros_market_radar.json
```

Optional:

```bash
--crossex-base-url http://127.0.0.1:6688
```

Token resolution stays inside the existing `CrossExClient`.

- [ ] **Step 1: Write RED tests for GET-only proxy collection**

Inject a fake `CrossExClient` into a pure helper:

```python
def collect_proxies(client: CrossExClient) -> tuple[
    dict[int, dict[str, ProxyEconomics]],
    dict[str, object],
]:
```

Assert:

```python
fake_client.fetch_calls == [10000, 25000, 50000]
```

No POST/PUT/DELETE surface is introduced.

- [ ] **Step 2: Write RED test for read-only DuckDB**

Monkeypatch/spy `duckdb.connect` and assert production builder calls:

```python
duckdb.connect(str(database_path), read_only=True)
```

No CREATE TABLE/INSERT is allowed on persistent schema. TEMP VIEW/TEMP TABLE inside the read-only connection is acceptable only if DuckDB permits it; otherwise keep all temporary cutoff data in CTE/value relations.

- [ ] **Step 3: Implement builder helpers**

Use:

```python
DEFAULT_DATABASE = Path("data/boros.duckdb")
DEFAULT_OUTPUT = Path("site/data/boros_market_radar.json")
```

`collect_proxies` must:
- fetch each notional once;
- gather valid asset proxies with `collect_asset_proxy`;
- gather the current CrossEx venue universe from valid pair legs;
- retain top-level/group warning text in output diagnostics;
- never call `fresh=1`.

- [ ] **Step 4: Fail clearly when CrossEx is unavailable**

Production CLI should exit nonzero with a concise message such as:

```text
Market Radar proxy unavailable: start the local CrossEx terminal/API and retry.
```

Do not silently substitute VIP0 costs, zero cost, guessed leverage, or a gross-only cutoff inside this builder.

- [ ] **Step 5: Write compact JSON with strict encoding**

Use:

```python
json.dumps(
    payload,
    separators=(",", ":"),
    sort_keys=True,
    allow_nan=False,
)
```

Enforce:

```python
encoded_size < 1_048_576
```

Raise `RuntimeError` if the Radar payload exceeds 1 MiB.

- [ ] **Step 6: Add builder regression tests**

Assert:
- schema version = 1;
- output contains exactly the three notionals;
- output is finite JSON (`allow_nan=False`);
- generated payload does not contain historical time series arrays;
- current-model proxy metadata is present;
- generated output size < 1 MiB;
- `build_arbitrage_site_data.py` is not imported or modified.

- [ ] **Step 7: Run focused tests**

```bash
python3 -m pytest tests/test_build_market_radar_data.py tests/test_radar.py tests/test_radar_economics.py -q
```

Expected: all pass.

- [ ] **Step 8: Run production builder with the local CrossEx API running**

```bash
python3 build_market_radar_data.py
```

Record:
- build runtime;
- output bytes;
- historical max timestamp;
- asset count per notional;
- unavailable asset/notional rows;
- DTE cutoff min/median/max per notional;
- proxy warnings.

Do not hide surprising results; inspect before committing generated data.

- [ ] **Step 9: Sanity-check production Radar rows**

At minimum inspect HYPE, BTC, and ETH if present:

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = json.loads(Path("site/data/boros_market_radar.json").read_text())
for row in p["rows"]:
    if row["asset"] in {"HYPE", "BTC", "ETH"} and row["notionalUsd"] == 10000:
        print(row)
PY
```

Verify no row claims a venue/direction with null/insufficient source metrics.

- [ ] **Step 10: Commit builder + generated data**

```bash
git add build_market_radar_data.py \
        boros_research/radar_economics.py \
        boros_research/radar.py \
        tests/test_build_market_radar_data.py \
        site/data/boros_market_radar.json
git commit -m "feat: build compact market radar data"
```

---

### Task 5: Build the standalone Market Radar UI

**Files:**
- Create: `site/radar.html`
- Create: `site/radar.js`
- Create: `site/radar.css`
- Create: `tests/test_radar_site.py`

**Interfaces:**
- Consumes: `./data/boros_market_radar.json`
- Produces: static read-only Radar page, default Traditional Chinese.

- [ ] **Step 1: Write RED static-structure tests**

Require:

```html
<html lang="zh-TW">
```

Required visible MVP columns in Chinese:

```text
資產
常態最佳方向
90天中位利差
爆發最佳方向
90天 P95
常高利率 Venue
常低利率 Venue
有效 DTE
```

Required notional choices:

```text
$10,000
$25,000
$50,000
```

Required explanatory note:

```text
DTE 係依目前 CrossEx 成本/資本模型作為歷史代理估算；不是歷史實際交易成本。
```

- [ ] **Step 2: Create minimal HTML/CSS**

Reuse shared `site/styles.css`.

`site/radar.css` may only add Radar-specific:
- table width/overflow;
- compact notional selector;
- unavailable state;
- responsive stacking/scroll behavior.

Do not copy the full shared stylesheet.

- [ ] **Step 3: Write RED Node DOM tests for language and notional rendering**

Test query semantics:

```text
no ?lang=  -> zh
?lang=zh   -> zh
?lang=en   -> en
```

Test default notional:

```text
10000
```

Test switching to 25000 renders only 25000 rows.

- [ ] **Step 4: Implement `site/radar.js` state**

Use:

```javascript
const DATA_URL = "./data/boros_market_radar.json";
const NOTIONALS = [10000, 25000, 50000];

const params = new URLSearchParams(window.location.search);
const state = {
  data: null,
  notional: NOTIONALS.includes(Number(params.get("notional")))
    ? Number(params.get("notional"))
    : 10000,
  lang: params.get("lang") === "en" ? "en" : "zh",
};
```

Do not persist to localStorage in MVP.

- [ ] **Step 5: Implement exact display semantics**

Formatting:

```text
medianSpreadApr / p95SpreadApr -> percentage
dteCutoffDays -> "≥ 9 天" / "≥ 9d"
null metric -> "—"
```

Direction:

```text
SHORT_VENUE → LONG_VENUE
```

Rows sorted by `asset`.

Do not add a score, badges that imply recommendations, or live language such as “buy”, “trade now”, or “recommended size”.

- [ ] **Step 6: Write RED tests for deep-link generation**

Normal direction link exact query keys:

```text
arbitrage.html
?asset=HYPE
&direction=HYPERLIQUID%7CBYBIT
&maturity=2026-09-25
&notional=10000
&lang=zh
```

Burst uses its own `drilldownMaturity`.

If normal/burst is unavailable, render plain `—`, not a broken link.

- [ ] **Step 7: Implement links with `URLSearchParams`**

Never hand-concatenate unescaped direction values.

- [ ] **Step 8: Add loading/error states**

Distinguish:
- fetch/HTTP failure;
- malformed payload;
- empty rows for selected notional.

Do not label a render exception as a data-fetch error; use separate try/catch phases.

- [ ] **Step 9: Verify focused UI tests and syntax**

```bash
python3 -m pytest tests/test_radar_site.py -q
node --check site/radar.js
git diff --check
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add site/radar.html site/radar.js site/radar.css tests/test_radar_site.py
git commit -m "feat: add historical market radar"
```

---

### Task 6: Add validated Explorer deep-links and navigation

**Files:**
- Modify: `site/arbitrage.js`
- Modify: `site/arbitrage.html`
- Modify: `site/index.html`
- Modify: `tests/test_arbitrage_site.py`
- Test: `tests/test_radar_site.py`

**Interfaces:**
- Consumes Radar query params:
  - `asset`
  - `direction` formatted as `SHORT|LONG`
  - `maturity`
  - `notional`
  - `lang`
- Produces validated Explorer initial state without changing analytical calculations.

- [ ] **Step 1: Write RED Explorer deep-link test**

Execute current `arbitrage.js` in the existing Node DOM harness with:

```text
?asset=HYPE&direction=HYPERLIQUID%7CBYBIT&maturity=2026-09-25&notional=25000&lang=zh
```

After data load and `populateFilters()`, assert state/selects match all four requested filters when they exist.

- [ ] **Step 2: Write RED invalid-query tests**

Examples:

```text
notional=12345
direction=NOT_A_REAL_PAIR
maturity=1900-01-01
```

Expected:
- invalid notional falls back to 10000;
- invalid direction uses the existing valid deterministic fallback for the selected asset;
- invalid maturity must not switch to an unrelated asset or direction;
- no exception.

- [ ] **Step 3: Implement query initialization only**

At module startup:

```javascript
const params = new URLSearchParams(window.location.search);
const requestedNotional = Number(params.get("notional"));

const state = {
  data: null,
  asset: params.get("asset") || "",
  direction: params.get("direction") || "",
  expiration: params.get("maturity") || "",
  market: "",
  notional: NOTIONALS.includes(requestedNotional) ? requestedNotional : 10000,
  view: "spread",
  lang: params.get("lang") === "en" ? "en" : "zh",
};
```

Keep `populateFilters()` as the authority that validates whether requested values exist.

Do not change percentile, distribution, leaderboard, notional comparison, or selected-structure semantics.

- [ ] **Step 4: Ensure language survives Radar → Explorer**

`radar.js` must include current language in the deep-link.

Explorer language toggle may keep its current behavior; no new persistence layer.

- [ ] **Step 5: Add navigation links**

All three pages should expose simple navigation among:

```text
APR Research
Market Radar
Arbitrage Research
```

Do not redesign the header.

- [ ] **Step 6: Run focused regression**

```bash
python3 -m pytest tests/test_arbitrage_site.py tests/test_radar_site.py -q
node --check site/arbitrage.js
node --check site/radar.js
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add site/arbitrage.js site/arbitrage.html site/index.html \
        tests/test_arbitrage_site.py tests/test_radar_site.py
git commit -m "feat: link radar to arbitrage explorer"
```

---

### Task 7: Production verification, docs, and preservation audit

**Files:**
- Modify: `README.md`
- Verify all created/modified files.
- No analytical changes outside Radar + Explorer query initialization/navigation.

**Interfaces:**
- Produces: release-ready Phase 4A Radar MVP evidence.

- [ ] **Step 1: Run the full suite**

```bash
python3 -m pytest -q
```

Expected: all tests pass; final count must be reported exactly.

- [ ] **Step 2: Run static checks**

```bash
git diff --check
node --check site/app.js
node --check site/arbitrage.js
node --check site/radar.js
```

All must pass.

- [ ] **Step 3: Rebuild Radar production data**

With local CrossEx API running:

```bash
python3 build_market_radar_data.py
```

Immediately rerun:

```bash
git diff --check
```

Record exact output size and diagnostics.

- [ ] **Step 4: Start/reuse the static server**

```bash
python3 -m http.server 8765
```

Verify HTTP 200:

```text
/site/index.html
/site/radar.html
/site/radar.html?lang=en
/site/arbitrage.html
/site/data/boros_market_radar.json
```

- [ ] **Step 5: Manual browser acceptance**

Open:

```text
http://127.0.0.1:8765/site/radar.html
```

Verify:
- opens in Traditional Chinese;
- `$10k` is default;
- every available asset row answers the eight MVP columns;
- switching `$25k/$50k` changes rows without mixing notionals;
- unavailable metrics show `—`;
- proxy disclaimer is visible;
- normal/burst direction click opens the matching Explorer filters;
- `?lang=en` renders English;
- no console exception.

If no browser connector is available, explicitly report that limitation and use the Node DOM + HTTP evidence instead; do not claim Safari verification.

- [ ] **Step 6: Preservation audit**

Run:

```bash
git diff 1010aff3103815733d33050cf6e34b8e3e34ff4f -- \
  boros_research/benchmark.py \
  boros_research/live_benchmark.py \
  boros_research/monitor.py \
  boros_research/alert_state.py \
  boros_research/execution.py \
  boros_research/crossex_client.py \
  build_arbitrage_site_data.py \
  site/data/boros_arbitrage_site_data.json
```

Expected: empty unless the starting HEAD legitimately advanced before execution. If non-empty, stop and explain why before finalizing.

Also search for mutation surfaces introduced by Radar:

```bash
grep -RInE 'POST|PUT|DELETE|place.?order|create.?order|cancel.?order|wallet|private.?key' \
  build_market_radar_data.py boros_research/radar*.py site/radar.* || true
```

Review every match. Radar must remain read-only.

- [ ] **Step 7: Add concise README instructions**

Document exactly:

```bash
# CrossEx local API must be running for current proxy economics.
python3 build_market_radar_data.py

python3 -m http.server 8765
# open http://127.0.0.1:8765/site/radar.html
```

Also state:
- Radar is historical research, not live opportunity economics;
- DTE uses current CrossEx cost/capital proxy;
- Explorer retains all historical DTE data.

- [ ] **Step 8: Re-run full verification after README/generated-data changes**

```bash
python3 -m pytest -q
git diff --check
node --check site/app.js
node --check site/arbitrage.js
node --check site/radar.js
git status --short
```

All checks must pass; status should contain only intended uncommitted files before final commit.

- [ ] **Step 9: Final commit**

```bash
git add README.md site/data/boros_market_radar.json
git commit -m "docs: document market radar workflow"
```

If generated data was already committed unchanged, commit only README.

- [ ] **Step 10: Push**

```bash
git push origin feature/boros-arbitrage-research
```

- [ ] **Step 11: Verify exact-head CI**

Record:

```bash
git rev-parse HEAD
```

Wait for the push-triggered GitHub Actions `Test` workflow for that exact SHA.

Required final state:

```text
status: completed
conclusion: success
head_sha: exact local HEAD
```

- [ ] **Step 12: Final report**

Report exactly:

```text
- starting HEAD
- final HEAD
- commits created
- RED/GREEN evidence per task
- full pytest count
- Radar production build runtime
- Radar JSON bytes
- assets available/unavailable by $10k/$25k/$50k
- DTE cutoff min/median/max by notional
- HYPE/BTC/ETH sanity-check rows when present
- default zh / explicit en verification
- Explorer deep-link verification
- HTTP smoke
- browser/manual result or explicit connector limitation
- preservation diff result
- CrossEx GET-only/read-only evidence
- push result
- exact-head CI run/result
- git status --short
```

Stop after this milestone. Do not add winner-frequency, extreme-event-source percentage, per-asset Radar detail pages, composite scores, live Radar data, or Phase 4B features.

---

## Plan Self-Review

### Spec coverage

- Separate Radar discovery page: Task 5.
- Existing Explorer retained: Tasks 5–6.
- Per-asset/per-notional DTE cutoff: Tasks 2–3.
- `$50 + 1%` proxy viability: Task 2.
- Historical gross + current proxy distinction: Tasks 2, 4, 5.
- No outlier trimming: Task 3 regression.
- 90D median normal-best: Task 3.
- 90D P95 burst-best: Task 3.
- High/low venue fair comparison: Task 3.
- CrossEx-compatible universe: Tasks 3–4.
- Compact separate JSON: Task 4.
- Traditional Chinese default / English opt-in: Task 5.
- Deep-link into Explorer: Task 6.
- No trading/mutations and no CrossEx modification: Global Constraints + Task 7.
- Missing-data conservatism: Task 3.
- `< 1 MiB` payload: Task 4.

### Placeholder scan

No TBD/TODO/“implement later” requirements remain. All deferred features are explicitly out of scope.

### Type/interface consistency

- `ProxyEconomics` is defined in Task 2 and consumed in Tasks 3–4.
- `ViabilityCutoff` is defined in Task 2 and consumed in Task 3.
- `build_radar_payload` is produced in Task 3 and consumed by Task 4.
- JSON keys used by `site/radar.js` are fixed in Task 3 and tested in Tasks 4–5.
- Deep-link query keys are fixed in Task 5 and consumed by Task 6.
