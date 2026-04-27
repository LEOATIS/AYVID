"""QuestDB writer for high-throughput OHLCV tick storage.

Uses the InfluxDB Line Protocol (ILP) over TCP for maximum ingestion
speed, with a Polars-vectorised payload builder (`bars_to_ilp_polars`)
so we don't burn cycles in per-bar f-string concatenation. Local JSONL
is the durable fallback when QuestDB is unreachable.
"""

from __future__ import annotations

import datetime as dt
import socket
from pathlib import Path

from shared.config import settings
from shared.exceptions import QuestDBWriteError
from shared.executors import run_in_io
from shared.logger import get_logger
from shared.types import OHLCVBar

log = get_logger("data.questdb")

_LOCAL_STORAGE = Path("data_store/ohlcv")
_LOCAL_STORAGE.mkdir(parents=True, exist_ok=True)

# P2-23: health tracker for the TSDB sink.
# Downstream callers (and eventually `/health/data`) can read this to
# distinguish "QuestDB has never worked" from "QuestDB was working and
# is now stale". Previously the ILP failure path only emitted a
# log.debug line — operators had no way to tell their TSDB was behind.
_writer_health: dict[str, dt.datetime | str | int | None] = {
    "last_success_at": None,
    "last_failure_at": None,
    "last_failure_reason": None,
    "consecutive_failures": 0,
}


def get_questdb_writer_health() -> dict[str, dt.datetime | str | int | None]:
    """Snapshot of the QuestDB writer's last-known state."""
    return dict(_writer_health)


async def write_bars_questdb(bars: list[OHLCVBar]) -> int:
    """Write OHLCV bars to local JSONL (primary) and QuestDB via ILP TCP (secondary).

    Local JSONL is always written first so the backtester always has data
    regardless of whether QuestDB is reachable. Both the JSONL append and
    the QuestDB ILP build go through Polars when available.

    P2-23: ILP failures now emit at log.warning (was log.debug), and
    the writer-health dict is updated so operators can see staleness
    via ``get_questdb_writer_health()``.
    """
    if not bars:
        return 0

    local_count = await _write_bars_local(bars)

    from data_primitives.loaders.polars_loader import bars_to_ilp_polars

    payload = await run_in_io(bars_to_ilp_polars, bars)
    if not payload:
        return local_count

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        await run_in_io(sock.connect, (settings.QUESTDB_HOST, settings.QUESTDB_PORT))
        await run_in_io(sock.sendall, payload.encode())
        sock.close()
        prior_failures = int(_writer_health["consecutive_failures"] or 0)
        if prior_failures:
            log.info(
                f"QuestDB ILP recovered after {prior_failures} consecutive "
                f"failures — resumed TSDB writes ({len(bars)} bars)."
            )
        else:
            log.info(f"Wrote {len(bars)} bars to QuestDB (+ local)")
        _writer_health["last_success_at"] = dt.datetime.now(dt.UTC)
        _writer_health["consecutive_failures"] = 0
    except (ConnectionRefusedError, OSError) as exc:
        _writer_health["last_failure_at"] = dt.datetime.now(dt.UTC)
        _writer_health["last_failure_reason"] = str(exc)[:200]
        prior = int(_writer_health["consecutive_failures"] or 0)
        _writer_health["consecutive_failures"] = prior + 1
        # Noise control: surface the FIRST failure at WARN with full
        # context so operators notice. Subsequent failures while the
        # TSDB is still down downgrade to DEBUG — 32 identical warns
        # per 15-minute cycle was drowning real signal in the log.
        # The health dict still counts every failure for /system-status
        # to report accurately.
        if prior == 0:
            log.warning(
                f"QuestDB ILP write failed (local write succeeded, TSDB "
                f"will be stale until recovery): {exc}. "
                f"Further failures in this downtime window will log at "
                f"DEBUG level; check /system-status for live counters."
            )
        else:
            log.debug(f"QuestDB ILP still offline (consecutive_failures={prior + 1}): {exc}")

    return local_count


async def _write_bars_local(bars: list[OHLCVBar]) -> int:
    """Persist bars to local JSONL via the Polars-backed appender."""
    if not bars:
        return 0
    symbol = bars[0].symbol
    from data_primitives.loaders.polars_loader import append_jsonl_polars

    written = await run_in_io(append_jsonl_polars, symbol, bars)
    log.info(f"Wrote {written} bars to local store: data_store/ohlcv/{symbol}.jsonl")
    return written


async def read_local_bars(symbol: str) -> list[OHLCVBar]:
    """Read bars from local JSONL via the Polars-backed loader."""
    from data_primitives.loaders.polars_loader import load_bars

    return await run_in_io(load_bars, symbol)


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
