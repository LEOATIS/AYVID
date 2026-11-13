"""SEC filing & earnings transcript analysis via GPT-4o.

Extracts supply chain intelligence, risk factors, revenue segments,
and management guidance from 10-K/10-Q/8-K filings and earnings calls.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from shared.exceptions import LLMProviderError, OpenBBFetchError
from shared.logger import get_logger

log = get_logger("data.filings")


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON output from LLMs."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


async def _chat_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Send a prompt pair to the shared LLM dispatch and parse the JSON
    response. Keeps this module's temperature=0.1 contract (filings need
    a touch more creativity than the default zero-temperature path)."""
    from shared.llm_client import json_call

    try:
        raw = await json_call(
            system_prompt,
            user_prompt,
            max_tokens=1500,
            temperature=0.1,
            model="deep",  # long 10-K reading is offline and accuracy-bound: Gemini 4 Carbon
        )
        return json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError as exc:
        log.error(f"Failed to parse LLM JSON: {exc}")
        return {"error": "json_parse_failure"}
    except Exception as exc:
        raise LLMProviderError(f"Filing analysis LLM call failed: {exc}") from exc


# ── SEC Filing Analysis ───────────────────────────────────────

_SEC_SYSTEM_PROMPT = """\
You are a quantitative analyst specializing in SEC filing analysis for supply chain intelligence.
Extract structured data from the filing metadata provided.
Always respond with valid JSON matching the requested schema exactly."""

_SEC_USER_TEMPLATE = """\
Analyze this SEC {filing_type} filing data for {symbol}:

Filing metadata:
{filing_data}

Extract the following into a JSON object:
{{
  "symbol": "{symbol}",
  "filing_type": "{filing_type}",
  "risk_factors": ["list of key risk factors mentioning supply chain, geopolitical, or operational risks"],
  "supply_chain_dependencies": [
    {{"supplier": "company name or ticker", "component": "what they supply", "criticality": "HIGH|MEDIUM|LOW"}}
  ],
  "revenue_segments": [
    {{"segment": "segment name", "revenue_pct": approximate percentage, "growth_trend": "GROWING|STABLE|DECLINING"}}
  ],
  "material_changes": ["list of material changes from prior period"],
  "geographic_exposure": [{{"region": "region name", "revenue_pct": approximate percentage}}],
  "key_customers": ["list of major customers if disclosed"],
  "capex_signals": ["notable capital expenditure plans or changes"],
  "summary": "2-3 sentence executive summary focused on supply chain positioning"
}}"""


async def analyze_sec_filing(
    symbol: str,
    filing_type: str = "10-K",
) -> dict[str, Any]:
    """Fetch SEC filing metadata and extract supply chain intelligence via GPT-4o."""
    from data_primitives.extractors.openbb_client import fetch_sec_filings

    try:
        filings = await fetch_sec_filings(symbol, limit=10)
    except OpenBBFetchError:
        log.warning(f"No SEC filings available for {symbol}")
        return {"symbol": symbol, "filing_type": filing_type, "error": "no_filings"}

    relevant = [f for f in filings if f.get("type", "") == filing_type]
    if not relevant:
        relevant = filings[:3]

    filing_data = json.dumps(relevant[:3], indent=2, default=str)
    user_prompt = _SEC_USER_TEMPLATE.format(
        filing_type=filing_type,
        symbol=symbol,
        filing_data=filing_data,
    )

    result = await _chat_json(_SEC_SYSTEM_PROMPT, user_prompt)
    log.info(f"Analyzed {filing_type} filing for [bold]{symbol}[/bold]")
    return result


# ── Earnings Transcript Analysis ──────────────────────────────

_EARNINGS_SYSTEM_PROMPT = """\
You are a quantitative analyst specializing in earnings call analysis.
Synthesize the provided news and earnings-related information into structured intelligence.
Always respond with valid JSON matching the requested schema exactly."""

_EARNINGS_USER_TEMPLATE = """\
Analyze recent earnings-related news and commentary for {symbol}:

News articles:
{news_data}

Extract the following into a JSON object:
{{
  "symbol": "{symbol}",
  "revenue_assessment": {{"beat_or_miss": "BEAT|MISS|INLINE|UNKNOWN", "magnitude": "description of deviation"}},
  "guidance_changes": {{"direction": "RAISED|LOWERED|MAINTAINED|UNKNOWN", "details": "specifics"}},
  "supply_chain_mentions": ["direct quotes or paraphrases about supply chain from earnings"],
  "key_management_quotes": ["important forward-looking statements from management"],
  "sentiment": {{"overall": float between -1.0 and 1.0, "rationale": "explanation"}},
  "peer_comparison": ["any mentions of competitive positioning"],
  "risk_flags": ["any new risks or concerns raised"],
  "catalyst_timeline": ["upcoming events or dates that could move the stock"],
  "summary": "2-3 sentence synthesis focused on supply chain implications"
}}"""


async def analyze_earnings_transcript(symbol: str) -> dict[str, Any]:
    """Fetch earnings-related news and synthesize via GPT-4o."""
    from data_primitives.extractors.openbb_client import fetch_company_news

    try:
        news_items = await fetch_company_news(symbol, limit=20)
    except OpenBBFetchError:
        log.warning(f"No news available for {symbol}")
        return {"symbol": symbol, "error": "no_news"}

    earnings_keywords = {"earnings", "revenue", "profit", "guidance", "eps", "quarterly"}
    earnings_news = [
        n for n in news_items if any(kw in n.title.lower() for kw in earnings_keywords)
    ]
    if not earnings_news:
        earnings_news = news_items[:5]

    news_data = json.dumps(
        [{"title": n.title, "text": n.text, "date": str(n.timestamp)} for n in earnings_news[:10]],
        indent=2,
    )
    user_prompt = _EARNINGS_USER_TEMPLATE.format(
        symbol=symbol,
        news_data=news_data,
    )

    result = await _chat_json(_EARNINGS_SYSTEM_PROMPT, user_prompt)
    log.info(f"Analyzed earnings for [bold]{symbol}[/bold]")
    return result


# ── Comprehensive Fundamental Profile ─────────────────────────


async def build_fundamental_profile(symbol: str) -> dict[str, Any]:
    """Combine SEC filing analysis, earnings synthesis, and financial ratios
    into a comprehensive fundamental profile for the LLM agents.
    """
    from data_primitives.extractors.openbb_client import fetch_financial_ratios

    filing_task = asyncio.create_task(analyze_sec_filing(symbol))
    earnings_task = asyncio.create_task(analyze_earnings_transcript(symbol))
    ratios_task = asyncio.create_task(fetch_financial_ratios(symbol))

    filing_analysis, earnings_analysis, ratios = await asyncio.gather(
        filing_task,
        earnings_task,
        ratios_task,
        return_exceptions=True,
    )

    def _safe_result(result: Any) -> dict[str, Any]:
        if isinstance(result, Exception):
            return {"error": str(result)}
        return result if isinstance(result, dict) else {}

    profile: dict[str, Any] = {
        "symbol": symbol,
        "filing_analysis": _safe_result(filing_analysis),
        "earnings_analysis": _safe_result(earnings_analysis),
        "financial_ratios": _safe_result(ratios),
    }

    supply_deps = profile["filing_analysis"].get("supply_chain_dependencies", [])
    sc_mentions = profile["earnings_analysis"].get("supply_chain_mentions", [])
    profile["supply_chain_summary"] = {
        "dependency_count": len(supply_deps),
        "dependencies": supply_deps,
        "recent_mentions": sc_mentions,
    }

    log.info(f"Built fundamental profile for [bold]{symbol}[/bold]")
    return profile
