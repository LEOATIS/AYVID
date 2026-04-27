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
    initial = float(rows[0].get("equity", 0.0))
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
        "meta": _read_meta(),
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


@router.get("/trades")
async def trades(
    limit: int = Query(200, ge=1, le=5000),
) -> dict[str, Any]:
    """Live paper-trading fills (one row per fill).

    Source: ``live/trades.jsonl`` if present.
    """
    rows = _read_jsonl(LIVE_DIR / "trades.jsonl", limit=limit)
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
    empty = {"positions": [], "count": 0, "as_of": None, "cash": None}
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
                    "market_price": market_price,
                    "market_value": mv,
                    "unrealized_pnl": pos.get("unrealized_pnl", round(upnl, 2)),
                    "realized_pnl": pos.get("realized_pnl", 0.0),
                    "entered_at": pos.get("entered_at"),
                    "strategy": pos.get("strategy"),
                    "source": pos.get("source"),
                    "rationale": pos.get("rationale"),
                }
            )

    return {
        "positions": rows,
        "count": len(rows),
        "as_of": data.get("as_of") or data.get("timestamp"),
        "cash": data.get("cash"),
    }


@router.get("/signals")
async def signals() -> dict[str, Any]:
    """Latest pre-computed signal book entries.

    Source: ``data_store/signal_book.json``.
    """
    if not SIGNAL_BOOK.exists():
        return {"signals": [], "count": 0}
    try:
        data = json.loads(SIGNAL_BOOK.read_text())
    except json.JSONDecodeError:
        return {"signals": [], "count": 0}
    signals_list: list[dict[str, Any]] = []
    if isinstance(data, dict):
        signals_list = list(data.values())
    elif isinstance(data, list):
        signals_list = data
    signals_list.sort(
        key=lambda s: s.get("computed_at", "") if isinstance(s, dict) else "",
        reverse=True,
    )
    return {"signals": signals_list, "count": len(signals_list)}


@router.get("/backtests")
async def backtests_index() -> dict[str, Any]:
    """List every backtest report on disk with summary metadata."""
    if not BACKTESTS_DIR.exists():
        return {"reports": []}
    reports: list[dict[str, Any]] = []
    for path in sorted(BACKTESTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        summary = _summarize_backtest(data)
        summary["name"] = path.stem
        summary["filename"] = path.name
        summary["size_bytes"] = path.stat().st_size
        reports.append(summary)
    reports.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
    return {"reports": reports, "count": len(reports)}


def _summarize_backtest(data: dict[str, Any]) -> dict[str, Any]:
    """Pull the headline metrics out of any of our backtest JSON shapes."""
    out: dict[str, Any] = {
        "kind": "unknown",
        "generated_at": data.get("generated_at"),
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
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Backtest '{name}' not found")
    try:
        return json.loads(path.read_text())
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
