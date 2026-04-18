"""Shared pytest fixtures for AYVID test suite."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LOG_LEVEL", "WARNING")


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
