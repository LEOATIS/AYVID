"""Thesis workflows: create-from-research and the "What changed?" evaluator.

Evaluation rule: a condition whose observation is absent is
``UNKNOWN_MISSING_DATA`` — never ``NOT_TRIGGERED``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.services.research_calc import dec, staleness
from backend.services.research_store import (
    MARKET_PROVIDERS,
    Actor,
    NotFoundError,
    ResearchStore,
    health_of,
    new_id,
    utcnow,
)
from shared.config import settings

EVENT_KIND = "EVENT"


def record_event(store: ResearchStore, actor: Actor, code: str, description: str) -> None:
    store.notify(actor, EVENT_KIND, f"Event recorded: {code}", description, "EVENT", code)


def _event_recorded(store: ResearchStore, actor: Actor, code: str) -> dict[str, Any] | None:
    return store.one(
        "SELECT * FROM notifications WHERE workspace_id = ? AND kind = ? AND ref_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (actor.workspace_id, EVENT_KIND, code),
    )


def evaluate_condition(store: ResearchStore, actor: Actor, cond: dict[str, Any]) -> dict[str, Any]:
    base = {"condition_id": cond["condition_id"], "description": cond["description"],
            "previous_status": cond["current_status"], "observed_value": None, "stale": False,
            "source_id": None}
    if cond["operator"] == "EVENT_OCCURS":
        code = cond["metric_code"].removeprefix("EVENT:")
        hit = _event_recorded(store, actor, cond["metric_code"]) or _event_recorded(store, actor, code)
        if hit:
            return {**base, "status": "TRIGGERED",
                    "explanation": f"Event recorded at {hit['created_at']}: {hit['body']}"}
        return {**base, "status": "UNKNOWN_MISSING_DATA",
                "explanation": "No live event feed is connected and no matching event has been recorded. "
                               "AYVID cannot confirm the event did not occur, so this is UNKNOWN, not 'not triggered'."}

    obs_all = store.observations(cond["instrument_id"], cond["metric_code"])
    if not obs_all:
        return {**base, "status": "UNKNOWN_MISSING_DATA",
                "explanation": f"No {cond['metric_code']} observation exists for {cond['instrument_id']} in AYVID's "
                               "sources (not disclosed or not ingested). Cannot evaluate — treated as UNKNOWN, never 0."}
    latest = obs_all[-1]
    threshold_days = settings.RESEARCH_STALE_DAYS if latest["provider"] in MARKET_PROVIDERS else 200
    fresh = staleness(latest["as_of"], threshold_days)
    value = dec(latest["value"])
    stale_note = f" Data is STALE (as of {fresh['as_of']})." if fresh["is_stale"] else ""
    out = {**base, "observed_value": str(value), "stale": fresh["is_stale"], "source_id": latest["source_id"],
           "observation_id": latest["observation_id"], "as_of": latest["as_of"]}

    if cond["threshold_value"] is None:
        return {**out, "status": "UNKNOWN_MISSING_DATA", "explanation": "Condition has no threshold."}
    if cond["operator"] == "DROP_PCT":
        if len(obs_all) < 2:
            return {**out, "status": "UNKNOWN_MISSING_DATA",
                    "explanation": "DROP_PCT needs two observations of the same metric; only one exists."}
        prior = dec(obs_all[-2]["value"])
        if prior == 0:
            return {**out, "status": "UNKNOWN_MISSING_DATA", "explanation": "Prior observation is zero."}
        drop = (prior - value) / prior * Decimal(100)
        hit = drop >= dec(cond["threshold_value"])
        return {**out, "status": "TRIGGERED" if hit else "NOT_TRIGGERED",
                "explanation": f"{latest['fiscal_period']} vs {obs_all[-2]['fiscal_period']}: change "
                               f"{-drop:.2f}% (threshold −{cond['threshold_value']}%).{stale_note}"}

    threshold = dec(cond["threshold_value"])
    hit = value < threshold if cond["operator"] == "<" else value > threshold
    return {**out, "status": "TRIGGERED" if hit else "NOT_TRIGGERED",
            "explanation": f"{latest['metric_label']} {latest['fiscal_period']} = {value} "
                           f"{cond['operator']} {threshold}? {'Yes' if hit else 'No'}.{stale_note}"}


def evaluate_thesis(store: ResearchStore, actor: Actor, thesis_id: str) -> dict[str, Any]:
    thesis = store.get_thesis(actor, thesis_id)
    results = [evaluate_condition(store, actor, c) for c in thesis["conditions"]]
    store.write_condition_results(thesis_id, results)

    since = thesis.get("last_reviewed_at") or thesis["created_at"]
    linked = {e["source_id"] for e in thesis["supporting_evidence"] + thesis["contradicting_evidence"]}
    new_sources = []
    for iid in thesis["instrument_ids"]:
        for s in store.sources_for(iid):
            if s["source_id"] not in linked and (s.get("retrieved_at") or "") > since:
                new_sources.append({k: s[k] for k in ("source_id", "title", "provider", "doc_type", "published_at")})

    changed = [r for r in results if r["status"] != r["previous_status"]]
    for r in results:
        if r["status"] == "TRIGGERED" and r["previous_status"] != "TRIGGERED":
            store.notify(actor, "CONDITION_TRIGGERED", f"Triggered: {r['description']}", r["explanation"],
                         "THESIS", thesis_id)
    statuses = [r["status"] for r in results]
    summary = {
        "thesis_id": thesis_id, "evaluated_at": utcnow(), "health": health_of(statuses),
        "results": results, "changed": changed, "new_evidence": new_sources[:20],
        "counts": {s: statuses.count(s) for s in ("TRIGGERED", "NOT_TRIGGERED", "UNKNOWN_MISSING_DATA")},
        "proposed_revision_note": _proposal(results, changed),
    }
    store.record_monitoring_run(actor, "THESIS", thesis_id, {"health": summary["health"], "counts": summary["counts"]})
    return summary


def _proposal(results: list[dict[str, Any]], changed: list[dict[str, Any]]) -> str:
    trig = [r["description"] for r in results if r["status"] == "TRIGGERED"]
    unk = sum(1 for r in results if r["status"] == "UNKNOWN_MISSING_DATA")
    parts = [f"Evaluated {len(results)} conditions"]
    if trig:
        parts.append("TRIGGERED: " + "; ".join(trig))
    if unk:
        parts.append(f"{unk} unknown (missing data)")
    if changed:
        parts.append(f"{len(changed)} status change(s)")
    return ". ".join(parts) + "."


def thesis_from_session(store: ResearchStore, actor: Actor, session_id: str, title: str | None) -> dict[str, Any]:
    session = store.get_session(actor, session_id)
    art = session["artifact"]
    if not art or art.get("status") != "COMPLETE":
        raise NotFoundError("research session has no completed answer to save")
    label_to_source = {label: c["source_id"] for label, c in art["citations"].items()}
    # A calculation's evidence is the source document its inputs came from.
    for label, calc in art.get("calculations", {}).items():
        src = art["citations"].get(calc.get("source_label") or "")
        if src:
            label_to_source[label] = src["source_id"]
    evidence, seen = [], set()
    for stance, bucket in (("SUPPORTING", art["bull"]), ("CONTRADICTING", art["bear"])):
        for claim in bucket:
            for ref in claim["refs"]:
                sid = label_to_source.get(ref)
                if sid and (sid, stance) not in seen:
                    seen.add((sid, stance))
                    evidence.append({"source_id": sid, "stance": stance, "note": claim["text"]})
    for claim in art["summary"]["claims"][:3]:
        for ref in claim["refs"]:
            sid = label_to_source.get(ref)
            if sid and (sid, "SUPPORTING") not in seen and (sid, "CONTRADICTING") not in seen:
                seen.add((sid, "SUPPORTING"))
                evidence.append({"source_id": sid, "stance": "SUPPORTING", "note": claim["text"]})
    instruments = [i["instrument_id"] for i in art["resolution"]["resolved"]]
    data = {
        "title": title or art["title"],
        "instrument_ids": instruments,
        "summary": art["summary"]["llm_rewrite"] or art["summary"]["headline"],
        "horizon": "12-24 months",
        "conviction_label": "MODERATE_EVIDENCE" if len(evidence) >= 4 else "EXPLORATORY",
        "assumptions": [c["text"] for c in art["summary"]["claims"] if c["epistemic"] in ("INTERPRETATION", "HYPOTHESIS")],
        "catalysts": [],
        "open_questions": [u["text"] for u in art["uncertainty"]],
        "evidence": evidence,
        "conditions": art["monitor"],
        "session_id": session_id,
    }
    thesis = store.create_thesis(actor, data)
    evaluate_thesis(store, actor, thesis["thesis_id"])
    return store.get_thesis(actor, thesis["thesis_id"])


def attach_session(store: ResearchStore, actor: Actor, thesis_id: str, session_id: str) -> dict[str, Any]:
    thesis = store.get_thesis(actor, thesis_id)
    session = store.get_session(actor, session_id)
    art = session["artifact"] or {}
    linked = {e["source_id"] for e in thesis["supporting_evidence"] + thesis["contradicting_evidence"]}
    with store.connect() as conn:
        for label, c in (art.get("citations") or {}).items():
            if c["source_id"] in linked:
                continue
            stance = "CONTRADICTING" if any(label in b["refs"] for b in art.get("bear", [])) else "SUPPORTING"
            conn.execute(
                "INSERT INTO thesis_evidence VALUES (?,?,?,?,?,?)",
                (new_id("ev"), thesis_id, c["source_id"], stance, f"From research: {art.get('title')}", utcnow()),
            )
    sessions = list(dict.fromkeys([*thesis["linked_session_ids"], session_id]))
    return store.update_thesis(actor, thesis_id, {"linked_session_ids": sessions}, f"Attached research {session_id}")
