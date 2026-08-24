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


def test_apr_explorer_uses_taiwan_traditional_chinese():
    javascript = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
    chinese_copy = javascript.split("  zh: {", 1)[1].split("\n  },\n};", 1)[0]

    for term in ("歷史", "隱含", "遠期", "資金費率", "市場", "載入", "標的資產"):
        assert term in chinese_copy
    for simplified in ("历史", "隐含", "远期", "资金费率", "市场", "加载", "标的资产"):
        assert simplified not in chinese_copy

    assert 'state.lang === "zh" ? "zh-TW" : "en"' in javascript
    assert 'switchLanguage: "繁體中文"' in javascript
