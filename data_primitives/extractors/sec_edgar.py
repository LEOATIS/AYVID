"""SEC EDGAR full-text search client for supply chain intelligence extraction.

Uses the EDGAR EFTS full-text search API and filing download endpoints.
No API key required — SEC EDGAR is a free, public data source.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import httpx

from shared.config import settings
from shared.exceptions import LLMProviderError
from shared.logger import get_logger

log = get_logger("data.edgar")

_EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_CIK_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data_store" / "cik_cache.json"

_cik_map: dict[str, str] | None = None


def _get_headers() -> dict[str, str]:
    return {"User-Agent": f"AYVID {settings.SEC_EDGAR_EMAIL}"}


async def _llm_json_call(system: str, user: str) -> str:
    """Thin wrapper so this module keeps its 0.1-temperature contract.

    The SEC edge proposer needs a little more creativity than the default
    strict-zero temperature used elsewhere; we override here rather than
    poking the module-global dispatch.
    """
    from shared.llm_client import json_call

    return await json_call(system, user, max_tokens=1500, temperature=0.1)


def _load_cik_cache() -> dict[str, str]:
    if _CIK_CACHE_PATH.exists():
        try:
            return json.loads(_CIK_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cik_cache(cache: dict[str, str]) -> None:
    try:
        _CIK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CIK_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    except OSError as exc:
        log.warning(f"Could not persist CIK cache: {exc}")


async def _fetch_full_ticker_map() -> dict[str, str]:
    """Download the SEC company_tickers.json and build a ticker->CIK map."""
    global _cik_map
    if _cik_map is not None:
        return _cik_map

    cached = _load_cik_cache()
    if cached:
        _cik_map = cached
        return _cik_map

    async with httpx.AsyncClient(headers=_get_headers(), timeout=30) as client:
        resp = await client.get(_COMPANY_TICKERS_URL)
        resp.raise_for_status()

    data: dict[str, dict[str, Any]] = resp.json()
    _cik_map = {
        entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
        for entry in data.values()
        if "ticker" in entry and "cik_str" in entry
    }
    _save_cik_cache(_cik_map)
    log.info(f"Loaded {len(_cik_map)} ticker->CIK mappings from SEC EDGAR")
    return _cik_map


async def lookup_cik(ticker: str) -> str | None:
    """Resolve a stock ticker to its SEC CIK number."""
    ticker_map = await _fetch_full_ticker_map()
    return ticker_map.get(ticker.upper())


async def fetch_recent_filings(
    ticker: str,
    form_types: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search EDGAR EFTS for recent filings matching a ticker and form type."""
    if form_types is None:
        form_types = ["10-K", "10-Q", "8-K"]

    end_dt = dt.date.today()
    start_dt = end_dt - dt.timedelta(days=365)
    forms_joined = ",".join(form_types)

    params = {
        "q": f'"{ticker}"',
        "forms": forms_joined,
        "dateRange": "custom",
        "startdt": start_dt.isoformat(),
        "enddt": end_dt.isoformat(),
    }

    async with httpx.AsyncClient(headers=_get_headers(), timeout=30) as client:
        resp = await client.get(_EFTS_SEARCH_URL, params=params)
        resp.raise_for_status()

    data = resp.json()
    hits = data.get("hits", {}).get("hits", [])

    filings: list[dict[str, Any]] = []
    for hit in hits[:limit]:
        source = hit.get("_source", {})
        file_num = source.get("file_num", "")
        # EFTS renamed its fields (accession_no→adsh, form_type→form, file_name→_id suffix);
        # read the current names first and keep the legacy ones as fallback.
        hit_id = str(hit.get("_id", ""))
        accession_dashed = source.get("adsh") or source.get("accession_no", "")
        accession = accession_dashed.replace("-", "")
        form_type = source.get("form") or source.get("form_type", "")
        filing_date = source.get("file_date", "")
        display_names = source.get("display_names", [])
        company_name = display_names[0] if display_names else ticker

        cik = await lookup_cik(ticker) or source.get("entity_id", "")
        cik_stripped = cik.lstrip("0") or "0"

        primary_doc = source.get("file_name") or (hit_id.split(":", 1)[1] if ":" in hit_id else "")
        url = ""
        if cik_stripped and accession and primary_doc:
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_stripped}/{accession}/{primary_doc}"
            )

        filings.append(
            {
                "accession": accession_dashed,
                "form_type": form_type,
                "filing_date": filing_date,
                "company_name": company_name,
                "url": url,
                "file_num": file_num,
            }
        )

    log.info(f"Found {len(filings)} EDGAR filings for [bold]{ticker}[/bold] ({forms_joined})")
    return filings


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace using regex only."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"&#?\w+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def download_filing_text(url: str, max_chars: int = 50_000) -> str:
    """Download a filing HTML page and return stripped plain text."""
    if not url:
        return ""

    async with httpx.AsyncClient(
        headers=_get_headers(), timeout=60, follow_redirects=True
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    text = _strip_html(resp.text)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


_ANALYSIS_SYSTEM_PROMPT = """\
You are a quantitative analyst extracting structured supply chain intelligence from SEC filings.
Always respond with valid JSON matching the requested schema exactly.
Focus on supply chain relationships, revenue dependencies, and risk factors."""

_ANALYSIS_USER_TEMPLATE = """\
Analyze this SEC {form_type} filing excerpt for {ticker}:

{filing_text}

Extract the following into a JSON object:
{{
  "supply_chain_dependencies": [
    {{"entity": "company or component", "relationship": "supplier|customer|partner", "criticality": "HIGH|MEDIUM|LOW"}}
  ],
  "revenue_segments": [
    {{"segment": "name", "revenue_pct": approximate percentage or null, "trend": "GROWING|STABLE|DECLINING|UNKNOWN"}}
  ],
  "risk_factors": ["key risk factors related to supply chain, operations, or geopolitics"],
  "key_customers": ["major customers if disclosed"],
  "key_suppliers": ["major suppliers if disclosed"],
  "key_competitors": ["named competitors mentioned in the filing"],
  "key_products": [
    {{"name": "product or service line name", "category": "broad category (semiconductors, software, etc.)"}}
  ],
  "key_facilities": [
    {{"name": "facility name or city", "facility_type": "fab|refinery|warehouse|port|mine|datacenter|unknown",
      "country": "ISO-2 country code if known"}}
  ],
  "geographic_exposure": [
    {{"region": "region name", "revenue_pct": approximate percentage or null}}
  ],
  "competitive_position": "1-2 sentence summary of competitive moat and market position"
}}"""


async def analyze_filing_with_llm(
    ticker: str,
    filing_text: str,
    form_type: str = "10-K",
) -> dict[str, Any]:
    """Send truncated filing text to the configured LLM for structured extraction."""
    if not filing_text:
        return {"error": "empty_filing_text", "ticker": ticker}

    # P1-P: SEC filing bodies are attacker-reachable through the EDGAR
    # submission pipeline. A filer embedding hidden HTML, NFKC tricks,
    # or homoglyph prompt-injection spans gets those bytes delivered
    # into an LLM `json_call`. The sanitiser normalises unicode,
    # strips hidden markup, and redacts script-switched tokens before
    # the filing text goes anywhere near the model.
    from data_primitives.security.text_sanitizer import sanitize_news_text

    clean_text = sanitize_news_text(filing_text[:40_000])
    user_prompt = _ANALYSIS_USER_TEMPLATE.format(
        form_type=form_type,
        ticker=ticker,
        filing_text=clean_text,
    )

    try:
        raw = await _llm_json_call(_ANALYSIS_SYSTEM_PROMPT, user_prompt)
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip())
        raw = re.sub(r"\n?```\s*$", "", raw)
        result: dict[str, Any] = json.loads(raw)
        result["ticker"] = ticker
        result["form_type"] = form_type
        log.info(f"LLM analysis complete for [bold]{ticker}[/bold] {form_type}")
        return result
    except json.JSONDecodeError as exc:
        log.error(f"Failed to parse LLM JSON for {ticker}: {exc}")
        return {"error": "json_parse_failure", "ticker": ticker}
    except Exception as exc:
        raise LLMProviderError(f"EDGAR analysis failed for {ticker}: {exc}") from exc


async def full_edgar_pipeline(ticker: str) -> list[dict[str, Any]]:
    """Fetch recent 10-K and 10-Q filings, download text, and analyze each."""
    filings = await fetch_recent_filings(ticker, form_types=["10-K", "10-Q"], limit=5)
    if not filings:
        log.warning(f"No EDGAR filings found for {ticker}")
        return []

    results: list[dict[str, Any]] = []
    for filing in filings:
        url = filing.get("url", "")
        form_type = filing.get("form_type", "10-K")
        try:
            text = await download_filing_text(url)
            if not text:
                log.warning(f"Empty filing text for {ticker} ({filing.get('accession')})")
                continue
            analysis = await analyze_filing_with_llm(ticker, text, form_type)
            analysis["filing_metadata"] = filing
            results.append(analysis)
        except Exception as exc:
            log.warning(f"Failed to process filing for {ticker}: {exc}")
            continue

    log.info(
        f"EDGAR pipeline complete for [bold]{ticker}[/bold]: "
        f"analyzed {len(results)}/{len(filings)} filings"
    )
    return results
