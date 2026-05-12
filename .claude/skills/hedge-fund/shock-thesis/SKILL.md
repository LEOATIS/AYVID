---
name: hedge-fund/shock-thesis
description: Generate a shock-driven directional thesis from an external trigger event — Suez closure, Fed surprise, earnings miss, geopolitical break, etc. Used by AYVID's strategist agent.
version: 1.0.0
owner: ayvid
upstream: ayvid-native
prompts:
  - system.md
  - user.md
inputs:
  - shock_event
  - affected_tickers
  - causal_chain
  - regime_snapshot
  - priors_block
outputs:
  - ShockThesis (direction, conviction, horizon_days, invalidation_level, asymmetry_R)
---

# Shock-driven directional thesis

When a continuous-ingestion worker flags an external trigger event
(news item, SEC filing, macro print, geopolitical break), the
strategist agent uses this skill to convert the raw shock + causal
chain (from Mapper) into a sized directional thesis.

The skill is **NOT** a general "what does the market think" prompt. It
is specifically scoped to:

1. **Event-driven setups.** Pre-condition: a discrete trigger exists.
   The skill refuses to emit a thesis on "vibes" alone.
2. **Causal-chain anchored.** Inputs include the N-th-order supply-chain
   walk surfaced by the Mapper. The thesis must cite at least one edge
   from that walk.
3. **Asymmetric R.** Output schema requires an `asymmetry_R ≥ 1.5`
   field. Symmetric bets are rejected — AYVID's edge is the
   asymmetric-payoff layer (35-50% WR × 2-4R mean).

## Discipline rules

- Direction in {LONG, SHORT, HOLD}.
- `invalidation_level` MUST be a price or quote-based observation.
- `horizon_days` ∈ [1, 42]. Beyond 42 days the orchestrator routes to
  the structural-thesis path, not the shock path.
- Cite the causal edge used (e.g. `MAERSK→ZIM via container-rate edge`).
