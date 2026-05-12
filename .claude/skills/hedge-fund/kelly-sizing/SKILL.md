---
name: hedge-fund/kelly-sizing
description: Explain a Half-Kelly position size in plain-English terms — what the conviction implies, what edge the priors imply, how the vol-regulator clipped it. Used by the analyst agent and by the audit trail surfaced to operators.
version: 1.0.0
owner: ayvid
upstream: ayvid-native
prompts:
  - system.md
  - user.md
inputs:
  - ticker
  - conviction
  - edge_estimate
  - variance_estimate
  - kelly_fraction_raw
  - kelly_fraction_clipped
  - final_notional
  - portfolio_equity
outputs:
  - KellyExplanation (plain_english, sensitivity, risk_capital_at_stake)
---

# Kelly-sizing explanation

AYVID sizes every position with a volatility-regulated Half-Kelly. The
math lives in `execution_engine/math/kelly.py` and is deterministic;
this skill is the human-readable surfacing.

Two consumers:

1. The **analyst agent** uses the explanation as part of its rationale
   when it scores a candidate signal.
2. The **operator audit trail** (CLI dashboard + console UI) renders
   the explanation alongside the trade so a human can sanity-check
   that the size matches the conviction.

This skill does NOT recompute the size. It receives the numbers and
explains them.

## Discipline rules

- Plain English only — no Greek letters, no jargon without expansion.
- ``risk_capital_at_stake`` is the dollar exposure assuming the
  invalidation level is hit (NOT the notional).
- ``sensitivity`` describes how the size moves under a ±10% conviction
  change and a ±25% variance change. Both numerical.
- If `kelly_fraction_clipped < kelly_fraction_raw` by more than 30%,
  explicitly note that the vol-regulator clipped the size and why.
