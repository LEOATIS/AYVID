"""Named Entity Recognition pipeline — extracts structured entities from news
and maps them into the Neo4j supply chain graph.

Also hosts the **autonomous shock detector** used by the LangGraph
pipeline and the backtest engine to replace the hardcoded
`SHOCK_SCENARIOS` dictionary. See `detect_live_shock()`.

Supports both OpenAI and Anthropic backends (selected via LLM_PROVIDER config).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from typing import Any

from data_primitives.security import (
    is_safe,
    safe_for_ticker_extraction,
    sanitize_news_text,
)
from shared.exceptions import LLMProviderError
from shared.logger import get_logger
from shared.types import CausalEvent, EventType, NewsItem

log = get_logger("data.ner")

from shared.llm_client import json_call as _llm_json_call  # noqa: E402

_sequence_counter = itertools.count(start=1)
_DEFAULT_LLM_CONCURRENCY = 25


def _next_seq() -> int:
    return next(_sequence_counter)


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON output from LLMs."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _parse_llm_json(raw: str) -> Any:
    """Safely parse JSON from LLM output, handling common formatting issues."""
    try:
        return json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        log.warning("Failed to parse LLM JSON output, returning empty result")
        return None


# ── Entity Extraction ─────────────────────────────────────────

_NER_SYSTEM_PROMPT = """\
You are a financial Named Entity Recognition system. Extract all relevant entities \
from the provided text. Focus on entities that matter for supply chain and market analysis.
Always respond with valid JSON matching the requested schema exactly."""

_NER_USER_TEMPLATE = """\
Extract all named entities from this financial news text:

{text_json}

Return a JSON object:
{{
  "entities": [
    {{
      "entity": "entity name",
      "type": "COMPANY|PRODUCT|FACILITY|PERSON|LOCATION",
      "ticker": "stock ticker if company, null otherwise",
      "confidence": float between 0.0 and 1.0
    }}
  ]
}}

Rules:
- For companies, always try to resolve the stock ticker (e.g., Apple -> AAPL)
- Include subsidiaries as separate COMPANY entities
- Facilities include factories, warehouses, ports, data centers
- Products include specific product lines, components, raw materials
- Be precise with confidence scores: 0.9+ for explicit mentions, 0.5-0.8 for inferred"""


async def extract_entities(text: str) -> list[dict[str, Any]]:
    """Extract structured entities from text using the configured LLM provider.

    Hostile input defence: the Adversarial-News sanitiser (Phase 1) runs
    as a mandatory pre-LLM pass. Homoglyph-poisoned ticker tokens and
    hidden-HTML sentiment-flip injections are stripped before the
    entity-extraction model ever sees them. If the sanitiser flags the
    input as unsafe for ticker extraction, we refuse to call the LLM
    and return an empty entity list — the caller's downstream gates
    treat this as a no-signal observation rather than trust a
    redacted payload.
    """
    sanitised = sanitize_news_text(text)
    if not safe_for_ticker_extraction(sanitised):
        log.warning(
            f"[SEC] Ticker-extraction refused: "
            f"rejected_tokens={sanitised.rejected_tokens}, "
            f"dropped_html_blocks={sanitised.dropped_html_blocks}, "
            f"raw_sha={sanitised.raw_sha256}"
        )
        return []

    try:
        # JSON-escape the user content. `json.dumps` yields a quoted
        # JSON string literal — embedded `"`, `}`, `{`, backslashes, and
        # newlines are all escaped, so an attacker cannot break out of
        # the prompt's quoted-string context to inject control directives.
        text_json = json.dumps(sanitised.clean, ensure_ascii=False)
        raw = await _llm_json_call(
            _NER_SYSTEM_PROMPT,
            _NER_USER_TEMPLATE.format(text_json=text_json),
        )
        parsed = _parse_llm_json(raw)
        if parsed is None:
            return []
        entities = parsed.get("entities", [])
        log.debug(f"Extracted {len(entities)} entities from text")
        return entities
    except Exception as exc:
        raise LLMProviderError(f"Entity extraction failed: {exc}") from exc


# ── Event Classification ──────────────────────────────────────

_CLASSIFY_SYSTEM_PROMPT = """\
You are a financial event classifier for a supply chain intelligence system.
Classify the event type and assess confidence.
Always respond with valid JSON matching the requested schema exactly."""

_CLASSIFY_USER_TEMPLATE = """\
Classify this financial news event:

Title: {title_json}
Text: {text_json}

Return a JSON object:
{{
  "event_type": one of ["SHOCK", "RECOVERY", "EARNINGS", "MACRO", "GEOPOLITICAL", "REGULATORY", "COMMODITY_SHOCK", "FX_SHOCK"],
  "confidence": float between 0.0 and 1.0,
  "rationale": "brief explanation of classification"
}}

Classification guide:
- SHOCK: Supply disruptions, factory fires, shortages, strikes, recalls, natural disasters
- RECOVERY: Reopenings, deal resolutions, capacity expansions, growth signals
- EARNINGS: Quarterly/annual results, EPS, revenue reports, guidance changes
- MACRO: Central bank actions, interest rates, inflation, GDP, employment data
- GEOPOLITICAL: Sanctions, tariffs, trade wars, political instability, military conflicts
- REGULATORY: New regulations, compliance changes, antitrust actions, policy shifts
- COMMODITY_SHOCK: Raw material price spikes, supply squeezes, inventory disruptions
- FX_SHOCK: Currency crises, major exchange rate moves, capital controls"""

_VALID_EVENT_TYPES = {e.value for e in EventType}


async def classify_event_with_llm(title: str, text: str) -> tuple[EventType, float]:
    """Classify event type using the configured LLM provider.

    Both title and body are sanitised before the LLM sees them.
    Hidden-HTML sentiment-flip is the canonical attack here: attacker
    writes a bullish visible headline and appends a hidden bearish
    clause. Without sanitisation the classifier flips the sign of the
    entire event signal. With it, the hidden subtree is dropped before
    the first token reaches the model.

    When sanitisation flags mixed-script contamination we still
    proceed (classification is more robust to stripped content than
    ticker extraction), but we cap the downstream confidence at 0.3
    so the event cannot drive a high-conviction trade.
    """
    title_clean = sanitize_news_text(title)
    body_clean = sanitize_news_text(text or "")

    hostile = (not is_safe(title_clean)) or (not is_safe(body_clean))
    combined_text = body_clean.clean or title_clean.clean

    if hostile:
        log.warning(
            f"[SEC] Event classification with hostile input: "
            f"title_rejected={title_clean.rejected_tokens}, "
            f"body_rejected={body_clean.rejected_tokens}"
        )

    try:
        raw = await _llm_json_call(
            _CLASSIFY_SYSTEM_PROMPT,
            _CLASSIFY_USER_TEMPLATE.format(
                title_json=json.dumps(title_clean.clean, ensure_ascii=False),
                text_json=json.dumps(combined_text[:2000], ensure_ascii=False),
            ),
        )
        parsed = _parse_llm_json(raw)
        if parsed is None:
            return EventType.SHOCK, 0.3

        event_type_str = parsed.get("event_type", "SHOCK")
        if event_type_str not in _VALID_EVENT_TYPES:
            event_type_str = "SHOCK"

        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        # Hostile-input confidence cap: we still emit the classifier's
        # best guess (the visible text may still be informative) but
        # bound the confidence hard so the event cannot drive a
        # high-conviction Tier-1 trade.
        if hostile:
            confidence = min(confidence, 0.3)

        return EventType(event_type_str), confidence
    except Exception as exc:
        log.warning(f"Event classification failed, falling back to SHOCK: {exc}")
        return EventType.SHOCK, 0.3


# ── Graph Mapping ─────────────────────────────────────────────


async def map_entities_to_graph(entities: list[dict[str, Any]]) -> int:
    """Upsert extracted entities into the Neo4j supply chain graph.

    Companies become Company nodes; products become Product nodes.
    Returns count of nodes upserted.
    """
    from graph_intelligence.tools.neo4j_tools import _session_kwargs, get_driver
    from shared.types import CompanyNode

    companies = [e for e in entities if e.get("type") == "COMPANY" and e.get("ticker")]
    products = [e for e in entities if e.get("type") == "PRODUCT"]

    if not companies and not products:
        return 0

    try:
        driver = await get_driver()
    except Exception as exc:
        log.warning(f"Neo4j unavailable for entity mapping: {exc}")
        return 0

    upserted = 0

    try:
        async with driver.session(**_session_kwargs()) as session:
            for company in companies:
                node = CompanyNode(
                    ticker=company["ticker"],
                    name=company.get("entity"),
                )
                await session.run(
                    """
                    MERGE (c:Company {ticker: $ticker})
                    SET c.name = COALESCE($name, c.name),
                        c.last_updated = datetime()
                    """,
                    ticker=node.ticker,
                    name=node.name,
                )
                upserted += 1

            for product in products:
                await session.run(
                    """
                    MERGE (p:Product {name: $name})
                    SET p.category = $category,
                        p.last_updated = datetime()
                    """,
                    name=product["entity"],
                    category=product.get("category", "unknown"),
                )
                upserted += 1
    except Exception as exc:
        log.warning(f"Neo4j entity upsert failed after {upserted} ops: {exc}")

    log.info(
        f"Upserted {upserted} entities to Neo4j ({len(companies)} companies, {len(products)} products)"
    )
    return upserted


# ── Full News Batch Pipeline ──────────────────────────────────


async def _process_single_item(
    item: NewsItem,
    semaphore: asyncio.Semaphore,
) -> list[CausalEvent]:
    """Process a single news item: extract entities, classify, map to graph."""
    async with semaphore:
        text = f"{item.title}. {item.text or ''}"

        entities, (event_type, confidence) = await asyncio.gather(
            extract_entities(text),
            classify_event_with_llm(item.title, item.text or ""),
        )

        upserted = await map_entities_to_graph(entities)

        if upserted > 0:
            try:
                from backend.services.orchestrator import update_graph_sequence

                update_graph_sequence(_next_seq())
            except Exception as exc:
                log.debug(f"Graph sequence update skipped: {exc}")

        symbols = item.symbols or [
            e["ticker"] for e in entities if e.get("type") == "COMPANY" and e.get("ticker")
        ]
        if not symbols:
            symbols = ["UNKNOWN"]

        # P1-P: ``compressed_summary`` survives into Qdrant, the
        # dashboard, and any downstream prompt that renders
        # ``state['timeline']`` to an LLM. The raw title can carry
        # hidden HTML/CSS, homoglyph attacks, or direct prompt
        # injection — the sanitiser normalises unicode, strips hidden
        # spans, and redacts script-switched tokens.
        #
        # ``sanitize_news_text`` returns a ``SanitizedText`` dataclass,
        # not a plain ``str`` — we pass its ``.clean`` field to the
        # Pydantic model. The prior revision handed the dataclass
        # directly, causing every NER-produced CausalEvent to fail
        # validation with "Input should be a valid string" and 0
        # events to land per cycle.
        sanitized = sanitize_news_text(item.title or "")
        clean_title = sanitized.clean or (item.title or "")

        events: list[CausalEvent] = []
        for symbol in symbols:
            events.append(
                CausalEvent(
                    timestamp=item.timestamp,
                    source_node=symbol,
                    event_type=event_type,
                    compressed_summary=clean_title,
                    confidence=confidence,
                    sequence_id=_next_seq(),
                )
            )
        return events


async def process_news_batch(
    news_items: list[NewsItem],
    concurrency: int | None = None,
) -> list[CausalEvent]:
    """Full NER pipeline: extract entities, classify events, map to graph,
    and produce CausalEvent objects with monotonic sequence IDs.

    Args:
        news_items: News items to process.
        concurrency: Max concurrent LLM calls. Defaults to _DEFAULT_LLM_CONCURRENCY (10).
            For large batches (200+), consider raising to 15-20.

    Uses a semaphore to respect LLM API rate limits.
    """
    if not news_items:
        return []

    effective_concurrency = concurrency or _DEFAULT_LLM_CONCURRENCY
    semaphore = asyncio.Semaphore(effective_concurrency)

    log.info(f"NER processing {len(news_items)} items (concurrency={effective_concurrency})")

    tasks = [_process_single_item(item, semaphore) for item in news_items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_events: list[CausalEvent] = []
    failed = 0
    for result in results:
        if isinstance(result, Exception):
            log.warning(f"News item processing failed: {result}")
            failed += 1
            continue
        all_events.extend(result)

    all_events.sort(key=lambda e: (e.timestamp, e.sequence_id or 0))
    log.info(
        f"NER pipeline processed {len(news_items)} items → "
        f"{len(all_events)} events ({failed} failures)"
    )
    return all_events


# ═══════════════════════════════════════════════════════════════════
#  Autonomous Shock Detector
#
#  Replaces the hardcoded `SHOCK_SCENARIOS` dict. Scans a recent
#  news window, scores geopolitical / supply-chain escalation by
#  keyword density + LLM classification, and — when a threshold is
#  crossed — emits a structured `causal_intel` dict compatible with
#  everything downstream (orchestrator MarketContext, backtest
#  `shock_context`).
# ═══════════════════════════════════════════════════════════════════

# Keyword families. Each tuple = (category, keywords, default_bias).
# Bias is used when no per-event LLM classification is available; it
# reflects the historical default P&L direction of that shock family
# on broad equity indices.
_SHOCK_KEYWORD_FAMILIES: list[tuple[str, tuple[str, ...], str]] = [
    # Geopolitical — historically index-bearish
    (
        "geopolitical_war",
        (
            "war",
            "invasion",
            "attack",
            "strike",
            "airstrike",
            "missile",
            "drone strike",
            "military conflict",
            "ceasefire violation",
            "mobilisation",
            "mobilization",
        ),
        "BEARISH",
    ),
    (
        "geopolitical_tension",
        (
            "tensions escalate",
            "escalating tensions",
            "standoff",
            "confrontation",
            "retaliation",
            "provocation",
            "threat of war",
            "red line",
        ),
        "BEARISH",
    ),
    (
        "sanctions_tariffs",
        (
            "sanctions",
            "sanction",
            "tariff",
            "tariffs",
            "trade war",
            "export ban",
            "export controls",
            "embargo",
            "blockade",
            "blacklist",
        ),
        "BEARISH",
    ),
    (
        "middle_east",
        (
            "iran",
            "israel",
            "gaza",
            "hezbollah",
            "houthi",
            "red sea",
            "strait of hormuz",
            "oil supply",
        ),
        "BEARISH",
    ),
    # Supply chain — selectively bearish on consumer / industrials
    (
        "supply_chain",
        (
            "factory fire",
            "plant explosion",
            "port closure",
            "shipping disruption",
            "container shortage",
            "chip shortage",
            "semiconductor shortage",
            "supply chain disruption",
            "supplier halt",
            "logistics bottleneck",
        ),
        "BEARISH",
    ),
    (
        "commodity_shock",
        (
            "oil spike",
            "opec cuts",
            "opec+ cut",
            "natural gas crisis",
            "copper squeeze",
            "gold surge",
            "commodity rally",
            "power outage",
            "grid failure",
        ),
        "BEARISH",
    ),
    # Recovery / positive
    (
        "recovery",
        (
            "ceasefire agreed",
            "truce signed",
            "peace deal",
            "sanctions lifted",
            "tariff rollback",
            "plant reopened",
            "capacity restored",
            "supply restored",
        ),
        "BULLISH",
    ),
]

# Minimum aggregate keyword-hit score (across all families) at which we
# treat the window as containing a live shock. Calibrated so 2-3 strong
# headlines trigger, random noise does not.
_SHOCK_DETECTION_THRESHOLD = 3.0


def _score_news_for_shock(
    news_items: list[NewsItem],
) -> tuple[float, dict[str, int], str, int]:
    """Heuristic scoring pass over a news window.

    Every item's text is sanitised before keyword-matching so a
    hidden-HTML-injected ``war``, ``sanctions`` or ``strike`` clause
    cannot inflate the shock score and trigger a false Tier-3
    signal. The fourth return value is the count of items that the
    sanitiser flagged as hostile — the caller uses this to enforce
    the Tier-3 hard-fail policy.

    Returns:
        (total_score, per_family_hits, dominant_bias, hostile_item_count)
    """
    per_family: dict[str, int] = {}
    bias_tally: dict[str, int] = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
    hostile_items: int = 0

    for item in news_items:
        title_s = sanitize_news_text(item.title or "")
        body_s = sanitize_news_text(item.text or "")
        if not is_safe(title_s) or not is_safe(body_s):
            hostile_items += 1
        # Score only on sanitised content — hidden HTML injection cannot
        # boost shock keywords, and homoglyph tokens have been redacted.
        text_lower = f"{title_s.clean} {body_s.clean}".lower()
        if not text_lower.strip():
            continue
        for family, keywords, default_bias in _SHOCK_KEYWORD_FAMILIES:
            hits = sum(1 for kw in keywords if kw in text_lower)
            if hits <= 0:
                continue
            per_family[family] = per_family.get(family, 0) + hits
            bias_tally[default_bias] = bias_tally.get(default_bias, 0) + hits

    total_score = float(sum(per_family.values()))
    dominant_bias = max(bias_tally, key=bias_tally.get) if bias_tally else "NEUTRAL"
    if bias_tally["BULLISH"] == 0 and bias_tally["BEARISH"] == 0:
        dominant_bias = "NEUTRAL"
    return total_score, per_family, dominant_bias, hostile_items


# Index symbols we attach directional biases to. These match the keys
# used by the backtest engine's shock_context / index_bias block.
_TRACKED_INDICES = (
    "^GSPC",
    "^IXIC",
    "^DJI",
    "^RUT",
    "^FTSE",
    "^GDAXI",
    "^FCHI",
    "^STOXX50E",
    "^N225",
    "^HSI",
    "^NSEI",
    "^BSESN",
)


def _ticker_frequency(news_items: list[NewsItem]) -> dict[str, int]:
    """Count which tickers are most mentioned in the news window."""
    freq: dict[str, int] = {}
    for item in news_items:
        for t in item.symbols or []:
            if not t:
                continue
            freq[t] = freq.get(t, 0) + 1
    return freq


_SHOCK_GEOGRAPHY_KEYWORDS: dict[str, list[str]] = {
    "europe": ["russia", "ukraine", "ecb", "eu sanctions", "eurozone", "nato", "brexit", "germany", "france"],
    "asia_pacific": ["china", "taiwan", "japan", "korea", "semiconductor", "tsmc", "samsung", "xi jinping", "boj"],
    "middle_east": ["iran", "opec", "red sea", "houthi", "saudi", "strait of hormuz", "israel", "gaza"],
    "north_america": ["fed", "fomc", "silicon valley bank", "tariff", "us china", "canada", "mexico"],
    "global": ["covid", "pandemic", "global recession", "world trade", "imf", "world bank"],
}


# ── Multi-asset shock routing tables ──────────────────────────
# When a shock is detected, these tables map shock types to instruments
# across ALL asset classes — not just equities. This is the core of the
# multi-asset wiring: a single shock simultaneously routes to FX, commodities,
# crypto, ETFs, and bonds.

FX_SHOCK_ROUTING: dict[str, list[str]] = {
    "central_bank_fed": ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCHF"],
    "central_bank_boj": ["USDJPY", "EURJPY", "AUDJPY", "GBPJPY"],
    "central_bank_ecb": ["EURUSD", "EURGBP", "EURJPY", "EURCHF"],
    "china_shock": ["USDCNY", "USDHKD", "AUDUSD", "USDKRW"],
    "em_crisis": ["USDMXN", "USDBRL", "USDINR", "USDZAR", "USDTRY"],
    "oil_shock": ["USDCAD", "USDNOK"],
    "risk_off": ["USDJPY", "USDCHF"],
    "geopolitical_war": ["USDJPY", "USDCHF", "EURUSD"],
    "sanctions_tariffs": ["EURUSD", "USDCNY", "USDMXN"],
}

SHOCK_TO_COMMODITY: dict[str, list[str]] = {
    "oil_supply": ["CL", "BZ"],
    "gas_supply": ["NG"],
    "food_supply": ["ZC", "ZS", "ZW"],
    "metal_supply": ["GC", "SI", "HG"],
    "energy_general": ["CL", "NG"],
    "geopolitical_war": ["NG", "ZW", "ZC", "GC"],
    "middle_east": ["CL", "BZ", "GC"],
    "china_shock": ["HG", "ZC", "ZS"],
    "commodity_shock": ["CL", "GC", "HG"],
    "supply_chain": ["CL", "HG", "GC"],
}

SHOCK_TO_CRYPTO: dict[str, list[str]] = {
    "exchange_hack": ["BTC"],
    "regulatory_crypto": ["BTC", "ETH"],
    "defi_exploit": ["ETH", "LINK"],
    "ai_narrative": ["SOL", "ETH", "LINK", "FIL"],
    "risk_on_macro": ["BTC", "ETH", "SOL"],
}

SHOCK_TO_ETF: dict[str, list[str]] = {
    "semiconductor_shortage": ["SMH", "SOXX"],
    "energy_shock": ["XLE", "USO", "UNG"],
    "financial_stress": ["XLF"],
    "tech_rally": ["QQQ", "XLK", "ARKK"],
    "safe_haven": ["GLD", "SLV", "TLT", "IEF"],
    "china_shock": ["FXI", "KWEB", "EEM"],
    "commodity_shock": ["USO", "UNG", "GLD"],
    "geopolitical_war": ["GLD", "XLE", "TLT"],
}

SHOCK_TO_RATES: dict[str, list[str]] = {
    "central_bank_fed": ["ZB", "ZN", "ZF"],
    "central_bank_ecb": ["ZN"],
    "risk_off": ["ZB", "ZN"],
    "geopolitical_war": ["ZB"],
    "recovery": ["ZF"],
}


def _classify_shock_geography(
    news_items: list[NewsItem],
    families: dict[str, int],
) -> list[str]:
    """Tag a shock with geographic regions for index routing."""
    text_blob = " ".join(
        (item.title or "").lower() + " " + (item.text or "")[:200].lower()
        for item in news_items[:20]
    )
    geo_hits: list[str] = []
    for region, keywords in _SHOCK_GEOGRAPHY_KEYWORDS.items():
        if any(kw in text_blob for kw in keywords):
            geo_hits.append(region)
    # If no specific region detected, mark as global
    if not geo_hits:
        geo_hits = ["global"]
    return geo_hits


def detect_live_shock(
    news_items: list[NewsItem],
    *,
    min_score: float = _SHOCK_DETECTION_THRESHOLD,
    shock_magnitude: float = -0.08,
) -> dict[str, Any] | None:
    """Autonomous shock detector.

    Given a window of recent news items (live or historical), returns a
    `causal_intel` dict when enough keyword evidence is present to flag
    an escalating shock. The dict is structurally identical to the
    hardcoded `SHOCK_SCENARIOS[*]["causal_intel"]` blocks so it is a
    drop-in replacement wherever those are used.

    Returns None when the news window is not elevated enough — in which
    case callers (backtester, LangGraph pipeline) should proceed with
    zero shock context and let the regime gates decide.

    Output schema:
        {
          "shock_type": "geopolitical" | "supply_chain" | "commodity" | "recovery",
          "shock_families": {family: hit_count, ...},
          "shock_score": float,
          "expected_direction": "BEARISH" | "BULLISH" | "NEUTRAL",
          "beneficiary_tickers": [str, ...],
          "victim_tickers": [str, ...],
          "index_bias": {"^GSPC": "BEARISH", ...},
          "headline_sample": [str up to 5],
          "causal_chain": "<free-text summary>",
          "auto_detected": True,
        }
    """
    if not news_items:
        return None

    score, families, dominant_bias, hostile_items = _score_news_for_shock(news_items)
    if score < min_score:
        return None

    # ── Hard-fail policy (Phase 1 Adversarial defence) ──
    # A shock context is DIRECTIONAL — wrong direction is worse than
    # no signal. When the news window contains hostile content (mixed-
    # script tokens, hidden sentiment-flip HTML), refuse to emit a
    # Tier-3 shock at all. Let the quant path + regime gates decide.
    hostile_ratio = hostile_items / max(len(news_items), 1)
    if hostile_items > 0 and hostile_ratio >= 0.15:
        log.warning(
            f"[SEC] Shock detection REFUSED: "
            f"{hostile_items}/{len(news_items)} items hostile "
            f"(ratio={hostile_ratio:.0%} >= 15 % threshold). "
            f"Downgrading to NO-SHOCK; keyword score was {score:.1f}."
        )
        return None

    # Pick the top-mentioned family for typing.
    if not families:
        return None
    top_family = max(families, key=families.get)
    shock_type_map = {
        "geopolitical_war": "geopolitical",
        "geopolitical_tension": "geopolitical",
        "sanctions_tariffs": "geopolitical",
        "middle_east": "geopolitical",
        "supply_chain": "supply_chain",
        "commodity_shock": "commodity",
        "recovery": "recovery",
    }
    shock_type = shock_type_map.get(top_family, "geopolitical")

    # Ticker segregation — frequency-based proxy. In a real deployment
    # the LangGraph mapper refines beneficiaries/victims via the Neo4j
    # causal graph. This heuristic is the conservative default.
    freq = _ticker_frequency(news_items)
    top_tickers = sorted(freq.items(), key=lambda kv: -kv[1])[:10]
    mentioned = [t for t, _ in top_tickers]

    beneficiaries: list[str] = []
    victims: list[str] = []
    if dominant_bias == "BEARISH":
        victims = mentioned[:6]
        # Defensive sectors tend to benefit on bearish shocks.
        beneficiaries = [t for t in ("GLD", "TLT", "XLU", "XLP") if t in freq]
    elif dominant_bias == "BULLISH":
        beneficiaries = mentioned[:6]
        victims = [t for t in ("GLD", "TLT") if t in freq]
    else:
        victims = mentioned[:4]

    index_bias = {idx: dominant_bias for idx in _TRACKED_INDICES}

    sample = [item.title for item in news_items[:5] if item.title]
    causal_chain = (
        f"Auto-detected {shock_type} shock "
        f"(score={score:.1f}, families={list(families.keys())}). "
        f"Dominant bias: {dominant_bias}. "
        f"Top tickers mentioned: {', '.join(mentioned[:5]) or 'none'}."
    )

    # Geographic shock routing — tag shock with geographic regions
    # so the orchestrator can select the right index futures to express through.
    shock_geography = _classify_shock_geography(news_items, families)

    # Multi-asset routing: determine which instruments across all asset
    # classes should be activated for this shock type.
    fx_targets: list[str] = []
    commodity_targets: list[str] = []
    crypto_targets: list[str] = []
    etf_targets: list[str] = []
    rates_targets: list[str] = []
    for family_name in families:
        fx_targets.extend(FX_SHOCK_ROUTING.get(family_name, []))
        commodity_targets.extend(SHOCK_TO_COMMODITY.get(family_name, []))
        crypto_targets.extend(SHOCK_TO_CRYPTO.get(family_name, []))
        etf_targets.extend(SHOCK_TO_ETF.get(family_name, []))
        rates_targets.extend(SHOCK_TO_RATES.get(family_name, []))
    # Also route by shock_type
    fx_targets.extend(FX_SHOCK_ROUTING.get(shock_type, []))
    commodity_targets.extend(SHOCK_TO_COMMODITY.get(shock_type, []))
    crypto_targets.extend(SHOCK_TO_CRYPTO.get(shock_type, []))
    etf_targets.extend(SHOCK_TO_ETF.get(shock_type, []))
    rates_targets.extend(SHOCK_TO_RATES.get(shock_type, []))

    return {
        "shock_type": shock_type,
        "shock_families": families,
        "shock_score": score,
        "expected_direction": dominant_bias,
        "beneficiary_tickers": beneficiaries,
        "victim_tickers": victims,
        "index_bias": index_bias,
        "headline_sample": sample,
        "causal_chain": causal_chain,
        "auto_detected": True,
        "shock_geography": shock_geography,
        # Multi-asset routing targets
        "fx_targets": sorted(set(fx_targets)),
        "commodity_targets": sorted(set(commodity_targets)),
        "crypto_targets": sorted(set(crypto_targets)),
        "etf_targets": sorted(set(etf_targets)),
        "rates_targets": sorted(set(rates_targets)),
        "sanitisation": {
            "hostile_items_in_window": hostile_items,
            "hostile_ratio": round(hostile_ratio, 3),
            "window_size": len(news_items),
        },
    }


def shock_context_from_news(
    news_items: list[NewsItem],
    *,
    shock_magnitude: float = -0.08,
    min_score: float = _SHOCK_DETECTION_THRESHOLD,
) -> dict[str, Any] | None:
    """Build a full `shock_context` dict (as consumed by the backtest
    engine's `run_backtest(..., shock_context=...)`) from a news window.

    Wraps `detect_live_shock` with the outer structure the backtester
    expects.  Returns None if no shock detected.
    """
    intel = detect_live_shock(news_items, min_score=min_score)
    if not intel:
        return None

    # The backtester reads `shock_tickers` as the equity universe
    # active for the shock. Use heuristic mentions ∪ victims ∪ benefs.
    mentioned = {t for t in (intel.get("victim_tickers") or []) if t}
    mentioned |= {t for t in (intel.get("beneficiary_tickers") or []) if t}

    return {
        "supply_chain_shock": True,
        "shock_source": f"auto:{intel['shock_type']}",
        "shock_tickers": mentioned,
        "shock_magnitude": shock_magnitude
        if intel["expected_direction"] == "BEARISH"
        else abs(shock_magnitude),
        "causal_intel": intel,
        "beneficiary_tickers": set(intel.get("beneficiary_tickers") or []),
        "victim_tickers": set(intel.get("victim_tickers") or []),
        "index_bias": intel.get("index_bias", {}),
        "auto_detected": True,
    }


__all__ = [
    "extract_entities",
    "classify_event_with_llm",
    "map_entities_to_graph",
    "process_news_batch",
    "detect_live_shock",
    "shock_context_from_news",
]
