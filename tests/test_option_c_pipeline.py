"""Tests for the Option-C refactor + pipeline-backtest feedback loop.

Three invariants:

  1. **Gate removal** — the backtest engine NO LONGER enforces
     signal-quality filters on decisions coming out of the orchestrator.
     A decision with `conviction=0.55` and `expected_r < min_r` must
     pass the engine's entry point; rejection belongs to the
     orchestrator alone.

  2. **Outcomes log contract** — ``append_backtest_outcomes`` writes
     one JSON line per closed trade, with `strategy` + `regime_key` +
     `realized_r` populated. Malformed rows are skipped on read.

  3. **Recalibrate idempotency** — ``rebuild_priors_from_outcomes``
     run twice in a row on the same log produces the same priors
     (statistically). Human-authored ``_block_reason`` fields
     (`Batch-*`) are preserved; auto-blocks are overwritten by
     fresh evidence.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

# ── 1. Gate-removal invariants ──────────────────────────────────────


class TestOptionCEngineTrustsOrchestrator:
    """The engine must no longer double-gate orchestrator decisions."""

    def test_fno_conviction_floor_source_removed(self):
        """The 0.70 F&O conviction floor was the filter that killed
        every shock_directed_put decision. It must no longer be in
        engine.py. Grep-equivalent: search for the specific string
        that constitutes the old filter."""
        engine_src = Path("backtesting/engine.py").read_text()
        assert "fno_min_conviction = max(min_conviction, 0.70)" not in engine_src, (
            "The 0.70 F&O conviction floor is back in engine.py — "
            "Option-C regression. This filter silently killed 100% "
            "of shock-directed F&O decisions."
        )

    def test_r_hurdle_filter_removed_fno(self):
        """The `expected_r < min_r` filter at the F&O entry is gone."""
        engine_src = Path("backtesting/engine.py").read_text()
        # Count occurrences inside the engine — there used to be TWO
        # (F&O + equity path). Both should be gone.
        # The substring below is the full filter line we removed.
        assert engine_src.count("if decision.expected_r < decision.min_r:") == 0, (
            "The expected_r < min_r hurdle filter(s) are back. The "
            "orchestrator's EV gate already does this check — "
            "double-gating kills intentionally R-capped strategies."
        )

    def test_equity_conviction_floor_removed(self):
        """The equity-side ``conviction < min_conviction`` filter is gone."""
        engine_src = Path("backtesting/engine.py").read_text()
        assert "if decision.conviction < min_conviction:" not in engine_src, (
            "The equity conviction floor in engine.py is back — "
            "Option-C regression. Orchestrator-side gating should "
            "be the only signal-quality filter."
        )

    def test_orchestrator_skip_is_still_honoured(self):
        """Orchestrator's own SKIP outcome MUST still short-circuit —
        that's the decision the engine is meant to trust."""
        engine_src = Path("backtesting/engine.py").read_text()
        assert 'if decision.action == "SKIP":' in engine_src, (
            "engine lost the SKIP short-circuit — backtest is now "
            "forcing trades the orchestrator explicitly rejected"
        )

    def test_pipeline_flags_folded_into_shock_walkforward(self):
        """The --ingest / --precalibrate / --recalibrate flags live on
        the ``shock-walkforward`` command itself. There is no
        standalone ``pipeline-backtest`` command — the user's
        policy was: modify existing commands, don't proliferate new
        entry points."""
        import inspect

        from cli.commands.data import data_app

        command_names = {c.name for c in data_app.registered_commands}
        assert "shock-walkforward" in command_names
        assert "pipeline-backtest" not in command_names, (
            "pipeline-backtest leaked back in — its behaviour should "
            "live as flags on shock-walkforward"
        )

        sw = next(c for c in data_app.registered_commands if c.name == "shock-walkforward")
        sig = inspect.signature(sw.callback)
        param_names = set(sig.parameters.keys())
        for expected in ("ingest", "precalibrate", "recalibrate", "reset_outcomes"):
            assert expected in param_names, (
                f"shock-walkforward missing --{expected.replace('_', '-')} "
                f"flag (params={sorted(param_names)})"
            )


# ── 2. Outcomes log contract ────────────────────────────────────────


def _stub_backtest_result(*, fno=None, eq=None):
    """Build a minimal BacktestResult double with a small trade log."""
    from backtesting.engine import BacktestResult

    r = BacktestResult(
        start_date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        end_date=dt.datetime(2026, 3, 31, tzinfo=dt.UTC),
        initial_equity=1_000_000.0,
        final_equity=1_050_000.0,
    )
    r.metadata = {}
    r.fno_trade_log = list(fno or [])
    r.trades = list(eq or [])
    return r


class TestOutcomesLog:
    def test_append_writes_one_row_per_closed_trade(self, tmp_path):
        from backtesting.engine import FnOTrade
        from backtesting.trade_outcomes import (
            append_backtest_outcomes,
            read_outcomes,
        )

        fno = [
            FnOTrade(
                trade_id="t1",
                strategy="shock_directed_put_es",
                underlying="^GSPC",
                instrument="INDEX_PUT",
                direction="LONG",
                entry_date=dt.date(2026, 1, 5),
                exit_date=dt.date(2026, 1, 8),
                pnl=1200.0,
                pnl_pct=3.0,
                hold_days=3,
                details={
                    "regime_key": "STRONG_TREND_DOWN__CRISIS",
                    "conviction": 0.78,
                    "expected_r": 1.2,
                    "realized_r": 2.4,
                    "exit_reason": "profit_target",
                },
            ),
            FnOTrade(
                trade_id="t2",
                strategy="idx_trend_nq",
                underlying="^IXIC",
                instrument="INDEX_FUTURE",
                direction="SHORT",
                entry_date=dt.date(2026, 2, 1),
                exit_date=dt.date(2026, 2, 2),
                pnl=-400.0,
                pnl_pct=-0.8,
                hold_days=1,
                details={
                    "regime_key": "WEAK_TREND_DOWN__NORMAL",
                    "conviction": 0.65,
                    "expected_r": 1.8,
                    "realized_r": -0.75,
                    "exit_reason": "hard_1R_stop",
                },
            ),
        ]
        result = _stub_backtest_result(fno=fno)
        log_path = tmp_path / "outcomes.jsonl"

        n = append_backtest_outcomes(
            result, scenario_id="covid_crash_2020", outcomes_path=log_path
        )
        assert n == 2
        rows = read_outcomes(log_path)
        assert len(rows) == 2
        assert rows[0]["strategy"] == "shock_directed_put_es"
        assert rows[0]["regime_key"] == "STRONG_TREND_DOWN__CRISIS"
        assert rows[0]["realized_r"] == 2.4
        assert rows[0]["scenario_id"] == "covid_crash_2020"
        assert rows[1]["realized_r"] == -0.75

    def test_append_is_additive_across_scenarios(self, tmp_path):
        from backtesting.engine import FnOTrade
        from backtesting.trade_outcomes import (
            append_backtest_outcomes,
            read_outcomes,
        )

        def _mk(tid, strat, r):
            return FnOTrade(
                trade_id=tid,
                strategy=strat,
                underlying="^GSPC",
                instrument="INDEX_PUT",
                direction="LONG",
                entry_date=dt.date(2026, 1, 1),
                exit_date=dt.date(2026, 1, 2),
                pnl=100.0,
                pnl_pct=1.0,
                hold_days=1,
                details={
                    "regime_key": "STRONG_TREND_DOWN__CRISIS",
                    "conviction": 0.7,
                    "expected_r": 1.5,
                    "realized_r": r,
                    "exit_reason": "ok",
                },
            )

        log_path = tmp_path / "outcomes.jsonl"
        append_backtest_outcomes(
            _stub_backtest_result(fno=[_mk("a", "s1", 1.1)]),
            scenario_id="sc_1",
            outcomes_path=log_path,
        )
        append_backtest_outcomes(
            _stub_backtest_result(fno=[_mk("b", "s1", -0.5)]),
            scenario_id="sc_2",
            outcomes_path=log_path,
        )
        rows = read_outcomes(log_path)
        assert len(rows) == 2
        assert rows[0]["scenario_id"] == "sc_1"
        assert rows[1]["scenario_id"] == "sc_2"

    def test_open_positions_not_logged(self, tmp_path):
        """BacktestTrade without an exit_date MUST be skipped — open
        positions carry no realised P&L and would bias priors."""
        from backtesting.engine import BacktestTrade
        from backtesting.trade_outcomes import (
            append_backtest_outcomes,
            read_outcomes,
        )
        from shared.types import AlphaHorizon, AlphaScore, TradeAction, TradeIntent

        open_sig = TradeIntent(
            intent_id="o1",
            execute=True,
            ticker="AAPL",
            direction=TradeAction.LONG,
            alpha_horizon=AlphaHorizon("2nd_order"),
            alpha_score=AlphaScore(
                ticker="AAPL",
                direction=TradeAction.LONG,
                factor_score=0.7,
                bayesian_conviction=0.7,
                unified_conviction=0.7,
                expected_r=2.0,
                min_r=1.5,
            ),
        )
        open_trade = BacktestTrade(
            signal=open_sig,
            entry_price=150.0,
            entry_date=dt.datetime(2026, 1, 10, tzinfo=dt.UTC),
            shares=100,
            stop_loss_price=145.0,
            # No exit_date — open
        )
        result = _stub_backtest_result(eq=[open_trade])
        log_path = tmp_path / "outcomes.jsonl"
        n = append_backtest_outcomes(result, scenario_id="x", outcomes_path=log_path)
        assert n == 0
        assert read_outcomes(log_path) == []

    def test_malformed_lines_skipped_on_read(self, tmp_path):
        from backtesting.trade_outcomes import read_outcomes

        log_path = tmp_path / "mixed.jsonl"
        log_path.write_text(
            '{"strategy": "s1", "regime_key": "R__N", "realized_r": 1.2}\n'
            "this is not JSON\n"
            "\n"
            '{"strategy": "s2", "regime_key": "R__H", "realized_r": -0.5}\n'
        )
        rows = read_outcomes(log_path)
        assert len(rows) == 2
        assert {r["strategy"] for r in rows} == {"s1", "s2"}


# ── 3. Recalibrate rebuild semantics ────────────────────────────────


def _seed_outcomes(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class TestRebuildPriorsFromOutcomes:
    def test_empty_log_is_noop(self, tmp_path):
        from backtesting.trade_outcomes import rebuild_priors_from_outcomes

        out = tmp_path / "outcomes.jsonl"
        out.write_text("")
        priors_p = tmp_path / "priors.json"
        priors_p.write_text(
            json.dumps({"_schema_version": 1, "priors": {"s_x": {"R__N": {"n": 5}}}})
        )

        payload = rebuild_priors_from_outcomes(
            outcomes_path=out,
            priors_path=priors_p,
        )
        assert payload["priors"]["s_x"]["R__N"]["n"] == 5  # unchanged

    def test_new_cell_is_added_with_stats(self, tmp_path):
        from backtesting.trade_outcomes import rebuild_priors_from_outcomes

        out = tmp_path / "outcomes.jsonl"
        # 6 winning trades, all +1R, should be profitable cell
        _seed_outcomes(
            out,
            [
                {
                    "source": "backtest",
                    "scenario_id": "sc",
                    "timestamp": "2026-01-01",
                    "strategy": "new_strat",
                    "regime_key": "NEW__CELL",
                    "realized_r": 1.0 + 0.01 * i,
                }
                for i in range(6)
            ],
        )
        priors_p = tmp_path / "priors.json"
        priors_p.write_text(json.dumps({"_schema_version": 1, "priors": {}}))

        payload = rebuild_priors_from_outcomes(
            outcomes_path=out,
            priors_path=priors_p,
        )
        cell = payload["priors"]["new_strat"]["NEW__CELL"]
        assert cell["n"] == 6
        assert cell["mean_r"] > 0.9
        assert cell["sharpe"] > 0  # all winners → positive Sharpe
        assert not cell.get("blocked_by_regime")

    def test_losing_cell_auto_blocks(self, tmp_path):
        from backtesting.trade_outcomes import (
            DEFAULT_BLOCKED_SHARPE,
            rebuild_priors_from_outcomes,
        )

        out = tmp_path / "outcomes.jsonl"
        # 6 consistently losing trades → sharp negative Sharpe
        _seed_outcomes(
            out,
            [
                {
                    "source": "backtest",
                    "scenario_id": "sc",
                    "timestamp": "2026-01-01",
                    "strategy": "bad_strat",
                    "regime_key": "LOSS__CELL",
                    "realized_r": -0.9 - 0.02 * i,
                }
                for i in range(6)
            ],
        )
        priors_p = tmp_path / "priors.json"
        priors_p.write_text(json.dumps({"_schema_version": 1, "priors": {}}))

        payload = rebuild_priors_from_outcomes(
            outcomes_path=out,
            priors_path=priors_p,
        )
        cell = payload["priors"]["bad_strat"]["LOSS__CELL"]
        assert cell.get("blocked_by_regime") is True
        assert cell["sharpe"] <= DEFAULT_BLOCKED_SHARPE
        assert payload["_rebuild_newly_blocked"] >= 1

    def test_manual_block_reasons_preserved(self, tmp_path):
        """Batch-* block reasons are human-authored policy. The
        rebuilder must NEVER flip them off even if the stats show
        positive edge. Option-C safety."""
        from backtesting.trade_outcomes import rebuild_priors_from_outcomes

        out = tmp_path / "outcomes.jsonl"
        _seed_outcomes(
            out,
            [
                {
                    "source": "backtest",
                    "scenario_id": "sc",
                    "timestamp": "2026-01-01",
                    "strategy": "idx_bear_spread",
                    "regime_key": "STRONG_TREND_UP__NORMAL",
                    "realized_r": 1.0,
                }
                for _ in range(10)
            ],
        )
        priors_p = tmp_path / "priors.json"
        priors_p.write_text(
            json.dumps(
                {
                    "_schema_version": 1,
                    "priors": {
                        "idx_bear_spread": {
                            "STRONG_TREND_UP__NORMAL": {
                                "blocked_by_regime": True,
                                "_block_reason": "Batch-1/A: shorting in uptrends",
                            }
                        }
                    },
                }
            )
        )

        payload = rebuild_priors_from_outcomes(
            outcomes_path=out,
            priors_path=priors_p,
        )
        cell = payload["priors"]["idx_bear_spread"]["STRONG_TREND_UP__NORMAL"]
        assert cell.get("blocked_by_regime") is True, (
            "Batch-1/A policy override was overwritten by evidence — Option-C safety regression"
        )
        assert cell["_block_reason"].startswith("Batch-")

    def test_rebuild_is_idempotent(self, tmp_path):
        from backtesting.trade_outcomes import rebuild_priors_from_outcomes

        out = tmp_path / "outcomes.jsonl"
        _seed_outcomes(
            out,
            [
                {"strategy": "s1", "regime_key": "R__N", "realized_r": r, "source": "backtest"}
                for r in (0.5, 1.2, -0.3, 0.8, 1.0, 0.9)
            ],
        )
        priors_p = tmp_path / "priors.json"
        priors_p.write_text(json.dumps({"_schema_version": 1, "priors": {}}))

        p1 = rebuild_priors_from_outcomes(outcomes_path=out, priors_path=priors_p)
        p2 = rebuild_priors_from_outcomes(outcomes_path=out, priors_path=priors_p)
        # Stats fields must match after a second pass.
        c1 = p1["priors"]["s1"]["R__N"]
        c2 = p2["priors"]["s1"]["R__N"]
        for k in ("n", "mean_r", "std_r", "sharpe", "win_rate"):
            assert c1[k] == c2[k], f"field '{k}' drifted across rebuilds"
