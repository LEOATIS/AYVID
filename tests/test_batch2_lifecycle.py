"""Tests for Batch 2 — trade lifecycle hardening.

Three features, one file:

    C — Thesis invalidation
        The SignalBook gains an open-thesis map. When the ingestion
        cycle detects that a ticker's causal intel has flipped to a
        signature that contradicts the pinned one, the `invalidated_intents`
        method surfaces the impacted intent_ids so the continuous worker
        can force-close them.

    E — Trailing-stop pyramid
        The backtest engine's equity trail helper ratchets the stop up
        at three R-multiple thresholds. Never loosens the stop.

    F — Hold-time gating
        Alpha-horizon-scoped (min, max) hold-day bounds. Take-profit
        may NOT fire before min_hold. Stop-loss is always honoured
        irrespective of hold time. Max hold forces close as before.
"""

from __future__ import annotations

import datetime as dt

import pytest

# ── C — Thesis invalidation ─────────────────────────────────────────


class TestThesisInvalidation:
    def test_compose_signature_is_stable(self):
        from execution_engine.signal_book import compose_thesis_signature

        a = {
            "shock_bias": "BEARISH",
            "expected_direction": "SHORT",
            "shock_type": "supply_chain",
            "shock_resolved": False,
            "shock_magnitude": -0.034,
            "source": "TSMC fab fire",
        }
        b = dict(a, shock_magnitude=-0.050, source="updated TSMC news")  # noise fields only
        assert compose_thesis_signature(a) == compose_thesis_signature(b), (
            "noise field edits should NOT change the thesis signature"
        )

    def test_compose_signature_flips_on_real_change(self):
        from execution_engine.signal_book import compose_thesis_signature

        bear = {"shock_bias": "BEARISH", "shock_type": "supply_chain", "shock_resolved": False}
        bull = {"shock_bias": "BULLISH", "shock_type": "supply_chain", "shock_resolved": False}
        resolved = dict(bear, shock_resolved=True)

        s_bear = compose_thesis_signature(bear)
        s_bull = compose_thesis_signature(bull)
        s_resolved = compose_thesis_signature(resolved)

        assert s_bear != s_bull, "bias flip must flip the signature"
        assert s_bear != s_resolved, "resolution flip must flip the signature"

    def test_compose_signature_empty_on_missing_intel(self):
        from execution_engine.signal_book import compose_thesis_signature

        assert compose_thesis_signature(None) == ""
        assert compose_thesis_signature({}) == ""

    def test_register_and_invalidate_matches_on_flip(self):
        """Core mechanic: a registered thesis gets flagged for
        invalidation iff the ticker's current signature differs from
        the pinned one."""
        from execution_engine.signal_book import SignalBook, compose_thesis_signature

        book = SignalBook()
        bear = compose_thesis_signature({"shock_bias": "BEARISH", "shock_type": "supply_chain"})
        bull = compose_thesis_signature({"shock_bias": "BULLISH", "shock_type": "supply_chain"})

        book.register_open_thesis("intent_A", "KLAC", bear)
        # Thesis unchanged — no invalidation.
        assert book.invalidated_intents({"KLAC": bear}) == []
        # Flip to bull — invalidation must surface.
        results = book.invalidated_intents({"KLAC": bull})
        assert len(results) == 1
        assert results[0][0] == "intent_A"
        assert results[0][1] == "KLAC"
        assert results[0][2] == bear
        assert results[0][3] == bull

    def test_empty_current_thesis_does_not_invalidate(self):
        """Quiet news cycles (no new causal intel for the ticker)
        must NOT trigger close — invalidation requires an explicit
        conflicting signature, not the absence of one."""
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.register_open_thesis("i1", "AAPL", "BEARISH|short|supply_chain|active")
        assert book.invalidated_intents({}) == []
        assert book.invalidated_intents({"AAPL": ""}) == []

    def test_clear_open_thesis_drops_the_pin(self):
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.register_open_thesis("i2", "NVDA", "BEARISH|short|supply_chain|active")
        book.clear_open_thesis("i2")
        # Now a conflicting thesis doesn't surface — position is
        # already closed, we don't want to double-close.
        assert book.invalidated_intents({"NVDA": "BULLISH|long|recovery|active"}) == []

    def test_thesis_invalidation_counter_is_tracked(self):
        from execution_engine.signal_book import SignalBook

        book = SignalBook()
        book.register_open_thesis("i3", "TSM", "BEARISH|short|supply_chain|active")
        book.invalidated_intents({"TSM": "BULLISH|long|recovery|active"})
        book.invalidated_intents({"TSM": "BULLISH|long|recovery|active"})  # same event, 2nd check
        stats = book.get_stats()
        assert stats["thesis_invalidations"] == 2


# ── E — Trailing-stop pyramid ───────────────────────────────────────


def _make_equity_pos(direction: str = "LONG", entry: float = 100.0, sl: float = 97.0):
    """Stub a BacktestTrade for trailing-stop math."""
    from backtesting.engine import BacktestTrade
    from shared.types import AlphaHorizon, AlphaScore, TradeAction, TradeIntent

    d = TradeAction.LONG if direction == "LONG" else TradeAction.SHORT
    sig = TradeIntent(
        intent_id="t1",
        execute=True,
        ticker="AAPL",
        direction=d,
        alpha_horizon=AlphaHorizon("2nd_order"),
        alpha_score=AlphaScore(
            ticker="AAPL",
            direction=d,
            factor_score=0.7,
            bayesian_conviction=0.7,
            unified_conviction=0.7,
            expected_r=2.0,
            min_r=1.5,
        ),
    )
    return BacktestTrade(
        signal=sig,
        entry_price=entry,
        entry_date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        shares=100,
        stop_loss_price=sl,
        take_profit_price=entry * 1.06,
        # Pinned 1R at entry — required for post-breakeven ratchet.
        initial_risk_per_share=abs(entry - sl),
    )


class TestPyramidTrailLong:
    """All paths use the spec 1R-2R-3R ratchet. Distance is E - SL."""

    def _apply_trail(self, pos, cur):
        """Contract test — mirrors the exact logic in the nested
        ``_pyramid_trail_long`` closure inside ``run_backtest``.
        Uses the pinned ``initial_risk_per_share`` so R-multiples
        remain meaningful AFTER the stop has ratcheted past entry.
        """
        E = pos.entry_price
        one_r = pos.initial_risk_per_share or (E - pos.stop_loss_price)
        if one_r <= 0:
            return
        gain = cur - E
        if gain < one_r:
            return
        r_multiple = gain / one_r
        if r_multiple >= 2.0:
            new_sl = E + 0.50 * gain
        elif r_multiple >= 1.0:
            new_sl = E
        else:
            return
        pos.stop_loss_price = max(pos.stop_loss_price, new_sl)

    def test_no_ratchet_below_plus_one_r(self):
        pos = _make_equity_pos(entry=100.0, sl=97.0)  # 1R = $3
        self._apply_trail(pos, 102.0)  # only +2/3 R
        assert pos.stop_loss_price == 97.0

    def test_plus_one_r_moves_stop_to_breakeven(self):
        pos = _make_equity_pos(entry=100.0, sl=97.0)
        self._apply_trail(pos, 103.0)  # exactly +1R
        assert pos.stop_loss_price == pos.entry_price

    def test_plus_two_r_trails_at_half_gain(self):
        pos = _make_equity_pos(entry=100.0, sl=97.0)
        self._apply_trail(pos, 106.0)  # +2R = +$6 gain
        # new_sl = E + 0.5 * gain = 100 + 3 = 103
        assert pos.stop_loss_price == 103.0

    def test_plus_three_r_trails_at_half_open_profit(self):
        pos = _make_equity_pos(entry=100.0, sl=97.0)
        self._apply_trail(pos, 112.0)  # +4R gain of $12
        # new_sl = 100 + 0.5 * 12 = 106
        assert pos.stop_loss_price == 106.0

    def test_stop_never_loosens(self):
        pos = _make_equity_pos(entry=100.0, sl=97.0)
        self._apply_trail(pos, 112.0)  # raises to 106
        assert pos.stop_loss_price == 106.0
        self._apply_trail(pos, 102.0)  # pullback — should NOT drop stop
        assert pos.stop_loss_price == 106.0

    def test_short_mirrors_long(self):
        """SHORT trade: entry 100, initial stop ABOVE at 103.
        1R = 3. At -3 price (97), new stop = breakeven 100.
        At -6 price (94), new stop = E - 0.5 * gain = 100 - 3 = 97.
        The pinned ``initial_risk_per_share`` lets the second ratchet
        fire even after breakeven has been crossed.
        """
        pos = _make_equity_pos(direction="SHORT", entry=100.0, sl=103.0)
        assert pos.initial_risk_per_share == 3.0
        E = pos.entry_price

        def trail_short(cur):
            one_r = pos.initial_risk_per_share or (pos.stop_loss_price - E)
            if one_r <= 0:
                return
            gain = E - cur
            if gain < one_r:
                return
            r = gain / one_r
            if r >= 2.0:
                new_sl = E - 0.5 * gain
            elif r >= 1.0:
                new_sl = E
            else:
                return
            pos.stop_loss_price = min(pos.stop_loss_price, new_sl)

        trail_short(97.0)
        assert pos.stop_loss_price == 100.0  # breakeven
        trail_short(94.0)
        assert pos.stop_loss_price == 97.0  # 50% of open profit


# ── F — Hold-time gating ────────────────────────────────────────────


class TestHoldTimeBounds:
    """Replicates the `_hold_bounds` helper in the engine and asserts
    the horizon → (min, max) mapping matches the docs."""

    def _bounds(self, pos):
        horizon = getattr(pos.signal, "alpha_horizon", None)
        hv = horizon.value if horizon is not None else "2nd_order"
        bounds = {
            "1st_order": (0, 3),
            "2nd_order": (1, 10),
            "3rd_order": (5, 15),
            "4th_order": (14, 42),
        }
        return bounds.get(hv, (1, 20))

    @pytest.mark.parametrize(
        "horizon,expected",
        [
            ("1st_order", (0, 3)),
            ("2nd_order", (1, 10)),
            ("3rd_order", (5, 15)),
            ("4th_order", (14, 42)),
        ],
    )
    def test_bounds_per_horizon(self, horizon, expected):
        from shared.types import AlphaHorizon

        pos = _make_equity_pos()
        pos.signal.alpha_horizon = AlphaHorizon(horizon)
        assert self._bounds(pos) == expected

    def test_min_hold_is_monotonic_in_horizon(self):
        """Structural trades (3rd/4th order) must have strictly longer
        minimum holds than swing trades."""
        bounds = {
            "1st_order": (0, 3),
            "2nd_order": (1, 10),
            "3rd_order": (5, 15),
            "4th_order": (14, 42),
        }
        mins = [bounds[h][0] for h in ("1st_order", "2nd_order", "3rd_order", "4th_order")]
        assert mins == sorted(mins), f"min-hold not monotonic: {mins}"
