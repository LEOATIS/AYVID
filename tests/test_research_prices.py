"""Live price top-up: merges onto the local snapshot and recomputes market metrics (no network)."""
# ruff: noqa: F811  (pytest fixtures imported from test_research_workspace)

from __future__ import annotations

from datetime import date

from backend.services import research_prices as rp
from backend.services.research_store import ResearchStore
from tests.test_research_workspace import NVDA, seeded_db, store  # noqa: F401  (fixtures)


def _fake(rows: dict[str, list[tuple[str, float, float | None]]], calls: list[date]):
    def fetch(symbols: list[str], start: date) -> dict[str, list[tuple[str, float, float | None]]]:
        calls.append(start)
        return {s: rows[s] for s in symbols if s in rows}
    return fetch


def test_refresh_appends_live_bars_and_recomputes_close(store: ResearchStore) -> None:
    inst = store.get_instrument(NVDA)
    calls: list[date] = []
    out = rp.refresh_prices(store, [inst], fetch=_fake({"NVDA": [("2099-01-02", 123.45, 1000.0)]}, calls))

    assert out == {"due": 1, "updated": 1, "failed": 0}
    assert calls[0] < date.fromisoformat(inst["last_bar"])  # backfill overlap
    assert store.get_instrument(NVDA)["last_bar"] == "2099-01-02"
    close = store.latest_observation(NVDA, "CLOSE_PRICE")
    assert close["value"] == "123.45" and close["provider"] == rp.PROVIDER
    assert rp.merged_bars(store, inst)[-1] == {"timestamp": "2099-01-02", "close": 123.45, "volume": 1000.0}


def test_live_bar_replaces_local_close_for_same_day(store: ResearchStore) -> None:
    inst = store.get_instrument(NVDA)
    day = inst["last_bar"]
    rp.refresh_prices(store, [inst], fetch=_fake({"NVDA": [(day, 1.0, None)]}, []))
    assert [b for b in rp.merged_bars(store, inst) if b["timestamp"] == day][0]["close"] == 1.0


def test_ttl_skips_second_call_and_failures_keep_old_data(store: ResearchStore) -> None:
    inst = store.get_instrument(NVDA)
    before = store.latest_observation(NVDA, "CLOSE_PRICE")["value"]
    calls: list[date] = []
    assert rp.refresh_prices(store, [inst], fetch=_fake({}, calls))["failed"] == 1
    assert rp.refresh_prices(store, [inst], fetch=_fake({}, calls))["due"] == 0
    assert len(calls) == 1
    assert store.latest_observation(NVDA, "CLOSE_PRICE")["value"] == before


def test_fetch_exception_never_raises(store: ResearchStore) -> None:
    def boom(symbols: list[str], start: date) -> dict:
        raise RuntimeError("yahoo down")
    assert rp.refresh_prices(store, [store.get_instrument(NVDA)], fetch=boom)["updated"] == 0
