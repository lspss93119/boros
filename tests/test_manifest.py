import json
from pathlib import Path

from boros_research.manifest import select_archive_files


def test_selects_lightweight_data_and_only_supported_combined_books():
    files = json.loads(Path("tests/fixtures/manifest.json").read_text())
    selected = [entry["path"] for entry in select_archive_files(files)]

    assert any(path.startswith("market-data/") for path in selected)
    assert "market-data/999-LIGHTER-HYPEUSDT-25SEP2026/2026-08.ndjson.zip" in selected
    assert any(path.startswith("settlement/") for path in selected)
    assert any(path.startswith("underlying-apr/") for path in selected)
    assert any(path.startswith("funding-rate/") for path in selected)
    assert any(path.startswith("ohlcv/5m/") for path in selected)
    assert any(path.startswith("ohlcv/1d/") for path in selected)
    assert "order-book/155-HYPERLIQUID-HYPEUSDT-25SEP2026/combined_0.0001/2026-08.ndjson.zip" in selected
    assert not any("/raw/" in path for path in selected)
    assert not any("999-LIGHTER" in path and path.startswith("order-book/") for path in selected)
