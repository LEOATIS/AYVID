"""Microsoft cloud-outage scenario: resolution, propagation and a clean (bannerless) LLM fallback."""
# ruff: noqa: F811  (pytest fixtures imported from test_research_workspace)

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.services.research_resolver import resolve_query
from backend.services.research_store import ResearchStore
from tests.test_research_workspace import ask, client, seeded_db, store  # noqa: F401  (fixtures)

OUTAGE = ("How does the Microsoft Azure and M365 cascading outage propagate across cloud, observability, "
          "and cybersecurity stocks (MSFT, GOOGL, AMZN, DDOG, CRWD, NET)?")


def test_word_ticker_inside_ticker_list_resolves(store: ResearchStore) -> None:
    res = resolve_query(store, OUTAGE)
    assert "US:NYSE:NET" in {i["instrument_id"] for i in res.resolved}
    assert not res.ambiguous
    # Outside a ticker list the common-word guard still applies.
    assert not any(i["symbol"] == "NET" for i in resolve_query(store, "What is NET income for MSFT?").resolved)


def test_outage_propagates_to_substitutes_without_degraded_banner(client: TestClient) -> None:
    art = ask(client, OUTAGE, simulate_provider_failure="llm")
    assert art["intent"] == "EVENT_IMPACT"
    first_order = {e["to"] for e in art["exposures"] if e["from"] == "MSFT" and e["order"] == 1}
    assert {"GOOGL", "AMZN", "DDOG", "CRWD", "NET", "PANW"} <= first_order
    edge = next(e for e in art["exposures"] if e["to"] == "GOOGL")
    assert edge["evidence_status"] == "INFERRED" and "Falsifier" in edge["uncertainty_note"]
    assert not any(b["kind"] == "DEGRADED" for b in art["banners"])
    assert art["telemetry"]["llm_used"] is False
    assert art["audit"]["dropped"] == 0
