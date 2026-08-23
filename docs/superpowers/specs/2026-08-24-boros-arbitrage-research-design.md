# Boros Arbitrage Research & Alert System — Design Specification

Date: 2026-08-24

## 1. Purpose

Extend `your-quantguy/boros` from an APR research pipeline into a historical and live research system for evaluating fixed-rate cross-venue Boros arbitrage opportunities that can be hedged through Gate CrossEx.

The system must answer two questions:

1. **Historical research:** For a given asset, maturity, direction, venue pair, and trade notional, how attractive was the executable Boros fixed-rate spread relative to comparable historical opportunities?
2. **Live monitoring (later phase):** When `Arbitrage with CrossEx` reports a live opportunity, how exceptional is it relative to the historical benchmark, and should the user be alerted?

This project is a research and monitoring companion. It is **not** a trading engine.

---

## 2. Hard Constraints

### 2.1 Arbitrage with CrossEx is read-only

`pendle-finance/arbitrage-with-crossex` is an external dependency and must remain untouched.

Forbidden:

- modifying its source code;
- forking or patching it as part of this project;
- importing internal modules from it;
- reading or writing its SQLite database;
- calling execution/mutation endpoints;
- reproducing its order-management logic.

Allowed only in the later live-integration phase:

- read-only HTTP GET access to its public/local opportunity endpoint(s), especially `/api/opportunities`;
- using its output as the live executable-opportunity reference.

The research system must continue to function without `Arbitrage with CrossEx` running.

### 2.2 No automated trading in this project

The system may research, rank, monitor, and notify. It must not submit Boros orders or CrossEx orders.

`@pendle/boros-mcp` is permitted as a Codex development/research aid, but production research jobs must not depend on an LLM or MCP process being available.

### 2.3 Historical executable spread is the primary research metric

Historical opportunity quality must be based on simulated executable Boros rates at a defined notional, not only on mark APR, mid APR, or best bid/ask.

---

## 3. Existing Codebase to Preserve

The base repository is `your-quantguy/boros`.

Existing useful capabilities include:

- downloading Boros Historical Data ZIP/NDJSON files;
- retaining raw ZIP archives;
- parsing `market-data`, `underlying-apr`, `settlement`, and OHLCV;
- implied-vs-realized APR analysis;
- generation of processed datasets;
- a static interactive research website and GitHub Pages deployment path.

These capabilities should be preserved where practical. The project may be refactored to support a larger data architecture, but unrelated functionality should not be removed.

---

## 4. System Scope by Phase

### Phase 1 — Data Foundation

Build a reliable historical data platform capable of reconstructing executable Boros spreads.

Deliverables:

- selective historical downloader;
- raw ZIP retention;
- normalized Parquet datasets;
- DuckDB research catalog/views;
- combined order-book parser;
- 5-minute canonical research timeline;
- executable order-book walker;
- cross-venue pair generator;
- notional ladder simulation;
- correctness and regression tests.

### Phase 2 — Historical Benchmarking

Build objective statistical context before inventing final grades.

Deliverables:

- contract, structural, pair, and asset percentile families;
- configurable DTE bucketing;
- rolling 30d/90d distributions where coverage permits;
- sample size and confidence labels;
- robust statistics;
- persistence/episode analysis;
- funding context features.

### Phase 3 — Research Dashboard

Extend the current static site into an arbitrage research UI.

Deliverables:

- Market Explorer;
- historical spread distribution;
- depth/notional curves;
- episode explorer;
- funding-context views;
- visible coverage/confidence diagnostics.

### Phase 4 — Live Read-Only Integration

Read live opportunities from `Arbitrage with CrossEx` without modifying it.

Deliverables:

- read-only localhost client;
- current opportunity to historical benchmark mapping;
- live comparison cards;
- stale/error handling;
- validation against the same notional/mode assumptions.

### Phase 5 — Grade and Alerting

Only after historical benchmarking is validated:

- define the final grade/score model;
- add persistence gates and hard safety filters;
- add Telegram/Discord/macOS alert channels;
- do not automate execution.

---

## 5. Data Sources

### 5.1 Historical source of truth

Use the official Boros bulk archive at `historical-data.boros.finance`.

Download and retain the official archive artifacts unchanged. In addition, cache official Boros Indicator exports needed for USD collateral conversion; these are stored separately from the bulk archive so provenance remains clear.

For all Boros markets, collect lightweight datasets relevant to context and research, including:

- `market-data`;
- underlying funding history (`funding-rate` in the current archive; accept legacy `underlying-apr` when present in older manifests);
- `settlement`;
- OHLCV (prefer 5m where useful for alignment; retain existing 1d analytics where already used).

For markets that can participate in a CrossEx-compatible hedge structure, additionally collect:

- `order-book/combined_0.0001`.

Do **not** make `order-book/raw` a default Phase 1 dependency. It may be added later for AMM-vs-CLOB microstructure research.

### 5.2 Live Boros source

Use official Boros REST/WebSocket endpoints in later phases for current market data and validation.

### 5.3 Boros MCP

Configure `@pendle/boros-mcp` for Codex as a developer/research assistant. Appropriate uses include:

- resolving market IDs and metadata;
- checking current APR and market state;
- validating API assumptions;
- spot-checking order books;
- consulting Boros documentation/tool outputs during development.

It is not the production scheduler or primary historical data source.

### 5.4 Arbitrage with CrossEx

Later live integration consumes read-only local HTTP responses. No internal imports or source coupling.

---

## 6. CrossEx-Compatible Market Selection

The downloader should fetch lightweight data for the full Boros universe.

Full combined order-book history should be downloaded only for Boros markets whose underlying venue can form a hedge through the currently supported CrossEx venue universe.

The compatibility mapping must be configuration/data driven, not scattered hard-coded conditionals.

Initial known CrossEx-compatible venues from the reference tool include:

- Binance;
- Bybit;
- Gate;
- OKX;
- Kraken;
- Hyperliquid.

Deribit support should be included only if the chosen compatibility source confirms it for the target version/runtime; the compatibility list must be easy to update without rewriting the pipeline.

Boros venues without a CrossEx hedge path still retain lightweight history so that historical coverage can be extended later if CrossEx adds support.

---

## 7. Storage Architecture

### 7.1 Raw layer

```text
raw_boros/
  files.json
  market-data/...
  funding-rate/...              # or legacy underlying-apr/... when present
  settlement/...
  ohlcv/...
  order-book/combined_0.0001/...
```

Supplementary official Indicator exports:

```text
raw_indicators/
  asset-price/...
```

Rules:

- official ZIP files are stored unchanged;
- repeat runs skip already complete files using manifest size/integrity checks;
- partial downloads use temporary files and atomic rename;
- raw files are never modified in place.

### 7.2 Normalized Parquet layer

```text
data/parquet/
  markets/
  market_data/
  funding_rates/
  asset_prices/
  settlements/
  orderbooks/
  observations/
  executable_opportunities/
  benchmarks/
  episodes/
```

Partition large datasets by fields that materially reduce scan cost, such as asset/market/date, while avoiding over-partitioning into tiny files.

### 7.3 DuckDB layer

```text
data/boros.duckdb
```

DuckDB is the research/query catalog and should contain:

- metadata tables;
- configuration snapshots;
- views over Parquet;
- compact aggregate/benchmark tables where beneficial;
- schema/version metadata.

Large raw time-series payloads should remain primarily in Parquet rather than being duplicated wholesale into DuckDB.

### 7.4 CSV

CSV remains an export/debug format only. It is not the canonical data store.

---

## 8. Canonical Historical Timeline

### 8.1 Sampling interval

The research layer uses a fixed **5-minute UTC grid**.

For each market and grid timestamp, use the most recent combined `0.0001` order-book snapshot at or before the grid timestamp.

### 8.2 Freshness

Maximum allowable snapshot age: **15 minutes**.

If the newest prior snapshot is older than 15 minutes:

- mark the order-book observation missing;
- do not forward-fill it into executable-spread calculations;
- preserve the reason/age for diagnostics.

Every normalized observation should include `snapshot_age_sec` and a freshness/coverage flag.

### 8.3 Why fixed-time resampling is required

Raw snapshots may be event-driven or irregular. Treating every raw snapshot as an equal statistical observation would over-weight busy periods. The 5-minute canonical grid provides time-weighted historical distributions and makes persistence analysis meaningful.

---

## 9. Order-Book Semantics and Book Walking

Boros order-book side semantics are easy to invert and must be encoded explicitly.

For the reference Boros API representation:

- wire `short` corresponds to the ask side consumed by a trader going LONG fixed;
- wire `long` corresponds to the bid side consumed by a trader going SHORT fixed.

Normalized representation:

```text
bids = rates available to SHORT fixed / receive fixed
asks = rates available to LONG fixed / pay fixed
```

APR tick size for the target historical combined book is `0.0001`.

The executable-rate engine must walk price levels until the target USD notional is filled, computing notional-weighted average fixed APR for each leg.

If either leg lacks sufficient depth, the opportunity is **not fully executable** at that notional and must not receive a normal executable-spread value.

No extrapolation beyond visible historical depth.

---

## 10. USD Notional and Collateral Conversion

Historical book sizes may be denominated in collateral token units rather than USD.

The pipeline must normalize book liquidity into USD before walking a requested USD notional.

Stable collateral can use its defined stable conversion assumption. Token-margined books require a valid historical collateral USD price aligned to the observation timestamp.

If a required collateral price cannot be resolved within a defined freshness rule:

- mark the observation unpriceable;
- do not silently treat token quantity as USD;
- surface the coverage loss in diagnostics.

Historical USD conversion precedence is fixed as follows:

1. For USDT collateral, use `1.0 USD` for sizing.
2. For non-stable collateral, ingest Boros Indicators `ap` (Asset Price) on the 5-minute grid from the official `/v1/indicators/export` endpoint and persist it as a normalized historical asset-price dataset. The official docs state that `ap` is sourced from Boros's historical asset-price store.
3. Align `ap` by asset and canonical 5-minute timestamp. A non-stable observation without a valid aligned `ap` value is unpriceable and must not be used for USD-notional execution simulation.
4. If multiple markets for the same asset provide `ap` at the same timestamp, deduplicate by `(asset, timestamp)` and require prices to agree within **0.5% relative difference**. A larger discrepancy is a data-quality failure and must not be averaged silently.

The price cache is an official Boros supplementary dataset even though it is fetched through Indicators rather than the bulk archive.

---

## 11. Cross-Venue Opportunity Construction

At each 5-minute timestamp, group markets by the economic contract identity needed for a four-leg fixed-rate spread:

- same underlying asset;
- same maturity;
- same Boros collateral token/zone, matching the reference `Arbitrage with CrossEx` grouping rule of same collateral + maturity + underlying;
- distinct Boros reference venues;
- both venues CrossEx-compatible for later hedge execution.

For each unordered venue pair A/B, evaluate both directed structures:

```text
A short fixed / B long fixed
B short fixed / A long fixed
```

A directed historical opportunity records:

- timestamp;
- asset;
- maturity;
- days to expiry;
- short venue;
- long venue;
- short market ID;
- long market ID;
- source snapshot timestamps and ages;
- top-of-book spread;
- notional-specific executable metrics;
- contextual market/funding fields;
- coverage/validity flags.

---

## 12. Notional Ladder

Precompute the following Phase 1 USD notionals:

- $1,000
- $2,000
- $5,000
- $10,000
- $25,000
- $50,000

For each notional store, at minimum:

- short-leg executable/VWAP APR;
- long-leg executable/VWAP APR;
- executable fixed spread;
- top-of-book spread;
- short-leg impact;
- long-leg impact;
- combined impact/erosion measure;
- short-leg sufficient-depth flag;
- long-leg sufficient-depth flag;
- fully-executable flag;
- depth consumed/levels consumed where useful for diagnostics.

Keep the original combined order-book archive so arbitrary future notionals can be recalculated without re-downloading history.

---

## 13. Historical Benchmark Model

Do not begin with an arbitrary 0-100 score. First expose objective distributions.

For a current/historical directed opportunity, compute multiple comparison families.

### 13.1 Contract percentile

Same asset, venue direction, and exact maturity/contract history.

Question answered: “How high is this opportunity within this contract’s own life?”

### 13.2 Structural percentile

Same asset, same directed venue pair, similar DTE regime.

This is the preferred long-run comparison when adequate history exists.

Default configurable DTE buckets:

- 0–7 days;
- 8–21 days;
- 22–45 days;
- 46–90 days;
- >90 days.

### 13.3 Pair percentile

Same asset and directed venue pair, all DTE.

### 13.4 Asset percentile

Same asset across all CrossEx-compatible directed venue pairs.

### 13.5 Rolling distributions

Where coverage permits, also compute rolling windows such as 30d and 90d to distinguish current regime from full-history behavior.

### 13.6 Required statistics

Each benchmark family should expose, where valid:

- percentile rank;
- median;
- p75;
- p90;
- p95;
- p99;
- robust z-score or equivalent robust distance statistic;
- sample count;
- time coverage;
- missing/invalid ratio.

### 13.7 Confidence and fallback

Percentiles must include sample size and confidence.

Initial configurable thresholds:

- >=500 observations: High confidence;
- 200–499: Medium;
- 50–199: Low;
- <50: do not present as a formal percentile.

When the preferred structural cohort lacks data, fall back in order to broader cohorts while retaining the original cohort’s sample count/coverage diagnostics.

Do not hide the fallback level from the user.

---

## 14. Persistence / Episode Analysis

Historical attractiveness is not only about magnitude; it is also about how long an opportunity remains available.

The benchmark layer should segment contiguous 5-minute observations into “episodes” under configurable threshold definitions, such as crossing p90/p95/p99 for a given notional and cohort.

For each episode record:

- start time;
- end time;
- duration;
- peak spread;
- peak percentile;
- time from start to peak;
- number of valid samples;
- interruptions caused by stale/missing data;
- notional and cohort definition.

Derived research statistics should include:

- episode frequency;
- median duration;
- p25/p75/p90 duration;
- median time to peak;
- recurrence interval.

This dataset will later inform alert persistence gates so notifications are not generated from one transient bad quote.

---

## 15. Funding and Market Context

Funding history is explanatory context, not the proof that a locked four-leg fixed-spread trade is profitable.

For each opportunity observation, where available, attach trailing realized funding-spread features such as:

- 1d;
- 3d;
- 7d;
- 14d;
- 30d.

Also attach relevant context such as:

- Boros mark/mid spread;
- open interest;
- volume;
- latest settlement APR;
- fixed spread minus trailing realized funding spread.

This enables later research into whether a large Boros spread is primarily:

- consistent with a recent exchange funding regime; or
- unusually dislocated relative to recent realized funding.

These labels are descriptive and must not automatically imply good/bad trade quality in Phase 2.

---

## 16. Dashboard Design

Preserve the existing static-site deployment approach where practical. Avoid introducing a backend service solely for Phase 3 if precomputed compact data can support the views.

### 16.1 Market Explorer

Filters:

- asset;
- short venue;
- long venue;
- maturity/contract;
- DTE bucket;
- notional;
- date range.

Primary outputs:

- executable-spread time series;
- top-of-book vs executable spread;
- percentile time series;
- snapshot age/coverage markers.

### 16.2 Distribution View

Show selected cohort distribution and key quantiles.

### 16.3 Depth / Notional Curve

For a selected timestamp/cohort, compare executable spread across the six notional levels and visualize capacity erosion.

### 16.4 Episode Explorer

List and chart historical p90/p95/p99 episodes, duration, peak, and recurrence.

### 16.5 Funding Context

Compare Boros executable/fixed spread with trailing realized funding-spread features.

### 16.6 Diagnostics

Every research view must make data quality visible:

- sample count;
- confidence;
- coverage range;
- missing ratio;
- stale snapshot count;
- current fallback cohort.

---

## 17. Live Integration Design (Phase 4)

The live client should query `Arbitrage with CrossEx` over localhost using read-only GET requests.

The project must treat the endpoint as an external contract that may change.

Requirements:

- configurable base URL;
- short request timeout;
- schema validation;
- graceful unavailable/stale state;
- no retry storm;
- no dependence on internal database or filesystem layout;
- preserve the exact requested notional and entry/exit assumptions alongside each live result.

The current opportunity should be mapped to the historical identity:

```text
asset + maturity + short venue + long venue + notional + DTE cohort
```

Live output will later display the live tool’s net economics together with historical Boros benchmark context.

---

## 18. Future Grade / Alert Model (Phase 5 Boundary)

The grading formula is deliberately **not fixed in this specification**. It must be calibrated after Phase 2 data is available.

Any future alert candidate should be gated by hard validity conditions before scoring, including at minimum:

- fully executable at the chosen notional;
- fresh data;
- positive conservative economics under the selected `Arbitrage with CrossEx` assumptions;
- sufficient historical confidence or explicitly degraded confidence;
- persistence requirement to suppress single-snapshot anomalies.

The final grade may later combine historical rarity, executable economics, depth/impact, persistence, and data confidence, but the weights are a research output rather than a Phase 1 assumption.

---

## 19. Validation Strategy

### 19.1 Unit tests

Required test coverage includes:

- market-name/metadata parsing;
- venue alias normalization;
- order-book side normalization;
- APR tick conversion;
- USD collateral conversion;
- book walking and VWAP;
- insufficient depth;
- 5-minute grid alignment;
- 15-minute staleness cutoff;
- pair construction and direction;
- DTE calculation and bucket selection;
- percentile/confidence calculations;
- episode segmentation.

### 19.2 Regression fixture: HYPE Hyperliquid ↔ Bybit

Use the already researched HYPE Hyperliquid/Bybit history as a regression fixture.

The rebuilt pipeline should reproduce the qualitative historical conclusions and reasonable numeric agreement from the manual study after accounting for differences between top-of-book and true notional-specific execution.

The fixture is a guard against:

- swapped venue identity;
- swapped bid/ask semantics;
- funding interval mistakes;
- accidental use of mark/mid APR instead of executable APR.

### 19.3 Cross-check against live Arbitrage with CrossEx

Before Phase 4 is considered valid, compare the project’s live Boros book-walking result against the reference tool for the same market snapshot and notional.

The exact comparison tolerance should reflect timestamp and feed differences, but material directional or spread discrepancies must fail validation and be investigated rather than accepted.

### 19.4 Data quality tests

On every pipeline build, emit diagnostics for:

- number of source files expected/downloaded;
- parse failures;
- stale/missing observations;
- unpriceable collateral observations;
- pair counts;
- executable-depth rates by notional;
- earliest/latest coverage by dataset.

The pipeline must fail loudly on schema changes that would silently corrupt rates, timestamps, or side semantics.

---

## 20. Operational Requirements

- Commands must be restartable and idempotent.
- A failed download must not corrupt an existing raw file.
- Derived Parquet/DuckDB outputs should be rebuildable from raw files.
- Expensive order-book transformation should support incremental processing where practical.
- Configuration belongs in one explicit configuration module/file rather than duplicated literals.
- All timestamps are stored in UTC; presentation may convert to local time.
- Percentage/APR units must be explicit in names or schema documentation to prevent decimal-vs-percent mistakes.
- The pipeline should run locally on macOS with Python 3.
- Avoid infrastructure that is unnecessary for a single-user local research tool.

---

## 21. Proposed Repository Evolution

The exact implementation plan may refine names after examining dependencies, but the responsibility boundaries should evolve toward something like:

```text
boros/
  boros_apr_pipeline.py          # compatibility CLI / orchestration entrypoint
  build_site_data.py             # existing site build entrypoint

  boros_research/
    config.py                    # compatibility venues, notionals, sampling/freshness
    manifest.py                  # archive manifest and selective download planning
    normalize.py                 # shared metadata/venue/asset normalization
    storage.py                   # Parquet and DuckDB catalog helpers
    orderbook.py                 # combined-book parser and normalized book model
    execution.py                 # USD book walking / VWAP / impact calculations
    timeline.py                  # 5m canonical sampling and freshness policy
    opportunities.py             # directed cross-venue pair construction
    funding.py                   # realized funding normalization/context features
    benchmarks.py                # cohort stats, percentiles, confidence/fallback
    episodes.py                  # persistence segmentation and statistics
    validation.py                # coverage and consistency reports

  tests/
    ... focused unit/regression tests ...

  data/
    parquet/...
    boros.duckdb

  raw_boros/...

  site/
    index.html
    app.js
    styles.css
    data/...
```

The existing 40k-line pipeline should not continue absorbing every new responsibility. New arbitrage research logic should be modularized while preserving the old CLI behavior where feasible.

---

## 22. Phase 1 Acceptance Criteria

Phase 1 is complete only when all of the following are true:

1. One command can refresh the Boros archive manifest and download required historical data.
2. Lightweight datasets cover the full Boros universe selected by the archive.
3. `combined_0.0001` history is selectively downloaded for CrossEx-compatible markets.
4. Existing complete raw ZIPs are skipped safely on rerun.
5. Raw ZIPs remain byte-for-byte unchanged.
6. Normalized historical outputs are written as Parquet.
7. DuckDB exposes documented views/tables over normalized datasets.
8. Non-stable collateral USD prices are materialized from official Boros `ap` indicator history and validated before USD-notional simulation.
9. The pipeline reconstructs a 5-minute canonical order-book timeline.
10. Snapshots older than 15 minutes are not used as valid execution observations.
11. The engine correctly normalizes Boros bid/ask side semantics.
12. The engine can simulate $1k/$2k/$5k/$10k/$25k/$50k Boros execution.
13. Insufficient depth is explicitly represented and never extrapolated.
14. Cross-venue directed opportunities are generated only for valid same-asset/same-maturity pairs.
15. Token-collateral books are not mistaken for USD-sized books.
16. Regression tests cover HYPE Hyperliquid ↔ Bybit.
17. Automated data-quality output reports coverage and failures.
18. Existing APR-analysis functionality still works or has an explicitly documented replacement path.
19. No code or data in `Arbitrage with CrossEx` is modified.

---

## 23. Decisions Deferred Until Data Exists

The following are intentionally not fixed now:

- final A/B/S or 0-100 grading formula;
- final percentile weighting across cohorts;
- final minimum net APR/ROI alert threshold;
- final impact threshold;
- final alert persistence duration;
- final notification channel priority;
- whether raw CLOB order books should be archived in addition to combined books;
- whether live monitoring should run every 30s, 60s, or another cadence.

These should be chosen from Phase 2 research rather than intuition.

---

## 24. Design Summary

The central architectural principle is separation of concerns:

```text
Official Boros historical/live data
          ↓
Historical executable-spread research
          ↓
Objective benchmarks and persistence statistics
          ↓
Read-only comparison with Arbitrage with CrossEx live opportunities
          ↓
Human-facing grade / alert
          ↓
Human executes separately in Arbitrage with CrossEx
```

This preserves the trusted execution tool while giving the user an independent research layer capable of deciding whether a live opportunity is ordinary, attractive, or historically exceptional.
