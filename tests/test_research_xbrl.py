"""SEC XBRL companyfacts → typed income / balance / cash-flow observations (offline)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.services.research_seed import seed_research_db
from backend.services.research_store import ResearchStore
from backend.services.research_xbrl import (
    XbrlUnavailable,
    ensure_fundamentals,
    fundamentals_status,
    parse_companyfacts,
)

NVDA = "US:NASDAQ:NVDA"
K25 = "0001045810-25-000023"  # 10-K FY2025 (filed 2025-02-26)
Q26 = "0001045810-25-000209"  # 10-Q Q2 FY2026 (filed 2025-08-27)
K25A = "0001045810-25-000999"  # hypothetical later restatement of FY2025


def _f(val, end, accn, form, fy, fp, filed, start=None):
    fact = {"val": val, "end": end, "accn": accn, "form": form, "fy": fy, "fp": fp, "filed": filed}
    if start:
        fact["start"] = start
    return fact


def payload() -> dict:
    usd = lambda *facts: {"units": {"USD": list(facts)}}  # noqa: E731
    return {
        "cik": 1045810,
        "entityName": "NVIDIA CORP",
        "facts": {
            "us-gaap": {
                "Revenues": usd(
                    _f(130497e6, "2025-01-26", K25, "10-K", 2025, "FY", "2025-02-26", "2024-01-29"),
                    _f(60922e6, "2024-01-28", K25, "10-K", 2025, "FY", "2025-02-26", "2023-01-30"),
                    _f(46743e6, "2025-07-27", Q26, "10-Q", 2026, "Q2", "2025-08-27", "2025-04-28"),
                    _f(30040e6, "2024-07-28", Q26, "10-Q", 2026, "Q2", "2025-08-27", "2024-04-29"),
                    _f(90805e6, "2025-07-27", Q26, "10-Q", 2026, "Q2", "2025-08-27", "2025-01-27"),  # 6M YTD
                ),
                "GrossProfit": usd(_f(97858e6, "2025-01-26", K25, "10-K", 2025, "FY", "2025-02-26", "2024-01-29")),
                "Assets": usd(
                    _f(111601e6, "2025-01-26", K25, "10-K", 2025, "FY", "2025-02-26"),
                    _f(140740e6, "2025-07-27", Q26, "10-Q", 2026, "Q2", "2025-08-27"),
                    _f(111601e6, "2025-01-26", Q26, "10-Q", 2026, "Q2", "2025-08-27"),  # comparative FY-end
                ),
                "NetCashProvidedByUsedInOperatingActivities": usd(
                    _f(64089e6, "2025-01-26", K25, "10-K", 2025, "FY", "2025-02-26", "2024-01-29"),
                    _f(64100e6, "2025-01-26", K25A, "10-K", 2025, "FY", "2025-06-01", "2024-01-29"),  # restated
                    _f(42779e6, "2025-07-27", Q26, "10-Q", 2026, "Q2", "2025-08-27", "2025-01-27"),  # YTD
                ),
                "PaymentsToAcquirePropertyPlantAndEquipment": usd(
                    _f(3236e6, "2025-01-26", K25, "10-K", 2025, "FY", "2025-02-26", "2024-01-29"),
                    _f(3122e6, "2025-07-27", Q26, "10-Q", 2026, "Q2", "2025-08-27", "2025-01-27"),
                ),
                "EarningsPerShareDiluted": {"units": {"USD/shares": [
                    _f(2.94, "2025-01-26", K25, "10-K", 2025, "FY", "2025-02-26", "2024-01-29")]}},
            },
        },
    }


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("xbrl") / "seed.db"
    seed_research_db(ResearchStore(path), force=True)
    return path


@pytest.fixture
def store(seeded: Path, tmp_path: Path) -> ResearchStore:
    path = tmp_path / "r.db"
    src, dst = sqlite3.connect(seeded), sqlite3.connect(path)
    src.backup(dst)
    src.close()
    dst.close()
    return ResearchStore(path)


def labels(code: str) -> dict[str, float]:
    facts, _ = parse_companyfacts(payload())
    return {f["label"]: f["val"] for f in facts if f["metric_code"] == code}


def test_annual_and_comparative_years_labelled_by_fiscal_year() -> None:
    assert labels("REVENUE")["FY2025"] == 130497e6
    assert labels("REVENUE")["FY2024"] == 60922e6


def test_discrete_quarter_and_prior_year_comparative() -> None:
    rev = labels("REVENUE")
    assert rev["Q2-FY2026"] == 46743e6 and rev["Q2-FY2025"] == 30040e6


def test_income_statement_ytd_is_not_mixed_into_quarters() -> None:
    assert not any(k.endswith("YTD") for k in labels("REVENUE"))


def test_cash_flow_ytd_kept_with_explicit_label() -> None:
    assert labels("OPERATING_CASH_FLOW")["Q2-FY2026-YTD"] == 42779e6
    assert "Q2-FY2026" not in labels("OPERATING_CASH_FLOW")


def test_balance_sheet_quarter_ignores_comparative_year_end() -> None:
    assets = labels("TOTAL_ASSETS")
    assert assets == {"FY2025": 111601e6, "Q2-FY2026": 140740e6}


def test_restatement_latest_filed_wins() -> None:
    assert labels("OPERATING_CASH_FLOW")["FY2025"] == 64100e6


def test_ifrs_taxonomy_is_supported() -> None:
    p = {"cik": 1046179, "entityName": "TSMC", "facts": {"ifrs-full": {"Revenue": {"units": {"TWD": [
        _f(2894307.7e6, "2024-12-31", "0001193125-25-083423", "20-F", 2024, "FY", "2025-04-17", "2024-01-01")]}}}}}
    facts, _ = parse_companyfacts(p)
    assert facts[0]["label"] == "FY2024" and facts[0]["unit"] == "TWD"


def test_ingest_writes_statements_with_accessions_and_derives_fcf(store: ResearchStore) -> None:
    inst = store.get_instrument(NVDA)
    out = ensure_fundamentals(store, inst, fetch=lambda cik: payload(), force=True)
    assert out["status"] == "OK" and out["filings"] == 3
    obs = {(o["metric_code"], o["fiscal_period"]): o for o in store.observations(NVDA)}
    rev = obs[("REVENUE", "FY2025")]
    assert rev["provider"] == "SEC_EDGAR" and rev["value"] == "130497" and rev["scale"] == "MILLIONS"
    src = store.get_source(rev["source_id"])
    assert src["accession_no"] == K25 and src["canonical_url"].endswith("000104581025000023/")
    assert obs[("EPS_DILUTED", "FY2025")]["scale"] == "UNITS"
    fcf = obs[("FREE_CASH_FLOW", "FY2025")]
    assert fcf["status"] == "DERIVED" and fcf["value"] == "60864.00"  # 64100 restated − 3236
    assert obs[("FREE_CASH_FLOW", "Q2-FY2026-YTD")]["value"] == "39657.00"
    assert obs[("GROSS_MARGIN_PCT", "FY2025")]["value"] == "74.99"


def test_ingest_supersedes_fixture_observations(store: ResearchStore) -> None:
    assert any(o["provider"] == "FIXTURE_FILING" for o in store.observations(NVDA))
    ensure_fundamentals(store, store.get_instrument(NVDA), fetch=lambda cik: payload(), force=True)
    assert not any(o["provider"] == "FIXTURE_FILING" for o in store.observations(NVDA))


def test_ttl_cache_skips_network(store: ResearchStore) -> None:
    inst = store.get_instrument(NVDA)
    ensure_fundamentals(store, inst, fetch=lambda cik: payload(), force=True)

    def boom(cik: str) -> dict:
        raise AssertionError("should not fetch inside TTL")

    assert ensure_fundamentals(store, inst, fetch=boom)["cached"] is True


def test_failure_is_reported_and_keeps_existing_data(store: ResearchStore) -> None:
    def down(cik: str) -> dict:
        raise XbrlUnavailable("ConnectError: offline")

    before = len(store.observations(NVDA))
    out = ensure_fundamentals(store, store.get_instrument(NVDA), fetch=down, force=True)
    assert out["status"] == "FAILED" and "offline" in out["error"]
    assert len(store.observations(NVDA)) == before
    assert fundamentals_status(store, NVDA)["status"] == "NOT_FETCHED"


def test_non_sec_listing_is_unsupported_not_failed(store: ResearchStore) -> None:
    out = ensure_fundamentals(store, store.get_instrument("IN:NSE:INFY"), fetch=lambda c: payload())
    assert out["status"] == "UNSUPPORTED"
