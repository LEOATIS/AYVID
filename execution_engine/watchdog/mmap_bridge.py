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
        seq_odd = struct.pack(_SEQ_FORMAT, odd_seq)
        seq_even = struct.pack(_SEQ_FORMAT, even_seq)

        # Canonical seqlock write protocol. Each step is a single
        # 8-byte-aligned slice assignment so the seq counter writes are
        # byte-atomic on x86-64 / aarch64. flush() between phases pushes
        # the previous step's bytes through the kernel page cache before
        # the next phase becomes visible.
        #
        #   1. seq_begin := odd     ("write in progress" marker)
        #   2. payload   := new
        #   3. seq_end   := even    (footer is now consistent w/ payload)
        #   4. seq_begin := even    (commit; readers see begin==end and
        #                            the new payload)
        #
        # Worst observable intermediate states:
        #   - (begin=odd, end=old_even, payload=old):    reader retries
        #     because begin is odd.
        #   - (begin=odd, end=old_even, payload=new):    same.
        #   - (begin=odd, end=even,     payload=new):    same.
        #   - (begin=even, end=even,    payload=new):    consistent commit.
        # No path can yield a torn read where the C++ side sees begin=end
        # but the payload is half-old / half-new, because the begin store
        # happens *after* the payload and footer are fully visible.
        # Note: mmap.flush(offset, size) requires page-aligned offsets
        # on macOS / BSD; passing the full-map flush (no args) keeps the
        # call portable. The kernel guarantees ordering of stores into
        # the same page, which is what we rely on between phases.
        self._mm[0:_SEQ_SIZE] = seq_odd
        self._mm.flush()
        self._mm[_SEQ_SIZE:_SEQ_SIZE + _PAYLOAD_SIZE] = payload
        self._mm.flush()
        self._mm[_SEQ_SIZE + _PAYLOAD_SIZE:_STRUCT_SIZE] = seq_even
        self._mm.flush()
        self._mm[0:_SEQ_SIZE] = seq_even
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
            dd,
            v95,
            v99,
            equity,
            peak,
            ts_us,
            pid,
            armed,
            max_dd,
            max_var,
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


def _libcurl_available() -> bool:
    """Best-effort probe for libcurl headers / library at compile time.

    We try the canonical ``curl-config --libs`` route first, then fall
    back to checking /usr/include/curl/curl.h (Linux) and the Homebrew
    prefix on macOS. When libcurl is absent the C++ watchdog still
    compiles (via the __has_include guard) but loses the step-2 IB REST
    flatten path — it will SIGKILL Python only.
    """
    try:
        rc = subprocess.run(
            ["curl-config", "--libs"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if rc.returncode == 0 and "-lcurl" in rc.stdout:
            return True
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    for probe in (
        Path("/usr/include/curl/curl.h"),
        Path("/usr/local/include/curl/curl.h"),
        Path("/opt/homebrew/include/curl/curl.h"),
    ):
        if probe.exists():
            return True
    return False


def _compile_if_needed() -> Path:
    if _CPP_BINARY.exists() and _CPP_BINARY.stat().st_mtime >= _CPP_SOURCE.stat().st_mtime:
        return _CPP_BINARY
    have_curl = _libcurl_available()
    log.info(
        f"[bold]compiling[/bold] kill_switch.cpp (libcurl={'yes' if have_curl else 'NO — REST flatten disabled'}) ..."
    )
    cmd = [
        "g++",
        "-O2",
        "-std=c++17",
        "-o",
        str(_CPP_BINARY),
        str(_CPP_SOURCE),
    ]
    if have_curl:
        try:
            curl_cflags = (
                subprocess.run(
                    ["curl-config", "--cflags"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                .stdout.strip()
                .split()
            )
            curl_libs = (
                subprocess.run(
                    ["curl-config", "--libs"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                .stdout.strip()
                .split()
            )
            cmd[1:1] = [f for f in curl_cflags if f]
            cmd.extend(f for f in curl_libs if f)
        except (FileNotFoundError, subprocess.SubprocessError):
            cmd.append("-lcurl")
    cmd.append("-lpthread")
    subprocess.check_call(cmd, cwd=str(_CPP_SOURCE.parent))
    log.info(f"[bold]compiled[/bold] → {_CPP_BINARY}")
    return _CPP_BINARY


def _ib_flatten_env() -> dict[str, str]:
    """Build the env passed to the watchdog process so its REST flatten
    knows where and how to reach IB Client Portal.

    Shipped as env (not argv) because the cookie is a secret and argv is
    visible to every UID on the machine.
    """
    from shared.config import settings

    env = dict(os.environ)
    gw_url = getattr(settings, "IB_CLIENT_PORTAL_URL", None) or os.environ.get(
        "AYVID_IB_GATEWAY_URL",
        "https://localhost:5000/v1/api",
    )
    env["AYVID_IB_GATEWAY_URL"] = gw_url
    if settings.IB_ACCOUNT:
        env["AYVID_IB_ACCOUNT_ID"] = settings.IB_ACCOUNT
    cafile = getattr(settings, "IB_CLIENT_PORTAL_CAFILE", None)
    if cafile:
        env["AYVID_IB_CAFILE"] = cafile
    cookie = getattr(settings, "IB_CLIENT_PORTAL_COOKIE", None)
    if cookie:
        env["AYVID_IB_SESSION_COOKIE"] = cookie
    return env


def start_watchdog(bridge: RiskMmapBridge) -> subprocess.Popen[bytes]:
    binary = _compile_if_needed()
    proc = subprocess.Popen(
        [str(binary), str(bridge._path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=_ib_flatten_env(),
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
