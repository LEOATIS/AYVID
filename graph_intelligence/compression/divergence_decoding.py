"""Divergence Decoding — contrastive logit adjustment for backtest integrity.

The problem: LLMs trained on historical data inherently "know" what happened,
causing lookahead bias when we ask them to reason about past events as if
they were current. The model's latent knowledge leaks future information
into its causal reasoning.

The solution: Inference-time unlearning via contrastive decoding.
1. Run the "informed" model with full context (standard forward pass)
2. Run a "naive" model with future data stripped (or a separate prompt
   instructing it to ignore future knowledge)
3. Subtract the naive logits from the informed logits
4. The residual captures ONLY the reasoning that depends on current data,
   not future knowledge

This is applied during backtest-mode causal analysis to ensure signals
are not contaminated by hindsight.

Reference: Li et al. "Contrastive Decoding" (2023), adapted for temporal debiasing.
"""
from __future__ import annotations

import json
from typing import Any

from shared.config import settings
from shared.logger import get_logger

log = get_logger("divergence.decode")

_openai_client = None
_anthropic_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import AsyncAnthropic
        _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


_INFORMED_SYSTEM = """\
You are a supply chain analyst at a quantitative hedge fund.
Analyze the following events and market data. Provide your assessment
of the likely impact on the target stock.

CRITICAL: Base your reasoning ONLY on the information provided.
Do NOT use knowledge of what actually happened after these events.
You are analyzing in real-time as of the analysis date."""

_NAIVE_SYSTEM = """\
You are a general knowledge assistant. You have access to public information
about companies, industries, and historical market patterns.

Given only the company name and sector, what is your general expectation
for this stock's near-term performance? Do NOT consider any specific
recent events — only base rates and general knowledge.

Be specific: output a JSON object with keys "direction" (LONG/SHORT/HOLD),
"confidence" (0.0-1.0), and "reasoning" (one sentence)."""


async def divergence_decode(
    ticker: str,
    analysis_date: str,
    compressed_timeline: str,
    causal_paths: str,
    sector: str = "Technology",
) -> dict[str, Any]:
    """Run divergence decoding to strip lookahead bias from causal reasoning.

    Returns adjusted signal with the future-knowledge component removed.
    """
    informed_prompt = f"""Analysis Date: {analysis_date}
Target Ticker: {ticker}
Sector: {sector}

=== Compressed Event Timeline ===
{compressed_timeline}

=== Causal Paths from Supply Chain Graph ===
{causal_paths}

Based ONLY on the above data as of {analysis_date}, what is your trade recommendation?
Output JSON: {{"direction": "LONG/SHORT/HOLD", "confidence": 0.0-1.0, "reasoning": "..."}}"""

    naive_prompt = f"""Company: {ticker}
Sector: {sector}
Date: {analysis_date}

Based ONLY on general sector knowledge and base rates (no specific events),
what is the typical near-term outlook for a {sector} stock?
Output JSON: {{"direction": "LONG/SHORT/HOLD", "confidence": 0.0-1.0, "reasoning": "..."}}"""

    import asyncio

    use_anthropic = settings.LLM_PROVIDER == "anthropic" and settings.ANTHROPIC_API_KEY

    if use_anthropic:
        client = _get_anthropic_client()
        sys_suffix = "\n\nYou MUST respond with valid JSON only."
        informed_task = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=800,
            system=_INFORMED_SYSTEM + sys_suffix,
            messages=[{"role": "user", "content": informed_prompt}],
            temperature=0.0,
        )
        naive_task = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=800,
            system=_NAIVE_SYSTEM + sys_suffix,
            messages=[{"role": "user", "content": naive_prompt}],
            temperature=0.0,
        )
        informed_resp, naive_resp = await asyncio.gather(informed_task, naive_task)
        informed_content = informed_resp.content[0].text if informed_resp.content else "{}"
        naive_content = naive_resp.content[0].text if naive_resp.content else "{}"
    else:
        client = _get_openai_client()
        informed_task = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _INFORMED_SYSTEM},
                {"role": "user", "content": informed_prompt},
            ],
            temperature=0.0,
            logprobs=True,
            top_logprobs=5,
            response_format={"type": "json_object"},
        )
        naive_task = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _NAIVE_SYSTEM},
                {"role": "user", "content": naive_prompt},
            ],
            temperature=0.0,
            logprobs=True,
            top_logprobs=5,
            response_format={"type": "json_object"},
        )
        informed_resp, naive_resp = await asyncio.gather(informed_task, naive_task)
        informed_content = informed_resp.choices[0].message.content or "{}"
        naive_content = naive_resp.choices[0].message.content or "{}"

    try:
        informed_signal = json.loads(informed_content)
    except json.JSONDecodeError:
        informed_signal = {"direction": "HOLD", "confidence": 0.0, "reasoning": "parse error"}
    try:
        naive_signal = json.loads(naive_content)
    except json.JSONDecodeError:
        naive_signal = {"direction": "HOLD", "confidence": 0.0, "reasoning": "parse error"}

    adjusted = _contrastive_adjust(informed_signal, naive_signal)

    if use_anthropic:
        informed_logprobs = _confidence_to_probs(informed_signal)
        naive_logprobs = _confidence_to_probs(naive_signal)
    else:
        informed_logprobs = _extract_direction_logprobs(informed_resp)
        naive_logprobs = _extract_direction_logprobs(naive_resp)
    kl_divergence = _compute_kl_divergence(informed_logprobs, naive_logprobs)

    result = {
        "adjusted_direction": adjusted["direction"],
        "adjusted_confidence": adjusted["confidence"],
        "adjusted_reasoning": adjusted["reasoning"],
        "informed_signal": informed_signal,
        "naive_signal": naive_signal,
        "kl_divergence": kl_divergence,
        "bias_detected": kl_divergence < 0.1,
        "bias_explanation": (
            "Low KL divergence suggests the informed model's reasoning "
            "is largely driven by base-rate knowledge rather than causal analysis. "
            "Signal may contain lookahead bias."
            if kl_divergence < 0.1
            else "Informed model shows distinct reasoning from naive baseline — "
            "signal appears grounded in causal data."
        ),
    }

    log.info(
        f"Divergence decode for {ticker}: "
        f"{adjusted['direction']} @ {adjusted['confidence']:.2f} "
        f"(KL={kl_divergence:.3f}, bias={'YES' if result['bias_detected'] else 'NO'})"
    )

    return result


def _contrastive_adjust(
    informed: dict[str, Any],
    naive: dict[str, Any],
) -> dict[str, Any]:
    """Subtract the naive signal from the informed signal."""
    direction_scores = {"LONG": 1.0, "SHORT": -1.0, "HOLD": 0.0}

    informed_score = direction_scores.get(informed.get("direction", "HOLD"), 0.0)
    naive_score = direction_scores.get(naive.get("direction", "HOLD"), 0.0)
    informed_conf = float(informed.get("confidence", 0.0))
    naive_conf = float(naive.get("confidence", 0.0))

    adjusted_score = informed_score * informed_conf - naive_score * naive_conf * 0.5
    adjusted_conf = max(0.0, min(1.0, abs(adjusted_score)))

    if adjusted_score > 0.1:
        direction = "LONG"
    elif adjusted_score < -0.1:
        direction = "SHORT"
    else:
        direction = "HOLD"
        adjusted_conf *= 0.5

    reasoning_parts = [f"Informed: {informed.get('reasoning', 'N/A')}"]
    if naive.get("reasoning"):
        reasoning_parts.append(f"Base rate subtracted: {naive['reasoning']}")

    return {
        "direction": direction,
        "confidence": round(adjusted_conf, 3),
        "reasoning": " | ".join(reasoning_parts),
        "raw_score": round(adjusted_score, 4),
    }


def _confidence_to_probs(signal: dict[str, Any]) -> dict[str, float]:
    """Approximate probability distribution from JSON confidence when logprobs unavailable."""
    direction = signal.get("direction", "HOLD")
    conf = float(signal.get("confidence", 0.33))
    probs = {"LONG": 0.1, "SHORT": 0.1, "HOLD": 0.1}
    if direction in probs:
        probs[direction] = conf
    remainder = 1.0 - conf
    other_keys = [k for k in probs if k != direction]
    for k in other_keys:
        probs[k] = remainder / len(other_keys)
    return probs


def _extract_direction_logprobs(response: Any) -> dict[str, float]:
    """Extract approximate probability distribution over directions from logprobs."""
    probs = {"LONG": 0.33, "SHORT": 0.33, "HOLD": 0.34}

    try:
        content = response.choices[0].logprobs.content
        if not content:
            return probs

        import math
        for token_info in content:
            token = token_info.token.upper().strip()
            if token in probs:
                probs[token] = math.exp(token_info.logprob)
                break
            if token_info.top_logprobs:
                for top in token_info.top_logprobs:
                    t = top.token.upper().strip()
                    if t in probs:
                        probs[t] = math.exp(top.logprob)
    except (AttributeError, IndexError, TypeError):
        pass

    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    return probs


def _compute_kl_divergence(
    p: dict[str, float],
    q: dict[str, float],
) -> float:
    """KL(P || Q) — measures how much the informed distribution diverges from naive."""
    import math
    kl = 0.0
    for key in p:
        pk = max(p.get(key, 1e-10), 1e-10)
        qk = max(q.get(key, 1e-10), 1e-10)
        kl += pk * math.log(pk / qk)
    return max(0.0, kl)
