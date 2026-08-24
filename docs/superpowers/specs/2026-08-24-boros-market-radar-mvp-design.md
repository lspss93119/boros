# Boros Market Radar MVP Design

Date: 2026-08-24  
Branch: `feature/boros-arbitrage-research`  
Status: Design approved in chat; implementation not started

## 1. Goal

Add a small historical **Market Radar** that answers, at a glance:

1. For each asset, which cross-venue direction is usually best?
2. Which direction is strongest when opportunities become unusually large?
3. Which venue is usually the high-fixed-rate side and which is usually the low-fixed-rate side?
4. What minimum DTE is economically worth including in Radar research for the selected notional?

The Radar is a discovery layer. The existing Historical Arbitrage Explorer remains the drill-down research tool.

## 2. Non-goals

The MVP does **not**:

- replace the existing Historical Arbitrage Explorer;
- add a composite opportunity score;
- add a separate per-asset Radar detail page;
- add P75/P90/P99, winner-frequency, lifetime-stability, or extreme-event-source metrics to the UI;
- make live trading decisions;
- place orders or mutate accounts;
- modify `Arbitrage with CrossEx` in any way;
- delete short-DTE or extreme observations from the historical database.

## 3. User flow

```text
radar.html
  ↓
small radar payload
  ↓
one row per asset
  ↓ click normal/burst direction
arbitrage.html
  ↓
existing Historical Arbitrage Explorer
```

The Radar should let a user understand the market in roughly ten seconds without manually opening every asset, venue direction, maturity, and notional.

## 4. MVP UI

Default language: Traditional Chinese, with existing English opt-in behavior.

Primary control:

- Notional: `$10k`, `$25k`, `$50k`
- Default: `$10k`

Primary table:

| Asset | 常態最佳方向 | 90D Median | 爆發最佳方向 | 90D P95 | 常高 Venue | 常低 Venue | 有效 DTE |
|---|---|---:|---|---:|---|---|---:|
| HYPE | HYPERLIQUID → BYBIT | 4.8% | HYPERLIQUID → BYBIT | 9.7% | HYPERLIQUID | BYBIT | ≥ 9d |

Values above are illustrative only.

Clicking the normal or burst direction should deep-link into the existing `arbitrage.html` with enough state to select the asset, venue direction, notional, and an appropriate available maturity. The Explorer remains responsible for exact maturity-level study.

## 5. Data scope

Source of truth remains the existing read-only DuckDB historical research database.

Radar statistics use `executable_opportunities` / validated opportunity structures already produced by Phase 1. No new historical download is required.

Main Radar ranks only venue directions that are executable through the current CrossEx-compatible venue universe represented in the historical dataset. Other Boros data may remain available in research storage but should not displace the main Radar ranking.

Only fully executable observations with non-null executable spread may contribute to spread statistics.

## 6. DTE viability

### 6.1 Principle

Short DTE is excluded from Radar statistics when remaining economics are too small to justify a four-leg trade. This is an economic filter, not statistical outlier removal.

Historical observations remain untouched in DuckDB and remain visible in the existing Explorer.

### 6.2 Per-asset, per-notional cutoff

The cutoff is derived separately for:

```text
asset × notional
```

for `$10k`, `$25k`, and `$50k`.

There is no single global DTE cutoff.

### 6.3 Viability target

A candidate DTE is considered economically meaningful under the proxy study when both are satisfied:

```text
estimated net profit >= $50
AND
estimated holding-period return on model capital >= 1%
```

### 6.4 Two-layer economics

The study must expose two layers separately:

1. **Historical gross economics**
   - derived directly from historical executable spread, notional, and remaining time;
   - objective historical quantity.

2. **Current-model proxy economics**
   - applies current CrossEx fee/cost/capital semantics as a proxy;
   - version-stamped and clearly labelled as a present-day model applied to historical spread observations;
   - must never be presented as the actual historical cost or historical realized P&L.

No write or modification to CrossEx is allowed. Any CrossEx dependency must be read-only/reference-only.

### 6.5 Outliers

Once an observation passes the DTE cutoff, no spread outlier trimming is allowed.

Example:

```text
normal observations: 2%, 2.5%, 2.2%, 2.8%
one-day spike:      15%
```

If the 15% observation satisfies the economically valid DTE rule and is fully executable, it remains in the Radar statistics and can affect P95/burst classification.

## 7. Radar metrics

### 7.1 Normal best direction

For every asset and notional:

- filter to economically valid DTE observations;
- filter to fully executable observations;
- use the most recent 90 days as the primary window;
- aggregate each directed venue pair across eligible maturities;
- calculate the median executable spread;
- rank directions by 90D median.

The highest median is `常態最佳方向`.

Median is primary instead of arithmetic mean so isolated spikes do not redefine what is normally attractive.

No spike is deleted; it is simply represented more appropriately by the burst metric.

### 7.2 Burst best direction

Using the same eligible observation set, calculate each direction's 90D P95 executable spread.

The highest P95 is `爆發最佳方向`.

This preserves markets that are usually ordinary but occasionally generate very large executable spreads.

### 7.3 High-rate venue

Within fair comparison groups of the same:

```text
asset
+ token
+ maturity
+ canonical timestamp
+ notional
```

deduplicate venue-side quotes so repeated pair combinations do not multiply-count the same venue quote.

For each comparable group, identify the venue with the highest executable short-fixed-side rate (`short_bid_vwap_apr`).

Across the 90D window, `常高 Venue` is the venue that most often occupies that high-rate position.

A comparison group with fewer than two valid venues does not award a winner.

### 7.4 Low-rate venue

Use the same fair comparison grouping and deduplication.

Identify the venue with the lowest executable long-fixed-side rate (`long_ask_vwap_apr`).

Across the 90D window, `常低 Venue` is the venue that most often occupies that low-rate position.

A comparison group with fewer than two valid venues does not award a winner.

### 7.5 90D and lifetime

The MVP UI shows 90D results only.

Lifetime statistics may be exported as diagnostic metadata for validation, but they do not get their own UI columns in MVP. They are intended to detect obvious regime instability during testing, not to create a second ranking system.

## 8. Fairness and missing-data rules

The Radar must be conservative:

- no forward filling;
- no depth extrapolation;
- no treating missing data as zero;
- no awarding a high/low venue winner when only one valid venue exists;
- no inventing a direction when its 90D sample is insufficient;
- no combining incompatible assets, tokens, or maturities in same-timestamp venue comparison;
- no silent fallback that changes metric meaning.

If a metric is not supported by enough valid data, display `—` / unavailable rather than manufacture a result.

## 9. Payload and files

Do not add Radar data to the existing ~14.5 MiB `boros_arbitrage_site_data.json`.

Create a separate small generated payload, conceptually:

```text
site/data/boros_market_radar.json
```

and a separate static page, conceptually:

```text
site/radar.html
site/radar.js
site/radar.css   # only if needed; reuse shared styles where practical
```

The exact builder/module filenames may follow existing repository conventions.

The Radar payload should contain only aggregated MVP metrics and deep-link identity data, not full historical series.

## 10. Existing Explorer integration

The existing Historical Arbitrage Explorer remains unchanged in analytical semantics.

Radar links may require a small bounded enhancement so `arbitrage.html` can initialize its selected asset/direction/notional/maturity from URL query parameters. If a requested maturity is unavailable, selection must be deterministic and clearly defined; it must not silently choose an unrelated direction.

No redesign of the Explorer is part of this milestone.

## 11. Navigation

Add a simple navigation path among:

```text
APR Research
Market Radar
Arbitrage Research
```

The Radar should be easy to reach, but it does not have to replace the existing `arbitrage.html` URL in MVP.

## 12. Testing and acceptance criteria

At minimum, tests must prove:

1. **DTE isolation**
   - short-DTE exclusion affects Radar aggregation only;
   - original historical rows and Explorer data are unchanged.

2. **No outlier trimming**
   - an eligible extreme executable spread remains in the sample and affects P95 as expected.

3. **Normal ranking**
   - 90D median selects the intended usual-best direction.

4. **Burst ranking**
   - 90D P95 can select a different burst-best direction from the median winner.

5. **Fair venue comparison**
   - high/low venue winners are awarded only from same asset/token/maturity/timestamp/notional groups with at least two valid venues;
   - duplicated pair rows do not multiply-count a venue quote.

6. **Missing data**
   - missing/insufficient data yields unavailable, not zero or a fabricated winner.

7. **Notional independence**
   - `$10k`, `$25k`, and `$50k` may produce different DTE cutoffs and rankings.

8. **Language**
   - no `?lang=` defaults to Traditional Chinese;
   - `?lang=en` remains English.

9. **Deep-linking**
   - clicking a Radar direction opens the matching Explorer asset/direction/notional without changing analytical semantics.

10. **Preservation**
    - no mutation/trading/account code is added;
    - `Arbitrage with CrossEx` is untouched;
    - Phase 1/2/3 historical and live semantics remain unchanged.

## 13. MVP success criterion

The milestone is successful if a user can open the Radar and, without manually checking each market, answer within roughly ten seconds:

- which direction is normally strongest for each asset;
- which direction has the strongest historical tail opportunity;
- which venue tends to be the high-rate side;
- which venue tends to be the low-rate side;
- what DTE floor is being used for that asset/notional;

then click directly into the existing Explorer for detailed research.
