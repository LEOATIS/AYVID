"""Live read-only data routes — exposes the artifacts the paper runner
and backtester land on disk (`live/*.jsonl`, `document/backtests/*.json`,
`data_store/signal_book.json`) so the Next.js frontend can render the
same data the CLI shows.

P1-15: while the payloads are read-only diagnostic data, they include
equity-curve snapshots, open-position counts, and signal-book entries
— everything an attacker needs to reverse-engineer the strategy. The
router is now guarded by ``verify_api_key``; in dev (no keys
registered, ``AYVID_REQUIRE_AUTH=False``) the dependency passes
through so local development keeps working.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.core.security import verify_api_key
from shared.config import settings
from shared.logger import get_logger

log = get_logger("api.live")

router = APIRouter(dependencies=[Depends(verify_api_key)])

PROJECT_ROOT = Path(__file__).resolve().parents[4]
LIVE_DIR = PROJECT_ROOT / "live"
BACKTESTS_DIR = PROJECT_ROOT / "document" / "backtests"
SIGNAL_BOOK = PROJECT_ROOT / "data_store" / "signal_book.json"
_GCS_BUCKET = (getattr(settings, "AYVID_LIVE_GCS_BUCKET", None) or "").strip()
_GCS_PREFIX = (getattr(settings, "AYVID_LIVE_GCS_PREFIX", "live_state") or "live_state").strip("/")
_gcs_client: Any | None = None


def _gcs_blob_name(path: Path) -> str:
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    return f"{_GCS_PREFIX}/{rel}" if _GCS_PREFIX else rel


def _read_text_any(path: Path) -> str | None:
    """Read from local disk; if absent, fallback to GCS shared-live store."""
    if path.exists():
        try:
            return path.read_text()
        except OSError:
            return None
    if not _GCS_BUCKET:
        return None
    global _gcs_client
    try:
        if _gcs_client is None:
            from google.cloud import storage

            _gcs_client = storage.Client()
        blob = _gcs_client.bucket(_GCS_BUCKET).blob(_gcs_blob_name(path))
        if not blob.exists():
            return None
        return blob.download_as_text()
    except Exception as exc:
        log.warning(f"gcs read failed for {path}: {exc}")
        return None


def _read_jsonl(path: Path, limit: int | None = None, tail: bool = True) -> list[dict[str, Any]]:
    text = _read_text_any(path)
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit is not None and limit > 0:
        rows = rows[-limit:] if tail else rows[:limit]
    return rows


@router.get("/equity")
async def equity_curve(
    limit: int = Query(500, ge=1, le=10000, description="Tail window of snapshots"),
) -> dict[str, Any]:
    """Live paper-trading equity curve snapshots.

    Source: ``live/equity_curve.jsonl`` (one snapshot per cycle).
    """
    rows = _read_jsonl(LIVE_DIR / "equity_curve.jsonl", limit=limit)
    if not rows:
        return {
            "snapshots": [],
            "summary": None,
            "meta": _read_meta(),
        }
    meta = _read_meta()
    # Use run_meta.json as authoritative initial equity so partial limit
    # windows don't misreport the starting value.
    initial = float(
        (meta or {}).get("initial_equity", 0) or rows[0].get("equity", 0.0)
    )
    final = float(rows[-1].get("equity", initial))
    peak = max((float(r.get("equity", 0.0)) for r in rows), default=initial)
    drawdown = (final - peak) / peak * 100 if peak else 0.0
    total_return = (final / initial - 1.0) * 100 if initial else 0.0
    return {
        "snapshots": rows,
        "summary": {
            "snapshots_count": len(rows),
            "initial_equity": initial,
            "final_equity": final,
            "peak_equity": peak,
            "total_return_pct": round(total_return, 4),
            "drawdown_from_peak_pct": round(drawdown, 4),
            "first_timestamp": rows[0].get("timestamp"),
            "last_timestamp": rows[-1].get("timestamp"),
            "mode": rows[-1].get("mode"),
            "open_positions": rows[-1].get("open_positions", 0),
            "cycle": rows[-1].get("cycle"),
        },
        "meta": meta,
    }


def _read_meta() -> dict[str, Any] | None:
    meta_path = LIVE_DIR / "run_meta.json"
    text = _read_text_any(meta_path)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@router.get("/runner-status")
async def runner_status() -> dict[str, Any]:
    """Paper runner status — uptime, last snapshot age, mode."""
    import datetime as dt

    meta = _read_meta()
    rows = _read_jsonl(LIVE_DIR / "equity_curve.jsonl", limit=1)
    last_ts = rows[-1].get("timestamp") if rows else None
    age_seconds: float | None = None
    if last_ts:
        try:
            ts = dt.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            age_seconds = (dt.datetime.now(dt.UTC) - ts).total_seconds()
        except Exception:
            pass

    started_at = meta.get("started_at") if meta else None
    uptime_seconds: float | None = None
    if started_at:
        try:
            started = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            uptime_seconds = (dt.datetime.now(dt.UTC) - started).total_seconds()
        except Exception:
            pass

    interval = (meta or {}).get("interval_seconds", 60)
    is_alive = age_seconds is not None and age_seconds < interval * 3
    return {
        "is_alive": is_alive,
        "mode": (meta or {}).get("mode"),
        "ib_host": (meta or {}).get("ib_host"),
        "ib_port": (meta or {}).get("ib_port"),
        "auto_execute": (meta or {}).get("auto_execute"),
        "paper_trading": (meta or {}).get("paper_trading", True),
        "interval_seconds": interval,
        "started_at": started_at,
        "uptime_seconds": uptime_seconds,
        "last_snapshot_at": last_ts,
        "last_snapshot_age_seconds": age_seconds,
        "snapshots_total": _count_lines(LIVE_DIR / "equity_curve.jsonl"),
    }


def _count_lines(path: Path) -> int:
    text = _read_text_any(path)
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and current trade row shapes for frontend rendering."""
    qty = (
        _num(row.get("quantity"))
        or _num(row.get("qty"))
        or _num(row.get("shares"))
        or _num(row.get("filled_qty"))
        or 0.0
    )
    price = (
        _num(row.get("price"))
        or _num(row.get("fill_price"))
        or _num(row.get("avg_fill_price"))
        or _num(row.get("avg_price"))
    )
    notional = _num(row.get("notional"))

    if (notional is None or notional <= 0.0) and price is not None and qty:
        notional = abs(price * qty)
    if (price is None or price <= 0.0) and notional is not None and qty:
        price = abs(notional / qty)

    side = str(row.get("side") or row.get("action") or "").upper() or None
    status = str(row.get("status") or "").upper() or None

    out = dict(row)
    out["quantity"] = abs(qty)
    out["qty"] = abs(qty)
    out["price"] = price
    out["notional"] = abs(notional) if notional is not None else None
    out["side"] = side
    out["status"] = status
    if not out.get("ticker") and out.get("symbol"):
        out["ticker"] = out.get("symbol")
    return out


@router.get("/trades")
async def trades(
    limit: int = Query(200, ge=1, le=5000),
) -> dict[str, Any]:
    """Live paper-trading fills (one row per fill).

    Source: ``live/trades.jsonl`` if present.
    """
    rows = _read_jsonl(LIVE_DIR / "trades.jsonl", limit=limit)
    rows = [_normalize_trade_row(r) for r in rows if isinstance(r, dict)]
    return {"trades": rows, "count": len(rows)}


@router.get("/positions")
async def positions() -> dict[str, Any]:
    """Currently-open positions snapshot.

    The paper runner historically stored positions as a ``dict`` keyed
    by ticker (O(1) lookup on the execution path). The frontend, and
    the public API contract, expect a list of position rows — so we
    normalise here, preserving the ticker key as ``ticker`` on the row.
    This keeps legacy ``positions.json`` files on disk renderable
    without a runner restart.

    Source: ``live/positions.json`` if present, else empty list.
    """
    path = LIVE_DIR / "positions.json"
    empty = {
        "positions": [],
        "count": 0,
        "as_of": None,
        "cash": None,
        "mode": None,
        "max_mark_age_seconds": None,
    }
    text = _read_text_any(path)
    if not text:
        return empty
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return empty

    if isinstance(data, list):
        return {
            "positions": data,
            "count": len(data),
            "as_of": None,
            "cash": None,
            "mode": None,
            "max_mark_age_seconds": None,
        }
    if not isinstance(data, dict):
        return empty

    raw = data.get("positions", [])
    rows: list[dict[str, Any]] = []
    if isinstance(raw, list):
        rows = [r for r in raw if isinstance(r, dict)]
    elif isinstance(raw, dict):
        for ticker, pos in raw.items():
            if not isinstance(pos, dict):
                continue
            qty = pos.get("shares", pos.get("quantity", pos.get("qty", 0)))
            try:
                qty_num = float(qty)
            except (TypeError, ValueError):
                qty_num = 0.0
            entry_price = pos.get("entry_price", pos.get("avg_price"))
            market_price = pos.get("market_price", pos.get("mark", entry_price))
            try:
                mv = float(market_price) * qty_num if market_price is not None else None
            except (TypeError, ValueError):
                mv = None
            try:
                upnl = (
                    (float(market_price) - float(entry_price)) * qty_num
                    if market_price is not None and entry_price is not None
                    else 0.0
                )
            except (TypeError, ValueError):
                upnl = 0.0
            rows.append(
                {
                    "ticker": ticker,
                    "quantity": qty_num,
                    "side": "LONG" if qty_num >= 0 else "SHORT",
                    "avg_price": entry_price,
                    "avg_cost": pos.get("avg_cost", entry_price),
                    "market_price": market_price,
                    "mark_price_timestamp": pos.get("mark_price_timestamp"),
                    "mark_price_source": pos.get("mark_price_source"),
                    "mark_age_seconds": pos.get("mark_age_seconds"),
                    "market_value": mv,
                    "unrealized_pnl": pos.get("unrealized_pnl", round(upnl, 2)),
                    "realized_pnl": pos.get("realized_pnl", 0.0),
                    "stop_loss_price": pos.get("stop_loss_price"),
                    "take_profit_price": pos.get("take_profit_price"),
                    "stop_loss_pct": pos.get("stop_loss_pct"),
                    "take_profit_pct": pos.get("take_profit_pct"),
                    "asset_class": pos.get("asset_class"),
                    "contract_multiplier": pos.get("contract_multiplier"),
                    "margin_used": pos.get("margin_used"),
                    "notional": pos.get("notional"),
                    "entered_at": pos.get("entered_at"),
                    "strategy": pos.get("strategy"),
                    "strategy_name": pos.get("strategy_name"),
                    "source": pos.get("source"),
                    "rationale": pos.get("rationale"),
                    "signal_key": pos.get("signal_key"),
                }
            )

    return {
        "positions": rows,
        "count": len(rows),
        "as_of": data.get("as_of") or data.get("timestamp"),
        "cash": data.get("cash"),
        "mode": data.get("mode"),
        "max_mark_age_seconds": data.get("max_mark_age_seconds"),
    }


_SIGNAL_DTO_KEYS = (
    # Identity
    "ticker", "asset_class", "instrument_type", "strategy_name", "direction",
    # Pricing
    "entry_price", "current_price", "stop_loss_price", "take_profit_price",
    "stop_loss_pct", "take_profit_pct", "risk_reward_ratio",
    "distance_to_entry_pct", "entry_zone_low", "entry_zone_high",
    "entry_price_at_compute",
    # Sizing
    "quantity", "notional_value", "risk_amount", "kelly_fraction",
    "lot_size", "contract_multiplier", "pip_value", "margin_required",
    # Options
    "strike", "expiry_date", "option_type", "leg_2_strike", "leg_2_expiry",
    "second_leg",
    # Quality
    "conviction", "factor_score", "expected_r", "min_r", "rationale",
    # Readiness
    "readiness_status", "readiness_reason", "readiness_checked_at",
    # Timestamps + metadata
    "computed_at", "expires_at", "ttl_minutes", "regime_fingerprint",
    "thesis_signature", "tier3_shock", "price_at_compute",
)


def _signal_to_dto(raw: dict[str, Any]) -> dict[str, Any]:
    """Project the disk-shape signal into the public API DTO.

    Read-only — never mutates the input. Missing fields are filled
    with sensible defaults (``None`` / ``0`` / ``"WAITING"``) so the
    frontend type-system has a stable shape regardless of which
    pipeline-version wrote the entry.
    """
    out: dict[str, Any] = {}
    for k in _SIGNAL_DTO_KEYS:
        out[k] = raw.get(k)
    # Default readiness for legacy entries that pre-date the field.
    if not out.get("readiness_status"):
        out["readiness_status"] = "WAITING"
    if out.get("readiness_reason") is None:
        out["readiness_reason"] = ""
    return out


@router.get("/signals")
async def signals() -> dict[str, Any]:
    """Latest pre-computed signal book entries with readiness state.

    Source: ``data_store/signal_book.json``. Each entry carries
    everything the trading view needs — entry price, SL/TP, quantity,
    notional, R:R, plus the live readiness gate (READY / WAITING /
    EXPIRED / CANCELLED) maintained by ``TradeReadinessChecker``.
    """
    text = _read_text_any(SIGNAL_BOOK)
    if not text:
        return {
            "signals": [],
            "count": 0,
            "ready_count": 0,
            "waiting_count": 0,
            "expired_count": 0,
            "cancelled_count": 0,
            "total": 0,
            "snapshot_at": None,
        }
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {
            "signals": [],
            "count": 0,
            "ready_count": 0,
            "waiting_count": 0,
            "expired_count": 0,
            "cancelled_count": 0,
            "total": 0,
            "snapshot_at": None,
        }

    raw_list: list[dict[str, Any]] = []
    if isinstance(data, dict):
        raw_list = [r for r in data.values() if isinstance(r, dict)]
    elif isinstance(data, list):
        raw_list = [r for r in data if isinstance(r, dict)]

    dtos = [_signal_to_dto(r) for r in raw_list]
    dtos.sort(key=lambda s: s.get("computed_at") or "", reverse=True)

    ready = sum(1 for s in dtos if s.get("readiness_status") == "READY")
    waiting = sum(1 for s in dtos if s.get("readiness_status") == "WAITING")
    expired = sum(1 for s in dtos if s.get("readiness_status") == "EXPIRED")
    cancelled = sum(1 for s in dtos if s.get("readiness_status") == "CANCELLED")

    import datetime as _dt

    return {
        "signals": dtos,
        "count": len(dtos),
        "ready_count": ready,
        "waiting_count": waiting,
        "expired_count": expired,
        "cancelled_count": cancelled,
        "total": len(dtos),
        "snapshot_at": _dt.datetime.now(_dt.UTC).isoformat(),
    }


@router.get("/signals/history")
async def signals_history(
    start: str | None = Query(None, description="ISO timestamp lower bound"),
    end: str | None = Query(None, description="ISO timestamp upper bound"),
    ticker: str | None = Query(None),
    asset_class: str | None = Query(None),
    status: str | None = Query(None, description="readiness_status filter"),
    event: str | None = Query(None, description="event filter (CREATED, EXPIRED, …)"),
    limit: int = Query(1000, ge=1, le=10000),
) -> dict[str, Any]:
    """Filtered scan over the immortal ``signal_history.jsonl`` ledger.

    The ledger is append-only and bounded only by disk — every signal
    AYVID has ever generated stays here, even after the active book's
    LRU evicts it. The Signals page's "Full History" toggle hits this
    endpoint with a date range; the active /signals route still serves
    the live book for the default view.
    """
    import datetime as _dt

    from execution_engine.signal_book import get_signal_book

    def _parse_ts(s: str | None) -> _dt.datetime | None:
        if not s:
            return None
        try:
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    book = get_signal_book()
    records = book.get_history(
        start=_parse_ts(start),
        end=_parse_ts(end),
        ticker=ticker.upper() if ticker else None,
        asset_class=asset_class.upper() if asset_class else None,
        status=status.upper() if status else None,
        event=event.upper() if event else None,
        limit=limit,
    )

    by_status: dict[str, int] = {}
    by_asset_class: dict[str, int] = {}
    by_event: dict[str, int] = {}
    by_ticker: dict[str, int] = {}
    for r in records:
        s = str(r.get("readiness_status") or "?")
        by_status[s] = by_status.get(s, 0) + 1
        ac = str(r.get("asset_class") or "?")
        by_asset_class[ac] = by_asset_class.get(ac, 0) + 1
        ev = str(r.get("event") or "?")
        by_event[ev] = by_event.get(ev, 0) + 1
        t = str(r.get("ticker") or "?")
        by_ticker[t] = by_ticker.get(t, 0) + 1

    return {
        "records": records,
        "total": len(records),
        "by_status": by_status,
        "by_asset_class": by_asset_class,
        "by_event": by_event,
        "by_ticker": dict(
            sorted(by_ticker.items(), key=lambda kv: -kv[1])[:20]
        ),
        "earliest": (
            records[-1].get("logged_at") or records[-1].get("computed_at")
            if records else None
        ),
        "latest": (
            records[0].get("logged_at") or records[0].get("computed_at")
            if records else None
        ),
        "snapshot_at": _dt.datetime.now(_dt.UTC).isoformat(),
    }


def _list_backtest_files_gcs() -> list[tuple[str, str]]:
    """List backtest JSON files from GCS. Returns [(blob_name, text), ...]."""
    if not _GCS_BUCKET:
        return []
    global _gcs_client
    try:
        if _gcs_client is None:
            from google.cloud import storage
            _gcs_client = storage.Client()
        prefix = f"{_GCS_PREFIX}/document/backtests/" if _GCS_PREFIX else "document/backtests/"
        blobs = _gcs_client.bucket(_GCS_BUCKET).list_blobs(prefix=prefix)
        results = []
        for blob in blobs:
            if blob.name.endswith(".json"):
                text = blob.download_as_text()
                # Extract filename from blob path
                fname = blob.name.rsplit("/", 1)[-1]
                results.append((fname, text))
        return results
    except Exception as exc:
        log.warning(f"gcs backtest listing failed: {exc}")
        return []


@router.get("/backtests")
async def backtests_index() -> dict[str, Any]:
    """List every backtest report on disk with summary metadata."""
    valid_kinds = {"walkforward", "shock_driven", "single_window"}
    reports: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Try local disk first
    if BACKTESTS_DIR.exists():
        for path in sorted(BACKTESTS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            summary = _summarize_backtest(data)
            if summary.get("kind") not in valid_kinds:
                continue
            summary["name"] = path.stem
            summary["filename"] = path.name
            summary["size_bytes"] = path.stat().st_size
            reports.append(summary)
            seen_names.add(path.name)

    # Fallback / supplement from GCS
    if not reports or _GCS_BUCKET:
        for fname, text in _list_backtest_files_gcs():
            if fname in seen_names:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            summary = _summarize_backtest(data)
            if summary.get("kind") not in valid_kinds:
                continue
            summary["name"] = fname.replace(".json", "")
            summary["filename"] = fname
            summary["size_bytes"] = len(text.encode())
            reports.append(summary)

    reports.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
    return {"reports": reports, "count": len(reports)}


def _summarize_backtest(data: dict[str, Any]) -> dict[str, Any]:
    """Pull the headline metrics out of any of our backtest JSON shapes."""
    out: dict[str, Any] = {
        "kind": "unknown",
        "generated_at": data.get("generated_at"),
    }
    # Side-by-side comparison metrics — populated for both
    # walkforward and shock_driven so the dashboard can show
    # Naive vs Shock-driven on a single row without a second
    # round-trip to /backtests/{name}.
    common = {
        "pooled_win_rate": data.get("pooled_win_rate"),
        "avg_sharpe": data.get("avg_sharpe"),
        "worst_drawdown_pct": data.get("worst_drawdown_pct"),
        "avg_cost_drag_pct": data.get("avg_cost_drag_pct"),
    }
    if "windows" in data and isinstance(data["windows"], list):
        out["kind"] = "walkforward"
        out.update(
            {
                "span": f"{data.get('start_date')} → {data.get('end_date')}",
                "windows": data.get("n_valid_windows", len(data["windows"])),
                "avg_return_pct": data.get("avg_return_pct"),
                "median_return_pct": data.get("median_return_pct"),
                "compounded_return_pct": data.get("aggregate_compounded_return_pct"),
                "cagr_pct": data.get("aggregate_cagr_pct"),
                "pct_positive": data.get("pct_positive_windows"),
                "median_sharpe": data.get("median_sharpe"),
                "pooled_trades": data.get("pooled_total_trades"),
                **common,
            }
        )
    elif "scenarios" in data and isinstance(data["scenarios"], list):
        out["kind"] = "shock_driven"
        out.update(
            {
                "span": f"{data.get('span_start')} → {data.get('span_end')}",
                "scenarios": data.get("n_valid", len(data["scenarios"])),
                "avg_return_pct": data.get("avg_return_pct"),
                "median_return_pct": data.get("median_return_pct"),
                "compounded_return_pct": data.get("aggregate_compounded_return_pct"),
                "cagr_pct": data.get("aggregate_cagr_pct"),
                "pct_positive": data.get("pct_positive_scenarios"),
                "median_sharpe": data.get("median_sharpe"),
                "pooled_trades": data.get("pooled_total_trades"),
                **common,
            }
        )
    elif "total_return_pct" in data:
        out["kind"] = "single_window"
        out.update(
            {
                "total_return_pct": data.get("total_return_pct"),
                "sharpe_ratio": data.get("sharpe_ratio"),
                "max_drawdown_pct": data.get("max_drawdown_pct"),
                "total_trades": data.get("total_trades"),
            }
        )
    return out


@router.get("/backtests/{name}")
async def backtest_detail(name: str) -> dict[str, Any]:
    """Full JSON of a named backtest report."""
    safe_name = name.replace("/", "").replace("..", "")
    path = BACKTESTS_DIR / f"{safe_name}.json"
    text = _read_text_any(path)
    if not text:
        raise HTTPException(status_code=404, detail=f"Backtest '{name}' not found")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt JSON: {exc}") from exc


@router.get("/scenarios")
async def shock_scenarios() -> dict[str, Any]:
    """The named-shock catalog the engine trades against."""
    from backtesting.engine import SHOCK_SCENARIOS

    items = []
    for sid, scen in SHOCK_SCENARIOS.items():
        causal = scen.get("causal_intel", {}) or {}
        items.append(
            {
                "id": sid,
                "name": scen.get("name"),
                "start_date": scen.get("start_date"),
                "end_date": scen.get("end_date"),
                "description": scen.get("description"),
                "tickers": scen.get("tickers", []),
                "indices": scen.get("indices", []),
                "shock_type": causal.get("shock_type"),
                "expected_direction": causal.get("expected_direction"),
                "beneficiary_tickers": causal.get("beneficiary_tickers", []),
                "victim_tickers": causal.get("victim_tickers", []),
                "index_bias": causal.get("index_bias", {}),
                "causal_chain": causal.get("causal_chain"),
            }
        )
    items.sort(key=lambda x: x.get("start_date") or "")
    return {"scenarios": items, "count": len(items)}


@router.get("/system-status")
async def system_status() -> dict[str, Any]:
    """Aggregate health: API + paper runner + Neo4j + LLM + recent backtest.

    All probes are best-effort. A failed probe is reported as ``ok: false``
    with a short error string; it never propagates a 500 to the frontend,
    because this endpoint is what the dashboard uses to render its own
    health panel.
    """
    import inspect

    # Paper runner — already a pure file read, can't really fail
    try:
        runner = await runner_status()
    except Exception as exc:  # pragma: no cover — defensive
        runner = {"is_alive": False, "error": str(exc)[:200]}

    # Backtests index
    try:
        bt = await backtests_index()
        latest_bt = bt["reports"][0] if bt["reports"] else None
    except Exception as exc:  # pragma: no cover
        latest_bt = {"error": str(exc)[:200]}

    # Neo4j — handles both async and sync `get_graph_stats` shapes, and
    # treats any connection failure as a clean "not running" signal so the
    # frontend can render an honest red dot instead of a 500.
    neo4j_ok = False
    neo4j_stats: dict[str, Any] | None = None
    neo4j_error: str | None = None
    try:
        from graph_intelligence.tools.neo4j_tools import get_graph_stats

        result = get_graph_stats()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            neo4j_stats = result
            neo4j_ok = True
        else:
            neo4j_error = f"unexpected stats type: {type(result).__name__}"
    except Exception as exc:
        neo4j_error = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__

    # LLM — surface the configured provider and whether a key is wired up.
    from shared.config import settings

    llm_provider = getattr(settings, "LLM_PROVIDER", None)
    llm_model = getattr(settings, "LLM_MODEL", None) or getattr(settings, "OPENAI_MODEL", None)
    has_key = False
    if llm_provider:
        key_attr = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
        }.get(str(llm_provider).lower())
        if key_attr:
            has_key = bool(getattr(settings, key_attr, None))

    auto_exec = bool(getattr(settings, "AUTO_EXECUTE", False))

    return {
        "api": "ok",
        "paper_runner": runner,
        "neo4j": {
            "ok": neo4j_ok,
            "stats": neo4j_stats,
            "error": neo4j_error,
            "uri": getattr(settings, "NEO4J_URI", None),
        },
        "llm": {
            "ok": bool(llm_provider and has_key),
            "provider": llm_provider,
            "model": llm_model,
            "has_key": has_key,
        },
        "latest_backtest": latest_bt,
        "config": {
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "ib_host": getattr(settings, "IB_HOST", None),
            "ib_port": getattr(settings, "IB_PORT", None),
            "auto_execute": auto_exec,
        },
    }


# ── Multi-Asset Allocation ──────────────────────────────────────────


@router.get("/allocation")
async def get_allocation():
    """Current asset-class allocation breakdown."""
    positions = {}
    pos_path = LIVE_DIR / "positions.json"
    raw = await _read_text_any(pos_path)
    if raw:
        positions = json.loads(raw)

    # Bucket positions by asset class
    allocation: dict[str, dict] = {}
    items = positions.values() if isinstance(positions, dict) else positions
    for pos in items:
        ac = str(pos.get("instrument_type", pos.get("asset_class", "EQUITY"))).upper()
        if ac not in allocation:
            allocation[ac] = {"count": 0, "notional": 0.0}
        allocation[ac]["count"] += 1
        price = float(pos.get("entry_price", 0) or 0)
        shares = abs(int(pos.get("shares", pos.get("qty", 1)) or 1))
        allocation[ac]["notional"] += price * shares

    return {
        "asset_classes": [
            {"name": k, "positions": v["count"], "notional": round(v["notional"], 2)}
            for k, v in sorted(allocation.items())
        ],
        "total_positions": sum(v["count"] for v in allocation.values()),
    }


# ── Win Rates by Strategy ──────────────────────────────────────────


@router.get("/win-rates")
async def get_win_rates():
    """Win rates per strategy from the latest backtest report."""
    latest_bt = None
    for bt_dir in sorted(BACKTESTS_DIR.glob("*"), reverse=True):
        rpt = bt_dir / "backtest_report.json"
        if rpt.exists():
            latest_bt = rpt
            break
    if not latest_bt:
        return {"strategies": [], "source": "none"}

    raw = latest_bt.read_text()
    report = json.loads(raw)
    strats = report.get("strategy_attribution", [])
    return {
        "strategies": [
            {
                "name": s["strategy"],
                "win_rate": s.get("win_rate", 0),
                "trades": s.get("trades", 0),
                "pnl": s.get("pnl", 0),
            }
            for s in strats
        ],
        "source": str(latest_bt.name),
    }


# ── Portfolio Risk Status ───────────────────────────────────────────


@router.get("/risk-status")
async def get_risk_status():
    """Portfolio-level risk snapshot: exposure, drawdown, per-asset-class limits."""
    from execution_engine.math.portfolio_risk import get_portfolio_risk_manager

    mgr = get_portfolio_risk_manager()
    return mgr.get_stats()


# ── SSE Signal Readiness Stream ─────────────────────────────────────


@router.get("/signals/stream")
async def signals_stream():
    """Server-Sent Events stream of signal readiness changes.

    Polls ``data_store/signal_book.json`` every 2 seconds and emits
    one event per ticker whose ``readiness_status`` flipped since
    the last tick (e.g. WAITING → READY when the live mark crosses
    into the entry zone). The frontend Signals page subscribes to
    this stream so the UI reflects readiness in near-real-time
    without polling the full book.
    """
    import asyncio as _asyncio

    async def event_generator():
        last_states: dict[str, str] = {}
        # Send a snapshot of current readiness on connect so the UI
        # can flash any signals that are already READY.
        snapshot = await signals()
        for sig in snapshot.get("signals", []):
            ticker = sig.get("ticker") or ""
            fp = sig.get("regime_fingerprint") or ""
            key = f"{ticker}|{fp}"
            status = sig.get("readiness_status") or "WAITING"
            last_states[key] = status
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "snapshot",
                        "ticker": ticker,
                        "asset_class": sig.get("asset_class"),
                        "direction": sig.get("direction"),
                        "readiness_status": status,
                        "readiness_reason": sig.get("readiness_reason"),
                        "current_price": sig.get("current_price"),
                        "entry_price": sig.get("entry_price"),
                        "stop_loss_price": sig.get("stop_loss_price"),
                        "take_profit_price": sig.get("take_profit_price"),
                        "quantity": sig.get("quantity"),
                        "distance_to_entry_pct": sig.get("distance_to_entry_pct"),
                        "conviction": sig.get("conviction"),
                        "strategy_name": sig.get("strategy_name"),
                    }
                )
                + "\n\n"
            )

        # Heartbeat / diff loop. Keep-alive every 30s in case nothing
        # has changed; readiness diffs are emitted as they happen.
        while True:
            await _asyncio.sleep(2.0)
            try:
                snap = await signals()
            except Exception as exc:
                log.warning(f"signals_stream snapshot failed: {exc}")
                continue
            seen: set[str] = set()
            for sig in snap.get("signals", []):
                ticker = sig.get("ticker") or ""
                fp = sig.get("regime_fingerprint") or ""
                key = f"{ticker}|{fp}"
                seen.add(key)
                status = sig.get("readiness_status") or "WAITING"
                if last_states.get(key) == status:
                    continue
                last_states[key] = status
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "readiness_change",
                            "ticker": ticker,
                            "asset_class": sig.get("asset_class"),
                            "direction": sig.get("direction"),
                            "readiness_status": status,
                            "readiness_reason": sig.get("readiness_reason"),
                            "current_price": sig.get("current_price"),
                            "entry_price": sig.get("entry_price"),
                            "stop_loss_price": sig.get("stop_loss_price"),
                            "take_profit_price": sig.get("take_profit_price"),
                            "quantity": sig.get("quantity"),
                            "distance_to_entry_pct": sig.get("distance_to_entry_pct"),
                            "conviction": sig.get("conviction"),
                            "strategy_name": sig.get("strategy_name"),
                        }
                    )
                    + "\n\n"
                )
            # Drop stale keys that no longer appear in the book so the
            # in-memory dict doesn't grow unbounded across long runs.
            for k in list(last_states.keys()):
                if k not in seen:
                    del last_states[k]

            # Keep-alive ping so proxies don't kill idle connections.
            yield 'data: {"type":"heartbeat"}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── SSE Agent Activity Feed ─────────────────────────────────────────


@router.get("/agent-activity")
async def agent_activity_sse():
    """Server-Sent Events stream of agent activity."""
    from backend.core.agent_event_bus import get_event_bus

    bus = get_event_bus()

    async def event_generator():
        # Send recent history first
        for evt in bus.recent(20):
            yield f"data: {json.dumps(evt.to_dict())}\n\n"

        # Then stream live events
        async for evt in bus.subscribe():
            yield f"data: {json.dumps(evt.to_dict())}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
