const DATA_URL = "./data/boros_arbitrage_site_data.json";
const NOTIONALS = [10000, 25000, 50000];

const state = {
  data: null,
  asset: "",
  direction: "",
  expiration: "",
  notional: 10000,
  view: "spread",
  lang: new URLSearchParams(window.location.search).get("lang") === "zh" ? "zh" : "en",
};

const el = {
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  languageToggle: document.getElementById("languageToggle"),
  assetLabel: document.getElementById("assetLabel"),
  directionLabel: document.getElementById("directionLabel"),
  expirationLabel: document.getElementById("expirationLabel"),
  notionalLabel: document.getElementById("notionalLabel"),
  asset: document.getElementById("assetSelect"),
  direction: document.getElementById("directionSelect"),
  expiration: document.getElementById("expirationSelect"),
  notional: document.getElementById("notionalSelect"),
  kpis: document.getElementById("kpis"),
  historyTitle: document.getElementById("historyTitle"),
  historySubtitle: document.getElementById("historySubtitle"),
  historyNote: document.getElementById("historyNote"),
  spreadTab: document.getElementById("spreadTab"),
  percentileTab: document.getElementById("percentileTab"),
  spreadChart: document.getElementById("spreadChart"),
  distributionTitle: document.getElementById("distributionTitle"),
  distributionSubtitle: document.getElementById("distributionSubtitle"),
  distributionChart: document.getElementById("distributionChart"),
  distributionMeta: document.getElementById("distributionMeta"),
  notionalTitle: document.getElementById("notionalTitle"),
  notionalSubtitle: document.getElementById("notionalSubtitle"),
  notionalComparisonBody: document.getElementById("notionalComparisonBody"),
  leaderboardTitle: document.getElementById("leaderboardTitle"),
  leaderboardSubtitle: document.getElementById("leaderboardSubtitle"),
  leaderboardBody: document.getElementById("leaderboardBody"),
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

const copy = {
  en: {
    title: "Historical Arbitrage Explorer",
    subtitle: "Static historical Boros fixed-rate spread research across exact market structures.",
    language: "繁體中文",
    asset: "Asset",
    direction: "Direction",
    expiration: "Expiration",
    notional: "Notional",
    latestSpread: "Latest Executable Spread",
    rank: "90D Historical Rank",
    threshold: "90D P95 Spread Threshold",
    samples: "90D Sample Count",
    executableRate: "Fully Executable Rate",
    historyTitle: "Executable Spread History",
    historySubtitle: "Daily aggregation only; missing UTC dates are not connected.",
    spread: "Executable Spread",
    percentile: "90D Percentile",
    historyNote: "Daily median and daily maximum use fully executable observations only.",
    distributionTitle: "Historical Distribution",
    distributionSubtitle: "90-day fully executable spread distribution.",
    notionalTitle: "Notional Comparison",
    notionalSubtitle: "All sizes use the same exact historical timestamp.",
    leaderboardTitle: "Historical Opportunity Leaderboard",
    leaderboardSubtitle: "Latest historical state, sorted by 90D rank.",
    executionStatus: "Execution Status",
    fullyExecutable: "Fully executable",
    notFullyExecutable: "Not fully executable",
    unavailable: "Unavailable",
    noData: "No matching historical structure.",
    benchmark: "Benchmark",
    dte: "DTE",
    pairFallback: "Pair fallback",
    unavailableBenchmark: "Benchmark unavailable",
    samplesLabel: "90D samples",
    latest: "Latest spread",
    median: "Daily median",
    maximum: "Daily maximum",
    p95: "P95",
    p99: "P99",
    commonTimestamp: "Common timestamp",
    noCommonTimestamp: "No common timestamp",
    assetHead: "Asset",
    directionHead: "Direction",
    expirationHead: "Expiration",
    dteHead: "DTE",
    spreadHead: "Executable Spread",
    rankHead: "90D Rank",
    sampleHead: "90D Samples",
    statusHead: "Execution Status",
    loading: "Loading historical data...",
    error: "Unable to load historical arbitrage data.",
  },
  zh: {
    title: "歷史套利研究",
    subtitle: "以精確市場結構查看 Boros 固定利率可成交利差的歷史研究。",
    language: "English",
    asset: "資產",
    direction: "方向",
    expiration: "到期日",
    notional: "名目金額",
    latestSpread: "最新可成交利差",
    rank: "90天歷史排名",
    threshold: "90天 P95 利差門檻",
    samples: "90天樣本數",
    executableRate: "完整可成交比例",
    historyTitle: "可成交利差歷史",
    historySubtitle: "僅顯示每日彙總；缺少的 UTC 日期不會被連線。",
    spread: "可成交利差",
    percentile: "90天百分位",
    historyNote: "每日中位數與每日最高值只使用完整可成交觀察。",
    distributionTitle: "歷史分布",
    distributionSubtitle: "90天完整可成交利差分布。",
    notionalTitle: "名目金額比較",
    notionalSubtitle: "三種規模使用同一個精確歷史時間戳。",
    leaderboardTitle: "歷史套利機會排行榜",
    leaderboardSubtitle: "最新歷史狀態，依 90天排名排序。",
    executionStatus: "成交狀態",
    fullyExecutable: "完整可成交",
    notFullyExecutable: "不可完整執行",
    unavailable: "不可用",
    noData: "沒有符合的歷史市場結構。",
    benchmark: "比較基準",
    dte: "DTE",
    pairFallback: "Pair fallback",
    unavailableBenchmark: "比較基準不可用",
    samplesLabel: "90天樣本",
    latest: "最新利差",
    median: "每日中位數",
    maximum: "每日最高值",
    p95: "P95",
    p99: "P99",
    commonTimestamp: "共同時間戳",
    noCommonTimestamp: "沒有共同時間戳",
    assetHead: "資產",
    directionHead: "方向",
    expirationHead: "到期日",
    dteHead: "DTE",
    spreadHead: "可成交利差",
    rankHead: "90天排名",
    sampleHead: "90天樣本",
    statusHead: "成交狀態",
    loading: "正在載入歷史資料…",
    error: "無法載入歷史套利資料。",
  },
};

const t = key => copy[state.lang][key] || copy.en[key] || key;
const fmtPercent = (value, digits = 2) => value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
const fmtRank = value => value == null || !Number.isFinite(value) ? "—" : `P${value.toFixed(1)}`;
const fmtNumber = value => value == null || !Number.isFinite(value) ? "—" : new Intl.NumberFormat("en-US").format(value);
const fmtDate = value => value || "—";
const directionKey = structure => [
  structure.shortVenue,
  structure.longVenue,
  structure.tokenId,
  structure.shortMarketId,
  structure.longMarketId,
].join("|");
const directionName = structure => `${structure.shortVenue} → ${structure.longVenue}`;
const selectedStructures = () => (state.data?.structures || []).filter(structure => {
  return structure.asset === state.asset
    && directionKey(structure) === state.direction
    && structure.maturity === state.expiration;
});
const selectedStructure = () => selectedStructures().sort((a, b) => a.id.localeCompare(b.id))[0] || null;
const selectedNotionalData = structure => structure?.notionals?.[String(state.notional)] || null;

function setOptions(select, values, selected, label = value => value) {
  select.innerHTML = "";
  values.forEach(value => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label(value);
    select.appendChild(option);
  });
  if (values.includes(selected)) select.value = selected;
  else if (values.length) {
    select.value = values[0];
  }
}

function populateFilters() {
  const structures = state.data.structures || [];
  const assets = [...new Set(structures.map(item => item.asset))].sort();
  if (!assets.includes(state.asset)) state.asset = assets[0] || "";
  setOptions(el.asset, assets, state.asset);

  const directionStructures = structures.filter(item => item.asset === state.asset);
  const directionByKey = new Map(directionStructures.map(item => [directionKey(item), item]));
  const baseDirectionCounts = directionStructures.reduce((counts, item) => {
    const base = directionName(item);
    counts.set(base, (counts.get(base) || 0) + 1);
    return counts;
  }, new Map());
  const directions = [...directionByKey.keys()].sort();
  if (!directions.includes(state.direction)) state.direction = directions[0] || "";
  setOptions(el.direction, directions, state.direction, value => {
    const structure = directionByKey.get(value);
    if (!structure) return value;
    const base = directionName(structure);
    if (baseDirectionCounts.get(base) === 1) return base;
    return `${base} · token ${structure.tokenId} · ${structure.shortMarketId}/${structure.longMarketId}`;
  });

  const expirations = [...new Set(structures
    .filter(item => item.asset === state.asset && directionKey(item) === state.direction)
    .map(item => item.maturity))].sort();
  if (!expirations.includes(state.expiration)) state.expiration = expirations[0] || "";
  setOptions(el.expiration, expirations, state.expiration);

  const matching = selectedStructures();
  const notionals = matching.length ? NOTIONALS : [];
  if (!notionals.includes(state.notional)) state.notional = notionals.includes(10000) ? 10000 : (notionals[0] || 10000);
  setOptions(el.notional, notionals, state.notional, value => `$${Number(value).toLocaleString("en-US")}`);
}

function totalExecutableRate(data) {
  const rows = data?.daily || [];
  const observations = rows.reduce((sum, row) => sum + (row.observationCount || 0), 0);
  const executable = rows.reduce((sum, row) => sum + (row.fullyExecutableCount || 0), 0);
  return observations ? executable / observations : null;
}

function renderKpis() {
  const data = selectedNotionalData(selectedStructure());
  const raw = data?.latestRaw;
  const benchmark = data?.latestBenchmark;
  const distribution = data?.distribution;
  const p95 = distribution?.windows?.["90d"]?.p95;
  const cards = [
    [t("latestSpread"), raw?.fullyExecutable ? fmtPercent(raw.executableSpreadApr) : t("notFullyExecutable")],
    [t("rank"), fmtRank(benchmark?.percentile90d)],
    [t("threshold"), fmtPercent(p95)],
    [t("samples"), fmtNumber(benchmark?.sampleCount90d)],
    [t("executableRate"), fmtPercent(totalExecutableRate(data))],
  ];
  el.kpis.innerHTML = cards.map(([label, value]) => `<div class="kpi"><b>${value}</b><span>${label}</span></div>`).join("");
}

function canvasContext(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || canvas.width;
  const height = canvas.clientHeight || canvas.height;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width, height };
}

function chartFrame(context, width, height, values, formatLabel = value => fmtPercent(value, 1)) {
  const finite = values.filter(value => value != null && Number.isFinite(value));
  const min = finite.length ? Math.min(...finite) : 0;
  const max = finite.length ? Math.max(...finite) : 1;
  const padding = { left: 52, right: 18, top: 22, bottom: 34 };
  const range = max === min ? 1 : max - min;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, width, height);
  context.strokeStyle = colors.line;
  context.fillStyle = colors.muted;
  context.font = "11px system-ui";
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + (height - padding.top - padding.bottom) * i / 4;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    const label = max - range * i / 4;
    context.fillText(formatLabel(label), 4, y + 4);
  }
  return {
    min,
    max,
    range,
    padding,
    plotWidth: width - padding.left - padding.right,
    plotHeight: height - padding.top - padding.bottom,
  };
}

function drawLine(context, rows, field, frame, color, dashed = false) {
  const { min, range, padding } = frame;
  const { plotWidth, plotHeight } = frame;
  let previous = null;
  rows.forEach((row, index) => {
    const value = row[field];
    if (value == null || !Number.isFinite(value)) {
      previous = null;
      return;
    }
    const x = padding.left + (rows.length <= 1 ? plotWidth / 2 : plotWidth * index / (rows.length - 1));
    const y = padding.top + plotHeight * (1 - (value - min) / range);
    if (previous && (!row.date || !previous.date || (Date.parse(row.date) - Date.parse(previous.date)) <= 86_400_000)) {
      context.beginPath();
      context.setLineDash(dashed ? [5, 4] : []);
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.moveTo(previous.x, previous.y);
      context.lineTo(x, y);
      context.stroke();
      context.setLineDash([]);
    }
    context.fillStyle = color;
    context.beginPath();
    context.arc(x, y, 2.5, 0, Math.PI * 2);
    context.fill();
    previous = { x, y, date: row.date };
  });
}

function drawHorizontalGuide(context, frame, width, value, color, label) {
  const { min, range, padding, plotWidth, plotHeight } = frame;
  const y = padding.top + plotHeight * (1 - (value - min) / range);
  context.beginPath();
  context.setLineDash([5, 4]);
  context.strokeStyle = color;
  context.lineWidth = 1;
  context.moveTo(padding.left, y);
  context.lineTo(width - padding.right, y);
  context.stroke();
  context.setLineDash([]);
  context.fillStyle = color;
  context.fillText(label, width - padding.right - 28, y - 4);
}

function renderSpreadChart() {
  const { context, width, height } = canvasContext(el.spreadChart);
  const data = selectedNotionalData(selectedStructure());
  const rows = data?.daily || [];
  if (!rows.length) {
    context.fillStyle = colors.muted;
    context.fillText(t("noData"), 24, 40);
    return;
  }
  const values = rows.flatMap(row => [row.dailyMedianExecutableSpreadApr, row.dailyMaxExecutableSpreadApr]);
  const frame = chartFrame(context, width, height, values);
  drawLine(context, rows, "dailyMedianExecutableSpreadApr", frame, colors.blue);
  drawLine(context, rows, "dailyMaxExecutableSpreadApr", frame, colors.gold, true);
  context.fillStyle = colors.blue;
  context.fillText(t("median"), frame.padding.left, height - 10);
  context.fillStyle = colors.gold;
  context.fillText(t("maximum"), frame.padding.left + 110, height - 10);
}

function renderPercentileChart() {
  const { context, width, height } = canvasContext(el.spreadChart);
  const data = selectedNotionalData(selectedStructure());
  const rows = (data?.daily || []).map(row => ({ ...row, percentile: row.benchmark?.percentile90d ?? null }));
  const values = [...rows.map(row => row.percentile), 95, 99].filter(value => value != null);
  if (!rows.length) {
    context.fillStyle = colors.muted;
    context.fillText(t("noData"), 24, 40);
    return;
  }
  const frame = chartFrame(context, width, height, values, value => `P${value.toFixed(0)}`);
  drawLine(context, rows, "percentile", frame, colors.violet);
  [95, 99].forEach((guide, index) => {
    drawHorizontalGuide(
      context,
      frame,
      width,
      guide,
      index ? colors.red : colors.gold,
      `P${guide}`,
    );
  });
}

function renderHistory() {
  el.spreadTab.classList.toggle("active", state.view === "spread");
  el.percentileTab.classList.toggle("active", state.view === "percentile");
  el.historyTitle.textContent = t("historyTitle");
  el.historySubtitle.textContent = t("historySubtitle");
  el.historyNote.textContent = t("historyNote");
  if (state.view === "spread") renderSpreadChart();
  else renderPercentileChart();
}

function renderDistribution() {
  const data = selectedNotionalData(selectedStructure());
  const distribution = data?.distribution;
  const { context, width, height } = canvasContext(el.distributionChart);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, width, height);
  el.distributionTitle.textContent = t("distributionTitle");
  el.distributionSubtitle.textContent = t("distributionSubtitle");
  if (!distribution) {
    context.fillStyle = colors.muted;
    context.fillText(t("unavailableBenchmark"), 24, 40);
    el.distributionMeta.textContent = t("unavailableBenchmark");
    return;
  }
  const histogram = distribution.histogram90d;
  const counts = histogram.counts || [];
  const maxCount = Math.max(...counts, 1);
  const left = 52;
  const top = 22;
  const bottom = height - 34;
  const barWidth = (width - left - 18) / Math.max(counts.length, 1);
  counts.forEach((count, index) => {
    const barHeight = (bottom - top) * count / maxCount;
    context.fillStyle = colors.blue;
    context.fillRect(left + index * barWidth, bottom - barHeight, Math.max(1, barWidth - 1), barHeight);
  });
  const min = histogram.min;
  const max = histogram.max;
  const latest = data.latestRaw?.fullyExecutable ? data.latestRaw.executableSpreadApr : null;
  const stats = distribution.windows?.["90d"] || {};
  const markers = [
    ["P50", stats.p50, colors.blue],
    ["P75", stats.p75, colors.violet],
    ["P90", stats.p90, colors.gold],
    ["P95", stats.p95, colors.red],
    ["P99", stats.p99, colors.ink],
    [t("latest"), latest, colors.green],
  ];
  if (min != null && max != null && max !== min) {
    markers.forEach(([label, value, color]) => {
      if (value == null) return;
      const x = left + (width - left - 18) * (value - min) / (max - min);
      context.strokeStyle = color;
      context.beginPath();
      context.moveTo(x, top);
      context.lineTo(x, bottom);
      context.stroke();
      context.fillStyle = color;
      context.font = "11px system-ui";
      context.fillText(`${label} ${fmtPercent(value, 1)}`, Math.min(x + 3, width - 100), 14);
    });
  }
  const benchmarkText = distribution.benchmarkLevel === "dte"
    ? `${t("benchmark")}: DTE ${distribution.dteBucket || "—"}`
    : `${t("benchmark")}: ${t("pairFallback")}`;
  el.distributionMeta.innerHTML = `<span><strong>${benchmarkText}</strong></span><span>${t("samplesLabel")}: <strong>${fmtNumber(stats.count)}</strong></span><span>${t("latest")}: <strong>${fmtPercent(latest)}</strong></span>`;
}

function renderNotionalComparison() {
  const structure = selectedStructure();
  el.notionalTitle.textContent = t("notionalTitle");
  el.notionalSubtitle.textContent = structure?.commonTimestamp
    ? `${t("commonTimestamp")}: ${new Date(structure.commonTimestamp * 1000).toISOString()}`
    : t("noCommonTimestamp");
  const rows = structure?.notionalComparison || NOTIONALS.map(notionalUsd => ({ notionalUsd, available: false }));
  el.notionalComparisonBody.innerHTML = rows.map(row => {
    const status = !row.available ? t("unavailable") : row.fullyExecutable ? t("fullyExecutable") : t("notFullyExecutable");
    const statusClass = row.available && row.fullyExecutable ? "status-valid" : "status-invalid";
    return `<tr><td>$${Number(row.notionalUsd).toLocaleString("en-US")}</td><td>${row.available && row.fullyExecutable ? fmtPercent(row.executableSpreadApr) : "—"}</td><td>${fmtRank(row.percentile90d)}</td><td>${fmtNumber(row.sampleCount90d)}</td><td class="${statusClass}">${status}</td></tr>`;
  }).join("");
}

function renderLeaderboard() {
  el.leaderboardTitle.textContent = t("leaderboardTitle");
  el.leaderboardSubtitle.textContent = t("leaderboardSubtitle");
  const rows = (state.data.leaderboard || []).filter(row => row.notionalUsd === state.notional);
  if (!rows.length) {
    el.leaderboardBody.innerHTML = `<tr><td colspan="8" class="empty-state">${t("noData")}</td></tr>`;
    return;
  }
  el.leaderboardBody.innerHTML = rows.map(row => {
    const status = row.fullyExecutable ? t("fullyExecutable") : t("notFullyExecutable");
    return `<tr data-structure-id="${row.structureId}" data-notional="${row.notionalUsd}"><td>${row.asset}</td><td>${row.shortVenue} → ${row.longVenue}</td><td>${fmtDate(row.maturity)}</td><td>${row.dteDays}</td><td>${row.fullyExecutable ? fmtPercent(row.executableSpreadApr) : "—"}</td><td>${fmtRank(row.percentile90d)}</td><td>${fmtNumber(row.sampleCount90d)}</td><td class="${row.fullyExecutable ? "status-valid" : "status-invalid"}">${status}</td></tr>`;
  }).join("");
  el.leaderboardBody.querySelectorAll("tr[data-structure-id]").forEach(row => {
    row.addEventListener("click", () => {
      const structure = state.data.structures.find(item => item.id === row.dataset.structureId);
      if (!structure) return;
      state.asset = structure.asset;
      state.direction = directionKey(structure);
      state.expiration = structure.maturity;
      state.notional = Number(row.dataset.notional);
      populateFilters();
      renderAll();
    });
  });
}

function renderLabels() {
  document.documentElement.lang = state.lang === "zh" ? "zh-Hant" : "en";
  el.pageTitle.textContent = t("title");
  el.pageSubtitle.textContent = t("subtitle");
  el.languageToggle.textContent = t("language");
  el.assetLabel.textContent = t("asset");
  el.directionLabel.textContent = t("direction");
  el.expirationLabel.textContent = t("expiration");
  el.notionalLabel.textContent = t("notional");
  ["notionalHead", "spreadHead", "rankHead", "sampleHead", "statusHead", "assetHead", "directionHead", "expirationHead", "dteHead", "leaderSpreadHead", "leaderRankHead", "leaderSampleHead", "leaderStatusHead"].forEach(id => {
    const key = id.replace("leader", "").replace("Head", "");
    if (el[id]) el[id].textContent = t(key === "asset" ? "assetHead" : key === "direction" ? "directionHead" : key === "expiration" ? "expirationHead" : key === "dte" ? "dteHead" : key === "spread" ? "spreadHead" : key === "rank" ? "rankHead" : key === "sample" ? "sampleHead" : "statusHead");
  });
}

function renderAll() {
  renderLabels();
  populateFilters();
  renderKpis();
  renderHistory();
  renderDistribution();
  renderNotionalComparison();
  renderLeaderboard();
}

async function load() {
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    renderAll();
  } catch (error) {
    document.getElementById("arbitrageApp").innerHTML = `<main><section class="panel"><p class="empty-state">${t("error")}</p></section></main>`;
    console.error("Unable to load arbitrage data", error);
  }
}

el.asset.addEventListener("change", () => { state.asset = el.asset.value; state.direction = ""; state.expiration = ""; renderAll(); });
el.direction.addEventListener("change", () => { state.direction = el.direction.value; state.expiration = ""; renderAll(); });
el.expiration.addEventListener("change", () => { state.expiration = el.expiration.value; renderAll(); });
el.notional.addEventListener("change", () => { state.notional = Number(el.notional.value); renderAll(); });
el.spreadTab.addEventListener("click", () => { state.view = "spread"; renderHistory(); });
el.percentileTab.addEventListener("click", () => { state.view = "percentile"; renderHistory(); });
el.languageToggle.addEventListener("click", () => { state.lang = state.lang === "en" ? "zh" : "en"; renderAll(); });
window.addEventListener("resize", () => { renderHistory(); renderDistribution(); });

load();
