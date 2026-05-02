"""Reconstruct ``data_store/signal_history.jsonl`` from existing artefacts.

Sources, in priority order:
  1. ``data_store/signal_history.jsonl``  — already-immortal rows (kept)
  2. ``data_store/signal_book.json``      — current active book snapshot
  3. ``live/trades.jsonl``                — every fill implies a signal
  4. ``live/equity_curve.jsonl``          — fills in the timeline so the
                                            UI's date-range picker has
                                            day-level coverage even when
                                            no fill happened that day

The script is **idempotent**. It de-dupes on ``(key, computed_at, event)``
so re-running never inflates the file. Safe to wire into CI, a one-shot
gcloud-ssh remediation, or a local dev refresh.

Run: ``python -m scripts.backfill_signal_history``
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_STORE = PROJECT_ROOT / "data_store"
LIVE_DIR = PROJECT_ROOT / "live"
TRADES_PATH = LIVE_DIR / "trades.jsonl"
EQUITY_PATH = LIVE_DIR / "equity_curve.jsonl"
HISTORY_PATH = DATA_STORE / "signal_history.jsonl"
BOOK_PATH = DATA_STORE / "signal_book.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _signal_record_from_trade(trade: dict[str, Any]) -> tuple[dict, dict] | None:
    """Synthesize CREATED + FILLED events for a trade.

    The fill timestamp comes from the trade itself; we backdate the
    "signal generated" event by 1 minute so the timeline tells a
    coherent story (signal then fill, not simultaneous).
    """
    ts = trade.get("timestamp") or trade.get("ts")
    if not ts:
        return None
    try:
        fill_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if fill_dt.tzinfo is None:
        fill_dt = fill_dt.replace(tzinfo=UTC)

    ticker = (trade.get("ticker") or "?").upper()
    side = (trade.get("side") or "BUY").upper()
    direction = "LONG" if side == "BUY" else "SHORT"
    fill_price = float(trade.get("fill_price") or trade.get("price") or 0.0)
    qty = float(trade.get("qty") or trade.get("shares") or 0.0)
    notional = float(trade.get("notional") or 0.0)
    asset_class = (
        trade.get("asset_class")
        or trade.get("instrument_type")
        or "EQUITY"
    )
    strategy = (
        trade.get("strategy")
        or trade.get("strategy_name")
        or "rule_based"
    )

    signal_dt = fill_dt - timedelta(minutes=1)
    expires_at = signal_dt + timedelta(minutes=30)
    signal_key = trade.get("signal_key") or f"{ticker}|backfilled_{int(fill_dt.timestamp())}"

    if direction == "LONG":
        sl = fill_price * 0.97
        tp = fill_price * 1.06
    else:
        sl = fill_price * 1.03
        tp = fill_price * 0.94

    base = {
        "key": signal_key,
        "ticker": ticker,
        "asset_class": asset_class,
        "instrument_type": trade.get("instrument_type") or asset_class,
        "direction": direction,
        "strategy_name": strategy,
        "entry_price": fill_price,
        "entry_price_at_compute": fill_price,
        "stop_loss_price": sl,
        "take_profit_price": tp,
        "stop_loss_pct": 3.0,
        "take_profit_pct": 6.0,
        "risk_reward_ratio": 2.0,
        "quantity": qty,
        "notional_value": notional,
        "conviction": float(trade.get("conviction") or 0.7),
        "expected_r": float(trade.get("expected_r") or 2.0),
        "rationale": trade.get("rationale") or "Backfilled from trade record",
        "computed_at": signal_dt.isoformat(),
        "expires_at": expires_at.isoformat(),
        "regime_fingerprint": "backfilled",
        "readiness_status": "FILLED",
    }

    created = dict(base)
    created["event"] = "BACKFILLED_CREATED"
    created["logged_at"] = signal_dt.isoformat()
    created["readiness_status"] = "READY"

    filled = dict(base)
    filled["event"] = "BACKFILLED_FILLED"
    filled["logged_at"] = fill_dt.isoformat()
    filled["fill_price"] = fill_price
    filled["fill_timestamp"] = fill_dt.isoformat()
    return created, filled


def _records_from_active_book() -> list[dict[str, Any]]:
    """Pull whatever's currently in signal_book.json into history."""
    if not BOOK_PATH.exists():
        return []
    try:
        book = json.loads(BOOK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for key, sig in book.items():
        if not isinstance(sig, dict):
            continue
        # Skip the polluted fixture so we don't immortalise it
        if (
            sig.get("strategy_name") in ("test_strategy", "dummy", "placeholder", "")
            or sig.get("ticker") in ("TEST", "DUMMY", "")
        ):
            continue
        rec = dict(sig)
        rec.setdefault("key", key)
        rec["event"] = "BACKFILLED_FROM_ACTIVE_BOOK"
        rec.setdefault("logged_at", sig.get("computed_at") or datetime.now(UTC).isoformat())
        out.append(rec)
    return out


def _records_from_equity_curve() -> list[dict[str, Any]]:
    """Detect position-count transitions and emit synthetic markers.

    These rows aren't tied to a specific ticker — they exist so the
    timeline view has a "something happened" marker on days where the
    signal wasn't filled (cancelled or expired in waiting).
    """
    snaps = _load_jsonl(EQUITY_PATH)
    if not snaps:
        return []
    out: list[dict[str, Any]] = []
    prev = -1
    for s in snaps:
        ts = s.get("timestamp")
        positions = int(s.get("open_positions") or 0)
        if not ts or positions == prev:
            prev = positions
            continue
        out.append({
            "event": "INFERRED_POSITION_DELTA",
            "logged_at": ts,
            "computed_at": ts,
            "ticker": "PORTFOLIO",
            "asset_class": "PORTFOLIO",
            "regime_fingerprint": "backfilled_inferred",
            "rationale": (
                f"Position count {prev if prev >= 0 else 0} → {positions} "
                f"at cycle {s.get('cycle')}"
            ),
            "readiness_status": "INFO",
            "key": f"PORTFOLIO|cycle_{s.get('cycle')}",
        })
        prev = positions
    return out


def _dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De-dupe on (key, event, logged_at-to-second) so re-runs don't grow the file."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in records:
        sig = (
            str(r.get("key") or ""),
            str(r.get("event") or ""),
            str(r.get("logged_at") or "")[:19],  # second precision
        )
        if sig in seen:
            continue
        seen.add(sig)
        out.append(r)
    return out


def main() -> int:
    print(f"[backfill] starting at {datetime.now(UTC).isoformat()}")

    existing = _load_jsonl(HISTORY_PATH)
    print(f"[backfill] existing history rows: {len(existing)}")

    new: list[dict[str, Any]] = []
    new.extend(existing)

    # Trade-derived rows (CREATED + FILLED pairs)
    trades = _load_jsonl(TRADES_PATH)
    print(f"[backfill] reading trades: {len(trades)}")
    trade_pairs = 0
    for t in trades:
        pair = _signal_record_from_trade(t)
        if pair is None:
            continue
        new.extend(pair)
        trade_pairs += 1
    print(f"[backfill] synthesized {trade_pairs} CREATED+FILLED pairs from trades")

    # Active-book carry-forward
    book_rows = _records_from_active_book()
    new.extend(book_rows)
    print(f"[backfill] carried forward {len(book_rows)} active-book rows")

    # Inferred timeline markers
    inferred = _records_from_equity_curve()
    new.extend(inferred)
    print(f"[backfill] inferred {len(inferred)} position-delta markers")

    # De-dupe + sort ascending by logged_at so the UI's "earliest"
    # field reads correctly when a tail-only query lands.
    deduped = _dedupe(new)
    deduped.sort(key=lambda r: r.get("logged_at") or r.get("computed_at") or "")
    print(f"[backfill] total rows after dedupe: {len(deduped)}")

    if len(deduped) <= len(existing):
        print("[backfill] nothing to add — exiting without rewriting")
        return 0

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in deduped:
            fh.write(json.dumps(r, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, HISTORY_PATH)

    earliest = deduped[0].get("logged_at") if deduped else None
    latest = deduped[-1].get("logged_at") if deduped else None
    print(f"[backfill] done. {len(deduped)} rows, {earliest} → {latest}")
    print(f"[backfill] -> {HISTORY_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
