"""Append-only trade outcomes log + priors recalibrator.

AYVID's priors (``calibration/priors.json``) were historically produced
by a one-shot offline grid sweep (``backtesting.calibrate``). That works
for the initial seeding but has two problems:

  1. Priors drift from reality as soon as the market regime shifts. By
     the time the operator re-runs the grid sweep (hours of compute),
     the LLM supervisor has been quoting stale statistics for weeks.
  2. Live trading outcomes are never fed back into the priors. The
     system keeps making the same decisions that just lost money.

This module closes that loop. Every time a backtest finishes — and,
when the live runner writes its fills, every time a real trade closes —
we append one structured row per closed trade to
``calibration/trade_outcomes.jsonl``. A separate function,
``rebuild_priors_from_outcomes``, aggregates that log into the same
per-cell schema ``priors.json`` uses and writes it back. The supervisor
then reads fresh, evidence-backed priors on the next decision.

Schema — one JSON object per line::

    {
      "source":        "backtest" | "live",
      "scenario_id":   "covid_crash_2020" | null,
      "timestamp":     "<ISO>",
      "strategy":      "shock_directed_put_es",
      "regime_key":    "STRONG_TREND_DOWN__CRISIS",
      "direction":     "LONG" | "SHORT",
      "instrument":    "INDEX_PUT" | "INDEX_FUTURE" | "INDEX_SPREAD" | "EQUITY",
      "conviction":    0.78,
      "expected_r":    1.2,
      "realized_r":    2.4,
      "pnl_usd":       12345.0,
      "hold_days":     3,
      "exit_reason":   "profit_target",
      "entry_date":    "<ISO>",
      "exit_date":     "<ISO>"
    }

The rebuilder is idempotent: rerunning it on the same log produces the
same priors. It preserves any manually-added ``_block_reason`` fields
(Batch-1/A regime gates) so human-authored policy overrides don't get
silently overwritten by evidence.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from shared.logger import get_logger

if TYPE_CHECKING:
    from backtesting.engine import BacktestResult

log = get_logger("backtesting.trade_outcomes")

DEFAULT_OUTCOMES_PATH = Path("calibration/trade_outcomes.jsonl")
DEFAULT_PRIORS_PATH = Path("calibration/priors.json")

# Minimum observations per (strategy, regime) cell for the rebuilder to
# trust the aggregated Sharpe. Matches the one-shot calibrate default.
DEFAULT_MIN_SAMPLES = 5

# A cell is auto-blocked (``blocked_by_regime=true``) if it has enough
# samples AND the observed Sharpe sits below this threshold. The
# threshold is intentionally low-ish: we want to fire the block only
# when there is unambiguous evidence of negative edge, not on noise.
DEFAULT_BLOCKED_SHARPE = -0.5


# ── Logging closed trades ───────────────────────────────────────────


def _iso(val: Any) -> str:
    """Best-effort ISO serialisation for dt.date / dt.datetime / str."""
    if isinstance(val, (dt.date, dt.datetime)):
        return val.isoformat()
    return str(val) if val is not None else ""


def append_backtest_outcomes(
    result: BacktestResult,
    scenario_id: str | None = None,
    outcomes_path: Path = DEFAULT_OUTCOMES_PATH,
) -> int:
    """Append every closed trade from a `BacktestResult` to the log.

    Returns the number of rows written. Zero-trade results produce no
    log rows (and the function is a no-op). The log file is opened in
    append mode with newline-separated JSON, so repeated calls across
    a walk-forward run concatenate cleanly without any locking.
    """
    rows = list(_result_to_outcome_rows(result, scenario_id=scenario_id, source="backtest"))
    if not rows:
        return 0

    outcomes_path = Path(outcomes_path)
    outcomes_path.parent.mkdir(parents=True, exist_ok=True)
    with outcomes_path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, default=_iso) + "\n")
    log.info(f"Appended {len(rows)} closed trades → {outcomes_path} (scenario={scenario_id})")
    return len(rows)


def _result_to_outcome_rows(
    result: BacktestResult,
    scenario_id: str | None,
    source: str,
) -> Iterable[dict[str, Any]]:
    """Materialise outcome rows from a `BacktestResult`.

    The generator yields one dict per closed trade. Closed-trade
    detection is conservative: we require an `exit_date` to be set.
    Open positions at backtest end are NOT logged — they carry no
    realised P&L and would bias the priors toward no-information
    cells.
    """
    ts = dt.datetime.now(dt.UTC).isoformat()

    # F&O trades — every row in `fno_trade_log` is, by construction,
    # a closed trade (the engine only appends after exit resolution).
    for fno in getattr(result, "fno_trade_log", []) or []:
        details = getattr(fno, "details", {}) or {}
        yield {
            "source": source,
            "scenario_id": scenario_id,
            "timestamp": ts,
            "strategy": getattr(fno, "strategy", "") or "",
            "regime_key": details.get("regime_key", "") or "",
            "direction": getattr(fno, "direction", "") or "",
            "instrument": getattr(fno, "instrument", "") or "",
            "conviction": float(details.get("conviction", 0.0) or 0.0),
            "expected_r": float(details.get("expected_r", 0.0) or 0.0),
            "realized_r": float(details.get("realized_r", 0.0) or 0.0),
            "pnl_usd": float(getattr(fno, "pnl", 0.0) or 0.0),
            "hold_days": int(getattr(fno, "hold_days", 0) or 0),
            "exit_reason": details.get("exit_reason", "") or "",
            "entry_date": _iso(getattr(fno, "entry_date", None)),
            "exit_date": _iso(getattr(fno, "exit_date", None)),
            "underlying": getattr(fno, "underlying", "") or "",
        }

    # Equity trades — only those with exit_date set (closed). The
    # engine stores regime_key / strategy / conviction / expected_r on
    # the BacktestTrade dataclass directly.
    for eq in getattr(result, "trades", []) or []:
        if not getattr(eq, "exit_date", None):
            continue
        sig = getattr(eq, "signal", None)
        direction_str = ""
        if sig is not None:
            try:
                direction_str = sig.direction.value
            except Exception:
                direction_str = str(getattr(sig, "direction", "")) or ""
        entry = getattr(eq, "entry_price", 0.0) or 0.0
        exit_ = getattr(eq, "exit_price", 0.0) or 0.0
        # Sign-correct realised R for SHORT trades (price down = win).
        r_sign = 1.0 if direction_str == "LONG" else -1.0
        # Approximate realised R off stop distance: (pct_move / stop_pct)
        # where stop_pct is derived from the saved stop price.
        stop_px = getattr(eq, "stop_loss_price", 0.0) or 0.0
        stop_pct = abs(entry - stop_px) / entry if entry > 0 else 0.0
        realised_pct = (exit_ - entry) / entry if entry > 0 else 0.0
        realized_r = (realised_pct * r_sign) / stop_pct if stop_pct > 1e-6 else 0.0
        yield {
            "source": source,
            "scenario_id": scenario_id,
            "timestamp": ts,
            "strategy": getattr(eq, "strategy_name", "") or "",
            "regime_key": getattr(eq, "regime_key", "") or "",
            "direction": direction_str,
            "instrument": "EQUITY",
            "conviction": float(getattr(eq, "conviction", 0.0) or 0.0),
            "expected_r": float(getattr(eq, "expected_r", 0.0) or 0.0),
            "realized_r": float(realized_r),
            "pnl_usd": float(getattr(eq, "pnl", 0.0) or 0.0),
            "hold_days": int(getattr(eq, "hold_days", 0) or 0),
            "exit_reason": getattr(eq, "exit_reason", "") or "",
            "entry_date": _iso(getattr(eq, "entry_date", None)),
            "exit_date": _iso(getattr(eq, "exit_date", None)),
            "underlying": getattr(sig, "ticker", "") if sig else "",
        }


# ── Reading the outcomes log ────────────────────────────────────────


def read_outcomes(
    outcomes_path: Path = DEFAULT_OUTCOMES_PATH,
    since: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """Read every outcome row from the JSONL log.

    When ``since`` is provided, rows with a ``timestamp`` strictly
    earlier than it are filtered out. Malformed lines are silently
    skipped — we prefer a partial view of reality over crashing the
    recalibrator on a single bad row.
    """
    outcomes_path = Path(outcomes_path)
    if not outcomes_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in outcomes_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since is not None:
            ts_raw = row.get("timestamp")
            if ts_raw:
                try:
                    ts = dt.datetime.fromisoformat(ts_raw)
                    if ts < since:
                        continue
                except ValueError:
                    pass
        rows.append(row)
    return rows


# ── Priors recalibration ────────────────────────────────────────────


def _aggregate_cell_stats(rs: list[float]) -> dict[str, float] | None:
    """Given a list of realised R values for one cell, return stats.

    Returns None when the sample is empty. Sharpe here is the classic
    per-trade t-stat (mean / std with Bessel correction) scaled by
    sqrt(n) — same convention as the offline calibrator.
    """
    if not rs:
        return None
    arr = np.asarray(rs, dtype=float)
    n = int(arr.size)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    sharpe = (mean / std) * float(np.sqrt(n)) if std > 1e-9 else 0.0
    wins = int((arr > 0).sum())
    win_rate = wins / n
    return {
        "n": n,
        "mean_r": round(mean, 4),
        "std_r": round(std, 4),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
    }


def rebuild_priors_from_outcomes(
    outcomes_path: Path = DEFAULT_OUTCOMES_PATH,
    priors_path: Path = DEFAULT_PRIORS_PATH,
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    blocked_sharpe_threshold: float = DEFAULT_BLOCKED_SHARPE,
    source_filter: str | None = None,
    since: dt.datetime | None = None,
) -> dict[str, Any]:
    """Rebuild `priors.json` from the appended trade outcomes log.

    Merge semantics:
      * Cells that exist in the current priors.json carry forward
        their `max_rsi`, `max_z`, `min_factor_score`, `min_expected_r`,
        and `_block_reason` fields. Those are the offline calibrator's
        or the operator's policy declarations — we never overwrite
        them. The rebuilder only updates the statistics fields
        (``n``, ``mean_r``, ``std_r``, ``sharpe``, ``win_rate``) and
        the automatic ``blocked_by_regime`` flag.
      * New cells (strategy/regime combos seen in outcomes but absent
        from priors.json) are added from scratch with just the stats
        and the block flag.
      * A cell is auto-blocked iff ``n >= min_samples`` AND the
        observed Sharpe is ``<= blocked_sharpe_threshold``. A cell
        previously auto-blocked becomes unblocked as soon as its
        rolling Sharpe climbs above the threshold — this is the
        online-feedback half of the loop.

    Returns the updated priors dict. Also writes it to ``priors_path``
    so the supervisor picks up the change on its next mtime check.
    """
    rows = read_outcomes(outcomes_path, since=since)
    if source_filter is not None:
        rows = [r for r in rows if r.get("source") == source_filter]

    # Group (strategy, regime) → [realized_r]
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        strat = r.get("strategy", "")
        regime = r.get("regime_key", "")
        if not strat or not regime:
            continue
        try:
            buckets[(strat, regime)].append(float(r.get("realized_r", 0.0)))
        except (TypeError, ValueError):
            continue

    # Load existing priors (if any) so we can preserve policy fields.
    existing: dict[str, Any] = {}
    if priors_path.exists():
        try:
            existing = json.loads(priors_path.read_text())
        except json.JSONDecodeError:
            log.warning(f"priors.json at {priors_path} is malformed; starting fresh")
            existing = {}

    existing_priors = existing.get("priors", {}) if isinstance(existing, dict) else {}

    # Work on a shallow copy so any strategies already in priors.json
    # but with no new outcomes stay untouched.
    priors: dict[str, dict[str, Any]] = {
        k: dict((rk, dict(rv)) for rk, rv in v.items()) for k, v in existing_priors.items()
    }

    updated_cells = 0
    newly_blocked = 0
    newly_unblocked = 0
    for (strat, regime), rs in buckets.items():
        stats = _aggregate_cell_stats(rs)
        if stats is None:
            continue

        cell = priors.setdefault(strat, {}).setdefault(regime, {})
        prior_block = bool(cell.get("blocked_by_regime"))

        # Overwrite stats fields only — never the policy fields.
        cell.update(stats)

        if stats["n"] >= min_samples and stats["sharpe"] <= blocked_sharpe_threshold:
            cell["blocked_by_regime"] = True
            if not prior_block:
                newly_blocked += 1
                cell.setdefault(
                    "_block_reason",
                    f"auto-blocked from outcomes: n={stats['n']} "
                    f"sharpe={stats['sharpe']:+.2f} "
                    f"(threshold {blocked_sharpe_threshold:+.2f})",
                )
        else:
            # Only auto-unblock when the cell wasn't manually blocked
            # by a human-authored `_block_reason` that starts with
            # "Batch-" (our manual policy overrides).
            br = str(cell.get("_block_reason") or "")
            if prior_block and not br.startswith("Batch-"):
                cell["blocked_by_regime"] = False
                newly_unblocked += 1
        updated_cells += 1

    payload = {
        "_generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "_min_samples": min_samples,
        "_schema_version": existing.get("_schema_version", 1) if isinstance(existing, dict) else 1,
        "_rebuild_source": "outcomes_log",
        "_rebuild_rows": len(rows),
        "_rebuild_cells_updated": updated_cells,
        "_rebuild_newly_blocked": newly_blocked,
        "_rebuild_newly_unblocked": newly_unblocked,
        "priors": priors,
    }
    priors_path.parent.mkdir(parents=True, exist_ok=True)
    priors_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    log.info(
        f"Rebuilt priors from {len(rows)} outcomes → {priors_path} "
        f"(cells updated={updated_cells}, +block={newly_blocked}, "
        f"-block={newly_unblocked})"
    )
    return payload


__all__ = [
    "DEFAULT_OUTCOMES_PATH",
    "DEFAULT_PRIORS_PATH",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_BLOCKED_SHARPE",
    "append_backtest_outcomes",
    "read_outcomes",
    "rebuild_priors_from_outcomes",
]
