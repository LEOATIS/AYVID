"""Tool-driven research pipeline.

Resolve → Retrieve → Compute → Synthesise → Audit. Every number comes from
``research_calc``; every claim carries citation labels that are verified to
exist in the retrieved set before the artifact is saved. The LLM (if any) may
only re-word the executive synthesis, and its output is discarded unless every
citation it uses is one we retrieved.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from backend.services.research_calc import staleness
from backend.services.research_resolver import (
    REFUSALS,
    classify_intent,
    resolve_query,
    screen_query,
)
from backend.services.research_store import Actor, ResearchStore, new_id, utcnow
from shared.config import settings
from shared.logger import get_logger

log = get_logger("research.orchestrator")

FILING_STALE_DAYS = 200
MAX_EXPOSURE_DEPTH = 3
EDGAR_TIMEOUT_SEC = 6.0
LLM_SYNTHESIS_TIMEOUT_SEC = 20.0
CITE = re.compile(r"\[((?:S|CALC)-?\d+)\]")

STEPS = [
    ("resolve", "Resolving instrument & period"),
    ("retrieve", "Retrieving SEC filings & local OHLCV"),
    ("compute", "Computing deterministic metrics & causal paths"),
    ("synthesise", "Synthesizing cited analysis & auditing claims"),
]

MECHANISM = {
    "SUPPLIER": "{src} supplies {dst}; a disruption at {src} constrains {dst}'s input supply.",
    "CUSTOMER": "{src} is a customer of {dst}; demand changes at {src} flow to {dst}'s revenue.",
    "COMPETITOR": "{src} competes with {dst}; share shifts between them.",
    "GEOGRAPHIC_CHOKEPOINT": "{src} concentrates capacity {dst} depends on in one region.",
    "COMMODITY_INPUT": "{src} provides a commodity input priced into {dst}'s costs.",
}


@dataclass
class Run:
    """Mutable per-run accumulator (local to one request)."""

    query: str
    started: float = field(default_factory=time.perf_counter)
    steps: list[dict[str, Any]] = field(default_factory=list)
    citations: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_to_label: dict[str, str] = field(default_factory=dict)
    calcs: dict[str, dict[str, Any]] = field(default_factory=dict)
    banners: list[dict[str, str]] = field(default_factory=list)
    degraded: list[dict[str, str]] = field(default_factory=list)
    live_docs: int = 0

    def cite(self, source: dict[str, Any] | None) -> str | None:
        if not source:
            return None
        sid = source["source_id"]
        if sid not in self.source_to_label:
            label = f"S{len(self.source_to_label) + 1}"
            self.source_to_label[sid] = label
            self.citations[label] = {**source, "label": label}
        return self.source_to_label[sid]

    def calc(self, entry: dict[str, Any]) -> str:
        label = f"CALC-{len(self.calcs) + 1}"
        self.calcs[label] = {**entry, "label": label}
        return label

    def step(self, key: str, status: str, detail: str, t0: float) -> None:
        label = dict(STEPS).get(key, key)
        self.steps.append({
            "key": key, "label": label, "status": status, "detail": detail,
            "ms": int((time.perf_counter() - t0) * 1000),
        })


def _fmt(value: str | float | None, scale: str, currency: str | None = None) -> str:
    if value is None:
        return "—"
    v = float(value)
    if scale == "PERCENT":
        return f"{v:.1f}%"
    if scale == "RATIO":
        return f"{v:.2f}"
    if scale == "MILLIONS":
        prefix = {"USD": "$", "EUR": "€", "TWD": "NT$", "INR": "₹"}.get(currency or "", "")
        return f"{prefix}{v / 1000:,.1f}B" if abs(v) >= 1000 else f"{prefix}{v:,.0f}M"
    return f"{v:,.2f} {currency or ''}".strip()


class ResearchOrchestrator:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    # ── public entry ───────────────────────────────────────────────────
    async def run(
        self,
        actor: Actor,
        query: str,
        *,
        simulate_failure: str | None = None,
        instrument_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        run = Run(query=query)
        session_id = new_id("rs")
        t0 = time.perf_counter()
        verdict = screen_query(query)
        if verdict.refusal_code:
            return self._finish(actor, session_id, run, self._refusal(run, verdict.refusal_code, verdict.sanitizer))

        if instrument_ids:
            resolution = resolve_query(self.store, "")
            resolution.resolved = [self.store.get_instrument(i) for i in instrument_ids]
        else:
            resolution = resolve_query(self.store, verdict.clean_query)
        intent = classify_intent(verdict.clean_query, len(resolution.resolved))
        detail = ", ".join(i["instrument_id"] for i in resolution.resolved) or "none"
        status = "done" if resolution.resolved else "blocked"
        run.step("resolve", status, f"Intent {intent}; instruments: {detail}", t0)

        if not resolution.resolved:
            payload = self._refusal(run, "NO_INSTRUMENT", verdict.sanitizer)
            payload["resolution"] = resolution.to_dict()
            if resolution.ambiguous:
                payload["status"] = "NEEDS_DISAMBIGUATION"
                payload["refusal"] = None
            return self._finish(actor, session_id, run, payload)

        for note in resolution.notes:
            run.banners.append({"kind": "AMBIGUOUS", "message": note})
        for amb in resolution.ambiguous:
            run.banners.append({"kind": "AMBIGUOUS", "message": f"{amb['token']}: {amb['reason']} — not analysed."})
        if resolution.unsupported:
            run.banners.append({
                "kind": "UNSUPPORTED",
                "message": "Not in AYVID universe (ignored): " + ", ".join(resolution.unsupported),
            })

        instruments = resolution.resolved
        t1 = time.perf_counter()
        retrieved = await self._retrieve(run, instruments, simulate_failure)
        run.step(
            "retrieve", "degraded" if run.degraded else "done",
            f"{sum(len(r['sources']) for r in retrieved.values())} source documents, "
            f"{run.live_docs} live EDGAR filings" + (" — upstream provider unavailable" if run.degraded else ""),
            t1,
        )

        t2 = time.perf_counter()
        table, metrics = self._compute_metrics(run, instruments)
        exposures = self._exposures(run, instruments, intent)
        run.step("compute", "done", f"{len(run.calcs)} calculations, {len(exposures)} exposure paths", t2)

        t3 = time.perf_counter()
        sections = self._synthesise(run, instruments, intent, metrics, exposures, verdict.clean_query)
        telemetry = await self._llm_polish(run, sections, simulate_failure)
        audit = self._audit(run, sections)
        run.step("synthesise", "done",
                 f"{audit['verified']} citations verified, {audit['dropped']} unverifiable claims dropped", t3)

        payload = {
            "status": "COMPLETE",
            "intent": intent,
            "title": self._title(instruments, intent),
            "resolution": resolution.to_dict(),
            "summary": sections["summary"],
            "data_table": table,
            "exposures": exposures,
            "bull": sections["bull"],
            "bear": sections["bear"],
            "uncertainty": sections["uncertainty"],
            "monitor": sections["monitor"],
            "audit": audit,
            "claim_links": audit.pop("links"),
            "telemetry": {
                **telemetry,
                "retrieval": {
                    "source_documents": len(run.citations),
                    "live_edgar_documents": run.live_docs,
                    "degraded_providers": run.degraded,
                },
                "sanitizer": verdict.sanitizer,
            },
            "refusal": None,
        }
        return self._finish(actor, session_id, run, payload)

    # ── helpers ────────────────────────────────────────────────────────
    def _refusal(self, run: Run, code: str, sanitizer: dict[str, Any]) -> dict[str, Any]:
        run.step("resolve", "blocked", f"Boundary: {code}", run.started)
        return {
            "status": "REFUSED",
            "intent": "UNSUPPORTED_QUERY",
            "title": "Outside AYVID's research scope",
            "refusal": {"code": code, "message": REFUSALS[code]},
            "resolution": {"resolved": [], "ambiguous": [], "unsupported": [], "notes": []},
            "summary": None, "data_table": [], "exposures": [], "bull": [], "bear": [],
            "uncertainty": [], "monitor": [], "claim_links": [],
            "audit": {"verified": 0, "dropped": 0, "dropped_claims": []},
            "telemetry": {"provider": "deterministic", "model": None, "llm_used": False,
                          "sanitizer": sanitizer},
        }

    def _finish(self, actor: Actor, session_id: str, run: Run, payload: dict[str, Any]) -> dict[str, Any]:
        fixtures = [bool(c.get("is_fixture")) for c in run.citations.values()]
        if fixtures and all(fixtures):
            data_mode = "FIXTURE"
        elif run.live_docs and not any(fixtures):
            data_mode = "LIVE"
        else:
            data_mode = "HYBRID"
        data_down = sorted({d["provider"] for d in run.degraded if d["provider"] == "SEC_EDGAR"})
        llm_down = sorted({d["provider"] for d in run.degraded if d["provider"] != "SEC_EDGAR"})
        if llm_down:
            reasons = ", ".join(sorted({d["error"] for d in run.degraded if d["provider"] in llm_down}))
            run.banners.insert(0, {
                "kind": "DEGRADED",
                "message": f"DEGRADED MODE — LLM provider unavailable ({', '.join(llm_down)}: {reasons}). "
                           "Synthesis fell back to the deterministic engine; every figure and citation is unaffected.",
            })
        if data_down:
            run.banners.insert(0, {
                "kind": "DEGRADED",
                "message": "DEGRADED MODE — Upstream provider unavailable (SEC EDGAR). Answer uses local verified "
                           "sources only; no live filing was fetched or cited.",
            })
        artifact = {
            **payload,
            "session_id": session_id,
            "query": run.query,
            "created_at": utcnow(),
            "data_mode": data_mode,
            "steps": run.steps,
            "banners": run.banners,
            "citations": run.citations,
            "calculations": run.calcs,
        }
        artifact["telemetry"] = {**artifact.get("telemetry", {}),
                                 "total_ms": int((time.perf_counter() - run.started) * 1000)}
        self.store.save_session(
            actor,
            {"session_id": session_id, "query": run.query, "intent": payload["intent"],
             "status": payload["status"], "data_mode": data_mode, "title": payload.get("title")},
            artifact,
        )
        return artifact

    @staticmethod
    def _title(instruments: list[dict[str, Any]], intent: str) -> str:
        names = " · ".join(i["symbol"] for i in instruments[:4])
        return f"{names} — {intent.replace('_', ' ').title()}"

    async def _retrieve(
        self, run: Run, instruments: list[dict[str, Any]], simulate_failure: str | None
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        edgar_down = simulate_failure in ("edgar", "all")
        for inst in instruments:
            sources = self.store.sources_for(inst["instrument_id"])
            if inst["data_tier"] == "LIVE_EDGAR_AND_LOCAL_OHLCV" and settings_live_edgar():
                if edgar_down:
                    run.degraded.append({"provider": "SEC_EDGAR", "instrument": inst["symbol"],
                                         "error": "simulated outage"})
                else:
                    sources = await self._live_edgar(run, inst) + sources
            out[inst["instrument_id"]] = {"sources": sources}
        return out

    async def _live_edgar(self, run: Run, inst: dict[str, Any]) -> list[dict[str, Any]]:
        from data_primitives.extractors.sec_edgar import fetch_recent_filings

        try:
            filings = await asyncio.wait_for(
                fetch_recent_filings(inst["symbol"], limit=3), timeout=EDGAR_TIMEOUT_SEC
            )
        except Exception as exc:  # network, 4xx/5xx, timeout — degrade, never invent
            run.degraded.append({"provider": "SEC_EDGAR", "instrument": inst["symbol"],
                                 "error": type(exc).__name__})
            log.warning("edgar fetch failed for %s: %s", inst["symbol"], exc)
            return []
        docs = []
        for f in filings:
            if not f.get("accession"):
                continue
            doc = {
                "source_id": f"SRC-EDGAR-{f['accession']}", "provider": "SEC_EDGAR",
                "title": f"{f.get('company_name', inst['symbol'])} {f.get('form_type')} filed {f.get('filing_date')}",
                "publisher": "SEC EDGAR", "canonical_url": f.get("url") or None,
                "published_at": f.get("filing_date"), "retrieved_at": utcnow(),
                "doc_type": f.get("form_type") or "8-K", "instrument_id": inst["instrument_id"],
                "accession_no": f["accession"], "locator": "EDGAR full-text search index",
                "excerpt": "Filing index entry retrieved live from EDGAR; open the canonical URL for the document.",
                "content_hash": None, "access_status": "AVAILABLE" if f.get("url") else "UNAVAILABLE",
                "is_fixture": 0,
            }
            self.store.insert("source_documents", doc, replace=True)
            docs.append({**doc, "is_fixture": False})
        run.live_docs += len(docs)
        return docs

    def _compute_metrics(
        self, run: Run, instruments: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        table: list[dict[str, Any]] = []
        metrics: dict[str, dict[str, Any]] = {}
        stale_days = settings.RESEARCH_STALE_DAYS
        for inst in instruments:
            iid = inst["instrument_id"]
            obs = self.store.observations(iid)
            latest: dict[str, dict[str, Any]] = {}
            for o in obs:
                latest[o["metric_code"]] = o  # ordered by period_end → last wins
            m: dict[str, Any] = {"latest": latest, "refs": {}}
            for code in ("REVENUE", "GROSS_MARGIN_PCT", "OPERATING_MARGIN_PCT", "NET_MARGIN_PCT",
                         "REVENUE_GROWTH_PCT", "CLOSE_PRICE", "RETURN_90D_PCT", "REALIZED_VOL_ANN",
                         "MAX_DRAWDOWN_PCT", "HURST_EXPONENT"):
                o = latest.get(code)
                if o is None:
                    continue
                source = self.store.get_source(o["source_id"]) if o.get("source_id") else None
                s_label = run.cite(source)
                ref = s_label
                if o["status"] == "DERIVED":
                    inputs = json.loads(o["inputs"]) if isinstance(o.get("inputs"), str) else o.get("inputs")
                    ref = run.calc({
                        "label_text": f"{inst['symbol']} {o['metric_label']} ({o['fiscal_period']})",
                        "formula": o["formula"], "inputs": inputs, "result": o["value"],
                        "unit": "%" if o["scale"] == "PERCENT" else o["scale"].lower(),
                        "observation_id": o["observation_id"], "source_label": s_label,
                        "instrument_id": iid, "as_of": o["as_of"],
                    })
                fresh = staleness(o["as_of"], FILING_STALE_DAYS if o["provider"] == "FIXTURE_FILING" else stale_days)
                m["refs"][code] = ref
                table.append({
                    "instrument_id": iid, "symbol": inst["symbol"], "metric_code": code,
                    "metric_label": o["metric_label"], "fiscal_period": o["fiscal_period"],
                    "value": o["value"], "display": _fmt(o["value"], o["scale"], o.get("currency")),
                    "currency": o.get("currency"), "scale": o["scale"], "basis": o["basis"],
                    "status": "STALE" if fresh["is_stale"] else o["status"], "as_of": o["as_of"],
                    "is_stale": fresh["is_stale"], "age_days": fresh["age_days"],
                    "ref": ref, "source_label": s_label, "observation_id": o["observation_id"],
                    "epistemic": "CALCULATED" if o["status"] == "DERIVED" else "REPORTED",
                })
            price_obs = latest.get("CLOSE_PRICE")
            fresh = staleness(price_obs["as_of"] if price_obs else None, stale_days)
            m["stale"] = fresh
            metrics[iid] = m
        stale = [(i["symbol"], metrics[i["instrument_id"]]["stale"]) for i in instruments
                 if metrics[i["instrument_id"]]["stale"]["is_stale"] and metrics[i["instrument_id"]]["stale"]["as_of"]]
        if stale:
            oldest = min(s["as_of"] for _, s in stale)
            names = ", ".join(f"{sym} ({s['age_days']}d)" for sym, s in stale)
            run.banners.append({
                "kind": "STALE",
                "message": f"STALE DATA (As of {oldest}) — latest local bars are older than "
                           f"{stale_days} days: {names}. Price-based figures are not current; do not use them "
                           "for live market-cap or valuation ratios.",
            })
        return table, metrics

    def _exposures(self, run: Run, instruments: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        symbols = [i["local_symbol"] or i["symbol"] for i in instruments]
        seen: set[str] = set()

        def add(edge: dict[str, Any], order: int, path: list[str], direction: str, score: float) -> None:
            if edge["edge_id"] in seen:
                return
            seen.add(edge["edge_id"])
            label = run.cite(self.store.get_source(edge["source_id"]) if edge.get("source_id") else None)
            rows.append({
                "edge_id": edge["edge_id"], "order": order, "direction": direction, "path": path,
                "from": edge["src_symbol"], "to": edge["dst_symbol"],
                "from_instrument_id": edge["src_instrument_id"], "to_instrument_id": edge["dst_instrument_id"],
                "relationship_type": edge["relationship_type"],
                "mechanism": MECHANISM[edge["relationship_type"]].format(src=edge["src_symbol"], dst=edge["dst_symbol"]),
                "revenue_dependency_pct": edge["revenue_dependency_pct"],
                "criticality_score": edge["criticality_score"], "evidence_status": edge["evidence_status"],
                "uncertainty_note": edge["uncertainty_note"], "source_label": label,
                "exposure_score": round(score, 4),
            })

        # Upstream dependencies (who the subject depends on) — first order only.
        if intent != "EVENT_IMPACT":
            for sym in symbols:
                for edge in self.store.query("SELECT * FROM supply_chain_edges WHERE dst_symbol = ?", (sym,)):
                    add(edge, 1, [edge["src_symbol"], sym], "UPSTREAM", (edge["criticality_score"] or 0.5))

        # Downstream propagation (BFS), product of dependency × criticality per hop.
        frontier = [(s, [s], 1.0) for s in symbols]
        for order in range(1, MAX_EXPOSURE_DEPTH + 1):
            nxt = []
            for sym, path, score in frontier:
                for edge in self.store.edges_from(sym):
                    if edge["dst_symbol"] in path:
                        continue
                    hop = (edge["revenue_dependency_pct"] or 10) / 100 * (edge["criticality_score"] or 0.5)
                    new_score = score * hop if order > 1 else hop
                    add(edge, order, path + [edge["dst_symbol"]], "DOWNSTREAM", new_score)
                    nxt.append((edge["dst_symbol"], path + [edge["dst_symbol"]], new_score))
            frontier = sorted(nxt, key=lambda x: -x[2])[:12]
        rows.sort(key=lambda r: (r["direction"] != "UPSTREAM", r["order"], -r["exposure_score"]))
        return rows[:40]

    def _synthesise(
        self, run: Run, instruments: list[dict[str, Any]], intent: str,
        metrics: dict[str, dict[str, Any]], exposures: list[dict[str, Any]], query: str,
    ) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        bull: list[dict[str, Any]] = []
        bear: list[dict[str, Any]] = []
        uncertainty: list[dict[str, Any]] = []
        monitor: list[dict[str, Any]] = []

        def claim(text: str, epistemic: str, refs: list[str | None], bucket: list[dict[str, Any]] = claims) -> None:
            refs_clean = [r for r in refs if r]
            bucket.append({"claim_id": new_id("c"), "text": text, "epistemic": epistemic, "refs": refs_clean})

        for inst in instruments:
            iid, sym = inst["instrument_id"], inst["symbol"]
            m = metrics[iid]
            lat, refs = m["latest"], m["refs"]
            filings = [s for s in self.store.sources_for(iid) if s["provider"] == "FIXTURE_FILING"]
            rev, gm = lat.get("REVENUE"), lat.get("GROSS_MARGIN_PCT")
            if rev:
                claim(f"{sym} reported revenue of {_fmt(rev['value'], rev['scale'], rev['currency'])} for "
                      f"{rev['fiscal_period']} [{refs['REVENUE']}].", "REPORTED", [refs["REVENUE"]])
            growth = lat.get("REVENUE_GROWTH_PCT")
            if growth:
                claim(f"Annual revenue growth was {_fmt(growth['value'], 'PERCENT')} in {growth['fiscal_period']} "
                      f"[{refs['REVENUE_GROWTH_PCT']}].", "CALCULATED", [refs["REVENUE_GROWTH_PCT"]])
                if float(growth["value"]) > 15:
                    claim(f"{sym}'s reported growth of {_fmt(growth['value'], 'PERCENT')} supports a demand-led thesis "
                          f"[{refs['REVENUE_GROWTH_PCT']}].", "CALCULATED", [refs["REVENUE_GROWTH_PCT"]], bull)
            if gm:
                claim(f"Most recent gross margin: {_fmt(gm['value'], 'PERCENT')} ({gm['fiscal_period']}) "
                      f"[{refs['GROSS_MARGIN_PCT']}].", "REPORTED" if gm["status"] == "REPORTED" else "CALCULATED",
                      [refs["GROSS_MARGIN_PCT"]])
                gm_series = self.store.observations(iid, "GROSS_MARGIN_PCT")
                low = min(gm_series, key=lambda o: float(o["value"]))
                if float(low["value"]) < float(gm["value"]) - 5:
                    low_ref = run.cite(self.store.get_source(low["source_id"]))
                    claim(f"Margins are not stable: gross margin fell to {_fmt(low['value'], 'PERCENT')} in "
                          f"{low['fiscal_period']} [{low_ref}] before recovering.", "REPORTED", [low_ref], bear)
                monitor.append({
                    "metric_code": "GROSS_MARGIN_PCT", "instrument_id": iid, "operator": "<",
                    "threshold_value": round(float(gm["value"]) - 3, 1),
                    "description": f"{sym} gross margin ({'quarterly' if gm['fiscal_period'].startswith('Q') else 'annual'}) < {round(float(gm['value']) - 3, 1)}%",
                })
            vol = lat.get("REALIZED_VOL_ANN")
            if vol:
                regime = "a high" if float(vol["value"]) > 50 else ("an elevated" if float(vol["value"]) > 30 else "a low")
                claim(f"90-day realised volatility is {_fmt(vol['value'], 'PERCENT')} annualised — {regime}-volatility "
                      f"regime as of {vol['as_of']} [{refs['REALIZED_VOL_ANN']}].", "CALCULATED", [refs["REALIZED_VOL_ANN"]])
                monitor.append({
                    "metric_code": "REALIZED_VOL_ANN", "instrument_id": iid, "operator": ">", "threshold_value": 65.0,
                    "description": f"{sym} 90-day realised volatility > 65%",
                })
            ret = lat.get("RETURN_90D_PCT")
            if ret:
                bucket = bull if float(ret["value"]) > 0 else bear
                claim(f"{sym} price moved {_fmt(ret['value'], 'PERCENT')} over the last 90 trading days to "
                      f"{ret['as_of']} [{refs['RETURN_90D_PCT']}].", "CALCULATED", [refs["RETURN_90D_PCT"]], bucket)
                monitor.append({
                    "metric_code": "RETURN_90D_PCT", "instrument_id": iid, "operator": "<", "threshold_value": -20.0,
                    "description": f"{sym} 90-day return < −20%",
                })
            for f in filings:
                text = f["excerpt"]
                sentences = [x.strip().rstrip(".") for x in re.split(r"(?<=[a-z0-9%)]\.)\s+(?=[A-Z])", text) if x.strip()]
                for sentence in sentences:
                    if re.search(r"licen[cs]e|export|china|restrict", sentence, re.I):
                        label = run.cite(f)
                        claim(f"Management disclosure ({f['doc_type']}): {sentence} [{label}].", "MANAGEMENT", [label], bear)
                    elif re.search(r"foundr|TSMC|outsourcing|single or limited|Taiwan|10% of total revenue", sentence):
                        label = run.cite(f)
                        claim(f"Filing discloses a concentration risk: {sentence} [{label}].", "MANAGEMENT", [label], bear)
            if not filings:
                uncertainty.append({"kind": "MISSING_DATA", "text":
                                    f"No filing-derived fundamentals for {sym} in AYVID's dataset; analysis uses market data only."})
            if m["stale"]["is_stale"]:
                uncertainty.append({"kind": "STALE_DATA", "text":
                                    f"{sym} market data ends {m['stale']['as_of']} ({m['stale']['age_days']} days ago)."})

        upstream = [e for e in exposures if e["direction"] == "UPSTREAM"]
        downstream = [e for e in exposures if e["direction"] == "DOWNSTREAM"]
        for inst in instruments[:2]:
            sym = inst["local_symbol"] or inst["symbol"]
            own = [e for e in upstream if e["to"] == sym]
            if not own:
                continue
            top = max(own, key=lambda e: e["criticality_score"] or 0)
            claim(f"{sym}'s highest-criticality upstream dependency: {top['from']} → {top['to']} "
                  f"({top['relationship_type'].lower()}, criticality {top['criticality_score']}, "
                  f"{top['evidence_status'].lower()}) [{top['source_label']}].", "INTERPRETATION", [top["source_label"]])
            monitor.append({
                "metric_code": f"EVENT:{top['from']}_SUPPLY_DISRUPTION", "instrument_id": inst["instrument_id"],
                "operator": "EVENT_OCCURS", "threshold_value": None,
                "description": f"Supply disruption at {top['from']} recorded (affects {sym})",
            })
        if downstream:
            orders = sorted({e["order"] for e in downstream})
            claim(f"A shock at {', '.join(i['symbol'] for i in instruments)} propagates to {len(downstream)} documented or "
                  f"inferred relationships across order(s) {', '.join(map(str, orders))}; highest exposure: "
                  f"{downstream[0]['to']} [{downstream[0]['source_label']}].", "HYPOTHESIS", [downstream[0]["source_label"]])
            if any(e["evidence_status"] != "EXTRACTED" for e in downstream):
                uncertainty.append({"kind": "INFERRED_EDGES", "text":
                                    "Some propagation edges are curated estimates (INFERRED), not extracted from filings."})

        lower = query.lower()
        primary = instruments[0]["instrument_id"]
        for pattern, code, text in (
            (r"churn", "CUSTOMER_CHURN_PCT", "Customer churn is not disclosed in the retrieved filings."),
            (r"capex|capital intensity", "CAPEX", "Capital expenditure is not in AYVID's structured dataset yet."),
            (r"china", None, "China revenue share is not available as a structured metric; only qualitative filing disclosures."),
            (r"customer concentration", None, "Customer-level revenue split is disclosed only qualitatively (>10% customers)."),
        ):
            if re.search(pattern, lower):
                uncertainty.append({"kind": "MISSING_DATA", "text": text})
                if code:
                    monitor.append({"metric_code": code, "instrument_id": primary, "operator": ">",
                                    "threshold_value": 5.0 if code == "CUSTOMER_CHURN_PCT" else 0.0,
                                    "description": f"{instruments[0]['symbol']} {code.replace('_', ' ').lower()} threshold"})
        if not bear:
            uncertainty.append({"kind": "NO_CONTRADICTING_EVIDENCE",
                                "text": "No contradicting evidence was found in retrieved sources — absence of evidence is not evidence of absence."})

        headline = self._headline(instruments, intent, metrics, exposures)
        return {"summary": {"headline": headline, "claims": claims, "llm_rewrite": None},
                "bull": bull, "bear": bear, "uncertainty": uncertainty, "monitor": monitor[:8]}

    @staticmethod
    def _headline(instruments: list[dict[str, Any]], intent: str, metrics: dict[str, Any], exposures: list[dict[str, Any]]) -> str:
        syms = ", ".join(i["symbol"] for i in instruments)
        if intent == "EVENT_IMPACT":
            return (f"Event exposure map for {syms}: {len(exposures)} propagation paths, ranked by dependency × "
                    "criticality. Evidence status is shown per edge.")
        if intent == "FINANCIAL_COMPARISON":
            return f"Side-by-side evidence for {syms}. Figures are in each company's reporting currency — not converted."
        return f"Evidence-backed profile of {syms}: reported fundamentals, deterministic market metrics, exposures and what would invalidate the view."

    async def _llm_polish(self, run: Run, sections: dict[str, Any], simulate_failure: str | None) -> dict[str, Any]:
        from shared import llm_client

        provider = llm_client.active_provider()
        base = {"provider": provider, "model": llm_client.active_model(provider), "llm_used": False,
                "latency_ms": None, "input_tokens": None, "output_tokens": None}
        if provider == llm_client.DETERMINISTIC or not settings.RESEARCH_LLM_SYNTHESIS:
            return {**base, "provider": "deterministic", "model": None,
                    "note": "Deterministic synthesis (no LLM key configured or LLM synthesis disabled)."}
        if simulate_failure in ("llm", "all"):
            run.degraded.append({"provider": provider.upper(), "instrument": "-", "error": "simulated outage"})
            return {**base, "note": "LLM unavailable — deterministic synthesis used."}
        facts = [c["text"] for c in sections["summary"]["claims"]]
        system = (
            "You are AYVID's research editor. Rewrite the facts into a 3-5 sentence executive synthesis. "
            "Use ONLY the given facts. Keep every bracketed citation like [S1] or [CALC-2] attached to the "
            "fact it supports. Do not add numbers, do not recommend buying or selling. "
            'Return JSON: {"synthesis": "..."}'
        )
        try:
            text, meta = await llm_client.json_call_with_meta(
                system, json.dumps({"facts": facts}), max_tokens=600, timeout=LLM_SYNTHESIS_TIMEOUT_SEC
            )
            synthesis = str(json.loads(text).get("synthesis", "")).strip()
        except Exception as exc:
            run.degraded.append({"provider": provider.upper(), "instrument": "-", "error": type(exc).__name__})
            return {**base, "note": f"LLM call failed ({type(exc).__name__}) — deterministic synthesis used."}
        cited = set(CITE.findall(synthesis))
        known = set(run.citations) | set(run.calcs)
        numbers_ok = all(n in " ".join(facts) for n in re.findall(r"\d+(?:\.\d+)?", synthesis))
        if synthesis and cited and cited <= known and numbers_ok:
            sections["summary"]["llm_rewrite"] = synthesis
            return {**meta.to_dict(), "llm_used": True}
        return {**meta.to_dict(), "llm_used": False,
                "note": "LLM synthesis rejected by citation/number audit — deterministic synthesis shown."}

    @staticmethod
    def _audit(run: Run, sections: dict[str, Any]) -> dict[str, Any]:
        known = set(run.citations) | set(run.calcs)
        links, dropped = [], []
        verified = 0
        for bucket in (sections["summary"]["claims"], sections["bull"], sections["bear"]):
            keep = []
            for c in bucket:
                refs = c["refs"]
                if refs and all(r in known for r in refs):
                    keep.append(c)
                    verified += len(refs)
                    links.extend({"claim_id": c["claim_id"], "ref_id": r,
                                  "ref_kind": "CALC" if r.startswith("CALC") else "SOURCE", "verified": True}
                                 for r in refs)
                else:
                    dropped.append(c["text"])
            bucket[:] = keep
        return {"verified": verified, "dropped": len(dropped), "dropped_claims": dropped, "links": links}


def settings_live_edgar() -> bool:
    return bool(settings.RESEARCH_LIVE_EDGAR)
