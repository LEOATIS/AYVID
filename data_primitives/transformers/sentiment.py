"""Per-NewsItem sentiment scoring via OpenAI.

Fills in ``NewsItem.sentiment`` for items that arrive without a score
(Reddit, Finnhub news, arbitrary scraped headlines). Items that already
carry a sentiment — e.g. StockTwits which maps the API's own
Bullish/Bearish label to +1.0 / -1.0 — are left untouched.

Scores are floats in ``[-1.0, +1.0]`` with ``0.0`` as strict neutral:
    +1.0  unambiguously bullish for the named ticker(s)
     0.0  neutral / non-actionable / off-topic
    -1.0  unambiguously bearish for the named ticker(s)

Batched in a single JSON-mode call to keep cost low. Failures are
non-fatal: on any error the items keep ``sentiment=None`` so the rest
of the pipeline continues unharmed.
"""
from __future__ import annotations

import asyncio
import json
from typing import Sequence

from openai import AsyncOpenAI

from shared.config import settings
from shared.logger import get_logger
from shared.types import NewsItem

log = get_logger("data.sentiment")

_BATCH_SIZE = 20
_MAX_CHARS_PER_ITEM = 600
_MAX_CONCURRENT_BATCHES = 4

_SYSTEM_PROMPT = """You are a financial sentiment classifier.

For each input news item, output a single float in [-1.0, +1.0] indicating
the sentiment TOWARD THE NAMED TICKER(S):
  +1.0  strongly bullish / materially positive for the ticker
  +0.5  moderately bullish
   0.0  neutral, off-topic, or ambiguous
  -0.5  moderately bearish
  -1.0  strongly bearish / materially negative for the ticker

Rules:
- Score the item's impact on the TICKER, not the author's mood.
- Macro / sector-wide items with no ticker-specific angle are 0.0.
- Pure questions, memes, or "watching" posts with no thesis are 0.0.
- Return a JSON object of shape {"scores": [{"id": <int>, "score": <float>}, ...]}
  with one entry per input item, in the same order.
- Clamp every score to [-1.0, 1.0]. Use 0.0 when uncertain.
"""


def _truncate(text: str, n: int = _MAX_CHARS_PER_ITEM) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _build_user_payload(items: Sequence[NewsItem]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(items):
        body = item.text or ""
        ticker_str = ",".join(item.symbols) if item.symbols else "?"
        lines.append(
            f"[{idx}] tickers={ticker_str} source={item.source}\n"
            f"  title: {_truncate(item.title, 200)}\n"
            f"  body:  {_truncate(body)}"
        )
    return "\n\n".join(lines)


def _clamp(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    if x > 1.0:
        return 1.0
    if x < -1.0:
        return -1.0
    return x


async def _score_batch(
    client: AsyncOpenAI,
    items: list[NewsItem],
) -> None:
    """Score one batch in-place. Silent on failure."""
    if not items:
        return

    payload = _build_user_payload(items)
    try:
        response = await client.chat.completions.create(
            model=settings.OPENAI_SENTIMENT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=16 * len(items) + 64,
        )
    except Exception as exc:
        log.warning(f"Sentiment batch of {len(items)} failed: {exc}")
        return

    raw = response.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning(f"Sentiment batch JSON decode failed: {exc}")
        return

    scores_list = data.get("scores") or []
    scored = 0
    for entry in scores_list:
        try:
            idx = int(entry.get("id"))
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(items):
            items[idx].sentiment = _clamp(score)
            scored += 1

    if scored < len(items):
        # Any items the model skipped get an explicit neutral so downstream
        # logic can distinguish "seen-but-neutral" from "never-scored" (None).
        for it in items:
            if it.sentiment is None:
                it.sentiment = 0.0


async def score_news_items(items: Sequence[NewsItem]) -> list[NewsItem]:
    """Populate ``sentiment`` on NewsItems that don't already have it.

    - Items with a non-None ``sentiment`` are left untouched.
    - Items are scored in parallel batches against OpenAI.
    - Returns the same list (mutation in place); return value is for
      convenience so callers can chain.
    - If ``OPENAI_API_KEY`` is unset, this is a no-op.
    """
    items_list = list(items)
    if not items_list:
        return items_list

    if not settings.OPENAI_API_KEY:
        log.debug("OPENAI_API_KEY unset — skipping per-NewsItem sentiment scoring")
        return items_list

    to_score = [it for it in items_list if it.sentiment is None]
    if not to_score:
        return items_list

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    batches: list[list[NewsItem]] = [
        to_score[i : i + _BATCH_SIZE]
        for i in range(0, len(to_score), _BATCH_SIZE)
    ]

    sem = asyncio.Semaphore(_MAX_CONCURRENT_BATCHES)

    async def _guarded(batch: list[NewsItem]) -> None:
        async with sem:
            await _score_batch(client, batch)

    await asyncio.gather(*[_guarded(b) for b in batches], return_exceptions=True)

    log.info(
        f"Sentiment scored {sum(1 for it in to_score if it.sentiment is not None)}/"
        f"{len(to_score)} NewsItems via {settings.OPENAI_SENTIMENT_MODEL}"
    )
    return items_list
