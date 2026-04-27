"""Thread / process pool discipline + plane-bifurcation queue.

The architectural rule ("THE ONE RULE" in ``broker/connection.py``):
the IB asyncio event loop must NEVER be blocked. Every synchronous
operation with non-trivial latency — network I/O that ib_async can't
hand back to us, NumPy reductions over big arrays, matplotlib renders,
LLM calls that internally spawn sub-loops, heavy Neo4j writes — must
hop off the IB loop into a pool we own.

This module exposes three pools with distinct sizing + semantics:

* ``io_pool``       — blocking I/O (HTTP, Neo4j, Qdrant, QuestDB writes)
* ``llm_pool``      — LLM calls (effectively I/O-bound over HTTPS, but
                      kept separate so a flood of graph writes can't
                      starve a supervisor reasoning step)
* ``cpu_pool``      — CPU-bound numerics (backtest replay, matplotlib,
                      large NumPy / Polars operations). A ``ProcessPool``
                      rather than ``ThreadPool`` so the GIL is bypassed.

Plus a single ``PlaneBifurcationQueue`` pair:

* ``ingestion_plane``  — producers: news extractors, graph refinery,
                         filing analyzer. Consumers: orchestrator.
* ``execution_plane``  — producers: orchestrator. Consumers: IB
                         routing + risk writer.

The point of the queue pair is that a slow ingestion producer (e.g.
GPT-4o extraction over a 50k-char 10-K) can never back-pressure the
execution plane — they sit in different asyncio.Queues with independent
buffer sizes, and the IB routing loop only awaits ``execution_plane``.
"""

from __future__ import annotations

import asyncio
import atexit
import functools
import os
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from shared.logger import get_logger

log = get_logger("executors")

T = TypeVar("T")


# ── Pool sizing heuristics ───────────────────────────────────


def _cpu_count() -> int:
    return os.cpu_count() or 4


# Keep conservative defaults; each can be overridden via env so operators
# can tune under production load.
_IO_WORKERS = int(os.environ.get("AYVID_IO_POOL", max(8, _cpu_count() * 4)))
_LLM_WORKERS = int(
    os.environ.get("AYVID_LLM_POOL", 16)
)  # OpenAI / Anthropic have their own rate-limit planes
_CPU_WORKERS = int(os.environ.get("AYVID_CPU_POOL", max(2, _cpu_count() - 1)))


# ── Module singletons (lazy + thread-safe) ───────────────────

_pools_lock = threading.Lock()
_io_pool: ThreadPoolExecutor | None = None
_llm_pool: ThreadPoolExecutor | None = None
_cpu_pool: ProcessPoolExecutor | None = None


def io_pool() -> ThreadPoolExecutor:
    global _io_pool
    if _io_pool is None:
        with _pools_lock:
            if _io_pool is None:
                _io_pool = ThreadPoolExecutor(
                    max_workers=_IO_WORKERS,
                    thread_name_prefix="ayvid-io",
                )
                log.info(f"io_pool: {_IO_WORKERS} threads")
    return _io_pool


def llm_pool() -> ThreadPoolExecutor:
    global _llm_pool
    if _llm_pool is None:
        with _pools_lock:
            if _llm_pool is None:
                _llm_pool = ThreadPoolExecutor(
                    max_workers=_LLM_WORKERS,
                    thread_name_prefix="ayvid-llm",
                )
                log.info(f"llm_pool: {_LLM_WORKERS} threads")
    return _llm_pool


def cpu_pool() -> ProcessPoolExecutor:
    global _cpu_pool
    if _cpu_pool is None:
        with _pools_lock:
            if _cpu_pool is None:
                # ProcessPool bypasses the GIL — critical for NumPy-heavy
                # backtest replay and matplotlib rendering paths. Lazy
                # import because ProcessPool forks eagerly on creation
                # and we don't want that cost if the caller never uses
                # the CPU plane.
                _cpu_pool = ProcessPoolExecutor(max_workers=_CPU_WORKERS)
                log.info(f"cpu_pool: {_CPU_WORKERS} processes")
    return _cpu_pool


# ── Coroutine-friendly dispatch helpers ──────────────────────


async def run_in_io(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking I/O call on the dedicated I/O thread pool."""
    loop = asyncio.get_running_loop()
    if kwargs:
        func = functools.partial(func, **kwargs)
    return await loop.run_in_executor(io_pool(), func, *args)


async def run_in_llm(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous LLM SDK call on the dedicated LLM thread pool.

    Many LLM SDKs have async variants we prefer; this wrapper is for the
    occasional call that can only be made synchronously without blocking
    the IB loop (e.g. tiktoken encoding, local embedding backends).
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        func = functools.partial(func, **kwargs)
    return await loop.run_in_executor(llm_pool(), func, *args)


async def run_in_cpu(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a CPU-bound function on the process pool (GIL bypass).

    The function AND its arguments must be picklable. Use this for
    NumPy-heavy reductions, matplotlib rendering, or Polars frame ops
    that otherwise would block for >100ms at a time.
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        func = functools.partial(func, **kwargs)
    return await loop.run_in_executor(cpu_pool(), func, *args)


# ── Plane-bifurcation queues ─────────────────────────────────


@dataclass
class _QueueConfig:
    name: str
    maxsize: int


# Ingestion plane buffers an order of magnitude more items than execution
# — news extractors burst; orders do not.
INGESTION_CFG = _QueueConfig(name="ingestion", maxsize=10_000)
EXECUTION_CFG = _QueueConfig(name="execution", maxsize=256)


class PlaneBifurcationQueue(Generic[T]):
    """Bounded asyncio queue tagged with a plane name for observability.

    Writes block / drop-oldest based on ``overflow_policy``:

    * ``"block"``       — ``put`` awaits until there's capacity
    * ``"drop_oldest"`` — silently pops one item from the head before
                          inserting. Used on the ingestion plane: we'd
                          rather lose a stale news item than pile up
                          unbounded memory.
    * ``"reject"``      — ``put`` raises ``asyncio.QueueFull``. Used on
                          the execution plane so a missed order becomes
                          a loud error, not a silent drop.
    """

    def __init__(
        self,
        cfg: _QueueConfig,
        overflow_policy: str = "block",
    ) -> None:
        self._cfg = cfg
        self._policy = overflow_policy
        self._q: asyncio.Queue[T] = asyncio.Queue(maxsize=cfg.maxsize)
        self._stats = {"put": 0, "get": 0, "dropped": 0}

    async def put(self, item: T) -> None:
        if self._policy == "block":
            await self._q.put(item)
        elif self._policy == "drop_oldest":
            try:
                self._q.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    _ = self._q.get_nowait()
                    self._stats["dropped"] += 1
                except asyncio.QueueEmpty:
                    pass
                await self._q.put(item)
        elif self._policy == "reject":
            self._q.put_nowait(item)  # raises QueueFull on overflow
        else:
            raise ValueError(f"Unknown overflow_policy: {self._policy}")
        self._stats["put"] += 1

    async def get(self) -> T:
        item = await self._q.get()
        self._stats["get"] += 1
        return item

    def qsize(self) -> int:
        return self._q.qsize()

    def stats(self) -> dict[str, Any]:
        return {
            "plane": self._cfg.name,
            "size": self.qsize(),
            "maxsize": self._cfg.maxsize,
            "policy": self._policy,
            **self._stats,
        }


# Module-level singletons — the orchestrator and the IB routing loop
# import these directly. They're created lazily so tests can build their
# own loops without polluting global state.
_ingestion_plane: PlaneBifurcationQueue | None = None
_execution_plane: PlaneBifurcationQueue | None = None
_planes_lock = threading.Lock()


def ingestion_plane() -> PlaneBifurcationQueue:
    global _ingestion_plane
    if _ingestion_plane is None:
        with _planes_lock:
            if _ingestion_plane is None:
                _ingestion_plane = PlaneBifurcationQueue(
                    INGESTION_CFG,
                    overflow_policy="drop_oldest",
                )
                log.info(f"ingestion_plane: maxsize={INGESTION_CFG.maxsize} drop_oldest")
    return _ingestion_plane


def execution_plane() -> PlaneBifurcationQueue:
    global _execution_plane
    if _execution_plane is None:
        with _planes_lock:
            if _execution_plane is None:
                _execution_plane = PlaneBifurcationQueue(
                    EXECUTION_CFG,
                    overflow_policy="reject",
                )
                log.info(f"execution_plane: maxsize={EXECUTION_CFG.maxsize} reject-on-full")
    return _execution_plane


# ── Deadline guard for IB-loop code paths ────────────────────


async def with_ib_deadline(
    coro: Awaitable[T],
    deadline_ms: int = 500,
    op_name: str = "ib_call",
) -> T:
    """Wrap a coroutine that runs on the IB loop with a hard deadline.

    If the coro exceeds ``deadline_ms`` the watchdog logs loudly; that
    is how we catch the "LangGraph reasoning blocked IB for >heartbeat"
    regression described in the PDF. The deadline is advisory — we do
    NOT cancel the coroutine (that would break mid-write), we just flag
    it so an ops alert can fire.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        return await coro
    finally:
        elapsed_ms = (loop.time() - start) * 1000
        if elapsed_ms > deadline_ms:
            log.warning(
                f"IB-loop deadline breach: {op_name} took {elapsed_ms:.1f}ms "
                f"(budget {deadline_ms}ms). Move it to io_pool / llm_pool."
            )


# ── Shutdown hook ────────────────────────────────────────────


def shutdown() -> None:
    """Cleanly shut every pool down. Idempotent."""
    global _io_pool, _llm_pool, _cpu_pool
    with _pools_lock:
        if _io_pool is not None:
            _io_pool.shutdown(wait=False, cancel_futures=True)
            _io_pool = None
        if _llm_pool is not None:
            _llm_pool.shutdown(wait=False, cancel_futures=True)
            _llm_pool = None
        if _cpu_pool is not None:
            _cpu_pool.shutdown(wait=False, cancel_futures=True)
            _cpu_pool = None


atexit.register(shutdown)
