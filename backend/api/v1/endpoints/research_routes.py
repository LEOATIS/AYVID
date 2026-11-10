"""Research workspace API — research, companies, theses, screener, watchlists, valuation.

Mounted at ``/api/v1``. Isolated from trade routes: nothing here can reach
the execution engine. Workspace scoping comes from ``X-Workspace-ID`` /
``X-User-ID`` headers (defaulting to the seeded local workspace).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from backend.core.security import verify_api_key
from backend.services.research_calc import CalculationError, dcf, dcf_sensitivity, staleness
from backend.services.research_orchestrator import ResearchOrchestrator
from backend.services.research_resolver import resolve_symbol
from backend.services.research_screener import parse_plan, run_screen
from backend.services.research_seed import OHLCV_DIR, REPO_ROOT, read_bars, seed_research_db
from backend.services.research_store import (
    DEFAULT_USER,
    DEFAULT_WORKSPACE,
    Actor,
    ForbiddenError,
    NotFoundError,
    ResearchStore,
    health_of,
)
from backend.services.research_thesis import (
    attach_session,
    evaluate_thesis,
    record_event,
    thesis_from_session,
)
from shared.config import settings
from shared.research_types import (
    DCFInput,
    ResearchQuery,
    ScreenerQuery,
    ThesisConditionIn,
    ThesisCreate,
    ThesisEvidenceIn,
    ThesisUpdate,
    WatchlistCreate,
    WatchlistItemIn,
)

router = APIRouter(dependencies=[Depends(verify_api_key)])

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_store: ResearchStore | None = None
_store_lock = threading.Lock()


def get_store() -> ResearchStore:
    global _store
    with _store_lock:
        if _store is None:
            path = Path(settings.RESEARCH_DB_PATH)
            store = ResearchStore(path if path.is_absolute() else REPO_ROOT / path)
            seed_research_db(store)
            _store = store
    return _store


def get_actor(
    x_workspace_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> Actor:
    ws = x_workspace_id or DEFAULT_WORKSPACE
    user = x_user_id or DEFAULT_USER
    if not _ID_RE.match(ws) or not _ID_RE.match(user):
        raise HTTPException(400, "X-Workspace-ID / X-User-ID must be 1-64 chars of [A-Za-z0-9_-]")
    return Actor(workspace_id=ws, user_id=user)


def _errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Map domain errors to HTTP status codes."""

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except ForbiddenError as exc:
            raise HTTPException(403, str(exc)) from exc
        except NotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except CalculationError as exc:
            raise HTTPException(422, str(exc)) from exc

    return wrapper


Store = Depends(get_store)
Who = Depends(get_actor)


# ── meta ──────────────────────────────────────────────────────────────
@router.get("/research/meta")
@_errors
async def research_meta(store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    from shared import llm_client

    provider = llm_client.active_provider()
    n = store.one("SELECT COUNT(*) AS n FROM instruments WHERE data_tier != 'REFERENCE_ONLY'")["n"]
    last = store.one("SELECT MAX(last_bar) AS d FROM instruments")["d"]
    return {
        "workspace_id": actor.workspace_id,
        "user_id": actor.user_id,
        "llm": {"provider": provider, "model": llm_client.active_model(provider),
                "configured": [p for p in llm_client.PROVIDERS if llm_client._provider_key(p)]},
        "data": {"instruments": n, "latest_bar": last, "stale": staleness(last, settings.RESEARCH_STALE_DAYS),
                 "live_edgar_enabled": settings.RESEARCH_LIVE_EDGAR,
                 "mode_label": "Verified Local + SEC EDGAR" if settings.RESEARCH_LIVE_EDGAR else "Local verified data"},
        "safety": {"PAPER_TRADING": settings.PAPER_TRADING, "AUTO_EXECUTE": settings.AUTO_EXECUTE,
                   "LIVE_TRADING_CONFIRMED": settings.LIVE_TRADING_CONFIRMED, "research_can_trade": False},
    }


# ── research ──────────────────────────────────────────────────────────
@router.get("/research/resolve")
@_errors
async def resolve(q: str = Query(min_length=1, max_length=64), store: ResearchStore = Store) -> dict[str, Any]:
    return resolve_symbol(store, q)


@router.post("/research/query")
@_errors
async def research_query(
    body: ResearchQuery,
    simulate_provider_failure: str | None = Query(default=None, pattern="^(edgar|llm|all)$"),
    store: ResearchStore = Store,
    actor: Actor = Who,
) -> dict[str, Any]:
    return await ResearchOrchestrator(store).run(
        actor, body.query,
        simulate_failure=simulate_provider_failure or body.simulate_provider_failure,
        instrument_ids=body.instrument_ids,
    )


@router.get("/research/sessions")
@_errors
async def list_sessions(limit: int = Query(20, ge=1, le=100), store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    return store.list_sessions(actor, limit)


@router.get("/research/sessions/{session_id}")
@_errors
async def get_session(session_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.get_session(actor, session_id)["artifact"]


@router.get("/research/sources/{source_id}")
@_errors
async def get_source(source_id: str, store: ResearchStore = Store) -> dict[str, Any]:
    src = store.get_source(source_id)
    if src is None:
        raise NotFoundError("source not found")
    return src


@router.get("/research/edges/{edge_id}")
@_errors
async def get_edge(edge_id: str, store: ResearchStore = Store) -> dict[str, Any]:
    edge = store.get_edge(edge_id)
    if edge is None:
        raise NotFoundError("edge not found")
    return {**edge, "source": store.get_source(edge["source_id"]) if edge.get("source_id") else None}


@router.post("/research/events")
@_errors
async def post_event(body: dict[str, str], store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    code = str(body.get("code", "")).strip()
    if not re.match(r"^[A-Z0-9_:.\-]{3,80}$", code):
        raise HTTPException(422, "code must be 3-80 chars of A-Z0-9_:.-")
    record_event(store, actor, code, str(body.get("description", ""))[:500])
    return {"status": "recorded", "code": code}


# ── companies ─────────────────────────────────────────────────────────
@router.get("/companies")
@_errors
async def list_companies(store: ResearchStore = Store) -> list[dict[str, Any]]:
    return [i for i in store.list_instruments() if i["data_tier"] != "REFERENCE_ONLY"]


@router.get("/companies/{instrument_id}")
@_errors
async def company(instrument_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    inst = store.get_instrument(instrument_id)
    obs = store.observations(instrument_id)
    sym = inst["local_symbol"] or inst["symbol"]
    edges = store.edges_touching([sym])
    theses = [t for t in store.list_theses(actor) if instrument_id in t["instrument_ids"]]
    fresh = staleness(inst["last_bar"], settings.RESEARCH_STALE_DAYS)
    return {
        "instrument": inst,
        "freshness": fresh,
        "observations": obs,
        "sources": store.sources_for(instrument_id),
        "exposures": {
            "upstream": [e for e in edges if e["dst_symbol"] == sym],
            "downstream": [e for e in edges if e["src_symbol"] == sym],
        },
        "theses": [{k: t[k] for k in ("thesis_id", "title", "health", "updated_at")} for t in theses],
    }


@router.get("/companies/{instrument_id}/prices")
@_errors
async def company_prices(instrument_id: str, store: ResearchStore = Store) -> dict[str, Any]:
    inst = store.get_instrument(instrument_id)
    if not inst["local_symbol"]:
        raise NotFoundError("no local price series for this listing")
    bars, cleaning = read_bars(OHLCV_DIR / f"{inst['local_symbol']}.jsonl")
    return {
        "instrument_id": instrument_id,
        "currency": inst["currency"],
        "source_id": f"SRC-OHLCV-{inst['local_symbol']}",
        "freshness": staleness(inst["last_bar"], settings.RESEARCH_STALE_DAYS),
        "cleaning": cleaning,
        "bars": [{"t": b["timestamp"][:10], "close": round(b["close"], 4), "volume": b.get("volume")} for b in bars],
    }


# ── theses ────────────────────────────────────────────────────────────
@router.get("/theses")
@_errors
async def list_theses(store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    return store.list_theses(actor)


@router.post("/theses", status_code=201)
@_errors
async def create_thesis(body: ThesisCreate, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    for iid in body.instrument_ids:
        store.get_instrument(iid)
    thesis = store.create_thesis(actor, body.model_dump())
    evaluate_thesis(store, actor, thesis["thesis_id"])
    return store.get_thesis(actor, thesis["thesis_id"])


@router.post("/theses/from-research", status_code=201)
@_errors
async def thesis_from_research(body: dict[str, str], store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    session_id = str(body.get("session_id", ""))
    if not _ID_RE.match(session_id):
        raise HTTPException(422, "session_id required")
    title = body.get("title")
    return thesis_from_session(store, actor, session_id, title[:200] if title else None)


@router.get("/theses/{thesis_id}")
@_errors
async def get_thesis(thesis_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    thesis = store.get_thesis(actor, thesis_id)
    return {**thesis, "health": health_of([c["current_status"] for c in thesis["conditions"]])}


@router.patch("/theses/{thesis_id}")
@_errors
async def update_thesis(thesis_id: str, body: ThesisUpdate, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True, exclude={"change_note"})
    return store.update_thesis(actor, thesis_id, changes, body.change_note)


@router.post("/theses/{thesis_id}/evidence")
@_errors
async def add_evidence(thesis_id: str, body: ThesisEvidenceIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.add_evidence(actor, thesis_id, body.model_dump())


@router.delete("/theses/{thesis_id}/evidence/{evidence_id}")
@_errors
async def remove_evidence(thesis_id: str, evidence_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.remove_evidence(actor, thesis_id, evidence_id)


@router.post("/theses/{thesis_id}/conditions")
@_errors
async def add_condition(thesis_id: str, body: ThesisConditionIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    store.get_instrument(body.instrument_id)
    return store.add_condition(actor, thesis_id, body.model_dump())


@router.delete("/theses/{thesis_id}/conditions/{condition_id}")
@_errors
async def remove_condition(thesis_id: str, condition_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.remove_condition(actor, thesis_id, condition_id)


@router.post("/theses/{thesis_id}/evaluate")
@_errors
async def evaluate(thesis_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return evaluate_thesis(store, actor, thesis_id)


@router.post("/theses/{thesis_id}/revisions")
@_errors
async def approve_revision(thesis_id: str, body: dict[str, str], store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    note = str(body.get("change_note", "")).strip()[:500] or "Approved evaluation"
    return store.update_thesis(actor, thesis_id, {}, note)


@router.get("/theses/{thesis_id}/revisions/{revision_no}")
@_errors
async def get_revision(thesis_id: str, revision_no: int, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.get_revision(actor, thesis_id, revision_no)


@router.post("/theses/{thesis_id}/attach")
@_errors
async def attach(thesis_id: str, body: dict[str, str], store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    session_id = str(body.get("session_id", ""))
    if not _ID_RE.match(session_id):
        raise HTTPException(422, "session_id required")
    return attach_session(store, actor, thesis_id, session_id)


# ── screener ──────────────────────────────────────────────────────────
@router.post("/screener/query")
@_errors
async def screener(body: ScreenerQuery, store: ResearchStore = Store) -> dict[str, Any]:
    plan = parse_plan(body.query) if body.filters is None else {
        "query": body.query, "filters": [f.model_dump() for f in body.filters], "unparsed": []}
    return {"plan": plan, **run_screen(store, plan["filters"])}


# ── watchlists & notifications ───────────────────────────────────────
@router.get("/watchlists")
@_errors
async def list_watchlists(store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    lists = store.list_watchlists(actor)
    for wl in lists:
        for item in wl["items"]:
            item["freshness"] = staleness(item.get("last_bar"), settings.RESEARCH_STALE_DAYS)
            item["metrics"] = {
                code: (store.latest_observation(item["instrument_id"], code) or {}).get("value")
                for code in ("CLOSE_PRICE", "RETURN_90D_PCT", "REALIZED_VOL_ANN")
            }
            item["theses"] = [
                {"thesis_id": t["thesis_id"], "title": t["title"], "health": t["health"]}
                for t in store.list_theses(actor) if item["instrument_id"] in t["instrument_ids"]
            ]
    return lists


@router.post("/watchlists", status_code=201)
@_errors
async def create_watchlist(body: WatchlistCreate, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    return {"watchlist_id": store.create_watchlist(actor, body.name, body.instrument_ids)}


@router.post("/watchlists/{watchlist_id}/items")
@_errors
async def add_item(watchlist_id: str, body: WatchlistItemIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    store.add_watchlist_item(actor, watchlist_id, body.instrument_id)
    return {"status": "added"}


@router.delete("/watchlists/{watchlist_id}/items/{instrument_id}")
@_errors
async def remove_item(watchlist_id: str, instrument_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    store.remove_watchlist_item(actor, watchlist_id, instrument_id)
    return {"status": "removed"}


@router.post("/watchlists/run-checks")
@_errors
async def run_checks(store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    """Manual "Run check now": evaluates every thesis in the workspace."""
    results = [evaluate_thesis(store, actor, t["thesis_id"]) for t in store.list_theses(actor)]
    return {"evaluated": len(results), "results": [
        {"thesis_id": r["thesis_id"], "health": r["health"], "counts": r["counts"], "changed": len(r["changed"])}
        for r in results]}


@router.get("/notifications")
@_errors
async def notifications(store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    return store.list_notifications(actor)


# ── valuation sandbox ─────────────────────────────────────────────────
@router.post("/valuation/dcf")
@_errors
async def valuation(body: DCFInput) -> dict[str, Any]:
    params = body.model_dump()
    rate, growth = params["discount_rate_pct"], params["revenue_growth_pct"]
    rates = [round(rate + d, 2) for d in (-2, -1, 0, 1, 2)]
    growths = [round(growth + d, 2) for d in (-10, -5, 0, 5, 10)]
    return {
        "inputs": {k: {"value": v, "label": "ASSUMED"} for k, v in params.items()},
        "result": dcf(params),
        "sensitivity": {"discount_rates": rates, "growth_rates": growths,
                        "grid": dcf_sensitivity(params, rates, growths)},
    }
