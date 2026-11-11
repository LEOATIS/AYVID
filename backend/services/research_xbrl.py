"""Live structured fundamentals from SEC EDGAR XBRL ``companyfacts``.

One request per company (``data.sec.gov/api/xbrl/companyfacts/CIK##########.json``)
returns every fact the company has tagged in its 10-K / 10-Q / 20-F filings, each
with its accession number, form, fiscal period and filing date. This module turns
those facts into typed ``financial_observations`` for the income statement,
balance sheet and cash-flow statement, with one ``source_documents`` row per
filing (real accession number + EDGAR folder URL).

Rules that keep the numbers honest:
  • Annual flows: 10-K/20-F facts spanning 330–400 days, labelled ``FY{end year}``.
  • Quarterly flows: 10-Q facts spanning 80–100 days (discrete quarter). A 10-Q's
    prior-year comparative is labelled with the prior fiscal year.
  • Quarterly cash flows are reported year-to-date; they are kept as YTD and
    labelled ``Qn-FYyyyy-YTD`` — never mixed with discrete-quarter figures.
  • Instants (balance sheet): annual = fiscal-year-end facts from annual filings;
    quarterly = only the filing's own period end (not its comparative year-end).
  • Restatements: for the same period the most recently *filed* fact wins.
On success the instrument's curated FIXTURE_FILING observations are replaced.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from backend.services.research_fixtures import METRIC_LABELS
from backend.services.research_store import ResearchStore, utcnow
from shared.config import settings
from shared.logger import get_logger

log = get_logger("research.xbrl")

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F"}
QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
MILLION = Decimal(1_000_000)
KEEP_ANNUAL = 5
KEEP_QUARTERS = 8
HTTP_TIMEOUT_SEC = 20.0


@dataclass(frozen=True)
class Concept:
    metric_code: str
    statement: str  # INCOME | BALANCE | CASH_FLOW
    kind: str  # flow | instant | per_share
    tags: tuple[tuple[str, str], ...]  # (taxonomy, concept) in priority order


CONCEPTS: tuple[Concept, ...] = (
    Concept("REVENUE", "INCOME", "flow", (
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
    )),
    Concept("GROSS_PROFIT", "INCOME", "flow", (("us-gaap", "GrossProfit"), ("ifrs-full", "GrossProfit"))),
    Concept("OPERATING_INCOME", "INCOME", "flow", (
        ("us-gaap", "OperatingIncomeLoss"), ("ifrs-full", "ProfitLossFromOperatingActivities"))),
    Concept("NET_INCOME", "INCOME", "flow", (
        ("us-gaap", "NetIncomeLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
        ("ifrs-full", "ProfitLoss"),
    )),
    Concept("EPS_DILUTED", "INCOME", "per_share", (
        ("us-gaap", "EarningsPerShareDiluted"), ("ifrs-full", "DilutedEarningsLossPerShare"))),
    Concept("TOTAL_ASSETS", "BALANCE", "instant", (("us-gaap", "Assets"), ("ifrs-full", "Assets"))),
    Concept("TOTAL_LIABILITIES", "BALANCE", "instant", (("us-gaap", "Liabilities"), ("ifrs-full", "Liabilities"))),
    Concept("STOCKHOLDERS_EQUITY", "BALANCE", "instant", (
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("ifrs-full", "EquityAttributableToOwnersOfParent"),
        ("ifrs-full", "Equity"),
    )),
    Concept("CASH", "BALANCE", "instant", (
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"), ("ifrs-full", "CashAndCashEquivalents"))),
    Concept("LONG_TERM_DEBT", "BALANCE", "instant", (
        ("us-gaap", "LongTermDebtNoncurrent"), ("us-gaap", "LongTermDebt"))),
    Concept("OPERATING_CASH_FLOW", "CASH_FLOW", "flow", (
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    )),
    Concept("CAPEX", "CASH_FLOW", "flow", (
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    )),
)
STATEMENT_OF = {c.metric_code: c.statement for c in CONCEPTS} | {"FREE_CASH_FLOW": "CASH_FLOW"}


class XbrlUnavailable(RuntimeError):
    """SEC EDGAR could not be reached or returned no usable facts."""


def _days(start: str | None, end: str) -> int | None:
    if not start:
        return None
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _unit_and_facts(node: dict[str, Any], per_share: bool) -> tuple[str, list[dict[str, Any]]] | None:
    units = node.get("units") or {}
    for unit, facts in units.items():
        if per_share == unit.endswith("/shares"):
            return unit, facts
    return None


def _fiscal_year(fact: dict[str, Any], current_end: str | None) -> int:
    """The fact's own fiscal year. ``fy`` on a fact is the *filing's* fiscal year, so a
    comparative period one year earlier in the same filing gets ``fy - 1`` (etc.)."""
    end = date.fromisoformat(fact["end"])
    if not fact.get("fy"):
        return end.year
    if not current_end or fact["end"] == current_end:
        return int(fact["fy"])
    years_back = round((date.fromisoformat(current_end) - end).days / 365.25)
    return int(fact["fy"]) - years_back


def _label(fact: dict[str, Any], kind: str, current_end: str | None, *, ytd_ok: bool = False) -> str | None:
    """Fiscal label for one fact, or None when it is not a period we keep."""
    form, fp = fact.get("form", ""), fact.get("fp", "")
    span = _days(fact.get("start"), fact["end"])
    if form in ANNUAL_FORMS and fp == "FY":
        if kind != "instant" and not (span and 330 <= span <= 400):
            return None
        return f"FY{_fiscal_year(fact, current_end)}"
    if form in QUARTERLY_FORMS and fp in {"Q1", "Q2", "Q3"} and fact.get("fy"):
        is_current = fact["end"] == current_end
        if kind == "instant":
            return f"{fp}-FY{fact['fy']}" if is_current else None
        if span and 80 <= span <= 100:
            return f"{fp}-FY{_fiscal_year(fact, current_end)}"
        if ytd_ok and span and span > 100 and is_current:  # 10-Q cash flows are YTD only
            return f"{fp}-FY{fact['fy']}-YTD"
    return None


def parse_companyfacts(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """→ (facts with metric_code/label/value/unit/accn…, filings keyed by accession)."""
    facts_root = payload.get("facts") or {}
    chosen: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for concept in CONCEPTS:
        for priority, (taxonomy, tag) in enumerate(concept.tags):
            node = (facts_root.get(taxonomy) or {}).get(tag)
            if not node:
                continue
            unit_facts = _unit_and_facts(node, concept.kind == "per_share")
            if unit_facts is None:
                continue
            unit, facts = unit_facts
            current_end_by_accn: dict[str, str] = {}
            for f in facts:
                current_end_by_accn[f["accn"]] = max(current_end_by_accn.get(f["accn"], ""), f["end"])
            for f in facts:
                if f.get("val") is None:
                    continue
                label = _label(f, concept.kind, current_end_by_accn.get(f["accn"]),
                               ytd_ok=concept.statement == "CASH_FLOW")
                if label is None:
                    continue
                key = (concept.metric_code, label)
                record = {**f, "metric_code": concept.metric_code, "label": label, "unit": unit,
                          "tag": f"{taxonomy}:{tag}", "kind": concept.kind}
                prior = chosen.get(key)
                # Lower priority index wins; within a concept the latest-filed fact wins (restatements).
                if prior is None or priority < prior[0] or (
                    priority == prior[0] and (f.get("filed", ""), f["end"]) > (prior[1].get("filed", ""), prior[1]["end"])
                ):
                    chosen[key] = (priority, record)

    records = [r for _, r in chosen.values()]
    trimmed: list[dict[str, Any]] = []
    for code in {r["metric_code"] for r in records}:
        mine = sorted((r for r in records if r["metric_code"] == code), key=lambda r: r["end"], reverse=True)
        annual = [r for r in mine if r["label"].startswith("FY")][:KEEP_ANNUAL]
        quarterly = [r for r in mine if r["label"].startswith("Q")][:KEEP_QUARTERS]
        trimmed.extend(annual + quarterly)

    filings: dict[str, dict[str, Any]] = {}
    for r in trimmed:
        f = filings.setdefault(r["accn"], {"accn": r["accn"], "form": r.get("form"), "filed": r.get("filed"),
                                           "period_end": r["end"], "facts": []})
        f["period_end"] = max(f["period_end"], r["end"])
        f["facts"].append(r)
    return trimmed, filings


def fetch_companyfacts(cik: str) -> dict[str, Any]:
    url = COMPANYFACTS_URL.format(cik=str(cik).zfill(10))
    headers = {"User-Agent": f"AYVID research {settings.SEC_EDGAR_EMAIL}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SEC, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise XbrlUnavailable(f"{type(exc).__name__}: {exc}") from exc


def _money(value: Any) -> str:
    return format((Decimal(str(value)) / MILLION).normalize(), "f")


def _summary(f: dict[str, Any]) -> str:
    parts = []
    for r in sorted(f["facts"], key=lambda r: (r["end"] != f["period_end"], r["metric_code"]))[:5]:
        ccy = r["unit"].split("/")[0]
        shown = f"{Decimal(str(r['val'])):,.2f} {ccy}/share" if r["kind"] == "per_share" else f"{_money(r['val'])}m {ccy}"
        parts.append(f"{r['tag']} = {shown} ({r['label']})")
    return "Structured XBRL facts reported in this filing: " + "; ".join(parts) + "."


def ingest_instrument(store: ResearchStore, inst: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Write parsed facts for one instrument. Returns a short ingest summary."""
    from backend.services.research_seed import (
        seed_derived,  # local: seed imports store, avoid cycle
    )

    facts, filings = parse_companyfacts(payload)
    if not facts:
        raise XbrlUnavailable("companyfacts contained no supported us-gaap / ifrs-full concepts")
    iid, local = inst["instrument_id"], inst["local_symbol"] or inst["symbol"]
    cik_int = str(int(payload.get("cik") or inst["cik"]))
    name = payload.get("entityName") or inst["name"]
    now = utcnow()
    sources = [{
        "source_id": f"SRC-XBRL-{f['accn']}", "provider": "SEC_EDGAR",
        "title": f"{name} {f['form']} — XBRL financial data (period ended {f['period_end']})",
        "publisher": "SEC EDGAR (XBRL companyfacts)",
        "canonical_url": ARCHIVE_URL.format(cik=cik_int, accn=f["accn"].replace("-", "")),
        "published_at": f["filed"], "retrieved_at": now, "doc_type": f["form"], "instrument_id": iid,
        "accession_no": f["accn"],
        "locator": "XBRL: " + ", ".join(sorted({r["tag"] for r in f["facts"]}))[:500],
        "excerpt": _summary(f),
        "content_hash": hashlib.sha256(json.dumps(f["facts"], sort_keys=True, default=str).encode()).hexdigest()[:16],
        "access_status": "AVAILABLE", "is_fixture": 0,
    } for f in filings.values()]
    observations = []
    for r in facts:
        per_share = r["kind"] == "per_share"
        currency = r["unit"].split("/")[0]
        basis = "IFRS" if r["tag"].startswith("ifrs") else "GAAP"
        observations.append({
            "observation_id": f"OBS-{local}-{r['metric_code']}-{r['label']}", "instrument_id": iid,
            "metric_code": r["metric_code"], "metric_label": METRIC_LABELS[r["metric_code"]],
            "value": format(Decimal(str(r["val"])), "f") if per_share else _money(r["val"]),
            "currency": currency, "scale": "UNITS" if per_share else "MILLIONS",
            "fiscal_period": r["label"], "period_start": r.get("start"), "period_end": r["end"],
            "published_at": r.get("filed"), "as_of": r.get("filed") or r["end"], "provider": "SEC_EDGAR",
            "source_id": f"SRC-XBRL-{r['accn']}", "basis": basis, "status": "REPORTED", "formula": None,
            "inputs": json.dumps({"xbrl_tag": r["tag"], "accession": r["accn"], "form": r.get("form"),
                                  "fy": r.get("fy"), "fp": r.get("fp"), "raw_value": r["val"], "unit": r["unit"]}),
        })
    with store.connect() as conn:
        # XBRL supersedes the curated stand-ins and any ratios derived from them.
        conn.execute("DELETE FROM financial_observations WHERE instrument_id = ? AND provider = 'FIXTURE_FILING'", (iid,))
        conn.execute(
            "DELETE FROM financial_observations WHERE instrument_id = ? AND status = 'DERIVED' AND provider NOT IN ('LOCAL_OHLCV', 'YAHOO_FINANCE_DAILY')",
            (iid,),
        )
    store.insert_many("source_documents", sources)
    store.insert_many("financial_observations", observations)
    seed_derived(store, [iid])
    latest = max(f["filed"] or "" for f in filings.values())
    summary = {"status": "OK", "source": "SEC_EDGAR_XBRL", "fetched_at": now, "filings": len(filings),
               "observations": len(observations), "latest_filing": latest}
    store.set_seed_value(f"xbrl:{iid}", json.dumps(summary))
    return summary


def _resolve_cik(inst: dict[str, Any]) -> str | None:
    if inst.get("cik"):
        return inst["cik"]
    if inst.get("country") != "US" or inst.get("data_tier") == "REFERENCE_ONLY":
        return None
    from data_primitives.extractors.sec_edgar import _load_cik_cache

    return _load_cik_cache().get(inst["symbol"].upper())


def fundamentals_status(store: ResearchStore, iid: str) -> dict[str, Any]:
    raw = store.seed_value(f"xbrl:{iid}")
    if raw:
        return json.loads(raw)
    return {"status": "NOT_FETCHED", "source": "FIXTURE_OR_NONE"}


def ensure_fundamentals(
    store: ResearchStore,
    inst: dict[str, Any],
    *,
    fetch: Callable[[str], dict[str, Any]] = fetch_companyfacts,
    force: bool = False,
) -> dict[str, Any]:
    """Refresh XBRL fundamentals if older than RESEARCH_XBRL_TTL_HOURS. Never raises:
    failures are returned (and recorded) so callers can show a degraded state."""
    iid = inst["instrument_id"]
    cik = _resolve_cik(inst)
    if not cik:
        return {"status": "UNSUPPORTED", "source": "FIXTURE_OR_NONE",
                "reason": "No SEC CIK — non-US listing or not an SEC filer."}
    status = fundamentals_status(store, iid)
    if not force and status.get("status") == "OK":
        fetched = datetime.fromisoformat(status["fetched_at"])
        if datetime.now(UTC) - fetched < timedelta(hours=settings.RESEARCH_XBRL_TTL_HOURS):
            return {**status, "cached": True}
    try:
        summary = ingest_instrument(store, {**inst, "cik": cik}, fetch(cik))
        if not inst.get("cik"):
            with store.connect() as conn:
                conn.execute("UPDATE instruments SET cik = ? WHERE instrument_id = ?", (cik, iid))
        return summary
    except XbrlUnavailable as exc:
        log.warning("xbrl ingest failed for %s: %s", iid, exc)
        failed = {"status": "FAILED", "source": "SEC_EDGAR_XBRL", "error": str(exc)[:300],
                  "attempted_at": utcnow(), "previous": status if status.get("status") == "OK" else None}
        return failed

