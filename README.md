# Boros APR Analysis

This workspace downloads Pendle Boros historical data and prepares APR-focused
datasets comparing Boros implied APR with realized exchange funding APR.

Source: https://historical-data.boros.finance/index.html

## Arbitrage Research

The separate research CLI caches immutable Boros ZIP archives and selects
lightweight history for the full archive universe plus
CrossEx-compatible `order-book/combined_0.0001` history:

```bash
python3 -m boros_research.cli download --refresh-manifest --workers 12
```

The manifest is cached at `raw_boros/files.json`, and archive paths are kept
under `raw_boros/` exactly as listed by the official manifest. Completed files
are checked by manifest byte size, so rerunning the command is idempotent.

Build the reproducible research outputs after the raw inputs are available:

```bash
python3 -m boros_research.cli build --refresh-manifest --workers 12
```

Add `--refresh-metadata` when the official market and collateral catalog cache
should be refreshed. The build reads or creates these reproducible raw/cache
inputs:

- `raw_boros/`: immutable historical ZIP archives and `files.json`
- `raw_api/`: raw Boros market and collateral metadata responses
- `raw_indicators/`: raw historical collateral-price exports

Canonical derived outputs are written to:

- `data/parquet/`
- `data/boros.duckdb`
- `data/build_report.json`

Derived Parquet and DuckDB data can be rebuilt from the raw inputs. The build
uses only `ohlcv/5m` for the canonical `ohlcv_5m` dataset; `ohlcv/1d` archives
remain raw inputs. The build is research-only and does not calculate live
orders, account state, or trading actions.

For development and schema verification, Boros MCP may be configured as:

```toml
[mcp_servers.boros]
command = "npx"
args = ["-y", "@pendle/boros-mcp"]
```

Boros MCP is a Codex development/research assistant only. The production
historical pipeline does not depend on MCP, an LLM, an agent key, or a wallet,
and it never places orders.

### Phase 1 complete

Phase 1 provides historical archive ingestion, 5-minute combined-book
reconstruction, collateral USD pricing, `$1k/$2k/$5k/$10k/$25k/$50k` book
walking, directed cross-venue executable Boros fixed-rate spreads, Parquet,
DuckDB, and build quality reports. The official historical build completed with
zero parse failures and all selected source files present.

`executable_spread_apr` is a historical Boros fixed-rate executable spread. It
is not net APR, profit, or a four-leg realized return.

Phase 1 does not provide historical percentiles or grades, persistence
episodes, live Arbitrage with CrossEx integration, notifications, or trade
execution. Those are deferred to later phases.

## Run

```bash
python3 boros_apr_pipeline.py all --workers 16
```

The default run downloads APR-relevant files:

- `market-data`: Boros implied APR snapshots
- `underlying-apr`: realized exchange funding APR
- `settlement`: on-chain settlement APR reference
- `ohlcv/1d`: daily implied APR candles and volume

Order-book depth is much larger and not needed for implied-vs-realized APR
visuals. To fetch it too:

```bash
python3 boros_apr_pipeline.py download --include-orderbook
```

## Outputs

- `raw_boros/`: original zipped NDJSON files plus `files.json`
- `processed/market_data.csv`: hourly Boros market snapshots
- `processed/underlying_apr.csv`: raw exchange funding APR observations
- `processed/settlement.csv`: raw on-chain settlement APR observations
- `processed/ohlcv_1d.csv`: daily APR candles
- `processed/market_daily_apr.csv`: daily average implied APR by market
- `processed/underlying_daily_apr.csv`: daily average realized funding APR by exchange and asset
- `processed/settlement_daily_apr.csv`: daily settlement APR by market
- `processed/apr_comparison_daily.csv`: joined implied-vs-realized comparison
- `visualizations/boros_apr_dashboard.html`: standalone dashboard
- `site/index.html`: interactive APR research website
- `site/data/boros_apr_site_data.json`: compact data file used by the website

`realizedForwardApr` in `apr_comparison_daily.csv` is the average actual
exchange funding APR from each Boros market date through that market maturity,
or through the latest available underlying funding data when the market has not
fully matured in the source history. The interactive website includes only
expired markets, so its forward realized APR comparisons are measured through
market expiry.

## Interactive Website

Build the website data after refreshing processed CSVs:

```bash
python3 build_site_data.py
```

Run it locally:

```bash
python3 -m http.server 8765
```

Then open:

```text
http://127.0.0.1:8765/site/index.html
```

The website includes only expired markets. It provides exchange/asset/expiration
filters, paid-more-vs-received-more comparisons, a market map, and selected
market drilldowns for implied-vs-forward-realized APR, implied-vs-actual-
settlement APR, cumulative settlement diff in bps, and OHLCV. The gap is
`implied APR - forward realized APR`.

## GitHub Pages Deployment

This repo is structured so GitHub Pages can publish the static site from
`site/` through GitHub Actions.

Files used for Pages:

- `.github/workflows/deploy-pages.yml`: builds `site/data/boros_apr_site_data.json` and deploys `site/`
- `site/index.html`: GitHub Pages entrypoint
- `site/app.js` and `site/styles.css`: dashboard UI
- `site/data/boros_apr_site_data.json`: compact dashboard data
- `site/.nojekyll`: prevents GitHub Pages from running Jekyll processing

To enable hosting:

1. Push this project to a GitHub repository.
2. In GitHub, open `Settings -> Pages`.
3. Set `Build and deployment -> Source` to `GitHub Actions`.
4. Push to `main` or `master`, or manually run the `Deploy GitHub Pages`
   workflow.

The deployed URL will be shown in the workflow summary and in
`Settings -> Pages`. If the repository is named `boros`, the default URL is
usually:

```text
https://<github-user>.github.io/boros/
```

If you want to use the simpler GitHub Pages branch setting instead of Actions,
copy `site/` to a `docs/` directory and set the Pages source to `Deploy from a
branch -> /docs`. The Actions setup avoids that duplicated directory.
