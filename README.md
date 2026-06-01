# Boros APR Analysis

This workspace downloads Pendle Boros historical data and prepares APR-focused
datasets comparing Boros implied APR with realized exchange funding APR.

Source: https://historical-data.boros.finance/index.html

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
