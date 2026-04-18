"""QuestDB writer for high-throughput OHLCV tick storage.

Uses the InfluxDB Line Protocol (ILP) over TCP for maximum ingestion speed.
Falls back to local Parquet storage when QuestDB is unavailable.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import socket
from pathlib import Path

from shared.config import settings
from shared.exceptions import QuestDBWriteError
from shared.logger import get_logger
from shared.types import OHLCVBar

log = get_logger("data.questdb")

_LOCAL_STORAGE = Path("data_store/ohlcv")
_LOCAL_STORAGE.mkdir(parents=True, exist_ok=True)


def _bar_to_ilp(bar: OHLCVBar) -> str:
    """Convert an OHLCV bar to InfluxDB Line Protocol format."""
    ts_ns = int(bar.timestamp.timestamp() * 1_000_000_000)
    return (
        f"ohlcv,symbol={bar.symbol} "
        f"open={bar.open},high={bar.high},low={bar.low},"
        f"close={bar.close},volume={bar.volume}i "
        f"{ts_ns}\n"
    )


async def write_bars_questdb(bars: list[OHLCVBar]) -> int:
    """Write OHLCV bars to local JSONL (primary) and QuestDB via ILP TCP (secondary).

    Local JSONL is always written first so the backtester always has data
    regardless of whether QuestDB is reachable.
    """
    if not bars:
        return 0

    local_count = await _write_bars_local(bars)

    payload = "".join(_bar_to_ilp(b) for b in bars)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        await asyncio.to_thread(
            sock.connect, (settings.QUESTDB_HOST, settings.QUESTDB_PORT)
        )
        await asyncio.to_thread(sock.sendall, payload.encode())
        sock.close()
        log.info(f"Wrote {len(bars)} bars to QuestDB (+ local)")
    except (ConnectionRefusedError, OSError) as exc:
        log.debug(f"QuestDB unavailable (local write succeeded): {exc}")

    return local_count


async def _write_bars_local(bars: list[OHLCVBar]) -> int:
    """Fallback: persist bars as JSON to local storage."""
    if not bars:
        return 0

    symbol = bars[0].symbol
    path = _LOCAL_STORAGE / f"{symbol}.jsonl"

    lines = [json.dumps(bar.model_dump(), default=str) + "\n" for bar in bars]
    await asyncio.to_thread(path.open("a").writelines, lines)
    log.info(f"Wrote {len(bars)} bars to local store: {path}")
    return len(bars)


async def read_local_bars(symbol: str) -> list[OHLCVBar]:
    """Read bars from local JSONL fallback storage."""
    path = _LOCAL_STORAGE / f"{symbol}.jsonl"
    if not path.exists():
        return []

    bars: list[OHLCVBar] = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            bars.append(OHLCVBar.model_validate_json(line))
    return bars


_MAX_BARS_PER_FILE = 5000
_ARCHIVE_DIR = Path("data_store/ohlcv_archive")


async def rotate_local_storage(max_bars: int = _MAX_BARS_PER_FILE) -> dict[str, int]:
    """Rotate JSONL files that exceed max_bars by archiving old data.

    Keeps the most recent `max_bars` entries and moves the rest to
    data_store/ohlcv_archive/{symbol}_{timestamp}.jsonl.
    Returns dict of {symbol: bars_archived}.
    """
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    rotated: dict[str, int] = {}

    for path in _LOCAL_STORAGE.glob("*.jsonl"):
        lines = path.read_text().strip().split("\n")
        if len(lines) <= max_bars:
            continue

        symbol = path.stem
        archive_lines = lines[:-max_bars]
        keep_lines = lines[-max_bars:]

        ts = dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")
        archive_path = _ARCHIVE_DIR / f"{symbol}_{ts}.jsonl"
        archive_path.write_text("\n".join(archive_lines) + "\n")

        path.write_text("\n".join(keep_lines) + "\n")
        rotated[symbol] = len(archive_lines)
        log.info(f"Rotated {symbol}: archived {len(archive_lines)} bars, kept {len(keep_lines)}")

    return rotated


async def query_questdb(sql: str) -> list[dict]:
    """Execute a SQL query against QuestDB's HTTP API."""
    import httpx

    url = f"http://{settings.QUESTDB_HOST}:{settings.QUESTDB_HTTP_PORT}/exec"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"query": sql})
            resp.raise_for_status()
            data = resp.json()
            columns = [c["name"] for c in data.get("columns", [])]
            rows = data.get("dataset", [])
            return [dict(zip(columns, row)) for row in rows]
    except Exception as exc:
        raise QuestDBWriteError(f"QuestDB query failed: {exc}") from exc
