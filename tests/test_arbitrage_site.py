from __future__ import annotations

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
