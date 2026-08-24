import io
import json
from pathlib import Path

import pytest

from boros_research.cli import main as cli_main
from boros_research.cli import render_selection_summary
from boros_research.download import download_archive_file, refresh_manifest


def manifest_entry(path="market-data/example/2026-08.ndjson.zip", size=4):
    return {"path": path, "size": size}


def test_existing_file_with_expected_size_is_skipped(tmp_path):
    entry = manifest_entry()
    target = tmp_path / entry["path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"same")
    calls = []

    def fake_fetcher(url):
        calls.append(url)
        raise AssertionError("an expected-size file must not be fetched")

    result = download_archive_file(entry, tmp_path, fetcher=fake_fetcher)

    assert result.status == "skipped"
    assert result.bytes_written == entry["size"]
    assert calls == []


class FailingReader:
    def __init__(self):
        self.reads = 0

    def read(self, _size=-1):
        self.reads += 1
        if self.reads == 1:
            return b"part"
        raise OSError("synthetic fetch failure")

    def close(self):
        return None


def test_partial_download_is_removed_on_failure(tmp_path):
    entry = manifest_entry(size=8)

    with pytest.raises(OSError, match="synthetic fetch failure"):
        download_archive_file(entry, tmp_path, fetcher=lambda _url: FailingReader())

    target = tmp_path / entry["path"]
    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()


def test_successful_download_is_atomic(tmp_path):
    entry = manifest_entry(size=8)
    payload = b"complete"

    result = download_archive_file(
        entry,
        tmp_path,
        fetcher=lambda _url: io.BytesIO(payload),
    )

    target = tmp_path / entry["path"]
    assert result.status == "downloaded"
    assert target.read_bytes() == payload
    assert not target.with_name(target.name + ".part").exists()


def test_existing_file_with_wrong_size_is_redownloaded(tmp_path):
    entry = manifest_entry(size=8)
    target = tmp_path / entry["path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"wrong")

    result = download_archive_file(
        entry,
        tmp_path,
        fetcher=lambda _url: io.BytesIO(b"complete"),
    )

    assert result.status == "downloaded"
    assert target.read_bytes() == b"complete"
    assert not target.with_name(target.name + ".part").exists()


def test_refresh_manifest_caches_official_manifest(tmp_path):
    entries = [manifest_entry()]
    calls = []

    def fake_fetcher(url):
        calls.append(url)
        return io.BytesIO(json.dumps(entries).encode("utf-8"))

    result = refresh_manifest(tmp_path, fetcher=fake_fetcher)

    assert result == entries
    assert calls == ["https://historical-data.boros.finance/files.json"]
    assert json.loads((tmp_path / "files.json").read_text()) == entries


def test_selection_summary_groups_dataset_sizes():
    selected = [
        manifest_entry("market-data/m1.zip", 10),
        manifest_entry("underlying-apr/a.zip", 20),
        manifest_entry("funding-rate/b.zip", 30),
        manifest_entry("ohlcv/5m/m1.zip", 40),
        manifest_entry("order-book/m1/combined_0.0001/a.zip", 50),
    ]

    summary = render_selection_summary(selected)

    assert "Selected file count: 5" in summary
    assert "Total compressed bytes: 150" in summary
    assert "market-data: 1 files, 10 bytes" in summary
    assert "underlying-apr / funding-rate: 2 files, 50 bytes" in summary
    assert "ohlcv: 1 files, 40 bytes" in summary
    assert "order-book: 1 files, 50 bytes" in summary


def test_cli_rejects_non_positive_workers(capsys):
    exit_code = cli_main(["download", "--workers", "0"])

    assert exit_code != 0
    assert "workers" in capsys.readouterr().err
