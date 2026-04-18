"""Python-side memory-mapped file writer for the C++ kill switch watchdog.

Writes VaR and drawdown to a memory-mapped file at 10Hz so the C++ daemon
can monitor risk independently of the Python GIL.
"""
from __future__ import annotations

import asyncio
import mmap
import struct
import tempfile
from pathlib import Path

from typing import Callable

from shared.config import settings
from shared.logger import get_logger
from shared.types import RiskSnapshot

log = get_logger("killswitch")

_MMAP_PATH = Path(tempfile.gettempdir()) / "ayvid_risk.mmap"
_STRUCT_FORMAT = "=ddd?"  # var_95, drawdown_pct, max_drawdown_pct, kill_armed
_STRUCT_SIZE = struct.calcsize(_STRUCT_FORMAT)
_mmap_file = None
_mm = None
_running = False


def initialize_mmap() -> Path:
    """Create and initialize the memory-mapped risk file."""
    global _mmap_file, _mm

    _MMAP_PATH.write_bytes(b"\x00" * _STRUCT_SIZE)
    _mmap_file = open(_MMAP_PATH, "r+b")
    _mm = mmap.mmap(_mmap_file.fileno(), _STRUCT_SIZE)

    log.info(f"Kill switch mmap initialized at {_MMAP_PATH}")
    return _MMAP_PATH


def write_risk_snapshot(snapshot: RiskSnapshot) -> None:
    """Write current risk metrics to the memory-mapped file."""
    if _mm is None:
        return

    data = struct.pack(
        _STRUCT_FORMAT,
        snapshot.value_at_risk_95,
        snapshot.current_drawdown_pct,
        snapshot.max_drawdown_pct,
        snapshot.kill_switch_armed,
    )
    _mm.seek(0)
    _mm.write(data)
    _mm.flush()


def read_risk_snapshot() -> dict[str, float | bool]:
    """Read current risk metrics from the memory-mapped file."""
    if _mm is None:
        return {}

    _mm.seek(0)
    data = _mm.read(_STRUCT_SIZE)
    var_95, drawdown, max_dd, armed = struct.unpack(_STRUCT_FORMAT, data)
    return {
        "value_at_risk_95": var_95,
        "current_drawdown_pct": drawdown,
        "max_drawdown_pct": max_dd,
        "kill_switch_armed": armed,
    }


def check_kill_switch(snapshot: RiskSnapshot) -> bool:
    """Check if the software kill switch should fire.

    Returns True if the drawdown exceeds the configured threshold.
    """
    if snapshot.current_drawdown_pct >= settings.MAX_DRAWDOWN_PCT:
        log.critical(
            f"KILL SWITCH TRIGGERED: drawdown {snapshot.current_drawdown_pct:.2f}% "
            f">= threshold {settings.MAX_DRAWDOWN_PCT:.2f}%"
        )
        return True
    return False


def _risk_writer_thread(
    get_snapshot: Callable[[], RiskSnapshot],
    interval: float = 0.1,
) -> None:
    """Dedicated thread for mmap writes — immune to event loop starvation.

    The asyncio event loop can stall for seconds during heavy LangGraph
    operations. If this writer ran as a coroutine, the mmap timestamp
    would freeze and the C++ watchdog would false-trigger a SIGKILL.
    Running on a dedicated OS thread guarantees 10Hz updates regardless
    of event loop pressure.
    """
    import time as _time

    if _mm is None:
        initialize_mmap()

    log.info("Risk writer thread started (10Hz, dedicated thread)")
    while _running:
        try:
            snapshot = get_snapshot()
            write_risk_snapshot(snapshot)
        except Exception as exc:
            log.error(f"Risk writer error: {exc}")
        _time.sleep(interval)
    log.info("Risk writer thread stopped")


_writer_thread = None


async def risk_writer_loop(
    get_snapshot: Callable[[], RiskSnapshot],
    interval: float = 0.1,
) -> None:
    """Start the mmap risk writer on a dedicated OS thread.

    Kept as an async function for API compatibility, but the actual
    writing runs on a daemon thread immune to event loop stalls.
    The kill switch check still runs on the event loop so it can
    raise into the asyncio exception handler.
    """
    import threading as _threading
    global _running, _writer_thread
    _running = True

    if _mm is None:
        initialize_mmap()

    _writer_thread = _threading.Thread(
        target=_risk_writer_thread,
        args=(get_snapshot, interval),
        daemon=True,
        name="ayvid-risk-writer",
    )
    _writer_thread.start()

    while _running:
        try:
            snapshot = get_snapshot()
            if check_kill_switch(snapshot):
                from shared.exceptions import KillSwitchTriggered
                raise KillSwitchTriggered(
                    snapshot.current_drawdown_pct,
                    settings.MAX_DRAWDOWN_PCT,
                )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            if "KILL SWITCH" in str(exc):
                raise
        await asyncio.sleep(1.0)


def stop_writer() -> None:
    """Stop the risk writer loop."""
    global _running
    _running = False


def cleanup() -> None:
    """Clean up mmap resources."""
    global _mm, _mmap_file
    if _mm:
        _mm.close()
        _mm = None
    if _mmap_file:
        _mmap_file.close()
        _mmap_file = None
    if _MMAP_PATH.exists():
        _MMAP_PATH.unlink()
