"""Shared pytest fixtures for AYVID test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LOG_LEVEL", "WARNING")


# ── Test isolation: redirect signal-book persistence to tmp ──────────
# Without this autouse fixture, any test that calls ``book.save_to_disk()``
# without a path argument writes into the real
# ``data_store/signal_book.json`` and pollutes the live runtime book —
# which is exactly how an "NVDA @ $150" fixture row leaked into the
# deployed signal book and showed up in the demo console.
#
# We monkeypatch the module-level path constants so ``save_to_disk``
# and ``_append_history`` both land inside a per-session tmp dir. The
# real files are untouched.
@pytest.fixture(autouse=True)
def _isolate_signal_book_paths(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    sandbox = tmp_path_factory.mktemp("signal_book_sandbox")
    monkeypatch.setattr(
        "execution_engine.signal_book._BOOK_PATH",
        sandbox / "signal_book.json",
        raising=False,
    )
    monkeypatch.setattr(
        "execution_engine.signal_book._HISTORY_PATH",
        sandbox / "signal_history.jsonl",
        raising=False,
    )
    return sandbox


@pytest.fixture
def sample_returns():
    import numpy as np

    np.random.seed(42)
    return np.random.normal(0.0005, 0.02, 252)


@pytest.fixture
def sample_portfolio():
    from shared.types import PortfolioState

    return PortfolioState(
        cash=900_000.0,
        total_equity=1_000_000.0,
        peak_equity=1_050_000.0,
    )
