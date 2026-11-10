"""Idempotent seeding of the research database from local repo data.

Sources (all on disk — no network):
  • ``data_store/ohlcv/*.jsonl``          → instruments + PRICE_SERIES docs + market metrics
  • ``research_fixtures.FUNDAMENTALS``    → curated filing summaries + reported figures
  • ``seed_supply_chain.GLOBAL_SUPPLY_CHAIN`` + ``calibration/edge_proposals.jsonl`` → edges
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from backend.services import research_fixtures as fx
from backend.services.research_calc import (
    CalculationError,
    difference,
    growth_pct,
    market_stats,
    ratio_pct,
)
from backend.services.research_store import Actor, ResearchStore, utcnow
from shared.logger import get_logger

log = get_logger("research.seed")

SEED_VERSION = "2026-11-10.4"
REPO_ROOT = Path(__file__).resolve().parents[2]
OHLCV_DIR = REPO_ROOT / "data_store" / "ohlcv"
EDGE_PROPOSALS = REPO_ROOT / "calibration" / "edge_proposals.jsonl"
OTHER_WORKSPACE = Actor(workspace_id="ws_partner_fund", user_id="user_partner")
PRIVATE_DEMO_THESIS_ID = "th_private_partner"


def _sha16(data: bytes | str) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()[:16]


def canonical_listing(local_symbol: str) -> tuple[str, str, str, str, str]:
    """local symbol (e.g. ``INFY.NS``) → (instrument_id, symbol, exchange, currency, country)."""
    for suffix, (country, exchange, currency) in fx.SUFFIX_EXCHANGE.items():
        if local_symbol.endswith(suffix):
            base = local_symbol[: -len(suffix)]
            return f"{country}:{exchange}:{base}", base, exchange, currency, country
    if local_symbol in fx.ETFS:
        exchange = "NASDAQ" if local_symbol in fx.NASDAQ else "ARCA"
    elif local_symbol in fx.OTC:
        exchange = "OTC"
    elif local_symbol in fx.NASDAQ:
        exchange = "NASDAQ"
    else:
        exchange = "NYSE"
    return f"US:{exchange}:{local_symbol}", local_symbol, exchange, "USD", "US"


def read_bars(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One bar per trading day, date-sorted, with a finite close.

    The local JSONL files are append-only and contain out-of-order and
    duplicated days (vendor re-pulls). For a duplicated day the LAST written
    row wins (most recent revision). Returns the clean bars plus counts of
    what was dropped so provenance can disclose it.
    """
    by_day: dict[str, dict[str, Any]] = {}
    stats = {"non_finite": 0, "duplicates": 0, "conflicting_duplicates": 0}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        bar = json.loads(line)
        close = bar.get("close")
        if not (isinstance(close, (int, float)) and math.isfinite(close)):
            stats["non_finite"] += 1
            continue
        day = bar["timestamp"][:10]
        prior = by_day.get(day)
        if prior is not None:
            stats["duplicates"] += 1
            if abs(prior["close"] - close) > 1e-6 * max(1.0, abs(close)):
                stats["conflicting_duplicates"] += 1
        by_day[day] = bar
    return [by_day[d] for d in sorted(by_day)], stats


def _cleaning_note(stats: dict[str, int]) -> str:
    parts = []
    if stats["duplicates"]:
        parts.append(f"{stats['duplicates']} duplicate day rows collapsed "
                     f"({stats['conflicting_duplicates']} with differing closes; latest write kept)")
    if stats["non_finite"]:
        parts.append(f"{stats['non_finite']} rows with a missing close excluded")
    return (" Cleaning: " + "; ".join(parts) + ".") if parts else ""


def _market_rows(iid: str, local: str, bars: list[dict[str, Any]], currency: str, source_id: str) -> list[dict[str, Any]]:
    stats = market_stats([b["close"] for b in bars])
    last = bars[-1]["timestamp"][:10]
    first = bars[0]["timestamp"][:10]
    spec = [
        ("CLOSE_PRICE", stats["close"], "UNITS", "REPORTED_MARKET", "REPORTED", None),
        ("RETURN_90D_PCT", stats["ret_90d"], "PERCENT", "DETERMINISTIC_CALC", "DERIVED",
         "(close[t] ÷ close[t−90] − 1) × 100 over daily bars"),
        ("REALIZED_VOL_ANN", stats["vol_90d"], "PERCENT", "DETERMINISTIC_CALC", "DERIVED",
         "stdev(ln returns, last 90 bars, ddof=1) × √252 × 100"),
        ("MAX_DRAWDOWN_PCT", stats["max_dd"], "PERCENT", "DETERMINISTIC_CALC", "DERIVED",
         "min((close − running peak) ÷ running peak) × 100 over full series"),
        ("HURST_EXPONENT", stats["hurst"], "RATIO", "DETERMINISTIC_CALC", "DERIVED",
         "Rescaled-range (R/S) Hurst on daily log returns, max lag 20 (shared.quant.compute_hurst)"),
    ]
    rows = []
    for code, value, scale, basis, status, formula in spec:
        if value is None:
            continue
        rows.append({
            "observation_id": f"OBS-{local}-{code}",
            "instrument_id": iid,
            "metric_code": code,
            "metric_label": fx.METRIC_LABELS[code],
            "value": str(round(value, 6)),
            "currency": currency if code == "CLOSE_PRICE" else None,
            "scale": scale,
            "fiscal_period": f"{last} (90D)" if code != "MAX_DRAWDOWN_PCT" else f"{first}→{last}",
            "period_start": first,
            "period_end": last,
            "published_at": last,
            "as_of": last,
            "provider": "LOCAL_OHLCV",
            "source_id": source_id,
            "basis": basis,
            "status": status,
            "formula": formula,
            "inputs": json.dumps({"bars": len(bars), "first_bar": first, "last_bar": last}),
        })
    return rows


def _seed_market(store: ResearchStore) -> dict[str, str]:
    """Returns local_symbol → instrument_id."""
    instruments, sources, observations = [], [], []
    mapping: dict[str, str] = {}
    now = utcnow()
    for path in sorted(OHLCV_DIR.glob("*.jsonl")):
        local = path.stem
        try:
            bars, clean_stats = read_bars(path)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("skipping unreadable OHLCV file %s: %s", path.name, exc)
            continue
        if not bars:
            continue
        iid, symbol, exchange, currency, country = canonical_listing(local)
        name, sector, industry, cik = fx.COMPANY_META.get(local, (local, None, None, None))
        if local in fx.ETFS:
            sector, industry = sector or "ETF / Index", industry or "Exchange-traded fund"
        tier = "LIVE_EDGAR_AND_LOCAL_OHLCV" if (country == "US" and cik) else "LOCAL_OHLCV_ONLY"
        first, last = bars[0]["timestamp"][:10], bars[-1]["timestamp"][:10]
        mapping[local] = iid
        instruments.append({
            "instrument_id": iid, "symbol": symbol, "local_symbol": local, "name": name,
            "exchange": exchange, "currency": currency, "country": country, "sector": sector,
            "industry": industry, "cik": cik, "data_tier": tier, "first_bar": first, "last_bar": last,
        })
        source_id = f"SRC-OHLCV-{local}"
        sources.append({
            "source_id": source_id, "provider": "LOCAL_OHLCV",
            "title": f"{local} daily OHLCV bars ({first} → {last})", "publisher": "AYVID local data store",
            "canonical_url": f"data_store/ohlcv/{path.name}", "published_at": last, "retrieved_at": now,
            "doc_type": "PRICE_SERIES", "instrument_id": iid, "accession_no": None,
            "locator": f"{len(bars)} daily bars", "excerpt": (
                f"{len(bars)} daily bars from {first} to {last}. Last close "
                f"{bars[-1]['close']:.2f} {currency} on {last}."
                + _cleaning_note(clean_stats)
            ),
            "content_hash": _sha16(path.read_bytes()), "access_status": "AVAILABLE", "is_fixture": 0,
        })
        observations.extend(_market_rows(iid, local, bars, currency, source_id))
    for ref in fx.REFERENCE_LISTINGS:
        instruments.append({**ref, "local_symbol": None, "cik": None, "data_tier": "REFERENCE_ONLY",
                            "first_bar": None, "last_bar": None})
    store.insert_many("instruments", instruments)
    store.insert_many("source_documents", sources)
    store.insert_many("financial_observations", observations)
    return mapping


def _seed_fundamentals(store: ResearchStore, mapping: dict[str, str]) -> None:
    now = utcnow()
    sources, obs = [], []
    for key, doc in fx.FILINGS.items():
        local = doc["symbol"]
        iid = mapping.get(local)
        cik = fx.COMPANY_META.get(local, (None, None, None, None))[3]
        url = fx.EDGAR_INDEX.format(cik=cik, form=doc["doc_type"]) if cik else None
        sources.append({
            "source_id": f"SRC-{key}", "provider": "FIXTURE_FILING", "title": doc["title"],
            "publisher": fx.COMPANY_META.get(local, (local,))[0], "canonical_url": url,
            "published_at": doc["published_at"], "retrieved_at": now, "doc_type": doc["doc_type"],
            "instrument_id": iid, "accession_no": None, "locator": doc["locator"],
            "excerpt": doc["excerpt"], "content_hash": _sha16(doc["excerpt"]),
            "access_status": "AVAILABLE", "is_fixture": 1,
        })
    for key, period, period_end, code, value, ccy, scale, basis in fx.FUNDAMENTALS:
        local = fx.FILINGS[key]["symbol"]
        iid = mapping.get(local)
        if iid is None:
            continue
        obs.append({
            "observation_id": f"OBS-{local}-{code}-{period}", "instrument_id": iid,
            "metric_code": code, "metric_label": fx.METRIC_LABELS[code], "value": str(value),
            "currency": ccy, "scale": scale, "fiscal_period": period, "period_start": None,
            "period_end": period_end, "published_at": fx.FILINGS[key]["published_at"],
            "as_of": fx.FILINGS[key]["published_at"], "provider": "FIXTURE_FILING",
            "source_id": f"SRC-{key}", "basis": basis, "status": "REPORTED",
            "formula": None, "inputs": None,
        })
    store.insert_many("source_documents", sources)
    store.insert_many("financial_observations", obs)
    seed_derived(store, sorted({o["instrument_id"] for o in obs}))


def seed_derived(store: ResearchStore, instrument_ids: list[str]) -> None:
    """Deterministic ratios / differences from same-period reported figures."""
    derived = []
    for iid in instrument_ids:
        by_key = {(o["metric_code"], o["fiscal_period"]): o for o in store.observations(iid)}
        periods = sorted({p for (_, p) in by_key})
        for period in periods:
            ocf, capex = by_key.get(("OPERATING_CASH_FLOW", period)), by_key.get(("CAPEX", period))
            fcf = None
            if ocf and capex and ("FREE_CASH_FLOW", period) not in by_key:
                try:
                    calc = difference(f"FCF-{period}", fx.METRIC_LABELS["FREE_CASH_FLOW"], ocf, capex)
                    fcf = _derived_row(iid, "FREE_CASH_FLOW", period, ocf, calc, scale=ocf["scale"])
                    derived.append(fcf)
                except CalculationError:
                    pass
            rev = by_key.get(("REVENUE", period))
            if rev is None:
                continue
            for num_code, out_code in (
                ("GROSS_PROFIT", "GROSS_MARGIN_PCT"),
                ("OPERATING_INCOME", "OPERATING_MARGIN_PCT"),
                ("NET_INCOME", "NET_MARGIN_PCT"),
                ("FREE_CASH_FLOW", "FCF_MARGIN_PCT"),
            ):
                num = by_key.get((num_code, period)) or (fcf if num_code == "FREE_CASH_FLOW" else None)
                if num is None or (out_code, period) in by_key:
                    continue
                try:
                    calc = ratio_pct(f"{out_code}-{period}", fx.METRIC_LABELS[out_code], num, rev)
                except CalculationError:
                    continue
                derived.append(_derived_row(iid, out_code, period, rev, calc))
        fy = [p for p in periods if p.startswith("FY") and ("REVENUE", p) in by_key]
        for prior, current in zip(fy, fy[1:]):
            try:
                calc = growth_pct("g", fx.METRIC_LABELS["REVENUE_GROWTH_PCT"],
                                  by_key[("REVENUE", current)], by_key[("REVENUE", prior)])
            except CalculationError:
                continue
            derived.append(_derived_row(iid, "REVENUE_GROWTH_PCT", current, by_key[("REVENUE", current)], calc))
    store.insert_many("financial_observations", derived)


def _derived_row(
    iid: str, code: str, period: str, anchor: dict[str, Any], calc: Any, *, scale: str = "PERCENT"
) -> dict[str, Any]:
    return {
        "observation_id": f"OBS-{iid}-{code}-{period}", "instrument_id": iid, "metric_code": code,
        "metric_label": fx.METRIC_LABELS[code], "value": str(calc.result), "currency": anchor["currency"],
        "scale": scale, "fiscal_period": period, "period_start": anchor.get("period_start"),
        "period_end": anchor["period_end"], "published_at": anchor["published_at"],
        "as_of": anchor["as_of"], "provider": anchor["provider"], "source_id": anchor["source_id"],
        "basis": "DETERMINISTIC_CALC", "status": "DERIVED", "formula": calc.formula,
        "inputs": json.dumps(list(calc.inputs)),
    }


def _seed_edges(store: ResearchStore, mapping: dict[str, str]) -> None:
    from graph_intelligence.tools.seed_supply_chain import GLOBAL_SUPPLY_CHAIN

    now = utcnow()
    graph_source = {
        "source_id": "SRC-GRAPH-SEED", "provider": "SUPPLY_CHAIN_GRAPH",
        "title": "AYVID curated global supply-chain seed",
        "publisher": "AYVID (graph_intelligence/tools/seed_supply_chain.py)",
        "canonical_url": "graph_intelligence/tools/seed_supply_chain.py", "published_at": "2026-04-01",
        "retrieved_at": now, "doc_type": "GRAPH_EDGE_AUDIT", "instrument_id": None, "accession_no": None,
        "locator": "GLOBAL_SUPPLY_CHAIN", "excerpt": (
            "Hand-curated (source, target, revenue dependency, criticality) tuples. Not extracted "
            "from a specific filing; treat as INFERRED until confirmed by a primary document."
        ),
        "content_hash": _sha16(json.dumps(GLOBAL_SUPPLY_CHAIN)), "access_status": "AVAILABLE",
        "is_fixture": 1,
    }
    edges: dict[str, dict[str, Any]] = {}
    for src, dst, dep, crit in GLOBAL_SUPPLY_CHAIN:
        if dep <= 0:
            continue
        rel = "SUPPLIER" if src in fx.UPSTREAM_SUPPLIERS else "CUSTOMER"
        edges[f"{src}>{dst}"] = {
            "edge_id": f"EDGE-{src}-{dst}", "src_instrument_id": mapping.get(src),
            "dst_instrument_id": mapping.get(dst), "src_symbol": src, "dst_symbol": dst,
            "relationship_type": rel, "revenue_dependency_pct": round(dep * 100, 2),
            "criticality_score": crit, "source_id": "SRC-GRAPH-SEED", "evidence_status": "INFERRED",
            "uncertainty_note": (
                "Curated seed edge. Dependency % is an analyst estimate of the downstream party's "
                "exposure, not a disclosed figure."
            ),
        }
    sources = [graph_source]
    if EDGE_PROPOSALS.exists():
        for i, line in enumerate(EDGE_PROPOSALS.read_text().splitlines()):
            if not line.strip():
                continue
            try:
                prop = json.loads(line)
            except json.JSONDecodeError:
                continue
            if prop.get("outcome") != "accepted":
                continue
            sid = f"SRC-EDGE-PROPOSAL-{i}"
            sources.append({
                "source_id": sid, "provider": "SEC_EDGAR",
                "title": f"{prop['extracted_from']} {prop.get('form_type', '10-K')} — supplier mention: "
                         f"{prop.get('raw_entity', prop['src'])}",
                "publisher": "SEC EDGAR (LLM edge proposer)", "canonical_url": prop.get("filing_url"),
                "published_at": prop["ts"][:10], "retrieved_at": prop["ts"],
                "doc_type": prop.get("form_type", "10-K"), "instrument_id": mapping.get(prop["dst"]),
                "accession_no": None, "locator": "Supplier / manufacturing risk-factor discussion",
                "excerpt": (
                    f"Edge proposer extracted '{prop.get('raw_entity')}' as a {prop.get('relationship')} "
                    f"of {prop['dst']} (criticality {prop.get('criticality')}, confidence "
                    f"{prop.get('confidence_after')})."
                ),
                "content_hash": _sha16(line), "access_status": "AVAILABLE", "is_fixture": 0,
            })
            key = f"{prop['src']}>{prop['dst']}"
            base = edges.get(key, {
                "edge_id": f"EDGE-{prop['src']}-{prop['dst']}", "src_instrument_id": mapping.get(prop["src"]),
                "dst_instrument_id": mapping.get(prop["dst"]), "src_symbol": prop["src"],
                "dst_symbol": prop["dst"], "relationship_type": "SUPPLIER",
                "revenue_dependency_pct": None, "criticality_score": None,
            })
            edges[key] = {
                **base, "source_id": sid, "evidence_status": "EXTRACTED",
                "uncertainty_note": (
                    f"Extracted from {prop['extracted_from']} filing by LLM edge proposer "
                    f"(confidence {prop.get('confidence_after')}); dependency % still estimated."
                ),
            }
    store.insert_many("source_documents", sources)
    with store.connect() as conn:
        conn.execute("DELETE FROM supply_chain_edges")
    store.insert_many("supply_chain_edges", list(edges.values()))


def _seed_demo_records(store: ResearchStore) -> None:
    """One private thesis owned by another workspace (permission-boundary demo)."""
    if store.one("SELECT 1 FROM theses WHERE thesis_id = ?", (PRIVATE_DEMO_THESIS_ID,)):
        return
    store.insert("workspaces", {"workspace_id": OTHER_WORKSPACE.workspace_id,
                                "name": "Partner fund (private)", "created_at": utcnow()}, replace=True)
    store.insert("users", {"user_id": OTHER_WORKSPACE.user_id, "workspace_id": OTHER_WORKSPACE.workspace_id,
                           "display_name": "Partner analyst"}, replace=True)
    store.create_thesis(OTHER_WORKSPACE, {
        "thesis_id": PRIVATE_DEMO_THESIS_ID,
        "title": "Partner fund — private semiconductor thesis",
        "instrument_ids": ["US:NASDAQ:AMD"],
        "summary": "Private to ws_partner_fund. Any other workspace must receive 403.",
    })


def seed_research_db(store: ResearchStore, *, force: bool = False) -> bool:
    """Seed if the stored seed version differs. Returns True when seeding ran."""
    if not force and store.seed_value("version") == SEED_VERSION:
        return False
    mapping = _seed_market(store)
    _seed_fundamentals(store, mapping)
    _seed_edges(store, mapping)
    _seed_demo_records(store)
    store.set_seed_value("version", SEED_VERSION)
    log.info("research db seeded: %d instruments", len(mapping))
    return True
