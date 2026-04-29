#!/usr/bin/env python3
"""Upload local AYVID artifacts to GCS so Cloud Run can serve them.

Syncs these directories/files into the configured GCS bucket:
  - live/                    (equity_curve, trades, positions, run_meta)
  - document/backtests/      (backtest JSON + markdown reports)
  - data_store/signal_book.json
  - calibration/             (priors, edge_proposals, refined_dag, trade_outcomes)

Usage:
  python -m scripts.gcs_sync                  # one-shot sync
  python -m scripts.gcs_sync --watch          # continuous sync every 60s
  python -m scripts.gcs_sync --watch --interval 30

Environment:
  AYVID_LIVE_GCS_BUCKET   (required) — e.g. "ayvid-production-tfstate"
  AYVID_LIVE_GCS_PREFIX   (optional) — default "live_state"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Artifact directories/files to sync (relative to PROJECT_ROOT)
SYNC_TARGETS: list[str] = [
    "live/equity_curve.jsonl",
    "live/trades.jsonl",
    "live/positions.json",
    "live/run_meta.json",
    "document/backtests",          # entire directory
    "data_store/signal_book.json",
    "calibration/priors.json",
    "calibration/edge_proposals.jsonl",
    "calibration/refined_dag.json",
    "calibration/trade_outcomes.jsonl",
]


def _resolve_files(targets: list[str]) -> list[Path]:
    """Expand directories and filter to existing files."""
    files: list[Path] = []
    for t in targets:
        p = PROJECT_ROOT / t
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    files.append(f)
        elif p.is_file():
            files.append(p)
    return files


def _md5(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def sync_once(bucket_name: str, prefix: str, verbose: bool = True) -> int:
    """Upload changed files to GCS. Returns count of files uploaded."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    files = _resolve_files(SYNC_TARGETS)

    uploaded = 0
    for f in files:
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        blob_name = f"{prefix}/{rel}" if prefix else rel
        blob = bucket.blob(blob_name)

        # Skip if blob exists with same md5
        local_md5 = _md5(f)
        blob.reload() if blob.exists() else None
        if blob.exists() and blob.md5_hash:
            import base64
            remote_md5 = base64.b64decode(blob.md5_hash).hex()
            if remote_md5 == local_md5:
                continue

        content_type = "application/json"
        if f.suffix == ".jsonl":
            content_type = "application/x-ndjson"
        elif f.suffix == ".md":
            content_type = "text/markdown"

        blob.upload_from_filename(str(f), content_type=content_type)
        uploaded += 1
        if verbose:
            print(f"  uploaded: {rel} -> gs://{bucket_name}/{blob_name}")

    if verbose:
        print(f"  sync complete: {uploaded}/{len(files)} files uploaded")
    return uploaded


def main():
    parser = argparse.ArgumentParser(description="Sync AYVID artifacts to GCS")
    parser.add_argument("--watch", action="store_true", help="Continuous sync mode")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between syncs (default: 60)")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    args = parser.parse_args()

    bucket = os.environ.get("AYVID_LIVE_GCS_BUCKET", "").strip()
    prefix = os.environ.get("AYVID_LIVE_GCS_PREFIX", "live_state").strip("/")

    if not bucket:
        print("ERROR: AYVID_LIVE_GCS_BUCKET not set", file=sys.stderr)
        sys.exit(1)

    verbose = not args.quiet
    if verbose:
        print(f"Syncing to gs://{bucket}/{prefix}/")

    if args.watch:
        if verbose:
            print(f"Watch mode: syncing every {args.interval}s (Ctrl-C to stop)")
        while True:
            try:
                sync_once(bucket, prefix, verbose)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                print(f"  sync error: {exc}", file=sys.stderr)
            time.sleep(args.interval)
    else:
        sync_once(bucket, prefix, verbose)


if __name__ == "__main__":
    main()
