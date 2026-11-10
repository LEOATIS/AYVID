"""Research workspace API — research, companies, theses, screener, watchlists, valuation.

Mounted at ``/api/v1``. Isolated from trade routes: nothing here can reach
the execution engine. Workspace scoping comes from ``X-Workspace-ID`` /
``X-User-ID`` headers (defaulting to the seeded local workspace).
"""

from __future__ import annotations

import inspect
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from decimal import InvalidOperation
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
    EventIn,
    ResearchQuery,
    RevisionIn,
    ScreenerQuery,
    SessionRef,
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


class _WindowLimiter:
    """Per-workspace sliding-window limit for routes that call paid LLMs / SEC.

    shortcut: in-process only — resets on restart and is per-worker; move to
    Redis (or the gateway) before running more than one API process.
    """

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                raise HTTPException(429, f"Rate limit: {self.per_minute} requests/minute per workspace")
            hits.append(now)


_query_limiter = _WindowLimiter(settings.RESEARCH_QUERY_RATE_PER_MIN)
_eval_limiter = _WindowLimiter(settings.RESEARCH_QUERY_RATE_PER_MIN * 3)
MAX_THESES_PER_CHECK = 50


def _map_error(exc: Exception) -> HTTPException | None:
    if isinstance(exc, ForbiddenError):
        return HTTPException(403, str(exc))
    if isinstance(exc, (NotFoundError, FileNotFoundError)):
        return HTTPException(404, str(exc) if isinstance(exc, NotFoundError) else "resource file not found")
    if isinstance(exc, sqlite3.IntegrityError):
        return HTTPException(409, "conflicts with an existing record")
    if isinstance(exc, (CalculationError, InvalidOperation, ValueError)):
        return HTTPException(422, str(exc) or "invalid value")
    return None


def _errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Map domain errors to HTTP status codes.

    Sync handlers stay sync so FastAPI runs their blocking SQLite work in its
    threadpool instead of on the event loop.
    """
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                mapped = _map_error(exc)
                if mapped is None:
                    raise
                raise mapped from exc

        return async_wrapper

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    return wrapper


Store = Depends(get_store)
Who = Depends(get_actor)


# ── meta ──────────────────────────────────────────────────────────────
@router.get("/research/meta")
@_errors
def research_meta(store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
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
def resolve(q: str = Query(min_length=1, max_length=64), store: ResearchStore = Store) -> dict[str, Any]:
    return resolve_symbol(store, q)


@router.post("/research/query")
@_errors
async def research_query(
    body: ResearchQuery,
    simulate_provider_failure: str | None = Query(default=None, pattern="^(edgar|llm|all)$"),
    store: ResearchStore = Store,
    actor: Actor = Who,
) -> dict[str, Any]:
    _query_limiter.check(actor.workspace_id)
    return await ResearchOrchestrator(store).run(
        actor, body.query,
        simulate_failure=simulate_provider_failure or body.simulate_provider_failure,
        instrument_ids=body.instrument_ids,
    )


@router.get("/research/sessions")
@_errors
def list_sessions(limit: int = Query(20, ge=1, le=100), store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    return store.list_sessions(actor, limit)


@router.get("/research/sessions/{session_id}")
@_errors
def get_session(session_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    artifact = store.get_session(actor, session_id)["artifact"]
    if artifact is None:
        raise NotFoundError("research session has no saved answer")
    return artifact


@router.get("/research/sources/{source_id}")
@_errors
def get_source(source_id: str, store: ResearchStore = Store) -> dict[str, Any]:
    src = store.get_source(source_id)
    if src is None:
        raise NotFoundError("source not found")
    return src


@router.get("/research/edges/{edge_id}")
@_errors
def get_edge(edge_id: str, store: ResearchStore = Store) -> dict[str, Any]:
    edge = store.get_edge(edge_id)
    if edge is None:
        raise NotFoundError("edge not found")
    return {**edge, "source": store.get_source(edge["source_id"]) if edge.get("source_id") else None}


@router.post("/research/events")
@_errors
def post_event(body: EventIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    record_event(store, actor, body.code, body.description)
    return {"status": "recorded", "code": body.code}


# ── companies ─────────────────────────────────────────────────────────
@router.get("/companies")
@_errors
def list_companies(store: ResearchStore = Store) -> list[dict[str, Any]]:
    return [i for i in store.list_instruments() if i["data_tier"] != "REFERENCE_ONLY"]


@router.get("/companies/{instrument_id}")
@_errors
def company(instrument_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
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
def company_prices(instrument_id: str, store: ResearchStore = Store) -> dict[str, Any]:
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
def list_theses(store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    return store.list_theses(actor)


@router.post("/theses", status_code=201)
@_errors
def create_thesis(body: ThesisCreate, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    for iid in {*body.instrument_ids, *(c.instrument_id for c in body.conditions)}:
        store.get_instrument(iid)
    for ev in body.evidence:
        if store.get_source(ev.source_id) is None:
            raise NotFoundError(f"source {ev.source_id} not found")
    if body.session_id:
        store.get_session(actor, body.session_id)
    thesis = store.create_thesis(actor, body.model_dump())
    evaluate_thesis(store, actor, thesis["thesis_id"])
    return store.get_thesis(actor, thesis["thesis_id"])


@router.post("/theses/from-research", status_code=201)
@_errors
def thesis_from_research(body: SessionRef, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return thesis_from_session(store, actor, body.session_id, body.title)


@router.get("/theses/{thesis_id}")
@_errors
def get_thesis(thesis_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    thesis = store.get_thesis(actor, thesis_id)
    return {**thesis, "health": health_of([c["current_status"] for c in thesis["conditions"]])}


@router.patch("/theses/{thesis_id}")
@_errors
def update_thesis(thesis_id: str, body: ThesisUpdate, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True, exclude={"change_note"})
    return store.update_thesis(actor, thesis_id, changes, body.change_note)


@router.post("/theses/{thesis_id}/evidence")
@_errors
def add_evidence(thesis_id: str, body: ThesisEvidenceIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.add_evidence(actor, thesis_id, body.model_dump())


@router.delete("/theses/{thesis_id}/evidence/{evidence_id}")
@_errors
def remove_evidence(thesis_id: str, evidence_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.remove_evidence(actor, thesis_id, evidence_id)


@router.post("/theses/{thesis_id}/conditions")
@_errors
def add_condition(thesis_id: str, body: ThesisConditionIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    store.get_instrument(body.instrument_id)
    return store.add_condition(actor, thesis_id, body.model_dump())


@router.delete("/theses/{thesis_id}/conditions/{condition_id}")
@_errors
def remove_condition(thesis_id: str, condition_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.remove_condition(actor, thesis_id, condition_id)


@router.post("/theses/{thesis_id}/evaluate")
@_errors
def evaluate(thesis_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    _eval_limiter.check(actor.workspace_id)
    return evaluate_thesis(store, actor, thesis_id)


@router.post("/theses/{thesis_id}/revisions")
@_errors
def approve_revision(thesis_id: str, body: RevisionIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.update_thesis(actor, thesis_id, {}, body.change_note.strip() or "Approved evaluation")


@router.get("/theses/{thesis_id}/revisions/{revision_no}")
@_errors
def get_revision(thesis_id: str, revision_no: int, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return store.get_revision(actor, thesis_id, revision_no)


@router.post("/theses/{thesis_id}/attach")
@_errors
def attach(thesis_id: str, body: SessionRef, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    return attach_session(store, actor, thesis_id, body.session_id)


# ── screener ──────────────────────────────────────────────────────────
@router.post("/screener/query")
@_errors
def screener(body: ScreenerQuery, store: ResearchStore = Store) -> dict[str, Any]:
    plan = parse_plan(body.query) if body.filters is None else {
        "query": body.query, "filters": [f.model_dump() for f in body.filters], "unparsed": []}
    return {"plan": plan, **run_screen(store, plan["filters"])}


# ── watchlists & notifications ───────────────────────────────────────
@router.get("/watchlists")
@_errors
def list_watchlists(store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    lists = store.list_watchlists(actor)
    theses = store.list_theses(actor)
    for wl in lists:
        for item in wl["items"]:
            item["freshness"] = staleness(item.get("last_bar"), settings.RESEARCH_STALE_DAYS)
            item["metrics"] = {
                code: (store.latest_observation(item["instrument_id"], code) or {}).get("value")
                for code in ("CLOSE_PRICE", "RETURN_90D_PCT", "REALIZED_VOL_ANN")
            }
            item["theses"] = [
                {"thesis_id": t["thesis_id"], "title": t["title"], "health": t["health"]}
                for t in theses if item["instrument_id"] in t["instrument_ids"]
            ]
    return lists


@router.post("/watchlists", status_code=201)
@_errors
def create_watchlist(body: WatchlistCreate, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    return {"watchlist_id": store.create_watchlist(actor, body.name, body.instrument_ids)}


@router.post("/watchlists/{watchlist_id}/items")
@_errors
def add_item(watchlist_id: str, body: WatchlistItemIn, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    store.add_watchlist_item(actor, watchlist_id, body.instrument_id)
    return {"status": "added"}


@router.delete("/watchlists/{watchlist_id}/items/{instrument_id}")
@_errors
def remove_item(watchlist_id: str, instrument_id: str, store: ResearchStore = Store, actor: Actor = Who) -> dict[str, str]:
    store.remove_watchlist_item(actor, watchlist_id, instrument_id)
    return {"status": "removed"}


@router.post("/watchlists/run-checks")
@_errors
def run_checks(store: ResearchStore = Store, actor: Actor = Who) -> dict[str, Any]:
    """Manual "Run check now": evaluates every thesis in the workspace."""
    _eval_limiter.check(actor.workspace_id)
    theses = store.list_theses(actor)[:MAX_THESES_PER_CHECK]
    results = [evaluate_thesis(store, actor, t["thesis_id"]) for t in theses]
    return {"evaluated": len(results), "results": [
        {"thesis_id": r["thesis_id"], "health": r["health"], "counts": r["counts"], "changed": len(r["changed"])}
        for r in results]}


@router.get("/notifications")
@_errors
def notifications(store: ResearchStore = Store, actor: Actor = Who) -> list[dict[str, Any]]:
    return store.list_notifications(actor)


# ── valuation sandbox ─────────────────────────────────────────────────
@router.post("/valuation/dcf")
@_errors
def valuation(body: DCFInput) -> dict[str, Any]:
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
