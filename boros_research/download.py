from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import HISTORICAL_BASE_URL, RAW_BOROS_DIR


Fetcher = Callable[[str], Any]
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class DownloadResult:
    path: str
    status: str
    bytes_written: int


class DownloadSizeMismatch(RuntimeError):
    def __init__(self, path: str, expected: int, actual: int):
        super().__init__(f"{path}: size mismatch: expected {expected}, got {actual}")
        self.path = path
        self.expected = expected
        self.actual = actual


class ManifestError(ValueError):
    pass


def _manifest_entry(entry: Mapping[str, Any]) -> tuple[str, int, PurePosixPath]:
    path = entry.get("path")
    size = entry.get("size")
    if not isinstance(path, str) or not path:
        raise ManifestError("manifest entry path must be a non-empty string")
    if "\\" in path:
        raise ManifestError(f"manifest path must use POSIX separators: {path!r}")
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ManifestError(f"manifest path must stay below raw directory: {path!r}")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ManifestError(f"manifest entry size must be a non-negative integer: {path!r}")
    return path, size, relative


def _target_path(raw_dir: Path, entry: Mapping[str, Any]) -> tuple[str, int, Path]:
    path, size, relative = _manifest_entry(entry)
    return path, size, raw_dir.joinpath(*relative.parts)


def _default_fetcher(url: str):
    request = Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "boros-research/0.1",
        },
    )
    return urlopen(request, timeout=120)


def _iter_payload(payload: Any):
    if isinstance(payload, (bytes, bytearray, memoryview)):
        if payload:
            yield bytes(payload)
        return

    reader = getattr(payload, "read", None)
    if not callable(reader):
        raise TypeError("fetcher must return bytes or a readable binary response")
    try:
        while True:
            chunk = reader(_CHUNK_SIZE)
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("fetcher response must produce binary chunks")
            if not chunk:
                return
            yield bytes(chunk)
    finally:
        close = getattr(payload, "close", None)
        if callable(close):
            close()


def _read_payload(payload: Any) -> bytes:
    return b"".join(_iter_payload(payload))


def _archive_url(base_url: str, relative_path: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(relative_path, safe='/')}"


def download_archive_file(
    entry: Mapping[str, Any],
    raw_dir: Path = RAW_BOROS_DIR,
    fetcher: Fetcher | None = None,
    base_url: str = HISTORICAL_BASE_URL,
) -> DownloadResult:
    """Download one manifest entry without exposing a partial final archive."""
    raw_dir = Path(raw_dir)
    path, expected_size, target = _target_path(raw_dir, entry)

    if target.exists() and target.is_file() and target.stat().st_size == expected_size:
        return DownloadResult(path=path, status="skipped", bytes_written=expected_size)
    if target.exists() and not target.is_file():
        raise IsADirectoryError(f"archive target is not a file: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    fetch = fetcher or _default_fetcher

    try:
        payload = fetch(_archive_url(base_url, path))
        with part.open("wb") as output:
            for chunk in _iter_payload(payload):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())

        actual_size = part.stat().st_size
        if actual_size != expected_size:
            raise DownloadSizeMismatch(path, expected_size, actual_size)
        part.replace(target)
        return DownloadResult(path=path, status="downloaded", bytes_written=actual_size)
    except Exception:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_manifest(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ManifestError("manifest must be a list of file entries")

    entries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ManifestError("manifest entries must be objects")
        _manifest_entry(item)
        entries.append(dict(item))
    return entries


def _load_manifest_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _validate_manifest(payload)
    if isinstance(payload, Mapping):
        raise ManifestError("manifest must be a list of file entries")
    try:
        decoded = json.loads(_read_payload(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest response is not valid JSON") from exc
    return _validate_manifest(decoded)


def _write_json_atomically(path: Path, value: Any) -> None:
    part = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with part.open("w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        part.replace(path)
    except Exception:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise


def refresh_manifest(
    raw_dir: Path = RAW_BOROS_DIR,
    fetcher: Fetcher | None = None,
    base_url: str = HISTORICAL_BASE_URL,
) -> list[dict[str, Any]]:
    """Fetch, validate, and atomically cache the official archive manifest."""
    fetch = fetcher or _default_fetcher
    payload = fetch(f"{base_url.rstrip('/')}/files.json")
    entries = _load_manifest_payload(payload)
    _write_json_atomically(Path(raw_dir) / "files.json", entries)
    return entries


def load_cached_manifest(raw_dir: Path = RAW_BOROS_DIR) -> list[dict[str, Any]]:
    path = Path(raw_dir) / "files.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"cached manifest is not valid JSON: {path}") from exc
    return _validate_manifest(payload)
