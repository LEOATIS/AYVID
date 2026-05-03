"""Tests for the live-quote feed.

The fetcher itself can't be unit-tested without hitting yfinance, but
the wrappers around it — symbol mapping, TTL cache, failure-to-None —
are pure logic and need to be locked down because the readiness
checker leans on this file every cycle.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from data_primitives.extractors.live_quote import (
    _QUOTE_CACHE_TTL_S,
    _reset_cache_for_tests,
    fetch_live_quote,
    to_yahoo_symbol,
)


@pytest.fixture(autouse=True)
def _clear_quote_cache():
    """Cache leaks between cases would mask broken cache-key logic."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


class TestSymbolMapping:
    @pytest.mark.parametrize(
        "ticker,asset_class,expected",
        [
            # Equities / ETFs / indices pass through.
            ("AAPL", "EQUITY", "AAPL"),
            ("SPY", "ETF", "SPY"),
            ("^GSPC", "INDEX", "^GSPC"),
            ("TSM", "EQUITY", "TSM"),
            ("KLAC", "EQUITY", "KLAC"),
            # Crypto gets the -USD suffix.
            ("BTC", "CRYPTO", "BTC-USD"),
            ("ETH", "CRYPTO", "ETH-USD"),
            # Already-shaped crypto is idempotent.
            ("BTC-USD", "CRYPTO", "BTC-USD"),
            # FX gets the =X suffix.
            ("USDJPY", "FX", "USDJPY=X"),
            ("EURUSD", "FX", "EURUSD=X"),
            # Already-shaped FX is idempotent.
            ("USDJPY=X", "FX", "USDJPY=X"),
            # Futures resolve via the explicit map first.
            ("GC", "FUTURE", "GC=F"),
            ("NQ", "FUTURE", "NQ=F"),
            ("CL", "COMMODITY", "CL=F"),
            # Unmapped futures fall back to {ticker}=F.
            ("XYZ", "FUTURE", "XYZ=F"),
            # Already-shaped futures are idempotent.
            ("GC=F", "FUTURE", "GC=F"),
            # Asset class is case-insensitive.
            ("BTC", "crypto", "BTC-USD"),
            ("USDJPY", "fx", "USDJPY=X"),
            # Empty / whitespace ticker just round-trips empty.
            ("", "EQUITY", ""),
            ("  ", "EQUITY", ""),
        ],
    )
    def test_to_yahoo_symbol(self, ticker, asset_class, expected):
        assert to_yahoo_symbol(ticker, asset_class) == expected


class TestFetchLiveQuote:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_returns_price_on_success(self):
        with patch(
            "data_primitives.extractors.live_quote._yf_lookup",
            return_value=78_389.54,
        ) as mock:
            price = asyncio.run(fetch_live_quote("BTC", "CRYPTO"))
        assert price == 78_389.54
        mock.assert_called_once_with("BTC-USD")

    def test_cache_hit_avoids_second_wire_call(self):
        with patch(
            "data_primitives.extractors.live_quote._yf_lookup",
            return_value=100.0,
        ) as mock:
            asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
            asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
            asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
        assert mock.call_count == 1, (
            "TTL cache should coalesce three lookups in <30s into one call"
        )

    def test_cache_expires_after_ttl(self):
        # First call lands at t=0; second call at t=TTL+1 must re-fetch.
        # fetch_live_quote calls time.time() exactly once per invocation,
        # so the side_effect list has exactly two entries.
        with patch(
            "data_primitives.extractors.live_quote._yf_lookup",
            side_effect=[100.0, 110.0],
        ) as mock:
            with patch(
                "data_primitives.extractors.live_quote.time.time",
                side_effect=[0.0, _QUOTE_CACHE_TTL_S + 1.0],
            ):
                first = asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
                second = asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
        assert first == 100.0
        assert second == 110.0
        assert mock.call_count == 2

    def test_yfinance_failure_returns_none(self):
        with patch(
            "data_primitives.extractors.live_quote._yf_lookup",
            return_value=None,
        ):
            price = asyncio.run(fetch_live_quote("BAD_SYM", "EQUITY"))
        assert price is None

    def test_zero_or_negative_price_returns_none(self):
        # `_yf_lookup` already filters non-positive prices but the
        # wrapper has its own guard — a sloppy edit upstream shouldn't
        # let zero quotes leak into the readiness checker (they'd
        # immediately mark every signal WAITING with reason
        # "price_unavailable" and then *flip* it once a non-zero quote
        # arrived, churning the SignalBook persistence loop).
        with patch(
            "data_primitives.extractors.live_quote._yf_lookup",
            return_value=0.0,
        ):
            price = asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
        assert price is None

    def test_empty_ticker_short_circuits(self):
        with patch(
            "data_primitives.extractors.live_quote._yf_lookup",
        ) as mock:
            price = asyncio.run(fetch_live_quote("", "EQUITY"))
        assert price is None
        mock.assert_not_called()

    def test_failure_does_not_poison_cache(self):
        # A None lookup must not get cached — otherwise a transient
        # 429 from yfinance would lock the readiness checker out of
        # live prices for the full TTL window.
        with patch(
            "data_primitives.extractors.live_quote._yf_lookup",
            side_effect=[None, 100.0],
        ) as mock:
            first = asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
            second = asyncio.run(fetch_live_quote("AAPL", "EQUITY"))
        assert first is None
        assert second == 100.0
        assert mock.call_count == 2


class TestYfLookupContractSurface:
    """The synchronous _yf_lookup is what actually hits yfinance. We
    can't test the integration without a network round-trip, but we
    *can* lock down its failure contract: every exception type yfinance
    is known to raise (HTTPError, KeyError, ValueError, AttributeError)
    must produce ``None`` rather than propagate.
    """

    @pytest.mark.parametrize(
        "raised",
        [
            ConnectionError("DNS failure"),
            TimeoutError("read timeout"),
            ValueError("bad symbol"),
            KeyError("last_price"),
            AttributeError("fast_info"),
            RuntimeError("yfinance internal"),
        ],
    )
    def test_yf_lookup_swallows_all_exceptions(self, raised):
        from data_primitives.extractors import live_quote

        # We patch yfinance as imported INSIDE _yf_lookup. The function
        # does ``import yfinance as yf`` lazily so we patch the module
        # attribute via a context manager that swaps the import target.
        class _FakeTicker:
            @property
            def fast_info(self):
                raise raised

        class _FakeYF:
            Ticker = _FakeTicker

        with patch.dict("sys.modules", {"yfinance": _FakeYF}):
            assert live_quote._yf_lookup("AAPL") is None
