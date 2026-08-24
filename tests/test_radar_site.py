from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_radar_static_shell_keeps_the_task_five_mvp_contract():
    """Catches a Radar shell that drops required columns or adds execution language."""
    html_path = ROOT / "site" / "radar.html"
    javascript_path = ROOT / "site" / "radar.js"
    css_path = ROOT / "site" / "radar.css"

    assert html_path.is_file()
    assert javascript_path.is_file()
    assert css_path.is_file()

    html = html_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8")

    assert '<html lang="zh-TW">' in html
    assert '<link rel="stylesheet" href="./styles.css">' in html
    assert '<link rel="stylesheet" href="./radar.css">' in html
    assert '<script src="./radar.js"></script>' in html
    assert 'id="languageToggle"' in html
    assert '>English</button>' in html
    assert 'href="./index.html"' in html
    assert 'href="./arbitrage.html"' in html

    for column in (
        "資產",
        "常態最佳方向",
        "90天中位利差",
        "爆發最佳方向",
        "90天 P95",
        "常高利率 Venue",
        "常低利率 Venue",
        "有效 DTE",
    ):
        assert column in html

    assert html.count('data-notional="10000"') == 1
    assert html.count('data-notional="25000"') == 1
    assert html.count('data-notional="50000"') == 1
    for label in ("$10,000", "$25,000", "$50,000"):
        assert html.count(label) == 1

    assert (
        "DTE 係依目前 CrossEx 成本/資本模型作為歷史代理估算；不是歷史實際交易成本。"
        in html
    )
    for forbidden in ("net apr", "score", "recommended", "trade now"):
        assert forbidden not in html.lower()

    assert "body" not in css
    assert "table" in css


def _run_radar_dom_test(script: str) -> dict[str, object]:
    assert shutil.which("node") is not None, "Node.js is required for the Radar DOM tests"
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_radar_query_language_and_notional_switch_rendering():
    """Catches query defaults or selected-notional filtering drifting from the Radar UI."""
    output = _run_radar_dom_test(
        r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("site/radar.js", "utf8");
const payload = {
  notionals: [10000, 25000, 50000],
  rows: [
    {asset: "ZED", available: true, notionalUsd: 10000, dteCutoffDays: 9, highVenue: "HYPERLIQUID", lowVenue: "BYBIT", normal: {shortVenue: "HYPERLIQUID", longVenue: "BYBIT", medianSpreadApr: 0.012, drilldownMaturity: "2026-09-25"}, burst: {shortVenue: "HYPERLIQUID", longVenue: "BYBIT", p95SpreadApr: 0.024, drilldownMaturity: "2026-09-25"}},
    {asset: "HYPE", available: true, notionalUsd: 10000, dteCutoffDays: 12, highVenue: "HYPERLIQUID", lowVenue: "BYBIT", normal: {shortVenue: "HYPERLIQUID", longVenue: "BYBIT", medianSpreadApr: 0.018, drilldownMaturity: "2026-09-25"}, burst: {shortVenue: "HYPERLIQUID", longVenue: "BYBIT", p95SpreadApr: 0.031, drilldownMaturity: "2026-09-25"}},
    {asset: "BTC", available: true, notionalUsd: 25000, dteCutoffDays: 20, highVenue: "HYPERLIQUID", lowVenue: "OKX", normal: {shortVenue: "HYPERLIQUID", longVenue: "OKX", medianSpreadApr: 0.02, drilldownMaturity: "2026-08-28"}, burst: {shortVenue: "HYPERLIQUID", longVenue: "OKX", p95SpreadApr: 0.04, drilldownMaturity: "2026-08-28"}}
  ]
};
function node(tag) {
  const listeners = new Map();
  const classes = new Set();
  const element = {
    tag, children: [], dataset: {}, textContent: "", href: "", value: "", hidden: false,
    classList: { toggle(name, force) { if (force) classes.add(name); else classes.delete(name); }, contains(name) { return classes.has(name); } },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(type, handler) { listeners.set(type, handler); },
    click() { const handler = listeners.get("click"); if (handler) handler({currentTarget: this}); },
  };
  let innerHTML = "";
  Object.defineProperty(element, "innerHTML", {get() { return innerHTML; }, set(value) { innerHTML = value; this.children = []; }});
  return element;
}
async function render(search, changeNotional) {
  const nodes = new Map();
  ["pageTitle", "pageSubtitle", "languageToggle", "notionalLabel", "tableTitle", "tableSubtitle", "assetHead", "normalDirectionHead", "medianSpreadHead", "burstDirectionHead", "p95Head", "highVenueHead", "lowVenueHead", "dteHead", "notesTitle", "proxyNote", "availabilityNote", "radarBody", "radarState"].forEach(id => nodes.set(id, node(id)));
  const buttons = [10000, 25000, 50000].map(value => { const button = node("button"); button.dataset.notional = String(value); return button; });
  const document = {documentElement: {lang: "zh-TW"}, getElementById(id) { return nodes.get(id); }, createElement(tag) { return node(tag); }, querySelectorAll(selector) { return selector === "[data-notional]" ? buttons : []; }};
  const context = {document, window: {location: {search}, addEventListener() {}}, URLSearchParams, fetch: async () => ({ok: true, json: async () => payload})};
  vm.runInNewContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  if (changeNotional) buttons.find(button => button.dataset.notional === "25000").click();
  const body = nodes.get("radarBody");
  return {
    lang: document.documentElement.lang,
    title: nodes.get("pageTitle").textContent,
    toggle: nodes.get("languageToggle").textContent,
    active: buttons.filter(button => button.classList.contains("active")).map(button => Number(button.dataset.notional)),
    assets: body.children.map(row => row.children[0].textContent),
  };
}
(async () => {
  const [defaultView, zhView, enView, switched] = await Promise.all([
    render("", false), render("?lang=zh", false), render("?lang=en", false), render("", true),
  ]);
  process.stdout.write(JSON.stringify({defaultView, zhView, enView, switched}));
})().catch(error => { process.stderr.write(String(error)); process.exit(1); });
'''
    )

    assert output["defaultView"] == {
        "lang": "zh-TW",
        "title": "市場雷達",
        "toggle": "English",
        "active": [10000],
        "assets": ["HYPE", "ZED"],
    }
    assert output["zhView"] == output["defaultView"]
    assert output["enView"] == {
        "lang": "en",
        "title": "Market Radar",
        "toggle": "繁體中文",
        "active": [10000],
        "assets": ["HYPE", "ZED"],
    }
    assert output["switched"] == {
        "lang": "zh-TW",
        "title": "市場雷達",
        "toggle": "English",
        "active": [25000],
        "assets": ["BTC"],
    }


def test_radar_drilldowns_escape_direction_and_keep_unavailable_cells_plain():
    """Catches unsafe drilldown queries or unavailable directions becoming broken links."""
    output = _run_radar_dom_test(
        r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("site/radar.js", "utf8");
const payload = {rows: [
  {asset: "HYPE", available: true, notionalUsd: 10000, dteCutoffDays: 9, highVenue: "HYPERLIQUID", lowVenue: "BYBIT", normal: {shortVenue: "HYPERLIQUID", longVenue: "BYBIT", medianSpreadApr: 0.01, drilldownMaturity: "2026-09-25"}, burst: {shortVenue: "HYPERLIQUID", longVenue: "OKX", p95SpreadApr: 0.02, drilldownMaturity: "2026-08-28"}},
  {asset: "XAU", available: false, notionalUsd: 10000, dteCutoffDays: null, highVenue: null, lowVenue: null, normal: null, burst: null}
]};
function node(tag) {
  const element = {tag, children: [], dataset: {}, textContent: "", href: "", classList: {toggle() {}}, appendChild(child) { this.children.push(child); return child; }, addEventListener() {}};
  let innerHTML = "";
  Object.defineProperty(element, "innerHTML", {get() { return innerHTML; }, set(value) { innerHTML = value; this.children = []; }});
  return element;
}
const nodes = new Map();
["pageTitle", "pageSubtitle", "languageToggle", "notionalLabel", "tableTitle", "tableSubtitle", "assetHead", "normalDirectionHead", "medianSpreadHead", "burstDirectionHead", "p95Head", "highVenueHead", "lowVenueHead", "dteHead", "notesTitle", "proxyNote", "availabilityNote", "radarBody", "radarState"].forEach(id => nodes.set(id, node(id)));
const buttons = [10000, 25000, 50000].map(value => { const button = node("button"); button.dataset.notional = String(value); return button; });
const document = {documentElement: {lang: "zh-TW"}, getElementById(id) { return nodes.get(id); }, createElement(tag) { return node(tag); }, querySelectorAll(selector) { return selector === "[data-notional]" ? buttons : []; }};
vm.runInNewContext(source, {document, window: {location: {search: ""}, addEventListener() {}}, URLSearchParams, fetch: async () => ({ok: true, json: async () => payload})});
setImmediate(() => {
  const rows = nodes.get("radarBody").children;
  const hype = rows[0];
  const unavailable = rows[1];
  const normalCell = hype?.children[1];
  const burstCell = hype?.children[3];
  const unavailableNormalCell = unavailable?.children[1];
  const unavailableBurstCell = unavailable?.children[3];
  process.stdout.write(JSON.stringify({
    normalHref: normalCell?.children[0]?.href ?? null,
    burstHref: burstCell?.children[0]?.href ?? null,
    unavailableNormal: unavailableNormalCell?.textContent ?? null,
    unavailableBurst: unavailableBurstCell?.textContent ?? null,
    unavailableNormalLinks: unavailableNormalCell?.children.length ?? 0,
    unavailableBurstLinks: unavailableBurstCell?.children.length ?? 0,
  }));
});
'''
    )

    assert output["normalHref"] == (
        "./arbitrage.html?asset=HYPE&direction=HYPERLIQUID%7CBYBIT&maturity=2026-09-25&notional=10000&lang=zh"
    )
    assert output["burstHref"] == (
        "./arbitrage.html?asset=HYPE&direction=HYPERLIQUID%7COKX&maturity=2026-08-28&notional=10000&lang=zh"
    )
    assert output["unavailableNormal"] == "—"
    assert output["unavailableBurst"] == "—"
    assert output["unavailableNormalLinks"] == 0
    assert output["unavailableBurstLinks"] == 0


def test_radar_distinguishes_fetch_malformed_and_empty_data_states():
    """Catches data-loading failures being conflated with invalid or empty Radar data."""
    output = _run_radar_dom_test(
        r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("site/radar.js", "utf8");
function node(tag) {
  const element = {tag, children: [], dataset: {}, textContent: "", classList: {toggle() {}}, appendChild(child) { this.children.push(child); return child; }, addEventListener() {}};
  let innerHTML = "";
  Object.defineProperty(element, "innerHTML", {get() { return innerHTML; }, set(value) { innerHTML = value; this.children = []; }});
  return element;
}
async function render(kind) {
  const nodes = new Map();
  ["pageTitle", "pageSubtitle", "languageToggle", "notionalLabel", "tableTitle", "tableSubtitle", "assetHead", "normalDirectionHead", "medianSpreadHead", "burstDirectionHead", "p95Head", "highVenueHead", "lowVenueHead", "dteHead", "notesTitle", "proxyNote", "availabilityNote", "radarBody", "radarState"].forEach(id => nodes.set(id, node(id)));
  const buttons = [10000, 25000, 50000].map(value => { const button = node("button"); button.dataset.notional = String(value); return button; });
  const document = {documentElement: {lang: "zh-TW"}, getElementById(id) { return nodes.get(id); }, createElement(tag) { return node(tag); }, querySelectorAll(selector) { return selector === "[data-notional]" ? buttons : []; }};
  const fetch = kind === "http"
    ? async () => ({ok: false})
    : async () => ({ok: true, json: async () => kind === "malformed" ? {rows: null} : {rows: []}});
  vm.runInNewContext(source, {document, window: {location: {search: ""}, addEventListener() {}}, URLSearchParams, fetch});
  await new Promise(resolve => setImmediate(resolve));
  return {kind: nodes.get("radarState").dataset.kind || "", text: nodes.get("radarState").textContent, rows: nodes.get("radarBody").children.length};
}
(async () => process.stdout.write(JSON.stringify({http: await render("http"), malformed: await render("malformed"), empty: await render("empty")})))().catch(error => { process.stderr.write(String(error)); process.exit(1); });
'''
    )

    assert output["http"] == {"kind": "error", "text": "無法載入市場雷達資料。", "rows": 0}
    assert output["malformed"] == {"kind": "error", "text": "市場雷達資料格式無效。", "rows": 0}
    assert output["empty"] == {"kind": "empty", "text": "所選名目金額沒有可顯示的資料。", "rows": 0}
