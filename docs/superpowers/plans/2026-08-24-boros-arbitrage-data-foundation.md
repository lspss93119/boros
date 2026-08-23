# Boros Arbitrage Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `lspss93119/boros` with a restartable Phase 1 historical-data foundation that reconstructs notional-specific executable Boros cross-venue spreads from official combined order books and stores normalized research data in Parquet + DuckDB.

**Architecture:** Preserve the existing APR-analysis script and static site, while moving all new arbitrage research code into a focused `boros_research/` package. Official Boros ZIP archives remain immutable raw inputs; derived normalized datasets live in partitioned Parquet, and DuckDB provides the query catalog. Phase 1 ends at executable historical opportunities and data-quality validation—no benchmark grades, alerts, or trading.

**Tech Stack:** Python 3.11+, standard library HTTP/ZIP/CSV, `pyarrow`, `duckdb`, `pytest`; official Boros Historical Data archive and Boros Open API/Indicators; optional `@pendle/boros-mcp` for Codex development-time validation only.

**Spec:** `docs/superpowers/specs/2026-08-24-boros-arbitrage-research-design.md`

## Global Constraints

- `pendle-finance/arbitrage-with-crossex` is an external read-only dependency; do not modify, fork, patch, import internals, read its SQLite DB, or call execution endpoints.
- This repository must not submit Boros or CrossEx trades.
- Historical executable spread is based on full visible combined-book depth at the requested USD notional, never extrapolated beyond available depth.
- Canonical research grid is 5 minutes UTC; an order-book snapshot older than 15 minutes is invalid for execution research.
- Precompute notionals: `$1k`, `$2k`, `$5k`, `$10k`, `$25k`, `$50k`.
- Use official `order-book/{market}/combined_0.0001` history; `raw` order-book data is not a Phase 1 dependency.
- Pair only Boros markets sharing asset, maturity, and collateral token, matching the reference arbitrage tool's grouping semantics.
- Keep official raw ZIPs byte-for-byte unchanged and rebuild all derived outputs from raw data.
- Existing APR-analysis functionality and Pages build must remain working.

---

## File Structure Locked for Phase 1

Create or evolve toward:

```text
boros/
  pyproject.toml
  boros_apr_pipeline.py              # existing compatibility CLI; minimal changes only
  build_site_data.py                 # existing, unchanged in Phase 1 unless tests expose a compatibility issue

  boros_research/
    __init__.py
    config.py                         # constants and configurable research policy
    normalize.py                      # venue/asset/market slug normalization
    manifest.py                       # historical archive manifest planning
    market_metadata.py                # Boros Open API market + collateral metadata cache
    download.py                       # atomic/idempotent raw downloads
    datasets.py                       # lightweight historical NDJSON normalization
    storage.py                        # Parquet writers + DuckDB catalog
    orderbook.py                      # combined book parser/model
    timeline.py                       # 5m grid + 15m freshness alignment
    indicators.py                     # official `ap` asset-price history cache
    execution.py                      # USD book walk/VWAP/notional ladder
    opportunities.py                  # same-asset/maturity/collateral directed venue pairs
    validation.py                     # coverage/data-quality report
    cli.py                            # Phase 1 orchestration

  tests/
    fixtures/
      manifest.json
      markets.json
      assets.json
      combined_orderbook.ndjson
      hype_hl_bybit_market_data.json
    test_config.py
    test_normalize.py
    test_manifest.py
    test_market_metadata.py
    test_download.py
    test_storage.py
    test_datasets.py
    test_orderbook.py
    test_timeline.py
    test_indicators.py
    test_execution.py
    test_opportunities.py
    test_validation.py
    test_hype_regression.py

  data/                               # ignored local derived data
    parquet/
    boros.duckdb
  raw_boros/                          # ignored official archive ZIPs
  raw_api/                            # ignored cached market/assets JSON
  raw_indicators/                     # ignored cached `ap` history
```

---

### Task 1: Add package/test foundation and central research configuration

**Files:**
- Create: `pyproject.toml`
- Create: `boros_research/__init__.py`
- Create: `boros_research/config.py`
- Create: `tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `NOTIONALS_USD`, `SAMPLE_INTERVAL_SEC`, `MAX_SNAPSHOT_AGE_SEC`, `CROSSEX_VENUES`, `DTE_BUCKETS`, base URLs, and directory defaults used by all later tasks.

- [ ] **Step 1: Write the failing configuration test**

```python
# tests/test_config.py
from boros_research.config import (
    CROSSEX_VENUES,
    DTE_BUCKETS,
    MAX_SNAPSHOT_AGE_SEC,
    NOTIONALS_USD,
    SAMPLE_INTERVAL_SEC,
)


def test_phase1_research_policy_is_explicit():
    assert SAMPLE_INTERVAL_SEC == 300
    assert MAX_SNAPSHOT_AGE_SEC == 900
    assert NOTIONALS_USD == (1_000, 2_000, 5_000, 10_000, 25_000, 50_000)
    assert {"BINANCE", "BYBIT", "GATE", "OKX", "KRAKEN", "HYPERLIQUID"} <= CROSSEX_VENUES
    assert DTE_BUCKETS == (
        (0, 7, "0-7"),
        (8, 21, "8-21"),
        (22, 45, "22-45"),
        (46, 90, "46-90"),
        (91, None, "91+"),
    )
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m pytest tests/test_config.py -q`
Expected: FAIL because `boros_research` does not exist.

- [ ] **Step 3: Add packaging and dependencies**

```toml
# pyproject.toml
[project]
name = "boros-research"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "duckdb>=1.0,<2",
  "pyarrow>=15",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Implement central configuration**

```python
# boros_research/config.py
from pathlib import Path

HISTORICAL_BASE_URL = "https://historical-data.boros.finance"
BOROS_OPEN_API_BASE_URL = "https://api-boros.pendle.finance/apis/v1"

SAMPLE_INTERVAL_SEC = 5 * 60
MAX_SNAPSHOT_AGE_SEC = 15 * 60
NOTIONALS_USD = (1_000, 2_000, 5_000, 10_000, 25_000, 50_000)

CROSSEX_VENUES = frozenset({
    "BINANCE", "BYBIT", "GATE", "OKX", "KRAKEN", "HYPERLIQUID"
})

DTE_BUCKETS = (
    (0, 7, "0-7"),
    (8, 21, "8-21"),
    (22, 45, "22-45"),
    (46, 90, "46-90"),
    (91, None, "91+"),
)

RAW_BOROS_DIR = Path("raw_boros")
RAW_API_DIR = Path("raw_api")
RAW_INDICATORS_DIR = Path("raw_indicators")
PARQUET_DIR = Path("data/parquet")
DUCKDB_PATH = Path("data/boros.duckdb")
```

- [ ] **Step 5: Update ignored local data paths**

Add to `.gitignore`:

```gitignore
raw_api/
raw_indicators/
data/
```

Do not ignore `docs/`, `tests/`, or the package.

- [ ] **Step 6: Run the test**

Run: `python3 -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore boros_research/__init__.py boros_research/config.py tests/test_config.py
git commit -m "chore: add Boros research package foundation"
```

---

### Task 2: Extract market normalization and plan selective archive downloads

**Files:**
- Create: `boros_research/normalize.py`
- Create: `boros_research/manifest.py`
- Create: `tests/fixtures/manifest.json`
- Create: `tests/test_normalize.py`
- Create: `tests/test_manifest.py`

**Interfaces:**
- Produces: `MarketSlug`, `parse_market_slug()`, `normalize_venue()`, `asset_from_symbol()`, `select_archive_files()`.
- Consumes: constants from `boros_research.config`.

- [ ] **Step 1: Add market-normalization tests**

```python
# tests/test_normalize.py
from boros_research.normalize import asset_from_symbol, normalize_venue, parse_market_slug


def test_parse_market_slug():
    m = parse_market_slug("155-HYPERLIQUID-HYPEUSDT-25SEP2026")
    assert m.market_id == 155
    assert m.venue == "HYPERLIQUID"
    assert m.asset == "HYPE"
    assert m.maturity.isoformat() == "2026-09-25"


def test_aliases_are_canonical():
    assert normalize_venue("Hyperliquid") == "HYPERLIQUID"
    assert asset_from_symbol("xyzGOLD") == "XAU"
    assert asset_from_symbol("ETHUSDT") == "ETH"
```

- [ ] **Step 2: Add a manifest fixture that includes supported, unsupported, raw, and combined books**

```json
[
  {"path":"market-data/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip","size":100},
  {"path":"settlement/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip","size":100},
  {"path":"underlying-apr/Hyperliquid-HYPE.ndjson.zip","size":100},
  {"path":"ohlcv/5m/155-HYPERLIQUID-HYPEUSDT-25SEP2026/2026-08.ndjson.zip","size":100},
  {"path":"order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/raw/2026-08.ndjson.zip","size":100},
  {"path":"order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip","size":100},
  {"path":"order-book/999-LIGHTER-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip","size":100}
]
```

- [ ] **Step 3: Write selective-manifest tests**

```python
# tests/test_manifest.py
import json
from pathlib import Path
from boros_research.manifest import select_archive_files


def test_selects_lightweight_data_and_only_supported_combined_books():
    files = json.loads(Path("tests/fixtures/manifest.json").read_text())
    selected = [x["path"] for x in select_archive_files(files)]

    assert any(p.startswith("market-data/") for p in selected)
    assert any(p.startswith("settlement/") for p in selected)
    assert any(p.startswith("underlying-apr/") for p in selected)
    assert any(p.startswith("ohlcv/5m/") for p in selected)
    assert "order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip" in selected
    assert not any("/raw/" in p for p in selected)
    assert not any("999-LIGHTER" in p and "order-book/" in p for p in selected)
```

- [ ] **Step 4: Run tests and verify failure**

Run: `python3 -m pytest tests/test_normalize.py tests/test_manifest.py -q`
Expected: FAIL because functions do not exist.

- [ ] **Step 5: Implement normalization**

Use a frozen dataclass:

```python
@dataclass(frozen=True)
class MarketSlug:
    market_id: int
    venue: str
    symbol: str
    asset: str
    maturity: date
    slug: str
```

`parse_market_slug()` must reject malformed slugs with `ValueError` instead of returning empty metadata. Keep the existing legacy behavior inside `boros_apr_pipeline.py` unchanged for now.

- [ ] **Step 6: Implement selective archive planning**

Rules in `select_archive_files()`:

```python
LIGHTWEIGHT_PREFIXES = (
    "market-data/",
    "settlement/",
    "underlying-apr/",
    "funding-rate/",      # accepted alias if a manifest contains it
    "ohlcv/5m/",
    "ohlcv/1d/",         # preserve existing daily analytics
)
```

For `order-book/`, accept only paths matching:

```text
order-book/{market-slug}/combined_0.0001/{month}.ndjson.zip
```

and only when `market-slug.venue in CROSSEX_VENUES`.

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/test_normalize.py tests/test_manifest.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add boros_research/normalize.py boros_research/manifest.py tests/fixtures/manifest.json tests/test_normalize.py tests/test_manifest.py
git commit -m "feat: plan CrossEx-compatible Boros archive downloads"
```

---

### Task 3: Add official market/collateral metadata caching

**Files:**
- Create: `boros_research/market_metadata.py`
- Create: `tests/fixtures/markets.json`
- Create: `tests/fixtures/assets.json`
- Create: `tests/test_market_metadata.py`

**Interfaces:**
- Produces: `MarketInfo`, `CollateralAsset`, `fetch_and_cache_market_catalog()`, `load_market_catalog()`, `load_assets()`.
- Later tasks rely on `MarketInfo.token_id`, `MarketInfo.asset`, `MarketInfo.venue`, and `MarketInfo.maturity`.

- [ ] **Step 1: Create deterministic API fixtures**

The fixture market object must include at least:

```json
{
  "marketId": 155,
  "tokenId": 3,
  "metadata": {"platformName": "Hyperliquid", "assetSymbol": "HYPE"},
  "imData": {"maturity": 1789747200, "name": "Hyperliquid HYPE 18 Sep 2026"},
  "state": "Normal"
}
```

The assets fixture must map `tokenId=3` to `USDT` and include at least one non-stable collateral asset such as HYPE.

- [ ] **Step 2: Write tests**

```python
from boros_research.market_metadata import normalize_market_catalog, normalize_assets


def test_market_catalog_keeps_collateral_identity():
    markets = normalize_market_catalog(load_fixture("markets.json"))
    m = markets[155]
    assert m.token_id == 3
    assert m.venue == "HYPERLIQUID"
    assert m.asset == "HYPE"


def test_assets_map_token_id_to_symbol():
    assets = normalize_assets(load_fixture("assets.json"))
    assert assets[3].symbol == "USDT"
```

- [ ] **Step 3: Run tests and verify failure**

Run: `python3 -m pytest tests/test_market_metadata.py -q`
Expected: FAIL.

- [ ] **Step 4: Implement the metadata client**

Use the canonical Open API base. Cache raw responses unchanged under:

```text
raw_api/markets.json
raw_api/assets.json
```

Fetch markets from `GET /markets` and assets from the current canonical assets endpoint. Treat list-shape changes as errors; do not silently return an empty catalog.

Normalize venue/asset names through `normalize.py` and parse `maturity` to UTC datetime/date once.

- [ ] **Step 5: Make pagination explicit**

If `/markets` returns `{results,total,skip}`, page until all rows are collected. Tests should simulate two pages and assert no market is lost.

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_market_metadata.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add boros_research/market_metadata.py tests/fixtures/markets.json tests/fixtures/assets.json tests/test_market_metadata.py
git commit -m "feat: cache Boros market and collateral metadata"
```

---

### Task 4: Add atomic downloader and a research download CLI

**Files:**
- Create: `boros_research/download.py`
- Create: `boros_research/cli.py`
- Create: `tests/test_download.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `download_archive_file()`, `refresh_manifest()`, CLI command `python -m boros_research.cli download`.
- Consumes: `select_archive_files()`.

- [ ] **Step 1: Write idempotency/atomicity tests with a fake fetcher**

```python
def test_existing_file_with_expected_size_is_skipped(tmp_path): ...
def test_partial_download_is_removed_on_failure(tmp_path): ...
def test_successful_download_is_atomic(tmp_path): ...
```

The fake fetcher must never make network calls.

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest tests/test_download.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement downloader behavior**

Required behavior:

```text
expected path -> if exact byte size exists: SKIP
             -> else stream/write .part
             -> verify byte size
             -> atomic rename
             -> on error delete .part only
```

Do not overwrite a correct existing raw ZIP.

- [ ] **Step 4: Add CLI**

Support:

```bash
python3 -m boros_research.cli download --refresh-manifest --workers 12
```

The command must:

1. refresh/cache `raw_boros/files.json` when requested;
2. select files via `select_archive_files()`;
3. print selected file count + compressed size split by dataset;
4. download concurrently with bounded workers;
5. exit non-zero if any download fails.

- [ ] **Step 5: Update README with a separate “Arbitrage Research” section**

Document the new command without removing existing `boros_apr_pipeline.py` instructions.

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_download.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add boros_research/download.py boros_research/cli.py tests/test_download.py README.md
git commit -m "feat: add selective historical research downloader"
```

---

### Task 5: Add Parquet storage, DuckDB catalog, and lightweight dataset normalization

**Files:**
- Create: `boros_research/datasets.py`
- Create: `boros_research/storage.py`
- Create: `tests/test_datasets.py`
- Create: `tests/test_storage.py`

**Interfaces:**
- Produces normalized Parquet datasets and DuckDB views named `market_data`, `funding_rates`, `settlements`, `ohlcv_5m`, `markets`, `assets`.
- Provides `iter_ndjson_zip()`, `write_dataset()`, `build_duckdb_catalog()`.

- [ ] **Step 1: Write NDJSON normalization tests**

Tests must assert:

- APR values stay decimal fractions (`0.05`, not `5`);
- timestamps are UTC seconds and preserved exactly;
- source path and market ID are retained;
- `underlying-apr` and `funding-rate` layouts normalize to the same schema.

- [ ] **Step 2: Write Parquet/DuckDB round-trip tests**

```python
def test_parquet_roundtrip_and_duckdb_view(tmp_path):
    # write two rows to partitioned parquet
    # build catalog
    # query SELECT count(*), max(mid_apr) FROM market_data
    # assert values
```

- [ ] **Step 3: Run tests and verify failure**

Run: `python3 -m pytest tests/test_datasets.py tests/test_storage.py -q`
Expected: FAIL.

- [ ] **Step 4: Implement lightweight parsers**

Normalized field naming must use explicit units, e.g.:

```text
mid_apr
best_bid_apr
best_ask_apr
annualized_funding_apr
settlement_apr
notional_oi_collateral
```

Do not use ambiguous names such as `rate` in canonical Parquet schemas.

- [ ] **Step 5: Implement Parquet writer without pandas**

Use `pyarrow.Table` / `pyarrow.dataset.write_dataset`. Partition by a low-cardinality layout such as:

```text
market_data/asset=HYPE/year=2026/month=08/...
funding_rates/venue=HYPERLIQUID/asset=HYPE/year=2026/month=08/...
```

Avoid one file per 5-minute observation.

- [ ] **Step 6: Implement DuckDB catalog**

Create `data/boros.duckdb`, schema-version metadata, and views over Parquet globs. Re-running catalog creation must be idempotent (`CREATE OR REPLACE VIEW`).

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/test_datasets.py tests/test_storage.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add boros_research/datasets.py boros_research/storage.py tests/test_datasets.py tests/test_storage.py
git commit -m "feat: normalize Boros history into Parquet and DuckDB"
```

---

### Task 6: Parse combined order books and build the canonical 5-minute timeline

**Files:**
- Create: `boros_research/orderbook.py`
- Create: `boros_research/timeline.py`
- Create: `tests/fixtures/combined_orderbook.ndjson`
- Create: `tests/test_orderbook.py`
- Create: `tests/test_timeline.py`

**Interfaces:**
- Produces: `BookLevel`, `OrderBookSnapshot`, `parse_combined_snapshot()`, `align_snapshots_to_grid()`.

- [ ] **Step 1: Create a combined-book fixture using the official historical schema**

```json
{"timestamp":1787443200,"datetime":"2026-08-23T00:00:00Z","blockNumber":1,"long":[{"rate":0.10,"size":1000},{"rate":0.09,"size":2000}],"short":[{"rate":0.11,"size":1000},{"rate":0.12,"size":3000}]}
```

Add at least two later snapshots so staleness behavior can be tested.

- [ ] **Step 2: Write side-semantics tests**

```python
def test_combined_book_long_is_bid_and_short_is_ask():
    book = parse_combined_snapshot(...)
    assert [x.rate_apr for x in book.bids] == [0.10, 0.09]
    assert [x.rate_apr for x in book.asks] == [0.11, 0.12]
```

Invalid/non-positive size levels must be discarded; malformed side arrays must raise.

- [ ] **Step 3: Write timeline tests**

Use snapshots at `00:02` and `00:13` UTC. Assert:

- 00:05 uses 00:02, age 180 sec;
- 00:10 uses 00:02, age 480 sec;
- 00:20 uses 00:13, age 420 sec;
- a grid point whose prior snapshot is >900 sec old is marked missing/stale and carries no executable book.

- [ ] **Step 4: Run tests and verify failure**

Run: `python3 -m pytest tests/test_orderbook.py tests/test_timeline.py -q`
Expected: FAIL.

- [ ] **Step 5: Implement parser and sorting**

Normalize historical combined schema:

```text
long[]  -> bids, sort APR descending
short[] -> asks, sort APR ascending
```

Keep sizes as `size_collateral` floats in this layer; USD conversion belongs in execution.

- [ ] **Step 6: Implement fixed-grid alignment**

`align_snapshots_to_grid()` must choose only the newest snapshot at or before each 5-minute UTC grid point. Never use a future snapshot.

- [ ] **Step 7: Write normalized order-book observations to Parquet**

Represent levels as list-of-struct columns or a normalized level table keyed by `(market_id, snapshot_ts, side, level_index)`. Pick one representation and document it in `storage.py`; tests must prove it round-trips without rate/size loss.

- [ ] **Step 8: Run tests**

Run: `python3 -m pytest tests/test_orderbook.py tests/test_timeline.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add boros_research/orderbook.py boros_research/timeline.py tests/fixtures/combined_orderbook.ndjson tests/test_orderbook.py tests/test_timeline.py
git commit -m "feat: reconstruct canonical Boros combined books"
```

---

### Task 7: Materialize historical collateral USD prices

**Files:**
- Create: `boros_research/indicators.py`
- Create: `tests/test_indicators.py`

**Interfaces:**
- Produces: `materialize_asset_prices()`, `price_at_or_before()`, normalized `asset_prices` Parquet dataset.
- Consumes: market catalog + asset catalog.

- [ ] **Step 1: Write stable-collateral and token-collateral tests**

```python
def test_usdt_collateral_is_one_dollar(): ...
def test_token_collateral_uses_reference_asset_price(): ...
def test_missing_stale_price_marks_observation_unpriceable(): ...
```

Do not silently treat a non-stable token unit as USD.

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest tests/test_indicators.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement reference-market selection**

For a non-stable collateral token, find a Boros market whose underlying asset matches the collateral symbol. Fetch the official `ap` (Asset Price) indicator for that reference market.

Use the canonical indicators endpoint with:

```text
marketId={reference_market_id}
timeFrame=5m
select=ap
```

For long ranges, chunk requests so no call exceeds the documented point/export cap. Cache raw API/CSV responses under `raw_indicators/asset-price/` so a rebuild does not re-fetch unchanged history.

- [ ] **Step 4: Normalize prices to a 5-minute table**

Canonical columns:

```text
asset
timestamp
price_usd
source_market_id
source_path
```

- [ ] **Step 5: Implement price lookup policy**

Use most recent price at or before the execution grid time. If no usable price exists, mark the execution observation unpriceable. Keep the allowable price age explicit and test it; default it to the same 15-minute maximum as order-book freshness unless official indicator alignment proves stricter is required.

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_indicators.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add boros_research/indicators.py tests/test_indicators.py
git commit -m "feat: materialize historical Boros collateral USD prices"
```

---

### Task 8: Implement USD book walking and six-notional simulation

**Files:**
- Create: `boros_research/execution.py`
- Create: `tests/test_execution.py`

**Interfaces:**
- Produces: `ExecutionResult`, `walk_book()`, `simulate_notional_ladder()`.
- Consumes: normalized book levels + collateral USD price.

- [ ] **Step 1: Write exact VWAP tests**

For bids `[(0.10,1000),(0.09,2000)]` with collateral price `$1` and target `$1500`:

```text
VWAP = (1000*0.10 + 500*0.09) / 1500 = 0.0966666667
```

For asks `[(0.11,1000),(0.12,3000)]`, target `$1500`:

```text
VWAP = (1000*0.11 + 500*0.12) / 1500 = 0.1133333333
```

- [ ] **Step 2: Write insufficient-depth test**

Target `$5000` against only `$3000` visible depth must return:

```python
result.depth_sufficient is False
result.vwap_apr is None
result.filled_usd == 3000
```

Never extrapolate the last rate.

- [ ] **Step 3: Write token-collateral conversion test**

With `size_collateral=2 HYPE` and `price_usd=50`, visible USD depth is `$100`, not `2`.

- [ ] **Step 4: Run and verify failure**

Run: `python3 -m pytest tests/test_execution.py -q`
Expected: FAIL.

- [ ] **Step 5: Implement execution result model**

```python
@dataclass(frozen=True)
class ExecutionResult:
    target_usd: float
    filled_usd: float
    depth_sufficient: bool
    vwap_apr: float | None
    top_apr: float | None
    impact_apr: float | None
    levels_used: int
```

Define impact direction consistently as deterioration from the top-of-book rate:

- short-fixed/bid: `top_bid - vwap_bid` (>=0 when worse);
- long-fixed/ask: `vwap_ask - top_ask` (>=0 when worse).

- [ ] **Step 6: Implement the notional ladder**

`simulate_notional_ladder(book, price_usd, NOTIONALS_USD)` returns both bid-side and ask-side `ExecutionResult` for every configured notional.

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/test_execution.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add boros_research/execution.py tests/test_execution.py
git commit -m "feat: simulate notional-specific Boros execution"
```

---

### Task 9: Reconstruct directed cross-venue executable opportunities

**Files:**
- Create: `boros_research/opportunities.py`
- Create: `tests/test_opportunities.py`

**Interfaces:**
- Produces: `OpportunityKey`, `build_market_groups()`, `build_executable_opportunities()` and `executable_opportunities` Parquet dataset.
- Consumes: `MarketInfo`, aligned book simulations, notionals.

- [ ] **Step 1: Write grouping tests**

Two markets may pair only when they share:

```text
asset + maturity + token_id
```

and both venues are CrossEx-compatible.

Assert that:

- HYPE/HL/USDT collateral and HYPE/Bybit/USDT collateral pair;
- same asset/maturity but different `token_id` do not pair;
- HYPE vs ETH do not pair;
- different maturity does not pair;
- Lighter is excluded from Phase 1 executable pair reconstruction.

- [ ] **Step 2: Write directed-spread test**

Given:

```text
HL bid VWAP   = 0.109
Bybit ask VWAP = 0.062
```

assert:

```text
short HL / long Bybit executable_spread_apr = 0.047
```

The reverse direction must be computed separately from the reverse bid/ask, not by negating this value.

- [ ] **Step 3: Write invalid-leg tests**

If either leg is stale, unpriceable, or lacks depth for that notional, the opportunity row must exist only as a diagnostic invalid row or be excluded from the executable table with an explicit reason table. Choose one convention and test it. Recommended: keep a row with `fully_executable=false` and `invalid_reason` so coverage analysis is possible.

- [ ] **Step 4: Run and verify failure**

Run: `python3 -m pytest tests/test_opportunities.py -q`
Expected: FAIL.

- [ ] **Step 5: Implement opportunity schema**

At minimum:

```text
timestamp
asset
maturity
dte_days
token_id
short_market_id
short_venue
long_market_id
long_venue
notional_usd
short_bid_vwap_apr
long_ask_vwap_apr
executable_spread_apr
top_of_book_spread_apr
short_impact_apr
long_impact_apr
fully_executable
invalid_reason
short_snapshot_age_sec
long_snapshot_age_sec
```

- [ ] **Step 6: Write partitioned Parquet output**

Partition primarily by `asset` and date/month; keep `notional_usd` as a column unless profiling proves partitioning by notional beneficial.

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/test_opportunities.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add boros_research/opportunities.py tests/test_opportunities.py
git commit -m "feat: reconstruct executable Boros cross-venue spreads"
```

---

### Task 10: Add end-to-end build orchestration and data-quality reporting

**Files:**
- Create: `boros_research/validation.py`
- Create: `tests/test_validation.py`
- Modify: `boros_research/cli.py`
- Modify: `README.md`
- Create: `.github/workflows/test.yml`

**Interfaces:**
- Produces CLI `python -m boros_research.cli build` and a deterministic quality report.

- [ ] **Step 1: Write validation-report test**

Input a small synthetic build result and assert report contains:

```text
source_files_expected
source_files_present
parse_failures
stale_observations
missing_observations
unpriceable_observations
market_group_count
opportunity_row_count
depth_sufficient_rate_by_notional
coverage_start
coverage_end
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m pytest tests/test_validation.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement validation report**

Write both human-readable stdout and machine-readable JSON at:

```text
data/build_report.json
```

A schema-breaking parser error must make the command exit non-zero. Normal data gaps (stale/missing book) are reported, not treated as parser crashes.

- [ ] **Step 4: Implement end-to-end build command**

Support:

```bash
python3 -m boros_research.cli build --refresh-manifest --workers 12
```

Pipeline order:

```text
refresh/select/download
-> fetch/cache market + asset metadata
-> normalize lightweight history
-> parse/resample combined books
-> materialize collateral prices
-> simulate six notionals
-> reconstruct directed opportunities
-> write Parquet
-> rebuild DuckDB catalog
-> emit quality report
```

A second run with no upstream changes must skip downloads and produce equivalent derived row counts.

- [ ] **Step 5: Add CI**

`.github/workflows/test.yml` should:

```yaml
name: Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install -e '.[dev]'
      - run: python -m pytest -q
      - run: python build_site_data.py
```

No live Boros downloads in CI.

- [ ] **Step 6: Document Codex Boros MCP setup as development-only**

Add to README:

```toml
[mcp_servers.boros]
command = "npx"
args = ["-y", "@pendle/boros-mcp"]
```

State explicitly that the production/history pipeline does not depend on MCP.

- [ ] **Step 7: Run the full test suite and legacy site build**

Run:

```bash
python3 -m pytest -q
python3 build_site_data.py
```

Expected: all tests PASS and existing site data rebuild succeeds.

- [ ] **Step 8: Commit**

```bash
git add boros_research/validation.py boros_research/cli.py tests/test_validation.py README.md .github/workflows/test.yml
git commit -m "feat: add end-to-end historical arbitrage build"
```

---

### Task 11: Add HYPE Hyperliquid ↔ Bybit regression guard and final Phase 1 verification

**Files:**
- Create: `tests/fixtures/hype_hl_bybit_market_data.json`
- Create: `tests/test_hype_regression.py`
- Modify: `README.md`

**Interfaces:**
- Produces a public-history regression check guarding venue identity and bid/ask direction.

- [ ] **Step 1: Add a deterministic HYPE top-of-book regression fixture**

Use these official historical rows at `2026-08-23T02:00:00Z`:

```json
{
  "timestamp": 1787450400,
  "hyperliquid": {
    "bestBid": 0.2903243588187977,
    "bestAsk": 0.29342477878590584
  },
  "bybit": {
    "bestBid": 0.07540591166979149,
    "bestAsk": 0.0791763324884592
  }
}
```

Expected executable top-of-book direction `short Hyperliquid / long Bybit`:

```text
0.2903243588187977 - 0.0791763324884592
= 0.2111480263303385
= 21.1148026330 percentage points annualized
```

- [ ] **Step 2: Write regression test**

```python
def test_hype_hl_bybit_direction_regression():
    spread = 0.2903243588187977 - 0.0791763324884592
    assert spread == pytest.approx(0.2111480263303385)
```

Then exercise the actual opportunity helper rather than only the arithmetic constant. The test must fail if venue identity or bid/ask side is swapped.

- [ ] **Step 3: Add a synthetic combined-book regression for `$2k`**

Create two deterministic books whose walked VWAPs are exactly `0.109` and `0.062`, then assert the engine returns a `0.047` executable spread at `$2k`. This protects the notional walker independent of top-of-book history.

- [ ] **Step 4: Run all tests**

Run: `python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Run a real local Phase 1 build**

Run:

```bash
python3 -m boros_research.cli build --refresh-manifest --workers 12
```

Verify the final report shows:

- combined `0.0001` order books only for configured CrossEx-compatible venues;
- six notional levels present;
- stale books excluded after 900 seconds;
- no extrapolated executions when depth is insufficient;
- at least one HYPE Hyperliquid/Bybit directed structure if archive coverage contains matching same-collateral/maturity markets;
- non-zero Parquet rows and queryable DuckDB views.

- [ ] **Step 6: Verify legacy behavior**

Run the existing lightweight path against an already-downloaded/small fixture dataset if available:

```bash
python3 boros_apr_pipeline.py clean
python3 build_site_data.py
```

If full raw data is not present locally, `python3 build_site_data.py` plus the test suite is the minimum compatibility gate.

- [ ] **Step 7: Document Phase 1 completion and deferred work**

README must state that Phase 1 does **not** yet provide:

```text
historical percentiles/grades
persistence episodes
live Arbitrage with CrossEx integration
notifications
trade execution
```

Those belong to later plans.

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures/hype_hl_bybit_market_data.json tests/test_hype_regression.py README.md
git commit -m "test: add HYPE arbitrage regression coverage"
```

---

## Final Verification Checklist

Run all of the following before declaring Phase 1 complete:

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
python3 build_site_data.py
python3 -m boros_research.cli build --refresh-manifest --workers 12
```

Inspect `data/build_report.json` and run DuckDB smoke queries:

```sql
SELECT count(*) FROM market_data;
SELECT count(*) FROM funding_rates;
SELECT count(*) FROM executable_opportunities;
SELECT notional_usd, avg(CASE WHEN fully_executable THEN 1.0 ELSE 0.0 END)
FROM executable_opportunities
GROUP BY 1 ORDER BY 1;
```

Expected properties:

- raw ZIP files unchanged and repeat downloads skipped;
- current official archive naming `underlying-apr` works, with `funding-rate` tolerated if encountered;
- combined-book `long` is treated as bid and `short` as ask;
- same asset + maturity + collateral token is required for cross-venue pairing;
- `$1k/$2k/$5k/$10k/$25k/$50k` simulations are present;
- insufficient depth never produces a fabricated VWAP;
- stale >15m books never produce executable opportunities;
- existing APR site still builds;
- no dependency on, import from, or modification of `Arbitrage with CrossEx` exists.

## Handoff to Phase 2

Do not implement scoring while executing this plan. After Phase 1 data has been built and inspected, create a separate Phase 2 plan for:

- contract / structural / pair / asset percentile cohorts;
- confidence/fallback logic;
- rolling 30d/90d distributions;
- robust z-scores;
- funding-context features;
- p90/p95/p99 persistence episodes.
