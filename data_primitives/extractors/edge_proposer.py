"""LLM-driven supply-chain edge proposer.

Closes the "graph is hand-seeded" objection. Pipeline:

    10-K / 10-Q filing
      └─ full_edgar_pipeline(ticker)                 # sec_edgar.py
         └─ GPT-4o / Claude extracts:
                - supply_chain_dependencies[]
                - key_suppliers[]
                - key_customers[]
            │
            ▼
      EntityResolver: name → canonical ticker
            │
            ▼
      EdgeProposalCandidate (Pydantic, per-edge confidence)
            │
            ▼
      upsert_proposed_edge(...) into Neo4j with:
            r.confidence           = 0.2   (low — unvalidated)
            r.confidence_method    = "llm_10k_proposal"
            r.source_filing        = URL
            r.proposed_at          = <now>
            r.proposed_by          = <ticker_of_filing>
            │
            ▼
      calibration/edge_proposals.jsonl  (audit trail)
            │
            ▼
      `ayvid graph refine` next run will CI-test these candidates
      against observational returns. Survivors get confidence bumped
      to >= γ; losers get pruned or stay at their floor and NEVER get
      traversed by find_hidden_victims(min_edge_confidence=0.6).

The narrative: **GPT-4o proposes, CausalFusion disposes**. Neo4j is now
a grow-or-shrink artefact of that loop — nothing is hand-seeded beyond
the one-off bootstrap set in ``cli/commands/graph.py::_GLOBAL_SUPPLY_CHAIN``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shared.logger import get_logger

log = get_logger("data.edge_proposer")


# ── Module paths ──────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_LOG_PATH = PROJECT_ROOT / "calibration" / "edge_proposals.jsonl"
ALIAS_MAP_PATH = PROJECT_ROOT / "data_store" / "entity_aliases.json"


# ── Pydantic contracts ────────────────────────────────────────


class EdgeProposalCandidate(BaseModel):
    """A single supplier→customer edge candidate extracted from a filing.

    Confidence is the *proposer's* prior on the edge actually existing.
    It is NOT the statistical validation confidence — that gets stamped
    by CausalFusion afterwards.
    """

    src_ticker: str
    dst_ticker: str
    criticality: str = "MEDIUM"  # HIGH|MEDIUM|LOW from the LLM
    relationship: str = "supplier"  # supplier|customer|partner
    raw_entity: str = ""  # original string from the filing
    extracted_from: str  # ticker whose filing the mention came from
    source_filing_url: str = ""
    form_type: str = "10-K"
    proposer_confidence: float = Field(default=0.2, ge=0.0, le=1.0)
    # Multi-asset extension: asset class for each side of the edge
    src_asset_class: str = "EQUITY"  # EQUITY|FX|COMMODITY|CRYPTO|ETF|BOND
    dst_asset_class: str = "EQUITY"


class CompetitorProposal(BaseModel):
    """Free-form competitor mention resolved to a canonical ticker."""

    ticker_a: str
    ticker_b: str
    raw_entity: str = ""
    extracted_from: str
    source_filing_url: str = ""


class ProductProposal(BaseModel):
    """Company → Product manufacturing relationship from a 10-K."""

    ticker: str
    product_name: str
    category: str = "unknown"
    extracted_from: str
    source_filing_url: str = ""


class FacilityProposal(BaseModel):
    """Facility node + OPERATES edge proposal from a 10-K."""

    facility_id: str
    name: str
    facility_type: str = "unknown"
    country: str | None = None
    operator_ticker: str
    extracted_from: str
    source_filing_url: str = ""


class EdgeProposalResult(BaseModel):
    """Return shape of propose_edges_from_10k."""

    ticker: str
    filings_analyzed: int
    candidates_raw: int
    candidates_resolved: int
    candidates_rejected: int
    proposals: list[EdgeProposalCandidate] = Field(default_factory=list)
    competitor_proposals: list[CompetitorProposal] = Field(default_factory=list)
    product_proposals: list[ProductProposal] = Field(default_factory=list)
    facility_proposals: list[FacilityProposal] = Field(default_factory=list)
    rejections: list[dict[str, Any]] = Field(default_factory=list)


# ── Entity Resolution ─────────────────────────────────────────

_STOP_WORDS = {
    "inc",
    "inc.",
    "incorporated",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
    "companies",
    "ltd",
    "ltd.",
    "limited",
    "plc",
    "llc",
    "group",
    "holdings",
    "holding",
    "sa",
    "s.a.",
    "n.v.",
    "ag",
    "se",
    "spa",
    "s.p.a.",
    "pte",
    "pty",
    "the",
    "&",
    "and",
}


def _normalize(name: str) -> str:
    """Normalize a company name to a comparable form.

    Lowercase, drop punctuation, strip legal suffixes and whitespace.
    """
    if not name:
        return ""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    tokens = [t for t in cleaned.split() if t and t not in _STOP_WORDS]
    return " ".join(tokens)


def _load_alias_map() -> dict[str, str]:
    """Load curated / learned name→ticker alias table.

    This file is write-through-auto-updated whenever the LLM confidently
    resolves a new alias via CIK lookup. Ships empty; grows at runtime.
    """
    if not ALIAS_MAP_PATH.exists():
        return {}
    try:
        raw = json.loads(ALIAS_MAP_PATH.read_text())
        return {_normalize(k): v.upper() for k, v in raw.items() if v}
    except (json.JSONDecodeError, OSError):
        return {}


def _persist_alias(name: str, ticker: str) -> None:
    """Append one learned alias back to disk."""
    ALIAS_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if ALIAS_MAP_PATH.exists():
        try:
            existing = json.loads(ALIAS_MAP_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing[name] = ticker.upper()
    ALIAS_MAP_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True))


class EntityResolver:
    """Map free-form company mentions in 10-Ks to canonical tickers.

    Uses (in order):
      1. Exact ticker passthrough ("AAPL" → "AAPL")
      2. Curated alias map from ``data_store/entity_aliases.json``
      3. SEC EDGAR full-company-ticker index (cached on disk via sec_edgar)
      4. Fuzzy match (token-set overlap) against that same index
      5. Giving up — candidate gets dropped with a rejection reason

    Matches are cached in-process for the lifetime of a single run.
    """

    def __init__(self) -> None:
        self._alias_map = _load_alias_map()
        self._sec_name_to_ticker: dict[str, str] | None = None
        self._memo: dict[str, str | None] = {}

    async def _ensure_sec_index(self) -> dict[str, str]:
        """Lazy-build a SEC name→ticker map from the cached CIK file."""
        if self._sec_name_to_ticker is not None:
            return self._sec_name_to_ticker

        from data_primitives.extractors.sec_edgar import (
            _CIK_CACHE_PATH,
            _fetch_full_ticker_map,
        )

        # Side-effect: populates the CIK cache if missing.
        await _fetch_full_ticker_map()

        index: dict[str, str] = {}
        if _CIK_CACHE_PATH.exists():
            try:
                cik_data = json.loads(_CIK_CACHE_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                cik_data = {}
            for ticker, _cik in cik_data.items():
                # Without the underlying company-name payload we can only
                # match on ticker — that's still useful since 10-Ks often
                # name suppliers by their ticker symbol directly.
                index[_normalize(ticker)] = ticker.upper()

        # Also mine the raw SEC company_tickers.json for the "title" field
        # (the legal company name). That's the key lookup for fuzzy match.
        try:
            import httpx

            from shared.config import settings

            headers = {"User-Agent": f"AYVID {settings.SEC_EDGAR_EMAIL}"}
            async with httpx.AsyncClient(headers=headers, timeout=30) as client:
                resp = await client.get("https://www.sec.gov/files/company_tickers.json")
                resp.raise_for_status()
                data: dict[str, dict[str, Any]] = resp.json()
                for entry in data.values():
                    title = entry.get("title")
                    ticker = entry.get("ticker")
                    if title and ticker:
                        index[_normalize(title)] = str(ticker).upper()
        except Exception as exc:
            log.debug(f"SEC name-index fetch failed, fuzzy match degrades: {exc}")

        self._sec_name_to_ticker = index
        log.info(f"EntityResolver built SEC index with {len(index)} names")
        return index

    async def resolve(self, raw: str) -> tuple[str | None, str]:
        """Resolve one mention → (ticker_or_None, resolution_method).

        ``resolution_method`` is one of:
          - ``"passthrough"`` — already a ticker
          - ``"alias"`` — matched curated alias map
          - ``"sec_exact"`` — exact match in SEC index
          - ``"sec_fuzzy"`` — token-set overlap match
          - ``"unresolved"`` — dropped
        """
        if not raw or not isinstance(raw, str):
            return None, "unresolved"

        raw_stripped = raw.strip()
        norm = _normalize(raw_stripped)
        if not norm:
            return None, "unresolved"

        if norm in self._memo:
            t = self._memo[norm]
            return t, ("alias" if t else "unresolved")

        # 1. Passthrough: already a ticker? Accept uppercase alphanumeric 1-6.
        if re.fullmatch(r"[A-Z0-9]{1,6}(\.[A-Z]{1,3})?", raw_stripped):
            self._memo[norm] = raw_stripped.upper()
            return raw_stripped.upper(), "passthrough"

        # 2. Alias map.
        if norm in self._alias_map:
            t = self._alias_map[norm]
            self._memo[norm] = t
            return t, "alias"

        # 3. SEC index exact.
        index = await self._ensure_sec_index()
        if norm in index:
            t = index[norm]
            self._memo[norm] = t
            _persist_alias(norm, t)
            self._alias_map[norm] = t
            return t, "sec_exact"

        # 4. SEC fuzzy — token-set containment (both directions) with a
        #    confidence threshold on token overlap.
        raw_tokens = set(norm.split())
        if not raw_tokens:
            self._memo[norm] = None
            return None, "unresolved"

        best_ticker: str | None = None
        best_overlap = 0.0
        for cand_norm, cand_ticker in index.items():
            cand_tokens = set(cand_norm.split())
            if not cand_tokens:
                continue
            inter = len(raw_tokens & cand_tokens)
            if inter == 0:
                continue
            union = len(raw_tokens | cand_tokens)
            jaccard = inter / union if union else 0.0
            if jaccard > best_overlap:
                best_overlap = jaccard
                best_ticker = cand_ticker
                if jaccard == 1.0:
                    break

        # 0.67 is the empirical "Samsung Electronics" → "samsung electronics"
        # cutoff. Anything below that starts to admit false positives like
        # "global foundries" → "global industries".
        if best_ticker and best_overlap >= 0.67:
            self._memo[norm] = best_ticker
            _persist_alias(norm, best_ticker)
            self._alias_map[norm] = best_ticker
            return best_ticker, "sec_fuzzy"

        self._memo[norm] = None
        return None, "unresolved"


# ── Proposal Extraction ───────────────────────────────────────

_CRITICALITY_TO_DEPENDENCY = {
    "HIGH": 0.30,
    "MEDIUM": 0.15,
    "LOW": 0.05,
}


def _criticality_to_dep_pct(criticality: str) -> float:
    """Map the LLM's coarse HIGH/MEDIUM/LOW label to a numeric dependency pct.

    These are *priors* only — the refinery will later validate / re-weight
    against observational returns. Conservative by design: if the LLM said
    HIGH and the data doesn't support it, the refinery will knock confidence
    down and find_hidden_victims will stop traversing the edge.
    """
    return _CRITICALITY_TO_DEPENDENCY.get(
        (criticality or "").upper(), _CRITICALITY_TO_DEPENDENCY["MEDIUM"]
    )


def _walk_filing_payload(
    filing_payload: dict[str, Any],
    source_ticker: str,
) -> list[dict[str, Any]]:
    """Extract every raw (src, dst, criticality, relationship) candidate
    from one LLM-analyzed filing payload.

    Handles both schemas emitted by sec_edgar.py and filing_analyzer.py,
    plus the key_suppliers / key_customers lists.

    Returns a list of dicts with keys::
        {"src_raw", "dst_raw", "criticality", "relationship", "raw_entity"}

    IMPORTANT: every returned dict is relationship-ORIENTED already —
    `src_raw` is always the supplier, `dst_raw` the customer.
    """
    out: list[dict[str, Any]] = []
    src = source_ticker.upper()

    # Schema A: supply_chain_dependencies with {entity, relationship, criticality}
    # (sec_edgar.py payload)
    deps = filing_payload.get("supply_chain_dependencies") or []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        entity = (dep.get("entity") or dep.get("supplier") or "").strip()
        rel = (dep.get("relationship") or "supplier").lower()
        crit = (dep.get("criticality") or "MEDIUM").upper()
        if not entity:
            continue

        # relationship=supplier → entity supplies source_ticker
        # relationship=customer → source_ticker supplies entity
        # relationship=partner  → skip (undirected; not a SUPPLIES_TO edge)
        if rel == "customer":
            out.append(
                {
                    "src_raw": src,
                    "dst_raw": entity,
                    "criticality": crit,
                    "relationship": "customer",
                    "raw_entity": entity,
                }
            )
        elif rel in ("supplier", "partner") or not rel:
            # Default assumption: filing lists entity as something source
            # *depends on* (i.e., supplier of source). Partners are weaker
            # but still directional in filings.
            out.append(
                {
                    "src_raw": entity,
                    "dst_raw": src,
                    "criticality": crit,
                    "relationship": "supplier",
                    "raw_entity": entity,
                }
            )

    # Schema B: explicit key_suppliers / key_customers lists (either module)
    for supplier in filing_payload.get("key_suppliers") or []:
        if isinstance(supplier, str) and supplier.strip():
            out.append(
                {
                    "src_raw": supplier,
                    "dst_raw": src,
                    "criticality": "MEDIUM",
                    "relationship": "supplier",
                    "raw_entity": supplier,
                }
            )
    for customer in filing_payload.get("key_customers") or []:
        if isinstance(customer, str) and customer.strip():
            out.append(
                {
                    "src_raw": src,
                    "dst_raw": customer,
                    "criticality": "MEDIUM",
                    "relationship": "customer",
                    "raw_entity": customer,
                }
            )

    return out


async def propose_edges_from_10k(
    ticker: str,
    *,
    resolver: EntityResolver | None = None,
    proposer_confidence: float = 0.2,
) -> EdgeProposalResult:
    """Run the full 10-K → edge-candidate pipeline for one ticker.

    Does not touch Neo4j. Returns an `EdgeProposalResult` the caller can
    feed to `ingest_proposed_edges` to actually persist.
    """
    from data_primitives.extractors.sec_edgar import full_edgar_pipeline

    resolver = resolver or EntityResolver()
    source = ticker.upper()

    try:
        filings = await full_edgar_pipeline(source)
    except Exception as exc:
        log.warning(f"edge_proposer: EDGAR pipeline failed for {source}: {exc}")
        return EdgeProposalResult(
            ticker=source,
            filings_analyzed=0,
            candidates_raw=0,
            candidates_resolved=0,
            candidates_rejected=0,
        )

    if not filings:
        return EdgeProposalResult(
            ticker=source,
            filings_analyzed=0,
            candidates_raw=0,
            candidates_resolved=0,
            candidates_rejected=0,
        )

    raw_candidates: list[dict[str, Any]] = []
    raw_competitors: list[dict[str, Any]] = []
    raw_products: list[dict[str, Any]] = []
    raw_facilities: list[dict[str, Any]] = []
    filings_analyzed = 0
    for payload in filings:
        if not isinstance(payload, dict) or payload.get("error"):
            continue
        filings_analyzed += 1
        meta = payload.get("filing_metadata") or {}
        filing_url = meta.get("url", "")
        form_type = meta.get("form_type") or payload.get("form_type") or "10-K"
        for cand in _walk_filing_payload(payload, source):
            cand["source_filing_url"] = filing_url
            cand["form_type"] = form_type
            raw_candidates.append(cand)
        for comp in payload.get("key_competitors") or []:
            if isinstance(comp, str) and comp.strip():
                raw_competitors.append(
                    {
                        "raw": comp.strip(),
                        "source_filing_url": filing_url,
                    }
                )
        for prod in payload.get("key_products") or []:
            if isinstance(prod, dict):
                name = (prod.get("name") or "").strip()
                if name:
                    raw_products.append(
                        {
                            "name": name,
                            "category": (prod.get("category") or "unknown").strip().lower(),
                            "source_filing_url": filing_url,
                        }
                    )
            elif isinstance(prod, str) and prod.strip():
                raw_products.append(
                    {
                        "name": prod.strip(),
                        "category": "unknown",
                        "source_filing_url": filing_url,
                    }
                )
        for fac in payload.get("key_facilities") or []:
            if not isinstance(fac, dict):
                continue
            fname = (fac.get("name") or "").strip()
            if not fname:
                continue
            raw_facilities.append(
                {
                    "name": fname,
                    "facility_type": (fac.get("facility_type") or "unknown").strip().lower(),
                    "country": (fac.get("country") or "").strip().upper() or None,
                    "source_filing_url": filing_url,
                }
            )

    resolved: list[EdgeProposalCandidate] = []
    rejected: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()

    for cand in raw_candidates:
        src_tick, src_method = await resolver.resolve(cand["src_raw"])
        dst_tick, dst_method = await resolver.resolve(cand["dst_raw"])

        if not src_tick or not dst_tick:
            rejected.append(
                {
                    "src_raw": cand["src_raw"],
                    "dst_raw": cand["dst_raw"],
                    "reason": "unresolved_entity",
                    "src_method": src_method,
                    "dst_method": dst_method,
                }
            )
            continue
        if src_tick == dst_tick:
            rejected.append(
                {
                    "src_raw": cand["src_raw"],
                    "dst_raw": cand["dst_raw"],
                    "reason": "self_loop",
                }
            )
            continue
        if (src_tick, dst_tick) in seen_edges:
            continue
        seen_edges.add((src_tick, dst_tick))

        resolved.append(
            EdgeProposalCandidate(
                src_ticker=src_tick,
                dst_ticker=dst_tick,
                criticality=cand.get("criticality", "MEDIUM"),
                relationship=cand.get("relationship", "supplier"),
                raw_entity=cand.get("raw_entity", ""),
                extracted_from=source,
                source_filing_url=cand.get("source_filing_url", ""),
                form_type=cand.get("form_type", "10-K"),
                proposer_confidence=proposer_confidence,
            )
        )

    # ── Competitor proposals ────────────────────────────────
    competitor_proposals: list[CompetitorProposal] = []
    seen_competitor_pairs: set[tuple[str, str]] = set()
    for comp in raw_competitors:
        comp_tick, comp_method = await resolver.resolve(comp["raw"])
        if not comp_tick or comp_tick == source:
            continue
        pair = tuple(sorted([source, comp_tick]))
        if pair in seen_competitor_pairs:
            continue
        seen_competitor_pairs.add(pair)
        competitor_proposals.append(
            CompetitorProposal(
                ticker_a=source,
                ticker_b=comp_tick,
                raw_entity=comp["raw"],
                extracted_from=source,
                source_filing_url=comp.get("source_filing_url", ""),
            )
        )

    # ── Product proposals (deduped on name) ─────────────────
    product_proposals: list[ProductProposal] = []
    seen_products: set[str] = set()
    for prod in raw_products:
        key = prod["name"].lower()
        if key in seen_products:
            continue
        seen_products.add(key)
        product_proposals.append(
            ProductProposal(
                ticker=source,
                product_name=prod["name"],
                category=prod.get("category", "unknown"),
                extracted_from=source,
                source_filing_url=prod.get("source_filing_url", ""),
            )
        )

    # ── Facility proposals ──────────────────────────────────
    facility_proposals: list[FacilityProposal] = []
    seen_facility_ids: set[str] = set()
    for fac in raw_facilities:
        # Deterministic facility_id: ``<ticker>:<facility_type>:<name-slug>``
        slug = re.sub(r"[^a-z0-9]+", "-", fac["name"].lower()).strip("-")
        fac_id = f"{source}:{fac['facility_type']}:{slug}"
        if fac_id in seen_facility_ids:
            continue
        seen_facility_ids.add(fac_id)
        facility_proposals.append(
            FacilityProposal(
                facility_id=fac_id,
                name=fac["name"],
                facility_type=fac.get("facility_type", "unknown"),
                country=fac.get("country"),
                operator_ticker=source,
                extracted_from=source,
                source_filing_url=fac.get("source_filing_url", ""),
            )
        )

    log.info(
        f"edge_proposer[{source}]: filings={filings_analyzed} "
        f"supply={len(resolved)} competitors={len(competitor_proposals)} "
        f"products={len(product_proposals)} facilities={len(facility_proposals)} "
        f"rejected={len(rejected)}"
    )
    return EdgeProposalResult(
        ticker=source,
        filings_analyzed=filings_analyzed,
        candidates_raw=len(raw_candidates),
        candidates_resolved=len(resolved),
        candidates_rejected=len(rejected),
        proposals=resolved,
        competitor_proposals=competitor_proposals,
        product_proposals=product_proposals,
        facility_proposals=facility_proposals,
        rejections=rejected,
    )


# ── Ingestion + Audit Trail ───────────────────────────────────


def _audit_row(
    cand: EdgeProposalCandidate,
    *,
    outcome: str,
    confidence_before: float | None,
    confidence_after: float,
) -> dict[str, Any]:
    return {
        "ts": dt.datetime.now(dt.UTC).isoformat(),
        "src": cand.src_ticker,
        "dst": cand.dst_ticker,
        "extracted_from": cand.extracted_from,
        "form_type": cand.form_type,
        "filing_url": cand.source_filing_url,
        "raw_entity": cand.raw_entity,
        "criticality": cand.criticality,
        "relationship": cand.relationship,
        "confidence_before": confidence_before,
        "confidence_after": confidence_after,
        "outcome": outcome,
        "method": "llm_10k_proposal",
    }


def _append_audit(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG_PATH.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")


async def ingest_competitor_proposals(
    proposals: Iterable[CompetitorProposal],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Persist resolved competitor edges via bulk_upsert_competitor_edges."""
    from graph_intelligence.tools.neo4j_tools import bulk_upsert_competitor_edges

    rows = [(p.ticker_a, p.ticker_b, 0.4) for p in proposals]
    if dry_run or not rows:
        return {"dry_run": dry_run, "competitor_pairs": len(rows), "ingested": 0}
    try:
        upserted = await bulk_upsert_competitor_edges(rows, source="llm_10k_proposal")
        return {"dry_run": False, "competitor_pairs": len(rows), "ingested": upserted}
    except Exception as exc:
        log.warning(f"ingest_competitor_proposals failed: {exc}")
        return {"dry_run": False, "competitor_pairs": len(rows), "ingested": 0, "error": str(exc)}


async def ingest_product_proposals(
    proposals: Iterable[ProductProposal],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Persist Company → MANUFACTURES → Product edges."""
    from graph_intelligence.tools.neo4j_tools import upsert_manufactures_edge

    proposals = list(proposals)
    if dry_run or not proposals:
        return {"dry_run": dry_run, "products": len(proposals), "ingested": 0}

    ingested = 0
    for p in proposals:
        try:
            await upsert_manufactures_edge(
                p.ticker,
                p.product_name,
                category=p.category,
            )
            ingested += 1
        except Exception as exc:
            log.warning(f"upsert_manufactures_edge failed for {p.ticker}/{p.product_name}: {exc}")
    return {"dry_run": False, "products": len(proposals), "ingested": ingested}


async def ingest_facility_proposals(
    proposals: Iterable[FacilityProposal],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Persist Facility nodes + OPERATES edges."""
    from graph_intelligence.tools.neo4j_tools import upsert_facility
    from shared.types import FacilityNode

    proposals = list(proposals)
    if dry_run or not proposals:
        return {"dry_run": dry_run, "facilities": len(proposals), "ingested": 0}

    ingested = 0
    for p in proposals:
        try:
            node = FacilityNode(
                facility_id=p.facility_id,
                name=p.name,
                facility_type=p.facility_type,
                operator_ticker=p.operator_ticker,
                operational=True,
            )
            await upsert_facility(node)
            ingested += 1
        except Exception as exc:
            log.warning(f"upsert_facility failed for {p.facility_id}: {exc}")
    return {"dry_run": False, "facilities": len(proposals), "ingested": ingested}


async def ingest_proposed_edges(
    candidates: Iterable[EdgeProposalCandidate],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Persist proposed edges into Neo4j + append to the audit JSONL.

    Writes with ``confidence = candidate.proposer_confidence`` (default 0.2)
    and ``confidence_method = "llm_10k_proposal"``. This keeps the edge
    BELOW the ``find_hidden_victims(min_edge_confidence=0.6)`` cutoff until
    the refinery either promotes or removes it.

    An *existing* edge's confidence is PRESERVED — we never overwrite a
    higher refinery-validated score with the proposer's floor.
    """
    from graph_intelligence.tools.neo4j_tools import upsert_proposed_edge

    cand_list = list(candidates)
    audit_rows: list[dict[str, Any]] = []
    ingested = 0
    preserved = 0
    skipped = 0

    for cand in cand_list:
        if dry_run:
            audit_rows.append(
                _audit_row(
                    cand,
                    outcome="dry_run",
                    confidence_before=None,
                    confidence_after=cand.proposer_confidence,
                )
            )
            skipped += 1
            continue

        try:
            state = await upsert_proposed_edge(
                src_ticker=cand.src_ticker,
                dst_ticker=cand.dst_ticker,
                proposer_confidence=cand.proposer_confidence,
                revenue_dependency_pct=_criticality_to_dep_pct(cand.criticality),
                source_filing=cand.source_filing_url,
                proposed_by=cand.extracted_from,
                criticality_label=cand.criticality,
            )
        except Exception as exc:
            log.warning(
                f"ingest_proposed_edges: upsert failed {cand.src_ticker}→{cand.dst_ticker}: {exc}"
            )
            skipped += 1
            continue

        if state["created"]:
            ingested += 1
            outcome = "created"
        else:
            preserved += 1
            outcome = "preserved_existing"

        audit_rows.append(
            _audit_row(
                cand,
                outcome=outcome,
                confidence_before=state.get("confidence_before"),
                confidence_after=state.get("confidence_after"),
            )
        )

    _append_audit(audit_rows)

    return {
        "total_candidates": len(cand_list),
        "ingested_new": ingested,
        "preserved_existing": preserved,
        "skipped": skipped,
        "dry_run": dry_run,
        "audit_log": str(AUDIT_LOG_PATH),
    }


# ── Batch / Cron Runner ───────────────────────────────────────


async def propose_for_universe(
    tickers: list[str] | None = None,
    *,
    limit: int | None = None,
    concurrency: int = 3,
    proposer_confidence: float = 0.2,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Propose edges for many tickers in parallel.

    When ``tickers`` is ``None``, derives the universe from local OHLCV
    data via ``get_available_tickers`` so the command remains fully
    dynamic — no ticker list ever hard-coded.
    """
    if tickers is None:
        from graph_intelligence.tools.dynamic_universe import get_available_tickers

        tickers = await get_available_tickers()

    if limit is not None:
        tickers = tickers[:limit]

    resolver = EntityResolver()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[EdgeProposalResult] = []

    async def _worker(t: str) -> None:
        async with semaphore:
            try:
                res = await propose_edges_from_10k(
                    t,
                    resolver=resolver,
                    proposer_confidence=proposer_confidence,
                )
                results.append(res)
            except Exception as exc:
                log.warning(f"propose_for_universe: {t} failed: {exc}")

    await asyncio.gather(*(_worker(t) for t in tickers))

    # Flatten across tickers for a single per-domain ingestion pass.
    all_supply: list[EdgeProposalCandidate] = []
    all_competitors: list[CompetitorProposal] = []
    all_products: list[ProductProposal] = []
    all_facilities: list[FacilityProposal] = []
    for res in results:
        all_supply.extend(res.proposals)
        all_competitors.extend(res.competitor_proposals)
        all_products.extend(res.product_proposals)
        all_facilities.extend(res.facility_proposals)

    ingest_summary = await ingest_proposed_edges(all_supply, dry_run=dry_run)
    competitor_summary = await ingest_competitor_proposals(all_competitors, dry_run=dry_run)
    product_summary = await ingest_product_proposals(all_products, dry_run=dry_run)
    facility_summary = await ingest_facility_proposals(all_facilities, dry_run=dry_run)

    return {
        "tickers_analyzed": len(results),
        "filings_analyzed": sum(r.filings_analyzed for r in results),
        "candidates_raw": sum(r.candidates_raw for r in results),
        "candidates_resolved": sum(r.candidates_resolved for r in results),
        "candidates_rejected": sum(r.candidates_rejected for r in results),
        "competitors_proposed": competitor_summary["competitor_pairs"],
        "competitors_ingested": competitor_summary.get("ingested", 0),
        "products_proposed": product_summary["products"],
        "products_ingested": product_summary.get("ingested", 0),
        "facilities_proposed": facility_summary["facilities"],
        "facilities_ingested": facility_summary.get("ingested", 0),
        **ingest_summary,
    }


# ── Audit Log Reader (for API / CLI surfacing) ────────────────


def read_audit_trail(last_n: int = 50) -> list[dict[str, Any]]:
    """Return the last ``last_n`` rows of ``calibration/edge_proposals.jsonl``."""
    if not AUDIT_LOG_PATH.exists():
        return []
    try:
        lines = AUDIT_LOG_PATH.read_text().splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-last_n:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
