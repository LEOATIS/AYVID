"""End-to-end tests for the signal pipeline.

Covers:
  * PrecomputedSignal field derivation in __post_init__
  * SignalBook readiness query API + atomic persistence
  * TradeReadinessChecker — all seven gates
  * Position sizer per asset class
  * SL/TP arithmetic invariants
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from execution_engine.math.position_sizer import (
    compute_quantity_for_signal,
    size_crypto,
    size_equity,
    size_fx,
    size_index_future,
)
from execution_engine.signal_book import PrecomputedSignal, SignalBook

# ── Helpers ─────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _make_signal(**kwargs: Any) -> PrecomputedSignal:
    """Construct a fully-populated test signal — caller can override any field."""
    defaults: dict[str, Any] = dict(
        ticker="NVDA",
        direction="LONG",
        instrument_type="EQUITY",
        conviction=0.74,
        strategy_name="momentum_equity_long",
        strike=None,
        expiry_date="",
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
        rationale="Momentum confirmed 7/10 factors, STRONG_TREND_UP regime",
        computed_at=_utcnow_iso(),
        ttl_minutes=30,
        regime_fingerprint="STRONG_TREND_UP|HIGH_VOL|VIX_NORMAL",
        expected_r=2.0,
        min_r=1.5,
        entry_price_at_compute=117.00,
        # New fields — sometimes set explicitly
        asset_class="EQUITY",
    )
    defaults.update(kwargs)
    return PrecomputedSignal(**defaults)


# ── PrecomputedSignal derivation ───────────────────────────────────


class TestPrecomputedSignalPostInit:
    def test_sl_tp_derived_for_long(self):
        s = _make_signal(
            direction="LONG",
            entry_price_at_compute=100.0,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )
        assert s.entry_price == 100.0
        assert s.stop_loss_price == pytest.approx(95.0)
        assert s.take_profit_price == pytest.approx(110.0)
        assert s.stop_loss_price < s.entry_price < s.take_profit_price

    def test_sl_tp_derived_for_short(self):
        s = _make_signal(
            direction="SHORT",
            entry_price_at_compute=100.0,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
        )
        assert s.stop_loss_price == pytest.approx(105.0)
        assert s.take_profit_price == pytest.approx(90.0)
        assert s.take_profit_price < s.entry_price < s.stop_loss_price

    def test_entry_zone_bounds(self):
        s = _make_signal(entry_price_at_compute=100.0, entry_zone_pct=0.005)
        assert s.entry_zone_low == pytest.approx(99.5)
        assert s.entry_zone_high == pytest.approx(100.5)
        assert s.is_in_entry_zone(100.0) is True
        assert s.is_in_entry_zone(100.4) is True
        assert s.is_in_entry_zone(101.0) is False

    def test_risk_reward_ratio(self):
        s = _make_signal(
            direction="LONG",
            entry_price_at_compute=100.0,
            stop_loss_pct=5.0,    # SL = 95
            take_profit_pct=15.0,  # TP = 115
        )
        # reward = 15, risk = 5  →  R:R = 3.0
        assert s.risk_reward_ratio == pytest.approx(3.0)

    def test_expires_at_derived(self):
        now = dt.datetime(2026, 5, 2, 12, 0, 0, tzinfo=dt.UTC)
        s = _make_signal(computed_at=now.isoformat(), ttl_minutes=30)
        # expires_at should be exactly 30 min later
        exp = dt.datetime.fromisoformat(s.expires_at)
        assert (exp - now).total_seconds() == pytest.approx(30 * 60)

    def test_entry_price_aliases_match(self):
        s = _make_signal(entry_price_at_compute=200.0)
        assert s.entry_price == 200.0
        assert s.price_at_compute == 200.0

    def test_hold_signal_no_sl_tp_derivation(self):
        s = _make_signal(direction="HOLD", entry_price_at_compute=100.0,
                         stop_loss_pct=5.0, take_profit_pct=10.0)
        # HOLD short-circuits — neither SL nor TP price is derived.
        assert s.stop_loss_price == 0.0
        assert s.take_profit_price == 0.0

    def test_explicit_quantity_derives_notional_and_risk(self):
        s = _make_signal(
            direction="LONG",
            entry_price_at_compute=100.0,
            stop_loss_pct=5.0,
            quantity=200,
        )
        assert s.notional_value == pytest.approx(200 * 100.0)
        # risk = qty × |entry − SL| = 200 × 5 = 1000
        assert s.risk_amount == pytest.approx(1000.0)


# ── SignalBook readiness API ───────────────────────────────────────


class TestSignalBookReadinessAPI:
    def test_get_signals_by_status_filters_correctly(self):
        book = SignalBook()
        book.upsert(_make_signal(ticker="A", regime_fingerprint="r1"))
        book.upsert(_make_signal(ticker="B", regime_fingerprint="r1"))
        # Default state is WAITING for new signals
        waiting = book.get_signals_by_status("WAITING")
        assert len(waiting) == 2

        # Promote one to READY
        a_key = "A|r1"
        book.update_readiness(a_key, status="READY", current_price=117.05)
        ready = book.get_signals_by_status("READY")
        assert len(ready) == 1
        assert list(ready.values())[0].ticker == "A"
        assert list(ready.values())[0].current_price == 117.05

    def test_get_ready_signals_sorted_by_conviction(self):
        book = SignalBook()
        book.upsert(_make_signal(ticker="LO", regime_fingerprint="r", conviction=0.5))
        book.upsert(_make_signal(ticker="HI", regime_fingerprint="r", conviction=0.9))
        book.update_readiness("LO|r", status="READY")
        book.update_readiness("HI|r", status="READY")
        ranked = book.get_ready_signals()
        assert [s.ticker for s in ranked] == ["HI", "LO"]

    def test_update_readiness_persists_reason_and_timestamp(self):
        book = SignalBook()
        book.upsert(_make_signal(ticker="X", regime_fingerprint="r"))
        ts = dt.datetime(2026, 5, 2, 12, 0, 0, tzinfo=dt.UTC)
        ok = book.update_readiness(
            "X|r",
            status="WAITING",
            current_price=99.0,
            distance_to_entry_pct=-0.015,
            readiness_checked_at=ts,
            readiness_reason="outside_entry_zone",
        )
        assert ok is True
        s = book.get_signals_by_status("WAITING")["X|r"]
        assert s.readiness_reason == "outside_entry_zone"
        assert s.readiness_checked_at == ts.isoformat()
        assert s.distance_to_entry_pct == pytest.approx(-0.015)

    def test_mark_all_expired_flips_only_expired_entries(self):
        book = SignalBook()
        old = _make_signal(
            ticker="OLD",
            regime_fingerprint="r",
            computed_at=(dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat(),
            ttl_minutes=30,
        )
        fresh = _make_signal(ticker="FRESH", regime_fingerprint="r")
        book.upsert(old)
        book.upsert(fresh)
        flipped = book.mark_all_expired()
        assert flipped == 1
        assert book.get_signals_by_status("EXPIRED")["OLD|r"].readiness_status == "EXPIRED"
        assert book.get_signals_by_status("WAITING")["FRESH|r"].readiness_status == "WAITING"

    def test_atomic_persistence_round_trip(self, tmp_path):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="NVDA", regime_fingerprint="r",
            quantity=341, kelly_fraction=0.05,
        ))
        path = tmp_path / "signal_book.json"
        book.save_to_disk(path)
        assert path.exists()

        fresh = SignalBook()
        loaded = fresh.load_from_disk(path)
        assert loaded == 1
        sig = fresh.get_signals_by_status("WAITING")["NVDA|r"]
        assert sig.quantity == 341
        assert sig.kelly_fraction == 0.05


# ── Position sizer ─────────────────────────────────────────────────


class TestPositionSizer:
    def test_equity_sizing_arithmetic(self):
        # $100k portfolio, 2% risk = $2,000 risk budget. Risk per share
        # = |117 − 111.14| = $5.86. Expected shares = floor(2000 / 5.86) = 341.
        result = size_equity(
            entry_price=117.00,
            stop_loss_price=111.14,
            portfolio_value=100_000.0,
            risk_per_trade_pct=0.02,
        )
        assert result.quantity == 341
        assert result.notional_value == pytest.approx(341 * 117.00)
        # Actual risk amount = 341 × 5.86
        assert result.risk_amount == pytest.approx(341 * 5.86, rel=1e-3)

    def test_index_future_sizing_uses_tick_value(self):
        # ES tick = 0.25, tick_value = $12.50. SL distance = 4 points = 16 ticks.
        # Risk per contract = 16 × 12.50 = $200. With $2000 risk budget = 10 contracts.
        result = size_index_future(
            ticker="ES",
            entry_price=5240.0,
            stop_loss_price=5236.0,
            portfolio_value=100_000.0,
            risk_per_trade_pct=0.02,
        )
        assert result.quantity == 10
        assert result.contract_multiplier == 50.0
        assert result.margin_required is not None and result.margin_required > 0

    def test_fx_sizing_uses_pip_value(self):
        # 30 pips SL × $10/pip per std lot = $300/lot. Risk budget $2000 → ~6.67 lots.
        result = size_fx(
            ticker="EURUSD",
            entry_price=1.0850,
            stop_loss_price=1.0820,
            portfolio_value=100_000.0,
            risk_per_trade_pct=0.02,
        )
        assert result.quantity == pytest.approx(6.67, rel=1e-2)
        assert result.pip_value == 10.0
        assert result.lot_size == 100_000.0

    def test_crypto_sizing_capped_at_one_pct_notional(self):
        # 100k portfolio → cap at $1k notional.
        result = size_crypto(
            ticker="BTC",
            entry_price=67_500.0,
            stop_loss_price=64_125.0,    # 5% SL
            portfolio_value=100_000.0,
            risk_per_trade_pct=0.02,
        )
        assert result.notional_value <= 1_000.0 + 0.01  # tight cap with rounding tolerance

    def test_dispatcher_routes_correctly(self):
        eq = compute_quantity_for_signal(
            ticker="AAPL", asset_class="EQUITY", instrument_type="EQUITY",
            direction="LONG", entry_price=200, stop_loss_price=190,
            portfolio_value=100_000,
        )
        assert eq.quantity > 0
        fx = compute_quantity_for_signal(
            ticker="EURUSD", asset_class="FX", instrument_type="FX",
            direction="LONG", entry_price=1.10, stop_loss_price=1.097,
            portfolio_value=100_000,
        )
        assert fx.lot_size == 100_000.0

    def test_hold_returns_zero_quantity(self):
        result = compute_quantity_for_signal(
            ticker="ABC", asset_class="EQUITY", instrument_type="EQUITY",
            direction="HOLD", entry_price=100, stop_loss_price=95,
            portfolio_value=100_000,
        )
        assert result.quantity == 0.0


# ── TradeReadinessChecker ──────────────────────────────────────────


class TestTradeReadinessChecker:
    def _build_checker(
        self,
        book: SignalBook,
        *,
        price: float | None = 117.10,
        regime: str | None = None,
        has_pos: bool = False,
        portfolio_val: float = 100_000.0,
        open_risk: float = 0.0,
        capital: float = 50_000.0,
    ):
        from execution_engine.trade_readiness import TradeReadinessChecker

        return TradeReadinessChecker(
            signal_book=book,
            get_price=lambda _t, _ac: price,
            get_regime_fingerprint=(lambda _t: regime) if regime is not None else None,
            has_open_position=lambda _t: has_pos,
            portfolio_value=lambda: portfolio_val,
            portfolio_open_risk=lambda: open_risk,
            portfolio_available_capital=lambda: capital,
            check_interval_seconds=0.01,
            persist_after_each_cycle=False,
        )

    @pytest.mark.asyncio
    async def test_signal_becomes_ready_when_price_in_zone(self):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="NVDA", regime_fingerprint="r", direction="LONG",
            entry_price_at_compute=117.00, entry_zone_pct=0.005,
            quantity=341,
        ))
        checker = self._build_checker(book, price=117.10)
        results = await checker.tick()
        assert len(results) == 1
        assert results[0].status == "READY"
        assert book.get_signals_by_status("READY")["NVDA|r"].current_price == 117.10

    @pytest.mark.asyncio
    async def test_signal_waits_when_outside_zone(self):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="NVDA", regime_fingerprint="r", direction="LONG",
            entry_price_at_compute=117.00, entry_zone_pct=0.005,
        ))
        checker = self._build_checker(book, price=120.00)
        results = await checker.tick()
        assert results[0].status == "WAITING"
        assert "outside_entry_zone" in results[0].reason

    @pytest.mark.asyncio
    async def test_signal_cancelled_on_regime_change(self):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="NVDA", regime_fingerprint="STRONG_TREND_UP|HIGH_VOL|VIX_NORMAL",
            direction="LONG", entry_price_at_compute=117.00,
        ))
        checker = self._build_checker(
            book, price=117.10, regime="CRISIS|CRISIS|VIX_EXTREME"
        )
        await checker.tick()
        cancelled = book.get_signals_by_status("CANCELLED")
        assert len(cancelled) == 1
        assert "regime_changed" in list(cancelled.values())[0].readiness_reason

    @pytest.mark.asyncio
    async def test_signal_blocked_when_position_exists(self):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="NVDA", regime_fingerprint="r", direction="LONG",
            entry_price_at_compute=117.00,
        ))
        checker = self._build_checker(book, price=117.10, has_pos=True)
        results = await checker.tick()
        assert results[0].status == "WAITING"
        assert results[0].reason == "position_already_open"

    @pytest.mark.asyncio
    async def test_signal_blocked_by_portfolio_risk(self):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="NVDA", regime_fingerprint="r", direction="LONG",
            entry_price_at_compute=117.00, quantity=341, stop_loss_pct=5.0,
        ))
        # 6% of 100k = $6000 cap. Already at $5500 + new $2000 → exceeds.
        checker = self._build_checker(book, price=117.10, open_risk=5_500.0)
        results = await checker.tick()
        assert results[0].status == "WAITING"
        assert "portfolio_risk_limit" in results[0].reason

    @pytest.mark.asyncio
    async def test_expired_signal_marked_expired(self):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="OLD", regime_fingerprint="r",
            computed_at=(dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat(),
            ttl_minutes=30,
        ))
        checker = self._build_checker(book, price=117.10)
        await checker.tick()
        assert "OLD|r" in book.get_signals_by_status("EXPIRED")

    @pytest.mark.asyncio
    async def test_hold_signal_never_ready(self):
        book = SignalBook()
        book.upsert(_make_signal(
            ticker="HOLD1", regime_fingerprint="r", direction="HOLD",
            entry_price_at_compute=100.0,
        ))
        checker = self._build_checker(book, price=100.0)
        results = await checker.tick()
        assert results[0].status == "WAITING"
        assert results[0].reason == "hold_signal_no_execution"

    def test_load_from_disk_flips_stale_entries_and_persists(
        self, tmp_path, monkeypatch
    ):
        """Regression: prior to the fix, ``load_from_disk`` filtered
        stale signals out of the in-memory book. The on-disk file was
        never re-written so READY entries that had TTL-elapsed weeks
        ago stayed READY on disk forever — invisible to the in-memory
        readiness checker that does the EXPIRE flips. The dashboard's
        ``/signals`` endpoint reads the on-disk file directly, so it
        kept serving zombie READY entries.

        Fix: load all signals (regardless of staleness), run
        mark_all_expired, then persist. After load_from_disk, the
        on-disk file must reflect EXPIRED for any TTL-elapsed entry.
        """
        import json

        from execution_engine import signal_book as sb_module

        target = tmp_path / "signal_book.json"
        # Hand-craft an on-disk file with one stale READY signal — the
        # exact prod state that triggered the bug.
        stale = _make_signal(
            ticker="ZOMBIE", regime_fingerprint="r",
            computed_at=(dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat(),
            ttl_minutes=30,
        )
        from dataclasses import asdict
        on_disk_blob = asdict(stale)
        on_disk_blob["readiness_status"] = "READY"
        on_disk_blob["readiness_checked_at"] = stale.computed_at
        target.write_text(json.dumps({"ZOMBIE|r": on_disk_blob}))

        monkeypatch.setattr(sb_module, "_BOOK_PATH", target)
        book = SignalBook()
        active = book.load_from_disk()

        assert active == 0, (
            "all signals were TTL-elapsed; load_from_disk should report "
            "0 active after the EXPIRE sweep"
        )
        # The in-memory book must have the entry (so subsequent
        # readiness queries see it as EXPIRED, not "missing").
        assert book.size() == 1
        # And the on-disk file must reflect EXPIRED — this is the bit
        # the prior code never got right.
        on_disk = json.loads(target.read_text())
        assert on_disk["ZOMBIE|r"]["readiness_status"] == "EXPIRED", (
            f"on-disk status is {on_disk['ZOMBIE|r']['readiness_status']!r}; "
            f"expected EXPIRED after load_from_disk's stale sweep"
        )

    @pytest.mark.asyncio
    async def test_tick_persists_when_only_expirations_change(self, tmp_path, monkeypatch):
        """Regression: prior to the fix, a tick whose ONLY change was
        expiring stale signals never persisted to disk because
        ``mark_all_expired`` was called for its side-effect and the
        return count was discarded. The candidates loop saw zero
        WAITING/READY entries (everything had just been flipped to
        EXPIRED) so ``any_change`` stayed False and the EXPIRED status
        sat in-memory only — dropped on container restart, the on-disk
        book reverted to READY, and the dashboard kept showing dead
        signals as live for weeks. This test pins the fix.
        """
        from execution_engine import signal_book as sb_module
        from execution_engine.trade_readiness import TradeReadinessChecker

        # Redirect persistence to a tmp file we can inspect.
        target = tmp_path / "signal_book.json"
        monkeypatch.setattr(sb_module, "_BOOK_PATH", target)

        book = SignalBook()
        # An already-expired signal — TTL elapsed 2 hours ago.
        sig = _make_signal(
            ticker="ZOMBIE", regime_fingerprint="r",
            computed_at=(dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat(),
            ttl_minutes=30,
        )
        # Force READY on disk so the bug-state is what we'd see in prod
        # after an LLM pass marked the signal READY then the runner
        # restarted before the next readiness sweep.
        sig.readiness_status = "READY"
        book.upsert(sig)

        checker = TradeReadinessChecker(
            signal_book=book,
            get_price=lambda _t, _ac: 100.0,
            check_interval_seconds=0.01,
            persist_after_each_cycle=True,
        )

        await checker.tick()

        # Disk must reflect EXPIRED — not the cached READY.
        assert target.exists(), (
            "tick() did not persist; mark_all_expired return value "
            "is being ignored again"
        )
        import json
        on_disk = json.loads(target.read_text())
        zombie = on_disk["ZOMBIE|r"]
        assert zombie["readiness_status"] == "EXPIRED", (
            f"on-disk status is {zombie['readiness_status']!r}; "
            f"expected EXPIRED after readiness tick"
        )


# ── SL/TP and quantity arithmetic invariants ───────────────────────


class TestSlTpInvariants:
    def test_long_sl_below_entry_tp_above(self):
        s = _make_signal(direction="LONG", entry_price_at_compute=117.0,
                         stop_loss_pct=5.0, take_profit_pct=10.0)
        assert s.stop_loss_price < s.entry_price < s.take_profit_price

    def test_short_sl_above_entry_tp_below(self):
        s = _make_signal(direction="SHORT", entry_price_at_compute=117.0,
                         stop_loss_pct=5.0, take_profit_pct=10.0)
        assert s.take_profit_price < s.entry_price < s.stop_loss_price

    def test_quantity_risk_arithmetic_matches(self):
        s = _make_signal(
            entry_price_at_compute=117.00, stop_loss_pct=5.0,
            quantity=341,
        )
        per_share_risk = abs(s.entry_price - s.stop_loss_price)
        expected = s.quantity * per_share_risk
        # __post_init__ rounds risk_amount to 2dp
        assert abs(s.risk_amount - expected) < 0.5
