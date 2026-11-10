"""Query safety screening, intent classification and instrument resolution.

All deterministic — runs before (and independently of) any LLM so a refusal
or an ambiguity can never be talked around by the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.services import research_fixtures as fx
from backend.services.research_store import ResearchStore
from data_primitives.security.text_sanitizer import sanitize_news_text

# ── Safety ────────────────────────────────────────────────────────────

_INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|your)\s+"
    r"(instructions|rules|prompts?)|system\s+prompt|you\s+are\s+now|developer\s+mode|"
    r"jailbreak|reveal\s+(your|the)\s+(prompt|instructions)|act\s+as\s+(an?\s+)?unrestricted",
    re.I,
)
_TRADE_EXECUTION = re.compile(
    r"\b(execute|place|submit|route|send|fill)\b.{0,40}\b(order|trade|buy|sell|short)\b|"
    r"\b(buy|sell|short)\s+\d[\d,]*\s*(shares|contracts|options|calls|puts|lots)?\b|"
    r"\blive\s+(market\s+)?(buy|sell|order|trade)\b|\bmarket\s+(buy|sell)\b",
    re.I,
)
_GUARANTEE = re.compile(
    r"\bguarantee[ds]?\b|\b(will|going\s+to)\s+(double|triple|10x|moon)\b|\brisk[- ]free\s+(profit|return)\b|"
    r"\bsure\s+(thing|bet)\b|\bwhich\s+(penny\s+)?stock\s+will\b",
    re.I,
)
_ADVICE = re.compile(r"\bshould\s+i\s+(buy|sell|short)\b|\b(buy|sell)\s+signal\b", re.I)

REFUSALS: dict[str, str] = {
    "PROMPT_INJECTION": (
        "This request contains instructions aimed at overriding AYVID's research rules. "
        "AYVID ignores embedded instructions and only answers research questions grounded "
        "in retrieved sources."
    ),
    "TRADE_EXECUTION": (
        "AYVID is a research workspace. It does not place, route or simulate real-money "
        "orders. Ask about the company, its filings or its exposures instead — for example "
        "\"What are NVDA's key invalidation risks?\""
    ),
    "RETURN_GUARANTEE": (
        "No source can guarantee a return, and AYVID will not predict which security will "
        "rise. It can show what the evidence says, what contradicts it and what to monitor."
    ),
    "PERSONAL_ADVICE": (
        "AYVID does not issue buy or sell recommendations. It can lay out the supporting and "
        "contradicting evidence, the deterministic metrics and the conditions that would "
        "invalidate a thesis, so you can make the decision."
    ),
    "NO_INSTRUMENT": (
        "AYVID could not find a supported instrument in this question. Name a ticker or "
        "company from the local universe (e.g. NVDA, TSMC, ASML, INFY)."
    ),
}


@dataclass(frozen=True)
class SafetyVerdict:
    clean_query: str
    refusal_code: str | None
    sanitizer: dict[str, Any]


def screen_query(raw: str) -> SafetyVerdict:
    sanitized = sanitize_news_text(raw, strict_ticker_guard=True, max_chars=1000)
    meta = {
        "dropped_html_blocks": sanitized.dropped_html_blocks,
        "rejected_tokens": len(sanitized.rejected_tokens),
        "bidi_controls_removed": sanitized.bidi_controls_removed,
        "changed": sanitized.sha256_changed,
    }
    text = sanitized.clean
    code = None
    if _INJECTION.search(raw) or sanitized.dropped_html_blocks or sanitized.rejected_tokens:
        code = "PROMPT_INJECTION"
    elif _TRADE_EXECUTION.search(text):
        code = "TRADE_EXECUTION"
    elif _GUARANTEE.search(text):
        code = "RETURN_GUARANTEE"
    elif _ADVICE.search(text):
        code = "PERSONAL_ADVICE"
    return SafetyVerdict(clean_query=text, refusal_code=code, sanitizer=meta)


# ── Intent ────────────────────────────────────────────────────────────

_EVENT = re.compile(
    r"disrupt|shock|outage|earthquake|blockade|propagat|embargo|sanction|tariff|export\s+ban|"
    r"war\b|invasion|fire\b|flood|strike\b|shutdown",
    re.I,
)
_COMPARE = re.compile(r"\bcompare\b|\bvs\.?\b|\bversus\b|\brelative\s+to\b", re.I)
_EARNINGS = re.compile(r"earnings|quarter|guidance|margin|eps\b|results", re.I)
_VALUATION = re.compile(r"\bdcf\b|valuation|intrinsic|fair\s+value|multiple|terminal", re.I)


def classify_intent(query: str, n_instruments: int) -> str:
    if _EVENT.search(query):
        return "EVENT_IMPACT"
    if _COMPARE.search(query) and n_instruments >= 2:
        return "FINANCIAL_COMPARISON"
    if _VALUATION.search(query):
        return "VALUATION_SCENARIO"
    if _EARNINGS.search(query):
        return "EARNINGS_ANALYSIS"
    return "COMPANY_RESEARCH"


# ── Resolution ────────────────────────────────────────────────────────

# Uppercase tokens that are finance vocabulary, not tickers.
_NOT_TICKERS = {
    "A", "I", "AI", "US", "USA", "UK", "EU", "CEO", "CFO", "GDP", "EPS", "FCF", "DCF", "TTM",
    "FY", "Q", "VS", "AND", "OR", "THE", "ETF", "IPO", "EUV", "DUV", "GAAP", "SEC", "YOY",
    "QOQ", "GPU", "CPU", "HBM", "AND", "OF", "IN", "TO", "IS", "IT", "IFRS", "NT", "EBIT",
    "EBITDA", "ROE", "ROIC", "WACC", "PE", "PS", "EV", "TAM", "R", "D", "M", "B", "K", "H20",
    "API", "LLM", "FAB", "NSE", "BSE", "NYSE", "NASDAQ", "HOW", "WHAT", "WHY",
}
# Real tickers that are also common English words — need explicit `$TICKER` or a name.
_WORD_TICKERS = {"ON", "ALL", "NOW", "NET", "MA", "V", "F", "GE", "HD", "DE", "BA", "IT"}
_TOKEN = re.compile(r"\$?\b[A-Z0-9]{1,6}(?:\.[A-Z]{1,2})?\b")


@dataclass
class Resolution:
    resolved: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": [_brief(i) for i in self.resolved],
            "ambiguous": self.ambiguous,
            "unsupported": self.unsupported,
            "notes": self.notes,
        }


def _brief(inst: dict[str, Any]) -> dict[str, Any]:
    keys = ("instrument_id", "symbol", "local_symbol", "name", "exchange", "currency", "country",
            "sector", "industry", "data_tier", "last_bar")
    return {k: inst.get(k) for k in keys}


def _lookup(store: ResearchStore, symbol: str) -> list[dict[str, Any]]:
    return store.instruments_by_symbol(symbol)


def _choose(symbol: str, candidates: list[dict[str, Any]], res: Resolution, *, explicit: bool) -> None:
    with_data = [c for c in candidates if c["data_tier"] != "REFERENCE_ONLY"]
    if len(candidates) == 1:
        res.resolved.append(candidates[0])
        return
    if len(with_data) == 1:
        chosen = with_data[0]
        others = ", ".join(c["instrument_id"] for c in candidates if c is not chosen)
        res.resolved.append(chosen)
        res.notes.append(
            f"{symbol} matched {len(candidates)} listings; resolved to {chosen['instrument_id']} "
            f"(the only listing with local data). Also listed: {others}."
        )
        return
    us = [c for c in with_data if c["country"] == "US"]
    if explicit and len(us) == 1:
        res.resolved.append(us[0])
        others = ", ".join(c["instrument_id"] for c in with_data if c is not us[0])
        res.notes.append(f"{symbol} resolved to US listing {us[0]['instrument_id']}; alternative listing(s): {others}.")
        return
    res.ambiguous.append({
        "token": symbol,
        "reason": "Multiple listings with data",
        "candidates": [_brief(c) for c in with_data],
    })


def resolve_query(store: ResearchStore, query: str) -> Resolution:
    res = Resolution()
    seen: set[str] = set()
    lowered = query.lower()

    for alias, symbol in sorted(fx.NAME_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(alias)}\b", lowered) and symbol not in seen:
            seen.add(symbol)
            _choose(symbol, _lookup(store, symbol), res, explicit=True)

    for match in _TOKEN.finditer(query):
        raw = match.group(0)
        explicit = raw.startswith("$")
        token = raw.lstrip("$")
        if token in seen or token.lower() in fx.NAME_ALIASES:
            continue
        if (token in _NOT_TICKERS and not explicit) or (token.isdigit() and "." not in token):
            continue
        if token in _WORD_TICKERS and not explicit:
            if _lookup(store, token):
                res.ambiguous.append({
                    "token": token,
                    "reason": f"'{token}' is a common word and also a ticker",
                    "candidates": [_brief(c) for c in _lookup(store, token)],
                })
            seen.add(token)
            continue
        seen.add(token)
        candidates = _lookup(store, token)
        if not candidates:
            if 2 <= len(token) <= 5 and token.isalpha():
                res.unsupported.append(token)
            continue
        _choose(token, candidates, res, explicit=True)

    # De-duplicate (alias + ticker can resolve to the same instrument), then order by
    # first mention so the subject of the question is the primary instrument.
    unique: dict[str, dict[str, Any]] = {}
    for inst in res.resolved:
        unique.setdefault(inst["instrument_id"], inst)
    res.resolved = sorted(unique.values(), key=lambda i: _first_mention(query, lowered, i))
    return res


def _first_mention(query: str, lowered: str, inst: dict[str, Any]) -> int:
    """Index of the earliest ticker or alias mention of ``inst`` in the query."""
    symbols = {inst["symbol"], inst.get("local_symbol") or inst["symbol"]}
    hits = [m.start() for s in symbols for m in re.finditer(rf"\$?\b{re.escape(s)}\b", query)]
    hits += [m.start() for alias, sym in fx.NAME_ALIASES.items() if sym in symbols
             for m in re.finditer(rf"\b{re.escape(alias)}\b", lowered)]
    return min(hits) if hits else len(query)


def resolve_symbol(store: ResearchStore, q: str) -> dict[str, Any]:
    """``GET /research/resolve`` — single-token resolution with explicit status."""
    token = q.strip().lstrip("$").upper()
    if not token:
        return {"query": q, "status": "UNSUPPORTED", "candidates": [], "note": "Empty query."}
    alias = fx.NAME_ALIASES.get(q.strip().lower())
    candidates = _lookup(store, alias or token)
    if not candidates:
        return {"query": q, "status": "UNSUPPORTED", "candidates": [],
                "note": f"'{q}' is not in the AYVID instrument universe."}
    briefs = [_brief(c) for c in candidates]
    if token in _WORD_TICKERS and not q.strip().startswith("$") and not alias:
        return {"query": q, "status": "AMBIGUOUS", "candidates": briefs,
                "note": f"'{token}' is also a common English word. Use ${token} to mean the ticker."}
    if len(candidates) > 1:
        return {"query": q, "status": "AMBIGUOUS", "candidates": briefs,
                "note": f"{token} is listed on {len(candidates)} venues. Pick one."}
    return {"query": q, "status": "RESOLVED", "candidates": briefs, "note": None}
