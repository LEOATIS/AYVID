"""Natural-language → typed filter plan → deterministic screen over the local universe.

The parser is rule-based on purpose: the plan it produces is shown to the
user, editable, and executed without any model in the loop.
"""

from __future__ import annotations

import re
from typing import Any

from backend.services.research_calc import staleness
from backend.services.research_store import ResearchStore
from shared.config import settings

METRIC_FIELDS = {"RETURN_90D_PCT", "REALIZED_VOL_ANN", "MAX_DRAWDOWN_PCT", "HURST_EXPONENT",
                 "CLOSE_PRICE", "GROSS_MARGIN_PCT", "REVENUE_GROWTH_PCT", "NET_MARGIN_PCT"}
ATTRIBUTE_FIELDS = {"sector", "industry", "country", "exchange"}

_SECTOR_WORDS = {
    r"semiconductor equipment|semi equipment|chip equipment": ["Semiconductor Equipment"],
    r"semiconductor|semis?\b|chip": ["Semiconductors", "Semiconductor Equipment"],
    r"software|saas": ["Software"],
    r"it services": ["IT Services"],
    r"bank": ["Banks"],
    r"internet": ["Internet", "Internet Retail", "Internet Retail & Cloud"],
}
_COUNTRY_WORDS = {r"\bindia|indian\b": "IN", r"\bus\b|american|u\.s\.": "US", r"taiwan": "TW", r"japan": "JP"}
_NUM = r"(-?\d+(?:\.\d+)?)\s*%?"


def parse_plan(query: str) -> dict[str, Any]:
    q = query.lower()
    filters: list[dict[str, Any]] = []
    unparsed: list[str] = []

    industries: list[str] = []
    for pattern, values in _SECTOR_WORDS.items():
        if re.search(pattern, q):
            industries.extend(v for v in values if v not in industries)
            q_marked = re.sub(pattern, " ", q)
            q = q_marked
    if industries:
        filters.append({"field": "industry", "op": "in", "value": industries})
    for pattern, code in _COUNTRY_WORDS.items():
        if re.search(pattern, query.lower()):
            filters.append({"field": "country", "op": "==", "value": code})
            break

    if re.search(r"positive\s+(90[- ]day\s+)?(momentum|return)", q):
        filters.append({"field": "RETURN_90D_PCT", "op": ">", "value": 0.0})
    elif re.search(r"negative\s+(90[- ]day\s+)?(momentum|return)", q):
        filters.append({"field": "RETURN_90D_PCT", "op": "<", "value": 0.0})
    for word, op in (("below|under|less than|<", "<"), ("above|over|greater than|>", ">")):
        m = re.search(rf"(?:volatility|vol)\s+(?:of\s+)?(?:{word})\s+{_NUM}", q)
        if m:
            filters.append({"field": "REALIZED_VOL_ANN", "op": op, "value": float(m.group(1))})
        m = re.search(rf"gross margin\s+(?:{word})\s+{_NUM}", q)
        if m:
            filters.append({"field": "GROSS_MARGIN_PCT", "op": op, "value": float(m.group(1))})
        m = re.search(rf"drawdown\s+(?:{word})\s+{_NUM}", q)
        if m:
            filters.append({"field": "MAX_DRAWDOWN_PCT", "op": ">" if op == "<" else "<",
                            "value": -abs(float(m.group(1)))})
    m = re.search(r"(?:supply[- ]chain\s+)?(?:links?|linked|exposure)\s+to\s+\$?([a-z0-9.]{1,10})", q)
    if m:
        filters.append({"field": "supply_chain", "op": "linked_to", "value": m.group(1).upper()})
    if query.strip() and not filters:
        unparsed.append(query.strip())
    return {"query": query, "filters": filters, "unparsed": unparsed}


def _passes(value: Any, op: str, target: Any) -> bool:
    if op == "in":
        return value in target
    if op == "==":
        return value == target
    v, t = float(value), float(target)
    return {"<": v < t, ">": v > t, "<=": v <= t, ">=": v >= t}[op]


def run_screen(store: ResearchStore, filters: list[dict[str, Any]]) -> dict[str, Any]:
    universe = [i for i in store.list_instruments() if i["data_tier"] != "REFERENCE_ONLY"]
    metric_codes = sorted({f["field"] for f in filters if f["field"] in METRIC_FIELDS} |
                          {"RETURN_90D_PCT", "REALIZED_VOL_ANN", "CLOSE_PRICE"})
    marks = ",".join("?" for _ in metric_codes)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for o in store.query(
        f"SELECT * FROM financial_observations WHERE metric_code IN ({marks}) ORDER BY period_end",
        tuple(metric_codes),
    ):
        latest[(o["instrument_id"], o["metric_code"])] = o

    linked: dict[str, set[str]] = {}
    for f in filters:
        if f["op"] == "linked_to":
            target = str(f["value"])
            syms = set()
            for e in store.edges_touching([target]):
                syms.add(e["src_symbol"] if e["dst_symbol"] == target else e["dst_symbol"])
            linked[target] = syms

    matches, missing = [], []
    for inst in universe:
        iid = inst["instrument_id"]
        ok, missing_fields = True, []
        for f in filters:
            fld = f["field"]
            if fld in ATTRIBUTE_FIELDS:
                value = inst.get(fld)
                if value is None:
                    missing_fields.append(fld)
                    ok = False
                elif not _passes(value, f["op"], f["value"]):
                    ok = False
            elif f["op"] == "linked_to":
                if (inst["local_symbol"] or inst["symbol"]) not in linked.get(str(f["value"]), set()):
                    ok = False
            elif fld in METRIC_FIELDS:
                obs = latest.get((iid, fld))
                if obs is None:
                    missing_fields.append(fld)
                    ok = False
                elif not _passes(obs["value"], f["op"], f["value"]):
                    ok = False
            if not ok and not missing_fields:
                break
        if missing_fields:
            missing.append({"instrument_id": iid, "symbol": inst["symbol"], "missing": missing_fields})
            continue
        if ok:
            def val(code: str) -> str | None:
                o = latest.get((iid, code))
                return o["value"] if o else None
            matches.append({
                "instrument_id": iid, "symbol": inst["symbol"], "name": inst["name"],
                "industry": inst["industry"], "country": inst["country"], "last_bar": inst["last_bar"],
                "is_stale": staleness(inst["last_bar"], settings.RESEARCH_STALE_DAYS)["is_stale"],
                "metrics": {c: val(c) for c in metric_codes},
            })
    matches.sort(key=lambda r: -float(r["metrics"].get("RETURN_90D_PCT") or -1e9))
    bars = [i["last_bar"] for i in universe if i["last_bar"]]
    firsts = [i["first_bar"] for i in universe if i["first_bar"]]
    return {
        "filters": filters,
        "matches": matches,
        "universe": {
            "size": len(universe),
            "description": "AYVID local universe: instruments with data_store/ohlcv files (US, India, Asia, Europe, ETFs).",
            "date_range": {"first_bar": min(firsts) if firsts else None, "last_bar": max(bars) if bars else None},
            "stale_instruments": sum(1 for b in bars if staleness(b, settings.RESEARCH_STALE_DAYS)["is_stale"]),
        },
        "missing_data": {
            "rule": "Instruments lacking any filtered field are EXCLUDED (never treated as passing or as 0).",
            "excluded_count": len(missing),
            "excluded": missing[:50],
        },
    }
