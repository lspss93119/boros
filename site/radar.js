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

const el = {
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  languageToggle: document.getElementById("languageToggle"),
  notionalLabel: document.getElementById("notionalLabel"),
  tableTitle: document.getElementById("tableTitle"),
  tableSubtitle: document.getElementById("tableSubtitle"),
  assetHead: document.getElementById("assetHead"),
  normalDirectionHead: document.getElementById("normalDirectionHead"),
  medianSpreadHead: document.getElementById("medianSpreadHead"),
  burstDirectionHead: document.getElementById("burstDirectionHead"),
  p95Head: document.getElementById("p95Head"),
  highVenueHead: document.getElementById("highVenueHead"),
  lowVenueHead: document.getElementById("lowVenueHead"),
  dteHead: document.getElementById("dteHead"),
  notesTitle: document.getElementById("notesTitle"),
  proxyNote: document.getElementById("proxyNote"),
  availabilityNote: document.getElementById("availabilityNote"),
  body: document.getElementById("radarBody"),
  status: document.getElementById("radarState"),
  notionals: [...document.querySelectorAll("[data-notional]")],
};

const copy = {
  zh: {
    pageTitle: "市場雷達",
    pageSubtitle: "以過去 90 天資料整理各資產的利差與方向。",
    languageToggle: "English",
    notionalLabel: "名目金額",
    tableTitle: "90 天市場摘要",
    tableSubtitle: "選擇名目金額後，依資產字母排序。",
    assetHead: "資產",
    normalDirectionHead: "常態最佳方向",
    medianSpreadHead: "90天中位利差",
    burstDirectionHead: "爆發最佳方向",
    p95Head: "90天 P95",
    highVenueHead: "常高利率 Venue",
    lowVenueHead: "常低利率 Venue",
    dteHead: "有效 DTE",
    notesTitle: "說明",
    proxyNote: "DTE 係依目前 CrossEx 成本/資本模型作為歷史代理估算；不是歷史實際交易成本。",
    availabilityNote: "沒有可用資料的欄位會顯示「—」。",
    loading: "正在載入市場雷達資料。",
    fetchError: "無法載入市場雷達資料。",
    malformed: "市場雷達資料格式無效。",
    renderError: "市場雷達資料無法顯示。",
    empty: "所選名目金額沒有可顯示的資料。",
  },
  en: {
    pageTitle: "Market Radar",
    pageSubtitle: "A 90-day view of historical spreads and directions by asset.",
    languageToggle: "繁體中文",
    notionalLabel: "Notional",
    tableTitle: "90-day market summary",
    tableSubtitle: "Rows are sorted by asset for the selected notional.",
    assetHead: "Asset",
    normalDirectionHead: "Typical best direction",
    medianSpreadHead: "90-day median spread",
    burstDirectionHead: "Burst best direction",
    p95Head: "90-day P95",
    highVenueHead: "Usually high-rate venue",
    lowVenueHead: "Usually low-rate venue",
    dteHead: "Viable DTE",
    notesTitle: "Notes",
    proxyNote: "DTE uses the current CrossEx cost/capital model as a historical proxy; it is not actual historical cost.",
    availabilityNote: "Unavailable fields display as —.",
    loading: "Loading Market Radar data.",
    fetchError: "Market Radar data could not be loaded.",
    malformed: "Market Radar data is invalid.",
    renderError: "Market Radar data could not be displayed.",
    empty: "No rows are available for the selected notional.",
  },
};

function text() {
  return copy[state.lang];
}

function applyCopy() {
  const labels = text();
  document.documentElement.lang = state.lang === "zh" ? "zh-TW" : "en";
  for (const [key, value] of Object.entries(labels)) {
    if (el[key]) el[key].textContent = value;
  }
}

function setStatus(kind, message) {
  el.status.dataset.kind = kind;
  el.status.textContent = message;
}

function formatPercent(value) {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(state.lang === "zh" ? "zh-TW" : "en", {
    style: "percent",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatDte(value) {
  if (!Number.isFinite(value)) return "—";
  return state.lang === "zh" ? `≥ ${value} 天` : `≥ ${value}d`;
}

function directionLabel(direction) {
  if (!direction?.shortVenue || !direction.longVenue) return "—";
  return `${direction.shortVenue} → ${direction.longVenue}`;
}

function drilldownHref(asset, direction) {
  if (!direction?.shortVenue || !direction.longVenue || !direction.drilldownMaturity) return null;
  const query = new URLSearchParams({
    asset,
    direction: `${direction.shortVenue}|${direction.longVenue}`,
    maturity: direction.drilldownMaturity,
    notional: String(state.notional),
    lang: state.lang,
  });
  return `./arbitrage.html?${query.toString()}`;
}

function appendTextCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value ?? "—";
  row.appendChild(cell);
}

function appendDirectionCell(row, asset, direction) {
  const cell = document.createElement("td");
  const href = drilldownHref(asset, direction);
  if (href) {
    const link = document.createElement("a");
    link.className = "radar-drilldown";
    link.href = href;
    link.textContent = directionLabel(direction);
    cell.appendChild(link);
  } else {
    cell.textContent = "—";
  }
  row.appendChild(cell);
}

function selectedRows() {
  return state.data.rows
    .filter((row) => row.notionalUsd === state.notional)
    .sort((left, right) => left.asset.localeCompare(right.asset));
}

function render() {
  applyCopy();
  el.notionals.forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.notional) === state.notional);
  });
  el.body.innerHTML = "";

  const rows = selectedRows();
  if (!rows.length) {
    setStatus("empty", text().empty);
    return;
  }

  setStatus("", "");
  for (const item of rows) {
    const row = document.createElement("tr");
    appendTextCell(row, item.asset);
    appendDirectionCell(row, item.asset, item.normal);
    appendTextCell(row, formatPercent(item.normal?.medianSpreadApr));
    appendDirectionCell(row, item.asset, item.burst);
    appendTextCell(row, formatPercent(item.burst?.p95SpreadApr));
    appendTextCell(row, item.highVenue);
    appendTextCell(row, item.lowVenue);
    appendTextCell(row, formatDte(item.dteCutoffDays));
    el.body.appendChild(row);
  }
}

function isValidPayload(payload) {
  return (
    payload &&
    Array.isArray(payload.rows) &&
    payload.rows.every(
      (row) =>
        row &&
        typeof row.asset === "string" &&
        Number.isFinite(row.notionalUsd) &&
        (row.normal === null || typeof row.normal === "object") &&
        (row.burst === null || typeof row.burst === "object"),
    )
  );
}

async function load() {
  applyCopy();
  el.body.innerHTML = "";
  setStatus("loading", text().loading);

  let payload;
  try {
    const response = await fetch(DATA_URL);
    if (!response.ok) throw new Error("Market Radar request failed");
    payload = await response.json();
  } catch {
    setStatus("error", text().fetchError);
    return;
  }

  if (!isValidPayload(payload)) {
    setStatus("error", text().malformed);
    return;
  }

  state.data = payload;
  try {
    render();
  } catch {
    setStatus("render", text().renderError);
  }
}

el.notionals.forEach((button) => {
  button.addEventListener("click", () => {
    state.notional = Number(button.dataset.notional);
    if (state.data) render();
  });
});

el.languageToggle.addEventListener("click", () => {
  state.lang = state.lang === "zh" ? "en" : "zh";
  if (state.data) render();
  else applyCopy();
});

load();
