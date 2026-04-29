"""Shared GCS sync utility for uploading artifacts from any module.

Usage:
    from shared.gcs import sync_artifact
    sync_artifact(Path("data_store/signal_book.json"))
    sync_artifact(Path("document/backtests/report.json"))
    sync_artifact(Path("calibration/priors.json"))
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.config import settings
from shared.logger import get_logger

log = get_logger("shared.gcs")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_GCS_BUCKET = (getattr(settings, "AYVID_LIVE_GCS_BUCKET", None) or "").strip()
_GCS_PREFIX = (getattr(settings, "AYVID_LIVE_GCS_PREFIX", "live_state") or "live_state").strip("/")
_gcs_client: Any | None = None


def _blob_name(path: Path) -> str:
    try:
        rel = path.relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        rel = path.as_posix()
    return f"{_GCS_PREFIX}/{rel}" if _GCS_PREFIX else rel


def sync_artifact(path: Path) -> bool:
    """Upload a single file to the shared GCS bucket. Returns True on success."""
    if not _GCS_BUCKET:
        return False
    if not path.exists():
        return False
    global _gcs_client
    try:
        if _gcs_client is None:
            from google.cloud import storage
            _gcs_client = storage.Client()
        bucket = _gcs_client.bucket(_GCS_BUCKET)
        blob = bucket.blob(_blob_name(path))
        ct = "application/json"
        if path.suffix == ".jsonl":
            ct = "application/x-ndjson"
        elif path.suffix == ".md":
            ct = "text/markdown"
        blob.upload_from_filename(str(path), content_type=ct)
        return True
    except Exception as exc:
        log.warning(f"gcs sync failed for {path}: {exc}")
        return False


def sync_directory(directory: Path) -> int:
    """Upload all files in a directory to GCS. Returns count uploaded."""
    if not _GCS_BUCKET or not directory.is_dir():
        return 0
    count = 0
    for f in sorted(directory.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            if sync_artifact(f):
                count += 1
    return count
