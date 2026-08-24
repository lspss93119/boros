const state = {
  data: null,
  filtered: [],
  selectedMarket: null,
  chart: new URLSearchParams(window.location.search).get("chart") === "settlement" ? "settlement" : "apr",
  lens: "all",
  lang: new URLSearchParams(window.location.search).get("lang") === "zh" ? "zh" : "en",
};

const el = {
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  languageToggle: document.getElementById("languageToggle"),
  exchangeLabel: document.getElementById("exchangeLabel"),
  assetLabel: document.getElementById("assetLabel"),
  maturityLabel: document.getElementById("maturityLabel"),
  lensAll: document.getElementById("lensAll"),
  lensPaid: document.getElementById("lensPaid"),
  lensReceived: document.getElementById("lensReceived"),
  exchange: document.getElementById("exchangeSelect"),
  asset: document.getElementById("assetSelect"),
  maturity: document.getElementById("maturitySelect"),
  kpis: document.getElementById("kpis"),
  mapTitle: document.getElementById("mapTitle"),
  mapSubtitle: document.getElementById("mapSubtitle"),
  forwardRealizedNote: document.getElementById("forwardRealizedNote"),
  marketMeta: document.getElementById("marketMeta"),
  marketTitle: document.getElementById("marketTitle"),
  tabApr: document.getElementById("tabApr"),
  tabSettlement: document.getElementById("tabSettlement"),
  tabOhlcv: document.getElementById("tabOhlcv"),
  mainChart: document.getElementById("mainChart"),
  scatterChart: document.getElementById("scatterChart"),
  crossTitle: document.getElementById("crossTitle"),
  crossSubtitle: document.getElementById("crossSubtitle"),
  crossAssetLabel: document.getElementById("crossAssetLabel"),
  crossAsset: document.getElementById("crossAssetSelect"),
  crossChart: document.getElementById("crossChart"),
  crossNote: document.getElementById("crossNote"),
};

const colors = {
  ink: "#16211c",
  muted: "#66716a",
  line: "#d9ded6",
  green: "#087f5b",
  red: "#b42318",
  gold: "#a46a00",
  blue: "#265d97",
  violet: "#6b4db3",
};

const pct = (v, digits = 2) => (v == null ? "n/a" : `${(v * 100).toFixed(digits)}%`);
const bps = (v, digits = 0) => (v == null ? "n/a" : `${(v * 10000).toFixed(digits)}`);
const num = (v, digits = 0) => {
  if (v == null || !Number.isFinite(v)) return "n/a";
  return Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(v);
};
const finite = values => values.filter(v => v != null && Number.isFinite(v));
const avg = values => {
  const vals = finite(values);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
};
const aprToFundingBps = (apr, settlementsPerDay) => {
  if (apr == null || !settlementsPerDay) return null;
  return apr * 10000 / (365 * settlementsPerDay);
};
const byMarket = market => state.data.markets.find(m => m.summary.market === market);
const marketGapSum = market => {
  const values = finite((market.series || []).map(row => row.fb));
  return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
};
let tooltipEl = null;
let focusEl = null;

const copy = {
  en: {
    pageTitle: "Boros Historical APR Explorer",
    pageSubtitle: "Explore Boros implied APR against forward realized funding: what the market priced versus what funding actually delivered.",
    switchLanguage: "繁體中文",
    exchange: "Exchange",
    asset: "Asset",
    expiration: "Expiration",
    allExchanges: "All exchanges",
    allAssets: "All assets",
    allExpirations: "All expirations",
    all: "All",
    paidMore: "Paid More",
    receivedMore: "Received More",
    markets: "Markets",
    avgAbsGapSum: "Avg Abs Gap Sum",
    paidMoreThanReceived: "Paid More Than Received",
    receivedMoreThanPaid: "Received More Than Paid",
    dateRange: "Date Range",
    marketMap: "Market Map",
    mapSubtitle: "Average implied APR versus forward realized APR for the filtered markets.",
    forwardRealizedNote: "Forward realized APR is the actual exchange funding APR averaged from each Boros observation date through that market's expiry.",
    market: "Market",
    loading: "Loading...",
    expired: "expired",
    expires: "expires",
    apr: "APR",
    impliedVsForward: "Implied vs forward realized",
    gap: "Gap",
    ohlcv: "OHLCV",
    implied: "Implied",
    forwardRealized: "Forward realized",
    settlement: "Implied vs actual settlement",
    actualSettlement: "Actual settlement",
    settlementApr: "Actual settlement APR",
    cumulativeDiffBps: "Cumulative diff",
    impliedFundingBps: "Implied funding/period",
    settlementFundingBps: "Actual settlement funding/period",
    settlementFrequency: "Settlements/day",
    settlementPeriods: "Settlement periods in day",
    forwardRealizedApr: "Forward realized APR",
    impliedApr: "Implied APR",
    avgImplied: "Avg implied",
    avgForwardRealized: "Avg forward realized",
    avgGap: "Avg gap",
    exchangeAsset: "Exchange / Asset",
    impliedMinusForward: "Implied minus forward realized",
    days: "Days",
    open: "Open",
    high: "High",
    low: "Low",
    close: "Close",
    volume: "Volume",
    insufficientData: "Insufficient data",
    nearRealized: "Near realized",
    paidMoreLabel: "Paid more than realized",
    receivedMoreLabel: "Received more than paid",
    crossTitle: "Cross-Exchange Implied APR",
    crossSubtitle: "Compare the stitched historical implied APR of one underlying asset across all exchanges. Independent of the filters above.",
    crossNote: "Each line stitches all of an exchange's markets for this asset into one continuous series, using the market closest to expiry on each date (markets sharing the same expiry are averaged).",
    underlyingAsset: "Underlying asset",
  },
  zh: {
    pageTitle: "Boros 歷史 APR 看板",
    pageSubtitle: "查看 Boros 隱含 APR 與遠期實際資金費率 APR 的對比：市場定價與最終實際資金費率的差異。",
    switchLanguage: "English",
    exchange: "交易所",
    asset: "資產",
    expiration: "到期日",
    allExchanges: "全部交易所",
    allAssets: "全部資產",
    allExpirations: "全部到期日",
    all: "全部",
    paidMore: "支付更多",
    receivedMore: "收到更多",
    markets: "市場數",
    avgAbsGapSum: "平均絕對 Gap 總和",
    paidMoreThanReceived: "支付多於實際",
    receivedMoreThanPaid: "實際多於支付",
    dateRange: "日期範圍",
    marketMap: "市場地圖",
    mapSubtitle: "篩選後市場的平均隱含 APR 與遠期實際 APR 對比。",
    forwardRealizedNote: "遠期實際 APR 指從每個 Boros 觀察日到該市場到期日之間，交易所實際資金費率 APR 的平均值。",
    market: "市場",
    loading: "載入中...",
    expired: "已到期",
    expires: "到期日",
    apr: "APR",
    impliedVsForward: "隱含 vs 遠期實際",
    gap: "Gap",
    ohlcv: "OHLCV",
    implied: "隱含",
    forwardRealized: "遠期實際",
    settlement: "隱含 vs 實際結算",
    actualSettlement: "實際結算",
    settlementApr: "實際結算 APR",
    cumulativeDiffBps: "累計差值",
    impliedFundingBps: "隱含每期資金費",
    settlementFundingBps: "實際結算每期資金費",
    settlementFrequency: "每日結算次數",
    settlementPeriods: "當日結算期數",
    forwardRealizedApr: "遠期實際 APR",
    impliedApr: "隱含 APR",
    avgImplied: "平均隱含",
    avgForwardRealized: "平均遠期實際",
    avgGap: "平均 Gap",
    exchangeAsset: "交易所 / 資產",
    impliedMinusForward: "隱含減遠期實際",
    days: "天數",
    open: "開盤",
    high: "最高",
    low: "最低",
    close: "收盤",
    volume: "成交量",
    insufficientData: "資料不足",
    nearRealized: "接近實際",
    paidMoreLabel: "支付高於實際",
    receivedMoreLabel: "實際高於支付",
    crossTitle: "跨交易所隱含 APR",
    crossSubtitle: "比較同一標的資產在各交易所拼接後的歷史隱含 APR，不受上方篩選影響。",
    crossNote: "每條線將該交易所此資產的所有市場拼接為一條連續序列：每個日期取距到期最近的市場（相同到期日的市場取平均）。",
    underlyingAsset: "標的資產",
  },
};

const t = key => copy[state.lang][key] || copy.en[key] || key;

const exchangeColors = {
  Binance: colors.gold,
  Bybit: colors.red,
  Gate: colors.blue,
  Hyperliquid: colors.green,
  KuCoin: "#0b7285",
  Lighter: colors.violet,
  OKX: colors.ink,
};
const exchangePalette = [colors.green, colors.gold, colors.blue, colors.violet, colors.red, colors.ink, "#0b7285", "#c2255c"];
const exchangeColor = (name, index) => exchangeColors[name] || exchangePalette[index % exchangePalette.length];

function buildCrossSeries(asset) {
  const markets = state.data.markets.filter(m => m.summary.asset === asset);
  const exchanges = [...new Set(markets.map(m => m.summary.exchange))].sort();
  const dateSet = new Set();
  const perExchange = new Map();
  markets.forEach(market => {
    const { exchange, maturity } = market.summary;
    if (!perExchange.has(exchange)) perExchange.set(exchange, new Map());
    const days = perExchange.get(exchange);
    (market.series || []).forEach(row => {
      if (row.i == null) return;
      dateSet.add(row.d);
      const current = days.get(row.d);
      if (!current || maturity < current.maturity) {
        days.set(row.d, { maturity, sum: row.i, count: 1 });
      } else if (maturity === current.maturity) {
        current.sum += row.i;
        current.count += 1;
      }
    });
  });
  const rows = [...dateSet].sort().map(d => {
    const row = { d };
    exchanges.forEach((exchange, index) => {
      const entry = perExchange.get(exchange).get(d);
      row[`e${index}`] = entry ? entry.sum / entry.count : null;
    });
    return row;
  });
  return { rows, exchanges };
}

function populateCrossAssets() {
  const assets = [...new Set(state.data.markets.map(m => m.summary.asset))].sort();
  const selected = el.crossAsset.value || (assets.includes("BTC") ? "BTC" : assets[0]);
  el.crossAsset.innerHTML = assets.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join("");
  el.crossAsset.value = assets.includes(selected) ? selected : assets[0];
}

function renderCross() {
  const asset = el.crossAsset.value;
  if (!asset) return;
  const { rows, exchanges } = buildCrossSeries(asset);
  const specs = exchanges.map((name, index) => ({
    key: `e${index}`,
    label: name,
    color: exchangeColor(name, index),
    scale: 100,
  }));
  drawLineChart(el.crossChart, rows, specs, { yLabel: t("apr"), suffix: "%", dateKey: "d" });
}

function gapClass(value) {
  if (value == null || Math.abs(value) < 0.0005) return "neutral";
  return value > 0 ? "positive-gap" : "negative-gap";
}

function gapLabel(value) {
  if (value == null) return t("insufficientData");
  if (Math.abs(value) < 0.0005) return t("nearRealized");
  return value > 0 ? t("paidMoreLabel") : t("receivedMoreLabel");
}

function populateFilterOptions() {
  const markets = state.data.markets.map(m => m.summary);
  const exchanges = [...new Set(markets.map(m => m.exchange))].sort();
  const assets = [...new Set(markets.map(m => m.asset))].sort();
  const maturities = [...new Set(markets.map(m => m.maturity))].sort();
  const selected = {
    exchange: el.exchange.value || "all",
    asset: el.asset.value || "all",
    maturity: el.maturity.value || "all",
  };
  el.exchange.innerHTML = `<option value="all">${t("allExchanges")}</option>${exchanges.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join("")}`;
  el.asset.innerHTML = `<option value="all">${t("allAssets")}</option>${assets.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join("")}`;
  el.maturity.innerHTML = `<option value="all">${t("allExpirations")}</option>${maturities.map(x => `<option value="${escapeHtml(x)}">${escapeHtml(x)}</option>`).join("")}`;
  el.exchange.value = exchanges.includes(selected.exchange) ? selected.exchange : "all";
  el.asset.value = assets.includes(selected.asset) ? selected.asset : "all";
  el.maturity.value = maturities.includes(selected.maturity) ? selected.maturity : "all";
}

function applyLanguage() {
  document.documentElement.lang = state.lang === "zh" ? "zh-TW" : "en";
  el.pageTitle.textContent = t("pageTitle");
  el.pageSubtitle.textContent = t("pageSubtitle");
  el.languageToggle.textContent = t("switchLanguage");
  el.exchangeLabel.textContent = t("exchange");
  el.assetLabel.textContent = t("asset");
  el.maturityLabel.textContent = t("expiration");
  el.lensAll.textContent = t("all");
  el.lensPaid.textContent = t("paidMore");
  el.lensReceived.textContent = t("receivedMore");
  el.mapTitle.textContent = t("marketMap");
  el.mapSubtitle.textContent = t("mapSubtitle");
  el.forwardRealizedNote.textContent = t("forwardRealizedNote");
  el.tabApr.textContent = t("impliedVsForward");
  el.tabSettlement.textContent = t("settlement");
  el.tabOhlcv.textContent = t("ohlcv");
  el.crossTitle.textContent = t("crossTitle");
  el.crossSubtitle.textContent = t("crossSubtitle");
  el.crossNote.textContent = t("crossNote");
  el.crossAssetLabel.textContent = t("underlyingAsset");
  populateFilterOptions();
  populateCrossAssets();
}

function setupControls() {
  applyLanguage();
  document.querySelectorAll(".chart-tabs button").forEach(button => {
    button.classList.toggle("active", button.dataset.chart === state.chart);
  });
  [el.exchange, el.asset, el.maturity].forEach(node => {
    node.addEventListener("input", () => {
      render();
    });
  });
  el.crossAsset.addEventListener("input", () => {
    renderCross();
  });
  document.querySelectorAll(".segmented button").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".segmented button").forEach(b => b.classList.remove("active"));
      button.classList.add("active");
      state.lens = button.dataset.lens;
      render();
    });
  });
  document.querySelectorAll(".chart-tabs button").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".chart-tabs button").forEach(b => b.classList.remove("active"));
      button.classList.add("active");
      state.chart = button.dataset.chart;
      const url = new URL(window.location.href);
      if (state.chart === "apr") {
        url.searchParams.delete("chart");
      } else {
        url.searchParams.set("chart", state.chart);
      }
      window.history.replaceState(null, "", url);
      renderMarket();
    });
  });
  el.languageToggle.addEventListener("click", () => {
    state.lang = state.lang === "en" ? "zh" : "en";
    const url = new URL(window.location.href);
    if (state.lang === "zh") {
      url.searchParams.set("lang", "zh");
    } else {
      url.searchParams.delete("lang");
    }
    window.history.replaceState(null, "", url);
    applyLanguage();
    render();
  });
}

function applyFilters() {
  const exchange = el.exchange.value;
  const asset = el.asset.value;
  const maturity = el.maturity.value;
  let rows = state.data.markets.slice();
  rows = rows.filter(market => {
    const m = market.summary;
    const gapSum = marketGapSum(market);
    if (exchange !== "all" && m.exchange !== exchange) return false;
    if (asset !== "all" && m.asset !== asset) return false;
    if (maturity !== "all" && m.maturity !== maturity) return false;
    if (state.lens === "paid" && !(gapSum > 0)) return false;
    if (state.lens === "received" && !(gapSum < 0)) return false;
    return true;
  });
  rows.sort((a, b) => Math.abs(marketGapSum(b) ?? 0) - Math.abs(marketGapSum(a) ?? 0));
  state.filtered = rows;
  if (!state.filtered.some(m => m.summary.market === state.selectedMarket)) {
    state.selectedMarket = state.filtered[0]?.summary.market || state.data.markets[0]?.summary.market;
  }
}

function renderKpis() {
  const rows = state.filtered;
  const summaries = rows.map(m => m.summary);
  const gapSums = rows.map(marketGapSum);
  const paidMore = rows.filter(m => (marketGapSum(m) ?? 0) > 0);
  const receivedMore = rows.filter(m => (marketGapSum(m) ?? 0) < 0);
  const largestGap = rows.slice().sort((a, b) => Math.abs(marketGapSum(b) ?? 0) - Math.abs(marketGapSum(a) ?? 0))[0]?.summary;
  el.kpis.innerHTML = [
    [t("markets"), rows.length],
    [t("avgAbsGapSum"), `${bps(avg(gapSums.map(v => Math.abs(v ?? 0))))} bps`],
    [t("paidMoreThanReceived"), paidMore.length],
    [t("receivedMoreThanPaid"), receivedMore.length],
    [t("dateRange"), `${state.data.dateMin} to ${state.data.dateMax}`],
  ].map(([label, value]) => `<div class="kpi"><b>${value}</b><span>${label}</span></div>`).join("");
  if (largestGap) {
    document.title = `${t("pageTitle")} · ${largestGap.asset}`;
  }
}

function renderMarket() {
  const market = byMarket(state.selectedMarket);
  if (!market) return;
  const m = market.summary;
  el.marketMeta.textContent = `${m.exchange} / ${m.asset} / ${t(m.matured ? "expired" : "expires")} ${m.maturity}`;
  el.marketTitle.textContent = m.market;
  drawSelectedChart(market);
}

function render() {
  applyFilters();
  renderKpis();
  drawScatter();
  renderMarket();
  renderCross();
}

function drawSelectedChart(market) {
  if (state.chart === "apr") {
    drawLineChart(el.mainChart, market.series, [
      { key: "i", label: t("implied"), color: colors.green, scale: 100 },
      { key: "rf", label: t("forwardRealized"), color: colors.gold, scale: 100 },
    ], { yLabel: t("apr"), suffix: "%", dateKey: "d" });
  } else if (state.chart === "settlement") {
    drawSettlementChart(el.mainChart, market.series, market.summary);
  } else {
    drawOhlcv(el.mainChart, market.ohlcv);
  }
}

function drawScatter() {
  const rows = state.filtered.map(m => m.summary).filter(m => m.avgImplied != null && m.avgRealizedForward != null);
  const canvas = el.scatterChart;
  const ctx = setupCanvas(canvas);
  const box = chartBox(canvas, 80, 30, 52, 30);
  const vals = rows.flatMap(r => [r.avgImplied * 100, r.avgRealizedForward * 100]);
  const [min, max] = domain(vals);
  clear(ctx, canvas);
  axes(ctx, box, min, max, min, max, t("forwardRealizedApr"), t("impliedApr"), "%", {
    xTickFormatter: value => `${value.toFixed(1)}%`,
  });
  line(ctx, box.x(min, min, max), box.y(min, min, max), box.x(max, min, max), box.y(max, min, max), colors.line, 1.5);
  const hoverPoints = [];
  rows.forEach(m => {
    const active = m.market === state.selectedMarket;
    const x = box.x(m.avgRealizedForward * 100, min, max);
    const y = box.y(m.avgImplied * 100, min, max);
    circle(ctx, x, y, active ? 6 : 3.5, active ? colors.violet : colors.green, active ? 1 : 0.65);
    hoverPoints.push({
      type: "point",
      x,
      y,
      radius: active ? 22 : 18,
      market: m.market,
      focus: {
        points: [{ x, y, color: active ? colors.violet : colors.green, radius: active ? 7 : 5 }],
      },
      html: tooltipHtml(m.market, [
        [t("exchangeAsset"), `${m.exchange} / ${m.asset}`],
        [t("expiration"), m.maturity],
        [t("avgImplied"), pct(m.avgImplied)],
        [t("avgForwardRealized"), pct(m.avgRealizedForward)],
        [t("avgGap"), `${bps(m.avgForwardBasis)} bps`],
      ]),
    });
  });
  setChartHover(canvas, hoverPoints);
  setChartClick(canvas, hoverPoints);
}

function drawLineChart(canvas, rows, specs, opts = {}) {
  const ctx = setupCanvas(canvas);
  clear(ctx, canvas);
  const box = chartBox(canvas, opts.compact ? 56 : 76, 30, opts.compact ? 40 : 58, 28);
  const yValues = rows.flatMap(r => specs.map(s => r[s.key] == null ? null : r[s.key] * s.scale));
  const [minY, maxY] = domain(yValues);
  axes(ctx, box, 0, Math.max(rows.length - 1, 1), minY, maxY, "", opts.yLabel, opts.suffix, {
    xTickFormatter: index => rows[Math.round(index)]?.[opts.dateKey] || "",
  });
  const hoverPoints = rows.map((r, i) => {
    const values = specs
      .filter(spec => r[spec.key] != null)
      .map(spec => [spec.label, `${(r[spec.key] * spec.scale).toFixed(opts.suffix === "%" ? 2 : 0)}${opts.suffix}`]);
    const x = box.x(i, 0, Math.max(rows.length - 1, 1));
    const points = specs
      .filter(spec => r[spec.key] != null)
      .map(spec => ({
        x,
        y: box.y(r[spec.key] * spec.scale, minY, maxY),
        color: spec.color,
      }));
    return {
      type: "x",
      x,
      threshold: Math.max(12, box.width / Math.max(rows.length, 2) / 2),
      focus: {
        x,
        top: box.top,
        bottom: box.top + box.height,
        points,
      },
      html: tooltipHtml(r[opts.dateKey] || `Point ${i + 1}`, values),
    };
  });
  specs.forEach(spec => {
    ctx.beginPath();
    let started = false;
    rows.forEach((r, i) => {
      const raw = r[spec.key];
      if (raw == null) {
        started = false;
        return;
      }
      const x = box.x(i, 0, Math.max(rows.length - 1, 1));
      const y = box.y(raw * spec.scale, minY, maxY);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.strokeStyle = spec.color;
    ctx.lineWidth = opts.compact ? 2 : 2.4;
    ctx.stroke();
  });
  legend(ctx, specs.map(s => ({ label: s.label, color: s.color })), box.left, canvas._drawHeight - 18);
  setChartHover(canvas, hoverPoints);
}

function drawSettlementChart(canvas, rows, summary = {}) {
  const ctx = setupCanvas(canvas);
  clear(ctx, canvas);
  const box = chartBox(canvas, 76, 30, 58, 88);
  const marketSettlementsPerDay = summary.settlementSamplesPerDay || null;
  const enrichedRows = [];
  let cumulativeBps = 0;
  rows.forEach(row => {
    const settlementsPerDay = marketSettlementsPerDay || row.ss || null;
    const settlementPeriods = row.ss || settlementsPerDay;
    const impliedFundingBps = aprToFundingBps(row.i, settlementsPerDay);
    const settlementFundingBps = aprToFundingBps(row.s, settlementsPerDay);
    const fundingDiffBps = impliedFundingBps == null || settlementFundingBps == null
      ? null
      : (impliedFundingBps - settlementFundingBps) * settlementPeriods;
    if (fundingDiffBps != null && Number.isFinite(fundingDiffBps)) {
      cumulativeBps += fundingDiffBps;
    }
    enrichedRows.push({
      ...row,
      settlementsPerDay,
      settlementPeriods,
      impliedFundingBps,
      settlementFundingBps,
      cumulativeSettlementDiffBps: fundingDiffBps == null ? null : cumulativeBps,
    });
  });

  const maxX = Math.max(enrichedRows.length - 1, 1);
  const aprValues = enrichedRows.flatMap(row => [row.i == null ? null : row.i * 100, row.s == null ? null : row.s * 100]);
  const cumulativeValues = enrichedRows.map(row => row.cumulativeSettlementDiffBps);
  const [minApr, maxApr] = domain(aprValues);
  const [minCum, maxCum] = domain(cumulativeValues.concat([0]));
  axes(ctx, box, 0, maxX, minApr, maxApr, "", t("apr"), "%", {
    xTickFormatter: index => enrichedRows[Math.round(index)]?.d || "",
  });
  rightAxis(ctx, box, minCum, maxCum, `${t("cumulativeDiffBps")} bps`, " bps");

  const specs = [
    { key: "i", label: t("implied"), color: colors.green },
    { key: "s", label: t("actualSettlement"), color: colors.blue },
  ];
  specs.forEach(spec => {
    ctx.beginPath();
    let started = false;
    enrichedRows.forEach((row, i) => {
      const raw = row[spec.key];
      if (raw == null) {
        started = false;
        return;
      }
      const x = box.x(i, 0, maxX);
      const y = box.y(raw * 100, minApr, maxApr);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.strokeStyle = spec.color;
    ctx.lineWidth = 2.4;
    ctx.stroke();
  });

  ctx.save();
  ctx.setLineDash([6, 4]);
  ctx.beginPath();
  let started = false;
  enrichedRows.forEach((row, i) => {
    const raw = row.cumulativeSettlementDiffBps;
    if (raw == null) {
      started = false;
      return;
    }
    const x = box.x(i, 0, maxX);
    const y = box.y(raw, minCum, maxCum);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.strokeStyle = colors.violet;
  ctx.lineWidth = 2.2;
  ctx.stroke();
  ctx.restore();

  const hoverPoints = enrichedRows.map((row, i) => {
    const x = box.x(i, 0, maxX);
    const points = [];
    if (row.i != null) points.push({ x, y: box.y(row.i * 100, minApr, maxApr), color: colors.green });
    if (row.s != null) points.push({ x, y: box.y(row.s * 100, minApr, maxApr), color: colors.blue });
    if (row.cumulativeSettlementDiffBps != null) {
      points.push({ x, y: box.y(row.cumulativeSettlementDiffBps, minCum, maxCum), color: colors.violet });
    }
    return {
      type: "x",
      x,
      threshold: Math.max(12, box.width / Math.max(enrichedRows.length, 2) / 2),
      focus: {
        x,
        top: box.top,
        bottom: box.top + box.height,
        points,
      },
      html: tooltipHtml(row.d, [
        [t("impliedApr"), pct(row.i)],
        [t("settlementApr"), pct(row.s)],
        [t("settlementFrequency"), row.settlementsPerDay ?? "n/a"],
        [t("impliedFundingBps"), row.impliedFundingBps == null ? "n/a" : `${row.impliedFundingBps.toFixed(2)} bps`],
        [t("settlementFundingBps"), row.settlementFundingBps == null ? "n/a" : `${row.settlementFundingBps.toFixed(2)} bps`],
        [t("settlementPeriods"), row.settlementPeriods ?? "n/a"],
        [`${t("cumulativeDiffBps")} bps`, row.cumulativeSettlementDiffBps == null ? "n/a" : `${row.cumulativeSettlementDiffBps.toFixed(2)} bps`],
      ]),
    };
  });

  legend(ctx, [
    { label: t("implied"), color: colors.green },
    { label: t("actualSettlement"), color: colors.blue },
    { label: t("cumulativeDiffBps"), color: colors.violet },
  ], box.left, canvas._drawHeight - 18);
  setChartHover(canvas, hoverPoints);
}

function drawBarChart(canvas, rows, key, opts) {
  const ctx = setupCanvas(canvas);
  clear(ctx, canvas);
  const box = chartBox(canvas, 76, 30, 52, 28);
  const values = rows.map(r => r[key] == null ? null : r[key] * opts.scale);
  const [minY, maxY] = domain(values.concat([0]));
  axes(ctx, box, 0, Math.max(rows.length - 1, 1), minY, maxY, "", opts.label, opts.suffix, {
    xTickFormatter: index => rows[Math.round(index)]?.d || "",
  });
  const zero = box.y(0, minY, maxY);
  const barW = Math.max(2, box.width / Math.max(rows.length, 1) * 0.72);
  const hoverPoints = [];
  values.forEach((v, i) => {
    if (v == null) return;
    const x = box.x(i, 0, Math.max(rows.length - 1, 1)) - barW / 2;
    const y = box.y(v, minY, maxY);
    ctx.fillStyle = v >= 0 ? colors.green : colors.red;
    ctx.globalAlpha = 0.78;
    ctx.fillRect(x, Math.min(y, zero), barW, Math.max(1, Math.abs(zero - y)));
    ctx.globalAlpha = 1;
    hoverPoints.push({
      type: "x",
      x: x + barW / 2,
      threshold: Math.max(10, barW),
      focus: {
        x: x + barW / 2,
        top: Math.min(y, zero),
        bottom: Math.max(y, zero),
        bar: { x, y: Math.min(y, zero), width: barW, height: Math.max(1, Math.abs(zero - y)), color: v >= 0 ? colors.green : colors.red },
      },
      html: tooltipHtml(rows[i].d, [
        [opts.label, `${v.toFixed(0)}${opts.suffix}`],
        [t("impliedApr"), pct(rows[i].i)],
        [t("forwardRealizedApr"), pct(rows[i].rf)],
      ]),
    });
  });
  setChartHover(canvas, hoverPoints);
}

function drawHistogram(canvas, values, opts) {
  const ctx = setupCanvas(canvas);
  clear(ctx, canvas);
  const scaled = finite(values).map(v => v * opts.scale);
  const [minX, maxX] = domain(scaled);
  const buckets = 18;
  const counts = Array.from({ length: buckets }, () => 0);
  scaled.forEach(v => {
    const idx = Math.max(0, Math.min(buckets - 1, Math.floor((v - minX) / (maxX - minX) * buckets)));
    counts[idx] += 1;
  });
  const box = chartBox(canvas, 76, 30, 52, 28);
  const maxCount = Math.max(...counts, 1);
  axes(ctx, box, minX, maxX, 0, maxCount, t("impliedMinusForward"), t("days"), "", {
    xTickFormatter: value => `${value.toFixed(0)} bps`,
  });
  const w = box.width / buckets - 3;
  const hoverPoints = [];
  counts.forEach((count, i) => {
    const x = box.left + i * box.width / buckets + 1.5;
    const h = count / maxCount * box.height;
    const midpoint = minX + (i + 0.5) * (maxX - minX) / buckets;
    ctx.fillStyle = midpoint >= 0 ? colors.green : colors.red;
    ctx.globalAlpha = 0.76;
    ctx.fillRect(x, box.top + box.height - h, w, h);
    ctx.globalAlpha = 1;
    const lo = minX + i * (maxX - minX) / buckets;
    const hi = minX + (i + 1) * (maxX - minX) / buckets;
    hoverPoints.push({
      type: "x",
      x: x + w / 2,
      threshold: Math.max(10, w),
      focus: {
        bar: { x, y: box.top + box.height - h, width: w, height: h, color: midpoint >= 0 ? colors.green : colors.red },
      },
      html: tooltipHtml(`${lo.toFixed(0)} to ${hi.toFixed(0)} bps`, [[t("days"), count]]),
    });
  });
  setChartHover(canvas, hoverPoints);
}

function drawOhlcv(canvas, rows) {
  const ctx = setupCanvas(canvas);
  clear(ctx, canvas);
  const box = chartBox(canvas, 76, 30, 92, 28);
  const prices = rows.flatMap(r => [r.h, r.l]).map(v => v == null ? null : v * 100);
  const [minY, maxY] = domain(prices);
  axes(ctx, box, 0, Math.max(rows.length - 1, 1), minY, maxY, "", t("impliedApr"), "%", {
    xTickFormatter: index => rows[Math.round(index)]?.d || "",
  });
  const candleW = Math.max(3, box.width / Math.max(rows.length, 1) * 0.55);
  const hoverPoints = [];
  rows.forEach((r, i) => {
    if ([r.o, r.h, r.l, r.c].some(v => v == null)) return;
    const x = box.x(i, 0, Math.max(rows.length - 1, 1));
    const yH = box.y(r.h * 100, minY, maxY);
    const yL = box.y(r.l * 100, minY, maxY);
    const yO = box.y(r.o * 100, minY, maxY);
    const yC = box.y(r.c * 100, minY, maxY);
    const up = r.c >= r.o;
    ctx.strokeStyle = up ? colors.green : colors.red;
    ctx.fillStyle = up ? colors.green : colors.red;
    line(ctx, x, yH, x, yL, ctx.strokeStyle, 1.2);
    ctx.globalAlpha = 0.78;
    ctx.fillRect(x - candleW / 2, Math.min(yO, yC), candleW, Math.max(2, Math.abs(yO - yC)));
    ctx.globalAlpha = 1;
    hoverPoints.push({
      type: "x",
      x,
      threshold: Math.max(10, candleW),
      focus: {
        x,
        top: yH,
        bottom: yL,
        bar: {
          x: x - candleW / 2,
          y: Math.min(yO, yC),
          width: candleW,
          height: Math.max(2, Math.abs(yO - yC)),
          color: up ? colors.green : colors.red,
        },
      },
      html: tooltipHtml(r.d, [
        [t("open"), pct(r.o)],
        [t("high"), pct(r.h)],
        [t("low"), pct(r.l)],
        [t("close"), pct(r.c)],
        [t("volume"), num(r.v, 0)],
      ]),
    });
  });
  const volBox = { left: box.left, top: box.top + box.height + 22, width: box.width, height: 38 };
  const maxVol = Math.max(...finite(rows.map(r => r.v)), 1);
  rows.forEach((r, i) => {
    if (r.v == null) return;
    const barW = Math.max(2, volBox.width / Math.max(rows.length, 1) * 0.6);
    const x = box.x(i, 0, Math.max(rows.length - 1, 1)) - barW / 2;
    const h = r.v / maxVol * volBox.height;
    ctx.fillStyle = colors.blue;
    ctx.globalAlpha = 0.36;
    ctx.fillRect(x, volBox.top + volBox.height - h, barW, h);
    ctx.globalAlpha = 1;
  });
  ctx.fillStyle = colors.muted;
  ctx.font = "12px system-ui";
  ctx.fillText(t("volume"), volBox.left, volBox.top - 4);
  setChartHover(canvas, hoverPoints);
}

function setupCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(320, Math.floor(rect.width * ratio));
  const h = Math.max(220, Math.floor(rect.height * ratio));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(ratio, ratio);
  canvas._drawWidth = w / ratio;
  canvas._drawHeight = h / ratio;
  return ctx;
}

function clear(ctx, canvas) {
  ctx.clearRect(0, 0, canvas._drawWidth, canvas._drawHeight);
}

function domain(values) {
  const vals = finite(values);
  if (!vals.length) return [0, 1];
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.08;
  return [min - pad, max + pad];
}

function chartBox(canvas, left, top, bottom, right) {
  const width = canvas._drawWidth - left - right;
  const height = canvas._drawHeight - top - bottom;
  return {
    left,
    top,
    width,
    height,
    x: (v, min, max) => left + (v - min) / (max - min || 1) * width,
    y: (v, min, max) => top + height - (v - min) / (max - min || 1) * height,
  };
}

function axes(ctx, box, minX, maxX, minY, maxY, xLabel, yLabel, suffix, opts = {}) {
  ctx.save();
  ctx.strokeStyle = colors.line;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(box.left, box.top);
  ctx.lineTo(box.left, box.top + box.height);
  ctx.lineTo(box.left + box.width, box.top + box.height);
  ctx.stroke();
  ctx.fillStyle = colors.muted;
  ctx.font = "12px system-ui";
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(yLabel, box.left, box.top - 5);
  for (let i = 0; i <= 4; i++) {
    const v = minY + (maxY - minY) * i / 4;
    const y = box.y(v, minY, maxY);
    line(ctx, box.left, y, box.left + box.width, y, colors.line, 1, 0.6);
    ctx.textAlign = "right";
    ctx.fillText(`${v.toFixed(suffix === "%" ? 1 : 0)}${suffix}`, box.left - 10, y + 4);
  }
  if (opts.xTickFormatter) {
    for (let i = 0; i <= 3; i++) {
      const v = minX + (maxX - minX) * i / 3;
      const x = box.x(v, minX, maxX);
      line(ctx, x, box.top + box.height, x, box.top + box.height + 4, colors.line, 1, 1);
      ctx.textAlign = i === 0 ? "left" : i === 3 ? "right" : "center";
      ctx.fillText(opts.xTickFormatter(v), x, box.top + box.height + 20);
    }
  }
  ctx.textAlign = "left";
  ctx.fillText(xLabel || "", box.left, box.top + box.height + (opts.xTickFormatter ? 38 : 20));
  ctx.restore();
}

function rightAxis(ctx, box, minY, maxY, yLabel, suffix) {
  ctx.save();
  const x = box.left + box.width;
  const span = Math.abs(maxY - minY);
  const digits = span < 5 ? 2 : span < 20 ? 1 : 0;
  ctx.strokeStyle = colors.line;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, box.top);
  ctx.lineTo(x, box.top + box.height);
  ctx.stroke();
  ctx.fillStyle = colors.muted;
  ctx.font = "12px system-ui";
  ctx.textAlign = "right";
  ctx.fillText(yLabel, x, box.top - 5);
  ctx.textAlign = "left";
  for (let i = 0; i <= 4; i++) {
    const v = minY + (maxY - minY) * i / 4;
    const y = box.y(v, minY, maxY);
    line(ctx, x, y, x + 4, y, colors.line, 1, 1);
    ctx.fillText(`${v.toFixed(digits)}${suffix}`, x + 8, y + 4);
  }
  ctx.restore();
}

function line(ctx, x1, y1, x2, y2, color, width = 1, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.restore();
}

function circle(ctx, x, y, r, color, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function legend(ctx, items, x, y) {
  let cursor = x;
  ctx.font = "12px system-ui";
  items.forEach(item => {
    ctx.fillStyle = item.color;
    ctx.fillRect(cursor, y - 9, 10, 10);
    ctx.fillStyle = colors.muted;
    ctx.fillText(item.label, cursor + 15, y);
    cursor += ctx.measureText(item.label).width + 38;
  });
}

function tooltipHtml(title, rows) {
  return `<b>${escapeHtml(title)}</b>${rows.map(([label, value]) => `
    <div><span>${escapeHtml(label)}</span><span>${escapeHtml(String(value))}</span></div>
  `).join("")}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setChartHover(canvas, points) {
  canvas._hoverPoints = points || [];
  if (canvas._hoverReady) return;
  canvas._hoverReady = true;
  canvas.classList.add("interactive");
  canvas.addEventListener("mousemove", event => handleChartHover(canvas, event));
  canvas.addEventListener("mouseleave", hideTooltip);
}

function setChartClick(canvas, points) {
  canvas._clickPoints = points || [];
  if (canvas._clickReady) return;
  canvas._clickReady = true;
  canvas.addEventListener("click", event => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const hit = findHoverHit(canvas._clickPoints || [], x, y);
    if (!hit?.market) return;
    state.selectedMarket = hit.market;
    renderMarket();
    drawScatter();
  });
}

function handleChartHover(canvas, event) {
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const hit = findHoverHit(canvas._hoverPoints || [], x, y);
  if (!hit) {
    hideTooltip();
    return;
  }
  showChartFocus(canvas, hit);
  showTooltip(hit.html, event.clientX, event.clientY);
}

function findHoverHit(points, x, y) {
  let best = null;
  let bestDist = Infinity;
  for (const point of points) {
    if (point.type === "rect") {
      if (
        x >= point.x &&
        x <= point.x + point.width &&
        y >= point.y &&
        y <= point.y + point.height
      ) {
        return point;
      }
      continue;
    }
    if (point.type === "x") {
      const dist = Math.abs(x - point.x);
      if (dist <= (point.threshold || 12) && dist < bestDist) {
        best = point;
        bestDist = dist;
      }
      continue;
    }
    const dist = Math.hypot(x - point.x, y - point.y);
    if (dist <= (point.radius || 10) && dist < bestDist) {
      best = point;
      bestDist = dist;
    }
  }
  return best;
}

function showTooltip(html, clientX, clientY) {
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.className = "chart-tooltip";
    document.body.appendChild(tooltipEl);
  }
  tooltipEl.innerHTML = html;
  tooltipEl.classList.add("visible");
  const margin = 12;
  const offset = 14;
  const width = tooltipEl.offsetWidth;
  const height = tooltipEl.offsetHeight;
  let left = clientX + offset;
  let top = clientY + offset;
  if (left + width + margin > window.innerWidth) {
    left = clientX - width - offset;
  }
  if (top + height + margin > window.innerHeight) {
    top = clientY - height - offset;
  }
  tooltipEl.style.transform = `translate(${Math.max(margin, left)}px, ${Math.max(margin, top)}px)`;
}

function hideTooltip() {
  if (!tooltipEl) return;
  tooltipEl.classList.remove("visible");
  tooltipEl.style.transform = "translate(-9999px, -9999px)";
  hideChartFocus();
}

function showChartFocus(canvas, hit) {
  const focus = hit.focus;
  if (!focus) {
    hideChartFocus();
    return;
  }
  if (!focusEl) {
    focusEl = document.createElement("div");
    focusEl.className = "chart-focus";
    document.body.appendChild(focusEl);
  }
  const rect = canvas.getBoundingClientRect();
  focusEl.style.width = `${rect.width}px`;
  focusEl.style.height = `${rect.height}px`;
  focusEl.style.transform = `translate(${rect.left}px, ${rect.top}px)`;
  const html = [];
  if (focus.x != null && focus.top != null && focus.bottom != null) {
    html.push(`<i class="chart-focus-line" style="left:${focus.x}px;top:${focus.top}px;height:${Math.max(1, focus.bottom - focus.top)}px"></i>`);
  }
  if (focus.bar) {
    html.push(`<i class="chart-focus-bar" style="left:${focus.bar.x}px;top:${focus.bar.y}px;width:${focus.bar.width}px;height:${focus.bar.height}px;border-color:${focus.bar.color};background:${hexToRgba(focus.bar.color, 0.12)}"></i>`);
  }
  (focus.points || []).forEach(point => {
    const radius = point.radius || 5;
    html.push(`<i class="chart-focus-dot" style="left:${point.x}px;top:${point.y}px;width:${radius * 2}px;height:${radius * 2}px;border-color:${point.color}"></i>`);
  });
  focusEl.innerHTML = html.join("");
  focusEl.classList.add("visible");
}

function hideChartFocus() {
  if (!focusEl) return;
  focusEl.classList.remove("visible");
  focusEl.style.transform = "translate(-9999px, -9999px)";
}

function hexToRgba(hex, alpha) {
  const clean = hex.replace("#", "");
  const bigint = parseInt(clean, 16);
  const r = (bigint >> 16) & 255;
  const g = (bigint >> 8) & 255;
  const b = bigint & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

fetch("./data/boros_apr_site_data.json")
  .then(response => {
    if (!response.ok) throw new Error(`Failed to load data: ${response.status}`);
    return response.json();
  })
  .then(data => {
    state.data = data;
    state.selectedMarket = null;
    setupControls();
    render();
    window.addEventListener("resize", () => render());
  })
  .catch(error => {
    document.body.innerHTML = `<main class="panel" style="margin:24px"><h1>Could not load site data</h1><p>${error.message}</p></main>`;
  });
