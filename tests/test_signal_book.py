"""Tests for the SignalBook fast-execute and regime-keyed cache semantics.

Covers the P0-A (shock-flag propagation) and P0-C (strict regime match +
unified override-timeout eviction) contracts. These tests are the
acceptance gate for the fast-path routing fix: if the fast-execute ever
drops the `tier3_shock` flag or the book ever returns a cross-regime
match, the broker router will silently TWAP a signal that should have
crossed the spread — the exact defect the audit caught.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from execution_engine.signal_book import PrecomputedSignal


def _make_signal(
    *,
    ticker: str = "TSM",
    direction: str = "LONG",
    regime_fingerprint: str = "fp_baseline",
    conviction: float = 0.8,
    tier3_shock: bool = False,
    entry_price_at_compute: float = 150.0,
    ttl_minutes: int = 30,
) -> PrecomputedSignal:
    from execution_engine.signal_book import PrecomputedSignal

    return PrecomputedSignal(
        ticker=ticker,
        direction=direction,
        instrument_type="EQUITY",
        conviction=conviction,
        strategy_name="causal_relative_value",
        strike=None,
        expiry_date="",
        stop_loss_pct=3.0,
        take_profit_pct=6.0,
        rationale="pytest signal",
        computed_at=dt.datetime.now(dt.UTC).isoformat(),
        ttl_minutes=ttl_minutes,
        regime_fingerprint=regime_fingerprint,
        expected_r=2.0,
        min_r=1.5,
        entry_price_at_compute=entry_price_at_compute,
        tier3_shock=tier3_shock,
    )


# ── P0-C: strict (ticker, regime) lookup ────────────────────────────


class TestSignalBookRegimeStrictLookup:
    """A pre-computed signal is only valid under the regime in which
    it was computed. The `key_any` cross-regime fallback removed as
    part of P0-C is a correctness invariant, not an optimisation."""

    def test_exact_regime_match_hits(self):
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.upsert(_make_signal(regime_fingerprint="fp_low_vol_uptrend"))

        hit = book.lookup("TSM", "fp_low_vol_uptrend")
        assert hit is not None
        assert hit.ticker == "TSM"
        assert hit.regime_fingerprint == "fp_low_vol_uptrend"

    def test_different_regime_is_a_miss_not_a_fallback(self):
        """The cross-regime fallback that the audit flagged as P0-C must
        be gone. A signal computed under `fp_low_vol_uptrend` must NOT
        surface to a caller asking for `fp_high_vol_downtrend` — trading
        under a rotated regime is the exact silent-bug vector we closed.
        """
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.upsert(_make_signal(regime_fingerprint="fp_low_vol_uptrend"))

        miss = book.lookup("TSM", "fp_high_vol_downtrend")
        assert miss is None, "cross-regime fallback leaked a stale-regime signal — P0-C regression"

        stats = book.get_stats()
        assert stats["misses"] >= 1

    def test_empty_regime_string_is_a_distinct_key(self):
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.upsert(_make_signal(regime_fingerprint="fp_real"))

        assert book.lookup("TSM", "") is None

    def test_stale_signal_is_evicted_on_lookup(self):
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.upsert(_make_signal(regime_fingerprint="fp_x", ttl_minutes=0))

        assert book.lookup("TSM", "fp_x") is None
        assert book.size() == 0


# ── P0-C: override-timeout eviction is unified with is_override_pending ──


class TestSignalBookOverrideEviction:
    def test_override_in_flight_blocks_lookup(self):
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.upsert(_make_signal(regime_fingerprint="fp"))
        book.mark_override_pending("TSM")

        assert book.lookup("TSM", "fp") is None

    def test_override_timeout_evicts_cached_signals_via_lookup(self, monkeypatch):
        """Before P0-C this was the exact asymmetry the audit caught:
        `is_override_pending(ticker)` evicted per-ticker cached signals
        after the 60s safety window, but `lookup` only removed the
        pending flag — so a caller that just used `lookup` after a
        stuck override would get a pre-shock cached signal back. Now
        `lookup` mirrors the eviction semantics.
        """
        import execution_engine.signal_book as sb

        book = sb.SignalBook()
        sig = _make_signal(regime_fingerprint="fp", conviction=0.9)
        book.upsert(sig)

        # Mark override pending with a fake past timestamp so the
        # 60s safety window has elapsed on the very next lookup.
        with book._lock:
            book._overrides_pending["TSM"] = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=90)

        miss = book.lookup("TSM", "fp")

        assert miss is None, "stale override should force a miss"
        assert book.size() == 0, (
            "cached signals must be evicted when the override times out — "
            "otherwise the fast path trades on pre-shock cache"
        )
        assert not book.is_override_pending("TSM")

    def test_is_override_pending_still_evicts_on_timeout(self):
        """Regression guard: the existing `is_override_pending`
        timeout-eviction behaviour must not have drifted while we were
        unifying `lookup`.
        """
        import execution_engine.signal_book as sb

        book = sb.SignalBook()
        book.upsert(_make_signal(regime_fingerprint="fp"))
        with book._lock:
            book._overrides_pending["TSM"] = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=120)

        assert book.is_override_pending("TSM") is False
        assert book.size() == 0


# ── P0-A: tier3_shock storage + retrieval ───────────────────────────


class TestPrecomputedSignalTier3Field:
    def test_default_is_false(self):
        sig = _make_signal()
        assert sig.tier3_shock is False

    def test_explicit_true_is_persisted(self):
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.upsert(_make_signal(tier3_shock=True))
        hit = book.lookup("TSM", "fp_baseline")
        assert hit is not None
        assert hit.tier3_shock is True

    def test_roundtrip_through_disk(self, tmp_path):
        """The flag must survive `save_to_disk` -> `load_from_disk`."""
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.upsert(_make_signal(tier3_shock=True))
        path = tmp_path / "book.json"
        book.save_to_disk(path)

        fresh = SignalBook()
        fresh.load_from_disk(path)
        hit = fresh.lookup("TSM", "fp_baseline")
        assert hit is not None
        assert hit.tier3_shock is True


# ── P0-A: fast_execute propagates tier3_shock → LIMIT routing ───────


class TestFastExecuteShockRouting:
    """End-to-end assertion of the P0-A fix: a shock-derived cached
    signal must produce a `TradeSignal` with `tier3_shock=True`, and
    the broker's `_determine_order_type` must consequently route it
    as a cross-the-spread LIMIT — not TWAP/VWAP on the 3rd-order
    horizon.
    """

    @pytest.mark.asyncio
    async def test_shock_signal_sets_flag_on_tradesignal(self, monkeypatch):
        import backend.services.orchestrator as orch
        from execution_engine.signal_book import get_signal_book

        book = get_signal_book()
        book.clear()
        book._overrides_pending.clear()

        book.upsert(
            _make_signal(
                ticker="TSM",
                regime_fingerprint="fp_live",
                conviction=0.82,
                tier3_shock=True,
                entry_price_at_compute=150.0,
            )
        )

        signal = await orch.fast_execute(
            ticker="TSM",
            current_price=150.5,
            regime_fingerprint="fp_live",
        )

        assert signal is not None, "fast_execute should hit the cached signal"
        assert signal.tier3_shock is True, (
            "fast_execute dropped the tier3_shock flag — P0-A regression"
        )

    @pytest.mark.asyncio
    async def test_non_shock_signal_leaves_flag_false(self, monkeypatch):
        import backend.services.orchestrator as orch
        from execution_engine.signal_book import get_signal_book

        book = get_signal_book()
        book.clear()
        book._overrides_pending.clear()

        book.upsert(
            _make_signal(
                ticker="AAPL",
                regime_fingerprint="fp_live",
                conviction=0.75,
                tier3_shock=False,
                entry_price_at_compute=200.0,
            )
        )

        signal = await orch.fast_execute(
            ticker="AAPL",
            current_price=200.1,
            regime_fingerprint="fp_live",
        )

        assert signal is not None
        assert signal.tier3_shock is False

    @pytest.mark.asyncio
    async def test_shock_fast_execute_routes_to_limit_order_type(self, monkeypatch):
        """The acceptance gate. A shock signal coming out of
        `fast_execute` feeds into `_determine_order_type` which MUST
        return `LIMIT` — not `TWAP`. Before P0-A the flag was dropped
        and TWAP was the (silently wrong) outcome for a 3rd-order
        horizon signal.
        """
        import backend.services.orchestrator as orch
        from execution_engine.broker.orders import _determine_order_type
        from execution_engine.signal_book import get_signal_book
        from shared.types import OrderType

        book = get_signal_book()
        book.clear()
        book._overrides_pending.clear()

        book.upsert(
            _make_signal(
                ticker="NVDA",
                regime_fingerprint="fp_live",
                conviction=0.9,
                tier3_shock=True,
                entry_price_at_compute=800.0,
            )
        )

        signal = await orch.fast_execute(
            ticker="NVDA",
            current_price=802.0,
            regime_fingerprint="fp_live",
        )
        assert signal is not None

        routed = _determine_order_type(signal)
        assert routed == OrderType.LIMIT, (
            f"fast_execute shock signal routed as {routed} — P0-A "
            "regression: tier3_shock flag was dropped somewhere on "
            "the fast path and the broker is about to TWAP a news-"
            "event entry"
        )
