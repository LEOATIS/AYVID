"""Tests for the wallclock-based tier scheduler in continuous_worker.

The tier gate is what keeps a 5-minute base cycle from triple-firing
the LLM pre-compute pass (which is gated to a 15-min cadence). If
``_tier_due`` regresses to "always True", the LLM bill triples
silently. These tests pin the gate's behaviour.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from data_primitives.pipeline import continuous_worker as worker


@pytest.fixture(autouse=True)
def _reset_tier_state():
    worker._reset_tier_state_for_tests()
    yield
    worker._reset_tier_state_for_tests()


class TestTierDue:
    def test_cold_start_fires(self):
        # No prior stamp → caller hasn't run yet → fire so we don't
        # silently drop the first cycle's work after a deploy.
        assert worker._tier_due("intraday_refresh") is True

    def test_within_window_does_not_fire(self):
        # First call stamps "now"; an immediate second call is well
        # inside the 5-minute window.
        worker._tier_due("intraday_refresh")
        assert worker._tier_due("intraday_refresh") is False

    def test_after_window_fires_again(self):
        # Stamp 6 minutes ago — past the 5-min window.
        past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=6)
        worker._tier_last_fired_at["intraday_refresh"] = past
        assert worker._tier_due("intraday_refresh") is True

    def test_explicit_interval_overrides_default(self):
        # Default for "signal_precompute" is 15 minutes; passing 1 min
        # explicitly must override.
        worker._tier_last_fired_at["signal_precompute"] = (
            dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2)
        )
        # 2 min < 15 → would NOT fire on default; 2 min > 1 → fires
        # with explicit 1.
        assert worker._tier_due("signal_precompute", interval_minutes=15.0) is False
        assert worker._tier_due("signal_precompute", interval_minutes=1.0) is True

    def test_unknown_tier_fails_open(self):
        # An unrecognised tier name should fire (rather than silently
        # drop work) so a typo surfaces by overwork instead of
        # underwork. Better to refresh too often than to never refresh.
        assert worker._tier_due("does_not_exist") is True

    def test_tiers_are_independent(self):
        # Firing one tier must not stamp the others.
        worker._tier_due("intraday_refresh")
        assert worker._tier_due("signal_precompute") is True
        assert worker._tier_due("universe_refresh") is True

    def test_default_intervals_match_user_facing_slas(self):
        # The user explicitly asked for "every 5 min, 15 min, and 1hr".
        # If a future edit reshuffles the defaults, this test fails
        # loudly so it shows up in code review.
        assert worker._TIER_INTERVAL_MINUTES["intraday_refresh"] == 5.0
        assert worker._TIER_INTERVAL_MINUTES["signal_precompute"] == 15.0
        assert worker._TIER_INTERVAL_MINUTES["universe_refresh"] == 60.0


class TestTierGateUnderRapidPolling:
    """Simulate a 5-minute base cycle and verify each tier fires the
    expected number of times across one wallclock hour."""

    def test_fire_count_over_simulated_hour(self):
        # 12 cycles spaced 5 minutes apart = 60 simulated minutes.
        # Tier-1 (intraday) should fire on every cycle: 12 times.
        # Tier-2 (signal_precompute, 15min) should fire ~4 times.
        # Tier-3 (universe_refresh, 60min) should fire once (the cold
        # start) plus the cycle that crosses the 60-min mark.
        base_now = dt.datetime(2026, 5, 3, 12, 0, 0, tzinfo=dt.UTC)
        fires = {"intraday_refresh": 0, "signal_precompute": 0, "universe_refresh": 0}

        for cycle in range(12):
            faux_now = base_now + dt.timedelta(minutes=5 * cycle)
            with patch(
                "data_primitives.pipeline.continuous_worker.dt"
            ) as mock_dt:
                # Patch only datetime.now; leave timedelta etc. alone.
                mock_dt.datetime.now.return_value = faux_now
                mock_dt.datetime.fromisoformat = dt.datetime.fromisoformat
                mock_dt.UTC = dt.UTC
                mock_dt.timedelta = dt.timedelta
                for tier in fires:
                    if worker._tier_due(tier):
                        fires[tier] += 1

        assert fires["intraday_refresh"] == 12, (
            f"intraday should fire every 5min cycle (got {fires['intraday_refresh']})"
        )
        # 15-min tier: cold-start fire at t=0, then at t=15, 30, 45 → 4 fires.
        assert fires["signal_precompute"] == 4, (
            f"signal_precompute should fire 4× across a simulated hour "
            f"(got {fires['signal_precompute']})"
        )
        # 60-min tier: cold-start fire at t=0; the next-eligible time is
        # t=60min, which is exactly the boundary of our 12-cycle simulation
        # (cycle 11 = t=55min — still inside the window). So one fire only.
        assert fires["universe_refresh"] == 1, (
            f"universe_refresh should fire 1× across a simulated 60min "
            f"(got {fires['universe_refresh']})"
        )
