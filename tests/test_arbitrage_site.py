from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_arbitrage_static_site_assets_and_dom_contract():
    html = (ROOT / "site" / "arbitrage.html").read_text(encoding="utf-8")
    javascript = (ROOT / "site" / "arbitrage.js").read_text(encoding="utf-8")
    css = (ROOT / "site" / "arbitrage.css").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="./styles.css"' in html
    assert '<script src="./arbitrage.js"' in html
    assert './data/boros_arbitrage_site_data.json' in javascript
    assert 'id="assetSelect"' in html
    assert 'id="directionSelect"' in html
    assert 'id="expirationSelect"' in html
    assert 'id="marketFilter"' in html
    assert 'id="marketSelect"' in html
    assert 'hidden' in html
    assert 'id="notionalSelect"' in html
    assert 'id="kpis"' in html
    assert 'id="spreadChart"' in html
    assert 'id="distributionChart"' in html
    assert 'id="notionalComparison"' in html
    assert 'id="leaderboard"' in html
    assert 'href="./index.html"' in html
    assert 'Historical Arbitrage Explorer' in html
    assert '歷史套利研究' in html
    assert '.kpis' in css


def test_original_apr_page_contract_and_minimal_navigation_link():
    html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")

    for element_id in (
        "exchangeSelect",
        "assetSelect",
        "maturitySelect",
        "scatterChart",
        "mainChart",
        "crossChart",
        "languageToggle",
    ):
        assert f'id="{element_id}"' in html
    assert 'class="ghost-link"' in html
    assert "Arbitrage Research" in html


def test_arbitrage_direction_labels_are_clean_but_identity_remains_exact():
    javascript = (ROOT / "site" / "arbitrage.js").read_text(encoding="utf-8")
    assert shutil.which("node") is not None, "Node.js is required for the browser helper regression"

    node_script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("site/arbitrage.js", "utf8");
const start = source.indexOf("const directionKey =");
const end = source.indexOf("function setOptions", start);
const helpers = source.slice(start, end);
const structures = [
  {asset: "HYPE", tokenId: 1, maturity: "2026-09-25", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 49, longMarketId: 48},
  {asset: "HYPE", tokenId: 1, maturity: "2026-08-28", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 49, longMarketId: 48},
  {asset: "HYPE", tokenId: 1, maturity: "2026-09-25", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 50, longMarketId: 51},
  {asset: "HYPE", tokenId: 2, maturity: "2026-09-25", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 49, longMarketId: 48},
  {asset: "HYPE", tokenId: 1, maturity: "2026-09-25", shortVenue: "BINANCE", longVenue: "HYPERLIQUID", shortMarketId: 48, longMarketId: 49},
];
const output = vm.runInNewContext(`${helpers}
JSON.stringify({
  normal: directionLabel(structures[0], structures.slice(0, 2)),
  otherMaturity: directionLabel(structures[1], structures.slice(0, 2)),
  marketAmbiguous: directionLabel(structures[2], [structures[0], structures[2]]),
  tokenAmbiguous: directionLabel(structures[3], [structures[0], structures[3]]),
  reverse: directionLabel(structures[4], [structures[4]]),
  keys: structures.map(directionKey),
})`, {structures});
process.stdout.write(output);
'''
    result = subprocess.run(
        ["node", "-e", node_script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)

    assert output["normal"] == "HYPERLIQUID → BINANCE"
    assert output["otherMaturity"] == "HYPERLIQUID → BINANCE"
    assert output["marketAmbiguous"] == "HYPERLIQUID → BINANCE · market 50/51"
    assert output["tokenAmbiguous"] == "HYPERLIQUID → BINANCE · token 2"
    assert output["reverse"] == "BINANCE → HYPERLIQUID"
    # maturity remains the separate expiration filter, so directionKey keeps
    # its existing scope while the other identity components stay distinct.
    assert output["keys"][0] == output["keys"][1]
    assert len(set(output["keys"])) == 4
    assert output["keys"][0] != output["keys"][2]
    assert output["keys"][0] != output["keys"][3]
    assert output["keys"][0] != output["keys"][4]


def test_arbitrage_filters_group_by_venue_pair_and_resolve_exact_market():
    assert shutil.which("node") is not None, "Node.js is required for the browser helper regression"
    javascript = (ROOT / "site" / "arbitrage.js").read_text(encoding="utf-8")
    assert "state.direction = venueDirectionKey(structure)" in javascript
    assert "state.market = directionKey(structure)" in javascript

    node_script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("site/arbitrage.js", "utf8");
const start = source.indexOf("const directionKey =");
const end = source.indexOf("function setOptions", start);
const helpers = source.slice(start, end);
const structures = [
  {asset: "BTC", tokenId: 1, maturity: "2026-02-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 49, longMarketId: 48},
  {asset: "BTC", tokenId: 1, maturity: "2026-03-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 67, longMarketId: 68},
  {asset: "BTC", tokenId: 1, maturity: "2026-02-27", shortVenue: "BINANCE", longVenue: "HYPERLIQUID", shortMarketId: 48, longMarketId: 49},
  {asset: "BTC", tokenId: 1, maturity: "2026-04-24", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 97, longMarketId: 98},
  {asset: "BTC", tokenId: 1, maturity: "2026-02-27", shortVenue: "HYPERLIQUID", longVenue: "OKX", shortMarketId: 49, longMarketId: 55},
  {asset: "BTC", tokenId: 1, maturity: "2026-03-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 70, longMarketId: 71},
  {asset: "BTC", tokenId: 2, maturity: "2026-03-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 67, longMarketId: 68},
];
const output = vm.runInNewContext(`${helpers}
JSON.stringify({
  directions: venueDirectionsForAsset(structures, "BTC"),
  expirations: expirationValuesForDirection(structures, "BTC", "HYPERLIQUID|BINANCE"),
  normal: filterStructureCandidates(structures, "BTC", "HYPERLIQUID|BINANCE", "2026-02-27"),
  ambiguous: filterStructureCandidates(structures, "BTC", "HYPERLIQUID|BINANCE", "2026-03-27"),
})` , {structures});
process.stdout.write(output);
'''
    result = subprocess.run(
        ["node", "-e", node_script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)

    assert output["directions"] == ["BINANCE|HYPERLIQUID", "HYPERLIQUID|BINANCE", "HYPERLIQUID|OKX"]
    assert output["expirations"] == ["2026-02-27", "2026-03-27", "2026-04-24"]
    assert len(output["normal"]) == 1
    assert output["normal"][0]["shortMarketId"] == 49
    assert output["normal"][0]["longMarketId"] == 48
    assert len(output["ambiguous"]) == 3

    node_resolution = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("site/arbitrage.js", "utf8");
const start = source.indexOf("const directionKey =");
const end = source.indexOf("function setOptions", start);
const helpers = source.slice(start, end);
const candidates = [
  {asset: "BTC", tokenId: 1, maturity: "2026-03-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 67, longMarketId: 68},
  {asset: "BTC", tokenId: 2, maturity: "2026-03-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 67, longMarketId: 68},
];
process.stdout.write(vm.runInNewContext(`${helpers}
JSON.stringify({
  unresolved: resolveSelectedStructure(candidates, ""),
  selected: resolveSelectedStructure(candidates, directionKey(candidates[1])),
  labels: candidates.map(item => marketLabel(item, candidates)),
})`, {candidates}));
'''
    resolution = subprocess.run(
        ["node", "-e", node_resolution],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    resolved = json.loads(resolution.stdout)
    assert resolved["unresolved"] is None
    assert resolved["selected"]["tokenId"] == 2
    assert resolved["labels"] == ["Market 67 / 68 · Token 1", "Market 67 / 68 · Token 2"]


def test_production_arbitrage_data_has_no_true_market_ambiguity():
    data = json.loads((ROOT / "site" / "data" / "boros_arbitrage_site_data.json").read_text(encoding="utf-8"))
    groups = {}
    for structure in data["structures"]:
        key = (structure["asset"], structure["shortVenue"], structure["longVenue"], structure["maturity"])
        groups.setdefault(key, set()).add(
            (structure["tokenId"], structure["shortMarketId"], structure["longMarketId"])
        )
    assert sum(len(values) > 1 for values in groups.values()) == 0


def test_arbitrage_market_filter_binding_supports_non_ambiguous_render():
    html = (ROOT / "site" / "arbitrage.html").read_text(encoding="utf-8")
    javascript = (ROOT / "site" / "arbitrage.js").read_text(encoding="utf-8")
    assert 'id="marketFilter"' in html
    assert 'marketFilter: document.getElementById("marketFilter")' in javascript
    assert "el.marketFilter" in javascript
    assert shutil.which("node") is not None, "Node.js is required for the DOM binding regression"

    node_script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("site/arbitrage.js", "utf8");
const end = source.lastIndexOf("\nload();");
const nodes = new Map();
function node(id) {
  const element = {
    id,
    value: "",
    hidden: false,
    textContent: "",
    children: [],
    classList: {toggle() {}},
    appendChild(child) { this.children.push(child); },
    addEventListener() {},
  };
  let innerHTML = "";
  Object.defineProperty(element, "innerHTML", {
    get() { return innerHTML; },
    set(value) { innerHTML = value; this.children = []; },
  });
  return element;
}
[
  "pageTitle", "pageSubtitle", "languageToggle", "assetLabel", "directionLabel",
  "expirationLabel", "marketLabel", "notionalLabel", "filterBar", "assetSelect",
  "directionSelect", "expirationSelect", "marketFilter", "marketSelect", "notionalSelect",
  "kpis", "historyTitle", "historySubtitle", "historyNote", "spreadTab", "percentileTab",
  "spreadChart", "distributionTitle", "distributionSubtitle", "distributionChart",
  "distributionMeta", "notionalTitle", "notionalSubtitle", "notionalComparisonBody",
  "leaderboardTitle", "leaderboardSubtitle", "leaderboardBody", "arbitrageApp",
].forEach(id => nodes.set(id, node(id)));
const documentStub = {
  documentElement: {lang: "en"},
  getElementById(id) { return nodes.get(id); },
  createElement(tag) { return node(tag); },
};
const windowStub = {
  location: {search: ""},
  devicePixelRatio: 1,
  addEventListener() {},
};
const structures = [
  {id: "btc-feb", asset: "BTC", tokenId: 1, maturity: "2026-02-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 49, longMarketId: 48},
  {id: "btc-feb-alt", asset: "BTC", tokenId: 1, maturity: "2026-02-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 50, longMarketId: 51},
  {id: "btc-mar", asset: "BTC", tokenId: 1, maturity: "2026-03-27", shortVenue: "HYPERLIQUID", longVenue: "BINANCE", shortMarketId: 67, longMarketId: 68},
];
const script = `${source.slice(0, end)}
state.data = {structures};
state.asset = "BTC";
state.direction = "HYPERLIQUID|BINANCE";
state.expiration = "2026-03-27";
populateFilters();
const normal = {
  hidden: el.marketFilter.hidden,
  marketOptions: el.market.children.length,
};
state.expiration = "2026-02-27";
state.market = "";
populateFilters();
JSON.stringify({
  bound: el.marketFilter === nodes.get("marketFilter"),
  normal,
  ambiguousHidden: el.marketFilter.hidden,
  ambiguousMarketOptions: el.market.children.length,
  directionValues: [...el.direction.children].map(option => option.value),
  expirationValues: [...el.expiration.children].map(option => option.value),
  notionalValues: [...el.notional.children].map(option => option.value),
})`;
process.stdout.write(vm.runInNewContext(script, {
  document: documentStub,
  window: windowStub,
  URLSearchParams,
  structures,
  nodes,
  console,
}));
'''
    result = subprocess.run(
        ["node", "-e", node_script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)
    assert output["bound"] is True
    assert output["normal"] == {"hidden": True, "marketOptions": 0}
    assert output["ambiguousHidden"] is False
    assert output["ambiguousMarketOptions"] == 3
    assert output["directionValues"] == ["HYPERLIQUID|BINANCE"]
    assert output["expirationValues"] == ["2026-02-27", "2026-03-27"]
    assert output["notionalValues"] == [10000, 25000, 50000]


def test_arbitrage_market_filter_hidden_css_is_scoped_and_production_is_unambiguous():
    html = (ROOT / "site" / "arbitrage.html").read_text(encoding="utf-8")
    css = (ROOT / "site" / "arbitrage.css").read_text(encoding="utf-8")
    javascript = (ROOT / "site" / "arbitrage.js").read_text(encoding="utf-8")
    assert 'id="marketFilter"' in html
    assert "hidden" in html
    assert "#arbitrageApp #marketFilter[hidden]" in css
    assert "#arbitrageApp #marketFilter[hidden] {\n  display: none;\n}" in css
    assert ".filters.market-ambiguous" in css
    assert "grid-template-columns: repeat(5" in css
    assert "el.marketFilter.hidden = !ambiguous" in javascript

    data = json.loads((ROOT / "site" / "data" / "boros_arbitrage_site_data.json").read_text(encoding="utf-8"))
    groups = {}
    for structure in data["structures"]:
        key = (structure["asset"], structure["shortVenue"], structure["longVenue"], structure["maturity"])
        groups.setdefault(key, set()).add(
            (structure["tokenId"], structure["shortMarketId"], structure["longMarketId"])
        )
    assert sum(len(values) > 1 for values in groups.values()) == 0


def test_apr_explorer_uses_taiwan_traditional_chinese():
    javascript = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    chinese_copy = javascript.split("  zh: {", 1)[1].split("\n  },\n};", 1)[0]

    for term in ("歷史", "隱含", "遠期", "資金費率", "市場", "載入", "標的資產"):
        assert term in chinese_copy
    for simplified in ("历史", "隐含", "远期", "资金费率", "市场", "加载", "标的资产"):
        assert simplified not in chinese_copy

    assert 'state.lang === "zh" ? "zh-TW" : "en"' in javascript
    assert 'switchLanguage: "繁體中文"' in javascript
