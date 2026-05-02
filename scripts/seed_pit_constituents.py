"""Seed point-in-time S&P 500 constituent snapshots.

Usage:
    python -m scripts.seed_pit_constituents \
        --source path/to/sp500_pit.csv \
        --output data_store/sp500_constituents/

The CSV is expected to have one row per (date, ticker) pair, in either:

    date,ticker            # ticker is in S&P 500 on that date
    YYYY-MM-DD,AAPL
    YYYY-MM-DD,MSFT
    ...

OR a long format with a single date column and one row per snapshot:

    date,tickers
    YYYY-MM-DD,"AAPL,MSFT,GOOGL,..."

The script writes one ``YYYY-MM-DD.json`` file per distinct date and an
``index.json`` mapping each date to its ticker list.

Authoritative sources for the underlying data:
  - CRSP Survivor-Bias Free database (commercial)
  - S&P Dow Jones Indices public release archive
  - Sharadar SF1 (Quandl) sf1_constituents
  - Wikipedia revision history (free, lower fidelity)

This script is intentionally provider-agnostic — it accepts any CSV in
the documented shapes. Backtests will refuse to run without seeded
PIT data once ``require_point_in_time=True`` is set in the call site.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


def _parse_csv(src: Path) -> dict[str, list[str]]:
    """Return ``{date_iso: [tickers]}`` from either CSV layout."""
    by_date: dict[str, set[str]] = defaultdict(set)
    with src.open() as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return {}
        cols = [c.strip().lower() for c in header]
        if "date" in cols and "ticker" in cols:
            d_idx = cols.index("date")
            t_idx = cols.index("ticker")
            for row in reader:
                if len(row) <= max(d_idx, t_idx):
                    continue
                date = row[d_idx].strip()
                ticker = row[t_idx].strip()
                if date and ticker:
                    by_date[date].add(ticker)
        elif "date" in cols and "tickers" in cols:
            d_idx = cols.index("date")
            t_idx = cols.index("tickers")
            for row in reader:
                if len(row) <= max(d_idx, t_idx):
                    continue
                date = row[d_idx].strip()
                tickers_blob = row[t_idx].strip().strip('"')
                tickers = [t.strip() for t in tickers_blob.split(",") if t.strip()]
                for t in tickers:
                    by_date[date].add(t)
        else:
            raise SystemExit(
                f"Unrecognised CSV header {header!r}. Expected (date, ticker) "
                f"or (date, tickers)."
            )
    return {d: sorted(t) for d, t in by_date.items()}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data_store/sp500_constituents"),
    )
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"Source not found: {args.source}", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)

    by_date = _parse_csv(args.source)
    if not by_date:
        print("No (date, ticker) rows parsed from source.", file=sys.stderr)
        return 3

    for date, tickers in by_date.items():
        (args.output / f"{date}.json").write_text(json.dumps(tickers, indent=2))

    index = {d: t for d, t in sorted(by_date.items())}
    (args.output / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True))
    print(f"Wrote {len(by_date)} dated snapshots to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
