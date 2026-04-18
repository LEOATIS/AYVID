from __future__ import annotations

import mmap
import os
import signal
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

from shared.logger import get_logger

log = get_logger("watchdog")

_MMAP_PATH_DEFAULT = "/tmp/ayvid_risk.mmap"

# Layout: seqlock_counter (uint64) + payload + seqlock_counter (uint64)
# The C++ reader must check that both counters match and are even.
# If they differ or are odd, a write was in-flight → retry.
_PAYLOAD_FORMAT = "=5d q 2i 2d"
_PAYLOAD_SIZE = struct.calcsize(_PAYLOAD_FORMAT)
_SEQ_FORMAT = "=Q"
_SEQ_SIZE = struct.calcsize(_SEQ_FORMAT)
_STRUCT_SIZE = _SEQ_SIZE + _PAYLOAD_SIZE + _SEQ_SIZE

_CPP_SOURCE = Path(__file__).parent / "kill_switch.cpp"
_CPP_BINARY = Path(__file__).parent / "kill_switch"


class RiskMmapBridge:
    """Read/write bridge to the shared-memory risk file consumed by the C++ watchdog.

    Uses a seqlock pattern to prevent the C++ reader from seeing a torn
    (partially updated) struct. The writer increments a sequence counter
    to an odd value before writing, then to the next even value after.
    The reader checks both the leading and trailing counter: if they
    differ or are odd, the read is discarded and retried.
    """

    def __init__(self, path: str = _MMAP_PATH_DEFAULT) -> None:
        self._path = Path(path)
        self._path.write_bytes(b"\x00" * _STRUCT_SIZE)
        self._fd = open(self._path, "r+b")
        self._mm = mmap.mmap(self._fd.fileno(), _STRUCT_SIZE)
        self._seq: int = 0
        log.info(f"[bold]mmap bridge[/bold] initialised at {self._path} ({_STRUCT_SIZE} bytes)")

    def update(
        self,
        drawdown_pct: float,
        var_95: float,
        var_99: float,
        portfolio_equity: float,
        peak_equity: float,
        *,
        armed: bool = True,
        max_drawdown_pct: float = 3.0,
        max_var_pct: float = 5.0,
    ) -> None:
        odd_seq = self._seq + 1
        even_seq = self._seq + 2
        payload = struct.pack(
            _PAYLOAD_FORMAT,
            drawdown_pct,
            var_95,
            var_99,
            portfolio_equity,
            peak_equity,
            int(time.time() * 1_000_000),
            os.getpid(),
            1 if armed else 0,
            max_drawdown_pct,
            max_var_pct,
        )
        frame = (
            struct.pack(_SEQ_FORMAT, odd_seq)
            + payload
            + struct.pack(_SEQ_FORMAT, even_seq)
        )
        self._mm.seek(0)
        self._mm.write(frame)
        self._mm.flush()
        self._mm.seek(0)
        self._mm.write(struct.pack(_SEQ_FORMAT, even_seq))
        self._mm.seek(_SEQ_SIZE + _PAYLOAD_SIZE)
        self._mm.write(struct.pack(_SEQ_FORMAT, even_seq))
        self._mm.flush()
        self._seq = even_seq

    def read(self) -> dict[str, Any]:
        """Read with seqlock validation. Returns empty dict on torn read."""
        self._mm.seek(0)
        raw = self._mm.read(_STRUCT_SIZE)
        seq_begin = struct.unpack_from(_SEQ_FORMAT, raw, 0)[0]
        seq_end = struct.unpack_from(_SEQ_FORMAT, raw, _SEQ_SIZE + _PAYLOAD_SIZE)[0]

        if seq_begin != seq_end or seq_begin % 2 != 0:
            return {}

        (
            dd, v95, v99, equity, peak,
            ts_us, pid, armed,
            max_dd, max_var,
        ) = struct.unpack_from(_PAYLOAD_FORMAT, raw, _SEQ_SIZE)
        return {
            "drawdown_pct": dd,
            "var_95": v95,
            "var_99": v99,
            "portfolio_equity": equity,
            "peak_equity": peak,
            "timestamp_us": ts_us,
            "python_pid": pid,
            "armed": bool(armed),
            "max_drawdown_pct": max_dd,
            "max_var_pct": max_var,
        }

    def close(self) -> None:
        if self._mm and not self._mm.closed:
            self._mm.close()
        if self._fd and not self._fd.closed:
            self._fd.close()
        if self._path.exists():
            self._path.unlink(missing_ok=True)
        log.info("[bold]mmap bridge[/bold] closed")

    def __enter__(self) -> RiskMmapBridge:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _compile_if_needed() -> Path:
    if _CPP_BINARY.exists() and _CPP_BINARY.stat().st_mtime >= _CPP_SOURCE.stat().st_mtime:
        return _CPP_BINARY
    log.info("[bold]compiling[/bold] kill_switch.cpp ...")
    subprocess.check_call(
        [
            "g++", "-O2", "-std=c++17",
            "-o", str(_CPP_BINARY),
            str(_CPP_SOURCE),
            "-lpthread",
        ],
        cwd=str(_CPP_SOURCE.parent),
    )
    log.info(f"[bold]compiled[/bold] → {_CPP_BINARY}")
    return _CPP_BINARY


def start_watchdog(bridge: RiskMmapBridge) -> subprocess.Popen[bytes]:
    binary = _compile_if_needed()
    proc = subprocess.Popen(
        [str(binary), str(bridge._path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    log.info(f"[bold]watchdog[/bold] started (PID {proc.pid})")
    return proc


def stop_watchdog(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        log.warning("[bold]watchdog[/bold] did not exit after SIGTERM — sending SIGKILL")
        proc.kill()
        proc.wait(timeout=2)
    log.info("[bold]watchdog[/bold] stopped")
